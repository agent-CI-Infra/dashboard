# DigitalMate production metrics view

## Model prefix-cache coverage

The hourly SFS collection reads Beebou's workspace
`beebou-prefix-cache.jsonl` and the owned Bee runtime's
`bee-workswarm/.jiuwenswarm/agent/.logs/context_usage.jsonl`. The legacy
`workswarm/` cache source is excluded from collection, retained history and
all dashboard cards and tables. It reads the active file and up to three
rotations of each source. The reader deduplicates calls by ID within each
workspace and source. Conflicting observations for one call are excluded and
counted. Only daily aggregates, coverage counts and model labels are published
under `prefix_cache` in `env-request-latency-daily.json`. The raw records and
workspace, call and session identifiers stay on the mount.

- A known call has positive input tokens and an authoritative, integral cache
  read count in the inclusive range 0 to input tokens. The weighted hit rate is
  the sum of cache reads divided by the sum of input tokens of known calls.
  Calls missing either count appear in the observed population with unavailable
  rates. Explicit zero cache reads remain valid.
- The single-call distribution uses nearest-rank percentiles over 0.1
  percentage-point buckets. The near-zero count covers known calls of at least
  1,024 input tokens with a hit rate at or below 1%. Its denominator is known
  calls meeting that input-size floor.
- A session contributes its daily token-weighted rate to the session
  distribution. The first observed call marks the start of that session's
  visible evidence in the retained logs. The session may predate the window.
- The watch compares returning-session near-zero counts and proportions
  separately for BeeBou and Bee. A day flags a spike when both
  exceed every qualifying day in the preceding seven Shanghai calendar days.
  The compared day and each baseline day require at least 20 eligible calls,
  and the baseline needs at least three days. Incomplete reads are ineligible
  for the comparison.

The snapshot keeps up to 30 Shanghai calendar days. When rotation leaves a
smaller daily population, the larger previous sample remains labelled with its
original observation time. The board reports workspaces with visible call
records and workspaces whose reads exceeded a limit or failed. Days without
matching logs show no sample. Collection uses at most 512 MiB and targets a
60-second read budget per run. Existing latency metrics keep their own sample
population.

The regression suite replays both wire formats with cold, continuing,
near-zero, short and missing observations, then checks the dashboard in
Chromium. Current provider hit rates and the effect of actual context
compression require production observations.

## Release publication markers

Version decoration preserves the metric paths, including their existing data
gaps. Background bands mark release publication stages without disconnecting
adjacent observations. A day's band is divided in proportion to the publication
times in Asia/Shanghai; multiple releases within that day get separate boundaries
and their exact local times appear in the daily tooltip. Midnight belongs to the
new calendar day. These are publication markers: daily aggregates cannot be
split into per-version metrics, and publication does not identify the version
that actually served each request.

## Full native trajectory archive

The cloud workflow has two scheduled triggers per hour. The existing NetMon
publishers provide a second trigger through
`.github/workflows/refresh-digitalmate-sfs.yml` on the `digitalmate` branch.
That workflow checks freshness on each NetMon push and dispatches collection
or Pages deployment when an hour is missing. Scheduled runs and dispatches
with `refresh_if_due=true` share an hourly archive check. A manual run can
force a refresh. The NetMon trigger uses the repository workflow token.

The reader enumerates native stores in the current DigitalMate production
bindings. The console's provisioned SFS volume ID supplies the workspace
directory. Older bindings use the log fingerprint lookup. Unresolved bindings
appear in the archive's coverage report.

The archive reader copies the main database and its WAL to a private temporary
directory outside SFS. It checks two complete hashes of each source file and
verifies the file and sidecar metadata, then runs SQLite `quick_check` on the copy.
SQLite rebuilds the WAL index locally. A rollback journal or changes during
copying defer the database for a later run. The archive reports
`consistency: unconfirmed` and `complete: false` because the source can change
between observations.

