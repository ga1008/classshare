# S3 预审与实施边界

预审核对日期：2026-09-20。基准 HEAD：`23fd77e050a54e57b12e70b2c742a36536ae9142`；同时读取当前工作区源码，不能以 HEAD 代表尚未提交的 S2 产物。依据 `docs/liquid-glass-execution-plan-2026-09.md` K4/K12、S3、§17–20，以及真实模板、路由、controller、测试源码。下文第1–9节保留实施前的只读调查；其中“尚未实施”“不存在”和“需要验收”是当时状态。S3现已开始施工，当前实现和验证以第10节及 `lq-acceptance.md` 为准，不能将预审清单视作通过记录。

## 1. 收敛建议与进入条件

1. 试点 A 选课程、班级、课堂运行总台、学期、教材、教案、资料、教师账号八个管理列表；共享宏/壳接线与页内业务 controller 分开。先保留业务 DOM、权限和提交路径，再验收编辑、删除、失败，不能把“页面头换了”记成整页迁完。
2. 试点 B 选学生 `/report-card`。它按当前学生 ID 读取成绩投影；`/achievements` 打开时会评定、补发并提交成就，不是所需的只读试点。
3. S3 文件清单存在计数歧义：S3 行只命名 **5 条业务 spec + 1 条 layout**，K12/出口写 **6 + 1**。本轮负责人已补定第六条为 `grading-return-resubmit.spec.ts`，覆盖退回→新轮次重交→旧页拒绝，与单轮双教师CAS区分；这是本轮决策，原计划文本仍有计数缺项。
4. 最小顺序：纯模板兼容测试与回退开关 → A 的共享呈现接线 → 八页有界业务验收 → B 的单路由壳/主题验证 → 考试三函数模块提取及版本门禁 → 七条 spec 收口。S2 本地工程出口补齐前不进入 S3 产品修改；正式负责人签字单独登记，不将文档签字升级为新的用户许可门槛。用户已有按计划实施授权。

测试执行以后仍须走独占合成环境/规范隔离入口。`docs/lq-test-isolation-incident-2026-09-20.md` 中历史数据影响未关闭，修复入口不等于数据恢复；本提案没有连接该库，也不授权清理或恢复。

## 2. 三宏：真实覆盖与兼容缺口

源码：`templates/macros/manage_page.html`；目标复用：`templates/macros/lq/content.html`、`classroom_app/lq_content.py`、`static/js/lq/content.js`。

只读 Jinja AST 统计得到 **39 个业务模板调用 `page_head`，`filter_bar` / `empty_state` 业务调用均为 0**（不将宏自身定义/注释计为消费者）。所以三宏都应有兼容 fixture，但不能宣称已有三宏的全站真实业务覆盖。

| 宏 | 原接口和 DOM 契约 | 直接委托新宏的缺口 / 最小适配 |
|---|---|---|
| `page_head(title, description, explain, explain_label, actions, eyebrow, title_id)` | `header.page-head[data-page-head]`；`.page-head__copy/__eyebrow/__desc/__aside/__actions`；`.manage-pagehead__title-row`；h2/title_id；原 explanation；零参数 caller 是 aside；action 原生 anchor/button，id 保留 | 新 `lq_page_head` 已兼容零参数 aside caller，但 helper 对 eyebrow 仅校验且不渲染；计划§10.5明确 eyebrow 参数保留但不再填充，与新 helper 一致；四个真实非空消费者要登记这一有意变化，不能把消失误报为实现bug。动作 variant/attrs 需有限映射，不能丢 action id、href、原生 button type 或可访问名称 |
| `filter_bar(search_id, search_placeholder, search_attrs='')` | **div** `.filter-bar[data-filter-bar]`；label/sr-only；原生 search input、autocomplete off；caller 在 controls | 新实现默认 **form**，不能意外嵌套 form 或将 Enter 变成提交；兼容 wrapper 明确保持 div，或在逐页接线时证明 form owner。搜索 ID/值/change 真源不迁给 chip |
| `empty_state(title, description, action_label, action_href, action_attrs='')` | `.page-empty[data-page-empty]`；strong/p；有 href 是 anchor，否则 button | 适配到 `lq_empty` 时明确 reason，初始空和筛选无结果不同；接口失败必须 error，不能伪零数据。旧 title 和动作名不能被默认 reason 文案盖掉 |

