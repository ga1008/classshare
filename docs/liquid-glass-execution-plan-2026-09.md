# LanShare 液态玻璃设计系统 · 执行版总纲与施工/验收细则

> 日期：2026-09-19。**本文是执行真源**，由三份文档合并而成：原案 `liquid-glass-design-system-2026-09.md`（视觉与组件规范）、补充稿 v1 `…-supplement.md`（风险纠偏 R01–R24）、补充稿 v2 `…-supplement-v2.md`（对照代码库裁定 D01–D22、状态色注册表、按钮登记、编辑器规格）。三份保留作为背景与证据，**条款冲突时以本文为准**。
> 状态：S0–S3 本地工程出口已收口，S4 共享壳与常规页分批实施中（2026-09-21）；正式阶段签字、实机和发布不由本地测试替代。当前证据、代码核对修正与未测项见 `lq-acceptance.md`，进度勾选在 §22。
> 读者与用法：第一部分（总纲）给决策者与所有参与者；第二部分（施工细则）给逐组件/逐页实施的 AI 与工程师，"照着做"；第三部分（验收细则）给验收者，"照着查"。任何一页/一组件的完成，以第三部分的证据为准，不以"类名已换"为准。

---

## 目录

**第一部分 总纲**
1. 目标与效果
2. 设计原则（十二条铁律，修订版）
3. 现状基线（2026-09-19 实测）
4. 关键决策汇总
5. 总体规划与依赖
6. 分工与协作方式
7. 验收总则与完成定义

**第二部分 施工细则**
8. 令牌规范（含语义状态色注册表）
9. 材质与分层
10. 组件规范（基础组件 + 业务组件）
11. 壳、布局与页面骨架
12. 文案、信息层级与图标
13. 特殊界面施工单（§13.0 固定 12 项结构；§13.5 课表已按 2026-09-20 工作区重写）
14. 业务按钮登记表（首批）
15. 工程施工（目录、构建、交付、守卫、SOP、缓存、回退、**通用收尾细则 §15.9**）
16. 阶段施工单 S0–S8

**第三部分 验收细则**
17. 自动化契约与命令
18. 业务场景门禁 B01–B16
19. 视觉与无障碍矩阵
20. 性能与兼容
21. PR 检查清单模板
22. 进度与文档所有权

附录 A 旧→新映射 · 附录 B 决策索引（R/D 对照）· 附录 C 术语

---

# 第一部分 总纲

## 1. 目标与效果

### 1.1 做什么

把 LanShare 全平台（Web 端：教师管理中心、教师/学生首页、课堂主页、作业/考试/批改链、五个独立编辑器、白板、AI 浮窗、材料阅读、博客、消息、个人资料、成长页、简历、登录/状态页、监控、星图）的视觉与交互统一到一套 **LanShare Glass 组件库**（组件类前缀 `lq-`，令牌前缀沿用 `--ls-*`），视觉语言为 Apple iOS 26 Liquid Glass 的 Web 诠释。

### 1.2 做成什么样（用户可见效果）

- **导航与控制层是玻璃**：顶栏、侧栏、底部 Dock、工具条、悬浮按钮、弹层、浮窗是半透明毛玻璃（模糊 + 饱和 + 镜面高光边 + 柔和投影），内容从其下滚过时软溶解。
- **内容层是清晰的霜面**：卡片、表格、表单、列表、正文是接近不透明的白/深色面，无模糊，靠留白与 1px 分隔线分组；阅读正文（博客、材料、题干）保持纸感。
- **控件是胶囊与同心圆角**：按钮、chip、开关、搜索框、Dock 是胶囊；卡片内元素圆角 = 父圆角 − 内边距。
- **每个视图只有一个主操作**：实色主色按钮唯一，其余用玻璃/软底/幽灵；选中态用 tint（12–14% 主色底）而非实色填充。
- **状态色全站一致**：作业/提交/分数/截止/保存/任务/签名等 10 个状态族只用 5 个语义色相 + 固定识别色（课程 10 色、附件 7 色），同一状态在任何页面同色同标签。
- **动效克制、有弹性**：进入/聚焦/选择/完成/状态变化才动；弹层与 Dock 用弹簧；`prefers-reduced-motion` 全关。
- **明暗自适应与个性化**：亮/暗/跟随系统三档外观；六套配色（教师默认青绿、学生默认靛蓝）对学生与教师都开放并作用于全站；"关闭透明"开关让玻璃退成霜面。
- **移动端可用**：≤768 侧栏变抽屉、弹窗变底部 sheet、Dock 承载页内切换；所有可点目标 ≥44×44；主操作在拇指区。
- **不割裂**：4 类壳布局共享同一顶栏/侧栏/Dock 组件；9 套 toast、67 处 `confirm`、约 40 种 spinner、≥8 套弹层收敛为各一套；每页私有前缀退役。
- **不啰嗦**：标题 ≤14 字、区块标题 ≤10 字、按钮动词开头 ≤4 字（推荐值）、说明 >20 字进说明浮窗；但错误、约束、权限、覆盖后果**常显**。

### 1.3 明确不做

Tailwind 4 升级；开课向导页（本项目**退役**，新向导结合教务系统另立项目，见 §13.13）；HTML 包壳加 `sandbox`；WebGL/SVG 折射（S8 可选 ≤3 处）；跨文档 View Transitions（可选）；小程序端（只输出令牌 JSON）；LessonDoc 课件引擎皮肤（`static/lessondoc/2.0/`）；改任何业务 API 语义（唯一计划内数据模型扩展是偏好表两列）；打印样式表（全站无打印路径，导出走 python-docx）；每文件行数硬指标；"每阶段必部署"（部署按项目发布门禁与用户授权）。

## 2. 设计原则（十二条铁律，修订版；★ 由 lint 强制）

1. ★ **只用令牌取色**：新代码禁止 hex/rgb/hsl 字面量（豁免：`tokens.css`、`url()`、`--ls-c-*` 定义行、SVG `fill="none"`、登记的例外：白板笔迹色、课表 DECK_CSS 注入机制、`--teacher-whiteboard-*` 运行时变量、vendor、LessonDoc 引擎）。
2. ★ **玻璃只给漂浮层，且只有材质宿主产生模糊**：`lq-glass*` 只出现在顶栏/侧栏/Dock/工具条/FAB/弹层/浮窗；内容层用 `lq-surface`；玻璃容器内的按钮/chip/输入框**不再创建 backdrop-filter**，只用填充、高光、描边表现状态。`lq-glass` 后代禁止 `lq-glass`。
3. ★ **按真实渲染计数**：普通页持续模糊宿主 ≤2（顶栏 + 侧栏/工具条），弹层打开后总数 ≤3；计数包含伪元素、`::backdrop`、被透明遮挡的元素。滚动容器内部为 0。scrim 与 toast 默认**不模糊**。
4. **一个活动任务区域一个主操作**：页面、模态框、正在编辑的分区各自定义；桌面/移动可互斥显示同一动作，不得渲染两个会重复触发业务的活动控件。
5. **胶囊与同心**：独立可点控件 = 胶囊；容器内贴边子元素圆角 = 父圆角 − 内边距；不贴边的子元素比父小两档。
6. **零值隐身、重复合并，但有例外**：装饰性空统计降为一行；同屏同一数字只出现一次。**零分、未答数、余额、截止时间、文件限制、错误、权限、覆盖后果必须可见**；筛选无结果保留筛选与清除入口。
7. **文案短而功能性**：按钮动词开头、推荐 ≤4 字（关键动作可换行不省略）；标题 ≤14 / 区块 ≤10 / 副标题 ≤28 / 占位 ≤20 字；说明 >20 字进 `data-explain` 浮窗；错误/约束/权限/硬约束常显。
8. **动效只表达进入、聚焦、选择、完成、状态变化**：120–400ms；弹簧只用于弹层与 Dock；玻璃层内只动 `opacity/transform`；`prefers-reduced-motion` 下全关且禁用形变。
9. **三入口语义等价**：每个组件的 Jinja 宏、原生 JS 工厂、React 组件输出**语义、状态、交互、视觉槽位一致**（不要求 HTML 逐字相等；原生元素与 Radix 合法包装、动态 id、Portal 允许）。改样式只改 CSS。
10. ★ **迁一页删一页，删除以消费者清单为据**：页面迁到 `lq-` 后，其专属 CSS 段与内联 `<style>` 删除；共享规则仍有消费者则保留；`Source:` 注释是线索不是证明。
11. **触控优先**：`pointer: coarse` 下所有可点目标 ≥44×44 CSS px（扩展区域不得重叠相邻控件）；hover 才能发现的信息一律有非 hover 等价入口（focus-within、触控常显）。
12. **无障碍不让位**：玻璃上正文 ≥4.5:1；焦点环实色 2px 不被裁切；`prefers-reduced-transparency` / `prefers-contrast: more` / `forced-colors` 三种模式截图通过；tab/radio/listbox/menu 语义不混用；模态焦点圈闭与回归。

## 3. 现状基线（2026-09-19 实测，排除 `.codex-temp/`）

| 维度 | 实测 | 含义 |
|---|---|---|
| 模板 | 135 个；56 无 `extends`（6 宏、31 partial、3 布局根、**5 个独立完整文档**：`exam_editor` 3196 行、`exam_take` 4228、`lesson_plan_editor` 57、`assessment_plan_editor` 81、`teacher_evaluation_editor` 86）；按基类：`manage/layout` 43(+1)、`base` 11、`base_navbar` 9、`base_centered` 8、`resume/layout` 7 | 壳 = 3 个文档根 + 2 个 base 派生 + 5 个独立文档 |
| 教师首页 | `dashboard_teacher.html` 已 extends `manage/layout.html` | 教师首页留在管理壳 |
| JS / React | `static/js` 156（递归 228）；18 `.tsx`、16 Vite 入口 | — |
| CSS | `ui-system.src.css` 63,880 行（17 个 `Source:` 段 + 尾部追加块）；产物 `tailwind-app.css` 1.39MB；21 个独立 css 7,117 行；**legacy `blog.css` 段 28625–31754（3,130 行）仍在，已被 `blog-paper.css` 全量覆盖** | — |
| 内联 `<style>` | 20 模板；前 10：exam_take 1490、wrong_summary 1473、detail_teacher 1260、exam_editor 1048、submission_detail 795、detail_student 484、classroom_main_v4 293、student_login_v4 200、base_centered 139、session_expired 97 | — |
| 令牌 | `--ls-*` 126 个（`:6-60` 唯一 `@layer base :root`）+ `--ls-c-*` 86 个（864 条声明依赖）；另 3 个裸 `:root`（52250 `--ux-motion-*`、54423、63609 灯箱 `--ls-glass-*`）；无深色；`tailwind.config.js` 无 `darkMode` | — |
| 玻璃 | `backdrop-filter` 手写 CSS 约 177 处，**17 种模糊半径**（416 用例）；真"液态玻璃"种子：灯箱 `.ls-glass`（`:63618`）、说明浮窗、人生一言卡（`:50741`）、登录卡 | — |
| shadcn / `tw-` | 模板 0 处；`frontend/src` 89 处全在 `components/ui/*.tsx`；只有 `dialog.tsx` 被 2 岛引用 | 方案 A 服务端层未落地 |
| 反馈层 | toast **9** 个实现；`confirm(` **67** 处/38 文件 + `alert` 3；spinner **约 40** 类名；弹层 **≥8** 套（`ui.js openModal` 59 处 `.modal-backdrop`、`ui_popover.js` 有栈/圈闭/焦点回归但仅 3 消费者、`poll-/collab-/ga-` overlay、`materials-editor-shell`、`rz-`、`lde-`、课表 `.cs-expand`、日历 todo modal、Radix Dialog）；tabs 8 套；drawer 4 套 | — |
| 状态色 | 10 个状态族按页硬编码（分数档在 3 处各写一套；签名 css 77 行 152 hex） | "用色混乱"根因 |
| 管理中心 | 2026-09-13 已收口：六域导航、`work_inbox`、`page_head/empty_state/filter_bar` 宏（41 处调用）、40 模板内联归零、864 条令牌化；结构性 e2e（`teacher-app-shell`、`[data-page-head]` 同构、nav service 契约） | 只需升级宏 |
| 3D 课表 | HEAD `23fd77e0`：`course_schedule_deck.js` 1626 行（`DECK_CSS` 86–505 自注入，51 hex）+ `course_schedule_change_links.js` 182 + `course_schedule_change_routes.js` 518（纯几何寻路）+ `course_schedule_presentation.js` 87 + 四个 `.d.ts`；放大层课次格与调整标签已带 `backdrop-filter`（内层不透明，视觉几乎无效但逐格计算）；容器查询密度自适应；120ms 可逆预览动画；工作区 +44 行（toggle/≥4px/Esc 顺序）；测试 5 vitest + 5 组件 spec + 12 app spec | §13.5 按工作区重写；保留自注入，令牌化，移除格级 blur |
| 静态交付 | **nginx 不代理 `/static`**；FastAPI 只对 `dist/assets/<hash>` 给 `immutable`，其余含 1.39MB CSS 每导航 `no-cache, must-revalidate`；仅 gzip | 首要性能项 |
| `?v=` | 模板 39 + JS 25 + `classroom-page.tsx` `LEGACY_MODULES` 9 处；`asset_url` 走 mtime + `lru_cache(1)` | — |
| 守卫 | 无 pre-commit / eslint / stylelint / hooks | 从零建 |
| 测试 | vitest ~50 套；`tests/e2e/specs` 28 条（作业链 4 条，钉 `p03-*` testid）；ui-v3 43 场景（断言监听器/socket/滚轮/reduced-motion，**无 CLS/4200px**）；components 13 条；`exam_draft_version.test.cjs` 靠正则从模板抽函数 | 门槛要先写 |
| 打印 | 0 条 `@media print`，导出全 python-docx | 无打印样式 |
| 偏好 | `user_ui_preferences` 学生专用（路由 `Depends(get_current_student)`）、仅 2 条路由生效、服务器唯一存储、无版本化迁移；表结构已允许 `user_role IN ('student','teacher')` | K10 扩展到教师 |
| 开课向导 | `manage/workflow.html` 125 行 + `manage_workflow.js` 679 行 + CSS 段 34851–35985（1,135 行）+ 导航项 `key="workflow"`（`manage_nav_service.py:146`）+ `test_manage_nav_service.py:59,194` 断言；iframe 轮播依赖 `embedded_mode`（唯一生产者 `ui_parts/common.py:1206`） | K4 退役 |

## 4. 关键决策汇总

| # | 决定 |
|---|---|
| K1 | **静态交付先行**（S0）：CSS/JS 入口内容哈希 + `immutable`；nginx 直出 `/static` 哈希文件 + `gzip_static`；`asset_url` 缓存按发布版本失效。 |
| K2 | **shadcn 服务端层退役**：删 13 个未引用组件文件，`dialog.tsx` 待 React 适配器就绪后删；不再新增 shadcn 基元；`tw-` 只做岛屿布局工具类；不引入 sonner/framer-motion/lucide-static。 |
| K3 | **前缀与别名**：组件类 `lq-` 只用于新共享组件；`--ls-*` 不变；`--ls-c-*`、`--ux-motion-*`、`.ls-anim-*`、`.ls-glass` 只加别名不改名（S7 统一）。 |
| K4 | **管理中心不按页 SOP**：升级 `macros/manage_page.html` 三宏 + `manage/layout.html` 一次生效，保留 `data-page-head/data-page-empty/data-filter-bar` 与 `.page-head__*` 结构，同提交更新断言。**开课向导页退役**（S0，§13.13）：路由 301 → classroom-hub，导航项与测试断言删除，模板/JS/CSS 段删除；新向导结合教务系统另立项目。**`profile.html` 双基类拆分**（S4，§13.12）：正文抽成 `partials/profile/*.html`，两个薄壳模板各自 extends 对应布局，路由不变。 |
| K5 | **壳**：不新建"唯一壳"模板；`sidebar` 布局由 `manage/layout.html` 演进，`topbar` 由 `base_navbar.html` 演进，`centered` 由 `base_centered.html` 演进，共享顶栏/侧栏/Dock partial；教师首页留管理壳；**五个独立文档编辑器归 `lq-editor` 族**（保持独立 `<html>`，共用 `partials/lq_editor_head.html`）。 |
| K6 | **状态色注册表先于组件**（§8.13）。 |
| K7 | **弹层协调层 `LQ.layer` 以 `ui_popover.js` 为种子**；`openModal/closeModal` 改为兼容前端；私有 overlay 逐个接入，不一次替换；原生 `<dialog>`/popover 属性作为增强，不作为唯一实现。 |
| K8 | **确认二值 + 三态**：`LQ.confirm` 与 `LQ.choose`；Esc/遮罩 = 取消/返回，绝不映射到有副作用选项。 |
| K9 | **业务门禁票**（独立于换皮，作为对应页迁移前置）：批改页评分 `parseInt` 改有限小数 + 携带 `expected_review_revision`；试卷编辑器 PUT 携带 `expected_revision` 并 409；布置弹窗成功返回任务卡数据（免整页 reload）；考试草稿测试改装载方式。 |
| K10 | **偏好对学生与教师都开放**（§8.15）：三项 `palette / appearance / glass`；服务器唯一存储不变；未登录只跟随系统深浅；`appearance/glass` 两列可空+默认值增量迁移，SQLite/PG 双路径；路由依赖从 `get_current_student` 改为学生/教师通用；配色作用域从"学生 2 条路由"扩展为"该用户的所有页面"（取代 ui-v3 的作用域契约）；设备降级不回写账户偏好。 |
| K11 | **3D 课表**：`DECK_CSS` 保留自注入，内容令牌化；`COURSE_PALETTE` 读令牌；`.d.ts` 补全；前后周是命令按钮不是 segment；迷你标签改状态点；同步入口合并为菜单但来源可辨。 |
| K12 | **测试先于门槛**：批改链 6 条 e2e、`layout-stability.spec.ts`（CLS/高度）在 S3 写出后才作为门槛；已有 `p03-*` testid 与 4 条作业链 spec 必须保留。 |
| K13 | **首胜清单进 S0**：删 legacy blog.css 段、13 个 shadcn 文件、`data-mobile-collapse` 死钩子；评分修复；`LEGACY_MODULES` 版本注入；`base_centered` 内联抽出。 |
| K14 | **学生首页目标文档处置**：C4 `--dash-*` 取消；C1 课程 tone 并入 `--tone-course-*`；C2 count-up 保留为 `lq-card--stat` 可选（HTML 初值为真）；A1/A2/C3 结构项纳入 S4。 |
| K15 | **深色令牌同时生成 `docs/lq-tokens.json`** 供小程序派生；本项目不改小程序。 |
| K16 | **Tailwind 3.4 + CLI 路线不变**；`@import` 置于 `@tailwind` 之前；不同时建 Vite CSS 入口编译同一组组件。 |
| K17 | **Apple 规则是美学参考不是强制**："每视图一个主按钮""四字"" >12 项扁平"是项目取舍，可说明理由。**登录卡启用 Clear 玻璃**（学生登录页，有校园背景图）：`lq-glass--clear` + `lq-scrim` + `data-lq-tone` 采样；S1 用 `contrast_probe.cjs` 对 `life_tips/manifest.json` 全部背景图逐张验证 ≥4.5:1，不达标的图从登录场景池剔除；图片未加载、tier B/C、`data-lq-glass=off` 时退回 `--thick`。教师登录页无背景图，用 `--thick`。 |

## 5. 总体规划与依赖

```
S0 基线·决策·首胜 ──► S1 令牌·状态色·材质·偏好基础 ──► S2 基础组件·业务组件·LQ.layer ──► S3 端到端试点 + 测试补齐
                                                                                            │
        ┌───────────────────────────────────────────────────────────────────────────────────┘
        ▼
S4 壳与常规页（管理宏升级 → 学生首页 → 教师首页 → 日历/消息/资料/登录状态页族）
        ▼
S5 教学核心链（试卷编辑器 → 布置弹窗 → 作答页 → 考试页 → 批改页 → 教师作业页 → 错题归集 → 最终材料/签名）
        ▼
S6 复杂工作台（3D 课表 → 课堂主页四批 → 白板 → AI 浮窗/Agent → LessonDoc 壳 → 材料阅读/HTML 壳 → 简历 → 投票/分组 → 监控/星图 → 其余 3 个独立编辑器）
        ▼
S7 全站收敛（删兼容层/别名/孤立 CSS；深色与五配色校准；四份实施资料定稿；tokens.json 交付）
        ▼
S8 可选增强（折射 ≤3 处、同文档 View Transitions、@starting-style）
```

硬依赖：S1 之前不得写任何 `lq-` 组件 CSS（令牌未定）；S2 之前不得迁任何页；S3 的六条批改链 spec 与 `layout-stability.spec.ts` 未绿之前不得进入 S5/S4 的相应页；K9 四张后端票各自是对应页的前置。每阶段独立可发布、可回退（回退单位 = 页面族的 HTML + CSS + JS + 兼容 schema）。

不给固定天数：S2 组件预览与 S3 试点完成后，按页面复杂度、共享依赖、交互状态数与验收数量拆批估算。

## 6. 分工与协作方式

本项目由一名负责人（用户）+ AI 实施（Claude 主导、Codex 批处理）完成。按"谁决定、谁实施、谁验收"划分：

| 角色 | 职责 | 产物 |
|---|---|---|
| **负责人（用户）** | 产品决策（S0 前五项已于 2026-09-20 决定，见 §22 决策记录；后续新决策同法追加）；每阶段验收签字；真实机型实测；生产部署授权 | 决策记录 §22；实机记录 §20.4 |
| **设计系统实施（Claude）** | S0–S2 全部；每阶段第一个页面（样板页）；组件规格与 `lq-components.md`；后端门禁票 K9；结构性 e2e 更新；每批 before/after 结论 | `static/css/lq/*`、`static/js/lq/*`、宏、React 适配器、测试、文档 |
| **批处理迁移（Codex 任务书）** | 样板页之后的同族页面机械迁移：`LQ.html.*` 替换、状态色替换、`?v=` 改 `asset_url`、spinner/toast/confirm 收敛；任务书写在 `.codex-temp/task-<批次>.md`，每张任务书限定文件清单与禁改清单 | 分支 + PR，附 §21 清单 |
| **验收（Claude 自动化 + 负责人人工）** | 跑 §17 命令；截图矩阵；对照 §18–§20；人工看视觉重心/留白/对齐/层级；实机 | `docs/lq-acceptance.md` 记录命令、基线、结果、未覆盖项 |
| **发布** | 按 `deploy-workflow` 记忆与项目发布门禁；每阶段"具备独立发布条件"而非"必须部署" | 部署记录 |

协作规则：
- 任何页迁移前，先在 `docs/lq-migration-registry.json` 登记（字段见 §15.6），状态从"未盘点"推进。
- 组件缺失时先做组件（含三入口等价测试与 `/dev/lq` 状态），再迁页；不允许"先美化再收敛"。
- 同一发布内，`course_schedule_deck.js`/`classroom-page.tsx` 等正在被其他工作修改的文件要先合并再迁。
- 所有 grep/统计排除 `.codex-temp/`（内有旧基线源码快照）。

## 7. 验收总则与完成定义

五层门禁，逐层通过才算"完成"：

1. **自动化契约**（§17）：构建/类型/单测/e2e/lint/层数审计/对比度探针全绿，命令与基线记录在案。
2. **业务场景**（§18）：B01–B16 对应页面族全过；数据版本、权限、副作用正确；不泄露未公布内容。
3. **视觉与无障碍**（§19）：组件级完整状态矩阵；页面级双视口双角色 + 六套偏好仿真；axe 零 serious；对比度探针；人工看视觉重心、留白、对齐、层级、颜色语义、真实可读性。
4. **性能与兼容**（§20）：交付体积、层数、长任务、CLS、生命周期、服务端请求数；tier B/C 仿真 + 实机记录（未测项标"未测"）。
5. **台账闭合**（§22）：路由级台账状态到"已发布/旧代码可删除"；例外有登记；旧代码删除有消费者清单。

**完成定义**（全项目）：每个路由/片段/独立文档有台账且状态闭合；每个可操作元素有标准组件或登记的领域变体；每个基础组件有规范、实例、状态表、键盘/触控行为、主题与降级；作业编写→成绩、最终材料→导出等关键链有完整通过记录；3D 课表/白板/编辑器/阅读壳保留领域能力且用户内容与产物不被主题重解释；旧代码删除有消费者清单支持；四份实施资料（`lq-components.md`、`lq-action-registry.md`、`lq-migration-registry.json`、`lq-acceptance.md`）定稿。

---

# 第二部分 施工细则

## 8. 令牌规范

唯一定义处 `static/css/lq/tokens.css`（合并 `ui-system.src.css` 第 6、52250、63609 行三处 `:root`）。三层组织：基础色阶/间距 → 语义令牌 `--ls-*` → 组件槽位（组件 CSS 内 `--lq-*` 自定义属性）。页面只选择变体，不定义令牌。

### 8.1 颜色（沿用 + 补齐，HSL 通道）

保留现有 126 个 `--ls-*` 与 86 个 `--ls-c-*`。新增：

```css
--ls-surface-0: 222 47% 97%;   /* 页面底 */
--ls-surface-1: 0 0% 100%;     /* 卡片/表单霜面 */
--ls-surface-2: 210 40% 98%;   /* 卡内嵌区 */
--ls-ink: 222 47% 11%;  --ls-ink-2: 215 25% 27%;  --ls-ink-3: 215 16% 47%;
--ls-line: 214 32% 91%; --ls-line-strong: 215 20% 80%;
--ls-primary-soft: var(--ls-primary) / 0.12;   /* 同理 success .14 / warning .16 / destructive .12 / info .14 */
--ls-ambient-a: var(--ls-primary) / 0.10;  --ls-ambient-b: 173 80% 40% / 0.08;  --ls-ambient-c: 274 48% 47% / 0.06;
```

**色对规则**：每套配色（默认、教师青绿、学生 indigo/sky/mint/violet/rose）在亮与暗各自定义 `primary + on-primary + primary-soft + on-primary-soft`，success/warning/danger/info 同样补全前景色对；禁止把亮色 primary 继承到深色再配深色字。`surface-0/1/2` 与旧 `background/card/popover` 的别名方向：旧 → 新，禁止循环引用。

**角色与个性化**：配色注册表 6 套 `[data-ui-palette="teal|indigo|sky|mint|violet|rose"]`（6 个独立选择器，不写 `="sky"|"mint"`）；`teal` = 现 `.role-teacher` 值（教师默认），`indigo` = 现默认（学生默认），其余 4 套沿用 `user_ui_preferences.css` 现值；每套只覆盖 primary/ring/accent/background/border 及色对（§8.14 给亮暗值）。`.role-teacher` 只负责默认配色（等价于 `data-ui-palette="teal"` 缺省），删除 `.app-topbar.role-teacher` 等 47 处局部覆盖（各页迁移时删）。`--ls-domain-accent` 跟随用户 primary。课程身份色、监考色、状态色、笔迹色与用户主色解耦（§8.14 保证色相不撞）。

### 8.2 玻璃材质（六旋钮，亮/暗/tinted/off 四套）

```css
--ls-glass-blur: 16px;  --ls-glass-saturate: 170%;
--ls-glass-fill: 0 0% 100% / 0.58;  --ls-glass-fill-strong: 0 0% 100% / 0.78;
--ls-glass-line: 0 0% 100% / 0.85;  --ls-glass-rim: 0 0% 100% / 0.95;  --ls-glass-rim-bottom: 0 0% 100% / 0.35;
--ls-glass-sheen: linear-gradient(135deg, hsl(0 0% 100% / .55) 0%, hsl(0 0% 100% / .08) 38%, transparent 60%, hsl(var(--ls-primary) / .05) 100%);
--ls-glass-shadow: 0 20px 60px hsl(215 43% 32% / .10), 0 2px 6px hsl(215 43% 32% / .05);
--ls-glass-shadow-strong: 0 24px 70px hsl(215 43% 32% / .18), 0 4px 10px hsl(215 43% 32% / .08);
--ls-glass-ink: 216 45% 16%;  --ls-glass-muted: 215 18% 43%;
--ls-scrim: 222 47% 11% / 0.28;
```

模糊阶梯（17 档 → 4 档，lint 禁止其他值）：`--ls-blur-thin` 8px（tooltip、小浮标）、`--ls-blur-regular` 16px（顶栏、侧栏、Dock、工具条、popover、菜单）、`--ls-blur-thick` 24px（sheet、modal、灯箱面板、AI 浮窗）、`--ls-blur-scrim` 8px（**默认不用**，scrim 无模糊；仅登记例外时启用）。`saturate` 170%（亮）/140%（暗）/130%（tier B）。

`off` 状态：`--ls-glass-blur: 0`，fill alpha→0.96，**并且所有 `backdrop-filter` 声明实际置 `none`**（不是只改一个变量）。

### 8.3 圆角（同心制）

| 令牌 | 值 | 用在 |
|---|---|---|
| `--ls-r-capsule` | 999px | 按钮、chip、开关、搜索框、Dock、进度条 |
| `--ls-r-xs` | 6px | 复选框、小标签 |
| `--ls-r-sm` | 10px | 输入框、下拉项、侧栏项 |
| `--ls-r-md` | 14px | 小卡、列表项、popover、气泡 |
| `--ls-r-lg` | 20px | 卡片、面板、菜单 |
| `--ls-r-xl` | 28px | sheet、modal、AI 浮窗 |
| `--ls-r-2xl` | 36px | 全屏壳、登录卡、底部 sheet 顶角 |

容器写 `--lq-r-outer` 与 `--lq-pad`，贴边子元素 `border-radius: max(var(--ls-r-xs), calc(var(--lq-r-outer) - var(--lq-pad)))`。旧 `--radius-sm/md/lg/xl/2xl/full` 别名映射保留到 S7。

### 8.4 阴影

```css
--ls-shadow-1: 0 1px 2px hsl(222 47% 11% / .06), 0 1px 3px hsl(222 47% 11% / .04);
--ls-shadow-2: 0 4px 14px hsl(222 47% 11% / .08);
--ls-shadow-3: 0 12px 32px -12px hsl(222 47% 11% / .22);
--ls-shadow-4: 0 24px 64px -16px hsl(222 47% 11% / .30);
--ls-shadow-focus: 0 0 0 4px hsl(var(--ls-ring) / .18);
```
玻璃族用 `--ls-glass-shadow*`。任何组件不得写自己的 box-shadow 字面量。

