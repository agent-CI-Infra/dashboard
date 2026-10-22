import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildDatasetFromSummary,
  defaultSummaryUrl,
  deriveStage,
  normalizeCaseSummary,
  normalizeHistory,
  summarizeCatalog,
  summaryIndexEntry,
  synthesizeHistory,
  validateCatalog,
  validateReport,
  validateSummary,
} from './ci-gates.mjs'

const summaryFixture = JSON.parse(
  readFileSync(
    new URL('../../protocol/examples/board-summary.json', import.meta.url),
    'utf8'
  )
)

const reportFixture = JSON.parse(
  readFileSync(
    new URL('../../protocol/examples/gate-report.build.json', import.meta.url),
    'utf8'
  )
)

const catalogFixture = {
  schema: 'ci-gates/case-catalog@1',
  version: 1,
  updatedAt: '2026-10-22T09:00:00Z',
  preset: 'node-web',
  cases: [
    {
      id: 'build.install',
      lane: 'build',
      layer: 'L0',
      title: 'install',
      status: 'ready',
      file: 'test/cases/build/install.test.ts',
      owner: '@acme/web-ci',
    },
    { id: 'build.lint', lane: 'build', layer: 'L0', title: 'lint', status: 'todo' },
    {
      id: 'merge.env-verify',
      lane: 'pr-merge',
      layer: 'L1',
      title: 'env verify',
      status: 'skipped',
      until: '2026-12-31',
      reason: 'envVerify disabled',
    },
  ],
}

const source = {
  id: 'acme/web',
  owner: 'acme',
  name: 'web',
  defaultBranch: 'main',
  summaryUrl: 'https://acme.github.io/web/data/summary.json',
}

const clone = (value) => JSON.parse(JSON.stringify(value))
const fixedNow = new Date('2026-10-22T12:00:00Z')

test('derives the same-repository summary URL', () => {
  assert.equal(
    defaultSummaryUrl('acme', 'web'),
    'https://acme.github.io/web/data/summary.json'
  )
  assert.equal(
    defaultSummaryUrl('acme', 'acme.github.io'),
    'https://acme.github.io/data/summary.json'
  )
  assert.equal(defaultSummaryUrl('acme', null), null)
})

test('accepts the published summary example', () => {
  assert.equal(validateSummary(clone(summaryFixture), source), true)
})

test('builds the dashboard dataset with lanes, cases and stage', () => {
  const dataset = buildDatasetFromSummary(source, clone(summaryFixture), {
    now: fixedNow,
  })

  assert.equal(dataset.schema, 'ci-gates/dashboard-repo@1')
  assert.equal(dataset.id, 'acme/web')
  assert.equal(dataset.level, 'C1')
  assert.equal(dataset.stage, 'cases-pending')
  assert.equal(dataset.sourceType, 'board-summary')
  assert.equal(dataset.branchData.length, 1)

  const branch = dataset.branchData[0]
  assert.equal(branch.name, 'main')
  assert.equal(branch.gateStatus, 'passed')
  assert.equal(branch.coverage, 72.4)
  assert.deepEqual(branch.caseSummary, {
    total: 20,
    ready: 8,
    todo: 12,
    skipped: 0,
    executed: 8,
    passed: 7,
    notRun: 1,
  })

  const categories = branch.stats.map((stat) => stat.category)
  assert.deepEqual(categories, ['build', 'ready', 'merge', 'daily', 'release'])

  const build = branch.stats.find((stat) => stat.category === 'build')
  assert.equal(build.latest.verdict, 'pass')
  assert.equal(build.latest.trust, 'trusted')
  assert.equal(build.cases.totalPlanned, 20)
  assert.equal(build.cases.planned, 8)
  assert.equal(build.cases.todo, 12)
  assert.equal(build.cases.notRun, 1)
  assert.deepEqual(build.cases.layers, [])
  assert.equal(build.daily.length, 2)
  assert.deepEqual(build.daily.map((day) => day.date), [
    '2026-10-21',
    '2026-10-22',
  ])

  const merge = branch.stats.find((stat) => stat.category === 'merge')
  assert.equal(merge.latest.verdict, 'not_applicable')
  assert.deepEqual(merge.daily, [])
})

