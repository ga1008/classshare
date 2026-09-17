# LanShare 微信小程序端 — 进度核验、Web 端对比与改进计划（2026-09-17）

> 本文是对 [IMPLEMENTATION-PLAN-2026-08.md](IMPLEMENTATION-PLAN-2026-08.md)（推进真源）§10–§12 的一次**代码级复核 + 接下来的执行计划**。复核基线：分支 `dev`，commit `3b343049`，小程序最后一次功能提交 `11b8ee8d`（2026-09-10 阶段 1 归并）。
> 复核方式：逐文件通读 `miniapp/src`（14 页 + utils + store + 6 个 vitest）、`classroom_app/routers/mp/*`、`wechat_mp_service.py`、`wechat_mp_subscribe_service.py`、8 个 `tests/test_wechat_mp_*`/`test_mp_grade_safety.py`，以及 Web 端 900 条路由的鉴权依赖与 2026-09-02 以来的 96 个提交。**未登录微信后台，线上正式版/体验版号未核对**；本文所有"已修/未修"均指当前 `dev` 代码。
> 本文只做规划与结论登记，未改任何业务代码。执行时按 §5 的批次逐项勾选，并在真源文档同步。

---

## 0. 一页结论

1. **开发进度 = M3 完成 + 阶段 1（核心正确性）已归并；验收进度 = M0 正式上线，M1–M3 真机出口均未勾选。** 代码可构建（type-check/build 通过），vitest 44/44、后端 mp 专项全绿，但**路由快照测试当前失败**（多出 5 条 `/api/feedback/*` 路由，非 mp，需审查后更新基线）。
2. **阶段 1 的 7 项 P0/P1（C01–C06、C11）在代码层面全部确认修复**，有单测覆盖；仍需 iOS/Android 真机清单 S1-01～S1-08。
3. **C07/C08/C09/C10/C12 全部仍开放，且通知管线新发现 3 处结构性缺陷**（去重键被无授权/无绑定/失败结果永久占用；批改完成去重键含时间戳等于不去重；额度在取 token 前先扣）。这些让"订阅消息"这个小程序最大价值项目前**不可信赖**，必须先于任何新功能修。
4. **前端新发现 8 处逻辑问题**（消息已读失败伪装成功、首次提交订阅授权静默跳过、随堂测可把空选项标为正确答案、绘图题静默退化为文本+拍照、议程上课/监考项点击无响应、"可撤销"无入口等），都在 §2.3 列出。
5. **Web 端对比**：所有 `/api/**` JSON 路由都走 `get_active_user_from_request`，mp bearer 直通，且全站无 CSRF 中间件；唯二不通的是课堂讨论 WebSocket（只读 cookie）和 Agent 任务中心写操作（mp 用户载荷缺 `session_id`）。因此**大多数缺口是纯前端投影工作**，后端只需少量薄壳。
6. **两块"零后端"的高价值缺口**已具备现成 API：教师工作台可直接用 `GET /api/work-inbox`（2026-09-13 新增，聚合审批/待批/签名/日历/开课缺口）；AI 助手可先走**私信 AI 联系人 + 任务轮询**（`POST /api/message-center/private/messages` → `GET …/private/ai-jobs/{id}`），绕开流式传输验证。
7. **工程健康**：无组件目录、两页超 800 行（task-detail 1535 / teacher-grade 1376）、`teacher.py` 856 行、11 页复制同一段 401 处理、两页各自手写轮询、无分包、无 Markdown 渲染、无深色/分享。这些是后续每加一页都在放大的欠账，本计划把"组件化 + 共享工具"作为独立批次而不是穿插。

---

## 1. 进度核验（代码 vs 文档 vs 发布）

### 1.1 页面与端点清单（当前真实状态）

| 页面（`miniapp/src/pages/`） | 行数 | 数据来源 | 状态 |
|---|---|---|---|
| welcome / bind | 455 / 228 | `/api/mp/auth/*`、`/api/mp/life-tips/feedback` | ✅ 阶段 1 已加票据一次消费、限速入库、退出只解绑当前微信 |
| home（今天） | 654 | `/api/mp/home`（全量 `build_dashboard_context` + 三桶计数）、`/api/learning/personal-greeting`、`/api/message-center/summary`、`/api/mp/todos/{id}/update` | ✅ 但议程中 `class`/`invigilation` 项点击为空操作 |
| tasks（任务） | 348 | `/api/mp/tasks`（LIMIT 200 后分桶）/ `/api/mp/teacher/tasks`（LIMIT 100） | ✅ 无分页、无"提醒我"入口 |
| task-detail（作答/结果/互评） | 1535 | `/api/mp/tasks/assignment/{id}` + 既有 draft/submit/peer-eval | ✅ 阶段 1 已加版本号乐观写、串行写队列、退回重交、草稿账号隔离 |
| classroom（课堂） + live（现场） | 285 / 837 | `/api/mp/classroom/live` 10s 轮询；polls + classroom-interactions 8s 双请求轮询 | ✅ 功能齐；C10/C12 未修 |
| report-card / wrong-book / growth | 406 / 383 / 433 | 直调 `/api/report-card`、`/api/wrong-book`、`/api/points`、`/api/achievements` | ✅ 只读；无分页 |
| messages | 285 | `/api/message-center/items?limit=150`、`/read` | ✅ 无正文展开、无分页、已读失败伪成功 |
| me（我的/工作台） | 328 | 无请求 | 学生 4 实 1 占位（AI 助手）；教师 1 实 2 占位（本周课表、催交中心） |
| teacher-task / teacher-grade | 577 / 1376 | `/api/mp/teacher/assignment/{id}/grading|nudge`、`/review`、`/files` + 既有 grade/batch-grade/zero-unsubmitted | ✅ 阶段 1 已加 `expected_review_revision` 409、原始分/最终分分离 |

