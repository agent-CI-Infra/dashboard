import { createFileRoute } from '@tanstack/react-router'
import { PullsAnalysis } from '@/features/community/pulls'
import { timeRangeSearchSchema } from '@/lib/monitor/ranges'

export const Route = createFileRoute('/_authenticated/pulls')({
  validateSearch: timeRangeSearchSchema,
  component: PullsAnalysis,
})