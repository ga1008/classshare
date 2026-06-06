# P01 - SQLite 迁移到 PostgreSQL 总目标

## 总目标

在不损坏线上 `/lanshare/data` 的前提下，建立一套可执行、可控制、可回滚、可验收的数据库迁移目标体系，将当前 SQLite 单文件数据库逐步迁移到 PostgreSQL。迁移不是简单换连接串，而是要覆盖数据模型、SQL 方言、事务并发、附件一致性、部署、备份恢复、性能和最终切换门禁。

## 最高安全原则

1. 任何分析、演练、压测都必须使用 SQLite 副本、远程快照副本或临时 PostgreSQL 容器。
2. 不允许直接修改本地真实 `data/classroom.db` 或远程 `/lanshare/data/classroom.db`。
3. 不允许部署脚本覆盖、删除、移动远程 `/lanshare/data`。
4. 不允许把真实数据库密码、Cookie、Token、生产连接串写入仓库、报告或提交记录。
5. 只有最终门禁状态为 `ready`，才允许修改生产 `docker.env` 启用 PostgreSQL。
6. 当前应用 PostgreSQL adapter 只允许在显式 `POSTGRES_BACKEND_READY=true` 时受控启用，且 PostgreSQL 启动路径只做 schema 校验，不执行 SQLite 建表或迁移初始化；生产仍不能强行设置 `DB_ENGINE=postgres`。

## 环境边界

- 本地开发测试环境：Windows 11。
- 当前 Win11 环境缺少 `npm`、`docker`、`docker compose`、`psql`，本机无法完成真实 PostgreSQL 容器回归。
- 最终部署环境：远程 Linux，项目根目录使用 Docker Compose 管理。
- 远程环境不能依赖临时外网访问，镜像和依赖必须提前准备或验证已存在。
- 远程运行临时演练时，只允许使用 `/tmp/lanshare-*` 目录和临时容器，不得写入 `/lanshare/data`。

## 目标文件

| 编号 | 文件 | 目标 |
| --- | --- | --- |
| T01 | `T01-database-inventory-and-boundaries.md` | 梳理所有数据库操作、运行时边界和数据域 |
| T02 | `T02-data-access-adapter-and-connection-pool.md` | 建立双后端连接入口、连接池和 fail-closed 策略 |
| T03 | `T03-schema-migration-versioning.md` | 建立 schema 迁移和版本登记机制 |
| T04 | `T04-sql-dialect-row-contract.md` | 梳理 SQL 方言差异和 row 访问契约 |
| T05 | `T05-transaction-queue-concurrency.md` | 处理事务、队列、并发写入和锁语义 |
| T06 | `T06-data-migration-and-integrity.md` | 建立数据导出、装载、校验和完整性迁移流程 |
| T07 | `T07-file-metadata-atomicity.md` | 验证数据库附件元数据与文件系统一致性 |
| T08 | `T08-test-and-local-win11-lab.md` | 建立 Win11 本地测试与远程等价演练要求 |
| T09 | `T09-docker-compose-linux-deployment.md` | 设计远程 Linux Docker Compose 部署方案 |
| T10 | `T10-observability-backup-rollback.md` | 建立监控、备份、恢复、回滚目标 |
| T11 | `T11-performance-index-acceptance.md` | 建立性能、索引、容量和压测验收标准 |
| T12 | `T12-final-cutover-gates.md` | 定义最终切换门禁和成功验收标准 |
| Runbook | `RUNBOOK-remote-postgres-staged-cutover.md` | 远程 PostgreSQL 分阶段部署、验证、切换、恢复手册 |

## 当前总体结论

截至 2026-06-05，已经完成：

1. 远程生产 SQLite 快照只读复制到本地 `.codex-temp/remote-sqlite-snapshot-current/classroom.remote.db`。
2. 快照 `quick_check=ok`，外键违规 0，表数量 118，`submissions=1253`，`classroom_behavior_events=304021`。
3. 从快照生成 PostgreSQL SQL 包，包含 schema、data、constraints/indexes、count verify。
4. 远程临时 PostgreSQL 容器完成全量装载、约束创建、行数校验、`pg_dump -Fc` 和 `pg_restore` 演练。
5. 远程 Docker Compose 临时 PostgreSQL 项目完成数据层装载和热点查询基线，最大查询耗时 16.212ms。
6. 远程临时容器、网络、工作目录和上传包已清理。
7. PostgreSQL 基础连接 adapter 已开始落地，包含 psycopg 驱动声明、连接包装、占位符转换、session 超时设置和连接错误脱敏。
8. PostgreSQL 启动 schema 校验路径已落地：`init_database()` 在 PostgreSQL 模式下只验证必要表和关键列，不会运行 SQLite schema 初始化器。
9. worker 队列和高风险状态机已开始真实接入：`email_outbox` 使用 PostgreSQL `FOR UPDATE SKIP LOCKED ... RETURNING *` 批量领取，`agent_tasks` 使用事务级 advisory lock 保护单例 running 语义后再领取，`blog_news_crawler_runs` 使用 `SKIP LOCKED/RETURNING` 领取 due pending run，`assignment_wrong_summary_jobs` 使用 `run_token` + queued-only + `SKIP LOCKED/RETURNING` 保护手动状态机；`AI grading` 已补充提交入队状态条件、队列失败 token 回滚保护和回调 token guard；`private_message_ai_jobs` 已补充 PostgreSQL `INSERT ... RETURNING *`、单 job claim、批量恢复 claim 和 finish running guard；`material_ai_import_records` 已补充 queued-only claim 与 PostgreSQL `SKIP LOCKED/RETURNING`；`session_material_generation_tasks` 已补充 PostgreSQL `INSERT ... RETURNING *`、queued-only claim、`SKIP LOCKED/RETURNING` 和 running-only 终态写回，避免旧任务或多实例恢复覆盖新状态。
10. 最终 cutover gate 仍为 `blocked`，没有执行生产 PostgreSQL 切换。
11. 已完成若干关键写入路径的 PostgreSQL 方言收口：`email_outbox` 入队改为 `ON CONFLICT (dedupe_key) DO NOTHING RETURNING id`，`course_materials`、`message_center_notifications`、`private_messages`、`private_message_attachments`、`material_ai_import_records` 和 `session_material_generation_tasks` 创建路径均已使用 PostgreSQL `RETURNING` 获取主键；`course_material_assignments` 幂等写入已改为 PostgreSQL `ON CONFLICT ... DO NOTHING`；散落的 SQLite `BEGIN IMMEDIATE` 已收口到 `begin_immediate_transaction()`，PostgreSQL 下不会发出该 SQLite 专属语句。
12. `execute_insert_returning_id()` 已开始作为统一主键返回入口使用，覆盖普通作业创建、试卷发布作业、草稿创建、学生提交记录创建、阶段试炼尝试/作业/证书写入和 AI 聊天 session 创建。

