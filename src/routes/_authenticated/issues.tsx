import { createFileRoute } from '@tanstack/react-router'
import { IssuesAnalysis } from '@/features/community/issues'
import { timeRangeSearchSchema } from '@/lib/monitor/ranges'

export const Route = createFileRoute('/_authenticated/issues')({
  validateSearch: timeRangeSearchSchema,
  component: IssuesAnalysis,
})