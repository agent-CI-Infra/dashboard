"""Collect sanitized production traces from Kubernetes-mounted workspaces.

The console supplies only the current production binding inventory and log
fingerprints. All request and session evidence comes from the cluster mount.
"""
import collections
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('workspace_traces', HERE / 'collect-production-workspace-traces.py')
traces = importlib.util.module_from_spec(spec)
spec.loader.exec_module(traces)
# The user explicitly selected direct access to the cluster and console.
traces.OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
REMOTE = (HERE / 'kube-workspace-remote.py').read_text(encoding='utf-8')


def ready_nodes(document):
    nodes = []
    for pod in document.get('items', []):
        name = (pod.get('metadata') or {}).get('name', '')
        conditions = (pod.get('status') or {}).get('conditions', [])
        if re.fullmatch(r'adx-node-[0-9]+', name) and any(
            c.get('type') == 'Ready' and c.get('status') == 'True' for c in conditions
        ):
            nodes.append(name)
    return sorted(nodes)


def kube_operation(payload):
    config = os.environ.get('KUBECONFIG', '')
    if not config:
        raise RuntimeError('KUBECONFIG is required')
    env = dict(os.environ)
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        env.pop(key, None)
    env['NO_PROXY'] = '*'
    prefix = [os.environ.get('KUBECTL', 'kubectl'), '--kubeconfig', config, '-n', 'adx-jiuwen']
    listing = subprocess.run(prefix + ['--request-timeout=30s', 'get', 'pods', '-l', 'app=adx-node', '-o', 'json'],
                             text=True, capture_output=True, env=env, timeout=45, check=False)
    if listing.returncode:
        raise RuntimeError('cannot discover Kubernetes workspace nodes')
    for node in ready_nodes(json.loads(listing.stdout)):
        cmd = prefix + ['--request-timeout=15m', 'exec', '-i', node, '-c', 'runtime', '--', 'python3', '-c', REMOTE]
        try:
            result = subprocess.run(cmd, input=json.dumps(payload), text=True, capture_output=True,
                                    env=env, timeout=900, check=False)
            if not result.returncode:
                return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, ValueError):
            continue
    raise RuntimeError('Kubernetes workspace operation failed on all ready nodes')


def fingerprint(item):
    try:
        query = traces.query_for(item)
        params = {**query, 'path': 'workswarm/process.log', 'action': 'download'}
        url = traces.BASE + '/api/workspace-files?' + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + traces.TOKEN})
        with traces.OPENER.open(request, timeout=45) as response:
            return hashlib.sha256(response.read(4096)).hexdigest(), None
    except urllib.error.HTTPError as error:
        return None, f'http_{error.code}'
    except (OSError, ValueError, KeyError, TypeError):
        return None, 'unavailable'


def select_workspaces():
    if not traces.TOKEN:
        raise RuntimeError('ADX_TOKEN is required to select production bindings')
    inventory = kube_operation({'mode': 'inventory'})
    bindings = [item for item in traces.binding_list()
                if (item.get('binding') or {}).get('scope', {}).get('tenant') == 'jiuwen']
    if not bindings:
        raise RuntimeError('no production bindings')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fingerprints = list(pool.map(fingerprint, bindings))
    names, errors = set(), collections.Counter()
    for digest, error in fingerprints:
        if error:
            errors[error] += 1
            continue
        candidates = inventory.get(digest, [])
        if len(candidates) != 1:
            errors['ambiguous_or_changed'] += 1
            continue
        if candidates[0] in names:
            errors['duplicate_workspace'] += 1
            continue
        names.add(candidates[0])
    if not names:
        raise RuntimeError('no unambiguous production workspaces')
    return sorted(names), len(bindings), errors


def collect():
    names, binding_count, errors = select_workspaces()
    source = (HERE / 'collect-production-workspace-traces.py').read_text(encoding='utf-8')
    result = kube_operation({'mode': 'collect', 'names': names, 'source': source})
    result['binding_count'] = binding_count
    result['errors'] = dict(collections.Counter(result.get('errors', {})) + errors)
    result['collection_source'] = 'kubernetes_shared_workspace'
    if result['errors']:
        for day in result.get('request_daily_metrics', []):
            day['partial'] = True
            day['telemetry_partial'] = True
    return result


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: collect-production-kube-traces.py OUTPUT')
    Path(sys.argv[1]).write_text(json.dumps(collect(), ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
