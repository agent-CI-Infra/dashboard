// Content-free full archive; independent of the sampled slow-request view.
export const PAGE_SIZE = 50;
const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const statusNames = {final:'已结束', running:'运行中', abandoned:'已中断', pending_descendants:'子跨度待补齐', root_unresolved:'根跨度待确认'};
export function selectTraces(traces, query, lifecycle, day = '') {
  const needle = query.trim().toLowerCase();
  return traces.filter(row => (!lifecycle || row.lifecycle === lifecycle)
    && (!day || new Date(row.start_time).toLocaleDateString('sv-SE', {timeZone:'Asia/Shanghai'}) === day)
    && (!needle || [row.request_id, row.key, ...(row.request_ids || [])].some(id => String(id || '').toLowerCase().includes(needle))));
}
export function coverageText(doc) {
  const c = doc.coverage || {};
  const states = c.statuses || {};
  return `每小时增量采集 · 最近运行 ${formatTime(doc.generated_at)}。授权工作区 ${c.authorized_workspaces ?? 0}/${c.bindings ?? 0}；发现 ${c.discovered_databases ?? 0} 个库，本轮观察校验通过且已读完 ${c.observed_databases ?? 0} 个，待采或待重试 ${c.pending_databases ?? 0} 个（WAL 等辅助文件存在 ${states.active_sidecars ?? 0}，读取失败 ${states.read_error ?? 0}，大小超限 ${states.size_limit ?? 0}，复制中变化 ${states.changing ?? 0}）。授权映射失败 ${Object.values(c.scope_errors || {}).reduce((a,b)=>a+b,0)}，目录错误 ${c.inventory_errors ?? 0}，未再发现 ${c.not_seen_databases ?? 0}，无效记录 ${c.invalid_records ?? 0}。累计归档 ${c.archived_traces ?? 0} 条轨迹、${c.archived_spans ?? 0} 个跨度。只读文件副本一致性未经写入端确认，不代表全量无遗漏；历史结果保留原采集时间。`;
}
const formatTime = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) : '未知';
const duration = value => Number.isFinite(value) ? `${(value / 1000).toFixed(3)} 秒` : '—';
export function detailPath(path) {
  if (!/^traces\/[0-9a-f]{64}\/\d+\.json$/.test(path)) throw new Error('invalid detail path');
  return path;
}

export function spanBar(span, total) {
  if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(span.start_ms)
      || !Number.isFinite(span.duration_ms)) return '';
  const left = Math.max(0, Math.min(100, 100 * span.start_ms / total));
  const width = Math.max(0, Math.min(100 - left, 100 * span.duration_ms / total));
  const colors = {phase:'#6476dc', agent:'#9b6bd2', model:'#d39a30', tool:'#35a798', event:'#8593a7'};
  const color = span.error ? '#cc5260' : colors[span.kind] || colors.event;
  return `<span class="archive-track"><span class="archive-bar" style="left:${left}%;width:${width}%;background:${color}"></span></span>`;
}

