#!/usr/bin/env python3
"""Read-only, local production slow-request traces through ADX workspace files.

Only sanitized request timing and tool names are written to the output. Raw logs,
history content, workspace identifiers and credentials stay in memory.
"""
import concurrent.futures
import collections
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get('ADX_BASE', 'https://onlineswarm.openjiuwen.com:31472').rstrip('/')
TOKEN = os.environ.get('ADX_TOKEN', '')
OPENER = urllib.request.build_opener()
UTC = dt.timezone.utc
CST = dt.timezone(dt.timedelta(hours=8))
MAX_LOG = 200_000_000
MAX_HISTORY = 8_000_000
MAX_SESSIONS = 25
MAX_TRACE_REQUESTS = 100
NAME = re.compile(r'^[A-Za-z0-9_.-]{1,160}$')
REQ = re.compile(r'request_id=([^\s,;]+)')
REQUEST_ID = re.compile(r'^digitalmate-[A-Za-z0-9_-]{1,128}$')
CHUNK = re.compile(r'process_stream chunk #\d+: request_id=\S+ event_type=(\S*)')


def fetch(path, params, *, download=False):
    url = BASE + path + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + TOKEN})
    for attempt in range(3):
        try:
            with OPENER.open(req, timeout=30) as response:
                if download:
                    length = int(response.headers.get('Content-Length', '0'))
                    limit = MAX_HISTORY if str(params.get('path', '')).endswith('history.jsonl') else MAX_LOG
                    if length > limit:
                        raise ValueError('file exceeds bound')
                    body = response.read(limit + 1)
                    if len(body) > limit:
                        raise ValueError('file exceeds bound')
                    return body.decode('utf-8', 'replace')
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (418, 429, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError('unreachable')


def binding_list():
    result, token, seen = [], None, set()
    for _ in range(1000):
        query = {'template': 'digitalmate', 'reference': 'production', 'kind': 'aliases', 'page_size': 50}
        if token:
            query['page_token'] = token
        page = fetch('/api/bindings', query)
        if not isinstance(page.get('items'), list):
            raise ValueError('invalid binding page')
        result.extend(page['items'])
        token = page.get('next_page_token')
        if not token:
            return result
        if not isinstance(token, str) or token in seen:
            raise ValueError('invalid pagination')
        seen.add(token)
    raise ValueError('pagination limit')


def query_for(item):
    binding = item['binding']
    scope = binding['scope']
    ref = scope['template_ref']
    query = {'template': scope['template'], 'kind': 'aliases' if ref.get('alias') is not None else 'versions',
             'reference': ref.get('alias') or ref.get('version'), 'binding_id': scope['binding_id'],
             'generation': binding['generation'], 'environment': 'current',
             'environment_id': binding['current']['environment_id']}
    mounts = fetch('/api/workspace-files', query).get('mounts', [])
    mount = next((m for m in mounts if m.get('target') == '/data/state'), None)
    if not mount:
        raise ValueError('state mount unavailable')
    return {**query, 'mount': mount['id']}


def listing(query, path):
    entries, offset, seen = [], 0, set()
    for _ in range(100):
        page = fetch('/api/workspace-files', {**query, 'path': path, 'action': 'list', 'offset': offset})
        if not isinstance(page.get('entries'), list):
            raise ValueError('invalid files listing')
        entries.extend(page['entries'])
        offset = page.get('next_offset')
        if offset is None:
            return entries
        if offset in seen:
            raise ValueError('repeated offset')
        seen.add(offset)
    raise ValueError('files pagination limit')


def stamp(line):
    try:
        return dt.datetime.strptime(line[:23], '%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def download_log(query):
    """Stream one bounded log; never retain the raw production file on disk."""
    params = {**query, 'path': 'workswarm/process.log', 'action': 'download'}
    url = BASE + '/api/workspace-files?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + TOKEN})
    with OPENER.open(request, timeout=60) as response:
        length = int(response.headers.get('Content-Length', '0'))
        if length > MAX_LOG:
            raise ValueError('log exceeds bound')
        seen = 0
        for line in response:
            seen += len(line)
            if seen > MAX_LOG:
                raise ValueError('log exceeds bound')
            yield line.decode('utf-8', 'replace')
        if length and seen != length:
            raise ValueError('incomplete log response')


def read_log(item):
    query = query_for(item)
    files = listing(query, 'workswarm')
    logfile = next((f for f in files if f.get('name') == 'process.log' and f.get('type') == 'file'), None)
    if not logfile:
        return query, [], 'missing', {}
    if not isinstance(logfile.get('size'), int) or logfile['size'] > MAX_LOG:
        return query, [], 'oversized', {}
    requests = {}
    usage = collections.Counter()
    today = dt.datetime.now(CST).date()
    cutoff = dt.datetime.combine(today - dt.timedelta(days=6), dt.time.min, CST).timestamp()
    for line in download_log(query):
        ts = stamp(line)
        if ts is None or ts < cutoff:
            continue
        day = dt.datetime.fromtimestamp(ts, CST).date()
        if day <= today and "[JiuWenSwarmDeepAdapter] llm_usage chunk: type='llm_usage'" in line:
            # These events have no stable invocation ID in the deployed image.
            # Publish their count as records, never as deduplicated calls.
            usage[day.isoformat()] += 1
        match = REQ.search(line)
        if not match:
            continue
        rid = match.group(1)
        if not REQUEST_ID.fullmatch(rid):
            continue
        span = requests.setdefault(rid, [None, None, None, None, None])
        if '[MessageHandler] dispatch:' in line and span[0] is None:
            span[0] = ts
        elif '[MessageHandler] Stream ' in line and 'total_chunks=' in line and span[1] is None:
            span[1] = ts
            if '[MessageHandler] Stream 正常完成:' in line:
                span[4] = 'normal'
            elif '[MessageHandler] Stream 异常:' in line:
                span[4] = 'exception'
            elif '[MessageHandler] Stream 被取消:' in line:
                span[4] = 'cancelled'
        marker = CHUNK.search(line)
        if marker and span[2] is None and marker.group(1) in ('chat.delta', 'chat.reasoning'):
            span[2] = ts
        if marker and span[3] is None and marker.group(1) == 'chat.delta':
            span[3] = ts
    result = []
    for rid, (start, end, first, answer, stream_status) in requests.items():
        if start is None or end is None or end < start:
            continue
        date = dt.datetime.fromtimestamp(start, CST).date()
        if not today - dt.timedelta(days=6) <= date <= today:
            continue
        gateway = round((end - start) * 1000, 1)
        row = {'request_id': rid, 'date': date.isoformat(), 'gateway': gateway,
               'start_time': dt.datetime.fromtimestamp(start, UTC).isoformat()}
        if stream_status is not None:
            row['gateway_stream_status'] = stream_status
        for name, value in (('first_token', first), ('answer_first', answer)):
            if value is not None and start <= value <= end:
                row[name] = round((value - start) * 1000, 1)
        if 'first_token' in row and 'answer_first' in row and row['answer_first'] >= row['first_token']:
            row['react'] = round(row['answer_first'] - row['first_token'], 1)
        result.append(row)
    return query, result, 'ok', dict(usage)


def attach_tool_events(query, rows):
    by_id = {r['request_id']: r for r in rows}
    files = listing(query, 'workswarm/.jiuwenswarm/agent/sessions')
    dirs = sorted((e for e in files if e.get('type') == 'directory' and NAME.fullmatch(e.get('name', ''))
                   and not e['name'].startswith(('health_check_', '.'))),
                  key=lambda x: str(x.get('modified_at') or ''), reverse=True)
    truncated = len(dirs) > MAX_SESSIONS
    pending = {}
    for directory in dirs[:MAX_SESSIONS]:
        path = 'workswarm/.jiuwenswarm/agent/sessions/' + directory['name'] + '/history.jsonl'
        try:
            data = fetch('/api/workspace-files', {**query, 'path': path, 'action': 'download'}, download=True)
        except (urllib.error.HTTPError, ValueError, TimeoutError, OSError):
            truncated = True
            continue
        for line in data.splitlines():
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            rid = event.get('request_id')
            if not isinstance(rid, str):
                continue
            row = by_id.get(rid)
            if row is None:
                continue
            ts = event.get('timestamp')
            if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
                continue
            origin = dt.datetime.fromisoformat(row['start_time']).timestamp()
            offset = (ts - origin) * 1000
            if not 0 <= offset <= row['gateway']:
                continue
            if event.get('event_type') == 'chat.tool_call':
                call = event.get('tool_call')
                if not isinstance(call, dict):
                    continue
                cid = call.get('tool_call_id')
                if isinstance(cid, str) and cid:
                    name = call.get('name')
                    name = name if name in ('bash','grep','read_file','tool_search') else 'tool'
                    pending[(row['request_id'], cid)] = (offset, name)
            elif event.get('event_type') == 'chat.tool_result':
                cid = event.get('tool_call_id')
                if not isinstance(cid, str):
                    continue
                before = pending.pop((row['request_id'], cid), None)
                if before and offset >= before[0]:
                    events = row.setdefault('tool_events', [])
                    if len(events) < 200:
                        events.append({'name': before[1], 'start_ms': round(before[0],1),
                                       'duration_ms': round(offset-before[0],1),
                                       'status': 'error' if event.get('success') is False else 'ok'})
                    else:
                        truncated = True
    for row in rows:
        row['tool_events'] = sorted(row.get('tool_events', []), key=lambda e:e['start_ms'])
        row['trace_complete'] = False  # bounded histories cannot establish complete coverage
    return not truncated


def request_daily_metrics(records, date, generated_at, incomplete, usage_events=0):
    """Summarize the same finished requests used for the slow-request ranking."""
    def summary(field):
        values = sorted(row[field] for _, _, row in records
                        if isinstance(row.get(field), (int, float))
                        and not isinstance(row[field], bool) and math.isfinite(row[field]))
        if not values:
            return {'n': 0}
        return {'n': len(values), 'mean': round(sum(values) / len(values), 1),
                'p50': round(values[int(round(.5 * (len(values) - 1)))], 1),
                'p95': round(values[int(round(.95 * (len(values) - 1)))], 1)}

    statuses = collections.Counter(row.get('gateway_stream_status', 'unknown') for _, _, row in records)
    observed = statuses['normal'] + statuses['exception']
    return {'date': date, 'n': len(records),
            'partial': incomplete or date == dt.datetime.now(CST).date().isoformat(),
            'observed_at': generated_at, 'telemetry_observed_at': generated_at,
            'telemetry_partial': incomplete or date == dt.datetime.now(CST).date().isoformat(),
            'source': 'workspace_request_logs',
            'model_usage_events': usage_events,
            'gateway_stream': {'normal': statuses['normal'], 'exception': statuses['exception'],
                               'cancelled': statuses['cancelled'], 'unknown': statuses['unknown'],
                               'observed': observed, 'population': len(records),
                               'exception_rate': statuses['exception'] / observed if observed else None},
            **{key: summary(key) for key in ('gateway', 'first_token', 'answer_first', 'react')}}


def collect():
    if not TOKEN:
        raise SystemExit('ADX_TOKEN required')
    items = binding_list()
    if not items:
        raise SystemExit('no production bindings')
    snapshots, problems = [], {}
    usage_by_day = collections.Counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        future_map = {pool.submit(read_log, item): idx for idx, item in enumerate(items)}
        for count, future in enumerate(concurrent.futures.as_completed(future_map), 1):
            idx = future_map[future]
            try:
                query, rows, status, usage = future.result()
                if status == 'ok':
                    snapshots.append((idx, query, rows))
                    usage_by_day.update(usage)
                else: problems[status] = problems.get(status, 0) + 1
            except Exception as error:
                category = 'http_' + str(error.code) if isinstance(error, urllib.error.HTTPError) else type(error).__name__
                problems[category] = problems.get(category, 0) + 1
            if count % 20 == 0 or count == len(items):
                print(f'bindings {count}/{len(items)}; logs read {len(snapshots)}; unavailable {sum(problems.values())}', flush=True)
    if not snapshots:
        raise SystemExit('no production logs could be read; retain previous snapshot')
    grouped = {}
    for idx, query, rows in snapshots:
        for row in rows:
            grouped.setdefault(row['date'], []).append((idx, query, row))
    days, chosen, daily_metrics = [], [], []
    generated_at = dt.datetime.now(UTC).isoformat()
    for date in sorted(set(grouped) | set(usage_by_day)):
        records = grouped.get(date, [])
        daily_metrics.append(request_daily_metrics(records, date, generated_at, bool(problems), usage_by_day[date]))
        if not records:
            continue
        records.sort(key=lambda item:item[2]['gateway'],reverse=True)
        slow_count = math.ceil(len(records) * .05)
        slow = records[:min(slow_count, 50)]
        chosen.extend(slow)
        days.append({'date': date, 'anomaly': {'latency': {'n':len(records),'slow_count':slow_count,
            'slow_requests':[row for _,_,row in slow]}, 'timeouts':{'requests':[]}}})
    chosen.sort(key=lambda item:(item[2]['date'], item[2]['gateway']), reverse=True)
    by_binding = {}
    for idx, query, row in chosen[:MAX_TRACE_REQUESTS]:
        by_binding.setdefault(idx, (query, []))[1].append(row)
    details_ok = details_failed = 0
    for index,(query, rows) in enumerate(by_binding.values(),1):
        try:
            if attach_tool_events(query, rows): details_ok += 1
            else: details_failed += 1
        except Exception:
            details_failed += 1
        print(f'trace workspaces {index}/{len(by_binding)}',flush=True)
    output = {'schema':'digitalmate/cloud-traces@1','generated_at':generated_at,
              'request_daily_metrics':daily_metrics,
              'binding_count':len(items),'read_log_count':len(snapshots),'errors':problems,
              'detail_workspaces_complete':details_ok,'detail_workspaces_partial':details_failed,
              'trace_request_limit':MAX_TRACE_REQUESTS,'days':days}
    print('output: days',len(days),'slow requests',sum(len(d['anomaly']['latency']['slow_requests']) for d in days),
          'requests with tool events',sum(bool(row.get('tool_events')) for d in days for row in d['anomaly']['latency']['slow_requests']),flush=True)
    return output


def main():
    if len(sys.argv) != 2:
        raise SystemExit('output path required')
    Path(sys.argv[1]).write_text(json.dumps(collect(),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
