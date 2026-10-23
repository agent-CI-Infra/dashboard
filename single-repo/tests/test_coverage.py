import io
import json
import unittest
import zipfile
from datetime import datetime
from unittest.mock import patch

from gsb.collectors import Context, _coverage_probe
from gsb.config import Config
from gsb.testparse import parse_artifact_zip, parse_coverage_file


LCOV = """TN:
SF:/workspace/services/example/src/index.js
FNF:4
FNH:3
BRF:10
BRH:8
LF:20
LH:17
end_of_record
SF:/workspace/packages/example/src/helper.js
FNF:2
FNH:2
BRF:4
BRH:3
LF:10
LH:9
end_of_record
"""


class CoverageParserTests(unittest.TestCase):
    def test_lcov_includes_line_branch_and_function_totals(self):
        coverage = parse_coverage_file("lcov.info", LCOV.encode())

        self.assertEqual(coverage["lines_pct"], 86.67)
        self.assertEqual((coverage["lines_hit"], coverage["lines_found"]), (26, 30))
        self.assertEqual(coverage["branches_pct"], 78.57)
        self.assertEqual((coverage["branches_hit"], coverage["branches_found"]), (11, 14))
        self.assertEqual(coverage["functions_pct"], 83.33)
        self.assertEqual((coverage["functions_hit"], coverage["functions_found"]), (5, 6))

    def test_sciencediscovery_artifact_is_coverage_only(self):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr("coverage/lcov.info", LCOV)
            archive.writestr("coverage/summary.json", json.dumps({
                "files": 2,
                "scope": "Built Node.js workspace tests",
                "totals": {"lines": {"covered": 26, "total": 30, "percentage": 86.67}},
            }))

        parsed = parse_artifact_zip("node-coverage-deadbeef", blob.getvalue())

        self.assertIsNone(parsed["summary"])
        self.assertIsNone(parsed["run_log"])
        self.assertEqual(len(parsed["coverage"]), 1)
        self.assertEqual(parsed["coverage"][0]["file"], "coverage/lcov.info")

    def test_ci_layer_summary_is_still_recognized(self):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr("summary.json", json.dumps({
                "layer": "ut",
                "status": "passed",
                "exitCode": 0,
                "outcomes": [{"command": "pnpm test", "exitCode": 0, "durationMs": 1200}],
            }))

        parsed = parse_artifact_zip("ut-results", blob.getvalue())

        self.assertEqual(parsed["summary"]["layer"], "ut")
        self.assertEqual(parsed["summary"]["status"], "passed")
        self.assertEqual(parsed["summary"]["outcomes"][0]["command"], "pnpm test")

    def test_schema_coverage_summary_is_not_a_ci_layer(self):
        blob = io.BytesIO()
        manifest = {
            "schema_version": 1,
            "mode": "full",
            "source_sha": "abc123",
            "groups": [{"name": "packages/example", "files": 1, "totals": {
                "lines": {"covered": 9, "total": 10, "percentage": 90},
                "branches": {"covered": 3, "total": 4, "percentage": 75},
                "functions": {"covered": 2, "total": 2, "percentage": 100},
            }}],
            "totals": {"lines": {"covered": 9, "total": 10, "percentage": 90}},
        }
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr("coverage/summary.json", json.dumps(manifest))
            archive.writestr("coverage/groups/packages-example/summary.json", json.dumps({
                **manifest, "groups": None, "group": "packages/example",
            }))

        parsed = parse_artifact_zip("node-coverage-summary-nightly-abc123", blob.getvalue())

        self.assertIsNone(parsed["summary"])
        self.assertEqual(parsed["coverage_manifest"]["source_sha"], "abc123")
        self.assertEqual(parsed["coverage_manifest"]["groups"][0]["name"], "packages/example")

    def test_default_branch_coverage_precedes_newer_pull_request_artifact(self):
        artifacts = [
            {"id": 2, "name": "node-coverage", "branch": "feature/newer", "created_at": "2026-09-21T11:00:00Z"},
            {"id": 1, "name": "node-coverage", "branch": "main", "created_at": "2026-09-21T10:00:00Z"},
        ]
        ctx = Context(gh=object(), cfg=Config(), now=datetime(2026, 9, 21), repo_meta={"default_branch": "main"})

        def load_artifact(_ctx, artifact, _notes):
            return {"coverage": [{"format": "lcov", "lines_pct": 80 + artifact["id"]}]}

        with patch("gsb.collectors._load_artifact", side_effect=load_artifact) as loader:
            result = _coverage_probe(ctx, artifacts, [], {}, [])

        self.assertEqual(loader.call_args.args[1]["id"], 1)
        self.assertEqual(result["value"]["lines_pct"], 81)

    def test_main_group_summaries_compose_over_latest_nightly_baseline(self):
        artifacts = [
            {"id": 3, "name": "node-coverage-summary-pr-17-prsha", "branch": "feature/x",
             "created_at": "2026-09-22T12:00:00Z"},
            {"id": 2, "name": "node-coverage-summary-main-incremental-mainsha", "branch": "main",
             "created_at": "2026-09-22T11:00:00Z"},
            {"id": 1, "name": "node-coverage-summary-nightly-base", "branch": "main",
             "created_at": "2026-09-22T03:30:00Z"},
        ]
        metric = lambda covered, total: {  # noqa: E731 - compact fixture
            "lines": {"covered": covered, "total": total, "percentage": covered * 100 / total},
            "branches": {"covered": covered, "total": total, "percentage": covered * 100 / total},
            "functions": {"covered": covered, "total": total, "percentage": covered * 100 / total},
        }
        manifests = {
            1: {"schema_version": 1, "source_sha": "base", "generated_at": "2026-09-22T03:30:00Z",
                "groups": [{"name": "packages/a", "files": 1, "totals": metric(5, 10)},
                           {"name": "packages/b", "files": 1, "totals": metric(8, 10)}],
                "totals": metric(13, 20)},
            2: {"schema_version": 1, "source_sha": "mainsha", "generated_at": "2026-09-22T11:00:00Z",
                "groups": [{"name": "packages/a", "files": 1, "totals": metric(9, 10)}],
                "totals": metric(9, 10)},
            3: {"schema_version": 1, "source_sha": "prsha", "generated_at": "2026-09-22T12:00:00Z",
                "groups": [{"name": "packages/b", "files": 1, "totals": metric(10, 10)}],
                "totals": metric(10, 10)},
        }
        ctx = Context(gh=object(), cfg=Config(), now=datetime(2026, 9, 22),
                      repo_meta={"default_branch": "main"})

        with patch("gsb.collectors._load_artifact",
                   side_effect=lambda _ctx, artifact, _notes: {"coverage_manifest": manifests[artifact["id"]]}):
            result = _coverage_probe(ctx, artifacts, [], {}, [])

        node = result["languages"]["node"]
        self.assertEqual(node["baseline"]["totals"]["lines"]["percentage"], 65)
        self.assertEqual(node["current"]["kind"], "incremental")
        self.assertEqual(node["current"]["totals"]["lines"]["percentage"], 85)
        self.assertEqual(node["pull_requests"][0]["number"], 17)
        self.assertEqual(node["pull_requests"][0]["groups"][0]["name"], "packages/b")

    def test_file_totals_follow_their_group_from_baseline_and_increments(self):
        artifacts = [
            {"id": 3, "name": "node-coverage-summary-pr-17-prsha", "branch": "feature/x", "created_at": "2026-09-22T12:00:00Z"},
            {"id": 2, "name": "node-coverage-summary-main-incremental-mainsha", "branch": "main", "created_at": "2026-09-22T11:00:00Z"},
            {"id": 1, "name": "node-coverage-summary-nightly-base", "branch": "main", "created_at": "2026-09-22T03:30:00Z"},
        ]
        lines = lambda covered, total: {"lines": {"covered": covered, "total": total}}  # noqa: E731 - compact fixture
        source = lambda path, covered: {"path": path, "totals": lines(covered, 10)}  # noqa: E731
        manifests = {
            1: {"schema_version": 1, "groups": [{"name": "packages/a", "totals": lines(5, 20)}, {"name": "packages/b", "totals": lines(8, 10)}],
                "sources": [source("packages/a/one.ts", 2), source("packages/a/gone.ts", 3), source("packages/b/two.ts", 8)]},
            2: {"schema_version": 1, "groups": [{"name": "packages/a", "totals": lines(9, 10)}], "sources": [source("packages/a/one.ts", 9)]},
            3: {"schema_version": 1, "groups": [{"name": "packages/b", "totals": lines(10, 10)}], "sources": [source("packages/b/two.ts", 10)]},
        }
        ctx = Context(gh=object(), cfg=Config(), now=datetime(2026, 9, 22), repo_meta={"default_branch": "main"})
        with patch("gsb.collectors._load_artifact",
                   side_effect=lambda _ctx, artifact, _notes: {"coverage_manifest": manifests[artifact["id"]]}):
            node = _coverage_probe(ctx, artifacts, [], {}, [])["languages"]["node"]
        # packages/a comes from the main increment (a removed file disappears); packages/b stays at
        # the baseline because the PR artifact never updates the default branch.
        self.assertEqual([(s["path"], s["totals"]["lines"]["covered"]) for s in node["current"]["sources"]],
                         [("packages/a/one.ts", 9), ("packages/b/two.ts", 8)])
        for manifest in manifests.values():
            manifest.pop("sources")
        with patch("gsb.collectors._load_artifact",
                   side_effect=lambda _ctx, artifact, _notes: {"coverage_manifest": manifests[artifact["id"]]}):
            self.assertEqual(_coverage_probe(ctx, artifacts, [], {}, [])["languages"]["node"]["current"]["sources"], [])

    def test_full_main_artifacts_are_authoritative_for_node_and_python(self):
        artifacts = [
            {"id": 2, "name": "python-coverage-summary-main-incremental-mainsha", "branch": "main",
             "created_at": "2026-09-22T07:31:00Z"},
            {"id": 1, "name": "node-coverage-summary-main-incremental-mainsha", "branch": "main",
             "created_at": "2026-09-22T07:30:00Z"},
        ]
        node_totals = {
            "lines": {"covered": 80, "total": 100, "percentage": 80},
            "branches": {"covered": 30, "total": 50, "percentage": 60},
            "functions": {"covered": 9, "total": 10, "percentage": 90},
        }
        python_totals = {
            "lines": {"covered": 60, "total": 100, "percentage": 60},
            "branches": {"covered": 20, "total": 50, "percentage": 40},
        }
        manifests = {
            1: {"schema_version": 1, "language": "node", "mode": "full", "authoritative": True,
                "source_sha": "mainsha", "groups": [{"name": "packages/example", "files": 1,
                                                        "totals": node_totals}], "totals": node_totals},
            2: {"schema_version": 1, "language": "python", "mode": "full", "authoritative": True,
                "source_sha": "mainsha", "groups": [{"name": "services/example", "files": 1,
                                                        "totals": python_totals}], "totals": python_totals},
        }
        ctx = Context(gh=object(), cfg=Config(), now=datetime(2026, 9, 22),
                      repo_meta={"default_branch": "main"})

        with patch("gsb.collectors._load_artifact",
                   side_effect=lambda _ctx, artifact, _notes: {"coverage_manifest": manifests[artifact["id"]]}):
            result = _coverage_probe(ctx, artifacts, [], {}, [])

        self.assertEqual(result["current"]["kind"], "authoritative")
        self.assertEqual(result["value"]["lines_pct"], 70)
        self.assertEqual(result["value"]["branches_pct"], 50)
        self.assertEqual(result["value"]["functions_pct"], 90)
        self.assertEqual(result["languages"]["node"]["baseline"]["kind"], "main full")
        self.assertEqual(result["languages"]["python"]["baseline"]["kind"], "main full")

    def test_complete_gate_push_is_authoritative_without_legacy_metadata(self):
        artifacts = [
            {"id": 2, "name": "python-coverage-summary-push-mainsha", "branch": "main",
             "sha": "mainsha", "created_at": "2026-09-23T03:00:00Z"},
            {"id": 1, "name": "node-coverage-summary-push-mainsha", "branch": "main",
             "sha": "mainsha", "created_at": "2026-09-23T02:59:00Z"},
        ]
        layers = {
            "ut": {"complete": True, "producer": "success"},
            "st": {"complete": True, "producer": "success"},
        }
        node_totals = {
            "lines": {"covered": 84, "total": 100, "percentage": 84},
            "branches": {"covered": 64, "total": 100, "percentage": 64},
            "functions": {"covered": 8, "total": 10, "percentage": 80},
        }
        python_totals = {
            "lines": {"covered": 66, "total": 100, "percentage": 66},
            "branches": {"covered": 49, "total": 100, "percentage": 49},
        }
        manifests = {
            1: {"schema_version": 1, "layers": layers,
                "groups": [{"name": "packages/example", "files": 1, "totals": node_totals}],
                "totals": node_totals},
            2: {"schema_version": 1, "language": "python", "layers": layers,
                "groups": [{"name": "services/example", "files": 1, "totals": python_totals}],
                "totals": python_totals},
        }
        ctx = Context(gh=object(), cfg=Config(), now=datetime(2026, 9, 23),
                      repo_meta={"default_branch": "main"})

        with patch("gsb.collectors._load_artifact",
                   side_effect=lambda _ctx, artifact, _notes: {"coverage_manifest": manifests[artifact["id"]]}):
            result = _coverage_probe(ctx, artifacts, [], {}, [])

        self.assertEqual(result["current"]["kind"], "authoritative")
        self.assertEqual(result["languages"]["node"]["baseline"]["artifact"], artifacts[1]["name"])
        self.assertEqual(result["languages"]["node"]["baseline"]["sha"], "mainsha")
        self.assertEqual(result["languages"]["python"]["current"]["groups"][0]["source_sha"], "mainsha")

    def test_complete_dispatch_gate_is_the_baseline_of_a_branch_without_push_ci(self):
        artifacts = [
            {"id": 1, "name": "node-coverage-summary-workflow_dispatch-swarmsha", "branch": "feat/swarm",
             "sha": "swarmsha", "created_at": "2026-09-23T03:00:00Z"},
        ]
        complete = {"ut": {"complete": True, "producer": "success"}, "st": {"complete": True, "producer": "success"}}
        totals = {"lines": {"covered": 7, "total": 10, "percentage": 70}}
        manifest = {"schema_version": 1, "layers": complete, "totals": totals,
                    "groups": [{"name": "packages/example", "files": 1, "totals": totals}]}
        ctx = Context(gh=object(), cfg=Config(), now=datetime(2026, 9, 23), repo_meta={"default_branch": "feat/swarm"})
        with patch("gsb.collectors._load_artifact", side_effect=lambda *_: {"coverage_manifest": manifest}):
            result = _coverage_probe(ctx, artifacts, [], {}, [])
        self.assertEqual(result["current"]["kind"], "authoritative")
        self.assertEqual(len(result["languages"]["node"]["history"]), 1)
        # An incomplete dispatch is not a baseline.
        manifest["layers"] = {**complete, "st": {"complete": False, "producer": "failure"}}
        with patch("gsb.collectors._load_artifact", side_effect=lambda *_: {"coverage_manifest": manifest}):
            result = _coverage_probe(ctx, artifacts, [], {}, [])
        self.assertEqual(result["current"]["kind"], "partial")

    def test_incomplete_gate_push_does_not_replace_last_successful_main_result(self):
        artifacts = [
            {"id": 2, "name": "node-coverage-summary-push-newsha", "branch": "main",
             "sha": "newsha", "created_at": "2026-09-23T04:00:00Z"},
            {"id": 1, "name": "node-coverage-summary-push-goodsha", "branch": "main",
             "sha": "goodsha", "created_at": "2026-09-23T03:00:00Z"},
        ]
        metric = lambda covered: {  # noqa: E731 - compact fixture
            "lines": {"covered": covered, "total": 100, "percentage": covered},
            "branches": {"covered": covered, "total": 100, "percentage": covered},
            "functions": {"covered": covered, "total": 100, "percentage": covered},
        }
        manifests = {
            1: {"schema_version": 1,
                "layers": {"ut": {"complete": True, "producer": "success"},
                           "st": {"complete": True, "producer": "success"}},
                "groups": [{"name": "packages/example", "files": 1, "totals": metric(80)}],
                "totals": metric(80)},
            2: {"schema_version": 1,
                "layers": {"ut": {"complete": False, "producer": "failure"},
                           "st": {"complete": True, "producer": "success"}},
                "groups": [{"name": "packages/example", "files": 1, "totals": metric(10)}],
                "totals": metric(10)},
        }
        ctx = Context(gh=object(), cfg=Config(), now=datetime(2026, 9, 23),
                      repo_meta={"default_branch": "main"})

        with patch("gsb.collectors._load_artifact",
                   side_effect=lambda _ctx, artifact, _notes: {"coverage_manifest": manifests[artifact["id"]]}):
            result = _coverage_probe(ctx, artifacts, [], {}, [])

        node = result["languages"]["node"]
        self.assertEqual(node["baseline"]["artifact"], "node-coverage-summary-push-goodsha")
        self.assertEqual(node["current"]["totals"]["lines"]["percentage"], 80)

    def test_multiple_pull_requests_are_listed_without_replacing_main(self):
        artifacts = [
            {"id": 4, "name": "node-coverage-summary-pr-18-pr18sha", "branch": "feature/18",
             "sha": "pr18sha", "created_at": "2026-09-23T05:00:00Z"},
            {"id": 3, "name": "node-coverage-summary-pr-17-pr17new", "branch": "feature/17",
             "sha": "pr17new", "created_at": "2026-09-23T04:00:00Z"},
            {"id": 2, "name": "node-coverage-summary-pr-17-pr17old", "branch": "feature/17",
             "sha": "pr17old", "created_at": "2026-09-23T03:00:00Z"},
            {"id": 1, "name": "node-coverage-summary-push-mainsha", "branch": "main",
             "sha": "mainsha", "created_at": "2026-09-23T02:00:00Z"},
        ]
        metric = lambda covered: {  # noqa: E731 - compact fixture
            "lines": {"covered": covered, "total": 100, "percentage": covered},
            "branches": {"covered": covered, "total": 100, "percentage": covered},
            "functions": {"covered": covered, "total": 100, "percentage": covered},
        }
        complete = {"ut": {"complete": True, "producer": "success"},
                    "st": {"complete": True, "producer": "success"}}
        manifests = {
            artifact["id"]: {"schema_version": 1, "layers": complete,
                              "groups": [{"name": "packages/example", "files": 1,
                                          "totals": metric({1: 80, 2: 70, 3: 75, 4: 90}[artifact["id"]])}],
                              "totals": metric({1: 80, 2: 70, 3: 75, 4: 90}[artifact["id"]])}
            for artifact in artifacts
        }
        ctx = Context(gh=object(), cfg=Config(), now=datetime(2026, 9, 23),
                      repo_meta={"default_branch": "main"})

        with patch("gsb.collectors._load_artifact",
                   side_effect=lambda _ctx, artifact, _notes: {"coverage_manifest": manifests[artifact["id"]]}):
            result = _coverage_probe(ctx, artifacts, [], {}, [])

        node = result["languages"]["node"]
        self.assertEqual(node["current"]["totals"]["lines"]["percentage"], 80)
        prs = {row["number"]: row for row in node["pull_requests"]}
        self.assertEqual(set(prs), {17, 18})
        self.assertEqual(prs[17]["sha"], "pr17new")
        self.assertEqual(prs[17]["totals"]["lines"]["percentage"], 75)


if __name__ == "__main__":
    unittest.main()
