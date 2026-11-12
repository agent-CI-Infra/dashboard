import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

test('embedded range control synchronizes with its own frame and rejects other senders', () => {
  const source = readFileSync(new URL('../single-repo/static/app.js', import.meta.url), 'utf8');
  const start = source.indexOf("  const productionFrame = document.querySelector('#tab-production-metrics iframe');");
  const end = source.indexOf("  window.addEventListener('hashchange'", start);
  assert.ok(start >= 0 && end > start);
  const handlers = {}, sent = [];
  const buttons = [0, 1, 7, 30].map((n) => ({dataset:{productionRange:String(n)}, disabled:false, setAttribute(k,v){this[k]=v;}}));
  const range = {hidden:true, addEventListener(_name,cb){handlers.click=cb;}, querySelectorAll(){return buttons;}};
  const frame = {contentWindow:{postMessage(data,origin){sent.push({data,origin});}}, addEventListener(_name,cb){handlers.load=cb;}};
  const context = vm.createContext({
    document:{querySelector(selector){return selector === '#tab-production-metrics iframe' ? frame : range;}},
    window:{addEventListener(name,cb){handlers[name]=cb;}}, location:{origin:'https://example.test'}
  });
  vm.runInContext(source.slice(start,end), context);
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
