# T03 - Schema 迁移与版本管理

## 目标

把当前 SQLite schema 转换为可在 PostgreSQL 中创建、校验和版本追踪的 schema，并建立迁移版本登记机制，避免靠手工 SQL 临时操作生产库。

## 必须处理的差异

1. SQLite `INTEGER PRIMARY KEY` 与 PostgreSQL identity/sequence。
2. SQLite 弱类型到 PostgreSQL `integer`、`numeric`、`boolean`、`text`、`jsonb`、`timestamp` 的映射。
3. 默认值、时间函数、布尔值、空字符串和 NULL。
4. 唯一约束、外键约束、索引和部分索引。
5. SQLite 系统表和 `PRAGMA` 不迁移。
6. 历史类型不一致的外键列必须按父表类型对齐。

## 执行步骤

1. 从 SQLite 副本导出 schema。
2. 生成 PostgreSQL schema SQL。
3. 对所有表建立主键、外键、唯一约束和索引。
4. 数据装载完成后再创建外键和部分索引，降低导入失败面。
5. 修正序列到 `max(id)+1`。
6. 将每次迁移脚本登记到 migration registry。
7. PostgreSQL runtime 启动时只允许校验已迁移 schema，不允许执行 SQLite schema 初始化器或临时建表逻辑。

## 验收条件

- [ ] PostgreSQL 空库能完整创建 118 张业务表。
- [ ] 所有外键约束在数据装载后创建成功。
- [ ] 所有主键序列对齐。
- [ ] schema 生成可重复，输出可审计 SQL 包。
- [ ] schema 变更有版本登记，不依赖人工记忆。
- [ ] `DB_ENGINE=postgres` 时 `init_database()` 只验证必要表和关键列，缺失时 fail-fast，不写生产库 schema。

## 当前执行记录

已完成：

- 新增 `classroom_app/db/migration_registry.py` 和 migrations 目录。
- 新增 `tools/db_schema_plan.py`。
- 新增 `tools/db_postgres_export.py`，可从 SQLite 副本生成 PostgreSQL SQL 包。
- 新增 `classroom_app/db/postgres_schema.py`，提供 PostgreSQL 必要表、关键列和行数的只读校验。
- `classroom_app/db/schema.py` 已增加 PostgreSQL 分支：`DB_ENGINE=postgres` 时调用 schema 校验后返回，不运行 SQLite 初始化器。
- PostgreSQL 启动校验的必要表已扩展到 worker/状态机关键表，包括 `email_outbox`、`agent_tasks`、`blog_news_crawler_runs`、`assignment_wrong_summary_jobs`、`private_messages`、`private_message_attachments`、`private_message_ai_jobs`、`material_ai_import_records`、`session_material_generation_tasks`、`ai_chat_sessions`、`learning_stage_exam_attempts` 和 `learning_certificates`；`course_materials`、`course_material_assignments`、`message_center_notifications`、`email_outbox`、`private_messages`、`private_message_attachments`、`submission_drafts`、`submissions`、`submission_files`、`assignments` 的必要列也已扩展到当前写入路径和 AI grading 实际依赖字段，避免切换后才暴露缺列。
- `wrong_question_summary_service.ensure_wrong_summary_cache_tables()` 在 PostgreSQL 模式下只校验已迁移表和关键列，不执行 SQLite `CREATE TABLE`、`ALTER TABLE` 或 `PRAGMA`。
- 远程 PostgreSQL 装载演练发现并修正了外键 child/parent 类型不一致问题，例如 `group_submissions.assignment_id` 应与 `assignments.id` 对齐。
- 当前 SQL 包已在远程临时 PostgreSQL 中成功执行 schema、data、constraints/indexes、verify counts。

当前状态：schema 装载演练已通过，PostgreSQL runtime 已具备只读 schema 校验保护，并开始覆盖 worker/状态机关键表、课次资料生成任务表和 AI grading 关键列；但自动 schema 版本迁移、业务 SQL 全栈适配和生产切换仍未完成，不允许直接在生产库手工改 schema。
## 2026-06-06 增量执行记录

本轮将 PostgreSQL runtime schema gate 从 26 张必备表扩展到 40 张必备表，新增纳入以下管理端和教务同步实际依赖：

