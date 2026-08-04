# 前端设计系统统一改造（2026-08 · 方案 A）

> 真源文档。目标:消灭「四套视觉语言并存」(Tailwind 工具类 + daisyUI + flowbite + 5.7 万行手写 CSS),
> 全站收敛到 **单一语义令牌层 + shadcn/ui 组件**,以绞杀者模式逐页迁移,不做大爆炸重写。
> 架构不变:FastAPI + Jinja + Vite islands(总体架构目标见 frontend-modernization-goal.md,
> 视觉/交互原则见 frontend-premium-design-language.md;本文档管"用什么组件体系、怎么迁")。

## 一、基座(已落地 ✅)

- **语义令牌**:`static/css/ui-system.src.css` `:root` 中的 `--ls-*` 变量(HSL 通道值),
  是新 UI 唯一取色源。主色 indigo `#4f46e5` → `--ls-primary: 243 75% 59%`,圆角基准 `--ls-radius: 0.7rem`。
- **Tailwind 接线**:`tailwind.config.js` `theme.extend.colors` 将 `primary/secondary/muted/accent/
  destructive/card/popover/border/input/ring/background/foreground` 映射到 `--ls-*`;
  `borderRadius.sm/md/lg` 映射 `--ls-radius`;新增 `tailwindcss-animate` 插件。
- **shadcn/ui**:`components.json`(new-york 风格,`tw-` 前缀);组件 vendor 在
  `frontend/src/components/ui/`(button/card/dialog/input/label/textarea/select/tabs/badge/
  tooltip/dropdown-menu/separator/skeleton/switch/sheet);`cn()` 工具在 `frontend/src/lib/utils.ts`。
- **追加组件**:`npx shadcn@latest add <name> --yes`(CLI 自动加 `tw-` 前缀)。
- **试点**:`dashboard-quick-actions` island 已迁移(遗留 CSS 类 → Badge + 令牌工具类),
  Playwright 截图验证通过。

## 二、铁律(新代码红线)

1. **新 UI 只准用 `--ls-*` 语义色**(经 `tw-bg-primary` 等类),禁止新增 hex/rgb 硬编码、
   禁止新用 daisyUI(`dui-`)/flowbite 组件。
2. **交互组件优先 shadcn**:弹窗用 Dialog/Sheet,下拉用 DropdownMenu/Select,不再手写。
3. **迁移一页,退役一页的 CSS**:把该页在 ui-system.src.css 中的专属段落删掉,禁止留双份。
4. 改 `ui-system.src.css` 或 tsx 后必须 `npm run build`(css + frontend),部署带 `static/dist` 与
   `tailwind-app.css`。

## 三、迁移路线(按优先级)

| 期 | 范围 | 说明 |
|---|---|---|
| P1 ✅ | 基座 + 试点 | 令牌层、shadcn、quick-actions |
| P2.5 ✅ | 全站基元并轨 | 遗留全局变量 `--primary-color/--danger-color/--text-primary` 派生自 `--ls-*`;`.btn-primary/outline/danger` 扁平化到令牌(去渐变),focus ring 走 `--ls-ring`——101 个模板的按钮一次统一,且教师页遗留按钮自动跟随青绿角色主题;dashboard 内联 style 硬编码色清零 |
| P2.6 ✅ | 硬编码主色清扫 | 全文件 340 处硬编码主色/危险色(`rgba(79,70,229,α)`×212、`#4f46e5`×79、`rgba(239,68,68,α)`×48)一次性替换为 `hsl(var(--ls-primary/destructive) / α)`;表单控件 focus/选中/文件按钮 hover 随之全部令牌化 |
| P2 🔄 | Dashboard 全页 | 变量层已并轨 ✅:`--dashboard-*` 全部派生自 `--ls-*`,角色主题(教师青绿 `175 77% 26%`/学生靛蓝)下沉为 `.role-teacher` 的 `--ls-primary/ring/accent` 覆盖,shadcn 组件自动跟随角色色;卡片圆角统一到 `--ls-radius`。剩余:面板/列表标记迁 shadcn、删旧 CSS 段 |
| P3 | 消息中心 + 个人资料页 | 已有 island 基础,改造成本最低 |
| P4 | 课堂主页(classroom_main_v4) | 体量最大,拆板块分批 |
| P5 | 管理中心各子页 | 表格密集,引入 shadcn 数据表格模式 |
| P6 ✅ | daisyUI/flowbite 退役 | 审计发现 `dui-`/flowbite 类在模板/JS/islands/编译产物中零使用(纯死重),已卸载两依赖(-14 包)并清理 tailwind.config,产物 -12KB;四页视觉回归无损 |
| P7 | Tailwind 3 → 4 升级 | 依赖 P6;CSS-first 配置,`--ls-*` 迁入 `@theme` |

## 四、验证方式

- 单测:`npm test`;类型:`npm run typecheck`。
- 视觉:tmpspec + Playwright harness 截图(流程见记忆 ui-verification-harness;
  config 放 tmpspec 时 `testDir: __dirname`、webServer 加 `cwd: repoRoot`)。
