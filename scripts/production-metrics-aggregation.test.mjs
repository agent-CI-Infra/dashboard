import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const code = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
function extract(start, end) { const a=code.indexOf(start), b=code.indexOf(end,a); if(a<0||b<0) throw Error(`missing function: ${start}`); return code.slice(a,b); }
const ctx = vm.createContext({});
vm.runInContext(
  extract('function newTokenTally()', 'function modelSeries(history, model)')
  + extract('function addDays(date, days)', 'function weekSeries(days)')
  + extract('function inRange(days, rangeDays, endDate = null)', 'function latestModelDate(history)')
  + extract('function historyLatestDate(history)', 'function recentCallsForDays(days, endDate')
  + extract('function hoursUntil(hours, cutoffHour)', 'function sortedCases()')
  + extract('function pointMetricRow(point)', 'function suiteDayStats(')
  + extract('function suiteDayStats(history)', 'function pointOn(')
  + extract('function questionCount(point)', 'function failureSeries(')
  + extract('function requireCurrentMetricsVersion(history)', 'async function load()')
  + '; globalThis.acc=newMetricAccumulator; globalThis.merge=mergeMetricRow; globalThis.finish=finalizeMetricAccumulator; globalThis.inRange=inRange; globalThis.completeDays=completeDays; globalThis.sameClock=sameClockComparison; globalThis.benchRow=benchmarkRowToMetric; globalThis.pointRow=pointMetricRow; globalThis.suite=suiteDayStats; globalThis.requireVersion=requireCurrentMetricsVersion;',
  ctx,
);

test('aggregate P95 unavailable if a contributing source lacks P95', () => {
  const a=ctx.acc();ctx.merge(a,{calls:20,duration_samples:20,p95_ms:100});ctx.merge(a,{calls:2,duration_samples:2,p95_ms:null});assert.equal(ctx.finish(a).p95_ms,null);
});

test('range uses the same supplied latest date even when model data is stale', () => {
  const rows=[{date:'2026-10-25'},{date:'2026-10-31'}];
  assert.deepEqual(Array.from(ctx.inRange(rows,7,'2026-11-03'), x=>x.date),['2026-10-31']);
});

test('overall mean is weighted by call volume, not an average of daily means', () => {
  const a = ctx.acc();
  ctx.merge(a, {calls: 100, duration_sum_ms: 100000, duration_samples: 100, success_count: 100});
  ctx.merge(a, {calls: 1, duration_sum_ms: 100000, duration_samples: 1, success_count: 1});
  const result = ctx.finish(a);
  assert.equal(result.duration_ms, 200000 / 101);
  assert.notEqual(result.duration_ms, (1000 + 100000) / 2);
  assert.equal(result.success_count, 101);
});

test('strict and estimated TTFT stay separate and tokens carry coverage', () => {
  const a = ctx.acc();
  ctx.merge(a, {
    calls: 4, success_count: 4, duration_sum_ms: 2000, duration_samples: 4,
    ttft_ms: 100, ttft_samples: 2,
    ttft_estimated_ms: 900, ttft_estimated_samples: 1,
    tpot_ms: 12, tpot_samples: 2,
    total_tokens: 20, input_tokens: 12, output_tokens: 8, token_sample_count: 2,
  });
  const result = ctx.finish(a);
  assert.equal(result.ttft_ms, 100);
  assert.equal(result.ttft_estimated_ms, 900);
  assert.equal(result.ttft_estimated_samples, 1);
  assert.equal(result.tpot_ms, 12);
  assert.equal(result.token_coverage, 0.5);
  assert.equal(result.total_per_request, 10);
});

test('failed-attempt tokens are reported separately from successful tokens', () => {
  const a = ctx.acc();
  ctx.merge(a, {
    calls: 2, success_count: 1, duration_sum_ms: 1000, duration_samples: 1,
    total_tokens: 14, token_sample_count: 1,
    failed_total_tokens: 150, failed_input_tokens: 100, failed_output_tokens: 50, failed_token_sample_count: 1,
  });
  const result = ctx.finish(a);
  assert.equal(result.total_tokens, 14);
  assert.equal(result.failed_total_tokens, 150);
  assert.equal(result.token_coverage, 1);
});

