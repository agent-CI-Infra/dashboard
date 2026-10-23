import json
from pathlib import Path
import tempfile
import unittest

from gsb import gitcode_sync
from publish import ROOT, SYNC_DATA, publish_batch

GITCODE_TOKEN = 'gitcode-fake-token-not-real-0123456789'
SHA_A, SHA_B = 'a' * 39 + '1', 'b' * 39 + '2'


def record(**overrides):
    base = {'id': 'r1', 'time': '2026-10-07T06:00:00.000Z', 'pr': 120, 'pr_url': 'https://github.com/openJiuwen-ai/sciencediscovery/pull/120',
            'title': 'feat: stream PDFs', 'action': 'opened', 'head_sha': SHA_A, 'mr': 11, 'mr_url': 'https://gitcode.com/openJiuwen/sciencediscovery/merge_requests/11',
            'status': 'success', 'summary': '已推送原始 head aaaaaaa，已创建 GitCode MR !11', 'error_code': None, 'error': None}
    return {**base, **overrides}


def payload(records, pulls=()):
    return {'ok': True, 'enabled': True, 'source': 'openJiuwen-ai/sciencediscovery', 'target': 'openJiuwen/sciencediscovery',
            'check_name': 'CodeCheck (GitCode)', 'generated_at': '2026-10-07T06:00:00.000Z', 'records': list(records), 'pulls': list(pulls)}


