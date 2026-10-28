"""Each artifact is read once: the board keeps what it already saw and only fetches what is new."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gsb.collectors import Context
from gsb.config import Config
from gsb.coverage_store import CoverageStore
from gsb.project import run_details, slim_run
from gsb.public_sections import public_tests
from gsb.sync import Sync, stamp
from test_incremental import NOW, REPO, Source, run
from test_reports import archive

ROOT = Path(__file__).resolve().parents[1]
FUTURE = "2026-12-01T00:00:00Z"
COMPLETE = {"ut": {"complete": True, "producer": "success"}, "st": {"complete": True, "producer": "success"}}


def totals(covered, total=10):
    return {"lines": {"covered": covered, "total": total, "percentage": covered * 100 / total}}


def raw(ident, name, branch="main", created=None):
    return {"id": ident, "name": name, "size_in_bytes": 10, "expired": False, "url": f"https://api.github.com/a/{ident}",
            "created_at": created or stamp(NOW - timedelta(minutes=1000 - ident % 1000)), "expires_at": FUTURE,
            "workflow_run": {"id": ident // 10, "head_branch": branch, "head_sha": f"{ident:040d}"}}


def manifest(artifact, covered):
    language = "python" if artifact["name"].startswith("python") else "node"
    group = "services/app" if language == "python" else "packages/app"
    sources = [{"path": f"{group}/a.py" if language == "python" else f"{group}/a.ts", "totals": totals(covered)}]
    return {"schema_version": 1, "language": language, "layers": COMPLETE, "totals": totals(covered),
            "groups": [{"name": group, "files": 1, "totals": totals(covered)}], "sources": sources}


class Artifacts:
    """GitHub's artifact listing, newest first, 100 per page; tree and files are empty."""
    def __init__(self, rows):
        self.rows, self.pages = rows, []

    def get(self, path, params=None):
        if path.endswith("/actions/artifacts"):
            self.pages.append(params["page"])
            ordered = sorted(self.rows, key=lambda row: -row["id"])
            return {"total_count": len(ordered), "artifacts": deepcopy(ordered[(params["page"] - 1) * 100:params["page"] * 100])}
        return {"tree": []}

    def get_text_file(self, *_):
        return "{}"


class CoverageStoreTests(unittest.TestCase):
    def setUp(self):
        # 140 test-result artifacts and five coverage summaries: two full main
        # pushes per language and one pull request.
        self.rows = [raw(1000 + i, "ut-results") for i in range(140)]
        self.rows += [raw(2001, "node-coverage-summary-push-a"), raw(2002, "python-coverage-summary-push-a"),
                      raw(2003, "node-coverage-summary-pr-7-b", branch="feature"),
                      raw(2004, "node-coverage-summary-push-c"), raw(2005, "python-coverage-summary-push-c")]
        self.covered = {2001: 5, 2002: 6, 2003: 7, 2004: 8, 2005: 9}

    def build(self, gh, state=None, sources=None):
        store = CoverageStore(deepcopy(state), deepcopy(sources))
        loads = []
        def load(_ctx, artifact, _notes):
            loads.append(artifact["id"])
            covered = self.covered.get(artifact["id"])
            return None if covered is None else {"coverage_manifest": manifest(artifact, covered)}
        ctx = Context(gh, Config(repo=REPO), NOW, {"default_branch": "main"}, coverage_store=store)
        with patch("gsb.collectors._load_artifact", side_effect=load):
            coverage = public_tests(ctx, [])["coverage"]
        store.finish(NOW, complete=True)
        # What the next build reads back is the committed JSON.
        return store, loads, coverage, json.loads(json.dumps(store.state)), json.loads(json.dumps(store.sources))

    def test_only_unseen_artifacts_are_listed_and_downloaded(self):
        gh = Artifacts(self.rows)
        store, loads, first, state, sources = self.build(gh)
        self.assertEqual(gh.pages, [1, 2])
        self.assertEqual(sorted(loads), [2001, 2002, 2003, 2004, 2005])
        # Per-file lists are kept only for the summaries the view shows.
        self.assertEqual(sorted(sources), ["2004", "2005"])
        self.assertEqual(first["languages"]["node"]["current"]["sources"][0]["totals"]["lines"]["covered"], 8)

        # A new pull request summary and a new test report arrive.
        self.rows += [raw(2006, "node-coverage-summary-pr-8-d", branch="feature"), raw(2007, "ut-results")]
        self.covered[2006] = 4
        gh.pages = []
        _, loads, second, state, sources = self.build(gh, state, sources)
        self.assertEqual(gh.pages, [1])
        self.assertEqual(loads, [2006])
        self.assertEqual(second["languages"]["node"]["current"], first["languages"]["node"]["current"])
        self.assertEqual([row["number"] for row in second["languages"]["node"]["pull_requests"]], [8, 7])

        # Nothing new: no download at all, the same answer.
        gh.pages = []
        _, loads, third, *_ = self.build(gh, state, sources)
        self.assertEqual((gh.pages, loads), ([1], []))
        self.assertEqual(third["languages"], second["languages"])

    def test_a_new_baseline_is_downloaded_once_and_old_lists_are_dropped(self):
        gh = Artifacts(self.rows)
        _, _, _, state, sources = self.build(gh)
        self.rows.append(raw(2008, "node-coverage-summary-push-e"))
        self.covered[2008] = 10
        _, loads, coverage, state, sources = self.build(gh, state, sources)
        # Its list is read from the same download that told the build it is the newest full result.
        self.assertEqual(loads, [2008])
        self.assertEqual(coverage["languages"]["node"]["current"]["sources"][0]["totals"]["lines"]["covered"], 10)
        self.assertEqual(sorted(sources), ["2005", "2008"])

    def test_failed_downloads_are_retried_and_expired_artifacts_forgotten(self):
        gh = Artifacts(self.rows)
        missing = self.covered.pop(2003)
        store, loads, _, state, sources = self.build(gh)
        self.assertNotIn("manifest", state["artifacts"].get("2003", {}))
        self.covered[2003] = missing
        _, loads, _, state, sources = self.build(gh, state, sources)
        self.assertEqual(loads, [2003])
        state["artifacts"]["2001"]["artifact"]["expires_at"] = stamp(NOW - timedelta(days=1))
        store = CoverageStore(state, sources)
        store.finish(NOW, complete=False)
        self.assertNotIn("2001", store.state["artifacts"])


