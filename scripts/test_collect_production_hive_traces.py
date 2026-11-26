import copy
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
remote = types.ModuleType('hive_test_remote')
exec('\n'.join((HERE / f).read_text(encoding='utf-8').rsplit('\nif __name__', 1)[0]
               for f in ('kube-workspace-remote.py', 'sfs-trajectory-remote.py', 'production_hive_metrics.py', 'sfs-hive-remote.py')),
     remote.__dict__)
NOW = dt.datetime(2026, 11, 26, 2, tzinfo=dt.timezone.utc)
START = int(dt.datetime(2026, 11, 25, tzinfo=remote.HIVE_TZ).timestamp() * 1000)


class HiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'sfs'
        self.root.mkdir()
        self.name = 'w-' + 'a' * 64
        self.addCleanup(patch.stopall)
        patch.object(remote, 'ROOT', self.root).start()

    def database(self, name=None):
        path = self.root / (name or self.name) / 'digitalmate/digitalmate.db'
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript('''
          CREATE TABLE dm_hive_inputs (seq INTEGER,workspace_id TEXT,principal_id TEXT,message_id TEXT,
            target_session_ref TEXT,kind TEXT,role TEXT,created_at_ms INTEGER,text TEXT);
          CREATE TABLE dm_hive_reply_records (workspace_id TEXT,principal_id TEXT,message_id TEXT,session_ref TEXT,
            normal_claimed_at_ms INTEGER,quick_committed_at_ms INTEGER,quick_state TEXT);
          CREATE TABLE dm_hive_runs (workspace_id TEXT,principal_id TEXT,run_id TEXT,queue_seq INTEGER,
            state TEXT,end_kind TEXT,started_at_ms INTEGER,ended_at_ms INTEGER);
          CREATE TABLE dm_hive_turn_queue (workspace_id TEXT,principal_id TEXT,run_id TEXT,seq INTEGER,created_at_ms INTEGER);
          CREATE TABLE dm_hive_tasks (created_at_ms INTEGER,state TEXT);
          CREATE TABLE dm_hive_beebou_turn_failures (created_at_ms INTEGER,kind TEXT);
        ''')
        return path, con

    def input(self, con, seq=1, created=START):
        con.execute('INSERT INTO dm_hive_inputs VALUES (?,?,?,?,?,?,?,?,?)',
                    (seq, 'scope', 'principal', 'SECRET_MESSAGE' + str(seq), 'SECRET_SESSION', 'user', 'USER', created, 'SECRET_BODY'))
        con.execute('INSERT INTO dm_hive_reply_records VALUES (?,?,?,?,?,?,?)',
                    ('scope', 'principal', 'SECRET_MESSAGE' + str(seq), 'SECRET_SESSION', created + 10, created + 3000, 'COMMITTED'))

    def collect(self, names=None):
        return remote.collect_hive_daily(names or [self.name], remote.path_for, remote.hive_snapshot, now=NOW)

    def day(self, output):
        return next(d for d in output['days'] if d['date'] == '2026-11-25')

    def test_new_hive_db_without_process_log_and_without_consume_kind(self):
        path, con = self.database()
        self.input(con)
        for number, state, kind, end in [(1, 'ENDED', 'natural', START + 1100),
                                        (2, 'ENDED', 'stopped', START + 2100),
                                        (3, 'RUNNING', None, None)]:
            con.execute('INSERT INTO dm_hive_runs VALUES (?,?,?,?,?,?,?,?)',
                        ('scope', 'principal', str(number), number, state, kind, START + 100, end))
            con.execute('INSERT INTO dm_hive_turn_queue VALUES (?,?,?,?,?)',
                        ('scope', 'principal', str(number), number, START))
        con.commit(); con.close()
        before = path.read_bytes()
        result = self.collect(); day = self.day(result)
        self.assertEqual(result['coverage']['read'], 1)
        self.assertEqual(day['counts']['user_inputs'], 1)
        self.assertEqual(day['counts']['runs_unended'], 1)
        self.assertEqual(day['metrics']['bee_queue_wait']['n'], 3)
        self.assertEqual(day['metrics']['bee_run_natural']['mean_ms'], 1000)
        self.assertEqual(day['metrics']['bee_run_ended']['n'], 2)
        self.assertEqual(day['metrics']['quick_commit']['mean_ms'], 3000)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(day['partial'])
        serialized = json.dumps(result)
        for secret in ('SECRET', 'principal', 'scope', self.name, 'gateway', 'answer_first', 'first_token'):
            self.assertNotIn(secret, serialized)

    def test_same_scope_and_sequence_in_different_workspaces_are_both_counted(self):
        names = [self.name, 'w-' + 'b' * 64]
        for name in names:
            _, con = self.database(name)
            self.input(con); con.commit(); con.close()
        day = self.day(self.collect(names))
        self.assertEqual(day['counts']['user_inputs'], 2)
        self.assertEqual(day['active_workspaces'], 2)

    def test_beijing_midnight_cohort_and_cross_day_completion(self):
        _, con = self.database()
        self.input(con, seq=1, created=START - 1)
        self.input(con, seq=2, created=START)
        con.execute('INSERT INTO dm_hive_runs VALUES (?,?,?,?,?,?,?,?)',
                    ('scope', 'principal', 'run', 1, 'ENDED', 'natural', START + 86399000, START + 86401000))
        con.commit(); con.close()
        result = self.collect()
        self.assertEqual(self.day(result)['counts']['user_inputs'], 1)
        self.assertEqual(self.day(result)['metrics']['bee_run_natural']['mean_ms'], 2000)
        self.assertEqual(next(d for d in result['days'] if d['date'] == '2026-11-24')['counts']['user_inputs'], 1)

    def test_changed_copy_is_rejected_and_retention_does_not_claim_freshness(self):
        _, con = self.database(); self.input(con); con.commit(); con.close()
        old = self.collect()
        with patch.object(remote, 'copy_archive_image', return_value=None):
            fresh = self.collect()
        self.assertEqual(fresh['coverage']['incomplete'], 1)
        self.assertEqual(fresh['days'], [])
        merged = remote.retain_hive_daily(old, fresh)
        self.assertTrue(merged['days'][0]['retained'])
        self.assertEqual(merged['days'][0]['observed_at'], old['days'][0]['observed_at'])

    def test_row_budget_rejects_entire_workspace_and_unknown_labels_are_sanitized(self):
        _, con = self.database(); self.input(con); self.input(con, seq=2)
        con.execute('INSERT INTO dm_hive_tasks VALUES (?,?)', (START, 'SECRET_LABEL'))
        con.execute('INSERT INTO dm_hive_beebou_turn_failures VALUES (?,?)', (START, 'SECRET_REASON'))
        con.commit(); con.close()
        result = self.collect()
        self.assertEqual(self.day(result)['task_states'], {'other': 1})
        self.assertNotIn('SECRET', json.dumps(result))
        with patch.object(remote, 'HIVE_ROWS', 1):
            limited = self.collect()
        self.assertEqual(limited['days'], [])
        self.assertEqual(limited['coverage']['incomplete'], 1)

    def test_invalid_timing_is_missing_and_does_not_become_zero(self):
        _, con = self.database(); self.input(con)
        con.execute('UPDATE dm_hive_reply_records SET normal_claimed_at_ms=?,quick_state=?', (START - 10, 'LATE'))
        con.commit(); con.close()
        day = self.day(self.collect())
        self.assertEqual(day['metrics']['normal_claim']['n'], 0)
        self.assertIsNone(day['metrics']['normal_claim']['mean_ms'])
        self.assertEqual(day['metrics']['quick_commit']['n'], 0)
        self.assertEqual(day['invalid_timestamps'], 1)

    def test_image_limit_is_explicit_partial_coverage(self):
        _, con = self.database(); self.input(con); con.commit(); con.close()
        with patch.object(remote, 'HIVE_DB_BYTES', 1):
            result = self.collect()
        self.assertEqual(result['coverage']['incomplete'], 1)
        self.assertEqual(result['days'], [])

    def test_wal_source_is_copied_without_opening_original_with_sqlite(self):
        path, con = self.database()
        self.addCleanup(con.close)
        con.execute('PRAGMA journal_mode=WAL'); self.input(con); con.commit()
        original = sqlite3.connect
        opened = []
        def connect(database, *args, **kwargs):
            opened.append(database)
            return original(database, *args, **kwargs)
        with patch.object(remote.sqlite3, 'connect', side_effect=connect):
            result = self.collect()
        self.assertEqual(self.day(result)['counts']['user_inputs'], 1)
        self.assertTrue(opened)
        self.assertTrue(all(str(self.root) not in uri for uri in opened))
        con.close()

    def test_retention_keeps_widest_cohort_and_replaces_live_run_status(self):
        _, con = self.database(); self.input(con); con.commit(); con.close()
        old = self.collect(); fresh = copy.deepcopy(old)
        self.day(fresh)['counts']['user_inputs'] = 0
        self.assertTrue(self.day(remote.retain_hive_daily(old, fresh))['retained'])
        fresh = copy.deepcopy(old)
        self.day(fresh)['counts']['runs_unended'] = 0
        self.assertFalse(self.day(remote.retain_hive_daily(old, fresh))['retained'])


if __name__ == '__main__':
    unittest.main()