计划§10.5明示停填 eyebrow，故不建议为S3擅自改回组件行为；若其文案承担必要业务区分，由该页显式放入title/description并逐页评审。四个非空 eyebrow 消费者：`manage/academic_final_materials.html`（期末材料 · 教务系统闭环）、`manage/attendance_reports.html`（教务归档）、`manage/system/gongwen_integrations.html`（OFFICIAL DOCUMENTS）、`manage/system/smart_classroom_integrations.html`（SMART CLASSROOM）。它们不是八页试点，但属于共用宏变更的回归样本。

安全边界：旧宏 `action.attrs/search_attrs/action_attrs` 接受可信模板字符串并 `safe`；新 LQ 只接结构化属性。**39 个实际 page_head 调用中没有非空 `action.attrs`**，另外两宏没有业务调用。因此当前只需 `'' → {}` 的旧默认兼容；不应为不存在的消费者新增通用 HTML 属性 parser 或 rawHTML 旁路。独立的旧 `topbar_action` 确有 `data-*` 字符串，它通过作者定义的 `header_actions` 槽保留，不能和 page_head 属性需求混为一谈。

三入口要求在这里是“语义与行为一致”，不能把已经带监听/输入值的 caller 重建为 HTML 字符串。Jinja 作者槽、JS Node 槽与安全 HTML 工厂按 S2 契约复用；节点先完整验证，再移动，不能部分失败后留半棵树。

### 开关建议（尚未实施）

采用一个服务端、默认关闭、只控制呈现的试点配置；路由白名单对应八页和 `/report-card`，不是权限开关，不接受浏览器任意 query/header 自行启用。模板上下文传 `lq_pilot_enabled`。

现有多处 `from ... import page_head` 不带 `with context`，不能假设路由变量自动进入宏。最小方式是在旧宏增加**可选且默认 false 的显式参数**，八个调用点明确传入；其他 31 个模板维持旧输出。filter/empty 先补双分支 fixture，有真实逐页调用时再显式启用。layout 的新旧 partial 由同一个上下文开关选择。这比依赖全局可变请求状态、一次修改全部 import 或 CSS 根选择器假装切换 DOM 更容易回退。

若决定让 39 个 page_head 同时切换，应将全部 39 个列入本批 SSR 契约，而不是仍以“八页抽查”声称无其他消费者；四 eyebrow 的计划性停填、说明按钮和 caller 至少需要明确覆盖。

## 3. manage/layout：必须保留的入口与状态所有权

真实链：`classroom_app/routers/ui_parts/common.py::_build_manage_template_context` → `manage_nav_service.build_manage_nav` → `templates/manage/layout.html`。服务端已经给出六域、超管第七域、active domain/item、组、搜索词和帮助信息；不在前端另造权限/导航表。隐藏入口不是 API 鉴权。

| 层 | 真实契约 | 最小接线 / 风险 |
|---|---|---|
| 根与资产 | 完整 HTML、S1 root/body 属性、`lq_editor_head`、extra_head/body_class/content/scripts blocks；user_info/current_teacher_is_super_admin/manage_nav | 保持语言、viewport、偏好 SSR、资源顺序及脚本入口。新局部壳仅使用现有 tokens/materials，不改账户偏好 |
| 侧栏 | `#sidebar`、`#sidebarCollapseBtn`、`#manageNavSearch`、`#manageNav`、`#manageNavEmpty`；`.manage-nav-domain[data-manage-domain][data-nav-domain]`；toggle aria-expanded/controls；`.manage-nav-item.active`；`data-nav-text`、`explain_attrs` | 保留 canonical href、六域单开、首页特殊链接、组标题、帮助说明。提取 partial 时直接消费后端 payload；不得把已退役 workflow 放回来 |
| 顶栏 | `.manage-topbar.manage-page-<active_page>`；品牌/首页/域/页名；`.manage-context-actions` 中 `header_actions`；`partials/app_topbar_utility_actions.html`；归档步骤 | 原生链接、操作按钮、消息和外观入口各保留单实例。只能对同一批试点收纳布局，不替换所有学生/教师顶栏 |
| controller | layout 内 `initManageSidebar`：搜索临时展开、Escape 清搜索、desktop collapsed、mobile 外点关闭；localStorage `lanshare:manage-sidebar-collapsed`；移除旧 open-groups key；active item 滚入视野 | 新旧 sidebar enhancer只能一个 owner。旧阈值 `<=768`，S2 壳响应式阈值不能无说明覆盖旧页；先登记试点断点，测试 ±1。旧 storage 迁移如需兼容必须显式 adapter，不升级成跨账户的新全局状态 |
| 导航动效 | `.manage-nav-item[href], .manage-context-actions a[href]` 点击拦截；保留修饰键/新窗/hash/reduced-motion；150ms leave；pageshow 复位 | 不与新 navigate/dirty guard 并行绑定；一次点击一次导航。回退恢复原 listener owner，不能叠加两套延迟 |
| 表单桥 | `window.apiFetch/showMessage/handleFormSubmit`；FormData、原按钮 innerHTML/busy、失败提示、成功后延迟 reload | 宏不得用 loading 替换输入或丢 name/form/fieldset disabled。S3不擅改成功 reload 为新业务状态缓存，也不改 API 接口 |
| embedded | `embedded_mode` 是完整文档；省略外壳/AI/islands；保留 manage-content 与脚本；同源 `manage-embed-height` + ResizeObserver | 保留 iframe，不能把完整文档当 fragment 注入。课堂配置 iframe 原 src/load/保存刷新检测保留；任意子文档不注入主题/键盘 |

