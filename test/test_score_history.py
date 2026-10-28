import unittest

from gsb.public_sections import daily_score_runs, latest_score_report, score_history


def run(ident, created_at, event, score, *, delivery='passed', duration_ms=1000):
    return dict(id=ident, attempt=1, created_at=created_at, event=event,
                url=f'https://example.test/runs/{ident}', tests=[dict(
                    name='real-e2e-results', url=f'https://example.test/artifacts/{ident}',
                    scores=[dict(case='case-a', delivery=delivery, quality_status='scored',
                                 duration_ms=duration_ms,
                                 metrics=[dict(label='Rubric', unit='score100', value=score)])])])


class ScoreHistoryTest(unittest.TestCase):
    def test_manual_run_wins_beijing_day_and_failed_case_carries_score(self):
        scheduled = run(1, '2026-09-28T21:00:00Z', 'schedule', 80)
        manual = run(2, '2026-09-29T10:00:00Z', 'workflow_dispatch', 90)
        failed = run(3, '2026-09-29T20:00:00Z', 'schedule', None,
                     delivery='failed', duration_ms=2345)
        self.assertEqual([r['id'] for r in daily_score_runs([scheduled, manual, failed])], [3, 2])
        points = score_history([scheduled, manual, failed])['case-a']
        self.assertEqual([p['run_id'] for p in points], [2, 3])
        self.assertEqual(points[-1]['metrics'][0]['value'], 90)
        self.assertEqual(points[-1]['metrics'][0]['source'], 'carried')
        self.assertEqual(points[-1]['duration_ms'], 2345)

    def test_missing_first_score_uses_explicit_zero_baseline(self):
        point = score_history([run(1, '2026-09-29T20:00:00Z', 'schedule', None,
                                   delivery='failed')])['case-a'][0]
        self.assertEqual(point['metrics'][0]['value'], 0)
        self.assertEqual(point['metrics'][0]['source'], 'baseline')

    def test_passed_case_with_missing_score_remains_empty(self):
        point = score_history([run(1, '2026-09-29T20:00:00Z', 'schedule', None)])['case-a'][0]
        self.assertIsNone(point['metrics'][0]['value'])
        self.assertEqual(point['metrics'][0]['source'], 'unavailable')

    def test_unfinished_manual_run_does_not_hide_the_days_readable_scores(self):
        scheduled = run(1, '2026-10-09T20:44:00Z', 'schedule', 80)
        running = dict(id=2, attempt=1, created_at='2026-10-10T09:26:00Z', event='workflow_dispatch', tests=[])
        # The Nightly summary still reports the manual run as the day's run.
        self.assertEqual([r['id'] for r in daily_score_runs([scheduled, running])], [2])
        points = score_history([scheduled, running])['case-a']
        self.assertEqual([p['run_id'] for p in points], [1])
        self.assertEqual(points[0]['metrics'][0]['source'], 'measured')

    def test_rerun_without_scores_does_not_fall_back_to_its_earlier_attempt(self):
        first = run(1, '2026-10-09T20:44:00Z', 'schedule', 80)
        rerun = dict(first, attempt=2, tests=[])
        self.assertEqual(score_history([first, rerun]), {})

    def test_latest_readable_report_survives_newer_unreadable_run(self):
        scored = run(1, '2026-10-05T22:00:00Z', 'schedule', 82)
        scored['branch'] = 'releases/v0.3.0.beta'
        unreadable = run(2, '2026-10-06T22:00:00Z', 'schedule', None)
        unreadable['tests'][0]['scores'] = []
        report = latest_score_report([unreadable, scored])
        self.assertEqual(report['run_id'], 1)
        self.assertEqual(report['branch'], 'releases/v0.3.0.beta')
        self.assertEqual(report['scores'][0]['metrics'][0]['value'], 82)


if __name__ == '__main__':
    unittest.main()
