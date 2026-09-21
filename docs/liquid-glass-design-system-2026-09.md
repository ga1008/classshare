# LanShare 液态玻璃设计系统（Liquid Glass · iOS 26 风格）全站改造方案

> 文档状态：**真源（2026-09-19 定稿，未动工）**。本文承接并**取代** `frontend-redesign-2026-08.md`（方案 A）与 `frontend-premium-design-language.md` 的视觉章节；`ux-overhaul-2026-08.md` 的"少即是多"结构规则与 `ui-copy-simplification-plan.md` 的文案红线**继续有效**，本文不重复它们。
> 读者：负责逐页改造的 AI/工程师。目标是"照着做不会错"，所以每个组件都给出：用途、DOM 契约、尺寸、字体、颜色、形态、状态、交互、动效、无障碍、三种接入方式（Jinja 宏 / 原生 JS / React）、禁忌。
> 架构不变：FastAPI + Jinja2 + Tailwind 3 + shadcn（`tw-` 前缀）+ Vite islands + 原生 JS 模块。不做整体重写。

---

## 目录

0. 一页结论
1. 调研结论（Apple 规则、Web 技术、选型）
2. 现状基线（量化证据）
3. 设计原则（十二条铁律）
4. 基础层：令牌（Tokens）
5. 材质体系（Materials）与分层规则
6. 组件库规范（Components）——逐个组件
7. 壳与页面骨架（Shell & Layout）
8. 文案、信息层级与图标
9. 特殊界面改造（3D 课表、作业/考试、批改、白板、课堂主页、AI 对话、日历、星图、管理向导、监控、登录、博客、学习文档、投票/分组、消息）
10. 工程落地（文件结构、构建、插件、宏、JS 组件 API、React 变体、守卫脚本、迁页 SOP）
11. 分阶段路线（P0–P10，每阶段范围/文件/验收）
12. 验收与回归
13. 明确不做与风险
14. 性能预算、浏览器兼容矩阵与移动端专项（支持基线 A/B/C 三档、特性检测与退路表、数值化预算、iOS/Android/微信专项规则、真实机型实测）
附录 A 令牌全表 · 附录 B 旧类名→新组件映射 · 附录 C 每页检查清单 · 附录 D 调研来源

---

## 0. 一页结论

**做什么**：把全平台（135 个模板、158 个 JS 模块、21 个 React 岛屿、63,880 行 CSS）的视觉与交互统一到一套 **LanShare Glass（组件类前缀 `lq-`，liquid）** 组件库。视觉语言 = Apple iOS 26 Liquid Glass：半透明毛玻璃承载导航与控制层、内容层用高不透明"霜面"、胶囊按钮、同心圆角、镜面高光边、弹簧动效、明暗自适应。

**怎么做（五句话）**：

1. **令牌先行**：在现有 `--ls-*`（HSL 通道）之上新增 **玻璃材质 / 模糊阶梯 / 同心圆角 / 弹簧动效 / z 轴 / 深色主题** 六组令牌；现有 5 套学生个人配色（indigo/sky/mint/violet/rose）和教师青绿角色色全部通过令牌继承，不再各写各的。
2. **纯 CSS 玻璃为主，折射为可选增强**：全站只用 `backdrop-filter: blur() saturate()` + 内阴影高光 + 渐变描边；SVG 位移折射只在 Chromium 上给 ≤3 个浮动控件（登录卡、底部 Dock、白板工具条）做增强，不支持或低端设备自动退回。
3. **先收敛后美化**：先把 4 个壳、5 套弹层、11 个 toast、43 处 `window.confirm`、9 种 spinner、≥9 套 tab、~78 个一次性按钮家族收敛成 **一套 `lq-` 组件**；再逐页迁移。迁一页删一页旧 CSS。
4. **内容层不叠玻璃**：Apple 规则——玻璃只给"漂浮在内容之上"的导航/控制/弹层；列表、表格、表单、卡片正文用 `lq-surface`（霜面，几乎不透明、无模糊或 ≤8px）。一屏同时可见的 `backdrop-filter` 图层 ≤3。
5. **特殊界面同一美学、组件化**：3D 课表、作业/考试作答页、批改页、白板、AI 浮窗、监控大屏、职业星图等无现成库的界面，按同一令牌与材质规则重做，并拆成可复用模块（每个文件 ≤800 行）。

**顺序**：P0 守卫与解阻（cache-bust、lint、iframe 决策）→ P1 令牌+材质+Tailwind 插件 → P2 组件库（宏/JS/React 三入口）→ P3 壳统一（AppShell）→ P4 弹层/toast/confirm 收敛 → P5 管理中心（试点）→ P6 首页与课堂 → P7 作业/考试/批改（含内联 CSS 抽出）→ P8 特殊界面 → P9 深色模式与个性化 → P10 清扫与 Tailwind 4。每阶段独立可发布、可回滚。

---

## 1. 调研结论

### 1.1 Apple Liquid Glass 硬规则（取自 HIG Materials 与 WWDC25-219/356/323）

| 主题 | 规则 | 本方案的落法 |
|---|---|---|
| 材质两种 | **Regular**（默认，自适应，可放任何内容）/ **Clear**（更透，必须配压暗层，仅在媒体之上）。两者绝不混用 | `lq-glass`（Regular）为默认；`lq-glass--clear` 只允许用在灯箱、登录背景图、学习文档全屏壳三处 |
| 分层 | 玻璃"只保留给漂浮在内容之上的导航层"；**禁止玻璃叠玻璃**，玻璃上的元素用填充/透明度而不是再来一层玻璃 | 四层模型（§5.2）；`lq-glass` 内部禁止再出现 `lq-glass`，lint 守卫 |
| 明暗自适应 | 小元素（tab bar、按钮、符号）随背景亮度在亮/暗间翻转；大面积元素（侧栏、菜单）只跟随不翻转 | `data-lq-tone="light|dark"` 由背景采样决定（复用已有 `sampleImageTone`）；侧栏固定跟随主题 |
| 阴影 | 元素感知背后内容：压在文字上阴影更重，在纯浅底上更轻 | 两档 `--ls-glass-shadow` / `--ls-glass-shadow-strong`，容器 `data-lq-over="text"` 切换 |
| Tint | 一个色相生成随背景亮度映射的色阶；**只给主操作着色**；"当所有元素都着色，什么都不突出" | 每个视图只有 1 个 `lq-btn--prominent`；chips/tab 选中态用 tint 而不是实色填充 |
| 分组/变形 | 同一漂浮平面上的控件按上下文 morph；菜单从按钮原地展开 | `lq-dock` 容器 + View Transitions；不支持时退回淡入缩放 |
| 按钮层级 | `.glass` 普通 / `.glassProminent` 唯一主操作 | `lq-btn`（默认 glass）/ `lq-btn--prominent` |
| Scroll edge | Soft（内容滚到玻璃下渐变溶解）/ Hard（固定表头用均匀不透明边界）。一个视图只用一种 | 顶栏用 soft（`lq-scroll-edge`），数据表格 sticky thead 用 hard |
| 形状 | 固定 / **胶囊（默认，半径=高度 50%）** / **同心（内半径 = 父半径 − padding）** | 按钮、chip、搜索框、开关、Dock = 胶囊；卡片内元素 = 同心 |
| 排版 | SF，关键节点加粗，标题/alert 左对齐 | 字体栈以 `-apple-system` 起头；标题一律左对齐 |
| 可读性 | 稳态下避免内容与玻璃相交；玻璃上文字对比 ≥4.5:1 | 内容区顶部预留顶栏高度；玻璃上文字用 `--ls-glass-ink` 而不是 muted 灰 |
| 无障碍 | Reduce Transparency → 更霜更遮；Increase Contrast → 近纯黑/白+对比边框；Reduce Motion → 禁用弹性形变 | 三个媒体查询 + 站内开关 `data-lq-glass="tinted|clear|off"`；默认 **tinted**（Apple iOS 26.1 后自己也在往更霜、更高对比走） |

Apple Dynamic Type（Large）基准：Large Title 34/41 · Title1 28/34 · Title2 22/28 · Title3 20/25 · Headline 17 semibold · Body 17/22 · Callout 16/21 · Subheadline 15/20 · Footnote 13/18 · Caption1 12/16 · Caption2 11/13。最小点击目标 44×44。Web 桌面端中文正文取 15px（不是 17），移动端 16px。

### 1.2 Web 技术结论

- `backdrop-filter: blur()/saturate()` 全平台可用；**SVG 位移折射（`backdrop-filter: url(#f)`）仅 Chromium**，Safari/Firefox 静默退成平模糊。折射"只做愉悦，不承载语义"。
- 性能红线（教室旧笔记本）：一屏 ≤3 个 backdrop-filter 图层；blur ≤24px；滚动容器内部不放玻璃；固定定位玻璃加 `will-change: transform` + `contain: paint`；玻璃层内动画只用 `opacity/transform`；折射元素每边 ≤800px 且只在尺寸变化时重建位移图。
- 弹簧：CSS `linear()` 缓动（2023-12 起全主流支持）三档弹簧只 +1.3kB；`@starting-style` + `transition-behavior: allow-discrete` 做 popover/dialog 出入场；`@view-transition { navigation: auto }` 让 Jinja 多页导航共享顶栏/侧栏，成本几乎为零。
- 对比度：不要靠模糊保证可读性；最差背景下正文 ≥4.5:1、大字与 UI ≥3:1，必要时加 scrim；焦点环用实色 `outline: 2px solid`。

### 1.3 选型（不装第三方玻璃组件库）

| 采纳 | 用途 | 理由 |
|---|---|---|
| **自研 Tailwind 3 插件 `lq-glass`**（`frontend/tailwind/lq-glass-plugin.cjs`） | 生成 `tw-glass*` 工具类 + 四个降级媒体查询 | 零依赖；与现有 `tw-` 前缀、`--ls-*` 令牌、build:css 流程完全兼容 |
| **deepika-builds/liquid-glass**（MIT，单文件，vendored 到 `static/js/vendor/liquid-glass.js`） | 可选折射增强，`data-lq-refract` 声明式接入 | 自动 Safari/Firefox 降级；参数可控；≤3 处使用 |
| **kvin.me CSS Spring Easing** 生成的三档 `linear()` | `--ls-spring-snappy/soft/bouncy` | 无运行时；React 岛屿用 framer-motion 同参数 |
| **shadcn**：新增 `popover`、`sonner`(toast)、`alert-dialog`、`scroll-area`、`accordion` | 岛屿内弹层 | 已有 15 个基元只有 `dialog` 被用；先补齐再定变体 |

**不采纳**：rdev/liquid-glass-react（停更、React-only）、shadcn-glass-ui / daisyUI 5（需 Tailwind 4）、任何 WebGL 方案、Tontoon7/liquidglass-tailwind（无 license，只抄 recipe）。

---

## 2. 现状基线（改造前量化证据，2026-09-19）

| 维度 | 现状 | 对方案的含义 |
|---|---|---|
| CSS | `ui-system.src.css` 63,880 行（17 个旧文件拼接 + 18k 行 polish 层）；另 20 个独立 css 6,317 行；7 个模板含 **5,486 行内联 `<style>`**（exam_take 1489、assignment_wrong_summary 1472、assignment_detail_teacher 1259、exam_editor 1047、submission_detail 794、assignment_detail_student 483、classroom_main_v4 292） | 内联 CSS 令牌够不着，必须先抽出（P7） |
| 令牌 | 唯一 `:root`，`--ls-*` 26 个语义色 + 6 档圆角 + 6 档字号 + 5 档阴影；**无深色主题**（仅 2 处模块级 `prefers-color-scheme`）；`--ux-motion-*` 在第二个 `:root` 里；灯箱 `--ls-glass-*` 在第 63609 行 | 深色从零建；三处令牌块合并 |
| 玻璃 | 177 处 `backdrop-filter`，**17 种模糊半径**；已有 5 处真"液态玻璃"：灯箱 `.ls-glass`（种子）、`.ui-explain-popover`、人生一言登录屏、登录表单、blog-paper 浮条；10 处 `backdrop-filter: none` 性能撤退 | 模糊阶梯收敛到 4 档；`.ls-glass` 升级为全局材质 |
| 壳 | 4 个独立文档级壳：`base_navbar.html`、`manage/layout.html`（零内联样式，最干净）、`resume/layout.html`（`rz-` 私有命名空间）、`classroom_main_v4.html`（自带顶栏+菜单+tab dock） | 统一为一个 AppShell（P3） |
| 弹层 | 5 套并存：Bootstrap 式 `.modal-*`（38 处 backdrop）、`.ls-popover`/ui_explanation、白板 popover、约 40 个按功能命名的 bespoke modal、shadcn Radix；4 套独立 drawer；≥8 套 popover | 收敛到 `lq-modal/sheet/drawer/popover/menu` 五种（P4） |
| 反馈 | **11 个 toast 实现**；**43 处 `window.confirm`**（25 文件）+3 处 `alert()`；**9 种 spinner 类名** | 一个 `LQ.toast`、一个 `LQ.confirm`、一个 `lq-spinner` |
| 按钮 | `.btn*` 676 处（模板）+ 约 520 处（JS 字符串）；`app-topbar-action` 108；`ls-button` 18；CSS 里 **83 个 btn 家族**，其中 ~78 个一次性；shadcn `button.tsx` 从未被 import | 6 变体 × 3 尺寸的 `lq-btn`，三入口 |
| JS 生成标记 | 256 处内联 `style="`、496 个 hex、115 个 rgba 散布在 JS 模板字符串里（最差：lessondoc_wizard 45 处 style、course_schedule_deck 38 hex + 318 行注入 CSS） | JS 也要迁，且要"去 hex"守卫 |
| shadcn | 15 个基元只有 `dialog` 被用（893 行死码） | 先补齐所需基元、再做 glass 变体；未用的删 |
| 图标 | 313 个手贴内联 SVG + `app_topbar_icon`（21 个）+ `manage_icon` 两套注册表 + lucide-react；3 种描边粗细 | 统一 lucide 几何、描边 1.75 |
| 字体 | 无 web 字体；栈以 `Segoe UI` 起头，无 `-apple-system`；7 种 mono 栈；无 `--font-mono` | 重排字体栈，加 mono 令牌 |
| z-index | 195 处字面量，事实阶梯 1040 / 1200 / 1400 / 2200 / 2400 / 2600 / 2147483000 | 建 8 档 `--ls-z-*` |
| 断点 | 全部 `max-width`，720/760/768、900/960、1120/1180 三对近重复 | 5 档断点令牌 |
| 无障碍 | ARIA 覆盖好（模板 916 处）；`:focus-visible` 212 处在 CSS、React 层 0；`prefers-reduced-motion` CSS 112 处、React 层 0 | React 层补齐 |
| cache-bust | `asset_url()` 自动 mtime；但仍有 38 处手写 `?v=`（career_path.html 4 处、resume 每页 2 处、3 个岛屿加载器内硬编码） | P0 先转 `asset_url()` |
| iframe | 管理向导 `manage_workflow.js` 与 HTML 包壳 `material_render_shell.js`、课堂成员工作区用 iframe | 玻璃无法折射 iframe 内容——P0 决策（§13） |
| 博客 | `blog-paper.css` 2,732 行"纸感阅读台"，文档化的**反玻璃**皮肤 | 保留纸感正文，只把导航/浮条/弹层接玻璃（§9.12） |

---

## 3. 设计原则（十二条铁律，带 ★ 的由 lint 强制）

