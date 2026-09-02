import { expect, test, type Page } from '@playwright/test';
import { collectBrowserErrors, expectNoBrowserErrors, loginTeacher, readFixture } from '../fixtures/p03';

const DEMO_HTML = `<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>白板 e2e</title></head>
<body style="font-family:sans-serif;padding:40px"><h1>白板 e2e 演示页</h1><p>用于讲课白板端到端测试。</p></body></html>`;

interface WhiteboardProbe {
  open: boolean;
  tool: string;
  types: string[];
  remoteVersion: number;
  dirty: boolean;
  popoverOpen: boolean;
  boards: number;
  activeName: string;
}

async function probe(page: Page): Promise<WhiteboardProbe> {
  return page.evaluate(() => {
    const wb = (window as any).teacherWhiteboard;
    const board = wb?.activeBoard;
    return {
      open: Boolean(wb?.isOpen),
      tool: String(wb?.settings?.tool || ''),
      types: (board?.elements || []).map((el: { type: string }) => el.type),
      remoteVersion: Number(board?.remoteVersion || 0),
      dirty: Boolean(board?.dirty),
      popoverOpen: Boolean(document.querySelector('.twb-popover.is-open')),
      boards: Number(wb?.state?.boards?.length || 0),
      activeName: String(board?.name || ''),
    };
  });
}

async function drag(page: Page, from: [number, number], to: [number, number]) {
  await page.mouse.move(from[0], from[1]);
  await page.mouse.down();
  await page.mouse.move((from[0] + to[0]) / 2, (from[1] + to[1]) / 2, { steps: 6 });
  await page.mouse.move(to[0], to[1], { steps: 6 });
  await page.mouse.up();
}

async function openWhiteboard(page: Page) {
  const fab = page.locator('#teacher-whiteboard-fab');
  await expect(fab).toBeVisible({ timeout: 15_000 });
  await fab.click();
  await expect(page.locator('#teacher-whiteboard-root.is-open')).toBeVisible();
  await expect(page.locator('#teacher-whiteboard-toolbar')).toBeVisible();
}

test.describe('P06 teacher whiteboard', () => {
  test('draw, eraser, popovers, cloud save, export and reload from cloud', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await page.setViewportSize({ width: 1440, height: 860 });
    await loginTeacher(page, fixture);

    // 通过上传 API 建一个 HTML 材料，作为白板宿主页。
    const upload = await page.request.post('/api/materials/upload', {
      multipart: {
        files: { name: `whiteboard-e2e-${Date.now()}.html`, mimeType: 'text/html', buffer: Buffer.from(DEMO_HTML, 'utf8') },
      },
    });
    expect(upload.ok()).toBeTruthy();
    const uploadBody = await upload.json();
    const materialId = Number(uploadBody.created_items?.[0]?.id);
    expect(materialId).toBeGreaterThan(0);

    await page.goto(`/materials/render-view/${materialId}`);
    await openWhiteboard(page);
    expect((await probe(page)).tool).toBe('brush');

    // 画两笔。
    await drag(page, [400, 400], [800, 520]);
    await drag(page, [420, 620], [900, 480]);
    await expect.poll(async () => (await probe(page)).types).toEqual(['stroke', 'stroke']);

    // 画笔芯片弹出浮窗；开始绘制后自动收回。
    await page.locator('[data-whiteboard-chip="brush"]').click();
    await expect(page.locator('.twb-popover.is-open')).toBeVisible();
    await drag(page, [300, 700], [700, 720]);
    await expect(page.locator('.twb-popover.is-open')).toHaveCount(0);
    await expect.poll(async () => (await probe(page)).types.length).toBe(3);

    // 橡皮（E）：像素擦追加 eraser 元素。
    await page.keyboard.press('e');
    await expect.poll(async () => (await probe(page)).tool).toBe('eraser');
    await drag(page, [600, 350], [620, 760]);
    await expect.poll(async () => (await probe(page)).types).toContain('eraser');

    // 清屏确认浮窗出现后取消。
    await page.locator('[data-whiteboard-action="clear"]').click();
    await expect(page.getByRole('alertdialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('alertdialog')).toHaveCount(0);

    // 线上保存（Ctrl+S）。
    const putResponse = page.waitForResponse((response) =>
      response.url().includes(`/api/materials/${materialId}/whiteboards/`) && response.request().method() === 'PUT',
    );
    await page.keyboard.press('Control+s');
    expect((await putResponse).ok()).toBeTruthy();
    await expect.poll(async () => (await probe(page)).remoteVersion).toBeGreaterThan(0);
    await expect.poll(async () => (await probe(page)).dirty).toBe(false);

    // 新建守卫：有内容时新建成功，空板再点不新建。
    await page.locator('[data-whiteboard-action="new-board"]').click();
    await expect.poll(async () => (await probe(page)).boards).toBe(2);
    await page.locator('[data-whiteboard-action="new-board"]').click();
    await page.waitForTimeout(300);
    expect((await probe(page)).boards).toBe(2);

    // 历史浮窗切回第一块板。
    await page.locator('[data-whiteboard-action="history"]').click();
    const rows = page.locator('.twb-history-row');
    await expect(rows).toHaveCount(2);
    await page.locator('.twb-history-row:not(.is-active)').first().click();
    await expect.poll(async () => (await probe(page)).types.length).toBeGreaterThanOrEqual(4);

    // 导出本地：默认 PNG 白底、比例锁定，下载文件名以 .png 结尾。
    await page.locator('[data-whiteboard-action="save-menu"]').click();
    await page.getByRole('menuitem', { name: /导出本地/ }).click();
    const dialog = page.locator('.twb-popover--modal.is-open');
    await expect(dialog).toBeVisible();
    const widthInput = dialog.getByLabel('宽度（像素）');
    const heightInput = dialog.getByLabel('高度（像素）');
    await expect(widthInput).toHaveValue(/^\d+$/);
    const heightBefore = Number(await heightInput.inputValue());
    await widthInput.fill('1024');
    await expect.poll(async () => Number(await heightInput.inputValue())).not.toBe(heightBefore);
    const downloadButton = dialog.getByRole('button', { name: /下载/ });
    await expect(downloadButton).toBeEnabled({ timeout: 10_000 });
    const downloadPromise = page.waitForEvent('download');
    await downloadButton.click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.png$/);

    // 清空本机缓存后刷新：内容从云端恢复。
    await page.evaluate(() => {
      const wb = (window as any).teacherWhiteboard;
      wb.state = null;
      wb.activeBoard = null;
      wb.sync.enabled = false;
      Object.keys(localStorage).filter((key) => key.startsWith('teacher-whiteboard')).forEach((key) => localStorage.removeItem(key));
    });
    await page.reload();
    await openWhiteboard(page);
    await expect.poll(async () => (await probe(page)).types.length, { timeout: 10_000 }).toBeGreaterThanOrEqual(4);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
