import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

type Case = { id: string; kind: string; props: Record<string, unknown>; normalized?: unknown; html?: string; error?: string };
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture: { cases: Case[]; invalid: Case[]; isolated: boolean } = JSON.parse(execFileSync(python,
  ['tests/e2e/scripts/render_lq_presentation.py'], { encoding: 'utf8' }));
const palettes = ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal'];
const tokenExport = JSON.parse(fs.readFileSync('docs/lq-tokens.json', 'utf8'));
const entries = ['jinja', 'html', 'element'];

async function mount(page: Page, cases = fixture.cases, entry = 'jinja') {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-presentation.test') return route.abort();
    if (url.pathname.startsWith('/static/js/lq/') || url.pathname === '/static/css/tailwind-app.css') {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file))
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/assets/avatar.png') return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=', 'base64') });
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light" data-ui-palette="indigo" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ presentation fixture</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{padding:24px;max-width:1400px;margin:auto}.samples{display:flex;flex-wrap:wrap;gap:16px;align-items:center}.sample{max-width:100%}.sample:has(.lq-progress){width:240px}.group{margin-block:24px}.group h2{font-size:18px;margin-bottom:12px}</style></head><body><main><h1>LQ 六类组件</h1><div id="samples" class="samples"></div></main><script type="module">import * as components from '/static/js/lq/components.js';import {componentProps} from '/static/js/lq/component-props.js';window.LQTest={...components,componentProps};window.enhancement=components.enhanceComponents(document);document.body.dataset.ready='true';</script></body></html>` });
    return route.fulfill({ status: 404, contentType: 'text/plain', body: 'Local fixture missing asset' });
  });
  await page.goto('https://lq-presentation.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  await page.evaluate(({ cases, entry }) => {
    const api = (window as any).LQTest;
    const host = document.querySelector('#samples')!;
    for (const item of cases) {
      const wrapper = document.createElement('div'); wrapper.className = 'sample'; wrapper.dataset.case = item.id;
      if (entry === 'element') { const el = api.createComponent(item.kind, item.props); if (el) wrapper.append(el); }
      else wrapper.innerHTML = entry === 'jinja' ? item.html! : api.html[item.kind](item.props);
      host.append(wrapper);
    }
  }, { cases, entry });
}

