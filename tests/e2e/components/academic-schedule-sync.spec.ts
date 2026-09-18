import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const source = fs.readFileSync(path.resolve('static/js/academic_schedule_sync.js'), 'utf8');
test('shared sync UI discovers an uninitialized term and keeps old content on failure', async ({ page }) => {
  const requests: any[] = [];
  let success = false;
  await page.route('http://sync.test/**', async route => {
    const url = route.request().url();
    if (url.endsWith('/academic-sync')) {
      requests.push(route.request().postDataJSON());
      await route.fulfill({ json: success ? { status: 'success', message: '同步完成', overview: { selected_term: { year: '2026-2027', term: '1' }, weeks: [], message: '有1次课需核对关联', warnings: [{ message: '节次来源不完整' }] } } : { status: 'failed', message: '教务暂时不可用' } }); return;
    }
    await route.fulfill({ contentType: url.endsWith('/sync.js') ? 'text/javascript' : 'text/html', body: url.endsWith('/sync.js') ? source : `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><button id="sync">同步教务课表</button><div id="current">旧课表第4周</div><script type="module">import { createAcademicScheduleSync } from '/sync.js';window.term={};createAcademicScheduleSync({button:document.getElementById('sync'),getTerm:()=>window.term,onSuccess:data=>{document.getElementById('current').textContent=data.overview.selected_term.year;}});</script>` });
  });
  await page.goto('http://sync.test');
  await page.getByRole('button', { name: '同步教务课表' }).click();
  await expect(page.getByLabel('同步范围')).toHaveValue('current');
  await page.getByRole('button', { name: '开始同步' }).click();
  await expect(page.locator('.cs-sync-feedback')).toContainText('原有课表已保留');
  await expect(page.locator('#current')).toHaveText('旧课表第4周');
  expect(requests).toEqual([{ year: '', term: '' }]);
  success = true;
  await page.evaluate(() => { (window as any).term = { year: '2024-2025', term: '2' }; });
  await page.getByRole('button', { name: '同步教务课表' }).click();
  await expect(page.getByLabel('同步范围')).toHaveValue('selected');
  await expect(page.getByLabel('学年', { exact: true })).toHaveValue('2024-2025');
  await page.getByRole('button', { name: '开始同步' }).click();
  await expect(page.locator('#current')).toHaveText('2026-2027');
  await expect(page.locator('.cs-sync-feedback')).toContainText('有1次课需核对关联');
  await expect(page.locator('.cs-sync-feedback')).toContainText('节次来源不完整');
  expect(requests[1]).toEqual({ year: '2024-2025', term: '2' });
});
