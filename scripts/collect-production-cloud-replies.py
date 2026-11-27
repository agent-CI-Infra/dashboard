"""Refresh only the current cloud reply projection and its HMAC dedup cursor."""
import importlib.util
import json
import os
from pathlib import Path
import sys

from production_cloud_replies import collect_cloud_replies
from production_sfs_api import binding_list


def main():
    if len(sys.argv) != 4:
        raise SystemExit('usage: collect-production-cloud-replies.py METRICS PREVIOUS_STATE NEXT_STATE')
    metrics_path, previous_path, next_path = map(Path, sys.argv[1:])
    metrics = json.loads(metrics_path.read_text(encoding='utf-8'))
    if metrics.get('schema') != 'digitalmate/env-request-latency-daily@1' or metrics.get('env') != 'adx-jiuwen':
        raise SystemExit('invalid production metrics seed')
    previous = json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.is_file() else None
    spec = importlib.util.spec_from_file_location('sfs_replies', Path(__file__).with_name('collect-production-sfs-traces.py'))
    sfs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sfs)
    result = collect_cloud_replies(os.environ['ADX_TOKEN'], previous, operation=sfs.ssh_operation, binding_reader=binding_list)
    metrics['cloud_reply_daily'] = result['daily']
    next_path.write_text(json.dumps(result['state'], separators=(',', ':')) + '\n', encoding='utf-8')
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('Current cloud reply aggregate prepared; raw profiles and identities stayed inside SSH', file=sys.stderr)


if __name__ == '__main__':
    main()
