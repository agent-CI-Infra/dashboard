import { createFileRoute } from '@tanstack/react-router'
import { EcosystemInfluence } from '@/features/community/ecosystem'
import { timeRangeSearchSchema } from '@/lib/monitor/ranges'

export const Route = createFileRoute('/_authenticated/ecosystem')({
  validateSearch: timeRangeSearchSchema,
  component: EcosystemInfluence,
})