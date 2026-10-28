# AGENT.md — Dashboard 仓的 AI 操作入口

> 这是 `agent-CI-Infra/dashboard` 的仓库级说明。
> **CI workflow（含门禁）的完整操作手册在 [`agent-CI-Infra/CI-workflow/AGENT.md`](https://github.com/agent-CI-Infra/CI-workflow/blob/main/AGENT.md)。**
> 用户 AI 做门禁接入时，先读那一份；做公共/单仓 Dashboard 时，读本文件。

## 1. 仓库地图

```text
agent-CI-Infra/dashboard/
  src/                        公共多仓 Dashboard（React/Vite）
  scripts/                    公共 Dashboard 数据采集
  scripts/assemble-boards.mjs 把 board/* 分支汇总进 Pages artifact
  config/                     社区列表、门禁来源
  single-repo/                单仓 / 私仓采集器（Python + 静态站点）
  .github/workflows/
    board-create.yml          创建 board/<owner>/<repo> 数据分支
    board-collect.yml         采集并写回所有 board/* 分支
    deploy.yml                构建公共站并汇总发布 /boards/
  AGENT.md                    本文件
  docs/repository-architecture.md
```

`board/<owner>/<repo>` 是**数据分支**，只放 `board-config.json`、`site/data/`、`.sync/`；采集器代码和静态资源始终来自 `main`。`board-collect.yml` 使用 `--publish-data-only`，不会把静态资源写进分支。

另有 CI-workflow 仓：

```text
agent-CI-Infra/CI-workflow/
  scripts/gate/gate.mjs
  template/
  protocol/
  AGENT.md
```

## 2. 两种 Dashboard

| 目标 | 使用 | 入口 |
| --- | --- | --- |
| 多个代码仓汇总 | 本仓根目录 | <https://agent-ci-infra.github.io/dashboard/> |
| 一个公开代码仓深度视图 | 本仓 `board/*` 分支 + `single-repo/` 采集器 | <https://agent-ci-infra.github.io/dashboard/boards/> |
| 私仓 | 本仓 `single-repo/` 本地模式 | `GSB_ALLOW_PRIVATE=1 ...` |

**不要**把私仓数据发布到公共 Dashboard，也不要为私仓创建 `board/*` 分支。

## 3. 公共 Dashboard 接入

### 3.1 只接社区指标

1. 在 `config/internal-contributors.json` 增加 `owner/repo`。
2. 运行 `GITHUB_TOKEN=... npm run collect`。
3. 刷新线上页面。

### 3.2 接门禁 + 单仓看板

1. 源仓按 `CI-workflow` 接入，产出 `ci-gates-reports`。
2. 在本仓 Actions 运行 **Create board**，填写公开源仓：
   - `source_repository`：`owner/repo`；
   - `config_json`：可选，支持 `label`、`workflows`、`branch_lines`、`review_sla_days`、`stale_days`。
3. 工作流创建 `board/<owner>/<repo>` 分支并触发 `Collect board data`。
4. `Deploy to GitHub Pages` 汇总发布到 `/boards/<owner>/<repo>/`。
5. 在 `config/gate-sources.json` 增加来源（当前优先 `status-board-lanes`）：
   - 推荐目标：`type: "board-summary"` + `summaryUrl`；
   - 当前过渡：`type: "status-board-lanes"` + `url`。
6. 运行 `npm run collect:gate`。
7. 打开 `/#/gate`。

当前限制：

- `CI-workflow` 直连 artifact 适配器未实现；
- `single-repo` 还没有 `data/summary.json` 生成器；
- 因此当前公开接入优先用 `status-board-lanes` 读取公开 snapshot。

### 3.3 私仓

```bash
cd single-repo
GSB_ALLOW_PRIVATE=1 GITHUB_TOKEN=... python3 publish.py --repo owner/private-repo
python3 server.py
```

公开 Dashboard 的 `gate-sources.json` 不得加入私仓。

## 4. 单仓看板（公仓托管）

1. 不需要复制 `single-repo/`，也不需要新建看板仓。
2. 在本仓运行 **Create board**，填公开源仓和可选 JSON。
3. 等 `Collect board data` → `Deploy to GitHub Pages`。
4. 打开 `https://agent-ci-infra.github.io/dashboard/boards/<owner>/<repo>/`。
5. 源仓 CI 上传 `ci-gates-reports` 后，CI 页出现质量门禁卡片。

GitHub Pages 一个仓只能发布一个站点；`board/*` 分支只是数据源，不是独立站点。`scripts/assemble-boards.mjs` 在 Vite 构建后把所有分支的 `site/data/` 复制到 `dist/boards/<owner>/<repo>/`，再统一部署。

详细说明见 [`single-repo/README.md`](single-repo/README.md)。

## 5. 改代码时的约束

- 根目录是 Vite 公共 Dashboard；`single-repo/` 是 Python 采集器 + 静态站点，不要把两者的构建混在一起。
- 不要在根目录重新添加 `templates/ci-gates`；门禁模板只维护在 `CI-workflow`。
- 不要恢复“复制模板到新仓”的流程；公开看板统一走 `board/*` 分支。
- 不要手改 `board/*` 分支；只通过 `board-create.yml` / `board-collect.yml` 写入。
- 分支里只允许 `board-config.json`、`site/data/`、`.sync/`；静态资源和采集器代码由 `main` 注入。
- 私仓数据、token、私仓的 `.sync` 运行时数据不得进入公开分支或 Pages；公开 board 的 `.sync` 只保存采集游标。
- 改动 `config/gate-sources.json` 后必须跑 `npm run collect:gate` 验证。
- 改动 `scripts/assemble-boards.mjs` 后跑 `node --test scripts/assemble-boards.test.mjs`。

## 6. 验证

```bash
npm run build
npm run test:collector
npm run collect:gate
```

单仓采集器：

```bash
cd single-repo
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

## 7. 输出要求

AI 完成任务后报告：

```text
改动文件：
验证命令与结果：
公共入口：
单仓看板入口：
仍需人工：
未验证：
```

没有实际验证的项写“未验证”，不要写“应该可以”。
