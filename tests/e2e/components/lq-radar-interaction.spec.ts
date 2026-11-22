import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const graph = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const asset = (key: string) => '/static/' + graph.entries[key];
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

function courseRadar(withExpression: boolean, legacyScore: number | null, teacher: boolean) {
    return JSON.parse(execFileSync('python', ['-c', `import json, sys
from unittest.mock import patch
from tools.isolated_environment import guard_dotenv_loading
guard_dotenv_loading()
from classroom_app.services import ability_radar_service as radar
facts = {'expression_count': 0, 'participation_count': 0}
opportunities = {'coverage_status': 'complete'}
if sys.argv[1] == 'yes':
    facts.update(expression_count=4, participation_count=7, ai_message_count=3,
                 expression_source_counts={'legacy_ai': 2, 'group_exchange': 1, 'discussion_reply': 1})
    opportunities.update(offered=5, participated=3)
metrics = {'material': {'required_count': 1, 'ratio': .6},
           'assignments': {'assignment_count': 1, 'completion_ratio': 0, 'graded_count': 1, 'score_ratio': .75},
           'interactions': {'consistency_ratio': .5}}
if sys.argv[2] != 'null':
    metrics['interactions']['interaction_ratio'] = float(sys.argv[2]) / 100
row = {'offering': 1, 'scored_student': 1, 'metrics_json': json.dumps(metrics), 'score': 65,
       'evidence_json': json.dumps({'facts': facts, 'opportunities': opportunities}), 'calculated_at': '2026-11-22T11:30:00'}
peers = {'axes': dict.fromkeys(radar.AXIS_KEYS, 60), 'student_count': 6}
with patch.object(radar, 'load_private_radar_rows', return_value=[row]), patch.object(radar, 'peer_average', return_value=peers):
    result = radar.build_ability_radar(None, student_id=1, offering_ids=[1], evidence_audience=sys.argv[3])
sys.stdout.write(json.dumps(result))`,
    withExpression ? 'yes' : 'no', String(legacyScore), teacher ? 'teacher' : 'self'], {
        encoding: 'utf8', env: { ...process.env, SECRET_KEY: 'isolated-browser-fixture-only' },
    }));
}

for (const width of [360, 1366]) {
    for (const scenario of [
        { title: '讲授课堂', status: '暂无可评机会', active: false, legacy: null },
        { title: '新提问尚待评分', status: '有表达记录，待评分', active: true, legacy: null },
        { title: '先前有分并有新提问', status: '有表达记录，待评分', active: true, legacy: 18 },
        { title: '先前零分并有新提问', status: '有表达记录，待评分', active: true, legacy: 0 },
    ]) {
        test(`LQ 互动雷达 ${scenario.title} at ${width}px`, async ({ page }, info) => {
            for (const key of ['js/ability_radar.js', 'css/lq/components/ability-radar.css', 'css/tailwind-app.css']) {
                expect(graph.sourceHashes[key]).toBe(createHash('sha256').update(fs.readFileSync('static/' + key)).digest('hex'));
            }
            const narrow = width === 360;
            await page.setViewportSize({ width, height: 880 });
            const data = courseRadar(scenario.active, scenario.legacy, !narrow);
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
                const publicView = area !== ownChart;
                const row = area.locator('.lq-radar__axis').filter({ hasText: '互动表达' });
                const value = row.locator('.lq-radar__axis-value');
                const chart = area.getByRole('img');
                if (scenario.legacy === null) {
                    const text = publicView ? '暂无参考分' : scenario.status;
                    await expect(value).toHaveText(text);
                    await expect(chart).toHaveAccessibleName(new RegExp(`互动表达 ${text}`));
                    await expect(row.locator('i, b')).toHaveCount(0);
                    await expect(area.locator('polygon.lq-radar__self')).toHaveCount(0);
                    await expect(area.locator('polyline.lq-radar__self')).toHaveCSS('fill', 'none');
                } else {
                    await expect(value).toHaveText(String(scenario.legacy));
                    await expect(row).toContainText('互动表达（原口径参考）');
                    await expect(chart).toHaveAccessibleName(new RegExp(`互动表达（原口径参考） ${scenario.legacy}（同门均值 60）`));
                    await expect(chart.locator('text').filter({ hasText: '原口径参考' })).toContainText(`${scenario.legacy} / 60`);
                    await expect(row.locator('i')).toHaveCount(1);
                    await expect(area.locator('polygon.lq-radar__self')).toHaveCount(1);
                    if (!publicView) await expect(row).toContainText(scenario.status);
                }
                await expect(chart).toHaveAccessibleName(/任务执行 0/);
                await expect(area.locator('.lq-radar__axis').filter({ hasText: '任务执行' }).locator('i')).toHaveCount(1);
                await expect(area.getByText('— 未评分', { exact: true })).toHaveCount(0);
            }
            const evidence = ownChart.getByRole('region', { name: '课堂互动记录' });
            await expect(evidence).not.toContainText(scenario.status);
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
