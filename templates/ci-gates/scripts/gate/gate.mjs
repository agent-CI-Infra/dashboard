#!/usr/bin/env node
/**
 * Minimal ci-gates runner (MVP).
 *
 * Usage:
 *   node scripts/gate/gate.mjs run --event pull_request
 *   node scripts/gate/gate.mjs run --lane build
 *   node scripts/gate/gate.mjs verify-cases
 *
 * It reads `.github/ci-gates.json`, runs the configured lane commands, writes
 * one `gate-report.json` per lane under `gate-reports/`, and exits non-zero
 * when an enforcing lane fails. No dependencies; Node 20+.
 */

import { spawnSync } from 'node:child_process'
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const REPORT_SCHEMA = 'ci-gates/gate-report@1'
const CATALOG_SCHEMA = 'ci-gates/case-catalog@1'
const LANES = ['build', 'pr-ready', 'pr-merge', 'daily', 'release']
const LEVELS = ['C0', 'C1', 'C2', 'C3', 'C4']
const STAGES = [
  'discovered',
  'gates-running',
  'cases-pending',
  'observing',
  'enforced',
  'failing',
]
const DEFAULT_EVENTS = {
  pull_request: ['build', 'pr-ready'],
  merge_group: ['build', 'pr-merge'],
  push: ['build'],
  schedule: ['daily'],
  release: ['release'],
  workflow_dispatch: ['build', 'pr-ready'],
}
const TEXT_EXTENSIONS = new Set([
  '.ts',
  '.tsx',
  '.js',
  '.jsx',
  '.mjs',
  '.cjs',
  '.py',
  '.go',
  '.rs',
  '.java',
  '.rb',
  '.php',
  '.sh',
])

function fail(message) {
  console.error(`[gate] ${message}`)
  process.exitCode = 1
}

function parseArgs(argv) {
  const args = { _: [] }
  for (let i = 0; i < argv.length; i += 1) {
    const value = argv[i]
    if (!value.startsWith('--')) {
      args._.push(value)
      continue
    }
    const key = value.slice(2)
    const next = argv[i + 1]
    if (!next || next.startsWith('--')) {
      args[key] = true
    } else {
      args[key] = next
      i += 1
    }
  }
  return args
}

function readJson(file) {
  try {
    return JSON.parse(readFileSync(file, 'utf8'))
  } catch (error) {
    throw new Error(`cannot read JSON ${file}: ${error.message}`)
  }
}

function readConfig(file) {
  if (!existsSync(file)) {
    throw new Error(`config not found: ${file}`)
  }
  const config = readJson(file)
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    throw new Error(`config must be an object: ${file}`)
  }
  if (config.version !== undefined && Number(config.version) !== 1) {
    throw new Error(`unsupported config version: ${config.version}`)
  }
  if (config.level !== undefined && !LEVELS.includes(config.level)) {
    throw new Error(`unknown level: ${config.level}`)
  }
  if (config.lanes !== undefined && (typeof config.lanes !== 'object' || Array.isArray(config.lanes))) {
    throw new Error('config.lanes must be an object')
  }
  return config
}

function gitValue(args) {
  const result = spawnSync('git', args, { encoding: 'utf8' })
  return result.status === 0 ? result.stdout.trim() : ''
}

function repoIdentity(config) {
  if (process.env.GITHUB_REPOSITORY) return process.env.GITHUB_REPOSITORY
  if (typeof config.repo === 'string' && config.repo.includes('/')) return config.repo
  const remote = gitValue(['remote', 'get-url', 'origin'])
  const match = remote.match(/github\.com[:/]([^/]+)\/([^/]+?)(?:\.git)?$/)
  return match ? `${match[1]}/${match[2]}` : 'local/repo'
}

function commitSha() {
  // For pull_request, GITHUB_SHA is the merge commit; prefer the PR head when
  // the workflow provides it so reports can be matched to a commit.
  if (process.env.CI_GATES_SHA) return process.env.CI_GATES_SHA
  if (process.env.GITHUB_SHA) return process.env.GITHUB_SHA
  return gitValue(['rev-parse', 'HEAD']) || 'local-dev'
}

function normalizeEvent(value) {
  return String(value ?? '').trim() || 'workflow_dispatch'
}

