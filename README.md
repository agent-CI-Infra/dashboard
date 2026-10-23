# Multi-Repo Community Dashboard

> **这是多个代码仓的公共 Dashboard，不是单仓看板。**  
> 它聚合展示多个社区的规模、活跃度、健康度和质量门禁；单个仓库的深度视图由本仓 `single-repo/` 模板提供。

**两个入口：**

| 入口 | 链接 |
| --- | --- |
| 公共多仓 Dashboard | <https://agent-ci-infra.github.io/dashboard/> |
| 单仓 / 私仓 Dashboard 模板 | [`single-repo/README.md`](single-repo/README.md) |
| 单仓看板在线示例 | <https://agent-ci-infra.github.io/dashboard/single-repo/> |

当前监控仓库（共 15 个）：

- `openJiuwen-ai/sciencediscovery`
- ThinkFlowLab：`system1-agents`、`vllm-rlt`、`system1-omni`、`nanodot`、`vllm-omni-mlx`、`OmniRSI`、`nanoMuse`、`mobilegym`、`OpenMinis`、`orbit_tests`、`pi`、`mlx-perf`、`agentic-video-generation`、`agent-core`

---

## 一、它是什么

### 1.1 三个层次

| 层 | 仓库 | 负责 | 不负责 |
| --- | --- | --- | --- |
| CI 层 | [`agent-CI-Infra/CI-workflow`](https://github.com/agent-CI-Infra/CI-workflow) | CI workflow（含门禁）模板、`gate.mjs`、报告/用例 schema、starter、`AGENT.md` | Dashboard UI、站点发布 |
| 单仓层 | **本仓** [`single-repo/`](single-repo/) | 单仓采集、单仓 Dashboard、公开 Pages 或私仓本地模式 | 跨仓聚合、门禁协议定义 |
| 汇总层 | **本仓** `agent-CI-Infra/dashboard` | 多仓聚合、`/gate`、来源登记、公开 Pages、接入入口 | 执行用户 CI、保存私仓数据 |

```text
用户源仓
  └─ CI（CI-workflow）─► ci-gates-reports artifact
                         │
                         ▼
              dashboard/single-repo（单仓）
                 ├─ data/snapshot.json  单仓页面
                 └─ data/summary.json   公开白名单摘要
                         │
                         ▼
              dashboard（本仓，多仓）
                 └─ /gate 跨仓门禁视图
```

### 1.2 当前状态

- 本仓已经有 15 个社区的指标采集和公开 Dashboard。
- 本仓已经支持 `board-summary` 和 `status-board-lanes` 两种门禁来源；`CI-workflow` 直连 artifact 适配器尚未实现。
- CI workflow（含门禁）模板已经迁到 [`agent-CI-Infra/CI-workflow`](https://github.com/agent-CI-Infra/CI-workflow)，本仓不再保留 `templates/ci-gates`。
- 单仓/私仓代码已收进 [`single-repo/`](single-repo/)；它仍是一个可复制到新仓的模板目录，不是独立 GitHub Template 仓库。
- **命名兼容**：仓名已改为 `CI-workflow`；协议标识 `ci-gates/*`、artifact `ci-gates-reports`、配置 `.github/ci-gates.json` 暂保持不变，避免已接入仓失效。

---

## 二、如何接入

接入分三条路径。先确认源仓是 **public** 还是 **private**：公开看板只能接 public 源仓。

### 2.1 只接入社区指标（不接门禁）

适合只想被公共 Dashboard 统计 Stars / PR / Issue / 贡献者的仓库。

1. 在 [`config/internal-contributors.json`](config/internal-contributors.json) 增加仓库键：

   ```json
   {
     "owner/repo": []
   }
   ```

2. 运行采集：

   ```bash
   GITHUB_TOKEN=... npm run collect
   ```

3. 打开 <https://agent-ci-infra.github.io/dashboard/>，确认仓库出现。

新增一个社区只需要改这个配置；内部贡献者名单也在这里维护。

### 2.2 接入门禁 + 单仓 Dashboard

适合需要门禁详情、用例进度、单仓深视图的仓库。

#### 第一步：源仓接入门禁

从 [`CI-workflow`](https://github.com/agent-CI-Infra/CI-workflow) 复制：

```bash
git clone --depth 1 https://github.com/agent-CI-Infra/CI-workflow.git /tmp/ci-workflow
mkdir -p .github scripts/gate test/cases
cp -R /tmp/ci-workflow/template/.github/. .github/
cp /tmp/ci-workflow/scripts/gate/gate.mjs scripts/gate/
cp -R /tmp/ci-workflow/template/test/. ./test/
```

要求：

- workflow name 为 `CI`；
- 上传 artifact `ci-gates-reports`；
- 先不填用例时，用空 catalog 并去掉 `pr-ready` 的 `verify-cases`；
- 填用例时，`ready` 需要 `file` + `owner` + `@case <id>`。

完整操作手册（面向用户 AI）：[`CI-workflow/AGENT.md`](https://github.com/agent-CI-Infra/CI-workflow/blob/main/AGENT.md)。

#### 第二步：建单仓 Dashboard

1. 打开 [`single-repo/`](single-repo/) 目录
2. 把 `single-repo/` 的内容复制到一个新仓的**根目录**（不要保留 `single-repo/` 这层）
3. 新仓命名例如 `owner/repo-board`，可见性 **Public**
4. **Settings → Pages → Source: GitHub Actions**
5. **Actions → Setup dashboard** 填：

   ```json
   {
     "label": "repo",
     "workflows": { "gate": ["^CI$"] },
     "branch_lines": [{ "key": "main", "ref": "main" }],
     "review_sla_days": 3,
     "stale_days": 30
   }
   ```

6. 等待 `Collect dashboard data (PAT)` → `Deploy dashboard Pages`，打开：

   ```text
   https://owner.github.io/repo-board/
   ```

**私仓**：不要接公开 Pages；用本机 `GSB_ALLOW_PRIVATE=1 python3 publish.py --repo owner/private-repo` + `python3 server.py`。

#### 第三步：让公共 Dashboard 收录门禁数据

当前在 [`config/gate-sources.json`](config/gate-sources.json) 增加来源：

```json
{
  "id": "owner/repo",
  "name": "repo",
  "owner": "owner",
  "type": "board-summary",
  "summaryUrl": "https://owner.github.io/repo-board/data/summary.json",
  "boardUrl": "https://owner.github.io/repo-board/",
  "defaultBranch": "main"
}
```

过渡期也可以读取单仓 snapshot：

```json
{
  "id": "owner/repo",
  "type": "status-board-lanes",
  "url": "https://owner.github.io/repo-board/data/snapshot.json"
}
```

然后：

```bash
npm run collect:gate
npm run dev
```

打开 `/#/gate`。

> **当前缺口**：`single-repo/` 还没有生成 `data/summary.json`。在补上 `board-summary@1` 生成器之前，公共 Dashboard 只能用 `status-board-lanes` 读取公开 snapshot；`CI-workflow` 直连 artifact 适配器也尚未实现。

### 2.3 接入申请

- 站内教程：<https://agent-ci-infra.github.io/dashboard/#/gate-onboarding>
- 接入 issue：本仓 `gate-onboarding.yml` 模板
- 目标自动化：issue 提交 repo + summary URL → 校验 public/schema/仓库匹配 → 自动开 PR 修改 `gate-sources.json` → 人工合并 → 采集

不要让用户直接提交任意 gate JSON/任意 URL；公共 Dashboard 只接受受 schema 约束的 summary。

---

## 三、功能页面

| 路由 | 页面 | 说明 |
| --- | --- | --- |
| `/` | 社区概览 | 以仓库为颗粒度展示核心指标（Stars / Forks / 贡献者 / PR / Issue）、PR 与 Issue 概览、社区运营预警；右上角可一键更新所有仓库的最新数据 |
| `/pulls` | PR 分析 | PR 累计数量曲线（近 7 天 / 30 天 / 全部）与明细表 |
| `/issues` | Issue 分析 | Issue 累计数量曲线（近 7 天 / 30 天 / 全部）与明细表（已排除 PR） |
| `/growth` | 社区成长 | 每社区一行：左侧贡献者增长曲线（全部 / 外部），右侧 Star History |
| `/gate` | 质量门禁 | 五条 lane 的通过率与覆盖率、分支切换、C0–C4 等级、接入阶段与用例缺口 |
| `/ecosystem` | 生态影响力 | 暂未开放 |

---

## 四、本地运行

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # 生产构建
```

> 需要 Node.js 20.19+（或 22.12+）。
> Windows 上若启动报 `Cannot find native binding`，执行 `npm install @rolldown/binding-win32-x64-msvc@1.2.13 --no-save`。

门禁本地验证：

```bash
git clone --depth 1 https://github.com/agent-CI-Infra/CI-workflow.git /tmp/ci-workflow
node --test /tmp/ci-workflow/scripts/gate/gate.test.mjs
node /tmp/ci-workflow/scripts/gate/gate.mjs run --lane build --config .github/ci-gates.json
node /tmp/ci-workflow/scripts/gate/gate.mjs verify-cases --config .github/ci-gates.json
```

---

## 五、数据采集

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

---

## 六、指标口径

- **PR 状态**：Open / 已合入（`merged_at` 非空）/ 关闭未合入（关闭且未合入）；累计量与周期增量分开统计。
- **Issue**：统计时排除 PR（GitHub 的 PR 也占用 Issue 编号）；区分 Open / Closed 与周期内新增、关闭。
- **滞留时长**：Open 条目的存续时长（创建至今）；Issue 超过阈值即计入「长期未解决」。
- **概览**：社区概览只展示最新现状，不提供时间范围选择（增量统计不使用该数据）。
- **时间**：统一 UTC 存储，历史点按 UTC 天切分。
- **内 / 外部**：白名单制，按仓库区分。`config/internal-contributors.json` 中该仓库列出的登录名记为 internal，其余非 bot 作者记为 external，bot 记为 unknown 且不计入内 / 外部；各页面口径一致。

---

## 七、安全边界

- 公共 Dashboard 只展示公开数据；私仓不要配置公开 snapshot/summary。
- 私仓 Dashboard 使用本机/私有部署，不发布到公开 GitHub Pages。
- 不把 token、PAT、私钥写入仓库或配置。
- 接入配置只接受受 schema 约束的公开 summary，不接受任意 gate JSON 或任意 URL。

---

## License

Licensed under the [MIT License](https://choosealicense.com/licenses/mit/).
