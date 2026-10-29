import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const script = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'assemble-boards.mjs')

function run(cwd, command, args) {
  return execFileSync(command, args, {
    cwd,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  })
}

function git(cwd, ...args) {
  return run(cwd, 'git', args)
}

test('assembles board branches into dist/boards/<owner>/<repo>', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'assemble-boards-'))
  try {
    const remote = path.join(root, 'remote.git')
    const seed = path.join(root, 'seed')
    const workspace = path.join(root, 'workspace')

    git(root, 'init', '--bare', remote)

    fs.mkdirSync(seed)
    git(seed, 'init')
    git(seed, 'config', 'user.name', 'test')
    git(seed, 'config', 'user.email', 'test@example.com')
    git(seed, 'checkout', '--orphan', 'board/acme/widget')

    fs.writeFileSync(
      path.join(seed, 'board-config.json'),
      JSON.stringify({ deployments: { 'acme/widget': { label: 'Widget', repository: 'acme/dashboard' } } })
    )
    fs.mkdirSync(path.join(seed, 'site', 'data'), { recursive: true })
    fs.writeFileSync(
      path.join(seed, 'site', 'data', 'snapshot.json'),
      JSON.stringify({
        generated_at: '2026-10-28T00:00:00Z',
        repository: { name: 'acme/widget', url: 'https://github.com/acme/widget', default_branch: 'main' },
      })
    )
    git(seed, 'add', '.')
    git(seed, 'commit', '-m', 'board data')
    git(seed, 'remote', 'add', 'origin', remote)
    git(seed, 'push', 'origin', 'HEAD:refs/heads/board/acme/widget')

    fs.mkdirSync(workspace)
    git(workspace, 'init')
    git(workspace, 'config', 'user.name', 'test')
    git(workspace, 'config', 'user.email', 'test@example.com')
    fs.writeFileSync(path.join(workspace, 'README.md'), 'workspace\n')
    git(workspace, 'add', '.')
    git(workspace, 'commit', '-m', 'init')
    git(workspace, 'remote', 'add', 'origin', remote)

    const staticDir = path.join(workspace, 'single-repo', 'static')
    fs.mkdirSync(staticDir, { recursive: true })
    fs.writeFileSync(path.join(staticDir, 'index.html'), '<!doctype html><title>board</title>\n')
    fs.writeFileSync(path.join(staticDir, 'style.css'), 'body {}\n')
    fs.mkdirSync(path.join(workspace, 'dist'))

    run(workspace, process.execPath, [script])

    assert.equal(
      fs.readFileSync(path.join(workspace, 'dist', 'boards', 'acme', 'widget', 'index.html'), 'utf8'),
      '<!doctype html><title>board</title>\n'
    )
    assert.ok(fs.existsSync(path.join(workspace, 'dist', 'boards', 'acme', 'widget', 'data', 'snapshot.json')))
    assert.ok(fs.existsSync(path.join(workspace, 'dist', '.nojekyll')))

    const index = fs.readFileSync(path.join(workspace, 'dist', 'boards', 'index.html'), 'utf8')
    assert.match(index, /acme\/widget/)
    assert.match(index, /Widget/)

    const boards = JSON.parse(fs.readFileSync(path.join(workspace, 'dist', 'boards', 'boards.json'), 'utf8'))
    assert.equal(boards.boards.length, 1)
    assert.equal(boards.boards[0].id, 'acme/widget')
    assert.equal(boards.boards[0].boardUrl, './acme/widget/')
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('assembles an imported static board from a non-board branch', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'assemble-boards-imported-'))
  try {
    const remote = path.join(root, 'remote.git')
    const seed = path.join(root, 'seed')
    const workspace = path.join(root, 'workspace')

    git(root, 'init', '--bare', remote)

    fs.mkdirSync(seed)
    git(seed, 'init')
    git(seed, 'config', 'user.name', 'test')
    git(seed, 'config', 'user.email', 'test@example.com')
    git(seed, 'checkout', '--orphan', 'digitalmate')

    fs.writeFileSync(
      path.join(seed, 'board-config.json'),
      JSON.stringify({ deployments: { 'openJiuwen-ai/digitalmate': { label: 'digitalmate' } } })
    )
    fs.mkdirSync(path.join(seed, 'site', 'data'), { recursive: true })
    fs.writeFileSync(
      path.join(seed, 'site', 'data', 'snapshot.json'),
      JSON.stringify({
        generated_at: '2026-10-28T00:00:00Z',
        repository: { name: 'openJiuwen-ai/DigitalMate', default_branch: 'main' },
      })
    )
    git(seed, 'add', '.')
    git(seed, 'commit', '-m', 'imported snapshot')
    git(seed, 'remote', 'add', 'origin', remote)
    git(seed, 'push', 'origin', 'HEAD:refs/heads/digitalmate')

    fs.mkdirSync(workspace)
    git(workspace, 'init')
    git(workspace, 'config', 'user.name', 'test')
    git(workspace, 'config', 'user.email', 'test@example.com')
    fs.mkdirSync(path.join(workspace, 'config'))
    fs.writeFileSync(
      path.join(workspace, 'config', 'imported-boards.json'),
      JSON.stringify({ boards: [{ branch: 'digitalmate', key: 'openJiuwen-ai/digitalmate' }] })
    )
    fs.writeFileSync(path.join(workspace, 'README.md'), 'workspace\n')
    git(workspace, 'add', '.')
    git(workspace, 'commit', '-m', 'init')
    git(workspace, 'remote', 'add', 'origin', remote)

    const staticDir = path.join(workspace, 'single-repo', 'static')
    fs.mkdirSync(staticDir, { recursive: true })
    fs.writeFileSync(path.join(staticDir, 'index.html'), '<!doctype html><title>board</title>\n')
    fs.mkdirSync(path.join(workspace, 'dist'))

    run(workspace, process.execPath, [script])

    assert.ok(
      fs.existsSync(
        path.join(workspace, 'dist', 'boards', 'openJiuwen-ai', 'digitalmate', 'data', 'snapshot.json')
      )
    )
    const boards = JSON.parse(fs.readFileSync(path.join(workspace, 'dist', 'boards', 'boards.json'), 'utf8'))
    assert.equal(boards.boards.length, 1)
    assert.equal(boards.boards[0].id, 'openJiuwen-ai/digitalmate')
    assert.equal(boards.boards[0].branch, 'refs/heads/digitalmate')
    assert.equal(boards.boards[0].boardUrl, './openJiuwen-ai/digitalmate/')
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('writes an empty index when there are no board branches', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'assemble-boards-empty-'))
  try {
    const remote = path.join(root, 'remote.git')
    const workspace = path.join(root, 'workspace')
    git(root, 'init', '--bare', remote)

    fs.mkdirSync(workspace)
    git(workspace, 'init')
    git(workspace, 'config', 'user.name', 'test')
    git(workspace, 'config', 'user.email', 'test@example.com')
    fs.writeFileSync(path.join(workspace, 'README.md'), 'workspace\n')
    git(workspace, 'add', '.')
    git(workspace, 'commit', '-m', 'init')
    git(workspace, 'remote', 'add', 'origin', remote)

    const staticDir = path.join(workspace, 'single-repo', 'static')
    fs.mkdirSync(staticDir, { recursive: true })
    fs.writeFileSync(path.join(staticDir, 'index.html'), '<!doctype html><title>board</title>\n')
    fs.mkdirSync(path.join(workspace, 'dist'))

    run(workspace, process.execPath, [script])

    assert.match(fs.readFileSync(path.join(workspace, 'dist', 'boards', 'index.html'), 'utf8'), /还没有看板/)
    const boards = JSON.parse(fs.readFileSync(path.join(workspace, 'dist', 'boards', 'boards.json'), 'utf8'))
    assert.deepEqual(boards.boards, [])
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
