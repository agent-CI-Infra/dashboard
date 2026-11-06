from copy import deepcopy
from datetime import timedelta
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gsb.run_changes import BATCH_SIZE, load_changes, write_receipt
from gsb.sync import Sync, stamp
from gsb.github import GitHubError
from test_incremental import Source, NOW, REPO, run

ROOT = Path(__file__).resolve().parents[1]


def hint(ident=1, attempt=2):
    return {'id': ident, 'attempt': attempt, 'revision': f'{ident:032x}'}


class RerunSource(Source):
    def __init__(self, rows):
        super().__init__()
        self.exact = {row['id']: row for row in rows}
        self.fail = set()
    def get(self, path, params=None):
        match = re.fullmatch(r'/repos/[^/]+/[^/]+/actions/runs/(\d+)(?:/attempts/(\d+))?', path)
        if match:
            ident = int(match[1]); self.calls.append((path, {}))
            if ident in self.fail:
                raise GitHubError('not logged', kind='server', status=503)
            row = deepcopy(self.exact[ident])
            if match[2]: row['run_attempt'] = int(match[2])
            return row
        return super().get(path, params)


class RunChangeTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
    def report(self, _gh, _cfg, row, **kwargs):
        row.update(reports_status='available', jobs_status='available', tests=[])
        return row
    def test_old_run_refreshes_by_id_before_lists_without_replaying_old_attempts(self):
        old = run(1); old['conclusion'] = 'failure'; old['created_at'] = stamp(NOW - timedelta(days=20))
        newer = dict(old, run_attempt=2, conclusion='success', updated_at=stamp(NOW))
        gh = RerunSource([newer])
        sync = Sync(gh, self.root, REPO, now=NOW)
        sync.save_run(old)
        with patch('gsb.sync.run_details', side_effect=self.report): sync.collect([hint()])
        self.assertEqual(sync.history.get('runs', '1-2')['conclusion'], 'success')
        self.assertEqual(sync.history.get('runs', '1-1')['conclusion'], 'failure')
        self.assertEqual(sync.completed_changes(), [hint()])
        self.assertEqual(gh.calls[1][0], '/repos/' + REPO + '/actions/runs/1')
        # No special historical listing is introduced to discover this old rerun.
        self.assertTrue(all(set(params) == {'created', 'page', 'per_page'} for path, params in gh.calls if path.endswith('/actions/runs')))
    def test_failed_metadata_and_exhausted_budget_do_not_acknowledge(self):
        gh = RerunSource([run(1, 2)]); gh.fail.add(1)
        sync = Sync(gh, self.root, REPO, now=NOW).collect([hint()])
        self.assertEqual(sync.completed_changes(), [])
        self.assertEqual(sync.state['errors']['rerun:1']['kind'], 'server')
        sync = Sync(gh, self.root, REPO, now=NOW, requests=1).collect([hint()])
        self.assertEqual(sync.completed_changes(), [])
    def test_failed_details_keep_the_hint_and_page_checkpoint_for_next_collection(self):
        gh = RerunSource([run(1, 2)])
        old = run(1); sync = Sync(gh, self.root, REPO, now=NOW); sync.save_run(old)
        with patch('gsb.sync.run_details', side_effect=GitHubError('private', kind='server', status=503)):
            sync.collect([hint()])
        self.assertEqual(sync.completed_changes(), [])
        self.assertIn('run:1-2', sync.state['pending'])
        for name, content in sync.files().items():
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
        recovered = Sync(gh, self.root, REPO, now=NOW + timedelta(hours=8))
        with patch('gsb.sync.run_details', side_effect=self.report): recovered.collect([hint()])
        self.assertEqual(recovered.completed_changes(), [hint()])
    def test_notified_details_get_a_turn_before_bulk_backfill(self):
        row = run(1, 2); gh = RerunSource([row])
        sync = Sync(gh, self.root, REPO, now=NOW, requests=2); sync.save_run(run(1))
        inspected = []
        def inspect(_gh, _cfg, record, **_kw):
            inspected.append((record['id'], record['attempt'])); return self.report(_gh, _cfg, record)
        with patch('gsb.sync.run_details', side_effect=inspect): sync.collect([hint()])
        self.assertEqual(inspected[0], (1, 2))
        self.assertEqual(inspected.count((1, 2)), 1)
        self.assertEqual(sync.completed_changes(), [hint()])
    def test_api_behind_notification_or_run_still_in_progress_stays_pending(self):
        for row in [run(1, 1), dict(run(1, 2), status='in_progress', conclusion=None)]:
            with self.subTest(row=row):
                sync = Sync(RerunSource([row]), self.root, REPO, now=NOW); sync.save_run(run(1))
                with patch('gsb.sync.run_details', side_effect=self.report): sync.collect([hint()])
                self.assertEqual(sync.completed_changes(), [])
    def test_receipts_and_input_are_nonsecret_bounded_and_source_scoped(self):
        path = self.root / 'input.json'
        value = {'ok': True, 'source': REPO, 'runs': [hint()]}; path.write_text(json.dumps(value))
        self.assertEqual(load_changes(path, REPO.upper()), [hint()])
        for bad in [{**value, 'source': 'other/source'}, {**value, 'runs': [hint()] * (BATCH_SIZE + 1)},
                    {**value, 'runs': [{**hint(), 'id': '../evil'}]}, {**value, 'runs': [{**hint(), 'id': True}]},
                    {**value, 'runs': [{**hint(), 'attempt': 1}]}, {**value, 'runs': [hint(), hint()]}]:
            path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError): load_changes(path, REPO)
        receipt = self.root / 'receipt.json'
        write_receipt(receipt, REPO, [], 'a' * 40); self.assertFalse(receipt.exists())
        write_receipt(receipt, REPO, [hint()], 'b' * 40)
        self.assertEqual(json.loads(receipt.read_text()), {'source': REPO, 'runs': [hint()], 'commit': 'b' * 40})
