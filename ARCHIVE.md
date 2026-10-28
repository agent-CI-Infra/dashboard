# DigitalMate board code archive

This branch preserves the standalone DigitalMate board code from
`haowenz001/digitalmate-board` inside the dashboard repository.

- It is **not** a hosted `/boards/` board and is ignored by `board-collect.yml`
  and `deploy.yml`.
- `site/data/` is intentionally empty: the private-source snapshot is not copied
  into this public repository.
- `.github/workflows/` is intentionally omitted so this branch cannot trigger the
  old standalone Pages deployment and overwrite the public dashboard site.
- The source repository `openJiuwen-ai/DigitalMate` is private. For public hosting,
  first make it public, then run **Create board** with branch convention
  `board/openJiuwen-ai/digitalmate`.
