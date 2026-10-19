/**
 * AgentGate · gate domain types, category metadata and demo data.
 * Numbers are design-mock placeholders until CI pipelines publish real
 * per-branch gate results.
 */

export type GateCategory = 'build' | 'ready' | 'merge' | 'daily' | 'release'
export type GateRunStatus = 'passed' | 'failed' | 'running'
/** Per-category health derived from pass rate thresholds. */
export type GateHealth = 'pass' | 'warn' | 'fail'

/** One test layer inside the latest run of a gate category. */
export interface GateCaseLayer {
  name: string
  planned: number
  executed: number
  passed: number
  /** True when only the plan (case list) is published, not per-case results. */
  planOnly?: boolean
}

/** Case counts of the latest run of a gate category (SD-board aligned). */
export interface GateCaseCounts {
  passed: number
  planned: number
  executed: number
  planOnlyPlanned: number
  totalPlanned: number
  runUrl?: string
  runAt?: string
  layers: GateCaseLayer[]
}

export interface RepoCategoryStat {
  category: GateCategory
  /**
   * Pass rate over the selected window (success / decided runs);
   * null when every run in the window is undecided (skipped/cancelled).
   */
  passRate: number | null
  avgDurationSec: number | null
  /** Canary-style runs still in progress → renders "2h 51m+". */
  ongoing?: boolean
  /** All matched runs in the selected window. */
  runs: number
  health: GateHealth
  /** Deep link to where this metric is presented on the source dashboard. */
  link?: string
  /** Test-case counts of the newest run of this category. */
  cases?: GateCaseCounts
}

/** Gate metrics for one branch of a repository. */
export interface RepoBranchStat {
  name: string
  gateStatus: GateRunStatus
  /** UT coverage %, red below the 75% threshold; null when unavailable. */
  coverage: number | null
  /** Deep link to the coverage view of the source dashboard. */
  coverageLink?: string
  stats: RepoCategoryStat[]
}

export interface GateRepo {
  id: string
  name: string
  owner: string
  /** Branch shown by default (first entry of branchData). */
  defaultBranch: string
  branchData: RepoBranchStat[]
  /** Optional external dashboard opened when the repo name is clicked. */
  url?: string
  /** True for repositories whose numbers are still design placeholders. */
  demo?: boolean
}

export interface GateCategoryMeta {
  id: GateCategory
  description: string
  /** Pass-rate colorization thresholds, per the approved design. */
  warnBelow: number
  failBelow: number
}

export const GATE_CATEGORIES: GateCategoryMeta[] = [
  {
    id: 'build',
    description: 'Commit & PR Base Gate',
    warnBelow: 85,
    failBelow: 80,
  },
  { id: 'ready', description: 'PR Ready Gate', warnBelow: 90, failBelow: 80 },
  {
    id: 'merge',
    description: 'Post-Merge Integration',
    warnBelow: 90,
    failBelow: 80,
  },
  {
    id: 'daily',
    description: 'Nightly & Canary',
    warnBelow: 90,
    failBelow: 80,
  },
  {
    id: 'release',
    description: 'Release Candidate',
    warnBelow: 95,
    failBelow: 80,
  },
]

const CATEGORY_MAP = new Map(GATE_CATEGORIES.map((c) => [c.id, c]))

export function getCategoryMeta(category: GateCategory): GateCategoryMeta {
  const meta = CATEGORY_MAP.get(category)
  if (!meta) throw new Error(`Unknown gate category: ${category}`)
  return meta
}

export function passRateHealth(
  category: GateCategory,
  passRate: number
): GateHealth {
  const { warnBelow, failBelow } = getCategoryMeta(category)
  if (passRate < failBelow) return 'fail'
  if (passRate < warnBelow) return 'warn'
  return 'pass'
}

/** Count repos below the coverage gate threshold (75%). */
export const COVERAGE_THRESHOLD = 75

function stat(
  category: GateCategory,
  passRate7d: number,
  avgDurationSec: number | null,
  runs7d: number,
  ongoing?: boolean
): RepoCategoryStat {
  return {
    category,
    passRate: passRate7d,
    avgDurationSec,
    runs: runs7d,
    ongoing,
    health: passRateHealth(category, passRate7d),
  }
}

function branch(
  name: string,
  gateStatus: GateRunStatus,
  coverage: number,
  stats: RepoCategoryStat[]
): RepoBranchStat {
  return { name, gateStatus, coverage, stats }
}

export const GATE_REPOS: GateRepo[] = [
  {
    id: 'sciencediscovery',
    name: 'sciencediscovery',
    owner: 'openJiuwen-ai',
    defaultBranch: 'main',
    url: 'https://sciencediscovery.github.io/github-status-board/#overview',
    branchData: [
      branch('main', 'failed', 71.2, [
        stat('build', 96.2, 492, 342),
        stat('ready', 93.8, 1300, 298),
        stat('merge', 88.6, 1565, 164),
        stat('daily', 85.1, 10260, 32, true),
        stat('release', 100, 18720, 4),
      ]),
      branch('release/v1.4', 'passed', 78.6, [
        stat('build', 97.8, 505, 128),
        stat('ready', 96.1, 1265, 96),
        stat('merge', 92.4, 1502, 48),
        stat('daily', 88.9, 9980, 12),
        stat('release', 96.7, 17600, 2),
      ]),
      branch('feat/mcp-tools', 'running', 69.8, [
        stat('build', 91.2, 640, 87),
        stat('ready', 84.5, 1420, 41),
        stat('merge', 76.9, 1810, 12),
      ]),
    ],
  },
  {
    id: 'digitalmate',
    name: 'DigitalMate',
    owner: 'openJiuwen-ai',
    defaultBranch: 'main',
    branchData: [
      branch('main', 'passed', 82.4, [
        stat('build', 98.2, 460, 351),
        stat('ready', 96.5, 1220, 305),
        stat('merge', 91.2, 1490, 171),
        stat('daily', 88.4, 9800, 33),
        stat('release', 100, 17600, 5),
      ]),
      branch('develop', 'passed', 80.9, [
        stat('build', 97.1, 470, 210),
        stat('ready', 94.8, 1240, 168),
        stat('merge', 89.3, 1585, 92),
        stat('daily', 86.2, 10150, 18),
      ]),
      branch('feat/data-pipeline', 'failed', 72.3, [
        stat('build', 89.4, 610, 76),
        stat('ready', 82.7, 1390, 33),
        stat('merge', 74.6, 1755, 9),
      ]),
    ],
  },
]

export function countReposByStatus(repos: { gateStatus: GateRunStatus }[]) {
  const counts = { passed: 0, failed: 0, running: 0 }
  for (const repo of repos) counts[repo.gateStatus] += 1
  return counts
}
