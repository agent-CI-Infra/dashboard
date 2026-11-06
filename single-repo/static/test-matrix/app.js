;(() => {
  'use strict'

  const REPO = 'https://github.com/openJiuwen-ai/DigitalMate'
  // Inside a board the data is one level up; a standalone copy keeps data/ next to the page.
  const DATA_URLS = ['../data/test-matrix.json', 'data/test-matrix.json']
  const STATUS_ORDER = ['stable_pass', 'pass_recorded', 'pass_recheck_blocked', 'fail', 'flaky', 'blocked', 'not_run', 'no_result', 'ci_lane']
  const STATUS_COLOR = {
    stable_pass: 'var(--s-stable)',
    pass_recorded: 'var(--s-pass)',
    pass_recheck_blocked: 'var(--s-recheck)',
    fail: 'var(--s-fail)',
    flaky: 'var(--s-flaky)',
    blocked: 'var(--s-blocked)',
    not_run: 'var(--s-notrun)',
    no_result: 'var(--s-none)',
    ci_lane: 'var(--s-ci)',
  }
  const LIGHT_CELLS = new Set(['pass_recorded', 'not_run', 'no_result'])
  const LAYERS = [
    ['UT', '单元测试（Node，读 ArkTS 源码）'],
    ['UT-Server', '服务端单测（personal-agent，pytest）'],
    ['REG', '协议回归（case-catalog）'],
    ['ST', '业务系统测试（Hypium + 真实云端）'],
    ['E2E', '模拟器/真机 workflow'],
    ['EVAL', 'Agent eval task'],
  ]
  const SUITE_LABEL = { default: '默认套件', explicit: '显式选择', quarantined: '隔离（默认不跑）' }
  const PAGE = 200

  const $ = (s) => document.querySelector(s)
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
  const state = { data: null, filters: { layer: 'core', status: '', suite: '', impl: '', dep: '', q: '', plan: '' }, limit: PAGE }

  async function load() {
    for (const url of DATA_URLS) {
      try {
        const res = await fetch(url, { cache: 'no-store' })
        if (res.ok) return res.json()
      } catch {
        // Try the next layout.
      }
    }
    throw new Error('没有找到 data/test-matrix.json。运行 “Collect DigitalMate test matrix” 工作流生成数据后再刷新。')
  }

  const statusLabel = (s) => state.data.statusLabels[s] || s
  const depLabel = (d) => state.data.dependencyLabels[d] || d
  const statusChip = (s) => `<span class="status"><i class="dot" style="background:${STATUS_COLOR[s] || 'var(--s-none)'}"></i>${esc(statusLabel(s))}</span>`
  const refFor = (impl) => (impl === 'main' || impl === 'spec-only' || impl === 'missing' ? 'main' : impl)
  const fileLink = (c) => (c.file ? `<a class="mono path" href="${REPO}/blob/${encodeURI(refFor(c.impl))}/${encodeURI(c.file)}" target="_blank" rel="noopener">${esc(c.file)}</a>` : '')

  function renderLedger() {
    const { data } = state
    const planned = data.cases.filter((c) => c.planId && (c.layer === 'ST' || c.layer === 'E2E'))
    $('#planned-total').textContent = planned.length
    const ledger = data.ledger ? `状态取自 <a href="${esc(data.ledger.url)}" target="_blank" rel="noopener">#${esc(String(data.ledger.url).split('/').pop())}</a>（更新于 ${esc(fmt(data.ledger.updatedAt))}）；` : ''
    $('#source-line').innerHTML = `${ledger}源码 main <span class="mono">${esc(data.source.main)}</span>，矩阵生成于 ${esc(fmt(data.generatedAt))}`
    $('#strip').innerHTML = ['ST', 'E2E']
      .map((layer) => {
        const cells = planned
          .filter((c) => c.layer === layer)
          .sort((a, b) => a.planId.localeCompare(b.planId))
          .map((c) => `<button type="button" class="cell" data-plan="${esc(c.planId)}" ${LIGHT_CELLS.has(c.status) ? 'data-light' : ''} style="background:${STATUS_COLOR[c.status]}" aria-pressed="${state.filters.plan === c.planId}" title="${esc(`${c.planId} · ${statusLabel(c.status)}\n${c.title}`)}">${esc(c.planId)}</button>`)
          .join('')
        return `<div class="strip-group"><span>${layer}</span><div class="cells">${cells}</div></div>`
      })
      .join('')
    const counts = data.summary.planned.byStatus
    $('#legend').innerHTML = STATUS_ORDER.filter((s) => counts[s])
      .map((s) => `<button type="button" data-status="${s}" aria-pressed="${state.filters.status === s && state.filters.layer === 'planned'}"><i class="dot" style="background:${STATUS_COLOR[s]}"></i>${esc(statusLabel(s))} ${counts[s]}</button>`)
      .join('')
  }

  function renderLayers() {
    const { layers } = state.data.summary
    const rows = LAYERS.filter(([k]) => layers[k]).map(([k, label]) => {
      const l = layers[k]
      const bar = STATUS_ORDER.filter((s) => l.byStatus[s])
        .map((s) => `<span style="width:${(100 * l.byStatus[s]) / l.cases}%;background:${STATUS_COLOR[s]}" title="${esc(statusLabel(s))} ${l.byStatus[s]}"></span>`)
        .join('')
      const parts = STATUS_ORDER.filter((s) => l.byStatus[s]).map((s) => `${statusLabel(s)} ${l.byStatus[s]}`).join('，')
      const impl = Object.entries(l.byImpl).map(([i, n]) => `${i === 'main' ? 'main' : i} ${n}`).join('，')
      return `<tr><td><strong>${k}</strong><br><span class="muted small">${esc(label)}</span></td><td class="num">${l.cases}</td><td class="num">${l.tests || '—'}</td><td><div class="bar" role="img" aria-label="${esc(parts)}">${bar}</div><span class="muted small">${esc(parts)}</span></td><td class="small">${esc(impl)}</td></tr>`
    })
    const dfx = state.data.dfx || []
    rows.push(`<tr><td><strong>DFX</strong><br><span class="muted small">性能 / 可靠性 / 稳定性</span></td><td class="num">${dfx.reduce((a, d) => a + d.files, 0)}</td><td class="num">—</td><td class="small muted">${esc(dfx.map((d) => `${d.name} ${d.files} 个文件`).join('，'))}（main 上只有 README）</td><td></td></tr>`)
    $('#layers').innerHTML = `<thead><tr><th>层级</th><th class="num">用例/文件</th><th class="num">测试函数</th><th>状态分布</th><th>实现位置</th></tr></thead><tbody>${rows.join('')}</tbody>`
    const jobs = state.data.nightly?.jobs
    $('#lane-note').innerHTML = jobs
      ? `最近一次定时 nightly（${esc(fmt(state.data.nightly.createdAt))}${state.data.nightly.url ? `，<a href="${esc(state.data.nightly.url)}" target="_blank" rel="noopener">运行记录</a>` : ''}）：${Object.entries(jobs).map(([n, c]) => `${esc(n)} <strong>${esc(c)}</strong>`).join('；')}。“无当前结果”表示该用例不在 #533 台账内，且最近的 nightly 没有产出它的结果。`
      : ''
  }

  function renderDeps() {
    const deps = Object.entries(state.data.summary.dependencies).sort((a, b) => b[1].length - a[1].length)
    $('#deps').innerHTML = deps.length
      ? deps.map(([k, ids]) => `<button type="button" class="dep" data-dep="${esc(k)}" aria-pressed="${state.filters.dep === k}"><strong>${esc(depLabel(k))}<em>${ids.length} 条</em></strong><p>${esc(ids.join('  '))}</p></button>`).join('')
      : '<p class="muted">没有记录依赖的未执行用例。</p>'
  }

  function options(sel, values, current, all = '全部') {
    $(sel).innerHTML = [`<option value="">${all}</option>`, ...values.map(([v, l]) => `<option value="${esc(v)}" ${v === current ? 'selected' : ''}>${esc(l)}</option>`)].join('')
  }

  function renderFilters() {
    const { cases } = state.data
    const f = state.filters
    $('#f-layer').innerHTML = [
      ['core', '不含单元测试'],
      ['planned', '计划用例（#533）'],
      ['all', '全部层级'],
      ...LAYERS.map(([k]) => [k, k]),
    ]
      .map(([v, l]) => `<option value="${v}" ${v === f.layer ? 'selected' : ''}>${esc(l)}</option>`)
      .join('')
    options('#f-status', STATUS_ORDER.filter((s) => cases.some((c) => c.status === s)).map((s) => [s, statusLabel(s)]), f.status)
    options('#f-suite', [...new Set(cases.map((c) => c.suite).filter(Boolean))].map((s) => [s, SUITE_LABEL[s] || s]), f.suite)
    options('#f-impl', [...new Set(cases.map((c) => c.impl))].map((s) => [s, s]), f.impl)
    options('#f-dep', Object.keys(state.data.summary.dependencies).map((d) => [d, depLabel(d)]), f.dep)
    $('#f-q').value = f.q
  }

  function matches(c) {
    const f = state.filters
    if (f.plan) return c.planId === f.plan
    if (f.layer === 'core' && (c.layer === 'UT' || c.layer === 'UT-Server')) return false
    if (f.layer === 'planned' && !(c.planId && (c.layer === 'ST' || c.layer === 'E2E'))) return false
    if (!['core', 'planned', 'all'].includes(f.layer) && c.layer !== f.layer) return false
    if (f.status && c.status !== f.status) return false
    if (f.suite && c.suite !== f.suite) return false
    if (f.impl && c.impl !== f.impl) return false
    if (f.dep && !(c.dependencyTags || []).includes(f.dep)) return false
    if (f.q) {
      const hay = [c.id, c.planId, c.title, c.file, c.owner, c.module, c.progress, c.dependency, c.related].join(' ').toLowerCase()
      if (!f.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false
    }
    return true
  }

  function links(c) {
    const out = []
    for (const pr of c.prs || []) out.push(`<a href="${REPO}/pull/${esc(pr.slice(1))}" target="_blank" rel="noopener">${esc(pr)}</a>`)
    if (c.evidence) out.push(`<a href="${esc(c.evidence)}" target="_blank" rel="noopener">证据</a>`)
    if (c.issue) out.push(`<a href="${esc(c.issue)}" target="_blank" rel="noopener">来源 issue</a>`)
    return out.join(' ')
  }

  function detail(c) {
    const bits = []
    if (c.layer === 'E2E' && c.steps != null) bits.push(`${c.steps} 步，${c.scoreCriteria} 个评分项`)
    if (c.layer === 'EVAL') bits.push(`${c.adapter || '—'} 适配器，${c.trials ?? '—'} 次 trial，${c.graders} 个 grader${c.related ? `，对应 ${c.related}` : ''}`)
    if (typeof c.tests === 'number' && (c.layer === 'UT' || c.layer === 'UT-Server')) bits.push(`${c.tests} 个测试`)
    if (c.owner) bits.push(`owner ${c.owner}`)
    if (c.note) bits.push(c.note)
    return bits.length ? `<br><span class="muted small">${esc(bits.join('；'))}</span>` : ''
  }

  const LAYER_RANK = { ST: 0, E2E: 1, EVAL: 2, REG: 3, UT: 4, 'UT-Server': 5 }
  // Planned cases first, then by plan/case id inside each layer.
  const byLayer = (a, b) => (LAYER_RANK[a.layer] ?? 9) - (LAYER_RANK[b.layer] ?? 9) || (a.planId ? 0 : 1) - (b.planId ? 0 : 1) || String(a.planId || a.id).localeCompare(String(b.planId || b.id))

  function renderTable() {
    const rows = state.data.cases.filter(matches).sort(byLayer)
    const shown = rows.slice(0, state.limit)
    $('#count').textContent = `共 ${rows.length} 行${rows.length > shown.length ? `，显示前 ${shown.length} 行` : ''}`
    const body = shown
      .map(
        (c) => `<tr>
      <td>${esc(c.layer)}</td>
      <td class="mono">${esc(c.planId || '—')}<br><span class="path">${esc(c.planId === c.id ? '' : c.id)}</span></td>
      <td>${esc(c.title)}${detail(c)}<br>${fileLink(c)}</td>
      <td class="small">${esc(SUITE_LABEL[c.suite] || c.suite || '—')}<br><span class="muted">${esc(c.impl)}</span></td>
      <td>${esc(c.priority || '—')}</td>
      <td>${statusChip(c.status)}${c.progress && c.status !== 'not_run' && c.progress !== statusLabel(c.status) ? `<br><span class="muted small">${esc(c.progress)}</span>` : ''}</td>
      <td>${(c.dependencyTags || []).map((d) => `<span class="tag">${esc(depLabel(d))}</span>`).join('')}${c.dependency ? `<br><span class="muted small">${esc(c.dependency)}</span>` : ''}</td>
      <td class="small">${links(c)}</td>
    </tr>`
      )
      .join('')
    $('#cases').innerHTML = `<thead><tr><th>层级</th><th>编号</th><th>内容</th><th>套件 / 实现</th><th>优先级</th><th>状态</th><th>前置依赖</th><th>PR / 证据</th></tr></thead><tbody>${body || '<tr><td colspan="8" class="muted">没有符合筛选的用例。清除筛选后重试。</td></tr>'}</tbody>`
    const old = document.querySelector('.more')
    old?.remove()
    if (rows.length > shown.length) {
      $('#cases').closest('.scroll').insertAdjacentHTML('afterend', `<button type="button" class="btn more">显示剩余 ${rows.length - shown.length} 行</button>`)
      document.querySelector('.more').addEventListener('click', () => { state.limit = Infinity; renderTable() })
    }
    reportHeight()
  }

  function rerender() {
    state.limit = PAGE
    renderLedger()
    renderDeps()
    renderFilters()
    renderTable()
  }

  function fmt(iso) {
    if (!iso) return '—'
    const d = new Date(iso)
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
  }

  function reportHeight() {
    if (window.parent !== window) window.parent.postMessage({ type: 'test-matrix-height', height: document.documentElement.scrollHeight }, location.origin)
  }

  function bind() {
    const f = state.filters
    const set = (patch) => { Object.assign(f, patch); rerender() }
    $('#f-layer').addEventListener('change', (e) => set({ layer: e.target.value, plan: '' }))
    $('#f-status').addEventListener('change', (e) => set({ status: e.target.value, plan: '' }))
    $('#f-suite').addEventListener('change', (e) => set({ suite: e.target.value, plan: '' }))
    $('#f-impl').addEventListener('change', (e) => set({ impl: e.target.value, plan: '' }))
    $('#f-dep').addEventListener('change', (e) => set({ dep: e.target.value, plan: '' }))
    $('#f-q').addEventListener('input', (e) => { f.q = e.target.value; f.plan = ''; state.limit = PAGE; renderTable() })
    $('#f-reset').addEventListener('click', () => set({ layer: 'core', status: '', suite: '', impl: '', dep: '', q: '', plan: '' }))
    $('#strip').addEventListener('click', (e) => {
      const b = e.target.closest('[data-plan]')
      if (b) set({ plan: f.plan === b.dataset.plan ? '' : b.dataset.plan })
    })
    $('#legend').addEventListener('click', (e) => {
      const b = e.target.closest('[data-status]')
      if (!b) return
      const on = f.layer === 'planned' && f.status === b.dataset.status
      set({ layer: on ? 'core' : 'planned', status: on ? '' : b.dataset.status, plan: '', dep: '' })
    })
    $('#deps').addEventListener('click', (e) => {
      const b = e.target.closest('[data-dep]')
      if (b) set({ dep: f.dep === b.dataset.dep ? '' : b.dataset.dep, layer: 'core', status: '', plan: '' })
    })
    window.addEventListener('resize', reportHeight)
  }

  load()
    .then((data) => {
      if (data?.schema !== 'digitalmate/test-matrix@1') throw new Error(`不支持的数据格式：${data?.schema ?? '缺少 schema'}`)
      state.data = data
      renderLayers()
      bind()
      rerender()
    })
    .catch((err) => {
      const box = $('#error')
      box.hidden = false
      box.textContent = err.message
      reportHeight()
    })
})()
