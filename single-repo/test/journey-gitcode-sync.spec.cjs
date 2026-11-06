const { test, expect } = require('../.e2e/node_modules/@playwright/test');
const { resolve } = require('node:path');
const shot = name => resolve(__dirname, '../.e2e/' + name + '.png');
// Same fake credential that test/prepare-site.py embeds in the bot fixture.
const FAKE_TOKEN = 'gitcode-e2e-fake-token-0123456789';

test('GitCode sync page shows failures with their summaries, links both PRs, and carries no credential', async ({ page, request }) => {
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/github-status-board/#sync');
  const tab = page.locator('#tabs a[data-tab="sync"]');
  await expect(tab).toHaveClass(/active/);
  await expect(tab.locator('.tab-count')).toHaveText('2');
  const section = page.locator('#tab-sync');
  await expect(section).toBeVisible();
  await expect(section.locator('.banner.error[data-sync="problems"]')).toContainText('2 个 PR 同步失败、重试中或历史分叉');
  await expect(section.locator('.tile.bad')).toHaveCount(2);
  const failed = section.locator('tbody tr.sync-error[data-record="s4"]');
  await expect(failed).toContainText('失败');
  await expect(failed).toContainText('permission_denied');
  await expect(failed).toContainText('GitCode receive-pack returned HTTP 403');
  await expect(failed).toContainText('<script>alert(1)</script> fix sync');
  expect(await section.locator('script').count()).toBe(0);
  const diverged = section.locator('tbody tr[data-record="s5"]');
  await expect(diverged).toContainText('GitCode diff 可能包含本 PR 以外的提交');
  const merged = section.locator('tbody tr[data-record="s2"]');
  await expect(merged).toContainText('GitHub 已合并');
  await expect(merged).toContainText('未调用合并接口');
  await expect(merged.locator('a', { hasText: '#119' })).toHaveAttribute('href', 'https://github.com/openJiuwen-ai/sciencediscovery/pull/119');
  await expect(merged.locator('a', { hasText: '!9' })).toHaveAttribute('href', 'https://gitcode.com/openJiuwen/sciencediscovery/merge_requests/9');
  const current = section.locator('.card').first();
  await expect(current.locator('tr.sync-error')).toHaveCount(2);
  await expect(current).toContainText('已推送，历史分叉');
  await expect(current).toContainText('同步失败');
  await page.screenshot({ path: shot('gitcode-sync-desktop'), fullPage: true });
  await page.locator('#sync-only-failures').check();
  await expect(section.locator('.card').nth(1).locator('tbody tr')).toHaveCount(3);
  await expect(section.locator('.card').nth(1).locator('tbody tr.sync-error')).toHaveCount(3);
  // Neither the rendered page nor the published data file holds a credential.
  const html = await page.content();
  const data = await (await request.get('/github-status-board/data/gitcode-sync.json')).text();
  for (const leaked of [FAKE_TOKEN, 'sync-bot:', 'Authorization: Bearer g', 'access_token=']) {
    expect(html).not.toContain(leaked);
    expect(data).not.toContain(leaked);
  }
  const source = await (await request.get('/github-status-board/gitcode-sync.js')).text();
  expect(source).not.toContain(FAKE_TOKEN);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: shot('gitcode-sync-mobile'), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(errors).toEqual([]);
});

test('stale, disabled and missing sync data are explicit page states', async ({ page }) => {
  const doc = { schema_version: 1, generated_at: '2026-10-07T07:00:00Z', available: false, enabled: true, error: 'bot returned HTTP 503',
    stale_since: '2026-10-06T01:00:00Z', records: [], pulls: [] };
  await page.route('**/data/gitcode-sync.json', route => route.fulfill({ json: doc }));
  await page.goto('/github-status-board/#sync');
  await expect(page.locator('#tab-sync .banner.error[data-sync="stale"]')).toContainText('最近一次采集没能读取 Bot 的同步记录');
  await expect(page.locator('#tab-sync')).toContainText('bot returned HTTP 503');
  await expect(page.locator('#tabs a[data-tab="sync"] .tab-count')).toHaveText('1');
  await page.unroute('**/data/gitcode-sync.json');
  await page.unroute('**/data/gitcode-sync.json');
  // Each reason the bot reports is named in Chinese; missing credentials are faults, no token and `off` are not.
  const reasons = {
    no_token: ['未设置 GITCODE_TOKEN', 'info', 0], off: ['同步已关闭（SDBOT_GITCODE_SYNC_TARGET=off）', 'info', 0],
    no_github_app: ['缺少 GitHub App 凭据', 'error', 1], no_webhook_secret: ['缺少 GitHub Webhook secret', 'error', 1],
  };
  for (const [code, [text, level, count]] of Object.entries(reasons)) {
    await page.route('**/data/gitcode-sync.json', route => route.fulfill({ json: { ...doc, available: true, enabled: false, error: undefined, reasons: [code] } }));
    await page.reload();
    const banner = page.locator('#tab-sync .banner[data-sync="disabled"]');
    await expect(banner).toContainText('GitCode 同步已停用');
    await expect(banner.locator(`li[data-reason="${code}"]`)).toContainText(text);
    await expect(banner).toHaveClass(new RegExp(level));
    await expect(page.locator('#tabs a[data-tab="sync"] .tab-count')).toHaveCount(count);
    await expect(page.locator('#tab-sync')).not.toContainText('未启用 GitCode 同步');
    await page.unroute('**/data/gitcode-sync.json');
  }
  await page.route('**/data/gitcode-sync.json', route => route.fulfill({ json: { ...doc, available: true, enabled: false, error: undefined, reasons: ['no_github_app', 'no_webhook_secret'] } }));
  await page.reload();
  const both = page.locator('#tab-sync .banner[data-sync="disabled"] li');
  await expect(both).toHaveCount(2);
  await expect(both.nth(0)).toContainText('缺少 GitHub App 凭据'); await expect(both.nth(1)).toContainText('缺少 GitHub Webhook secret');
  await page.screenshot({ path: shot('gitcode-sync-disabled'), fullPage: true });
  await page.unroute('**/data/gitcode-sync.json');
  await page.goto('/empty/#sync');
  await expect(page.locator('#tab-sync')).toContainText('尚未发布同步记录');
});
