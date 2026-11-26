import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { chromium } from 'playwright';

const code = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
const shell = readFileSync(new URL('../single-repo/static/index.html', import.meta.url), 'utf8');
const shellCode = readFileSync(new URL('../single-repo/static/app.js', import.meta.url), 'utf8');
const frame = readFileSync(new URL('../single-repo/static/production-metrics/index.html', import.meta.url), 'utf8');
function extract(start, end) {
  const a = code.indexOf(start), b = code.indexOf(end, a);
  assert(a >= 0 && b > a);
  return code.slice(a, b);
}
const ctx = vm.createContext({});
vm.runInContext(`const esc=String, fmtCount=String, fmtDateTime=String, fmtPercent=String;
  const trimNumberZeros=String;
  const fmtTpotBare=(v)=>Number.isFinite(v)?v.toFixed(2):'—';
  const fmtSecondsBare=(v)=>Number.isFinite(v)?String(v/1000):'—';` +
  extract('function mergeMetricHistogram(', '// Each bucket covers') +
  extract('function aggregateCloudMetric(', '// Outlier analysis.') +
  ';globalThis.render=envSummary; globalThis.summary=summarizeCloudWindow;', ctx);

const row = (date, value, count = 1) => ({ date, gateway: { n: 0 },
  uplink_send: { n: count, sum: value * count, mean: value, p50: value, p95: value,
    histogram: [[Math.floor(Math.log(value) / Math.log(1.02)), count]] } });
const card = (html, key) => html.match(new RegExp(`data-metric="${key}"[\\s\\S]*?(?=<div class="stat-card|</section>)`))?.[0];

test('production script releases invalidate the shell bundle and embedded page caches', () => {
  const version = frame.match(/<script src="app\.js\?v=([^"&]+)"/)[1];
  const shellVersion = shell.match(/<script src="\.\/app\.js\?v=([^"&]+)"/)[1];
  const frameVersion = shellCode.match(/'production-metrics': '\.\/production-metrics\/index\.html\?v=([^'#&]+)#models'/)[1];
  assert.equal(frameVersion, version, 'the iframe URL must change with its script release');
  assert.equal(shellVersion, version, 'the shell must load the bundle containing the new iframe URL');
  assert.equal(frame.match(/src="trajectory-archive\.js\?v=([^"&]+)"/)[1], version,
    'the archive module and its range-event producer must release together');
});

test('all three network cards stay in the cloud summary without valid samples', () => {
  const html = ctx.render({ days: [{ date: '2026-11-19', gateway: { n: 3 } }] });
  for (const key of ['uplink_send', 'downlink_first_text', 'downlink_terminal']) {
    const text = card(html, key);
    assert.match(text, /平均 \/ P50 \/ P95（ms）/);
    assert.match(text, /— \/ — \/ —/);
    assert.match(text, /所选范围无有效样本/);
    assert.match(text, /覆盖 0 \/ 1 天/);
  }
});

test('cloud summary shows measured first answer without a historical estimate card or fallback', () => {
  const measured = { n: 1, mean: 100, p50: 100, p95: 100 };
  const data = { date: '2026-11-19', gateway: measured, answer_first: measured,
    answer_first_history: { n: 100, mean: 999, p50: 999, p95: 999 } };
  const html = ctx.render({ days: [data] });
  assert.match(html, /<strong>答案首吐字<\/strong>/);
  assert.match(html, /0\.1 \/ 0\.1 \/ 0\.1/);
  assert.doesNotMatch(html, /历史估算|999/);
  delete data.answer_first;
  const missing = ctx.render({ days: [data] }).match(/<div class="stat-card">[\s\S]*?<\/div>\s*<\/div>/g)
    .find(text => text.includes('<strong>答案首吐字</strong>'));
  assert.match(missing, /— \/ — \/ —/);
});