### 8.5 字体与字号

```css
--ls-font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Noto Sans CJK SC", system-ui, sans-serif;
--ls-font-mono: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace;
--ls-font-serif-cjk: "Songti SC", SimSun, serif;   /* 仅试卷/公文渲染 */
```

| 令牌 | 桌面 | 移动(≤640) | 行高 | 字重 | 用在 |
|---|---|---|---|---|---|
| `--ls-t-display` | 32 | 28 | 1.2 | 700 | 首页/登录唯一大标题 |
| `--ls-t-title1` | 24 | 22 | 1.25 | 700 | 页头 h1 |
| `--ls-t-title2` | 20 | 19 | 1.3 | 600 | 区块标题 |
| `--ls-t-title3` | 17 | 17 | 1.35 | 600 | 卡片/弹层标题 |
| `--ls-t-headline` | 15 | 16 | 1.45 | 600 | 列表主文字、按钮 lg |
| `--ls-t-body` | 15 | 16 | 1.6 | 400 | 正文 |
| `--ls-t-callout` | 14 | 15 | 1.5 | 500 | 按钮 md、表格单元、输入框（**iOS 输入框 ≥16 防缩放**） |
| `--ls-t-sub` | 13 | 14 | 1.45 | 500 | 次级信息、chip |
| `--ls-t-footnote` | 12 | 13 | 1.4 | 500 | 时间戳、表头、帮助文字 |
| `--ls-t-caption` | 11 | 12 | 1.35 | 600 | 图例、Dock 文字、计数徽章（**不用于可点击说明**） |

字重只用 400/500/600/700（650 在 Windows 中文字体下退化）。中文 `letter-spacing: 0`；数字 `tabular-nums`；关键状态文字 ≥13px。旧 `--text-2xs/xs/sm/md/rg` 别名保留到 S7。

### 8.6 间距与宽度

4px 基准 `--ls-s-1…16`：4/8/12/16/20/24/32/40/48/64。栏距 `--ls-gutter: clamp(16px, 3vw, 32px)`；内容最大宽 1280px；阅读页 42–56rem；编辑器按主内容最小可用宽收栏（§11.3）。密度 `comfortable` 默认；`compact` 仅鼠标下的管理表格/评分矩阵，触控与 200% 缩放自动回到 comfortable。

### 8.7 z 轴（8 档，195 处字面量迁入）

`--ls-z-raised` 10 · `--ls-z-nav` 100 · `--ls-z-popover` 1100 · `--ls-z-drawer` 1200 · `--ls-z-modal` 1300 · `--ls-z-viewer` 1400（灯箱/白板全屏，须 ≥ iframe 之上）· `--ls-z-toast` 1500 · `--ls-z-explain` 1600。原生 top-layer 元素不参与此阶梯，由 `LQ.layer` 决定宿主（§10.6）。

### 8.8 动效

```css
--ls-dur-fast: 120ms; --ls-dur-base: 180ms; --ls-dur-slow: 280ms; --ls-dur-enter: 400ms;
--ls-ease-out: cubic-bezier(.2,.7,.3,1); --ls-ease-in-out: cubic-bezier(.4,0,.2,1);
--ls-spring-snappy / -soft / -bouncy: 默认值 = var(--ls-ease-out)；在 @supports (animation-timing-function: linear(0,1)) 内覆盖为用 kvin.me 生成的完整 linear() 串（350ms/0%、500ms/20%、700ms/35%）。占位 linear(/*…*/) 不得进入代码。
```

| 场景 | 动效 | 时长 |
|---|---|---|
| 列表/卡片进入 | opacity + translateY 6→0 | 180ms；stagger 40ms/项、≤10 项、总 ≤400ms |
| 内容切换 | cross-fade | 120ms |
| 按钮按压 | scale .97 | snappy |
| popover/菜单 | opacity + scale .96→1，origin 指向锚点 | soft |
| sheet | translate 100%→0 | soft |
| modal | opacity + scale .98→1 | 220ms |
| Dock 变形 | 同文档 View Transition；退回 opacity | soft |
| 计数徽章 | scale 1→1.15→1 | 300ms |
| reduced-motion | `transition-duration: 1ms`，禁 scale/translate/rotate，保留 opacity；课表不旋转 | — |

`--ux-motion-*` 与 `.ls-anim-*` 以别名指向新令牌，保留到 S7（LessonDoc 编辑器与白板依赖）。

### 8.9 断点（5 档）

`--bp-sm` 640 · `--bp-md` 768 · `--bp-lg` 1024 · `--bp-xl` 1280 · `--bp-2xl` 1536。CSS 写 `@media (max-width: 640px)`（自定义属性不能直接进 media query，值由脚本同步）；JS `LQ.mq.sm/md/lg/xl`。10 种近重复值在各页迁移时归并，不做全站脚本替换。

### 8.10 图标

统一 lucide 几何（24 视口、stroke 1.75、round）。Jinja 宏 `lq_icon(name, size)`（合并 `app_topbar_icon` 21 个与 `manage_icon` 26 个注册表；SVG 由 `tools/ui/build_icon_registry.py` 从 lucide 静态 SVG 生成，**以 devDependency 方式引入生成源，不进运行时**）；JS `LQ.icon(name)`；React `lucide-react`（已有）。313 个手贴 SVG 随页替换。尺寸 16/20/24。

### 8.11 主题与偏好挂载点

```html
<html data-theme="lanshare" data-appearance="light|dark" data-appearance-preference="light|dark|auto" data-lq-glass="tinted|off" data-lq-tier="A|B|C">
<body class="role-teacher|role-student" data-ui-palette="teal|indigo|sky|mint|violet|rose" data-ui-palette-version="…" data-ui-palette-context="…">
```

- `appearancePreference` 与 `resolvedAppearance` 分开；`auto` 由首屏最早同步脚本按媒体查询解析并监听变化（不每次变化写请求）；SSR 输出已保存偏好与身份上下文。
- 三个文档根（`base.html`、`manage/layout.html`、`resume/layout.html`）与 `partials/lq_editor_head.html` 都输出上述属性；Portal（`#lq-layers`）、原生 dialog、同源 iframe 各自明确继承方式（Portal 在 body 内自然继承；iframe 由宿主通过 `postMessage`/同源写入 `documentElement.dataset`）。
- 自动映射：`prefers-reduced-transparency` → off；`prefers-contrast: more` → fill .92 + 2px `hsl(var(--ls-ink)/.6)` 描边；`forced-colors` → 去 backdrop-filter/阴影，边框 `CanvasText`。用户关闭透明/减少动效始终生效，不被局部主题或弹层覆盖。
- 局部内容主题（星图、监控、白板全屏）用 `[data-lq-scope="…"]` 登记作用域。
- 深色令牌值（`[data-appearance="dark"]`）：`--ls-background 224 28% 8%`、`--ls-surface-1 224 22% 12%`、`--ls-surface-2 224 20% 16%`、`--ls-ink 214 32% 94%`、`--ls-ink-2 215 20% 75%`、`--ls-ink-3 215 16% 58%`、`--ls-line 220 14% 22%`、`--ls-primary 239 84% 72%`（教师 173 70% 55%）、`--ls-glass-fill 224 30% 12% / .62`、`-strong .80`、`--ls-glass-line 0 0% 100% / .14`、`rim .18`、`rim-bottom .06`、`--ls-glass-saturate 140%`、`--ls-scrim 0 0% 0% / .5`；`.lq-glass` 追加 `brightness(1.05)`。

### 8.12 设备与能力分级（首屏同步脚本，内联到各文档根 `<head>`）

分别检测：`backdrop-filter`（含 `-webkit-`）、`linear()`、`popover` 属性、`HTMLDialogElement`、`scrollbar-gutter`、`startViewTransition`、`100dvh`、`inert`。`data-lq-tier`：C = 无 backdrop-filter（自动 `data-lq-glass=off`）；A = 全部支持；B = 其余。低端提示：`pointer:coarse` 且（`deviceMemory ≤4` 或 `hardwareConcurrency ≤4`）→ blur regular 12/thick 16、不渲染 ambient 色团；`saveData`/2g → ambient none、动效减半、折射不加载；微信 X5 `Chrome/<96` → C。**设备降级不回写账户偏好**；UA/核数只作辅助提示，未知设备用保守默认。每个新 CSS/JS 特性必须在 §20.2 退路表登记，否则 lint 报错。

### 8.13 语义状态色注册表（K6）

令牌 `--tone-<family>-<state>`（+ `-fg`、`-soft`，亮暗成对）。**族只决定状态→等级，不新增色相**：`success` 完成/已同步/已生效；`warning` 待处理/临近/待审；`danger` 失败/冲突/破坏/过期；`info` 进行中/排队/中性；`neutral` 未开始/草稿/不可用。每个状态必须有文字标签。

| 族 | 状态 → 等级 | 现硬编码位置（迁移替换） |
|---|---|---|
| `assignment` | draft neutral / published success / closed danger | `assignment_detail_teacher.html:1471`、`assignment_detail_student.html:515-521` |
| `submission` | unsubmitted neutral / submitted info / grading info / grading_review warning / grading_failed danger / graded success / returned warning | `assignment_detail_teacher.html:2779-2787`、`submission_detail.html` |
| `submission-flag` | offline neutral / absence-zero danger / late warning / group-pending info / group-final success | `:2793-2797`；`assignment_detail_student.html:663-676` |
| `score` | none neutral / fail(<60) danger / pass warning / good info / excellent primary / top success | `:2393-2398`、`:2878-2880`、`submission_detail.html:921-925`（三处合一） |
| `deadline` | none neutral / regular info / urgent warning / late warning / closed danger | `assignment_time.js:79-115` |
| `save` | dirty warning / local_saved neutral / syncing info / synced success / offline warning / error danger / conflict danger / submitting info / submitted success | `assignment_detail_student.html:377-379`、`exam_take.html #saveStatus`、`lessondoc_editor/index.js:33`、`resume_builder.js:516` |
| `job` | queued neutral / retry_wait warning / running info / result_ready success / superseded neutral / failed danger / canceled neutral | `ai_jobs`、`exam_editor.html:1341-1372`、`resume_list.js:358`、导出任务 |
| `agent` | queued neutral / running info / waiting_input warning / question_expired danger / unverified warning / partial warning / committed info / completed success / failed danger / canceled neutral | `ai_workspace_widget.js:845-867`；FAB `:684-688` |
| `signature` | neutral / pending warning / partially_approved warning / approved success / rejected danger / cancelled neutral / superseded neutral；区域 updating info / dirty warning / confirmed success | `signature_point_workflow.js:4-11,186-194`、`.css` |
| `attachment` | image/doc/sheet/slide/pdf/archive/other：固定 7 识别色（`--ls-c-*`） | `assignment_detail_teacher.html:737-743` |
| `course` | 1…10 固定色（现 `COURSE_PALETTE`） | `course_schedule_deck.js:34-37`；学生首页 C1 并入 |
| `agenda` | proctor amber / exam red / homework violet / todo sky / class teal | 已令牌化 |
| `monitor` | accent/good/warn/bad/muted（`[data-lq-scope="monitor"]` 专用） | `manage_system_monitor.js:11-26` |

接入：`lq_status(family, state)` 宏、`LQ.tone(family, state)`、`<LqStatus>`；`lq-chip--status`/`lq-badge` 只接受 `data-tone`；缺失 → neutral + DEBUG 警告。lint：已迁页面出现 `badge-(primary|warning|success|danger|secondary)` 或状态相关 hex → 阻断。

### 8.14 五个语义等级与身份色的定稿值（2026-09-20 决定）

**取值原则**：五个等级的色相在色轮上彼此相距 ≥40°，并避开六套用户配色的主色相带（teal 175、sky 199、mint ≈160、indigo 243、violet 262、rose 347），使状态 chip 与选中态/主按钮在任何配色下都不同色；饱和度比 Tailwind 默认低 10–20%，在霜面与玻璃上不刺眼；每个等级给 `base`（dot/进度/图标）、`fg`（文字，白底 ≥4.5:1）、`soft`（chip/行底）三档，亮暗成对。`info` 与 `sky` 配色相距 13°，靠形态区分：状态 chip 恒有文字 + 实色 dot，选中态用 tint 无 dot；这是允许的最小距离，其余组合均 ≥25°。

| 等级 | 亮 base | 亮 fg | 亮 soft | 暗 base | 暗 fg | 暗 soft | 用途 |
|---|---|---|---|---|---|---|---|
| `success` | 152 58% 40% | 152 62% 27% | 152 58% 40% / .13 | 152 52% 56% | 152 55% 78% | 152 52% 56% / .20 | 完成 / 已同步 / 已生效 / 已批改 |
| `warning` | 34 92% 50% | 30 88% 32% | 34 92% 50% / .16 | 38 94% 62% | 40 95% 80% | 38 94% 62% / .20 | 待处理 / 临近 / 待审 / 待重交 |
| `danger` | 4 74% 54% | 4 68% 38% | 4 74% 54% / .12 | 4 82% 68% | 4 88% 82% | 4 82% 68% / .18 | 失败 / 冲突 / 破坏 / 过期 / 缺交 |
| `info` | 212 86% 48% | 212 82% 33% | 212 86% 48% / .13 | 212 88% 66% | 212 90% 82% | 212 88% 66% / .20 | 进行中 / 排队 / 中性提示 |
| `neutral` | 220 12% 50% | 220 16% 34% | 220 12% 50% / .10 | 220 10% 62% | 220 12% 80% | 220 10% 62% / .16 | 未开始 / 草稿 / 不可用 / 已取消 |
| `primary` | = 当前配色 primary | on-primary | primary / .12 | 同左（暗值） | | | 选中 / 主操作 / `score-excellent` |

现有 `--ls-success 160 84% 39%`、`--ls-warning 38 92% 50%`、`--ls-destructive 0 84% 60%`、`--ls-info 199 89% 48%` 在 S1 改为指向上表 base（别名），S7 删除别名。`--ls-info` 原值 199° 与 `sky` 配色同色相，是本次调整的主因。

**用户配色 6 套**（primary 亮 / 暗；其余色对由 primary 派生：on-primary 白或 `--ls-ink`，soft = primary / .12，暗 soft / .18）：

| key | 亮 primary | 暗 primary | 默认角色 |
|---|---|---|---|
| `teal` | 175 77% 26%（现 `.role-teacher`） | 173 62% 52% | 教师 |
| `indigo` | 243 75% 59%（现 `--ls-primary`） | 239 84% 72% | 学生 |
| `sky` / `mint` / `violet` / `rose` | 沿用 `user_ui_preferences.css` 现值 | 亮度 +14%、饱和 −10% | — |

**身份色（不随主题变色相，暗色只提亮）**：课程 `--tone-course-1…10` 沿用 `COURSE_PALETTE` 现值转 HSL：243 75% 59% / 199 89% 48% / 160 94% 31% / 32 95% 44% / 336 74% 51% / 262 83% 58% / 192 91% 36% / 84 81% 35% / 21 90% 48% / 347 77% 50%（暗色 L +14%）。附件 `--tone-attachment-*`：image 199 / doc 221 / sheet 152 / slide 21 / pdf 4 / archive 262 / other 220 12%（S 各 70%、L 46%，暗色 L 62%）。议程 `--tone-agenda-*` 沿用现值。监控 `--tone-monitor-*` 沿用 `manage_system_monitor.js:11-26` 现值。

验收：`contrast_probe.cjs` 在 `/dev/lq` 对每个等级的 fg-on-surface-1、fg-on-soft、白字-on-base 三组各测亮/暗，全部 ≥4.5:1（大字与 UI ≥3:1）；六套配色 × 五等级的 chip 与选中态并排截图，人工确认无同色。

### 8.15 偏好模型（K10，学生与教师）

| 项 | 取值 | 默认 | 作用域 |
|---|---|---|---|
| `palette_key` | 6 套 key（§8.14） | 教师 `teal`、学生 `indigo` | 该用户渲染的**所有页面**（三个文档根 + 五个独立编辑器 + 岛屿 Portal）；取代 ui-v3 的"仅学生首页/课堂页"作用域，`resolve_user_ui_preferences` 删除 `_LEARNING_PAGE` 路由过滤 |
| `appearance` | `light / dark / auto` | `auto` | 全站；`auto` 由首屏同步脚本解析 |
| `glass` | `tinted / off` | `tinted` | 全站；设备 tier C 强制 off 但不回写 |

- **API**：`GET/PATCH /api/profile/ui-preferences` 保留路径；依赖改为 `get_current_preference_user`（学生或教师，超管按教师）；schema 三字段均可选，**字段级更新**（旧客户端只发 `palette_key` 不清空其余）；`X-UI-Preferences-Context` 身份 token 与 CAS `version` 机制不变；同字段冲突不自动覆盖。
- **表**：`palette_key` 校验集合加 `teal`；新增 `appearance TEXT NULL`、`glass TEXT NULL`（NULL = 默认），增量迁移可空，SQLite/PG 双路径，旧服务忽略新列。
- **SSR**：`base.html`、`manage/layout.html`、`resume/layout.html`、`partials/lq_editor_head.html` 统一经 `partials/lq_theme_attrs.html` 输出 `data-appearance-preference / data-lq-glass / data-ui-palette(-version/-context)`；`user_ui_preferences.css` 并入 `tokens.css`。
- **UI**：个人资料"外观"分区（§13.12）三个控件对学生与教师相同；教师配色默认 `teal`，可切换其余 5 套。
- **契约更新**：`home-classroom-ui-v3` 中断言配色只作用于学生首页/课堂页的用例改为断言全站生效；`tests/test_*ui_preferences*` 增加教师用例与字段级更新用例。

## 9. 材质与分层

### 9.1 四种材质类（唯一允许的表面类）

| 类 | 关键值 | 允许用在 |
|---|---|---|
| `lq-glass` | `background: hsl(var(--ls-glass-fill)); backdrop-filter: blur(var(--ls-blur-regular)) saturate(var(--ls-glass-saturate)); border: 1px solid hsl(var(--ls-glass-line)); box-shadow: var(--ls-glass-shadow), inset 0 1px 0 hsl(var(--ls-glass-rim)), inset 0 -1px 0 hsl(var(--ls-glass-rim-bottom)); isolation: isolate;` + `::before` sheen（`pointer-events:none`，自身定位，`mix-blend-mode: screen`）。**不设 `contain: paint`、不设 `will-change`、不强制直接子元素 `position: relative`**（按宿主局部采用；iOS 玻璃层不加 will-change，桌面 Chromium 才加） | 顶栏、侧栏、Dock、工具条、FAB、popover、菜单、浮窗 |
| `lq-glass--thick` | blur thick，fill strong | sheet、modal、drawer、AI 浮窗、灯箱面板 |
| `lq-glass--clear` | fill alpha .22，必须与 `lq-scrim` 同用，文字白 | 灯箱、（对比实测通过的）背景图登录卡、学习文档全屏壳 |
| `lq-surface` | `background: hsl(var(--ls-surface-1) / .96); border: 1px solid hsl(var(--ls-line) / .8); box-shadow: var(--ls-shadow-1); border-radius: var(--lq-r-outer, var(--ls-r-lg));` **无 backdrop-filter**；`--frost` 修饰加 blur thin，仅首屏英雄卡与 3D 课表当前卡 | 卡片、面板、表格容器、表单区、列表、气泡 |

辅助：`lq-scrim`（`background: hsl(var(--ls-scrim))`，**无模糊**）、`lq-ambient`（`body::before` 固定定位 `inset:0` 三径向渐变；色团仅桌面 tier A 且非 reduced-motion 才动画；考试、编辑器、监控、白板页静态）、`lq-scroll-edge`（顶栏底部 24px 渐变，`data-scrolled` 时显示；数据表格 sticky thead 用不透明 hard edge）。

每条 `backdrop-filter` 必须同时写 `-webkit-backdrop-filter`，并放在 `@supports` 内，`@supports not` 分支给 `hsl(var(--ls-surface-1) / .96)`。

### 9.2 四层模型（每页在台账里画出来）

```
L3 弹层     lq-scrim + lq-glass--thick   z ≥ 1200
L2 导航控制 lq-glass                    z 100–1100
L1 内容     lq-surface                  z 0–10
L0 环境     lq-ambient                  z -1
```

普通页持续宿主 ≤2；L3 打开后总数 ≤3（按真实渲染计，scrim/toast 不模糊所以不计）。超限时先去掉子控件与 scrim 模糊，再降背景壳材质。

### 9.3 明暗自适应（tone）

小型玻璃控件（Dock、FAB、灯箱按钮、登录卡）支持 `data-lq-tone="dark"`，由 `LQ.tone.observe(el)` 采样背后**已知可访问的背景图**（复用 `sampleImageTone`，处理 CORS 失败并缓存，不循环截屏 DOM）。侧栏/顶栏不翻转。

## 10. 组件规范

约定：状态类 `is-active/is-selected/is-open/is-loading/is-disabled/is-invalid/is-empty`；尺寸 `--sm/--md/--lg`；色调 `data-tone`；交互态顺序 默认 → hover（`@media (hover:hover)`）→ focus-visible → active → disabled。文件：CSS `static/css/lq/components/<c>.css`，宏 `templates/macros/lq/<c>.html`，JS `static/js/lq/<c>.js`（挂 `window.LQ`，ESM 可 import；老页面通过就绪协议 `LQ.ready(fn)` 使用），React `frontend/src/components/lq/<C>.tsx`（只为实际有消费者的组件实现）。

### 10.1 按钮 `lq-btn`

DOM：`<button type="button|submit" class="lq-btn lq-btn--prominent lq-btn--md"><span class="lq-btn__icon"></span><span class="lq-btn__label">开设课堂</span><span class="lq-btn__badge">3</span></button>`；`<a>` 同类名（导航，真实 href）；图标按钮 `--icon` + `aria-label`。

| 变体 | 底 | 字 | 每活动区域上限 | 用途 |
|---|---|---|---|---|
| `--prominent` | `hsl(var(--ls-primary))` + 顶高光 | on-primary | 1 | 唯一主操作 |
| `--glass`（玻璃宿主上的默认） | `hsl(0 0% 100% / .5)`（**无自建 blur**）+ `--ls-glass-line` | glass-ink | 不限 | 工具条/顶栏/浮层普通操作 |
| `--soft` | `--ls-primary-soft` | on-primary-soft | 不限 | 内容区次级操作 |
| `--ghost` | 透明，hover `--ls-ink/.06` | ink-2 | 不限 | 取消、更多、关闭 |
| `--destructive` | danger-soft；确认弹层内主按钮实色 danger | danger 色对 | 1 | 删除、撤回、清空 |
| `--link` | 无 | primary，hover 下划线 | 不限 | 行内命令 |

尺寸：`--sm` 32/13px/图标 16 · `--md` 40/14px/18 · `--lg` 48/16px/20 · `--icon` 正方形；`pointer:coarse` 一律 ≥44 有效目标（用 `min-height` 或布局留白，不用会重叠的伪元素）。胶囊；图标文字间距 6；`min-width 64`；文案推荐 ≤4 字，关键动作可换行不省略。

状态：hover glass fill +.12 / prominent brightness 1.06 / soft .12→.18；active scale .97（snappy）；focus-visible `outline: 2px solid hsl(var(--ls-ring)); outline-offset: 2px`；disabled 原生 `disabled`（确不可操作）或 `aria-disabled`（需解释原因时，同时阻止键鼠执行）+ `opacity .45`；loading `is-loading` 保留可访问名称与占位（不 `visibility:hidden`），`aria-busy`，内含 `lq-spinner--sm` 与"保存中"文本或关联状态。表单主提交 `type=submit`，其余 `type=button`。

接入：`lq_btn(label, variant, size, icon, href, id, attrs, badge, loading, type)`；`LQ.btn({...})` → Element；`LQ.html.btn({...})` → 字符串（**转义文本与属性、校验 href**，不提供任意 HTML 旁路）；`<LqButton>`。

禁忌：非玻璃容器用 `--glass`；自定义按钮颜色；`<div onclick>`；两个 prominent 并排；用 `pointer-events:none` 代替禁用。

### 10.2 芯片 `lq-chip`（filter / status / tag）

filter：`<button class="lq-chip lq-chip--filter" aria-pressed>`，高 28（sm 24），13/600，胶囊，默认 `surface-1/.7` + line 描边，选中 tint（primary/.14 底 + primary 字 + primary/.35 描边）；status：`<span class="lq-chip lq-chip--status" data-tone>`，soft 底 + 深色字 + 8px dot；tag：可删（`__remove` 按钮，44 触控）。玻璃宿主上自动切玻璃底（无自建 blur）。`lq-chip-row`：横向滚动 + scroll-snap + 渐隐遮罩；>8 个收进"更多 (N)"。`manage_filter_chips.js` 的 `data-filter-chips` 代理 `<select>` 机制原样保留。静态状态 chip 不加 `role=status`。

### 10.3 分段控件 `lq-segment` 与标签页 `lq-tabs`

`lq-segment`：2–5 个**互斥视图切换**（`role=tablist`/`tab`），高 36（sm 30），胶囊底 `ink/.06`，白色 thumb 用 snappy 平移（JS 写 `--lq-thumb-x/w`），键盘 ←/→。**不用于**：radio 语义（用 `lq-radio` 组）、命令按钮（如上周/下周）、值选择（用 select/listbox）。
`lq-tabs`：≥3 个内容面板，可滚动、可徽章，2px 指示器，`role=tab/tabpanel` + `aria-controls`，面板 120ms cross-fade **不重挂内容**；`--pill`（Dock 内）、`--vertical`（资料页）；`LQ.tabs(root, {persist, hash})` 替换 8 套实现。

### 10.4 导航壳组件

- **顶栏 `lq-topbar`**：`lq-glass` sticky（**不用 fixed**，iOS），高 56（移动 52），三区 `__lead/__title/__actions`（≤3 个 `lq-btn--glass --sm` + 更多菜单 + 头像）；滚动 >80px `is-condensed`（48）；`lq-scroll-edge`；`view-transition-name: lq-topbar`；`--immersive` 变体（返回/标题/页内操作，`data-lq-lock-nav` 时无站点导航）。
- **侧栏 `lq-sidebar`**：`lq-glass` fill strong，264px；`--rail` 72px；≤1024 变 `lq-drawer`；`__brand/__search(⌘K)/__nav(域手风琴，只开一个)/__user`；`lq-nav-item` 40px、圆角 sm、选中 tint + 左 3px 胶囊指示条（snappy 滑动）。导航数据继续来自 `manage_nav_service.py`（不改注册表结构）；学生端导航项共享 schema 与呈现组件，但不硬塞入该服务。
- **Dock `lq-dock`**：`lq-glass` 胶囊，fixed bottom `max(12px, env(safe-area-inset-bottom))`，居中用 `left:12px; right:12px; margin:0 auto; width:fit-content`（不用 translateX），高 56/48，≤5 项（多的进"更多"sheet），选中 thumb；软键盘弹出（`visualViewport` 高度缩小 >150px）`is-hidden`，**但页面须保留该 Dock 承载的唯一主操作的等价入口**。替换 `partials/app_bottomnav.html` 与课堂活动 tab 条。
- **FAB `lq-fab`**：56px 圆 `lq-glass`（`--prominent` 主色，`--sm` 40），右下 `bottom: calc(Dock高 + 16px)`，展开 = `lq-menu`；最多 3 个竖排。
- **面包屑 `lq-crumbs` / 步骤条 `lq-steps`**：13/500，chevron 14；移动端只显示上一级。步骤条节点 28px 胶囊，完成 success / 当前 tint 脉冲一次 / 未来 line-strong；≤640 纵向。

### 10.5 内容组件

