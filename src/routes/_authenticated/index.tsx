import { createFileRoute } from '@tanstack/react-router'
import { CommunityOverview } from '@/features/community/overview'
import { timeRangeSearchSchema } from '@/lib/monitor/ranges'

export const Route = createFileRoute('/_authenticated/')({
  validateSearch: timeRangeSearchSchema,
  component: CommunityOverview,
})