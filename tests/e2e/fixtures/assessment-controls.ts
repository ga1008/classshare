import { expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

/** Load the production asset graph without an application or database. */
export async function mountAssessmentControls(page: Page, { appearance = 'light', drawing = false } = {}) {
  const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
  const asset = (key: string) => {
    if (!manifest.entries[key]) throw Error(`Build required: missing ${key}`);
    return `/static/${manifest.entries[key]}`;
  };
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://assessment-controls.test') return route.abort();
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({
        contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file),
      });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html>
      <html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="${appearance}" data-lq-glass="tinted">
      <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>作答工具验收</title>
      <link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/exam-take.css')}">
      <link rel="stylesheet" href="${asset('css/assessment-editor.css')}"></head><body>
      <main style="max-width:900px;margin:auto;padding:32px 16px"><h1>作答工具验收</h1><form id="answer-form">
      <label for="answer">程序题答案</label><textarea id="answer" name="answer" rows="2" data-lq-component="textarea" class="lq-textarea"></textarea>
      <button type="button" id="after">下一题</button></form></main>
      <script src="${asset('js/marked.min.js')}"></script><script src="${asset('js/markdown_runtime.js')}"></script>
      <script type="module">import * as editorApi from '${asset('js/assessment_editor.js')}';
      import { initExamDrawingWhiteboard } from '${asset('js/whiteboard/exam_board.js')}';
      import * as tooltipApi from '${asset('js/lq/tooltips.js')}';
      window.editorApi=editorApi; window.tooltipApi=tooltipApi; window.original=document.getElementById('answer');
      if (${drawing}) window.board=initExamDrawingWhiteboard(); else window.editor=editorApi.mountAssessmentEditor(window.original,{code:true,label:'程序题答案'});
      document.body.dataset.ready='true';</script></body></html>` });
    return route.abort();
  });
  await page.goto('https://assessment-controls.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}
