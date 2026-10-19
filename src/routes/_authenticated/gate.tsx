import { useMemo } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ThemeSwitch } from '@/components/theme-switch'
import { GATE_REPOS } from '@/features/gate/data/gate'
import { useGateRepos } from '@/features/gate/data/gate-api'
import { GateLanguageSwitch } from '@/features/gate/components/gate-language-switch'
import { RepoGateTable } from '@/features/gate/overview/repo-gate-table'
import { useLang } from '@/lib/i18n'

export const Route = createFileRoute('/_authenticated/gate')({
  component: GatePage,
})

/**
 * CI gate monitor: per-branch gate performance for the monitored
 * repositories, rendered inside the existing platform shell.
 */
function GatePage() {
  const { t } = useLang()
  const { data: collectedRepos } = useGateRepos()

  /** Collected datasets win; demo entries fill repositories without one. */
  const repos = useMemo(() => {
    const collected = collectedRepos ?? []
    const collectedIds = new Set(collected.map((repo) => repo.id))
    return [
      ...collected,
      ...GATE_REPOS.filter((r) => !collectedIds.has(r.id)).map((r) => ({
        ...r,
        demo: true,
      })),
    ]
  }, [collectedRepos])

  return (
    <>
      <Header>
        <div className='me-auto' />
        <GateLanguageSwitch />
        <ThemeSwitch />
      </Header>
      <Main className='flex flex-1 flex-col gap-6'>
        <div>
          <h1 className='text-2xl font-bold tracking-tight'>
            {t('gate.title')}
          </h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            {t('gate.subtitle')}
          </p>
        </div>

        <RepoGateTable repos={repos} />
      </Main>
    </>
  )
}