当前仍有两个硬阻塞：

1. `CUT-R003`：存在 3 条缺失附件引用，必须恢复文件或形成签收豁免。
2. `CUT-R005`：真实 `docker.env` 未请求 PostgreSQL 切换；在 app PostgreSQL adapter、schema 校验、历史业务 SQL 和全栈回归未完成前不得修改。

## 推进顺序

1. 先完成 T01-T07 的代码和数据边界清点。
2. 再完成 T08-T11 的测试、部署、备份和性能证据。
3. 最后由 T12 统一判定是否允许生产切换。
4. 任一目标未通过时，不允许跳过目标直接部署 PostgreSQL。

## 远程分阶段部署原则

远程服务器部署必须拆成独立阶段，不能把“部署数据库、迁移数据、切换应用”合并成一个不可回退动作。

1. 先部署 PostgreSQL 数据库服务，只验证容器、持久化目录、健康检查和备份目录。
2. 迁移 SQLite 数据副本进入 PostgreSQL，严禁修改原始 SQLite 和 `/lanshare/data`。
3. 用脚本直接连接 PostgreSQL 验证数据完整性、关键表行数、约束、序列和关键接口依赖数据。
4. 只有前 3 步通过，才允许修改受控配置文件切换到新数据库并重启 app/worker。
5. 重启后执行 postflight、关键页面和关键 API 验证。
6. 若发现可修复问题，必须先冻结风险写入，再做最小修复并重复验证。
7. 若不可用或风险不可控，按恢复方案切回原 SQLite 服务，并保留所有失败报告。

## 验收总标准

P01 只有在以下条件同时满足时才算完成：

1. 所有 T01-T12 目标均有执行记录和报告。
2. cutover gate 状态为 `ready`。
3. 生产 SQLite、运行时数据目录、PostgreSQL 数据目录均有可恢复备份。
4. 应用、worker、脚本全部使用同一 PostgreSQL 配置。
5. 远程 Docker Compose postflight 通过。
6. 观察窗口内核心业务无阻断故障。
7. 已确认没有 SQLite/PostgreSQL 双写分叉。
8. 已记录最终权威数据库从 SQLite 切换为 PostgreSQL。
## 2026-06-06 增量执行记录

本轮继续按“本地先验证可行性，远程分阶段推进”的原则推进，新增完成以下内容：

1. 管理端教学基础数据写入路径开始收口到 PostgreSQL 主键返回语义：班级创建、单个学生新增、课程保存/创建、课堂开设、教师 onboarding 创建班级/课程/课堂、学期创建、教材创建均已改为通过 `execute_insert_returning_id()` 获取新记录 ID；SQLite 仍使用 `lastrowid`，PostgreSQL 使用 `RETURNING id`。
2. `classroom_app/routers/manage_parts` 当前已无 `lastrowid` 和裸 `conn.cursor()`，避免 PostgreSQL facade 被绕过导致 qmark 占位符无法转换。
3. PostgreSQL schema gate 已扩展到 40 张必备表，新增覆盖 `academic_semesters`、`textbooks`、`course_lessons`、`class_offering_sessions`、`ai_class_configs` 以及教务同步链路的 `teacher_calendar_events`、`teacher_academic_course_sync_items`、`teacher_academic_course_session_occurrences`、`teacher_academic_roster_sync_items`、`teacher_academic_roster_memberships`、`teacher_academic_invigilation_items`、`teacher_academic_course_exam_items`、`teacher_academic_exam_roster_items`、`teacher_academic_exam_roster_students`，并补充学生、班级、课程、课堂、学期、教材、课次、AI 配置和教务同步实际依赖列。
4. 新增 `tests/test_manage_postgres_writes.py`，用 fake connection 验证管理端关键写路径只通过统一 insert helper 获取 ID，且若误用裸 `cursor()` 会测试失败。
5. 已运行并通过：`python -m py_compile` 针对本轮改动文件；`python -m unittest tests.test_manage_postgres_writes`；`python -m unittest tests.test_academic_sync_postgres_writes`；`python -m unittest tests.test_db_postgres_schema tests.test_academic_sync_postgres_writes tests.test_manage_postgres_writes`，其中 schema gate 输出为 `40/40 required tables`。

本轮随后继续推进教务同步服务 PostgreSQL 适配：

1. `academic_calendar_sync_service.py` 创建学期改为 `execute_insert_returning_id()`。
2. `academic_course_sync_service.py` 新建课程改为统一主键返回 helper；教务课表明细和课次 occurrence 的 `INSERT OR IGNORE` 在 PostgreSQL 下改为 `ON CONFLICT ... DO NOTHING`，明细插入使用 `RETURNING id`。
3. `academic_roster_sync_service.py` 创建班级/学生改为统一主键返回 helper；教务名单 upsert 在 PostgreSQL 下使用 `RETURNING id`。
4. `academic_exam_roster_sync_service.py` 考试名单 upsert 在 PostgreSQL 下使用 `RETURNING id`。
5. `academic_course_exam_sync_service.py` 在 PostgreSQL 下只做 `information_schema.columns` 只读 schema 校验，不再执行 SQLite `CREATE TABLE`；教师日历事件创建改为统一主键返回 helper。
6. `academic_invigilation_sync_service.py` 教师日历监考事件创建改为统一主键返回 helper。
7. 新增 `tests/test_academic_sync_postgres_writes.py`，覆盖教务同步 PostgreSQL schema 只读校验、`ON CONFLICT DO NOTHING`、upsert `RETURNING id`。

远程执行顺序固定为以下阶段，不能合并成一次不可回滚动作：

1. 先部署 PostgreSQL 数据库服务，只验证容器、健康检查、持久化目录、备份目录，不切换 app。
2. 将 SQLite 快照迁移进入 PostgreSQL，迁移过程不修改原始 SQLite，不触碰 `/lanshare/data`。
3. 用脚本直连 PostgreSQL 验证数据完整性、关键表行数、约束、序列、附件引用和关键接口依赖数据。
4. 只有前 3 阶段和 cutover gate 都通过后，才允许修改受控配置切换到新数据库并重启 app/worker。
5. 重启后执行 postflight、关键 API、关键页面和 worker 验证。
6. 对可控问题做最小修复，修复前必须先冻结风险写入并保留报告。
7. 若不可用或风险不可控，按恢复方案切回 SQLite，并保留 PostgreSQL 现场 dump 和差异报告。

