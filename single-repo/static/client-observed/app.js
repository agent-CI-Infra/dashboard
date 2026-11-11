(() => {
  'use strict';

  // The client-observed snapshot is produced by scripts/collect-client-observed-metrics.py
  // from each deployment's aggregate. This view never reads production data and never
  // asks for message content: every value below is a number, a model name or a deployment
  // label the collector supplied.
  const SCHEMA = 'client-observed-metrics@1';
  const SCOPE = 'client-observed';
  const DATA_URL = '../data/client-observed-metrics.json';
  const SEGMENTS = [
    ['tap_to_queued', '点击 → 入队'],
    ['queued_to_dequeued', '排队等待'],
    ['dequeued_to_wire_send', '本地编码与发送'],
    ['wire_send_to_ack', '发送返回 → 确认'],
    ['ack_to_first_text', '首字等待'],
    ['first_text_to_last_text', '流式生成'],
    ['last_text_to_task_done', '任务收尾'],
    ['task_done_to_display_done', '渲染完成'],
  ];

  // The collector records a short reason code; the board never renders the raw diagnostic.
  const REASONS = {
    unreachable: '连接失败',
    unauthorized: '凭据被拒绝或未启用',
    'not-configured': '服务未开启端侧接收',
    invalid: '数据格式不符',
  };

  const state = { doc: null, environment: 'all' };

  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));

  const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
  const num = (value) => (isNumber(value) ? value.toLocaleString('zh-CN') : '—');
  const ms = (value) => {
    if (!isNumber(value)) return '—';
    if (value >= 1000) return `${(value / 1000).toFixed(2)} s`;
    return `${Math.round(value)} ms`;
  };
  const pct = (part, whole) => (isNumber(part) && isNumber(whole) && whole > 0
    ? `${((part / whole) * 100).toFixed(1)}%` : '—');
  const block = (value) => (value && typeof value === 'object' ? value : { count: 0, avg_ms: 0 });
  const sampleMs = (value, field = 'avg_ms') => value && value.count > 0 ? ms(value[field]) : '—';
  const modelCalls = (models) => (Array.isArray(models)
    ? models.reduce((total, model) => total + (isNumber(model?.calls) ? model.calls : 0), 0)
    : 0);

  function recordFor(name) {
    if (!state.doc || name === 'all') return null;
    return (state.doc.environments || []).find((record) => record.environment === name) || null;
  }

  function statCard(label, value, hint, extraClass = '') {
    return `<article class="stat-card">
      <div class="stat-label">${escapeHtml(label)}</div>
      <div class="stat-value${extraClass ? ` ${extraClass}` : ''}">${escapeHtml(value)}</div>
      <div class="stat-hint">${escapeHtml(hint)}</div>
    </article>`;
  }

  function renderSummary() {
    const doc = state.doc;
    if (!doc) {
      $('summary').innerHTML = '<p class="empty wide">尚未采集到端侧数据。采集器配置 CLIENT_OBSERVED_DEPLOYMENTS 并运行后，这里会显示端侧上报的分段与模型指标。</p>';
      return;
    }
    const totals = doc.totals || {};
    const turns = totals.turns || {};
    const outcomes = totals.outcomes || {};
    const unavailable = Array.isArray(doc.unavailable) ? doc.unavailable.length : 0;
    const failures = (outcomes.failed || 0) + (outcomes.error || 0);
    const models = Array.isArray(totals.models) ? totals.models.length : 0;
    $('summary').innerHTML = [
      statCard('端侧轮次', num(turns.total), `窗口 ${num(doc.window_days)} 天`),
      statCard('完成率', pct(turns.completed, turns.total), `完成 ${num(turns.completed)} · 失败 ${num(failures)}`),
      statCard('客户端首文本均值', sampleMs(block(totals.client_ttft)), `样本 ${num(block(totals.client_ttft).count)} · 覆盖 ${pct(block(totals.client_ttft).count, turns.total)}`),
      statCard('端到端均值', sampleMs(block(totals.end_to_end)), `样本 ${num(block(totals.end_to_end).count)} · 覆盖 ${pct(block(totals.end_to_end).count, turns.total)}`),
      statCard('模型调用', num(modelCalls(totals.models)), `${num(models)} 个模型`),
      statCard('环境', num(totals.environment_count), unavailable ? `${num(unavailable)} 个不可用` : '全部可用'),
    ].join('');
  }

  function segmentRows(segments) {
    const values = SEGMENTS.map(([key]) => block(segments?.[key]).avg_ms || 0);
    const max = Math.max(0, ...values);
    return SEGMENTS.map(([key, label], index) => {
      const entry = block(segments?.[key]);
      const value = entry.avg_ms || 0;
      const width = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
      const tip = `${label}：平均 ${sampleMs(entry)}`
        + `${entry.count > 0 && isNumber(entry.p95_ms) ? ` · P95 ${ms(entry.p95_ms)}` : ''}`
        + ` · 样本 ${num(entry.count)}`;
      return `<div class="segment-row${entry.count ? '' : ' is-empty'}" tabindex="0"
        data-tip="${escapeHtml(tip)}" aria-label="${escapeHtml(tip)}">
        <span class="segment-label">${escapeHtml(label)}</span>
        <span class="segment-track"><span class="segment-bar" style="width:${width.toFixed(1)}%"></span></span>
        <span class="segment-value">${sampleMs(entry)}</span>
      </div>`;
    }).join('');
  }

  function renderSegments() {
    const record = recordFor(state.environment);
    const source = record ? record.segments : state.doc?.totals?.segments;
    $('segment-bars').innerHTML = source
      ? `<div class="segment-bars">${segmentRows(source)}</div>`
      : '<p class="empty">暂无分段数据</p>';
  }

  function renderEnvironments() {
    const doc = state.doc;
    if (!doc) {
      $('environment-table').innerHTML = '<p class="empty">暂无数据</p>';
      $('environment-count').textContent = '';
      return;
    }
    const records = Array.isArray(doc.environments) ? doc.environments : [];
    const unavailable = Array.isArray(doc.unavailable) ? doc.unavailable : [];
    $('environment-count').textContent = `${records.length} 个环境有数据`;
    if (!records.length) {
      $('environment-table').innerHTML = '<p class="empty">暂无可用环境</p>';
    } else {
      const rows = records.map((record) => {
        const turns = record.turns || {};
        const ttft = block(record.client_ttft);
        const total = block(record.end_to_end);
        return `<tr>
          <td class="name">${escapeHtml(record.environment)}${record.stale
            ? ' <span class="badge stale" title="本轮采集失败，展示上一次成功的数据">历史快照</span>' : ''}</td>
          <td class="num">${num(turns.total)}</td>
          <td class="num">${pct(turns.completed, turns.total)}</td>
          <td class="num">${sampleMs(ttft)}</td>
          <td class="num">${sampleMs(ttft, 'p95_ms')}</td>
          <td class="num">${sampleMs(total)}</td>
          <td class="num">${sampleMs(total, 'p95_ms')}</td>
          <td class="num">${num(modelCalls(record.models))}</td>
        </tr>`;
      }).join('');
      $('environment-table').innerHTML = `<table class="data-table">
        <thead><tr>
          <th>环境</th><th>轮次</th><th>完成率</th>
          <th>TTFT 均值</th><th>TTFT P95</th>
          <th>端到端均值</th><th>端到端 P95</th><th>模型调用</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    }
    if (unavailable.length) {
      const names = unavailable
        .map((item) => `${item?.environment}（${REASONS[item?.reason] || '不可用'}）`)
        .map(escapeHtml)
        .join('、');
      $('environment-table').insertAdjacentHTML('beforeend', `<p class="unavailable">未采集到：${names}</p>`);
    }
  }

  function renderModels() {
    const record = recordFor(state.environment);
    const models = record ? record.models : state.doc?.totals?.models;
    $('model-scope').textContent = state.environment === 'all'
      ? '全部环境 · 按调用数加权'
      : `${state.environment} · 单环境`;
    if (!Array.isArray(models) || !models.length) {
      $('model-table').innerHTML = '<p class="empty">暂无模型调用记录</p>';
      return;
    }
    const rows = models.map((model) => {
      const ttft = block(model.ttft_ms);
      const tpot = block(model.tpot_ms);
      const statuses = model.statuses && typeof model.statuses === 'object'
        ? Object.entries(model.statuses).map(([name, count]) => `${name} ${num(count)}`).join(' · ')
        : '';
      return `<tr>
        <td class="name"${statuses ? ` data-tip="${escapeHtml(statuses)}"` : ''}>${escapeHtml(model.model)}</td>
        <td class="num">${num(model.calls)}</td>
        <td class="num">${pct(model.succeeded, model.calls)}</td>
        <td class="num">${sampleMs(ttft)}</td>
        <td class="num">${sampleMs(ttft, 'p95_ms')}</td>
        <td class="num">${sampleMs(tpot)}</td>
        <td class="num">${num(model.input_tokens)}</td>
        <td class="num">${num(model.output_tokens)}</td>
      </tr>`;
    }).join('');
    $('model-table').innerHTML = `<table class="data-table">
      <thead><tr>
        <th>模型</th><th>调用</th><th>成功率</th>
        <th>TTFT 均值</th><th>TTFT P95</th><th>TPOT 均值</th>
        <th>输入 Token</th><th>输出 Token</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  function renderScopeHint() {
    const record = recordFor(state.environment);
    const totals = state.doc?.totals || {};
    $('scope-hint').textContent = record
      ? `${record.environment} · 轮次 ${num(record.turns?.total)} · 窗口 ${num(record.window_days)} 天`
      : `全部环境 · 轮次 ${num(totals.turns?.total)} · 窗口 ${num(state.doc?.window_days)} 天`;
  }

  function render() {
    renderScopeHint();
    renderSummary();
    renderSegments();
    renderEnvironments();
    renderModels();
    reportHeight();
  }

  function renderEnvironmentPicker() {
    const select = $('environment-select');
    const records = Array.isArray(state.doc?.environments) ? state.doc.environments : [];
    const options = ['<option value="all">全部环境</option>']
      .concat(records.map((record) => `<option value="${escapeHtml(record.environment)}">${escapeHtml(record.environment)}</option>`));
    select.innerHTML = options.join('');
    if (state.environment !== 'all' && !records.some((record) => record.environment === state.environment)) {
      state.environment = 'all';
    }
    select.value = state.environment;
  }

  function showError(message) {
    const banner = $('error');
    banner.hidden = !message;
    banner.textContent = message || '';
  }

  function validDocument(doc) {
    return !!doc && typeof doc === 'object' && doc.schema === SCHEMA && doc.scope === SCOPE
      && Array.isArray(doc.environments) && !!doc.totals && typeof doc.totals === 'object';
  }

  async function load() {
    try {
      const response = await fetch(DATA_URL, { cache: 'no-store' });
      if (response.status === 404) {
        state.doc = null;
        showError('');
        renderEnvironmentPicker();
        render();
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const document_ = await response.json();
      if (!validDocument(document_)) throw new Error('端侧指标快照契约不匹配');
      state.doc = document_;
      showError('');
      $('generated').textContent = document_.generated_at ? `采集于 ${document_.generated_at}` : '';
      $('window').textContent = `窗口 ${num(document_.window_days)} 天`;
    } catch (error) {
      state.doc = null;
      showError(`端侧指标暂不可用：${error instanceof Error ? error.message : String(error)}`);
    }
    renderEnvironmentPicker();
    render();
  }

  // --- hover tooltip -------------------------------------------------------
  const tooltip = $('chart-tooltip');
  const hideTooltip = () => { tooltip.hidden = true; };
  function showTooltip(text) {
    tooltip.textContent = text;
    tooltip.hidden = false;
  }
  document.addEventListener('pointerover', (event) => {
    const target = event.target.closest('[data-tip]');
    if (target) showTooltip(target.dataset.tip);
    else hideTooltip();
  });
  document.addEventListener('pointermove', (event) => {
    if (tooltip.hidden) return;
    const x = Math.min(event.clientX + 14, window.innerWidth - tooltip.offsetWidth - 8);
    const y = Math.min(event.clientY + 14, window.innerHeight - tooltip.offsetHeight - 8);
    tooltip.style.left = `${Math.max(8, x)}px`;
    tooltip.style.top = `${Math.max(8, y)}px`;
  });
  document.addEventListener('pointerleave', hideTooltip);
  document.addEventListener('focusin', (event) => {
    const target = event.target.closest('[data-tip]');
    if (!target) { hideTooltip(); return; }
    showTooltip(target.dataset.tip);
    const box = target.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - tooltip.offsetWidth - 8))}px`;
    tooltip.style.top = `${Math.min(box.bottom + 6, window.innerHeight - tooltip.offsetHeight - 8)}px`;
  });
  document.addEventListener('focusout', hideTooltip);

  $('environment-select').addEventListener('change', (event) => {
    state.environment = event.target.value;
    render();
  });

  // Keep the embedded view as tall as its content.
  function reportHeight() {
    if (window.parent === window) return;
    window.parent.postMessage(
      { type: 'client-observed-height', height: document.documentElement.scrollHeight },
      location.origin,
    );
  }
  if (window.parent !== window) {
    new ResizeObserver(reportHeight).observe(document.body);
    window.addEventListener('load', reportHeight);
  }

  load();
})();
