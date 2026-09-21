import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

type Case = { kind: string; props: Record<string, any>; normalized?: any; html?: string; error?: string };
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture: { cases: Case[]; invalid: Case[]; composition: string; isolated: boolean } = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_forms.py'], { encoding: 'utf8' }));
const tokens = JSON.parse(fs.readFileSync('docs/lq-tokens.json', 'utf8'));
const palettes = ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal'];
const entries = ['jinja', 'html', 'element'];

async function mount(page: Page, entry = 'jinja', enhance = true, cases = fixture.cases) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-forms.test') return route.abort();
    if (url.pathname.startsWith('/static/js/lq/') || ['/static/css/tailwind-app.css', '/static/css/lq/components/forms.css'].includes(url.pathname)) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="indigo" data-appearance="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ forms</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{max-width:800px;margin:auto;padding:24px}form{display:grid;gap:24px}h1{margin-bottom:24px}#external-help{margin-bottom:16px}</style></head><body><main><h1>LQ 原生表单</h1><p id="external-help">来自页面的常显帮助</p><form id="native-form"><button type="submit" id="submit">提交</button></form></main><script type="module">import * as forms from '/static/js/lq/forms.js';window.forms=forms;document.body.dataset.ready='true';</script></body></html>` });
    return route.abort();
  });
  await page.goto('https://lq-forms.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  await page.evaluate(({ cases, entry, enhance }) => {
    const api = (window as any).forms, form = document.querySelector('#native-form')!;
    for (const item of cases) {
      let element;
      if (entry === 'element') element = api.createForm(item.kind, item.props);
      else { const t = document.createElement('template'); t.innerHTML = entry === 'jinja' ? item.html! : api.formMarkup(item.kind, item.props); element = t.content.firstElementChild; }
      form.insertBefore(element, form.lastElementChild);
    }
    for (const identity of ['mode-a', 'mode-b']) {
      const radio = document.getElementById(identity); const section = document.querySelector('#basic-section [data-lq-slot]');
      if (radio && section) section.append(radio.closest('.lq-field')!);
    }
    const external = document.querySelector('[data-lq-field="external"]'); if (external) document.querySelector('main')!.append(external);
    (window as any).submissions = 0;
    form.addEventListener('submit', event => { event.preventDefault(); (window as any).submissions++; });
    if (enhance) (window as any).lease = api.enhanceForms(document);
  }, { cases, entry, enhance });
}

