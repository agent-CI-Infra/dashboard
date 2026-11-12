"""Bounded read-only workspace operations executed in the production node.

Only allowlisted workspaces and sanitized collector results are returned.
"""
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import zlib

ROOT = Path('/mnt/sfs/adx-jiuwen/workspaces')
NAME = re.compile(r'^(?:digitalmate-)?w-[0-9a-f]{64}$')
MAX_LOG = 200_000_000
MAX_NATIVE_SPANS = 300
MAX_NATIVE_PAYLOAD = 1_000_000
MAX_NATIVE_DATABASES = 64
SAFE_TOOL_NAMES = frozenset({'tool_search', 'tool_call', 'fetch_webpage', 'free_search'})
NATIVE_PHASES = {
    'workswarm.input.prepare': '输入准备',
    'workswarm.input.dispatch': '输入分发',
    'workswarm.session.checkpoint': '会话检查点',
    'workswarm.runner.wait_first_or_end': '等待首个运行事件或结束',
    'workswarm.runner.wait_stream_end': '等待运行流结束',
    'workswarm.session.flush_pending_history': '待写入历史刷新',
    'workswarm.stream.close': '关闭运行流',
    'llm.reasoning': '模型推理记录',
    'context.window.commit': '上下文提交',
}


def path_for(name, relative):
    if not NAME.fullmatch(name) or not isinstance(relative, str):
        raise ValueError('invalid workspace path')
    root = (ROOT / name).resolve(strict=True)
    if root.parent != ROOT.resolve(strict=True):
        raise ValueError('workspace escapes root')
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError('path escapes workspace')
    return path


def inventory():
    found = {}
    for entry in os.scandir(ROOT):
        if not NAME.fullmatch(entry.name) or not entry.is_dir(follow_symlinks=False):
            continue
        try:
            root = path_for(entry.name, 'digitalmate')
            if not root.is_dir():
                continue
            path = path_for(entry.name, 'workswarm/process.log')
            if not path.is_file() or path.stat().st_size > MAX_LOG:
                continue
            with path.open('rb') as handle:
                digest = hashlib.sha256(handle.read(4096)).hexdigest()
            found.setdefault(digest, []).append(entry.name)
        except (OSError, ValueError):
            continue
    return found


def native_span_name(blob):
    """Read only the OTLP name; never return attributes or payload content."""
    if not isinstance(blob, bytes) or len(blob) > MAX_NATIVE_PAYLOAD:
        return None
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(blob, MAX_NATIVE_PAYLOAD + 1)
        if len(raw) > MAX_NATIVE_PAYLOAD or not decoder.eof or decoder.unconsumed_tail:
            return None
        data = json.loads(raw)
        names = [s.get('name') for rs in data.get('resourceSpans', []) if isinstance(rs, dict)
                 for ss in rs.get('scopeSpans', []) if isinstance(ss, dict)
                 for s in ss.get('spans', []) if isinstance(s, dict)]
        return names[0] if len(names) == 1 and isinstance(names[0], str) else None
    except (ValueError, TypeError, AttributeError, zlib.error):
        return None


