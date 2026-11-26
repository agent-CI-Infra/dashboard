import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sfs', Path(__file__).with_name('collect-production-sfs-traces.py'))
sfs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sfs)


class SfsTests(unittest.TestCase):
    def test_ssh_is_pinned_and_sends_only_command_payload_on_stdin(self):
        with patch.dict(os.environ, {'SFS_SSH_KEY_FILE': 'key', 'SFS_KNOWN_HOSTS_FILE': 'hosts', 'SFS_SSH_JUMP': 'hk-leihui'}), \
             patch.object(sfs.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{}')) as run:
            self.assertEqual(sfs.ssh_operation({'mode': 'inventory'}), {})
        args = run.call_args.args[0]
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertIn('UserKnownHostsFile="hosts"', args)
        self.assertEqual(args[args.index('-J') + 1], 'hk-leihui')
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['payload'], {'mode': 'inventory'})

    def test_failure_never_returns_raw_stderr(self):
        with patch.dict(os.environ, {'SFS_SSH_KEY_FILE': 'key', 'SFS_KNOWN_HOSTS_FILE': 'hosts'}), \
             patch.object(sfs.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr='PRIVATE')):
            with self.assertRaisesRegex(RuntimeError, 'previous snapshot retained') as error:
                sfs.ssh_operation({'mode': 'inventory'})
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_prefix_cache_is_loaded_only_for_the_production_sfs_collection(self):
        with patch.dict(os.environ, {'SFS_SSH_KEY_FILE': 'key', 'SFS_KNOWN_HOSTS_FILE': 'hosts'}), \
             patch.object(sfs.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{}')) as run:
            sfs.ssh_operation({'mode': 'collect', 'prefix_cache': True})
        program = json.loads(run.call_args.kwargs['input'])['source']
        namespace = {'__name__': 'sfs_test'}
        exec(compile(program, '<sfs_test>', 'exec'), namespace)
        self.assertEqual(namespace['collect_prefix_cache']([], namespace['path_for'])['days'], [])

    def test_coverage_below_threshold_is_rejected(self):
        with patch.object(sfs, 'select_workspaces', return_value=([], 100, {})):
            with patch.object(sfs.collector, 'collect_workspaces', return_value={'binding_count': 100, 'read_log_count': 94}):
                with self.assertRaisesRegex(RuntimeError, 'coverage'):
                    sfs.collect()
            with patch.object(sfs.collector, 'collect_workspaces', return_value={'binding_count': 100, 'read_log_count': 95}), \
                 patch.object(sfs, 'ssh_operation', return_value={'coverage': {}, 'days': []}):
                self.assertEqual(sfs.collect()['collection_source'], 'production_sfs_ssh')

    def test_hive_is_read_even_without_legacy_request_events(self):
        name = 'w-' + 'a' * 64
        hive = {'schema': 'digitalmate/hive-daily@1', 'coverage': {'expected': 1}, 'days': []}
        with patch.object(sfs, 'select_workspaces', return_value=([name], 1, {})), \
             patch.object(sfs.collector, 'collect_workspaces', return_value={'binding_count': 1, 'read_log_count': 1, 'days': []}), \
             patch.object(sfs, 'ssh_operation', return_value=hive) as operation:
            result = sfs.collect()
        self.assertIs(result['hive_daily'], hive)
        self.assertEqual(operation.call_args.args[0], {'names': [name]})
        source = operation.call_args.kwargs['remote_source']
        self.assertIn('copy_archive_image(db, local, before)', source)
        self.assertIn('collect_hive_daily', source)

    def test_declared_production_volume_resolves_without_a_log_download(self):
        name = 'digitalmate-w-' + 'a' * 64

        def binding(volumes, tenant='jiuwen'):
            return {'binding': {'scope': {'tenant': tenant, 'template': 'digitalmate',
                                          'template_ref': {'alias': 'production'}},
                                'current': {'environment_id': 'environment'}, 'volumes': volumes}}

        direct = binding({'digitalmate-workspace-sfs-' + name: 'volume-uuid'})
        legacy = binding({})
        other_tenant = binding({'digitalmate-workspace-sfs-w-' + 'c' * 64: 'other'}, tenant='test')
        earlier_name = 'w-' + 'b' * 64
        with patch.object(sfs.collector.traces, 'TOKEN', 'configured'), \
             patch.object(sfs.collector.traces, 'binding_list', return_value=[direct, legacy, direct, other_tenant]), \
             patch.object(sfs.collector, 'match_workspaces_from_logs', return_value=({earlier_name}, {})) as fallback:
            self.assertEqual(sfs.select_workspaces(), ([name, earlier_name], 3, {'duplicate_workspace': 1}))
        fallback.assert_called_once_with([legacy], sfs.ssh_operation)

if __name__ == '__main__':
    unittest.main()
