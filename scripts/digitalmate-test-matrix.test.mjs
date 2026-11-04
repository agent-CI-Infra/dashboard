import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { buildMatrix, classifyDependency, countNodeTests, countPyTests, parseCatalog, parseLedger, parseLedgerStatus, parseScenes } from './digitalmate-test-matrix.mjs'

const LEDGER = `### Design Ideas

| 进度 | ST | E2E | 合计 |
|---|---:|---:|---:|
| 目标用例 | 2 | 2 | 4 |

ST 用例（3 条）：

| 编号 | 测试内容 | 当前进度 | 对应 PR | 证据 |
|---|---|---|---|---|
| ATT-01 | 元数据冲突 | 稳定通过 | #527；复核 #538 | [原始记录](https://github.com/o/r/blob/abc/README.md)；两轮 |
| ART-01 | 假回执 | 未真实执行；假回执注入 | #507 | 待实测取证 |
| ATT-07 | 图片问答 | 实测不稳定（FAIL/PASS） | #507 | 待实测取证 |

E2E 用例（2 条）：

| 编号 | 测试内容 | 当前进度 | 对应 PR | 证据 |
|---|---|---|---|---|
| UI-05 | 长草稿 | 已有 PASS；当前版复核 BLOCKED | #530 | 原件 |
| CTL-07 | 断网恢复 | 未真实执行；可恢复的真实模拟器网络控制 | #510 | 待实测取证 |

<!-- live-progress:start -->
| CTL-99 | 不应解析 | 稳定通过 | #1 | x |
`

test('ledger status keywords map to matrix statuses', () => {
  assert.equal(parseLedgerStatus('稳定通过').status, 'stable_pass')
  assert.equal(parseLedgerStatus('已有 PASS；当前版复核待完成').status, 'pass_recorded')
  assert.equal(parseLedgerStatus('已有 PASS；当前版复核 BLOCKED').status, 'pass_recheck_blocked')
  assert.equal(parseLedgerStatus('实测 FAIL').status, 'fail')
  assert.equal(parseLedgerStatus('实测不稳定（FAIL/PASS）').status, 'flaky')
  assert.equal(parseLedgerStatus('已尝试，BLOCKED').status, 'blocked')
  assert.deepEqual(parseLedgerStatus('未真实执行；双账号'), { status: 'not_run', dependency: '双账号' })
  assert.equal(parseLedgerStatus('').status, 'no_result')
})

test('ledger tables are parsed per layer and stop at the progress block', () => {
  const ledger = parseLedger(LEDGER)
  assert.deepEqual([...ledger.ST.keys()], ['ATT-01', 'ART-01', 'ATT-07'])
  assert.deepEqual([...ledger.E2E.keys()], ['UI-05', 'CTL-07'])
  assert.deepEqual(ledger.ST.get('ATT-01').prs, ['#527', '#538'])
  assert.equal(ledger.ST.get('ATT-01').evidence, 'https://github.com/o/r/blob/abc/README.md')
  assert.equal(ledger.E2E.get('CTL-07').dependency, '可恢复的真实模拟器网络控制')
})

test('dependencies cluster into stable tags', () => {
  assert.deepEqual(classifyDependency('双账号'), ['identity'])
  assert.deepEqual(classifyDependency('工具故障注入'), ['tool_fault'])
  assert.deepEqual(classifyDependency('帧注入 + 分片器'), ['frame'])
  assert.deepEqual(classifyDependency('真实模型超时一次性注入'), ['model_fault'])
  assert.deepEqual(classifyDependency('可恢复的真实模拟器网络控制'), ['env_control'])
  assert.deepEqual(classifyDependency('签名旧版与候选正式包'), ['release'])
  assert.deepEqual(classifyDependency('某个新依赖'), ['other'])
  assert.deepEqual(classifyDependency(''), [])
})

test('source parsers count tests and read scenes and the catalog', () => {
  assert.equal(countNodeTests("test('a', () => {})\n  test.skip('b')\nit('c')\n// test('no')\nconst x = test"), 3)
  assert.equal(countPyTests('def test_a():\n    pass\n    async def test_b(self):\ndef helper_test(): pass'), 2)
  const scenes = parseScenes("[\n { key:'text', name:'t', title:'文本', default:true, core:true },\n { key:'iso', name:'i', title:'隔离', coverageId:'CON-01' },\n]")
  assert.deepEqual(scenes.map((s) => [s.key, s.default, s.coverageId]), [['text', true, ''], ['iso', false, 'CON-01']])
  const catalog = parseCatalog('cases:\n  - id: DM-REG-A\n    lane: pr-ready\n    file: a.py\n  - id: DM-REG-B\n    owner: x\n')
  assert.deepEqual(catalog, [{ id: 'DM-REG-A', lane: 'pr-ready', file: 'a.py' }, { id: 'DM-REG-B', owner: 'x' }])
})

