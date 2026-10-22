import { useMemo } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ThemeSwitch } from '@/components/theme-switch'
import { GATE_REPOS } from '@/features/gate/data/gate'
import { deriveGateRepos, useGateDatasets } from '@/features/gate/data/gate-api'
import { LanguageSwitch } from '@/components/language-switch'
import { GateWindowSelect } from '@/features/gate/components/gate-window-select'
import { useGateWindow } from '@/features/gate/stores/gate-window'
import { RepoGateTable } from '@/features/gate/overview/repo-gate-table'
import { useLang } from '@/lib/i18n'

export const Route = createFileRoute('/_authenticated/gate')({
  component: GatePage,
})

/**
 * Quality gate monitor: per-branch gate performance for the monitored
 * repositories, rendered inside the existing platform shell.
 */
function GatePage() {
  const { t } = useLang()
  const { data: datasets } = useGateDatasets()
  const windowDays = useGateWindow((s) => s.windowDays)

  /** Collected datasets win; demo entries fill repositories without one. */
  const repos = useMemo(() => {
    const collected = deriveGateRepos(datasets ?? [], windowDays)
    const collectedIds = new Set(collected.map((repo) => repo.id))
    return [
      ...collected,
      ...GATE_REPOS.filter((r) => !collectedIds.has(r.id)).map((r) => ({
        ...r,
        demo: true,
      })),
    ]
  }, [datasets, windowDays])

  return (
    <>
      <Header>
        <div className='me-auto' />
        <LanguageSwitch />
        <ThemeSwitch />
      </Header>
      <Main className='flex flex-1 flex-col gap-6'>
        <div className='flex flex-wrap items-center gap-x-2 gap-y-1'>
          <h1 className='text-2xl font-bold tracking-tight'>
            {t('gate.title')}
          </h1>
          <p className='text-sm text-muted-foreground'>{t('gate.subtitle')}</p>
          <GateWindowSelect />
        </div>

        <RepoGateTable repos={repos} />
      </Main>
    </>
  )
}
