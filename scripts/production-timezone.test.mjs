import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const code = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
function extract(start, end) {
  const first = code.indexOf(start), last = code.indexOf(end, first);
  assert.ok(first >= 0 && last > first);
  return code.slice(first, last);
}

test('production timestamps and day selection use China time even if an extension overrides Intl', () => {
  const context = vm.createContext({});
  vm.runInContext(`
    const NativeDateTimeFormat = Intl.DateTimeFormat;
    Intl.DateTimeFormat = function (locales, options = {}) {
      Object.assign(options, { timeZone: 'Etc/GMT' });
      return new NativeDateTimeFormat(locales, options);
    };
    const NativeDate = Date;
    Date = class extends NativeDate {
      getHours() { return this.getUTCHours(); }
      getDate() { return this.getUTCDate(); }
      getMonth() { return this.getUTCMonth(); }
      getFullYear() { return this.getUTCFullYear(); }
    };
  ` + extract('function fmtDateTime(', 'function niceMax(')
    + extract('function todayShanghai(', 'function historyLatestDate(')
    + extract('function shanghaiHour(', 'function windowAverage(')
    + ';globalThis.check={fmtDateTime,todayShanghai,shanghaiHour};', context);
  const { fmtDateTime, todayShanghai, shanghaiHour } = context.check;
  assert.equal(fmtDateTime('2026-11-11T16:02:54.988Z'), '11-12 00:02');
  assert.equal(fmtDateTime('2026-11-11T18:59:22.203Z'), '11-12 02:59');
  assert.equal(fmtDateTime('2026-11-11T23:30:53Z'), '11-12 07:30');
  assert.equal(todayShanghai(new Date('2026-11-11T16:00:00Z')), '2026-11-12');
  assert.equal(todayShanghai(new Date('2026-11-11T15:59:59Z')), '2026-11-11');
  assert.equal(shanghaiHour('2026-11-11T18:59:22Z'), 2);
  assert.equal(shanghaiHour('bad date'), null);
  assert.equal(fmtDateTime('bad date'), 'bad date');
});
