import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const slot = (week: number, weekday: number, sections: number[], room: string) => ({ week, weekday, sections, room, date: '2026-10-09' });
const overview = {
  sync_state: { last_success_at: '2026-10-09T09:30:00' },
  academic_entry_url: 'https://jwxt.example.test/tkgl',
  academic_requests: [
    { request_id: 'R-PENDING', serial: '202610099588', status: 'pending', kind: 'move', course_name: 'Python程序设计', class_label: '人工智能2601班',
      reason: '国庆调课', applied_at: '2026-10-09 09:12:24',
      details: [{ detail_id: 'D1', original: slot(6, 6, [6, 7], 'B416-1'), proposed: slot(17, 5, [4, 5], 'B416-1') }] },
    { request_id: 'R-DRAFT', serial: '', status: 'draft', kind: 'move', course_name: '计算机网络原理', class_label: '计科2606班',
      reason: '', applied_at: '', details: [{ detail_id: 'D2', original: slot(6, 4, [4, 5], 'B310'), proposed: slot(6, 5, [8, 9], 'B310') }] },
  ],
};

test('教务同步 menu syncs immediately, keeps old content on failure and lists applications read-only', async ({ page }) => {
  const requests: any[] = [];
  let success = false;
  await page.route('http://sync.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/academic-sync')) {
      requests.push(route.request().postDataJSON());
      await route.fulfill({ json: success
        ? { status: 'success', message: '同步完成', overview: { ...overview, selected_term: { year: '2024-2025', term: '2' }, weeks: [], warnings: [{ message: '节次来源不完整' }] } }
        : { status: 'failed', message: '教务暂时不可用' } });
      return;
    }
    if (url.pathname.startsWith('/static/js/')) {
      const file = path.resolve(url.pathname.slice(1));
      await route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file, 'utf8') });
      return;
    }
    await route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><button id="sync">教务同步</button><div id="current">旧课表第4周</div><div id="msg"></div><script type="module">import { createAcademicScheduleSync } from '/static/js/academic_schedule_sync.js';window.term={};window.overview=null;createAcademicScheduleSync({button:document.getElementById('sync'),getTerm:()=>window.term,getOverview:()=>window.overview,getEditorUrl:t=>'/editor?year='+t.year,onSuccess:data=>{window.overview=data.overview;document.getElementById('current').textContent=data.overview.selected_term.year;},onMessage:m=>{document.getElementById('msg').textContent=m;}});</script>` });
  });
  await page.goto('http://sync.test');
  const trigger = page.getByRole('button', { name: '教务同步' });
  await expect(trigger).toHaveAttribute('aria-haspopup', 'menu');

  await trigger.click();
  await page.getByRole('menuitem', { name: '立即同步' }).click();
  await expect(page.locator('.cs-sync-feedback')).toContainText('原有课表已保留');
  await expect(page.locator('#current')).toHaveText('旧课表第4周');
  expect(requests).toEqual([{ year: '', term: '' }]);

  await trigger.click();
  await page.getByRole('menuitem', { name: '申请列表' }).click();
  const dialog = page.getByRole('dialog', { name: '教务调停课申请' });
  await expect(dialog).toContainText('请先「立即同步」');
  await dialog.getByRole('button', { name: '关闭' }).click();
  await expect(dialog).toHaveCount(0);

  success = true;
  await page.evaluate(() => { (window as any).term = { year: '2024-2025', term: '2' }; });
  await trigger.click();
  await page.getByRole('menuitem', { name: '立即同步' }).click();
  await expect(page.locator('#current')).toHaveText('2024-2025');
  await expect(page.locator('#msg')).toHaveText('同步完成（1 条提示见课表下方）');
  await expect(page.locator('.cs-sync-feedback')).toHaveText('同步完成（1 条提示见课表下方）');
  expect(requests[1]).toEqual({ year: '2024-2025', term: '2' });

  await trigger.click();
  await page.getByRole('menuitem', { name: '申请列表' }).click();
  const cards = dialog.locator('.cs-req');
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toHaveAttribute('data-status', 'pending');
  await expect(cards.nth(0)).toHaveAttribute('open', '');
  await expect(cards.nth(0)).toContainText('原因：国庆调课');
  await expect(cards.nth(0).locator('[data-cse-version="proposed"] .is-changed')).toHaveCount(3);
  await expect(cards.nth(0).locator('[data-cs-req-jump]')).toHaveAttribute('href', 'https://jwxt.example.test/tkgl');
  await expect(cards.nth(0).locator('[data-cs-req-edit]')).toHaveCount(0);
  await expect(cards.nth(1).locator('[data-cs-req-edit]')).toHaveAttribute('href', '/editor?year=2024-2025');
  // Read-only: no submit/approve/withdraw controls are offered for 教务 applications.
  await expect(dialog.getByRole('button', { name: /提交|撤回|审批/ })).toHaveCount(0);
});