- **卡片 `lq-card`**：`lq-surface`，`--lq-r-outer 20`，`--lq-pad 16（lg 24）`；`__head/__title(17/600)/__meta/__actions/__body/__foot`。`--interactive`：**主动作由原生 `<a>`/`<button>` 承载**（标题链接 + `::after` 铺满卡面或整卡 `<a>`），附加操作是并列控件放在 `__actions`（focus-within/触控常显），**不嵌套交互元素、不用 `stopPropagation` 补救**；hover translateY(-2px) + shadow-2。`--flat`（无阴影无 hover）、`--stat`（数字 28/700 tabular + 标签 13，可选 count-up，HTML 初值为真）、`--hero`（每页 ≤1，可 frost）、`--empty`。禁忌：卡套卡、卡内玻璃、卡当分区背景。列表 >12 项建议 `--flat`（项目取舍，可说明理由）。
- **列表 `lq-list` / 行 `lq-row`**：行高 ≥56，`__lead(36)/__main(15/500 + 13 ink-3)/__trail`；分隔线从 `__main` 起；未读 = 左 3px 主色条 + 600；`--swipe` 移动滑出 destructive；容器圆角 20 `overflow: clip`；分组标题 sticky 12/600。
- **表格 `lq-table`**：容器 `lq-surface` `overflow: clip`；thead sticky 不透明 hard edge；th 12/600 ink-3；td 14，行高 48（`--dense` 40，仅鼠标）；去斑马纹；数字右对齐 tabular；排序 `arrow-up-down`；分页 `lq-pager`；批量选择 `lq-table__select` + `lq-bulk-bar`；结果计数 `lq-result-count`。**≤768：记录型列表转卡片（`data-label`）；成绩对照、课表、评分矩阵保留横向滚动（容器 `overscroll-behavior: contain`，页面本身不横溢）并保留表头关联、排序、批量选择**。空/错误/离线分别用 `lq-empty` 三种变体。
- **表单**：`lq-field`（标签 13/600 ink-2 在上，必填 `*`，帮助 12 ink-3，错误 12 danger + 图标 `aria-describedby`），`lq-error-summary`（提交后列出并定位第一个错误，保留输入），`lq-form-actions`，`lq-form-section`（15/600 + 描述，间距 32）。控件：`lq-input` 40/48/32、圆角 sm、focus 描边 ring 1.5 + shadow-focus、前后缀槽、清除；`--search` 36 胶囊 + ⌘K；`lq-textarea` min 96 自增 + 字数；`lq-select` = **原生 `<select>` 保留为值载体与默认实现**，`lq-combobox/lq-listbox` 按 WAI-ARIA combobox/listbox 模式单独实现（不用操作菜单代替）；`lq-switch` 31×51 `role=switch`；`lq-checkbox` 20 圆角 xs；`lq-radio` 20 圆（原生 radio 语义，一组 ≤4 也不改用 segment）；`lq-slider` 轨 4 thumb 24 + 值气泡；日期/时间 = `ls_date_picker.js` 面板换 `lq-popover` 皮（逻辑不动，注册进 `LQ.layer`）；`lq-dropzone` + `lq-upload`（§10.9）。禁用 `opacity .5`；只读底透明 dashed 下划线。Enter 发送尊重 `isComposing`；多行允许换行。
- **空态 `lq-empty`**：`--inline`（48px 一行：图标 + ≤16 字 + 可选 soft sm 按钮，零值默认档）、`--card`（120px）、`--page`（首次引导）；**变体按原因区分**：`data-reason="empty|no-results|error|forbidden|offline"`，`no-results` 保留清除筛选，`error` 不伪装为"暂无数据"并给重试。父容器 `is-empty` 收起 chrome。
- **页头**：升级现有 `page_head(title, description, explain, explain_label, actions, eyebrow, title_id)` 宏内部 DOM 为 `lq-page-head`（标题 24/700 左对齐；右侧 ≤1 prominent + 1 soft + 更多菜单；`explain_button`；描述 14 ink-3；`__aside` 放 ≤3 个 `lq-card--stat`）。**宏名、参数、`data-page-head`、`.page-head__copy/__desc/__aside/__actions` 保留**；`eyebrow` 参数保留但不再填充。`empty_state`、`filter_bar` 同法升级。
- **图表基元 `lq-insight`**：沿用 `insight_ring/bars/meter` 宏，改类名 `lq-ring/lq-bars/lq-meter`，色调 `data-tone`，零数据不渲染（保留 `is-empty` 降级）。
- **富文本 `lq-prose`**：15/1.7；代码块 surface-2 圆角 12 mono 13 + 复制按钮；行内代码 tint；表格 `lq-table--dense`；图片接灯箱；引用左 3px 主色。只负责排版；HTML 由 `markdown_runtime.js` 安全渲染，`LQ.html.*` 必须转义。
- **气泡 `lq-bubble`**：己方 primary-soft 底 + ink 字，对方 surface-2；圆角 18，连续消息同侧角 6；最大宽 72%（移动 85%）；时间 11 ink-3（触控常显）；图片接灯箱（程序式 API，见 §10.9）。composer `lq-glass` 胶囊固定底（输入自增 ≤6 行，附件/表情 icon，发送 `lq-btn--prominent --icon`，局部主操作）。

### 10.6 弹层协调层 `LQ.layer` 与五种弹层

以 `static/js/ui_popover.js`（已有栈、嵌套父解析、Tab 圈闭、Esc、backdrop、焦点回归、reduced-motion）扩展为 `static/js/lq/layer.js`，`createPopoverSystem` 继续导出（白板 `twb` 与 LessonDoc 不改）。

API：`LQ.layer.open(el|html, {type:'modal'|'sheet'|'drawer'|'popover'|'menu', modality:'modal'|'non-modal', anchor, parent, size, returnFocus, beforeClose, onClose})` → handle；`close(handle, reason)`（reason ∈ `button|escape|outside|back|parent-destroyed|programmatic`）；`closeTop(reason)`；`closeAll()`；`top()`。登记字段：`owner/root/trigger/modality/parentLayer/returnFocus/closeReason/beforeClose`。

行为：初始焦点（`[data-autofocus]` → 第一个可聚焦 → 面板）；关闭后焦点回触发器或合理后继；modal 使背景 `inert`（不支持时 `aria-hidden` + tabindex 遍历 + 指针拦截 + 恢复）；popover/menu 不全局锁滚；滚动锁 refcount 在协调层，iOS 用 `body{position:fixed; top:-scrollY}` 记录恢复，`scrollbar-gutter: stable` 或 `--lq-scrollbar-w` 防跳；脏表单 `beforeClose` 可否决；只关最上层；允许有父子关系的子层（日期面板、选择器、说明浮窗、灯箱、确认）；原生 `<dialog>`/popover 属性作为**增强宿主**（检测通过时用 top-layer，否则挂到 `#lq-layers`，无 transform 祖先）；body Portal 不得用更大 z-index 穿过原生模态层；`close()/destroy()` 幂等，清监听/observer/定时器，取消迟到异步回写，关闭动画有超时兜底；`?open=` 深链。

| 类型 | 类 | 材质 | 尺寸/位置 | 动效 |
|---|---|---|---|---|
| 弹窗 | `lq-modal` | `lq-scrim` + `lq-glass--thick`，圆角 28 | `min(560px, 100vw-32px)`（`--lg` 800、`--xl` 1080、`--full`）；≤640 变底部 sheet | scale .98→1 220ms |
| sheet | `lq-sheet --bottom/--right` | 同上，顶角 36 / 左角 28 | bottom 高 auto max `min(92dvh, 100vh - env(safe-area-inset-top))` + 拖柄；right `min(480px, 100vw)` | soft translate |
| 抽屉 | `lq-drawer`（`--wide` 960） | `lq-glass--thick` | `min(640px, 100vw)` | 280ms |
| 浮层 | `lq-popover` | `lq-glass` 圆角 20 | 锚定四向 + 12px 视口边距，宽 auto max 360 | soft scale .96→1 |
| 菜单 | `lq-menu`（`role=menu`） | `lq-glass` 圆角 16 | min 200；项 40/14/图标 18；分组线；风险组 danger 且与常用项有间距 | 同 popover |
| 确认 | `lq-confirm`（modal sm 360） | 同 modal | 标题 17/600 + 正文 14 + 取消 ghost / 确认 prominent 或 destructive；初始焦点在安全动作 | 同 modal |
| 三态 | `LQ.choose` | 同 modal | ≤3 选项；Esc/遮罩 = `dismissed`（等同返回） | 同 modal |
| 提示 | `lq-tooltip` | `lq-glass` blur thin，圆角 8 | 12/500，延迟 400ms（键盘即时）；仅图标按钮名称；功能说明一律 `data-explain` | 120ms |

弹层结构 `__head`（17/600 左对齐 + 关闭 ghost icon）→ `__body`（滚动区 20/24）→ `__foot`（右对齐，移动端等宽堆叠主按钮在下；破坏确认写明对象、数量、后果、可恢复性；结果以服务端确认为准）。同步 `confirm()` 改 Promise 时事件先 `preventDefault()`，调用者必须 `await`，重复点击加锁，取消清理。

迁移路径（不一次替换）：`ui.js openModal/closeModal` 保留签名内部转 `LQ.layer`（S2 桥）；`poll-/collab-/ga-` overlay、`materials-editor-shell`、`rz-` modal、`lde-` dialog 在各自页族迁移时接入；课表 `.cs-expand` **不接入**（可移植契约），登记为"外部层"保证 Esc 顺序；日历 todo modal S4 接入；原生 `<dialog id="assignment-kind-modal">` 登记进栈；Radix Dialog 经 `useLqLayer()` 适配后删除；`ls_date_picker` 注册 popover 类型；灯箱注册 viewer 类型；`ui_explanation.js` 不接入（`closeTop` 时若打开先关它）。

### 10.7 通知 `lq-toast` 与状态播报

唯一实现 `LQ.toast(message, {tone, duration=3000, action, icon})`（在 `ui.js showToast` 上重写；`window.showToast/showMessage` 保留别名；删除其余 8 个实现；React 调 `window.LQ.toast`）。容器 `#lq-toasts` 右上（移动顶部居中，Dock 页避让）；单条 `lq-surface`（**不模糊**）圆角 16 高 ≥48，图标 tone 色，14/500 ≤60 字，可选一个 link 动作；≤3 条堆叠；hover 暂停；`role=status aria-live=polite`（danger `assertive`）；去重。**不用 toast**：表单校验（就地）、后台任务完成（`lq-job`）、每次轮询状态变化。

### 10.8 徽标/头像/进度/骨架/spinner

`lq-badge`（18px 胶囊 11/700，`--dot` 8px，0 不渲染）；`lq-avatar` 24/32/40/56（首字 + hash 6 色 soft 底；`lq-avatar-stack` −8px ≤4 + "+N"）；`lq-progress` 轨 6 胶囊 + `--ring`（沿用 `.insight-ring` SVG）；`lq-spinner` 16/20/32 2px 环 800ms（**替换约 40 种**）；`lq-skeleton` 流光 1.6s（reduced-motion 静止）。

### 10.9 业务组件（原案缺、业务链必需）

| 组件 | 规格要点 |
|---|---|
| **`lq-status`（SaveStatus）** | `<span class="lq-status" data-tone="save-synced" role="status" aria-live="polite">dot + label + time + 可选 link action</span>`；`save` 族 9 态；`conflict/error` 必带 action 且常显；**只在跨等级变化时更新 live 文本**（syncing↔synced 不播报）；高 28 胶囊 13/600；组件只有 `set(state,{time,action})`，状态机与请求队列在页面 controller |
| **`lq-clock`（DeadlineClock）** | 承接 `assignment_time.js` 全部 `data-*` 契约，逻辑不动；`deadline` 族；绝对时间**常显**；`urgent` dot 脉冲一次；`--compact`（考试）`mm:ss` 固定宽 tabular，变色不改布局 |
| **`lq-job`（JobStatus）** | `__head`(lq-status + elapsed) + `lq-progress` + `__msg` + `__actions`(刷新/中断/查看结果/应用)；`job`/`agent` 族；轮询/SSE 由业务 controller 持有（`ai_workspace_widget.js:1959` SSE + 轮询退路保持）；`superseded` 灰显并说明；不用 toast 表达完成 |
| **`lq-nav-grid`（QuestionNavigator）** | `<nav aria-label>` + `<button aria-current aria-label="第3题，已作答">`；`is-answered`(tint + ✓) / `is-current`(实色) / `is-flagged`(warning) / `is-error`(danger + !) / `is-pending-upload`(info dot)；32px（coarse 40）；桌面 sticky rail 240、移动 `lq-sheet--bottom`；`submission-jump-nav` 岛输出同类名 |
| **`lq-editor`（EditorShell）** | 见 §11.3 |
| **`lq-upload`（UploadQueue）** | `lq-dropzone` + `__list`(`lq-file-chip` 每项 `data-file-state`: selected→validating→rejected(原因常显)→uploading(progress)→uploaded(服务器确认)→failed(重试)→removing) + `__policy`（类型/大小/数量限制常显）；队列级 idle/busy/partial-failed；重复截图被拒必须显示原因与归属题号；`draftSyncChain`/`questionDraftUploadTimers` 逻辑不动 |
| **`LQ.choose`** | 见 §10.6 |
| **`lq-split` / `lq-viewer`** | `grid-template-columns: var(--lq-split, 280px) 1fr`，分隔条可拖（键盘 ←/→ 8px）；<1024 上下堆叠 + segment；viewer 工具条 `lq-glass` 浮顶，**iframe 场景工具条不叠在 iframe 上** |
| **灯箱 `lq-lightbox`** | 改类名同时保留：`data-ls-lightbox[-src|-group|-title|-scope]` 声明式；`ensure/isOpen/open(items,index)/close` 程序式 API（`chat_image_preview.js` 委托）；`.ls-glass/.ls-glass-pill` 别名；材质 `lq-glass--clear` + `lq-scrim`；z `--ls-z-viewer` |
| **`lq-dirty-guard`** | `beforeunload` 原生机制保留 + 站内导航 `beforeClose`；不拦截原生表单提交 |
| **`lq-conflict`（ConflictNotice）** | 常显冲突区：保留本地版本、"重新核对"加载服务器状态、不自动覆盖、不无限重试 |
| **`lq-alert`** | 页内常驻提示（error/warning/info），用于 409/400/权限/约束，不淡出 |

### 10.10 已有单例只换皮不换契约

`ui_explanation.js`（`data-explain*`、`explain_attrs/explain_button` 宏、一页一个 DOM、≤8KB、`attach/register`、`help_text`）；`ls_date_picker.js`（自动接管原生输入、`data-dp-pair`、`data-ls-native`）；`ls_image_lightbox.js`（§10.9）；`prompt_pool.js`；`agent_user_confirmation.js`（三级确认模态：快照区、勾选、备注；`agent-user-confirmation.spec.ts`）；`approval_workflow.js`（launcher/drawer、`DETAIL_RENDERERS/DECISION_FORMS`）；`signature_point_workflow.js`（`SignaturePointControl` 四区域态、selected/confirmed 区分、"申请并应用签名"/"确认并更新文档"互斥）。

## 11. 壳、布局与页面骨架

### 11.1 四种布局（K5）

| 布局 | 原型模板（演进，不新建） | 页面 |
|---|---|---|
| `sidebar` | `manage/layout.html`（含 `dashboard_teacher.html`、拆分后的 `manage/me/profile.html`） | 管理域、教务、教师首页、教师"我的" |
| `topbar` | `base_navbar.html` | 学生首页、消息、博客、成长页、学生资料 |
| `immersive` | `base.html` 派生的课堂主页 / `material_render_shell` / 监控（`[data-lq-scope]`）| 课堂主页、材料阅读全屏壳、白板全屏、监控 |
| `centered` | `base_centered.html` | 登录、注册、错误、状态、会话过期、权限 |
| `editor`（独立文档） | `partials/lq_editor_head.html` + 各自 `<html>` | 试卷编辑、考试作答、教案、考核方案、教师评学 |

三个文档根共享：`partials/lq_head_assets.html`（ui-system、`lq/index.js`、`ui_explanation`、`ls_date_picker`、灯箱、`vite_islands`）、`partials/lq_topbar.html`、`partials/lq_sidebar.html`、`partials/lq_dock.html`、`#lq-layers`、`#lq-toasts`、身份/主题属性输出、skip link（`<a class="lq-skip" href="#main">跳到主内容</a>`，现无）。`embedded_mode` 保留只输出 `lq-content`。`resume/layout.html` 在简历族迁移时改为 `sidebar` 布局（前置：`rz-` modal 先接 `LQ.layer`）。

ShellContract 登记（每个根）：head 资产与顺序、SSR 身份/主题属性、语言/viewport/标题、skip link、main、导航当前态、角色/资源权限、顶部高度与底部安全区变量（`--lq-topbar-h/--lq-dock-h`，正文补偿实际遮挡）、页面 JSON 数据与转义、脚本入口、island 根、mount/destroy、一次性监听、embedded/独立/登录异常/全屏/打印能力。

### 11.2 页面骨架（每页套用其一，登记在台账）

| 骨架 | 结构 |
|---|---|
| 列表页 | page_head → filter_bar（搜索 + ≤3 chips 组 + 更多筛选 sheet）→ `lq-table` 或 `lq-grid--cards` → pager |
| 总台页 | page_head(+aside stats) → 2–3 个 `lq-section`（20/600 标题）→ 右栏（≥1280，`minmax(300px,360px)`） |
| 详情页 | topbar 返回 + 标题；左主内容 sections + 右 sticky 侧栏（摘要卡 + 操作） |
| 编辑器页 | `lq-editor` 三栏（§11.3） |
| 作答页 | `lq-editor` 变体 `take`（§13.3） |
| 沉浸工作台 | immersive；主区 + 右栏/底部 Dock 切换活动面板（单实例） |
| 阅读页 | topbar；正文 42rem `lq-prose`；右侧浮动 TOC popover |

栅格 `lq-grid` 12 列 gap 16（≤640 gap 12）；卡片网格 `repeat(auto-fill, minmax(280px, 1fr))`；区块间距 32，区块内 16。每工作区一个主滚动轴；主区与侧栏独立滚动时标明边界。

### 11.3 编辑器骨架 `lq-editor`（五个独立文档共用）

```
<html data-appearance …><head>{% include "partials/lq_editor_head.html" %}</head>
<body class="lq-editor" data-lq-editor="exam|take|lesson-plan|assessment|evaluation">
  <header class="lq-editor__bar lq-glass">   左：返回 / 标题（可编辑）  中：lq-status + 只读 chips  右：≤3 glass sm + 1 prominent + 更多
  <aside class="lq-editor__rail lq-surface">  280，可折叠到 0；lq-segment 切换（目录 / 设置）
  <main class="lq-editor__main lq-surface">    min 640；主区永远 ≥320 可编辑宽
  <aside class="lq-editor__aside lq-surface">  320，可折叠；属性 / 预览 / 评分
  <div id="lq-layers"></div><div id="lq-toasts"></div>
```

收栏：≥1280 三栏；1024–1279 aside → `lq-drawer--right`；768–1023 rail 也 → drawer；<768 单列，rail/aside 进底部固定栏两个入口 + 主操作 prominent（`lq-glass` sticky bottom）。材质：仅顶栏 1 层 blur；弹层时 ≤2。不加载站点导航脚本与 `app_bottomnav`；模板头注释写明独立原因（考试锁定/全屏编辑）。

### 11.4 响应式行为

| 宽度 | 侧栏 | 顶栏 | Dock | 弹窗 | 表格 |
|---|---|---|---|---|---|
| ≥1280 | 完整 | 完整 | 无（immersive 除外） | 居中 | 完整 |
| 1024–1279 | rail | 完整 | 无 | 居中 | 完整 |
| 768–1023 | drawer | 标题+更多 | 无 | 居中 | 横滚 |
| 640–767 | drawer | 精简 | 有 | 底部 sheet | 记录型卡片化 / 矩阵横滚 |
| <640 | drawer | 精简 | 有 | 底部 sheet | 同上，chips 横滚 |

320/375/390 均可操作：先保证主工作区，再收辅助区；页面本身不横向溢出。移动折叠：`lq-section--collapsible` + `LQ.collapsible`（默认 <768 收起并记忆，沿用 stage-9 的 localStorage 与事件委托）；**错误/当前步骤/未保存内容不折叠**。删除无人设置的 `data-mobile-collapse` 钩子。

## 12. 文案、信息层级与图标

- 层级三档：每屏一个焦点（page_head 或 hero）→ `lq-section` 标题 → 卡片标题；无四级标题；无装饰性英文眉题（`eyebrow` 槽不再填充，动态数据眉题保留数据）。
- 按钮：动词开头，推荐 ≤4 字；否定"取消"；破坏动作写明对象（"删除试卷"）；不用"确定/OK"。
- 空态：一句话"没有什么 + 下一步"；错误/无结果/无权限/离线分别表达。
- 说明：>20 字进 `data-explain`；表单帮助 ≤20 字。**必须常显**：同步/保存/生成状态与失败原因；文件格式/大小/截止/"总分须为 100"；成绩调整公式、"考勤不会被修改"、可见范围变化、删除/覆盖警告；权限不足/未配置/缺前置；校验错误。
- 数字：同屏一次；装饰性 0 不展示（零分/未答数/余额/截止例外）；时间用明确日期+时间（服务端时区契约），相对时间只作辅助。
- 图标：仅 lucide；导航项图标+文字；纯图标按钮 `aria-label` + tooltip；不用 emoji 做图标（聊天内容除外）。
- 快捷键：显示 Ctrl/⌘ 平台差异；不覆盖浏览器与编辑器常用键。

## 13. 特殊界面施工单（§13.0 固定 12 项结构；§13.5 课表已按 2026-09-20 工作区重写）

### 13.0 施工单的固定结构与阅读方式

每张施工单按下列 12 项写，**缺项即视为"不改动"**，实施者不得自行补充未写明的改动：

| 项 | 写什么 | 实施者怎么用 |
|---|---|---|
| 现状与证据 | 文件、行号、行数、现有类名/钩子/测试 | 动手前逐条核对；不符则停下更新施工单，不猜 |
| 功能 | 按角色列出"用户能做什么"，一行一个动作 | 迁移后逐条演示；少一条即未完成 |
| 形态 | 组件构成（用 §10 组件名）与 DOM 骨架 | 模板/JS 输出必须与骨架一致；类名以 §10 为准 |
| 布局 | 区域、尺寸、栅格、断点行为 | 用 §11.4 五档视口截图核对 |
| 质感 | 材质类、令牌、圆角档、阴影档、字号档 | 只允许写这里列出的令牌；出现其他值即 lint 阻断 |
| 交互 | 每个手势/键盘/状态转换的触发 → 结果 | 写成 e2e 断言 |
| 动效 | 时长/缓动令牌、reduced-motion 行为 | 用 §8.8 表核对 |
| 无障碍 | 角色、名称、焦点顺序、播报 | axe + 键盘遍历 + 读屏抽查 |
| 契约 | 必须原样保留的 API/data-*/事件/testid/测试 | 迁移前登记进台账，迁移后 grep 逐条确认 |
| 施工步骤 | 有序、可单独提交的步骤；每步说明改哪些文件 | 按序执行，每步一个提交 |
| 收尾 | 该页专属的清理与同步项（通用收尾见 §15.9） | 全部勾完才可标"业务验收通过" |
| 验收 | 命令、断言、截图矩阵、人工检查点 | 记录到 `lq-acceptance.md` |

术语约定：**"卡"**指 `lq-card`；**"格"**指课表网格中的一节课；**"chip"**指 `lq-chip`；**"常显"**指不依赖 hover/焦点、不自动消失；**"就地"**指在触发控件旁边而不是 toast。

### 13.1 作业/试卷编写页 `exam_editor.html`

**现状与证据**：3196 行独立 `<html>` 文档（无 `{% extends %}`）；`<style>` 12–1059（1048 行）；`<script>` 1470–3194（1725 行）；私有弹层 `.modal-overlay > .modal-box` ×4（评分 `#scoring-modal` :1166、AI `#ai-exam-modal` :1235、JSON `#json-exam-modal` :1392、预览 `#paper-preview-modal` :1454）；自带 `#toast-container` :1163 与 `window.showToast` 绑定 :1477；题型闭集 `radio/checkbox/text/textarea`（:1650、:2161-2164）；每题字段 `id/type/text(Markdown)/options[]/placeholder/allow_ai`，问答题另有附件要求 `required/min_count/max_count/allowed_file_types/description`（:2211-2224）；评分字段在评分弹层（分值/标准答案/得分点/失分点，默认生成 :1705-1714）；试卷级 `title/description/scope_level(private|department|school)/config.allow_student_ai/grading{total_score,style,style_label,description}`；`saveExam` :2439 → 评分不完整时 `confirm` 双分支 :2442（确定=打开评分弹层并放弃保存；取消=`persistExam` 存草稿）；`persistExam` :2395 → `POST/PUT /api/exam-papers`，**PUT 无版本字段**；后端 `exam_papers.py:333-358`：已有提交/草稿时改题 → 409，仅有布置时要求评分完整；`GET` 返回 `is_owned/can_manage/scope_level`（:327-330）；AI 出题任务 `#ai-task-status/-progress/-time` + 中断/刷新（:1341-1372）；`?v=` 手写 5 处。

**功能**（教师，`can_manage=true`）：新建/重命名试卷；增删改页（题组）并排序；增删改题并排序、复制；切换题型（4 种）；编辑题干 Markdown 并预览；编辑选项与正确答案；设置填空占位；设置问答题附件要求；设置本题/整卷 AI 开关；设置开放范围；设置评分（总分、风格、每题分值/标准答案/得分点/失分点、均分）；AI 出题（选材料、题型数量、生成、中断、预览、应用）；导入 JSON（下载模板、上传、看解析摘要、应用或放弃）；全屏预览；保存（含三态选择）；返回列表（脏检查）。**`can_manage=false`**：只读浏览、全屏预览、返回；不渲染任何编辑控件。

**形态**（`lq-editor`，`data-lq-editor="exam"`，§11.3）：
```
lq-editor__bar    [返回 lq-btn--ghost --icon] [标题 lq-input--ghost] · [lq-status] [chips 已布置/锁定] · [全屏预览 glass sm] [AI 出题 glass sm] [评分标准 glass sm + lq-badge--dot] [更多 lq-menu: 导入 JSON] [保存试卷 prominent]
lq-editor__rail   lq-segment[题目|设置]
                  题目: lq-list(页) → 每页 lq-row(页名 · 题数 · 分值) + lq-nav-grid(题缩略) ; 尾 [新增页面 soft sm]
                  设置: lq-form-section×3(基本 / 开放范围 lq-select / AI lq-switch)
lq-editor__main   每题 lq-card(head: 题N + 类型 chip neutral + 分值 chip + __actions[上移/下移/复制/删除 ghost icon]) (body: 题干 lq-textarea + 题型区) (foot: 本题 AI lq-switch--sm + 评分摘要行)
                  页尾 [新增题目 soft md] + 类型 lq-segment
lq-editor__aside  lq-segment[预览|评分] → lq-prose 预览 / lq-table--dense 分值表 + 合计 + 目标总分 + [均分 soft sm]
弹层              评分标准 lq-modal--lg · AI 出题 lq-modal--lg(lq-job) · 导入 JSON lq-modal(lq-dropzone) · 全屏预览 lq-modal--full · LQ.choose(保存三态)
```

**布局**：≥1280 三栏 280 / 1fr(min 640) / 320；1024–1279 aside 折叠为 `lq-drawer--right`（顶栏"属性"按钮开）；768–1023 rail 也折叠为 drawer；<768 单列，顶栏只留 返回/标题/status/更多，底部固定 `lq-glass` 栏三格：目录(sheet) · 保存(prominent) · 属性(sheet)。题卡间距 16；卡内 `--lq-pad 16`；主区左右内边距 `--ls-gutter`。

**质感**：顶栏 `lq-glass`（页内唯一持续模糊宿主）；rail/aside/主区底 `lq-surface`；题卡 `lq-card`（圆角 `--ls-r-lg` 20，`--ls-shadow-1`）；当前编辑中的题卡描边 `hsl(var(--ls-ring))` 1.5px；类型 chip `data-tone="neutral"`；缺评分角标 `data-tone="warning"`；标题字号 `--ls-t-title3`，题干 `--ls-t-body`，选项 `--ls-t-callout`；弹层 `lq-scrim` + `lq-glass--thick`。

**交互**：
- 标题：点击即编辑（`lq-input--ghost`），Enter/失焦提交，Esc 还原；空标题保存时就地错误"请填写试卷名称"。
- 页列表：点击切换当前页并滚动主区到该页首题；拖拽排序（`lq-list--sortable`），键盘 Alt+↑/↓；删除页 → `LQ.confirm` destructive 写明题数；最后一页不可删。
- 题卡：`__actions` 在 hover/focus-within/触控常显；删除 → `LQ.confirm` destructive + 8s 撤销 toast；类型切换 → 若已有选项/答案，`LQ.confirm`"切换题型将清除选项与答案"。
- 选项：Enter 在当前选项后新增；Backspace 于空选项删除；正确答案 radio/checkbox 原生语义。
- 附件要求 `lq-switch` 开 → 展开子表单；最少 > 最多 → 就地错误。
- 评分摘要行点击 → 打开评分弹层并定位到本题（`lq-nav-grid` current）。
- 评分弹层：左 `lq-nav-grid` 切题；分值 `lq-input` 数字 step 0.5；合计行常显"合计 N / 目标 M"，不等时 warning；均分 → 按题数平均并四舍五入到 0.5，余数给第一题；完成 → 校验全部题有分值与标准答案后关闭，否则就地列出缺项并定位第一处。
- AI 出题弹层：材料 `lq-list` 多选 → 题型数量四个 `lq-input`（0–20）→ 开始生成 → `lq-job` running（进度、耗时、中断）→ result_ready 时右侧 `lq-prose` 预览 + [应用到试卷 prominent]/[放弃 ghost]；应用时已有题 → `LQ.choose`【追加到末尾 / 替换全部 / 返回】。
- 导入 JSON：`lq-dropzone` 接受 `.json`；解析摘要常显（题数、错误行）；有错误时"应用"禁用并解释；应用 → `LQ.confirm` destructive"将覆盖现有 N 题"。
- 保存：`exam.save` → 若 `!scoringIsComplete()` → `LQ.choose`【完善评分 / 仅存草稿 / 返回编辑（初始焦点）】；有作答锁定态下保存按钮 `aria-disabled` + 相邻 `lq-alert` "已有学生作答，题目已锁定；如需修改请创建新版本"；成功 → `lq-status` synced + 时间；409 → `lq-alert` danger 常显 + 内容保留；400 → 就地定位缺评分。
- 返回：dirty → `LQ.confirm`"放弃未保存修改？"（destructive=放弃）。
- 快捷键：Ctrl/⌘+S 保存；Ctrl/⌘+Enter 新增题目；Esc 关最上层弹层。

**动效**：题卡增删 `--ls-dur-base` 淡入/淡出 + 高度过渡；评分弹层 modal 220ms；AI 预览 `lq-prose` 淡入 `--ls-dur-base`；reduced-motion 全部即时。

**无障碍**：三栏各为 `<aside aria-label="题目目录">`/`<main>`/`<aside aria-label="属性与预览">`；题卡 `role="group" aria-labelledby=题N`；排序按钮有 aria-label；评分缺项用 `aria-describedby` 关联；弹层焦点圈闭与回归由 `LQ.layer` 保证；`lq-status` 只在跨等级时播报。

**契约**：`can_manage/is_owned/scope_level` 语义；题型闭集；`scoringIsComplete()`；`/api/ai/exam/generate`、`/api/ai/exam/task/{id}/status|cancel`、`suggest-topics`；`/api/exam-papers/json-template`、`import-json`、`material-reverse`；`PATCH …/attributes`、`PUT …/tags`；保存后跳转 `/exam/{id}/edit`。

