import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const fixture = JSON.parse(execFileSync(process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python', ['tests/e2e/scripts/render_lq_insights.py'], { encoding: 'utf8' }));
async function mount(page: Page) {
    const requests: string[] = [];
    await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://lq-insights.test') return route.abort();
        if (url.pathname.startsWith('/static/')) {
            const file = path.resolve(`.${url.pathname}`);
            if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/avatar.png') return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=', 'base64') });
        if (url.pathname === '/missing.png') return route.fulfill({ status: 404, body: 'missing image' });
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light" data-ui-palette="indigo"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ insights</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{padding:16px;max-width:100%;box-sizing:border-box}#fixture{display:grid;gap:16px}h1{margin-bottom:16px}</style></head><body><main><h1>成员与统计摘要</h1><div id="fixture"></div></main><script type="module">import * as insights from '/static/js/lq/insights.js';import * as primitives from '/static/js/lq/components.js';window.insights=insights;window.primitives=primitives;document.body.dataset.ready='true';</script></body></html>` });
        requests.push(url.pathname); return route.fulfill({ status: 404, body: 'No application network' });
    });
    await page.goto('https://lq-insights.test/'); await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
    return requests;
}

test('LQ insights actual old signatures and typed Jinja, HTML and Element entries agree', async ({ page }) => {
    expect(fixture.isolated).toBe(true); expect(fixture.cases.filter((c: any) => c.error)).toEqual([]);
    expect(fixture.invalid.filter((c: any) => !c.error)).toEqual([]);
    for (const signature of Object.values(fixture.signatures) as any[]) expect(signature.new).toEqual(signature.old);
    await mount(page);
    const result = await page.evaluate(serialized => {
        const f = JSON.parse(serialized), api = (window as any).insights;
        function semantic(n: Node): any {
            if (n.nodeType === 3) return n.textContent?.trim() ? { text: n.textContent } : null;
            const el = n as Element; return { tag: el.tagName.toLowerCase(), attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(semantic).filter(Boolean) };
        }
        const parse = (html: string) => { const template = document.createElement('template'); template.innerHTML = html; return [...template.content.childNodes].map(semantic).filter(Boolean); };
        return { cases: f.cases.map((c: any) => ({ props: api.insightProps(c.kind, c.props), jinja: parse(c.html), html: parse(api.insightMarkup(c.kind, c.props)), element: [semantic(api.createInsight(c.kind, c.props))] })),
            invalid: f.invalid.map((c: any) => ['insightProps', 'insightMarkup', 'createInsight'].map(method => { try { api[method](c.kind, c.props); return false; } catch { return true; } })),
            legacy: f.legacy.map((c: any) => ({ kind: c.kind, old: parse(c.old), updated: parse(c.new) })) };
    }, JSON.stringify(fixture));
    result.cases.forEach((c: any, i: number) => { expect(c.props).toEqual(fixture.cases[i].normalized); expect(c.html).toEqual(c.jinja); expect(c.element).toEqual(c.jinja); });
    result.invalid.forEach((checks: boolean[]) => expect(checks).toEqual([true, true, true]));
    expect(fixture.legacy[0].new).toContain('3 / 7 项'); expect(fixture.legacy[0].old).toContain('3 / 7 项');
    expect(fixture.legacy[1].new).toContain('5项'); expect(fixture.legacy[1].old).toContain('5<small>项');
});

for (const entry of ['jinja', 'html', 'element']) test(`LQ ${entry} AvatarStack limits display, preserves hash and full accessible names and recovers failed images`, async ({ page }) => {
    await mount(page);
    await page.evaluate(({ fixture, entry }) => {
        const w = window as any, api = w.insights, host = document.getElementById('fixture')!;
        const item = fixture.cases.find((item: any) => item.kind === 'avatar_stack' && item.props.items.length === 6);
        if (entry === 'jinja') host.innerHTML = item.html;
        else if (entry === 'html') host.innerHTML = api.html.avatar_stack(item.props);
        else host.append(api.avatar_stack(item.props));
        const images = host.querySelectorAll('img'); for (const image of images) { image.loading = 'eager'; image.src = '/missing.png'; }
    }, { fixture, entry });
    await expect(page.locator('.lq-avatar')).toHaveCount(4); await expect(page.locator('.lq-avatar-stack__more')).toHaveText('+2');
    await expect(page.getByRole('img')).toHaveCount(1);
    await expect(page.getByRole('img')).toHaveAccessibleName('成员：😀张三（组员0）；李四（组员1）；王五（组员2）；赵六（组员3）；孙七（组员4）；周八（组员5）');
    await expect(page.locator('.lq-avatar').first()).toHaveAttribute('data-avatar-bucket', '5');
    await expect.poll(() => page.locator('img').evaluateAll(nodes => nodes.every(n => (n as HTMLImageElement).complete))).toBe(true);
    // SSR errors may predate module enhancement. Reuse the existing P1 sweep.
    await page.evaluate(() => { (window as any).enhancer = (window as any).primitives.enhanceComponents(document); });
    await expect(page.locator('img')).toBeHidden(); await expect(page.locator('.lq-avatar__fallback').first()).toBeVisible();
    const ax = await page.locator('.lq-avatar-stack').ariaSnapshot(); expect(ax.match(/😀张三/g)).toHaveLength(1);
    expect(ax).not.toContain('img "李四');
    await page.evaluate(() => (window as any).enhancer.dispose());
});