test('legacy snapshot mean is reconstructed into an exact sum', () => {
  const row = ctx.benchRow(
    {call_count: 10, duration_sample_count: 10, duration_p95_ms: 500},
    {query_ms: 100, query_count: 10},
  );
  assert.equal(row.duration_sum_ms, 1000);
  assert.equal(row.success_count, null);
  const a = ctx.acc();
  ctx.merge(a, row);
  const result = ctx.finish(a);
  assert.equal(result.duration_ms, 100);
  assert.equal(result.success_count, 10);
  assert.equal(result.p50_ms, null);
  assert.equal(result.p95_ms, 500);
});

test('a v2 model row without its own sum falls back to a single-model point mean', () => {
  const row = ctx.benchRow(
    {call_count: 10, success_count: 10, duration_sample_count: 10, duration_p95_ms: 500},
    {query_ms: 100, query_count: 10, benchmark_models: [{}]},
  );
  assert.equal(row.duration_sum_ms, 1000);
  const ambiguous = ctx.benchRow(
    {call_count: 10, success_count: 10, duration_sample_count: 10, duration_p95_ms: 500},
    {query_ms: 100, query_count: 10, benchmark_models: [{}, {}]},
  );
  assert.equal(ambiguous.duration_sum_ms, null);
});

test('a v2 day with no successful call contributes no duration sample', () => {
  // duration_sum_ms is present but null: the call failed, so there is no
  // successful sample to average and no percentile to invalidate.
  const row = ctx.pointRow({
    date: '2026-11-02', query_count: 1, success_count: 0, failed_queries: 1,
    duration_sum_ms: null, duration_sample_count: 0,
    duration_p50_ms: null, duration_p95_ms: null, query_ms: 1500,
  });
  assert.equal(row.duration_samples, 0);
  assert.equal(row.duration_sum_ms, null);
  const acc = ctx.acc();
  ctx.merge(acc, row);
  const result = ctx.finish(acc);
  assert.equal(result.calls, 1);
  assert.equal(result.success_count, 0);
  assert.equal(result.duration_samples, 0);
  assert.equal(result.duration_ms, null);
  assert.equal(result.p95_lower_ms, null);
});

test('summary days exclude the unfinished current day', () => {
  const days = [{date: '2026-11-02'}, {date: '2026-11-03'}, {date: '2026-11-04'}];
  const history = {partial_day: '2026-11-04', complete_through: '2026-11-03'};
  assert.deepEqual(ctx.completeDays(days, history).map((row) => row.date), ['2026-11-02', '2026-11-03']);
});

test('same-clock comparison ignores hours that have not elapsed', () => {
  const hours = (sum) => [
    {hour: 10, calls: 1, success_count: 1, duration_sum_ms: sum, duration_sample_count: 1},
    {hour: 18, calls: 9, success_count: 9, duration_sum_ms: 90000, duration_sample_count: 9},
  ];
  const history = {
    partial_day: '2026-11-04',
    updated_at: '2026-11-04T10:30:00Z', // 18:30 Asia/Shanghai
    window_end: '2026-11-04T10:30:00Z',
    cases: {
      a: {points: [
        {date: '2026-11-03', hours: hours(2000)},
        {date: '2026-11-04', hours: hours(1000)},
      ]},
    },
  };
  const result = ctx.sameClock(history);
  assert.equal(result.cutoff, 18);
  assert.equal(result.today_ms, 1000);
  assert.equal(result.prior_ms, 2000);
  assert.equal(result.change_percent, -50);
});

test('same-clock cutoff follows collection end, not later publication', () => {
  const history = {
    partial_day: '2026-11-04', window_end: '2026-11-04T08:54:00Z',
    updated_at: '2026-11-04T10:30:00Z',
    cases: {a: {points: [{date:'2026-11-03', hours:[{hour:16,calls:1}]}, {date:'2026-11-04', hours:[{hour:16,calls:1}]}]}},
  };
  assert.equal(ctx.sameClock(history).cutoff, 16);
});

test('suite failure rate is unavailable if any status is unknown', () => {
  const history = {cases:{a:{points:[{date:'2026-11-03',query_count:2,success_count:1,failed_queries:0,unknown_status_count:1,duration_sum_ms:100,duration_sample_count:1}]}}};
  const [day] = ctx.suite(history);
  assert.equal(day.unknown, 1);
  assert.equal(day.failure_rate, null);
});

