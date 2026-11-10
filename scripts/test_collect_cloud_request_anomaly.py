import datetime
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import json
import os
import subprocess
import sys
import tempfile

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


class TraceCollectionTests(unittest.TestCase):
    def test_exact_request_tool_events_are_paired_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / 'process.log'
            logs.write_text('2026-11-09 00:00:00.000 [MessageHandler] dispatch: request_id=digitalmate-one\n'
                            '2026-11-09 00:00:10.000 [MessageHandler] Stream done request_id=digitalmate-one total_chunks=2\n', encoding='utf-8')
            history = root / '.jiuwenswarm' / 'agent' / 'sessions' / 'session' / 'history.jsonl'
            history.parent.mkdir(parents=True)
            events = [
                {'request_id': 'digitalmate-one', 'timestamp': 1794182401, 'event_type': 'chat.tool_call',
                 'tool_call': {'tool_call_id': 'one', 'name': 'bash', 'arguments': {'command': 'PRIVATE_COMMAND'}}},
                {'request_id': 'digitalmate-one', 'timestamp': 1794182403, 'event_type': 'chat.tool_result',
                 'tool_call_id': 'one', 'success': False, 'result': 'PRIVATE_RESULT'},
                {'request_id': 'digitalmate-other', 'timestamp': 1794182402, 'event_type': 'chat.tool_call',
                 'tool_call': {'tool_call_id': 'other', 'name': 'private_name', 'arguments': 'PRIVATE_OTHER'}},
            ]
            history.write_text('\n'.join(json.dumps(event) for event in events), encoding='utf-8')
            env = dict(os.environ, DM_LOG=str(logs))
            completed = subprocess.run([sys.executable, str(Path(__file__).with_name('cloud-request-log-aggregate.py'))],
                                       env=env, capture_output=True, text=True, check=True)
            self.assertNotIn('PRIVATE_', completed.stdout)
            doc = json.loads(completed.stdout)
            sample = doc['days'][0]['samples'][0]
            self.assertEqual(sample[5], 'digitalmate-one')
            self.assertEqual(sample[6], [{'name': 'bash', 'start_ms': 1000.0, 'duration_ms': 2000.0, 'status': 'error'}])
            published = collector.request_rows({'samples': [sample]})[0]
            self.assertEqual(published['tool_events'], sample[6])

    def test_rejects_raw_fields_and_invalid_intervals(self):
        row = collector.request_rows({'samples': [[100, 20, 80, 60, 90, 'rid', [
            {'name': 'secret_tool', 'start_ms': 10, 'duration_ms': 20, 'status': 'ok', 'arguments': 'secret'},
            {'name': 'bash', 'start_ms': 95, 'duration_ms': 20},
        ]]]})[0]
        self.assertEqual(row['tool_events'], [{'name': 'tool', 'start_ms': 10.0, 'duration_ms': 20.0, 'status': 'ok'}])
        self.assertNotIn('secret', json.dumps(row))

    def test_retained_day_does_not_receive_a_false_new_observation_time(self):
        old = {'date': '2026-11-08', 'n': 20, 'calls': {'total': 20}, 'tool': {'n': 20}}
        new = {'date': '2026-11-08', 'n': 1, 'calls': {'total': 1}, 'tool': {'n': 1}}
        merged = collector.merge({'days': [old], 'generated_at': '2026-11-09T00:00:00Z'}, {'days': [new]})
        self.assertNotIn('observed_at', merged['days'][0])
        self.assertEqual(merged['days'][0]['n'], 20)

    def test_main_rejects_a_test_environment_seed_before_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path(directory) / 'previous.json'
            previous.write_text(json.dumps({'env': 'adx-jiuwen-test-0'}), encoding='utf-8')
            with patch.object(collector, 'TOKEN', 'dummy'), \
                 patch.object(collector.sys, 'argv', ['collector', str(Path(directory) / 'out.json'), str(previous)]), \
                 patch.object(collector, 'collect') as collect, \
                 patch.object(collector.subprocess, 'check_output', side_effect=FileNotFoundError):
                with self.assertRaisesRegex(SystemExit, 'refusing to mix environments'):
                    collector.main()
                collect.assert_not_called()

    def test_incompatible_seed_falls_back_to_production_snapshot(self):
        prod = {'env': 'adx-jiuwen', 'schema': 'digitalmate/env-request-latency-daily@1', 'days': []}
        with patch.object(collector.subprocess, 'check_output', return_value=json.dumps(prod).encode()):
            self.assertEqual(collector.production_seed({'env': 'adx-jiuwen-test-0'}), prod)

    def test_binding_inventory_fetches_every_page(self):
        pages = [{'items': [{'id': 'a'}], 'next_page_token': 'next'},
                 {'items': [{'id': 'b'}], 'next_page_token': None}]
        with patch.object(collector, 'call', side_effect=pages) as call:
            self.assertEqual([item['id'] for item in collector.bindings()], ['a', 'b'])
            self.assertIn('page_token=next', call.call_args_list[1].args[0])

    def test_binding_inventory_rejects_repeated_page_tokens(self):
        pages = [{'items': [{'id': 'a'}], 'next_page_token': 'next'},
                 {'items': [{'id': 'b'}], 'next_page_token': 'next'}]
        with patch.object(collector, 'call', side_effect=pages):
            with self.assertRaisesRegex(SystemExit, 'repeated'):
                collector.bindings()

    def test_publication_keeps_old_aggregate_clock_and_publishes_trace_coverage(self):
        previous = {'env': 'adx-jiuwen', 'generated_at': '2026-11-08T00:00:00Z', 'days': [{'date': '2026-11-08'}]}
        traces = {'schema': 'digitalmate/cloud-traces@1', 'read_log_count': 2, 'binding_count': 3, 'days': []}
        result = collector.with_workspace_traces(previous, traces)
        self.assertEqual(result['generated_at'], previous['generated_at'])
        self.assertEqual(result['days'], previous['days'])
        self.assertEqual(result['cloud_trace_collection']['binding_count'], 3)
        with self.assertRaises(ValueError):
            collector.with_workspace_traces(previous, {**traces, 'read_log_count': 0})

    def test_main_uses_read_only_workspace_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path(directory) / 'previous.json'
            output = Path(directory) / 'out.json'
            previous.write_text(json.dumps({'env':'adx-jiuwen','schema':'digitalmate/env-request-latency-daily@1','days':[]}),encoding='utf-8')
            trace = {'schema':'digitalmate/cloud-traces@1','read_log_count':1,'days':[]}
            with patch.object(collector,'TOKEN','dummy'), \
                 patch.object(collector.sys,'argv',['collector',str(output),str(previous)]), \
                 patch.object(collector,'collect_workspace_traces',return_value=trace), \
                 patch.object(collector,'collect') as legacy:
                collector.main()
                legacy.assert_not_called()
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['cloud_trace_collection'],trace)

    def test_failure_of_an_active_environment_keeps_old_snapshot(self):
        with patch.object(collector, 'bindings', return_value=[{}, {}]), \
             patch.object(collector, 'collect_environment', side_effect=[{'days': []}, RuntimeError('unavailable')]):
            with self.assertRaises(SystemExit):
                collector.collect()

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
