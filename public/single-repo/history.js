/* Complete history is indexed separately so normal tabs never download it. */
(() => {
  const root = document.getElementById('tab-history');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const when = value => value ? new Date(value).toLocaleString() : '—';
  const link = (url, label) => /^https:\/\/github\.com\//.test(url || '') ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>` : esc(label);
  let snapshot, version, manifest, busy = false, error = '', kind = 'issues', query = '', status = '', page = 0;
  let generation = 0;
  const indexes = new Map(), records = new Map();
  const names = {issues:'Issue', prs:'PR', runs:'构建 / 测试', releases:'版本'};
  async function json(path) {
    const response = await fetch(path, {signal: AbortSignal.timeout(20000)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  async function pooled(items, fn) {
    let next = 0;
    await Promise.all(Array.from({length: Math.min(4, items.length)}, async () => {
      while (next < items.length) await fn(items[next++]);
    }));
  }
  function progress() {
    const sync = snapshot?.sync;
    if (!sync) return '<p class="muted">下一次增量采集后可查看完整历史。</p>';
    const counts = Object.entries(names).map(([k,n]) => `${n} ${sync.totals?.[k] ?? 0}`).join(' · ');
    return `<div class="history-progress"><strong>${sync.complete ? '历史列表已回填' : '历史回填中'}</strong><span>${esc(counts)}</span><span>待补详情 ${sync.pending} · 失败重试 ${sync.failed}</span></div>`;
  }
  function shell() {
    root.innerHTML = `<div class="page-heading"><h1>历史数据</h1><p class="muted">全部已采集记录 · 测试日志与原始报告在 GitHub 查看</p></div>${progress()}
      <div class="filters history-filters"><label>类型 <select id="history-kind" aria-label="类型">${Object.entries(names).map(([k,v]) => `<option value="${k}" ${kind===k?'selected':''}>${v}</option>`).join('')}</select></label>
      <label>搜索 <input id="history-query" type="search" value="${esc(query)}" placeholder="编号、标题、作者、分支或 SHA"></label>
      <label>状态 <select id="history-state" aria-label="状态"><option value="">全部状态</option>${['open','closed','success','failure','cancelled','in_progress','queued'].map(k=>`<option ${status===k?'selected':''}>${k}</option>`).join('')}</select></label><button class="btn" id="history-search">查询</button></div>
      <div id="history-result" aria-live="polite"></div><div class="history-pager"><button class="btn" id="history-prev">上一页</button><span id="history-count"></span><button class="btn" id="history-next">下一页</button></div>`;
    root.querySelector('#history-kind').onchange = e => {kind=e.target.value;page=0;void load();};
    root.querySelector('#history-state').onchange = e => {status=e.target.value;page=0;void load();};
    root.querySelector('#history-search').onclick = () => {query=root.querySelector('#history-query').value.trim();page=0;void load();};
    root.querySelector('#history-query').onkeydown = e => {if(e.key==='Enter')root.querySelector('#history-search').click();};
    root.querySelector('#history-prev').onclick = () => {page=Math.max(0,page-1);void load();};
    root.querySelector('#history-next').onclick = () => {page++;void load();};
  }
  function card(row) {
    const state = row.merged_at ? 'merged' : row.conclusion || row.status || row.state;
    const title = (row.number ? `#${row.number} ` : '') + (row.title || row.name || row.tag || '');
    const metrics = row.tests?.map(t => `<div class="history-metric"><strong>${esc(t.layer.toUpperCase())} · ${esc(t.name)}</strong><span>${t.counts ? `用例 ${t.counts.tests} · 通过 ${t.counts.passed} · 失败 ${t.counts.failed} · 跳过 ${t.counts.skipped} · 重试通过 ${t.counts.flaky}` : '用例指标未知'} · ${link(t.url, 'GitHub 报告')}</span></div>`).join('') || '';
    return `<article class="card history-record"><div class="run-head"><h3>${link(row.url, title)}</h3><span class="pill">${esc(state || (row.prerelease ? '预发布' : '发布'))}</span></div><p class="muted">${esc(row.author || row.branch || row.tag || '')} · ${esc(when(row.updated_at || row.published_at || row.created_at))}${row.attempt ? ` · Run ${row.id} / attempt ${row.attempt} · <code>${esc(row.sha)}</code>` : ''}</p>${row.sha && !row.attempt ? `<p><code>${esc(row.sha)}</code> <button class="btn" data-history-sha="${esc(row.sha)}">查看此版本运行</button></p>` : ''}${metrics}${row.body ? `<details><summary>查看内容</summary><pre class="history-body">${esc(row.body)}</pre></details>` : ''}</article>`;
  }
  async function load() {
    const current = ++generation;
    busy = true; error = '';
    const result = root.querySelector('#history-result');
    result.innerHTML = '<p class="muted">读取历史索引…</p>';
    root.querySelector('#history-prev').disabled = root.querySelector('#history-next').disabled = true;
    try {
      if (!snapshot?.history) { result.innerHTML=progress();return; }
      if (!manifest) manifest = await json(`./data/history/manifest.json?v=${encodeURIComponent(version)}`);
      const shards = Object.values(manifest.shards).filter(s=>s.kind===kind);
      const catalogs = Object.values(manifest.catalogs || {}).filter(s=>s.kind===kind);
      const sources = catalogs.length ? catalogs : shards.map(s=>({...s,path:s.index}));
      await pooled(sources, async shard => {
        const key=shard.path+'?v='+shard.revision;
        if(!indexes.has(key)) indexes.set(key, await json('./data/history/'+key));
      });
      const term = query.toLocaleLowerCase();
      const matches = (catalogs.length
        ? sources.flatMap(s=>indexes.get(s.path+'?v='+s.revision).map(e=>({key:e.key,row:e.row,shard:{records:e.record,revision:e.revision}})))
        : shards.flatMap(shard=>Object.entries(indexes.get(shard.index+'?v='+shard.revision)).map(([key,row])=>({key,row,shard}))))
        .filter(({row})=>(!status || (row.state||row.conclusion||row.status)===status) && (!term || [row.number,row.id,row.title,row.name,row.author,row.branch,row.sha,row.tag].join(' ').toLocaleLowerCase().includes(term)))
        .sort((a,b)=>(b.row.updated_at||b.row.created_at||'').localeCompare(a.row.updated_at||a.row.created_at||'') || b.key.localeCompare(a.key));
      page = Math.min(page, Math.max(0, Math.ceil(matches.length/25)-1));
      const selected = matches.slice(page*25, (page+1)*25);
      await pooled(selected, async ({shard}) => {
        const key=shard.records+'?v='+shard.revision;
        if(!records.has(key)) records.set(key, await json('./data/history/'+key));
      });
      if(current !== generation)return;
      result.innerHTML = selected.map(({key,shard})=>card(records.get(shard.records+'?v='+shard.revision)[key])).join('') || '<div class="card empty">没有符合条件的记录。</div>';
      result.querySelectorAll('[data-history-sha]').forEach(button=>button.onclick=()=>{kind='runs';query=button.dataset.historySha;status='';page=0;shell();void load();});
      root.querySelector('#history-count').textContent = `${matches.length} 条 · 第 ${page+1} / ${Math.max(1,Math.ceil(matches.length/25))} 页`;
      root.querySelector('#history-prev').disabled=page===0;
      root.querySelector('#history-next').disabled=(page+1)*25>=matches.length;
    } catch(err) {
      if(current !== generation)return;
      error=err.message;
      result.innerHTML=`<div class="banner error">历史数据读取失败：${esc(error)}。<button class="btn" id="history-retry">重试</button></div>`;
      root.querySelector('#history-retry').onclick=()=>void load();
    } finally { if(current===generation)busy=false; }
  }
  window.GSBHistory={
    setSnapshot(doc) {snapshot=doc;if(version!==doc.generated_at){version=doc.generated_at;manifest=null;indexes.clear();records.clear();generation++;busy=false;}},
    render() {if(location.hash!=='#history')return;if(!root.querySelector('#history-result'))shell();if(!busy)void load();}
  };
})();
