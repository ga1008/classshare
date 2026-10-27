import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

// No application, database, or external services: use the production asset
// graph, actual Jinja avatar fragments/card partial, and synthetic public APIs.
const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const asset = (name: string) => `/static/${manifest.entries[name]}`;
const keys = ['qi_awakening', 'qi_refining', 'foundation', 'application_seed', 'golden_core',
    'practical_mastery', 'systems_thinking', 'nascent_soul', 'independent_path', 'mentor_heart'];
const image = (tier: number) => `/static/img/cultivation/emblems/${keys[tier - 1]}.svg`;
const wanderer = { id: 21, display_name: '林晚舟', avatar_url: '/api/wanderers/21/avatar', tier_name: '可为人师',
    emblem: { emblem_url: image(10) }, status: 'active', status_label: '活跃', archetype: '夜读者', today_actions: 2,
    daily_action_budget: 8, origin: 'seed', last_action_preview: '正在研读合成课程' };

function templates() {
    return JSON.parse(execFileSync('python', ['-c', `
import json,re,runpy,sys
from pathlib import Path
from jinja2 import Environment,FileSystemLoader
env=Environment(loader=FileSystemLoader('templates'),autoescape=True)
env.globals['lq_props']=runpy.run_path('classroom_app/lq_components.py')['lq_props']
card=json.loads(sys.argv[1])
def avatar(file,cls,context,prefix=''):
    source=Path(file).read_text(encoding='utf-8')
    fragment=re.search(r'<span class="'+re.escape(cls)+r'">[\\s\\S]*?</span>',source).group(0)
    return env.from_string(prefix+fragment).render(**context)
result={
 'admin':avatar('templates/manage/system/wanderers.html','wd-card__avatar',{'card':card}),
 'home':avatar('templates/wanderers/home.html','wh-identity__avatar',{'home':dict(card,badge=card['emblem'])},"{% from 'macros/lq/indicators.html' import lq_avatar %}"),
 'cards':[]}
for variant in ['compact','full','chip']:
    result['cards'].append(env.get_template('partials/cultivation_card.html').render(cultivation_card={
      'variant':variant,'embedded':True,'level':{'tier':10,'key':'mentor_heart','level_name':'可为人师'},
      'score':208,'progress_percent':80,'title':'合成课程','subtitle':variant}))
result['mortal']=env.get_template('partials/cultivation_card.html').render(cultivation_card={'level':{'tier':0},'title':'尚未入道'})
print(json.dumps(result,ensure_ascii=False))
`, JSON.stringify(wanderer)], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));
}

