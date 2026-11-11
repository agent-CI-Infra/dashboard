"""Collect Kubernetes evidence and publish only sanitized dashboard JSON."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
REPOSITORY = 'https://github.com/agent-CI-Infra/dashboard.git'
PROXY = 'socks5h://127.0.0.1:18769'


def validate_coverage(result):
    total, read = result.get('binding_count', 0), result.get('read_log_count', 0)
    if (type(total) is not int or type(read) is not int or total <= 0
            or read < 0 or read > total or read / total < 0.95):
        raise RuntimeError('less than 95 percent of production bindings were read; snapshot retained')
    return total, read


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if not os.environ.get('ADX_TOKEN') or not os.environ.get('KUBECONFIG'):
        raise RuntimeError('ADX_TOKEN and KUBECONFIG are required')
    kube = load('kube_collector', HERE / 'collect-production-kube-traces.py')
    cloud = load('cloud_collector', HERE / 'collect-cloud-request-metrics.py')
    # Do not give the management credential to git or GitHub CLI.
    git_env = dict(os.environ)
    git_env.pop('ADX_TOKEN', None)
    git_env.update(HTTPS_PROXY=PROXY, HTTP_PROXY=PROXY, ALL_PROXY=PROXY, NO_PROXY='')
    git_prefix = ['git', '-c', f'http.proxy={PROXY}', '-c', 'credential.helper=',
                  '-c', 'credential.helper=!gh auth git-credential']

    def git(*args):
        subprocess.run(git_prefix + list(args), cwd=folder, env=git_env, check=True, timeout=180)

    with tempfile.TemporaryDirectory(prefix='digitalmate-kube-publish-') as folder:
        git('init', '-q')
        git('remote', 'add', 'origin', REPOSITORY)
        git('fetch', '--depth=1', 'origin', 'digitalmate')
        git('checkout', '-q', '-B', 'digitalmate', 'FETCH_HEAD')
        target = Path(folder) / 'site/data/env-request-latency-daily.json'
        previous = json.loads(target.read_text(encoding='utf-8'))
        if previous.get('schema') != 'digitalmate/env-request-latency-daily@1' or previous.get('env') != 'adx-jiuwen':
            raise RuntimeError('production snapshot is required')
        result = kube.collect()
        total, read = validate_coverage(result)
        merged = cloud.with_workspace_traces(previous, result)
        target.write_text(json.dumps(merged, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        git('config', 'user.name', 'DigitalMate metrics collector')
        git('config', 'user.email', 'digitalmate-metrics@users.noreply.github.com')
        git('add', '--', 'site/data/env-request-latency-daily.json')
        git('commit', '-m', 'chore(digitalmate): collect Kubernetes request metrics')
        git('fetch', 'origin', 'digitalmate')
        git('rebase', 'FETCH_HEAD')
        git('push', 'origin', 'HEAD:digitalmate')
        subprocess.run(['gh', 'workflow', 'run', 'deploy.yml', '--repo', 'agent-CI-Infra/dashboard', '--ref', 'main'],
                       cwd=folder, env=git_env, check=True, timeout=120)
        print(f'Published Kubernetes metrics: {read}/{total} workspaces; generation {result.get("generated_at")}')


if __name__ == '__main__':
    main()
