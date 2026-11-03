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
    def test_publishes_only_aggregates_and_requires_stream_evidence(self):
        rows = [{'model_group': 'test', 'model': 'provider/test', 'status': 'success', 'startTime': '2026-11-03T00:00:00+00:00', 'completionStartTime': '2026-11-03T00:00:01+00:00', 'endTime': '2026-11-03T00:00:02+00:00', 'request_duration_ms': 2000, 'prompt_tokens': 10, 'completion_tokens': 4, 'request_id': 'private-id', 'messages': 'private-content'}]
        with tempfile.TemporaryDirectory() as d, patch.object(collector, 'fetch_rows', return_value=rows), patch.dict(os.environ, {'PRODUCTION_GATEWAY_KEY': 'dummy'}), patch.object(sys, 'argv', ['collector', d]), contextlib.redirect_stdout(io.StringIO()):
            collector.main()
            raw = (Path(d) / 'production-model-history.json').read_text(encoding='utf-8')
            point = next(iter(json.loads(raw)['cases'].values()))['points'][0]
            self.assertNotIn('private-id', raw)
            self.assertNotIn('private-content', raw)
            self.assertIsNone(point['benchmark_models'][0]['ttft_avg_ms'])
            self.assertIsNone(point['benchmark_models'][0]['tpot_avg_ms'])
            self.assertEqual(point['benchmark_models'][0]['call_count'], 1)

    def test_rejects_changed_pagination(self):
        pages = [{'total_pages': 2, 'data': [{}]}, {'total_pages': 3, 'data': [{}]}]
        with patch.object(collector, 'fetch_json', side_effect=pages), patch.object(collector.time, 'sleep'):
            with self.assertRaises(ValueError):
                collector.fetch_rows('dummy', collector.datetime.now(collector.timezone.utc), collector.datetime.now(collector.timezone.utc))

if __name__ == '__main__': unittest.main()