async function mount(page: Page, { motion = 'standard', dark = false } = {}) {
    const fragments = templates();
    const state = { requests: [] as string[], errors: [] as string[] };
    page.on('pageerror', error => state.errors.push(error.message));
    await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://emblem.test') return route.abort();
        if (url.pathname.startsWith(`/static/assets/${manifest.revision}/`) || url.pathname.startsWith('/static/img/cultivation/emblems/')) {
            const file = path.resolve(`.${url.pathname}`);
            if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw Error(`Unexpected resource: ${file}`);
            return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/api/profile/avatar' || url.pathname === '/api/wanderers/21/avatar') {
            return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="32" fill="#16877f"/><text x="32" y="40" text-anchor="middle" fill="white" font-size="24">林</text></svg>' });
        }
        if (url.pathname.startsWith('/api/')) {
            state.requests.push(url.pathname);
            if (url.pathname === '/api/cultivation/emblems') return route.fulfill({ json: { items: Object.fromEntries(keys.map((key, i) => [String(i + 1), { key, tier: i + 1, name: `第${i + 1}境`, score: 108 + i }])) } });
            if (url.pathname === '/api/manage/system/wanderers') return route.fulfill({ json: { overview: {}, wanderers: [wanderer] } });
            if (decodeURIComponent(url.pathname) === '/api/social/users/student:1/card') return route.fulfill({ json: {
                identity: 'student:1', role: 'student', name: '林晚舟', avatar_url: '/api/profile/avatar?role=student&user_id=1', relation: 'self',
                tier_badge: { name: '可为人师', score: 208, emblem_url: image(10) },
                courses: [{ course_name: '合成课程', tier_name: '启蒙入门', emblem: image(1) }],
            } });
            return route.fulfill({ status: 404, json: { detail: 'Unmocked API' } });
        }
        if (url.pathname !== '/') return route.fulfill({ status: 404, body: 'No external resources' });
        return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-cultivation-emblems="off" data-appearance="${dark ? 'dark' : 'light'}" data-lq-scene-tone="${dark ? 'dark' : 'light'}" data-lq-motion="${motion}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
          ${['css/tailwind-app.css', 'css/lq/pages/wanderers.css', 'css/lq/pages/wanderer-home.css', 'css/lq/pages/social.css'].map(name => `<link rel="stylesheet" href="${asset(name)}">`).join('')}
          <style>main{padding:24px;max-width:1100px;margin:auto}h2{margin:16px 0}.fixture-row{display:flex;flex-wrap:wrap;gap:32px;padding:16px}.fixture-tile{display:grid;gap:12px;min-width:64px}.fixture-position{height:80px;width:80px;position:relative}.fixture-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:20px}.fixture-section{margin-block:24px}.fixture-stack{position:relative;width:100px;height:80px}.fixture-stack .lq-avatar{position:absolute;left:0;top:0}#stack-cover{position:absolute;inset:0;z-index:1;width:80px;height:70px}.fixture-legacy{display:inline-block;width:40px;height:40px;border-radius:50%}.fixture-legacy img{width:40px;height:40px}.fixture-consumers{display:flex;gap:40px;align-items:center;flex-wrap:wrap}</style>
          </head><body><main><h1>境界标志 · 合成消费者验收</h1>
          <section class="fixture-section" data-consumer="auto"><h2>十阶段与在线状态</h2><div class="fixture-row">${keys.map((_, i) => `<div class="fixture-tile"><span class="lq-avatar lq-avatar--40" data-cultivation-student-id="${i + 1}"><span class="lq-avatar__fallback">林</span><span class="social-avatar__dot"></span></span><small>第 ${i + 1} 境</small></div>`).join('')}</div></section>
          <section class="fixture-section"><h2>定位、键盘与层级</h2><div class="fixture-row">
          <span id="legacy-avatar" class="fixture-legacy"><img src="/api/profile/avatar?role=student&user_id=1" alt="林晚舟"></span>
          <div class="fixture-position"><span id="absolute-avatar" class="lq-avatar lq-avatar--40" style="position:absolute;inset:0 auto auto 0" data-cultivation-student-id="2">林</span></div>
          <button id="avatar-link" class="lq-btn lq-btn--ghost" type="button"><img width="40" height="40" src="/api/profile/avatar?role=student&user_id=3" alt="进入主页"></button>
          <span id="hidden-avatar" class="lq-avatar lq-avatar--40" data-cultivation-student-id="99">隐</span>
          <div class="fixture-stack"><span id="stack-avatar" class="lq-avatar lq-avatar--40" data-cultivation-student-id="10">林</span><button id="stack-cover" class="lq-btn lq-btn--soft">相邻操作</button></div>
          </div></section>
          <section class="fixture-section"><h2>散修管理与主页（原模板）</h2><div class="fixture-consumers"><div data-consumer="admin-ssr">${fragments.admin}</div><div data-consumer="home-ssr">${fragments.home}</div><div data-consumer="admin-js"><button id="wd-refresh-btn" class="lq-btn lq-btn--soft">刷新散修</button><div id="wd-card-grid"></div></div></div></section>
          <section class="fixture-section" data-consumer="shared-card"><h2>课程 / 个人中心共享修炼卡</h2><div class="fixture-cards">${fragments.cards.join('')}${fragments.mortal}</div></section>
          <button id="card-open" class="lq-btn lq-btn--soft" data-social-entry data-social-css="${asset('css/lq/pages/social.css')}">个人信息</button>
          </main><div id="wd-detail" hidden><form data-wd-tier-form></form><form data-wd-persona-form></form><form data-wd-avatar-form></form></div>
          <script>window.WANDERER_ADMIN_BOOT={overview:{},wanderers:[]};</script>
          <script type="module">
          import {initCultivationEmblems} from '${asset('js/cultivation_emblems.js')}';
          import {bindUserCardTriggers} from '${asset('js/social/user_card_trigger.js')}';
          import {openUserCard} from '${asset('js/social/user_card.js')}';
          import '${asset('js/manage_wanderers.js')}';
          initCultivationEmblems(document);bindUserCardTriggers(document,()=>null);
          document.querySelector('#card-open').addEventListener('click',event=>openUserCard('student:1',{trigger:event.currentTarget}));
          document.body.dataset.ready='true';</script></body></html>` });
    });
    await page.goto('https://emblem.test/');
    await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
    await expect(page.locator('[data-consumer=auto] .lq-cultivation-emblem')).toHaveCount(10);
    return state;
}

test('emblems stay within avatar stacking contexts without losing position, presence, or focus', async ({ page }) => {
    const state = await mount(page);
    await expect(page.locator('#legacy-avatar')).toHaveCSS('position', 'relative');
    await expect(page.locator('#absolute-avatar')).toHaveCSS('position', 'absolute');
    for (const id of ['legacy-avatar', 'absolute-avatar', 'stack-avatar']) await expect(page.locator(`#${id}`)).toHaveCSS('isolation', 'isolate');
    await expect(page.locator('#hidden-avatar .lq-cultivation-emblem')).toHaveCount(0);
    await expect(page.locator('#avatar-link .lq-cultivation-emblem')).toHaveAttribute('tabindex', '-1');
    const badge = page.locator('#stack-avatar .lq-cultivation-emblem');
    const box = (await badge.boundingBox())!;
    const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    expect(await page.evaluate(p => document.elementFromPoint(p.x, p.y)?.id, point)).toBe('stack-cover');
    await page.locator('#stack-cover').evaluate(el => el.remove());
    expect(await page.evaluate(p => document.elementFromPoint(p.x, p.y)?.closest('.lq-cultivation-emblem') !== null, point)).toBe(true);
    const first = page.locator('[data-consumer=auto] .lq-cultivation-emblem').first();
    await first.focus();
    await expect(first).toBeFocused();
    await expect(first).toHaveCSS('outline-style', 'solid');
    await expect(page.getByRole('tooltip')).toContainText('第1境');
    await page.keyboard.press('Escape');
    expect(state.errors).toEqual([]);
});

