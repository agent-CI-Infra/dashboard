import assert from 'node:assert/strict';
import test from 'node:test';
import {selectTraces, traceDay, startArchive, coverageText, detailPath, spanBar, reconciliationHtml, PAGE_SIZE} from '../single-repo/static/production-metrics/trajectory-archive.js';

test('archive dates use Shanghai boundaries and intersect global and manual filters', () => {
  const rows = ['2026-10-01T00:00:00Z', '2026-11-12T00:00:00Z', '2026-11-18T15:59:59Z',
    '2026-11-18T16:00:00Z', '2026-11-19T16:00:00Z', 'invalid'].map(start_time => ({start_time}));
  assert.equal(traceDay(rows[2].start_time), '2026-11-18');
  assert.equal(traceDay(rows[3].start_time), '2026-11-19');
  assert.deepEqual(selectTraces(rows, '', '', '', {start:'2026-11-19', end:'2026-11-19'}), [rows[3]]);
  assert.deepEqual(selectTraces(rows, '', '', '', {start:'2026-11-12', end:'2026-11-18'}), rows.slice(1, 3));
  assert.deepEqual(selectTraces(rows, '', '', '2026-11-19', {start:'2026-11-12', end:'2026-11-18'}), []);
  assert.deepEqual(selectTraces(rows, '', '', '', {end:'2026-11-19'}), rows.slice(0, 4));
});

test('reconciliation counts use the intersection of the selected range and retained logs', () => {
  const requests = ['2026-11-13', '2026-11-18', '2026-11-19'].map((day, i) => ({
    request_id:`request-${i}`, start_time:`${day}T00:00:00Z`, match_state:'matched', log_state:'finished', native_lifecycle:'final',
  }));
  const doc = {traces:[...requests, {request_id:'native-only', start_time:'2026-11-19T00:00:00Z'}], request_reconciliation:{
    schema:'digitalmate/request-reconciliation@1', state:'available', requests,
    window:{start:'2026-11-13',end:'2026-11-19'}, read_log_count:1, binding_count:1,
  }};
  const today = reconciliationHtml(doc, '', '', {start:'2026-11-19',end:'2026-11-19'});
  assert.match(today, /请求日志 1 条/);
  assert.match(today, /仅见轨迹 1/);
  assert.match(today, /对账范围 2026-11-19\s/);
  const week = reconciliationHtml(doc, '', '', {start:'2026-11-12',end:'2026-11-18'});
  assert.match(week, /请求日志 2 条/);
  assert.match(week, /仅见轨迹 0/);
  assert.match(week, /对账范围 2026-11-13 至 2026-11-18/);
  assert.match(reconciliationHtml(doc, '', '', {end:'2026-11-12'}), /超出逐请求日志对账范围/);
});

