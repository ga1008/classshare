# LanShare 开发 / 测试 / 部署指南

> 核心原则：**本地开发数据库与线上一致，统一使用 PostgreSQL**。
> 历史上本地用 SQLite、线上用 PostgreSQL，导致只在 SQLite 测试通过、上线却 500 的事故
> （例：`SELECT DISTINCT ... ORDER BY 派生表达式` SQLite 容忍、PostgreSQL 拒绝）。
> 现在应用运行时只认 PostgreSQL；SQLite 仅保留为：①单元测试的内存隔离 ②一次性迁移导出工具的读取源。

## 1. 数据库引擎约定

- 应用运行时（dev 与 prod）：**PostgreSQL only**。`DB_ENGINE` 默认即 `postgres`，`.env` 指向本机 PostgreSQL。
- `classroom_app/db/postgres.py` 的 `sqlite_sql_to_psycopg()` 是 **方言兼容层**（`?`→`%s`、`GROUP_CONCAT`、`LIKE NOCASE`→`ILIKE` 等），保留不动——它不是“SQLite 后端”，是查询占位符兼容层。
- `schema_*.py` 里的建表 DDL 仍是 SQLite 语法：它们被**迁移导出工具** `tools/db_postgres_export.py` 读取并翻译成 PostgreSQL DDL。请勿手写 PostgreSQL 专属 DDL 绕过它。

## 2. 本地一次性环境搭建

1. 安装原生 PostgreSQL 16（已通过 `choco install postgresql16 --params "/Password:..."` 安装为 Windows 服务 `postgresql-x64-16`，监听 `127.0.0.1:5432`）。
2. venv 安装驱动：`venv/Scripts/python.exe -m pip install "psycopg[binary]==3.3.4"`（已在 `requirements.lock.txt`）。
3. 创建角色与库（超管口令见安装时设定）：
   ```bash
   export PGPASSWORD=<postgres超管口令>
   PSQL="/c/Program Files/PostgreSQL/16/bin/psql.exe"
   "$PSQL" -h 127.0.0.1 -U postgres -c "CREATE ROLE lanshare LOGIN PASSWORD 'lanshare_dev_pwd';"
   "$PSQL" -h 127.0.0.1 -U postgres -c "CREATE DATABASE lanshare OWNER lanshare;"
   ```
4. `.env` 数据库段（本地，部署脚本已排除 `.env`，不影响线上）：
   ```
   DB_ENGINE=postgres
   DATABASE_URL=postgresql://lanshare:lanshare_dev_pwd@127.0.0.1:5432/lanshare
   POSTGRES_BACKEND_READY=true
   POSTGRES_USER=lanshare
   POSTGRES_PASSWORD=lanshare_dev_pwd
   POSTGRES_HOST=127.0.0.1
   POSTGRES_PORT=5432
   ```
5. 灌入 schema + 数据（与线上同一条迁移链路；一键脚本见 `tools/db/setup_local_pg.ps1`）：
   - 把当前 SQLite（`data/classroom.db`）升级到最新 schema 后导出，再 `psql` 应用 `01-schema.sql / 02-data.sql / 03-constraints-indexes.sql`；
   - 最后 `python -c "from classroom_app.db.schema import init_database; init_database()"` 补齐运行期托管表（poll/scheduler/gongwen/分组方案 等）并校验 130/130 必需表。
   - 注意 `03-constraints-indexes.sql` 含 `BEGIN;...COMMIT;`，整批遇错会回滚；逐句应用更稳：`sed '/^BEGIN;/d;/^COMMIT;/d' 03-...sql | psql ...`。

## 3. 运行应用（本地，PostgreSQL）

```bash
venv/Scripts/python.exe -m uvicorn classroom_app.app:app --host 127.0.0.1 --port 8099
curl -s http://127.0.0.1:8099/api/internal/health   # database_backend.engine 应为 postgres
```

## 4. 测试

- **单元测试统一通过隔离入口运行**，在应用导入前阻断 dotenv 与真实 PostgreSQL，并使用本轮独占的 SQLite 和临时目录：
  ```bash
  venv/Scripts/python.exe tools/test_backend.py --pattern "test_*.py" --report .codex-temp/evidence/backend-tests/result.json
  ```
  `--pattern` 可限定本次相关测试；`--check-only` 仅检查隔离配置。父进程等待测试子进程退出后删除本轮 scratch，报告单独保留在 scratch 外；失败或中断也写明结果与清理状态。不要以直接导入应用或普通 `unittest discover` 替代此入口。
- **数据库功能还需在隔离合成的原生 PostgreSQL 上验证**，单元测试的 SQLite 不能覆盖 PostgreSQL 方言与并发合同。自有回环集群可用 `venv/Scripts/python.exe tools/wanderer_native_pg_probe.py`；其他领域 probe 使用其明确指定的独立合成集群。不要连接本机业务库或线上库运行测试，即使计划回滚。
- 测试夹具创建连接、临时目录或 patch 后立即注册清理，使用 `enterContext` / `enterClassContext` 或 `addCleanup` / `addClassCleanup`，覆盖 `setUp` / `setUpClass` 中途失败。资源释放顺序、部署资料与恢复证据的保留边界见 [临时产物生命周期](docs/temporary-artifact-lifecycle-2026-11-12.md)。

### PostgreSQL vs SQLite 常见方言坑（上线前自检）
- `SELECT DISTINCT ... ORDER BY <派生表达式>`：PG 要求 ORDER BY 列在选择列表里；多数情况 DISTINCT 可去掉。
- `ON CONFLICT (...)`：PG 需要对应列上有 UNIQUE 约束/索引。经迁移导出的表，内联 `UNIQUE(...)` 已由 `tools/db_postgres_export.py` 的 `_unique_constraints()` 还原；新表建议用 `ensure_*_schema()` 引擎感知 DDL 建（自带 UNIQUE）。
- 布尔以 INTEGER 0/1 存储；时间统一 ISO-8601 TEXT。
- `?` 占位符由方言层转 `%s`，直接用 `?` 即可。

## 5. 部署（线上 PostgreSQL，Docker Compose）

```bash
powershell.exe -ExecutionPolicy Bypass -Command "$env:PATH='C:\Windows\System32;'+$env:PATH; & 'deployment\deploy_remote.ps1'"
```
- 脚本排除 `data/`、`.env`、`docker.env`，备份远端代码与 `pg_dump`，`compose build app` 后 `up -d` 并健康检查。
- 线上库的连接串/口令在服务器自己的 `.env`/`docker.env`，与本地 `.env` 互不影响。
- 提交后 `git push origin <branch>`。

## 6. 重新灌库 / schema 漂移

- 重新导出：`venv/Scripts/python.exe tools/db_postgres_export.py --source-db data/classroom.db --runtime-root .codex-temp/db-pg-export`，再逐句应用三个 SQL，最后 `init_database()`。
- 运行期托管列缺失（如曾经的 `assignment_wrong_summary_jobs.run_token`）：加进 `classroom_app/db/postgres_schema.py` 的 `POSTGRES_RUNTIME_COLUMN_DEFINITIONS`，`init_database()` 会自动补列。
