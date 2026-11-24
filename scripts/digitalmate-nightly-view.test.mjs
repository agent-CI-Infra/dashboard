import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// The nightly view switcher and the snapshot loading behavior live in the board
// bundle as plain script; these tests pin the wiring so a refactor cannot
// silently turn the picker back into a copy-only helper or re-enable no-store.
const app = readFileSync(new URL('../single-repo/static/app.js', import.meta.url), 'utf8');
const boardLocal = readFileSync(new URL('../single-repo/static/board-local.js', import.meta.url), 'utf8');

test('picking a nightly applies the view and persists it', () => {
  assert.ok(app.includes('perfRunId: loadPerfRun()'), 'the pinned run survives reloads from localStorage');
  assert.ok(app.includes("const loadPerfRun = () => { try { const value = localStorage.getItem(PERF_RUN_KEY) || ''; return /^\\d+$/.test(value) ? value : ''; } catch (e) { return ''; } };"), 'only numeric run ids load');
  assert.ok(app.includes('function applyNightlyView(runId)'), 'the apply helper exists');
  assert.ok(app.includes("localStorage.setItem(PERF_RUN_KEY, value)"), 'the selection persists');
  assert.ok(app.includes("localStorage.removeItem(PERF_RUN_KEY)"), 'resetting to latest clears the key');
  assert.ok(app.includes("ev.target.id !== 'nightly-run-id'"), 'selecting from the dropdown switches immediately');
  assert.ok(app.includes('[data-nightly-view]') && app.includes('[data-nightly-latest]'), 'manual input and reset buttons are wired');
});

test('case performance reads the pinned run instead of the latest point', () => {
  assert.ok(app.includes("const viewRunId = String(STATE.perfRunId || '')"), 'the perf section reads the pinned run');
  assert.ok(app.includes('const basis = (item) => (viewRunId ? runPoint(item, viewRunId) : item.latest);'), 'per-case reads go through the basis point');
  assert.ok(app.includes('const statuses = scoped.map((c) => basis(c)?.status);'), 'KPIs use the basis run');
  assert.ok(app.includes('`查看 run ${viewRunId}（${n(scoped.length)} 个用例）`'), 'the header names the viewed run');
  assert.ok(app.includes('run ${esc(viewRunId)} 没有已采集的用例性能数据'), 'uncollected runs say so instead of showing stale data');
  assert.ok(app.includes('perfHistoryTable(points, viewRunId)'), 'the history table knows the viewed run');
  assert.ok(app.includes("viewRunId && dmSameRun(p.run_id, viewRunId) ? ' class=\"perf-view-run\"'", 'the viewed run row is highlighted'));
});

test('real e2e scores switch to the pinned run with hidden missing cases', () => {
  assert.ok(app.includes('const basis = item.points.find((p) => dmSameRun(p.run_id, viewRunId));'), 'score cases pin to the viewed run');
  assert.ok(app.includes('const latest = item.basis || item.points.at(-1);'), 'per-case values read the basis point');
  assert.ok(app.includes("run ${esc(viewRunId)} 没有已采集的评分数据"), 'runs without score data explain themselves');
  assert.ok(app.includes("${item.basis ? '该轮' : '最新'}"), 'case headers say which run the shown state is from');
});

test('the run matcher accepts both bare ids and nightly-<id>-<attempt> names', () => {
  const start = app.indexOf('const dmSameRun = ');
  const end = app.indexOf('};', start) + 2;
  assert.ok(start >= 0 && end > start, 'the matcher is defined');
  const context = vm.createContext({});
  vm.runInContext(app.slice(start, end) + ';globalThis.dmSameRun = dmSameRun;', context);
  const same = context.dmSameRun;
  assert.equal(same('42919555343', '42919555343'), true);
  assert.equal(same('nightly-42919555343-1', '42919555343'), true);
  assert.equal(same('nightly-42919555343-12', '42919555343'), true);
  assert.equal(same('142919553431', '42919555343'), false);
  assert.equal(same('nightly-42919555343-1', ''), false);
  assert.equal(same(undefined, '42919555343'), false);
});

