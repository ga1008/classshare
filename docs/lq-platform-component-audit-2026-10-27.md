# 全平台组件源码审计快照（2026-10-27）

此报告是源码入口清单，不是组件完成率或页面验收证明。所有记录仍为 pending/unknown；JSON 保存逐条文件、行号、类名、来源、建议 owner 与源文件 SHA256。

扫描 579 个作者源码文件，覆盖 208 个 HTML 模板，发现 5618 个组件/控件候选入口。共享定义和调用分别登记；互斥模板分支均计入，不能作为运行时控件总数。

| 类别 | 源码入口数 |
|---|---:|
| button | 2252 |
| field | 760 |
| select | 305 |
| surface | 741 |
| menu | 40 |
| dialog | 31 |
| tab | 37 |
| choice | 207 |
| toolbar | 136 |
| layer | 137 |
| content-slot | 39 |
| handle | 1 |
| chip | 30 |
| domain | 18 |
| status | 53 |
| unknown | 831 |

| 来源 | 数量 |
|---|---:|
| component-declared | 3726 |
| external-icon-content-slot | 28 |
| factory-ownership-unknown | 49 |
| legacy-or-native | 139 |
| lq-marked-native | 131 |
| runtime-output-unknown | 690 |
| shared-adapter-call-candidate | 81 |
| shared-component-call | 720 |
| shared-definition | 54 |

## 页面台账缺口

原台账 203 模板/225 条，内存重建为 208 模板/230 条；原文件及手工验收状态未更改。
常规页面模板根 82 个；生成器发现 99 个页面方法路径，另显式注册的个人中心页面 7 个需补充。异常响应文档另计。

- 新增台账身份：`GET /manage/system/wanderers::manage/system/wanderers.html`
- 新增台账身份：`GET /wanderers/{slug}::wanderers/home.html`
- 新增台账身份：`template::partials/ability_radar.html`
- 新增台账身份：`template::partials/cultivation_panel.html`
- 新增台账身份：`template::partials/login_remember.html`
- 新增台账身份：`template::partials/social_entry.html`
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
| `templates/classroom_main_v4.html` | 215 |
| `templates/assignment_detail_teacher.html` | 173 |
| `templates/exam_editor.html` | 108 |
| `static/js/blog.js` | 107 |
| `static/js/collaboration.js` | 102 |
| `static/js/materials_manage.js` | 91 |
| `templates/manage/system/wanderers.html` | 77 |
| `templates/manage/signatures.html` | 67 |
| `static/js/teacher_onboarding.js` | 67 |
| `static/js/attendance_reports.js` | 66 |
| `templates/blog.html` | 65 |
| `templates/manage/courses.html` | 61 |
| `templates/manage/system/blog_crawler.html` | 61 |
| `templates/partials/materials/modals_generate.html` | 61 |
| `static/js/course_schedule_editor.js` | 59 |
| `templates/dashboard_teacher.html` | 56 |
| `templates/manage/classes.html` | 56 |
| `static/js/approval_workflow.js` | 56 |
| `static/js/career_path_app.js` | 55 |
| `templates/dashboard.html` | 54 |
| `static/js/message_center.js` | 54 |
| `templates/manage/attendance_reports.html` | 49 |
| `templates/submission_detail.html` | 49 |
| `static/js/manage_lesson_plans.js` | 49 |
| `templates/dev/lq_interactions.html` | 47 |

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

## 本轮限定验收记录：社交与道印

设计与业务边界见 [本轮方案](social-awards-redesign-2026-10-27.md)，模块消费者见 [LQ 记录本](lq-module-registry.md)，源文件/合同 SHA256 与命令结果见 [结构化审计摘要](lq-platform-component-summary-2026-10-27.json) 的 `scopedReview`。本节不更改上面 4787 项 pending、831 项 unknown，也不将源码覆盖率当作业务通过率。

正式资源图 `81b15e1acb4c59a57dbab94c7e8ec8557117c7fc63c5a5e09ceda45ed7ed80d1` 下，好友/群聊 8 项及道印 9 项 Playwright 场景通过。覆盖两标签与空分组、二层找人取消/焦点、群成员权限菜单与触摸、@ 配额留稿、免打扰未读、铃铛去重、十阶段艺术、透明图片保存、嵌套收藏取消、历史查看不改颁发状态、减少动效和窄屏。通知策略/数据规范化 6 项、道印映射/导出 3 项单元测试通过；既有社交前端合同/隐私 27 项通过；TypeScript 检查通过。

实际查看了 360px 深色好友/找人/群菜单截图、桌面亮色菜单，以及十级道印总览、桌面亮暗和 360px 道印截图。本地证据目录为 `.codex-temp/social-redesign/`、`.codex-temp/award-reveal-e2e-final/` 与 `.codex-temp/cultivation-awards-preview/`。两个菜单合成场景各观测到 2 个 backdrop 宿主（对话框与 portal 菜单）、零长任务；桌面为 59 次资源/9 次 API 请求，窄屏为 57 次资源/7 次 API 请求。这些值只描述本次场景，不代表全平台性能结论。

| 门禁 | 本轮结果 |
|---|---|
| `npm run inventory:lq-modules` | 完成：40 模块、18 页面适配层、341 消费者文件 |
| `npm run check:lq-modules` | 通过，无生成物漂移 |
| `npm run check:lq-size` | 通过，正式图源哈希一致；18,179 / 18,432 gzip 字节 |
| `npm run lint:lq` | 仍失败：7 项既有阻断，3660 项警告；不新增豁免 |
| `npm run probe:lq-layout` | 未运行全站探针；本轮只做上述正式构建定向浏览器验收 |

lint 未决项为 `templates/manage/materials.html` 与 `templates/manage/system/users.html` 的既有审核哈希失效，`components/cultivation-emblem.css` 第 14/21/27/32 行的 z-index/图形颜色命中，以及 `components/search.css` 第 66 行 z-index。四个文件相对 HEAD `87cbc34c8057f21c1749cb31154b38a9b2b982d2` 无差异；已只读提取 HEAD 对照，原始规则同位置命中、两个审核值在 HEAD 也不匹配。CRLF 差异单独归一化核对，未覆盖工作区或刷新审核哈希。逐项证据写入摘要 `checks.lint:lq.baselineProof`。

浏览器服务、HTTP/WS 和证书载荷均为隔离合成数据，不连接真实业务库、SMTP 或 AI。此验收不是全站角色×宽度×配色矩阵，生产上线核验与 Git 推送须另行完成。页面台账既有缺口及所有未决状态继续保留。