class RunDetailTests(unittest.TestCase):
    def details(self, artifacts, bodies):
        class GH:
            downloads = []
            def paginate(self, path, **kw):
                return [] if path.endswith("/jobs") else deepcopy(artifacts)
            def download_artifact(self, repo, ident, **kw):
                self.downloads.append(ident)
                return bodies[ident]
        gh, record = GH(), slim_run(run(), {})
        read = lambda version=1: run_details(gh, Config(repo=REPO), record, cache=deepcopy(record), parser_version=version)  # noqa: E731
        return gh, record, read

    def test_summaries_and_raw_coverage_are_left_to_the_coverage_view(self):
        artifacts = [dict(id=1, name="node-coverage-summary-push-a"), dict(id=3, name="ut-coverage"), dict(id=4, name="e2e-results")]
        bodies = {4: archive({"dashboard-summary.json": json.dumps(dict(tests=2, passed=2, failed=0, skipped=0, flaky=0))})}
        gh, record, read = self.details(artifacts, bodies)
        read()
        self.assertEqual(gh.downloads, [4])
        self.assertEqual(record["reports_status"], "available")

    def test_artifacts_without_counts_or_over_budget_are_read_once(self):
        artifacts = [dict(id=2, name="dashboard-trace"), dict(id=3, name="lcov-coverage"), dict(id=4, name="e2e-results"),
                     dict(id=5, name="st-results", size_in_bytes=10 ** 12)]
        bodies = {2: archive({"trace.zip": "not a report"}), 3: archive({"lcov.info": "SF:a.js\nLF:2\nLH:1\nend_of_record\n"}),
                  4: archive({"dashboard-summary.json": json.dumps(dict(tests=2, passed=2, failed=0, skipped=0, flaky=0))})}
        gh, record, read = self.details(artifacts, bodies)
        read()
        self.assertEqual(gh.downloads, [2, 3, 4])
        self.assertEqual(sorted(record["inspected"]), ["2", "3", "5"])
        # Too large is a property of the artifact, not a passing failure: no recheck is waiting for it.
        self.assertEqual({t["artifact_id"]: t["status"] for t in record["tests"]}, {2: "no_counts", 4: "available", 5: "unreadable"})
        coverage, tests = deepcopy(record["coverage"]), deepcopy(record["tests"])
        read()
        self.assertEqual(gh.downloads, [2, 3, 4])
        self.assertEqual((record["coverage"], record["tests"]), (coverage, tests))
        # A new parser reads them again and replaces, not duplicates, what they held.
        read(version=2)
        self.assertEqual(gh.downloads, [2, 3, 4, 2, 3, 4])
        self.assertEqual(record["coverage"], coverage)


class QueueTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)

    def test_completed_runs_leave_the_recheck_queue_once_settled(self):
        results = {1: "available", 2: "missing"}
        def details(_gh, _cfg, row, **_kw):
            row.update(reports_status=results[row["id"]], jobs_status="available", tests=[])
            return row
        gh = Source(runs=2)
        with patch("gsb.sync.run_details", side_effect=details):
            sync = Sync(gh, Path(self.temp.name), REPO, now=NOW).collect()
            # Every report read: nothing left to recheck.
            self.assertNotIn("run:1-1", sync.state["pending"])
            # Nothing to read: one more look for late listings, then done.
            self.assertIn("run:2-1", sync.state["pending"])
            sync.now = NOW + timedelta(days=1)
            sync.pending()
            self.assertNotIn("run:2-1", sync.state["pending"])

    def test_unreadable_details_keep_being_retried(self):
        def details(_gh, _cfg, row, **_kw):
            row.update(reports_status="unavailable", tests=[])
            return row
        with patch("gsb.sync.run_details", side_effect=details):
            sync = Sync(Source(runs=1), Path(self.temp.name), REPO, now=NOW).collect()
            for day in range(1, 4):
                sync.now = NOW + timedelta(days=day)
                sync.pending()
        self.assertEqual(sync.state["pending"]["run:1-1"]["error"], "details_unavailable")


if __name__ == "__main__":
    unittest.main()


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def build(self, gh, tests):
        from gsb.incremental_project import build_snapshot
        sync = Sync(gh, self.root, REPO, now=NOW).collect()
        with patch("gsb.incremental_project.public_ops", return_value={}), patch("gsb.incremental_project.public_tests", side_effect=tests):
            doc = build_snapshot(sync)
        for name, content in {**sync.files(), "site/data/snapshot.json": json.dumps(doc)}.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(content)
        return doc, json.loads((self.root / ".sync/supplements.json").read_text())

    def test_a_failed_refresh_keeps_the_last_good_evidence_and_is_retried(self):
        gh = Source(runs=1)
        def good(ctx, runs, owns=None, coverage_eligible=None):
            self.assertIsInstance(ctx.coverage_store, CoverageStore)
            return {"executed": [], "coverage": {"source": "Actions coverage summaries", "value": {"lines_pct": 80}}}
        doc, supplements = self.build(gh, good)
        revision = supplements["tests_revision"]
        gh.runs.append(run(2))
        from gsb.github import GitHubError
        def limited(*_a, **_kw):
            raise GitHubError("quota", kind="rate_limited", status=403)
        doc, supplements = self.build(gh, limited)
        self.assertEqual(doc["sections"]["tests"]["status"], "ok")
        self.assertEqual(doc["sections"]["tests"]["data"]["coverage"]["value"], {"lines_pct": 80})
        # The stored revision still names the old run set, so the next build reads again.
        self.assertEqual(supplements["tests_revision"], revision)


class FailureLineTests(unittest.TestCase):
    def test_the_failure_line_names_phase_status_and_limit(self):
        import contextlib, sys
        import publish
        from gsb.github import GitHubError
        error = GitHubError("You have exceeded a secondary rate limit", kind="rate_limited", status=403,
                            reset_at=datetime(2026, 9, 23, 16, 0, tzinfo=timezone.utc).timestamp(), url="https://api.github.com/secret-path")
        out = io.StringIO()
        with patch.object(sys, "argv", ["publish.py", "--incremental", "--repo", REPO, "--output", str(ROOT / ".tmp/unused")]), \
                patch("publish.discover_token", return_value=("t", None)), patch("gsb.sync.Sync", **{"return_value.collect.side_effect": error}), \
                contextlib.redirect_stdout(out):
            self.assertEqual(publish.main(), 1)
        line = json.loads(out.getvalue())
        self.assertEqual(line, {"ok": False, "error": "rate_limited", "phase": "collect", "status": 403,
                                "limit": "secondary", "reset_at": "2026-09-23T16:00:00+00:00"})
        self.assertNotIn("secret-path", out.getvalue())
