'use strict';
/**
 * 液态玻璃布局可读性探针（只读，隔离合成运行环境专用）。
 *
 * 登录合成账号，按亮/暗外观遍历路由，在页面内统计四类问题：
 *   tight    —— 有底色/描边的板块，内容贴边（内边距 < 6px）
 *   glued    —— 相邻两个有底色的兄弟板块间距 < 4px
 *   contrast —— 文字与合成后背景对比度 < 3（大字）/ 4.5（正文）
 *   overlay  —— 浮层（fixed/absolute + z-index）底色不透明度 < .82 且无 backdrop-filter
 *   capsule  —— 胶囊形控件高于两行文字（多行或嵌套内容被塞进胶囊）
 * 背景按祖先底色逐层合成到页面场景色调（html[data-lq-scene-tone]）上估算；照片背景
 * 只用色调近似，结论是"候选"，需截图复核。
 *
 *   node tools/ui/probe_layout_readability.cjs --base http://127.0.0.1:8023 \
 *        --fixture .codex-temp/p03-runtime/fixture.json --role student \
 *        --routes /dashboard,/profile?section=overview --output report.json
 */
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('@playwright/test');

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 2) out[argv[i].replace(/^--/, '')] = argv[i + 1];
  return out;
}

async function login(page, base, fixture, role) {
  if (role === 'teacher') {
    await page.goto(`${base}/teacher/login`);
    await page.locator('#email').fill(fixture.teacher.email);
    await page.locator('#password').fill(fixture.password);
    await Promise.all([page.waitForURL(/\/dashboard/, { timeout: 30000 }), page.locator('button[type="submit"]').click()]);
  } else {
    await page.goto(`${base}/student/login`);
    await page.locator('#identifier').fill(fixture.student.studentNumber);
    await page.locator('#password').fill(fixture.password);
    await Promise.all([page.waitForURL(/\/dashboard/, { timeout: 30000 }), page.locator('#student-password-login-form button[type="submit"]').click()]);
  }
}

async function setAppearance(page, base, appearance) {
  const current = (await (await page.request.get(`${base}/api/profile/ui-preferences`)).json()).preferences;
  const response = await page.request.patch(`${base}/api/profile/ui-preferences`, {
    headers: { 'X-UI-Preferences-Context': current.context_token }, data: { appearance, version: current.version },
  });
  if (!response.ok()) throw new Error(`appearance ${appearance}: HTTP ${response.status()}`);
  return current;
}

