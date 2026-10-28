import json
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import setup


class SetupConfigTests(unittest.TestCase):
    def test_defaults_and_deployment(self):
        config = setup.build_config("owner/source", "owner/board", {})
        self.assertEqual(config["review_sla_days"], 3)
        self.assertEqual(config["stale_days"], 30)
        self.assertEqual(config["deployments"], {
            "owner/source": {
                "environment": "production",
                "label": "source",
                "repository": "owner/board",
            }
        })
        self.assertEqual(config["branch_lines"], [])

    def test_custom_json_is_validated_and_kept(self):
        raw = {
            "label": "service",
            "review_sla_days": 7,
            "stale_days": 14,
            "workflows": {"gate": ["^CI$", "CI Gates"], "daily": ["nightly"], "release": ["release"]},
            "branch_lines": [{"key": "main", "ref": "main"}],
        }
        config = setup.build_config("owner/source", "owner/board", raw)
        self.assertEqual(config["review_sla_days"], 7)
        self.assertEqual(config["workflows"]["gate"], ["^CI$", "CI Gates"])
        self.assertEqual(config["branch_lines"], [{"key": "main", "ref": "main"}])
        self.assertEqual(config["deployments"]["owner/source"]["label"], "service")

    def test_rejects_unknown_keys_and_bad_regex(self):
        with self.assertRaises(SystemExit):
            setup.build_config("owner/source", "owner/board", {"unknown": 1})
        with self.assertRaises(SystemExit):
            setup.build_config("owner/source", "owner/board", {"workflows": {"gate": ["["]}})

    def test_rejects_same_source_and_board(self):
        with self.assertRaises(SystemExit):
            setup.build_config("owner/same", "owner/same", {})

    def test_rejects_invalid_branch_line(self):
        with self.assertRaises(SystemExit):
            setup.build_config("owner/source", "owner/board", {"branch_lines": [{"key": "Bad Key", "ref": "main"}]})


if __name__ == "__main__":
    unittest.main()
