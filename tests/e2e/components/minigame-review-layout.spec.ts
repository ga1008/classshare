import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const repo = path.resolve(__dirname, '../../..');
const origin = 'https://review-queue.test';
const api = '/api/games/v1/moderation/reviews';
const longTitle = '迷城寻路 · 第二章 ' + 'Observatory'.repeat(5);
const items = ['manual', 'approved', 'queued', 'running'].flatMap(status => Array.from({ length: ['manual', 'approved'].includes(status) ? 15 : 1 }, (_, index) => ({
  status,
  gameId: 'game-' + index,
  versionId: status + '-' + index,
  title: index === 0 ? longTitle : '星图探索 ' + (index + 1),
  version: index === 0 ? '2.1.0-release-candidate.3' : '1.0.0',
  ownerRole: index % 2 ? 'student' : 'teacher',
  authorName: index ? '星图工作室' : '教研组联合制作与协作小组',
  aiVerdict: status === 'manual' ? 'major_risk' : 'approved',
  reason: status === 'manual' ? '有待核对的剧情对白及第三方素材说明，请结合游戏文件内的引用出处人工复核。' : '已核对游戏内容并批准发布。',
  fileCount: 12, config: {}, createdAt: 1780000000000,
})));

async function openQueue(page: Page) {
  const python = process.env.LQ_TEST_PYTHON || (fs.existsSync(path.join(repo, 'venv/Scripts/python.exe'))
    ? path.join(repo, 'venv/Scripts/python.exe') : 'python');
  const html = execFileSync(python, [path.join(__dirname, 'fixtures/minigame_templates.py')], {
    cwd: repo, input: JSON.stringify({ page: 'reviews', role: 'super', game: {} }),
    encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' },
  });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(repo, '.' + url.pathname);
      if (!file.startsWith(path.join(repo, 'static') + path.sep) || !fs.existsSync(file)) {
        return route.fulfill({ status: 404, body: '' });
      }
      return route.fulfill({ path: file });
    }
    if (url.pathname === '/manage/library/games/reviews') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
    if (url.pathname === api) {
      const status = url.searchParams.get('status');
      const visible = items.filter(item => status === 'ai_pending' ? ['queued', 'running'].includes(item.status) : item.status === status);
      return route.fulfill({ json: { items: visible, counts: { manual: 15, approved: 15, aiPending: 2, total: items.length } } });
    }
    if (url.pathname.endsWith('/tree')) return route.fulfill({ json: { tree: {
      id: 1, name: '游戏包', is_folder: true, children: [], preview_mode: 'folder', document_kind: 'folder',
    }, selected_id: 1, stats: { file_count: 0, folder_count: 1, total_size: 0 } } });
    if (url.pathname.startsWith(api + '/')) return route.fulfill({ json: items.find(item => url.pathname.endsWith('/' + item.versionId)) });
    return route.fulfill({ json: {} });
  });
  await page.goto(origin + '/manage/library/games/reviews');
  await expect(page.locator('[data-review-list] button')).toHaveCount(15);
}

async function checkCards(page: Page, info: TestInfo, label: string) {
  const geometry = await page.locator('[data-review-list]').evaluate(list => {
    const cards = Array.from(list.querySelectorAll<HTMLButtonElement>('button'));
    const box = (el: Element) => {
      const { left, top, right, bottom, height, width } = el.getBoundingClientRect();
      return { left, top, right, bottom, height, width };
    };
    return {
      listWidth: list.clientWidth, scrollWidth: list.scrollWidth, listHeight: list.clientHeight, scrollHeight: list.scrollHeight,
      cards: cards.map(card => ({
        item: box(card.parentElement!), button: box(card),
        slots: Array.from(card.querySelectorAll('strong, code, small, .lq-chip, p')).map(slot => ({
          text: slot.textContent, ...box(slot),
        })),
      })),
    };
  });
  fs.writeFileSync(info.outputPath(label + '-geometry.json'), JSON.stringify(geometry, null, 2));
  await page.screenshot({ path: info.outputPath(label + '.png'), animations: 'disabled' });
  expect(geometry.scrollWidth, '队列内容宽度').toBeLessThanOrEqual(geometry.listWidth + 1);
  for (const { item, button, slots } of geometry.cards) {
    expect(button.bottom, '卡片按钮被所在行纵向裁切').toBeLessThanOrEqual(item.bottom + 1);
    expect(button.right).toBeLessThanOrEqual(item.right + 1);
    for (const slot of slots) {
      expect(slot.left, slot.text || '字段左边界').toBeGreaterThanOrEqual(button.left - 1);
      expect(slot.right, slot.text || '字段右边界').toBeLessThanOrEqual(button.right + 1);
      expect(slot.top, slot.text || '字段上边界').toBeGreaterThanOrEqual(button.top - 1);
      expect(slot.bottom, slot.text || '字段下边界').toBeLessThanOrEqual(button.bottom + 1);
    }
  }
  return geometry;
}

for (const [width, height] of [[1440, 900], [1100, 560], [390, 760], [320, 700]]) {
  test('审核队列在 ' + width + 'px 完整显示待审与已通过条目', async ({ page }, info) => {
    await page.setViewportSize({ width, height });
    if (width === 1100 || width === 390) await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
    await openQueue(page);
    const cards = page.locator('[data-review-list] button');
    await expect(cards.first()).toHaveAttribute('aria-pressed', 'true');
    await expect(cards.first()).toContainText('AI 预警');
    const initial = await checkCards(page, info, '待审核');
    expect(initial.scrollHeight, '长队列使用自身滚动区').toBeGreaterThan(initial.listHeight);
    await cards.last().scrollIntoViewIfNeeded();
    await cards.last().focus();
    await page.keyboard.press('Enter');
    await expect(cards.last()).toHaveAttribute('aria-pressed', 'true');
    await expect(cards.last()).toBeFocused();
    await expect(cards.first()).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('.game-review-title')).toHaveText('星图探索 15');
    await page.getByRole('tab', { name: /已通过/ }).click();
    await expect(cards.first()).toContainText('已通过');
    await expect(cards.first()).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('complementary', { name: '已通过' })).toBeVisible();
    await checkCards(page, info, '已通过');
  });
}

test('AI 审核队列只显示一个进度标签，轮询保留当前行和键盘焦点', async ({ page }) => {
  await openQueue(page);
  await page.getByRole('tab', { name: /AI 审核中/ }).click();
  const cards = page.locator('[data-review-list] button');
  await expect(cards).toHaveCount(2);
  await expect(cards.first().getByText('排队 AI 审核', { exact: true })).toHaveCount(1);
  await expect(cards.last().getByText('AI 查验中', { exact: true })).toHaveCount(1);
  await cards.last().focus();
  await page.keyboard.press('Enter');
  await expect(cards.last()).toHaveAttribute('aria-pressed', 'true');
  // A poll replaces the queue DOM even if the server returns the same page.
  const initial = await cards.last().elementHandle();
  await expect.poll(() => initial!.evaluate(el => el.isConnected), { timeout: 8000 }).toBe(false);
  await expect(cards.last()).toBeFocused();
  await expect(cards.last()).toHaveAttribute('aria-pressed', 'true');
});