/* Runs inside the page. Pure DOM/CSSOM reads; never mutates the document. */
function probe() {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const rgba = value => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1); const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const parse = value => { if (!value || value === 'transparent') return [0, 0, 0, 0]; const m = value.match(/rgba?\(([^)]+)\)/); if (!m) return rgba(value); const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const over = (f, b) => { const a = f[3] + b[3] * (1 - f[3]); return [0, 1, 2].map(i => a ? (f[i] * f[3] + b[i] * b[3] * (1 - f[3])) / a : 0).concat(a); };
  const lum = c => c.slice(0, 3).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((s, v, i) => s + v * [.2126, .7152, .0722][i], 0);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
  const tone = document.documentElement.dataset.lqSceneTone || (document.documentElement.dataset.appearance === 'dark' ? 'dark' : 'light');
  const scene = tone === 'dark' ? [28, 32, 42, 1] : [226, 229, 234, 1];
  const visible = el => { const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return false; for (let n = el; n; n = n.parentElement) { const s = getComputedStyle(n); if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) < .05) return false; } return true; };
  const bgOf = el => { const s = getComputedStyle(el); let c = parse(s.backgroundColor); if (c[3] < .02 && s.backgroundImage.includes('gradient')) { const m = s.backgroundImage.match(/rgba?\([^)]+\)/); if (m) c = parse(m[0]); } return c; };
  const painted = el => { const s = getComputedStyle(el); const b = bgOf(el); const bw = parseFloat(s.borderTopWidth) + parseFloat(s.borderLeftWidth); const bc = parse(s.borderTopColor); return b[3] > .12 || (bw > 0 && bc[3] > .2 && parseFloat(s.borderTopLeftRadius) > 4); };
  const backdrop = !!document.querySelector('.lq-page-backdrop, [data-lq-page-backdrop]') || !!document.documentElement.dataset.lqSceneTone;
  // With a page backdrop image the photo sits between body and content: skip body's own colour.
  const composite = el => { const chain = []; for (let n = el; n && n !== document.documentElement && !(backdrop && n === document.body); n = n.parentElement) chain.push(n); let c = scene; for (const n of chain.reverse()) { const b = bgOf(n); if (b[3] > 0) c = over(b, c); } return c; };
  const label = el => { const id = el.id ? `#${el.id}` : ''; const cls = (el.className && typeof el.className === 'string') ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : ''; const own = `${el.tagName.toLowerCase()}${id}${cls}`; if (id || cls) return own; const parent = el.parentElement; const pc = parent && typeof parent.className === 'string' && parent.className.trim() ? '.' + parent.className.trim().split(/\s+/)[0] : ''; return `${parent ? parent.tagName.toLowerCase() + pc : ''} > ${own}`; };
  const issues = { tight: [], glued: [], contrast: [], overlay: [], capsule: [] };
  const all = [...document.body.querySelectorAll('*')].filter(el => !['SCRIPT', 'STYLE', 'svg', 'path', 'BR', 'OPTION'].includes(el.tagName));
  for (const el of all) {
    if (!visible(el)) continue;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    // tight: a painted block of real size whose in-flow content touches the edge
    if (r.width * r.height > 9000 && painted(el) && !el.matches('button, a.lq-btn, input, select, textarea, .lq-btn, .lq-chip, img, video, canvas, iframe, progress, [role="progressbar"], .lq-input, .lq-select')) {
      const kids = [...el.children].filter(k => visible(k) && getComputedStyle(k).position !== 'absolute' && getComputedStyle(k).position !== 'fixed');
      if (kids.length) {
        const pad = Math.min(...kids.map(k => { const q = k.getBoundingClientRect(); return Math.min(q.left - r.left, q.top - r.top, r.right - q.right) - parseFloat(s.borderLeftWidth); }));
        const ownPad = parseFloat(s.paddingLeft) + parseFloat(s.paddingTop);
        if (pad < 6 && ownPad < 6 && s.overflow !== 'auto' && s.overflowY !== 'auto') issues.tight.push({ el: label(el), pad: Math.round(pad), size: [r.width | 0, r.height | 0] });
      }
    }
    // glued: painted sibling blocks stacked with no gap
    if (painted(el) && r.width * r.height > 9000) {
      let next = el.nextElementSibling; while (next && !visible(next)) next = next.nextElementSibling;
      if (next && painted(next) && next.getBoundingClientRect().width * next.getBoundingClientRect().height > 9000) {
        const q = next.getBoundingClientRect();
        const gap = Math.max(q.top - r.bottom, q.left - r.right);
        if (gap > -2 && gap < 4) issues.glued.push({ el: label(el), next: label(next), gap: Math.round(gap) });
      }
    }
    // capsule: a capsule-shaped control taller than two text lines (multi-line or
    // nested content squeezed into rounded ends), or left-aligned content whose
    // inset is smaller than half the corner radius.
    if (el.matches('button, a, summary, [role="tab"], .lq-btn, .lq-chip') && !el.matches('.lq-btn--icon') && r.width > r.height * 1.25 && parseFloat(s.borderTopLeftRadius) >= r.height * 0.45) {
      // Real text lines (distinct line-box tops of the text inside), and any
      // painted child whose distance to the rounded edge is under half the radius.
      const centres = [];
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let t = walker.nextNode(); t; t = walker.nextNode()) {
        if (!t.textContent.trim() || !visible(t.parentElement)) continue;
        const range = document.createRange(); range.selectNodeContents(t);
        for (const q of range.getClientRects()) if (q.width > 1 && q.height > 4) centres.push(q.top + q.height / 2);
      }
      centres.sort((a, b) => a - b);
      const lines = centres.reduce((n, c, i) => n + (i === 0 || c - centres[i - 1] > 9 ? 1 : 0), 0);
      const radius = Math.min(parseFloat(s.borderTopLeftRadius), r.height / 2);
      const squeezed = [...el.querySelectorAll('*')].some(k => painted(k) && visible(k) && Math.min(k.getBoundingClientRect().left - r.left, r.right - k.getBoundingClientRect().right) < radius * 0.5);
      if (lines > 2 || squeezed) issues.capsule.push({ el: label(el), height: Math.round(r.height), lines, squeezed });
    }
    // overlay: floating layer without enough opacity or frost
    const z = parseInt(s.zIndex, 10);
    if ((s.position === 'fixed' || s.position === 'absolute') && z > 5 && r.width * r.height > 12000 && bgOf(el)[3] < .82 && !/blur/.test(s.backdropFilter || s.webkitBackdropFilter || '') && painted(el)) {
      issues.overlay.push({ el: label(el), alpha: +bgOf(el)[3].toFixed(2), filter: s.backdropFilter });
    }
    // contrast: own text nodes only
    const text = [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim()).map(n => n.textContent.trim()).join(' ');
    if (text && !el.matches('input, textarea, select, option')) {
      const fg = parse(s.color); const bg = composite(el); const fgc = over(fg, bg);
      const big = parseFloat(s.fontSize) >= 18.5 || (parseFloat(s.fontSize) >= 14 && Number(s.fontWeight) >= 700);
      const value = ratio(fgc, bg);
      if (value < (big ? 3 : 4.5)) issues.contrast.push({ el: label(el), text: text.slice(0, 24), ratio: +value.toFixed(2), color: s.color, bg: `rgb(${bg.slice(0, 3).map(Math.round).join(',')})` });
    }
  }
  // the same component repeats; keep the first few per selector
  for (const key of Object.keys(issues)) {
    const seen = new Map();
    issues[key] = issues[key].filter(item => { const k = item.el + (item.next || ''); seen.set(k, (seen.get(k) || 0) + 1); return seen.get(k) <= 2; });
  }
  return { tone, issues };
}

