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
 extract('function mergeMetricHistogram(', '// Each bucket covers') +
 extract('const LATENCY_DISTRIBUTION_BUCKETS =', 'function setAnalysisView(') +
 extract('function refineCharts(', 'function hideChartHover(') +
 ';globalThis.label=pointLabel;globalThis.chart=envTrendChart;globalThis.summary=summarizeCloudAnomalies;globalThis.render=envAnomaly;globalThis.failures=envFailureAnalysis;globalThis.refine=refineCharts;globalThis.aggregateLatency=aggregateAnalysisLatency;globalThis.latencyHtml=analysisLatencyHtml;', ctx);

const latencyMetric = (value, n = 1) => ({ n, sum: value * n,
  histogram: n ? [[value === 0 ? -1001 : Math.floor(Math.log(value) / Math.log(1.02)), n]] : [] });
const latencyDay = (date, all, failed, slow) => ({ date, request_latency_distributions: {
  schema: 'digitalmate/request-latency-distributions@1', all, failed, slow,
  slow_policy: { ratio: .1, minimum: 10 },
} });

test('analysis latency pools each population and histogram weights instead of daily percentiles', () => {
  const days = [
    latencyDay('2026-11-01', latencyMetric(100), latencyMetric(0), latencyMetric(100)),
    latencyDay('2026-11-02', latencyMetric(6000, 9), latencyMetric(20000, 2), latencyMetric(6000, 3)),
  ];
  const all = ctx.aggregateLatency(days, 'all');
  assert.equal(all.n, 10);
  assert.equal(all.mean, 5410);
  assert.equal(all.counts[0], 1);
  assert.equal(all.counts[2], 9);
  assert(Math.abs(all.p50 / 6000 - 1) < .011);
  assert(Math.abs(all.p95 / 6000 - 1) < .011);
  assert.equal(ctx.aggregateLatency(days, 'failed').n, 3);
  assert.equal(ctx.aggregateLatency(days, 'slow').n, 4);
  for (const mode of ['all', 'failed', 'slow']) {
    const stats = ctx.aggregateLatency(days, mode);
    assert.equal(stats.counts.reduce((a, b) => a + b, 0), stats.n);
    const html = ctx.latencyHtml(days, mode);
    assert.equal([...html.matchAll(/data-count="(\d+)"/g)].reduce((sum, match) => sum + Number(match[1]), 0), stats.n);
    assert.doesNotMatch(html, /NaN|undefined|Infinity/);
  }
});

test('missing distributions remain unknown, explicit zero remains zero, and legacy all-request histograms still work', () => {
  const legacy = [{ date: '2026-11-01', gateway: latencyMetric(500, 5) }];
  assert.equal(ctx.aggregateLatency(legacy, 'all').n, 5);
  const missing = ctx.latencyHtml(legacy, 'failed');
  assert.equal(missing, '');
  const known = [latencyDay('2026-11-01', latencyMetric(500), latencyMetric(0, 0), latencyMetric(500))];
  const empty = ctx.latencyHtml(known, 'failed');
  assert.equal(ctx.aggregateLatency(known, 'failed').n, 0);
  assert.equal(empty, '');
  const zero = latencyDay('2026-11-02', { n: 0, sum: 999999, histogram: [] }, null, null);
  assert.equal(ctx.aggregateLatency([...legacy, zero], 'all').mean, 500);
});

test('invalid histogram counts cannot contaminate valid days, and incompatible slow policies stay unavailable', () => {
  const invalid = latencyDay('2026-11-01', { ...latencyMetric(1, 3), histogram: [[0, 2]] }, null, null);
  const valid = latencyDay('2026-11-02', latencyMetric(6000), latencyMetric(6000), latencyMetric(6000));
  valid.request_latency_distributions.partial = true;
  valid.request_latency_distributions.retained = true;
  const stats = ctx.aggregateLatency([invalid, valid], 'all');
  assert.equal(stats.n, 1);
  assert.equal(stats.coveredDays, 1);
  assert.equal(stats.partial, true);
  assert.equal(stats.retained, true);
  assert.equal(stats.counts.reduce((a, b) => a + b, 0), 1);
  assert.match(ctx.latencyHtml([invalid, valid], 'all'), /另有 1 个日志日缺少可用分布/);
  valid.request_latency_distributions.slow_policy.ratio = .2;
  assert.equal(ctx.aggregateLatency([valid], 'slow').coveredDays, 0);
});

test('failure analysis pools explicit exceptions and timeouts without treating cancellations as failures', () => {
  const html = ctx.failures({ days: [
    { date: '2026-11-04', gateway_stream: { exception: 2, observed: 12, cancelled: 40, unknown: 8 },
      anomaly: { timeouts: { records: 3, identified: 2, requests: [{ request_id: 'timeout-1', gateway: 100 }] }, error_categories: { timeout: 3 } } },
    { date: '2026-11-05', gateway_stream: { exception: 1, observed: 8, cancelled: 200 },
      anomaly: { timeouts: { records: 2, identified: 1 }, error_categories: { rate_limit: 2 } } },
    { date: '2026-11-06' },
  ] });
  assert.match(html, /data-failure-metric="stream_exception"[\s\S]*?3 \/ 20/);
  assert.match(html, /data-failure-metric="timeout_records"[\s\S]*?>5<\/div>/);
  assert.match(html, /data-failure-metric="timeout_requests"[\s\S]*?>3<\/div>/);
  assert.match(html, /覆盖 2 \/ 3 天/);
  assert.match(html, /data-trace-id="timeout-1"/);
  assert.doesNotMatch(html, /243 \/|240 \/|251/);
});

test('missing failure telemetry stays unknown while explicit zero counts remain zero', () => {
  const missing = ctx.failures({ days: [{ date: '2026-11-05' }] });
  assert.match(missing, /data-failure-metric="stream_exception"[\s\S]*?— \/ —/);
  assert.match(missing, /data-failure-metric="timeout_records"[\s\S]*?>—<\/div>/);
  assert.match(missing, /失败原因尚未采集/);
  const zero = ctx.failures({ days: [{ date: '2026-11-05', gateway_stream: { exception: 0, observed: 4 },
    anomaly: { timeouts: { records: 0, identified: 0 }, error_categories: {} } }] });
  assert.match(zero, /data-failure-metric="stream_exception"[\s\S]*?0 \/ 4/);
  assert.match(zero, /data-failure-metric="timeout_records"[\s\S]*?>0<\/div>/);
  assert.match(zero, /所选范围没有已记录的失败原因/);
});

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
