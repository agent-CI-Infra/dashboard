"""Build dashboard history.json from production gateway request logs.

Groups LiteLLM spend logs by (logical model, Asia/Shanghai day) and writes
the digitalmate/e2e-performance-history@1 schema with one case per model:

- case id  PROD-<model_group>            e.g. PROD-deepseek-v4.1-flash-1017
- point per active day: query_ms        mean gateway duration of the day
                        query_count     number of calls that day
                        failed_queries  calls whose status != success
                        status          FAILED when failures outnumber successes
                        benchmark_models[0] per-day TTFT/TPOT/P95/Token stats
                          (physical model name, P95 only with >=20 samples)

Usage: set PRODUCTION_GATEWAY_KEY and run python collect-production-model-history.py <data_dir> [days]
"""
import json
import hashlib
import os
import math
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = 'https://onlineswarm.openjiuwen.com:31474'
SCHEMA = 'digitalmate/e2e-performance-history@1'
RUN_ID = 'prod-adx-jiuwen'
P95_MIN_SAMPLES = 20
SH = timezone(timedelta(hours=8))


def fetch_json(path, key, params=None):
    url = f'{BASE}{path}'
    if params:
        url += '?' + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={'Authorization': f'Bearer {key}'})
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode('utf-8'))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            last_error = error
            time.sleep(1.5 * (attempt + 1))
    raise last_error


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%f%z')
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def mean(values):
    return sum(values) / len(values) if values else None


def percentile_nearest_rank(values, percentile):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))]


