"""Aggregate prefix-cache evidence on the production mount before exporting it."""

import collections
import datetime as dt
import json
import math
import os
import re
import stat
import time


CACHE_SCHEMA = 'digitalmate/prefix-cache-daily@1'
CACHE_TZ = dt.timezone(dt.timedelta(hours=8))
CACHE_DAYS = 30
CACHE_INPUT_MIN = 1024
CACHE_BINS = 1000
CACHE_BASELINE_DAYS = 7
CACHE_MIN_WATCH_CALLS = 20
CACHE_LOGS = {
    'beebou': ('beebou-prefix-cache.jsonl', 4 * 1024**2),
    'bees': ('bee-workswarm/.jiuwenswarm/agent/.logs/context_usage.jsonl', 10 * 1024**2),
    'workswarm': ('workswarm/.jiuwenswarm/agent/.logs/context_usage.jsonl', 10 * 1024**2),
}
CACHE_ID = re.compile(r'[a-f0-9]{32}')
CACHE_MODEL = re.compile(r'(?:[A-Za-z0-9._-]+/)?[A-Za-z0-9][A-Za-z0-9._:-]{0,95}')
CacheCall = collections.namedtuple('CacheCall', 'id time session model input cached')


def cache_call(line, source):
    """Project the two persisted event formats onto the same call evidence."""
    row = json.loads(line)
    if not isinstance(row, dict):
        return None
    if source == 'beebou':
        if row.get('schema') != 'digitalmate/beebou-prefix-cache@1':
            return None
        when = row.get('at_ms')
        if type(when) is not int:
            return None
        when = dt.datetime.fromtimestamp(when / 1000, dt.timezone.utc)
        call_id = row.get('call_id')
        session = row.get('session_key')
        usage = row
        cached = usage.get('cache_read_tokens')
    else:
        if row.get('event') != 'context.usage':
            return None
        row = row.get('payload')
        if not isinstance(row, dict) or row.get('schema_version') != 'context-usage.v1' or row.get('phase') != 'post_call':
            return None
        when = row.get('timestamp')
        if not isinstance(when, str):
            return None
        when = dt.datetime.fromisoformat(when)
        if when.utcoffset() is None:
            return None
        call_id = row.get('request_id')
        session = row.get('execution_session_id') or row.get('session_id')
        kv_cache = row.get('kv_cache')
        usage = kv_cache.get('request') if isinstance(kv_cache, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        cached = usage.get('cache_read_tokens') if usage.get('authoritative') is True and not usage.get('invalid_reason') else None
    if not isinstance(call_id, str) or not CACHE_ID.fullmatch(call_id):
        return None
    model = row.get('model')
    if not isinstance(model, str) or len(model) > 96 or not CACHE_MODEL.fullmatch(model):
        model = None
    if not isinstance(session, str) or not 0 < len(session) <= 512:
        session = None
    tokens = usage.get('input_tokens')
    if type(tokens) is not int or not 0 < tokens <= 2**53 - 1:
        tokens = None
    if tokens is None or type(cached) is not int or not 0 <= cached <= tokens:
        cached = None
    return CacheCall(call_id, when, session, model, tokens, cached)


def cache_counter():
    return {'calls': 0, 'known': 0, 'input_tokens': 0, 'cache_read_tokens': 0,
            'miss_eligible': 0, 'near_miss': 0, 'histogram': collections.Counter()}


def count_cache_call(stats, call):
    stats['calls'] += 1
    if call.cached is None:
        return
    stats['known'] += 1
    stats['input_tokens'] += call.input
    stats['cache_read_tokens'] += call.cached
    stats['histogram'][call.cached * CACHE_BINS // call.input] += 1
    if call.input >= CACHE_INPUT_MIN:
        stats['miss_eligible'] += 1
        stats['near_miss'] += call.cached * 100 <= call.input


def cache_session_percentile(rates, percentile):
    return rates[math.ceil(percentile * len(rates)) - 1] if rates else None


def collect_prefix_cache(names, resolve_path, *, now=None):
    """Return counts and distributions only. The workspace and session keys stay here."""
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(CACHE_TZ).date()
    start = today - dt.timedelta(days=CACHE_DAYS - 1)
    groups = collections.defaultdict(dict)
    sessions = collections.defaultdict(list)
    coverage = {source: {'read': 0, 'with_calls': 0, 'incomplete': 0, 'invalid_lines': 0,
                         'conflicting_calls': 0} for source in CACHE_LOGS}
    # This is supplemental to the existing production snapshot. Keep its
    # workload bounded even if every workspace has its full log rotation.
    deadline = time.monotonic() + 60
    remaining_bytes = 512 * 1024**2
    for name in names:
        for source, (relative, limit) in CACHE_LOGS.items():
            calls = {}
            counts = coverage[source]
            read = False
            incomplete = False
            for generation in ('', '.1', '.2', '.3'):
                if time.monotonic() >= deadline or not remaining_bytes:
                    incomplete = True
                    break
                try:
                    path = resolve_path(name, relative + generation)
                    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
                    with os.fdopen(os.open(path, flags), 'rb') as stream:
                        file_stat = os.fstat(stream.fileno())
                        size = file_stat.st_size
                        if not stat.S_ISREG(file_stat.st_mode) or size > min(limit, remaining_bytes):
                            incomplete = True
                            continue
                        remaining_bytes -= size
                        read = True
                        while size and time.monotonic() < deadline:
                            line = stream.readline(size)
                            if not line or not line.endswith(b'\n'):
                                incomplete = True
                                break
                            size -= len(line)
                            try:
                                call = cache_call(line, source)
                            except (ValueError, OverflowError, OSError, RecursionError):
                                counts['invalid_lines'] += 1
                                continue
                            if call is None or not start <= call.time.astimezone(CACHE_TZ).date() <= today:
                                continue
                            if calls.setdefault(call.id, call) != call:
                                calls[call.id] = None
                        incomplete |= size > 0
                except FileNotFoundError:
                    continue
                except (OSError, ValueError):
                    incomplete = True
            counts['read'] += read
            counts['incomplete'] += incomplete
            counts['conflicting_calls'] += sum(call is None for call in calls.values())
            observed = sorted((call for call in calls.values() if call is not None), key=lambda c: (c.time, c.id))
            counts['with_calls'] += bool(observed)
            first_seen = set()
            local_sessions = collections.defaultdict(lambda: [0, 0])
            for call in observed:
                date = call.time.astimezone(CACHE_TZ).date().isoformat()
                cohort = 'unknown'
                if call.session is not None:
                    cohort = 'returning' if call.session in first_seen else 'first_observed'
                    first_seen.add(call.session)
                    if call.cached is not None:
                        tokens = local_sessions[(date, call.session)]
                        tokens[0] += call.input
                        tokens[1] += call.cached
                group = groups[date].setdefault((source, call.model, cohort), cache_counter())
                count_cache_call(group, call)
            for (date, _), (tokens, cached) in local_sessions.items():
                sessions[(date, source)].append(cached / tokens)

    days = []
    for date, daily_groups in sorted(groups.items()):
        rows = []
        for (source, model, cohort), stats in daily_groups.items():
            histogram = sorted(stats.pop('histogram').items())
            rows.append(dict(stats, source=source, model=model, cohort=cohort, histogram=histogram))
        session_stats = {}
        for source in CACHE_LOGS:
            rates = sorted(sessions[(date, source)])
            session_stats[source] = {'known': len(rates), 'p50': cache_session_percentile(rates, .5),
                                     'p95': cache_session_percentile(rates, .95)}
        days.append({'date': date, 'calls': sum(row['calls'] for row in rows), 'groups': rows,
                     'sessions': session_stats, 'observed_at': now.isoformat(), 'retained': False,
                     'incomplete_sources': [source for source, counts in coverage.items() if counts['incomplete']]})
    return {'schema': CACHE_SCHEMA, 'observed_at': now.isoformat(), 'today': today.isoformat(),
            'start': start.isoformat(), 'workspaces': len(names), 'coverage': coverage, 'days': days}


def retain_prefix_cache(previous, current):
    """Keep the widest observed population per day across rotating log reads."""
    rows = {row['date']: dict(row) for row in current['days']}
    if previous and previous.get('schema') == CACHE_SCHEMA:
        for old in previous.get('days', []):
            date = old['date']
            observed = rows.get(date)
            if current['start'] <= date <= current['today'] and (observed is None or old['calls'] > observed['calls']):
                rows[date] = dict(old, retained=True)
    days = [rows[date] for date in sorted(rows)]
    # Watch returning sessions separately from first observations, where
    # cold starts are expected. Both rate and count must exceed the baseline.
    for index, day in enumerate(days):
        date = dt.date.fromisoformat(day['date'])
        cutoff = (date - dt.timedelta(days=CACHE_BASELINE_DAYS)).isoformat()
        history = [old for old in days[max(0, index - CACHE_BASELINE_DAYS):index] if old['date'] >= cutoff]
        day['watch'] = {source: prefix_cache_watch(day, history, source) for source in CACHE_LOGS}
    return dict(current, days=days)


def prefix_cache_watch(day, history, source):
    def sample(row):
        groups = [group for group in row['groups'] if group['source'] == source and group['cohort'] == 'returning']
        return sum(group['near_miss'] for group in groups), sum(group['miss_eligible'] for group in groups)

    misses, eligible = sample(day)
    baseline = [pair for row in history if source not in row.get('incomplete_sources', ())
                and (pair := sample(row))[1] >= CACHE_MIN_WATCH_CALLS]
    status = 'insufficient_baseline'
    if source in day.get('incomplete_sources', ()):
        status = 'incomplete'
    elif eligible >= CACHE_MIN_WATCH_CALLS and len(baseline) >= 3:
        exceeded = all(misses > prior and misses * prior_n > prior * eligible for prior, prior_n in baseline)
        status = 'spike' if exceeded else 'within_baseline'
    return {'near_miss': misses, 'eligible': eligible, 'status': status,
            'baseline_days': len(baseline), 'baseline_max': max((m for m, _ in baseline), default=None)}