function laneCommands(laneConfig) {
  const raw = laneConfig?.commands
  if (raw === undefined) return []
  if (typeof raw === 'string') return [raw]
  if (!Array.isArray(raw) || raw.some((item) => typeof item !== 'string')) {
    throw new Error('lane commands must be a string or an array of strings')
  }
  return raw
}

function commandResult(command, index, options) {
  const started = Date.now()
  const timeoutMs = options.timeoutMs
  const child = spawnSync(command, {
    shell: true,
    stdio: 'inherit',
    env: {
      ...process.env,
      CI_GATES_CLI: options.cliPath,
      CI_GATES_LANE: options.lane,
    },
    timeout: timeoutMs,
    killSignal: 'SIGTERM',
  })
  const durationMs = Date.now() - started
  const result = {
    id: `cmd-${index + 1}`,
    name: command,
    status: 'pass',
    durationMs,
  }

  if (child.error?.code === 'ETIMEDOUT') {
    result.status = 'fail'
    result.reason = `timeout after ${Math.round(timeoutMs / 1000)}s`
  } else if (child.error) {
    result.status = 'error'
    result.reason = child.error.message
  } else if (child.signal) {
    result.status = 'fail'
    result.reason = `terminated by ${child.signal}`
  } else if (child.status !== 0) {
    result.status = 'fail'
    result.reason = `exit code ${child.status ?? 'unknown'}`
  } else {
    result.exitCode = 0
  }
  if (child.status !== null && child.status !== undefined) {
    result.exitCode = child.status
  }
  return result
}

function defaultStage({ verdict, stats, observeUntil, hasCatalog }) {
  if (verdict === 'fail') return 'failing'
  if (!hasCatalog) return 'gates-running'
  if (stats.todo > 0) return 'cases-pending'
  if (observeUntil) {
    const until = new Date(observeUntil)
    if (!Number.isNaN(until.valueOf()) && until >= new Date()) return 'observing'
  }
  return 'enforced'
}

function runLane(lane, config, context) {
  if (!LANES.includes(lane)) {
    throw new Error(`unknown lane: ${lane}`)
  }
  const laneConfig = config.lanes?.[lane] ?? {}
  const commands = laneCommands(laneConfig)
  const enforcement = laneConfig.enforcement ?? 'enforce'
  const continueOnError = laneConfig.continueOnError === true
  const timeoutMs =
    Math.max(1, Number(laneConfig.timeoutSec ?? config.timeoutSec ?? 900)) * 1000
  const results = []
  let failed = false

  for (let index = 0; index < commands.length; index += 1) {
    if (failed && !continueOnError) {
      results.push({
        id: `cmd-${index + 1}`,
        name: commands[index],
        status: 'not_run',
        reason: 'previous command failed',
      })
      continue
    }
    const result = commandResult(commands[index], index, {
      lane,
      timeoutMs,
      cliPath: context.cliPath,
    })
    results.push(result)
    if (result.status === 'fail' || result.status === 'error') {
      failed = true
    }
  }

  const anyFailure = results.some(
    (result) => result.status === 'fail' || result.status === 'error'
  )
  let verdict
  if (commands.length === 0) verdict = 'not_applicable'
  else if (anyFailure) verdict = enforcement === 'observe' ? 'observe_fail' : 'fail'
  else verdict = 'pass'

  const catalog = context.catalog
  const stats = catalog ? summarizeCatalog(catalog) : null
  const stage = defaultStage({
    verdict,
    stats: stats ?? { todo: 0 },
    observeUntil: config.observeUntil,
    hasCatalog: Boolean(catalog),
  })
  const event = context.event
  const runId = Number(process.env.GITHUB_RUN_ID)
  const runAttempt = Number(process.env.GITHUB_RUN_ATTEMPT)
  const server = process.env.GITHUB_SERVER_URL
  const run = Number.isFinite(runId) && runId > 0
    ? {
        id: runId,
        ...(Number.isFinite(runAttempt) && runAttempt > 0
          ? { attempt: runAttempt }
          : {}),
        ...(server
          ? { url: `${server}/${context.repo}/actions/runs/${runId}` }
          : {}),
      }
    : undefined

  const report = {
    schema: REPORT_SCHEMA,
    gates: 'mvp-1',
    gatesVersion: 'mvp-1',
    repo: context.repo,
    sha: context.sha,
    event,
    lane,
    level: config.level ?? 'C1',
    preset: config.preset ?? 'auto',
    verdict,
    ...(commands.length === 0
      ? { results: [], reason: `no commands configured for ${lane}` }
      : { results }),
    ...(stats
      ? {
          cases: { catalog: stats },
          onboarding: {
            stage,
            catalog: config.caseCatalog,
            ...(config.observeUntil ? { observeUntil: config.observeUntil } : {}),
          },
        }
      : {}),
    waivers: [],
    ...(run ? { run } : {}),
  }

  if (!STAGES.includes(stage)) throw new Error(`invalid stage: ${stage}`)
  return { report, ok: verdict !== 'fail' }
}

