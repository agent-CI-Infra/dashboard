import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  TIME_RANGES,
  TIME_RANGE_LABELS,
  type TimeRangeKey,
  type TimeRangeSearch,
} from '@/lib/monitor/ranges'

type TimeRangeFilterProps = {
  value: TimeRangeSearch
  onChange: (next: TimeRangeSearch) => void
  className?: string
}

export function TimeRangeFilter({
  value,
  onChange,
  className,
}: TimeRangeFilterProps) {
  const range: TimeRangeKey = value.range ?? '30d'

  return (
    <div
      className={cn(
        'inline-flex flex-wrap items-center gap-1 rounded-lg border p-1',
        className
      )}
    >
      {TIME_RANGES.map((key) => (
        <Button
          key={key}
          type='button'
          size='sm'
          variant={range === key ? 'secondary' : 'ghost'}
          className='h-7 px-2.5 text-xs'
          onClick={() => onChange({ ...value, range: key })}
        >
          {TIME_RANGE_LABELS[key]}
        </Button>
      ))}
    </div>
  )
}