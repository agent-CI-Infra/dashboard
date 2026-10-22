import type { Lang } from '@/lib/i18n'

export type NeedKind = 'required' | 'optional' | 'gateRequired'

export type AboutCopy = {
  hero: { title: string; subtitle: string }
  site: {
    title: string
    description: string
    repoLabel: string
    siteLabel: string
    p1: string
    p2: string
  }
  add: {
    title: string
    description: string
    step1: { text: string; rulesTitle: string; rules: string[]; note: string }
    step2: { text: string; note: string }
    step3: { text: string }
    footer: string
  }
  info: {
    title: string
    description: string
    columns: { name: string; required: string; detail: string }
    needLabels: Record<NeedKind, string>
    rows: { label: string; need: NeedKind; detail: string }[]
  }
  gate: {
    title: string
    description: string
    intro: string
    configIntro: string
    bullets: string[]
    footer: string
  }
  data: {
    title: string
    description: string
    files: { codes: string[]; note: string }[]
    bullets: string[]
  }
  metrics: { title: string; bullets: string[] }
  notes: { title: string; bullets: string[] }
  feedback: { title: string; p1: string; p2: string }
}

export const ADD_SNIPPET = `{
  "your-org/your-repo": [
    "alice",
    "bob"
  ],
  "your-org/another-repo": []
}`

export const GATE_SNIPPET = `{
  "id": "your-repo",
  "name": "your-repo",
  "owner": "your-org",
  "defaultBranch": "main",
  "type": "status-board-lanes",
  "url": "https://your-org.github.io/status-board/data/snapshot.json",
  "dashboardUrl": "https://your-org.github.io/status-board/#overview",
  "boardBaseUrl": "https://your-org.github.io/status-board/",
  "channelMap": [
    { "channel": "gate", "workflow": ["CI"] },
    { "channel": "daily", "workflow": ["Nightly"] },
    { "channel": "release", "workflow": ["Release"] }
  ],
  "laneRules": {
    "pr": { "channel": ["gate"], "event": ["pull_request"] },
    "main": { "channel": ["gate"], "event": ["push", "workflow_dispatch"] },
    "daily": { "channel": ["daily"] },
    "release": { "channel": ["release"] }
  },
  "coverageLine": "main",
  "coverage": {
    "artifacts": ["node-coverage-summary"],
    "summaryPaths": ["summary.json", "coverage/summary.json"]
  },
  "caseLayers": [
    {
      "name": "UT",
      "artifacts": ["ut-results"],
      "summaries": ["ut/tagged/group-*/summary.json"]
    }
  ]
}`