function writeReports(reports, outDir) {
  mkdirSync(outDir, { recursive: true })
  for (const { report } of reports) {
    const file = path.join(outDir, `${report.lane}.json`)
    writeFileSync(file, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
    console.log(`[gate] ${report.lane}: ${report.verdict} (${report.results.length} result(s)) -> ${file}`)
  }
}

/* ------------------------------------------------------------------ */
/* Case catalog                                                        */
/* ------------------------------------------------------------------ */

function parseScalar(value) {
  const trimmed = value.trim()
  if (trimmed === '') return null
  if (
    (trimmed.startsWith("'") && trimmed.endsWith("'")) ||
    (trimmed.startsWith('"') && trimmed.endsWith('"'))
  ) {
    return trimmed.slice(1, -1)
  }
  if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
    const inner = trimmed.slice(1, -1).trim()
    return inner === '' ? [] : inner.split(',').map((item) => parseScalar(item))
  }
  if (trimmed === 'true') return true
  if (trimmed === 'false') return false
  if (trimmed === 'null' || trimmed === '~') return null
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed)
  return trimmed
}

/**
 * A deliberately small YAML subset parser for `case-catalog.yaml`: top-level
 * scalars plus a `cases:` list of flat mappings. It rejects nested structures
 * instead of guessing, so the catalog cannot silently lose fields.
 */
function parseCatalogYaml(text, file) {
  const root = {}
  let current = null
  let inCases = false

  text.split(/\r?\n/).forEach((raw, index) => {
    const lineNumber = index + 1
    if (!raw.trim() || raw.trimStart().startsWith('#')) return
    if (raw.includes('\t')) {
      throw new Error(`${file}:${lineNumber}: tabs are not supported; use spaces`)
    }
    const indent = raw.length - raw.trimStart().length
    const trimmed = raw.trim()

    if (indent === 0) {
      const separator = trimmed.indexOf(':')
      if (separator < 0) {
        throw new Error(`${file}:${lineNumber}: expected "key: value"`)
      }
      const key = trimmed.slice(0, separator).trim()
      const rest = trimmed.slice(separator + 1).trim()
      if (rest === '' && key === 'cases') {
        root.cases = []
        inCases = true
        current = null
        return
      }
      if (rest === '') {
        throw new Error(`${file}:${lineNumber}: nested mapping "${key}" is not supported`)
      }
      root[key] = parseScalar(rest)
      inCases = false
      current = null
      return
    }

    if (!inCases) {
      throw new Error(`${file}:${lineNumber}: unexpected indentation outside cases`)
    }
    if (trimmed.startsWith('-')) {
      const rest = trimmed.slice(1).trim()
      current = {}
      root.cases.push(current)
      if (rest) {
        const separator = rest.indexOf(':')
        if (separator < 0) {
          throw new Error(`${file}:${lineNumber}: expected "- key: value"`)
        }
        current[rest.slice(0, separator).trim()] = parseScalar(
          rest.slice(separator + 1)
        )
      }
      return
    }

    if (!current) {
      throw new Error(`${file}:${lineNumber}: case field without a list item`)
    }
    const separator = trimmed.indexOf(':')
    if (separator < 0) {
      throw new Error(`${file}:${lineNumber}: expected "key: value"`)
    }
    current[trimmed.slice(0, separator).trim()] = parseScalar(
      trimmed.slice(separator + 1)
    )
  })

  return root
}

