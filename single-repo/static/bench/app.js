;(() => {
  'use strict'

  // Inside a board the data is one level up; a standalone copy keeps data/ next to the page.
  const DATA_URLS = ['../data/bench.json', 'data/bench.json']
  const REPO = 'https://github.com/openJiuwen-ai/DigitalMate'

  const $ = (s) => document.querySelector(s)
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
  const pct = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? '—' : `${Math.round(Number(v) * 100)}%`)
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
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—'
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

  const normalizeTask = (task) => {
    const passes = Number(task.passes) || 0
    const trials = Number(task.trials) || 1
    return {
      id: task.id || task.taskId || '',
      title: task.title || '',
      status: task.status || (task.flaky ? 'FLAKY' : passes >= trials ? 'PASS' : passes > 0 ? 'FLAKY' : 'FAIL'),
      trials,
      passes,
      passAt1: Number.isFinite(Number(task.passAt1)) ? Number(task.passAt1) : Number.isFinite(Number(task.passRate)) ? Number(task.passRate) : null,
    }
  }
  const benchmarksOf = (bench) => {
    if (Array.isArray(bench && bench.benchmarks) && bench.benchmarks.length) {
      return bench.benchmarks.map((item) => ({ ...item, tasks: (item.tasks || []).map(normalizeTask) }))
    }
    return [
      {
        name: benchmarkName(bench),
        status: bench.status,
        tasks: (bench.tasks || []).map(normalizeTask),
        trials: trialsOf(bench),
        passAt1: bench.passAt1,
        passAtK: bench.passAtK,
        passHatK: bench.passHatK,
        detail: detailOf(bench),
        latencyMs: bench.latencyMs,
      },
    ]
  }
  const latestWithData = (runs) => runs.find((run) => {
    const bench = benchOf(run)
    return bench && benchmarksOf(bench).length > 0
  }) || runs[0]
  const totalOf = (benchmarks, key) => benchmarks.reduce((sum, item) => sum + (Number(item[key]) || 0), 0)
  const totalTasks = (benchmarks) => benchmarks.reduce((sum, item) => sum + (Array.isArray(item.tasks) ? item.tasks.length : 0), 0)

  function renderLatest(runs) {
    const latest = latestWithData(runs)
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
      card('规模', `<span class="mono">${totalTasks(benchmarks)} task</span>`, [
        ['Tasks', String(totalTasks(benchmarks))],
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
    return benchmarksOf(bench).flatMap((item) => {
      const tasks = item.tasks || []
      const toggle = tasks.length
        ? '<button type="button" class="bench-toggle" aria-expanded="false" title="展开 task 列表">▸</button>'
        : '<span class="toggle-spacer" aria-hidden="true"></span>'
      const row = `<tr class="bench-row">
      <td>${toggle}${esc(item.name)}</td>
      <td>${statusChip(item.status)}</td>
      <td class="num">${tasks.length}</td>
      <td class="num">${item.trials || 0}</td>
      <td class="num">${pct(item.passAt1)}</td>
      <td class="num">${pct(item.passAtK)}</td>
      <td class="num">${pct(item.passHatK)}</td>
      <td>${esc(item.detail || '—')}</td>
      <td class="nowrap">${latency(item)}</td>
      <td>${runLink(run)}</td>
    </tr>`
      if (!tasks.length) return [row]
      const taskRows = tasks.map((task) => `<tr>
        <td class="mono">${esc(task.id)}</td>
        <td>${statusChip(task.status)}</td>
        <td class="num">${pct(task.passAt1)}</td>
        <td class="num">${task.passes}/${task.trials}</td>
        <td class="task-title">${esc(task.title)}</td>
      </tr>`).join('')
      return [row, `<tr class="task-row" hidden><td colspan="10"><table class="task-table"><thead><tr><th>task</th><th>result</th><th>pass@1</th><th>passes/trials</th><th>title</th></tr></thead><tbody>${taskRows}</tbody></table></td></tr>`]
    })
  }

  function renderTables(runs) {
    const summary = $('#summary')
    if (!runs.length) {
      summary.innerHTML = '<tbody><tr><td class="empty">暂无记录</td></tr></tbody>'
    } else {
      summary.innerHTML = `<thead><tr>${SUMMARY_HEAD.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${summaryRows(latestWithData(runs)).join('')}</tbody>`
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
    document.querySelectorAll('.bench-toggle').forEach((button) => {
      button.addEventListener('click', () => {
        const taskRow = button.closest('tr').nextElementSibling
        if (!taskRow || !taskRow.classList.contains('task-row')) return
        const open = taskRow.hidden
        taskRow.hidden = !open
        button.setAttribute('aria-expanded', String(open))
        button.textContent = open ? '▾' : '▸'
      })
    })
  }

  // ------------------------------------------------------------ trend chart
  // One line per benchmark, shared y-axis. Keep the labels stable even when a
  // run only contains one profile (older nightly runs are Pi-Bench only).
  const SERIES = [
    { key: 'gaia', label: 'GAIA', match: /GAIA/i, color: '#4e79a7' },
    { key: 'past', label: 'PAST-Bench', match: /PAST/i, color: '#f28e2b' },
    { key: 'pi', label: 'Pi-Bench', match: /Pi-Bench/i, color: '#59a14f' },
    { key: 'vita', label: 'VitaBench', match: /VitaBench/i, color: '#e15759' },
    { key: 'vibelife', label: 'VibeLife office', match: /VibeLife/i, color: '#b07aa1' },
  ]
  const seriesFor = (name) => SERIES.find((item) => item.match.test(name)) || { key: name, label: name, color: '#8b949e' }
  const scoreOf = (item) => {
    const passAt1 = item.passAt1
    if (passAt1 !== null && passAt1 !== undefined && passAt1 !== '' && Number.isFinite(Number(passAt1))) {
      return Number(passAt1)
    }
    const match = String(item.detail || '').match(/^([0-9.]+)/)
    return match ? Number(match[1]) : null
  }
  const metricValue = (item, metric) => {
    if (!item) return null
    if (metric === 'latency') {
      const ms = Number(item.latencyMs && item.latencyMs.p50)
      return Number.isFinite(ms) ? ms / 1000 : null
    }
    const value = scoreOf(item)
    return Number.isFinite(value) ? value * 100 : null
  }
  const xLabel = (run) => {
    const d = new Date(runTime(run))
    if (Number.isNaN(d.getTime())) return '—'
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  }

  let lastRuns = []
  let chartMetric = 'pass'

  function renderChart(runs) {
    const target = $('#chart')
    const note = $('#chart-note')
    if (!target) return
    const ordered = runs.slice().sort((a, b) => new Date(runTime(a)) - new Date(runTime(b)))
    const points = ordered.map((run) => {
      const bench = benchOf(run)
      return { run, items: bench ? benchmarksOf(bench) : [] }
    })
    const series = SERIES.map((spec) => ({
      ...spec,
      values: points
        .map((point, index) => {
          const item = point.items.find((candidate) => seriesFor(candidate.name).key === spec.key)
          const value = metricValue(item, chartMetric)
          return value === null ? null : { index, value, item, run: point.run }
        })
        .filter(Boolean),
    })).filter((item) => item.values.length)
    if (!series.length) {
      target.innerHTML = '<div class="chart-empty">还没有可画趋势的 benchmark 数据。</div>'
      note.textContent = ''
      return
    }
    const width = 1000
    const height = 280
    const left = 48
    const right = 16
    const top = 16
    const bottom = 36
    const plotW = width - left - right
    const plotH = height - top - bottom
    const maxValue = Math.max(...series.flatMap((item) => item.values.map((point) => point.value)), chartMetric === 'pass' ? 100 : 1)
    const yMax = chartMetric === 'pass' ? 100 : Math.max(1, Math.ceil(maxValue * 1.2))
    const xAt = (index) => (points.length === 1 ? left + plotW / 2 : left + (index * plotW) / (points.length - 1))
    const yAt = (value) => top + plotH - (Math.max(0, value) / yMax) * plotH
    const ticks = [0, 1, 2, 3, 4].map((index) => {
      const value = (yMax * index) / 4
      return { value, y: yAt(value) }
    })
    const labelEvery = Math.max(1, Math.ceil(points.length / 8))
    const grid = ticks
      .map(
        (tick) =>
          `<line class="gridline" x1="${left}" y1="${tick.y.toFixed(1)}" x2="${width - right}" y2="${tick.y.toFixed(1)}"></line>
           <text class="tick" x="${left - 8}" y="${(tick.y + 3).toFixed(1)}" text-anchor="end">${chartMetric === 'pass' ? `${Math.round(tick.value)}%` : `${tick.value.toFixed(1)}s`}</text>`,
      )
      .join('')
    const xAxis = points
      .map((point, index) =>
        index % labelEvery === 0 || index === points.length - 1
          ? `<text class="tick" x="${xAt(index).toFixed(1)}" y="${height - 14}" text-anchor="middle">${esc(xLabel(point.run))}</text>`
          : '',
      )
      .join('')
    const lines = series
      .map((item) => {
        const coords = item.values.map((point) => `${xAt(point.index).toFixed(1)},${yAt(point.value).toFixed(1)}`).join(' ')
        const path =
          item.values.length > 1
            ? `<polyline class="series-line" points="${coords}" stroke="${item.color}"></polyline>`
            : ''
        const dots = item.values
          .map(
            (point) =>
              `<circle class="series-dot" cx="${xAt(point.index).toFixed(1)}" cy="${yAt(point.value).toFixed(1)}" r="3.5" fill="${item.color}">
                <title>${esc(item.label)} · ${esc(xLabel(point.run))} · ${chartMetric === 'pass' ? `${point.value.toFixed(1)}%` : `${point.value.toFixed(2)}s`}</title>
              </circle>`,
          )
          .join('')
        return path + dots
      })
      .join('')
    const legend = series
      .map((item) => `<span><i style="background:${item.color}"></i>${esc(item.label)}</span>`)
      .join('')
    target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Bench 趋势图">
      ${grid}
      <line class="axis" x1="${left}" y1="${top + plotH}" x2="${width - right}" y2="${top + plotH}"></line>
      ${xAxis}
      ${lines}
    </svg><div class="chart-legend">${legend}</div>`
    note.textContent =
      chartMetric === 'pass'
        ? '每个点是一轮运行；通过率取 pass@1，VibeLife 取加权得分。至少两轮后才会出现连线。'
        : '每个点是一轮运行；p50 为查询发出到消息接收，单位秒。至少两轮后才会出现连线。'
  }

  document.querySelectorAll('[data-metric]').forEach((button) => {
    button.addEventListener('click', () => {
      chartMetric = button.dataset.metric
      document.querySelectorAll('[data-metric]').forEach((item) => item.classList.toggle('active', item === button))
      renderChart(lastRuns)
    })
  })

  async function main() {
    try {
      const data = normalize(await load())
      const runs = data.runs.slice().sort((a, b) => new Date(runTime(b)) - new Date(runTime(a)))
      $('#source-line').textContent = data.updatedAt
        ? `数据更新于 ${fmt(data.updatedAt)} · 最近 ${runs.length} 次运行`
        : `最近 ${runs.length} 次运行`
      renderLatest(runs)
      renderTables(runs)
      lastRuns = runs
      renderChart(runs)
    } catch (err) {
      const box = $('#error')
      box.hidden = false
      box.textContent = err.message || String(err)
    }
  }

  main()
})()
