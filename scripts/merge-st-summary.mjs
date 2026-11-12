#!/usr/bin/env node
/**
 * Merge one Nightly's DigitalMate ST summary into the board's ST history.
 *
 *   node scripts/merge-st-summary.mjs \
 *     --st /tmp/st --run /tmp/nightly-run.json \
 *     --previous /tmp/previous-st.json --out /tmp/st.json
 *
 * `--st` is the directory produced by `gh run download` for the
 * `digitalmate-st-<run>-<attempt>` artifact; summary.json is found recursively.
 * A missing summary still produces an ERROR entry so a failed night is visible
 * instead of silently disappearing — the same contract as merge-bench-summary.
 *
 * Output schema: digitalmate/st-history@1 (newest first, 90 runs retained,
 * deduplicated by GitHub run id).
 */

import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'

export const SCHEMA = 'digitalmate/st-history@1'
export const MAX_RUNS = 90

// Hypium it-name -> scene key/title, mirroring tests/st/scenes.mjs sceneRegistry.
// The dashboard collector has no DigitalMate checkout, so the mapping lives here.
export const ST_CASES = {
  real_model_text_reply: { key: 'text', title: '文本回复' },
  real_model_txt_download_and_content: { key: 'text-file', title: '文本生成 TXT 并下载' },
  real_model_multi_turn: { key: 'multi', title: '三轮对话与上下文更新' },
  real_model_file_to_text: { key: 'file-text', title: '上传文件并提取文本' },
  real_model_file_to_file: { key: 'file-file', title: '上传 CSV 并生成转换文件' },
  real_model_structured_choice: { key: 'choice', title: '结构化选项回答并继续任务' },
  gateway_connection: { key: 'connection', title: 'Gateway 连接检查' },
  session_isolation_parallel: { key: 'session-isolation-parallel', title: '双会话并发隔离', coverageId: 'CON-01' },
  duplicate_downlink_idempotent: { key: 'duplicate-downlink-idempotent', title: '同一真实确认帧三次下行与幂等恢复', coverageId: 'CON-04', liveWireOnly: true },
  protocol_invalid_task_identity: { key: 'protocol-invalid-task-identity', title: '当前真实下行 task_id 八种异常与原事件恢复', coverageId: 'PRO-02', liveWireOnly: true },
  protocol_unknown_event_forward_compat: { key: 'protocol-unknown-event-forward-compat', title: '当前真实事件的未知类型和新增字段兼容', coverageId: 'PRO-03', liveWireOnly: true },
  protocol_malformed_optional_fields: { key: 'protocol-malformed-optional-fields', title: '不同实时增量的 segment_id/earlier 八种类型异常', coverageId: 'PRO-01', liveWireOnly: true },
  protocol_chunk_boundary_reassembly: { key: 'protocol-chunk-boundary-reassembly', title: '当前中英 Unicode/Markdown 文本与 UTF-8 continuation 分片重组', coverageId: 'PRO-06', liveWireOnly: true },
  reset_detached_recovery: { key: 'reset-detached-recovery', title: '独立房间 reset/detached 与真实断链恢复', coverageId: 'REC-03' },
  // REC-04 is temporarily excluded from the published catalog: it needs a dedicated
  // model-boundary injector deployment, and the board should not list a case that
  // no lane can run yet.
  content_refusal_context_recovery: { key: 'content-refusal-context-recovery', title: '内容拦截后同房间连续发送与历史保留', coverageId: 'REC-04', requiresBoundaryFixture: true, hidden: true },
  artifact_receipt_required: { key: 'artifact-receipt-required', title: '真实产物的缺失回执、无效 ID、不可读内容与恢复', coverageId: 'ART-01', liveWireOnly: true },
  attachment_metadata_conflict: { key: 'attachment-metadata-conflict', title: '附件元数据冲突与真实云端对照', coverageId: 'ATT-01' },
  attachment_corrupt_recovery: { key: 'attachment-corrupt-recovery', title: '损坏图片与文本恢复', coverageId: 'ATT-02' },
  attachment_image_understanding: { key: 'attachment-image-understanding', title: '真实华为 HDR/MPF 相机 JPG 原图识别', coverageId: 'ATT-03' },
  skill_default_generation: { key: 'skill-default-generation', title: '默认生成技能：Word / Excel / PPT / PDF 各交付一个文件', coverageId: 'SKL-01' },
  skill_default_parsing: { key: 'skill-default-parsing', title: '默认解析技能：文本 PDF 提取与 CSV 对账单合计', coverageId: 'SKL-02' },
}