1. ★ **只用令牌取色**：新代码禁止 hex / rgb / hsl 字面量（`url()` 与 `--ls-c-*` 调色板定义行除外）。玻璃透明度也走令牌。
2. ★ **玻璃只给漂浮层**：`lq-glass` 只允许出现在 顶栏/侧栏/Dock/工具条/FAB/弹层/浮窗；列表项、表格、表单、卡片正文用 `lq-surface`。`lq-glass` 的后代里禁止再出现 `lq-glass`。
3. ★ **一屏 ≤3 个 backdrop-filter 图层**，滚动容器内部为 0。列表超过 12 项时卡片必须是 `lq-card--flat`。
4. **一个视图一个主操作**：每个页面/弹层只有一个 `lq-btn--prominent`；其余是 glass / soft / ghost。
5. **胶囊与同心**：可点击的独立控件 = 胶囊；容器内贴边子元素圆角 = 父圆角 − 内边距，不许自定半径。
6. **零值隐身、重复合并**（承接 ux-overhaul）：空数据组件降级为一行；同屏同一数字只出现一次；不为"展示玻璃"添加装饰块。
7. **文案短而功能性**：按钮 ≤4 个汉字（动词开头），标题 ≤10 字，说明进 `data-explain` 浮窗；错误/权限/硬约束必须常显（ui-copy 红线）。
8. **动效只表达进入、聚焦、选择、完成、状态变化**：时长 120–400ms，弹簧只用于弹层与 Dock；`prefers-reduced-motion` 下全关且禁用形变。
9. **三入口等价**：每个组件的 Jinja 宏、原生 JS 工厂、React 组件输出**完全相同的 DOM 与类名**；改样式只改 CSS。
10. ★ **迁一页删一页**：页面迁到 `lq-` 后，该页在 `ui-system.src.css` 的专属段落与内联 `<style>` 必须删除；`grep` 该页旧类名应为 0。
11. **移动优先的触控**：所有可点击目标在 `pointer: coarse` 下 ≥44×44；hover 才能发现的信息一律有非 hover 等价入口。
12. **无障碍不让位**：玻璃上正文 ≥4.5:1；焦点环实色 2px；`prefers-reduced-transparency` / `prefers-contrast: more` / `forced-colors` 三种模式截图必须通过。

---

## 4. 基础层：令牌（Tokens）

所有令牌定义在 `static/css/lq/tokens.css` 的**唯一** `:root` 块（把第 52249 行的 `--ux-motion-*` 第二个 `:root` 与第 63609 行灯箱 `--ls-glass-*` 合并进来）。Tailwind 通过 `tailwind.config.js` 暴露为 `tw-` 工具类；插件 `lq-glass-plugin.cjs` 生成组件类。命名：**语义令牌 `--ls-*`**（沿用），**组件类 `lq-*`**（新，便于 grep 迁移残留）。

### 4.1 颜色（沿用 + 补齐）

保留现有 26 个 `--ls-*` HSL 通道令牌（附录 A）。新增：

```css
/* 表面层级（亮色） */
--ls-surface-0: 222 47% 97%;      /* 页面底 ambient */
--ls-surface-1: 0 0% 100%;        /* 卡片/表单 霜面基色 */
--ls-surface-2: 210 40% 98%;      /* 卡片内嵌区（同心内层） */
--ls-ink: 222 47% 11%;            /* = foreground */
--ls-ink-2: 215 25% 27%;          /* 次级文字 */
--ls-ink-3: 215 16% 47%;          /* 辅助文字（= muted-foreground） */
--ls-line: 214 32% 91%;           /* 分隔线（= border） */
--ls-line-strong: 215 20% 80%;
/* 语义色 soft 档（tint 用，写成 "通道 / alpha" 供 hsl() 直接用） */
--ls-primary-soft: var(--ls-primary) / 0.12;
--ls-success-soft: var(--ls-success) / 0.14;
--ls-warning-soft: var(--ls-warning) / 0.16;
--ls-destructive-soft: var(--ls-destructive) / 0.12;
--ls-info-soft: var(--ls-info) / 0.14;
/* 环境光（页面底部渐变网格，让玻璃"有东西可折射"） */
--ls-ambient-a: var(--ls-primary) / 0.10;
--ls-ambient-b: 173 80% 40% / 0.08;
--ls-ambient-c: 274 48% 47% / 0.06;
```

**角色与个性化配色**（统一挂载点）：

```css
.role-teacher { --ls-primary: 175 77% 26%; --ls-ring: var(--ls-primary); --ls-accent: 168 60% 94%; --ls-accent-foreground: 175 70% 22%; }
[data-ui-palette="sky"|"mint"|"violet"|"rose"] { /* 沿用 user_ui_preferences.css 的 5 套值，只覆盖 primary/accent/background/border */ }
```

`frontend-redesign-2026-08.md` 声称的全局 `.role-teacher` 覆盖**实际不存在**（只有 `.app-topbar.role-teacher` 与 `.classroom-page.role-teacher` 局部覆盖），本方案在 P1 补上全局覆盖，并删除局部覆盖。

### 4.2 玻璃材质令牌（六旋钮法，亮/暗/tinted/off 四套值）

```css
:root {
  --ls-glass-blur: 16px;                     /* Regular */
  --ls-glass-saturate: 170%;
  --ls-glass-fill: 0 0% 100% / 0.58;         /* HSL 通道 + alpha */
  --ls-glass-fill-strong: 0 0% 100% / 0.78;  /* 大面积（侧栏、厚玻璃）*/
  --ls-glass-line: 0 0% 100% / 0.85;         /* 描边 */
  --ls-glass-rim: 0 0% 100% / 0.95;          /* 顶部镜面高光 */
  --ls-glass-rim-bottom: 0 0% 100% / 0.35;
  --ls-glass-sheen: linear-gradient(135deg, hsl(0 0% 100% / .55) 0%, hsl(0 0% 100% / .08) 38%, transparent 60%, hsl(var(--ls-primary) / .05) 100%);
  --ls-glass-shadow: 0 20px 60px hsl(215 43% 32% / .10), 0 2px 6px hsl(215 43% 32% / .05);
  --ls-glass-shadow-strong: 0 24px 70px hsl(215 43% 32% / .18), 0 4px 10px hsl(215 43% 32% / .08);
  --ls-glass-ink: 216 45% 16%;      /* 现 #16243a */
  --ls-glass-muted: 215 18% 43%;    /* 现 #5b6b82 */
  --ls-scrim: 222 47% 11% / 0.28;   /* 弹层压暗层 */
}
```

模糊阶梯（P2.8 圆角同款收敛，全站 17 档 → 4 档）：

| 令牌 | 值 | 用在 |
|---|---|---|
| `--ls-blur-thin` | 8px | chip、pill、小浮标、tooltip、玻璃按钮 |
| `--ls-blur-regular` | 16px | 顶栏、侧栏、Dock、工具条、popover、菜单 |
| `--ls-blur-thick` | 24px | sheet、modal、灯箱面板、AI 浮窗 |
| `--ls-blur-scrim` | 8px | 弹层背后压暗层（不是 40，性能） |

`saturate` 固定 170%（亮）/ 140%（暗）。禁止出现其他 blur 值（lint）。

### 4.3 圆角（同心制）

| 令牌 | 值 | 用在 |
|---|---|---|
| `--ls-r-capsule` | 999px | 按钮、chip、开关、搜索框、Dock、进度条 |
| `--ls-r-xs` | 6px | 复选框、小标签、代码块内元素 |
| `--ls-r-sm` | 10px | 输入框、下拉项、表格容器、侧栏项 |
| `--ls-r-md` | 14px | 小卡、列表项、popover、气泡 |
| `--ls-r-lg` | 20px | 卡片、面板、菜单 |
| `--ls-r-xl` | 28px | sheet、modal、AI 浮窗、灯箱舞台 |
| `--ls-r-2xl` | 36px | 全屏壳、登录卡、底部 sheet 顶角 |
| `--lq-r-outer` / `--lq-pad` / `--lq-r-inner` | 容器自定义属性 | `.lq-card { --lq-r-outer: var(--ls-r-lg); --lq-pad: 16px; }`；贴边子元素 `border-radius: var(--lq-r-inner)` = `max(var(--ls-r-xs), calc(var(--lq-r-outer) - var(--lq-pad)))`；**不贴边的子元素用比父小两档**（如卡片内图片 `--ls-r-md`） |

旧令牌 `--radius-sm/md/lg/xl/2xl/full` 映射：sm→`--ls-r-sm`、md→`--ls-r-md`、lg→`--ls-r-lg`、xl→`--ls-r-xl`、2xl→`--ls-r-2xl`、full→capsule（P1 用别名保留，P10 删）。

### 4.4 阴影（贴纸面 + 玻璃两族）

```css
--ls-shadow-1: 0 1px 2px hsl(222 47% 11% / .06), 0 1px 3px hsl(222 47% 11% / .04);   /* 霜面卡片静止 */
--ls-shadow-2: 0 4px 14px hsl(222 47% 11% / .08);                                   /* 霜面 hover */
--ls-shadow-3: 0 12px 32px -12px hsl(222 47% 11% / .22);                            /* popover/菜单 */
--ls-shadow-4: 0 24px 64px -16px hsl(222 47% 11% / .30);                            /* sheet/modal */
--ls-shadow-focus: 0 0 0 4px hsl(var(--ls-ring) / .18);
```

玻璃族用 §4.2 的 `--ls-glass-shadow*`；**任何组件不得写自己的 box-shadow 字面量**。

### 4.5 字体与字号

```css
--ls-font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Noto Sans CJK SC", system-ui, sans-serif;
--ls-font-mono: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace;
--ls-font-serif-cjk: "Songti SC", SimSun, serif;  /* 仅试卷/公文渲染 */
```

字号阶梯（映射 Apple Dynamic Type，桌面 CJK 校准；全部 `rem`，根 16px）：

| 令牌 | 桌面 | 移动(≤640) | 行高 | 字重 | 对应 Apple | 用在 |
|---|---|---|---|---|---|---|
| `--ls-t-display` | 32px | 28px | 1.2 | 700 | Large Title | 首页/登录唯一大标题 |
| `--ls-t-title1` | 24px | 22px | 1.25 | 700 | Title 1 | 页头 h1 |
| `--ls-t-title2` | 20px | 19px | 1.3 | 650 | Title 2 | 区块标题 |
| `--ls-t-title3` | 17px | 17px | 1.35 | 650 | Title 3 | 卡片标题、弹层标题 |
| `--ls-t-headline` | 15px | 16px | 1.45 | 600 | Headline | 列表主文字、按钮 lg |
| `--ls-t-body` | 15px | 16px | 1.6 | 400 | Body | 正文 |
| `--ls-t-callout` | 14px | 15px | 1.5 | 500 | Callout | 按钮 md、表格单元、输入框 |
| `--ls-t-sub` | 13px | 14px | 1.45 | 500 | Subheadline | 次级信息、chip |
| `--ls-t-footnote` | 12px | 13px | 1.4 | 500 | Footnote | 时间戳、表头、帮助文字 |
| `--ls-t-caption` | 11px | 12px | 1.35 | 600 | Caption | 图例、Dock 文字、计数徽章 |

`letter-spacing: 0`（中文）；数字一律 `font-variant-numeric: tabular-nums`。旧 `--text-2xs/xs/sm/md/rg` 别名映射到 caption/footnote/sub/callout/body（P1 保留、P10 删）。

### 4.6 间距

4px 基准：`--ls-s-1: 4px, -2: 8, -3: 12, -4: 16, -5: 20, -6: 24, -8: 32, -10: 40, -12: 48, -16: 64`。组件内边距只用这些；页面栏距 `--ls-gutter: clamp(16px, 3vw, 32px)`；内容最大宽 `--ls-content-max: 1280px`（阅读页 42rem）。

### 4.7 z 轴（8 档，全部字面量迁入）

| 令牌 | 值 | 占用 |
|---|---|---|
| `--ls-z-raised` | 10 | 卡片 hover、sticky 表头 |
| `--ls-z-nav` | 100 | 顶栏、侧栏、Dock、FAB |
| `--ls-z-popover` | 1100 | popover、菜单、tooltip、日期选择器 |
| `--ls-z-drawer` | 1200 | 抽屉、侧 sheet |
| `--ls-z-modal` | 1300 | modal、底部 sheet、confirm |
| `--ls-z-viewer` | 1400 | 全屏阅读/灯箱/白板全屏 |
| `--ls-z-toast` | 1500 | toast |
| `--ls-z-explain` | 1600 | 说明浮窗（不再用 2147483000） |

### 4.8 动效与弹簧

```css
--ls-dur-fast: 120ms;  --ls-dur-base: 180ms;  --ls-dur-slow: 280ms;  --ls-dur-enter: 400ms;
--ls-ease-out: cubic-bezier(.2,.7,.3,1);
--ls-ease-in-out: cubic-bezier(.4,0,.2,1);
/* 用 https://www.kvin.me/css-springs 按 (perceptual duration, bounce) 生成完整 linear() 串后填入；下面是参数，不是最终串 */
--ls-spring-snappy: linear(/* 350ms, bounce 0% */);   /* 按钮按压、分段 thumb、tab 指示器 */
--ls-spring-soft:   linear(/* 500ms, bounce 20% */);  /* 弹层出入、Dock 变形、顶栏收缩 */
--ls-spring-bouncy: linear(/* 700ms, bounce 35% */);  /* 仅装饰：灯箱、成就揭晓 */
--ls-spring-snappy-dur: 350ms; --ls-spring-soft-dur: 500ms; --ls-spring-bouncy-dur: 700ms;
```

动效契约（承接 ux-overhaul D1–D3 并扩展）：

| 场景 | 动效 | 时长/缓动 |
|---|---|---|
| 列表/卡片进入 | opacity 0→1 + translateY 6px→0 | 180ms ease-out；stagger 40ms/项、≤10 项、总 ≤400ms |
| 内容切换（tab/筛选） | cross-fade | 120ms |
| 按钮按压 | scale .97 + 填充加深 | snappy（按下即时，松开回弹） |
| popover/菜单 | opacity + scale .96→1，transform-origin 指向触发器 | soft（reduced-motion: 100ms opacity） |
| sheet（底部/侧） | translate 100%→0 | soft |
| modal | opacity + scale .98→1 | 220ms ease-out |
| Dock 变形（合并/分裂） | View Transition；退回 opacity | soft |
| 计数徽章变化 | scale 1→1.15→1 一次 | 300ms |
| 页面导航 | `@view-transition{navigation:auto}`，顶栏/侧栏/Dock 带 `view-transition-name` | 浏览器默认 250ms |
| reduced-motion | 全部 `transition-duration: 1ms`，禁 scale/translate，保留 opacity | — |

### 4.9 断点（5 档，替换 10 种近重复值）

| 令牌 | 值 | 布局 |
|---|---|---|
| `--bp-sm` | 640px | 手机：单列，底部 Dock，侧栏变 sheet |
| `--bp-md` | 768px | 平板竖：单列+顶栏，侧栏折叠成图标栏 |
| `--bp-lg` | 1024px | 平板横/小笔记本：侧栏图标栏，双列 |
| `--bp-xl` | 1280px | 桌面：完整侧栏 + 主内容 + 右栏 |
| `--bp-2xl` | 1536px | 宽屏：内容居中限宽 |

CSS 统一写 `@media (max-width: 640px)`（带一个空格，lint 统一），JS 用 `LQ.mq.sm/md/lg/xl`。

### 4.10 图标

统一 **lucide** 几何：24 视口、`stroke-width 1.75`、`stroke-linecap/linejoin round`、`currentColor`。尺寸令牌 `--ls-icon-sm 16 / -md 20 / -lg 24`。Jinja 用一个宏 `lq_icon(name, size='md')`（合并 `app_topbar_icon` 与 `manage_icon` 两套注册表到 `templates/macros/lq/icons.html`，SVG 由 `tools/ui/build_icon_registry.py` 从 `node_modules/lucide-static` 生成，禁止手贴）；JS 用 `LQ.icon(name)`；React 直接 `lucide-react`。313 个手贴 SVG 在各页迁移时替换。

### 4.11 主题与偏好挂载点

```html
<html data-theme="lanshare" data-appearance="light|dark|auto" data-lq-glass="tinted|clear|off">
<body class="role-teacher|role-student" data-ui-palette="indigo|sky|mint|violet|rose">
```

- `data-appearance="auto"` 时用 `@media (prefers-color-scheme: dark)` 切深色令牌；`dark` 强制。存储在 `user_ui_preferences`（扩展现有表加 `appearance`、`glass` 两列，沿用其 CAS 版本机制与 SSR 首屏属性输出），未登录用 localStorage。
- `data-lq-glass="off"` = Reduce Transparency：`--ls-glass-blur: 0`，`--ls-glass-fill` alpha→0.96。
- 自动映射：`prefers-reduced-transparency: reduce` → off；`prefers-contrast: more` → fill 0.92 + 描边 `hsl(var(--ls-ink)/.6)` 2px；`forced-colors: active` → 去掉所有 backdrop-filter/阴影，边框 `CanvasText`。

深色令牌值（`[data-appearance="dark"]` 与 `[data-appearance="auto"]` + media 两处）：

