import { createFileRoute } from '@tanstack/react-router'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ThemeSwitch } from '@/components/theme-switch'
import { GATE_REPOS } from '@/features/gate/data/gate'
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

        <RepoGateTable repos={GATE_REPOS} />
      </Main>
    </>
  )
}