1. `academic_semesters`：学期创建、复用、校历同步排队依赖 `teacher_id`、`school_code`、`school_name`、`name`、`start_date`、`end_date`、`week_count`、校历同步状态字段和时间戳字段。
2. `textbooks`：教材创建/编辑/附件下载依赖标题、作者、出版社、出版日期、简介、目录、附件元数据、标签、所有权和组织作用域字段。
3. `course_lessons`：课程保存后重建课次设置依赖 `course_id`、`order_index`、`title`、`content`、`section_count`、`source_type`、`learning_material_id`。
4. `class_offering_sessions`：课堂保存后重建课堂课次依赖 `class_offering_id`、课程课次引用、顺序、标题、内容、节数、日期、周次、排课来源和材料绑定。
5. `ai_class_configs`：课堂 AI 配置和教师 onboarding 完成链路依赖 `class_offering_id`、`system_prompt`、`syllabus` 和更新时间。
6. `teacher_calendar_events`：任课考试、监考、校历等教师日历事件依赖 source、时间、地点、状态、跳转链接、metadata 和同步时间字段。
7. `teacher_academic_course_sync_items` 与 `teacher_academic_course_session_occurrences`：教务课表同步和课次 occurrence 依赖课程、教学班、周次、节次、地点、同步时间和幂等唯一键字段。
8. `teacher_academic_roster_sync_items` 与 `teacher_academic_roster_memberships`：教务名单同步依赖教学班、课程、班级、学生和成员关系字段。
9. `teacher_academic_invigilation_items`、`teacher_academic_course_exam_items`、`teacher_academic_exam_roster_items`、`teacher_academic_exam_roster_students`：监考、任课考试、考试名单和学生名单同步依赖考试键、课程键、课堂/班级/学生关联、状态和同步时间字段。

同时补强已有核心表的关键列校验：`students`、`classes`、`courses`、`class_offerings` 不再只验证最小主键列，而是覆盖当前管理端写入实际使用的组织作用域、学期/教材绑定、排课和状态字段。

本轮验证结果：

1. `python -m unittest tests.test_db_postgres_schema tests.test_db_postgres_adapter tests.test_manage_postgres_writes` 通过。
2. `init_database()` PostgreSQL 分支输出 `PostgreSQL schema verified: 40/40 required tables`。
3. schema gate 仍保持只读验证，不创建、不修改、不迁移生产 schema；缺表或缺列时必须 fail-fast。
## 2026-06-06 聊天运行时 schema gate 增量

本轮将 PostgreSQL runtime schema gate 从历史记录中的 `40/40` 继续扩展到当前 `43/43 required tables`，新增纳入课堂聊天运行时实际依赖：

1. `chat_logs`：课堂聊天消息主表，必须包含 `logged_at`、`message_type`、`emoji_payload_json`、`attachments_json`、`quote_message_id`、`quote_payload_json` 等运行时列。
2. `chat_log_migrations`：历史 JSONL 聊天日志迁移标记表，必须包含 `class_offering_id` 和 `migrated_at`。
3. `discussion_attachments`：课堂讨论附件表，必须包含缩略图和预览派生文件相关列。

新增约束：

1. PostgreSQL 模式下 `ensure_chat_log_schema()` 只允许通过 `information_schema.columns` 做只读校验，不得执行 `CREATE TABLE`、`ALTER TABLE`、`DROP INDEX` 等 SQLite 兼容迁移。
2. schema 缺失时必须 fail-fast，阻止 app/worker 以半迁移状态启动。
3. SQLite 模式继续保留原有兼容迁移逻辑，避免破坏当前线上 SQLite 数据。

验证记录：

1. `python -m unittest tests.test_remaining_postgres_write_paths tests.test_db_postgres_schema` 通过。
2. `python -m py_compile classroom_app/services/chat_handler.py classroom_app/db/postgres_schema.py` 通过。
3. 测试输出确认：`PostgreSQL schema verified: 43/43 required tables`。

## 2026-06-06 邮件 worker schema gate 增量

本轮将 PostgreSQL runtime schema gate 从 `43/43` 继续扩展到当前 `45/45 required tables`，新增纳入邮件 worker 和 health snapshot 实际依赖：

1. `teacher_email_configs`：教师邮箱配置、SMTP/IMAP 凭据密文字段、默认配置、频率限制、发送状态和计数字段。
2. `email_worker_heartbeats`：mailer 心跳、队列深度、最近错误和更新时间字段。
3. `email_outbox` 关键列补齐：`attempt_count`、`sent_at`、`last_error`，覆盖重试、发送成功/失败写回和错误摘要。

新增约束：