test.describe('LQ native forms', () => {
  test('real Jinja normalization and DOM match HTML/Element factories, including textarea leading newline', async ({ page }) => {
    expect(fixture.isolated).toBe(true);
    expect(fixture.cases.filter(item => item.error)).toEqual([]);
    await mount(page, 'jinja', false, []);
    const result = await page.evaluate(cases => {
      const api = (window as any).forms;
      const semantic = (n: Node): any => n.nodeType === Node.TEXT_NODE ? (n.textContent?.trim() ? { text: n.textContent } : null) : ({ tag: (n as Element).tagName.toLowerCase(), attrs: Object.fromEntries([...(n as Element).attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(semantic).filter(Boolean) });
      const parse = (markup: string) => { const t = document.createElement('template'); t.innerHTML = markup; return [...t.content.childNodes].map(semantic).filter(Boolean); };
      return cases.map(item => ({ kind: item.kind, id: item.props.id, props: api.formProps(item.kind, item.props), jinja: parse(item.html!), html: parse(api.formMarkup(item.kind, item.props)), element: [semantic(api.createForm(item.kind, item.props))] }));
    }, fixture.cases);
    result.forEach((item, i) => {
      expect(item.props, `${item.kind}/${item.id} props`).toEqual(fixture.cases[i].normalized);
      expect(item.html, `${item.kind}/${item.id} HTML`).toEqual(item.jinja);
      expect(item.element, `${item.kind}/${item.id} Element`).toEqual(item.jinja);
    });
  });

  test('invalid state, fake readonly, unsafe attributes and inconsistent values reject at every entrance', async ({ page }) => {
    expect(fixture.invalid.every(item => item.error === 'ValueError')).toBe(true);
    await mount(page, 'jinja', false, []);
    const rejected = await page.evaluate(cases => cases.map(item => {
      const api = (window as any).forms;
      return ['formProps', 'formMarkup', 'createForm'].map(method => { try { api[method](item.kind, item.props); return false; } catch (error) { return error instanceof TypeError; } });
    }), fixture.invalid);
    rejected.forEach((value, i) => expect(value, JSON.stringify(fixture.invalid[i])).toEqual([true, true, true]));
  });

  test('structure slots compose existing DOM controls and real Jinja callers without raw HTML props', async ({ page }) => {
    await mount(page, 'jinja', false, []);
    const composition = await page.evaluate(jinja => {
      const api = (window as any).forms;
      const input = () => api.input({ id: 'composed-input', label: '组合字段', value: '保留' });
      const button = () => { const el = document.createElement('button'); el.type = 'submit'; el.textContent = '保存'; return el; };
      const built = document.createElement('div'); built.append(api.formSection({ id: 'composed', title: '组合分组' }, [input()]), api.formActions({ hint: '常显说明' }, [button()]));
      const parsed = document.createElement('div'); parsed.innerHTML = api.html.form_section({ id: 'composed', title: '组合分组' }) + api.html.form_actions({ hint: '常显说明' });
      parsed.querySelector('.lq-form-section__content')!.append(input()); parsed.querySelector('.lq-form-actions__content')!.append(button());
      const ssr = document.createElement('div'); ssr.innerHTML = jinja;
      const tree = (el: Element): any => ({ tag: el.tagName, attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value]).sort()), text: [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(''), children: [...el.children].map(tree) });
      let rawRejected = false; try { api.formActions({}, ['<button>unsafe</button>']); } catch { rawRejected = true; }
      return { built: tree(built), parsed: tree(parsed), ssr: tree(ssr), rawRejected };
    }, fixture.composition);
    expect(composition.rawRejected).toBe(true);
    expect(composition.built).toEqual(composition.ssr);
    expect(composition.parsed).toEqual(composition.ssr);
  });

  for (const entry of entries) {
    test(`${entry}: native labels, descriptions, radio/select/range/switch keyboard and form values`, async ({ page }) => {
      await mount(page, entry);
      await expect(page.locator('#course')).toHaveAccessibleName('课程名称');
      await expect(page.locator('#course')).toHaveAccessibleDescription('来自页面的常显帮助 请填写课程的正式名称。 名称已存在，请检查所属学期。');
      await page.locator('label[for="course"]').click(); await expect(page.locator('#course')).toBeFocused();
      await page.locator('#mode-a').focus(); await page.keyboard.press('ArrowRight'); await expect(page.locator('#mode-b')).toBeChecked();
      await expect(page.locator('#mode-a')).not.toBeChecked();
      await expect(page.locator('#basic-section')).toHaveAccessibleName('基本信息');
      await page.locator('#publish').focus(); await page.keyboard.press('Space'); await expect(page.getByRole('switch')).toBeChecked();
      await page.locator('#consent').focus(); await page.keyboard.press('Space'); await expect(page.locator('#consent')).not.toBeChecked();
      await page.locator('#weight').focus(); await page.keyboard.press('ArrowRight'); await expect(page.locator('#weight')).toHaveValue('30');
      await expect(page.locator('#weight--lq-value')).toHaveText('30');
      await page.locator('#semester').focus(); await page.keyboard.press('Home'); await page.keyboard.press('ArrowDown'); await expect(page.locator('#semester')).toHaveValue('2026');
      await page.locator('#readonly').focus(); await page.keyboard.press('ControlOrMeta+A'); await page.keyboard.type('overwrite'); await expect(page.locator('#readonly')).toHaveValue('7');
      const data = await page.locator('form').evaluate(form => Object.fromEntries(new FormData(form as HTMLFormElement)));
      expect(data).toMatchObject({ course_name: '网络原理', credits: '2.5', revision: '7', external_value: '保留', semester: '2026', mode: 'online', weight: '30', published: '1' });
      expect(data).not.toHaveProperty('locked');
      expect(await page.evaluate(() => (window as any).submissions)).toBe(0);
    });

    test(`${entry}: clear emits native events, textarea keeps IME/newlines, summaries preserve edited values`, async ({ page }) => {
      await mount(page, entry);
      await page.locator('#course').fill('用户修改后保留');
      await page.locator('#typed-field').fill('遇到 409 后仍然保留');
      await page.locator('[data-lq-error-target="typed-field"]').click(); await expect(page.locator('#typed-field')).toBeFocused();
      await expect(page.locator('#typed-field')).toHaveValue('遇到 409 后仍然保留');
      expect(await page.evaluate(() => (window as any).forms.focusFirstError(document.querySelector('#errors')))).toBe(true);
      await expect(page.locator('#course')).toBeFocused(); await expect(page.locator('#course')).toHaveValue('用户修改后保留');
      await page.locator('#search').evaluate(el => { (window as any).inputEvents = []; for (const type of ['input', 'change']) el.addEventListener(type, () => (window as any).inputEvents.push(type)); });
      await page.locator('[data-lq-clear="search"]').click(); await expect(page.locator('#search')).toHaveValue(''); await expect(page.locator('#search')).toBeFocused();
      expect(await page.evaluate(() => (window as any).inputEvents)).toEqual(['input', 'change']);
      await page.locator('#notes').focus(); await page.keyboard.press('ControlOrMeta+End'); await page.keyboard.press('Enter'); await page.keyboard.type('third');
      await expect(page.locator('#notes')).toHaveValue('第一行\n第二行😀\nthird');
      await page.locator('#notes').dispatchEvent('compositionstart');
      const composingEvent = await page.locator('#notes').evaluate(el => { const e = new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true, cancelable: true }); el.dispatchEvent(e); return e.defaultPrevented; });
      expect(composingEvent).toBe(false);
      await page.locator('#notes').dispatchEvent('compositionend');
      expect(await page.evaluate(() => (window as any).submissions)).toBe(0);
      const value = await page.locator('#notes').inputValue(); await expect(page.locator('#notes--lq-count')).toHaveText(`${value.length} / 300 字`);
    });
  }

  test('SSR native disabled/readonly and required validation work without enhancement', async ({ page }) => {
    await mount(page, 'jinja', false);
    await expect(page.locator('#disabled')).toBeDisabled(); await expect(page.locator('#locked-check')).toBeDisabled();
    await page.locator('#submit').click(); expect(await page.evaluate(() => (window as any).submissions)).toBe(0);
    await expect(page.locator('#email')).toBeFocused();
    await page.locator('#email').fill('teacher@example.org'); await page.locator('#submit').click();
    expect(await page.evaluate(() => (window as any).submissions)).toBe(1);
    await expect(page.locator('#course')).toHaveValue('网络原理');
  });

  test('enhancement ownership is reference counted, restores autoGrow styles and ignores queued reset after dispose', async ({ page }) => {
    await mount(page);
    const result = await page.evaluate(async () => {
      const api = (window as any).forms, first = (window as any).lease, duplicate = await import('/static/js/lq/forms.js?duplicate');
      const second = duplicate.enhanceForms(document), notes = document.querySelector('#notes') as HTMLTextAreaElement, input = document.querySelector('#search') as HTMLInputElement;
      const before = notes.style.height;
      first.dispose(); input.value = 'still active'; input.dispatchEvent(new Event('input', { bubbles: true }));
      (document.querySelector('[data-lq-clear="search"]') as HTMLButtonElement).click(); const activeValue = input.value;
      (document.querySelector('form') as HTMLFormElement).reset(); second.dispose(); second.dispose();
      await Promise.resolve();
      input.value = 'after dispose'; (document.querySelector('[data-lq-clear="search"]') as HTMLButtonElement).click();
      return { before, restored: notes.style.height, activeValue, final: input.value };
    });
    expect(result.before).not.toBe(''); expect(result.restored).toBe(''); expect(result.activeValue).toBe(''); expect(result.final).toBe('after dispose');
  });

  test('autoGrow handles late controls and width changes; native reset refreshes count and clear state', async ({ page }) => {
    await mount(page);
    await page.locator('#notes').fill('很长的教学反思内容。'.repeat(24));
    const before = await page.locator('#notes').evaluate(el => el.getBoundingClientRect().height);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.locator('#notes').evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThan(before);
    await page.evaluate(() => {
      document.querySelector('form')!.append((window as any).forms.textarea({ id: 'late', label: '稍后加入', value: '多行\n'.repeat(20), autoGrow: true, count: true }));
    });
    await expect.poll(() => page.locator('#late').evaluate((el: HTMLTextAreaElement) => parseFloat(el.style.height))).toBeGreaterThan(200);
    await page.locator('form').evaluate((el: HTMLFormElement) => el.reset());
    await expect(page.locator('#notes')).toHaveValue('第一行\n第二行😀'); await expect(page.locator('#notes--lq-count')).toHaveText('9 / 300 字');
    await expect(page.locator('[data-lq-clear="search"]')).toBeVisible();
  });

  test('late fieldset/readonly changes update clear state and detached autoGrow nodes release styles', async ({ page }) => {
    await mount(page);
    await page.locator('#locked-section [data-lq-slot]').evaluate(slot => { slot.append(document.querySelector('[data-lq-field="search"]')!); });
    await expect(page.locator('#search')).toBeDisabled(); await expect(page.locator('[data-lq-clear="search"]')).toBeDisabled();
    await page.locator('#locked-section').evaluate((el: HTMLFieldSetElement) => { el.disabled = false; });
    await expect(page.locator('[data-lq-clear="search"]')).toBeEnabled();
    await page.locator('#search').evaluate((el: HTMLInputElement) => { el.readOnly = true; });
    await expect(page.locator('[data-lq-clear="search"]')).toBeDisabled();
    await page.locator('#notes').evaluate(el => { (window as any).detachedNotes = el; el.closest('.lq-field')!.remove(); });
    await expect.poll(() => page.evaluate(() => (window as any).detachedNotes.style.height)).toBe('');
  });

  test('coarse targets, long help, focus/reduced/forced colors and no own blur remain usable at 390px', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }); const page = await context.newPage();
    try {
      await mount(page);
      await page.locator('#course--lq-help').evaluate(el => { el.textContent = '长帮助说明与不可分断的代码'.repeat(30); });
      for (const selector of ['.lq-input', '.lq-select', '.lq-choice', '.lq-field__clear:not([hidden])', '.lq-range']) {
        const rects = await page.locator(selector).evaluateAll(els => els.map(el => el.getBoundingClientRect().height));
        rects.forEach(height => expect(height, selector).toBeGreaterThanOrEqual(44));
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.locator('#course').focus(); await expect(page.locator('#course')).toHaveCSS('outline-style', 'solid');
      // Legacy global CSS forces a tiny duration; no transition property means
      // these controls still create no reduced-motion transition at all.
      await page.emulateMedia({ reducedMotion: 'reduce' }); await expect(page.locator('#course')).toHaveCSS('transition-property', 'none');
      await page.emulateMedia({ forcedColors: 'active' }); await expect(page.locator('#course')).toHaveCSS('box-shadow', 'none');
      expect(await page.locator('.lq-field').evaluateAll(els => [...new Set(els.map(el => getComputedStyle(el).backdropFilter))])).toEqual(['none']);
    } finally { await context.close(); }
  });

  for (const palette of palettes) for (const appearance of ['light', 'dark']) {
    test(`axe full native form ${palette}/${appearance} at desktop and mobile widths`, async ({ page }) => {
      expect(createHash('sha256').update(fs.readFileSync('static/css/lq/tokens.css')).digest('hex')).toBe(tokens.source_sha256);
      expect(Object.keys(tokens.themes).sort()).toEqual(palettes.slice().sort());
      await mount(page);
      await page.evaluate(({ palette, appearance }) => { document.documentElement.dataset.uiPalette = palette; document.documentElement.dataset.appearance = appearance; }, { palette, appearance });
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 980 });
        const channels = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ls-primary').trim());
        expect(channels.replace(/\s+/g, ' ')).toBe(tokens.themes[palette][appearance]['--ls-primary']);
        const results = await new AxeBuilder({ page }).analyze();
        expect(results.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => ({ target: n.target, reason: n.failureSummary })) })), `${palette}/${appearance}/${width}`).toEqual([]);
      }
    });
  }
});
