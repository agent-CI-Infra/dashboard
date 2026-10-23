import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from gsb.board import BoardStore
from gsb.collectors import Context, _ops_releases
from gsb.config import Config
from gsb.public_sections import public_ops, public_tests, score_history, extend_project
from gsb.reports import parse_report_zip
from test_reports import archive

ROOT = Path(__file__).resolve().parents[1]


class PublicSectionsTests(unittest.TestCase):
    def test_default_board_does_not_read_or_overwrite_local_notes(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            cfg = Config(repo='example/repo', data_dir=Path(directory))
            board = BoardStore(cfg)
            board.update_item('issue:1', {'note': 'PRIVATE-LOCAL-NOTE', 'priority': 'P0'})
            previous = (cfg.data_dir / 'board.json').read_bytes()
            ctx = Context(None, cfg, datetime.now(timezone.utc), {'html_url': cfg.repo_url})
            doc = {'issues': {'items': [{'number': 1, 'state': 'open', 'title': 'Public issue', 'url': cfg.repo_url+'/issues/1'}]}, 'prs': None, 'quality': {'runs': []}, 'generated_at': '2026-09-20T12:00:00Z'}
            with patch('gsb.public_sections.collect_ci', return_value={}), patch('gsb.public_sections.public_tests', return_value={}), patch('gsb.public_sections.public_ops', return_value={}):
                extend_project(doc, ctx)
            self.assertNotIn('PRIVATE-LOCAL-NOTE', json.dumps(doc))
            self.assertIsNone(doc['board']['items'][0]['priority'])
            self.assertEqual((cfg.data_dir / 'board.json').read_bytes(), previous)

    def test_public_ops_never_calls_private_collectors(self):
        calls = []
        def collector(name, result=None):
            def run(*args):
                calls.append(name)
                return result
            return (run, name)
        blocks = {k: collector(k) for k in ('releases', 'branches', 'community', 'activity', 'commits', 'stale_automation', 'security', 'traffic')}
        class GH:
            def get(self, path, *args):
                self_path = '/repos/example/repo/security-advisories'
                assert path == self_path
                return [{'state': 'draft', 'summary': 'PRIVATE-DRAFT'}, {'state': 'published', 'summary': 'Public advisory'}]
        ctx = Context(GH(), Config(repo='example/repo'), datetime.now(timezone.utc))
        with patch('gsb.public_sections.OPS_BLOCKS', blocks):
            doc = public_ops(ctx)
        self.assertNotIn('security', calls)
        self.assertNotIn('traffic', calls)
        self.assertNotIn('PRIVATE-DRAFT', json.dumps(doc))
        self.assertEqual(len(doc['public_advisories']), 1)

    def test_release_drafts_excluded_even_with_privileged_credential(self):
        class GH:
            def paginate(self, path, *args, **kwargs):
                return [{'draft': True, 'name': 'PRIVATE-DRAFT'}] if path.endswith('/releases') else []
        doc = _ops_releases(Context(GH(), Config(repo='example/repo'), datetime.now(timezone.utc)), [])
        self.assertEqual(doc['count'], 0)
        self.assertNotIn('PRIVATE-DRAFT', json.dumps(doc))

    def test_coverage_is_not_a_test_count(self):
        doc = parse_report_zip(archive({'lcov.info': 'SF:src/main.py\nLF:200\nLH:160\nend_of_record\n'}))
        self.assertIsNone(doc['tests'])
        self.assertEqual(doc['coverage'][0]['lines_pct'], 80)
        doc = parse_report_zip(archive({'coverage.xml': '<coverage line-rate="0.75"/>', 'junit.xml': '<testsuite tests="4" failures="1"/>'}))
        self.assertEqual((doc['tests'], doc['passed']), (4, 3))
        self.assertEqual(doc['coverage'][0]['lines_pct'], 75)

    def test_structural_test_files_do_not_invent_executed_cases(self):
        class GH:
            def get_text_file(self, *args): return '{}'
        ctx = Context(GH(), Config(repo='example/repo'), datetime.now(timezone.utc))
        run = {'id': 1, 'sha': 'a'*40, 'attempt': 2, 'coverage': [{'lines_pct': 80}]}
        with patch('gsb.public_sections._tree_paths', return_value=(['services/core/a.py', 'services/core/tests/test_a.py'], 'github:git-tree')):
            doc = public_tests(ctx, [run])
        self.assertEqual(doc['tree']['total'], 1)
        self.assertEqual(doc['executed'], [])
        self.assertIsNone(doc['inventory'][0]['ci_cases'])
        self.assertIn('attempt 2', doc['coverage']['source'])

    def test_complete_case_paths_restore_package_counts_but_truncated_reports_do_not(self):
        class GH:
            def get_text_file(self, *args): return '{}'
        ctx = Context(GH(), Config(repo='example/repo'), datetime.now(timezone.utc))
        report = dict(artifact_id=1,name='ut-results',url='https://github.com/example/repo/actions/runs/1',layer='unit',counts=dict(tests=1,passed=0,failed=1,skipped=0,flaky=0),cases=[dict(name='case',file='services/core/tests/test_a.py',status='failed')])
        run = dict(id=1,sha='a'*40,attempt=2,branch='main',updated_at='2026-09-20',tests=[report],coverage=[{'lines_pct':80}])
        with patch('gsb.public_sections._tree_paths', return_value=(['services/core/a.py', 'services/core/tests/test_a.py'], 'github:git-tree')):
            doc=public_tests(ctx,[run])
            self.assertEqual((doc['inventory'][0]['ci_cases'],doc['inventory'][0]['ci_failed']),(1,1))
            report['counts']['tests']=2
            self.assertIsNone(public_tests(ctx,[run])['inventory'][0]['ci_cases'])

    def test_log_breakdowns_keep_counts_without_command_arguments(self):
        log='$ node --test --private-key=DO-NOT-PUBLISH\n# tests 2\n# pass 1\n# fail 1\n# skipped 0\n'
        report=parse_report_zip(archive({'run.log':log}))
        self.assertEqual(report['tests'],2)
        self.assertEqual(report['commands'][0]['failed'],1)
        self.assertNotIn('DO-NOT-PUBLISH',json.dumps(report))

    def test_real_e2e_scores_reach_public_executed_report(self):
        class GH:
            def get_text_file(self, *args): return '{}'
        ctx = Context(GH(), Config(repo='example/repo'), datetime.now(timezone.utc))
        score = dict(case='TC-E2E-01',family='research-team',delivery='passed',quality_status='scored',duration_ms=1000,metrics=[dict(label='Judge total',value=90,unit='score100')])
        report = dict(artifact_id=1,name='real-e2e-results',url='https://github.com/example/repo/actions/runs/1',layer='e2e',counts=dict(tests=1,passed=1,failed=0,skipped=0,flaky=0),cases=[],scores=[score])
        run = dict(id=1,sha='a'*40,attempt=1,branch='main',updated_at='2026-09-20',tests=[report])
        with patch('gsb.public_sections._tree_paths', return_value=([], 'github:git-tree')):
            doc = public_tests(ctx, [run])
        self.assertEqual(doc['executed'][0]['scores'], [score])
        self.assertEqual(doc['executed'][0]['url'], report['url'])
        self.assertEqual(len(doc['score_history']['TC-E2E-01']), 1)
        self.assertEqual(doc['score_history']['TC-E2E-01'][0]['family'], 'research-team')

    def test_score_history_uses_latest_attempt_and_keeps_native_metric_values(self):
        def run(ident, attempt, day, value):
            return dict(id=ident, attempt=attempt, created_at=f'2026-09-{day:02d}T12:00:00Z',
                        url=f'https://github.com/example/repo/actions/runs/{ident}', tests=[dict(
                            name='real-e2e-results', url=f'https://github.com/example/repo/actions/runs/{ident}/artifacts/{attempt}',
                            scores=[dict(case='DRB-59', delivery='passed', quality_status='scored', duration_ms=attempt * 60000,
                                         metrics=[dict(label='RACE', value=value, unit='ratio')])])])
        points = score_history([run(1, 1, 18, .40), run(2, 1, 19, .52), run(1, 2, 18, .45)])['DRB-59']
        self.assertEqual([p['run_id'] for p in points], [1, 2])
        self.assertEqual([p['metrics'][0]['value'] for p in points], [.45, .52])
        self.assertEqual([p['duration_ms'] for p in points], [120000, 60000])
        self.assertEqual(points[0]['attempt'], 2)
