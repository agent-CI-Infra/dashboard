import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {mergeHistory} from './merge-e2e-performance.mjs';

const app=readFileSync(new URL('../single-repo/static/app.js',import.meta.url),'utf8');
const helpers=`const esc=(x)=>String(x).replaceAll('<','&lt;'); const perfInt=(x)=>x == null ? '—' : String(x); const perfMs=(x)=>x == null ? '—' : String(x)+'ms'; const empty=(x)=>x;`;
function extract(from,to) { return app.slice(app.indexOf(from),app.indexOf(to)); }
const ctx=vm.createContext({});
vm.runInContext(helpers+extract('  function productionModelSection(doc)', '  function renderOverview(snap)')+extract('  function perfBenchmark(latest)', '  function perfCloud(latest)')+';globalThis.prod=productionModelSection;globalThis.bench=perfBenchmark;',ctx);

test('production view labels gateway scope and refuses unknown snapshots',()=>{
 assert.equal(ctx.prod({schema:'other'}),'');
 const html=ctx.prod({schema:'model-request-metrics@1',scope:'classified-production',models:[{model:'<model>',calls:2}],calls:[]});
 assert.match(html,/现网模型请求/);assert.match(html,/网关侧/);assert.match(html,/&lt;model>/);
});
test('benchmark view refuses legacy unmatched traces',()=>{
 assert.match(ctx.bench({cloud:{calls:[{model:'other'}]}}),/没有可确认关联/);
 const html=ctx.bench({cloud:{benchmark:{matched_calls:1,unmatched_calls:2,models:[{model:'a',call_count:1}],calls:[]}}});
 assert.match(html,/已关联 1 次调用/);assert.match(html,/未关联 2 次/);
});
test('daily history preserves verified model summaries',()=>{
 const data=mergeHistory(null,{run_id:'r1',generated_at:'2026-10-30T00:00:00Z',cases:[{id:'case',finished_at:'2026-10-30T00:00:00Z',cloud:{benchmark:{models:[{model:'a',total_tokens:12}]}}}]});
 assert.equal(data.cases.case.points[0].benchmark_models[0].total_tokens,12);
});