当前结论不变：生产 cutover gate 仍为 `blocked`，不得修改远程生产 `docker.env` 启用 `DB_ENGINE=postgres`，不得声明生产迁移完成。

## 2026-06-06 后续增量执行记录

本轮继续围绕 `CUT-R005` 收口 app/worker 历史写入路径，重点减少 SQLite `lastrowid`、`last_insert_rowid()` 和裸 `cursor()` 对 PostgreSQL 切换的阻断。

新增完成：

1. Agent 写路径：`agent_tasks`、`agent_runtime_api_keys`、Agent 生成博客草稿、Agent 生成作业草稿均改为通过 `execute_insert_returning_id()` 获取新 ID。
2. 用户可见路由写路径：反馈提交、反馈附件、自定义表情、课程文件秒传关联、分块上传完成写入课程文件均改为统一主键返回 helper。
3. 消息中心普通写路径：通知、私信、私信附件创建收口到统一 helper；需要 `RETURNING *` 的 AI job 分支暂保留显式分支。
4. 材料链路：课程资料 folder/file、final material 完成记录、material AI import SQLite 回查、session material generation 生成 folder/file 均进一步收口。
5. 账户与管理辅助路径：教师邮箱配置、学生密码找回申请、教师账号创建、学生手动待办、智能考勤每日同步任务完成 PostgreSQL 主键或 `ON CONFLICT ... DO NOTHING RETURNING id` 语义。
6. 高频与协作路径：课堂行为事件、博客发帖/评论/附件、课堂互动活动/问题/求助、小组协作创建/文件/提交/互评、讨论附件、电子签名主记录创建均完成统一主键返回。

新增测试文件：

1. `tests/test_agent_postgres_writes.py`
2. `tests/test_router_postgres_writes.py`
3. `tests/test_account_todo_postgres_writes.py`
4. `tests/test_blog_postgres_writes.py`
5. `tests/test_behavior_postgres_writes.py`
6. `tests/test_classroom_interaction_postgres_writes.py`
7. `tests/test_collaboration_postgres_writes.py`
8. `tests/test_file_related_postgres_writes.py`

当前剩余 `lastrowid` 命中已缩小到统一 helper 自身、SQLite-only 分支或尚未完成的少数域：SQLite migration marker、教务同步 SQLite fallback、blog crawler SQLite fallback、chat handler、materials git、portfolio、signature access request、submission file alignment 等。上述剩余项未清零前，`CUT-R005` 仍不能消除。

当前结论仍然不变：不得修改远程生产 `docker.env` 启用 PostgreSQL；不得执行阶段 4 配置切换；远程最多继续阶段 1-3 的数据库层部署、迁移副本、脚本直连验证。

## 2026-06-06 复核后的当前结论

本轮追加完成并验证：

1. P01 本地回归集合通过：`Ran 218 tests ... OK`。
2. cutover gate 已重新生成，状态仍为 `blocked`，安全字段保持 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
3. `deployment\deploy_remote.ps1 -DryRun` 通过，确认没有上传文件，也没有触碰 Docker Compose。
4. 远程部署顺序已在 `RUNBOOK-remote-postgres-staged-cutover.md`、`T09-docker-compose-linux-deployment.md`、`T12-final-cutover-gates.md` 中明确为：先部署数据库、迁移数据、脚本直连验证、配置切换重启、验证、修复、恢复。

仍需阻断生产切换：

1. `CUT-R003`：3 条附件缺失引用未恢复或签收豁免。
2. `CUT-R005`：少量历史写入点、SQLite fallback、真实 PostgreSQL app/worker 全栈运行和远程 Compose app 验证仍未完成。

因此当前只能继续远程阶段 1-3 的数据库层工作；不得进入阶段 4，不得修改生产 `docker.env` 启用 PostgreSQL，不得声明迁移成功。

## 2026-06-06 继续推进记录

本轮继续围绕 `CUT-R005` 收口历史写入点和静态审计噪声：

1. 聊天记录、资料 Git 同步、作品集 upsert、电子签名申请、提交文件对齐恢复、Blog crawler run/item 创建已完成 PostgreSQL 主键返回或冲突安全写入适配。
2. private message AI job 和 session material generation task 创建路径改为统一 `execute_insert_returning_id()` 后再按 id 读回完整行，返回结构不变。
3. 教务课表、教学班名单、考试名单的 SQLite 分支不再依赖 `cursor.lastrowid`，改为按业务唯一键查回 id；PostgreSQL 分支继续使用 `RETURNING id`。
4. 新增 `tests/test_remaining_postgres_write_paths.py`，覆盖本轮新增的 6 个历史写入域。
5. P01 回归集合扩展为 `Ran 224 tests ... OK`。
6. `rg -n "lastrowid|last_insert_rowid\(" classroom_app -g "*.py"` 当前只剩统一 helper、迁移标记和 SQL helper 元数据，不再指向普通 app/service/route 写入路径。

重新生成 cutover gate 后结论仍为 `blocked`：

1. `CUT-R003` 仍存在：3 条附件缺失引用仍需恢复原文件或形成业务签收豁免。
2. `CUT-R005` 仍存在：真实生产 `docker.env` 未请求 PostgreSQL cutover，且仍需继续完成运行时 PRAGMA/SQLite fallback 审计、真实 PostgreSQL app/worker 全栈运行和远程 Compose app 验证。

当前仍只能继续远程阶段 1-3；不得进入阶段 4 配置切换。

## 2026-06-06 运行时元数据检查继续收口

本轮继续减少 `CUT-R005` 中运行时 SQLite 元数据依赖：

1. `discussion_attachment_service.ensure_discussion_attachment_schema()` 在 PostgreSQL 下改为查询 `information_schema.columns` 做只读列校验，不再执行 SQLite 建表、`ALTER TABLE` 或 `PRAGMA`。
2. `background_task_ledger_service` 的表/列存在性检查在 PostgreSQL 下改为 `information_schema.tables` / `information_schema.columns`。
3. `base_resource_modes_service`、`organization_management_service`、`organization_scope_service` 的 `_table_exists()` 在 PostgreSQL 下改为 `information_schema.tables`。
4. 新增 `tests/test_postgres_metadata_helpers.py`，并扩展 `tests/test_file_related_postgres_writes.py`、`tests/test_background_task_ledger.py`。
5. P01 回归集合扩展为 `Ran 229 tests ... OK`。
6. `deployment\deploy_remote.ps1 -DryRun` 通过，最新 deployable files 为 660，代码归档约 5.21 MB；未上传文件，未触碰 Docker Compose。

