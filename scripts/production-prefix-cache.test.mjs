import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const site = new URL('../single-repo/static/production-metrics/', import.meta.url);
const today = '2026-11-17';
const yesterday = '2026-11-16';
const stamp = '2026-11-17T07:00:00Z';
const group = (source, cohort, usage) => ({
  source, model: 'test/model', cohort,
  calls: 1, known: 0, input_tokens: 0, cache_read_tokens: 0, near_miss: 0, miss_eligible: 0, histogram: [],
  ...usage,
});

test('production page shows the SFS cache sample even when it chooses a different latency snapshot', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.clock.setFixedTime(new Date(stamp));
    const daily = { schema: 'digitalmate/env-request-latency-daily@1', env: 'adx-jiuwen' };
    const snapshots = {
      'production-model-history.json': { metrics_version: 7, cases: {}, complete_through: yesterday },
      'production-request-latency-daily.json': { ...daily, generated_at: stamp, days: [{ date: today }] },
      'env-request-latency-daily.json': {
        ...daily, generated_at: '2026-11-17T06:00:00Z', days: [{ date: yesterday }],
        prefix_cache: {
          schema: 'digitalmate/prefix-cache-daily@1', observed_at: stamp, workspaces: 3,
          coverage: { beebou: { with_calls: 1 }, bees: { with_calls: 1 }, workswarm: { with_calls: 2 } },
          days: [
            { date: '2026-11-15', groups: [group('workswarm', 'returning', { model: 'legacy/model' })] },
            { date: yesterday, observed_at: stamp, groups: [group('beebou', 'first_observed', {})] },
            {
              date: today, observed_at: stamp,
              groups: [
                group('beebou', 'first_observed', { calls: 15, known: 15, input_tokens: 61440,
                  cache_read_tokens: 61440, miss_eligible: 15, histogram: [[1000, 15]] }),
                group('bees', 'returning', { calls: 30, known: 30, input_tokens: 122880, miss_eligible: 30,
                  near_miss: 30, histogram: [[0, 30]] }),
                group('bees', 'returning', { calls: 3 }),
                group('workswarm', 'returning', { model: 'legacy/model', calls: 500, known: 500,
                  input_tokens: 2048000, cache_read_tokens: 2048000, histogram: [[1000, 500]] }),
              ],
              sessions: { beebou: { known: 1, p50: 1, p95: 1 }, bees: { known: 1, p50: 0, p95: 0 } },
              watch: { bees: { status: 'spike', near_miss: 30, eligible: 30 } },
            },
          ],
        },
      },
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
      return route.fulfill({ path: fileURLToPath(new URL(asset, site)) });
    });

    const url = 'http://127.0.0.1:16830/production-metrics/#models?range=1';
    await page.goto(url);
    const cache = page.locator('section.env-anomaly').filter({ has: page.getByRole('heading', { name: '模型前缀缓存命中率' }) });
    await cache.getByText('近零命中突增').waitFor();
    const bee = cache.locator('[data-cache-source="bees"]');
    const beebou = cache.locator('[data-cache-source="beebou"]');
    assert.deepEqual(await cache.locator('.prefix-cache-source h4').allTextContents(), ['Bee', 'BeeBou']);
    assert.equal(await bee.locator('.stat-value').first().innerText(), '0.0%');
    assert.equal(await beebou.locator('.stat-value').first().innerText(), '100.0%');
    assert.match(await bee.innerText(), /30 \/ 33/);
    assert.match(await beebou.innerText(), /15 \/ 15/);
    assert.match(await cache.innerText(), /缺失或无效 3 次/);
    assert.match(await cache.innerText(), /BeeBou 1 \/ 3/);
    assert.doesNotMatch(await cache.innerText(), /WorkSwarm|legacy\/model|undefined/);
    assert.doesNotMatch(await cache.innerText(), /2026-11-16/);
    await cache.getByText('按链路、模型与会话拆分').click();
    assert.equal(await cache.getByText('test/model', { exact: true }).filter({ visible: true }).count(), 2);
    const desktop = await cache.locator('.prefix-cache-source').evaluateAll((cards) => cards.map((card) => {
      const { x, y, width } = card.getBoundingClientRect();
      return { x, y, width };
    }));
    assert.equal(desktop[0].y, desktop[1].y);
    assert.ok(desktop[1].x > desktop[0].x);
    await page.setViewportSize({ width: 390, height: 844 });
    const mobile = await cache.locator('.prefix-cache-source').evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().y));
    assert.ok(mobile[1] > mobile[0]);
    await page.goto('http://127.0.0.1:16830/production-metrics/?history#models');
    await cache.locator('.prefix-cache-source').first().waitFor();
    assert.doesNotMatch(await cache.innerText(), /WorkSwarm|legacy\/model|2026-11-15|undefined/);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
