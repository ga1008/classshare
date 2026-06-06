# T05 - 事务、队列与并发

## 目标

保证从 SQLite 迁移到 PostgreSQL 后，课堂并发、作业提交、草稿保存、行为事件、邮件、AI 和 Agent 队列不会出现重复领取、长事务、锁等待失控或数据分叉。

## 重点场景

1. 学生同时进入课堂和讨论区。
2. 多名学生同时保存草稿和提交作业。
3. 行为事件高频写入。
4. 邮件 outbox worker 批量领取。
5. AI grading、wrong summary、private message AI job、material AI import、session material generation 领取。
6. Agent task worker 轮询和状态更新。

## PostgreSQL 目标语义

1. 队列领取使用短事务。
2. 多 worker 领取同一队列时使用 `FOR UPDATE SKIP LOCKED`。
3. 状态机更新必须带当前状态条件，避免 stale callback 覆盖新状态。
4. 批量行为事件必须合批写入，避免每条事件一个事务。
5. 连接池大小必须和 worker 数、主应用并发匹配。

## 验收条件

- [ ] 队列领取无重复任务。
- [ ] 并发写入错误率小于 1%。
- [ ] 没有长时间 idle in transaction。
- [ ] 行为事件队列不会长期满载。
- [ ] SQLite 模式下现有并发优化不退化。

## 当前执行记录

已完成：

- 新增 `tools/db_concurrency_plan.py` 和相关测试。
- 已记录 SQLite 并发配置：busy timeout、WAL、behavior queue size、batch size、flush interval。
- SQL helper 已提供 PostgreSQL queue claim 生成能力。
- `classroom_app/services/email_notification_service.py` 的邮件 outbox 入队和 `_claim_due_jobs()` 已改为引擎感知：
  - `_insert_email_outbox_job_if_absent()` 在 SQLite 下保留 `INSERT OR IGNORE` 幂等语义，在 PostgreSQL 下使用 `ON CONFLICT (dedupe_key) DO NOTHING RETURNING id`，冲突时按 `dedupe_key` 找回已有 job。
  - SQLite：保留原有 `SELECT` + 条件 `UPDATE` + `rowcount` 领取语义，防止重复处理。
  - PostgreSQL：使用单条 `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED LIMIT ?) RETURNING *`，在短事务内完成领取并返回任务。
- `tests/test_email_notification_queue_claim.py` 已覆盖 SQLite 入队幂等、PostgreSQL `ON CONFLICT/RETURNING` 入队、SQLite 领取行为、PostgreSQL `SKIP LOCKED/RETURNING` 领取和未知 engine fail-fast。
- `classroom_app/services/agent_task_service.py` 的 `claim_next_agent_task()` 已改为引擎感知：
  - SQLite：通过 `begin_immediate_transaction()` 保留 `BEGIN IMMEDIATE`、先检查 running、再领取最高优先级 queued 任务的单例语义；PostgreSQL 下该 helper 不发出 SQLite 专属 SQL。
  - PostgreSQL：使用事务级 `pg_try_advisory_xact_lock` 保护“同一时间只允许一个 Agent running”的业务约束，再用 `FOR UPDATE SKIP LOCKED` 和 `RETURNING agent_tasks.*` 领取候选任务。
- `tests/test_agent_task_service.py` 已补充 SQLite 单例领取、PostgreSQL advisory lock/SKIP LOCKED/RETURNING SQL 形态、锁忙返回空和未知 engine fail-fast。
- `classroom_app/services/blog_news_crawler_service.py` 已完成 due-run 领取和前置配置写入的 PostgreSQL 适配：
  - `load_blog_news_crawler_config()` 在 PostgreSQL 下使用 `ON CONFLICT (id) DO NOTHING`，不再依赖 SQLite `INSERT OR IGNORE`。
  - `enqueue_blog_news_crawler_run()` 在 PostgreSQL 下使用 `RETURNING *`，不再依赖 `lastrowid`。
  - `process_due_blog_news_crawler_runs_once()` 在完成心跳/调度前置写入并提交后，用短事务领取 due pending run。
  - PostgreSQL due-run 领取使用 `FOR UPDATE SKIP LOCKED` 和 `RETURNING *`，避免多 worker 双跑同一爬取任务。