function parseCatalog(file) {
  if (!existsSync(file)) throw new Error(`case catalog not found: ${file}`)
  const text = readFileSync(file, 'utf8')
  const catalog = file.endsWith('.json')
    ? JSON.parse(text)
    : parseCatalogYaml(text, file)
  validateCatalog(catalog, file)
  return catalog
}

function validateCatalog(catalog, file) {
  if (!catalog || typeof catalog !== 'object' || Array.isArray(catalog)) {
    throw new Error(`${file}: catalog must be an object`)
  }
  if (catalog.schema !== CATALOG_SCHEMA) {
    throw new Error(`${file}: unsupported schema ${catalog.schema ?? '(none)'}`)
  }
  if (Number(catalog.version) !== 1) {
    throw new Error(`${file}: unsupported version ${catalog.version}`)
  }
  if (!Array.isArray(catalog.cases)) {
    throw new Error(`${file}: cases must be an array`)
  }
  const ids = new Set()
  for (const entry of catalog.cases) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
      throw new Error(`${file}: case must be an object`)
    }
    if (typeof entry.id !== 'string' || !entry.id) {
      throw new Error(`${file}: every case needs an id`)
    }
    if (ids.has(entry.id)) throw new Error(`${file}: duplicate case id ${entry.id}`)
    ids.add(entry.id)
    if (!LANES.includes(entry.lane)) {
      throw new Error(`${file}: ${entry.id} has unknown lane ${entry.lane}`)
    }
    if (!/^L[0-5](-L[0-5])?$/.test(String(entry.layer ?? ''))) {
      throw new Error(`${file}: ${entry.id} has unknown layer ${entry.layer}`)
    }
    if (!['ready', 'todo', 'skipped'].includes(entry.status)) {
      throw new Error(`${file}: ${entry.id} has unknown status ${entry.status}`)
    }
    if (entry.status === 'ready' && (!entry.file || !entry.owner)) {
      throw new Error(`${file}: ready case ${entry.id} needs file and owner`)
    }
    if (entry.status === 'skipped' && (!entry.until || !entry.reason)) {
      throw new Error(`${file}: skipped case ${entry.id} needs until and reason`)
    }
  }
}

function summarizeCatalog(catalog) {
  const stats = { total: 0, ready: 0, todo: 0, skipped: 0 }
  for (const entry of catalog?.cases ?? []) {
    stats.total += 1
    if (entry.status === 'ready') stats.ready += 1
    else if (entry.status === 'todo') stats.todo += 1
    else if (entry.status === 'skipped') stats.skipped += 1
  }
  return stats
}

function walkFiles(root, out = []) {
  if (!existsSync(root)) return out
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (
      entry.name === 'node_modules' ||
      entry.name === '.git' ||
      entry.name === 'dist' ||
      entry.name === 'coverage' ||
      entry.name === 'gate-reports'
    ) {
      continue
    }
    const full = path.join(root, entry.name)
    if (entry.isDirectory()) walkFiles(full, out)
    else if (entry.isFile()) out.push(full)
  }
  return out
}

function caseMarkers(roots) {
  const markers = new Map()
  const files = roots.flatMap((root) => walkFiles(root))
  for (const file of files) {
    if (!TEXT_EXTENSIONS.has(path.extname(file))) continue
    let text
    try {
      text = readFileSync(file, 'utf8')
    } catch {
      continue
    }
    for (const match of text.matchAll(/@case\s+([A-Za-z0-9_.:-]+)/g)) {
      const id = match[1]
      if (!markers.has(id)) markers.set(id, new Set())
      markers.get(id).add(file)
    }
  }
  return markers
}

