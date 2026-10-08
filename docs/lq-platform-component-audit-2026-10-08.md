# 全平台组件源码审计基线（2026-10-08）

此报告是源码入口清单，不是组件完成率或页面验收证明。所有记录仍为 pending/unknown；JSON 保存逐条文件、行号、类名、来源、建议 owner 与源文件 SHA256。

扫描 520 个作者源码文件，覆盖 201 个 HTML 模板，发现 5302 个组件/控件候选入口。共享定义和调用分别登记；互斥模板分支均计入，不能作为运行时控件总数。

| 类别 | 源码入口数 |
|---|---:|
| button | 2207 |
| field | 737 |
| select | 299 |
| surface | 659 |
| menu | 34 |
| dialog | 22 |
| tab | 31 |
| choice | 163 |
| toolbar | 133 |
| layer | 139 |
| content-slot | 45 |
| handle | 1 |
| chip | 8 |
| domain | 18 |
| status | 44 |
| unknown | 762 |

| 来源 | 数量 |
|---|---:|
| component-declared | 3696 |
| external-icon-content-slot | 34 |
| factory-ownership-unknown | 20 |
| legacy-or-native | 92 |
| lq-marked-native | 112 |
| runtime-output-unknown | 633 |
| shared-adapter-call-candidate | 81 |
| shared-component-call | 582 |
| shared-definition | 52 |

## 本轮审查与动态入口解释

详见 [需求、模块与人工分类](lq-interface-modules-2026-10-08.md)、[共享模板完整审查](lq-interface-source-review-2026-10-08.json) 及 [源码与合同摘要](lq-platform-component-summary-2026-10-08.json)。源码声明仍为 pending，不能替代浏览器验收。

本次增量覆盖已批准调课的可视历史端点、同一课次后续待审调课、课次重排展示与 Git 学习文档绑定结果；历史浏览器结果及迁移验收状态保留。正式构建图为 `52d9070c0fd971673d59343c26ffdbc9f5aa4873fc49bc5e3b0b9362fc2c77ad`，增量检查单列在 source-review 的 `scheduleBindingsReview`。

本次后续增量覆盖教室查询页与 3D 课表编辑器的空闲查询状态、分页和当前教室未知状态说明；使用共享 Pager、Button 与既有选值 owner，无新增私有材质。当前构建图 `2dcac58147515a2c9021d34109035f3dc675797511c415fa0e06e2ae08ac833d`，独立记录于 source-review 的 `classroomFreeQueryReview`；上轮浏览器证据原样保留。

| unknown 语法 | 数量 |
| --- | ---: |
| `create-element` | 30 |
| `dynamic-html-sink` | 633 |
| `jinja-component-call` | 43 |
| `jinja-html` | 14 |
| `react-jsx` | 42 |

## 动画过程专项审查

当前正式资源图 `e335ac7d8d73a4ec2c15c45702a6a5bf48b21bba1c8febe1537739dcbeb6d18e`。201 个 HTML 模板与 290 个非压缩领域 JS 的显隐候选、继承关系和例外清单保留在 [动效源码清单](lq-motion-source-inventory-2026-10-08.json)；自然中间帧和真实业务验证见 [动效过程验收](lq-motion-process-acceptance-2026-10-08.md)。源码声明、只读页面扫描和领域业务验收分别记录，所有候选仍保留 pending/unknown，历史证据不删除。

## 页面台账缺口

原台账 190 模板/208 条，内存重建为 201 模板/223 条；原文件及手工验收状态未更改。
常规页面模板根 78 个；生成器发现 95 个页面方法路径，另显式注册的个人中心页面 7 个需补充。异常响应文档另计。

