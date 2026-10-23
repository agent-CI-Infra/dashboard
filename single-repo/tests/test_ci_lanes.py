from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gsb.ci_lanes import build_lanes, lane_of, pr_number, window_start
from gsb.history import encode
from gsb.incremental_project import build_snapshot
from gsb.sync import Sync
from test_incremental import Source, REPO

ROOT = Path(__file__).resolve().parents[1]
RULES = json.loads((ROOT / 'board-config.json').read_text())['workflows']
# 2026-09-22 20:00 in UTC+8: the window runs from 8/24 to 9/22.
NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)


def run(ident, name='CI', event='pull_request', created='2026-09-22T03:00:00Z', branch='feat', conclusion='success',
        status='completed', attempt=1, pull_requests=(), **extra):
    return dict(id=ident, attempt=attempt, name=name, workflow_id={'CI': 1, 'Nightly': 2, 'Release': 3}.get(name, 9),
                event=event, status=status, conclusion=conclusion, branch=branch, sha=f'{ident:040d}', created_at=created,
                url=f'https://github.com/{REPO}/actions/runs/{ident}', pull_requests=list(pull_requests), title=f'run {ident}',
                **extra)


def lanes(runs, **kw):
    doc = build_lanes(runs, default_branch='main', now=NOW, rules=RULES, **kw)
    return doc, {lane['key']: lane for lane in doc['lanes']}


def points(lane):
    return [cell['id'] for column in lane['days'] for cell in column]


class LaneClassificationTests(unittest.TestCase):
    def test_lanes_follow_how_actions_are_triggered(self):
        cases = [
            ('CI', 'pull_request', 'feat', 'pr'),
            ('CI', 'pull_request_target', 'feat', 'pr'),
            ('Nightly', 'workflow_call', 'main', None),
            ('Release', 'workflow_call', 'main', None),
            ('CI', 'push', 'main', 'main'),
            ('CI', 'workflow_dispatch', 'main', 'main'),
            ('CI', 'push', 'master', None),
            ('Nightly', 'schedule', 'main', 'daily'),
            ('Nightly', 'workflow_dispatch', 'main', 'daily'),
            ('Release', 'push', '0.3.0', 'release'),
            ('CI', 'workflow_call', 'main', None),
            # A CI run carrying its caller's trigger can only be a reusable call.
            ('CI', 'schedule', 'main', None),
            ('.github/workflows/test-policy.yml', 'push', 'feat', None),
        ]
        for name, event, branch, expected in cases:
            with self.subTest(name=name, event=event):
                self.assertEqual(lane_of(dict(name=name, event=event, branch=branch), RULES, 'main'), expected)

    def test_missing_trigger_degrades_without_guessing(self):
        self.assertEqual(lane_of(dict(name='CI', event=None, branch='feat', pull_requests=[5]), RULES, 'main'), 'pr')
        self.assertEqual(lane_of(dict(name='CI', event=None, branch='main'), RULES, 'main'), None)
        self.assertEqual(lane_of(dict(name='CI', branch='main'), None, 'main'), None)

    def test_merge_group_precedes_every_workflow_name_rule(self):
        runs = [run(i, name, 'merge_group') for i, name in enumerate(('CI', 'Nightly', 'Release', 'Maintenance'), 1)]
        doc, by_key = lanes(runs)
        self.assertEqual([(lane['key'], lane['label']) for lane in doc['lanes']],
                         [('pr', 'PR'), ('main', '主干'), ('merge_queue', '合并队列'), ('daily', 'Daily'), ('release', '版本')])
        self.assertEqual(points(by_key['merge_queue']), [1, 2, 3, 4])
        self.assertTrue(all(not points(lane) for key, lane in by_key.items() if key != 'merge_queue'))

    def test_hidden_categories_have_no_points_or_excluded_counts(self):
        visible = [run(1, 'Nightly', 'schedule'), run(2, 'Release', 'push')]
        hidden = [run(3, 'CI', 'workflow_call'), run(4, 'CI', 'schedule'),
                  run(5, 'CI', 'push', branch='other'), run(6, event=None),
                  run(7, 'Maintenance', 'push'), run(8, 'Nightly', 'workflow_call')]
        doc, by_key = lanes([*visible, *hidden, dict(hidden[0], attempt=2)])
        self.assertEqual(sorted(ident for lane in by_key.values() for ident in points(lane)), [1, 2])
        self.assertNotIn('excluded', doc)

    def test_release_branch_keeps_manually_dispatched_nightly_in_daily(self):
        doc = build_lanes([run(37845266203, 'Nightly', 'workflow_dispatch', branch='releases/v0.3.0.beta')],
                          default_branch='releases/v0.3.0.beta', now=NOW, rules=RULES)
        daily = next(lane for lane in doc['lanes'] if lane['key'] == 'daily')
        self.assertEqual(points(daily), [37845266203])
        self.assertTrue(daily['days'][-1][0]['manual'])
        self.assertNotIn('excluded', doc)

    def test_path_named_startup_failure_stays_with_its_workflow(self):
        # GitHub names a run after its file when the workflow does not parse.
        broken = run(2, '.github/workflows/ci.yml', conclusion='startup_failure')
        broken['workflow_id'] = 1
        _, by_key = lanes([run(1), broken])
        self.assertEqual(points(by_key['pr']), [1, 2])
        self.assertEqual(by_key['pr']['days'][-1][1]['workflow'], 'CI')
        self.assertEqual(by_key['pr']['summary']['failure'], 1)

    def test_manual_dispatch_is_flagged(self):
        _, by_key = lanes([run(1, 'CI', 'workflow_dispatch', branch='main'), run(2, 'Nightly', 'workflow_dispatch', branch='main')])
        self.assertTrue(by_key['main']['days'][-1][0]['manual'])
        self.assertTrue(by_key['daily']['days'][-1][0]['manual'])


