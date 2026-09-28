# 全平台组件源码审计基线（2026-09-27）

此报告是源码入口清单，不是组件完成率或页面验收证明。所有记录仍为 pending/unknown；JSON 保存逐条文件、行号、类名、来源、建议 owner 与源文件 SHA256。

扫描 510 个作者源码文件，覆盖 198 个 HTML 模板，发现 4517 个组件/控件候选入口。共享定义和调用分别登记；互斥模板分支均计入，不能作为运行时控件总数。

| 类别 | 源码入口数 |
|---|---:|
| button | 2186 |
| field | 729 |
| select | 301 |
| surface | 490 |
| menu | 90 |
| dialog | 173 |
| tab | 93 |
| choice | 141 |
| toolbar | 127 |
| unknown | 187 |

| 来源 | 数量 |
|---|---:|
| factory-ownership-unknown | 20 |
| legacy-or-native | 3715 |
| lq-marked-native | 242 |
| shared-component-call | 490 |
| shared-definition | 50 |

## 页面台账缺口

原台账 190 模板/208 条，内存重建为 198 模板/220 条；原文件及手工验收状态未更改。
常规页面模板根 78 个；生成器发现 95 个页面方法路径，另显式注册的个人中心页面 7 个需补充。异常响应文档另计。

- 新增台账身份：`GET /manage/academic/course-schedule/editor::manage/course_schedule_editor.html`
- 新增台账身份：`GET /student/login/identity::student_auth_flow_v4.html`
- 新增台账身份：`GET /student/password/forgot::student_auth_flow_v4.html`
- 新增台账身份：`POST /student/login/identity::student_auth_flow_v4.html`
- 新增台账身份：`POST /student/password/forgot::student_auth_flow_v4.html`
- 新增台账身份：`POST /student/password/setup::student_auth_flow_v4.html`
- 新增台账身份：`template::macros/lq/nav-menu.html`
- 新增台账身份：`template::partials/ai_workspace_assets.html`
- 新增台账身份：`template::partials/ai_workspace_mount.html`
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
| `templates/classroom_main_v4.html` | 234 |
| `templates/assignment_detail_teacher.html` | 151 |
| `static/js/collaboration.js` | 96 |
| `templates/exam_editor.html` | 93 |
| `static/js/materials_manage.js` | 81 |
| `templates/blog.html` | 73 |
| `templates/manage/signatures.html` | 63 |
| `static/js/blog.js` | 59 |
| `templates/manage/courses.html` | 58 |
| `static/js/teacher_onboarding.js` | 57 |
| `templates/manage/classes.html` | 53 |
| `templates/partials/materials/modals_generate.html` | 53 |
| `templates/dashboard.html` | 51 |
| `templates/dashboard_teacher.html` | 50 |
| `templates/manage/system/users.html` | 47 |
| `templates/dev/lq_interactions.html` | 46 |
| `templates/manage/attendance_reports.html` | 46 |
| `templates/manage/system/blog_crawler.html` | 45 |
| `static/js/manage_lesson_plans.js` | 45 |
| `templates/manage/academic_final_materials.html` | 43 |
| `templates/manage/textbooks.html` | 43 |
| `static/js/career_path_app.js` | 43 |
| `templates/manage/classrooms.html` | 42 |
| `templates/submission_detail.html` | 41 |
| `frontend/src/islands/dashboard-workspace.tsx` | 40 |

## 穷尽覆盖门禁

1. 路由快照与真实 app.routes 对齐；直接、add_api_route、重定向、异常响应和动态片段分别登记，未知路由不能默认为通过。
2. 逐个源码入口确认共享组件、域组件或有理由的例外；只加类名/颜色的桥接仍为 pending。域 owner 候选必须人工确认，不能按路径自动接受。
3. 对全部页面根生成授权角色×320/390/768/1024/1440宽度×浅/深色的基础可达性与控件盘点；隐藏、空数据、禁用、繁忙、错误、权限、冲突、弹层/iframe等状态由领域场景补齐。
4. 运行时所有 button/field/select/surface/menu/dialog/tab/choice/toolbar 必须映射 source occurrence 或共享工厂及领域 owner；动态 DOM 新增控件也纳入，unknown 不可吞掉。
5. 组件合同验证默认/悬停/按下/键盘/选中/禁用/繁忙、关闭否决、焦点归还、草稿与表单归属、20次开关资源清理；交互只动画transform/opacity并尊重reduced-motion。
6. 建立来源SHA、组件合同SHA、浏览器场景结果三者绑定的验收记录；源码变化使相关记录失效，不覆盖旧报告为全绿。
7. 相同场景比较渲染宿主、事件耗时、长任务、布局偏移与请求数量；CSS继承或静态类命中不能代替真浏览器证据。

## 边界

2026-09-29 调课变更清单的追加清点、共享 Table/Chip/Button/Popover 组合和运行时验收见 [专项记录](lq-schedule-changes-2026-09-29.md) 与 [最新源码摘要](lq-schedule-changes-source-summary-2026-09-29.json)。该追加记录不把其他页面的 pending/unknown 自动改为通过。

- Static lexical inventory, not a JavaScript interpreter or a full JSX/Jinja execution engine. Unresolved dynamic tags/factories remain unknown.
- Class names, inherited CSS, and data-lq attributes are provenance hints only; none imply component or business acceptance.
- JS strings with UI markup remain candidates unless proven to be plain-text sinks; dynamically assembled markup and runtime-only API content still require DOM inventory.
- Source occurrences cannot enumerate data-dependent instance counts, authorization branches, native popups, shadow DOM, portal/iframe states, or generated server HTML.
- Pair the source inventory with the isolated runtime route/DOM/state matrix; all pending and unknown records need an owner, contract, and scenario or an explicit reviewed exception.