- 新增 `tests/test_blog_news_crawler_queue_claim.py`，覆盖 SQLite due-run 领取、PostgreSQL `SKIP LOCKED/RETURNING` SQL 形态、配置初始化/入队方言和未知 engine fail-fast。
- `classroom_app/services/wrong_question_summary_service.py` 已完成 wrong summary job 的 PostgreSQL 领取和运行时 schema 保护：
  - `ensure_wrong_summary_cache_tables()` 在 PostgreSQL 下只查询 `information_schema.columns` 校验已迁移表，不运行 SQLite DDL 或 `PRAGMA`。
  - `_mark_wrong_summary_job_running()` 只允许 `queued -> running`，避免 completed/failed 或 stale job 被重新置为 running。
  - PostgreSQL claim 使用 `WITH candidate AS (... FOR UPDATE SKIP LOCKED) UPDATE ... RETURNING assignment_wrong_summary_jobs.id`，并继续要求 `run_token` 匹配，保留 stale job 防护。
- `tests/test_wrong_question_summary_service.py` 已补充 PostgreSQL schema 只读校验、SQLite queued-only claim、PostgreSQL `SKIP LOCKED/RETURNING` SQL 形态。
- `classroom_app/services/ai_grading_service.py` 和 `classroom_app/routers/ai.py` 已完成 AI grading 状态机的 PostgreSQL 并发保护增量：
  - `submit_submission_for_ai_grading()` 最终置为 `grading` 时必须满足 `status != 'grading'`、未撤回，且 `allow_graded=False` 时不会抢占已批改状态；PostgreSQL 分支使用 `RETURNING id` 判定原子更新成功。
  - AI 服务提交失败后的 `_reset_submission_after_queue_failure()` 只会在 `grading_attempt_fingerprint` 匹配当前尝试时回滚，避免旧请求失败清空新批改 token。
  - `/api/internal/grading-complete` 回调最终落库时要求 `status='grading'`、未撤回，并在有回调 token 时追加 `grading_attempt_fingerprint` guard；若并发窗口内 token 或状态变化，则返回 `ignored_stale_grading_result`。
- `tests/test_ai_grading_service.py` 已补充 SQLite 状态条件、PostgreSQL `RETURNING` SQL、token 匹配回滚和回调最终更新 guard 测试。
- `classroom_app/services/message_center_service.py` 已完成 private message AI job 状态机的 PostgreSQL 增量：
  - `message_center_notifications`、`private_messages` 和 `private_message_attachments` 写入路径已使用 PostgreSQL `RETURNING id` 获取主键；SQLite 继续使用 `lastrowid`。
  - `ensure_private_message_attachment_schema()` 在 PostgreSQL 下只读校验已迁移的 `private_message_attachments` 列，不再执行 SQLite `CREATE TABLE`、`ALTER TABLE` 或 `PRAGMA`。
  - `create_private_ai_reply_job()` 在 PostgreSQL 下使用 `INSERT ... RETURNING *`，不依赖 SQLite `lastrowid`。
  - `_claim_private_ai_reply_job()` 使用 `FOR UPDATE SKIP LOCKED` + `RETURNING *` 完成 `pending -> running` 单 job claim，保留 SQLite 条件更新回归。
  - `schedule_pending_private_ai_reply_jobs()` 在 PostgreSQL 下批量 `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED LIMIT ?) RETURNING *` 后直接调度已 claim 的任务，降低多实例恢复时重复调度同一 pending job 的风险。
  - `_finish_private_ai_reply_job()` 只允许 `running -> completed/failed`，避免未 claim 或已变化的 job 被旧处理流程覆盖。
- 新增 `tests/test_message_center_private_ai_jobs.py`，覆盖 SQLite create/claim/finish 原行为、PostgreSQL create `RETURNING`、单 job claim `SKIP LOCKED/RETURNING`、批量 schedule claim `SKIP LOCKED/RETURNING` 和未知 engine fail-fast。
- `classroom_app/routers/materials_parts/ai_import_helpers.py` 已完成 material AI import claim 增量：
  - `classroom_app/routers/materials_parts/ai_import.py` 的创建入口已使用 PostgreSQL `INSERT ... RETURNING *` 获取 `material_ai_import_records.id`，SQLite 继续使用 `lastrowid` 后查询。
  - `_claim_material_ai_import_record()` 只允许 `queued -> running`，SQLite 使用条件 `UPDATE` + `rowcount`，PostgreSQL 使用 `FOR UPDATE SKIP LOCKED` + `RETURNING *`。
  - `_run_material_ai_import_record()` 改为先 claim 再解析，避免读后无条件置 running。
  - `_mark_material_ai_import_failed()` 只更新 `queued/running` 活跃任务，避免旧失败覆盖 completed 等终态。
