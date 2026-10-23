# AGENT.md — ci-gates + 单仓 Dashboard 自动化

> **读者**：用户自己的 AI coding agent。
> **目标**：只依赖仓库内容和本文件，完成三件事：
> 1. 在源仓接入 ci-gates 门禁（只上 CI 也可以）；
> 2. 用 `github-status-board` 模板建单仓 Dashboard 并发布；
> 3. 按真实代码补充用例，让看板显示 ready/todo/skipped 数量。
>
> 本文件不依赖聊天历史。每一步都必须以实际仓库内容为准，不能假设语言、包管理器、分支名或测试框架。

---

## 0. 硬性规则

1. **先读再改**：先检查仓库结构、已有 CI、默认分支、构建/测试命令；不要直接覆盖 `.github/workflows/ci.yml`。
2. **不伪造证据**：没有跑过的命令、没有看到的 URL、没有成功的 run，不得写成“已验证”。
3. **不伪造用例**：`ready` 用例必须对应仓库里真实存在的测试/检查文件；不要为了凑数创建假测试或假 marker。
4. **不自动公开仓库**：把 private 改成 public 必须得到用户明确同意。公开 Pages 会暴露源码和数据。
5. **不提交凭据**：token、PAT、密码、私钥不得写入仓库、日志、提交信息。AI 不应要求用户在聊天里粘贴 token。
6. **不覆盖用户 CI**：已有 CI 时，合并一个 `gate` job；没有 CI 时才复制模板 workflow。
7. **保持幂等**：重复执行时不要重复加 job、重复加 lane、重复创建文件；先 `git diff` 检查。
8. **失败就停**：权限不足、Pages 未开启、命令不确定、用户未确认公开范围时，报告阻塞点，不要猜。

---

## 1. 两个仓库和三个产物

```text
源仓（用户代码）                    看板仓（Dashboard）
  .github/workflows/ci.yml            .github/workflows/setup.yml
  .github/ci-gates.json               .github/workflows/collect-selfhosted.yml
  scripts/gate/gate.mjs               .github/workflows/pages.yml
  test/cases/case-catalog.yaml        board-config.json
          │                                   ▲
          │ 上传 ci-gates-reports             │ 采集并发布
          └──────────────► Collect dashboard data (PAT) ──► Pages
```

模板地址：

- 单仓看板模板：`https://github.com/haowenz001/github-status-board`
- 源仓模板目录：`https://github.com/haowenz001/github-status-board/tree/main/source-template`
- 当前示例看板：`https://haowenz001.github.io/github-status-board/`

关键点：

- 源仓 workflow 的 **name 必须是 `CI`**（或让看板 `workflows.gate` 匹配它）。
- 门禁报告 artifact 名必须是 **`ci-gates-reports`**。
- 看板仓和源仓**不能是同一个仓库**；`setup.py` 会拒绝。
- 公开看板只能采集 **public 源仓**；private 源仓只能本地看板。

---

## 2. 先做决策，不要直接复制

按顺序判断：

### 2.1 源仓是 public 还是 private？

- **public**：可以走公开 Pages 全自动链路。
- **private**：
  - 如果用户接受改成 public：先让用户明确确认，再改 visibility。
  - 如果必须保持 private：不要接公开 Pages，改用本地 `publish.py` + `server.py`。
  - 不要在 private 源仓上执行 `Setup dashboard` 后声称成功；采集会拒绝发布。

### 2.2 已有 CI 吗？

检查：

```bash
ls -la .github/workflows 2>/dev/null || true
```

- **没有 CI**：复制 `source-template` 的 `ci.yml`。
- **已有 CI**：不要覆盖。把 `gate` job 合并进现有 workflow；保留原有 job 和触发条件。
- **已有同名 `CI` workflow**：合并 job，不要创建两个都叫 `CI` 的 workflow。

### 2.3 现在填不填用例？

- **先只上 CI**：用空 catalog，并从 `pr-ready` 移除 `verify-cases`。
- **已有真实测试**：保留 `verify-cases`，按第 5 节登记 ready 用例。
- **不确定**：先空 catalog；不要生成假用例。

---

## 3. Phase A — 在源仓接入门禁

### A0. 发现项目事实

执行并记录：

```bash
pwd
git status --short
git branch --show-current
git remote -v
ls -la
find . -maxdepth 3 -type f \( -name 'package.json' -o -name 'pyproject.toml' -o -name 'go.mod' -o -name 'Cargo.toml' -o -name 'Makefile' \) -not -path './node_modules/*'
ls -la .github/workflows 2>/dev/null || true
```

确认：

