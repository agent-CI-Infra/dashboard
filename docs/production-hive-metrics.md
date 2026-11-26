# Production Hive daily timings

The production Hive path persists its admission and Bee lifecycle timestamps in
`digitalmate/digitalmate.db`. It does not emit the legacy `MessageHandler` stream
events expected by `collect-production-workspace-traces.py`. A successful legacy
log read therefore does not prove that current Hive requests are represented.

The SFS collector now attaches `hive_daily` (`digitalmate/hive-daily@1`) to
`env-request-latency-daily.json`. The production page selects this independent
sample even if another latency producer has a newer legacy request day.
Legacy gateway and stream metrics keep their original meaning and population.

## Collection

The same ADX production binding allowlist used by the existing SFS collector is
passed to the Hive reader. Each DB and its WAL are copied to a private temporary
directory outside SFS and checked twice with the existing archive image reader.
Only that copy is opened with SQLite, using `mode=ro` and `query_only`. An image
that changes during copying is retried up to three times. This is an
**observed-stable copy, not an atomic snapshot**. Shared source files are never
opened with SQLite or modified by the Hive reader.

Each image is limited to 512 MiB (independent of the trajectory archive limit).
Queries have a 15-second progress deadline and a 100,000-row budget per cohort
query; the collection has a four-minute workspace budget. Missing, changing,
oversized, invalid or unreadable images are counted in coverage. A workspace is
merged only after all its queries succeed, so a failed query cannot publish half
of its population. Unexpected state/reason values are mapped to `other`.

The public projection contains only daily counts, state categories and timing
distributions. It exports no workspace, principal, session, message, task or Run
identifiers, SQL errors, file paths, text, tool arguments or model payloads.
Store scope IDs and sequence numbers may repeat across workspaces; they are not
used to deduplicate different workspace databases.

## Timing definitions

All cohorts use the Asia/Shanghai date of their starting event, within the last
30 days. Current-day rows and incomplete coverage are explicitly labelled.

| Metric | Start | End | Population |
| --- | --- | --- | --- |
| `normal_claim` | User input `created_at_ms` | `normal_claimed_at_ms` | User inputs created that day |
| `quick_commit` | User input `created_at_ms` | `quick_committed_at_ms` | Inputs whose quick reply remains `COMMITTED` |
| `bee_queue_wait` | Queue `created_at_ms` | Matched Run `started_at_ms` | Queue items created that day |
| `bee_run_natural` | Run `started_at_ms` | `ended_at_ms` | Runs started that day and naturally ended |
| `bee_run_ended` | Run `started_at_ms` | `ended_at_ms` | All ended Runs started that day, including stopped Runs |

Completions may cross midnight. Unended Runs have no duration sample. Negative
or future timestamps are rejected; missing measurements remain null rather than
zero. `consumed_at_ms` is deliberately not treated as reply completion.

Run duration includes internal model calls, tools and waiting. Normal claim is
before model processing. Neither is a user end-to-end response or first-token
measurement. Quick commits do not supply the ordinary answer's first token.
Ordinary reply completion, first text, first answer, ReAct and network latency
still require their own paired instrumentation. Failure attempts may include
retries and must not be divided by user inputs to invent a request failure rate.

Daily nearest-rank percentiles are exact. Mergeable log buckets use base 1.02
and a separate zero bucket. The UI shows approximate pooled percentiles only
when all distributions are valid and there are at least 20 samples; it never
averages daily percentiles. Retained rows preserve their original observation
timestamp and whole cohort instead of combining overlapping old and new data.

## Validation

The collector tests exercise DB-only Hive workspaces, older schemas, repeating
scope IDs, WAL images, changing copies, row/image budgets, midnight cohorts,
unfinished Runs, privacy and retention. The browser test covers independent
producer selection, missing reply clocks, date controls and mobile width.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_collect_production*traces.py' -v
node --test scripts/production-prefix-cache.test.mjs
```
