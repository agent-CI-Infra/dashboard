import { useMemo } from 'react'
import { getRouteApi } from '@tanstack/react-router'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ThemeSwitch } from '@/components/theme-switch'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  repoKey,
  useActivityScores,
  useIssues,
  useSelectedRepo,
} from '@/lib/monitor/data'
import { formatDurationHours, formatNumber } from '@/lib/monitor/format'
import { ChartCard } from './components/chart-card'
import { MonitorHeader } from './components/monitor-header'
import { MonitorTable } from './components/monitor-table'
import { RepoFilter } from './components/repo-filter'
import { ErrorState, LoadingState } from './components/states'
import { TimeRangeFilter } from './components/time-range-filter'
import { TrendChart } from './components/trend-chart'
import { issuesColumns } from './issues-columns'
import { useMonitorBase } from './use-monitor'

const route = getRouteApi('/_authenticated/issues')

export function IssuesAnalysis() {
  const search = route.useSearch()
  const navigate = route.useNavigate()
  const repoList = useSelectedRepo(search)
  const scores = useActivityScores(repoList.repos)

  // Repositories ranked by community activity, highest first.
  const orderedRepos = [...repoList.repos].sort(
    (a, b) => (scores.get(repoKey(b)) ?? -1) - (scores.get(repoKey(a)) ?? -1)
  )

  // Default to the highest-activity repository when the URL does not pin one.
  const selectedKey = search.repo
    ? repoList.key
    : orderedRepos[0]
      ? repoKey(orderedRepos[0])
      : repoList.key

  const base = useMonitorBase(selectedKey, search)
  const issues = useIssues(selectedKey)

  // Activity scores are `null` until every repository's data has loaded.
  const scoresReady = [...scores.values()].every((value) => value !== null)

  const records = issues.data ?? []

  // Cumulative Issue count and cumulative comment count as of each day, counted
  // from the full record set (not the young daily history). Comments are summed
  // over the Issues opened up to that day. Fixed ranges keep their window;
  // "全部历史" spans from the first Issue to today.
  const cumulative = useMemo(() => {
    const { from, to, key } = base.range
    const opened = [...records].sort((a, b) =>
      a.createdAt.localeCompare(b.createdAt)
    )
    const startDay =
      key === 'all'
        ? opened[0]?.createdAt.slice(0, 10)
        : from?.toISOString().slice(0, 10)
    const endDay = to.toISOString().slice(0, 10)
    if (!startDay || startDay > endDay) return []
    const points: { date: string; total: number; comments: number }[] = []
    let seen = 0
    let comments = 0
    const cursor = new Date(`${startDay}T00:00:00.000Z`)
    while (cursor.toISOString().slice(0, 10) <= endDay) {
      const day = cursor.toISOString().slice(0, 10)
      while (
        seen < opened.length &&
        opened[seen].createdAt.slice(0, 10) <= day
      ) {
        comments += opened[seen].comments
        seen += 1
      }
      points.push({ date: day.slice(5), total: seen, comments })
      cursor.setUTCDate(cursor.getUTCDate() + 1)
    }
    return points
  }, [records, base.range])

  // Summary stats shown between the trend chart and the detail table.
  const stats = useMemo(() => {
    const total = records.length
    const commentTotal = records.reduce((sum, r) => sum + (r.comments ?? 0), 0)
    const openIssues = records.filter((r) => r.state === 'open')
    const externalMax = openIssues
      .filter((r) => r.contributionType === 'external')
      .reduce<number | null>(
        (max, r) =>
          r.waitingHours === null
            ? max
            : max === null || r.waitingHours > max
              ? r.waitingHours
              : max,
        null
      )
    const waiting = openIssues
      .map((r) => r.waitingHours)
      .filter((h): h is number => h !== null)
    const avgWaiting =
      waiting.length === 0
        ? null
        : waiting.reduce((sum, h) => sum + h, 0) / waiting.length
    return { total, commentTotal, externalMax, avgWaiting }
  }, [records])

  const onSearchChange = (next: typeof search) =>
    navigate({ search: (prev) => ({ ...prev, ...next }) })

  if (repoList.isError) {
    return (
      <>
        <Header>
          <div className='me-auto' />
          <ThemeSwitch />
        </Header>
        <Main>
          <ErrorState onRetry={() => repoList.refetch()} />
        </Main>
      </>
    )
  }

  if (base.isLoading || (!search.repo && !scoresReady)) {
    return (
      <>
        <Header>
          <div className='me-auto' />
          <ThemeSwitch />
        </Header>
        <Main>
          <LoadingState />
        </Main>
      </>
    )
  }

  if (base.isError) {
    return (
      <>
        <Header>
          <div className='me-auto' />
          <ThemeSwitch />
        </Header>
        <Main>
          <ErrorState onRetry={() => base.meta.refetch()} />
        </Main>
      </>
    )
  }

  const latest = base.latest.data
  const issuesData = latest?.issues

  return (
    <>
      <Header>
        <div className='me-auto' />
        <ThemeSwitch />
      </Header>

      <Main className='flex flex-1 flex-col gap-6'>
        <MonitorHeader
          title='Issue 分析'
          description=''
          meta={base.meta.data}
          search={search}
          onSearchChange={onSearchChange}
          showRange={false}
        />

        <RepoFilter
          repos={orderedRepos}
          value={selectedKey}
          onChange={(key) => onSearchChange({ repo: key })}
        />

        <ChartCard
          title='Issue 累计总数与评论累计数增长趋势'
          description=''
          action={<TimeRangeFilter value={search} onChange={onSearchChange} />}
          empty={cumulative.length === 0}
          emptyMessage='暂无历史数据。'
        >
          <TrendChart
            data={cumulative}
            xKey='date'
            series={[
              {
                key: 'total',
                label: 'Issue 累计',
                color: 'var(--chart-1)',
                type: 'line',
              },
              {
                key: 'comments',
                label: '评论累计',
                color: 'var(--chart-2)',
                type: 'line',
                yAxis: 'right',
              },
            ]}
            yDomain={['auto', 'auto']}
            y2Domain={['auto', 'auto']}
          />
        </ChartCard>

        <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-4'>
          {[
            { label: 'Issue 累计数量', value: formatNumber(stats.total) },
            {
              label: 'Issue 评论累计数量',
              value: formatNumber(stats.commentTotal),
            },
            {
              label: '外部贡献者 Issue 最大滞留时长',
              value: formatDurationHours(stats.externalMax),
            },
            {
              label: 'Issue 平均滞留时长',
              value: formatDurationHours(stats.avgWaiting),
            },
          ].map((item) => (
            <Card key={item.label}>
              <CardContent className='flex flex-col gap-1 p-4'>
                <span className='text-xs text-muted-foreground'>
                  {item.label}
                </span>
                <span className='text-2xl font-semibold tabular-nums'>
                  {item.value}
                </span>
              </CardContent>
            </Card>
          ))}
        </div>

        <Card>
          <CardHeader>
            <CardTitle className='text-base'>Issue 明细</CardTitle>
          </CardHeader>
          <CardContent>
            <MonitorTable
              data={records}
              columns={issuesColumns}
              searchPlaceholder='按标题、作者或编号搜索 Issue…'
              filters={[
                {
                  columnId: 'state',
                  title: '状态',
                  options: [
                    { label: '开启中', value: 'open' },
                    { label: '已关闭', value: 'closed' },
                  ],
                },
                {
                  columnId: 'contributionType',
                  title: '类型',
                  options: [
                    { label: '内部', value: 'internal' },
                    { label: '外部', value: 'external' },
                    { label: '未知', value: 'unknown' },
                  ],
                },
              ]}
              emptyMessage='尚未采集到 Issue。'
            />
          </CardContent>
        </Card>

        <p className='text-xs text-muted-foreground'>
          数据集中的 Issue 总数：{formatNumber(issuesData?.total)}。
        </p>
      </Main>
    </>
  )
}