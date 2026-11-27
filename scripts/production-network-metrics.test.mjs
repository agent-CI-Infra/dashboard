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
  extract('function cloudRequestSampleStatus(', 'function renderEnv(') +
  ';globalThis.render=envSummary; globalThis.summary=summarizeCloudWindow; globalThis.sampleStatus=cloudRequestSampleStatus;', ctx);

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

test('network cards without measured samples stay out of the cloud summary', () => {
  const html = ctx.render({ days: [{ date: '2026-11-19', gateway: { n: 3 } }] });
  for (const key of ['uplink_send', 'downlink_first_text', 'downlink_terminal']) {
    assert.equal(card(html, key), undefined);
  }
});

test('a new collection timestamp cannot make old request samples current, and zero durations remain real samples', () => {
  const doc = { generated_at: '2026-11-27T01:00:00Z', cloud_trace_collection: { generated_at: '2026-11-27T01:00:00Z' },
    days: [{ date: '2026-11-24', gateway: { n: 3, mean: 100 } }, { date: '2026-11-27', gateway: { n: 0 } }] };
  const old = ctx.sampleStatus(doc, '2026-11-27');
  assert.equal(old.latest, '2026-11-24');
  assert.equal(old.lagDays, 3);
  assert.equal(old.historical, true);
  doc.days[1].gateway = { n: 1, mean: 0, p50: 0, p95: 0 };
  assert.equal(ctx.sampleStatus(doc, '2026-11-27').historical, false);
  assert.match(ctx.render({ days: [doc.days[1]] }), /0 \/ 0 \/ 0/);
  assert.equal(ctx.sampleStatus({ days: [{ date: '2026-11-28', gateway: { n: 1 } }] }, '2026-11-27').latest, undefined);
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
  const missing = ctx.render({ days: [data] });
  assert.doesNotMatch(missing, /<strong>答案首吐字<\/strong>|历史估算|999/);
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
const { normalize, phase } = context.DigitalMateReleaseTimeline;
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
  const day = phase('2026-11-03', releases);
  assert.equal(day.label, 'v0.2.0（当日 1 次发布）');
  assert.equal(day.events.length, 1);
  assert.deepEqual(Array.from(day.periods, (period) => [period.tag, period.from, period.to]), [['v0.2.0', 0, 1]]);
});

test('a publication day retains both phases instead of attributing the entire day to the latest version', () => {
  assert.equal(phase('2026-11-04', releases).label, 'v0.2.0 → v0.2.1（当日 1 次发布）');
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
  const day = phase('2026-11-04', extra);
  assert.equal(day.label, 'v0.2.0 → v0.2.1 → v0.2.2（当日 2 次发布）');
  assert.deepEqual(Array.from(day.periods, (period) => [period.from, period.to]), [[0, 13 / 24], [13 / 24, 14 / 24], [14 / 24, 1]]);
  assert.equal(day.events.length, 2);
  assert.equal(phase('2026-11-05', releases).periods[0].color, phase('2026-11-06', releases).periods[0].color);
});

test('release markers preserve continuous lines, data gaps and areas while distinguishing same-day publications', async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const paths = ['M120,70L360,55L600,60', 'M120,45M360,40L600,35', 'M120,90L360,65L600,90Z'];
    await page.setContent(`<div id="charts"><svg class="trend-chart" viewBox="0 0 720 100">
      ${paths.map((path) => `<path class="trend-series" d="${path}"></path>`).join('')}
      ${['2026-11-03', '2026-11-04', '2026-11-05'].map((date, index) => `<rect
        class="hover-zone" data-date="${date}" data-tip="${date} 请求数：10"
        x="${index * 240}" y="10" width="240" height="80"></rect>`).join('')}
      </svg></div>`);
    await page.addScriptTag({ path: fileURLToPath(new URL('../single-repo/static/production-metrics/release-timeline.js', import.meta.url)) });
    await page.evaluate((rows) => {
      const timeline = window.DigitalMateReleaseTimeline;
      const root = document.querySelector('#charts');
      timeline.decorate(root, rows);
      timeline.decorate(root, rows);
    }, [...releases, { tag: 'v0.2.2', published: Date.parse('2026-11-04T06:00:00Z') }]);
    assert.deepEqual(await page.locator('path.trend-series').evaluateAll((paths) => paths.map((path) => path.getAttribute('d'))), paths);
    const markers = await page.locator('.release-divider').evaluateAll((lines) => lines.map((line) => [line.dataset.release, Number(line.getAttribute('x1'))]));
    assert.deepEqual(markers, [['v0.2.1', 370], ['v0.2.2', 380]]);
    assert.equal(await page.locator('.release-bands').count(), 1);
    assert.equal(await page.locator('.release-legend').count(), 1);
    const tip = await page.locator('[data-date="2026-11-04"]').getAttribute('data-tip');
    assert.match(tip, /v0\.2\.1 13:00；v0\.2\.2 14:00/);
    assert.match(tip, /当日 2 次发布/);
    assert.match(tip, /日聚合数据无法按发布时刻拆分/);
    assert.match(tip, /非请求实际部署版本/);
    assert.equal(tip.match(/请求数：10/g).length, 1);
    await page.evaluate(() => window.DigitalMateReleaseTimeline.decorate(document.querySelector('#charts'), []));
    assert.deepEqual(await page.locator('path.trend-series').evaluateAll((paths) => paths.map((path) => path.getAttribute('d'))), paths);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});

