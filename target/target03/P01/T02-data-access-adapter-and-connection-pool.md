# T02 - 数据访问适配层与连接池

## 目标

建立 SQLite/PostgreSQL 双后端的受控连接入口，确保 PostgreSQL 未完全适配前不会自动回退到 SQLite，也不会在配置错误时静默写错数据库。

## 设计原则

1. `DB_ENGINE=sqlite` 时继续保持现有 SQLite 行为。
2. `DB_ENGINE=postgres` 时必须要求 `DATABASE_URL`。
3. PostgreSQL adapter 未完成前必须 fail-closed，不允许生产误切。
4. 连接池、事务、超时、锁等待和 row 访问行为必须显式定义。
5. 所有 worker 和 app 必须使用同一数据库配置。

## 必须实现的配置

- `DB_ENGINE`
- `DATABASE_URL`
- `POSTGRES_POOL_MIN`
- `POSTGRES_POOL_MAX`
- `POSTGRES_STATEMENT_TIMEOUT_MS`
- `POSTGRES_LOCK_TIMEOUT_MS`
- `POSTGRES_IDLE_IN_TRANSACTION_TIMEOUT_MS`
- `POSTGRES_BACKEND_READY`

真实密码只能写入未提交的 `.env` 或远程 `docker.env`，不得写入仓库。

## 执行步骤

1. 保留现有 SQLite 连接入口。
2. 增加 PostgreSQL 配置读取和脱敏展示。
3. 增加 `database_backend_state()`，供 health/metrics/postflight 使用。
4. 在 PostgreSQL adapter 未激活时抛出明确配置错误。
5. 后续逐步引入 PostgreSQL 连接池和事务上下文。
6. 所有新 SQL helper 必须支持显式 engine 参数。

## 验收条件

- [ ] SQLite 模式下现有测试通过。
- [ ] `DB_ENGINE=postgres` 且无 `DATABASE_URL` 时拒绝启动。
- [ ] `DB_ENGINE=postgres` 但 `POSTGRES_BACKEND_READY=false` 时拒绝启动。
- [ ] health/metrics 能显示当前数据库后端且不泄露密码。
- [ ] app、mailer、blog-crawler、agent-worker 的数据库配置一致。

## 当前执行记录

已完成：

- `classroom_app/config.py` 增加 PostgreSQL 配置项。
- `classroom_app/db/connection.py` 增加 `SUPPORTED_DB_ENGINES`、`database_backend_state()` 和 PostgreSQL fail-closed 分支。
- 新增 `classroom_app/db/postgres.py`，提供 psycopg 驱动加载、PostgreSQL URL 校验、基础连接包装、`?` 到 psycopg `%s` 的占位符转换、session timeout 设置和连接错误脱敏。
- `requirements.lock.txt` 增加 `psycopg[binary]==3.3.4` 与 `psycopg-binary==3.3.4`，为 Docker/远程 PostgreSQL runtime 准备驱动。
- SQLite 仍启用 WAL、busy timeout、foreign keys、cache 等参数。
- `DB_ENGINE=postgres` 仍必须同时满足 `DATABASE_URL` 和 `POSTGRES_BACKEND_READY=true` 才会调用 adapter，避免生产误切或静默回退。
- 新增 `tests/test_db_postgres_adapter.py`，覆盖驱动注入、占位符转换、连接上下文提交/回滚、session 设置和连接串脱敏。

当前状态：T02 的连接基础已经从纯占位推进为可测试 adapter，但应用 schema 初始化、历史 SQL 方言、业务写入路径、worker 队列领取仍未完成 PostgreSQL 全栈适配；不得修改生产 `docker.env` 启用 `DB_ENGINE=postgres`。
