import datetime
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('cloud_collector', Path(__file__).with_name('collect-cloud-request-metrics.py'))
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def make_day(date, values, partial=False, sample=None):
    row = sample or [100, 20, 90, 70, 95]
    samples = [list(row) for _ in values]
    return {
        'date': date,
        'partial': partial,
        'samples': samples,
        'gateway': [item[0] for item in samples],
        'first_token': [item[1] for item in samples],
        'answer_first': [item[2] for item in samples],
        'react': [item[3] for item in samples],
        'workswarm': [item[4] for item in samples],
        'ttft': [item[1] for item in samples],
        'tpot': [[item[3], 10] for item in samples],
        'tool': [item[3] for item in samples],
    }


class AnomalyTests(unittest.TestCase):
    def test_baseline_skips_partial_days_and_scores_against_earlier_complete_days(self):
        days = [make_day(f'2026-11-{index:02d}', [100 + offset for offset in range(20)]) for index in range(1, 8)]
        # A partial day must not enter the baseline even when its samples are extreme.
        days.append(make_day('2026-11-08', [999999] * 20, partial=True))
        target = make_day('2026-11-09', [50 + offset for offset in range(20)])
        target['samples'].append([100000, 50000, 90000, 40000, 95000])
        days.append(target)

        collector.annotate_anomalies(days)

        anomaly = target['anomaly']
        self.assertTrue(anomaly['available'])
        self.assertEqual(anomaly['baseline']['to'], '2026-11-07')
        self.assertEqual(anomaly['baseline']['days'], 7)
        gateway = anomaly['metrics']['gateway']
        self.assertEqual(gateway['baseline_p95'], 100.0)
        self.assertEqual(gateway['n'], 21)
        self.assertEqual(gateway['outliers'], 1)
        self.assertAlmostEqual(gateway['rate'], 1 / 21, places=4)
        self.assertEqual(anomaly['cohort']['outliers'], 1)
        self.assertEqual(anomaly['cohort']['driver'], 'first_token')
        self.assertGreater(anomaly['cohort']['driver_delta_ms'], 0)

    def test_first_day_has_no_baseline(self):
        days = [make_day('2026-11-01', [100] * 20)]
        collector.annotate_anomalies(days)
        anomaly = days[0]['anomaly']
        self.assertFalse(anomaly['available'])
        self.assertEqual(anomaly['reason'], 'no_complete_baseline')
        self.assertEqual(anomaly['latency']['max_ms'], 100.0)

    def test_metric_with_few_baseline_samples_is_not_scored(self):
        days = [make_day('2026-11-01', [100] * 5)]
        target = make_day('2026-11-02', [100] * 5)
        days.append(target)
        collector.annotate_anomalies(days)
        gateway = target['anomaly']['metrics']['gateway']
        self.assertTrue(gateway['insufficient_baseline'])
        self.assertNotIn('baseline_p95', gateway)
        self.assertNotIn('outliers', gateway)

    def test_day_without_samples_reports_unmeasured_not_zero(self):
        days = [make_day('2026-11-01', [100] * 20)]
        target = make_day('2026-11-02', [100] * 20)
        target['samples'] = []
        days.append(target)
        collector.annotate_anomalies(days)
        gateway = target['anomaly']['metrics']['gateway']
        self.assertEqual(gateway['n'], 0)
        self.assertIsNone(gateway['outliers'])
        self.assertIsNone(gateway['rate'])

    def test_array_metrics_score_against_pooled_raw_values(self):
        raw_days = []
        for index in range(1, 9):
            ttft = [10.0] * 20
            tool = [100.0] * 20
            if index == 8:
                ttft[-1] = 999.0
                tool[-1] = 9999.0
            raw_days.append({
                'date': f'2020-01-{index:02d}',
                'rids': [f'rid-{index}-{item}' for item in range(20)],
                'samples': [[100, 20, 90, 70, 95]] * 20,
                'ttft': ttft,
                'tpot': [[5.0, 10]] * 20,
                'tool': tool,
                'calls': {'success': 20, 'failed': 0},
                'tokens': {'input': 1, 'output': 1, 'total': 2},
            })
        result = collector.pool([{'noise': 0, 'days': raw_days}])
        target = result['days'][-1]
        ttft = target['anomaly']['metrics']['ttft']
        self.assertEqual(ttft['baseline_n'], 140)
        self.assertEqual(ttft['baseline_p95'], 10.0)
        self.assertEqual(ttft['outliers'], 1)
        self.assertEqual(target['anomaly']['metrics']['tool']['outliers'], 1)
        # The raw arrays must not survive into the published row.
        self.assertNotIn('_raw', target)
        self.assertNotIn('[10.0, 10.0', str(result))

    def test_latency_max_and_slowest_five_percent_keep_request_ids(self):
        samples = [[100 + index * 10, 10, 90, 20, 95, f'rid-{index}'] for index in range(20)]
        day = {'date': '2026-11-10', 'partial': False, 'samples': samples,
               'error_categories': {}, 'timeout_rids': []}
        collector.annotate_anomalies([day])
        # No prior complete day: the baseline is unavailable, but the latency
        # summary must still be published.
        self.assertFalse(day['anomaly']['available'])
        latency = day['anomaly']['latency']
        self.assertEqual(latency['n'], 20)
        self.assertEqual(latency['max_ms'], 290.0)
        self.assertEqual(latency['max_request']['request_id'], 'rid-19')
        self.assertEqual(latency['slow_count'], 1)
        self.assertEqual(latency['slow_threshold_ms'], 290.0)
        self.assertEqual([row['request_id'] for row in latency['slow_requests']], ['rid-19'])

    def test_timeout_summary_links_ids_to_phase_rows_when_available(self):
        day = {'date': '2026-11-10', 'partial': False,
               'samples': [[100, 20, 90, 70, 95, 'rid-timeout']],
               'error_categories': {'timeout': 2},
               'timeout_rids': ['rid-timeout', 'rid-unmatched']}
        collector.annotate_anomalies([day])
        timeouts = day['anomaly']['timeouts']
        self.assertEqual(timeouts['count'], 2)
        self.assertEqual(timeouts['records'], 2)
        matched, unmatched = timeouts['requests']
        self.assertEqual(matched['request_id'], 'rid-timeout')
        self.assertEqual(matched['gateway'], 100)
        self.assertEqual(unmatched, {'request_id': 'rid-unmatched'})

    def test_pool_aggregates_failure_categories_and_timeout_ids(self):
        raw = {'noise': 0, 'days': [{
            'date': '2026-11-10',
            'samples': [], 'gateway': [], 'calls': {'success': 0, 'failed': 3},
            'tokens': {'input': 0, 'output': 0, 'total': 0},
            'error_categories': {'timeout': 2, 'rate_limit': 1},
            'timeout_rids': ['rid-a', 'rid-b'],
        }]}
        day = collector.pool([raw])['days'][0]
        self.assertEqual(day['error_categories'], {'timeout': 2, 'rate_limit': 1})
        self.assertEqual(day['timeout_rids'], ['rid-a', 'rid-b'])
        self.assertEqual(day['anomaly']['timeouts']['count'], 2)
        # Without phase samples, the timeout ids are still published so the
        # request can be found, but no phase value is invented.
        self.assertEqual([row['request_id'] for row in day['anomaly']['timeouts']['requests']],
                         ['rid-a', 'rid-b'])

    def test_pool_publishes_aggregates_not_sample_rows(self):
        today = datetime.datetime.now(collector.CST).strftime('%Y-%m-%d')
        raw = {
            'noise': 0,
            'first_ts': None,
            'days': [{
                'date': today,
                'rids': ['private-request-id'],
                'gateway': [100],
                'samples': [[100, 20, 90, 70, 95]],
                'calls': {'success': 1, 'failed': 0},
                'tokens': {'input': 1, 'output': 1, 'total': 2},
            }],
        }
        result = collector.pool([raw])
        day = result['days'][0]
        self.assertNotIn('samples', day)
        self.assertNotIn('private-request-id', str(result))
        self.assertEqual(day['uplink_send'], {'n': 0})
        self.assertEqual(day['downlink_first_text'], {'n': 0})
        self.assertEqual(day['downlink_terminal'], {'n': 0})
        self.assertFalse(day['anomaly']['available'])


if __name__ == '__main__':
    unittest.main()
