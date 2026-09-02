# LanShare 微信小程序端 — 工程实施推进详细规划（2026-08-31）

> 目的：小程序已正式上线（v0.9.5，当前体验版 v0.9.6），但功能推进节奏散乱——每批做什么靠临时起意，没有对照平台业务全景的差距清单和分期路线。本文档：
> 1. 整理平台现有业务链条与逻辑结构；
> 2. 比对小程序现状，给出差距与价值排序；
> 3. 拆分实现步骤，形成分里程碑的实施计划与工程推进机制。
>
> 本文是**推进真源**：每个里程碑完成后在此勾选、记录版本号。原架构设计见 [DESIGN.md](DESIGN.md)（技术选型/认证/部署仍有效，不重复）。

---

## 1. 平台业务链条全景（比对基准）

平台 = FastAPI 单后端，Web 端（Jinja + islands）与小程序共用。按角色梳理业务链条：

### 1.1 学生端业务链

```
登录 → 首页(欢迎语/议程/统计/3D课表) → 课堂
  ├─ 作业考试：列表 → 作答(草稿/附件/倒计时) → 提交 → 互评(小组) → 成绩/批语 → 错题本
  ├─ 课堂现场：签到 → 课堂互动(答题/弹幕) → 投票 → 分组协作(组内对话/目标进度)
  ├─ 学习：学习文档/课后材料 → 材料渲染 → 学习进度 → 个性化学习路径
  ├─ AI：AI 助手聊天(平台知识/学业上下文) → 职业路线星图 → 心理测试/侧写
  ├─ 成长：修为值/成就/连续打卡 → 积分商城 → 成绩单(report card)
  └─ 个人：资料/签名库(上传/审批/使用记录) → 消息中心 → 博客社区
```

对应路由/服务（文件在 `classroom_app/routers/`、`classroom_app/services/`）：

| 业务 | 路由 | 核心服务 |
|---|---|---|
| 首页聚合 | `ui_parts/dashboard.py` | `dashboard_service.build_dashboard_context`、`dashboard_agenda_events`（全平台统一议程数据源） |
| 作业考试 | `homework.py` + `homework_parts/*`（`/api`） | `assignment_lifecycle_service`、`load_student_task_buckets`（三桶单一真源）、`group_assignment_service`、`late_submission_policy` |
| 课堂互动 | `classroom_interactions.py`（`/api/classroom-interactions`） | `classroom_interaction_service` |
| 签到 | `smart_classroom.py`（`/api/classrooms`） | `smart_attendance_*`、`smart_classroom_checkin_sync_service` |
| 投票 | `polls.py`（`/api/polls`） | `poll_service`（跨班共享票数、三态、黑名单互斥） |
| 分组协作 | `collaboration.py`（`/api/collaboration`) | `collaboration_service`（group_schemes、组内对话、目标/进度） |
| 学习材料 | `learning.py`、`materials.py`、`document_renderer.py` | `materials_service`、`material_render_service`、`learning_progress_service` |
| AI 聊天 | `ai.py`（`/api`）+ WebSocket | `ai_gateway_service`、`student_ai_tutor_context_service`、`chat_handler` |
| 错题本 | `wrong_book.py` | `student_wrong_book_service` |
| 成绩单 | `report_card.py` | `student_report_card_service` |
| 修为/成就/积分 | `achievements.py`、`points_shop.py` | `student_achievement_service`、`student_points_service`、`student_streak_service` |
| 消息中心 | `message_center.py` | `message_center_service` |
| 签名 | `signatures.py`（`/api/signatures`） | `signature_point_service`、`signature_workflow_service` |
| 博客 | `blog.py` | `blog_service`、`blog_community_service` |
| 职业路线 | `career_path.py` | `career_path_service` |

### 1.2 教师端业务链

```
教务同步(课程/班级/名册/排课) → 备课(教案/材料库/课次文档)
  → 上课(课堂互动大屏/签到/分组大屏/投票)
  → 批改(提交进度 → 逐份批阅/AI批改 → 迟交策略 → 缺交记零)
  → 归因(错题归集/知识点掌握度)
  → 收尾(结课一键收口 → 成绩材料链: 平时成绩表→登分表→期末成绩单
         → 过程材料: 考核计划表/评学表/教案导出 → 重修生管理)
旁支：监考/考试邮件提醒、课时统计、公文同步、消息中心
```