test.describe('LQ presentation three-entry contract', () => {
  test('real Jinja props and parsed DOM equal HTML factories and actual Element trees', async ({ page }) => {
    expect(fixture.isolated).toBe(true);
    const declaredPalettes = [...new Set([...fs.readFileSync('static/css/lq/tokens.css', 'utf8').matchAll(/data-ui-palette="([^"]+)"/g)].map(match => match[1]))];
    expect(palettes.slice().sort()).toEqual(declaredPalettes.sort());
    expect(createHash('sha256').update(fs.readFileSync('static/css/lq/tokens.css')).digest('hex')).toBe(tokenExport.source_sha256);
    expect(fixture.cases.filter(item => item.error)).toEqual([]);
    await mount(page, []);
    const actual = await page.evaluate(cases => {
      const api = (window as any).LQTest;
      function semantic(node: Node): any {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent?.trim() ? { text: node.textContent } : null;
        const el = node as Element;
        return { tag: el.tagName.toLowerCase(), attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value]).sort()), children: [...el.childNodes].map(semantic).filter(Boolean) };
      }
      function parse(html: string) { const template = document.createElement('template'); template.innerHTML = html; return [...template.content.childNodes].map(semantic).filter(Boolean); }
      return cases.map(item => {
        const el = api.createComponent(item.kind, item.props);
        return { id: item.id, normalized: api.componentProps(item.kind, item.props), jinja: parse(item.html!), html: parse(api.html[item.kind](item.props)), element: el ? [semantic(el)] : [] };
      });
    }, fixture.cases);
    for (let i = 0; i < actual.length; i++) {
      expect(actual[i].normalized, `${actual[i].id}: normalized props`).toEqual(fixture.cases[i].normalized);
      expect(actual[i].html, `${actual[i].id}: HTML vs real Jinja`).toEqual(actual[i].jinja);
      expect(actual[i].element, `${actual[i].id}: Element vs real Jinja`).toEqual(actual[i].jinja);
    }
  });

  test('unsafe attrs, URL schemes, missing names and impossible props fail at all entrances', async ({ page }) => {
    expect(fixture.invalid.every(item => item.error === 'ValueError')).toBe(true);
    await mount(page, []);
    const results = await page.evaluate(cases => cases.map(item => {
      const api = (window as any).LQTest;
      return ['props', 'html', 'element'].map(entry => {
        try {
          if (entry === 'props') api.componentProps(item.kind, item.props);
          else if (entry === 'html') api.html[item.kind](item.props);
          else api.createComponent(item.kind, item.props);
          return 'accepted';
        } catch (error) { return (error as Error).name; }
      });
    }), fixture.invalid);
    results.forEach((result, index) => expect(result, JSON.stringify(fixture.invalid[index].props)).toEqual(['TypeError', 'TypeError', 'TypeError']));
  });

  for (const entry of entries) {
    test(`${entry}: keyboard and click cannot activate disabled or busy controls or submit implicitly`, async ({ page }) => {
      const selected = fixture.cases.filter(item => ['button-soft-md', 'button-soft-md-busy', 'button-aria-disabled', 'button-native-disabled', 'button-submit', 'button-disabled-link', 'filter-off', 'filter-disabled', 'tag-disabled'].includes(item.id));
      await mount(page, selected, entry);
      await page.evaluate(() => {
        const samples = document.querySelector('#samples')!; const form = document.createElement('form');
        samples.replaceWith(form); form.append(samples); (window as any).submissions = 0; (window as any).activations = 0;
        form.addEventListener('submit', event => { event.preventDefault(); (window as any).submissions++; });
        form.addEventListener('click', () => { (window as any).activations++; });
      });
      for (const id of ['button-soft-md-busy', 'button-aria-disabled', 'button-disabled-link']) {
        const control = page.locator(`[data-case="${id}"] > :first-child`);
        await control.focus(); await expect(control).toBeFocused();
        await page.keyboard.press('Enter'); await page.keyboard.press('Space');
        // Playwright treats aria-disabled as disabled; force the physical click
        // so this checks the product guard instead of Playwright actionability.
        await control.click({ force: true });
      }
      for (const selector of ['[data-case="button-native-disabled"] > button', '[data-case="filter-disabled"] > button', '.lq-chip__remove']) {
        const control = page.locator(selector); await expect(control).toBeDisabled();
        await control.evaluate((el: HTMLButtonElement) => { el.focus(); el.click(); });
        await expect(control).not.toBeFocused();
      }
      expect(await page.evaluate(() => [(window as any).submissions, (window as any).activations])).toEqual([0, 0]);
      await page.locator('[data-case="button-soft-md"] > button').focus(); await page.keyboard.press('Enter');
      await page.locator('[data-case="filter-off"] > button').focus(); await page.keyboard.press('Space');
      expect(await page.evaluate(() => (window as any).submissions)).toBe(0);
      await page.locator('[data-case="button-submit"] > button').focus(); await page.keyboard.press('Enter');
      expect(await page.evaluate(() => (window as any).submissions)).toBe(1);
      expect(page.url()).toBe('https://lq-presentation.test/');
    });

    test(`${entry}: loading preserves width/name and broken images expose named initial fallback`, async ({ page }) => {
      await mount(page, fixture.cases, entry);
      const widths = await page.evaluate(() => [...document.querySelectorAll('[data-case$="-busy"]')].map(wrapper => {
        const base = document.querySelector(`[data-case="${(wrapper as HTMLElement).dataset.case!.replace(/-busy$/, '')}"]`)!;
        return { id: (wrapper as HTMLElement).dataset.case, before: base.firstElementChild!.getBoundingClientRect().width, after: wrapper.firstElementChild!.getBoundingClientRect().width, name: wrapper.firstElementChild!.getAttribute('aria-label') };
      }));
      widths.forEach(item => { expect(item.after, item.id).toBe(item.before); expect(item.name).toBe('保存'); });
      const img = page.locator('.lq-avatar__image');
      await img.evaluate((el: HTMLImageElement) => { el.loading = 'eager'; el.src = '/assets/missing-avatar.png'; });
      await expect(img).toBeHidden();
      await expect(page.locator('[data-case="avatar-image"] .lq-avatar__fallback')).toBeVisible();
      await expect(page.locator('[data-case="avatar-image"] .lq-avatar')).toHaveAccessibleName('教师头像');
    });
  }

  test('touch controls occupy 44px; reduced motion, focus and forced colors retain usable geometry', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const page = await context.newPage();
    try {
      await mount(page);
      expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
      const rects = await page.locator('.lq-btn,.lq-chip--filter,.lq-chip__remove').evaluateAll(els => els.map(el => ({ class: el.className, width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height })));
      rects.forEach(rect => { expect(rect.width, rect.class).toBeGreaterThanOrEqual(44); expect(rect.height, rect.class).toBeGreaterThanOrEqual(44); });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      expect(await page.locator('.lq-spinner,.lq-progress:not([value])').evaluateAll(els => [...new Set(els.map(el => getComputedStyle(el).animationName))])).toEqual(['none']);
      await page.locator('[data-case="button-icon-only"] button').focus();
      expect(await page.locator('[data-case="button-icon-only"] button').evaluate(el => getComputedStyle(el).outlineStyle)).toBe('solid');
      await page.emulateMedia({ forcedColors: 'active' });
      expect(await page.locator('.lq-btn').first().evaluate(el => ({ shadow: getComputedStyle(el).boxShadow, border: getComputedStyle(el).borderTopStyle, blur: getComputedStyle(el).backdropFilter }))).toEqual({ shadow: 'none', border: 'solid', blur: 'none' });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    } finally { await context.close(); }
  });

  for (const palette of palettes) for (const appearance of ['light', 'dark']) {
    test(`axe whole fixture: ${palette}/${appearance} desktop and mobile`, async ({ page }) => {
      await mount(page);
      await page.evaluate(({ palette, appearance }) => { document.documentElement.dataset.uiPalette = palette; document.documentElement.dataset.appearance = appearance; }, { palette, appearance });
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 980 });
        const colors = await page.evaluate(expected => {
          const root = getComputedStyle(document.documentElement);
          const probe = document.createElement('span'); document.body.append(probe);
          const rgb = (channels: string) => { probe.style.color = `hsl(${channels})`; return getComputedStyle(probe).color; };
          const tokens = ['--ls-primary', '--ls-on-primary', '--ls-surface-0'].map(name => ({ name, actual: rgb(root.getPropertyValue(name)), expected: rgb(expected[name]) }));
          const prominent = getComputedStyle(document.querySelector('.lq-btn--prominent')!);
          const button = { actualFill: prominent.backgroundColor, expectedFill: rgb(expected['--ls-primary']), actualInk: prominent.color, expectedInk: rgb(expected['--ls-on-primary']) };
          probe.remove(); return { tokens, button };
        }, tokenExport.themes[palette][appearance]);
        colors.tokens.forEach(item => expect(item.actual, `${palette}/${appearance}/${width}/${item.name}`).toBe(item.expected));
        // Theme changes animate background-color; assert the settled rendered
        // pair with Playwright's retrying CSS assertion instead of a timer.
        await expect(page.locator('.lq-btn--prominent').first()).toHaveCSS('background-color', colors.button.expectedFill);
        await expect(page.locator('.lq-btn--prominent').first()).toHaveCSS('color', colors.button.expectedInk);
        const result = await new AxeBuilder({ page }).analyze();
        expect(result.violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) })), `${palette}/${appearance}/${width}`).toEqual([]);
      }
    });
  }
});
