# 仓库分工与接入架构

> 状态：建议方案，待评审。
> 目标：明确 `agent-CI-Infra/dashboard`、`haowenz001/github-status-board` 的边界，并回答是否新增门禁模板仓。

## 1. 结论

**建议新增一个门禁仓，但不是第三个 Dashboard。**

三仓分工：

| 仓库 | 定位 | 负责 | 不负责 |
| --- | --- | --- | --- |
| `agent-CI-Infra/ci-gates`（建议新增） | 门禁契约与模板 | reusable workflow、`gate.mjs`、报告/用例 schema、starter 模板、`AGENT.md`、版本标签 | Dashboard UI、站点发布、仓库发现 |
| `agent-CI-Infra/dashboard` | 公共多仓 Dashboard | 跨仓聚合、`/gate` 页面、来源登记、公开 Pages、接入入口 | 运行用户 CI、保存私仓数据、复制门禁实现 |
| `haowenz001/github-status-board` | 单仓/私仓 Dashboard 模板 | 单仓采集、单仓页面、Setup/Collect/Pages、私仓本地模式 | 门禁协议定义、公共跨仓聚合 |

关键原则：

1. **门禁只实现一次**。源仓 CI、单仓看板、公共 Dashboard 都消费同一份 `ci-gates` 发布物。
2. **公共 Dashboard 不直接执行用户门禁**。它只消费公开 summary 或公开 snapshot。
3. **私仓默认不进入公共 Dashboard**。私仓使用单仓看板本地模式；公开 Pages 会泄露数据。

## 2. 两个现有仓库的边界

### 2.1 `agent-CI-Infra/dashboard`

公共、多仓、汇总型：

- 展示 15 个受监控仓库的社区指标、PR、Issue、成长、质量门禁。
- `/gate` 是跨仓门禁视图；`/#/gate-onboarding` 是接入教程。
- `config/internal-contributors.json` 决定社区监控列表。
- `config/gate-sources.json` 决定门禁数据来源。
- 当前 `templates/ci-gates/` 是门禁模板的一份副本，属于历史过渡。

它不应该成为：

- 用户 CI 的执行器；
- 用户私仓数据的存储；
- 唯一一份 `gate.mjs` 的来源。

### 2.2 `haowenz001/github-status-board`

单仓、可私有、可发布：

- 从模板创建后，只展示一个源仓。
- `Setup dashboard` 写 `board-config.json`，`Collect dashboard data (PAT)` 采集，`Deploy dashboard Pages` 发布。
- 支持公开 Pages 和本机 `GSB_ALLOW_PRIVATE=1` 私仓模式。
- 当前 `source-template/` 是门禁模板的另一份副本，也是历史过渡。

它不应该成为：

- 公共 Dashboard 的跨仓聚合器；
- 门禁协议的唯一权威来源。

## 3. 为什么需要 `ci-gates`

当前门禁模板有两份：

```text
dashboard/templates/ci-gates/
github-status-board/source-template/
```

它们包含同一套 `gate.mjs`、`ci-gates.json`、case catalog 和 workflow。两份副本会漂移，尤其是：

- 报告 schema 变更；
- 用例校验规则变更；
- workflow 权限/触发器调整；
- `AGENT.md` 操作步骤变化。

新增 `ci-gates` 后：

```text
用户源仓 ──uses──► ci-gates reusable workflow @v1
                         │
                         ├── 产出 ci-gates-reports
                         ▼
               github-status-board（单仓）
                         │
                         ├── data/snapshot.json（单仓页面）
                         └── data/summary.json（公开白名单摘要）
                                 │
                                 ▼
               dashboard（公共多仓） /gate
```

`ci-gates` 应包含：

```text
.github/workflows/gate.yml       # reusable workflow
scripts/gate/gate.mjs
schema/gate-report.schema.json
schema/case-catalog.schema.json
schema/board-summary.schema.json
templates/                       # 可复制的 starter
AGENT.md                         # 面向用户 AI 的唯一权威操作手册
```

版本策略：

- `v1` 标签给用户仓和两个 Dashboard 引用；
- 破坏性 schema 变更升 `v2`；
- Dashboard 读取 summary 的 `schema` 字段，不按仓库 main 分支猜版本。

## 4. 数据契约

### 4.1 源仓产出

源仓 CI 上传：

```text
ci-gates-reports/
  build.json
  pr-ready.json
  ...
```

