import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../single-repo/static/production-metrics/app.js',import.meta.url),'utf8');
const code=source.slice(source.indexOf('function requestMetricsFromRow('),source.indexOf('function bindRequestLookup()'));
let rows=[];
const ctx=vm.createContext({Date,Number,String,Array,AbortSignal,encodeURIComponent,fetch:async()=>({ok:true,json:async()=>rows}),GATEWAY_BASE:'https://example.invalid',esc:String,fmtDateTime:String,fmtDuration:String,fmtTpot:String,fmtToken:String,state:{history:null},benchmarkModelNames:()=>[]});
vm.runInContext(code+';globalThis.lookup=queryGatewayRequest;globalThis.metrics=requestMetricsFromRow;',ctx);
test('gateway lookup returns only exact matching id',async()=>{
 rows=[{request_id:'different',model:'wrong'}];assert.equal(await ctx.lookup('requested','key'),null);
 rows.push({request_id:'requested',model:'correct'});assert.equal((await ctx.lookup('requested','key')).model,'correct');
});
test('TPOT uses generated tokens minus one and excludes one-token output',()=>{
 const row={status:'success',startTime:'2026-11-01T00:00:00Z',completionStartTime:'2026-11-01T00:00:01Z',endTime:'2026-11-01T00:00:02Z',completion_tokens:3};
 assert.equal(ctx.metrics(row).tpotMs,500);assert.equal(ctx.metrics({...row,completion_tokens:1}).tpotMs,null);
 assert.equal(ctx.metrics({...row,stream:false}).tpotMs,null);
 assert.equal(ctx.metrics({...row,metadata:{spend_logs_metadata:{stream:false}}}).ttftMs,null);
 assert.equal(ctx.metrics({...row,status:'failure'}).tpotMs,null);
 assert.equal(ctx.metrics({...row,endTime:row.completionStartTime}).tpotMs,null);
 assert.equal(ctx.metrics({...row,completionStartTime:'2026-11-01T00:00:01.000100Z',endTime:'2026-11-01T00:00:01.000900Z'}).tpotMs,0.4);
});