**施工步骤**：
1. 后端票：`PUT /api/exam-papers/{id}` 接受 `expected_revision`，不匹配 → 409 `{code:'revision_conflict'}`；`GET` 返回 `revision`；旧客户端不带字段时跳过校验（兼容）。单测 + 台账。
2. 新建 `partials/lq_editor_head.html`（若 S2 未建）；`exam_editor.html` 顶部换为 include；删自带 toast 容器与 `window.showToast` 绑定（改用 `LQ.toast`）。
3. 内联 CSS 1048 行 → `static/css/lq/pages/exam-editor.css`（只保留题型区与评分表的独有布局；其余用组件类）；`npm run build`。
4. 内联 JS 1725 行 → `static/js/exam_editor/{state,questions,rubric,ai,import,preview,save}.js`（每个 ≤400 行；逐函数搬，先不改行为；`?v=` 用 `asset_url`）。
5. 模板按"形态"骨架重排；四个私有弹层改 `LQ.layer`；`confirm` 改 `LQ.choose/confirm`（事件先 `preventDefault`，调用处 `await`）。
6. 状态色：chips 走 `lq_status`；AI 任务走 `lq-job` + `job` 族。
7. 移动端底栏与 sheet；键盘快捷键。
8. `exam-authoring.spec.ts`（S3 已写）跑绿；截图矩阵。

**收尾**：删 `exam_editor.html` 内联 `<style>/<script>`；删 `ui-system.src.css` 中若有 `exam-editor` 专属段；`lq-migration-registry.json` 状态推进；`lq-action-registry.md` 补 §14.1 的测试编号；`docs/lq-components.md` 若新增 `lq-list--sortable` 则登记；更新 `exam-papers` 路由文档注释（版本字段）；通用收尾 §15.9。

**验收**：`exam-authoring.spec.ts`（空试卷 / 加题改型 / 评分三态 / 已作答锁定 / 只读共享 / AI 生成三态 / 导入错误 / 409 / 未保存离开 / 键盘快捷键）；`test_exam_paper_scope_access.py`；`python -m unittest`；截图 1440/1024/390 × 亮/暗/off × 教师可管理/只读；`lint_lq.py --page exam_editor` 零阻断；一屏 blur 宿主 = 1（弹层时 2）。

### 13.2 布置弹窗（课堂页 `#assignment-modal`、`#exam-assign-modal`）

**现状与证据**：`classroom_main_v4.html:915` 新建作业 → `#assignment-modal`（:1833）；`:919` 从试卷库添加考试 → `#exam-assign-modal`（:1986）；逻辑 `app_exams.js:596 loadExamPapers`（单选列表）、`:656 confirmExamAssign` → `POST /api/exam-papers/{id}/assign`（`assessment_kind` 客户端校验 :674、`class_offering_id`、`allowed_file_types`、`learning_stage_key`、`send_email_notification`、日程 `readSchedulePayload('exam')`）；成功后**整页 reload**；`saveAssignment()` 走 `POST /api/assignments`；`assignment-classification-modal.spec.ts`（280 行）钉住原生 `<dialog id="assignment-kind-modal">` 并发/取消/重试行为。

**功能**（教师）：新建普通作业（标题、要求 Markdown、日程、迟交策略、附件类型、学习阶段、邮件通知、存草稿或布置）；从试卷库选一份试卷布置为考试（同上 + 考核类型）；两者都可在布置前校验并看到错误。

**形态**：`lq-modal--lg`；`__head` 标题"新建作业 / 添加考试"；`__body`：
```
[考核类型 lq-segment: 作业|期中|期末]  (仅考试弹窗；未选 → 就地 lq-field 错误)
[试卷 lq-list 单选 lq-radio，每行: 标题 · 题数 · 总分 · 范围 chip]  (仅考试弹窗；空/加载/错误三态 lq-empty)
lq-form-section 基本: 标题 lq-input · 要求 lq-textarea(Markdown)
lq-form-section 日程: lq-segment[长期|截止|倒计时] → 截止: 日期时间 lq-input(ls_date_picker) / 倒计时: 分钟 lq-input ; 迟交 lq-switch → 展开 固定扣分/梯度扣分 lq-radio + lq-input
lq-form-section 提交: 附件类型 lq-chip-row --tag · 学习阶段 lq-select · 邮件通知 lq-checkbox
__foot: [存为草稿 soft] [布置到课堂 prominent]
```

**布局**：宽 `min(800px, 100vw-32px)`；≤640 变 `lq-sheet--bottom` 高 92dvh，foot 等宽堆叠主按钮在下；试卷列表最大高 40vh 内滚。

**质感**：`lq-scrim`（无模糊）+ `lq-glass--thick` 圆角 28；表单区无卡套卡，用 `lq-form-section` 间距 32；错误 `data-tone="danger"` 文本 12px。

**交互**：考核类型未选点布置 → 定位第一错误并 `lq-error-summary`；日程模式切换保留已填值；迟交开关关闭时子字段禁用不清空；布置成功 → 关弹层 → 新任务卡插入任务区顶部（淡入）+ `LQ.toast` success；失败 → `lq-alert` 常显于 `__body` 顶，内容保留；Esc/遮罩 = 取消（dirty 时 `LQ.confirm`）。

**动效**：modal 220ms；新卡插入 `lq-anim-rise`。

**无障碍**：`aria-modal`、初始焦点在第一个字段；试卷列表 `role="radiogroup"`；错误 `aria-describedby`。

**契约**：`POST /api/exam-papers/{id}/assign` 与 `POST /api/assignments` 字段不变；`assessment_kind` 三值；`<dialog id="assignment-kind-modal">` 行为与 spec 不动；任务卡 SSR 结构（`classroom_task_preview.html`）不变。

**施工步骤**：1) 后端票：两个创建端点返回任务卡渲染所需字段（或返回 `card_html` 片段）；2) 两弹层改 `LQ.layer` + 组件；3) `app_exams.js` 成功分支改为插卡不 reload；4) 校验就地化；5) sheet 断点。

**收尾**：删 `classroom_main_v4.html:1833-2115` 两段旧标记中被组件替代的部分；`app_exams.js` 内旧类名与 `UI.openModal` 调用清零；登记 §14.2 测试编号；§15.9。

**验收**：`home-classroom-business.spec.ts` + 新增用例"布置后不 reload 卡片出现"；`assignment-classification-modal.spec.ts` 不变通过；B01。

### 13.3 学生作答页 `assignment_detail_student.html` 与考试作答页 `exam_take.html`

**现状与证据**：作答页 1650 行，`<style>` 6–489（484 行），`<script>` 897–1647（751 行）；宏 `submission_form` :522；`#answer-area` :531、React 岛 `assignment-submit-sync` 挂载 :535-549（依赖 `.answer-textarea`、`#submit-btn`、`.answer-form-container`、`[data-assignment-submit-sync-payload]`、`ASSIGNMENT_UPLOAD_CHANGE_EVENT`）；附件块 :551-577（选择文件/文件夹/粘贴、`#drop-zone`、`#file-chips`）；`SUBMISSION_VERSION` :916；本地草稿 key :1070；服务端草稿 `POST /draft` 带 `expected_submission_version`（:1182,:1194）；409 → 提示"提交状态已变化…"并保留本地（:1200）；`sendBeacon` :1224；恢复时取较新者 :1256-1282；上传队列 `draftSyncChain` :1411-1443；提交 :1580-1616（flush → `expected_submission_version` + `use_server_draft` → 成功清本地 → `openGroupPeerEval`）；撤回 `confirm` :1631；自动保存 chip 色 :377-379 硬编码；我的提交卡各状态 :651-828；`[data-assignment-clock]` :617-640。考试页 4228 行独立文档，`<style>` 9–1498（1490 行），`<script>` 1707–4220（2514 行）；顶栏 :1502（页码、已答 n/m、时钟 :1524、答题卡切换、上一页/下一页、"整理答卷" `<details>`、交卷 `#topbarSubmitBtn` :1584）；侧栏答题卡 `#sidebarBody` + `#saveStatus`；`SUBMISSION_VERSION` :1729；轮次守卫 :2106；每题附件 :2761；白板 `initExamDrawingWhiteboard` :1713/:1865，覆盖类 `.exam-drawing-whiteboard-root` :966-980；confirm ×5（清页 :3295、清卷 :3313、未答 :3617、交卷 :3622、撤回 :3743）；服务器时间拦截 :3587；`tests/frontend/exam_draft_version.test.cjs` 用正则抽 `performServerDraftSave/loadServerDraft/handleSubmission`。

**功能**（学生）：查看作业要求/评分标准/截止；作答（文本/单选/多选）；添加附件（选文件/文件夹/粘贴/拖入）、移除附件；自动保存（本地 + 服务端）与状态感知；提交（含未答提示）；查看提交结果与反馈、补交扣分明细、综合表现分；撤回（窗口内）；申请重做（审批）；小组互评；错题本复盘；导出复习。考试页另有：分页作答、答题卡跳题、手写作答（白板）、清空当前页/整卷、倒计时、服务器时间拦截交卷。

**形态**（作答页 = 详情页骨架；考试页 = `lq-editor` `take`）：
```
作答页
lq-topbar        [返回] 作业名 · [lq-status save] [lq-clock] · [提交作业 prominent lg]（≤1024 移入底部固定栏）
主列             lq-section 作业要求(lq-prose) · lq-section 评分标准(可折叠) · 题目流: 每题 lq-card(题号+分值 chip / 题干 lq-prose / 作答 lq-textarea|lq-radio|lq-checkbox / 附件 lq-upload)
右栏(sticky)     lq-card--flat: lq-nav-grid 题目导航 · 我的提交 lq-card(状态 chip + 结果)
考试页
lq-editor__bar   [返回(需确认)] 试卷名 · [lq-clock--compact] [已答 n/m] [lq-progress 120] [lq-status] · [答题卡 glass icon] [上一页|下一页 lq-btn-group glass] [整理答卷 lq-menu] [交卷 prominent]
lq-editor__rail  lq-nav-grid 答题卡（分节 group）+ lq-status
lq-editor__main  lq-paper(42–56rem lq-surface 圆角 28) 内每题 lq-card--flat，分隔线相隔；页脚 [上一页|下一页]
无 aside；移动 Dock: 题目(sheet) · 白板 · 交卷
```

**布局**：作答页 ≥1280 主列 `1fr` + 右栏 320 sticky top `--lq-topbar-h + 16`；<1280 右栏内容进底部 Dock "题目"sheet 与页尾"我的提交"；≤1024 提交按钮移到底部固定 `lq-glass` 栏（高 64，safe-area）。考试页 rail 240 sticky；<768 rail → sheet；主区 `lq-paper` 内边距 24/16。

**质感**：顶栏/底栏 `lq-glass`（各 1 层，同屏只有一个可见）；题卡 `lq-card`（作答页）/`lq-card--flat`（考试页纸面内）；作答输入 `lq-textarea` min 120；`lq-nav-grid` 32px 方胶囊；状态 chip 走 `save/deadline/submission/submission-flag/score` 族；结果卡分数 `lq-card--stat` 28/700 tabular；考试页背景 `lq-ambient` 静态（无色团）。

**交互**（作答页）：
- 输入 → 本地草稿 debounce 400ms → `lq-status dirty→local_saved`；服务端草稿 debounce 2s → `syncing→synced(time)`；失败 → `error` + [重试]；409 → `conflict` + "刷新核对" action，**停止后续写入**，内容保留。
- 附件：选择/拖入/粘贴 → `lq-upload` 队列逐项 `validating→uploading→uploaded`；被拒（类型/大小/重复截图）→ 项内常显原因（含归属题号）；移除 → 立即移除 + 8s 撤销。
- 提交按钮启用条件 = published && accepting(regular|late-open|重交窗口) && 队列 idle && 无 conflict；点击 → 未答题 → `LQ.choose`【提交 / 继续作答（初始焦点）】→ busy 锁定 → 成功：清本地草稿 → 小组作业先 `openGroupPeerEval` → 刷新结果区（不整页 reload 若后端返回结果，否则 reload 保留）；失败 → `lq-alert` 常显。
- 撤回：`LQ.confirm` destructive"撤回后本轮提交与附件将清空"。
- 申请重做：`approval_workflow.js` launcher（不改）。
- 题目导航：点击滚到题卡并聚焦首个输入；`is-answered` 随输入实时更新。
- 时钟：`lq-clock` 承接 `assignment_time.js`；`urgent`（<1h）变色一次脉冲；`closed` 后提交按钮禁用并就地说明。
（考试页）：
- 分页：上一页/下一页、答题卡点击、←/→（焦点不在输入时）；切页保留滚动到页顶。
- 答题卡格状态：answered/current/flagged(标记待查，长按或 F 键)/error/pending-upload。
- 手写作答：题卡"手写作答"→ 白板挂载在题下（`.exam-drawing-whiteboard-root`），笔迹计入已答；工具条 `lq-wb-toolbar`。
- 清空当前页/整卷：`lq-menu` 风险组 → `LQ.confirm` destructive（写明题数）→ 草稿同步。
- 交卷：未答 → `LQ.choose`【交卷 / 回去作答（初始焦点）】→ `LQ.confirm`"交卷后不可修改" → busy → 成功进入只读结果态；服务器拦截 → `lq-alert` danger 常显"已超过允许提交时间，以服务器时间为准"。
- 软键盘弹出：Dock `is-hidden`，主区底部保留"交卷"等价按钮（同一命令，互斥显示）。
- 返回：`data-lq-lock-nav`，返回按钮 → `LQ.confirm`"离开将保存草稿，考试继续计时"。

**动效**：题卡进入 stagger ≤10；`lq-status` 跨等级切换 120ms cross-fade；交卷成功 → 结果区 `lq-anim-rise`；考试页禁 ambient 色团；reduced-motion 全关。

**无障碍**：每题 `<fieldset><legend>题N（分值）</legend>`；答题卡 `nav aria-label="答题卡"`，格 `aria-current/aria-label`；倒计时 `role="timer"`，只在 30/10/5/1 分钟阈值播报；`lq-status` 跨等级播报；上传项 `aria-live` 只播报拒绝与失败；白板区 `aria-label="手写作答"`。

**契约**：React 岛钩子（先加 `data-answer-field`/`data-submit-button`/`data-answer-form` 稳定属性并让岛先改读这些，再迁样式类）；`p03-assignment-answer-area`、`p03-submit-assignment`、`p03-submission-status`；`SUBMISSION_VERSION` 固定、`expected_submission_version`、`use_server_draft`、`sendBeacon`、本地 key 格式、恢复取较新者；`assignment_time.js` 全部 `data-*`；`openGroupPeerEval`；`approval_workflow.js` launcher；考试页服务器截止规则、每题附件策略、localStorage 配额兜底、白板 API；`exam_draft_version.test.cjs` 的三个函数名与行为断言。

**施工步骤**：
1. （考试页）先把 `performServerDraftSave/loadServerDraft/handleSubmission` 抽到 `static/js/exam_take/submit.js` 并把 `exam_draft_version.test.cjs` 改为 `import`（同一提交）；跑绿。
2. 作答页：内联 JS → `static/js/assignment_student_page.js`（逐函数搬）；内联 CSS → `lq/pages/assignment-student.css`；岛屿钩子加稳定 `data-*`。
3. 作答页模板按形态重排：顶栏、题卡、`lq-upload`、右栏、结果卡；chips 走注册表；`confirm` → `LQ.confirm/choose`。
4. 考试页：`lq_editor_head` include；内联 CSS → `lq/pages/exam-take.css`；JS 拆 `timer/navigator/answers/attachments/submit`；模板按形态重排；白板工具条换皮（§13.8）。
5. 移动端底栏/Dock/sheet；软键盘等价按钮。
6. e2e：`assignment-student-draft.spec.ts`、`exam-take.spec.ts`、`assignment-submission.spec.ts` 全绿。

**收尾**：删两模板内联 `<style>/<script>`；删 `ui-system.src.css` 中作答/考试专属段（若有）；`?v=` 手写 5 处 → `asset_url`；台账；§14.3/14.4 测试编号；`frontend-redesign` 记忆中的"作答页 React 双系统"描述更新；§15.9。

**验收**：B02–B04；`test_submission_write_guard/image_guard/question_file_policy`；截图 1440/390 × 亮/暗/off × published/late/closed/graded/returned/group；Playwright 4× CPU 节流下切页无 >50ms 长任务；一屏 blur 宿主 ≤1。

### 13.4 批改页 `submission_detail.html` 与教师作业页 `assignment_detail_teacher.html`（含错题归集）

**现状与证据**：批改页 1881 行，`<style>` 6–800（795 行），`<script>` 1125–1879；跳题 `aside.submission-jump-nav` :824 + 岛 `submission-jump-nav` :855；信息卡 :868 含分数档硬编码色 :921-925；反馈卡 :956；答案卡 :968；附件卡 :983（列表 + 预览 + TOC + 教师管理面板"保存附件 / 保存并提交 AI"）；评分卡 :1068（returned 时锁定 :1069-1078；表单 :1080-1113）；分数 `parseInt` :1785（后端允许有限小数 `submission_grade_guard_service.py:30`；`expected_review_revision` Web 可选 :57）；AI 批改 `confirm` :1856 → `POST …/regrade`；学生也可访问（`assignment_pages.py:313`，附件管理按角色 :346）。教师作业页 4014 行，`<style>` 9–1268（1260 行），`<script>` 2095–4013；顶栏 :1288-1470（计数、筛选 `<details>`、错题归集 :1341、批改处理 `<details>`、截止 :1376、状态切换 :1386-1403、管理作业 `<details>`）；信息卡 + insight :1468；分数分布 ECharts 色 :2393-2398；筛选 `.filter-btn` :1696；批量条 :1706；列表 JS 渲染 :2779（状态映射 :2779-2787，附加 chips :2793-2797）；modals ×7（:1745-2036）；`<dialog id="assignment-kind-modal">` :1780。错题归集 2662 行，`<style>` 40–1512，100 hex，无 `.btn`；hero :1543、知识点面板 :1566/:1577、tabs :1638/:1650、错答明细 dialog :1937、归因 drawer :1956、AI 重算 `POST …/wrong-summary/reorganize` :2118 + 轮询 :2629。e2e：`assignment-submission`（testid `p03-submission-status/score-input/submit-manual-grade/ai-regrade-detail`）、`teacher-review-ai`（停止在途 AI、越权拒绝）、`classroom-task-card-layout`；组件 `grade-publication.spec.ts`。

**功能**（教师，批改页）：看学生信息与状态；看逐题答案与 AI 建议；预览/下载附件；管理附件（增删、保存、保存并提交 AI）；上一份/下一份；打分（0–100 有限小数）与评语（Markdown，插入逐题模板）；AI 辅助批改；保存评分。（学生）看自己的答案/反馈/附件，不见评分表单。（教师，作业页）看计数与分布；筛选/搜索提交；批量撤回；AI 批量批改；未提交记 0；线下代交；截止作业（默认分）；切换状态（含邮件通知）；编辑作业；作业分类；导出成绩/附件；删除；错题归集入口。（错题归集）看错误人数归集与难题归集；看错答明细；看知识点掌握度与归因、与本班其他考试比对；AI 重算。

**形态**：
```
批改页（详情页骨架）
lq-topbar   [返回] 学生名 · 作业名 · [上一份|下一份 lq-btn-group glass sm] [更多 lq-menu: 导出/撤回/重做申请]
主列        lq-card 信息(状态 chip submission 族 · 分数 chip score 族 · 补交扣分 lq-alert info 常显)
            lq-section 答题内容: 每题 lq-card(题干 lq-prose / 学生答案 lq-prose / AI 建议 lq-section--collapsible)
            lq-section 附件: lq-split(左 lq-list 文件 · 右 lq-viewer + TOC lq-popover) ; 教师: 管理面板 lq-form-section + [保存附件 soft][保存并提交 AI soft]
            lq-section 批改(教师): lq-field 分数 lq-input number step .5 · lq-field 评语 lq-textarea + [插入逐题模板 ghost sm] · lq-form-actions [AI 辅助批改 soft][保存评分 prominent] ; returned 时整段 lq-alert warning "已撤回，等待学生重交"
右栏 sticky lq-card--stat 总分 · lq-nav-grid 跳题(岛屿改类名)
教师作业页（详情页骨架 + 列表）
lq-page-head 标题 + 状态 lq-segment[草稿|进行中|已截止] + __actions[错题归集 soft][更多 lq-menu: 截止作业 / 编辑作业 / 作业分类 / 导出成绩 / 导出附件 / ─ / 删除作业]
             __aside: lq-card--stat 已交 · 待批 · 未交 + lq-ring 提交率
lq-section 概览: lq-card 分数分布(ECharts，色取 score 族令牌) · lq-card 作业要求/时间/附件设置
lq-section 提交: filter_bar(搜索 + lq-chip-row --filter 全部/已提交/已批改/待重交/未提交 含计数) · lq-bulk-bar[撤回选中 (N) destructive-soft][全部撤回 destructive-soft][批改处理 lq-menu: AI 批量批改 / 未提交记 0 / 线下代交] · lq-table(姓名 · 状态 chip · 分数 · 提交时间 · 操作 ghost) ≤768 卡片化
弹层: rubric/exam-paper/edit/delete/withdraw/close/offline → lq-modal ; 结课默认分 LQ.choose ; assignment-kind 原生 dialog 保留
错题归集
lq-page-head 标题 + [AI 重算 soft] · lq-job(重算态)
lq-section 知识点掌握度: lq-card + lq-bars(四档 data-tone) + [详情 ghost → lq-drawer 归因/比对]
lq-tabs[错误人数归集|难题归集] → lq-card 每题(题干 · 选项分布 lq-bars · 错误人数 chip) → [错答明细 ghost → lq-modal]
```

**布局**：批改页 ≥1280 主列 1fr + 右栏 320；<1280 右栏进页尾；`lq-split` <1024 上下堆叠。作业页列表 ≥768 `lq-table`，<768 卡片化（姓名/状态/分数/操作）。错题归集单列，卡片网格 `lq-grid--cards`。

**质感**：顶栏 `lq-glass`；卡片 `lq-surface`；状态/分数/附件 chips 全部 `data-tone`（§8.13：submission/score/attachment 族，分数档三处硬编码合一）；分布图六档色读 `--tone-score-*`；错题四档柱色读 `--tone-score-fail/pass/good/excellent`；评分表单 `lq-form-section`；AI 建议折叠区 `lq-surface` 内层 `--ls-surface-2`。

**交互**：
- 分数输入：`type=number min=0 max=100 step=0.5`；空 → 就地错误"请输入分数（空分数不会记为 0）"；>100/<0 就地错误；失焦格式化保留一位小数。
- 保存评分：携带 `expected_review_revision`（页面加载时的值）+ `expected_assignment_revision`；busy 锁定；成功 → `LQ.toast` success + 状态 chip 更新 + 右栏总分更新 + 下一份按钮高亮；409 → `lq-conflict` 常显（保留分数评语、[重新核对] 拉取最新并显示差异摘要）；400 就地。
- AI 辅助批改：`LQ.confirm`"AI 结果需人工确认后保存" → `lq-job` 在评分卡顶（running 时保存按钮可用但提示"AI 进行中"）；完成 → 建议填入表单（不自动保存）；失败保留旧成绩；status=grading 时按钮隐藏。
- 上一份/下一份：dirty → `LQ.confirm`；保持筛选上下文（URL 参数）。
- 附件：点击文件 → 右侧 `lq-viewer`（图片接灯箱、PDF/文档走现有预览、其余下载）；教师管理：增删后"保存附件"启用；"保存并提交 AI" → `LQ.confirm`。
- 作业页状态 `lq-segment`：切换 → `LQ.confirm`（含"邮件通知学生" `lq-checkbox`）→ PATCH → chip 更新。
- 截止作业：`lq-modal`（默认分 `lq-slider` 0–100 默认 0 + "已提交未批改也按默认分" `lq-checkbox`）→ `LQ.choose`【记默认分并截止 / 仅截止 / 返回】。
- 筛选 chips 互斥单选，计数实时；空结果 `lq-empty data-reason=no-results` + [清除筛选]。
- 批量：勾选行 → `lq-bulk-bar` 出现（sticky 底）；撤回选中 → `lq-modal`（新截止时间必填）→ 行 chip `returned`。
- 未提交记 0：`LQ.confirm` destructive 写明 N 人 → 行 chip `absence-zero`。
- 线下代交：`lq-modal--lg` 逐题答案 + 附件。
- 错题归集：AI 重算 → `lq-job` 轮询 → 完成刷新面板；知识点柱点击 → `lq-drawer` 归因；错答明细 → `lq-modal`。

**动效**：保存成功后总分 `--ls-dur-base` 数字过渡；行 chip 更新 cross-fade 120ms；drawer 280ms；reduced-motion 即时。

**无障碍**：评分表单 `aria-describedby` 错误；`lq-conflict` `role=alert`；表格 `th scope=col`、排序按钮 `aria-sort`；批量选择复选框有 aria-label（含姓名）；分布图提供 `<table class="sr-only">` 数据等价；错题柱 `aria-label` 含百分比。

**契约**：`grade_submission_record` 副作用链（通知一次、阶段推进、小组结算）；`group_assignment_service` 未公布剥离在服务端；学生视图不渲染教师区（`assignment_pages.py:313,346`）；`p03-*` 四个 testid；`teacher-review-ai.spec.ts` 的停止在途 AI 与越权断言；`<dialog id="assignment-kind-modal">` + `assessment_kind_controls.js` 行为；`classroom-task-card-layout` 卡片控件；`/wrong-summary/reorganize|status` 轮询；`test_group_assignment_service`、`test_wrong_question_summary_service`。

**施工步骤**：1) 后端/前端票：分数小数 + `expected_review_revision`（S0 已做）；2) 批改页内联 CSS/JS 抽出；模板按形态重排；chips 走注册表；`lq-split/lq-viewer`；3) 教师作业页内联抽出；page_head + segment + menu；列表 `lq-table` + 卡片化；七弹层 → `LQ.layer`；ECharts 色改令牌读取；4) 错题归集内联抽出（1473 行）；`lq-bars` 令牌化；两弹层 → `LQ.layer`；5) e2e `grading-concurrency.spec.ts`、`wrong-summary.spec.ts` 跑绿。

**收尾**：三模板内联 `<style>/<script>` 归零；`ui-system.src.css` 相关专属段删除；100+79+41 处 hex 归零（除 ECharts 运行时读取）；§14.5/14.6 测试编号；`wrong-question-summary` 记忆中的截图渲染法更新为新类名；§15.9。

**验收**：B05、B06；`assignment-submission`、`teacher-review-ai`、`classroom-task-card-layout`、`grade-publication`、`grading-concurrency`、`wrong-summary` 全绿；截图矩阵含学生视图、returned 锁定、AI running、409；`lint_lq` 零阻断。

### 13.5 3D 课表 `course_schedule_deck.js` 及其三个宿主（2026-09-20 依工作区重写）

**现状与证据**（HEAD `23fd77e0` + 工作区 +44 行）：
- 模块群：`course_schedule_deck.js` **1626 行**（`DECK_CSS` 86–505 ≈420 行，`ensureStyles()` :506 自注入 `#course-schedule-deck-style`，**51 hex**）；`course_schedule_change_links.js` 182 行（把快照中显式的 pending original/proposed 端点配对成 `ScheduleConnection`，方向 local/outgoing/incoming，标签"时间更改 / 时间更改 · 教室更改"，`scheduleChangeColors` 按课程稳定配色并继承上一张图）；`course_schedule_change_routes.js` 518 行（**无 DOM 读取**的正交折线寻路：障碍避让、转弯/交叉代价、端点短桩、标签独立放置、`reason` 失败码 `invalid_canvas|missing_endpoint|same_time|endpoint_occluded|no_safe_path`、`reused` 复用上次几何）；`course_schedule_presentation.js` 87 行（`compactClassroomName` 教室编号简称、`classroomChangeState`、`adjustmentActionText` → `改时间|改教室|教室+时间|停课|待审变更`）；四个 `.d.ts`。模块间 `import` 带手写 `?v=`（`change-lines-glass-20260920`、`change-lines-simple-20260920`、`schedule-glass-20260920`），三宿主 `?v=deck3d-20260920-glass`。
- 视图：**3D 缩略层**（`.cs-stage` `perspective` + 所有周卡同时在 DOM，`offset∈[-1,5]` 可见，`.cs-lesson--mini` **无 backdrop-filter**、静态高光）；**整周放大层** `.cs-expand`（`position:fixed; z-index:1200; background: rgba(15,23,42,.55); backdrop-filter: blur(6px)` 自管 `role=dialog aria-modal`，body 追加；`.cs-expand__card` `min(1240px,94vw) × min(86vh,900px)`）；放大层内 `.cs-grid--expanded`（`30px <label> repeat(7, 1fr)`，时段背景带 `.cs-grid__band--dawn/am/pm/eve` 四组硬编码 rgba/hex）；**变更连线图** `.cs-change-map`（绝对定位覆盖网格，`min-height 580px`，`<svg class="cs-change-lines">` 折线 `.cs-change-line` + 可点击标签 `.cs-change-line-label`（白底描边 rect + 12px 文本）+ 起点圆 `.cs-change-line-origin`；无法布线时 `.cs-change-line-fallback` 文本；移动端 `min-width 960px/1210px` 横滚）。
- 课次格 `.cs-lesson`：`--cs-radius 14px`、`backdrop-filter: blur(14px) saturate(1.15)`（:255）；内层 `.cs-lesson__surface` **不透明** `background-color: var(--cs-accent)` + 高光渐变 + inset 描边；标题 ≤2 行；底部 `__room`（短名/全名切换）+ `.cs-adjustment-label`（`blur(10px) saturate(1.3)`，:292）；**容器查询密度自适应**（`container: cs-lesson / size`，8 条 `@container` 规则 :325-357：按高度 92/72/57/39/30px 与宽度 100/80px 逐级收紧内边距、行数、隐藏教室、按钮覆盖教室区）；待审原卡 `.cs-lesson--pending` 2px 虚线 + 4px 间隙（极窄 2px）；拟安排 `.cs-lesson--proposed` 不透明白混色。
- 单课预览（展开态内点击/悬停）：`.is-preview`/`.is-preview-closing`/`.is-preview-moving`；Web Animations **120ms 可逆**（读取当前帧后反转，字号插值不缩放文字，教室短/全名与按钮短/全说明 80ms 交替渐显，动画期间零滚动坐标）；`hoverArmed` ≥4px 真实位移门槛（工作区）；`setChangeDetail()` 单点管理 `.cs-adjustment-details[hidden]` + `aria-expanded`（工作区）；Esc 先关预览再关对话框；`ResizeObserver` 重定位；`prefers-reduced-motion` 直接终态。
- 降级：`@media (prefers-reduced-transparency: reduce), (prefers-contrast: more)` 去 blur、去渐变、按钮实色（:404-409）；`@media (max-width: 860px)` 舞台 400/卡 330。
- 焦点/定位：`focus-visible` 黄 `#fbbf24`；`is-counterpart-focus` 橙 `#f59e0b` 描边 + `::after "已定位"`（`#713f12`）3s；播报 `announce()` :927 到 `[data-csd-feedback]`/`[data-csd-expand-feedback]`（两个 `role=status`）+ `[data-csd-indicator]` `aria-live`。
- 宿主：`dashboard.js:701`（教师首页：`showTermSelect:true`、`onTermChange`、角色 `emptyHtml`；同步 `academic_schedule_sync.js` 88 行 `[data-academic-schedule-sync]`）；`manage_course_schedule.js:37`（课时统计页：自有学期/课程/班级筛选、`courseAccentFor` 复用、`[data-cs-sync]`、`[data-cs-toast]`）；`student_dashboard_schedule.js:63`（学生首页：`compactSummary:true`、`onWeekChange`；**宿主自带**周导航 `data-student-week-prev/next/today`、`data-student-schedule-expand`、模式切换 `data-student-schedule-mode=3d|agenda|courses`、学期 `data-student-schedule-term`、`[data-student-schedule-feedback]`、重试、提示行、`courses-payload` JSON；partial `student_dashboard_schedule.html` 23 行）。`ui-system.src.css:55817-55845` 只有面板壳 `.cs-deck-panel` 与 `.cs-deck-slider` 宽度；`:63181` 学生页隐藏 deck 自带头部与提示。
- 测试：vitest `academic-schedule`、`course-schedule-change-links`、`course-schedule-change-routes`、`course-schedule-presentation`、`dashboard-schedule-wheel`；组件 spec `course-schedule-deck`、`academic-schedule-deck`（工作区 +8 行）、`academic-schedule-lines`、`academic-schedule-density`、`academic-schedule-sync` + `schedule-fixture-modules.ts`；app 级 `dashboard-schedule.spec.ts` 12 条；Python `test_student_course_schedule.py`。设计记录：`docs/dashboard-schedule-refinement-2026-09-06.md`、`docs/课表预测集成设计.md`。

