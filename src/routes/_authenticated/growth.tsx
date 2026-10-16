import { createFileRoute } from '@tanstack/react-router'
import { GrowthTrends } from '@/features/community/growth'
import { timeRangeSearchSchema } from '@/lib/monitor/ranges'

export const Route = createFileRoute('/_authenticated/growth')({
  validateSearch: timeRangeSearchSchema,
  component: GrowthTrends,
})