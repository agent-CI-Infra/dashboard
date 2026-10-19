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
  mkdtempSync,
  readdirSync,
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


/** Recent runs used to locate each lane's latest completed run. */
async function fetchRunsForCaseCounts(source, now) {
  return fetchActionsApiRuns(source, now)
}

/** Latest completed run per lane → per-layer case counts. */
async function collectCaseCounts(source, runs) {
  const lanes = Object.keys(source.laneRules ?? {})
  const latest = new Map()
  for (const run of runs) {
    if (run.status !== 'completed') continue
    const lane = lanes.find((id) =>
      matchesRule(source.laneRules[id], run)
    )
    if (!lane) continue
    const prev = latest.get(lane)
    if (!prev || run.created_at > prev.created_at) latest.set(lane, run)
  }
  const cases = {}
  for (const [lane, run] of latest) {
    const counts = await fetchCaseCounts(source, run)
    if (counts) cases[lane] = counts
  }
  const line = Object.entries(cases)
    .map(([c, v]) => `${c} ${v.passed}/${v.planned}`)
    .join(', ')
  if (line) console.log(`[gate]   cases@latest: ${line}`)
  return cases
}

/** Attach per-lane case counts and the branch coverage to one branch line. */
function attachCasesAndCoverage(branch, source, cases, coverage) {
  const isCoverageLine =
    branch.name === (source.coverageLine ?? source.defaultBranch)
  return {
    ...branch,
    coverage: isCoverageLine && coverage ? coverage.coverage : null,
    coverageDetail:
      isCoverageLine && coverage ? coverage.coverageDetail : undefined,
    coverageLink: isCoverageLine && coverage ? coverageLink(source) : undefined,
    stats: branch.stats.map((stat) => {
      const counts = cases?.[stat.category]
      return counts ? { ...stat, cases: counts } : stat
    }),
  }
}

/** ---------- SD lane model (numbers taken from the board's own data) ---------- */

/** SD lane key → dashboard category id (other/unknown lanes are dropped). */
const LANE_IDS = ['pr', 'main', 'daily', 'release', 'called']

/** SD lane → a day-indexed bucket list, in the dashboard's daily shape. */
function lanesToBranch(laneData, source) {
  const days = laneData?.days ?? []
  const stats = []
  for (const lane of laneData?.lanes ?? []) {
    if (!LANE_IDS.includes(lane.key)) continue
    const byDay = new Map()
    lane.days?.forEach((cells, index) => {
      const date = days[index]
      if (!date || !Array.isArray(cells) || cells.length === 0) return
      const bucket = {
        date,
        pass: 0,
        fail: 0,
        ignored: 0,
        retries: 0,
        durations: [],
      }
      for (const run of cells) {
        const outcome = run?.outcome ?? run?.conclusion
        if (outcome === 'success') bucket.pass += 1
        else if (outcome === 'failure') bucket.fail += 1
        else bucket.ignored += 1
        if ((run?.attempt ?? 1) > 1) bucket.retries += 1
        if (typeof run?.duration_s === 'number' && run.duration_s > 0) {
          bucket.durations.push(run.duration_s)
        }
      }
      byDay.set(date, bucket)
    })
    if (byDay.size === 0) continue
    stats.push({
      category: lane.key,
      daily: [...byDay.values()].sort((a, b) => (a.date < b.date ? -1 : 1)),
      link: `${source.boardBaseUrl}#ci`,
    })
  }
  if (stats.length === 0) return null
  // Lane order follows SD's own lane order.
  const order = new Map(LANE_IDS.map((key, index) => [key, index]))
  stats.sort((a, b) => order.get(a.category) - order.get(b.category))
  const latest = laneData?.lanes?.find((l) => l.key === 'main') ??
    laneData?.lanes?.find((l) => l.summary?.total)
  const rate = latest?.summary?.success_rate
  return {
    name: laneData?.branch ?? source.defaultBranch,
    gateStatus: rate == null ? 'passed' : rate >= 50 ? 'passed' : 'failed',
    coverage: null,
    stats,
  }
}

/** All branch lines the board publishes (main + line_sections). */
function extractLaneBranches(snapshot) {
  const main = snapshot?.sections?.ci?.data?.lanes
  const branches = []
  if (main?.lanes?.length) branches.push(main)
  for (const line of Object.values(snapshot?.line_sections ?? {})) {
    const lanes = line?.ci?.data?.lanes
    if (lanes?.lanes?.length) branches.push(lanes)
  }
  return branches
}
/** ---------- case counts (per test layer, same source as the SD board) ---------- */

