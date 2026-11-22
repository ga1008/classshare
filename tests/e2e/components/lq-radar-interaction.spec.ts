import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const graph = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const asset = (key: string) => '/static/' + graph.entries[key];
const spokes = [{ x: 120, y: 40 }, { x: 189.3, y: 80 }, { x: 189.3, y: 160 },
    { x: 120, y: 200 }, { x: 50.7, y: 160 }, { x: 50.7, y: 80 }];
const axes = [
    ['material', '材料吸收', 60, 120, 72, 120, 12, 'middle'],
    ['task', '任务执行', 0, 120, 120, 205, 64, 'start'],
    ['quality', '成绩质量', 75, 172, 150, 205, 164, 'start'],
    ['interaction', '互动表达', null, 120, 120, 120, 220, 'middle'],
    ['consistency', '学习稳定', 50, 85, 140, 35, 164, 'end'],
    ['cultivation', '修为推进', 65, 75, 94, 35, 64, 'end'],
].map(([key, label, score, vertex_x, vertex_y, label_x, label_y, anchor]) => ({
    key, label, score, average: key === 'material' ? 60 : null, delta: key === 'material' ? 0 : null,
    vertex_x, vertex_y, label_x, label_y, anchor,
}));
const chart = {
    available: true, center: 120, course_count: 1, peer_count: 5, peer_label: '同门均值', has_average: true,
    axes, self_segments: ['85,140 75,94 120,72 120,120 172,150'], average_segments: ['120,72'],
    rings: [25, 50, 75, 100].map(ratio => ({
        ratio, points: spokes.map(({ x, y }) => `${120 + (x - 120) * ratio / 100},${120 + (y - 120) * ratio / 100}`).join(' '),
    })), spokes, points: '', average_points: '',
};
const renderSSR = (radar: object, teacher: boolean) => execFileSync('python', ['-c', [
    "import json, sys",
    "from jinja2 import Environment, FileSystemLoader",
    "env = Environment(loader=FileSystemLoader('templates'), autoescape=True)",
    "result = env.get_template('partials/ability_radar.html').render(**json.load(sys.stdin))",
    "sys.stdout.buffer.write(result.encode('utf-8'))",
].join('\n')], {
    encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' },
    input: JSON.stringify({ ability_radar: radar, radar_show_evidence: true, radar_self_label: teacher ? '该生' : '本人' }),
});

function interactionDetail(withExpression: boolean, teacher: boolean) {
    return JSON.parse(execFileSync('python', ['-c', `import json, sys
from tools.isolated_environment import guard_dotenv_loading
guard_dotenv_loading()
from classroom_app.services.interaction_expression_detail import build_interaction_detail
facts = {'expression_count': 0, 'participation_count': 0}
opportunities = {'coverage_status': 'complete'}
if sys.argv[1] == 'yes':
    facts.update(expression_count=4, participation_count=7, ai_message_count=3,
                 expression_source_counts={'legacy_ai': 2, 'group_exchange': 1, 'discussion_reply': 1})
    opportunities.update(offered=5, participated=3)
row = {'evidence_json': json.dumps({'facts': facts, 'opportunities': opportunities}), 'calculated_at': '2026-11-22T11:30:00'}
sys.stdout.write(json.dumps(build_interaction_detail([row], audience=sys.argv[2])))`,
    withExpression ? 'yes' : 'no', teacher ? 'teacher' : 'self'], {
        encoding: 'utf8', env: { ...process.env, SECRET_KEY: 'isolated-browser-fixture-only' },
    }));
}

