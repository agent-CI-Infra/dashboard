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


traces = load('anomaly_workspace', 'collect-production-workspace-traces.py')
remote = load('anomaly_remote', 'kube-workspace-remote.py')
cloud = load('anomaly_cloud', 'collect-cloud-request-metrics.py')


class ProductionAnomalyTests(unittest.TestCase):
    today = dt.date(2026, 11, 19)

    def records(self, durations, **extra):
        return [(0, {}, {'request_id': f'digitalmate-{index}', 'gateway': value,
                          'first_token': value / 4, 'answer_first': value / 2,
                          'react': value / 4, **extra}) for index, value in enumerate(durations)]

    def test_partial_workspace_coverage_keeps_a_labelled_historical_baseline(self):
        grouped = {'2026-11-17': self.records([100] * 20), '2026-11-18': self.records([100, 1000])}
        result = traces.request_anomalies(grouped, {}, 'now', True, self.today)['2026-11-18']
        self.assertTrue(result['available'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['baseline']['scope'], 'readable_logs')
        self.assertEqual(result['metrics']['gateway']['baseline_p95'], 100)
        self.assertEqual(result['metrics']['gateway']['outliers'], 1)
        self.assertEqual(result['metrics']['gateway']['rate'], .5)
        self.assertEqual(result['cohort']['driver'], 'answer_tail')
        self.assertEqual(result['cohort']['phases']['answer_tail']['outlier']['mean'], 500)

    def test_baseline_excludes_current_day_and_samples_older_than_seven_days(self):
        grouped = {'2026-11-01': self.records([1] * 100), '2026-11-18': self.records([100] * 19),
                   '2026-11-19': self.records([10000] * 100)}
        result = traces.request_anomalies(grouped, {}, 'now', False, self.today)
        self.assertFalse(result['2026-11-18']['available'])
        self.assertFalse(result['2026-11-19']['available'])
        self.assertEqual(result['2026-11-19']['metrics']['gateway']['baseline_n'], 19)
        self.assertEqual(result['2026-11-19']['latency']['max_ms'], 10000)

    def test_old_days_publish_no_request_identifiers(self):
        grouped = {'2026-11-01': self.records([500], request_id='digitalmate-PRIVATE')}
        result = traces.request_anomalies(grouped, {}, 'now', False, self.today)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(result['2026-11-01']['latency']['max_ms'], 500)
        self.assertFalse(result['2026-11-01']['latency']['details_retained'])

    def test_timeout_events_require_error_markers_and_deduplicate_request_ids(self):
        usage = {}
        for line in ['[MessageHandler] dispatch: request_id=digitalmate-a prompt=timeout',
                     '[MessageHandler] Stream 异常: request_id=digitalmate-a error=TimeoutError PRIVATE',
                     '[llm_call_error] request_id=digitalmate-a error=timed out PRIVATE',
                     '[MessageHandler] Stream 异常: request_id=digitalmate-b error=budget exceeded']:
            traces.record_timeout(usage, line)
        self.assertEqual(usage['timeout_events'], 2)
        self.assertEqual(list(usage['timeout_requests']), ['digitalmate-a'])
        self.assertNotIn('PRIVATE', json.dumps(usage))
        traces.record_timeout(usage, '[llm_call_error] error=deadline exceeded PRIVATE')
        self.assertEqual(usage['timeout_unlinked'], 1)
        day = traces.request_anomalies({}, {'2026-11-19': {'timeout_count': 1, 'timeout_unlinked': 1,
                'timeout_events': 3}}, 'now', False, self.today)['2026-11-19']
        self.assertIsNone(day['timeouts']['count'])
        self.assertEqual(day['timeouts']['identified'], 1)
        self.assertEqual(day['timeouts']['records'], 3)

    def test_invalid_phase_is_missing_and_zero_is_a_valid_sample(self):
        grouped = {'2026-11-17': self.records([100] * 20),
                   '2026-11-18': self.records([1000], first_token=float('nan'), answer_first=2000, react=True)}
        result = traces.request_anomalies(grouped, {}, 'now', False, self.today)['2026-11-18']
        self.assertEqual(result['metrics']['first_token']['n'], 0)
        self.assertEqual(result['cohort']['phases']['answer_tail']['outlier']['n'], 0)
        self.assertNotIn('NaN', json.dumps(result))
        self.assertEqual(traces.anomaly_value({'gateway': 0, 'first_token': 0}, 'first_token'), 0)

    def test_fresh_anomaly_survives_a_larger_historical_latency_population(self):
        previous = {'days': [{'date': '2026-11-19', 'n': 100, 'gateway': {'n': 100}}]}
        usage = traces.new_usage()
        day = traces.request_daily_metrics([], '2026-11-19', 'now', False, usage)
        day['anomaly'] = {'available': True, 'metrics': {'gateway': {'n': 10, 'outliers': 2}}}
        result = cloud.with_workspace_traces(previous, {'schema': 'digitalmate/cloud-traces@1',
            'read_log_count': 1, 'request_daily_metrics': [day]})
        self.assertEqual(result['days'][0]['n'], 100)
        self.assertEqual(result['days'][0]['anomaly']['metrics']['gateway']['n'], 10)
        self.assertEqual(result['anomaly_version'], 3)

    def test_actual_remote_collector_publishes_anomalies_and_timeout_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); name = 'w-' + 'a' * 64
            work = root / name / 'workswarm'; work.mkdir(parents=True)
            yesterday = dt.datetime.now(dt.timezone.utc).replace(hour=1, minute=0, second=0, microsecond=0) - dt.timedelta(days=1)
            lines = []
            for offset, count in [(-1, 20), (0, 2)]:
                for index in range(count):
                    start = yesterday + dt.timedelta(days=offset, seconds=index * 10)
                    duration = 5 if offset == 0 and index == 1 else 1
                    rid = f'digitalmate-{offset}-{index}'
                    lines.append(f'{start.strftime("%Y-%m-%d %H:%M:%S.000")} [MessageHandler] dispatch: request_id={rid}')
                    end = start + dt.timedelta(seconds=duration)
                    marker = '异常' if duration == 5 else '正常完成'
                    lines.append(f'{end.strftime("%Y-%m-%d %H:%M:%S.000")} [MessageHandler] Stream {marker}: request_id={rid} total_chunks=1 error={"TimeoutError PRIVATE" if duration == 5 else "none"}')
            (work / 'process.log').write_text('\n'.join(lines), encoding='utf8')
            with patch.object(remote, 'ROOT', root):
                result = remote.run_collector([name], Path(traces.__file__).read_text(encoding='utf8'))
            day = result['request_daily_metrics'][-1]
            self.assertEqual(day['anomaly']['metrics']['gateway']['outliers'], 1)
            self.assertEqual(day['anomaly']['timeouts']['count'], 1)
            self.assertEqual(day['anomaly']['timeouts']['requests'][0]['gateway'], 5000)
            self.assertNotIn('PRIVATE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
