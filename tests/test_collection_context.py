import json
import unittest
from unittest.mock import patch

import collection_context as context_module
from collection_context import collection_context


class CollectionContextTests(unittest.TestCase):
    def setUp(self):
        self.deployments = json.loads((context_module.ROOT / "board-config.json").read_text())["deployments"]
        self.assertEqual(len(self.deployments), 1, "each dashboard must configure exactly one source")
        self.source, self.deployment = next(iter(self.deployments.items()))
        self.target = self.deployment["repository"]

    def test_this_dashboard_chooses_its_own_source(self):
        result = collection_context(self.target.upper(), self.source.upper(), "refresh-17")
        self.assertEqual(result["source"], self.source)
        self.assertEqual(result["target"], self.target)
        self.assertEqual(result["source_owner"], self.source.split("/")[0])
        self.assertEqual(result["target_name"], self.target.split("/")[1])

    def test_cross_site_and_unknown_repository_are_rejected(self):
        other_source, other_target = "example/other-source", "example/other-dashboard"
        for destination, source in [(self.target, other_source), (other_target, ""), ("evil/dashboard", "")]:
            with self.subTest(destination=destination, source=source), self.assertRaises(ValueError):
                collection_context(destination, source)

    def test_request_id_cannot_inject_workflow_outputs(self):
        for request_id in ["refresh-1\nsource=other/repo", "$(curl example.org)", "x" * 101]:
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                collection_context(self.target, request_id=request_id)

    def test_ambiguous_destination_is_rejected(self):
        content = json.dumps({"deployments": {
            "example/first": {"repository": self.target},
            "example/second": {"repository": self.target},
        }})
        with patch("pathlib.Path.read_text", return_value=content), self.assertRaises(ValueError):
            collection_context(self.target)
