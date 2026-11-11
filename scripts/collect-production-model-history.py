"""Build dashboard history.json from production gateway request logs.

Groups LiteLLM spend logs by (logical model, Asia/Shanghai day) and writes
the digitalmate/e2e-performance-history@1 schema with one case per model:

- case id  PROD-<model_group>            e.g. PROD-deepseek-v4.1-flash-1017
- point per active day:
    query_ms            mean gateway duration of successful calls (legacy view)
    duration_sum_ms     sum of successful gateway durations; duration_sum / count
                        is the volume-weighted mean the dashboard displays
    duration_sample_count, success_count
    duration_p50_ms, duration_p95_ms   request-level percentiles (>=20 samples)
    hours[]             per-hour call / success / duration aggregates so the
                        dashboard can compare the unfinished current day with
                        the same clock window on earlier days
    query_count         number of calls that day (all statuses)
    failed_queries      calls whose status is a failure
    failure_reasons     failure counts keyed by cause (timeout, budget, rate_limit,
                        content_blocked, invalid_request, permission_denied, other)
    timeout_count       failed calls whose gateway error says timeout
    latency_max_ms      slowest request of the day (any status) and its identifiers
    slow_count          size of the slowest 5% cohort
    slow_requests       request_id / litellm_call_id of the slowest requests;
                        kept for the last REQUEST_DETAIL_DAYS days only
    timeout_requests    identifiers of the timed-out requests, same retention
    status              FAILED when failures outnumber successes
    benchmark_models[0] per-day TTFT/TPOT/P50/P95/Token stats (physical model)

Metric rules (see single-repo/static/production-metrics/README.md):

- Gateway request durations are volume-weighted; daily means are never averaged
  again with equal weight.
- TTFT/TPOT use only successful calls whose stream flag is explicitly true.
  Successful calls with unknown streaming are reported separately as estimates;
  explicit non-streaming calls are excluded. TTFT within 5% of total duration
  and TPOT below 0.1 ms/token are treated as timestamp artefacts.
- Tokens count successful calls only and carry a coverage ratio; tokens spent
  on failed attempts are reported separately.
- P50 and P95 are nearest-rank percentiles over the raw successful durations of
  the day and are omitted below 20 samples.

Usage: set PRODUCTION_GATEWAY_KEY and run
python collect-production-model-history.py <data_dir> [fetch_days] [previous_history]

With a previous_history file, only the last fetch_days (default 2) are read
from the gateway and merged into it; older days are kept until they leave the
HISTORY_DAYS retention window. Without it, the full HISTORY_DAYS window is
rebuilt from the gateway. A previous snapshot written by an older metrics
version is rebuilt in full once so every retained day carries the new fields.
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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = 'https://onlineswarm.openjiuwen.com:31474'
SCHEMA = 'digitalmate/e2e-performance-history@1'
METRICS_VERSION = 7
RUN_ID = 'prod-adx-jiuwen'
P50_MIN_SAMPLES = 20
P95_MIN_SAMPLES = 20
TTFT_ANOMALY_RATIO = 0.95
TPOT_MIN_MS = 0.1
HISTORY_DAYS = 35
SH = timezone(timedelta(hours=8))
FAILURE_STATUSES = ('failure', 'failed', 'error')
# The slow tail is a share of the day's requests; only this many rows are
# published, because the table exists to locate requests, not to mirror the log.
SLOW_TAIL_RATIO = 0.05
REQUEST_DETAIL_DAYS = 7
REQUEST_DETAIL_LIMIT = 50
PAGINATION_ATTEMPTS = 3
MIN_SPLIT_WINDOW = timedelta(minutes=15)
# Match provider codes as whole numbers: an id or timestamp that happens to
# contain 429 or 81011 must not classify an error.
CODE_429 = re.compile(r'\b429\b')
CODE_CONTENT_SAFETY = re.compile(r'\b81011\b')
CODE_INVALID_REQUEST = re.compile(r'\b81001\b')


class ChangingPagination(ValueError):
    """The gateway's paginated result changed while it was being read."""


class CappedWindow(ValueError):
    """The gateway refuses to enumerate a window with too many rows.

    The UI endpoint caps a single query at a fixed number of rows and reports
    total_is_capped instead of paginating further. A busy day can exceed that
    cap, so the window has to be split before its rows can be read completely.
    """


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