// The full registered scenario set, so the board can show explicit-only cases as
// grey rows instead of hiding them: 13 direct-backend cases run every Nightly, six
// live-wire fault-injection cases need the loopback relay, REC-04 needs the
// model-boundary injector.
export function buildCatalog() {
  const business = Object.entries(ST_CASES).filter(([, meta]) => !meta.hidden).map(([name, meta]) => ({
    name,
    key: meta.key,
    title: meta.title,
    coverageId: meta.coverageId || '',
    group: 'business',
    nightly: !meta.liveWireOnly && !meta.requiresBoundaryFixture,
    requirement: meta.liveWireOnly ? 'live-wire' : meta.requiresBoundaryFixture ? 'model-boundary' : '',
  }))
  const component = Object.entries(COMPONENT_SUITES).flatMap(([suite, meta]) => Object.entries(meta.cases).map(([name, title]) => ({
    name,
    key: suite + '/' + name,
    title,
    coverageId: '',
    group: 'component',
    nightly: true,
    requirement: '',
    suite,
  })))
  return [...business, ...component]
}

// live-wire suite directory name -> Hypium case name (tests/st/live-wire/run.mjs).
export const LIVE_WIRE_SUITE_CASES = {
  connection: 'gateway_connection',
  business: 'duplicate_downlink_idempotent',
  identity: 'protocol_invalid_task_identity',
  forward: 'protocol_unknown_event_forward_compat',
  optional: 'protocol_malformed_optional_fields',
  chunks: 'protocol_chunk_boundary_reassembly',
  artifact: 'artifact_receipt_required',
}

// Each suite directory holds wire-assessment.json (three-way evidence verdict).
export function readLiveWireRuns(dir) {
  if (!dir || !existsSync(dir)) return []
  const runs = []
  for (const name of readdirSync(dir)) {
    const file = path.join(dir, name, 'wire-assessment.json')
    if (!existsSync(file)) continue
    const assessment = readJson(file)
    if (!assessment) continue
    const caseName = LIVE_WIRE_SUITE_CASES[name]
    if (!caseName) continue
    runs.push({
      suite: name,
      caseName,
      status: assessment.status || 'blocked',
      nativeStatus: assessment.nativeStatus || 'unavailable',
      issues: Array.isArray(assessment.issues) ? assessment.issues.map(redact).slice(0, 5) : [],
    })
  }
  return runs.sort((a, b) => a.suite.localeCompare(b.suite))
}

// Component ST suites: deterministic on-device component contracts, published by
// the separate digitalmate-st-component artifact. Suite -> its cases/titles.
export const COMPONENT_SUITES = {
  'attachment-policy': {
    title: '本地附件策略',
    cases: {
      local_attachment_blocked_types: '拒绝的类型',
      local_attachment_image_containers: '图片容器',
      local_attachment_magic_mismatch: '魔数不符',
      local_attachment_size_limits: '大小限制',
      local_attachment_supported_controls: '支持的控件',
      local_attachment_preview_decode: '预览解码',
    },
  },
  'session-proximity': {
    title: '会话邻近契约',
    cases: {
      native_topic_projection: '主题投影',
      native_capacity_draft: '容量与草稿',
      native_capacity_controls: '容量与控件',
    },
  },
  markdown: {
    title: 'Markdown 渲染',
    cases: { fixed_review_inputs_render_on_device: '固定输入渲染' },
  },
  suggestions: {
    title: '建议协议与按钮',
    cases: { metadata_replay_and_native_buttons: '元数据回放与原生按钮' },
  },
}

// Each suite directory holds summary.json (report.mjs or the suite's assess()).
export function readComponentRuns(dir) {
  if (!dir || !existsSync(dir)) return []
  const runs = []
  for (const name of readdirSync(dir)) {
    const file = path.join(dir, name, 'summary.json')
    if (!existsSync(file)) continue
    const summary = readJson(file)
    if (!summary) continue
    const meta = COMPONENT_SUITES[name]
    if (!meta) continue
    const cases = (summary.cases || []).map((item) => {
      const id = item.name || item.id
      return {
        name: id,
        title: meta.cases[id] || id,
        status: item.status || 'unknown',
        elapsedMs: typeof item.elapsedMs === 'number' ? item.elapsedMs : null,
        detail: redact(item.detail),
      }
    })
    runs.push({
      suite: name,
      title: meta.title,
      status: summary.status || 'failed',
      runId: summary.runId || '',
      issues: Array.isArray(summary.issues) ? summary.issues.map(redact).slice(0, 5) : [],
      cases,
    })
  }
  return runs.sort((a, b) => a.suite.localeCompare(b.suite))
}

function parse(argv) {
  const out = {}
  for (let i = 0; i < argv.length; i += 1) {
    if (!argv[i].startsWith('--')) continue
    const key = argv[i].slice(2)
    const value = argv[i + 1] !== undefined && !argv[i + 1].startsWith('--') ? argv[i + 1] : true
    if (value !== true) i += 1
    out[key] = value
  }
  return out
}

