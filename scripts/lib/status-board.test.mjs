import assert from 'node:assert/strict'
import test from 'node:test'

import {
  boardCaseSummary,
  boardGateMeta,
  laneMapOf,
  lanesToBranch,
  snapshotCoverage,
} from './status-board.mjs'

/** Trimmed-down shape of the DigitalMate public snapshot. */
function snapshot(overrides = {}) {
  return {
    sections: {
      ci: {
        data: {
          gates: {
            lanes: [
              {
                lane: 'pr-ready',
                verdict: 'pass',
                event: 'push',
                sha: 'db299fa',
                level: 'C1',
                results: [{ name: 'bash scripts/ci/node-tests.sh', status: 'pass' }],
              },
            ],
            cases: { total: 0, ready: 0, todo: 0, skipped: 0 },
            stage: 'enforced',
            verdict: 'pass',
            run: {
              id: 41087955112,
              url: 'https://example.test/actions/runs/41087955112',
              event: 'push',
              branch: 'main',
              sha: 'db299fa',
              created_at: '2026-10-30T16:13:49Z',
            },
            ...overrides,
          },
        },
      },
      tests: {
        data: {
          coverage: {
            source: 'Actions coverage summaries',
            value: {
              lines_pct: 82.44,
              branches_pct: 70.1,
              functions_pct: null,
            },
          },
        },
      },
    },
  }
}

test('maps the board gate lanes onto overview columns', () => {
  const meta = boardGateMeta(snapshot(), 'main', 'main')
  assert.equal(meta.level, 'C1')
  assert.equal(meta.stage, 'enforced')
  assert.deepEqual(meta.caseSummary, {
    total: 0,
    ready: 0,
    todo: 0,
    skipped: 0,
    executed: 0,
    passed: 0,
    notRun: 0,
  })
  assert.deepEqual(meta.latestByCategory.get('ready'), {
    verdict: 'pass',
    event: 'push',
    runId: 41087955112,
    runAt: '2026-10-30T16:13:49Z',
    runUrl: 'https://example.test/actions/runs/41087955112',
    sha: 'db299fa',
    results: [{ name: 'bash scripts/ci/node-tests.sh', status: 'pass' }],
  })
  assert.equal(meta.latestByCategory.has('merge'), false)
})

test('keeps repo-level gate metadata on the measured branch only', () => {
  assert.notEqual(boardGateMeta(snapshot(), 'main', 'main'), null)
  assert.equal(boardGateMeta(snapshot(), 'legacy', 'main'), null)
})

test('assumes the default branch when the run omits its branch', () => {
  const data = snapshot()
  delete data.sections.ci.data.gates.run.branch
  assert.notEqual(boardGateMeta(data, 'main', 'main'), null)
  assert.equal(boardGateMeta(data, 'legacy', 'main'), null)
})

test('ignores unknown levels, stages and lanes but keeps the summary', () => {
  const meta = boardGateMeta(
    snapshot({
      lanes: [{ lane: 'unknown', verdict: 'fail', level: 'C9' }],
      stage: 'shipping',
    }),
    'main',
    'main'
  )
  assert.equal(meta.level, undefined)
  assert.equal(meta.stage, undefined)
  assert.equal(meta.latestByCategory.size, 0)
  assert.equal(meta.caseSummary.total, 0)
})

test('normalizes partial case counters and rejects a missing block', () => {
  assert.deepEqual(boardCaseSummary({ cases: { total: 3, ready: 2 } }), {
    total: 3,
    ready: 2,
    todo: 0,
    skipped: 0,
    executed: 0,
    passed: 0,
    notRun: 0,
  })
  assert.equal(boardCaseSummary({}), null)
  assert.equal(boardCaseSummary(null), null)
})

test('reads the coverage the board already resolved', () => {
  assert.deepEqual(snapshotCoverage(snapshot()), {
    coverage: 82.4,
    coverageDetail: { lines: 82.44, branches: 70.1 },
    coverageSource: 'Actions coverage summaries',
  })
  assert.equal(
    snapshotCoverage({ sections: { tests: { data: { coverage: { value: null } } } } }),
    null
  )
  assert.equal(snapshotCoverage({}), null)
})

/** Two days of lane buckets, close to the DigitalMate board shape. */
function laneData() {
  return {
    branch: 'main',
    days: ['2026-10-30', '2026-10-31'],
    lanes: [
      {
        key: 'pr',
        summary: { total: 2, success_rate: 100 },
        days: [
          [
            { outcome: 'success', duration_s: 10 },
            { outcome: 'cancelled', duration_s: 5 },
          ],
          [],
        ],
      },
      {
        key: 'main',
        summary: { total: 1, success_rate: 100 },
        days: [[{ outcome: 'success', duration_s: 20 }], []],
      },
      {
        key: 'daily',
        summary: { total: 1, success_rate: null },
        days: [[], [{ outcome: 'cancelled', duration_s: 30 }]],
      },
      {
        key: 'release',
        summary: { total: 0, success_rate: null },
        days: [[], []],
      },
    ],
  }
}

test('keeps the SD-compatible lane mapping by default', () => {
  const source = { defaultBranch: 'main', boardBaseUrl: 'https://board.test/' }
  const branch = lanesToBranch(laneData(), source)
  assert.equal(branch.name, 'main')
  assert.equal(branch.gateStatus, 'passed')
  assert.equal(branch.coverage, null)
  assert.deepEqual(
    branch.stats.map((stat) => stat.category),
    ['ready', 'merge', 'daily']
  )
  assert.deepEqual(branch.stats[0].daily[0], {
    date: '2026-10-30',
    pass: 1,
    fail: 0,
    ignored: 1,
    retries: 0,
    durations: [10, 5],
  })
  assert.equal(branch.stats[2].daily[0].ignored, 1)
  assert.equal(branch.stats[0].link, 'https://board.test/#ci')
})

test('a source laneMap only keeps the lanes that board really gates', () => {
  const source = {
    defaultBranch: 'main',
    laneMap: { pr: 'ready', daily: 'daily' },
  }
  assert.deepEqual(laneMapOf(source), { pr: 'ready', daily: 'daily' })
  const branch = lanesToBranch(laneData(), source)
  assert.deepEqual(
    branch.stats.map((stat) => stat.category),
    ['ready', 'daily']
  )
  assert.equal(branch.stats.some((stat) => stat.category === 'merge'), false)
  assert.equal(branch.stats.some((stat) => stat.category === 'release'), false)
})

test('returns null when no mapped lane has a day bucket', () => {
  assert.equal(lanesToBranch(laneData(), { laneMap: { build: 'build' } }), null)
  assert.equal(lanesToBranch({ lanes: [] }, {}), null)
})
