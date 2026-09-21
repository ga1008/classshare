import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { buildReactFixture } from './lq-react-fixture';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_presentation.py'], { encoding: 'utf8' }));
const cases = fixture.cases.filter((item: any) => ['button', 'avatar'].includes(item.kind));
const invalid = fixture.invalid.filter((item: any) => ['button', 'avatar'].includes(item.kind));
let script: string;
test.beforeAll(async () => {
  script = await buildReactFixture(`
    import React from 'react'; import {createRoot} from 'react-dom/client'; import {flushSync} from 'react-dom';
    import {renderToStaticMarkup} from 'react-dom/server';
    import {LqButton,LqAvatar} from '@/components/lq-presentation';
    import {IconActionLink,IconActionButton,AvatarActionLink} from '@/components/action-entry';
    import {SquarePen,CircleAlert} from 'lucide-react';
    window.stats={click:0,capture:0,submit:0,feedback:0,focus:0};
    let root, values=[];
    const Component=kind=>kind==='button'?LqButton:LqAvatar;
    function App(){return <><form onSubmit={event=>{event.preventDefault();window.stats.submit++;}}>
      <div id="canonical" className="samples">{values.map(item=>{const C=Component(item.kind);return <div className="sample" data-case={item.id} key={item.id}><C {...item.props} onClick={()=>window.stats.click++} onClickCapture={()=>window.stats.capture++}/></div>;})}</div>
      </form><section id="compat" aria-label="实际入口组件"><h2>博客、反馈、个人中心</h2>
      <IconActionLink href="/blog" className="message-center-bell" aria-label="打开博客中心" title="打开博客中心" icon={<SquarePen size={18}/>} iconClassName="message-center-bell__icon"/>
      <IconActionButton className="feedback-entry-button" data-open-feedback aria-label="打开问题反馈" title="打开问题反馈" icon={<CircleAlert size={19}/>} onClick={()=>window.stats.feedback++} onFocus={()=>window.stats.focus++}/>
      <AvatarActionLink href="/profile" className="profile-entry-button" avatarClassName="profile-entry-button__avatar" aria-label="打开个人中心" title="打开个人中心" avatarSrc="/assets/missing.png"/>
      </section></>;}
    const draw=()=>flushSync(()=>root.render(<React.StrictMode><App/></React.StrictMode>));
    window.reactPresentation={mount(items){values=items;root=createRoot(document.getElementById('island'));draw();},
      update(id,patch){values=values.map(item=>item.id===id?{...item,props:{...item.props,...patch}}:item);draw();},
      unmount(){flushSync(()=>root.unmount());},
      invalid(items){return items.map(item=>{try{renderToStaticMarkup(React.createElement(Component(item.kind),item.props));return 'accepted';}catch(error){return error.name;}});}
    };window.fixtureLoaded=true;
  `);
});

async function setup(page: Page, items = cases) {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://lq-react-presentation.test') return route.abort();
    if (url.pathname === '/fixture.js') return route.fulfill({ contentType: 'text/javascript', body: script });
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file))
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/assets/avatar.png') return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=', 'base64') });
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light" data-ui-palette="indigo" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>React presentation</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{padding:24px;max-width:1300px;margin:auto}.samples{display:flex;flex-wrap:wrap;gap:16px;align-items:center}.sample{max-width:100%}#compat{margin-top:32px}#compat h2{margin-bottom:16px}</style></head><body><main><h1>React 实际入口</h1><div id="island"></div></main><script type="module" src="/fixture.js"></script></body></html>` });
    return route.fulfill({ status: 404, body: 'missing fixture resource' });
  });
  await page.goto('http://lq-react-presentation.test/');
  await page.waitForFunction(() => (window as any).fixtureLoaded);
  await page.evaluate(items => (window as any).reactPresentation.mount(items), items);
  return errors;
}

