"""Read production APIs inside the cluster through the pinned SFS SSH host.

Credentials travel on SSH stdin and remain in memory. Only the two existing
read APIs are reachable; redirects, external hosts and write methods are refused.
"""
import importlib.util
from pathlib import Path
import urllib.error


REMOTE = r'''
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def run(payload):
    token = payload['token']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def read(base, path, params):
        req = urllib.request.Request(base + path + '?' + urllib.parse.urlencode(params),
                                     headers={'Authorization': 'Bearer ' + token})
        with opener.open(req, timeout=120) as response:
            body = response.read(32_000_001)
            if len(body) > 32_000_000:
                raise ValueError('response exceeds bound')
            return json.loads(body)

    if payload['action'] == 'gateway_logs':
        params = payload['params']
        if not isinstance(params, dict) or set(params) - {'start_date', 'end_date', 'page', 'page_size'}:
            raise ValueError('invalid gateway query')
        return read('http://litellm:4000', '/spend/logs/ui', params)
    if payload['action'] != 'bindings':
        raise ValueError('unsupported read operation')
    items, cursor, seen = [], None, set()
    for _ in range(1000):
        query = {'template': 'digitalmate', 'reference': 'production', 'kind': 'aliases', 'page_size': 50}
        if cursor:
            query['page_token'] = cursor
        page = read('http://adx-console', '/api/bindings', query)
        if not isinstance(page.get('items'), list):
            raise ValueError('invalid binding page')
        for item in page['items']:
            binding = item.get('binding') or {}
            # Retain only fields used to authorize and resolve workspaces.
            items.append({'binding': {k: binding[k] for k in
                          ('scope', 'current', 'generation', 'volumes') if k in binding}})
        cursor = page.get('next_page_token')
        if not cursor:
            return items
        if not isinstance(cursor, str) or cursor in seen:
            raise ValueError('invalid binding pagination')
        seen.add(cursor)
    raise ValueError('binding pagination limit')

try:
    result = {'ok': True, 'data': run(json.load(sys.stdin))}
except urllib.error.HTTPError as error:
    result = {'ok': False, 'status': error.code}
except Exception:
    # Never return upstream bodies, headers or exception text containing secrets.
    result = {'ok': False, 'status': 502}
print(json.dumps(result, ensure_ascii=False))
'''


def request(action, key, params=None, *, operation=None):
    if operation is None:
        spec = importlib.util.spec_from_file_location(
            'production_sfs_api_transport', Path(__file__).with_name('collect-production-sfs-traces.py'))
        transport = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(transport)
        operation = transport.ssh_operation
    result = operation({'action': action, 'token': key, 'params': params}, remote_source=REMOTE)
    if not isinstance(result, dict) or result.get('ok') is not True or 'data' not in result:
        status = result.get('status', 502) if isinstance(result, dict) else 502
        if type(status) is not int or not 300 <= status <= 599:
            status = 502
        raise urllib.error.HTTPError('sfs-ssh://production/' + action, status,
                                     'Production API read through SFS SSH failed', None, None)
    return result['data']


def binding_list(key, *, operation):
    return request('bindings', key, operation=operation)


def gateway_json(path, key, params=None):
    if path != '/spend/logs/ui':
        raise ValueError('unsupported gateway read path')
    return request('gateway_logs', key, params or {})
