import datetime as dt
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from production_sfs_freshness import collection_due


class SfsFreshnessTest(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 14, 4, 0, tzinfo=dt.timezone.utc)

    def snapshot(self, stamp, source='production_sfs_ssh'):
        return {'cloud_trace_collection': {'collection_source': source, 'generated_at': stamp,
                                           'binding_count': 100, 'read_log_count': 95}}

    def test_recent_success_skips_redundant_sfs_read(self):
        self.assertFalse(collection_due(self.snapshot('2026-11-14T03:11:00Z'), now=self.now))
        self.assertTrue(collection_due(self.snapshot('2026-11-14T03:10:00Z'), now=self.now))

    def test_missing_invalid_future_or_other_source_retries(self):
        for snapshot in (None, {}, self.snapshot('invalid'), self.snapshot('2026-11-14T03:50:00'),
                         self.snapshot('2026-11-14T04:01:00Z'),
                         self.snapshot('2026-11-14T03:59:00Z', 'kubernetes_shared_workspace')):
            with self.subTest(snapshot=snapshot):
                self.assertTrue(collection_due(snapshot, now=self.now))

    def test_incomplete_coverage_retries_even_if_recent(self):
        for count, read in ((100, 94), (0, 0), (100, 101), (None, None)):
            snapshot = self.snapshot('2026-11-14T03:59:00Z')
            snapshot['cloud_trace_collection'].update(binding_count=count, read_log_count=read)
            with self.subTest(count=count, read=read):
                self.assertTrue(collection_due(snapshot, now=self.now))

    def test_cli_returns_true_for_missing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name('production_sfs_freshness.py')),
                 str(Path(directory) / 'missing.json')],
                check=True, capture_output=True, text=True,
            )
        self.assertEqual(result.stdout.strip(), 'true')


if __name__ == '__main__':
    unittest.main()