1. PostgreSQL 启动校验必须在 app/worker 正常运行前发现邮件链路缺表或缺列。
2. health 中的 `email_worker` snapshot 不应在切换后才因缺少 `email_worker_heartbeats` 暴露 5xx。
3. 邮件密码仍只允许以密文字段存在于数据库或远程未提交环境配置，不得进入仓库、P01 文档或报告。

验证记录：

1. `python -m unittest tests.test_db_postgres_schema tests.test_email_notification_queue_claim tests.test_api_contract_schemas` 通过。
2. `python -m py_compile classroom_app/db/postgres_schema.py classroom_app/services/email_notification_service.py` 通过。
3. 测试输出确认：`PostgreSQL schema verified: 45/45 required tables`。

## 2026-06-06 AI 聊天 runtime schema gate 增量

本轮将 PostgreSQL runtime schema gate 从 `45/45` 继续扩展到当前 `47/47 required tables`，新增纳入 AI 聊天运行时实际依赖：

1. `ai_chat_messages`：AI 聊天消息表，必须包含 `session_id`、`role`、`message`、`thinking_content`、`final_answer`、`attachments_json`、`timestamp`。
2. `ai_psychology_profiles`：内部学习支持快照表，必须包含 `class_offering_id`、`session_id`、`user_pk`、`user_role`、`round_index`、`profile_summary`、`mental_state_summary`、`support_strategy`、`hidden_premise_prompt`、`confidence`、`raw_payload`、`created_at`。

新增约束：

1. PostgreSQL 启动校验必须在 AI 面板真实请求前发现消息表或心理画像表缺失。
2. 缺少 `thinking_content` 或 `final_answer` 时不得放行，否则历史消息恢复和流式回答落库会在切换后失败。
3. 该 gate 仍为只读校验，不创建、不修改生产 schema。

验证记录：

1. `python -m unittest tests.test_db_postgres_schema tests.test_api_contract_schemas` 通过。
2. `python -m py_compile classroom_app/db/postgres_schema.py classroom_app/routers/ai.py` 通过。
3. 测试输出确认：`PostgreSQL schema verified: 47/47 required tables`。
## 2026-06-06 启动 worker 与运行时业务表 schema gate 增量

本轮继续收口 `CUT-R005` 中“应用/worker 在 PostgreSQL 模式下启动后才暴露缺表缺列”的风险，PostgreSQL runtime schema gate 从 `47/47` 扩展到 `57/57 required tables`。

新增纳入 required schema 的表：

1. `assignment_wrong_answer_ai_cache`：错题主观题 AI 错答分组缓存，覆盖 `answer_signature`、`prompt_version`、`result_json`、`created_at`、`updated_at`。
2. `exam_paper_difficulty_ai_cache`：试卷难度 AI 缓存，覆盖 `exam_paper_id`、`questions_signature`、`prompt_version`、`result_json`。
3. `classroom_behavior_states`：课堂行为状态与画像调度状态，覆盖在线累计、焦点/可见性累计、画像 pending、下一次画像时间等字段。
4. `classroom_behavior_profiles`：课堂行为画像结果，覆盖 `trigger_event_id`、`activity_count_snapshot`、`support_strategy`、`hidden_premise_prompt`、`trigger_mode`、`raw_payload`。
5. `teacher_smart_classroom_credentials`：智慧教室凭据密文字段、校验状态、访问方式快照。
6. `smart_classroom_schedule_items`：智慧教室远端课表同步结果。
7. `smart_classroom_checkin_sessions`：智慧教室签到场次同步结果。
8. `smart_classroom_checkin_students`：智慧教室签到学生明细。
9. `smart_attendance_daily_tasks`：教师每日签到同步后台任务。
10. `smart_attendance_student_advice`：学生考勤 AI 建议任务和缓存。

同时补强已有 required 表：

1. `assignment_wrong_summary_jobs` 的 required columns 从最小状态机字段扩展到 `teacher_id`、`pending_text_questions`、`pending_difficulty`、`error_message`、`created_at`、`started_at`、`completed_at`、`updated_at` 等实际运行字段。
2. `classroom_behavior_events` 的 required columns 从最小主键/课堂/时间扩展到 `user_pk`、`user_role`、`display_name`、`action_type`、`summary_text`、`payload_json`。

新增约束：

1. PostgreSQL 模式下，错题归集、行为追踪、智慧教室和智能考勤相关表必须在 app/worker 启动前通过只读 schema 校验。
2. 该 gate 只读 `information_schema`，不创建、不修改、不迁移生产 schema；缺表或缺列时必须 fail-fast。
3. 该项只降低 `CUT-R005` 风险，不消除 `CUT-R003`，也不代表允许生产阶段 4 切换。