def fetch_rows(key, start, end):
    window = {
        'start_date': start.strftime('%Y-%m-%d %H:%M:%S'),
        'end_date': end.strftime('%Y-%m-%d %H:%M:%S'),
    }
    page_size = 500
    first = fetch_json('/spend/logs/ui', key, {**window, 'page': 1, 'page_size': page_size})
    total_pages = first.get('total_pages')
    total = first.get('total')
    if (not isinstance(total_pages, int) or not 0 <= total_pages <= 2000
            or not isinstance(total, int) or total < 0 or first.get('total_is_capped')
            or not isinstance(first.get('data'), list)):
        raise ValueError('Invalid or oversized gateway pagination')
    rows = list(first.get('data', []))
    for page in range(2, total_pages + 1):
        time.sleep(0.2)
        batch = fetch_json('/spend/logs/ui', key, {**window, 'page': page, 'page_size': page_size})
        if (not isinstance(batch.get('data'), list) or batch.get('total_pages') != total_pages
                or batch.get('total') != total or batch.get('total_is_capped')):
            raise ValueError('Incomplete or changing gateway pagination')
        rows.extend(batch['data'])
    ids = [row.get('request_id') for row in rows if isinstance(row, dict)]
    if (len(rows) != total or len(ids) != len(rows) or any(not item for item in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('Missing, duplicate or incomplete gateway records')
    return rows


def fetch_window(key, start, end):
    # Split large windows into overlapping one-day pages; deduplicate boundary rows.
    rows = []
    seen = {}
    cursor = start
    while cursor < end:
        stop = min(cursor + timedelta(days=1), end)
        for row in fetch_rows(key, cursor, stop):
            request_id = row['request_id']
            if request_id in seen and seen[request_id] != row:
                raise ValueError('Gateway record changed across window boundaries')
            if request_id not in seen:
                rows.append(row)
                seen[request_id] = row
        cursor = stop
    return rows


def complete_token_sum(rows, field):
    values = [row.get(field) for row in rows]
    return sum(values) if all(type(value) is int and value >= 0 for value in values) else None


def main():
    data_dir = Path(sys.argv[1])
    key = os.environ['PRODUCTION_GATEWAY_KEY']
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 35
    if not 1 <= days <= 365:
        raise ValueError('Days must be between 1 and 365')

    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = (end.astimezone(SH).replace(hour=0, minute=0, second=0, microsecond=0)
             - timedelta(days=days - 1)).astimezone(timezone.utc)
    rows = fetch_window(key, start, end)

    seen_ids = set()
    groups = defaultdict(list)
    excluded_missing_model = 0
    for row in rows:
        model_group = (row.get('model_group') or '').strip()
        physical = (row.get('model') or '').strip()
        if not model_group or not physical:
            excluded_missing_model += 1
            continue
        request_id = row.get('request_id')
        if request_id:
            if request_id in seen_ids:
                continue
            seen_ids.add(request_id)
        groups[(model_group, physical)].append(row)

    cases = {}
    for (model_group, physical), items in sorted(groups.items()):
        by_day = defaultdict(list)
        for item in items:
            start_dt = parse_dt(item.get('startTime'))
            if not start_dt:
                continue
            by_day[start_dt.astimezone(SH).date().isoformat()].append(item)
        identifier = model_group + '\0' + physical
        case_id = 'PROD-' + re.sub(r'[^A-Za-z0-9._-]+', '-', model_group + '-' + physical) + '-' + hashlib.sha256(identifier.encode()).hexdigest()[:12]
        points = []
        for date in sorted(by_day):
            day_rows = by_day[date]
            durations = [r['request_duration_ms'] for r in day_rows
                         if (r.get('status') or '').lower() == 'success' and isinstance(r.get('request_duration_ms'), (int, float)) and math.isfinite(r['request_duration_ms']) and r['request_duration_ms'] >= 0]
            failed = sum(1 for r in day_rows if (r.get('status') or '').lower() in ('failure', 'failed', 'error'))
            unknown = sum(1 for r in day_rows if (r.get('status') or '').lower() not in ('success', 'failure', 'failed', 'error'))
            ttft_values = []
            tpot_values = []
            for row in day_rows:
                start_dt = parse_dt(row.get('startTime'))
                first_dt = parse_dt(row.get('completionStartTime'))
                end_dt = parse_dt(row.get('endTime'))
                metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
                spend_metadata = metadata.get('spend_logs_metadata')
                if not isinstance(spend_metadata, dict):
                    spend_metadata = {}
                # Unknown stream status is allowed as an explicitly labelled estimate.
                # A known non-streaming call cannot measure first-token latency.
                if ((row.get('status') or '').lower() == 'success'
                        and row.get('stream') is not False and spend_metadata.get('stream') is not False
                        and start_dt and first_dt and end_dt
                        and start_dt <= first_dt < end_dt):
                    ttft = (first_dt - start_dt).total_seconds() * 1000
                    if ttft >= 0:
                        ttft_values.append(ttft)
                    completion = row.get('completion_tokens')
                    if first_dt and end_dt and isinstance(completion, int) and completion > 1:
                        tpot = (end_dt - first_dt).total_seconds() * 1000 / (completion - 1)
                        if tpot >= 0:
                            tpot_values.append(tpot)
            prompt = complete_token_sum(day_rows, 'prompt_tokens')
            completion_tokens = complete_token_sum(day_rows, 'completion_tokens')
            total = prompt + completion_tokens if prompt is not None and completion_tokens is not None else None
            benchmark = {
                'model': physical,
                'call_count': len(day_rows),
                'ttft_avg_ms': round(mean(ttft_values)) if ttft_values else None,
                'ttft_sample_count': len(ttft_values),
                'tpot_avg_ms': round(mean(tpot_values), 2) if tpot_values else None,
                'tpot_sample_count': len(tpot_values),
                'duration_p95_ms': round(percentile_nearest_rank(durations, 0.95))
                if len(durations) >= P95_MIN_SAMPLES
                else None,
                'duration_sample_count': len(durations),
                'input_tokens': prompt,
                'output_tokens': completion_tokens,
                'total_tokens': total,
            }
            last_end = max((dt for r in day_rows if (dt := parse_dt(r.get('endTime'))) is not None), default=None)
            points.append({
                'date': date,
                'status': 'FAILED' if failed > len(day_rows) - failed else 'COMPLETED',
                'query_ms': round(mean(durations)) if durations else None,
                'query_count': len(day_rows),
                'run_id': RUN_ID,
                'commit': '',
                'finished_at': last_end.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if last_end else None,
                'failed_queries': failed,
                'unknown_status_count': unknown,
                'blocked_queries': 0,
                'benchmark_models': [benchmark],
            })
        if not points:
            continue
        latest = dict(points[-1])
        cases[case_id] = {
            'title': f'现网模型 {model_group}',
            'points': points,
            'latest': latest,
        }

    generated = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    doc = {
        'schema': SCHEMA,
        'generated_at': generated,
        'updated_at': generated,
        'window_start': start.isoformat(),
        'window_end': end.isoformat(),
        'current_day_partial': True,
        'source_scope': 'gateway-all-requests',
        'excluded_missing_model': excluded_missing_model,
        'device': {'model': '现网网关 prod / adx-jiuwen', 'kind': 'gateway'},
        'cases': cases,
    }
    out = data_dir / 'production-model-history.json'
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=data_dir, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(doc, handle, ensure_ascii=False, indent=1, allow_nan=False)
        os.replace(temp, out)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    print(f'window {start.strftime("%Y-%m-%d")} .. {end.strftime("%Y-%m-%d")} UTC | rows {len(rows)} -> {sum(len(c["points"]) for c in cases.values())} case-days')
    for case_id, case in cases.items():
        calls = sum(p['query_count'] for p in case['points'])
        print(f'  {case_id}: {len(case["points"])} days, {calls} calls')


if __name__ == '__main__':
    main()