重新生成 cutover gate 后仍为 `blocked`。当前仍不得执行阶段 4 配置切换，仍不得修改生产 `docker.env` 启用 PostgreSQL。

## 2026-06-06 高并发烟测工具继续推进记录

本轮继续围绕 `CUT-R005` 和 T11 全栈验收前置工具链推进：

1. `tools/high_concurrency_smoke.py` 的 `_seed_test_data()` 已从裸 `cursor.lastrowid` 改为统一 `execute_insert_returning_id()`，覆盖教师、班级、课程、课堂和学生测试数据创建。
2. 新增 `tests/test_high_concurrency_smoke_postgres.py`，验证 PostgreSQL engine 下种子插入会生成 `RETURNING id`，不依赖 fake cursor 的 `lastrowid`。
3. 通过 `python -m unittest tests.test_high_concurrency_smoke_postgres tests.test_full_stack_load_profile`：`Ran 6 tests ... OK`。
4. 通过 `python -m py_compile tools\high_concurrency_smoke.py tests\test_high_concurrency_smoke_postgres.py`。
5. 当前 `tools/full_stack_load_test.py` 的 `lastrowid` 命中属于隔离 SQLite 副本压测工具，不代表生产 PostgreSQL app 验证完成，也不得用于证明 cutover ready。

当前结论仍然不变：可以继续减少 PostgreSQL app/worker 验证前置风险，但不得修改生产 `docker.env` 启用 PostgreSQL，不得执行阶段 4 配置切换。

本轮最新部署预检：

1. P01 回归集合已扩展为 `Ran 232 tests ... OK`。
2. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 662，归档约 5.22 MB；未上传文件，未触碰远程 Docker Compose。
3. cutover gate 仍为 `blocked`，安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。

## 2026-06-06 附件签收豁免流程增强记录

本轮围绕 `CUT-R003` 做流程增强，而不是绕过门禁：

1. `tools/db_attachment_restore_plan.py` 生成的 `missing-attachment-exception-template.json` 已包含每条缺失附件的课程、作业、学生、原文件名、hash、大小、历史路径、规范目标路径和可信候选数量。
2. 豁免清单验证更严格：必须有固定 scope、manifest version、批准人、批准时间、原因、业务确认，并显式确认 4 项风险。
3. `python -m unittest tests.test_db_attachment_restore_plan tests.test_db_cutover_gate` 通过：`Ran 8 tests ... OK`。
4. 当前附件恢复计划重新生成后仍为 `blocked`，因为还没有恢复文件，也没有有效签收豁免。

当前结论不变：`CUT-R003` 不能技术性假通过；必须恢复原始附件，或由业务负责人基于增强模板做明确签收。

## 2026-06-06 高并发烟测可重复运行继续推进记录

本轮继续围绕 `CUT-R005` 的真实 app/worker 验证前置条件推进：

1. `tools/high_concurrency_smoke.py` 的种子数据已支持唯一 `run_id` 后缀，教师邮箱、班级、课程、学生学号和学生邮箱不再固定。
2. 教师登录改为使用 seed 返回的动态账号，避免重复运行时登录旧账号或因唯一约束失败。
3. `tests/test_high_concurrency_smoke_postgres.py` 覆盖 PostgreSQL `RETURNING id`、唯一 run 后缀和动态教师登录。
4. 相关测试通过：`Ran 7 tests ... OK`。

当前结论不变：烟测工具更适合后续远程 PostgreSQL app 验证，但还不能替代真实全栈验证，也不能放行阶段 4。