关键路由：`manage.py`+`manage_parts/*`（教务/班级/课程）、`homework_parts/grading.py`（批改）、`lesson_plans.py`、`assessment_plans.py`、`teacher_evaluations.py`、`classroom_closeout.py`、`classroom_retake.py`、`smart_classroom.py`（课表/签到）、错题归集 `wrong_question_summary_service`。

### 1.3 管理端

班级/课程/offering 管理、材料中心、监控大屏、系统配置等（`manage_parts/*`、`ui_parts/manage_pages.py`）。**明确不搬小程序**——管理操作是桌面场景。

---

## 2. 小程序现状盘点

### 2.1 已完成闭环（正式版可用）

| 模块 | 页面 | 后端 | 状态 |
|---|---|---|---|
| 微信绑定自动登录 | `pages/welcome` `pages/bind` | `mp/auth.py`（login/bind/me/logout） | ✅ 稳定；logout 同步 revoke binding 已修 |
| 欢迎屏（人生一言复刻） | `pages/welcome` | `mp/life_tips.py` | ✅ |
| 学生首页 | `pages/home`（今天：欢迎语+议程+统计） | `mp/home.py`（复用 build_dashboard_context 投影） | ✅ 与任务列表数据已对齐（load_student_task_buckets） |
| 学生作业考试全流程 | `pages/tasks` `pages/task-detail` | `mp/tasks.py` + 既有 `/api/assignments/*`（bearer 直通） | ✅ 题型渲染/30s 草稿/附件拍照/倒计时/小组互评/结果视图/鉴权附件预览 |
| 教师进度+批阅 | `pages/teacher-task` `pages/teacher-grade` | `mp/teacher.py`（tasks / grading 聚合 / files） | ✅ 顺序批阅页 v0.9.6 待提审 |
| UI 设计语言 | App.vue 磨砂玻璃令牌（.glass-card 等） | — | ✅ 全页面已套用 |
| 发布链路 | miniprogram-ci（`npm run mp:upload`） | 域名 guardianangel.net.cn | ✅ 正式上线 |

**关键架构资产**（后续开发必须遵守）：
- `dependencies.get_active_user_from_request` 已接 mp bearer 回落 → 小程序可直调**全部既有 /api 端点**；`/api/mp` 只放"为小程序聚合/投影"的端点，**严禁复制业务端点**。
- `mp_sessions` 独立会话（30 天滑动、不绑 IP）；`wechat_bindings` openid 一对一。
- 鉴权文件预览统一走 `utils/preview.ts`（downloadFile 带 bearer）。
- 新增 mp 路由后必须重生成 `tests/fixtures/p02_route_snapshot.json`。

### 2.2 现存混乱与欠账（这是"推进缓慢且混乱"的根源）

1. **信息架构偏离设计**：DESIGN.md 规划学生/教师各 4 tab（含"课堂"tab），实际只有 3 个纯文字 tab（首页/作业考试/我的），教师与学生混用同一组 tab，教师功能藏在"作业考试"分支里，角色心智不清。tabBar 无图标。
2. **没有差距清单驱动**：P2/P3 标签用完后，后续（互评、预览、批阅改版）全是散点式推进，无里程碑、无版本-功能对照。
3. **移动端最大价值场景缺失**：通知提醒（订阅消息）完全没做——作业截止/批改完成/催交都收不到，用户不打开就没有触达，留存无从谈起。
4. **课堂现场零覆盖**：签到、课堂互动、投票在小程序上全部没有——而"上课时掏手机"恰是小程序相对 Web 最强的场景。
5. **闭环断点**：学生交完作业能看分数，但错题本、成绩单、修为值在小程序上不可见；"我的"页只有退出登录，几乎是空壳。
6. **体验欠账**：深色模式未做、弱网重试策略未系统化、分享转发未接、绘图题仍提示"去网页端"。
7. **运营待办悬置**：测试账号（微信审核/9999001、测试学生/9999002）未删；备案主体名称是否仍为旧名 SentimentRoom 未核对；v0.9.6 提审未点。

---

## 3. 差距分析与价值排序

不是所有 Web 功能都该搬。按"移动场景适配度 × 用户价值 × 实施成本"排序：

