"""Content-free daily Hive timings, independent of the legacy stream-log clock."""
import collections
import contextlib
import datetime as dt
import math
import re
import sqlite3
import time

HIVE_SCHEMA = 'digitalmate/hive-daily@1'
HIVE_TZ = dt.timezone(dt.timedelta(hours=8))
HIVE_DAYS = 30
HIVE_ROWS = 100_000
HIVE_SECONDS = 240
HIVE_METRICS = ('normal_claim', 'quick_commit', 'bee_queue_wait', 'bee_run_natural', 'bee_run_ended')
HIVE_COUNTS = ('user_inputs', 'runs_started', 'runs_unended', 'tasks_created', 'failure_attempts')


def hive_bucket(values):
    values = sorted(values)
    n = len(values)
    bins = collections.Counter(-1001 if value == 0 else math.floor(math.log(value, 1.02)) for value in values)
    return {'n': n, 'sum_ms': sum(values), 'histogram': sorted(bins.items()),
            'mean_ms': sum(values) / n if n else None,
            'p50_ms': values[math.ceil(n * .5) - 1] if n else None,
            'p95_ms': values[math.ceil(n * .95) - 1] if n else None}


def hive_day():
    return {'counts': collections.Counter(), 'run_states': collections.Counter(),
            'task_states': collections.Counter(), 'failure_kinds': collections.Counter(),
            'samples': collections.defaultdict(list), 'invalid_timestamps': 0}


def read_hive_image(local, start_ms, end_ms):
    """Only a private, observed-stable copy may be opened with SQLite."""
    days = collections.defaultdict(hive_day)
    with contextlib.closing(sqlite3.connect(local.as_uri() + '?mode=ro', uri=True, timeout=1)) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + 15
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        con.execute('BEGIN')
        if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('invalid image')
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'dm_hive_inputs' not in tables:
            return {}, False

        def rows(sql):
            cursor = con.execute(sql, (start_ms, end_ms))
            for index, row in enumerate(cursor):
                if index >= HIVE_ROWS:
                    raise ValueError('row limit')
                yield row

        def target(stamp):
            if type(stamp) is not int or not start_ms <= stamp < end_ms:
                raise ValueError('invalid cohort timestamp')
            return days[dt.datetime.fromtimestamp(stamp / 1000, HIVE_TZ).date().isoformat()]

        def sample(day, metric, start, end):
            if end is None:
                return
            if type(end) is not int or end < start or end >= end_ms:
                day['invalid_timestamps'] += 1
                return
            day['samples'][metric].append(end - start)

        reply_columns = {r[1] for r in con.execute('PRAGMA table_info(dm_hive_reply_records)')}
        replies = {'normal_claimed_at_ms', 'quick_committed_at_ms', 'quick_state'} <= reply_columns
        sql = '''SELECT i.created_at_ms,i.workspace_id,i.principal_id,i.message_id,i.target_session_ref
                 FROM dm_hive_inputs i WHERE i.kind='user' AND i.role='USER'
                 AND i.created_at_ms>=? AND i.created_at_ms<?'''
        for row in rows(sql):
            day = target(row['created_at_ms'])
            day['counts']['user_inputs'] += 1
            if replies:
                reply = con.execute('''SELECT normal_claimed_at_ms,quick_committed_at_ms,quick_state
                    FROM dm_hive_reply_records WHERE workspace_id=? AND principal_id=?
                    AND message_id=? AND session_ref=? LIMIT 1''',
                    (row['workspace_id'], row['principal_id'], row['message_id'], row['target_session_ref'])).fetchone()
                if reply:
                    sample(day, 'normal_claim', row['created_at_ms'], reply[0])
                    if reply[2] == 'COMMITTED':
                        sample(day, 'quick_commit', row['created_at_ms'], reply[1])

        if {'dm_hive_runs', 'dm_hive_turn_queue'} <= tables:
            for row in rows('''SELECT q.created_at_ms,r.started_at_ms FROM dm_hive_turn_queue q
                LEFT JOIN dm_hive_runs r ON r.workspace_id=q.workspace_id AND r.principal_id=q.principal_id
                AND r.run_id=q.run_id AND r.queue_seq=q.seq
                WHERE q.created_at_ms>=? AND q.created_at_ms<?'''):
                sample(target(row[0]), 'bee_queue_wait', row[0], row[1])
        if 'dm_hive_runs' in tables:
            for row in rows('''SELECT started_at_ms,ended_at_ms,state,end_kind FROM dm_hive_runs
                WHERE started_at_ms>=? AND started_at_ms<?'''):
                day = target(row[0])
                day['counts']['runs_started'] += 1
                state = row[2] if row[2] in ('ENDED', 'RUNNING', 'RESERVED') else 'other'
                kind = row[3] if row[3] in ('natural', 'stopped') else 'other'
                day['run_states'][state + '/' + kind] += 1
                if row[1] is None:
                    day['counts']['runs_unended'] += 1
                elif state == 'ENDED':
                    sample(day, 'bee_run_ended', row[0], row[1])
                    if kind == 'natural':
                        sample(day, 'bee_run_natural', row[0], row[1])
        if 'dm_hive_tasks' in tables:
            for row in rows('SELECT created_at_ms,state FROM dm_hive_tasks WHERE created_at_ms>=? AND created_at_ms<?'):
                day = target(row[0])
                day['counts']['tasks_created'] += 1
                state = row[1] if row[1] in ('completed', 'failed', 'cancelled', 'running', 'queued', 'waiting_user') else 'other'
                day['task_states'][state] += 1
        if 'dm_hive_beebou_turn_failures' in tables:
            for row in rows('''SELECT created_at_ms,kind FROM dm_hive_beebou_turn_failures
                WHERE created_at_ms>=? AND created_at_ms<?'''):
                day = target(row[0])
                day['counts']['failure_attempts'] += 1
                kind = row[1] if row[1] in ('model', 'interrupted', 'tool', 'timeout') else 'other'
                day['failure_kinds'][kind] += 1
    return days, True