`/api/mp` 共 18 条路由（auth 5、classroom 1、home 1、life-tips 1、subscribe 2、tasks 2、teacher 5、todos 1），与 `tests/fixtures/p02_route_snapshot.json` 一致。

### 1.2 里程碑对照

| 里程碑 | 代码 | 真机出口 | 备注 |
|---|---|---|---|
| M0 地基 | ✅ | ✅ v0.10.0 正式上线（08-31） | M0.1–M0.3 修复批已传体验版 |
| 阶段 1 核心正确性 | ✅ 09-08 实现、09-10 归并补齐草稿版本契约 | ❌ S1-01～S1-08 未执行 | 未部署后端、未上传小程序 |
| M1 通知 | 管线已写 | ❌ | C07/C08 + 3 处新发现，见 §2.2 |
| M2 课堂现场 | ✅ | ❌ | C10、C12 未修 |
| M3 学习闭环 | ✅ | ❌ | 与 Web 成绩口径对账未做 |
| M4 AI / M5 工作台 / M6 打磨 | 占位 | — | 本文 §5 重排 |

### 1.3 本次执行的验证

| 验证 | 结果 |
|---|---|
| `miniapp` `npm test` | 44/44 通过（6 文件） |
| 后端 `tests.test_wechat_mp_*` + `test_mp_grade_safety`（SQLite 层） | 通过；PG 层用例在无 `LANSHARE_MP_AUTH_TEST_DATABASE_URL` / `MP_PHASE1_*_DSN` 时跳过 |
| `tests.test_architecture_route_snapshot` | **失败**：基线 942 vs 当前 953，多出 `/api/feedback/admin` GET、`/api/feedback/{id}/messages` GET+POST、`/read` POST、`/status` POST（commit `8a2a01a9`），mp 路由全部匹配 |
| 生产/微信后台 | 未核对 |

---

## 2. 逻辑检查结论

### 2.1 已确认修复（阶段 1）

| 编号 | 证据 |
|---|---|
| C01 批阅串卷 | `teacher-grade/index.vue:190-222,259-266,339-380` 请求序号 + 提交 ID 丢弃过期响应；`submission_grade_guard_service.py:57-64` 锁内校验修订号 409；`tests/teacher-grade-page.test.ts` |
| C02 迟交重复扣分 | 前端回填原始分并携带 `expected_review_revision`（`:160-175,359`）；后端 `submission_grading_service.py:95-148` 持久化 `score_before_late_penalty`；`test_mp_grade_safety` "80 扣 10 得 70 稳定" |
| C03 草稿账号隔离 | `utils/task-submission.ts:2-7` 键含环境/角色/用户/任务/轮次；`tests/task-submission.test.ts` |
| C04 停用身份 | `routers/mp/deps.py:68` + `dependencies.py:714` 同走 `validate_authenticated_user_identity`；`test_wechat_mp_auth.py:290-314` |
| C05 退回重交 | 后端 `tasks.py:109-120,198-219` 投影 `can_submit/resubmission_state`；前端 `utils/assessment.ts:46-50` 消费 |
| C06 合班口径 | `/teacher/tasks`、`/grading` 用 `offering_student_where`；**催交端点 `teacher.py:680-700` 用等价内联谓词**，未共用 helper（漂移风险，归入 A7） |
| C11 会话底座 | 12 个受保护页均先 `await ensurePageSession`；`App.vue:19-21` 记录冷启动目标；`session-target.ts` 白名单；并发 401 单次跳转（`tests/session-flow.test.ts`） |

### 2.2 仍开放（原编号）+ 通知管线新发现

| 编号 | 现状（file:line） | 影响 |
|---|---|---|
| **C07** 通知管线顺序/状态 | `wechat_mp_subscribe_service.py:279-350`：`_claim_dedupe`(296) → `_find_openid`(304) → `_consume_grant`(307) → token(309) → HTTP(320)。`mp_subscribe_sends` 只有 `(template_key,user_role,user_pk,dedupe_key,sent_at)`，**无状态列，无待发任务表** | 截止提醒 `deadline:{aid}:{sid}:{stage}`、催交 `nudge:{aid}:{sid}:{day}` 一旦遇到 no_binding/no_grant/no_token/failed，去重键永久占用，**该事件不再可能送达** |
| **C07-新1** 额度先扣后取 token | `:307` 先 `remaining-1`，`:309` 才取 token | 凭证缺失或超时会白白消耗用户一次性授权 |
| **C07-新2** 批改完成不去重 | `message_center_service.py:4176` 的 dedupe key 含时间戳 | 重批/AI 回调可重复推送 |
| **C07-新3** 日志可能带 access_token | `:329` `print` 异常对象，URL 含 `?access_token=`（`:323`） | 低，但要改 |
| **C08** 请求内同步调微信 | 催交 `teacher.py:663-725` 在同一连接内逐人 `send_subscribe_message`（每人最多 2×8s），`commit` 在 724；批改通知 `message_center_service.py:4165-4177` 在手动批改事务内（`submission_grading_service.py:166-173`）与 AI 批改（`ai.py:573`）同步发送 | 微信慢响应拖长批改事务、持有 `mp_subscribe_*` 行锁；催交接口可能数十秒才返回 |
| **C09** LIMIT 后分桶 / 无分页 / 教师原始列 | `tasks.py:37,71-74` LIMIT 200 再分桶；`home.py` 复用同函数计数；`teacher.py:31` LIMIT 100；`teacher.py:483-489,565-579,621,634-637` 直接读 `sub.score/status`，未 import `score_projection_service`（学生侧 `tasks.py:78-79` 已用 `load_submission_score_facts`） | 早创建仍进行中的任务会消失；首页计数与列表可能不一致；教师统计与学生有效成绩口径可能不同（重批/退回/缺交零分） |
| **C10** 随堂测正确答案错位 | `live/index.vue:250-257` 先 `filter(Boolean)` 再用原数组下标 `composer.correct` 比对；且不校验被选为"正确"的槽位是否为空 | 中间留空选项 → 正确答案指向错的选项；空槽为正确 → 无正确答案仍可发出 |
| **C12** 轮询无互斥/退避/缓存 | `live/index.vue:103,141-148,300-303`（8s，双请求 `Promise.all`，无 in-flight 守卫，无序号，`act()` 内再触发 `loadAll` 可重叠）；`classroom/index.vue:31,78`（10s，同样无守卫）；后端 `/api/mp/classroom/live` 每次 4 条业务查询 + 约 3 条鉴权查询、2 个连接、无缓存 | 200 人现场页 ≈ 50 req/s；慢响应可覆盖新状态 |

