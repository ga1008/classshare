import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, readS3Fixture, settleEntranceAnimations } from '../fixtures/lq-s3';
import { loginStudent } from '../fixtures/p03';

// S9 U3 包：`static/css/ui-system.src.css` 里写死的浅色填充清扫验收。
//
// 这批缺陷只在一个条件下显形：**外观 dark + 背景图 scene**。写死的
// rgba(255,255,255,…) 不随主题翻转，暗色下会像白板浮在照片上；背景图若不是
// scene，玻璃背后是一块纯色，看不出差别。所以每个用例开头都显式
// PATCH /api/profile/ui-preferences 把两者钉死，不依赖默认值。
//
// 判据分两层：
//  1) **中性近白**（白/slate 系：B>=G>=R 且 B-R<=16，alpha>=0.5，相对亮度>0.8）
//     —— 正是本包负责改掉的那一类，暗色下必须为 0，断言。
//  2) **色调近白**（indigo-50 / amber-50 / emerald-50 这类语义色浅底）
//     —— 不属于本包范围（改成中性材质会丢掉语义），只计数留证，不断言。

const OUTPUT = path.resolve(process.env.LQ_S9_OUTPUT || '.codex-temp/claude-s9-u3-e2e');

function record(name: string, payload: unknown) {
  fs.mkdirSync(OUTPUT, { recursive: true });
  fs.writeFileSync(path.join(OUTPUT, name), JSON.stringify(payload, null, 2) + '\n', 'utf8');
}

type Case = { id: string; title: string; route: string; scan: string };

// 覆盖本包改动量最大的几个来源段：profile.css / classroom.css(path-*、pts-*)
// / dashboard.css / semester_calendar.css / blog.css。
const CASES: Case[] = [
  { id: 'profile-overview', title: '个人中心 · 概览', route: '/profile?section=overview', scan: '.lq-profile, main' },
  { id: 'profile-settings', title: '个人中心 · 设置', route: '/profile?section=settings', scan: '.lq-profile, main' },
  { id: 'dashboard', title: '首页与学期日历', route: '/dashboard', scan: 'main' },
  { id: 'learning-path', title: '成长 · 学习路径', route: '/learning-path', scan: '[data-lq-scope="growth"], main' },
  { id: 'achievements', title: '成长 · 成就墙', route: '/achievements', scan: '[data-lq-scope="growth"], main' },
  { id: 'blog', title: '博客中心', route: '/blog', scan: 'main' },
  { id: 'wrong-book', title: '成长 · 错题本', route: '/wrong-book', scan: '[data-lq-scope="growth"], main' },
];

// 本包管不到的两处中性近白，**不是白名单，是所有权边界**：两者都不在
// `static/css/ui-system.src.css` 里，U3 按分包规则不得改动，所以它们从本包的
// 判据里摘出来单独计数，由主任务转交给持有方。改掉它们之前，这两条会一直出现
// 在 foreignResidue 里，不会被悄悄吃掉。
const FOREIGN: { match: RegExp; owner: string }[] = [
  { match: /\btopbar-scene-chip\b/, owner: 'static/css/lq/pages/life-tip.css:567 rgba(255,255,255,0.55)（static/css/lq/** 非本包）' },
  { match: /\bcs-card\b/, owner: 'static/js/course_schedule_deck.js:153 rgba(255,255,255,0.97)（runbook §5 明令不动其内部）' },
];

// 暗色下**改动前就已经存在**的 color-contrast 违规签名（`fg|bg` → 节点数）。
// 取自同一 spec 在改动前的实测：`.codex-temp/claude-s9-u3-e2e-baseline/axe-*.json`
// （做法：把 ui-system.src.css 还原成改动前的副本、locked_build、跑同一套用例）。
// 这不是放宽阈值——判据仍然是"不得出现新的违规、也不得比改动前更多"，只是把
// 本包没有引入、也无权修（色调令牌与 color-mix(…, white/black) 定色）的存量
// 单列出来，让回归一眼可辨。存量本身写进报告，交主任务另行处理。
const BASELINE_SEVERE: Record<string, Record<string, number>> = {
  'learning-path': {
    'color-contrast|#b3bdcc|#eceef1': 6, 'color-contrast|#64748b|#cacbcd': 3,
    'color-contrast|#b3bdcc|#eef0f2': 3, 'color-contrast|#64748b|#eef0f2': 3,
    'color-contrast|#ebeff5|#797c85': 2,
  },
  blog: {
    'color-contrast|#7c3aed|#241f3d': 1, 'color-contrast|#ebeff5|#909296': 1,
    'color-contrast|#8f9cae|#909296': 1,
  },
  'wrong-book': { 'color-contrast|#7c7ef4|#282c4c': 1 },
};