test('range replay before fetch, pagination, refresh and pending details stay in sync', async t => {
  const element = () => ({value:'', min:'', max:'', content:'', handlers:{},
    set innerHTML(value){this.content=value;}, get innerHTML(){return this.content;},
    set textContent(value){this.content=value;}, get textContent(){return this.content;},
    addEventListener(name, handler){this.handlers[name]=handler;},
  });
  const nodes = Object.fromEntries(['coverage','query','day','lifecycle','form','reconciliation','list','detail'].map(id=>[`archive-${id}`,element()]));
  const host = {...element(), querySelector(selector){return nodes[selector.slice(1)];}};
  const events = new EventTarget();
  events.getElementById = () => host;
  const period = (range, start, end='2026-11-19') => events.dispatchEvent(new CustomEvent('production-metrics-range-change', {detail:{range,start,end}}));
  events.addEventListener('production-metrics-range-request', () => period(1, '2026-11-19'));
  const traces = Array.from({length:60}, (_, i) => ({request_id:`yesterday-${i}`, key:`y-${i}`, start_time:'2026-11-18T00:00:00Z', lifecycle:'final', pages:[]}));
  const key = 'a'.repeat(64);
  traces.push({request_id:'today', key, start_time:'2026-11-18T16:00:00Z', lifecycle:'final', pages:[`traces/${key}/0.json`]});
  traces.push({request_id:'older', key:'older', start_time:'2026-11-01T00:00:00Z', pages:[]});
  traces.push({request_id:'oldest', key:'oldest', start_time:'2026-10-01T00:00:00Z', pages:[]});
  const doc = {schema:'digitalmate/trajectory-archive@1', traces, request_reconciliation:{
    schema:'digitalmate/request-reconciliation@1', state:'available',
    window:{start:'2026-11-13',end:'2026-11-19'}, requests:traces.slice(0,61),
  }};
  let resolveIndex, resolveDetail, refresh;
  const first = new Promise(resolve=>{resolveIndex=resolve;});
  const detail = new Promise(resolve=>{resolveDetail=resolve;});
  let firstFetch=true;
  const overrides = {document:events, location:{href:'https://example.test/production-metrics/index.html'},
    setInterval(callback){refresh=callback;return 1;},
    fetch:async url => ({ok:true, json:()=>url.pathname.endsWith('/index.json') ? (firstFetch ? (firstFetch=false,first) : doc) : detail}),
  };
  for (const [name,value] of Object.entries(overrides)) {
    const old=Object.getOwnPropertyDescriptor(globalThis,name);
    Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});
    t.after(()=>old ? Object.defineProperty(globalThis,name,old) : delete globalThis[name]);
  }
  const starting=startArchive();
  assert.equal(nodes['archive-day'].value,'2026-11-19', 'initial range must replay before the index arrives');
  resolveIndex(doc);
  await starting;
  assert.match(nodes['archive-list'].innerHTML,/符合筛选 1 条/);
  assert.match(nodes['archive-reconciliation'].innerHTML,/请求日志 1 条/);
  const click = dataset => host.handlers.click({target:{closest:()=>({dataset})}});
  click({archiveKey:key});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(nodes['archive-detail'].textContent,'正在读取轨迹…');
  period(7,'2026-11-12','2026-11-18');
  assert.equal(nodes['archive-day'].value,'');
  assert.equal(nodes['archive-day'].min,'2026-11-12');
  assert.equal(nodes['archive-day'].max,'2026-11-18');
  assert.match(nodes['archive-list'].innerHTML,/符合筛选 60 条/);
  assert.match(nodes['archive-reconciliation'].innerHTML,/请求日志 60 条/);
  resolveDetail({...doc,key,offset:0,total:1,spans:[]});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(nodes['archive-detail'].innerHTML,'', 'late detail must not restore an out-of-range request');
  click({archivePage:'1'});
  assert.match(nodes['archive-list'].innerHTML,/第 2 \/ 2 页/);
  period(30,'2026-10-20','2026-11-18');
  assert.match(nodes['archive-list'].innerHTML,/符合筛选 61 条.*第 1 \/ 2 页/);
  period(0,'');
  assert.equal(nodes['archive-day'].min,'');
  assert.match(nodes['archive-list'].innerHTML,/符合筛选 63 条/);
  nodes['archive-day'].value='2026-11-18';
  nodes['archive-form'].handlers.submit({preventDefault(){}});
  await refresh();
  assert.match(nodes['archive-list'].innerHTML,/符合筛选 60 条/);
  period(1,'2026-11-19');
  assert.equal(nodes['archive-day'].value,'2026-11-19');
  assert.match(nodes['archive-list'].innerHTML,/符合筛选 1 条/);
});