const ZH: AboutCopy = {
  hero: {
    title: '关于本站',
    subtitle:
      '开源社区运营与健康度监控平台：监控哪些仓库、如何接入新仓库、需要提供哪些信息，以及数据口径与更新节奏。',
  },
  site: {
    title: '站点与源码',
    description: '一个纯静态的监控看板：社区指标 + 可选接入的质量门禁。',
    repoLabel: '仓库：',
    siteLabel: '在线访问：',
    p1: '前端不携带任何 Token，所有展示数据都来自仓库内 `public/data` 下的公开数据快照；GitHub Token 只在数据采集时通过环境变量注入，不会写入任何产物。',
    p2: '页面包括社区总览、Pull Requests、Issues、社区成长、生态影响力（暂未开放）与质量门禁。监控列表以 {config} 的键为准，新增一个键即新增一个受监控仓库。',
  },
  add: {
    title: '新增一个受监控仓库',
    description: '社区指标是默认能力；只需要改一个配置文件，无需改动前端代码。',
    step1: {
      text: '**登记仓库与内部贡献者。** 在 {config} 中新增一项：键为仓库全名 `owner/name`，值为该仓库的 **内部贡献者** GitHub 登录名数组（暂时没有可留空）：',
      rulesTitle: '该仓库的内 / 外部分类规则：',
      rules: [
        '数组内的登录名记为 **内部**；',
        '该仓库其余非 bot 作者记为 **外部**；',
        'bot 记为未知，不计入内部或外部。',
      ],
      note: '这份文件同时是监控列表与内部贡献者名单的唯一来源。仓库必须是公开仓库。',
    },
    step2: {
      text: '**采集数据。** 提交 PR 并合并（有写权限也可直接推送 `main`）后，部署工作流会自动重新采集并发布；也可以在本地执行：',
      note: '不配置 Token 时使用匿名 GitHub API（60 次/小时）；配置后额度更高，采集更稳定。Token 只从环境变量读取。',
    },
    step3: {
      text: '**确认结果。** 打开社区总览与社区成长，确认新仓库已出现。若数据为空，先看该仓库的 `meta.json` 中的 `errors`，再检查 GitHub Actions 的采集日志。',
    },
    footer:
      '采集与发布流程见 {collect} 和 {deploy} 两个文件。临时覆盖监控列表可用环境变量 `MONITOR_REPOS`（逗号分隔），但长期接入仍建议写进配置文件。',
  },
  info: {
    title: '需要提供的信息',
    description: '除仓库全名外都可以后补：先接社区指标，再按需接入质量门禁。',
    columns: {
      name: '信息',
      required: '是否必须',
      detail: '说明 / 示例',
    },
    needLabels: {
      required: '必填',
      optional: '可选',
      gateRequired: '接入门禁时必填',
    },
    rows: [
      {
        label: '仓库全名',
        need: 'required',
        detail: '公开 GitHub 仓库的 `owner/name`，例如 `your-org/your-repo`。',
      },
      {
        label: '内部贡献者',
        need: 'optional',
        detail:
          'GitHub 登录名数组；留空 `[]` 时该仓库所有非 bot 作者都算外部。',
      },
      {
        label: '默认分支',
        need: 'optional',
        detail: '默认 `main`；接入质量门禁时用于定位覆盖率与分支线。',
      },
      {
        label: '是否接入质量门禁',
        need: 'optional',
        detail: '不接入时只展示社区指标；接入后质量门禁页增加该仓库一行。',
      },
      {
        label: '门禁看板快照',
        need: 'gateRequired',
        detail:
          'status board 的 `snapshot.json` 地址：`https://<org>.github.io/<board>/data/snapshot.json`',
      },
      {
        label: '门禁看板地址',
        need: 'optional',
        detail:
          '指标 pill 的深链，例如看板的 overview 页：`https://<org>.github.io/<board>/#overview`',
      },
      {
        label: '覆盖率产物',
        need: 'optional',
        detail:
          '覆盖率阈值为 75%；产物为 Actions artifact 名称前缀与包内 summary 路径，例如 `node-coverage-summary` 和 `summary.json`。',
      },
      {
        label: '用例分层',
        need: 'optional',
        detail:
          '层级名、artifact 前缀、summary glob，例如 `UT` / `ut-results` / `ut/tagged/group-*/summary.json`。',
      },
      {
        label: '联系人',
        need: 'optional',
        detail: '采集失败或数据异常时的联系人。',
      },
    ],
  },
  gate: {
    title: '可选：接入质量门禁',
    description:
      '按仓库 / 分支展示 PR-READY、PR-MERGE、DAILY、RELEASE 通过率与 UT 覆盖率，支持 24h / 7d / 30d 窗口。',
    intro:
      '质量门禁页的列与 SD status board 的车道一一对应：`pr → PR-READY`、`main → PR-MERGE`（SD 的「主干」）、`daily → DAILY`、`release → RELEASE`。`BUILD` 列保留但 SD 看板没有对应车道，因此为空。合并队列（`gh-readonly-queue/...`）会归到目标分支；cancelled / skipped 等未决结果不计入通过率分母。',
    configIntro: '在 {gateConfig} 的 `sources` 数组中新增一项：',
    bullets: [
      '`url` / `dashboardUrl`：看板快照与看板入口；`boardBaseUrl` 用于生成指标深链。',
      '看板快照的 lane key 必须与上面的列映射一致（即 `pr`、`main`、`daily`、`release` 四种），否则该车道不会显示。',
      '`channelMap` / `laneRules`：把 workflow 名称归到 gate / daily / release 通道，并按 event / channel 定位每条车道的最新运行。',
      '`coverageLine` / `coverage`：在指定分支线上展示 UT 行覆盖率；缺少产物时显示为空，不影响通过率。',
      '`caseLayers`：每个测试层级的 artifact 前缀与 summary 文件，用于在门禁单元格上显示用例数；需要配合 `laneRules` 才能定位到最新运行。',
      '没有 status board 时，也可以用 `type: "actions-api"` 直接读 GitHub Actions，并在 `categoryMap` 中把 event / channel 映射到五个门禁列；具体匹配规则见 {gateCollect} 中的说明。',
    ],
    footer:
      '配置后执行 `npm run collect:gate` 命令，或等待部署工作流自动采集。单个数据源失败时会保留上一次的有效数据，并在 `public/data/gate/index.json` 中把该数据源标记为 `stale` 状态。',
  },
  data: {
    title: '数据产物与更新节奏',
    description:
      '站点读取的是静态快照，不是实时 API；页面上的「数据更新于」就是采集时间。',
    files: [
      { codes: ['public/data/index.json'], note: '受监控仓库清单' },
      { codes: ['public/data/config.json'], note: '公开分类规则，不含密钥' },
      { codes: ['<owner>/<name>/meta.json'], note: '采集状态与错误' },
      {
        codes: ['latest.json', 'history.json'],
        note: '累计快照 / 每日历史',
      },
      {
        codes: ['pulls.json', 'issues.json', 'contributors.json'],
        note: '明细记录',
      },
      {
        codes: ['gate/index.json', 'gate/<owner>/<name>.json'],
        note: '门禁聚合数据',
      },
    ],
    bullets: [
      '**部署工作流**：推送 `main`、每小时定时、手动触发时，先采集社区与门禁数据，再构建并发布到 GitHub Pages。',
      '**采集工作流**：每天 23:00（北京时间）增量采集社区数据并提交到 `public/data`，提交信息带 `[skip ci]`。',
      '**失败策略**：单个仓库失败不会中断其它仓库，失败时保留上一次有效数据，并把错误写进该仓库的 `meta.json`。',
      '**手动刷新**：在 Actions 中运行「Collect community data」或「Deploy to GitHub Pages」，也可以本地执行采集后提交。',
    ],
  },
  metrics: {
    title: '指标口径',
    bullets: [
      '**PR**：Open / 已合入（`merged_at` 非空）/ 关闭未合入；累计量与周期增量分开统计。',
      '**Issue**：统计时排除 PR；区分 Open / Closed；超过阈值（默认 30 天）的 Open Issue 计入长期未解决。',
      '**内 / 外部**：白名单制，按仓库区分；bot 记为未知，不计入内部或外部，各页面口径一致。',
      '**时间**：数据按 UTC 天切分存储，页面按访问者本地时区展示时间。',
      '**空值**：`null` 表示没有样本，`0` 表示真实的零，两者在图表和表格中不会混用。',
      '**门禁通过率**：窗口内 success 计为通过，failure / timed_out / startup_failure 计为失败，cancelled / skipped / neutral / stale / action_required 不计入分母；覆盖率低于 75% 标红。',
    ],
  },
  notes: {
    title: '注意事项',
    bullets: [
      '只接入**公开仓库**：数据快照会随站点公开，私有仓库的名称与指标也会被看到。',
      '没有仓库写权限时，按「需要提供的信息」整理后提交 Issue，由维护者合并配置。',
      '新增后不会立刻出现：等待下一次定时采集，或手动运行工作流 / 本地采集后刷新页面。',
      '不要把 Token 写进 `.env` 并提交；本地调试用命令行环境变量，CI 用 Actions secret。',
      '仓库改名或迁移组织时，同步更新 `internal-contributors.json` 的键与 `gate-sources.json` 的 `owner` / `name`，并清理旧的数据目录，避免残留快照。',
      '采集失败时页面仍展示上次成功的数据；如果数字长期不更新，先看 `meta.json` 的 `errors` 与 Actions 日志。',
    ],
  },
  feedback: {
    title: '反馈问题',
    p1: '接入过程中遇到问题，可以在 {issues} 提问。',
    p2: '提交 Issue 时请附上仓库全名、期望接入的页面（社区指标 / 质量门禁）、以及相关的 Actions 运行链接，便于快速定位。',
  },
}

