# 测试报告契约

看板读取现有 CI 证据，不替源仓库执行测试，也不会将缺失证据当成成功。默认按 `release` 事件／版本工作流归为版本验证、`schedule`／daily/nightly 归为每日构建，其余为门禁；可在 `board-config.json` 配置名称正则。

Actions 产物名使用 `ut-results`、`st-results`、`e2e-results`、`real-e2e-results`（分片可追加后缀）。支持：

1. Playwright `results.json` / `report.json`：优先使用最终 outcome，重试不增加用例数；预期失败按框架结果处理。
2. JUnit XML：从 testcase 或最内层 testsuite 计数，避免父子汇总重复。
3. `dashboard-summary.json`：字段 `tests`、`passed`、`failed`、`skipped`、`flaky` 均为非负整数，后四项之和必须等于 tests。
4. 标签化测试框架的 `<层>/tagged/summary.json`：tests 为计划数，passed、skipped 取报告值；计划中既未通过也未跳过的用例（失败或未报告）计为 failed。同目录的 `catalog.json`、`plan.json` 另用于[标签化测试](tagged-tests.md)。
5. `run.log`：兼容现有 CI 的 TAP / unittest / pytest 汇总，作为没有结构化报告时的后备来源。
6. Real E2E 评分：从 `benchmark-metrics.json`、`team-metrics.json`、`evolve-metrics.json` 分别读取 DeepResearchBench、BiomniBench、Research Team 和 PUCT Compression 的原生分数、交付状态与耗时。看板不统一不同评分器的量纲，也不设置质量通过门槛；prompt、原始响应、错误详情和凭据不会进入公开快照。

Real E2E 的简化分数与运行耗时随每次 run / attempt 保存在看板历史记录中。E2E 页按分支线、用例 ID 汇总最近 30 次有记录的运行；同一 run 重跑只采用最新 attempt；每个北京日取一次运行，手动或触发式优先，但只在最新 attempt 有可读 `real-e2e-results` 评分的运行中挑选，仍在运行或产物不可读的运行不会挡掉当天已读到的结果；缺失指标或耗时留空，不当作 0。E2E 页在每个用例下直接画出这些点的全部曲线，不需要打开弹窗。历史只从已采集的产物累积，无法用最新结果推造过去的趋势。

同一产物内只取一种报告格式，优先级为 summary、Playwright、标签化 summary、JUnit、log。分片产物必须互不重叠，避免同时上传同一层的合并报告和分片。用例数按报告中的测试实例（包含浏览器项目）计数，不是测试文件数；重试不重复计数。稳定通过率 = passed / tests；skipped 和 flaky 单列，不计稳定通过。零用例不显示 100%。

覆盖率支持 Actions 产物中的 lcov、Istanbul summary 和 Cobertura；若只能读取 Codecov 公共汇总，会明确标记未关联本次构建。测试文件比值不当作行覆盖率；包级用例只来自完整可定位的结构化报告或日志中的包级计数，原始日志与命令不发布。

报告只关联当前 run、SHA 和 attempt；重跑前的产物不会挪用。过期、下载失败、解析失败和未上传均显示未知数量。每个产物最多下载 80 MiB，下载受整次读取时间预算约束；持续缓慢传输也会中止并标记不可读取，不阻断整站更新。展开内容最多 160 MiB / 3000 个条目，不解压到磁盘。公开历史只保留指标；仍在保留期的 E2E 运行另有[执行记录](e2e-run-records.md)，展示用例步骤与 HTML 报告并随产物过期删除，其余原始详情在 GitHub 查看。