最新部署预检：`deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 仍为 662，归档约 5.22 MB；未上传文件，未触碰远程 Docker Compose。
## 2026-06-06 数据库后端验收补强记录

本轮把用户提出的远程 7 步切换流程进一步落到自动化验收脚本中，重点解决“接口能访问但未证明已经连到目标数据库”的风险。

新增目标项：

1. `tools/deploy/health_backend_check.py` 必须能读取 `/api/internal/health` payload，校验 `database_backend.engine`、`configured` 和连接串打码状态。
2. `tools/db/run_dual_backend_tests.ps1` 必须支持 `-ExpectedDbEngine sqlite|postgres`，用于本地 Win11 临时 app 的双后端验证。
3. `tools/deploy/postflight.ps1` 必须支持 `-ExpectedDbEngine sqlite|postgres`，用于远程 Docker Compose 环境切换后验收和恢复验收。
4. 阶段 5 PostgreSQL 验收必须执行 `tools/deploy/postflight.ps1 -ExpectedDbEngine postgres -CheckPostgres`，生成 `health-database-backend.json` 与 `remote-database-backend.json`。
5. 阶段 7 SQLite 恢复必须执行 `tools/deploy/postflight.ps1 -ExpectedDbEngine sqlite`，证明服务实际回到 SQLite 权威库。
6. 任一 health 或容器内状态与期望后端不一致，都必须判定为失败，不允许继续推进。
7. 所有报告必须避免真实 PostgreSQL 密码、明文连接串、Cookie、Token 泄露。

当前结论不变：只允许继续阶段 1-3 的数据库层部署、迁移副本和脚本直连验证；`CUT-R003`、`CUT-R005` 未消除前，不得修改生产 `docker.env`，不得进入阶段 4，不得触碰或破坏线上 `/lanshare/data`。
## 2026-06-06 聊天运行时 PostgreSQL gate 补强记录

本轮继续推进 `CUT-R005` 中的 app/worker PostgreSQL 全栈适配风险收口：

1. `classroom_app/services/chat_handler.py` 在 PostgreSQL 模式下不再执行 SQLite 运行时 DDL；`ensure_chat_log_schema()` 改为只读校验 `chat_logs` 和 `chat_log_migrations` 必备列。
2. 聊天历史迁移标记在 PostgreSQL 下使用 `INSERT ... ON CONFLICT (class_offering_id) DO UPDATE`，不再执行 SQLite `INSERT OR REPLACE`。
3. `classroom_app/db/postgres_schema.py` 的 schema gate 从 `40/40` 扩展为 `43/43 required tables`，新增覆盖 `chat_logs`、`chat_log_migrations`、`discussion_attachments`。
4. 新增/扩展测试：`tests/test_remaining_postgres_write_paths.py` 覆盖聊天 schema PostgreSQL 只读校验和迁移标记 upsert；`tests/test_db_postgres_schema.py` 覆盖聊天运行时表纳入 required schema。
5. 已运行并通过：`python -m unittest tests.test_remaining_postgres_write_paths tests.test_db_postgres_schema`，输出 `PostgreSQL schema verified: 43/43 required tables`。

该改进减少了课堂聊天首次访问和历史日志迁移在 PostgreSQL app 全栈运行时的方言风险，但不消除 `CUT-R003`，也不代表可以进入阶段 4。
## 2026-06-06 本轮验证记录

本轮新增数据库后端 health 验收后，已完成以下验证：

1. `python -m unittest tests.test_deploy_check_tools tests.test_db_cutover_gate`：`Ran 13 tests ... OK`。
2. P01 回归集合：`Ran 235 tests ... OK`。
3. `tools/db/run_dual_backend_tests.ps1 -SkipApiSmoke`：`Ran 37 tests ... OK`，生成 `.codex-temp/pg-migration-lab/reports/dual-backend-tests-summary.json`。
4. `python tools/db_cutover_gate.py ...`：重新生成 `.codex-temp/db-cutover-gate-current/cutover-gate.json` 和 `.md`，状态仍为 `blocked`。
5. `deployment/deploy_remote.ps1 -DryRun`：deployable files 为 `663`，归档约 `5.22 MB`，明确未上传文件、未触碰 Docker Compose。
6. `git diff --check`：通过，仅有既有 LF/CRLF 提示。
7. 敏感配置扫描：仅命中占位值、协议校验和测试用假连接串；未发现真实 PostgreSQL 密码或生产连接串进入代码、文档、报告或日志。

因此当前可继续推进本地与远程阶段 1-3 的数据库层验证，但生产阶段 4 仍被门禁禁止。
## 2026-06-06 聊天 gate 后完整复核记录

聊天运行时 schema gate 增强后，已重新执行完整复核：

1. P01 回归集合：`Ran 238 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 43/43 required tables`。
2. `python tools/db_cutover_gate.py --json-output .codex-temp/db-cutover-gate-current/cutover-gate.json --markdown-output .codex-temp/db-cutover-gate-current/cutover-gate.md` 已重新生成门禁，结果仍为 `status=blocked`。
3. 当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`；安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
4. `deployment/deploy_remote.ps1 -DryRun` 通过，deployable files 为 `663`，归档约 `5.22 MB`，明确未上传文件、未触碰 Docker Compose。
5. `git diff --check` 通过，仅有 LF/CRLF 提示。
6. 敏感配置扫描仍只命中占位值、协议校验和测试用假连接串，未发现真实 PostgreSQL 密码或生产连接串进入代码、文档、报告或日志。

结论不变：本轮继续推进了 `CUT-R005` 的代码侧风险收口，但生产阶段 4 仍不得执行。
## 2026-06-06 邮件 worker schema gate 补强记录

本轮继续收口 `CUT-R005` 中的 health/worker 全栈运行风险：

1. `classroom_app/db/postgres_schema.py` 的 PostgreSQL required schema 从 `43/43` 扩展为 `45/45 required tables`。
2. 新增覆盖 `teacher_email_configs` 和 `email_worker_heartbeats`，确保切换后教师邮箱配置、mailer heartbeat 和 health snapshot 的基础表存在。
3. 扩展 `email_outbox` 必备列，新增校验 `attempt_count`、`sent_at`、`last_error`，避免 mailer 重试、成功/失败写回和 health 队列统计在 PostgreSQL 下缺列。
4. `tests/test_db_postgres_schema.py` 新增邮件 worker runtime schema 覆盖测试。
5. 已运行并通过：`python -m unittest tests.test_db_postgres_schema tests.test_email_notification_queue_claim tests.test_api_contract_schemas`，输出 `PostgreSQL schema verified: 45/45 required tables`，共 `Ran 20 tests ... OK`。

该改进不修改线上数据，也不改变最终结论：`CUT-R003` 和 `CUT-R005` 未完全消除前仍不得执行阶段 4。
## 2026-06-06 邮件 gate 后完整复核记录

邮件 worker schema gate 增强后，已重新执行完整复核：

1. P01 回归集合：`Ran 239 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 45/45 required tables`。
2. `python tools/db_cutover_gate.py --json-output .codex-temp/db-cutover-gate-current/cutover-gate.json --markdown-output .codex-temp/db-cutover-gate-current/cutover-gate.md` 已重新生成门禁，结果仍为 `status=blocked`。
3. 当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`；安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
4. `deployment/deploy_remote.ps1 -DryRun` 通过，deployable files 为 `663`，归档约 `5.23 MB`，明确未上传文件、未触碰 Docker Compose。
5. `git diff --check` 通过，仅有 LF/CRLF 提示。
6. 敏感配置扫描仍只命中占位值、协议校验和测试用假连接串，未发现真实 PostgreSQL 密码或生产连接串进入代码、文档、报告或日志。

结论不变：本轮继续推进了 `CUT-R005` 的 app/worker 运行前置条件，但生产阶段 4 仍不得执行。
## 2026-06-06 AI 聊天 runtime schema gate 补强记录

本轮继续收口 `CUT-R005` 中的 AI 聊天页面运行时风险：

1. `classroom_app/db/postgres_schema.py` 的 PostgreSQL required schema 从 `45/45` 扩展为 `47/47 required tables`。
2. 新增覆盖 `ai_chat_messages` 和 `ai_psychology_profiles`，与既有 `ai_chat_sessions` 形成完整 AI 聊天链路校验。
3. `ai_chat_messages` 必备列包含 `thinking_content`、`final_answer`、`attachments_json`，覆盖流式思考内容、最终回答恢复和附件上下文。
4. `ai_psychology_profiles` 必备列包含 `hidden_premise_prompt`、`support_strategy`、`raw_payload`，覆盖内部学习支持快照读写。
5. `tests/test_db_postgres_schema.py` 新增 AI chat runtime schema 覆盖测试。
6. 已运行并通过：`python -m unittest tests.test_db_postgres_schema tests.test_api_contract_schemas`，输出 `PostgreSQL schema verified: 47/47 required tables`，共 `Ran 14 tests ... OK`。

该改进不修改线上数据，也不代表最终切换已放行；真实 PostgreSQL app/worker 全栈验证仍必须等待 gate 允许后执行。
## 2026-06-06 AI gate 后完整复核记录

AI 聊天 runtime schema gate 补强后，已重新执行 P01 当前回归与部署前安全复核：

1. P01 回归集合通过：`Ran 240 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 47/47 required tables`。
2. `python tools\db_cutover_gate.py --json-output .codex-temp\db-cutover-gate-current\cutover-gate.json --markdown-output .codex-temp\db-cutover-gate-current\cutover-gate.md` 已重新生成门禁报告，结果仍为 `status=blocked`。
3. 当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`；安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
4. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 `663`，归档约 `5.23 MB`，明确未上传文件、未触碰 Docker Compose。
5. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误。
6. 敏感配置扫描仅命中占位值、协议校验和测试用假连接串，未发现真实 PostgreSQL 密码或生产连接串。

