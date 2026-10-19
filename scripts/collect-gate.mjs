#!/usr/bin/env node
/**
 * Gate data collector (plan A) — consumes the sciencediscovery GitHub status
 * board snapshot and derives per-branch / per-category gate aggregates.
 *
 * Input : config/gate-sources.json (source list + category mapping rules)
 *         <source.url>            public status-board snapshot JSON
 * Output: public/data/gate/index.json                dataset list
 *         public/data/gate/<owner>/<name>.json       per-repository aggregates
 *
 * Aggregation rules:
 * - Only runs created within the configured window (default 7 days) count.
 * - conclusion success = pass; failure/timed_out/startup_failure = fail;
 *   cancelled/skipped/neutral/stale/action_required are excluded from the
 *   pass-rate denominator (they are neither green nor a gate failure).
 * - Merge-queue runs live on `gh-readonly-queue/<target>/pr-...` branches and
 *   are attributed back to their target branch.
 * - Coverage is not part of the snapshot (plan A limitation) → null and the
 *   dashboard renders an empty cell.
 */

import { execFileSync } from 'node:child_process'
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

const CONFIG_FILE = path.join(process.cwd(), 'config', 'gate-sources.json')
const OUT_DIR = path.join(process.cwd(), 'public', 'data', 'gate')
const DAY_MS = 24 * 60 * 60 * 1000
const RECENT_RUNS_PER_BRANCH = 10

const PASS_CONCLUSIONS = new Set(['success'])
const FAIL_CONCLUSIONS = new Set(['failure', 'timed_out', 'startup_failure'])
const IGNORED_CONCLUSIONS = new Set([
  'cancelled',
  'skipped',
  'neutral',
  'stale',
  'action_required',
  null,
])

function readJson(file) {
  return JSON.parse(readFileSync(file, 'utf8'))
}

function writeJson(file, data) {
  mkdirSync(path.dirname(file), { recursive: true })
  const tmp = `${file}.tmp`
  writeFileSync(tmp, `${JSON.stringify(data, null, 2)}\n`, 'utf8')
  renameSync(tmp, file)
}

const GITHUB_TOKEN =
  process.env.GITHUB_TOKEN ?? process.env.MONITOR_TOKEN ?? ''

async function fetchJson(url) {
  const headers = { accept: 'application/json' }
  // Authenticated API calls get a much higher rate limit; only needed for
  // the GitHub API (public sites work anonymously).
  if (GITHUB_TOKEN && url.startsWith('https://api.github.com/')) {
    headers.authorization = `Bearer ${GITHUB_TOKEN}`
  }
  const res = await fetch(url, { headers })
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`)
  return res.json()
}

/** `gh-readonly-queue/main/pr-123-<sha>` → `main`; other refs unchanged. */
function normalizeBranch(ref) {
  if (!ref) return null
  const queue = /^gh-readonly-queue\/([^/]+)\//.exec(ref)
  return queue ? queue[1] : ref
}

function conclusionKind(conclusion) {
  if (PASS_CONCLUSIONS.has(conclusion)) return 'pass'
  if (FAIL_CONCLUSIONS.has(conclusion)) return 'fail'
  if (IGNORED_CONCLUSIONS.has(conclusion)) return 'ignored'
  return 'ignored'
}

/**
 * Rule match: every array-valued field must contain the run's value.
 * Non-array fields (`scope`, `line`, ...) are routing metadata, not
 * matching conditions.
 */
function matchesRule(rule, run) {
  return Object.entries(rule).every(
    ([field, allowed]) => !Array.isArray(allowed) || allowed.includes(run[field])
  )
}

/** Categories whose runs are not tied to one branch (e.g. PR gates). */
function repoScopedCategories(source) {
  return Object.entries(source.categoryMap)
    .filter(([, rule]) => rule.scope === 'repo')
    .map(([id]) => id)
}

/** Deep link into the source dashboard's tab for a category. */
function categoryLink(source, category) {
  if (!source.boardBaseUrl) return null
  const tab = source.categoryTabs?.[category] ?? 'overview'
  return `${source.boardBaseUrl}#${tab}`
}

function coverageLink(source) {
  if (!source.boardBaseUrl) return null
  return `${source.boardBaseUrl}#${source.coverageTab ?? 'overview'}`
}

/** Branch line a repo-scoped category belongs to (defaults to the default branch). */
function repoScopedLine(source, category) {
  return source.categoryMap[category]?.line ?? source.defaultBranch
}