class LaneLayoutTests(unittest.TestCase):
    def test_all_lanes_share_one_calendar_axis(self):
        runs = [run(1, 'CI', 'pull_request', '2026-09-21T02:00:00Z'),
                run(2, 'CI', 'push', '2026-09-21T09:00:00Z', 'main'),
                run(3, 'Nightly', 'schedule', '2026-09-21T01:00:00Z', 'main'),
                run(4, 'Release', 'push', '2026-09-21T10:00:00Z', '0.3.0'),
                run(5, 'CI', 'pull_request', '2026-08-23T15:00:00Z')]  # 8/23 23:00, before the window
        doc, by_key = lanes(runs)
        self.assertEqual(len(doc['days']), 30)
        self.assertEqual((doc['days'][0], doc['days'][-1]), ('2026-08-24', '2026-09-22'))
        steps = {(datetime.fromisoformat(b) - datetime.fromisoformat(a)).days for a, b in zip(doc['days'], doc['days'][1:])}
        self.assertEqual(steps, {1})
        column = doc['days'].index('2026-09-21')
        for key, ident in (('pr', 1), ('main', 2), ('daily', 3), ('release', 4)):
            lane = by_key[key]
            self.assertEqual(len(lane['days']), 30)
            self.assertEqual([cell['id'] for cell in lane['days'][column]], [ident], key)
            self.assertEqual(sum(len(cells) for cells in lane['days']), 1, key)
        self.assertEqual(window_start(NOW), datetime(2026, 8, 23, 16, tzinfo=timezone.utc))

    def test_same_day_runs_stack_earliest_first_in_display_zone(self):
        runs = [run(1, created='2026-09-22T11:59:00Z'),
                run(2, created='2026-09-21T17:30:00Z'),  # 9/22 01:30 in UTC+8
                run(3, created='2026-09-22T03:00:00Z'),
                run(4, created='2026-09-21T15:00:00Z'),  # 9/21 23:00 in UTC+8
                run(5, created='2026-09-22T03:00:00Z')]  # same second: id breaks the tie
        doc, by_key = lanes(runs)
        today, yesterday = by_key['pr']['days'][-1], by_key['pr']['days'][-2]
        self.assertEqual([cell['id'] for cell in today], [2, 3, 5, 1])
        self.assertEqual([cell['time'] for cell in today], ['01:30', '11:00', '11:00', '19:59'])
        self.assertEqual([cell['id'] for cell in yesterday], [4])
        self.assertEqual(doc['order'], 'earliest_first')

    def test_rerun_uses_latest_attempt_on_creation_day(self):
        first = run(1, created='2026-09-20T03:00:00Z', conclusion='failure')
        rerun = dict(first, attempt=2, conclusion='success')
        doc, by_key = lanes([rerun, first])
        column = doc['days'].index('2026-09-20')
        self.assertEqual([(c['attempt'], c['outcome']) for c in by_key['pr']['days'][column]], [(2, 'success')])
        self.assertEqual(by_key['pr']['summary']['total'], 1)

    def test_lane_without_runs_is_empty_not_failed(self):
        _, by_key = lanes([run(1)])
        queue = by_key['merge_queue']
        self.assertEqual(queue['days'], [[] for _ in range(30)])
        self.assertEqual(queue['summary'], dict(total=0, success=0, failure=0, cancelled=0, running=0, other=0, success_rate=None, median_duration_s=None))

    def test_summary_counts_each_outcome_and_completed_success_rate(self):
        results = [('success', 'completed'), ('failure', 'completed'), ('timed_out', 'completed'), ('startup_failure', 'completed'),
                   ('cancelled', 'completed'), ('skipped', 'completed'), (None, 'in_progress'), (None, 'queued'),
                   ('action_required', 'completed')]
        _, by_key = lanes([run(i, conclusion=c, status=s) for i, (c, s) in enumerate(results, 1)])
        self.assertEqual(by_key['pr']['summary'], dict(total=9, success=1, failure=3, cancelled=2, running=2, other=1, success_rate=25.0, median_duration_s=None))
        self.assertEqual([c['outcome'] for c in by_key['pr']['days'][-1]][-3:], ['running', 'running', 'other'])

    def test_days_before_collected_history_are_marked(self):
        doc, _ = lanes([run(1)], collected_since='2026-09-10T00:00:00Z')
        self.assertEqual(doc['collected_since'], '2026-09-10')
        doc, _ = lanes([run(1)], collected_since='2026-01-01T00:00:00Z')
        self.assertIsNone(doc['collected_since'])


