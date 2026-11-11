import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const src=readFileSync(new URL('../single-repo/static/production-metrics/app.js',import.meta.url),'utf8');
const part=(start,end)=>src.slice(src.indexOf(start),src.indexOf(end,src.indexOf(start)));
const c=vm.createContext({});
vm.runInContext(`const state={traceFilter:'all'}; const esc=(v)=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'); const fmtDuration=(v)=>v+'ms';`+part('function slowTraceCandidates(doc)', 'function renderRequestTraces(doc)')+';globalThis.rows=traceRows;globalThis.html=traceHtml;globalThis.available=availableTraceCandidates;globalThis.state=state;',c);
const incident={scope:'local',gateway:1000,user_event_ms:20,final_ms:990,tools:[{index:1,name:'bash',start_ms:100,duration_ms:500,success:false,summary:'超时',result_chars:10}],models:[{index:1,time_ms:99,input_tokens:30,cache_read_tokens:20,cache_miss_tokens:10}]};
test('product phases use a separate clock and reject invalid or unknown spans',()=>{
 const item={scope:'cloud',gateway:3000,product_trajectory:{duration_ms:800,steps:[
  {name:'session.proximity',start_ms:10,duration_ms:200},{name:'session.route',start_ms:20,duration_ms:50},
  {name:'<private>',start_ms:20,duration_ms:50},{name:'brief',start_ms:799,duration_ms:10}]}};
 const html=c.html(item);assert.match(html,/产品侧阶段/);assert.match(html,/会话路由/);
 assert.match(html,/使用不同起点/);assert.match(html,/800ms/);assert.doesNotMatch(html,/<private>/);assert.doesNotMatch(html,/准备 Brief/);
});
test('local diagnostics are shown only on loopback',()=>{
 const item={scope:'cloud',gateway:1000,tool_events:[{name:'tool',start_ms:100,duration_ms:20,status:'ok',local_debug_name:'weather',local_debug_arguments:{location:'Hong Kong'}}]};
 c.location={hostname:'public.example'};let rows=c.rows(item);assert.match(rows[1].label,/工具/);assert.equal(rows[1].arguments,null);
 c.location={hostname:'127.0.0.1'};rows=c.rows(item);assert.match(rows[1].label,/weather/);assert.match(rows[1].arguments,/Hong Kong/);
 delete c.location;
});
test('native spans render actual model and nested phase intervals on their own root clock',()=>{
 const item={scope:'cloud',gateway:3000,native_trajectory:{duration_ms:2000,root_span_id:'a',spans:[
  {span_id:'b',parent_span_id:'a',kind:'phase',label:'输入准备',start_ms:100,duration_ms:200},
  {span_id:'c',parent_span_id:'b',kind:'model',label:'模型调用',start_ms:150,duration_ms:100},
  {span_id:'d',parent_span_id:'a',kind:'tool',label:'工具调用',start_ms:500,duration_ms:300}]}};
 c.state.traceFilter='all';const rows=c.rows(item);assert.equal(rows[0].duration,2000);assert.equal(rows[2].depth,2);
 const html=c.html(item);assert.match(html,/WorkSwarm 原生轨迹/);assert.match(html,/模型调用/);assert.match(html,/输入准备/);
 assert.match(html,/2000ms/);assert.doesNotMatch(html,/3000ms/);
 c.state.traceFilter='models';assert.match(c.html(item).split('class="trace-event-list"')[1],/模型调用/);
});
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