结论不变：当前只允许继续阶段 1-3 的数据库层部署、迁移副本和脚本直连验证；`CUT-R003`、`CUT-R005` 未消除前，不得修改生产 `docker.env`，不得进入阶段 4，不得触碰或破坏线上 `/lanshare/data`。
## 2026-06-06 worker/runtime schema gate 继续收口记录

本轮继续围绕 `CUT-R005` 收口 app/worker PostgreSQL 全栈运行前置风险：

1. `classroom_app/db/postgres_schema.py` 的 PostgreSQL required schema 从 `47/47` 扩展为 `57/57 required tables`。
2. wrong-summary 链路新增覆盖 `assignment_wrong_answer_ai_cache`、`exam_paper_difficulty_ai_cache`，并补齐 `assignment_wrong_summary_jobs` 的实际运行列。
3. 行为追踪链路新增覆盖 `classroom_behavior_states`、`classroom_behavior_profiles`，并补齐 `classroom_behavior_events` 的实际写入列。
4. 智慧教室/智能考勤链路新增覆盖 `teacher_smart_classroom_credentials`、`smart_classroom_schedule_items`、`smart_classroom_checkin_sessions`、`smart_classroom_checkin_students`、`smart_attendance_daily_tasks`、`smart_attendance_student_advice`。
5. `tests/test_db_postgres_schema.py` 新增 wrong-summary、behavior、smart attendance runtime schema 覆盖测试。
6. `wrong_question_summary_service` PostgreSQL 只读 schema 校验同步补齐主键、错误信息和时间戳等运行字段。

本轮验证结果：

1. P01 回归集合通过：`Ran 243 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 57/57 required tables`。
2. cutover gate 重新生成后仍为 `status=blocked`。
3. 当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`；安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
4. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 `663`，归档约 `5.23 MB`，明确未上传文件、未触碰 Docker Compose。
5. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误。
6. 敏感配置扫描仅命中占位值、协议校验和测试用假连接串，未发现真实 PostgreSQL 密码或生产连接串。

结论不变：本轮继续降低了 `CUT-R005` 中 app/worker 启动后缺表缺列的风险，但最终 gate 仍未放行。`CUT-R003`、`CUT-R005` 未消除前，不得修改生产 `docker.env`，不得执行阶段 4，不得触碰或破坏线上 `/lanshare/data`。

## 2026-06-06 account/integration 与课堂协作 gate 继续收口

本轮继续围绕 `CUT-R005` 扩展 PostgreSQL 启动前只读 schema gate，重点覆盖账号辅助表、教师集成凭据表、课堂协作和课堂实时互动运行时表。

新增完成：

1. PostgreSQL required schema 从 `57/57` 先扩展到 `65/65 required tables`，新增覆盖 `student_login_audit_logs`、`student_password_reset_requests`、`classroom_todos`、`app_feedback`、`app_feedback_attachments`、`teacher_git_credentials`、`teacher_academic_system_credentials`、`teacher_academic_teaching_places`。
2. PostgreSQL required schema 继续扩展到 `75/75 required tables`，新增覆盖小组协作链路的 `study_groups`、`study_group_members`、`study_group_files`、`group_submissions`、`peer_reviews`。
3. 同步新增覆盖课堂实时互动链路的 `classroom_live_activities`、`classroom_live_options`、`classroom_live_responses`、`classroom_live_questions`、`classroom_live_help_signals`。
4. `tests/test_db_postgres_schema.py` 新增 account/support/integration 与 classroom collaboration/live runtime schema 覆盖测试，确保缺表缺列会在 `DB_ENGINE=postgres` 启动前 fail-fast。
5. 课堂协作与实时互动均属于课堂内高频写入路径；本轮只补强 schema gate 和单元测试，不声称已完成真实 PostgreSQL app/worker 全栈压测。

本轮验证结果：

1. 聚焦 account/integration 集合通过：`python -m unittest tests.test_db_postgres_schema tests.test_account_todo_postgres_writes tests.test_router_postgres_writes tests.test_academic_sync_postgres_writes tests.test_api_contract_schemas`，schema gate 输出 `PostgreSQL schema verified: 65/65 required tables`，`Ran 33 tests ... OK`。
2. 聚焦 classroom collaboration/live 集合通过：`python -m unittest tests.test_db_postgres_schema tests.test_collaboration_postgres_writes tests.test_classroom_interaction_postgres_writes tests.test_api_contract_schemas`，schema gate 输出 `PostgreSQL schema verified: 75/75 required tables`，`Ran 26 tests ... OK`。
3. 本地 P01 回归集合通过：`Ran 225 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 75/75 required tables`。
4. cutover gate 重新生成后仍为 `status=blocked`，当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`。
5. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
6. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 `663`，归档约 `5.24 MB`，明确未上传文件、未触碰 Docker Compose。
7. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误。
8. 严格敏感配置复核后，命中项均归类为协议校验、脱敏 health fixture 或单元测试假连接串，未发现真实 PostgreSQL 密码或生产连接串进入代码、文档或报告。

结论不变：当前只允许继续阶段 1-3 的数据库层部署、迁移副本和脚本直连验证；`CUT-R003`、`CUT-R005` 未消除前，不得修改生产 `docker.env`，不得启用 `DB_ENGINE=postgres`，不得执行阶段 4 配置切换，不得触碰或破坏线上 `/lanshare/data`。

## 2026-06-06 静态 schema 表全覆盖 gate

本轮继续收口 `CUT-R005`，把 `classroom_app/db/schema_*.py` 中静态声明的 SQLite 业务表全部纳入 PostgreSQL 启动前 required schema gate。

新增完成：