function severeSignatures(violations: { id: string; nodes: { failureSummary?: string | null }[] }[]) {
  const counts: Record<string, number> = {};
  for (const v of violations) {
    for (const node of v.nodes) {
      const m = /foreground color: (#\w+), background color: (#\w+)/.exec(node.failureSummary || '');
      const key = m ? `${v.id}|${m[1]}|${m[2]}` : v.id;
      counts[key] = (counts[key] || 0) + 1;
    }
  }
  return counts;
}

/** 把外观与背景图钉死：dark + scene 是这批缺陷唯一会显形的条件。 */
async function forceAppearance(page: import('@playwright/test').Page, appearance: 'dark' | 'light') {
  await page.goto('/profile?section=appearance');
  const context = await page.evaluate(
    () => document.body.getAttribute('data-ui-palette-context') || '');
  const current = await page.request.get('/api/profile/ui-preferences');
  const version = (await current.json()).preferences.version as number;
  const response = await page.request.patch('/api/profile/ui-preferences', {
    headers: { 'Content-Type': 'application/json', 'X-UI-Preferences-Context': context },
    data: { appearance, backdrop: 'scene', version },
  });
  return { status: response.status(), body: await response.text(), context };
}

/** 扫描整页，按中性/色调把近白背景分开计数。 */
async function scanFills(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    type Hit = { selector: string; background: string; alpha: number; luminance: number; area: number };
    const neutral: Hit[] = [];
    const tinted: Hit[] = [];

    const channel = (v: number) => {
      const c = v / 255;
      return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    const describe = (node: Element) => {
      const cls = typeof node.className === 'string'
        ? node.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
      return node.tagName.toLowerCase() + (cls ? '.' + cls : '');
    };

    for (const node of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
      if (!node.getClientRects().length) continue;
      const box = node.getBoundingClientRect();
      // 面板才算数：小于 24×24 的是图标/角标，不是"浮在照片上的白块"。
      if (box.width < 24 || box.height < 24) continue;
      const bg = getComputedStyle(node).backgroundColor;
      const m = bg.match(/^rgba?\(([^)]+)\)$/);
      if (!m) continue;
      const parts = m[1].split(/[,/]/).map(s => s.trim()).filter(Boolean).map(Number);
      const [r, g, b] = parts;
      const alpha = parts.length === 4 ? parts[3] : 1;
      if (!(alpha >= 0.5)) continue;
      const luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
      if (!(luminance > 0.8)) continue;
      const hit: Hit = {
        selector: describe(node), background: bg, alpha,
        luminance: Number(luminance.toFixed(3)), area: Math.round(box.width * box.height),
      };
      if (b >= g && g >= r && b - r <= 16) neutral.push(hit);
      else tinted.push(hit);
    }

    // §13.2 共同要求：每页实测模糊宿主数。本包不得新增宿主，所以这里只计数留证。
    const blurHosts: string[] = [];
    for (const node of Array.from(document.querySelectorAll<HTMLElement>('*'))) {
      const style = getComputedStyle(node);
      const value = style.backdropFilter
        || (style as unknown as { webkitBackdropFilter?: string }).webkitBackdropFilter || 'none';
      if (!value || value === 'none') continue;
      const rendered = typeof node.checkVisibility === 'function'
        ? node.checkVisibility({ contentVisibilityAuto: true, opacityProperty: true, visibilityProperty: true })
        : !!node.getClientRects().length;
      if (!rendered) continue;
      blurHosts.push(describe(node));
    }

    const backdropLayer = document.querySelector('[data-lq-page-backdrop]');
    const backdropImage = backdropLayer?.querySelector('.lq-page-backdrop__image') ?? null;
    return {
      neutral, tinted, blurHosts,
      backdropMode: backdropLayer?.getAttribute('data-lq-backdrop-mode') ?? null,
      backdropImage: backdropImage ? getComputedStyle(backdropImage).backgroundImage : null,
    };
  });
}

