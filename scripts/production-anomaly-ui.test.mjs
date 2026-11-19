import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const code = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
const extract = (a, b) => { const x = code.indexOf(a), y = code.indexOf(b, x); assert(x >= 0 && y > x); return code.slice(x, y); };
const ctx = vm.createContext({});
vm.runInContext(`const esc=String,fmtCount=(v)=>Number.isFinite(v)?String(v):'—',fmtDuration=String,fmtPercent=String,fmtTpotBare=String;
 const MODEL_FAILURE_LABELS={}; const chartSize=()=>({width:1000,height:300}); const niceMax=v=>v||1;
 const fmtAxis=String; const envLatencyDailySeries=(rows,key,field)=>rows.map(row=>row[key]?.[field]??null);` +
 extract('function pointLabel(', '// A signed delta') +
 extract('function envTrendChart(', 'function envLatencyTrend(') +
 extract('const ANOMALY_METRICS =', '// Cloud call volume') +
 extract('function refineCharts(', 'function hideChartHover(') +
 ';globalThis.label=pointLabel;globalThis.chart=envTrendChart;globalThis.summary=summarizeCloudAnomalies;globalThis.render=envAnomaly;globalThis.refine=refineCharts;', ctx);

test('18 and 30 day charts retain values and change annotations', () => {
  for (const count of [18, 30]) {
    const days = Array.from({ length: count }, (_, index) => ({ date: `2026-11-${String(index + 1).padStart(2, '0')}`, gateway: { n: 1, mean: 100 + index } }));
    const html = ctx.chart({ days }, { title: 'Latency', ariaLabel: 'Latency', series: [{ key: 'mean', group: 'gateway', field: 'mean', label: 'Mean', style: 'mean' }] });
    assert.equal((html.match(/class="point-label"/g) || []).length, count);
    assert.match(html, /▲/);
    assert.equal((html.match(/data-tip=/g) || []).length, count);
  }
});

const day = (date, n, outliers, max, threshold) => ({ date, partial: true, anomaly: {
  available: true, source: 'workspace_request_logs', baseline: { from: '2026-11-01', to: '2026-11-07', days: 7 },
  metrics: { gateway: { n, outliers, rate: outliers / n, baseline_n: 100, baseline_p95: threshold } },
  latency: { n, max_ms: max, max_request: { request_id: `request-${date}` }, slow_count: 1, slow_requests: [{ request_id: `request-${date}`, gateway: max }] },
  timeouts: { count: 1, records: 2, identified: 1, requests: [] },
  cohort: { normal: n - outliers, outliers, phases: { first_token: { normal: { n: n - outliers, mean: 10 }, outlier: { n: outliers, mean: max / 2 } } } },
} });

test('expanded diagnostic samples do not enlarge the anomaly fallback tail', () => {
  const html = ctx.render({days:[{date:'2026-11-19'}], cloud_trace_collection:{days:[{
    date:'2026-11-19', anomaly:{latency:{n:16,slow_count:10,slow_requests:
      Array.from({length:10},(_,i)=>({request_id:`quota-${i}`,gateway:i+1}))}},
  }]}});
  assert.match(html,/quota-9/);
  assert.doesNotMatch(html,/quota-8/);
});

test('anomaly cards use the full selected range and weighted phase populations', () => {
  const days = [day('2026-11-10', 100, 20, 1000, 100), day('2026-11-11', 10, 1, 200, 200)];
  const out = ctx.summary(days);
  assert.equal(out.metrics.gateway.n, 110);
  assert.equal(out.metrics.gateway.outliers, 21);
  assert.equal(out.metrics.gateway.rate, 21 / 110);
  assert.equal(out.latency.max_ms, 1000);
  assert.equal(out.latency.max_date, '2026-11-10');
  assert.equal(out.timeouts.count, 2);
  assert.equal(out.cohort.phases.first_token.outlier.mean, 10100 / 21);
  assert.equal(out.metrics.gateway.baseline_p95, 100);
  assert.equal(out.metrics.gateway.baseline_p95_max, 200);
  const html = ctx.render({ days });
  assert.match(html, /21 \/ 110/);
  assert.match(html, /request-2026-11-10/);
  assert.match(html, /所选范围 2 天/);
  assert.equal(ctx.summary([days[1]]).latency.max_ms, 200);
});

test('a missing timeout population is not presented as zero', () => {
  const a = day('2026-11-10', 100, 20, 1000, 100);
  const b = day('2026-11-11', 10, 1, 200, 200);
  b.anomaly.timeouts = {};
  assert.equal(ctx.summary([a, b]).timeouts.count, null);
});

test('an unmeasured current phase retains its known historical threshold', () => {
  const value = day('2026-11-10', 100, 20, 1000, 100);
  value.anomaly.metrics.answer_first = { n: 0, baseline_n: 100, baseline_p95: 50, outliers: null };
  const metric = ctx.summary([value]).metrics.answer_first;
  assert.equal(metric.baseline_p95, 50);
  assert.equal(metric.baseline_days, 1);
  assert.equal(metric.n, 0);
  assert.equal(metric.outliers, null);
});

class Element {
  constructor(tag, className = '') { this.tag = tag; this.className = className; this.children = []; this.parentElement = null; }
  append(...nodes) { for (const node of nodes) { if (node.parentElement) node.parentElement.children.splice(node.parentElement.children.indexOf(node), 1); node.parentElement = this; this.children.push(node); } }
  replaceWith(node) { const parent = this.parentElement; parent.children.splice(parent.children.indexOf(this), 1, node); node.parentElement = parent; this.parentElement = null; }
  closest(tag) { return this.tag === tag ? this : this.parentElement?.closest(tag); }
  querySelectorAll(selector) {
    const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
    return selector === '*' ? all : all.filter(node => selector === 'p.chart-note' ? node.tag === 'p' && node.className === 'chart-note' : selector === '.trend-chart' ? node.className === 'trend-chart' : false);
  }
}

test('methodology paragraphs become one disclosure per card, preserving every paragraph', () => {
  const root = new Element('root'), first = new Element('div'), second = new Element('div');
  root.append(first, second);
  const notes = Array.from({ length: 3 }, () => new Element('p', 'chart-note'));
  first.append(...notes);
  second.append(new Element('p', 'chart-note'));
  ctx.document = { createElement: tag => new Element(tag) };
  ctx.refine(root);
  assert.equal(first.children.filter(node => node.tag === 'details').length, 1);
  assert.equal(first.children[0].children.filter(node => node.tag === 'p').length, 3);
  assert.equal(first.children[0].children[0].textContent, '统计口径');
  assert.equal(second.children.filter(node => node.tag === 'details').length, 1);
  ctx.refine(root);
  assert.equal(first.children.filter(node => node.tag === 'details').length, 1);
});
