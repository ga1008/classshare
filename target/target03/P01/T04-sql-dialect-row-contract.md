# T04 - SQL 方言与 Row 契约

## 目标

将 SQLite 专用 SQL 和 row 访问行为抽象为清晰契约，为 PostgreSQL adapter 分阶段落地做准备。

## 必须处理的 SQL 差异

1. 占位符：SQLite `?`，PostgreSQL `$1`、`$2`。
2. 插入返回主键：SQLite `cursor.lastrowid`，PostgreSQL `RETURNING id`。
3. 忽略冲突：SQLite `INSERT OR IGNORE`，PostgreSQL `ON CONFLICT DO NOTHING`。
4. UPSERT：两端都支持 `ON CONFLICT`，但约束目标必须明确。
5. 时间函数：SQLite `CURRENT_TIMESTAMP`，PostgreSQL `now()`。
6. 队列领取：SQLite 普通事务，PostgreSQL 应使用 `FOR UPDATE SKIP LOCKED`。
7. JSON：SQLite 多为 text，PostgreSQL 可使用 `jsonb`，但必须先确认字段语义。

## 执行步骤

1. 建立 `classroom_app/db/sql.py`，提供 placeholder、identifier quote、insert returning、upsert、job claim SQL helper。
2. 建立 row 访问适配，避免业务代码依赖 sqlite3.Row 的所有细节。
3. 对热点写入路径优先替换 SQL helper。
4. 对历史 SQL 做清单化迁移，不一次性全量重写。
5. 每个替换都必须保留 SQLite 回归测试。

## 验收条件

- [ ] SQL helper 单元测试覆盖 SQLite 和 PostgreSQL 两种输出。
- [ ] 新代码不得新增裸写 `INSERT OR IGNORE`、`lastrowid` 等未封装用法。
- [ ] PostgreSQL 返回主键行为有统一策略。
- [ ] row 访问在 dict、sqlite3.Row、未来 PostgreSQL row 上行为一致。

## 当前执行记录

已完成：

- 新增 `classroom_app/db/sql.py`。
- 新增 `classroom_app/db/row.py`。
- 新增 `classroom_app/db/postgres.py` 的 `qmark_to_psycopg()`，作为过渡期基础占位符转换层。
- 新增 `tests/test_db_sql_helpers.py`。
- 当前 helper 已覆盖 placeholder、insert returning、insert ignore、upsert、PostgreSQL queue claim、singleton status index，以及基础 qmark 转换测试。
- `email_outbox` worker 领取路径已拆出可注入连接的执行函数，PostgreSQL 分支通过 qmark facade 执行 `FOR UPDATE SKIP LOCKED` 与 `RETURNING *`，SQLite 分支保留原有条件更新回归。

当前状态：T04 的基础工具已建立，邮件 outbox 领取路径已完成一个真实业务适配样本；但业务代码中仍有大量历史 SQL 需要按域分批迁移，不允许声称应用已 PostgreSQL 兼容。

## 2026-06-06 后续 SQL 方言收口记录

本轮进一步收口 app/service 层 SQLite 主键返回依赖：

1. `chat_handler.py` 的课堂聊天记录写入改为 `execute_insert_returning_id()`。
2. `materials_git_service.py` 的 Git 工作区同步 folder/file 创建改为 `execute_insert_returning_id()`。
3. `portfolio_service.py` 的作品集 upsert 去掉 `cursor.lastrowid` 兜底，改为按业务唯一键查回持久行。
4. `signature_service.py` 的签名申请创建改为 `execute_insert_returning_id()`，重复申请仍映射为 409。
5. `submission_file_alignment.py` 的孤儿提交恢复创建改为 `execute_insert_returning_id()`。
6. `blog_news_crawler_service.py` 的 run 创建改为统一 helper，candidate item 在 PostgreSQL 下使用 `ON CONFLICT DO NOTHING RETURNING *`，避免唯一冲突破坏事务。
7. `message_center_service.py` 和 `session_material_generation_service.py` 创建任务时统一 `RETURNING id` 后按 id 读回完整行。
8. 教务同步 SQLite 分支不再依赖 `lastrowid`，改为业务唯一键查回 id。

验收证据：