Each database batch contains up to 16,000 spans. The hash of the database image
and the rowid cursor bind consecutive batches to one generation. A change
restarts that database's scan. Each run scans up to 100,000 rows and can copy
up to 256 MiB per database and 1 GiB across databases within 480 seconds. The
round-robin cursor resumes deferred work in the next run. Published coverage
includes size limits, failures, retries, and invalid records.

Only sanitized labels, validated request identifiers, opaque trace keys, span
relationships, lifecycle and timing leave the host. No raw databases, prompts,
answers, tool arguments, workspace paths or session IDs are published. Sanitized
staged pages live under `site/data/trajectory-archive/staging`; `.sync` contains
only generation/position/status checkpoints and is excluded from Pages. Archive
and checkpoint publication is one data-branch commit. A failed run retains both.
Previously archived observations survive source retention; retained spans carry
their original observation time. No archive expiry is imposed by this collector.

The independent “全部原生轨迹” section searches all archived requests and pages
the list at 50 traces and details at 500 spans. Date filtering uses Beijing time;
it is independent of the slow-request range selector. The index refreshes every
five minutes; this is an hourly collection target, not a real-time feed. GitHub
scheduled execution can be delayed. The existing slow-request view remains a
sample and does not become a full archive merely because the archive exists.

The same collection run links finalized archive trees into the slow-request
snapshot using a unique request ID and a native time window contained within
the gateway request. Every detail page and parent chain must be present; ID
conflicts, unfinished trees and invalid pages keep the event fallback. Existing
directly collected native profiles are preserved. Archive links disclose their
observation time and unconfirmed consistency, including after console refreshes.
Native timings remain relative to the native root; gateway timings and tool
events retain their separate clock. Metrics and archive publish together.

Validation: `python -m unittest discover -s scripts -p 'test_sfs_trajectory_archive.py'`
and `node --test scripts/trajectory-archive.test.mjs`.

Assets are adapted from DigitalMate `dashboard/e2e-performance` (commit `c07077d`). The view reads `data/production-model-history.json`, separately from nightly E2E data. `.github/workflows/collect-production-model-metrics.yml` publishes a 35-calendar-day aggregate every three hours to the imported `digitalmate` branch and triggers Pages deployment. Configure the `PRODUCTION_GATEWAY_KEY` Actions secret in the dashboard repository. The runner must be able to reach the gateway. Collection uses up to three concurrent daily windows and logs each completed window. A failed collection leaves the previous aggregate intact.

Only daily model aggregates are published, plus the failure analysis below. The published request details are limited to opaque identifiers (`request_id`, `metadata.litellm_call_id`) and timing/status fields for timeout calls and the slowest 5% of calls of each day. Prompts, responses, message content, user identifiers, API keys, IP addresses, and raw log lines are never written to the board. The gateway rows are calls, not user questions. All traffic on the configured gateway is included; production versus test traffic is not independently classified. Unknown-status records make the selected model/day failure rate unavailable. Missing values stay unavailable, not zero.

Metric definitions (`metrics_version: 7`):

