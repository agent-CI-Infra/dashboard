const { test, expect } = require('../.e2e/node_modules/@playwright/test');
const { resolve } = require('node:path');
const shot = name => resolve(__dirname, '../.e2e/' + name + '.png');

test('all existing pages render; only static requests and no promotional copy', async ({ page }) => {
  const requests = [], errors = [];
  page.on('request', r => requests.push(r.url()));
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/github-status-board/');
  // Problems first: the red main CI and overdue PR work lead the overview.
  const health=page.locator('#tab-overview .health');
  await expect(health).toContainText('1 个流水线问题需要处理');
  await expect(health.locator('.attention').first()).toContainText('主干 CI 连续失败 1 次');
  await expect(health).toContainText('1 个 PR 超过 3 天无人评审');
  await expect(page.locator('#tab-overview .tile').filter({hasText:'PR 门禁'})).toContainText('近 30 天成功率');
  await expect(page.locator('#tab-overview .tile').filter({hasText:'PR 门禁'})).toContainText('耗时 18m 44s');
  await expect(page.locator('#tab-overview .tile').filter({hasText:'主干门禁用例'})).toContainText('324');
  await expect(page.locator('#tab-overview .tile').filter({hasText:'整仓行覆盖率'})).toContainText('80.7%');
  await expect(page.locator('#tab-overview .ci-lane-row:not(.ci-axis)')).toHaveCount(5);
  await expect(page.locator('#tab-overview .ci-lane-row[data-lane="pr"] .ci-lane-day')).toHaveCount(14);
  await expect(page.locator('#tab-overview')).toContainText('v1.0');
  await expect(page.locator('#meta')).toContainText('20:00');
  await expect(page.locator('#global-banner')).toContainText('超过 36 小时未更新');
  await expect(page.locator('body')).not.toContainText('把进展与风险放在同一页');
  await page.screenshot({path:shot('overview-desktop'),fullPage:true});
  await expect(page.locator('.topbar #tabs a')).toHaveCount(7);
  await expect(page.locator('.topbar #tabs')).not.toContainText('构建报告');
  await expect(page.locator('.topbar #tabs')).not.toContainText('历史数据');
  await expect(page.locator('body > footer')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('GitHub Pages 静态看板 · GitHub 数据只读');
  await expect(page.locator('#global-banner')).not.toContainText('部分补充信息不可读取');
  const tabsBox=await page.locator('#tabs').boundingBox(), timeBox=await page.locator('#meta').boundingBox();
  expect(timeBox.x).toBeGreaterThan(tabsBox.x+tabsBox.width);
  expect(timeBox.y).toBeLessThan(tabsBox.y+tabsBox.height);
  for (const id of ['issues','prs','ci','tests','coverage','sync']) {
    await page.locator(`[data-tab="${id}"]`).click();
    await expect(page.locator(`#tab-${id}`)).toBeVisible();
    await expect(page.locator(`#tab-${id}`)).not.toContainText('渲染出错');
  }
  expect(errors).toEqual([]);
  expect(requests.every(url => url.startsWith('http://127.0.0.1:18890/github-status-board/'))).toBeTruthy();
  expect(requests.some(url => url.includes('/api/'))).toBeFalsy();
});

test('issue distribution and filters; PR reviews and current commit checks', async ({page})=>{
  await page.goto('/github-status-board/#issues');
  await expect(page.locator('#tab-issues')).toContainText('开启时长');
  await expect(page.locator('#tab-issues')).toContainText('<script>alert(1)</script>');
  expect(await page.locator('#tab-issues script').count()).toBe(0);
  await page.locator('[data-filter="issueQ"]').fill('不存在');
  await expect(page.locator('#tab-issues')).toContainText('0 / 1');
  await page.locator('[data-filter="issueQ"]').fill('超时');
  await expect(page.locator('#tab-issues')).toContainText('1 / 1');
  await page.locator('[data-tab="prs"]').click();
  await expect(page.locator('#tab-prs')).toContainText('需修改');
  await expect(page.locator('#tab-prs')).toContainText('评审负载');
  await page.locator('#tab-prs .work-table summary').click();
  await expect(page.locator('#tab-prs .work-table details')).toContainText('E2E');
});

test('open Issue table defaults to newest updates and resets sorting on reload', async ({page}) => {
  await page.route('**/github-status-board/data/snapshot.json*', async route => {
    const response = await route.fetch(), doc = await response.json();
    const issues = doc.sections.issues.data, original = issues.items[0];
    const item = (number, title, updated_at) => ({...original, number, title, updated_at,
      url: `${doc.repo_url}/issues/${number}`});
    const missing = item(31, '缺失更新时间', undefined);
    // Keep the shared Issue untouched and deliberately return an unsorted list.
    issues.items = [item(30, '空更新时间', null), item(10, '较早更新的 Issue', '2026-09-21T10:00:00Z'),
      original, item(20, '较新更新的 Issue', '2026-09-22T10:00:00Z'), missing];
    issues.open_count = issues.items.length;
    await route.fulfill({response, json: doc});
  });
  await page.goto('/github-status-board/#issues');
  const tab = page.locator('#tab-issues');
  const table = tab.locator('.work-table');
  const numbers = table.locator('tbody tr td:first-child');
  const updateHead = table.locator('th[data-key="updated_at"]');
  const defaultOrder = ['#20', '#10', '#1', '#30', '#31'];
  await expect(numbers).toHaveText(defaultOrder);
  await expect(table.locator('tbody tr').nth(0)).toContainText('较新更新的 Issue');
  await expect(table.locator('tbody tr').nth(1)).toContainText('较早更新的 Issue');
  await expect(updateHead).toHaveText('更新 ▼');
  await expect(tab.getByRole('heading', {name: /^最近更新/})).toHaveCount(0);
  await expect(tab.getByRole('heading', {name: /^最久未动/})).toHaveCount(0);
  await expect(tab.locator('.tiles')).toContainText('陈旧（≥30 天无更新）');
  await expect(tab.getByRole('heading', {name: /^标签分布/})).toBeVisible();
  await expect(tab.getByRole('heading', {name: /^认领负载/})).toBeVisible();
  await expect(tab.getByRole('heading', {name: '里程碑', exact: true})).toBeVisible();
  await table.locator('th[data-key="number"]').click();
  await expect(numbers).toHaveText(['#31', '#30', '#20', '#10', '#1']);
  await expect(updateHead).toHaveText('更新');
  await page.reload();
  await expect(numbers).toHaveText(defaultOrder);
  await expect(updateHead).toHaveText('更新 ▼');
  await page.screenshot({path: shot('issue-list-updated-desktop'), fullPage: true});
  await page.setViewportSize({width: 390, height: 1000});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: shot('issue-list-updated-narrow'), fullPage: true});
  await page.locator('[data-tab="prs"]').click();
  await expect(page.locator('#tab-prs').getByRole('heading', {name: '最近合并', exact: true})).toBeVisible();
});

// Issue and PR lists switch between the table and the local board.
const showBoard = async (page, tab) => { await page.locator(`[data-work-view="${tab}"] [data-val="board"]`).click(); await expect(page.locator(`#tab-${tab} .work-board .board`)).toBeVisible(); };
test('board grouping, drawer edits and reload retain browser fields', async ({page})=>{
  await page.goto('/github-status-board/#issues');
  await expect(page.locator('#tab-issues .work-table')).toContainText('处理超时问题');
  await showBoard(page, 'issues');
  // Each page shows only its own kind; statistics stay above the list.
  await expect(page.locator('#tab-issues .bcard')).toHaveCount(1);
  await expect(page.locator('#tab-issues')).toContainText('开启时长');
  await page.locator('[data-open="issue:1"]').click();
  await expect(page.locator('#drawer')).toContainText('Reproduction');
  await expect(page.locator('#drawer')).toContainText('<script>alert(2)</script>');
  await page.locator('[data-dfield="priority"]').selectOption('P1');
  await page.locator('[data-dfield="iteration"]').fill('Sprint 8');
  await page.locator('[data-dfield="iteration"]').press('Tab');
  await page.locator('[data-dfield="note"]').fill('本地记录');
  await page.locator('[data-dfield="note"]').press('Tab');
  await page.locator('[data-baction="close-drawer"]').click();
  await page.locator('#tab-issues [data-bpref="group"] [data-val="priority"]').click();
  await expect(page.locator('.bcard[data-id="issue:1"]')).toContainText('P1');
  await page.locator('[data-tab="prs"]').click();
  await expect(page.locator('#tab-prs .work-table')).toContainText('修复工作流');
  await showBoard(page, 'prs');
  await expect(page.locator('#tab-prs .bcard')).toHaveCount(1);
  await expect(page.locator('#tab-prs .bcard[data-id="pr:3"]')).toBeVisible();
  // PR filters and grouping are kept apart from the Issue board.
  await expect(page.locator('#tab-prs [data-bpref="group"] [data-val="status"]')).toHaveClass('on');
  await page.reload();
  await expect(page.locator('#tab-prs .bcard[data-id="pr:3"]')).toBeVisible();
  await page.locator('[data-tab="issues"]').click();
  await page.locator('[data-open="issue:1"]').click();
  await expect(page.locator('[data-dfield="priority"]')).toHaveValue('P1');
  await expect(page.locator('[data-dfield="iteration"]')).toHaveValue('Sprint 8');
  await expect(page.locator('[data-dfield="note"]')).toHaveValue('本地记录');
  await page.locator('[data-baction="close-drawer"]').click();
  await page.screenshot({path:shot('issue-board'),fullPage:true});
  await page.locator('[data-work-view="issues"] [data-val="table"]').click();
  await expect(page.locator('#tab-issues .work-board')).toHaveCount(0);
  await expect(page.locator('#tab-issues .work-table')).toContainText('处理超时问题');
});

