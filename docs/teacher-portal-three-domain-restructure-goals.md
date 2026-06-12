# 教师端三域重构目标文档（教学 · 教务 · 教师）

> 版本：v1.0（2026-06-12）
> 范围：教师角色登录后的全部界面 —— `/dashboard` 教师分支、教师管理中心 `/manage/*`（`templates/manage/layout.html` + `classroom_app/routers/ui_parts/manage_pages.py` + `classroom_app/routers/manage_parts/*`）、个人中心 `/profile`（`classroom_app/routers/profile.py` + `classroom_app/services/profile_service.py`）。
> 总体方向：**易用性、直观性、美观性**，同时保证架构合理、不给未来埋坑。所有改动遵循既有约定：UI 文案集中在 `ui_copy_service.py`、CSS 追加到 `static/css/ui-system.src.css` 底部 polish 层、新交互优先"服务端 partial + 小型自包含 JS 模块"、数据库双引擎（sqlite/postgres）兼容、定时逻辑一律走统一调度器（`scheduled_task_service.py` + handler registry）。
>
> 本文档的写法面向"其他 AI 直接执行"：每条改进都给出 为什么改 / 怎么改（含文件路径）/ 改成什么样 / 验收标准 / 测试形式与预估结果 / 偏差处理。执行时按"实施阶段"章节的顺序推进，每个阶段独立可发布、可回滚。

---

## 第一部分：现状基线（执行前必读）

### 1.1 当前教师端的功能分布

教师端功能目前散落在三个壳子里，**功能归属按"实现时间"而不是"业务领域"组织**，这是本次重构要解决的根本问题：

| 壳子 | 路由 | 当前内容 |
|------|------|---------|
| 仪表盘 | `/dashboard`（教师分支） | 课堂卡片、agenda（监考/考试/待办，数据源 `dashboard_agenda_events`）、快捷入口 |
| 教师管理中心 | `/manage/*` | 侧栏分四组：**流程工作台**（`/manage`）；**教学准备**（确认学期 `/manage/semesters`、开设课堂 `/manage/offerings`、配置 AI 助教 `/manage/ai`）；**基础资源**（班级 `/manage/classes`、课程 `/manage/courses`、教室 `/manage/classrooms`、教材 `/manage/textbooks`、试卷 `/manage/exams`、签名 `/manage/signatures`、材料 `/manage/materials`、公文 `/manage/gongwen`）；**对接与申请**（教务对接 `/manage/system/academic-integrations`、智慧课堂 `/manage/system/smart-classroom-integrations`、公文同步 `/manage/system/gongwen-integrations`、找回申请 `/manage/system/password-resets`）；**管理员**（仅超管：用户/组织/反馈/博客爬虫/Agent Key/压测诊断） |
| 个人中心 | `/profile?section=*` | sections：overview / settings / security / notifications / private / email（教师专属邮件配置）；学生另有 portfolio |

### 1.2 关键代码位置

- 管理中心壳子模板：`templates/manage/layout.html`（侧栏导航**硬编码在模板里**，约 120 行 HTML 锚点）
- 管理中心页面路由：`classroom_app/routers/ui_parts/manage_pages.py`（全部 `@router.get("/manage/...")`）
- 管理中心 API 路由：`classroom_app/routers/manage.py` + `classroom_app/routers/manage_parts/`（classes_courses、semesters_textbooks、integrations、system_config 等）
- 个人中心：`classroom_app/routers/profile.py`、`classroom_app/services/profile_service.py`（`PROFILE_SECTIONS` 元组 + `build_profile_nav`）
- 教务同步服务群：`classroom_app/services/academic_*.py`（integration、calendar/course/exam/roster/invigilation 同步、`academic_location_service.py` 教室/空闲教室）
- 公文服务群：`classroom_app/services/gongwen_*.py`
- 顶栏入口：`templates/base_navbar.html`（教师顶栏只有一个"管理中心"按钮）
- UI 文案：`classroom_app/services/ui_copy_service.py`（`STATIC_UI_COPY_SNAPSHOT[scene][role]`）
- AI 平台知识注入：`classroom_app/services/platform_knowledge_service.py`；agent 工具注册 `agent_action_registry.py`、`agent_platform_actions.py`
- 统一调度器：`classroom_app/services/scheduled_task_service.py` + `scheduled_task_handlers.py`（任何定时/延时逻辑必须走这里）

### 1.3 现状的核心问题（为什么必须重构）

1. **领域混杂**：侧栏"基础资源"里既有教学资产（课程/教材/试卷/材料），又有教务资产（教室），还有个人资产（签名）；"对接与申请"里教务对接（业务数据）和找回申请（账号事务）并列。教师找功能靠记忆位置而不是靠业务直觉。
2. **导航硬编码**：侧栏 HTML 写死在 `layout.html`，新增页面要改模板、改 `active_page` 字符串、改搜索关键词三处，已多次出现遗漏；导航无法按域/权限做数据驱动渲染。
3. **个人中心与管理中心割裂**：教师的邮件提醒配置在 `/profile?section=email`，签名在 `/manage/signatures`，对接凭据在 `/manage/system/*-integrations`——三处都是"教师个人的东西"，却分散三地。
4. **教务能力即将膨胀**：调停课、空闲教室查询、监考安排等教务功能在规划中，若继续塞进"基础资源/对接与申请"，导航将彻底失控。
5. **AI 不知道域**：`platform_knowledge_service` 注入的平台知识没有域结构，agent 给教师指路时无法说"这在教务域的××页"。

---

## 第二部分：目标信息架构蓝图

### 2.1 三域定义

| 域 | 标识 | 职责边界 | 主题色 |
|----|------|---------|--------|
| **教学** | `teaching` | 我"开的课"的一切：课堂的开设、属性设置、内容建设、进程推进。对象是**课堂（offering）及其内容资产** | 现有靛蓝 `#4f46e5`（保持，最高频域） |
| **教务** | `academic` | 学校教务系统数据的整合、筛选与利用：课表/考试/监考同步、教室与空闲教室查询、调停课（新）、公文。对象是**学校层面的事务数据** | 青绿 `#0f766e`（沿用 manage-topbar 现有 accent） |
| **教师** | `teacher` | 我自己：资料、安全、通知、邮件提醒、签名、对接凭据、状态与发展档案。对象是**教师本人** | 琥珀 `#b45309`（与现有 admin 组 amber 系一致，但 admin 组保留独立标识） |

### 2.2 功能归属总表（执行迁移时的唯一依据）

| 现有页面 | 现路由 | 目标域 | 目标分组 | 备注 |
|---------|--------|--------|---------|------|
| 流程工作台 | `/manage` | 教学 | 域首页 | 开课向导本质是教学流程 |
| 确认学期 | `/manage/semesters` | 教学 | 开课准备 | |
| 开设课堂 | `/manage/offerings` | 教学 | 开课准备 | |
| 配置 AI 助教 | `/manage/ai` | 教学 | 开课准备 | |
| 班级 | `/manage/classes` | 教学 | 教学对象 | 班级/学生名册服务于课堂 |
| 课程 | `/manage/courses` | 教学 | 内容资产 | |
| 教材 | `/manage/textbooks` | 教学 | 内容资产 | |
| 试卷 | `/manage/exams` | 教学 | 内容资产 | |
| 材料 | `/manage/materials` | 教学 | 内容资产 | |
| 智慧课堂对接 | `/manage/system/smart-classroom-integrations` | 教学 | 课堂工具 | 点名/签到服务于课堂进程 |
| 教室 | `/manage/classrooms` | **教务** | 场地 | 含空闲教室查询 |
| 教务对接 | `/manage/system/academic-integrations` | **教务** | 数据同步 | 同步动作与数据视图留教务；账号凭据移教师域（见 D4） |
| 公文 | `/manage/gongwen` | **教务** | 公文 | |
| 公文同步 | `/manage/system/gongwen-integrations` | **教务** | 公文 | 同步配置留教务；个人统一认证凭据移教师域 |
| （新）调停课 | `/manage/academic/adjustments` | **教务** | 调停课 | 本文档 C3 |
| （新）教务总览 | `/manage/academic` | **教务** | 域首页 | 本文档 C1 |
| 签名 | `/manage/signatures` | **教师** | 我的资产 | 签名是教师个人手写资产 |
| 找回申请 | `/manage/system/password-resets` | **教师** | 账号安全 | 教师协助学生找回，属账号事务 |
| 个人中心全部 sections | `/profile?section=*` | **教师** | 对应分组 | 见 D1 |
| 管理员组（超管） | `/manage/system/users` 等 | 不动 | 独立"平台管理"组 | 超管职责不属于三域，保留 amber 独立分组 |

