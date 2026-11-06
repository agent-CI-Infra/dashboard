from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gsb.history import History, encode
from gsb.incremental_project import build_snapshot
from gsb.sync import Sync
from gsb.work_trend import daily_trend
from test_incremental import NOW, REPO, ROOT, Source

def work(number, created, closed=None, merged=None):
    return dict(number=number, state='closed' if closed else 'open', created_at=created + 'T03:00:00Z',
                closed_at=closed and closed + 'T05:00:00Z', merged_at=merged and merged + 'T05:00:00Z')


class TrendTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp'); self.addCleanup(self.temp.cleanup)
        self.history = History(Path(self.temp.name))

    def test_issue_days_are_contiguous_and_open_is_created_minus_closed(self):
        for row in (work(1, '2026-09-01'), work(2, '2026-09-01', '2026-09-03'), work(3, '2026-09-04')):
            self.history.put('issues', row)
        trend = daily_trend(self.history.aggregate, 'issues', '2026-09-06')
        self.assertEqual((trend['start'], trend['end'], trend['timezone']), ('2026-09-01', '2026-09-06', 'UTC'))
        self.assertEqual(trend['opened'], [2, 0, 0, 1, 0, 0])
        self.assertEqual(trend['closed'], [0, 0, 1, 0, 0, 0])
        self.assertEqual(trend['open'], [2, 2, 1, 2, 2, 2])
        self.assertNotIn('merged', trend)
        self.assertEqual(trend['open'][-1], self.history.aggregate['issues:state:open'])

    def test_pull_requests_split_merged_from_closed_without_merging(self):
        for row in (work(1, '2026-09-01', '2026-09-02', '2026-09-02'), work(2, '2026-09-01', '2026-09-02'), work(3, '2026-09-02')):
            self.history.put('prs', row)
        trend = daily_trend(self.history.aggregate, 'prs', '2026-09-02')
        self.assertEqual(trend['opened'], [2, 1])
        self.assertEqual(trend['merged'], [0, 1])
        self.assertEqual(trend['closed'], [0, 1])
        self.assertEqual(trend['open'], [2, 1])

    def test_updates_change_the_series_through_the_aggregate_alone(self):
        self.history.put('issues', work(1, '2026-09-01'))
        before = daily_trend(self.history.aggregate, 'issues', '2026-09-03')
        # Closing replaces the record's contribution; reopening takes it back out again.
        self.history.put('issues', work(1, '2026-09-01', '2026-09-02'))
        closed = daily_trend(self.history.aggregate, 'issues', '2026-09-03')
        self.assertEqual((closed['closed'], closed['open']), ([0, 1, 0], [1, 0, 0]))
        self.history.put('issues', work(1, '2026-09-01'))
        self.assertEqual(daily_trend(self.history.aggregate, 'issues', '2026-09-03'), before)
        # Nothing but the day keys is read: the records are not needed.
        self.assertEqual(daily_trend(dict(self.history.aggregate), 'issues', '2026-09-03'), before)

    def test_no_items_means_no_trend(self):
        self.assertIsNone(daily_trend({}, 'prs', '2026-09-03'))

    def test_snapshot_carries_trends_and_older_snapshots_are_rebuilt_once(self):
        sync = Sync(Source(issues=2, prs=1), Path(self.temp.name), REPO, now=NOW).collect()
        with patch('gsb.incremental_project.public_ops', return_value={}), patch('gsb.incremental_project.public_tests', return_value={}):
            doc = build_snapshot(sync)
        issues, prs = doc['sections']['issues']['data']['trend'], doc['sections']['prs']['data']['trend']
        self.assertEqual((issues['start'], issues['end'], issues['open'][-1], issues['complete']), ('2026-01-01', NOW.date().isoformat(), 2, True))
        self.assertEqual((prs['opened'][0], prs['merged'][0], prs['open'][-1]), (1, 0, 1))
        for name, content in sync.files().items():
            path = Path(self.temp.name)/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
        # An unchanged poll on the same day reuses a snapshot that already has trends...
        path = Path(self.temp.name)/'site/data/snapshot.json'; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(encode(doc))
        later = Sync(Source(issues=2, prs=1), Path(self.temp.name), REPO, now=NOW + timedelta(minutes=10)).collect()
        with patch('gsb.incremental_project.public_ops', side_effect=AssertionError('must use cache')):
            self.assertEqual(build_snapshot(later), doc)
        # ...but one published before the charts existed is rebuilt to add them.
        for kind in ('issues', 'prs'):
            del doc['sections'][kind]['data']['trend']
        path.write_text(encode(doc))
        later = Sync(Source(issues=2, prs=1), Path(self.temp.name), REPO, now=NOW + timedelta(minutes=20)).collect()
        with patch('gsb.incremental_project.public_ops', return_value={}), patch('gsb.incremental_project.public_tests', return_value={}):
            rebuilt = build_snapshot(later)
        self.assertEqual(rebuilt['sections']['issues']['data']['trend']['open'][-1], 2)


if __name__ == '__main__':
    unittest.main()
