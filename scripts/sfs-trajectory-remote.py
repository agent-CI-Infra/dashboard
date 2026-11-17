"""Appended to kube-workspace-remote definitions; no SQLite opens on SFS.

An observed-stable copy is NOT an atomic snapshot. This distinction is part
of the wire protocol, archive and UI. All output is content-free projection.
"""
import shutil
import stat
import tempfile
import time

ARCHIVE_PAGE = 16000
ARCHIVE_RUN_ROWS = 100000
ARCHIVE_DB_BYTES = 256 * 1024 * 1024
ARCHIVE_RUN_BYTES = 1024 * 1024 * 1024
ARCHIVE_SECONDS = 480


def archive_inventory(names):
    if len(names) != len(set(names)) or any(not NAME.fullmatch(n) for n in names):
        raise ValueError('invalid allowlist')
    found, failures = {}, 0
    for name in names:
        try:
            root = path_for(name, 'workswarm/.jiuwenswarm/.trace/sessions')
            for directory in root.iterdir():
                if directory.is_symlink():
                    failures += 1
                    continue
                if not directory.is_dir() or not re.fullmatch('[0-9a-f]{2}', directory.name):
                    continue
                for entry in directory.iterdir():
                    if not entry.name.endswith('.sqlite3'):
                        continue
                    if (entry.is_symlink() or not entry.is_file()
                            or not re.fullmatch('[0-9a-f]{64}\\.sqlite3', entry.name)
                            or not entry.name.startswith(directory.name)):
                        failures += 1
                        continue
                    resolved = path_for(name, str(entry.relative_to((ROOT / name).resolve())))
                    key = hashlib.sha256((name + '/' + entry.name).encode()).hexdigest()
                    found[key] = resolved
        except FileNotFoundError:
            # A workspace without a native store is not a read failure.
            continue
        except (OSError, ValueError):
            failures += 1
    return found, failures


def archive_stamp(path):
    try:
        s = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(s.st_mode):
            raise ValueError('unsafe source file')
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    except FileNotFoundError:
        return None


def archive_signature(db):
    files = [db] + [Path(str(db) + suffix) for suffix in ('-wal', '-shm', '-journal')]
    return [archive_stamp(p) for p in files]


def archive_file_hash(path, size, target=None):
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as source:
        remaining = size
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                return None
            remaining -= len(block)
            digest.update(block)
            if target is not None:
                target.write(block)
        if source.read(1):
            return None
    return digest.digest()


def copy_archive_image(db, local, before):
    files = [(db, local, before[0][2])]
    if before[1] is not None:
        files.append((Path(str(db) + '-wal'), Path(str(local) + '-wal'), before[1][2]))
    # SQLite rebuilds the WAL index in the private directory. SHM never contains
    # durable records. Read the main file and WAL twice to detect concurrent changes.
    hashes = []
    for path, copy, size in files:
        with copy.open('xb') as target:
            hashes.append(archive_file_hash(path, size, target))
    if any(digest is None or archive_file_hash(path, size) != digest
           for (path, _, size), digest in zip(files, hashes, strict=True)):
        return None
    if archive_signature(db) != before:
        return None
    if len(files) == 1:
        return hashes[0].hex()
    _, wal, wal_size = files[1]
    if wal_size:
        with wal.open('rb') as handle:
            if wal_size < 32 or handle.read(4) not in (b'\x37\x7f\x06\x82', b'\x37\x7f\x06\x83'):
                raise ValueError('invalid WAL header')
    return hashlib.sha256(b'wal-pair\0' + b''.join(hashes)).hexdigest()


def archive_label(blob):
    name = native_span_name(blob)
    if name in NATIVE_PHASES:
        return 'phase', NATIVE_PHASES[name]
    if isinstance(name, str):
        if name.startswith('agent.main_agent.task_iteration.'):
            return 'agent', '任务执行'
        if name.startswith('agent.main_agent.react_iteration.'):
            return 'agent', 'ReAct 轮次'
        if name.startswith('chat '):
            return 'model', '模型调用'
        if name.startswith('execute_tool '):
            tool = name.removeprefix('execute_tool ')
            return 'tool', ('工具调用 · ' + tool) if tool in SAFE_TOOL_NAMES else '工具调用'
    return 'event', '其他阶段'