### 2.3 导航蓝图

**壳子不拆、导航分域**：保留唯一的管理中心壳子（`manage/layout.html` 演化而来），侧栏顶部增加**三域切换 Tab**（教学 / 教务 / 教师），切换后侧栏只显示当前域的分组与条目；顶栏与侧栏按域换主题色。理由：

- 三个独立壳子 = 三套布局代码 + 三倍维护成本，且教师跨域操作（开完课去查教室）要整页跳壳，体验割裂；
- 单壳 + 域 Tab 在 2c/4GB 服务器上零额外开销，且与现有折叠侧栏/菜单搜索/移动端抽屉全部兼容。

```
┌─────────────────────────────────────────────┐
│ 侧栏                                          │
│ ┌─────────────────────────┐                  │
│ │ [教学] [教务] [教师]      │ ← 域 Tab（分段控件）│
│ └─────────────────────────┘                  │
│ 🔍 搜索菜单…（跨域全局搜索，命中自动切 Tab）       │
│                                              │
│ （教学 Tab 激活时）                             │
│   域首页：流程工作台                            │
│   ── 开课准备 ──                               │
│   确认学期 / 开设课堂 / 配置 AI 助教              │
│   ── 教学对象 ──                               │
│   班级                                        │
│   ── 内容资产 ──                               │
│   课程 / 教材 / 试卷 / 材料                      │
│   ── 课堂工具 ──                               │
│   智慧课堂                                     │
│                                              │
│ （教务 Tab 激活时）                             │
│   域首页：教务总览（新）                         │
│   ── 数据同步 ──                               │
│   教务对接                                     │
│   ── 我的安排 ──                               │
│   课表与考试 / 监考                             │
│   ── 调停课 ──（新）                            │
│   调停课申请                                   │
│   ── 场地 ──                                  │
│   教室与空闲教室                                │
│   ── 公文 ──                                  │
│   公文列表 / 公文同步                            │
│                                              │
│ （教师 Tab 激活时）                             │
│   域首页：我的概览（profile overview 升级）       │
│   ── 我的资料 ──                               │
│   基本资料 / 我的签名                            │
│   ── 提醒与通知 ──                              │
│   通知偏好 / 邮件提醒配置                         │
│   ── 账号与安全 ──                              │
│   修改密码 / 我的对接凭据 / 学生找回申请            │
│   ── 我的发展 ──（新，可后置）                    │
│   教学档案                                     │
│                                              │
│ （超管追加，amber 独立组，不参与域 Tab 过滤）       │
│   ── 平台管理 ──                               │
│   用户 / 组织 / 反馈 / 博客管家 / Agent Key / 诊断 │
└─────────────────────────────────────────────┘
```

### 2.4 URL 策略（防埋坑的关键决策）

- **新路由命名空间**：`/manage/teaching/*`、`/manage/academic/*`、`/manage/me/*`。新功能（教务总览、调停课、教学档案）**只**用新命名空间。
- **旧路由全部保留为 301 重定向**（不是别名双活）：如 `/manage/offerings` → 301 `/manage/teaching/offerings`。一处定义重定向映射表，禁止散落各文件。
- `/profile` 保留（学生也用），教师访问 `/profile` 时页面顶部出现"在教师中心打开"链接指向 `/manage/me`；`/manage/me` 的资料/安全/通知页直接复用 profile 的服务层与 partial，**不复制模板**。
- 永远不做"同一页面两个 canonical URL"——所有入口（dashboard 快捷、顶栏、agenda 链接、AI 指路）一律指新 URL。

---

## 第三部分：分点改进目标

> 编号规则：A=壳子与导航（前端骨架）、B=教学域、C=教务域、D=教师域、E=后端架构、F=数据库、G=AI 调度、H=横切质量。每条独立可验收。

## A. 壳子与导航

### A1. 导航注册表：把侧栏从模板硬编码改为服务端数据驱动

- **为什么改**：侧栏 120 行硬编码 HTML 是三域重构的最大阻力——域 Tab 过滤、权限裁剪、跨域搜索、AI 指路都需要"导航是数据"这个前提。先改这个，后面所有导航类改动都变成改一个 Python 列表。
- **怎么改**：
  1. 新建 `classroom_app/services/manage_nav_service.py`，定义不可变的导航注册表（模块级 tuple of dict，遵循 immutability 约定）：每项含 `key`、`domain`（teaching/academic/teacher/admin）、`group`（分组标题）、`label`、`icon`（SVG 名称，复用现有图标，集中放到 `templates/macros/manage_icons.html` 宏）、`href`、`search_text`、`required_flag`（如 `super_admin`）、`legacy_hrefs`（旧路由列表，供 A4 重定向表生成）。
  2. 提供 `build_manage_nav(user, active_key) -> dict`：按权限过滤、按 domain 分组、标记 active，返回 Jinja 可直接迭代的结构。
  3. `templates/manage/layout.html` 侧栏改为双层循环渲染（domain → group → item），删除全部硬编码锚点；`active_page` 参数继续兼容（映射到 `active_key`）。
  4. `manage_pages.py` 各路由的 context 里统一注入 `manage_nav=build_manage_nav(...)`，提取到 `ui_parts/common.py` 的辅助函数避免重复。
- **改成什么样**：增删导航项 = 在注册表加/删一条 dict；模板零改动；权限/域过滤在一处生效。
- **验收标准**：
  - [ ] 渲染后的侧栏 HTML 与重构前逐项一致（条目、顺序、图标、active 态、超管组可见性）；
  - [ ] 注册表中每项的 `href` 都有对应路由（启动时自检或测试断言）；
  - [ ] 菜单搜索功能行为不变（`data-nav-text` 来自 `search_text`）。
- **测试形式与预估结果**：pytest 单测 `tests/test_manage_nav_service.py`：断言注册表完整性（key 唯一、href 非空、domain 合法）、超管过滤、active 标记；P03 Playwright 截图对比侧栏。预估一次通过（纯数据搬移），图标宏提取可能漏 1-2 个 SVG 需补。
- **偏差处理**：若某些页面（如嵌入模式 `embedded_mode`）渲染路径绕过了 context 注入导致侧栏空白，回退策略是 `build_manage_nav` 在模板里通过全局 Jinja 函数调用（`templates.env.globals`），而不是逐路由注入；不要为赶进度在模板里留一半硬编码一半循环的混合态。

### A2. 三域 Tab 切换 + 按域换肤