### 2.3 前端新发现

| # | 问题 | 位置 | 建议批次 |
|---|---|---|---|
| F1 | 消息标记已读：先本地翻转再 POST，失败静默 → 伪装已保存 | `messages/index.vue:65-74` | A |
| F2 | 首次提交时若订阅模板未预取（冷启动未经过首页），`requestSubscribe` 静默返回不弹授权 | `utils/subscribe.ts:31-35` | A |
| F3 | 授权上报无幂等标识；`reject/ban/filter` 结果不处理 | `subscribe.ts:40-50` | B |
| F4 | `requestSubscribeMessage` 在 `showModal` 确认回调的异步续体里调用，是否被微信认作用户手势未验证 | `task-detail/index.vue:762-767` | A（真机验证） |
| F5 | 议程 `class`/`invigilation` 行点击无任何反馈 | `home/index.vue:133-138` | D |
| F6 | 绘图题/未知题型静默退化为 textarea + 拍照上传，无"本端不支持"说明 | `task-detail/index.vue:257-259,989-1040` | A |
| F7 | 缺交记零文案承诺"可撤销"，App 内无入口；后端也**无撤销端点** | `teacher-task/index.vue:173` | A（改文案）/ D（做入口） |
| F8 | `welcome` 局部 `.glass-card` 与全局同名规则叠加 | `welcome/index.vue:323` vs `App.vue:36` | G |
| F9 | live 深链恢复时丢 `title` 参数 | `session-target.ts:28` | G |
| F10 | 任务/题干/批语/要求全部 `<text>` 纯文本，无 Markdown/图片渲染；批阅编辑器占位写"支持 Markdown"但无解析 | `task-detail:881-906,962`；`teacher-grade:596,602-614` | E2 |
| F11 | 消息中心仅 `body_preview` 两行截断，无全文；`limit=150` 硬编码无翻页 | `messages/index.vue:49,150,257-265` | B |
| F12 | 催交结果只显示 pushed/total，无"无授权/失败/处理中"分类 | `teacher-task/index.vue:149-153` | B |
| F13 | `/teacher/submission/{id}/files` 无提交级归属预检，靠逐文件检查（异课教师得到空数组而非 403） | `teacher.py:828` | A |
| F14 | `mp_subscribe_*` DDL 在业务事务内首次使用时懒执行 | `wechat_mp_subscribe_service.py:64-100,292` | B |

### 2.4 工程健康

- **组件化**：无 `src/components/`；14 页全部单文件内联模板+样式。
- **重复代码**：401→`redirectToLogin()` 12 处；`error instanceof Error ? … : …` 13 处；`showModal→Promise<boolean>` 6 处；`.empty`/`.card__title`/segment/hero 样式 8–9 页复制；色板常量（BAND_COLORS / masteryColor / VERDICT_COLORS）各页重声明。
- **超长文件**：task-detail 1535、teacher-grade 1376（前端 800 行红线）；`routers/mp/teacher.py` 856（后端红线），其中 `_judge_question`/`build_submission_review`（197-434）是业务相邻逻辑放在路由文件。
- **分包**：无 `subPackages`，13 页全在主包（当前 292 KB 无压力，但 §5 新增 6–8 页后需要）。
- **依赖**：`vue-i18n` 未使用。
- **测试空白**：home / tasks / classroom / live / messages / me / welcome / report-card / wrong-book / growth / teacher-task 零测试；`subscribe.ts`、`format.ts` 零测试；后端无 nudge、`/api/mp/home`、`/tasks` 分桶、`run_deadline_reminder_scan`、发送管线顺序的测试。
- **错误处理**：`api.ts` 无重试；超时与离线不可区分（均 statusCode 0）；页面加载失败一律"加载失败，点击重试"丢弃 `error.message`。

---

## 3. Web 端对比：差距清单

### 3.1 鉴权前提（决定"能不能直接搬"）

- `get_current_user` / `get_current_teacher` / `get_current_student` / `require_teacher_domain` 全部经 `get_active_user_from_request`（`dependencies.py:570`）→ `_get_mp_bearer_user`（`:541`），**mp bearer 直通**。全站**无 CSRF 中间件**。
- **不通**：① 课堂讨论 WebSocket `/ws/{oid}`（`routers/files.py:1604` 只读 cookie）；② `/api/agent-tasks/*` 写操作（`_source_session_id` 需要 `user["session_id"]`，`load_mp_user` 不返回）。
- **无 JSON 路由、只有服务端渲染**：教学阶段条 `teaching_stage_service`、课堂运营总台 `build_offering_hub_context`（需要薄壳）。

### 3.2 学生端差距

