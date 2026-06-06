# T08 - Win11 本地测试与远程等价实验

## 目标

在 Windows 11 本地建立可重复测试流程，并在本机缺少 Docker/psql 时，用远程 Linux 临时环境完成等价 PostgreSQL 数据层演练。

## 本地环境要求

理想情况下应具备：

1. Python 和项目虚拟环境。
2. Node/npm，用于前端构建。
3. Docker Desktop 或本机 PostgreSQL。
4. `psql` 客户端。
5. 可清理的 `.codex-temp` 实验目录。

当前 Win11 实际情况：

- Python 可用。
- `npm` 缺失。
- `docker` 缺失。
- `docker compose` 缺失。
- `psql` 缺失。

## 执行策略

1. 本机运行所有无需 Docker 的 Python 单元测试和报告生成工具。
2. 本机所有数据实验必须使用 `.codex-temp` 下的 SQLite 副本。
3. 需要 Docker/PostgreSQL 的实验转移到远程 Linux 临时 `/tmp/lanshare-*` 目录。
4. 远程实验不得访问或修改 `/lanshare/data`。
5. 远程实验完成后必须清理临时容器、网络、目录和上传包。

## 验收条件

- [ ] 本机工具链报告明确记录缺失命令。
- [ ] 本机可运行的 Python 单测通过。
- [ ] 远程等价 PostgreSQL 装载报告保存到 `.codex-temp`。
- [ ] 远程临时资源清理完成。
- [ ] 不把本机缺少 Docker 误写为迁移已失败；也不把远程数据层演练误写为 app 已适配。

## 当前执行记录

已完成：

- 生成 `.codex-temp/pg-migration-lab/reports/environment.json`，记录本机缺失命令。
- 本机完成 SQLite 副本 readiness、file integrity、performance acceptance、cutover gate 等报告。
- 远程 Linux 完成 PostgreSQL 数据装载、dump/restore、Compose 数据层性能演练。

当前状态：T08 的本机可运行部分已完成；本机仍不具备完整 Docker/psql 回归能力。后续若要做 app PostgreSQL 联调，可安装本机 Docker/psql，或继续使用远程临时 Compose 环境。
## 2026-06-06 增量本地验证记录

本轮在 Win11 本地完成以下不依赖 Docker/psql 的验证：

1. `python -m py_compile classroom_app\routers\manage_parts\common.py classroom_app\routers\manage_parts\classes_courses_classes.py classroom_app\routers\manage_parts\classes_courses_courses.py classroom_app\routers\manage_parts\classes_courses_offerings.py classroom_app\routers\manage_parts\classes_courses_onboarding.py classroom_app\routers\manage_parts\semesters_textbooks.py classroom_app\db\postgres_schema.py tests\test_manage_postgres_writes.py`
2. `python -m unittest tests.test_manage_postgres_writes`
3. `python -m unittest tests.test_db_postgres_schema tests.test_db_postgres_adapter tests.test_manage_postgres_writes`
4. `rg -n "lastrowid|conn\.cursor\(" classroom_app\routers\manage_parts -g "*.py"` 无匹配。

本地验证结论：

1. Win11 当前仍不能替代完整 PostgreSQL app 联调，因为本地缺少 Docker/psql。
2. 本轮已经证明管理端教学基础数据写入路径的 Python 代码和 helper 调用在本地可测试、可回归。
3. 后续如需验证真实 PostgreSQL 连接、容器网络、Compose healthcheck，仍需安装本地 Docker/psql，或继续使用远程临时 Compose 环境。

## 2026-06-06 教务同步本地验证记录

新增完成以下本地验证：

1. `python -m py_compile` 针对 `academic_calendar_sync_service.py`、`academic_course_exam_sync_service.py`、`academic_course_sync_service.py`、`academic_exam_roster_sync_service.py`、`academic_invigilation_sync_service.py`、`academic_roster_sync_service.py` 和 `tests/test_academic_sync_postgres_writes.py` 通过。
2. `python -m unittest tests.test_academic_sync_postgres_writes` 通过。
3. `python -m unittest tests.test_db_postgres_schema tests.test_academic_sync_postgres_writes tests.test_manage_postgres_writes` 通过，schema gate 输出 `40/40 required tables`。

