# DigitalMate board snapshot

This branch preserves the standalone DigitalMate board code from
`haowenz001/digitalmate-board` and supplies the data for the static imported
board at `/boards/openJiuwen-ai/digitalmate/`.

- It is assembled by `config/imported-boards.json` on `main`.
- It is **not** collected by `board-collect.yml`; refresh the snapshot and then
  redeploy the public dashboard.
- `site/data/snapshot.json` is intentionally included: the snapshot comes from
  the private source `openJiuwen-ai/DigitalMate` and is published with the
  repository owner's explicit approval.
- `.github/workflows/` is intentionally omitted so this branch cannot trigger
  the old standalone Pages deployment and overwrite the public dashboard site.
- To switch to daily collection, make the source repository public and create
  the standard `board/openJiuwen-ai/digitalmate` branch instead.
