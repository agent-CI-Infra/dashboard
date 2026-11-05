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

spec = importlib.util.spec_from_file_location('collector', Path(__file__).with_name('collect-client-observed-metrics.py'))
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

SEGMENTS = collector.SEGMENT_NAMES


def block(count, avg, p50=None, p95=None):
    return {'count': count, 'avg_ms': avg, 'p50_ms': p50, 'p95_ms': p95}


def summary(turns=10, completed=9, environment='client-claimed', ttft=None):
    return {
        'schema': 'client-observed-aggregate@1',
        'scope': 'client-observed',
        'generated_at_ms': 1_700_000_000_000,
        'window_days': 7,
        'turns': {'total': turns, 'completed': completed},
        'outcomes': {'completed': completed, 'failed': turns - completed},
        'networks': {'wifi': turns},
        'client_ttft': ttft or block(turns, 800.0, 700.0, 1500.0),
        'end_to_end': block(turns, 2400.0, 2000.0, 5000.0),
        'segments': {name: block(turns, 100.0, 90.0, 200.0) for name in SEGMENTS},
        'models': [
            {'model': 'gpt-5.2', 'calls': turns, 'succeeded': completed, 'streamed': turns,
             'ttft_ms': block(turns, 300.0, 280.0, 600.0),
             'tpot_ms': block(turns, 12.0, 11.0, 20.0),
             'input_tokens': turns * 100, 'output_tokens': turns * 20, 'total_tokens': turns * 120,
             'statuses': {'completed': completed}},
        ],
        'models_omitted': 0,
        'environment': environment,
        'trace_id': 'tr-secret',
        'request_id': 'req-secret',
        'apikey': 'secret-token',
    }


def deployments(*environments):
    return json.dumps([
        {'environment': environment, 'url': f'https://{environment}.example.test:20010', 'token': f'token-{environment}'}
        for environment in environments
    ])


def run_main(data_dir, fetch, listed, previous_path=None, days=None):
    arguments = ['collector', str(data_dir)]
    if days is not None:
        arguments.append(str(days))
    if previous_path is not None:
        arguments.append(str(days if days is not None else collector.DEFAULT_WINDOW_DAYS))
        arguments.append(str(previous_path))
    with patch.object(collector, 'fetch_summary', side_effect=fetch), \
            patch.dict(os.environ, {collector.DEPLOYMENTS_ENV: listed}), \
            patch.object(sys, 'argv', arguments), \
            contextlib.redirect_stdout(io.StringIO()):
        collector.main()
    return json.loads((Path(data_dir) / 'client-observed-metrics.json').read_text(encoding='utf-8'))


