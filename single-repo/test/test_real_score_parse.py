import io
import json
import unittest
import zipfile

from gsb.reports import parse_report_zip, research_score
from gsb.project import run_details, slim_run
from gsb.config import Config


class RealScoreParseTest(unittest.TestCase):
    def test_failed_biomni_metrics_without_score_remain_visible(self):
        result = research_score(
            {'case_id': 'da-14-1', 'integration_status': 'failed',
             'evaluation': {'status': 'error'}, 'generation_duration_ms': 120000},
            'test-results/biomnibench-da-14-1/benchmark-metrics.json')
        self.assertEqual(result['case'], 'BiomniBench-da-14-1')
        self.assertEqual(result['delivery'], 'failed')
        self.assertEqual(result['duration_ms'], 120000)
        self.assertEqual(result['metrics'][0]['label'], 'Rubric')
        self.assertIsNone(result['metrics'][0]['value'])

    def test_failed_drb_metrics_without_judge_output_remain_visible(self):
        result = research_score(
            {'case_id': 62, 'integration_status': 'failed', 'evaluation': {'status': 'error'}},
            'test-results/deepresearchbench-drb-62/benchmark-metrics.json')
        self.assertEqual(result['case'], 'DRB-62')
        self.assertEqual(result['delivery'], 'failed')
        self.assertEqual([m['label'] for m in result['metrics']],
                         ['RACE', 'Citation accuracy', 'Verification coverage'])
        self.assertTrue(all(m['value'] is None for m in result['metrics']))

    def test_small_score_only_artifact_keeps_passes_and_failures(self):
        """The proposed CI artifact contains score JSON, not Playwright traces/reports."""
        documents = {
            'real-standard/e2e-real/test-results/biomnibench-da-14-1/benchmark-metrics.json':
                {'case_id': 'da-14-1', 'integration_status': 'failed',
                 'generation_duration_ms': 120000, 'evaluation': {'status': 'error'}},
            'real-standard/e2e-real/test-results/deepresearchbench-62/benchmark-metrics.json':
                {'case_id': 62, 'integration_status': 'passed',
                 'generation_duration_ms': 90000, 'evaluation': {
                     'race': {'status': 'completed', 'overall_score': 0.72},
                     'fact': {'status': 'completed', 'citation_accuracy': 80}}},
            'real-team/e2e-real/test-results/team-metrics.json':
                {'case': 'TC-E2E-01', 'integration': 'failed',
                 'generation_duration_ms': 30000, 'evaluation': {'status': 'error'}},
            'real-evolve/e2e-real/test-results/evolve-metrics.json':
                {'case': 'PUCT-COMPRESS', 'integration_status': 'passed',
                 'generation_duration_ms': 45000,
                 'evaluation': {'status': 'completed', 'score': 0.61}},
        }
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path, document in documents.items():
                archive.writestr(path, json.dumps(document))
        parsed = parse_report_zip(stream.getvalue())
        self.assertIsNone(parsed['tests'])
        self.assertEqual(len(parsed['scores']), 4)
        by_case = {score['case']: score for score in parsed['scores']}
        self.assertEqual(by_case['BiomniBench-da-14-1']['delivery'], 'failed')
        self.assertEqual(by_case['BiomniBench-da-14-1']['duration_ms'], 120000)
        self.assertIsNone(by_case['BiomniBench-da-14-1']['metrics'][0]['value'])
        self.assertEqual(by_case['DRB-62']['metrics'][0]['value'], 0.72)
        self.assertEqual(by_case['TC-E2E-01']['delivery'], 'failed')
        self.assertEqual(by_case['PUCT-COMPRESS']['metrics'][0]['value'], 0.61)

    def test_failed_run_keeps_scores_from_small_artifact(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('real-team/e2e-real/test-results/team-metrics.json', json.dumps({
                'case': 'TC-E2E-01', 'integration': 'passed', 'generation_duration_ms': 42000,
                'evaluation': {'status': 'completed', 'total_score': 91}}))
            archive.writestr('real-standard/e2e-real/test-results/biomnibench-da-14-1/benchmark-metrics.json', json.dumps({
                'case_id': 'da-14-1', 'integration_status': 'failed',
                'generation_duration_ms': 90000, 'evaluation': {'status': 'error'}}))

        class GH:
            def paginate(self, path, **kwargs):
                return [] if path.endswith('/jobs') else [dict(id=100, name='real-e2e-results', size_in_bytes=2048)]

            def download_artifact(self, *args, **kwargs):
                return stream.getvalue()

        run = slim_run({'id': 9, 'run_attempt': 1, 'head_sha': 'a' * 40,
                        'conclusion': 'failure', 'html_url': 'https://example.test/runs/9'}, {})
        run_details(GH(), Config(repo='example/repo'), run)
        report = run['tests'][0]
        self.assertEqual(report['status'], 'available')
        self.assertIsNone(report['counts'])
        self.assertEqual([(row['case'], row['delivery']) for row in report['scores']],
                         [('TC-E2E-01', 'passed'), ('BiomniBench-da-14-1', 'failed')])


if __name__ == '__main__':
    unittest.main()
