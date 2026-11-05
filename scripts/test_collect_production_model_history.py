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
    def test_publishes_only_aggregates_and_reports_unknown_stream_as_estimate(self):
        rows = [{'model_group': 'test', 'model': 'provider/test', 'status': 'success', 'startTime': '2026-11-03T00:00:00+00:00', 'completionStartTime': '2026-11-03T00:00:01+00:00', 'endTime': '2026-11-03T00:00:02+00:00', 'request_duration_ms': 2000, 'prompt_tokens': 10, 'completion_tokens': 4, 'request_id': 'private-id', 'messages': 'private-content'}]
        with tempfile.TemporaryDirectory() as d, patch.object(collector, 'fetch_window', return_value=rows), patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY': 'dummy'}), patch.object(sys, 'argv', ['collector', d]), contextlib.redirect_stdout(io.StringIO()):
            collector.main()
            raw = (Path(d) / 'production-model-history.json').read_text(encoding='utf-8')
            point = next(iter(json.loads(raw)['cases'].values()))['points'][0]
            metrics = point['benchmark_models'][0]
            self.assertNotIn('private-id', raw)
            self.assertNotIn('private-content', raw)
            # Unknown streaming is never mixed into the strict TTFT mean.
            self.assertIsNone(metrics['ttft_avg_ms'])
            self.assertEqual(metrics['ttft_sample_count'], 0)
            self.assertEqual(metrics['ttft_estimated_avg_ms'], 1000)
            self.assertEqual(metrics['ttft_estimated_sample_count'], 1)
            self.assertIsNone(metrics['tpot_avg_ms'])
            self.assertEqual(metrics['tpot_sample_count'], 0)
            self.assertEqual(metrics['call_count'], 1)
            self.assertEqual(point['duration_sum_ms'], 2000)
            self.assertEqual(point['duration_sample_count'], 1)
            self.assertEqual(point['success_count'], 1)
            self.assertEqual(point['hours'][0]['duration_sum_ms'], 2000)
            # The model row carries its own sum so the dashboard can weight it
            # against other models without averaging daily means.
            self.assertEqual(metrics['duration_sum_ms'], 2000)
            self.assertEqual(metrics['duration_sample_count'], 1)

    def test_strict_stream_ttft_filters_timestamp_artefacts(self):
        base = {'model_group': 'test', 'model': 'provider/test', 'status': 'success', 'stream': True,
                'startTime': '2026-11-03T00:00:00+00:00', 'completionStartTime': '2026-11-03T00:00:01+00:00',
                'endTime': '2026-11-03T00:00:02+00:00', 'request_duration_ms': 2000, 'completion_tokens': 11}
        rows = [
            dict(base, request_id='strict'),
            dict(base, request_id='nonstream', stream=False),
            dict(base, request_id='failed', status='failed'),
            # TTFT within 5% of the whole duration: a completion timestamp, not a first token.
            dict(base, request_id='ttft-artefact', completionStartTime='2026-11-03T00:00:01.999000+00:00'),
            # Implausibly small TPOT is a timestamp artefact too.
            dict(base, request_id='tpot-artefact', completion_tokens=1000000),
        ]
        with tempfile.TemporaryDirectory() as d, patch.object(collector, 'fetch_window', return_value=rows), patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY': 'dummy'}), patch.object(sys, 'argv', ['collector', d]), contextlib.redirect_stdout(io.StringIO()):
            collector.main()
            point = next(iter(json.loads((Path(d) / 'production-model-history.json').read_text(encoding='utf-8'))['cases'].values()))['points'][0]
            metrics = point['benchmark_models'][0]
            self.assertEqual(metrics['ttft_sample_count'], 2)
            self.assertEqual(metrics['ttft_filtered_count'], 1)
            self.assertEqual(metrics['tpot_filtered_count'], 2)
            self.assertEqual(metrics['tpot_sample_count'], 1)

    def test_near_end_ttft_excludes_tpot_even_above_minimum(self):
        row = {'status': 'success', 'stream': True,
               'startTime': '2026-11-03T00:00:00Z',
               'completionStartTime': '2026-11-03T00:00:09.9Z',
               'endTime': '2026-11-03T00:00:10Z',
               'request_duration_ms': 10000, 'completion_tokens': 3}
        metrics = collector.aggregate_point([row], '2026-11-03', 'model')['benchmark_models'][0]
        self.assertEqual(metrics['ttft_filtered_count'], 1)
        self.assertEqual(metrics['tpot_filtered_count'], 1)
        self.assertEqual(metrics['tpot_sample_count'], 0)
        self.assertIsNone(metrics['tpot_avg_ms'])

    def test_conflicting_stream_flags_are_unknown(self):
        row = {'stream': True, 'metadata': {'spend_logs_metadata': {'stream': False}}}
        self.assertIsNone(collector.stream_state(row))

    def test_unknown_status_cannot_be_counted_as_passed(self):
        point = collector.aggregate_point([{'status': 'unknown'}], '2026-11-03', 'model')
        self.assertEqual(point['status'], 'UNKNOWN')

    def test_tokens_count_successes_and_failed_attempts_separately(self):
        rows = [
            {'model_group': 'test', 'model': 'provider/test', 'status': 'success', 'request_id': 'ok',
             'startTime': '2026-11-03T00:00:00+00:00', 'endTime': '2026-11-03T00:00:01+00:00',
             'request_duration_ms': 1000, 'prompt_tokens': 10, 'completion_tokens': 4},
            {'model_group': 'test', 'model': 'provider/test', 'status': 'success', 'request_id': 'partial',
             'startTime': '2026-11-03T00:00:00+00:00', 'endTime': '2026-11-03T00:00:01+00:00',
             'request_duration_ms': 1000, 'prompt_tokens': 7, 'completion_tokens': None},
            {'model_group': 'test', 'model': 'provider/test', 'status': 'failed', 'request_id': 'bad',
             'startTime': '2026-11-03T00:00:00+00:00', 'endTime': '2026-11-03T00:00:01+00:00',
             'request_duration_ms': 1000, 'prompt_tokens': 100, 'completion_tokens': 50},
        ]
        with tempfile.TemporaryDirectory() as d, patch.object(collector, 'fetch_window', return_value=rows), patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY': 'dummy'}), patch.object(sys, 'argv', ['collector', d]), contextlib.redirect_stdout(io.StringIO()):
            collector.main()
            point = next(iter(json.loads((Path(d) / 'production-model-history.json').read_text(encoding='utf-8'))['cases'].values()))['points'][0]
            metrics = point['benchmark_models'][0]
            self.assertEqual(metrics['input_tokens'], 10)
            self.assertEqual(metrics['output_tokens'], 4)
            self.assertEqual(metrics['total_tokens'], 14)
            self.assertEqual(metrics['token_sample_count'], 1)
            self.assertEqual(metrics['token_coverage'], 0.5)
            self.assertEqual(metrics['failed_input_tokens'], 100)
            self.assertEqual(metrics['failed_total_tokens'], 150)
            self.assertEqual(metrics['failed_token_sample_count'], 1)

    def test_request_level_p50_and_p95_need_twenty_samples(self):
        rows = []
        for index in range(20):
            rows.append({'model_group': 'test', 'model': 'provider/test', 'status': 'success',
                         'request_id': f'r{index}', 'startTime': f'2026-11-03T00:00:{index:02d}+00:00',
                         'endTime': '2026-11-03T00:10:00+00:00', 'request_duration_ms': (index + 1) * 100})
        with tempfile.TemporaryDirectory() as d, patch.object(collector, 'fetch_window', return_value=rows), patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY': 'dummy'}), patch.object(sys, 'argv', ['collector', d]), contextlib.redirect_stdout(io.StringIO()):
            collector.main()
            point = next(iter(json.loads((Path(d) / 'production-model-history.json').read_text(encoding='utf-8'))['cases'].values()))['points'][0]
            self.assertEqual(point['duration_sum_ms'], 21000)
            self.assertEqual(point['duration_p50_ms'], 1000)
            self.assertEqual(point['duration_p95_ms'], 1900)

    def test_excludes_failed_nonstreaming_and_invalid_timestamps(self):
        base = {'model_group': 'test', 'model': 'provider/test', 'status': 'success', 'stream': True, 'startTime': '2026-11-03T00:00:00+00:00', 'completionStartTime': '2026-11-03T00:00:01+00:00', 'endTime': '2026-11-03T00:00:02+00:00', 'completion_tokens': 4}
        rows = [dict(base, request_id='a'), dict(base, request_id='b', stream=False), dict(base, request_id='c', completionStartTime='2026-11-03T00:00:03+00:00'), dict(base, request_id='d', completion_tokens=1)]
        rows.append(dict(base, request_id='e', status='failed'))
        with tempfile.TemporaryDirectory() as d, patch.object(collector, 'fetch_window', return_value=rows), patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY': 'dummy'}), patch.object(sys, 'argv', ['collector', d]), contextlib.redirect_stdout(io.StringIO()):
            collector.main()
            doc = json.loads((Path(d) / 'production-model-history.json').read_text(encoding='utf-8'))
            metrics = next(iter(doc['cases'].values()))['points'][0]['benchmark_models'][0]
            # 'a' and 'd' have usable timestamps; 'b' is non-streaming, 'c' is
            # out of order and 'e' failed. Only 'a' can produce a TPOT.
            self.assertEqual(metrics['ttft_sample_count'], 2)
            self.assertEqual(metrics['tpot_sample_count'], 1)

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
        previous = {'metrics_version': collector.METRICS_VERSION, 'cases': {'PROD-kept': {'title': 'kept', 'points': [{'date': yesterday, 'query_count': 1}]}}}
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

    def test_legacy_previous_snapshot_triggers_a_full_rebuild(self):
        today = collector.datetime.now(collector.SH).replace(hour=0, minute=0, second=0, microsecond=0)
        expected_start = (today - collector.timedelta(days=collector.HISTORY_DAYS - 1)).astimezone(collector.timezone.utc)
        yesterday = (today - collector.timedelta(days=1)).date().isoformat()
        previous = {'cases': {'PROD-kept': {'title': 'kept', 'points': [{'date': yesterday, 'query_count': 1}]}}}
        with tempfile.TemporaryDirectory() as d:
            previous_path = Path(d) / 'previous.json'
            previous_path.write_text(json.dumps(previous), encoding='utf-8')
            with patch.object(collector, 'fetch_window', return_value=[]) as fetch, \
                    patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY': 'dummy'}), \
                    patch.object(sys, 'argv', ['collector', d, '2', str(previous_path)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                collector.main()
            doc = json.loads((Path(d) / 'production-model-history.json').read_text(encoding='utf-8'))
        self.assertEqual(fetch.call_args.args[1], expected_start)
        self.assertNotIn('PROD-kept', doc['cases'])
        self.assertEqual(doc['metrics_version'], collector.METRICS_VERSION)
        self.assertEqual(doc['complete_through'], (today - collector.timedelta(days=1)).date().isoformat())
        self.assertEqual(doc['partial_day'], today.date().isoformat())

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
