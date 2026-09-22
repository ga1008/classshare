import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, readS3Fixture, settleEntranceAnimations } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

// S8 PAGE 包：页面级表面材质清理的真实页验收。
//
// 三件事，每页都查：
//  1) 内容面板确实是玻璃 —— 计算值背景色带 alpha（rgba(...) 且 alpha < 1）。
//     页面层不再有"和周围玻璃格格不入的不透明卡片"。
//  2) 正文与次级文字 axe 无 serious/critical —— 半透明面板叠在页面背景图上以后，
//     对比度必须仍然过关。扫描前先等入场动画结束（合成中的 opacity 会让 axe 读到
//     根本不存在的对比失败）。
//  3) 模糊宿主的来源是结构性的 —— 每一个 backdrop-filter 非 none 的元素都必须是
//     自身带材质类、且祖先里没有另一个材质类的元素。页面层不许新造宿主，嵌套的
//     材质由 materials.css 强制关闭。这条是硬断言；总数另外量出来写进报告。

const OUTPUT = path.resolve(process.env.LQ_S8_OUTPUT || '.codex-temp/claude-s8-page-e2e');

// 每一格量完就落盘，不在内存里攒。Playwright 在用例失败后会换一个 worker 进程，
// 模块级数组会随旧进程一起消失 —— 那样恰恰是失败最多的几页一条实测值都留不下。
function record(name: string, payload: unknown) {
  fs.mkdirSync(OUTPUT, { recursive: true });
  fs.writeFileSync(path.join(OUTPUT, name), JSON.stringify(payload, null, 2) + '\n', 'utf8');
}

// 模糊宿主总数上限的论证：一页上持续存在的宿主只有两类。
//  - 外壳：顶栏 1 + Dock/侧栏 1 + 最多一个浮层 = 3。
//  - 内容：首屏可见的顶层 lq-surface 面板。管理列表页与学生首页是面板最多的两类，
//    一屏放得下的卡片不超过 13 张（1440 宽三列 × 四行再留一行余量）。
// 3 + 13 = 16。超过这个数说明有人在页面层又造了宿主，或者嵌套材质没有被压平。
const BLUR_HOST_CAP = 16;

const MATERIAL = '.lq-glass, .lq-surface, .lq-surface--frost, .ls-glass, .ls-glass-pill, .ls-lightbox__stage';

type Role = 'student' | 'teacher' | 'anonymous';
type Case = { id: string; title: string; role: Role; route: string; panels: string[]; scan: string };

const CASES: Case[] = [
  { id: 'student-home', title: '学生首页', role: 'student', route: '/dashboard',
    panels: ['[data-lq-dashboard] .ls-course-row', '[data-lq-dashboard] .ls-domain-card',
             '[data-lq-dashboard] .ls-focus-list', '[data-lq-dashboard] .lq-surface'],
    scan: 'main, [data-lq-dashboard]' },
  { id: 'teacher-home', title: '教师首页', role: 'teacher', route: '/dashboard',
    panels: ['[data-lq-dashboard] .ls-course-row', '[data-lq-dashboard] .ls-domain-card',
             '[data-lq-dashboard] .ls-focus-list', '[data-lq-dashboard] .lq-surface'],
    scan: 'main, [data-lq-dashboard]' },
  { id: 'profile', title: '个人中心', role: 'student', route: '/profile?section=settings',
    panels: ['.lq-profile-head', '.lq-profile [data-lq-profile-content] .profile-band', '.lq-profile .lq-surface'],
    scan: '.lq-profile, main' },
  { id: 'message-center', title: '消息中心', role: 'student', route: '/message-center',
    panels: ['.lq-messages .lq-surface', '.lq-messages .lq-list', '.lq-messages .lq-card'],
    scan: '.lq-messages, main' },
  // 选 textbooks 而不是随便一个管理列表页：它的正文区已经用上 lq_card/lq_surface。
  // 多数管理列表页（archive/assessment-plans、library/materials、offerings ……）
  // 的正文面板至今仍是 ui-system.src.css 里的 .manage-lp__* / .materials-* 遗留
  // 表面，整页一个 .lq-surface 都没有 —— 那是组件迁移的缺口，不在本包文件里，
  // 已写进报告交给主任务。
  { id: 'manage-list', title: '管理列表页', role: 'teacher', route: '/manage/library/textbooks',
    panels: ['.manage-content .lq-card', '.manage-content .lq-surface'],
    scan: '.manage-content, main' },
  { id: 'login', title: '登录页', role: 'anonymous', route: '/student/login',
    panels: ['.lq-login-card'], scan: '.lq-centered-main, main' },
];

async function signIn(page: import('@playwright/test').Page, role: Role) {
  const fixture = readS3Fixture();
  if (role === 'student') await loginStudent(page, fixture);
  else if (role === 'teacher') await loginTeacher(page, fixture);
}

/** 第一个真实存在的面板选择器的计算背景色。 */
async function panelBackground(page: import('@playwright/test').Page, panels: string[]) {
  return page.evaluate((selectors) => {
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      if (!node) continue;
      const style = getComputedStyle(node);
      return { selector, background: style.backgroundColor, backdrop: style.backdropFilter || 'none' };
    }
    return null;
  }, panels);
}

/** 模糊宿主：只数真正参与渲染的元素。
 *
 * 只有被渲染的元素才会建合成层、才会付出模糊的代价。页面上常驻着一批 display:none
 * 的遗留浮层底板（`.modal-backdrop` 等），它们在计算值里确实带 backdrop-filter，
 * 但一帧也没有被合成过，把它们算进预算只会让这条断言失去意义。所以按
 * checkVisibility 过滤后再判断"是不是材质元素、祖先里有没有另一个材质元素"，
 * 同时把被过滤掉的那批单独数出来，写进报告备查。 */
