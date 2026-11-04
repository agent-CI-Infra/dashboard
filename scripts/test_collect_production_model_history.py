import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('collector', Path(__file__).with_name('collect-production-model-history.py'))
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

class CollectionTests(unittest.TestCase):
    def test_publishes_only_aggregates_and_estimates_unknown_stream(self):
        rows = [{'model_group': 'test', 'model': 'provider/test', 'status': 'success', 'startTime': '2026-11-03T00:00:00+00:00', 'completionStartTime': '2026-11-03T00:00:01+00:00', 'endTime': '2026-11-03T00:00:02+00:00', 'request_duration_ms': 2000, 'prompt_tokens': 10, 'completion_tokens': 4, 'request_id': 'private-id', 'messages': 'private-content'}]
        with tempfile.TemporaryDirectory() as d, patch.object(collector, 'fetch_window', return_value=rows), patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY': 'dummy'}), patch.object(sys, 'argv', ['collector', d]), contextlib.redirect_stdout(io.StringIO()):
            collector.main()
            raw = (Path(d) / 'production-model-history.json').read_text(encoding='utf-8')
            point = next(iter(json.loads(raw)['cases'].values()))['points'][0]
            self.assertNotIn('private-id', raw)
            self.assertNotIn('private-content', raw)
            self.assertEqual(point['benchmark_models'][0]['ttft_avg_ms'], 1000)
            self.assertEqual(point['benchmark_models'][0]['tpot_avg_ms'], 333.33)
            self.assertEqual(point['benchmark_models'][0]['call_count'], 1)

    def test_excludes_failed_nonstreaming_and_invalid_timestamps(self):
        base = {'model_group': 'test', 'model': 'provider/test', 'status': 'success', 'startTime': '2026-11-03T00:00:00+00:00', 'completionStartTime': '2026-11-03T00:00:01+00:00', 'endTime': '2026-11-03T00:00:02+00:00', 'completion_tokens': 4}
        rows = [dict(base, request_id='a', stream=False), dict(base, request_id='b', status='failed'), dict(base, request_id='c', completionStartTime='2026-11-03T00:00:03+00:00'), dict(base, request_id='d', completion_tokens=1)]
        with tempfile.TemporaryDirectory() as d, patch.object(collector, 'fetch_window', return_value=rows), patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY': 'dummy'}), patch.object(sys, 'argv', ['collector', d]), contextlib.redirect_stdout(io.StringIO()):
            collector.main()
            doc = json.loads((Path(d) / 'production-model-history.json').read_text(encoding='utf-8'))
            metrics = next(iter(doc['cases'].values()))['points'][0]['benchmark_models'][0]
            self.assertEqual(metrics['ttft_sample_count'], 1)
            self.assertEqual(metrics['tpot_sample_count'], 0)

    def test_missing_tokens_and_unknown_status_are_not_zero_or_failure(self):
        rows = [{'model_group':'test','model':'provider/test','status':'unknown','startTime':'2026-11-03T00:00:00+00:00','endTime':'2026-11-03T00:00:01+00:00','prompt_tokens':None,'completion_tokens':3,'request_id':'a'}]
        with tempfile.TemporaryDirectory() as d, patch.object(collector, 'fetch_window', return_value=rows), patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY':'dummy'}), patch.object(sys, 'argv', ['collector', d]), contextlib.redirect_stdout(io.StringIO()):
            collector.main()
            point = next(iter(json.loads((Path(d)/'production-model-history.json').read_text(encoding='utf-8'))['cases'].values()))['points'][0]
            self.assertEqual(point['unknown_status_count'], 1)
            self.assertEqual(point['failed_queries'], 0)
            self.assertIsNone(point['benchmark_models'][0]['input_tokens'])
            self.assertIsNone(point['benchmark_models'][0]['total_tokens'])

    def test_rejects_short_capped_missing_or_duplicate_records(self):
        now = collector.datetime.now(collector.timezone.utc)
        pages = [
            {'total_pages':1,'total':2,'data':[{'request_id':'a'}]},
            {'total_pages':1,'total':1,'total_is_capped':True,'data':[{'request_id':'a'}]},
            {'total_pages':1,'total':1,'data':[{}]},
            {'total_pages':1,'total':2,'data':[{'request_id':'a'}, {'request_id':'a'}]},
        ]
        for page in pages:
            with self.subTest(page=page), patch.object(collector, 'fetch_json', return_value=page):
                with self.assertRaises(ValueError): collector.fetch_rows('dummy', now, now)

    def test_deduplicates_overlapping_daily_boundaries(self):
        now = collector.datetime.now(collector.timezone.utc)
        with patch.object(collector, 'fetch_rows', side_effect=[[{'request_id':'a'}, {'request_id':'shared'}], [{'request_id':'shared'}, {'request_id':'b'}]]):
            rows = collector.fetch_window('dummy', now, now + collector.timedelta(days=2), workers=1)
            self.assertEqual([r['request_id'] for r in rows], ['a','shared','b'])

    def test_failed_day_does_not_return_partial_data(self):
        now = collector.datetime.now(collector.timezone.utc)
        with patch.object(collector, 'fetch_rows', side_effect=[[{'request_id':'a'}], ValueError('incomplete')]):
            with self.assertRaises(ValueError):
                collector.fetch_window('dummy', now, now + collector.timedelta(days=2), workers=1)

    def test_rejects_changed_boundary_record(self):
        now = collector.datetime.now(collector.timezone.utc)
        with patch.object(collector, 'fetch_rows', side_effect=[[{'request_id':'a','status':'success'}], [{'request_id':'a','status':'failure'}]]):
            with self.assertRaises(ValueError):
                collector.fetch_window('dummy', now, now + collector.timedelta(days=2), workers=1)

    def test_rejects_changed_pagination(self):
        pages = [{'total_pages': 2, 'total': 2, 'data': [{'request_id':'a'}]}, {'total_pages': 3, 'total': 2, 'data': [{'request_id':'b'}]}]
        with patch.object(collector, 'fetch_json', side_effect=pages), patch.object(collector.time, 'sleep'):
            with self.assertRaises(ValueError):
                collector.fetch_rows('dummy', collector.datetime.now(collector.timezone.utc), collector.datetime.now(collector.timezone.utc))

    def test_merge_history_replaces_fresh_days_and_prunes_aged_points(self):
        today = collector.datetime.now(collector.SH).date()
        old = (today - collector.timedelta(days=5)).isoformat()
        stale = (today - collector.timedelta(days=collector.HISTORY_DAYS + 5)).isoformat()
        cutoff = (today - collector.timedelta(days=collector.HISTORY_DAYS - 1)).isoformat()
        previous = {
            'PROD-a': {'title': 'a', 'points': [{'date': stale, 'query_count': 1}, {'date': old, 'query_count': 2}]},
            'PROD-b': {'title': 'b', 'points': [{'date': stale, 'query_count': 3}]},
        }
        fresh = {'PROD-a': {'title': 'a', 'points': [{'date': old, 'query_count': 9}, {'date': today.isoformat(), 'query_count': 4}]}}
        merged = collector.merge_history(previous, fresh, cutoff)
        self.assertEqual(sorted(merged), ['PROD-a'])
        self.assertEqual([p['date'] for p in merged['PROD-a']['points']], [old, today.isoformat()])
        self.assertEqual(merged['PROD-a']['points'][0]['query_count'], 9)
        self.assertEqual(merged['PROD-a']['latest']['query_count'], 4)

    def test_main_merges_previous_snapshot_and_keeps_older_days(self):
        today = collector.datetime.now(collector.SH).date()
        yesterday = (today - collector.timedelta(days=1)).isoformat()
        today_text = today.isoformat()
        rows = [{'model_group': 'test', 'model': 'provider/test', 'status': 'success',
                 'startTime': f'{today_text}T00:00:00+08:00', 'completionStartTime': f'{today_text}T00:00:01+08:00',
                 'endTime': f'{today_text}T00:00:02+08:00', 'request_duration_ms': 2000,
                 'prompt_tokens': 10, 'completion_tokens': 4, 'request_id': 'fresh'}]
        previous = {'cases': {'PROD-kept': {'title': 'kept', 'points': [{'date': yesterday, 'query_count': 1}]}}}
        with tempfile.TemporaryDirectory() as d:
            previous_path = Path(d) / 'previous.json'
            previous_path.write_text(json.dumps(previous), encoding='utf-8')
            with patch.object(collector, 'fetch_window', return_value=rows), \
                    patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY': 'dummy'}), \
                    patch.object(sys, 'argv', ['collector', d, '2', str(previous_path)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                collector.main()
            doc = json.loads((Path(d) / 'production-model-history.json').read_text(encoding='utf-8'))
            self.assertIn('PROD-kept', doc['cases'])
            self.assertEqual([p['date'] for p in doc['cases']['PROD-kept']['points']], [yesterday])
            fresh = next(case for case_id, case in doc['cases'].items() if case_id != 'PROD-kept')
            self.assertEqual([p['date'] for p in fresh['points']], [today_text])

    def test_missing_previous_snapshot_rebuilds_the_full_window(self):
        today = collector.datetime.now(collector.SH).replace(hour=0, minute=0, second=0, microsecond=0)
        expected_start = (today - collector.timedelta(days=collector.HISTORY_DAYS - 1)).astimezone(collector.timezone.utc)
        with tempfile.TemporaryDirectory() as d, \
                patch.object(collector, 'fetch_window', return_value=[]) as fetch, \
                patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY': 'dummy'}), \
                patch.object(sys, 'argv', ['collector', d, '2']), \
                contextlib.redirect_stdout(io.StringIO()):
            collector.main()
        self.assertEqual(fetch.call_args.args[1], expected_start)


if __name__ == '__main__': unittest.main()