test('network cards use milliseconds and distinguish first text and terminal downlink', () => {
  const data = row('2026-11-19', 120);
  data.downlink_first_text = { n: 2, mean: 30, p50: 20, p95: 40 };
  data.downlink_terminal = { n: 1, mean: 50, p50: 50, p95: 50 };
  const html = ctx.render({ days: [data] });
  assert.match(card(html, 'uplink_send'), /120 \/ 120 \/ 120/);
  assert.match(card(html, 'downlink_first_text'), /30 \/ 20 \/ 40/);
  assert.match(card(html, 'downlink_terminal'), /50 \/ 50 \/ 50/);
  assert.match(card(html, 'uplink_send'), /1 个有效样本/);
});

test('selected date ranges pool sample weights and histograms, exposing partial coverage', () => {
  const days = [row('2026-11-18', 10), { ...row('2026-11-19', 100, 9), network_partial: true }];
  const pooled = ctx.summary(days).uplink_send;
  assert.equal(pooled.n, 10);
  assert.equal(pooled.mean, 91);
  assert.equal(pooled.approx, true);
  assert(pooled.p95 > 99 && pooled.p95 < 102);
  const html = ctx.render({ days });
  assert.match(card(html, 'uplink_send'), /91 \/ ≈/);
  assert.match(card(html, 'uplink_send'), /覆盖 2 \/ 2 天 · 采集部分覆盖/);
  assert.match(card(ctx.render({ days: [days[0]] }), 'uplink_send'), /10 \/ 10 \/ 10/);
});

const context = vm.createContext({});
vm.runInContext(readFileSync(new URL('../single-repo/static/production-metrics/release-timeline.js', import.meta.url), 'utf8'), context);
const { normalize, phase, splitPath } = context.DigitalMateReleaseTimeline;
const releases = normalize({ releases: [
  { tag: 'v0.1.0', published_at: '2026-11-01T00:00:00Z' },
  { tag: 'v0.2.0', published_at: '2026-11-02T16:00:00Z' },
  { tag: 'v0.2.1', published_at: '2026-11-04T05:00:00Z' },
] });

test('uses the refreshed operations release feed and ignores drafts, prereleases and invalid entries', () => {
  const data = normalize({
    sections: { ops: { data: { releases: { items: [
      { tag: 'v0.2.0', published_at: '2026-11-03T00:00:00Z' },
      { tag: 'v0.3.0', draft: true, published_at: '2026-11-05T00:00:00Z' },
      { tag: 'v0.4.0', prerelease: true, published_at: '2026-11-06T00:00:00Z' },
      { tag: 'v0.5.0', published_at: 'not-a-date' },
      { tag: '<script>', published_at: '2026-11-05T00:00:00Z' },
    ] } } } },
    releases: [
      { tag: 'v0.2.0', published_at: '2026-11-02T00:00:00Z' },
      { tag: 'v0.1.0', published_at: '2026-11-01T00:00:00Z' },
    ],
  });
  assert.deepEqual(Array.from(data, (row) => row.tag), ['v0.1.0', 'v0.2.0']);
  assert.equal(data[1].published, Date.parse('2026-11-03T00:00:00Z'));
});

test('release at midnight Shanghai applies to the new day, not the previous UTC date', () => {
  assert.equal(phase('2026-11-02', releases).label, 'v0.1.0');
  assert.equal(phase('2026-11-03', releases).label, 'v0.2.0');
});

test('a publication day retains both phases instead of attributing the entire day to the latest version', () => {
  assert.equal(phase('2026-11-04', releases).label, 'v0.2.0 → v0.2.1（发布日）');
  assert.equal(phase('2026-11-05', releases).label, 'v0.2.1');
});

test('earlier dates and missing release history stay unknown', () => {
  assert.equal(phase('2026-10-31', releases).label, '发布版本未知');
  assert.equal(phase('2026-11-05', []).label, '发布版本未知');
  assert.equal(phase('invalid-date', releases).label, '发布版本未知');
  assert.deepEqual(Array.from(normalize({})), []);
});