- 新增 `tests/test_material_ai_import_queue_claim.py`，覆盖 SQLite queued-only claim、PostgreSQL `SKIP LOCKED/RETURNING` 和未知 engine fail-fast。
- `classroom_app/services/session_material_generation_service.py` 已完成 session material generation task 状态机的 PostgreSQL 增量：
  - `create_generation_task()` 在 PostgreSQL 下使用 `INSERT ... RETURNING *`，不依赖 SQLite `lastrowid`。
  - `_claim_generation_task_for_run()` 只允许 `queued -> running`，SQLite 使用条件 `UPDATE` + `rowcount`，PostgreSQL 使用 `FOR UPDATE SKIP LOCKED` + `RETURNING id`。
  - `run_generation_task()` 改为先 claim 再生成，并且 completed/failed 写回只允许更新仍处于 `running` 的任务，避免重复调度或旧执行流覆盖终态。
- 新增 `tests/test_session_material_generation_queue_claim.py`，覆盖 SQLite queued-only claim、PostgreSQL `SKIP LOCKED/RETURNING`、PostgreSQL create `RETURNING` 和未知 engine fail-fast。
- `classroom_app/routers/materials_parts/generation_helpers.py`、`library.py`、`final_material_helpers.py` 和 `classroom_app/services/materials_service.py` 已完成课程资料关键写入增量：
  - `course_materials` folder/file 创建在 PostgreSQL 下使用 `RETURNING id`，并保留顶层 root_id 回填语义。
  - `course_material_assignments` 幂等写入在 PostgreSQL 下使用 `ON CONFLICT (material_id, class_offering_id) DO NOTHING`。
  - final material completed import record 在 PostgreSQL 下使用 `RETURNING id`。
- 新增 `tests/test_materials_postgres_writes.py`，覆盖课程资料 folder/file 和 final material completed record 的 PostgreSQL 主键返回路径。
- `classroom_app/db/connection.py` 已新增 `execute_insert_returning_id()`，统一 SQLite `lastrowid` 与 PostgreSQL `RETURNING id` 语义；`homework_parts` 普通作业创建、试卷发布作业、草稿创建、学生提交记录创建、`learning_progress_service` 阶段试炼尝试/作业/证书写入、`routers/ai.py` AI 聊天 session 创建已接入该 helper。

当前状态：并发目标和辅助 SQL 已建立，邮件 outbox、Agent task、blog crawler、wrong summary、AI grading、作业/试卷/阶段试炼关键写入、课程资料关键写入、消息中心通知/私信写入、private message AI job、material AI import 和 session material generation task 均已完成关键领取/写入/状态机增量；散落的 `BEGIN IMMEDIATE` 已收口到 `begin_immediate_transaction()`，避免 PostgreSQL 执行 SQLite 专属事务语句；但历史业务 SQL、其他后台任务和 PostgreSQL 全栈运行仍需继续验证，不得用数据层装载演练替代应用并发验收。
## 2026-06-06 增量执行记录

本轮新增完成管理端教学基础数据写入路径的 PostgreSQL 主键返回适配：

1. `classes_courses_classes.py`：
   - 班级导入创建 `classes` 使用 `execute_insert_returning_id()` 获取 `class_id`。
   - 学生批量导入改用 connection facade 的 `executemany()`，避免 PostgreSQL 下裸 cursor 绕过 qmark 转换。
   - 单个学生新增使用 `execute_insert_returning_id()` 获取 `student_id`。
2. `classes_courses_courses.py`：
   - 课程保存创建路径使用 `execute_insert_returning_id()` 获取 `course_id`。
   - 旧 `/courses/create` 接口也返回 `course_id`，便于切换后脚本直接用新 ID 做接口验证。
3. `classes_courses_offerings.py`：
   - 课堂保存创建路径使用 `execute_insert_returning_id()` 获取 `offering_id`。
   - 旧 `/class_offerings/create` 接口返回 `class_offering_id`，便于切换后验证课堂绑定和后续页面。
4. `classes_courses_onboarding.py`：
   - 教师 onboarding 创建班级、课程、课堂均改为统一主键返回 helper，避免 PostgreSQL 下依赖 `lastrowid`。
