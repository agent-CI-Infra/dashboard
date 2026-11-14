;(() => {
  'use strict'

  // Inside a board the data is one level up; a standalone copy keeps data/ next to the page.
  const DATA_URLS = ['../data/runners.json', 'data/runners.json']
  // The `digitalmate` data branch is public; raw.githubusercontent serves the
  // newest JSON without waiting for the hourly Pages deploy. Unreachable
  // networks fall back to the deployed copy silently.
  const RAW_BASE =
    'https://raw.githubusercontent.com/agent-CI-Infra/dashboard/digitalmate/site/data/'
  const MACHINES = ['winbox', 'winbox2']
  const HISTORY_WINDOW = 96 // samples shown in the timeline (24h at 4 samples per hour)
  const STALE_AFTER_MS = 45 * 60 * 1000
  const REFRESH_MS = 60 * 1000

  const $ = (s) => document.querySelector(s)
  const esc = (v) =>
    String(v ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
    )
  const fmt = (v) => {
    if (!v) return '—'
    const d = new Date(v)
    return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString('zh-CN', { hour12: false })
  }
  const bps = (v) => {
    const n = Number(v)
    if (!Number.isFinite(n) || n <= 0) return '0 B/s'
    if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB/s'
    if (n >= 1024) return (n / 1024).toFixed(0) + ' KB/s'
    return n.toFixed(0) + ' B/s'
  }
  const ago = (v) => {
    if (!v) return '—'
    const ms = Date.now() - new Date(v).getTime()
    if (!Number.isFinite(ms) || ms < 0) return '—'
    const min = Math.round(ms / 60000)
    if (min < 1) return '刚刚'
    if (min < 60) return `${min} 分钟前`
    return `${Math.floor(min / 60)} 小时 ${min % 60} 分钟前`
  }
  const durSince = (v) => {
    if (!v) return ''
    const ms = Date.now() - new Date(v).getTime()
    if (!Number.isFinite(ms) || ms < 0) return ''
    const min = Math.round(ms / 60000)
    if (min < 60) return `已 ${min} 分钟`
    return `已 ${Math.floor(min / 60)} 小时 ${min % 60} 分钟`
  }

  async function fetchJSON(url) {
    const res = await fetch(url, { cache: 'no-store' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  }

  async function loadDeployed(paths) {
    for (const url of paths) {
      try {
        return await fetchJSON(url)
      } catch {
        // Try the next layout.
      }
    }
    throw new Error('数据文件不存在')
  }

  // The collector writes { updatedAt, runners, queued, history: [{ at, s, b, j }] }.
  const normalize = (raw) => {
    const data = raw && typeof raw === 'object' ? raw : {}
    return {
      updatedAt: data.updatedAt || null,
      runners: Array.isArray(data.runners) ? data.runners : [],
      queued: Array.isArray(data.queued) ? data.queued : [],
      history: Array.isArray(data.history) ? data.history : [],
    }
  }

  // Only re-render when the snapshot is actually newer than what is on screen.
  const seen = {}
  const newer = (key, at) => {
    if (!at) return false
    if (String(at) <= String(seen[key] || '')) return false
    seen[key] = String(at)
    return true
  }

  const postHeight = () => {
    if (window.parent !== window) {
      window.parent.postMessage(
        { type: 'runners-height', height: document.documentElement.scrollHeight },
        location.origin
      )
    }
  }

  const chip = (cls, text, title) =>
    `<span class="chip ${cls}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`

  const statusChips = (runner) => {
    if (runner.status === 'online') {
      return chip('ok', '在线') + (runner.busy ? chip('busy', '忙碌') : chip('idle', '空闲'))
    }
    if (runner.status === 'offline') return chip('down', '离线')
    return chip('na', runner.status || '未知')
  }

  const jobLine = (runner) => {
    const job = runner.job
    if (!job || !job.name) return ''
    const name = job.url
      ? `<a href="${esc(job.url)}" target="_blank" rel="noopener">${esc(job.name)}</a>`
      : esc(job.name)
    const wf = job.workflow ? ` · ${esc(job.workflow)} run#${esc(job.run_number ?? '')}` : ''
    const dur = durSince(job.started_at) ? ` · ${durSince(job.started_at)}` : ''
    return `<p class="meta job">正在跑 ${name}${wf}${dur}</p>`
  }

  const renderCards = (runners) => {
    const cards = runners.map((r) => {
      const meta = [
        r.machine ? `机器 ${r.machine}` : null,
        r.emulator ? `模拟器 ${r.emulator}` : null,
        r.device ? `设备 ${r.device}` : null,
      ]
        .filter(Boolean)
        .join(' · ')
      const labels = (r.labels || [])
        .map((l) => `<span class="label-chip">${esc(l)}</span>`)
        .join('')
      const extra = [r.id != null ? `GitHub runner id ${esc(r.id)}` : null, r.os ? esc(r.os) : null]
        .filter(Boolean)
        .join(' · ')
      return `<article class="run-card${r.status === 'offline' ? ' down' : ''}">
        <div class="run-head"><span class="run-name" title="${esc(r.name)}">${esc(r.name)}</span>${statusChips(r)}</div>
        ${meta ? `<p class="meta">${esc(meta)}</p>` : ''}
        ${jobLine(r)}
        ${extra ? `<p class="meta">${esc(extra)}</p>` : ''}
        ${labels ? `<div class="labels">${labels}</div>` : ''}
      </article>`
    })
    $('#cards').innerHTML = cards.join('') || '<p class="muted">还没有 runner 数据。</p>'
    $('#count').textContent = runners.length ? `（${runners.length} 台）` : ''
    postHeight()
  }

  const renderQueued = (queued) => {
    const el = $('#queued')
    const list = Array.isArray(queued) ? queued : []
    if (!list.length) {
      el.hidden = true
      el.innerHTML = ''
      return
    }
    const items = list
      .slice(0, 5)
      .map((j) => {
        const link = j.url
          ? `<a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.name)}</a>`
          : esc(j.name)
        return `<li>${link}<span class="muted small"> · ${esc(j.workflow || '')} run#${esc(j.run_number ?? '')} · 等待 ${ago(j.created_at)}</span></li>`
      })
      .join('')
    const more = list.length > 5 ? `<li class="muted small">… 还有 ${list.length - 5} 个</li>` : ''
    el.hidden = false
    el.innerHTML = `<h2>排队中的任务（${list.length}）</h2><ul>${items}${more}</ul>`
    postHeight()
  }

  const cellClass = (entry, name) => {
    const state = (entry.s || {})[name]
    if (!state) return 'na'
    if (state !== 'online') return 'down'
    return (entry.b || {})[name] ? 'busy' : 'ok'
  }

  const cellTitle = (entry, name) => {
    const cls = cellClass(entry, name)
    const text =
      cls === 'down' ? '离线' : cls === 'busy' ? '在线且忙碌' : cls === 'ok' ? '在线' : '无数据'
    const job = (entry.j || {})[name]
    return `${fmt(entry.at)} · ${name} · ${text}${job ? ` · 任务 ${job}` : ''}`
  }

  const renderTimeline = (runners, history) => {
    const names = [
      ...new Set([
        ...runners.map((r) => r.name),
        ...history.flatMap((h) => Object.keys(h.s || {})),
      ]),
    ].sort()
    if (!names.length || !history.length) {
      $('#timeline').innerHTML = '<p class="muted">还没有历史采样，时间线将在工作流跑几轮后出现。</p>'
      $('#tl-note').textContent = ''
      postHeight()
      return
    }
    const recent = history.slice(-HISTORY_WINDOW)
    const rows = names
      .map((name) => {
        const cells = recent
          .map((entry) => `<span class="cell ${cellClass(entry, name)}" title="${esc(cellTitle(entry, name))}"></span>`)
          .join('')
        return `<div class="tl-row"><span class="tl-name" title="${esc(name)}">${esc(name)}</span><span class="tl-cells">${cells}</span></div>`
      })
      .join('')
    $('#timeline').innerHTML = rows
    $('#tl-note').textContent = `最近 ${recent.length} 个采样点（每 15 分钟一个，窗口最长 24 小时），左旧右新，悬停查看时间与任务。`
    postHeight()
  }

  const renderWarn = (updatedAt) => {
    const messages = []
    if (!updatedAt) {
      messages.push('数据缺少时间戳。')
    } else {
      const age = Date.now() - new Date(updatedAt).getTime()
      if (Number.isFinite(age) && age > STALE_AFTER_MS) {
        messages.push(`数据已 ${Math.round(age / 60000)} 分钟未更新，采集工作流可能已停止。`)
      }
    }
    const el = $('#warn')
    el.hidden = !messages.length
    el.textContent = messages.join(' ')
  }

  // --- machine network speed cards (pushed by each runner machine) ---

  const spark = (minutes) => {
    if (!Array.isArray(minutes) || minutes.length < 2) {
      return '<span class="muted small">暂无趋势</span>'
    }
    const max = Math.max(...minutes.map((m) => Number(m.down) || 0), 1)
    const bars = minutes
      .map((m, i) => {
        const h = Math.max(1.5, ((Number(m.down) || 0) / max) * 36)
        const t = `${esc(m.t)} · 下行 ${bps(m.down)} · workers ${esc(m.workers ?? '-')}`
        return `<rect x="${i * 5}" y="${(40 - h).toFixed(1)}" width="4" height="${h.toFixed(1)}" fill="var(--accent)" opacity="${m.workers ? 1 : 0.35}"><title>${t}</title></rect>`
      })
      .join('')
    return `<svg class="spark" viewBox="0 0 ${minutes.length * 5} 40" height="40" role="img" aria-label="最近一小时每分钟下行速度">${bars}</svg>`
  }

  const renderNetmon = (machine, data) => {
    const container = $('#netmon')
    let el = container.querySelector(`[data-machine="${machine}"]`)
    if (!el) {
      el = document.createElement('article')
      el.className = 'net-card'
      el.dataset.machine = machine
      container.appendChild(el)
    }
    if (!data) {
      el.innerHTML = `<h3>${esc(machine)}</h3><p class="meta">暂无数据，等待机器推送。</p>`
      postHeight()
      return
    }
    // Distinguish "idle, nothing to download" from "downloading slowly":
    // the 5-minute average only counts when a worker is actually active.
    const active = (data.minutes || []).filter((m) => (Number(m.down) || 0) > 51200)
    const lastActive = active[active.length - 1]
    const busy = (Number(data.downAvg5m) || 0) > 51200 || (Number(data.workersNow) || 0) > 0
    const speed = busy
      ? `近 5 分钟：下行 ${bps(data.downAvg5m)}（峰值 ${bps(data.downPeak5m)}）· 上行 ${bps(data.upAvg5m)}`
      : '近 5 分钟：无下载活动（runner 空闲）'
    const tail = lastActive
      ? `最近下载活动 ${esc(lastActive.t)} · 下行 ${bps(lastActive.down)}`
      : '过去一小时无下载活动'
    el.innerHTML = `
      <h3>${esc(machine)} <span class="muted small">${esc(data.host || '')}</span></h3>
      <p class="meta">${speed}</p>
      <p class="meta">当前 worker ${esc(data.workersNow ?? '-')} 个 · 连接本地代理 ${esc(data.proxyConns ?? '-')} 条</p>
      <div class="spark-wrap">${spark(data.minutes)}</div>
      <p class="meta small">${tail} · 机器每 15 分钟推送 · 更新于 ${ago(data.updatedAt)}</p>`
    postHeight()
  }

  const renderAll = (data) => {
    renderCards(data.runners)
    renderQueued(data.queued)
    renderTimeline(data.runners, data.history)
    $('#updated').textContent = fmt(data.updatedAt)
    if (data.updatedAt) $('#updated').dateTime = data.updatedAt
    renderWarn(data.updatedAt)
  }

  const updateRunners = async () => {
    let data = null
    try {
      data = normalize(await loadDeployed(DATA_URLS))
    } catch (err) {
      const el = $('#error')
      el.hidden = false
      el.textContent = `加载失败：${err.message}`
      postHeight()
    }
    if (data && newer('runners', data.updatedAt)) {
      renderAll(data)
      $('#error').hidden = true
    }
    // Fresh upgrade from the data branch (best-effort, never blocks).
    try {
      const raw = normalize(await fetchJSON(RAW_BASE + 'runners.json'))
      if (newer('runners', raw.updatedAt)) {
        renderAll(raw)
        $('#error').hidden = true
      }
    } catch {
      // raw.githubusercontent unreachable here; the deployed copy stays.
    }
  }

  const updateNetmon = async (machine) => {
    const deployed = [`../data/netmon/${machine}.json`, `data/netmon/${machine}.json`]
    try {
      const data = await loadDeployed(deployed)
      if (newer(machine, data.updatedAt)) renderNetmon(machine, data)
    } catch {
      // not deployed yet; try raw
    }
    try {
      const raw = await fetchJSON(`${RAW_BASE}netmon/${machine}.json`)
      if (newer(machine, raw.updatedAt)) renderNetmon(machine, raw)
    } catch {
      // no data from either source
    }
    if (!seen[machine]) renderNetmon(machine, null)
  }

  const tick = async () => {
    await updateRunners()
    await Promise.all(MACHINES.map((m) => updateNetmon(m)))
    postHeight()
  }

  tick()
  setInterval(tick, REFRESH_MS)
})()