for (const scenario of CASES) {
  test(`S9 遗留填充 ${scenario.id} (${scenario.title})`, async ({ page }) => {
    fs.mkdirSync(OUTPUT, { recursive: true });
    const fixture = readS3Fixture();
    await loginStudent(page, fixture);

    for (const scheme of ['dark', 'light'] as const) {
      const patched = await forceAppearance(page, scheme);
      expect(patched.status, `切换 ${scheme} + scene 失败：${patched.body}`).toBe(200);
      await page.emulateMedia({ colorScheme: scheme });
      const response = await page.goto(scenario.route);
      expect(response?.status(), `${scenario.route} ${scheme}`).toBe(200);
      await settleEntranceAnimations(page);

      const appearance = await page.evaluate(
        () => document.documentElement.getAttribute('data-appearance'));
      const tier = await page.evaluate(
        () => document.documentElement.getAttribute('data-lq-tier'));
      const data = await scanFills(page);

      const builder = new AxeBuilder({ page });
      for (const part of scenario.scan.split(',').map(s => s.trim())) {
        if (await page.locator(part).count()) builder.include(part);
      }
      const scan = await builder.analyze();
      const severe = scan.violations.filter(v => v.impact === 'serious' || v.impact === 'critical');
      if (severe.length) {
        record(`axe-${scenario.id}-${scheme}.json`, severe.map(v => ({
          id: v.id, impact: v.impact,
          nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary, html: n.html.slice(0, 300) })),
        })));
      }
      const foreign = data.neutral.filter(h => FOREIGN.some(f => f.match.test(h.selector)));
      const owned = data.neutral.filter(h => !FOREIGN.some(f => f.match.test(h.selector)));

      record(`measure-${scenario.id}-${scheme}.json`, {
        page: scenario.id, title: scenario.title, route: scenario.route, scheme, appearance, tier,
        backdropMode: data.backdropMode, backdropImage: data.backdropImage,
        blurHostCount: data.blurHosts.length,
        blurHosts: data.blurHosts,
        neutralNearWhite: data.neutral,
        ownedResidue: owned,
        foreignResidue: foreign.map(h => ({
          ...h, owner: FOREIGN.find(f => f.match.test(h.selector))!.owner,
        })),
        tintedNearWhiteCount: data.tinted.length,
        tintedNearWhiteSample: data.tinted.slice(0, 12),
        axeSevere: severe.reduce((total, v) => total + v.nodes.length, 0),
      });
      await page.screenshot({ path: path.join(OUTPUT, `${scenario.id}-${scheme}.png`), fullPage: true });

      // soft：一格失败不该让后面几格连证据都留不下。
      expect.soft(tier, `${scenario.id} tier`).not.toBe('C');
      expect.soft(data.backdropMode, `${scenario.id} ${scheme}: 背景层形态`).toBe('scene');
      expect.soft(data.backdropImage ?? '', `${scenario.id} ${scheme}: 背景图为空`).toContain('url(');

      expect.soft(appearance, `${scenario.id} 外观未切到 ${scheme}`).toBe(scheme);
      if (scheme === 'dark') {
        // 本包的核心判据：暗色下，ui-system.src.css 名下没有中性近白的面板。
        expect.soft(owned, `${scenario.id} dark: 中性近白面板 ${owned.length} 个`).toEqual([]);
      }

      // axe：暗色比对存量基线（不得新增、不得更多）；亮色基线为空，必须为空。
      const signatures = severeSignatures(severe as never);
      if (scheme === 'dark') {
        const baseline = BASELINE_SEVERE[scenario.id] || {};
        const regressions = Object.entries(signatures)
          .filter(([key, count]) => count > (baseline[key] || 0))
          .map(([key, count]) => `${key} ${baseline[key] || 0}→${count}`);
        expect.soft(regressions, `${scenario.id} dark axe 新增/增多的 serious|critical`).toEqual([]);
      } else {
        expect.soft(signatures, `${scenario.id} light axe`).toEqual({});
      }
    }
  });
}