def metric_histogram(values):
    """Mergeable 2%-wide logarithmic buckets; -1001 represents exact zero."""
    counts = {}
    for value in values:
        index = math.floor(math.log(value) / math.log(1.02)) if value > 0 else -1001
        counts[index] = counts.get(index, 0) + 1
    return sorted(counts.items())


def status_of(row):
    return str(row.get('status') or '').lower()


def failure_information(row):
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    info = metadata.get('error_information')
    return info if isinstance(info, dict) else {}


def failure_category(row):
    """Map one failed gateway row to a stable cause key.

    LiteLLM records the provider error under metadata.error_information; the
    row-level error fields are a fallback for older log shapes. Content safety
    blocks also arrive as HTTP 403, so that check must precede the generic
    permission case.
    """
    if status_of(row) not in FAILURE_STATUSES:
        return None
    info = failure_information(row)
    parts = [info.get(key) for key in ('normalized_error', 'error_class', 'error_message', 'error_code')]
    parts.extend(row.get(key) for key in ('error_message', 'error_code'))
    text = ' '.join(str(value) for value in parts if value).lower()
    if 'timeout' in text or 'timed out' in text or 'deadline exceeded' in text:
        return 'timeout'
    if 'budget_exceeded' in text or 'budget exceeded' in text:
        return 'budget'
    if 'rate_limit' in text or 'rate limit' in text or CODE_429.search(text):
        return 'rate_limit'
    if ('sensitive' in text or CODE_CONTENT_SAFETY.search(text) or 'content_policy' in text
            or 'content filter' in text or 'content_filter' in text):
        return 'content_blocked'
    if 'invalid_request' in text or 'invalid request' in text or CODE_INVALID_REQUEST.search(text):
        return 'invalid_request'
    if 'permission_denied' in text or 'permission denied' in text:
        return 'permission_denied'
    return 'other'


def request_duration(row):
    value = row.get('request_duration_ms')
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def request_detail(row):
    """The identifiers needed to find one request in the gateway console.

    Only opaque request identifiers and timing/status fields are published;
    prompts, messages and user identifiers never leave the collector.
    """
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    return {
        'request_id': str(row.get('request_id') or '') or None,
        'call_id': str(metadata.get('litellm_call_id') or '') or None,
        'start_time': row.get('startTime'),
        'duration_ms': rounded(request_duration(row)),
        'status': str(row.get('status') or ''),
        'reason': failure_category(row),
    }


def stream_state(row):
    """Return True/False when the gateway states streaming, else None.

    The spend log may expose the flag either on the row or under
    metadata.spend_logs_metadata. Conflicting values count as unknown rather
    than silently choosing one.
    """
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    spend_metadata = metadata.get('spend_logs_metadata')
    if not isinstance(spend_metadata, dict):
        spend_metadata = {}
    flags = [row.get('stream'), spend_metadata.get('stream')]
    known = [flag for flag in flags if isinstance(flag, bool)]
    if not known:
        return None
    return known[0] if all(flag == known[0] for flag in known) else None


def token_sums(rows):
    """Sum prompt/completion tokens over rows that report both fields.

    Returns (prompt, completion, total, sample_count). Rows missing either
    field are excluded from the sum instead of being treated as zero, so the
    caller can report coverage against the success count.
    """
    prompt = 0
    completion = 0
    samples = 0
    for row in rows:
        p = row.get('prompt_tokens')
        c = row.get('completion_tokens')
        if type(p) is int and p >= 0 and type(c) is int and c >= 0:
            prompt += p
            completion += c
            samples += 1
    if not samples:
        return None, None, None, 0
    return prompt, completion, prompt + completion, samples


def rounded(value, digits=0):
    if value is None:
        return None
    return round(value, digits) if digits else round(value)


def percentile(values, target, minimum):
    return rounded(percentile_nearest_rank(values, target)) if len(values) >= minimum else None