// Open-age pie: bucket counts come from the page's own open items, and clicking a sector
// or legend entry filters only that page's open list. Boundary ages arrive via a patched
// snapshot so the shared fixture keeps its single issue and PR.
const withAgedSnapshot = async (page) => {
  await page.route('**/github-status-board/data/snapshot.json*', async (route) => {
    const res = await route.fetch();
    const doc = await res.json();
    const t = '2026-09-20T12:00:00Z', base = doc.repo_url;
    const mkIssue = (number, age_days) => ({
      number, title: age_days == null ? '样本 无年龄' : `样本 ${age_days}d`, url: `${base}/issues/${number}`,
      state: 'open', author: 'dev', labels: [], assignees: [], milestone: null, comments: 0,
      created_at: t, updated_at: t, closed_at: null, age_days, idle_days: age_days, reactions: 0, body: '',
    });
    const mkPr = (number, age_days) => ({
      number, title: `PR 样本 ${age_days}d`, url: `${base}/pull/${number}`, state: 'open', author: 'dev',
      labels: [], assignees: [], milestone: null, comments: 0, created_at: t, updated_at: t, closed_at: null,
      age_days, idle_days: age_days, draft: false, review_decision: 'NONE', reviews: [], requested_reviewers: [],
      waiting_review: false, mergeable: 'MERGEABLE', ci: { state: 'success', checks: [] }, head: `aged-${number}`, base: 'main',
      linked_issues: [], head_sha: 'b'.repeat(40), body: '',
    });
    const ages = [0, 6.9, 7, 29.9, 30, 89.9, 90, 365, null];
    const issues = doc.sections.issues.data, prs = doc.sections.prs.data;
    ages.forEach((age, i) => issues.items.push(mkIssue(100 + i, age)));
    issues.open_count = issues.items.length;
    prs.items.push(mkPr(50, 45), mkPr(51, 100));
    prs.open_count = prs.items.length;
    prs.recent_merged = [{ number: 99, title: '早前合并的 PR', url: `${base}/pull/99`, author: 'dev', merged_at: t, time_to_merge_h: 5 }];
    const boardItem = (kind, x) => ({
      id: `${kind}:${x.number}`, kind, number: x.number, title: x.title, url: x.url, state: 'open', author: x.author,
      labels: [], assignees: [], milestone: null, comments: 0, created_at: t, updated_at: t, closed_at: null,
      age_days: x.age_days, idle_days: x.age_days, linked: [], draft: false, review_decision: null, ci_state: null, merged: false,
      ...(kind === 'pr' ? { head: x.head, base: x.base } : {}),
    });
    ages.forEach((age, i) => doc.board.items.push(boardItem('issue', mkIssue(100 + i, age))));
    [mkPr(50, 45), mkPr(51, 100)].forEach((p) => doc.board.items.push(boardItem('pr', p)));
    await route.fulfill({ response: res, body: JSON.stringify(doc) });
  });
};

test('open-age pie filters only the open list on its own page', async ({page}) => {
  test.setTimeout(60000);
  await withAgedSnapshot(page);
  const header = tab => page.locator(`#tab-${tab} .open-list-header`);
  const stableClick = async (tab, target, below = false) => {
    await header(tab).evaluate((el, below) => {
      const rect = el.getBoundingClientRect();
      window.scrollTo(0, window.scrollY + (below ? rect.bottom + 20 : rect.top - 120));
    }, below);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
    const before = await page.evaluate(() => window.scrollY);
    expect(before).toBeGreaterThan(0);
    const height = (await header(tab).boundingBox()).height;
    const list = page.locator(`#tab-${tab} .work-table, #tab-${tab} .work-board`);
    const listTop = await list.evaluate(el => el.getBoundingClientRect().top + window.scrollY);
    // Playwright click would scroll an offscreen sector into view before the handler.
    // Dispatch its click while below the chart to isolate the application's focus/scroll behavior.
    if (below) await target.dispatchEvent('click'); else await target.click();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect(Math.abs(await page.evaluate(() => window.scrollY) - before)).toBeLessThanOrEqual(2);
    expect(Math.abs((await header(tab).boundingBox()).height - height)).toBeLessThanOrEqual(2);
    expect(Math.abs(await list.evaluate(el => el.getBoundingClientRect().top + window.scrollY) - listTop)).toBeLessThanOrEqual(2);
  };
  const checkLayout = async (tab, width) => {
    const chart = await page.locator(`#tab-${tab} .age-overview`).boundingBox();
    const controls = await page.locator(`#tab-${tab} .open-list-controls`).boundingBox();
    if (width === 1440) {
      expect(controls.x).toBeGreaterThanOrEqual(chart.x + chart.width);
      expect(Math.abs(controls.y - chart.y)).toBeLessThanOrEqual(2);
    } else {
      expect(controls.y).toBeGreaterThanOrEqual(chart.y + chart.height);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  };
  await page.goto('/github-status-board/#issues');
  const pie = page.locator('#tab-issues [data-age-pie="issues"]');
  await expect(pie).toBeVisible();
  const key = (name) => pie.locator('.age-key', {hasText: name});
  const rows = page.locator('#tab-issues .work-table tbody tr');
  // Buckets follow the collector boundaries [0,7) [7,30) [30,90) [90,∞); the item without
  // age_days stays in the list but is not counted in any sector.
  await expect(rows).toHaveCount(10);
  await expect(pie.locator('.age-total')).toHaveText('9');
  await expect(pie.locator('.age-sector')).toHaveCount(4);
  await expect(key('<7d')).toContainText('3');
  await expect(key('<7d')).toContainText('33.3%');
  await expect(key('7-30d')).toContainText('2');
  await expect(key('7-30d')).toContainText('22.2%');
  await expect(key('30-90d')).toContainText('22.2%');
  await expect(key('>90d')).toContainText('22.2%');
  // Legend click filters the open Issue table; summary counts remain unchanged.
  await stableClick('issues', key('7-30d'));
  await expect(rows).toHaveCount(2);
  await expect(page.locator('#tab-issues .list-head')).toContainText('2 / 10');
  await expect(page.locator('#tab-issues .work-table')).toContainText('样本 7d');
  await expect(page.locator('#tab-issues .work-table')).toContainText('样本 29.9d');
  await expect(page.locator('#tab-issues .work-table')).not.toContainText('样本 0d');
  await expect(page.locator('#tab-issues .work-table')).not.toContainText('样本 无年龄');
  await expect(key('7-30d')).toHaveAttribute('aria-pressed', 'true');
  await expect(pie.locator('.age-sector[data-age-filter="1"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#tab-issues .age-filter-slot .chip')).toContainText('开启时长：7-30d');
  await expect(page.locator('#tab-issues').getByRole('heading', {name: /^最近更新/})).toHaveCount(0);
  await expect(page.locator('#tab-issues').getByRole('heading', {name: /^最久未动/})).toHaveCount(0);
  await expect(page.locator('#tab-issues .tiles .tile').filter({hasText: /^开放/})).toContainText('10');
  await page.screenshot({path:shot('issue-age-pie'),fullPage:true});
  // The age bucket intersects with the text search; an empty result keeps the pie.
  await page.locator('[data-filter="issueQ"]').fill('29.9');
  await expect(rows).toHaveCount(1);
  await expect(page.locator('#tab-issues .work-table')).toContainText('样本 29.9d');
  await page.locator('[data-filter="issueQ"]').fill('不存在');
  await expect(rows).toHaveCount(0);
  await expect(page.locator('#tab-issues .work-table')).toContainText('没有符合筛选条件的开放 Issue');
  await expect(pie).toBeVisible();
  await page.locator('[data-filter="issueQ"]').fill('');
  // Clicking the selected legend entry again clears the filter.
  await stableClick('issues', key('7-30d'));
  await expect(rows).toHaveCount(10);
  await expect(page.locator('#tab-issues .age-filter-slot .chip')).toHaveCount(0);
  await expect(key('7-30d')).toHaveAttribute('aria-pressed', 'false');
  // A sector click works too, and the chip clears it.
  await stableClick('issues', pie.locator('.age-sector[data-age-filter="3"]'));
  await expect(rows).toHaveCount(2);
  await expect(page.locator('#tab-issues .work-table')).toContainText('样本 90d');
  await expect(page.locator('#tab-issues .work-table')).toContainText('样本 365d');
  await stableClick('issues', pie.locator('.age-sector[data-age-filter="3"]'), true);
  await expect(rows).toHaveCount(10);
  await stableClick('issues', pie.locator('.age-sector[data-age-filter="3"]'), true);
  await expect(rows).toHaveCount(2);
  await stableClick('issues', page.locator('#tab-issues .age-filter-slot .chip button'));
  await expect(rows).toHaveCount(10);
  // Sectors are keyboard-operable controls.
  await pie.locator('.age-sector[data-age-filter="2"]').focus();
  await page.keyboard.press('Enter');
  await expect(rows).toHaveCount(2);
  await expect(page.locator('#tab-issues .work-table')).toContainText('样本 30d');
  // The PR page keeps its own pie and selection; zero-count buckets are not clickable.
  await page.locator('[data-tab="prs"]').click();
  const pPie = page.locator('#tab-prs [data-age-pie="prs"]');
  const pRows = page.locator('#tab-prs .work-table tbody tr');
  await expect(pRows).toHaveCount(3);
  await expect(pPie.locator('.age-total')).toHaveText('3');
  await expect(pPie.locator('.age-sector')).toHaveCount(3);
  await expect(pPie.locator('button.age-key')).toHaveCount(3);
  await expect(pPie.locator('span.age-key.zero')).toHaveCount(1);
  await expect(pPie.locator('span.age-key.zero')).toContainText('7-30d');
  await stableClick('prs', pPie.locator('button.age-key', {hasText: '>90d'}));
  await expect(pRows).toHaveCount(1);
  await expect(page.locator('#tab-prs .work-table')).toContainText('PR 样本 100d');
  await expect(page.locator('#tab-prs .age-filter-slot .chip')).toContainText('开启时长：>90d');
  // Recently merged PRs are not touched by the open-list filter.
  await expect(page.locator('#tab-prs')).toContainText('早前合并的 PR');
  // Back on Issues the earlier selection still holds; the PR choice did not follow.
  await page.locator('[data-tab="issues"]').click();
  await expect(rows).toHaveCount(2);
  await expect(page.locator('#tab-issues .work-table')).toContainText('样本 89.9d');
  await expect(key('30-90d')).toHaveAttribute('aria-pressed', 'true');
  await expect(key('>90d')).toHaveAttribute('aria-pressed', 'false');
  // The board view of the same page reads the very same filter.
  await showBoard(page, 'issues');
  await expect(page.locator('#tab-issues .bcard')).toHaveCount(2);
  await expect(page.locator('#tab-issues .age-filter-slot .chip')).toContainText('开启时长：30-90d');
  await stableClick('issues', page.locator('#tab-issues .age-filter-slot .chip button'));
  await expect(page.locator('#tab-issues .bcard')).toHaveCount(10);
  await page.locator('[data-tab="prs"]').click();
  await showBoard(page, 'prs');
  await expect(page.locator('#tab-prs .bcard')).toHaveCount(1);
  await expect(page.locator('#tab-prs .bcard')).toContainText('PR 样本 100d');
  await stableClick('prs', page.locator('#tab-prs .age-filter-slot .chip button'));
  await expect(page.locator('#tab-prs .bcard')).toHaveCount(3);
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: 1000});
    for (const tab of ['issues', 'prs']) {
      await page.locator(`[data-tab="${tab}"]`).click();
      for (const view of ['table', 'board']) {
        await page.locator(`[data-work-view="${tab}"] [data-val="${view}"]`).click();
        await checkLayout(tab, width);
        for (const colorScheme of ['light', 'dark']) {
          await page.emulateMedia({colorScheme});
          const chart = page.locator(`#tab-${tab} [data-age-pie]`);
          const expected = ['--s2', '--s4', '--s7', '--s3'];
          const colors = await chart.locator('.age-key i').evaluateAll((els, vars) => els.map((el, k) => {
            const probe = document.createElement('i');
            probe.style.backgroundColor = `var(${vars[k]})`; el.append(probe);
            const matches = getComputedStyle(el).backgroundColor === getComputedStyle(probe).backgroundColor;
            probe.remove(); return {matches, color: getComputedStyle(el).backgroundColor};
          }), expected);
          expect(colors.every(c => c.matches)).toBe(true);
          expect(new Set(colors.map(c => c.color)).size).toBe(4);
          const sector = chart.locator('.age-sector[data-age-filter="0"]');
          await stableClick(tab, sector, true);
          await expect(sector).toHaveAttribute('aria-pressed', 'true');
          await expect(sector).not.toHaveAttribute('transform', /translate/);
          await expect(sector).toHaveCSS('stroke', 'rgb(11, 11, 11)');
          await page.evaluate(() => document.activeElement?.blur());
          await page.mouse.move(0, 0);
          await header(tab).evaluate(el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - document.querySelector('.topbar').getBoundingClientRect().height - 12));
          await header(tab).screenshot({path: shot(`age-layout-${tab}-${view}-${width}-${colorScheme}`)});
          await stableClick(tab, sector, true);
          await expect(sector).toHaveAttribute('aria-pressed', 'false');
          await stableClick(tab, chart.locator('button.age-key').first(), true);
          await stableClick(tab, page.locator(`#tab-${tab} .age-filter-slot button`), true);
          await checkLayout(tab, width);
        }
      }
    }
  }
});