test('falls back to the configured boardUrl when the summary omits it', () => {
  const summary = clone(summaryFixture)
  delete summary.boardUrl
  const dataset = buildDatasetFromSummary(
    { ...source, boardUrl: 'https://acme.github.io/web/' },
    summary,
    { now: fixedNow }
  )
  assert.equal(dataset.boardUrl, 'https://acme.github.io/web/')
  assert.equal(
    dataset.branchData[0].coverageLink,
    'https://acme.github.io/web/#coverage'
  )
})

test('drops provisional PR lanes from the public dataset', () => {
  const summary = clone(summaryFixture)
  summary.lanes.push({
    id: 'pr-ready',
    verdict: 'fail',
    trust: 'provisional',
    lastEvent: 'pull_request',
  })
  assert.throws(() => validateSummary(summary, source), /duplicate lane/)

  const clean = clone(summaryFixture)
  clean.lanes = clean.lanes.filter((lane) => lane.id !== 'pr-ready')
  clean.lanes.push({
    id: 'pr-ready',
    verdict: 'fail',
    trust: 'provisional',
    lastEvent: 'pull_request',
  })
  const dataset = buildDatasetFromSummary(source, clean, { now: fixedNow })
  const ready = dataset.branchData[0].stats.find(
    (stat) => stat.category === 'ready'
  )
  assert.equal(ready, undefined)
})

test('treats a PR-tagged lane without trust as provisional', () => {
  const summary = clone(summaryFixture)
  const release = summary.lanes.find((lane) => lane.id === 'release')
  delete release.trust
  release.lastEvent = 'pull_request'
  release.verdict = 'fail'
  const dataset = buildDatasetFromSummary(source, summary, { now: fixedNow })
  const lanes = dataset.branchData[0].stats.map((stat) => stat.category)
  assert.ok(!lanes.includes('release'))
  assert.equal(dataset.stage, 'cases-pending')
})

test('rejects a summary for another repository', () => {
  const summary = clone(summaryFixture)
  summary.repo = 'evil/repo'
  assert.throws(() => validateSummary(summary, source), /repo mismatch/)
})

test('rejects an unknown schema and unknown verdicts', () => {
  const wrongSchema = clone(summaryFixture)
  wrongSchema.schema = 'ci-gates/board-summary@2'
  assert.throws(() => validateSummary(wrongSchema, source), /unsupported summary schema/)

  const wrongVerdict = clone(summaryFixture)
  wrongVerdict.lanes[0].verdict = 'green'
  assert.throws(() => validateSummary(wrongVerdict, source), /unknown verdict/)
})

test('rejects duplicate lanes written with aliases', () => {
  const summary = clone(summaryFixture)
  const lane = clone(summary.lanes[0])
  lane.id = 'pr_ready'
  summary.lanes.push(lane)
  summary.lanes.push({ id: 'pr-ready', verdict: 'pass' })
  assert.throws(() => validateSummary(summary, source), /duplicate lane/)
})

test('rejects an unknown lane trust value', () => {
  const summary = clone(summaryFixture)
  summary.lanes[0].trust = 'maybe'
  assert.throws(() => validateSummary(summary, source), /unknown trust/)
})

test('rejects an oversized summary', () => {
  const summary = clone(summaryFixture)
  summary.notes = 'x'.repeat(110 * 1024)
  assert.throws(() => validateSummary(summary, source), /summary too large/)
})

test('derives onboarding stages', () => {
  const base = clone(summaryFixture)
  delete base.stage

  const failing = clone(base)
  failing.lanes[0].verdict = 'fail'
  assert.equal(deriveStage(failing, fixedNow), 'failing')

  const pending = clone(base)
  pending.caseSummary.todo = 3
  assert.equal(deriveStage(pending, fixedNow), 'cases-pending')

  const notRun = clone(base)
  notRun.caseSummary.todo = 0
  notRun.caseSummary.notRun = 2
  assert.equal(deriveStage(notRun, fixedNow), 'cases-pending')

  const observing = clone(base)
  observing.caseSummary.todo = 0
  observing.caseSummary.notRun = 0
  observing.observeUntil = '2026-11-03'
  assert.equal(deriveStage(observing, fixedNow), 'observing')

  const expired = clone(observing)
  assert.equal(deriveStage(expired, new Date('2026-12-01T00:00:00Z')), 'enforced')

  const enforced = clone(observing)
  delete enforced.observeUntil
  assert.equal(deriveStage(enforced, fixedNow), 'enforced')

  assert.equal(deriveStage({ lanes: [] }, fixedNow), 'discovered')
})

