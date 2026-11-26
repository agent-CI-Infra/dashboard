import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from production_prefix_cache import CACHE_LOGS, CACHE_TZ, cache_call, collect_prefix_cache, retain_prefix_cache


DAY = dt.datetime(2026, 11, 17, 12, tzinfo=CACHE_TZ)


def event(source, index, inputs, cached, *, valid=True, date=DAY, phase='post_call', session='PRIVATE_SESSION'):
    call = format(index, '032x')
    when = date + dt.timedelta(seconds=index)
    if source == 'beebou':
        return {'schema': 'digitalmate/beebou-prefix-cache@1', 'at_ms': int(when.timestamp() * 1000),
                'call_id': call, 'session_key': session, 'model': 'test/model',
                'input_tokens': inputs, 'cache_read_tokens': cached}
    return {'event': 'context.usage', 'payload': {
        'schema_version': 'context-usage.v1', 'phase': phase, 'request_id': call,
        'timestamp': when.isoformat(), 'execution_session_id': session, 'model': 'test/model',
        'deployment': 'PRIVATE_DEPLOYMENT', 'kv_cache': {'request': {
            'input_tokens': inputs, 'cache_read_tokens': cached, 'authoritative': valid,
            'cache_tokens': 0,
        }},
    }}


def write_rows(root, source, rows, suffix=''):
    file = root / (CACHE_LOGS[source][0] + suffix)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')


