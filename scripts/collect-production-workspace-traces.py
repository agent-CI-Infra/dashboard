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
SUMMARY_DAYS = 30
TRACE_DAYS = 7
NAME = re.compile(r'^[A-Za-z0-9_.-]{1,160}$')
TOOL_FILE = re.compile(r'^tool-calls-\d{4}-\d{2}-\d{2}\.jsonl$')
MAX_TOOL_FILE = 16_000_000
MAX_TOOL_FILES = 32
REQ = re.compile(r'request_id=([^\s,;]+)')
REQUEST_ID = re.compile(r'^digitalmate-[A-Za-z0-9_-]{1,128}$')
CHUNK = re.compile(r'process_stream chunk #\d+: request_id=\S+ event_type=(\S*)')
USAGE_FIELDS = {key: re.compile(r"['\"]" + key + r"['\"]\s*:\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)")
                for key in ('input_tokens', 'output_tokens', 'total_tokens', 'tpot_ms', 'ttft_ms', 'total_latency_ms')}


def new_usage():
    return {'events': 0, 'input': 0, 'output': 0, 'total': 0,
            'input_samples': 0, 'output_samples': 0, 'total_samples': 0, 'tpot_pairs': [], 'ttft_values': []}


def record_usage(usage, line):
    usage['events'] += 1
    values = {}
    for key, pattern in USAGE_FIELDS.items():
        match = pattern.search(line)
        value = float(match.group(1)) if match else None
        values[key] = value if value is not None and math.isfinite(value) and 0 <= value <= 1e12 else None
    for field, name in (('input_tokens', 'input'), ('output_tokens', 'output'), ('total_tokens', 'total')):
        value = values[field]
        if value is not None and value.is_integer():
            usage[name] += int(value)
            usage[name + '_samples'] += 1
    output, tpot = values['output_tokens'], values['tpot_ms']
    if output is not None and output.is_integer() and output > 1 and tpot is not None:
        usage['tpot_pairs'].append((tpot, int(output) - 1))
    ttft, duration = values['ttft_ms'], values['total_latency_ms']
    if ttft is not None and duration is not None and 0 < ttft <= duration:
        usage['ttft_values'].append(ttft)


def metric_histogram(pairs):
    """A bounded mergeable histogram; no individual samples are published."""
    bins = collections.Counter()
    for value, weight in pairs:
        index = -1001 if value == 0 else math.floor(math.log(value) / math.log(1.02))
        if value != 0 and (index < -1000 or index > 1000):
            return None
        bins[index] += weight
    return sorted([index, weight] for index, weight in bins.items())


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


def read_tool_metrics(query):
    """Read bounded adapter observations; never export IDs or event content."""
    files = [entry for entry in listing(query, 'workswarm')
             if entry.get('type') == 'file' and TOOL_FILE.fullmatch(str(entry.get('name', '')))]
    files.sort(key=lambda entry: entry['name'], reverse=True)
    complete = len(files) <= MAX_TOOL_FILES
    days = {}
    for entry in files[:MAX_TOOL_FILES]:
        if type(entry.get('size')) is not int or entry['size'] > MAX_TOOL_FILE:
            complete = False
            continue
        raw = fetch('/api/workspace-files', {**query, 'path': 'workswarm/' + entry['name'], 'action': 'download'}, download=True)
        if len(raw.encode('utf-8')) > MAX_TOOL_FILE:
            complete = False
            continue
        for line in raw.splitlines():
            try:
                item = json.loads(line)
                t = item['t']
                if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
                    raise ValueError('invalid timestamp')
                if item.get('v') == 1 and item.get('event') == 'result' and item.get('pairing') == 'matched':
                    t = item.get('start_t', t)
                if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
                    raise ValueError('invalid start timestamp')
                day = dt.datetime.fromtimestamp(t, CST).date()
                today = dt.datetime.now(CST).date()
                if not today - dt.timedelta(days=SUMMARY_DAYS - 1) <= day <= today:
                    continue
                rec = days.setdefault(day.isoformat(), {'started': 0, 'matched': 0, 'unmatched': 0,
                                                        'unknown_status': 0, 'failed': 0, 'durations': []})
                if item.get('v') == 1 and item.get('event') == 'start':
                    rec['started'] += 1
                elif item.get('v') == 1 and item.get('event') == 'result' and item.get('pairing') == 'unmatched':
                    rec['unmatched'] += 1
                elif (item.get('v') == 1 and item.get('event') == 'result' and item.get('pairing') == 'matched') or 'v' not in item:
                    ms = item.get('ms')
                    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or not math.isfinite(ms) or ms < 0:
                        raise ValueError('invalid duration')
                    rec['durations'].append(ms)
                    if item.get('v') == 1:
                        rec['matched'] += 1
                        if item.get('status') not in ('ok', 'error'):
                            rec['unknown_status'] += 1
                    if item.get('status') == 'error':
                        rec['failed'] += 1
            except (ValueError, TypeError, KeyError, OverflowError, OSError):
                complete = False
    return days, bool(files), complete


