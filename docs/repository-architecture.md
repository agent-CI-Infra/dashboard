# 仓库分工与接入架构

> 状态：已落地。
> 目标：两个 Dashboard 形态放在一个代码仓；公开单仓看板不复制模板、不新建看板仓。

## 1. 三个层次

| 仓库 / 目录 | 定位 | 负责 | 不负责 |
| --- | --- | --- | --- |
| [`agent-CI-Infra/CI-workflow`](https://github.com/agent-CI-Infra/CI-workflow) | CI workflow（含门禁）契约与模板 | workflow 模板、`gate.mjs`、报告/用例 schema、starter、`AGENT.md`、版本 tag | Dashboard UI、站点发布 |
| [`agent-CI-Infra/dashboard`](https://github.com/agent-CI-Infra/dashboard) | 公共与单仓 Dashboard 的代码仓 | 多仓聚合、`/gate`、`/boards/`、`board/*` 数据分支、公开 Pages、接入入口 | 执行用户 CI、保存私仓数据、复制门禁实现 |
| `agent-CI-Infra/dashboard/single-repo/` | 单仓/私仓采集器 | 单仓采集、静态页面、本地私仓模式 | 跨仓聚合、门禁协议定义、独立站点部署 |

原独立仓库 `haowenz001/github-status-board` 的代码已收进 `single-repo/`；不再作为独立模板仓维护。

## 2. 代码布局

```text
agent-CI-Infra/dashboard/
  src/                        公共多仓 Dashboard（React/Vite）
  scripts/                    公共 Dashboard 采集
  scripts/assemble-boards.mjs 汇总 board/* 分支到 Pages artifact
  config/                     社区列表、门禁来源、静态导入看板登记
  single-repo/                单仓 / 私仓采集器
    gsb/                      采集与快照
    static/                   单仓静态页面
    publish.py / server.py    采集与本地预览
    setup.py                  生成 board-config.json
  .github/workflows/
    board-create.yml          创建 board/<owner>/<repo>
    board-collect.yml         采集并写回 board/*
    deploy.yml                构建公共站 + 汇总分支 + 发布 Pages
```

```text
agent-CI-Infra/CI-workflow/
  scripts/gate/gate.mjs
  scripts/gate/gate.test.mjs
  template/.github/workflows/ci.yml
  template/.github/ci-gates.json
  template/test/cases/
  protocol/*.schema.json
  AGENT.md
```

## 3. 分支模型与 Pages 约束

**GitHub Pages 一个仓库只能发布一个站点。** 因此 `board/*` 分支不是独立站点，也不能各自配置 Pages。

```text
main
  ├─ src/、single-repo/、scripts/、workflows/    代码与静态资源
  └─ board/<owner>/<repo>                        数据分支（orphan）
       ├─ board-config.json
       ├─ site/data/...                          snapshot、history、E2E 索引
       └─ .sync/...                              增量采集状态
```

- 一个源仓对应一个 `board/<owner>/<repo>` 分支；分支名统一小写，避免大小写重复。
- 分支只存配置和生成数据，不放采集器代码、静态资源或 workflow。
- `board-collect.yml` 从 `main` 取采集器和静态资源，并以 `--publish-data-only` 只写回数据；缓存/静态资源不会进入分支。
- `deploy.yml` 构建 Vite 后运行 `scripts/assemble-boards.mjs`，把每个分支的 `site/data/` 复制到：

```text
dist/boards/<owner>/<repo>/
```

- 最终只有一套 Pages 站点：`/` 是多仓聚合，`/boards/` 是单仓列表，`/boards/<owner>/<repo>/` 是单仓页面。
- 不复制 `single-repo/` 到新仓，也不要在源仓里加看板代码。

## 4. 数据流

```text
用户源仓
  └─ CI（CI-workflow）─► ci-gates-reports artifact
                         │
                         ▼
       dashboard/board/<owner>/<repo> 分支
         ├─ site/data/snapshot.json  单仓页面
         └─ site/data/summary.json   公开白名单摘要（待补）
                         │  Deploy 汇总
                         ▼
       /boards/<owner>/<repo>/  单仓页面
                         │
                         ▼
       /gate                    跨仓门禁视图
```

公共 Dashboard 不运行用户 CI；单仓看板不定义门禁协议；`CI-workflow` 仓不发布站点。

## 5. 数据契约

- `ci-gates/gate-report@1`：源仓 CI 上传的 `ci-gates-reports/*.json`。
- `ci-gates/case-catalog@1`：`test/cases/case-catalog.yaml`。
- `ci-gates/board-summary@1`：单仓看板发布的公开摘要，供公共 Dashboard 读取。

公开摘要只允许白名单字段：

- 仓库身份、默认分支；
- 生成时间、schema 版本；
- lane 结论/通过率；
- 用例总数、ready/todo/skipped；
- 接入阶段；
- board URL。

不得包含源码、日志、Issue/PR 正文、token、私有测试数据。

**当前缺口**：`single-repo/` 还没有生成 `data/summary.json`。在补上之前，公共 Dashboard 用 `status-board-lanes` 读取公开 snapshot；`CI-workflow` 直连 artifact 适配器也尚未实现。

## 6. 接入

### 6.1 只接社区指标

修改 `config/internal-contributors.json`，运行 `npm run collect`。

### 6.2 接门禁 + 单仓看板

1. 源仓接入 `CI-workflow`。
2. 在本仓 Actions 运行 **Create board**：
   - `source_repository`：公开源仓 `owner/repo`；
   - `config_json`：可选，支持 `label`、`workflows`、`branch_lines`、`review_sla_days`、`stale_days`。
3. 工作流创建 `board/<owner>/<repo>` 分支并触发 `Collect board data`。
4. `Deploy to GitHub Pages` 发布到 `https://agent-ci-infra.github.io/dashboard/boards/<owner>/<repo>/`。
5. 在 `config/gate-sources.json` 增加来源：

```jsonc
// 当前可用：公开 snapshot
{
  "id": "owner/repo",
  "name": "repo",
  "owner": "owner",
  "type": "status-board-lanes",
  "url": "https://agent-ci-infra.github.io/dashboard/boards/owner/repo/data/snapshot.json"
}
```

```jsonc
// 目标：白名单摘要
{
  "id": "owner/repo",
  "name": "repo",
  "owner": "owner",
  "type": "board-summary",
  "summaryUrl": "https://agent-ci-infra.github.io/dashboard/boards/owner/repo/data/summary.json",
  "boardUrl": "https://agent-ci-infra.github.io/dashboard/boards/owner/repo/",
  "defaultBranch": "main"
}
```

6. 运行 `npm run collect:gate`，打开 `/#/gate`。

### 6.3 自动接入目标

1. 用户提交 issue，提供公开源仓；维护者运行 Create board。
2. workflow 校验：
   - 源仓 public、未归档；
   - 不能是 dashboard 仓自身；
   - 分支不存在（避免覆盖已有看板）。
3. 采集后校验 snapshot/summary 可访问且仓库匹配。
4. 自动开 PR 修改 `config/gate-sources.json`，人工合并。
5. 采集后 `/gate` 出现该仓。

不要接受任意 gate JSON 或任意 URL。

## 7. 私仓

- 默认不进入公共 Dashboard，不创建 `board/*` 分支。
- 使用 `single-repo/` 本地模式：

```bash
cd single-repo
GSB_ALLOW_PRIVATE=1 GITHUB_TOKEN=... python3 publish.py --repo owner/private-repo
python3 server.py
```

- 公开 GitHub Pages 默认不可以发布私仓数据。
- `publish.py --publish-repo` 会同时校验源仓和发布目标必须是公开仓。
- **显式例外：静态导入看板。** 源仓所有者明确同意公开 snapshot 后，在 `config/imported-boards.json` 登记分支和 key；该分支只保存 `board-config.json` 与 `site/data/snapshot.json`，由 `assemble-boards.mjs` 汇总发布。它不进入 `board-collect`，不会每日自动刷新；更新 snapshot 后需重新触发 `Deploy`。当前唯一登记的是 `openJiuwen-ai/digitalmate`。

## 8. 版本与迁移

- `CI-workflow` 打 `v1`；Dashboard 的 `.github/workflows/gate.yml` checkout `agent-CI-Infra/CI-workflow@v1`。
- 用户仓引用 `v1`，不要跟踪 `main`。
- 破坏性 schema 变更升 `v2`。
- 历史文档中的 `templates/ci-gates` / `source-template` 路径已废弃。
- “复制 `single-repo/` 到新仓 + Setup dashboard”的路径已废弃；旧 workflow 仅保留在 [`single-repo/docs/standalone-workflows/`](../single-repo/docs/standalone-workflows/) 供追溯。

## 9. 验收

- 公共 Dashboard：`npm run build`、`npm run test:collector`、`npm run collect:gate`。
- 分支汇总：`node --test scripts/assemble-boards.test.mjs`。
- 单仓采集器：`cd single-repo && python3 -m unittest discover -s tests -v`。
- `CI-workflow` 仓：`node --test scripts/gate/gate.test.mjs`。
- 线上：`/` 是公共多仓；`/boards/` 是单仓列表；`/boards/<owner>/<repo>/` 是单仓看板；`CI-workflow` 是 CI workflow（含门禁）入口。
