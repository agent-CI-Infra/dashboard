import { useQuery } from '@tanstack/react-query'
import {
  passRateHealth,
  type GateCategory,
  type GateHealth,
  type GateRepo,
  type GateRunStatus,
  type RepoBranchStat,
  type RepoCategoryStat,
} from '@/features/gate/data/gate'

/**
 * Real gate datasets collected by `scripts/collect-gate.mjs` into
 * `public/data/gate/` (same static-snapshot pattern as the community data).
 * Repositories without a published dataset keep their demo entry, which the
 * overview merges in as a fallback.
 */
const DATA_DIR = `${import.meta.env.BASE_URL}data/gate`

async function fetchJson<T>(file: string): Promise<T> {
  const res = await fetch(`${DATA_DIR}/${file}`, { cache: 'no-cache' })
  if (!res.ok) throw new Error(`Failed to load ${file} (HTTP ${res.status})`)
  return (await res.json()) as T
}

type GateIndex = {
  generatedAt: string
  windowDays: number
  sources: { id: string; owner: string; name: string; file: string }[]
}

type RawStat = {
  category: GateCategory
  passRate7d: number | null
  avgDurationSec: number | null
  runs7d: number
  retryRate?: number
}

type RawBranch = {
  name: string
  gateStatus: GateRunStatus
  coverage: number | null
  stats: RawStat[]
  recentRuns?: unknown[]
}

type RawRepo = {
  id: string
  name: string
  owner: string
  defaultBranch: string
  url?: string | null
  generatedAt?: string
  sourceUrl?: string
  windowDays?: number
  branchData: RawBranch[]
}

/** Dataset shape on the wire → the dashboard's GateRepo shape. */
function toGateRepo(raw: RawRepo): GateRepo & { generatedAt?: string } {
  const branchData: RepoBranchStat[] = raw.branchData.map((branch) => ({
    name: branch.name,
    gateStatus: branch.gateStatus,
    coverage: branch.coverage ?? null,
    stats: branch.stats.map((stat): RepoCategoryStat => {
      const health: GateHealth =
        stat.passRate7d == null
          ? 'warn'
          : passRateHealth(stat.category, stat.passRate7d)
      return {
        category: stat.category,
        passRate7d: stat.passRate7d,
        avgDurationSec: stat.avgDurationSec,
        runs7d: stat.runs7d,
        health,
      }
    }),
  }))

  return {
    id: raw.id,
    name: raw.name,
    owner: raw.owner,
    defaultBranch: raw.defaultBranch,
    url: raw.url ?? undefined,
    branchData,
    generatedAt: raw.generatedAt,
  }
}

export function useGateRepos() {
  return useQuery({
    queryKey: ['gate', 'repos'],
    queryFn: async (): Promise<GateRepo[]> => {
      const index = await fetchJson<GateIndex>('index.json')
      const datasets = await Promise.all(
        (index.sources ?? []).map((source) =>
          fetchJson<RawRepo>(source.file).then(toGateRepo)
        )
      )
      return datasets
    },
    staleTime: 5 * 60 * 1000,
    retry: 1,
  })
}