| 优先级 | 功能域 | 移动场景理由 | 成本 |
|---|---|---|---|
| ★★★ | 订阅消息通知（截止/批改完成/催交） | 触达是小程序的独有能力，Web 端做不到；直接决定留存 | 中（需微信模板申请+scheduler 对接） |
| ★★★ | 课堂签到 + 互动答题 + 投票参与 | 上课现场人手一部手机，点击即达，比开网页快一个数量级 | 中 |
| ★★☆ | 成绩/错题/修为（学习闭环只读视图） | 碎片时间查看，纯投影低成本 | 低 |
| ★★☆ | 消息中心聚合 | 配合通知形成"收到推送→进小程序看详情"闭环 | 低 |
| ★★☆ | AI 助手聊天 | 移动端天然对话场景；复用既有 gateway | 中 |
| ★☆☆ | 教师移动工作台（今日课表/监考提醒/一键催交） | 教师在路上/课间的高频快查 | 中 |
| ★☆☆ | 学习材料只读浏览 | 有价值但 HTML 包/复杂文档在小程序渲染受限，只做轻量投影 | 中 |
| ✗ 不做 | 博客、职业星图、签名管理、协作大屏、成绩材料链、教案/过程材料、管理端 | 桌面/大屏场景或低频重操作，留在 Web | — |

---

## 4. 目标信息架构（定稿后不再漂移）

同一小程序按角色渲染 4 tab（`wx.setTabBarItem` 动态换文案图标，或自绘 tabbar）：

```
学生：今天 · 任务 · 课堂 · 我的
  今天   = 欢迎语 + 大议程卡(含通知未读) + 统计
  任务   = 作业考试三桶(现有 tasks 页)
  课堂   = 签到入口 + 进行中的互动/投票 + 我的课程列表
  我的   = 成绩单 · 错题本 · 修为/积分 · 消息中心 · AI 助手入口 · 设置/退出

教师：今天 · 任务 · 课堂 · 工作台
  今天   = 议程(今日课程/监考/待批) + 统计
  任务   = 作业进度卡列表(现有教师分支) → 批阅
  课堂   = 发起签到 · 发起投票 · 互动最小集 · 我的课堂列表
  工作台 = 本周课表 · 消息中心 · 催交中心 · 设置/退出
```

设计原则沿用：磨砂玻璃令牌、每页一个主任务、大卡片不堆九宫格；tabBar 本期补齐图标（4 组 PNG，选中/未选中）。

---

## 5. 分里程碑实施计划

节奏约定：**一个里程碑 = 一个体验版版本 = 一次真机验收 = 一次提审窗口**。每个里程碑内部按「后端 → 前端 → 联调 → 测试 → 部署 → 上传体验版」推进，全部走既定流程：TDD（mp 路由单测 + 路由快照重生成）→ code review → 真 PG 冒烟 → deploy-workflow 部署 → `npm run mp:upload`。

### M0 工程治理与地基（版本 v0.10.0）——先止乱，再加功能

- [x] **信息架构落地**（2026-08-31）：pages.json 4 tab（今天/任务/课堂/我的）；新增 `pages/classroom/index` 结构壳（进行中区+功能预告，角色文案分支）；"我的"页改造为功能列表（学生 5 项/教师 3 项，未上线项"即将上线"徽标）；`utils/tabs.ts` 角色化第 4 tab（学生"我的"/教师"工作台"+公文包图标，教师端导航标题同步）。
- [x] **tabBar 图标**（2026-08-31）：`scripts/make_tab_icons.py` 生成 10 张 81px 线性双态 PNG（today/tasks/classroom/me/work），选中色 #5B6EE0。
- [x] **前端工程规约固化**（2026-08-31）：`miniapp/README.md`（技术栈硬约束/API 红线/页面模板/UI 令牌/发布纪律）。
- [ ] **运营欠账清理**（用户操作项）：体验版提审发布（可直接跳过 v0.9.6 提审 v0.10.0）；核对备案主体名（SentimentRoom→LanShare蓝享）；上线稳定后删两个测试账号。
- [x] 出口标准（部分）：type-check + build 过，**体验版 v0.10.0 已上传**（2026-08-31，commit 51cdb49e）；真机验收通过，用户提审中。

#### M0.1 真机反馈修复批（v0.10.1，2026-08-31）

