from datetime import timedelta
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from gsb.collectors import collect_ci
from gsb.incremental_project import build_snapshot
from gsb.lines import canonical_branch, configured_lines, line_of, target_of
from gsb.sync import Sync
from test_ci_lanes import NOW, RULES, SnapshotLaneTests, points
from test_incremental import REPO

ROOT = Path(__file__).resolve().parents[1]
LINES = [{"key": "jiuwen", "ref": "feat/jiuwenswarm"}]
RENAMED_LINES = [
    {"key": "main", "ref": "main", "historical_refs": [{"ref": "feat/jiuwenswarm", "before": "2026-09-22T11:56:00Z"}]},
    {"key": "legacy", "ref": "legacy", "historical_refs": [{"ref": "main", "before": "2026-09-22T11:56:00Z"}]},
    {"key": "release", "ref": "releases/v0.3.0.beta"},
]


class ConfigTests(unittest.TestCase):
    def test_default_branch_comes_first_and_invalid_entries_are_dropped(self):
        lines = configured_lines({"branch_lines": [
            {"key": "jiuwen", "ref": "feat/jiuwenswarm"}, {"key": "main", "ref": "main"},
            {"key": "Bad Key", "ref": "x"}, {"key": "jiuwen", "ref": "other"}, {"key": "again", "ref": "feat/jiuwenswarm"}, "junk"]}, "main")
        self.assertEqual([(line["key"], line["ref"], line["default"]) for line in lines],
                         [("main", "main", True), ("jiuwen", "feat/jiuwenswarm", False)])
        self.assertEqual(configured_lines({}, "main"), [{"key": "main", "ref": "main", "default": True}])

    def test_runs_belong_to_the_branch_their_work_targets(self):
        lines = configured_lines({"branch_lines": LINES}, "main")
        prs = [dict(number=9, head="swarm-fork", head_repo="fork/source", base="feat/jiuwenswarm",
                    created_at="2026-09-20T00:00:00Z", closed_at=None)]
        by_number = {9: prs[0]}
        cases = [
            (dict(event="push", branch="feat/jiuwenswarm"), "jiuwen"),
            (dict(event="workflow_dispatch", branch="feat/jiuwenswarm"), "jiuwen"),
            (dict(event="pull_request", branch="x", base_branch="feat/jiuwenswarm"), "jiuwen"),
            (dict(event="pull_request", branch="x", pull_requests=[9]), "jiuwen"),
            (dict(event="pull_request", branch="swarm-fork", head_repo="fork/source", created_at="2026-09-21T00:00:00Z"), "jiuwen"),
            (dict(event="pull_request", branch="unknown", created_at="2026-09-21T00:00:00Z"), "main"),
            (dict(event="push", branch="0.3.0"), "main"),
            (dict(event="schedule", branch="main"), "main"),
        ]
        for run, expected in cases:
            self.assertEqual(line_of(target_of(run, prs, by_number), lines), expected, run)

    def test_historical_refs_split_old_main_from_new_main(self):
        lines = configured_lines({"branch_lines": RENAMED_LINES}, "main")
        self.assertEqual([line["key"] for line in lines], ["main", "legacy", "release"])
        old, new = "2026-09-22T11:55:00Z", "2026-09-22T11:57:00Z"
        self.assertEqual(line_of("main", lines, old), "legacy")
        self.assertEqual(canonical_branch("main", lines, old), "legacy")
        self.assertEqual(line_of("main", lines, new), "main")
        self.assertEqual(line_of("feat/jiuwenswarm", lines, old), "main")
        self.assertEqual(canonical_branch("feat/jiuwenswarm", lines, old), "main")
        self.assertEqual(line_of("releases/v0.3.0.beta", lines, old), "release")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self):
        gh = SnapshotLaneTests.source(None)
        def raw(ident, event, branch, minutes, **extra):
            created = (NOW - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
            return {**gh.runs[1], "id": ident, "event": event, "head_branch": branch, "display_title": f"CI {ident}",
                    "html_url": f"https://github.com/{REPO}/actions/runs/{ident}", "created_at": created, "updated_at": created,
                    "run_started_at": created, **extra}
        gh.runs += [raw(6, "pull_request", "swarm-fix", 8, pull_requests=[{"number": 8, "base": {"ref": "feat/jiuwenswarm"}}]),
                    raw(7, "pull_request", "swarm-fork", 7, head_repository={"full_name": "fork/source"}),
                    raw(8, "workflow_dispatch", "feat/jiuwenswarm", 6), raw(9, "push", "feat/jiuwenswarm", 5)]
        pr = gh.prs[0]
        gh.prs += [{**pr, "number": 8, "head": {"ref": "swarm-fix", "sha": "e" * 40, "repo": {"full_name": REPO}}, "base": {"ref": "feat/jiuwenswarm"},
                    "html_url": f"https://github.com/{REPO}/pull/8"},
                   {**pr, "number": 9, "head": {"ref": "swarm-fork", "sha": "f" * 40, "repo": {"full_name": "fork/source"}}, "base": {"ref": "feat/jiuwenswarm"},
                    "html_url": f"https://github.com/{REPO}/pull/9"}]
        return gh

    def test_each_line_gets_its_own_ci_tests_and_coverage_evidence(self):
        sync = Sync(self.source(), self.root, REPO, settings={"workflows": RULES, "branch_lines": LINES}, now=NOW).collect()
        calls = {}
        def tests(ctx, runs, owns=None, coverage_eligible=None):
            calls[ctx.default_branch] = ([run["id"] for run in runs], owns)
            return {"executed": [], "coverage": {}}
        with patch("gsb.incremental_project.public_ops", return_value={}), patch("gsb.incremental_project.public_tests", side_effect=tests):
            doc = build_snapshot(sync)
        self.assertEqual([(line["key"], line["default"]) for line in doc["lines"]], [("main", True), ("jiuwen", False)])
        main, swarm = doc["sections"]["ci"]["data"], doc["line_sections"]["jiuwen"]["ci"]["data"]
        self.assertEqual({lane["key"]: points(lane) for lane in main["lanes"]["lanes"]},
                         {"pr": [1], "main": [2, 5], "merge_queue": [], "daily": [3], "release": [4]})
        self.assertEqual({lane["key"]: points(lane) for lane in swarm["lanes"]["lanes"]},
                         {"pr": [6, 7], "main": [8, 9], "merge_queue": [], "daily": [], "release": []})
        self.assertEqual([lane["label"] for lane in swarm["lanes"]["lanes"]],
                         ["PR", "feat/jiuwenswarm", "合并队列", "Daily", "版本"])
        self.assertEqual((swarm["default_branch"], swarm["lanes"]["branch"]), ("feat/jiuwenswarm", "feat/jiuwenswarm"))
        self.assertEqual((main["pull_request"]["total"], swarm["pull_request"]["total"], swarm["main"]["total"]), (1, 2, 2))
        self.assertEqual(sorted(calls["main"][0]), [1, 2, 3, 4, 5])
        self.assertEqual(sorted(calls["feat/jiuwenswarm"][0]), [6, 7, 8, 9])
        # Coverage artifacts follow their run; unknown runs follow their branch.
        own = calls["feat/jiuwenswarm"][1]
        self.assertEqual([own({"run_id": 8}), own({"run_id": 2}), own({"run_id": 99, "branch": "feat/jiuwenswarm"}), own({"run_id": 98, "branch": "x"})],
                         [True, False, True, False])
        self.assertEqual(doc["line_sections"]["jiuwen"]["tests"]["data"]["tagged"], None)
        self.assertNotIn("main", doc["line_sections"])

    def test_changing_the_lines_rebuilds_an_unchanged_snapshot(self):
        def build(settings, collect):
            sync = Sync(self.source(), self.root, REPO, settings={"workflows": RULES, **settings}, now=NOW)
            if collect:
                sync.collect()
            else:
                sync.meta = sync.gh.get("/repos/" + REPO)
            with patch("gsb.incremental_project.public_ops", return_value={}), patch("gsb.incremental_project.public_tests", return_value={}), \
                    patch("gsb.incremental_project.collect_ci", wraps=collect_ci) as ci:
                doc = build_snapshot(sync)
            for name, content in {**sync.files(), "site/data/snapshot.json": json.dumps(doc)}.items():
                (self.root / name).parent.mkdir(parents=True, exist_ok=True)
                (self.root / name).write_text(content)
            return doc, ci.call_count
        self.assertEqual(build({}, True)[1], 1)
        doc, built = build({}, False)
        self.assertEqual((len(doc["lines"]), built), (1, 0))
        doc, built = build({"branch_lines": LINES}, False)
        self.assertEqual((sorted(doc["line_sections"]), built), (["jiuwen"], 2))

    def test_all_lines_rebuild_five_lanes_from_stored_runs_without_fetching_artifacts(self):
        source = self.source()
        source.runs += [
            {**source.runs[2], "id": 37845266203, "event": "workflow_dispatch", "head_branch": "releases/v0.3.0.beta"},
            {**source.runs[1], "id": 11, "event": "workflow_call", "head_branch": "releases/v0.3.0.beta"},
            {**source.runs[1], "id": 12, "name": "Maintenance", "head_branch": "releases/v0.3.0.beta"},
            {**source.runs[1], "id": 13, "name": "Maintenance", "head_branch": "feat/jiuwenswarm"},
            {**source.runs[1], "id": 14, "event": "merge_group", "head_branch": "feat/jiuwenswarm"},
            {**source.runs[1], "id": 15, "event": "merge_group", "head_branch": "releases/v0.3.0.beta"},
            {**source.runs[1], "id": 16, "event": None, "head_branch": "releases/v0.3.0.beta"},
        ]
        settings = {"workflows": RULES, "branch_lines": RENAMED_LINES}
        sync = Sync(source, self.root, REPO, settings=settings, now=NOW).collect()
        with patch("gsb.incremental_project.public_ops", return_value={}), patch("gsb.incremental_project.public_tests", return_value={}):
            doc = build_snapshot(sync)
        views = {"main": doc["sections"], **doc["line_sections"]}
        expected = {
            "main": {"pr": [6, 7], "main": [8, 9], "release": [4], "merge_queue": [14]},
            "legacy": {"pr": [1], "main": [2, 5], "daily": [3]},
            "release": {"daily": [37845266203], "merge_queue": [15]},
        }
        def check(snapshot):
            for key, sections in {"main": snapshot["sections"], **snapshot["line_sections"]}.items():
                lanes = sections["ci"]["data"]["lanes"]
                self.assertEqual({lane["key"]: points(lane) for lane in lanes["lanes"] if points(lane)}, expected[key])
                self.assertEqual([lane["key"] for lane in lanes["lanes"]], ["pr", "main", "merge_queue", "daily", "release"])
                self.assertNotIn("excluded", lanes)
        check(doc)
        # Rebuild the former seven-row snapshot even when no records change.
        for sections in views.values():
            lanes = sections["ci"]["data"]["lanes"]
            lanes["lanes"] = [lane for lane in lanes["lanes"] if lane["key"] != "merge_queue"]
            lanes["lanes"] += [{"key": key, "summary": {"median_duration_s": None}} for key in ("called", "other", "unknown")]
            lanes["excluded"] = {"daily": 1}
        for name, content in {**sync.files(), "site/data/snapshot.json": json.dumps(doc)}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        source.calls.clear()
        sync = Sync(source, self.root, REPO, settings=settings, now=NOW)
        sync.meta = sync.gh.get("/repos/" + REPO)
        self.assertFalse(sync.history.changed)
        with patch("gsb.incremental_project.public_ops", return_value={}), patch("gsb.incremental_project.public_tests", return_value={}) as tests:
            check(build_snapshot(sync))
        tests.assert_not_called()
        # No polling, artifact listing or downloads are needed for this projection.
        self.assertEqual(source.calls, [("/repos/" + REPO, {})])
        self.assertFalse(any("/records/" in name for name in sync.history.changed))
        for ident in (11, 12, 13, 16):
            self.assertIn(ident, {row["id"] for _, row in sync.history.rows("runs")})

    def test_renamed_branches_project_historical_runs_into_their_current_lines(self):
        source = self.source()
        created = (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        source.runs.append({**source.runs[1], "id": 10, "head_branch": "main", "created_at": created,
                            "updated_at": created, "run_started_at": created,
                            "html_url": f"https://github.com/{REPO}/actions/runs/10"})
        sync = Sync(source, self.root, REPO, settings={"workflows": RULES, "branch_lines": RENAMED_LINES}, now=NOW).collect()
        calls = {}
        def tests(ctx, runs, owns=None, coverage_eligible=None):
            calls[ctx.default_branch] = ([row["id"] for row in runs], owns)
            return {"executed": [], "coverage": {}}
        with patch("gsb.incremental_project.public_ops", return_value={}), patch("gsb.incremental_project.public_tests", side_effect=tests):
            doc = build_snapshot(sync)
        self.assertEqual([line["ref"] for line in doc["lines"]], ["main", "legacy", "releases/v0.3.0.beta"])
        self.assertEqual({lane["key"]: points(lane) for lane in doc["sections"]["ci"]["data"]["lanes"]["lanes"] if points(lane)},
                         {"pr": [6, 7], "main": [8, 9, 10], "release": [4]})
        legacy = doc["line_sections"]["legacy"]["ci"]["data"]
        self.assertEqual({lane["key"]: points(lane) for lane in legacy["lanes"]["lanes"]},
                         {"pr": [1], "main": [2, 5], "merge_queue": [], "daily": [3], "release": []})
        self.assertEqual(sorted(calls["legacy"][0]), [1, 2, 3, 5])
        self.assertEqual(sorted(calls["main"][0]), [4, 6, 7, 8, 9, 10])
        old_main = {"run_id": 2, "branch": "main", "created_at": "2026-09-22T11:20:00Z"}
        old_swarm = {"run_id": 8, "branch": "feat/jiuwenswarm", "created_at": "2026-09-22T11:54:00Z"}
        self.assertTrue(calls["legacy"][1](old_main))
        self.assertEqual(old_main["branch"], "legacy")
        self.assertTrue(calls["main"][1](old_swarm))
        self.assertEqual(old_swarm["branch"], "main")