- 新增并通过 `tests/test_remaining_postgres_write_paths.py`。
- P01 回归集合：`Ran 224 tests ... OK`。
- `rg -n "lastrowid|last_insert_rowid\(" classroom_app -g "*.py"` 仅剩 `classroom_app/db/connection.py`、`classroom_app/db/migrations.py`、`classroom_app/db/sql.py`，不再出现在普通业务 service/router 写入路径。

仍需继续：

- 运行时 `PRAGMA`/SQLite fallback 的 PostgreSQL 审计。
- 真实 PostgreSQL app/worker 全栈运行验证。
- 远程 Compose app 验证和维护窗口 postflight。

## 2026-06-06 运行时元数据 SQL 收口记录

本轮继续收口运行时 SQLite 元数据 SQL：

1. `discussion_attachment_service.py` 在 PostgreSQL 下使用 `information_schema.columns` 校验 `discussion_attachments` 必备列。
2. `background_task_ledger_service.py` 在 PostgreSQL 下使用 `information_schema.tables` 和 `information_schema.columns`。
3. `base_resource_modes_service.py`、`organization_management_service.py`、`organization_scope_service.py` 在 PostgreSQL 下使用 `information_schema.tables`。
4. 新增 `tests/test_postgres_metadata_helpers.py`，并扩展 discussion attachment 和 background task ledger 测试。

验收证据：

- `python -m unittest tests.test_postgres_metadata_helpers tests.test_base_resource_modes_service tests.test_background_task_ledger tests.test_file_related_postgres_writes` 通过。
- P01 回归集合：`Ran 229 tests ... OK`。

仍需继续审计的残留：

- 明确 SQLite 分支中的 `INSERT OR IGNORE` 文本仍存在，但 PostgreSQL 分支已分离或需要继续逐域确认。
- `message_center_service.py`、`wrong_question_summary_service.py` 等 SQLite schema fallback 中仍保留 `PRAGMA` 文本，需继续确认 PostgreSQL 调用路径不会进入 SQLite fallback。

## 2026-06-06 运行时 SQLite-only SQL 门禁补强记录

本轮继续深挖 `classroom_app/services` 与 `classroom_app/routers` 中仍然存在的 SQLite-only SQL 文本，结论如下：

1. 已审计 `INSERT OR IGNORE` 命中点：`academic_course_sync_service.py`、`materials_service.py`、`final_material_helpers.py`、`blog_news_crawler_service.py`、`email_notification_service.py` 均已有 PostgreSQL 分支，PostgreSQL 下使用 `ON CONFLICT`/`RETURNING` 路径。
2. 已审计运行时 DDL/元数据命中点：`chat_handler.py`、`discussion_attachment_service.py`、`message_center_service.py`、`wrong_question_summary_service.py`、`academic_course_exam_sync_service.py` 等在 PostgreSQL 下只做 `information_schema` 校验或 fail-fast，不执行 SQLite 兼容 DDL。
3. `wrong_question_summary_service.ensure_wrong_summary_cache_tables()` 已补充非 sqlite/postgres 引擎的显式拒绝，避免未来异常配置落入 SQLite DDL fallback。
4. 新增 `tests/test_db_sql_dialect_guard.py`，将当前允许存在的运行时 SQLite-only SQL 命中点做成受控 allowlist。以后如果 service/router 新增 `INSERT OR IGNORE`、`INSERT OR REPLACE`、`PRAGMA`、`sqlite_master`、运行时 `CREATE TABLE IF NOT EXISTS` 或 `ALTER TABLE`，必须先完成引擎分支审计、补 PostgreSQL 测试，再更新 allowlist。

验收证据：

- `python -m unittest tests.test_db_sql_dialect_guard tests.test_wrong_question_summary_service tests.test_remaining_postgres_write_paths tests.test_academic_sync_postgres_writes tests.test_file_related_postgres_writes tests.test_materials_postgres_writes tests.test_postgres_metadata_helpers` 通过，`Ran 42 tests ... OK`。

当前结论：

- 运行时 SQLite-only SQL 已进入可跟踪、可复核状态，但这仍不是生产切换许可。真实 PostgreSQL app/worker 全栈验证、远程 Compose app 验证、观察窗口 postflight 仍必须在阶段 4 前完成。
