import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const asset = (name: string) => `/static/${manifest.entries[name]}`;
const keys = ['qi_awakening', 'qi_refining', 'foundation', 'application_seed', 'golden_core',
    'practical_mastery', 'systems_thinking', 'nascent_soul', 'independent_path', 'mentor_heart'];
const titles = ['启蒙入门', '基础成形', '核心筑基', '迁移初通', '融会小成', '基本掌握', '系统贯通', '完全掌握', '自主精进', '可为人师'];

function markup(tier: number) {
    return execFileSync('python', ['-c', `import json,sys;from jinja2 import Environment,FileSystemLoader;env=Environment(loader=FileSystemLoader('templates'),autoescape=True);print(env.get_template('partials/learning_certificate_reveal.html').render(learning_certificate=json.loads(sys.argv[1]),user_info={'name':'测试同学'}));print(env.get_template('partials/cultivation_panel.html').render(cultivation_panel={}))`, JSON.stringify({ id: tier, tier,
        theme: keys[tier - 1], level_name: titles[tier - 1], title: `${titles[tier - 1]}灵契`,
        course_name: '计算机网络原理', student_name: '测试同学', certificate_code: `LS-SYNTHETIC-${tier}`, issued_at: '2026-10-27T12:30:00+08:00' })], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
}

async function mount(page: Page, { tier = 1, motion = 'standard', appearance = 'light' } = {}) {
    const template = markup(tier);
    await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://award.test') return route.abort();
        if (url.pathname.startsWith(`/static/assets/${manifest.revision}/`) || url.pathname.startsWith('/static/img/cultivation/')) {
            const file = path.resolve(`.${url.pathname}`);
            if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw Error(`Unexpected resource ${file}`);
            return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === `/api/learning/certificates/${tier}/revealed`) return route.fulfill({ json: { status: 'ok' } });
        if (url.pathname !== '/') return route.fulfill({ status: 404, body: 'No external services in this fixture' });
        return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-cultivation-emblems="off" data-appearance="${appearance}" data-lq-motion="${motion}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"></head><body><main style="padding:30px"><button id="trigger" class="lq-btn lq-btn--glass">学习首页</button><h1>我的学习</h1><p>已通过本阶段试炼</p></main>${template}<script type="module">import {initLearningCertificateReveal} from '${asset('js/learning_certificate_reveal.js')}';document.querySelector('#trigger').focus();window.award=initLearningCertificateReveal({});window.initAward=initLearningCertificateReveal;document.body.dataset.ready='true';</script></body></html>` });
    });
    await page.goto('https://award.test/');
    await expect(page.locator('#learning-certificate-backdrop')).toBeVisible();
    await page.locator('[data-award-image]').evaluate((img: HTMLImageElement) => img.decode());
}

test('LQ award has one owner, outside and Escape dismiss, and focus returns', async ({ page }) => {
    let marks = 0; page.on('request', request => { if (request.url().endsWith('/revealed')) marks++; });
    await mount(page);
    expect(await page.evaluate(() => (window as any).initAward({}) === (window as any).award)).toBe(true);
    await page.mouse.click(5, 5);
    await expect(page.locator('#learning-certificate-backdrop')).toBeHidden();
    await expect(page.locator('#trigger')).toBeFocused();
    await page.evaluate(() => (window as any).award.open());
    await page.keyboard.press('Escape');
    await expect(page.locator('#learning-certificate-backdrop')).toBeHidden();
    expect(marks).toBe(1);
});

test('LQ nested award save cancels independently and keeps the original medal', async ({ page }) => {
    await page.addInitScript(() => { (window as any).showSaveFilePicker = async () => { (window as any).pickerActivated = navigator.userActivation.isActive; throw new DOMException('cancel', 'AbortError'); }; });
    await mount(page, { tier: 8 });
    await page.locator('[data-learning-certificate-card]').click();
    await expect(page.getByRole('dialog', { name: '收藏这枚道印' })).toBeVisible();
    await page.getByRole('button', { name: '保存道印', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '已取消保存' })).toBeVisible();
    expect(await page.evaluate(() => (window as any).pickerActivated)).toBe(true);
    await page.getByRole('button', { name: '取消', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '收藏这枚道印' })).toHaveCount(0);
    await expect(page.locator('[data-learning-certificate-card]')).toBeFocused();
    await expect(page.locator('#learning-certificate-backdrop')).toBeVisible();
});

test('LQ award exports the same art as a transparent PNG with browser fallback', async ({ page }, info) => {
    await page.addInitScript(() => { Object.defineProperty(window, 'showSaveFilePicker', { value: undefined, configurable: true }); });
    await mount(page, { tier: 10 });
    await page.locator('[data-learning-certificate-card]').click();
    const pending = page.waitForEvent('download');
    await page.getByRole('button', { name: '保存道印', exact: true }).click();
    const download = await pending;
    expect(download.suggestedFilename()).toBe('可为人师-LS-SYNTHETIC-10.png');
    const file = info.outputPath('award.png'); await download.saveAs(file);
    const data = fs.readFileSync(file); expect(data.subarray(1, 4).toString()).toBe('PNG');
    expect(data.readUInt32BE(16)).toBe(1600); expect(data.readUInt32BE(20)).toBe(1850);
    await info.attach('exported-award', { path: file, contentType: 'image/png' });
    await expect(page.getByRole('status').filter({ hasText: '已交给浏览器保存' })).toBeVisible();
});

test('LQ native save writes once and preserves a transparent corner', async ({ page }) => {
    await page.addInitScript(() => { (window as any).saved = { writes: 0, closes: 0 }; (window as any).showSaveFilePicker = async () => ({ createWritable: async () => ({
        write: async (blob: Blob) => { (window as any).saved.writes++; const bitmap = await createImageBitmap(blob); const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height; const ctx = canvas.getContext('2d')!; ctx.drawImage(bitmap, 0, 0); (window as any).saved.alpha = ctx.getImageData(0, 0, 1, 1).data[3]; bitmap.close(); },
        close: async () => { (window as any).saved.closes++; }, abort: async () => {},
    }) }); });
    await mount(page, { tier: 5 }); await page.locator('[data-learning-certificate-card]').click();
    await page.getByRole('button', { name: '保存道印', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '道印已保存。' })).toBeVisible();
    expect(await page.evaluate(() => (window as any).saved)).toEqual({ writes: 1, closes: 1, alpha: 0 });
});

test('LQ passed stage reopens its award without changing reveal state and restores panel focus', async ({ page }) => {
    const marks: string[] = []; page.on('request', request => { if (request.url().endsWith('/revealed')) marks.push(request.url()); });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await mount(page, { tier: 10 }); await page.keyboard.press('Escape');
    await expect(page.locator('#learning-certificate-backdrop')).toBeHidden();
    await page.evaluate(async moduleUrl => {
        const { openCultivationPanel } = await import(moduleUrl);
        const stage = { key: 'qi_awakening', name: '启蒙入门', short_name: '启蒙', tier: 1, status: 'passed',
            certificate: { id: 91, level_key: 'qi_awakening', level_name: '启蒙入门', tier: 1, title: '启蒙入门灵契', certificate_code: 'LS-HISTORY-91', issued_at: '2026-10-21T12:00:00+08:00' } };
        (window as any).panel = await openCultivationPanel({ classOfferingId: 9, courseName: '网络原理', userName: '测试<同学>',
            trigger: document.querySelector('#trigger'), progress: { score: 20, current_level: stage, stages: [stage] } });
    }, asset('js/cultivation_panel.js'));
    const view = page.getByRole('button', { name: '查看道印', exact: true });
    await expect(view).toBeVisible(); await view.click();
    const history = page.locator('.learning-certificate-backdrop:visible');
    await expect(history).toHaveCount(1);
    await expect(history.locator('h3')).toHaveText('启蒙入门灵契');
    await expect(history.locator('p')).toHaveText('网络原理 · 测试<同学>');
    await expect(history.locator('[data-award-image]')).toHaveAttribute('src', /qi_awakening\.svg/);
    await page.keyboard.press('Escape');
    await expect(page.locator('.learning-certificate-backdrop:visible')).toHaveCount(0);
    await expect(view).toBeFocused();
    expect(marks).toHaveLength(1);
    expect(await page.evaluate(() => localStorage.getItem('learning-cert-seen:91'))).toBeNull();
});

for (const mode of ['off', 'quiet', 'reduce']) test(`LQ award respects ${mode} and fits narrow screens`, async ({ page }, info) => {
    await page.setViewportSize({ width: 360, height: 720 });
    if (mode === 'reduce') await page.emulateMedia({ reducedMotion: 'reduce' });
    await mount(page, { tier: 10, motion: mode === 'reduce' ? 'expressive' : mode, appearance: 'dark' });
    expect(await page.locator('[data-award-scene]').evaluate(node => node.getAnimations({ subtree: true }).length)).toBe(0);
    const box = await page.locator('.learning-certificate-shell').boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(360);
    expect(box!.y + box!.height).toBeLessThanOrEqual(720);
    const screenshot = info.outputPath(`award-mobile-${mode}.png`); await page.screenshot({ path: screenshot });
    await info.attach('mobile-award', { path: screenshot, contentType: 'image/png' });
});

test('LQ all ten stages retain their artwork and stop celebrating at rest', async ({ page }, info) => {
    for (let tier = 1; tier <= 10; tier++) {
        await mount(page, { tier, appearance: tier % 2 ? 'light' : 'dark' });
        await expect(page.locator('[data-award-image]')).toHaveAttribute('src', new RegExp(`${keys[tier - 1]}\\.svg`));
        await expect(page.locator('[data-award-scene]')).toHaveAttribute('data-tier', String(tier));
        const animations = await page.locator('[data-award-scene]').evaluate(node => node.getAnimations({ subtree: true }).map(a => a.effect?.getTiming().iterations));
        expect(animations.length).toBeGreaterThan(0); expect(animations.every(value => value === 1)).toBe(true);
        if (tier === 1 || tier === 10) { const screenshot = info.outputPath(`award-tier-${tier}.png`); await page.screenshot({ path: screenshot }); await info.attach(`tier-${tier}`, { path: screenshot, contentType: 'image/png' }); }
        await page.unrouteAll({ behavior: 'wait' });
    }
    await expect.poll(() => page.locator('[data-award-scene]').evaluate(node => node.getAnimations({ subtree: true }).length), { timeout: 8000 }).toBe(0);
    await page.evaluate(() => (window as any).award.destroy());
    await expect(page.locator('#learning-certificate-backdrop')).toBeHidden();
});
