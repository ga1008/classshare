# LanShare 液态玻璃设计系统：第二轮评估与补充改进计划（对照代码库）

> 日期：2026-09-19。对应文档：原案 `docs/liquid-glass-design-system-2026-09.md`（视觉与组件规范）、补充稿 v1 `docs/liquid-glass-design-system-2026-09-supplement.md`（风险与实施规则）。
> 状态：**评估稿，未实施**。本文不替代前两份，而是第三层：把两份文档与当前代码库逐项对照后，给出**口径修正、结构性遗漏、最终裁定、缺失规格（状态色注册表、缺失组件、逐按钮登记表、作业编写页完整规格）、修订路线**。三份合并后才是执行版本。
> 审查基线：Git HEAD `b24e16ca` + 当前工作区（课表模块有未提交修改，已按工作区分析）。本次为只读检查：通读两份文档，六路并行核对模板/CSS/JS/岛屿/路由/服务/测试/部署配置，未运行浏览器回归、未改业务代码、未部署。所有统计排除 `.codex-temp/`（内含旧基线源码快照）与 `node_modules/`。
> 阅读顺序：先看 §0 一页结论与 §3 决策表；实施者再按 §4–§9 逐节使用。

---

## 目录

0. 一页结论
1. 口径修正：两份文档共同用错或用旧的基线数字
2. 两份文档都遗漏的结构性事实（14 项新发现）
3. 最终裁定 D01–D22（原案 + 补充稿 v1 合并后的决定）
4. 令牌层补齐：语义状态色注册表（"用色混乱"的根治）
5. 组件库补齐：原案 §6 没有、业务链必需的 11 个组件规格
6. 逐按钮登记表：教学核心链首批（可直接复制进 `lq-action-registry.md`）
7. 特殊界面补充：作业/试卷编写页完整规格、3D 课表修订、五个独立编辑器、课堂页、其余页族
8. 工程补充：静态交付、CSS 拆分、守卫、真实测试入口、缓存联动、shadcn 退役
9. 修订路线 S0–S8 细化（出口条件、首胜清单、不做项）
10. 文档合并与所有权、核验记录

---

## 0. 一页结论

**对原案的判断**：视觉语言、四层材质模型、令牌先行、"玻璃只给漂浮层"、组件库化这几个方向正确，且是本项目当前最需要的（现状是 63,880 行拼接 CSS、约 40 种 spinner、9 套 toast、≥8 套弹层、每页一套私有前缀）。问题是它写于**没有逐项核对代码**的状态：现状基线大半是估算，多处已过期；它把管理中心当作"待标准化的 43 页"，而管理中心改造在 2026-09-13 已经收口；它给 3D 课表、作业页开的方子建立在旧版本上；它对"作业编写页"只有一行；它假设 shadcn/`tw-` 是现行基础，而模板里 `tw-` 使用为零。

**对补充稿 v1 的判断**：它正确地把原案从"照着做不会错"降级为"照着守不会坏"，24 条纠偏（R01–R24）大多成立，本文不重复。但它缺三样东西：（1）**规格**——它指出"作业编写没有独立章节"，但没有补写；它要求"每个业务按钮登记"，只给了 14 行示例；（2）**口径**——它自己也沿用了一些过期数字（"11 个 toast"、"9 种 spinner"、"43 页管理中心"），且断言"`tests/e2e/specs` 目录不存在"是错的（实有 28 条 spec，其中 4 条直接覆盖作业链）；（3）**结构事实**——它没有发现五个独立文档编辑器、shadcn 零采用、静态资源 no-cache、无打印路径、状态色按页硬编码这些直接决定路线的事实。

**本文的核心主张（六句话）**：

1. **先修交付再谈玻璃**：`tailwind-app.css` 1.39MB 经 FastAPI 以 `no-cache, must-revalidate` 下发，nginx 不经手静态资源；这比"一屏 ≤3 个 blur"对课堂旧笔记本的影响大得多，列为 S0 首项。
2. **"用色混乱"的根因是状态色按页硬编码**，不是 hex 太多。作业状态、提交状态、分数档、附件类型、截止阶段、自动保存、AI 任务、Agent 任务、签名状态、保存状态共 10 个状态族在 ≥6 个文件里各写各的色值。解法是一张**语义状态色注册表**（§4），先于任何组件迁移落地。
3. **组件库要补的不是更多变体，而是业务链必需的 11 个组件**（§5）：SaveStatus、DeadlineClock、JobStatus、QuestionNavigator、EditorShell、UploadQueue、三态确认、Overlay 协调层、Split/Viewer、Lightbox 双契约、StatusChip 映射。没有它们，"作答页/批改页/编辑器"只能换皮。
4. **管理中心已标准化，直接以 `page_head/empty_state/filter_bar` 宏为升级点**，不新造 `lq-page-head` 宏名，保留 `data-page-head` 等 e2e 钩子；把原案 P5 从"43 页 SOP"改为"3 个宏 + 1 个壳 + 抽查 8 页"。
5. **五个独立文档编辑器**（试卷编辑、考试作答、教案、考核方案、教师评学）是原案未覆盖的一族，统一进 `lq-editor` 三栏骨架（§5.5、§7.1、§7.3），这才是"作业编写页"规格的落点。
6. **路线改为 S0–S8 的出口条件制**（§9），首胜项是零风险的删除与修复（legacy `blog.css` 3,130 行、13 个未用 shadcn 文件、评分 `parseInt` 截断、`data-mobile-collapse` 死钩子），不是先换壳。

---

## 1. 口径修正：两份文档共同用错或用旧的基线数字

所有数字为 2026-09-19 工作区实测；文件数不等于路由数。

| 项目 | 原案 | 补充稿 v1 | 实测 | 对方案的影响 |
|---|---|---|---|---|
| 模板 | 135 个模板 | 135（顶层 36，manage 43） | `templates/**/*.html` 135；**56 个无 `{% extends %}`**：6 宏、31 partial、3 布局根、**5 个独立完整文档**（`exam_editor` 3196 行、`exam_take` 4228、`lesson_plan_editor` 57、`assessment_plan_editor` 81、`teacher_evaluation_editor` 86）；按基类计：`manage/layout` 43(+1 条件)、`base` 11、`base_navbar` 9、`base_centered` 8、`resume/layout` 7 | 壳不是"4 个"，是 3 个文档根 + 2 个 base 派生 + 5 个独立文档；后者没有任何壳，原案 §7.1 与 P3 都没有把它们计入 |
| JS | 158 | 156 / 递归 228 | `static/js/*.js` 156，递归 228（白板 21 模块、lessondoc_editor 25 模块） | 同 v1 |
| React | 21 岛 | 18 文件 / 16 入口 | 18 `.tsx`、16 Vite 入口、模板内 19 处 `data-lanshare-island`（16 个名） | 同 v1 |
| 内联 `<style>` | 7 模板 5,486 行；P7 写 6,544 | 20 模板；7 页 6,838 | 20 模板；前 10 名：exam_take 1490、wrong_summary 1473、detail_teacher 1260、exam_editor 1048、submission_detail 795、detail_student 484、classroom_main_v4 293、**student_login_v4 200、base_centered 139、session_expired 97** | 登录与状态页族（8 页共用 `base_centered` 的 137 行内联）是原案 P8/§9.11 没算的量 |
| toast 实现 | 11 | 未核 | **9**：`ui.js:26`（真源，被 6 个壳/页 re-bind 到 `window.showToast`）、`resume_common.js:32`、`manage_course_schedule.js:45`、`message_center_bell.js:62`（标记在 4 个模板重复）、`ai_chat_component.js:19`、`ai_workspace_widget.js:54`、`dashboard_agenda_widget.js:516`、`lessondoc_wizard.js:96`、`attendance_reports.js:30`；React 只做代理，**无 sonner** | 收敛目标不变，但 `sonner` 不在依赖里，原案 §1.3/§6.11 的"React 用 sonner"要删 |
| confirm/alert | 43 + 3 | 未核 | `confirm(` **67 处 / 38 文件**（JS 40 处 `window.confirm` + 3 裸 + 模板 24）；`alert(` 3；已有两个局部确认组件 `RZ.confirmDialog`（`resume_common.js:232`）、`classroom_material_list.js:184` | 其中 `exam_editor.html:2442` 的 confirm 是**有效双分支**（确定=去补评分并放弃保存，取消=存草稿），不能机械替换为二值 `LQ.confirm`（见 §5.7） |
| spinner | 9 种 | 未核 | **约 40 个类名**（`.spinner` 54 处引用为主，其余每功能一个：`afm-/mh-/obs-/spw-/tsf-/te-/wrong-summary-/export-/prompt-pool-/semester-button-/thinking-status-spinner`、十余个 `*-loading` 块） | 收敛工作量是原案的 4 倍，但机械 |
| 弹层系统 | 5 套 + 40 bespoke | 未核 | `.modal-backdrop` 59 处/22 模板 + 5 JS；**至少 8 套独立实现**：`ui.js openModal`（无焦点圈闭/无 Esc/无 refcount 滚动锁）、`ui_popover.js PopoverManager`（有栈、圈闭、焦点回归，**仅 3 个消费者**：lessondoc_editor、白板 twb、测试）、`ui_overlay_motion.js`、`poll-overlay`、`collab-overlay`、`ga-modal-overlay`、`materials-editor-shell`、`rz-` 手工 modal、`lde-` dialog、课表 `.cs-expand`、日历 `.semester-todo-modal-card`、Radix Dialog（2 岛） | 原案要"全部走 `LQ.layer`"；v1 要"协议统一不重写 DOM"。本文裁定见 D08：**以 `ui_popover.js` 为协调层种子** |
| tabs | ≥9 套 | 未核 | 8 个 JS 实现 + 9 个模板含 `role=tab`；`components/ui/tabs.tsx` 无人引用 | 同原案 |
| drawer | 4 套 | 未核 | 4 套无共享代码（`class-student-drawer`、`offering-hub-drawer`、`#ai-agent-history-drawer`、`gw-reader-panel`） | 同原案 |
| 3D 课表 | 318 行注入 CSS，38 hex，模块 1365 行 | 引用原案 | **1399 行**；`DECK_CSS` 第 80–430 行 **351 行**；**61 hex + 43 rgba**；`.d.ts` 14 行**只覆盖纯函数**，`createScheduleDeck/courseAccentFor` 无类型；`ui-system.src.css:55818` 明确写"CSS 由模块自行注入，不进构建" | §7.2 修订 |
| shadcn / `tw-` | "shadcn 15 基元只用 dialog" | 同 | 模板 `tw-` **0 处**；`static/js` 3 处（1 文件）；`frontend/src` 89 处**全部在 `components/ui/*.tsx`**；产物里只有 172 条 `.tw-*` 工具类；只有 `dialog.tsx` 被 2 个岛 import | 方案 A 在服务端渲染层**从未落地**；见 D02 退役 |
| 深色模式 | 无 | 无 | `tailwind.config.js` **无 `darkMode` 键**；`prefers-color-scheme` 手写 CSS 仅 2 处 | 同 |
| 守卫 | 待建 | 待建 | **无 pre-commit、无 eslint/stylelint、无 git hooks**；唯一 UI 检查器是 `tools/ui/audit_ui_copy.py`（文案候选生成，非 CI） | lint 从零建；§8.3 |
| 打印 | 未提 | "打印/导出另用不透明浅底" | **全库 0 条 `@media print`、0 处 `window.print`**；所有导出走 python-docx 服务（`lesson_plan_docx_service`、`submission_export_docx_service`、`material_export_template_service`、`academic_final_material_document_service`、`resume/`） | v1 §3.1 的打印条款缩小为"网页样式不进 docx 服务"，无需打印样式表 |
| 静态交付 | "nginx 缓存头不变" | "资源图/回退" | **nginx 不代理 `/static`**（`nginx.conf` 无 location、无 gzip/brotli/expires）；FastAPI `deployment_cache_service.py:27-40`：只有 `dist/assets/<hash>` 得到 `immutable`，**其余含 1.39MB `tailwind-app.css` 全部 `public, no-cache, max-age=0, must-revalidate`**；gzip 由 `StreamingAwareGZipMiddleware` 做，无 brotli | 见 D01 |
| `?v=` 手写 | 38 | 未核 | 模板 39 + JS 25 + `classroom-page.tsx:7-19` 的 `LEGACY_MODULES` 9 处（绕过 `asset_url`）；`asset_url` 走 mtime 且 `@lru_cache(maxsize=1)` | 见 §8.5 |
| e2e | 列出多套 | "`tests/e2e/specs` 不存在" | **存在，28 条**；根配置 testIgnore 3 条交给 `ui-v3.playwright.config.ts`（6 文件 43 场景）；`tests/e2e/components` 13 条纯 fixture。作业链**已有** 4 条：`assignment-submission`（提交→教师看到行与详情，钉住 `p03-*` testid）、`teacher-review-ai`（AI 批改状态、停止在途任务、越权拒绝）、`assignment-classification-modal`（分类弹窗并发/取消/重试）、`classroom-task-card-layout`（任务卡多宽度）；组件层 `grade-publication`、`assessment-classification-batch`。**试卷编辑器、考试作答页、学生草稿/409、教师并发批改（409）、错题归集 0 条** | 见 D19 |
| CLS / 4200px 断言 | "沿用 v3 断言" | 引用 | **不存在**：全部 spec 中无 layout-shift/CLS/4200；v3 实测 CLS 0.0319 记录在文档，不在测试里；ui-v3 spec 实际断言的是监听器/socket 数不增、滚轮边界、reduced-motion 帧 | 作为门槛前必须先写出来 |

