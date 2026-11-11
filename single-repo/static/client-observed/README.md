# DigitalMate client-observed view

Device-reported metrics kept separate from the production view. The view reads
`data/client-observed-metrics.json`, produced by
`.github/workflows/collect-client-observed-metrics.yml` through
`scripts/collect-client-observed-metrics.py`, and triggers the Pages deployment when the
aggregate changes. Configure the `CLIENT_OBSERVED_DEPLOYMENTS` Actions secret: a JSON array
of `{"environment", "url", "token"}` entries, where `token` is the deployment's
`DIGITALMATE_TELEMETRY_EXPORT_TOKEN`. The runner must reach each deployment.

Each deployment serves `GET /digitalmate/telemetry/summary` (`client-observed-aggregate@1`)
only when its receiver is enabled with `DIGITALMATE_TELEMETRY_DIR`. The collector copies only
numbers, model names and the deployment label; it never publishes trace, task or request IDs,
message text, prompts, responses, credentials or client-claimed environment names. Environment
labels come from the secret's deployment configuration, so a device cannot relabel its data.

The data is advisory (`client-observed`): it is never merged into the classified production
snapshot. Client TTFT and the eight end-to-end segments are client clock differences. Segment
samples only count turns that recorded both boundary marks, so each segment may cover a
different number of turns. Cross-environment averages are weighted by sample count; P95 is
shown per environment only because percentiles cannot be merged exactly. A deployment that
cannot be read on this run reuses its previous record marked as a historical snapshot; if no
deployment can be read at all, the published file is left unchanged.