- 新增台账身份：`GET /manage/academic/course-schedule/editor::manage/course_schedule_editor.html`
- 新增台账身份：`GET /student/login/identity::student_auth_flow_v4.html`
- 新增台账身份：`GET /student/password/forgot::student_auth_flow_v4.html`
- 新增台账身份：`POST /student/login/identity::student_auth_flow_v4.html`
- 新增台账身份：`POST /student/password/forgot::student_auth_flow_v4.html`
- 新增台账身份：`POST /student/password/setup::student_auth_flow_v4.html`
- 新增台账身份：`template::macros/app_utility_menus.html`
- 新增台账身份：`template::macros/classroom_menus.html`
- 新增台账身份：`template::macros/lq/nav-menu.html`
- 新增台账身份：`template::partials/ai_workspace_assets.html`
- 新增台账身份：`template::partials/ai_workspace_mount.html`
- 新增台账身份：`template::partials/dashboard_schedule_tools.html`
- 新增台账身份：`template::partials/lq_page_backdrop.html`
- 新增台账身份：`template::partials/profile/appearance.html`
- 新增台账身份：`template::partials/profile/hero_lq.html`
- 动态入口：`GET /manage/me` → `templates/manage/profile.html`
- 动态入口：`GET /manage/me/appearance` → `templates/manage/profile.html`
- 动态入口：`GET /manage/me/email` → `templates/manage/profile.html`
- 动态入口：`GET /manage/me/notifications` → `templates/manage/profile.html`
- 动态入口：`GET /manage/me/private` → `templates/manage/profile.html`
- 动态入口：`GET /manage/me/security` → `templates/manage/profile.html`
- 动态入口：`GET /manage/me/settings` → `templates/manage/profile.html`
- 异常文档：`templates/error.html`
- 异常文档：`templates/session_expired.html`
- 异常文档：`templates/material_reader_unavailable.html`

## 按源码入口数量排序

| 文件 | 候选入口 |
|---|---:|
| `templates/classroom_main_v4.html` | 222 |
| `templates/assignment_detail_teacher.html` | 172 |
| `static/js/materials_manage.js` | 117 |
| `templates/exam_editor.html` | 109 |
| `static/js/collaboration.js` | 102 |
| `static/js/blog.js` | 97 |
| `templates/blog.html` | 73 |
| `templates/manage/signatures.html` | 67 |
| `static/js/teacher_onboarding.js` | 67 |
| `templates/manage/courses.html` | 61 |
| `templates/manage/system/blog_crawler.html` | 61 |
| `templates/partials/materials/modals_generate.html` | 61 |
| `static/js/attendance_reports.js` | 61 |
| `templates/dashboard_teacher.html` | 56 |
| `templates/manage/classes.html` | 56 |
| `static/js/course_schedule_editor.js` | 56 |
| `static/js/career_path_app.js` | 55 |
| `templates/dashboard.html` | 54 |
| `templates/manage/system/users.html` | 53 |
| `static/js/manage_lesson_plans.js` | 49 |
| `static/js/message_center.js` | 49 |
| `templates/dev/lq_interactions.html` | 47 |
| `templates/manage/attendance_reports.html` | 46 |
| `templates/submission_detail.html` | 46 |
| `templates/manage/textbooks.html` | 45 |

## 穷尽覆盖门禁

1. 路由快照与真实 app.routes 对齐；直接、add_api_route、重定向、异常响应和动态片段分别登记，未知路由不能默认为通过。
2. 逐个源码入口确认共享组件、域组件或有理由的例外；只加类名/颜色的桥接仍为 pending。域 owner 候选必须人工确认，不能按路径自动接受。
3. 对全部页面根生成授权角色×320/390/768/1024/1440宽度×浅/深色的基础可达性与控件盘点；隐藏、空数据、禁用、繁忙、错误、权限、冲突、弹层/iframe等状态由领域场景补齐。
4. 运行时所有 button/field/select/surface/menu/dialog/tab/choice/toolbar 必须映射 source occurrence 或共享工厂及领域 owner；动态 DOM 新增控件也纳入，unknown 不可吞掉。
5. 组件合同验证默认/悬停/按下/键盘/选中/禁用/繁忙、关闭否决、焦点归还、草稿与表单归属、20次开关资源清理；交互只动画transform/opacity并尊重reduced-motion。
6. 建立来源SHA、组件合同SHA、浏览器场景结果三者绑定的验收记录；源码变化使相关记录失效，不覆盖旧报告为全绿。
7. 相同场景比较渲染宿主、事件耗时、长任务、布局偏移与请求数量；CSS继承或静态类命中不能代替真浏览器证据。

## 边界

- Static lexical inventory, not a JavaScript interpreter or a full JSX/Jinja execution engine. Unresolved dynamic tags/factories remain unknown.
- Class names, inherited CSS, and data-lq attributes are provenance hints only; none imply component or business acceptance.
- JS strings with UI markup remain candidates unless proven to be plain-text sinks; dynamically assembled markup and runtime-only API content still require DOM inventory.
- Source occurrences cannot enumerate data-dependent instance counts, authorization branches, native popups, shadow DOM, portal/iframe states, or generated server HTML.
- Pair the source inventory with the isolated runtime route/DOM/state matrix; all pending and unknown records need an owner, contract, and scenario or an explicit reviewed exception.
