import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('assignment status preserves notification choice on failure and hands off after shared exit', async ({ page }) => {
  const template = fs.readFileSync('templates/assignment_detail_teacher.html', 'utf8');
  const status = template.slice(template.indexOf('    // ==================== Status Dropdown'), template.indexOf('    // ==================== Edit Form'))
    .replace(/\{\{[\s\S]*?\}\}/g, 'null');
  const errors: string[] = [], payloads: any[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://assignment-status.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw Error(`Unexpected asset ${file}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname.startsWith('/api/assignments/')) {
      payloads.push(route.request().postDataJSON());
      return route.fulfill({ status: 409, contentType: 'application/json', body: '{}' });
    }
    return route.fulfill({ contentType: 'text/html; charset=utf-8', body: `<!doctype html><html data-theme="lanshare" data-appearance="light" data-lq-glass="tinted"><head><meta charset="utf-8"><link rel="stylesheet" href="/static/css/tailwind-app.css"></head><body>
      <button id="status-btn" onclick="toggleStatusDropdown()" aria-expanded="false">作业状态</button>
      <div id="status-dropdown" hidden><label><input id="status-email-notify" type="checkbox">发布通知</label><button onclick="changeStatus('published')">发布</button><button onclick="openCloseAssignmentFromStatus()">截止</button></div>
      <script type="module">
        import {createDomainPopoverSystem} from '/static/js/lq/domain-controls.js';
        const currentAssignmentId=12,currentAllowedFileTypes='',currentAvailabilityMode='permanent',currentDueAt=null,currentDurationMinutes=null,currentStartsAt=null,currentAutoClose=1,currentLatePolicy={};
        const showToast=()=>window.failed=true,getStatusText=value=>value;
        window.handoffs=[];window.openCloseAssignmentModal=()=>window.handoffs.push({hidden:document.getElementById('status-dropdown').hidden,focus:document.activeElement.id});
        ${status}
        window.ready=true;
      </script></body></html>` });
  });
  await page.goto('https://assignment-status.test/');
  await page.waitForFunction(() => (window as any).ready);
  await page.getByRole('button', { name: '作业状态', exact: true }).click();
  const panel = page.locator('#status-dropdown');
  await expect(panel).toHaveAttribute('role', 'dialog');
  await page.locator('#status-email-notify').check();
  await page.getByRole('button', { name: '发布', exact: true }).click();
  await expect.poll(() => payloads.length).toBe(1);
  expect(payloads[0]).toMatchObject({ status: 'published', send_email_notification: true });
  await expect(panel).toBeVisible();
  await expect(page.locator('#status-email-notify')).toBeChecked();
  await page.getByRole('button', { name: '截止', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).handoffs)).toEqual([{ hidden: true, focus: 'status-btn' }]);
  await page.locator('#status-btn').click();
  await expect(page.locator('#status-email-notify')).toBeChecked();
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await expect(page.locator('#status-btn')).toHaveAttribute('aria-expanded', 'false');
  expect(errors).toEqual([]);
});
