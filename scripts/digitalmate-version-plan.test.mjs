import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// The version plan lives in the board bundle as plain script: extract the real
// scoreDay + plan helpers and run them against a stubbed STATE, so the test
// follows the exact deployed lookup (Beijing calendar day -> plan version).
const source = readFileSync(new URL('../single-repo/static/app.js', import.meta.url), 'utf8');

function extractPlanHelpers(state) {
  const scoreDayStart = source.indexOf('  const scoreDay = (iso) => {');
  const scoreDayEnd = source.indexOf('};', scoreDayStart) + 2;
  assert.ok(scoreDayStart >= 0 && scoreDayEnd > scoreDayStart, 'scoreDay helper not found');
  const planStart = source.indexOf('  // DigitalMate 版本计划');
  const dmVersionTextLine = "const dmVersionText = (iso) => dmVersionOf(iso) || '—';";
  const planEnd = source.indexOf(dmVersionTextLine, planStart);
  assert.ok(planStart >= 0 && planEnd > planStart, 'DigitalMate version plan block not found');
  const planEndOfLine = source.indexOf('\n', planEnd);
  const script = `(() => {\n${source.slice(scoreDayStart, scoreDayEnd)}\n${source.slice(planStart, planEndOfLine)}\n`
    + 'return { plan: DIGITALMATE_VERSION_PLAN, dmVersionOf, dmVersionBadge, dmVersionText };\n})()';
  const badge = (text, tone = '') => `<span class="badge ${tone}">${text}</span>`;
  return vm.runInNewContext(script, { STATE: state, badge });
}

test('version plan table matches the team calendar: Tue/Thu/Sat releases, versions increment by one', () => {
  const { plan } = extractPlanHelpers({ snap: { repo: 'openJiuwen-ai/DigitalMate' } });
  assert.ok(plan.length >= 26, 'plan must cover the 2026-11/12 calendar');
  let previous = null;
  for (const [date, version] of plan) {
    assert.match(date, /^\d{4}-\d{2}-\d{2}$/, 'dates are plain YYYY-MM-DD');
    assert.match(version, /^v0\.1\.\d+$/, 'versions are v0.1.x');
    const weekday = new Date(`${date}T00:00:00Z`).getUTCDay();
    assert.ok([2, 4, 6].includes(weekday), `${date} must be a Tuesday, Thursday or Saturday`);
    if (previous) {
      assert.ok(date > previous.date, 'release dates are strictly increasing');
      assert.equal(Number(version.slice(5)), Number(previous.version.slice(5)) + 1, 'version numbers increment by one');
    }
    previous = { date, version };
  }
  assert.equal(plan[0][0], '2026-11-03');
  assert.equal(plan[0][1], 'v0.1.7');
  assert.equal(plan.at(-1)[0], '2026-12-31');
  assert.equal(plan.at(-1)[1], 'v0.1.32');
});

test('release days resolve to the exact plan version (Beijing calendar)', () => {
  const { dmVersionOf } = extractPlanHelpers({ snap: { repo: 'openJiuwen-ai/DigitalMate' } });
  assert.equal(dmVersionOf('2026-11-02T17:00:00Z'), 'v0.1.7');   // Beijing 11-03 01:00
  assert.equal(dmVersionOf('2026-11-14T10:00:00Z'), 'v0.1.12');  // Beijing 11-14 18:00
  assert.equal(dmVersionOf('2026-11-17T01:45:02Z'), 'v0.1.13');  // Beijing 11-17 09:45
  assert.equal(dmVersionOf('2026-12-31T04:00:00Z'), 'v0.1.32');  // Beijing 12-31 12:00
});

test('blank days name as previous version + betaN, counting Beijing calendar days', () => {
  const { dmVersionOf } = extractPlanHelpers({ snap: { repo: 'openJiuwen-ai/DigitalMate' } });
  assert.equal(dmVersionOf('2026-11-14T22:09:22Z'), 'v0.1.12-beta1'); // Beijing 11-15, one day after the v0.1.12 release
  assert.equal(dmVersionOf('2026-11-16T02:00:00Z'), 'v0.1.12-beta2'); // Beijing 11-16
  assert.equal(dmVersionOf('2026-11-04T00:00:00Z'), 'v0.1.7-beta1');  // Beijing 11-04
  assert.equal(dmVersionOf('2026-12-02T00:00:00Z'), 'v0.1.19-beta1'); // Beijing 12-02
});

test('days before the plan have no version; days past the plan keep the last version + betaN', () => {
  const { dmVersionOf } = extractPlanHelpers({ snap: { repo: 'openJiuwen-ai/DigitalMate' } });
  assert.equal(dmVersionOf('2026-10-20T00:00:00Z'), null);
  assert.equal(dmVersionOf('2026-11-02T10:00:00Z'), null);            // Beijing 11-02, before the first entry
  assert.equal(dmVersionOf('2026-12-31T20:00:00Z'), 'v0.1.32-beta1'); // Beijing 2027-01-01
  assert.equal(dmVersionOf('2027-01-02T00:00:00Z'), 'v0.1.32-beta2'); // Beijing 2027-01-02
});

test('lookup only applies to the DigitalMate board and tolerates bad input', () => {
  const dm = extractPlanHelpers({ snap: { repo: 'openJiuwen-ai/DigitalMate' } });
  assert.equal(dm.dmVersionOf('2026-11-14T22:09:22Z'), 'v0.1.12-beta1');
  assert.equal(extractPlanHelpers({ snap: { repo: 'other/repo' } }).dmVersionOf('2026-11-14T22:09:22Z'), null);
  assert.equal(extractPlanHelpers({ snap: null }).dmVersionOf('2026-11-14T22:09:22Z'), null);
  assert.equal(dm.dmVersionOf('not-a-date'), null);
  assert.equal(dm.dmVersionOf(undefined), null);
});

test('badge helpers render a chip when known and nothing when unknown', () => {
  const { dmVersionBadge, dmVersionText } = extractPlanHelpers({ snap: { repo: 'openJiuwen-ai/DigitalMate' } });
  assert.equal(dmVersionBadge('2026-11-14T22:09:22Z'), '<span class="badge none">v0.1.12-beta1</span> ');
  assert.equal(dmVersionBadge('2026-10-20T00:00:00Z'), '');
  assert.equal(dmVersionText('2026-11-14T22:09:22Z'), 'v0.1.12-beta1');
  assert.equal(dmVersionText('2026-10-20T00:00:00Z'), '—');
});

test('ST and E2E run displays wire the version chip next to the run id', () => {
  assert.ok(source.includes('value: `${dmVersionBadge(latest.createdAt)}<a href='), 'ST latest-run tile shows version + runid');
  assert.ok(source.includes("{ key: 'dmVersion', label: '版本'"), 'ST run-history table has a version column');
  assert.ok(source.includes('版本 ${esc(latestVersion)} · '), 'ST latest-run detail header names the version');
  assert.ok(source.includes('${dmVersionBadge(run.created_at)}${link(run.url, `run'), 'E2E score-run rows show version + run id');
  assert.ok(source.includes('最近一次可读取的逐用例报告：${dmVersionBadge(report.created_at)}'), 'E2E report line shows the version');
  assert.ok(source.includes('口径 ${dmVersionBadge(currentRun.at)}'), 'nightly performance header shows the version');
  assert.ok(source.includes('${dmVersionBadge(p.finished_at || p.date)}<code>'), 'performance history table shows the version');
  assert.ok(source.includes('${dmVersionText(record.created_at)}'), 'E2E record tooltips include the version');
});
