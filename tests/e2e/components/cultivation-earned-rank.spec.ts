import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const entries = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).entries;
const asset = (key: string) => '/static/' + entries[key];
const shell = execFileSync('python', ['-c',
    "from jinja2 import Environment,FileSystemLoader; import sys; sys.stdout.buffer.write(Environment(loader=FileSystemLoader('templates')).get_template('partials/cultivation_panel.html').render().encode())",
], { encoding: 'utf8' });
const pending = {
    score: 76, snapshot_status: 'ready', class_position_status: 'updating',
    breakdown: [{ key: 'material', label: '学习材料', points: 27, percent: 60, weight: 45, remaining: 18,
        signals: [{ label: '研读覆盖', ratio: 0.5, percent: 50, value_label: '2 / 4 份读完', points: 13.5, max_points: 22.5 }] }],
    recent_score_events: [{ component_label: '材料修为校正', delta: 4.2, delta_label: '+4.2' }],
};
const self = { rank: 2, name: '本人', score: 76, is_self: true };
const leader = { rank: 1, name: '同学甲', score: 80 };
const ready = { ...pending, class_position_status: 'ready', class_position: {
    total: 2, current: self, leader, ranking: [leader, self], mountain: { min_score: 76, max_score: 80, points: [] },
} };

for (const width of [390, 1366]) {
    test(`class material credit and pending rank at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 850 });
        const errors: string[] = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.origin !== 'https://cultivation.test') return route.abort();
            if (url.pathname.startsWith('/static/')) return route.fulfill({ path: path.join(process.cwd(), url.pathname.slice(1)) });
            if (url.pathname === '/api/classrooms/1/learning/progress') return route.fulfill({ json: { progress: ready } });
            if (url.pathname !== '/') return route.fulfill({ status: 404 });
            return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><head>
                <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
                <link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/lq/pages/cultivation.css')}">
                </head><body><button data-cultivation-panel-open>查看修为</button>${shell}
                <script type="module">import {initCultivationPanel} from '${asset('js/cultivation_panel.js')}';
                initCultivationPanel({classOfferingId:1,progress:${JSON.stringify(pending)}});</script></body></html>` });
        });
        await page.goto('https://cultivation.test/');
        const open = page.getByRole('button', { name: '查看修为' });
        await expect(open).toHaveAttribute('aria-expanded', 'false');
        await open.click();
        const dialog = page.getByRole('dialog');
        await expect(dialog.getByText(/全班更新完成后显示名次/)).toBeVisible();
        await expect(dialog.getByRole('list', { name: '宗门实力榜' })).toHaveCount(0);
        await dialog.locator('[data-component="material"] summary').click();
        await expect(dialog.locator('.lq-cultivation__signal-value')).toHaveText('2 / 4 份读完');
        await dialog.getByText(/修为日志 · 最近/).click();
        await expect(dialog.getByText('材料修为校正')).toBeVisible();
        await expect(dialog.getByText('+4.2')).toBeVisible();
        await page.keyboard.press('Escape');
        await expect(open).toBeFocused();
        await open.click();
        const board = dialog.getByRole('list', { name: '宗门实力榜' });
        await expect(board.locator('li').nth(1)).toContainText('本人');
        await expect(board.locator('li').nth(1)).toHaveAttribute('data-rank', '2');
        expect(errors).toEqual([]);
    });
}