- [x] **欢迎屏背景图丢失**：根因是 uvicorn 未开 proxy_headers，`request.base_url` 拼出 `http://` 图片地址被小程序拒载；mp/auth.py 新增 `_external_base_url` 尊重 `X-Forwarded-Proto`（需部署后端生效）。
- [x] **首页议程历史折叠**：过期事项（`status=completed`，dashboard_agenda_events 自带）默认隐藏，"历史 N 条"按钮展开，半透明弱化显示。
- [x] **批阅页逐题重构**：新端点 `GET /api/mp/teacher/submission/{id}/review`（复用 `build_deterministic_grading_evidence` 逐题客观判定：满分/0分/未作答/与标准不符/需人工；试卷题干+选项+标准答案；answers_json 内嵌附件清单按题归属，匹配不上归"整卷附件"兜底）。前端：顶部试卷信息+醒目最终分+迟交罚分说明、左侧可收起题目列表（判定着色圆点）、主区单题视图（学生答案/标准答案分块）、右侧本题附件面板、退回待重交禁改分。打分仍走既有 grade 端点（迟交罚分/AI job 冲正/修订台账/小组分联动服务端不动）。单测 `tests/test_wechat_mp_submission_review.py`，路由快照已重生成。
- [x] 部署后端（2026-08-31，含合班课堂 P4.0 同批上线；review 端点生产 401 验证通过）+ **体验版 v0.10.1 已上传**；待真机验收。
- 备注：**v0.10.0 已审核通过并正式上线**（2026-08-31 10:24）。M0 全部出口标准达成。

#### M0.2 真机反馈修复批（v0.10.2，2026-08-31，commit 1638abf8）

- [x] **批阅判定修复**：v0.10.1 线上 bug——多选答案是完整选项文本用 `|||` 连接，确定性判卷分词器把长文本拆碎导致所有多选恒判"与标准不符"；现按 `|||` 拆成列表再喂 evidence（`_normalize_answer_entries`）。
- [x] **六档判定+每题得分**：full 满分(绿)/partial 部分正确·多选漏选(琥珀)/zero 0分·答错或含错选(红)/blank 未作答(紫)/doubt 待评判·填空不匹配(蓝)/manual 人工评判(灰)；抽屉与题卡显示 `5/5`、`—/10` 式得分 chip（客观可确定才给数字，主观绝不猜分）。
- [x] **首页议程点击改造**：取消盲跳任务列表；私人待办点击弹编辑层（标题/备注/日期时间/提醒开关/标记完成），过期待办改时间即重新加入提醒；作业/考试深链保留。新 shim `POST /api/mp/todos/{id}/update`（微信不支持 PATCH，业务全委托 update_manual_todo，归属校验在服务层）。
- [x] 部署生产（todo 端点 401 验证通过）+ **体验版 v0.10.2 已上传**；待真机验收。

#### M0.3 真机反馈修复批（v0.10.3，2026-08-31，commit c3712a24）

- [x] **主观题实际得分透出**：Web 端逐题分来自 feedback_md 里"### 第 N 题 + 本题得分/扣分点/评价"结构（前端 `static/js/grading_feedback.js` 解析），小程序此前只有客观确定性预测。现服务端移植同口径解析（`parse_question_feedback`），**已批改的实际逐题分优先于客观预测**：主观题显示 `14/16` 式 chip，verdict 按实际分重着色（full/partial/zero）；题卡新增"本题批改"块（扣分点红字+评价）。
- [x] 部署生产 + **体验版 v0.10.3 已上传**；待真机验收。

### M1 通知触达（版本 v0.11.x）——移动端价值最大项

前置（用户操作）：mp 后台"订阅消息"申请模板（教育类目），至少三个：①作业/考试截止提醒 ②批改完成/成绩发布 ③教师催交/课堂通知。模板 ID 写入 env。
✅ 已选用（2026-08-31）：班级作业提醒 `8LGiFyiq…`（thing10/thing11/date8/thing3）、作业催交通知 `1HlhCJsP…`（thing1/thing4/time2/thing3）、作业批改完成通知 `Fft4anTP…`（thing6/number7/phrase3/thing4）；ID 内置于 `wechat_mp_subscribe_service.TEMPLATES`，env `WECHAT_MP_TMPL_*` 可覆盖。

