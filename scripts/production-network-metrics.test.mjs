import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

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

test('all three network cards stay visible without valid samples', () => {
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