class CollectionTests(unittest.TestCase):
    def test_publishes_only_aggregates_and_never_identifiers_tokens_or_claimed_environment(self):
        with tempfile.TemporaryDirectory() as d:
            doc = run_main(d, lambda *_: summary(), deployments('adx-jiuwen-test-0'))
            raw = (Path(d) / 'client-observed-metrics.json').read_text(encoding='utf-8')
        for secret in ('tr-secret', 'req-secret', 'secret-token', 'token-adx-jiuwen-test-0', 'client-claimed'):
            self.assertNotIn(secret, raw)
        self.assertEqual(doc['schema'], 'client-observed-metrics@1')
        self.assertEqual(doc['scope'], 'client-observed')
        self.assertIs(doc['production_merged'], False)
        self.assertEqual([record['environment'] for record in doc['environments']], ['adx-jiuwen-test-0'])
        self.assertIs(doc['environments'][0]['stale'], False)

    def test_merges_environments_by_sum_and_weighted_average(self):
        pages = {
            'adx-jiuwen-test-0': summary(turns=10, completed=9, ttft=block(10, 800.0, 700.0, 1500.0)),
            'test-1': summary(turns=30, completed=30, ttft=block(30, 400.0, 380.0, 900.0)),
        }
        with tempfile.TemporaryDirectory() as d:
            doc = run_main(d, lambda url, token, days: pages[url.split('//')[1].split('.')[0]],
                           deployments('adx-jiuwen-test-0', 'test-1'))
        totals = doc['totals']
        self.assertEqual(totals['turns'], {'total': 40, 'completed': 39})
        self.assertEqual(totals['client_ttft']['count'], 40)
        self.assertEqual(totals['client_ttft']['avg_ms'], 500.0)
        self.assertIsNone(totals['client_ttft']['p95_ms'])
        self.assertEqual(totals['segments'][SEGMENTS[0]]['avg_ms'], 100.0)
        self.assertEqual(totals['models'][0]['calls'], 40)
        self.assertEqual(totals['models'][0]['ttft_ms']['avg_ms'], 300.0)

    def test_an_unreachable_environment_falls_back_to_its_previous_snapshot(self):
        previous = {
            'schema': 'client-observed-metrics@1',
            'scope': 'client-observed',
            'environments': [{'environment': 'test-1', 'turns': {'total': 3}, 'stale': False}],
        }
        with tempfile.TemporaryDirectory() as d:
            previous_path = Path(d) / 'previous.json'
            previous_path.write_text(json.dumps(previous), encoding='utf-8')

            def fetch(url, token, days):
                if 'test-1' in url:
                    raise collector.CollectorError('the deployment is unreachable (URLError)',
                                                   reason='unreachable')
                return summary()

            doc = run_main(d, fetch, deployments('adx-jiuwen-test-0', 'test-1'), previous_path)
        self.assertEqual([record['environment'] for record in doc['environments']], ['adx-jiuwen-test-0', 'test-1'])
        self.assertIs(doc['environments'][1]['stale'], True)
        self.assertEqual(doc['unavailable'], [{'environment': 'test-1', 'reason': 'unreachable'}])

    def test_failing_everything_does_not_replace_the_published_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'client-observed-metrics.json'
            target.write_text('{"existing": true}', encoding='utf-8')

            def down(*_):
                raise collector.CollectorError('down')

            with self.assertRaises(SystemExit):
                run_main(d, down, deployments('test-1'))
            self.assertEqual(target.read_text(encoding='utf-8'), '{"existing": true}')

    def test_a_removed_environment_is_not_resurrected_from_the_previous_snapshot(self):
        previous = {
            'schema': 'client-observed-metrics@1',
            'scope': 'client-observed',
            'environments': [{'environment': 'retired', 'turns': {'total': 3}}],
        }
        with tempfile.TemporaryDirectory() as d:
            previous_path = Path(d) / 'previous.json'
            previous_path.write_text(json.dumps(previous), encoding='utf-8')
            doc = run_main(d, lambda *_: summary(), deployments('adx-jiuwen-test-0'), previous_path)
        self.assertEqual([record['environment'] for record in doc['environments']], ['adx-jiuwen-test-0'])

    def test_rejects_unsafe_deployment_configuration(self):
        cases = [
            'not json',
            '[]',
            json.dumps([{'environment': 'bad label', 'url': 'https://a.test', 'token': 't'}]),
            json.dumps([{'environment': 'test-1', 'url': 'http://a.test', 'token': 't'}]),
            json.dumps([{'environment': 'test-1', 'url': 'https://a.test/?x=1', 'token': 't'}]),
            json.dumps([{'environment': 'test-1', 'url': 'https://a.test', 'token': ''}]),
            json.dumps([{'environment': 'test-1', 'url': 'https://user:pass@a.test', 'token': 't'}]),
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(collector.CollectorError):
                collector.load_deployments(text)

    def test_accepts_a_loopback_url_for_local_verification(self):
        deployments = collector.load_deployments(json.dumps([
            {'environment': 'local', 'url': 'http://127.0.0.1:8899/', 'token': 't'},
        ]))
        self.assertEqual(deployments[0]['url'], 'http://127.0.0.1:8899')

    def test_sanitize_summary_drops_unknown_models_and_extra_keys(self):
        payload = summary()
        payload['models'].append({'model': 'bad model\n', 'calls': 100})
        payload['models'].append({'model': 'x' * 200, 'calls': 100})
        payload['segments']['injected_stage'] = block(1, 1.0)
        clean = collector.sanitize_summary(payload)
        self.assertEqual([model['model'] for model in clean['models']], ['gpt-5.2'])
        self.assertEqual(set(clean['segments']), set(SEGMENTS))
        self.assertEqual(set(clean), {'window_days', 'turns', 'outcomes', 'networks', 'client_ttft',
                                      'end_to_end', 'segments', 'models', 'models_omitted'})

    def test_fetch_summary_maps_http_status_to_a_board_reason(self):
        import urllib.error
        cases = {
            403: 'unauthorized',
            404: 'not-configured',
            400: 'invalid',
        }
        for status, reason in cases.items():
            with self.subTest(status=status), \
                    patch.object(collector.urllib.request, 'urlopen',
                                 side_effect=urllib.error.HTTPError('https://a.test', status, 'x', {}, None)):
                with self.assertRaises(collector.CollectorError) as caught:
                    collector.fetch_summary('https://a.test', 'token', 7)
                self.assertEqual(caught.exception.reason, reason)
                self.assertNotIn('token', str(caught.exception))

    def test_rejects_a_summary_from_another_contract(self):
        payload = summary()
        payload['scope'] = 'classified-production'
        with self.assertRaises(collector.CollectorError):
            collector.sanitize_summary(payload)

    def test_an_unconfigured_secret_is_a_quiet_no_op(self):
        with tempfile.TemporaryDirectory() as d, \
                patch.dict(os.environ, {collector.DEPLOYMENTS_ENV: ''}), \
                patch.object(sys, 'argv', ['collector', d]), \
                contextlib.redirect_stdout(io.StringIO()):
            collector.main()
        self.assertFalse((Path(d) / 'client-observed-metrics.json').exists())


if __name__ == '__main__':
    unittest.main()