---

## 2. 两份文档都遗漏的结构性事实

### 2.1 壳的真实拓扑（决定 P3 怎么做）

`templates/dashboard_teacher.html:1` **已经 extends `manage/layout.html`**，即教师首页本身就是管理壳的一页；学生首页 `dashboard.html` 走 `base_navbar`。所以"教师/学生两个首页两个壳"是现状——原案 §7.1 让 `dashboard` 归 `topbar` 布局会把教师首页从管理壳里拽出来，与 2026-09-13 刚做完的"首页进管理壳"（manage 计划 S1）对冲。

`manage/layout.html` 与 `resume/layout.html` 是独立 `<html>` 根，**不输出 `data-ui-palette`、不加载 `user_ui_preferences.css`**；`base.html:23` 才输出。原案 §4.11 的"`<html data-appearance>` + `<body data-ui-palette>`"在三个根上要各写一次，而 v1 R16 只讲了解析优先级。

五个独立文档（§1 表）各自带 `<html>`、自己的 `#toast-container`、自己的 `window.showToast` 绑定（`exam_editor.html:1477`、`exam_take.html:1724`）。它们独立是为了不受站点导航干扰（考试锁定、编辑器全屏）。原案 §9.3 让 `exam_take` `extends "lq/app_shell.html"`，但另外三个编辑器未提；v1 §7.5 提到"分别登记"，没给骨架。本文把五者归为 `lq-editor` 族（§5.5）。

### 2.2 方案 A 的 shadcn 迁移在服务端层是零

`tw-` 在 `templates/` 出现 0 次。`frontend-redesign-2026-08.md` 的 P2/P3/P4 仍标 🔄，工作区只加了 3 行"已被取代"横幅。原案 §1.3 说"先补齐再定变体"，v1 R04 说"Radix 合法包装"。两者都默认 shadcn 是基础设施；实际是 14 个死文件 + 1 个被 2 个岛使用的 dialog。裁定见 D02。

### 2.3 管理中心已经标准化，且被结构性 e2e 守着

`docs/manage-center-improvement-plan-2026-09-11.md` §12 记录 S1–P5 全部于 2026-09-13 收口，代码证实：`manage_nav_service.py:10` 六域、`work_inbox_service.py` + `/api/work-inbox`（小程序也消费）、`macros/manage_page.html` 的 `page_head/empty_state/filter_bar`、40 个模板内联 `<style>` 归零、864 条声明令牌化到 `hsl(var(--ls-c-*)/α)`、`dw-*`→`ls-*` 872 处。`teacher-app-shell.spec.ts` 断言顶栏同构，Playwright 断言 `[data-page-head]` 子结构跨页相等，`test_manage_nav_service.py` 断言六域/九步。

含义：原案 P5"43 页 SOP、删 5k 行 CSS"是对已完成工作的重复计划。正确做法是**升级 3 个宏的内部 DOM 与 CSS**（一次改动覆盖 41 个调用点），保留 `data-page-head/data-page-empty/data-filter-bar` 钩子，并在同一提交里更新断言。两处遗留要先决定：开课向导页被保留（manage 计划偏差 `:522`），重做它是浪费；`profile.html` 条件双基类使教师"我的"与学生 `/profile` 共用一个模板，改它必影响学生。

### 2.4 静态资源交付是首要性能问题

每次导航都要对 1.39MB 的 CSS 做条件请求（ETag 304），且只有 gzip；nginx 完全不参与。教室旧笔记本 + 校园网下，这个 RTT 与解析成本远大于 3 层 backdrop-filter。原案 §12.3 写"nginx 缓存头不变、服务器侧无变化"，v1 §8.5 讲了资源图但没有指出当前策略本身有问题。见 D01。

### 2.5 没有打印路径

所有正式产物由 python-docx 生成，浏览器不打印。v1 §3.1/§6.5 的"打印/导出样式"条款自动成立，无需实现打印样式；唯一要守的是导出服务**不读取任何前端 CSS**（现状如此）。

### 2.6 状态色是按页硬编码的（"用色混乱"的根因）

| 状态族 | 位置 | 现状 |
|---|---|---|
| 提交状态 ×6 + 待重交 | `assignment_detail_teacher.html:2779-2787` | `badge-primary/warning/success/secondary` 映射，页内私定 |
| 分数档 ×6 | `assignment_detail_teacher.html:2393-2398`（ECharts）与 `:2878-2880`；`submission_detail.html:921-925`（Jinja 内联） | `#94a3b8 #ef4444 #f59e0b #06b6d4 #3b82f6 #10b981` 与 `#d1fae5/#059669 …` 两套写法 |
| 附件类型 ×7 | `assignment_detail_teacher.html:737-743` | 7 对硬编码 |
| 作业状态 ×3 | `assignment_detail_teacher.html:1471`；`assignment_detail_student.html:515-521` | 两页各写 |
| 自动保存 ×5 | `assignment_detail_student.html:377-379` | `#047857 #1d4ed8 #b45309` |
| 截止阶段 ×4 | `assignment_time.js:106-108` 类名 `is-urgent/is-late/is-expired` | 颜色在各页 CSS 里各定 |
| AI 任务 ×5 | `ai_jobs.status`；教师页轮询无独立组件 | 无统一表达 |
| Agent 任务 ×7 | `ai_workspace_widget.js:867` 标签表 + FAB 红黄绿 `:684-688` | 私有 |
| 签名 ×6 + 区域 4 态 | `signature_point_workflow.js:4-11,186-194`；`signature_point_workflow.css` 77 行 152 hex | 每行 2 个 hex |
| LessonDoc 保存态 ×6 | `lessondoc_editor/index.js:33` | `lde-` 私有 |
| 课程色 ×10 | `course_schedule_deck.js:34-37` `COURSE_PALETTE`；`manage_course_schedule.js` 复用 `courseAccentFor` | 两页必须同源 |
| 监控 ×7 | `manage_system_monitor.js:11-26` | 私有深色 |
| 议程 5 色 | dashboard/日历 | 已令牌化（`semester_calendar.js` 0 hex） |

原案 §8 只列了 5 个 agenda 色与 `--tone-course-1…10`；v1 §3.1 讲了色对，没有列族。§4 给出完整注册表。

### 2.7 作答/考试/批改链的浏览器回归缺口

已有 4 条 spec（§1 表）覆盖"提交→教师看到"、AI 批改状态、分类弹窗、任务卡；它们钉住的 `data-testid`（`p03-assignment-answer-area`、`p03-submit-assignment`、`p03-submission-status`、`p03-submission-score-input`、`p03-submit-manual-grade`、`p03-ai-regrade-detail`）是改版最便宜的回归网，**必须保留**。缺口：试卷编辑器保存/409、学生草稿 409/双窗口、考试作答页、教师并发批改 409、错题归集。`exam_take` 的关键保护（固定 `SUBMISSION_VERSION`、409 后停止写入）靠 `tests/frontend/exam_draft_version.test.cjs` **用正则从模板里抽三个 `async function`** 跑在 VM 里；一旦把内联 JS 拆成模块（原案 P7 核心动作）这个测试立刻失效，而它是唯一守住"旧轮次不覆盖新答卷"的自动化。

