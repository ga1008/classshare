import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';

async function mount(page: Page) {
  await page.setContent(`<!doctype html><html lang="zh-CN" data-appearance="light" data-theme="lanshare">
    <body><form id="owner"><input name="draft" value="保留草稿"></form>
    <button id="before">前一项</button>
    <button id="action" data-lq-component="button" class="lq-btn lq-btn--glass late-control" hidden
      type="submit" name="intent" value="save" form="owner" disabled>保存</button>
    <input id="field" data-lq-component="input" class="lq-input late-field" hidden name="context" value="0" form="owner">
    <div id="surface" data-lq-component="surface" class="lq-surface" hidden>隐藏内容</div>
    <section id="searchable" data-lq-component="surface" class="lq-surface" hidden="until-found">页面查找可发现的内容</section>
    <button id="after">后一项</button></body></html>`);
  // Exercise the actual release cascade, followed by legacy display rules.
  await page.addStyleTag({ content: fs.readFileSync('static/css/tailwind-app.css', 'utf8') });
  await page.addStyleTag({ content: '.late-control{display:inline-flex}.late-field{display:block}.lq-surface{display:block}' });
}

test('LQ explicit ownership preserves native hidden, until-found and form semantics', async ({ page }) => {
  await mount(page);
  for (const id of ['action', 'field', 'surface']) await expect(page.locator(`#${id}`)).toBeHidden();
  await expect(page.locator('#searchable')).not.toHaveCSS('display', 'none');
  await expect(page.locator('#searchable')).toHaveCSS('content-visibility', 'hidden');
  await page.locator('#before').focus();
  await page.keyboard.press('Tab');
  await expect(page.locator('#after')).toBeFocused();
  expect(await page.locator('#owner').evaluate(form => [...new FormData(form as HTMLFormElement)])).toEqual([
    ['draft', '保留草稿'], ['context', '0'],
  ]);
  await page.evaluate(() => {
    for (const id of ['action', 'field', 'surface']) document.getElementById(id)!.hidden = false;
  });
  await expect(page.locator('#action')).toBeVisible();
  await expect(page.locator('#action')).toBeDisabled();
  await expect(page.locator('#field')).toHaveValue('0');
  await expect(page.locator('#surface')).toBeVisible();
  await page.evaluate(() => { (document.getElementById('action') as HTMLButtonElement).disabled = false; });
  expect(await page.evaluate(() => {
    const form = document.getElementById('owner') as HTMLFormElement;
    const action = document.getElementById('action') as HTMLButtonElement;
    return [...new FormData(form, action)];
  })).toEqual([['draft', '保留草稿'], ['intent', 'save'], ['context', '0']]);
  await page.evaluate(() => { document.getElementById('action')!.setAttribute('hidden', 'hidden'); });
  await expect(page.locator('#action')).toBeHidden();
});

test('LQ destructive actions retain semantic color across retained ghost classes and press', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    document.body.insertAdjacentHTML('beforeend', `
      <button id="remove" data-lq-component="button" type="button"
        class="lq-btn lq-btn--sm lq-btn--destructive btn btn-ghost btn-sm text-danger">删除课次</button>
      <span id="danger-ink" style="color:hsl(var(--ls-tone-danger-fg))"></span>
      <span id="danger-fill" style="color:hsl(var(--ls-tone-danger-solid))"></span>
      <span id="danger-on-fill" style="color:hsl(var(--ls-tone-danger-on-solid))"></span>`);
  });
  const remove = page.locator('#remove');
  for (const appearance of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.appearance = value; }, appearance);
    const colors = await page.evaluate(() => Object.fromEntries(['danger-ink', 'danger-fill', 'danger-on-fill']
      .map(id => [id, getComputedStyle(document.getElementById(id)!).color])));
    await expect(remove).toHaveCSS('color', colors['danger-ink']);
    await expect(remove).toHaveCSS('backdrop-filter', 'none');
    await remove.focus();
    await page.keyboard.down('Space');
    await expect(remove).toHaveCSS('color', colors['danger-on-fill']);
    await expect(remove).toHaveCSS('background-color', colors['danger-fill']);
    await page.keyboard.up('Space');
    await expect(remove).toHaveCSS('color', colors['danger-ink']);
  }
});

test('LQ selected surfaces retain a visible shared state in light dark and transparency off', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => document.body.insertAdjacentHTML('beforeend', `
    <article id="selected-school" data-lq-component="surface" data-lq-selected="true" class="lq-surface org-school-card is-active" data-school-code="owned-school">School
      <div id="unselected-college" data-lq-component="surface" class="lq-surface org-unit-card" data-college-id="42">College</div>
    </article>
    <span id="selected-ink" style="color:hsl(var(--ls-on-primary-soft))"></span>
    <span id="selected-edge" style="color:hsl(var(--ls-primary))"></span>`));
  for (const appearance of ['light', 'dark']) for (const transparency of ['tinted', 'off']) {
    await page.evaluate(({ appearance, transparency }) => {
      document.documentElement.dataset.appearance = appearance;
      document.documentElement.dataset.lqGlass = transparency;
    }, { appearance, transparency });
    const state = await page.evaluate(() => {
      const selected = getComputedStyle(document.getElementById('selected-school')!);
      const unselected = getComputedStyle(document.getElementById('unselected-college')!);
      return { selectedImage: selected.backgroundImage, unselectedImage: unselected.backgroundImage,
        selectedFill: selected.backgroundColor, unselectedFill: unselected.backgroundColor,
        selectedInk: selected.color, expectedInk: getComputedStyle(document.getElementById('selected-ink')!).color,
        selectedBorder: selected.borderColor, expectedBorder: getComputedStyle(document.getElementById('selected-edge')!).color,
        unselectedBorder: unselected.borderColor, blur: selected.backdropFilter,
        schoolCode: document.getElementById('selected-school')!.dataset.schoolCode,
        collegeId: document.getElementById('unselected-college')!.dataset.collegeId };
    });
    if (transparency === 'off') expect(state.selectedImage).toBe('none');
    else expect(state.selectedImage).toContain('linear-gradient');
    expect(state.unselectedImage).toBe('none');
    expect(state.selectedInk).toBe(state.expectedInk);
    // The existing school-card border transition intentionally survives theme changes.
    await expect(page.locator('#selected-school')).toHaveCSS('border-color', state.expectedBorder);
    await expect(page.locator('#selected-school')).not.toHaveCSS('border-color', state.unselectedBorder);
    expect(state.blur).toBe('none');
    expect([state.schoolCode, state.collegeId]).toEqual(['owned-school', '42']);
    if (transparency === 'off') {
      expect(state.selectedFill).not.toMatch(/^rgba/);
      expect(state.unselectedFill).not.toMatch(/^rgba/);
    }
  }
});
