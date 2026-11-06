"""Validate workflow inputs before minting repository-scoped App tokens."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


def collection_context(destination: str, source_hint: str = "", request_id: str = "") -> dict[str, str]:
    deployments = json.loads((ROOT / "board-config.json").read_text())["deployments"]
    matches = [(source, value["repository"]) for source, value in deployments.items()
               if value["repository"].lower() == destination.lower()]
    if len(matches) != 1:
        raise ValueError("repository is not a configured dashboard destination")
    source, target = matches[0]
    if source_hint and source_hint.lower() != source.lower():
        raise ValueError("source repository does not match this dashboard")
    if request_id and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", request_id):
        raise ValueError("invalid collection request id")
    source_owner, source_name = source.split("/")
    target_owner, target_name = target.split("/")
    return {"source": source, "target": target, "source_owner": source_owner,
            "source_name": source_name, "target_owner": target_owner, "target_name": target_name}


def main() -> None:
    context = collection_context(os.environ["GITHUB_REPOSITORY"], os.environ.get("SOURCE_REPOSITORY", ""), os.environ.get("COLLECTION_REQUEST_ID", ""))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.writelines(f"{key}={value}\n" for key, value in context.items())


if __name__ == "__main__":
    main()
