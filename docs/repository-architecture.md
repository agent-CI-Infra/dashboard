# 仓库分工与接入架构

> 状态：已落地。
> 目标：两个 Dashboard 形态放在一个代码仓，门禁模板独立成仓。

## 1. 三个仓库

| 仓库 | 定位 | 负责 | 不负责 |
| --- | --- | --- | --- |
| [`agent-CI-Infra/ci-gates`](https://github.com/agent-CI-Infra/ci-gates) | 门禁契约与模板 | workflow 模板、`gate.mjs`、报告/用例 schema、starter、`AGENT.md`、版本 tag | Dashboard UI、站点发布 |
| [`agent-CI-Infra/dashboard`](https://github.com/agent-CI-Infra/dashboard) | 两个 Dashboard 的代码仓 | 根目录公共多仓聚合；`single-repo/` 单仓/私仓模板；来源登记；公开 Pages | 执行用户 CI、保存私仓数据、复制门禁实现 |
| `single-repo/`（本仓目录） | 单仓/私仓 Dashboard 模板 | 单仓采集、单仓页面、Setup/Collect/Pages、本地私仓模式 | 跨仓聚合、门禁协议定义 |

原独立仓库 `haowenz001/github-status-board` 的代码已收进本仓 `single-repo/`；它不再作为新的独立模板仓维护。

## 2. 代码布局

```text
agent-CI-Infra/dashboard/
  src/                        公共多仓 Dashboard（React/Vite）
  scripts/                    公共 Dashboard 采集
  config/                     社区列表、门禁来源
  single-repo/                单仓 / 私仓 Dashboard 模板
    .github/workflows/        复制到新仓后使用的 Setup/Collect/Pages
    gsb/                      采集与快照
    site/                     静态页面
    setup.py / publish.py
    README.md
```

```text
agent-CI-Infra/ci-gates/
  scripts/gate/gate.mjs
  scripts/gate/gate.test.mjs
  template/.github/workflows/ci.yml
  template/.github/ci-gates.json
  template/test/cases/
  protocol/*.schema.json
  AGENT.md
```

## 3. 数据流

```text
用户源仓
  └─ CI（ci-gates）─► ci-gates-reports artifact
                         │
                         ▼
              dashboard/single-repo（单仓）
                 ├─ data/snapshot.json  单仓页面
                 └─ data/summary.json   公开白名单摘要（待补）
                         │
                         ▼
              dashboard（根目录，多仓）
                 └─ /gate 跨仓门禁视图
```

公共 Dashboard 不运行用户 CI；单仓看板不定义门禁协议；门禁仓不发布站点。

## 4. 数据契约

- `ci-gates/gate-report@1`：源仓 CI 上传的 `ci-gates-reports/*.json`。
- `ci-gates/case-catalog@1`：`test/cases/case-catalog.yaml`。
- `ci-gates/board-summary@1`：单仓看板发布的公开摘要，供公共 Dashboard 读取。

公开摘要只允许白名单字段：

- 仓库身份、默认分支；
- 生成时间、schema 版本；
- lane 结论/通过率；
- 用例总数、ready/todo/skipped；
- 接入阶段；
- board URL。

不得包含源码、日志、Issue/PR 正文、token、私有测试数据。

**当前缺口**：`single-repo/` 还没有生成 `data/summary.json`。在补上之前，公共 Dashboard 用 `status-board-lanes` 读取公开 snapshot；`ci-gates` 直连 artifact 适配器也尚未实现。

## 5. 公共 Dashboard 接入

### 5.1 只接社区指标

修改 `config/internal-contributors.json`，运行 `npm run collect`。

### 5.2 接门禁 + 单仓看板

1. 源仓接入 `ci-gates`。
2. 从本仓 `single-repo/` 复制到新仓，运行 Setup。
3. 单仓看板发布公开 snapshot（当前）或 summary（目标）。
4. 在 `config/gate-sources.json` 增加：

```jsonc
// 当前可用：公开 snapshot
{
  "id": "owner/repo",
  "name": "repo",
  "owner": "owner",
  "type": "status-board-lanes",
  "url": "https://owner.github.io/repo-board/data/snapshot.json"
}
```

```jsonc
// 目标：白名单摘要
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

5. 运行 `npm run collect:gate`，打开 `/#/gate`。

### 5.3 自动接入目标

1. 用户提交 issue，提供 repo + board + summary URL + 默认分支。
2. workflow 校验：
   - 源仓 public；
   - summary 可访问；
   - schema 为 `ci-gates/board-summary@1`；
   - `repository` 匹配；
   - 字段在白名单内。
3. 自动开 PR 修改 `config/gate-sources.json`，人工合并。
4. 采集后 `/gate` 出现该仓。

不要接受任意 gate JSON 或任意 URL。

## 6. 私仓

- 不进入公共 Dashboard。
- 使用 `single-repo/` 本地模式：

```bash
cd single-repo
GSB_ALLOW_PRIVATE=1 GITHUB_TOKEN=... python3 publish.py --repo owner/private-repo
python3 server.py
```

- 公开 GitHub Pages 不可以发布私仓数据。
- `publish.py --publish-repo` 会拒绝私有源仓。

## 7. 版本与迁移

- `ci-gates` 打 `v1`；Dashboard 的 `.github/workflows/gate.yml` checkout `agent-CI-Infra/ci-gates@v1`。
- 用户仓引用 `v1`，不要跟踪 `main`。
- 破坏性 schema 变更升 `v2`。
- 历史文档中的 `templates/ci-gates` / `source-template` 路径已废弃。

## 8. 验收

- 公共 Dashboard：`npm run build`、`npm run test:collector`、`npm run collect:gate`。
- 单仓模板：`cd single-repo && python3 -m unittest discover -s tests -v`。
- 门禁仓：`node --test scripts/gate/gate.test.mjs`。
- 线上：`/` 是公共多仓；`single-repo/README.md` 是单仓入口；`ci-gates` 是门禁模板入口。