function verifyCases(config) {
  const catalogPath = config.caseCatalog
  if (!catalogPath) {
    console.log('[gate] no caseCatalog configured; skipping case verification')
    return { ok: true, errors: [], warnings: [] }
  }
  const catalog = parseCatalog(catalogPath)
  const stats = summarizeCatalog(catalog)
  const caseRoots = Array.isArray(config.caseRoot)
    ? config.caseRoot
    : [config.caseRoot ?? 'test']
  const caseRootLabel = caseRoots.join(', ')
  const markers = caseMarkers(caseRoots)
  const errors = []
  const warnings = []
  const catalogIds = new Set(catalog.cases.map((entry) => entry.id))

  for (const entry of catalog.cases) {
    if (entry.status === 'ready') {
      if (!existsSync(entry.file)) {
        errors.push(`ready case ${entry.id}: file not found: ${entry.file}`)
      }
      const markerFiles = [...(markers.get(entry.id) ?? [])].map((file) =>
        path.resolve(file)
      )
      if (existsSync(entry.file) && !markerFiles.includes(path.resolve(entry.file))) {
        errors.push(
          `ready case ${entry.id}: no @case marker in ${entry.file} (scanned: ${caseRootLabel}; found: ${markerFiles.join(', ') || 'none'})`
        )
      }
    } else if (markers.has(entry.id)) {
      warnings.push(
        `${entry.status} case ${entry.id} already has a marker; move it to ready`
      )
    }
  }

  for (const [id, files] of markers) {
    if (!catalogIds.has(id)) {
      errors.push(
        `unknown @case marker ${id} in ${[...files].join(', ')} (not in ${catalogPath})`
      )
    }
  }

  console.log(
    `[gate] cases: ${stats.total} total, ${stats.ready} ready, ${stats.todo} todo, ${stats.skipped} skipped`
  )
  for (const warning of warnings) console.warn(`[gate] warn: ${warning}`)
  for (const error of errors) console.error(`[gate] error: ${error}`)
  return { ok: errors.length === 0, errors, warnings }
}

/* ------------------------------------------------------------------ */
/* CLI                                                                 */
/* ------------------------------------------------------------------ */

function loadCatalogForRun(config) {
  return config.caseCatalog ? parseCatalog(config.caseCatalog) : null
}

function runCommand(args) {
  const configFile = args.config ?? '.github/ci-gates.json'
  const config = readConfig(configFile)
  const event = normalizeEvent(args.event ?? process.env.GITHUB_EVENT_NAME)
  let lanes
  if (args.lane) {
    lanes = [String(args.lane)]
  } else {
    const events = { ...DEFAULT_EVENTS, ...(config.events ?? {}) }
    lanes = events[event] ?? ['build']
  }
  if (!Array.isArray(lanes) || lanes.length === 0) {
    throw new Error(`no lanes configured for event ${event}`)
  }
  for (const lane of lanes) {
    if (!LANES.includes(lane)) throw new Error(`unknown lane: ${lane}`)
  }

  const outDir = path.resolve(args.out ?? config.outDir ?? 'gate-reports')
  if (!args.keep) {
    // A fresh directory per run prevents stale lane reports from being uploaded.
    rmSync(outDir, { recursive: true, force: true })
  }

  const catalog = loadCatalogForRun(config)
  const context = {
    repo: repoIdentity(config),
    sha: commitSha(),
    event,
    cliPath: fileURLToPath(import.meta.url),
    catalog,
  }
  const reports = []
  let allOk = true
  for (const lane of lanes) {
    const { report, ok } = runLane(lane, config, context)
    reports.push({ report, ok })
    if (!ok) allOk = false
  }

  writeReports(reports, outDir)

  const failed = reports.filter(({ report }) => report.verdict === 'fail')
  if (failed.length > 0) {
    console.error(
      `[gate] failed lanes: ${failed.map(({ report }) => report.lane).join(', ')}`
    )
  } else {
    console.log(`[gate] all lanes completed for ${event}`)
  }
  process.exitCode = allOk ? 0 : 1
}

function main(argv) {
  const args = parseArgs(argv)
  const command = args._[0]
  if (command === 'run') {
    runCommand(args)
    return
  }
  if (command === 'verify-cases') {
    const config = readConfig(args.config ?? '.github/ci-gates.json')
    const result = verifyCases(config)
    process.exitCode = result.ok ? 0 : 1
    return
  }
  console.error(`Usage:
  node gate.mjs run [--event <event>] [--lane <lane>] [--config <file>] [--out <dir>] [--keep]
  node gate.mjs verify-cases [--config <file>]`)
  process.exitCode = 2
}

const isMain =
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)

if (isMain) {
  try {
    main(process.argv.slice(2))
  } catch (error) {
    fail(error?.message ?? String(error))
    process.exitCode = 2
  }
}

export {
  DEFAULT_EVENTS,
  LANES,
  parseCatalog,
  parseCatalogYaml,
  runLane,
  summarizeCatalog,
  verifyCases,
}