- 默认分支：通常是 `main`，以 `git remote show origin` 或仓库设置为准。
- 构建命令：从 `package.json` / `pyproject.toml` / `Makefile` 读，不要猜。
- lint 命令、test 命令是否存在。
- 是否已有 `.github/workflows/ci.yml`。

### A1. 复制最小文件

没有现成门禁时，复制四个文件：

```bash
git clone --depth 1 https://github.com/haowenz001/github-status-board.git /tmp/gsb-template

mkdir -p .github/workflows scripts/gate test/cases

cp /tmp/gsb-template/source-template/.github/workflows/ci.yml .github/workflows/
cp /tmp/gsb-template/source-template/.github/ci-gates.json .github/
cp /tmp/gsb-template/source-template/scripts/gate/gate.mjs scripts/gate/
```

第四个文件在第 A2 步创建。

如果用户已有 CI，只复制：

```text
scripts/gate/gate.mjs
test/cases/case-catalog.yaml
```

并把 `gate` job 合并进现有 workflow，保留原有触发器和 job。

### A2. 写 `.github/ci-gates.json`

先只上 CI、不填用例时，使用空 catalog，并去掉 `verify-cases`：

```json
{
  "version": 1,
  "level": "C1",
  "preset": "auto",
  "caseCatalog": "test/cases/case-catalog.yaml",
  "caseRoot": ["test", "tests", "src"],
  "lanes": {
    "build": {
      "commands": ["npm run build"]
    },
    "pr-ready": {
      "commands": ["npm run lint"]
    },
    "pr-merge": {
      "commands": ["npm run build"]
    },
    "daily": {
      "enforcement": "observe",
      "continueOnError": true,
      "commands": ["npm test", "npm run build"]
    },
    "release": {
      "commands": ["npm run build"]
    }
  }
}
```

规则：

- `commands` 必须换成项目真实命令。Python 项目可能是 `pytest`、`ruff check .`；Go 项目可能是 `go test ./...`、`go build ./...`。
- 没有 `npm test` 就删掉 `daily` 里的这一条，或改成真实命令。
- `daily` 默认是 `observe` + `continueOnError`，失败不阻断。
- 不要为了“看起来完整”保留不存在的脚本。

空 catalog：

```yaml
schema: ci-gates/case-catalog@1
version: 1
cases: []
```

`caseCatalog` 路径不要删；gate runner 启动时仍会读取这个文件。

### A3. 事件到 lane 的映射

`ci.yml` 只有一个 job `gate`，lane 由 `gate.mjs` 按事件选择：

| 事件 | 实际 lane |
|---|---|
| `pull_request` | `build` + `pr-ready` |
| `merge_group` | `build` + `pr-merge` |
| `push` 到 main | `build` |
| `schedule` | `daily` |
| `release` published | `release` |
| `workflow_dispatch` | 默认 `build` + `pr-ready` |

不要在 PR 上声称 `pr-merge`/`daily`/`release` 已经跑过。  
需要手动跑单条 lane 时：

```bash
CI_GATES_CLI="$PWD/scripts/gate/gate.mjs" \
  node scripts/gate/gate.mjs run --lane daily
```

### A4. 本地验证

```bash
CI_GATES_CLI="$PWD/scripts/gate/gate.mjs" \
  node scripts/gate/gate.mjs run --event pull_request --out gate-reports
```

期望：

```text
build: pass
pr-ready: pass
```

生成的报告：

```text
gate-reports/build.json
gate-reports/pr-ready.json
```

检查 JSON：

```bash
python3 - <<'PY'
import json
for lane in ('build', 'pr-ready'):
    d = json.load(open(f'gate-reports/{lane}.json'))
    print(lane, d['verdict'], d.get('cases'))
PY
```

如果本地失败，先修命令，不要提交 workflow。

### A5. 提交、PR、required check

```bash
git checkout -b add-ci-gates
git add .github scripts/gate test/cases
git commit -m "ci: add ci-gates"
git push -u origin add-ci-gates
```

开 PR 后确认：

- Actions 里有 `CI`，job 是 `CI / gate`；
- artifact `ci-gates-reports` 存在；
- PR 的 lane 是 `build` + `pr-ready`。

可选：在仓库 **Settings → Rules / Branches** 把 `CI / gate` 设为 required check。  
如果套餐不支持 private 仓 branch protection，跳过，不影响门禁运行。

---

## 4. Phase B — 建单仓 Dashboard

### B0. 前置检查

- 源仓必须是 **public**。
- 看板仓必须从模板新建，且是 **public**。
- 源仓和看板仓不能是同一个仓库。
- 看板仓 Pages 必须启用为 **GitHub Actions**。

如果源仓是 private 且用户不同意公开：停止，转 B4 本地模式。

### B1. 创建看板仓

