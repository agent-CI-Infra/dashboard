import io
import unittest
from unittest.mock import patch

from gsb.github import GitHub, GitHubError


class ArtifactDownloadTests(unittest.TestCase):
    def download(self, data, max_bytes=32, ticks=(0, 1, 2, 3)):
        gh = GitHub('test-credential', timeout=10)
        with patch.object(gh, '_request', return_value=(None, {'Location': 'https://example.com/artifact'}, 302)), \
             patch('gsb.github.urllib.request.urlopen') as opened, \
             patch('gsb.github.time.monotonic', side_effect=ticks):
            response = opened.return_value.__enter__.return_value
            response.headers = {}
            response.read1.side_effect = io.BytesIO(data).read1
            result = gh.download_artifact('example/source', 1, max_bytes=max_bytes)
            request = opened.call_args.args[0]
            self.assertFalse(request.has_header('Authorization'))
            return result

    def test_small_report_is_complete_and_redirect_is_anonymous(self):
        self.assertEqual(self.download(b'report'), b'report')

    def test_continuous_stream_has_a_total_time_budget(self):
        with self.assertRaisesRegex(GitHubError, 'time budget'):
            self.download(b'x' * 200000, max_bytes=300000, ticks=(0, 1, 11))

    def test_stream_cannot_exceed_size_budget_without_content_length(self):
        with self.assertRaisesRegex(GitHubError, 'exceeds'):
            self.download(b'x' * 40)
