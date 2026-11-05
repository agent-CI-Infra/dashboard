;(() => {
  'use strict'

  // Inside a board the data is one level up; a standalone copy keeps data/ next to the page.
  const DATA_URLS = ['../data/bench.json', 'data/bench.json']
  const REPO = 'https://github.com/openJiuwen-ai/DigitalMate'

  const $ = (s) => document.querySelector(s)
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
  const pct = (v) => (Number.isFinite(Number(v)) ? `${Math.round(Number(v) * 100)}%` : '—')
  const fmt = (v) => {
    if (!v) return '—'
    const d = new Date(v)
    return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString('zh-CN', { hour12: false })
  }
  const shortSha = (v) => (v ? String(v).slice(0, 7) : '—')

  const STATUS_LABEL = { PASS: '通过', FLAKY: '不稳定', FAIL: '失败', ERROR: '未产出' }
  const STATUS_CLASS = { PASS: 'pass', FLAKY: 'flaky', FAIL: 'fail', ERROR: 'error' }
  const statusChip = (status) => {
    const s = String(status || 'UNKNOWN').toUpperCase()
    const cls = STATUS_CLASS[s] || 'unknown'
    return `<span class="status ${cls}" title="${esc(s)}">${esc(STATUS_LABEL[s] || s)}</span>`
  }

  async function load() {
    for (const url of DATA_URLS) {
      try {
        const res = await fetch(url, { cache: 'no-store' })
        if (res.ok) return res.json()
      } catch {
        // Try the next layout.
      }
    }
    throw new Error('没有找到 data/bench.json。Nightly 跑完后由 Collect DigitalMate bench 工作流生成。')
  }

  // The collector writes { runs: [...] }; accept a single summary or a bare
  // array as well so a hand-written file still renders.
  const normalize = (raw) => {
    if (Array.isArray(raw)) return { updatedAt: null, runs: raw }
    if (raw && Array.isArray(raw.runs)) return raw
    if (raw && raw.kind === 'bench') return { updatedAt: raw.generatedAt, runs: [raw] }
    return { updatedAt: null, runs: [] }
  }

  const benchOf = (run) => (run && run.kind === 'bench' ? run : run && run.bench) || (run && run.task ? run : null)
  const runTime = (run) => run.generatedAt || run.updatedAt || ''
  const runId = (run) => run.runId || ''
  const runNumber = (run) => {
    if (run.runNumber) return String(run.runNumber)
    const match = String(runId(run)).match(/(?:nightly|bench)-(\d+)/)
    return match ? match[1] : ''
  }
  const runLink = (run) => {
    const number = runNumber(run)
    const url = run.runUrl || (number ? `${REPO}/actions/runs/${number}` : '')
    return url ? `<a href="${esc(url)}" target="_blank" rel="noopener">#${esc(number || 'run')}</a>` : '—'
  }

  const tasksOf = (bench) => (Array.isArray(bench && bench.tasks) ? bench.tasks : [])
  const passedOf = (bench) => tasksOf(bench).reduce((sum, task) => sum + (Number(task.passes) || 0), 0)
  const trialsOf = (bench) => {
    if (Number.isFinite(Number(bench.trials))) return Number(bench.trials)
    return tasksOf(bench).reduce((sum, task) => sum + (Number(task.trials) || 0), 0)
  }
  const taskCount = (bench) => {
    if (Number.isFinite(Number(bench.taskCount))) return Number(bench.taskCount)
    return tasksOf(bench).length || (bench.task ? 1 : 0)
  }
  const fmtMs = (value) => {
    if (!Number.isFinite(Number(value))) return '—'
    const ms = Number(value)
    return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
  }
  const latency = (bench) => {
    const value = bench && bench.latencyMs
    if (!value || typeof value !== 'object') return '—'
    return `${fmtMs(value.p50)} / ${fmtMs(value.p95)}`
  }
  const detailOf = (bench) => {
    if (bench && bench.detail) return String(bench.detail)
    const trials = trialsOf(bench)
    if (!trials) return '—'
    return `${passedOf(bench)}/${trials} 通过`
  }
  const benchmarkName = (bench) => {
    const profile = String(bench.profile || '').trim()
    const task = String(bench.task || '').trim()
    if (profile && task) return `${profile} · ${task}`
    return profile || task || '—'
  }

  const benchmarksOf = (bench) => {
    if (Array.isArray(bench && bench.benchmarks) && bench.benchmarks.length) return bench.benchmarks
    return [
      {
        name: benchmarkName(bench),
        status: bench.status,
        tasks: taskCount(bench),
        trials: trialsOf(bench),
        passAt1: bench.passAt1,
        passAtK: bench.passAtK,
        passHatK: bench.passHatK,
        detail: detailOf(bench),
        latencyMs: bench.latencyMs,
      },
    ]
  }
  const totalOf = (benchmarks, key) => benchmarks.reduce((sum, item) => sum + (Number(item[key]) || 0), 0)

  function renderLatest(runs) {
    const latest = runs[0]
    if (!latest) {
      $('#latest').innerHTML = '<div class="empty">还没有 Bench 运行记录。</div>'
      return
    }
    const bench = benchOf(latest)
    if (!bench) {
      $('#latest').innerHTML = '<div class="empty">最近一次 Nightly 没有产出 bench summary。</div>'
      return
    }
    const benchmarks = benchmarksOf(bench)
    const many = benchmarks.length > 1
    $('#latest').innerHTML = [
      card('Bench 结果', statusChip(bench.status), [
        ['Benchmark', many ? `${benchmarks.length} benchmarks` : benchmarks[0].name],
        ['pass@1', many ? '—' : `${pct(benchmarks[0].passAt1)} · ${passedOf(bench)}/${trialsOf(bench)}`],
        ['pass@k', many ? '—' : pct(benchmarks[0].passAtK)],
      ]),
      card('规模', `<span class="mono">${totalOf(benchmarks, 'tasks')} task</span>`, [
        ['Tasks', String(totalOf(benchmarks, 'tasks'))],
        ['Trials', String(totalOf(benchmarks, 'trials'))],
        ['p50 / p95', many ? '—' : latency(bench)],
      ]),
      card('运行', `<span class="mono">${esc(shortSha(latest.commit || bench.commit))}</span>`, [
        ['时间', fmt(runTime(latest))],
        ['后端', hostOf(latest.backend || bench.backend)],
        ['运行号', runLink(latest)],
      ]),
    ].join('')
  }

  function card(title, chip, rows) {
    return `<article class="card">
      <div class="card-head"><h3>${esc(title)}</h3>${chip}</div>
      <dl>${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>
    </article>`
  }

  function hostOf(url) {
    if (!url) return '—'
    try {
      return new URL(String(url)).host
    } catch {
      return String(url)
    }
  }

  const SUMMARY_HEAD = ['benchmark', 'result', 'tasks', 'trials', 'pass@1', 'pass@k', 'pass^k', 'score / detail', 'p50 / p95', '运行']

  function summaryRows(run) {
    const bench = benchOf(run)
    if (!bench) {
      return [`<tr><td class="muted">—</td><td>${statusChip('ERROR')}</td>${'<td class="muted">—</td>'.repeat(7)}<td>${runLink(run)}</td></tr>`]
    }
    return benchmarksOf(bench).map(
      (item) => `<tr>
      <td>${esc(item.name)}</td>
      <td>${statusChip(item.status)}</td>
      <td class="num">${item.tasks || 0}</td>
      <td class="num">${item.trials || 0}</td>
      <td class="num">${pct(item.passAt1)}</td>
      <td class="num">${pct(item.passAtK)}</td>
      <td class="num">${pct(item.passHatK)}</td>
      <td>${esc(item.detail || '—')}</td>
      <td class="nowrap">${latency(item)}</td>
      <td>${runLink(run)}</td>
    </tr>`,
    )
  }

  function renderTables(runs) {
    const summary = $('#summary')
    if (!runs.length) {
      summary.innerHTML = '<tbody><tr><td class="empty">暂无记录</td></tr></tbody>'
    } else {
      summary.innerHTML = `<thead><tr>${SUMMARY_HEAD.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${summaryRows(runs[0]).join('')}</tbody>`
    }

    const history = $('#history')
    if (!runs.length) {
      history.innerHTML = '<tbody><tr><td class="empty">暂无记录</td></tr></tbody>'
      return
    }
    const rows = runs
      .map((run) => {
        const bench = benchOf(run)
        const benchmarks = bench ? benchmarksOf(bench) : []
        const label = benchmarks.length > 1 ? `all (${benchmarks.length})` : benchmarks[0] ? benchmarks[0].name : ''
        return `<tr>
          <td class="nowrap">${esc(fmt(runTime(run)))}</td>
          <td>${bench ? esc(label) : '<span class="muted">—</span>'}</td>
          <td>${bench ? statusChip(bench.status) : statusChip('ERROR')}</td>
          <td class="num">${bench ? pct(bench.passAt1) : '—'}</td>
          <td class="mono">${esc(shortSha(run.commit || (bench && bench.commit)))}</td>
          <td>${runLink(run)}</td>
        </tr>`
      })
      .join('')
    history.innerHTML = `<thead><tr>
      <th>时间</th><th>benchmark</th><th>result</th><th>pass@1</th><th>commit</th><th>运行</th>
    </tr></thead><tbody>${rows}</tbody>`
  }

  async function main() {
    try {
      const data = normalize(await load())
      const runs = data.runs.slice().sort((a, b) => new Date(runTime(b)) - new Date(runTime(a)))
      $('#source-line').textContent = data.updatedAt
        ? `数据更新于 ${fmt(data.updatedAt)} · 最近 ${runs.length} 次运行`
        : `最近 ${runs.length} 次运行`
      renderLatest(runs)
      renderTables(runs)
    } catch (err) {
      const box = $('#error')
      box.hidden = false
      box.textContent = err.message || String(err)
    }
  }

  main()
})()