1. 打开 `https://github.com/haowenz001/github-status-board`
2. **Use this template → Create a new repository**
3. 名称例如 `my-service-board`，可见性 **Public**
4. 创建后进入 **Settings → Pages**
5. **Build and deployment → Source: GitHub Actions**

> 这一步通常需要用户手动点一次；不要假设 `GITHUB_TOKEN` 能替用户开启 Pages。

### B2. 写看板配置

看板仓 → **Actions → Setup dashboard → Run workflow**：

`source_repository`：

```text
<owner>/<source-repo>
```

`config_json`：

```json
{
  "label": "my-service",
  "workflows": {
    "gate": ["^CI$"],
    "daily": ["nightly", "daily"],
    "release": ["release", "version"]
  },
  "branch_lines": [
    { "key": "main", "ref": "main" }
  ],
  "review_sla_days": 3,
  "stale_days": 30
}
```

`reset`：

- 第一次配置：勾选。
- 切换源仓：勾选。
- 仅刷新数据：不要勾选。

`Setup dashboard` 会：

1. 写入 `board-config.json`；
2. 提交到看板仓；
3. 触发 `Collect dashboard data (PAT)`。

### B3. 等待并验证

自动链路：

```text
Setup dashboard
  → Collect dashboard data (PAT)
  → Deploy dashboard Pages
```

验证：

```bash
curl -fsS "https://<owner>.github.io/<board-repo>/data/snapshot.json" | python3 -c "
import json,sys
d=json.load(sys.stdin)
ci=d['sections']['ci']['data']
print('repo:', d['repository']['name'])
print('generated:', d['generated_at'])
print('gates:', ci.get('gates', {}).get('run', {}).get('id'))
print('lanes:', [(x['lane'], x['verdict']) for x in ci.get('gates', {}).get('lanes', [])])
"
```

打开：

```text
https://<owner>.github.io/<board-repo>/#ci
```

CI 页应出现「质量门禁」卡片，显示最新 run 的 lane 和用例数。

### B4. private 源仓：本地模式

不要发布到公开 Pages：

```bash
git clone https://github.com/haowenz001/github-status-board.git /tmp/gsb-board
cd /tmp/gsb-board

GSB_ALLOW_PRIVATE=1 \
GITHUB_TOKEN=<read-only-token> \
python3 publish.py --repo <owner>/<private-repo>

python3 server.py
```

打开 `http://127.0.0.1:8790`。

`publish.py --publish-repo` 会拒绝 private 源仓；不要绕过。

---

## 5. Phase C — 添加用例

### C1. 三种状态

| 状态 | 含义 | 要求 |
|---|---|---|
| `ready` | 可验证 | `file` + `owner` + 文件内 `@case <id>` |
| `todo` | 计划补 | 不要求 `file`/`owner`；不要加 marker |
| `skipped` | 暂时跳过 | `until` + `reason` |

### C2. ready 用例

`test/cases/case-catalog.yaml`：

```yaml
schema: ci-gates/case-catalog@1
version: 1

cases:
  - id: build.app
    lane: build
    layer: L0
    title: 应用可以构建
    status: ready
    file: src/app.js
    owner: '@your-handle'
    source: manual
```

文件 `src/app.js` 中：

```js
// @case build.app
export function greet(name) {
  return `Hello, ${name}!`;
}
```

注意：

- marker 必须写在 `file` 指向的文件中。
- marker 扫描范围由 `caseRoot` 决定，默认 `test`、`tests`、`src`。
- `id` 必须全局唯一；改 id 会被看板视为新用例。
- `owner` 必须是真实负责人 handle，不要写 `@your-team` 占位符。

### C3. todo 用例

```yaml
  - id: pr-ready.unit
    lane: pr-ready
    layer: L0
    title: 补充单元测试
    status: todo
    file: test/app.test.mjs
    source: manual
```

不要给它加 `@case` marker；如果加了，`verify-cases` 会警告“todo 已有 marker”。

### C4. skipped 用例

```yaml
  - id: daily.e2e
    lane: daily
    layer: L2
    title: 真实环境 E2E
    status: skipped
    until: '2026-12-31'
    reason: '等待测试环境'
    source: manual
```

### C5. 加回用例校验

`.github/ci-gates.json` 的 `pr-ready`：

```json
"pr-ready": {
  "commands": [
    "npm run lint",
    "node \"$CI_GATES_CLI\" verify-cases"
  ]
}
```

本地验证：

```bash
node scripts/gate/gate.mjs verify-cases
```

期望：

```text
[gate] cases: 1 total, 1 ready, 0 todo, 0 skipped
```

如果失败，优先检查：

- `ready` 缺 `file` / `owner`；
- marker 在别的文件；
- marker 没有登记；
- 文件不在 `caseRoot`；
- YAML 使用了 tab 或嵌套结构（本 runner 只支持扁平映射）。

