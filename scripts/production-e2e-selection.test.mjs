import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../single-repo/static/production-metrics/app.js',import.meta.url),'utf8');
const section=source.slice(source.indexOf('let e2eHistory = null;'),source.indexOf('async function loadE2eBreakdown()'));

test('each curve uses exactly one region and network, and selection changes both graph and table',()=>{
 const target={innerHTML:'',querySelector:()=>null};
 const context=vm.createContext({$:()=>target,esc:String,fmtDuration:String,fmtCount:String,STAGE_AVG_ORDER:[],STAGE_AVG_LABEL:{},dailyBreakdownChart:points=>`GRAPH:${points.map(p=>p.date+':'+p.region).join(',')}`});
 vm.runInContext(section+';globalThis.setHistory=h=>{e2eHistory=h};globalThis.selectGroup=v=>{e2eNetwork=v};globalThis.render=renderE2eBreakdown;',context);
 context.setHistory({cases:{case:{id:'case',points:[{date:'2026-11-01',region:'上海',network:'WiFi'},{date:'2026-11-02',region:'北京',network:'蜂窝'}]}}});
 context.render();
 assert.match(target.innerHTML,/GRAPH:2026-11-01:上海/);
 assert.doesNotMatch(target.innerHTML,/GRAPH:2026-11-01:上海,2026-11-02:北京/);
 assert.doesNotMatch(target.innerHTML,/全部地域/);
 context.selectGroup(JSON.stringify(['北京','蜂窝']));context.render();
 assert.match(target.innerHTML,/GRAPH:2026-11-02:北京/);
 assert.match(target.innerHTML,/<td>2026-11-02<\/td>/);
 assert.doesNotMatch(target.innerHTML,/<td>2026-11-01<\/td>/);
});