test('LQ actual React Button/Avatar match real Jinja, HTML and Element entry semantics', async ({ page }) => {
  const errors = await setup(page);
  expect(fixture.isolated).toBe(true);
  const results = await page.evaluate(async items => {
    const api = await import(/* @vite-ignore */ '/static/js/lq/components.js');
    function semantic(node: Node): any {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent?.trim() ? { text: node.textContent } : null;
      const el = node as Element;
      return { tag: el.tagName.toLowerCase(), attrs: Object.fromEntries([...el.attributes].filter(a => a.name !== 'hidden').map(a => [a.name, a.value]).sort()), children: [...el.childNodes].map(semantic).filter(Boolean) };
    }
    function parsed(markup: string) { const t = document.createElement('template'); t.innerHTML = markup; return semantic(t.content.firstElementChild!); }
    return items.map((item: any) => ({ id: item.id, react: semantic(document.querySelector('[data-case="'+item.id+'"] > *')!),
      jinja: parsed(item.html), html: parsed(api.componentMarkup(item.kind, item.props)), element: semantic(api.createComponent(item.kind, item.props)) }));
  }, cases);
  for (const result of results) {
    expect(result.react, result.id).toEqual(result.jinja);
    expect(result.react, result.id).toEqual(result.html);
    expect(result.react, result.id).toEqual(result.element);
  }
  expect(await page.evaluate(items => (window as any).reactPresentation.invalid(items), invalid)).toEqual(invalid.map(() => 'TypeError'));
  expect(errors).toEqual([]);
});

test('LQ React busy and disabled states block activation while native submit and legacy callbacks survive', async ({ page }) => {
  const errors = await setup(page, cases.filter((item: any) => ['button-soft-md', 'button-soft-md-busy', 'button-aria-disabled', 'button-native-disabled', 'button-submit', 'button-disabled-link'].includes(item.id)));
  for (const id of ['button-soft-md-busy', 'button-aria-disabled', 'button-disabled-link']) {
    const control = page.locator(`[data-case="${id}"] > *`);
    await control.focus(); await page.keyboard.press('Enter'); await page.keyboard.press('Space'); await control.click({ force: true });
  }
  await page.locator('[data-case="button-native-disabled"] > button').evaluate((el: HTMLButtonElement) => el.click());
  expect(await page.evaluate(() => (window as any).stats)).toMatchObject({ click: 0, capture: 0, submit: 0 });
  await page.locator('[data-case="button-soft-md"] > button').click();
  expect(await page.evaluate(() => (window as any).stats)).toMatchObject({ click: 1, capture: 1, submit: 0 });
  await page.locator('[data-case="button-submit"] > button').focus(); await page.keyboard.press('Enter');
  expect(await page.evaluate(() => (window as any).stats.submit)).toBe(1);
  const feedback = page.getByRole('button', { name: '打开问题反馈', exact: true });
  await expect(feedback).toHaveAttribute('type', 'button'); await expect(feedback).toHaveAttribute('data-open-feedback', 'true');
  await feedback.focus(); await page.keyboard.press('Space');
  expect(await page.evaluate(() => (window as any).stats)).toMatchObject({ feedback: 1, focus: 1 });
  await expect(page.getByRole('link', { name: '打开博客中心', exact: true })).toHaveAttribute('href', '/blog');
  await expect(page.getByRole('link', { name: '打开个人中心', exact: true })).toHaveAttribute('href', '/profile');
  expect(errors).toEqual([]);
});

