# DigitalMate 源仓接入说明

看板只消费源仓的证据，不执行源仓的测试。要让 CI 页、测试页有数据，`openJiuwen-ai/DigitalMate` 需要先产出两类东西：

| 看板区域 | 依赖 | 现状 |
| --- | --- | --- |
| 质量门禁卡片 | workflow `CI` 上传的 artifact `ci-gates-reports` | 已接入（2026-10-28） |
| 单测 / ST / E2E 卡片 | 名字为 `ut-results` / `st-results` / `e2e-results` 的 artifact | 现为 `hypium-result`、`orbit-st-<run_id>`、`e2e-nightly-<run_id>`，**名字不匹配** |
| 覆盖率卡片 | `node-coverage-summary-<event>-<sha>` | 当前只写 `coverage-report.txt` |

## 1. 门禁（Phase A）

按 [`CI-workflow/AGENT.md`](https://github.com/agent-CI-Infra/CI-workflow/blob/main/AGENT.md) 复制四个文件：

```
.github/workflows/ci.yml            # name 必须是 CI，job 名 gate
.github/ci-gates.json               # lane 配置
scripts/gate/gate.mjs               # 上游脚本，记录来源 commit
test/cases/case-catalog.yaml        # 先空 catalog
```

要点：

- artifact 名必须是 `ci-gates-reports`，内容为 `gate-reports/*.json`（schema `ci-gates/gate-report@1`）。
- `name: CI` 只负责产出 `ci-gates-reports`，供“质量门禁”卡片；它不是 PR/主干 lane 的门禁来源。本仓 `board-config.json` 的 `workflows.gate` 是 `["^Code Check$", "^UT$"]`，与源仓实际的 PR 门禁一致。
- 首期用空 catalog，并从 `pr-ready` 的 commands 里去掉 `verify-cases`；补用例时 `ready` 必须有 `file` + `owner` + 文件内 `@case <id>` marker。

### lane 映射建议

| lane | 命令 | Runner | 说明 |
| --- | --- | --- | --- |
| `build` | `hvigorw assembleHap ...` | 自建 macOS（DevEco） | 首期可留空，报告为 `not_applicable`，不假绿 |
| `pr-ready` | `node --test tests/unit/*.test.mjs tests/unit/ascf-device/*.test.mjs tests/e2e/workflow/workflow.test.mjs`（+ codelinter） | ubuntu-latest | 复用现有 `node-ut` 与 `code-check` 的命令 |
| `pr-merge` | 同 `build` | 自建 macOS | `merge_group` 事件 |
| `daily` | node 测试 + Hypium，`observe` + `continueOnError` | 自建 macOS | 失败不阻断 |
| `release` | `./scripts/build_pkg_harmonyos.sh` | 自建 macOS | 需要 ohpm/hvigorw |

`gate.mjs` 一个 job 会把当前事件的 lane 串行跑在**同一 runner**。托管 runner 和自建 runner 都需要的场景，拆成两个 job 各自 `gate.mjs run --lane <lane>`，再合成一个 `ci-gates-reports`。

### 安全

- 新门禁用 `pull_request` 触发（不需要 secrets），不要沿用现有 `pull_request_target` + `allow-unsafe-pr-checkout` 去执行 PR 代码。
- 自建 runner 上运行 PR 代码时，限制为同仓分支：
  `if: github.event.pull_request.head.repo.full_name == github.repository`。
- 与现有总开关 `scripts/ci-gate.mjs` / `ENABLE_CI_GATE` 不是一回事，别复用同名变量；在 `docs/developer_guide/ci.md` 里分开描述。

## 2. 测试产物名（Phase B）

看板按**精确名**取 artifact（`gsb/config.py` 的 `artifact_names` 默认四值）：

| 现有 | 目标名 | 备注 |
| --- | --- | --- |
| `hypium-result` | `ut-results` | 建议内含 `junit.xml` 或 `dashboard-summary.json`，纯文本 `test_result.txt` 只能走 `run.log` 兜底 |
| `orbit-st-<run_id>-<attempt>` | `st-results` | 现有产物里已有 `junit.xml` / `summary.json`，改名即可被解析 |
| `e2e-nightly-<run_id>-<attempt>` | `e2e-results` | 需要 `results.json` / `report.json` / JUnit 之一 |
| 无 | `node-coverage-summary-<event>-<sha>` | 覆盖率卡片；内容为 Istanbul `summary.json` 或 lcov |

改名会波及 `.github/workflows/nightly.yml` 里按名字 `download-artifact` 的 `Nightly report` job，以及 `docs/developer_guide/ci.md` 的说明，一次改完。

## 3. 切换与验收

1. 源仓转 public，并把 `BOARD_COLLECT` 设为 `true`（本仓 Actions Variables）。
2. 跑一次 `Collect dashboard data (PAT)`，然后校验：

   ```bash
   curl -fsS https://haowenz001.github.io/digitalmate-board/data/snapshot.json | python3 -c "
   import json,sys; d=json.load(sys.stdin)
   ci=d['sections']['ci']['data']
   print('repo:', d['repository']['name'])
   print('lanes:', [(x['lane'], x['verdict']) for x in ci.get('gates',{}).get('lanes',[])])
   "
   ```

3. 期望：`repo` 为 `DigitalMate`；`lanes` 非空且与这次 `CI` 运行的结论一致；CI / 测试 / 覆盖率三个页面有数据。

## 私仓发布（本仓的例外）

上游模板默认拒绝「private 源仓 → 公开 Pages」。本仓显式放行，改动集中在四个文件：

| 文件 | 行 | 作用 |
| --- | --- | --- |
| `gsb/config.py` | `allow_private_publish` | 读取 `GSB_ALLOW_PRIVATE_PUBLISH`（默认关闭） |
| `gsb/project.py` | 私仓检查 | `allow_private or allow_private_publish` |
| `gsb/sync.py` | 私仓检查（增量路径） | 同上 |
| `publish.py` | 发布前校验 | 只有 `GSB_ALLOW_PRIVATE_PUBLISH=1` 才跳过来源仓可见性检查 |

采集 workflow 里对应的环境变量：

```yaml
env:
  GITHUB_TOKEN: ${{ secrets.SOURCE_TOKEN }}      # 只读私仓
  GSB_PUBLISH_TOKEN: ${{ github.token }}          # 写本站 site/
  GSB_ALLOW_PRIVATE_PUBLISH: '1'
```

风险清单（都在公开页面上可见）：

- PR / Issue 标题与正文摘要、作者、时间；
- 测试用例名、失败原因、命令行的非敏感部分；
- 文件树、包名、workflow 名称、release 名称；
- E2E 截图与 HTML 报告（由 `e2e_records` 挑选，仍在 artifact 保留期内时）。

不会公开：token、日志原文、代码内容（只读树 API 的路径与大小）、Dependabot/安全告警（`ops` 分区在公开快照里被裁剪）。

回退：`BOARD_COLLECT=false` 停止采集；要真正收回，需要同时删除 `site/` 快照、`.sync/` 历史和 git 历史中的相关提交，再强制推送。

## 未接入前的边界

- 本仓不运行源仓的测试，也不保存源仓日志原文；没有证据时显示「未知」，不显示绿色。
- 私仓阶段禁止把快照提交到本仓；本地预览只在 `dist/`，`.gitignore` 已覆盖。
