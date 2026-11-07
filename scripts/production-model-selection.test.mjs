import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
const extract=(start,end)=>{const a=source.indexOf(start),b=source.indexOf(end,a);if(a<0||b<0)throw Error('missing function');return source.slice(a,b);};
const context=vm.createContext({});
vm.runInContext(`const MIN_RECENT_CALLS=50; const RECENT_WINDOW_DAYS=7; const esc=(s)=>String(s).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'); const fmtCount=(v)=>String(v); const fmtDuration=()=> '—'; const fmtSecondsBare=()=> '—'; const fmtTpot=()=> '—'; const fmtTpotBare=()=> '—'; const fmtInUnit=()=> '—'; const fmtToken=()=> '—'; const fmtPercent=()=> '—'; const modelDisplayName=(n)=>String(n); const p50RangeText=()=> '—'; const p95RangeText=()=> '—'; const deltaChip=()=>''; const regressionFindings=()=>[];`+extract('function modelCompareCard(', '// The freshest point')+'; globalThis.card=modelCompareCard;',context);
test('a single model is not repeated as a one-item switcher', () => {
 const html=context.card([{name:'only',hasData:true,totals:{calls:900},latest:null,previous:null}],{selected:'only'});
 assert.equal(html,'');
});

test('model selection is a chip with an accessible selected state and a no-data marker',()=>{
 const busy={name:'busy',hasData:true,totals:{calls:900},latest:null,previous:null};
 const other={name:'<model>',hasData:false,totals:{calls:0},latest:null,previous:null};
 const html=context.card([busy,other],{selected:'<model>'});
 assert.match(html, /aria-pressed="true"/);
 assert.match(html, /&lt;model>/);
});

test('low-volume models stay out of the comparison list', () => {
 const active={name:'busy',hasData:true,totals:{calls:900},latest:null,previous:null,lowVolume:false,recentCalls:900};
 const steady={name:'steady',hasData:true,totals:{calls:300},latest:null,previous:null,lowVolume:false,recentCalls:300};
 const quiet={name:'one-call',hasData:true,totals:{calls:1},latest:null,previous:null,lowVolume:true,recentCalls:1};
 const html=context.card([active,steady,quiet],{selected:'busy'});
 assert.match(html,/data-select-model="busy"/);
 assert.doesNotMatch(html,/data-select-model="one-call"/);
 assert.doesNotMatch(html,/低流量模型/);
});

test('comparison retains selectable models outside the selected time window', () => {
 const c=vm.createContext({});
 vm.runInContext(`const MIN_RECENT_CALLS=50; const RECENT_WINDOW_DAYS=7; const isLowVolumeModel=()=>false; const benchmarkModelNames=()=>['active','inactive']; const modelSeries=(_,m)=>m==='active'?[{date:'2026-11-03'}]:[{date:'2026-10-01'}]; const addDays=()=> '2026-10-28'; const newMetricAccumulator=()=>({}); const mergeMetricRow=()=>{}; const finalizeMetricAccumulator=()=>({calls:0}); const previousRow=()=>null; const historyCompleteThrough=()=>'2026-11-03'; const historyLatestDate=()=>'2026-11-03'; const recentCallsForDays=()=>0; const failureSeries=()=>[];`+extract('function modelComparison(', 'function modelCompareCard(')+'; globalThis.comparison=modelComparison;',c);
 const rows=c.comparison({},7);
 assert.equal(rows.length,2);
 assert.equal(rows.find((row)=>row.name==='inactive').hasData,false);
});

test('comparison does not treat a stale model day as the latest complete day', () => {
 const c=vm.createContext({});
 vm.runInContext(`const MIN_RECENT_CALLS=50; const RECENT_WINDOW_DAYS=7; const isLowVolumeModel=()=>false; const benchmarkModelNames=()=>['idle']; const modelSeries=()=>[{date:'2026-11-02',calls:100,ttft_ms:10}]; const addDays=(d)=>d==='2026-11-04'?'2026-11-03':'2026-10-29'; const newMetricAccumulator=()=>({}); const mergeMetricRow=()=>{}; const finalizeMetricAccumulator=()=>({calls:100}); const historyCompleteThrough=()=>'2026-11-04'; const historyLatestDate=()=>'2026-11-04'; const recentCallsForDays=()=>100; const failureSeries=()=>[];`+extract('function modelComparison(', 'function modelCompareCard(')+'; globalThis.comparison=modelComparison;',c);
 const [row]=c.comparison({},0);
 assert.equal(row.latest,null);
 assert.equal(row.previous,null);
});
