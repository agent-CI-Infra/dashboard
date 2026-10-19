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

import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from 'node:fs'
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

/** Rule match: every non-scope key must contain the run's value. */
function matchesRule(rule, run) {
  return Object.entries(rule)
    .filter(([field]) => field !== 'scope')
    .every(([field, allowed]) => allowed.includes(run[field]))
}

/** Categories whose runs are not tied to one branch (e.g. PR gates). */
function repoScopedCategories(source) {
  return Object.entries(source.categoryMap)
    .filter(([, rule]) => rule.scope === 'repo')
    .map(([id]) => id)
}

/** Branch whitelist: explicit config, or every branch with runs. */
function branchAllowed(source, branch) {
  if (Array.isArray(source.branches) && source.branches.length > 0) {
    return branch === source.defaultBranch || source.branches.includes(branch)
  }
  return true
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

function bucketToStat(category, bucket) {
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
  }
}

function aggregateSource(source, runs, now) {
  const windowStart = new Date(now.getTime() - windowDays * DAY_MS).toISOString()

  const categories = Object.keys(source.categoryMap)
  const repoScoped = repoScopedCategories(source)
  /** branch → category → bucket (branch-scoped categories) */
  const buckets = new Map()
  /** repo-wide buckets for repo-scoped categories (e.g. the PR gate) */
  const repoBuckets = new Map()
  /** branch → latest run (by created_at) */
  const latestByBranch = new Map()

  for (const run of runs) {
    const branch = normalizeBranch(run.branch)
    if (!branch) continue

    if (
      branchAllowed(source, branch) &&
      (!latestByBranch.has(branch) ||
        run.created_at > latestByBranch.get(branch).created_at)
    ) {
      latestByBranch.set(branch, run)
    }

    const category = categories.find((id) =>
      matchesRule(source.categoryMap[id], run)
    )
    if (!category) continue

    if (repoScoped.includes(category)) {
      if (!repoBuckets.has(category)) repoBuckets.set(category, emptyBucket())
      addRunToBucket(repoBuckets.get(category), run)
      continue
    }

    if (!branchAllowed(source, branch)) continue
    if (!buckets.has(branch)) buckets.set(branch, new Map())
    const byCategory = buckets.get(branch)
    if (!byCategory.has(category)) byCategory.set(category, emptyBucket())
    addRunToBucket(byCategory.get(category), run)
  }

  // Repo-scoped gates (PR) always pair with the default branch row.
  if (repoBuckets.size > 0) {
    if (!buckets.has(source.defaultBranch)) {
      buckets.set(source.defaultBranch, new Map())
    }
    const byCategory = buckets.get(source.defaultBranch)
    for (const [category, bucket] of repoBuckets) {
      byCategory.set(category, bucket)
    }
  }

  const branchNames = [...buckets.keys()]
    .filter((branch) => branchAllowed(source, branch))
    .sort((a, b) => {
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
      .map((id) => bucketToStat(id, byCategory.get(id)))

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
      // Status-board snapshots carry no coverage (plan A) — the dashboard
      // renders an empty cell until a coverage source is wired in.
      coverage: null,
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
    const data = aggregateSource(source, runs, now)
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
