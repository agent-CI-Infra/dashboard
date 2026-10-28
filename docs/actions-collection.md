# Actions 看板采集

## 功能

`.github/workflows/collect.yml` 将已有 Python 采集器放到 GitHub Actions 运行。Node／Compose 或 Cloudflare Worker Bot 只接收／记录 Webhook、合并更新并触发该工作流；采集后的 App 提交继续触发 `pages.yml` 发布 GitHub Pages。现有本机 `publish.py` 用法保持兼容。

正式仓和测试仓使用相同工作流及脚本，保留各自的站点快照与同步进度。每个仓的 `board-config.json` 只保存自己的单一源仓映射；同步共享代码时不能复制另一环境的部署映射。`collection_context.py` 从本仓 `board-config.json` 按当前 `GITHUB_REPOSITORY` 反查唯一源仓。正式对应 `openJiuwen-ai/sciencediscovery`，测试对应 `ScienceDiscovery/sciencediscovery`。未知目标仓、跨站源仓参数和非法请求编号会在申请凭据前被拒绝。

## 配置和运行

正式与测试分别配置 Actions Variables：`SDBOT_TOKEN_BROKER_URL` 为对应 Worker 的 HTTPS `/actions/token` 地址，`SDBOT_TOKEN_AUDIENCE` 与该 Worker 的 OIDC audience 一致。采集 job 使用 `id-token: write`，通过 `collect_with_oidc.py` 领取临时凭据，不再使用仓库 App ID 或私钥 Secret。

Worker 校验 GitHub OIDC 签名、issuer、audience、不可变仓库与组织 ID、main 分支、collect.yml 和 schedule／workflow_dispatch 事件，固定映射决定令牌的仓库和权限。源仓令牌仅有 Contents／Issues／Pull requests／Actions／Checks／Commit statuses 读取权限，目标仓仅有 Contents 写权限；Metadata 均只读。App 私钥保存在对应 Worker，不能把正式私钥交给测试实例。

客户端领取令牌后先遮蔽日志，只把两个安装令牌传给采集子进程；不会传递 OIDC 请求凭据或私钥。结束时尝试撤销两令牌，部分兑换失败也撤销已领取的令牌；强制终止或撤销失败时由令牌有效期兜底。没有长期 Secret 或个人令牌回退。

同一个 run／attempt 对每种用途仅能兑换一次，Worker 持久保存非秘密签发记录。网络失败或响应丢失后使用 GitHub Re-run jobs 或新的运行恢复。先部署支持兑换的 Worker，再更新本仓工作流；验证成功后删除旧 `SDBOT_GITHUB_APP_PRIVATE_KEY` 仓库 Secret。不要撤销 Worker 正在使用的 App 私钥本身。

App 的 Actions 写权限在 App 注册页 **Permissions & events → Repository permissions → Actions → Read and write** 设置，并由目标组织在安装页批准更新；仅修改注册页不等于既有 installation 已获授权。即使两个看板仓同属一个组织，也要分别批准正式与测试 App 各自的 installation；测试 App 的安装范围须覆盖实验源仓及测试看板仓，也可以包含其他仓库。两套 App 的安装范围可以重叠；各 Bot 通过源仓过滤、看板目标映射和 OIDC 信任配置隔离业务，范围外 Webhook 仍归档，不触发业务。临时令牌只授予当前配置中的源仓读取或目标看板写入权限，不随 App 安装范围扩大。仓库 Settings → Actions 的默认 `GITHUB_TOKEN` 权限继续保持只读；触发所需的 App Actions 写权限与此设置不同。

工作流接受 `workflow_dispatch`。Bot 只在 Issue、PR 新建、重新打开或关闭时立即触发，其余变化（CI 运行、评审、发布等）由 Bot 每 8 小时一次的空闲刷新带上；看板仓自己每天 UTC 02:17 定时运行一次，只作 Bot 停止触发时的兜底；固定 checkout main，禁止 checkout 输入指定的任意分支。输入 `source_repository` 可省略，填写时必须匹配本站源仓；`request_id` 为诊断用刷新编号，不包含原始 Webhook、私钥或安装令牌。可在 Actions 的 **Collect dashboard data** 手动执行，也可以由 Bot 调用 GitHub workflow dispatch API。

`collect-on-push.yml` 在 `main` 收到包含 `site/`、`.sync/` 以外文件改动的推送时，自动派发 `collect.yml` 的 `workflow_dispatch`，固定 `ref=main`，`request_id=push-<完整提交 SHA>`，不传 `source_repository`。它使用本仓 `GITHUB_TOKEN`，仅声明 `contents: read` 和 `actions: write`；不在 PR 上运行，也不取消正在执行的采集。`paths-ignore` 排除 `site/**` 与 `.sync/**`，因此只修改这两个目录的采集提交不会再次派发；源码与数据混合的推送仍会触发。定时、手动及 Bot 触发保持不变，Pages 仍由 `site/` 的提交触发。