验证记录：

1. `python -m unittest tests.test_db_postgres_schema tests.test_wrong_question_summary_service` 通过，输出 `PostgreSQL schema verified: 49/49 required tables`。
2. `python -m unittest tests.test_db_postgres_schema tests.test_behavior_postgres_writes tests.test_api_contract_schemas` 通过，输出 `PostgreSQL schema verified: 51/51 required tables`。
3. `python -m unittest tests.test_db_postgres_schema tests.test_account_todo_postgres_writes tests.test_api_contract_schemas` 通过，输出 `PostgreSQL schema verified: 57/57 required tables`。
4. `python -m py_compile classroom_app/db/postgres_schema.py classroom_app/services/wrong_question_summary_service.py classroom_app/services/behavior_tracking_service.py classroom_app/services/smart_attendance_advice_service.py classroom_app/services/smart_attendance_entry_service.py classroom_app/services/smart_classroom_checkin_sync_service.py classroom_app/services/smart_classroom_integration_service.py` 通过。

## 2026-06-06 account/integration 与课堂协作 runtime schema gate 增量

本轮继续降低 `CUT-R005` 中 app/worker 切到 PostgreSQL 后才暴露缺表缺列的风险，required schema 从 `57/57` 继续扩展到 `75/75 required tables`。

新增纳入 required schema 的 account/support/integration 表：

1. `student_login_audit_logs`：学生登录审计、设备识别和登录序列依赖。
2. `student_password_reset_requests`：学生找回密码申请、教师审核和完成状态依赖。
3. `classroom_todos`：课堂待办的 owner、完成、软删除和 metadata 依赖。
4. `app_feedback` 与 `app_feedback_attachments`：反馈正文和附件元数据依赖。
5. `teacher_git_credentials`：教师 Git 凭据密文字段、远程标识和最近使用时间依赖。
6. `teacher_academic_system_credentials`：教务系统凭据密文、学校作用域、验证状态和访问方式快照依赖。
7. `teacher_academic_teaching_places`：教务教学地点同步、教室容量和批次标识依赖。

新增纳入 required schema 的课堂协作与实时互动表：

1. `study_groups`、`study_group_members`、`study_group_files`：小组、成员贡献、文件元数据和 metadata 依赖。
2. `group_submissions`：小组提交、最终文件、博客关联和教师反馈依赖。
3. `peer_reviews`：组内互评、分项评分、可见性和状态依赖。
4. `classroom_live_activities`、`classroom_live_options`、`classroom_live_responses`：投票/互动活动、选项、学生回答和匿名设置依赖。
5. `classroom_live_questions`、`classroom_live_help_signals`：课堂问答、求助信号、教师处理状态和 metadata 依赖。

新增约束：

1. PostgreSQL 模式下，上述 18 张表及其关键运行时列必须在 app/worker 启动前通过只读 `information_schema` 校验。
2. schema gate 仍然只读，不创建、不修改、不迁移生产 schema；缺表或缺列时必须 fail-fast。
3. 课堂协作和实时互动属于课堂高频写入路径，本轮只证明 schema 前置校验覆盖，不替代真实 PostgreSQL 全栈运行、并发压测和远程 Compose app 验证。

验证记录：

1. `python -m unittest tests.test_db_postgres_schema tests.test_account_todo_postgres_writes tests.test_router_postgres_writes tests.test_academic_sync_postgres_writes tests.test_api_contract_schemas` 通过，输出 `PostgreSQL schema verified: 65/65 required tables`，`Ran 33 tests ... OK`。
2. `python -m unittest tests.test_db_postgres_schema tests.test_collaboration_postgres_writes tests.test_classroom_interaction_postgres_writes tests.test_api_contract_schemas` 通过，输出 `PostgreSQL schema verified: 75/75 required tables`，`Ran 26 tests ... OK`。
3. `python -m py_compile classroom_app/db/postgres_schema.py tests/test_db_postgres_schema.py classroom_app/services/collaboration_service.py classroom_app/services/classroom_interaction_service.py` 通过。

## 2026-06-06 静态 SQLite schema 表全量纳入 required gate

本轮将 PostgreSQL required schema 从 `75/75` 扩展到 `113/113 required tables`，覆盖当前 `classroom_app/db/schema_*.py` 中通过 `CREATE TABLE IF NOT EXISTS` 静态声明的全部 113 张业务表。