- **为什么改**：教师面对 20+ 菜单项一屏滚动，找"教室"要扫过试卷/签名/材料。三域 Tab 让任意时刻屏幕上只有 5-8 个与当前心智相关的条目，是本次重构对"直观性"贡献最大的一条。
- **怎么改**：
  1. 在 A1 的导航数据基础上，`layout.html` 侧栏品牌区下方渲染分段控件（3 个 button，`data-domain` 属性），点击切换显示对应 domain 的 nav group；当前页面所属 domain 默认激活（由 `active_key` 反查）。
  2. 切换为纯前端显隐（CSS class `is-domain-hidden`），不发请求；用户手动切换的 Tab 记忆到 `localStorage['lanshare:manage-domain']`，但**页面所属域优先于记忆值**（避免"打开试卷页却显示教务菜单"的错位）。
  3. 换肤：`<body>` 加 `manage-domain-{teaching|academic|teacher}` class，在 `ui-system.src.css` polish 层定义三组 CSS 变量覆盖（`--manage-accent`、`--manage-accent-soft`、`--manage-accent-rgb`），侧栏 active 态、topbar accent、域 Tab 激活色全部引用变量。admin 组保持现有 amber 硬编码不参与换肤。
  4. 菜单搜索升级为跨域：输入时三域条目全部参与匹配，命中条目自动显示并在条目右侧加所属域的小色点；清空搜索恢复 Tab 过滤。
  5. 移动端抽屉内同样渲染域 Tab（现有 `.manage-sidebar.open` 机制不变）。
- **改成什么样**：打开"开设课堂"看到的是教学域的靛蓝侧栏 + 教学菜单；点"教务"Tab 侧栏变青绿、菜单变成教务 5 项；搜索"教室"无论在哪个 Tab 都能直达。
- **验收标准**：
  - [ ] 每个 manage 页面打开时自动激活其所属域 Tab，active 条目可见；
  - [ ] 三域主题色在侧栏 active、topbar、Tab 上正确生效，admin 组仍为 amber；
  - [ ] 跨域搜索可命中其他域条目并能点击跳转；
  - [ ] 折叠成 76px 图标栏时域 Tab 退化为三个小色点按钮（仍可切换）；
  - [ ] 390px 移动端抽屉内 Tab 可用。
- **测试形式与预估结果**：P03 Playwright：登录教师 → 打开 `/manage/classrooms`（迁移后为教务域）断言 body class 与可见菜单项集合；切 Tab 断言显隐；搜索"签名"断言教师域条目出现。预估需 2 轮：首轮折叠态与移动端的 Tab 样式大概率要微调。
- **偏差处理**：若折叠图标栏下三色点按钮可用性差（点击区过小），改为折叠态下隐藏 Tab、搜索即全域（可接受的降级，不阻塞发布）；若 localStorage 记忆与页面域冲突引起用户困惑反馈，直接移除记忆逻辑只跟随当前页面，逻辑更简单。

### A3. Dashboard 教师分支改为"三域驾驶舱入口"

- **为什么改**：教师 dashboard 目前的快捷入口与管理中心导航是两套各自维护的清单，重构后必须同源，否则旧入口会把人带回旧心智；同时 dashboard 是展示"三域各自待处理事项"的最佳位置。
- **怎么改**：
  1. `classroom_app/services/dashboard_service.py` 的 `_build_teacher_dashboard_context` 增加 `domain_entries`：三张域入口卡（教学/教务/教师），每张含域名、主题色 token、2-3 条该域的动态摘要（教学：进行中课堂数、待批改数；教务：下一场监考/考试（取自 `dashboard_agenda_events`）、待处理调停课数（C3 落地后）；教师：未读通知数、邮件提醒配置状态）、主按钮（进入域首页）。
  2. `templates/dashboard.html` 教师分支用三张横排卡片渲染（≤960px 纵排），样式进 polish 层，复用现有卡片设计语言。
  3. 快捷入口列表的链接全部改为从 `manage_nav_service` 注册表取（按 `key` 引用），消灭手写 URL。
- **改成什么样**：教师登录第一眼看到三张域卡："教学——3 个课堂进行中，5 份待批改"；"教务——明天 14:00 监考《大学英语》"；"教师——2 条未读通知"。点卡片进对应域。
- **验收标准**：
  - [ ] 三张卡的摘要数字与对应页面实际数据一致；
  - [ ] 所有 dashboard 上指向 manage 的链接均为新 URL（无 301 跳转，直接 200）；
  - [ ] 无监考/无调停课时摘要行优雅降级（显示"暂无安排"而非空白）。
- **测试形式与预估结果**：pytest 对 `_build_teacher_dashboard_context` 的 `domain_entries` 做数据断言（seed 监考事件 + 待批改作业）；P03 截图三卡布局。预估摘要数字的 SQL 口径需要 1 轮校对（待批改数的定义容易与现有统计不一致）。
- **偏差处理**：若三卡摘要查询拖慢 dashboard 首屏（目标：新增查询合计 < 50ms），将摘要降级为静态文案 + 异步 fetch 填充（`/api/dashboard/domain-summaries`），卡片骨架先渲染；绝不为摘要数字阻塞首屏。

### A4. 旧路由 301 重定向层 + 全站入口替换

- **为什么改**：路由迁移最大的坑是死链——浏览器书签、邮件提醒里的链接、AI 历史回答、模板里散落的硬编码 href。必须有系统性的兼容层而不是逐个碰运气。
- **怎么改**：
  1. 重定向映射**从 A1 注册表的 `legacy_hrefs` 字段自动生成**：新建 `classroom_app/routers/manage_redirects.py`，启动时遍历注册表注册 `RedirectResponse(status_code=301)` 路由（保留 query string）。
  2. 全仓 grep 替换硬编码旧 URL：`grep -rn "manage/offerings\|manage/classrooms\|manage/signatures\|manage/system/" templates/ static/js/ frontend/src/ classroom_app/` 逐处替换为新 URL（模板内优先改为引用 nav 注册表项或 Jinja 变量）。
  3. 邮件模板（`email_notification_service.py` 等拼链接处）同步替换。
- **改成什么样**：访问任何旧 URL 都 301 到新位置，站内不再产生旧 URL。
- **验收标准**：
  - [ ] 注册表内每个 `legacy_href` 请求返回 301 且 Location 正确（含 query 透传，如 `?semester=xxx`）；
  - [ ] `grep -rn` 旧 URL 在 templates/static/frontend/classroom_app 中零命中（manage_redirects.py 与测试文件除外）;
  - [ ] 登录态与未登录态访问旧 URL 行为一致（301 在鉴权前发生，鉴权由目标页执行）。
- **测试形式与预估结果**：pytest 参数化测试遍历全部 legacy 映射断言 301 + Location；预估一次通过。grep 替换预估发现 15-30 处散落引用，其中 JS 文件改动需要 bump `?v=` 缓存参数（遵循 frontend-change-playbook）。
- **偏差处理**：若某旧 URL 被外部系统（如已发出的提醒邮件）大量引用且 query 语义在新页面变化了，为该 URL 单独写带参数转换的重定向函数，不要改通用生成器；如果 301 与 FastAPI 路由顺序冲突（旧路径是新路径的前缀），调整 router include 顺序并加注释说明，禁止用 307。

## B. 教学域

### B1. 教学域首页：流程工作台升级为"教学工作台"

- **为什么改**：`/manage` 现在是开课向导（workflow），但开课只是学期初的事；学期中教师高频动作是"看各课堂进程、去批改、发材料"，这些动作目前要回 dashboard 或进各课堂找。教学域需要一个学期全程都有用的首页。
- **怎么改**：
  1. 路由 `/manage/teaching` 指向升级版 workflow 页（`templates/manage/workflow.html` 重命名为 `teaching_home.html` 并扩展）。
  2. 页面结构（自上而下）：(a) **学期阶段感知条**——根据当前日期与学期数据判断"学期初/中/末"，学期初突出开课向导（现有 workflow 内容折叠为可展开步骤条），学期中默认折叠向导、突出课堂进程；(b) **课堂进程矩阵**——每个进行中 offering 一行：课堂名、学生数、最近活动、待批改作业数、未发布考试数、快捷按钮（进入课堂/批改/材料）；(c) 底部保留"教学准备"快捷卡（学期/开课/AI 助教）。
  3. 数据来源：复用 `dashboard_service` 与 `assignment_lifecycle_service` 已有查询，新增 `classroom_app/services/teaching_home_service.py` 聚合（薄聚合层，禁止复制查询逻辑，引用既有 service 函数）。
