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
    def test_collection_connects_bounded_tool_samples_to_request_day(self):
        today = dt.datetime.now(collector.CST).date().isoformat()
        row = {'date': today, 'request_id': 'digitalmate-test', 'gateway': 100.0}
        metrics = {today: {'values': [10.0, 30.0], 'failed': 1, 'unknown': 0,
                           'observed': 2, 'matched': 2, 'incomplete': 0}}
        with patch.object(collector, 'TOKEN', 'test'), \
             patch.object(collector, 'binding_list', return_value=[{}]), \
             patch.object(collector, 'read_log', return_value=({}, [row], 'ok')), \
             patch.object(collector, 'read_tool_metrics', return_value=(metrics, True)), \
             patch.object(collector, 'attach_tool_events', return_value=True):
            result = collector.collect()
        day = result['request_daily_metrics'][0]
        self.assertEqual(day['tool']['mean'], 20.0)
        self.assertEqual(day['tool_failed'], 1)
        self.assertEqual(day['tool_coverage']['matched'], 2)
        self.assertTrue(day['tool_coverage']['partial'])
        self.assertNotIn('digitalmate-test', json.dumps(day))

    def test_tool_files_pool_samples_without_leaking_ids_and_track_partial_coverage(self):
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        records = [
            {'t': now, 'request_id': 'private-request', 'call_key': 'a' * 64, 'ms': 10, 'status': 'done'},
            {'t': now, 'request_id': 'private-request', 'call_key': 'b' * 64, 'ms': 30, 'status': 'error'},
            {'t': now, 'type': 'coverage', 'observed_calls': 3, 'matched': 2,
             'unmatched_events': 1, 'open_calls': 0},
        ]
        content = '\n'.join(map(json.dumps, records))
        with patch.object(collector, 'listing', return_value=[{'name': 'tool-calls-2026-11-11.jsonl',
                                                               'type': 'file', 'size': len(content)}]), \
             patch.object(collector, 'fetch', return_value=content):
            days, complete = collector.read_tool_metrics({})
        self.assertTrue(complete)
        summary = collector.summarize_tool_metrics(list(days.values()), not complete)
        self.assertEqual(summary['tool'], {'n': 2, 'mean': 20.0, 'p50': 10.0, 'p95': 30.0})
        self.assertEqual(summary['tool_failed'], 1)
        self.assertEqual(summary['tool_coverage']['observed_calls'], 3)
        self.assertTrue(summary['tool_coverage']['partial'])
        self.assertNotIn('private-request', json.dumps(summary))
        self.assertTrue(collector.summarize_tool_metrics(list(days.values()), True)['tool_coverage']['partial'])

    def test_duplicate_call_identity_is_excluded_and_marks_incomplete(self):
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        sample = {'t': now, 'call_key': 'c' * 64, 'ms': 10, 'status': 'done'}
        content = '\n'.join(json.dumps(sample) for _ in range(2))
        with patch.object(collector, 'listing', return_value=[{'name': 'tool-calls-2026-11-11.jsonl',
                                                               'type': 'file', 'size': len(content)}]), \
             patch.object(collector, 'fetch', return_value=content):
            days, complete = collector.read_tool_metrics({})
        self.assertFalse(complete)
        self.assertEqual(collector.summarize_tool_metrics(list(days.values()), not complete)['tool']['n'], 0)

    def test_absent_or_oversized_tool_files_do_not_invent_samples(self):
        with patch.object(collector, 'listing', return_value=[]):
            self.assertEqual(collector.read_tool_metrics({}), ({}, False))
        with patch.object(collector, 'listing', return_value=[{'name': 'tool-calls-2026-11-11.jsonl',
                                                               'type': 'file', 'size': collector.MAX_TOOL_FILE + 1}]), \
             patch.object(collector, 'fetch') as fetch:
            self.assertEqual(collector.read_tool_metrics({}), ({}, False))
            fetch.assert_not_called()

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
            _, rows, status = collector.read_log({})
        self.assertEqual(status, 'ok')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['gateway'], 10000)
        self.assertEqual(rows[0]['react'], 5000)
        self.assertNotIn('private prompt', json.dumps(rows))

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

    def test_history_does_not_pair_across_sessions_or_duplicate_starts(self):
        start = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        row = {'request_id': 'digitalmate-a', 'gateway': 10000, 'start_time': start.isoformat()}
        event = lambda ts, typ, **fields: {'request_id': 'digitalmate-a', 'timestamp': start.timestamp()+ts,
                                          'event_type': typ, **fields}
        histories = {
            'one': [event(1, 'chat.tool_call', tool_call={'tool_call_id': 'cross', 'name': 'bash'}),
                    event(2, 'chat.tool_call', tool_call={'tool_call_id': 'dup', 'name': 'bash'}),
                    event(3, 'chat.tool_call', tool_call={'tool_call_id': 'dup', 'name': 'bash'}),
                    event(4, 'chat.tool_result', tool_call_id='dup', success=True),
                    event(5, 'chat.tool_call', tool_call={'tool_call_id': 'good', 'name': 'bash'}),
                    event(6, 'chat.tool_result', tool_call_id='good')],
            'two': [event(7, 'chat.tool_result', tool_call_id='cross', success=True)],
        }
        def fetch(_path, params, **_kwargs):
            name = params['path'].split('/')[-2]
            return '\n'.join(map(json.dumps, histories[name]))
        with patch.object(collector, 'listing', return_value=[{'type': 'directory', 'name': 'one'},
                                                               {'type': 'directory', 'name': 'two'}]), \
             patch.object(collector, 'fetch', side_effect=fetch):
            collector.attach_tool_events({}, [row])
        self.assertEqual(row['tool_events'], [{'name': 'bash', 'start_ms': 5000.0,
                                               'duration_ms': 1000.0, 'status': 'unknown'}])

if __name__ == '__main__':
    unittest.main()
