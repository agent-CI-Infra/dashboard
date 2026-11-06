# 单仓 / 私仓 Dashboard 采集器

> 本目录是 `agent-CI-Infra/dashboard` 内的**单仓采集器 + 静态站点**。
> 公开和私有源仓都由公仓的分支统一托管，**不需要复制本目录，也不需要新建看板仓**；
> 私有源仓只多一个 dashboard 仓库级只读 secret `PRIVATE_SOURCE_TOKEN`。

## 链接

| 用途 | 链接 |
| --- | --- |
| 公共多仓 Dashboard | <https://agent-ci-infra.github.io/dashboard/> |
| 单仓看板列表 | <https://agent-ci-infra.github.io/dashboard/boards/> |
| 单仓看板示例 | <https://agent-ci-infra.github.io/dashboard/single-repo/> |

## 公仓托管（推荐）

公开和私有源仓的单仓看板直接放在本仓，一个源仓对应一个数据分支：

```text
agent-CI-Infra/dashboard
  board/<owner>/<repo>       数据分支，不放采集器代码
    board-config.json        源仓与展示配置
    site/data/               snapshot、history、E2E 索引
    .sync/                   增量采集状态
  /boards/<owner>/<repo>/    由 Deploy 汇总后的公开页面
```

维护者只需要：

1. 私有源仓：在本仓 Settings → Secrets and variables → Actions 添加一次只读 repository secret `PRIVATE_SOURCE_TOKEN`，所有私有源仓共用；公开源仓跳过。
2. 在源仓接入 [`CI-workflow`](https://github.com/agent-CI-Infra/CI-workflow)。
3. 在本仓 **Actions → Create board** 填源仓 `owner/repository`，可选填配置 JSON：

   ```json
   {
     "label": "service",
     "workflows": { "gate": ["^CI$"], "daily": ["nightly"], "release": ["release"] },
     "branch_lines": [{ "key": "main", "ref": "main" }],
     "review_sla_days": 3,
     "stale_days": 30
   }
   ```

4. 等待 `Collect board data` 完成，再等待 `Deploy to GitHub Pages`，打开：

   ```text
   https://agent-ci-infra.github.io/dashboard/boards/owner/repo/
   ```

工作流分工：

- `board-create.yml` 创建 `board/<owner>/<repo>` 分支，把 `visibility` 写进 `board-config.json`，并触发首次采集；
- `board-collect.yml` 每天把采集结果写回对应分支（`--publish-data-only`，不写静态资源）；公开源用 workflow 令牌，私有源用 `PRIVATE_SOURCE_TOKEN`，发布始终用本仓令牌；
- `deploy.yml` 在构建公共 Dashboard 后，把所有分支的 `site/data/` 汇总到 `/boards/<owner>/<repo>/`，并检查私有看板只发布数据。

GitHub Pages 一个仓只能发布一个站点，所以**分支本身不是站点**；分支只存数据，由 `main` 上的 Deploy 统一发布。不要手动修改数据分支，也不要把源码或凭据写进去。

已知限制：当前汇总部署不附带 E2E HTML 报告产物，页面会回退到源仓 artifact 链接；后续可把各 board 的 `e2e-html` 产物一起汇总。

## 本地运行

私有源仓也可以不发布到 Pages，只在本机运行。克隆本仓后直接在 `single-repo/` 运行：

```bash
cd single-repo
GSB_ALLOW_PRIVATE=1 GITHUB_TOKEN=... python3 publish.py --repo owner/private-repo
python3 server.py
```

浏览器打开 <http://127.0.0.1:8790/>。`publish.py` 默认输出到 `dist/`。远程发布时源仓读取令牌和发布令牌分开：`--source-token-env SOURCE_TOKEN` 指定读取私有源的环境变量，`GSB_PUBLISH_TOKEN` 必须单独提供发布令牌；发布目标仍然必须是公开 Pages 仓。

公开源仓也可以本地预览：

```bash
cd single-repo
GITHUB_TOKEN=... python3 publish.py --repo owner/public-repo
python3 server.py
```

## 源仓门禁

单仓看板消费源仓的 `ci-gates-reports` artifact，不运行门禁。源仓接入见：

- [CI-workflow](https://github.com/agent-CI-Infra/CI-workflow)
- [CI-workflow/AGENT.md](https://github.com/agent-CI-Infra/CI-workflow/blob/main/AGENT.md)

源仓 `CI` 上传 `ci-gates-reports` 后，看板 CI 页显示「质量门禁」卡片：lane 结论、命令通过数、用例 catalog 的 ready/todo/skipped 数量。

## 文档

- [文档目录](docs/README.md)
- [增量同步与历史](docs/incremental-history.md)
- [Actions 采集（上游设计）](docs/actions-collection.md)
- [测试报告契约](docs/test-reports.md)
- [页面与交互](docs/dashboard-pages.md)
- [CI 分层历史](docs/ci-history-lanes.md)
- [标签化测试](docs/tagged-tests.md)
- [分支线](docs/branch-lines.md)
- [E2E 执行记录](docs/e2e-run-records.md)

## 验证

```bash
cd single-repo
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 test/journey-rerun-collection.py
node test/sync-e2e.mjs
node .e2e/node_modules/playwright/cli.js test --config test/playwright.config.cjs
```

修改功能时同步更新对应文档。测试使用独立临时数据，禁止把 fixture 发布为真实看板数据。

## 旧独立仓部署

旧的「复制本目录到新仓 + Setup dashboard」工作流已移到
[`docs/standalone-workflows/`](docs/standalone-workflows/)，仅作历史参考，不再推荐。

## 上游

本采集器来自 `ScienceDiscovery/github-status-board`；`docs/upstream-workflows/` 保留上游 OIDC/Bot 采集配置。
