#!/usr/bin/env node
// Build the DigitalMate unified test-case matrix from a local DigitalMate checkout.
//
// Inputs (all local; the script itself never calls the network):
//   --source <dir>        DigitalMate checkout whose HEAD is main; extra refs must be fetched.
//   --config <file>       config/digitalmate-test-matrix.json (extra refs, ledger issue, lanes).
//   --issue <file>        GitHub issue JSON of the ledger issue (optional).
//   --nightly <file>      GitHub jobs JSON of the latest scheduled nightly run (optional).
//   --out <file>          Output JSON (site/data/test-matrix.json on the digitalmate branch).
//
// Published fields are limited to case identity, titles, file paths, owners, status, dependency
// labels and links to issues/PRs. Prompts, fixtures, endpoints, logs and credentials are never read
// into the output.

import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

export const SCHEMA = 'digitalmate/test-matrix@1'

export const STATUS = {
  stable_pass: '稳定通过',
  pass_recorded: '有历史 PASS，当前版待复核',
  pass_recheck_blocked: '有历史 PASS，当前版复核阻塞',
  fail: '实测 FAIL',
  flaky: '实测不稳定',
  blocked: '已尝试，阻塞',
  not_run: '未真实执行',
  no_result: '无当前结果',
  ci_lane: '由 CI 车道执行',
}

// Ordered: the first matching rule names the dependency cluster.
export const DEPENDENCY_RULES = [
  ['identity', '第二账号/多身份', /双账号|第二账号|切账号|身份/],
  ['frame', '帧/协议注入', /帧|分片|协议\/二进制|协议边界/],
  ['tool_fault', '工具故障注入', /工具(故障|中断)?注入|工具故障|沙箱检索工具|可撤销沙箱工具/],
  ['model_fault', '模型故障注入', /模型(故障|空闲|超时)|模型.*注入/],
  ['artifact_fault', '产物/附件/一次性失败注入', /假回执|阶段注入|交付.*失败注入|图片故障注入|一次性失败注入|上传延迟/],
  ['env_control', '可控测试环境（重启/网络/时序/配置）', /重启|网络控制|时区|顺序控制|延迟控制|预算配置|运行时配置|停止作用域|独立测试绑定/],
  ['system_ui', '系统 UI/系统配置控制', /picker|撤权|提醒资源|系统时钟|读屏|系统配置|通知|授权/],
  ['release', '安装/发布环境', /清洁安装|签名旧版|正式包|发布渠道|法律协议/],
  ['fixture', '受控测试样本', /受控网页|安全测试样本|固定.*夹具|Unicode/],
]

export function classifyDependency(text) {
  const value = String(text || '').trim()
  if (!value) return []
  const hits = DEPENDENCY_RULES.filter(([, , re]) => re.test(value)).map(([key]) => key)
  return hits.length ? hits : ['other']
}

export function parseLedgerStatus(text) {
  const value = String(text || '')
  if (/稳定通过/.test(value)) return { status: 'stable_pass', dependency: '' }
  if (/已有 PASS/.test(value)) return { status: /BLOCKED/.test(value) ? 'pass_recheck_blocked' : 'pass_recorded', dependency: '' }
  if (/不稳定/.test(value)) return { status: 'flaky', dependency: '' }
  if (/FAIL/.test(value)) return { status: 'fail', dependency: '' }
  if (/BLOCKED|阻塞/.test(value)) return { status: 'blocked', dependency: '' }
  if (/未真实执行/.test(value)) return { status: 'not_run', dependency: value.split(/[；;]/).slice(1).join('；').trim() }
  return { status: 'no_result', dependency: '' }
}

const PLAN_ID = /\b(UI|CTL|CON|ART|PRO|ATT|CAP|REC)-\d{2}\b/