本地结论：教务同步写入路径的 PostgreSQL 方言分支和主键返回逻辑可通过 fake connection 验证；真实 PostgreSQL 网络、容器和 app 端到端同步仍需远程临时 Compose 或后续本地 Docker/psql 环境验证。

## 2026-06-06 继续本地验证记录

新增通过的本地单元测试：

1. `tests.test_agent_postgres_writes`
2. `tests.test_router_postgres_writes`
3. `tests.test_account_todo_postgres_writes`
4. `tests.test_blog_postgres_writes`
5. `tests.test_behavior_postgres_writes`
6. `tests.test_classroom_interaction_postgres_writes`
7. `tests.test_collaboration_postgres_writes`
8. `tests.test_file_related_postgres_writes`

新增验证结论：

1. 本轮新增 fake connection 测试均禁止裸 `cursor()`，可防止未来绕过 PostgreSQL facade。
2. 本轮验证覆盖 Agent、反馈/表情/文件、消息中心、材料、账户/待办/考勤、行为事件、博客、课堂互动、协作、讨论附件、电子签名主记录。
3. 这些测试仍属于 Python 层方言/契约验证，不等价于真实 PostgreSQL app 端到端联调。
4. 本地 Win11 仍缺少完整 Docker/psql 验证能力，真实容器网络和 app PostgreSQL 运行必须在后续本地 Docker/psql 或远程临时 Compose 环境验证。

## 2026-06-06 后续本地验证记录

本轮新增并通过以下验证：

1. 新增 `tests/test_remaining_postgres_write_paths.py`，覆盖 chat、materials git、portfolio、signature access request、submission file alignment、blog crawler item/run 等历史写入路径。
2. 更新 `tests/test_message_center_private_ai_jobs.py`、`tests/test_session_material_generation_queue_claim.py`、`tests/test_blog_news_crawler_queue_claim.py`，将创建路径契约同步为统一 `RETURNING id` 后按 id 读回完整行。
3. 受影响测试组：`Ran 33 tests ... OK`。
4. P01 回归集合：`Ran 224 tests ... OK`。
5. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示。
6. 敏感配置扫描未发现真实 PostgreSQL 密码或生产连接串进入代码、目标文档、报告或日志。

当前本地结论：

- 本地 Python 层回归继续通过。
- 未执行生产 cutover。
- 未修改线上 `/lanshare/data`。
- 仍需真实 PostgreSQL app/worker 全栈运行验证和远程 Compose app 验证。

## 2026-06-06 元数据 helper 本地验证记录

本轮新增并通过以下验证：

1. 新增 `tests/test_postgres_metadata_helpers.py`，覆盖 base resource、organization management、organization scope 的 PostgreSQL table-exists 元数据查询。
2. 扩展 `tests/test_file_related_postgres_writes.py`，验证 discussion attachment 在 PostgreSQL 下只查 `information_schema.columns`，不执行 `CREATE TABLE` 或 `PRAGMA`。
3. 扩展 `tests/test_background_task_ledger.py`，验证 background task ledger 在 PostgreSQL 下只查 `information_schema.tables` / `information_schema.columns`。
4. 相关测试组：`Ran 19 tests ... OK`。
5. P01 回归集合：`Ran 229 tests ... OK`。

当前本地结论：运行时元数据读取风险进一步降低，但仍不是完整 PostgreSQL app/worker 端到端验证。

## 2026-06-06 高并发烟测工具本地验证记录

本轮新增并通过以下本地验证：

1. `tools/high_concurrency_smoke.py` 的测试数据种子写入改为使用统一主键返回 helper，避免 PostgreSQL 模式下依赖 SQLite `cursor.lastrowid`。
2. 新增 `tests/test_high_concurrency_smoke_postgres.py`，用 fake connection 验证 PostgreSQL engine 下教师、班级、课程、课堂和学生种子插入均使用 `RETURNING id`。
3. `python -m unittest tests.test_high_concurrency_smoke_postgres tests.test_full_stack_load_profile` 通过：`Ran 6 tests ... OK`。
4. `python -m py_compile tools\high_concurrency_smoke.py tests\test_high_concurrency_smoke_postgres.py` 通过。
5. `rg -n "lastrowid|last_insert_rowid\(" tools\high_concurrency_smoke.py tools\full_stack_load_test.py classroom_app -g "*.py"` 当前只剩隔离 SQLite 副本压测工具、统一 helper、迁移标记和 SQL helper 元数据命中。

