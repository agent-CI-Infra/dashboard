# Community Ecosystem Monitor

开源社区运营与健康度监控平台，动态展示社区规模、活跃度、健康度与影响力。

**在线访问：** https://nanzhaogang.github.io/community-ecosystem/

当前监控仓库（共 15 个）：

- `openJiuwen-ai/sciencediscovery`
- ThinkFlowLab：`system1-agents`、`vllm-rlt`、`system1-omni`、`nanodot`、`vllm-omni-mlx`、`OmniRSI`、`nanoMuse`、`mobilegym`、`OpenMinis`、`orbit_tests`、`pi`、`mlx-perf`、`agentic-video-generation`、`agent-core`

## 功能页面

| 路由 | 页面 | 说明 |
| --- | --- | --- |
| `/` | 社区概览 | 以仓库为颗粒度展示核心指标（Stars / Forks / 贡献者 / PR / Issue）、PR 与 Issue 概览、社区运营预警；右上角可一键更新所有仓库的最新数据 |
| `/pulls` | PR 分析 | PR 累计数量曲线（近 7 天 / 30 天 / 全部）与明细表 |
| `/issues` | Issue 分析 | Issue 累计数量曲线（近 7 天 / 30 天 / 全部）与明细表（已排除 PR） |
| `/growth` | 社区成长 | 每社区一行：左侧贡献者增长曲线（全部 / 外部），右侧 Star History |
| `/ecosystem` | 生态影响力 | 暂未开放 |

## 本地运行

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # 生产构建
```

> 需要 Node.js 20.19+（或 22.12+）。
> Windows 上若启动报 `Cannot find native binding`，执行 `npm install @rolldown/binding-win32-x64-msvc@1.2.13 --no-save`。

## 数据采集

```bash
npm run collect
```

采集脚本从 GitHub 获取仓库数据、计算指标，并输出到 `public/data/` 供站点读取。

- 采集列表接口（分页读取仓库信息、贡献者、PR 与 Issue），单仓库约十余次请求。
- 未配置 Token 时使用匿名 API（60 次/小时）；配置 `GITHUB_TOKEN` 后为 1000 次/小时，采集更稳定。

采集输出文件：

| 文件 | 内容 |
| --- | --- |
| `meta.json` | 采集状态、监控启用时间、错误与声明（`notes`） |
| `latest.json` | 当前累计快照（Star / Fork、PR / Issue / 贡献者聚合指标） |
| `history.json` | 每日一条历史点（每日增量活动与累计计数） |
| `pulls.json` / `issues.json` | PR / Issue 明细记录 |
| `contributors.json` | 贡献者记录与分类 |
| `ecosystem.json` | 站外传播数据 |
| `config.json` | 公开的分类规则（不含任何密钥） |

### 内部贡献者清单

内 / 外部判定采用白名单制，每个仓库各自维护自己的清单，统一写在 `config/internal-contributors.json`，以 `owner/name` 为键：

```json
{
  "openJiuwen-ai/sciencediscovery": [
    "hsliuustc0106",
    "wyhohyw"
  ],
  "ThinkFlowLab/agent-core": []
}
```

某个仓库清单中的登录名记为 internal，该仓库其余非 bot 作者记为 external，bot 记为 unknown。新增内部贡献者时，在对应仓库的数组里按行追加即可。

该文件的键同时决定被监控的社区列表（采集脚本以此为准），因此**新增一个社区只需在这里加一项**，再运行一次采集即可。

### 可配置环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MONITOR_REPOS` | `config/internal-contributors.json` 的键 | 逗号分隔的监控仓库 `owner/name` 列表，仅用于临时覆盖默认列表（`MONITOR_REPO` 为单仓库的兼容写法）；平时新增社区只需修改配置文件 |
| `GITHUB_TOKEN` | 空 | GitHub Token，仅通过环境变量注入，不写入任何产物 |
| `MONITOR_BOTS` | 内置常见 bot | 逗号分隔的机器人账号 |
| `MONITOR_LONG_OPEN_DAYS` | `30` | Issue 长期未解决阈值（天） |
| `MONITOR_MAX_LIST_PAGES` | `20` | 列表分页上限 |

## 指标口径

- **PR 状态**：Open / 已合入（`merged_at` 非空）/ 关闭未合入（关闭且未合入）；累计量与周期增量分开统计。
- **Issue**：统计时排除 PR（GitHub 的 PR 也占用 Issue 编号）；区分 Open / Closed 与周期内新增、关闭。
- **滞留时长**：Open 条目的存续时长（创建至今）；Issue 超过阈值即计入「长期未解决」。
- **概览**：社区概览只展示最新现状，不提供时间范围选择（增量统计不使用该数据）。
- **时间**：统一 UTC 存储，历史点按 UTC 天切分。
- **内 / 外部**：白名单制，按仓库区分。`config/internal-contributors.json` 中该仓库列出的登录名记为 internal，其余非 bot 作者记为 external，bot 记为 unknown 且不计入内 / 外部；各页面口径一致。

## License

Licensed under the [MIT License](https://choosealicense.com/licenses/mit/).