test('drag a card, configure renamed column and WIP, preserve manual status',async({page})=>{
  await page.goto('/github-status-board/#issues');
  await showBoard(page, 'issues');
  // Use the same native drag/drop events as the board; no server mutation occurs.
  const transfer=await page.evaluateHandle(()=>new DataTransfer());
  await page.locator('.bcard[data-id="issue:1"]').dispatchEvent('dragstart',{dataTransfer:transfer});
  await page.locator('.bcol[data-key="进行中"]').dispatchEvent('drop',{dataTransfer:transfer});
  await page.locator('.bcard[data-id="issue:1"]').dispatchEvent('dragend',{dataTransfer:transfer});
  await page.locator('[data-open="issue:1"]').click();
  await expect(page.locator('[data-dfield="status"]')).toHaveValue('进行中');
  await page.locator('[data-baction="close-drawer"]').click();
  await page.locator('[data-baction="settings"]').click();
  const row=page.locator('#status-rows tr').nth(1);
  await row.locator('[name="name"]').fill('处理中');
  await row.locator('[name="limit"]').fill('1');
  await page.locator('[data-baction="add-status"]').click();
  await page.locator('#status-rows tr').last().locator('[name="name"]').fill('临时列');
  await page.locator('#status-rows tr').last().locator('[data-srow="up"]').click();
  await expect(page.locator('#status-rows tr').nth(3).locator('[name="name"]')).toHaveValue('临时列');
  await page.locator('#status-rows tr').nth(3).locator('[data-srow="del"]').click();
  await expect(page.locator('#status-rows tr')).toHaveCount(4);
  await page.locator('#settings-form').getByRole('button',{name:'保存',exact:true}).click();
  await expect(page.locator('#board-settings')).not.toBeVisible();
  await page.getByRole('button',{name:'刷新视图',exact:true}).click();
  await page.locator('[data-open="issue:1"]').click();
  await expect(page.locator('[data-dfield="status"]')).toHaveValue('处理中');
  await page.locator('[data-baction="close-drawer"]').click();
  await page.locator('[data-baction="history"]').click();
  await expect(page.locator('.board-history')).toContainText('手动');
});