function matchesPattern(file, pattern) {
  const escaped = pattern
    .split('*')
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('.*')
  return new RegExp(`^${escaped}$`).test(file)
}

function walkFiles(root, base = root) {
  const out = []
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const full = path.join(root, entry.name)
    if (entry.isDirectory()) out.push(...walkFiles(full, base))
    else out.push(path.relative(base, full))
  }
  return out
}

async function extractArtifact(owner, name, artifactId, dir) {
  const url = `https://api.github.com/repos/${owner}/${name}/actions/artifacts/${artifactId}/zip`
  const res = await fetch(url, {
    headers: {
      accept: 'application/vnd.github+json',
      ...(GITHUB_TOKEN ? { authorization: `Bearer ${GITHUB_TOKEN}` } : {}),
    },
    redirect: 'follow',
  })
  if (!res.ok) throw new Error(`HTTP ${res.status} downloading artifact ${artifactId}`)
  const zipPath = path.join(tmpdir(), `gate-cases-${artifactId}.zip`)
  writeFileSync(zipPath, Buffer.from(await res.arrayBuffer()))
  try {
    execFileSync('unzip', ['-o', '-q', zipPath, '-d', dir], {
      maxBuffer: 256 * 1024 * 1024,
    })
  } finally {
    rmSync(zipPath, { force: true })
  }
}

/**
 * Case counts for one gate category: the latest completed run's per-layer
 * summaries (planned / executed / passed) — the same numbers the SD board
 * shows in its "门禁用例" card.
 */