const EN: AboutCopy = {
  hero: {
    title: 'About',
    subtitle:
      'A dashboard for open-source community operations and health: which repositories are monitored, how to add one, what information to provide, and how metrics are calculated and refreshed.',
  },
  site: {
    title: 'Site and source',
    description:
      'A fully static dashboard: community metrics plus optional Quality Gates.',
    repoLabel: 'Repository:',
    siteLabel: 'Live site:',
    p1: 'The frontend holds no tokens. All displayed data comes from the public data snapshots under `public/data` in the repository; the GitHub token is injected only as an environment variable during collection and is never written to any artifact.',
    p2: 'The site covers Community Overview, Pull Requests, Issues, Growth, Ecosystem (not yet available), and Quality Gates. The monitored list is defined by the keys in the {config} file: add a key to monitor another repository.',
  },
  add: {
    title: 'Add a monitored repository',
    description:
      'Community metrics work by default. You only edit one config file; no frontend changes are needed.',
    step1: {
      text: '**Register the repository and its internal contributors.** Add an entry to the {config} file. The key is the full repository name `owner/name`, and the value is the array of GitHub logins for the repository’s **internal contributors** (leave it empty if you do not have any yet):',
      rulesTitle:
        'How the repository classifies internal and external contributors:',
      rules: [
        'Logins in the array are counted as **internal**;',
        'Every other non-bot author is counted as **external**;',
        'Bots are counted as unknown and belong to neither group.',
      ],
      note: 'This file is the single source of truth for both the monitored list and the internal-contributor allowlist. The repository must be public.',
    },
    step2: {
      text: '**Collect data.** After you open a PR and merge it (or push directly to `main` if you have write access), the deploy workflow re-collects and publishes automatically. You can also run it locally:',
      note: 'Without a token, collection uses the anonymous GitHub API (60 requests/hour). With a token you get a higher quota and more reliable collection. The token is read only from the environment.',
    },
    step3: {
      text: '**Verify the result.** Open Community Overview and Growth and confirm the new repository appears. If the data is empty, check `errors` in the repository’s `meta.json` first, then the collection logs in GitHub Actions.',
    },
    footer:
      'See {collect} and {deploy} for the collection and deploy flows. You can temporarily override the monitored list with the `MONITOR_REPOS` environment variable (comma-separated), but long-term additions should go in the config file.',
  },
  info: {
    title: 'What to provide',
    description:
      'Everything except the full repository name can be added later: start with community metrics, then connect Quality Gates when needed.',
    columns: {
      name: 'Item',
      required: 'Required?',
      detail: 'Details / example',
    },
    needLabels: {
      required: 'Required',
      optional: 'Optional',
      gateRequired: 'Required (Gates)',
    },
    rows: [
      {
        label: 'Full repository name',
        need: 'required',
        detail:
          '`owner/name` of a public GitHub repository, for example `your-org/your-repo`.',
      },
      {
        label: 'Internal contributors',
        need: 'optional',
        detail:
          'An array of GitHub logins. Leave it as `[]` to count every non-bot author as external.',
      },
      {
        label: 'Default branch',
        need: 'optional',
        detail:
          'Defaults to `main`. Used by Quality Gates to locate coverage and branch lines.',
      },
      {
        label: 'Connect Quality Gates?',
        need: 'optional',
        detail:
          'If omitted, only community metrics are shown. When connected, the repository gets a row on the Quality Gates page.',
      },
      {
        label: 'Gate board snapshot',
        need: 'gateRequired',
        detail:
          'The status board’s `snapshot.json` URL: `https://<org>.github.io/<board>/data/snapshot.json`',
      },
      {
        label: 'Gate board URL',
        need: 'optional',
        detail:
          'Deep link for metric pills, such as the board overview: `https://<org>.github.io/<board>/#overview`',
      },
      {
        label: 'Coverage artifacts',
        need: 'optional',
        detail:
          'The threshold is 75%. Provide the Actions artifact name prefix and the summary path inside the artifact, for example `node-coverage-summary` and `summary.json`.',
      },
      {
        label: 'Test case layers',
        need: 'optional',
        detail:
          'Layer name, artifact prefix, and summary glob, for example `UT` / `ut-results` / `ut/tagged/group-*/summary.json`.',
      },
      {
        label: 'Contact',
        need: 'optional',
        detail: 'Who to contact if collection fails or the data looks wrong.',
      },
    ],
  },
  gate: {
    title: 'Optional: connect Quality Gates',
    description:
      'Shows PR-READY, PR-MERGE, DAILY, and RELEASE pass rates plus UT coverage per repository and branch, with 24h / 7d / 30d windows.',
    intro:
      'The columns on the Quality Gates page map one-to-one to the SD status board lanes: `pr → PR-READY`, `main → PR-MERGE` (the SD “mainline”), `daily → DAILY`, and `release → RELEASE`. The `BUILD` column is kept but has no corresponding lane on the SD board, so it stays empty. Merge-queue refs (`gh-readonly-queue/...`) are attributed to the target branch; unresolved results such as cancelled / skipped are excluded from the pass-rate denominator.',
    configIntro:
      'Add an entry to the `sources` array in the {gateConfig} file:',
    bullets: [
      '`url` / `dashboardUrl`: the board snapshot and board entry point. `boardBaseUrl` is used to build metric deep links.',
      'The lane keys in the board snapshot must match the column mapping above (`pr`, `main`, `daily`, `release`). Otherwise the lane is not displayed.',
      '`channelMap` / `laneRules`: map workflow names to the gate / daily / release channels and locate the latest run for each lane by event / channel.',
      '`coverageLine` / `coverage`: show UT line coverage on the specified branch line. When artifacts are missing the value is blank and does not affect the pass rate.',
      '`caseLayers`: the artifact prefix and summary file for each test layer, used to show case counts in gate cells. It relies on `laneRules` to find the latest run.',
      'Without a status board, use `type: "actions-api"` to read GitHub Actions directly and map events / channels to the five gate columns in `categoryMap`. See {gateCollect} for the exact matching rules.',
    ],
    footer:
      'After updating the config, run `npm run collect:gate` or wait for the deploy workflow. If one source fails, its last valid data is kept and the source is marked `stale` in `public/data/gate/index.json`.',
  },
  data: {
    title: 'Data artifacts and refresh cadence',
    description:
      'The site reads static snapshots, not a live API. The “data updated” timestamp on the page is the collection time.',
    files: [
      { codes: ['public/data/index.json'], note: 'monitored repository list' },
      {
        codes: ['public/data/config.json'],
        note: 'public classification rules, no secrets',
      },
      {
        codes: ['<owner>/<name>/meta.json'],
        note: 'collection status and errors',
      },
      {
        codes: ['latest.json', 'history.json'],
        note: 'cumulative snapshot / daily history',
      },
      {
        codes: ['pulls.json', 'issues.json', 'contributors.json'],
        note: 'detailed records',
      },
      {
        codes: ['gate/index.json', 'gate/<owner>/<name>.json'],
        note: 'aggregated Quality Gates data',
      },
    ],
    bullets: [
      '**Deploy workflow**: on pushes to `main`, hourly, or manual dispatch, it collects community and gate data first, then builds and deploys to GitHub Pages.',
      '**Collection workflow**: every day at 23:00 Beijing time, it incrementally collects community data and commits it to `public/data` with `[skip ci]` in the message.',
      '**Failure handling**: one failing repository does not stop the others. The last valid data is kept and the error is written to that repository’s `meta.json`.',
      '**Manual refresh**: run “Collect community data” or “Deploy to GitHub Pages” in Actions, or collect locally and commit the result.',
    ],
  },
  metrics: {
    title: 'Metric definitions',
    bullets: [
      '**PR**: Open / merged (`merged_at` is set) / closed without merging. Cumulative totals and period deltas are reported separately.',
      '**Issue**: PRs are excluded. Open and Closed are reported separately. Open issues older than the threshold (30 days by default) count as long-standing.',
      '**Internal / external**: allowlist-based and per repository. Bots are unknown and belong to neither group; every page uses the same rule.',
      '**Time**: data is bucketed by UTC day and rendered in the visitor’s local time zone.',
      '**Empty values**: `null` means no sample, while `0` means a real zero. Charts and tables never mix the two.',
      '**Gate pass rate**: within the window, success counts as passed; failure / timed_out / startup_failure count as failed; cancelled / skipped / neutral / stale / action_required are excluded from the denominator. Coverage below 75% is shown in red.',
    ],
  },
  notes: {
    title: 'Things to know',
    bullets: [
      '**Public repositories only**: data snapshots are published with the site, so private repository names and metrics would be exposed.',
      'Without repository write access, put the information from “What to provide” in an Issue and a maintainer will merge the config.',
      'A new repository does not appear immediately: wait for the next scheduled collection, or run the workflow / collect locally and refresh the page.',
      'Never commit a token in `.env`. Use command-line environment variables locally and Actions secrets in CI.',
      'When a repository is renamed or moved to another organization, update its key in `internal-contributors.json`, the `owner` / `name` in `gate-sources.json`, and remove the old data directory to avoid stale snapshots.',
      'If collection fails, the page keeps showing the last successful data. If the numbers stay stale, check `errors` in `meta.json` and the Actions logs.',
    ],
  },
  feedback: {
    title: 'Feedback',
    p1: 'If you run into a problem while connecting a repository, ask in {issues}.',
    p2: 'When opening an Issue, include the full repository name, the page you want to connect (community metrics / Quality Gates), and links to the relevant Actions runs so we can investigate quickly.',
  },
}

export const ABOUT_COPY: Record<Lang, AboutCopy> = { zh: ZH, en: EN }
