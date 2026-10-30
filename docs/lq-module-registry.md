# LQ 模块使用记录本

生成于 2026-10-31，命令 `python tools/ui/lq_module_registry.py`（源码只读扫描；设计说明来自 `docs/lq-module-registry.notes.json`，由人工维护）。

## 怎么用

1. **改模块前**：找到模块条目，看「消费者」里列出的模板 / JS / React / 后端文件，逐个判断这次改动会不会破坏它们的布局、状态或业务钩子；影响面大的先写改进计划再动手。
2. **改模块后**：重新运行生成器，把新的消费者、入口或根类记录进来，并在 notes 里更新设计摘要；`--check` 可作门禁，漂移即失败。
3. **新增模块**：先在 notes 里写标题、摘要、参数、状态与规则，再提供 CSS / 宏 / JS / React 入口与至少一个真实消费者；不要先在页面里写私有组件。
4. **页面只做布局**：`static/css/lq/pages/*.css` 是页面适配层，只允许摆放共享模块与留白，不得重新配色或造第二套材质；见文末「页面适配层」。

设计原则真源：[液态玻璃组件开发合同](lq-component-contract-2026-09-27.md)、[组件 API](lq-components.md)、[场景色调契约](lq-scene-ink-2026-09-26.md)、[模板增补](lq-ui-template-sweep-2026-10-13.md)。

## 总览