**功能**（三宿主共有）：浏览周（滚轮/拖拽/方向键/滑杆/上下周按钮）；看当前周摘要；放大整周；在放大层看每节课（课程、教室简称、班级）、待审变更（原位置/拟安排/停课/换教室）与变更连线；点标签跳到对应周并定位对应课；打开单课预览看完整信息；进入课堂（有 `classroom_url` 时）；切换学期（教师首页/课时统计页）；同步教务课表/智慧课堂（教师，宿主提供）。学生宿主另有：日程列表模式、全部课程模式、回本周、放大课表按钮、"只显示本平台课程"常显说明。

**形态**（模块内部类名族 `.cs-*` 保留，外观改令牌；宿主控件改 `lq-*`）：
```
deck（模块自注入 CSS）
.cs-deck-head      h3 + p · [学期 lq-select](可选) · [‹ lq-btn--glass --icon][周次指示 aria-live][› lq-btn--glass --icon][滑杆 lq-slider]
.cs-stage          perspective 舞台；周卡 .cs-card = lq-surface 质感（inset 高光，无 blur）；当前卡可 --frost（≤1 层）
.cs-deck-feedback  role=status（仅用户动作后播报）
.cs-expand(自管 dialog) → .cs-expand__card = lq-glass--thick 圆角 28（**页内唯一持续模糊宿主**）
   __bar: 第N周 · 副标题 · status · [‹ 上一周 glass sm][下一周 › glass sm][返回 3D 视图 ghost sm]
   __body: .cs-grid--expanded（带 .cs-grid__band 时段带）+ .cs-change-map(svg 连线 + 标签)
   格 .cs-lesson（无 blur，见"质感"）: __surface(课程色 tint 或实色) > __main(a 或 div) > __title / __details ; __footer: __room(短|全) + .cs-adjustment-label(chip 形态)
宿主（学生）
ls-schedule 头: 标题 · lq-segment[3D课表|日程列表|全部课程 N] · 学期 lq-select
ls-week-nav:   [‹ lq-btn--glass --icon][周标签 aria-live][› lq-btn--glass --icon][回本周 lq-btn--soft --sm(不在本周时显示)][放大课表 lq-btn--soft --sm]
状态行 lq-status-line(常显"只显示本平台课程…") · [重新加载 lq-btn--soft --sm](失败时)
宿主（教师首页）
组模式 lq-segment[列表|系别班级|按课程|3D课表] ；3D 面板内 deck 自带头部 + [同步 lq-btn--glass --sm + lq-menu: 教务课表 / 智慧课堂]
```

**布局**：舞台高 460（≤860px 宽 400）；周卡 `min(1240px,94vw)` 放大层；放大网格 8 列 `30px + label + 7×1fr`，行高 `minmax(20px,.45fr)`/`minmax(34px,3fr)`；≤640 放大网格 `min-width 850px`（有重叠 1100px）横向滚动 + `overscroll-behavior: contain`（保留）；连线图 `min-height 580px`，移动 960/1210px 横滚（保留）；单课预览面板限制在放大层内部、边缘向内避让（保留）。学生宿主移动端默认视图仍为 3D，`agenda` 作为"按日列表"（不新写）。

**质感**（令牌化清单，DECK_CSS 内替换，自注入机制不变）：
- 周卡 `.cs-card`：`background: hsl(var(--ls-surface-1) / .96)`、`border: 1px solid hsl(var(--ls-line))`、`box-shadow: var(--ls-shadow-3), inset 0 1px 0 hsl(0 0% 100% / .8)`、圆角 `--ls-r-xl`；当前卡 `lq-surface--frost`（blur thin，仅 1 张）。
- 课次格 `.cs-lesson`：**删除 `backdrop-filter`**（内层 `__surface` 不透明，模糊在视觉上只影响待审卡 2–4px 间隙环与圆角，却对每格计算一次；放大层可同时有 30–60 格）；待审卡间隙环改为 `hsl(var(--tone-course-N) / .18)` 无模糊平铺；`__surface` 底 `hsl(var(--tone-course-N))`、高光渐变与 inset 描边改 `hsl(0 0% 100% / .2|.03|.1|.42|.22)`；拟安排 `.cs-lesson--proposed` 两层叠加 `linear-gradient(hsl(var(--tone-course-N) / .25), …), hsl(var(--ls-surface-1))`，文字 `--ls-ink`；圆角 `--cs-radius` 由 `--ls-r-md` 14 供值；缩略卡 `--ls-r-sm`。
- `.cs-adjustment-label`：**删除 `backdrop-filter`**，改 `lq-chip--status --sm` 视觉（`data-tone="warning"` soft 底 + 描边，停课 `danger`，仅换教室 `info`）；字号 ≥11px（`--ls-t-caption`），紧凑档 `.64rem`≈10px 的 `@container` 规则改为隐藏标签只留 8px 状态点 + `title`（与迷你卡一致）。
- 时段带 `.cs-grid__band--*`：`--tone-band-dawn 43 96% 56%`、`-am 199 89% 48%`、`-pm 239 84% 67%`、`-eve 215 25% 27%`，底 `/ .18–.24`，字色用对应 `-fg`。
- 连线：`stroke` 用 `scheduleChangeColors` 输出（改为返回 `--tone-course-N` 的 `hsl()` 串）；标签 rect `hsl(var(--ls-surface-1))` + `stroke: currentColor`，文字 `--ls-ink`、12px/600；hover/focus rect `hsl(var(--ls-primary-soft))`。
- 焦点环 `outline: 2px solid hsl(var(--ls-ring))`；定位高亮 `outline: 3px solid hsl(var(--tone-warning-base))` + `::after` 底 `--tone-warning-fg` 字白。
- 放大层遮罩：`.cs-expand` 底 `hsl(var(--ls-scrim))`，**去掉 blur(6px)**（scrim 无模糊，§9.1）；`.cs-expand__card` 为 `lq-glass--thick`（这是页内唯一持续宿主；3D 舞台当前卡 frost 在放大层打开时被遮，不计）。
- 降级：`prefers-reduced-transparency/contrast` 分支保留并简化为"去 frost、去渐变、chip 实色"；`data-lq-glass=off` 同效。
- 字体：标题 `.86rem/850` → `--ls-t-sub`/700；教室 `.68rem` → `--ls-t-caption`；连线标签 12px。

**交互**（全部保留现行为，只列出与新组件相关或需明确的项）：
- 舞台：滚轮 `scheduleWheelIntent`（≥32px 累积切一周，边界放行页面滚动）；拖拽阈值；←/→ 切周、Home/End 首末周（补齐）；滑杆 `input` 即时切周；点击当前周卡 → `openExpanded()`。
- 放大层：‹/› 切周；Esc → 若单课预览开则关预览，否则关对话框（工作区断言）；遮罩点击关对话框；焦点进入到"返回 3D 视图"按钮，关闭后回到触发元素。
- 课次格：桌面 hover（≥4px 真实位移后才触发）→ 单课预览 120ms 可逆展开；点击格 → 固定预览；`__main` 为 `<a>` 时 Enter/Ctrl+点击进入课堂（保留）；触屏首点预览、第二点进入课堂（保留）。
- 调整标签：点击/Enter 切换 `aria-expanded` 与 `.cs-adjustment-details`（工作区 `setChangeDetail`）；"↩ 原位置 / ↗ 新位置 · 第N周" → `focusLesson(eventKey, weekIndex)` 切周并 `is-counterpart-focus` 3s；不在筛选内 → 播报"对应课程未在当前筛选结果中显示…"（保留文案）。
- 连线标签：点击/Enter → 同上跳转；`tabindex=0`、`role=button`、`aria-label` 含课程名与"时间更改"。
- 同步（教师）：`lq-btn--glass --sm` "同步" → `lq-menu`【同步教务课表 · 权限说明 / 同步智慧课堂 · 权限说明】→ 忙碌 `is-loading` → 结果 `LQ.toast`（文案写明来源与数量）→ `setOverview({keepWeek:true})`；宿主状态行只更新一处。
- 学生宿主：模式 `lq-segment` 三态互斥（`aria-pressed` 改 `role=tab`）；"回本周"仅当 `activeIndex !== currentIndex` 时显示；"放大课表" → `openExpanded()`；学期切换 → 取消旧请求，迟到响应丢弃（保留）；失败 → `lq-empty data-reason=error` + [重新加载]。

**动效**：周切换 `--ls-spring-soft`（现 transform 过渡）；单课预览 120ms 可逆（保留实现，时长改读 `--ls-dur-fast`）；短/全名交替 80ms（保留）；定位高亮 3s 后淡出 `--ls-dur-base`；reduced-motion：不 `rotateY`，切周只 opacity，预览直接终态（保留）。

**无障碍**：舞台 `tabindex=0 aria-label`（保留）；周次指示 `aria-live=polite`；两个 `role=status` 只在用户动作后写入且相同文本不重复；放大层 `role=dialog aria-modal aria-label="整周课表"`；每格 `aria-label`="课程 · 星期X 第N节 · 教室 · 待审变更（若有）"；迷你卡（3D 缩略层）不可单独聚焦，整卡 `aria-label` 含"N 项待审"；连线图 `<svg role="img" aria-label="本周 N 条调课连线">` + 标签可聚焦；`title` 不作为触屏唯一说明（展开态提供完整信息）。

**契约**（必须原样保留，迁移后逐条 grep）：
- 公开 API：`createScheduleDeck(container, {title, description, showTermSelect, onTermChange, emptyHtml, onNavigate, compactSummary, onWeekChange})` 返回 `goToWeek/focusLesson/showAdjustment/openExpanded/setOverview(overview,{keepWeek})/getActiveWeekIndex/destroy`；纯函数 `scheduleWheelIntent/pendingScheduleChange/countScheduleLessons/scheduleLessonLanes/scheduleChangeLabel/courseAccentFor`；三个子模块的导出与 `.d.ts`（`scheduleChangeConnections/scheduleChangeColors/routeScheduleChanges/roundedScheduleRoute/compactClassroomName/classroomChangeState/adjustmentActionText`）。
- DOM：`data-csd-term/stage/indicator/prev/next/slider/feedback/expand-*`、`data-event-key`、`data-csd-change`、`data-cs-lanes`、`dataset.weekIndex`；类名族 `.cs-deck/.cs-stage/.cs-card/.cs-grid*/.cs-lesson*/.cs-adjustment-*/.cs-expand*/.cs-change-*/.is-preview*/.is-counterpart-focus`（外部 CSS `ui-system.src.css:55817-55845,63181-63182` 与 e2e 依赖）；`#course-schedule-deck-style` 自注入。
- 行为：所有周同时在 DOM、`offset∈[-1,5]` 可见；`.cs-stage overflow:clip`；`.cs-expand` 自管、body 追加、**不接入 `LQ.layer`**（登记为外部层，`LQ.layer.closeTop` 先询问 deck）；预测不计正式课时；同一变更所有投影共用真实 `session_id`；连线只连快照显式端点、从不自行配对；`reason` 失败码与 fallback 文本；`reused` 几何复用；`hoverArmed` 4px 门槛；Esc 顺序；教室简称规则；按钮文案五种；120ms 可逆动画与零滚动坐标；`?v=` 联动（4 模块 + 3 宿主）。
- 学生宿主：`data-student-*` 全部钩子、模式三态、`courses-payload`、"只显示本平台课程"常显、学期请求取消；`/api/dashboard/course-schedule/overview` 授权规则；教师宿主：`lanshare:dashboard-calendar-invalidate` 事件、`localStorage` 学期 key、TDZ 注意（`dashboard.js:499-509`）。

**施工步骤**（每步一个提交，先合并工作区 +44 行）：
1. `course_schedule_deck.d.ts` 补 `createScheduleDeck` options/实例类型与 `courseAccentFor`；vitest 类型检查通过。
2. `DECK_CSS` 令牌化：51 hex/所有 rgba → `--ls-*`/`--tone-*`（登记 lint 例外"自注入机制"，但**值**必须是 `var()`）；`COURSE_PALETTE` 改为首次 `getComputedStyle(document.documentElement).getPropertyValue('--tone-course-N')` 读取并缓存，`courseAccentFor` 返回值格式不变；`scheduleChangeColors` 同源。
3. 删 `.cs-lesson`/`.cs-adjustment-label` 的 `backdrop-filter`，删 `.cs-expand` 的 `blur(6px)`；`.cs-expand__card` 加 `lq-glass--thick` 规则（模块内复制关键声明，不依赖外部 CSS 加载顺序）；周卡 `lq-surface` 质感；当前卡 frost。**前后截图 diff**：放大层格子视觉差 ≤ 阈值（预期几乎不可见），待审卡间隙环用平铺 tint 复现。
4. 调整标签改 chip 形态与 `data-tone`；紧凑档改状态点；时段带令牌。
5. 头部/放大层按钮换 `lq-btn` 类（模块内输出类名，样式由全局 `lq/components/button.css` 提供；模块自注入 CSS 只保留布局）；滑杆 `lq-slider`；学期 `lq-select`。
6. 学生宿主 partial：模式 `lq-segment`、周导航按钮、回本周条件显示、状态行 `lq-status-line`、`lq-empty` 错误态；`ui-system.src.css:63181` 段随之调整。
7. 教师首页与课时统计页：同步入口合并为按钮 + `lq-menu`；结果 toast 文案；`[data-cs-toast]` 改 `LQ.toast`。
8. 补 Home/End 键；连线图 `role=img` 与标签 aria。
9. 四模块 + 三宿主 `?v=` 统一 bump（或改 `asset_url`/`__LS_ASSET_REV`）。
10. 全部课表测试跑绿；`docs/dashboard-schedule-refinement-2026-09-06.md` 追加"lq 迁移"小节。

**收尾**：`ui-system.src.css:55817-55845` 面板壳段改令牌或并入 `lq/pages/dashboard.css`；`course-schedule-hours` 与 `semester-calendar-panel` 记忆更新（新模块群、glass 移除决定）；`lq-lint-exceptions.json` 登记 `DECK_CSS` 自注入；台账三宿主页状态推进；§15.9。

**验收**：`npm test`（5 个课表 vitest）；`npx playwright test --config tests/e2e/components/playwright.config.ts`（deck/academic-deck/lines/density/sync 五 spec，含工作区新增断言）；`dashboard-schedule.spec.ts` 12 条；`test_student_course_schedule.py`；截图 1440/1024/390 × 亮/暗/off × 教师/学生 × 3D/放大/预览/连线；`audit_glass_layers`：放大层打开时页内 blur 宿主 = 1（`.cs-expand__card`），关闭时 ≤1（当前周卡 frost）；DECK_CSS hex = 0；4× CPU 节流下放大层滚动无 >50ms 长任务且比迁移前不劣化（记录前后 trace）。

### 13.6 课堂主页 `classroom_main_v4.html`

**现状与证据**：2346 行，`<style>` 15–307（293 行，5 hex），`<script>` 314–328（`window.APP_CONFIG`）、2343（`#cw-deferred-assets` JSON）；岛屿 `[data-island-id="classroom-page-main"]` :338 → `classroom-page.tsx` 动态 import 11 个 legacy 模块（`LEGACY_MODULES` 9 处手写 `?v=`）；区域：顶栏 341–460（品牌返回 :342、`h1.cw-course-title` :345、区段跳转 359–371、`<details class="classroom-topbar-menu">` ×2 379–456）；课次区 484–690（`#hero-course-detail-btn` → `#course-info-popover` :2115、课次导航 `#teachingTimelineScroll`、`#teachingSessionModal` :577、AI 任务条 :621、点名 :655）；学习进度 692–899；任务区 902–1170（`partials/classroom_task_preview.html`、`#assignment-list` SSR 卡片）；活动 dock 1173–1215（五 tab `discussion|interaction|collaboration|polls|resources`）；面板 1216–1511（投票、互动、研讨室 `#discussion-room` 含嵌套 `discussion-room-tabs` 研讨室/一对一、`#chat-*`、表情 popover :1326、私信 :1378、协作、资源上传）；材料区 1512–1600（批选 :1538/:1545）；弹层 1571–2115（材料详情、AI 期末材料、共享文件、作业编辑器、考试布置）；`#course-info-popover` 2115–2236；末尾 includes + 结课弹层 :2295。CSS `classroom.css` 段 12004–25182（13,180 行）。e2e：ui-v3 43 场景（监听器/socket 不增、reduced-motion、草稿保留）、`classroom.spec.ts`、`classroom-members-tabs`、`classroom-group-qr`、`home-classroom-business`、`classroom-task-card-layout`。

**功能**（教师）：看课程/课堂信息与统计；课次导航与详情、管理课次、AI 任务条、点名；看学习进度；新建作业/添加考试/分组配置/分类；材料浏览/批选/详情/AI 期末材料；活动：研讨室聊天（表情/自定义表情/引用/@全体/附件）、一对一私信、课堂互动、小组协作、投票、资源上传；班级成员/修为；结课；个人入口。（学生）同上除管理项；提交作业入口；互评；协作入组。

**形态**（immersive）：
```
lq-topbar--immersive  [返回首页 ghost icon] 课堂名 · [课次 chip] [修为 chip] · [签到 glass sm] [更多 lq-menu: 班级成员/修为 · 结课(教师) · ─ · 消息 · 反馈 · 个人中心 · 账号安全 · 退出]
主区 lq-section×4
  课次: lq-deck--row(横向课次卡 lq-card--interactive, 拖只浏览, 点击 → lq-drawer 课次详情) + [全部课次 soft sm][定位课次 ghost sm] ; AI 任务条 lq-job ; 点名面板 lq-card
  学习进度: lq-card--stat×3 + lq-progress + 趋势 lq-bars + 成长机会 lq-list
  任务: lq-tabs[待处理|已提交|全部] + lq-card 列表(SSR 真实卡片, React 原子接管) + 教师工具 [新建作业 soft][添加考试 soft]
  材料: 面包屑 lq-crumbs + lq-list + 批选 lq-bulk-bar
活动区 ActivityHost(单实例)
  ≥1280: 右栏 lq-surface 宽 minmax(320px,400px) sticky, 内 lq-tabs[研讨室|互动|协作|投票|资源]
  <1280: 底部 lq-dock 五项 → lq-sheet--bottom 承载同一节点
  研讨室: lq-segment[研讨室|一对一] · 消息流 lq-bubble · composer lq-glass 胶囊(展开/@全体/附件/表情 lq-popover/发送 prominent icon)
弹层: 6 个 .modal-backdrop → lq-modal/lq-drawer ; 成员工作区 lq-drawer--wide(内 iframe 保留) ; 结课 lq-modal + LQ.choose ; #course-info-popover → lq-popover(stats|details 两面板)
```

**布局**：≥1280 `grid-template-columns: minmax(0,1fr) minmax(320px,400px)`，主区间距 32；1024–1279 单列 + Dock；<768 单列，课次卡横滚，材料列表单列；顶栏 52/56；Dock 高 56 safe-area。

**质感**：顶栏 `lq-glass`、Dock `lq-glass`（同屏 ≤2 持续宿主，Dock 只在 <1280）；右栏与卡片 `lq-surface`；课次卡当前课 tint 描边；任务卡状态 chip `assignment/submission` 族；聊天己方 `--ls-primary-soft`；composer `lq-glass` 胶囊（放大层/sheet 打开时它不再计入）；页面 `lq-ambient` 静态。

**交互**：
- ActivityHost 断点切换：`matchMedia(--bp-xl)` 变化时把同一 DOM 节点 `appendChild` 到右栏或 sheet 容器，**不销毁不重挂**；聊天输入、滚动位置、socket 保持；切换后 `lq-tabs` 当前 tab 不变。
- 活动 tab 切换 120ms cross-fade，面板 `hidden` 但不卸载；嵌套研讨室/一对一 `lq-segment`。
- 课次卡：拖动只浏览（阈值 6px），点击/Enter/Space 开 `lq-drawer`；单 Tab 停靠 + ←/→/Home/End；卡底按钮 36 视觉/44 触控。
- 任务卡：整卡链接 + `__actions`（教师：分类/分组配置/批改；学生：去作答）；分类 `<dialog>` 保留。
- 材料批选：全选本页 → `lq-bulk-bar` 常驻底部（Dock 之上）。
- 聊天：Enter 发送（尊重 `isComposing`），Shift+Enter 换行；表情 `lq-popover` 三段；引用块点击定位原消息（保留）；图片走灯箱 API。
- 两个 `<details>` 菜单合并为一个 `lq-menu`（风险项"结课"单独分组）。
- 结课：`lq-modal` 列出未结束项 → `LQ.choose`【按默认分收口 / 仅结束 / 返回】（承接 `classroom_closeout.js` 硬约束）。
- 课程信息 popover：两面板 `lq-segment` 切换，锚定标题按钮。

**动效**：sheet `--ls-spring-soft`；tab cross-fade；课次卡 hover 位移 2px；reduced-motion 全关且 sheet 直接显示。

**无障碍**：`main` landmark；活动区 `aria-label="课堂活动"`；Dock `role=tablist`；sheet 打开时焦点进入面板首元素，关闭回 Dock 项；聊天消息流 `role=log aria-live=polite`（新消息只播报一次）；`[inert]` 隐藏 tabpanel 保留。

**契约**：`[data-island-id="classroom-page-main"]`、`[data-classroom-page-app]`、`window.APP_CONFIG`、`#cw-deferred-assets`、`LEGACY_MODULES` 动态 import；`[data-classroom-activity-tab|-target|-panel]` 五 key、`[data-classroom-message-tab]`；`#chat-*` ids 与 `.chat-message*` 族、`has-emoji-popover-open`；`#course-info-popover` 面板 key 与 `[data-course-popover-target]`；`[data-cw-open/-source/-external-modal]`、`[data-workspace-section]`、`[data-anchor-order]`；`[data-student-insight-frame]` + `[inert]` + 草稿守卫；`collab-/poll-` overlay 行为等价；`classroom_closeout.js` 两条硬约束；跨课堂待办不进课堂页；ui-v3 监听器/socket 断言。

**施工步骤**（四批，每批可回滚）：
1. **入口映射表**（文档产物）+ 壳与顶栏：顶栏组件化、两菜单合一、`lq-ambient`；删 293 行内联；`classroom.css` 段拆出 `shell` 子文件。
2. 主区四 section：课次 `lq-deck--row` + drawer、进度、任务（SSR 卡片类名替换但结构保留）、材料 + 批选；`classroom.css` 拆 `session/progress/tasks/materials`。
3. 活动区：ActivityHost 单实例 + 右栏/Dock/sheet；聊天气泡/composer/表情 popover 换皮；`chat.js` 16 处 `style=` 改类；`classroom.css` 拆 `activity/chat`。
4. 弹层与成员工作区：6 个 `.modal-backdrop` → `LQ.layer`；成员 drawer；结课 modal；`collab-/poll-` overlay 接入；`classroom.css` 拆 `modals`；删除无消费者段。

**收尾**：`classroom.css` 13,180 行按消费者清单删除（目标按删后实测记录）；`LEGACY_MODULES` `?v=` 改注入；`home-classroom-ui-v3` 与 `home-classroom-business` spec 更新选择器（结构不变处不改）；`.codex-temp/home-classroom-redesign-*` 旧基线不动；`offering-hub-page`、`group-scheme-system`、`poll-system`、`classroom-closeout` 记忆更新类名；§15.9。

**验收**：ui-v3 43 场景 + 5 条 specs；新增"跨断点 20 次切换：草稿/滚动/选中保留、监听器与 socket 不增"；`layout-stability`（CLS ≤0.05）；截图 1440/1024/390 × 亮/暗/off × 教师/学生；blur 宿主 ≤2。

### 13.7 首页（教师/学生）与学期日历

**现状与证据**：`dashboard_teacher.html` 184 行（extends `manage/layout.html`；`[data-dashboard-root]` :22 含 `data-initial-group-mode="schedule3d"`；hero :26；"需要处理" `[data-agenda-reminder]` :41 + `partials/dashboard_agenda_widget.html`（145 行，`[data-agenda-item][aria-haspopup=dialog]` 开共享 todo modal）；我的课堂 :67（`<details>` 筛选、组模式 tabs 列表/系别/课程/3D、`[data-offering-list]`）；评估菜单 `<details>` + `.dashboard-evaluation-menu__popover` :108；去哪里 `.ls-domain-card` :130；日历 include :149（`semester_calendar_compact=true`）；`.academic-evaluation-modal__dialog` :155）；`dashboard.html` 182 行（extends `base_navbar`；`<details class="ls-tools-menu">` :28；学生课表 partial :36；`.ls-focus--student` :39；学习与成长 ≤4 入口 :93；日历 :129）；`dashboard.js` 1603 行（组模式动画折叠 :822-1102、3D 面板移动不重建 :679-820、TDZ 注意 :499-509、同步 :522、日历懒加载事件 :619-677、评估菜单 :1339）；`semester_calendar.js` 2074 行 0 hex（sticky 首列 `semester-sticky-cell`、todo modal `.semester-todo-modal-card` :1270、拖动与 scroll-snap 抑制 :670）；CSS `dashboard.css` 段 25183–26928、`semester_calendar.css` 26929–28624。e2e：`dashboard-schedule` 12、`dashboard-todo-modal`、`semester-calendar-dialog`、`teacher-app-shell`、`home-classroom-ui-v3`。

**功能**（教师）：问候与日期；需要处理（收件箱真实项、计数、局部刷新、添加待办、日历订阅、同步）；我的课堂（搜索、筛选、四种组模式、课堂卡进入课堂/管理）；评估菜单（同步教务评价）；去哪里（六域卡）；学期日历（周视图、待办甘特、定位今天/回到开头、添加待办）。（学生）问候；课表三模式；需要处理；学习与成长 ≤4 入口；日历（可添加/完成/删除个人待办）。

**形态**（教师 = `sidebar` 壳内总台页；学生 = `topbar` 壳总台页）：
```
lq-page-head(问候 h1 · 日期 · __aside: 收件箱计数 lq-card--stat×≤3)
lq-section 需要处理: lq-list(行 = 类型 chip agenda 族 · 标题 · 截止 lq-clock 简式 · 操作) + [添加待办 soft sm][订阅日历 ghost sm][同步 glass sm]
lq-section 我的课堂(教师) / 课程日程(学生): 工具行 filter_bar(搜索 + lq-chip-row 筛选) + lq-segment[列表|系别班级|按课程|3D课表] → 列表: lq-grid--cards 课堂卡 lq-card--interactive ; 3D: deck 面板(§13.5)
lq-section 去哪里(教师): lq-grid--cards lq-card--interactive×6(域图标 + 名称 + 一句话) ; 学习与成长(学生): ≤4 个 lq-card--interactive
lq-section 学期日历: lq-surface 面板(工具行: 学期 lq-select · [回到开头 ghost][定位今天 soft][添加待办 soft]) + 周网格(sticky 首列) + 待办甘特 lq-progress--bar(tone agenda 族)
弹层: 待办 lq-modal(接 LQ.layer) ; 评估菜单 lq-menu ; 评估同步 lq-modal
```

**布局**：总台页骨架；≥1280 右栏（教师：日历缩略 + 评估菜单；学生：无）；课堂卡网格 `minmax(280px,1fr)`；日历面板高 auto、横向滚动容器 `[data-semester-calendar-scroll]`，**容器不 `overflow:hidden`**（sticky 依赖）；<768 各 section `lq-section--collapsible`（记忆），错误/当前步骤不折叠；学生首页移动总高 ≤4200px。

**质感**：壳顶栏/侧栏 `lq-glass`；section 无卡套卡；课堂卡 `lq-card--interactive` 左侧 4px 课程色条 `--tone-course-N`（K14 C1）；收件箱行类型 chip `agenda` 族；日历学期归属带 tint、今天列 `--ls-primary-soft`、待办条 `--tone-agenda-*`；统计卡数字 count-up 可选（HTML 初值为真）。