// Parse the two markdown tables ("ST 用例" / "E2E 用例") of the ledger issue.
export function parseLedger(body) {
  const ledger = { ST: new Map(), E2E: new Map() }
  let layer = null
  for (const line of String(body || '').split(/\r?\n/)) {
    if (/^ST 用例/.test(line)) layer = 'ST'
    else if (/^E2E 用例/.test(line)) layer = 'E2E'
    else if (/^#{1,6} /.test(line) || /^<!--/.test(line)) layer = null
    if (!layer || !line.startsWith('|')) continue
    const cells = line.split('|').slice(1, -1).map((c) => c.trim())
    if (cells.length < 4 || !PLAN_ID.test(cells[0]) || cells[0].length > 8) continue
    const { status, dependency } = parseLedgerStatus(cells[2])
    const evidence = (cells[4] || '').match(/\((https:\/\/github\.com\/[^)\s]+)\)/)
    ledger[layer].set(cells[0], {
      planId: cells[0],
      title: cells[1],
      progress: cells[2],
      status,
      dependency,
      prs: [...new Set((cells[3] || '').match(/#\d+/g) || [])],
      evidence: evidence ? evidence[1] : null,
    })
  }
  return ledger
}

// ------------------------------------------------------------------ git readers
function git(source, args) {
  return execFileSync('git', ['-C', source, ...args], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
}
function refExists(source, ref) {
  try { git(source, ['rev-parse', '--verify', '--quiet', `${ref}^{commit}`]); return true } catch { return false }
}
function listFiles(source, ref, dir) {
  try { return git(source, ['ls-tree', '-r', '--name-only', ref, '--', dir]).split('\n').filter(Boolean) } catch { return [] }
}
function readFile(source, ref, file) {
  try { return git(source, ['show', `${ref}:${file}`]) } catch { return null }
}
function readJson(source, ref, file) {
  const text = readFile(source, ref, file)
  if (text == null) return null
  try { return JSON.parse(text) } catch { return null }
}

export function countNodeTests(text) {
  return (String(text).match(/^\s*(?:test|it)(?:\.(?:skip|only|todo))?\(/gm) || []).length
}
export function countPyTests(text) {
  return (String(text).match(/^\s*(?:async\s+)?def test_\w+/gm) || []).length
}
export function parseScenes(text) {
  const scenes = []
  for (const m of String(text || '').matchAll(/\{[^{}]*key:'([^']+)'[^{}]*\}/g)) {
    const raw = m[0]
    const get = (k) => (raw.match(new RegExp(`${k}:'([^']*)'`)) || [])[1] || ''
    scenes.push({ key: m[1], name: get('name'), title: get('title'), coverageId: get('coverageId'), default: /default:true/.test(raw) })
  }
  return scenes
}
export function parseCatalog(text) {
  const cases = []
  let current = null
  for (const line of String(text || '').split(/\r?\n/)) {
    const start = line.match(/^\s*-\s+id:\s*(\S+)/)
    if (start) { current = { id: start[1] }; cases.push(current); continue }
    const field = line.match(/^\s+(\w+):\s*(.+?)\s*$/)
    if (current && field) current[field[1]] = field[2]
  }
  return cases
}
const moduleOf = (file) => path.basename(file).replace(/\.test\.mjs$/, '').split('-')[0]

// ------------------------------------------------------------------ builder
export function buildMatrix({ source, config, issue, nightly, now = new Date() }) {
  const main = 'HEAD'
  const refs = [{ ref: main, label: 'main' }]
  for (const entry of config.refs || []) {
    if (refExists(source, entry.ref)) refs.push({ ref: entry.ref, label: entry.label || entry.ref.replace(/^origin\//, ''), pr: entry.pr || null })
  }
  const ledger = parseLedger(issue?.body)
  const ledgerUrl = issue?.html_url || null
  const jobs = new Map((nightly?.jobs || []).map((j) => [j.name, j.conclusion || j.status]))
  const nightlyDate = nightly?.created_at || null
  const cases = []
  const push = (row) => cases.push({ priority: null, owner: null, planId: null, suite: null, dependency: '', dependencyTags: [], prs: [], evidence: null, issue: null, note: '', tests: null, ...row })

  // UT (Node, device-free)
  for (const file of listFiles(source, main, 'tests/unit').filter((f) => f.endsWith('.test.mjs'))) {
    push({ layer: 'UT', id: file.replace(/^tests\/unit\//, ''), title: moduleOf(file), module: moduleOf(file), file, impl: 'main', status: 'ci_lane', lane: 'ut.yml', tests: countNodeTests(readFile(source, main, file)) })
  }
  // Server-side Python tests under harness/personal-agent
  for (const file of listFiles(source, main, 'harness/personal-agent').filter((f) => /\/test_[^/]+\.py$/.test(f))) {
    const module = path.dirname(file).replace(/^harness\/personal-agent\//, '').replace(/\/tests?$/, '')
    push({ layer: 'UT-Server', id: file.replace(/^harness\/personal-agent\//, ''), title: path.basename(file, '.py'), module, file, impl: 'main', status: 'ci_lane', lane: 'server-regression.yml', tests: countPyTests(readFile(source, main, file)) })
  }
  // Registered protocol regressions
  for (const c of parseCatalog(readFile(source, main, 'tests/cases/case-catalog.yaml'))) {
    push({ layer: 'REG', id: c.id, title: c.id.replace(/^DM-REG-/, '').toLowerCase().replace(/-/g, ' '), module: c.layer || '', file: c.file || '', impl: 'main', status: 'ci_lane', lane: c.lane || '', owner: c.owner || null, issue: c.issue || null, note: c.status ? `catalog status: ${c.status}` : '' })
  }

  // ST: scenes (implemented) + specifications (spec-only) across refs, keyed by planId or scene key.
  const st = new Map()
  for (const { ref, label, pr } of refs) {
    for (const s of parseScenes(readFile(source, ref, 'tests/st/scenes.mjs'))) {
      const key = s.coverageId || `ST-${s.key}`
      const existing = st.get(key)
      // A spec read from an earlier ref is upgraded once any later ref implements the scene.
      if (!existing || existing.impl === 'spec-only') st.set(key, { ...existing, key, planId: s.coverageId || null, title: s.title, selector: s.key, suite: s.coverageId ? 'explicit' : (s.default ? 'default' : 'quarantined'), impl: label, implPr: pr, file: 'tests/st/scenes.mjs' })
    }
    for (const file of listFiles(source, ref, 'tests/st/specifications').filter((f) => f.endsWith('.case.json'))) {
      const spec = readJson(source, ref, file)
      if (!spec?.coverageId) continue
      const row = st.get(spec.coverageId) || { key: spec.coverageId, planId: spec.coverageId, title: spec.requiredBehavior, selector: null, suite: 'explicit', impl: 'spec-only', implPr: null }
      row.specFile = row.specFile || file
      row.specRef = row.specRef || label
      row.priority = row.priority || spec.priority || null
      row.specDependency = row.specDependency || spec.dependency || ''
      row.file = row.file || file
      st.set(spec.coverageId, row)
    }
  }
  for (const id of ledger.ST.keys()) if (!st.has(id)) st.set(id, { key: id, planId: id, title: ledger.ST.get(id).title, suite: 'explicit', impl: 'missing', file: '' })
  for (const row of st.values()) {
    const l = row.planId ? ledger.ST.get(row.planId) : null
    const dependency = l?.dependency || (l && l.status !== 'not_run' ? '' : row.specDependency || '')
    const stJob = jobs.get('DigitalMate ST (Windows)')
    push({
      layer: 'ST', id: row.planId || row.key, planId: row.planId, title: l?.title || row.title, module: (row.planId || 'DEF').split('-')[0],
      suite: row.suite, file: row.file, impl: row.impl, priority: row.priority || null,
      status: l ? l.status : 'no_result', progress: l?.progress || '', dependency, dependencyTags: classifyDependency(dependency),
      prs: l?.prs || (row.implPr ? [row.implPr] : []), evidence: l?.evidence || null, lane: 'nightly.yml · DigitalMate ST (Windows)',
      note: !l && stJob ? `最近一次定时 nightly 的 ST 作业：${stJob}` : (row.suite === 'quarantined' ? '因 source-changed/stale 退出默认套件，仅显式 -Cases multi 运行' : ''),
    })
  }

  // E2E workflows across refs
  const e2e = new Map()
  for (const { ref, label, pr } of refs) {
    for (const file of listFiles(source, ref, 'tests/e2e/workflow/cases').filter((f) => f.endsWith('.workflow.json'))) {
      const wf = readJson(source, ref, file)
      if (!wf?.id || e2e.has(wf.id)) continue
      const planId = (String(wf.sourceCase || '').match(PLAN_ID) || String(`${wf.title} ${wf.goal || ''}`).match(PLAN_ID) || [])[0] || null
      e2e.set(wf.id, { wf, file, label, pr, planId })
    }
  }
  const e2eJob = jobs.get('E2E (real device)')
  for (const { wf, file, label, pr, planId } of e2e.values()) {
    const l = planId ? ledger.E2E.get(planId) : null
    const criteria = wf.score?.criteria?.length || 0
    push({
      layer: 'E2E', id: wf.id, planId, title: wf.title, module: wf.id.replace(/^DM-WF-/, '').replace(/-\d+$/, ''),
      suite: wf.supplemental === true ? 'explicit' : 'default', mode: wf.mode || '', priority: wf.priority || null,
      owner: wf.owner || null, file, impl: label, status: l ? l.status : 'no_result', progress: l?.progress || '',
      dependency: l?.dependency || '', dependencyTags: classifyDependency(l?.dependency), prs: l?.prs || (pr && label !== 'main' ? [pr] : []),
      evidence: l?.evidence || null, issue: /^https:\/\/github\.com\//.test(wf.sourceCase || '') ? String(wf.sourceCase).split(/\s*;\s*/)[0] : null,
      lane: 'nightly.yml · E2E (real device)', steps: Array.isArray(wf.steps) ? wf.steps.length : null, scoreCriteria: criteria,
      note: !l && e2eJob ? `最近一次定时 nightly 的 E2E 作业：${e2eJob}` : '',
    })
  }
  for (const [id, l] of ledger.E2E) {
    if (![...e2e.values()].some((e) => e.planId === id)) push({ layer: 'E2E', id, planId: id, title: l.title, module: id.split('-')[0], suite: 'explicit', impl: 'missing', status: l.status, progress: l.progress, dependency: l.dependency, dependencyTags: classifyDependency(l.dependency), prs: l.prs, evidence: l.evidence })
  }

  // Eval tasks
  for (const file of listFiles(source, main, 'tests/eval/tasks').filter((f) => f.endsWith('.task.json'))) {
    const t = readJson(source, main, file)
    if (!t?.id) continue
    const related = t.adapter?.workflowId || (String(t.source || '').match(/DM-WF-[A-Z0-9-]+?(?=\.workflow|$|\s)/) || [])[0] || null
    push({ layer: 'EVAL', id: t.id, title: t.title, module: t.suite || '', suite: t.suite || '', priority: t.priority || null, owner: t.owner || null, file, impl: 'main', status: 'no_result', lane: 'eval.yml', trials: t.trials ?? null, graders: (t.graders || []).length, adapter: t.adapter?.kind || '', related, note: /合成|synthetic/i.test(t.title || '') ? '合成 fixture，不代表产品质量' : '' })
  }

  // DFX suites: count real case files beyond README.
  const dfx = ['perf', 'reliability', 'stability'].map((name) => ({ name, files: listFiles(source, main, `tests/dfx/${name}`).filter((f) => !/README\.md$/.test(f)).length }))

  const summary = summarize(cases)
  return {
    schema: SCHEMA,
    generatedAt: now.toISOString(),
    source: { repository: 'openJiuwen-ai/DigitalMate', main: git(source, ['rev-parse', '--short=8', main]).trim(), refs: refs.slice(1).map(({ ref, label, pr }) => ({ label, pr, sha: git(source, ['rev-parse', '--short=8', ref]).trim() })) },
    ledger: issue ? { url: ledgerUrl, title: issue.title, updatedAt: issue.updated_at, st: ledger.ST.size, e2e: ledger.E2E.size } : null,
    nightly: nightly ? { createdAt: nightlyDate, url: nightly.html_url || null, jobs: Object.fromEntries(jobs) } : null,
    statusLabels: STATUS,
    dependencyLabels: Object.fromEntries([...DEPENDENCY_RULES.map(([k, label]) => [k, label]), ['other', '其他']]),
    dfx,
    summary,
    cases,
  }
}

export function summarize(cases) {
  const layers = {}
  for (const c of cases) {
    const l = (layers[c.layer] ||= { cases: 0, tests: 0, byStatus: {}, bySuite: {}, byImpl: {} })
    l.cases += 1
    if (typeof c.tests === 'number') l.tests += c.tests
    l.byStatus[c.status] = (l.byStatus[c.status] || 0) + 1
    if (c.suite) l.bySuite[c.suite] = (l.bySuite[c.suite] || 0) + 1
    l.byImpl[c.impl] = (l.byImpl[c.impl] || 0) + 1
  }
  const dependencies = {}
  for (const c of cases) for (const tag of c.dependencyTags || []) (dependencies[tag] ||= []).push(`${c.layer}:${c.planId || c.id}`)
  const planned = cases.filter((c) => c.planId && (c.layer === 'ST' || c.layer === 'E2E'))
  return { layers, dependencies, planned: { total: planned.length, byStatus: planned.reduce((a, c) => ((a[c.status] = (a[c.status] || 0) + 1), a), {}) } }
}

// ------------------------------------------------------------------ CLI
function parseArgs(argv) {
  const out = {}
  for (let i = 0; i < argv.length; i++) if (argv[i].startsWith('--')) out[argv[i].slice(2)] = argv[i + 1]?.startsWith('--') || argv[i + 1] == null ? true : argv[++i]
  return out
}
const readOptional = (file) => (file && fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : null)

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = parseArgs(process.argv.slice(2))
  if (!args.source || !args.out) {
    console.error('usage: digitalmate-test-matrix.mjs --source <DigitalMate checkout> --out <file> [--config f] [--issue f] [--nightly f]')
    process.exit(2)
  }
  const config = readOptional(args.config) || {}
  const matrix = buildMatrix({ source: args.source, config, issue: readOptional(args.issue), nightly: readOptional(args.nightly) })
  if (matrix.source.repository !== 'openJiuwen-ai/DigitalMate') throw new Error('unexpected repository')
  fs.mkdirSync(path.dirname(path.resolve(args.out)), { recursive: true })
  fs.writeFileSync(args.out, `${JSON.stringify(matrix, null, 1)}\n`)
  const p = matrix.summary.planned
  console.log(`test matrix: ${matrix.cases.length} rows; planned ST/E2E ${p.total}; ${JSON.stringify(p.byStatus)}`)
}