1. PostgreSQL required schema 从 `75/75` 先扩展到 `94/94 required tables`，新增覆盖 Agent runtime、博客/资讯 crawler、博客社交、私信控制、私信审计、自定义表情和表情使用统计。
2. PostgreSQL required schema 继续扩展到 `113/113 required tables`，新增覆盖系统设置、教师 onboarding、学生共享备注、学期日历日表、课程文件、分块上传、提交草稿附件、错题复盘备注、UI 文案快照、讨论氛围快照、学习进度、学习阶段状态、学习路径项、作品集、成长事件、电子签名、签名使用日志和签名访问申请。
3. `tests/test_db_postgres_schema.py` 新增自动库存测试：从 `schema_*.py` 抽取 `CREATE TABLE IF NOT EXISTS` 表名，要求全部出现在 `REQUIRED_POSTGRES_TABLES` 中；以后新增 SQLite schema 表但忘记纳入 PostgreSQL gate 会直接失败。
4. 本轮没有写入或迁移任何业务数据，没有修改生产 `docker.env`，没有触碰线上 `/lanshare/data`。

验证结果：

1. 聚焦 Agent/博客/社交集合通过：`Ran 51 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 94/94 required tables`。
2. 聚焦剩余学习/上传/签名集合通过：`Ran 41 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 113/113 required tables`。
3. 静态 schema 差集复核：`sqlite_schema_tables=113`、`required_postgres_tables=113`、`missing_from_required_count=0`。
4. 本地 P01 回归集合通过：`Ran 231 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 113/113 required tables`。
5. cutover gate 重新生成后仍为 `status=blocked`，当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`。
6. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
7. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 `663`，归档约 `5.24 MB`，明确未上传文件、未触碰 Docker Compose。
8. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误；严格敏感扫描剩余命中仅为 `postgres.py` 的协议校验字面量，未发现真实 PostgreSQL 密码或生产连接串。

结论：schema 启动前门禁已经覆盖当前静态 SQLite schema 文件中的全部业务表，这显著降低了 PostgreSQL app/worker 启动后才发现缺表缺列的风险。但这仍不等于生产 cutover ready；`CUT-R003` 和 `CUT-R005` 未消除前，阶段 4 配置切换仍禁止。

## 2026-06-06 组织作用域与登录态支撑表纳入 gate

本轮继续把静态 schema 文件之外、但由迁移/修复层创建且直接影响权限边界和登录态的 5 张表纳入 PostgreSQL required schema gate。

新增覆盖：

1. `user_sessions`：登录态、会话清理和会话修复依赖。
2. `organization_schools`、`organization_colleges`、`organization_departments`：系统组织目录和学校/学院/专业作用域依赖。
3. `teacher_organization_memberships`：教师多组织身份、主组织、启停状态和权限作用域依赖。

验证结果：

1. 聚焦组织/会话集合通过：`Ran 46 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 118/118 required tables`。
2. 本地 P01 回归集合通过：`Ran 246 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 118/118 required tables`。
3. cutover gate 重新生成后仍为 `status=blocked`，当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`。
4. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
5. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 `663`，归档约 `5.24 MB`，明确未上传文件、未触碰 Docker Compose。
6. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误；严格敏感扫描剩余命中仅为 `postgres.py` 的协议校验字面量，未发现真实 PostgreSQL 密码或生产连接串。

结论：当前 required schema gate 已覆盖历史库存中的 118 张关键业务/运行支撑表。该项进一步降低 `CUT-R005`，但不解除 `CUT-R003`，也不替代真实 PostgreSQL app/worker 全栈运行和远程 Compose app 验证。

## 2026-06-06 运行时 SQL 方言门禁继续收口

本轮继续深挖 PostgreSQL 切换风险中的 SQL 方言问题，重点检查 service/router 层仍然出现的 SQLite-only SQL 文本。

新增完成：

1. 审计 `INSERT OR IGNORE` 残留命中点，确认课程材料分配、博客 crawler 配置、邮件 outbox、教务课程同步等路径均已有 PostgreSQL 分支。
2. 审计运行时 DDL/元数据残留命中点，确认聊天、附件、私信、错题总结、教务任课考试等路径在 PostgreSQL 下只做 `information_schema` 校验或 fail-fast。
3. `wrong_question_summary_service.ensure_wrong_summary_cache_tables()` 增加非支持数据库引擎显式拒绝，避免异常配置误入 SQLite DDL fallback。
4. 新增 `tests/test_db_sql_dialect_guard.py`，把运行时 SQLite-only SQL 命中点纳入 allowlist 门禁；未来新增命中必须先说明用途、完成引擎分支和 PostgreSQL 测试。

验证结果：

1. 聚焦方言门禁集合通过：`Ran 42 tests ... OK`。
2. 本轮只修改代码和本地测试，不修改生产 `docker.env`，不连接生产写库，不触碰线上 `/lanshare/data`。

当前远程部署阶段边界：

1. 阶段 1：可以先部署 PostgreSQL 数据库服务，但 app 仍保持 SQLite。
2. 阶段 2：只能从 SQLite 快照迁移数据到 PostgreSQL，不破坏原始数据。
3. 阶段 3：只能脚本直连验证数据完整性和关键接口依赖数据。
4. 阶段 4：切换配置、重启 app 必须等待 `CUT-R003` 和 `CUT-R005` 消除，且必须具备恢复方案。

结论：`CUT-R005` 的代码侧风险继续降低，但 gate 仍未放行；不得执行生产 PostgreSQL 配置切换。

本轮完整复核：

1. `python -m py_compile classroom_app\services\wrong_question_summary_service.py tests\test_db_sql_dialect_guard.py tests\test_wrong_question_summary_service.py` 通过。
2. P01 回归集合通过：`Ran 247 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 118/118 required tables`。
3. cutover gate 已重新生成，结果仍为 `status=blocked`，blocker 仍为 `CUT-R003` 和 `CUT-R005`。
4. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
5. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 `664`，归档约 `5.25 MB`，明确未上传文件、未触碰 Docker Compose。
6. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误。
7. 敏感信息扫描命中均为配置读取、脱敏函数、占位值或测试用假连接串，未发现真实 PostgreSQL 密码或生产连接串进入代码、文档或报告。

## 2026-06-06 gate 阶段拆分与敏感报告修复

本轮继续推进远程阶段 1-3 的可控验收：

