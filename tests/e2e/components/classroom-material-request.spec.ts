import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const sources = new Map(['classroom_page.js', 'classroom_workspace.js', 'ui_overlay_motion.js']
  .map(name => [name, fs.readFileSync(path.resolve('static/js', name), 'utf8')]));
const dependencies = `export const apiFetch = async () => ({});
  export const initLearningMaterialSelector = () => ({});
  export const initSessionMaterialAiAssistant = () => ({});
  export const initAssignmentClocks = () => {};
  export const showToast = () => {};
  export const openMaterialListPopup = () => {};`;

async function mountTimeline(page: Page) {
  await page.route('http://schedule.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/js/')) {
      await route.fulfill({ contentType: 'text/javascript', body: sources.get(path.basename(url.pathname)) || dependencies });
      return;
    }
    await route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8">
      <body class="classroom-workspace-v2"><div id="teaching-plan-widget">
      <div id="teachingTimelineScroll"><button data-session-order="3">课次 3</button><button data-session-order="4">课次 4</button></div></div>
      <div id="teachingSessionModal"><h2 id="teachingSessionModalTitle"></h2><div id="teachingSessionModalSummary"></div>
      <button id="teachingSessionOpenMaterialBtn"><span>进入本次学习材料</span></button><p id="cw-session-material-status" hidden></p></div>
      <script type="module">
      import { initClassroomPage } from '/static/js/classroom_page.js';
      window.APP_CONFIG = {classOfferingId:1,userInfo:{role:'student'},teachingPlan:{sessions:[3,4].map(id=>({id,order_index:id,title:'课次 '+id}))}};
      window.pendingMaterials = [];
      window.fetch = (url, options) => new Promise(resolve => {
        // Keep an already-started response deliverable after cancellation as well:
        // the controller must guard against a late body from the old session.
        window.pendingMaterials.push({scope:new URL(url,location.origin).searchParams.get('session_id'),
          signal:options.signal, resolve:materials=>resolve({ok:true,json:async()=>({materials})})});
      });
      initClassroomPage();
      window.timelineReady = true;
      </script></body></html>` });
  });
  await page.goto('http://schedule.test/');
  await page.waitForFunction(() => (window as any).timelineReady);
}

async function selectSession(page: Page, order: number) {
  await page.evaluate(value => document.dispatchEvent(new CustomEvent('classroom:select-session', { detail: { order: value } })), order);
}

test('an old workspace exit cannot cancel the newly opened empty-material session', async ({ page }) => {
  await mountTimeline(page);
  await selectSession(page, 3);
  await page.evaluate(() => (window as any).pendingMaterials[0].resolve([{ id: 11 }, { id: 12 }]));
  const button = page.locator('#teachingSessionOpenMaterialBtn');
  await expect(button).toBeEnabled();
  await selectSession(page, 4);
  await expect(button).toHaveAttribute('aria-busy', 'true');
  // A previous Radix FocusScope can run this notification after the next
  // detail already started its request (the real list -> reader -> return case).
  await page.evaluate(() => document.dispatchEvent(new CustomEvent('classroom:workspace-closed')));
  expect(await page.evaluate(() => (window as any).pendingMaterials[1].signal.aborted)).toBe(false);
  await page.evaluate(() => (window as any).pendingMaterials[1].resolve([]));
  await expect(button).toBeEnabled();
  await expect(button).not.toHaveAttribute('aria-busy');
  await expect(page.locator('#cw-session-material-status')).toContainText('本课次暂无可访问的学习材料');
  await button.click();
  await expect(button).toBeEnabled();
});

test('switching sessions still cancels the old request and ignores its late response', async ({ page }) => {
  await mountTimeline(page);
  await selectSession(page, 3);
  await selectSession(page, 4);
  expect(await page.evaluate(() => (window as any).pendingMaterials.map((item: any) => item.signal.aborted))).toEqual([true, false]);
  await page.evaluate(() => (window as any).pendingMaterials[0].resolve([]));
  await expect(page.locator('#teachingSessionOpenMaterialBtn')).toBeDisabled();
  await expect(page.locator('#cw-session-material-status')).toHaveText('正在读取本课可访问的学习材料…');
  await page.evaluate(() => (window as any).pendingMaterials[1].resolve([{ id: 12, open_url: '/learning/12' }]));
  await expect(page.locator('#teachingSessionOpenMaterialBtn')).toBeEnabled();
  await expect(page.locator('#cw-session-material-status')).toBeHidden();
  await expect(page.locator('#teachingSessionModalTitle')).toHaveText('课次 4');
});
