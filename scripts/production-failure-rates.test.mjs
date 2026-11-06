import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const code = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
function extract(start, end) { return code.slice(code.indexOf(start), code.indexOf(end)); }
const context = vm.createContext({});
vm.runInContext(extract('function benchmarkModelName(row)', 'function benchmarkModelNames(history)') + extract('function failureSeries(history, dates, model)', 'function fmtPercent(value)') + extract('function fmtPercent(value)', 'function perfStrip(history)') + extract('function weekStart(date)', 'function addDays(date, days)') + '; globalThis.weeklyFailureRates = weeklyFailureRates; globalThis.failureSeries = failureSeries;', context);
test('weekly failure rate uses total failures divided by total requests', () => {
  const rates = context.weeklyFailureRates([{date:'2026-11-02',queries:100,failed:10,failure_rate:10}, {date:'2026-11-03',queries:1,failed:1,failure_rate:100}, {date:'2026-11-09',queries:0,failed:0,failure_rate:null}]);
  assert.equal(rates.get('2026-11-02'), 11 / 101 * 100);
  assert.equal(rates.get('2026-11-09'), null);
});


test('only selected model contributes and ambiguous model records stay unknown', () => {
  const point = (model, total, failed) => ({date:'2026-11-03',query_count:total,failed_queries:failed,unknown_status_count:0,benchmark_models:[{model}]});
  const history = {cases:{a:{points:[point('a',10,1)]},b:{points:[point('b',20,10)]}}};
  assert.equal(context.failureSeries(history,[{date:'2026-11-03'}],'a')[0].failure_rate, 10);
  assert.equal(context.failureSeries(history,[{date:'2026-11-03'}],'b')[0].failure_rate, 50);
  history.cases.a.points[0].benchmark_models.push({model:'b'});
  assert.equal(context.failureSeries(history,[{date:'2026-11-03'}],'a')[0].failure_rate, null);
});
test('week with missing daily evidence is unavailable', () => {
  const rates = context.weeklyFailureRates([{date:'2026-11-02',queries:100,failed:10,failure_rate:10}, {date:'2026-11-03',queries:0,failed:0,failure_rate:null}]);
  assert.equal(rates.get('2026-11-02'), null);
});

test('unknown request status leaves model failure rate unavailable', () => {
 const history={cases:{a:{points:[{date:'2026-11-03',query_count:10,failed_queries:1,unknown_status_count:2,benchmark_models:[{model:'a'}]}]}}};
 assert.equal(context.failureSeries(history,[{date:'2026-11-03'}],'a')[0].failure_rate,null);
});