test('full archive search covers non-slow requests and every page', () => {
  const rows=Array.from({length:251},(_,i)=>({key:String(i),request_id:`digitalmate-${i}`,lifecycle:'final',start_time:'2026-11-11T17:00:00Z'}));
  assert.equal(selectTraces(rows,'','').length,251);
  assert.equal(selectTraces(rows,'digitalmate-250','final')[0].key,'250');
  assert.equal(selectTraces(rows,'','final','2026-11-12').length,251);
  assert.equal(selectTraces(rows,'','running').length,0);
  assert.equal(PAGE_SIZE,50);
});
test('coverage never claims atomic consistency or complete collection', () => {
  const text=coverageText({coverage:{archived_traces:123,discovered_databases:9,observed_databases:8,pending_databases:1,statuses:{active_sidecars:1}}});
  assert.match(text,/123 条轨迹/); assert.match(text,/一致性未经写入端确认/);
  assert.match(text,/待采或待重试 1/); assert.match(text,/不代表全量无遗漏/);
});
test('detail fetch cannot escape the archive or use remote URLs', () => {
  assert.equal(detailPath(`traces/${'a'.repeat(64)}/3.json`),`traces/${'a'.repeat(64)}/3.json`);
  for(const path of ['../../secret.json','https://example.org/steal','traces/a/1.json','//example.org/a']) assert.throws(()=>detailPath(path));
});
test('timeline clips numeric intervals and only uses fixed colors', () => {
  const html = spanBar({start_ms:50,duration_ms:500,kind:'model'},100);
  assert.match(html,/left:50%;width:50%/);
  assert.equal(spanBar({start_ms:NaN,duration_ms:1},100),'');
  assert.doesNotMatch(spanBar({start_ms:0,duration_ms:1,kind:'evil'},100),/evil/);
});

test('reconciliation shows missing requests even when there is no archived row', () => {
  const time = '2026-11-18T16:00:00Z';
  const doc = {
    traces: [{request_id: 'digitalmate-present', request_ids: [], key: 'a'.repeat(64), start_time: time}],
    request_reconciliation: {
      schema: 'digitalmate/request-reconciliation@1', state: 'available',
      window: {start: '2026-11-13', end: '2026-11-19'}, binding_count: 194, read_log_count: 193,
      request_observed_at: time, archive_observed_at: time, errors: {missing: 1},
      requests: [
        {request_id: 'digitalmate-present', start_time: time, match_state: 'matched', log_state: 'finished', native_lifecycle: 'final'},
        {request_id: 'digitalmate-missing', start_time: time, match_state: 'missing', log_state: 'unfinished'},
        {request_id: 'digitalmate-pending', start_time: time, match_state: 'pending_archive', log_state: 'finished'},
      ],
    },
  };
  const html = reconciliationHtml(doc, '', '2026-11-19');
  assert.match(html, /请求日志 3 条/);
  assert.match(html, /已结束 2，未见结束日志 1/);
  assert.match(html, /未归档 1/);
  assert.match(html, /等待采集 1/);
  assert.match(html, /工作区日志覆盖 193\/194 · 部分覆盖/);
  assert.match(html, /digitalmate-missing/);
  assert.match(reconciliationHtml(doc, 'digitalmate-missing'), /请求日志 1 条/);
  assert.doesNotMatch(reconciliationHtml(doc, 'digitalmate-missing'), /digitalmate-pending/);
  assert.match(reconciliationHtml(doc, '', '2026-11-12'), /超出逐请求日志对账范围/);
  assert.match(reconciliationHtml({}), /尚未采集/);
});

test('reconciliation escapes identifiers and retains pending requests beyond one page', () => {
  const time = '2026-11-19T01:00:00Z';
  const doc = {traces: [], request_reconciliation: {
    schema: 'digitalmate/request-reconciliation@1', state: 'available',
    window: {start: '2026-11-19', end: '2026-11-19'},
    binding_count: 1, read_log_count: 1, errors: {},
    requests: Array.from({length: 101}, (_, number) => ({
      request_id: `digitalmate-${number}<script>`, start_time: time,
      log_state: 'unfinished', match_state: 'missing',
    })),
  }};
  const html = reconciliationHtml(doc);
  assert.match(html, /digitalmate-100&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /未归档 101/);
});

test('cancelled requests show native cleanup after the stream has closed', () => {
  const time = '2026-11-19T01:00:00Z';
  const html = reconciliationHtml({traces: [], request_reconciliation: {
    schema: 'digitalmate/request-reconciliation@1', state: 'available',
    window: {start: '2026-11-19', end: '2026-11-19'}, binding_count: 1, read_log_count: 1,
    requests: [{request_id: 'digitalmate-cancelled', start_time: time, log_state: 'finished',
      native_lifecycle: 'final', match_state: 'matched', cancel_tail_ms: 712.836}],
  }});
  assert.match(html, /已关联 1/);
  assert.match(html, /取消后收尾 0.713 秒/);
  assert.match(html, /关联待确认 0/);
});