def read_log(item):
    query = query_for(item)
    files = listing(query, 'workswarm')
    logfile = next((f for f in files if f.get('name') == 'process.log' and f.get('type') == 'file'), None)
    if not logfile:
        return query, [], 'missing', {}
    if not isinstance(logfile.get('size'), int) or logfile['size'] > MAX_LOG:
        return query, [], 'oversized', {}
    requests = {}
    correlations = {}
    usage = {}
    today = dt.datetime.now(CST).date()
    cutoff = dt.datetime.combine(today - dt.timedelta(days=SUMMARY_DAYS - 1), dt.time.min, CST).timestamp()
    for line in download_log(query):
        ts = stamp(line)
        if ts is None or ts < cutoff:
            continue
        day = dt.datetime.fromtimestamp(ts, CST).date()
        if day <= today and "[JiuWenSwarmDeepAdapter] llm_usage chunk: type='llm_usage'" in line:
            # These events have no stable invocation ID in the deployed image.
            # Publish their count as records, never as deduplicated calls.
            record_usage(usage.setdefault(day.isoformat(), new_usage()), line)
        if '[TrajectoryCorrelation] ' in line:
            try:
                link = json.loads(line.split('[TrajectoryCorrelation] ', 1)[1])
                rid = link.get('request_id')
                session = link.get('session_id')
                trace = link.get('trace_id')
                span = link.get('span_id')
                if (isinstance(rid, str) and REQUEST_ID.fullmatch(rid)
                        and isinstance(session, str) and 0 < len(session) <= 512
                        and isinstance(trace, str) and re.fullmatch(r'[0-9a-f]{32}', trace)
                        and isinstance(span, str) and re.fullmatch(r'[0-9a-f]{16}', span)):
                    safe = {'request_id':rid,'session_id':session,'trace_id':trace,'span_id':span}
                    if rid not in correlations:
                        correlations[rid] = safe
                    elif correlations[rid] != safe:
                        correlations[rid] = None
            except (ValueError, TypeError, AttributeError):
                pass
        match = REQ.search(line)
        if not match:
            continue
        rid = match.group(1)
        if not REQUEST_ID.fullmatch(rid):
            continue
        span = requests.setdefault(rid, [None, None, None, None, None, None])
        if '[CloudMetrics] gateway.accept' in line and span[5] is None:
            span[5] = ts
        elif '[MessageHandler] dispatch:' in line and span[0] is None:
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
    for rid, (start, end, first, answer, stream_status, accepted) in requests.items():
        if start is None or end is None or end < start:
            continue
        date = dt.datetime.fromtimestamp(start, CST).date()
        if not today - dt.timedelta(days=SUMMARY_DAYS - 1) <= date <= today:
            continue
        gateway = round((end - start) * 1000, 1)
        row = {'request_id': rid, 'date': date.isoformat(), 'gateway': gateway,
               'start_time': dt.datetime.fromtimestamp(start, UTC).isoformat()}
        if correlations.get(rid):
            row['_native_correlation'] = correlations[rid]
        if stream_status is not None:
            row['gateway_stream_status'] = stream_status
        for name, value in (('first_token', first), ('answer_first', answer)):
            if value is not None and start <= value <= end:
                row[name] = round((value - start) * 1000, 1)
        if accepted is not None and accepted <= start:
            row['gateway_admission'] = round((end - accepted) * 1000, 1)
            for name, value in (('first_text_admission', first), ('answer_admission', answer)):
                if value is not None and start <= value <= end:
                    row[name] = round((value - accepted) * 1000, 1)
        if 'first_token' in row and 'answer_first' in row and row['answer_first'] >= row['first_token']:
            row['react'] = round(row['answer_first'] - row['first_token'], 1)
        result.append(row)
    return query, result, 'ok', dict(usage)


