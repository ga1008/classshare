import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, readS3Fixture, settleEntranceAnimations } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

// S9 U2 包：profile / messages / growth / calendar 四个页族的材质边界验收。
//
// 每页四件事：
//  1) 背景图必须处在 scene 形态 —— 否则玻璃背后是一块纯色，测的是关闭态。
//     用例开头显式 PATCH /api/profile/ui-preferences（带 X-UI-Preferences-Context
//     与当前 version），不依赖默认值。
//  2) 顶层面板确实是材质边界 —— 画上了预渲染的霜层（background-image 含
//     /frost/、background-attachment 含 fixed、材质填充带 alpha），且**不是**
//     模糊宿主。背景层是固定图，模糊每帧重算没有意义。
//  3) 整页模糊宿主数不超过 BLUR_HOST_CAP（论证见下），且每一个宿主都必须是
//     "被声明过的顶层面板"，不是页面层随手造出来的。
//  4) 正文与次级文字 axe 无 serious/critical（扫描前先等入场动画结束）。

const OUTPUT = path.resolve(process.env.LQ_S9_OUTPUT || '.codex-temp/claude-s9-u2-e2e');

function record(name: string, payload: unknown) {
  fs.mkdirSync(OUTPUT, { recursive: true });
  fs.writeFileSync(path.join(OUTPUT, name), JSON.stringify(payload, null, 2) + '\n', 'utf8');
}

// 模糊宿主预算的论证。
//
// 合成一帧的代价不是"文档里有多少个宿主"，而是"这一帧真正要被模糊的面积"。只有
// 落在视口里的宿主才会被绘制、才会建离屏通道；折叠在下方的卡片这一帧一分钱也不
// 花。所以预算按**视口内可见宿主**封顶，另外把整文档总数一并量出来备查。
//
//  - 外壳：顶栏 1 + 底部 Dock/侧栏 1 + 至多一个常驻浮层 = 3。
//  - 内容：一屏放得下的顶层面板。1440×900 一屏最多三列 × 四行 = 12 块还放得下
//    正文（再密就不是面板而是芯片了），加上一行余量 13。
// 3 + 13 = 16，与 S8 PAGE 包 (lq-s8-page-material.spec.ts) 已经论证并落地的预算
// 一致；超过它只可能是嵌套材质没被压平，或页面层新造了宿主。
//
// 这条论证现在只剩历史意义：面板的玻璃改由预渲染霜层贴图承担，不再有宿主，所以
// 预算不再是「一屏放得下几块面板」，而是「还有谁在用实时模糊」。
//
// 实测（12 个场景 × 明暗 × 1440/390）：视口内宿主最多 2 个，多数页面为 0。剩下的
// 只有外壳与短暂浮层：顶栏 1 + 窄屏底部 Dock 1 + 至多一个浮层 = 3。对话框、灯箱、
// toast 这类**短暂**浮层仍用实时模糊——它们盖的是页面内容而不是背景图，静态贴图
// 在那里是错的，而且同时至多一两个。
// 超过 3 就说明某个常驻面板又自己造了宿主，也就是这次改造要消除的那类重绘。
const BLUR_HOST_CAP = 3;

// 允许成为模糊宿主的元素：组件材质类 + 本包/主任务在 page-material.css 里声明的
// 顶层面板。任何不在这份清单里的模糊宿主都是页面层新造的，属于违规。
const MATERIAL = [
  '.lq-glass', '.lq-surface', '.lq-surface--frost',
  '.ls-glass', '.ls-glass-pill', '.ls-lightbox__stage',
  '[data-lq-material]:not([data-lq-material="control"])',
  // 主任务 page-material.css 末尾声明的顶层面板
  '[data-lq-dashboard] .ls-schedule', '[data-lq-dashboard] .ls-tools',
  '[data-lq-dashboard] .ls-domains', '[data-lq-dashboard] .ls-focus-list',
  '[data-lq-dashboard] .ls-courses',
  '[data-lq-scope="growth"] .pts-panel',
  // 成就墙的承载面板：卡片数随数据增长，宿主在承载层而不是每张卡片上
  '[data-lq-scope="growth"] .lq-grid--cards',
  '.lq-messages .message-center-private-panel',
  // 本包在 pages/*.css 里声明的顶层面板
  '.lq-profile .lq-profile-head', '.lq-profile .lq-profile-nav',
  '.lq-profile .lq-profile-appearance',
  '.lq-profile [data-lq-profile-content] .profile-band',
  '.lq-profile [data-lq-profile-content] .profile-portfolio-dashboard',
  '.lq-messages .message-center-feed',
  '[data-lq-scope="growth"] .path-focus-card',
  '[data-lq-scope="growth"] .wrongbook-panel',
  '[data-lq-scope="growth"] .path-card-list', '[data-lq-scope="growth"] .review-card-list',
  '[data-lq-scope="growth"] .path-toolbar', '[data-lq-scope="growth"] .path-ladder',
  '[data-lq-scope="growth"] .path-course-board', '[data-lq-scope="growth"] .review-toolbar',
  '[data-lq-scope="growth"] .review-card',
  '[data-lq-calendar]',
].join(', ');