- **Complete-day anchor.** When 当天 is selected, summary, comparison, details, and cloud cards use only the current Asia/Shanghai calendar day, including its partial snapshot; missing today stays unavailable. Other ranges use complete-day summaries anchored at `complete_through` and show the partial day separately in model charts and tables. A snapshot older than six hours is marked stale. Same-clock comparisons use the actual `window_end` collection cutoff, not the later publication time.
- **Volume-weighted means.** The collector stores `duration_sum_ms` and `duration_sample_count` per model/day; `query_ms` remains only as a legacy display value. The dashboard computes means as `sum / count` across the selected scope and never averages per-day means with equal weight. TTFT is weighted by valid requests; TPOT is weighted by generated tokens.
- **Request-level percentiles.** `duration_p50_ms` and `duration_p95_ms` are nearest-rank percentiles over the raw successful durations of the day and require 20 samples. When sources have mergeable duration histograms, combined percentiles are estimated from all samples and labelled approximate. Legacy sources without histograms retain a visible min–max interval rather than an invented pooled value. No daily-mean median is labelled as a request percentile.
- **Strict streaming.** TTFT and TPOT use only successful calls with `stream === true`. Conflicting or missing stream flags are unknown; explicit non-streaming calls are excluded. Successful calls with unknown streaming status are reported separately as TTFT and TPOT estimates with coverage ratios, never mixed into the main means. TTFT ≥ 95% of total duration invalidates both TTFT and TPOT; TPOT < 0.1 ms/token is also filtered out.
- **Successful-call tokens.** Displayed tokens count successful calls only and carry `token_coverage = token_sample_count / success_count`. Failed-attempt totals remain in the aggregate but are not displayed or included in successful-call means.
- **Low-volume models.** Models with fewer than 50 calls in the trailing 7 days are folded into a separate section of the comparison card and excluded from the headline status counts, so one call cannot present a 100% failure rate as fleet state.

TTFT is completionStartTime minus startTime. TPOT is token-weighted: sum of (endTime minus completionStartTime) divided by sum of (completion_tokens minus one) for eligible calls, separately for confirmed and unknown streaming. The collector preserves elapsed sums, token weights, and request sample counts for accurate daily and weekly rollups; coverage remains a request-based ratio. A previous snapshot with an older `metrics_version` is rebuilt in full once so every retained day uses the same definitions. The page rejects older snapshots until that rebuild succeeds. Other boards do not show this tab.

Estimated TTFT and TPOT P50/P95 use mergeable 2%-wide logarithmic histograms of per-request values, separately for confirmed and unknown stream status. Bucket midpoints have at most about 1% relative error; each percentile requires at least 20 valid requests.

Duration distributions use the same buckets. After upgrading, the model collector fetches the latest seven calendar days once to backfill them; older days without histograms remain intervals until their source data is recollected.

## Model-side failure analysis

Each daily point also records why its calls failed and which calls were slow:

- `failure_reasons` counts failed calls by category, read from `metadata.error_information` and the row's error fields: `timeout`, `budget` (`429_BUDGET_EXCEEDED`), `rate_limit` (`429_RATE_LIMIT_EXCEEDED`, `ModelArts.81114`), `content_blocked` (`ModelArts.81011` and content/safety wording), `invalid_request` (`400_INVALID_REQUEST`, `ModelArts.81001`), `permission_denied`, and `other`. The categories are checked in that order, so a timeout is not also counted as a rate limit.
- `timeout_count` counts the timeout category. `latency_max_ms` / `latency_max_request` name the slowest call of the day regardless of status. `slow_count` and `slow_threshold_ms` describe the slowest 5% of the day's calls (`ceil(n × 0.05)`, at least one when the day has calls); every status participates, because a timeout is a latency problem.
- `slow_requests` and `timeout_requests` publish up to 50 rows each: `request_id`, `litellm_call_id` (`call_id`), `start_time`, `duration_ms`, `status`, and a failure `reason` when the call failed. The gateway exposes no separate `trace_id`; `request_id` and `litellm_call_id` are the identifiers the console accepts.
- Request-row tables are retained for the last 7 Asia/Shanghai days only. Older points keep their counts, causes, and the maximum latency with its identifier, but drop the slow/timeout row lists, so the snapshot stays small; the board states this when a selected range extends past the retention window. Each analysis card lists up to 20 rows (slowest first) for the selected range.

Bumping `metrics_version` rebuilds the retained window once so every day carries the same fields; until that rebuild completes the reason table reports which fraction of selected days has detail.

## Cloud-side one-way network metrics

`uplink_send`, `downlink_first_text`, and `downlink_terminal` carry client-observed one-way latency (`n`, `sum`, `mean`, `p50`, `p95`, `histogram`, milliseconds) in each daily row. The three headline cards summarize the selected range, weighted by valid sample counts; multi-day percentiles merge bounded histograms and are marked approximate. Missing measurements show a dash and zero sample coverage.