test('legacy and previous-version snapshots cannot be displayed as current metrics', () => {
  assert.throws(() => ctx.requireVersion({}), /版本过旧/);
  assert.throws(() => ctx.requireVersion({metrics_version: 2}), /版本过旧/);
  assert.throws(() => ctx.requireVersion({metrics_version: 3}), /版本过旧/);
  assert.doesNotThrow(() => ctx.requireVersion({metrics_version: 4}));
});

test('estimated TPOT is weighted separately from confirmed streaming', () => {
  const acc = ctx.acc();
  ctx.merge(acc, {calls: 10, success_count: 10, tpot_ms: 1, tpot_samples: 1,
    tpot_estimated_ms: 2, tpot_estimated_samples: 2, tpot_estimated_sum_ms: 4});
  ctx.merge(acc, {calls: 5, success_count: 5, tpot_estimated_ms: 4,
    tpot_estimated_samples: 1, tpot_estimated_sum_ms: 4});
  const result = ctx.finish(acc);
  assert.equal(result.tpot_ms, 1);
  assert.equal(result.tpot_estimated_samples, 3);
  assert.equal(result.tpot_estimated_ms, 8 / 3);
});

test('latency chart renders estimated TPOT on the right axis and tooltip', () => {
  const c = vm.createContext({});
  vm.runInContext(`const chartSize=()=>({width:1000,height:220}); const niceMax=(v)=>v||1;
    const fmtAxis=String; const fmtDuration=String; const fmtTpot=(v)=>v+' ms/token'; const esc=String;` +
    extract('function latencyTrendChart(points', 'function tokenTrendChart(') +
    '; globalThis.chart=latencyTrendChart;', c);
  const html = c.chart([{date:'2026-11-04',tpot_estimated_ms:2.5}], {});
  assert.match(html, /TPOT 估算（流式未知）/);
  assert.match(html, /latency-layer tpot-est/);
  assert.match(html, /axis-right/);
  assert.match(html, /2.5 ms\/token/);
});

test('model summary does not substitute an older day for the shared anchor', () => {
  const context = vm.createContext({});
  vm.runInContext(extract('function modelSummary(days, model', '// Day-over-day increases') + '; globalThis.summary=modelSummary;', context);
  const html = context.summary([{date:'2026-11-02'}], 'idle-model', {anchor:'2026-11-03'});
  assert.match(html, /最新完整日暂无数据/);
});

test('headline never substitutes an unfinished day when complete day has no data', () => {
  const context = vm.createContext({});
  vm.runInContext(`const state={history:{}}; const elements={summary:{}}; const $=(id)=>elements[id];
    const entries=()=>[]; const historyCompleteThrough=()=> '2026-11-03';
    const historyLatestDate=()=> '2026-11-04'; const suiteDayStats=()=>[{date:'2026-11-04',calls:5}];
    const newMetricAccumulator=()=>({}); const finalizeMetricAccumulator=()=>({duration_ms:null});
    const sameClockComparison=()=>null; const fmtCount=String; const fmtDuration=()=> '—';
    const p95RangeText=()=> '—'; const deltaChip=()=> ''; const esc=String;
    const MIN_RECENT_CALLS=50; const RECENT_WINDOW_DAYS=7;` +
    extract('function renderSummary()', 'function renderPerfStrip()') +
    '; renderSummary(); globalThis.html=elements.summary.innerHTML;', context);
  assert.match(context.html, /暂无完整日数据/);
  assert.doesNotMatch(context.html, /成功 5/);
});

test('seven-day range includes seven complete days plus the separate partial day', () => {
  const context = vm.createContext({});
  vm.runInContext(`const state={history:{},model:'m',rangeDays:7};
    const historyCompleteThrough=()=> '2026-11-04'; const historyPartialDay=()=> '2026-11-05';
    const modelSeries=()=>Array.from({length:8},(_,i)=>({date:'2026-'+(i===0?'10-29':i===1?'10-30':i===2?'10-31':'11-0'+(i-2))}));` +
    extract('function addDays(date, days)', 'function weekSeries(days)') +
    extract('function inRange(days, rangeDays, endDate = null)', 'function latestModelDate(history)') +
    extract('function completeDays(days, history)', '// A point carries') +
    extract('function currentModelDays()', 'function renderTrends()') +
    '; globalThis.days=currentModelDays();', context);
  assert.equal(context.days.length, 8);
  assert.equal(context.days[0].date, '2026-10-29');
  assert.equal(context.days[7].date, '2026-11-05');
});
