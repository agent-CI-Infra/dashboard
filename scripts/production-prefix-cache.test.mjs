import { readFileSync } from 'node:fs';
import vm from 'node:vm';
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
    assert.equal(await bee.getByText('有效缓存样本 / 已采集模型调用', { exact: true }).count(), 1);
    assert.match(await cache.innerText(), /缓存计数缺失或无效 3 次，不计入命中率/);
    assert.match(await cache.innerText(), /一次用户请求可能调用模型多次/);
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

// The existing SFS UI test entry also covers independent Hive timing data.
{
  const site = new URL('../single-repo/static/production-metrics/', import.meta.url);
  const stamp = '2026-11-26T02:00:00Z';
  const metric = (ms, n = 1) => ({ n, sum_ms: ms * n, mean_ms: ms, p50_ms: ms, p95_ms: ms,
    histogram: [[ms === 0 ? -1001 : Math.floor(Math.log(ms) / Math.log(1.02)), n]] });
  const day = (date, users, ms) => ({ date, observed_at: stamp, counts: { user_inputs: users, runs_started: 2, runs_unended: 1, failure_attempts: 3 }, active_workspaces: 1,
    metrics: { bee_queue_wait: metric(ms), bee_run_natural: metric(100000), normal_claim: metric(10), quick_commit: { n: 0, mean_ms: null, p50_ms: null, p95_ms: null, histogram: [] } } });

  test('pooled Hive timings weight samples and merge distributions instead of averaging percentiles', () => {
    const code = readFileSync(new URL('app.js', site), 'utf8');
    const extract = (a, b) => code.slice(code.indexOf(a), code.indexOf(b, code.indexOf(a)));
    const context = vm.createContext({});
    vm.runInContext(extract('function hiveMetric(', 'function hiveDailyPanel(')
      + extract('function mergeMetricHistogram(', 'function finalizeMetricAccumulator(')
      + ';globalThis.pool=hiveMetric;', context);
    const stats = context.pool([{ metrics: { x: metric(10, 1) } }, { metrics: { x: metric(1000, 99) } }], 'x');
    assert.equal(stats.n, 100);
    assert.equal(stats.mean_ms, 990.1);
    assert.ok(Math.abs(stats.p95_ms - 1000) / 1000 < .011);
    assert.ok(stats.approximate);
    const sparse = context.pool([{ metrics: { x: metric(10) } }, { metrics: { x: metric(1000) } }], 'x');
    assert.equal(sparse.p95_ms, null);
    assert.equal(context.pool([], 'x').mean_ms, null);
  });

  test('Hive survives a newer legacy producer, follows date controls, and keeps missing reply clocks empty', async () => {
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      const errors = [];
      page.on('pageerror', (e) => errors.push(e.message));
      await page.clock.setFixedTime(new Date(stamp));
      const daily = { schema: 'digitalmate/env-request-latency-daily@1', env: 'adx-jiuwen' };
      const snapshots = {
        'production-model-history.json': { metrics_version: 7, complete_through: '2026-11-25', cases: {
          model: { points: [{ date: '2026-11-25', queries: 1, success_count: 1, duration_sum_ms: 1000,
            duration_sample_count: 1, benchmark_models: [{ model: 'test/model', call_count: 1,
              success_count: 1, duration_sum_ms: 1000, duration_sample_count: 1 }] }] },
        } },
        // A different producer has newer legacy request days, but carries no Hive.
        'production-request-latency-daily.json': { ...daily, generated_at: stamp, days: [{ date: '2026-11-26' }] },
        'env-request-latency-daily.json': { ...daily, generated_at: stamp, days: [{ date: '2026-11-24' }],
          hive_daily: { schema: 'digitalmate/hive-daily@1', observed_at: stamp,
            coverage: { expected: 2, read: 1, with_hive: 1, missing: 0, incomplete: 1 },
            days: [day('2026-11-25', 10, 5000), { ...day('2026-11-26', 20, 8000), partial: true }] } },
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
      await page.goto('http://127.0.0.1:16830/production-metrics/#models?range=1');
      const hive = page.locator('.hive-daily');
      await hive.getByText(/用户输入 20 条/).waitFor();
      assert.match(await hive.innerText(), /8\.000 \/ 8\.000 \/ 8\.000/);
      assert.match(await hive.innerText(), /未读全 1/);
      assert.match(await hive.innerText(), /Run 历时.*不等于用户端到端/);
      const quick = hive.locator('.stat-card').filter({ hasText: '快回服务端提交' });
      assert.match(await quick.innerText(), /— \/ — \/ —/);
      assert.doesNotMatch(await hive.locator('tbody').innerText(), /2026-11-25/);
      await page.locator('#model-controls [data-range="7"]').click();
      await hive.getByText(/用户输入 10 条/).waitFor();
      assert.match(await hive.innerText(), /5\.000 \/ 5\.000 \/ 5\.000/);
      assert.doesNotMatch(await hive.locator('tbody').innerText(), /2026-11-26/);
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.deepEqual(errors, []);
    } finally {
      await browser.close();
    }
  });
}
