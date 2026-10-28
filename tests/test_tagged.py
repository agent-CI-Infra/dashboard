from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from gsb.incremental_project import build_snapshot
from gsb.reports import parse_report_zip
from gsb.sync import Sync, stamp
from gsb.tagged import TaggedStore, build_view, extract, instances, matches, merge_rules, normalize, parse_selector, rules
from test_incremental import REPO, Source

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
# Mirrors the source repository's closed vocabulary (test/support/tagged/schema.json).
SCHEMA = {"version": 1, "groups": {
    "category": {"multiple": False, "values": ["ut", "st", "e2e"]},
    "os": {"multiple": True, "values": ["linux", "macos", "windows"]},
    "arch": {"multiple": True, "values": ["amd64", "arm64"]},
    "npu": {"multiple": False, "values": ["none", "required"], "default": "none"},
    "model": {"multiple": False, "values": ["none", "mock", "real"], "default": "none"},
    "judge": {"multiple": False, "values": ["none", "llm"], "default": "none"},
    "status": {"multiple": False, "values": ["reviewed", "external", "legacy", "unreviewed"], "default": "reviewed"},
    "sandbox": {"multiple": False, "values": ["none", "bubblewrap", "seatbelt"], "default": "none"}}}
GROUPS = {name: {"multiple": rule["multiple"], "values": rule["values"], "default": rule.get("default")}
          for name, rule in SCHEMA["groups"].items()}
POLICY = ("(category:ut or category:st or category:e2e) and os:linux and arch:amd64 and npu:none "
          "and (model:none or model:mock) and judge:none and status:reviewed")
LINUX = [{"os": "linux", "arch": "amd64"}]


def case(ident, *tags, source=None):
    return {"id": ident, "source": source or ident.split("::")[0], "sourceHash": "0" * 64, "runner": "node", "tags": list(tags)}


# One catalog per slice, as each CI job collects only its own sources.
CATALOG = {
    "ut": [case("a.test.ts::one", "category:ut", "os:linux", "os:macos", "arch:amd64", "arch:arm64"),
           case("a.test.ts::two", "category:ut", "os:linux", "arch:amd64", "sandbox:bubblewrap"),
           case("b.py::legacy", "category:ut", "os:linux", "arch:amd64", "status:legacy"),
           case("mac.test.ts::only", "category:ut", "os:macos", "arch:arm64", "sandbox:seatbelt"),
           case("command:real", "category:st", "os:linux", "arch:amd64", "model:real")],
    "st": [case("s.test.ts::mock", "category:st", "os:linux", "arch:amd64", "model:mock"),
           case("command:real", "category:st", "os:linux", "arch:amd64", "model:real")],
    "e2e": [case("playwright:j.spec.ts::journey", "category:e2e", "os:linux", "arch:amd64", "model:mock"),
            case("command:real", "category:st", "os:linux", "arch:amd64", "model:real")],
}
PLANNED = {"ut": 2, "st": 1, "e2e": 1}