不要直接给 `collect.yml` 增加 `push`：Worker 的 OIDC 校验只接受该工作流的 `schedule` 和 `workflow_dispatch`，直接由 `push` 启动会在领取令牌时被拒绝。派发工作流只负责调度，不领取采集凭据；采集串行和等待队列仍由 `collect-${{ github.repository }}` 并发组管理。

兑换令牌后，客户端用同一 OIDC 身份向同一 Worker 的 `/actions/gitcode-sync` 读取 GitCode 同步记录，经 `GSB_GITCODE_SYNC_FILE` 交给发布器；读取失败只把页面标为过期，不影响采集。见 [GitCode 同步记录](gitcode-sync.md)。

客户端还向同一 Worker `POST /actions/reruns`（正文 `{}`）领取最多 20 条完成重跑通知，经非秘密的 `GSB_RUN_CHANGES_FILE` 交给采集器。Worker 仅合并 run ID／attempt／revision，不为每个完成事件调度采集；仍随既有 Issue／PR 合并刷新、8 小时空闲刷新、每日兜底或手动采集处理。发布器按 ID 优先读取最新 attempt、状态与必要报告，不回扫全部 Actions 历史。成功原子提交后才生成 `GSB_RUN_CHANGES_RECEIPT`，父进程重新领取短期 OIDC 身份，向 `/actions/reruns/ack` 确认成功项；失败项和确认丢失均保留，旧确认不删除后来的新 attempt。两个接口沿用固定看板仓 ID／所有者 ID、`main`、`collect.yml` 和 `schedule`／`workflow_dispatch` 的校验，不新增信任或安装令牌权限。建议先发布 Bot 接口再更新采集器；旧 Bot 不提供接口时给出警告并继续普通采集。请求、下载和时间预算保持不变。见 [增量同步与历史](incremental-history.md)。

采集步骤从环境变量读取源仓 `GITHUB_TOKEN` 和目标仓 `GSB_PUBLISH_TOKEN`，运行 `publish.py --repo ... --publish-repo ... --output .tmp/collected-site --incremental`。临时目录不上传，不写回原始测试 artifact。发布器基于本轮 checkout 的准确 HEAD，将 `.sync/` 进度及变化的 `site/` 分片放入一个 Git 提交。保留源码和工作流；并发冲突直接失败，下轮从新 main 续跑。只有 site 变化才触发 Pages。详情见 [增量同步](incremental-history.md)。

E2E 执行记录的 HTML 报告不进入提交：采集成功后，`collect.yml` 把新读取的报告上传为本次运行的 `e2e-html` 产物，保留到对应源产物的 `expires_at`；`pages.yml` 以 `actions: read` 读取这些产物并在部署前挂载，取不到时页面改为 GitHub 产物链接。见 [E2E 执行记录](e2e-run-records.md)。

## 完成状态和重试

Bot 的“已触发采集”只表示 GitHub 接受调度。实际采集结果看 **Collect dashboard data**，Pages 结果看 **Deploy dashboard Pages**。失败保留上一版页面，支持手动重跑或下一轮刷新。

工作流按目标仓串行，`cancel-in-progress=false`，不会中断运行中的采集。GitHub 可能合并／替换尚未开始的同组等待任务，快照按执行时最新仓库状态采集。dispatch 在网络异常时可能重复执行，刷新编号不作为 GitHub 的去重保证。

写 site 使用 App 安装令牌，不能替换为 Actions 默认 GITHUB_TOKEN 后仍期待 push 自动触发另一个工作流。`pages.yml` 自身的上传和部署继续使用其默认 GITHUB_TOKEN 即可。

## 本地验证与同步

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 test/journey-rerun-collection.py
```

测试覆盖正式／测试映射、拒绝跨站和恶意 inputs、读写凭据隔离、公开内容边界、非强制提交和测试报告统计。Bot 仓 `npm run test:worker-adapter` 覆盖 Worker 到模拟 Actions API 的签名凭据与触发链路。两者不代表真实 GitHub runner 或 Pages 已执行，上线前仍需验证。

将此功能同步到测试仓前，先比较目标 main 与共同源码基线；目标已有的独立功能须三方合并并在该目标源码上验证。只同步本次明确的源码、工作流、测试与文档，保留目标仓自己的 `.sync/` 和 `site/`，不要整条分支覆盖。切换前停止同站点的本机自动采集，避免两种调度同时写入。

参考：[GitHub OIDC](https://docs.github.com/en/actions/reference/security/oidc)、[工作流触发 API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)。