for (const [motion, scale, duration] of [['standard', 1.012, '0.18s'], ['quiet', .995, '0.12s'], ['expressive', 1.025, '0.22s'], ['off', 1, '0s']] as const) {
    test(`emblem hover inherits ${motion} motion and never animates the filter`, async ({ page }) => {
        await mount(page, { motion });
        const badge = page.locator('[data-consumer=auto] .lq-cultivation-emblem').first();
        await badge.hover();
        await expect(badge).toHaveCSS('transition-property', 'transform');
        await expect(badge).toHaveCSS('transition-duration', duration);
        await expect.poll(() => badge.evaluate(el => new DOMMatrix(getComputedStyle(el).transform).a)).toBeCloseTo(scale, 4);
        // Local modes must override an inherited off mode; no ancestor selector
        // may permanently suppress a nearer standard preference.
        await page.locator('html').evaluate(el => el.dataset.lqMotion = 'off');
        await badge.evaluate(el => (el.parentElement as HTMLElement).dataset.lqMotion = 'standard');
        await expect.poll(() => badge.evaluate(el => new DOMMatrix(getComputedStyle(el).transform).a)).toBeCloseTo(1.012, 4);
        await page.emulateMedia({ reducedMotion: 'reduce' });
        await page.locator('body').evaluate(el => el.classList.add('classroom-page', 'dashboard-page'));
        await expect(badge).toHaveCSS('transition-duration', '0s');
        await expect.poll(() => badge.evaluate(el => new DOMMatrix(getComputedStyle(el).transform).a)).toBe(1);
    });
}