test('drawer edits and field import/export stay local',async({page})=>{
  const writes=[];page.on('request',r=>{if(r.method()!=='GET')writes.push(r.url())});
  await page.goto('/github-status-board/#prs');
  await showBoard(page, 'prs');
  await page.locator('[data-open="pr:3"]').click();
  await page.locator('[data-dfield="priority"]').selectOption('P0');
  await page.locator('[data-baction="close-drawer"]').click();
  await expect(page.locator('.bcard[data-id="pr:3"]')).toContainText('P0');
  const download=page.waitForEvent('download');
  await page.locator('#tab-prs [data-local-export]').click();
  expect((await download).suggestedFilename()).toBe('board-fields.json');
  const state=await page.evaluate(()=>JSON.parse(window.GSBLocalBoard.export()));
  state.items['pr:3'].priority='P2';state.items['pr:3'].note='导入的记录';
  await page.locator('#tab-prs [data-local-import]').setInputFiles({name:'fields.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(state))});
  await expect(page.locator('.bcard[data-id="pr:3"]')).toContainText('P2');
  await expect(page.locator('.bcard[data-id="pr:3"]')).toContainText('导入的记录');
  state.repo='example/other';
  await page.locator('#tab-prs [data-local-import]').setInputFiles({name:'wrong.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(state))});
  await expect(page.locator('#board-flash')).toContainText('文件不属于当前仓库');
  expect(writes).toEqual([]);
});

test('CI trends, failed job steps, test distribution and coverage',async({page})=>{
  await page.goto('/github-status-board/#ci');
  await expect(page.locator('#tab-ci')).toContainText('Workflow 健康');
  await expect(page.locator('#tab-ci')).toContainText('Run browser journeys');
  await expect(page.locator('#tab-ci')).toContainText('Coverage');
  const lanes = page.locator('#tab-ci .ci-lanes');
  const lane = (key) => lanes.locator(`.ci-lane-row[data-lane="${key}"]`);
  await expect(lanes.locator('.ci-lane-row:not(.ci-axis)')).toHaveCount(5);
  for (const key of ['pr','main','merge_queue','daily','release']) await expect(lane(key).locator('.ci-lane-day')).toHaveCount(30);
  // One shared day axis: the newest column starts at the same x in every lane.
  const newestX = await Promise.all(['pr','main','merge_queue','daily','release'].map(async (key) => Math.round((await lane(key).locator('.ci-lane-day').last().boundingBox()).x)));
  expect(new Set(newestX).size).toBe(1);
  const today = lane('pr').locator('.ci-lane-day').last().locator('.ci-run');
  await expect(today).toHaveCount(13);
  expect((await today.nth(1).boundingBox()).y).toBeGreaterThan((await today.nth(0).boundingBox()).y);
  await expect(today.first()).toHaveAttribute('aria-label', /00:10（UTC\+8）/);
  await expect(today.first()).toHaveAttribute('href', /\/actions\/runs\/500$/);
  await expect(today.nth(1)).toHaveClass(/failure/);
  await expect(lane('pr').locator('.ci-day-more')).toHaveText('13');
  await today.nth(1).hover();
  await expect(page.locator('#tooltip')).toContainText('CI · pull_request · PR #3');
  // Each finished run says how long it took; each lane gives the median of finished runs.
  await expect(page.locator('#tooltip')).toContainText('失败 · 耗时 11m 57s');
  await expect(lane('pr').locator('.ci-lane-time')).toContainText('中位耗时');
  await expect(page.locator('#tooltip')).toContainText('分支 fix-timeout');
  await expect(lane('main').locator('.ci-run').last()).toHaveAttribute('aria-label', /workflow_dispatch · 手动/);
  await expect(lane('daily').locator('.ci-run')).toHaveCount(8);
  await expect(lane('release').locator('.ci-run')).toHaveCount(0);
  await expect(lane('release')).toContainText('窗口内没有 run');
  await expect(lane('release')).not.toContainText('失败 1');
  await expect(lanes.locator('.ci-lane-head b')).toHaveText(['PR', '主干', '合并队列', 'Daily', '版本']);
  await expect(page.locator('#tab-ci')).not.toContainText(/工作流调用|其他工作流|未知触发/);
  await expect(page.locator('#tab-ci a[href$="/runs/399"], #tab-ci a[href$="/runs/398"], #tab-ci a[href$="/runs/396"]')).toHaveCount(0);
  const queue = lane('merge_queue').locator('.ci-run');
  await expect(queue).toHaveAttribute('href', /\/runs\/397$/);
  await queue.focus();
  await expect(page.locator('#tooltip')).toContainText('CI · merge_group');
  await page.context().route('https://github.com/**/actions/runs/397', (route) => route.fulfill({ contentType: 'text/html', body: '<h1>Queue run 397</h1>' }));
  const [openedRun] = await Promise.all([page.waitForEvent('popup'), queue.click()]);
  await expect(openedRun).toHaveURL(/\/actions\/runs\/397$/);
  await expect(openedRun.getByRole('heading')).toHaveText('Queue run 397');
  await openedRun.close();
  await page.mouse.move(0, 0);
  await page.locator('#tab-ci .card:has(.ci-lanes)').screenshot({path:shot('ci-lanes-desktop')});
  // Every finished run prints its time in its cell; a running one has none yet.
  await expect(today.first()).toHaveText('11m');
  await expect(today.nth(4)).toHaveText('');
  await expect(page.locator('#tab-ci [data-lane-time]')).toHaveCount(0);
  await page.setViewportSize({width:390,height:844});
  const scroller = page.locator('#tab-ci .ci-lanes-scroll');
  // The day axis scrolls inside the card and opens on the newest day.
  await expect.poll(() => scroller.evaluate((el) => el.scrollWidth > el.clientWidth && el.scrollLeft + el.clientWidth >= el.scrollWidth - 2)).toBeTruthy();
  await expect(lanes.locator('.ci-lane-head b')).toHaveText(['PR', '主干', '合并队列', 'Daily', '版本']);
  await expect(lanes).not.toContainText(/工作流调用|其他工作流|未知触发/);
  const heads = await Promise.all(['pr','main','merge_queue','daily','release'].map(async (key) => (await lane(key).locator('.ci-lane-head').boundingBox())));
  for (const [i, box] of heads.entries()) {
    expect(box.x).toBeGreaterThanOrEqual(0);
    if (i) expect(box.y).toBeGreaterThan(heads[i - 1].y + 20);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.locator('#tab-ci .card:has(.ci-lanes)').screenshot({path:shot('ci-lanes-narrow')});
  // At 390 px the printed time still fits its cell.
  expect(await today.first().evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy();
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('[data-tab="tests"]').click();
  await expect(page.locator('#tab-tests')).toContainText('按包 / 目录分布');
  await expect(page.locator('#tab-tests')).toContainText('services/core');
  await expect(page.locator('#tab-tests')).not.toContainText('200.0%');
  await expect(page.locator('#tab-tests')).not.toContainText('undefined');
  await page.screenshot({path:shot('tests-desktop'),fullPage:true});
  await page.locator('[data-tab="coverage"]').click();
  await expect(page.locator('#tab-coverage')).toContainText('整仓行覆盖率');
  await expect(page.locator('#tab-coverage .tiles').first().locator('.tile')).toHaveCount(3);
  await expect(page.locator('#tab-coverage .tiles').first()).not.toContainText('Node.js 函数覆盖率');
  await expect(page.locator('#tab-coverage')).toContainText('80.7%');
  await expect(page.locator('#tab-coverage')).toContainText('Node.js');
  await expect(page.locator('#tab-coverage')).toContainText('83.0%');
  await expect(page.locator('#tab-coverage')).toContainText('Python');
  await expect(page.locator('#tab-coverage')).toContainText('65.4%');
  await expect(page.locator('#tab-coverage')).not.toContainText('口径');
  await expect(page.locator('#tab-coverage')).toContainText('目标为 main 的 PR 的 UT/ST 门禁实测范围');
  await expect(page.locator('#tab-coverage')).toContainText('门禁实测行覆盖率');
  const nodePrCoverage = page.locator('th[data-table="cov-prs-node"]').locator('xpath=ancestor::table');
  await expect(nodePrCoverage).toContainText('来源分支');
  await expect(nodePrCoverage).toContainText('目标分支');
  await expect(nodePrCoverage.getByRole('row').filter({ hasText: '#3' })).toContainText('fix-timeout');
  await expect(nodePrCoverage.getByRole('row').filter({ hasText: '#3' })).toContainText('releases/v0.3.0.beta');
  await expect(page.locator('#tab-coverage .coverage-trend')).toHaveCount(2);
  await expect(page.locator('#tab-coverage .coverage-trend').first().locator('.coverage-dot')).toHaveCount(5);
  await expect(page.locator('#tab-coverage .coverage-trend').first().locator('.coverage-x-label')).toHaveCount(7);
  await expect(page.locator('#tab-coverage .coverage-trend').first().locator('.coverage-y-label')).toHaveCount(5);
  await expect(page.locator('#tab-coverage .coverage-trend').first()).toContainText('9/17');
  await expect(page.locator('#tab-coverage .coverage-week-toolbar')).toContainText('最新 · 9/15–9/21');
  await expect(page.locator('#tab-coverage .coverage-trend').first()).not.toContainText('2026');
  await page.locator('#tab-coverage .coverage-trend').first().locator('.coverage-hit').last().hover();
  await expect(page.locator('#tooltip')).toContainText('83.0%');
  await expect(page.locator('#tooltip')).toContainText('nightly');
  await page.locator('[data-coverage-week="older"]').click();
  await expect(page.locator('#tab-coverage .coverage-trend').first().locator('.coverage-dot')).toHaveCount(4);
  await expect(page.locator('#tab-coverage .coverage-week-toolbar')).toContainText('9/14–9/20');
  await page.locator('[data-coverage-week="older"]').click();
  await expect(page.locator('#tab-coverage .coverage-trend').first().locator('.coverage-dot')).toHaveCount(2);
  await expect(page.locator('#tab-coverage .coverage-week-toolbar')).toContainText('9/7–9/13');
  await page.locator('[data-coverage-week="latest"]').click();
  await expect(page.locator('#tab-coverage .coverage-week-toolbar')).toContainText('最新 · 9/15–9/21');
  const tree=page.locator('#tab-coverage .cov-tree[data-language="node"]');
  await expect(tree.locator('.cov-dir').first()).toContainText('packages/core/src/');
  await expect(tree.locator('.cov-dir').first()).toContainText('62.3%');
  await page.locator('[data-cov-expand="node"]').click();
  await expect(tree.locator('.cov-file', {hasText:'strings.ts'})).toContainText('90.0%');
  await expect(tree.locator('.cov-dir[data-cov-path="node:packages/core/src/util"] > summary')).toContainText('11 / 20 · 2 个文件');
  await page.locator('[data-cov-collapse="node"]').click();
  await expect(tree.locator('.cov-file', {hasText:'strings.ts'})).toBeHidden();
  await page.locator('[data-filter="coverageQ"]').first().fill('numbers');
  await expect(tree.locator('.cov-file')).toHaveCount(1);
  await expect(tree.locator('.cov-file')).toContainText('20.0%');
  await page.locator('[data-filter="coverageQ"]').first().fill('');
  // The tree replaced the per-group table and carries one source line for the latest data.
  await expect(page.locator('th[data-table^="cov-groups"]')).toHaveCount(0);
  await expect(page.locator('#tab-coverage')).not.toContainText('路径覆盖率');
  await expect(page.locator('#tab-coverage .section-head', {hasText:'Node.js 目录覆盖率'})).toContainText('4 个文件，逐层展开到文件 · 最新数据：');
  await expect(page.locator('#tab-coverage .section-head', {hasText:'Node.js 目录覆盖率'})).toContainText('提交 aaaaaaaaaa');
  // Lowest line coverage first, remembered across a reload.
  const pyFiles=page.locator('#tab-coverage .cov-tree[data-language="python"] .cov-file .cov-name');
  await page.locator('[data-cov-expand="python"]').click();
  await expect(pyFiles).toHaveText(['auth.py','candidates.py']);
  await page.locator('[data-cov-sort="lines"]').first().click();
  await expect(page.locator('[data-cov-sort="lines"]').first()).toHaveClass(/on/);
  await page.locator('[data-cov-expand="python"]').click();
  await expect(pyFiles).toHaveText(['candidates.py','auth.py']);
  await page.reload();
  await expect(page.locator('[data-cov-sort="lines"]').first()).toHaveClass(/on/);
  await page.locator('[data-cov-sort="name"]').first().click();
  await page.screenshot({path:shot('coverage-desktop'),fullPage:true});

});

test('tagged dimensions, profile combinations and never-covered cases', async ({ page }) => {
  await page.goto('/github-status-board/#tests');
  const tab = page.locator('#tab-tests');
  await expect(tab).toContainText('标签化测试');
  await expect(tab).toContainText('349 个用例 · 8 个标签维度 · 10 种标签组合');
  await expect(tab.locator('.tile', { hasText: '从未覆盖' })).toContainText('25');
  await expect(tab.locator('.tile', { hasText: 'PR 选中' })).toContainText('324');
  await expect(tab.locator('.tile', { hasText: 'Release 选中' })).toContainText('暂无运行');
  const rulesText = tab.locator('.rule-list');
  await expect(rulesText).toContainText('PR：category ∈ {ut, st, e2e} 且 os = linux 且 arch = amd64');
  await expect(rulesText).toContainText('Daily：与 PR 规则相同');
  await expect(rulesText).toContainText('Release：暂无运行，无法读取组合');
  const matrix = tab.locator('.tag-matrix');
  await expect(matrix.locator('.dim-row')).toHaveCount(8);
  await expect(matrix.locator('.dim-row').first()).toContainText('category 单选 · 词表 3 个 · 使用 3 个');
  const takes = (tag) => matrix.locator(`tr[data-tag="${tag}"] .take`);
  await expect(takes('status:reviewed').first()).toHaveText('✓');
  await expect(takes('os:macos').first()).toHaveText('✗');
  await expect(takes('sandbox:seatbelt').first()).toHaveText('不限');
  await expect(takes('os:macos')).toHaveCount(2); // Release has no run, so its column stays empty
  await expect(matrix.locator('tr[data-tag="os:windows"]')).toHaveClass(/unused/);
  await expect(matrix.locator('tr[data-tag="status:external"]')).toContainText('未覆盖 12');
  await matrix.locator('tr[data-tag="status:external"] .tag-bar').hover();
  await expect(page.locator('#tooltip')).toContainText('从未覆盖 12');
  const uncovered = tab.locator('.card', { hasText: '从未覆盖的用例' });
  await expect(uncovered.locator('.bars')).toContainText('status=external');
  await expect(uncovered.locator('.bars')).toContainText('os=macos；arch=arm64');
  await uncovered.locator('summary').click();
  await expect(uncovered).toContainText('services/paper/tests/test_external.py::case 1');
  const combos = tab.locator('.card', { hasText: '标签组合' }).locator('tbody tr');
  await expect(combos).toHaveCount(10);
  await expect(combos.first()).toContainText('280');
  await page.mouse.move(0, 0);
  await tab.locator('.card:has(.tag-matrix)').screenshot({ path: shot('tagged-matrix-desktop') });
  await tab.locator('.grid.wide:has(.tag-chip)').screenshot({ path: shot('tagged-uncovered-desktop') });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  const wrap = matrix.locator('xpath=..');
  expect(await wrap.evaluate((el) => el.scrollWidth >= el.clientWidth)).toBeTruthy();
  await tab.locator('.card:has(.tag-matrix)').screenshot({ path: shot('tagged-matrix-narrow') });
});

test('Real E2E cases show run marks and score curves inline like the E2E run records', async ({ page }) => {
  await page.goto('/github-status-board/#tests');
  const tab = page.locator('#tab-tests');
  // Same layout as the mocked run records: category → case → one mark per run; no dialog to open.
  const families = tab.locator('[data-real-family]');
  await expect(families).toHaveCount(4);
  await expect(families.locator('.e2e-category-toggle')).toHaveText([/DeepResearchBench\s*5 个用例/, /BiomniBench\s*2 个用例/, /Research Team\s*1 个用例/, /PUCT Compression\s*1 个用例/]);
  await expect(tab.locator('[data-real-item]')).toHaveCount(9);
  await expect(page.locator('#score-trend-dialog')).toHaveCount(0);
  await expect(tab.locator('[data-score-case]')).toHaveCount(0);
  const realCase = (name) => tab.locator(`[data-real-item="${name}"]`);

  const drb = realCase('DRB-59');
  await expect(drb.locator('.e2e-case-head')).toContainText('12 次执行');
  await expect(drb.locator('.e2e-case-head .badge')).toHaveText('失败');
  await expect(drb.locator('.e2e-case-file')).toHaveText('test/deepresearchbench-swarm.spec.ts');
  await expect(drb.locator('.e2e-history-point')).toHaveCount(12);
  await expect(drb.locator('.e2e-history-point.passed .e2e-status-mark')).toHaveText(Array(11).fill('✓'));
  await expect(drb.locator('.e2e-history-point.failed .e2e-status-mark')).toHaveText(['×']);
  // Marks run oldest → newest; each links to its run and names date, result, scores and time.
  const last = drb.locator('.e2e-history-point').last().locator('a');
  await expect(last).toHaveAttribute('href', /\/actions\/runs\/\d+\/artifacts\/210$/);
  await expect(last).toHaveAttribute('aria-label', /交付失败 · .*RACE 0\.\d{4}（沿用上次实测）.*耗时/);
  await last.hover();
  await expect(page.locator('#tooltip')).toContainText('交付失败');

  // Every metric plus the run time is a curve under the case, with its latest value.
  const trends = drb.locator('.real-trend');
  await expect(trends.locator('figcaption > span:first-child')).toHaveText(['RACE', 'Citation accuracy', 'Verification coverage', '运行耗时']);
  await expect(trends.first().locator('figcaption')).toContainText('（沿用上次实测）');
  await expect(trends.first().locator('.score-dot')).toHaveCount(11);
  await expect(trends.first().locator('.score-x')).toHaveCount(1);
  await expect(trends.first().locator('.score-line.carried')).toHaveCount(1);
  await expect(trends.first().locator('.score-x title')).toContainText('交付失败，沿用上次实测');
  const carried = await trends.first().evaluate((figure) => {
    const measured = [...figure.querySelectorAll('.score-dot')].at(-1);
    const { e: x, f: y } = figure.querySelector('.score-x').transform.baseVal.consolidate().matrix;
    const line = figure.querySelector('.score-line.carried');
    const start = line.getPointAtLength(0), end = line.getPointAtLength(line.getTotalLength());
    return { previous: { x: Number(measured.getAttribute('cx')), y: Number(measured.getAttribute('cy')) },
      cross: { x, y }, start: { x: start.x, y: start.y }, end: { x: end.x, y: end.y }, dash: getComputedStyle(line).strokeDasharray };
  });
  expect(carried.cross.y).toBeCloseTo(carried.previous.y, 1);
  expect(carried.cross.x).toBeGreaterThan(carried.previous.x);
  expect(carried.start.x).toBeCloseTo(carried.previous.x, 1);
  expect(carried.start.y).toBeCloseTo(carried.previous.y, 1);
  expect(carried.end.x).toBeCloseTo(carried.cross.x, 1);
  expect(carried.end.y).toBeCloseTo(carried.cross.y, 1);
  expect(carried.dash).not.toBe('none');
  await expect(trends.last().locator('.score-dot')).toHaveCount(12);
  await expect(trends.last().locator('.score-x')).toHaveCount(0);

  // Score curves start at the first measurement; the earlier failure remains on the status axis.
  const team = realCase('TC-E2E-01');
  await expect(team.locator('.e2e-history-point').first()).toHaveClass(/failed/);
  const teamScore = team.locator('.real-trend').first();
  await expect(teamScore.locator('.score-dot')).toHaveCount(11);
  await expect(teamScore.locator('.score-x')).toHaveCount(0);
  await expect(teamScore.locator('.score-line')).toHaveCount(10);
  await expect(teamScore.locator('.score-dot').first().locator('title')).toContainText('run 806');
  await expect(teamScore.locator('title').filter({ hasText: 'run 804' })).toHaveCount(0);
  await expect(team.locator('.real-trend').last().locator('.score-dot')).toHaveCount(12);
  // Later missing scores use a cross at the last measured height and a dashed bridge, never zero.
  const puct = realCase('PUCT-COMPRESS');
  await expect(puct.locator('.real-trend')).toHaveCount(3);
  const missingScore = puct.locator('.real-trend').nth(1);
  await expect(missingScore.locator('.score-dot')).toHaveCount(11);
  await expect(missingScore.locator('.score-x')).toHaveCount(1);
  await expect(missingScore.locator('.score-line')).toHaveCount(10);
  await expect(missingScore.locator('.score-line.gap')).toHaveCount(1);
  const gap = await missingScore.evaluate((figure) => {
    const { e: x, f: y } = figure.querySelector('.score-x').transform.baseVal.consolidate().matrix;
    const measured = [...figure.querySelectorAll('.score-dot')].map((dot) => [Number(dot.getAttribute('cx')), Number(dot.getAttribute('cy'))]);
    const previous = measured.findLast((point) => point[0] < x), next = measured.find((point) => point[0] > x);
    const line = figure.querySelector('.score-line.gap');
    return { x, y, previous, next, path: line.getAttribute('d').match(/-?\d+(?:\.\d+)?/g).map(Number), dash: getComputedStyle(line).strokeDasharray };
  });
  expect(gap.y).toBeCloseTo(gap.previous[1], 1);
  const expectedGap = [...gap.previous, gap.x, gap.previous[1], ...gap.next];
  expect(gap.path).toHaveLength(expectedGap.length);
  expectedGap.forEach((value, index) => expect(gap.path[index]).toBeCloseTo(value, 1));
  expect(gap.dash).not.toBe('none');
  await missingScore.locator('.score-x').hover();
  await expect(page.locator('#tooltip')).toContainText('评分缺失');
  await expect(page.locator('#tooltip')).toContainText('不是本次测量值');

  // Scores stay inside each chart's own scale.
  const rubric = realCase('BiomniBench-da-14-1').locator('.real-trend').first().locator('svg');
  const plotted = await rubric.evaluate((svg) => ({
    height: svg.viewBox.baseVal.height,
    dots: [...svg.querySelectorAll('circle')].map((dot) => ({ y: Number(dot.getAttribute('cy')), score: Number(dot.querySelector('title').textContent.match(/Rubric ([\d.]+)/)[1]) })),
  }));
  expect(plotted.dots).toHaveLength(12);
  expect(plotted.dots.every((dot) => dot.score >= 0 && dot.score <= 100 && dot.y >= 0 && dot.y <= plotted.height)).toBeTruthy();

  // Families collapse like run-record categories, and stay collapsed when the page refreshes its data.
  const biomni = families.nth(1).locator('.e2e-category-toggle');
  await biomni.click();
  await expect(biomni).toHaveAttribute('aria-expanded', 'false');
  await expect(realCase('BiomniBench-da-13-3')).toBeHidden();
  await page.locator('#refresh-btn').click();
  await expect(page.locator('#refresh-btn')).toBeEnabled();
  await expect(families.nth(1).locator('.e2e-category-toggle')).toHaveAttribute('aria-expanded', 'false');
  await families.nth(1).locator('.e2e-category-toggle').click();
  await expect(realCase('BiomniBench-da-13-3')).toBeVisible();

  await page.mouse.move(0, 0);
  await drb.screenshot({ path: shot('real-e2e-case-desktop') });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expectTimelineFits(page, drb.locator('.e2e-timeline'));
  expect(await drb.locator('.real-trend svg').evaluateAll((charts) => charts.every((svg) => {
    const box = svg.getBoundingClientRect();
    return box.width > 200 && box.right <= innerWidth;
  }))).toBeTruthy();
  await drb.screenshot({ path: shot('real-e2e-case-narrow') });
});

test('Real E2E history remains visible when the latest report falls outside the current run window', async ({ page }) => {
  await page.route('**/data/snapshot.json*', async (route) => {
    const response = await route.fetch();
    const snapshot = await response.json();
    const tests = snapshot.sections.tests.data;
    tests.score_report = null;
    tests.executed = tests.executed.filter((entry) => !entry.artifact.startsWith('real-e2e-results'));
    await route.fulfill({ response, json: snapshot });
  });
  await page.goto('/github-status-board/#tests');
  const tab = page.locator('#tab-tests');
  await expect(tab).toContainText('最新 Real E2E 评分');
  await expect(tab).toContainText('以下仍显示已保存的逐用例历史');
  await expect(tab.locator('[data-real-item]')).toHaveCount(9);
  await expect(tab.locator('[data-real-item="DRB-59"] .score-chart')).toHaveCount(4);
  await tab.locator('[data-real-item="DRB-59"] .real-case-link').click();
  await expect(page).toHaveURL(/real-e2e\.html\?line=main&case=DRB-59$/);
  await expect(page.locator('.history-table .real-history-row')).toHaveCount(12);
});

test('Real E2E case names open branch-specific run details', async ({ page }) => {
  await page.goto('/github-status-board/#tests');
  await page.getByRole('link', { name: '查看 DRB-59 的 Real E2E 详情' }).click();
  await expect(page).toHaveURL(/real-e2e\.html\?line=main&case=DRB-59$/);
  await expect(page.getByRole('heading', { name: '旅程报告 · DRB-59', exact: true })).toBeVisible();
  await expect(page.locator('.history-table .real-history-row')).toHaveCount(12);
  await expect(page.locator('.history-table .badge.failed')).toHaveCount(1);
  await expect(page.locator('.metric-table thead')).toContainText('最新');
  await expect(page.locator('.metric-table thead')).toContainText('平均（实测）');
  await expect(page.locator('.metric-table tbody tr').first()).toContainText('沿用上次实测');
  await expect(page.locator('.real-run-card')).toHaveCount(0);
  for (const heading of ['场景目标', '前置条件', '步骤总览', '运行元数据']) {
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
  }
  await expect(page.getByText('Research DRB-59 and evaluate both delivery and report quality.')).toBeVisible();
  await expect(page.getByText('isolated Swarm stack', { exact: true })).toBeVisible();
  await expect(page.getByText('本次报告没有记录独立的用户步骤。')).toBeVisible();
  await expect(page.locator('.report-meta').last()).toContainText('E2E_API_TOKEN（仅变量名）');
  await page.getByRole('link', { name: '← 返回 E2E 看板' }).click();
  await expect(page.locator('#tab-tests')).toBeVisible();
  await expect(page.locator('#tab-tests .line-switch [data-line="main"]')).toHaveClass(/on/);
  const caseLink = page.getByRole('link', { name: '查看 DRB-59 的 Real E2E 详情' });
  await expect.poll(() => caseLink.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return rect.top >= 0 && rect.bottom <= innerHeight;
  })).toBeTruthy();
  await expect(page.locator('[data-real-item="DRB-59"] .e2e-history-point')).toHaveCount(12);
});

const wakeTitle = '后台执行完成后显示运行时提示而不是伪装成用户消息，并且保留项目保存结果和完整的任务执行记录供后续查看';
const wakeCase = page => page.locator('[data-e2e-slice="mocked-standard"] .e2e-case')
  .filter({ has: page.getByRole('heading', { name: wakeTitle, exact: true }) })
  .filter({ has: page.locator('.e2e-case-file code', { hasText: /^journey-first-run\.spec\.ts$/ }) });

// Check actual point geometry: hiding overflow must not masquerade as fitting the axis.
async function expectTimelineFits(page, timeline) {
  expect(await timeline.evaluate(el => {
    const box = el.getBoundingClientRect(), style = getComputedStyle(el);
    return el.scrollWidth <= el.clientWidth && el.scrollHeight <= el.clientHeight
      && !['scroll','auto'].includes(style.overflowX) && !['scroll','auto'].includes(style.overflowY)
      && [...el.querySelectorAll('.e2e-point')].every(point => {
        const p = point.getBoundingClientRect();
        return p.width > 0 && p.height > 0 && p.left >= box.left && p.right <= box.right
          && p.top >= box.top && p.bottom <= box.bottom;
      });
  })).toBeTruthy();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
}

test('E2E run records show categories and case timelines without opening runs', async ({ page }) => {
  const errors = [], requests = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => requests.push(r.url()));
  await page.goto('/github-status-board/#tests');
  const records = page.locator('#e2e-records'), subject = wakeCase(page);
  await expect(records.locator('.section-head')).toContainText('2 个类别 · 7 个用例');
  await expect(records.locator(':scope > .e2e-category')).toHaveCount(2);
  await expect(records.locator('.e2e-category-toggle')).toHaveText(['mocked-literature 2 个用例', 'mocked-standard 5 个用例']);
  await expect(records.locator('[data-e2e-run], [data-e2e-record]')).toHaveCount(0);
  await expect(page.locator('#e2e-dialog, dialog[open]')).toHaveCount(0);
  await expect(subject).toHaveCount(1);
  await expect(subject.getByRole('heading', { name: wakeTitle, exact: true })).toBeVisible();
  // Same title in another file or slice remains a separate case, not another execution.
  await expect(records.getByRole('heading', { name: wakeTitle, exact: true })).toHaveCount(3);
  const points = subject.locator('.e2e-history-point');
  await expect(points).toHaveCount(4);
  expect(await points.evaluateAll(els => els.map(el => el.dataset.e2eArtifact))).toEqual(['7001','7004','7005','7003']);
  const descriptions = await points.locator('.e2e-point').evaluateAll(els => els.map(el => el.getAttribute('aria-label')));
  for (const [i, status, duration, time] of [[0,'失败','9.2s','19:00:00'],[1,'重试通过','640ms','22:00:00'],[2,'跳过','0ms','23:00:00'],[3,'通过','2.3s','00:00:00']]) {
    expect(descriptions[i]).toContain(status);
    expect(descriptions[i]).toContain(`耗时 ${duration}`);
    expect(descriptions[i]).toContain(time);
  }
  expect(new Set(await points.locator('.e2e-status-mark').evaluateAll(els => els.map(el => getComputedStyle(el).backgroundColor))).size).toBe(4);
  // Details are available on both pointer hover and keyboard focus, not inside cards.
  await points.first().locator('.e2e-point').hover();
  await expect(page.locator('#tooltip')).toBeVisible();
  await expect(page.locator('#tooltip')).toHaveText(descriptions[0]);
  expect(descriptions[0]).toContain('2026/9/19 19:00:00');
  expect(descriptions[0]).toMatch(/ · .+ 10/);
  await page.mouse.move(0, 0);
  await points.nth(2).locator('.e2e-point').focus();
  await expect(points.nth(2).locator('.e2e-point')).toBeFocused();
  await expect(page.locator('#tooltip')).toBeVisible();
  await expect(page.locator('#tooltip')).toHaveText(descriptions[2]);
  await page.keyboard.press('Tab');
  await expect(points.last().locator('.e2e-point')).toBeFocused();
  await expect(page.locator('#tooltip')).toHaveText(descriptions[3]);
  await expect(points.nth(2).locator('a')).toHaveCount(0); // skipped without an HTML attachment
  await expect(points.nth(3).locator('a')).toHaveCount(0); // fork's HTML is not hosted
  await expect(records.locator('a[href*="/index.html"]')).toHaveCount(0);
  const report = points.first().locator('a');
  const reportUrl = new URL(await report.getAttribute('href'), page.url());
  expect(reportUrl.pathname).toBe('/github-status-board/e2e-report.html');
  expect(reportUrl.searchParams.get('src')).toBe('e2e/7001/mocked-standard/data/40072e79cd3d0cda7a79c6bad7501851b4babf54.html');
  expect(reportUrl.searchParams.get('line')).toBe('main');
  const [tab] = await Promise.all([page.waitForEvent('popup'), report.click()]);
  const detail = tab.frameLocator('#report-frame');
  await expect(detail.locator('h1')).toHaveText('后台执行完成后显示运行时提示');
  await expect(detail.locator('img')).toHaveCount(3);
  await expect.poll(() => detail.locator('img').evaluateAll(imgs => imgs.every(img => img.complete && img.naturalWidth > 0))).toBeTruthy();
  for (const src of await detail.locator('img').evaluateAll(imgs => imgs.map(img => img.src))) {
    expect(decodeURI(src)).toContain('/journey-reports/issue-77-wake-notice/');
    expect((await page.request.get(src)).status()).toBe(200);
  }
  await tab.getByRole('link', { name: '返回 E2E 看板' }).click();
  await expect(tab.locator('#tab-tests .e2e-case').filter({ has: tab.locator('h4', { hasText: '后台执行完成后显示运行时提示' }) }).first()).toBeInViewport();
  await tab.close();
  await expect(records.locator('.e2e-case-details, .e2e-attempt, .e2e-error, .e2e-steps')).toHaveCount(0);
  await expect(records).not.toContainText('步骤与错误摘要');
  await expect(records).not.toContainText('Error: expect(locator)');
  await records.getByLabel('按最新结果筛选').selectOption('flaky');
  await expect(records.locator('.e2e-case')).toHaveCount(1);
  await expect(records.locator('.e2e-case-title')).toHaveText('恢复会话');
  await records.getByLabel('按最新结果筛选').selectOption('');
  await records.getByLabel('搜索用例').fill('journey-other');
  await expect(records.locator('.e2e-case')).toHaveCount(1);
  await expect(records.locator('.e2e-history-point')).toHaveCount(1);
  await records.getByLabel('搜索用例').fill('');
  await expect(records.locator('.e2e-case')).toHaveCount(7);
  await records.evaluate(el => scrollTo(0, scrollY + el.getBoundingClientRect().top - 110));
  await page.screenshot({ path: shot('e2e-case-history-desktop') });

  await subject.evaluate(el => scrollTo(0, scrollY + el.getBoundingClientRect().top - 110));
  await page.screenshot({ path: shot('e2e-case-timeline-desktop') });
  await page.setViewportSize({ width: 390, height: 844 });
  await subject.evaluate(el => scrollTo(0, scrollY + el.getBoundingClientRect().top - 130));
  const title = subject.locator('.e2e-case-title');
  await expect(title).toHaveText(wakeTitle);
  expect(await title.evaluate(el => el.scrollHeight <= el.clientHeight && getComputedStyle(el).whiteSpace === 'normal' && getComputedStyle(el).textOverflow !== 'ellipsis')).toBeTruthy();
  const timeline = subject.locator('.e2e-timeline');
  await expectTimelineFits(page, timeline);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  const box = await timeline.boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: shot('e2e-case-history-mobile') });
  await expect(points.last().locator('.e2e-point')).toBeVisible();
  // Expired and oversized records never produce a point or a steps request.
  await expect(records.locator('[data-e2e-artifact="7002"], [data-e2e-artifact="7007"]')).toHaveCount(0);
  expect(requests.some(url => /data\/e2e\/(7002|7007)\.json/.test(url))).toBeFalsy();
  expect(errors).toEqual([]);
});

test('E2E run records remember category collapse and reveal filtered matches', async ({ page }) => {
  await page.clock.install();
  await page.goto('/github-status-board/#tests');
  const records = page.locator('#e2e-records');
  const standard = records.locator('[data-e2e-slice="mocked-standard"]');
  const literature = records.locator('[data-e2e-slice="mocked-literature"]');
  const toggle = standard.getByRole('button', { name: 'mocked-standard 5 个用例' });
  await expect(records.locator('.e2e-case')).toHaveCount(7);
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(standard.locator('.e2e-case').first()).toBeHidden();
  await expect(literature.locator('.e2e-case').first()).toBeVisible();
  // Both manual refresh and the real 60-second poll retain the closed category.
  await page.locator('#refresh-btn').click();
  await expect(page.locator('#refresh-btn')).toBeEnabled();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  const polled = page.waitForResponse(r => r.url().includes('/data/e2e/index.json'));
  await page.clock.fastForward(60_001);
  await polled;
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  // Rendering a search result temporarily expands the matching category.
  await records.getByLabel('搜索用例').fill('journey-first-run');
  await expect(standard.locator('.e2e-category-toggle')).toHaveAttribute('aria-expanded', 'true');
  await expect(wakeCase(page)).toBeVisible();
  await records.getByLabel('搜索用例').fill('');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await records.getByLabel('按最新结果筛选').selectOption('passed');
  await expect(standard.locator('.e2e-category-toggle')).toHaveAttribute('aria-expanded', 'true');
  await expect(wakeCase(page)).toBeVisible();
  await records.getByLabel('按最新结果筛选').selectOption('');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toBeFocused();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(wakeCase(page)).toBeVisible();
  await page.keyboard.press('Space');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await records.evaluate(el => scrollTo(0, scrollY + el.getBoundingClientRect().top - 110));
  await page.mouse.move(0, 0);
  await page.screenshot({ path: shot('e2e-categories-collapsed') });
});

test('E2E run records show every retained point without scrolling on desktop and narrow screens', async ({ page }) => {
  const { readFileSync } = require('node:fs');
  const read = file => JSON.parse(readFileSync(resolve(__dirname, '../.e2e/site/github-status-board/data/e2e', file), 'utf8'));
  const index = read('index.json'), source = index.records.find(r => r.artifact_id === 7001), steps = read('7001.json');
  const ids = Array.from({length: 20}, (_, i) => 8000 + i);
  // Expand only the already-published browser fixture, without changing collector limits.
  index.records = ids.map((id, i) => ({...source, artifact_id: id, run_id: id, steps: `data/e2e/${id}.json`, reports: [],
    created_at: new Date(Date.UTC(2026, 8, i + 1, 11)).toISOString()})).reverse();
  await page.route('**/data/snapshot.json*', async route => {
    const response = await route.fetch(), doc = await response.json();
    const runs = doc.sections.ci.data.recent_runs;
    runs.push(...ids.map(id => ({...runs[0], id})));
    await route.fulfill({response, json: doc});
  });
  await page.route('**/data/e2e/index.json*', route => route.fulfill({json: index}));
  await page.route(/\/data\/e2e\/80\d\d\.json$/, route => {
    const id = Number(route.request().url().match(/(80\d\d)\.json$/)[1]);
    const doc = structuredClone(steps), c = doc.slices.find(s => s.slice === 'mocked-standard').cases[0];
    c.status = ['failed','flaky','skipped','passed'][id % 4];
    c.duration_ms = id === 8019 ? null : 1234;
    return route.fulfill({json: doc});
  });
  await page.goto('/github-status-board/#tests');
  const subject = wakeCase(page), timeline = subject.locator('.e2e-timeline'), points = timeline.locator('.e2e-point');
  await expect(points).toHaveCount(20);
  expect(await timeline.locator('.e2e-history-point').evaluateAll(els => els.map(el => Number(el.dataset.e2eArtifact)))).toEqual(ids);
  await expect(points.last()).toHaveAttribute('aria-label', /耗时 —/);
  await expect(timeline.locator('a')).toHaveCount(0);
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: width === 390 ? 844 : 1000});
    await subject.evaluate(el => scrollTo(0, scrollY + el.getBoundingClientRect().top - 110));
    await expectTimelineFits(page, timeline);
    const title = subject.locator('.e2e-case-title');
    expect(await title.evaluate(el => el.scrollWidth <= el.clientWidth && el.scrollHeight <= el.clientHeight
      && getComputedStyle(el).whiteSpace === 'normal' && getComputedStyle(el).textOverflow !== 'ellipsis')).toBeTruthy();
    if (width === 390) expect(await points.last().evaluate(el => el.offsetTop)).toBeGreaterThan(await points.first().evaluate(el => el.offsetTop));
    await points.nth(18).focus();
    await page.keyboard.press('Tab');
    await expect(points.last()).toBeFocused();
    await expect(page.locator('#tooltip')).toHaveText(await points.last().getAttribute('aria-label'));
    await expect(page.locator('#tooltip')).toBeVisible();
    expect(await page.locator('#tooltip').evaluate(el => {
      const b = el.getBoundingClientRect(); return b.left >= 0 && b.right <= innerWidth;
    })).toBeTruthy();
    await page.screenshot({ path: shot(`e2e-all-points-${width}`) });
  }
});

