import assert from 'node:assert/strict';
import test from 'node:test';
import {selectTraces, coverageText, detailPath, spanBar, reconciliationHtml, PAGE_SIZE} from '../single-repo/static/production-metrics/trajectory-archive.js';

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