async function startArchive() {
  const host = document.getElementById('trajectory-archive');
  if (!host) return;
  const base = new URL('../data/trajectory-archive/', location.href);
  let doc, page = 0, selected = '', requestGeneration = 0;
  const get = async path => {
    const res = await fetch(new URL(path, base), {cache:'no-store'});
    if (!res.ok) throw new Error('archive unavailable');
    const value = await res.json();
    if (value.schema !== 'digitalmate/trajectory-archive@1') throw new Error('invalid archive');
    return value;
  };
  host.innerHTML = '<div class="card-head"><h2>全部原生轨迹</h2><span class="muted">全量扫描 · 分页查看 · 独立于慢请求榜单</span></div><p class="data-freshness" id="archive-coverage">正在读取全量采集状态…</p><form id="archive-form" class="lookup-form"><input id="archive-query" type="search" placeholder="搜索请求 ID 或轨迹编号" aria-label="搜索全部轨迹"><input id="archive-day" type="date" aria-label="按北京时间日期筛选"><select id="archive-lifecycle" aria-label="轨迹状态"><option value="">全部状态</option><option value="final">已结束</option><option value="running">运行中</option><option value="abandoned">已中断</option><option value="pending_descendants">子跨度待补齐</option><option value="root_unresolved">根跨度待确认</option></select><button type="submit">查询</button></form><div id="archive-list"></div><div id="archive-detail" aria-live="polite"></div>';
  const node = id => host.querySelector(`#${id}`);
  function render() {
    const filtered = selectTraces(doc.traces, node('archive-query').value, node('archive-lifecycle').value, node('archive-day').value);
    page = Math.min(page, Math.max(0, Math.ceil(filtered.length / PAGE_SIZE) - 1));
    const rows = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
    node('archive-list').innerHTML = `<p class="muted">符合筛选 ${filtered.length} 条 · 第 ${page + 1} / ${Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))} 页</p><div class="table-wrap"><table class="history-table"><thead><tr><th>开始时间（北京时间）</th><th>状态</th><th>跨度</th><th>原生耗时</th><th>请求 / 轨迹</th><th></th></tr></thead><tbody>${rows.map(r => `<tr><td>${escape(formatTime(r.start_time))}</td><td>${escape(statusNames[r.lifecycle] || '未知')}</td><td>${r.span_count}</td><td>${escape(duration(r.duration_ms))}</td><td>${escape(r.request_id || r.request_ids?.join(', ') || r.key.slice(0,16))}</td><td><button type="button" data-archive-key="${escape(r.key)}">查看轨迹</button></td></tr>`).join('')}</tbody></table></div>${rows.length ? '' : '<p class="empty">暂无匹配的归档轨迹。请结合上方采集覆盖率判断，不能据此断定源端没有轨迹。</p>'}<div class="trajectory-toolbar"><button type="button" data-archive-page="-1" ${page === 0 ? 'disabled' : ''}>上一页</button><button type="button" data-archive-page="1" ${(page + 1) * PAGE_SIZE >= filtered.length ? 'disabled' : ''}>下一页</button></div>`;
  }
  async function showDetail(key, part = 0) {
    const entry = doc.traces.find(r => r.key === key);
    if (!entry || !entry.pages[part]) return;
    selected = key;
    const generation = ++requestGeneration;
    node('archive-detail').textContent = '正在读取轨迹…';
    try {
      const data = await get(detailPath(entry.pages[part]));
      if (generation !== requestGeneration) return;
      if (data.key !== key) throw new Error('identity mismatch');
      node('archive-detail').innerHTML = `<h3>原生轨迹 · ${escape(entry.request_id || entry.key.slice(0,16))}</h3><p class="muted">采集于 ${escape(formatTime(entry.observed_at))} · 一致性未经写入端确认 · ${escape(statusNames[entry.lifecycle] || '未知')}。保留历史跨度 ${entry.retained_spans || 0} 个。本页展示第 ${data.offset + 1}–${data.offset + data.spans.length} / ${data.total} 个跨度；跨度可能嵌套或并行，不能相加。运行中跨度的结束耗时尚未确定。</p><div class="table-wrap"><table class="history-table"><thead><tr><th>阶段</th><th>状态</th><th>相对开始</th><th>时间轴</th><th>耗时</th><th>跨度 / 父跨度</th></tr></thead><tbody>${data.spans.map(s => `<tr><td>${escape(s.label)}</td><td>${escape(statusNames[s.lifecycle] || '未知')}${s.error ? ' · 错误' : ''}</td><td>${escape(duration(s.start_ms))}</td><td>${spanBar(s, entry.duration_ms)}</td><td>${s.lifecycle === 'running' ? '待结束' : escape(duration(s.duration_ms))}</td><td><code>${escape(s.span_id)}</code><br><small>${escape(s.parent_span_id || '根跨度')}</small></td></tr>`).join('')}</tbody></table></div><div class="trajectory-toolbar"><button type="button" data-archive-part="${part - 1}" ${part === 0 ? 'disabled' : ''}>上一页跨度</button><button type="button" data-archive-part="${part + 1}" ${part + 1 >= entry.pages.length ? 'disabled' : ''}>下一页跨度</button></div>`;
    } catch {
      if (generation === requestGeneration) node('archive-detail').textContent = '轨迹明细暂时读取失败，请稍后重试。';
    }
  }
  node('archive-form').addEventListener('submit', event => {event.preventDefault(); if (doc) {page = 0; render();}});
  host.addEventListener('click', event => {
    const target = event.target.closest('button');
    if (!target || target.disabled || !doc) return;
    if (target.dataset.archivePage) {page += Number(target.dataset.archivePage); render();}
    if (target.dataset.archiveKey) showDetail(target.dataset.archiveKey);
    if (target.dataset.archivePart !== undefined) showDetail(selected, Number(target.dataset.archivePart));
  });
  async function refresh() {
    try {doc = await get('index.json'); node('archive-coverage').textContent = coverageText(doc); render();}
    catch {node('archive-coverage').textContent = '全量轨迹归档尚未发布或暂时不可访问；下方慢请求样本仍可使用。';}
  }
  await refresh();
  setInterval(refresh, 300000);
}
if (typeof document !== 'undefined') startArchive();
