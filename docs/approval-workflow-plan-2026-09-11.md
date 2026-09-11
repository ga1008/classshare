# 通用审批流模板 + 作业撤回重做申请（2026-09-11）

## 0. 目标与决策

1. **仲裁只留给期中/期末**：`_review_grading_result_if_needed` 只对 `assessment_kind ∈ {midterm, final}`（`vision_assessment_high` / `text_assessment_high`）做豆包 pro 二次评分；平时作业（flash 档）有风险信号时只标记 `needs_review` 给教师，不再花仲裁额度。学生对平时作业分数不满意 → 走本次新增的"撤回重做申请"。
2. **通用审批流模板**（新模块，签名申请流保持不动，作为设计参照）：一张 `approval_requests` 主表 + 审批人表 + 事件表，服务层用**请求类型注册表**挂业务钩子（校验/审批人/通过/拒绝/取消/详情），通知统一走消息中心（新分类 `approval_workflow`，重要级 → 自动进邮件 outbox），前端一个可复用组件（发起浮窗 + 办理侧栏 + 大尺寸详情弹窗）。后续新流程只需注册一个类型 + 提供详情渲染器。
3. **首个类型 `submission_withdraw`**：学生在作业详情页分数下方"?"→ 悬停 1 s 浮窗"分数有异议？"→ 填理由发起；教师收到消息+邮件，可从消息深链或作业统计页右侧"申请办理"侧栏处理；通过时可单独设重交截止，未设则按规则自动算；拒绝须填原因。

## 1. 数据模型（`classroom_app/db/schema_approval_workflow.py`，运行时 engine-aware，仿 polls）

- `approval_requests`：id、request_type、status(pending/approved/rejected/cancelled/expired)、applicant_role/applicant_user_pk/applicant_name、subject_type/subject_id、assignment_id、class_offering_id、title、reason、payload_json、decision_note、decision_payload_json、decided_by_role/decided_by_user_pk/decided_by_name、decided_at、expires_at、dedupe_key、created_at/updated_at。partial unique `(dedupe_key) WHERE status='pending'`。
- `approval_request_reviewers`：request_id、reviewer_role、reviewer_user_pk、reviewer_name、notified_at、reminded_at。任一审批人决定即终态（与签名流一致）。
- `approval_request_events`：request_id、event_type(created/approved/rejected/cancelled/expired/reminded/auto_cancelled)、actor_role/actor_user_pk/actor_name、note、payload_json、created_at。

## 2. 服务层

- `approval_workflow_service.py`：`register_request_type(ApprovalRequestType)`；`create_request(conn, applicant, request_type, subject_id, reason, payload)`（类型钩子 `prepare` 返回 subject/reviewers/title/dedupe_key/assignment_id/class_offering_id/expires_at；pending 去重 409）；`list_requests(conn, user, scope=incoming|mine, status, assignment_id, limit)`；`get_request_detail(conn, user, id)`（含类型钩子 `detail`）；`decide_request(conn, reviewer, id, decision, note, decision_payload)`（守卫式 `UPDATE … WHERE status='pending'` 防并发；钩子 `on_approve/on_reject` 在同一事务）；`cancel_request(conn, applicant, id)`；`auto_cancel_requests(conn, request_type, subject_ids, note)`（业务侧动作使申请失效时调用）；`remind_stale_requests(conn)`（>48h 未处理提醒审批人一次，>7 天标 expired 并通知申请人）+ `ensure_approval_reminder_task`；通知 `_notify(...)` 走 `message_center_service._build_notification_payload` + `_insert_notification_if_allowed`，跳过操作者本人。
- 请求类型 `approval_request_types/submission_withdraw.py`：
  - `prepare`：申请人必须是该提交的学生；提交状态 graded（分数已出）且非 `is_absence_score`；作业 `assessment_kind ∉ {midterm, final}`（重要考核不允许）；作业未被教师撤回中；审批人 = 课堂教师 ∪ 课程创建教师（`_load_submission_notification_context` 的 offering_teacher_id / created_by_teacher_id）；dedupe_key `submission_withdraw:{submission_id}`。
  - `on_approve`：`submission_return_service.return_submissions_for_resubmission(...)`（从教师撤回路由抽出的核心，同一事务），重交截止 = `resolve_resubmission_due_at(assignment, explicit, now)`；通知学生（含截止时间）。
  - `on_reject`：通知学生（含教师意见）。
  - `detail`：学生/作业/提交摘要（分数、提交时间、批改摘要）、`review_url=/submission/{id}`（教师页大弹窗内嵌 iframe 展示答题与批改详情）、当前作业截止信息、推荐重交截止（按规则预填）。