| 功能 | 现成 API（均接受 mp bearer） | 移动价值 | 结论 |
|---|---|---|---|
| **AI 助手** | 方案①私信 AI 联系人：`POST /api/message-center/private/messages`（`contact_identity=assistant:{oid}`）→ `GET …/private/ai-jobs/{job_id}` 轮询 → `GET …/private/conversation`；方案②课堂会话流式：`GET /api/ai/chat/sessions/{oid}`、`POST …/session/new/{oid}`、`GET …/history/{uuid}`、`POST /api/ai/chat`（multipart，返回 **NDJSON** 行事件，非 SSE） | ★★★ | 先①（零传输风险），后②（需 `enableChunked` 真机验证） |
| **学习材料轻浏览** | `GET /api/classrooms/{id}/learning-materials?session_id=`（返回 `render_kind/is_renderable/render_url/viewer_url/open_url/ai_blurb`）、`GET /api/classrooms/{id}/materials?parent_id=`、`/materials/raw|download/{id}`（bearer downloadFile）、`/api/document-renderer/jobs/{key}/pages/{n}`（Office 转页图）、`POST …/learning/material-progress`、mastery-check | ★★☆ | Markdown/图片/PDF/Office 直接做；HTML 包与 LessonDoc 只能 `web-view`（域名已备案，可评估） |
| **私信（学生↔任课教师）** | `GET /api/message-center/private/contacts`、`GET /api/classrooms/{id}/private/contacts`、`GET …/private/conversation?contact=&scope=&limit=`、`POST …/private/messages`（含附件）、blocks | ★★☆ | 与 AI 助手同一套 UI，一并做 |
| **审批流（作业撤回重做申请）** | `GET /api/approvals/types`、`GET /api/approvals?scope=&status=`、`POST /api/approvals`（type `submission_withdraw`）、`POST /{id}/cancel`；未批改可直接 `DELETE /api/assignments/{id}/withdraw` | ★★☆ | 小面积，补到 task-detail 结果视图 |
| **课表列表** | `GET /api/dashboard/course-schedule/overview?year=&term=`（学生 403 以外角色） | ★★☆ | 列表/周视图，不搬 3D |
| **待办 CRUD + 工作区筛选** | `POST /api/todos`、`PATCH|DELETE /api/todos/{id}`、`/api/classrooms/{id}/todos`、`GET /api/dashboard/workspace?date_scope=&cursor=&limit=` | ★★☆ | 首页议程目前只能改不能建/删 |
| **协作分组** | `/api/collaboration/classrooms/{id}/snapshot`、`/schemes/{id}/random-join`、`/groups/{id}/chat`（HTTP 轮询）、`/goal`、`/nominate-leader` | ★★☆ | 组内对话是 HTTP，可做 |
| **我的出勤** | `GET /api/classrooms/{id}/smart-attendance/analytics`（学生只看自己） | ★☆☆ | 平台**仍无原生签到**（09-13 新增的是教师侧考勤归档 `/api/attendance-reports/*`，桌面场景），M2 决策不变 |
| **反馈对话** | `POST /api/feedback`、`/{id}/upload|messages|read`、`GET /api/feedback/my`、`DELETE /{id}` | ★☆☆ | 与消息中心 `app_feedback` 类别联动 |
| **个人资料** | `GET /api/profile/bootstrap`、`PUT /api/profile/basic|mood|password`、`POST /api/profile/avatar` | ★☆☆ | "我的"页当前无任何编辑 |
| 修为分课堂快照 / 积分兑换 | `/api/classrooms/{id}/learning/snapshot|progress|alerts`、`POST /api/points/redeem` | ★☆☆ | growth 页目前全局只读 |
| 全局搜索 / ICS 日历 / 群二维码 | `GET /api/global-search`、`/api/calendar-feed`、`/api/classrooms/{id}/group-qr` | ☆ | 候选池 |
| 课堂讨论区 | WS cookie-only | — | 需先改 `files.py:1604` 接受 bearer/query token，候选池 |
| 签名库 / 职业星图 / 博客 / 心理测试 / 学习路径页 | — | ✘ | 维持桌面 |

### 3.3 教师端差距

| 功能 | 现成 API | 结论 |
|---|---|---|
| **工作台收件箱** | `GET /api/work-inbox?source=&limit=`（`work_inbox_service.py:30-38` 来源：approval / signature_request / password_reset / grading / offering_gap / teacher_calendar / manual / feedback） | ★★★ 直接作为"工作台"tab 主体，零后端 |
| 今日/本周课表 + 监考 | `GET /api/dashboard/workspace`（`source_type` 含 `teacher_calendar`/`academic_exam`/`grading`）、`GET /api/dashboard/calendar`、`GET /api/manage/academic/course-schedule/overview?year=&term=` | ★★☆ 列表形态 |
| 考试邮件提醒 | `GET|POST|DELETE /api/manage/system/exam-reminders/email` | ★☆☆ |
| 跨作业催交中心 | 无现成聚合；`/api/mp/teacher/tasks` 已有各任务未交数 | 需薄壳聚合 + 依赖 C07/C08 修复 |
| 退回重交 / 教师撤回 / 关闭作业 | `DELETE /api/submissions/{id}`、`POST /api/assignments/{id}/submissions/withdraw`、`POST …/close` | ★★☆ 补到 teacher-task/teacher-grade |
| 批量 AI 批改状态 | `POST …/batch-grade` 返回 queued/skipped；**无 job 状态端点**，只能轮询 `GET /api/assignments/{id}/submissions` 的 `status`；单份 `regrade|force-regrade|stop-grading` | ★★☆ 阶段 4 要求"排队/进行中/失败可查" → 需薄壳投影 |
| 缺交记零撤销 | **无端点** | 先改文案；若要做需新增共享业务端点（非 mp 专属） |
| 审批（教师侧） | `GET /api/approvals?scope=incoming`、`POST /{id}/approve|reject` | ★★☆ |
| 私信学生 | `GET /api/classrooms/{id}/private/contacts` + 同学生端 | ★★☆ |
| 教学阶段条 / 课堂总台 | 仅服务端渲染 | 需 `/api/mp/teacher/classroom/{oid}/stage` 薄壳，候选 |
| 考勤归档 / 材料与教案编辑 / 结课 / 公文 | — | ✘ 桌面 |

### 3.4 2026-09-02 以来影响契约的平台变更（需回归）

