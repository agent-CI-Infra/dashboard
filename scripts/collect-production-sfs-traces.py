"""Collect DigitalMate production workspace metrics over the read-only SFS SSH host."""
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('production_kube', HERE / 'collect-production-kube-traces.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
collector.traces.OPENER = urllib.request.build_opener()

WORKSPACE_VOLUME = re.compile(r'digitalmate-workspace-sfs-((?:digitalmate-)?w-[0-9a-f]{64})')


def ssh_operation(payload, *, remote_source=None):
    key = os.environ.get('SFS_SSH_KEY_FILE')
    known_hosts = os.environ.get('SFS_KNOWN_HOSTS_FILE')
    if not key or not known_hosts:
        raise RuntimeError('SFS_SSH_KEY_FILE and SFS_KNOWN_HOSTS_FILE are required')
    # Send code through stdin too: the full archive reader exceeds Windows'
    # command-line limit. Never interpolate credentials or paths into code.
    command = 'python3 -c "import sys,json,io; e=json.load(sys.stdin); sys.stdin=io.StringIO(json.dumps(e[\'payload\'])); exec(compile(e[\'source\'],\'<collector>\',\'exec\'))"'
    args = ['ssh', '-i', key, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile="' + known_hosts + '"',
            '-o', 'ConnectTimeout=30', '-p', '2222']
    if os.environ.get('SFS_SSH_JUMP'):
        args += ['-J', os.environ['SFS_SSH_JUMP']]
    args += ['root@190.92.232.35', command]
    try:
        command_data = json.dumps({'source': remote_source or collector.REMOTE, 'payload': payload}).encode('utf-8')
        result = subprocess.run(args, input=command_data, capture_output=True, timeout=900, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('SFS SSH collection failed') from error
    if result.returncode:
        raise RuntimeError('SFS SSH collection failed; previous snapshot retained')
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError('SFS SSH returned invalid collection data') from error


def select_workspaces():
    if not collector.traces.TOKEN:
        raise RuntimeError('ADX_TOKEN is required to select production bindings')
    bindings = [item for item in collector.traces.binding_list()
                if (item.get('binding') or {}).get('scope', {}).get('tenant') == 'jiuwen']
    if not bindings:
        raise RuntimeError('no production bindings')
    names, errors, legacy = set(), Counter(), []
    for item in bindings:
        binding = item['binding']
        scope = binding['scope']
        reference = scope.get('template_ref') or {}
        if scope.get('template') != 'digitalmate' or reference.get('alias') != 'production':
            errors['unexpected_binding_scope'] += 1
            continue
        if not (binding.get('current') or {}).get('environment_id'):
            errors['missing_current_environment'] += 1
            continue
        volumes = binding.get('volumes')
        if not isinstance(volumes, dict):
            volumes = {}
        # ADX records the provisioned SFS volume as a key in the binding.
        # The value is the volume instance ID. The key encodes its workspace directory.
        matches = [m[1] for key, volume_id in volumes.items()
                   if isinstance(volume_id, str) and volume_id and (m := WORKSPACE_VOLUME.fullmatch(key))]
        if not matches:
            legacy.append(item)
        elif len(matches) > 1:
            errors['ambiguous_workspace_volume'] += 1
        elif matches[0] in names:
            errors['duplicate_workspace'] += 1
        else:
            names.add(matches[0])
    if legacy:
        fallback, failures = collector.match_workspaces_from_logs(legacy, ssh_operation)
        errors.update(failures)
        errors['duplicate_workspace'] += len(names & fallback)
        names.update(fallback)
    if not names:
        raise RuntimeError('no unambiguous production workspaces')
    return sorted(names), len(bindings), +errors


def collect():
    result = collector.collect_workspaces(ssh_operation, *select_workspaces())
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