function median(values) {
  if (values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 0
    ? Math.round((sorted[mid - 1] + sorted[mid]) / 2)
    : sorted[mid]
}

function extractRuns(snapshot) {
  const runs = snapshot?.quality?.runs
  if (!Array.isArray(runs)) return []
  return runs
}

/** Workflow name → board channel, per the source config. */
function channelOf(source, workflowName) {
  for (const entry of source.channelMap ?? []) {
    if (entry.workflow.some((name) => workflowName.includes(name))) {
      return entry.channel
    }
  }
  return null
}

function runDurationSec(run) {
  if (typeof run.duration_s === 'number') return run.duration_s
  const start = run.run_started_at ?? run.created_at
  const end = run.updated_at ?? run.completed_at
  if (!start || !end) return null
  const seconds = (new Date(end) - new Date(start)) / 1000
  return seconds > 0 ? Math.round(seconds) : null
}

/**
 * Direct GitHub Actions API collection (source.type = "actions-api").
 * Run-level data is complete (no snapshot cap) and needs no token for
 * public repositories (~5 requests for a 7-day window).
 */
async function fetchActionsApiRuns(source, now) {
  const windowStart = new Date(now.getTime() - windowDays * DAY_MS)
  // The `created=>YYYY-MM-DD` filter is date-granular and drops that whole
  // boundary day, so query one day wider and filter precisely by timestamp.
  const since = new Date(windowStart.getTime() - DAY_MS)
    .toISOString()
    .slice(0, 10)
  const maxPages = Number(source.maxPages ?? 10)
  const collected = []
  for (let page = 1; page <= maxPages; page += 1) {
    const url =
      `https://api.github.com/repos/${source.owner}/${source.name}/actions/runs` +
      `?created=${encodeURIComponent(`>${since}`)}&per_page=100&page=${page}`
    const data = await fetchJson(url)
    const batch = data?.workflow_runs ?? []
    collected.push(...batch)
    if (batch.length < 100) break
  }
  return collected
    .filter((run) => run?.created_at && run.created_at >= windowStart.toISOString())
    .map((run) => ({
      id: run.id,
      name: run.name,
      channel: channelOf(source, run.name ?? ''),
      event: run.event,
      conclusion: run.conclusion,
      status: run.status,
      duration_s: runDurationSec(run),
      branch: run.head_branch,
      created_at: run.created_at,
      url: run.html_url,
      title: run.display_title,
      sha: run.head_sha,
      attempt: run.run_attempt ?? 1,
    }))
}

/** ---------- coverage extraction ---------- */

function extractUtLines(summary, lang) {
  const layers = summary?.layers?.ut
  const byLang = lang && layers?.[lang]?.totals?.lines
  const utLines = layers?.totals?.lines
  const topLines = summary?.totals?.lines
  const lines = byLang ?? utLines ?? topLines
  if (!lines || typeof lines.covered !== 'number' || typeof lines.total !== 'number') {
    return null
  }
  return { covered: lines.covered, total: lines.total }
}

async function downloadArtifactSummary(owner, name, artifactId, fileCandidates) {
  const url = `https://api.github.com/repos/${owner}/${name}/actions/artifacts/${artifactId}/zip`
  const res = await fetch(url, {
    headers: {
      accept: 'application/vnd.github+json',
      ...(GITHUB_TOKEN ? { authorization: `Bearer ${GITHUB_TOKEN}` } : {}),
    },
    redirect: 'follow',
  })
  if (!res.ok) throw new Error(`HTTP ${res.status} downloading artifact ${artifactId}`)
  const buffer = Buffer.from(await res.arrayBuffer())
  const tmpZip = path.join(tmpdir(), `gate-coverage-${artifactId}.zip`)
  writeFileSync(tmpZip, buffer)
  try {
    for (const candidate of fileCandidates) {
      try {
        const out = execFileSync('unzip', ['-p', tmpZip, candidate], {
          maxBuffer: 64 * 1024 * 1024,
        })
        return JSON.parse(out.toString('utf8'))
      } catch {
        // try the next candidate path inside the archive
      }
    }
    return null
  } finally {
    rmSync(tmpZip, { force: true })
  }
}

/**
 * Latest UT line coverage for the default branch, merged across the
 * configured summary artifacts (e.g. node + python). Returns null when the
 * artifacts are unavailable (no token, older runs, ...) — the dashboard then
 * renders an empty cell.
 */
async function fetchCoverage(source) {
  const coverage = source.coverage
  if (!coverage?.artifacts?.length) return null

  const runsUrl =
    `https://api.github.com/repos/${source.owner}/${source.name}/actions/runs` +
    `?branch=${encodeURIComponent(source.defaultBranch)}&status=completed&per_page=5`
  const { workflow_runs: runs = [] } = await fetchJson(runsUrl)

  for (const run of runs) {
    const artifactsUrl = `https://api.github.com/repos/${source.owner}/${source.name}/actions/runs/${run.id}/artifacts?per_page=100`
    let artifacts = []
    try {
      artifacts = (await fetchJson(artifactsUrl)).artifacts ?? []
    } catch {
      continue
    }

    let covered = 0
    let total = 0
    const detail = {}
    let found = false
    for (const prefix of coverage.artifacts) {
      const artifact = artifacts
        .filter((a) => a.name.startsWith(prefix) && !a.expired)
        .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0]
      if (!artifact) continue
      const summary = await downloadArtifactSummary(
        source.owner,
        source.name,
        artifact.id,
        coverage.summaryPaths ?? ['summary.json']
      )
      if (!summary) continue
      const lines = extractUtLines(summary, coverage.languageByArtifact?.[prefix])
      if (!lines) continue
      covered += lines.covered
      total += lines.total
      detail[prefix] = Math.round((lines.covered / lines.total) * 1000) / 10
      found = true
    }

    if (found && total > 0) {
      return {
        coverage: Math.round((covered / total) * 1000) / 10,
        coverageDetail: detail,
        coverageRunId: run.id,
        coverageSha: run.head_sha,
      }
    }
  }
  return null
}