- `submission_return_service.py`：`resolve_resubmission_due_at(assignment, explicit_due_at, now)` —— 基准 = 作业有效截止（启用补交则 `late_submission_until`，否则 `due_at`）；基准为空或已过 → `now+24h`；有显式时限则取 `max(显式, 基准)`。`return_submissions_for_resubmission(conn, assignment, targets, teacher_id, due_at, reason)` = 原教师撤回逻辑（锁小组、退休成绩版本、UPDATE），并 `auto_cancel_requests('submission_withdraw', ...)` 使这些提交上的 pending 申请变 cancelled（note=教师已手动撤回）。教师撤回路由改为调用它；**默认截止**从"now+120 分钟"改为同一规则（用户填了才覆盖）。

## 3. 截止时间逻辑排查结论

- 学生重交窗口只看 `resubmission_due_at`（`submission_resubmission_accepts`），与作业 `due_at`/自动关闭解耦：作业关闭后仍可在窗口内重交 —— 符合需求。
- 教师撤回默认 2 小时与"作业截止哪个长按哪个"不一致 → 统一到 `resolve_resubmission_due_at`。
- `build_resubmission_due_at` 把 payload 的 `due_at` 键也当显式时限，教师页只传 `resubmission_due_at`，保留兼容。
- 小程序端读 `resubmission_due_at` 展示，无需改。
- 草稿轮次 `draft_matches_submission_round` 以 `returned_at` 为界，通过申请后 `returned_at` 更新会自动开启新一轮草稿 —— 正确。

## 4. 路由（`classroom_app/routers/approval_workflow.py`，前缀 `/api/approvals`）

`POST /` 创建；`GET /?scope=incoming|mine&status=&assignment_id=&request_type=` 列表；`GET /{id}` 详情；`POST /{id}/approve` `{note, decision:{resubmission_due_at|extension_minutes}}`；`POST /{id}/reject` `{note}`（必填）；`POST /{id}/cancel`（申请人）；`GET /types` 注册表（前端文案）。教师/学生共用 `get_current_user`，权限在服务层。

## 5. 前端

- `static/js/approval_workflow.js`（plain JS，无构建依赖）：`ApprovalWorkflow.mountLauncher(el, {requestType, subjectId, currentRequest})`（"?"触发器：悬停 1 s / 触屏点按弹出小浮窗，显示当前申请状态或"申请撤回重做"按钮 → 理由输入 → 提交）；`ApprovalWorkflow.mountPanel(el, {scope:'incoming', assignmentId, autoOpenId})`（右侧抽屉：待办/已办列表，点击打开大弹窗：申请信息 + 详情 iframe + 通过（截止 datetime-local + 延长分钟，预填推荐值）/拒绝（原因）表单）。类型详情渲染通过 `detailRenderers[request_type]` 挂钩，默认渲染键值摘要。
- 样式 `ui-system.src.css` 追加 `.apr-*`（Docker 构建时 `npm run build`，本地验证需 `npm run build:css`）。
- 学生页 `assignment_detail_student.html`：分数区加 `?` 触发器，服务端注入 `withdraw_request`（当前申请状态）+ `can_request_withdraw`。
- 教师页 `assignment_detail_teacher.html`：右侧固定抽屉（带待办数徽标），`?approval_request=<id>` 深链自动打开；消息中心通知 link 指向该深链。
- 消息中心：新分类 `approval_workflow`（师生均可见，标签"审批流程"），重要级 → 邮件。

