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
import sys

ROOT = Path('/mnt/sfs/adx-jiuwen/workspaces')
NAME = re.compile(r'^(?:digitalmate-)?w-[0-9a-f]{64}$')
MAX_LOG = 200_000_000


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

    namespace.update(listing=listing, download_log=download_log, fetch=fetch)
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
