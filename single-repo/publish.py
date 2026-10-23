#!/usr/bin/env python3
"""Generate a static site and commit site/ for the repository's Pages workflow."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from gsb import gitcode_sync, run_changes
from gsb.github import GitHub, GitHubError, discover_token
from gsb.project import REPO_RE, build_project

ROOT = Path(__file__).resolve().parent
STATIC_FILES = ("index.html", "app.js", "style.css", "board.js", "board-local.js", "gitcode-sync.js",
                "real-e2e.html", "real-e2e.js", "e2e-report.html")
# Written only when the collect workflow fetched GitCode sync records from the bot.
SYNC_DATA = "data/gitcode-sync.json"


def deployment_for(repo, settings, target=None):
    deployment = next((value for source, value in settings.get("deployments", {}).items()
                       if source.lower() == repo.lower()), None)
    if deployment and target and target.lower() != deployment["repository"].lower():
        raise ValueError("source repository does not match publishing destination")
    return deployment


def export_site(output, snapshot):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for name in STATIC_FILES:
        shutil.copyfile(ROOT / "static" / name, output / name)
    (output / "data").mkdir(exist_ok=True)
    target = output / "data/snapshot.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(target)
    (output / ".nojekyll").write_text("")


def publish(gh, site, repository, branch="main", *, source_token=None):
    if not REPO_RE.fullmatch(repository) or not REPO_RE.fullmatch("owner/" + branch):
        raise ValueError("invalid publishing target")
    prefix = f"/repos/{repository}"
    if gh.get(prefix).get("private"):
        raise ValueError("publishing target must be a public Pages repository")
    # The destination must already contain its deployment workflow. Retain all
    # source files and workflows; the bot's Contents token changes only site/.
    old = gh.get(prefix + "/git/ref/heads/" + branch)["object"]["sha"]
    base_tree = gh.get(prefix + "/git/commits/" + old)["tree"]["sha"]
    files = [*STATIC_FILES, "data/snapshot.json", ".nojekyll", *([SYNC_DATA] if (Path(site) / SYNC_DATA).exists() else [])]
    tree = [{"path": "site/" + p, "mode": "100644", "type": "blob", "content": (Path(site) / p).read_text(encoding="utf-8")} for p in files]
    for entry in tree:
        if any(token and token in entry["content"] for token in (gh.token, source_token)):
            raise ValueError("credential detected in site")

    def write(method, path, body):
        return gh._request(method, gh._url(prefix + path, None), body=body)[0]

    new_tree = write("POST", "/git/trees", {"base_tree": base_tree, "tree": tree})
    commit = write("POST", "/git/commits", {"message": "更新项目看板快照", "tree": new_tree["sha"], "parents": [old]})
    write("PATCH", "/git/refs/heads/" + branch, {"sha": commit["sha"], "force": False})
    return commit["sha"]


INLINE_FILE, INLINE_TOTAL = 256 * 1024, 2 * 1024 * 1024
BUNDLE_DIR = ".tmp/e2e-html"


def e2e_run_records(sync, snapshot):
    """Replace E2E run records; any failure keeps the published ones and the collection goes on."""
    from gsb import e2e_records
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    try:
        # Run details may spend their entire budget before records get a turn.
        # Reserve one listing and two capped ZIP downloads, not an unbounded bypass.
        gh = sync.gh.separate_phase(requests=1 + e2e_records.MAX_DOWNLOADS, seconds=120)
        return e2e_records.refresh(gh, sync.repo, ROOT, snapshot, sync.now, bundle=int(run_id) if run_id.isdigit() else None,
                                   max_bytes=sync.cfg.artifact_max_bytes)
    except Exception as err:  # noqa: BLE001 - records are optional evidence
        print(json.dumps({"e2e_records": "skipped", "error": type(err).__name__}), file=sys.stderr)
        return None


def publish_batch(gh, repository, branch, base, files, *, source_token=None):
    """Commit checkpoints and public data on the exact checkout we collected."""
    import re
    if not REPO_RE.fullmatch(repository) or not REPO_RE.fullmatch("owner/" + branch) or not re.fullmatch("[0-9a-f]{40}", base):
        raise ValueError("invalid publication base")
    prefix = f"/repos/{repository}"
    if gh.get(prefix).get("private"):
        raise ValueError("publishing target must be public")
    if gh.get(prefix + "/git/ref/heads/" + branch)["object"]["sha"] != base:
        raise ValueError("publication conflict; retry from latest checkout")
    for path, content in files.items():
        allowed = path in {"site/" + x for x in (*STATIC_FILES, "data/snapshot.json", ".nojekyll", SYNC_DATA)} or path in {".sync/state.json", ".sync/aggregate.json", ".sync/supplements.json", ".sync/tagged.json", ".sync/coverage.json", ".sync/coverage-sources.json"} or re.fullmatch(r"site/data/history/(manifest\.json|(?:index|records|catalog)/(?:issues|prs|runs|releases)/[0-9]{12}\.json)", path) or re.fullmatch(r"site/data/e2e/(?:index|[0-9]{1,20})\.json", path)
        # None deletes a file; only E2E run records are ever removed.
        if content is None and not path.startswith("site/data/e2e/"):
            raise ValueError("unsafe public file")
        if not allowed or any(t and content is not None and t in content for t in (gh.token, source_token)):
            raise ValueError("unsafe public file")
    if not files:
        return None
    def write(method, path, body):
        return gh._request(method, gh._url(prefix + path, None), body=body)[0]
    # GitHub limits content-creating requests per hour, so small files travel
    # inside the one tree request; only large ones get a blob request each,
    # which keeps big issue bodies out of a single giant tree body.
    entries, inline = [], 0
    for path, content in sorted(files.items()):
        if content is None:
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        size = len(content.encode("utf-8"))
        if size <= INLINE_FILE and inline + size <= INLINE_TOTAL:
            inline += size
            entries.append({"path": path, "mode": "100644", "type": "blob", "content": content})
            continue
        blob = write("POST", "/git/blobs", {"content": content, "encoding": "utf-8"})
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = write("POST", "/git/trees", {"base_tree": gh.get(prefix + "/git/commits/" + base)["tree"]["sha"], "tree": entries})
    commit = write("POST", "/git/commits", {"message": "增量同步看板数据与进度", "tree": tree["sha"], "parents": [base]})
    write("PATCH", "/git/refs/heads/" + branch, {"sha": commit["sha"], "force": False})
    return commit["sha"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="openJiuwen-ai/sciencediscovery")
    parser.add_argument("--output", default=str(ROOT / "dist"))
    parser.add_argument("--settings", default=str(ROOT / "board-config.json"))
    parser.add_argument("--publish-repo")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--incremental", action="store_true", help="resume .sync/ from this checkout")
    parser.add_argument("--request-budget", type=int, default=180)
    args = parser.parse_args()
    token, _ = discover_token()
    publish_token = os.environ.get("GSB_PUBLISH_TOKEN", "").strip() or token
    gh = GitHub(token, timeout=30)
    phase = "collect"
    try:
        settings = json.loads(Path(args.settings).read_text())
        deployment = deployment_for(args.repo, settings, args.publish_repo)
        if args.publish_repo and os.environ.get("GSB_ALLOW_PRIVATE", "").strip().lower() in ("1", "true", "yes", "on"):
            source_meta = gh.get(f"/repos/{args.repo}")
            if source_meta.get("private") or source_meta.get("visibility", "public") != "public":
                raise ValueError("private source cannot be published to public Pages; use the local server")
        sync = records = None
        if args.incremental:
            from gsb.sync import Sync
            from gsb.incremental_project import build_snapshot
            sync = Sync(gh, ROOT, args.repo, settings, requests=args.request_budget).collect(
                changed_runs=run_changes.load_changes(os.environ.get("GSB_RUN_CHANGES_FILE"), args.repo))
            phase = "snapshot"
            snapshot = build_snapshot(sync)
            records = e2e_run_records(sync, snapshot)
        else:
            snapshot = build_project(gh, args.repo, settings)
        if deployment:
            snapshot["deployment"] = deployment
        export_site(args.output, snapshot)
        result = {"ok": True, "repo": args.repo, "generated_at": snapshot["generated_at"]}
        sync_doc = gitcode_sync.load(os.environ.get("GSB_GITCODE_SYNC_FILE"), ROOT / "site" / SYNC_DATA, snapshot["generated_at"])
        if sync_doc is not None:
            (Path(args.output) / SYNC_DATA).write_text(gitcode_sync.encode(sync_doc), encoding="utf-8")
            result["gitcode_sync"] = {"available": sync_doc["available"], "records": len(sync_doc["records"])}
        if records:
            records.write_preview(args.output)
            result["e2e_records"] = records.summary
        if args.publish_repo:
            phase = "publish"
            if not publish_token:
                raise ValueError("publishing requires a token")
            publisher = GitHub(publish_token, timeout=30)
            if sync:
                from gsb.history import encode
                files = sync.files()
                files.update({"site/" + name: (Path(args.output) / name).read_text() for name in (*STATIC_FILES, ".nojekyll")})
                files["site/data/snapshot.json"] = encode(snapshot)
                if sync_doc is not None:
                    files["site/" + SYNC_DATA] = gitcode_sync.encode(sync_doc)
                if records:
                    files.update(records.files)
                    # New HTML reports leave through the workflow's Actions artifact, never git.
                    records.write_bundle(ROOT / BUNDLE_DIR)
                files = {path: content for path, content in files.items()
                         if ((ROOT / path).exists() if content is None else not (ROOT / path).exists() or (ROOT / path).read_text() != content)}
                base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
                result["commit"] = publish_batch(publisher, args.publish_repo, args.branch, base, files, source_token=token)
                result["sync"] = sync.progress()
                result["changed_files"] = len(files)
                run_changes.write_receipt(os.environ.get("GSB_RUN_CHANGES_RECEIPT"), args.repo, sync.completed_changes(), result["commit"] or base)
            else:
                result["commit"] = publish(publisher, args.output, args.publish_repo, args.branch, source_token=token)
            result["requests"] = {"source": getattr(gh, "calls", None), "publish": getattr(publisher, "calls", None)}
        else:
            result["requests"] = {"source": getattr(gh, "calls", None)}
        print(json.dumps(result))
    except (GitHubError, OSError, ValueError) as err:
        # API errors can include request context; expose only a stable category,
        # the phase that failed, the HTTP status and when a rate limit resets.
        failure = {"ok": False, "error": getattr(err, "kind", type(err).__name__), "phase": phase}
        if isinstance(err, GitHubError):
            failure["status"] = err.status
            if err.kind == "rate_limited":
                failure["limit"] = "secondary" if err.status == 429 or "secondary" in (err.message or "").lower() else "primary"
                if err.reset_at:
                    failure["reset_at"] = datetime.fromtimestamp(err.reset_at, timezone.utc).isoformat(timespec="seconds")
        print(json.dumps(failure))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