def read_native_connection(connection, correlation):
    """Extract content-free final descendants from one native SQLite connection."""
    try:
        session = correlation['session_id']
        trace = correlation['trace_id']
        root_id = correlation['span_id']
        request_id = correlation['request_id']
        if (not isinstance(session, str) or not session or len(session) > 512
                or not isinstance(request_id, str) or not re.fullmatch(r'digitalmate-[A-Za-z0-9_-]{1,128}', request_id)
                or not isinstance(trace, str) or not isinstance(root_id, str)
                or not re.fullmatch(r'[0-9a-f]{32}', trace)
                or not re.fullmatch(r'[0-9a-f]{16}', root_id)):
            return None
        records = connection.execute('''SELECT c.span_id,c.parent_span_id,c.request_id,c.lifecycle,
                c.start_time_unix_nano,c.end_time_unix_nano,c.has_error,
                CASE WHEN length(COALESCE(NULLIF(c.raw_json,X''),a.raw_json)) <= ?
                     THEN COALESCE(NULLIF(c.raw_json,X''),a.raw_json) ELSE NULL END
                FROM trajectory_current_records c LEFT JOIN otlp_span_records a
                ON a.trace_id=c.trace_id AND a.span_id=c.span_id WHERE c.trace_id=? LIMIT ?''',
                (MAX_NATIVE_PAYLOAD, trace, MAX_NATIVE_SPANS + 1)).fetchall()
        if len(records) > MAX_NATIVE_SPANS:
            return None
        by_id = {r[0]: r for r in records if isinstance(r[0], str) and re.fullmatch(r'[0-9a-f]{16}', r[0])}
        root = by_id.get(root_id)
        if not root or root[2] != request_id or root[3] != 'final':
            return None
        start, end = root[4], root[5]
        if not isinstance(start, int) or not isinstance(end, int) or end <= start:
            return None
        spans = []
        for record in records:
            sid, parent, _, lifecycle, begin, finish, error, raw = record
            if sid == root_id or sid not in by_id or lifecycle != 'final':
                continue
            if (not isinstance(begin, int) or not isinstance(finish, int)
                    or begin < start or finish < begin or finish > end):
                continue
            ancestor = parent
            seen = set()
            while ancestor and ancestor != root_id and ancestor in by_id and ancestor not in seen:
                seen.add(ancestor)
                ancestor = by_id[ancestor][1]
            if ancestor != root_id:
                continue
            name = native_span_name(raw)
            if name in NATIVE_PHASES:
                kind, label = 'phase', NATIVE_PHASES[name]
            elif isinstance(name, str) and name.startswith('agent.main_agent.task_iteration.'):
                kind, label = 'agent', '任务执行'
            elif isinstance(name, str) and name.startswith('agent.main_agent.react_iteration.'):
                match = re.fullmatch(r'agent\.main_agent\.react_iteration\.([0-9]{1,4})', name)
                kind, label = 'agent', f'ReAct 轮次 {match.group(1)}' if match else 'ReAct 轮次'
            elif isinstance(name, str) and name.startswith('chat '):
                kind, label = 'model', '模型调用'
            elif isinstance(name, str) and name.startswith('execute_tool '):
                tool = name.removeprefix('execute_tool ')
                kind, label = 'tool', f'工具调用 · {tool}' if tool in SAFE_TOOL_NAMES else '工具调用'
            else:
                kind, label = 'event', '其他阶段'
            spans.append({'span_id': sid, 'parent_span_id': parent, 'kind': kind, 'label': label,
                          'start_ms': round((begin - start) / 1_000_000, 3),
                          'duration_ms': round((finish - begin) / 1_000_000, 3), 'error': bool(error)})
        spans.sort(key=lambda row: (row['start_ms'], row['span_id']))
        if not any(row['kind'] in ('phase', 'agent', 'model', 'tool') for row in spans):
            return None
        return {'root_span_id': root_id, 'duration_ms': round((end-start)/1_000_000, 3), 'spans': spans}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, UnicodeError):
        return None


def read_native_trajectory(query, correlation):
    """Read a bounded, request-linked final native trace from a workspace."""
    try:
        session = correlation['session_id']
        if not isinstance(session, str) or not session or len(session) > 512:
            return None
        digest = hashlib.sha256(session.encode('utf8')).hexdigest()
        relative = f'workswarm/.jiuwenswarm/.trace/sessions/{digest[:2]}/{digest}.sqlite3'
        db = path_for(query['name'], relative)
        if not db.is_file():
            return None
        connection = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            connection.execute('PRAGMA query_only=ON')
            return read_native_connection(connection, correlation)
        finally:
            connection.close()
    except sqlite3.Error as error:
        # Do not expose paths or SQLite details in the public snapshot.
        raise OSError('native trajectory database unavailable') from error
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return None