```css
--ls-background: 224 28% 8%;  --ls-foreground: 214 32% 94%;
--ls-card: 224 22% 12%;  --ls-surface-1: 224 22% 12%;  --ls-surface-2: 224 20% 16%;
--ls-ink: 214 32% 94%;  --ls-ink-2: 215 20% 75%;  --ls-ink-3: 215 16% 58%;
--ls-line: 220 14% 22%;  --ls-line-strong: 220 12% 32%;
--ls-primary: 239 84% 72%;  --ls-primary-foreground: 224 28% 8%;
--ls-glass-fill: 224 30% 12% / 0.62;  --ls-glass-fill-strong: 224 30% 12% / 0.80;
--ls-glass-line: 0 0% 100% / 0.14;  --ls-glass-rim: 0 0% 100% / 0.18;  --ls-glass-rim-bottom: 0 0% 100% / 0.06;
--ls-glass-saturate: 140%;
--ls-glass-shadow: 0 24px 60px hsl(0 0% 0% / .45), 0 2px 6px hsl(0 0% 0% / .3);
--ls-glass-ink: 214 32% 94%;  --ls-glass-muted: 215 16% 68%;
--ls-scrim: 0 0% 0% / 0.5;
--ls-ambient-a: var(--ls-primary) / 0.14;  --ls-ambient-b: 173 80% 40% / 0.10;  --ls-ambient-c: 274 48% 47% / 0.08;
```

深色下 `.lq-glass` 的 `backdrop-filter` 追加 `brightness(1.05)` 抵消发灰。

---

## 5. 材质体系与分层规则

### 5.1 四种材质类（唯一允许的表面类）

| 类 | 材质 | 关键值 | 允许用在 |
|---|---|---|---|
| `lq-glass` | **Regular 玻璃** | `background: hsl(var(--ls-glass-fill)); backdrop-filter: blur(var(--ls-blur-regular)) saturate(var(--ls-glass-saturate)); border: 1px solid hsl(var(--ls-glass-line)); box-shadow: var(--ls-glass-shadow), inset 0 1px 0 hsl(var(--ls-glass-rim)), inset 0 -1px 0 hsl(var(--ls-glass-rim-bottom)); isolation: isolate; contain: paint;` + `::before` 镜面 sheen（`--ls-glass-sheen`，`pointer-events:none`，`mix-blend-mode: screen`）；直接子元素 `position: relative` 压在 sheen 上 | 顶栏、侧栏、Dock、工具条、FAB、popover、菜单、sheet、modal、浮窗 |
| `lq-glass--thick` | 厚玻璃 | blur → `--ls-blur-thick`，fill → strong | sheet、modal、drawer、AI 浮窗、灯箱面板 |
| `lq-glass--clear` | **Clear 玻璃** | fill alpha 0.22，必须与 `lq-scrim` 同用，文字强制 `hsl(0 0% 100%)` | 灯箱、登录背景图上的表单、学习文档全屏壳 |
| `lq-surface` | **霜面**（内容层） | `background: hsl(var(--ls-surface-1) / .86); border: 1px solid hsl(var(--ls-line) / .8); box-shadow: var(--ls-shadow-1); border-radius: var(--lq-r-outer, var(--ls-r-lg));` **无 backdrop-filter**（修饰 `lq-surface--frost` 加 `blur(var(--ls-blur-thin))`，仅允许在非滚动容器的首屏英雄卡与 3D 课表当前卡） | 卡片、面板、表格容器、表单区、列表、气泡 |

辅助：`lq-scrim`（弹层压暗：`background: hsl(var(--ls-scrim)); backdrop-filter: blur(var(--ls-blur-scrim))`）、`lq-ambient`（页面底：三个径向渐变 `--ls-ambient-a/b/c` + 可选 2 个 `filter: blur(70px)` 漂浮色团，`prefers-reduced-motion` 下静止；每页只有一个，挂在 `body::before`，`position: fixed; z-index: -1`）。

可选增强：`data-lq-refract`（deepika 折射）仅允许在 `.lq-login-card`、`.lq-dock`、`.lq-wb-toolbar` 三处；`navigator.hardwareConcurrency <= 2 || navigator.deviceMemory <= 4 || prefers-reduced-transparency || data-lq-glass=off` 时不初始化。

### 5.2 四层模型（每页必须能画出来）

```
L3 弹层     lq-scrim + lq-glass--thick   (modal / sheet / drawer / 灯箱)              z ≥ 1200
L2 导航控制 lq-glass                    (顶栏 / 侧栏 / Dock / 工具条 / FAB / popover) z 100–1100
L1 内容     lq-surface                  (卡片 / 表格 / 表单 / 列表 / 正文)             z 0–10
L0 环境     lq-ambient                  (渐变网格 + 色团)                              z -1
```

规则：L2 漂浮在 L1 之上（顶栏 sticky，内容从其下滚过并用 `lq-scroll-edge` 软溶解）；L1 之间用留白与分隔线分组，不用玻璃；L3 出现时 L2 不再计入"可见 blur 图层"（scrim 盖住）。一屏 blur 图层预算：L2 最多 2（顶栏 + Dock/侧栏）+ L3 1。

### 5.3 明暗自适应（tone）

小型玻璃控件（Dock、FAB、灯箱按钮、登录卡）支持 `data-lq-tone="dark"`：`--ls-glass-fill` 换成 `224 30% 12% / .55`、ink 换白。由 `LQ.tone.observe(el)` 采样其背后的背景（复用 `cultivation_identity.js` 的 `sampleImageTone`，仅在有背景图的页面启用；无背景图页面跟随主题）。侧栏/顶栏等大面积玻璃**不翻转**。

---

## 6. 组件库规范

约定：
- 每个组件给出 **DOM 契约**（类名 + 必需属性），三入口产出一致 DOM。
- 状态类统一：`is-active` / `is-selected` / `is-open` / `is-loading` / `is-disabled`（同时加 `disabled` 或 `aria-disabled`）/ `is-invalid` / `is-empty`。
- 尺寸修饰统一：`--sm` / `--md`（默认，可省）/ `--lg`。
- 色调修饰统一：`--tone-primary|success|warning|danger|info|neutral`。
- 交互态顺序：默认 → hover（仅 `@media (hover:hover)`）→ focus-visible → active → disabled。
- 文件：CSS 在 `static/css/lq/components/<component>.css`，Jinja 宏在 `templates/macros/lq/<component>.html`，JS 在 `static/js/lq/<component>.js`（挂到 `window.LQ`），React 在 `frontend/src/components/lq/<Component>.tsx`。

### 6.1 按钮 `lq-btn`

**用途**：一切可点击的命令。纯文字导航用 `lq-link`，不用按钮。

**DOM**：
```html
<button type="button" class="lq-btn lq-btn--prominent lq-btn--md" data-lq="btn">
  <span class="lq-btn__icon">{svg 18}</span>
  <span class="lq-btn__label">开设课堂</span>
  <span class="lq-btn__badge">3</span>   <!-- 可选 -->
</button>
```
`<a>` 同类名（导航型命令）；图标按钮加 `lq-btn--icon` + `aria-label`。

**变体（6 种）**：

| 变体 | 材质/填充 | 文字色 | 描边 | 阴影 | 用途 | 每视图上限 |
|---|---|---|---|---|---|---|
| `--prominent` | `hsl(var(--ls-primary))` 实色 tint + 顶部高光 `inset 0 1px 0 hsl(0 0% 100%/.35)` | `--ls-primary-foreground` | 无 | `0 6px 18px -8px hsl(var(--ls-primary)/.55)` | 唯一主操作（提交、保存、开始） | 1 |
| `--glass`（默认） | `lq-glass` 规则但 blur = thin | `hsl(var(--ls-glass-ink))` | `hsl(var(--ls-glass-line))` | `0 4px 18px hsl(215 43% 32%/.08), inset 0 1px 0 hsl(0 0% 100%)` | 工具条/顶栏/浮层上的普通操作 | 不限 |
| `--soft` | `hsl(var(--ls-primary-soft))`（tint 软填充，随 tone 换色） | `hsl(var(--ls-primary))` | 无 | 无 | 内容区里的次级操作（"查看详情"、筛选、取消式主流程） | 不限 |
| `--ghost` | 透明，hover `hsl(var(--ls-ink)/.06)` | `--ls-ink-2` | 无 | 无 | 取消、更多、关闭 | 不限 |
| `--destructive` | `--soft` 规则但 tone=danger；确认弹层内主按钮用实色 `hsl(var(--ls-destructive))` | danger | 无 | 无 | 删除、撤回 | 1 |
| `--link` | 无 | `--ls-primary`，hover 下划线 | 无 | 无 | 行内文字命令 | 不限 |

**尺寸**：

| 尺寸 | 高度 | 内边距 | 字号/字重 | 图标 | 圆角 | 触控（pointer:coarse） |
|---|---|---|---|---|---|---|
| `--sm` | 32px | 0 12px | 13px / 600 | 16 | capsule | 高 36 |
| `--md` | 40px | 0 16px | 14px / 600 | 18 | capsule | 高 44 |
| `--lg` | 48px | 0 22px | 15px / 600 | 20 | capsule | 高 48 |
| `--icon` | 与高度相等的正方形 | 0 | — | 20 | capsule | ≥44 |

**形态**：全部胶囊；图标与文字间距 6px；`min-width: 64px`（icon 除外）；文字不换行、`text-overflow: ellipsis`；标签 ≤4 汉字。

**状态与交互**：
- hover：`--glass` fill alpha +0.12、`translateY(-1px)`；`--prominent` `filter: brightness(1.06)`；`--soft` alpha .12→.18；`--ghost` 出现浅底。
- active：`transform: scale(.97)`，用 `--ls-spring-snappy`；`--prominent` 亮度 .96。
- focus-visible：`outline: 2px solid hsl(var(--ls-ring)); outline-offset: 2px`（实色，不用阴影）。
- disabled：`opacity: .45; pointer-events: none`（不改色相）。
- loading：`is-loading` → 文字 `visibility:hidden`，中央 `lq-spinner--sm`，`aria-busy="true"`，宽度锁定（JS 读 `offsetWidth` 写入 `style.minWidth`，唯一允许的内联样式）。
- 按钮组：`lq-btn-group`（胶囊分段：内部按钮去圆角，首尾保留，1px 描边共享）。

**接入**：
- Jinja：`{{ lq_btn('开设课堂', variant='prominent', size='md', icon='plus', href=None, id=None, attrs='', badge=None, loading=False) }}`
- JS：`LQ.btn({label, variant, size, icon, onClick, badge})` → `HTMLButtonElement`；模板字符串场景用 `LQ.html.btn({...})` 返回字符串（**替换 JS 里 520 处 `btn btn-sm …`**）。
- React：`<LqButton variant="prominent" size="md" icon={Plus}>开设课堂</LqButton>`（cva，类名输出与宏一致；替换未使用的 shadcn `button.tsx`）。

**禁忌**：不许在非玻璃容器里用 `--glass` 变体（内容区用 `--soft`）；不许自定义按钮颜色；不许 `<div onclick>` 当按钮；不许两个 prominent 并排。

### 6.2 芯片/胶囊标签 `lq-chip`

**用途**：筛选（可选中）、状态标识（只读）、可删除标签。三种 `--filter` / `--status` / `--tag`。

**DOM**：`<button class="lq-chip lq-chip--filter is-active" aria-pressed="true"><span class="lq-chip__dot"></span><span class="lq-chip__label">进行中</span><span class="lq-chip__count">12</span></button>`；状态型用 `<span role="status">`；可删用 `<span class="lq-chip lq-chip--tag">标签<button class="lq-chip__remove" aria-label="移除">×</button></span>`。

**尺寸**：高 28px（`--sm` 24）；内边距 0 12px；字号 13px/600；圆角 capsule；`__count` 18px 圆 `hsl(var(--ls-ink)/.10)` 11px/700 tabular。

**颜色**：
- filter 默认：`hsl(var(--ls-surface-1)/.7)` + 描边 `hsl(var(--ls-line))`；hover 描边 strong；**选中 = tint**：`hsl(var(--ls-primary)/.14)` 底 + `hsl(var(--ls-primary))` 字 + 描边 `hsl(var(--ls-primary)/.35)`（**不用实色填充**）。
- status 六色：`--tone-*` 各用 soft 底 + 深色字（success 用 `--success-dark` 等已有深档）；`__dot` 8px 实色。
- 在 `lq-glass` 上（顶栏/Dock 内）自动切为玻璃 chip（fill `hsl(0 0% 100%/.5)`，blur thin）。

**交互**：filter 用 `aria-pressed`；一组用 `lq-chip-row`（横向、`overflow-x:auto`、`scroll-snap-type: x proximity`、移动端不换行、`mask-image: linear-gradient(90deg,#000 92%,transparent)` 渐隐）；选中切换 120ms cross-fade；多于 8 个时后面的收进"更多 (N)"chip（承接 ux-overhaul A1）。

**接入**：`lq_chip(label, kind='filter', tone='', active=False, count=None, value='', attrs='')`；`LQ.chip()` / `LQ.html.chip()`；`<LqChip>`。现有 `manage_filter_chips.js` 的 `data-filter-chips` 代理 `<select>` 机制原样保留，只换类名。

### 6.3 分段控件 `lq-segment`

**用途**：2–5 个互斥视图切换（列表/日程/3D课表、原稿/优化稿、研讨室/一对一）。替代所有"按钮当 tab"。

**DOM**：`<div class="lq-segment" role="tablist" aria-label="视图"><button role="tab" aria-selected="true" class="lq-segment__item">列表</button>…<span class="lq-segment__thumb" aria-hidden="true"></span></div>`

**规格**：容器高 36px（`--sm` 30），胶囊，底 `hsl(var(--ls-ink)/.06)`（玻璃上：`hsl(0 0% 100%/.25)`），内边距 3px；item 字号 13px/600、色 `--ls-ink-2`，选中色 `--ls-ink`；`__thumb` 白色（深色：`hsl(var(--ls-surface-2))`）胶囊 + `--ls-shadow-1`，用 `--ls-spring-snappy` 平移到选中项（JS 写 `--lq-thumb-x/--lq-thumb-w` 两个自定义属性，唯一内联）。键盘 ←/→ 切换。宽度等分（`--fit` 时按内容）。

### 6.4 标签页 `lq-tabs`

**用途**：同一区域 ≥3 个内容面板（课堂活动、消息中心分类、博客栏目）。与分段控件的区别：tabs 可滚动、可带徽章、下方有内容面板。

**DOM**：`<div class="lq-tabs" data-lq="tabs"><div class="lq-tabs__list" role="tablist">…<button role="tab" id aria-controls aria-selected class="lq-tabs__tab"><svg/><span>讨论</span><span class="lq-tabs__badge">3</span></button>…<span class="lq-tabs__indicator"></span></div><div class="lq-tabs__panels"><section role="tabpanel" hidden>…</section></div></div>`

**规格**：tab 高 40px，字号 14px/600，色 `--ls-ink-3`，选中 `--ls-primary`；指示器 2px 高、宽=文字宽、`--ls-primary`、spring-snappy 平移；tab 之间 20px；列表 `overflow-x:auto` + 渐隐遮罩；面板切换 120ms cross-fade；`lq-tabs--pill` 变体 = 放在玻璃上的胶囊 tabs（Dock 内用，指示器变成胶囊 thumb）；`--vertical` 变体用于个人资料分区。JS：`LQ.tabs(root)` 单一控制器（**替换 ≥9 套手写实现**），支持 `data-lq-tabs-persist="key"` 记忆、`hashchange` 联动。

### 6.5 导航壳组件

#### 6.5.1 顶栏 `lq-topbar`

`lq-glass` + `position: sticky; top: 0; z-index: var(--ls-z-nav)`；高 56px（移动 52）；左右内边距 `--ls-gutter`；三区 `__lead`（返回/品牌/面包屑）`__title`（当前页，17px/650，单行省略）`__actions`（≤3 个 `lq-btn--glass --sm` 或 icon + 1 个"更多"菜单 + 头像）。底部无边线，改用 `lq-scroll-edge`（`::after` 高 24px 的 `linear-gradient(hsl(var(--ls-background)/.9), transparent)`，只在 `data-scrolled` 时显示，JS 用 `IntersectionObserver` 哨兵）。向下滚动超过 80px 时 `is-condensed`（高 48，标题缩 15px），向上恢复——spring-soft。`view-transition-name: lq-topbar`。`--immersive` 变体：无站点导航，只有返回/标题/页内操作，可 `data-lq-autohide`。

#### 6.5.2 侧栏 `lq-sidebar`

