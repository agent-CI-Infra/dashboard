import type { ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { IconGithub } from '@/assets/brand-icons'
import { TimeRangeFilter } from './time-range-filter'
import type { MonitorMeta } from '@/lib/monitor/types'
import type { TimeRangeSearch } from '@/lib/monitor/ranges'

type MonitorHeaderProps = {
  title: string
  description?: ReactNode
  meta: MonitorMeta | undefined
  search: TimeRangeSearch
  onSearchChange: (next: TimeRangeSearch) => void
  /** Render the time-range filter (default true). */
  showRange?: boolean
  /** Optional right-aligned actions (e.g. a refresh button). */
  actions?: ReactNode
}

export function MonitorHeader({
  title,
  description,
  meta,
  search,
  onSearchChange,
  showRange = true,
  actions,
}: MonitorHeaderProps) {
  return (
    <div className='flex flex-col gap-4'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='space-y-1'>
          <h1 className='text-2xl font-bold tracking-tight'>{title}</h1>
          {description ? (
            <p className='text-sm text-muted-foreground'>{description}</p>
          ) : null}
        </div>
        <div className='flex flex-wrap items-center gap-2'>
          {meta?.repo ? (
            <a
              href={meta.repo.htmlUrl}
              target='_blank'
              rel='noreferrer'
              className='inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition-colors hover:bg-muted'
            >
              <IconGithub className='size-4' />
              <span className='font-medium'>{meta.repo.fullName}</span>
              <Badge variant='outline' className='font-normal'>
                {meta.repo.defaultBranch}
              </Badge>
            </a>
          ) : null}
          {actions}
        </div>
      </div>
      {showRange ? (
        <TimeRangeFilter value={search} onChange={onSearchChange} />
      ) : null}
    </div>
  )
}