| 提交 | 对小程序的影响 | 处理 |
|---|---|---|
| `ac2c77d7` 考核分类 | 任务/成绩/消息标题携带 `assessment_kind*`、`score_visible`、`grade_display_state`；mp 已同步 | 回归即可 |
| `11b8ee8d` 阶段 1 归并 | 详情返回 `submission_version/draft_revision/server_now_ms` 等；批阅返回 `review_revision` | 已消费 |
| `04f0e0ab` 审批流 | 已批改作业撤回改走 `POST /api/approvals`；消息中心新增 `approval_workflow` 类别 | messages 页深链映射需加类别（当前落到 `null` 不跳转） |
| `8a2a01a9` 反馈对话 | 消息中心新增 `app_feedback` 类别（`ref_type=app_feedback_message`） | 同上 |
| `cfb1f2dd` 管理中心 P0/P1 | 教师 `build_dashboard_context` 变大 → `/api/mp/home` 教师侧更重 | 归入 C 批次 home 瘦身 |
| `d3518a44` 考勤归档 | `GET /api/classrooms/{id}/members` 有改动；无原生签到 | 无影响 |
| `4958a4a5`/`58dbe20a` Agent DSH | `get_current_user_optional` 先查 agent 上下文再查 cookie/bearer | 无行为变化 |

---

## 4. 改进原则（本轮新增或重申）

1. **先可信，再扩面**：通知管线、课堂轮询、分页这三项没收口之前，不开新页面；否则每个新页面都在放大同一类问题。
2. **零后端优先**：能用现成 `/api/*` 投影的功能先做（work-inbox、私信 AI、审批、课表、材料列表），把 `/api/mp` 薄壳留给真正需要聚合的地方。
3. **一个共享层**：本轮建立 `src/components/`、`src/composables/`（轮询、确认框、错误映射、分页加载）、`src/styles/tokens.scss`（色板与 glass 令牌），后续页面只能复用不能复制。
4. **写操作四件套**：幂等键、状态可查、失败可解释、409 保留输入——已在提交/批改落地，通知与催交按同样标准。
5. **每批一个体验版 + 一份证据**：沿用真源 §12.1（用例 ID、版本、设备、样本、截图、对账）。

---

## 5. 分批执行计划

版本号延续 `v0.<批次>.<修订>`，从 v0.14.0 起（v0.13.x 归 M3）。每批 = 后端 → 前端 → 单测 → 真 PG 冒烟 → 部署 → 上传体验版 → 真机清单 → 记录。**批次 A 与 B 之间不插任何新功能。**

### 批次 A：止血与阶段 1 收口（v0.14.0）

**范围**（全部是缺陷修复，不加页面）：

| 项 | 改动 | 文件 |
|---|---|---|
| A1 C10 | 随堂测选项改为 `{id,label}` 稳定标识，`correct` 存 id；过滤空选项后按 id 标 `is_correct`；校验"正确答案槽非空、≥2 有效选项" | `pages/live/index.vue:121-122,250-257,366` |
| A2 F1 | 已读改为"POST 成功后再翻转"；失败恢复未读并 toast | `pages/messages/index.vue:60-74` |
| A3 F2/F4 | `requestSubscribe` 无配置时 `await` 预取再弹；在 `submit()` 的**同步点击处理器**内先发起 `requestSubscribeMessage`，再进入 `showModal`（真机验证手势有效性并记录） | `utils/subscribe.ts:31-35`、`task-detail/index.vue:762-767` |
| A4 F6 | 题型白名单：`drawing`/未知类型渲染"本端暂不支持，请到网页端"卡片，且在**进入任务时**顶部汇总提示（阶段 6 要求前移） | `task-detail/index.vue:257-259,989-1040` |
| A5 F7 | 缺交记零确认文案去掉"可撤销"；说明"如需更正请到网页端逐份改分" | `teacher-task/index.vue:173` |
| A6 F13 | `/teacher/submission/{id}/files` 先做提交级归属预检，异课 403 | `routers/mp/teacher.py:828` |
| A7 C06 尾巴 | 催交名单改用 `offering_student_where` | `routers/mp/teacher.py:680-700` |
| A8 深链类别 | `utils/assessment.ts:60-68` 增加 `approval_workflow`/`app_feedback` 映射（暂落消息全文视图，B 批次做） | `utils/assessment.ts` |
| A9 快照基线 | 审查 5 条 `/api/feedback/*` 新路由后更新 `p02_route_snapshot.json` | `tests/fixtures/` |
| A10 真机清单 | 执行阶段 1 的 S1-01～S1-08（iOS + Android 各一台），登记证据 | `PHASE1-IMPLEMENTATION-2026-09-08.md` §4 |

**测试**：`live` 页新增 vitest（选项映射、空槽校验）；`messages` 新增 vitest（已读失败回滚）；后端 `test_wechat_mp_teacher_grading` 加 files 越权 403。
**出口**：C10 关闭、阶段 1 出口勾选、部署 + 体验版 v0.14.0、路由快照绿。

**执行记录（2026-09-17，本地实现完成，未部署/未上传）**

