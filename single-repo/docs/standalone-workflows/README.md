# 独立仓部署工作流（历史参考）

这些工作流来自单仓看板被复制到独立看板仓的旧模式：

- `setup.yml`：在独立看板仓里写 `board-config.json`；
- `collect-selfhosted.yml`：采集源仓并提交 `site/`；
- `pages.yml`：发布独立看板仓的 GitHub Pages；
- `checks.yml`：独立仓的采集器测试。

**当前不再推荐这条路径。** 公开源仓请使用本仓根目录的 `board/*` 分支托管：

- `.github/workflows/board-create.yml` 创建分支；
- `.github/workflows/board-collect.yml` 采集并写回分支；
- `.github/workflows/deploy.yml` 汇总发布到 `/boards/<owner>/<repo>/`。

私有源仓只在本机运行，不发布到公开 Pages。保留这些文件仅用于追溯旧部署方式，不要把它们复制到新仓。