本地结论：真实 app 连接型高并发烟测工具已减少一个 PostgreSQL 阻断点；但 Win11 本地仍缺少 Docker/psql，不能替代真实 PostgreSQL app/worker 端到端验证。

## 2026-06-06 高并发烟测可重复运行本地验证

本轮新增并通过以下验证：

1. `tools/high_concurrency_smoke.py` 的测试数据种子已支持唯一 `run_id` 后缀，避免重复运行时固定老师邮箱、学号、学生邮箱等字段撞唯一约束。
2. 教师登录流程改为读取 seed 返回的动态教师邮箱和密码。
3. `tests/test_high_concurrency_smoke_postgres.py` 覆盖动态 run 后缀、`RETURNING id` 主键返回和动态教师登录。
4. `python -m unittest tests.test_high_concurrency_smoke_postgres tests.test_full_stack_load_profile` 通过：`Ran 7 tests ... OK`。
5. `python -m py_compile tools\high_concurrency_smoke.py tests\test_high_concurrency_smoke_postgres.py` 通过。

本地结论：烟测工具的重复执行风险降低；真实 PostgreSQL 网络、容器和 app/worker 验证仍需后续阶段执行。
## 2026-06-06 本地双后端 health 验收补充

本地 Win11 验证必须把“服务是否启动”和“服务实际连接哪个数据库”分开记录。

新增要求：

1. 默认 SQLite 临时 app 验证时，执行 `tools/db/run_dual_backend_tests.ps1 -BaseUrl http://127.0.0.1:<port> -ExpectedDbEngine sqlite`。
2. PostgreSQL 临时 app 验证时，必须使用 `.codex-temp` 下的临时数据根和临时 PostgreSQL 连接，执行 `tools/db/run_dual_backend_tests.ps1 -BaseUrl http://127.0.0.1:<port> -ExpectedDbEngine postgres`。
3. 本地验证不得连接真实 `data/classroom.db` 做写入测试，不得把真实 `DATABASE_URL` 或密码写入报告。
4. `api-health-database-backend.json` 必须显示期望 engine 且 `configured=true`；否则对应后端验证不通过。
5. 本地缺少 Docker 或 PostgreSQL 客户端时，必须把缺口写入报告，不能把缺口视为通过。

这些检查只作为阶段 0 可行性验证；远程阶段 4 仍必须等待 T12 gate 为 `ready`。
## 2026-06-06 聊天运行时 PostgreSQL 本地验证记录

本轮在 Win11 本地完成聊天运行时 PostgreSQL 方言风险的单元级验证：

1. `tests.test_remaining_postgres_write_paths` 覆盖 `ensure_chat_log_schema()` PostgreSQL 只读校验，确认只访问 `information_schema.columns`。
2. 同一测试覆盖 `chat_log_migrations` PostgreSQL upsert，确认不使用 SQLite `INSERT OR REPLACE`。
3. `tests.test_db_postgres_schema` 覆盖 `chat_logs`、`chat_log_migrations`、`discussion_attachments` 纳入 required schema。
4. `python -m py_compile classroom_app/services/chat_handler.py classroom_app/db/postgres_schema.py` 通过。

这些验证仍是本地阶段 0 证据；真实 PostgreSQL app/worker 全栈运行和远程 Compose app 验证仍待阶段 4 前后按 T12 gate 执行。
## 2026-06-06 AI 聊天 runtime schema 本地验证记录

本轮在 Win11 本地完成 AI 聊天 runtime schema gate 的单元级验证：

1. `tests.test_db_postgres_schema` 覆盖 `ai_chat_messages` 和 `ai_psychology_profiles` 纳入 required schema。
2. `ai_chat_messages` 校验 `thinking_content`、`final_answer`、`attachments_json` 等实际读写字段。
3. `ai_psychology_profiles` 校验 `hidden_premise_prompt`、`support_strategy`、`raw_payload` 等内部学习支持字段。
4. `python -m unittest tests.test_db_postgres_schema tests.test_api_contract_schemas` 通过，`Ran 14 tests ... OK`。
5. `python -m py_compile classroom_app/db/postgres_schema.py classroom_app/routers/ai.py` 通过。

这些验证只证明 schema gate 的覆盖范围增强；真实 AI 聊天在 PostgreSQL app 中的端到端验证仍属于后续全栈验收。