`lq-glass`（fill strong，**不翻转 tone**），宽 264px；`--rail` 折叠态 72px 只留图标+tooltip；≤1024 变成左侧 `lq-drawer`。结构：`__brand`（40px 图标 + 名，可点回首页）→ `__search`（胶囊输入，⌘K）→ `__nav`（域手风琴：`lq-nav-group` + `lq-nav-item`）→ `__user`（头像卡 + 登出）。`lq-nav-item`：高 40px，圆角 `--ls-r-sm`，图标 20 + 文字 14px/500；hover `hsl(var(--ls-ink)/.06)`；**选中 = tint**（`hsl(var(--ls-primary)/.14)` 底 + 主色字 + 左侧 3px 胶囊指示条 spring-snappy 滑动，替代现在的渐变+inset+阴影三重）。域手风琴：只开一个（保留现契约），toggle 高 36px，标题 12px/600 `--ls-ink-3`。`view-transition-name: lq-sidebar`。

#### 6.5.3 底部 Dock `lq-dock`（移动端 tab bar + immersive 页工具条）

`lq-glass` 胶囊，`position: fixed; bottom: max(12px, env(safe-area-inset-bottom)); left:50%; transform: translateX(-50%)`；高 56（移动）/ 48（桌面）；内边距 6px；子项 `lq-dock__item`（icon 22 + 11px/600 文字，移动端图标上文字下）；选中 = 胶囊 thumb（同分段控件）；≤5 项，多的进"更多"sheet；软键盘弹出时 `is-hidden`（translateY 120%）。支持 `data-lq-refract`。**替换 `partials/app_bottomnav.html`** 与课堂页活动 dock 的 tab 条。`view-transition-name: lq-dock`。

#### 6.5.4 悬浮按钮 `lq-fab`

56px 圆 `lq-glass`，图标 24；`--prominent` 变体主色；`--sm` 40px；固定右下 `bottom: calc(Dock 高 + 16px)`；展开菜单 = `lq-menu` 从 FAB 原地 morph（View Transition）。用于：AI 助手、白板入口、返回顶部（`--sm`、滚动 >600px 才出现、300ms 淡入）。多个 FAB 垂直堆叠 12px 间距，最多 3 个。

#### 6.5.5 面包屑 `lq-crumbs` / 步骤条 `lq-steps`

面包屑：13px/500，分隔符 lucide `chevron-right` 14；最后一项 `--ls-ink` 600；移动端只显示上一级（"‹ 上一级"）。步骤条（成绩链、开课向导、管理向导）：胶囊节点 28px，连线 2px，完成态 success，当前态 tint 主色+spring 脉冲一次，未来态 `--ls-line-strong`；标签 12px；≤640 纵向。

### 6.6 卡片 `lq-card`（内容层）

**用途**：重复项目的容器（课堂卡、任务卡、统计卡、材料卡）。**不是页面分区**——分区用留白与 `lq-section` 标题，不套卡。

**DOM**：
```html
<article class="lq-card lq-card--interactive" data-lq="card" tabindex="0">
  <header class="lq-card__head"><h3 class="lq-card__title">…</h3><div class="lq-card__meta">…chips…</div><div class="lq-card__actions">…</div></header>
  <div class="lq-card__body">…</div>
  <footer class="lq-card__foot">…</footer>
</article>
```

**规格**：`lq-surface`；`--lq-r-outer: var(--ls-r-lg)` 20px；`--lq-pad: 16px`（`--lg` 24）；标题 17px/650；meta 13px；`__actions` 右上，hover 才显示（`opacity` 0→1；`pointer:coarse` 常显）。

**变体**：`--interactive`（整卡可点：hover `translateY(-2px)` + `--ls-shadow-2` + 描边 strong；active scale .995；focus-visible 实色环；内部不许再有其他链接，附加操作进 `__actions` 并 `stopPropagation`）；`--flat`（无阴影、无 hover，列表 >12 项强制）；`--stat`（统计卡：数字 28px/700 tabular + 标签 13px + 可选 `lq-ring`）；`--hero`（首屏英雄卡，允许 `lq-surface--frost`，每页最多 1 个）；`--empty`（空态收缩为单行 48px，见 6.13）。

**禁忌**：卡套卡；卡内出现 `lq-glass`；卡片当分区背景。

### 6.7 列表行 `lq-row` 与列表 `lq-list`

用于消息、待办、成员、文件等纵向列表。行高 ≥56px；结构 `__lead`（头像/图标 36）`__main`（主 15px/500 + 副 13px `--ls-ink-3`）`__trail`（chip/时间/chevron）；分隔线 `hsl(var(--ls-line)/.7)` 1px 从 `__main` 起始；hover 底 `hsl(var(--ls-ink)/.04)`；选中 tint；未读 = 左侧 3px 主色胶囊 + 主文字 600；可滑动操作（移动）用 `lq-row--swipe` 露出 `lq-btn--destructive`。列表容器 `lq-list` = `lq-surface` 圆角 20 内 `overflow: clip`，行不再单独圆角。分组标题 `lq-list__group` 12px/600 `--ls-ink-3` sticky。

### 6.8 表格 `lq-table`

容器 `lq-surface`（圆角 `--ls-r-lg`，`overflow: clip`）；`thead` sticky（`top: 0`，**hard scroll edge**：`background: hsl(var(--ls-surface-1))` 不透明 + 底部 1px line）；th 12px/600 `--ls-ink-3`；td 14px、行高 48（`--dense` 40）；去斑马纹，改 hover 底；数字列右对齐 tabular；操作列 `lq-btn--ghost --sm`/icon；选中行 tint；空表用 6.13 空态代替 `.table-empty`；排序图标 lucide `arrow-up-down` 14；分页 `lq-pager`（胶囊，页码 32px）；≤768 转卡片列表（`data-label` 显示列名）。

### 6.9 表单控件

**通用**：标签在上（13px/600 `--ls-ink-2`，必填后缀主色 `*`）；帮助文字 12px `--ls-ink-3`；错误 12px danger + 图标，`aria-describedby`；字段间距 16；同排字段 `lq-field-row` gap 12。

| 控件 | 类 | 高度 | 圆角 | 底/描边 | 焦点 | 备注 |
|---|---|---|---|---|---|---|
| 文本输入 | `lq-input` | 40（lg 48，sm 32） | `--ls-r-sm` 10 | `hsl(var(--ls-surface-1)/.9)` / `hsl(var(--ls-line))` | 描边 `hsl(var(--ls-ring))` 1.5px + `--ls-shadow-focus` | 字号 14；前后缀槽 `__prefix/__suffix`；清除按钮 |
| 搜索 | `lq-input--search` | 36 | capsule | 同上；玻璃上：fill `hsl(0 0% 100%/.45)` blur thin | 同上 | 左 lucide `search` 16；⌘K 徽记 |
| 文本域 | `lq-textarea` | min 96，自增 | 14 | 同 input | 同 | 右下字数 12px |
| 下拉 | `lq-select` | 40 | 10 | 同 input，右 chevron | 同 | 原生 `<select>` 保留为值载体（沿用日期选择器"原生输入留在 DOM"策略），弹出面板 = `lq-menu`；≥6 项才用 select，否则 chip |
| 开关 | `lq-switch` | 31×51（sm 24×40） | capsule | 关 `hsl(var(--ls-ink)/.18)`，开主色；thumb 27px 白 + `--ls-shadow-2` | 环 | spring-snappy；`role="switch" aria-checked` |
| 复选 | `lq-checkbox` | 20 | `--ls-r-xs` 6 | 描边 1.5 `--ls-line-strong`；选中主色实心 + 白勾 | 环 | 勾 stroke 2.5 从 0 长度画出 150ms |
| 单选 | `lq-radio` | 20 | 圆 | 同复选；选中内圆 10 | 环 | 一组 ≤4 时优先 `lq-segment` |
| 滑块 | `lq-slider` | 轨 4 | capsule | 轨 `--ls-ink/.12`，填主色；thumb 24 白 | 环 | 值气泡 `lq-tooltip` |
| 日期/时间 | `ls_date_picker.js` 面板改 `lq-popover` 皮 | — | 20 | 玻璃 | — | 逻辑不动，只换类；移动端底部 sheet |
| 文件拖放 | `lq-dropzone` | min 120 | `--ls-r-lg` | 虚线 1.5 `--ls-line-strong`，拖入 tint 主色 | 环 | 图标 32 + "拖入或点击上传" + 限制说明 12px |
| 文件片 | `lq-file-chip` | 40 | 12 | `lq-surface` | — | 缩略图 32、名 13px、大小 11px、移除按钮；图片片接灯箱 `data-ls-lightbox` |

禁用：`opacity .5`；只读：底透明、无描边、下划线 dashed。表单区块 `lq-form-section`（标题 15px/650 + 描述）之间 32px。

### 6.10 弹层家族（五种，替代 5 套系统与 ~40 个 bespoke modal）

统一 JS 控制器 `LQ.layer`（在 `ui_popover.js` 的 `PopoverManager` 基础上扩展）：栈管理、焦点圈闭、Esc/外点关闭、滚动锁（`body.lq-scroll-locked`，用 `scrollbar-gutter: stable` 防跳）、`aria-modal`、返回焦点、`?open=` 深链。全部用原生 `<dialog>` 或 `popover` 属性（顶层渲染，解决 `position:fixed` 被 transform 祖先劫持的老坑）+ `@starting-style` 出入场。

| 类型 | 类 | 材质 | 尺寸/位置 | 动效 | 用途 |
|---|---|---|---|---|---|
| 弹窗 | `lq-modal` | `lq-scrim` + `lq-glass--thick`，圆角 `--ls-r-xl` 28 | 宽 `min(560px, 100vw-32px)`（`--lg` 800、`--xl` 1080、`--full` 100vw-32）居中；≤640 变底部 sheet | scale .98→1 + opacity 220ms | 表单、详情、向导 |
| 底部/侧 sheet | `lq-sheet` (`--bottom`/`--right`) | 同上，顶角 `--ls-r-2xl` 36 / 左角 28 | bottom：高 auto max 92vh，顶部 36×5 拖柄；right：宽 `min(480px, 100vw)` | spring-soft translate | 移动端弹窗、筛选器、快速编辑 |
| 抽屉 | `lq-drawer` | `lq-glass--thick`，右侧 | 宽 `min(640px, 100vw)`，可 `--wide` 960 | translateX 280ms ease-out | 学生详情、课堂运营台详情、审批、成员工作区 |
| 浮层 | `lq-popover` | `lq-glass`，圆角 20 | 锚定触发器，自动四向+视口 12px 边距；宽 auto max 360 | scale .96→1 spring-soft，origin 指向锚点 | 说明浮窗、日期选择、颜色、议程详情、课程详情 |
| 菜单 | `lq-menu` | `lq-glass`，圆角 16 | 宽 min 200；项高 40，14px，图标 18；分组线；破坏项 danger | 同 popover | 顶栏"更多"、行操作、select 面板、FAB 展开 |
| 确认 | `lq-confirm`（= `lq-modal--sm` 特化） | 同 modal | 宽 360；标题 17/650 + 正文 14 + 双按钮（取消 ghost / 确认 prominent 或 destructive） | 同 modal | **替换 43 处 `window.confirm` 与 3 处 `alert()`**；`await LQ.confirm({title, body, confirmLabel, tone})` 返回 boolean |
| 提示 | `lq-tooltip` | `lq-glass` blur thin，圆角 8 | 12px/500，padding 6 10，延迟 400ms（键盘 focus 立即） | opacity 120ms | 仅图标按钮的名称；**功能说明一律 `data-explain`** |

弹层内部结构：`__head`（标题 17/650 左对齐 + 右上关闭 `lq-btn--ghost --icon`）→ `__body`（滚动区，padding 20/24）→ `__foot`（右对齐按钮，移动端等宽堆叠，主按钮在下）。头尾不加分隔线，滚动时 `__head` 出现 scroll-edge。嵌套弹层禁止（用步骤或替换内容）；popover 内允许 tooltip。

### 6.11 通知 `lq-toast`

唯一实现 `LQ.toast(message, {tone, duration=3000, action, icon})`（在 `ui.js` `showToast` 上重写，`window.showToast/showMessage` 保留为别名，**删除其余 10 个实现**）。容器 `#lq-toasts` 右上（移动端顶部居中，有 Dock 的页避让）；单条 `lq-glass` 圆角 16，高 ≥48，图标 20 tone 色，文字 14/500 `--ls-glass-ink` ≤60 字，可选一个 `lq-btn--link` 动作；最多 3 条堆叠（新在上，spring-soft 入，translateX 出）；hover 暂停计时；`role="status" aria-live="polite"`，danger 用 `assertive`；每条独立可关闭。**不用 toast 报错表单校验**（就地显示）。React 岛屿用 sonner 但样式映射同类名。

### 6.12 徽标/计数/头像/进度

- `lq-badge`：计数 18px 圆胶囊，11px/700，danger 或主色；`--dot` 8px；0 不渲染。
- `lq-avatar`：24/32/40/56 四档圆，无图时首字 + 由用户名 hash 选 6 色 soft 底；组合 `lq-avatar-stack`（重叠 −8px，最多 4 + "+N"）。
- `lq-progress`：轨 6px 胶囊 `--ls-ink/.10`，填 tone 色，`transition width 400ms ease-out`；`--ring`（沿用 `.insight-ring` SVG，stroke 3.5，86px，改类名）。
- `lq-spinner`：20/16/32 三档，2px 环 `--ls-ring` 圆弧 25%，800ms linear 旋转；`aria-label="加载中"`。**替换 9 种 spinner**。
- `lq-skeleton`：`hsl(var(--ls-ink)/.08)` 圆角同心，1.6s 流光 `hsl(0 0% 100%/.4)`；reduced-motion 静止。

### 6.13 空态 `lq-empty`

三档：`--inline`（一行 48px：图标 20 + 一句话 ≤16 字 + 可选 `lq-btn--soft --sm`，**零值默认档**）、`--card`（卡片内 120px：图标 32 + 标题 15/600 + 一句 13px + 一个按钮）、`--page`（整页：插画 120 + 标题 20 + 描述 + 主按钮，仅首次引导）。空态出现时父容器加 `is-empty` 收起 chrome（工具条、图例、统计条不渲染）。

### 6.14 页头 `lq-page-head`（升级 `page_head` 宏）

标题 24/700 左对齐；右侧 ≤2 个按钮（1 prominent + 1 soft）+ "更多"菜单；标题旁 `explain_button`；一句描述 14 `--ls-ink-3`（可省）；可选 `__aside` 插槽放 1–3 个 `lq-card--stat` 或 `lq-ring`；无眉题（`eyebrow` 参数删除）。移动端按钮换行且等宽。

### 6.15 图表基元 `lq-insight`

沿用 `.insight-*` 纯 CSS/SVG 工具集，改类名 `lq-ring / lq-bars / lq-meter`，色调改 `--tone-*` 令牌；容器 `lq-surface`；标题 12/600 `--ls-ink-3`（去大写与字距）；零数据不渲染（承接 B3）。监控大屏的 SVG 图表接同一套 stroke 令牌（§9.10）。

### 6.16 灯箱 `lq-lightbox`（现 `ls_image_lightbox.js`）

已是种子实现，只做：类名 `ls-lightbox*` → `lq-lightbox*`（保留 `data-ls-lightbox` 数据契约不变）、材质改 `lq-glass--clear` + `lq-scrim`、色团 `prefers-reduced-motion` 静止、z 改 `--ls-z-viewer`、按钮改 `lq-btn--glass --icon`。

### 6.17 说明浮窗 `ui_explanation.js`

逻辑不动；面板改 `lq-popover` 皮（去掉自有渐变），z 改 `--ls-z-explain`，触发器改 `lq-btn--ghost --icon --sm`。宏名与 `data-explain-*` 契约不变。

### 6.18 富文本 `lq-prose`

正文 15/1.7；标题阶梯同 §4.5；代码块 `lq-surface` 内层（`--ls-surface-2`）圆角 12、mono 13px、复制按钮 `lq-btn--ghost --icon --sm` 右上 hover 显示；行内代码 tint 底；表格套 `lq-table--dense`；图片接灯箱；引用块左 3px 主色胶囊。AI 对话、博客正文、学习文档、作业题干共用。

### 6.19 气泡 `lq-bubble`（聊天/私信/AI）

己方 `hsl(var(--ls-primary-soft))` 底 + `--ls-ink` 字（不用实色主色底，深色模式与个性化配色更稳），对方 `lq-surface-2`；圆角 18，同侧连续消息角收到 6；最大宽 72%（移动 85%）；时间 11px `--ls-ink-3` 悬停显示；引用块同心内层；图片接灯箱；贴纸无底。composer `lq-glass` 胶囊固定底部（输入自增 ≤6 行、表情/附件 icon 按钮、发送 `lq-btn--prominent --icon`）。

