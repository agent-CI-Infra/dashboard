import io
import json
import unittest
import zipfile
from gsb.reports import playwright, junit, parse_report_zip, artifact_layer
from gsb.project import channel, run_details, slim_run, build_project
from gsb.config import Config


def archive(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, value in files.items(): z.writestr(name, value)
    return buf.getvalue()


class ReportsTests(unittest.TestCase):
    def test_playwright_retries_are_not_extra_cases(self):
        doc = {'suites': [{'specs': [{'title': 'journey', 'tests': [
            {'status': 'flaky', 'results': [{'status': 'failed'}, {'status': 'passed'}]},
            {'status': 'expected', 'results': [{'status': 'failed'}]},
            {'status': 'skipped'}, {'status': 'unexpected'}]}]}]}
        parsed = playwright(doc)
        self.assertEqual([parsed[k] for k in ('tests','passed','failed','skipped','flaky')], [4,1,1,1,1])

    def test_nested_junit_only_counts_leaves(self):
        result = junit('<testsuites tests="5"><testsuite tests="5"><testsuite tests="3" failures="1"/><testsuite tests="2" skipped="1"/></testsuite></testsuites>')
        self.assertEqual([result[k] for k in ('tests','passed','failed','skipped')], [5,3,1,1])

    def test_testcases_take_precedence_over_aggregate(self):
        result = junit('<testsuite tests="100"><testcase name="a"/><testcase name="b"><failure/></testcase></testsuite>')
        self.assertEqual(result['tests'], 2)
        self.assertEqual(result['failed'], 1)

    def test_report_formats_not_double_counted(self):
        parsed = parse_report_zip(archive({'report.xml':'<testsuite tests="9"/>', 'test-results/results.json':json.dumps({'suites': [], 'stats': {'expected':3,'unexpected':1,'skipped':1,'flaky':1}})}))
        self.assertEqual((parsed['tests'], parsed['format']), (6,'playwright'))

    def test_invalid_counts_are_missing(self):
        self.assertIsNone(parse_report_zip(archive({'dashboard-summary.json':json.dumps({'tests':3,'passed':4,'failed':0,'skipped':0,'flaky':0})})))
        self.assertIsNone(parse_report_zip(archive({'trace.zip':'not a test summary'})))

    def test_real_e2e_scores_keep_four_native_shapes_without_private_payloads(self):
        files = {
            'results.json': json.dumps({'suites': [], 'stats': {'expected': 4, 'unexpected': 0, 'skipped': 0, 'flaky': 0}}),
            'drb/benchmark-metrics.json': json.dumps({'case_id': 59, 'integration_status': 'passed', 'prompt': 'PRIVATE', 'evaluation': {'status': 'passed', 'race': {'status': 'completed', 'overall_score': .54}, 'fact': {'status': 'completed', 'citation_accuracy': 80, 'verification_coverage': 90, 'effective_citations': 3}}}),
            'biomni/benchmark-metrics.json': json.dumps({'case_id': 'da-13-3', 'integration_status': 'passed', 'evaluation': {'status': 'scored', 'score': 87}, 'raw_response': 'PRIVATE'}),
            'team/team-metrics.json': json.dumps({'case': 'TC-E2E-01', 'integration': 'passed', 'generation_duration_ms': 1234, 'evaluation': {'status': 'scored', 'total_score': 86.25}, 'artifacts': {'PRIVATE': True}}),
            'puct/evolve-metrics.json': json.dumps({'case': 'PUCT-COMPRESS', 'integration_status': 'passed', 'started_at': '2026-09-20T10:00:00Z', 'finished_at': '2026-09-20T10:01:00Z', 'evaluation': {'status': 'scored', 'score': .69, 'baseline_gate_score': .5, 'best_gate_score': .7}, 'llm_evaluation': {'status': 'error', 'total_score': None, 'error': 'PRIVATE'}}),
        }
        parsed = parse_report_zip(archive(files))
        self.assertEqual(len(parsed['scores']), 4)
        self.assertEqual({row['family'] for row in parsed['scores']}, {'deepresearchbench', 'biomnibench', 'research-team', 'evolve-compression'})
        self.assertEqual(next(row for row in parsed['scores'] if row['case'] == 'PUCT-COMPRESS')['duration_ms'], 60000)
        drb = next(row for row in parsed['scores'] if row['case'] == 'DRB-59')
        self.assertEqual(drb['journey']['source'], 'source-definition')
        self.assertIn('DRB-59', drb['journey']['goal'])
        self.assertEqual(drb['journey']['metadata']['type'], 'real')
        self.assertNotIn('PRIVATE', json.dumps(parsed))

    def test_real_e2e_journey_report_attaches_public_fields_to_matching_score(self):
        report = '''<h1>旅程报告 · DRB-58 Swarm research integration</h1>
        <table><tr><th>用例</th><td>DRB-58 Swarm research integration</td></tr></table>
        <h2>场景目标</h2><p>Research DRB-58 and evaluate delivery.</p>
        <h2>前置条件</h2><ul><li>live generator</li><li>isolated Swarm stack</li></ul>
        <h2>步骤总览</h2><p>本次运行未记录用户步骤。</p>
        <h2>运行元数据（来自 E2E-META）</h2><table>
        <tr><th>类型</th><td>real</td></tr><tr><th>模型</th><td>Live generator</td></tr>
        <tr><th>凭据</th><td>E2E_API_TOKEN</td></tr></table>
        <h2>关键日志</h2><pre>PRIVATE LOG</pre>'''
        parsed = parse_report_zip(archive({
            'e2e-real/test-results/DRB-58/benchmark-metrics.json': json.dumps({
                'case_id': 58, 'integration_status': 'passed', 'generator_model': 'example-model',
                'evaluation': {'status': 'scored', 'race': {'overall_score': .7}}, 'prompt': 'PRIVATE PROMPT'}),
            'e2e-real/journey-reports/deepresearchbench-swarm/DRB-58/report.html': report,
            'e2e-real/journey-reports/deepresearchbench-swarm/DRB-59/report.html':
                report.replace('DRB-58', 'DRB-59'),
        }))
        score = parsed['scores'][0]
        self.assertEqual(score['case'], 'DRB-58')
        self.assertEqual(score['model'], 'example-model')
        self.assertEqual(score['journey']['goal'], 'Research DRB-58 and evaluate delivery.')
        self.assertEqual(score['journey']['preconditions'], ['live generator', 'isolated Swarm stack'])
        self.assertEqual(score['journey']['metadata']['credentials'], 'E2E_API_TOKEN')
        self.assertNotIn('PRIVATE', json.dumps(parsed))

    def test_archive_limit(self):
        with self.assertRaises(ValueError): parse_report_zip(archive({f'{i}.txt':'x' for i in range(3001)}))

    def test_layer_and_channel(self):
        self.assertEqual([artifact_layer(n) for n in ('e2e-results','st-results','ut-results','other')], ['e2e','st','unit','other'])
        self.assertEqual(channel({'name':'CI','event':'schedule'}, {'gate':['CI']}), 'daily')
        self.assertEqual(channel({'name':'CI','event':'release'}, {'gate':['CI']}), 'release')

    def test_previous_attempt_and_different_sha_are_excluded(self):
        class GH:
            def paginate(self, path, **kw):
                if path.endswith('/jobs'): return []
                return [dict(id=i, name='e2e-results', created_at=date, workflow_run={'head_sha':sha}) for i,date,sha in ((1,'2026-01-01','abc'),(2,'2026-01-03','old'),(3,'2026-01-03','abc'))]
            def download_artifact(self, repo, ident, **kw):
                assert ident == 3
                return archive({'report.xml':'<testsuite tests="2"/>'})
        run = slim_run({'id':1,'run_attempt':2,'run_started_at':'2026-01-02','head_sha':'abc','html_url':'https://github.com/example/repo/actions/runs/1'}, {})
        run_details(GH(), Config(repo='example/repo'), run)
        self.assertEqual([t['artifact_id'] for t in run['tests']], [3])
        self.assertEqual(run['tests'][0]['counts']['tests'], 2)

    def test_corrupt_report_does_not_break_snapshot(self):
        class GH:
            def paginate(self,path,**kw):
                return [] if path.endswith('/jobs') else [{'id':1,'name':'e2e-results'}]
            def download_artifact(self,*args,**kw): return b'broken zip'
        run = slim_run({'id':1,'html_url':'https://github.com/example/repo/actions/runs/1'}, {})
        run_details(GH(), Config(repo='example/repo'), run)
        self.assertEqual(run['reports_status'], 'partial')
        self.assertIsNone(run['tests'][0]['counts'])

    def test_private_source_cannot_be_published(self):
        class GH:
            def get(self,*a): return {'private':True}
        with self.assertRaises(ValueError): build_project(GH(), 'example/private')
