"""Incremental content-free archive, independent of the slow-request sample."""
import argparse
import collections
import datetime as dt
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('sfs_archive_transport', HERE / 'collect-production-sfs-traces.py')
sfs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sfs)
SCHEMA = 'digitalmate/trajectory-archive@1'
DETAIL_PAGE = 500


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def load_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def publish_database(output, rows, observed_at, entries):
    traces = collections.defaultdict(list)
    for row in rows:
        traces[row['trace_key']].append(row)
    for key, spans in traces.items():
        # Retention/rotation may remove rows from a later source image. Keep
        # earlier observations and label them, rather than shrinking history.
        retained = 0
        old = entries.get(key)
        if old:
            current_ids = {r['span_id'] for r in spans}
            for path in old['pages']:
                for row in load_json(output / path, {'spans': []})['spans']:
                    if row['span_id'] in current_ids:
                        continue
                    absolute = round(old['origin_ms'] + row['start_ms'], 3)
                    spans.append(dict(row, trace_key=key, request_id=old.get('request_id'),
                                      start_ms=absolute, end_ms=round(absolute + row['duration_ms'], 3)))
                    retained += 1
        for row in spans:
            row.setdefault('observed_at', observed_at)
        spans.sort(key=lambda r: (r['start_ms'], r['span_id']))
        roots = [r for r in spans if not r['parent_span_id']]
        root = roots[0] if len(roots) == 1 else None
        start = root['start_ms'] if root else min(r['start_ms'] for r in spans)
        end = root['end_ms'] if root else max(r['end_ms'] for r in spans)
        lifecycle = root['lifecycle'] if root else 'root_unresolved'
        # Root finalization alone does not prove that all descendants arrived.
        if lifecycle == 'final' and any(r['lifecycle'] != 'final' for r in spans):
            lifecycle = 'pending_descendants'
        rid = root['request_id'] if root else None
        entry = {'key': key, 'request_id': rid, 'start_time': dt.datetime.fromtimestamp(start / 1000, dt.timezone.utc).isoformat(),
                 'request_ids': sorted({r['request_id'] for r in spans if r['request_id']}),
                 'duration_ms': max(0, round(end - start, 3)), 'lifecycle': lifecycle,
                 'origin_ms': start, 'retained_spans': retained,
                 'span_count': len(spans), 'observed_at': observed_at,
                 'consistency': 'observed_stable', 'pages': []}
        for offset in range(0, len(spans), DETAIL_PAGE):
            path = f'traces/{key}/{offset // DETAIL_PAGE}.json'
            projected = [dict(r, start_ms=round(r['start_ms'] - start, 3),
                              duration_ms=max(0, round(r['end_ms'] - r['start_ms'], 3)))
                         for r in spans[offset:offset + DETAIL_PAGE]]
            for row in projected:
                for field in ('trace_key', 'request_id', 'end_ms'):
                    row.pop(field, None)
            write_json(output / path, {'schema': SCHEMA, 'key': key, 'offset': offset,
                                      'total': len(spans), 'spans': projected})
            entry['pages'].append(path)
        entries[key] = entry


