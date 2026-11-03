import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const code = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
function extract(start, end) { return code.slice(code.indexOf(start), code.indexOf(end)); }
const context = vm.createContext({});
vm.runInContext(extract('function fmtPercent(value)', 'function perfStrip(history)') + extract('function weekStart(date)', 'function addDays(date, days)') + '; globalThis.weeklyFailureRates = weeklyFailureRates;', context);
test('weekly failure rate uses total failures divided by total requests', () => {
  const rates = context.weeklyFailureRates([{date:'2026-11-02',queries:100,failed:10}, {date:'2026-11-03',queries:1,failed:1}, {date:'2026-11-09',queries:0,failed:0}]);
  assert.equal(rates.get('2026-11-02'), 11 / 101 * 100);
  assert.equal(rates.get('2026-11-09'), null);
});