现成 Shell 的原位 pane 会校验祖先 transform、裁切、stacking 等（`shells.js::safeHost`），不能假设把现有 `#sidebar` 直接传入就可用。实施前要用真实 host 验证；不通过时调整**该试点壳的结构/祖先**或保留旧呈现，不能无效堆 z-index。不可为绕过限制将表单、禁用 fieldset 子树或 iframe 克隆/portal 搬走。

## 4. 试点 A：八页覆盖表

八页都是真实列表，不把 `/manage/teaching/offerings` 配置表单冒充列表；后者作为课堂运行总台的编辑子流程。下面是将来验收的最小集合，不代表本轮已执行。

| 页面 / 真实呈现 | 权限与业务入口 | 必留 DOM / JS | 编辑、删除、失败门禁 |
|---|---|---|---|
| **课程** `/manage/library/courses`，卡片 | `manage_pages_library.py` scoped rows；`manage_parts/common.py::_ensure_teacher_can_manage_course`：共享可用不等于可管理，仅创建者/超管可编辑；`classes_courses_courses.py` save/delete | `manage/courses.html`；`manage_courses.js` + base_resource_modes/lessondoc_wizard；`#courseSearchInput/#courseFilterSelect/#courseCardGrid/#courseEmptyState/#courseModal/#courseNameInput`；`data-filter-target="#courseFilterSelect"` | 编辑名称/课次后读回；共享卡无编辑且直调 API 拒绝；删除取消与引用阻断；保存 4xx/5xx保留草稿、busy解除、一次重试一次请求；筛选无结果可清 |
| **班级** `/manage/teaching/classes`，卡片+学生抽屉 | `manage_pages_teaching.py` 按学校/owner/任课关联可见；can_manage=owner或超管，任课可查看不应升级管理；`classes_courses_classes.py` | `manage/classes.html`；`manage_classes.js`；`#classList/#classSearchInput/#classDepartmentFilter/#classHealthFilter/#classSortSelect/#classCreateForm/#classStudentDrawer`；initial/filter empty分离 | 合成班级创建/编辑、抽屉关闭返回同卡/筛选；删除取消和失败保留列表；学生编辑/状态操作仍依原权限。本批不顺带改导入协议或批量改真实学生 |
| **课堂运行总台** `/manage/teaching/classroom-hub`，SSR 卡片 | `offering_hub_service.build_offering_hub_context`；`classes_courses_offerings.py` 编辑/删除；删除 SQL 强制 `teacher_id=user.id`，active grade publication返回409，不推断超管可越过 | `manage/offering_hub.html`；`manage_offering_hub.js`；`#offeringHubList/SearchInput/SemesterFilter/StatusFilter/SortSelect/ResultCount`（各为完整 offeringHub 前缀 ID）；`[data-offering-card]`、edit-config/delete-offering；第二个真实 filter-chips消费者；`#offeringHubEditDrawer/#offeringHubDrawerFrame` | iframe编辑配置并读回；原保存刷新以 frame load次数检测；取消不能写。删除自有合成课堂/已有公布409/他人403；失败不删卡、不清筛选。保留同源高度消息与iframe状态，不迁独立开课编辑器 |
| **学期** `/manage/teaching/semesters`，列表+modal | `semesters_textbooks.py`；共用学期可用不等于可维护；`_ensure_teacher_can_manage_semester`；同校学期身份复用、calendar sync状态 | `manage/semesters.html`；`manage_semesters.js`；`#heroSemesterCreateBtn/#semesterSearchInput/#semesterList/#semesterListEmpty/#semesterForm/#semesterIdInput/#semesterStartInput/#semesterEndInput/#semesterSubmitBtn/#semesterSyncCurrentBtn` | 编辑日期范围/native验证/删除引用阻断；sync进行中409保留输入；取消/失败/重试不能重复同步。测试需替身隔离校历同步边界，不产生外部教务/模型任务 |
| **教材** `/manage/library/textbooks`，卡片+附件表单 | 同系/范围可复用；owner/超管 can_manage；`semesters_textbooks.py` save/delete + `build_textbook_delete_blockers` | `manage/textbooks.html`；`manage_textbooks.js`；`#textbookCardGrid/#textbookSearchInput/#textbookPublisherFilter/#textbookTagFilter/#textbookAttachmentFilter/#textbookForm/#textbookAuthorsJsonInput/#textbookTagsJsonInput/#textbookRemoveAttachmentInput` | 编辑书名/作者/标签/本地合成附件后读回；失败保留 File/JSON/移除标记；有课堂引用删除受阻，取消不请求；不用真实 AI 格式化服务 |
| **教案** `/manage/library/lesson-plans`，异步列表 | `routers/lesson_plans.py` owner/超管管理与公开范围只读/继承分开；list GET会处理stale任务，不能把所有管理GET称为无副作用 | `manage/lesson_plans.html`；`manage_lesson_plans.js`；`[data-lp-root/search/filter-scope/filter-school/filter-college/filter-course/filter-class/grid/empty/loading/summary]`（实际属性均 data-lp-*）；`#lesson-plan-boot`；create/import/generate topbar hooks | 只做列表属性/标签编辑、删除取消/成功/失败；他人只读不露管理；首次/重试load失败不能显示零教案。生成/导出/独立编辑器样式不进入本批；不重建summary/boot节点 |
| **资料** `/manage/library/materials`，SSR 主体+React island启动旧controller | `materials_parts/library.py` + node_ops + permission service；owner/组织scope/资源mode和版本边界保留；学生不能管理 | `manage/materials.html`；`frontend/src/islands/materials-manage-page.tsx` → **`materials_manage.js`**；`data-materials-manage-page-app/data-lanshare-island="materials-manage-page"/data-island-id`；`#materials-manage-page-main/#materials-file-input`、`p03-*`、分类rail/页头隐藏stats | 临时本地资料上传、属性编辑、delete-impact后删除取消/失败；版本409保留现状；只读/限制资源直接API仍拒绝。保留island host、挂载guard、file input对象；不把同controller同时作为script再载一次 |
| **教师账号** `/manage/system/users`，native table+modals | `manage_pages_admin.py::_ensure_manage_super_admin`；`manage_parts/system_config.py` 每个账号API `_require_current_super_admin`；delete是deactivate，保留历史教学数据 | `manage/system/users.html` 内联controller；`#teacher-create-form/#teacher-search/#teacher-status-filter/#teacher-table-body/#teacher-empty-state/#teacher-edit-modal/#teacher-membership-modal/#teacher-password-modal`；`[data-teacher-row][data-id]` / `[data-action]` | 合成账号创建/编辑、停用确认/取消/失败；普通教师403、学生无入口；自己的disabled控制不能因工厂改为可点；不触碰真实管理员身份/口令。成员关系与授权弹层保留原业务controller |

