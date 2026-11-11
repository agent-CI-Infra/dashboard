import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('local_traces', Path(__file__).with_name('collect-production-workspace-traces.py'))
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

class ProductionTraceTests(unittest.TestCase):
    def test_daily_metrics_use_every_finished_request_without_leaking_ids(self):
        rows = [(0, {}, {'gateway': 100.0, 'first_token': 20.0, 'request_id': 'secret-id'}),
                (1, {}, {'gateway': 300.0, 'first_token': 40.0, 'request_id': 'other-id'})]
        day = collector.request_daily_metrics(rows, '2026-11-10', '2026-11-11T01:00:00Z', True)
        self.assertEqual(day['gateway'], {'n': 2, 'mean': 200.0, 'p50': 100.0, 'p95': 300.0})
        self.assertEqual(day['first_token']['mean'], 30.0)
        self.assertEqual(day['answer_first'], {'n': 0})
        self.assertTrue(day['partial'])
        self.assertNotIn('secret-id', json.dumps(day))

    def test_log_parser_pairs_same_request_and_limits_fields(self):
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        start = now - dt.timedelta(seconds=10)
        stamp = lambda t: t.strftime('%Y-%m-%d %H:%M:%S.000')
        log = '\n'.join([
            f'{stamp(start)} [MessageHandler] dispatch: request_id=digitalmate-valid private prompt',
            f'{stamp(start + dt.timedelta(seconds=2))} [MessageHandler] process_stream chunk #1: request_id=digitalmate-valid event_type=chat.reasoning',
            f'{stamp(start + dt.timedelta(seconds=7))} [MessageHandler] process_stream chunk #2: request_id=digitalmate-valid event_type=chat.delta',
            f'{stamp(now)} [MessageHandler] Stream done request_id=digitalmate-valid total_chunks=2',
            f'{stamp(start)} [MessageHandler] dispatch: request_id=digitalmate-unfinished',
        ])
        with patch.object(collector, 'query_for', return_value={}), \
             patch.object(collector, 'listing', return_value=[{'name':'process.log','type':'file','size':len(log)}]), \
             patch.object(collector, 'download_log', return_value=iter(log.splitlines())):
            _, rows, status, usage = collector.read_log({})
        self.assertEqual(status, 'ok')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['gateway'], 10000)
        self.assertEqual(rows[0]['react'], 5000)
        self.assertNotIn('private prompt', json.dumps(rows))
        self.assertEqual(usage, {})

    def test_usage_records_and_gateway_outcomes_have_distinct_scopes(self):
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        stamp = lambda t: t.strftime('%Y-%m-%d %H:%M:%S.000')
        log = [
            f"{stamp(now)} [JiuWenSwarmDeepAdapter] llm_usage chunk: type='llm_usage' payload='PRIVATE'",
            f"{stamp(now)} tool output mentions llm_usage but is not a usage event",
            f'{stamp(now)} [MessageHandler] dispatch: request_id=digitalmate-a',
            f'{stamp(now)} [MessageHandler] Stream 异常: request_id=digitalmate-a total_chunks=1 error=PRIVATE',
            f'{stamp(now)} [MessageHandler] dispatch: request_id=digitalmate-b',
            f'{stamp(now)} [MessageHandler] Stream 正常完成: request_id=digitalmate-b total_chunks=1',
            f'{stamp(now)} [MessageHandler] dispatch: request_id=digitalmate-c',
            f'{stamp(now)} [MessageHandler] Stream 被取消: request_id=digitalmate-c total_chunks=1',
        ]
        with patch.object(collector, 'query_for', return_value={}), \
             patch.object(collector, 'listing', return_value=[{'name':'process.log','type':'file','size':100}]), \
             patch.object(collector, 'download_log', return_value=iter(log)):
            _, rows, status, usage = collector.read_log({})
        self.assertEqual(status, 'ok')
        self.assertEqual(sum(usage.values()), 1)
        date = rows[0]['date']
        day = collector.request_daily_metrics([(0, {}, row) for row in rows], date, now.isoformat(), False, usage[date])
        self.assertEqual(day['model_usage_events'], 1)
        self.assertEqual(day['gateway_stream'], {'normal': 1, 'exception': 1, 'cancelled': 1,
                                                'unknown': 0, 'observed': 2, 'population': 3, 'exception_rate': 0.5})
        self.assertNotIn('PRIVATE', json.dumps(day))
        self.assertNotIn('failure_rate', day)

    def test_unknown_gateway_outcomes_do_not_become_zero_failure(self):
        row = {'gateway': 10, 'gateway_stream_status': 'cancelled'}
        day = collector.request_daily_metrics([(0, {}, row)], '2026-11-10', '2026-11-11T00:00:00Z', True)
        self.assertIsNone(day['gateway_stream']['exception_rate'])

    def test_streamed_log_must_match_advertised_length(self):
        class Response(io.BytesIO):
            headers = {'Content-Length': '10'}
        with patch.object(collector.OPENER, 'open', return_value=Response(b'abc\n')):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                list(collector.download_log({}))

    def test_history_pairs_exact_request_and_drops_private_fields(self):
        start = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        row = {'request_id':'digitalmate-a','gateway':10000,'start_time':start.isoformat()}
        event = lambda rid, ts, typ, **fields: {'request_id':rid,'timestamp':start.timestamp()+ts,'event_type':typ,**fields}
        records = [
            event('digitalmate-a', 1, 'chat.tool_call', tool_call={'tool_call_id':'1','name':'bash','arguments':{'command':'PRIVATE_COMMAND'}}),
            event('digitalmate-b', 2, 'chat.tool_result', tool_call_id='1', success=False, result='PRIVATE_OTHER'),
            event('digitalmate-a', 3, 'chat.tool_result', tool_call_id='1', success=False, result='PRIVATE_RESULT'),
        ]
        with patch.object(collector,'listing',return_value=[{'type':'directory','name':'session'}]), \
             patch.object(collector,'fetch',return_value='\n'.join(map(json.dumps,records))):
            collector.attach_tool_events({},[row])
        self.assertEqual(row['tool_events'],[{'name':'bash','start_ms':1000.0,'duration_ms':2000.0,'status':'error'}])
        self.assertNotIn('PRIVATE_',json.dumps(row))
        self.assertFalse(row['trace_complete'])

if __name__ == '__main__':
    unittest.main()