每个文件符合 `ci-gates/gate-report@1`。

### 4.2 单仓看板发布

单仓看板负责两类输出：

1. **完整 snapshot**：`data/snapshot.json`，给单仓页面用；只适合公开仓公开部署。
2. **公开摘要**：`data/summary.json`，符合 `ci-gates/board-summary@1`；给公共 Dashboard 用。

摘要只允许白名单字段：

- 仓库身份、默认分支；
- 生成时间、schema 版本；
- lane 结论/通过率；
- 用例总数、ready/todo/skipped；
- 接入阶段；
- board URL。

不得包含：源码、日志、Issue/PR 正文、token、私有测试数据。

> 当前 `github-status-board` 尚未生成 `data/summary.json`，这是接入公共 Dashboard 前必须补的一项。短期可继续用 `status-board-lanes` 读取公开 snapshot，但不适合长期多仓扩展。

### 4.3 公共 Dashboard 来源

`config/gate-sources.json` 支持：

```jsonc
// 推荐：单仓看板发布白名单摘要
{
  "id": "owner/repo",
  "name": "repo",
  "owner": "owner",
  "type": "board-summary",
  "summaryUrl": "https://owner.github.io/repo/data/summary.json",
  "boardUrl": "https://owner.github.io/repo/",
  "defaultBranch": "main"
}
```

```jsonc
// 过渡：直接读取单仓 snapshot
{
  "id": "owner/repo",
  "type": "status-board-lanes",
  "url": "https://owner.github.io/repo/data/snapshot.json"
}
```

`ci-gates` 直连 artifact 的适配器目前还没有实现；不要把它当成已可用路径。

## 5. 用户如何接入公共 Dashboard

推荐流程：

1. 用户源仓接入 `ci-gates`（可直接用 starter 或 reusable workflow）。
2. 用户从 `github-status-board` 模板创建单仓看板。
3. 单仓看板运行 `Setup dashboard`，生成 `board-config.json` 和 `data/summary.json`。
4. 用户在公共 Dashboard 提交接入 issue，提供：
   - 源仓 URL；
   - 单仓看板 URL；
   - `summary.json` URL；
   - 默认分支；
   - 确认源仓和摘要都是公开的。
5. 公共 Dashboard 的接入 workflow 校验：
   - 源仓 public；
   - summary URL 可访问；
   - schema 是 `ci-gates/board-summary@1`；
   - `summary.repository` 与申请仓库一致；
   - 字段在白名单内。
6. 校验通过后，workflow 自动开 PR 修改 `config/gate-sources.json`；人工合并。
7. `collect-gate` 重新采集，`/gate` 出现该仓库。

**不要让用户直接提交任意 gate JSON 或任意 URL。** Dashboard 只接受受 schema 约束的 summary，避免注入和私仓数据泄露。

## 6. 私有源仓

- 私有源仓不进入公共 Dashboard。
- 私有源仓使用 `github-status-board` 本地模式：
  ```bash
  GSB_ALLOW_PRIVATE=1 GITHUB_TOKEN=... python3 publish.py --repo owner/private-repo
  python3 server.py
  ```
- 单仓看板可以部署到有访问控制的私有 Pages/内网；公共 GitHub Pages 不可以。
- 不要在公共 Dashboard 的 `gate-sources.json` 中配置私仓 snapshot URL。

## 7. 迁移顺序

1. 新建 `agent-CI-Infra/ci-gates`，把现有两份门禁模板收敛进去，打 `v1`。
2. 在 `ci-gates` 中保留唯一一份 `AGENT.md`，供用户 AI 使用。
3. `github-status-board` 改为引用 `ci-gates` 发布物，删除 `source-template` 的重复实现。
4. 给 `github-status-board` 增加 `data/summary.json` 生成器，发布白名单摘要。
5. `dashboard` 删除 `templates/ci-gates` 副本，接入 `board-summary` 来源和接入 issue workflow。
6. 用 `acme/web` 或一个新公开仓做端到端验收。

## 8. 如果坚持两个仓库

可以暂不新增 `ci-gates`：

- 把 `dashboard/templates/ci-gates` 作为唯一模板；
- `github-status-board` 只引用该模板的 release，不再复制；
- 两边 README 都明确“模板主副本在 dashboard”。

代价是门禁版本与 Dashboard 发布耦合，且用户仓无法独立引用一个稳定的门禁 release。对当前规模可以接受，但不建议长期维持。
