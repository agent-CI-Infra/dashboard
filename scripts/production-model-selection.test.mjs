import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source = readFileSync(new URL('../single-repo/static/production-metrics/app.js', import.meta.url), 'utf8');
const extract=(start,end)=>{const a=source.indexOf(start),b=source.indexOf(end,a);if(a<0||b<0)throw Error('missing function');return source.slice(a,b);};
const context=vm.createContext({});
vm.runInContext(`const esc=(s)=>String(s).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'); const fmtCount=(v)=>String(v); const fmtDuration=()=> '—'; const fmtTpot=()=> '—'; const fmtToken=()=> '—'; const p95RangeText=()=> '—'; const deltaChip=()=>''; const regressionFindings=()=>[];`+extract('function modelCompareCard(', '// The freshest point')+'; globalThis.card=modelCompareCard;',context);
test('model selection is a button with an accessible selected state and no-data row',()=>{
 const html=context.card([{name:'<model>',hasData:false,totals:{calls:0},latest:null,previous:null}],{selected:'<model>'});
 assert.match(html, /aria-pressed="true"/);
 assert.match(html, /&lt;model>/);
 assert.match(html, /当前查看/);
 assert.match(html, /无数据/);
});

test('comparison retains selectable models outside the selected time window', () => {
 const c=vm.createContext({});
 vm.runInContext(`const benchmarkModelNames=()=>['active','inactive']; const modelSeries=(_,m)=>m==='active'?[{date:'2026-11-03'}]:[{date:'2026-10-01'}]; const addDays=()=> '2026-10-28'; const newMetricAccumulator=()=>({}); const mergeMetricRow=()=>{}; const finalizeMetricAccumulator=()=>({calls:0}); const previousRow=()=>null;`+extract('function modelComparison(', 'function modelCompareCard(')+'; globalThis.comparison=modelComparison;',c);
 const rows=c.comparison({},7);
 assert.equal(rows.length,2);
 assert.equal(rows.find((row)=>row.name==='inactive').hasData,false);
});