class PublicDocumentTests(unittest.TestCase):
    def test_records_are_rebuilt_from_an_allowlist_and_redacted(self):
        bad = record(id='r2', time='2026-10-07T07:00:00.000Z', status='error', error_code='permission_denied',
                     error=f'GitCode returned HTTP 403: token={GITCODE_TOKEN} Authorization: Bearer ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 https://sync-bot:{GITCODE_TOKEN}@gitcode.com/x.git',
                     summary='同步失败（第 1 次，已停止重试）', mr_url='javascript:alert(1)', extra='private', payload={'raw': 'webhook'})
        doc = gitcode_sync.public_document(payload([record(), bad, record(id='r3', action='deleted'), {'pr': 'x'}, 'junk']), None, '2026-10-07T08:00:00Z')
        self.assertTrue(doc['available'] and doc['enabled'])
        self.assertEqual([r['id'] for r in doc['records']], ['r2', 'r1'], 'invalid rows dropped, newest first')
        text = gitcode_sync.encode(doc)
        for leaked in (GITCODE_TOKEN, 'ghs_ABCDEFGH', 'sync-bot:', 'javascript:', 'private', 'webhook'):
            self.assertNotIn(leaked, text)
        self.assertIsNone(doc['records'][0]['mr_url'])
        self.assertIn('[REDACTED]', doc['records'][0]['error'])
        self.assertEqual(set(doc['records'][0]), {'id', 'time', 'pr', 'pr_url', 'title', 'action', 'head_sha', 'mr', 'mr_url', 'status', 'summary', 'error_code', 'error'})

    def test_previous_records_stay_until_evicted_and_new_ones_replace_by_id(self):
        previous = gitcode_sync.public_document(payload([record(id='old', time='2026-10-06T00:00:00.000Z')]), None, '2026-10-06T01:00:00Z')
        doc = gitcode_sync.public_document(payload([record(id='new', head_sha=SHA_B)]), previous, '2026-10-07T08:00:00Z')
        self.assertEqual([r['id'] for r in doc['records']], ['new', 'old'])

    def test_unreadable_bot_keeps_previous_data_and_says_so(self):
        previous = gitcode_sync.public_document(payload([record()], [{'pr': 120, 'sync_status': 'failed', 'head_sha': SHA_A, 'error': 'push rejected'}]), None, '2026-10-06T01:00:00Z')
        doc = gitcode_sync.public_document({'ok': False, 'error': f'bot returned HTTP 503 token={GITCODE_TOKEN}'}, previous, '2026-10-07T08:00:00Z')
        self.assertFalse(doc['available'])
        self.assertEqual(doc['stale_since'], '2026-10-06T01:00:00Z')
        self.assertEqual(len(doc['records']), 1)
        self.assertEqual(doc['pulls'][0]['sync_status'], 'failed')
        self.assertNotIn(GITCODE_TOKEN, gitcode_sync.encode(doc))
        again = gitcode_sync.public_document(None, doc, '2026-10-07T09:00:00Z')
        self.assertEqual(again['stale_since'], '2026-10-06T01:00:00Z', 'staleness is measured from the last good fetch')

    def test_disabled_sync_publishes_why_it_is_off(self):
        disabled = lambda **extra: gitcode_sync.public_document({'ok': True, 'enabled': False, 'records': [], 'pulls': [], **extra}, None, '2026-10-07T08:00:00Z')
        doc = disabled(reason='no_github_app,no_webhook_secret', reasons=['no_github_app', 'no_webhook_secret'])
        self.assertEqual((doc['available'], doc['enabled'], doc['records']), (True, False, []))
        self.assertEqual(doc['reasons'], ['no_github_app', 'no_webhook_secret'], 'both missing credentials stay visible')
        for code in ('no_token', 'off', 'no_github_app', 'no_webhook_secret'):
            self.assertEqual(disabled(reason=code, reasons=[code])['reasons'], [code])
        # The comma-joined reason is enough on its own; unknown or hostile codes are dropped.
        self.assertEqual(disabled(reason='no_webhook_secret, no_github_app')['reasons'], ['no_webhook_secret', 'no_github_app'])
        self.assertEqual(disabled(reasons=['<script>', 'off', 'off', 42])['reasons'], ['off'])
        self.assertEqual(disabled()['reasons'], [])
        stale = gitcode_sync.public_document({'ok': False, 'error': 'bot returned HTTP 503'}, disabled(reasons=['no_token']), '2026-10-07T09:00:00Z')
        self.assertEqual((stale['available'], stale['enabled'], stale['reasons']), (False, False, ['no_token']))

    def test_load_reads_the_fetched_file_and_previous_site_data(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp' if (ROOT / '.tmp').is_dir() else None) as temp:
            fetched, previous = Path(temp) / 'fetched.json', Path(temp) / 'previous.json'
            fetched.write_text(json.dumps(payload([record()])))
            self.assertIsNone(gitcode_sync.load(None, previous, 'now'), 'local runs without fetched records keep the site file untouched')
            self.assertEqual(len(gitcode_sync.load(str(fetched), previous, '2026-10-07T08:00:00Z')['records']), 1)
            fetched.write_text('{not json')
            self.assertFalse(gitcode_sync.load(str(fetched), previous, '2026-10-07T08:00:00Z')['available'])


class PublishAllowlistTests(unittest.TestCase):
    def test_sync_data_is_publishable_but_credentials_are_refused(self):
        calls = []

        class GH:
            token = 'board-write-token-0123456789'
            def get(self, path):
                if '/git/commits/' in path: return {'tree': {'sha': 'base-tree'}}
                return {'object': {'sha': 'a' * 40}} if '/git/' in path else {'private': False}
            def _url(self, path, _): return path
            def _request(self, method, path, body):
                calls.append((method, path, body)); return ({'sha': 'new'}, None, 200)
        doc = gitcode_sync.encode(gitcode_sync.public_document(payload([record()]), None, '2026-10-07T08:00:00Z'))
        publish_batch(GH(), 'example/board', 'main', 'a' * 40, {'site/' + SYNC_DATA: doc, 'site/gitcode-sync.js': '// page'})
        self.assertEqual({e['path'] for e in calls[0][2]['tree']}, {'site/data/gitcode-sync.json', 'site/gitcode-sync.js'})
        with self.assertRaises(ValueError):
            publish_batch(GH(), 'example/board', 'main', 'a' * 40, {'site/' + SYNC_DATA: doc.replace('feat', GH.token)})
        with self.assertRaises(ValueError):
            publish_batch(GH(), 'example/board', 'main', 'a' * 40, {'site/data/gitcode-sync-raw.json': doc})


if __name__ == '__main__':
    unittest.main()
