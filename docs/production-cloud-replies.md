# Current production cloud reply metrics

The Hive Home caller bypasses the legacy WorkSwarm MessageHandler stream. Its
existing `digitalmate_hive_message_received` and `digitalmate_hive_reply_visible`
profile records are captured in ADX nodes' `/work/runtime-logs`, including gzip
archives and resumed runtime revisions. Reading only SFS `workswarm/process.log`
therefore misses the current reply data.

`collect-production-cloud-replies.py` adds `cloud_reply_daily` to the existing
production daily document. It reads the current `jiuwen / digitalmate /
production` binding inventory, then scans only matching environment log files
on every discovered ADX runtime node. Node errors or incomplete transport abort
publication. Coverage reports the nodes scanned and environments with log files;
the samples are observed messages, not a claim of coverage of every user input.

The three clocks share the original message-received event and remain separate:

| Field | End event | Meaning |
| --- | --- | --- |
| `normal_response` | normal, via=response | Beebou response ready, before returning the response |
| `quick_send` | quick | Quick reply send callback returned |
| `normal_push` | normal, via=push | Beebou reply publish callback returned |

They use the difference between existing server wall-clock event timestamps.
None is a phone display acknowledgement, first streamed token, Bee final
result, or the retired Gateway stream-duration definition. The legacy clocks
are kept separately in a collapsed historical section; they are never extended
using Hive claim waits, Run durations, or these reply clocks. Dates use the
received event in Asia/Shanghai. Missing endpoints, empty/failed replies,
negative or more-than-one-day durations, and ambiguous duplicate receives do
not produce samples. Exact duplicate log records and resends cannot inflate
the count or create a faster start.

The `.sync/cloud-reply-state.json` cursor retains HMAC identities and timing
fields for 30 days. It permits pairing across log rotation and makes repeated
hourly reads idempotent. It contains no raw message or workspace identities,
text, model messages, headers or credentials. The ADX token keys the HMAC;
rotation of that token requires an explicit cursor migration and otherwise
fails closed. The public `cloud_reply_daily` projection contains only daily
counts, aggregate timing buckets, observation time and coverage.

The existing hourly cloud collection workflow runs this reader after the SFS
archive collector and persists the cursor together with the public aggregate.
The reader uses the existing pinned SFS SSH channel and verified cluster mTLS.
`PRODUCTION_KUBE_READ_CREDENTIALS` is a GitHub Actions secret containing only
`ca`, `cert`, and `key` PEM strings from the already configured production
read credential. Private material travels on SSH stdin, is loaded via Linux
memory-backed file descriptors, and is never written into the workspace or
printed. The cluster endpoint, namespace, runtime node selector, exec command
and log directory are fixed in the collector. No production application code,
template flags or user sessions are changed.

Validation includes actual node reads plus parser, scope, gzip, budget,
cross-day, retry, rotation, duplicate, privacy, failure and browser regressions.
The browser regression checks current cards and curves, independent source
selection, measured-only display, range controls and narrow layouts.