def collect_hive_daily(names, path_for, snapshot, *, now=None):
    if len(names) != len(set(names)) or any(not re.fullmatch(r'(?:digitalmate-)?w-[0-9a-f]{64}', n) for n in names):
        raise ValueError('invalid workspace allowlist')
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    today = now.astimezone(HIVE_TZ).date()
    start = today - dt.timedelta(days=HIVE_DAYS - 1)
    low = int(dt.datetime.combine(start, dt.time.min, HIVE_TZ).timestamp() * 1000)
    high = int(now.timestamp() * 1000) + 1
    days = collections.defaultdict(hive_day)
    coverage = collections.Counter(expected=len(names), read=0, with_hive=0, missing=0, incomplete=0)
    deadline = time.monotonic() + HIVE_SECONDS
    for index, name in enumerate(names):
        if time.monotonic() >= deadline:
            coverage['incomplete'] += len(names) - index
            break
        try:
            db = path_for(name, 'digitalmate/digitalmate.db')
            with snapshot(db) as local:
                local_days, present = read_hive_image(local, low, high)
            coverage['read'] += 1
            coverage['with_hive'] += present
            # Store scope IDs and sequence numbers can repeat in different
            # workspace DBs. Never deduplicate them across the allowlist.
            for date, row in local_days.items():
                day = days[date]
                for key in ('counts', 'run_states', 'task_states', 'failure_kinds'):
                    day[key].update(row[key])
                day['invalid_timestamps'] += row['invalid_timestamps']
                day['active_workspaces'] = day.get('active_workspaces', 0) + bool(row['counts']['user_inputs'])
                for key, samples in row['samples'].items():
                    day['samples'][key].extend(samples)
        except FileNotFoundError:
            coverage['missing'] += 1
        except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
            coverage['incomplete'] += 1
    partial = bool(coverage['incomplete'] or coverage['missing'])
    output = []
    for date, row in sorted(days.items()):
        output.append({'date': date, 'observed_at': now.isoformat(), 'partial': partial or date == today.isoformat(),
                       'retained': False, 'active_workspaces': row.get('active_workspaces', 0),
                       'counts': {key: row['counts'][key] for key in HIVE_COUNTS},
                       **{key: dict(row[key]) for key in ('run_states', 'task_states', 'failure_kinds')},
                       'invalid_timestamps': row['invalid_timestamps'],
                       'metrics': {key: hive_bucket(row['samples'][key]) for key in HIVE_METRICS}})
    return {'schema': HIVE_SCHEMA, 'observed_at': now.isoformat(), 'start': start.isoformat(),
            'today': today.isoformat(), 'consistency': 'observed_stable',
            'coverage': dict(coverage), 'days': output}


def retain_hive_daily(previous, current):
    """Retain missing days without combining overlapping populations."""
    rows = {row['date']: row for row in current['days']}
    if previous and previous.get('schema') == HIVE_SCHEMA:
        for old in previous.get('days', []):
            if not current['start'] <= old['date'] <= current['today']:
                continue
            fresh = rows.get(old['date'])
            incomplete = current['coverage'].get('incomplete', 0) or current['coverage'].get('missing', 0)
            # Prefer a complete fresh cohort; after a failed read keep the
            # entire old cohort instead of constructing a mixed distribution.
            shrank = fresh is not None and any(old.get('counts', {}).get(key, 0) > fresh.get('counts', {}).get(key, 0)
                                                for key in ('user_inputs', 'runs_started', 'tasks_created', 'failure_attempts'))
            if fresh is None or shrank or (incomplete and not old.get('partial')):
                rows[old['date']] = dict(old, retained=True)
    return dict(current, days=[rows[date] for date in sorted(rows)])
