# ci-gates MVP 模板

把“门禁自动跑起来”所需的最小文件复制到你的仓库：一个 GitHub Actions 工作流、一个零依赖运行器、一份 lane 配置和一份用例目录。

> 这是 MVP，不是完整 ci-gates。它负责**自动触发 + 自动裁决 + 产出报告 artifact**；分支保护、merge queue、单仓 board、公开看板仍需单独配置。

## 文件清单

复制到用户仓根目录后：

```text
.github/
  workflows/gate.yml        # 一个 job，按事件跑对应 lane
  ci-gates.json             # lane 命令与等级
scripts/gate/
  gate.mjs                  # 运行器（Node 20+，零依赖）
  gate.test.mjs             # 运行器自测（可选）
test/cases/
  case-catalog.yaml         # 用例目录
  example.test.ts           # 示例 @case 标记，可删
```

## 三步接入

### 1. 复制模板

在 dashboard 仓根目录执行：

```bash
mkdir -p .github scripts test
cp -R templates/ci-gates/.github/. .github/
cp -R templates/ci-gates/scripts/gate ./scripts/
cp -R templates/ci-gates/test/. ./test/
```

如果你的项目不是 Node，保留 `gate.mjs` 和 workflow，改掉 `npm ci` / `npm run build` 等命令即可。

### 2. 改 `.github/ci-gates.json`

```json
{
  "version": 1,
  "level": "C1",
  "preset": "auto",
  "caseCatalog": "test/cases/case-catalog.yaml",
  "caseRoot": "test",
  "lanes": {
    "build": { "commands": ["npm run build"] },
    "pr-ready": {
      "commands": ["npm run lint", "node \"$CI_GATES_CLI\" verify-cases"]
    },
    "pr-merge": { "commands": ["npm run build"] },
    "daily": { "commands": ["npm run test"] },
    "release": { "commands": ["npm run build"] }
  }
}
```

- `commands` 顺序执行；默认第一个失败后停止，后面的记为 `not_run`。
- `continueOnError: true` 可让后续命令继续跑。
- `timeoutSec` 可设置单条命令超时，默认 900 秒。
- `enforcement: "observe"` 时失败只记 `observe_fail`，不阻断 job。
- `events` 可覆盖默认事件映射；不写则用下表。

### 3. 提交并开 PR

工作流会自动触发：

| 事件 | 默认 lane |
|---|---|
| `pull_request` | `build`、`pr-ready` |
| `merge_group` | `build`、`pr-merge` |
| `push` 到 main | `build` |
| `schedule` | `daily` |
| `release` | `release` |
| `workflow_dispatch` | `build`、`pr-ready` |

首次运行前，把 `package.json` 里的 `build` / `lint` / `test` 换成你项目真实命令。

## 产物

每次运行会写：

```text
gate-reports/build.json
gate-reports/pr-ready.json
...
```

并上传 artifact `ci-gates-reports`。报告格式是 `ci-gates/gate-report@1`：

- `verdict`：`pass` / `fail` / `not_applicable` / `observe_fail`
- `results`：每条命令的 `status`、`durationMs`、`exitCode` / `reason`
- `cases.catalog`：用例目录计数
- `onboarding.stage`：`gates-running` / `cases-pending` / `observing` / `enforced` / `failing`
- `run`：GitHub run id / attempt / url

## 分支保护

MVP 不自动改仓库设置，需要管理员手动做一次：

1. Settings → Rules → New ruleset（或 Branches → Branch protection）。
2. Require status checks → 选择 **`CI Gates / gate`**。
3. 如果启用 merge queue，勾选 merge queue 的 required check；`merge_group` 事件会自动跑 `build` + `pr-merge`。
4. PR 报告是 provisional；`merge_group` / `push` / `schedule` / `release` 才是可信裁决。

## 用例目录

`test/cases/case-catalog.yaml` 的每条用例：

```yaml
- id: build.login
  lane: build
  layer: L1
  title: 登录成功
  status: ready            # ready | todo | skipped
  file: test/login.test.ts
  owner: '@your-team'
```

规则：

- `ready`：必须有 `file` 和 `owner`，且 `file` 里要有 `@case build.login` 标记。
- `todo`：允许没有实现，不要求标记。
- `skipped`：必须有 `until` 和 `reason`。
- `verify-cases` 会扫描 `caseRoot`（默认 `test`，可写成数组，如 `["test", "src"]`），发现：
  - ready 用例缺标记 → 失败
  - 出现目录里没有的 `@case` → 失败
  - todo/skipped 已经有标记 → 警告

先全部写 `todo`，等测试补上再改成 `ready`，这样门禁不会因为“还没填用例”直接红。

## 本地验证

```bash
node scripts/gate/gate.mjs run --lane build
node scripts/gate/gate.mjs verify-cases
node --test scripts/gate/gate.test.mjs
```

`run` 在失败时退出码为 1；`observe` 模式退出码为 0。

## 当前限制

- 只提供 Node 版 setup；其他技术栈需改 workflow 的安装步骤和 lane 命令。
- 不自动创建 ruleset / merge queue，需要管理员手动开启。
- 不自动生成用例，只校验 `case-catalog.yaml`。
- 不接单仓 board / 公开 dashboard；报告目前是 artifact，后续由 board 采集。
- `pull_request` 报告是 provisional，不能作为公开合规结论。