for (const width of [360, 1366]) {
    for (const scenario of [
        { title: '讲授课堂', status: '暂无可评机会', active: false },
        { title: '已见提问', status: '已记录互动表达', active: true },
    ]) {
        test(`LQ 互动雷达 ${scenario.title} at ${width}px`, async ({ page }, info) => {
            for (const key of ['js/ability_radar.js', 'css/lq/components/ability-radar.css', 'css/tailwind-app.css']) {
                expect(graph.sourceHashes[key]).toBe(createHash('sha256').update(fs.readFileSync('static/' + key)).digest('hex'));
            }
            const narrow = width === 360;
            await page.setViewportSize({ width, height: 880 });
            const data = { ...chart, interaction_detail: interactionDetail(scenario.active, !narrow) };
            const own = renderSSR(data, !narrow);
            const errors: string[] = [];
            page.on('pageerror', error => errors.push(error.message));
            await page.route('**/*', route => {
                const url = new URL(route.request().url());
                if (url.origin !== 'https://radar.test') return route.abort();
                if (url.pathname.startsWith('/static/')) return route.fulfill({ path: path.join(process.cwd(), url.pathname.slice(1)) });
                if (url.pathname !== '/') return route.fulfill({ status: 404 });
                return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="${narrow ? 'dark' : 'light'}"><head>
                <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
                <link rel="stylesheet" href="${asset('css/tailwind-app.css')}"></head><body>
                <main id="own">${own}</main><aside id="peer"></aside>
                <script type="module">import {renderAbilityRadar} from '${asset('js/ability_radar.js')}';
                document.querySelector('#peer').append(renderAbilityRadar(${JSON.stringify(data)}, {selfLabel: '同学'}));</script></body></html>` });
            });
            await page.goto('https://radar.test/');
            const ownChart = page.locator('#own');
            for (const area of [ownChart, page.locator('#peer')]) {
                await expect(area.getByRole('img')).toHaveAccessibleName(/任务执行 0.*互动表达 未评分/);
                await expect(area.locator('polygon.lq-radar__self')).toHaveCount(0);
                await expect(area.locator('polyline.lq-radar__self')).toHaveCSS('fill', 'none');
                await expect(area.locator('circle.lq-radar__average')).toHaveCount(1);
                await expect(area.locator('.lq-radar__axis').filter({ hasText: '互动表达' })).toHaveText(/未评分/);
                await expect(area.locator('.lq-radar__axis').filter({ hasText: '互动表达' }).locator('i')).toHaveCount(0);
                await expect(area.locator('.lq-radar__axis').filter({ hasText: '任务执行' }).locator('i')).toHaveCount(1);
                await expect(area.getByText('— 未评分', { exact: true })).toBeVisible();
            }
            const evidence = ownChart.getByRole('region', { name: '课堂互动记录' });
            await expect(evidence).toContainText(scenario.status);
            await expect(ownChart.locator('.lq-radar__key.is-scope')).toHaveCount(0);
            await expect(evidence.locator('dd').nth(2)).toContainText('1 门课堂');
            await expect(evidence.locator('dt')).toHaveText(['课堂参与', '互动表达', '数据范围']);
            await expect(evidence.locator('dd')).toHaveText([data.interaction_detail.participation_label, data.interaction_detail.expression_label, data.interaction_detail.coverage_label]);
            if (scenario.active) {
                await expect(evidence.locator('dd').nth(0)).toContainText('课堂 AI 学生消息 3 条');
                await expect(evidence.locator('dd').nth(1)).toContainText('AI 提问 2 件');
                await expect(evidence.locator('dd').nth(1)).toContainText('小组讨论 1 件');
                await expect(evidence.locator('dd').nth(1)).toContainText('研讨中回应 1 条原发言');
            }
            await expect(page.locator('#peer')).not.toContainText(scenario.status);
            await expect(page.locator('#peer dl')).toHaveCount(0);
            expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
            expect(errors).toEqual([]);
            await ownChart.screenshot({ path: info.outputPath('private-radar.png') });
        });
    }
}

test('LQ 教师洞察区分待整理的 AI 学生消息与已核实的零条', async ({ page }) => {
    const pages = JSON.parse(execFileSync('python', ['-c', `import json
from jinja2 import ChainableUndefined, ChoiceLoader, DictLoader, Environment, FileSystemLoader
layout = DictLoader({'manage/layout.html': '{% block content %}{% endblock %}'})
env = Environment(loader=ChoiceLoader([layout,FileSystemLoader('templates')]), autoescape=True, undefined=ChainableUndefined)
env.filters['datetime_format'] = str
detail = env.get_template('manage/student_detail.html')
print(json.dumps([detail.render(insight={'activity_summary': {'ai_question_count': count}, 'radar': {'available': False}})
                  for count in (None, 0, 3)]))`], { encoding: 'utf8' }));
    for (const [index, value] of ['待整理', '0', '3'].entries()) {
        await page.setContent(pages[index]);
        const card = page.locator('.student-activity-card').filter({ hasText: '课堂 AI 学生消息' });
        await expect(card.locator('strong')).toHaveText(value);
    }
});
