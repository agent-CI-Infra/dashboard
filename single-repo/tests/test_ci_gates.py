import io
import json
import zipfile
import unittest

from gsb.ci_gates import parse_artifact, summary


def report(lane="pr-ready", verdict="pass"):
    return {
        "schema": "ci-gates/gate-report@1",
        "repository": {"owner": "acme", "name": "app"},
        "event": "pull_request",
        "sha": "a" * 40,
        "branch": "main",
        "lane": lane,
        "level": "blocking",
        "verdict": verdict,
        "started_at": "2026-10-22T01:00:00Z",
        "finished_at": "2026-10-22T01:00:01Z",
        "results": [
            {"name": "npm run build", "status": "pass" if verdict == "pass" else "fail", "exitCode": 0 if verdict == "pass" else 1, "durationMs": 12},
            {"name": "./scripts/secret.sh", "status": "not_run", "exitCode": None, "durationMs": None},
        ],
        "cases": {"catalog": {"total": 2, "ready": 1, "todo": 1, "skipped": 0}},
        "onboarding": {"stage": "cases-pending"},
    }


class CiGatesTest(unittest.TestCase):
    def zip(self, docs):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            for index, doc in enumerate(docs):
                archive.writestr(f"lane-{index}.json" if index else "report.json", json.dumps(doc))
            archive.writestr("../escape.json", "{}")
        return out.getvalue()

    def test_parses_lanes_and_catalog(self):
        parsed = parse_artifact(self.zip([report(), report("build", "fail")]))
        self.assertEqual([lane["lane"] for lane in parsed["lanes"]], ["build", "pr-ready"])
        self.assertEqual(parsed["verdict"], "fail")
        self.assertEqual(parsed["cases"], {"total": 2, "ready": 1, "todo": 1, "skipped": 0})
        self.assertEqual(parsed["stage"], "cases-pending")
        self.assertEqual(parsed["lanes"][1]["results"][0]["name"], "npm run build")

    def test_ignores_traversal_unknown_schema_and_bad_files(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("../escape.json", json.dumps(report()))
            archive.writestr("other.json", json.dumps({"schema": "other"}))
            archive.writestr("broken.json", "{")
        self.assertIsNone(parse_artifact(out.getvalue()))

    def test_summary_uses_latest_gate_run(self):
        older = {"created_at": "2026-10-21T00:00:00Z", "gates": {"verdict": "pass", "cases": {}, "lanes": [{"lane": "build", "verdict": "pass", "results": []}]}}
        newer = {"created_at": "2026-10-22T00:00:00Z", "gates": {"verdict": "fail", "cases": {}, "lanes": [{"lane": "build", "verdict": "fail", "results": []}]}}
        out = summary([older, newer])
        self.assertEqual(out["verdict"], "fail")
        self.assertEqual(out["run"]["created_at"], "2026-10-22T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
