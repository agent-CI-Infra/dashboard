from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import collect_with_oidc as client
import tempfile


class OIDCCollectionTests(unittest.TestCase):
    def setUp(self):
        source, deployment = next(iter(json.loads((client.ROOT / 'board-config.json').read_text())['deployments'].items()))
        self.source, self.target = source, deployment['repository']
        self.env = {'GITHUB_REPOSITORY': self.target, 'SDBOT_TOKEN_BROKER_URL': 'https://broker.example/actions/token',
                    'SDBOT_TOKEN_AUDIENCE': 'test-audience', 'ACTIONS_ID_TOKEN_REQUEST_URL': 'https://issuer.example/oidc?x=1',
                    'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'runner-auth', 'SDBOT_GITHUB_APP_PRIVATE_KEY': 'must-not-inherit'}
        self.calls = []
        (client.ROOT / '.tmp').mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=client.ROOT / '.tmp')
        self.addCleanup(temp.cleanup)
        for name, file in [('SYNC_RECORDS', 'gitcode-sync.json'), ('RUN_CHANGES', 'run-changes.json'), ('RUN_RECEIPT', 'receipt.json')]:
            patcher = patch.object(client, name, Path(temp.name) / file)
            patcher.start(); self.addCleanup(patcher.stop)

    def request(self, url, bearer, body=None, method='GET', **kwargs):
        self.calls.append((url, bearer, body, method))
        if url.startswith('https://issuer.example/'):
            self.assertIn('audience=test-audience', url)
            self.assertEqual(bearer, 'runner-auth')
            return {'value': 'oidc-identity'}
        if method == 'DELETE':
            return None
        self.assertEqual(bearer, 'oidc-identity')
        if url == 'https://broker.example/actions/gitcode-sync':
            self.assertEqual((body, method), ({}, 'POST'))
            return {'ok': True, 'enabled': True, 'source': self.source, 'target': 'openJiuwen/sciencediscovery', 'records': [], 'pulls': []}
        if url == 'https://broker.example/actions/reruns':
            return {'ok': True, 'source': self.source, 'runs': []}
        if url == 'https://broker.example/actions/reruns/ack':
            return {'ok': True}
        purpose = body['purpose']
        return {'token': purpose + '-token', 'repository': self.source if purpose == 'source' else self.target,
                'purpose': purpose, 'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}

    def test_scoped_tokens_only_reach_collector_and_are_revoked_on_exit(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(client, 'request_json', side_effect=self.request), \
             patch.object(client, 'mask') as mask, patch.object(client.subprocess, 'run', return_value=SimpleNamespace(returncode=7)) as run:
            self.assertEqual(client.main(), 7)
        env = run.call_args.kwargs['env']
        self.assertEqual(env['GITHUB_TOKEN'], 'source-token')
        self.assertEqual(env['GSB_PUBLISH_TOKEN'], 'target-token')
        self.assertNotIn('ACTIONS_ID_TOKEN_REQUEST_TOKEN', env)
        self.assertNotIn('ACTIONS_ID_TOKEN_REQUEST_URL', env)
        self.assertNotIn('SDBOT_GITHUB_APP_PRIVATE_KEY', env)
        self.assertEqual([c[1] for c in self.calls if c[3] == 'DELETE'], ['source-token', 'target-token'])
        self.assertEqual(mask.call_count, 3)

    def test_partial_exchange_failure_revokes_source_and_never_collects(self):
        def request(url, bearer, body=None, method='GET'):
            if body == {'purpose': 'target'}:
                raise client.CredentialError('sensitive response must not appear')
            return self.request(url, bearer, body, method)
        output = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.object(client, 'request_json', side_effect=request), \
             patch.object(client, 'mask'), patch.object(client.subprocess, 'run') as run, patch('sys.stdout', output):
            self.assertEqual(client.main(), 1)
        run.assert_not_called()
        self.assertEqual([c[1] for c in self.calls if c[3] == 'DELETE'], ['source-token'])
        self.assertNotIn('sensitive response', output.getvalue())

    def test_invalid_broker_and_cross_source_fail_before_credentials(self):
        for override in [{'SDBOT_TOKEN_BROKER_URL': 'http://broker.example/actions/token'},
                         {'SDBOT_TOKEN_BROKER_URL': 'https://broker.example/actions/token?token=bad'},
                         {'SOURCE_REPOSITORY': 'other/source'}]:
            with self.subTest(override=override), patch.dict(os.environ, {**self.env, **override}, clear=True), \
                 patch.object(client, 'request_json') as request, patch('sys.stdout', io.StringIO()):
                self.assertEqual(client.main(), 1)
                request.assert_not_called()

    def test_token_scope_mismatch_is_revoked(self):
        def request(url, bearer, body=None, method='GET'):
            result = self.request(url, bearer, body, method)
            if body:
                result['repository'] = 'other/source'
            return result
        with patch.dict(os.environ, self.env, clear=True), patch.object(client, 'request_json', side_effect=request), \
             patch.object(client, 'mask'), patch.object(client.subprocess, 'run') as run, patch('sys.stdout', io.StringIO()):
            self.assertEqual(client.main(), 1)
            run.assert_not_called()
        self.assertEqual(self.calls[-1][3], 'DELETE')

    def test_redirects_are_never_followed_and_mask_commands_are_escaped(self):
        self.assertIsNone(client.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example'))
        out = io.StringIO()
        with patch('sys.stdout', out): client.mask('a%\nb\r')
        self.assertEqual(out.getvalue(), '::add-mask::a%25%0Ab%0D\n')

    def test_sync_records_use_the_same_identity_and_reach_publish_as_a_file(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(client, 'request_json', side_effect=self.request), \
             patch.object(client, 'mask'), patch.object(client.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(client.main(), 0)
        env = run.call_args.kwargs['env']
        self.assertEqual(env['GSB_GITCODE_SYNC_FILE'], str(client.SYNC_RECORDS))
        self.assertEqual(json.loads(client.SYNC_RECORDS.read_text())['enabled'], True)
        records = [c for c in self.calls if c[0].endswith('/actions/gitcode-sync')]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][1], 'oidc-identity')

    def test_unreadable_sync_records_do_not_fail_collection(self):
        def request(url, bearer, body=None, method='GET', **kwargs):
            if url.endswith('/actions/gitcode-sync'):
                failure = client.CredentialError('private upstream body')
                failure.status = 503
                raise failure
            return self.request(url, bearer, body, method)
        with patch.dict(os.environ, self.env, clear=True), patch.object(client, 'request_json', side_effect=request), \
             patch.object(client, 'mask'), patch.object(client.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(client.main(), 0)
        written = json.loads(client.SYNC_RECORDS.read_text())
        self.assertEqual(written, {'ok': False, 'error': 'bot returned HTTP 503'})


    def test_rerun_ack_only_follows_a_successful_publish_receipt_with_a_fresh_identity(self):
        change = {'id': 42, 'attempt': 2, 'revision': 'a' * 32}
        def request(url, bearer, body=None, method='GET', **kwargs):
            if url.endswith('/actions/reruns'):
                self.calls.append((url, bearer, body, method))
                return {'ok': True, 'source': self.source, 'runs': [change]}
            return self.request(url, bearer, body, method, **kwargs)
        def publish(*_args, **kwargs):
            self.assertFalse(client.RUN_RECEIPT.exists(), 'stale receipts are removed before starting')
            self.assertEqual(json.loads(Path(kwargs['env']['GSB_RUN_CHANGES_FILE']).read_text())['runs'], [change])
            client.RUN_RECEIPT.write_text(json.dumps({'source': self.source, 'runs': [change], 'commit': 'b' * 40}))
            return SimpleNamespace(returncode=0)
        client.RUN_RECEIPT.write_text('stale')
        with patch.dict(os.environ, self.env, clear=True), patch.object(client, 'request_json', side_effect=request), \
             patch.object(client, 'mask'), patch.object(client.subprocess, 'run', side_effect=publish):
            self.assertEqual(client.main(), 0)
        ack = [call for call in self.calls if call[0].endswith('/reruns/ack')]
        self.assertEqual(ack[0][2], {'commit': 'b' * 40, 'runs': [change]})
        self.assertEqual(sum(call[0].startswith('https://issuer.example/') for call in self.calls), 2)

    def test_failed_publish_stale_receipt_or_hint_fetch_never_loses_pending_ids(self):
        for code in [1, 7]:
            self.calls.clear(); client.RUN_RECEIPT.write_text('{"commit":"stale"}')
            with patch.dict(os.environ, self.env, clear=True), patch.object(client, 'request_json', side_effect=self.request), \
                 patch.object(client, 'mask'), patch.object(client.subprocess, 'run', return_value=SimpleNamespace(returncode=code)):
                self.assertEqual(client.main(), code)
            self.assertFalse(any(call[0].endswith('/reruns/ack') for call in self.calls))
            self.assertFalse(client.RUN_RECEIPT.exists())
        def request(url, *args, **kwargs):
            if url.endswith('/actions/reruns'):
                raise client.CredentialError('private response')
            return self.request(url, *args, **kwargs)
        out = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.object(client, 'request_json', side_effect=request), \
             patch.object(client, 'mask'), patch.object(client.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), patch('sys.stdout', out):
            self.assertEqual(client.main(), 0)
        self.assertEqual(json.loads(client.RUN_CHANGES.read_text()), {'ok': False})
        self.assertNotIn('private response', out.getvalue())

    def test_lost_ack_does_not_fail_successful_publication_or_expose_credentials(self):
        receipt = {'source': self.source, 'runs': [{'id': 42, 'attempt': 2, 'revision': 'a' * 32}], 'commit': 'b' * 40}
        client.RUN_RECEIPT.write_text(json.dumps(receipt))
        out = io.StringIO()
        for response in [client.CredentialError('secret'), {'ok': False}]:
            with patch.object(client, 'oidc_identity', return_value='fresh-secret'), \
                 patch.object(client, 'request_json', **({'side_effect': response} if isinstance(response, Exception) else {'return_value': response})), \
                 patch('sys.stdout', out):
                client.acknowledge_runs('https://broker.example/actions/reruns/ack', 'test-audience', client.RUN_RECEIPT, {'runs': receipt['runs']}, self.source)
        self.assertIn('pending IDs remain', out.getvalue())
        self.assertNotIn('secret', out.getvalue())
