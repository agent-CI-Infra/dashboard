"""Collect DigitalMate production workspace metrics over the read-only SFS SSH host."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('production_kube', HERE / 'collect-production-kube-traces.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def ssh_operation(payload):
    key = os.environ.get('SFS_SSH_KEY_FILE')
    known_hosts = os.environ.get('SFS_KNOWN_HOSTS_FILE')
    if not key or not known_hosts:
        raise RuntimeError('SFS_SSH_KEY_FILE and SFS_KNOWN_HOSTS_FILE are required')
    source = base64.b64encode(collector.REMOTE.encode('utf-8')).decode('ascii')
    command = "python3 -c \"import base64;exec(base64.b64decode('" + source + "'))\""
    args = ['ssh', '-i', key, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile="' + known_hosts + '"',
            '-o', 'ConnectTimeout=30', '-p', '2222']
    if os.environ.get('SFS_SSH_JUMP'):
        args += ['-J', os.environ['SFS_SSH_JUMP']]
    args += ['root@190.92.232.35', command]
    try:
        result = subprocess.run(args, input=json.dumps(payload), text=True, capture_output=True,
                                timeout=900, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('SFS SSH collection failed') from error
    if result.returncode:
        raise RuntimeError('SFS SSH collection failed; previous snapshot retained')
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError('SFS SSH returned invalid collection data') from error


def collect():
    collector.kube_operation = ssh_operation
    # On hosted CI the console uses its normal network route; callers may
    # inject an opener (e.g. a verified SOCKS tunnel) for local use.
    collector.traces.OPENER = urllib.request.build_opener()
    result = collector.collect()
    count, read = result.get('binding_count'), result.get('read_log_count')
    if type(count) is not int or type(read) is not int or count <= 0 or read / count < 0.95:
        raise RuntimeError('SFS coverage below 95%; previous snapshot retained')
    result['collection_source'] = 'production_sfs_ssh'
    return result


def main():
    if len(sys.argv) != 3:
        raise SystemExit('usage: collect-production-sfs-traces.py OUTPUT PREVIOUS')
    cloud_spec = importlib.util.spec_from_file_location('cloud_metrics', HERE / 'collect-cloud-request-metrics.py')
    cloud = importlib.util.module_from_spec(cloud_spec)
    cloud_spec.loader.exec_module(cloud)
    previous = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    if previous.get('schema') != 'digitalmate/env-request-latency-daily@1' or previous.get('env') != 'adx-jiuwen':
        raise SystemExit('invalid production seed; previous snapshot retained')
    result = cloud.with_workspace_traces(previous, collect())
    Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('SFS collection complete; sanitized aggregate prepared', file=sys.stderr)


if __name__ == '__main__':
    main()
