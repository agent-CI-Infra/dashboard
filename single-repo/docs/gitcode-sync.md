# GitCode 同步记录页

## 功能

看板的「GitCode 同步」标签页展示 Bot 把 GitHub PR 同步到 GitCode MR 的结果，以及回读 CodeCheck 后写到 GitHub Check 的结论：

- **当前状态**：每个 PR 最近一次同步的 GitHub PR、GitCode MR、head、同步状态（已同步、重试中、同步失败、已推送但历史分叉、已关闭、GitHub 已合并、跳过）、CodeCheck 结论（等待结论、通过、未通过、超时无结论、已取消）和错误摘要。
- **同步记录**：每次同步动作（创建、推送新提交、重新打开、关闭、GitHub 已合并、CodeCheck 回读）一行，含时间、两边链接、状态和说明／错误摘要，可只看失败。

失败不需要翻日志：同步失败、重试中和历史分叉的 PR 计入导航上的红色数字，并在页首显示红色横幅，对应行整行标红。最近一次采集没能从 Bot 读取记录时，页面显示「数据可能已过期」及上次成功读取的时间；从未发布过记录时也有明确提示。Bot 停用同步时，页首「GitCode 同步已停用」横幅逐条列出 Bot 给出的原因：缺少 GitHub App 凭据（`no_github_app`）、缺少 GitHub Webhook secret（`no_webhook_secret`）、未设置 GITCODE_TOKEN（`no_token`）、同步已关闭（`off`）。两项凭据都缺时两条都会列出；缺凭据属于故障，横幅为红色并计入导航数字，`no_token` 与 `off` 是有意关闭，横幅为说明样式。CodeCheck「未通过」是门禁正常给出的结论，不算同步失败。

## 数据来源

1. Bot 的同步队列在 Webhook 之外完成推送、MR 操作和 CodeCheck 回读，保存每个 PR 的状态与有限条数的记录（实现见 Bot 仓 `docs/features/gitcode-sync.md`）。
2. `collect_with_oidc.py` 在兑换完源仓、看板仓令牌后，用同一个 OIDC 身份 `POST` Bot 的 `/actions/gitcode-sync`（由 `SDBOT_TOKEN_BROKER_URL` 推出同主机地址，不需要新变量），写入 `.tmp/gitcode-sync.json`，并通过 `GSB_GITCODE_SYNC_FILE` 交给 `publish.py`。读取失败只写入失败标记，不让整次采集失败。
3. `publish.py` 经 `gsb/gitcode_sync.py` 重建公开数据：只保留白名单字段，停用原因只保留上述四个已知代码（优先读 `reasons`，否则拆分逗号连接的 `reason`），校验动作、状态、SHA、时间格式，链接只接受无凭据的 https（PR 链接限定 github.com），文本先删除令牌、`Authorization`／token 赋值和 `user:pass@` URL 再截断。新记录按 ID 合并进上次发布的记录，最多保留 300 条；读取失败时沿用上次数据并标明过期。
4. 结果写入 `site/data/gitcode-sync.json`，与快照同一提交发布；它在 `publish_batch` 的白名单内，内容含发布令牌时拒绝提交。

本机运行 `publish.py` 且没有设置 `GSB_GITCODE_SYNC_FILE` 时，不改动已发布的同步数据。

## 页面实现

`static/gitcode-sync.js` 独立于 `app.js`，只读取 `data/gitcode-sync.json`，在浏览器可见时重新加载。所有文本经转义输出；链接只在是 https 且不含 `@` 时渲染为链接。窄屏下两张表逐行堆叠，状态和错误摘要不需要横向滚动。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_gitcode_sync tests.test_oidc_collection tests.test_publish -v
node .e2e/node_modules/@playwright/test/cli.js test --config test/playwright.config.cjs test/journey-gitcode-sync.spec.cjs
```

`tests/test_gitcode_sync.py` 覆盖字段白名单、脱敏、过期与停用状态、记录合并和发布白名单；`test_oidc_collection.py` 覆盖同一身份读取与读取失败不影响采集。`test/prepare-site.py` 把一份带失败、分叉、恶意标题和凭据形态错误文本的 Bot 响应经 `public_document()` 写入验收站点；`journey-gitcode-sync.spec.cjs` 检查失败可见、链接正确、页面与数据文件不含凭据、窄屏布局，以及过期、停用、缺失三种状态。