5. `semesters_textbooks.py`：
   - 学期创建和教材创建使用统一主键返回 helper。

新增测试 `tests/test_manage_postgres_writes.py` 覆盖上述代表性路径，fake connection 的 `cursor()` 会直接失败，防止以后回退到 SQLite 专属写法。

本轮验证结果：

1. `rg -n "lastrowid|conn\\.cursor\\(" classroom_app\\routers\\manage_parts -g "*.py"` 无匹配。
2. `python -m unittest tests.test_manage_postgres_writes` 通过。
3. `python -m py_compile` 针对本轮管理端改动文件通过。

注意：这只代表管理端教学基础数据写入路径已适配 PostgreSQL 主键返回，不代表全部业务 SQL 已完成迁移；生产 cutover gate 仍然必须保持阻断。

## 2026-06-06 教务同步增量执行记录

本轮继续完成教务同步服务的 PostgreSQL 写入和幂等语义适配：

1. `academic_calendar_sync_service.py`：教务识别当前学期并创建 `academic_semesters` 时使用 `execute_insert_returning_id()`。
2. `academic_course_sync_service.py`：
   - 新建 `courses` 使用 `execute_insert_returning_id()`。
   - `teacher_academic_course_session_occurrences` 在 SQLite 下保留 `INSERT OR IGNORE`，在 PostgreSQL 下使用 `ON CONFLICT (...) DO NOTHING`。
   - `teacher_academic_course_sync_items` 在 PostgreSQL 下使用 `ON CONFLICT (...) DO NOTHING RETURNING id`，冲突时再按唯一键查询既有记录。
3. `academic_roster_sync_service.py`：
   - 教务名单同步创建 `classes`、`students` 使用 `execute_insert_returning_id()`。
   - `teacher_academic_roster_sync_items` upsert 在 PostgreSQL 下使用 `RETURNING id`。
4. `academic_exam_roster_sync_service.py`：`teacher_academic_exam_roster_items` upsert 在 PostgreSQL 下使用 `RETURNING id`。
5. `academic_course_exam_sync_service.py`：PostgreSQL 模式下 `ensure_course_exam_schema()` 只读校验 `teacher_academic_course_exam_items` 必要列，不执行 SQLite `CREATE TABLE`；任课考试日历事件创建使用统一主键返回 helper。
6. `academic_invigilation_sync_service.py`：监考日历事件创建使用统一主键返回 helper。

新增测试 `tests/test_academic_sync_postgres_writes.py`，覆盖 PostgreSQL 下 schema 只读校验、`ON CONFLICT DO NOTHING` 和 upsert `RETURNING id`。

验证结果：

1. `python -m py_compile` 针对教务同步改动文件通过。
2. `python -m unittest tests.test_academic_sync_postgres_writes` 通过。
3. `python -m unittest tests.test_db_postgres_schema tests.test_academic_sync_postgres_writes tests.test_manage_postgres_writes` 通过，schema gate 输出 `40/40 required tables`。

注意：教务同步链路仍需后续在真实 PostgreSQL app 环境中做端到端验证；本轮只完成 Python 层方言与主键返回风险的收口。

## 2026-06-06 后续写路径收口记录

本轮继续收口 `CUT-R005` 下的 app/worker 历史写入路径，重点是所有“插入后立即依赖新 ID”的业务逻辑。

新增覆盖域：

1. Agent 任务、Agent API Key、Agent 平台动作。
2. 反馈、自定义表情、课程文件上传与秒传关联。
3. 消息中心通知、私信、私信附件。
4. 材料 folder/file、final material 完成记录、material AI import、session material generation 生成资料。
5. 教师邮箱配置、学生密码找回申请、教师账号创建、学生手动待办、智能考勤每日同步任务。
6. 课堂行为事件、博客发帖/评论/附件、课堂互动、协作小组/文件/提交/互评、讨论附件、电子签名主记录。

新增测试覆盖：

`tests/test_agent_postgres_writes.py`、`tests/test_router_postgres_writes.py`、`tests/test_account_todo_postgres_writes.py`、`tests/test_blog_postgres_writes.py`、`tests/test_behavior_postgres_writes.py`、`tests/test_classroom_interaction_postgres_writes.py`、`tests/test_collaboration_postgres_writes.py`、`tests/test_file_related_postgres_writes.py`。

当前约束：

