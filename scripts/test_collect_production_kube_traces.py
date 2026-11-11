import importlib.util
import hashlib
import json
import datetime as dt
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zlib
from unittest.mock import patch


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kube = load('kube_collector', 'collect-production-kube-traces.py')
remote = load('kube_remote', 'kube-workspace-remote.py')
publisher = load('kube_publisher', 'publish-local-kube-metrics.py')


class KubeTraceTests(unittest.TestCase):
    def test_full_collector_attaches_native_spans_without_publishing_private_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); workspace = 'w-' + 'b' * 64
            work = root / workspace / 'workswarm'; sessions = work / '.jiuwenswarm' / 'agent' / 'sessions'
            sessions.mkdir(parents=True)
            session = 'PRIVATE_SESSION'; request = 'digitalmate-a'; trace = 'a' * 32; span = '1' * 16
            start = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - dt.timedelta(seconds=5)
            stamp = lambda value: value.strftime('%Y-%m-%d %H:%M:%S.000')
            link = {'session_id':session,'request_id':request,'trace_id':trace,'span_id':span}
            (work / 'process.log').write_text('\n'.join([
                f'{stamp(start)} [MessageHandler] dispatch: request_id={request}',
                f'{stamp(start)} [TrajectoryCorrelation] {json.dumps(link)}',
                f'{stamp(start + dt.timedelta(seconds=3))} [MessageHandler] Stream done request_id={request} total_chunks=1',
            ]),encoding='utf8')
            digest = hashlib.sha256(session.encode()).hexdigest()
            db = work / '.jiuwenswarm' / '.trace' / 'sessions' / digest[:2] / (digest + '.sqlite3')
            db.parent.mkdir(parents=True); con=sqlite3.connect(db)
            con.executescript('''CREATE TABLE trajectory_current_records
                (trace_id TEXT,span_id TEXT,parent_span_id TEXT,request_id TEXT,lifecycle TEXT,
                 start_time_unix_nano INTEGER,end_time_unix_nano INTEGER,has_error INTEGER,raw_json BLOB);
                CREATE TABLE otlp_span_records (trace_id TEXT,span_id TEXT,raw_json BLOB);''')
            def payload(name):
                return zlib.compress(json.dumps({'resourceSpans':[{'scopeSpans':[{'spans':[{'name':name,'content':'PRIVATE_ANSWER'}]}]}]}).encode())
            con.executemany('INSERT INTO trajectory_current_records VALUES (?,?,?,?,?,?,?,?,?)',[
                (trace,span,None,request,'final',1_000_000_000,3_000_000_000,0,payload('root')),
                (trace,'2'*16,span,request,'final',1_100_000_000,1_300_000_000,0,payload('workswarm.input.prepare')),
            ]);con.commit();con.close()
            source=Path(__file__).with_name('collect-production-workspace-traces.py').read_text(encoding='utf8')
            with patch.object(remote,'ROOT',root):
                result=remote.run_collector([workspace],source)
            self.assertEqual(result['native_trace_count'],1)
            item=result['days'][0]['anomaly']['latency']['slow_requests'][0]
            self.assertEqual(item['native_trajectory']['spans'][0]['label'],'输入准备')
            self.assertNotIn('PRIVATE',json.dumps(result))
            self.assertNotIn(trace,json.dumps(result))

    def test_native_trace_reads_only_matched_timing_and_safe_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace = 'w-' + 'a' * 64
            session = 'private-session'
            digest = hashlib.sha256(session.encode()).hexdigest()
            db = root / workspace / 'workswarm' / '.jiuwenswarm' / '.trace' / 'sessions' / digest[:2] / (digest + '.sqlite3')
            db.parent.mkdir(parents=True)
            con = sqlite3.connect(db)
            con.executescript('''CREATE TABLE trajectory_current_records
                (trace_id TEXT,span_id TEXT,parent_span_id TEXT,request_id TEXT,lifecycle TEXT,
                 start_time_unix_nano INTEGER,end_time_unix_nano INTEGER,has_error INTEGER,raw_json BLOB);
                CREATE TABLE otlp_span_records (trace_id TEXT,span_id TEXT,raw_json BLOB);''')
            trace = 'a' * 32
            def payload(name):
                return zlib.compress(json.dumps({'resourceSpans':[{'scopeSpans':[{'spans':[
                    {'name':name,'attributes':[{'key':'secret','value':{'stringValue':'PRIVATE_PROMPT'}}]}]}]}]}).encode())
            records = [
                (trace,'1'*16,None,'digitalmate-abc','final',1_000_000_000,3_000_000_000,0,payload('agent.root')),
                (trace,'2'*16,'1'*16,'digitalmate-abc','final',1_100_000_000,1_300_000_000,0,payload('workswarm.input.prepare')),
                (trace,'3'*16,'1'*16,'digitalmate-abc','final',1_400_000_000,2_000_000_000,0,payload('chat PRIVATE_MODEL')),
                ('b'*32,'4'*16,None,'digitalmate-abc','final',1_000_000_000,2_000_000_000,0,payload('PRIVATE_OTHER')),
            ]
            con.executemany('INSERT INTO trajectory_current_records VALUES (?,?,?,?,?,?,?,?,?)',records);con.commit();con.close()
            with patch.object(remote, 'ROOT', root):
                result = remote.read_native_trajectory({'name':workspace}, {'request_id':'digitalmate-abc',
                    'session_id':session,'trace_id':trace,'span_id':'1'*16})
            self.assertEqual(result['duration_ms'],2000)
            self.assertEqual([s['kind'] for s in result['spans']],['phase','model'])
            self.assertEqual(result['spans'][0]['start_ms'],100)
            self.assertNotIn('PRIVATE',json.dumps(result))
            self.assertNotIn(session,json.dumps(result))
            with patch.object(remote, 'ROOT', root):
                wrong = remote.read_native_trajectory({'name':workspace}, {'request_id':'digitalmate-wrong',
                    'session_id':session,'trace_id':trace,'span_id':'1'*16})
            self.assertIsNone(wrong)

    def test_native_payload_parser_rejects_oversize_and_malformed_content(self):
        self.assertIsNone(remote.native_span_name(zlib.compress(b'x' * (remote.MAX_NATIVE_PAYLOAD + 1))))
        self.assertIsNone(remote.native_span_name(b'not-a-zlib-payload'))
        self.assertIsNone(remote.native_span_name(zlib.compress(b'{"resourceSpans":[]}')))

    def test_native_reader_includes_committed_uncheckpointed_wal(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);workspace='w-'+'c'*64;session='session';digest=hashlib.sha256(session.encode()).hexdigest()
            db=root/workspace/'workswarm'/'.jiuwenswarm'/'.trace'/'sessions'/digest[:2]/(digest+'.sqlite3')
            db.parent.mkdir(parents=True);writer=sqlite3.connect(db)
            try:
                writer.execute('PRAGMA journal_mode=WAL');writer.execute('PRAGMA wal_autocheckpoint=0')
                writer.executescript('''CREATE TABLE trajectory_current_records
                    (trace_id TEXT,span_id TEXT,parent_span_id TEXT,request_id TEXT,lifecycle TEXT,
                     start_time_unix_nano INTEGER,end_time_unix_nano INTEGER,has_error INTEGER,raw_json BLOB);
                    CREATE TABLE otlp_span_records (trace_id TEXT,span_id TEXT,raw_json BLOB);''')
                blob=zlib.compress(json.dumps({'resourceSpans':[{'scopeSpans':[{'spans':[{'name':'workswarm.input.prepare'}]}]}]}).encode())
                writer.executemany('INSERT INTO trajectory_current_records VALUES (?,?,?,?,?,?,?,?,?)',[
                    ('a'*32,'1'*16,None,'digitalmate-a','final',1_000_000,5_000_000,0,blob),
                    ('a'*32,'2'*16,'1'*16,'digitalmate-a','final',2_000_000,3_000_000,0,blob),
                ]);writer.commit()
                with patch.object(remote,'ROOT',root):
                    out=remote.read_native_trajectory({'name':workspace},{'session_id':session,'request_id':'digitalmate-a','trace_id':'a'*32,'span_id':'1'*16})
                self.assertEqual(out['spans'][0]['label'],'输入准备')
            finally:writer.close()

    def test_collector_proxy_uses_the_selected_local_port(self):
        self.assertEqual(publisher.proxy_url('47123'), 'socks5h://127.0.0.1:47123')
        for value in ('0', '65536', 'example.org:22', '1;command', ''):
            with self.assertRaises(ValueError):
                publisher.proxy_url(value)

    def test_node_discovery_only_uses_ready_workspace_nodes(self):
        def pod(name, ready):
            return {'metadata': {'name': name}, 'status': {'conditions': [{'type': 'Ready', 'status': ready}]}}
        result = kube.ready_nodes({'items': [pod('adx-node-2', 'True'), pod('adx-node-0', 'False'),
                                             pod('other-1', 'True'), pod('adx-node-1', 'True')]})
        self.assertEqual(result, ['adx-node-1', 'adx-node-2'])

    def test_publication_rejects_incomplete_or_invalid_coverage(self):
        self.assertEqual(publisher.validate_coverage({'binding_count': 138, 'read_log_count': 137}), (138, 137))
        for result in ({'binding_count': 100, 'read_log_count': 94},
                       {'binding_count': 0, 'read_log_count': 0},
                       {'binding_count': 1, 'read_log_count': 2}):
            with self.assertRaises(RuntimeError):
                publisher.validate_coverage(result)

    def test_production_scope_rejects_ambiguous_and_marks_partial(self):
        bindings = [{'idx': n, 'binding': {'scope': {'tenant': 'jiuwen'}}} for n in range(3)]
        bindings.append({'binding': {'scope': {'tenant': 'other'}}})
        result = {'schema': 'digitalmate/cloud-traces@1', 'read_log_count': 1,
                  'errors': {}, 'request_daily_metrics': [{'partial': False}]}
        inventory = {'one': ['w-' + 'a' * 64], 'duplicate': ['w-' + 'b' * 64, 'w-' + 'c' * 64]}
        with patch.object(kube.traces, 'TOKEN', 'test'), \
             patch.object(kube.traces, 'binding_list', return_value=bindings), \
             patch.object(kube, 'fingerprint', side_effect=lambda item: [('one', None), ('duplicate', None), (None, 'http_413')][item['idx']]), \
             patch.object(kube, 'kube_operation', side_effect=[inventory, result]) as operation:
            actual = kube.collect()
        self.assertEqual(actual['binding_count'], 3)
        self.assertEqual(actual['errors'], {'ambiguous_or_changed': 1, 'http_413': 1})
        self.assertEqual(actual['collection_source'], 'kubernetes_shared_workspace')
        self.assertTrue(actual['request_daily_metrics'][0]['partial'])
        self.assertTrue(actual['request_daily_metrics'][0]['telemetry_partial'])
        self.assertEqual(operation.call_args.args[0]['names'], ['w-' + 'a' * 64])

    def test_inventory_and_path_validation_prevent_workspace_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            name = 'w-' + 'a' * 64
            workspace = root / name
            (workspace / 'digitalmate').mkdir(parents=True)
            (workspace / 'workswarm').mkdir()
            (workspace / 'workswarm' / 'process.log').write_text('PRIVATE LOG', encoding='utf-8')
            with patch.object(remote, 'ROOT', root):
                found = remote.inventory()
                self.assertEqual(sum(map(len, found.values())), 1)
                self.assertNotIn('PRIVATE LOG', json.dumps(found))
                with self.assertRaises(ValueError):
                    remote.path_for('../outside', 'test')
                with self.assertRaises((ValueError, FileNotFoundError)):
                    remote.path_for(name, '../../outside')

    def test_both_workspace_naming_schemes_are_supported(self):
        self.assertIsNotNone(remote.NAME.fullmatch('w-' + 'a' * 64))
        self.assertIsNotNone(remote.NAME.fullmatch('digitalmate-w-' + 'b' * 64))
        self.assertIsNone(remote.NAME.fullmatch('digitalmate-w-../outside'))


if __name__ == '__main__':
    unittest.main()