test('high-stage and shared-card shadows disappear under forced colors while art stays visible', async ({ page }) => {
    await mount(page);
    const low = page.locator('[data-consumer=auto] .lq-cultivation-emblem').first();
    const high = page.locator('[data-consumer=auto] .lq-cultivation-emblem').last();
    const card = page.locator('.cultivation-card__emblem').first();
    await expect(low).toHaveCSS('filter', 'drop-shadow(rgba(0, 0, 0, 0.45) 0px 1px 2px)');
    expect(await high.evaluate(el => getComputedStyle(el).filter)).toContain('rgba(0, 0, 0, 0.45)');
    await expect(card).toHaveCSS('filter', 'drop-shadow(rgba(0, 0, 0, 0.35) 0px 2px 6px)');
    await page.emulateMedia({ forcedColors: 'active' });
    for (const element of [low, high, card]) {
        await expect(element).toHaveCSS('filter', 'none');
        await expect(element).toBeVisible();
    }
});

for (const mobile of [false, true]) test(`actual consumer images and user-card trigger remain intact (${mobile ? '360 dark' : 'desktop light'})`, async ({ page: desktopPage, browser }, testInfo) => {
    const touchContext = mobile ? await browser.newContext({ viewport: { width: 360, height: 900 }, hasTouch: true, isMobile: true }) : null;
    const page = touchContext ? await touchContext.newPage() : desktopPage;
    const state = await mount(page, { dark: mobile });
    if (mobile) await page.locator('#wd-refresh-btn').tap();
    else await page.locator('#wd-refresh-btn').click();
    await expect(page.locator('[data-consumer=admin-js] .wd-card__emblem')).toHaveCount(1);
    for (const consumer of ['admin-ssr', 'home-ssr', 'admin-js']) {
        await expect(page.locator(`[data-consumer=${consumer}] .lq-cultivation-emblem-img`)).toHaveCount(1);
        await expect(page.locator(`[data-consumer=${consumer}] .lq-cultivation-emblem`)).toHaveCount(0);
    }
    await expect(page.locator('.cultivation-card__emblem')).toHaveCount(3);
    await expect(page.locator('.cultivation-card__mark:not(.has-emblem)')).toContainText('未入道');
    await page.locator('main img').evaluateAll(images => Promise.all(images.map(img => (img as HTMLImageElement).decode())));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    // Badge clicks keep the tooltip identity; clicking the actual face still
    // opens the real public card controller, which renders its own inline art.
    const badge = page.locator('#legacy-avatar .lq-cultivation-emblem');
    if (mobile) {
        await badge.tap();
        expect(await page.evaluate(() => matchMedia('(hover: hover)').matches)).toBe(false);
        await expect(badge).toHaveCSS('transform', 'none');
    } else await badge.click();
    expect(state.requests.some(url => url.includes('/card'))).toBe(false);
    // Keep the face tap clear of the adjacent emblem's touch-adjusted target.
    if (mobile) await page.locator('#legacy-avatar > img').tap({ position: { x: 8, y: 8 } });
    else await page.locator('#legacy-avatar > img').click();
    await expect(page.locator('.social-card__tier .lq-cultivation-emblem-img')).toBeVisible();
    await expect(page.locator('.social-card__course-tier .lq-cultivation-emblem-img')).toBeVisible();
    await expect(page.locator('.social-card__avatar .lq-cultivation-emblem')).toHaveCount(0);
    expect(await page.locator('#social-user-card .lq-dialog__surface').evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`user-card-${mobile ? 'mobile-dark' : 'desktop-light'}.png`) });
    await page.keyboard.press('Escape');
    await expect(page.locator('#social-user-card')).toBeHidden();
    expect(state.errors).toEqual([]);
    // Chromium 156's full-page capture resets the emulated primary pointer to
    // fine. Capture only after touch assertions, so screenshots cannot change
    // the input capability being tested (reproduced without application code).
    await page.screenshot({ path: testInfo.outputPath(`consumers-${mobile ? 'mobile-dark' : 'desktop-light'}.png`), fullPage: true });
    await testInfo.attach('consumer-evidence', { contentType: 'application/json', body: JSON.stringify({ assetRevision: manifest.revision, requests: state.requests, consumers: ['cultivation_emblems.js', 'user_card.js', 'user_card_trigger.js', 'manage_wanderers.js', 'manage/system/wanderers.html', 'wanderers/home.html', 'partials/cultivation_card.html (learning_path + profile hero/hero_lq/overview)'] }, null, 2) });
    await touchContext?.close();
});
