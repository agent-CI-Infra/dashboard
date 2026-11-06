#!/usr/bin/env python3
"""E2E HTML reports in the workflows: bundle retention for collect.yml, attachment for pages.yml."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import sys

from gsb import e2e_records
from gsb.github import GitHub


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    retention = commands.add_parser("retention", help="print days=N for a bundle directory; nothing when it holds no report")
    retention.add_argument("directory")
    attach = commands.add_parser("attach", help="copy unexpired reports into a Pages site")
    attach.add_argument("site")
    attach.add_argument("--wait", type=int, default=300, help="seconds to wait for a collection that is still uploading")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    if args.command == "retention":
        days = e2e_records.retention_days(args.directory, now)
        if days:
            print(f"days={days}")
        return 0
    try:
        result = e2e_records.attach(GitHub(os.environ.get("GITHUB_TOKEN", ""), timeout=60), os.environ["GITHUB_REPOSITORY"],
                                    args.site, now, wait=args.wait)
    except Exception as err:  # noqa: BLE001 - the site deploys without HTML rather than not at all
        e2e_records.withdraw(args.site)
        result = {"error": type(err).__name__}
    print(json.dumps(result), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
