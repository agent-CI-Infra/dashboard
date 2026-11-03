# DigitalMate production metrics view

Assets are adapted from DigitalMate `dashboard/e2e-performance` (commit `fadb7b5`). The view reads `data/production-model-history.json`, separately from nightly E2E data. `.github/workflows/collect-production-model-metrics.yml` publishes a 10-day aggregate daily to the imported `digitalmate` branch and triggers Pages deployment. Configure the `PRODUCTION_GATEWAY_KEY` Actions secret in the dashboard repository. The runner must be able to reach the gateway. A failed collection leaves the previous aggregate intact.

Only daily model aggregates are published; raw logs and request IDs are never written. The gateway rows are requests, not user questions. TTFT and TPOT are shown only for successful calls explicitly marked as streaming; missing streaming markers yield no samples. P95 uses successful gateway durations and requires 20 samples. Other boards do not show this tab.
