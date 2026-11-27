import datetime as dt
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from production_sfs_freshness import collection_due, reply_collection_due


class SfsFreshnessTest(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 14, 4, 40, tzinfo=dt.timezone.utc)

    def snapshot(self, stamp, schema='digitalmate/trajectory-archive@1'):
        return {'schema': schema, 'generated_at': stamp,
                'coverage': {'bindings': 100, 'authorized_workspaces': 95,
                             'discovered_databases': 100, 'observed_databases': 95}}

    def test_same_hour_success_skips_redundant_sfs_read(self):
        self.assertFalse(collection_due(self.snapshot('2026-11-14T12:00:00+08:00'), now=self.now))
        self.assertTrue(collection_due(self.snapshot('2026-11-14T03:59:30Z'), now=self.now))

    def test_missing_invalid_future_or_other_source_retries(self):
        for snapshot in (None, {}, self.snapshot('invalid'), self.snapshot('2026-11-14T04:20:00'),
                         self.snapshot('2026-11-14T04:41:00Z'), self.snapshot('2026-11-14T04:30:00Z', 'other')):
            with self.subTest(snapshot=snapshot):
                self.assertTrue(collection_due(snapshot, now=self.now))

    def test_incomplete_coverage_retries_even_if_recent(self):
        for bindings, authorized, observed in ((100, 94, 90), (0, 0, 0), (100, 101, 95),
                                               (None, None, None), (100, 100, 0)):
            snapshot = self.snapshot('2026-11-14T04:20:00Z')
            snapshot['coverage'].update(bindings=bindings, authorized_workspaces=authorized, observed_databases=observed)
            with self.subTest(coverage=snapshot['coverage']):
                self.assertTrue(collection_due(snapshot, now=self.now))

    def test_cli_returns_true_for_missing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name('production_sfs_freshness.py')),
                 str(Path(directory) / 'missing.json')],
                check=True, capture_output=True, text=True,
            )
        self.assertEqual(result.stdout.strip(), 'true')

    def test_fresh_archive_does_not_hide_missing_or_failed_current_reply_read(self):
        metrics = {'schema': 'digitalmate/env-request-latency-daily@1', 'env': 'adx-jiuwen'}
        self.assertTrue(reply_collection_due(metrics, now=self.now))
        replies = {'schema': 'digitalmate/cloud-reply-daily@1', 'observed_at': '2026-11-14T04:20:00Z',
                   'coverage': {'expected_nodes': 6, 'read_nodes': 6}}
        metrics['cloud_reply_daily'] = replies
        self.assertFalse(reply_collection_due(metrics, now=self.now))
        replies['coverage']['read_nodes'] = 5
        self.assertTrue(reply_collection_due(metrics, now=self.now))
        replies['coverage']['read_nodes'] = 6
        replies['observed_at'] = '2026-11-14T03:59:59Z'
        self.assertTrue(reply_collection_due(metrics, now=self.now))


if __name__ == '__main__':
    unittest.main()