def fetch_rows_once(key, start, end):
    window = {
        'start_date': start.strftime('%Y-%m-%d %H:%M:%S'),
        'end_date': end.strftime('%Y-%m-%d %H:%M:%S'),
    }
    page_size = 500
    first = fetch_json('/spend/logs/ui', key, {**window, 'page': 1, 'page_size': page_size})
    total_pages = first.get('total_pages')
    total = first.get('total')
    if first.get('total_is_capped'):
        raise CappedWindow('Gateway caps this window before all rows are returned')
    if (not isinstance(total_pages, int) or not 0 <= total_pages <= 2000
            or not isinstance(total, int) or total < 0
            or not isinstance(first.get('data'), list)):
        raise ValueError('Invalid or oversized gateway pagination')
    rows = list(first.get('data', []))
    for page in range(2, total_pages + 1):
        time.sleep(0.2)
        batch = fetch_json('/spend/logs/ui', key, {**window, 'page': page, 'page_size': page_size})
        if (not isinstance(batch.get('data'), list) or batch.get('total_pages') != total_pages
                or batch.get('total') != total or batch.get('total_is_capped')):
            raise ChangingPagination('Incomplete or changing gateway pagination')
        rows.extend(batch['data'])
    ids = [row.get('request_id') for row in rows if isinstance(row, dict)]
    if (len(rows) != total or len(ids) != len(rows) or any(not item for item in ids)
            or len(set(ids)) != len(ids)):
        raise ChangingPagination('Missing, duplicate or incomplete gateway records')
    return rows


def fetch_rows(key, start, end):
    """Read a complete window; split changing or capped windows, never publish partial pages."""
    last_error = None
    for attempt in range(PAGINATION_ATTEMPTS):
        try:
            return fetch_rows_once(key, start, end)
        except CappedWindow as error:
            # Retrying cannot help: the gateway will cap the same window again.
            last_error = error
            break
        except ChangingPagination as error:
            last_error = error
            if attempt + 1 < PAGINATION_ATTEMPTS:
                print(f'Gateway pagination changed for {start:%Y-%m-%d %H:%M:%S}..'
                      f'{end:%Y-%m-%d %H:%M:%S} UTC; retrying the whole window '
                      f'({attempt + 1}/{PAGINATION_ATTEMPTS}).', flush=True)
                time.sleep(attempt + 1)
                continue
            break

    if end - start < MIN_SPLIT_WINDOW * 2:
        raise last_error or ChangingPagination('Cannot split gateway window')
    # API times are serialized at second precision; split on a whole second so
    # adjacent windows overlap only at their shared boundary.
    midpoint = (start + (end - start) / 2).replace(microsecond=0)
    if midpoint <= start or midpoint >= end:
        raise last_error or ChangingPagination('Cannot safely split gateway window')
    print(f'Splitting gateway window {start:%Y-%m-%d %H:%M:%S}..'
          f'{end:%Y-%m-%d %H:%M:%S} UTC ({last_error}).', flush=True)
    combined = {}
    for row in fetch_rows(key, start, midpoint) + fetch_rows(key, midpoint, end):
        request_id = row['request_id']
        if request_id in combined and combined[request_id] != row:
            raise ChangingPagination('Gateway record changed across split window boundary')
        combined[request_id] = row
    return list(combined.values())


def fetch_window(key, start, end, workers=3):
    # Split large windows into overlapping one-day pages; bound gateway load and
    # keep page validation within each day before deduplicating boundary rows.
    rows = []
    seen = {}
    cursor = start
    windows = []
    while cursor < end:
        stop = min(cursor + timedelta(days=1), end)
        windows.append((cursor, stop))
        cursor = stop

    def collect_day(window):
        day_start, day_end = window
        result = fetch_rows(key, day_start, day_end)
        print(f'Collected window {day_start:%Y-%m-%d %H:%M} UTC: {len(result)} records', flush=True)
        return result

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for batch in executor.map(collect_day, windows):
            for row in batch:
                request_id = row['request_id']
                if request_id in seen and seen[request_id] != row:
                    raise ValueError('Gateway record changed across window boundaries')
                if request_id not in seen:
                    rows.append(row)
                    seen[request_id] = row
    return rows


def load_previous_history(path):
    """Read the published snapshot that fresh days are merged into."""
    if not path:
        return None
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get('cases'), dict):
        raise ValueError('Previous history must contain a cases object')
    return doc


def prune_request_details(cases, keep_from_date):
    """Drop request-identifier tables from days outside the detail window.

    Counts, causes and the maximum-duration summary stay on every retained day,
    so a 30-day view keeps its failure trend without carrying identifiers for
    requests that are already hard to find in the gateway UI.
    """
    for case in cases.values():
        for point in case.get('points') or []:
            date = point.get('date')
            if isinstance(date, str) and date < keep_from_date:
                point.pop('slow_requests', None)
                point.pop('timeout_requests', None)
    return cases