test('E2E run records isolate missing steps and branch histories', async ({ page }) => {
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/data/e2e/7004.json', route => route.fulfill({ status: 404, body: 'missing' }));
  await page.goto('/github-status-board/#tests');
  const records = page.locator('#e2e-records');
  await expect(wakeCase(page).locator('.e2e-history-point')).toHaveCount(3);
  await expect(records).toContainText('1 份执行记录暂不可读取');
  await page.locator('#tab-tests [data-line="release"]').click();
  await expect(wakeCase(page).locator('.e2e-history-point')).toHaveCount(1);
  await expect(wakeCase(page).locator('.e2e-history-point')).toHaveAttribute('data-e2e-artifact', '7006');
  await page.locator('#tab-tests [data-line="legacy"]').click();
  await expect(records.locator('.e2e-case')).toHaveCount(0);
  await expect(records).toContainText('暂时没有可展示的用例执行历史');
  await page.locator('#tab-tests [data-line="main"]').click();
  await expect(wakeCase(page).locator('.e2e-history-point')).toHaveCount(3);
  await page.unroute('**/data/e2e/7004.json');
  await page.locator('#refresh-btn').click();
  await expect(wakeCase(page).locator('.e2e-history-point')).toHaveCount(4);
  await expect(records).not.toContainText('暂不可读取');
  expect(errors).toEqual([]);
});

