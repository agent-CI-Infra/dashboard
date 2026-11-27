import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import test from 'node:test';
import { chromium } from 'playwright';

test('current reply cards and curves use current samples independently of legacy logs', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.clock.setFixedTime(new Date('2026-11-27T02:00:00Z'));
    const bucket = (n) => ({ n, sum_ms: n * 5000, mean_ms: 5000, p50_ms: 4000, p95_ms: 6000, histogram: [[430, n]] });
    const base = { schema: 'digitalmate/env-request-latency-daily@1', env: 'adx-jiuwen',
      days: [{ date: '2026-11-24', n: 1, gateway: { n: 1, mean: 1000, p50: 1000, p95: 1000 } }] };
    const snapshots = {
      'production-model-history.json': { metrics_version: 7, cases: {}, complete_through: '2026-11-26' },
      'production-request-latency-daily.json': { ...base, generated_at: '2026-11-27T02:00:00Z' },
      'env-request-latency-daily.json': { ...base, generated_at: '2026-11-27T01:00:00Z', cloud_reply_daily: {
        schema: 'digitalmate/cloud-reply-daily@1', observed_at: '2026-11-27T02:00:00Z',
        coverage: { read_nodes: 6, expected_nodes: 6, matched_environments: 194, expected_bindings: 217 },
        days: [{ date: '2026-11-27', partial: true, normal_response: bucket(20), quick_send: bucket(5), normal_push: bucket(0) }],
      } },
    };
    await page.route('**/*', async (route) => {
      const { pathname, hostname } = new URL(route.request().url());
      if (hostname !== '127.0.0.1') return route.abort();
      if (pathname.startsWith('/data/')) {
        const data = snapshots[pathname.split('/').at(-1)];
        return data ? route.fulfill({ json: data }) : route.fulfill({ status: 404 });
      }
      const asset = pathname.split('/').at(-1) || 'index.html';
      if (!/^(index\.html|[a-z.-]+\.(css|js))$/.test(asset)) return route.fulfill({ status: 404 });
      return route.fulfill({ path: fileURLToPath(new URL(`../single-repo/static/production-metrics/${asset}`, import.meta.url)) });
    });
    await page.goto('http://127.0.0.1:16831/production-metrics/#models');
    const replies = page.locator('.cloud-replies');
    await replies.getByText(/最新实际样本日 2026-11-27/).waitFor();
    assert.equal(await replies.locator('[data-cloud-reply-metric]').count(), 2);
    assert.equal(await replies.locator('.trend-chart').count(), 2);
    assert.match(await replies.innerText(), /20 个有效消息样本/);
    assert.match(await replies.innerText(), /5 个有效消息样本/);
    assert.equal(await replies.locator('[data-cloud-reply-metric="normal_push"]').count(), 0);
    assert.equal(await page.locator('.historical-request-metrics:not([open])').count(), 1);
    for (const range of [1, 7, 30, 0]) {
      await page.locator(`#floating-range-controls [data-range="${range}"]`).click();
      assert.equal(await replies.locator('[data-cloud-reply-metric]').count(), 2);
      assert.match(await replies.innerText(), /2026-11-27/);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

test('the production refresh workflow persists the dedup cursor and runs the real reader', () => {
  const workflow = readFileSync(new URL('../.github/workflows/collect-cloud-request-metrics.yml', import.meta.url), 'utf8');
  assert.match(workflow, /python3 -B scripts\/collect-production-cloud-replies\.py/);
  assert.match(workflow, /cp .*cloud-reply-state\.json \.sync\/cloud-reply-state\.json/);
});