### 2.8 3D 课表工作区现状与原案 §9.1 的差异

工作区 diff（+44 行）：`data-csd-change` 标签变为 toggle 并由 `setChangeDetail()` 单点管理 `hidden/aria-expanded`（`:947`）；对照说明随预览生命周期关闭；**悬停预览须 ≥4px 真实位移才触发**（`:917-923`、`:1249`）；spec 新增 Escape 先关说明、对话框保留。原案 §9.1 补充段已描述这些，但仍要求"CSS 抽到 `schedule-deck.css`"。这与 `ui-system.src.css:55818` 明文的"可移植模块自注入"契约冲突，且三个宿主（`dashboard.js:701`、`manage_course_schedule.js:37`、`student_dashboard_schedule.js:63`）各自传不同 options（`showTermSelect/compactSummary/onWeekChange/emptyHtml`），CSS 外置后每个宿主页都要保证加载顺序。裁定见 D13。

### 2.9 灯箱有两种接入契约

声明式 `data-ls-lightbox[-src|-group|-title|-scope]`（学习文档、博客、批改页附件），以及**程序式**：`chat_image_preview.js` 的 `ChatImagePreviewController` 委托给 `ls_image_lightbox.js` 的 `ensure/isOpen/open/close` API 并传入同消息的兄弟附件（提交 `4e292110`）。`chat.js/classroom_private_messages.js/ai_chat_component.js/blog.js` 中 `data-ls-lightbox` 为 0。原案 §6.16 只提"保留 data 契约"；改类名时 API 契约同样要保。

### 2.10 偏好系统的真实范围

`user_ui_preferences` 路由 `Depends(get_current_student)`（教师 403）；`resolve_user_ui_preferences` 只在 `^/(dashboard|classroom/<id>)/?$` 两条路由启用；SSR 只在 `base.html:23`；JS 无 localStorage（服务器唯一存储）；表无版本化迁移（`CREATE TABLE IF NOT EXISTS`）。原案 P9 的"扩展两列 + 教师可用 + 未登录 localStorage"是三项独立决定；本文补充：**未登录 localStorage 与"服务器唯一存储"的既有设计原则相反**，需明确取舍（D12）。

### 2.11 页面私有令牌命名空间清单（原案附录 B 未列）

`--blog-*`（249 处，`blog-paper.css:14-24`）、`--glass/--glass-bd`（仅 `career_path.css:12`）、`--mb-*`（监控 `ui-system.src.css:61038-61046`）、`--att-*`（`attendance_reports.css:1`）、`--teacher-whiteboard-bg-alpha/ink-alpha/pan-x/pan-y/grid-size`（JS 驱动，`:37065-37069`，**不能收编**，是运行时状态）、`--exam-topbar-offset`（`exam_take.html:10`）、`--section-accent`（博客按栏目注入）、`--ux-motion-*`（`:52251`，LessonDoc 编辑器与白板依赖）、`--ls-c-<family>-<step>` 86 个（864 条声明依赖，**只能加别名不能改名**）、计划中的 `--dash-*`（学生首页目标文档 C4，未动工）。

### 2.12 legacy `blog.css` 仍在产物里

`ui-system.src.css:28625–31754`（约 3,130 行、22 hex）定义了被 `blog-paper.css` 全量覆盖的 `.blog-shell` 等规则。删除它是零风险、可测量的首胜，原案 §9.12 提到但排在 P8。

### 2.13 学生首页目标文档零进度且与全局令牌冲突

`docs/student-dashboard-improvement-goals.md` 全部 `[ ]`；其 C4 定义 `body.dashboard-page` 作用域的 `--dash-gap/radius/shadow-*`，C1 定义 8 色课程 tone hash，C2 定义 count-up。原案与 v1 均未提。裁定 D16。

### 2.14 小程序无共享令牌

`miniapp/` 14 页全部单文件内联样式；`IMPROVEMENT-PLAN-2026-09-17.md` 批次 G 计划本地 `tokens.scss` 与深色模式。Web 侧现在定深色令牌值，命名若不对齐将出现两份色板。裁定 D17（只约定命名与导出，不扩大范围）。

---

## 3. 最终裁定 D01–D22

"替代"表示覆盖原案/v1 对应条款。

| 编号 | 决定 | 替代/补充 | 理由与证据 |
|---|---|---|---|
| D01 | **静态交付进 S0**：`tailwind-app.css` 与 `static/js` 入口改为内容哈希（或 `asset_url` 输出哈希写入 `static/vendor/manifest.json`，`_resolve_asset_revision` 已优先读 manifest）并归入 `immutable`；nginx 增加 `/static` 直出 + `gzip_static`；`asset_url` 的 `@lru_cache(maxsize=1)` 改为按发布版本失效 | 替代原案 §12.3、§10.6 | `deployment_cache_service.py:27-40`；1.39MB 每导航 must-revalidate |
| D02 | **shadcn 在服务端层退役**：删除 `frontend/src/components/ui/` 中 13 个未引用文件（保留 `dialog.tsx` 与 `utils.ts`），`LQ.layer` React 适配器就绪后再删 dialog；不再 `npx shadcn add`；`tw-` 只做岛屿布局工具类 | 替代原案 §1.3、§6.1 "cva 替换 button.tsx" | 模板 `tw-` 0 处；`sonner/framer-motion/lucide-static` 不在依赖 |
| D03 | **`lq-` 只用于新共享组件**；`--ls-*` 不变；`--ls-c-*`、`--ux-motion-*`、`.ls-anim-*` **只加别名不改名**；页面私有前缀在该页迁移时退役 | 补充原案 §4 | 864 + LessonDoc/白板依赖 |
| D04 | **管理中心不按页 SOP**：升级 `macros/manage_page.html` 三个宏与 `manage/layout.html`；保留 `data-page-head/data-page-empty/data-filter-bar`、`.page-head__copy/__desc/__aside/__actions`；同一提交更新 `teacher-app-shell.spec.ts` 与 page-head 断言；开课向导页与 `profile.html` 双基类先做产品决定 | 替代原案 P5 | §2.3 |
| D05 | **教师首页留在管理壳**；`sidebar` 布局以 `manage/layout.html` 为原型演进，`topbar` 以 `base_navbar.html` 为原型；共享 partial（顶栏工具区已共享 `app_topbar_utility_actions.html`），**不建第三个模板当"唯一壳"** | 替代原案 §7.1 | `dashboard_teacher.html:1` |
| D06 | **五个独立文档编辑器归 `lq-editor` 族**（§5.5）：保持独立 `<html>`，共用 `partials/lq_editor_head.html` 与三栏骨架 CSS | 补充原案 §7.2 | §1 表 |
| D07 | **状态色注册表先于组件**（§4）：S1 落地，组件与页面只引用族名 | 补充原案 §8、v1 §3.1 | §2.6 |
| D08 | **弹层协调层以 `ui_popover.js` 为种子**扩展为 `LQ.layer`；`ui.js openModal/closeModal` 改为兼容前端；8 套私有 overlay 按 §5.6 逐个接入 | 替代原案 §6.10；具体化 v1 R05/§4.4 | `ui_popover.js:18-258` |
| D09 | **确认二值与三态并存**：`LQ.confirm` 二值；`LQ.choose` 三态，用于 `exam_editor.html:2442` 等有效多分支 | 补充原案 §6.10、v1 §6.1 | §1 表 |
| D10 | **评分输入修复独立票并作为批改页迁移门禁**：`submission_detail.html:1785` `parseInt` → 保留后端允许的有限小数（`submission_grade_guard_service.py:30`）；Web 携带 `expected_review_revision`；空分数不能变 0；保留 `p03-*` testid | 采纳 v1 §6.4，升为门禁 | — |
| D11 | **考试草稿版本测试先改装载方式再拆模块**：`exam_draft_version.test.cjs` 改为 import 拆出的模块；拆模块 PR 必须同时提交这个测试改动 | 补充原案 P7、v1 §6.3 | §2.7 |
| D12 | **偏好：服务器唯一存储原则不变**。未登录只跟随系统深浅（媒体查询），不写 localStorage；教师可用性单列产品票；`appearance/glass` 两列可空+默认值增量迁移，SQLite/PG 双路径 | 替代原案 §4.11；具体化 v1 §8.3 | §2.10 |
| D13 | **3D 课表 CSS 保留自注入机制**，内容令牌化：`DECK_CSS` 内 61 hex/43 rgba 改引用令牌；`COURSE_PALETTE` 从 `--tone-course-N` 读取一次并缓存，`courseAccentFor` 签名不变；`.d.ts` 补全；三宿主 `?v=` 同步 | 替代原案 §9.1 "CSS 抽出" | §2.8 |
| D14 | **课堂主页迁移前先做入口映射表**并补 e2e（活动 dock 单实例跨断点、草稿保留、监听器不增、材料批选、结课入口） | 采纳 v1 §7.2 | §7.4 |
| D15 | **首胜清单进 S0**：删 legacy `blog.css` 段；删 13 个 shadcn 文件；删 `data-mobile-collapse` 死钩子；D10 评分修复；`LEGACY_MODULES` 版本注入；`base_centered` 137 行内联抽出 | 新增 | §2.12、§1 表 |
| D16 | **学生首页目标文档书面处置**：C4 `--dash-*` 取消；C1 课程 tone 并入 `--tone-course-*`；C2 count-up 保留为 `lq-card--stat` 可选行为（HTML 初值为真）；A1/A2/C3 结构项纳入 S4 | 新增 | §2.13 |
| D17 | **深色令牌与小程序命名对齐**：`tokens.css` 同时生成 `docs/lq-tokens.json`；小程序批次 G 从它派生；本项目不改小程序 | 新增 | §2.14 |
| D18 | **不做**：Tailwind 4；向导去 iframe；HTML 包壳加 `sandbox`（`material_render_shell.html:62` 同源依赖）；折射库；跨文档 View Transitions；每文件行数硬指标；"每阶段必部署" | 采纳 v1 §9 + sandbox | — |
| D19 | **批改链 e2e 补齐先于 P7**：S3 交付 5 条 spec（试卷编辑保存/409、学生草稿 409/双窗口、考试计时/交卷/旧轮次、教师并发批改 409/小数分、错题归集）后才允许动这 6 个模板；已有 4 条与 `p03-*` testid 必须保留 | 补充原案 P7 验收 | §2.7 |
| D20 | **CLS/高度断言先写后用**：S3 写 `layout-stability.spec.ts`（首页/课堂页/作答页 CLS ≤0.05，学生首页移动高 ≤4200px）后才能作为门槛 | 修正原案 §12.3、§9.6 | §1 表末行 |
| D21 | **登录/状态页族纳入 S4**：`base_centered.html` 137 行内联 + 4 页各自内联，8 页共用 `.login-card/.status-card`；人生一言性能层（`:52146` 背景静止）原样保留 | 补充原案 §9.11 | §1 表 |
| D22 | **`--ux-motion-*` 与 `--ls-dur-*` 并存到 S7**：LessonDoc 编辑器（`lessondoc-editor-2026-09.md:391`）与白板依赖前者 | 补充原案 §4.8 | — |