test('all same-day publications stay visible and colors remain stable across range changes', () => {
  const extra = [...releases, { tag: 'v0.2.2', published: Date.parse('2026-11-04T06:00:00Z') }];
  assert.equal(phase('2026-11-04', extra).label, 'v0.2.0 → v0.2.1 → v0.2.2（发布日）');
  assert.equal(phase('2026-11-05', releases).color, phase('2026-11-06', releases).color);
});

test('curves break at release-phase changes while retaining existing gaps and area shapes', () => {
  const points = [{ x: 0, key: 'old' }, { x: 10, key: 'old' }, { x: 20, key: 'new' }, { x: 30, key: 'new' }];
  assert.equal(splitPath('M0,1L10,2L20,3L30,4', points), 'M0,1L10,2M20,3L30,4');
  assert.equal(splitPath('M0,1M10,2L20,3L30,4', points), 'M0,1M10,2M20,3L30,4');
  assert.equal(splitPath('M0,1L10,2L20,3Z', points), 'M0,1L10,2L20,3Z');
  assert.equal(splitPath('M0,1L10,2', []), 'M0,1L10,2');
});

test('the entire model section follows cloud diagnostics, starts folded and retains controls and release annotations', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.clock.setFixedTime(new Date('2026-11-06T01:00:00Z'));
    const metric = { n: 1, mean: 100, p50: 100, p95: 100, sum: 100, histogram: [[232, 1]] };
    const daily = { schema: 'digitalmate/env-request-latency-daily@1', env: 'adx-jiuwen',
      generated_at: '2026-11-06T01:00:00Z', days: [1, 2, 3, 4, 5].map((day) => ({
        date: `2026-11-0${day}`, n: 1, gateway: metric, first_token: metric,
        uplink_send: metric, downlink_first_text: metric, downlink_terminal: metric,
      })) };
    const points = daily.days.map((row) => ({ date: row.date, query_ms: 100, query_count: 100,
      success_count: 100, failed_queries: 0, unknown_status_count: 0, duration_sum_ms: 10000,
      duration_sample_count: 100, benchmark_models: [{ model: 'test/model', call_count: 100,
        success_count: 100, duration_sum_ms: 10000, duration_sample_count: 100 }] }));
    const snapshots = {
      'production-model-history.json': { metrics_version: 7,
        cases: { PROD: { title: 'Test model', points } }, complete_through: '2026-11-05' },
      'env-request-latency-daily.json': daily,
      'snapshot.json': { releases: releases.map((row) => ({ tag: row.tag, published_at: new Date(row.published).toISOString() })) },
    };
    const site = new URL('../single-repo/static/production-metrics/', import.meta.url);
    await page.route('**/*', (route) => {
      const { pathname, hostname } = new URL(route.request().url());
      if (hostname !== '127.0.0.1') return route.abort();
      if (pathname === '/shell.html') return route.fulfill({ contentType: 'text/html', body: `<!doctype html>
        <style>body { margin: 0; } iframe { display: block; width: 100%; border: 0; }</style>
        <iframe src="/production-metrics/#models" style="height: 1800px"></iframe>
        <script>addEventListener('message', (event) => {
          const frame = document.querySelector('iframe');
          if (event.source === frame.contentWindow && event.data?.type === 'production-metrics-height')
            frame.style.height = Math.max(200, event.data.height + 24) + 'px';
        });</script>` });
      if (pathname.startsWith('/data/')) {
        const data = snapshots[pathname.split('/').at(-1)];
        return data ? route.fulfill({ json: data }) : route.fulfill({ status: 404 });
      }
      const asset = pathname.split('/').at(-1) || 'index.html';
      if (!/^(index\.html|[a-z.-]+\.(css|js))$/.test(asset)) return route.fulfill({ status: 404 });
      return route.fulfill({ path: fileURLToPath(new URL(asset, site)) });
    });
    await page.goto('http://127.0.0.1:16831/production-metrics/#models');
    await page.locator('#env-latency .release-chip').getByText('v0.2.1', { exact: true }).first().waitFor();
    const models = page.locator('#model-section');
    assert.equal(await models.evaluate((node) => node.open), false);
    for (const id of ['model-insights', 'model-compare', 'model-summary', 'model-trends-card', 'model-failure-card', 'model-stats']) {
      assert.equal(await models.locator(`#${id}`).count(), 1, `${id} belongs inside the model disclosure`);
      assert.equal(await models.locator(`#${id}`).isVisible(), false, `${id} starts hidden`);
    }
    assert(await models.evaluate((node) => Boolean(document.querySelector('#request-lookup')
      .compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)));
    assert.equal(await page.locator('#view-models > .view-section-head').first().getAttribute('id'), 'env-section');
    assert.equal(await page.locator('#client-section').count(), 0);
    assert.equal(await page.locator('#env-latency .network-card').count(), 3);
    assert(await page.locator('#env-latency .network-card').first().isVisible());
    assert(await page.locator('#env-latency .env-summary').evaluate((node) => {
      const cards = [...node.children];
      const network = cards.filter((card) => card.classList.contains('network-card'));
      return cards.indexOf(network[0]) === 5 && cards.indexOf(network[2]) === 7;
    }));
    const replies = page.locator('#env-latency .reply-timing-card');
    assert.equal(await replies.count(), 3);
    assert.deepEqual(await replies.locator('.stat-label strong').allTextContents(), ['快回复', '正常回复（Bee）', '最终回复']);
    for (const card of await replies.all()) {
      assert.equal(await card.isVisible(), true);
      assert.equal(await card.locator('.stat-value').innerText(), '— / — / —');
      assert.match(await card.innerText(), /暂无打点数据/);
    }
    assert.equal(await page.locator('#env-latency [data-env-series*="reply"]').count(), 0);
    const zone = page.locator('#env-latency .hover-zone[data-date="2026-11-04"]').first();
    assert.match(await zone.getAttribute('data-tip'), /v0\.2\.0 → v0\.2\.1（发布日）/);
    assert.match(await zone.getAttribute('data-tip'), /非请求实际部署版本/);
    assert(await page.locator('#env-latency .release-divider').count() > 0);
    assert(await page.locator('#model-trends .release-chip').count() > 0);
    await page.locator('#floating-range-controls [data-range="1"]').click();
    assert.equal(await models.evaluate((node) => node.open), false);
    assert.equal(await page.locator('#env-latency .network-card').count(), 0);
    assert.equal(await replies.count(), 3);
    assert(await replies.first().isVisible());
    await page.locator('#floating-range-controls [data-range="7"]').click();
    assert.equal(await page.locator('#env-latency .network-card').count(), 3);
    await models.locator(':scope > summary').click();
    await models.locator('#model-trends .release-chip').first().waitFor();
    assert.equal(await models.locator('#model-summary').isVisible(), true);
    await models.locator('#model-controls [data-range="30"]').click();
    assert.equal(await models.evaluate((node) => node.open), true);
    await page.evaluate(() => refreshProductionSnapshots());
    assert.equal(await models.evaluate((node) => node.open), true);
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto('http://127.0.0.1:16831/shell.html');
    const embedded = page.frameLocator('iframe');
    await embedded.locator('#env-latency .release-chip').first().waitFor();
    const matchesContentHeight = () => {
      const frame = document.querySelector('iframe');
      return Math.abs(frame.getBoundingClientRect().height - (frame.contentDocument.body.getBoundingClientRect().height + 24)) < 2;
    };
    await page.waitForFunction(matchesContentHeight);
    const foldedHeight = (await page.locator('iframe').boundingBox()).height;
    await embedded.locator('#model-section > summary').click();
    await page.waitForFunction((height) => document.querySelector('iframe').getBoundingClientRect().height > height + 500, foldedHeight);
    await page.waitForFunction(matchesContentHeight);
    await embedded.locator('#model-section > summary').click();
    await page.waitForFunction((height) => document.querySelector('iframe').getBoundingClientRect().height < height + 2, foldedHeight);
    await page.waitForFunction(matchesContentHeight);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