def read_native_trajectory(query, correlation):
    """Console collection does not read native stores; Kubernetes injects a bounded reader."""
    return None


def attach_native_trajectories(query, rows):
    matched = 0
    for row in rows:
        correlation = row.pop('_native_correlation', None)
        if not correlation:
            continue
        try:
            native = read_native_trajectory(query, correlation)
        except (OSError, ValueError, TypeError, KeyError):
            continue
        if isinstance(native, dict):
            row['native_trajectory'] = native
            matched += 1
    return matched


def attach_tool_events(query, rows):
    by_id = {r['request_id']: r for r in rows}
    files = listing(query, 'workswarm/.jiuwenswarm/agent/sessions')
    dirs = sorted((e for e in files if e.get('type') == 'directory' and NAME.fullmatch(e.get('name', ''))
                   and not e['name'].startswith(('health_check_', '.'))),
                  key=lambda x: str(x.get('modified_at') or ''), reverse=True)
    truncated = len(dirs) > MAX_SESSIONS
    completed = set()
    for directory in dirs[:MAX_SESSIONS]:
        # Never pair a call from one session history with a result from another.
        pending = {}
        ambiguous = set()
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
                    key = (row['request_id'], cid)
                    if key in completed or key in ambiguous:
                        continue
                    if key in pending:
                        pending.pop(key, None)
                        ambiguous.add(key)
                        truncated = True
                        continue
                    name = call.get('name')
                    name = name if name in ('bash','grep','read_file','tool_search') else 'tool'
                    pending[key] = (offset, name)
            elif event.get('event_type') == 'chat.tool_result':
                cid = event.get('tool_call_id')
                if not isinstance(cid, str):
                    continue
                key = (row['request_id'], cid)
                before = pending.pop(key, None)
                if before and offset >= before[0]:
                    completed.add(key)
                    events = row.setdefault('tool_events', [])
                    if len(events) < 200:
                        events.append({'name': before[1], 'start_ms': round(before[0],1),
                                       'duration_ms': round(offset-before[0],1),
                                       'status': 'error' if event.get('success') is False else
                                                 'ok' if event.get('success') is True else 'unknown'})
                    else:
                        truncated = True
    for row in rows:
        row['tool_events'] = sorted(row.get('tool_events', []), key=lambda e:e['start_ms'])
        row['trace_complete'] = False  # bounded histories cannot establish complete coverage
    return not truncated


def attach_answer_estimates(query, rows):
    """Use proven history segment-start timestamps as explicitly labelled estimates."""
    by_id = {row['request_id']: row for row in rows}
    if not by_id:
        return True
    files = listing(query, 'workswarm/.jiuwenswarm/agent/sessions')
    dirs = sorted((entry for entry in files if entry.get('type') == 'directory'
                   and NAME.fullmatch(entry.get('name', ''))
                   and not entry['name'].startswith(('health_check_', '.'))),
                  key=lambda entry: str(entry.get('modified_at') or ''), reverse=True)
    complete = len(dirs) <= MAX_SESSIONS
    for directory in dirs[:MAX_SESSIONS]:
        path = 'workswarm/.jiuwenswarm/agent/sessions/' + directory['name'] + '/history.jsonl'
        try:
            data = fetch('/api/workspace-files', {**query, 'path': path, 'action': 'download'}, download=True)
        except (urllib.error.HTTPError, ValueError, TimeoutError, OSError):
            complete = False
            continue
        for line in data.splitlines():
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict) or event.get('event_type') != 'chat.final':
                continue
            rid = event.get('request_id')
            row = by_id.get(rid) if isinstance(rid, str) else None
            timestamp, completed = event.get('timestamp'), event.get('completed_at')
            if not row or not isinstance(event.get('content'), str) or not event['content'].strip():
                continue
            if (isinstance(timestamp, bool) or isinstance(completed, bool)
                    or not isinstance(timestamp, (int, float)) or not isinstance(completed, (int, float))
                    or not math.isfinite(timestamp) or not math.isfinite(completed) or completed <= timestamp):
                continue
            start = dt.datetime.fromisoformat(row['start_time']).timestamp()
            offset = (timestamp - start) * 1000
            if not 0 <= offset <= row['gateway']:
                continue
            old = row.get('answer_first_history_ms')
            if old is None or offset < old:
                row['answer_first_history_ms'] = round(offset, 1)
    return complete