- **改成什么样**：学期中打开教学域，3 秒内看清"哪个课堂有待办、点一下直达"；学期初打开则是熟悉的开课向导。
- **验收标准**：
  - [ ] 学期阶段判断正确（以学期起止日期 ±2 周为界，常量集中定义）；
  - [ ] 进程矩阵的待批改数与作业页一致；
  - [ ] 无任何 offering 时显示开课引导空状态。
- **测试形式与预估结果**：pytest 测 `teaching_home_service` 三种学期阶段分支 + 矩阵数据；P03 截图。预估学期阶段边界条件需 1 轮修正（跨学期/无学期数据的教师）。
- **偏差处理**：若"学期阶段感知"对部分教师误判（如补开课的教师在学期中仍需向导），在感知条上提供手动切换（"展开开课向导"按钮始终可见），感知只决定**默认**展开态，不隐藏任何功能。

### B2. 课堂属性与内容设置集中化（课堂设置页统一入口）

- **为什么改**：课堂相关设置目前分散：AI 助教配置在 `/manage/ai`（按 offering 配置却放在全局页）、智慧课堂绑定在 `/manage/system/smart-classroom-integrations`、课堂内容（材料/作业/考试）在课堂页内。教师"配置一个课堂"要跑三个地方。
- **怎么改**：
  1. 在开设课堂页（offerings）每个课堂卡上提供"课堂设置"入口，路由 `/manage/teaching/offerings/{id}/settings`，页面为 Tab 式：基本属性（名称/教室/时间）、AI 助教（该 offering 的 AI 配置，复用 `/manage/ai` 的表单 partial）、智慧课堂绑定（该 offering 的绑定状态与操作，复用 integration partial）、高级（归档/转让）。
  2. `/manage/ai` 与智慧课堂页**保留**为全局批量视图（一次看所有课堂的配置状态），但每行操作链接指向上述课堂设置页对应 Tab——全局页管"看全貌"，设置页管"改一个"。
  3. 表单 partial 抽取到 `templates/partials/offering_settings/`，两处复用，禁止复制表单 HTML。
- **改成什么样**：配置一个课堂 = 进一个页面切 Tab；检查所有课堂 AI 配置状态 = 进全局页扫一眼。
- **验收标准**：
  - [ ] 课堂设置页四个 Tab 的读写与原页面行为一致（同一套 API，不新增端点）；
  - [ ] 全局页与设置页数据实时一致（无双份状态）；
  - [ ] partial 复用：`grep` 确认表单字段 HTML 仅存在一份。
- **测试形式与预估结果**：P03 走通"开课 → 进设置 → 改 AI 配置 → 全局页验证状态变化"链路；pytest 不需新增（API 未变）。预估 partial 抽取时 JS 事件绑定（form submit handler）需要 1 轮适配，因 `handleFormSubmit` 目前挂在 layout 全局。
- **偏差处理**：若智慧课堂绑定的 partial 与 integration 页耦合过深（依赖该页特有 JS），第一期允许该 Tab 只显示状态 + "前往智慧课堂页配置"链接，不强行内嵌表单；宁可少一个内嵌 Tab，不要复制一份会腐烂的表单代码。

### B3. 班级与学生：保留现有页面，导航归位 + 文案校正

- **为什么改**：班级/学生详情页（`/manage/classes`、`/manage/students/{id}`）功能完整，只是归属错位（在"基础资源"组）。低成本归位即可，不动功能——遵循 YAGNI。
- **怎么改**：A1 注册表中 `classes` 项设 `domain='teaching'`、`group='教学对象'`；`ui_copy_service.py` 相关 scene 文案中"基础资源"措辞改为"教学对象"；页面内面包屑/标题随 nav 数据自动更新。
- **改成什么样**：班级出现在教学域"教学对象"组，其余不变。
- **验收标准**：[ ] 教学 Tab 下可见班级入口；[ ] 页面功能回归无变化。
- **测试形式与预估结果**：包含在 A2 的导航测试集内；预估零额外工作量。
- **偏差处理**：无实质风险；若文案 scene 找不到对应 key，仅改导航不改文案，记 TODO。

## C. 教务域

### C1. 教务总览页（域首页，新建）

- **为什么改**：教务数据（课表/考试/监考/公文/教室）已通过 `academic_*` 服务群同步进平台，但教师没有一个"我的教务面板"——要看监考去 dashboard agenda 翻，要看同步状态去对接页。教务域首页是该域"信息整合与筛选"定位的直接载体。
- **怎么改**：
  1. 新路由 `/manage/academic` + 模板 `templates/manage/academic_home.html` + 服务 `classroom_app/services/academic_home_service.py`。
  2. 页面结构：(a) **本周教务时间线**——复用 `dashboard_agenda_events` 数据源（监考/考试事件），按周视图渲染，支持周切换；(b) **筛选器**——按类型（考试/监考/调停课）、按课程过滤，纯前端筛选（数据量小）；(c) **同步状态卡**——各 academic 同步源（课表/考试/监考/名册）的最近同步时间与状态，数据取自现有 integration 服务的状态查询，异常时显示"前往教务对接处理"；(d) **快捷区**——空闲教室查询、发起调停课（C3 后）。
  3. 时间线事件可展开详情（考试地点、监考同事、对应"设置邮件提醒"按钮——复用现有 exam reminder popover 组件）。
- **改成什么样**：教师周一打开教务域，本周监考/考试一目了然，可顺手设邮件提醒；同步异常一眼可见。
- **验收标准**：
  - [ ] 时间线事件与 dashboard agenda 同源同数（同一服务函数，禁止重写查询）；
  - [ ] 周切换不整页刷新（服务端 partial 或预载本学期数据前端切换，二选一，倾向后者因数据量小）；
  - [ ] 同步状态卡在"从未同步/正常/失败"三态下渲染正确；
  - [ ] 邮件提醒按钮创建的提醒走统一调度器（`scheduled_task_service`），与现有考试提醒同一 handler。
- **测试形式与预估结果**：pytest 测 `academic_home_service` 周窗口切分与三态同步状态；P03 seed 监考事件后截图断言时间线渲染与提醒按钮。预估周边界（周日/周一为首日）需 1 轮确认，与现有学生周历约定保持一致。
- **偏差处理**：若 `dashboard_agenda_events` 的事件结构缺教务详情字段（如监考同事），**扩展该数据源的字段**而不是新建第二条查询通路——agenda 统一数据源是既有架构约定，破坏它会让 dashboard 与教务页未来必然漂移；扩展字段时学生分支不受影响（字段可选）。

### C2. 教室与空闲教室查询迁入教务域并升级

- **为什么改**：`/manage/classrooms` 现在与"教材/试卷"并列在基础资源里，但教室是学校教务资产；且空闲教室查询是调停课（C3）选新教室的前置依赖，两者必须同域协作。
- **怎么改**：
  1. 路由迁移：`/manage/academic/classrooms`（旧路由 301，走 A4 机制），导航归入教务域"场地"组。
  2. 页面升级：顶部增加"按时段查空闲"查询条（日期 + 节次选择），调用 `academic_location_service` 既有/扩展的占用查询，结果以教室卡片网格展示（空闲绿色描边、占用灰显并显示占用课程）；保留现有教室列表管理功能在下方 Tab。
  3. 查询逻辑封装为可复用函数 `find_available_classrooms(conn, date, periods, *, building=None)`，供 C3 调停课表单内嵌调用。
- **改成什么样**：教师选"6月20日 第3-4节"即见全部空闲教室；调停课表单里能直接调同一查询选教室。
- **验收标准**：
  - [ ] 空闲判定正确：与该时段已同步课表/考试占用冲突的教室不出现在空闲列表；
  - [ ] 查询函数无 N+1（一次查询取全部占用再内存比对，教室量级 < 500 可接受）；
  - [ ] 旧路由 301 生效。
