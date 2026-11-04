# DigitalMate test-case matrix view

The `用例矩阵` tab of the DigitalMate board. It reads `data/test-matrix.json` from the imported `digitalmate` branch; other boards do not show this tab.

`scripts/digitalmate-test-matrix.mjs` builds the file from a DigitalMate checkout (`main` plus the work-in-progress branches listed in `config/digitalmate-test-matrix.json`), the ledger issue (#533) and the latest scheduled nightly run. `.github/workflows/collect-digitalmate-test-matrix.yml` runs it every three hours and on demand, and only publishes when cases changed.

Rows cover Node UT files, personal-agent pytest files, `tests/cases/case-catalog.yaml` regressions, ST scenes and specifications, E2E workflows and eval tasks. ST/E2E statuses come from the ledger issue; cases outside the ledger show `无当前结果` together with the latest nightly job outcome. Dependencies of cases that have not run are clustered by keyword (`DEPENDENCY_RULES`), and a case can belong to several clusters.

Only case identity, titles, file paths, owners, priorities, statuses, dependency labels and issue/PR/evidence links are published. Prompts, fixtures, endpoints, logs and credentials are never read into the output. File links point at the private source repository and open only for readers with access.

The collector needs the `DIGITALMATE_TOKEN` secret with Contents: read, Issues: read and Actions: read on `openJiuwen-ai/DigitalMate`; without it the workflow is a no-op.