test('LQ insights distinguish unknown and meaningful zero; charts do not duplicate accessible numbers', async ({ page }) => {
    await mount(page);
    await page.evaluate(() => {
        const api = (window as any).insights, host = document.getElementById('fixture')!;
        host.append(api.insight_meter({ id: 'score', title: '成绩', value: 0, percent: 0, zero: 'value', unit: '分' }),
            api.insight_meter({ id: 'unknown', title: '未返回成绩', value: null, percent: null, zero: 'value', unit: '分' }),
            api.insight_ring({ id: 'zero', title: '空统计', value: 0, total: 10 }),
            api.insight_ring({ id: 'ratio', title: '文件占比', value: 3, total: 7 }),
            api.insight_bars({ id: 'mixed', title: '分布', items: [{ label: '实际零', value: 0, zero: 'value' }, { label: '暂无来源', value: null }, { label: '已有量', value: 3 }] }));
    });
    await expect(page.locator('#score')).toHaveAttribute('data-state', 'value'); await expect(page.locator('#score')).toContainText('0分');
    await expect(page.locator('#score .lq-meter__track')).toHaveCount(0);
    await expect(page.locator('#unknown')).toHaveAttribute('data-state', 'missing'); await expect(page.locator('#unknown')).not.toContainText('0分');
    await expect(page.locator('#zero')).toHaveClass(/is-empty/); await expect(page.locator('#zero svg')).toHaveCount(0);
    await expect(page.locator('#mixed .lq-bars__track')).toHaveCount(1); await expect(page.locator('#mixed')).toContainText('未提供');
    await expect(page.locator('#mixed').getByRole('listitem')).toHaveCount(3);
    const ax = await page.locator('#ratio').ariaSnapshot(); expect(ax.match(/3 \/ 7/g)).toHaveLength(1); expect(ax).not.toContain('43%');
    await expect(page.locator('[aria-live]')).toHaveCount(0);
    const geometry = await page.locator('#ratio circle.lq-ring__fill').evaluate(node => ({ ns: node.namespaceURI, length: (node as SVGCircleElement).getTotalLength(), dash: getComputedStyle(node).strokeDasharray }));
    expect(geometry.ns).toBe('http://www.w3.org/2000/svg'); expect(geometry.length).toBeGreaterThan(99); expect(geometry.dash).toContain('42.86');
});

test('LQ insight Node and Jinja caller slots preserve inputs and reject invalid props or slots before moving caller content', async ({ page }) => {
    const requests = await mount(page);
    await page.evaluate(fixture => { document.getElementById('fixture')!.innerHTML = fixture.composition; }, fixture);
    await page.locator('#draft').fill('必须保留的业务说明');
    const result = await page.evaluate(() => {
        const api = (window as any).insights, input = document.getElementById('draft')!, label = input.previousElementSibling!, parent = input.parentNode;
        let calls = 0; input.addEventListener('input', () => calls++);
        const p = { title: '评分', value: 0, percent: 0, zero: 'value' };
        let invalid = 0;
        for (const [props, slots] of [[{ ...p, percent: 200 }, { caption: [input] }], [p, { caption: [input], bad: [] }], [p, { caption: [input, input] }], [p, { caption: [parent, input] }], [p, { caption: ['<b>raw</b>'] }]]) {
            try { api.insight_meter(props, slots); } catch { invalid++; }
        }
        const notMoved = input.parentNode === parent;
        const root = api.insight_meter(p, { caption: [label, input] }); document.getElementById('fixture')!.replaceChildren(root);
        (window as any).calls = () => calls;
        return { invalid, notMoved, sameInput: root.querySelector('#draft') === input };
    });
    expect(result).toEqual({ invalid: 5, notMoved: true, sameInput: true });
    await expect(page.locator('#draft')).toHaveValue('必须保留的业务说明'); await page.locator('#draft').fill('仍可编辑');
    expect(await page.evaluate(() => (window as any).calls())).toBe(1); expect(requests).toEqual([]);
});