---

## 7. 壳与页面骨架

### 7.1 唯一 AppShell（P3）

新建 `templates/lq/app_shell.html`（取代 `base_navbar.html`、`manage/layout.html`、`resume/layout.html`、`base_centered.html` 四个壳；`classroom_main_v4.html` 改为在 AppShell 内用 `layout='immersive'`）。

```
<body class="role-* lq-app" data-lq-layout="sidebar|topbar|immersive|centered">
  body::before  lq-ambient
  <aside class="lq-sidebar">        (sidebar 布局；其余布局不渲染)
  <div class="lq-main">
    <header class="lq-topbar">      (所有布局；centered 为极简版)
    <main class="lq-content" id="main">
      {% block page_head %}{% block content %}
    </main>
  </div>
  <nav class="lq-dock">             (≤768 或 immersive 布局)
  <div id="lq-layers"> <div id="lq-toasts">
  统一脚本：auth / lq.core(layer,toast,confirm,tabs,segment,collapsible,tone,mq) / ui_explanation / ls_date_picker / lightbox / vite_islands
```

四种布局：
- `sidebar`：教师管理域、教务、我的域（现 `/manage/*`、profile 在壳内）。
- `topbar`：学生首页、消息、博客、成长页（现 `base_navbar`）。
- `immersive`：课堂主页、作答页、白板、学习文档壳、监控大屏——`lq-topbar--immersive` + Dock 承载页内 tab。
- `centered`：登录/错误/状态（现 `base_centered`）：`lq-ambient` + 背景图（可选）+ 单张 `lq-login-card`。

导航数据继续来自 `manage_nav_service.py`（注册表不动）；学生端顶栏项也注册进同一服务（新增 `student` 域）。`embedded_mode` 保留但只输出 `lq-content`（无壳）。

### 7.2 页面骨架模板（每页必须套用其一）

| 骨架 | 结构 | 例 |
|---|---|---|
| 列表页 | page_head → filter_bar（搜索 + ≤3 chips 组 + 更多筛选 sheet）→ lq-table 或 card grid → pager | 课程、班级、试卷、材料 |
| 总台页 | page_head(+aside stats) → 2–3 个 `lq-section`（标题 20/650 + 内容）→ 右栏（≥1280） | 首页、课堂运营台 |
| 详情页 | topbar 返回 + 标题；左主内容（lq-section 堆叠）+ 右 sticky 侧栏（摘要卡 + 操作） | 作业详情、学生详情 |
| 编辑器页 | immersive；顶栏含保存状态 chip + 主按钮；左导航/右属性面板可折叠 | 试卷编辑、教案、学习文档 |
| 作答页 | immersive；顶栏计时/进度/自动保存；左题目导航 sheet（移动）/rail（桌面）；主答题流 | 考试、作业 |
| 沉浸工作台 | immersive；主区 + 底部 Dock 切换活动面板 | 课堂主页 |
| 阅读页 | topbar；正文 42rem 居中 `lq-prose`；右侧浮动 TOC popover | 材料阅读、博客详情 |

栅格：`lq-grid` 12 列 gap 16（≤640 gap 12）；卡片网格 `lq-grid--cards` `repeat(auto-fill, minmax(280px, 1fr))`；总台右栏 `minmax(300px, 360px)`。区块间距 32（`lq-section` 之间），区块内元素 16。

### 7.3 响应式行为

| 宽度 | 侧栏 | 顶栏 | Dock | 弹窗 | 表格 |
|---|---|---|---|---|---|
| ≥1280 | 完整 | 完整 | 无（immersive 除外） | 居中 | 完整 |
| 1024–1279 | 图标栏 | 完整 | 无 | 居中 | 完整 |
| 768–1023 | drawer | 标题+更多 | 无 | 居中 | 横向滚动 |
| 640–767 | drawer | 精简 | 有 | 底部 sheet | 卡片化 |
| <640 | drawer | 精简 | 有 | 底部 sheet | 卡片化，chips 横滚 |

移动端手风琴 `data-mobile-collapse`（阶段 9 遗留，CSS 有钩子但无人设置）：改为 `lq-section--collapsible`，JS `LQ.collapsible` 统一，默认 <768 收起并记忆。

---

## 8. 文案、信息层级与图标

- **层级三档**：每屏一个焦点（page_head 或 hero），二级用 `lq-section` 标题，三级只用卡片标题；不再出现四级标题。
- **按钮文案**：动词开头 ≤4 字（"开设课堂""导出成绩""标记已读"）；否定动作用"取消"，破坏动作写明对象（"删除试卷"）；不用"确定/OK"。
- **空态文案**：一句话说"没有什么 + 下一步"（"暂无作业，先布置一份"），不解释系统原理。
- **说明**：任何 >20 字的解释进 `data-explain`；表单帮助文字 ≤20 字。
- **数字**：同屏只出现一次；0 不展示（承接）；时间用相对时（今天/明天/N 天后）+ hover 绝对时。
- **图标**：仅 lucide；导航项必须图标+文字；纯图标按钮必须 `aria-label` + tooltip；不用 emoji 做图标（聊天内容除外）。
- **色彩语义**：主色=行动/选中；success=完成；warning=临近/提醒（监考、截止）；danger=错误/破坏/未读风险；info=进度/中性提示；沿用 agenda 配色（监考 amber、考试 red、作业 violet、待办 sky、上课 teal）映射为 `--tone-proctor/exam/homework/todo/class` 令牌（由 `--ls-c-*` 派生）。

---

## 9. 特殊界面改造（同一美学、组件化）

每节给出：现状 → 目标 → 材质分层 → 组件拆分 → 验收。

### 9.1 3D 课表 `course_schedule_deck.js`

**现状**：318 行 CSS 注入在 JS 里，38 hex + 39 rgba，10 处内联 style，`COURSE_PALETTE` 10 个实色。
**目标**：
- CSS 抽到 `static/css/lq/components/schedule-deck.css`；`COURSE_PALETTE` 改为 `--tone-course-1…10` 令牌（`--ls-c-*` 派生），卡片用 **tint**：底 `hsl(var(--tone-course-N)/.16)`，左侧 4px 胶囊实色，文字 `--ls-ink`；深色自动跟随。
- 舞台 `lq-deck`：`perspective: 1500px` 保留；周卡片 `lq-deck__card` = `lq-surface`（**不是玻璃**——多张叠放 3D 卡若用 backdrop-filter 会同时 5–7 层，违反预算），圆角 `--ls-r-xl`，`--ls-shadow-3`，`inset 0 1px 0 hsl(0 0% 100%/.8)` 做玻璃质感但无 blur；仅**当前激活卡**加 `lq-surface--frost`（1 层 blur thin）。
- 头部（学期 select + 前后周按钮）改 `lq-segment`（上周/本周/下周）+ `lq-select`；展开态 `csd-expand-*` 改为 `lq-sheet--bottom`（移动）/ `lq-modal--lg`（桌面），课节列表用 `lq-list`，时段分区用 `lq-list__group`。
- 滚轮意图 `scheduleWheelIntent` 与 `.d.ts` 公共 API 不变；过渡用 `--ls-spring-soft`；reduced-motion 下取消 rotateY，只做 opacity。
- 小容器正面摘要保留（v3 结论）。
**验收**：`node --check`、单测不变；1440/390 截图；一屏 blur 图层 ≤1；hex 为 0。

**2026-09-19 补充（调停课预测投影落地后重审）**——提交 `b3818ff2`/`b24e16ca` 给课表加入了"教务同步 + 待审调课/停课/换教室投影"，模块现为 1365 行（其中 `DECK_CSS` ≈350 行）。重审结论与对本节的修订：

- **必须原样保留的契约**（迁移不得改动行为，e2e `tests/e2e/components/academic-schedule-deck.spec.ts` 7 项 + `course-schedule-deck.spec.ts` 9 项 + `frontend/src/lib/academic-schedule.test.ts` 为验收基线）：公开 API `goToWeek / focusLesson / showAdjustment / openExpanded / setOverview / getActiveWeekIndex / destroy`；纯函数 `scheduleWheelIntent / pendingScheduleChange / countScheduleLessons / scheduleLessonLanes / scheduleChangeLabel / courseAccentFor`；DOM 数据契约 `data-event-key`、`data-csd-change`、`data-cs-lanes`、`data-csd-feedback`、`data-csd-expand-*`；三个入口（教师首页、课时统计页、学生首页）只提供数据/权限/同步回调（设计 R9）；同一变更的所有投影引用同一真实 `session_id`（R5）；预测不计入正式课时（R4）。
- **投影卡的 `lq` 落法**：待审原卡 = `lq-card--flat` 外套 2px 虚线 `hsl(var(--tone-course-N))` 边框 + 4px 透明间隙（现 `.cs-lesson--pending` 结构保留，只换令牌）；拟安排卡 = **不透明 tint**：`background: linear-gradient(hsl(var(--tone-course-N) / .25), hsl(var(--tone-course-N) / .25)), hsl(var(--ls-surface-1))`（两层叠加代替 `color-mix()`，无兼容缺口），文字 `--ls-ink`；"调课待审 / 正在申请变更 / ↗ 新位置"标签 = `lq-chip--status --sm --tone-warning`，`aria-label` 与跳转语义不变；"已定位"高亮 = `lq-card` 的 `is-counterpart-focus` 状态用 `outline: 3px solid hsl(var(--ls-warning))` + 3s 自动消退（保留）。
- **展开态的对照说明**（现内联 `.cs-adjustment-details`）改为 `lq-popover` 挂在标签 chip 上：可关闭、`aria-expanded` 成对切换、Escape 先关 popover 再关整周对话框；不再把说明塞进格子里撑高。
- **迷你 3D 卡上的标签**：现 `.cs-lesson--mini .cs-adjustment-label` 字号 .58rem（≈9px）且是可点按钮，低于 §4.5 最小 11px 与 §14.4 触控 44px；改为**只渲染一个 8px 状态点 + `title`**，所有跳转/对照交互只在展开态提供（3D 缩略卡保持"点卡片放大"单一手势，避免与 `a.cs-lesson__main` 直接导航混用）。
- **反馈区**：现 `setOverview` 每次都向 `[data-csd-feedback]` 与 `[data-csd-expand-feedback]` 两个 `role=status` 写入"消息 · 最近同步 · N 项待审 · 警告"长串，且首页/学生页宿主也各自显示同一条 `overview.message`，同屏重复。改为：宿主只保留一处状态行（`lq-empty--inline` 样式的 `lq-status-line`），组件的两个 live region 只在**用户动作**（定位/跳转失败/切周）时 `announce` 一句，`setOverview` 不再自动播报；`aria-live="polite"` 且相同文本不重复写入。
- **同步按钮**：`[data-academic-schedule-sync]` 与"同步智慧课堂"并列 → 合并为一个 `lq-btn--glass --sm` + `lq-menu`（教务课表 / 智慧课堂两项），忙碌态 `is-loading`；结果用 `LQ.toast`。
- **新增硬编码色**（`#172554 #f59e0b #713f12 #fbbf24 #312e81 #fff` 与 `color-mix(... #fff 75%)`）随 `DECK_CSS` 抽出时一并令牌化：焦点环 → `--ls-ring`，定位高亮 → `--ls-warning`，对照标签底 → `--ls-warning-soft`。
- **重叠泳道**：`scheduleLessonLanes` 与 `data-cs-lanes` 宽度切分保留，泳道间隙 2px 改 `--ls-s-1`（4px），≤640 展开网格 `min-width` 1100px 横向滚动保留并加 §14.4 的 `overscroll-behavior: contain`。
- **基线缺陷（已于 2026-09-19 修复，未提交）**：`course-schedule-deck.spec.ts:67`"narrow screens"用例原本在改动前后均失败，根因是整周对话框打开后 Chromium 会在静止的光标下重派 `pointerover`，立刻弹出一张无人请求的悬停预览，首次 Escape 只关掉了它。现改为悬停预览只在对话框打开后出现真实位移（≥4px，以文档级最后指针位置为基准）才生效；对照说明 `.cs-adjustment-details` 改为可开可关、`aria-expanded` 成对切换并随预览关闭而收起。18 项组件 e2e、11 项首页课表 e2e、11 项单测全绿。

### 9.2 作业作答页 `assignment_detail_student.html`

**现状**：483 行内联 CSS + 759 行内联 JS；自动保存状态 chip 四色硬编码；React `assignment-submit-sync` 与页面双系统。
**目标**：
- 作答页骨架（immersive）：`lq-topbar--immersive`（返回 / 作业名 17 / 右：`lq-chip--status` 自动保存态（info=保存中、success=已保存、warning=离线/待重试）+ 倒计时 chip + `lq-btn--prominent`"提交"）。
- 题目流：每题一张 `lq-card`（标题 = 题号+分值 chip；题干 `lq-prose`；作答区 `lq-textarea`/`lq-radio`/`lq-checkbox`；附件区 `lq-dropzone` + `lq-file-chip` 网格 + 粘贴按钮 `lq-btn--soft --sm`）。
- 题目导航：桌面右侧 sticky `lq-card--flat` 内 `lq-nav-grid`（32px 方胶囊，已答 tint 主色、当前实色、未答描边）；≤1024 变 Dock 上的"题目"按钮打开 `lq-sheet--bottom`。
- 结果区 → `lq-section` + `lq-card--stat`（得分）+ 逐题反馈 `lq-list`。
- 内联 CSS 迁到 `static/css/lq/pages/assignment-student.css`；内联 JS 迁到 `static/js/assignment_student_page.js`（自动保存/草稿/sendBeacon 逻辑逐行搬，不改行为）；状态 chip 用 `LQ.chip`。
- 灯箱、哈希去重、粘贴路由（已完成）保持。
**验收**：`tests/test_submission_*` 与 e2e 不变；草稿恢复用例通过；页面无内联 `<style>`。

### 9.3 考试作答页 `exam_take.html`

**现状**：独立文档 4229 行（1489 CSS / 2513 JS），自带顶栏、侧栏、题目导航、白板嵌入。
**目标**（同 9.2 骨架，保留独立计时不受壳干扰）：
- `extends "lq/app_shell.html"` `layout='immersive'`，`data-lq-lock-nav`（顶栏隐藏站点导航，只留返回确认）。
- 顶栏右侧：计时 chip（warning <5 分钟脉冲一次 + `aria-live`）、进度 `lq-progress`（宽 120）、自动保存 chip、"交卷" `lq-btn--prominent`（走 `LQ.confirm`）。
- 左侧题目导航 `lq-exam-rail`（桌面 sticky 240px `lq-surface`；分节 `lq-list__group`；`lq-nav-grid`）；移动 Dock：题目 / 白板 / 交卷。
- 主区 `lq-paper`（42–56rem 居中 `lq-surface`，圆角 28，内题目 `lq-card--flat` 分隔线相隔）；分页脚 `lq-btn-group`（上一题/下一题）。
- 白板：`initExamDrawingWhiteboard` 不动，工具条改 `lq-wb-toolbar`（§9.5）。
- 1489 行 CSS 迁 `static/css/lq/pages/exam-take.css`；2513 行 JS 拆为 `static/js/exam_take/{timer,navigator,answers,attachments,submit}.js`（每个 ≤400 行）。
**验收**：交卷/计时/自动保存 e2e 全绿；Playwright 4× CPU 节流下题目切换无 >50ms long task。

### 9.4 教师批改页 `submission_detail.html` 与教师作业详情 `assignment_detail_teacher.html`

详情页骨架。顶栏：返回 / 学生名+作业名 / 右：上一份·下一份 `lq-btn-group`、"发布成绩" prominent、更多菜单（导出、撤回、重做申请）。左主列：信息卡（`lq-card` + `lq-chip--status`）、逐题 `lq-card`（学生答案 `lq-prose` + AI 建议折叠区 `lq-section--collapsible` + 评分 `lq-input` 数字 + 评语 `lq-textarea`）、附件预览双栏改 `lq-split`（左 `lq-list` 文件、右 `lq-viewer`，TOC 用 `lq-popover`）。右 sticky：总分 `lq-card--stat` + 快速跳题（`submission-jump-nav` 岛屿改用 `lq-nav-grid` 类名）。四个 `.assignment-more-dropdown` 合并为一个 `lq-menu`；`rubric/scoring/wrong-answer/export` 四个 modal 全部 `lq-modal`。`assignment_wrong_summary.html` 的错题聚类改 `lq-card` + `lq-bars`（四档色调柱图令牌化）。内联 CSS/JS 各自抽文件。

