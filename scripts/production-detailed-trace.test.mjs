import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const src=readFileSync(new URL('../single-repo/static/production-metrics/app.js',import.meta.url),'utf8');
const part=(start,end)=>src.slice(src.indexOf(start),src.indexOf(end,src.indexOf(start)));
const c=vm.createContext({});
vm.runInContext(`const state={traceFilter:'all'}; const esc=(v)=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'); const fmtDuration=(v)=>v+'ms';`+part('function slowTraceCandidates(doc)', 'function renderRequestTraces(doc)')+';globalThis.rows=traceRows;globalThis.html=traceHtml;globalThis.available=availableTraceCandidates;globalThis.state=state;',c);
const incident={scope:'local',gateway:1000,user_event_ms:20,final_ms:990,tools:[{index:1,name:'bash',start_ms:100,duration_ms:500,success:false,summary:'超时',result_chars:10}],models:[{index:1,time_ms:99,input_tokens:30,cache_read_tokens:20,cache_miss_tokens:10}]};
test('records full history as intervals and point events without inferring model duration',()=>{
 const rows=c.rows(incident); assert.equal(rows.length,5); assert.equal(rows.filter(r=>r.kind==='error')[0].duration,500);
 const point=rows.find(r=>r.kind==='model-point');assert.equal(point.start,99);assert.equal(point.duration,0);assert.match(point.detail,/无法推算模型耗时/);
 assert.deepEqual(Array.from(rows,r=>r.start),[0,20,99,100,990]);
});
test('renders selectable timeline and filters tool and model events',()=>{
 c.state.traceFilter='all';let html=c.html(incident);assert.match(html,/执行轨迹/);assert.match(html,/data-trace-row/);assert.match(html,/模型用量记录/);assert.match(html,/bash/);
 c.state.traceFilter='tools';html=c.html(incident).split('class="trace-event-list"')[1];assert.doesNotMatch(html,/01 · 模型用量记录/);assert.match(html,/01 · bash/);
 c.state.traceFilter='models';html=c.html(incident).split('class="trace-event-list"')[1];assert.match(html,/01 · 模型用量记录/);assert.doesNotMatch(html,/01 · bash/);
});
test('rejects a tool event outside request and escapes event names',()=>{
 const rows=c.rows({...incident,tools:[{index:1,name:'bash',summary:'<unsafe>',start_ms:990,duration_ms:20}]});assert.equal(rows.some(r=>r.kind==='tool'),false);
 c.state.traceFilter='all';const html=c.html({...incident,tools:[{index:1,name:'bash',summary:'<unsafe>',start_ms:100,duration_ms:20}]});assert.match(html,/&lt;unsafe>/);assert.doesNotMatch(html,/<unsafe>/);
});

test('cloud candidates take priority and do not include model calls',()=>{
 const doc={days:[{date:'2026-11-10',anomaly:{latency:{slow_requests:[{request_id:'cloud-a',gateway:1000,first_token:100,answer_first:800,react:700}]},timeouts:{requests:[]}}}]};
 const local={scope:'local',request_id:'example',gateway:530619};
 const candidates=c.available(doc,local);
 assert.deepEqual(Array.from(candidates, r=>r.scope),['cloud','local']);
 c.state.traceFilter='tools'; const html=c.html(candidates[0]);
 assert.match(html,/至首个文本/);assert.match(html,/思考与工具阶段/);assert.match(html,/答案输出阶段/);
});

test('published production trace collection is rendered as cloud requests',()=>{
 const ctx=vm.createContext({});
 const a=src.indexOf('function slowTraceCandidates(doc)'), b=src.indexOf("$('request-traces').addEventListener('change'",a);
 const setup=`const state={rangeDays:1}; const nodes={'trace-picker':{},'trace-result':{}}; const $=(id)=>nodes[id]; const todayShanghai=()=> '2026-11-10'; const historyCompleteThrough=()=> '2026-11-09'; const cloudScopedDays=(rows)=>rows.filter(r=>r.date==='2026-11-10'); const fmtCount=String; const fmtDateTime=String; const fmtDuration=(n)=>n+'ms'; const esc=String;`;
 vm.runInContext(setup+src.slice(a,b)+';globalThis.render=renderRequestTraces;globalThis.nodes=nodes;',ctx);
 const traces={schema:'digitalmate/cloud-traces@1',generated_at:'2026-11-10T12:00:00Z',read_log_count:2,binding_count:3,days:[{date:'2026-11-10',anomaly:{latency:{slow_requests:[{request_id:'digitalmate-test',gateway:1000}]},timeouts:{requests:[]}}}]};
 ctx.render({cloud_trace_collection:traces});
 assert.match(ctx.nodes['trace-picker'].innerHTML,/digitalmate-test/);
 assert.match(ctx.nodes['trace-picker'].innerHTML,/2 \/ 3/);
 assert.match(ctx.nodes['trace-result'].innerHTML,/执行轨迹/);
});