function readJson(file) {
  try {
    return JSON.parse(readFileSync(file, 'utf8'))
  } catch {
    return null
  }
}

function walk(dir) {
  if (!dir || !existsSync(dir)) return []
  const out = []
  for (const name of readdirSync(dir)) {
    const file = path.join(dir, name)
    let stat
    try {
      stat = statSync(file)
    } catch {
      continue
    }
    if (stat.isDirectory()) out.push(...walk(file))
    else out.push(file)
  }
  return out
}

function findSummary(dir) {
  return walk(dir).find((file) => file.endsWith(path.sep + 'summary.json') || file.endsWith('summary.json'))
}

function findDeviceResult(dir) {
  return walk(dir).find((file) => file.endsWith('device-result.json'))
}

function redact(detail) {
  return String(detail || '').slice(0, 300)
}

export function buildEntry({ summary, deviceResult, run, liveWire = [], component = [] }) {
  const cases = Array.isArray(summary?.cases) ? summary.cases : []
  const mapped = cases.map((item) => {
    const meta = ST_CASES[item.name] || {}
    return {
      name: item.name,
      key: meta.key || item.name,
      title: meta.title || item.name,
      coverageId: item.coverageId || meta.coverageId || '',
      status: item.status || 'unknown',
      elapsedMs: typeof item.elapsedMs === 'number' ? item.elapsedMs : null,
      stage: item.stage || '',
      detail: redact(item.detail),
    }
  })
  return {
    runId: run?.id ?? 'unknown',
    attempt: run?.attempt ?? 1,
    url: run?.html_url || '',
    createdAt: run?.created_at || new Date().toISOString(),
    headSha: (run?.head_sha || '').slice(0, 12),
    conclusion: run?.conclusion || 'unknown',
    st: {
      runId: summary?.runId || deviceResult?.runId || '',
      status: summary?.status || 'failed',
      mode: summary?.mode || 'business',
      runner: summary?.runner || '',
      machine: summary?.machine || '',
      issues: Array.isArray(summary?.issues) ? summary.issues.map(redact).slice(0, 5) : [],
      liveWire,
      component,
      cases: mapped,
    },
  }
}

export function buildErrorEntry({ run, message }) {
  return {
    runId: run?.id ?? 'unknown',
    attempt: run?.attempt ?? 1,
    url: run?.html_url || '',
    createdAt: run?.created_at || new Date().toISOString(),
    headSha: (run?.head_sha || '').slice(0, 12),
    conclusion: run?.conclusion || 'failure',
    st: {
      runId: '',
      status: 'failed',
      mode: 'business',
      runner: '',
      machine: '',
      issues: [message],
      cases: [],
    },
  }
}

function previousRuns(previous) {
  let raw = null
  if (typeof previous === 'string') {
    // Accept either a file path (CLI --previous) or an inline JSON document (tests).
    raw = previous.trimStart().startsWith('{') ? JSON.parse(previous) : readJson(previous)
  } else if (previous && typeof previous === 'object') {
    raw = previous
  }
  if (!raw || !Array.isArray(raw.runs)) return []
  return raw.runs.filter((item) => item && typeof item === 'object')
}

export function merge({ entry, previous }) {
  const history = previousRuns(previous)
  const merged = [entry, ...history.filter((item) => String(item.runId) !== String(entry.runId) || String(item.attempt) !== String(entry.attempt))]
    .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
    .slice(0, MAX_RUNS)
  return { schema: SCHEMA, repository: 'openJiuwen-ai/DigitalMate', updatedAt: new Date().toISOString(), catalog: buildCatalog(), runs: merged }
}

const isMain = process.argv[1] && import.meta.url === new URL(`file://${path.resolve(process.argv[1])}`).href
if (isMain) {
  const args = parse(process.argv.slice(2))
  if (!args.st || !args.run || !args.out) {
    throw new Error('Usage: --st ARTIFACT_DIR --run nightly-run.json --out st.json [--previous st.json]')
  }
  const run = readJson(args.run)
  const liveWire = readLiveWireRuns(args['live-wire'])
  const component = readComponentRuns(args.component)
  const summaryFile = findSummary(args.st)
  let entry
  if (summaryFile) {
    const summary = readJson(summaryFile)
    const deviceFile = findDeviceResult(args.st)
    entry = buildEntry({ summary, deviceResult: deviceFile ? readJson(deviceFile) : null, run, liveWire, component })
  } else {
    entry = buildErrorEntry({ run, message: 'digitalmate-st artifact has no summary.json (ST job failed before report generation)' })
  }
  const merged = merge({ entry, previous: args.previous })
  writeFileSync(args.out, `${JSON.stringify(merged, null, 2)}\n`)
  console.log(JSON.stringify({ out: args.out, runs: merged.runs.length, latest: entry.runId, status: entry.st.status }))
}
