"""Build the dashboard's client-observed snapshot from the test deployments.

Each deployment returns ``client-observed-aggregate@1`` from
``GET /digitalmate/telemetry/summary``. This collector validates and copies only
the aggregate fields, labels every record with the deployment's own environment
(never a device-claimed one), merges them into ``client-observed-metrics@1`` and
writes ``client-observed-metrics.json``.

This file is deliberately separate from the production collector: device
reports are advisory and are never merged into ``classified-production``.

Usage: set CLIENT_OBSERVED_DEPLOYMENTS and run
python collect-client-observed-metrics.py <data_dir> [window_days] [previous_snapshot]

CLIENT_OBSERVED_DEPLOYMENTS is a JSON array:

  [{"environment": "adx-jiuwen-test-0",
    "url": "https://onlineswarm.openjiuwen.com:20010",
    "token": "<deployment export token>"}]

It belongs in a GitHub secret, never in the repository.
"""
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 'client-observed-metrics@1'
SOURCE_SCHEMA = 'client-observed-aggregate@1'
SCOPE = 'client-observed'
DEPLOYMENTS_ENV = 'CLIENT_OBSERVED_DEPLOYMENTS'
DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 365
MAX_MODELS = 50
MAX_LABELS = 32
MAX_BODY_BYTES = 4 * 1024 * 1024
SUMMARY_PATH = '/digitalmate/telemetry/summary'

SEGMENT_NAMES = (
    'tap_to_queued',
    'queued_to_dequeued',
    'dequeued_to_wire_send',
    'wire_send_to_ack',
    'ack_to_first_text',
    'first_text_to_last_text',
    'last_text_to_task_done',
    'task_done_to_display_done',
)

MODEL_PATTERN = re.compile(r'[A-Za-z0-9._:/@+-]{1,96}')
LABEL_PATTERN = re.compile(r'[A-Za-z0-9._ -]{1,32}')
ENVIRONMENT_PATTERN = re.compile(r'[A-Za-z0-9._-]{1,64}')
LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})


class CollectorError(ValueError):
    """A deployment configuration or summary that cannot be used at all.

    ``reason`` is the short code the dashboard renders; the message stays in the
    collector log, where the full diagnostic belongs.
    """

    def __init__(self, message, reason='invalid'):
        super().__init__(message)
        self.reason = reason


def integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return 0
    return int(min(number, 9_007_199_254_740_991))


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    result = float(value)
    if not math.isfinite(result) or result < 0:
        return 0.0
    return round(result, 1)


def optional_number(value):
    return None if value is None else number(value)


def metric_block(value):
    source = value if isinstance(value, dict) else {}
    return {
        'count': integer(source.get('count')),
        'avg_ms': number(source.get('avg_ms')),
        'p50_ms': optional_number(source.get('p50_ms')),
        'p95_ms': optional_number(source.get('p95_ms')),
    }


def label_counts(value):
    """Keep the most frequent labels; an unbounded map is never rendered."""
    if not isinstance(value, dict):
        return {}
    counted = {}
    for label, count in value.items():
        if isinstance(label, str) and LABEL_PATTERN.fullmatch(label):
            counted[label] = integer(count)
    ordered = sorted(counted.items(), key=lambda item: (-item[1], item[0]))
    kept = dict(ordered[:MAX_LABELS])
    remainder = sum(count for _, count in ordered[MAX_LABELS:])
    if remainder:
        kept['other'] = kept.get('other', 0) + remainder
    return dict(sorted(kept.items(), key=lambda item: (-item[1], item[0])))


def model_entry(value):
    if not isinstance(value, dict):
        return None
    model = value.get('model')
    if not isinstance(model, str) or not MODEL_PATTERN.fullmatch(model):
        return None
    return {
        'model': model,
        'calls': integer(value.get('calls')),
        'succeeded': integer(value.get('succeeded')),
        'streamed': integer(value.get('streamed')),
        'ttft_ms': metric_block(value.get('ttft_ms')),
        'tpot_ms': metric_block(value.get('tpot_ms')),
        'input_tokens': integer(value.get('input_tokens')),
        'output_tokens': integer(value.get('output_tokens')),
        'total_tokens': integer(value.get('total_tokens')),
        'statuses': label_counts(value.get('statuses')),
    }


def sanitize_metrics(payload):
    """Copy the aggregate fields of a summary or a published record, dropping everything else."""
    turns = payload.get('turns') if isinstance(payload.get('turns'), dict) else {}
    segments = payload.get('segments') if isinstance(payload.get('segments'), dict) else {}
    models = []
    for entry in payload.get('models') if isinstance(payload.get('models'), list) else []:
        cleaned = model_entry(entry)
        if cleaned is not None:
            models.append(cleaned)
        if len(models) >= MAX_MODELS:
            break
    models.sort(key=lambda item: (-item['calls'], item['model']))
    return {
        'window_days': integer(payload.get('window_days')),
        'turns': {'total': integer(turns.get('total')), 'completed': integer(turns.get('completed'))},
        'outcomes': label_counts(payload.get('outcomes')),
        'networks': label_counts(payload.get('networks')),
        'client_ttft': metric_block(payload.get('client_ttft')),
        'end_to_end': metric_block(payload.get('end_to_end')),
        'segments': {name: metric_block(segments.get(name)) for name in SEGMENT_NAMES},
        'models': models,
        'models_omitted': integer(payload.get('models_omitted')),
    }


