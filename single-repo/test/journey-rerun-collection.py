"""CLI journey: an old failed run becomes green through one notified ID and an atomic publication."""
from datetime import datetime, timedelta, timezone
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gsb.history import History
from gsb.project import slim_run


class RerunCLIJourney(unittest.TestCase):
    def journey(self, fail=False, conflict=False):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder:
            root = Path(folder)
            for name in ['gsb', 'static']:
                shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copyfile(ROOT / 'publish.py', root / 'publish.py')
            source, target = 'example/source', 'example/board'
            now = datetime.now(timezone.utc).replace(microsecond=0)
            stamp = lambda dt: dt.isoformat().replace('+00:00', 'Z')
            old = dict(id=42, run_attempt=1, name='CI', workflow_id=7, event='push', status='completed', conclusion='failure',
                       head_branch='main', head_sha='a' * 40, html_url=f'https://github.com/{source}/actions/runs/42',
                       created_at=stamp(now - timedelta(days=5)), updated_at=stamp(now - timedelta(days=5)),
                       run_started_at=stamp(now - timedelta(days=5)))
            latest = dict(old, run_attempt=2, conclusion='success', run_started_at=stamp(now - timedelta(minutes=2)), updated_at=stamp(now))
            history = History(root); history.put('runs', slim_run(old, {}))
            for name, data in history.files().items():
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(data)
            (root / '.sync').mkdir(exist_ok=True)
            done = {kind: {'done': True, 'page': 1, 'until': stamp(now), **({'windows': []} if kind == 'runs' else {})}
                    for kind in ['issues', 'prs', 'runs', 'releases']}
            (root / '.sync/state.json').write_text(json.dumps({'schema': 1, 'repository': source, 'started_at': stamp(now), 'migrated': True,
                'backfill': done, 'incremental': {}, 'pending': {}, 'errors': {}, 'reconcile': {**done, 'next_at': stamp(now + timedelta(days=7))}}))
            (root / 'board-config.json').write_text(json.dumps({'deployments': {source: {'repository': target, 'environment': 'test', 'label': 'fixture'}}}))
            change = {'id': 42, 'attempt': 2, 'revision': '1' * 32}
            hints, receipt = root / 'hints.json', root / 'receipt.json'
            hints.write_text(json.dumps({'ok': True, 'source': source, 'runs': [change]}))
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('junit.xml', '<testsuite tests="1" failures="0" skipped="0"><testcase name="recovered"/></testsuite>')
            base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
            calls, updates = [], []
            class API(BaseHTTPRequestHandler):
                def log_message(self, *_): pass
                def reply(self, value, status=200, binary=False):
                    raw = value if binary else json.dumps(value).encode()
                    self.send_response(status); self.send_header('Content-Type', 'application/zip' if binary else 'application/json')
                    self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
                def do_GET(self):
                    parsed = urlsplit(self.path); path = parsed.path; calls.append(('GET', path, parsed.query))
                    if path == f'/repos/{source}':
                        return self.reply({'full_name': source, 'html_url': 'https://github.com/' + source, 'default_branch': 'main',
                                           'private': False, 'visibility': 'public', 'created_at': old['created_at']})
                    if path == f'/repos/{target}': return self.reply({'private': False})
                    if path == f'/repos/{target}/git/ref/heads/main': return self.reply({'object': {'sha': 'e' * 40 if conflict else base}})
                    if path == f'/repos/{target}/git/commits/{base}': return self.reply({'tree': {'sha': 'b' * 40}})
                    if path == f'/repos/{source}/actions/runs/42':
                        return self.reply({'message': 'temporary fixture failure'}, 503) if fail else self.reply(latest)
                    if path.endswith('/attempts/2/jobs'): return self.reply({'jobs': []})
                    if path.endswith('/runs/42/artifacts'):
                        return self.reply({'artifacts': [{'id': 7, 'name': 'ut-results', 'size_in_bytes': len(archive.getvalue()),
                            'created_at': stamp(now), 'expired': False}]})
                    if path == f'/repos/{source}/actions/artifacts/7/zip':
                        self.send_response(302)
                        self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/blob/7')
                        self.end_headers(); return
                    if path == '/blob/7':
                        assert not self.headers.get('Authorization'), 'artifact blob must be anonymous'
                        return self.reply(archive.getvalue(), binary=True)
                    if path == f'/repos/{source}/actions/runs': return self.reply({'total_count': 0, 'workflow_runs': []})
                    if path == f'/repos/{source}/actions/artifacts': return self.reply({'total_count': 0, 'artifacts': []})
                    if path.endswith(('/issues', '/pulls', '/releases')): return self.reply([])
                    return self.reply({'message': 'fixture endpoint unavailable'}, 404)
                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    calls.append(('POST', self.path, '')); updates.append((self.path, body))
                    if self.path.endswith('/git/trees'): return self.reply({'sha': 'c' * 40})
                    if self.path.endswith('/git/commits'): return self.reply({'sha': 'd' * 40})
                    return self.reply({'message': 'fixture endpoint unavailable'}, 404)
                def do_PATCH(self):
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    calls.append(('PATCH', self.path, '')); updates.append((self.path, body)); self.reply({})
            server = ThreadingHTTPServer(('127.0.0.1', 0), API)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            bootstrap = '''import runpy,sys,urllib.request,urllib.error,urllib.parse
from gsb import github
github.API_ROOT=sys.argv.pop(1)
original=urllib.request.OpenerDirector.open
def isolated(self,req,*args,**kw):
    url=req if isinstance(req,str) else req.full_url
    if urllib.parse.urlsplit(url).hostname!='127.0.0.1':raise urllib.error.URLError('network isolated')
    return original(self,req,*args,**kw)
urllib.request.OpenerDirector.open=isolated
sys.argv[0]='publish.py'
runpy.run_path('publish.py',run_name='__main__')'''
            import os
            env = {**os.environ, 'GITHUB_TOKEN': 'fixture-source', 'GSB_PUBLISH_TOKEN': 'fixture-target',
                   'GSB_RUN_CHANGES_FILE': str(hints), 'GSB_RUN_CHANGES_RECEIPT': str(receipt), 'PYTHONDONTWRITEBYTECODE': '1',
                   'TMPDIR': str(root), 'GSB_GITCODE_SYNC_FILE': ''}
            try:
                result = subprocess.run([sys.executable, '-c', bootstrap, f'http://127.0.0.1:{server.server_port}', '--repo', source,
                    '--publish-repo', target, '--output', str(root / 'preview'), '--incremental'], cwd=root, env=env,
                    capture_output=True, text=True, timeout=45)
            finally:
                server.shutdown(); server.server_close(); thread.join()
            if conflict:
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(receipt.exists(), 'failed publication must not acknowledge the bot hint')
                self.assertFalse(any(method == 'PATCH' for method, _, _ in calls))
                return
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            exact = [call for call in calls if call[1] == f'/repos/{source}/actions/runs/42']
            self.assertEqual(len(exact), 1)
            # Normal recent polling remains; there is no historical fallback query for the notified run.
            from urllib.parse import parse_qs
            for _, path, query in calls:
                if path == f'/repos/{source}/actions/runs':
                    self.assertGreater(parse_qs(query)['created'][0].split('..')[0], old['created_at'])
            tree = next(body['tree'] for path, body in updates if path.endswith('/git/trees'))
            snapshot = json.loads(next(entry['content'] for entry in tree if entry['path'] == 'site/data/snapshot.json'))
            lane = next(lane for lane in snapshot['sections']['ci']['data']['lanes']['lanes'] if lane['key'] == 'main')
            points = [cell for column in lane['days'] for cell in column]
            self.assertEqual([(point['attempt'], point['outcome']) for point in points], [(1, 'failure')] if fail else [(2, 'success')])
            self.assertTrue(any(entry['path'] == '.sync/state.json' for entry in tree))
            self.assertEqual(updates[-1], (f'/repos/{target}/git/refs/heads/main', {'sha': 'd' * 40, 'force': False}))
            if fail:
                self.assertFalse(receipt.exists(), 'failed source lookup must remain pending at the bot')
            else:
                self.assertEqual(json.loads(receipt.read_text()), {'source': source, 'commit': 'd' * 40, 'runs': [change]})
    def test_old_failed_run_becomes_green_and_receipt_follows_publication(self): self.journey()
    def test_failed_lookup_preserves_red_and_does_not_ack(self): self.journey(fail=True)
    def test_publication_conflict_does_not_ack(self): self.journey(conflict=True)


if __name__ == '__main__': unittest.main()