**交互**：组模式切换保留动画折叠与 3D 面板"移动不重建"；筛选/搜索 URL 与 localStorage 同步（保留）；收件箱项点击 → 深链或 todo modal（`aria-haspopup=dialog`）；日历拖动横滚、定位今天时抑制 scroll-snap（保留）、待办条点击 → 同一 todo modal；添加待办 → modal 表单；评估菜单 `lq-menu`（`is-ready` 状态保留）。

**动效**：section 进入 stagger；组模式折叠高度过渡 `--ls-dur-slow`；日历定位今天平滑滚动（reduced-motion 直接跳）。

**无障碍**：`[data-agenda-list]` `role=list`；收件箱行按钮 `aria-haspopup=dialog`；日历周网格 `role=grid` 保留现有 aria；统计卡 `aria-label` 含数值。

**契约**：`[data-dashboard-root]` 全部 dataset、`[data-agenda-todo-options]`、`[data-dashboard-workspace-payload]`、`[data-student-schedule-courses-payload]`、`window.DASHBOARD_SEMESTER_CALENDAR/ACADEMIC_EVALUATION_SYNC`；组模式 key 与 `schedule3d` 教师默认；localStorage key；`lanshare:dashboard-calendar-open|scope|invalidate`；`[data-semester-calendar-*]` 全部；`semester-sticky-cell`；`.semester-todo-modal-card` 与 `dashboard-todo-modal.spec.ts`；`work_inbox` 项形状；`--ls-schedule-stage-height` SSR 预留；五配色 SSR 首屏；`teacher-app-shell.spec.ts` 顶栏同构。

**施工步骤**：1) 教师首页：page_head 升级即生效；section 标题/卡片/chips 换类；评估菜单 → `lq-menu`；2) 学生首页：同上 + K14 结构项（A1 继续学习入口、A2 移动折叠顺序、C3 侧栏降权）；3) 日历：面板 `lq-surface`、工具行按钮、待办条 tone、todo modal → `LQ.layer`；4) `dashboard.css`/`semester_calendar.css` 段拆 `lq/pages/dashboard.css`、`lq/pages/calendar.css` 并删旧段。

**收尾**：`student-dashboard-improvement-goals.md` 按 K14 标注取消/并入项；`semester-calendar-panel` 记忆更新；`ux-overhaul` 记忆的 stage-9 折叠改 `LQ.collapsible`；§15.9。

**验收**：`dashboard-schedule` 12、`dashboard-todo-modal`、`semester-calendar-dialog`、`teacher-app-shell`、`home-classroom-ui-v3`、`layout-stability`（CLS、学生首页高度）；截图 1440/390 × 亮/暗/off × 六配色抽查两套；blur 宿主 ≤2。

### 13.8 白板与考试白板

**现状与证据**：`static/js/whiteboard/` 21 模块 4,741 行（`board.js` 1136、`exam_board.js` 499、`panels/*` 7 个、`popover.js` 4 行垫片 `createPopoverSystem({prefix:'twb'})`）；入口 `teacher_whiteboard.js` 20 行；CSS 段 37056–38541（1,486 行，31 hex；`twb-` 子段 :37523）；颜色常量 `constants.js:65-66,111-120`（默认 `#ff0000`，8 色 swatch）；`is-drawing` 关 blur `board.js:183-192`（260ms 释放 `constants.js:40`，CSS :37250）；导出 `export.js` 三格式 + 边界/像素上限；远端持久化 `/api/materials/{id}/whiteboards` 409 冲突；`z-index 2400` :37070；运行时变量 `--teacher-whiteboard-*` :37065-37069；`exam_take.html:937,1391` 覆盖类名；13 个 vitest；e2e `whiteboard.spec.ts`、`whiteboard-regression.spec.ts`。

**功能**：画笔/橡皮/文字/形状；颜色与粗细；撤销/重做；平移/缩放/网格；历史面板；保存（本地/远端、冲突处理）；导出 PNG 白底/透明/JPG；全屏；考试白板：按题作答、随答案持久化。

**形态**：
```
lq-wb-toolbar(= lq-glass 胶囊浮条；桌面顶部居中 / 移动 Dock 位)
  组1 工具: lq-btn--glass --icon ×N(选中 = tint thumb)  组2 颜色 chips(8 色 lq-chip 圆点 + 自定义) + 粗细 lq-slider(popover)  组3 历史: 撤销/重做/历史面板  组4 保存 lq-menu / 导出 / 全屏
popover: 颜色/粗细/橡皮 → twb popover 换皮 lq-popover ; 确认 → lq-confirm ; 保存菜单 → lq-menu ; 历史 → lq-drawer ; 导出 → lq-modal
FAB 入口 lq-fab
```

**布局**：工具条高 48（移动 56），画布内绝对定位保留现实现；全屏 `--ls-z-viewer`；考试内嵌时工具条在题卡内顶部。

**质感**：工具条 `lq-glass`（**绘制期间 `is-drawing` 切换到局部 `data-lq-glass=off`**，保留 260ms 释放）；颜色 chip 显示的是**用户内容色**（登记例外，不令牌化）；选中工具 tint；画布背景由 `--teacher-whiteboard-bg-alpha` 等运行时变量驱动（不收编）。

**交互**：全部保留（键盘 B/E/T/H、Ctrl+S、`[`/`]`、Esc 链：先关 popover 再关板）；工具条切换不截断笔迹；触控 pointer capture。

**动效**：popover 160ms 开/120ms 关（保留，值改读令牌）；reduced-motion 即时。

**无障碍**：工具按钮 `aria-pressed` + `aria-label`；popover `role=dialog/menu` + 焦点回归（`ui_popover` 已有）。

**契约**：`teacher-whiteboard-*` 与 `twb-*` 类名冻结；`createPopoverSystem` 导出；`is-drawing` 机制；颜色常量与持久化格式；导出格式与上限；`initTeacherWhiteboard/initExamDrawingWhiteboard` 签名；13 单测 + 2 e2e。

**施工步骤**：1) 工具条容器加 `lq-wb-toolbar lq-glass` 并把 `is-drawing` 分支改为切换局部 `data-lq-glass=off`；2) 按钮/chip/slider 换 `lq-*` 类（保留旧类名并存到 S7）；3) 面板换皮（popover/confirm/menu/drawer/modal）经 `ui_popover`→`LQ.layer` 本体，`popover.js` 垫片不改；4) 31 hex → 令牌（笔迹色除外，登记例外）；5) `z-index` → `--ls-z-viewer`。

**收尾**：CSS 段 37056–38541 迁 `lq/components/wb-toolbar.css` + `lq/pages/whiteboard.css`，删旧段；`whiteboard-system` 记忆更新；§15.9。

**验收**：13 单测；`whiteboard*.spec.ts`；B14；绘制期 DevTools 无 backdrop-filter 重绘；导出不含 UI；亮暗切换前后旧内容与导出一致。

### 13.9 AI 浮窗与 Agent 工作台

**现状与证据**：`ai_chat_component.js` 1493 行（FAB `#ai-chat-fab`、720×680 默认、最小 360×440、移动断点 768、toast `notifyAIChat` :19）；`ai_workspace_widget.js` 3236 行（轮询 5000/2500/10000ms、SSE `/api/agent-tasks/{id}/stream` :1959 + 退路、状态表 :867、核验 :845、红黄绿 FAB :684、提问表单 `data-question-signature` DOM 保留 :1602、过期 :1640、附件限制 :5-14、历史抽屉 `#ai-agent-history-drawer` :2470）；CSS 段 5675–8219（2,545 行，45 hex）；`markdown_runtime.js` 346 行自研净化；`agent_user_confirmation.js` 三级确认模态；e2e `agent-questions.spec.ts`、`agent-user-confirmation.spec.ts`、`ai-chat-markdown.spec.ts`。

**功能**：打开/最小化/全屏/关闭浮窗；拖拽/缩放；选模型与思考强度；发消息（文本/附件/图片粘贴）；流式接收 Markdown；历史会话；Agent 任务：提交、看状态与事件流、回答提问、取消、核验结果、看回执与对账、确认破坏性操作（三级确认）。

**形态**：
```
lq-fab--prominent(AI) + 红黄绿点 → agent 族 tone
浮窗 lq-glass--thick 圆角 28 (全屏态 → lq-surface)
  __head: 标题 · lq-segment--sm[模型/强度] · [历史 ghost icon][最小化][全屏][关闭]
  消息流: lq-bubble(己方 primary-soft / 对方 surface-2) + lq-prose ; Agent 任务卡 lq-job(agent 族) + 提问表单 lq-form-section(保留 data-question-signature) + 回执 lq-list + 对账 lq-table--dense
  composer: lq-glass 胶囊 [附件 ghost icon][深度思考 lq-switch--sm][输入 自增≤6 行][发送 prominent icon]
历史 lq-drawer ; 确认 agent_user_confirmation 模态换皮 lq-modal（快照区/勾选/备注保留）
```

**布局**：浮窗默认 720×680，最小 360×440，可拖缩（常量不动）；<768 全屏 sheet；全屏双栏（左会话 `lq-list` 280 / 右对话）。

**质感**：浮窗 `lq-glass--thick`（页内 1 层；全屏无背景可折射 → `lq-surface`）；composer 在玻璃宿主内**不再 blur**，只用填充；任务卡 `lq-job` 状态色 `agent` 族；FAB 点色 danger/warning/success。

**交互**：发送尊重 IME；轮询不打断输入（签名 DOM 保留）；SSE 断开 → 退路轮询 + `lq-status offline`；提问过期 → 表单降级只读 + 说明；取消 → `LQ.confirm`；结果未核验不显示成功色；破坏性操作 → 三级确认模态（快照必读、勾选、备注）。

**动效**：浮窗打开 `--ls-spring-soft` scale；消息进入 `lq-anim-rise`；流式文本无动画；reduced-motion 即时。

**无障碍**：浮窗 `role=dialog aria-label`；消息流 `role=log`；任务状态 `lq-status` 跨等级播报；提问表单字段 label；确认模态焦点圈闭。

**契约**：`AI_WORKSPACE_WIDGET_CONFIG`；轮询/SSE/心跳常量；`data-question-signature`；`/api/agent-tasks/*` 端点；`markdown_runtime.sanitizeHtml` 唯一渲染入口；`agent_user_confirmation.js` 快照/勾选/备注 affordance 与 spec 选择器；`prompt_pool.js`；灯箱程序式 API（同气泡兄弟图片）。

**施工步骤**：1) CSS 段 5675–8219 迁 `lq/components/ai-chat.css` 并瘦身（去 45 hex）；2) 浮窗/composer/气泡换类；3) `notify()` ×2 → `LQ.toast`；4) 任务卡 → `lq-job`，状态映射 `agent` 族；5) 历史 → `lq-drawer`（经 `LQ.layer`）；6) 确认模态换皮。

**收尾**：删旧 CSS 段；`agent-bridge-and-knowledge` 记忆更新类名；§15.9。

**验收**：三条 agent/AI spec；B15；截图浮窗/全屏/移动 × 亮/暗；blur 宿主 ≤1。

### 13.10 材料阅读、HTML 包壳、LessonDoc 编辑器、材料库

**现状与证据**：`material_viewer.html` 232 行（TOC `#viewer-toc` :44、原稿/优化稿 :41/:166、AI 摘要 :55、编辑器覆盖层 `.materials-editor-shell` :200）+ `material_viewer.js` 1048；`material_render_shell.html` 76 行（iframe `#render-shell-frame` **无 sandbox**，同源依赖）+ `material_render_shell.js` 262（历史 :21、slide 深链 + `return_to` :83-107、白板覆盖时隐藏 iframe :122-164、FAB `#render-shell-edit/-slide-rewrite`）；CSS `:52968`、`:63545`；`lessondoc_editor.html` 34 行（`lde-page`，iframe `#lde-frame`）+ `lessondoc_editor/` 25 模块 1,881 行 + `lessondoc_editor.css` 97 行 136 hex（bridge 15s 握手、`operation_id/revision/serial`、409、BroadcastChannel、双 keydown）；`lessondoc_wizard.js` 504 行 49 处 `style=`；`static/lessondoc/2.0/*.css` 引擎皮肤（iframe 内）；材料库 `materials_manage.js` 6,922 行（14 处 `openModal`）、`material_hub.js`、`process_material_*` 9 模块；CSS `materials.css` 段 8220–11744。

**功能**：阅读材料（TOC、原稿/优化稿、AI 摘要）；全屏渲染 HTML 包（前进/后退/首页/折叠、编辑学习文档、AI 改页、白板覆盖）；LessonDoc 可视编辑（页面导航、画布选择/属性、撤销、历史、试运行、保存/冲突/草稿恢复、AI 重写）；材料库管理（树、上传、移动、开放范围、AI 优化、过程材料导入）。

**形态**：
```
阅读页(阅读页骨架): lq-topbar [返回] 标题 · lq-segment[原稿|优化稿] · [AI 解析 glass sm → lq-drawer] ; 左 sticky lq-surface TOC rail(≥1024)/移动 lq-popover ; 正文 lq-prose 42rem
HTML 包壳(immersive): lq-topbar--immersive [后退][前进][首页] 标题 · [折叠 glass icon][放大] ; iframe 全高 ; 右侧 lq-fab 栈(编辑学习文档 / AI 改页 / 白板 / AI)
LessonDoc 编辑器(lq-editor "lessondoc"): bar[返回 · 标题 · lq-status(save 族) · 撤销/重做/历史/试运行 glass sm · 保存 prominent] ; rail = 页面导航 lq-list(缩略) ; main = 1280×720 画布(iframe) 居中缩放 ; aside = 属性 lq-form-section
材料库(列表页骨架): filter_bar + 树 lq-list + 详情 lq-drawer ; 弹层族 → lq-modal
```

**布局/质感/交互/动效/无障碍**：阅读正文纸感 `lq-surface--paper`；HTML 壳 iframe 不加 sandbox、工具条不叠 iframe（§10.9 viewer 规则）；LessonDoc 画布 iframe 内引擎皮肤不改；编辑器 `lq-status` 六态映射 `save` 族（`正在保存/版本冲突/网络中断待重试/内容待检查/有未保存修改/已保存`）；冲突 → `lq-conflict`（下载本地草稿 / 查看并处理冲突）；快捷键跨 iframe 注册保留；材料库 14 处 `openModal` 走桥；分类多选与合班徽章接说明浮窗。

**契约**：`MATERIAL_VIEWER*` 全局；slide 深链 `return_to`/`slide_id`；`/api/lessondoc/editor/editability`；`teacher-whiteboard:state` 事件；bridge/`LESSONDOC.edit`/锚点抑制/`operation_id`/BroadcastChannel；`lde-` 作用域；`2.0/` 引擎；`process_material_*` 契约测试 `test_process_material_workflow_contract.py`。

**施工步骤**：1) 阅读页与 HTML 壳换类（CSS `:52968`、`:63545` 段迁 `lq/pages/material-*.css`）；2) LessonDoc 壳 → `lq-editor`（`lessondoc_editor.css` 136 hex → 令牌，`lde-` 类保留到 S7）；3) `lessondoc_wizard.js` 49 处 `style=` → `lq-steps`/`lq-form-section`；4) 材料库弹层族 → `LQ.layer`，`materials.css` 段拆 `lq/pages/materials.css`。

**收尾**：删旧段；`html-package-learning-docs`、`material-render-system`、`lessondoc-template-system`、`material-library-management` 记忆更新；§15.9。

**验收**：8 个 lessondoc_editor 单测；`materials.spec.ts`、`classroom-material-request.spec.ts`；`test_process_material_workflow_contract`；截图阅读/壳/编辑器 × 亮/暗；编辑器缩放后选框坐标、跨页撤销、双窗口冲突、草稿恢复人工核。

### 13.11 登录、人生一言、状态页族

**现状与证据**：`base_centered.html` 157 行含 137 行 `<style>`（`.login-card` blur(8px)、`.status-card`、`slideUp`、1 hex），`bg_gradient` 块；`student_login_v4.html` 352 行含 200 行 `<style>`；`teacher_login_v4.html` 39 行（渐变底）；`teacher_register_v4` 37；`status` 35、`error` 83（1 `<style>`）、`session_expired` 159（97 行 `<style>`）、`permission_denied` 42；`login_scene.js` 100 行（manifest 场景图、时段过滤、4.5s 超时、`sampleImageTone` → `body.dataset.sceneTone`、`login-scene-active`）；人生一言 CSS 块 50348–52152（液态玻璃卡 :50741 `blur(26px) saturate(1.5)` + `data-tip-tone`；性能层 :52146 背景静止）；`student_login.js` 313、`teacher_login.js` 94。

**功能**：学生登录（账号/密码/记住/找回；场景背景；登录后一言揭示）；教师登录/注册；会话过期重登；错误/权限/状态页返回来源。

**形态**（`centered` 布局）：
```
body lq-ambient(或场景背景图 + 预烘焙模糊) · 中央 lq-login-card
lq-login-card(学生: lq-glass--clear + lq-scrim ; 教师/状态页: lq-glass--thick) 圆角 36 宽 420
  logo · 标题 --ls-t-display · 表单 lq-field×N(lq-input --lg) · [登录 prominent lg 全宽] · 次级 lq-btn--link(找回/切换)
  错误 lq-alert danger 常显于表单顶
状态页: lq-card--status(图标 64 + 标题 + 说明 + [返回 prominent][首页 soft])
一言舞台 .life-tip-stage(改类名 lq-life-tip)：卡 = lq-glass--clear/thick 按 data-tip-tone
```

**布局**：卡宽 `min(420px, 100vw-32px)`；低高度屏（<640px 高）卡内滚动、按钮不被软键盘遮挡（`100dvh` + `visualViewport`）；≤640 圆角 28。

**质感**：学生登录 `--clear`（K17）：fill `.22`、文字白、`lq-scrim` 压暗 `.28`；`data-lq-tone` 由 `sampleImageTone` 决定亮/暗文字；背景图预烘焙模糊（保留性能层，静止）；教师/状态页 `--thick`；输入 `lq-input --lg`（iOS 16px）；一言卡沿用 `:50741` 数值改令牌。

**交互**：背景图 4.5s 未加载 → 卡退回 `--thick`（无闪动：初始即 thick，图加载完成再切 clear，切换 `--ls-dur-base` 淡入）；tier B/C 或 off → 始终 thick；提交 busy 锁定；错误就地；登录成功 → 一言揭示 morph（`fromElement: .lq-login-card`）→ 跳转；会话过期页保留返回来源。

**动效**：卡入场 `slideUp` → `lq-anim-rise`；clear/thick 切换淡入；一言舞台过渡保留；reduced-motion 即时。

**无障碍**：表单 label 显式；错误 `role=alert`；一言舞台 `aria-live=polite` 一次；对比度：clear 卡在每张场景图上正文 ≥4.5:1（S1 逐张验证，不达标者从 manifest 剔除或加局部 scrim 增强）。

**契约**：`login_scene.js` 流程与 `body.dataset.sceneTone`、`login-scene-active`；`data-tip-tone`；`finishLoginWithScene`/`playLoginSceneReveal`；`bg_gradient` 块（教师页）；`status.html` 升级提示用于不支持浏览器。

**施工步骤**：1) `base_centered` 内联 → `lq/pages/centered.css`；`.login-card/.status-card` 换类；2) `student_login_v4` 200 行、`session_expired` 97 行、`error` 内联抽出；3) clear/thick 切换逻辑接入 `login_scene.js`；4) 一言块 50348–52152 迁 `lq/pages/life-tip.css` 令牌化（性能层原样）；5) 对比度逐张验证脚本进 S1。

**收尾**：删四模板内联；删 ui-system 一言块；`life-tip-system` 记忆更新；§15.9。

**验收**：`auth.spec.ts`、`password-recovery.spec.ts`；B16；截图 学生登录 × 3 张场景图（最亮/最暗/中等）× 亮/暗 tone × clear/thick；教师登录、四状态页；软键盘 390×660 可达。

### 13.12 博客、消息中心、个人资料、成长页族、简历、投票/分组、监控/星图、系统管理

每族按 13.0 结构精简列出关键项；未列项 = 不改。

**博客**（`blog.html` 397、`blog.js` 3156、`blog-paper.css` 2732 行 5 hex；legacy `blog.css` 段 28625–31754 待删）
- 功能：栏目浏览、帖子阅读（`?post=` 深链）、发布/编辑（composer modal）、点赞/关注/私信（用户 popover）、举报、阅读进度。
- 形态：`lq-topbar` + 栏目 `lq-tabs`；正文 `lq-surface--paper`（`--blog-*` 映射 `--ls-*`）；右侧浮动 pill `lq-glass`；阅读进度浮条 `lq-glass` 细条；composer → `lq-modal--lg`；用户 popover → `lq-popover`。
- 质感：正文纸感（17px/1.85/42rem 保留）；玻璃只给顶栏/浮条/pill/弹层（≤2 持续宿主）。
- 交互：`[hidden]` 驱动视图切换保留；composer Tab 圈闭由 `LQ.layer` 接管；popover 视口钳制由 `LQ.layer` 接管。
- 契约：`?post=` replaceState、`data-blog-open-post`、`--section-accent` 注入、`.blog-shell [hidden]` 强制隐藏、`blog-composer-revision.spec.ts`。
- 步骤：S0 删 legacy 段 → S6 换类 → `blog-paper.css` 改为 `lq/pages/blog.css`（保留 `--blog-*` 作为别名到 S7）。
- 收尾：`blog-paper-skin` 记忆更新；部署时清远端旧 css（记忆中的坑）。
- 验收：`blog-composer-revision.spec.ts`；截图列表/详情/composer × 亮/暗；正文对比 ≥4.5:1。

**消息中心**（`message_center.html` 164、`message_center.js` 1658、`message_center_bell.js` 258、`message-center-sync.tsx` 243；CSS 段 2888–4579）
- 功能：分类 tab（`?tab=` 同步、折叠）、会话列表、消息流、发送（`can_send`、12s 冷却）、附件/表情、标已读跳转、铃铛未读与弹出。
- 形态：列表页骨架；`lq-tabs`（>8 收纳）；会话 `lq-list`（未读左条）；消息 `lq-bubble`；composer `lq-glass` 胶囊；铃铛 toast → `LQ.toast`（`allowPopup`/`latestUnreadId` 去重）。
- 交互：冷却倒计时在发送按钮内常显"12s"；`can_send=false` 时按钮 `aria-disabled` + 相邻说明；标已读后落点保留（`:743`）。
- 契约：`unreadCountText`/`messageBellAriaLabel` 共享 lib；`[data-send-button]`；`message-center.spec.ts`。
- 步骤：tabs → `LQ.tabs`；列表/气泡/composer 换类；bell 标记 4 处重复 → 一处 partial；CSS 段迁 `lq/pages/message-center.css`。
- 收尾：删旧段；§15.9。验收：`message-center.spec.ts`；B 场景：轮询不重复 toast、切用户不串未读。

**个人资料**（K4 拆分；补充形态）
- 形态：`lq-tabs--vertical`[基本资料|外观|安全|通知|签名|…]；每分区 `lq-form-section`；外观分区：外观 `lq-segment`[浅色|深色|跟随系统] · 透明效果 `lq-switch` · 配色 6 个 `lq-chip` 圆点（`aria-label` 含名称，选中 tint 描边）· 预览条（当前主色按钮 + chip 示例）· 状态 `[data-ui-palette-status]` `role=status`。
- 交互：任一控件变更 → 立即预览（写 `documentElement/body` 属性）→ 800ms 后字段级 PATCH → 成功保持、409 冲突 → 恢复远端值并提示"已在其他设备修改，请重新选择"（不自动覆盖）。
- 步骤：正文分区 partial 化 → 两薄壳 → 外观分区 → 签名分区异步占位保留。
- 收尾：删 `profile.html` 条件 extends；`profile.css` 段（38542–~45236，6,695 行）按消费者清单拆删；`signature-point-workflow` 记忆更新学生端路径不变。
- 验收：`student-signatures.spec.ts`、`signature-points.spec.ts`；B11；截图教师壳/学生壳 × 外观分区。

**成长页族**（learning_path 247、achievements 89、points_shop 150、report_card 217、wrong_book 264、feedback_review 195；各自 `<style>` 或内联；前缀 `path-/achv-/pts-/report-/wrongbook-/review-`）
- 形态：总台页骨架 `lq-page-head`（aside ≤3 `lq-card--stat`）+ `lq-grid--cards`；成就卡 `lq-card`（揭晓 `--ls-spring-bouncy` 一次）；积分商品卡含余额/兑换条件常显；成绩单表 `lq-table`（0 分显示）；错题本 `lq-list` + 复盘入口；反馈评审 `lq-tabs`。
- 交互：兑换 → `LQ.confirm`（写明扣除积分）→ busy 防重入 → 服务器确认后更新余额。
- 收尾：六套内联 `<style>` 与私有前缀删除；`learning_certificate_reveal.js` 33 hex → 令牌（证书图形色可登记例外）。
- 验收：截图六页 × 亮/暗；零值例外可见（余额 0、零分）。

**简历族**（8 模板独立壳 `resume/layout.html`、`resume_console.css` 639 行 138 hex、8 JS 2,958 行、`rz-` 551 处）
- 顺序：1) `rz-` modal ×4 → `LQ.layer`；2) `resume/layout.html` 改 `sidebar` 布局（侧栏项：首页/资料/搭建器/岗位目标/投递/列表）；3) 换类；4) `?v=20260906a` 15 处 → `asset_url`。
- 契约：`revision/render_revision` 双版本显示与旧产物提示；409/428 静默；导出 content-type 门与同源守卫；`RZ.openJob` 轮询字段；预览 iframe。
- 交互：搭建器拖拽有键盘/触摸替代（↑/↓ 移动区块）；保存状态 `lq-status`；生成任务 `lq-job`。
- 收尾：`resume_console.css` 退役；`career-path-network` 记忆更新。验收：`tests/frontend/career_resume.test.cjs` 手工探针 + 截图 8 页。

**投票/分组**（`manage_polls.js` 439、`classroom_polls.js` 670、`collaboration.js` 1786、`group_assignment_config.js`）
- 形态：投票卡 `lq-card`（状态 chip 草稿/进行/结束 → `neutral/info/success`）；选项 `lq-radio/checkbox` 列表；结果 `lq-bars`；`poll-/collab-/ga-` overlay → `lq-modal/sheet`；组卡 `lq-card`（组长/成员 `lq-avatar-stack`）；拖拽分配大屏画布保留，工具条 `lq-glass`；互评 `lq-modal`。
- 交互：投票提交 busy 防重入；分组随机入组 → `LQ.confirm`；互评 20 分制 `lq-slider`。
- 契约：跨班级共享票数、黑名单互斥、可见时机；组方案绑定与 `test_group_assignment_service`。
- 收尾：三套 overlay 删除；`poll-system`、`group-scheme-system` 记忆更新。验收：`classroom-group-qr.spec.ts`、`test_group_assignment_service`、`test_poll*`。

**监控 / 星图**（监控 `manage_system_monitor.js` 624 + CSS 61036–61271；星图 `career_path.css` 647 行 105 hex + 3 JS）
- 形态：`[data-lq-scope="monitor"]` 局部深色（`--mb-*` → `--tone-monitor-*` + 深色 surface 令牌）；资源卡 `lq-card--stat`；SVG 图表 stroke/fill `var()`；进程树 `lq-table--dense` + 缩进 `style="--depth:n"`（唯一允许内联）；AI 解读 `lq-drawer`；终止进程 → `LQ.confirm` destructive + 强制重试。星图 `[data-lq-scope="career"]`：`.career-root` 作用域保留、`[hidden]` 强制隐藏保留、fixed-inset 画布；顶栏 `lq-topbar--immersive` 深色玻璃；方向筛选 `lq-chip-row`；详情 aside/prep → `lq-drawer`（桌面）/`lq-sheet`（移动）；提示 `lq-tooltip`；测试问卷 `lq-modal`；`?v=` 4 处 → `asset_url`。
- 契约：监控轮询与 `STATUS_COLORS` 语义；星图 canvas 引擎与 `data-career-view` 切换；`test_career_postgres_workflow.py`。
- 收尾：`server-monitor-dashboard`、`career-path-network` 记忆更新。验收：截图两页 × 深色；键盘/列表路径可达星图节点。

**系统管理**（`manage/system/*`：用户、权限、组织、集成、后台任务、口令重置）
- 规则：凭据字段 `lq-input type=password` + 显示/复制按钮，保存后不可回读（显示"已设置"）；危险动作（禁用用户、重置口令、删除组织）→ `LQ.confirm` destructive 写明对象；后台任务列表 `lq-job` 行；权限矩阵 `lq-table` 保留横滚；管理员范围服务端复核。
- 验收：`system-permissions.spec.ts`、`system-background-tasks.spec.ts`、`zz-data-safety.spec.ts`。

### 13.13 开课向导页退役（K4，S0）

**决定**：`/manage/teaching/workflow` 及其 iframe 轮播在本项目内退役；更完善的开课向导（结合教务系统同步、选课名单、教材与 AI 助教配置）另立项目与文档，不在本改造范围。

**施工**（一个 PR，可整体回滚）：
1. `manage_nav_service.py:145-154` 删除 `key="workflow"` 项；若 `legacy_hrefs` 机制支持，把 `/manage/teaching/workflow` 登记为 301 → `/manage/teaching/classroom-hub`，否则在 `manage_pages_teaching.py` 加显式重定向路由。
2. `tests/test_manage_nav_service.py:59`（开课准备组的 key 列表）与 `:194`（以 `workflow` 为 active_key 的用例）同步修改；`:194` 改用 `semesters`。
3. 删除 `templates/manage/workflow.html`、`static/js/manage_workflow.js`、`ui-system.src.css:34851-35985`（`Source: manage_workflow.css` 段，1,135 行，含 `.workflow-stage-track/-carousel/-rail`）；附录 A 中 `.workflow-stage-track` 映射行删除。
4. `embedded_mode`（`ui_parts/common.py:1206` 唯一生产者，`manage/layout.html` 8 处消费）：向导是唯一的 iframe 嵌入者；保留标志到 S7，S7 前用 grep 确认无其他 `?embedded=` 调用后连同 `manage/layout.html` 的 8 处分支一起删除。
5. `teacher_onboarding.css` 段（4580–5600）与 `teacher_onboarding.js` 是首次引导弹层，**不属于向导，不删**。
6. 首页"去哪里"域卡与 `work_inbox` 若引用向导 href，改指 classroom-hub。