function emptyBucket() {
  return { pass: 0, fail: 0, runs: 0, retries: 0, durations: [] }
}

function addRunToBucket(bucket, run) {
  bucket.runs += 1
  if ((run.attempt ?? 1) > 1) bucket.retries += 1
  const kind = conclusionKind(run.conclusion)
  if (kind === 'pass') bucket.pass += 1
  if (kind === 'fail') bucket.fail += 1
  if (typeof run.duration_s === 'number' && run.duration_s > 0) {
    bucket.durations.push(run.duration_s)
  }
}

function bucketToStat(source, category, bucket) {
  const decided = bucket.pass + bucket.fail
  return {
    category,
    passRate7d:
      decided > 0 ? Math.round((bucket.pass / decided) * 1000) / 10 : null,
    avgDurationSec: median(bucket.durations),
    runs7d: bucket.runs,
    retryRate:
      bucket.runs > 0
        ? Math.round((bucket.retries / bucket.runs) * 1000) / 10
        : 0,
    // Where this metric is presented on the source dashboard.
    link: categoryLink(source, category),
  }
}

function aggregateSource(source, runs, coverage, now) {
  const windowStart = new Date(now.getTime() - windowDays * DAY_MS).toISOString()

  const categories = Object.keys(source.categoryMap)
  const repoScoped = repoScopedCategories(source)
  /** branch → category → bucket (branch-scoped categories) */
  const buckets = new Map()
  /** branch → latest run (by created_at) */
  const latestByBranch = new Map()

  for (const run of runs) {
    const branch = normalizeBranch(run.branch)
    if (!branch) continue

    if (
      !latestByBranch.has(branch) ||
      run.created_at > latestByBranch.get(branch).created_at
    ) {
      latestByBranch.set(branch, run)
    }

    const category = categories.find((id) =>
      matchesRule(source.categoryMap[id], run)
    )
    if (!category) continue

    if (repoScoped.includes(category)) {
      // Repo-scoped gates render on their configured branch line
      // (e.g. the PR gate on main, the release gate on the release line).
      const line = repoScopedLine(source, category)
      if (!buckets.has(line)) buckets.set(line, new Map())
      const byLine = buckets.get(line)
      if (!byLine.has(category)) byLine.set(category, emptyBucket())
      addRunToBucket(byLine.get(category), run)
      continue
    }

    if (!buckets.has(branch)) buckets.set(branch, new Map())
    const byCategory = buckets.get(branch)
    if (!byCategory.has(category)) byCategory.set(category, emptyBucket())
    addRunToBucket(byCategory.get(category), run)
  }


  const branchNames = [...buckets.keys()].sort((a, b) => {
    if (a === source.defaultBranch) return -1
    if (b === source.defaultBranch) return 1
    const runsA = [...buckets.get(a).values()].reduce((n, c) => n + c.runs, 0)
    const runsB = [...buckets.get(b).values()].reduce((n, c) => n + c.runs, 0)
    return runsB - runsA
  })

  const branchData = branchNames.map((branch) => {
    const byCategory = buckets.get(branch)
    const stats = categories
      .filter((id) => byCategory.has(id))
      .map((id) => bucketToStat(source, id, byCategory.get(id)))

    const latest = latestByBranch.get(branch)
    const latestKind = latest ? conclusionKind(latest.conclusion) : 'ignored'
    const gateStatus =
      latest?.status === 'in_progress' || latest?.status === 'queued'
        ? 'running'
        : latestKind === 'fail'
          ? 'failed'
          : 'passed'

    const recentRuns = runs
      .filter((run) => normalizeBranch(run.branch) === branch)
      .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
      .slice(0, RECENT_RUNS_PER_BRANCH)
      .map((run) => ({
        id: run.id,
        name: run.name,
        event: run.event,
        conclusion: run.conclusion,
        status: run.status,
        durationSec: run.duration_s,
        createdAt: run.created_at,
        url: run.url,
        title: run.title,
        sha: run.sha,
        attempt: run.attempt ?? 1,
      }))

    return {
      name: branch,
      gateStatus,
      // UT line coverage of this branch, when the CI publishes summary
      // artifacts; null renders as an empty cell.
      coverage:
        coverage && branch === (source.coverageLine ?? source.defaultBranch)
          ? coverage.coverage
          : null,
      coverageDetail:
        coverage && branch === (source.coverageLine ?? source.defaultBranch)
          ? coverage.coverageDetail
          : undefined,
      coverageLink:
        coverage && branch === (source.coverageLine ?? source.defaultBranch)
          ? coverageLink(source)
          : undefined,
      stats,
      recentRuns,
    }
  })

  return {
    id: source.id,
    name: source.name,
    owner: source.owner,
    defaultBranch: source.defaultBranch,
    url: source.dashboardUrl ?? null,
    generatedAt: now.toISOString(),
    sourceUrl: source.url,
    windowDays,
    branchData,
  }
}

