"""Existing Hive profile events projected into content-free reply timings.

These are Beebou response-ready / callback-return clocks, not legacy stream
TTFT, Bee execution completion, or a device display acknowledgement.
"""
import collections
import datetime as dt
import gzip
import hashlib
import hmac
import json
import math
from pathlib import Path
import re

REPLY_SCHEMA = 'digitalmate/cloud-reply-daily@1'
REPLY_STATE_SCHEMA = 'digitalmate/cloud-reply-state@1'
REPLY_TZ = dt.timezone(dt.timedelta(hours=8))
REPLY_DAYS = 30
REPLY_METRICS = ('normal_response', 'quick_send', 'normal_push')
REPLY_FILE_BYTES = 32 * 1024 * 1024
REPLY_READ_BYTES = 128 * 1024 * 1024
REPLY_NODE_BYTES = 512 * 1024 * 1024
REPLY_MAX_EVENTS = 100_000
REPLY_MAX_MS = 24 * 60 * 60 * 1000


def read_node_replies(environments, *, directory='/work/runtime-logs'):
    """Read only current production environments, including their rN resumes."""
    if not environments or any(not re.fullmatch(r'adx-[a-zA-Z0-9-]{1,128}', e) for e in environments):
        raise ValueError('invalid production environment allowlist')
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('missing runtime log directory')
    events, matched = [], set()
    coverage = {'files': 0, 'bytes': 0, 'read_bytes': 0}
    for path in sorted(root.iterdir()):
        if not re.fullmatch(r'adx-[a-zA-Z0-9-]+\.(?:out|err)(?:\.gz)?', path.name):
            continue
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root.resolve():
            continue
        stem = path.name.split('.')[0]
        candidates = [e for e in environments if re.fullmatch(re.escape(e) + r'(?:-[0-9a-f]{11})?(?:-r[0-9]+)?', stem)]
        if len(candidates) != 1:
            continue
        environment = candidates[0]
        matched.add(environment)
        size = path.stat().st_size
        if size > REPLY_FILE_BYTES:
            raise ValueError('runtime log exceeds byte limit')
        coverage['files'] += 1
        coverage['bytes'] += size
        read = 0
        with (gzip.open(path, 'rt', encoding='utf-8', errors='replace') if path.suffix == '.gz'
              else path.open('r', encoding='utf-8', errors='replace')) as handle:
            for line in iter(lambda: handle.readline(65537), ''):
                read += len(line.encode('utf-8'))
                coverage['read_bytes'] += len(line.encode('utf-8'))
                if read > REPLY_READ_BYTES or coverage['read_bytes'] > REPLY_NODE_BYTES:
                    raise ValueError('runtime log decompression exceeds bound')
                if len(line) > 65536 or '"event_type"' not in line:
                    continue
                try:
                    row = json.loads(line[line.index('{'):])
                except (ValueError, TypeError):
                    continue
                if not isinstance(row, dict) or row.get('event_type') not in (
                        'digitalmate_hive_message_received', 'digitalmate_hive_reply_visible'):
                    continue
                mid, stamp = row.get('message_id'), row.get('at_ms')
                if not isinstance(mid, str) or not 0 < len(mid) <= 256 or type(stamp) is not int:
                    continue
                clean = {key: row[key] for key in ('event_type', 'message_id', 'at_ms', 'phase', 'via', 'text_chars') if key in row}
                clean['scope'] = environments[environment]
                events.append(clean)
                if len(events) > REPLY_MAX_EVENTS:
                    raise ValueError('runtime event count exceeds bound')
    return {'events': events, 'coverage': coverage, 'matched': sorted(matched)}


def reply_bucket(values):
    values = sorted(values)
    n = len(values)
    bins = collections.Counter(-1001 if v == 0 else math.floor(math.log(v, 1.02)) for v in values)
    return {'n': n, 'sum_ms': sum(values), 'mean_ms': sum(values) / n if n else None,
            'p50_ms': values[math.ceil(n * .5) - 1] if n else None,
            'p95_ms': values[math.ceil(n * .95) - 1] if n else None,
            'histogram': sorted(bins.items())}