- **测试形式与预估结果**：pytest 构造"教室 A 被课表占用、教室 B 空闲"的 seed，断言查询结果；P03 截图查询交互。预估占用数据的来源口径（课表 vs 考试 vs 调停课自身）需要 1-2 轮与现有同步数据结构对齐——这是本条最大不确定点。
- **偏差处理**：若同步的课表数据不含教室占用粒度（只有教师自己的课），第一期将功能定义收窄为"查询**平台已知**占用下的空闲教室"，页面明示数据范围（"基于已同步课表，仅供参考"），不做虚假承诺；待教务对接数据补全后移除提示。**不要**为补全数据去爬取额外教务接口——那是独立的对接需求，单独立项。

### C3. 调停课设置（全新功能）

- **为什么改**：用户明确要求的新增能力。教师调课/停课目前完全在平台外（线下流程），导致平台内课表/agenda 与现实不符，提醒会提醒到已取消的课。
- **怎么改**（分层说明）：
  1. **数据库**（详见 F1）：新表 `course_adjustments`——id、teacher_id、offering_id（可空，允许针对同步课表中的非平台课堂）、source_schedule_ref（原课次标识：日期+节次+教室）、adjustment_type（`cancel` 停课 / `reschedule` 调课 / `substitute` 代课）、new_date/new_periods/new_classroom_id/new_teacher_name（调课/代课时填）、reason、status（`draft`/`announced`/`revoked`，第一期**不做审批流**，见偏差处理）、notify_students（INTEGER 0/1）、created_at/updated_at。
  2. **后端**：`classroom_app/services/course_adjustment_service.py`（CRUD + 校验：新时段教室占用冲突检查复用 C2 查询；不可调整过去的课次）+ `classroom_app/routers/manage_parts/` 新增 `course_adjustments.py` API + `manage_pages.py` 加页面路由 `/manage/academic/adjustments`。
  3. **前端**：列表页（状态筛选 + 时间排序）+ 新建抽屉表单：选原课次（从已同步课表下拉选择，无课表时手动填）→ 选类型 → 调课时内嵌空闲教室查询（C2 函数）→ 填原因 → 选择是否通知学生。
  4. **联动**：(a) 发布（announced）后写入 `dashboard_agenda_events` 数据通路——停课在学生/教师 agenda 中将原课次标记"已停课"，调课生成新事件；(b) `notify_students=1` 时通过现有消息中心（`message_center_service`）向 offering 学生发站内通知，邮件通知走 `email_outbox` 既有通路；(c) 提前提醒（如调课前 1 天提醒学生）注册为统一调度器的一次性任务，新增 handler `course_adjustment_reminder` 到 `scheduled_task_handlers.py`。
- **改成什么样**：教师 1 分钟内完成一次调课登记：选课次 → 选新时段（系统推荐空闲教室）→ 发布 → 学生 agenda 自动更新并收到通知。
- **验收标准**：
  - [ ] 三种类型（停/调/代）创建、发布、撤销全链路可用；
  - [ ] 调课目标时段与已知占用冲突时表单阻断并提示；
  - [ ] 发布后学生 dashboard agenda 出现对应变更，撤销后恢复；
  - [ ] 通知与提醒任务在 `scheduled_tasks` 表可见且执行后学生收到站内信/邮件；
  - [ ] sqlite 与 postgres 双引擎下 schema 与查询均通过（布尔用 INTEGER，INSERT 用 `sql.py` builders）。
- **测试形式与预估结果**：pytest 为主（service 层全分支：冲突校验/状态机/agenda 事件生成/调度任务注册），预估 15+ 用例；P03 走通"创建调课 → 学生账号看 agenda"端到端。预估 agenda 联动是最难点，需 2 轮（事件去重：原课次事件与调课事件并存的显示策略）。
- **偏差处理**：
  - 若 agenda 数据通路不支持"修饰既有事件"（标记已停课），第一期降级为只**新增**事件（"【停课】原X月X日第3-4节《课程》不上课"），不修改原事件——信息正确性优先于展示优雅性；
  - **审批流**（教务管理员审批调课）刻意不做：当前平台无教务管理员角色，凭空造角色就是埋坑。status 字段已预留 `draft→announced` 之外的扩展空间，未来加 `pending_approval` 即可，表结构不需变；
  - 若与学校教务系统的"官方调课"数据冲突（同步进来的课表已变），以同步数据为准，平台调停课记录加"与教务系统数据不一致"警示，人工裁决。

### C4. 公文归入教务域

- **为什么改**：公文（校园公文通同步）是学校行政事务信息，属教务域"信息整合"定位；现在公文列表在"基础资源"、公文同步在"对接与申请"，同一业务拆在两组。
- **怎么改**：A1 注册表将 `gongwen`、`gongwen_integrations` 两项归 `domain='academic'`、`group='公文'`，相邻排列；路由迁移至 `/manage/academic/gongwen` 与 `/manage/academic/gongwen-sync`（301 兼容）；个人统一认证凭据部分按 D4 迁出。
- **改成什么样**：教务 Tab 下"公文"组两项并列：看公文、管同步。
- **验收标准**：[ ] 导航归位；[ ] 301 生效；[ ] 公文 AI 检索、归档等功能回归无损。
- **测试形式与预估结果**：A2/A4 测试集覆盖 + 公文页 P03 冒烟；预估一次通过。
- **偏差处理**：公文同步页若与凭据管理（D4）拆分有耦合，允许本条先行（仅挪导航与路由），凭据拆分随 D4 节奏，两条互不阻塞。

## D. 教师域

### D1. 个人中心并入教师域：`/manage/me` 复用 profile 服务层

- **为什么改**：教师的"自己"散在 `/profile`（资料/安全/通知/邮件）与 `/manage`（签名/找回申请）两壳。教师域要把这些聚到管理中心壳子里，但 `/profile` 是学生共用的，不能砍。正确解法是**服务层单一、视图层两壳复用**。
- **怎么改**：
  1. `manage_pages.py` 新增 `/manage/me`（域首页=概览）、`/manage/me/settings`、`/manage/me/security`、`/manage/me/notifications`、`/manage/me/email` 路由，全部调用 `profile_service.build_profile_page_context`（已有），渲染时外层套 manage 壳（`manage/layout.html`），内容区 include profile 既有 section partial。前置工作：把 `templates/profile.html` 中各 section 的 HTML 抽成 `templates/partials/profile_sections/*.html`（profile.html 与 manage 包装页共同 include）。
  2. `profile_service.PROFILE_SECTIONS` 与 `build_profile_nav` 不动（学生路径零影响）；教师访问 `/profile` 时顶部加一条轻提示链接"前往教师中心查看完整功能 → /manage/me"。
  3. 站内所有教师指向 `/profile` 的入口（顶栏头像菜单等）改指 `/manage/me`；学生入口不变。
- **改成什么样**：教师在管理中心"教师"Tab 下完成资料/安全/通知/邮件全部操作，与教学/教务同壳无跳出感；学生 profile 完全不受影响。
- **验收标准**：
  - [ ] `/manage/me/*` 各页功能与 `/profile?section=*` 行为一致（同一 API、同一 partial）；
  - [ ] partial 抽取后 `profile.html` 学生视角像素级回归（P03 截图对比）；
  - [ ] 教师顶栏入口指向 `/manage/me`；
  - [ ] 头像上传、密码修改、邮件配置测试发送等写操作在新壳下可用（JS 依赖完整加载）。
- **测试形式与预估结果**：P03 双角色回归（学生 profile 截图对比 + 教师 /manage/me 各 section 功能走查）；pytest 不需新增（服务层未动）。预估 partial 抽取 2 轮：profile 页内 JS（头像裁剪、表单提交）与 manage 壳的脚本加载顺序需要适配。
- **偏差处理**：若 profile 的 section JS 与 manage 壳全局脚本冲突（如重复定义 `handleFormSubmit`），将 profile section 脚本模块化为 `static/js/profile_sections.js`（ES module、幂等初始化、`data-` 属性挂载），两壳引同一模块；**禁止**在 manage 壳里 iframe 嵌 profile——embedded_mode iframe 是历史方案，新代码不再扩大其使用面。