def read_native_by_request_ids(query, request_ids):
    """Resolve only unique native roots across the complete, bounded workspace inventory.

    An unreadable or oversized inventory is inconclusive, never evidence of uniqueness.
    Nothing from a native payload other than allowlisted labels and timings is returned.
    """
    ids = set(request_ids)
    if not ids or len(ids) > 100 or any(not isinstance(x, str) or not re.fullmatch(r'digitalmate-[A-Za-z0-9_-]{1,128}', x) for x in ids):
        return {}
    try:
        root = path_for(query['name'], 'workswarm/.jiuwenswarm/.trace/sessions')
        databases = []
        with os.scandir(root) as directories:
            for directory in directories:
                if directory.is_symlink():
                    return {}
                if not directory.is_dir(follow_symlinks=False) or not re.fullmatch(r'[0-9a-f]{2}', directory.name):
                    continue
                with os.scandir(directory.path) as entries:
                    for entry in entries:
                        if not re.fullmatch(r'[0-9a-f]{64}\.sqlite3', entry.name):
                            if entry.name.endswith('.sqlite3'):
                                return {}
                            continue
                        if entry.is_symlink() or not entry.is_file(follow_symlinks=False) or not entry.name.startswith(directory.name):
                            return {}
                        databases.append(Path(entry.path))
                        if len(databases) > MAX_NATIVE_DATABASES:
                            return {}
        candidates = {rid: [] for rid in ids}
        placeholders = ','.join('?' for _ in ids)
        for db in databases:
            with contextlib.closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
                connection.execute('PRAGMA query_only=ON')
                roots = connection.execute(
                    f"SELECT request_id,trace_id,span_id,lifecycle FROM trajectory_current_records WHERE request_id IN ({placeholders}) "
                    "AND (parent_span_id IS NULL OR parent_span_id='') LIMIT ?",
                    (*ids, 2 * len(ids) + 1),
                ).fetchall()
                if len(roots) > 2 * len(ids):
                    return {}
                for rid, trace, span, lifecycle in roots:
                    candidates[rid].append((db, trace, span, lifecycle))
        result = {}
        for rid, roots in candidates.items():
            if len(roots) != 1 or roots[0][3] != 'final':
                continue
            db, trace, span, _ = roots[0]
            with contextlib.closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
                connection.execute('PRAGMA query_only=ON')
                native = read_native_connection(connection, {'request_id': rid, 'trace_id': trace,
                                                               'span_id': span, 'session_id': 'request-id-only'})
            if native:
                result[rid] = native
        return result
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return {}


def run_collector(names, source):
    if len(names) != len(set(names)) or any(not NAME.fullmatch(n) for n in names):
        raise ValueError('invalid workspace allowlist')
    namespace = {'__name__': 'kube_traces', '__file__': 'collect-production-workspace-traces.py'}
    exec(compile(source, namespace['__file__'], 'exec'), namespace)
    namespace['TOKEN'] = 'not-used'
    namespace['binding_list'] = lambda: [{'name': name} for name in names]
    namespace['query_for'] = lambda item: {'name': item['name']}

    def listing(query, relative):
        root = path_for(query['name'], relative)
        result = []
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                stat = entry.stat(follow_symlinks=False)
                result.append({'name': entry.name, 'type': 'directory' if entry.is_dir(follow_symlinks=False) else 'file',
                               'size': stat.st_size,
                               'modified_at': dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat()})
        return result

    def download_log(query):
        path = path_for(query['name'], 'workswarm/process.log')
        size = path.stat().st_size
        if size > MAX_LOG:
            raise ValueError('log exceeds bound')
        with path.open('rb') as handle:
            remaining = size
            while remaining:
                line = handle.readline(remaining)
                if not line:
                    raise ValueError('incomplete log')
                remaining -= len(line)
                yield line.decode('utf-8', 'replace')

    def fetch(_endpoint, params, *, download=False):
        if not download or not params.get('path', '').endswith('/history.jsonl'):
            raise ValueError('unexpected workspace operation')
        path = path_for(params['name'], params['path'])
        limit = namespace['MAX_HISTORY']
        with path.open('rb') as handle:
            body = handle.read(limit + 1)
        if len(body) > limit:
            raise ValueError('history exceeds bound')
        return body.decode('utf-8', 'replace')

    namespace.update(listing=listing, download_log=download_log, fetch=fetch,
                     read_native_trajectory=read_native_trajectory,
                     read_native_by_request_ids=read_native_by_request_ids)
    with contextlib.redirect_stdout(sys.stderr):
        return namespace['collect']()


def main():
    command = json.load(sys.stdin)
    if command.get('mode') == 'inventory':
        output = inventory()
    elif command.get('mode') == 'collect':
        output = run_collector(command['names'], command['source'])
    else:
        raise ValueError('invalid operation')
    json.dump(output, sys.stdout, ensure_ascii=False)


if __name__ == '__main__':
    main()