新增覆盖范围：

1. Agent runtime 与任务事件：`agent_task_events`、`agent_task_composers`、`agent_runtime_api_keys`、`agent_runtime_key_checks`、`agent_runtime_usage_snapshots`。
2. 博客与资讯 crawler：`blog_news_crawler_config`、`blog_news_crawler_items`、`blog_posts`、`blog_comments`、`blog_likes`、`blog_bookmarks`、`blog_attachments`、`blog_media_assets`、`blog_moderation_logs`、`blog_ai_reply_jobs`。
3. 私信控制和课堂表情：`private_message_blocks`、`private_message_audit_logs`、`custom_emojis`、`emoji_usage_stats`。
4. 基础配置、上传与草稿：`system_settings`、`teacher_onboarding_state`、`student_shared_teacher_notes`、`academic_semester_calendar_days`、`course_files`、`chunked_uploads`、`submission_draft_files`、`student_feedback_review_notes`。
5. UI/讨论快照、学习成长、作品集与签名：`ui_copy_snapshots`、`discussion_mood_snapshots`、`learning_material_progress`、`learning_stage_status`、`student_learning_path_item_states`、`student_portfolio_items`、`student_portfolio_reflections`、`student_growth_events`、`electronic_signatures`、`signature_usage_logs`、`signature_access_requests`。

新增约束：

1. `tests/test_db_postgres_schema.py` 会自动扫描 `schema_*.py` 中的静态建表语句，并断言所有表都纳入 `REQUIRED_POSTGRES_TABLES`。
2. required schema gate 继续只读 `information_schema`，不创建、不修改、不迁移生产 schema。
3. 该项只证明启动前表/列门禁覆盖，不替代真实 PostgreSQL app/worker 全栈运行和远程 Docker Compose app 验证。

验证记录：

1. `python -m unittest tests.test_db_postgres_schema tests.test_agent_postgres_writes tests.test_agent_task_service tests.test_blog_postgres_writes tests.test_blog_news_crawler_queue_claim tests.test_router_postgres_writes tests.test_message_center_private_ai_jobs tests.test_api_contract_schemas` 通过，输出 `PostgreSQL schema verified: 94/94 required tables`，`Ran 51 tests ... OK`。
2. `python -m unittest tests.test_db_postgres_schema tests.test_remaining_postgres_write_paths tests.test_file_related_postgres_writes tests.test_materials_postgres_writes tests.test_high_concurrency_smoke_postgres tests.test_api_contract_schemas` 通过，输出 `PostgreSQL schema verified: 113/113 required tables`，`Ran 41 tests ... OK`。
3. 静态 schema 差集复核结果为 `missing_from_required_count=0`。
4. 本地 P01 回归集合通过：`Ran 231 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 113/113 required tables`。

## 2026-06-06 迁移/修复层支撑表纳入 required gate

本轮在静态 `schema_*.py` 全覆盖基础上，继续将迁移/修复层创建的权限和会话支撑表纳入 PostgreSQL required schema gate，使 gate 从 `113/113` 扩展到 `118/118 required tables`。

新增 required tables：

1. `user_sessions`：由 `db/repair.py` 创建，用于登录会话、过期清理和会话索引修复。
2. `organization_schools`、`organization_colleges`、`organization_departments`：由 `db/migrations.py` 创建，用于组织目录和作用域筛选。
3. `teacher_organization_memberships`：由 `db/migrations.py` 创建，用于教师多组织身份、主组织和启停状态。

新增约束：

1. PostgreSQL app/worker 启动前必须确认登录态表和组织作用域表存在，避免权限边界在切换后退化。
2. 这些表仍只通过 `information_schema` 只读校验，不在生产 PostgreSQL 启动时自动建表或修表。

验证记录：

1. `python -m unittest tests.test_db_postgres_schema tests.test_postgres_metadata_helpers tests.test_base_resource_modes_service tests.test_permission_resource_access tests.test_permission_course_files tests.test_architecture_import_compatibility` 通过，输出 `PostgreSQL schema verified: 118/118 required tables`，`Ran 46 tests ... OK`。
2. `python -m py_compile classroom_app/db/postgres_schema.py tests/test_db_postgres_schema.py classroom_app/db/repair.py classroom_app/db/sessions.py classroom_app/services/organization_management_service.py classroom_app/services/organization_scope_service.py classroom_app/services/teacher_account_service.py` 通过。
3. 本地 P01 回归集合通过：`Ran 246 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 118/118 required tables`。