(async () => {
  const opt = args(process.argv.slice(2));
  const fixture = JSON.parse(fs.readFileSync(opt.fixture, 'utf8'));
  // --steps file.json: [{"route": "/x", "click": ["selector", ...], "label": "..."}] opens
  // dialogs/popovers before measuring; --routes stays the simple comma list.
  const steps = opt.steps ? JSON.parse(fs.readFileSync(opt.steps, 'utf8')) : (opt.routes || '/dashboard').split(',').map(route => ({ route }));
  const appearances = (opt.appearances || 'light,dark').split(',');
  const browserPath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  const browser = await chromium.launch({ headless: true, ...(fs.existsSync(browserPath) ? { executablePath: browserPath } : {}) });
  const report = { base: opt.base, role: opt.role, measuredAt: new Date().toISOString(), pages: [] };
  try {
    const context = await browser.newContext({ viewport: { width: Number(opt.width || 1440), height: Number(opt.height || 1000) } });
    const page = await context.newPage();
    await login(page, opt.base, fixture, opt.role || 'student');
    const original = await setAppearance(page, opt.base, appearances[0]);
    try {
      for (const appearance of appearances) {
        await setAppearance(page, opt.base, appearance);
        for (const step of steps) {
          const route = step.label || step.route;
          try {
            await page.goto(`${opt.base}${step.route}`, { waitUntil: 'load', timeout: 45000 });
            await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => undefined);
            await page.waitForTimeout(600);
            // First-run guides (teacher onboarding) cover the page; close them when present.
            const guide = page.locator('[data-teacher-onboarding-dismiss]:visible').first();
            if (await guide.count()) { await guide.click({ timeout: 3000 }).catch(() => undefined); await page.waitForTimeout(400); }
            for (const selector of step.click || []) { await page.locator(selector).first().click({ timeout: 8000 }); await page.waitForTimeout(500); }
            if (opt.shots) await page.screenshot({ path: path.join(opt.shots, `${appearance}-${route.replace(/[^a-z0-9]+/gi, '_').slice(0, 60)}.png`) });
            const result = await page.evaluate(probe);
            const counts = Object.fromEntries(Object.entries(result.issues).map(([k, v]) => [k, v.length]));
            report.pages.push({ route, appearance, finalUrl: page.url().replace(opt.base, ''), counts, ...result });
          } catch (error) { report.pages.push({ route, appearance, error: error.message }); }
        }
      }
    } finally { await setAppearance(page, opt.base, original.appearance || 'system').catch(() => undefined); }
  } finally { await browser.close(); }
  const json = `${JSON.stringify(report, null, 1)}\n`;
  if (opt.output) { fs.mkdirSync(path.dirname(path.resolve(opt.output)), { recursive: true }); fs.writeFileSync(opt.output, json); }
  const summary = report.pages.map(p => `${p.appearance.padEnd(5)} ${p.route.padEnd(48)} ${p.error ? 'ERROR ' + p.error.slice(0, 80) : JSON.stringify(p.counts)}`).join('\n');
  process.stdout.write(`${summary}\n`);
})().catch(error => { console.error(error); process.exit(2); });
