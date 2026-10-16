import type { ReactNode } from 'react'
import { ExternalLink } from 'lucide-react'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ThemeSwitch } from '@/components/theme-switch'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

const SITE_URL = 'https://nanzhaogang.github.io/community-ecosystem/'
const REPO_URL = 'https://github.com/nanzhaogang/community-ecosystem'
const CONFIG_URL = `${REPO_URL}/blob/main/config/internal-contributors.json`
const CONTACT_EMAIL = 'nanzhaogang@qq.com'

/** Inline code token, e.g. a file path or a login name. */
function Code({ children }: { children: ReactNode }) {
  return (
    <code className='rounded bg-muted px-1.5 py-0.5 font-mono text-xs'>
      {children}
    </code>
  )
}

/** Inline code token that links out to its source file on GitHub. */
function CodeLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target='_blank'
      rel='noreferrer'
      className='inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-primary hover:underline'
    >
      {children}
      <ExternalLink className='size-3' />
    </a>
  )
}

/** Multi-line code / data snippet. */
function CodeBlock({ children }: { children: ReactNode }) {
  return (
    <pre className='overflow-x-auto rounded-lg border bg-muted/50 p-3 font-mono text-xs leading-relaxed'>
      {children}
    </pre>
  )
}

function StepList({ children }: { children: ReactNode }) {
  return (
    <ol className='list-decimal space-y-3 ps-5 text-sm leading-relaxed marker:text-muted-foreground'>
      {children}
    </ol>
  )
}

function ExtLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target='_blank'
      rel='noreferrer'
      className='inline-flex items-center gap-1 font-medium text-primary hover:underline'
    >
      {children}
      <ExternalLink className='size-3.5' />
    </a>
  )
}

export function AboutSite() {
  return (
    <>
      <Header>
        <div className='me-auto' />
        <ThemeSwitch />
      </Header>

      <Main className='flex flex-1 flex-col gap-6'>
        <div className='space-y-1'>
          <h1 className='text-2xl font-bold tracking-tight'>关于本站</h1>
          <p className='text-sm text-muted-foreground'>
            开源社区运营与健康度监控平台：站点源码、如何接入新社区、如何维护内部贡献者名单。
          </p>
          <p className='text-sm text-muted-foreground'>
            联系方式：
            <a
              href={`mailto:${CONTACT_EMAIL}`}
              className='font-medium text-primary hover:underline'
            >
              {CONTACT_EMAIL}
            </a>
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className='text-base'>开源地址</CardTitle>
          </CardHeader>
          <CardContent className='space-y-2 text-sm leading-relaxed'>
            <p className='flex flex-wrap items-center gap-2'>
              <span className='text-muted-foreground'>仓库：</span>
              <ExtLink href={REPO_URL}>{REPO_URL}</ExtLink>
            </p>
            <p className='flex flex-wrap items-center gap-2'>
              <span className='text-muted-foreground'>在线访问：</span>
              <ExtLink href={SITE_URL}>{SITE_URL}</ExtLink>
            </p>
            <p className='text-muted-foreground'>
              站点为纯静态应用：前端不携带任何 Token，所有展示数据都来自仓库内
              <Code>public/data</Code>
              下的公开数据快照；GitHub Token 只在数据采集时通过环境变量注入，不会写入任何产物。
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className='text-base'>新增一个社区</CardTitle>
          </CardHeader>
          <CardContent>
            <StepList>
              <li>
                在 <CodeLink href={CONFIG_URL}>config/internal-contributors.json</CodeLink> 中新增该社区：键为
                <Code>owner/name</Code>，值为该仓库的
                <span className='font-medium'>内部贡献者</span>
                GitHub 登录名数组（暂无时可留空）：
                <div className='mt-2'>
                  <CodeBlock>{`{
  "ThinkFlowLab/new-repo": [
    "alice",
    "bob"
  ],
  "ThinkFlowLab/another-repo": []
}`}</CodeBlock>
                </div>
                <p className='mt-2'>该仓库的内 / 外部分类规则：</p>
                <ul className='mt-1 list-disc space-y-1 ps-5'>
                  <li>
                    数组内的登录名记为 <span className='font-medium'>内部</span>；
                  </li>
                  <li>
                    该仓库其余非 bot 作者记为 <span className='font-medium'>外部</span>；
                  </li>
                  <li>
                    bot 记为 <span className='font-medium'>未知</span>，不计入内部或外部。
                  </li>
                </ul>
                <p className='mt-2 text-muted-foreground'>
                  这份文件同时是监控列表与内部贡献者名单的唯一来源：采集脚本以它的键作为要监控的仓库、值为内部贡献者，无需再改动其它文件。
                </p>
              </li>
              <li>
                提交并推送。部署工作流（Deploy to GitHub Pages）会重新采集并自动发布，站点随即出现该社区。
              </li>
              <li>
                之后如需为某个社区补充内部贡献者，只需在该仓库的数组里按行追加登录名，再次提交推送即可，各页面会统一按此口径展示。
              </li>
            </StepList>
          </CardContent>
        </Card>
      </Main>
    </>
  )
}