def artifact(part, profile="pr", planned=None, extra=None):
    """A results zip laid out like the source CI's ``.ci-results``: every
    profile under the slice's own directory, the profile inside plan.json."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        base = f"{part}/tagged/"
        archive.writestr(base + "catalog.json", json.dumps(CATALOG[part]))
        archive.writestr(base + "plan.json", json.dumps({
            "version": 1, "revision": "c" * 40, "selector": f"{POLICY} and (category:{part})", "targets": LINUX, "profile": profile,
            "entries": [{"key": f"x{i}@linux/amd64"} for i in range(PLANNED[part] if planned is None else planned)]}))
        archive.writestr(base + "summary.json", json.dumps({"status": "PASS", "planned": PLANNED[part], "executed": PLANNED[part],
                                                            "passed": PLANNED[part], "failed": 0, "skipped": 0}))
        archive.writestr(base + "group-1/plan.json", json.dumps({"entries": []}))
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def slices(profile="pr", planned=None):
    out = []
    for part in ("ut", "st", "e2e"):
        with zipfile.ZipFile(io.BytesIO(artifact(part, profile, planned))) as archive:
            out.extend(extract(archive))
    return out


def store(*runs):
    tagged = TaggedStore()
    for run, found in runs:
        tagged.observe(run, found, "main")
    tagged.refresh_schema(lambda repo, path, ref: json.dumps(SCHEMA), REPO)
    return tagged


def meta(ident, branch="main", event="push", created="2026-09-22T10:00:00Z"):
    return dict(id=ident, attempt=1, url=f"https://github.com/{REPO}/actions/runs/{ident}", created_at=created, branch=branch, event=event)


class ExtractTests(unittest.TestCase):
    def test_reads_each_profile_slice_and_skips_queries_and_subplans(self):
        with zipfile.ZipFile(io.BytesIO(artifact("e2e", "daily", extra={"query/tagged/plan.json": "{}"}))) as archive:
            found = extract(archive)
        self.assertEqual([(s["profile"], s["slice"], s["planned"]) for s in found], [("daily", "e2e", 1)])
        # Only the current layout counts: the profile comes from the plan, not the directory.
        with zipfile.ZipFile(io.BytesIO(artifact("ut", None))) as archive:
            self.assertEqual(extract(archive), [])
        old = io.BytesIO()
        with zipfile.ZipFile(old, "w") as archive, zipfile.ZipFile(io.BytesIO(artifact("ut", None))) as source:
            for name in source.namelist():
                archive.writestr("daily-" + name, source.read(name))
        with zipfile.ZipFile(old) as archive:
            self.assertEqual(extract(archive), [])
        self.assertEqual(found[0]["result"]["passed"], 1)
        self.assertEqual(found[0]["targets"], LINUX)
        # Catalog rows are [id, signature, source]; equal tags share one signature.
        self.assertEqual(len(found[0]["cases"]), 2)
        self.assertNotIn("sourceHash", json.dumps(found[0]))
        with zipfile.ZipFile(io.BytesIO(artifact("ut"))) as archive:
            self.assertEqual(extract(archive)[0]["profile"], "pr")

    def test_harness_summary_counts_tests_but_playwright_report_wins(self):
        parsed = parse_report_zip(artifact("ut"))
        self.assertEqual((parsed["format"], parsed["tests"], parsed["passed"], parsed["failed"]), ("tagged", 2, 2, 0))
        self.assertEqual(parsed["tagged"][0]["slice"], "ut")
        report = {"suites": [{"specs": [{"title": "t", "file": "j.spec.ts", "tests": [{"status": "expected", "results": [{"status": "passed"}]}]}]}]}
        parsed = parse_report_zip(artifact("e2e", extra={"e2e/test-results/results.json": json.dumps(report)}))
        self.assertEqual(parsed["format"], "playwright")
        self.assertEqual(len(parsed["tagged"]), 1)


class SelectionTests(unittest.TestCase):
    def test_defaults_come_from_the_schema(self):
        self.assertEqual(normalize(["category:st", "os:linux", "arch:amd64", "model:real"], GROUPS),
                         ("arch:amd64", "category:st", "judge:none", "model:real", "npu:none", "os:linux", "sandbox:none", "status:reviewed"))
        self.assertEqual(normalize(["category:ut"], None), ("category:ut",))

    def test_selector_grammar_matches_the_harness(self):
        tree = parse_selector("category:ut or category:st and not model:real")
        self.assertTrue(matches(tree, {"category:ut", "model:real"}))  # and binds tighter than or
        self.assertFalse(matches(tree, {"category:st", "model:real"}))
        self.assertTrue(matches(parse_selector(""), set()))
        for bad in ("category:ut and", "(category:ut", "category:ut)", "category:ut xor os:linux", "CATEGORY:ut"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_selector(bad)

    def test_platform_tags_expand_into_target_instances(self):
        tags = ("arch:amd64", "category:ut", "os:linux", "os:macos")
        targets = [{"os": "linux", "arch": "amd64"}, {"os": "macos", "arch": "amd64"}]
        tree = parse_selector("not os:linux")
        self.assertEqual([sorted(i) for i in instances(tags, targets) if matches(tree, i)], [["arch:amd64", "category:ut", "os:macos"]])
        self.assertEqual(list(instances(("category:ut", "os:macos", "arch:arm64"), LINUX)), [])

    def test_slice_selectors_fold_back_into_the_profile_rule(self):
        rows = [row for part in ("ut", "st", "e2e") for row in rules(parse_selector(f"{POLICY} and (category:{part})"))]
        merged = merge_rules(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["category"], {"ut", "st", "e2e"})
        self.assertEqual(merged[0]["model"], {"none", "mock"})
        self.assertIsNone(rules(parse_selector("category:ut and not os:linux")))


class ViewTests(unittest.TestCase):
    def test_dimensions_profiles_and_never_covered_cases(self):
        view = store((meta(1), slices()), (meta(2, created="2026-09-22T11:00:00Z", event="schedule"), slices("daily"))).view("main")
        # command:real is collected by every slice but counted once.
        self.assertEqual(view["cases"], 7)
        profiles = {p["name"]: p for p in view["profiles"]}
        self.assertEqual(profiles["pr"]["covered"], 4)
        self.assertEqual(profiles["daily"]["same_as"], "pr")
        self.assertIsNone(profiles["release"]["run"])
        self.assertEqual(profiles["pr"]["rules"], [[["category", ["ut", "st", "e2e"]], ["os", ["linux"]], ["arch", ["amd64"]], ["npu", ["none"]],
                                                  ["model", ["none", "mock"]], ["judge", ["none"]], ["status", ["reviewed"]]]])
        self.assertEqual((view["covered"], view["uncovered"]["cases"]), (4, 3))
        self.assertEqual({r["reason"]: r["cases"] for r in view["uncovered"]["reasons"]},
                         {"status=legacy": 1, "os=macos；arch=arm64": 1, "model=real": 1})
        self.assertEqual(sorted(i["id"] for i in view["uncovered"]["items"]), ["b.py::legacy", "command:real", "mac.test.ts::only"])
        dims = {d["group"]: d for d in view["dimensions"]}
        self.assertEqual((dims["status"]["vocabulary"], dims["status"]["used"]), (4, 2))
        values = {v["value"]: v for v in dims["os"]["values"]}
        self.assertEqual((values["linux"]["cases"], values["macos"]["cases"], values["windows"]["cases"]), (6, 2, 0))
        # A multi-platform case counts as covered through its Linux instance.
        self.assertEqual(values["macos"]["covered"], 1)
        self.assertEqual((values["linux"]["profiles"]["pr"], values["macos"]["profiles"]["pr"]), ("yes", "no"))
        sandbox = {v["value"]: v["profiles"]["pr"] for v in dims["sandbox"]["values"]}
        self.assertEqual(sandbox, {"none": "any", "bubblewrap": "any", "seatbelt": "any"})
        self.assertEqual({c["slice"]: (c["planned"], c["computed"]) for c in view["checks"]}, {"ut": (2, 2), "st": (1, 1), "e2e": (1, 1)})
        self.assertEqual(sum(c["cases"] for c in view["combinations"]), 7)

    def test_drift_from_the_frozen_plan_is_reported(self):
        view = store((meta(1), slices(planned=9))).view("main")
        self.assertIn({"slice": "ut", "planned": 9, "computed": 2}, view["checks"])

    def test_no_catalog_means_no_view(self):
        self.assertIsNone(build_view({"profiles": {}, "catalog": None}))


class StoreTests(unittest.TestCase):
    def test_only_default_branch_runs_and_release_tags_define_profiles(self):
        tagged = store((meta(1, branch="feature", event="pull_request", created="2026-09-22T11:59:00Z"), slices()),
                       (meta(2, branch="main", event="push"), slices()),
                       (meta(3, branch="0.3.0", event="push"), slices("release")))
        self.assertEqual(tagged.state["lines"]["main"]["profiles"]["pr"]["run"]["id"], 2)
        self.assertEqual(tagged.state["lines"]["main"]["profiles"]["release"]["run"]["id"], 3)
        self.assertEqual(tagged.state["lines"]["main"]["catalog"]["run"]["id"], 2)

    def test_each_branch_line_keeps_its_own_profiles_and_catalog(self):
        tagged = TaggedStore()
        refs = ("main", "feat/swarm")
        tagged.observe(meta(1), slices(), "main", refs)
        tagged.observe(meta(2, branch="feat/swarm", event="workflow_dispatch", created="2026-09-22T11:00:00Z"), slices(), "main", refs)
        # A PR into the line may edit the policy it runs; an unlisted branch has no line.
        tagged.observe(meta(3, branch="swarm-fix", event="pull_request", created="2026-09-22T11:30:00Z"), slices(), "main", refs)
        tagged.observe(meta(4, branch="topic", created="2026-09-22T11:40:00Z"), slices(), "main", refs)
        tagged.observe(meta(5, branch="0.3.0", created="2026-09-22T11:50:00Z"), slices("release"), "main", refs)
        lines = tagged.state["lines"]
        self.assertEqual(sorted(lines), ["feat/swarm", "main"])
        self.assertEqual(lines["main"]["catalog"]["run"]["id"], 1)
        self.assertEqual(lines["feat/swarm"]["catalog"]["run"]["id"], 2)
        self.assertEqual(sorted(lines["main"]["profiles"]), ["pr", "release"])
        self.assertEqual(sorted(lines["feat/swarm"]["profiles"]), ["pr"])
        tagged.refresh_schema(lambda repo, path, ref: json.dumps(SCHEMA), REPO)
        self.assertEqual(tagged.view("feat/swarm")["catalog"]["run"]["id"], 2)
        self.assertIsNone(tagged.view("topic"))

    def test_single_branch_state_becomes_the_default_line(self):
        legacy = store((meta(1), slices()), (meta(2, branch="0.3.0", created="2026-09-22T11:00:00Z"), slices("release"))).state["lines"]["main"]
        tagged = TaggedStore({"version": 1, **legacy})
        self.assertTrue(tagged.changed)
        self.assertEqual(tagged.state, {"version": 2, "lines": {"main": legacy}})
        self.assertEqual(tagged.view("main")["cases"], store((meta(1), slices())).view("main")["cases"])

    def test_newer_run_replaces_and_same_run_adds_missing_slices(self):
        tagged = TaggedStore()
        tagged.observe(meta(5, created="2026-09-22T11:00:00Z"), slices()[:1], "main")
        tagged.observe(meta(4, created="2026-09-22T10:00:00Z"), slices(), "main")  # older: ignored
        self.assertEqual(sorted(tagged.state["lines"]["main"]["catalog"]["slices"]), ["ut"])
        tagged.observe(meta(5, created="2026-09-22T11:00:00Z"), slices()[1:], "main")
        self.assertEqual(sorted(tagged.state["lines"]["main"]["catalog"]["slices"]), ["e2e", "st", "ut"])
        self.assertEqual(tagged.state["lines"]["main"]["catalog"]["run"]["id"], 5)
        tagged.changed = False
        tagged.observe(meta(5, created="2026-09-22T11:00:00Z"), slices(), "main")
        self.assertFalse(tagged.changed)

    def test_schema_is_read_once_per_revision_and_kept_on_failure(self):
        calls = []
        tagged = store((meta(1), slices()))
        tagged.refresh_schema(lambda *a: calls.append(a) or "{}", REPO)
        self.assertEqual(calls, [])
        tagged.state["lines"]["main"]["schema"]["revision"] = "old"
        tagged.refresh_schema(lambda *a: "not json", REPO)
        self.assertEqual(tagged.state["lines"]["main"]["schema"]["revision"], "old")


class SyncTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_results_artifact_feeds_sync_state_and_snapshot(self):
        blobs = {100 + i: artifact(part) for i, part in enumerate(("ut", "st", "e2e"))}

        class Tagged(Source):
            def get(self, path, params=None):
                if path.endswith("/artifacts"):
                    return {"artifacts": [dict(id=ident, name=f"{part}-results", size_in_bytes=len(blobs[ident]),
                                               created_at=stamp(NOW), expired=False)
                                          for ident, part in zip(blobs, ("ut", "st", "e2e"))]}
                return super().get(path, params)

            def download_artifact(self, repo, ident, max_bytes=None):
                return blobs[ident]

            def get_text_file(self, repo, path, ref=None):
                self.calls.append((path, {"ref": ref}))
                return json.dumps(SCHEMA)

        gh = Tagged(runs=1)
        gh.runs[0].update(name="CI", created_at=stamp(NOW - timedelta(minutes=5)))
        sync = Sync(gh, self.root, REPO, now=NOW).collect()
        record = sync.history.get("runs", "1-1")
        self.assertNotIn("tagged", record)
        self.assertEqual({t["name"]: t["counts"]["tests"] for t in record["tests"]}, {"ut-results": 2, "st-results": 1, "e2e-results": 1})
        files = sync.files()
        self.assertIn(".sync/tagged.json", files)
        self.assertIn(("test/support/tagged/schema.json", {"ref": "c" * 40}), gh.calls)
        with patch("gsb.incremental_project.public_ops", return_value={}), patch("gsb.incremental_project.public_tests", return_value={}):
            view = build_snapshot(sync)["sections"]["tests"]["data"]["tagged"]
        self.assertEqual((view["cases"], view["covered"]), (7, 4))
        self.assertLess(len(files[".sync/tagged.json"]), 20000)


if __name__ == "__main__":
    unittest.main()
