import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const code = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
function extract(start, end) { const a=code.indexOf(start), b=code.indexOf(end,a); if(a<0||b<0) throw Error('missing function'); return code.slice(a,b); }
const ctx = vm.createContext({});
vm.runInContext(extract('function newMetricAccumulator()', 'function benchmarkRowToMetric(row)') + extract('function addDays(date, days)', 'function weekSeries(days)') + extract('function inRange(days, rangeDays, endDate = null)', 'function latestModelDate(history)') + '; globalThis.acc=newMetricAccumulator; globalThis.merge=mergeMetricRow; globalThis.finish=finalizeMetricAccumulator; globalThis.inRange=inRange;',ctx);
test('aggregate P95 unavailable if a contributing source lacks P95', () => {
 const a=ctx.acc();ctx.merge(a,{calls:20,duration_samples:20,p95_ms:100});ctx.merge(a,{calls:2,duration_samples:2,p95_ms:null});assert.equal(ctx.finish(a).p95_ms,null);
});
test('range uses the same supplied latest date even when model data is stale', () => {
 const rows=[{date:'2026-10-25'},{date:'2026-10-31'}];
 assert.deepEqual(Array.from(ctx.inRange(rows,7,'2026-11-03'), x=>x.date),['2026-10-31']);
});
