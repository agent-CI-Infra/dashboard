#!/usr/bin/env python3
"""Configure this dashboard repository for one source repository.

The template is intentionally small: a user supplies a source repository and an
optional JSON configuration, then this script writes ``board-config.json`` and
the normal collection workflow does the rest.  No source-repository code is
copied here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
KEY_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,30}\Z")

DEFAULT_CONFIG = {
    "review_sla_days": 3,
    "stale_days": 30,
    "workflows": {
        "gate": ["^CI$", "CI Gates", "gate", "门禁"],
        "daily": ["nightly", "daily", "每日"],
        "release": ["release", "version", "版本"],
    },
    "branch_lines": [],
}
ALLOWED_KEYS = {"source_repository", "label", "environment", *DEFAULT_CONFIG}
DATA_FILES = (
    ".sync/state.json",
    ".sync/aggregate.json",
    ".sync/supplements.json",
    ".sync/tagged.json",
    ".sync/coverage.json",
    ".sync/coverage-sources.json",
    "site/data/snapshot.json",
    "site/data/gitcode-sync.json",
)


def fail(message: str) -> None:
    print(f"setup: {message}", file=sys.stderr)
    raise SystemExit(2)


def read_config(args) -> dict:
    if args.config and args.json:
        fail("use either --config or --json, not both")
    if args.config:
        try:
            return json.loads(Path(args.config).read_text(encoding="utf-8"))
        except OSError as error:
            fail(f"cannot read {args.config}: {error}")
        except json.JSONDecodeError as error:
            fail(f"{args.config} is not valid JSON: {error}")
    if args.json:
        try:
            return json.loads(args.json)
        except json.JSONDecodeError as error:
            fail(f"--json is not valid JSON: {error}")
    return {}


def validate_repository(value: str, field: str) -> str:
    value = (value or "").strip()
    if not REPO_RE.fullmatch(value):
        fail(f"{field} must be owner/repository, got {value!r}")
    return value


def build_config(source: str, board: str, raw: dict) -> dict:
    if not isinstance(raw, dict):
        fail("configuration must be a JSON object")
    unknown = sorted(set(raw) - ALLOWED_KEYS)
    if unknown:
        fail("unknown configuration keys: " + ", ".join(unknown))
    source = validate_repository(raw.get("source_repository") or source, "source_repository")
    if source.lower() == board.lower():
        fail("source_repository and the dashboard repository must be different")

    config = json.loads(json.dumps(DEFAULT_CONFIG))
    for key in ("review_sla_days", "stale_days"):
        if key in raw:
            value = raw[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                fail(f"{key} must be a non-negative integer")
            config[key] = value
    if "workflows" in raw:
        workflows = raw["workflows"]
        if not isinstance(workflows, dict) or not workflows:
            fail("workflows must be a non-empty object")
        for kind, patterns in workflows.items():
            if kind not in ("gate", "daily", "release"):
                fail(f"unknown workflow kind: {kind}")
            if not isinstance(patterns, list) or not patterns or not all(isinstance(item, str) and item for item in patterns):
                fail(f"workflows.{kind} must be a non-empty list of regular expressions")
            for pattern in patterns:
                try:
                    re.compile(pattern)
                except re.error as error:
                    fail(f"invalid regex in workflows.{kind}: {error}")
        config["workflows"] = workflows
    if "branch_lines" in raw:
        lines = raw["branch_lines"]
        if not isinstance(lines, list):
            fail("branch_lines must be a list")
        for line in lines:
            if not isinstance(line, dict):
                fail("each branch line must be an object")
            key, ref = str(line.get("key") or ""), str(line.get("ref") or "")
            if not KEY_RE.fullmatch(key) or not ref:
                fail("branch line keys must match [a-z0-9][a-z0-9-]{0,30} and ref must be non-empty")
        config["branch_lines"] = lines

    config["deployments"] = {
        source: {
            "environment": str(raw.get("environment") or "production"),
            "label": str(raw.get("label") or source.split("/", 1)[1]),
            "repository": board,
        }
    }
    return config


def reset_data() -> None:
    # Only generated data is removed. Static assets, workflows and source stay.
    for relative in DATA_FILES:
        path = ROOT / relative
        if path.exists():
            path.unlink()
            print(f"removed {relative}")
    for relative in ("site/data/history", "site/data/e2e"):
        path = ROOT / relative
        if path.exists():
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            path.rmdir()
            print(f"removed {relative}/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="source repository, owner/repo")
    parser.add_argument("--board", default=os.environ.get("GITHUB_REPOSITORY", ""),
                        help="dashboard repository (default: GITHUB_REPOSITORY)")
    parser.add_argument("--config", help="JSON configuration file")
    parser.add_argument("--json", help="JSON configuration string")
    parser.add_argument("--output", default=str(ROOT / "board-config.json"))
    parser.add_argument("--reset", action="store_true", help="remove generated data for a different source")
    parser.add_argument("--dry-run", action="store_true", help="print the generated config without writing")
    args = parser.parse_args()

    source = validate_repository(args.source, "--source")
    board = validate_repository(args.board, "--board")
    config = build_config(source, board, read_config(args))
    encoded = json.dumps(config, ensure_ascii=False, indent=2) + "\n"

    if args.dry_run:
        print(encoded, end="")
        return 0

    if args.reset:
        reset_data()
    output = Path(args.output)
    output.write_text(encoded, encoding="utf-8")
    print(f"wrote {output}")
    print(f"source: {source}")
    print(f"dashboard: {board}")
    if not os.environ.get("GITHUB_ACTIONS"):
        print("next: commit board-config.json, run Collect dashboard data (PAT); for a private source use the local server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
