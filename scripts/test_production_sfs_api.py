import contextlib
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

import production_sfs_api as api


class SfsApiTests(unittest.TestCase):
    def remote(self, payload, opener):
        output = io.StringIO()
        with patch('sys.stdin', io.StringIO(json.dumps(payload))), \
                patch('urllib.request.build_opener', return_value=opener), \
                contextlib.redirect_stdout(output):
            exec(compile(api.REMOTE, '<remote-api>', 'exec'), {})
        return json.loads(output.getvalue())

    def test_binding_inventory_pages_and_excludes_unneeded_configuration(self):
        requests = []

        class Opener:
            def open(self, req, timeout):
                requests.append(req)
                page = {'items': [{'binding': {'scope': {'tenant': 'jiuwen'}, 'volumes': {},
                                               'secret_configuration': 'PRIVATE'}}]}
                if len(requests) == 1:
                    page['next_page_token'] = 'next'
                return io.BytesIO(json.dumps(page).encode())

        result = self.remote({'action': 'bindings', 'token': 'SECRET'}, Opener())
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['data']), 2)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertTrue(all(r.full_url.startswith('http://adx-console/api/bindings?') for r in requests))
        self.assertIn('page_token=next', requests[1].full_url)

    def test_gateway_reads_only_the_fixed_service_and_path(self):
        requests = []

        class Opener:
            def open(self, req, timeout):
                requests.append(req)
                return io.BytesIO(b'{"data": [], "total": 0}')

        self.assertTrue(self.remote({'action': 'gateway_logs', 'token': 'SECRET',
                                     'params': {'page': 1}}, Opener())['ok'])
        self.assertEqual(requests[0].full_url, 'http://litellm:4000/spend/logs/ui?page=1')
        self.assertEqual(requests[0].get_method(), 'GET')
        self.assertFalse(self.remote({'action': 'gateway_logs', 'token': 'SECRET',
                                      'params': {'url': 'https://other-host'}}, Opener())['ok'])
        self.assertEqual(len(requests), 1)

    def test_error_bodies_and_credentials_are_never_returned(self):
        class Opener:
            def open(self, req, timeout):
                raise urllib.error.HTTPError(req.full_url, 401, 'SECRET', {}, io.BytesIO(b'PRIVATE'))

        result = self.remote({'action': 'bindings', 'token': 'SECRET'}, Opener())
        self.assertEqual(result, {'ok': False, 'status': 401})

    def test_remote_refuses_redirects(self):
        namespace = {}
        with patch('sys.stdin', io.StringIO('{"action":"invalid","token":"SECRET"}')), \
                contextlib.redirect_stdout(io.StringIO()):
            exec(compile(api.REMOTE, '<remote-api>', 'exec'), namespace)
        self.assertIsNone(namespace['NoRedirect']().redirect_request(None, None, 302, '', {}, 'https://other-host'))

    def test_transport_failure_never_falls_back_to_https(self):
        calls = []

        def operation(payload, **kwargs):
            calls.append(payload)
            return {'ok': False, 'status': 401, 'detail': 'PRIVATE'}

        with self.assertRaises(urllib.error.HTTPError) as error:
            api.binding_list('SECRET', operation=operation)
        self.assertEqual(error.exception.code, 401)
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertNotIn('SECRET', str(error.exception))
        self.assertEqual(len(calls), 1)

    def test_gateway_rejects_unexpected_api_paths(self):
        with self.assertRaises(ValueError):
            api.gateway_json('/key/generate', 'SECRET')


if __name__ == '__main__':
    unittest.main()