### 9.5 白板 `static/js/whiteboard/*`

**现状**：模块化良好、ARIA 完整、12 个测试；CSS 1480 行在 ui-system；`board.js` 在绘制时关掉 toolbar 的 backdrop-filter（性能先例）。
**目标**：
- 工具条 `lq-wb-toolbar` = `lq-glass` 胶囊浮条（桌面顶部居中，移动底部 Dock 位），四组 `lq-btn--glass --icon`（工具 / 颜色 chips / 历史 / 导出），选中工具 tint 胶囊 thumb；**保留 `is-drawing` 关闭 blur 的机制**（改为切换局部 `data-lq-glass="off"`）。
- 颜色/粗细 popover、橡皮、确认、保存菜单、历史面板、导出对话框 → `lq-popover` / `lq-menu` / `lq-confirm` / `lq-drawer` / `lq-modal`（通过 `whiteboard/popover.js` 垫片指向 `LQ.layer`，四处调用点不改）。
- `constants.js` 的 10 个 ink hex 改为 `--tone-ink-1…10` 令牌（画布内颜色用 `getComputedStyle` 读一次）。
- FAB 入口 `lq-fab`；全屏态 z `--ls-z-viewer`。
- 支持 `data-lq-refract`（三处之一）。
**验收**：12 个单测通过；绘制期间 DevTools 无 backdrop-filter 重绘；导出 PNG 不含 UI。

### 9.6 课堂主页 `classroom_main_v4.html`

**现状**：2347 行，自带顶栏+两套 `<details>` 菜单+5 tab 活动 dock+嵌套讨论 tab；13k 行 classroom.css；React dialog 叠在上面。
**目标**（沉浸工作台骨架）：
- 壳：AppShell `immersive`；`lq-topbar--immersive`（课堂名 + 课次 chip + 修为值 `lq-chip--status` + 右侧：签到 `lq-btn--glass`、更多 `lq-menu`（合并两个 `<details>` 菜单））。
- 主区从上到下 `lq-section`：课次导航（横向拖动卡片改 `lq-deck--row`，同 9.1 的 `lq-surface` 卡；单击开详情 `lq-drawer`）、学习进度（`lq-card--stat` ×3 + `lq-progress`）、任务区（`lq-tabs` 待处理/已提交/全部 + `lq-card` 列表，整卡可点）、材料区（`lq-list` 或网格，空态 inline）。
- 活动区：右栏（≥1280）`lq-surface` 面板 + 内 `lq-tabs`（讨论/互动/协作/投票/资源）；<1280 移到底部 **Dock**（`lq-dock` 五项）打开 `lq-sheet--bottom` 承载面板。嵌套的"研讨室/一对一"改 `lq-segment`。
- 聊天：`lq-bubble` + `lq-glass` composer；表情面板 `lq-popover`；`chat.js` 2842–2897 的内联样式贴纸/引用卡全部改类。
- 末尾 6 个 `.modal-backdrop`（材料详情、结课材料向导等）→ `lq-modal`/`lq-drawer`；成员工作区 `partials/classroom_members/workspace.html` 的 `learning-modal-backdrop` + iframe → `lq-drawer--wide` + 同文档渲染（P0 iframe 决策）。
- 292 行内联 CSS 删除；classroom.css 13k 行按区块拆到 `static/css/lq/pages/classroom-*.css` 并在迁移中删除无用段（目标 ≤4k 行）。
**验收**：27 项 v3 浏览器验收场景重跑通过；CLS ≤0.05；20 次开关 sheet 无监听增长（沿用 v3 断言）。

### 9.7 AI 对话浮窗 `ai_chat_component.js` 与 Agent 工作台 `ai_workspace_widget.js`

浮窗 = `lq-glass--thick` 圆角 28，头部 `__head`（标题 + 模型/思考强度 `lq-segment--sm` + 最小化/全屏/关闭 icon 按钮），消息流 `lq-bubble` + `lq-prose`，composer `lq-glass` 胶囊（附件、深度思考 `lq-switch--sm`、发送）。拖拽/缩放逻辑不动（常量保留）。**全屏态材质切 `lq-surface`**（背后无内容可折射，玻璃只增开销），布局改双栏（左会话列表 `lq-list`、右对话）。历史抽屉 `lq-drawer`；`notify()` 两处 → `LQ.toast`。FAB 用 `lq-fab--prominent`。ai_chat.css 870 行迁 `static/css/lq/components/ai-chat.css` 并瘦身。Markdown 归一化保持。

### 9.8 学期日历 `semester_calendar.js`

零内联、零 hex，最容易。面板容器改 `lq-surface`（去两层渐变）；周列头 sticky hard edge；学期归属带 tint；甘特条用 `--tone-*`；拖动说明进 explain；空态 inline；详情 `lq-popover`；新增待办表单 `lq-sheet--bottom`（移动）/`lq-popover`（桌面）。**注意**：保留 `overflow` 与 sticky 的既知坑（memory）——容器不许 `overflow:hidden`，用 `clip` 且只在非 sticky 轴上。

### 9.9 职业路线星图 `career_path_*.js` + `career_path.css`

深色沉浸页，是 **Clear/深色玻璃的天然场景**：页面 `data-appearance="dark"` 强制；星图 SVG 的 24 个 hex → CSS 自定义属性（`--career-node-*`，从 `--ls-c-*` 派生）；`.career-topbar` → `lq-topbar--immersive`（深色玻璃）；方向筛选 → `lq-chip-row`；详情 `aside.career-detail`、`section.career-prep` → `lq-glass--thick` 浮层（右侧 drawer 桌面 / 底部 sheet 移动）；`#career-tip` → `lq-tooltip`；测试问卷 → `lq-modal`；`career_path.css` 647 行改成 `lq/pages/career.css`（去自有 `--glass*`）。`?v=` 改 `asset_url`。

### 9.10 管理向导 `manage_workflow.js` 与监控大屏 `manage_system_monitor.js`

- **向导**：iframe 轮播（P0 决策 = **放弃 iframe**，改同文档渲染：阶段卡 `lq-steps` 横向 + 当前阶段内容通过 `fetch` 拉 `embedded_mode` HTML 片段注入 `lq-content`；保留惯性拖动）。阶段卡 `lq-card--interactive`，聚焦卡 `lq-card--hero`。
- **监控大屏**：保留独立深色（局部 `data-appearance="dark"`）；`COLORS` 6 色与模板 6 个图例 swatch → `--tone-monitor-*` 令牌；SVG 图表 stroke/fill 用 `var()`；进程树缩进用 `style="--depth:3"` + `padding-left: calc(var(--depth) * 16px)`（唯一允许的内联形式）；卡片 `lq-surface`（深色值）；顶部资源卡 `lq-card--stat`；AI 解读面板 `lq-drawer`。

### 9.11 登录与人生一言

`base_centered` → AppShell `centered`。`lq-login-card`：`lq-glass--clear`（背景图上）或 `lq-glass--thick`（无图），圆角 36，宽 420，内 `lq-input --lg`、`lq-btn--prominent --lg` 全宽、次级 `lq-btn--link`；`data-lq-tone` 由 `sampleImageTone` 决定；支持 `data-lq-refract`。人生一言舞台 `.life-tip-stage` 已是玻璃，改类名并接令牌；**52146 行"性能层：静态模糊一次成型"保留**——登录页背景用预烘焙模糊而非实时 `backdrop-filter`，只有卡片本体 1 层 blur。

### 9.12 博客 `blog.js` + `blog-paper.css`

产品决策：**正文保留纸感**（阅读体验优先，Apple 也不在内容层用玻璃），纸感 = `lq-surface--paper` 变体（把 `--blog-*` 令牌映射到 `--ls-*`）。玻璃只给：顶部 `lq-topbar`、阅读进度浮条、右侧浮动 pill、栏目 `lq-tabs`、用户 popover、举报/发布 `lq-modal`。删除 `ui-system.src.css:28625` 的旧 blog.css 段（双份）。`?post=` 深链契约不变。

### 9.13 学习文档 / HTML 包壳 / 材料阅读

- 阅读页 `material_viewer.html`：阅读页骨架；侧栏 TOC → 桌面 sticky `lq-surface` rail、移动 `lq-popover`/sheet；原稿/优化稿 `lq-segment`；AI 摘要面板 `lq-drawer`。
- HTML 包壳 `material_render_shell.js`：iframe **保留**（第三方 HTML 必须隔离，P0 决策），壳顶栏 `lq-topbar--immersive` 玻璃（iframe 内容不折射，顶栏靠 `lq-ambient` 与自身高光仍成立）；折叠后的 `☰` 改 `lq-fab--sm`；FAB 竖排停靠改 `lq-fab` 栈。
- LessonDoc 编辑器：`lessondoc_wizard.js` 45 处内联 style 全部改类（`lq-steps` + `lq-form-section`）；编辑器壳 `lq/pages/lessondoc-editor.css`；引擎 `static/lessondoc/2.0/*.css` 是课件产物皮肤，**不改**。

### 9.14 投票 / 分组 / 消息中心 / 个人资料

- 投票：`classroom_polls.js`/`manage_polls.js` 的 `openOverlay` → `lq-modal`；选项 `lq-radio/checkbox` 列表；结果柱 `lq-bars`；状态 chip。
- 分组：`collaboration.js` 14 个 render 改 `LQ.html.*` 输出；三种 overlay → `lq-modal`/`lq-sheet`；组卡 `lq-card`；拖拽分配大屏保持自有画布但工具条玻璃化；互评 modal → `lq-modal`。
- 消息中心：列表页骨架；分类 `lq-tabs`（>8 收纳）；会话 `lq-list` + `lq-bubble`；铃铛 toast → `LQ.toast`（删 JS/TS 双实现）；`message-center-sync.tsx` 的 toast 改调 `window.LQ.toast`。
- 个人资料：条件基类改为 AppShell `sidebar`（学生 `topbar`）；分区 `lq-tabs--vertical`；签名/安全/偏好各 `lq-form-section`；外观设置新增（亮/暗/自动 `lq-segment` + 玻璃强度 `lq-segment` + 配色 5 色 `lq-chip` 圆点）。

### 9.15 成长页族（learning_path / achievements / points_shop / report_card / wrong_book / feedback_review）与 resume 家族

六页各有私有前缀但同一 idiom（hero + 统计条 + 网格）：统一为总台页骨架：`lq-page-head`（aside 放 ≤3 个 `lq-card--stat`）+ `lq-grid--cards`。成就揭晓用 `--ls-spring-bouncy` 一次。删除六套私有 CSS。resume 8 页改 AppShell `sidebar`，`rz-*` 命名空间与 `resume_console.css` 全部退役。

---

## 10. 工程落地

### 10.1 目录

```
static/css/ui-system.src.css        # 只剩：@tailwind 三行 + @import "./lq/index.css" + 尚未迁移的旧段（逐段删）
static/css/lq/
  index.css                          # @import 顺序：tokens → base → materials → components/* → pages/*
  tokens.css   base.css   materials.css
  components/{button,chip,segment,tabs,topbar,sidebar,dock,fab,crumbs,card,list,table,form,layer,toast,badge,empty,page-head,insight,lightbox,prose,bubble,schedule-deck,ai-chat,wb-toolbar}.css
  pages/{dashboard,classroom-*,assignment-student,exam-take,submission,manage-*,blog,career,monitor,login,lessondoc-editor}.css
frontend/tailwind/lq-glass-plugin.cjs   # addComponents: tw-glass*, tw-surface, tw-scrim + 4 个降级 @media
templates/lq/app_shell.html
templates/macros/lq/{button,chip,segment,tabs,card,list,table,form,layer,empty,page_head,icons}.html
static/js/lq/{index,core,layer,toast,confirm,tabs,segment,collapsible,tone,html,refract}.js  # ESM，index 挂 window.LQ
static/js/vendor/liquid-glass.js       # deepika MIT，可选
frontend/src/components/lq/*.tsx       # LqButton/LqChip/LqSegment/LqTabs/LqCard/LqModal/LqSheet/LqPopover/LqMenu/LqToast(sonner)/LqConfirm
tools/ui/lint_lq.py                    # 守卫（§10.5）
tools/ui/audit_glass_layers.cjs        # Playwright 层数审计
tools/ui/contrast_probe.cjs            # 玻璃上文本对比度采样
tools/ui/build_icon_registry.py
docs/lq-components.md                  # 组件 API 速查（由本文件 §6 生成，实施时维护）
docs/lq-migrated.json                  # 已迁页面清单（lint 严格范围）
```

Tailwind `content` 已包含上述路径；`build:css` 不变，需新增 `postcss-import` 并置于 `tailwindcss` 之前处理 `@import`。

### 10.2 `tailwind.config.js` 变更

- 新增 `darkMode: ['selector', '[data-appearance="dark"]']`（auto 模式由 CSS 变量处理，不用 `dark:` 工具类）。
- `theme.extend`：`borderRadius` 七档映射 `--ls-r-*`；`backdropBlur` 四档映射 `--ls-blur-*`；`boxShadow` 五档 + glass 两档；`fontFamily.sans/mono`；`fontSize` 十档（含行高/字重元组）；`spacing` 4px 制；`zIndex` 八档；`transitionTimingFunction.spring-*`；`screens` 五档。
- `plugins: [require('tailwindcss-animate'), require('./frontend/tailwind/lq-glass-plugin.cjs')]`。

### 10.3 JS 组件 API（`window.LQ`，ESM 也可 import）

```js
LQ.toast(msg, {tone, duration, action})      LQ.confirm({title, body, confirmLabel, tone}) → Promise<boolean>
LQ.layer.open(el|html, {type:'modal'|'sheet'|'drawer'|'popover'|'menu', anchor, size, onClose}) → handle
LQ.layer.closeTop()  LQ.layer.closeAll()
LQ.tabs(root, {persist})   LQ.segment(root)   LQ.collapsible(root)
LQ.html.btn/chip/badge/empty/spinner/bubble({...}) → string   // 模板字符串用
LQ.btn/chip(...) → Element
LQ.icon(name, size) → string
LQ.tone.observe(el)   LQ.mq.sm/md/lg/xl → MediaQueryList
LQ.motion.reduced → boolean
LQ.refract.mount(el)  // 仅 3 处白名单
```

兼容层（P4 建、P10 删）：`window.showToast/showMessage → LQ.toast`；`openModal/closeModal(id)` → `LQ.layer`（按 id 找 `.modal-backdrop` 自动包成 modal，旧 DOM 在迁移前继续工作）；`createPopoverSystem` 继续导出。

### 10.4 三入口一致性保证

`tests/lq/dom_contract.test.mjs`：对每个组件用同一参数分别调用 Jinja 宏（通过 `jinja2` 子进程渲染）、`LQ.html.*`、React `renderToStaticMarkup`，断言归一化后的 HTML（属性排序、空白）**逐字相等**。新组件必须先加这个测试。

### 10.5 守卫脚本 `tools/ui/lint_lq.py`（CI 与 pre-commit）

对 `docs/lq-migrated.json` 中的模板/JS/CSS 检查并报错：
1. hex/rgb/hsl 字面量（豁免：`tokens.css`、`url()`、`--ls-c-*` 定义行、SVG `fill="none"`）。
2. `style="`（豁免形式：仅含 `--` 自定义属性的 `style="--x:…"`）。
3. `backdrop-filter` 出现在 `lq/components|materials` 之外；blur 值不在四档内。
4. `.lq-glass` 后代含 `.lq-glass`（用 html.parser 解析模板静态部分）。
5. `window.confirm|alert(`、`innerHTML =` 中含 `class="btn `、`spinner` 非 `lq-spinner`、`showToast(` 非 `LQ.toast`。
6. 旧类名残留：`btn btn-|modal-backdrop|app-topbar-action|academic-|ls-button|filter-chip|toast-container`。
7. `z-index:` 字面量。
8. 手写 `?v=`。
未迁页面只警告不阻断；每迁一页把它加入清单。`tools/ui/audit_glass_layers.cjs`（Playwright）：对每个已迁页面统计视口内 `backdrop-filter != none` 的元素数 ≤3、且不在滚动容器内。

### 10.6 cache-bust 与部署

P0 把 38 处手写 `?v=` 改为 `asset_url()`；岛屿加载器里的 `LEGACY_MODULES` 改为从 `partials/vite_islands.html` 注入的 `window.__LS_ASSET_REV` 读取版本（服务端渲染 mtime），彻底消灭手改。部署流程不变（Docker 构建阶段 `npm run build`）。

