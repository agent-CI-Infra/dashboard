# DigitalMate Bench Baseline

- captured: `2026-11-03T06:03:33Z`
- run: [#41579349187](https://github.com/openJiuwen-ai/DigitalMate/actions/runs/41579349187)
- commit: `9bc91a3afec3b01128b5704d82e387957bf4bf74`
- backend: `wss://1.94.211.46/orbit-profiling/ws?binding_id=default`

| benchmark | result | tasks | trials | pass@1 | pass@k | pass^k | score / detail | p50 / p95 |
|---|---|---:|---:|---:|---:|---:|---|---|
| GAIA | PASS | 6 | 18 | 27.8% | 33.3% | 16.7% | 1 flaky | 162.5s / 206.1s |
| PAST-Bench | PASS | 6 | 6 | 100.0% | 100.0% | 100.0% | 0 flaky | 24.1s / 31.5s |
| Pi-Bench | PASS | 13 | 13 | 76.9% | 76.9% | 76.9% | 0 flaky | 103.0s / 199.2s |
| VitaBench 2.0 | PASS | 9 | 9 | 77.8% | 77.8% | 77.8% | 0 flaky | 60.7s / 68.2s |
| VibeLifeBench office_fitout_15d | FAIL | 1 stage | 1 | — | — | — | 0.583 (3.5/6.0 weight) | 113.2s |
| VibeLifeBench apartment_renovation_20d | INFO | 1 stage | 1 | — | — | — | apt_s0_artifact=fail; apt_s0_tool_evidence=skipped | 73.6s |

> VibeLife rows are synced from standalone run 41591625795. The original all-run office check was a 203.9s timeout (0.000).
> Headless component-level results; text proxies are not official leaderboard scores.