- [x] A1 `utils/live-composer.ts` `buildQuizOptions`（槽位 id 映射 + 空槽/少于 2 项拒绝）；`pages/live` 改用；`tests/live-composer.test.ts` 4 例。
- [x] A2 `pages/messages` 已读改为服务端确认后翻转，401 回登录，其余 toast；`tests/messages-page.test.ts` 3 例（真实页面脚本）。
- [x] A3 `utils/subscribe.ts` `requestSubscribe` 返回 `Promise<SubscribeOutcome>`，无配置时先拉配置再弹（不再静默跳过）；`task-detail` 在 `submit()` 任何 await 之前、以 `hasVisibleContent()` 为门槛同步发起授权（原来在 showModal 回调后）；`tests/subscribe.test.ts` 3 例 + task-submission 新增"授权先于确认框、无内容不问"1 例。**F4 手势有效性仍需真机确认。**
- [x] A4 `SUPPORTED_QUESTION_TYPES = {radio, checkbox, text, textarea, attachment}`；不支持题型渲染提示卡 + 头部汇总警告；`isAttachmentQuestion` 收窄为 `attachment`。
- [x] A5 缺交记零文案改为"记零为占位记录；如需更正，请到网页端逐份改分"。
- [x] A6 `_ensure_teacher_owns_submission`：files 端点先做提交级归属预检（404 不存在 / 403 非本教师课堂）；`test_mp_grade_safety` 新增 1 例。
- [x] A7 催交名单改用 `offering_student_where()`；`test_mp_grade_safety` 新增"催交对象与名单同口径"1 例（缺交零分占位视为未交，停用/他班不催）。
- [x] A8 `assessmentNotificationTarget` 对 `/dashboard`、`/manage/system/feedback` 显式返回 null；审批流链接 `/assignment/{id}?approval_request=` 已由原正则覆盖，补 4 条断言。
- [x] A9 审查 5 条 `/api/feedback/*` 路由（均 `get_current_user`，属已发布的反馈对话功能）后重生成 `p02_route_snapshot.json`。
- [ ] A10 真机清单 S1-01～S1-08 + F4 手势验证（需部署后端与上传体验版后执行）。
- 代码审查（code-reviewer）：APPROVE，0 CRITICAL/HIGH。1 条 MEDIUM 为既有情况：后端 `exam_json_service.VALID_QUESTION_TYPES` 只有 radio/checkbox/text/textarea，试卷题从不带 `attachment` 类型，`task-detail` 逐题上传区（`isAttachmentQuestion`）在本批前后都不可达；普通作业的整卷上传（`PLAIN_FILE_QID`）不受影响。→ 纳入批次 F 候选：为 textarea 题开放逐题拍照上传（对应 Web 端画板附件），或删除死代码。

验证：`miniapp` `npm run type-check` 通过、`npm test` 9 文件 55/55、`npm run build:mp-weixin` 通过；后端 `test_architecture_route_snapshot + test_wechat_mp_* + test_mp_grade_safety + test_architecture_import_compatibility` 119 通过（29 个为无 DSN 跳过的 PG 层用例）。

### 批次 B：通知可信（v0.15.x，对应真源阶段 2）

**后端**：

| 项 | 改动 |
|---|---|
| B1 持久化发送任务 | 新表 `mp_subscribe_tasks`（engine-aware runtime schema，仿 polls）：`id, event_key(UNIQUE), template_key, user_role, user_pk, payload_json, state(pending/processing/succeeded/no_binding/no_grant/rejected/temp_failed/perm_failed/unknown), attempts, next_attempt_at, lease_owner, lease_until, last_error_class, created_at, updated_at`。`mp_subscribe_sends` 退役为历史表（迁移只加不删） |
| B2 业务只入队 | 截止扫描、`create_student_grading_notification`、催交端点全部改为**短事务写 task 行**，不再在请求内调微信；催交立即返回 `{accepted, task_ids}` |
| B3 发送 worker | 注册 scheduler handler `mp_subscribe_dispatch`（30s 一轮，原子领取 `FOR UPDATE SKIP LOCKED`，租约 2 min），顺序：**读绑定 → 读额度（不扣）→ 取 token → 发送 → 成功才扣额度并置 succeeded**；43101 → rejected + 清额度；40001/42001 → 刷新 token 重试一次；超时 → unknown，按 `attempts` 指数退避最多 3 次后 perm_failed；no_binding/no_grant → 终态但**不占事件键的重试权**（用户后续授权上报时，对未过期的截止提醒可补发） |
| B4 去重键修正 | `graded` 去重键改为 `graded:{submission_id}:{review_revision}`；截止 `deadline:{aid}:{sid}:{stage}` 不变但 no_grant 不占用 |
| B5 授权上报幂等 | `POST /api/mp/subscribe/report` 增加 `report_id`（客户端 uuid），`mp_subscribe_grants` 记录最近 report_id 去重；返回当前剩余额度 |
| B6 状态查询 | `GET /api/mp/teacher/assignment/{id}/nudge-status` 返回按 state 计数与最近一次时间；`GET /api/mp/subscribe/status`（学生侧：各模板剩余额度、最近送达） |
| B7 日志与 DDL | 发送异常只记 errcode/errmsg，屏蔽 URL；`ensure_mp_subscribe_schema` 移到 app 启动 |
| B8 消息中心分页 | 前端改用既有 `limit`+`offset`（若 `message_center.py` 无 offset 则加 `before_id` 游标，属共享端点小改） |

**前端**：

| 项 | 改动 |
|---|---|
| B9 tasks 页"提醒我" | 每张进行中任务卡增加铃铛：点击手势内 `requestSubscribeMessage(deadline)` → 上报 → chip 显示"已订阅/剩余 N 次" |
| B10 催交结果 | 显示受理数 + 轮询 nudge-status 展示 成功/无授权/失败/处理中 |
| B11 消息全文 | 点击展开正文（`body` 字段）、长列表滚动到底加载更多、未读跨端一致 |
| B12 通知落点 | `approval_workflow` → task-detail 结果视图；`app_feedback` → 消息全文（反馈页留 F 批次） |

**测试**：发送 worker 纯函数状态机单测（每种 errcode/超时/无绑定/无额度）；PG 多 worker 并发领取（`SKIP LOCKED`）无重复成功、额度不为负；scheduler 注册测试；前端 subscribe.ts 单测（预取等待、accept 上报幂等、reject 分支）。
**真机**：截止 24h/2h、催交、批改完成三类各收到一次；冷启动点击通知到达正确任务；模拟微信慢 8s 时批改接口 p95 不受影响、催交 p95≤1s 受理。
**出口**：C07/C08 及三处新发现关闭；M1 出口勾选；v0.15.x 体验版。

### 批次 C：分页、容量与成绩口径（v0.16.x，对应真源阶段 3）

