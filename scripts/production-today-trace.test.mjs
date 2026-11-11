import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const code = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
function extract(a,b) { const x=code.indexOf(a), y=code.indexOf(b,x); assert(x>=0 && y>x); return code.slice(x,y); }

test('cloud trace source identifies Kubernetes when present', () => {
  const c=vm.createContext({});
  vm.runInContext(extract('function cloudCollectionLabel(', 'function renderRequestTraces(')+';globalThis.label=cloudCollectionLabel;', c);
  assert.equal(c.label({collection_source:'kubernetes_shared_workspace'}), 'Kubernetes 生产工作区');
  assert.equal(c.label({}), '现网');
});

test('Shanghai calendar uses local day at the UTC day boundary', () => {
  const c=vm.createContext({});
  vm.runInContext(extract('function todayShanghai(', 'function historyLatestDate(')+'; globalThis.today=todayShanghai;',c);
  assert.equal(c.today(new Date('2026-11-09T16:01:00Z')), '2026-11-10');
});

test('cloud range keeps partial days and uses the current calendar window', () => {
  const c=vm.createContext({});
  vm.runInContext(extract('function addDays(date, days)', 'function weekSeries(days)')+
    extract('function inRange(days, rangeDays, endDate = null)', 'function latestModelDate(history)')+
    extract('function cloudScopedDays(', 'function renderEnv()')+';globalThis.scope=cloudScopedDays;',c);
  const rows=[{date:'2026-11-04',partial:false},{date:'2026-11-10',partial:true},{date:'2026-11-11',partial:true}];
  assert.deepEqual(Array.from(c.scope(rows,7,'2026-11-11'),r=>r.date),['2026-11-10','2026-11-11']);
  assert.deepEqual(Array.from(c.scope(rows,1,'2026-11-11'),r=>r.date),['2026-11-11']);
});

test('today selection contains only that day and never falls back to yesterday', () => {
  const c=vm.createContext({});
  vm.runInContext(`const state={history:{},model:'m',rangeDays:1}; const todayShanghai=()=> '2026-11-10';
    const modelSeries=()=>[{date:'2026-11-09',partial:false},{date:'2026-11-10',partial:true}];`+
    extract('function addDays(date, days)', 'function weekSeries(days)')+
    extract('function inRange(days, rangeDays, endDate = null)', 'function latestModelDate(history)')+
    extract('function currentModelDays()', 'function renderTrends()')+
    ';globalThis.days=currentModelDays;',c);
  assert.deepEqual(Array.from(c.days(),r=>r.date),['2026-11-10']);
});

test('today selection stays empty when only yesterday is present', () => {
  const c=vm.createContext({});
  vm.runInContext(`const state={history:{},model:'m',rangeDays:1}; const todayShanghai=()=> '2026-11-10';
    const modelSeries=()=>[{date:'2026-11-09',partial:false}];`+
    extract('function addDays(date, days)', 'function weekSeries(days)')+
    extract('function inRange(days, rangeDays, endDate = null)', 'function latestModelDate(history)')+
    extract('function currentModelDays()', 'function renderTrends()')+
    ';globalThis.days=currentModelDays;',c);
  assert.equal(c.days().length,0);
});

test('trace uses measured timings and rejects impossible tool spans',()=>{
  const c=vm.createContext({});
  vm.runInContext(extract('function slowTraceCandidates(', 'function renderRequestTraces(')+';globalThis.rows=traceRows; globalThis.candidates=slowTraceCandidates;',c);
  const item={gateway:100,first_token:10,answer_first:80,request_id:'req',tool_events:[{name:'bash',start_ms:12,duration_ms:50,status:'error'}, {name:'private',start_ms:99,duration_ms:3}]};
  const rows=c.rows(item);
  assert.equal(rows.length,5);
  assert.equal(rows[2].duration,70);
  assert.equal(rows[4].kind,'error');
  assert.equal(c.candidates({days:[{date:'2026-11-10',anomaly:{latency:{slow_requests:[item]},timeouts:{requests:[item]}}}]}).length,1);
  assert.equal(c.rows({gateway:100,first_token:80,answer_first:20}).some(r=>r.kind==='react'),false);
});

test('both slow request lists are collapsed by default',()=>{
  const matches=code.match(/<details class="slow-request-details"><summary class="anomaly-subhead">超时与最慢 5% 请求<\/summary>/g) || [];
  assert.equal(matches.length,2);
});

test('cloud picker prefers the snapshot with the latest data day over a freshly republished old snapshot', () => {
 const c=vm.createContext({});
 vm.runInContext(extract('function newestEnvDaily', 'function renderEnv()')+';globalThis.pick=newestEnvDaily;', c);
 const schema='digitalmate/env-request-latency-daily@1';
 const recentData={schema,env:'adx-jiuwen',generated_at:'2026-11-09T16:34:43Z',days:[{date:'2026-11-10'}],label:'has-today'};
 const newerDocument={schema,env:'adx-jiuwen',generated_at:'2026-11-10T05:05:21Z',days:[{date:'2026-11-08'}],label:'old-data'};
 assert.equal(c.pick([newerDocument,recentData]).label,'has-today');
 assert.equal(c.pick([recentData,newerDocument]).label,'has-today');
});

test('production cloud picker rejects test and unlabelled environments', () => {
 const c=vm.createContext({});
 vm.runInContext(extract('function newestEnvDaily', 'function renderEnv()')+';globalThis.pick=newestEnvDaily;', c);
 const schema='digitalmate/env-request-latency-daily@1';
 const prod={schema,env:'adx-jiuwen',generated_at:'2026-11-09T00:00:00Z',days:[{date:'2026-11-09'}]};
 const testEnv={schema,env:'adx-jiuwen-test-0',generated_at:'2026-11-10T00:00:00Z',days:[{date:'2026-11-10'}]};
 assert.equal(c.pick([testEnv,prod]),prod);
 assert.equal(c.pick([testEnv,{schema}]),null);
});