def sanitize_summary(payload):
    if not isinstance(payload, dict) or payload.get('schema') != SOURCE_SCHEMA or payload.get('scope') != SCOPE:
        raise CollectorError('summary has an unexpected contract')
    return sanitize_metrics(payload)


def sanitize_record(value):
    """A record reused from a previous snapshot still has to look like a record."""
    if not isinstance(value, dict):
        return None
    environment = value.get('environment')
    if not isinstance(environment, str) or not ENVIRONMENT_PATTERN.fullmatch(environment):
        return None
    return {
        'environment': environment,
        'stale': True,
        'sampled_at_ms': integer(value.get('sampled_at_ms')),
        **sanitize_metrics(value),
    }


def load_deployments(text):
    """Parse and validate the deployment list; refuses anything it cannot serve safely."""
    try:
        document = json.loads(text)
    except ValueError as error:
        raise CollectorError(f'{DEPLOYMENTS_ENV} is not JSON') from error
    if not isinstance(document, list) or not document:
        raise CollectorError(f'{DEPLOYMENTS_ENV} must be a non-empty array')
    deployments, seen = [], set()
    for entry in document:
        if not isinstance(entry, dict):
            raise CollectorError('every deployment must be an object')
        environment = entry.get('environment')
        url = entry.get('url')
        token = entry.get('token')
        if not isinstance(environment, str) or not ENVIRONMENT_PATTERN.fullmatch(environment):
            raise CollectorError('deployment environment labels must match [A-Za-z0-9._-]{1,64}')
        parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
        if (parsed is None or parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')
                or (parsed.scheme == 'http' and parsed.hostname not in LOOPBACK_HOSTS)):
            raise CollectorError(f'deployment {environment} needs a plain https base URL')
        if not isinstance(token, str) or not token or len(token) > 512 or any(ch in token for ch in '\r\n'):
            raise CollectorError(f'deployment {environment} needs a token')
        if environment in seen:
            continue
        seen.add(environment)
        deployments.append({'environment': environment, 'url': url.rstrip('/'), 'token': token})
    return deployments


def fetch_summary(url, token, days):
    """Read one deployment's aggregate, retrying transient failures only."""
    endpoint = f'{url}{SUMMARY_PATH}?days={days}'
    last_error = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(endpoint, headers={'Authorization': f'Bearer {token}'})
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise CollectorError('summary is too large')
            return json.loads(body.decode('utf-8'))
        except CollectorError:
            raise
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code in (401, 403):
                raise CollectorError(f'the deployment declined the summary ({error.code})',
                                     reason='unauthorized') from error
            if error.code == 404:
                raise CollectorError('the deployment does not expose the summary route',
                                     reason='not-configured') from error
            if error.code not in (408, 429) and error.code < 500:
                raise CollectorError(f'the deployment refused the summary ({error.code})') from error
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            last_error = error
        except ValueError as error:
            raise CollectorError('summary is not JSON') from error
        time.sleep(1.5 * (attempt + 1))
    raise CollectorError(f'the deployment is unreachable ({last_error.__class__.__name__})',
                         reason='unreachable')


def weighted(blocks):
    """Weighted mean of metric blocks; percentiles cannot be merged exactly, so they stay null."""
    count = sum(block['count'] for block in blocks)
    if not count:
        return {'count': 0, 'avg_ms': 0.0, 'p50_ms': None, 'p95_ms': None}
    total = sum(block['avg_ms'] * block['count'] for block in blocks)
    return {'count': count, 'avg_ms': round(total / count, 1), 'p50_ms': None, 'p95_ms': None}


def merge_models(records):
    merged = {}
    for record in records:
        for model in record['models']:
            entry = merged.setdefault(model['model'], {
                'calls': 0, 'succeeded': 0, 'streamed': 0,
                'ttft_count': 0, 'ttft_total': 0.0, 'tpot_count': 0, 'tpot_total': 0.0,
                'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'statuses': {},
            })
            entry['calls'] += model['calls']
            entry['succeeded'] += model['succeeded']
            entry['streamed'] += model['streamed']
            entry['ttft_count'] += model['ttft_ms']['count']
            entry['ttft_total'] += model['ttft_ms']['avg_ms'] * model['ttft_ms']['count']
            entry['tpot_count'] += model['tpot_ms']['count']
            entry['tpot_total'] += model['tpot_ms']['avg_ms'] * model['tpot_ms']['count']
            for field in ('input_tokens', 'output_tokens', 'total_tokens'):
                entry[field] += model[field]
            for status, count in model['statuses'].items():
                entry['statuses'][status] = entry['statuses'].get(status, 0) + count
    ordered = sorted(merged.items(), key=lambda item: (-item[1]['calls'], item[0]))
    models = []
    for name, entry in ordered[:MAX_MODELS]:
        models.append({
            'model': name,
            'calls': entry['calls'],
            'succeeded': entry['succeeded'],
            'streamed': entry['streamed'],
            'ttft_ms': {'count': entry['ttft_count'],
                        'avg_ms': round(entry['ttft_total'] / entry['ttft_count'], 1)
                        if entry['ttft_count'] else 0.0,
                        'p50_ms': None, 'p95_ms': None},
            'tpot_ms': {'count': entry['tpot_count'],
                        'avg_ms': round(entry['tpot_total'] / entry['tpot_count'], 1)
                        if entry['tpot_count'] else 0.0,
                        'p50_ms': None, 'p95_ms': None},
            'input_tokens': entry['input_tokens'],
            'output_tokens': entry['output_tokens'],
            'total_tokens': entry['total_tokens'],
            'statuses': label_counts(entry['statuses']),
        })
    omitted = sum(record['models_omitted'] for record in records) + max(0, len(ordered) - MAX_MODELS)
    return models, omitted


