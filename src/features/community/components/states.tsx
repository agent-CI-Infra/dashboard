import { AlertTriangle, Inbox, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'

type StateProps = {
  className?: string
  title?: string
  description?: string
}

export function LoadingState({ className, title = '正在加载数据…' }: StateProps) {
  return (
    <div
      className={cn(
        'flex min-h-40 flex-col items-center justify-center gap-3 text-muted-foreground',
        className
      )}
    >
      <Loader2 className='size-6 animate-spin' />
      <p className='text-sm'>{title}</p>
    </div>
  )
}

export function EmptyState({
  className,
  title = '暂无数据',
  description = '该部分尚未采集到数据。',
}: StateProps) {
  return (
    <div
      className={cn(
        'flex min-h-40 flex-col items-center justify-center gap-3 text-center text-muted-foreground',
        className
      )}
    >
      <Inbox className='size-6' />
      <div className='space-y-1'>
        <p className='text-sm font-medium text-foreground'>{title}</p>
        <p className='max-w-sm text-xs'>{description}</p>
      </div>
    </div>
  )
}

export function ErrorState({
  className,
  title = '无法加载数据',
  description = '数据文件无法读取，可能尚未发布。',
  onRetry,
}: StateProps & { onRetry?: () => void }) {
  return (
    <Card className={cn('border-destructive/40', className)}>
      <CardContent className='flex min-h-40 flex-col items-center justify-center gap-3 text-center'>
        <AlertTriangle className='size-6 text-destructive' />
        <div className='space-y-1'>
          <p className='text-sm font-medium'>{title}</p>
          <p className='max-w-md text-xs text-muted-foreground'>{description}</p>
        </div>
        {onRetry ? (
          <Button variant='outline' size='sm' onClick={onRetry}>
            <RefreshCw className='me-2 size-4' />
            重试
          </Button>
        ) : null}
      </CardContent>
    </Card>
  )
}