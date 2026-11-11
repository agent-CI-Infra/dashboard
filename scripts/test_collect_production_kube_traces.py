import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
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