def merge_reply_events(previous, events, salt, *, now=None, coverage=None):
    """Keep HMAC identities across log rotation; raw identities never leave SSH."""
    if not isinstance(salt, str) or not salt:
        raise ValueError('reply identity salt required')
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    today = now.astimezone(REPLY_TZ).date()
    low = int(dt.datetime.combine(today - dt.timedelta(days=REPLY_DAYS - 1), dt.time.min, REPLY_TZ).timestamp() * 1000)
    high = int(now.timestamp() * 1000) + 1
    epoch = hmac.new(salt.encode(), b'cloud-reply-state-v1', hashlib.sha256).hexdigest()
    if previous and (previous.get('schema') != REPLY_STATE_SCHEMA or previous.get('epoch') != epoch):
        raise ValueError('reply cursor identity changed; explicit migration required')
    records = {}
    for key, row in (previous or {}).get('records', {}).items():
        if not re.fullmatch(r'[0-9a-f]{64}', key) or not isinstance(row, dict):
            raise ValueError('invalid reply cursor')
        stamps = [row.get('start'), *row.get('ends', {}).values()]
        if any(type(v) is int and low <= v < high for v in stamps):
            records[key] = {'start': row.get('start'), 'ends': dict(row.get('ends', {})),
                            'ambiguous': bool(row.get('ambiguous', False))}
    for event in events:
        stamp = event.get('at_ms')
        if type(stamp) is not int or not low <= stamp < high:
            continue
        scope, mid = event.get('scope'), event.get('message_id')
        if not isinstance(scope, str) or not isinstance(mid, str) or not 0 < len(mid) <= 256:
            continue
        key = hmac.new(salt.encode(), json.dumps([scope, mid], separators=(',', ':')).encode(), hashlib.sha256).hexdigest()
        row = records.setdefault(key, {'start': None, 'ends': {}, 'ambiguous': False})
        if event.get('event_type') == 'digitalmate_hive_message_received':
            if row['start'] is not None and row['start'] != stamp:
                row['ambiguous'] = True  # A resend cannot create an artificially fast reply.
            row['start'] = stamp if row['start'] is None else min(row['start'], stamp)
        elif event.get('event_type') == 'digitalmate_hive_reply_visible':
            if type(event.get('text_chars')) is not int or event['text_chars'] <= 0:
                continue
            phase, via = event.get('phase'), event.get('via')
            metric = ('quick_send' if phase == 'quick' and via is None else
                      'normal_response' if phase == 'normal' and via == 'response' else
                      'normal_push' if phase == 'normal' and via == 'push' else None)
            if metric:
                row['ends'][metric] = min(row['ends'].get(metric, stamp), stamp)
    days = collections.defaultdict(lambda: {'samples': collections.defaultdict(list), 'scopes': set(), 'received': 0})
    rejected = collections.Counter()
    for key, row in records.items():
        start = row['start']
        if type(start) is not int:
            rejected['missing_receive'] += 1
            continue
        if not low <= start < high:
            continue
        if row['ambiguous']:
            rejected['ambiguous_receive'] += 1
            continue
        date = dt.datetime.fromtimestamp(start / 1000, REPLY_TZ).date().isoformat()
        day = days[date]
        day['received'] += 1
        for metric, end in row['ends'].items():
            if metric not in REPLY_METRICS or type(end) is not int or not 0 <= end - start <= REPLY_MAX_MS:
                rejected['invalid_clock'] += 1
                continue
            day['samples'][metric].append(end - start)
    rows = [{'date': date, 'partial': date == today.isoformat(), 'received': day['received'],
             **{metric: reply_bucket(day['samples'][metric]) for metric in REPLY_METRICS}}
            for date, day in sorted(days.items())]
    doc = {'schema': REPLY_SCHEMA, 'timezone': 'Asia/Shanghai', 'observed_at': now.isoformat(),
           'source': 'production_runtime_profile_events', 'coverage': coverage or {},
           'rejected': dict(rejected), 'days': rows}
    state = {'schema': REPLY_STATE_SCHEMA, 'epoch': epoch, 'records': records}
    return {'daily': doc, 'state': state}