test('normalizes and merges history buckets by date', () => {
  const history = normalizeHistory([
    { date: '2026-10-22', pass: 1, fail: 0, durations: [10] },
    { date: '2026-10-21T23:00:00Z', pass: 2, fail: 1, durations: [20, 'bad'] },
    { date: '2026-10-22', pass: 3, fail: 1, durations: [30] },
    { date: 'not-a-date', pass: 9 },
  ])
  assert.deepEqual(history.map((day) => day.date), ['2026-10-21', '2026-10-22'])
  assert.equal(history[1].pass, 4)
  assert.equal(history[1].fail, 1)
  assert.deepEqual(history[1].durations, [10, 30])
  assert.equal(history[0].pass, 2)
})

test('synthesizes one bucket when a board only publishes aggregates', () => {
  const history = synthesizeHistory(
    { runs: 10, passRate: 90, avgDurationSec: 120 },
    '2026-10-22T09:00:00Z'
  )
  assert.deepEqual(history, [
    {
      date: '2026-10-22',
      pass: 9,
      fail: 1,
      ignored: 0,
      retries: 0,
      durations: [120],
    },
  ])
})

test('normalizes case counters and keeps missing keys absent', () => {
  assert.deepEqual(normalizeCaseSummary({ total: 3.4, todo: -1, passed: 2 }), {
    total: 3,
    todo: 0,
    passed: 2,
  })
  assert.equal(normalizeCaseSummary(null), null)
  assert.equal(normalizeCaseSummary({}), null)
})

test('validates a gate report against its source and run', () => {
  const report = clone(reportFixture)
  assert.equal(
    validateReport(report, source, { head_sha: report.sha }),
    true
  )
  assert.throws(
    () => validateReport({ ...report, repo: 'acme/other' }, source),
    /repo mismatch/
  )
  assert.equal(
    validateReport({ ...report, verdict: 'observe_fail' }, source),
    true
  )
  assert.equal(validateReport({ ...report, verdict: 'waived' }, source), true)
  assert.throws(
    () => validateReport({ ...report, verdict: 'not_run' }, source),
    /unknown report verdict/
  )
  assert.throws(
    () => validateReport(report, source, { head_sha: '0'.repeat(40) }),
    /sha mismatch/
  )
})

test('validates the case catalog and counts statuses', () => {
  assert.equal(validateCatalog(clone(catalogFixture)), true)
  assert.deepEqual(summarizeCatalog(catalogFixture), {
    total: 3,
    ready: 1,
    todo: 1,
    skipped: 1,
  })
  assert.throws(
    () =>
      validateCatalog({
        ...catalogFixture,
        cases: [...catalogFixture.cases, catalogFixture.cases[0]],
      }),
    /duplicate catalog case id/
  )
  assert.throws(
    () =>
      validateCatalog({
        ...catalogFixture,
        cases: [{ ...catalogFixture.cases[0], file: '' }],
      }),
    /needs file and owner/
  )
  assert.throws(
    () =>
      validateCatalog({
        ...catalogFixture,
        cases: [{ ...catalogFixture.cases[2], reason: '' }],
      }),
    /needs until and reason/
  )
})

test('builds a compact index entry', () => {
  const dataset = buildDatasetFromSummary(source, clone(summaryFixture), {
    now: fixedNow,
  })
  assert.deepEqual(summaryIndexEntry(source, dataset), {
    id: 'acme/web',
    name: 'web',
    owner: 'acme',
    file: 'acme/web.json',
    sourceUrl: 'https://acme.github.io/web/data/summary.json',
    sourceType: 'board-summary',
    level: 'C1',
    stage: 'cases-pending',
    boardUrl: 'https://acme.github.io/web/',
    windowDays: 30,
  })
})