type Family = 'profile' | 'messages' | 'growth' | 'calendar';
type Case = {
  id: string; title: string; family: Family; role: 'student' | 'teacher'; route: string;
  panels: string[]; scan: string;
  /** 必须**不是**模糊宿主的元素（嵌套材质、数据项）。 */
  flattened?: string[];
};

const CASES: Case[] = [
  { id: 'profile-overview', title: '个人中心 · 概览', family: 'profile', role: 'student', route: '/profile?section=overview',
    panels: ['.lq-profile [data-lq-profile-content] .profile-band:not(.profile-section-head)', '.lq-profile .lq-profile-head'],
    flattened: ['.lq-profile [data-lq-profile-content] .profile-metric-card',
                '.lq-profile [data-lq-profile-content] .profile-chart-card'],
    scan: '.lq-profile, main' },
  { id: 'profile-settings', title: '个人中心 · 设置', family: 'profile', role: 'student', route: '/profile?section=settings',
    panels: ['.lq-profile [data-lq-profile-content] .profile-band:not(.profile-section-head)', '.lq-profile .lq-profile-head'],
    scan: '.lq-profile, main' },
  { id: 'profile-appearance', title: '个人中心 · 外观', family: 'profile', role: 'student', route: '/profile?section=appearance',
    panels: ['.lq-profile .lq-profile-appearance', '.lq-profile .lq-profile-head'], scan: '.lq-profile, main' },
  { id: 'messages-notifications', title: '消息中心 · 通知', family: 'messages', role: 'student', route: '/profile?section=notifications',
    panels: ['.lq-messages .message-center-feed'],
    flattened: ['.lq-messages .message-center-feed .lq-card', '.lq-messages .message-center-feed .lq-list'],
    scan: '.lq-messages, main' },
  { id: 'messages-private', title: '消息中心 · 私信', family: 'messages', role: 'student', route: '/profile?section=private',
    panels: ['.lq-messages .message-center-private-panel'],
    flattened: ['.lq-messages .message-center-private-panel .lq-composer'],
    scan: '.lq-messages, main' },
  { id: 'growth-learning-path', title: '成长 · 学习路径', family: 'growth', role: 'student', route: '/learning-path',
    panels: ['[data-lq-scope="growth"] .path-card-list', '[data-lq-scope="growth"] .path-focus-card'],
    flattened: ['[data-lq-scope="growth"] .path-ladder-step', '[data-lq-scope="growth"] .path-course-card',
                '[data-lq-scope="growth"] .path-card'],
    scan: '[data-lq-scope="growth"], main' },
  { id: 'growth-points-shop', title: '成长 · 学分币', family: 'growth', role: 'student', route: '/points',
    panels: ['[data-lq-scope="growth"] .pts-panel'],
    flattened: ['[data-lq-scope="growth"] .pts-item'],
    scan: '[data-lq-scope="growth"], main' },
  { id: 'growth-wrong-book', title: '成长 · 错题本', family: 'growth', role: 'student', route: '/wrong-book',
    panels: ['[data-lq-scope="growth"] .wrongbook-panel'],
    flattened: ['[data-lq-scope="growth"] .wrongbook-item', '[data-lq-scope="growth"] .wrongbook-stat'],
    scan: '[data-lq-scope="growth"], main' },
  { id: 'growth-feedback-review', title: '成长 · 复盘', family: 'growth', role: 'student', route: '/feedback-review',
    panels: ['[data-lq-scope="growth"] .review-card-list', '[data-lq-scope="growth"] .review-toolbar'],
    flattened: ['[data-lq-scope="growth"] .review-stat', '[data-lq-scope="growth"] .review-feedback-grid div',
                '[data-lq-scope="growth"] .review-card'],
    scan: '[data-lq-scope="growth"], main' },
  { id: 'growth-achievements', title: '成长 · 成就墙', family: 'growth', role: 'student', route: '/achievements',
    // The wall's carrier is the boundary, not each card: card count follows the
    // data, and a host per card made the compositing follow it too.
    panels: ['[data-lq-scope="growth"] .lq-grid--cards'], scan: '[data-lq-scope="growth"], main' },
  // 日历的独立形态在管理端「学期」页：学生/教师首页那一份被搬进 .ls-schedule
  // 面板内部，按契约不再做宿主，由下面 calendar-nested 这一格单独盯着。
  { id: 'calendar-semesters', title: '学期日历 · 独立形态', family: 'calendar', role: 'teacher', route: '/manage/semesters',
    panels: ['[data-lq-calendar]'], scan: '[data-lq-calendar]' },
  { id: 'calendar-nested', title: '学期日历 · 首页嵌套形态', family: 'calendar', role: 'student', route: '/dashboard',
    panels: ['[data-lq-dashboard] .ls-schedule'],
    flattened: ['.ls-calendar-host [data-lq-calendar]'],
    scan: '[data-lq-dashboard], main' },
];

