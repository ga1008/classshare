import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { buildReactFixture } from './lq-react-fixture';

let script: string;
test.beforeAll(async () => {
  script = await buildReactFixture(`
    import React,{useState,useRef} from 'react';
    import {createRoot} from 'react-dom/client';
    import {LqButton,LqNativeInput,LqNativeTextarea,LqNativeSelect,LqChipGroup,LqPager,LqEmpty,LqStatus} from '@/components/lq-presentation';
    window.choiceChanges=[];
    function App(){const [query,setQuery]=useState('初稿'),[choice,setChoice]=useState('a'),[note,setNote]=useState('备注'),[page,setPage]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState(''),[showChoice,setShowChoice]=useState(true);
      const input=useRef(null);window.controls={input,setBusy,setError,setShowChoice};
      return <form id="native-form" onSubmit={e=>{e.preventDefault();window.submitted=Object.fromEntries(new FormData(e.currentTarget));}}>
        <LqNativeInput ref={input} id="query" label="搜索事项" name="q" value={query} onChange={e=>setQuery(e.target.value)} help="保留草稿" error={error} required />
        <LqNativeInput id="local" label="本地输入" name="local" defaultValue="不受控" />
        <LqNativeTextarea id="note" label="备注" name="note" value={note} onChange={e=>setNote(e.target.value)} rows={3} />
        <LqNativeTextarea id="scratch" label="暂存" name="scratch" defaultValue={'首行\\n第二行'} />
        {showChoice && <LqNativeSelect id="choice" label="任务分类" name="choice" value={choice} onChange={e=>{window.choiceChanges.push(e.target.value);setChoice(e.target.value);}}><option value="a">作业</option><option value="b">考试</option></LqNativeSelect>}
        <LqNativeInput id="locked" label="已禁用" name="locked" disabled defaultValue="不会提交" />
        <LqChipGroup label="任务筛选" value={choice} onChange={setChoice} items={[{value:'a',label:'作业',count:0},{value:'b',label:'考试',count:3},{value:'locked',label:'不可用',disabled:true}]} />
        <LqStatus family="submission" state="submitted" label="已提交" />
        <LqEmpty title="暂无更多内容" />
        <LqPager label="分页" page={page} pages={3} busy={busy} onChange={setPage} />
        <LqButton type="submit" variant="prominent" loading={busy}>保存</LqButton>
      </form>;
    }
    createRoot(document.getElementById('app')).render(<React.StrictMode><App/></React.StrictMode>);
  `);
});

