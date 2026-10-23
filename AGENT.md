# AGENT.md — Dashboard 仓的 AI 操作入口

> 这是 `agent-CI-Infra/dashboard` 的仓库级说明。
> **门禁模板的完整操作手册在 [`agent-CI-Infra/ci-gates/AGENT.md`](https://github.com/agent-CI-Infra/ci-gates/blob/main/AGENT.md)。**
> 用户 AI 做门禁接入时，先读那一份；做公共/单仓 Dashboard 时，读本文件。

## 1. 仓库地图

```text
agent-CI-Infra/dashboard/
  src/                        公共多仓 Dashboard（React/Vite）
  scripts/                    公共 Dashboard 数据采集
  config/                     社区列表、门禁来源
  single-repo/                单仓 / 私仓 Dashboard 模板（Python + 静态站点）
  AGENT.md                    本文件
  docs/repository-architecture.md
```

另有门禁仓：

```text
agent-CI-Infra/ci-gates/
  scripts/gate/gate.mjs
  template/
  protocol/
  AGENT.md
```

## 2. 两种 Dashboard

| 目标 | 使用 | 入口 |
| --- | --- | --- |
| 多个代码仓汇总 | 本仓根目录 | <https://agent-ci-infra.github.io/dashboard/> |
| 一个代码仓深度视图 | 本仓 `single-repo/` | [`single-repo/README.md`](single-repo/README.md) |
| 私仓 | 本仓 `single-repo/` 本地模式 | `GSB_ALLOW_PRIVATE=1 ...` |

**不要**把私仓数据发布到公共 Dashboard。

## 3. 公共 Dashboard 接入

### 3.1 只接社区指标

1. 在 `config/internal-contributors.json` 增加 `owner/repo`。
2. 运行 `GITHUB_TOKEN=... npm run collect`。
3. 刷新线上页面。

### 3.2 接门禁

1. 源仓按 `ci-gates` 接入，产出 `ci-gates-reports`。
2. 单仓看板发布公开 snapshot 或 summary。
3. 在 `config/gate-sources.json` 增加来源：
   - 推荐：`type: "board-summary"` + `summaryUrl`；
   - 过渡：`type: "status-board-lanes"` + `url`。
4. 运行 `npm run collect:gate`。
5. 打开 `/#/gate`。

当前限制：

- `ci-gates` 直连 artifact 适配器未实现；
- `single-repo` 还没有 `data/summary.json` 生成器；
- 因此当前公开接入优先用 `status-board-lanes` 读取公开 snapshot。

### 3.3 私仓

```bash
cd single-repo
GSB_ALLOW_PRIVATE=1 GITHUB_TOKEN=... python3 publish.py --repo owner/private-repo
python3 server.py
```

公开 Dashboard 的 `gate-sources.json` 不得加入私仓。

## 4. 单仓看板

1. 复制 `single-repo/` 的内容到新仓根目录。
2. Pages → Source: GitHub Actions。
3. Actions → Setup dashboard 填源仓和 JSON。
4. 等 Collect + Deploy。
5. 源仓 CI 上传 `ci-gates-reports` 后，CI 页出现质量门禁卡片。

详细步骤见 [`single-repo/README.md`](single-repo/README.md)。

## 5. 改代码时的约束

- 根目录是 Vite 公共 Dashboard；`single-repo/` 是独立 Python 模板，不要把两者的构建混在一起。
- `single-repo/` 下的 `.github/workflows` 是给复制出去的新仓用的，不会在本仓运行。
- 不要在根目录重新添加 `templates/ci-gates`；门禁模板只维护在 `ci-gates`。
- 改动 `config/gate-sources.json` 后必须跑 `npm run collect:gate` 验证。
- 不要提交 token、私仓 snapshot、`.sync` 运行时数据。

## 6. 验证

```bash
npm run build
npm run test:collector
npm run collect:gate
```

单仓模板：

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
单仓入口：
仍需人工：
未验证：
```

没有实际验证的项写“未验证”，不要写“应该可以”。