/** 把背景图切到 scene：关闭态下玻璃背后是纯色，测不出材质。 */
async function forceSceneBackdrop(page: import('@playwright/test').Page) {
  await page.goto('/profile?section=appearance');
  // 令牌由 partials/lq_theme_attrs.html 的 body_attrs 写在 <body
  // data-ui-palette-context>，和 static/js/user_ui_preferences.js 读的是同一处。
  const context = await page.evaluate(
    () => document.body.getAttribute('data-ui-palette-context') || '');
  const current = await page.request.get('/api/profile/ui-preferences');
  const version = (await current.json()).preferences.version as number;
  const response = await page.request.patch('/api/profile/ui-preferences', {
    headers: { 'Content-Type': 'application/json', 'X-UI-Preferences-Context': context },
    // LQ_S9_BACKDROP=off 用于归因：同一页同一外观下比较开/关两态的 axe 结果，
    // 分清"材质本身对比度不够"和"背景层参与了合成"。默认永远是 scene。
    data: { backdrop: process.env.LQ_S9_BACKDROP === 'off' ? 'off' : 'scene', appearance: 'auto', version },
  });
  return { status: response.status(), body: await response.text(), context };
}

async function measure(
  page: import('@playwright/test').Page,
  panels: string[],
  flattened: string[],
  material: string,
) {
  return page.evaluate(({ selectors, flatSelectors, materialSelector }) => {
    const filterOf = (node: Element) => {
      const style = getComputedStyle(node);
      return style.backdropFilter
        || (style as unknown as { webkitBackdropFilter?: string }).webkitBackdropFilter || 'none';
    };

    let panel: {
      selector: string; background: string; backdrop: string;
      image: string; attachment: string;
    } | null = null;
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      if (!node) continue;
      const style = getComputedStyle(node);
      panel = {
        selector, background: style.backgroundColor, backdrop: filterOf(node),
        image: style.backgroundImage, attachment: style.backgroundAttachment,
      };
      break;
    }

    // 声明为"必须被压平"的元素：嵌套材质与数据项。抓到一个还带模糊的就记下来。
    const notFlattened: { selector: string; className: string; backdrop: string }[] = [];
    for (const selector of flatSelectors) {
      for (const node of Array.from(document.querySelectorAll(selector))) {
        const value = filterOf(node);
        if (value && value !== 'none') {
          notFlattened.push({
            selector,
            className: typeof node.className === 'string' ? node.className.slice(0, 120) : '',
            backdrop: value,
          });
        }
      }
    }

    const hosts: {
      tag: string; className: string; declared: boolean; nested: boolean; inViewport: boolean;
    }[] = [];
    let hidden = 0;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    for (const node of Array.from(document.querySelectorAll<HTMLElement>('*'))) {
      const value = filterOf(node);
      if (!value || value === 'none') continue;
      // 只有真正参与渲染的元素才建合成层。常驻 display:none 的遗留浮层底板在
      // 计算值里也带 backdrop-filter，把它们算进预算会让这条断言失去意义。
      const rendered = typeof node.checkVisibility === 'function'
        ? node.checkVisibility({ contentVisibilityAuto: true, opacityProperty: true, visibilityProperty: true })
        : !!node.getClientRects().length;
      if (!rendered) { hidden += 1; continue; }
      const rect = node.getBoundingClientRect();
      hosts.push({
        tag: node.tagName.toLowerCase(),
        className: typeof node.className === 'string' ? node.className.slice(0, 140) : '',
        declared: node.matches(materialSelector),
        nested: !!node.parentElement?.closest(materialSelector),
        inViewport: rect.width > 0 && rect.height > 0
          && rect.bottom > 0 && rect.top < vh && rect.right > 0 && rect.left < vw,
      });
    }

    // 取样：把 axe 报出问题的那几类块的计算值一并落盘，报告里要写实测数字。
    const samples: Record<string, { background: string; color: string }> = {};
    for (const selector of ['body', 'main.main-content', '.review-workbench', '.review-toolbar', '.lq-page-backdrop__veil', '.review-feedback-grid div', '.review-search', '.path-search',
                            '.review-card', '.path-card', '.wrongbook-badge--course',
                            '.cultivation-card__mark', '.date-meta', '.semester-mini-tag']) {
      const node = document.querySelector(selector);
      if (!node) continue;
      const style = getComputedStyle(node);
      samples[selector] = { background: style.backgroundColor, color: style.color };
    }

    const backdropLayer = document.querySelector('[data-lq-page-backdrop]');
    const backdropImage = backdropLayer?.querySelector('.lq-page-backdrop__image') ?? null;
    return {
      panel, hosts, hidden, notFlattened, samples,
      backdropMode: backdropLayer?.getAttribute('data-lq-backdrop-mode') ?? null,
      backdropImage: backdropImage ? getComputedStyle(backdropImage).backgroundImage : null,
    };
  }, { selectors: panels, flatSelectors: flattened, materialSelector: material });
}