### D2. 教师域首页"我的概览"：状态一屏

- **为什么改**：profile overview 目前是静态资料展示；教师域首页应回答"我现在的状态"——今天的课、本周工作量、通知、待办配置项（如邮件提醒未配置）。
- **怎么改**：
  1. `/manage/me` 概览页在资料卡之外增加：(a) **今日安排**——复用 `dashboard_agenda_events` 取当天事件；(b) **本周工作量摘要**——本周课时数（来自同步课表）、监考场次、待批改数；(c) **配置健康度清单**——逐项检查：已设头像？已配邮件提醒？已上传签名？已绑定教务对接？未完成项给一键直达链接（链接取自 nav 注册表）。
  2. 新增 `classroom_app/services/teacher_overview_service.py` 聚合（薄层，引用既有查询）。
- **改成什么样**：教师域首页 = 名片 + 今天 + 本周 + "还有 2 项配置建议完成"。
- **验收标准**：
  - [ ] 配置健康度四项检查准确（各取自权威数据源）；
  - [ ] 全部配置完成时清单整卡隐藏（不展示空清单）；
  - [ ] 今日无事件时显示"今天没有安排"。
- **测试形式与预估结果**：pytest 测健康度四项的真/假分支组合；P03 截图。预估一次通过。
- **偏差处理**：若工作量摘要的"本周课时"因课表未同步而恒为 0 造成误导，该格降级显示"绑定教务对接后显示"并链到对接页——把缺数据变成引导，而不是显示错误的 0。

### D3. 签名与找回申请迁入教师域

- **为什么改**：签名是教师个人手写资产（用于材料落款），放"基础资源"语义错误；找回申请是教师协助处理的账号事务，与"教务对接"并列更是误导。
- **怎么改**：A1 注册表归位：`signatures` → `domain='teacher'`、`group='我的资料'`；`password_resets` → `domain='teacher'`、`group='账号与安全'`。路由迁 `/manage/me/signatures`、`/manage/me/password-resets`（301 兼容）。
- **改成什么样**：教师 Tab 下"我的签名"在基本资料旁边；"学生找回申请"在安全组。
- **验收标准**：[ ] 导航归位 + 301；[ ] 签名上传/管理、找回审批功能回归无损。
- **测试形式与预估结果**：A2/A4 测试集覆盖 + 两页 P03 冒烟；预估一次通过。
- **偏差处理**：无实质风险；若有人认为找回申请属"平台管理"，注意它面向**全部教师**（非超管），归教师域是按使用者归类，保持此决策。

### D4. "我的对接凭据"页：个人凭据与同步配置分离

- **为什么改**：教务对接/公文同步页目前混着两类东西：(a) 教师个人的教务系统/统一认证**账号凭据**（属于"我"），(b) 同步范围、频率、数据视图（属于"教务数据"）。凭据混在教务页里导致教师改密码后不知去哪更新，也让教务页承担了敏感信息展示。
- **怎么改**：
  1. 新页 `/manage/me/credentials`（教师域"账号与安全"组）：列出全部对接渠道（教务系统、公文通、智慧课堂——如有个人凭据）的凭据状态卡：账号（脱敏显示）、最近验证时间、有效/失效徽标、更新凭据表单（复用各 integration 现有的凭据表单 partial 与 API，**不新增凭据存储逻辑、不动加密方式**）。
  2. 教务对接/公文同步页中的凭据表单区替换为状态摘要 + "前往我的对接凭据管理 →"链接；同步配置与数据视图保留原地。
  3. 凭据失效时（同步任务报认证错误），消息中心通知的跳转链接指向新页。
- **改成什么样**：教师改了学校密码 → 进"我的对接凭据"一页更新全部渠道；教务页只管数据与同步。
- **验收标准**：
  - [ ] 凭据更新在新页可完成且同步任务随后成功（端到端验证）；
  - [ ] 凭据字段全程脱敏（页面源码不含明文密码回显）；
  - [ ] 旧页不再渲染凭据输入框；
  - [ ] 表单 partial 单份复用（grep 验证）。
- **测试形式与预估结果**：pytest 测凭据状态聚合（有效/失效/未配置三态 × 渠道数）；P03 走凭据更新交互（用 seed 假凭据，不连真实学校系统）。预估 2 轮：各 integration 的凭据 API 形态不一（教务 vs 公文），聚合层需要适配器。
- **偏差处理**：若某渠道凭据表单与其同步页耦合过深（如公文的验证码流程内嵌在同步页 JS），该渠道第一期在新页只显示状态 + 深链到原页表单锚点，迭代期再抽取——与 B2 的偏差策略一致：宁缺内嵌、不复制表单。**安全红线**：本条只做展示层重排，任何凭据存储/加密改动都超出本文档范围，发现存储层有安全问题立即停下走安全评审，不顺手改。

### D5. 教师发展档案（教学档案，低优先级、可独立后置）

- **为什么改**：用户要求教师域涵盖"发展"。学生已有 portfolio（成长档案），教师侧缺对称能力：教学履历、荣誉、培训记录的沉淀，可服务于年度考核材料整理。
- **怎么改**：
  1. 复用 `portfolio_service.py` 的结构思路（但**独立表**，见 F2，不与学生 portfolio 混表）：`teacher_development_records`——id、teacher_id、record_type（`honor` 荣誉 / `training` 培训 / `achievement` 教学成果 / `note` 备忘）、title、description、occurred_on、attachment_file_id（可空，复用文件服务）、created_at。
  2. 页面 `/manage/me/development`：时间轴展示 + 新增/编辑抽屉；类型筛选 chips。
  3. **自动沉淀钩子**（第二期）：学期结束时调度器任务自动生成一条"本学期教学摘要"记录（课堂数/学生数/作业批改量），教师可编辑保留或删除。
- **改成什么样**：教师域多一个"教学档案"页，年度考核时按时间轴导出自己的一年。
- **验收标准**：
  - [ ] CRUD + 附件上传可用，仅本人可见；
  - [ ] 类型筛选与时间排序正确；
  - [ ] （二期）学期摘要任务在调度器注册并正确生成。
- **测试形式与预估结果**：pytest service 层 CRUD 与权限（教师 A 不可读教师 B）；P03 截图时间轴。预估一次通过（模式成熟，照 portfolio 模式做）。
- **偏差处理**：本条整体可后置，不阻塞任何其他条目；若工期紧张，第一期只做表 + 列表 + 手动新增（不做附件、不做自动沉淀），表结构按全量设计建好避免后续迁移。

## E. 后端架构

### E1. 路由与服务的域化整理（物理结构跟随逻辑结构）

- **为什么改**：`manage_pages.py` 已 800+ 行且还在涨；`manage_parts/` 的拆分维度（classes_courses、semesters_textbooks）是历史产物，与三域不对应。物理结构与逻辑结构长期背离会让"该把新代码放哪"持续产生错误答案。
- **怎么改**（渐进、只挪不改逻辑）：
  1. `classroom_app/routers/ui_parts/manage_pages.py` 按域拆为 `manage_pages_teaching.py`、`manage_pages_academic.py`、`manage_pages_teacher.py`、`manage_pages_admin.py`，共享辅助留在 `ui_parts/common.py`；`__init__.py` 聚合 include 保持对外不变。
  2. `manage_parts/` 暂不大改（API 层稳定优先），仅新功能（调停课等）按域命名新文件；在 `manage_parts/__init__.py` 加注释块说明域归属映射，作为未来整理的路标。
  3. 新服务命名约定：教学域聚合服务前缀 `teaching_`、教务 `academic_`（已有）、教师 `teacher_`，写入 `CLAUDE.md` 或仓库内约定文档。
