import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
import zlib
from unittest.mock import patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('archive', HERE / 'collect-sfs-trajectory-archive.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)
remote = types.ModuleType('archive_remote')
exec((HERE / 'kube-workspace-remote.py').read_text(encoding='utf-8').rsplit('\nif __name__', 1)[0]
     + '\n' + (HERE / 'sfs-trajectory-remote.py').read_text(encoding='utf-8'), remote.__dict__)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'sfs'
        self.name = 'w-' + 'a' * 64
        self.sessions = self.root / self.name / 'workswarm/.jiuwenswarm/.trace/sessions'
        self.output = Path(self.temp.name) / 'public'
        self.state = {'schema': archive.SCHEMA, 'databases': {}}
        self.index = {'traces': []}
        self.addCleanup(patch.stopall)
        patch.object(remote, 'ROOT', self.root).start()

    def make_db(self, digest='b'*64, spans=3):
        db = self.sessions / digest[:2] / (digest + '.sqlite3')
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db)
        con.executescript('''PRAGMA journal_mode=WAL;
          CREATE TABLE trajectory_current_records
          (trace_id TEXT, span_id TEXT, parent_span_id TEXT, request_id TEXT, lifecycle TEXT,
           start_time_unix_nano INTEGER, end_time_unix_nano INTEGER, has_error INTEGER, raw_json BLOB);
          CREATE TABLE otlp_span_records (trace_id TEXT, span_id TEXT, raw_json BLOB, PRIMARY KEY(trace_id,span_id));''')
        raw = zlib.compress(json.dumps({'resourceSpans':[{'scopeSpans':[{'spans':[{
            'name':'chat SECRET_MODEL', 'content':'SECRET_PROMPT'}]}]}]}).encode())
        for i in range(spans):
            con.execute('INSERT INTO trajectory_current_records VALUES (?,?,?,?,?,?,?,?,?)',
                        ('c'*32, f'{i+1:016x}', None if i == 0 else f'{1:016x}', 'digitalmate-request',
                         'final', 1_800_000_000_000_000_000+i*1000, 1_800_000_001_000_000_000, 0, raw))
        con.commit()
        con.close()
        return db

    def collect(self):
        cursors = {k: {f: v[f] for f in ('version','cursor','done') if f in v}
                   for k,v in self.state['databases'].items()}
        return remote.archive_collect({'names':[self.name], 'states':cursors,
                                       'last_database':self.state.get('last_database','')})

    def merge(self, batch):
        self.state, self.index = archive.merge_batch(self.state, self.index, batch, self.output,
            {'bindings':1,'authorized_workspaces':1,'errors':{}}, '2026-11-12T12:00:00+00:00')

    def matching_metrics(self):
        entry = self.index['traces'][0]
        start = dt.datetime.fromisoformat(entry['start_time']) - dt.timedelta(milliseconds=250)
        row = {'request_id':entry['request_id'], 'start_time':start.isoformat(),
               'gateway':entry['duration_ms'] + 500, 'native_trace_state':'read_error',
               'tool_events':[{'name':'tool', 'start_ms':300, 'duration_ms':10}]}
        return {'cloud_trace_collection':{'schema':'digitalmate/cloud-traces@1',
                'days':[{'anomaly':{'latency':{'slow_requests':[row]}}}]}}, row

    def test_archive_links_all_detail_pages_without_changing_gateway_clock(self):
        self.make_db(spans=701)
        self.merge(self.collect())
        metrics, row = self.matching_metrics()
        before = copy.deepcopy(row)
        archive.attach_archived_profiles(metrics, self.index, self.output)
        self.assertEqual(len(row['native_trajectory']['spans']), 700)
        self.assertEqual(row['native_trajectory']['duration_ms'], 1000)
        self.assertEqual(row['native_trajectory']['root_span_id'], f'{1:016x}')
        for field in ('gateway', 'start_time', 'tool_events'):
            self.assertEqual(row[field], before[field])
        self.assertEqual(row['native_trace_consistency'], 'unconfirmed')
        self.assertEqual(row['native_trace_source'], 'sfs_archive')
        self.assertEqual(row['native_trace_observed_at'], self.index['traces'][0]['observed_at'])
        self.assertNotIn('native_trace_state', row)
        self.assertEqual(metrics['cloud_trace_collection']['archived_native_trace_count'], 1)
        self.assertEqual(metrics['cloud_trace_collection']['archive_link_errors'], 0)

    def test_archive_leaves_existing_direct_native_profile_untouched(self):
        self.make_db(); self.merge(self.collect())
        metrics, row = self.matching_metrics()
        row.pop('native_trace_state')
        row['native_trajectory'] = {'root_span_id':'direct-root', 'duration_ms':900, 'spans':[]}
        before = copy.deepcopy(row)
        archive.attach_archived_profiles(metrics, self.index, self.output)
        self.assertEqual(row, before)
        self.assertEqual(metrics['cloud_trace_collection']['native_trace_count'], 1)
        self.assertEqual(metrics['cloud_trace_collection']['archived_native_trace_count'], 0)

    def test_archive_rejects_conflicting_ids_unfinished_trees_and_wrong_time_windows(self):
        self.make_db(); self.merge(self.collect())
        for case in ('request', 'duplicate_request', 'duplicate_trace', 'subspan_conflict',
                     'root_request', 'pending', 'ambiguous', 'later_start', 'earlier_end'):
            with self.subTest(case=case):
                metrics, row = self.matching_metrics()
                index = copy.deepcopy(self.index)
                entry = index['traces'][0]
                if case == 'request': row['request_id'] = 'different'
                if case == 'duplicate_request':
                    metrics['cloud_trace_collection']['days'][0]['anomaly']['latency']['slow_requests'].append(dict(row))
                if case in ('duplicate_trace', 'subspan_conflict'):
                    duplicate = dict(entry, key='f'*64)
                    if case == 'subspan_conflict': duplicate['request_id'] = 'other-root'
                    index['traces'].append(duplicate)
                if case == 'root_request': entry['request_id'] = 'other-root'
                if case == 'pending': entry['lifecycle'] = 'pending_descendants'
                if case == 'ambiguous': row['native_trace_state'] = 'ambiguous'
                if case == 'later_start':
                    row['start_time'] = (dt.datetime.fromisoformat(entry['start_time']) + dt.timedelta(seconds=1)).isoformat()
                if case == 'earlier_end': row['gateway'] = 500
                archive.attach_archived_profiles(metrics, index, self.output)
                self.assertNotIn('native_trajectory', row)

    def test_archive_revalidates_old_link_when_another_trace_claims_the_request(self):
        self.make_db(); self.merge(self.collect())
        metrics, row = self.matching_metrics()
        archive.attach_archived_profiles(metrics, self.index, self.output)
        self.index['traces'].append(dict(self.index['traces'][0], key='f'*64))
        archive.attach_archived_profiles(metrics, self.index, self.output)
        self.assertNotIn('native_trajectory', row)
        self.assertEqual(row['native_trace_state'], 'ambiguous')

    def test_archive_rejects_missing_pages_wrong_identity_and_broken_ancestry(self):
        self.make_db(); self.merge(self.collect())
        entry = self.index['traces'][0]
        path = self.output / entry['pages'][0]
        original = json.loads(path.read_text(encoding='utf-8'))
        for case in ('missing', 'wrong_key', 'wrong_total', 'missing_root', 'missing_parent',
                     'cycle', 'pending_span', 'outside_root', 'unsafe_path'):
            with self.subTest(case=case):
                page = copy.deepcopy(original)
                index = copy.deepcopy(self.index)
                if case == 'wrong_key': page['key'] = 'f'*64
                if case == 'wrong_total': page['total'] += 1
                if case == 'missing_root': page['spans'][0]['parent_span_id'] = 'f'*16
                if case == 'missing_parent': page['spans'][1]['parent_span_id'] = 'f'*16
                if case == 'cycle': page['spans'][1]['parent_span_id'] = page['spans'][1]['span_id']
                if case == 'pending_span': page['spans'][1]['lifecycle'] = 'partial'
                if case == 'outside_root': page['spans'][1]['duration_ms'] = 2000
                if case == 'unsafe_path': index['traces'][0]['pages'] = ['../private.json']
                archive.write_json(path, page)
                if case == 'missing': path.unlink()
                metrics, row = self.matching_metrics()
                archive.attach_archived_profiles(metrics, index, self.output)
                self.assertNotIn('native_trajectory', row)
                self.assertEqual(metrics['cloud_trace_collection']['archive_link_errors'], 1)

    def test_wal_header_copy_reads_all_spans_and_keeps_source_untouched(self):
        db = self.make_db(spans=701)
        before = db.read_bytes()
        real_connect = remote.sqlite3.connect
        def guarded_connect(database, *args, **kwargs):
            self.assertNotIn(self.root.as_uri(), str(database))
            return real_connect(database, *args, **kwargs)
        with patch.object(remote.sqlite3, 'connect', side_effect=guarded_connect):
            self.merge(self.collect())
        self.assertEqual(db.read_bytes(), before)
        self.assertFalse(Path(str(db)+'-wal').exists())
        self.assertFalse(Path(str(db)+'-shm').exists())
        self.assertEqual(self.index['coverage']['archived_spans'], 701)
        self.assertFalse(self.index['coverage']['complete'])
        self.assertEqual(len(self.index['traces'][0]['pages']), 2)
        public = ''.join(p.read_text(encoding='utf-8') for p in self.output.rglob('*.json'))
        for private in ('SECRET', self.name, 'c'*32):
            self.assertNotIn(private, public)

    def test_resume_pages_and_unchanged_database_is_not_duplicated(self):
        self.make_db(spans=7)
        with patch.object(remote, 'ARCHIVE_PAGE', 3):
            self.merge(self.collect())
            self.assertEqual(self.index['coverage']['archived_traces'],0)
            self.assertEqual(self.index['coverage']['pending_pages'],1)
            self.merge(self.collect())
            self.merge(self.collect())
        self.assertEqual(self.index['coverage']['archived_spans'],7)
        self.assertNotIn('staged', json.dumps(self.state))
        self.merge(self.collect())
        self.assertEqual(self.index['coverage']['archived_spans'],7)
        self.assertFalse(list((self.output / 'staging').glob('*.json')))

    def test_labels_are_recovered_from_final_otlp_archive(self):
        db = self.make_db()
        con = sqlite3.connect(db)
        con.execute('INSERT INTO otlp_span_records SELECT trace_id,span_id,raw_json FROM trajectory_current_records')
        con.execute("UPDATE trajectory_current_records SET raw_json=X''")
        con.commit(); con.close()
        self.merge(self.collect())
        detail = json.loads((self.output / self.index['traces'][0]['pages'][0]).read_text(encoding='utf-8'))
        self.assertTrue(all(r['label'] == '模型调用' for r in detail['spans']))

    def test_generation_change_restarts_staging_and_failure_retains_archive(self):
        db = self.make_db(spans=7)
        with patch.object(remote, 'ARCHIVE_PAGE', 3):
            self.merge(self.collect())
        with sqlite3.connect(db) as con:
            con.execute('UPDATE trajectory_current_records SET has_error=1')
        con.close()
        self.merge(self.collect())
        self.assertEqual(self.index['coverage']['archived_spans'],7)
        page = json.loads((self.output / self.index['traces'][0]['pages'][0]).read_text(encoding='utf-8'))
        self.assertTrue(all(r['error'] for r in page['spans']))
        Path(str(db)+'-wal').write_bytes(b'active')
        self.merge(self.collect())
        self.assertEqual(self.index['coverage']['archived_spans'],7)
        self.assertEqual(self.index['coverage']['pending_databases'],1)
        self.assertEqual(self.index['coverage']['statuses']['active_sidecars'],1)

    def test_inventory_has_no_64_database_cap(self):
        for i in range(70):
            digest = f'{i:064x}'
            db = self.sessions / digest[:2] / (digest+'.sqlite3')
            db.parent.mkdir(parents=True, exist_ok=True)
            db.touch()
        found, errors = remote.archive_inventory([self.name])
        self.assertEqual(len(found),70)
        self.assertEqual(errors,0)

    def test_changed_source_and_size_limit_are_explicit(self):
        db = self.make_db()
        signature = remote.archive_signature(db)
        changed = [tuple([*signature[0][:-1],signature[0][-1]+1]),None,None,None]
        with patch.object(remote, 'archive_signature', side_effect=[signature,changed]):
            batch = self.collect()
        self.assertEqual(next(iter(batch['updates'].values()))['status'],'changing')
        with patch.object(remote, 'ARCHIVE_DB_BYTES', 1):
            self.merge(self.collect())
        self.assertEqual(self.index['coverage']['statuses']['size_limit'],1)

    def test_source_retention_does_not_shrink_archive(self):
        db = self.make_db(spans=7)
        self.merge(self.collect())
        con = sqlite3.connect(db)
        con.execute('DELETE FROM trajectory_current_records WHERE rowid=7')
        con.commit(); con.close()
        self.merge(self.collect())
        self.assertEqual(self.index['coverage']['archived_spans'],7)
        self.assertEqual(self.index['traces'][0]['retained_spans'],1)

    def test_budget_queue_rotates_without_claiming_old_reads_are_current(self):
        self.make_db('b'*64)
        self.make_db('d'*64)
        with patch.object(remote, 'ARCHIVE_RUN_BYTES', 1):
            first = self.collect(); self.merge(first)
            second = self.collect(); self.merge(second)
        self.assertNotEqual(first['last_database'],second['last_database'])
        self.assertEqual(self.index['coverage']['archived_traces'],2)
        self.assertEqual(self.index['coverage']['pending_databases'],1)


if __name__ == '__main__':
    unittest.main()
