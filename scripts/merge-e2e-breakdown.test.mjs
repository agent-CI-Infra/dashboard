import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeHistory } from './merge-e2e-performance.mjs';

test('merges measured client stages and explicitly supplied network labels', () => {
  const run = { run_id:'a', generated_at:'2026-11-03T02:00:00Z', cases:[{
    id:'case', region:'上海', network:'WiFi', query_ms:1000, finished_at:'2026-11-03T02:00:00Z',
    queries:[{stages:[{name:'queued',at_ms:10},{name:'dequeued',at_ms:30},{name:'wire_send',at_ms:40},{name:'ack',at_ms:140},{name:'first_text',at_ms:340},{name:'last_text',at_ms:640},{name:'display_done',at_ms:690}]}],
  }] };
  const history = mergeHistory(null,run);
  const point = history.cases.case.points[0];
  assert.deepEqual(point.stage_avg_ms, {queue_ms:20,send_ms:10,ack_ms:100,first_text_ms:200,generation_ms:300,display_ms:50});
  assert.equal(point.stage_sample_count,1); assert.equal(point.region,'上海'); assert.equal(point.network,'WiFi');
  mergeHistory(history, { ...run, cases: [{...run.cases[0], region:'北京', finished_at:'2026-11-03T02:30:00Z'}] });
  assert.equal(history.cases.case.breakdown_points.length,2);
  run.cases[0] = {id:'case', query_ms:2000, finished_at:'2026-11-03T03:00:00Z', region:'1.2.3.4'};
  mergeHistory(history,run);
  assert.equal(history.cases.case.points[0].region,undefined);
  assert.equal(history.cases.case.points[0].network,undefined);
  assert.equal(history.cases.case.points[0].stage_avg_ms,undefined);
});

test('missing and invalid stage timestamps do not manufacture values', () => {
  const history=mergeHistory(null,{generated_at:'2026-11-03T02:00:00Z',cases:[{id:'case',queries:[{stages:[{name:'queued',at_ms:null},{name:'dequeued',at_ms:10}]}]}]});
  assert.equal(history.cases.case.points[0].stage_avg_ms,undefined);
});