test('E2E run records reject unsafe case paths and unavailable bundles', async ({ page }) => {
  await page.route('**/data/e2e/7001.json', async route => {
    const response = await route.fetch(), doc = await response.json();
    const slice = doc.slices.find(s => s.slice === 'mocked-standard');
    const original = slice.cases[0];
    slice.cases = ['../secret.html', '/data/report.html', 'data/../secret.html', 'data/x\\secret.html', 'data/x\u0001.html'].map(html => ({...original, html}));
    await route.fulfill({response, json: doc});
  });
  await page.goto('/github-status-board/#tests');
  const badPoints = wakeCase(page).locator('[data-e2e-artifact="7001"]');
  await expect(badPoints).toHaveCount(5);
  await expect(badPoints.locator('a')).toHaveCount(0);
  await page.unroute('**/data/e2e/7001.json');
  await page.route('**/data/e2e/index.json*', async route => {
    const response = await route.fetch(), doc = await response.json();
    doc.records.find(r => r.artifact_id === 7001).reports.forEach(rep => { rep.html = null; rep.reason = 'unavailable'; });
    await route.fulfill({response, json: doc});
  });
  await page.reload();
  await expect(badPoints).toHaveCount(1);
  await expect(badPoints.locator('a')).toHaveCount(0);
  await expect(wakeCase(page).locator('[data-e2e-artifact="7004"] a')).toHaveCount(1);
});

