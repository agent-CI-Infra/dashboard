# CI 分层历史

## 功能

CI 页的「CI 分层历史」按触发方式展示该分支线窗口内的 PR、分支门禁、合并队列、Daily 与版本运行，五层共用一条日期横轴：

| 层 | 归入的 run |
| --- | --- |
| PR | 门禁工作流（默认名称 `^CI$`）的 `pull_request` / `pull_request_target` |
| 主干 / 完整分支名 | 门禁工作流在该分支线上的 `push`，以及它自己的 `workflow_dispatch`（提示中标“手动”） |
| 合并队列 | 所有 `event=merge_group` 的 run，优先于工作流名称规则 |
| Daily | 名称匹配 daily 规则（Nightly）的 run，包括 `schedule` 和手动 `workflow_dispatch` |
| 版本 | 名称匹配 release 规则（Release）的 run，包括版本 tag 推送或手动运行 |

[分支线](branch-lines.md)的每条线都有上述完整分层。非默认分支的 push / 手动门禁层使用完整分支名；例如在 `releases/v0.3.0.beta` 手动运行 Nightly，会出现在该分支线的 Daily 层。

`merge_group` 优先归入合并队列；其余名称规则取 `board-config.json` 的 `workflows.gate` / `daily` / `release`，缺省时使用相同默认值。`workflow_call` 子 run、其他工作流、缺少事件且没有关联 PR 的门禁 run，以及不属于该线门禁层的分支事件，不在分层图或最近 run 表中展示，也不追加脚注计数。采集仍保存所有 run，不过滤 Actions 查询；同一 run 的重试只展示最后一次尝试。没有事件但有关联 PR 的门禁 run 仍归 PR。

## 使用

- 默认窗口为近 30 天，日期按 UTC+8 划分，最右列是快照生成当天。副标题写明起止日期。
- 每格一次 run，颜色表示该 run 最后一次尝试的结论：成功（绿）、失败 / 超时 / 启动失败（红色斜纹）、取消 / 跳过（浅灰）、运行中 / 排队（黄）、其他如待批准（深灰）。颜色沿用原时间线图例，斜纹使红绿色弱也能区分失败。
- 同一天同一层的多次 run 在该日格内按创建时间自上而下排列（上早下晚）。超过 10 次时该日格在层内滚动，底部显示当天总次数。
- 悬停或键盘聚焦显示工作流、事件（定时 / 手动）、PR 号、结论、耗时、尝试次数、时间、分支或标签与标题；点击打开对应 GitHub run。
- 每格直接写出该 run 的耗时（如 `45s`、`18m`、`1h05`），颜色仍表示结论；总览的近 14 天图同样显示。耗时是该 run 最后一次尝试从开始到结束的时间，运行中的 run 没有耗时。失败格的斜纹较淡，保证文字可读。
- 层标签给出窗口内成功、失败、取消、运行中计数、成功率和中位耗时（只统计成功与失败的 run，取消的不计入）。成功率 = 成功 ÷（成功 + 失败/超时），取消、运行中与其他不计入。没有 run 的层显示“窗口内没有 run”，不计为失败。
- 窄屏时层标签固定在左侧，日期轴在卡片内横向滚动，默认停在最新一天；读者滚走后保持位置。
- 历史尚未回填完成，或兼容模式只读取一页 run 时，最早已采集日期之前的列显示斜纹“未采集”，不代表没有运行。

## 主要实现

- `gsb/ci_lanes.py`：`lane_of` 分层，`pr_number` 取 PR 号，`duration` 取每次 run 的耗时（记录中的 `duration_s`，否则为开始到最后更新的时间），`build_lanes` 生成 `sections.ci.data.lanes`，每格带 `duration_s`，每层摘要带 `median_duration_s`。`days` 是共同日期轴，每层 `days[i]` 与之对齐，格内已按时间排序。前端 `static/app.js` 的 `ciLanes` 只负责渲染。
- PR 号优先使用 run 的 `pull_requests`。fork PR 的该字段为空，改为按 head 分支匹配当时开放的 PR，再用 head 仓库、SHA、标题排除歧义；仍不唯一时不显示号码。推断出的号码在提示中标“按分支推断”。
- 增量模式（`incremental_project.build_snapshot`）只为窗口内的 run 读取完整记录，并只读取窗口内开放过的 PR。兼容模式（`project.build_project`）使用本次读取的 run 页。
- run 与 PR 记录新增 `head_repo`，历史搜索索引新增 `event`。旧索引行缺少 `event` 时，快照从对应记录重建该行一次，不改记录、不删除历史；新字段随增量采集与每周对账补齐。既无触发事件又无关联 PR 的门禁 run 不画点。
- 已发布快照没有 `lanes`，或任一分支线不是上述五层顺序时，下一次构建从已有记录重建快照；不改变 Actions 查询，也不重新下载产物。

## 边界

- 只展示已采集的 Actions run，不触发、不执行测试，也不修改源仓工作流。
- 窗口天数与时区是代码常量（`WINDOW_DAYS`、`DISPLAY_ZONE`）。
- 位置按 run 创建时间；重跑后颜色更新，但不会移到重跑当天。
- 重跑完成通知随现有刷新节奏合并处理，每轮最多按 ID 补查 20 条最新 attempt 与必要报告，不为此扫描全部 Actions 历史或单独触发采集；发布成功后确认，失败保留到下次刷新。旧通知未送达时仍由原有对账兜底。
- 构建报告页的三张摘要卡与本图独立。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_ci_lanes.py' -v
node .e2e/node_modules/playwright/cli.js test --config test/playwright.config.cjs -g "CI trends"
```

单元测试覆盖五层分类与隐藏类别（合并队列优先、PR、分支 push、Nightly 定时与手动、Release tag，以及 `workflow_call`、其他工作流、无事件无关联 PR 不画点）、run ID 去重、同一天多次 run 的纵向顺序、各层日期对齐、PR 号推断以及旧索引缺字段降级。浏览器用例检查五层与 30 列对齐、同日纵向顺序、合并队列提示和链接、空队列、窄屏滚动与标签固定。