async function setup(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://lq-react-fields.test') return route.abort();
    if (url.pathname === '/fixture.js') return route.fulfill({ contentType: 'text/javascript', body: script });
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>main{max-width:700px;margin:auto;padding:16px}form{display:grid;gap:16px}</style><body><main><h1>共享原生表单</h1><div id="app"></div></main><script type="module" src="/fixture.js"></script></body></html>` });
  });
  await page.goto('http://lq-react-fields.test/');
  await expect(page.locator('#query')).toHaveValue('初稿');
  return errors;
}

test('LQ React fields preserve controlled/uncontrolled values, native form submission, refs and descriptor labels', async ({ page }) => {
  const errors = await setup(page);
  await expect(page.getByLabel('搜索事项')).toHaveAttribute('aria-describedby', 'query--lq-help');
  await expect(page.locator('label[for=query]')).toHaveAttribute('id', 'query--lq-label');
  expect(await page.locator('#query').evaluate(el => el === (window as any).controls.input.current)).toBe(true);
  await page.getByLabel('搜索事项').fill('中文输入后保留');
  await page.getByLabel('本地输入').fill('本地草稿');
  await page.getByLabel('备注', { exact: true }).fill('第一行\n第二行');
  await expect(page.getByLabel('暂存')).toHaveValue('首行\n第二行');
  await page.getByRole('combobox', { name: '任务分类', exact: true }).click();
  await page.getByRole('option', { name: '考试', exact: true }).click();
  await expect(page.locator('#choice')).toHaveValue('b');
  expect(await page.evaluate(() => (window as any).choiceChanges)).toEqual(['b']);
  await expect(page.getByRole('button', { name: '考试 3', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  expect(await page.evaluate(() => (window as any).submitted)).toEqual({ q: '中文输入后保留', local: '本地草稿', note: '第一行\n第二行', scratch: '首行\n第二行', choice: 'b' });
  await page.evaluate(() => (window as any).controls.setError('请检查内容'));
  await expect(page.getByLabel('搜索事项')).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByLabel('搜索事项')).toHaveAttribute('aria-describedby', 'query--lq-help query--lq-error');
  await expect(page.getByLabel('本地输入')).toHaveValue('本地草稿');
  await page.evaluate(() => (window as any).controls.input.current.focus());
  await expect(page.getByLabel('搜索事项')).toBeFocused();
  expect(errors).toEqual([]);
});

test('LQ React controlled chips/pager preserve zero counts and disabled/busy activation', async ({ page }) => {
  const errors = await setup(page);
  const zero = page.getByRole('button', { name: '作业 0', exact: true });
  await expect(zero.locator('.lq-badge')).toHaveText('0');
  await page.getByRole('button', { name: '考试 3', exact: true }).click();
  await expect(page.locator('#choice')).toHaveValue('b');
  await expect(page.getByRole('combobox', { name: '任务分类', exact: true })).toHaveText('考试');
  await expect(page.getByRole('button', { name: '不可用', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '上一页', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page.getByRole('navigation')).toContainText('第 2 / 3 页');
  await page.evaluate(() => (window as any).controls.setBusy(true));
  await expect(page.getByRole('button', { name: '上一页', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '保存', exact: true }).click({ force: true });
  expect(await page.evaluate(() => (window as any).submitted)).toBeUndefined();
  expect(errors).toEqual([]);
});

test('LQ React select StrictMode remount retains one presentation owner and clears an open portal', async ({ page }) => {
  const errors = await setup(page);
  for (let iteration = 0; iteration < 3; iteration++) {
    const trigger = page.getByRole('combobox', { name: '任务分类', exact: true });
    await expect(page.locator('.lq-dropdown')).toHaveCount(1);
    await trigger.click();
    await expect(page.getByRole('listbox', { name: '任务分类', exact: true })).toHaveCount(1);
    await page.getByRole('option', { name: iteration % 2 ? '作业' : '考试', exact: true }).click();
    await expect(page.locator('#choice')).toHaveValue(iteration % 2 ? 'a' : 'b');
    await expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await trigger.click();
    await expect(page.getByRole('listbox', { name: '任务分类', exact: true })).toBeVisible();
    await page.evaluate(() => (window as any).controls.setShowChoice(false));
    await expect(page.locator('#choice, .lq-dropdown, .lq-dropdown__popup')).toHaveCount(0);
    await page.evaluate(() => (window as any).controls.setShowChoice(true));
    await expect(page.locator('#choice')).toHaveValue(iteration % 2 ? 'a' : 'b');
    await expect(page.locator('.lq-dropdown')).toHaveCount(1);
  }
  expect(await page.evaluate(() => (window as any).choiceChanges)).toEqual(['b', 'a', 'b']);
  expect(errors).toEqual([]);
});

for (const appearance of ['light', 'dark']) test(`LQ React fields mobile ${appearance} share glass controls without extra blur hosts`, async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  const errors = await setup(page);
  await page.evaluate(appearance => document.documentElement.dataset.appearance = appearance, appearance);
  expect(await page.locator('[data-lq-component]').evaluateAll(els => els.every(el => getComputedStyle(el).backdropFilter === 'none'))).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('combobox', { name: '任务分类', exact: true }).tap();
  await page.getByRole('option', { name: '考试', exact: true }).tap();
  await expect(page.locator('#choice')).toHaveValue('b');
  await expect(page.getByRole('button', { name: '考试 3', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: '作业 0', exact: true }).tap();
  await expect(page.locator('#choice')).toHaveValue('a');
  await expect(page.getByRole('combobox', { name: '任务分类', exact: true })).toHaveText('作业');
  fs.mkdirSync('.codex-temp/lq-react-fields', { recursive: true });
  await page.screenshot({ path: `.codex-temp/lq-react-fields/${appearance}-mobile.png`, fullPage: true });
  expect(errors).toEqual([]);
  await context.close();
});
