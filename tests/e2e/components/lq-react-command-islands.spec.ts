import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { buildReactFixture } from './lq-react-fixture';

let script: string;
test.beforeAll(async () => {
  script = await buildReactFixture(`
    import '@/islands/assignment-authoring-sync';
    import '@/islands/exam-assign-sync';
    import '@/islands/message-center-workspace-sync';
    import '@/islands/submission-jump-nav';
  `);
});
async function setup(page: Page) {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://lq-command-islands.test') return route.abort();
    if (url.pathname === '/fixture.js') return route.fulfill({ contentType: 'text/javascript', body: script });
    if (url.pathname.startsWith('/static/css/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static/css') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: 'text/css', body: fs.readFileSync(file) });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css"><link rel="stylesheet" href="/static/css/submission_detail.css"><style>main{max-width:1000px;margin:auto;padding:16px;display:grid;gap:16px}#submission-jump-question-groups{max-width:340px}</style><body class="classroom-page"><main>
      <div id="assignment" data-lanshare-island="assignment-authoring-sync"></div>
      <div id="exam" data-lanshare-island="exam-assign-sync"></div>
      <div id="messages" data-lanshare-island="message-center-workspace-sync"></div>
      <span id="submission-jump-answer-count"></span>
      <div id="submission-jump-question-groups" data-lanshare-island="submission-jump-nav"><script type="application/json" data-submission-jump-nav-payload>{"answers":{"q1":"已完成"},"examQuestions":{"pages":[{"name":"题目跳转","questions":[{"id":"q1","text":"说明网络中的数据如何可靠传输？"},{"id":"q2","text":"比较两种常见网络拓扑的特点。"}]}]}}</script></div>
      </main><div style="margin-top:1500px" id="submission-q-1">第一题答案</div><div style="height:1000px" id="submission-q-2">第二题答案</div><script>
      window.__LANSHARE_ASSIGNMENT_AUTHORING__={canSave:true,assessmentKindLabel:'平时作业',title:'网络原理',completedChecks:5,totalChecks:5};
      window.__LANSHARE_EXAM_ASSIGN__={canPublish:true,paperCount:3,selectedPaperTitle:'单元测试',assessmentKindLabel:'课堂测验'};
      window.__LANSHARE_MESSAGE_CENTER_WORKSPACE__={lqEnabled:false,mode:'full',privateOpen:true,hasConversation:true,canSend:false,currentContactName:'学生甲'};
      window.commands=[];for(const kind of ['assignment-authoring','exam-assign','message-center-workspace'])window.addEventListener('lanshare:'+kind+'-command',e=>window.commands.push({kind,...e.detail}));
      window.change=(kind,patch)=>{const key={assignment:'__LANSHARE_ASSIGNMENT_AUTHORING__',exam:'__LANSHARE_EXAM_ASSIGN__',message:'__LANSHARE_MESSAGE_CENTER_WORKSPACE__'}[kind];const event={assignment:'assignment-authoring',exam:'exam-assign',message:'message-center-workspace'}[kind];window[key]={...window[key],...patch};window.dispatchEvent(new CustomEvent('lanshare:'+event+'-change',{detail:window[key]}));};
      </script><script type="module" src="/fixture.js"></script></body></html>` });
  });
  await page.goto('http://lq-command-islands.test/');
  await expect(page.locator('#assignment .lq-btn')).toHaveCount(10);
  await expect(page.locator('#exam .lq-btn')).toHaveCount(11);
  await expect(page.locator('#messages .lq-btn')).toHaveCount(4);
  await expect(page.locator('.submission-jump-question.lq-btn')).toHaveCount(2);
  return errors;
}

test('LQ actual command islands preserve command payloads, native disabled controls, link semantics and jump targets', async ({ page }) => {
  const errors = await setup(page);
  await expect(page.locator('main button:not([data-lq-component="button"]),main a:not([data-lq-component="button"])')).toHaveCount(0);
  await page.locator('#assignment').getByRole('button', { name: '平时作业' }).click();
  await page.locator('#assignment').getByRole('button', { name: '保存', exact: true }).click();
  await page.locator('#exam').getByRole('button', { name: '刷新', exact: true }).click();
  await page.locator('#exam').getByRole('button', { name: '发布', exact: true }).click();
  await page.locator('#messages').getByRole('link', { name: '私信', exact: true }).click();
  expect(await page.evaluate(() => (window as any).commands)).toEqual([
    { kind: 'assignment-authoring', type: 'focus-field', fieldId: 'assignment-assessment-kind' },
    { kind: 'assignment-authoring', type: 'save' }, { kind: 'exam-assign', type: 'reload-papers' },
    { kind: 'exam-assign', type: 'publish' }, { kind: 'message-center-workspace', type: 'set-tab', tab: 'private_message' },
  ]);
  await page.evaluate(() => { const w = window as any; w.change('assignment', { isSaving: true }); w.change('exam', { isLoading: true, canPublish: false }); });
  const blocked = [page.locator('#assignment').getByRole('button', { name: '保存', exact: true }), page.locator('#exam').getByRole('button', { name: '刷新', exact: true }), page.locator('#exam').getByRole('button', { name: '发布', exact: true }), page.locator('#messages').getByRole('button', { name: '输入', exact: true })];
  for (const button of blocked) { await expect(button).toBeDisabled(); await button.evaluate(node => (node as HTMLButtonElement).click()); }
  expect(await page.evaluate(() => (window as any).commands.length)).toBe(5);
  await expect(page.locator('#submission-jump-answer-count')).toHaveText('1/2');
  await expect(page.locator('[data-jump-question="submission-q-1"]')).toHaveClass(/is-answered/);
  await page.locator('[data-jump-question="submission-q-1"]').click();
  await expect.poll(async () => (await page.locator('#submission-q-1').boundingBox())!.y).toBeLessThan(50);
  await page.evaluate(() => (window as any).change('message', { mode: 'notifications' }));
  await page.locator('#messages').getByRole('link', { name: '私信', exact: true }).click();
  await page.waitForURL('**/profile?section=private&tab=private_message#profile-message-center');
  expect(errors).toEqual([]);
});

for (const appearance of ['light', 'dark']) for (const width of [1440, 390]) test(`LQ command islands keep shared controls and compound layout ${appearance} ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 980 }); const errors = await setup(page);
  await page.evaluate(value => { document.documentElement.dataset.appearance = value; document.documentElement.dataset.lqGlass = 'tinted'; }, appearance);
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  for (const control of await page.locator('main .lq-btn').all()) {
    expect(await control.evaluate(node => getComputedStyle(node).backdropFilter)).toBe('none');
  }
  expect(await page.locator('.submission-jump-question').first().evaluate(node => getComputedStyle(node).display)).toBe('grid');
  expect(await page.locator('.submission-jump-question > .lq-btn__label').first().evaluate(node => getComputedStyle(node).display)).toBe('contents');
  const messageSurface = page.locator('#messages .message-center-workspace-sync');
  await expect(messageSurface).toHaveAttribute('data-lq-component', 'surface');
  await expect(messageSurface).toHaveClass(/lq-card/);
  await expect(messageSurface).toHaveCSS('background-image', 'none');
  await expect(messageSurface).toHaveCSS('backdrop-filter', 'none');
  const folder = path.resolve('.codex-temp/lq-react-command-islands'); fs.mkdirSync(folder, { recursive: true });
  await page.locator('main').screenshot({ path: path.join(folder, `${appearance}-${width}.png`) });
  expect(errors).toEqual([]);
});