另外修正 v1 的三处：`tests/e2e/specs` 存在（28 条，含 4 条作业链）；toast 为 9 个；spinner 约 40。

---

## 4. 令牌层补齐：语义状态色注册表

### 4.1 规则

- 令牌形如 `--tone-<family>-<state>`，HSL 通道，由 `--ls-c-*` 或 `--ls-success/warning/destructive/info/primary` 派生；每个状态同时定义 `-fg` 与 `-soft`，深色主题成对覆盖。
- 组件只接受 `data-tone="<family>-<state>"`；**页面不得自定 chip/badge 色**。
- 同一"语义等级"跨族共用基色：`success` = 完成/已同步/已生效；`warning` = 待处理/临近/待审；`danger` = 失败/冲突/破坏/过期；`info` = 进行中/排队/中性；`neutral` = 未开始/草稿/不可用。族表只决定**状态→等级**，不新增色相。全站状态色最多 5 个色相 + 课程 10 色 + 监控 5 色 + 附件 7 色。
- 与 v1 R10 一致：颜色不是唯一表达，每个状态必须有文字标签；图标可选。

### 4.2 注册表（首批，标签为现有文案）

| 族 | 状态 | 标签 | 等级 | 现硬编码位置 |
|---|---|---|---|---|
| `assignment` | `draft` / `published` / `closed` | 草稿 / 进行中 / 已截止 | neutral / success / danger | `assignment_detail_teacher.html:1471`、`assignment_detail_student.html:515-521` |
| `submission` | `unsubmitted` / `submitted` / `grading` / `grading_review` / `grading_failed` / `graded` / `returned` | 未提交 / 已提交 / 批改中 / 等待教师复核 / AI批改失败 / 已批改 / 待重交 | neutral / info / info / warning / danger / success / warning | `assignment_detail_teacher.html:2779-2787`、`submission_detail.html` |
| `submission-flag` | `offline` / `absence-zero` / `late` / `group-pending` / `group-final` | 线下 / 缺交记0 / 补交 / 小组待揭晓 / 综合表现分 | neutral / danger / warning / info / success | `:2793-2797`；`assignment_detail_student.html:663-676` |
| `score` | `none` / `fail` / `pass` / `good` / `excellent` / `top` | 无成绩 / <60 / 及格 / 良好 / 优秀 / 极好 | neutral / danger / warning / info / primary / success | `:2393-2398`、`:2878-2880`、`submission_detail.html:921-925`（**三处合一**） |
| `deadline` | `none` / `regular` / `urgent` / `late` / `closed` | — / 进行中 / 不足1小时 / 补交期 / 已关闭 | neutral / info / warning / warning / danger | `assignment_time.js:79-115` |
| `save` | `dirty` / `local_saved` / `syncing` / `synced` / `offline` / `error` / `conflict` / `submitting` / `submitted` | 有未保存修改 / 已存本机 / 正在保存 / 已保存 / 网络中断待重试 / 保存失败 / 版本冲突 / 提交中 / 已提交 | warning / neutral / info / success / warning / danger / danger / info / success | `assignment_detail_student.html:377-379`、`exam_take.html #saveStatus`、`lessondoc_editor/index.js:33`、`resume_builder.js:516` |
| `job` | `queued` / `retry_wait` / `running` / `result_ready` / `superseded` / `failed` / `canceled` | 排队 / 等待重试 / 运行中 / 结果就绪 / 已被取代 / 失败 / 已取消 | neutral / warning / info / success / neutral / danger / neutral | `ai_jobs`、`exam_editor.html:1341-1372`、`resume_list.js:358`、导出任务 |
| `agent` | `queued` / `running` / `waiting_input` / `question_expired` / `unverified` / `partial` / `committed` / `completed` / `failed` / `canceled` | 排队 / 运行中 / 待回答 / 提问已过期 / 结果待核验 / 部分完成 / 已提交 / 已完成 / 失败 / 已取消 | neutral / info / warning / danger / warning / warning / info / success / danger / neutral | `ai_workspace_widget.js:845-867`；FAB `:684-688` |
| `signature` | `neutral` / `pending` / `partially_approved` / `approved` / `rejected` / `cancelled` / `superseded`；区域 `updating` / `dirty` / `confirmed` | 尚未选择 / 待审批 / 部分已处理 / 已批准 / 已拒绝 / 已结束 / 无需重复审批；后台更新中 / 修改待确认 / 已生效 | neutral / warning / warning / success / danger / neutral / neutral；info / warning / success | `signature_point_workflow.js:4-11,186-194`、`.css`（152 hex） |
| `attachment` | `image` / `doc` / `sheet` / `slide` / `pdf` / `archive` / `other` | 图片 / 文档 / 表格 / 演示 / PDF / 压缩包 / 其他 | 固定 7 色（识别用，非等级） | `assignment_detail_teacher.html:737-743` |
| `course` | `1…10` | 课程名 | 固定 10 色 | `course_schedule_deck.js:34-37`；学生首页 C1 并入 |
| `agenda` | `proctor` / `exam` / `homework` / `todo` / `class` | 监考 / 考试 / 作业 / 待办 / 上课 | amber / red / violet / sky / teal（已令牌化） | `semester_calendar.js` |
| `monitor` | `accent` / `good` / `warn` / `bad` / `muted` | — | 局部深色域专用 | `manage_system_monitor.js:11-26` |

### 4.3 落地步骤

1. S1：`static/css/lq/tokens.css` 增加全部令牌（亮/暗）；生成 `docs/lq-tokens.json`（D17）。
2. S2：`lq-chip--status` 与 `lq-badge` 只接受 `data-tone`；`LQ.tone(family, state)` 返回类名与标签；宏 `lq_status(family, state)`。
3. 每迁一页：替换硬编码位置，并在 `lq-migration-registry.json` 记录"状态族已接入"。
4. lint：已迁页面出现 `badge-(primary|warning|success|danger|secondary)` 或状态相关 hex → 阻断。

---

## 5. 组件库补齐：原案 §6 没有、业务链必需的 11 个组件

尺寸沿用原案 §4–§6；这里只写差异。

### 5.1 保存状态 `lq-status`（SaveStatus）

- **用途**：作答页、考试页、试卷编辑器、LessonDoc、简历、最终材料。
- **DOM**：`<span class="lq-status" data-tone="save-synced" role="status" aria-live="polite"><span class="lq-status__dot"></span><span class="lq-status__label">已保存</span><time class="lq-status__time" datetime="…">14:32</time><button class="lq-btn lq-btn--link lq-btn--sm lq-status__action">重新核对</button></span>`
- **状态**：`save` 族 9 态；`conflict/error` 必带 `__action`；`aria-live` 只在**跨等级**变化时更新文本，`syncing↔synced` 抖动不播报。
- **视觉**：高 28，胶囊，软底，13/600；`syncing` 用 `lq-spinner--sm` 替代 dot；`conflict/error` 常显不淡出。
- **行为归属**：只有 `set(state, {time, action})`；状态机与请求队列在页面 controller（v1 §5.1）。

### 5.2 截止时钟 `lq-clock`（DeadlineClock）

- **承接** `assignment_time.js` 全部 data 契约（`data-server-now/countdown-at/starts-at/late-until/deadline-phase/accepting/late-open/late-policy-label/personal-resubmission/resubmission-due-at/can-resubmit`），逻辑不动，只换渲染类。
- **DOM**：`<div class="lq-clock" data-tone="deadline-regular" data-assignment-clock …><span class="lq-clock__label">截止</span><time class="lq-clock__value">3 天 4 小时</time><span class="lq-clock__abs">9月22日 23:59</span></div>`
- **视觉**：`urgent` dot 脉冲一次；`closed` 只留绝对时间；绝对时间**常显**（v1 §3.3）。考试变体 `--compact` 只显示 `mm:ss`，`urgent` 时变色**不改布局尺寸**（tabular-nums + 固定宽）。

### 5.3 长任务 `lq-job`（JobStatus）

- **用途**：AI 出题、AI 批改、Agent 任务、简历生成、材料导出、错题归集重算。
- **DOM**：`<div class="lq-job" data-tone="job-running"><div class="lq-job__head"><span class="lq-status">运行中</span><time class="lq-job__elapsed">01:24</time></div><div class="lq-progress"></div><p class="lq-job__msg"></p><div class="lq-job__actions"><button class="lq-btn lq-btn--ghost lq-btn--sm">刷新状态</button><button class="lq-btn lq-btn--destructive lq-btn--sm">中断</button></div></div>`
- **状态**：`job`/`agent` 族；`result_ready` 时 actions 变"查看结果/应用"；`superseded` 灰显并说明被谁取代。
- **行为归属**：轮询/SSE 由业务 controller 持有（`ai_workspace_widget.js:1959` SSE + 轮询退路保持）。
- **禁忌**：不用 toast 表达完成；`running` 不显示成功色。

### 5.4 题目导航 `lq-nav-grid`（QuestionNavigator）

- **用途**：考试答题卡、作答页导航、批改页跳题（`submission-jump-nav` 岛改输出同类名）。
- **DOM**：`<nav class="lq-nav-grid" aria-label="题目导航"><button class="lq-nav-grid__item is-answered" aria-current="true" aria-label="第3题，已作答">3</button>…</nav>`；分节 `<div class="lq-nav-grid__group" role="group">`。
- **状态**：`is-answered`（tint）/ `is-current`（实色）/ `is-flagged`（warning 描边）/ `is-error`（danger 描边 + ! 角标）/ `is-pending-upload`（info dot）/ 默认（描边）。每态有形状差异。
- **尺寸**：32px 方胶囊，`pointer:coarse` 40；桌面 sticky rail 宽 240，移动 `lq-sheet--bottom`。