test('LQ insights are passive across twenty renders and reuse enhancement ownership across module aliases', async ({ page }) => {
    const requests = await mount(page);
    const result = await page.evaluate(async () => {
        const w = window as any, host = document.getElementById('fixture')!, listeners = new Set<EventListenerOrEventListenerObject>();
        const add = document.addEventListener.bind(document), remove = document.removeEventListener.bind(document);
        document.addEventListener = ((type: string, listener: EventListenerOrEventListenerObject, options?: any) => { listeners.add(listener); add(type, listener, options); }) as any;
        document.removeEventListener = ((type: string, listener: EventListenerOrEventListenerObject, options?: any) => { listeners.delete(listener); remove(type, listener, options); }) as any;
        const originalInterval = window.setInterval, originalTimeout = window.setTimeout; let timers = 0;
        window.setInterval = ((...args: any[]) => { timers++; return (originalInterval as any)(...args); }) as any;
        window.setTimeout = ((...args: any[]) => { timers++; return (originalTimeout as any)(...args); }) as any;
        const aliasUrl = '/static/js/lq/components.js?insights-alias'; const alias = await import(aliasUrl);
        for (let i = 0; i < 20; i++) {
            host.replaceChildren(w.insights.avatar_stack({ items: [{ name: '教师' }] }), w.insights.insight_ring({ title: '比例', value: 1, total: 2 }));
            const first = w.primitives.enhanceComponents(document), second = alias.enhanceComponents(document);
            first.dispose(); first.dispose(); second.dispose();
        }
        window.setInterval = originalInterval; window.setTimeout = originalTimeout; document.addEventListener = add; document.removeEventListener = remove;
        return { timers, listeners: listeners.size };
    });
    expect(result).toEqual({ timers: 0, listeners: 0 }); expect(requests).toEqual([]);
});

for (const appearance of ['light', 'dark']) for (const palette of ['indigo', 'teal', 'rose', 'sky', 'mint', 'violet']) {
    test(`LQ insights ${appearance}/${palette} fits 390 with meaningful zero and axe`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 }); await mount(page);
        await page.evaluate(({ appearance, palette }) => {
            Object.assign(document.documentElement.dataset, { appearance, uiPalette: palette });
            const api = (window as any).insights, host = document.getElementById('fixture')!;
            host.append(api.avatar_stack({ items: ['张三', '李四', '王五', '赵六', '孙七', '周八'].map(name => ({ name, detail: '小组成员' })), size: 56 }),
                api.insight_ring({ title: '课堂资料完备度', value: 3, total: 7, value_label: '3 / 7 份', caption: '比例仅呈现调用方已确认的统计。'.repeat(4), tone: 'teal' }),
                api.insight_bars({ title: '分布摘要', items: [{ label: '很长的课程与任务分类名称'.repeat(4), value: 12 }, { label: '保留的有效零分', value: 0, zero: 'value' }, { label: '数据暂未返回', value: null }] }),
                api.insight_meter({ title: '已评分结果', value: 0, percent: 0, zero: 'value', unit: '分', caption: '零分来自业务，仍须完整显示。' }),
                api.insight_ring({ title: '比例未提供', value: null, total: null }), api.insight_meter({ title: '空集合', value: 0, percent: 0 }));
        }, { appearance, palette });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect((await new AxeBuilder({ page }).include('main').analyze()).violations).toEqual([]);
        if (palette === 'rose') await page.screenshot({ path: `.codex-temp/lq-insights-${appearance}-390.png`, fullPage: true });
    });
}

test('LQ insights forced colors retain ring and bar information and reduced motion is static', async ({ page }) => {
    await mount(page);
    await page.evaluate(() => {
        const api = (window as any).insights;
        document.getElementById('fixture')!.append(api.insight_ring({ title: '比例', value: 3, total: 7 }), api.insight_meter({ title: '数量', value: 4, percent: 30 }));
    });
    await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
    const ring = await page.locator('.lq-ring__fill').evaluate(node => { const s = getComputedStyle(node); return { stroke: s.stroke, dash: s.strokeDasharray, animation: s.animationName }; });
    expect(ring.stroke).not.toBe('none'); expect(ring.dash).toContain('42.86'); expect(ring.animation).toBe('none');
    const bar = await page.locator('.lq-meter__track > i').evaluate(node => ({ bg: getComputedStyle(node).backgroundColor, width: node.getBoundingClientRect().width, animation: getComputedStyle(node).animationName }));
    expect(bar.bg).not.toBe('rgba(0, 0, 0, 0)'); expect(bar.width).toBeGreaterThan(0); expect(bar.animation).toBe('none');
});