**收尾**：`manage-center-improvement-plan` 记忆中"向导页被保留"的偏差记录更新为"已退役"；`docs/manage-center-improvement-plan-2026-09-11.md` §12 追加一行；§15.9。

**验收**：`test_manage_nav_service.py` 全绿；`/manage/teaching/workflow` 返回 301；导航搜索 `开课向导` 无结果；CSS 体积下降 ≥1,100 行；`teacher-app-shell.spec.ts` 通过。

## 14. 业务按钮登记表（首批）

Schema（`docs/lq-action-registry.md` 每行）：`actionId | 路由/组件 | 可见角色 / 资源权限 / 前置业务状态 | 类型(button/submit/link) | 变体·tone | 尺寸 | 位置(桌面/移动) | 文案·aria | 图标 | 确认 | 状态(默认/hover/focus/active/disabled/loading) | API·版本字段 | 重复点击策略 | 成功/失败反馈 | 完成后焦点/导航 | 测试编号`。不新增前端权限真源：引用后端已有能力字段，缺失时复用现判定 + 403/404/409 兜底。所有按钮继承 §10.1；下表只写领域差异（状态列、重复点击策略统一：busy 锁定 + `aria-busy`）。

### 14.1 试卷编辑器

| actionId | 可见/前置 | 类型 | 变体 | 尺寸 | 位置 | 文案 | 确认 | 反馈/完成后 | API |
|---|---|---|---|---|---|---|---|---|---|
| `exam.save` | `can_manage`；≥1 题 | button | prominent（唯一） | md | 顶栏右 / 移动底栏右 | 保存试卷 | 评分不完整 `LQ.choose`；有作答改题 预先禁用+说明 | status synced；新建跳编辑页；409/400 `lq-alert` 内容保留 | `POST/PUT /api/exam-papers` |
| `exam.preview` | 任何 | button | glass | sm | 顶栏右 | 全屏预览 | 无 | `lq-modal--full` | — |
| `exam.ai` | `can_manage` | button | glass | sm | 顶栏右 | AI 出题 | 无 | `lq-modal--lg` + `lq-job` | `POST /api/ai/exam/generate` |
| `exam.import` | `can_manage` | menu 项 | — | — | 更多 | 导入 JSON | 覆盖 confirm destructive | 摘要常显；失败保留原试卷 | `POST /api/exam-papers/import-json` |
| `exam.rubric` | 任何 | button | glass | sm | 顶栏右 | 评分标准 | 无 | `lq-modal--lg`；未完整 warning 徽点 | — |
| `exam.cancel` | 任何 | link | ghost | sm | 顶栏左 | 返回试卷库 | dirty confirm | 跳列表 | — |
| `exam.page.add` / `exam.question.add` | `can_manage` | button | soft | sm / md | rail 底 / 主区页尾 | 新增页面 / 新增题目 | 无 | 追加并聚焦 | 本地 |
| `exam.question.delete` | `can_manage` | button --icon | ghost | sm | 题卡 actions | 删除第N题 | confirm destructive + 撤销 toast 8s | — | 本地 |
| `exam.rubric.distribute` / `exam.rubric.apply` | 评分弹层 | button | soft / prominent | sm / md | 弹层头 / foot | 均分总分 / 完成评分 | 无 | 合计常显 / 关弹层 | 本地 |
| `exam.ai.generate` / `exam.ai.cancel` / `exam.ai.apply` | AI 弹层 | button | prominent / destructive-soft / prominent | md | foot / foot / 预览底 | 开始生成 / 中断生成 / 应用到试卷 | 中断 confirm；应用覆盖 confirm | `lq-job`；status dirty | generate / cancel / 本地 |
| `exam.scope` | `can_manage` | 原生 select | `lq-select` | md | rail 设置 | 开放范围 | 缩小范围说明常显 | — | `PATCH …/attributes` |

### 14.2 布置弹窗

| actionId | 可见/前置 | 类型 | 变体 | 位置 | 文案 | 反馈 | API |
|---|---|---|---|---|---|---|---|
| `assign.new` / `assign.fromLibrary` | 教师 | button | soft | 任务区头 | 新建作业 / 添加考试 | `lq-modal--lg` | `GET /api/exam-papers` |
| `assign.kind` | 弹层 | `lq-segment` | — | 首行 | 作业/期中/期末 | 未选就地错误 | — |
| `assign.schedule` | 弹层 | `lq-segment` + 日期 | — | 第二组 | 长期/截止/倒计时 | 迟交 `lq-switch` 展开 | — |
| `assign.publish` | 弹层 | submit | prominent | foot 右 | 布置到课堂 | 关弹层 + 卡插入 | `POST …/assign` / `POST /api/assignments` |
| `assign.saveDraft` | 弹层 | button | soft | foot 左 | 存为草稿 | chip draft | 同上 `status:new` |

### 14.3 学生作答页

| actionId | 可见/前置 | 类型 | 变体 | 尺寸 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|---|
| `submit.send` | 学生；published & accepting & 队列空闲 & 无 409 | submit | prominent（唯一） | lg | foot 右 / 移动底栏 | 提交作业（补交期"补交作业"） | 未答 `LQ.choose` | 锁定；成功 → 互评 → 刷新；409 → conflict；`p03-submit-assignment` | `POST /submit` |
| `submit.withdraw` | 已提交且窗口内 | button | destructive-soft | sm | 提交卡更多 | 撤回提交 | confirm destructive | 刷新 | `DELETE /withdraw` |
| `submit.redoRequest` | graded & homework & 非缺交 | button | soft | sm | 提交卡 | 申请重做 | 审批表单 | chip | 审批流 |
| `submit.peerEval` | 小组未揭晓 | button | soft | sm | 提交卡 | 完成互评 | 无 | `lq-modal` | — |
| `upload.pick/folder/paste` | accepting | `lq-btn-group` | soft/soft/ghost | sm | 附件块头 | 选择文件/选择文件夹/粘贴 | 无 | `lq-upload` | draft-files |
| `upload.remove` | 每项 | button --icon | ghost | sm | chip 尾 | 移除{文件名} | 无 | 撤销 toast | `DELETE /draft-files/{id}` |
| `result.wrongBook` / `result.exportReview` | graded | link / button | link / soft | sm | 提交卡底 | 错题本复盘 / 导出复习 Word | 无 | — | — |

### 14.4 考试作答页

| actionId | 可见/前置 | 类型 | 变体 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|
| `exam.submit` | 考生；服务器时间未过截止；无 409 | button | prominent（唯一） | 顶栏右 / Dock 右（隐藏时表单尾等价） | 交卷 | 未答 `LQ.choose` → confirm | 锁定；拦截常显 | `POST /submit` |
| `exam.prev` / `exam.next` | 有前/后页 | `lq-btn-group` | glass | 顶栏中 + 主区底 | 上一页/下一页 | 无 | nav-grid current | 本地 |
| `exam.card` | 任何 | button --icon | glass | 顶栏 | 打开答题卡 | 无 | rail / sheet | 本地 |
| `exam.clearPage` / `exam.clearAll` | 任何 | menu 风险组 | destructive-soft | 整理答卷 | 清空当前页/整张试卷 | confirm destructive | status dirty | draft |
| `exam.draw` | 允许作图 | button | soft | 题卡 | 手写作答 | 无 | 白板挂载 | — |
| `exam.withdraw` | 已交且允许 | button | destructive-soft | 结果区 | 撤回 | confirm | — | `DELETE /withdraw` |

### 14.5 批改页

| actionId | 可见/前置 | 类型 | 变体 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|
| `grade.save` | 教师；非 returned；0–100 有限小数 | submit | prominent | foot 右 / 移动底栏 | 保存评分 | 无 | 携带两个 revision；409 `lq-conflict`；一次通知；`p03-submission-score-input`、`p03-submit-manual-grade` | `POST …/grade` |
| `grade.aiRegrade` | 教师；status≠grading | button | soft | foot 左 | AI 辅助批改 | confirm | `lq-job`；失败保留旧成绩；`p03-ai-regrade-detail` | `POST …/regrade` |
| `grade.template` | 教师 | button | ghost | 评语域头 | 插入逐题模板 | 无 | 光标插入 | 本地 |
| `grade.prev` / `grade.next` | 教师；列表上下文 | `lq-btn-group` | glass | 顶栏 | 上一份/下一份 | dirty confirm | 导航 | — |
| `files.manage.save` / `files.manage.saveAi` | 教师；可管理附件 | button | soft | 面板尾 | 保存附件 / 保存并提交 AI | 后者 confirm | chip | — |
| `files.delete` | 教师 | button --icon | ghost | 文件行 | 删除附件 | confirm destructive | — | — |

### 14.6 教师作业页

| actionId | 可见/前置 | 类型 | 变体 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|
| `tasks.status` | 教师 | `lq-segment` | — | 页头右 | 草稿/进行中/已截止 | confirm（含邮件通知） | chip | `PATCH /api/assignments/{id}` |
| `tasks.close` | published | menu 项 | destructive-soft | 更多 | 截止作业 | `lq-modal` + `LQ.choose` 默认分 | — | `/close` |
| `tasks.aiGradeAll` / `tasks.zeroUnsubmitted` / `tasks.offline` | 条件各异 | menu 项 / button | soft / destructive-soft / soft | 批改处理 / 风险组 / 未提交行 | AI 批量批改 / 未提交记 0 / 线下代交 | confirm / confirm destructive(写人数) / 无 | 行 `lq-job` / chip absence-zero / `lq-modal--lg` | 各端点 |
| `tasks.withdrawSelected` / `tasks.withdrawAll` | 有选中 / 有已提交 | button | destructive-soft | 批量条 | 撤回选中 (N) / 全部撤回 | `lq-modal`（新截止） | chip returned | `/submissions/withdraw` |
| `tasks.filter` | 任何 | `lq-chip-row --filter` | — | 列表头 | 全部/已提交/已批改/待重交/未提交(计数) | 无 | 空结果保留清除 | 本地 |
| `tasks.kind` / `tasks.edit` / `tasks.exportGrades` / `tasks.exportFiles` / `tasks.delete` | 教师 | menu 项 | delete 风险组 | 管理作业 | 作业分类 / 编辑作业 / 导出成绩 / 导出附件 / 删除作业 | 原生 dialog 保留 / — / — / — / confirm destructive | export `lq-job` | 各端点 |
| `tasks.wrongSummary` | 有批改 | link | soft | 页头 | 错题归集 | 无 | 跳转 | — |

### 14.7 后续批次范围

课堂主页、3D 课表宿主、最终材料（三签名点、导出）、消息中心、白板工具条、AI composer、简历、管理列表通用（新建/筛选/批量/导出/删除）、系统管理。每批进 `lq-action-registry.md` 并补 `测试编号`。

## 15. 工程施工

### 15.1 目录

```
static/css/ui-system.src.css        # @import "./lq/index.css"; 然后 @tailwind base/components/utilities; 然后尚未迁移的旧段（逐段删）
static/css/lq/{index,tokens,base,materials}.css
static/css/lq/components/*.css      # button chip segment tabs topbar sidebar dock fab crumbs card list table form layer toast badge empty page-head insight lightbox prose bubble status clock job nav-grid editor upload split alert ai-chat wb-toolbar
static/css/lq/shell-{sidebar,topbar,immersive,centered,editor}.css
static/css/lq/pages/*.css
static/js/lq/{index,core,layer,toast,confirm,choose,tabs,segment,collapsible,tone,html,icon,mq,status,job,upload}.js   # ESM，index 挂 window.LQ 并派发 lq:ready
templates/partials/lq_{head_assets,topbar,sidebar,dock,editor_head}.html
templates/macros/lq/*.html          # 新组件；manage_page.html 三宏原名升级
frontend/src/components/lq/*.tsx    # 只为有消费者的组件；frontend/src/lib/lq-layer.ts (useLqLayer)
tools/ui/{lint_lq.py,audit_glass_layers.cjs,contrast_probe.cjs,build_icon_registry.py,export_tokens.py}
docs/{lq-components.md,lq-action-registry.md,lq-migration-registry.json,lq-acceptance.md,lq-lint-exceptions.json,lq-tokens.json}
```

### 15.2 构建链（K16）

保持 `build:css` = Tailwind CLI 单入口（自带 postcss-import，`@import` 必须在 `@tailwind` 之前）；不新增 Vite CSS 入口编译同一组件；旧无层规则覆盖新规则的问题靠选择器作用域与消费者清单解决，不靠文件顺序宣称隔离。`tailwind.config.js`：`darkMode: ['selector', '[data-appearance="dark"]']`（`auto` 由属性解析后落到 dark，不用 `dark:` 工具类）；`theme.extend` 映射圆角/模糊/阴影/字体/字号/间距/z/断点；动态变体用完整静态映射，不 `"lq-" + variant` 拼接。构建验收：无未展开 `@import`；探针组件出现在产物；computed style 正确；未迁页样式不受影响。

### 15.3 静态交付（K1）

1. `build:css` 产物 `tailwind-app.<contenthash>.css`（或 `asset_url` 对 `static/css`、`static/js` 计算内容哈希写入 `static/vendor/manifest.json`）；`static_asset_cache_control()` 对哈希 URL 返回 `public, max-age=31536000, immutable`。
2. nginx：`location /static/ { root …; gzip_static on; expires 1y; }` 仅对哈希文件；非哈希路径继续走 FastAPI 的 `no-cache` 规则。
3. `asset_url` 的 `@lru_cache` 以 `X-LanShare-Release` 为 key；`Clear-Site-Data` 首屏逻辑不变。
4. 资源图：模板、CSS、原生 ESM 子依赖、Vite manifest 同一发布一致；`classroom-page.tsx` `LEGACY_MODULES` 改读 `window.__LS_ASSET_REV`；`signature_multi_select.js` 两处不同 `?v=` 统一；岛屿 `?v=` 契约测试同步。
5. 长开的考试/编辑页不在后台热切主题系统或强制刷新。

### 15.4 CSS 拆分与体积

按壳加载，不按页加载：`lq/index.css`（≤120KB min）+ 对应 `shell-*.css` + 该壳的 `pages/*`。过渡期两份并存；目标"每壳首屏 CSS ≤400KB min"，每阶段体积不增（经批准的有期限增量除外）。

### 15.5 守卫（从零建）

`tools/ui/lint_lq.py`（CI + `npm run lint:lq`，可选 pre-commit）对 `lq-migration-registry.json` 中状态 ≥"迁移中"的文件分三级：
- **阻断**：hex/rgb/hsl 字面量（豁免见 §2.1）；`style="`（除 `--var`）；`backdrop-filter` 在 `lq/components|materials|shell` 之外或 blur 不在四档；`.lq-glass` 后代含 `.lq-glass`；`window.confirm|alert(`；`innerHTML` 含 `class="btn `；非 `lq-spinner` 的 spinner；非 `LQ.toast` 的 `showToast(`；旧类名残留（`btn btn-|modal-backdrop|app-topbar-action|ls-button|filter-chip|toast-container|badge-(primary|warning|success|danger|secondary)`）；`z-index:` 字面量；手写 `?v=`；玻璃层内动画 `backdrop-filter|box-shadow|filter|width|height`；未登记的新 CSS/JS 特性；无可访问名称的图标按钮。
- **提示**：动态模板、共享钩子、`stopPropagation`、嵌套交互元素。
- **运行时**（`audit_glass_layers.cjs`、`contrast_probe.cjs`、Playwright）：实际层数（含伪元素/`::backdrop`/被遮挡）、焦点链、权限、溢出。
未迁页只警告。例外登记 `docs/lq-lint-exceptions.json`（`path/reason/owner/scope/reviewAt`）：白板笔迹色、课表 `DECK_CSS`、`--teacher-whiteboard-*`、echarts vendor、LessonDoc `2.0/`、图表坐标、必要尺寸测量、第三方内部样式。禁止为过 lint 破坏功能。

### 15.6 迁移台账 `lq-migration-registry.json`

扩展 `tools/frontend_migration_inventory.py` 生成路由级条目：`routePattern / template或fragment / roles与资源scope / layout / assets与islands / controller与DOM钩子 / APIs与版本字段 / 关键状态 / 组件依赖 / before基线 / tests与配置 / migrationFlag / sharedCssConsumers / exceptions / rollbackUnit / status`。状态：未盘点 → 已盘点 → 组件就绪 → 迁移中 → 业务验收通过 → 视觉验收通过 → 待发布 → 已发布 → 旧代码可删除。135 个模板逐一归属到路由/宏/片段/独立文档。

### 15.7 迁移一页的 SOP

1. 台账登记；用 P03 harness 截图 before（1440×900 + 390×844，双角色）存 `.codex-temp/lq-audit/<page>/before/`。
2. 选骨架（§11.2），画四层图（§9.2），列出所有交互元素 → 组件映射（附录 A）与按钮登记（§14）；列出 DOM 钩子/事件/testid/岛屿依赖（先加稳定 `data-*` 再迁样式类）。
3. 模板：换基类/partial；宏替换标记；删内联 `<style>`；内联 `<script>` 抽到 `static/js/<page>.js`（逐行搬，不改行为）。
4. JS：`btn/chip/spinner/toast/confirm/modal` 改 `LQ.*`；内联 style 改类或 `--var`；状态色改 `LQ.tone`。
5. CSS：`lq/pages/<page>.css` 只放该页独有布局；按消费者清单从 `ui-system.src.css` 删该页旧段；`npm run build`。
6. 跑 `lint_lq.py --page`、`audit_glass_layers.cjs`、相关 Python/vitest/e2e；截图 after；写 3 行结论（信息无丢失、功能无丢失、层数达标）。
7. 台账状态推进；PR 附 §21 清单；提交 `feat(lq): migrate <page>`。
8. 组件缺失 → 先做组件（含等价测试与 `/dev/lq` 状态）再迁页。

### 15.8 回退单位与发布

回退单位 = 页面族的 HTML + CSS + JS + 向后兼容 schema，可按页面/领域开关（`migrationFlag`）启用；不能只保留旧模板而删其 CSS/脚本。偏好两列增量迁移可空有默认值，旧服务可忽略。每个实现 PR 完成本地构建与相应回归；**部署按项目现有发布门禁与用户授权**（`deploy-workflow` 记忆），不自动。

### 15.9 通用收尾细则（每个页面/组件/阶段完成时逐条执行）

"完成"不是"新样式已显示"，而是下列清单全部勾完。各施工单的"收尾"只列该页专属项，通用项以本节为准。分四层：

**A. 代码收尾（同一 PR 内）**
1. 旧标记：该页模板内被替换的旧类名、内联 `<style>`、内联 `<script>`、手写 `?v=` 归零（`lint_lq.py --page` 零阻断）。
2. 旧样式：从 `ui-system.src.css` 删除该页专属段（按 `Source:` 注释定位，**以消费者清单为删除依据**：`rg` 该段每个选择器在 `templates/`、`static/js/`、`frontend/src/`、iframe 页、导出/打印路径中的引用为 0 才删）；共享段保留并在台账 `sharedCssConsumers` 登记。
3. 旧脚本：被 `LQ.*` 替换的局部函数（toast/confirm/modal/spinner/tabs）删除；仍被其他页引用的保留并登记。
4. 别名：只在 S7 删除的别名（`--ux-motion-*`、`--radius-*`、`--text-*`、`.ls-glass`、`.ls-anim-*`、`--blog-*`、`--mb-*`）本阶段**不删**。
5. 缓存联动：本页涉及的 JS 入口与其 ESM 子依赖 `?v=` 一并 bump（或已走 `asset_url`/`__LS_ASSET_REV`）；§8.5 清单逐项核对。
6. `npm run build`（CSS + Vite）产物提交；产物体积记录到 `lq-acceptance.md`（每阶段不增）。

**B. 测试与文档收尾（同一 PR 内）**
7. 相关 e2e/vitest/unittest 全绿，命令与 commit 写入 `lq-acceptance.md`；结构性断言（`[data-page-head]`、`teacher-app-shell`、nav service、`p03-*` testid）改动与页面改动在同一提交。
8. 新增断言：本施工单"交互"项中至少每个状态转换一条断言（新 spec 或既有 spec 追加）。
9. 截图：before/after 双视口双角色（+ 亮/暗/off，壳或组件改动时六套）存 `.codex-temp/lq-audit/<page>/`；三行结论（信息无丢失 / 功能无丢失 / 层数达标）写入 PR 描述与 `lq-acceptance.md`。
10. `docs/lq-components.md`：本页新增/修改的组件变体登记（含状态表与三入口示例）。
11. `docs/lq-action-registry.md`：本页按钮补齐"测试编号"列。
12. `docs/lq-migration-registry.json`：状态推进到"业务验收通过"→"视觉验收通过"；`exceptions` 登记本页例外（引用 `lq-lint-exceptions.json` 条目）。
13. 领域设计文档：若该页有自己的设计记录（如 `dashboard-schedule-refinement-*.md`、`whiteboard-upgrade-*.md`、`lessondoc-editor-*.md`、`manage-center-improvement-plan-*.md`），追加"lq 迁移（日期）"小节，写明改了什么、保留了什么、删了什么。
14. 项目记忆：对应 memory 文件更新类名/路径/坑（列在各施工单"收尾"）；不写进 MEMORY.md 正文。

**C. 阶段收尾（阶段最后一个 PR 合并后）**
15. 本文 §22 进度勾选；§16 该阶段"出口条件"逐条打勾并附证据链接。
16. `lq-acceptance.md` 阶段小结：通过项、未覆盖项（明确写"未测"）、实机记录（§20.4）。
17. 兼容层清单复核：`openModal` 桥、别名、`embedded_mode`、旧 partial（`app_bottomnav.html` 等）——本阶段是否已无消费者；有则登记 S7 删除。
18. 回退演练：按 `migrationFlag` 关闭本阶段任一页面族，确认旧壳 + 旧 CSS + 旧 JS 仍完整可用（不能只剩模板）。
19. 发布条件声明：写明"具备独立发布条件"及依赖的后端票/迁移；**是否部署由负责人决定**（`deploy-workflow`）。

**D. 项目收尾（S7）**
20. 删除所有兼容层与别名；`ui-system.src.css` 只剩 `@import` + `@tailwind`；`grep` 旧类名（附录 A 左列）为 0。
21. 四份实施资料定稿；`lq-tokens.json` 交付小程序批次；本文标记完成并把"现状基线"改为"改造后基线"。
22. 记忆索引：`liquid-glass-design-system` 记忆改为"已完成"并列出长期约定（令牌真源、lint、台账维护规则）；`design-system-shadcn`、`ux-overhaul-2026-08` 记忆标注被取代/已并入。
23. `.codex-temp/lq-audit/` 截图归档到 `artifacts/lq-release-<date>/`，工作目录清理。

## 16. 阶段施工单 S0–S8

| 阶段 | 范围与任务 | 产物 | 出口条件 | 回退 |
|---|---|---|---|---|
| **S0 基线·决策·首胜** | 采纳 K1–K17 与附录 B（五项决策已记录于 §22）；静态交付哈希+immutable+nginx；**开课向导退役**（§13.13）；删 legacy blog.css 段、13 个 shadcn 文件、`data-mobile-collapse`；评分 `parseInt` 修复 + `expected_review_revision`；`LEGACY_MODULES` 版本注入；`base_centered` 内联抽出；台账初稿（135 模板归属）；全站 before 截图（24 页 + §13 特殊页 + 5 独立编辑器）；lint/audit/probe 脚本骨架；`/dev/lq` 路由骨架 | 配置、脚本、`lq-migration-registry.json`、`lq-acceptance.md` 初稿 | 二次导航零 CSS 请求；CSS 体积下降可测量；§17 所有入口全绿并记录命令与基线；未动业务行为 | 配置回滚 |
| **S1 令牌·状态色·材质·偏好基础** | `lq/tokens.css`（亮/暗成对 + §8.13 注册表 + 别名层）；`lq/materials.css`（四材质 + 降级 + forced-colors）；`tailwind.config.js` 变更；字体栈；`.role-teacher` 改为 `teal` 配色缺省；§8.14 五等级与六配色亮暗值；三个文档根 + editor head 经 `lq_theme_attrs` 输出主题属性；首屏 tier 脚本；偏好：两列迁移 + `teal` key + 依赖改学生/教师通用 + 字段级更新 + 作用域全站 + CAS 同字段冲突不自动覆盖（§8.15）；登录背景图对比度逐张验证（K17）；`export_tokens.py` → `lq-tokens.json` | tokens/materials/base、偏好迁移与 API、`lq-tokens.json`、背景图对比报告 | `data-lq-glass=off` 时全站 computed `backdrop-filter: none`；SQLite/PG 迁移双通过；教师 PATCH 偏好 200、旧客户端只发 palette 不清空其余；旧页截图 diff 仅色值；三种偏好仿真截图；五等级 × 六配色对比度全过；lint 对 `lq/` 零报错 | 删新文件 + 迁移可空 |
| **S2 基础组件·业务组件·协调器** | §10.1–10.9 全部组件（CSS + 宏 + JS + 有消费者的 React）；`LQ.layer`（`ui_popover.js` 扩展）+ `openModal` 桥 + `useLqLayer` + 日期/灯箱注册；`LQ.toast/confirm/choose/tabs/segment/collapsible/tone/html/icon`；图标注册表生成；`/dev/lq` 全组件全状态；三入口语义等价测试 `tests/lq/*.test.mjs`；`lq-components.md` | 组件库、预览页、测试、文档 | `/dev/lq` 六套偏好截图；弹层链（modal → 日期 popover → 说明浮窗 → Esc 顺序）e2e；axe 零 serious；键盘遍历；lint 零报错 | 不影响任何页 |
| **S3 端到端试点 + 测试补齐** | 试点 A：升级 `manage_page.html` 三宏 + `manage/layout.html` 顶栏/侧栏 partial，抽查 8 个管理列表页（含编辑/删除/失败流）；试点 B：学生只读页（成长页族一页）验证 topbar 壳与主题；新增 e2e：`exam-authoring`、`assignment-student-draft`、`exam-take`、`grading-concurrency`、`wrong-summary`、`layout-stability`；`exam_draft_version.test.cjs` 改 import 装载 | 两个试点页、6+1 条 spec | 双角色/权限/表单/弹层/手机/深色/性能闭环；`teacher-app-shell`、page-head 同构、nav service 契约同步更新通过；未迁页无回归；据此估算批次 | 试点页开关 |
| **S4 壳与常规页** | `manage/layout.html` → sidebar 布局（Dock/rail/drawer）；`base_navbar.html` → topbar；`app_bottomnav` → `lq-dock`；学生首页（K14 结构项）；教师首页；日历；消息中心；个人资料（双基类拆分 + 外观分区，§13.12）；登录/状态页族（学生登录 Clear）；成长页族；管理其余页（Codex 批处理） | 页族 PR | 每批可独立回退；`dashboard-schedule` 12、`dashboard-todo-modal`、`semester-calendar-dialog`、`message-center`、`home-classroom-ui-v3`、`layout-stability` 通过；一屏 blur ≤2 | migrationFlag |
| **S5 教学核心链** | 后端票：试卷 `expected_revision`、布置返回卡数据；试卷编辑器（§13.1）→ 布置弹窗 → 作答页 → 考试页（拆模块）→ 批改页 → 教师作业页 → 错题归集 → 最终材料/签名子批次；§14 登记表补测试编号 | 六模板 + 编辑器 + 后端票 | B01–B07、B13–B16；S3 六条 spec + 已有 4 条全绿；六模板 `<style>`=0；状态色全走注册表；`p03-*` 保留 | migrationFlag + 后端票独立回滚 |
| **S6 复杂工作台** | 3D 课表（§13.5）；课堂主页四批（§13.6）；白板（§13.8）；AI/Agent（§13.9）；LessonDoc 壳、材料阅读、HTML 壳、材料库（§13.10）；简历；投票/分组；监控/星图；教案/考核方案/评学三编辑器；系统管理 | 页族 PR | 各族专属验收；B08–B12；生命周期/坐标/版本/连接/导出不变；课表组件 spec + 工作区断言 | migrationFlag |
| **S7 全站收敛** | 按台账删兼容层（`openModal` 桥、别名 `--ux-motion-*`/`--radius-*`/`--text-*`/`.ls-glass`）、孤立 CSS、私有前缀；深色与五配色全站校准；四份实施资料定稿；记忆更新 | 清扫 PR、文档 | 零遗漏路由；例外均登记；旧代码确无消费者；每壳首屏 CSS ≤400KB min；实机记录填写 | 逐段回滚 |
| **S8 可选增强** | 折射（登录卡/Dock/白板工具条，设备门槛）；同文档 View Transitions（Dock morph、FAB→菜单）；`@starting-style` | 可关闭增强 | 实测收益且可独立关闭；不增业务依赖 | 开关 |

---

# 第三部分 验收细则

## 17. 自动化契约与命令

| 范围 | 命令 | 说明 |
|---|---|---|
| 构建 / 类型 | `npm run build`、`npm run typecheck` | 产物含探针组件；无未展开 `@import` |
| 前端单测 | `npm test`（vitest：`frontend/src/**/*.test.ts`、白板、lessondoc_editor、新增 `tests/lq/*.test.mjs`） | 三入口等价；课表 5 套（academic-schedule / change-links / change-routes / presentation / wheel） |
| lint / 审计 | `npm run lint:lq`（`tools/ui/lint_lq.py`）、`node tools/ui/audit_glass_layers.cjs`、`node tools/ui/contrast_probe.cjs` | 已迁页零阻断；层数 ≤ 预算；对比 ≥4.5:1 |
| 默认 e2e | `npx playwright test`（`tests/e2e/specs` 28 条 + 新增 7 条；Windows PowerShell webServer） | 含作业链 4 条、`teacher-app-shell`、`ui-explanation`、`whiteboard*`、`signature-points`、`system-*` |
| ui-v3 | `npx playwright test --config tests/e2e/ui-v3.playwright.config.ts`（43 场景，合成 fixture 8152） | 监听器/socket 不增、滚轮边界、reduced-motion 帧、草稿保留 |
| 组件 fixture | `npx playwright test --config tests/e2e/components/playwright.config.ts`（16 条 + 新增 `lq-layers`、`lq-shell`） | 课表 5（deck/academic-deck/lines/density/sync）、日历、签名点、agent 确认/提问、成绩公布、分类批量、博客修订、材料申请、开课计划 CAS、教学生命周期确认 |
| 成员工作区 | `tests/e2e/classroom-members.config.ts` | — |
| 考试草稿版本 | `node tests/frontend/exam_draft_version.test.cjs`（S3 后为 import 版） | 旧轮次不覆盖新答卷 |
| Python | `python tools/test_backend.py`（临时SQLite、dotenv关闭、真实PG连接拒绝） | 提交/图片/分组/错题/审批/权限/偏好；原生PG另走显式独占隔离簇入口 |
| 截图回归 | P03 harness（`ui_v3_capture.cjs`）+ `/dev/lq` 六套偏好 | 像素阈值 0.5% 仅对稳定化区域；结构性遮挡/溢出/文字缺失不放行 |

