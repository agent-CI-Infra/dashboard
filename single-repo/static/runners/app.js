;(() => {
  'use strict'

  // Inside a board the data is one level up; a standalone copy keeps data/ next to the page.
  const DATA_URLS = ['../data/runners.json', 'data/runners.json']
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

  async function load() {
    for (const url of DATA_URLS) {
      try {
        const res = await fetch(url, { cache: 'no-store' })
        if (res.ok) return await res.json()
      } catch {
        // Try the next layout.
      }
    }
    throw new Error('没有找到 data/runners.json。由 Collect DigitalMate runners 工作流生成。')
  }

  // The collector writes { updatedAt, runners: [...], history: [{ at, s, b }] }.
  const normalize = (raw) => {
    const data = raw && typeof raw === 'object' ? raw : {}
    return {
      updatedAt: data.updatedAt || null,
      runners: Array.isArray(data.runners) ? data.runners : [],
      history: Array.isArray(data.history) ? data.history : [],
    }
  }

  const chip = (cls, text, title) =>
    `<span class="chip ${cls}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`

  const statusChips = (runner) => {
    if (runner.status === 'online') {
      return (
        chip('ok', '在线') +
        (runner.busy ? chip('busy', '忙碌') : chip('idle', '空闲'))
      )
    }
    if (runner.status === 'offline') return chip('down', '离线')
    return chip('na', runner.status || '未知')
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
      const extra = [
        r.id != null ? `GitHub runner id ${esc(r.id)}` : null,
        r.os ? esc(r.os) : null,
      ]
        .filter(Boolean)
        .join(' · ')
      return `<article class="run-card${r.status === 'offline' ? ' down' : ''}">
        <div class="run-head"><span class="run-name" title="${esc(r.name)}">${esc(r.name)}</span>${statusChips(r)}</div>
        ${meta ? `<p class="meta">${esc(meta)}</p>` : ''}
        ${extra ? `<p class="meta">${esc(extra)}</p>` : ''}
        ${labels ? `<div class="labels">${labels}</div>` : ''}
      </article>`
    })
    $('#cards').innerHTML =
      cards.join('') || '<p class="muted">还没有 runner 数据。</p>'
    $('#count').textContent = runners.length ? `（${runners.length} 台）` : ''
  }

  const cellClass = (entry, name) => {
    const state = (entry.s || {})[name]
    if (!state) return 'na'
    if (state !== 'online') return 'down'
    return (entry.b || {})[name] ? 'busy' : 'ok'
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
      return
    }
    const recent = history.slice(-HISTORY_WINDOW)
    const rows = names
      .map((name) => {
        const cells = recent
          .map((entry) => {
            const cls = cellClass(entry, name)
            const text =
              cls === 'down'
                ? '离线'
                : cls === 'busy'
                  ? '在线且忙碌'
                  : cls === 'ok'
                    ? '在线'
                    : '无数据'
            return `<span class="cell ${cls}" title="${esc(`${fmt(entry.at)} · ${name} · ${text}`)}"></span>`
          })
          .join('')
        return `<div class="tl-row"><span class="tl-name" title="${esc(name)}">${esc(name)}</span><span class="tl-cells">${cells}</span></div>`
      })
      .join('')
    $('#timeline').innerHTML = rows
    $('#tl-note').textContent = `最近 ${recent.length} 个采样点（每 15 分钟一个，窗口最长 24 小时），左旧右新，悬停查看时间。`
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

  const render = async () => {
    try {
      const data = normalize(await load())
      renderCards(data.runners)
      renderTimeline(data.runners, data.history)
      $('#updated').textContent = fmt(data.updatedAt)
      if (data.updatedAt) $('#updated').dateTime = data.updatedAt
      renderWarn(data.updatedAt)
      $('#error').hidden = true
    } catch (err) {
      $('#error').hidden = false
      $('#error').textContent = `加载失败：${err.message}`
    }
    if (window.parent !== window) {
      window.parent.postMessage(
        { type: 'runners-height', height: document.documentElement.scrollHeight },
        location.origin
      )
    }
  }

  render()
  setInterval(render, REFRESH_MS)
})()
