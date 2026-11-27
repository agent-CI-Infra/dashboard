import datetime as dt
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from production_reply_metrics import merge_reply_events, read_node_replies
from production_cloud_replies import collect_cloud_replies

NOW = dt.datetime(2026, 11, 27, 2, tzinfo=dt.timezone.utc)
START = int(dt.datetime(2026, 11, 26, 16, 5, tzinfo=dt.timezone.utc).timestamp() * 1000)
ENV = 'adx-11111111-2222-3333-4444-555555555555'


def receive(stamp=START, scope='PRIVATE_WORKSPACE', mid='PRIVATE_MESSAGE'):
    return {'scope': scope, 'message_id': mid, 'at_ms': stamp, 'event_type': 'digitalmate_hive_message_received'}


def reply(stamp=START + 5000, phase='normal', via='response', **kwargs):
    return {**receive(stamp, **kwargs), 'event_type': 'digitalmate_hive_reply_visible',
            'phase': phase, 'via': via, 'text_chars': 10}


class ReplyTests(unittest.TestCase):
    def merge(self, events, previous=None):
        return merge_reply_events(previous, events, 'PRIVATE_SALT', now=NOW)

    def test_current_clock_has_its_own_metric_and_shanghai_day(self):
        result = self.merge([receive(), reply(), reply(START + 900, 'quick', None), reply(START + 5100, via='push')])
        day = result['daily']['days'][0]
        self.assertEqual(day['date'], '2026-11-27')
        self.assertEqual(day['normal_response']['mean_ms'], 5000)
        self.assertEqual(day['quick_send']['mean_ms'], 900)
        self.assertEqual(day['normal_push']['mean_ms'], 5100)
        self.assertNotIn('first_token', day)
        for value in ('PRIVATE_MESSAGE', 'PRIVATE_WORKSPACE', 'PRIVATE_SALT'):
            self.assertNotIn(value, json.dumps(result))

    def test_resumed_and_replayed_logs_do_not_add_samples(self):
        batch = [receive(), reply()]
        first = self.merge(batch + batch)
        second = self.merge(batch, first['state'])
        self.assertEqual(first['daily']['days'], second['daily']['days'])
        self.assertEqual(second['daily']['days'][0]['normal_response']['n'], 1)

    def test_rotation_keeps_old_pairs_and_can_finish_an_earlier_receive(self):
        first = self.merge([receive()])
        second = self.merge([reply()], first['state'])
        third = self.merge([], second['state'])
        self.assertEqual(third['daily']['days'][0]['normal_response']['n'], 1)

    def test_retried_receive_is_ambiguous_and_cannot_look_faster(self):
        first = self.merge([receive(), reply()])
        later = self.merge([receive(START + 100), reply(START + 200)], first['state'])
        self.assertEqual(later['daily']['days'], [])
        self.assertEqual(later['daily']['rejected']['ambiguous_receive'], 1)

    def test_missing_receive_failure_empty_text_and_negative_clock_are_not_samples(self):
        self.assertEqual(self.merge([reply()])['daily']['days'], [])
        for event in (reply(START - 1), reply(phase='failed'), {**reply(), 'text_chars': 0}, reply(phase='bee')):
            day = self.merge([receive(), event])['daily']['days'][0]
            self.assertEqual(sum(day[key]['n'] for key in ('normal_response', 'normal_push', 'quick_send')), 0)

    def test_same_message_id_in_two_workspaces_is_two_samples(self):
        result = self.merge([receive(), reply(), receive(scope='other'), reply(scope='other')])
        self.assertEqual(result['daily']['days'][0]['normal_response']['n'], 2)

    def test_identity_key_rotation_fails_instead_of_double_counting(self):
        first = self.merge([receive(), reply()])
        with self.assertRaisesRegex(ValueError, 'migration required'):
            merge_reply_events(first['state'], [], 'new-salt', now=NOW)

    def test_reader_includes_exact_current_environment_resumes_and_gzip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a = {**receive(), 'text': 'PRIVATE_CONTENT'}
            b = {**reply(), 'headers': 'PRIVATE_CREDENTIAL'}
            (root / (ENV + '-abc12345678-r7.out')).write_text(json.dumps(a) + '\n', encoding='utf8')
            with gzip.open(root / (ENV + '-abc12345678.err.gz'), 'wt', encoding='utf8') as f:
                f.write(json.dumps(b) + '\n')
            (root / (ENV + 'other.out')).write_text(json.dumps(a), encoding='utf8')
            (root / 'adx-other.out').write_text(json.dumps(a), encoding='utf8')
            found = read_node_replies({ENV: 'scope'}, directory=folder)
            self.assertEqual(found['coverage']['files'], 2)
            self.assertEqual(len(found['events']), 2)
            self.assertNotIn('PRIVATE_CONTENT', json.dumps(found))
            self.assertNotIn('PRIVATE_CREDENTIAL', json.dumps(found))

    def test_compressed_log_read_budget_is_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            with gzip.open(Path(folder) / (ENV + '.out.gz'), 'wt') as f:
                f.write('x' * 300)
            with patch('production_reply_metrics.REPLY_READ_BYTES', 100):
                with self.assertRaisesRegex(ValueError, 'decompression'):
                    read_node_replies({ENV: 'scope'}, directory=folder)

    def test_cluster_failure_is_not_a_fresh_empty_snapshot(self):
        inventory = [{'binding': {'scope': {'tenant': 'jiuwen', 'template': 'digitalmate',
                                             'template_ref': {'alias': 'production'}, 'binding_id': 'scope'},
                                  'current': {'environment_id': ENV}}}]
        with self.assertRaisesRegex(RuntimeError, 'previous snapshot retained'):
            collect_cloud_replies('salt', None, operation=lambda *a, **k: {'ok': False},
                                  binding_reader=lambda *a, **k: inventory,
                                  credentials={'ca': 'ca', 'cert': 'cert', 'key': 'key'})


if __name__ == '__main__':
    unittest.main()