async function fetchCaseCounts(source, run) {
  const layers = source.caseLayers ?? []
  if (layers.length === 0 || !run) return null

  const artifactsUrl = `https://api.github.com/repos/${source.owner}/${source.name}/actions/runs/${run.id}/artifacts?per_page=100`
  const artifacts = (await fetchJson(artifactsUrl)).artifacts ?? []
  const result = { runUrl: run.html_url, runAt: run.created_at, layers: [] }
  const tmp = mkdtempSync(path.join(tmpdir(), 'gate-cases-'))

  try {
    for (const layer of layers) {
      const matched = artifacts.filter(
        (a) =>
          !a.expired &&
          layer.artifacts.some((prefix) => a.name.startsWith(prefix))
      )
      let planned = 0
      let executed = 0
      let passed = 0
      let planOnly = false
      for (const artifact of matched) {
        const dir = path.join(tmp, String(artifact.id))
        await extractArtifact(source.owner, source.name, artifact.id, dir)
        const files = walkFiles(dir)
        for (const summaryPath of layer.summaries) {
          for (const file of files.filter((f) => matchesPattern(f, summaryPath))) {
            const data = JSON.parse(readFileSync(path.join(dir, file), 'utf8'))
            if (Array.isArray(data.entries)) {
              // e2e plan artifacts: the entry list is the planned case set
              planned += data.entries.length
              planOnly = true
            } else {
              planned += data.planned ?? 0
              executed += data.executed ?? 0
              passed += data.passed ?? 0
            }
          }
        }
      }
      if (planned > 0 || executed > 0) {
        result.layers.push({
          name: layer.name,
          planned,
          executed,
          passed,
          ...(planOnly ? { planOnly: true } : {}),
        })
      }
    }
  } finally {
    rmSync(tmp, { recursive: true, force: true })
  }

  if (result.layers.length === 0) return null
  // Layers published as plans only (mocked E2E) carry no per-case outcome in
  // the small artifacts, so they are reported separately instead of skewing
  // the passed/planned ratio.
  const measured = result.layers.filter((l) => !l.planOnly)
  result.passed = measured.reduce((n, l) => n + l.passed, 0)
  result.planned = measured.reduce((n, l) => n + l.planned, 0)
  result.executed = measured.reduce((n, l) => n + l.executed, 0)
  result.planOnlyPlanned = result.layers
    .filter((l) => l.planOnly)
    .reduce((n, l) => n + l.planned, 0)
  result.totalPlanned = result.planned + result.planOnlyPlanned
  return result
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

/**
 * Buckets are keyed by UTC day so the dashboard can aggregate any window
 * (24h / 7d / 30d) client-side instead of shipping one fixed window.
 */
function emptyBucket() {
  return new Map()
}

function addRunToBucket(days, run) {
  const day = run.created_at.slice(0, 10)
  if (!days.has(day)) {
    days.set(day, { date: day, pass: 0, fail: 0, ignored: 0, retries: 0, durations: [] })
  }
  const bucket = days.get(day)
  if ((run.attempt ?? 1) > 1) bucket.retries += 1
  const kind = conclusionKind(run.conclusion)
  if (kind === 'pass') bucket.pass += 1
  else if (kind === 'fail') bucket.fail += 1
  else bucket.ignored += 1
  if (typeof run.duration_s === 'number' && run.duration_s > 0) {
    bucket.durations.push(run.duration_s)
  }
}

function bucketToStat(source, category, days) {
  return {
    category,
    // Per-day counts; the dashboard sums the selected window and derives
    // pass rate / runs / median duration from these buckets.
    daily: [...days.values()].sort((a, b) => (a.date < b.date ? -1 : 1)),
    // Where this metric is presented on the source dashboard.
    link: categoryLink(source, category),
  }
}

function aggregateSource(source, runs, coverage, cases, now) {
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
      .map((id) => {
        const stat = bucketToStat(source, id, byCategory.get(id))
        const caseCounts = cases?.[id]
        return caseCounts ? { ...stat, cases: caseCounts } : stat
      })

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
    if (source.type === 'status-board-lanes') {
      // Numbers come from the board's own lane data, so the dashboard shows
      // exactly what the SD board shows.
      const snapshot = await fetchJson(source.url)
      const laneBranches = extractLaneBranches(snapshot)
      let coverage = null
      try {
        coverage = await fetchCoverage(source)
      } catch (error) {
        console.warn(
          `[gate] ${source.owner}/${source.name} coverage unavailable: ${error?.message ?? error}`
        )
      }
      const runs = await fetchRunsForCaseCounts(source, now)
      const cases = await collectCaseCounts(source, runs)
      const branchData = laneBranches
        .map((lanes) => lanesToBranch(lanes, source))
        .filter(Boolean)
        .map((branch) => attachCasesAndCoverage(branch, source, cases, coverage))
      writeJson(outPath, {
        id: source.id,
        name: source.name,
        owner: source.owner,
        defaultBranch: source.defaultBranch,
        url: source.dashboardUrl ?? null,
        generatedAt: now.toISOString(),
        sourceUrl: source.url,
        windowDays,
        branchData,
      })
      indexEntries.push({
        id: source.id,
        name: source.name,
        owner: source.owner,
        file,
        sourceUrl: source.url,
        windowDays,
      })
      console.log(
        `[gate] ${source.owner}/${source.name}: ${branchData
          .map(
            (b) =>
              `${b.name}(${b.stats
                .map((st) => {
                  const p = st.daily.reduce((n, d) => n + d.pass, 0)
                  const f = st.daily.reduce((n, d) => n + d.fail, 0)
                  return `${st.category} ${f || p ? Math.round((p / (p + f)) * 1000) / 10 : '–'}%`
                })
                .join(', ')})`
          )
          .join(' ')}`
      )
      continue
    }

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
    // Case counts per category: latest completed run of that category.
    const cases = {}
    try {
      const categories = Object.keys(source.categoryMap)
      const latest = new Map()
      for (const run of runs) {
        if (run.status !== 'completed') continue
        const category = categories.find((id) =>
          matchesRule(source.categoryMap[id], run)
        )
        if (!category) continue
        const prev = latest.get(category)
        if (!prev || run.created_at > prev.created_at) latest.set(category, run)
      }
      for (const [category, run] of latest) {
        const counts = await fetchCaseCounts(source, run)
        if (counts) cases[category] = counts
      }
      const line = Object.entries(cases)
        .map(([c, v]) => `${c} ${v.passed}/${v.planned}`)
        .join(', ')
      if (line) console.log(`[gate]   cases@latest: ${line}`)
    } catch (error) {
      console.warn(
        `[gate] ${source.owner}/${source.name} case counts unavailable: ${error?.message ?? error}`
      )
    }
    const data = aggregateSource(source, runs, coverage, cases, now)
    writeJson(outPath, data)
    indexEntries.push({
      id: source.id,
      name: source.name,
      owner: source.owner,
      file,
      sourceUrl: source.url,
      windowDays,
    })
    const runCount = (stat) =>
      stat.daily.reduce((n, d) => n + d.pass + d.fail + d.ignored, 0)
    const summary = data.branchData
      .map((b) => `${b.name}(${b.stats.map((s) => `${s.category} ${s.daily.length}d/${runCount(s)}runs`).join(', ')})`)
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