def merge_batch(state, previous_index, batch, output, scope, now):
    if state.get('schema', SCHEMA) != SCHEMA:
        raise ValueError('invalid archive state')
    state.setdefault('schema', SCHEMA)
    databases = state.setdefault('databases', {})
    entries = {row['key']: row for row in previous_index.get('traces', [])}
    seen = set(batch['inventory'])
    for key, old in databases.items():
        if key not in seen:
            old['status'] = 'not_seen'
    for key in seen:
        databases.setdefault(key, {'status': 'queued'})
    # A previous success is not evidence that an unvisited DB is current.
    for key in seen - batch['updates'].keys():
        databases[key]['status'] = 'queued'
    for key, update in batch['updates'].items():
        old = databases[key]
        old['status'] = update['status']
        old['checked_at'] = now
        if update['status'] != 'observed_stable' or update.get('unchanged'):
            continue
        if old.get('version') != update['version']:
            # Never combine pages from different byte images.
            old.update(version=update['version'], cursor=0, invalid=0, done=False)
            staged = []
        else:
            staged = load_json(output / 'staging' / (key + '.json'), [])
        staged.extend(update['rows'])
        old['invalid'] += update['invalid']
        old['cursor'] = update['cursor']
        old['done'] = update['done']
        if old['done']:
            publish_database(output, staged, now, entries)
            (output / 'staging' / (key + '.json')).unlink(missing_ok=True)
        else:
            write_json(output / 'staging' / (key + '.json'), staged)
    state['last_database'] = batch['last_database']
    statuses = collections.Counter(databases[k]['status'] for k in seen)
    completed = sum(databases[k].get('done', False) and databases[k]['status'] == 'observed_stable' for k in seen)
    pending_pages = sum(not databases[k].get('done', False) and databases[k]['status'] == 'observed_stable' for k in seen)
    coverage = {'bindings': scope['bindings'], 'authorized_workspaces': scope['authorized_workspaces'],
                'scope_errors': scope['errors'], 'discovered_databases': len(seen),
                'inventory_errors': batch['inventory_errors'], 'observed_databases': completed,
                'pending_databases': len(seen) - completed, 'pending_pages': pending_pages,
                'not_seen_databases': sum(v['status'] == 'not_seen' for v in databases.values()),
                'invalid_records': sum(v.get('invalid', 0) for v in databases.values()),
                'statuses': dict(statuses), 'archived_traces': len(entries),
                'archived_spans': sum(e['span_count'] for e in entries.values()),
                'consistency': 'unconfirmed', 'complete': False}
    # Full index stays separate from metrics; UI requests details lazily.
    ordered = sorted(entries.values(), key=lambda r: (r['start_time'], r['key']), reverse=True)
    index = {'schema': SCHEMA, 'generated_at': now, 'coverage': coverage, 'traces': ordered}
    write_json(output / 'index.json', index)
    return state, index


