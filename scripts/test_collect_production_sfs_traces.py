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
        self.assertEqual(json.loads(run.call_args.kwargs['input']), {'mode': 'inventory'})

    def test_failure_never_returns_raw_stderr(self):
        with patch.dict(os.environ, {'SFS_SSH_KEY_FILE': 'key', 'SFS_KNOWN_HOSTS_FILE': 'hosts'}), \
             patch.object(sfs.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr='PRIVATE')):
            with self.assertRaisesRegex(RuntimeError, 'previous snapshot retained') as error:
                sfs.ssh_operation({'mode': 'inventory'})
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_coverage_below_threshold_is_rejected(self):
        with patch.object(sfs.collector, 'collect', return_value={'binding_count': 100, 'read_log_count': 94}):
            with self.assertRaisesRegex(RuntimeError, 'coverage'):
                sfs.collect()
        with patch.object(sfs.collector, 'collect', return_value={'binding_count': 100, 'read_log_count': 95}):
            self.assertEqual(sfs.collect()['collection_source'], 'production_sfs_ssh')

if __name__ == '__main__':
    unittest.main()