def archive_page(local, key, after, limit):
    rows, invalid, cursor = [], 0, after
    with contextlib.closing(sqlite3.connect(local.as_uri() + '?mode=ro', uri=True, timeout=1)) as con:
        con.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + 20
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('invalid copy')
        query = con.execute('''SELECT c.rowid,c.trace_id,c.span_id,c.parent_span_id,c.request_id,c.lifecycle,
            c.start_time_unix_nano,c.end_time_unix_nano,c.has_error,
            CASE WHEN length(COALESCE(NULLIF(c.raw_json,X''),a.raw_json))<=?
                 THEN COALESCE(NULLIF(c.raw_json,X''),a.raw_json) ELSE NULL END
            FROM trajectory_current_records c LEFT JOIN otlp_span_records a
            ON a.trace_id=c.trace_id AND a.span_id=c.span_id
            WHERE c.rowid>? ORDER BY c.rowid LIMIT ?''',
            (MAX_NATIVE_PAYLOAD, after, limit + 1))
        more = False
        for i, record in enumerate(query):
            if i == limit:
                more = True
                break
            rowid, trace, span, parent, rid, lifecycle, start, end, error, raw = record
            cursor = rowid
            if (not isinstance(trace, str) or not re.fullmatch('[0-9a-f]{32}', trace)
                    or not isinstance(span, str) or not re.fullmatch('[0-9a-f]{16}', span)
                    or (parent and (not isinstance(parent, str) or not re.fullmatch('[0-9a-f]{16}', parent)))
                    or lifecycle not in ('running', 'final', 'abandoned')
                    or type(start) is not int or type(end) is not int or start < 0 or end < 0
                    or max(start, end) > 253402300799000000000):
                invalid += 1
                continue
            kind, label = archive_label(raw)
            rows.append({'trace_key': hashlib.sha256((key + trace).encode()).hexdigest(),
                         'span_id': span, 'parent_span_id': parent or None,
                         'request_id': rid if isinstance(rid, str) and re.fullmatch(r'digitalmate-[A-Za-z0-9_-]{1,128}', rid) else None,
                         'lifecycle': lifecycle, 'start_ms': round(start / 1e6, 3),
                         'end_ms': round(end / 1e6, 3), 'error': bool(error), 'kind': kind, 'label': label})
    return {'rows': rows, 'cursor': cursor, 'done': not more, 'invalid': invalid}


def archive_collect(command):
    inventory, failures = archive_inventory(command['names'])
    states = command.get('states', {})
    last = command.get('last_database', '')
    ordered = sorted(inventory)
    ordered = [k for k in ordered if k > last] + [k for k in ordered if k <= last]
    updates, consumed, rows_read = {}, 0, 0
    started = time.monotonic()
    for key in ordered:
        if time.monotonic() - started > ARCHIVE_SECONDS or consumed >= ARCHIVE_RUN_BYTES or rows_read >= ARCHIVE_RUN_ROWS:
            break
        db = inventory[key]
        try:
            before = archive_signature(db)
            size = sum(s[2] for s in before[:2] if s is not None)
            if consumed and size <= ARCHIVE_DB_BYTES and consumed + size > ARCHIVE_RUN_BYTES:
                break
            last = key
            if not before[0]:
                updates[key] = {'status': 'missing'}
                continue
            if before[3]:
                updates[key] = {'status': 'rollback_journal'}
                continue
            if size > ARCHIVE_DB_BYTES:
                updates[key] = {'status': 'size_limit'}
                continue
            consumed += size
            with tempfile.TemporaryDirectory(prefix='sfs-archive-') as folder:
                local = Path(folder) / 'copy.sqlite3'
                if local.resolve().is_relative_to(Path('/mnt/sfs')):
                    raise ValueError('temporary directory cannot be on SFS')
                version = copy_archive_image(db, local, before)
                if version is None:
                    updates[key] = {'status': 'changing'}
                    continue
                old = states.get(key, {})
                if old.get('version') == version and old.get('done'):
                    updates[key] = {'status': 'observed_stable', 'unchanged': True}
                    continue
                after = old.get('cursor', 0) if old.get('version') == version else 0
                page = archive_page(local, key, after, min(ARCHIVE_PAGE, ARCHIVE_RUN_ROWS - rows_read))
                rows_read += len(page['rows']) + page['invalid']
                updates[key] = dict(page, version=version, status='observed_stable')
        except (sqlite3.Error, OSError, ValueError, TypeError):
            # Never publish paths, session ids, SQL errors or raw content.
            updates[key] = {'status': 'read_error'}
            last = key
    return {'inventory': sorted(inventory), 'inventory_errors': failures,
            'updates': updates, 'last_database': last}


if __name__ == '__main__':
    json.dump(archive_collect(json.load(sys.stdin)), sys.stdout, ensure_ascii=False)
