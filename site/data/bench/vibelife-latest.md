# VibeLifeBench office_fitout_15d standalone

- run: [#41591625795](https://github.com/openJiuwen-ai/DigitalMate/actions/runs/41591625795)
- commit: `7fbaa283683024cefd0b2354341083b5082781e4`
- backend: `wss://1.94.211.46/orbit-profiling/ws?binding_id=default`
- outcome: `completed`
- score: **0.583** (3.5/6.0 weight)
- elapsed: 113.2s
- artifacts: budget_tracker.md, fit_out_plan.md, risk_register.md, schedule.md, vendor_comparison.md

| check | weight | result | note |
|---|---:|---|---|
| chk_artifact_core_files_exist | 1 | PASS | — |
| chk_artifact_budget_has_committed_pending_reserve | 1.5 | FAIL | Rubric requires committed > 0; Stage 0 has no signed contracts and correctly reports committed=0. |
| chk_artifact_schedule_sequenced | 1.5 | PASS | — |
| chk_artifact_contractor_comparison_rows | 1 | FAIL | Vendor file has 3 vendors, ratings and quotes; rubric amount parser misses bare comma amounts under a ¥ header. |
| chk_artifact_risk_register_has_known_traps | 1 | PASS | — |

> Previous all-run VibeLife 0.000 was a 203.9s WorkSwarm timeout with no artifacts, not this standalone result.
> The two failed checks are rubric false negatives: committed=0 at Stage 0 is valid; the vendor amount parser misses bare comma amounts under a ¥ header.