const config = readJson(CONFIG_FILE)
const windowDays = Number(config.windowDays ?? 7)
const now = new Date()

const indexEntries = []
for (const source of config.sources ?? []) {
  const file = `${source.owner}/${source.name}.json`
  const outPath = path.join(OUT_DIR, file)
  try {
    const runs =
      source.type === 'actions-api'
        ? await fetchActionsApiRuns(source, now)
        : extractRuns(await fetchJson(source.url))
    let coverage = null
    try {
      coverage = await fetchCoverage(source)
    } catch (error) {
      console.warn(
        `[gate] ${source.owner}/${source.name} coverage unavailable: ${error?.message ?? error}`
      )
    }
    const data = aggregateSource(source, runs, coverage, now)
    writeJson(outPath, data)
    indexEntries.push({
      id: source.id,
      name: source.name,
      owner: source.owner,
      file,
      sourceUrl: source.url,
      windowDays,
    })
    const summary = data.branchData
      .map((b) => `${b.name}(${b.stats.map((s) => `${s.category} ${s.passRate7d ?? '–'}%/${s.runs7d}`).join(', ')})`)
      .join(' ')
    console.log(`[gate] ${source.owner}/${source.name}: ${summary}`)
  } catch (error) {
    // Never overwrite the last valid dataset with an empty one: keep the
    // previously published file and flag it as stale in the index.
    const message = error?.message ?? String(error)
    if (existsSync(outPath)) {
      console.warn(
        `[gate] ${source.owner}/${source.name} failed (${message}); keeping the previously published dataset`
      )
      indexEntries.push({
        id: source.id,
        name: source.name,
        owner: source.owner,
        file,
        sourceUrl: source.url,
        windowDays,
        stale: true,
      })
    } else {
      console.warn(
        `[gate] ${source.owner}/${source.name} failed (${message}); no previous dataset to keep`
      )
    }
  }
}

writeJson(path.join(OUT_DIR, 'index.json'), {
  generatedAt: now.toISOString(),
  windowDays,
  sources: indexEntries,
})
console.log(`[gate] wrote ${OUT_DIR}`)