- **改成什么样**：找"教务页面路由"= 打开 `manage_pages_academic.py`；新增页面的落点无歧义。
- **验收标准**：
  - [ ] 拆分为纯移动（`git diff` 验证函数体零变更）；
  - [ ] 全部 manage 路由表拆分前后一致（用 `app.routes` 快照对比测试）;
  - [ ] 每个新文件 < 400 行。
- **测试形式与预估结果**：pytest 路由快照测试（收集所有 `/manage` 路由的 path+methods 排序比对）+ 既有测试套件全绿。预估一次通过（机械移动）。
- **偏差处理**：若移动引发循环 import（ui_parts 之间互相引用辅助函数），把共享函数下沉到 `common.py` 解环，禁止 routers 文件之间横向 import；若快照测试发现路由丢失，逐文件 diff 找回，不要凭记忆补写。

### E2. 域权限与可见性的单点控制

- **为什么改**：当前权限判断散在各路由（`get_current_teacher`、超管检查各写各的）。三域引入后若再散，未来"某域只对某类教师开放"（如教务域功能依赖组织归属）会改不动。
- **怎么改**：
  1. A1 的 nav 注册表 `required_flag` 即可见性单点；路由侧补充对应 Depends：新建 `classroom_app/dependencies.py` 内（或扩展现有）`require_teacher_domain(domain)` 依赖工厂，目前实现 = `get_current_teacher`（+超管项校验 super_admin），但所有新路由必须通过它声明域归属。
  2. 不引入新的权限模型/表——当前所有教师可见全部三域，这一层只是**预留收口点**，避免未来加细粒度权限时全仓改造。
- **改成什么样**：未来"调停课仅对已绑定教务对接的教师开放"这类需求 = 改一个依赖工厂 + 一个注册表字段。
- **验收标准**：[ ] 新增路由（C1/C3/D2/D4/D5）全部使用域依赖；[ ] 行为与 `get_current_teacher` 等价（现阶段无权限差异）。
- **测试形式与预估结果**：pytest 验证未登录/学生身份访问新路由被拒；预估一次通过。
- **偏差处理**：警惕过度设计——若实现时发现依赖工厂除了透传没有任何逻辑且团队认为是噪音，可降级为"仅在注册表声明域 + 路由用现有依赖"，把收口点只留在导航层；记录该决策即可，两个方案都不埋坑，唯一禁止的是一半路由用新依赖一半用旧的混乱态。

## F. 数据库

### F1. 调停课表 `course_adjustments`

- **为什么改**：C3 的数据载体。设计原则：兼容双引擎、字段一次到位避免近期迁移、不过度建模（无审批流表、无历史版本表）。
- **怎么改**：
  1. `classroom_app/db/schema.py` 的 `init_database()` 增加建表（双引擎分支或共用 SQL，布尔用 INTEGER，时间戳用 TEXT ISO 格式与现有表一致）：

     ```sql
     CREATE TABLE IF NOT EXISTS course_adjustments (
         id            INTEGER PRIMARY KEY,         -- pg: BIGSERIAL，按既有模式
         teacher_id    INTEGER NOT NULL,
         offering_id   INTEGER,                     -- 可空：非平台课堂
         source_date   TEXT NOT NULL,               -- 原课次日期 YYYY-MM-DD
         source_periods TEXT NOT NULL,              -- 原节次，如 "3-4"
         source_classroom TEXT,
         source_course_name TEXT NOT NULL,          -- 冗余课程名，防同步数据变动后失引
         adjustment_type TEXT NOT NULL,             -- cancel | reschedule | substitute
         new_date      TEXT,
         new_periods   TEXT,
         new_classroom_id INTEGER,
         new_classroom_label TEXT,
         substitute_teacher_name TEXT,
         reason        TEXT,
         status        TEXT NOT NULL DEFAULT 'draft',  -- draft | announced | revoked
         notify_students INTEGER NOT NULL DEFAULT 0,
         announced_at  TEXT,
         created_at    TEXT NOT NULL,
         updated_at    TEXT NOT NULL
     );
     CREATE INDEX IF NOT EXISTS idx_course_adjustments_teacher
         ON course_adjustments(teacher_id, status);
     CREATE INDEX IF NOT EXISTS idx_course_adjustments_offering
         ON course_adjustments(offering_id, status);
     CREATE INDEX IF NOT EXISTS idx_course_adjustments_dates
         ON course_adjustments(source_date, new_date);
     ```
  2. INSERT/UPDATE 一律走 `classroom_app/db/sql.py` 的跨引擎 builders；状态机变更在 service 层校验（draft→announced→revoked 单向，revoked 终态）。
- **改成什么样**：一张自洽的调整记录表，agenda/通知/提醒都从它派生，无第二事实来源。
- **验收标准**：
  - [ ] sqlite 与 postgres 双引擎建表成功且 `init_database` 幂等（重复执行无错）；
  - [ ] 索引存在（postgres 下 `\di` 验证，参照 postgres-migration 的索引经验）；
  - [ ] 无 BOOLEAN 列、无 `NOW()` 类引擎特有默认值。
- **测试形式与预估结果**：现有 schema 测试模式（`tests/test_dashboard_service_postgres.py` 同款双引擎夹具）下建表 + CRUD 冒烟；预估一次通过。
- **偏差处理**：若发现现有同步课表数据有稳定的课次主键（可外键引用而非冗余 source_* 字段），优先引用 + 保留 `source_course_name` 冗余兜底（同步数据可能被重新同步刷掉，冗余字段保证调整记录永远可读）；禁止只存外键不存冗余。

### F2. 教师发展记录表 `teacher_development_records`

- **为什么改**：D5 载体。独立于学生 portfolio 表——两者生命周期、权限、字段演化方向都不同，混表省一时、痛三年。
- **怎么改**：建表（同 F1 约定）：id、teacher_id（索引）、record_type TEXT、title TEXT NOT NULL、description TEXT、occurred_on TEXT、attachment_file_id INTEGER（引用现有文件表）、source TEXT DEFAULT 'manual'（manual | semester_auto，为 D5 二期自动沉淀预留）、created_at、updated_at。
- **验收标准 / 测试 / 偏差处理**：同 F1 模式；本表随 D5 后置，建表代码可与 D5 一并提交，不提前占位。

### F3. 明确不做的数据库改动（防跑偏声明）

- **不**为三域导航建任何配置表——导航是代码（A1 注册表），版本随发布走，进库只会造成"改导航要发版还是改数据"的双头问题。
- **不**为 301 映射建表——同上，A4 由注册表生成。
- **不**动既有表的列归属（如把 signatures 表改名挪 schema）——域是展示与路由层概念，数据层按实体组织，现状合理。
- **执行约束**：任何执行本档的 AI 如认为需要新增上述之外的表/列，必须先在 PR 描述中说明并等待人工确认，不得顺手建表。

## G. AI 调度与智能化

### G1. 平台知识注入按三域重构

- **为什么改**：`platform_knowledge_service.py` 向 AI 注入的平台功能知识若不更新，重构后 AI 会把教师指到 301 旧地址、用旧的"基础资源"话术，AI 指路质量随重构反向劣化。
- **怎么改**：
  1. 平台知识的功能清单部分改为**从 A1 nav 注册表生成**（域 → 分组 → 功能 → URL → 一句话说明），注册表每项增加 `ai_hint` 字段（给 AI 的功能描述，如"调停课：教师登记停课/调课/代课并通知学生"）。
  2. 注入文案显式声明三域心智："教师端分教学（管课堂）、教务（管学校事务数据）、教师（管自己）三域"，让 AI 回答"去哪做 X"时先报域再报页。
  3. `ui_copy_service.py` 增加三域相关 scene 文案（域名称、域简介、空状态文案），供模板与 AI 注入共用同一份措辞。