The production SFS/Kubernetes collector and console reader parse `workswarm/client-network-YYYY-MM-DD.jsonl` with schema `digitalmate-client-network@1`. The Bridge instrumentation must be deployed and `DIGITALMATE_NETWORK_METRICS_ENABLED=true` for measurements to appear. Uplink measures phone request send to Bridge receipt. Downlink measures Bridge first-text or terminal frame send to phone receipt; a Home reply can belong to both buckets. These are transport samples, including RPCs, rather than counts of completed Agent turns.

The producer applies the measured phone-minus-Bridge clock offset and verifies the exact downlink frame before writing a sample. Collection accepts only samples with clock error at most 50 ms and one-way duration from 0 to 120 seconds; downlink arithmetic must match the recorded offset and timestamps. Cumulative coverage records are ignored. Uplink samples are deduplicated by request ID and downlink samples by bucket, task/execution and frame timestamp, within each workspace. Record timestamps determine the Shanghai day, independently of the writer's file timezone. Each file is bounded to 8 MB and each workspace to 32 MB over the retained 30-day window. Unreadable or oversized optional files mark network coverage partial without discarding the request log metrics. Only aggregates and histograms are published; raw frame timestamps, network sample IDs and clock offsets remain private. These samples do not estimate RTT/2 or include cloud Agent execution time.

## Outlier analysis

The production SFS/workspace collector publishes `anomaly_version: 3` from all readable finished request logs. Each day's threshold uses the previous seven closed calendar days and requires at least 20 samples per metric. Missing workspaces remain marked partial and the baseline explicitly represents only readable logs; an unavailable binding does not erase all historical baselines. Request duration, first text, first answer and ReAct are analysed separately. Normal/outlier phase groups retain their sample counts and sums so the page can merge means correctly across the selected range. The maximum, timeout counts and outlier totals cover that range rather than its final day. Explicit `llm_call_error` and gateway stream exception timeout events are deduplicated by request ID within each workspace/day; unidentified events retain an event count with request count unknown. Raw error text is never published. Request identifiers and detail rows are retained only for the latest seven days; older days keep numeric aggregates. Collector code releases trigger a fresh production collection. Legacy direct-environment snapshots below use version 2.

`anomaly_version: 2` adds an `anomaly` object to every cloud-side daily row. For each day, the collector pools the previous up-to-seven complete days (excluding the day itself and any partial day) and computes a nearest-rank P95 per metric. A sample is an outlier when it is strictly greater than that baseline P95. `metrics` reports `n`, `baseline_n`, `baseline_p95`, `outliers`, and `rate` for request duration, first token, answer first token, ReAct, model-call TTFT, TPOT, and tool calls; a metric with fewer than 20 baseline samples is marked `insufficient_baseline` instead of being scored. `cohort` compares the phase profile of requests whose gateway duration exceeds the gateway baseline P95 with the rest of the day: `first_token`, `react`, and `answer_tail` partition gateway duration, so `driver` names the phase with the largest mean increase.

The same object carries the latency tail and timeout inventory even when no baseline exists:

- `latency` reports `n`, `max_ms`, `max_request`, `slow_count`, `slow_threshold_ms`, and up to 50 `slow_requests` — the slowest 5% of the day's gateway durations (`ceil(n × 0.05)`), each with `request_id` and whichever phase values the request's samples provide. Phase keys that the request did not measure are omitted, not zeroed.
- `timeouts` reports `count` (failed calls whose `llm_call_error` classified as a timeout), `records` (the timeout log lines actually seen), and up to 50 `requests`. A timeout id matched to a phase sample carries its timings; an unmatched id is published as `{request_id}` only, so the request can still be found in the gateway without inventing measurements.
- `error_categories` counts all failed model calls of the day by the same categories as the model-side analysis, and `timeout_rids` carries up to 200 `request_id` values from timeout log lines. The dashboard turns the category counts into the failure-reason line of the anomaly card.

