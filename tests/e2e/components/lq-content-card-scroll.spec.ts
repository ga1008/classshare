import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';

// Content cards (compound Buttons and surface-shaped choices) are sized by their
// content. A scrolling flex column must scroll past them, never compress them:
// the capsule height floor is not a shrink target (2026-10-26, 申诉与申请 drawer).
const CARD_COUNT = 12;
const COLUMN_HEIGHT = 240;

function compoundCard(index: number): string {
  return `<button type="button" data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass card">
      <div class="top"><span>撤回提交</span><span class="lq-badge">待审批</span></div>
      <div class="title">学生 ${index}</div>
      <div class="note">理由说明第一行，理由说明第二行，理由说明第三行需要完整换行显示。</div>
      <div class="meta">2026/10/26 12:0${index % 10}</div>
    </button>`;
}

function surfaceChoice(index: number): string {
  return `<button type="button" data-lq-component="choice" data-lq-shape="surface"
      class="lq-btn lq-btn--sm lq-btn--glass lq-domain-choice card entry">
      <strong>课堂对话 · 对话 ${index}</strong><small>2026-10-26 12:0${index % 10}</small>
    </button>`;
}

async function mount(page: Page) {
  const cards = (factory: (i: number) => string) => Array.from({ length: CARD_COUNT }, (_, i) => factory(i + 1)).join('');
  await page.setContent(`<!doctype html><html lang="zh-CN" data-appearance="light" data-theme="lanshare">
    <body>
      <div id="compound" class="column">${cards(compoundCard)}</div>
      <div id="choices" class="column">${cards(surfaceChoice)}</div>
    </body></html>`);
  await page.addStyleTag({ content: fs.readFileSync('static/css/tailwind-app.css', 'utf8') });
  // Consumer layout only: a bounded scrolling column, block cards, stacked entries.
  await page.addStyleTag({ content: `
    .column { width: 320px; height: ${COLUMN_HEIGHT}px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; margin-bottom: 24px; }
    .card { display: block; width: 100%; text-align: start; white-space: normal; }
    .top { display: flex; justify-content: space-between; }
    .entry { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
  ` });
}

async function overflowReport(page: Page, columnId: string) {
  return page.locator(`#${columnId}`).evaluate((column) => {
    const cards = [...column.querySelectorAll<HTMLElement>('.card')];
    return {
      scrolls: column.scrollHeight > column.clientHeight,
      overflowing: cards.filter(card => card.scrollHeight > card.clientHeight + 1).length,
      shortest: Math.min(...cards.map(card => card.getBoundingClientRect().height)),
    };
  });
}

test('content cards keep their content height inside a scrolling flex column', async ({ page }) => {
  await mount(page);
  for (const columnId of ['compound', 'choices']) {
    const report = await overflowReport(page, columnId);
    expect(report.scrolls, `${columnId} column scrolls`).toBe(true);
    expect(report.overflowing, `${columnId} cards spilling over their box`).toBe(0);
  }
  // A compound card with a two-line note is taller than two text lines plus its inset.
  expect((await overflowReport(page, 'compound')).shortest).toBeGreaterThan(64);
});
