import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = load('network_workspace', 'collect-production-workspace-traces.py')
remote = load('network_remote', 'kube-workspace-remote.py')
cloud = load('network_cloud', 'collect-cloud-request-metrics.py')


class NetworkMetricsTests(unittest.TestCase):
    today = dt.date(2026, 11, 19)

    def sample(self, **changes):
        return {'schema': 'digitalmate-client-network@1', 'kind': 'sample',
                't': '2026-11-18T16:01:00Z', 'bucket': 'uplink_send', 'ms': 20,
                'request_id': 'PRIVATE_REQUEST', 'clock_error_ms': 12,
                'clock_offset_ms': 30, **changes}

    def parse(self, records, files=None):
        data = '\n'.join(json.dumps(row) if not isinstance(row, str) else row for row in records)
        files = files or [{'name': 'client-network-2026-11-18.jsonl', 'type': 'file', 'size': len(data)}]
        usage = {}
        with patch.object(collector, 'fetch', return_value=data):
            collector.read_network({}, files, usage, self.today)
        return usage

    def test_shanghai_day_deduplication_zero_and_private_aggregation(self):
        row = self.sample()
        usage = self.parse([row, row, self.sample(request_id='second', ms=0)])
        self.assertEqual(set(usage), {'2026-11-19'})
        self.assertEqual(usage['2026-11-19']['network']['uplink_send'], [20, 0])
        result = collector.request_daily_metrics([], '2026-11-19', '2026-11-19T01:00:00Z', False,
                                                 usage['2026-11-19'])
        self.assertEqual(result['uplink_send']['n'], 2)
        self.assertEqual(result['uplink_send']['sum'], 20)
        self.assertEqual(result['uplink_send']['mean'], 10)
        self.assertEqual(sum(n for _, n in result['uplink_send']['histogram']), 2)
        self.assertEqual(result['downlink_terminal'], {'n': 0})
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('clock_offset_ms', json.dumps(result))

    def test_clock_rejections_bad_dates_coverage_and_malformed_rows_are_not_samples(self):
        invalid = [dict(clock_error_ms=51), dict(clock_error_ms=True), dict(clock_error_ms=-1),
                   dict(clock_offset_ms=86_400_001), dict(clock_offset_ms=None),
                   dict(ms=-1), dict(ms=120_001), dict(ms=True), dict(ms=float('nan')),
                   dict(t='2026-11-19T00:00:00'), dict(t='2026-10-01T00:00:00Z'),
                   dict(t='2026-11-20T00:00:00Z'), dict(t=None), dict(request_id=None),
                   dict(schema='unknown'), dict(kind='coverage', uplink_accepted=999),
                   dict(bucket='unknown')]
        self.assertEqual(self.parse([self.sample(**fields) for fields in invalid] + ['{', '[]', 'null']), {})

    def test_downlink_frame_pair_and_home_response_deduplication(self):
        frame = self.sample(bucket='downlink_first_text', task_id='PRIVATE_TASK', execution_id='exec',
                            server_at_ms=1000, client_received_at_ms=1050)
        home = self.sample(bucket='downlink_terminal', surface='hive_home_response',
                           server_at_ms=2000, client_received_at_ms=2050)
        usage = self.parse([frame, {**frame, 'request_id': 'observation-two'}, home,
                            {**home, 'bucket': 'downlink_first_text'},
                            {**frame, 'client_received_at_ms': 1051},
                            {**frame, 'task_id': None}, {**home, 'server_at_ms': True}])
        network = usage['2026-11-19']['network']
        self.assertEqual(network['downlink_first_text'], [20, 20])
        self.assertEqual(network['downlink_terminal'], [20])
        self.assertEqual(network['uplink_send'], [])

    def test_optional_file_failure_marks_partial_without_raw_errors(self):
        files = [{'name': 'client-network-2026-11-19.jsonl', 'type': 'file', 'size': 123}]
        usage = {}
        with patch.object(collector, 'fetch', side_effect=OSError('PRIVATE')):
            collector.read_network({}, files, usage, self.today)
        day = collector.request_daily_metrics([], self.today.isoformat(), 'now', False, usage[self.today.isoformat()])
        self.assertTrue(day['network_partial'])
        self.assertEqual(day['uplink_send'], {'n': 0})
        self.assertNotIn('PRIVATE', json.dumps(day))
        with patch.object(collector, 'fetch') as fetch:
            collector.read_network({}, [{**files[0], 'size': collector.MAX_NETWORK_LOG + 1}], {}, self.today)
            fetch.assert_not_called()
        with patch.object(collector, 'MAX_NETWORK_BYTES', 1), patch.object(collector, 'fetch') as fetch:
            collector.read_network({}, files, {}, self.today)
            fetch.assert_not_called()

    def test_sfs_remote_collects_network_only_days_and_pools_workspaces(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=2)
            for char, value in [('a', 20), ('b', 100)]:
                work = root / ('w-' + char * 64) / 'workswarm'
                work.mkdir(parents=True)
                (work / 'process.log').touch()
                (work / f'client-network-{now.date()}.jsonl').write_text(
                    json.dumps(self.sample(t=now.isoformat(), ms=value)), encoding='utf8')
            source = Path(collector.__file__).read_text(encoding='utf8')
            with patch.object(remote, 'ROOT', root):
                result = remote.run_collector(['w-' + char * 64 for char in ('a', 'b')], source)
            day = result['request_daily_metrics'][0]
            self.assertEqual(result['read_log_count'], 2)
            self.assertEqual(day['n'], 0)
            self.assertEqual(day['uplink_send']['n'], 2)
            self.assertEqual(day['uplink_send']['sum'], 120)
            self.assertEqual(day['uplink_send']['p95'], 100)
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_current_network_samples_survive_larger_historical_latency_population(self):
        previous = {'days': [{'date': '2026-11-19', 'n': 100, 'gateway': {'n': 100},
                              'uplink_send': {'n': 50, 'mean': 999}}]}
        current = collector.request_daily_metrics([], '2026-11-19', '2026-11-19T01:00:00Z', False,
                                                  self.parse([self.sample()])['2026-11-19'])
        traces = {'schema': 'digitalmate/cloud-traces@1', 'read_log_count': 1,
                  'request_daily_metrics': [current]}
        result = cloud.with_workspace_traces(previous, traces)['days'][0]
        self.assertEqual(result['n'], 100)
        self.assertEqual(result['uplink_send']['n'], 1)
        self.assertEqual(result['uplink_send']['mean'], 20)
        self.assertEqual(result['downlink_first_text'], {'n': 0})


if __name__ == '__main__':
    unittest.main()