每次验收记录：命令、代码基线（commit）、结果、未覆盖项，写入 `docs/lq-acceptance.md`。测试用合成数据/隔离库，不用真实账号产生提交、评分、消息、模型任务或导出副作用。历史通过数只用于定位场景。

## 18. 业务场景门禁 B01–B16

| 编号 | 场景 | 通过标准 | 对应页/阶段 |
|---|---|---|---|
| B01 | 试卷编写保存与课堂布置 | 评分不完整可按三态明确存草稿；已有作答改题预先禁用；布置条件与范围正确；失败不丢内容；两窗口 409 | S5 编辑器/布置 |
| B02 | 双窗口作答 / 退回新轮次 | 旧窗口不覆盖新答卷；409 后停止写；答案与附件可核对 | S5 作答页 |
| B03 | 附件未完成 / 断网时提交 | 不误报成功、不提前清空；重复截图拒绝显示原因与题号；重试遵守幂等/版本 | S5 |
| B04 | 手动交卷与截止竞态 | 只产生一个正确业务结果；倒计时与动效无关；Dock 隐藏时仍有交卷入口 | S5 考试页 |
| B05 | 并发批改与 0 / 小数成绩 | 服务端版本保护生效；0 显示、88.5 不截断、空不为 0；标准改变后旧页不能覆盖；一次通知 | S5 批改页 |
| B06 | 小组未公布与学生查看 | 服务端不泄露隐藏成绩/评语（页面源码/JSON/导出）；教师动作符合真实权限 | S5 |
| B07 | 签名 / 材料编辑 / AI 重生成竞态 | selected 与 confirmed 正确；`expected_updated_at` 旧版本拒绝并保留；导出基于当前确认版本；原件缺失/溢出常显 | S5 最终材料 |
| B08 | 列表筛选无结果 / 接口失败 | 能清筛选；错误不显示为空；翻页排序批选维持业务范围 | S3/S4 管理页 |
| B09 | 工作台跨断点 / 20 次开关 | 草稿、选中项、滚动保留；不增监听、timer、observer、连接 | S6 课堂页 |
| B10 | 原生层 / Portal / 日期 / 灯箱 / 说明浮窗链 | 最上层 Esc、焦点返回、滚动锁、dirty guard 正确；`.cs-expand` Esc 顺序 | S2/S6 |
| B11 | 六配色 / 亮暗 / 透明开关 / 双角色偏好并发 | 学生与教师各自 PATCH 成功且互不可见；前景背景成对；旧客户端只发 palette 不清空 appearance/glass；身份切换不串写；设备降级不回写；配色在管理壳、编辑器、岛屿 Portal 全部生效 | S1/S4 |
| B12 | 旧页 / 新页 / 资源回退共存 | 样式、事件、缓存一致；长开考试页不被强制刷新或失去依赖 | 全程 |
| B13 | 3D 课表投影与定位 | 跨周预测定位、重叠泳道、边界滚轮、≥4px 门槛、首次 Esc、触屏、Ctrl/Cmd 打开、无动效 | S6 |
| B14 | 白板 / 考试白板 | 画笔/橡皮/撤销/缩放/触控/恢复/导出；工具条切换不截断笔迹；亮暗切换旧内容与导出一致 | S6 |
| B15 | AI / Agent 任务 | IME 刷新、流式滚动、SSE 重连、过期提问、取消、核验、附件失败、非 owner、限流 | S6 |
| B16 | 登录 / 会话过期 / 权限拒绝 | 学生登录 Clear 卡在每张背景图上正文 ≥4.5:1；背景图加载失败/超时退回厚玻璃且表单不闪动；玻璃关闭、脚本失败、软键盘、低高度屏可达；错误定位、恢复、返回来源有效 | S4 |

## 19. 视觉与无障碍矩阵

- **组件级**：`/dev/lq` 覆盖每组件全部状态 × 6 套（亮、暗、tinted、off、contrast:more、forced-colors）× 桌面/移动；axe 零 serious。
- **页面级**：视口 320、375、390、768、1024、1440 + 各断点 ±1px；200% 缩放重排；平板 `hasTouch`；WebKit 引擎一次；双角色（学生/教师，必要时非所属教师/管理员/超管）；核心作答/评分/签名链必须覆盖亮、暗、off 三套。
- **数据**：长中文、英文长串、无头像、图片失败、0/1/多项、长表格、错误提示、权限禁用、后台处理中。
- **操作**：全键盘、focus-visible 可见且不被裁切、触控、输入法、拖拽的键盘/触摸替代、reduced-motion/transparency/contrast/forced-colors；无法仿真的媒体特性记录手工验证方式。
- **读屏**：名称/角色/值；错误与帮助关联；tab 控制关系、radio 选中、listbox/menu 语义；自动刷新不反复播报整屏；计时器不每秒播报。
- **对比**：文本、选中/未选中、边界/焦点分别检查；玻璃采样先固定背景图、时间、动效与内容。
- **人工**：视觉重心、留白、对齐、层级、颜色语义、真实可读性；每视图一个 prominent；无装饰眉题；零值处理正确且例外可见；文案在长度上限内；说明进浮窗而错误常显。截图 diff 对动态区域受控掩码。

## 20. 性能与兼容

### 20.1 支持矩阵（能力分开记录，不推断"A 档必有全部特性"）

| 等级 | 浏览器 | 承诺 |
|---|---|---|
| A | Chrome/Edge ≥111、Safari ≥17、Firefox ≥128 | 玻璃、弹簧、同文档转场、原生 dialog/popover 宿主 |
| B | Chrome/Edge 96–110、Safari 15.4–16.x、Firefox 103–127、微信 XWeb/X5 近两年 | 玻璃 + 基础动效；弹簧退 ease-out；JS 自实现顶层与圈闭 |
| C | 更旧、UC/QQ/360 旧内核、Android WebView <96 | 自动 off：无 backdrop-filter，霜面；功能完整 |
| 不支持 | IE、Android 4.x | `status.html` 提示 |

基础功能（登录、导航、保存、提交、读取结果）在 A/B/C 全部可用；能力关闭仿真只验证降级分支，不代替旧内核实测。

### 20.2 特性检测与退路表（每项新特性必须登记）

| 特性 | 检测 | 退路 |
|---|---|---|
| `backdrop-filter` | `@supports` 含 `-webkit-` | `surface-1/.96` 不透明 |
| `linear()` | `@supports (animation-timing-function: linear(0,1))` | `--ls-ease-out` |
| `@starting-style` / `allow-discrete` | `@supports (transition-behavior: allow-discrete)` | rAF 两帧切类；出场等 transitionend + 超时兜底 |
| 原生 `<dialog>` / popover 属性 | `typeof HTMLDialogElement`、`'popover' in HTMLElement.prototype` | 挂 `#lq-layers`，自实现 inert |
| `inert` | `'inert' in HTMLElement.prototype` | aria-hidden + tabindex 遍历 + 指针拦截 |
| View Transitions | `document.startViewTransition` | 直接切换 / opacity |
| `scrollbar-gutter` | `CSS.supports` | `--lq-scrollbar-w` |
| `100dvh` | `@supports (height: 100dvh)` | `100vh` + `--lq-vh` |
| `mask-composite` | `@supports` | 仅 1px border |
| `prefers-reduced-transparency` | Chromium 118+/Safari 17.4+ | 站内开关 + tier C |
| head module `blocking="render"` | `document.createElement('script').blocking?.supports('render') === true` | 试点保持SSR内联导航几何，同一Shell用keepOpen继续搜索与原操作；模块失败保留原生导航，不隐藏整页等待脚本 |

### 20.3 数值化预算与测法

| 指标 | 预算 | 测法 |
|---|---|---|
| 持续模糊宿主 / 弹层后总数 | ≤2 / ≤3（真实渲染计） | `audit_glass_layers.cjs` |
| 单个 blur | ≤24px | lint |
| ambient 色团 | 桌面 tier A 且非 reduced-motion 才动；B 静止；C/coarse/saveData/deviceMemory≤4 不渲染 | `data-lq-ambient` |
| 首屏 CSS | 每壳 ≤400KB min（过渡期不增） | size-limit |
| 壳 JS | `lq/index.js` ≤18KB gzip；折射按需 | size-limit |
| 交互时延 | 固定机器/浏览器/数据/操作，Event Timing 记 p50/p95，核心路径实验室 p95 ≤200ms；真实用户 INP 另采集不混报 | Playwright + CDP |
| 长任务 | 交互期间 >50ms 任务捕获定位；玻璃改造不得新增可归因长任务；既有业务长任务列票 | CDP trace |
| CLS | 固定场景 ≤0.05；busy/字号/顶栏收缩不使关键操作漂移 | `layout-stability.spec.ts` |
| 学生首页移动高 | ≤4200px | 同上 |
| 生命周期 | 20 次开关/切页无重复请求/监听/订阅/timer/observer | ui-v3 spec |
| 服务端 | 同一操作请求数、并发保存数、轮询频率不增；偏好查询有界；React 与原生不重复拉取 | 网络记录 |
| 图片 | 首屏关键图不 lazy；卡片图 `loading=lazy decoding=async`；灯箱走 thumb | lint + 人工 |
| 动画属性 | 玻璃层内只 `opacity/transform` | lint |

### 20.4 实机记录（S1 后、S4 前必测一次；未取得设备标"未测"）

| 机型 | 浏览器 | tier | `/dev/lq` 滚动 FPS | 课堂页滚动 FPS | 弹层打开 ms | 结论 |
|---|---|---|---|---|---|---|
| 2 核 4GB Windows 10 集显笔记本 | Chrome/Edge | 未测 | 未测 | 未测 | 未测 | 未取得该实体设备 |
| 8 年前 i5 台式 | Chrome | 未测 | 未测 | 未测 | 未测 | 未取得该实体设备 |
| iPad 9 | Safari | 未测 | 未测 | 未测 | 未测 | 未取得该实体设备；当前WebKit引擎结果另记 |
| 千元安卓 + 微信内置 | XWeb/X5 | 未测 | 未测 | 未测 | 未测 | 未取得该实体设备；能力仿真不替代真机 |

任一机型 FPS <45 → 该 tier blur 下调一档或改 C。

## 21. PR 检查清单模板（复制进 PR 描述）

```
- [ ] 台账：路由 ___ 状态 ___→___；骨架 ___；四层图已画；一屏 blur 宿主 ___（≤2/≤3）
- [ ] 主操作每活动区域 1 个；状态色全部 data-tone；无装饰眉题；零值处理正确且例外可见；文案在上限内
- [ ] 无内联 <style>；无 style=（除 --var）；无 hex；无 window.confirm/alert；无旧类名残留
- [ ] 弹层全部 LQ.layer（或登记的外部层）；toast 全部 LQ.toast；spinner 统一；确认用 confirm/choose 且 Esc=取消
- [ ] DOM 钩子/事件/testid/岛屿依赖已登记并保留（列出：___）
- [ ] 旧 CSS 段已删（行号：___），消费者清单：___；lq-lint-exceptions 无新增（或列出理由）
- [ ] 截图 before/after 双视口双角色；六套偏好仿真（壳/组件改动时）
- [ ] 键盘可达；焦点环可见；axe 零 serious；对比度探针通过；触控 ≥44
- [ ] 测试：___ 全绿（命令+基线）；新增断言：___；未覆盖项：___
- [ ] 后端票依赖：___（已合并/无）
```

## 22. 进度与文档所有权

**进度**：`[ ] S0 · [ ] S1 · [ ] S2 · [ ] S3 · [ ] S4 · [ ] S5 · [ ] S6 · [ ] S7 · [ ] S8`

> **2026-09-21 交接**：Codex 施工至 S4 第一包中断，全部工作未提交；已完成/进行中/待继续的整理与负责人待决事项见 [lq-progress-2026-09-21.md](lq-progress-2026-09-21.md)。S0–S3 本地工程出口有证据但未签字；S4 共享壳性能门禁未过；本地库测试污染事故未关闭。

**施工记录（2026-09-20 22:40）**：S0、S1、S2本地工程门禁已收口。S2独立审计缺项补齐后，统一623项组件浏览器（13.9分钟）、72文件632项前端单元、112项隔离LQ后端与typecheck全部通过；七骨架14项、居中壳2项和实际宏/React消费者通过各自实页门禁。42图标正式入口测量17,309/18,432 gzip字节，最终图`f107d4d2b5f7d130fb4b452261d3c66e5ec5e92849a8c4607803a89716c58979`。S3已按[预审](lq-s3-preflight.md)开始默认关闭的八个管理列表/学生成绩页试点及考试submit模块提取；第六业务spec明确为`grading-return-resubmit.spec.ts`，补足原S3行计数缺项。实施与验证以 [lq-acceptance.md](lq-acceptance.md) 与 [lq-components.md](lq-components.md) 为准；总进度保留负责人正式验收签字语义，不用本地结果代替Docker发布、生产或真机验收。本地测试隔离事故的数据影响独立保持未关闭，详见 [事故记录](lq-test-isolation-incident-2026-09-20.md)，不以测试入口修复代表数据恢复。

**最新本地记录（2026-09-21）**：S3本地工程出口已完成，九条明确试点仍默认关闭，页面台账仍按partial范围保留“迁移中”。业务20项、独占原生PG6项、管理13项的分轮覆盖、成绩7项、关闭开关回退、能力回退7项及双引擎8项均有实际通过证据；原失败、修复与产物轮次完整保留。最终第九图 `de1603bb1755d603744b9d612d4b0808b676f53c07df6dce0f8e0d362ff4d743` 的6项性能通过，62个完整页面层数观测最高2，实验室p95为16–40ms，八段明确CDP窗口内无>50ms任务，资源/请求无增长。图像背景上的真实文本对比另以48场景528目标验证，最低6.436。详细命令、边界、图像与轨迹见验收记录；实体设备表已明确未测。S4按[分批预审](lq-s4-preflight.md)推进共享壳和可独立回退页面包，不以局部壳通过宣称整个旧正文已迁移。数据库事故、正式签字、Docker/生产发布仍分别未关闭。

S0 本地施工明细（不替代阶段出口签字）：

- [x] 原生资源完整内容哈希图、版本缓存、预压缩、模板/岛屿统一入口；浏览器二次导航零新增CSS请求及旧图懒加载验证。
- [x] nginx/共享卷/首次升级旧Vite保留的实现与离线命令门禁；真实本地nginx引擎15项通过，Docker挂载/发布验证仍未执行（见 `lq-static-nginx-validation.md`）。
- [x] 开课向导鉴权后301退役；14个无消费者组件及已证明的死CSS清理；centered样式等价抽出。
- [x] 小数/0分、双版本CAS、409草稿保护；12项组件、3条真实浏览器、19项SQLite、22项原生PG门禁通过。
- [x] 135模板台账初稿、审计/lint骨架、教师限定preview；完整构建的before/after各94视图采集。
- [x] S1色对/令牌类型/继承/偏好并发预审，约束纠偏已记录；已启动S1实现。
- [x] 实证发现的聊天Escape抢焦点已最小修复；7项组件、修为连续3次、聊天/草稿/动效5项通过。
- [x] §17各本地完整入口无未解决失败：最终Python3476项通过/206条件跳过，前端451项/类型/构建通过，默认e2e83通过/10条件跳过，UI-v3 37通过，全组件129通过。Windows创建时收容和绝对截止watchdog均含在最终完整Python入口中；历史失败日志保留，PG评分22项另在真实临时簇通过。
- [ ] S0出口正式验收；在此之前不勾选总进度S0。

**现场核对修正（优先于上文原始数量/删除假设）**：

- K2 的无消费者 shadcn 文件实际为 **14 个**，`dialog.tsx` 仍有消费者，保留。
- K13/S0 中“删 legacy blog.css 段”改为“**仅删已证明无消费者的规则**”。旧博客段并未被 `blog-paper.css` 全量覆盖；评论、筛选及反馈共用 emoji 样式仍活跃，本阶段保留其原级联。移除48个无消费者选择器；保留规则的声明、优先级、上下文与顺序均已对照验证。
- K1 的旧资源保留必须覆盖 **pre-S0 首次升级**：在替换旧应用前，将旧容器不可变 Vite 资源写入新的静态卷；导出或校验失败须停止切换。不能只测试“S0图→下一张图”。
- 全量回归发现的旧考试草稿测试依赖遗漏、空教务表导致合班整体拒绝、根目录 multipart 上传422分别做最小修复；具体数据语义和剩余平台缺口记入验收记录，不算页面迁移完成。

**S1 实施前约束修正（2026-09-20；数值和浏览器证据见 [S1预审](lq-s1-preflight.md)）**：

- §8.14 的“白字-on-base全部≥4.5”与定稿颜色相矛盾。保留状态 base/soft/fg 分工，新增明确的 `on-base` 和 `on-primary` 色对并按实际前景验收；暗模式不能把浅色 `--ls-ink` 当深色按钮前景。亮色 `ink-3` 由 L47 调至 L45，仍须对六套实际底色复核。
- 颜色令牌区分 HSL 三通道与含 alpha 通道；soft/fill/rim 的消费者只包装一次，不使用双斜线 Tailwind opacity 组合。导出 JSON 同步标明类型。旧灯箱同名 glass 令牌原为完整色值，改类型时须同批调整8处消费者，不能推迟至S7。
- 沿用现有用户配色的真实值（sky为205°等），不再以纸面“最小色相距离”宣称状态可辨。验收同时检查文字、状态点、形态和实图。重复数值以§8.14定稿表为源，纠偏后只生成一份令牌定义。
- 首屏以 `html` 为主题令牌主作用域，`body` 保留旧身份/版本兼容属性；教师首页、管理壳、域导航等局部主色覆盖在S1同步桥接，否则不能称全站配色生效。旧页面暗色与透明关闭须检验实际表面/伪元素/动态注入，不能只验证属性存在。
- 字段级PATCH继续整行版本CAS：仅更新提交字段，缺省不清空；显式null/未知字段拒绝；不同字段旧版本也409，任何自动重试不得隐式覆盖本地冲突。教师无记录首次只改外观时仍默认teal。设备降级不持久化。
- S1旧页面的字号、间距、圆角兼容别名保持原值；字体变化单列断行/尺寸验收，避免与“仅色值diff”自相矛盾。低端模糊使用已注册档位，或先明确登记受控变体；未验收全站暗色不能记为已通过。
- 实站字体修正：S1新字体令牌保留给新材质、预览和后续组件；旧页面body/原Tailwind font-sans继续S0字体栈，按页面迁移再切换。实际390宽批改482→514、评学396→400均可仅恢复旧字体而还原，不能为全局换字体接受新增溢出。
- K17材质实测修正：默认Clear白色填充加共享强高光及`.28`遮罩不能保证白字对比（初测最低1.7572）。Clear使用独立弱高光；登录卡区域采用具名增强遮罩`--ls-scrim-login`，常规/厚玻璃及整个背景图保持原语义。316图×亮暗×两尺寸材质试件1264组最低5.8289；最终产物需复核，真实登录表单及加载/降级仍属S4，不将试件结果冒充B16完成。

**决策记录**

| 日期 | 事项 | 决定 | 落点 |
|---|---|---|---|
| 2026-09-20 | 开课向导页 | 退役；新向导结合教务系统另立项目 | K4、§13.13、S0 |
| 2026-09-20 | `profile.html` 双基类 | 拆分为分区 partial + 两个薄壳模板，路由不变 | K4、§13.12、S4 |
| 2026-09-20 | 教师外观/配色偏好 | 开放；三项偏好师生相同，教师默认 `teal`；作用域扩展到全站 | K10、§8.15 |
| 2026-09-20 | 登录卡 Clear 玻璃 | 学生登录页启用（背景图逐张对比验证，失败退厚玻璃）；教师登录用厚玻璃 | K17、§13.11、B16 |
| 2026-09-20 | 3D 课表格级玻璃 | 放大层课次格/调整标签的 `backdrop-filter` 在 lq 迁移时移除（内层不透明，视觉几乎无差、逐格计算有成本），放大层卡片作为唯一模糊宿主；120ms 可逆动画与密度自适应保留 | §13.5 |
| 2026-09-20 | 五个语义等级色相 | success 152 / warning 34 / danger 4 / info 212 / neutral 220（低饱和，避开六套配色主色相带） | §8.14 |

后续新决策同法追加。

**四份实施资料**：`lq-components.md`（§10 生成，实施时维护）· `lq-action-registry.md`（§14 为首批）· `lq-migration-registry.json`（§15.6）· `lq-acceptance.md`（§17–§20 记录）。令牌真源 `static/css/lq/tokens.css`，`lq-tokens.json` 为生成物。例外只登记一次并被页面引用。

**与其他文档的关系**：本文替代原案 §0–§14 的执行语义（原案保留为视觉研究与调研来源）；v1 的 R01–R24 与 v2 的 D01–D22 已并入（附录 B）；`ux-overhaul-2026-08.md` 结构规则、`ui-copy-simplification-plan.md` 文案红线、`ui-explanation` 契约、`manage-center-improvement-plan` 已落地结果、`whiteboard-upgrade-2026-09.md`、`lessondoc-editor-2026-09.md`、`home-classroom-ui-v3` 契约继续有效；`frontend-redesign-2026-08.md` 与 `frontend-premium-design-language.md` 视觉章节为历史；`student-dashboard-improvement-goals.md` 按 K14 处置。

---

## 附录 A 旧 → 新映射

| 旧 | 新 |
|---|---|
| `.btn .btn-primary` | `lq-btn--prominent`（每活动区域 1，其余 `--soft`） |
| `.btn-outline/.btn-secondary` | `--soft`（内容区）/ `--glass`（玻璃上） |
| `.btn-ghost` / `.btn-danger*` / `.btn-sm/-lg/-icon` | `--ghost` / `--destructive` / `--sm/--lg/--icon` |
| `.btn-accent/.btn-success`（渐变） | 删除；`--prominent` 或 `--soft data-tone` |
| `app-topbar-action*`（`topbar_action` 宏） | `lq_btn(variant='glass', size='sm')`；caption 进 explain |
| `.ls-button*` | `lq-btn` |
| `.filter-chip*` | `lq-chip--filter` |
| `.badge-*` / `.lanshare-pill` | `lq-chip--status data-tone` / `lq-badge` |
| `.card/.panel/.lanshare-surface/.status-card/.academic-card/.dashboard-*-card/.insight-panel` | `lq-card` / `lq-surface` |
| `.table*` | `lq-table` |
| `.form-control/.form-select/.form-check*` | `lq-input/lq-select(原生)/lq-checkbox/lq-radio/lq-switch` |
| `.modal-backdrop > .modal-dialog` 与约 40 个 bespoke modal（`academic-*`、`um-modal-*`、`smart-classroom-modal-*`、`edu-sync-modal-*`、`gw-*`、`learning-modal-*`、`materials-*-modal`、`class-*-modal`、`course-modal-*`、`teaching-session-modal-*`、`signature-*-modal`、`life-tip-modal`、`teacher-onboarding-*`、`session-material-ai-modal`、`shared-file-modal`、`feedback-modal`、`blog-modal`、`exam-paper-preview-*`、`exam-reverse-modal`、`wrong-answer-modal`、`rubric-modal-*`、`scoring-modal-*`、`closeout-modal`、`knowledge-detail-modal`、`afm-dialog`、`att-dialog`、`export-dialog`、`classroom-group-qr-dialog`、`textbook-intro-catalog-backdrop`、`ai-workspace-modal`、`learning-certificate-backdrop`、`.modal-overlay > .modal-box`、`poll-overlay`、`collab-overlay`、`ga-modal-overlay`、`materials-editor-shell`） | `lq-modal` / `lq-sheet` / `lq-drawer` / `lq-confirm` / `LQ.choose`（经 `LQ.layer`） |
| `assignment-kind-dialog`（原生 dialog） | 保留，登记进 `LQ.layer` 栈 |
| `.class-student-drawer/.offering-hub-drawer/.ai-agent-history-drawer/.gw-reader-panel` | `lq-drawer` |
| `.ls-popover*/.course-popover*/.tag-popover*/.agenda-popover/.blog-user-popover/.chat-emoji-popover*/.dashboard-evaluation-menu__popover/.smart-attendance-detail-popover` | `lq-popover` / `lq-menu` |
| `.toast-container/.message-center-bell-toast/.cs-toast` + 9 个 toast 函数 | `LQ.toast` |
| 约 40 个 spinner/loading 类 | `lq-spinner` / `lq-skeleton` |
| `.empty-state*/.table-empty/.page-empty` | `lq-empty --inline/--card/--page data-reason` |
| `page_head/empty_state/filter_bar` 宏 | 同名升级（内部 DOM 为 lq，钩子保留） |
| `.manage-nav*` / `partials/app_bottomnav.html` | `lq-sidebar` + `lq-nav-item` / `lq-dock` |
| `.classroom-activity-tabs/.discussion-room-tabs/.message-center-tab/.blog-section-tabs/.member-tabs` | `lq-tabs` / `lq-segment` |
| `manage/workflow.html`、`manage_workflow.js`、`.workflow-*`（CSS 34851–35985） | 退役删除（§13.13），不映射 |
| `.ls-glass/.ls-glass-pill/.ls-lightbox*` | `lq-glass/lq-btn--glass/lq-lightbox*`（别名保留到 S7） |
| `.ui-explain-popover` | `lq-popover` 皮（逻辑与契约不变） |
| `.ls-anim-*`、`--ux-motion-*` | `lq-anim-rise/stagger` + `--ls-dur/ease/spring-*`（别名到 S7） |
| `--radius-*`、`--text-*`、`--shadow-*`、`--gray-*`、`--primary-color`、`--dashboard-*`、`--blog-*`、`--glass/--glass-bd`、`--mb-*`、`--att-*`、`--exam-topbar-offset` | §8 令牌（别名到 S7；`--teacher-whiteboard-*` 运行时变量不收编） |
| 分数/提交/附件/自动保存/签名/任务硬编码色 | `--tone-<family>-<state>` |

## 附录 B 决策索引（R/D → 本文）

| 来源 | 本文落点 |
|---|---|
| v1 R01 玻璃子控件不再模糊 | §2 铁律 2、§10.1 `--glass` |
| R02 真实渲染计数、scrim/toast 不模糊 | §2 铁律 3、§9.1、§10.7 |
| R03 import 前置 | §15.2 |
| R04 三入口语义等价 | §2 铁律 9 |
| R05 层协调器 | §10.6 |
| R06 "保存评分"非"发布" | §13.4、§14.5 |
| R07 向导 iframe 保留 | 被 2026-09-20 决策取代：向导页退役（§13.13） |
| R08 偏好是唯一数据模型扩展 | §8.11、K10 |
| R09 Tailwind 3.4 | K16 |
| R10 零值例外 | §2 铁律 6、§12 |
| R11 触控 44 | §2 铁律 11、§10.1 |
| R12 语义分离 | §10.3、表单 |
| R13 卡片原生主动作 | §10.5 卡片 |
| R14 表格移动端 | §10.5 表格、§11.4 |
| R15 不全局 contain/will-change | §9.1 |
| R16 主题解析优先级 | §8.11 |
| R17 课表周命令按钮 | §13.5 |
| R18 笔迹色是数据 | §13.8 |
| R19 逐族接入 | §5、§16 |
| R20 资源图 | §15.3 |
| R21 分别检测 | §8.12、§20.2 |
| R22 指标分开 | §20.3 |
| R23 lint 三级与例外 | §15.5 |
| R24 领域状态 | §13、§14 |
| v2 D01 静态交付 | K1、§15.3 |
| D02 shadcn 退役 | K2、§15.1 |
| D03 前缀别名 | K3 |
| D04 管理宏升级 | K4、§10.5 页头 |
| D05 壳演进 | K5、§11.1 |
| D06 五编辑器 | K5、§11.3、§13.1/13.3 |
| D07 状态色先行 | K6、§8.13 |
| D08 ui_popover 种子 | K7、§10.6 |
| D09 三态确认 | K8、§10.6 |
| D10 评分修复 | K9、§13.4 |
| D11 考试测试装载 | K9、§13.3 |
| D12 偏好存储 | K10 |
| D13 课表自注入 | K11、§13.5 |
| D14 课堂入口表 | §13.6 |
| D15 首胜 | K13、S0 |
| D16 学生首页文档 | K14 |
| D17 tokens.json | K15 |
| D18 不做 | §1.3 |
| D19 批改链 e2e | K12、S3 |
| D20 CLS 断言 | K12、§20.3 |
| D21 登录页族 | §13.11 |
| D22 motion 别名 | §8.8 |

## 附录 C 术语

- **玻璃宿主**：实际声明 `backdrop-filter` 的元素；子控件不是宿主。
- **活动任务区域**：页面、模态框或正在编辑的分区，"一个主操作"的计数单位。
- **tint**：主色 12–16% 透明底 + 主色字，用于选中/软操作，不用实色填充。
- **同心圆角**：内元素圆角 = 外圆角 − 内边距。
- **状态族**：一组互斥业务状态（如 `submission`），映射到五个语义等级之一。
- **tier A/B/C**：按能力检测得出的体验等级，与业务功能无关。
- **回退单位**：可整体开关的页面族 HTML + CSS + JS + 兼容 schema。
- **消费者清单**：某条 CSS/钩子/别名被谁引用的证据，删除前置。