1. `tools/deploy/postgres_preflight.py` 扩展敏感环境变量脱敏规则，新增覆盖 `API_KEY`、`PASSWD`、`PWD`、`USERNAME`、`CREDENTIAL`、`ACCESS_KEY` 等字段。
2. 已重新生成 `.codex-temp/pg-migration-lab/reports/postgres-preflight.json`，报告中 API key、教师密码和教务账号密码均为 `***`。
3. `tools/db_cutover_gate.py` 新增 `--phase pre-cutover|final-cutover`。
4. 默认仍为 `final-cutover`，保持最终切换判断不被放宽。
5. pre-cutover 阶段把 `docker.env` 仍为 SQLite 记录为 `CUT-W004`，因为这正是阶段 4 前的安全状态；若提前切到 PostgreSQL，则触发 `CUT-R011`。

当前 gate 结果：

1. pre-cutover gate：`status=blocked`，blocker 仅剩 `CUT-R003`。
2. final-cutover gate：`status=blocked`，blocker 为 `CUT-R003` 和 `CUT-R005`。
3. 安全字段均保持 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。

当前结论：远程阶段 1-3 的判断已经去掉配置切换循环依赖；下一步必须解决缺失附件，或取得有效业务签收豁免，才能让 pre-cutover gate 进入 `ready`。

本轮复核结果：

1. `python -m py_compile tools\deploy\postgres_preflight.py tools\db_cutover_gate.py tests\test_deploy_check_tools.py tests\test_db_cutover_gate.py` 通过。
2. P01 回归集合通过：`Ran 250 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 118/118 required tables`。
3. pre-cutover gate 重新生成：`status=blocked`，blocker 为 `CUT-R003`，warning 为 `CUT-W002`、`CUT-W004`。
4. final-cutover gate 重新生成：`status=blocked`，blocker 为 `CUT-R003`、`CUT-R005`。
5. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 `664`，归档约 `5.25 MB`，明确未上传文件、未触碰 Docker Compose。
6. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误。
7. 真实密码、token、API key、连接串反扫通过：`real_password_token_key_scan_hits=0`。
8. 本轮远程只读 hash 搜索仍未找到缺失附件原件；未复制、未删除、未修改远程 `/lanshare/data`。

## 2026-06-06 生产 PostgreSQL 切换完成记录

本轮按照用户确认的 7 步顺序完成远程生产 PostgreSQL 部署与迁移。执行过程中始终保留 SQLite 原始数据和可恢复路径；真实 PostgreSQL 密码只写入远程未提交的 `.env`/`docker.env`，不写入仓库、目标文档、报告或日志。

实际执行结果：

1. 阶段 1 数据库部署完成：远程 PostgreSQL 服务已通过 Docker Compose overlay 启动，`/lanshare/data/postgres` 为持久化数据目录，`/lanshare/data/postgres-backups` 为 PostgreSQL 备份目录；报告见 `.codex-temp/pg-remote-stage1/postgres-service-verify.json`。
2. 阶段 2 数据迁移完成：从 SQLite 快照导入 PostgreSQL，源库 `quick_check=ok`，外键违规 `0`，SQLite/PostgreSQL 表数均为 `118`，迁移时点总行数均为 `346662`，报告见 `.codex-temp/pg-remote-stage2/postgres-import.json`。
3. 阶段 3 直连验证完成：关键表抽样、附件引用、占位附件签收、app 一次性容器 PostgreSQL schema 验证均通过；报告见 `.codex-temp/pg-remote-stage3/postgres-direct-verify.json`。
4. 阶段 4 第一次切换发现 PostgreSQL dict-row 兼容问题并已自动回滚到 SQLite，未丢失数据；失败报告见 `.codex-temp/pg-remote-cutover/cutover-report.json`。
5. 阶段 6 最小修复完成：修复 `email_notification_service.py`、`background_task_ledger_service.py`、`submission_file_alignment.py` 的 PostgreSQL row/scalar 兼容问题，并修复 `tools/deploy/postflight.ps1` 的 Win11/SSH 内联 Python 引号问题。
6. 阶段 4 重试切换成功：远程 app/worker 已切换到 PostgreSQL，`database_backend_state()` 返回 `engine=postgres`、`configured=true`；成功报告见 `.codex-temp/pg-remote-cutover/cutover-report-retry1.json`。
7. 阶段 5 postflight 通过：最新报告目录为 `.codex-temp/deploy-checks/postflight-20260606-112128`，覆盖登录页、manifest、后台任务接口、远端容器状态、HTTP health 数据库后端和容器内数据库后端校验。

当前权威数据库：PostgreSQL。

必须继续保留的恢复点：

1. 切换前 SQLite 备份：`/tmp/lanshare-cutover-backups/20260606T025934Z/classroom-before-pg-cutover-20260606T025934Z.db`，备份快照 `quick_check=ok`、外键违规 `0`、表数 `118`、行数 `346662`。
2. PostgreSQL 切换前 dump：`/lanshare/data/postgres-backups/pre-final-cutover-20260606T025934Z.dump`。
3. 远程配置备份：`/lanshare/.env.pg-stage1-backup-20260606T023840Z` 与 `/lanshare/docker.env.pg-stage1-backup-20260606T023840Z`。
4. 不得执行 `docker compose --remove-orphans` 来“清理” `lanshare-postgres-1`；部署脚本已在远程 `DB_ENGINE=postgres` 且 overlay 存在时自动带上 `docker-compose.postgres.yml`，但人工操作仍必须显式保留 PostgreSQL 服务。

缺失附件处理结论：

1. 原始 3 份历史提交附件仍未找回原件。
2. 按用户授权，已创建明确标记的审计占位文件，补齐文件路径可解析性，未删除、未覆盖、未修改数据库记录。
3. 占位文件不是原始作业证据，不能用于还原学生原始提交内容或重新评分；审计记录见 `.codex-temp/missing-attachment-fill-current/accepted-missing-attachment-exception.json` 与 `.codex-temp/missing-attachment-fill-current/remote-placeholder-fill-manifest.json`。

最终本地回归：

1. `python -m unittest ...` P01 完整集合通过：`Ran 252 tests ... OK`。
2. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误。
3. postflight 数据库后端强校验通过，远程 app 内部状态确认为 PostgreSQL。

剩余观察项：

1. `/api/internal/background-tasks` HTTP 状态为 `200`，各任务状态项为可读；其中 `ok=false` 来自历史失败计数，不是本次 PostgreSQL 切换后的连接错误。是否清理或重放这些历史失败任务，应作为独立业务运维事项处理。
2. PostgreSQL 已成为当前权威库；从现在起不得再假设 SQLite 是生产写入源。任何恢复到 SQLite 的动作都必须先冻结写入、导出 PostgreSQL 差异并形成书面恢复记录，不能直接丢弃 PostgreSQL 切换后的新数据。