### 5.5 编辑器骨架 `lq-editor`（EditorShell，五个独立文档共用）

- **文档结构**（独立 `<html>`）：`{% include "partials/lq_editor_head.html" %}`（ui-system 资产、令牌、`data-appearance`、`data-authenticated-user`、`#toast-container`、`ui_explanation`、`ls_date_picker`）→ `<body class="lq-editor" data-lq-editor="exam|take|lesson-plan|assessment|evaluation">`。**不加载**站点导航脚本与 `app_bottomnav`。
- **区域**：`lq-editor__bar`（56：左 返回/标题；中 `lq-status`；右 ≤3 个 `lq-btn--glass --sm` + 1 个 prominent + 更多）→ `lq-editor__rail`（280，可折叠，`lq-segment` 切换）→ `lq-editor__main`（min 640）→ `lq-editor__aside`（320，可折叠）。
- **收栏**：≥1280 三栏；1024–1279 aside 变 `lq-drawer--right`；768–1023 rail 也变 drawer；<768 单列，rail/aside 进底部 Dock 两个入口，主操作固定底栏。**主区永远 ≥320px 可编辑宽度。**
- **材质**：顶栏 `lq-glass`；rail/aside/主区 `lq-surface`；弹层 `lq-scrim + lq-glass--thick`。全页 blur ≤2。

### 5.6 弹层协调层 `LQ.layer`（以 `ui_popover.js` 为种子）与 overlay 迁移路径

`ui_popover.js` 已有：栈（`:20`）、嵌套父解析（`:71-77`）、z 由栈深决定（`:77`）、resize/blur 全关（`:24-27`）、外点、`focusFirst` + `[data-autofocus]`（`:168`）、Tab 圈闭（`:179-190`）、Esc（`:176`）、backdrop（`:203`）、焦点回归（`:238`）、reduced-motion 时长（`:242,258`）。缺：模态/非模态区分、滚动锁所有权、`beforeClose` 脏检查、关闭原因、超时兜底、原生 top-layer 与 Portal 宿主规则。

最小 API：`open(el|html, {type, modality:'modal'|'popover'|'menu'|'sheet'|'drawer', anchor, parent, returnFocus, beforeClose, onClose})`、`close(handle, reason)`、`closeTop(reason)`、`closeAll()`、`top()`。滚动锁 refcount 在协调层，iOS 用 `position:fixed + top:-scrollY`。

| 现存实现 | 消费者 | 迁移方式 | 时机 |
|---|---|---|---|
| `ui.js openModal/closeModal`（59 处 `.modal-backdrop`） | 33 文件 | 保留签名，内部改 `LQ.layer.open(byId, {modality:'modal'})`；类名保留到该页迁移 | S2 桥，S4–S6 逐页 |
| `ui_popover.js createPopoverSystem` | lessondoc_editor、白板 `twb` | 成为本体；继续导出（`whiteboard/popover.js:4` 不改） | S2 |
| `ui_overlay_motion.js setOverlayOpen` | 6 文件 | 动效钩子 | S2 |
| `poll-overlay`（`manage_polls.js:98`、`classroom_polls.js:141`） | 2 | `LQ.layer.open(html)`，类名 `lq-modal` | S6 |
| `collab-overlay`（`collaboration.js:717-760`） | 1 | 同上；confirm 包装改 `LQ.confirm` | S6 |
| `ga-modal-overlay`（`group_assignment_config.js:17-85`） | 1 | 同上 | S6 |
| `materials-editor-shell`（`material_viewer.html:200`） | 1 | `lq-drawer--wide` | S6 |
| `rz-` 手工 modal（`resume_list.js:203`） | 4 页 | 简历族迁移时统一 | S6 |
| `lde-` `dialog()` | LessonDoc | 已用 `ui_popover`，只换皮 | S6 |
| `.cs-expand`（课表整周，`course_schedule_deck.js:519-538`） | 3 宿主 | **不接入**（可移植模块契约），只换令牌；登记为"外部层"以保证 Esc 顺序 | S6 |
| `.semester-todo-modal-card`（`semester_calendar.js:1270`） | 首页/日历 | 接入；保留 `dashboard-todo-modal.spec.ts` | S4 |
| `<dialog id="assignment-kind-modal">`（原生，`assignment_detail_teacher.html:1780`） | 1 | 已是原生 dialog，登记进栈即可；`assignment-classification-modal.spec.ts` 280 行断言并发/取消/重试必须保留 | S5 |
| Radix Dialog（2 岛） | classroom/dashboard workspace | React 适配器 `useLqLayer()`：Portal 目标 `#lq-layers`，注册同一栈 | S2 适配器，S6 切换 |
| `ls_date_picker.js` 自有 popover | 全站 | 注册为 `popover` 类型（在 modal 内打开是常见嵌套） | S2 |
| `ls_image_lightbox.js` | 全站 | 注册为 `viewer`；双契约见 §5.10 | S2 |
| `ui_explanation.js` | 全站 | **不接入**（一页一个 DOM、无栈）；`closeTop` 时若打开先关它 | — |

### 5.7 三态选择 `LQ.choose`

- **API**：`await LQ.choose({title, body, options:[{key, label, variant}], initialFocus})` → `key | 'dismissed'`。
- **规则**：Esc/遮罩/关闭 = `dismissed`，**绝不映射到任何有副作用的选项**；选项 ≤3；破坏性选项用 `destructive` 且不可为初始焦点。
- **首批调用点**：`exam_editor.html:2442`（完善评分/仅存草稿/返回编辑）；`exam_take.html:3617`（交卷/回去作答）；教师页结课默认分（记默认分/仅关闭/取消）。

### 5.8 上传队列 `lq-upload`（UploadQueue + FileItem）

- **用途**：作答页附件块（`assignment_detail_student.html:551-577`）、考试每题附件、聊天/私信附件、Agent 附件（`ai_workspace_widget.js:5-14` 限制）。
- **DOM**：`<div class="lq-upload"><div class="lq-dropzone"></div><ul class="lq-upload__list"><li class="lq-file-chip" data-file-state="uploading"><progress></progress><button aria-label="取消"></button></li></ul><p class="lq-upload__policy">允许 jpg/png/pdf，单文件 ≤20MB</p></div>`
- **状态**（每项）：`selected → validating → rejected(原因常显) → uploading → uploaded(服务器确认) → failed(重试) → removing`；队列级 `idle / busy / partial-failed`。**"重复截图被拒"**（`test_submission_image_guard.py`）必须显示拒绝原因与归属题号。
- **策略文案常显**（v1 R10）。行为（`draftSyncChain`/`questionDraftUploadTimers`）不动。

### 5.9 分栏与查看器 `lq-split` / `lq-viewer`

- 批改页附件预览（`submission_detail.html:983`）、材料阅读页、成员工作区（iframe 保留）。
- `lq-split`：`grid-template-columns: var(--lq-split, 280px) 1fr`，分隔条可拖（键盘 ←/→ 8px），<1024 上下堆叠 + `lq-segment`。
- `lq-viewer`：工具条 `lq-glass` 浮于顶部；iframe 场景工具条**不叠在 iframe 上**。

### 5.10 灯箱双契约

改类名时同时保留：（a）`data-ls-lightbox[-src|-group|-title|-scope]`；（b）`ensure/isOpen/open(items, index)/close` API 与 `chat_image_preview.js` 委托；（c）`.ls-glass/.ls-glass-pill` 别名。验收：聊天、私信、消息中心、AI 对话四处 prev/next 仍限定在同一消息的兄弟附件内。

### 5.11 状态 chip 映射 `lq_status` 宏 / `LQ.tone`

见 §4.3。`data-tone` 缺失时渲染 neutral 并在 DEBUG 下 `console.warn`。

---

## 6. 逐按钮登记表：教学核心链首批

Schema：`actionId` | 可见角色 / 前置状态 | 类型 | 变体 / tone | 尺寸 | 位置（桌面 / 移动） | 文案 · aria | 图标 | 确认 | 反馈 / 完成后 | API。字体/圆角/交互态继承原案 §6.1。已有 `p03-*` testid 的按钮在"反馈"列标注。

### 6.1 试卷编辑器 `exam_editor.html`

| actionId | 可见 / 前置 | 类型 | 变体 | 尺寸 | 位置 | 文案 · aria | 图标 | 确认 | 反馈 / 完成后 | API |
|---|---|---|---|---|---|---|---|---|---|---|
| `exam.save` | `can_manage`；≥1 题 | button | **prominent**（唯一） | md | 顶栏右端 / 移动固定底栏右 | 保存试卷 | save | 评分不完整 → `LQ.choose`（D09）；已有作答改题 → **预先禁用并说明**（后端 409 `exam_papers.py:348`） | `lq-status` synced；新建后跳 `/exam/{id}/edit`；409/400 常显 `lq-alert`，内容保留 | `POST/PUT /api/exam-papers` |
| `exam.preview` | 任何 | button | glass | sm | 顶栏右 | 全屏预览 | eye | 无 | `lq-modal--full` | — |
| `exam.ai` | `can_manage` | button | glass | sm | 顶栏右 | AI 出题 | sparkles | 无（结果先预览后应用） | `lq-modal--lg` 内 `lq-job` | `POST /api/ai/exam/generate` + 轮询 |
| `exam.import` | `can_manage` | menu 项 | — | — | 顶栏"更多" | 导入 JSON | upload | 覆盖 → `LQ.confirm` destructive | 解析摘要常显；失败保留原试卷 | `POST /api/exam-papers/import-json` |
| `exam.rubric` | 任何 | button | glass | sm | 顶栏右 | 评分标准 | list-checks | 无 | `lq-modal--lg`；未完整时 warning 徽点 | — |
| `exam.cancel` | 任何 | link | ghost | sm | 顶栏左 | 返回试卷库 | arrow-left | dirty → `LQ.confirm` | 跳 `/manage/library/exams` | — |
| `exam.page.add` | `can_manage` | button | soft | sm | rail 底 | 新增页面 | plus | 无 | 追加并聚焦 | 本地 |
| `exam.question.add` | `can_manage` | button | soft | md | 主区页尾 | 新增题目 | plus | 无 | 类型 `lq-segment`（单选/多选/填空/问答，**闭集**） | 本地 |
| `exam.question.delete` | `can_manage` | button --icon | ghost | sm | 题卡 `__actions`（focus-within 常显） | 删除第N题 | trash | `LQ.confirm` destructive + 撤销 toast 8s | — | 本地 |
| `exam.rubric.distribute` | 评分弹层 | button | soft | sm | 弹层头右 | 均分总分 | divide | 无 | 合计行常显"合计 100" | 本地 |
| `exam.rubric.apply` | 评分弹层 | button | prominent（弹层内唯一） | md | `__foot` 右 | 完成评分 | check | 无 | 关弹层；徽点消失 | 本地 |
| `exam.ai.generate` / `exam.ai.cancel` | AI 弹层 / job running | button（互斥显示） | prominent / destructive-soft | md | `__foot` 右 | 开始生成 / 中断生成 | sparkles / square | 中断 → confirm | `lq-job` | generate / `…/task/{id}/cancel` |
| `exam.ai.apply` | result_ready | button | prominent | md | 预览区底 | 应用到试卷 | check | 覆盖已有题 → confirm | `lq-status dirty` | 本地 |
| `exam.scope` | `can_manage` | 原生 select | `lq-select` | md | rail 设置 | 开放范围 | — | 缩小范围且已被他人布置 → 说明常显 | — | `PATCH …/attributes` |