test('E2E run records reuse published JSON and refresh changed versions', async ({ page }) => {
  let upgraded = false, reads = 0;
  await page.route('**/data/e2e/index.json*', async route => {
    const response = await route.fetch(), doc = await response.json();
    doc.records.find(r => r.artifact_id === 7001).version = upgraded ? 3 : 2;
    await route.fulfill({response, json: doc});
  });
  await page.route('**/data/e2e/7001.json', async route => {
    const response = await route.fetch(), doc = await response.json();
    reads++;
    if (!upgraded) doc.slices.forEach(s => s.cases.forEach(c => { delete c.html; }));
    await route.fulfill({response, json: doc});
  });
  await page.goto('/github-status-board/#tests');
  const point = wakeCase(page).locator('[data-e2e-artifact="7001"]');
  await expect(point).toHaveCount(1);
  await expect(point.locator('a')).toHaveCount(0);
  await page.locator('#refresh-btn').click();
  await expect(page.locator('#refresh-btn')).toBeEnabled();
  expect(reads).toBe(1);
  upgraded = true;
  await page.locator('#refresh-btn').click();
  // The upgraded record's point opens its HTML report through the report viewer.
  await expect(point.locator('a')).toHaveAttribute('href', /^\.\/e2e-report\.html\?src=e2e%2F7001%2Fmocked-standard%2Fdata%2F40072e79cd3d0cda7a79c6bad7501851b4babf54\.html&/);
  expect(reads).toBe(2);
});

test('CI, tests and coverage switch between main, legacy and release without mixing', async ({ page }) => {
  await page.goto('/github-status-board/#ci');
  const ci = page.locator('#tab-ci'), rows = ci.locator('.ci-lanes .ci-lane-row:not(.ci-axis)');
  const switchOf = (tab) => page.locator(`#tab-${tab} .line-switch`);
  await expect(switchOf('ci').locator('button')).toHaveText(['main', 'legacy', 'releases/v0.3.0.beta']);
  await expect(switchOf('ci').locator('button.on')).toHaveText('main');
  await expect(rows).toHaveCount(5);
  await expect(ci.locator('.ci-lanes')).not.toContainText('legacy');
  await expect(ci.locator('.ci-lanes .ci-run[href$="/runs/602"]')).toHaveCount(0);
  await switchOf('ci').getByRole('button', { name: 'legacy' }).click();
  await expect(rows).toHaveCount(5);
  await expect(rows.nth(1).locator('.ci-lane-head b')).toHaveText('legacy');
  await expect(rows.nth(1).locator('.ci-run')).toHaveAttribute('href', /\/runs\/610$/);
  await expect(rows.filter({has: page.locator('[href$="/runs/611"]')})).toHaveAttribute('data-lane', 'daily');
  await expect(ci.locator('.ci-lanes .ci-run[href$="/runs/602"]')).toHaveCount(0);
  await page.locator('[data-tab="tests"]').click();
  await expect(switchOf('tests').locator('button.on')).toHaveText('legacy');
  await expect(page.locator('#tab-tests')).not.toContainText('323 个用例');
  await page.locator('[data-tab="ci"]').click();
  await switchOf('ci').getByRole('button', { name: 'releases/v0.3.0.beta' }).click();
  // Nightly stays in Daily; hidden categories have no rows or fallback run links.
  await expect(rows).toHaveCount(5);
  await expect(rows.nth(1).locator('.ci-lane-head b')).toHaveText('releases/v0.3.0.beta');
  await expect(rows.nth(1).locator('.ci-lane-head')).toContainText('CI · releases/v0.3.0.beta push / 手动');
  await expect(rows.first().locator('.ci-run')).toHaveCount(2);
  await expect(rows.nth(1).locator('.ci-run.failure')).toHaveAttribute('href', /\/runs\/602$/);
  await expect(ci.locator('.ci-lanes .ci-run[href$="/runs/500"]')).toHaveCount(0);
  await expect(ci.locator('.tile').first()).toContainText('releases/v0.3.0.beta 分支成功率');
  await expect(ci).toContainText('Run unit tests');
  const nightly = ci.locator('[data-lane="daily"] .ci-run');
  await expect(nightly).toHaveAttribute('href', /\/runs\/37845266203$/);
  await expect(nightly).toHaveAttribute('target', '_blank');
  await nightly.focus();
  await expect(page.locator('#tooltip')).toContainText('Nightly · workflow_dispatch · 手动');
  await expect(rows.locator('.ci-lane-head b')).toHaveText(['PR', 'releases/v0.3.0.beta', '合并队列', 'Daily', '版本']);
  await expect(ci.locator('[data-lane="merge_queue"]')).toContainText('窗口内没有 run');
  await expect(ci.locator('[data-lane="merge_queue"]')).not.toContainText('失败 1');
  await expect(ci).not.toContainText(/工作流调用|其他工作流|未知触发/);
  await expect(ci.locator('a[href$="/runs/603"], a[href$="/runs/604"]')).toHaveCount(0);
  const recent = ci.locator('th[data-table="ci-runs"]').first().locator('xpath=ancestor::table').locator('tbody tr');
  await expect(recent).toHaveCount(4);
  await page.mouse.move(0, 0);
  await page.screenshot({ path: shot('line-ci-release-desktop') });
  // The choice holds on the test and coverage pages and across a reload.
  await page.locator('[data-tab="tests"]').click();
  const tests = page.locator('#tab-tests');
  await expect(switchOf('tests').locator('button.on')).toHaveText('releases/v0.3.0.beta');
  await expect(tests).toContainText('releases/v0.3.0.beta 最新 CI 冻结的用例目录 · 323 个用例');
  await expect(tests.locator('.tile', { hasText: 'Daily 选中' })).toHaveCount(0);
  await expect(tests.locator('.tag-matrix thead th.take-col')).toHaveCount(1);
  await page.locator('[data-tab="coverage"]').click();
  // This line's summaries have no per-file totals: the tree stops at its own groups.
  const swarmCov = page.locator('#tab-coverage');
  await expect(swarmCov.locator('.section-head', { hasText: 'Node.js 目录覆盖率' })).toContainText('2 个覆盖率分组；源仓摘要暂无逐文件数据');
  await expect(swarmCov.locator('.section-head', { hasText: 'Node.js 目录覆盖率' })).toContainText('提交 cccccccccc');
  await page.locator('[data-cov-expand="node"]').click();
  const groupRows = swarmCov.locator('.cov-tree[data-language="node"] .cov-group');
  await expect(groupRows).toHaveCount(2);
  await expect(groupRows.filter({ hasText: 'web/' })).toContainText('12 个文件');
  await expect(groupRows.filter({ hasText: 'web/' })).toContainText('50.0%');
  await expect(swarmCov.locator('.cov-file:not(.cov-group)')).toHaveCount(0);
  await page.mouse.move(0, 0);
  await swarmCov.locator('.card:has(.cov-tree[data-language="node"])').screenshot({ path: shot('coverage-groups-release-desktop') });
  await page.reload();
  await expect(switchOf('coverage').locator('button.on')).toHaveText('releases/v0.3.0.beta');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.locator('[data-tab="ci"]').click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.locator('#tab-ci .line-bar').screenshot({ path: shot('line-switch-narrow') });
  // The full branch name fits the narrow lane header instead of being clipped.
  const branchHead = rows.nth(1).locator('.ci-lane-head b');
  await expect(branchHead).toHaveText('releases/v0.3.0.beta');
  expect(await branchHead.evaluate((el) => el.scrollWidth <= el.clientWidth + 1 && el.getBoundingClientRect().right <= el.closest('.ci-lane-head').getBoundingClientRect().right + 1)).toBeTruthy();
  await page.locator('#tab-ci .card:has(.ci-lanes)').screenshot({ path: shot('line-ci-release-narrow') });
  await switchOf('ci').getByRole('button', { name: 'main' }).click();
  await expect(rows).toHaveCount(5);
  await page.locator('[data-tab="tests"]').click();
  await expect(page.locator('#tab-tests')).toContainText('349 个用例');
  // The overview stays on main and still surfaces the failing release run.
  await page.locator('[data-tab="overview"]').click();
  await expect(page.locator('#tab-overview .health')).toContainText('releases/v0.3.0.beta 分支最近一次运行失败');
  await expect(page.locator('#tab-overview .ci-lane-row:not(.ci-axis)')).toHaveCount(5);
});