async function blurHosts(page: import('@playwright/test').Page, material: string) {
  return page.evaluate((selector) => {
    const hosts: { tag: string; className: string; isMaterial: boolean; nested: boolean }[] = [];
    let hidden = 0;
    for (const node of Array.from(document.querySelectorAll<HTMLElement>('*'))) {
      const style = getComputedStyle(node);
      const value = style.backdropFilter
        || (style as unknown as { webkitBackdropFilter?: string }).webkitBackdropFilter
        || 'none';
      if (!value || value === 'none') continue;
      const rendered = typeof node.checkVisibility === 'function'
        ? node.checkVisibility({ contentVisibilityAuto: true, opacityProperty: true, visibilityProperty: true })
        : !!node.getClientRects().length;
      if (!rendered) { hidden += 1; continue; }
      hosts.push({
        tag: node.tagName.toLowerCase(),
        className: typeof node.className === 'string' ? node.className.slice(0, 120) : '',
        isMaterial: node.matches(selector),
        nested: !!node.parentElement?.closest(selector),
      });
    }
    return { hosts, hidden };
  }, material);
}

function alphaOf(color: string): number | null {
  const rgba = color.match(/^rgba?\(([^)]+)\)$/);
  if (!rgba) return null;
  const parts = rgba[1].split(',').map(s => s.trim());
  return parts.length === 4 ? Number(parts[3]) : 1;
}

for (const scenario of CASES) {
  test(`S8 页面材质 ${scenario.id} (${scenario.title})`, async ({ page }) => {
    fs.mkdirSync(OUTPUT, { recursive: true });
    await signIn(page, scenario.role);

    for (const scheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 900 });
        const response = await page.goto(scenario.route);
        expect(response?.status(), `${scenario.route} ${scheme} ${width}`).toBe(200);
        await settleEntranceAnimations(page);

        const appearance = await page.evaluate(() => document.documentElement.getAttribute('data-appearance'));
        const tier = await page.evaluate(() => document.documentElement.getAttribute('data-lq-tier'));

        // 先把这一格的全部证据取齐、落盘，再统一断言。断言一旦失败测试就停了，
        // 证据必须已经在产物目录里，否则失败的那一格反而是唯一没有截图的一格。
        const panel = await panelBackground(page, scenario.panels);
        const builder = new AxeBuilder({ page });
        for (const part of scenario.scan.split(',').map(s => s.trim())) {
          if (await page.locator(part).count()) builder.include(part);
        }
        const scan = await builder.analyze();
        const severe = scan.violations.filter(v => v.impact === 'serious' || v.impact === 'critical');
        const { hosts, hidden } = await blurHosts(page, MATERIAL);
        const stray = hosts.filter(h => !h.isMaterial || h.nested);
        const alpha = panel ? alphaOf(panel.background) : null;

        if (severe.length) {
          record(`axe-${scenario.id}-${scheme}-${width}.json`, severe.map(v => ({
            id: v.id, impact: v.impact,
            nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary, html: n.html.slice(0, 300) })),
          })));
        }
        record(`measure-${scenario.id}-${scheme}-${width}.json`, {
          page: scenario.id, title: scenario.title, route: scenario.route, scheme, width,
          appearance, tier,
          panelSelector: panel?.selector ?? null, panelBackground: panel?.background ?? null, panelAlpha: alpha,
          blurHosts: hosts.length, blurHostsHiddenDeclared: hidden,
          blurHostClasses: hosts.map(h => h.className || h.tag),
          axeSevere: severe.reduce((total, v) => total + v.nodes.length, 0),
        });
        await page.screenshot({
          path: path.join(OUTPUT, `${scenario.id}-${scheme}-${width}.png`),
          fullPage: true,
        });

        // 用 expect.soft：一格失败不该让后面 11 格连证据都拿不到。测试照样判红，
        // 报告里会列出全部失败项，而不是只有最先撞上的那一个。
        expect.soft(appearance, `${scenario.id} ${scheme}`).toBe(scheme);
        // 玻璃只在 A/B 档存在；C 档按契约整页回到不透明表面，那是另一条验收线。
        expect.soft(tier, `${scenario.id} tier`).not.toBe('C');

        // 1) 内容面板是玻璃
        expect.soft(panel, `${scenario.id} ${scheme} ${width}: 没有找到内容面板`).not.toBeNull();
        expect.soft(alpha, `${scenario.id} ${scheme} ${width} ${panel?.selector} 背景 ${panel?.background}`).not.toBeNull();
        expect.soft(alpha ?? 1, `${scenario.id} ${scheme} ${width} ${panel?.selector} 背景 ${panel?.background} 不带 alpha`).toBeLessThan(1);

        // 2) 正文与次级文字 axe 无 serious/critical
        expect.soft(severe.map(v => `${v.id}:${v.nodes.length}`), `${scenario.id} ${scheme} ${width}`).toEqual([]);

        // 3) 模糊宿主只能来自材质边界
        expect.soft(stray, `${scenario.id} ${scheme} ${width}: 出现了材质边界以外的模糊宿主`).toEqual([]);
        expect.soft(hosts.length, `${scenario.id} ${scheme} ${width}: 模糊宿主 ${hosts.length} 个`).toBeLessThanOrEqual(BLUR_HOST_CAP);
      }
    }
  });
}
