# T06 - 数据迁移与完整性

## 目标

建立从 SQLite 到 PostgreSQL 的可重复数据迁移流程，覆盖源库检查、SQL 包生成、目标库装载、约束校验、行数校验、dump/restore 和失败阻断。

## 执行步骤

1. 只读复制 SQLite 源库。
2. 对副本执行 `quick_check`、外键检查、表行数统计。
3. 生成 PostgreSQL schema、data、constraints/indexes、verify counts SQL 包。
4. 在空 PostgreSQL 临时库执行完整装载。
5. 创建约束和索引。
6. 执行行数校验和关键表抽样。
7. 执行 `pg_dump -Fc`。
8. 使用 dump 恢复到新库，并验证表数量和关键行数。

## 验收条件

- [ ] 源 SQLite 副本 `quick_check=ok`。
- [ ] 迁移使用副本，不修改生产源库。
- [ ] PostgreSQL 装载 118 张表成功。
- [ ] 外键约束创建成功。
- [ ] `verify counts` 通过。
- [ ] `pg_dump` 和 `pg_restore` 演练成功。
- [ ] 序列与主键最大值对齐。
- [ ] 附件引用完整性由 T07 给出可通过结论或签收豁免。

## 当前执行记录

### 2026-06-05 远程生产快照只读迁移演练

数据来源：

- 远程 `/lanshare/data/classroom.db` 只读压缩快照。
- 本地副本：`.codex-temp/remote-sqlite-snapshot-current/classroom.remote.db`
- SHA256：`AF1AB01AB6B75449EDB4496DB33A12883AC17DBC3F7625672EB3C468AC3C94F3`

源库结果：

- `quick_check=ok`
- 外键违规：0
- 表数量：118
- `submissions=1253`
- `classroom_behavior_events=304021`

PostgreSQL SQL 包：

- `.codex-temp/db-postgres-export-current/package/01-schema.sql`
- `.codex-temp/db-postgres-export-current/package/02-data.sql`
- `.codex-temp/db-postgres-export-current/package/03-constraints-indexes.sql`
- `.codex-temp/db-postgres-export-current/package/04-verify-counts.sql`

远程 PostgreSQL 装载结果：

- schema loaded：true
- data loaded：true
- constraints loaded：true
- verify counts：true
- `pg_dump -Fc` executed：true
- `pg_restore` executed：true
- restored table count：118

安全结论：

- `production_data_modified=false`
- `remote_data_modified=false`
- 仅使用远程 `/tmp/lanshare-*` 临时目录和临时容器。
- 临时容器和工作目录已清理。

当前状态：T06 的数据层装载、约束、dump/restore 演练已经通过；但正式生产迁移仍未执行。app 已具备 PostgreSQL 基础连接和启动 schema 只读校验保护，但业务 SQL、worker 队列和全栈回归未完成，仍不得切换生产。

## 2026-06-06 正式迁移执行结果

正式远程迁移已经完成，且没有覆盖或删除原始 SQLite 文件。

验收结果：

1. 源 SQLite 快照 `quick_check=ok`，外键违规 `0`。
2. PostgreSQL 目标库表数 `118`，与源快照一致。
3. 导入时点总行数 `346662`，与源快照一致。
4. 关键表抽样通过：`teachers=4`、`students=284`、`courses=5`、`class_offerings=9`、`assignments=88`、`submissions=1253`、`submission_files=8588`、`course_materials=2265`、`email_outbox=2475`、`agent_tasks=2`、`blog_posts=117`。
5. PostgreSQL app 一次性容器 schema 校验通过：`118/118 required tables`。
6. 导入报告：`.codex-temp/pg-remote-stage2/postgres-import.json`。
7. 直连验证报告：`.codex-temp/pg-remote-stage3/postgres-direct-verify.json`。

数据安全结论：

1. 原始 SQLite 仍保留；切换前备份为 `/tmp/lanshare-cutover-backups/20260606T025934Z/classroom-before-pg-cutover-20260606T025934Z.db`。
2. PostgreSQL dump 备份为 `/lanshare/data/postgres-backups/pre-final-cutover-20260606T025934Z.dump`。
3. 后续不得再以迁移时点 `346662` 作为实时行数等式要求，因为切换成功后 PostgreSQL 已可能产生新业务写入；该数值只用于证明迁移时点一致性。