def merge_records(records):
    """The cross-environment totals the board's summary row shows."""
    outcomes, networks = {}, {}
    for record in records:
        for label, count in record['outcomes'].items():
            outcomes[label] = outcomes.get(label, 0) + count
        for label, count in record['networks'].items():
            networks[label] = networks.get(label, 0) + count
    models, omitted = merge_models(records)
    return {
        'environment_count': len(records),
        'turns': {'total': sum(record['turns']['total'] for record in records),
                  'completed': sum(record['turns']['completed'] for record in records)},
        'outcomes': dict(sorted(outcomes.items(), key=lambda item: (-item[1], item[0]))),
        'networks': dict(sorted(networks.items())),
        'client_ttft': weighted([record['client_ttft'] for record in records]),
        'end_to_end': weighted([record['end_to_end'] for record in records]),
        'segments': {name: weighted([record['segments'][name] for record in records])
                     for name in SEGMENT_NAMES},
        'models': models,
        'models_omitted': omitted,
    }


def load_previous(path):
    if not path:
        return {}
    try:
        document = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(document, dict) or document.get('schema') != SCHEMA or document.get('scope') != SCOPE:
        return {}
    records = {}
    for value in document.get('environments') if isinstance(document.get('environments'), list) else []:
        record = sanitize_record(value)
        if record is not None:
            records[record['environment']] = record
    return records


def stale_record(previous, environment):
    return previous.get(environment)


def collect(deployments, previous, days, now_ms):
    records, unavailable = [], []
    for deployment in deployments:
        environment = deployment['environment']
        try:
            summary = sanitize_summary(fetch_summary(deployment['url'], deployment['token'], days))
        except (CollectorError, ValueError) as error:
            kept = stale_record(previous, environment)
            reason = error.reason if isinstance(error, CollectorError) else 'invalid'
            unavailable.append({'environment': environment, 'reason': reason})
            if kept is not None:
                records.append(kept)
            print(f'  {environment}: unavailable ({error})', flush=True)
            continue
        records.append({
            'environment': environment,
            'stale': False,
            'sampled_at_ms': now_ms,
            **summary,
        })
        print(f"  {environment}: {summary['turns']['total']} turns, "
              f"{len(summary['models'])} models", flush=True)
    return records, unavailable


def main():
    data_dir = Path(sys.argv[1])
    days = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_WINDOW_DAYS
    if not 1 <= days <= MAX_WINDOW_DAYS:
        raise ValueError(f'Window days must be between 1 and {MAX_WINDOW_DAYS}')
    configured = os.environ.get(DEPLOYMENTS_ENV, '').strip()
    if not configured:
        print(f'{DEPLOYMENTS_ENV} is not configured; no client-observed deployment is collected.')
        return
    deployments = load_deployments(configured)
    previous = load_previous(sys.argv[3] if len(sys.argv) > 3 else '')
    now_ms = int(time.time() * 1000)
    records, unavailable = collect(deployments, previous, days, now_ms)
    if not records:
        # Never replace a published board with nothing: a configuration or network
        # failure should fail the run instead.
        raise SystemExit('No client-observed deployment could be read; the snapshot was not updated.')
    doc = {
        'schema': SCHEMA,
        'scope': SCOPE,
        'trust': 'device-reported',
        'production_merged': False,
        'label': '端侧观测（client-observed）',
        'notice': '端侧自述数据，仅供诊断；不并入现网指标（classified-production），不包含消息内容。',
        'generated_at_ms': now_ms,
        'generated_at': datetime.fromtimestamp(now_ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'window_days': days,
        'environments': records,
        'unavailable': unavailable,
        'totals': merge_records(records),
    }
    out = data_dir / 'client-observed-metrics.json'
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=data_dir, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(doc, handle, ensure_ascii=False, indent=1, allow_nan=False)
        os.replace(temp, out)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    print(f'collected {len(records)} environment(s), {len(unavailable)} unavailable; '
          f'{doc["totals"]["turns"]["total"]} turns in the last {days} day(s)')


if __name__ == '__main__':
    main()