1. 单纯需要新 ID 的 insert 应优先使用 `execute_insert_returning_id()`。
2. 需要整行返回的队列/状态机可继续显式 `RETURNING *`，但必须有测试证明 PostgreSQL 分支不会依赖 SQLite `lastrowid`。
3. 带唯一去重语义的任务创建必须用 `ON CONFLICT ... DO NOTHING RETURNING id` 或等价实现，不能简单套 helper。
4. 剩余 `lastrowid` 命中尚未清零，`CUT-R005` 不得关闭。

## 2026-06-06 后续队列与事务收口记录

本轮完成以下并发相关改进：

1. `private_message_ai_jobs` 创建路径统一 `RETURNING id` 后读回完整行，避免 PostgreSQL/SQLite 创建返回结构分叉。
2. `session_material_generation_tasks` 创建路径统一 `RETURNING id` 后读回完整行，继续保留 queued/running 状态机领取保护。
3. `blog_news_crawler_items` 在 PostgreSQL 下使用 `ON CONFLICT DO NOTHING RETURNING *`，避免唯一冲突导致事务进入失败状态。
4. 教务同步 SQLite fallback 不再依赖 `lastrowid`，改为业务唯一键查回 id，减少不同数据库 driver 行为差异。

验收证据：

- `python -m unittest tests.test_message_center_private_ai_jobs tests.test_session_material_generation_queue_claim tests.test_blog_news_crawler_queue_claim tests.test_academic_sync_postgres_writes`
- P01 回归集合：`Ran 224 tests ... OK`。

当前仍需真实 PostgreSQL app/worker 全栈运行验证，不能仅凭 fake connection 单元测试放行 cutover。

## 2026-06-06 元数据 helper 对并发目标的补充记录

本轮新增的元数据 helper 适配不直接改变队列领取算法，但降低了 PostgreSQL app/worker 启动和运行时误执行 SQLite 元数据 SQL 的风险：

1. 后台任务账本、基础资源模式、组织管理、组织作用域和讨论附件 schema 检查在 PostgreSQL 下使用 `information_schema`。
2. 这些 helper 的失败策略是“缺表/缺列即 fail fast”，避免 app 在 PostgreSQL 上静默执行 SQLite DDL 或继续带病运行。
3. P01 回归集合扩展后通过：`Ran 229 tests ... OK`。

剩余验收仍必须包含真实 PostgreSQL app/worker 全栈运行、队列多实例领取、长事务和锁等待观测；当前结果不能替代阶段 5 的真实切换后验证。
## 2026-06-06 聊天历史迁移并发与方言补强

本轮继续收口 PostgreSQL app/worker 全栈运行前的运行时方言风险：

1. `ensure_chat_log_schema()` 在 PostgreSQL 下改为只读 schema 校验，避免多个 app/worker 进程启动时同时执行 SQLite 兼容 DDL。
2. `chat_log_migrations` 的迁移标记在 PostgreSQL 下改为 `INSERT ... ON CONFLICT (class_offering_id) DO UPDATE`，避免 SQLite `INSERT OR REPLACE` 方言失败。
3. `chat_logs`、`chat_log_migrations`、`discussion_attachments` 已纳入 PostgreSQL required schema gate，当前测试输出为 `43/43 required tables`。
4. SQLite 模式的历史日志迁移兼容行为保持不变，避免影响当前线上权威 SQLite 数据。

验证记录：

1. `python -m unittest tests.test_remaining_postgres_write_paths tests.test_db_postgres_schema` 通过。
2. 相关测试确认 PostgreSQL 路径不执行 `CREATE TABLE`、`ALTER TABLE`、`INSERT OR REPLACE`。

该项只减少 `CUT-R005` 的 app 全栈风险，不放宽最终 cutover gate。

## 2026-06-06 邮件 worker 队列表 schema gate 增量

本轮继续补强 mailer 队列在 PostgreSQL 下的启动前校验：

1. `teacher_email_configs` 纳入 required schema，保证 mailer 发送时读取默认邮箱配置、频率限制和发送计数的表结构存在。
2. `email_worker_heartbeats` 纳入 required schema，保证 app health 和 mailer 进程都能读写 heartbeat。
3. `email_outbox` required columns 增加 `attempt_count`、`sent_at`、`last_error`，覆盖重试、成功/失败终态写回和错误记录。
4. 相关队列领取测试仍覆盖 PostgreSQL `FOR UPDATE SKIP LOCKED` 和 `ON CONFLICT ... RETURNING id`。