| 项 | 改动 |
|---|---|
| C1 C09 任务分页 | `/api/mp/tasks` 改为 `bucket=pending|completed|expired&cursor=&limit=30`；`pending` 全量（按截止升序，不设 LIMIT，进行中任务本身有限）；`completed/expired` 游标分页；总计数独立 SQL 不受分页影响；`/api/mp/home` 计数改用计数查询而非全量装载 |
| C2 教师口径 | `/teacher/tasks`、`/grading` 统计改读 `score_projection_service.load_submission_score_facts`（有效成绩/发布态/缺交零分/退回），与学生侧同源；`teacher/tasks` 也分页 |
| C3 home 瘦身 | `/api/mp/home` 不再调用全量 `build_dashboard_context`，改为只取 `dashboard_agenda_events` + 统计（新增 `dashboard_service.build_mobile_home_context`，共享服务层而非 mp 私有逻辑） |
| C4 C12 轮询 | 新增 `composables/usePolling.ts`：单飞（in-flight 互斥）、响应序号丢弃旧结果、失败指数退避（8s→16s→32s 上限 60s）+ ±20% 抖动、`onHide` 停 / `onShow` 先刷新再启；live/classroom 两页迁移，live 两个快照合并为 `GET /api/mp/classroom/{oid}/live-snapshot` 一次返回（聚合而非复制逻辑） |
| C5 后端缓存 | `/api/mp/classroom/live` 与 live-snapshot 加 5s 进程内 TTL（键 = offering + 角色），mp 鉴权的两次连接合并为一次 |
| C6 长列表 | report-card 按课程折叠已存在，改为课程级懒加载；wrong-book 用 `/api/wrong-book?cursor=`（共享端点加游标，`MAX_WRONG_ITEMS` 保留为默认页） |
| C7 容量基线 | 用 `tools/` 下压测脚本对 live-snapshot 做 50→100→200 并发（8s 周期），记录 p95、5xx、PG 连接数（配合 `db_pool` 健康检查）；目标：读 p95≤1s、5xx<0.5% |
| C8 M2/M3 真机 | 双账号实课流程 + "交作业→看分→错题→成绩单"与 Web 逐条对账（重批保留旧成绩、迟交、小组揭晓、缺交零分、退回） |

**出口**：C09/C12 关闭；M2、M3 出口勾选；容量报告给出经验证的并发上限；v0.16.x。

### 批次 D：教师移动工作台（v0.17.x，对应真源阶段 4 / M5）

| 项 | 改动 |
|---|---|
| D1 工作台 tab | `pages/me`（教师态）改为三段：**今天安排**（`/api/dashboard/workspace?date_scope=today` 的 teacher_calendar/academic_exam/grading 项）、**收件箱**（`GET /api/work-inbox` 按 source 分组，approval 可直接 approve/reject）、**设置/退出** |
| D2 本周课表 | 新页 `pages/schedule/index`：`GET /api/manage/academic/course-schedule/overview?year=&term=` 列表化（周内按日分组，节次/教室/班级/调停课态）；学生态同页用 `/api/dashboard/course-schedule/overview` |
| D3 议程点击 | F5：`class` → schedule 页定位当日；`invigilation` → 监考详情弹层（复用 workspace item 字段）+ "设置邮件提醒"（`/api/manage/system/exam-reminders/email`） |
| D4 催交中心 | 新页 `pages/nudge-center/index`：`GET /api/mp/teacher/nudge-overview`（薄壳：跨作业未交 = 复用 `/teacher/tasks` 数据 + 每任务未交名单，合班口径），按课堂/任务/截止筛选，勾选范围后批量入队（走 B2），结果按 B10 展示 |
| D5 批阅后续动作 | teacher-task 增加：退回重交（`DELETE /api/submissions/{id}`）、教师撤回（`POST …/submissions/withdraw`）、关闭作业（`POST …/close`）；批量 AI 批改后轮询 `GET /api/assignments/{id}/submissions` 投影 排队/进行中/失败/完成计数（薄壳 `GET /api/mp/teacher/assignment/{id}/grading-progress`），失败项可 `regrade` |
| D6 待批返回位置 | teacher-grade 返回 teacher-task 时保留滚动位置与筛选（`onShow` 局部刷新而非整页重载） |

**出口**：真源阶段 4 验收（早看安排→课间催交→晚批两份→回工作台核对待办减少）；v0.17.x。

### 批次 E：AI 助手（v0.18.x，对应真源阶段 5 / M4）

| 项 | 改动 |
|---|---|
| E1 首版（轮询） | 新页 `pages/ai-chat/index`：课程选择（`/api/classrooms/mine`）→ 私信 AI 联系人 `assistant:{oid}`：`GET …/private/conversation` 恢复历史、`POST …/private/messages` 发送（带客户端 `message_id`）、`GET …/private/ai-jobs/{job_id}` 轮询（2s→5s 退避，最长 180s）、生成中/失败/停止态；用量与频控沿用平台 `ai_usage_budget_service`（不在 mp 另开口子） |
| E2 Markdown 轻渲染 | 引入 `mp-html`（或自研受控解析：标题/列表/粗体/代码块/图片/链接），**同时**用于 F10 的题干、要求、批语、错题；图片经 bearer downloadFile 临时路径 |
| E3 流式二版（可选） | 真机验证 `uni.request({enableChunked:true})` + `onChunkReceived` 对 `POST /api/ai/chat` NDJSON 的 UTF-8 跨块、nginx 缓冲、切后台中断；通过则切到课堂会话模式（sessions/history/new）；不通过维持 E1 |
| E4 私信教师 | 同一聊天 UI 接 `/api/classrooms/{id}/private/contacts` 学生↔任课教师私信（教师态同样可用） |

**出口**：真源阶段 5 验收（三轮带上下文问答、恢复、权限、用量对账、iOS/Android 传输验证）；v0.18.x。

### 批次 F：学习闭环扩面（v0.19.x）