- **改成什么样**：问 AI"我想调课去哪"，答"教务域 → 调停课（/manage/academic/adjustments）"，链接即点即达。
- **验收标准**：
  - [ ] 注入知识中的全部 URL 为新 URL（自动来自注册表，无手写）；
  - [ ] 注册表每项 `ai_hint` 非空（测试断言）；
  - [ ] AI 工作台实测三个跨域指路问题回答正确。
- **测试形式与预估结果**：pytest 断言知识生成包含三域结构与新 URL、不含旧 URL 字符串；人工对 AI 工作台做 5 问抽测。预估一次通过，AI 回答质量抽测可能暴露 `ai_hint` 措辞需打磨 1 轮。
- **偏差处理**：若注入知识因功能清单全量展开而超出提示词预算（参照"chat platform query planning budget"的既有约束），按域做两级注入——常驻只注入三域概览，用户问到具体功能时由检索/工具补充明细；不要靠删功能描述硬塞。

### G2. agent-bridge 工具与调度的域适配

- **为什么改**：agent-bridge 只读工具（SQL/文件/联网）与 `agent_action_registry.py` 的平台动作注册是 AI 执行层；调停课等新实体若不注册，AI 无法回答"我下周有没有调课"。
- **怎么改**：
  1. `agent_action_registry.py` / `agent_platform_actions.py` 按既有模式注册调停课**只读查询**动作（`list_my_course_adjustments`）；写操作（创建调课）**不**开放给 agent——涉及对学生的广播通知，必须人在回路，页面表单是唯一写入口。
  2. agent 任务（如"帮我整理本周教务安排"）的产出中引用教务数据时，链接统一指向教务域新 URL（随 G1 注册表生成自然达成）。
  3. 调度侧无新增需求：调停课提醒走统一调度器（C3 已含），AI 不参与定时触发。
- **改成什么样**：AI 能查并汇总教师的调停课与教务安排，但不能替教师发布调课。
- **验收标准**：[ ] 新只读动作注册并在 agent 会话可调用；[ ] 确认动作注册表中无调停课写动作；[ ] agent 产出链接为新 URL。
- **测试形式与预估结果**：pytest 测动作注册与查询返回结构；agent 端到端人工抽测 1 例。预估一次通过。
- **偏差处理**：若未来确需 AI 代办调课草稿，开放"创建 draft"动作但发布（announced）永远保留人工点击——该红线写入动作注册处注释。

## H. 横切质量（美观性与一致性）

### H1. 三域视觉语言统一

- **为什么改**：换肤（A2）只换 accent 还不够"美观"，三域需要在统一设计语言（参照 `docs/frontend-premium-design-language.md`）内有可感知但不刺眼的身份差异。
- **怎么改**：polish 层为三域定义：域 Tab 激活渐变、域首页 hero 区微渐变背景（teaching 沿用现有 indigo 光晕、academic 用 teal、teacher 用 amber，复用 `.manage-layout::before` 的 radial-gradient 模式只换色值）、空状态插画色调跟随域色。全部通过 CSS 变量实现，禁止逐页硬编码色值。
- **验收标准**：[ ] 三域页面截图并排对比：同构、同间距、仅色彩身份不同；[ ] 暗色兼容性不回归（如有暗色变量体系则同步覆盖）；[ ] `prefers-reduced-motion` 下无新增动画问题。
- **测试形式与预估结果**：P03 三域各 1 张截图人工评审 + 既有页面像素回归。预估 1-2 轮调色。
- **偏差处理**：若 amber 作为教师域主色在大面积 UI 上观感刺眼（amber 通常只适合点缀），降级为"教师域用中性灰金 + amber 仅作 Tab 与 active 点缀"，与超管组拉开明度差即可。

### H2. 全链路回归清单（发布门槛）

- **为什么改**：本重构横跨导航/路由/双壳复用，回归面大，需要一份固定清单防漏测。
- **怎么改**：在 P03 harness 中固化教师端回归套件 `tests/e2e/teacher_three_domains.spec.ts`（命名按现有 harness 约定调整）：
  1. 三域 Tab 各页可达性遍历（从 nav 注册表生成页面清单，逐页 200 + 标题断言——注册表再次成为单一事实来源）；
  2. 全部 legacy URL 301 断言（与 pytest 重复一层，e2e 层验证浏览器真实跳转后页面可用）；
  3. 学生侧反向回归：学生 dashboard、profile、课堂页截图对比（确保教师端重构零外溢）；
  4. 移动端 390px：域 Tab、抽屉、教务时间线三张截图。
- **验收标准**：[ ] 套件全绿是每个实施阶段合入 main 的前置条件；[ ] 套件运行时间 < 5 分钟（控制截图数量）。
- **测试形式与预估结果**：套件本身即测试；首次建立预估 1 天，此后每阶段增量维护。
- **偏差处理**：若逐页遍历因页面依赖 seed 数据不足而批量失败，优先补 harness 的 seed（一次性投资），不要把失败页从清单剔除。

---

## 第四部分：实施阶段（执行顺序，每阶段独立可发布）

| 阶段 | 内容 | 依赖 | 可回滚性 |
|------|------|------|---------|
| **Phase 0：地基** | A1 导航注册表（行为零变化）+ E1 路由文件拆分 + H2 回归套件骨架 | 无 | 纯重构，git revert 即回滚 |
| **Phase 1：三域亮相** | A2 域 Tab 与换肤 + A4 路由迁移与 301 + B3/C4/D3 导航归位 + H1 视觉 | Phase 0 | 保留旧 URL 301，回滚=恢复注册表旧 domain 值 |
| **Phase 2：域首页** | B1 教学工作台 + C1 教务总览 + A3 dashboard 三域卡 + G1 AI 知识重构 | Phase 1 | 新页独立，回滚=导航摘除 |
| **Phase 3：教师域整合** | D1 profile 并入 + D2 我的概览 + D4 对接凭据页 + E2 权限收口 | Phase 1 | partial 抽取需谨慎，回滚=恢复 profile.html 单体 |
| **Phase 4：调停课** | F1 建表 + C3 全功能 + C2 空闲教室升级 + G2 agent 动作 | Phase 2（教务总览挂入口） | 新表新页，功能开关式回滚 |
| **Phase 5：发展档案（可选）** | F2 + D5 + B2 课堂设置集中化 | Phase 3 | 完全独立 |

每阶段执行流程（遵循仓库既有 playbook）：feature 分支 → 实现 → `npm run build` + `npx tsc --noEmit` + `node --check` → pytest 全套（双引擎）→ P03 回归套件 → code review（重点查：partial 是否单份、URL 是否走注册表、双引擎 SQL）→ 部署验证（参照 deploy-workflow，注意 postgres cutover gate）。

## 第五部分：全局偏差处理原则

1. **信息正确 > 展示优雅**：任何"数据可能不准"的场景（空闲教室、工作量统计），宁可显示数据范围声明或引导配置，绝不显示可能错误的数字。
2. **单一事实来源不可破**：导航=A1 注册表、agenda=`dashboard_agenda_events`、定时=统一调度器、表单=单份 partial。执行中任何"复制一份更快"的诱惑都按埋坑论处。
3. **降级有明确台阶**：每条的偏差处理给出的降级方案是**预先批准的**，执行 AI 可直接采用并在 PR 中注明；降级之外的方案变更需人工确认。
4. **学生侧零外溢**：所有阶段合入前必须通过 H2 第 3 项学生回归；学生端出现任何视觉/功能变化即视为阻断缺陷。
5. **审批流、细粒度权限、多角色体系**：本文档刻意全部不做（YAGNI），但 C3 状态机、E2 依赖工厂、F1 status 字段均已预留扩展点——未来需要时是"加分支"而不是"改结构"。
6. **性能红线**：单页新增查询合计 < 50ms（2c/4GB 服务器、200 并发的现实约束）；超标时一律降级为异步加载，不阻塞首屏。
