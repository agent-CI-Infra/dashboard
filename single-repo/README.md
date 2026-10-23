# 单仓 / 私仓 Dashboard 模板

> 本目录是 `agent-CI-Infra/dashboard` 里的**单仓看板模板**，用于一个代码仓的深度视图。
> 公共多仓聚合在仓库根目录；门禁模板在 [`agent-CI-Infra/CI-workflow`](https://github.com/agent-CI-Infra/CI-workflow)。

## 两个链接

| 用途 | 链接 |
| --- | --- |
| 公共多仓 Dashboard | <https://agent-ci-infra.github.io/dashboard/> |
| 单仓/私仓 Dashboard 模板 | 本目录 [`single-repo/`](.) |
| 单仓看板在线示例 | <https://agent-ci-infra.github.io/dashboard/single-repo/> |

## 适用场景

- 需要看**一个仓库**的 PR、Issue、门禁、每日构建、覆盖率、E2E、标签化测试；
- 源仓是 **public**，可以发布到公开 GitHub Pages；
- 源仓是 **private**，只在本机/内网运行，绝不发布到公开 Pages。

跨多个仓的汇总看板请用仓库根目录的公共 Dashboard。

## 从本目录创建单仓看板

本模板不是独立 GitHub Template 仓库；需要把 `single-repo/` 的内容复制到一个新仓库的**根目录**。

```bash
git clone https://github.com/agent-CI-Infra/dashboard.git
mkdir my-service-board
cd my-service-board
git init
cp -R ../dashboard/single-repo/. .
```

然后：

1. 推到新的 public 看板仓（例如 `owner/my-service-board`）。
2. **Settings → Pages → Build and deployment → Source: GitHub Actions**。
3. **Actions → Setup dashboard → Run workflow**：
   - `source_repository`：要展示的代码仓，例如 `owner/service`；
   - `config_json`（可选）：

     ```json
     {
       "label": "service",
       "workflows": { "gate": ["^CI$", "CI Gates"], "daily": ["nightly"], "release": ["release"] },
       "branch_lines": [{ "key": "main", "ref": "main" }],
       "review_sla_days": 3,
       "stale_days": 30
     }
     ```

   - `reset`：第一次配置或切换源仓时勾选。
4. 等待 `Collect dashboard data (PAT)` → `Deploy dashboard Pages`，打开：

   ```text
   https://owner.github.io/my-service-board/
   ```

## 源仓门禁

单仓看板只消费源仓的 `ci-gates-reports` artifact，不负责运行门禁。源仓需要先接入：

- CI-workflow 仓：<https://github.com/agent-CI-Infra/CI-workflow>
- 操作手册：[`CI-workflow/AGENT.md`](https://github.com/agent-CI-Infra/CI-workflow/blob/main/AGENT.md)

源仓的 `CI` 运行上传 `ci-gates-reports` 后，看板 CI 页出现「质量门禁」卡片，显示每个 lane 的结论、命令通过数和用例 catalog 的 ready/todo/skipped 数量。

## 公开源仓 + 公开 Pages

模板默认面向**公开源仓 + 公开 Pages**：采集使用看板仓的 `GITHUB_TOKEN` 读取源仓并提交 `site/`。

## 私有源仓

私有源仓不要用 Actions 发布到公开 Pages；在本机运行：

```bash
GSB_ALLOW_PRIVATE=1 GITHUB_TOKEN=... python3 publish.py --repo owner/private-repo
python3 server.py
```

`GSB_ALLOW_PRIVATE=1` 只允许生成本机 `dist/`；`publish.py --publish-repo` 仍会拒绝私有源仓，避免误发到公开 Pages。私有源仓要对外发布，需要私有 Pages、内网 runner 或额外的访问控制，这不是本模板默认提供的。

## 本地预览

Python 3.10+，凭据来自环境或已登录的 `gh`：

```bash
python3 publish.py --repo owner/service
python3 server.py
```

此兼容命令生成有限近期快照；完整历史由 `--incremental` 模式基于目标仓自己的 `.sync/` 续跑。两种模式不要同时写同一站点。

## 文档

- [文档目录](docs/README.md)
- [增量同步与历史](docs/incremental-history.md)
- [Actions 配置](docs/actions-collection.md)
- [测试报告契约](docs/test-reports.md)
- [页面与交互](docs/dashboard-pages.md)
- [CI 分层历史](docs/ci-history-lanes.md)
- [标签化测试](docs/tagged-tests.md)
- [分支线](docs/branch-lines.md)

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 test/journey-rerun-collection.py
node test/sync-e2e.mjs
node .e2e/node_modules/playwright/cli.js test --config test/playwright.config.cjs
```

修改功能时同步更新对应文档。测试使用独立临时数据，禁止将 fixture 发布为真实看板数据。

## 上游

本模板来自 `ScienceDiscovery/github-status-board` 的单仓看板实现；`docs/upstream-workflows/` 保留上游 OIDC/Bot 采集配置作参考。