def merge_history(previous_cases, fresh_cases, cutoff_date):
    """Merge freshly collected days into the published case history.

    Fresh points replace the same dates from the previous snapshot, older days
    are kept until they fall outside the retention window, and cases whose
    points have all aged out are dropped.
    """
    merged = {}
    for case_id, case in previous_cases.items():
        if not isinstance(case, dict):
            continue
        points = [point for point in case.get('points') or []
                  if isinstance(point, dict) and isinstance(point.get('date'), str)
                  and point['date'] >= cutoff_date]
        if points:
            merged[case_id] = {'title': case.get('title') or case_id, 'points': points}
    for case_id, case in fresh_cases.items():
        slot = merged.setdefault(case_id, {'title': case['title'], 'points': []})
        by_date = {point['date']: point for point in slot['points']}
        for point in case['points']:
            by_date[point['date']] = point
        slot['title'] = case['title']
        slot['points'] = [by_date[date] for date in sorted(by_date)]
    return {
        case_id: {'title': slot['title'], 'points': slot['points'], 'latest': dict(slot['points'][-1])}
        for case_id, slot in sorted(merged.items())
        if slot['points']
    }


def aggregate_point(day_rows, date, physical, partial_day=None, include_details=True):
    """Aggregate one (case, Asia/Shanghai day) into a history point.

    include_details controls only the request-identifier tables; counts and the
    maximum duration stay on every retained day so the range view keeps its
    failure and latency summaries. Details are pruned after merge by
    prune_request_details.
    """
    durations = []
    success_rows = []
    failed_rows = []
    unknown = 0
    for row in day_rows:
        status = status_of(row)
        if status == 'success':
            success_rows.append(row)
            value = row.get('request_duration_ms')
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                durations.append(value)
        elif status in FAILURE_STATUSES:
            failed_rows.append(row)
        else:
            unknown += 1
    failed = len(failed_rows)
    failure_reasons = defaultdict(int)
    timeout_rows = []
    for row in failed_rows:
        category = failure_category(row) or 'other'
        failure_reasons[category] += 1
        if category == 'timeout':
            timeout_rows.append(row)

    # Slow-tail and maximum-latency analysis spans all statuses: a timed-out
    # request is often the slowest row of the day and must not disappear just
    # because it failed.
    timed_rows = []
    for row in day_rows:
        duration = request_duration(row)
        if duration is not None:
            timed_rows.append((duration, row))
    timed_rows.sort(key=lambda item: (item[0], str(item[1].get('request_id') or '')), reverse=True)
    slow_count = math.ceil(len(timed_rows) * SLOW_TAIL_RATIO) if timed_rows else 0
    slow_rows = timed_rows[:slow_count]
    max_row = timed_rows[0][1] if timed_rows else None

    ttft_strict = []
    ttft_estimated = []
    tpot_strict = []
    tpot_estimated = []
    ttft_filtered = 0
    tpot_filtered = 0
    hours = {}
    for row in day_rows:
        start_dt = parse_dt(row.get('startTime'))
        first_dt = parse_dt(row.get('completionStartTime'))
        end_dt = parse_dt(row.get('endTime'))
        duration = None
        if start_dt and end_dt and end_dt >= start_dt:
            duration = (end_dt - start_dt).total_seconds() * 1000
        if start_dt:
            bucket = hours.setdefault(start_dt.astimezone(SH).hour, {
                'hour': start_dt.astimezone(SH).hour,
                'calls': 0,
                'success_count': 0,
                'duration_sum_ms': 0,
                'duration_sample_count': 0,
                'failed': 0,
            })
            bucket['calls'] += 1
            if status_of(row) == 'success':
                bucket['success_count'] += 1
            elif status_of(row) in FAILURE_STATUSES:
                bucket['failed'] += 1
            value = row.get('request_duration_ms')
            if (status_of(row) == 'success' and isinstance(value, (int, float))
                    and math.isfinite(value) and value >= 0):
                bucket['duration_sum_ms'] += value
                bucket['duration_sample_count'] += 1
        if status_of(row) != 'success':
            continue
        stream = stream_state(row)
        if stream is False:
            # A known non-streaming call cannot measure first-token latency.
            continue
        if not (start_dt and first_dt and end_dt and start_dt <= first_dt < end_dt):
            continue
        ttft = (first_dt - start_dt).total_seconds() * 1000
        total_ms = duration if duration and duration > 0 else None
        timestamp_anomaly = bool(total_ms and ttft >= TTFT_ANOMALY_RATIO * total_ms)
        if timestamp_anomaly:
            # first token arriving with the last token means the timestamp is a
            # completion time, not a measurable TTFT.
            ttft_filtered += 1
        elif ttft >= 0:
            (ttft_strict if stream is True else ttft_estimated).append(ttft)
        completion = row.get('completion_tokens')
        if timestamp_anomaly and isinstance(completion, int) and completion > 1:
            tpot_filtered += 1
        elif isinstance(completion, int) and completion > 1:
            tpot = (end_dt - first_dt).total_seconds() * 1000 / (completion - 1)
            if tpot < TPOT_MIN_MS:
                tpot_filtered += 1
            elif tpot >= 0:
                (tpot_strict if stream is True else tpot_estimated).append(
                    ((end_dt - first_dt).total_seconds() * 1000, completion - 1))

    prompt, completion_tokens, total, token_samples = token_sums(success_rows)
    failed_prompt, failed_completion, failed_total, failed_samples = token_sums(failed_rows)
    benchmark = {
        'model': physical,
        'call_count': len(day_rows),
        'success_count': len(success_rows),
        'failed_count': failed,
        'unknown_status_count': unknown,
        'ttft_avg_ms': rounded(mean(ttft_strict)) if ttft_strict else None,
        'ttft_sample_count': len(ttft_strict),
        'ttft_sum_ms': rounded(sum(ttft_strict)) if ttft_strict else None,
        'ttft_estimated_avg_ms': rounded(mean(ttft_estimated)) if ttft_estimated else None,
        'ttft_estimated_sample_count': len(ttft_estimated),
        'ttft_estimated_sum_ms': rounded(sum(ttft_estimated)) if ttft_estimated else None,
        'ttft_estimated_histogram': metric_histogram(ttft_estimated),
        'ttft_filtered_count': ttft_filtered,
        'tpot_avg_ms': sum(item[0] for item in tpot_strict) / sum(item[1] for item in tpot_strict) if tpot_strict else None,
        'tpot_sample_count': len(tpot_strict),
        'tpot_decode_sum_ms': sum(item[0] for item in tpot_strict) if tpot_strict else None,
        'tpot_weight_tokens': sum(item[1] for item in tpot_strict),
        'tpot_estimated_avg_ms': sum(item[0] for item in tpot_estimated) / sum(item[1] for item in tpot_estimated) if tpot_estimated else None,
        'tpot_estimated_sample_count': len(tpot_estimated),
        'tpot_estimated_decode_sum_ms': sum(item[0] for item in tpot_estimated) if tpot_estimated else None,
        'tpot_estimated_weight_tokens': sum(item[1] for item in tpot_estimated),
        'tpot_histogram': metric_histogram(duration / tokens for duration, tokens in tpot_strict),
        'tpot_estimated_histogram': metric_histogram(duration / tokens for duration, tokens in tpot_estimated),
        'tpot_filtered_count': tpot_filtered,
        # The sum lets the dashboard weight this model-day against every other
        # model-day without ever averaging daily means.
        'duration_sum_ms': rounded(sum(durations)) if durations else None,
        'duration_p50_ms': percentile(durations, 0.50, P50_MIN_SAMPLES),
        'duration_p95_ms': percentile(durations, 0.95, P95_MIN_SAMPLES),
        'duration_sample_count': len(durations),
        'duration_histogram': metric_histogram(durations),
        'input_tokens': prompt,
        'output_tokens': completion_tokens,
        'total_tokens': total,
        'token_sample_count': token_samples,
        'token_coverage': round(token_samples / len(success_rows), 4) if success_rows else None,
        'failed_input_tokens': failed_prompt,
        'failed_output_tokens': failed_completion,
        'failed_total_tokens': failed_total,
        'failed_token_sample_count': failed_samples,
    }
    last_end = max((dt for r in day_rows if (dt := parse_dt(r.get('endTime'))) is not None), default=None)
    point = {
        'date': date,
        'status': 'UNKNOWN' if unknown else ('FAILED' if failed > len(success_rows) else 'COMPLETED'),
        'partial': date == partial_day,
        'query_ms': rounded(mean(durations)) if durations else None,
        'duration_sum_ms': rounded(sum(durations)) if durations else None,
        'duration_sample_count': len(durations),
        'duration_p50_ms': percentile(durations, 0.50, P50_MIN_SAMPLES),
        'duration_p95_ms': percentile(durations, 0.95, P95_MIN_SAMPLES),
        'duration_histogram': metric_histogram(durations),
        'success_count': len(success_rows),
        'query_count': len(day_rows),
        'run_id': RUN_ID,
        'commit': '',
        'finished_at': last_end.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if last_end else None,
        'failed_queries': failed,
        'unknown_status_count': unknown,
        'blocked_queries': 0,
        'failure_reasons': dict(sorted(failure_reasons.items(), key=lambda item: (-item[1], item[0]))),
        'timeout_count': len(timeout_rows),
        'latency_max_ms': rounded(timed_rows[0][0]) if timed_rows else None,
        'latency_max_request': request_detail(max_row) if max_row is not None else None,
        'slow_count': slow_count,
        'slow_threshold_ms': rounded(slow_rows[-1][0]) if slow_rows else None,
        'hours': [hours[hour] for hour in sorted(hours)],
        'benchmark_models': [benchmark],
    }
    if include_details:
        point['slow_requests'] = [request_detail(row) for _, row in slow_rows[:REQUEST_DETAIL_LIMIT]]
        point['timeout_requests'] = [
            request_detail(row) for row in sorted(
                timeout_rows, key=lambda row: request_duration(row) or 0, reverse=True)[:REQUEST_DETAIL_LIMIT]
        ]
    return point


