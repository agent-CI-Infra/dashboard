import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { buildEntry, buildErrorEntry, merge, readLiveWireRuns, SCHEMA, ST_CASES } from './merge-st-summary.mjs'

const RUN = {
  id: 43039010304,
  attempt: 1,
  html_url: 'https://github.com/openJiuwen-ai/DigitalMate/actions/runs/43039010304',
  created_at: '2026-11-11T14:29:49Z',
  head_sha: 'cdde1b3a3454fe8156ec700e7e9c5ee473df196b',
  conclusion: 'success',
}

const SUMMARY = {
  status: 'passed',
  mode: 'business',
  runId: 'ST-1794408431982-814b97',
  runner: 'digitalmate-win-st-02',
  machine: 'PC-20261103SYCB',
  roomId: 'room-x',
  cases: [
    { name: 'real_model_text_reply', status: 'passed', elapsedMs: 6197, stage: 'validate-text', detail: 'Reply marker verified', coverageId: '' },
    { name: 'real_model_txt_download_and_content', status: 'passed', elapsedMs: 17090, stage: 'validate-file', detail: 'content matched', coverageId: '' },
  ],
  issues: [],
}

test('maps Hypium case names to scene keys and titles', () => {
  const entry = buildEntry({ summary: SUMMARY, run: RUN })
  assert.equal(entry.runId, 43039010304)
  assert.equal(entry.headSha, 'cdde1b3a3454')
  assert.equal(entry.conclusion, 'success')
  assert.equal(entry.st.status, 'passed')
  assert.deepEqual(
    entry.st.cases.map((c) => [c.name, c.key, c.title, c.status]),
    [
      ['real_model_text_reply', 'text', '文本回复', 'passed'],
      ['real_model_txt_download_and_content', 'text-file', '文本生成 TXT 并下载', 'passed'],
    ],
  )
})

test('keeps coverage ids and truncates details', () => {
  const summary = {
    ...SUMMARY,
    cases: [{ name: 'duplicate_downlink_idempotent', status: 'failed', elapsedMs: 10, stage: 'x', detail: 'x'.repeat(500), coverageId: 'CON-04' }],
    issues: ['i'.repeat(500)],
  }
  const entry = buildEntry({ summary, run: RUN })
  assert.equal(entry.st.cases[0].coverageId, 'CON-04')
  assert.equal(entry.st.cases[0].detail.length, 300)
  assert.equal(entry.st.issues[0].length, 300)
})

test('unknown case names pass through without a title', () => {
  const entry = buildEntry({ summary: { ...SUMMARY, cases: [{ name: 'brand_new_case', status: 'passed' }] }, run: RUN })
  assert.deepEqual(entry.st.cases.map((c) => [c.name, c.key, c.title]), [['brand_new_case', 'brand_new_case', 'brand_new_case']])
})

test('every registry entry carries key and title', () => {
  for (const [name, meta] of Object.entries(ST_CASES)) {
    assert.ok(meta.key, `${name} missing key`)
    assert.ok(meta.title, `${name} missing title`)
  }
})

test('merge dedupes by run id and attempt, newest first, caps history', () => {
  const previous = {
    schema: SCHEMA,
    runs: [
      { runId: '1', attempt: 1, createdAt: '2026-11-10T00:00:00Z', st: { cases: [] } },
      { runId: '2', attempt: 1, createdAt: '2026-11-11T00:00:00Z', st: { cases: [] } },
    ],
  }
  const entry = buildEntry({ summary: SUMMARY, run: RUN })
  const merged = merge({ entry, previous: JSON.stringify(previous) })
  assert.equal(merged.schema, SCHEMA)
  assert.equal(merged.runs.length, 3)
  assert.equal(merged.runs[0].runId, 43039010304)
  // same run id + same attempt replaces; same id different attempt keeps both
  const again = merge({ entry, previous: JSON.stringify(merged) })
  assert.equal(again.runs.length, 3)
  const attempt2 = merge({ entry: { ...entry, attempt: 2 }, previous: JSON.stringify(merged) })
  assert.equal(attempt2.runs.length, 4)
  const many = Array.from({ length: 100 }, (_, i) => ({ runId: String(i), attempt: 1, createdAt: `2026-11-${String((i % 28) + 1).padStart(2, '0')}T00:00:00Z`, st: { cases: [] } }))
  const capped = merge({ entry, previous: JSON.stringify({ runs: many }) })
  assert.equal(capped.runs.length, 90)
})

test('missing summary produces a visible ERROR entry', () => {
  const entry = buildErrorEntry({ run: { ...RUN, conclusion: 'failure' }, message: 'no summary' })
  assert.equal(entry.st.status, 'failed')
  assert.equal(entry.st.cases.length, 0)
  assert.match(entry.st.issues[0], /no summary/)
})

test('CLI merges a downloaded artifact directory', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'st-merge-'))
  try {
    const artifact = path.join(dir, 'artifact')
    fs.mkdirSync(artifact, { recursive: true })
    fs.writeFileSync(path.join(artifact, 'summary.json'), JSON.stringify(SUMMARY))
    fs.writeFileSync(path.join(artifact, 'device-result.json'), JSON.stringify({ runId: 'ST-device' }))
    const runFile = path.join(dir, 'run.json')
    fs.writeFileSync(runFile, JSON.stringify(RUN))
    const out = path.join(dir, 'st.json')
    const result = execFileSync(process.execPath, [new URL('./merge-st-summary.mjs', import.meta.url).pathname, '--st', artifact, '--run', runFile, '--out', out], { encoding: 'utf8' })
    const line = JSON.parse(result.trim())
    assert.equal(line.runs, 1)
    assert.equal(line.status, 'passed')
    const written = JSON.parse(fs.readFileSync(out, 'utf8'))
    assert.equal(written.schema, SCHEMA)
    assert.equal(written.runs[0].st.cases.length, 2)
    // missing artifact -> error entry, still writes history
    const out2 = path.join(dir, 'st2.json')
    execFileSync(process.execPath, [new URL('./merge-st-summary.mjs', import.meta.url).pathname, '--st', path.join(dir, 'empty'), '--run', runFile, '--out', out2], { encoding: 'utf8' })
    const written2 = JSON.parse(fs.readFileSync(out2, 'utf8'))
    assert.equal(written2.runs[0].st.status, 'failed')
    assert.match(written2.runs[0].st.issues[0], /summary\.json/)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('live-wire suite directories merge into the run entry', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'st-lw-'))
  try {
    const write = (suite, assessment) => {
      fs.mkdirSync(path.join(dir, suite), { recursive: true })
      fs.writeFileSync(path.join(dir, suite, 'wire-assessment.json'), JSON.stringify(assessment))
    }
    write('connection', { status: 'passed', nativeStatus: 'passed', issues: [] })
    write('business', { status: 'blocked', nativeStatus: 'passed', issues: ['transport error'] })
    write('unknown-suite', { status: 'passed' })
    fs.mkdirSync(path.join(dir, 'empty'), { recursive: true })
    const runs = readLiveWireRuns(dir)
    assert.deepEqual(runs.map((r) => [r.suite, r.caseName, r.status]), [
      ['business', 'duplicate_downlink_idempotent', 'blocked'],
      ['connection', 'gateway_connection', 'passed'],
    ])
    assert.deepEqual(readLiveWireRuns(path.join(dir, 'missing')), [])
    const entry = buildEntry({ summary: SUMMARY, run: RUN, liveWire: runs })
    assert.equal(entry.st.liveWire.length, 2)
    assert.equal(entry.st.liveWire[0].issues[0], 'transport error')
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})
