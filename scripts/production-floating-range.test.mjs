import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

test('global range emits the same diagnostic window to the archive', () => {
  const source=readFileSync(new URL('../single-repo/static/production-metrics/app.js',import.meta.url),'utf8');
  const extract=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
  const emitted=[], sent=[];
  const state={rangeDays:1,history:{}};
  const context=vm.createContext({state, todayShanghai:()=> '2026-11-19',historyCompleteThrough:()=> '2026-11-18',
    document:{querySelectorAll:()=>[],dispatchEvent(event){emitted.push(event.detail);}},
    CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
    window:{parent:{postMessage(value){sent.push(value);}}},location:{origin:'https://example.test'},
  });
  vm.runInContext(extract('function addDays(date, days)', 'function weekSeries(days)')
    + extract('function cloudTraceWindow(', 'function cloudTraceRangeLabel(')
    + extract('function syncRangeButtons(', 'function currentModelDays()')
    + ';globalThis.sync=syncRangeButtons;',context);
  for (const [range,start,end] of [[1,'2026-11-19','2026-11-19'],[7,'2026-11-12','2026-11-18'],
    [30,'2026-10-20','2026-11-18'],[0,'','2026-11-19']]) {
    state.rangeDays=range;
    context.sync(false);
    assert.deepEqual({...emitted.at(-1)}, {range,start,end});
    assert.equal(sent.at(-1).range,range);
  }
});

test('embedded range control synchronizes with its own frame and rejects other senders', () => {
  const source = readFileSync(new URL('../single-repo/static/app.js', import.meta.url), 'utf8');
  const start = source.indexOf("  const productionFrame = document.querySelector('#tab-production-metrics iframe');");
  const end = source.indexOf("  window.addEventListener('hashchange'", start);
  assert.ok(start >= 0 && end > start);
  const handlers = {}, sent = [];
  const buttons = [0, 1, 7, 30].map((n) => ({dataset:{productionRange:String(n)}, disabled:false, setAttribute(k,v){this[k]=v;}}));
  const range = {hidden:true, style:{}, addEventListener(_name,cb){handlers.click=cb;}, querySelectorAll(){return buttons;}};
  const frame = {contentWindow:{postMessage(data,origin){sent.push({data,origin});}}, addEventListener(_name,cb){handlers.load=cb;}};
  const topbar = {getBoundingClientRect(){return {bottom:84};}};
  const context = vm.createContext({
    document:{querySelector(selector){return selector === '#tab-production-metrics iframe' ? frame : selector === '.topbar' ? topbar : range;}},
    window:{addEventListener(name,cb){handlers[name]=cb;}}, location:{origin:'https://example.test'}
  });
  vm.runInContext(source.slice(start,end), context);
  assert.equal(range.style.top, '92px');
  handlers.message({origin:'https://other.test', source:frame.contentWindow, data:{type:'production-metrics-range-state',range:1}});
  assert.equal(range.hidden,true);
  handlers.message({origin:'https://example.test', source:{}, data:{type:'production-metrics-range-state',range:1}});
  assert.equal(range.hidden,true);
  handlers.message({origin:'https://example.test', source:frame.contentWindow, data:{type:'production-metrics-range-state',range:7,disabled:false}});
  assert.equal(range.hidden,false);
  assert.equal(buttons[1]['aria-pressed'],'false');
  assert.equal(buttons[2]['aria-pressed'],'true');
  handlers.click({target:{closest(){return buttons[2];}}});
  assert.equal(sent.at(-1).data.type,'production-metrics-range-select');
  assert.equal(sent.at(-1).data.range,7);
  assert.equal(sent.at(-1).origin,'https://example.test');
});
