(() => {
"use strict";
const $ = (s) => document.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const safeUrl = (v) => (/^https:\/\//.test(v || "") ? v : "#");
const link = (url, text) =>
  `<a href="${esc(safeUrl(url))}" target="_blank" rel="noopener">${esc(text)} ↗</a>`;
const number = (v) => (v == null ? "—" : Number(v).toLocaleString("zh-CN"));
const date = (v) =>
  v && !isNaN(new Date(v))
    ? new Date(v).toLocaleString("zh-CN", { hour12: false })
    : "—";
const labels = {
  success: ["通过", "good"],
  failure: ["失败", "bad"],
  timed_out: ["超时", "bad"],
  cancelled: ["已取消", "warn"],
  skipped: ["跳过", "muted"],
  neutral: ["中性", "muted"],
  action_required: ["待处理", "warn"],
  stale: ["已过期", "warn"],
  in_progress: ["运行中", "info"],
  queued: ["排队中", "info"],
  waiting: ["等待", "info"],
  pending: ["等待", "info"],
  completed: ["已完成", "muted"],
  unknown: ["未知", "muted"],
  passed: ["通过", "good"],
  failed: ["失败", "bad"],
  flaky: ["重试后通过", "warn"],
  available: ["已取得报告", "good"],
  missing: ["未上传报告", "warn"],
  unavailable: ["无法读取", "warn"],
  expired: ["产物已过期", "warn"],
  no_counts: ["报告不可解析", "warn"],
  not_inspected: ["未采集报告", "muted"],
  partial: ["报告不完整", "warn"],
};
const badge = (state, text) => {
  const [name, tone] = labels[String(state).toLowerCase()] || [
    text || state || "未知",
    "muted",
  ];
  return `<span class="badge ${tone}">${esc(text || name)}</span>`;
};
const channelNames = {
  gate: "合并门禁",
  daily: "每日构建",
  release: "版本验证",
};
let data,
  search = "",
  workKind = "all",
  lane = "all";
function state(run) {
  return run?.status === "completed"
    ? run.conclusion || "unknown"
    : run?.status || "unknown";
}
function metric(label, value, hint) {
  return `<div class="metric"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="hint">${esc(hint)}</div></div>`;
}
function heading(title, note = "") {
  return `<div class="section-head"><h2>${esc(title)}</h2><p>${esc(note)}</p></div>`;
}
function countSummary(report) {
  const c = report?.counts;
  return c ? `${number(c.passed)} / ${number(c.tests)} 通过` : "暂无用例结果";
}
function breakdown(c) {
  if (!c) return "";
  const total = c.tests || 1;
  return `<div class="bar">${["passed", "failed", "skipped", "flaky"].map((k) => `<i class="${k}" style="width:${Math.max(0, Math.min(100, (c[k] / total) * 100))}%"></i>`).join("")}</div><div class="legend">${[
    ["passed", "通过", "#399d80"],
    ["failed", "失败", "#d26a62"],
    ["skipped", "跳过", "#c0c8ce"],
    ["flaky", "重试", "#d7a948"],
  ]
    .map(
      ([k, n, col]) =>
        `<span style="--color:${col}">${n} ${number(c[k])}</span>`,
    )
    .join("")}</div>`;
}
function e2e(run) {
  return (run?.tests || []).filter((t) => t.layer === "e2e");
}
function sumReports(reports) {
  if (!reports.length || reports.some((r) => !r.counts)) return null;
  return Object.fromEntries(
    ["tests", "passed", "failed", "skipped", "flaky"].map((k) => [
      k,
      reports.reduce((n, r) => n + r.counts[k], 0),
    ]),
  );
}
function laneCard(kind) {
  const r = data.quality.runs.find((r) => r.channel === kind);
  const c = sumReports(e2e(r));
  return `<article class="card lane"><div class="kicker">${{ gate: "PULL REQUEST / CI", daily: "NIGHTLY / SCHEDULE", release: "RELEASE / VERSION" }[kind]}</div><h3>${channelNames[kind]}</h3>${r ? `<div class="run-title">${link(r.url, r.name)}</div>${badge(state(r))}<span class="sub">${esc(r.branch)} · <code>${esc(r.sha?.slice(0, 8))}</code> · 第 ${r.attempt} 次运行</span><span class="sub">${date(r.updated_at)}</span><div class="numbers"><div><b>${c ? number(c.passed) + " / " + number(c.tests) : "—"}</b><span>E2E 稳定通过 / 总用例</span></div><div><b>${c && c.tests ? ((c.passed / c.tests) * 100).toFixed(1) + "%" : "—"}</b><span>通过率</span></div></div>${c ? breakdown(c) : `<p class="small-note">${r.status === "completed" ? "没有可用的 E2E 用例报告；构建通过不等于 E2E 已通过。" : "运行尚未结束，测试结果待产出。"}</p>`}` : '<div class="empty">暂无运行记录<br><span class="sub">接入此类工作流后自动展示</span></div>'}</article>`;
}
function runCard(r) {
  return `<article class="card run-card"><div class="run-head"><h3>${link(r.url, r.name)} <span class="count-inline">${channelNames[r.channel]}</span></h3>${badge(state(r))}</div><p class="run-meta">${esc(r.branch)} · <code>${esc(r.sha?.slice(0, 12))}</code> · Run #${r.id} / attempt ${r.attempt} · ${date(r.updated_at)} · ${esc(r.event)}</p>${r.tests.length ? `<div class="table-wrap"><table><thead><tr><th>测试层 / 报告</th><th>总用例</th><th>稳定通过</th><th>失败</th><th>跳过</th><th>重试通过</th><th>通过率</th></tr></thead><tbody>${r.tests.map((t, i) => `<tr><td class="title"><strong>${esc(t.layer.toUpperCase())}</strong> <button type="button" class="test-link" data-run="${r.id}" data-test="${i}">${esc(t.name)} →</button><span class="sub">${badge(t.status)}</span></td>${["tests", "passed", "failed", "skipped", "flaky"].map((k) => `<td class="num">${number(t.counts?.[k])}</td>`).join("")}<td class="num">${t.counts?.tests ? ((t.counts.passed / t.counts.tests) * 100).toFixed(1) + "%" : "—"}</td></tr>`).join("")}</tbody></table></div>` : `<p class="empty">${badge(r.reports_status)}<br>暂无可核验的用例数量；请上传 Playwright JSON、JUnit 或测试汇总产物。</p>`}<div class="job-list">${r.jobs.map((j) => `<span>${badge(j.conclusion || j.status)} ${link(j.url, j.name)}${j.failed_steps.length ? `<span class="sub">失败步骤：${esc(j.failed_steps.join("、"))}</span>` : ""}</span>`).join("")}</div></article>`;
}
function quality() {
  const runs = data.quality.runs.filter(
    (r) => lane === "all" || r.channel === lane,
  );
  return `${heading("构建与测试证据", "运行结果和测试用例结果分别判断")}<div class="filters"><label>阶段 <select id="lane" aria-label="阶段"><option value="all">全部阶段</option><option value="gate">合并门禁</option><option value="daily">每日构建</option><option value="release">版本验证</option></select></label><span class="muted">稳定通过率 = 通过 ÷ 总用例；跳过与重试通过单列</span></div>${runs.length ? runs.slice(0, 30).map(runCard).join("") : '<div class="card empty">此阶段尚无 Actions 运行记录。接入对应工作流后，这里会展示提交、任务和测试报告。</div>'}<p class="small-note">此页展示近期运行。<a href="#history">历史数据</a>可按运行和 attempt 查看全部已采集指标。</p>`;
}
function releases() {
  return `${heading("版本验证", "仅关联版本提交 SHA 一致的版本验证运行")}<div class="card">${
    data.releases.length
      ? data.releases
          .map((release) => {
            const runs = data.quality.runs.filter((r) =>
              release.validation_run_ids.includes(r.id),
            );
            return `<div class="release-row"><div><h3>${link(release.url, release.tag)}</h3>${badge(release.prerelease ? "pending" : "success", release.prerelease ? "预发布" : "已发布")}<span class="sub">${date(release.published_at)}</span><code>${esc(release.sha?.slice(0, 12) || "提交未知")}</code></div><div>${runs.length ? runs.map((r) => `<div class="release-evidence">${badge(state(r))}${link(r.url, r.name)}<span>E2E ${countSummary({ counts: sumReports(e2e(r)) })}</span></div>`).join("") : `${badge("missing", "尚无匹配的版本验证")}<p class="small-note">发布成功不等于测试完成；等待此版本提交的验证运行。</p>`}</div></div>`;
          })
          .join("")
      : '<div class="empty">仓库尚无 Release。发布版本后，将按标签对应的提交关联验证证据。</div>'
  }</div>`;
}

function render(){
  const target=document.querySelector('#tab-quality');
  if(!data || !target)return;
  target.innerHTML=quality();
  const select=target.querySelector('#lane');select.value=lane;
  select.onchange=e=>{lane=e.target.value;render();};
}
$("#close-detail").onclick = () => $("#test-detail").close();
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-test]");
  if (!btn || !data) return;
  const r = data.quality.runs.find((r) => String(r.id) === btn.dataset.run),
    t = r.tests[Number(btn.dataset.test)];
  $("#detail-title").textContent = t.layer.toUpperCase() + " · " + t.name;
  $("#detail-body").innerHTML =
    `<p>${link(r.url, `${r.name} · Run #${r.id} / attempt ${r.attempt}`)}</p><p class="small-note">${date(r.updated_at)} · ${esc(r.sha)}</p><h3>${esc(countSummary(t))}</h3>${breakdown(t.counts)}<div class="table-wrap" style="margin-top:20px"><table><thead><tr><th>结果</th><th>用例</th><th>文件 / 项目</th></tr></thead><tbody>${(t.cases || []).length ? t.cases.map((c) => `<tr><td>${badge(c.status)}</td><td class="title">${esc(c.name)}</td><td>${esc(c.file)}<span class="sub">${esc(c.project || "")}</span></td></tr>`).join("") : '<tr><td colspan="3" class="empty">此报告仅提供汇总，没有逐用例明细。</td></tr>'}</tbody></table></div><p class="small-note">原始日志、逐用例记录、截图和 trace 请从 GitHub 运行页查看；历史指标独立保留。</p>`;
  $("#test-detail").showModal();
});
window.GSBQuality={
 setSnapshot(snapshot){data=snapshot;},
 render(){render();document.querySelector('#tab-releases').innerHTML=releases();},
 summary(){return heading('构建状态')+'<div class="quality-lanes">'+['gate','daily','release'].map(laneCard).join('')+'</div>';}
};

})();