class DurationTests(unittest.TestCase):
    def test_each_run_carries_its_duration_and_each_lane_the_median(self):
        runs = [run(1, duration_s=600), run(2, conclusion='failure', duration_s=300),
                run(3, started_at='2026-09-22T03:00:00Z', updated_at='2026-09-22T03:20:30Z'),
                run(4, conclusion='cancelled', duration_s=5), run(5, status='in_progress', conclusion=None, duration_s=99)]
        _, by_key = lanes(runs)
        cells = {cell['id']: cell['duration_s'] for column in by_key['pr']['days'] for cell in column}
        # A recorded duration wins; otherwise start to last update; a running attempt has none yet.
        self.assertEqual(cells, {1: 600, 2: 300, 3: 1230, 4: 5, 5: None})
        # Cancelled runs stopped early and do not pull the median down.
        self.assertEqual(by_key['pr']['summary']['median_duration_s'], 600)


class PullRequestNumberTests(unittest.TestCase):
    PRS = [dict(number=7, head='feat', head_repo='fork-a/source', head_sha='a' * 40, title='Add feature',
                created_at='2026-09-20T00:00:00Z', closed_at=None),
           dict(number=8, head='feat', head_repo='fork-b/source', head_sha='b' * 40, title='Other feature',
                created_at='2026-09-20T00:00:00Z', closed_at=None),
           dict(number=6, head='fix', head_repo=None, head_sha='c' * 40, title='Old fix',
                created_at='2026-09-01T00:00:00Z', closed_at='2026-09-02T00:00:00Z')]

    def test_linked_pull_request_wins(self):
        self.assertEqual(pr_number(run(1, pull_requests=[42]), self.PRS), 42)
        self.assertEqual(pr_number(run(1, pull_requests=[{'number': 43}]), self.PRS), 43)

    def test_fork_run_matches_head_repository_then_sha(self):
        self.assertEqual(pr_number(run(1, branch='feat', head_repo='fork-b/source'), self.PRS), 8)
        self.assertEqual(pr_number(dict(run(1, branch='feat'), sha='a' * 40), self.PRS), 7)

    def test_ambiguous_closed_or_non_pr_runs_get_no_number(self):
        self.assertIsNone(pr_number(run(1, branch='feat'), self.PRS))
        self.assertIsNone(pr_number(run(1, branch='fix'), self.PRS))
        self.assertIsNone(pr_number(run(1, 'CI', 'push', branch='feat', head_repo='fork-a/source'), self.PRS))

    def test_lane_cells_mark_inferred_numbers(self):
        _, by_key = lanes([run(1, branch='feat', head_repo='fork-a/source'), run(2, pull_requests=[9])], prs=self.PRS)
        cells = by_key['pr']['days'][-1]
        self.assertEqual([(c['pr'], c['pr_linked']) for c in cells], [(7, False), (9, True)])


class SnapshotLaneTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self):
        def raw(ident, name, event, branch, minutes, **extra):
            created = (NOW - timedelta(minutes=minutes)).isoformat().replace('+00:00', 'Z')
            return dict(id=ident, run_attempt=1, name=name, workflow_id=ident, event=event, status='completed',
                        conclusion='success', head_branch=branch, head_sha='d' * 40, display_title=f'{name} {ident}',
                        html_url=f'https://github.com/{REPO}/actions/runs/{ident}', created_at=created, updated_at=created,
                        run_started_at=created, pull_requests=[], **extra)
        gh = Source()
        gh.runs = [raw(1, 'CI', 'pull_request', 'feat', 50, head_repository={'full_name': 'fork/source'}),
                   raw(2, 'CI', 'push', 'main', 40), raw(3, 'Nightly', 'schedule', 'main', 30),
                   raw(4, 'Release', 'push', '0.3.0', 20), raw(5, 'CI', 'workflow_dispatch', 'main', 10)]
        gh.prs = [dict(number=7, title='Feature', state='open', body='', user={'login': 'person'}, created_at='2026-09-20T00:00:00Z',
                       updated_at='2026-09-21T00:00:00Z', closed_at=None, merged_at=None, html_url=f'https://github.com/{REPO}/pull/7',
                       head={'ref': 'feat', 'sha': 'd' * 40, 'repo': {'full_name': 'fork/source'}}, base={'ref': 'main'})]
        return gh

    def snapshot(self, sync):
        with patch('gsb.incremental_project.public_ops', return_value={}), patch('gsb.incremental_project.public_tests', return_value={}):
            return build_snapshot(sync)

    def test_snapshot_lanes_use_history_records_even_with_old_index_shards(self):
        sync = Sync(self.source(), self.root, REPO, settings={'workflows': RULES}, now=NOW).collect()
        ci = self.snapshot(sync)['sections']['ci']['data']
        lanes_by_key = {lane['key']: lane for lane in ci['lanes']['lanes']}
        self.assertEqual({k: points(v) for k, v in lanes_by_key.items()},
                         {'pr': [1], 'main': [2, 5], 'merge_queue': [], 'daily': [3], 'release': [4]})
        self.assertEqual(lanes_by_key['pr']['days'][-1][0]['pr'], 7)
        self.assertEqual(ci['pull_request']['total'], 1)
        for name, content in sync.files().items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        # Shards written before "event" joined the index still classify correctly.
        for path in (self.root / 'site/data/history/index/runs').glob('*.json'):
            rows = json.loads(path.read_text())
            path.write_text(encode({key: {k: v for k, v in row.items() if k != 'event'} for key, row in rows.items()}))
        (self.root / 'site/data/snapshot.json').unlink(missing_ok=True)
        sync = Sync(self.source(), self.root, REPO, settings={'workflows': RULES}, now=NOW)
        sync.meta = sync.gh.get('/repos/' + REPO)
        ci = self.snapshot(sync)['sections']['ci']['data']
        self.assertEqual(ci['pull_request']['total'], 1)
        self.assertEqual(points(ci['lanes']['lanes'][0]), [1])
        # Those rows are rewritten from the records once; records are untouched.
        self.assertTrue(any('/index/' in name for name in sync.history.changed))
        self.assertFalse(any('/records/' in name for name in sync.history.changed))


if __name__ == '__main__':
    unittest.main()
