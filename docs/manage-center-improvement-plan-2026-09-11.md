# 管理端改进方案：首页与管理中心一体化（2026-09-11）

> 状态：**执行中**（分支 `feat/manage-center-unification`）。代码基线 `dev @ fae155fe`。
> 进度（2026-09-12）：P0 ✅ 全部；P1 ✅（首页进壳 / 收件箱 / 首页三段；S1 顶栏合一：教师所有壳内页同顶栏，学生与课堂页仍用旧顶栏）；P2 ✅ H1 学期阶段条、I2 审批深链、H4 教师「我的」并入壳（/manage/me/* 复用 profile 模板，/profile 与 /message-center 对教师 302）；P3 ✅ H2 归档流水线首页 `/manage/archive` + 壳内「第 k/9 步」步条、H3 教务日程周视图 `/manage/academic`；P4 ✅ 主体：页面宏 `macros/manage_page.html` 已建并迁 8 页页头；**全部 40 个管理模板内联 `<style>` 清零**（约 8,500 行迁入 `ui-system.src.css` pages layer，色值待令牌化）；`exams.html` 拆为 `partials/exams/*`（主模板 82 行）。剩：courses/materials 拆分、pages layer 令牌化、其余页面页头换宏；P5 清扫未开始。向导页保留为「开课向导」菜单项。
> 范围：教师登录后的全部工作面——`/dashboard` 教师分支、`/manage/*` 四域管理中心、`/profile`、`/message-center`，以及它们之间的跳转与数据共享。学生端零改动。
> 写法面向"其他 AI 直接执行"：每条改进给出 为什么 / 怎么改（含文件） / 验收 / 测试 / 偏差处理。执行顺序见第 8 节。
> 前置阅读：`docs/frontend-redesign-2026-08.md`（令牌与 shadcn 铁律）、`docs/ux-overhaul-2026-08.md`（少即是多验收标准）、`docs/teacher-portal-three-domain-restructure-goals.md`（上一轮三域重构，本方案是它的第二版，已落地的 A1/A2/A4/B3/C4/D3/E2 不再重复）。

---

## 0. 一页结论

**诊断一句话**：首页和管理中心是**两套壳子、三套视觉语言**；管理中心的 44 个菜单项按"数据表和实现时间"分组而不是按"教师要做的事"分组；教师的"待办"散在六个地方；"我"散在三个地方。用户感到"割裂"和"混乱"都是这两个根因的表象。

**五个改动方向**（按优先级）：

| # | 方向 | 一句话 | 主要收益 |
|---|---|---|---|
| 1 | **一个壳子** | 首页进入管理中心的壳，共用同一个顶栏/侧栏/令牌；`manage/layout.html` 800 行内联 CSS 迁出并令牌化 | 消灭"两个世界"的观感 |
| 2 | **按任务重组导航** | 四域改为 6 个按生命周期排列的域：首页 · 教学 · 资源库 · 成绩与归档 · 教务 · 我的（+ 超管"平台"） | 找东西不再靠猜 |
| 3 | **一个收件箱** | 新建 `work_inbox_service`，把批改/审批/签名申请/找回申请/课堂配置缺口/日程统一为一份"需要处理"，首页和"我的 → 待我处理"同源 | 教师一眼知道今天干什么 |
| 4 | **一个教学首页** | 课堂管理（classroom-hub）升格为教学域首页；开课向导降级为学期初才展开的阶段条 | 学期中不再打开"开课向导"这张空页 |
| 5 | **页面标准化** | 统一页头/筛选/列表/空态/抽屉宏，27 个内联 `<style>` 清零，3515 行的试卷页拆分 | 管理中心内部也长得一样 |

---

## 1. 调查范围与证据

### 1.1 核对的代码

- 导航真源 `classroom_app/services/manage_nav_service.py`（44 条 `ManageNavItem`，4 域，`build_dashboard_domain_cards`、`iter_manage_legacy_redirects`）
- 页面路由 `classroom_app/routers/ui_parts/manage_pages.py`（1280 行，46 个 `/manage` GET）
- 壳子 `templates/manage/layout.html`（1310 行，其中第 11–824 行是内联 `<style>`）
- 首页 `templates/dashboard.html` + `static/css/dashboard_workspace.css`（383 行，`dw-*` 体系）+ `templates/base_navbar.html` + `templates/partials/app_topbar_utility_actions.html`
- 首页数据 `classroom_app/services/dashboard_service.py`（quick_actions 第 1487 行）、`dashboard_workspace_service.py`（focus_items 来源）
- 个人中心 `classroom_app/services/profile_service.py`（`PROFILE_SECTIONS`、`nav_items`）、`templates/profile.html`
- 课堂总台 `offering_hub_service.py` / `offering_hub.html`；开课向导 `workflow.html` + `manage_workflow.js`（iframe 轮播）
- 审批 `approval_workflow_service.py`（通知走 message center；前端只挂在 `assignment_detail_teacher.html`）、签名审批 `/manage/me/signature-workflows`、找回 `/manage/me/password-resets`
- 测试契约 `tests/test_manage_nav_service.py`（12 条）、`tests/e2e/specs/teacher-three-domains.spec.ts`、路由快照 `tests/fixtures/p02_route_snapshot.json`

### 1.2 真实截图（P03 harness，超管教师 `qa_p03_super`，1440×900 与 390×844）

存于 `.codex-temp/manage-audit/*.png`（临时目录，不入库）：`dashboard`、`manage-teaching`、`manage-hub`、`manage-offerings`、`manage-exams`、`manage-materials`、`manage-lesson-plans`、`manage-academic`、`manage-library`、`manage-me`、`manage-users`、`profile`，以及移动端 `m-dashboard`、`m-manage-teaching`、`m-manage-hub`。全程 0 控制台错误。

### 1.3 量化证据

| 指标 | 数值 | 说明 |
|---|---|---|
| `manage/layout.html` 内联 CSS 中的 hex 色 | 23 处 | `--ls-*` 引用 **0** 处；首页 `dashboard_workspace.css` hex 为 0、全部走令牌 |
| 管理中心页面使用的页头模式 | ≥ 8 种 | `manage-pagehead+insight-board`（班级/课程/试卷/材料/教材/教室/签名）、`academic-hero`（开设课堂/AI/学期/向导）、`gwlist-hero`、`afm-hero`、`um-hero`、`edu-hero`、`smart-classroom-hero`、`cs-shell`、纯标题（教案/考核计划/评学表/投票/材料中心）、`manage-domain-grid` 数字卡（教务总览/我的概览） |
| 带内联 `<style>` 的管理模板 | 27 / 40 | 每页自带一套局部样式 |
| 超大模板 | `exams.html` 3515 行、`courses.html` 1532、`layout.html` 1310、`materials.html` 1179 | 全部超过 800 行红线 |
| `ui-system.src.css` 中管理端相关规则 | `.academic-*` 474、`.workflow-*` 186、`.manage-*` 114、`.insight-*` 42 | 与首页 `.dw-*`（独立文件）完全不共享 |
| 教师"回首页"的方式 | 3 种 | 侧栏"返回仪表盘"、顶栏品牌、顶栏"教学首页"按钮（每页都渲染） |
| 顶栏"管理中心"入口 | 2 个 | `base_navbar` 主按钮 + `app_topbar_utility_actions` 的"管理"，都指 `/manage` |
| 教师待办散布位置 | 6 处 | 首页"需要处理"（日程+批改）、课堂管理右栏"课堂待办"（缺教材/AI/排课）、作业详情内的撤回申请、`/manage/me/signature-workflows`、`/manage/me/password-resets`、消息中心 |
| "我"散布位置 | 3 处 | `/profile`（6 个 section）、`/manage/me`（概览+签名+审批+凭据+找回）、`/message-center` |

---

## 2. 现状基线

### 2.1 三套视觉语言并存

| 维度 | 首页 `/dashboard` | 管理中心 `/manage/*` | 个人中心 `/profile` |
|---|---|---|---|
| 壳 | `base_navbar.html`：单顶栏，无侧栏，1280px 居中 | `manage/layout.html`：左侧栏 + 独立顶栏 `manage-topbar`，全宽 | `base_navbar.html` + 页内左子导航 |
| 品牌文案 | 课堂互动平台 / 教师工作台 | 课堂平台 / 教师管理中心（侧栏）+ 课堂互动平台 / 教师管理中心（顶栏） | 课堂互动平台 / 教师工作台 |
| 主色 | 教师青绿 `--ls-primary`（role-teacher 覆盖） | 教学域靛蓝 `#4f46e5`、教务青 `#0f766e`、材料紫 `#9333ea`、管理琥珀 `#d97706`（硬编码在 nav_service 与 layout） | 青绿 + 深色统计卡 |
| 卡片语言 | 扁平、细边框、无渐变、`dw-*` | 玻璃渐变 hero、`insight-board` 环形图、大阴影、`academic-*` | 大头像 hero + 深色数字卡 |
| 顶栏动作 | 2–3 个药丸（开设课堂/管理/博客/消息/我的） | 每页 2–5 个 `topbar_action` 药丸 + 常驻"教学首页" | 同首页 |
| 移动端 | 顶栏折成 5 个药丸 | 顶栏 3 个大按钮占 1/3 屏 + 汉堡抽屉 | 子导航纵排 |

结论：教师从首页点"管理"后，**品牌名变了、主色变了、卡片风格变了、回家方式变了**。这就是"形式割裂"。

### 2.2 管理中心现有信息架构（44 项，按 `manage_nav_service` 原样）

```
教学 (teaching, 靛蓝)                        教务 (academic, 青)
 域首页(1)   教学工作台=开课向导 iframe 轮播      域首页(1)   教务总览（4 张数字卡）
 开课准备(3) 确认学期 / 开设课堂 / 配置 AI 助教    数据同步(2) 教务对接 / 智慧课堂(URL 在 /manage/teaching/)
 课堂运行(2) 课堂管理 / 课堂合并                  课表课时(1) 课时统计(URL 在 /manage/teaching/)
 教学对象(1) 班级                                场地(1)     教室查询
 内容资产(6) 课程 / 教材 / 试卷 / 教案 / 投票 / 材料  公文(2)     公文列表 / 公文同步
 过程材料(7) 考核计划表 / 评分细则表 / 平时成绩表 /
             考核登分表 / 期末成绩单 / 教师评学表 / 课后材料
 期末材料(2) 成绩登记表 / 试卷分析表

材料 (library, 紫)                           管理 (admin, 琥珀；全员可见)
 材料中心(1) 材料检索（左栏 14 个分类勾选）         我的资料(3)  我的概览 / 我的签名 / 签名审批与使用
                                              账号与安全(2) 对接凭据 / 账号找回
                                              平台管理(9,超管) 用户 / 组织 / 一言(URL 在 /manage/teaching/) /
                                                           反馈 / 博客管家 / AI 用量 / Agent Key / 监控 / 压测
```

不在菜单里但存在的教师页面：`/manage/students/{id}`（学生详情）、`/manage/system`（302 跳转）、`/manage/system/super-admin`、`/profile?section=*`（6 个）、`/message-center`、`/feedback-review`、`/classroom/{id}`。

### 2.3 教师业务线（本方案的组织依据）

按一个学期的真实时间轴梳理，每条线标注现在的落点：

```
学期初 ──────────────────────────────────────────────────────────────
  L1 开课线   教务同步(教务对接) → 确认学期 → 一键开课/开设课堂 → 绑教材 → 配 AI 助教 → 绑课次文档
             落点：教务域·教务对接 → 教学域·开课准备(3 页) → 课堂管理右栏"一键开课"
             断点：向导页是 iframe 轮播，只在学期初有用；"教材缺口/AI 缺口"只在课堂管理右栏出现，首页看不到

学期中 ──────────────────────────────────────────────────────────────
  L2 授课运行线  课次/材料/作业/考试/投票/分组/互动/签到/修为 → 批改 → 学生撤回申请审批 → 结课
             落点：/classroom/{id}（主战场）；课堂管理（总台）；投票在内容资产；批改入口只在首页课堂行"查看待批改"
             断点：撤回申请只在作业详情页有入口；结课在课堂页；课堂管理 run_status 与首页课堂列表各算各的
  L3 教务信息线  考试/监考日程 → 邮件提醒 → 课表/课时 → 教室 → 公文
             落点：首页 agenda（监考/考试）；教务总览（空数字卡）；课时统计/智慧课堂（URL 在 teaching）；公文两页
             断点：没有"我的教务日程"页，教务总览只有 4 个数；调停课未做

学期末 ──────────────────────────────────────────────────────────────
  L4 成绩与归档线  结课 → 平时成绩表 → 考核登分表 → 期末成绩单 → 教务双表(成绩登记表/试卷分析表) →
                考核计划表/评分细则表 → 教师评学表 → 课后材料归档
             落点：过程材料(7) + 期末材料(2) 两个分组，顺序与流程不一致（考核计划表排第一，实际是命题期做；教案在内容资产）
             断点：没有一条"流水线视图"；`?locate=` 深链已有但页面之间没有"上一步/下一步"

常年 ────────────────────────────────────────────────────────────────
  L5 资源沉淀线  课程模板/课次结构 → 教材 → 试卷/题库 → 学习文档(HTML 包/LessonDoc) → 教案 → 投票模板
             落点：教学域·内容资产(6) + 材料域·材料检索(14 类)
             断点：同一批资源在两个域出现两次；材料域一个搜索页占一个域 Tab
  L6 个人线   资料/头像/心情 → 密码 → 通知偏好 → 邮箱发信 → 签名 → 签名审批 → 对接凭据 → 学生找回申请
             落点：/profile(6) + /manage/me(5) + /message-center
             断点：/manage/me 概览是 /profile 的链接农场；概览"教学工作总览"四个数与首页重复
  L7 平台线（超管）  用户/组织/反馈/博客管家/AI 用量/Agent Key/监控/压测/一言
             落点：管理域"平台管理"组
             断点：与"我的资料"混装在一个域；域名"管理"与"管理中心"撞名
```

---

## 3. 症结诊断（根因，不是症状）

| 根因 | 证据 | 影响 |
|---|---|---|
| **R1 壳子独立**：`manage/layout.html` 自带 800 行内联 CSS 与自己的顶栏，不引用 `--ls-*`，硬编码 23 处 hex；首页用 `base_navbar.html` + 令牌化的 `dw-*` | 第 1.3 节 | 两边任何一次视觉调整都不会同步；用户看到"两个产品" |
| **R2 域色即品牌色**：`MANAGE_DOMAIN_META.accent` 用靛蓝/青/紫/琥珀四色硬编码，教学域主色靛蓝 ≠ 首页教师青绿 | `manage_nav_service.py` 第 10–36 行；`.manage-domain-teaching` | 从首页（青绿）进管理中心（靛蓝）第一眼就变色 |
| **R3 三个"教学首页"**：`/dashboard`、`/manage/teaching`（开课向导）、`/manage/teaching/classroom-hub`（课堂管理）各自宣称是入口；每页顶栏常驻"教学首页→/dashboard" | `workflow.html`、`offering_hub.html`、`layout.html` 第 986–992 行 | 学期中打开管理中心默认落在一张与当下无关的向导页 |
| **R4 按数据表分组**：过程材料/期末材料/内容资产的边界是"存在哪张表、哪个 commit 加的"，不是"教师什么时候做" | 2.2 与 2.3 对照 | 考核计划表排在成绩链最前、教案不在文档类、课后材料在成绩类 |
| **R5 待办碎片化**：首页 focus 只聚合 agenda + grading；课堂配置缺口、撤回审批、签名申请、找回申请、反馈各有自己的角落 | `dashboard_workspace_service.py` 第 336 行；`approval_workflow.js` 只在作业详情加载 | 教师不知道有申请在等他，直到打开那个作业 |
| **R6 "我"三分**：`/profile` 与 `/manage/me` 是同一批数据的两个壳；`/manage/me` 概览的"快捷入口"六张卡全部跳回 `/profile` | `me.html`、`profile_service.nav_items` | 改个资料要跳壳；上一轮 D1"复用 partial"没做完 |
| **R7 域划分失衡**：材料域 = 1 页；管理域 = 5 个人页 + 9 超管页；教学域 = 22 页 | `MANAGE_NAV_ITEMS` | 域 Tab 的心智负担不均；普通教师看到"管理"以为是后台 |
| **R8 URL 与域错位**：智慧课堂、课时统计在 `/manage/teaching/` 但属教务域；一言提示在 `/manage/teaching/life-tips` 但属平台管理 | 2.2 括注 | AI 指路与人工书签都会错 |
| **R9 页面各自为政**：8 种页头、27 个内联 style、4 个千行模板；筛选区从"1 个搜索框"到"10 个全宽下拉"（试卷页）不等 | 1.3 | 管理中心内部也不统一，"少即是多"改造没有覆盖到这里 |
| **R10 首页给管理中心的入口太弱**：底部"教学工具"是三个文字链"教学/教务/材料"+ 四个动词链；"管理"域没有入口；顶栏两个按钮都指 `/manage` | `dashboard.html` 第 100–104 行 | 首页与管理中心之间没有"带状态的桥" |

---

## 4. 用户到底需要什么

把教师一天/一学期的问题按频次排：

1. **今天要做什么？**（每天）——批改、审批、监考、上课、待办、配置缺口。需要**一个**列表。
2. **我的课堂现在怎么样？**（每周）——进度、下次课、活动、学生动态。需要课堂总台，且与首页课堂列表同一口径。
3. **东西在哪？**（每周）——课程/教材/试卷/文档/教案/公文。需要一个资源库 + 搜索，而不是两个域。
4. **把材料做出来交上去**（学期末集中）——成绩链与归档表要像流水线，能看到"做到第几步"。
5. **教务给我安排了什么？**（每周）——考试/监考/课表/教室/公文/调停课。需要"我的教务日程"。
6. **维护我自己**（低频）——资料/密码/签名/凭据/通知。需要一处。
7. **维护平台**（超管、低频）——独立、不打扰普通教师。

由此得出**设计原则**（本方案所有条目都要能追溯到其中一条）：

- P1 **单壳**：首页是壳内的一个页面，不是另一个壳。
- P2 **按任务分域，按时间排组**：域 = 教师的一类问题；组内顺序 = 教师做事的顺序。
- P3 **一个收件箱，处处同源**：所有"等我处理"的东西来自同一个服务。
- P4 **每个域一个真首页**：域首页回答该域的问题，不是数字卡。
- P5 **同构页面**：页头/筛选/列表/空态/抽屉五个宏，禁止内联样式。
- P6 **令牌单源**：颜色只来自 `--ls-*`；域身份只用一个 `--ls-domain-accent` 变量表达，且教学域 = 教师青绿。

---

## 5. 目标信息架构

### 5.1 壳子：AppShell 一体化

```
┌ 顶栏（唯一实现 partials/app_shell_topbar.html）──────────────────────────────┐
│ ☰(≤1024px)  课堂互动平台·教师工作台   [搜索 /]   当前页面▸面包屑   消息🔔  我的▾ │
├ 侧栏（/manage/* 与 /dashboard 都渲染，可折叠为 76px 图标栏）────┬───────────────┤
│ 首页                                                            │               │
│ ── 教学 ──   课堂管理 · 开课准备 · 班级                           │   页面内容     │
│ ── 资源库 ── 课程 · 教材 · 试卷 · 学习文档 · 教案 · 投票 · 材料检索 │  (统一页头宏)  │
│ ── 成绩与归档 ── (按流程 9 项)                                    │               │
│ ── 教务 ──   我的日程 · 教务对接 · 智慧课堂 · 课时统计 · 教室 · 公文 │               │
│ ── 我的 ──   个人首页 · 待我处理 · 资料 · 安全 · 通知与邮箱 · 签名 · 凭据 │           │
│ ── 平台 ──   (仅超管 9 项)                                       │               │
└──────────────────────────────────────────────────────────────┴───────────────┘
```

决策要点：

- **首页进壳**：`/dashboard` 教师分支改为 `{% extends "app_shell.html" %}`（`manage/layout.html` 重命名为 `templates/app_shell.html`，原路径保留一行 `{% extends "app_shell.html" %}` 兼容 40 个子模板）。学生分支不动，继续 `base_navbar.html`。
- **侧栏在首页默认折叠为图标栏**（记忆到既有 `localStorage['lanshare:manage-sidebar-collapsed']`），在管理页默认展开。这样首页保持宽松，但"世界"是同一个。
- **域 Tab 取消**：现在的四个域 Tab（教学/教务/材料/管理）改为侧栏中的**分组标题**（手风琴保留，一次只开一组）。理由：域从 4 变 6，Tab 放不下；且 Tab 让"域"看起来像四个 app。折叠图标栏下分组标题退化为分隔线。
- **顶栏动作规范**：每页最多 **1 个主操作**（实色）+ **1 个次操作**（描边），其余进"更多▾"菜单；常驻"教学首页"按钮删除（顶栏品牌 + 侧栏"首页"已足够）。移动端主操作变成页底悬浮按钮。
- **品牌文案统一**："课堂互动平台 / 教师工作台"，侧栏与顶栏同一份。

### 5.2 新导航树与 44 项映射表

新域定义（写入 `MANAGE_DOMAIN_META`；`accent` 字段删除，改为 `tone` 令牌名）：

| 域 key | 标签 | 回答的问题 | tone |
|---|---|---|---|
| `home` | 首页 | 今天要做什么、我的课堂怎么样 | `--ls-primary`（教师青绿） |
| `teaching` | 教学 | 课堂怎么运行、怎么开课、教谁 | `--ls-primary`（与首页同色，**不再靛蓝**） |
| `library` | 资源库 | 东西在哪、怎么找 | `--ls-info`（靛蓝转为资源库的身份色） |
| `archive` | 成绩与归档 | 材料做到第几步、交给谁 | `--ls-warning`（琥珀，学期末的"交付"感） |
| `academic` | 教务 | 教务给我安排了什么 | `--ls-teal`（保留现有教务青） |
| `me` | 我的 | 我自己的资料与等我处理的事 | `--ls-muted-foreground`（中性） |
| `admin` | 平台 | （超管）平台怎么样 | `--ls-destructive` 的低饱和变体，与其他域明显区分 |

44 项映射（`→` 后为新域 / 新组 / 新 URL；URL 变化的走 `legacy_hrefs` 自动 301，机制已在 `manage_redirects.py`）：

| 现条目 | 现域/组 | → 新域 / 新组 | URL |
|---|---|---|---|
| 教学工作台（开课向导） | 教学/域首页 | **删除为独立页**；并入课堂管理页顶部"学期阶段条"（见 5.5） | `/manage/teaching` → 301 到 `/manage/teaching/classroom-hub` |
| 课堂管理 | 教学/课堂运行 | 教学 / **域首页** | 不变 |
| 课堂合并 | 教学/课堂运行 | 教学 / 课堂运行（改为课堂管理页内"更多"菜单项 + 保留独立页） | 不变 |
| 确认学期 | 教学/开课准备 | 教学 / 开课准备 | 不变 |
| 开设课堂 | 教学/开课准备 | 教学 / 开课准备 | 不变 |
| 配置 AI 助教 | 教学/开课准备 | 教学 / 开课准备 | 不变 |
| 班级 | 教学/教学对象 | 教学 / 教学对象 | 不变 |
| （新）结课与成绩 | — | 教学 / 课堂运行：课堂管理卡片上的"结课"入口 + 结课后自动指向成绩链 | 复用 `/classroom/{id}` 结课 + `/manage/archive/...` |
| 课程 | 教学/内容资产 | **资源库** / 教学资源 | `/manage/library/courses`（301） |
| 教材 | 教学/内容资产 | 资源库 / 教学资源 | `/manage/library/textbooks`（301） |
| 试卷 | 教学/内容资产 | 资源库 / 教学资源 | `/manage/library/exams`（301） |
| 材料（学习文档） | 教学/内容资产 | 资源库 / 教学资源，**改名"学习文档"** | `/manage/library/materials`（301） |
| 教案 | 教学/内容资产 | 资源库 / 教学资源 | `/manage/library/lesson-plans`（301） |
| 投票 | 教学/内容资产 | 资源库 / 教学资源 | `/manage/library/polls`（301） |
| 材料检索 | 材料/材料中心 | 资源库 / **域首页**（分类 rail 变为页内筛选 chips，不再占侧栏） | `/manage/library` 不变 |
| 考核计划表 | 教学/过程材料 | **成绩与归档** / 命题与考核（第 1 步） | `/manage/archive/assessment-plans`（301） |
| 评分细则表 | 教学/过程材料 | 成绩与归档 / 命题与考核（第 2 步） | `/manage/archive/grading-rubrics`（301） |
| 平时成绩表 | 教学/过程材料 | 成绩与归档 / 成绩链（第 3 步） | `/manage/archive/ordinary-grade-records`（301） |
| 考核登分表 | 教学/过程材料 | 成绩与归档 / 成绩链（第 4 步） | `/manage/archive/exam-grade-records`（301） |
| 期末成绩单 | 教学/过程材料 | 成绩与归档 / 成绩链（第 5 步） | `/manage/archive/final-grade-transcripts`（301） |
| 成绩登记表 | 教学/期末材料 | 成绩与归档 / 教务归档（第 6 步） | `/manage/archive/academic-grade-registers`（301） |
| 试卷分析表 | 教学/期末材料 | 成绩与归档 / 教务归档（第 7 步） | `/manage/archive/academic-exam-analyses`（301） |
| 教师评学表 | 教学/过程材料 | 成绩与归档 / 教务归档（第 8 步） | `/manage/archive/teacher-evaluations`（301） |
| 课后材料 | 教学/过程材料 | 成绩与归档 / 归档（第 9 步） | `/manage/archive/postclass-materials`（301） |
| 教务总览 | 教务/域首页 | 教务 / **域首页 → 重做为"我的教务日程"** | `/manage/academic` 不变 |
| 教务对接 | 教务/数据同步 | 教务 / 数据同步 | 不变 |
| 智慧课堂 | 教务/数据同步 | 教务 / 数据同步 | `/manage/academic/smart-classroom`（301，修正错位） |
| 课时统计 | 教务/课表课时 | 教务 / 我的日程（并入日程页的一个视图，保留独立页） | `/manage/academic/course-schedule`（301） |
| 教室查询 | 教务/场地 | 教务 / 场地 | 不变 |
| 公文列表 | 教务/公文 | 教务 / 公文 | 不变 |
| 公文同步 | 教务/公文 | 教务 / 公文 | 不变 |
| （预留）调停课 | — | 教务 / 我的日程（上一轮 C3，本方案不实施，留槽位） | `/manage/academic/adjustments` |
| 我的概览 | 管理/我的资料 | **我的** / 域首页，**与 `/profile` overview 合并**（复用 partial） | `/manage/me` 不变；`/profile` 教师访问 302 到此 |
| （新）待我处理 | — | 我的 / 域首页第二段 + 独立页：统一收件箱全量视图 | `/manage/me/inbox` |
| 基础信息 / 账号安全 / 通知中心 / 私信 / 邮箱通知 | `/profile?section=*` | 我的 / 资料与安全 / 通知与邮箱（进壳，复用 profile partial） | `/manage/me/settings` 等（`/profile?section=x` 教师 302） |
| 我的签名 | 管理/我的资料 | 我的 / 签名 | 不变 |
| 签名审批与使用 | 管理/我的资料 | 我的 / 签名（审批部分并入"待我处理"，使用记录留此页） | 不变 |
| 对接凭据 | 管理/账号与安全 | 我的 / 资料与安全 | 不变 |
| 账号找回 | 管理/账号与安全 | 我的 / 待我处理（审批）+ 独立页保留 | 不变 |
| 用户/组织/反馈/博客管家/AI 用量/Agent Key/监控/压测 | 管理/平台管理 | **平台** / 平台管理（超管） | 不变 |
| 一言提示 | 管理/平台管理 | 平台 / 平台管理 | `/manage/system/life-tips`（301，修正错位） |

映射后各域条目数：首页 1 · 教学 6 · 资源库 7 · 成绩与归档 9 · 教务 7 · 我的 8 · 平台 9。教学域从 22 降到 6，是本次"梳理"的核心成果。

### 5.3 统一收件箱：`work_inbox_service`

新建 `classroom_app/services/work_inbox_service.py`，**只聚合、不新建表**，来源注册表（不可变 tuple，仿 `agent_action_registry` 模式）：

| source key | 来源函数（已存在） | 条目类型 | 动作深链 |
|---|---|---|---|
| `grading` | `dashboard_workspace_service` 的 grading source | 待批改 N 份（按课堂） | `/classroom/{id}#assignment-panel` |
| `approval` | `approval_workflow_service` 按审批人列 pending | 作业撤回重做申请 | 作业详情 + 抽屉自动打开（`?approval=id`） |
| `signature_request` | `signatures` 的 `/requests` 待审 | 签名使用申请 | `/manage/me/signature-workflows?request=id` |
| `password_reset` | `password_resets` 待处理 | 学生找回申请 | `/manage/me/password-resets` |
| `offering_gap` | `offering_hub_service` 的待办（缺教材/缺 AI/未排课） | 课堂配置缺口 | `/manage/teaching/offerings?offering_id=x` |
| `agenda` | `dashboard_agenda_events`（监考/考试/待办） | 今日与近期 | 既有 popover |
| `feedback`（超管） | `system_feedback` 未处理 | 用户反馈 | `/manage/system/feedback` |

接口：`build_work_inbox(conn, user, *, limit=8) -> {"items": [...], "counts": {source: n}, "total": n}`；每个来源 `try/except` 独立降级（同 material_hub 原则：单来源异常不拖垮整份收件箱）。

消费方：首页"需要处理"（替换现有 focus_items 的教师分支）、我的域首页第二段、`/manage/me/inbox` 全量页、顶栏铃铛角标（`counts.total` 与未读消息分别显示）、AI 平台知识（`iter_platform_manage_routes` 追加"待我处理"路径）。

### 5.4 首页重构（教师分支）

页面三段，全部在壳内，宽度跟随壳（侧栏折叠时内容区约 1360px）：

1. **今天**：问候 + 日期 + `work_inbox` 前 6 条（类型 chip 用 5.2 的域 tone）+ "全部待处理 (N)" → `/manage/me/inbox`；右侧保留日程按钮、新增待办、订阅日历。
2. **我的课堂**：现有课堂列表保留，但**每行状态改为读 `offering_hub_service` 的 run_status / 下次课 / 待批改**（同源），行尾"进入课堂"+"管理"两个动作；3D 课表模式保留；空态引导指向开设课堂。
3. **去哪里**：六张域卡（教学/资源库/成绩与归档/教务/我的/平台[超管]），每张卡 = 域标签 + 一行实时摘要（教学："3 个课堂进行中，2 个缺教材"；成绩与归档："本学期 4 门课，成绩链完成 1/4"；教务："明天 14:00 监考"；我的："2 条申请待处理"）+ 2 个高频入口。摘要来自 `build_dashboard_domain_cards` 扩展的 `summary` 字段，查询合计 < 50ms，超标改异步 `/api/dashboard/domain-summaries`。

删除：底部"教学工具"文字链区、`dashboard_quick_actions` 教师分支（其四个链接全部并入域卡）、"评价"下拉移入课堂行的"教学评价"按钮旁（已有）。

### 5.5 教学域首页 = 课堂管理

- `/manage/teaching` 301 → `/manage/teaching/classroom-hub`；`workflow.html` 与 `manage_workflow.js`（iframe 轮播）**删除**，`.workflow-*` 186 条 CSS 随之清理。
- 课堂管理页顶部新增**学期阶段条**（`teaching_stage_service`，阈值：学期开始前 2 周至开始后 2 周 = 学期初；结束前 3 周至结束后 2 周 = 学期末；其余 = 学期中；常量集中定义）：
  - 学期初：展开"开课清单"（学期 ✓ / 课程 ✓ / 教材 ✗ / 班级 ✓ / AI ✗ + 一键开课候选 N 个），每项一个按钮；这就是原向导的全部信息，不再需要 iframe。
  - 学期中：折叠为一行"本学期第 N 周 · 3 个课堂进行中 · 展开开课清单"。
  - 学期末：折叠为一行 + "去成绩与归档：完成 1/4" 深链。
- 教师引导弹窗（`teacher_onboarding_modal`）保留作为"逐步开设"向导（它本来就是真正的向导），阶段条的"开设新课堂"按钮打开它。

### 5.6 成绩与归档域：流水线视图

- 域首页 `/manage/archive`（新，薄页）：按课堂一行，九步用 `insight-meter` 风格的步进条显示每步状态（未开始/进行中/已导出/已归档），点击某步深链到对应页并带 `?offering_id=`。数据来自各表已有的"按课堂"查询（`grade_materials_chain` 的 export_payload 状态、lesson_plans/assessment_plans/teacher_evaluations 的 `class_offering_id`），聚合在 `archive_pipeline_service.py`。
- 九个子页页头统一加"第 k/9 步 · 上一步 / 下一步"导航（读注册表顺序，纯模板宏，不改业务）。
- 侧栏组内顺序严格按流程（5.2 表的步号）。

### 5.7 教务域首页 = 我的教务日程

- 重做 `/manage/academic`：周视图时间线（复用 `dashboard_agenda_events` 教师分支的监考/考试/上课事件 + 课时统计的课表事件），顶部筛选 chips（考试/监考/上课/公文），右栏三张状态卡（教务对接最近同步、智慧课堂最近同步、公文最近同步，三态：从未/正常/失败，失败带"去处理"）。
- "查看日程 → /dashboard#dashboard-semester"这类跨壳跳转删除，因为已同壳。
- 调停课留槽位不实施。

### 5.8 我的域：合并 profile

- 上一轮 D1 的未完成部分：把 `templates/profile.html` 的六个 section 抽成 `templates/partials/profile_sections/*.html` + `static/js/profile_sections.js`（ES module、幂等初始化），`/profile`（学生）与 `/manage/me/*`（教师）都 include 同一份。
- `/manage/me` 域首页 = 名片（头像/心情/完整度）+ **待我处理**（`work_inbox` 前 8 条）+ 配置健康度清单（头像/邮件提醒/签名/教务凭据四项，全绿则整卡隐藏）。删除现在的"教学工作总览"四个数（首页已有）和"快捷入口"六张卡（侧栏已有）。
- 教师访问 `/profile*` 一律 302 到 `/manage/me/*` 对应 section；学生路径零改动。

### 5.9 资源库域：内容资产 + 材料中心合一

- 域首页 `/manage/library` = 现材料中心，但左栏 14 个分类勾选**迁到页内**成为 chips（默认全选逻辑、URL 同步、AI 搜索全部保留，`material_hub.js` 只改选择器）。侧栏恢复为普通菜单：课程 / 教材 / 试卷 / 学习文档 / 教案 / 投票 / 材料检索。
- `layout.html` 中 `domain.key == 'library'` 的特殊分支删除。

### 5.10 页面标准（Manage Page Standard，所有 /manage 页遵守）

新增 `templates/macros/manage_page.html`：

| 宏 | 职责 | 替代 |
|---|---|---|
| `page_head(title, description, primary_action, secondary_action, more_actions, insights)` | 标题 + 一句描述 + ≤2 个动作 + 可选 `insight_*` 图表条 | 8 种 hero |
| `filter_bar(search, chips, selects, more)` | 1 个搜索框 + ≤3 个可见筛选 + "更多筛选"折叠 | 试卷页 10 个全宽下拉 |
| `list_section(title, count, toolbar)` / `card_grid` / `data_table` | 列表容器 | 各页自定义 |
| `empty_state(title, description, action)` | 空态（遵循 `is-empty` 降级模式） | 各页自定义 |
| `drawer(id, title)` | 右侧抽屉（复用课堂管理的编辑抽屉实现，含 `[hidden]{display:none}` 守卫） | 各页模态 |

规则：新页必须用宏；存量页按第 8 节阶段逐页迁移，**迁一页删一页内联 `<style>`**（与 shadcn 迁移铁律一致）。

---

## 6. 视觉统一方案

### 6.1 令牌与 CSS 落点

| 动作 | 文件 | 说明 |
|---|---|---|
| 把 `layout.html` 第 11–824 行内联 CSS 迁出 | → `static/css/ui-system.src.css` 尾部新增 `/* app-shell layer */` | 23 处 hex 全部替换为 `hsl(var(--ls-*))`；`.manage-domain-*` 的四色改为读 `--ls-domain-accent` |
| 域身份变量 | `ui-system.src.css`：`.app-shell[data-domain="teaching"]{--ls-domain-accent: var(--ls-primary)}` 等 6 条 | 侧栏 active、页头强调线、域卡左边条只引用这一个变量 |
| `dashboard_workspace.css` 并入 | → `ui-system.src.css` 同一层，类名 `dw-*` 改 `ls-*`（页面级前缀取消） | 首页与管理页共用按钮/输入/列表基元；`.dw-button` 与 `.btn` 二选一保留 `.btn`（shadcn 风格） |
| 删除 | `.workflow-*`（186 条）、`.academic-hero*`、各页 `<style>`（分阶段） | 每删一组跑一次 P03 截图集 |
| 顶栏 | 新 `templates/partials/app_shell_topbar.html`，`base_navbar.html` 教师分支与 `layout.html` 顶栏都换成它 | 学生分支保留原顶栏 |

### 6.2 组件对照（现 → 目标）

| 场景 | 现状 | 目标 |
|---|---|---|
| 页头 | 玻璃渐变 hero + 大圆角 + 阴影 | 白底、1px 下边线、标题 24px、描述 14px 灰、右侧动作 |
| 统计/图表 | `insight-board` 环形图四联 | 保留 `insight_*` 宏但缩为一行 `insight-strip`（高 72px），只在有数据时渲染（`is-empty` 降级） |
| 卡片 | 圆角 18–24px、多层阴影 | 圆角 `--radius-lg`、1px 边框、hover 才起 1 级阴影（与首页 `.dw-course-row` 一致） |
| 按钮 | `.btn-primary` 靛蓝 / `dw-button` 青绿 / `topbar_action` 五种 tone | 主按钮 = `--ls-primary`（青绿），次按钮描边，危险红；`topbar_action` 的 tone 参数废弃 |
| 筛选 | 全宽 select 堆叠 | `filter_bar` 宏：chips 优先（已有 `manage_filter_chips.js`），select 只在 ≥ 6 个值时使用 |
| 空态 | 各页文案与图形不一 | `empty_state` 宏，一句话 + 一个按钮 |
| 侧栏 | 靛蓝 active 底 + 计数 chip | active = 左侧 3px `--ls-domain-accent` 竖线 + 加粗，取消底色块；计数 chip 只在收件箱类条目显示 |

### 6.3 移动端（≤ 1024px）

- 侧栏 → 抽屉（已有）；顶栏只留 汉堡 / 品牌 / 铃铛 / 我的 四项，页面主操作变为右下悬浮按钮（复用 AI 悬浮按钮的定位，上下叠放）。
- 首页与课堂管理卡片单列；`filter_bar` 的 chips 横向滚动；成绩链步进条改纵向。

---

## 7. 改进条目清单（执行单元）

编号：S=壳子 · N=导航 · I=收件箱 · H=域首页 · P=页面标准化 · V=视觉 · M=移动端。每条独立可验收。

### S1 顶栏合一
- **改**：新建 `partials/app_shell_topbar.html`（品牌 / 搜索 / 面包屑 / 铃铛 / 我的菜单 / 主次动作 slot）；`base_navbar.html` 教师分支与 `layout.html` 第 961–993 行改 include；删除常驻"教学首页"按钮与重复"管理"药丸。
- **验收**：首页与任一管理页顶栏 DOM 结构相同（Playwright 断言两页 `header.app-topbar` 的子节点 class 序列一致）；学生首页顶栏截图与改前像素一致。
- **测试**：`tests/e2e/specs/teacher-three-domains.spec.ts` 增加顶栏同构断言；学生 `home-classroom-ui-v3.spec.ts` 全绿。
- **偏差**：若 `global_search.js`、`message_center_bell.html` 在新顶栏挂载顺序冲突，优先保留其 `data-*` 钩子不改 JS。

### S2 壳子 CSS 令牌化与迁出
- **改**：`layout.html` 内联 CSS → `ui-system.src.css` app-shell 层；hex → `--ls-*`；`MANAGE_DOMAIN_META.accent` 字段删除，改 `tone`；`.manage-domain-{key}` 只设置 `--ls-domain-accent`。
- **验收**：`grep -c "#[0-9a-fA-F]\{3,6\}" templates/manage/layout.html` = 0；`grep -c "<style" templates/manage/layout.html` = 0；`npm run build:css` 通过；P03 15 张截图与改前对比仅色相差异。
- **偏差**：`managePageEnter` 入场动画必须保持 `animation-fill-mode: backwards`（历史坑：forwards 会劫持 `position:fixed` 弹窗）。

### S3 首页进壳
- **改**：`dashboard.html` 拆为分发 + `dashboard_teacher.html`（`extends "app_shell.html"`）+ `dashboard_student.html`（原样）；`ui_parts/dashboard.py` 为教师注入 `manage_nav=build_manage_nav(user, "home")`；侧栏在 `home` 域默认折叠。
- **验收**：教师首页出现侧栏（折叠态）；`/dashboard` 与 `/manage/teaching/classroom-hub` 之间切换无品牌/主色变化；学生首页零变化（截图 diff）。
- **测试**：`dashboard-schedule.spec.ts`、`dashboard-todo-modal.spec.ts` 全绿（它们依赖的 `data-dashboard-root` 等钩子不变）。

### N1 导航注册表重组
- **改**：`manage_nav_service.py`：`MANAGE_DOMAIN_ORDER = ("home","teaching","library","archive","academic","me")`，admin 追加；44 条按 5.2 表改 `domain/group/href/legacy_hrefs`；`build_dashboard_domain_cards` 改为 6 域并加 `summary` 回调；`iter_platform_manage_routes` 自动跟随。
- **验收**：`tests/test_manage_nav_service.py` 更新契约（`test_smart_classroom_and_course_schedule_live_under_academic`、`test_life_tips_lives_under_platform_admin`、`test_library_domain_hosts_material_hub_and_categories` 等按新树改写）；每条 `legacy_href` 301 且 Location 正确（参数化测试已有）；路由快照 `p02_route_snapshot.json` 重生成；`grep -rn` 全仓旧 URL 零命中（`manage_redirects.py` 与测试除外）。
- **偏差**：AI 平台知识引用的路径由注册表生成，无需手改；若 `codex/agent-dsh-migration` 分支的 Agent 路由层缓存了旧路径，只需重启 agent-worker。

### N2 URL 域化与路由文件拆分
- **改**：`manage_pages.py` 为 5.2 表中"301"条目增加新 canonical 路由（装饰器叠加，与现有 legacy 双装饰器同模式），旧路径进 `legacy_hrefs`；顺势按域拆为 `manage_pages_{teaching,library,archive,academic,me,admin}.py`（上一轮 E1，纯移动）。
- **验收**：同 N1；`git diff` 函数体零变更；每文件 < 400 行。

### I1 `work_inbox_service`
- **改**：新建服务（5.3），来源注册表 + 独立降级；`GET /api/work-inbox`（分页、按 source 过滤）；首页教师 focus 改读它；顶栏铃铛角标加"待处理 N"。
- **验收**：单测 `tests/test_work_inbox_service.py`：七个来源各 seed 一条 → 合并顺序（逾期 > 今日 > 审批 > 批改 > 缺口 > 其余）、单来源异常不影响其余、超管才见 feedback、学生调用返回 403。
- **性能**：七个来源合计 < 50ms（sqlite seed 计时断言；postgres 上线前在真 PG 复测）。
- **偏差**：若 `approval_workflow_service` 缺"按审批人列待办"的查询，在该服务内加薄函数，不在 inbox 里写 SQL。

### I2 待我处理页与作业审批入口
- **改**：`/manage/me/inbox` 全量页（`filter_bar` 按来源 chips + `list_section`）；作业详情页支持 `?approval=<id>` 自动打开审批抽屉（`approval_workflow.js` 读 query）；签名审批页支持 `?request=<id>` 定位。
- **验收**：e2e：seed 一条撤回申请 → 首页"需要处理"出现 → 点击 → 作业详情抽屉已打开并可批准 → 回首页条目消失。

### H1 教学域首页切换（5.5）
- **改**：`/manage/teaching` 301；删除 `workflow.html`、`manage_workflow.js`、`.workflow-*` CSS、`workflow_snapshot` 构建函数；新增 `teaching_stage_service.py`（阶段判定 + 开课清单，复用 `build_offering_bootstrap_candidates` 与现有向导数据函数）；`offering_hub.html` 顶部插入阶段条 partial。
- **验收**：三种阶段 seed（学期开始前 1 周 / 第 8 周 / 结束后 1 周）下阶段条文案与展开态正确；无学期数据时显示"先确认学期"；开课清单四项勾选状态与对应页一致。
- **偏差**：向导 iframe 轮播的 `embedded_mode` 机制**保留**（课堂管理编辑抽屉仍用它），只删轮播页本身。

### H2 成绩与归档流水线（5.6）
- **改**：`archive_pipeline_service.py` + `/manage/archive` 页 + 九个子页页头的"第 k/9 步"宏。
- **验收**：seed 一个已结课课堂并生成平时成绩表 → 流水线显示 3/9；点击第 4 步深链到考核登分表且 `offering_id` 预选。
- **偏差**：某步状态无法从现有表推导（如评分细则表未绑课堂）时显示"未关联"而非"未开始"，不猜。

### H3 我的教务日程（5.7）
- **改**：`academic_home_service.py`（周窗口切分 + 三态同步状态）+ 重写 `academic_overview.html`；课时统计页保留，日程页的"课表"视图以 partial 复用 `course_schedule_deck.js`。
- **验收**：事件与首页 agenda 同源同数（同一函数，单测断言两处输出集合相等）；周切换不整页刷新；三态状态卡渲染正确。

### H4 我的域合并 profile（5.8）
- **改**：抽 `partials/profile_sections/*.html` + `profile_sections.js`；`/manage/me/{settings,security,notifications,private,email}` 路由；`/manage/me` 首页重做；教师 `/profile*` 302。
- **验收**：学生 `/profile` 截图像素回归；教师六个 section 在新壳下头像上传/改密/邮件测试发送可用；`me.html` 不再含"教学工作总览"与"快捷入口"。
- **偏差**：section JS 与壳的 `handleFormSubmit` 冲突时模块化 profile 脚本，**禁止** iframe 嵌 profile。

### H5 资源库合一（5.9）
- **改**：分类 rail 迁入 `material_hub.html` 成 chips；`layout.html` 删 library 特殊分支；六个资源页归入资源库域。
- **验收**：材料检索的勾选/URL 同步/AI 搜索 e2e 不变；侧栏在资源库域显示 7 个普通条目。

### P1 页面宏与首批迁移
- **改**：`macros/manage_page.html` 五个宏；首批迁移 **课堂管理、开设课堂、班级、课程、教材、学期、AI 助教**（教学域 + 资源库高频页），每页删内联 `<style>`。
- **验收**：迁移页 `grep -c "<style"` = 0；页头/筛选/空态 DOM 结构一致（Playwright 对比 `[data-page-head]` 子节点）；功能 e2e 全绿。

### P2 超大页面拆分
- **改**：`exams.html`（3515 行）拆为 `exams.html` + `partials/exams/{filters,list,editor,question_bank,assign}.html`，JS 已是模块无需动；`courses.html`、`materials.html` 同法；筛选区改 `filter_bar`（试卷页 10 个下拉 → 搜索 + 状态/分配/来源 3 个 chips 组 + "更多筛选"）。
- **验收**：每文件 < 800 行；试卷页首屏筛选控件 ≤ 4 个；既有试卷 e2e（`teacher-review-ai`、`assignment-*`）全绿。

### P3 第二批与收尾迁移
- **改**：成绩与归档 9 页、教务 7 页、我的 8 页、平台 9 页逐页套宏、删内联样式；`.academic-hero*`、各 `*-hero` CSS 清理。
- **验收**：`grep -c "<style" templates/manage/**/*.html` 全部为 0；`ui-system.src.css` 中 `.academic-hero`、`.gwlist-hero`、`.afm-hero`、`.um-hero`、`.edu-hero`、`.smart-classroom-hero` 零引用后删除。

### V1 令牌与组件对照落地
- 第 6.1/6.2 表逐项执行，与 S2/P1/P3 同步推进；`tone` 参数从 `topbar_action` 宏移除（宏签名保留可选参数一版，下一版删）。

### M1 移动端
- 第 6.3；验收：390px 下首页、课堂管理、试卷页、我的首页四张截图无横向溢出，主操作悬浮按钮可点，抽屉可开合。

---

## 8. 实施阶段（每阶段独立可发布、可回滚）

| 阶段 | 内容 | 依赖 | 交付物 / 回滚 |
|---|---|---|---|
| **P0 地基（1 周）** | S2 壳子 CSS 迁出令牌化 + N1/N2 注册表重组与 URL 301 + 测试契约更新 + H5 资源库合一 | 无 | 视觉只变色相、结构不变；回滚 = revert 注册表 |
| **P1 一体化（1 周）** | S1 顶栏合一 + S3 首页进壳 + I1 收件箱服务 + 首页三段重构（5.4） | P0 | 首页与管理中心同壳；回滚 = 首页改回 `base_navbar` |
| **P2 域首页（1–2 周）** | H1 教学首页切换 + 阶段条 + I2 待我处理页与审批入口 + H4 我的域合并 | P1 | 删除向导页；回滚 = 恢复 `/manage/teaching` 路由与 workflow 文件（git） |
| **P3 归档与教务（1 周）** | H2 成绩链流水线 + H3 我的教务日程 | P0 | 新页独立；回滚 = 导航摘除 |
| **P4 页面标准化（2 周，可分批发布）** | P1 宏与首批 7 页 → P2 三个大页拆分 → P3 收尾 33 页 + V1 + M1 | P1 | 逐页发布；每页可单独回滚 |
| **P5 清扫** | 删除 `.workflow-*`、`*-hero` CSS、`dashboard_workspace.css`、`topbar_action` tone；更新 `docs/frontend-redesign-2026-08.md` 期数（"P5 管理中心"标记完成）、`docs/ai-and-agent-engineering-standard.md` 平台路由索引、记忆 | P4 | 纯删除 |

每阶段执行流程：feature 分支 → 实现 → `node --check` / Jinja parse / `npm run build` / `npx tsc --noEmit` → `python -m unittest discover -s tests -t .`（sqlite）→ P03 截图集（15 张桌面 + 4 张移动）→ 路由快照重生成 → code review（查：partial 单份、URL 走注册表、hex 零新增、内联 style 零新增）→ 真 PG 验证 → 部署（`deploy-workflow`）→ push。

---

## 9. 验收与回归

### 9.1 自动化契约
- `tests/test_manage_nav_service.py`：六域顺序、每域条目集合、成绩链九步顺序、`home` 域只含首页、admin 过滤、legacy 301 全量、平台知识路径。
- `tests/test_work_inbox_service.py`：来源合并顺序、独立降级、权限、性能计时。
- `tests/test_teaching_stage_service.py`：三阶段边界、无学期、开课清单四项。
- `tests/test_archive_pipeline_service.py`：九步状态推导、未关联不猜。
- `tests/test_architecture_route_snapshot.py`：快照重生成后全绿。
- e2e：`teacher-three-domains.spec.ts` 改写为 `teacher-app-shell.spec.ts`（六域可达遍历从注册表生成、顶栏同构、首页收件箱端到端、301 浏览器真跳转）；学生反向回归 `home-classroom-ui-v3.spec.ts`、`classroom.spec.ts`、`message-center.spec.ts` 必须零变化。

### 9.2 人工验收（每阶段）
- 15 张桌面 + 4 张移动截图并排对比：同构、同间距、仅域色不同。
- "从首页 → 管理 → 回首页"三步内品牌/主色/顶栏零变化。
- 学期中登录：第一屏能回答"今天要做什么"和"课堂怎么样"，不出现向导。

### 9.3 性能红线
- 首页新增查询合计 < 50ms；收件箱 < 50ms；域卡摘要超标即异步。
- CSS 总量：`tailwind-app.css` 体积在 P5 后应**下降**（删除 `.workflow-*`/`*-hero`/`dashboard_workspace.css` 抵消 app-shell 层新增）。

---

## 10. 明确不做（YAGNI）

- 不引入新角色/细粒度权限；`require_teacher_domain` 收口点保留即可。
- 不做调停课（C3）与教师发展档案（D5）；只留导航槽位与 URL 约定。
- 不合并学生端壳；学生首页、课堂页、profile 零改动。
- 不换架构（仍是 FastAPI + Jinja + islands），不新增 React 壳；侧栏/顶栏继续是服务端 partial + 小型 JS。
- 不新建数据库表；收件箱、流水线、阶段条全部是聚合层。
- 不重写课堂页 `/classroom/{id}`（它属于授课运行线，另有 home-classroom 系列文档）。

---

## 11. 风险与偏差处理

| 风险 | 处理 |
|---|---|
| `dashboard.html` 进壳后 `data-dashboard-root` 等 island 钩子被壳的 CSS 影响（宽度/`min-width:0`） | 首页内容区加 `.app-shell-page--home` 作用域，`dw-*`→`ls-*` 改名时保留旧类名一版作别名 |
| 域 Tab 取消后普通教师看到 6 个分组会觉得"更多了" | 手风琴一次只开一组 + 首页折叠侧栏；分组标题带一行副标（"东西在哪"/"材料做到第几步"），用 `data-explain` 浮窗不常驻 |
| 301 影响 Agent 数字分身的路由缓存 | 路由能力层按注册表实时解析（`route.*`），重启 agent-worker 即可；部署清单加一行 |
| `exams.html` 拆分引入回归 | 先拆不改：partial 边界按现有 `<section>` 切，JS 零改动，e2e 全绿后再动筛选区 |
| 收件箱把"审批"暴露到首页后申请量可见性提高，教师觉得打扰 | 收件箱条目可"稍后"（本地 24h 静默，localStorage，不落库） |
| 教学域主色从靛蓝改青绿引起"变淡"观感 | 域身份用 3px 竖线与页头强调线表达，不靠大面积底色；资源库接手靛蓝，视觉总量不变 |

---

## 附录 A：影响文件一览

| 类别 | 文件 |
|---|---|
| 新增 | `templates/app_shell.html`、`templates/partials/app_shell_topbar.html`、`templates/macros/manage_page.html`、`templates/partials/profile_sections/*.html`、`templates/partials/teaching_stage_bar.html`、`templates/manage/archive_pipeline.html`、`templates/manage/work_inbox.html`、`templates/dashboard_teacher.html`、`templates/dashboard_student.html`、`classroom_app/services/{work_inbox_service,teaching_stage_service,archive_pipeline_service,academic_home_service}.py`、`classroom_app/routers/ui_parts/manage_pages_{teaching,library,archive,academic,me,admin}.py`、`static/js/profile_sections.js`、`tests/test_{work_inbox_service,teaching_stage_service,archive_pipeline_service}.py`、`tests/e2e/specs/teacher-app-shell.spec.ts` |
| 重写 | `classroom_app/services/manage_nav_service.py`（域/组/URL/tone/summary）、`templates/manage/layout.html`（→ 一行 extends）、`templates/manage/academic_overview.html`、`templates/manage/me.html`、`templates/manage/material_hub.html`（rail→chips）、`templates/dashboard.html`（分发）、`static/css/ui-system.src.css`（app-shell 层 + 删除层） |
| 删除 | `templates/manage/workflow.html`、`static/js/manage_workflow.js`、`static/css/dashboard_workspace.css`（并入后）、`.workflow-*` / `*-hero` CSS、`MANAGE_DOMAIN_META.accent` |
| 拆分 | `templates/manage/exams.html`、`courses.html`、`materials.html`、`classroom_app/routers/ui_parts/manage_pages.py` |
| 契约更新 | `tests/test_manage_nav_service.py`、`tests/fixtures/p02_route_snapshot.json`、`tests/e2e/specs/teacher-three-domains.spec.ts`（改名） |

## 附录 B：调查复现命令

```bash
# 壳子令牌漂移
sed -n 11,824p templates/manage/layout.html | grep -c "#[0-9a-fA-F]\{3,6\}"   # 23
sed -n 11,824p templates/manage/layout.html | grep -c "\-\-ls-"               # 0
# 内联样式页数
grep -c "<style" templates/manage/*.html templates/manage/system/*.html | grep -v ":0" | wc -l   # 27
# 页头模式
for f in templates/manage/*.html; do grep -o 'class="[a-z-]*\(hero\|pagehead\)[a-z-]*"' "$f" | sort -u; done
# 截图（P03 harness 起在 8023 后）
node .codex-temp/manage-audit/shoot.mjs
```
