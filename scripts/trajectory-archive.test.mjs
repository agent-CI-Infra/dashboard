import assert from 'node:assert/strict';
import test from 'node:test';
import {selectTraces, coverageText, detailPath, spanBar, PAGE_SIZE} from '../single-repo/static/production-metrics/trajectory-archive.js';

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