### 6.2 课堂页布置弹窗（`#assignment-modal`、`#exam-assign-modal`）

| actionId | 可见 / 前置 | 类型 | 变体 | 尺寸 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|---|
| `assign.new` / `assign.fromLibrary` | 教师 | button ×2 | soft | md | 任务区头 | 新建作业 / 添加考试 | 无 | `lq-modal--lg`；试卷列表空/加载/错误三态 | `GET /api/exam-papers` |
| `assign.kind` | 弹层内 | `lq-segment` | — | md | 首行 | 作业 / 期中 / 期末 | 无 | 未选 → 就地错误（`app_exams.js:674`） | — |
| `assign.schedule` | 弹层内 | `lq-segment` + 日期 | — | md | 第二组 | 长期 / 截止 / 倒计时 | 无 | 迟交策略 `lq-switch` 展开固定/梯度 | — |
| `assign.publish` | 弹层内 | submit | prominent | md | `__foot` 右 | 布置到课堂 | 无 | 关弹层 + 任务卡插入（**不整页 reload**，需后端返回卡数据） | `POST …/assign` / `POST /api/assignments` |
| `assign.saveDraft` | 弹层内 | button | soft | md | `__foot` 左 | 存为草稿 | 无 | chip `assignment-draft` | 同上 `status:new` |

### 6.3 学生作答页 `assignment_detail_student.html`

| actionId | 可见 / 前置 | 类型 | 变体 | 尺寸 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|---|
| `submit.send` | 学生；published 且 accepting 且队列空闲且无 409 | submit | **prominent**（唯一） | lg | 作答区 `__foot` 右 / 移动固定底栏 | 提交作业（补交期"补交作业"） | 未答 → `LQ.choose` | busy 锁定；成功走 `openGroupPeerEval` 再刷新；409 → `lq-status conflict`；testid `p03-submit-assignment` 保留 | `POST /submit` |
| `submit.withdraw` | 已提交且窗口内 | button | destructive-soft | sm | 我的提交卡"更多" | 撤回提交 | confirm destructive | 刷新 | `DELETE /withdraw` |
| `submit.redoRequest` | graded 且 homework 且非缺交 | button | soft | sm | 我的提交卡 | 申请重做 | `approval_workflow.js` 表单 | chip（`#withdraw-request-state`） | 审批流 |
| `submit.peerEval` | 小组已提交未揭晓 | button | soft | sm | 我的提交卡 | 完成互评 | 无 | `lq-modal` | — |
| `upload.pick` / `upload.folder` / `upload.paste` | accepting | `lq-btn-group` | soft / soft / ghost | sm | 附件块头 | 选择文件 / 选择文件夹 / 粘贴 | 无 | 进 `lq-upload` | draft-files |
| `upload.remove` | 每项 | button --icon | ghost | sm | file chip 尾 | 移除{文件名} | 无 | 移除 + 撤销 toast | `DELETE /draft-files/{id}` |
| `result.wrongBook` / `result.exportReview` | graded | link / button | link / soft | sm | 提交卡底 | 错题本复盘 / 导出复习 Word | 无 | — | — |

### 6.4 考试作答页 `exam_take.html`

| actionId | 可见 / 前置 | 类型 | 变体 | 尺寸 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|---|
| `exam.submit` | 考生；服务器时间未过截止；无 409 | button | **prominent**（唯一） | md | 顶栏右 / 移动 Dock 右（软键盘弹出 Dock 隐藏时，**表单尾保留等价按钮**） | 交卷 | 未答 → `LQ.choose`；再 `LQ.confirm` | 锁定；服务器拦截 → 常显"以服务器时间为准" | `POST /submit` |
| `exam.prev` / `exam.next` | 有前/后页 | `lq-btn-group` | glass | sm | 顶栏中 + 主区底 | 上一页 / 下一页 | 无 | `lq-nav-grid` current | 本地 |
| `exam.card` | 任何 | button --icon | glass | sm | 顶栏 | 打开答题卡 | 无 | 桌面 rail / 移动 sheet | 本地 |
| `exam.clearPage` / `exam.clearAll` | 任何 | menu 风险组 | destructive-soft | sm | "整理答卷" | 清空当前页 / 清空整张试卷 | confirm destructive | `lq-status dirty` | draft |
| `exam.draw` | 该题允许作图 | button | soft | sm | 题卡 | 手写作答 | 无 | 白板挂载（颜色为数据） | — |
| `exam.withdraw` | 已交且允许 | button | destructive-soft | sm | 结果区 | 撤回 | confirm | — | `DELETE /withdraw` |

### 6.5 批改页 `submission_detail.html`

| actionId | 可见 / 前置 | 类型 | 变体 | 尺寸 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|---|
| `grade.save` | 教师；非 returned；0–100 有限小数 | submit | **prominent** | md | 评分表单 `__foot` 右 / 移动固定底栏 | **保存评分**（不叫"发布"） | 无 | 携带 `expected_review_revision` + `expected_assignment_revision`；409 → 分数评语保留 + "重新核对"；testid `p03-submission-score-input`、`p03-submit-manual-grade` 保留 | `POST …/grade` |
| `grade.aiRegrade` | 教师；status≠grading | button | soft | md | `__foot` 左 | AI 辅助批改 | confirm | `lq-job`；失败保留旧成绩；testid `p03-ai-regrade-detail` | `POST …/regrade` |
| `grade.template` | 教师 | button | ghost | sm | 评语域头 | 插入逐题模板 | 无 | 光标处插入 | 本地 |
| `grade.prev` / `grade.next` | 教师；列表上下文 | `lq-btn-group` | glass | sm | 顶栏 | 上一份 / 下一份 | dirty → confirm | 导航 | — |
| `files.manage.save` / `files.manage.saveAi` | 教师；可管理附件 | button ×2 | soft | sm | 附件管理面板尾 | 保存附件 / 保存并提交 AI | 后者 confirm | chip | — |
| `files.delete` | 教师 | button --icon | ghost | sm | 文件行 | 删除附件 | confirm destructive | — | — |
| 学生视图 | 学生 | 不渲染评分表单、附件管理、AI 按钮（`assignment_pages.py:313,346`） | | | | | | testid `p03-submission-status` | |

### 6.6 教师作业页 `assignment_detail_teacher.html`

| actionId | 可见 / 前置 | 类型 | 变体 | 尺寸 | 位置 | 文案 | 确认 | 反馈 | API |
|---|---|---|---|---|---|---|---|---|---|
| `tasks.status` | 教师 | `lq-segment` | — | sm | 页头右 | 草稿 / 进行中 / 已截止 | 变更 → confirm（含"邮件通知" `lq-checkbox`） | chip | `PATCH /api/assignments/{id}` |
| `tasks.close` | published | menu 项 | destructive-soft | — | 页头"更多" | 截止作业 | `lq-modal`（默认分、含未批改；`LQ.choose` 三态） | — | `/close` |
| `tasks.aiGradeAll` | 有已提交未批 | menu 项 | soft | — | "批改处理" | AI 批量批改 | confirm | 每行 `lq-job` | `/regrade` 循环 |
| `tasks.zeroUnsubmitted` | 已截止 | menu 项 | destructive-soft | — | 同菜单风险组 | 未提交记 0 | confirm destructive（写明人数） | chip `absence-zero` | `/submissions/zero-unsubmitted` |
| `tasks.withdrawSelected` / `tasks.withdrawAll` | 有选中 / 有已提交 | button ×2 | destructive-soft | sm | 批量条 | 撤回选中 (N) / 全部撤回 | `lq-modal`（新截止时间） | chip `returned` | `/submissions/withdraw` |
| `tasks.filter` | 任何 | `lq-chip-row --filter` | — | — | 列表头 | 全部 / 已提交 / 已批改 / 待重交 / 未提交（含计数） | 无 | 空结果保留清除入口 | 本地 |
| `tasks.kind` | 教师 | menu 项 | — | — | "管理作业" | 作业分类 | 原生 `<dialog>` 保留（spec 280 行） | — | `PATCH …/assessment-kind` |
| `tasks.edit` / `tasks.exportGrades` / `tasks.exportFiles` / `tasks.delete` | 教师 | menu 项 | — | — | "管理作业"，delete 单独风险组 | 编辑作业 / 导出成绩 / 导出附件 / 删除作业 | delete → confirm destructive；export → `lq-job` | — | 各端点 |
| `tasks.wrongSummary` | 有批改 | link | soft | sm | 页头 | 错题归集 | 无 | 跳转 | — |
| `tasks.offline` | 教师 | button | soft | sm | 未提交行 | 线下代交 | 无 | `lq-modal--lg` | `/submissions/offline` |

### 6.7 后续批次（范围）

课堂主页（`classroom_main_v4.html:389-460` 两菜单 + 任务区 + 活动 dock + 材料批选 + 结课）、3D 课表宿主（同步菜单、上/下周、整周、定位）、最终材料（三签名点 + 导出）、消息中心（发送/冷却/标已读）、白板工具条、AI 浮窗 composer、简历（保存/发布/导出/采用建议）、管理列表页通用（新建/筛选/批量/导出/删除）。进 `docs/lq-action-registry.md` 时补 `测试编号` 列。