test('production metrics and version analysis preserve ranges, trace links, disclosures and embedded sizing', async () => {
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
        gateway_stream: { exception: day % 2, observed: 1, cancelled: 0, unknown: 0, population: 1 },
        request_latency_distributions: { schema: 'digitalmate/request-latency-distributions@1',
          all: metric, failed: day % 2 ? metric : { n: 0 }, slow: metric,
          slow_policy: { ratio: .1, minimum: 10 } },
        ...(day === 5 ? { anomaly: { available: false, timeouts: { count: 1, records: 1, identified: 1,
          requests: [{ request_id: 'digitalmate-timeout', gateway: 100 }] }, error_categories: { timeout: 1 } } } : {}),
      })) };
    const points = daily.days.map((row) => ({ date: row.date, query_ms: 100, query_count: 100,
      success_count: 100, failed_queries: 0, unknown_status_count: 0, duration_sum_ms: 10000,
      duration_sample_count: 100, benchmark_models: [{ model: 'test/model', call_count: 100,
        success_count: 100, duration_sum_ms: 10000, duration_sample_count: 100 }] }));
    const archiveKey = 'a'.repeat(64);
    const snapshots = {
      'production-model-history.json': { metrics_version: 7,
        cases: { PROD: { title: 'Test model', points } }, complete_through: '2026-11-05' },
      'env-request-latency-daily.json': daily,
      'snapshot.json': { releases: releases.map((row) => ({ tag: row.tag, published_at: new Date(row.published).toISOString() })) },
      'index.json': { schema: 'digitalmate/trajectory-archive@1', generated_at: '2026-11-06T01:00:00Z',
        coverage: {}, traces: [{ key: archiveKey, request_id: 'archived-request', start_time: '2026-11-05T05:00:00Z',
          lifecycle: 'final', span_count: 1, duration_ms: 100, pages: [`traces/${archiveKey}/0.json`] }] },
      '0.json': { schema: 'digitalmate/trajectory-archive@1', key: archiveKey, offset: 0, total: 1,
        spans: [{ label: '任务执行', lifecycle: 'final', start_ms: 0, duration_ms: 100, span_id: 'span', kind: 'phase' }] },
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
      return cards.indexOf(network[0]) === 2 && cards.indexOf(network[2]) === 4;
    }));
    const replies = page.locator('#env-latency .reply-timing-card');
    assert.equal(await replies.count(), 0);
    assert.doesNotMatch(await page.locator('#env-latency').innerText(), /暂无打点数据/);
    assert.equal(await page.locator('#env-latency [data-env-series*="reply"]').count(), 0);
    const zone = page.locator('#env-latency .hover-zone[data-date="2026-11-04"]').first();
    assert.match(await zone.getAttribute('data-tip'), /v0\.2\.0 → v0\.2\.1（当日 1 次发布）/);
    assert.match(await zone.getAttribute('data-tip'), /非请求实际部署版本/);
    assert(await page.locator('#env-latency .release-divider').count() > 0);
    assert(await page.locator('#model-trends .release-chip').count() > 0);
    assert.equal(await page.locator('.cloud-metric-summary > .stat-card').count(), 5);
    assert.doesNotMatch(await page.locator('.cloud-metric-summary').innerText(), /答案首吐字|ReAct|TPOT|暂无打点数据/);
    assert.equal(await page.locator('#env-latency [aria-label="异常请求分析"]').count(), 0);
    const analysis = page.locator('#version-analysis');
    assert.equal(await analysis.getByRole('heading', { name: '版本现网分析', exact: true }).count(), 1);
    const analysisSummary = analysis.locator(':scope > summary');
    assert.equal(await analysis.evaluate((node) => node.open), false);
    assert.equal(await page.locator('#analysis-failed').isVisible(), false);
    await analysisSummary.focus();
    await page.keyboard.press('Enter');
    assert.equal(await analysis.evaluate((node) => node.open), true);
    assert.equal(await page.locator('#analysis-failed').isVisible(), true);
    assert.equal(await page.locator('#analysis-slow').isVisible(), false);
    assert.equal(await page.locator('#analysis-all').isVisible(), false);
    assert.equal(await page.locator('#analysis-failed-latency [data-latency-stat="n"]').innerText(), '3');
    assert.equal(await page.locator('#analysis-failed-latency [data-latency-bucket]').count(), 9);
    await page.locator('#analysis-tab-failed').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#analysis-tab-slow').getAttribute('aria-selected'), 'true');
    assert.equal(await page.locator('#analysis-slow #request-traces').isVisible(), true);
    assert.equal(await page.locator('#analysis-slow-latency [data-latency-stat="n"]').innerText(), '5');
    await page.keyboard.press('End');
    assert.equal(await page.locator('#analysis-tab-all').getAttribute('aria-selected'), 'true');
    assert.equal(await page.locator('#analysis-all #request-lookup').isVisible(), true);
    assert.equal(await page.locator('#analysis-all-latency [data-latency-stat="n"]').innerText(), '5');
    await page.locator('#analysis-tab-failed').click();
    await page.locator('#analysis-failures [data-trace-id="digitalmate-timeout"]').click();
    assert.equal(await page.locator('#analysis-tab-slow').getAttribute('aria-selected'), 'true');
    assert.match(await page.locator('#trace-select option:checked').innerText(), /digitalmate-timeout/);
    await page.locator('#analysis-tab-all').click();
    await page.locator('#archive-query').fill('archived-request');
    await page.locator('#archive-form button[type="submit"]').click();
    await page.locator('#archive-list [data-archive-key]').click();
    await page.locator('#archive-detail').getByText('任务执行', { exact: true }).waitFor();
    assert.equal(await page.locator('#analysis-all-latency [data-latency-stat="n"]').innerText(), '5');
    await analysisSummary.click();
    assert.equal(await analysis.evaluate((node) => node.open), false);
    await page.locator('#floating-range-controls [data-range="1"]').click();
    assert.equal(await analysis.evaluate((node) => node.open), false);
    assert.equal(await models.evaluate((node) => node.open), false);
    assert.equal(await page.locator('#env-latency .network-card').count(), 0);
    assert.equal(await replies.count(), 0);
    assert.equal(await page.locator('#analysis-tab-all').getAttribute('aria-selected'), 'true');
    await analysisSummary.click();
    assert.equal(await page.locator('#analysis-tab-all').getAttribute('aria-selected'), 'true');
    assert.equal(await page.locator('#analysis-all-latency [data-latency-stat="n"]').count(), 0);
    await page.locator('#archive-detail').filter({ hasText: '任务执行' }).waitFor({ state: 'hidden' });
    await page.locator('#floating-range-controls [data-range="7"]').click();
    assert.equal(await page.locator('#analysis-all-latency [data-latency-stat="n"]').innerText(), '5');
    assert.equal(await page.locator('#env-latency .network-card').count(), 3);
    await models.locator(':scope > summary').click();
    await models.locator('#model-trends .release-chip').first().waitFor();
    assert.equal(await models.locator('#model-summary').isVisible(), true);
    assert.equal(await models.locator('#model-summary > .stat-card').count(), 2);
    assert.doesNotMatch(await models.locator('#model-summary').innerText(), /TTFT|TPOT/);
    await models.locator('#model-controls [data-range="30"]').click();
    assert.equal(await models.evaluate((node) => node.open), true);
    await page.evaluate(() => refreshProductionSnapshots());
    assert.equal(await models.evaluate((node) => node.open), true);
    assert.equal(await analysis.evaluate((node) => node.open), true);
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
    assert.equal(await embedded.locator('#version-analysis').evaluate((node) => node.open), false);
    await embedded.locator('#version-analysis > summary').click();
    await page.waitForFunction((height) => document.querySelector('iframe').getBoundingClientRect().height > height + 100, foldedHeight);
    await page.waitForFunction(matchesContentHeight);
    await embedded.locator('#version-analysis > summary').click();
    await page.waitForFunction((height) => document.querySelector('iframe').getBoundingClientRect().height < height + 2, foldedHeight);
    await page.waitForFunction(matchesContentHeight);
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
