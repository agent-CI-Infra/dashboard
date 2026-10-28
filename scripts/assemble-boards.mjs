#!/usr/bin/env node
/**
 * Assemble per-repository boards into the public Pages artifact.
 *
 * Each board is stored on an orphan branch:
 *
 *   board/<owner>/<repo>/
 *     board-config.json
 *     site/data/...       snapshot, history, E2E index
 *     .sync/...           incremental state
 *
 * GitHub Pages can only deploy one site per repository, so branches are not
 * independent sites. This script extracts the data from every board branch,
 * adds the current static assets from main, and writes:
 *
 *   dist/boards/<owner>/<repo>/
 *   dist/boards/index.html
 *   dist/boards/boards.json
 */
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const root = process.cwd()
const dist = path.resolve(root, process.env.DIST_DIR ?? 'dist')
const staticDir = path.resolve(root, process.env.BOARD_STATIC_DIR ?? 'single-repo/static')
const remote = process.env.BOARD_REMOTE ?? 'origin'
const refPrefix = 'refs/heads/board/'
const boardsDir = path.join(dist, 'boards')

function git(args, options = {}) {
  return execFileSync('git', args, {
    cwd: root,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    ...options,
  })
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch {
    return null
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function listBoardRefs() {
  const output = git(['ls-remote', '--heads', remote, `${refPrefix}*`])
  return output
    .split('\n')
    .filter(Boolean)
    .map((line) => line.split('\t')[1])
    .filter((ref) => ref?.startsWith(refPrefix))
    .sort()
}

function copyDirectory(source, destination) {
  fs.mkdirSync(destination, { recursive: true })
  for (const name of fs.readdirSync(source)) {
    fs.cpSync(path.join(source, name), path.join(destination, name), {
      recursive: true,
      force: true,
    })
  }
}

function assembleBoard(branch) {
  const key = branch.slice(refPrefix.length)
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(key)) {
    console.warn(`skip ${branch}: expected board/<owner>/<repo>`)
    return null
  }

  git(['fetch', '--no-tags', '--depth=1', remote, branch])
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-board-'))
  const worktree = path.join(temporary, 'worktree')
  try {
    git(['worktree', 'add', '--detach', '--force', worktree, 'FETCH_HEAD'])
    const data = path.join(worktree, 'site', 'data')
    if (!fs.existsSync(path.join(data, 'snapshot.json'))) {
      console.warn(`skip ${key}: no snapshot yet`)
      return null
    }

    const destination = path.join(boardsDir, key)
    copyDirectory(staticDir, destination)
    fs.cpSync(data, path.join(destination, 'data'), { recursive: true, force: true })

    const config = readJson(path.join(worktree, 'board-config.json')) ?? {}
    const deployment = Object.values(config.deployments ?? {})[0] ?? {}
    const snapshot = readJson(path.join(destination, 'data', 'snapshot.json')) ?? {}
    const repository = snapshot.repository ?? {}
    const generatedAt = snapshot.generated_at ?? ''

    return {
      id: key,
      branch,
      label: deployment.label || repository.name?.split('/').pop() || key.split('/')[1],
      boardUrl: `./${key}/`,
      snapshotUrl: `./${key}/data/snapshot.json`,
      generatedAt,
      repository: {
        name: repository.name || key,
        url: repository.url || `https://github.com/${key}`,
        defaultBranch: repository.default_branch || 'main',
      },
    }
  } finally {
    try {
      git(['worktree', 'remove', '--force', worktree])
    } catch {
      // The checkout may be incomplete; the temp directory removal below wins.
    }
    fs.rmSync(temporary, { recursive: true, force: true })
  }
}

function renderIndex(boards) {
  const cards = boards
    .map(
      (board) => `      <a class="card" href="${escapeHtml(board.boardUrl)}">
        <span class="repo">${escapeHtml(board.repository.name)}</span>
        <span class="label">${escapeHtml(board.label)}</span>
        <span class="meta">${board.generatedAt ? `更新于 ${escapeHtml(board.generatedAt.slice(0, 10))}` : '等待首次采集'}</span>
      </a>`
    )
    .join('\n')

  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>单仓看板 · Dashboard</title>
<style>
  :root { color-scheme: light dark; --bg: #f6f7f9; --card: #fff; --ink: #1c2430; --dim: #61708a; --line: #dde3ec; --accent: #2563eb; }
  @media (prefers-color-scheme: dark) { :root { --bg: #111827; --card: #1c2534; --ink: #e5e9f0; --dim: #98a6bc; --line: #2d3a4f; --accent: #7aa2ff; } }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", sans-serif; }
  main { max-width: 980px; margin: 0 auto; padding: 40px 22px 64px; }
  a { color: inherit; text-decoration: none; }
  .back { color: var(--dim); font-size: 13px; }
  .back:hover { color: var(--accent); }
  h1 { margin: 14px 0 6px; font-size: 28px; letter-spacing: -0.02em; }
  p.lead { margin: 0 0 28px; color: var(--dim); max-width: 680px; }
  .grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
  .card { display: flex; flex-direction: column; gap: 3px; padding: 16px 17px; border: 1px solid var(--line); border-radius: 12px; background: var(--card); transition: border-color .15s, transform .15s; }
  .card:hover { border-color: var(--accent); transform: translateY(-1px); }
  .repo { font-weight: 600; overflow-wrap: anywhere; }
  .label { color: var(--dim); font-size: 13px; }
  .meta { margin-top: 8px; color: var(--dim); font-size: 12px; }
  .empty { padding: 28px; border: 1px dashed var(--line); border-radius: 12px; color: var(--dim); }
  footer { margin-top: 30px; color: var(--dim); font-size: 12px; }
</style>
</head>
<body>
<main>
  <a class="back" href="../">← 公共多仓 Dashboard</a>
  <h1>单仓看板</h1>
  <p class="lead">每个代码仓一个看板，由本仓的 <code>board/&lt;owner&gt;/&lt;repo&gt;</code> 数据分支托管；不需要复制模板或新建仓库。</p>
  <div class="grid">
${cards || '    <div class="empty">还没有看板。维护者在本仓运行 <code>Create board</code> 工作流即可创建。</div>'}
  </div>
  <footer>数据每天自动刷新；私有源仓只在本地运行，不发布到这里。</footer>
</main>
</body>
</html>
`
}

function main() {
  if (!fs.existsSync(staticDir)) {
    throw new Error(`missing static assets: ${staticDir}`)
  }

  fs.mkdirSync(dist, { recursive: true })
  fs.rmSync(boardsDir, { recursive: true, force: true })
  fs.mkdirSync(boardsDir, { recursive: true })
  fs.writeFileSync(path.join(dist, '.nojekyll'), '')

  const boards = []
  for (const branch of listBoardRefs()) {
    try {
      const board = assembleBoard(branch)
      if (board) boards.push(board)
    } catch (error) {
      // One broken board must not block the rest of the public site.
      const key = branch.slice(refPrefix.length)
      if (/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(key)) {
        fs.rmSync(path.join(boardsDir, key), { recursive: true, force: true })
      }
      console.warn(`skip ${branch}: ${error.message}`)
    }
  }

  boards.sort((a, b) => a.id.localeCompare(b.id))
  fs.writeFileSync(path.join(boardsDir, 'index.html'), renderIndex(boards))
  fs.writeFileSync(
    path.join(boardsDir, 'boards.json'),
    `${JSON.stringify({ generated_at: new Date().toISOString(), boards }, null, 2)}\n`
  )
  console.log(`assembled ${boards.length} board(s) into ${path.relative(root, boardsDir)}`)
}

main()