---

## 7. 特殊界面补充

### 7.1 作业/试卷编写页完整规格（原案缺、v1 只给模块名）

**现状**：3196 行独立文档；1048 行内联 CSS；1725 行内联 JS；`.modal-overlay > .modal-box` 私有弹层 4 个；4 种题型闭集；试卷级只有 标题/说明/开放范围/整卷 AI 开关/评分（总分、风格 strict|medium|loose|rescue、说明）；**kind/时限/重交/分组/可见性全部在布置时决定**；保存 PUT 无版本字段；已有作答/草稿改题 → 409。

**骨架**：`lq-editor`（§5.5），`data-lq-editor="exam"`。

**顶栏**：左 返回 + 标题（`lq-input--ghost`，占位"未命名试卷"）；中 `lq-status` + 只读 chip（已布置：`已布置 · 2 个课堂`，点击 `lq-popover` 列课堂；有作答 → warning chip"已有作答，题目锁定"）；右 §6.1 按钮。

**rail**（280）：`lq-segment`【题目 | 设置】。题目 = 页面/题组 `lq-list`（页名 + 题数 + 分值合计；拖排序，键盘 Alt+↑/↓；当前页 tint）+ 页内 `lq-nav-grid` 缩略（类型图标 + 序号；未配评分 warning 角标）+ `exam.page.add`。设置 = `lq-form-section` ×3（基本：标题、说明；开放范围：`lq-select` 三档 + 常显说明"全校可见后其他教师可复制"；AI：整卷允许学生用课堂 AI `lq-switch` + 常显说明）。

**主区**（min 640）：每题 `lq-card`——头：`题 N` + 类型 chip（neutral）+ 分值 chip（点击行内编辑）+ `__actions`（上移/下移/复制/删除，focus-within 常显）；体：题干 `lq-textarea`（Markdown，`lq-segment--sm` 预览切换）；按类型：选项列表（`lq-input` + 正确答案 `lq-radio`/`lq-checkbox` + 删除；"添加选项" ghost）/ 填空占位 / 问答：占位 + **附件要求** `lq-form-section--inline`（`lq-switch` 必需 → 最少/最多 `lq-input`、允许类型 `lq-chip-row --tag`、说明）；脚：本题允许 AI `lq-switch--sm` + 评分摘要行（"标准答案 · 得分点 3 · 失分点 1"，点击开评分弹层定位；缺失时 warning 常显"未设评分标准"）。页尾 `exam.question.add`。

**aside**（320）：`lq-segment`【预览 | 评分】。预览 = `lq-prose`（现 `#preview-content`）；评分 = 本页分值表 `lq-table--dense` + 合计 + 目标总分 + 均分。

**弹层**：评分标准 `lq-modal--lg`（左 `lq-nav-grid` 定位、右 分值/标准答案/得分点/失分点；`__foot` 均分 soft + 完成 prominent）；AI 出题 `lq-modal--lg`（材料 `lq-list` 多选、四题型数量、考核类型提示 `lq-segment`、`lq-job`、结果 `lq-prose` + 应用）；导入 JSON `lq-modal`（`lq-dropzone` + 模板 link + 解析摘要常显）；全屏预览 `lq-modal--full`。

**状态矩阵**（都要截图）：空试卷 / 有题无评分 / 评分完整 / 已布置未作答 / 已有作答（题目锁定，设置可改）/ 只读共享（`can_manage=false`：编辑控件不渲染）/ AI running·failed·ready / 导入错误 / 保存 409·400 / 未保存离开。

**移动端**（<768）：单列主区；顶栏只留返回、标题、`lq-status`、更多；底部固定栏：目录（sheet）/ 保存（prominent）/ 属性（sheet）。

**后端票**（不混入换皮）：PUT 携带 `expected_revision` 并 409（v1 §6.1）；布置弹窗返回新任务卡数据以免整页 reload。

### 7.2 3D 课表：对原案 §9.1 的修订

- 保留：全部公开 API/纯函数/data 契约/类名（原案补充段 + §2.8 工作区四项）；所有周同时在 DOM、`offset∈[-1,5]` 可见（最多 7 张卡，**卡片不能有 backdrop-filter**）；`.cs-stage` `overflow: clip`；`.cs-expand` 自管对话框；两个 `role=status` + `[data-csd-indicator]` 周次播报。
- D13：`DECK_CSS` 留在模块内，改引用令牌；`COURSE_PALETTE` 从 `--tone-course-1…10` 读取；`.d.ts` 补齐；三宿主 `?v=` 同步。
- 按 v1 R17 撤回"上周/本周/下周 segment"：前后周 `lq-btn--glass --icon`，"本周"为回到当前周的 `lq-btn--soft --sm`（仅不在本周时显示）。
- 迷你卡标签改 8px 状态点 + 卡片 `aria-label` 含"N 项待审"，交互只在展开态。
- 同步入口合并为一个 `lq-btn--glass --sm` + `lq-menu`，菜单两项分别标注来源与权限，toast 分别写明来源。
- 移动"按日列表"复用 `student_dashboard_schedule.js` 的 `agenda` 模式，教师首页也开放，不新写。
- 验收：`npx playwright test --config tests/e2e/components/playwright.config.ts`（含工作区新增 8 行断言）+ `npm test` + `tests/e2e/specs/dashboard-schedule.spec.ts` 12 条；DECK_CSS hex = 0。

### 7.3 五个独立编辑器与作答页

| 文档 | 行数 | `data-lq-editor` | 差异 |
|---|---|---|---|
| `exam_editor.html` | 3196 | `exam` | §7.1 |
| `exam_take.html` | 4228 | `take` | 顶栏 `lq-clock--compact` + 已答 n/m + `exam.submit`；rail = `lq-nav-grid` + `lq-status`；主区 `lq-paper`（42–56rem）；无 aside；白板按题挂载；`data-lq-lock-nav`；2514 行内联 JS 拆 `static/js/exam_take/{timer,navigator,answers,attachments,submit}.js`，**D11 先改测试装载** |
| `lesson_plan_editor.html` | 57 | `lesson-plan` | 主区 = 每课次 8×4 表格（像素复刻 Word，**表格本体不套 lq-table**）；aside = 课次导航 + 导出 `lq-job` |
| `assessment_plan_editor.html` | 81 | `assessment` | `lq-form-section` + `SignaturePointControl`（signature 族）+ 导出 `lq-job` |
| `teacher_evaluation_editor.html` | 86 | `evaluation` | 10 项 `lq-slider`/`lq-input` + 综合评价只读 + 评语 `lq-textarea`（≤300 计数常显）；导出前完整性闸 409 常显 |

### 7.4 课堂主页

先交付**入口映射表**（每个现有入口 → 新位置 → 权限）：顶栏两个 `<details>`（`:379-456`：班级成员/修为、结课、消息、反馈、个人中心、安全、退出）、任务区 `:902-1170`（新建作业、添加考试、分组配置、卡片分类）、活动 dock 五 tab `:1181-1205` + 研讨室/一对一、私信 `:1378`、材料批选 `:1538-1545`、课次导航 `:484-690`（管理课次、AI 任务条、点名）、学习进度 `:696-899`。

`ActivityHost` 单实例：桌面右栏与 <1280 底部 sheet 之间移动同一 DOM 节点，不重挂 `chat.js`/`collaboration.js`（由 `classroom-page.tsx` 一次性 import）。已有监听器/socket 不增断言（`home-classroom-workspace.spec.ts:52-70`），新增"切断点后草稿与滚动位置保留"。

`classroom.css` 13,180 行（`ui-system.src.css:12004-25182`）按区块拆 `lq/pages/classroom-{shell,session,progress,tasks,activity,chat,materials,modals}.css`，每块迁完删原段；目标按删后实测，不预设 4k。

### 7.5 博客

S0 首胜：删 `ui-system.src.css:28625-31754`；验证 `.blog-shell` 宽 1200、`[hidden]` 强制隐藏（`blog-paper.css:32-34`）仍成立。其余按原案 §9.12；composer 焦点圈闭（`blog.js:341`）与用户 popover 视口钳制（`:1388-1426`）迁 `LQ.layer` 时行为等价。

### 7.6 白板与考试白板

采纳 v1 §7.3：笔迹颜色是数据（`constants.js:65-66,111-120`、`state.js`、`export.js`），UI 选色器可令牌化，落笔前解析为稳定 hex，lint 例外登记。保留 `is-drawing` 关 blur（`board.js:183-192`，260ms）、`twb-` 前缀、`teacher-whiteboard-*` 类名冻结（`exam_take.html:937,1391`）、`z-index 2400`（改 `--ls-z-viewer` 且 ≥ iframe）、`--teacher-whiteboard-*` 运行时变量不收编。

### 7.7 登录 / 状态页族（D21）

`base_centered.html` 137 行内联 → `lq/pages/centered.css`；`.login-card` → `lq-login-card`（`lq-glass--thick` 默认；有背景图且 `sampleImageTone` 对比通过才 `--clear`）；`.status-card` → `lq-card--status`（error/permission_denied/session_expired/status）；人生一言 `:50741` 玻璃卡与 `:52146` 性能层只改类名。

### 7.8 简历族

并入 `sidebar` 布局的前提：`rz-` 私有 modal 先迁 `LQ.layer`；`revision/render_revision`、409/428 静默（`resume_common.js:325`）、导出 content-type 门与同源守卫（`:158-188`）、任务轮询（`resume_list.js:358,392`）保留；`?v=20260906a` 15 处改 `asset_url`。

### 7.9 监控 / 星图

登记为"局部内容主题"：监控 `--mb-*` → `--tone-monitor-*` + `[data-lq-scope="monitor"]` 深色覆盖，负外边距全出血保留；星图 `career_path.css` 引入 tokens 但保留 `.career-root` 作用域、`[hidden]` 强制隐藏（`:23`）、fixed-inset 画布。

### 7.10 消息中心 / 私信 / AI 对话

`lq-bubble` 与 composer 共享；`chat.js` 以 `createElement` 建 DOM（16 处 `style=`），迁移成本低于原案估计；表情 popover 三段结构保留；12s 冷却与 `can_send` 门保留；bell toast 标记 4 处重复 → 一处 partial；AI 提问表单 `data-question-signature` 防轮询打断 IME（`ai_workspace_widget.js:1602-1614`）保留。

---

## 8. 工程补充

