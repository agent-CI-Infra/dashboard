# DigitalMate 单仓看板

> 数据源：`openJiuwen-ai/DigitalMate`（单仓深视图：PR / Issue / CI / 测试 / 覆盖率 / 发布）。
> 看板地址：<https://haowenz001.github.io/digitalmate-board/>
> 模板上游：[`agent-CI-Infra/dashboard/single-repo`](https://github.com/agent-CI-Infra/dashboard/tree/main/single-repo)（手册见 [`CI-workflow/AGENT.md`](https://github.com/agent-CI-Infra/CI-workflow/blob/main/AGENT.md)）。

## 当前状态

| 项 | 状态 |
| --- | --- |
| 看板仓 | 本仓（public，Pages 由 Actions 部署） |
| 源仓 | `openJiuwen-ai/DigitalMate`（**private**） |
| 采集 | 开关 `BOARD_COLLECT`（Variables），当前为 `false`；打开后定时采集 |
| 私仓放行 | 已加 `GSB_ALLOW_PRIVATE_PUBLISH=1` 开关，允许 private 源仓发布到公开 Pages |
| 凭据 | Actions Secret `SOURCE_TOKEN`（只读 private 源仓）+ 本仓 `GITHUB_TOKEN`（写 site/） |

> ⚠️ **打开 `BOARD_COLLECT` 即等于公开私仓内容**：这是公开 Pages 站点，发布出去的 PR 标题、Issue 标题、测试用例名、文件树、失败命令都会对所有人可见，并进入 git 历史（无法通过删站点彻底撤回）。

## 与上游模板的差异

除了 `board-config.json` 和 `collect-selfhosted.yml`，本仓相对模板改了三处 Python 代码，用来放行「private 源仓 → 公开 Pages」。上游默认是拒绝的：

| 文件 | 改动 |
| --- | --- |
| `gsb/config.py` | 新增 `allow_private_publish`，读取 `GSB_ALLOW_PRIVATE_PUBLISH` |
| `gsb/project.py` | 私仓检查改为 `allow_private or allow_private_publish` |
| `gsb/sync.py` | 同上（增量采集路径） |
| `publish.py` | 发布前始终校验源仓可见性，只有 `GSB_ALLOW_PRIVATE_PUBLISH=1` 才跳过 |
| `.github/workflows/collect-selfhosted.yml` | 源仓用 `secrets.SOURCE_TOKEN`，发布用 `github.token`，并设置上述开关 |

`publish.py` 对**目标仓**必须是 public 的检查没有放开（公开 Pages 的必要条件）。

## 启用步骤

1. 确认 Secret 已存在：**Settings → Secrets and variables → Actions → Secrets** 里的 `SOURCE_TOKEN` 必须有源仓 `Contents / Issues / Pull requests / Actions / Checks / Commit statuses` 的读权限。
2. **Settings → Secrets and variables → Actions → Variables** 把 `BOARD_COLLECT` 改成 `true`。
3. **Actions → Collect dashboard data (PAT) → Run workflow**（首次不勾 `--incremental`）。
4. 等 `Deploy dashboard Pages` 成功后打开 <https://haowenz001.github.io/digitalmate-board/>。

回退：把 `BOARD_COLLECT` 改回 `false` 可停止后续采集；已发布的数据仍需手工清理 `site/`、`.sync/` 和 git 历史。

## 本地预览（不发布）

```bash
GSB_ALLOW_PRIVATE=1 GITHUB_TOKEN=... \
  python3 publish.py --repo openJiuwen-ai/DigitalMate --incremental
python3 server.py          # http://127.0.0.1:8790，仅回环地址
```

本地生成的 `dist/`、`.sync/`、`.cache/` 已在 `.gitignore` 中，不要提交带私仓数据的快照。

## 源仓侧还需要

看板只消费证据：`CI` workflow 的 `ci-gates-reports`（已接入），以及 `ut-results` / `st-results` / `e2e-results` 产物（DigitalMate 目前还没有这些）。详见 [`docs/digitalmate-integration.md`](docs/digitalmate-integration.md)。

## 文档

- [接入说明（源仓侧要做什么）](docs/digitalmate-integration.md)
- [模板原 README](docs/template-readme.md)
- [测试报告契约](docs/test-reports.md)
- [增量同步与历史](docs/incremental-history.md)
- [页面与交互](docs/dashboard-pages.md)
- [分支线](docs/branch-lines.md)

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
node test/sync-e2e.mjs
```

`Dashboard checks` 工作流在每次推送（`site/**`、`.sync/**` 除外）时自动跑上面的检查。