各页均应保留 0/1/多项、长中文/英文、初始空/筛选空/错误三种区别。纯筛选使用现有原生 select 为真值；courses 和 offering_hub 的 chips 继续 `data-filter-target`、一次 change、幂等 init/destroy，不引入第二个选择数组。为证明删除/失败而设置的路由拦截只覆盖有界合成 ID，不能 mock 掉整套权限服务后声称鉴权已验收。

### 已有可复用测试（源码存在，本轮未运行）

- `tests/e2e/specs/teacher-app-shell.spec.ts`：六域、超管第七域、canonical重定向、资料分类rail仍在页内、移动侧栏及学生边界；`tests/test_manage_nav_service.py` 还直接断言旧 layout 字符串。提取 partial 同一提交应更新读取位置/渲染断言，不删除业务断言。
- `tests/e2e/specs/materials.spec.ts`：资料页/临时上传/学生拒绝；最终成绩材料导入生成属于既有场景，不因本试点额外开展真实导出。`home-classroom-business.spec.ts` 已有资料选中、select all、返回保留和受限下载拒绝。
- `tests/e2e/specs/system-permissions.spec.ts`：超管成功、普通教师和学生拒绝；尚不能替代账号新增/编辑/停用的成功与失败路径。
- `tests/test_base_resource_modes_service.py`、`test_semester_identity_service.py`、`test_lesson_plan_service.py`、`test_permission_materials_service.py`、`test_materials_postgres_writes.py` 是相应业务基础。原生 PG 文件只按其独立隔离入口运行，不顺带连默认库。
- 八页尚无同一组 page-head/actions/empty/filter 完整浏览器等价矩阵；应新增一个 S3 pilot 专属参数化 spec，复用既有 P03合成fixture和登录工具，不复制八套服务种子。

