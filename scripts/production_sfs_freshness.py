"""Decide whether a scheduled SFS snapshot needs another read."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import sys

MINIMUM_INTERVAL = dt.timedelta(minutes=50)


def collection_due(snapshot: object, *, now: dt.datetime | None = None) -> bool:
    """Fail open for missing, malformed, future or non-SFS observations."""
    collection = snapshot.get('cloud_trace_collection') if isinstance(snapshot, dict) else None
    if not isinstance(collection, dict) or collection.get('collection_source') != 'production_sfs_ssh':
        return True
    count, read = collection.get('binding_count'), collection.get('read_log_count')
    if type(count) is not int or type(read) is not int or count <= 0 or not 0 <= read <= count or read / count < 0.95:
        return True
    stamp = collection.get('generated_at')
    if not isinstance(stamp, str):
        return True
    try:
        observed = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError:
        return True
    if observed.tzinfo is None or observed.utcoffset() is None:
        return True
    current = now or dt.datetime.now(dt.timezone.utc)
    age = current - observed
    return age < dt.timedelta(0) or age >= MINIMUM_INTERVAL


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('usage: production_sfs_freshness.py SNAPSHOT')
    try:
        snapshot = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        snapshot = None
    print('true' if collection_due(snapshot) else 'false')


if __name__ == '__main__':
    main()
