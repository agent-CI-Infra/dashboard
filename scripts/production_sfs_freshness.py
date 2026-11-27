"""Decide whether a scheduled SFS snapshot needs another read."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import sys

def collection_due(snapshot: object, *, now: dt.datetime | None = None) -> bool:
    """Collect until a usable archive has been published in the current hour."""
    if not isinstance(snapshot, dict) or snapshot.get('schema') != 'digitalmate/trajectory-archive@1':
        return True
    coverage = snapshot.get('coverage')
    if not isinstance(coverage, dict):
        return True
    bindings = coverage.get('bindings')
    authorized = coverage.get('authorized_workspaces')
    discovered = coverage.get('discovered_databases')
    observed_count = coverage.get('observed_databases')
    if (
        not all(type(n) is int for n in (bindings, authorized, discovered, observed_count))
        or not 0 < authorized <= bindings
        or authorized / bindings < 0.95
        or not 0 <= observed_count <= discovered
        or (discovered > 0 and observed_count == 0)
    ):
        return True
    stamp = snapshot.get('generated_at')
    if not isinstance(stamp, str):
        return True
    try:
        observed = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError:
        return True
    if observed.tzinfo is None or observed.utcoffset() is None:
        return True
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    hour = current.replace(minute=0, second=0, microsecond=0)
    return not hour <= observed <= current


def reply_collection_due(metrics: object, *, now: dt.datetime | None = None) -> bool:
    if not isinstance(metrics, dict) or metrics.get('schema') != 'digitalmate/env-request-latency-daily@1' or metrics.get('env') != 'adx-jiuwen':
        return True
    replies = metrics.get('cloud_reply_daily')
    if not isinstance(replies, dict) or replies.get('schema') != 'digitalmate/cloud-reply-daily@1':
        return True
    c = replies.get('coverage') or {}
    if type(c.get('expected_nodes')) is not int or c['expected_nodes'] <= 0 or c.get('read_nodes') != c['expected_nodes']:
        return True
    try:
        observed = dt.datetime.fromisoformat(replies['observed_at'].replace('Z', '+00:00'))
        if observed.tzinfo is None or observed.utcoffset() is None:
            return True
    except (KeyError, ValueError, AttributeError, TypeError):
        return True
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    return not current.replace(minute=0, second=0, microsecond=0) <= observed <= current


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit('usage: production_sfs_freshness.py SNAPSHOT [METRICS]')
    try:
        snapshot = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        snapshot = None
    due = collection_due(snapshot)
    if len(sys.argv) == 3:
        try:
            metrics = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            metrics = None
        due = due or reply_collection_due(metrics)
    print('true' if due else 'false')


if __name__ == '__main__':
    main()