test('LQ React rerenders retain native focus/width and failed avatars recover after a changed URL', async ({ page }) => {
  const errors = await setup(page, [{ id: 'save', kind: 'button', props: { label: '保存', icon: 'check' } }, { id: 'avatar', kind: 'avatar', props: { name: '张老师', src: '/assets/missing.png' } }]);
  const button = page.getByRole('button', { name: '保存', exact: true });
  await button.focus(); const before = await button.boundingBox();
  await page.evaluate(() => (window as any).reactPresentation.update('save', { loading: true,
    nativeProps: { 'aria-label': '旁路名字', 'aria-labelledby': 'invalid-reference', 'aria-disabled': 'false', 'data-lq-disabled': 'false' } }));
  await expect(button).toBeFocused(); await expect(button).toHaveAttribute('aria-busy', 'true');
  await expect(button).not.toHaveAttribute('aria-labelledby'); await expect(button).toHaveAttribute('data-lq-disabled', 'true');
  expect((await button.boundingBox())?.width).toBe(before?.width);
  const img = page.locator('[data-case=avatar] img');
  await expect(img).toBeHidden();
  await expect(page.locator('[data-case=avatar] .lq-avatar__fallback')).toBeVisible();
  await page.evaluate(() => (window as any).reactPresentation.update('avatar', { src: '/assets/avatar.png' }));
  await expect(img).toBeVisible();
  await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(1);
  for (let index = 0; index < 20; index++) {
    await page.evaluate(() => (window as any).reactPresentation.unmount());
    await expect(page.locator('#island > *')).toHaveCount(0);
    await page.evaluate(() => (window as any).reactPresentation.mount([{ id: 'save', kind: 'button', props: { label: '保存' } }]));
    await page.getByRole('button', { name: '保存', exact: true }).click();
  }
  expect(await page.evaluate(() => (window as any).stats.click)).toBe(20);
  expect(errors).toEqual([]);
});

for (const appearance of ['light', 'dark']) for (const width of [1440, 390]) test(`LQ actual React launcher and primitive axe ${appearance} ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  const errors = await setup(page);
  await page.evaluate(appearance => document.documentElement.dataset.appearance = appearance, appearance);
  for (const palette of ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal']) {
    await page.evaluate(palette => document.documentElement.dataset.uiPalette = palette, palette);
    const scan = await new AxeBuilder({ page }).analyze();
    expect(scan.violations.filter(item => ['serious', 'critical'].includes(item.impact || '')), palette).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.evaluate(() => {
      const avatar = document.querySelector('#compat .lq-avatar')!;
      const initial = avatar.querySelector('.lq-avatar__fallback')!;
      const a = avatar.getBoundingClientRect(), b = initial.getBoundingClientRect();
      const probe = document.createElement('span'); probe.style.background = 'hsl(var(--ls-surface-1))'; document.body.append(probe);
      const expected = getComputedStyle(probe).backgroundColor;
      const paired = [...document.querySelectorAll('#compat > .lq-btn, #compat > .lq-avatar-link')].every(el => getComputedStyle(el).backgroundColor === expected);
      probe.remove();
      return { paired, centered: Math.abs((a.left + a.width / 2) - (b.left + b.width / 2)) < 1 && Math.abs((a.top + a.height / 2) - (b.top + b.height / 2)) < 1 };
    })).toEqual({ paired: true, centered: true });
  }
  fs.mkdirSync('.codex-temp/lq-s2-react-presentation', { recursive: true });
  await page.screenshot({ path: `.codex-temp/lq-s2-react-presentation/${appearance}-${width}.png`, fullPage: true });
  expect(errors).toEqual([]);
});

test('LQ actual React launchers retain 44px touch targets and visible forced-color focus', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  try {
    await setup(page, []);
    expect(await page.evaluate(() => matchMedia('(pointer:coarse)').matches)).toBe(true);
    for (const name of ['打开博客中心', '打开问题反馈', '打开个人中心']) {
      const node = page.getByRole(name === '打开问题反馈' ? 'button' : 'link', { name, exact: true });
      const box = await node.boundingBox(); expect(box?.width).toBeGreaterThanOrEqual(44); expect(box?.height).toBeGreaterThanOrEqual(44);
    }
    await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
    await page.keyboard.press('Tab');
    const link = page.getByRole('link', { name: '打开博客中心', exact: true });
    await expect(link).toBeFocused(); await expect(link).not.toHaveCSS('outline-style', 'none');
    await page.getByRole('button', { name: '打开问题反馈', exact: true }).tap();
    expect(await page.evaluate(() => (window as any).stats.feedback)).toBe(1);
  } finally { await context.close(); }
});