### 10.7 迁移一页的标准操作（SOP，AI 逐页执行）

1. 用 P03 harness 截图该页（桌面 1440×900 + 移动 390×844，双角色），存 `.codex-temp/lq-audit/<page>/before/`。
2. 选骨架（§7.2），画四层图（§5.2），列出该页所有交互元素 → 组件映射（附录 B）。
3. 模板：换基类为 AppShell；用宏替换标记；删除内联 `<style>`；内联 `<script>` 抽到 `static/js/<page>.js`。
4. JS：`btn/chip/spinner/toast/confirm/modal` 改 `LQ.*`；内联 style 改类或 `--var`。
5. CSS：新建 `lq/pages/<page>.css` 只放该页独有布局（目标 ≤300 行）；从 `ui-system.src.css` **删除**该页旧段落（按 `Source:` 与 polish 注释定位）；`npm run build`。
6. 运行 `lint_lq.py --page <page>`、`audit_glass_layers.cjs`、相关 Python/vitest/e2e 测试。
7. 截图 after，与 before 对照写 3 行结论（信息无丢失、功能无丢失、层数达标）；把页加入 `docs/lq-migrated.json`；提交 `feat(lq): migrate <page>`。
8. 不允许"先美化再收敛"：若该页依赖的组件尚无 `lq-` 版本，先做组件（含三入口测试）再迁页。

---

## 11. 分阶段路线

每阶段：独立分支 → 通过 §12 门槛 → 合并 dev → 部署 → 更新本文档"进度"。阶段内按列表顺序执行。

### P0 解阻与守卫（1–2 天）
- [ ] 38 处 `?v=` → `asset_url()`；岛屿加载器版本注入（§10.6）。
- [ ] 决策落文：向导与课堂成员工作区放弃 iframe（同文档片段）；HTML 包壳保留 iframe；博客正文保留纸感；监控大屏保留深色；LessonDoc 引擎 CSS 不改。
- [ ] `tools/ui/lint_lq.py`、`audit_glass_layers.cjs`、`contrast_probe.cjs`、`tests/lq/dom_contract.test.mjs` 骨架；`docs/lq-migrated.json` = `[]`。
- [ ] 删除 14 个未使用 shadcn 基元（保留 dialog）；`npx shadcn@latest add popover sonner alert-dialog scroll-area accordion --yes`。
- [ ] 基线截图全站（沿用 2026-08 的 24 页清单 + 本文 §9 的 15 个特殊页）。
- 验收：`npm run build`、`npm test`、`unittest discover` 全绿；生产无变化。

### P1 令牌与材质（2–3 天）
- [ ] `lq/tokens.css`：§4 全部令牌（亮/暗/tinted/off）；合并三处令牌块；旧令牌别名层。
- [ ] `lq/materials.css`：`lq-glass*`、`lq-surface*`、`lq-scrim`、`lq-ambient`、`lq-scroll-edge` + 四个降级媒体查询 + `forced-colors`。
- [ ] `lq-glass-plugin.cjs`；`tailwind.config.js` 变更；`postcss-import`。
- [ ] 字体栈、`--ls-font-mono`；`body` 背景改 `lq-ambient`。
- [ ] `.role-teacher` 全局覆盖补齐并删除 topbar/classroom 局部覆盖；`user_ui_preferences.css` 并入 tokens（挂载点不变）。
- [ ] 17 档模糊 → 4 档（脚本替换，P2.8 同法）；195 处 z-index → 令牌；断点近重复值归并（脚本）。
- [ ] 折射 vendored + `LQ.refract`（含设备门槛）。
- 验收：全站截图 diff 仅色值/模糊差异、无布局变化；三种偏好仿真截图；lint 对 `lq/` 目录零报错。

### P2 组件库（5–7 天）
- [ ] 按 §6 顺序实现 20+ 个组件的 CSS + 宏 + JS + React，每个附 `dom_contract` 测试与 `docs/lq-components.md` 条目。
- [ ] `LQ.core`：layer/toast/confirm/tabs/segment/collapsible/tone/html。
- [ ] 图标注册表生成脚本与 `lq_icon` 宏。
- [ ] 预览页 `/dev/lq`（仅 DEBUG，渲染全部组件全部状态，供截图回归）。
- 验收：`/dev/lq` 截图（亮/暗/tinted/off/contrast/forced-colors 六套）；axe 零 serious；键盘遍历全部组件。

### P3 AppShell（3–4 天）
- [ ] `templates/lq/app_shell.html` 四布局；`lq-topbar/sidebar/dock/fab`；View Transitions；scroll-edge。
- [ ] `manage/layout.html` → 壳（首个试点，其 43 个子页立即受益）；`base_navbar.html`、`base_centered.html`、`resume/layout.html` 依次切换（旧壳保留一个发布周期作回退）。
- [ ] 学生导航注册进 `manage_nav_service`；`app_bottomnav.html` 退役。
- 验收：`test_manage_nav_service` 契约更新通过；teacher-app-shell e2e；一屏 blur 层 ≤2；侧栏手风琴/折叠/搜索行为不变。

### P4 反馈与弹层收敛（3–4 天）
- [ ] `LQ.toast` 替换 11 个实现；`LQ.confirm` 替换 43 处 confirm + 3 alert；`lq-spinner` 替换 9 种。
- [ ] `openModal/closeModal` 兼容层；40 个 bespoke modal 按功能逐个改 `lq-modal/sheet/drawer`（清单见附录 B）；4 个 drawer 合并；≥8 个 popover 走 `LQ.layer`。
- [ ] `ui_explanation`、`ls_date_picker`、灯箱换皮。
- 验收：`grep -c "window.confirm" static/js` = 0；模板中 `modal-backdrop` = 0；所有弹层键盘可关、焦点回归、`aria-modal` 正确。

### P5 管理中心 43 页（5–7 天，机械、可交 codex 批处理）
- [ ] 列表页骨架 + `lq-table` + `filter_bar` chips；`page_head` 升级；insight 改名。
- [ ] 每页 SOP；`manage_*.js` 里 `LQ.html.*` 替换；私有 modal 收敛。
- [ ] 向导同文档化；监控大屏令牌化。
- 验收：43 页 before/after；`lint_lq` 零报错；`manage_academic.css`、`manage_classes.css`、`manage_workflow.css` 三段（≈5k 行）从 ui-system 删除。

### P6 首页与课堂（5–7 天）
- [ ] `dashboard.html`/`dashboard_teacher.html`：总台页骨架；3D 课表（§9.1）；议程浮窗 `lq-popover`；快捷入口 `lq-card--interactive`；移动手风琴 `lq-section--collapsible`。
- [ ] 课堂主页（§9.6）分四批：壳与顶栏 → 主区 sections → 活动区 Dock/sheet + 聊天 → 末尾弹层与成员工作区。
- [ ] 学期日历（§9.8）。
- 验收：v3 的 27 项浏览器验收 + CLS 断言；学生首页移动全页高 ≤4200px 断言继续成立；`dashboard.css` + `classroom.css` 段删除（≈15k 行）。

### P7 作业/考试/批改（6–8 天）
- [ ] 内联 CSS 抽出（6,544 行）与内联 JS 拆模块；§9.2–9.4；`exam_editor.html` 编辑器骨架；`assignment_wrong_summary`。
- 验收：提交/批改/考试全部 Python + e2e 测试；4× CPU 节流性能；七个模板 `<style>` 行数 = 0。

### P8 特殊界面与其余页（5–7 天）
- [ ] 白板（9.5）、AI 浮窗（9.7）、星图（9.9）、登录（9.11）、博客（9.12）、材料阅读/HTML 壳/LessonDoc 编辑器（9.13）、投票/分组/消息/资料（9.14）、成长页族与 resume（9.15）、错误/状态页。
- 验收：各节验收条；`blog.css`、`profile.css`、`materials.css`、`ai_chat.css`、`teacher_whiteboard.css` 等旧段删除。

### P9 深色模式与个性化（2–3 天）
- [ ] `user_ui_preferences` 加 `appearance`、`glass` 两列 + API；资料页外观设置；`data-appearance=auto`；深色令牌全站截图校对（重点：图表、聊天气泡、监控、星图、状态色对比度）。
- 验收：深色六套截图；对比度自动检查（axe + contrast_probe）；切换无闪白（SSR 首屏属性）。

### P10 清扫与 Tailwind 4（3–4 天）
- [ ] 删除兼容层、旧令牌别名、`ui-system.src.css` 残余段（目标：文件只剩 `@tailwind` + `@import`，总 CSS ≤15k 行）；删除 `blog-paper.css` 已并入部分、`career_path.css`、`classroom_workspace.css`、`resume_console.css` 等独立文件。
- [ ] Tailwind 3 → 4：`--ls-*` 迁入 `@theme`，插件改 CSS-first。
- [ ] 更新记忆与 `docs/lq-components.md`；本文档标记完成。
- 验收：`tailwind-app.css` 体积 ≤400KB（现 1.39MB）；全站截图与 P9 一致。

---

## 12. 验收与回归

> 补充项见 §14.5（tier B/C 仿真、WebKit 与多机型截图、size-limit、long task）。

### 12.1 自动化契约（CI）
- `tools/ui/lint_lq.py`（已迁页零报错）；`tests/lq/dom_contract.test.mjs`；`npm test`、`npm run typecheck`、`npm run build`；`python -m unittest discover -s tests -t .`；e2e：`ui-explanation`、`teacher-app-shell`、课表×2、待办弹窗、签名点、ui-v3 27 项、新增 `lq-layers.spec.ts`（弹层栈/焦点/Esc）、`lq-shell.spec.ts`（四布局 + 5 断点）。
- `audit_glass_layers.cjs`：每已迁页 blur 层 ≤3、滚动容器内 0。
- 截图回归：`/dev/lq` 六套 + 已迁页面双视口双角色，像素 diff 阈值 0.5%。

### 12.2 人工验收（每阶段）
- 四层图能对上；每视图 1 个 prominent；无眉题、无零值卡、无重复数字；文案 ≤ 字数上限；移动端拇指可达；说明进浮窗。

### 12.3 性能红线
- Chrome DevTools 4× CPU 节流：课堂页、考试页、首页滚动无 >50ms long task；INP p75 <200ms（本地合成数据）。
- `tailwind-app.css` 每阶段体积不增；首屏 CSS 阻塞 <150ms（本机）。
- 服务器侧无变化（纯静态资产）；nginx 缓存头不变。

### 12.4 无障碍
- axe：serious/critical = 0；玻璃上文本对比 ≥4.5:1（`contrast_probe.cjs` 在最亮/最暗 ambient 上采样）；键盘全流程；`prefers-reduced-motion/transparency/contrast`、`forced-colors` 四种仿真截图。

---

## 13. 明确不做与风险

**不做**：整站换框架；WebGL 折射；每张卡片都玻璃；自定义字体下载；Tailwind 4 提前（放 P10）；小程序端（另有计划）；LessonDoc 课件引擎皮肤；改任何业务 API。

| 风险 | 缓解 |
|---|---|
| 旧笔记本 backdrop-filter 卡顿 | 层数预算 + `data-lq-glass=off` 自动门槛（`hardwareConcurrency<=2`）+ 绘制期关 blur 先例 |
| iframe 内容不可折射 | 向导与成员工作区去 iframe；HTML 包壳接受不折射 |
| 玻璃降低可读性 | 默认 tinted（非 clear）；ink 令牌专用；contrast probe |
| 迁移期双系统并存混乱 | 兼容层 + `lq-migrated.json` 清单 + lint 只对已迁页严格 |
| JS 字符串标记量大（~640 处 innerHTML） | `LQ.html.*` 工厂 + codex 批处理任务书（沿用 `.codex-temp/task-*.md` 模式）+ Claude 验收 |
| 63k 行 CSS 删错 | 每页按 `Source:`/polish 注释定位删除，删后全站截图 diff |
| 深色模式引入新对比度问题 | P9 独立阶段、六套截图、axe |
| View Transitions 浏览器差异 | 全部 `if (!document.startViewTransition)` 守卫，退回 opacity |
| 个性化配色与 tint 冲突 | tint 全部从 `--ls-primary` 派生，配色只改 primary 通道 |

---

## 14. 性能预算、浏览器兼容矩阵与移动端专项

> 本章是 §3 铁律 1–3、§5.1 材质、§11 各阶段验收的补充；与本章冲突时以本章为准。

### 14.1 支持基线与浏览器矩阵

| 等级 | 浏览器 | 承诺 |
|---|---|---|
| **A 完整体验** | Chrome / Edge ≥ 111（含 Chromium 内核国产浏览器最新版）、Safari ≥ 17（macOS 14 / iOS 17）、Firefox ≥ 128 | 玻璃、弹簧、View Transitions、原生 dialog/popover 全部生效 |
| **B 玻璃体验** | Chrome / Edge 96–110、Safari 15.4–16.x、Firefox 103–127、微信 XWeb/X5 近两年版本 | 有毛玻璃与基础动效；弹簧退回 `ease-out`；弹层用 JS 自实现顶层与焦点圈闭；无 View Transitions |
| **C 霜面体验** | 以上更旧版本、UC/QQ/360 极速模式旧内核、Android 系统 WebView < 96 | 自动 `data-lq-glass="off"`：无 backdrop-filter，`lq-glass` 退成 0.96 不透明霜面；功能完整 |
| **不支持** | IE、Android 4.x WebView | 显示 `status.html` 升级提示 |

判定在 `static/js/lq/core.js` 首屏同步执行（内联到 `app_shell.html` `<head>`，避免闪变）：

```js
const tier = (() => {
  const s = CSS.supports.bind(CSS);
  const glass = s('backdrop-filter','blur(1px)') || s('-webkit-backdrop-filter','blur(1px)');
  if (!glass) return 'C';
  const full = s('animation-timing-function','linear(0,1)') && 'popover' in HTMLElement.prototype && s('scrollbar-gutter','stable');
  return full ? 'A' : 'B';
})();
document.documentElement.dataset.lqTier = tier;   // A | B | C
if (tier === 'C') document.documentElement.dataset.lqGlass = 'off';
```

### 14.2 每项新特性的检测与退路（必须成对出现，lint 检查）

| 特性 | 使用处 | 检测 | 退路 |
|---|---|---|---|
| `backdrop-filter` | 所有 `lq-glass*`、`lq-scrim` | `@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px)))`；**每条声明必须同时写 `-webkit-` 前缀**（Safari ≤ 17 仍需） | `@supports not` 分支：`background: hsl(var(--ls-surface-1) / .96)`，阴影保留 |
| `linear()` 弹簧 | `--ls-spring-*` | `@supports (animation-timing-function: linear(0, 1))` | 令牌默认值先写 `var(--ls-ease-out)`，在 `@supports` 内覆盖为 `linear()` |
| `@starting-style` + `transition-behavior: allow-discrete` | 弹层出入场 | `@supports (transition-behavior: allow-discrete)` | `LQ.layer` 用 `requestAnimationFrame` 两帧切 `is-open` 类做入场；出场等 `transitionend` 再 `hidden` |
| 原生 `<dialog>` / `popover` 属性 | `LQ.layer` 顶层渲染 | `'popover' in HTMLElement.prototype`、`typeof HTMLDialogElement !== 'undefined'` | 把弹层节点 `appendChild` 到 `#lq-layers`（body 直接子元素，无 transform 祖先），自实现 inert（`inert` 属性不支持时用 `aria-hidden` + tabindex 遍历） |
| View Transitions（同文档与跨文档） | 顶栏/侧栏/Dock、FAB→菜单 morph | `document.startViewTransition`；`@view-transition` 在 CSS 内天然忽略 | 直接切换；morph 退回 opacity + scale |
| `scrollbar-gutter: stable` | 滚动锁防跳 | `CSS.supports` | JS 计算滚动条宽写 `--lq-scrollbar-w` 给 `padding-right` |
| `mask-composite` 渐变描边 | `lq-glass::before`（可选装饰） | `@supports ((mask-composite: exclude) or (-webkit-mask-composite: xor))` | 不渲染渐变描边，仅保留 1px `border` |
| `prefers-reduced-transparency` | 自动 off | Chromium 118+ / Safari 17.4+ 才识别；其它忽略 | 站内开关 `data-lq-glass` 与 tier C 兜底 |
| `100dvh` | 沉浸布局、sheet 高度 | `@supports (height: 100dvh)` | `100vh` + JS 写 `--lq-vh`（`visualViewport.height`） |
| `contain: paint`、`isolation` | 玻璃容器 | 全面支持，无需检测 | — |
| `mix-blend-mode: screen` sheen | `lq-glass::before` | 全面支持 | — |
| `inert` | 弹层背景不可交互 | `'inert' in HTMLElement.prototype` | 上文 aria-hidden 方案 |