def archive_native_profile(output, entry):
    """Translate a complete archived tree onto the existing native root clock."""
    key = entry['key']
    if not re.fullmatch('[0-9a-f]{64}', key) or entry['lifecycle'] != 'final':
        raise ValueError('invalid archive identity')
    rows = []
    for page_number, relative in enumerate(entry['pages']):
        if relative != f'traces/{key}/{page_number}.json':
            raise ValueError('invalid archive path')
        path = (output / relative).resolve(strict=True)
        if not path.is_relative_to(output.resolve(strict=True)):
            raise ValueError('archive path escapes root')
        page = load_json(path, {})
        if (page.get('schema') != SCHEMA or page.get('key') != key
                or page.get('offset') != len(rows) or page.get('total') != entry['span_count']):
            raise ValueError('incomplete archive page')
        rows.extend(page['spans'])
    if not rows or len(rows) != entry['span_count']:
        raise ValueError('incomplete archive')
    total = entry['duration_ms']
    roots = [row for row in rows if not row.get('parent_span_id')]
    if (len(roots) != 1 or roots[0]['start_ms'] != 0
            or abs(roots[0]['duration_ms'] - total) > .002):
        raise ValueError('ambiguous archive root')
    root_id = roots[0]['span_id']
    by_id = {row['span_id']: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError('duplicate archive spans')
    spans = []
    for row in rows:
        if (not re.fullmatch('[0-9a-f]{16}', row['span_id']) or row['lifecycle'] != 'final'
                or row['kind'] not in ('phase', 'agent', 'model', 'tool', 'event')
                or not isinstance(row['label'], str)
                or any(type(row[f]) not in (int, float) or not math.isfinite(row[f]) or row[f] < 0
                       for f in ('start_ms', 'duration_ms'))
                or row['start_ms'] + row['duration_ms'] > total + .002):
            raise ValueError('invalid archive span')
        if row['span_id'] == root_id:
            continue
        ancestor, seen = row['parent_span_id'], {row['span_id']}
        while ancestor != root_id:
            if ancestor not in by_id or ancestor in seen:
                raise ValueError('incomplete archive ancestry')
            seen.add(ancestor)
            ancestor = by_id[ancestor]['parent_span_id']
        spans.append({field: row[field] for field in ('span_id', 'parent_span_id', 'kind', 'label',
                                                     'start_ms', 'duration_ms', 'error')})
    return {'root_span_id': root_id, 'duration_ms': total, 'spans': spans}


def attach_archived_profiles(metrics, index, output):
    """Fill slow-request profiles by unique request ID AND contained time window.

    Gateway duration/tool events remain untouched. Ambiguous links, missing
    pages and unfinished trees must never replace the existing event fallback.
    """
    collection = metrics.get('cloud_trace_collection') or {}
    if collection.get('schema') != 'digitalmate/cloud-traces@1' or index.get('schema') != SCHEMA:
        raise ValueError('invalid collection schema')
    rows = [row for day in collection.get('days', [])
            for row in ((day.get('anomaly') or {}).get('latency') or {}).get('slow_requests', [])]
    counts = collections.Counter(row.get('request_id') for row in rows)
    by_request = collections.defaultdict(list)
    for entry in index['traces']:
        for rid in set(entry.get('request_ids', [])) | {entry.get('request_id')}:
            if rid:
                by_request[rid].append(entry)
    linked, errors = 0, 0
    for row in rows:
        rid = row.get('request_id')
        # Revalidate previously linked profiles against the current archive.
        # A newly discovered duplicate must not leave a stale unique link.
        if row.get('native_trace_source') == 'sfs_archive':
            for field in ('native_trajectory', 'native_trace_source', 'native_trace_consistency',
                          'native_trace_observed_at', 'native_trace_retained_span_count',
                          'native_trace_retained_from'):
                row.pop(field, None)
            row.setdefault('native_trace_state', 'unavailable')
        if (counts[rid] != 1 or row.get('native_trace_state') == 'ambiguous'
                or (row.get('native_trajectory') and row.get('native_trace_source') != 'sfs_archive')):
            continue
        candidates = by_request[rid]
        if len(candidates) > 1:
            row['native_trace_state'] = 'ambiguous'
        if len(candidates) != 1 or candidates[0].get('request_id') != rid:
            continue
        entry = candidates[0]
        if entry.get('lifecycle') != 'final':
            continue
        try:
            start = dt.datetime.fromisoformat(row['start_time'])
            native_start = dt.datetime.fromisoformat(entry['start_time'])
            offset = (native_start - start).total_seconds() * 1000
            total, gateway = entry['duration_ms'], row['gateway']
            if (not start.tzinfo or not native_start.tzinfo or not math.isfinite(offset)
                    or type(total) not in (int, float) or not math.isfinite(total) or total <= 0
                    or type(gateway) not in (int, float) or not math.isfinite(gateway)
                    or offset < -.002 or offset + total > gateway + .002):
                continue
            native = archive_native_profile(output, entry)
        except (OSError, ValueError, TypeError, KeyError):
            errors += 1
            continue
        row.update(native_trajectory=native, native_trace_source='sfs_archive',
                   native_trace_consistency='unconfirmed', native_trace_observed_at=entry['observed_at'],
                   native_trace_retained_span_count=entry.get('retained_spans', 0))
        row.pop('native_trace_state', None)
        row.pop('native_trace_retained_from', None)
        linked += 1
    collection['archived_native_trace_count'] = linked
    collection['archive_link_errors'] = errors
    collection['native_trace_count'] = sum(isinstance(row.get('native_trajectory'), dict) for row in rows)
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--next-state', type=Path, required=True)
    parser.add_argument('--metrics', type=Path, help='Attach archived native profiles to the slow-request snapshot')
    args = parser.parse_args()
    state = load_json(args.state, {'schema': SCHEMA, 'databases': {}})
    previous = load_json(args.previous / 'index.json', {'schema': SCHEMA, 'traces': []})
    if previous.get('schema') != SCHEMA:
        raise ValueError('invalid previous archive')
    if args.previous.exists():
        shutil.copytree(args.previous, args.output, dirs_exist_ok=True)
    sfs.collector.kube_operation = sfs.ssh_operation
    names, count, errors = sfs.collector.select_workspaces()
    remote = sfs.collector.REMOTE.rsplit("\nif __name__", 1)[0] + '\n' + (HERE / 'sfs-trajectory-remote.py').read_text(encoding='utf-8')
    cursors = {key: {field: value[field] for field in ('version', 'cursor', 'done') if field in value}
               for key, value in state.get('databases', {}).items()}
    batch = sfs.ssh_operation({'names': names, 'states': cursors, 'last_database': state.get('last_database', '')}, remote_source=remote)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    state, index = merge_batch(state, previous, batch, args.output,
                               {'bindings': count, 'authorized_workspaces': len(names), 'errors': dict(errors)}, now)
    write_json(args.next_state, state)
    if args.metrics:
        metrics = attach_archived_profiles(load_json(args.metrics, {}), index, args.output)
        write_json(args.metrics, metrics)
        print(json.dumps({'archived_profiles_linked': metrics['cloud_trace_collection']['archived_native_trace_count'],
                          'archive_link_errors': metrics['cloud_trace_collection']['archive_link_errors']}))
    print(json.dumps(index['coverage'], ensure_ascii=False))


if __name__ == '__main__':
    main()