function fixtureRepo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dm-matrix-'))
  const g = (...args) => execFileSync('git', ['-C', dir, ...args], { stdio: 'pipe' })
  const write = (file, text) => { fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true }); fs.writeFileSync(path.join(dir, file), text) }
  g('init', '-q', '-b', 'main')
  g('config', 'user.email', 't@example.com')
  g('config', 'user.name', 't')
  write('tests/unit/cloud-chat.test.mjs', "test('a')\ntest('b')\n")
  write('harness/personal-agent/services/agent_bridge/tests/test_flow.py', 'def test_x():\n  pass\n')
  write('tests/cases/case-catalog.yaml', 'cases:\n  - id: DM-REG-X\n    lane: pr-ready\n    file: harness/x.py\n')
  write('tests/st/scenes.mjs', "export const sceneRegistry = [\n { key:'text', name:'t', title:'文本回复', default:true },\n { key:'attachment-metadata-conflict', name:'a', title:'附件冲突', coverageId:'ATT-01' },\n];\n")
  write('tests/e2e/workflow/cases/DM-WF-CHAT-01.workflow.json', JSON.stringify({ id: 'DM-WF-CHAT-01', title: '问候', priority: 'P0', steps: [{}, {}], score: { criteria: [{}] } }))
  write('tests/e2e/workflow/cases/DM-WF-BACK-02.workflow.json', JSON.stringify({ id: 'DM-WF-BACK-02', title: '长草稿', supplemental: true, sourceCase: 'plan.md (2026-11-03), UI-05' }))
  write('tests/eval/tasks/nightly/DM-EVAL-CHAT-01.task.json', JSON.stringify({ id: 'DM-EVAL-CHAT-01', title: '问候', suite: 'nightly', trials: 2, adapter: { kind: 'workflow', workflowId: 'DM-WF-CHAT-01' }, graders: ['a', 'b'] }))
  write('tests/dfx/perf/README.md', '# perf\n')
  g('add', '-A')
  g('commit', '-qm', 'main')
  g('checkout', '-qb', 'wip')
  write('tests/st/specifications/ART-01.case.json', JSON.stringify({ coverageId: 'ART-01', priority: 'P1', requiredBehavior: '假回执', dependency: '假回执注入' }))
  write('tests/e2e/workflow/cases/DM-WF-NETWORK-01.workflow.json', JSON.stringify({ id: 'DM-WF-NETWORK-01', title: '断网', supplemental: true, sourceCase: 'plan.md, CTL-07' }))
  g('add', '-A')
  g('commit', '-qm', 'wip')
  g('update-ref', 'refs/remotes/origin/wip', 'HEAD')
  g('checkout', '-q', 'main')
  return dir
}

test('buildMatrix merges main, branch refs and the ledger', () => {
  const source = fixtureRepo()
  try {
    const m = buildMatrix({
      source,
      config: { refs: [{ ref: 'origin/wip', label: 'wip', pr: '#9' }, { ref: 'origin/missing' }] },
      issue: { body: LEDGER, html_url: 'https://github.com/o/r/issues/533', title: 'ledger', updated_at: '2026-11-04T00:00:00Z' },
      nightly: { created_at: '2026-11-03T23:00:00Z', jobs: [{ name: 'E2E (real device)', conclusion: 'failure' }] },
      now: new Date('2026-11-04T00:00:00Z'),
    })
    assert.equal(m.schema, 'digitalmate/test-matrix@1')
    assert.deepEqual(m.source.refs.map((r) => r.label), ['wip'])
    const by = (layer, id) => m.cases.find((c) => c.layer === layer && c.id === id)
    assert.equal(by('UT', 'cloud-chat.test.mjs').tests, 2)
    assert.equal(by('UT-Server', 'services/agent_bridge/tests/test_flow.py').module, 'services/agent_bridge')
    assert.equal(by('REG', 'DM-REG-X').lane, 'pr-ready')
    assert.equal(by('ST', 'ST-text').suite, 'default')
    assert.equal(by('ST', 'ATT-01').status, 'stable_pass')
    assert.equal(by('ST', 'ART-01').impl, 'spec-only')
    assert.deepEqual(by('ST', 'ART-01').dependencyTags, ['artifact_fault'])
    // Ledger rows with no implementation or spec stay visible.
    assert.equal(by('ST', 'ATT-07').impl, 'missing')
    assert.equal(by('E2E', 'DM-WF-BACK-02').planId, 'UI-05')
    assert.equal(by('E2E', 'DM-WF-BACK-02').status, 'pass_recheck_blocked')
    assert.equal(by('E2E', 'DM-WF-NETWORK-01').impl, 'wip')
    assert.deepEqual(by('E2E', 'DM-WF-NETWORK-01').dependencyTags, ['env_control'])
    assert.equal(by('E2E', 'DM-WF-CHAT-01').status, 'no_result')
    assert.match(by('E2E', 'DM-WF-CHAT-01').note, /failure/)
    assert.equal(by('EVAL', 'DM-EVAL-CHAT-01').related, 'DM-WF-CHAT-01')
    assert.deepEqual(m.dfx.find((d) => d.name === 'perf'), { name: 'perf', files: 0 })
    assert.equal(m.summary.planned.total, 5)
    assert.deepEqual(m.summary.dependencies.env_control, ['E2E:CTL-07'])
  } finally {
    fs.rmSync(source, { recursive: true, force: true })
  }
})
