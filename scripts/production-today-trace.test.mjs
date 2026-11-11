import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const code = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
function extract(a,b) { const x=code.indexOf(a), y=code.indexOf(b,x); assert(x>=0 && y>x); return code.slice(x,y); }

test('older incomplete distributions use a labelled current sample, without losing other metrics', () => {
  const c=vm.createContext({});
  vm.runInContext(extract('function mergeMetricHistogram(', '// Each bucket covers')+
    extract('function cloudDisplayRow(', 'function cloudScopedDays(')+';globalThis.display=cloudDisplayRow;',c);
  const current={n:30, gateway:{n:30,histogram:[[0,30]]},first_token:{n:25,histogram:[[0,25]]},
    answer_first_history:{n:22,mean:10}};
  const row={date:'2026-11-08',n:100,gateway:{n:100},first_token:{n:90},tokens:{total:200},current_request_metrics:current};
  const out=c.display(row);
  assert.equal(out.n,30); assert.equal(out.gateway.n,30); assert.equal(out.date,row.date);
  assert.equal(out.partial,true); assert.equal(out.current_sample_used,true);
  assert.equal(out.tokens.total,200); assert.equal(out.answer_first_history.n,22); assert.equal(row.n,100);
  const unsafe=c.display({...row,current_request_metrics:{...current,first_token:{n:0}}});
  assert.equal(unsafe.n,100); assert.equal(unsafe.first_token.n,90);
  assert.equal(unsafe.answer_first_history.n,22); assert.equal(unsafe.answer_first,undefined);
});

test('cloud cards aggregate the selected days using sample and token weights', () => {
  const c=vm.createContext({});
  vm.runInContext(extract('function mergeMetricHistogram(', '// Each bucket covers')+
    extract('function aggregateCloudMetric(', '// Headline tiles summarize')+
    ';globalThis.summary=summarizeCloudWindow;',c);
  const rows=[
    {date:'2026-11-10',gateway:{n:2,sum:200,mean:100,p50:100,p95:100,histogram:[[0,2]]},
     tpot:{n:1,tokens:10,weighted_sum:20,mean:2,p50:2,p95:2,histogram:[[35,10]]},
     tokens:{input:100,output:20,total:120,samples:{input:1,output:1,total:1}},model_usage_events:1,
     gateway_stream:{normal:1,exception:1,observed:2,population:2,cancelled:0,unknown:0}},
    {date:'2026-11-11',gateway:{n:8,sum:1600,mean:200,p50:200,p95:200,histogram:[[35,8]]},
     tpot:{n:1,tokens:30,weighted_sum:240,mean:8,p50:8,p95:8,histogram:[[105,30]]},
     tokens:{input:300,output:40,total:340,samples:{input:1,output:1,total:1}},model_usage_events:1,
     gateway_stream:{normal:8,exception:0,observed:8,population:8,cancelled:0,unknown:0}},
  ];
  const today=c.summary([rows[1]]), both=c.summary(rows);
  assert.equal(today.gateway.mean,200);
  assert.equal(both.gateway.mean,180);
  assert.equal(both.tpot.mean,6.5);
  assert.equal(both.tokens.input,400);
  assert.equal(both.tokens.output,60);
  assert.equal(both.model_usage_events,2);
  assert.equal(both.gateway_stream.exception_rate,0.1);
  assert.equal(both.gateway.approx,true);
  assert.notEqual(both.gateway.p95,null);
});

test('cloud multi-day percentiles stay unavailable if any distribution is missing', () => {
  const c=vm.createContext({});
  vm.runInContext(extract('function mergeMetricHistogram(', '// Each bucket covers')+
    extract('function aggregateCloudMetric(', '// Headline tiles summarize')+
    ';globalThis.metric=aggregateCloudMetric;',c);
  const out=c.metric([{gateway:{n:1,mean:10,p50:10,p95:10,histogram:[[0,1]]}},
                      {gateway:{n:1,mean:20,p50:20,p95:20}}], 'gateway');
  assert.equal(out.mean,15);
  assert.equal(out.p50,undefined);
  assert.equal(out.p95,undefined);
});

test('cloud summary cards render the requested date window, not its last day', () => {
  const c=vm.createContext({});
  vm.runInContext(`const NETWORK_TILES=[]; const fmtSecondsBare=String, fmtCount=String, fmtDuration=String,
    fmtTpotBare=String, fmtPercent=String, fmtDateTime=String, esc=String, trimNumberZeros=String;`+
    extract('function mergeMetricHistogram(', '// Each bucket covers')+
    extract('function aggregateCloudMetric(', '// Outlier analysis.')+';globalThis.render=envSummary;',c);
  const rows=[{date:'2026-11-10',gateway:{n:1,mean:10,p50:10,p95:10}},
              {date:'2026-11-11',gateway:{n:3,mean:30,p50:30,p95:30}}];
  const today=c.render({days:[rows[1]]});
  const week=c.render({days:rows});
  assert.match(today, /30 \/ 30 \/ 30/);
  assert.match(week, /25 \/ undefined \/ undefined/);
  assert.match(week, /2026-11-10 至 2026-11-11/);
});

test('cloud model events and stream exception rates keep separate semantics', () => {
  const c=vm.createContext({});
  vm.runInContext(extract('function envCallSeries(', 'function envCallTrend(')+';globalThis.series=envCallSeries;',c);
  const rows=[{calls:{total:7},failure_rate:0.7}, {model_usage_events:5,gateway_stream:{exception_rate:0}},
              {model_usage_events:2,gateway_stream:{exception_rate:null}}];
  const result=c.series(rows);
  assert.deepEqual(Array.from(result.calls),[7,5,2]);
  assert.deepEqual(Array.from(result.rates),[null,0,null]);
});

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
  assert.deepEqual(Array.from(c.scope(rows,7,'2026-11-11','2026-11-10'),r=>r.date),['2026-11-04','2026-11-10']);
  assert.deepEqual(Array.from(c.scope(rows,1,'2026-11-11','2026-11-10'),r=>r.date),['2026-11-11']);
  assert.deepEqual(Array.from(c.scope(rows,0,'2026-11-11','2026-11-10'),r=>r.date),['2026-11-04','2026-11-10']);
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