The collector publishes aggregate counts and a seven-day inventory of opaque request identifiers with timing and status fields, including requests with a dispatch marker and no terminal log. Message content, prompts, user identifiers, credentials, and raw log lines stay private. The baseline uses earlier complete days, since using the analysed day's own P95 would mechanically label about 5% of every day as anomalous.

The native archive reconciles that inventory with archived roots by a unique request ID and compatible time interval. It reports matched requests, requests awaiting an archive, ambiguous or conflicting associations, and native traces whose request logs are absent. A cancelled request can close its stream before the native root completes. When the uniquely linked root starts within that request, reconciliation records its measured cleanup tail and retains the association. Pending databases and read failures keep absent traces pending. The page shows the log and archive observation times, the actual log coverage, the selected archive count and the historical total. These observations begin at workspace dispatch. Gateway ingress rejections and forwarding failures require an ingress log source. Older archive snapshots display reconciliation as unavailable until a successful collection publishes the inventory.

## Cloud slow request trajectories and freshness

The tools section lists only cloud-side slow requests, not individual model calls. The production collector reads `/data/state` via the management console's read-only workspace files API, including paused bindings without waking them. It ranks completed gateway requests by duration for each Shanghai calendar day in the last seven days. The daily diagnostic list selects `min(n, max(ceil(n × 0.10), 10))` requests from the *logs actually read*, without the former 50-row daily cap. `slow_trace_sampling` records this policy. The separate anomaly tail remains the slowest 5%. Coverage and errors are published alongside the result; this ranking is not represented as a complete population when logs cannot be read.

The floating time-range selector also filters the full native archive and its request-log reconciliation. Today uses the current Shanghai day; 7/30-day windows end at the latest complete day, matching the other diagnostic views. All includes the current day. The archive date field resets with each range change and can narrow that window further. Reconciliation counts intersect the selected window with the retained log window; cumulative archive and collection coverage remain explicitly labelled. Range changes reset pagination and clear selected details, including pending detail fetches. The archive requests the current range when it initializes, so script load order and the five-minute refresh do not reset the filter.

When daily anomaly analysis is unavailable, the anomaly card can display the matching day's Kubernetes slow-request sample and its maximum. It explicitly marks baseline comparisons and timeout counts as unavailable; a missing timeout count is not zero and the slowest 5% is not the same as exceeding a historical P95 baseline.

For up to 100 selected requests, the collector scans at most 25 recent session histories per workspace, each bounded to 8 MB, and matches tool call/results by exact request and tool call IDs. Only request IDs, timing, allowlisted tool names and status are published; raw commands, tool results, prompts, messages, credentials and workspace IDs are not. Missing phase or model markers and incomplete histories are displayed as missing; a zero model duration is never inferred. Raw logs are streamed in memory and bounded to 200 MB per file. The console may reject large downloads with HTTP 413.

When the production workspace records a unique `TrajectoryCorrelation`, the Kubernetes collector can also read that session's native SQLite store in read-only mode. It matches the request, trace and root span, accepts only final descendants within the root interval, and publishes bounded timing, parent span IDs, status and a fixed set of safe labels. Raw OTLP attributes, model names, tool arguments and conversation content are never published. The native timeline uses the WorkSwarm root clock; it is not stretched to the gateway duration, and nested spans can overlap. Older or incomplete traces fall back to the existing gateway/tool-event view.

