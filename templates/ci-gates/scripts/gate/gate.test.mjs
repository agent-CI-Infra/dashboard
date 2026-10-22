import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
  mkdirSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  parseCatalogYaml,
  summarizeCatalog,
} from './gate.mjs'

const GATE = fileURLToPath(new URL('./gate.mjs', import.meta.url))
const TEMP_DIRS = []

function makeRepo(config) {
  const dir = mkdtempSync(path.join(tmpdir(), 'ci-gates-'))
  TEMP_DIRS.push(dir)
  mkdirSync(path.join(dir, '.github'), { recursive: true })
  writeFileSync(
    path.join(dir, '.github', 'ci-gates.json'),
    `${JSON.stringify(config, null, 2)}\n`
  )
  return dir
}

function writeCatalog(dir, cases) {
  const file = path.join(dir, 'test', 'cases', 'case-catalog.yaml')
  mkdirSync(path.dirname(file), { recursive: true })
  const lines = ['schema: ci-gates/case-catalog@1', 'version: 1', 'cases:']
  for (const entry of cases) {
    lines.push(`  - id: ${entry.id}`)
    for (const key of ['lane', 'layer', 'title', 'status', 'file', 'owner']) {
      if (entry[key] !== undefined) lines.push(`    ${key}: '${entry[key]}'`)
    }
  }
  writeFileSync(file, `${lines.join('\n')}\n`)
}

function runGate(dir, args, env = {}) {
  return spawnSync(process.execPath, [GATE, ...args], {
    cwd: dir,
    encoding: 'utf8',
    env: {
      ...process.env,
      GITHUB_SHA: 'a'.repeat(40),
      GITHUB_REPOSITORY: 'acme/test',
      GITHUB_RUN_ID: '42',
      GITHUB_RUN_ATTEMPT: '1',
      GITHUB_SERVER_URL: 'https://github.com',
      ...env,
    },
  })
}

function readReport(dir, lane) {
  return JSON.parse(
    readFileSync(path.join(dir, 'gate-reports', `${lane}.json`), 'utf8')
  )
}

after(() => {
  for (const dir of TEMP_DIRS) rmSync(dir, { recursive: true, force: true })
})

test('parses the catalog YAML subset', () => {
  const catalog = parseCatalogYaml(
    `schema: ci-gates/case-catalog@1
version: 1
cases:
  - id: build.login
    lane: build
    layer: L1
    title: '登录: 成功'
    status: ready
    file: test/login.test.ts
    owner: '@alice'
    tags: [unit, '@core']
  - id: daily.legacy
    lane: daily
    layer: L3
    title: 旧入口
    status: todo
`,
    'catalog.yaml'
  )
  assert.equal(catalog.cases.length, 2)
  assert.equal(catalog.cases[0].title, '登录: 成功')
  assert.deepEqual(catalog.cases[0].tags, ['unit', '@core'])
  assert.equal(catalog.cases[1].status, 'todo')
  assert.deepEqual(summarizeCatalog(catalog), {
    total: 2,
    ready: 1,
    todo: 1,
    skipped: 0,
  })
})

test('runs a passing lane and writes a schema-shaped report', () => {
  const dir = makeRepo({
    version: 1,
    repo: 'acme/test',
    level: 'C1',
    preset: 'node-web',
    caseCatalog: 'test/cases/case-catalog.yaml',
    caseRoot: 'test',
    lanes: {
      build: { commands: ['node -e "process.exit(0)"'] },
    },
  })
  writeCatalog(dir, [
    {
      id: 'build.unit',
      lane: 'build',
      layer: 'L1',
      title: 'unit',
      status: 'todo',
    },
  ])

  const result = runGate(dir, ['run', '--lane', 'build'])
  assert.equal(result.status, 0, result.stderr)
  const report = readReport(dir, 'build')
  assert.equal(report.schema, 'ci-gates/gate-report@1')
  assert.equal(report.repo, 'acme/test')
  assert.equal(report.sha, 'a'.repeat(40))
  assert.equal(report.lane, 'build')
  assert.equal(report.level, 'C1')
  assert.equal(report.verdict, 'pass')
  assert.equal(report.results[0].status, 'pass')
  assert.equal(report.cases.catalog.todo, 1)
  assert.equal(report.onboarding.stage, 'cases-pending')
  assert.equal(
    report.run.url,
    'https://github.com/acme/test/actions/runs/42'
  )
})

test('prefers the pull_request head sha over GITHUB_SHA', () => {
  const dir = makeRepo({
    version: 1,
    repo: 'acme/test',
    lanes: { build: { commands: ['node -e "process.exit(0)"'] } },
  })
  const result = runGate(dir, ['run', '--lane', 'build'], {
    CI_GATES_SHA: 'b'.repeat(40),
  })
  assert.equal(result.status, 0)
  assert.equal(readReport(dir, 'build').sha, 'b'.repeat(40))
})

test('fails the lane and the process when a command fails', () => {
  const dir = makeRepo({
    version: 1,
    repo: 'acme/test',
    lanes: { build: { commands: ['node -e "process.exit(3)"'] } },
  })
  const result = runGate(dir, ['run', '--lane', 'build'])
  assert.equal(result.status, 1)
  const report = readReport(dir, 'build')
  assert.equal(report.verdict, 'fail')
  assert.equal(report.results[0].status, 'fail')
  assert.match(report.results[0].reason, /exit code 3/)
})

test('observe mode records observe_fail without blocking the job', () => {
  const dir = makeRepo({
    version: 1,
    repo: 'acme/test',
    lanes: {
      build: {
        enforcement: 'observe',
        commands: ['node -e "process.exit(1)"'],
      },
    },
  })
  const result = runGate(dir, ['run', '--lane', 'build'])
  assert.equal(result.status, 0)
  assert.equal(readReport(dir, 'build').verdict, 'observe_fail')
})

test('a lane without commands is not_applicable and does not fail', () => {
  const dir = makeRepo({
    version: 1,
    repo: 'acme/test',
    lanes: { build: {} },
  })
  const result = runGate(dir, ['run', '--lane', 'build'])
  assert.equal(result.status, 0)
  const report = readReport(dir, 'build')
  assert.equal(report.verdict, 'not_applicable')
  assert.deepEqual(report.results, [])
})

test('verify-cases accepts a ready case with a marker and rejects it without one', () => {
  const dir = makeRepo({
    version: 1,
    repo: 'acme/test',
    caseCatalog: 'test/cases/case-catalog.yaml',
    caseRoot: 'test',
    lanes: {},
  })
  writeCatalog(dir, [
    {
      id: 'build.unit',
      lane: 'build',
      layer: 'L1',
      title: 'unit',
      status: 'ready',
      file: 'test/unit.test.ts',
      owner: '@acme',
    },
  ])
  const markerFile = path.join(dir, 'test', 'unit.test.ts')
  mkdirSync(path.dirname(markerFile), { recursive: true })
  writeFileSync(markerFile, '// @case build.unit\nexport {}\n')

  const ok = runGate(dir, ['verify-cases'])
  assert.equal(ok.status, 0, ok.stderr)
  assert.match(ok.stdout, /1 ready/)

  writeFileSync(markerFile, 'export {}\n')
  const bad = runGate(dir, ['verify-cases'])
  assert.equal(bad.status, 1)
  assert.match(bad.stderr, /no @case marker/)
})