## 5. 试点 B：学生成绩单

源码：`routers/report_card.py`（HTML与JSON两条路由）、`services/student_report_card_service.py::build_student_report_card`、`templates/report_card.html`。

事实依据：

- `_ensure_student` 只允许 student；student ID 取当前身份，不由客户端任意指定。页面参数仅 assessment_kind/class_offering_id；服务读取 submissions/score projection/已公布成绩，不在该调用链提交业务写入。
- 成绩投影以 `student_view=True, include_content=False` 读取；小组未公布内容不能进入本人/同伴统计；个人阶段试炼和正式课程评定分开。页面的零分判断使用 `is not none`，不能套 badge“零隐藏”。
- 模板继承 `base_navbar`，有筛选链接和 aria-current、课程卡/表、`.report-chart[data-report-chart]` 与 JSON数据节点；ECharts读取既有序列。迁移对象是此路由的 topbar壳、呈现/主题、默认图表字和线，不改变分数/排名算法、数据series含义或公开范围。
- `routers/achievements.py` 的 `/achievements` 与 `/api/achievements` 调用评定补发并 `conn.commit()`；所以不采用“成长页天然只读”的假设。

最小实现时，仅 report-card 路由显式选用新学生 topbar partial；base_navbar其余页面保持旧分支。保留返回/消息/外观/当前导航和匿名会话处理；不得以该试点顺便迁全站 Dock、个人中心或所有成长页。

验收：本人含0分/无成绩/1条/长课程、多条图表；筛选链接保留另一个参数；未公布小组成绩从HTML/JSON/图表源均不可见；教师拒绝、学生不能通过请求他人ID读取；亮/暗/off和六palette（实际 teal/indigo/sky/mint/violet/rose），1440/390、键盘、200%重排，图表不整页溢出。检查成绩、提交和公布快照不写入，图表主题切换不产生额外业务请求；如提取图表controller，resize/dispose只各一次。旧学生基类的修为GET可能刷新其独立投影，不能仅凭未出现POST宣称整页没有数据库写入。

已有 `test_student_report_card_service.py` 四例覆盖时间线/均值分位、序列对齐、同伴身份不泄露、无成绩；`test_score_projection_service.py` 直接覆盖0分、null区别、未公布小组不进入成绩单/同伴统计、课程和个人阶段分离。缺专属学生成绩单实页browser门禁；服务单测不能代替壳主题可读性。

## 6. S3 “六 + 一”现存文件、复用与缺项

