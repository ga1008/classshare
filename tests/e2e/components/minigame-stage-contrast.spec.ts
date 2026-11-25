import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { PNG } from 'playwright-core/lib/utilsBundle';

const root = path.resolve(__dirname, '../../..');
const origin = 'https://classroom.test';
const content = 'https://isolated-game.test';
const python = process.env.LQ_TEST_PYTHON ||
    (fs.existsSync(path.join(root, 'venv/Scripts/python.exe')) ? path.join(root, 'venv/Scripts/python.exe') : 'python');

async function host(page: Page, stageTone: string, solo = false) {
    const config = JSON.parse(fs.readFileSync(path.join(root, 'docs/minigames/example/config.json'), 'utf8'));
    config.display.stageTone = stageTone;
    config.modes.solo = solo;
    const game = { id: 'g1', title: '航标接力', version_id: 'v1', config };
    const html = execFileSync(python, [path.join(__dirname, 'fixtures/minigame_templates.py')], {
        cwd: root, input: JSON.stringify({ page: 'host', role: 'student', game }), encoding: 'utf8',
        env: { ...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' },
    });
    const me = { playerId: 'p1', displayName: '合成玩家', seat: 1 };
    let phase = 'waiting', mode = 'private', denied = false, startsAt = 0;
    const writes: string[] = [], errors: string[] = [];
    const snapshot = () => ({
        gameId: 'g1', versionId: 'v1', mode, phase, serverNow: Date.now(), startsAt,
        runId: mode === 'solo' ? 'run1' : null, roomId: mode === 'private' || (mode === 'random' && phase !== 'waiting') ? 'rm1' : null,
        ticketId: mode === 'random' ? 'q1' : null, code: mode === 'private' ? 'M7K3' : null,
        isOwner: true, members: phase === 'waiting' ? [me] : [me, { seat: 2, displayName: '另一位队友' }],
        targetSize: 2, controlEpoch: 1, revision: 1, seq: 1, me,
    });
    page.on('pageerror', err => errors.push(err.message));
    await page.route('**/*', async route => {
        const url = new URL(route.request().url()), p = url.pathname;
        if (url.origin === content) {
            if (p === '/sdk.js') return route.fulfill({ contentType: 'application/javascript',
                path: path.join(root, 'static/minigames/lan-game-sdk.v1.js') });
            return route.fulfill({ contentType: 'text/html; charset=utf-8',
                body: '<script src="/sdk.js"></script><button>点灯</button><a href="/next.html">换页</a>' });
        }
        if (url.origin !== origin) return route.abort();
        if (p.startsWith('/static/')) {
            const asset = path.resolve(root, '.' + p);
            if (!asset.startsWith(path.join(root, 'static') + path.sep) || !fs.existsSync(asset)) return route.abort();
            return route.fulfill({ path: asset });
        }
        if (route.request().isNavigationRequest()) return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
        const api = p.replace('/api/games/v1', '');
        if (route.request().method() !== 'GET') writes.push(api);
        const json = (value: object) => route.fulfill({ json: value });
        if (api === '/games/g1/launches') return json({ leaseId: 'l1', expiresAt: Date.now() + 90000,
            definition: { config }, contentEntryUrl: content + '/index.html', versionId: 'v1', me });
        if (api === '/me/progress/g1') return json({ rank: 0 });
        if (api === '/me/activity') return json({ solo: null, multiplayer: null });
        if (api === '/games/g1/runs') {
            mode = 'solo';
            phase = 'running';
            return json(snapshot());
        }
        if (api === '/games/g1/queue' || api === '/games/g1/rooms') {
            mode = api.endsWith('/queue') ? 'random' : 'private';
            phase = 'waiting';
            return json(snapshot());
        }
        if (api === '/sync') {
            if (denied) return route.fulfill({ status: 403, json: { detail: { code: 'CLASSROOM_GAME_RESTRICTED', message: '课堂期间暂停访问' } } });
            return json({ snapshot: snapshot(), events: [] });
        }
        return json({});
    });
    await page.goto(origin + '/games/g1');
    return { writes, errors, changePhase(value: string) { phase = value; startsAt = Date.now() + 5000; },
        expireCountdown() { startsAt = Date.now() - 100; }, restrict() { denied = true; } };
}

// Compare the rendered text pixels against the same frame with only glyph fill removed.
// This includes the actual panel, its backdrop, parent opacity and nested button material.
async function measure(page: Page, state: string, selectors: string[], info: TestInfo) {
    const labels = page.locator(selectors.join(','));
    const rects = await labels.evaluateAll(elements => elements.map(element => {
        const range = document.createRange();
        range.selectNodeContents(element);
        const {x, y, width, height} = range.getBoundingClientRect();
        const css = getComputedStyle(element);
        const large = parseFloat(css.fontSize) >= 24 || (parseFloat(css.fontSize) >= 18.67 && Number(css.fontWeight) >= 700);
        return { name: element.textContent?.trim(), x, y, width, height, large };
    }));
    expect(rects.length).toBeGreaterThan(0);
    const normal = PNG.sync.read(await page.screenshot({ path: info.outputPath(state + '.png'), animations: 'disabled', scale: 'css' }));
    const back = PNG.sync.read(await page.screenshot({ animations: 'disabled', scale: 'css',
        style: selectors.join(',') + ' { -webkit-text-fill-color: transparent !important; text-shadow: none !important }' }));
    const linear = Array.from({ length: 256 }, (_, n) => n <= 10 ? n / 3294.6 : ((n / 255 + .055) / 1.055) ** 2.4);
    function luminance(buffer: Buffer, p: number) {
        return .2126 * linear[buffer[p]] + .7152 * linear[buffer[p + 1]] + .0722 * linear[buffer[p + 2]];
    }
    const results = rects.map(({ name, x, y, width, height, large }) => {
        expect(x).toBeGreaterThanOrEqual(-1);
        expect(x + width).toBeLessThanOrEqual(normal.width + 1);
        expect(y).toBeGreaterThanOrEqual(-1);
        expect(y + height).toBeLessThanOrEqual(normal.height + 1);
        let peak = 1;
        for (let row = Math.max(0, Math.floor(y)); row < Math.min(normal.height, Math.ceil(y + height)); row++) {
            for (let col = Math.max(0, Math.floor(x)); col < Math.min(normal.width, Math.ceil(x + width)); col++) {
                const p = (row * normal.width + col) * 4, a = luminance(normal.data, p), b = luminance(back.data, p);
                peak = Math.max(peak, (Math.max(a, b) + .05) / (Math.min(a, b) + .05));
            }
        }
        return { name, contrast: +peak.toFixed(2), minimum: large ? 3 : 4.5 };
    });
    const report = info.outputPath(state + '-contrast.json');
    fs.writeFileSync(report, JSON.stringify(results, null, 2));
    await info.attach(state + '-contrast', { path: report, contentType: 'application/json' });
    expect(results.filter(r => r.contrast < r.minimum), JSON.stringify(results)).toEqual([]);
}

for (const setup of [
    { account: 'light', matte: 'dark', width: 1366, height: 768, glass: 'on' },
    { account: 'dark', matte: 'light', width: 360, height: 740, glass: 'on' },
    { account: 'light', matte: 'dark', width: 320, height: 720, glass: 'off' },
]) {
    test(`联机舞台像素对比及可操作状态 ${setup.account} / ${setup.matte} / ${setup.width}`, async ({page}, info) => {
        await page.setViewportSize({ width: setup.width, height: setup.height });
        await page.emulateMedia({ colorScheme: setup.account as 'light' | 'dark', reducedMotion: 'reduce' });
        const fixture = await host(page, setup.matte);
        await page.evaluate(({account, glass}) => {
            document.documentElement.dataset.appearance = account;
            document.documentElement.dataset.lqGlass = glass;
        }, setup);
        const title = page.locator('[data-stage-title]'), dialog = page.getByRole('dialog');
        const card = page.getByRole('button', { name: '随机匹配', exact: true });
        await expect(card).toBeVisible();
        await measure(page, '模式选择', ['[data-stage-title]', '.game-mode-card strong', '.game-mode-card small', '[data-exit-game] .lq-btn__label'], info);
        await page.getByRole('button', {name:'加入队伍', exact:true}).click();
        await dialog.getByLabel('四位队伍码').fill('0000');
        await dialog.getByRole('button', {name:'加入', exact:true}).click();
        await expect(dialog.getByRole('status')).toContainText('有效邀请码');
        await measure(page, '邀请码校验', ['[role="dialog"] form [role="status"]'], info);
        await page.keyboard.press('Escape');
        await card.click();
        await dialog.getByRole('button', {name:'开始匹配'}).click();
        await expect(title).toHaveText('正在匹配队友');
        await expect(dialog).toBeHidden();
        const waiting = ['[data-stage-title]', '[data-stage-message]', '.game-seat span', '.game-seat b', '[data-action="leave"] .lq-btn__label'];
        await measure(page, '随机匹配', waiting, info);
        await page.getByRole('button', {name:'退出等待'}).click();
        await dialog.getByRole('button', {name:'取消'}).click();
        await expect(title).toHaveText('正在匹配队友');
        await page.getByRole('button', {name:'退出等待'}).click();
        await dialog.getByRole('button', {name:'确认', exact:true}).click();
        await expect(card).toBeVisible();
        expect(fixture.writes).toContain('/queue/q1/cancel');
        await page.getByRole('button', {name:'创建队伍', exact:true}).click();
        await dialog.getByRole('button', {name:'创建', exact:true}).click();
        await expect(title).toHaveText('等待队友加入');
        await expect(dialog).toBeHidden();
        await measure(page, '私人队伍', [...waiting, '[data-code-text]'], info);
        fixture.changePhase('countdown');
        await expect(title).toHaveText('满员开局');
        await expect(page.locator('[data-countdown]')).toHaveText(/[1-5]/);
        await measure(page, '开局倒计时', [...waiting, '[data-countdown]'], info);
        fixture.expireCountdown();
        await expect(title).toHaveText('正在进入游戏');
        await expect(page.locator('[data-stage-frame] iframe')).toHaveCount(0);
        fixture.changePhase('running');
        await expect(page.frameLocator('[data-stage-frame] iframe').getByText('点灯')).toBeVisible();
        await expect(page.locator('[data-stage-panel]')).toBeHidden();
        fixture.restrict();
        await expect(title).toHaveText('已停止');
        await expect(page.getByRole('button',{name:'队伍与对话'})).toBeHidden();
        await expect(page.locator('[data-stage-frame] iframe')).toHaveCount(0);
        await expect(page.getByRole('button',{name:'重新连接'})).toBeVisible();
        await measure(page, '访问结束', ['[data-stage-title]', '[data-stage-message]', '[data-action="retry"] .lq-btn__label'], info);
        await page.getByRole('link',{name:'大厅'}).click();
        await expect(page).toHaveURL(origin + '/games');
        expect(fixture.errors).toEqual([]);
    });
}

test('双模式游戏退出组队后可返回单人局，子帧断开后错误可读且能重试', async ({ page }, info) => {
    const fixture = await host(page, 'dark', true);
    const frame = page.frameLocator('[data-stage-frame] iframe'), dialog = page.getByRole('dialog');
    await expect(frame.getByRole('button', { name: '点灯' })).toBeVisible();
    await page.getByRole('button', { name: '创建队伍' }).click();
    await dialog.getByRole('button', { name: '创建', exact: true }).click();
    await expect(page.locator('[data-stage-title]')).toHaveText('等待队友加入');
    await page.getByRole('button', { name: '退出等待' }).click();
    await dialog.getByRole('button', { name: '确认', exact: true }).click();
    const resume = page.getByRole('button', { name: '返回单人游戏' });
    await expect(resume).toBeVisible();
    await expect(dialog).toBeHidden();
    await measure(page, '返回单人游戏', ['[data-stage-title]', '[data-action="restart"] .lq-btn__label'], info);
    expect(fixture.writes).toContain('/rooms/rm1/leave');
    await resume.click();
    await expect(frame.getByRole('button', { name: '点灯' })).toBeVisible();
    expect(fixture.writes.filter(p => p === '/games/g1/runs')).toHaveLength(2);
    await frame.getByRole('link', { name: '换页' }).click();
    await expect(page.locator('[data-stage-title]')).toHaveText('连接中断');
    await expect(page.locator('[data-stage-message]')).toHaveText('游戏页面发生跳转，请重新连接');
    await measure(page, '连接中断', ['[data-stage-title]', '[data-stage-message]', '[data-action="retry"] .lq-btn__label'], info);
    await page.getByRole('button', { name: '重新连接' }).click();
    await expect(frame.getByRole('button', { name: '点灯' })).toBeVisible();
    expect(fixture.errors).toEqual([]);
});
