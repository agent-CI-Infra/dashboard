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
  + extract('function questionCount(point)', 'function failureSeries(')
  + '; globalThis.acc=newMetricAccumulator; globalThis.merge=mergeMetricRow; globalThis.finish=finalizeMetricAccumulator; globalThis.inRange=inRange; globalThis.completeDays=completeDays; globalThis.sameClock=sameClockComparison; globalThis.benchRow=benchmarkRowToMetric; globalThis.pointRow=pointMetricRow;',
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