## 6. 测试

`tests/test_approval_workflow.py`（sqlite 内存库 + 运行时建表）：创建/去重/权限/通过（重交截止规则三种情况、提交状态被重置、pending 去重解除）/拒绝/取消/教师手动撤回自动取消/提醒过期；`resolve_resubmission_due_at` 表驱动；期中期末拒绝申请。仲裁门控改动补 `test_ai_execution_profiles`。

## 7. 部署

同 2026-09-11 上午流程：冻结 LF 工作树 + `-QuiesceForMigration -MigrationReport … -MigrationBackup …`。

## 8. 实施记录（2026-09-11 下午）

- **仲裁门控**：`ai_assistant.AI_GRADING_ADJUDICATION_KINDS`（env，默认 `midterm,final`）+ `_adjudication_allowed_for_context`，替代按档位判断；作业有风险信号只标 `needs_review`（`review_deferred=manual_review_required`）。测试 `test_default_adjudication_kinds_skip_homework`，review 机制测试用 homework fixture 时把 kinds patch 成 `{homework}`。
- **通用审批流**：`classroom_app/services/approval_workflow_schema.py`（三张表，运行时 engine-aware ensure）、`approval_workflow_service.py`（注册表 `ApprovalRequestType`、create/list/get/decide/cancel/auto_cancel/remind/ensure_reminder_task、消息中心分类 `approval_workflow`（重要级→邮件））、`approval_request_types/submission_withdraw.py`、路由 `routers/approval_workflow.py`（`/api/approvals`）、调度 handler `approval_request_reminder`（6h 一轮：48h 提醒一次、7 天过期）、`app.py` 启动注册。
- **撤回核心抽取**：`services/submission_return_service.py`（`resolve_resubmission_due_at` 规则 + `return_submissions_for_resubmission`），教师撤回路由改为复用；教师撤回弹窗预填改为"作业截止 / 24h"规则，不再默认 2 小时。
- **前端**：`static/js/approval_workflow.js`（`mountLauncher` 悬停 1 s 浮窗、body 级 fixed 定位；`mountPanel` 右侧抽屉 + 宽弹窗，弹窗内嵌 `/submission/{id}` iframe 展示答题与批改详情，通过表单预填推荐截止）；`ui-system.src.css` `.apr-*`；学生页 `assignment_detail_student.html` 与试卷型作业的 `exam_take.html` 都挂了 `?`；教师页 `assignment_detail_teacher.html` 挂抽屉，`?approval_request=<id>` 深链自动打开；消息中心教师通知 link 指向该深链。
- **DDL 模块放在 services/ 而非 db/ 的原因**：原生 PG 部署门禁把 `classroom_app/db/*.py` 全部按哈希绑定到演练报告，新增/修改任一文件都要重做整套原生演练；本功能的表是运行时 `CREATE TABLE IF NOT EXISTS`（与 polls 同模式），由服务在每次操作前 ensure，且对"进程标记已就绪但连接的库没有表"的情况有二次 ensure 兜底，因此不触碰 `schema.py`。
- **验证**：`tests/test_approval_workflow.py` 12 例 + 相关套件 106 例通过；全量 3191 例中剩余 77 错/3 败经 clean HEAD 工作树比对确认为既有问题（lessondoc/agent/route-snapshot 等）。浏览器端到端（P03 sqlite 运行时 + Playwright 临时 spec，已删除）：学生悬停出浮窗→提交申请→教师抽屉徽标 1→弹窗内嵌答题详情→通过（预填 = 作业截止 9/13 15:27）→学生页出现"待重交，请在 2026-09-13 15:27 前重新提交"→深链自动打开弹窗，截图留在 `.codex-temp/ui-audit/apr-*.png`。