| 计划名称（目标均在 `tests/e2e/specs/`） | 当前文件 | 最小场景 / 可复用来源 |
|---|---|---|
| `exam-authoring.spec.ts` | **不存在** | 真实试卷编辑/保存/读回、长题型与校验、失败保留、已有作答时边界、非owner。`expected_revision` 业务若未交付，明确依赖K9独立票，不能为绿测试去掉并发要求 |
| `assignment-student-draft.spec.ts` | **不存在** | 本地/服务器草稿恢复、附件未完成/失败、409停止写且答案保留、退回新轮次；保留 `assignment-submission.spec.ts` 的真实提交→教师行/详情闭环与 p03钩子 |
| `exam-take.spec.ts` | **不存在** | 手动交卷、截止竞态、一个结果、失败不清草稿、mobile主入口和输入；与draft版本Node门禁分层，不能仅替身响应证明真实提交 |
| `grading-concurrency.spec.ts` | **不存在** | **现有 `manual-grade-revisions.spec.ts` 已有3个真实服务场景**：双tab旧版本拒绝保留/小数/零；标准改变使旧页失效；学生及他人教师拒绝。复用或提取共享case并保持既有注册，避免复制后重复制造业务记录；补断言一次有效结果/通知边界需明确可观测点 |
| `wrong-summary.spec.ts` | **不存在** | 按本人/课堂scope读取错题归集、空/加载/失败分离、修订后归集正确、不泄露他人答案；实际路由/controller需在该票开始时定点登记，不能以UI静态卡作完成 |
| **第六条：`grading-return-resubmit.spec.ts`** | **原计划未命名；本轮负责人已定名，文件待新增** | 批改退回→学生新轮次重交→旧作答页版本拒绝、已评结果不能串轮次；与grading-concurrency单轮双教师CAS区分。退回/新轮次数据仍由真实业务service产生，不在浏览器假造版本 |
| `layout-stability.spec.ts` | **不存在** | 固定fixture CLS≤0.05；busy/字体/顶栏收缩不漂移；页面高度/遮挡。计划的学生首页≤4200px属于首页场景，S3先建立测量基线，**不把此门槛强套report-card或为达标提前改S4首页** |

当前全 `tests/e2e/**/*.spec.ts` 检索未见 `PerformanceObserver`/CLS/4200 断言；原 `home-classroom-ui-v3.spec.ts` 有交互/响应式，不等于布局稳定门禁。应在导航前注入 buffered layout-shift observer，等待明确字体/数据就绪窗口，再分别记录初载、busy、响应断点；不能通过忽略关键布局段人为清零。height场景要锁定合成数据规模和视口，不把服务器数据多少算成CSS变化。

既有 `assignment-classification-modal.spec.ts` 的保存/取消、失败不丢值、防重复、并发冲突、迟到响应不污染重开，以及 `teacher-review-ai.spec.ts` 的成功/取消迟到回调/他人拒绝继续保留。计划提到旧“四条作业链”，现应按验收台账登记其**实际文件清单**，不能凭历史总数量删除任何现有 spec 或 p03 testid。

七个文件名并不自动证明S3通过：新增的是有明确业务端点/角色/资源/数据隔离的场景；缺后端契约的条目应保持待办依赖，不能在S3呈现票内暗改业务或扩成S5页面重构。

## 7. exam_draft_version import 改造的准确入口

当前 `tests/frontend/exam_draft_version.test.cjs:1–14` 从 `templates/exam_take.html` 读字符串，以固定4空格/右花括号正则抽取 `performServerDraftSave`、`loadServerDraft`、`handleSubmission`，再 `vm.runInContext`。当前 `static/js/exam_take/` 尚无目录；计划 §13.3/K12 指向的新生产模块是 `static/js/exam_take/submit.js`。

生产函数位置（本次读取）：`loadServerDraft` 约2103行、`performServerDraftSave` 2208行、`handleSubmission` 3587行；固定 `SUBMISSION_VERSION` 约1731行；外层 `saveServerDraft` 2202行拥有 `serverDraftWriteChain` 串行队列；提交listener分别在3060/4142附近。改造时用函数名定位，不能把本次行号作为抽取机制。

**最小模块契约建议：** 一个显式依赖注入的 submission-controller factory，返回上述三函数；页面原生脚本传固定context（assignment ID、打开时的 submission version、开始时间）、live state/getters/setters、api/timer/storage/status/验证/上传ports。具体名称可在实施票定稿；关键是不要把外层可变primitive复制成导入时快照，不要建立第二个上传队列或答案仓库。模块顶层不触碰window/document/network，Node可以直接import。

需要一起保留的耦合：