| 项 | 改动 |
|---|---|
| F1 学习材料 | 新页 `pages/materials/index?oid=`：`GET /api/classrooms/{id}/learning-materials` 按课次分组；`render_kind` 为 markdown/image → 页内渲染（E2 组件）；pdf/office → bearer downloadFile + `openDocument`；html 包/lessondoc → `web-view` 打开 `render_url`（需在后台加业务域名；bearer 通过一次性 ticket 换 cookie 的薄壳 `POST /api/mp/materials/web-view-ticket`）；进度回写复用 `material-progress`，达标条件与 Web 相同 |
| F2 审批 | task-detail 结果视图加"申请撤回重做"（`POST /api/approvals`）与状态；未批改直接 `DELETE …/withdraw` |
| F3 待办 CRUD | home 议程"+"新建、滑动删除（`/api/todos`） |
| F4 协作分组 | 课堂现场页加"我的小组"抽屉：`/api/collaboration/classrooms/{id}/snapshot`、随机入组、组内对话（HTTP 轮询走 C4 的 usePolling）、目标 |
| F5 我的出勤 | growth 或课堂页加 `smart-attendance/analytics` 只读卡 |
| F6 反馈 | "我的"页"意见反馈"：`POST /api/feedback` + 对话线程 |
| F7 个人资料 | "我的"页头像/心情/密码修改（`/api/profile/*`） |
| F8 分享 | task/poll 页 `onShareAppMessage`（带 oid/id 深链，经 `session-target` 白名单；不带 token/答案/成绩） |

### 批次 G：工程收敛（穿插在 A–C 之间执行，不单独发版）

| 项 | 改动 |
|---|---|
| G1 组件 | `src/components/`：`GlassCard`、`SegmentControl`、`EmptyState`、`ErrorRetry`、`ScoreChip`、`HeroStats`、`QuestionRenderer`（从 task-detail 抽出）、`FeedbackBlocks`（从 teacher-grade 抽出） |
| G2 composables | `usePolling`（C4）、`useConfirm`（替换 6 处 showModal 样板）、`useLoader`（统一 loading/failed/error.message + 401 处理，替换 12 处）、`usePagedList`（游标加载更多） |
| G3 令牌 | `src/styles/tokens.scss` 集中色板、间距、圆角；`welcome` 去掉局部 `.glass-card`（F8） |
| G4 拆分 | task-detail → 答题/结果/互评三子组件，目标 <600 行；teacher-grade → 题目面板/附件面板/评语编辑，<600 行；`routers/mp/teacher.py` 把 `build_submission_review`/`_judge_question` 迁到 `services/wechat_mp_review_service.py` |
| G5 分包 | `subPackages`: `pkg-learn`（report-card/wrong-book/growth/materials）、`pkg-teacher`（teacher-task/teacher-grade/nudge-center/schedule）、`pkg-ai`（ai-chat）；主包留 tab 页 + task-detail + live；`preloadRule` 按角色预载；深链路径保持不变 |
| G6 依赖 | 移除未用 `vue-i18n`；不升级 uni-app/pinia |
| G7 错误语义 | `api.ts` 区分 timeout/offline（`errMsg` 含 `timeout`）；页面显示 `error.message`；GET 可配置一次重试 |
| G8 测试 | 每个新组件/composable 一份 vitest；零测试页面至少补加载/失败/401 三态 |

### 批次 H：体验与发布闭环（v0.20.x，对应真源阶段 6–7）

- 深色模式（manifest `darkmode:true` + `theme.json` + 令牌暗色变体）；键盘遮挡与大字模式检查。
- 冷启动/首屏/包体基线记录（分包前后对比）。
- 发布纪律：每候选版记录 commit/后端版本/体验版号/审核状态/真机清单；预发布回滚演练；一个真实课堂试点 7 天观察；观察指标：订阅授权率与送达率（按 B1 状态表统计，区分受理/微信成功/点击）、任务提交移动占比、课堂互动参与率、JS 异常与关键接口耗时。

---

## 6. 明确不做（维持真源 §3 结论）

博客、职业星图、心理测试、签名管理、成绩材料链、教案/过程材料、考勤归档复核、材料与 LessonDoc 编辑、结课、公文、管理端、课堂讨论 WebSocket（除非先改 `files.py:1604` 支持 bearer）、Agent 任务中心写操作（除非 `load_mp_user` 补 `session_id` 语义）、原生签到（平台无此真源）。

## 7. 用户操作项（Claude 无法代办）

1. 微信后台核对当前正式版/体验版号与代码对应关系（阶段 0 遗留）。
2. 批次 B 前确认三个订阅模板仍有效；如需"课堂通知"类模板另申请。
3. 批次 F1 前在后台"业务域名"添加 `guardianangel.net.cn`（web-view 需要）。
4. 删除测试账号（微信审核/9999001、测试学生/9999002）改在批次 H 试点后执行；提审保留审核账号。
5. 每批体验版的提审/发布。

## 8. 风险

| 风险 | 应对 |
|---|---|
| 批次 B 改共享服务（批改通知、message_center）影响 Web | 只改"发送方式"不改"何时发"；Web 端消息中心行为不变；回归 `test_message_center*` + `test_ai_grading_service` |
| 订阅授权率低 | B9 在任务卡提供主动入口 + 提交时请求；文案说明价值；状态表可观测 |
| 2c/4GB 承压 | C4/C5 先落地再扩 live 使用；C7 压测给出上限后才在真实课堂扩大 |
| `enableChunked` 兼容 | E1 先轮询，E3 只有真机通过才切 |
| 分包改路径破坏历史通知深链 | G5 保持原路径，仅 tab 页外的页面进分包，`session-target` 白名单同步 |
| 审核波动 | 沿用已过审品牌表述；每批留缓冲 |

---

*变更记录：2026-09-17 初版——三路代码审计（前端/后端/Web 差距）结论 + 批次 A–H 计划。执行时在 [IMPLEMENTATION-PLAN-2026-08.md](IMPLEMENTATION-PLAN-2026-08.md) 勾选并回填版本号。*