The collector publishes `cloud_trace_collection` and daily gateway, first-text, first-answer and ReAct summaries from the same bounded read-only request logs. The summaries include every finished request, not just the slow tail, and carry their own `observed_at` timestamp and source. A day with unreadable bindings or an unfinished current day is marked partial. Historical days with a larger recorded population are retained rather than silently shrunk after log rotation. The collector counts model usage log records and sums their input, output and total tokens; these cannot be deduplicated into model calls because deployed records lack invocation IDs. TPOT is weighted by output-token intervals for valid usage records. It separately reports gateway stream exceptions divided by streams explicitly logged as normal or exceptional, excluding cancelled and unknown streams. A normal gateway stream can carry a business error: this is not a business request failure rate. Old daily call totals use a different event-based definition. Tool metrics remain unavailable rather than reusing stale values. Charts include partial days and explain coverage. If no logs are readable, publication fails and preserves the previous snapshot. The page refreshes snapshots every five minutes; scheduled collection is not a real-time feed.

If the current binding sample is smaller than an existing latency row, latency keeps its previous larger population. Newly observed usage and gateway-stream metrics are still published with a separate observation timestamp, their own request denominator and an explicit partial-coverage marker; the two populations are never described as identical.

Cloud summary cards aggregate the selected calendar range. Means are weighted by samples or output-token intervals; tokens and event counts are summed over available days. Multi-day P50/P95 use mergeable 2%-wide histograms and are labelled approximate. When a retained larger historical sample has no histogram, a newer, smaller sample may be used for the latency cards if its distributions are available; the dashboard marks the day partial and reports that substitution. Otherwise the percentile remains unavailable rather than averaging daily percentiles. The retained historical data is not overwritten. The cards state the date span, partial coverage and usage-record duplication limit.

Following DigitalMate PR #975, the main answer-first metric is the first gateway `chat.delta` minus gateway acceptance; ReAct is the first gateway answer minus the first gateway text for the same request. Separately, the collector matches request IDs to a nonempty `chat.final` history record whose segment start lies inside the request and has a later completion timestamp; its start provides a separately labelled agent-history estimate, not an exact gateway-forwarding timestamp. Estimates never contribute to the gateway metric or ReAct. The estimate includes no message text. At most 25 recent sessions per workspace are scanned and unavailable or truncated histories are counted as partial, so sample coverage is not the full request population.

For Today, cloud charts, cards and the slow-request list use the current Shanghai calendar day. The 7- and 30-day slow-request windows match the model summary's latest complete-day anchor, while retaining historically incomplete cloud rows and marking them partial. The slow-request list's All choice includes all available dates through today, including the latest partial-day collection; headline aggregate cards continue to use complete days. The list displays the actual selected window and every matching collected slow-request row, grouped by Shanghai calendar date (newest first) and sorted by duration within each day. The grouped list is scrollable for longer results.

The collector reads up to 30 calendar days of aggregate evidence available in the current workspaces, while per-request slow-trace identifiers remain limited to the most recent seven days. Older aggregate days do not expose request IDs or raw usage records.

The production console default is `https://onlineswarm.openjiuwen.com:31472` (overridable with `ADX_BASE`). The inventory follows all `next_page_token` pages, and only a snapshot labelled `adx-jiuwen` may seed publication. `ADX_TOKEN` must be valid for the production console. A localhost-only diagnostic may load a separate local evidence file and never reaches GitHub Pages.

The Kubernetes collector can also recover native traces for older gateways that lack an explicit trajectory link: it searches the selected workspace's native session databases for a unique root with the exact request ID. It skips this fallback when explicit links conflict, when any database is unreadable, or when more than 64 databases are present. It never infers a relationship from timestamps. Only final, bounded descendants and allowlisted labels are published; prompts, outputs, tool parameters and arbitrary tool names are not. The request selector covers the existing bounded slow-request sample, not every request. The native profile and the gateway-relative request/tool view use different origins and are displayed separately; nested and parallel spans must not be summed. A missing profile means no reliable association was available, not that no work occurred. The scheduled console collector cannot read the native stores; it retains a previously published profile only when the request ID is unique in both snapshots and its date, start and gateway duration still match. Retained profiles show their original collection time. New profiles still require the Kubernetes collector.