- `loadServerDraft`：旧窗口收到新submission version即冲突；服务端/本地时间比较；文件投影到manager；旧内容不覆盖新答案。
- `performServerDraftSave`：固定 expected_submission_version；上传/清除/replaceQuestionIds与manifest；有文件副作用时不能用答案signature去重吞掉；成功同步files，409设冲突并阻止后续写；inFlight finally清理。
- `handleSubmission`：既有题目/附件业务校验和同步不完整确认（原函数没有原生form/checkValidity路径）、实际可提交状态、清上传timer→等待pending draft队列→再检查未同步附件；409绝不退化到无版本直接提交；非409 draft失败仅在当时`state.serverQuestionFiles`没有服务器文件时继续，本地附件仍可经原端口追加。原`useServerDraftFiles`赋值位置与catch行为保持；最终POST带固定version，成功才清local草稿，失败解除busy不清答案。
- 原 `saveServerDraft` 队列、schedule/retry timer、state和uploadManager仍一个owner；模块提取不能变更退回轮次、超时策略、倒计时业务或自动交卷。

同一提交改模板为真实 ESM import / factory bindings，测试CJS改为 `await import(pathToFileURL(...))`（不用改package type），删除模板regex/vm装载。先完整保留当前5例：①文本/上传/清除都带打开版本；②新轮次draft不应用旧页；③409后停止写且答案保留；④draft409后最终提交不fallback；⑤成功最终提交带固定版本。补充提取风险用例只针对队列/文件去重例外/迟到响应与side effect一次，不重写整页测试架构。

这是S3明确授权的模块化入口，**不等于允许S3提前迁考试题卡/白板/计时/Dock全页**。测试必须加载实际生产模块，不能在fixture重写三函数再宣称import版完成。

## 8. 最小验收 / 回退边界

| 包 | 有界交付 | 放行依据 / 回退 |
|---|---|---|
| A0 兼容与开关 | 三宏wrapper双分支、两个manage partial、显式pilot context、纯SSRfixture；旧nav payload不变 | old分支旧hooks/name/type/escaping/caller不变；四eyebrow的新分支停填/旧分支保留分别断言。开关关掉回旧模板，不改schema/接口 |
| A1 八页试点 | 顶栏/侧栏/页头呈现；必要时原controller只改受影响hook，不迁未触及弹层/业务 | 上表编辑/删除/失败/权限、初始/筛选/错误态、form owner、focus返回、一次请求、Node状态保留；未迁31页SSR抽查与既有shell spec无回归 |
| B 单页试点 | report-card专用新topbar分支和主题呈现 | 本人可见范围/0/null/未公布及chart保持，成绩/公布数据不写入；既有修为GET可能刷新缓存，不能以无POST宣称整页无写；flag回旧base_navbar分支 |
| T 业务门禁与import | 已定名的七文件；真实submit模块及旧5例import化 | 后端票依赖单列；module+template+test原子提交/回滚，不能只回退模板使长开页面丢依赖 |

截图/行为最小组合：八页先1440与390、light/dark/off，至少一个真实coarse设备和768±1/关键壳断点；report-card六palette亮暗，off/forced/reduced按壳矩阵；所有编辑/失败modal检查焦点/44px目标/名字、Enter和Escape不误提交、200%重排、长文本。axe仅是其中一项，不能替代真实内容可读和功能断言。组件层已验证的生命周期可以复用，但真实页的重复挂载/20次开关仍要确认listener和observer没有双owner。

S3不清理全站旧CSS，不全局换 `btn`/确认弹窗，不更改主题内容（PDF、打印iframe、签名、用户文档）、业务数据/权限/通知、全站Dock或教学核心全页。迁移台账应按本次触及的partial/组件登记；如果八页保留旧页内dialogs/controller，则明确记为**壳/页头试点通过、页内组件待后续迁移**，不能把全页列为零旧类/零旧确认实现。

回退验证必须实测开关关后同一fixture：当前身份/导航/筛选/输入草稿不串、旧JS只挂一次、资源graph可回读；不强制刷新长开作答页。失败时先回对应页面族 HTML+CSS+JS兼容组合，保留后端和数据。后续批次估算应基于这两试点的实际修复量与状态数，本轮不凭文件数给固定天数。

## 9. 本轮交付与未做项

本轮仅新增本提案文件；未改S1/S2/产品/业务/部署/主文档，未运行任何应用或测试。以上是当前源码可核对的实施提案，不是S3验收，也不扩大S4/S5。负责人本轮已采纳八页显式pilot，并将第六业务spec定为grading-return-resubmit；无需另向用户索要批准。其余可以按最小顺序在S2本地工程出口补齐后实施，正式签字/设备/发布验收另列。