def request_daily_metrics(records, date, generated_at, incomplete, usage=None, tool=None):
    """Summarize the same finished requests used for the slow-request ranking."""
    def summary(field):
        values = sorted(row[field] for _, _, row in records
                        if isinstance(row.get(field), (int, float))
                        and not isinstance(row[field], bool) and math.isfinite(row[field]))
        if not values:
            return {'n': 0}
        return {'n': len(values), 'sum': sum(values), 'mean': round(sum(values) / len(values), 1),
                'p50': round(values[math.ceil(.5 * len(values)) - 1], 1),
                'p95': round(values[math.ceil(.95 * len(values)) - 1], 1),
                'histogram': metric_histogram([(value, 1) for value in values])}

    statuses = collections.Counter(row.get('gateway_stream_status', 'unknown') for _, _, row in records)
    observed = statuses['normal'] + statuses['exception']
    usage = usage or new_usage()
    ttft_values = sorted(usage['ttft_values'])
    ttft = {'n': len(ttft_values)}
    if ttft_values:
        ttft.update(sum=sum(ttft_values), mean=round(sum(ttft_values) / len(ttft_values), 1),
                    p50=round(ttft_values[math.ceil(.5 * len(ttft_values)) - 1], 1),
                    p95=round(ttft_values[math.ceil(.95 * len(ttft_values)) - 1], 1),
                    histogram=metric_histogram([(value, 1) for value in ttft_values]))
    pairs = sorted(usage['tpot_pairs'])
    weight = sum(w for _, w in pairs)
    safe_weight = weight if weight <= 2**53 - 1 else None
    tpot = {'n': len(pairs), 'tokens': safe_weight}
    if safe_weight:
        def quantile(q):
            target = q * weight
            seen = 0
            for value, count in pairs:
                seen += count
                if seen >= target:
                    return round(value, 2)
        weighted_sum = sum(value * w for value, w in pairs)
        tpot.update(weighted_sum=weighted_sum, mean=round(weighted_sum / weight, 2),
                    p50=quantile(.5), p95=quantile(.95), histogram=metric_histogram(pairs))
    tokens = {key: usage[key] if usage[key + '_samples'] and usage[key] <= 2**53 - 1 else None
              for key in ('input', 'output', 'total')}
    tokens['samples'] = {key: usage[key + '_samples'] for key in ('input', 'output', 'total')}
    result = {'date': date, 'n': len(records),
            'partial': incomplete or date == dt.datetime.now(CST).date().isoformat(),
            'observed_at': generated_at, 'telemetry_observed_at': generated_at,
            'telemetry_partial': incomplete or date == dt.datetime.now(CST).date().isoformat(),
            'source': 'workspace_request_logs',
            'model_usage_events': usage['events'], 'tokens': tokens, 'ttft': ttft, 'tpot': tpot,
            'gateway_stream': {'normal': statuses['normal'], 'exception': statuses['exception'],
                               'cancelled': statuses['cancelled'], 'unknown': statuses['unknown'],
                               'observed': observed, 'population': len(records),
                               'exception_rate': statuses['exception'] / observed if observed else None},
            **{key: summary(key) for key in ('gateway', 'first_token', 'answer_first', 'react',
                                             'gateway_admission', 'first_text_admission', 'answer_admission')},
            'answer_first_history': summary('answer_first_history_ms')}
    if tool:
        values = sorted(tool['durations'])
        metric = {'n': len(values)}
        if values:
            metric.update(sum=sum(values), mean=round(sum(values) / len(values), 1),
                          p50=round(values[math.ceil(.5 * len(values)) - 1], 1),
                          p95=round(values[math.ceil(.95 * len(values)) - 1], 1),
                          histogram=metric_histogram([(value, 1) for value in values]))
        result['tool'] = metric
        result['tool_failed'] = tool['failed']
        result['tool_coverage'] = {key: tool[key] for key in ('started', 'matched', 'unmatched', 'unknown_status')}
    return result