test('E2E navigation replaces tests and removed tabs have no dangling links', async ({ page }) => {
  await page.goto('/github-status-board/');
  const tabs = page.getByRole('navigation', { name: '看板页面' });
  await expect(tabs.getByRole('link')).toHaveText(['总览', 'Issue', 'PR', 'CI', 'E2E', 'Coverage', /^GitCode 同步\d*$/]);
  await expect(page.locator('#tab-releases, #tab-ops')).toHaveCount(0);
  await expect(page.locator('a[href="#releases"], a[href="#ops"]')).toHaveCount(0);
  await expect(page.getByRole('link', { name: '在 GitHub 查看全部版本 ↗' })).toHaveAttribute('href', /github\.com\/.+\/releases$/);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await tabs.getByRole('link', { name: 'E2E', exact: true }).click();
    await expect(page.locator('#tab-tests')).toBeVisible();
    await expect(tabs.getByRole('link', { name: 'E2E', exact: true })).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('#tab-tests')).toContainText('Real E2E');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    await page.screenshot({ path: shot(`e2e-tabs-${width}`) });
  }
  // Existing report return links keep #tests; old removed tabs recover to the overview.
  for (const hash of ['releases', 'ops', 'unknown']) {
    await page.goto(`/github-status-board/#${hash}`);
    await expect(page.locator('#tab-overview')).toBeVisible();
    await expect(page).toHaveURL(/#overview$/);
  }
  // Exercise hashchange too, without loading a fresh document.
  await tabs.getByRole('link', { name: 'CI', exact: true }).click();
  await page.evaluate(() => { location.hash = 'ops'; });
  await expect(page.locator('#tab-overview')).toBeVisible();
  await expect(page).toHaveURL(/#overview$/);
});

test('unavailable data is not zero or a passing build',async({page})=>{
  await page.goto('/empty/');
  const health=page.locator('#tab-overview .health');
  await expect(health).toContainText('CI 数据暂不可读取，状态未知');
  await expect(health).not.toContainText('正常');
  await expect(page.locator('#tab-overview .tile').filter({hasText:'主干门禁用例'}).locator('.value')).toHaveText('—');
  await expect(page.locator('#global-banner')).toContainText('结果未知');
  await expect(page.locator('#tabs [data-tab="releases"]')).toHaveCount(0);
});

test('Issue and PR tabs chart daily flow with an adjustable range and series switches', async ({ page }) => {
  await page.goto('/github-status-board/#issues');
  const card = page.locator('[data-trend-card="issues"]');
  const charts = card.locator('.trend-chart');
  // The last month by default: daily counts and the open count are two charts on one time axis.
  await expect(card.locator('[data-trend-range="30"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(card.locator('[data-trend-from]')).toHaveValue('2026-08-22');
  await expect(card.locator('[data-trend-to]')).toHaveValue('2026-09-20');
  await expect(charts.locator('figcaption')).toHaveText(['每日数量', '存量（当天结束时仍开放）']);
  await expect(charts.first().locator('.trend-line')).toHaveCount(2);
  await expect(charts.last().locator('.trend-line')).toHaveCount(1);
  expect(await card.locator('.trend-tick').evaluateAll((ticks) => ticks.filter((t) => t.getAttribute('text-anchor') === 'end').every((t) => /^\d[\d,]*$/.test(t.textContent)))).toBeTruthy();
  await expect(card.locator('.trend-note')).toContainText('按 UTC 日期统计');
  await expect(card.locator('.trend-note')).not.toContainText('回填');
  await card.locator('.trend-table summary').click();
  await expect(card.locator('.trend-table summary')).toHaveText('按日数据（30 天）');
  await expect(card.locator('.trend-table tbody tr')).toHaveCount(30);
  await expect(card.locator('.trend-table tbody tr').first().locator('td')).toHaveText(['2026-09-20', '0', '1', '95']);

  // Hovering picks the nearest day in both charts; arrow keys step through days.
  const hit = charts.first().locator('[data-trend-hit]');
  const box = await hit.boundingBox();
  await page.mouse.move(box.x + box.width - 2, box.y + box.height / 2);
  await expect(page.locator('#tooltip')).toHaveText('2026-09-20（UTC）\n0  新增\n1  关闭\n95  存量');
  await expect(card.locator('.trend-cross.on')).toHaveCount(2);
  await expect(card.locator('.trend-focus.on')).toHaveCount(3);
  await page.mouse.move(0, 0);
  await expect(card.locator('.trend-cross.on')).toHaveCount(0);
  await hit.focus();
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#tooltip')).toContainText('2026-09-19（UTC）');
  await page.keyboard.press('Home');
  await expect(page.locator('#tooltip')).toContainText('2026-08-22（UTC）');

  // Presets, the whole history and a custom range.
  await card.locator('[data-trend-range="7"]').click();
  await expect(card.locator('[data-trend-from]')).toHaveValue('2026-09-14');
  await expect(card.locator('.trend-table summary')).toHaveText('按日数据（7 天）');
  await card.locator('[data-trend-range="all"]').click();
  await expect(card.locator('[data-trend-from]')).toHaveValue('2026-05-24');
  await expect(card.locator('.trend-table summary')).toHaveText('按日数据（120 天）');
  await card.locator('[data-trend-from]').fill('2026-09-01');
  await card.locator('[data-trend-to]').fill('2026-09-10');
  await expect(card.locator('.trend-table summary')).toHaveText('按日数据（10 天）');
  await expect(card.locator('.seg button[aria-pressed="true"]')).toHaveCount(0);
  // The chosen range survives a data refresh.
  await page.locator('#refresh-btn').click();
  await expect(page.locator('#refresh-btn')).toBeEnabled();
  await expect(card.locator('[data-trend-from]')).toHaveValue('2026-09-01');

  // Each series can be switched off and on; with none left the charts say how to bring them back.
  await card.getByRole('button', { name: '存量' }).click();
  await expect(card.getByRole('button', { name: '存量' })).toHaveAttribute('aria-pressed', 'false');
  await expect(charts).toHaveCount(1);
  await card.getByRole('button', { name: '新增' }).click();
  await card.getByRole('button', { name: '关闭' }).click();
  await expect(card.locator('.trend-none')).toContainText('所有数据都已关闭显示');
  for (const name of ['新增', '关闭', '存量']) await card.getByRole('button', { name }).click();
  await expect(charts).toHaveCount(2);

  // PRs separate merges from PRs closed without merging, and say when history is still filling in.
  await page.goto('/github-status-board/#prs');
  const prs = page.locator('[data-trend-card="prs"]');
  await expect(prs.locator('.trend-toggle')).toHaveText(['新增', '合入', '关闭未合入', '存量']);
  await expect(prs.locator('.trend-chart').first().locator('.trend-line')).toHaveCount(3);
  await expect(prs.locator('.trend-note')).toContainText('历史仍在回填');
  await page.mouse.move(0, 0);
  await prs.screenshot({ path: shot('pr-trend-desktop') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(prs.locator('.trend-chart')).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect(await prs.evaluate((el) => [...el.querySelectorAll('.trend-svg')].every((svg) => svg.getBoundingClientRect().right <= el.getBoundingClientRect().right))).toBeTruthy();
  await prs.screenshot({ path: shot('pr-trend-narrow') });
});

test('mobile layout and failed refresh preserve snapshot',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.goto('/github-status-board/');
  await expect(page.locator('#tab-overview .health')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:shot('overview-mobile'),fullPage:true});
  await page.route('**/data/snapshot.json*',r=>r.fulfill({status:503,body:'unavailable'}));
  await page.getByRole('button',{name:'刷新视图',exact:true}).click();
  await expect(page.locator('#global-banner')).toContainText('无法读取快照');
  await expect(page.locator('#tab-overview .health')).toContainText('流水线问题');
});

test('board columns scroll independently inside the Issue page',async({page})=>{
  await page.setViewportSize({width:1280,height:720});
  await page.route('**/data/snapshot.json*',async route=>{
    const response=await route.fetch(), doc=await response.json();
    const item=doc.board.items.find(i=>i.kind==='issue');
    doc.board.items=Array.from({length:60},(_,i)=>({...item,id:'issue:'+(100+i),number:100+i,title:'待处理工作项 '+i,linked:[]}));
    await route.fulfill({response,json:doc});
  });
  await page.goto('/github-status-board/#issues');
  await showBoard(page, 'issues');
  const column=page.locator('.bcol[data-key="待处理"] .bcol-body');
  await expect(column.locator('.bcard')).toHaveCount(60);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await expect.poll(()=>column.evaluate(el=>el.scrollHeight>el.clientHeight)).toBeTruthy();
  await column.hover();await page.mouse.wheel(0,700);
  await expect.poll(()=>column.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
  const scroll=await column.evaluate(el=>el.scrollTop);
  await page.getByRole('button',{name:'刷新视图',exact:true}).click();
  await expect.poll(()=>column.evaluate(el=>el.scrollTop)).toBe(scroll);
  await page.setViewportSize({width:390,height:640});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  const group=page.locator('#tab-issues [data-bpref="group"]');
  expect(await group.locator('button').evaluateAll(buttons=>buttons.every(b=>b.clientHeight<40))).toBeTruthy();
  await group.locator('[data-val="author"]').click();
  await expect(group.locator('[data-val="author"]')).toHaveClass('on');
});

test('production and test sites show their source and isolate browser fields', async ({page}) => {
  let repo='openJiuwen-ai/sciencediscovery', label='正式';
  await page.route('**/data/snapshot.json*', async route => {
    const response=await route.fetch(), snapshot=await response.json();
    snapshot.repo=repo; snapshot.repo_url='https://github.com/'+repo;
    snapshot.deployment={label};
    await route.fulfill({response,json:snapshot});
  });
  async function note(value) {
    await page.locator('[data-open="issue:1"]').click();
    if(value!==undefined) {
      await page.locator('[data-dfield="note"]').fill(value);
      await page.locator('[data-dfield="note"]').press('Tab');
    }
  }
  await page.goto('/github-status-board/#issues');
  await showBoard(page, 'issues');
  await expect(page.locator('.brand-title')).toHaveText('正式 · GitHub 状态看板');
  await note('正式项目备注');
  await page.locator('[data-baction="close-drawer"]').click();
  repo='ScienceDiscovery/sciencediscovery'; label='测试';
  await page.reload();
  await expect(page.locator('.brand-title')).toHaveText('测试 · GitHub 状态看板');
  await expect(page.locator('#repo-link')).toHaveText(repo);
  await note();
  await expect(page.locator('[data-dfield="note"]')).toHaveValue('');
  await page.locator('[data-baction="close-drawer"]').click();
  repo='openJiuwen-ai/sciencediscovery'; label='正式';
  await page.reload(); await note();
  await expect(page.locator('[data-dfield="note"]')).toHaveValue('正式项目备注');
});