class PrefixCacheTests(unittest.TestCase):
    def test_same_measures_across_both_producers_without_exporting_session_or_call_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for source in CACHE_LOGS:
                # Cold, stable prefix, reproduced near miss, missing usage,
                # and smaller input after context changes.
                rows = [event(source, i, inputs, cached) for i, (inputs, cached) in enumerate(
                    [(2048, 0), (2048, 1920), (2048, 4), (2048, None), (512, 0)], 1)]
                write_rows(root, source, rows[:2], '.1')
                write_rows(root, source, rows[1:] + (
                    [event(source, 10, 2048, 0, phase='post_compact')] if source != 'beebou' else []))
            snapshot = collect_prefix_cache(['PRIVATE_WORKSPACE'],
                                            lambda _name, path: (root / path).resolve(strict=True), now=DAY)

        day, = snapshot['days']
        self.assertEqual(day['calls'], 10)
        for source in CACHE_LOGS:
            groups = {g['cohort']: g for g in day['groups'] if g['source'] == source}
            self.assertEqual((groups['first_observed']['near_miss'], groups['first_observed']['known']), (1, 1))
            later = groups['returning']
            self.assertEqual((later['calls'], later['known'], later['miss_eligible'], later['near_miss']), (4, 3, 2, 1))
            self.assertEqual((later['input_tokens'], later['cache_read_tokens']), (4608, 1924))
            self.assertEqual(later['histogram'], [(0, 1), (1, 1), (937, 1)])
            self.assertEqual(day['sessions'][source], {'known': 1, 'p50': 1924 / 6656, 'p95': 1924 / 6656})
            self.assertEqual(snapshot['coverage'][source]['with_calls'], 1)
        serialized = json.dumps(snapshot)
        self.assertNotIn('PRIVATE', serialized)
        self.assertNotIn(format(2, '032x'), serialized)

    def test_invalid_or_nonauthoritative_counts_remain_unknown(self):
        for source in CACHE_LOGS:
            for inputs, cached in [(None, 0), (1024, True), (1024, -1), (1024, 1025), (0, 0), (1024, '0')]:
                with self.subTest(source=source, inputs=inputs, cached=cached):
                    self.assertIsNone(cache_call(json.dumps(event(source, 1, inputs, cached)), source).cached)
        for source in ('bees',):
            self.assertIsNone(cache_call(json.dumps(event(source, 1, 1024, 0, valid=False)), source).cached)

    def test_conflicting_call_ids_are_excluded_and_unknown_sessions_still_contribute(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = 'beebou'
            write_rows(root, source, [event(source, 1, 2048, 0), event(source, 1, 2048, 2048),
                                      event(source, 2, 2048, 2048, session=None)])
            snapshot = collect_prefix_cache(['workspace'], lambda _, path: (root / path).resolve(strict=True), now=DAY)
        self.assertEqual(snapshot['coverage'][source]['conflicting_calls'], 1)
        group, = snapshot['days'][0]['groups']
        self.assertEqual((group['calls'], group['known'], group['cohort']), (1, 1, 'unknown'))
        self.assertEqual(snapshot['days'][0]['sessions'][source]['known'], 0)

    def test_detection_requires_baseline_volume_and_flags_each_returning_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for source in CACHE_LOGS:
                rows = []
                for days_ago in (3, 2, 1, 0):
                    date = DAY - dt.timedelta(days=days_ago)
                    for i in range(22):
                        cached = 0 if i == 0 or (not days_ago and i < 8) else 1800
                        rows.append(event(source, days_ago * 100 + i, 2048, cached, date=date))
                write_rows(root, source, rows)
            snapshot = collect_prefix_cache(['workspace'], lambda _, path: (root / path).resolve(strict=True), now=DAY)
        retained = retain_prefix_cache(None, snapshot)
        old_day, _, _, today = retained['days']
        self.assertEqual(old_day['watch']['beebou']['status'], 'insufficient_baseline')
        self.assertEqual(today['watch']['beebou']['status'], 'spike')
        self.assertEqual(today['watch']['bees']['status'], 'spike')
        self.assertEqual(today['watch']['bees']['near_miss'], 8)
        self.assertEqual(today['watch']['bees']['baseline_max'], 1)
        snapshot['days'][-1]['incomplete_sources'] = ['bees']
        self.assertEqual(retain_prefix_cache(None, snapshot)['days'][-1]['watch']['bees']['status'], 'incomplete')

    def test_rotation_retains_more_complete_day_and_expires_outside_window(self):
        def row(date, calls, **extra):
            return {'date': date, 'calls': calls, 'groups': [{'source': 'bees', 'calls': calls,
                    'cohort': 'returning', 'near_miss': 0, 'miss_eligible': calls}], **extra}

        snapshot = {'schema': 'digitalmate/prefix-cache-daily@1', 'days': [
            row('2026-11-16', 10, observed_at='older'),
            row('2026-10-01', 50),
        ]}
        current = {'schema': snapshot['schema'], 'start': '2026-10-19', 'today': '2026-11-17',
                   'days': [row('2026-11-16', 4)]}
        day, = retain_prefix_cache(snapshot, current)['days']
        self.assertTrue(day['retained'])
        self.assertEqual(day['observed_at'], 'older')
        self.assertEqual(day['calls'], 10)

    def test_collector_does_not_read_legacy_workswarm_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_rows(root, 'bees', [event('bees', 1, 2048, 1024)])
            attempted = []

            def resolve(_, path):
                attempted.append(path)
                return (root / path).resolve(strict=True)

            snapshot = collect_prefix_cache(['workspace'], resolve, now=DAY)
        self.assertFalse(any(path.startswith('workswarm/') for path in attempted))
        self.assertEqual(snapshot['days'][0]['calls'], 1)
        self.assertEqual(set(snapshot['coverage']), {'bees', 'beebou'})

    def test_legacy_history_is_filtered_before_comparing_population_sizes(self):
        def row(date, bee_calls, legacy_calls):
            return {'date': date, 'calls': bee_calls + legacy_calls,
                    'groups': [{'source': source, 'cohort': 'returning', 'calls': calls,
                                'near_miss': 0, 'miss_eligible': calls}
                               for source, calls in [('bees', bee_calls), ('workswarm', legacy_calls)] if calls],
                    'sessions': {'bees': {'known': 1}, 'workswarm': {'known': 100}},
                    'incomplete_sources': ['workswarm']}

        previous = {'schema': 'digitalmate/prefix-cache-daily@1', 'days': [
            row('2026-11-14', 0, 100), row('2026-11-15', 10, 100), row('2026-11-16', 4, 100),
        ]}
        current = {'schema': previous['schema'], 'start': '2026-10-19', 'today': '2026-11-17',
                   'coverage': {'bees': {'with_calls': 1}, 'workswarm': {'with_calls': 100}},
                   'days': [row('2026-11-15', 3, 0), row('2026-11-16', 6, 0)]}
        result = retain_prefix_cache(previous, current)
        older, newer = result['days']
        self.assertEqual((older['date'], older['calls'], older['retained']), ('2026-11-15', 10, True))
        self.assertEqual((newer['date'], newer['calls']), ('2026-11-16', 6))
        self.assertNotIn('workswarm', json.dumps(result))
        self.assertEqual(previous['days'][0]['calls'], 100)
