import { useMemo, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  GitBranch,
  SearchX,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { TONE_BG, TONE_TEXT } from '@/features/gate/components/gate-styles'
import {
  COVERAGE_THRESHOLD,
  GATE_CATEGORIES,
  countReposByStatus,
  type GateRepo,
  type RepoBranchStat,
  type RepoCategoryStat,
} from '@/features/gate/data/gate'
import { formatPercent } from '@/features/gate/lib/format'
import { useLang, type I18nKey } from '@/lib/i18n'

/**
 * Flat gate overview table (spec v1.9): repository (with branch picker) ·
 * build / ready / merge / daily / release pass-rate pills · coverage.
 * Rows sort by clicking a column header (first click: desc for metrics,
 * asc for repository names); branches without a category always sink.
 */
type SortKey =
  | 'repo'
  | 'build'
  | 'ready'
  | 'merge'
  | 'daily'
  | 'release'
  | 'coverage'
type SortDir = 'asc' | 'desc'

const ROW_COLS =
  'grid grid-cols-[minmax(280px,1fr)_repeat(5,104px)_104px] items-center gap-x-2.5'

const COL_LABEL: Record<string, I18nKey> = {
  repo: 'gate.col.repo',
  build: 'gate.col.build',
  ready: 'gate.col.ready',
  merge: 'gate.col.merge',
  daily: 'gate.col.daily',
  release: 'gate.col.release',
  coverage: 'gate.col.coverage',
}

/** Sort value per column; null sinks to the bottom in both directions. */
function sortValue(
  repo: GateRepo,
  branch: RepoBranchStat,
  key: SortKey
): number | string | null {
  switch (key) {
    case 'repo':
      return repo.name
    case 'coverage':
      return branch.coverage
    default:
      return branch.stats.find((s) => s.category === key)?.passRate ?? null
  }
}

/** Per-layer tooltip for a gate cell (SD-board format). */
function casesTooltip(stat: RepoCategoryStat, plannedLabel: string): string | null {
  const cases = stat.cases
  if (!cases) return null
  const parts = cases.layers.map((layer) =>
    layer.planOnly
      ? `${layer.name} ${plannedLabel} ${layer.planned}`
      : `${layer.name} ${layer.passed}/${layer.planned}`
  )
  const when = cases.runAt ? cases.runAt.slice(0, 16).replace('T', ' ') : ''
  return `${parts.join(' · ')}${when ? ` — ${when}` : ''}`
}

/** Shared chip shell: one or two stacked lines inside the tinted block. */
const CHIP_BASE =
  'flex h-9 w-full flex-col items-center justify-center rounded-md px-1 leading-tight'

/**
 * Pass rate as a soft status chip tinted by health tier, with the latest run's
 * case count on a second line. Chips link to the matching view of the source
 * dashboard when one is known.
 */
function RateCell({ stat }: { stat?: RepoCategoryStat }) {
  const { t } = useLang()
  if (!stat || stat.passRate == null) {
    return <span className='text-sm text-gate-faint'>–</span>
  }
  const tone = cn(TONE_BG[stat.health], TONE_TEXT[stat.health])
  const value = formatPercent(stat.passRate)
  const cases = stat.cases
  const tooltip = casesTooltip(stat, t('gate.casesPlanned'))

  const inner = (
    <>
      <span className='text-[13px] font-medium tabular-nums'>{value}</span>
      {cases ? (
        <span className='font-mono text-[9px] tabular-nums opacity-75'>
          {cases.totalPlanned}
        </span>
      ) : null}
    </>
  )

  if (!stat.link) {
    return <span className={cn(CHIP_BASE, tone)}>{inner}</span>
  }
  return (
    <a
      href={stat.link}
      target='_blank'
      rel='noreferrer'
      title={tooltip ?? t('gate.openSource')}
      className={cn(
        CHIP_BASE,
        tone,
        'transition-shadow hover:ring-2 hover:ring-gate-accent/40'
      )}
    >
      {inner}
    </a>
  )
}

function CoverageCell({
  coverage,
  link,
}: {
  coverage: number | null
  link?: string
}) {
  const { t } = useLang()
  if (coverage == null) {
    return <span className='text-sm text-gate-faint'>–</span>
  }
  const low = coverage < COVERAGE_THRESHOLD
  const tone = low
    ? 'bg-gate-fail-soft text-gate-fail'
    : 'bg-gate-accent-soft text-gate-accent'
  const inner = (
    <>
      <span className='text-[13px] font-medium tabular-nums'>
        {formatPercent(coverage)}
      </span>
      <span className='font-mono text-[9px] tabular-nums opacity-75'>
        ≥{COVERAGE_THRESHOLD}%
      </span>
    </>
  )
  if (!link) {
    return <span className={cn(CHIP_BASE, tone)}>{inner}</span>
  }
  return (
    <a
      href={link}
      target='_blank'
      rel='noreferrer'
      title={t('gate.openSource')}
      className={cn(
        CHIP_BASE,
        tone,
        'transition-shadow hover:ring-2 hover:ring-gate-accent/40'
      )}
    >
      {inner}
    </a>
  )
}

/** Branch selector chip shown next to the repository name. */
function BranchPicker({
  repo,
  selected,
  onSelect,
}: {
  repo: GateRepo
  selected: RepoBranchStat
  onSelect: (branchName: string) => void
}) {
  const { t } = useLang()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={t('gate.switchBranch')}
        title={t('gate.switchBranch')}
        className='inline-flex h-5 max-w-32 shrink-0 items-center gap-1 rounded-md border border-gate-line bg-gate-track px-1.5 text-[11px] font-medium text-gate-dim outline-none hover:text-gate-ink focus-visible:ring-2 focus-visible:ring-gate-accent/30'
      >
        <GitBranch className='size-3 shrink-0' />
        <span className='truncate'>{selected.name}</span>
        <ChevronDown className='size-3 shrink-0' />
      </DropdownMenuTrigger>
      <DropdownMenuContent align='start' className='min-w-44'>
        <DropdownMenuLabel className='text-xs text-muted-foreground'>
          {t('gate.branches')}
        </DropdownMenuLabel>
        {repo.branchData.map((b) => (
          <DropdownMenuItem
            key={b.name}
            onClick={() => onSelect(b.name)}
            className='gap-2 text-[12px] font-medium'
          >
            <GitBranch className='size-3.5 text-gate-faint' />
            <span className='truncate'>{b.name}</span>
            <Check
              size={14}
              className={cn('ms-auto', b.name !== selected.name && 'hidden')}
            />
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function SortHeader({
  labelKey,
  active,
  dir,
  onClick,
  centered,
  accent,
}: {
  labelKey: I18nKey
  active: boolean
  dir: SortDir
  onClick: () => void
  centered?: boolean
  /** Coverage is a separate metric — its header carries the accent hue. */
  accent?: boolean
}) {
  const { t } = useLang()
  const label = t(labelKey)
  return (
    <button
      type='button'
      onClick={onClick}
      className={cn(
        'group flex items-center gap-1 text-[11px] font-semibold tracking-[0.06em] uppercase outline-none focus-visible:ring-2 focus-visible:ring-gate-accent/30',
        centered ? 'justify-center text-center' : 'text-left',
        active
          ? 'text-gate-ink'
          : accent
            ? 'text-gate-accent hover:text-gate-accent/80'
            : 'text-gate-faint hover:text-gate-dim'
      )}
    >
      {label}
      {active ? (
        dir === 'asc' ? (
          <ArrowUp className='size-3' />
        ) : (
          <ArrowDown className='size-3' />
        )
      ) : (
        <ArrowDown className='size-3 opacity-0 transition-opacity group-hover:opacity-40' />
      )}
    </button>
  )
}

export function RepoGateTable({ repos }: { repos: GateRepo[] }) {
  const { t } = useLang()
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  /** Selected branch name per repo id; falls back to the default branch. */
  const [branchByRepo, setBranchByRepo] = useState<Record<string, string>>({})

  const branchOf = (repo: GateRepo): RepoBranchStat =>
    repo.branchData.find((b) => b.name === branchByRepo[repo.id]) ??
    repo.branchData[0]

  /** Repo paired with its currently selected branch. */
  const views = useMemo(
    () => repos.map((repo) => ({ repo, branch: branchOf(repo) })),
    // branchOf closes over branchByRepo; listing it keeps the view in sync.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [repos, branchByRepo]
  )

  const sortedViews = useMemo(() => {
    if (!sortKey) return views
    return [...views].sort((a, b) => {
      const va = sortValue(a.repo, a.branch, sortKey)
      const vb = sortValue(b.repo, b.branch, sortKey)
      if (va == null && vb == null) return 0
      if (va == null) return 1
      if (vb == null) return -1
      if (typeof va === 'string' || typeof vb === 'string') {
        return String(va).localeCompare(String(vb))
      }
      return sortDir === 'asc' ? va - vb : vb - va
    })
  }, [views, sortKey, sortDir])

  const statusCounts = useMemo(
    () =>
      countReposByStatus(
        sortedViews.map((v) => ({ gateStatus: v.branch.gateStatus }))
      ),
    [sortedViews]
  )

  const onSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir(key === 'repo' ? 'asc' : 'desc')
    }
  }

  return (
    <div className='overflow-hidden rounded-xl border border-gate-line bg-gate-card'>
      {/* Header */}
      <div className={cn(ROW_COLS, 'h-11 border-b border-gate-grid px-4')}>
        <SortHeader
          labelKey='gate.col.repo'
          active={sortKey === 'repo'}
          dir={sortDir}
          onClick={() => onSort('repo')}
        />
        {GATE_CATEGORIES.map((c) => (
          <SortHeader
            key={c.id}
            labelKey={COL_LABEL[c.id]}
            active={sortKey === c.id}
            dir={sortDir}
            onClick={() => onSort(c.id)}
            centered
          />
        ))}
        <div className='flex h-full items-center self-stretch border-l border-gate-grid pl-2.5'>
          <SortHeader
            labelKey='gate.col.coverage'
            active={sortKey === 'coverage'}
            dir={sortDir}
            onClick={() => onSort('coverage')}
            centered
            accent
          />
        </div>
      </div>

      {/* Rows */}
      {sortedViews.map(({ repo, branch }) => (
        <div
          key={repo.id}
          className={cn(
            ROW_COLS,
            'h-12 border-b border-gate-grid px-4 transition-colors last:border-b-0 hover:bg-gate-subtle'
          )}
        >
          <div className='min-w-0'>
            <div className='flex min-w-0 items-center gap-1.5'>
              {repo.url ? (
                <a
                  href={repo.url}
                  target='_blank'
                  rel='noreferrer'
                  title={repo.url}
                  className='truncate text-sm leading-5 font-semibold text-gate-ink hover:text-gate-accent hover:underline'
                >
                  {repo.name}
                </a>
              ) : (
                <span className='truncate text-sm leading-5 font-semibold text-gate-ink'>
                  {repo.name}
                </span>
              )}
              {repo.demo ? (
                <span
                  title={t('gate.demoHint')}
                  className='inline-flex h-5 shrink-0 items-center rounded-md border border-gate-line bg-gate-track px-1.5 text-[11px] font-medium text-gate-faint'
                >
                  {t('gate.demo')}
                </span>
              ) : null}
              <BranchPicker
                repo={repo}
                selected={branch}
                onSelect={(name) =>
                  setBranchByRepo((cur) => ({ ...cur, [repo.id]: name }))
                }
              />
            </div>
            <div className='truncate text-xs leading-4 text-gate-dim'>
              {repo.owner}
            </div>
          </div>

          {GATE_CATEGORIES.map((c) => (
            <RateCell
              key={c.id}
              stat={branch.stats.find((s) => s.category === c.id)}
            />
          ))}

          <div className='flex h-full items-center self-stretch border-l border-gate-grid pl-2.5'>
            <CoverageCell
              coverage={branch.coverage}
              link={branch.coverageLink}
            />
          </div>
        </div>
      ))}

      {/* Empty state */}
      {repos.length === 0 && (
        <div className='flex flex-col items-center gap-2 py-16'>
          <SearchX className='size-8 text-gate-faint' />
          <p className='text-sm text-gate-dim'>{t('gate.empty')}</p>
        </div>
      )}

      {/* Footer sync bar */}
      <div className='flex h-10 items-center justify-between border-t border-gate-grid px-4 text-xs text-gate-faint'>
        <span className='flex items-center gap-4'>
          <span className='flex items-center gap-1.5 text-gate-dim'>
            <span className='size-1.5 animate-pulse rounded-full bg-gate-pass' />
            {t('gate.live')}
          </span>
          <span>{t('gate.synced')}</span>
        </span>
        <span>
          {t('gate.summary', {
            total: repos.length,
            passed: statusCounts.passed,
            failed: statusCounts.failed,
            running: statusCounts.running,
          })}
        </span>
      </div>
    </div>
  )
}