规则：**任何 §4–§6 之外的新 CSS/JS 特性引入，必须在本表登记检测与退路，否则 lint 报错**（`lint_lq.py` 维护一份允许特性白名单）。

### 14.3 性能预算（数值化，CI 断言）

| 指标 | 预算 | 测法 |
|---|---|---|
| 一屏可见 `backdrop-filter` 元素 | ≤3（L3 打开时 scrim 计 1，L2 不计） | `audit_glass_layers.cjs` |
| 单个 blur 值 | ≤24px；scrim 8px | lint |
| `lq-ambient` 色团 | 桌面 tier A 且 `prefers-reduced-motion: no-preference` 才动画；tier B 静止；tier C、`pointer: coarse`、`saveData`、`deviceMemory ≤ 4` **不渲染色团**（只保留径向渐变） | `core.js` 设 `data-lq-ambient="full|static|none"` |
| `saturate()` | 只在 L2/L3 玻璃上；tier B 降到 130% | 令牌按 tier 覆盖 |
| 首屏 CSS | `tailwind-app.css` ≤400KB（P10 目标），过渡期每阶段不增；`lq/*.css` 合计 ≤120KB min | `npm run build` 后 size-limit 断言 |
| 首屏 JS（壳） | `lq/index.js` ≤18KB gzip；折射库按需 `import()`，不进首屏 | size-limit |
| 长任务 | 4× CPU 节流、慢 3G 网络仿真：首页/课堂/考试页滚动与 tab 切换无 >50ms long task；弹层打开 ≤2 帧掉帧 | Playwright + CDP `Performance.enable` |
| CLS | ≤0.05（沿用 v3 断言） | `ui_v3_layout.cjs` |
| INP p75 | <200ms 本机合成数据 | 同上 |
| 动画属性 | 玻璃层内只允许 `opacity/transform`；禁止动画 `backdrop-filter`、`box-shadow`、`filter`、`width/height` | lint（解析 `@keyframes` 与 `transition-property`） |
| 图片 | 灯箱缩略图走已有 thumb 变体；卡片图 `loading="lazy" decoding="async"` | lint |

真实机型实测（P1 完成后、P5 开始前必须做一次，结果写入本文档 §14.6）：教室常见配置各取一台——2 核 4GB Windows 10 集显笔记本、8 年前 i5 台式、iPad 9、千元安卓（如 Redmi 系列）+ 微信内置浏览器。测 `/dev/lq` 全组件页与课堂主页：滚动 FPS、弹层打开耗时、CPU 占用。任一机型 FPS < 45 → 该 tier 的 blur 值下调一档或改 tier C。

### 14.4 移动端专项规则

**布局与触控**
- 布局断点行为见 §7.3；所有可点击目标 `pointer: coarse` 下 ≥44×44（按钮通过 `min-height` 提升，chip 通过 8px 透明 `::after` 扩大命中区）。
- 主操作在拇指区：弹层 `__foot` 主按钮在最下；作答页"提交"在 Dock；表单页主按钮固定底部 `lq-glass` 条（`position: sticky; bottom: 0`）。
- 单列优先；卡片网格 ≤640 强制单列；右栏内容移到主列尾部或 Dock 的 sheet。
- 字号移动档 +1px（§4.5 表），正文 16px 以避免 iOS 输入框聚焦自动缩放（`lq-input` 字号 ≥16px 在 iOS 上必需，桌面 14px）。

**iOS Safari 专项**
- `backdrop-filter` 元素禁止与 `position: fixed` 祖先内的 `transform` 共存（会渲染成不透明）：`lq-topbar` 用 `sticky` 不用 `fixed`；`lq-dock` 的居中改用 `left: 12px; right: 12px; margin: 0 auto; width: fit-content` 替代 `translateX(-50%)`。
- 高度用 `100dvh`；sheet 最大高 `min(92dvh, 100vh - env(safe-area-inset-top))`。
- 滚动锁：`body.lq-scroll-locked { position: fixed; width: 100%; top: -<scrollY>px }` 记录并恢复 scrollY（iOS 上 `overflow: hidden` 无效）；sheet 内部滚动区 `overscroll-behavior: contain`。
- 橡皮筋回弹时 sticky 顶栏下露底：`lq-ambient` 用 `position: fixed; inset: 0`，不用 `body` 背景。
- `-webkit-tap-highlight-color: transparent` 全局；`touch-action: manipulation` 于按钮防 300ms 延迟；`user-select: none` 只在按钮/Dock。
- 底部 Dock `padding-bottom: env(safe-area-inset-bottom)`；全屏页 `viewport-fit=cover`。
- iOS 上 `will-change: transform` 会让玻璃元素变糊/闪烁，玻璃层**不加** `will-change`，只加 `transform: translateZ(0)`（桌面 Chromium 才加 will-change，按 UA 令牌分支）。

**Android / 微信专项**
- 低端安卓 blur 成本高：`pointer: coarse` 且 `deviceMemory ≤ 4` 或 `hardwareConcurrency ≤ 4` → tier 降为 B 并把 `--ls-blur-regular` 降到 12px、thick 16px；`lq-ambient` 不渲染色团。
- 微信内置浏览器：X5 旧版 `backdrop-filter` 支持不稳，按 UA 含 `MicroMessenger` 且 `Chrome/` 版本 < 96 → tier C；`position: fixed` 在软键盘弹出时会上浮，Dock 在 `visualViewport.resize` 且高度缩小 >150px 时 `is-hidden`。
- 系统 WebView 深色反转（Android "强制深色"）会破坏玻璃色：`<meta name="color-scheme" content="light dark">` + `color-scheme` CSS 属性按 `data-appearance` 输出，避免系统二次反转。

**离线与弱网**
- `navigator.connection.saveData` 或 `effectiveType` 为 2g/slow-2g → `data-lq-ambient="none"`，折射不加载，动效时长减半。
- 作答页与考试页在 tier C 与弱网下体验必须与 A 一致（功能层面），这两页的 e2e 在 tier C 仿真下也要跑。

### 14.5 验收补充（并入 §12）
- 每阶段截图矩阵新增两列：`tier B`（禁用 `linear()`/popover 的 Chrome 100 仿真，Playwright 用旧版 Chromium 通道）与 `tier C`（`--disable-features=BackdropFilter` 或直接注入 `data-lq-glass=off`）。
- 移动端截图除 390×844 外增加 iPhone SE 375×667 与 iPad 768×1024，`hasTouch: true`，并跑 WebKit 引擎（Playwright webkit）一次。
- size-limit 与 long task 断言进 CI；真实机型实测记录在 §14.6。

### 14.6 真实机型实测记录（待填）

| 机型 | 浏览器 | tier | /dev/lq 滚动 FPS | 课堂页滚动 FPS | 弹层打开 ms | 结论 |
|---|---|---|---|---|---|---|
| （P1 后填写） | | | | | | |

---

## 附录 A 令牌全表（实施时以 `lq/tokens.css` 为准，此处为定稿值）

| 组 | 令牌 | 亮 | 暗 |
|---|---|---|---|
| 色 | `--ls-primary` | 243 75% 59% | 239 84% 72% |
| 色 | `--ls-primary`（.role-teacher） | 175 77% 26% | 173 70% 55% |
| 色 | `--ls-success / warning / destructive / info` | 160 84% 39% / 38 92% 50% / 0 84% 60% / 199 89% 48% | 160 70% 55% / 38 95% 62% / 0 84% 68% / 199 89% 62% |
| 色 | `--ls-background / surface-1 / surface-2` | 222 47% 97% / 0 0% 100% / 210 40% 98% | 224 28% 8% / 224 22% 12% / 224 20% 16% |
| 色 | `--ls-ink / ink-2 / ink-3` | 222 47% 11% / 215 25% 27% / 215 16% 47% | 214 32% 94% / 215 20% 75% / 215 16% 58% |
| 色 | `--ls-line / line-strong` | 214 32% 91% / 215 20% 80% | 220 14% 22% / 220 12% 32% |
| 玻璃 | `--ls-glass-fill / -strong` | 0 0% 100%/.58 / .78 | 224 30% 12%/.62 / .80 |
| 玻璃 | `--ls-glass-line / rim / rim-bottom` | 0 0% 100%/.85 / .95 / .35 | 0 0% 100%/.14 / .18 / .06 |
| 玻璃 | `--ls-glass-ink / muted` | 216 45% 16% / 215 18% 43% | 214 32% 94% / 215 16% 68% |
| 玻璃 | `--ls-glass-saturate` | 170% | 140% |
| 玻璃 | `--ls-scrim` | 222 47% 11%/.28 | 0 0% 0%/.5 |
| 模糊 | thin / regular / thick / scrim | 8 / 16 / 24 / 8 px | 同 |
| 圆角 | capsule / xs / sm / md / lg / xl / 2xl | 999 / 6 / 10 / 14 / 20 / 28 / 36 | 同 |
| 阴影 | 1 / 2 / 3 / 4 / focus / glass / glass-strong | §4.4 | 黑基 .3–.45 |
| 字体 | sans / mono / serif-cjk | §4.5 | 同 |
| 字号 | display…caption 十档 | §4.5 | 同 |
| 间距 | 1…16 | 4/8/12/16/20/24/32/40/48/64 | 同 |
| z | raised…explain 八档 | 10/100/1100/1200/1300/1400/1500/1600 | 同 |
| 动效 | dur fast/base/slow/enter；ease-out/in-out；spring snappy/soft/bouncy | §4.8 | 同 |
| 断点 | sm/md/lg/xl/2xl | 640/768/1024/1280/1536 | 同 |
| tone 扩展 | proctor/exam/homework/todo/class/course-1…10/ink-1…10/monitor-* | 由 `--ls-c-*` 派生 | 提亮一档 |

## 附录 B 旧 → 新映射（迁页时逐项对照）

| 旧 | 新 |
|---|---|
| `.btn .btn-primary` | `lq-btn lq-btn--prominent`（每视图 1 个，其余降 `--soft`） |
| `.btn-outline` / `.btn-secondary` | `lq-btn--soft`（内容区）/ `lq-btn--glass`（玻璃上） |
| `.btn-ghost` | `lq-btn--ghost` |
| `.btn-danger*` | `lq-btn--destructive` |
| `.btn-sm/-lg/-icon` | `--sm/--lg/--icon` |
| `.btn-accent/.btn-success`（渐变） | 删除；用 `--prominent` 或 `--soft --tone-success` |
| `app-topbar-action*`（`topbar_action` 宏） | `lq_btn(variant='glass', size='sm')` 于 `lq-topbar__actions`；caption 继续进 explain |
| `.ls-button*` | `lq-btn` |
| `.filter-chip*`（两处定义） | `lq-chip--filter` |
| `.badge*` / `.lanshare-pill` | `lq-chip--status` / `lq-badge` |
| `.card/.panel/.lanshare-surface/.status-card/.academic-card/.dashboard-*-card/.insight-panel` | `lq-card` / `lq-surface` |
| `.table*` | `lq-table` |
| `.form-control/.form-select/.form-check*` | `lq-input/lq-select/lq-checkbox/lq-radio/lq-switch` |
| `.modal-backdrop > .modal-dialog…` 与 40 个 bespoke `*-modal-*`（`academic-*`、`um-modal-*`、`smart-classroom-modal-*`、`edu-sync-modal-*`、`gw-*`、`learning-modal-*`、`materials-*-modal`、`class-*-modal`、`course-modal-*`、`teaching-session-modal-*`、`signature-*-modal`、`life-tip-modal`、`teacher-onboarding-*`、`session-material-ai-modal`、`shared-file-modal`、`feedback-modal`、`blog-modal`、`exam-paper-preview-*`、`exam-reverse-modal`、`wrong-answer-modal`、`rubric-modal-*`、`scoring-modal-*`、`closeout-modal`、`knowledge-detail-modal`、`assignment-kind-dialog`、`afm-dialog`、`att-dialog`、`export-dialog`、`classroom-group-qr-dialog`、`textbook-intro-catalog-backdrop`、`ai-workspace-modal`、`learning-certificate-backdrop`） | `lq-modal`（表单/详情）、`lq-sheet`（移动/筛选）、`lq-drawer`（详情侧栏）、`lq-confirm`（确认） |
| `.class-student-drawer/.offering-hub-drawer/.ai-agent-history-drawer/gw-reader-panel` | `lq-drawer` |
| `.ls-popover*/.course-popover*/.tag-popover*/.agenda-popover/.blog-user-popover/.chat-emoji-popover*/.dashboard-evaluation-menu__popover/.smart-attendance-detail-popover` | `lq-popover` / `lq-menu` |
| `.toast-container/.message-center-bell-toast/.cs-toast` + 11 个 notify/toast 函数 | `LQ.toast` |
| `.spinner/.afm-spinner/.obs-spinner/.spw-spinner/.tsf-spinner/.prompt-pool-spinner/.semester-button-spinner/.thinking-status-spinner` | `lq-spinner` |
| `.empty-state*/.table-empty/.page-empty` | `lq-empty --inline/--card/--page` |
| `page_head/empty_state/filter_bar` 宏 | 同名升级（去 eyebrow；chips 优先） |
| `.manage-nav*` | `lq-sidebar` / `lq-nav-item` |
| `partials/app_bottomnav.html` | `lq-dock` |
| `.classroom-activity-tabs/.discussion-room-tabs/.message-center-tab/.blog-section-tabs/.workflow-stage-track/.member-tabs/…` | `lq-tabs` / `lq-segment` |
| `.ls-glass/.ls-glass-pill/.ls-lightbox*` | `lq-glass/lq-btn--glass/lq-lightbox*` |
| `.ui-explain-popover` | `lq-popover`（逻辑不变） |
| `.ls-anim-*`、`--ux-motion-*` | `lq-anim-rise/stagger` + `--ls-dur/ease/spring-*` |
| `--radius-*`、`--text-*`、`--shadow-*`、`--gray-*`、`--primary-color`、`--dashboard-*` 等 | §4 令牌（别名保留至 P10） |

## 附录 C 每页检查清单（复制进 PR 描述）

- [ ] 骨架类型：___；四层图已画；一屏 blur 层数 ___（≤3）
- [ ] 主操作 1 个；按钮文案 ≤4 字；无眉题；零值隐身；同屏数字唯一
- [ ] 无内联 `<style>`；无 `style="`（除 `--var`）；无 hex；无 `window.confirm/alert`
- [ ] 弹层全部 `LQ.layer`；toast 全部 `LQ.toast`；spinner 统一
- [ ] 旧 CSS 段已删（行号范围：___）；`lq-migrated.json` 已加
- [ ] 截图 before/after 双视口双角色；六套偏好仿真（已迁壳/组件时）
- [ ] 键盘可达；焦点环可见；axe 零 serious；对比度探针通过
- [ ] 相关测试：___ 全绿

## 附录 D 调研来源

Apple HIG Materials（Liquid Glass）、Adopting Liquid Glass、WWDC25 219/356/323；conorluddy/LiquidGlassReference；kube.io "Liquid Glass in the Browser"；Aave "Building glass for the web"；webtricks.dev liquid-glass-css；CSS-Tricks "Getting Clarity on Apple's Liquid Glass"；LogRocket liquid glass CSS/SVG；deepika-builds/liquid-glass（MIT）；shuding/liquid-glass；rdev/liquid-glass-react；Tontoon7/liquidglass-tailwind（仅参考）；daisyUI 5 glass 变量命名；Josh Comeau linear() 弹簧；kvin.me CSS springs 与 tailwindcss-spring；MDN @starting-style / View Transitions；Axess Lab glassmorphism accessibility；W3C SVG WG issue #1142；sixcolors "Soaping up Liquid Glass"。项目内：`docs/frontend-redesign-2026-08.md`、`docs/ux-overhaul-2026-08.md`、`docs/manage-center-improvement-plan-2026-09-11.md`、`docs/home-classroom-ui-v3-implementation-2026-09-07.md`、`docs/frontend-premium-design-language.md`、`static/css/ui-system.src.css:63605-63900`（灯箱种子）。

---

## 进度

- [ ] P0 · [ ] P1 · [ ] P2 · [ ] P3 · [ ] P4 · [ ] P5 · [ ] P6 · [ ] P7 · [ ] P8 · [ ] P9 · [ ] P10
