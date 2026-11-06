# CI gate protocol contracts

These schemas are the contract between **CI-workflow** (producer), the **per-repository board** (edge aggregator) and this **public dashboard** (federation consumer). They are deliberately versioned and append-only: dashboards must keep working when a producer only emits an older subset.

> Canonical schemas live in [`agent-CI-Infra/CI-workflow`](https://github.com/agent-CI-Infra/CI-workflow); this directory keeps the dashboard validator copy. Protocol identifiers remain `ci-gates/*` for backward compatibility. Do not fork the definitions: copy, don't rewrite.

| File | Contract | Producer | Consumers |
|---|---|---|---|
| `gate-report.schema.json` | `ci-gates/gate-report@1` (v1.1 fields are optional) | CI-workflow `verdict` job | board, dashboard artifact mode |
| `case-catalog.schema.json` | `ci-gates/case-catalog@1` | generator + repository owner | CI-workflow `case-contract`, board |
| `board-summary.schema.json` | `ci-gates/board-summary@1` | per-repository board | public dashboard |

## Trust rules encoded by the contract

- `event` decides trust: `merge_group`, `push`, `schedule`, `release` are **trusted**; `pull_request` / `pull_request_target` are **provisional** and must not drive compliance or onboarding stage in the public dashboard.
- Lane `verdict` is a closed set: `pass` / `fail` / `not_applicable` / `waived` / `observe_fail` / `error`. Per-gate `status` additionally distinguishes `not_run`.
- `todo` and `not_run` are never green. A missing core case is `not_run`, not `pass`.
- `board-summary` is whitelisted by design: no case titles, file paths, logs, PR metadata or credentials. It is the only file the public dashboard reads from a board.

## Examples

- `examples/gate-report.build.json` — a `build` lane report at level C1 with an observe gate and case statistics.
- `examples/case-catalog.yaml` — `ready` / `todo` / `skipped` cases across three lanes.
- `examples/board-summary.json` — a public summary with five lanes, 30-day history buckets and case totals.

## Validation

The dashboard validates incoming artifacts in `scripts/lib/ci-gates.mjs` and normalizes summaries into the existing `public/data/gate/<owner>/<name>.json` dataset. The schemas in this directory are the human- and editor-facing source; the runtime validators are intentionally dependency-free.

| Function | Used by | Checks |
|---|---|---|
| `validateSummary` | collector `board-summary` source | schema, repo identity, lane/verdict enums, duplicate aliases, 100 KB limit |
| `validateReport` | future direct artifact mode | schema, repo/sha match, lane/level/verdict, results array, 2 MB limit |
| `validateCatalog` | board / generator | schema, unique ids, lane/layer, ready file+owner, skipped until+reason |
| `summarizeCatalog` | board / report | `total` / `ready` / `todo` / `skipped` |

```bash
npm run test:collector   # 18 fixture-driven tests
```