test('snapshot and data files load through the browser cache, not no-store', () => {
  assert.ok(!app.includes("cache: 'no-store'"), 'no fetch may bypass the HTTP cache');
  assert.ok(!app.includes('?ts=' + 'Date.now()'), 'per-minute cache busting is gone');
  assert.ok(app.includes("const bucket = Math.floor(Date.now()/6e5);"), 'data files share the snapshot 10-minute bucket');
  for (const file of ['data/e2e/index.json', 'data/nightly-runs.json', 'data/e2e-performance/history.json', 'data/st.json']) {
    assert.ok(app.includes(`'./${file}?m='+bucket`), `${file} uses the shared bucket`);
  }
});

test('drawer bodies load lazily from data/details.json', () => {
  assert.ok(boardLocal.includes("new URL('data/details.json',document.baseURI)"), 'the first drawer open fetches the details file');
  assert.ok(boardLocal.includes("if(!Object.hasOwn(snapshot.details,key))"), 'later opens reuse the cached file');
  assert.ok(boardLocal.includes("catch(e){/* keep the empty cache; the fallback text still explains */}"), 'a missing file keeps the drawer usable');
});

test('Orbit UI and cloud trace helpers are removed', () => {
  assert.ok(!/Orbit|perfCloud|PERF_PHASE_NAME/.test(app));
});

test('profiling aggregates only valid queries from completed cases', () => {
  const start = app.indexOf('  const perfIsCompleted = ');
  const end = app.indexOf('\n  function perfHistoryTable', start);
  assert.ok(start >= 0 && end > start);
  const context = vm.createContext({});
  vm.runInContext(`${app.slice(start, end)};globalThis.stats = perfQueryStats;globalThis.mean = perfWeightedMean;globalThis.median = perfWeightedMedian;`, context);
  const current = { status: 'COMPLETED', query_count: 3, query_ms: 400, queries: [
    { outcome: 'completed', duration_ms: 100 },
    { outcome: 'ok', duration_ms: 300 },
    { outcome: 'failed', duration_ms: 900 },
  ] };
  const historical = { status: 'COMPLETED', query_count: 3, query_ms: 500 };
  const failed = { status: 'FAILED', query_count: 1, query_ms: 1 };
  assert.deepEqual({ ...context.stats(current) }, { count: 2, total: 400, mean: 200 });
  assert.equal(context.mean([current, historical, failed]), 380);
  assert.equal(context.median([current, historical, failed]), 500);
  assert.equal(context.mean([failed, { status: 'COMPLETED', query_count: 0, query_ms: null }]), null);
  assert.equal(context.median([{ status: 'COMPLETED', query_count: 2, query_ms: null }]), null);
});

test('E2E daily history uses Beijing days and the last run of each day', () => {
  const dayStart = app.indexOf('  const scoreDay = ');
  const dayEnd = app.indexOf('\n  const metricKey', dayStart);
  const dailyStart = app.indexOf('  function e2eDailyHistory');
  const dailyEnd = app.indexOf('\n  function e2eDailySummary', dailyStart);
  const context = vm.createContext({});
  vm.runInContext(`${app.slice(dayStart, dayEnd)}${app.slice(dailyStart, dailyEnd)};globalThis.daily = e2eDailyHistory;`, context);
  const history = [
    { record: { created_at: '2026-11-23T15:30:00Z' }, c: { status: 'failed' } },
    { record: { created_at: '2026-11-23T16:30:00Z' }, c: { status: 'failed' } },
    { record: { created_at: '2026-11-23T18:00:00Z' }, c: { status: 'passed' } },
  ];
  const daily = context.daily(history);
  assert.equal(daily.length, 2);
  assert.equal(daily[0].day, '2026-11-23');
  assert.equal(daily[1].day, '2026-11-24');
  assert.equal(daily[1].runs, 2);
  assert.equal(daily[1].c.status, 'passed');
});