- [x] 发送管线（2026-08-31，v0.11.1，commit 39fd9dbf）：`wechat_mp_subscribe_service`——模板注册表（env 可覆盖）、stable_token 进程内缓存、字段清洗（thing≤20/phrase≤5/number/中文日期）、`send_subscribe_message`（尽力而为，43101 拒收清零额度）。
- [x] 额度台账 `mp_subscribe_grants` + 发送去重 `mp_subscribe_sends`（runtime 建表；grant 上报+1、发送-1；dedupe_key 原子占位）。
- [x] scheduler 截止扫描：`mp_deadline_reminder_scan` 30 分钟一轮（24h/2h 两档、合班 membership 口径、作业×学生×档位幂等），app 启动注册；批改完成挂钩 `create_student_grading_notification`（非阻塞）；教师"📣 催交"按钮（teacher-task 页）→ `POST /api/mp/teacher/assignment/{id}/nudge`（每人每天一次）。
- [x] 前端：提交作业的点击手势内拉 `wx.requestSubscribeMessage`（三模板一并请求，accept 上报 `/api/mp/subscribe/report`）；模板配置 home onShow 预取保证手势内零等待。
- [x] 消息中心只读页（2026-08-31，v0.11.0，零 mp 后端——既有 `/api/message-center/{items,read,summary}` bearer 直通）：`pages/messages` 全部/未读分段+单条/全部已读+作业深链；home 顶部铃铛未读角标；"我的"页消息中心入口点亮（双角色）。
- [ ] 出口标准：真机收到三类订阅消息；催交端到端可用；消息中心可读。

### M2 课堂现场（版本 v0.12.x）——第二核心场景

- [x] ~~签到~~ **范围修正（2026-09-02）**：平台考勤来自智慧课堂外部同步（`smart_classroom_checkin_sync_service`），**无原生签到**；小程序不另造签到系统，M2 聚焦投票 + 随堂互动 + 举手/求助。原生签到若需要，另立需求评估。
- [x] **投票参与（学生）**（v0.12.0，commit a3543833）：课堂现场页直调 `/api/polls/classrooms/{oid}/snapshot` + `/{id}/vote`；单选/多选、修改投票、结果柱条按既有 show_results 规则。
- [x] **投票开闸（教师）**：草稿投票"开始"/进行中"结束"（`/{id}/status`），创建编辑留在 Web。
- [x] **课堂互动（学生）**：随堂测选项作答（`/activities/{id}/respond`）、匿名提问（`/activities/{id}/questions`）、举手/求助/跟不上/已完成状态 chips（`/classrooms/{oid}/signals`，再点取消）。
- [x] **课堂互动（教师）**：页内发起随堂测（题目+2~4 选项+标正确答案）/匿名提问主题（`/classrooms/{oid}/activities`）、结束互动、标记问题已解答、举手/求助名单实时展示。
- [x] 课堂 tab 装配：`GET /api/mp/classroom/live` 轻聚合（我的课堂 + 各课堂进行中投票/互动/求助计数 + 我的举手状态，合班 membership 口径），进行中课堂绿色置顶，其余列"我的课堂"，点入 `pages/live/index?oid=`。
- [x] 技术决策：**前台轮询**（课堂 tab 10s、课堂现场页 8s，onHide 停），不上 WebSocket。
- [ ] 出口标准：真机双账号——教师发起随堂测 → 学生作答 → 教师看到计数/正确率；教师开始投票 → 学生投票 → 结果柱条；学生举手 → 教师名单出现。

### M3 学习闭环补全（版本 v0.13.x）——低成本高感知

- [x] **成绩视图**（2026-09-02，v0.13.0，零 mp 后端——直调 `GET /api/report-card`）：`pages/report-card` 学期均分/已评分数/领先次数 + 最强/待加强课程 + 按课程折叠逐次成绩（我的分 vs 班均 + 段位色），点记录深链作答页。
- [x] **错题本**（直调 `GET /api/wrong-book`）：`pages/wrong-book` 错题数/客观题正确率/薄弱知识点 + 知识点掌握度色条 + 课程筛选 chips + 错题卡（题干/选项/我的答案与得分/点开看正确答案/知识点标签）。
- [x] **修为/积分**（直调 `GET /api/points` + `GET /api/achievements`）：`pages/growth` 积分余额/徽章进度 + 徽章墙（未达成半透明+进度提示）/积分明细与赚取规则/商店只展示（兑换留 Web）。修为境界已在首页统计卡展示，不重复做页。
- [x] **"我的"页成型**：学生 5 项入口全部点亮（成绩单/错题本/修为与积分/消息中心/AI 助手待 M4）。
- [ ] 出口标准：学生真机"交作业→看分→看错题→看成绩单"闭环验收。

### M4 AI 助手（版本 v0.14.x）

- [ ] 后端评估：既有 AI 聊天通道（`ai.py` + ai_assistant 网关）对 mp bearer 的可用性；缺口处补 `/api/mp/ai` 聚合（会话列表/发消息/轮询取回——小程序端优先**分段轮询**而非 SSE，uni.request 不支持流式）。
- [ ] 前端 `pages/ai-chat`：对话 UI（markdown 轻渲染，沿用 Web 端聊天归一化约定）、历史会话、学生学业上下文自动注入（复用 `student_ai_tutor_context_service`）。
- [ ] 频控与成本：沿用平台 AI 预算体系（`ai_usage_budget_service`），mp 侧不另开口子。
- [ ] 出口标准：学生真机完成一次带上下文的多轮问答；AI 用量记账正确。

