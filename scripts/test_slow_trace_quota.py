import contextlib
import datetime as dt
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('quota_workspace', Path(__file__).with_name('collect-production-workspace-traces.py'))
traces = importlib.util.module_from_spec(spec)
spec.loader.exec_module(traces)


class SlowTraceQuotaTests(unittest.TestCase):
    def test_quota_rounds_up_and_never_exceeds_the_population(self):
        for population, expected in [(0, 0), (1, 1), (9, 9), (10, 10), (16, 10),
                                     (100, 10), (101, 11), (600, 60), (2000, 200)]:
            with self.subTest(population=population):
                self.assertEqual(traces.slow_trace_count(population), expected)

    def test_real_collector_selects_each_day_without_the_old_50_row_cap(self):
        today = dt.datetime.now(traces.CST).date()
        populations = [16, 8, 600, 2000]
        rows = []
        dates = []
        for offset, population in enumerate(populations):
            day = (today - dt.timedelta(days=offset)).isoformat()
            dates.append(day)
            rows.extend({'request_id': f'digitalmate-{offset}-{i}', 'date': day,
                         'start_time': f'{day}T01:00:00+08:00', 'gateway': i + 1,
                         'gateway_stream_status': 'normal'} for i in range(population))
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(traces, 'TOKEN', 'test'))
            stack.enter_context(patch.object(traces, 'binding_list', return_value=[{}]))
            stack.enter_context(patch.object(traces, 'read_log', return_value=({}, rows, 'ok', {})))
            stack.enter_context(patch.object(traces, 'attach_answer_estimates', return_value=True))
            native = stack.enter_context(patch.object(traces, 'attach_native_trajectories', return_value=0))
            stack.enter_context(patch.object(traces, 'attach_tool_events', return_value=True))
            result = traces.collect()
        self.assertEqual(result['slow_trace_sampling'], {'ratio': .1, 'minimum': 10, 'population': 'finished_gateway_requests'})
        by_day = {row['date']: row['anomaly']['latency'] for row in result['days']}
        for day, population, expected in zip(dates, populations, [10, 8, 60, 200]):
            with self.subTest(day=day):
                self.assertEqual(by_day[day]['n'], population)
                self.assertEqual(by_day[day]['slow_count'], expected)
                self.assertEqual([row['gateway'] for row in by_day[day]['slow_requests']],
                                 list(range(population, population - expected, -1)))
        self.assertEqual(sum(len(call.args[1]) for call in native.call_args_list), 100,
                         'session detail budget must not truncate the published daily list')
        daily = {row['date']: row['anomaly']['latency'] for row in result['request_daily_metrics']}
        self.assertEqual(daily[dates[0]]['slow_count'], 1, 'statistical anomaly tail remains 5%')
        self.assertEqual(daily[dates[-1]]['slow_count'], 100)


if __name__ == '__main__':
    unittest.main()