## 10. S3 实施记录（2026-09-20，验收进行中）

- `classroom_app/lq_pilot.py` 以 `LANSHARE_LQ_PILOT` 默认关闭控制九条准确路由；query/header/偏好不能开启。三宏显式可选参数默认为旧输出，八个调用点自行传入，其他31个调用点保持原分支。共享导航payload、权限、表单归属、iframe和业务controller保留。
- 管理壳复用S2原位Shell，sidebar使用native details，更多操作使用native dialog；旧样式类不再是测试的状态真源。`teacher-app-shell` 同批兼容native open与旧is-open，继续检查同一导航权限和canonical href。顶部业务动作释放本次更多面板后由原controller接管，不重发click、不重建节点。
- 成绩页单独接入topbar；图表模块维护一个owner，主题更新保留数值、legend选择和图表实例，resize/removal/bfcache释放明确。成绩、提交与公布快照独立观测；旧学生基类修为GET的投影刷新另列边界。
- 考试三函数已移入 `static/js/exam_take/submit.js`，版本Node测试直接import生产模块，23项通过。提交成功后禁止pagehide/beforeunload再次写回已清除的草稿；普通作业同一生命周期缺陷也作最小修复。失败、409和尚未提交不进入成功分支，实际刷新后的草稿状态由浏览器验证。
- K9试卷版本票独立实现：GET/SSR `paper.revision`，PUT可选 `expected_revision`，原短锁内对原始整行hash比较；省略兼容，显式null/非法token400，过期409，成功返回新revision。编辑器busy和持续冲突提示保留草稿，不自动重试。新后端8项、原相关21项和实际保存函数4项通过；真实浏览器3项已通过，未据此声称S5编辑器视觉迁移完成。
- 七条门禁文件均已创建；管理/成绩/回退另有专属spec。首轮真实应用测试发现并修复样式优先级、手机偏好面板定位和提示条阻挡测试操作等问题；仍在逐项复验。没有把文件存在、纯模板或组件通过当作页面业务/视觉完成。
- S3审计补出S2 Shell未定义的主色/成功前景变量引用，改为已定义成对token，新增全LQ CSS变量引用守卫。`backdrop-filter:none`不会产生blur宿主，lint据此纠正误报；管理页保留作者定义header动作节点的五处legacy选择器有精确例外，待对应节点迁移后删除。最新lint无阻断，未迁页警告仍保留。
- 合成入口统一复用 `tools/isolated_environment.py`：应用导入前阻断dotenv、清除继承PG设置、拒绝psycopg直接/同步类/异步类连接。新S3种子只接受有明确身份的 `.codex-temp` 子目录及准确SQLite路径，拒绝重置已使用的S3 fixture；浏览器先核对health DB路径，只允许loopback。

本阶段尚未关闭。实际命令、产物hash、失败后复验、回退演练与未测设备应继续进入 `lq-acceptance.md`，后续阶段不得以这里的实现说明绕过出口门禁。

### 2026-09-21 补充边界

独占合成业务组合20项、管理13项、成绩7项已有实际通过轮次；K9全新独占原生PostgreSQL簇6项通过并确认停止。业务结果不代替后续兼容/性能门禁，具体资源图与原失败保留在验收记录中。

实页材料详情暴露旧LP确认与已协调父层的冲突，因此增加限定父层存在时的公共桥接，保留独立旧LP同步合同。连续开关、pending-veto强制销毁、onMount移除和真实取消路径纳入验证，不扩成全站LP视觉迁移。

首屏能力回退与人生一言异步插入需要共同保持几何；新增真实模块404/中断/延迟、无JS和能力关闭测试。跨断点真实点击又发现窄屏管理导航遮挡，层数审计发现成绩顶栏内按钮重复模糊，均按具体所有者修复。WebKit引擎、CSS缩放和触控仿真分别记录，实体设备仍未测。

台账只触及九个试点，并以partial契约区分新适配源码、已审阅共享源与编译/vendor依赖。原五处legacy说明已不足以描述最终规则，当前准确例外以 `lq-lint-exceptions.json` 的八条和lint报告为准；未迁页警告仍保留。