def collect():
    if not TOKEN:
        raise SystemExit('ADX_TOKEN required')
    items = binding_list()
    if not items:
        raise SystemExit('no production bindings')
    snapshots, problems = [], {}
    usage_by_day = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        future_map = {pool.submit(read_log, item): idx for idx, item in enumerate(items)}
        for count, future in enumerate(concurrent.futures.as_completed(future_map), 1):
            idx = future_map[future]
            try:
                query, rows, status, usage = future.result()
                if status == 'ok':
                    snapshots.append((idx, query, rows))
                    for day, values in usage.items():
                        pooled = usage_by_day.setdefault(day, new_usage())
                        for key in ('events', 'input', 'output', 'total', 'input_samples', 'output_samples', 'total_samples'):
                            pooled[key] += values[key]
                        pooled['tpot_pairs'].extend(values['tpot_pairs'])
                        pooled['ttft_values'].extend(values['ttft_values'])
                else: problems[status] = problems.get(status, 0) + 1
            except Exception as error:
                category = 'http_' + str(error.code) if isinstance(error, urllib.error.HTTPError) else type(error).__name__
                problems[category] = problems.get(category, 0) + 1
            if count % 20 == 0 or count == len(items):
                print(f'bindings {count}/{len(items)}; logs read {len(snapshots)}; unavailable {sum(problems.values())}', flush=True)
    if not snapshots:
        raise SystemExit('no production logs could be read; retain previous snapshot')
    tool_by_day = {}
    tool_files_found = tool_incomplete = 0
    for _, query, _ in snapshots:
        try:
            tool_days, found, complete = read_tool_metrics(query)
            tool_files_found += int(found)
            tool_incomplete += int(not complete)
            for day, values in tool_days.items():
                pooled = tool_by_day.setdefault(day, {'started': 0, 'matched': 0, 'unmatched': 0,
                                                       'unknown_status': 0, 'failed': 0, 'durations': []})
                for key in ('started', 'matched', 'unmatched', 'unknown_status', 'failed'):
                    pooled[key] += values[key]
                pooled['durations'].extend(values['durations'])
        except (OSError, ValueError, KeyError, TypeError, urllib.error.HTTPError):
            tool_incomplete += 1
    answer_coverage = collections.Counter()
    for _, query, rows in snapshots:
        try:
            answer_coverage['complete' if attach_answer_estimates(query, rows) else 'partial'] += 1
        except (OSError, ValueError, KeyError, TypeError, urllib.error.HTTPError):
            answer_coverage['partial'] += 1
    grouped = {}
    for idx, query, rows in snapshots:
        for row in rows:
            grouped.setdefault(row['date'], []).append((idx, query, row))
    days, chosen, daily_metrics = [], [], []
    generated_at = dt.datetime.now(UTC).isoformat()
    trace_cutoff = (dt.datetime.now(CST).date() - dt.timedelta(days=TRACE_DAYS - 1)).isoformat()
    for date in sorted(set(grouped) | set(usage_by_day) | set(tool_by_day)):
        records = grouped.get(date, [])
        day_metrics = request_daily_metrics(records, date, generated_at, bool(problems), usage_by_day.get(date), tool_by_day.get(date))
        if 'tool' in day_metrics:
            day_metrics['tool_observed_at'] = generated_at
            day_metrics['tool_collection_partial'] = bool(problems) or bool(tool_incomplete) or tool_files_found < len(snapshots)
        daily_metrics.append(day_metrics)
        if not records or date < trace_cutoff:
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
    details_ok = details_failed = native_count = 0
    for index,(query, rows) in enumerate(by_binding.values(),1):
        native_count += attach_native_trajectories(query, rows)
        try:
            if attach_tool_events(query, rows): details_ok += 1
            else: details_failed += 1
        except Exception:
            details_failed += 1
        print(f'trace workspaces {index}/{len(by_binding)}',flush=True)
    # Session and trace correlation metadata are temporary and never published.
    for _, _, rows in snapshots:
        for row in rows:
            row.pop('_native_correlation', None)
    output = {'schema':'digitalmate/cloud-traces@1','generated_at':generated_at,
              'request_daily_metrics':daily_metrics,
              'binding_count':len(items),'read_log_count':len(snapshots),'errors':problems,
              'tool_file_workspaces':tool_files_found, 'tool_collection_incomplete':tool_incomplete,
              'detail_workspaces_complete':details_ok,'detail_workspaces_partial':details_failed,
              'answer_workspaces_complete':answer_coverage['complete'],
              'answer_workspaces_partial':answer_coverage['partial'],
              'native_trace_count':native_count,
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