验证记录：

1. `python -m unittest tests.test_db_postgres_schema tests.test_email_notification_queue_claim tests.test_api_contract_schemas` 通过，`Ran 20 tests ... OK`。
2. schema gate 输出 `PostgreSQL schema verified: 45/45 required tables`。

该项降低 mailer/health 在阶段 5 postflight 中暴露 5xx 的风险，但仍不能替代真实 PostgreSQL app/worker 全栈运行。

## 2026-06-06 课堂协作与实时互动并发 gate 补强

本轮继续围绕课堂内高频写入路径降低 PostgreSQL 切换风险，重点覆盖小组协作、互评、投票、课堂问答和求助信号。

新增并发相关约束：

1. `study_groups`、`study_group_members`、`study_group_files`、`group_submissions`、`peer_reviews` 必须在 PostgreSQL 启动前通过 required schema gate，避免小组协作入口在真实课堂中首个写请求才暴露缺表缺列。
2. `classroom_live_activities`、`classroom_live_options`、`classroom_live_responses`、`classroom_live_questions`、`classroom_live_help_signals` 必须在 PostgreSQL 启动前通过 required schema gate，避免投票、问答、求助等高频课堂互动在阶段 5 验收中出现运行时 5xx。
3. 写入路径测试必须继续禁止绕过 connection facade 的裸 `cursor()`，并确保创建后依赖新 ID 的路径使用 PostgreSQL `RETURNING` 语义或等价 helper。
4. 本轮不把 fake connection 单元测试当作最终并发验收；真实放行仍必须执行 PostgreSQL app/worker 全栈运行、课堂烟测、队列观察、连接数和锁等待观察。

验证记录：

1. `python -m unittest tests.test_db_postgres_schema tests.test_collaboration_postgres_writes tests.test_classroom_interaction_postgres_writes tests.test_api_contract_schemas` 通过，`Ran 26 tests ... OK`。
2. schema gate 输出 `PostgreSQL schema verified: 75/75 required tables`。
3. `python -m py_compile classroom_app/db/postgres_schema.py tests/test_db_postgres_schema.py classroom_app/services/collaboration_service.py classroom_app/services/classroom_interaction_service.py` 通过。
4. 本地 P01 回归集合通过：`Ran 225 tests ... OK`。

当前结论不变：课堂高频 schema 风险继续降低，但 `CUT-R003` 和 `CUT-R005` 未消除前，不得执行远程阶段 4 配置切换，不得修改生产 `docker.env`，不得触碰线上 `/lanshare/data`。

## 2026-06-06 runtime/互动/上传/学习链路 schema gate 全覆盖

本轮继续降低 PostgreSQL app/worker 全栈运行前的结构性风险，将当前静态 SQLite schema 中的全部 113 张业务表纳入 PostgreSQL required schema gate。

并发与运行时相关新增覆盖：

1. Agent runtime 凭据、检测、使用快照和任务事件表已纳入 gate，避免 Agent worker 或管理页启动后才发现缺表缺列。
2. 博客发布、评论、点赞、收藏、附件、media、moderation、AI reply job 和 news crawler item/config 表已纳入 gate，降低用户可见社交写入和后台 crawler 写入风险。
3. 私信 block/audit、自定义表情、表情使用统计已纳入 gate，降低课堂互动和私信控制逻辑运行时失败风险。
4. 分块上传、课程旧文件、提交草稿附件、错题复盘备注已纳入 gate，降低上传和草稿恢复链路在 PostgreSQL 下缺列失败风险。
5. 学习进度、学习阶段状态、学习路径项、作品集、成长事件和签名相关表已纳入 gate，降低学生成长链路和签名权限链路切换后失败风险。

验证记录：

1. Agent/博客/社交聚焦集合通过：`Ran 51 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 94/94 required tables`。
2. 学习/上传/签名聚焦集合通过：`Ran 41 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 113/113 required tables`。
3. 本地 P01 回归集合通过：`Ran 231 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 113/113 required tables`。
4. 静态 schema 差集复核为 `missing_from_required_count=0`。

仍需注意：schema gate 全覆盖不等于并发验收完成。阶段 4/5 仍必须在 PostgreSQL app/worker 真实环境中验证队列领取、上传、课堂互动、博客社交、签名、学习成长链路的写入成功率、锁等待、连接数和 worker 积压情况。