### 8.1 静态交付（D01，S0）

1. `build:css` 产物改名 `tailwind-app.<hash>.css`（或 `asset_url` 对 `static/css`/`static/js` 计算内容哈希写入 manifest）；`static_asset_cache_control()` 对哈希 URL 返回 `immutable`。
2. nginx：`location /static/ { gzip_static on; expires 1y; }` 仅对哈希文件；非哈希路径继续走 FastAPI。
3. `asset_url` 的 `@lru_cache(maxsize=1)` 以 `X-LanShare-Release` 为 key。
4. 验收：二次导航无 CSS 请求（304 也不允许）；`Clear-Site-Data` 首屏逻辑不变。

### 8.2 CSS 拆分（替代原案"总 CSS ≤15k 行"）

**按壳加载，不按页加载**（Tailwind CLI 单入口）：`lq/index.css`（令牌+材质+组件，≤120KB min）+ `lq/shell-{sidebar,topbar,editor,centered}.css` + `lq/pages/*.css`。过渡期两份并存，每壳只加载自己的 pages 包。体积目标改为"每壳首屏 CSS ≤400KB min"。

### 8.3 守卫（从零建）

`tools/ui/lint_lq.py`（原案 §10.5 八项 + v1 三级）+ `package.json` `lint:lq` + CI 一条；例外登记 `docs/lq-lint-exceptions.json`（`path/reason/owner/reviewAt`），首批例外：白板笔迹色、课表 `DECK_CSS` 注入机制、`--teacher-whiteboard-*` 运行时变量、echarts vendor、LessonDoc `2.0/` 引擎。grep 一律排除 `.codex-temp/`。

### 8.4 真实测试入口（修正 v1 §10.1）

| 范围 | 命令 | 备注 |
|---|---|---|
| 前端单测 | `npm test`（vitest：`frontend/src/**/*.test.ts` 29 + 白板 13 + lessondoc_editor 8） | 含课表纯函数 |
| 类型 / 构建 | `npm run typecheck`、`npm run build` | — |
| 默认 e2e | `npx playwright test`（`tests/e2e/specs` 28 条，Windows PowerShell webServer） | 忽略 3 条 ui-v3；含作业链 4 条 |
| ui-v3 | `npx playwright test --config tests/e2e/ui-v3.playwright.config.ts`（43 场景，合成 fixture 8152） | 监听器/socket、滚轮边界、reduced-motion；**无 CLS/4200** |
| 组件 fixture | `npx playwright test --config tests/e2e/components/playwright.config.ts`（13 条） | 课表、同步、日历、签名点、agent 确认、成绩公布 |
| 成员工作区 | `tests/e2e/classroom-members.config.ts` | — |
| 考试草稿版本 | `node tests/frontend/exam_draft_version.test.cjs`（D11） | 另 11 个 `.cjs` 手工探针 |
| Python | `python -m unittest discover -s tests -t .` | 提交/图片/分组/错题/审批等 |

S3 新增：`exam-authoring.spec.ts`、`assignment-student-draft.spec.ts`、`exam-take.spec.ts`、`grading-concurrency.spec.ts`、`wrong-summary.spec.ts`、`layout-stability.spec.ts`（D19/D20）。

### 8.5 缓存联动清单

- `course_schedule_deck.js` ↔ `dashboard.js:2`、`manage_course_schedule.js`、`student_dashboard_schedule.js` 的 `?v=deck3d-*`。
- `classroom-page.tsx:7-19` `LEGACY_MODULES`（9 项手写 `?v=`）→ 读取 `window.__LS_ASSET_REV`。
- `signature_point_workflow.js:2` 与 `academic_final_materials.js:4` 对 `signature_multi_select.js` 用了**两个不同** `?v=` → 统一。
- 岛屿 `?v=` 契约测试（`lessondoc-editor-2026-09.md:786`）。

### 8.6 shadcn 退役步骤（D02）

1. S0 删除 13 个未引用文件；`npm run typecheck && npm run build`。
2. S2 写 `frontend/src/lib/lq-layer.ts`（`useLqLayer`）；`classroom-workspace.tsx:291`、`dashboard-workspace.tsx:249` 切换。
3. 删 `dialog.tsx` 与相关 `@radix-ui` 依赖；`components.json` 注明"仅历史"。

---

## 9. 修订路线 S0–S8（出口条件制）

沿用 v1 编号；★ 为本文新增。不给天数。

| 阶段 | 产物 | 出口条件 |
|---|---|---|
| **S0 基线、决策、首胜** | 采纳 D01–D22；★ 静态交付哈希 + immutable + nginx；★ 删 legacy blog.css 段、13 个 shadcn 文件、`data-mobile-collapse`；★ D10 评分修复；★ `LEGACY_MODULES` 版本注入；★ `base_centered` 内联抽出；路由级台账初稿；全站 before 截图（含 5 独立编辑器） | 二次导航零 CSS 请求；CSS 体积下降可测量；§8.4 所有入口全绿并记录命令与基线；未动业务行为 |
| **S1 令牌、状态色、偏好基础** | `lq/tokens.css`（亮/暗成对）+ ★ §4 注册表 + `tokens.json`；`lq/materials.css`；别名层；★ 三个文档根都输出 `data-appearance`；偏好两列增量迁移（D12）；构建链验证 | `data-lq-glass=off` 时全站 computed `backdrop-filter: none`；SQLite/PG 迁移双通过；旧页截图 diff 仅色值 |
| **S2 基础组件与协调层** | 原案 §6 基础组件 + ★ §5 的 11 个业务组件；`LQ.layer`（`ui_popover.js` 扩展）+ `openModal` 桥 + React 适配器 + 日期/灯箱注册；`LQ.confirm` + ★ `LQ.choose`；`/dev/lq` 预览页；三入口语义等价测试 | 弹层链（modal → 日期 popover → 说明浮窗 → Esc 顺序）e2e 通过；axe 零 serious；lint 对 `lq/` 零报错 |
| **S3 端到端试点 + 批改链 e2e** | 试点 A：管理列表页（升级 3 个宏，抽查 8 页）；试点 B：学生只读页；★ D19 六条 spec + ★ D20 `layout-stability.spec.ts`；★ D11 测试改装载 | 双角色/权限/表单/弹层/手机/深色/性能闭环；结构性 e2e 同步更新通过 |
| **S4 壳与常规页** | `manage/layout.html`→`sidebar`（D05）；`base_navbar`→`topbar`；`app_bottomnav`→`lq-dock`；学生首页（含 D16 A1/A2/C3）；教师首页（留管理壳）；日历；消息中心；个人资料（双基类）；★ 登录/状态页族（D21） | 每批可独立回退；`teacher-app-shell`、`dashboard-schedule` 12 条、`dashboard-todo-modal` 通过；CLS/高度断言通过 |
| **S5 教学核心链** | ★ §7.1 试卷编辑器（含 `expected_revision` 后端票）；布置弹窗；作答页；考试页（拆模块）；批改页（D10 门禁）；教师作业页；错题归集；★ §6 登记表落实并补测试编号；最终材料/签名子批次 | v1 B01–B07 + S3 六条 spec 全绿；六模板内联 `<style>` = 0；状态色全部走注册表；`p03-*` testid 全部保留 |
| **S6 复杂工作台** | 3D 课表（D13/§7.2）；课堂主页四批（§7.4）；白板；AI 浮窗/Agent；LessonDoc 编辑器壳；材料阅读/HTML 壳；简历族；投票/分组；监控/星图；★ 其余 3 个独立编辑器 | 各族专属验收；生命周期/坐标/版本/连接/导出不变 |
| **S7 全站收敛** | 按台账删孤立 CSS 与兼容层；深色/五配色校准；四份实施资料定稿；★ `tokens.json` 交付小程序 | 零遗漏路由；例外有登记；每壳首屏 CSS ≤400KB min；实机记录填写 |
| **S8 可选增强** | 折射（≤3 处）、同文档 View Transitions、`@starting-style` | 有实测收益且可独立关闭 |

**不作为完成前提**（D18）：Tailwind 4、向导去 iframe、HTML 包壳 sandbox、跨文档转场、每文件行数、每阶段必部署。

---

## 10. 文档合并与所有权

### 10.1 原案条款替换清单

| 原案位置 | 处置 |
|---|---|
| §0/§2 数字 | 以本文 §1 为准 |
| §1.3 选型（shadcn 5 基元、sonner、framer-motion、lucide-static） | D02 退役；依赖不新增 |
| §4.11 未登录 localStorage | D12 删除 |
| §6.10 "全部原生 dialog/popover" | D08 |
| §6.11 "React 用 sonner" | 删除；React 调 `window.LQ.toast` |
| §7.1 四壳→一壳、dashboard→topbar | D05 |
| §8 五色 agenda | 并入 §4.2 |
| §9.1 CSS 抽出、周 segment | D13、§7.2 |
| §9.2–9.4 | §6、§7.1、§7.3 扩展；"发布成绩"改"保存评分" |
| §9.10 向导去 iframe | D18 保留 |
| §10.1 `@tailwind` 后 `@import` | v1 R03 |
| §11 P0–P10 | §9 S0–S8 替代 |
| §12.3 nginx 不变、CLS 沿用 | D01、D20 |
| P10 Tailwind 4 | D18 |

### 10.2 四份实施资料

- `docs/lq-components.md`：原案 §6 + 本文 §5。
- `docs/lq-action-registry.md`：本文 §6 为首批，schema 固定。
- `docs/lq-migration-registry.json`：v1 §8.4 字段，S0 建。
- `docs/lq-acceptance.md`：§8.4 入口 + v1 §10 矩阵 + 实机记录。
- 令牌真源 `static/css/lq/tokens.css`；`docs/lq-tokens.json` 为生成物。

### 10.3 本次核验记录

- 已完成：两份文档通读；六路并行只读核对（构建/部署、壳与反馈层、作业链、首页/课堂/课表、其余特殊面、姊妹计划文档）；代理结论冲突已直接复核（`tests/e2e/specs` 存在 28 条且含 4 条作业链；聊天灯箱走程序式 API；五个独立文档均无 `{% extends %}`）。
- 未执行：浏览器回归、真实机型测试、数据库迁移、部署。所有"验收"均为待实施项。
- 已知未覆盖：小程序端细节；`materials_manage.js`（6,922 行）内部未逐函数核对，其 14 处 `openModal` 按 §5.6 桥接。