| 模块 | 层 | 根类 | 入口 | 消费者文件数 |
|---|---|---|---|---|
| [Control geometry 控件几何](#control-geometry) | foundation | — | 仅 CSS | 0 |
| [Control states 控件状态配方](#control-states) | foundation | — | 仅 CSS | 0 |
| [Declarative 显式声明与领域壳](#declarative) | foundation | `lq-disclosure-popover`, `lq-disclosure-trigger`, `lq-domain-choice`, `lq-domain-raised`, `lq-domain-region`, `lq-domain-toolbar` … | JS | 153 |
| [Domain controls 领域控件就地接入](#domain-controls) | foundation | `lq-chip-group`, `lq-domain-graph-choice`, `lq-pagination` | JS | 45 |
| [Material boundaries 用户偏好终裁](#material-boundaries) | foundation | — | 仅 CSS | 0 |
| [Materials 材质层（四层材质模型）](#materials) | foundation | `lq-glass`, `lq-material-accent`, `lq-material-control`, `lq-material-muted`, `lq-profile`, `lq-surface` | 仅 CSS | 180 |
| [Motion 动效参数](#motion) | foundation | `lq-domain-popover` | 仅 CSS | 1 |
| [Page backdrop 页面背景层](#page-backdrop) | foundation | `lq-page-backdrop` | 仅 CSS | 1 |
| [Prose copy 正文排版](#prose-copy) | foundation | — | 仅 CSS | 0 |
| [React bridges](#react-bridges) | foundation | `lq-avatar-link` | 仅 CSS | 1 |
| [Ability radar 六边形能力雷达](#ability-radar) | component | `lq-radar` | 仅 CSS | 3 |
| [Business 业务呈现（DeadlineClock / JobStatus / QuestionNavigator）](#business) | component | `lq-clock`, `lq-job`, `lq-nav-grid` | 宏 / JS / Py | 1 |
| [Button 按钮](#button) | component | `lq-btn` | 宏 / JS / Py / React | 278 |
| [Chip 芯片](#chip) | component | `lq-chip` | 宏 / JS / Py / React | 36 |
| [ChipRow 芯片行](#chip-row) | component | `lq-chip-row` | 宏 / JS / Py | 10 |
| [Collapsible 折叠](#collapsible) | component | `lq-collapsible`, `lq-filter-more` | 宏 / JS / Py | 8 |
| [Composer 输入区](#composer) | component | `lq-composer` | 宏 / JS / Py | 5 |
| [Content 内容组件（Card / List / Row / Empty / PageHead / FilterBar / Prose / Bubble）](#content) | component | `lq-board`, `lq-bubble`, `lq-card`, `lq-empty`, `lq-filter-bar`, `lq-list` … | 宏 / JS / Py / React | 174 |
| [Cultivation emblem 境界标志与道印图案身份](#cultivation-emblem) | component | `lq-cultivation-emblem`, `lq-cultivation-emblem-img`, `lq-cultivation-host`, `cultivation-card__emblem` | 仅 CSS | 8 |
| [Dialog / Sheet / Drawer / Confirm / Choose](#dialogs) | component | `lq-dialog`, `lq-dialog-root`, `lq-drawer`, `lq-modal`, `lq-popover`, `lq-scrim` … | 宏 / JS / Py | 33 |
| [EmojiPicker 表情选择器](#emoji-picker) | component | `.emoji-picker`, `.emoji-picker-item`, `.emoji-picker-grid`, `createEmojiPicker` | JS | 5 |
| [Forms 字段与原生输入](#forms) | component | `lq-checkbox`, `lq-choice`, `lq-control`, `lq-error-summary`, `lq-field`, `lq-form-actions` … | 宏 / JS / Py / React | 178 |
| [Indicators 徽标 / 头像 / 进度 / 加载](#indicators) | component | `lq-avatar`, `lq-badge`, `lq-progress`, `lq-spinner` | 宏 / JS / Py / React | 19 |
| [Insights 统计（AvatarStack / Ring / Bars / Meter）](#insights) | component | `lq-avatar-stack`, `lq-bars`, `lq-insight`, `lq-meter`, `lq-ring` | 宏 / JS / Py | 4 |
| [Layer 层栈](#layers) | component | — | JS | 25 |
| [Lightbox 灯箱](#lightbox) | component | `lq-lightbox` | JS | 1 |
| [Menu / Popover 菜单与浮层](#menus) | component | `lq-menu` | 宏 / JS / Py | 12 |
| [NavMenu 导航菜单](#nav-menu) | component | `lq-nav-menu` | 宏 / JS / Py | 6 |
| [Navigation Tabs / Segment](#navigation) | component | `lq-segment`, `lq-tabs` | 宏 / JS / Py | 20 |
| [OverflowLabel 固定宽度长标签](#overflow-label) | component | — | JS | 1 |
| [Search 搜索包装](#search) | component | `lq-search` | 仅 CSS | 12 |
| [Selection / Dropdown 选择列表](#selection) | component | `lq-dropdown`, `lq-selection` | 宏 / JS / Py | 9 |
| [Skeleton 骨架屏](#skeleton) | component | `lq-skeleton` | 宏 / JS / Py | 22 |
| [Status / SaveStatus / Alert / Conflict](#status) | component | `lq-alert`, `lq-conflict`, `lq-save-status`, `lq-status` | 宏 / JS / Py / React | 22 |
| [Table / Pager / BulkBar / ResultCount](#tables) | component | `lq-bulk-bar`, `lq-pager`, `lq-result-count`, `lq-table`, `lq-table-shell` | 宏 / JS / Py / React | 11 |
| [Toast 通知](#toast) | component | `lq-toast`, `lq-toast-fallback`, `lq-toasts` | JS | 4 |
| [Tooltip 提示](#tooltips) | component | `lq-tooltip` | 宏 / JS / Py | 2 |
| [Upload / Dropzone / FileChip](#upload) | component | `lq-dropzone`, `lq-file-chip`, `lq-upload` | 宏 / JS / Py | 1 |
| [Workspace Split / Viewer](#workspace) | component | `lq-domain-workspace`, `lq-split`, `lq-viewer` | 宏 / JS / Py | 2 |
| [Shells 页面壳（Topbar / Sidebar / Dock / Editor / PageLayout / FAB / Crumbs / Steps）](#shells) | composite | `lq-crumbs`, `lq-dock`, `lq-editor`, `lq-fab`, `lq-nav-item`, `lq-page-layout` … | 宏 / JS / Py | 10 |

## 模块

### control-geometry

**Control geometry 控件几何** · 层：foundation · CSS：`static/css/lq/components/control-geometry.css`

- **设计摘要**：按钮/芯片的胶囊半径、三档尺寸高度、44px 触控最小命中区；多行内容的选择卡用 `data-lq-shape="surface"` 退出胶囊。
- **规则**：大块内容不得被画成胶囊；改尺寸要同时看 composer / toolbar / 表格里的小按钮。 2026-10-24：复合按钮（两组及以上内容、至少一组上下叠放）自动改为卡片圆角 + 充足内边距 + 柔和选中态，不再塞进胶囊；被套上胶囊圆角的旧控件若边缘带圆点/计数/徽标，至少留出 12px 内边距。 2026-10-26：复合卡片与 `data-lq-shape="surface"` 选择卡的高度由内容决定（`min-height: auto`），不再继承胶囊高度下限——显式下限会成为 flex 收缩目标，放进有界滚动列（申诉与申请抽屉、AI 历史列表）时整列卡片被压扁叠在一起；列表容器只负责滚动，不得给卡片设固定高度。回归：`tests/e2e/components/lq-content-card-scroll.spec.ts`。
- **消费者**：0 个文件

### control-states

**Control states 控件状态配方** · 层：foundation · CSS：`static/css/lq/components/control-states.css`

- **设计摘要**：按钮 / 芯片 / 字段共享的 hover、focus-visible、active、selected、disabled、loading、aria-busy 状态变量（`--lq-control-*`），按压形变与回弹来自 motion。
- **规则**：新控件不得自写 hover/focus 配色；disabled 用 native disabled，aria-disabled 由唯一控制器阻止执行。
- **消费者**：0 个文件

### declarative

**Declarative 显式声明与领域壳** · 层：foundation · CSS：`static/css/lq/components/declarative.css`

- **设计摘要**：`data-lq-component` 显式声明把服务端/领域 DOM 的填充、前景、描边锁定到共享配方，防止晚加载的旧样式夺回外观；也提供 `lq-domain-toolbar`（组合边界）、`lq-domain-choice`（选择卡）、`lq-domain-raised`（浮层材质）、`data-lq-selected` Surface 选中态、`lq-disclosure-popover`（原生 details 浮层）。
- **参数 / 变体**：`data-lq-component="button|input|select|surface|toolbar|choice|popover|…"`、`data-lq-padding="none|sm|md|lg"`、`data-lq-selected="true"`、`data-lq-align/placement`。；`data-lq-surface="embedded"`（2026-10-24：宿主里的分组，不铺底、不描边、不成为模糊宿主，内边距由宿主负责）。
- **规则**：声明必须与规范 class、正确 DOM、实际 owner 同时存在；纯布局 toolbar/region 不因声明获得模糊。 作者把声明 Surface 设计成扁平分组（`background: transparent; padding: 0`）时必须同时加 `data-lq-surface="embedded"`，否则声明的 `!important` 底色会铺回来，形成"有底无边距"的框套框。空的 `.lq-btn.btn-close` 自动画共享叉号。 `lq-domain-choice[data-lq-shape="surface"]` 退出胶囊后高度即内容高度（2026-10-26），在滚动列里不会被压缩；需要固定格高的网格单元（学期日历）在 domain-controls.css 以更高特异性自设 min-height。 2026-10-27材料资源管理器工具栏/窗格头/状态栏用embedded Surface，不各自采样模糊；树节点/文件选择和卡片主入口为原生button+Choice，菜单按钮为兄弟节点。
- **根类**：`lq-disclosure-popover`, `lq-disclosure-trigger`, `lq-domain-choice`, `lq-domain-raised`, `lq-domain-region`, `lq-domain-toolbar`, `lq-native-dialog`
- **JS 入口** `static/js/lq/domain-controls.js`：`adoptDomainContentSlot`, `adoptDomainControl`, `adoptDomainHandle`, `createDomainButton`, `createDomainPopoverSystem`, `domainButtonAttributes`
- **消费者**：153 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/assignment_detail_teacher.html` ×49
- `templates/classroom_main_v4.html` ×33
- `templates/dashboard.html` ×22
- `templates/dashboard_teacher.html` ×22
- `static/js/materials_manage.js` ×20
- `templates/partials/materials/modals_library.html` ×18
- `static/js/teacher_onboarding.js` ×17
- `templates/manage/gongwen.html` ×17
- `static/js/attendance_reports.js` ×16
- `templates/exam_editor.html` ×16
- `templates/manage/attendance_reports.html` ×16
- `templates/manage/signatures.html` ×16
- `templates/partials/materials/modals_generate.html` ×14
- `templates/assignment_wrong_summary.html` ×12
- `classroom_app/services/document_render_service.py` ×10
- `templates/partials/materials/modals_repository.html` ×9
- `static/js/materials_explorer.js` ×8
- `templates/manage/academic_final_materials.html` ×8
- `templates/manage/classes.html` ×8
- `templates/manage/courses.html` ×8
- `templates/material_viewer.html` ×8
- `templates/partials/exams/modals.html` ×8
- `templates/blog.html` ×7
- `templates/lessondoc_editor.html` ×7
- `templates/partials/classroom_members/workspace.html` ×7
- `static/js/academic_sync_dialog.js` ×6
- `static/js/dashboard_agenda_widget.js` ×6
- `templates/manage/signature_workflows.html` ×6
- `templates/manage/system/academic_integrations.html` ×6
- `templates/manage/system/smart_classroom_integrations.html` ×6
- `templates/manage/system/wanderers.html` ×6
- `templates/manage/textbooks.html` ×6
- `templates/student_login_v4.html` ×6
- `templates/partials/ai_workspace_widget.html` ×5
- `templates/partials/feedback_modal.html` ×5
- `templates/partials/learning_material_selector.html` ×5
- `static/js/base_resource_modes.js` ×4
- `static/js/career_path_app.js` ×4
- `static/js/classroom_materials.js` ×4
- `static/js/classroom_polls.js` ×4
- `static/js/collaboration.js` ×4
- `static/js/global_search.js` ×4
- `static/js/learning_progress.js` ×4
- `static/js/manage_polls.js` ×4
- `static/js/materials_sync_report.js` ×4
- `static/js/semester_calendar.js` ×4
- `templates/assessment_plan_editor.html` ×4
- `templates/manage/semesters.html` ×4
- `templates/manage/system/gongwen_integrations.html` ×4
- `templates/partials/academic_sync_dialog.html` ×4
- `templates/partials/profile/messages.html` ×4
- `templates/partials/student_dashboard_schedule.html` ×4
- `templates/submission_detail.html` ×4
- `templates/teacher_evaluation_editor.html` ×4
- `static/js/agent_workbench.js` ×3
- `static/js/agent_workbench_render.js` ×3
- `static/js/approval_workflow.js` ×3
- `static/js/learning_certificate_reveal.js` ×3
- `static/js/prompt_pool.js` ×3
- `static/js/whiteboard/exam_board.js` ×3
- `templates/exam_take.html` ×3
- `templates/feedback_review.html` ×3
- `templates/manage/course_schedule_editor.html` ×3
- `templates/manage/system/password_resets.html` ×3
- `templates/partials/exams/control_panel.html` ×3
- `templates/partials/learning_certificate_reveal.html` ×3
- `templates/partials/semester_calendar_panel.html` ×3
- `templates/partials/session_material_ai_modal.html` ×3
- `templates/partials/student_security_modal.html` ×3
- `templates/partials/teacher_onboarding_modal.html` ×3
- `static/js/ai_workspace_capture.js` ×2
- `static/js/blog.js` ×2
- `static/js/classroom_material_list.js` ×2
- `static/js/course_schedule_deck.js` ×2
- `static/js/course_schedule_editor.js` ×2
- `static/js/grade_material_preflight.js` ×2
- `static/js/grade_publication_controls.js` ×2
- `static/js/group_assignment_config.js` ×2
- `static/js/group_peer_eval.js` ×2
- `static/js/manage_course_schedule.js` ×2
- `static/js/manage_courses.js` ×2
- `static/js/manage_offerings.js` ×2
- `static/js/manage_semesters.js` ×2
- `static/js/material_selection_panel.js` ×2
- `static/js/material_viewer.js` ×2
- `static/js/materials_library_cards.js` ×2
- `static/js/process_material_modal.js` ×2
- `static/js/profile_signatures.js` ×2
- `static/js/resume_common.js` ×2
- `static/js/signature_pad.js` ×2
- `static/js/signature_point_workflow.js` ×2
- `static/js/signature_workflows.js` ×2
- `static/js/teacher_evaluation_editor.js` ×2
- `static/js/tree_select_form_modal.js` ×2
- `static/js/whiteboard/toolbar.js` ×2
- `templates/base_navbar.html` ×2
- `templates/career_path.html` ×2
- `templates/learning_path.html` ×2
- `templates/lesson_plan_editor.html` ×2
- `templates/manage/assessment_plans.html` ×2
- `templates/manage/classrooms.html` ×2
- `templates/manage/course_schedule.html` ×2
- `templates/manage/exams.html` ×2
- `templates/manage/layout.html` ×2
- `templates/manage/lesson_plans.html` ×2
- `templates/manage/life_tips.html` ×2
- `templates/manage/lq_topbar.html` ×2
- `templates/manage/offering_hub.html` ×2
- `templates/manage/system/blog_crawler.html` ×2
- `templates/manage/system/feedback.html` ×2
- `templates/manage/system/monitor.html` ×2
- `templates/manage/system/organizations.html` ×2
- `templates/manage/teacher_evaluations.html` ×2
- `templates/manage/teacher_schedule_query.html` ×2
- `templates/material_render_shell.html` ×2
- `templates/partials/classroom_group_qr.html` ×2
- `templates/partials/classroom_members/exams.html` ×2
- `templates/partials/classroom_task_preview.html` ×2
- `templates/partials/dashboard_agenda_widget.html` ×2
- `templates/resume/layout.html` ×2
- `frontend/src/islands/submission-jump-nav.tsx` ×1
- `static/js/ai_chat_component.js` ×1
- `static/js/ai_workspace_history.js` ×1
- `static/js/assessment_appeal.js` ×1
- `static/js/career_path_network.js` ×1
- `static/js/cultivation_identity.js` ×1
- `static/js/emoji_picker.js` ×1
- `static/js/lesson_plan_editor.js` ×1
- `static/js/lessondoc_wizard.js` ×1
- `static/js/ls_date_picker.js` ×1
- `static/js/manage_gongwen.js` ×1
- `static/js/manage_offering_merge.js` ×1
- `static/js/manage_teacher_schedule_query.js` ×1
- `static/js/manage_users.js` ×1
- `static/js/manage_wanderers.js` ×1
- `static/js/process_material_import_summary.js` ×1
- `static/js/resume_personal.js` ×1
- `static/js/signature_multi_select.js` ×1
- `static/js/social/chat_modal.js` ×1
- `static/js/social/friends_modal.js` ×1
- `static/js/ui_explanation.js` ×1
- `static/js/user_ui_preferences.js` ×1
- `templates/macros/manage_page.html` ×1
- `templates/macros/user_ui_preferences.html` ×1
- `templates/manage/lq_sidebar.html` ×1
- `templates/manage/offerings.html` ×1
- `templates/manage/system/users.html` ×1
- `templates/partials/assessment_classification_batch.html` ×1
- `templates/partials/exams/board.html` ×1
- `templates/partials/profile/email.html` ×1
- `templates/partials/profile/hero_lq.html` ×1
- `templates/partials/submission_retained_grade.html` ×1
- `templates/partials/teaching_stage_bar.html` ×1

</details>

### domain-controls

**Domain controls 领域控件就地接入** · 层：foundation · CSS：`static/css/lq/components/domain-controls.css`

- **设计摘要**：`adoptDomainControl` / `createDomainButton` / `adoptDomainContentSlot` / `adoptDomainHandle` / `createDomainPopoverSystem`：已有领域控制器持有节点时只换呈现、不重绑业务事件；分页、图形选择卡、芯片组的布局钩子。
- **规则**：不得在全页面 MutationObserver 里猜组件；数据色样只允许真实色值槽位 `data-lq-visual="color"`。
- **根类**：`lq-chip-group`, `lq-domain-graph-choice`, `lq-pagination`
- **JS 入口** `static/js/lq/domain-controls.js`：`adoptDomainContentSlot`, `adoptDomainControl`, `adoptDomainHandle`, `createDomainButton`, `createDomainPopoverSystem`, `domainButtonAttributes`
- **消费者**：45 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/ai_workspace_history.js` ×2
- `static/js/app_files.js` ×2
- `static/js/approval_workflow.js` ×2
- `static/js/assessment_appeal.js` ×2
- `static/js/assessment_classification_batch.js` ×2
- `static/js/attendance_reports.js` ×2
- `static/js/chat.js` ×2
- `static/js/classroom_members.js` ×2
- `static/js/classroom_page.js` ×2
- `static/js/classroom_private_messages.js` ×2
- `static/js/cultivation_identity.js` ×2
- `static/js/dashboard.js` ×2
- `static/js/dashboard_agenda_widget.js` ×2
- `static/js/emoji_picker.js` ×2
- `static/js/feedback.js` ×2
- `static/js/feedback_conversation.js` ×2
- `static/js/file_preview.js` ×2
- `static/js/knowledge_tree/chrome.js` ×2
- `static/js/knowledge_tree/side.js` ×2
- `static/js/learning_certificate_reveal.js` ×2
- `static/js/lessondoc_editor/ai_panel.js` ×2
- `static/js/lessondoc_editor/canvas_controller.js` ×2
- `static/js/lessondoc_editor/content_controls.js` ×2
- `static/js/lessondoc_editor/index.js` ×2
- `static/js/lessondoc_editor/media_picker.js` ×2
- `static/js/lessondoc_editor/ui.js` ×2
- `static/js/ls_date_picker.js` ×2
- `static/js/manage_filter_chips.js` ×2
- `static/js/manage_polls.js` ×2
- `static/js/manage_signatures.js` ×2
- `static/js/manage_teacher_schedule_query.js` ×2
- `static/js/material_render_shell.js` ×2
- `static/js/material_selection_panel.js` ×2
- `static/js/material_viewer.js` ×2
- `static/js/message_center.js` ×2
- `static/js/semester_calendar.js` ×2
- `static/js/session_knowledge_tree_manager.js` ×2
- `static/js/ui_color_picker.js` ×2
- `static/js/whiteboard/board.js` ×2
- `static/js/whiteboard/exam_board.js` ×2
- `static/js/whiteboard/panels/controls.js` ×2
- `static/js/whiteboard/popover.js` ×2
- `static/js/whiteboard/text_editor.js` ×2
- `static/js/whiteboard/toolbar.js` ×2
- `static/js/career_path_network.js` ×1

</details>

### material-boundaries

**Material boundaries 用户偏好终裁** · 层：foundation · CSS：`static/css/lq/components/material-boundaries.css`

- **设计摘要**：用户的透明度 / 对比度 / 低端设备偏好对所有材质拥有最终裁决：关闭实时模糊、改为不透明配对；Toast/Tooltip 不采样。放在 index.css 最后以保证优先级。 2026-10-24：最外层 raised（以及嵌在弹窗里的 `data-lq-presence` 临时浮层）一律保留磨砂并压过嵌套守卫；减少透明度/强制颜色/打印仍关闭。
- **规则**：领域 CSS 不得用更高特异性恢复模糊或半透明；改动此文件要跑六配色亮暗矩阵。
- **消费者**：0 个文件

### materials

**Materials 材质层（四层材质模型）** · 层：foundation · CSS：`static/css/lq/materials.css`

- **设计摘要**：control / content / chrome / raised / clear 五档材质的配方：填充、模糊、光泽边、与填充配对的 ink/muted/accent。`.lq-glass` 与 `.lq-surface` 是材质根；场景色调 `data-lq-tone` / `html[data-lq-scene-tone]` 同时切换填充与全部前景。 场景自适应浓度（pages/page-material.css）：外观与页面场景色调相反时（暗外观+亮照片、亮外观+暗照片）自动把各级填充调浓到 .62–.78，暗外观次要墨色提亮一档。
- **参数 / 变体**：`data-lq-material="content|raised|clear|control"`、`.lq-glass--thick/--clear`、`.lq-surface--frost/--boundary`、`data-lq-tone="light|dark"`、`data-lq-scene="auto"`。；场景墨色 `--lq-scene-ink/-muted/-halo` 与 `.lq-on-scene` / `data-lq-on-scene`（直接坐在背景照片上的文字）。
- **状态**：透明关闭 / 高对比 / Tier B·C / forced-colors 退回不透明主题配对；嵌套材质自动不再成为模糊宿主。
- **规则**：玻璃上的文字只读 `--lq-material-ink/muted/accent`，禁用随主题翻转的灰阶；普通内容面板与重复行不得各自开 backdrop-filter；真源 docs/lq-scene-ink-2026-09-26.md。
- **根类**：`lq-glass`, `lq-material-accent`, `lq-material-control`, `lq-material-muted`, `lq-profile`, `lq-surface`
- **消费者**：180 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/classroom_main_v4.html` ×41
- `templates/assignment_detail_teacher.html` ×23
- `templates/manage/system/blog_crawler.html` ×22
- `templates/assignment_detail_student.html` ×15
- `templates/manage/system/wanderers.html` ×15
- `static/js/attendance_reports.js` ×13
- `templates/submission_detail.html` ×12
- `static/js/collaboration.js` ×11
- `templates/exam_editor.html` ×11
- `templates/manage/system/password_resets.html` ×11
- `templates/manage/offerings.html` ×10
- `templates/manage/student_detail.html` ×10
- `templates/manage/system/users.html` ×10
- `templates/assignment_wrong_summary.html` ×9
- `templates/dev/lq.html` ×8
- `templates/manage/system/monitor.html` ×8
- `static/js/classroom_interactions.js` ×7
- `templates/dev/lq_interactions.html` ×7
- `templates/exam_take.html` ×7
- `templates/manage/ai.html` ×7
- `templates/manage/courses.html` ×7
- `templates/manage/semesters.html` ×7
- `templates/partials/materials/modals_repository.html` ×7
- `static/js/cultivation_panel.js` ×6
- `templates/dashboard_teacher.html` ×6
- `templates/manage/classes.html` ×6
- `templates/manage/gongwen.html` ×6
- `templates/manage/signatures.html` ×6
- `templates/manage/system/ai_usage.html` ×6
- `templates/manage/system/smart_classroom_integrations.html` ×6
- `templates/partials/materials/modals_generate.html` ×6
- `templates/partials/materials/modals_library.html` ×6
- `templates/partials/profile/portfolio.html` ×6
- `classroom_app/services/document_render_service.py` ×5
- `static/js/message_center.js` ×5
- `static/js/profile_signatures.js` ×5
- `templates/blog.html` ×5
- `templates/dashboard.html` ×5
- `templates/dev/lq_presentation.html` ×5
- `templates/lessondoc_editor.html` ×5
- `templates/manage/academic_final_materials.html` ×5
- `templates/manage/attendance_reports.html` ×5
- `templates/manage/classrooms.html` ×5
- `templates/manage/system/gongwen_integrations.html` ×5
- `templates/partials/semester_calendar_panel.html` ×5
- `templates/partials/session_material_ai_modal.html` ×5
- `static/js/course_schedule_editor.js` ×4
- `templates/dev/lq_business.html` ×4
- `templates/learning_path.html` ×4
- `templates/manage/me_credentials.html` ×4
- `templates/manage/offering_hub.html` ×4
- `templates/manage/system/diagnostics.html` ×4
- `templates/partials/exams/modals.html` ×4
- `templates/partials/profile/messages.html` ×4
- `templates/partials/profile/overview.html` ×4
- `templates/student_login_v4.html` ×4
- `static/js/approval_workflow.js` ×3
- `static/js/blog.js` ×3
- `static/js/career_path_app.js` ×3
- `static/js/classroom_material_list.js` ×3
- `static/js/course_schedule_deck.js` ×3
- `static/js/dashboard_agenda_widget.js` ×3
- `static/js/manage_assessment_plans.js` ×3
- `static/js/manage_blog_crawler.js` ×3
- `static/js/manage_blog_review.js` ×3
- `static/js/manage_lesson_plans.js` ×3
- `static/js/manage_organizations.js` ×3
- `static/js/manage_teacher_evaluations.js` ×3
- `static/js/material_selection_panel.js` ×3
- `static/js/resume_common.js` ×3
- `static/js/semester_calendar.js` ×3
- `static/js/teacher_onboarding.js` ×3
- `templates/error.html` ×3
- `templates/macros/manage_insights.html` ×3
- `templates/manage/academic_overview.html` ×3
- `templates/manage/system/academic_integrations.html` ×3
- `templates/manage/system/agent_keys.html` ×3
- `templates/manage/textbooks.html` ×3
- `templates/partials/academic_sync_dialog.html` ×3
- `templates/partials/classroom_members/exams.html` ×3
- `templates/partials/classroom_members/workspace.html` ×3
- `templates/partials/exams/board.html` ×3
- `templates/partials/profile/settings.html` ×3
- `templates/partials/teacher_onboarding_modal.html` ×3
- `templates/permission_denied.html` ×3
- `templates/points_shop.html` ×3
- `templates/report_card.html` ×3
- `templates/session_expired.html` ×3
- `templates/status.html` ×3
- `templates/teacher_login_v4.html` ×3
- `templates/wrong_book.html` ×3
- `static/js/app_files.js` ×2
- `static/js/classroom_page.js` ×2
- `static/js/cultivation_identity.js` ×2
- `static/js/dashboard.js` ×2
- `static/js/login_scene.js` ×2
- `static/js/ls_image_lightbox.js` ×2
- `static/js/manage_users.js` ×2
- `static/js/materials_explorer.js` ×2
- `static/js/materials_manage.js` ×2
- `static/js/resume_section.js` ×2
- `static/js/signature_point_workflow.js` ×2
- `static/js/social/chat_sidebar.js` ×2
- `static/js/tree_select_form_modal.js` ×2
- `templates/dev/lq_insights.html` ×2
- `templates/dev/lq_status.html` ×2
- `templates/feedback_review.html` ×2
- `templates/macros/manage_page.html` ×2
- `templates/manage/course_schedule.html` ×2
- `templates/manage/exams.html` ×2
- `templates/manage/materials.html` ×2
- `templates/manage/system/organizations.html` ×2
- `templates/manage/system/super_admin.html` ×2
- `templates/manage/teacher_schedule_query.html` ×2
- `templates/partials/classroom_members/settings.html` ×2
- `templates/partials/cultivation_panel.html` ×2
- `templates/partials/exams/control_panel.html` ×2
- `templates/partials/feedback_modal.html` ×2
- `templates/partials/materials/library_card.html` ×2
- `templates/resume/job_targets.html` ×2
- `templates/student_auth_flow_v4.html` ×2
- `templates/teacher_register_v4.html` ×2
- `static/js/academic_final_materials.js` ×1
- `static/js/academic_schedule_sync.js` ×1
- `static/js/base_resource_modes.js` ×1
- `static/js/chat.js` ×1
- `static/js/classroom_polls.js` ×1
- `static/js/emoji_picker.js` ×1
- `static/js/global_search.js` ×1
- `static/js/group_assignment_config.js` ×1
- `static/js/group_peer_eval.js` ×1
- `static/js/learning_certificate_reveal.js` ×1
- `static/js/lessondoc_wizard.js` ×1
- `static/js/manage_agent_keys.js` ×1
- `static/js/manage_course_schedule.js` ×1
- `static/js/manage_gongwen_integrations.js` ×1
- `static/js/manage_life_tips.js` ×1
- `static/js/manage_polls.js` ×1
- `static/js/manage_semesters.js` ×1
- `static/js/manage_system_monitor.js` ×1
- `static/js/manage_textbooks.js` ×1
- `static/js/material_hub.js` ×1
- `static/js/material_viewer.js` ×1
- `static/js/materials_library_cards.js` ×1
- `static/js/offering_bootstrap.js` ×1
- `static/js/process_material_modal.js` ×1
- `static/js/resume_applications.js` ×1
- `static/js/resume_builder.js` ×1
- `static/js/resume_home.js` ×1
- `static/js/resume_list.js` ×1
- `static/js/resume_personal.js` ×1
- `static/js/signature_multi_select.js` ×1
- `static/js/signature_pad.js` ×1
- `static/js/ui_explanation.js` ×1
- `templates/achievements.html` ×1
- `templates/base_navbar.html` ×1
- `templates/dev/lq_centered.html` ×1
- `templates/dev/lq_content.html` ×1
- `templates/dev/lq_data.html` ×1
- `templates/dev/lq_workspace.html` ×1
- `templates/macros/user_ui_preferences.html` ×1
- `templates/manage/course_schedule_editor.html` ×1
- `templates/manage/life_tips.html` ×1
- `templates/manage/lq_sidebar.html` ×1
- `templates/manage/material_hub.html` ×1
- `templates/manage/offering_merge.html` ×1
- `templates/manage/signature_workflows.html` ×1
- `templates/material_viewer.html` ×1
- `templates/partials/classroom_group_qr.html` ×1
- `templates/partials/classroom_members/overview.html` ×1
- `templates/partials/classroom_task_preview.html` ×1
- `templates/partials/cultivation_card.html` ×1
- `templates/partials/learning_certificate_reveal.html` ×1
- `templates/partials/learning_material_selector.html` ×1
- `templates/partials/lq_navbar_topbar.html` ×1
- `templates/partials/profile/body.html` ×1
- `templates/partials/student_security_modal.html` ×1
- `templates/resume/builder.html` ×1
- `templates/resume/layout.html` ×1
- `templates/teacher_evaluation_editor.html` ×1

</details>

### motion

**Motion 动效参数** · 层：foundation · CSS：`static/css/lq/components/motion.css`

- **设计摘要**：按压 / 释放 / 浮层出入场 / 菜单 / 反馈的时长与曲线变量；`data-lq-motion="off|quiet|standard|expressive"` 可设在 html、组件或子树；系统 prefers-reduced-motion 优先。
- **规则**：只动画 transform/opacity，不动画 blur、宽高；不用动画时长当网络完成信号。
- **根类**：`lq-domain-popover`
- **消费者**：1 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/ui_popover.js` ×1

</details>

### page-backdrop

**Page backdrop 页面背景层** · 层：foundation · CSS：`static/css/lq/components/page-backdrop.css`

- **设计摘要**：视口背景图/纯色与到场过渡（`lq-scene-arrive`）；由 page_backdrop.js 绘制并发布场景色调。
- **规则**：材质只在视口背景上采样，面板内不得重绘一份壁纸。
- **根类**：`lq-page-backdrop`
- **消费者**：1 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/partials/lq_page_backdrop.html` ×2

</details>

### prose-copy

**Prose copy 正文排版** · 层：foundation · CSS：`static/css/lq/components/prose-copy.css`

- **设计摘要**：`.lq-prose` 内 Markdown 正文（标题、列表、表格、代码、链接）的排版与配色；AI 回复气泡与博客正文共用。
- **规则**：正文链接保留真实 a；用户文档内容是内容槽位，不强制涂成玻璃。
- **消费者**：0 个文件

### react-bridges

**React bridges** · 层：foundation · CSS：`static/css/lq/components/react-bridges.css`

- **设计摘要**：Radix/React 岛与共享 DOM 合同的桥接样式（如 `lq-avatar-link`）；React 薄适配器只消费同一 props 与 CSS。
- **规则**：React 不得各自实现颜色、动画、禁用判断或焦点栈。
- **根类**：`lq-avatar-link`
- **消费者**：1 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `frontend/src/components/action-entry.tsx` ×1

</details>

### ability-radar

**Ability radar 六边形能力雷达** · 层：component · CSS：`static/css/lq/components/ability-radar.css`

- **设计摘要**：六轴能力雷达（ability_radar_service.build_ability_radar 载荷，几何由服务端算好）。服务端页用 templates/partials/ability_radar.html；异步浮层用 static/js/ability_radar.js renderAbilityRadar(payload, {selfLabel})，两者输出同一 DOM/类名（lq-radar__*）。2026-10-27 起个人信息面板（social/user_card.js）消费 JS 版。 2026-10-28私聊侧栏也消费JS版，在领域内容槽保留图与图例、去除重复明细，并限定图尺寸；不创建第二套几何或评分。
- **规则**：改雷达 DOM 时 Jinja 宏与 JS 渲染器同步修改；JS 版只用 textContent/setAttribute，不拼 HTML；颜色只在 ability-radar.css。
- **根类**：`lq-radar`
- **消费者**：3 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/partials/ability_radar.html` ×27
- `static/js/ability_radar.js` ×26
- `static/js/social/chat_sidebar.js` ×1

</details>

### business

**Business 业务呈现（DeadlineClock / JobStatus / QuestionNavigator）** · 层：component · CSS：`static/css/lq/components/business.css`

- **设计摘要**：截止时钟、异步任务状态、题目导航网格；原控制器继续持有时钟、任务轮询与代次检查。
- **根类**：`lq-clock`, `lq-job`, `lq-nav-grid`
- **Jinja 宏** `templates/macros/lq/business.html`：`lq_deadline_clock`, `lq_job_status`, `lq_question_navigator`
- **JS 入口** `static/js/lq/business.js`：`BUSINESS_KINDS`, `businessMarkup`, `businessProps`, `createBusiness`, `deadlineClock`, `deadline_clock`, `html`, `jobStatus`, `job_status`, `questionNavigator`, `question_navigator`
- **Python props** `classroom_app/lq_business.py`
- **消费者**：1 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/dev/lq_business.html` ×5

</details>

### button

**Button 按钮** · 层：component · CSS：`static/css/lq/components/button.css`

- **设计摘要**：所有操作入口：`lq-btn` + 变体 + 尺寸 + 可选图标/徽标/加载态；原生 button/a 语义与 type、form、href 保留。
- **参数 / 变体**：`label, variant(prominent|glass|soft|ghost|destructive|link), size(sm|md|lg), icon, href, type, disabled, aria_disabled, loading, badge, attrs`；纯图标按钮必须给 aria-label。
- **状态**：default / hover / focus-visible / active 按压 / disabled（native）/ aria-disabled / loading（保留名称，防重复提交）。
- **规则**：禁止 div 模拟按钮；颜色只来自变体不传任意色；固定宽度长标签用 overflow-label。
- **根类**：`lq-btn`
- **Jinja 宏** `templates/macros/lq/button.html`：`lq_btn`
- **JS 入口** `static/js/lq/components.js`：`avatar`, `badge`, `button`, `chip`, `componentMarkup`, `createComponent`, `enhanceComponents`, `html`, `progress`, `skeleton`, `spinner`
- **Python props** `classroom_app/lq_components.py`
- **React 适配器**：`LqButton`（`frontend/src/components/lq-presentation.tsx`）
- **消费者**：278 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/classroom_main_v4.html` ×371
- `templates/assignment_detail_teacher.html` ×312
- `static/js/blog.js` ×228
- `static/js/collaboration.js` ×196
- `static/js/materials_manage.js` ×158
- `templates/exam_editor.html` ×154
- `templates/blog.html` ×148
- `templates/dashboard_teacher.html` ×125
- `static/js/career_path_app.js` ×124
- `templates/dashboard.html` ×121
- `static/js/course_schedule_editor.js` ×120
- `templates/exam_take.html` ×108
- `templates/manage/courses.html` ×108
- `static/js/manage_lesson_plans.js` ×104
- `templates/submission_detail.html` ×104
- `static/js/teacher_onboarding.js` ×92
- `templates/manage/offering_hub.html` ×88
- `static/js/materials_explorer.js` ×84
- `static/js/resume_list.js` ×84
- `templates/partials/materials/modals_generate.html` ×84
- `static/js/manage_assessment_plans.js` ×80
- `static/js/manage_teacher_evaluations.js` ×80
- `templates/manage/classes.html` ×80
- `templates/manage/gongwen.html` ×80
- `templates/manage/signatures.html` ×80
- `static/js/lessondoc_wizard.js` ×65
- `static/js/classroom_interactions.js` ×64
- `templates/manage/academic_final_materials.html` ×64
- `templates/partials/materials/modals_library.html` ×64
- `templates/partials/exams/control_panel.html` ×61
- `templates/manage/classrooms.html` ×60
- `templates/partials/materials/modals_repository.html` ×60
- `static/js/classroom_polls.js` ×56
- `templates/partials/classroom_group_qr.html` ×56
- `templates/partials/exams/board.html` ×56
- `static/js/manage_gongwen.js` ×53
- `templates/manage/textbooks.html` ×53
- `static/js/profile_signatures.js` ×52
- `static/js/resume_common.js` ×52
- `templates/manage/attendance_reports.html` ×52
- `templates/material_viewer.html` ×52
- `templates/partials/academic_sync_dialog.html` ×52
- `static/js/message_center.js` ×51
- `templates/manage/signature_workflows.html` ×49
- `templates/partials/ai_workspace_widget.html` ×49
- `templates/manage/offerings.html` ×48
- `static/js/classroom_materials.js` ×47
- `frontend/src/islands/dashboard-workspace.tsx` ×45
- `static/js/attendance_reports.js` ×45
- `classroom_app/services/document_render_service.py` ×44
- `static/js/signature_multi_select.js` ×44
- `static/js/signature_point_workflow.js` ×44
- `templates/lessondoc_editor.html` ×44
- `templates/partials/student_dashboard_schedule.html` ×44
- `templates/assignment_wrong_summary.html` ×41
- `static/js/manage_polls.js` ×40
- `templates/assignment_detail_student.html` ×40
- `templates/manage/academic_overview.html` ×40
- `templates/partials/classroom_task_preview.html` ×40
- `templates/partials/feedback_modal.html` ×40
- `templates/partials/materials/library_card.html` ×40
- `templates/partials/profile/portfolio.html` ×40
- `templates/student_login_v4.html` ×40
- `static/js/course_schedule_deck.js` ×36
- `static/js/material_selection_panel.js` ×36
- `templates/manage/system/blog_crawler.html` ×36
- `templates/partials/classroom_members/workspace.html` ×36
- `templates/partials/exams/modals.html` ×36
- `static/js/semester_calendar.js` ×34
- `templates/manage/system/academic_integrations.html` ×34
- `templates/manage/system/smart_classroom_integrations.html` ×34
- `static/js/academic_final_materials.js` ×32
- `static/js/classroom_page.js` ×32
- `templates/lesson_plan_editor.html` ×32
- `templates/manage/system/wanderers.html` ×32
- `templates/partials/lq_navbar_topbar.html` ×31
- `templates/manage/system/organizations.html` ×29
- `templates/partials/profile/email.html` ×29
- `static/js/ai_workspace_capture.js` ×28
- `static/js/dashboard.js` ×28
- `static/js/dashboard_agenda_widget.js` ×28
- `templates/assessment_plan_editor.html` ×28
- `templates/partials/learning_material_selector.html` ×28
- `templates/teacher_evaluation_editor.html` ×28
- `static/js/ai_chat_component.js` ×27
- `static/js/cultivation_panel.js` ×27
- `static/js/materials_library_cards.js` ×27
- `static/js/app_files.js` ×24
- `static/js/ls_image_lightbox.js` ×24
- `static/js/manage_organizations.js` ×24
- `static/js/signature_workflows.js` ×24
- `static/js/student_dashboard_schedule.js` ×24
- `templates/learning_path.html` ×24
- `templates/manage/ai.html` ×24
- `templates/manage/materials.html` ×24
- `templates/manage/semesters.html` ×24
- `templates/manage/student_detail.html` ×24
- `templates/manage/system/diagnostics.html` ×24
- `frontend/src/islands/exam-assign-sync.tsx` ×23
- `static/js/profile.js` ×22
- `frontend/src/islands/assignment-authoring-sync.tsx` ×21
- `static/js/signature_pad.js` ×21
- `templates/manage/system/password_resets.html` ×21
- `frontend/src/islands/classroom-workspace.tsx` ×20
- `static/js/base_resource_modes.js` ×20
- `static/js/classroom_material_list.js` ×20
- `static/js/group_assignment_config.js` ×20
- `static/js/learning_material_selector.js` ×20
- `static/js/manage_academic_integrations.js` ×20
- `static/js/manage_smart_classroom_integrations.js` ×20
- `static/js/manage_textbooks.js` ×20
- `static/js/material_viewer.js` ×20
- `static/js/resume_job_targets.js` ×20
- `templates/career_path.html` ×20
- `templates/feedback_review.html` ×20
- `templates/manage/material_hub.html` ×20
- `templates/manage/system/feedback.html` ×20
- `templates/manage/system/monitor.html` ×20
- `templates/material_render_shell.html` ×20
- `templates/partials/classroom_members/settings.html` ×20
- `templates/partials/exams/scripts.html` ×20
- `templates/partials/profile/hero.html` ×20
- `templates/partials/session_material_ai_modal.html` ×20
- `static/js/manage_semesters.js` ×19
- `templates/partials/profile/settings.html` ×19
- `templates/manage/system/gongwen_integrations.html` ×18
- `templates/manage/system/users.html` ×18
- `static/js/manage_blog_crawler.js` ×16
- `static/js/manage_gongwen_integrations.js` ×16
- `static/js/material_editors.js` ×16
- `static/js/tree_select_form_modal.js` ×16
- `templates/manage/course_schedule_editor.html` ×16
- `templates/manage/exams.html` ×16
- `templates/partials/assessment_classification_batch.html` ×16
- `templates/partials/classroom_members/alerts.html` ×16
- `templates/partials/classroom_members/exams.html` ×16
- `templates/partials/teacher_onboarding_modal.html` ×16
- `templates/resume/builder.html` ×16
- `templates/resume/home.html` ×16
- `templates/macros/manage_page.html` ×15
- `templates/partials/dashboard_agenda_widget.html` ×15
- `templates/partials/profile/messages.html` ×15
- `templates/dev/lq_interactions.html` ×14
- `templates/partials/social_entry.html` ×14
- `static/js/process_material_modal.js` ×13
- `templates/manage/system/agent_keys.html` ×13
- `static/js/agent_workbench_render.js` ×12
- `static/js/career_path_network.js` ×12
- `static/js/classroom_retake.js` ×12
- `static/js/cultivation_identity.js` ×12
- `static/js/grade_publication_controls.js` ×12
- `static/js/group_peer_eval.js` ×12
- `static/js/manage_agent_keys.js` ×12
- `static/js/manage_classes.js` ×12
- `static/js/manage_life_tips.js` ×12
- `static/js/manage_system_monitor.js` ×12
- `static/js/offering_bootstrap.js` ×12
- `static/js/resume_builder.js` ×12
- `static/js/resume_personal.js` ×12
- `static/js/resume_section.js` ×12
- `static/js/teacher_evaluation_editor.js` ×12
- `static/js/teaching_lifecycle_review.js` ×12
- `templates/manage/layout.html` ×12
- `templates/manage/life_tips.html` ×12
- `templates/partials/app_topbar_utility_actions.html` ×12
- `templates/partials/assessment_kind_control.html` ×12
- `templates/partials/semester_calendar_panel.html` ×12
- `templates/resume/personal.html` ×12
- `templates/wrong_book.html` ×12
- `templates/permission_denied.html` ×11
- `static/js/academic_sync_dialog.js` ×10
- `static/js/manage_users.js` ×10
- `static/js/whiteboard/toolbar.js` ×10
- `templates/partials/profile/security.html` ×10
- `static/js/agent_workbench.js` ×9
- `static/js/chat.js` ×9
- `templates/manage/lq_utility.html` ×9
- `templates/manage/work_inbox.html` ×9
- `templates/report_card.html` ×9
- `templates/status.html` ×9
- `static/js/assessment_plan_editor.js` ×8
- `static/js/classroom_private_messages.js` ×8
- `static/js/grade_material_preflight.js` ×8
- `static/js/manage_blog_review.js` ×8
- `static/js/manage_classrooms.js` ×8
- `static/js/manage_offering_merge.js` ×8
- `static/js/material_renderers.js` ×8
- `static/js/process_material_file_picker.js` ×8
- `static/js/submission_upload.js` ×8
- `templates/base_navbar.html` ×8
- `templates/manage/assessment_plans.html` ×8
- `templates/manage/lesson_plans.html` ×8
- `templates/manage/lq_sidebar.html` ×8
- `templates/manage/teacher_evaluations.html` ×8
- `templates/partials/profile/overview.html` ×8
- `templates/partials/student_security_modal.html` ×8
- `templates/partials/teaching_stage_bar.html` ×8
- `templates/resume/list.html` ×8
- `static/js/blog_moderation_ui.js` ×7
- `static/js/global_search.js` ×7
- `static/js/manage_courses.js` ×7
- `static/js/learning_progress.js` ×6
- `static/js/social/friends_modal.js` ×6
- `templates/dev/lq_content.html` ×6
- `templates/dev/lq_presentation.html` ×6
- `templates/session_expired.html` ×6
- `frontend/src/islands/message-center-workspace-sync.tsx` ×5
- `static/js/assessment_appeal.js` ×5
- `static/js/manage_signatures.js` ×5
- `static/js/manage_wanderers.js` ×5
- `templates/error.html` ×5
- `templates/partials/cultivation_panel.html` ×5
- `templates/student_auth_flow_v4.html` ×5
- `templates/teacher_login_v4.html` ×5
- `templates/teacher_register_v4.html` ×5
- `static/js/academic_schedule_sync.js` ×4
- `static/js/ai_workspace_history.js` ×4
- `static/js/app_exams.js` ×4
- `static/js/classroom_members.js` ×4
- `static/js/file_preview.js` ×4
- `static/js/lesson_plan_editor.js` ×4
- `static/js/process_material_filters.js` ×4
- `static/js/process_material_import_summary.js` ×4
- `static/js/resume_applications.js` ×4
- `static/js/resume_home.js` ×4
- `static/js/session_material_ai_assistant.js` ×4
- `static/js/ui_explanation.js` ×4
- `templates/base.html` ×4
- `templates/dev/lq_insights.html` ×4
- `templates/macros/app_topbar.html` ×4
- `templates/macros/ui_explanation.html` ×4
- `templates/manage/archive_pipeline.html` ×4
- `templates/manage/course_schedule.html` ×4
- `templates/manage/system/ai_usage.html` ×4
- `templates/manage/system/super_admin.html` ×4
- `templates/manage/teacher_schedule_query.html` ×4
- `templates/material_reader_unavailable.html` ×4
- `templates/partials/feedback_button.html` ×4
- `templates/partials/profile/hero_lq.html` ×4
- `templates/partials/profile_button.html` ×4
- `templates/points_shop.html` ×4
- `templates/resume/applications.html` ×4
- `templates/resume/job_targets.html` ×4
- `templates/resume/section.html` ×4
- `frontend/src/components/action-entry.tsx` ×3
- `frontend/src/islands/submission-jump-nav.tsx` ×3
- `static/js/course_schedule_editor_styles.js` ×3
- `static/js/manage_course_schedule.js` ×3
- `static/js/manage_offerings.js` ×3
- `static/js/manage_teacher_schedule_query.js` ×3
- `static/js/message_center_blog.js` ×3
- `static/js/prompt_pool.js` ×3
- `static/js/social/chat_messages.js` ×3
- `static/js/social/chat_modal.js` ×3
- `static/js/ui.js` ×3
- `static/js/user_ui_preferences.js` ×3
- `static/js/whiteboard/panels/controls.js` ×3
- `frontend/src/components/ui/dialog.tsx` ×2
- `static/js/course_schedule_changes.js` ×2
- `static/js/knowledge_tree/chrome.js` ×2
- `static/js/learning_certificate_reveal.js` ×2
- `static/js/login_scene.js` ×2
- `templates/dev/lq_centered.html` ×2
- `templates/dev/lq_data.html` ×2
- `templates/macros/user_ui_preferences.html` ×2
- `templates/partials/learning_certificate_reveal.html` ×2
- `templates/resume/layout.html` ×2
- `frontend/src/islands/dashboard-quick-actions.tsx` ×1
- `static/js/approval_workflow.js` ×1
- `static/js/course_schedule_styles.js` ×1
- `static/js/knowledge_tree/side.js` ×1
- `static/js/session_knowledge_tree_manager.js` ×1
- `static/js/social/friends_discover.js` ×1
- `static/js/social/social_dom.js` ×1
- `static/js/whiteboard/board.js` ×1
- `static/js/whiteboard/exam_board.js` ×1
- `templates/dev/lq_shell.html` ×1
- `templates/dev/lq_workspace.html` ×1

</details>

### chip

**Chip 芯片** · 层：component · CSS：`static/css/lq/components/chip.css`

- **设计摘要**：筛选/标签/状态胶囊：`kind=filter` 时是可按下的 toggle（aria-pressed），其余为只读标签；tone 六色成对填充与前景。
- **参数 / 变体**：`label, kind(label|filter), tone, size, pressed, icon, attrs`。
- **状态**：pressed / hover / focus-visible / disabled。
- **规则**：芯片是单行胶囊：不换行，过长标签以省略号截断（2026-10-24）。
- **根类**：`lq-chip`
- **Jinja 宏** `templates/macros/lq/chip.html`：`lq_chip`
- **JS 入口** `static/js/lq/components.js`：`avatar`, `badge`, `button`, `chip`, `componentMarkup`, `createComponent`, `enhanceComponents`, `html`, `progress`, `skeleton`, `spinner`
- **Python props** `classroom_app/lq_components.py`
- **React 适配器**：`LqChipGroup`（`frontend/src/components/lq-presentation.tsx`）
- **消费者**：36 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/materials_sync_report.js` ×14
- `static/js/materials_library_cards.js` ×13
- `static/js/course_schedule_deck.js` ×6
- `static/js/cultivation_panel.js` ×6
- `templates/manage/system/wanderers.html` ×6
- `static/js/materials_explorer.js` ×5
- `static/js/materials_manage.js` ×4
- `static/js/message_center.js` ×4
- `templates/dev/lq_presentation.html` ×4
- `templates/manage/system/password_resets.html` ×4
- `templates/wanderers/home.html` ×4
- `static/js/academic_schedule_sync.js` ×3
- `static/js/social/friends_discover.js` ×3
- `templates/achievements.html` ×3
- `templates/partials/materials/library_card.html` ×3
- `frontend/src/islands/classroom-workspace.tsx` ×2
- `frontend/src/islands/dashboard-workspace.tsx` ×2
- `static/js/course_schedule_changes.js` ×2
- `static/js/course_schedule_editor_styles.js` ×2
- `static/js/knowledge_tree/chrome.js` ×2
- `static/js/knowledge_tree/side.js` ×2
- `static/js/manage_classrooms.js` ×2
- `static/js/manage_filter_chips.js` ×2
- `static/js/message_center_blog.js` ×2
- `templates/partials/profile/appearance.html` ×2
- `static/js/course_schedule_styles.js` ×1
- `static/js/manage_classes.js` ×1
- `static/js/manage_gongwen.js` ×1
- `static/js/manage_wanderers.js` ×1
- `static/js/session_knowledge_tree_manager.js` ×1
- `static/js/social/chat_messages.js` ×1
- `static/js/social/chat_sidebar.js` ×1
- `static/js/social/group_files.js` ×1
- `static/js/social/social_people.js` ×1
- `static/js/social/user_card.js` ×1
- `templates/manage/work_inbox.html` ×1

</details>

### chip-row

**ChipRow 芯片行** · 层：component · CSS：`static/css/lq/components/chip-row.css`

- **设计摘要**：一组互斥或多选芯片的行容器与键盘漫游（`bindChipRow`）；真实值仍在原生 select/input。
- **根类**：`lq-chip-row`
- **Jinja 宏** `templates/macros/lq/chip-row.html`：`lq_chip_row`
- **JS 入口** `static/js/lq/chip-row.js`：`bindChipRow`, `chipRowMarkup`, `chipRowProps`, `createChipRow`
- **Python props** `classroom_app/lq_chip_row.py`
- **消费者**：10 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/manage/classes.html` ×3
- `templates/dev/lq_presentation.html` ×1
- `templates/manage/classrooms.html` ×1
- `templates/manage/courses.html` ×1
- `templates/manage/material_hub.html` ×1
- `templates/manage/offering_hub.html` ×1
- `templates/manage/system/academic_integrations.html` ×1
- `templates/manage/system/gongwen_integrations.html` ×1
- `templates/manage/system/smart_classroom_integrations.html` ×1
- `templates/manage/work_inbox.html` ×1

</details>

### collapsible

**Collapsible 折叠** · 层：component · CSS：`static/css/lq/components/collapsible.css`

- **设计摘要**：原生 details/summary 的共享外观与过渡；`lq-filter-more` 用于筛选栏「更多」。
- **规则**：保留浏览器的展开、焦点和键盘行为。 2026-10-27 社交页用 createCollapsible/enhanceCollapsible 组织申请、好友与推荐；只渲染非空分组，好友首次默认展开，更新列表保留展开状态。分组是 data-lq-surface="embedded"，不产生额外材质；summary 保留共享内边距和原生键盘/焦点。
- **根类**：`lq-collapsible`, `lq-filter-more`
- **Jinja 宏** `templates/macros/lq/collapsible.html`：`lq_collapsible`
- **JS 入口** `static/js/lq/collapsible.js`：`collapsibleMarkup`, `collapsibleProps`, `createCollapsible`, `enhanceCollapsible`, `html`
- **Python props** `classroom_app/lq_collapsible.py`
- **消费者**：8 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/cultivation_panel.js` ×10
- `static/js/dashboard.js` ×2
- `static/js/social/friends_modal.js` ×2
- `templates/dashboard.html` ×2
- `templates/dashboard_teacher.html` ×2
- `templates/dev/lq_interactions.html` ×2
- `static/js/manage_filter_chips.js` ×1
- `templates/manage/system/wanderers.html` ×1

</details>

### composer

**Composer 输入区** · 层：component · CSS：`static/css/lq/components/composer.css`

- **设计摘要**：消息/评论输入外壳：content 槽（附件/引用）+ 无框 textarea + actions（tools 槽与发送）；焦点环落在整个外壳；`enhanceComposer` 处理自动增高、Enter 策略、固定底部与键盘避让，不发请求。
- **参数 / 变体**：`label, name, value, placeholder, required, maxlength, disabled, busy, hasContent, enter(newline|send), attachment, emoji, sendLabel, submit_name/value`。
- **状态**：disabled / busy（readonly + 发送禁用）/ hasContent / data-lq-composer-fixed。
- **规则**：领域已有输入 owner（如私信）可只复用槽位结构与 CSS，不重复调用 enhanceComposer；发送、草稿、冷却由业务控制器持有。 2026-10-27 私信/群聊的 @ 元数据、按会话保存的姓名映射和文本草稿、异步加载只读态、配额提示由 social/chat_composer.js 与 chat_modal.js 持有。IME 不触发候选或发送；429 保留草稿，允许移除 @ 后发送普通消息；网络发送中不切会话。配额与授权仍以服务器为准。 2026-10-28普通AI联网按钮复用LQ Button/aria-pressed与状态文本，独立布局行不挤原生textarea；默认off，深思共用chat日额。HTTP读代次不能覆盖较新的流额度，额度失败不阻断普通聊天。
- **根类**：`lq-composer`
- **Jinja 宏** `templates/macros/lq/composer.html`：`lq_composer`
- **JS 入口** `static/js/lq/composer.js`：`COMPOSER_KINDS`, `composer`, `composerMarkup`, `composerProps`, `createComposer`, `enhanceComposer`, `html`
- **Python props** `classroom_app/lq_composer.py`
- **消费者**：5 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/classroom_main_v4.html` ×10
- `static/js/social/chat_composer.js` ×4
- `templates/partials/profile/messages.html` ×4
- `static/js/message_center_blog.js` ×3
- `templates/dev/lq_content.html` ×1

</details>

### content

**Content 内容组件（Card / List / Row / Empty / PageHead / FilterBar / Prose / Bubble）** · 层：component · CSS：`static/css/lq/components/content.css`

- **设计摘要**：数据呈现家族：Card（标题/meta/actions/body/foot 槽）、List+Row（lead/main/trail 槽，未读边、滑动动作）、Empty（reason + variant inline|card|page）、PageHead（标题/说明/说明浮窗/动作）、FilterBar（搜索 + filters/actions 槽）、Prose、Bubble（incoming/outgoing/assistant，连续气泡收紧）。
- **参数 / 变体**：card: `title, meta, variant(default|flat|stat|hero), primary, actions, value`；row: `title, meta, unread, primary, actions, swipe`；list: `label, items|groups, ordered`；empty: `reason, title, description, variant, actions`；bubble: `author, time, side, connected, datetime`。
- **状态**：row: is-unread / interactive / swipe open / busy；empty: error/forbidden 标题变危险色；bubble: connected / data-tone。
- **规则**：内容工厂不接受 raw HTML，槽位只收 Node；列表数据项不是模糊宿主；选中态用 `data-lq-selected` Surface 表达。 修炼卡（partials/cultivation_card.html）嵌在宿主卡里时传 `embedded: true`。 页头所在的普通块容器自动成为纵向堆叠（零特异性 grid + gap），页头与筛选栏、列表之间不再相贴；已自带布局的容器不受影响。
- **根类**：`lq-board`, `lq-bubble`, `lq-card`, `lq-empty`, `lq-filter-bar`, `lq-list`, `lq-page-head`, `lq-prose`, `lq-row`, `lq-scene-heading`, `lq-stat-strip`
- **Jinja 宏** `templates/macros/lq/content.html`：`lq_card`, `lq_list`, `lq_row`, `lq_empty`, `lq_page_head`, `lq_filter_bar`, `lq_prose`, `lq_bubble`
- **JS 入口** `static/js/lq/content.js`：`contentKinds`, `contentMarkup`, `contentProps`, `createContent`, `enhanceRow`, `html`
- **Python props** `classroom_app/lq_content.py`
- **React 适配器**：`LqCardFrame`, `LqEmpty`（`frontend/src/components/lq-presentation.tsx`）
- **消费者**：174 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/classroom_main_v4.html` ×40
- `static/js/blog_studio.js` ×34
- `static/js/message_center.js` ×30
- `templates/assignment_detail_teacher.html` ×23
- `templates/manage/system/blog_crawler.html` ×22
- `templates/dev/lq_content.html` ×20
- `templates/wanderers/home.html` ×18
- `templates/assignment_detail_student.html` ×15
- `templates/dashboard.html` ×15
- `templates/manage/system/wanderers.html` ×14
- `static/js/attendance_reports.js` ×13
- `templates/macros/manage_page.html` ×13
- `templates/manage/system/password_resets.html` ×13
- `templates/submission_detail.html` ×12
- `static/js/collaboration.js` ×11
- `templates/dashboard_teacher.html` ×11
- `templates/exam_editor.html` ×11
- `templates/wrong_book.html` ×11
- `templates/manage/offerings.html` ×10
- `templates/manage/student_detail.html` ×10
- `templates/partials/profile/messages.html` ×10
- `templates/assignment_wrong_summary.html` ×9
- `templates/manage/system/smart_classroom_integrations.html` ×9
- `static/js/manage_wanderers.js` ×8
- `templates/manage/courses.html` ×8
- `templates/manage/system/gongwen_integrations.html` ×8
- `templates/manage/system/monitor.html` ×8
- `frontend/src/islands/classroom-workspace.tsx` ×7
- `static/js/classroom_interactions.js` ×7
- `static/js/message_center_blog.js` ×7
- `static/js/social/social_dom.js` ×7
- `templates/dev/lq_shell.html` ×7
- `templates/exam_take.html` ×7
- `templates/learning_path.html` ×7
- `templates/manage/ai.html` ×7
- `templates/manage/me_credentials.html` ×7
- `templates/manage/semesters.html` ×7
- `templates/manage/signatures.html` ×7
- `templates/partials/materials/modals_repository.html` ×7
- `static/js/chat.js` ×6
- `static/js/classroom_private_messages.js` ×6
- `templates/feedback_review.html` ×6
- `templates/manage/academic_final_materials.html` ×6
- `templates/manage/classes.html` ×6
- `templates/manage/classrooms.html` ×6
- `templates/manage/gongwen.html` ×6
- `templates/manage/system/academic_integrations.html` ×6
- `templates/manage/system/ai_usage.html` ×6
- `templates/manage/textbooks.html` ×6
- `templates/partials/materials/modals_generate.html` ×6
- `templates/partials/materials/modals_library.html` ×6
- `templates/partials/profile/portfolio.html` ×6
- `static/js/profile_signatures.js` ×5
- `templates/blog.html` ×5
- `templates/manage/attendance_reports.html` ×5
- `templates/partials/semester_calendar_panel.html` ×5
- `templates/partials/session_material_ai_modal.html` ×5
- `templates/points_shop.html` ×5
- `static/js/blog.js` ×4
- `static/js/social/chat_messages.js` ×4
- `static/js/social/social_people.js` ×4
- `templates/achievements.html` ×4
- `templates/manage/offering_hub.html` ×4
- `templates/manage/system/diagnostics.html` ×4
- `templates/manage/system/users.html` ×4
- `templates/partials/exams/modals.html` ×4
- `templates/partials/profile/overview.html` ×4
- `classroom_app/services/document_render_service.py` ×3
- `frontend/src/islands/dashboard-workspace.tsx` ×3
- `static/js/career_path_app.js` ×3
- `static/js/classroom_material_list.js` ×3
- `static/js/course_schedule_editor.js` ×3
- `static/js/manage_assessment_plans.js` ×3
- `static/js/manage_blog_crawler.js` ×3
- `static/js/manage_blog_review.js` ×3
- `static/js/manage_lesson_plans.js` ×3
- `static/js/manage_organizations.js` ×3
- `static/js/manage_teacher_evaluations.js` ×3
- `static/js/resume_common.js` ×3
- `static/js/social/friends_discover.js` ×3
- `static/js/teacher_onboarding.js` ×3
- `templates/lessondoc_editor.html` ×3
- `templates/macros/manage_insights.html` ×3
- `templates/manage/academic_overview.html` ×3
- `templates/manage/system/agent_keys.html` ×3
- `templates/manage/teacher_schedule_query.html` ×3
- `templates/manage/work_inbox.html` ×3
- `templates/partials/academic_sync_dialog.html` ×3
- `templates/partials/classroom_members/exams.html` ×3
- `templates/partials/classroom_members/workspace.html` ×3
- `templates/partials/exams/board.html` ×3
- `templates/partials/profile/settings.html` ×3
- `templates/partials/teacher_onboarding_modal.html` ×3
- `static/js/classroom_page.js` ×2
- `static/js/course_schedule_deck.js` ×2
- `static/js/cultivation_panel.js` ×2
- `static/js/dashboard.js` ×2
- `static/js/dashboard_agenda_widget.js` ×2
- `static/js/manage_users.js` ×2
- `static/js/materials_explorer.js` ×2
- `static/js/materials_manage.js` ×2
- `static/js/resume_section.js` ×2
- `static/js/signature_point_workflow.js` ×2
- `static/js/tree_select_form_modal.js` ×2
- `templates/manage/assessment_plans.html` ×2
- `templates/manage/course_schedule.html` ×2
- `templates/manage/exams.html` ×2
- `templates/manage/lesson_plans.html` ×2
- `templates/manage/signature_workflows.html` ×2
- `templates/manage/system/organizations.html` ×2
- `templates/manage/system/super_admin.html` ×2
- `templates/manage/teacher_evaluations.html` ×2
- `templates/partials/classroom_members/settings.html` ×2
- `templates/partials/exams/control_panel.html` ×2
- `templates/partials/feedback_modal.html` ×2
- `templates/partials/materials/library_card.html` ×2
- `templates/resume/job_targets.html` ×2
- `templates/student_login_v4.html` ×2
- `static/js/academic_final_materials.js` ×1
- `static/js/app_files.js` ×1
- `static/js/base_resource_modes.js` ×1
- `static/js/classroom_polls.js` ×1
- `static/js/cultivation_identity.js` ×1
- `static/js/global_search.js` ×1
- `static/js/group_assignment_config.js` ×1
- `static/js/group_peer_eval.js` ×1
- `static/js/learning_certificate_reveal.js` ×1
- `static/js/lessondoc_wizard.js` ×1
- `static/js/manage_agent_keys.js` ×1
- `static/js/manage_course_schedule.js` ×1
- `static/js/manage_gongwen.js` ×1
- `static/js/manage_gongwen_integrations.js` ×1
- `static/js/manage_life_tips.js` ×1
- `static/js/manage_polls.js` ×1
- `static/js/manage_semesters.js` ×1
- `static/js/manage_system_monitor.js` ×1
- `static/js/manage_textbooks.js` ×1
- `static/js/material_hub.js` ×1
- `static/js/material_viewer.js` ×1
- `static/js/materials_library_cards.js` ×1
- `static/js/offering_bootstrap.js` ×1
- `static/js/process_material_modal.js` ×1
- `static/js/resume_applications.js` ×1
- `static/js/resume_builder.js` ×1
- `static/js/resume_home.js` ×1
- `static/js/resume_list.js` ×1
- `static/js/resume_personal.js` ×1
- `static/js/semester_calendar.js` ×1
- `static/js/signature_multi_select.js` ×1
- `static/js/signature_pad.js` ×1
- `templates/base_navbar.html` ×1
- `templates/error.html` ×1
- `templates/macros/user_ui_preferences.html` ×1
- `templates/manage/archive_pipeline.html` ×1
- `templates/manage/course_schedule_editor.html` ×1
- `templates/manage/life_tips.html` ×1
- `templates/manage/material_hub.html` ×1
- `templates/manage/offering_merge.html` ×1
- `templates/material_viewer.html` ×1
- `templates/partials/classroom_group_qr.html` ×1
- `templates/partials/classroom_members/overview.html` ×1
- `templates/partials/classroom_task_preview.html` ×1
- `templates/partials/cultivation_card.html` ×1
- `templates/partials/learning_certificate_reveal.html` ×1
- `templates/partials/learning_material_selector.html` ×1
- `templates/partials/student_security_modal.html` ×1
- `templates/permission_denied.html` ×1
- `templates/report_card.html` ×1
- `templates/resume/builder.html` ×1
- `templates/resume/layout.html` ×1
- `templates/session_expired.html` ×1
- `templates/status.html` ×1
- `templates/teacher_evaluation_editor.html` ×1
- `templates/teacher_login_v4.html` ×1

</details>

### cultivation-emblem

**Cultivation emblem 境界标志与道印图案身份** · 层：component · CSS：`static/css/lq/components/cultivation-emblem.css`

- **设计摘要**：既有头像境界标志由 static/js/cultivation_emblems.js 与 components/cultivation-emblem.css 提供；2026-10-27续修局部层级、阴影令牌与共享动效；保持SVG身份和宿主原定位。2026-10-27 道印大徽章在 static/img/cultivation/awards/ 扩展相同十阶段图案身份，tools/ui/build_cultivation_awards.py 生成 SVG；展示与 PNG 保存统一消费 learning_certificate_art.js 的映射，避免展示/导出两张不同图。
- **参数 / 变体**：道印顺序 qi_awakening/qi_refining/foundation/application_seed/golden_core/practical_mastery/systems_thinking/nascent_soul/independent_path/mentor_heart；兼容 self_directed 与 teacher_guiding。主色依次天蓝、青绿、青、翡翠、金、橙、靛、紫、玫红、暖金。
- **状态**：头像宿主isolation、共享raised层级、精细指针hover/键盘focus、quiet/standard/expressive/off、系统reduce零过渡、forced-colors无外部阴影；隐藏修为和SSR/动态更新保留。原头像标志状态保持；道印内容庆祝粒子 6–24、基础时长 1900–5140ms，单次有限播放；高阶段增加外环、扩散范围与多色光晕。quiet/off/reduced-motion 停止庆祝；forced-colors 隐藏装饰光效。
- **规则**：奖章 SVG/Canvas 是可导出的图形内容，不作为控件或面板材质；操作与信息区继续使用 LQ。图形色只描绘境界身份；不能将奖章特效推广成共享控件第二套动效。四项硬编码阻断已用现有令牌修复，不增加例外。除六个原登记消费者，还覆盖partials/cultivation_card.html及learning_path、profile/hero、hero_lq、overview父模板；测试tests/e2e/components/lq-cultivation-emblem.spec.ts。
- **根类**：`lq-cultivation-emblem`, `lq-cultivation-emblem-img`, `lq-cultivation-host`, `cultivation-card__emblem`
- **消费者**：8 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/cultivation_emblems.js` ×20
- `static/js/social/user_card.js` ×2
- `static/js/manage_wanderers.js` ×1
- `static/js/social/chat_sidebar.js` ×1
- `static/js/social/user_card_trigger.js` ×1
- `templates/manage/system/wanderers.html` ×1
- `templates/partials/cultivation_card.html` ×1
- `templates/wanderers/home.html` ×1

</details>

### dialogs

**Dialog / Sheet / Drawer / Confirm / Choose** · 层：component · CSS：`static/css/lq/components/dialogs.css`

- **设计摘要**：模态与非模态浮层外壳：raised Surface、scrim、头/体/脚槽；`openDialog` 与 `confirm/choose` 返回 Promise，取消不执行分支；关闭可被未保存草稿否决。
- **状态**：open / closing（可取消）/ busy 否决关闭 / nested child。
- **规则**：浮层有且只有一个 owner（LQ.layer）；退出后焦点归还有效触发器。 修为面板（partials/cultivation_panel.html + static/js/cultivation_panel.js）是领域弹层样板：壳用 lq-dialog-root/lq-scrim/lq-dialog__surface（lq-modal--xl），内容由 JS 从 /api/classrooms/{id}/learning/progress 渲染，课堂页与个人中心共用，经 createDomainModal 注册到 LQ.layer；页面层样式只在 pages/cultivation.css。 2026-10-27 好友页「+新朋友」复用二层 Dialog，取消后恢复触发器，已发申请与找人草稿由同一持久 owner 保留。道印点击后 createDialog/openDialog 创建 parentLayer 为 Viewer 的保存子层，取消只关闭子层、焦点回到道印；写入期间否决关闭与重复保存。 2026-10-27用户管理4个原生表单继续由manage_users.js显式createDomainModal托管；confirm导入别名confirmDialog区分共享确认与浏览器原生确认，未更改取消/脏草稿/忙碌合同。 2026-10-28群设置与邀请由social_dom适配共享Dialog；busy阻重复与关闭，dirty关闭用共享confirm；对话失权/切换的销毁不保留孤立子层，网络失败保留输入。 2026-10-28私聊设置遵循同一子层合同，读取失败原位重试、保存失败留稿、成功字段推进基线、防止部分成功后重复写入。
- **根类**：`lq-dialog`, `lq-dialog-root`, `lq-drawer`, `lq-modal`, `lq-popover`, `lq-scrim`, `lq-sheet`
- **Jinja 宏** `templates/macros/lq/dialogs.html`：`lq_dialog`
- **JS 入口** `static/js/lq/dialogs.js`：`choiceProps`, `choose`, `confirm`, `createDialog`, `dialogMarkup`, `dialogProps`, `disposeDialog`, `openDialog`
- **Python props** `classroom_app/lq_dialogs.py`
- **消费者**：33 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/manage/system/users.html` ×30
- `static/js/approval_workflow.js` ×9
- `static/js/manage_users.js` ×8
- `templates/partials/cultivation_panel.html` ×8
- `templates/manage/system/wanderers.html` ×7
- `templates/classroom_main_v4.html` ×6
- `static/js/social/social_dom.js` ×5
- `static/js/agent_workbench.js` ×4
- `static/js/knowledge_tree/chrome.js` ×4
- `static/js/academic_schedule_sync.js` ×3
- `static/js/session_knowledge_tree_manager.js` ×3
- `frontend/src/components/ui/dialog.tsx` ×2
- `static/js/ai_workspace_history.js` ×2
- `static/js/assessment_appeal.js` ×2
- `static/js/course_schedule_editor.js` ×2
- `static/js/knowledge_tree/editing.js` ×2
- `static/js/knowledge_tree/progress.js` ×2
- `static/js/learning_certificate_reveal.js` ×2
- `static/js/manage_teacher_schedule_query.js` ×2
- `static/js/materials_explorer.js` ×2
- `static/js/session_knowledge_tree.js` ×2
- `static/js/social/chat_sidebar.js` ×2
- `static/js/social/dm_settings.js` ×2
- `static/js/social/friends_modal.js` ×2
- `static/js/social/group_settings.js` ×2
- `static/js/social/user_card.js` ×2
- `static/js/submission_grading.js` ×2
- `static/js/blog.js` ×1
- `static/js/cultivation_panel.js` ×1
- `static/js/ls_image_lightbox.js` ×1
- `static/js/manage_wanderers.js` ×1
- `templates/dev/lq.html` ×1
- `templates/student_auth_flow_v4.html` ×1

</details>

### emoji-picker

**EmojiPicker 表情选择器** · 层：component · CSS：`static/css/lq/components/emoji-picker.css`

- **设计摘要**：贴在触发器上方的 raised 浮层（lq-glass + lq-domain-raised），常用 + 分类网格；表情格是 adoptDomainControl 的 choice 控件；出入场走 ui_overlay_motion。
- **规则**：挂载在调用方的锚点里（锚点经 `:has(> .emoji-picker)` 自动成为定位盒）；传入 `trigger` 后以触发按钮为基准测量最近裁剪祖先内的可用空间，自动上/下翻转、限高、横向夹在可视区内，因此锚点不必紧挨按钮（反馈弹窗）。材质与前景来自 raised 配对，本模块只定几何。
- **根类**：`.emoji-picker`, `.emoji-picker-item`, `.emoji-picker-grid`, `createEmojiPicker`
- **JS 入口** `static/js/emoji_picker.js`：`createEmojiPicker`
- **消费者**：5 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/emoji_picker.js` ×4
- `static/js/blog.js` ×2
- `static/js/feedback.js` ×1
- `static/js/message_center.js` ×1
- `static/js/social/chat_composer.js` ×1

</details>

### forms

**Forms 字段与原生输入** · 层：component · CSS：`static/css/lq/components/forms.css`

- **设计摘要**：`lq-field`（label / control / help / error / count）包裹原生 input / textarea / select / checkbox / radio / switch / range / file；`enhanceForms` 提供清空、计数、自动增高、首错聚焦，不接管值与校验。
- **参数 / 变体**：`id, name, label, type, value, placeholder, required, readonly, min/max/step, autocomplete, help, error, clearable, count, autoGrow, attrs`。
- **状态**：focus-visible 环 / aria-invalid 错误 / disabled 虚线 / readonly；IME composition 期间不触发派生逻辑。
- **规则**：搜索输入统一 `type=search`（图标由组件画）；迁移必须保留 id/name/type/form/校验与 input/change 次数。
- **根类**：`lq-checkbox`, `lq-choice`, `lq-control`, `lq-error-summary`, `lq-field`, `lq-form-actions`, `lq-form-section`, `lq-input`, `lq-native-color`, `lq-native-file`, `lq-radio`, `lq-range`, `lq-select`, `lq-switch`, `lq-textarea`
- **Jinja 宏** `templates/macros/lq/forms.html`：`lq_input`, `lq_textarea`, `lq_select`, `lq_checkbox`, `lq_radio`, `lq_range`, `lq_switch`, `lq_field`, `lq_form_section`, `lq_form_actions`, `lq_error_summary`
- **JS 入口** `static/js/lq/forms.js`：`checkbox`, `createForm`, `enhanceForms`, `errorSummary`, `field`, `focusFirstError`, `formActions`, `formKinds`, `formMarkup`, `formProps`, `formSection`, `html`, `input`, `nativeSelect` …
- **Python props** `classroom_app/lq_forms.py`
- **React 适配器**：`LqField`, `LqNativeInput`, `LqNativeTextarea`, `LqNativeSelect`（`frontend/src/components/lq-presentation.tsx`）
- **消费者**：178 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/classroom_main_v4.html` ×115
- `templates/partials/profile/email.html` ×88
- `templates/exam_editor.html` ×84
- `templates/assignment_detail_teacher.html` ×68
- `templates/manage/system/wanderers.html` ×62
- `templates/partials/profile/settings.html` ×62
- `static/js/collaboration.js` ×60
- `templates/manage/system/blog_crawler.html` ×58
- `static/js/teacher_onboarding.js` ×52
- `templates/manage/signatures.html` ×52
- `templates/partials/materials/modals_generate.html` ×50
- `templates/manage/classes.html` ×46
- `templates/manage/attendance_reports.html` ×38
- `templates/blog.html` ×37
- `templates/student_login_v4.html` ×36
- `templates/manage/courses.html` ×35
- `templates/manage/textbooks.html` ×35
- `templates/manage/system/organizations.html` ×34
- `static/js/manage_lesson_plans.js` ×32
- `templates/manage/academic_final_materials.html` ×32
- `templates/manage/classrooms.html` ×31
- `templates/manage/gongwen.html` ×24
- `templates/partials/profile/messages.html` ×24
- `static/js/classroom_interactions.js` ×22
- `templates/manage/life_tips.html` ×22
- `templates/manage/signature_workflows.html` ×22
- `static/js/manage_polls.js` ×21
- `templates/manage/system/academic_integrations.html` ×21
- `templates/manage/system/smart_classroom_integrations.html` ×21
- `static/js/classroom_polls.js` ×20
- `static/js/lessondoc_wizard.js` ×20
- `static/js/resume_applications.js` ×20
- `templates/manage/assessment_plans.html` ×20
- `templates/manage/lesson_plans.html` ×20
- `templates/manage/offerings.html` ×20
- `templates/manage/teacher_evaluations.html` ×20
- `templates/partials/exams/control_panel.html` ×20
- `templates/partials/materials/library_card.html` ×20
- `templates/student_auth_flow_v4.html` ×20
- `static/js/career_path_app.js` ×18
- `templates/manage/system/agent_keys.html` ×18
- `templates/dev/lq_interactions.html` ×17
- `static/js/attendance_reports.js` ×16
- `static/js/resume_builder.js` ×16
- `templates/partials/exams/modals.html` ×16
- `templates/partials/profile/security.html` ×16
- `templates/teacher_login_v4.html` ×15
- `static/js/course_schedule_editor.js` ×14
- `static/js/manage_assessment_plans.js` ×14
- `static/js/manage_teacher_evaluations.js` ×14
- `templates/exam_take.html` ×14
- `templates/partials/profile/portfolio.html` ×14
- `static/js/agent_workbench.js` ×12
- `static/js/assessment_plan_editor.js` ×12
- `static/js/grade_publication_controls.js` ×12
- `static/js/resume_section.js` ×12
- `templates/macros/user_ui_preferences.html` ×12
- `templates/manage/system/users.html` ×12
- `templates/manage/teacher_schedule_query.html` ×12
- `templates/teacher_register_v4.html` ×12
- `frontend/src/islands/dashboard-workspace.tsx` ×11
- `templates/manage/semesters.html` ×11
- `static/js/blog.js` ×10
- `static/js/dashboard_agenda_widget.js` ×10
- `static/js/group_assignment_config.js` ×10
- `static/js/semester_calendar.js` ×10
- `static/js/signature_scope_fields.js` ×10
- `templates/partials/materials/modals_library.html` ×10
- `templates/partials/materials/modals_repository.html` ×10
- `templates/partials/session_material_ai_modal.html` ×10
- `static/js/lessondoc_editor/ui.js` ×9
- `templates/manage/course_schedule.html` ×9
- `templates/manage/system/gongwen_integrations.html` ×9
- `frontend/src/islands/classroom-workspace.tsx` ×8
- `static/js/base_resource_modes.js` ×8
- `static/js/classroom_closeout.js` ×8
- `static/js/grade_material_preflight.js` ×8
- `static/js/lesson_plan_editor.js` ×8
- `static/js/material_selection_panel.js` ×8
- `static/js/materials_manage.js` ×8
- `static/js/resume_personal.js` ×8
- `static/js/teacher_evaluation_editor.js` ×8
- `static/js/teaching_lifecycle_review.js` ×8
- `templates/assignment_detail_student.html` ×8
- `templates/feedback_review.html` ×8
- `templates/learning_path.html` ×8
- `templates/manage/ai.html` ×8
- `templates/manage/materials.html` ×8
- `templates/manage/offering_hub.html` ×8
- `templates/partials/classroom_members/exams.html` ×8
- `templates/partials/feedback_modal.html` ×8
- `templates/partials/student_dashboard_schedule.html` ×8
- `static/js/assessment_appeal.js` ×7
- `static/js/social/group_settings.js` ×7
- `static/js/exam_paper_preview.js` ×6
- `static/js/manage_offering_merge.js` ×6
- `static/js/materials_explorer.js` ×6
- `static/js/resume_list.js` ×6
- `static/js/social/friends_modal.js` ×6
- `static/js/tree_select_form_modal.js` ×6
- `templates/dashboard.html` ×6
- `templates/dashboard_teacher.html` ×6
- `templates/dev/lq.html` ×6
- `templates/dev/lq_shell.html` ×6
- `templates/lessondoc_editor.html` ×6
- `templates/manage/material_hub.html` ×6
- `templates/partials/classroom_members/workspace.html` ×6
- `templates/partials/exams/scripts.html` ×6
- `templates/partials/student_security_modal.html` ×6
- `templates/resume/job_targets.html` ×6
- `templates/submission_detail.html` ×6
- `templates/wrong_book.html` ×6
- `templates/dev/lq_centered.html` ×5
- `static/js/academic_final_materials.js` ×4
- `static/js/ai_workspace_capture.js` ×4
- `static/js/blog_studio.js` ×4
- `static/js/classroom_materials.js` ×4
- `static/js/course_schedule_deck.js` ×4
- `static/js/material_viewer.js` ×4
- `static/js/offering_bootstrap.js` ×4
- `static/js/profile.js` ×4
- `static/js/profile_signatures.js` ×4
- `static/js/signature_point_workflow.js` ×4
- `static/js/signature_workflows.js` ×4
- `static/js/social/dm_settings.js` ×4
- `static/js/social/group_invite.js` ×4
- `static/js/ui_color_picker.js` ×4
- `templates/career_path.html` ×4
- `templates/partials/ai_workspace_widget.html` ×4
- `templates/partials/classroom_group_qr.html` ×4
- `templates/partials/classroom_members/settings.html` ×4
- `templates/resume/builder.html` ×4
- `static/js/manage_wanderers.js` ×3
- `static/js/session_knowledge_tree.js` ×3
- `static/js/signature_multi_select.js` ×3
- `static/js/social/friends_discover.js` ×3
- `templates/macros/manage_page.html` ×3
- `templates/partials/login_remember.html` ×3
- `templates/partials/profile/appearance.html` ×3
- `static/js/academic_sync_dialog.js` ×2
- `static/js/agent_workbench_render.js` ×2
- `static/js/app_exams.js` ×2
- `static/js/approval_workflow.js` ×2
- `static/js/blog_moderation_ui.js` ×2
- `static/js/classroom_retake.js` ×2
- `static/js/global_search.js` ×2
- `static/js/knowledge_tree/side.js` ×2
- `static/js/manage_blog_review.js` ×2
- `static/js/manage_gongwen.js` ×2
- `static/js/manage_signatures.js` ×2
- `static/js/material_editors.js` ×2
- `static/js/materials_library_cards.js` ×2
- `static/js/message_center.js` ×2
- `static/js/message_center_blog.js` ×2
- `static/js/prompt_pool.js` ×2
- `static/js/resume_common.js` ×2
- `templates/dev/lq_business.html` ×2
- `templates/dev/lq_content.html` ×2
- `templates/manage/course_schedule_editor.html` ×2
- `templates/manage/layout.html` ×2
- `templates/manage/student_detail.html` ×2
- `templates/manage/system/ai_usage.html` ×2
- `templates/manage/system/monitor.html` ×2
- `templates/manage/system/password_resets.html` ×2
- `templates/manage/system/super_admin.html` ×2
- `templates/material_viewer.html` ×2
- `templates/partials/academic_sync_dialog.html` ×2
- `templates/partials/assessment_kind_control.html` ×2
- `templates/partials/learning_material_selector.html` ×2
- `templates/partials/semester_calendar_panel.html` ×2
- `templates/resume/home.html` ×2
- `templates/resume/list.html` ×2
- `templates/resume/personal.html` ×2
- `templates/teacher_evaluation_editor.html` ×2
- `static/js/whiteboard/panels/controls.js` ×1
- `templates/dev/lq_status.html` ×1
- `templates/dev/lq_workspace.html` ×1
- `templates/manage/lq_sidebar.html` ×1

</details>

### indicators

**Indicators 徽标 / 头像 / 进度 / 加载** · 层：component · CSS：`static/css/lq/components/indicators.css`

- **设计摘要**：`lq-badge`（计数/点）、`lq-avatar`（首字或图片）、`lq-spinner`、`lq-progress`（条/环）；都是只读呈现。
- **参数 / 变体**：badge: `value, tone, dot, label`；avatar: `name, src, size(24|32|40|56)`；progress: `label, value, max, variant`。
- **规则**：放在已选中/主色按钮或已按下芯片里的 Badge 跟随宿主按钮的墨色（currentColor 18% 底），不再固定灰色。 学生头像旁的境界标志由 static/js/cultivation_emblems.js 自动挂载（components/cultivation-emblem.css），不要在页面里手写标志；首字头像加 data-cultivation-student-id，退出用 data-cultivation-emblem="off"。 2026-10-27 四种顶栏好友入口共用 data-social 钩子与 LQ Badge，数字放入口右上角；免打扰消息仍纳入未读。角标是显示状态，不能承载读取确认或权限逻辑。
- **根类**：`lq-avatar`, `lq-badge`, `lq-progress`, `lq-spinner`
- **Jinja 宏** `templates/macros/lq/indicators.html`：`lq_badge`, `lq_avatar`, `lq_spinner`, `lq_progress`
- **JS 入口** `static/js/lq/components.js`：`avatar`, `badge`, `button`, `chip`, `componentMarkup`, `createComponent`, `enhanceComponents`, `html`, `progress`, `skeleton`, `spinner`
- **Python props** `classroom_app/lq_components.py`
- **React 适配器**：`LqAvatar`（`frontend/src/components/lq-presentation.tsx`）
- **消费者**：19 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/dev/lq_presentation.html` ×10
- `classroom_app/services/document_render_service.py` ×8
- `static/js/cultivation_panel.js` ×6
- `templates/partials/lq_navbar_topbar.html` ×4
- `templates/partials/social_entry.html` ×4
- `static/js/materials_sync_report.js` ×3
- `frontend/src/components/action-entry.tsx` ×2
- `static/js/knowledge_tree/chrome.js` ×2
- `static/js/manage_blog_review.js` ×2
- `static/js/message_center.js` ×2
- `static/js/profile.js` ×2
- `static/js/social/social_dom.js` ×2
- `static/js/session_knowledge_tree_manager.js` ×1
- `static/js/social/friends_discover.js` ×1
- `static/js/social/friends_modal.js` ×1
- `templates/partials/materials/library_card.html` ×1
- `templates/partials/profile/hero_lq.html` ×1
- `templates/partials/profile/messages.html` ×1
- `templates/wanderers/home.html` ×1

</details>

### insights

**Insights 统计（AvatarStack / Ring / Bars / Meter）** · 层：component · CSS：`static/css/lq/components/insights.css`

- **设计摘要**：头像堆叠与三种小型统计图；数据色是内容槽位。
- **规则**： 六边形能力雷达用共享 partials/ability_radar.html（数据来自 ability_radar_service.build_ability_radar，样式 components/ability-radar.css），教师学生洞察与学生个人中心共用；不要再手写雷达 SVG。
- **根类**：`lq-avatar-stack`, `lq-bars`, `lq-insight`, `lq-meter`, `lq-ring`
- **Jinja 宏** `templates/macros/lq/insights.html`：`lq_avatar_stack`, `insight_ring`, `insight_bars`, `insight_meter`
- **JS 入口** `static/js/lq/insights.js`：`INSIGHT_KINDS`, `avatar_stack`, `createInsight`, `html`, `insightMarkup`, `insightProps`, `insight_bars`, `insight_meter`, `insight_ring`
- **Python props** `classroom_app/lq_insights.py`
- **消费者**：4 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/manage_wanderers.js` ×6
- `static/js/message_center_blog.js` ×3
- `static/js/blog_studio.js` ×2
- `templates/dev/lq_insights.html` ×1

</details>

### layers

**Layer 层栈** · 层：component · CSS：`static/css/lq/components/layers.css`

- **设计摘要**：`getLayerSystem()` 唯一持有层级、焦点、背景 inert、滚动锁、嵌套顺序与关闭；可移植领域弹层显式注册。
- **规则**：禁止第二个焦点栈或滚动锁。 2026-10-27 道印使用 createDomainModal(type=viewer)；颁发与修为面板中的历史查看共享同一 Viewer 控制器，空白/Escape 退出、焦点恢复、inert 与滚动锁均交给 LQ。历史查看销毁临时节点、监听器和动画，不改颁发已读状态。 2026-10-28 close 的 beforeClose 自有确认子层沿祖先推进 pending 序号，成功关闭仍返回有效触发器；无关新层打断后不再恢复旧焦点请求。覆盖新开/重开/ordered external，保留新窗口防抢焦点与取消合同，见 social-dm-sidebar-2026-10-28 和 layer-core 浏览器回归。
- **JS 入口** `static/js/lq/layer.js`：`getLayerSystem`
- **消费者**：25 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/academic_sync_dialog.js` ×2
- `static/js/ai_workspace_capture.js` ×2
- `static/js/approval_workflow.js` ×2
- `static/js/assessment_appeal.js` ×2
- `static/js/assessment_kind_controls.js` ×2
- `static/js/base_resource_modes.js` ×2
- `static/js/career_path_app.js` ×2
- `static/js/classroom_materials.js` ×2
- `static/js/classroom_page.js` ×2
- `static/js/classroom_polls.js` ×2
- `static/js/collaboration.js` ×2
- `static/js/dashboard.js` ×2
- `static/js/global_search.js` ×2
- `static/js/learning_certificate_reveal.js` ×2
- `static/js/ls_date_picker.js` ×2
- `static/js/ls_image_lightbox.js` ×2
- `static/js/process_material_modal.js` ×2
- `static/js/resume_shell.js` ×2
- `static/js/semester_calendar.js` ×2
- `static/js/session_knowledge_tree.js` ×2
- `static/js/teacher_evaluation_editor.js` ×2
- `static/js/tree_select_form_modal.js` ×2
- `static/js/ui.js` ×2
- `static/js/ui_explanation.js` ×2
- `static/js/ui_preferences_panel.js` ×2

</details>

### lightbox

**Lightbox 灯箱** · 层：component · CSS：`static/css/lq/components/lightbox.css`

- **设计摘要**：通用液态毛玻璃图片灯箱（`data-ls-lightbox` 声明式接入），私信/学习文档/AI/博客共用。
- **根类**：`lq-lightbox`
- **JS 入口** `static/js/ls_image_lightbox.js`：`bindImageLightboxDelegation`, `closeImageLightbox`, `collectLightboxGroup`, `destroyImageLightbox`, `isImageLightboxOpen`, `openImageLightbox`
- **消费者**：1 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/ls_image_lightbox.js` ×31

</details>

### menus

**Menu / Popover 菜单与浮层** · 层：component · CSS：`static/css/lq/components/menus.css`

- **设计摘要**：由 LQ.layer 托管的 portal 菜单：原生条目 + 作者内容槽，精细指针悬停、触摸点击、键盘同一 owner；`lq-popover` 为 raised 边界采样。
- **规则**：菜单条目不套按钮胶囊；关闭成功后才执行领域动作。 2026-10-27 群成员管理改为行尾右对齐的 ellipsis 入口，createMenu/bindMenu 把菜单 portal 到共享层栈，桌面点击与触摸同一 owner。列表重绘或侧栏销毁必须释放绑定；权限与破坏性动作确认仍由领域持有，不能由隐藏菜单代替服务端授权。 2026-10-27材料卡片/资源管理器通过materials_menus.js共用createMenu/bindMenu；只传业务动作与权限，不重建portal/焦点/定位。原生上传菜单在原始点击激活内调用input.click，后续原生动作由共享菜单退出后回放，禁止重复提交。
- **根类**：`lq-menu`
- **Jinja 宏** `templates/macros/lq/menus.html`：`lq_menu`
- **JS 入口** `static/js/lq/menus.js`：`bindMenu`, `createMenu`, `menuMarkup`, `menuProps`, `resolveMenuActionTrigger`
- **Python props** `classroom_app/lq_menu_tooltip.py`
- **消费者**：12 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/assignment_detail_teacher.html` ×10
- `templates/manage/exams.html` ×6
- `templates/manage/materials.html` ×6
- `templates/macros/app_utility_menus.html` ×3
- `templates/macros/classroom_menus.html` ×3
- `static/js/academic_schedule_sync.js` ×2
- `static/js/materials_menus.js` ×2
- `static/js/social/chat_sidebar.js` ×2
- `static/js/ui_explanation.js` ×2
- `templates/dev/lq_interactions.html` ×1
- `templates/exam_take.html` ×1
- `templates/partials/exams/control_panel.html` ×1

</details>

### nav-menu

**NavMenu 导航菜单** · 层：component · CSS：`static/css/lq/components/nav-menu.css`

- **设计摘要**：顶栏「更多 / 个人」等触发器 + Menu 组合；窄屏保留完整可点击宽度。
- **根类**：`lq-nav-menu`
- **Jinja 宏** `templates/macros/lq/nav-menu.html`：`lq_nav_menu`
- **JS 入口** `static/js/lq/nav-menu.js`：`createNavMenu`, `enhanceNavMenus`, `navMenuMarkup`, `navMenuProps`
- **Python props** `classroom_app/lq_nav_menu.py`
- **消费者**：6 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/partials/lq_navbar_topbar.html` ×3
- `static/js/classroom_page.js` ×2
- `static/js/dashboard.js` ×2
- `templates/macros/app_utility_menus.html` ×2
- `templates/macros/classroom_menus.html` ×2
- `templates/partials/dashboard_schedule_tools.html` ×1

</details>

### navigation

**Navigation Tabs / Segment** · 层：component · CSS：`static/css/lq/components/navigation.css`

- **设计摘要**：`lq-tabs`（可滚动、键盘漫游、aria-selected）与 `lq-segment`（分段控件）；路由与选中项由领域持有。 横向标签列表被相邻高元素拉伸时，标签保持自身高度并垂直居中，胶囊不会被撑成圆球（2026-10-24）。
- **根类**：`lq-segment`, `lq-tabs`
- **Jinja 宏** `templates/macros/lq/navigation.html`：`lq_tabs`, `lq_segment`
- **JS 入口** `static/js/lq/navigation.js`：`createNavigation`, `createSegment`, `createTabs`, `html`, `navigationMarkup`, `navigationProps`, `segment`, `tabs`
- **Python props** `classroom_app/lq_navigation.py`
- **消费者**：20 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/manage/system/wanderers.html` ×18
- `static/js/collaboration.js` ×6
- `static/js/classroom_interactions.js` ×4
- `templates/assignment_wrong_summary.html` ×4
- `templates/classroom_main_v4.html` ×4
- `templates/dev/lq_interactions.html` ×4
- `templates/feedback_review.html` ×4
- `templates/learning_path.html` ×4
- `templates/partials/profile/appearance.html` ×3
- `static/js/dashboard.js` ×2
- `static/js/manage_wanderers.js` ×2
- `static/js/resume_common.js` ×2
- `static/js/semester_calendar.js` ×2
- `static/js/social/friends_modal.js` ×2
- `templates/blog.html` ×2
- `templates/manage/attendance_reports.html` ×2
- `templates/manage/signature_workflows.html` ×2
- `templates/partials/classroom_members/workspace.html` ×2
- `templates/partials/feedback_modal.html` ×2
- `templates/manage/system/blog_review.html` ×1

</details>

### overflow-label

**OverflowLabel 固定宽度长标签** · 层：component · CSS：`static/css/lq/components/overflow-label.css`

- **设计摘要**：`bindOverflowLabels(owner)`：仅精细指针 hover / 键盘 focus-visible 时测量一次，确有溢出才做一次内部文字 transform。
- **JS 入口** `static/js/lq/overflow-label.js`：`bindOverflowLabels`
- **消费者**：1 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/course_schedule_editor.js` ×2

</details>

### search

**Search 搜索包装** · 层：component · CSS：`static/css/lq/components/search.css`

- **设计摘要**：`.lq-search` 只清除包装层底色并提供尾部槽（清空/提交）；放大镜由 `.lq-input[type=search]` 自己绘制，避免框中框。 2026-10-27局部isolation防止尾部raised动作跨越邻接面板；所有现有12个消费者保留原生输入，实际带尾部动作的试卷/材料筛选共用此实现。
- **规则**：不要再给搜索框外面画一层框或自放图标。 不覆盖共享粗指针44px命中高度；清空动作仍发送一次input，提交使用原生form。验证：tests/e2e/components/lq-search-stacking.spec.ts。
- **根类**：`lq-search`
- **消费者**：12 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/manage/academic_final_materials.html` ×2
- `templates/partials/exams/control_panel.html` ×2
- `templates/partials/materials/library_card.html` ×2
- `static/js/materials_explorer.js` ×1
- `templates/manage/classes.html` ×1
- `templates/manage/classrooms.html` ×1
- `templates/manage/courses.html` ×1
- `templates/manage/gongwen.html` ×1
- `templates/manage/material_hub.html` ×1
- `templates/manage/offering_hub.html` ×1
- `templates/manage/semesters.html` ×1
- `templates/manage/textbooks.html` ×1

</details>

### selection

**Selection / Dropdown 选择列表** · 层：component · CSS：`static/css/lq/components/selection.css`

- **设计摘要**：平台 select 的共享呈现：`data-lq-dropdown` 由 Dropdown 接管展示，原生 select 继续独占值、表单与校验；combobox/listbox 用于可搜索选择。
- **参数 / 变体**：`data-lq-dropdown`、`data-lq-selection-owner`（已有 owner）、`data-lq-dropdown-manual`（React 显式生命周期）、`searchable`。
- **规则**：同一 select 只能有一个呈现 owner；动态领域 owner 调 `enhanceDropdowns(root)`。
- **根类**：`lq-dropdown`, `lq-selection`
- **Jinja 宏** `templates/macros/lq/selection.html`：`lq_combobox`, `lq_listbox`
- **JS 入口** `static/js/lq/selection.js`：`bindSelection`, `createSelection`, `selectionKinds`, `selectionMarkup`, `selectionOptions`, `selectionProps`
- **JS 入口** `static/js/lq/dropdown.js`：`bindDropdown`, `dropdownResults`, `enhanceDropdowns`, `getDropdown`, `installDropdowns`
- **Python props** `classroom_app/lq_selection.py`
- **消费者**：9 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/course_schedule_editor_styles.js` ×2
- `static/js/dashboard.js` ×2
- `static/js/message_center.js` ×2
- `static/js/semester_calendar.js` ×2
- `static/js/student_dashboard_schedule.js` ×2
- `static/js/ui_preferences_panel.js` ×2
- `templates/dev/lq_data.html` ×2
- `static/js/signature_multi_select.js` ×1
- `templates/classroom_main_v4.html` ×1

</details>

### skeleton

**Skeleton 骨架屏** · 层：component · CSS：`static/css/lq/components/skeleton.css`

- **设计摘要**：加载占位：形状 + 行数；reduced-motion 下静止。
- **根类**：`lq-skeleton`
- **Jinja 宏** `templates/macros/lq/skeleton.html`：`lq_skeleton`
- **JS 入口** `static/js/lq/components.js`：`avatar`, `badge`, `button`, `chip`, `componentMarkup`, `createComponent`, `enhanceComponents`, `html`, `progress`, `skeleton`, `spinner`
- **Python props** `classroom_app/lq_components.py`
- **消费者**：22 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/academic_schedule_sync.js` ×2
- `static/js/agent_workbench.js` ×2
- `static/js/agent_workbench_render.js` ×2
- `static/js/attendance_reports.js` ×2
- `static/js/course_schedule_changes.js` ×2
- `static/js/knowledge_tree/chrome.js` ×2
- `static/js/knowledge_tree/side.js` ×2
- `static/js/login_scene.js` ×2
- `static/js/manage_polls.js` ×2
- `static/js/manage_teacher_schedule_query.js` ×2
- `static/js/manage_wanderers.js` ×2
- `static/js/message_center.js` ×2
- `static/js/message_center_blog.js` ×2
- `static/js/session_knowledge_tree_manager.js` ×2
- `static/js/social/chat_messages.js` ×2
- `static/js/social/chat_sidebar.js` ×2
- `static/js/social/friends_discover.js` ×2
- `static/js/social/group_files.js` ×2
- `static/js/social/social_dom.js` ×2
- `static/js/social/social_people.js` ×2
- `static/js/social/user_card.js` ×2
- `templates/dev/lq_presentation.html` ×1

</details>

### status

**Status / SaveStatus / Alert / Conflict** · 层：component · CSS：`static/css/lq/components/status.css`

- **设计摘要**：业务状态呈现：状态胶囊、保存状态行、Alert（含重试动作与诊断转义）、409 冲突面板；只消费控制器确认的快照。
- **规则**：警告/错误/保存语义优先复用这里的填充与前景配对，不把有色前景放到任意透明背景。
- **根类**：`lq-alert`, `lq-conflict`, `lq-save-status`, `lq-status`
- **Jinja 宏** `templates/macros/lq/status.html`：`lq_status`, `lq_save_status`, `lq_alert`, `lq_conflict`
- **JS 入口** `static/js/lq/status.js`：`STATUS_KINDS`, `alert`, `conflict`, `createStatus`, `html`, `saveStatus`, `save_status`, `status`, `statusMarkup`, `statusProps`
- **Python props** `classroom_app/lq_status.py`
- **React 适配器**：`LqStatus`（`frontend/src/components/lq-presentation.tsx`）
- **消费者**：22 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/dev/lq_status.html` ×9
- `templates/lessondoc_editor.html` ×6
- `templates/manage/system/users.html` ×6
- `static/js/knowledge_tree/chrome.js` ×5
- `static/js/social/friends_discover.js` ×4
- `static/js/social/group_invite.js` ×4
- `static/js/learning_certificate_reveal.js` ×3
- `static/js/session_knowledge_tree.js` ×3
- `static/js/social/dm_settings.js` ×3
- `static/js/social/group_settings.js` ×3
- `frontend/src/islands/classroom-workspace.tsx` ×2
- `frontend/src/islands/dashboard-workspace.tsx` ×2
- `templates/classroom_main_v4.html` ×2
- `templates/manage/teacher_schedule_query.html` ×2
- `static/js/assessment_appeal.js` ×1
- `static/js/blog.js` ×1
- `static/js/chat.js` ×1
- `static/js/lessondoc_editor/ui.js` ×1
- `static/js/manage_users.js` ×1
- `static/js/materials_explorer.js` ×1
- `templates/partials/learning_certificate_reveal.html` ×1
- `templates/partials/profile/hero_lq.html` ×1

</details>

### tables

**Table / Pager / BulkBar / ResultCount** · 层：component · CSS：`static/css/lq/components/tables.css`

- **设计摘要**：数据表外壳（sticky 表头、行选中）、分页器、批量操作栏与结果计数。
- **根类**：`lq-bulk-bar`, `lq-pager`, `lq-result-count`, `lq-table`, `lq-table-shell`
- **Jinja 宏** `templates/macros/lq/tables.html`：`lq_table`, `lq_pager`, `lq_bulk_bar`, `lq_result_count`
- **JS 入口** `static/js/lq/tables.js`：`createTable`, `enhanceTable`, `html`, `tableKinds`, `tableMarkup`, `tableProps`
- **Python props** `classroom_app/lq_tables.py`
- **React 适配器**：`LqPager`（`frontend/src/components/lq-presentation.tsx`）
- **消费者**：11 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/dev/lq_data.html` ×7
- `static/js/course_schedule_editor_styles.js` ×5
- `static/js/attendance_reports.js` ×3
- `static/js/course_schedule_changes.js` ×3
- `frontend/src/islands/dashboard-workspace.tsx` ×2
- `static/js/academic_schedule_sync.js` ×2
- `static/js/manage_classrooms.js` ×2
- `static/js/manage_gongwen.js` ×2
- `templates/dev/lq_shell.html` ×2
- `templates/manage/gongwen.html` ×1
- `templates/manage/system/password_resets.html` ×1

</details>

### toast

**Toast 通知** · 层：component · CSS：`static/css/lq/components/toast.css`

- **设计摘要**：raised 填充 + thick 实时模糊（栈上限 3），有 shell 顶栏时下移；图标为成对 tone 小胶囊。
- **规则**：全站通过 `LQ.toast` / `showToast` 发出；不自建 toast 容器。 2026-10-27 社交聊天浮窗唯一归 SocialClient：message_notification_policy.js 识别 category=social 且 metadata.event_type（兼容 event）为 social_message/social_mention；React message-center-sync 与旧 message_center_bell 继续更新总未读但不重复弹聊天。免打扰仅抑制该会话浮窗，保留消息中心与好友角标；当前可见会话抑制浮窗，其他会话仍可提示；WS 重复消息去重；刷新铃铛通过 900ms 合并并传 allowPopup:false，避免请求风暴。好友申请不在聊天排除规则内。
- **根类**：`lq-toast`, `lq-toast-fallback`, `lq-toasts`
- **JS 入口** `static/js/lq/toast.js`：`createToastClock`, `getToastSystem`, `toast`, `toastProps`
- **消费者**：4 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/ui.js` ×3
- `static/js/materials_explorer.js` ×2
- `static/js/message_center_bell.js` ×2
- `static/js/social/social_dom.js` ×2

</details>

### tooltips

**Tooltip 提示** · 层：component · CSS：`static/css/lq/components/tooltips.css`

- **设计摘要**：悬停/聚焦提示，不采样模糊；全站功能说明浮窗另见 ui explanation popover。
- **根类**：`lq-tooltip`
- **Jinja 宏** `templates/macros/lq/tooltips.html`：`lq_tooltip`
- **JS 入口** `static/js/lq/tooltips.js`：`bindTooltip`, `createTooltip`, `tooltipMarkup`, `tooltipProps`
- **Python props** `classroom_app/lq_menu_tooltip.py`
- **消费者**：2 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `static/js/cultivation_emblems.js` ×2
- `templates/dev/lq_interactions.html` ×1

</details>

### upload

**Upload / Dropzone / FileChip** · 层：component · CSS：`static/css/lq/components/upload.css`

- **设计摘要**：上传队列呈现：拖放区、文件芯片与 FILE_STATES；队列、413 恢复、提交由业务控制器持有。
- **根类**：`lq-dropzone`, `lq-file-chip`, `lq-upload`
- **Jinja 宏** `templates/macros/lq/upload.html`：`lq_upload`, `lq_dropzone`, `lq_file_chip`
- **JS 入口** `static/js/lq/upload.js`：`FILE_STATES`, `UPLOAD_KINDS`, `bindUpload`, `createUpload`, `uploadItem`, `uploadMarkup`, `uploadProps`, `uploadSnapshot`
- **Python props** `classroom_app/lq_upload.py`
- **消费者**：1 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/dev/lq_business.html` ×9

</details>

### workspace

**Workspace Split / Viewer** · 层：component · CSS：`static/css/lq/components/workspace.css`

- **设计摘要**：可拖分栏与文档查看器外壳；iframe/表单原位，移动端操作可达。 2026-10-27 破境道印沿用 LQ Viewer 层管理，主体是透明圆形奖章内容槽，不绘制另一套玻璃面板。铭文使用 raised Surface，交互道印使用共享 Choice/Button 焦点态，保存用子 Dialog；LQ 持有外壳动效，奖章内容内部仅有限 transform/opacity 庆祝。
- **根类**：`lq-domain-workspace`, `lq-split`, `lq-viewer`
- **Jinja 宏** `templates/macros/lq/workspace.html`：`lq_split`, `lq_viewer`
- **JS 入口** `static/js/lq/workspace.js`：`createWorkspace`, `enhanceSplit`, `html`, `workspaceKinds`, `workspaceMarkup`, `workspaceProps`
- **Python props** `classroom_app/lq_workspace.py`
- **消费者**：2 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/dev/lq_workspace.html` ×2
- `templates/partials/ai_workspace_widget.html` ×1

</details>

### shells

**Shells 页面壳（Topbar / Sidebar / Dock / Editor / PageLayout / FAB / Crumbs / Steps）** · 层：composite · CSS：`static/css/lq/components/shells.css`

- **设计摘要**：AppShell 家族：导航壳、编辑器壳、底部 dock（`enhanceDock` 测量高度令牌）、浮动操作入口、面包屑与步骤条。
- **规则**：FAB 复用 `.lq-fab` 定位与 slot 合同；禁止另做 resize/scroll 测量循环。 2026-10-27修复新增好友入口后的1024–1279管理壳换行：从水平留白收回空间，保留全部11个入口与44px命中区、侧栏展开及原换行兜底；pilot/family同一CSS，8个真实页头两宽度与18项adapter回归。
- **根类**：`lq-crumbs`, `lq-dock`, `lq-editor`, `lq-fab`, `lq-nav-item`, `lq-page-layout`, `lq-scroll-edge`, `lq-shell-pane`, `lq-shell-slot`, `lq-sidebar`, `lq-sidebar-root`, `lq-steps`, `lq-topbar`
- **Jinja 宏** `templates/macros/lq/shells.html`：`lq_shell`, `lq_topbar`, `lq_sidebar`, `lq_dock`, `lq_editor`, `lq_page_layout`, `lq_nav_item`, `lq_fab`, `lq_crumbs`, `lq_steps`
- **JS 入口** `static/js/lq/shells.js`：`createShell`, `enhanceDock`, `enhanceShell`, `html`, `shellKinds`, `shellMarkup`, `shellProps`
- **Python props** `classroom_app/lq_shells.py`
- **消费者**：10 个文件

<details><summary>消费者清单（按命中次数）</summary>

- `templates/manage/lq_sidebar.html` ×16
- `templates/partials/lq_navbar_topbar.html` ×13
- `templates/dev/lq_shell.html` ×8
- `static/js/social/friends_discover.js` ×3
- `static/js/manage_lq_pilot.js` ×2
- `static/js/navbar_lq.js` ×2
- `static/js/report_card.js` ×2
- `templates/manage/lq_topbar.html` ×1
- `templates/partials/ai_workspace_widget.html` ×1
- `templates/partials/lq_app_bottomnav.html` ×1

</details>

## 页面适配层（static/css/lq/pages）

| 页面 CSS | 链接它的模板 | 并入 index.css |
|---|---|---|
| `static/css/lq/pages/calendar.css` | （仅 index.css 或未直接链接） | 是 |
| `static/css/lq/pages/centered.css` | `templates/base_centered.html` | 否 |
| `static/css/lq/pages/certificate-reveal.css` | （仅 index.css 或未直接链接） | 否 |
| `static/css/lq/pages/cultivation.css` | （仅 index.css 或未直接链接） | 是 |
| `static/css/lq/pages/dashboard.css` | （仅 index.css 或未直接链接） | 是 |
| `static/css/lq/pages/growth.css` | （仅 index.css 或未直接链接） | 是 |
| `static/css/lq/pages/life-tip.css` | （仅 index.css 或未直接链接） | 是 |
| `static/css/lq/pages/login.css` | `templates/base_centered.html` | 否 |
| `static/css/lq/pages/manage-archive.css` | （仅 index.css 或未直接链接） | 是 |
| `static/css/lq/pages/message-center.css` | `templates/partials/profile/head.html`, `templates/partials/profile/messages.html` | 否 |
| `static/css/lq/pages/navbar.css` | `templates/base_navbar.html` | 否 |
| `static/css/lq/pages/page-material.css` | （仅 index.css 或未直接链接） | 是 |
| `static/css/lq/pages/profile-signatures.css` | `templates/partials/profile/head.html` | 否 |
| `static/css/lq/pages/profile.css` | `templates/partials/profile/head.html` | 否 |
| `static/css/lq/pages/social.css` | `templates/partials/social_entry.html` | 否 |
| `static/css/lq/pages/status.css` | `templates/base_centered.html` | 否 |
| `static/css/lq/pages/wanderer-home.css` | `templates/wanderers/home.html` | 否 |
| `static/css/lq/pages/wanderers.css` | `templates/manage/system/wanderers.html` | 否 |

## 局限

- 源码扫描只证明「引用了模块的类/宏/工厂」，不证明运行时合同与业务验收；视觉与交互仍需浏览器证据。
- 后端拼接的 HTML 字符串、动态类名与第三方 iframe 内容可能漏扫；新增模块时在 notes 里登记 extraSelectors。