def main():
    data_dir = Path(sys.argv[1])
    key = os.environ['PRODUCTION_GATEWAY_KEY']
    days = int(sys.argv[2]) if len(sys.argv) > 2 else HISTORY_DAYS
    if not 1 <= days <= 365:
        raise ValueError('Days must be between 1 and 365')
    previous = load_previous_history(sys.argv[3] if len(sys.argv) > 3 else '')
    if previous is not None and int(previous.get('metrics_version') or 1) < METRICS_VERSION:
        # Older snapshots lack the weighted sums, request-level percentiles and
        # strict-streaming fields. Backfill the retained window once instead of
        # mixing two metric definitions for up to 35 days.
        print('Previous snapshot uses an older metrics version; rebuilding the full window.', flush=True)
        previous = None
        days = HISTORY_DAYS
    if previous is None and days < HISTORY_DAYS:
        # Nothing to merge into: rebuild the full window rather than publish a
        # history that only covers the freshly fetched days.
        days = HISTORY_DAYS
    if previous is not None and not previous.get('duration_hist_backfill_done'):
        # Backfill the recent model groups once so same-model percentiles can
        # be pooled instead of disappearing when logical model groups overlap.
        days = max(days, 7)

    end = datetime.now(timezone.utc).replace(microsecond=0)
    today = end.astimezone(SH).replace(hour=0, minute=0, second=0, microsecond=0)
    partial_day = end.astimezone(SH).date().isoformat()
    detail_from = (end.astimezone(SH).date() - timedelta(days=REQUEST_DETAIL_DAYS - 1)).isoformat()
    complete_through = (end.astimezone(SH).date() - timedelta(days=1)).isoformat()
    history_start = today - timedelta(days=HISTORY_DAYS - 1)
    fetch_start = today - timedelta(days=days - 1)
    rows = fetch_window(key, fetch_start.astimezone(timezone.utc), end)

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
        points = [
            aggregate_point(by_day[date], date, physical, partial_day,
                            include_details=date >= detail_from)
            for date in sorted(by_day)
        ]
        if not points:
            continue
        latest = dict(points[-1])
        cases[case_id] = {
            'title': f'现网模型 {model_group}',
            'points': points,
            'latest': latest,
        }

    if previous is not None:
        cases = merge_history(previous.get('cases', {}), cases, history_start.date().isoformat())
    # Older snapshots may still carry detail tables inside the new window.
    prune_request_details(cases, detail_from)

    generated = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    doc = {
        'schema': SCHEMA,
        'metrics_version': METRICS_VERSION,
        'duration_hist_backfill_done': True,
        'generated_at': generated,
        'updated_at': generated,
        'window_start': history_start.astimezone(timezone.utc).isoformat(),
        'window_end': end.isoformat(),
        'current_day_partial': True,
        'complete_through': complete_through,
        'partial_day': partial_day,
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
    mode = 'merge' if previous is not None else 'rebuild'
    print(f'{mode}: fetched {fetch_start.strftime("%Y-%m-%d")} .. {end.strftime("%Y-%m-%d")} UTC | rows {len(rows)} -> {sum(len(c["points"]) for c in cases.values())} case-days')
    for case_id, case in cases.items():
        calls = sum(p['query_count'] for p in case['points'])
        print(f'  {case_id}: {len(case["points"])} days, {calls} calls')


if __name__ == '__main__':
    main()