### C6. 看板更新

提交并合并后：

1. `CI` 跑出新的 `ci-gates-reports`；
2. 看板下次 `Collect dashboard data (PAT)` 采集；
3. 质量门禁卡片的 `total/ready/todo/skipped` 更新。

看板不需要改 `board-config.json`。

---

## 6. 验收清单

### 门禁

- [ ] workflow name 是 `CI`（或已配置匹配）
- [ ] `CI / gate` 在 PR 上运行
- [ ] artifact `ci-gates-reports` 存在
- [ ] `build.json` / `pr-ready.json` 的 `verdict` 符合预期
- [ ] 本地 `node scripts/gate/gate.mjs verify-cases` 通过（如果启用）
- [ ] 没有未登记的 `@case` marker

### Dashboard

- [ ] 看板仓从模板创建，且是 public
- [ ] Pages Source = GitHub Actions
- [ ] `Setup dashboard` success
- [ ] `Collect dashboard data (PAT)` success
- [ ] `Deploy dashboard Pages` success
- [ ] `snapshot.json` 的 `sections.ci.data.gates.run` 指向源仓 run
- [ ] CI 页「质量门禁」显示 lane 和用例数

### 证据格式

最终汇报必须包含：

```text
改动的文件：
验证命令与输出：
CI run URL：
artifact：
Collect run URL：
Pages URL：
仍需要人工完成的步骤：
```

没有证据的项写“未验证”，不要写“应该可以”。

---

## 7. 故障排查

| 现象 | 常见原因 | 处理 |
|---|---|---|
| PR 没有 `CI` | workflow 不在默认分支 / 路径错 / YAML 语法错 | 检查 `.github/workflows/ci.yml`，看 Actions 是否解析 |
| `CI` 失败 | 构建/lint 命令错 | 先本地跑真实命令，再改 `ci-gates.json` |
| 没有 `ci-gates-reports` | upload 步骤被删 / 路径错 / 权限不足 | 检查 artifact 名和 `if: always()` |
| 看板没有门禁卡片 | 没有采集 / artifact 过期 / 最新 run 没有报告 | 手动跑 `Collect dashboard data (PAT)`，检查 run |
| 卡片只有 `build` | 最新 run 是 push；PR 才跑 `pr-ready` | 打开 PR 的 run，或手动采集 PR run |
| `case catalog not found` | 路径错 / 文件未提交 | 检查 `caseCatalog` 和文件 |
| `ready case ... needs file and owner` | catalog 字段缺失 | 补 `file`、`owner` |
| `no @case marker in ...` | marker 不在 `file` 指定文件 | 加到正确文件，或改 `file` |
| `unknown @case marker ...` | 代码有 marker，catalog 没登记 | 登记或删除 marker |
| `todo case ... already has a marker` | todo 不应有 marker | 删除 marker，或改成 ready |
| Setup 失败 | 源仓 private / 源仓=看板仓 / JSON 非法 | 看 setup 日志；公开源仓或改本地模式 |
| Pages 404 | Pages 没开 / deploy 失败 | Settings → Pages 选 GitHub Actions；看 deploy 日志 |
| 看板不更新 | schedule 未到 / collect 失败 / 源仓无新数据 | 手动 dispatch collect；看 collect 日志 |

---

## 8. 权限与安全

- GitHub Actions 内优先使用 `GITHUB_TOKEN`。
- 公开源仓不需要额外 PAT。
- private 源仓本地模式才需要用户自己的只读 token；不要把它写进仓库。
- 不要把 token 放进 `board-config.json`、workflow、issue、PR 描述。
- 如果用户把 token 贴进聊天，提醒其立即撤销/轮换；AI 不应回显 token。
- 不要把 private 源仓数据发布到公开 Pages；这是数据泄露，不是配置问题。

---

## 9. AI 的最终输出模板

```markdown
## 已完成
- ...

## 改动文件
- ...

## 验证证据
- 本地命令：...
- CI run：...
- artifact：...
- Collect：...
- Pages：...

## 仍需人工
- 开启 Pages / 确认公开范围 / 配置 required check

## 未验证
- ...
```

---

## 10. 参考

- 单仓模板：`https://github.com/haowenz001/github-status-board`
- 源仓模板：`https://github.com/haowenz001/github-status-board/tree/main/source-template`
- 模板 README：`https://github.com/haowenz001/github-status-board/blob/main/README.md`
- 示例看板：`https://haowenz001.github.io/github-status-board/`
- 当前示例的 canonical `CI` run：`https://github.com/haowenz001/ci-gates-smoke-20261022/actions/workflows/ci.yml`