### M5 教师移动工作台（版本 v0.15.x）

- [ ] 今日/本周课表投影（复用 course_schedule 数据，列表形态，不搬 3D）。
- [ ] 监考/考试提醒卡（复用 `dashboard_agenda_events` 教师侧事件）。
- [ ] 催交中心：跨作业的未交汇总 + 批量催交（依赖 M1 订阅消息）。
- [ ] 工作台 tab 装配。
- [ ] 出口标准：教师真机完成"早上看今日安排→课间催交→晚上批两份作业"的移动日常。

### M6 打磨与长尾（版本 v0.16.x，可拆散穿插）

- [ ] 深色模式（跟随系统，glass 令牌出暗色变体）。
- [ ] 弱网体系化：请求重试策略、草稿断网补传验证、骨架屏。
- [ ] 分享转发：作业/投票卡片 `wx.onShareAppMessage`（带 offering 上下文深链）。
- [ ] 学习材料轻浏览（评估后再定范围：优先 markdown/图片类，HTML 包不做）。
- [ ] 绘图题移动端方案评估（canvas 手写板）或维持"去网页端"提示。
- [ ] 性能：分包加载（页面增多后必须）、图片懒加载审计。

---

## 6. 工程推进机制（防再次失序）

1. **单一真源**：本文件是小程序推进唯一路线图。每完成一项勾选并注版本号/commit；新想法先进"候选池"（§9），不插队。
2. **里程碑纪律**：进行中的里程碑未验收，不开下一个的功能开发（打磨类小项可穿插）。每里程碑收尾动作固定：全量测试绿 → 部署生产 → 上传体验版 → 真机验收清单过一遍 → 用户提审 → 在本文档记录。
3. **API 红线**（重申）：小程序直调既有 `/api/*`；`/api/mp/*` 只做聚合/投影/绝对化 URL 之类的小程序适配；发现要"复制业务逻辑"就停下来改成复用。
4. **版本规范**：`v0.<里程碑序号>.<修订>`；上传描述写"M{n} <内容>"。
5. **测试要求**：每个新 mp 端点有纯函数单测（仿 `tests/test_wechat_mp_teacher_grading.py`）；路由快照重生成；涉及提交/批改/签到等写路径必须真 PG 冒烟后才部署。
6. **用户操作项显式交办**：微信后台操作（提审、发布、订阅消息模板申请、类目材料）无法代办（mp.weixin.qq.com 被浏览器安全策略封锁），每次收尾时列成清单明确交办。

## 7. 风险与依赖

| 风险 | 应对 |
|---|---|
| 订阅消息模板审核不过/字段受限（教育类目敏感） | M1 前置先申请，模板措辞避开敏感词；批下来之前 M1 的消息中心部分可先行 |
| 一次性订阅授权率低 → 通知覆盖不足 | 在高意愿节点请求授权 + 授权次数余额管理；文案说明价值 |
| 审核波动（"课堂/教育"字样曾触发资质要求） | 沿用已过审的品牌表述；每版提审留缓冲，不承诺硬时间点 |
| 服务器 2c/4GB 承压（课堂轮询+订阅消息突发） | 轮询限前台+间隔≥5s；订阅消息批量发送走 scheduler 容器异步化；上线 M2 前压测一次 |
| 小程序包体积增长 | M2 起页面转分包；图标/图片压缩入库 |
| pinia/uni-app 版本坑（pinia 必须 v2） | 锁版本，升级另立任务 |

## 8. 验收与度量

- 每里程碑真机验收清单在里程碑小节"出口标准"中。
- 上线后观察指标（可从 nginx/应用日志低成本统计）：mp 会话日活、订阅消息授权率与送达量、签到走小程序的占比、任务提交走小程序的占比。目标：M2 上线一个月后，签到与任务提交的移动占比可观测且趋升。

## 9. 候选池（未排期，新想法先进这里）

- 学生互助/讨论区移动化；博客只读流；小程序码贴纸（教室扫码直达签到）；教师快速发布作业（模板化）；家长/访客角色。

---

*变更记录：2026-08-31 初版（整理平台业务链 + 现状盘点 + M0–M6 路线）。*