function alphaOf(color: string): number | null {
  const rgba = color.match(/^rgba?\(([^)]+)\)$/);
  if (!rgba) return null;
  const parts = rgba[1].split(/[,/]/).map(s => s.trim()).filter(Boolean);
  return parts.length === 4 ? Number(parts[3]) : 1;
}

for (const scenario of CASES) {
  test(`S9 页面材质 ${scenario.id} (${scenario.title})`, async ({ page }) => {
    fs.mkdirSync(OUTPUT, { recursive: true });
    const fixture = readS3Fixture();
    if (scenario.role === 'teacher') await loginTeacher(page, fixture);
    else await loginStudent(page, fixture);
    const patched = await forceSceneBackdrop(page);
    expect(patched.status, `切换背景图到 scene 失败：${patched.body}`).toBe(200);

    for (const scheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 900 });
        const response = await page.goto(scenario.route);
        expect(response?.status(), `${scenario.route} ${scheme} ${width}`).toBe(200);
        await settleEntranceAnimations(page);

        const appearance = await page.evaluate(() => document.documentElement.getAttribute('data-appearance'));
        const tier = await page.evaluate(() => document.documentElement.getAttribute('data-lq-tier'));
        const data = await measure(page, scenario.panels, scenario.flattened ?? [], MATERIAL);
        const visibleHosts = data.hosts.filter(h => h.inViewport);

        const builder = new AxeBuilder({ page });
        for (const part of scenario.scan.split(',').map(s => s.trim())) {
          if (await page.locator(part).count()) builder.include(part);
        }
        const scan = await builder.analyze();
        const severe = scan.violations.filter(v => v.impact === 'serious' || v.impact === 'critical');
        const stray = data.hosts.filter(h => !h.declared || h.nested);
        const alpha = data.panel ? alphaOf(data.panel.background) : null;

        if (severe.length) {
          record(`axe-${scenario.id}-${scheme}-${width}.json`, severe.map(v => ({
            id: v.id, impact: v.impact,
            nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary, html: n.html.slice(0, 300) })),
          })));
        }
        record(`measure-${scenario.id}-${scheme}-${width}.json`, {
          page: scenario.id, title: scenario.title, family: scenario.family, route: scenario.route,
          scheme, width, appearance, tier,
          backdropMode: data.backdropMode, backdropImage: data.backdropImage,
          panelSelector: data.panel?.selector ?? null,
          panelBackground: data.panel?.background ?? null,
          panelBackdrop: data.panel?.backdrop ?? null,
          panelAlpha: alpha,
          blurHosts: data.hosts.length,
          blurHostsInViewport: visibleHosts.length,
          blurHostsHiddenDeclared: data.hidden,
          blurHostClasses: data.hosts.map(h => h.className || h.tag),
          blurHostClassesInViewport: visibleHosts.map(h => h.className || h.tag),
          strayHosts: stray,
          notFlattened: data.notFlattened,
          samples: data.samples,
          axeSevere: severe.reduce((total, v) => total + v.nodes.length, 0),
        });
        await page.screenshot({
          path: path.join(OUTPUT, `${scenario.id}-${scheme}-${width}.png`),
          fullPage: true,
        });

        // soft：一格失败不该让后面几格连证据都留不下。
        expect.soft(appearance, `${scenario.id} ${scheme}`).toBe(scheme);
        expect.soft(tier, `${scenario.id} tier`).not.toBe('C');

        // 1) 背景图处在 scene 形态，玻璃背后确有东西可透
        if (process.env.LQ_S9_BACKDROP !== 'off') {
          expect.soft(data.backdropMode, `${scenario.id} ${scheme} ${width}: 背景层形态`).toBe('scene');
          expect.soft(data.backdropImage ?? '', `${scenario.id} ${scheme} ${width}: 背景图为空`).toContain('url(');
        }

        // 2) 顶层面板是材质边界——现在靠静态霜层，不再靠 backdrop-filter。
        //
        // 背景层是固定的、来自随程序发布的图库，同一张图、同一种模糊，每帧重算
        // 毫无意义；首页曾因此背着六个 16px 宿主铺满 88% 视口。面板改为直接画
        // 它身后那一块预渲染的模糊图（background-attachment: fixed 让这一层按
        // 视口定位，与背景层对齐）。所以判据反过来了：**必须不是模糊宿主**，
        // 而且必须真的画上了霜层。
        expect.soft(data.panel, `${scenario.id} ${scheme} ${width}: 没有找到顶层面板`).not.toBeNull();
        expect.soft(data.panel?.backdrop ?? 'none',
          `${scenario.id} ${scheme} ${width} ${data.panel?.selector} 仍是模糊宿主`).toBe('none');
        expect.soft(data.panel?.image ?? '',
          `${scenario.id} ${scheme} ${width} ${data.panel?.selector} 没有画上霜层`).toContain('/frost/');
        expect.soft(data.panel?.attachment ?? '',
          `${scenario.id} ${scheme} ${width} ${data.panel?.selector} 霜层没有按视口定位`).toContain('fixed');
        // 材质填充仍然半透明：霜层之上压的是材质自己的底色，那一层必须透。
        expect.soft(alphaOf((data.panel?.image.match(/rgba?\([^)]*\)/) ?? ['rgb(0,0,0)'])[0]) ?? 1,
          `${scenario.id} ${scheme} ${width} ${data.panel?.selector} 材质填充 ${data.panel?.image.slice(0, 60)} 不带 alpha`).toBeLessThan(1);

        // 3) 宿主只能来自被声明的顶层面板，视口内数量有上界，且该压平的必须压平
        expect.soft(stray, `${scenario.id} ${scheme} ${width}: 出现了未声明的模糊宿主`).toEqual([]);
        expect.soft(data.notFlattened,
          `${scenario.id} ${scheme} ${width}: 嵌套材质/数据项仍然是模糊宿主`).toEqual([]);
        expect.soft(visibleHosts.length,
          `${scenario.id} ${scheme} ${width}: 视口内模糊宿主 ${visibleHosts.length} 个`
          + `（整文档 ${data.hosts.length} 个）`).toBeLessThanOrEqual(BLUR_HOST_CAP);

        // 4) 正文与次级文字 axe 无 serious/critical
        expect.soft(severe.map(v => `${v.id}:${v.nodes.length}`), `${scenario.id} ${scheme} ${width}`).toEqual([]);
      }
    }
  });
}
