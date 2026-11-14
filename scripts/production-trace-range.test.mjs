import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
function extract(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `missing ${start}`);
  return source.slice(a, b);
}
const rangeCode = extract('function addDays(date, days)', 'function weekSeries(days)')
  + extract('function inRange(days, rangeDays, endDate = null)', 'function latestModelDate(history)')
  + extract('function cloudScopedDays(', 'function renderEnv()');

test('slow traces follow the visible complete-day window, with today included in All', () => {
  const context = vm.createContext({});
  vm.runInContext(rangeCode + ';globalThis.scope=cloudTraceScopedDays;globalThis.label=cloudTraceRangeLabel;', context);
  const rows = ['2026-10-14', '2026-10-24', '2026-11-06', '2026-11-07', '2026-11-13', '2026-11-14', '2026-11-15'].map(date => ({date}));
  const dates = (range) => Array.from(context.scope(rows, range, '2026-11-14', '2026-11-13'), row => row.date);
  assert.deepEqual(dates(1), ['2026-11-14']);
  assert.deepEqual(dates(7), ['2026-11-07', '2026-11-13']);
  assert.deepEqual(dates(30), ['2026-10-24', '2026-11-06', '2026-11-07', '2026-11-13']);
  assert.deepEqual(dates(0), ['2026-10-14', '2026-10-24', '2026-11-06', '2026-11-07', '2026-11-13', '2026-11-14']);
  assert.match(context.label(7, '2026-11-14', '2026-11-13'), /2026-11-07 至 2026-11-13/);
  assert.match(context.label(0, '2026-11-14', '2026-11-13'), /含 2026-11-14/);
});

test('request list displays every matching row and drops an out-of-range selection', () => {
  const context = vm.createContext({});
  const setup = `const state={rangeDays:7,history:{},traceTouched:true,traceId:'cloud:today',traceDisplay:'events',traceFilter:'tools'};
    const nodes={'trace-picker':{},'trace-result':{}}; const $=(id)=>nodes[id];
    const todayShanghai=()=> '2026-11-14'; const historyCompleteThrough=()=> '2026-11-13';
    const fmtCount=String, fmtDateTime=String, fmtDuration=String, esc=String;
    const traceHtml=(item)=>item ? item.request_id : 'empty';`;
  vm.runInContext(setup + rangeCode
    + extract('function slowTraceCandidates(doc)', 'function traceRows(')
    + extract('function availableTraceCandidates(', "$('request-traces').addEventListener('change'")
    + ';globalThis.render=renderRequestTraces;globalThis.nodes=nodes;globalThis.state=state;', context);
  const yesterday = Array.from({length: 225}, (_, i) => ({request_id:`yesterday-${i}`,gateway:1000+i}));
  const doc = {cloud_trace_collection:{schema:'digitalmate/cloud-traces@1',days:[
    {date:'2026-11-13',anomaly:{latency:{slow_requests:yesterday}}},
    {date:'2026-11-14',anomaly:{latency:{slow_requests:[{request_id:'today',gateway:2000}]}}},
  ]}};
  context.render(doc);
  assert.equal(context.state.traceCandidates.length, 225);
  assert.equal((context.nodes['trace-picker'].innerHTML.match(/data-cloud-select=/g) || []).length, 225);
  assert.doesNotMatch(context.nodes['trace-picker'].innerHTML, /cloud:today/);
  assert.equal(context.state.traceId, 'cloud:yesterday-224');
  assert.equal(context.state.traceDisplay, 'native');
  context.state.rangeDays = 0;
  context.render(doc);
  assert.equal(context.state.traceCandidates.length, 226);
  assert.match(context.nodes['trace-picker'].innerHTML, /today/);
  assert.match(context.nodes['trace-picker'].innerHTML, /含 2026-11-14/);
});

test('fallback daily snapshots are filtered with the same range', () => {
  const context = vm.createContext({});
  const setup = `const state={rangeDays:1,history:{}}; const nodes={'trace-picker':{},'trace-result':{}}; const $=(id)=>nodes[id];
    const todayShanghai=()=> '2026-11-14'; const historyCompleteThrough=()=> '2026-11-13';
    const fmtCount=String, fmtDateTime=String, fmtDuration=String, esc=String, traceHtml=()=>'';`;
  vm.runInContext(setup + rangeCode
    + extract('function slowTraceCandidates(doc)', 'function traceRows(')
    + extract('function availableTraceCandidates(', "$('request-traces').addEventListener('change'")
    + ';globalThis.render=renderRequestTraces;globalThis.state=state;', context);
  const doc={days:[
    {date:'2026-11-13',anomaly:{latency:{slow_requests:[{request_id:'old',gateway:20}]}}},
    {date:'2026-11-14',anomaly:{latency:{slow_requests:[{request_id:'new',gateway:10}]}}},
  ]};
  context.render(doc);
  assert.deepEqual(Array.from(context.state.traceCandidates, row=>row.request_id), ['new']);
  context.state.localCloudTraces = {schema:'digitalmate/cloud-traces@1'};
  context.render(doc);
  assert.deepEqual(Array.from(context.state.traceCandidates, row=>row.request_id), ['new']);
});
