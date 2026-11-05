#!/usr/bin/env node
/**
 * Merge one Nightly's bench summary into the board's bench history.
 *
 *   node scripts/merge-bench-summary.mjs \
 *     --bench /tmp/bench --run /tmp/run.json \
 *     --previous /tmp/previous-bench.json --out /tmp/bench.json
 *
 * `--bench` is the directory produced by `gh run download`; the artifact
 * layout nests the workflow folders, so the summary is found recursively.
 * A missing summary still produces an ERROR entry so a failed night is visible
 * instead of silently disappearing.
 *
 * E2E results deliberately stay on the board's E2E tab; this file is Bench only.
 */

import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'

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

function findFirst(dir, base) {
  const file = walk(dir).find((candidate) => path.basename(candidate) === base)
  return file ? readJson(file) : null
}

function findFile(dir, base) {
  return walk(dir).find((candidate) => path.basename(candidate) === base) || null
}

function fraction(value) {
  const text = String(value || '').trim()
  if (!text || text === '—') return null
  const match = text.match(/^([0-9.]+)%$/)
  return match ? Number(match[1]) / 100 : null
}

function milliseconds(value) {
  const parts = String(value || '').split('/').map((part) => part.trim())
  const parse = (part) => {
    if (!part) return null
    const match = part.match(/^([0-9.]+)(ms|s)$/)
    if (!match) return null
    return Number(match[1]) * (match[2] === 's' ? 1000 : 1)
  }
  const p50 = parse(parts[0])
  const p95 = parse(parts[1])
  return p50 === null && p95 === null ? null : { p50, p95 }
}

// The manual Bench workflow publishes one Markdown table for all profiles.
// Parse it rather than reading five different report schemas; `render_report.py`
// is the single source of truth for the table.
function reportEntry(file, run) {
  const lines = readFileSync(file, 'utf8').split(/\r?\n/)
  const benchmarks = []
  let inTable = false
  for (const line of lines) {
    if (!line.startsWith('|')) {
      if (inTable) inTable = false
      continue
    }
    if (/^\|\s*-/.test(line)) {
      inTable = true
      continue
    }
    if (!inTable) continue
    const cells = line.split('|').slice(1, -1).map((cell) => cell.trim())
    if (cells.length < 9 || cells[0] === 'benchmark') continue
    const [name, result, tasks, trials, passAt1, passAtK, passHatK, detail, latency] = cells
    if (/not run/i.test(result)) continue
    benchmarks.push({
      name,
      status: result,
      tasks: Number.parseInt(tasks, 10) || 0,
      trials: Number.parseInt(trials, 10) || 0,
      passAt1: fraction(passAt1),
      passAtK: fraction(passAtK),
      passHatK: fraction(passHatK),
      detail,
      latencyMs: milliseconds(latency),
    })
  }
  if (!benchmarks.length) return null
  const runId = run && run.id ? `bench-${run.id}-${run.run_attempt || 1}` : ''
  const overall = benchmarks.every((item) => item.status === 'PASS') ? 'PASS' : 'FAIL'
  const backendLine = lines.find((line) => line.startsWith('| backend |')) || ''
  const backend = (backendLine.match(/`([^`]+)`/) || [])[1] || ''
  return {
    schemaVersion: 1,
    kind: 'bench',
    runId,
    commit: (run && run.head_sha) || '',
    backend,
    profile: 'all',
    status: overall,
    benchmarks,
    generatedAt: (run && run.created_at) || new Date().toISOString(),
  }
}

function previousRuns(previous) {
  const raw = readJson(previous)
  if (!raw) return []
  if (Array.isArray(raw)) return raw
  return Array.isArray(raw.runs) ? raw.runs : []
}

function numberFromRunId(value) {
  const match = String(value || '').match(/(?:nightly|bench)-(\d+)/)
  return match ? match[1] : ''
}

function entryFrom({ bench, run }) {
  const runNumber = run && run.id ? String(run.id) : ''
  const runId = (bench && bench.runId) || (runNumber ? `nightly-${runNumber}` : '')
  const base = bench
    ? {
        ...bench,
        runId,
        commit: bench.commit || (run && run.head_sha) || '',
        generatedAt: bench.generatedAt || (run && run.created_at) || new Date().toISOString(),
        ...(Array.isArray(bench.benchmarks)
          ? {
              taskCount: bench.benchmarks.reduce((sum, item) => sum + (Number(item.tasks) || 0), 0),
              trials: bench.benchmarks.reduce((sum, item) => sum + (Number(item.trials) || 0), 0),
              passAt1: null,
              passAtK: null,
              passHatK: null,
            }
          : {
              taskCount: Number.isFinite(Number(bench.taskCount))
                ? Number(bench.taskCount)
                : Array.isArray(bench.tasks)
                  ? bench.tasks.length
                  : bench.task
                    ? 1
                    : 0,
            }),
      }
    : {
        schemaVersion: 1,
        kind: 'bench',
        runId,
        commit: (run && run.head_sha) || '',
        backend: '',
        profile: '',
        task: '',
        trials: 0,
        status: 'ERROR',
        passAt1: 0,
        passAtK: 0,
        passHatK: 0,
        tasks: [],
        taskCount: 0,
        generatedAt: (run && run.created_at) || new Date().toISOString(),
        report: null,
      }
  return {
    ...base,
    runNumber,
    runUrl: (run && run.html_url) || null,
    conclusion: (run && run.conclusion) || null,
  }
}

function main() {
  const args = parse(process.argv.slice(2))
  const run = readJson(args.run)
  let bench = findFirst(args.bench, 'bench-summary.json')
  if (!bench) {
    const report = findFile(args.bench, 'bench-report.md')
    if (report) bench = reportEntry(report, run)
  }
  const entry = entryFrom({ bench, run })
  if (!entry.runNumber && !entry.runId) throw new Error('no run id in artifacts or run metadata')
  if (!entry.runId) entry.runId = `nightly-${numberFromRunId(entry.runNumber) || entry.runNumber}`

  const history = previousRuns(args.previous)
  const merged = [entry, ...history.filter((item) => String(item.runId) !== String(entry.runId))]
    .sort((a, b) => new Date(b.generatedAt || 0) - new Date(a.generatedAt || 0))
    .slice(0, 60)
  const out = args.out || 'bench.json'
  writeFileSync(out, `${JSON.stringify({ schemaVersion: 1, updatedAt: new Date().toISOString(), runs: merged }, null, 2)}\n`)
  console.log(JSON.stringify({ out, runs: merged.length, latest: entry.runId, status: entry.status }))
}

main()
