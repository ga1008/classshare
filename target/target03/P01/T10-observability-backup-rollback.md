# T10 - 观测、备份、恢复与回滚

## 目标

建立数据库切换前后的观测、备份、恢复和回滚能力，确保 PostgreSQL 切换不是不可逆操作。

## 必须具备的能力

1. app health/metrics 能显示当前数据库后端。
2. SQLite 切换前完整备份。
3. PostgreSQL 迁移后 `pg_dump -Fc`。
4. PostgreSQL dump 可恢复到临时库。
5. postflight 能确认 app 和 worker 当前连接的数据库后端。
6. 切换失败时有冻结写入、回滚和数据补偿决策点。

## 回滚原则

1. 如果 PostgreSQL 切换前失败，保持 SQLite 继续作为权威库。
2. 如果 PostgreSQL 切换后已产生新写入，不得简单切回旧 SQLite。
3. 一旦出现数据分叉，必须先冻结写入，再决定导出补偿、反向同步或修复 PostgreSQL。
4. 回滚过程必须保留所有备份和报告。

## 验收条件

- [ ] SQLite 备份可恢复。
- [ ] PostgreSQL `pg_dump -Fc` 执行成功。
- [ ] `pg_restore` 到临时库成功。
- [ ] 恢复库表数量与源库一致。
- [ ] postflight 可检查数据库后端。
- [ ] 报告中不包含密码或连接串明文。

## 当前执行记录

已完成：

- 新增 `tools/db_backup_rollback.py`。
- SQLite 副本备份和恢复演练完成。
- 远程临时 PostgreSQL 装载后执行 `pg_dump -Fc`。
- 使用 dump 恢复到临时库并验证表数量 118。
- `.codex-temp/db-backup-rollback-current/reports/backup-rollback.json` 已记录 PostgreSQL dump/restore 演练完成。

仍未完成：

- 生产维护窗口中的真实 `/lanshare/data` 备份。
- 生产 PostgreSQL 持久目录备份策略。
- PostgreSQL adapter 启用后的连接池、慢查询、锁等待、deadlock 指标。

当前状态：T10 的演练证据已覆盖数据层恢复，但生产切换回滚能力必须在维护窗口内再次执行并留存。

## 2026-06-06 观测时间戳清洁记录

本轮对运行时 metrics 的时间戳实现做了低风险清洁：

1. `classroom_app/services/runtime_metrics_service.py` 的 `_utcnow_iso()` 已从 `datetime.utcnow()` 改为 timezone-aware `datetime.now(timezone.utc)`，仍保持 `Z` 结尾输出。
2. `rg -n "utcnow\(" classroom_app tests tools -g "*.py"` 已无命中。
3. P01 回归集合通过：`Ran 232 tests ... OK`，且不再出现 `datetime.utcnow()` deprecation warning。

该改动不改变 metrics JSON 结构，只减少后续 Python 版本升级和长期观测窗口中的噪声风险。
## 2026-06-06 health 数据库后端检查补强

本轮新增 `tools/deploy/health_backend_check.py`，用于把 `/api/internal/health` 中的 `database_backend` 变成可机器验收的切库证据。

新增验收条件：

1. 切换到 PostgreSQL 后，postflight 必须以 `-ExpectedDbEngine postgres` 运行，并产出 `health-database-backend.json`。
2. 恢复到 SQLite 后，postflight 必须以 `-ExpectedDbEngine sqlite` 运行，证明服务没有停留在 PostgreSQL 或异常 fallback 状态。
3. 报告必须显示 `configured=true`；否则即使 HTTP 200，也不能视为数据库后端可用。
4. 如果 health 中的数据库详情包含未打码 PostgreSQL URL，检查器必须失败；输出报告不得记录真实密码。
5. `tools/db/run_dual_backend_tests.ps1` 支持 `-ExpectedDbEngine`，本地 Win11 临时 app 验证时也必须显式声明期望后端。

建议命令：

```powershell
tools\db\run_dual_backend_tests.ps1 -BaseUrl http://127.0.0.1:8000 -ExpectedDbEngine sqlite
tools\deploy\postflight.ps1 -BaseUrl https://guardianangel.net.cn -ExpectedDbEngine postgres -CheckPostgres
tools\deploy\postflight.ps1 -BaseUrl https://guardianangel.net.cn -ExpectedDbEngine sqlite
```

这些命令只读 health 和容器状态，不写生产数据；真实切换仍必须等待 T12 gate 变为 `ready`。
## 2026-06-06 邮件 worker health schema 补强

`/api/internal/health` 会调用 `email_worker_health_snapshot()`，该路径依赖 `email_worker_heartbeats` 和 `email_outbox`。为避免切换后才发现 health 5xx，本轮把邮件 worker 观测依赖纳入 PostgreSQL 启动前 schema gate：

1. `email_worker_heartbeats` 必须存在，并包含 `worker_id`、`status`、`queue_depth`、`last_error`、`updated_at`。
2. `email_outbox` 必须包含 `attempt_count`、`sent_at`、`last_error`，用于队列重试、成功/失败写回和错误摘要。
3. `teacher_email_configs` 必须存在并包含 SMTP/IMAP 配置、频率限制和发送状态字段，避免 mailer 发送时才失败。
4. schema gate 当前输出为 `PostgreSQL schema verified: 45/45 required tables`。

验证记录：

1. `python -m unittest tests.test_db_postgres_schema tests.test_email_notification_queue_claim tests.test_api_contract_schemas` 通过。
2. `python -m py_compile classroom_app/db/postgres_schema.py classroom_app/services/email_notification_service.py` 通过。

该检查不写生产数据；它只是把阶段 5 postflight 可能遇到的观测失败提前到阶段 0/3 的 schema 校验中。

## 2026-06-06 备份、观测与回滚实绩

本轮生产切换已经实际验证一次失败回滚和一次成功切换。

已生成并必须保留的恢复点：

1. 首次切换尝试备份目录：`/tmp/lanshare-cutover-backups/20260606T024730Z`。
2. 成功切换前备份目录：`/tmp/lanshare-cutover-backups/20260606T025934Z`。
3. 成功切换前 SQLite 备份：`/tmp/lanshare-cutover-backups/20260606T025934Z/classroom-before-pg-cutover-20260606T025934Z.db`。
4. PostgreSQL 成功切换前 dump：`/lanshare/data/postgres-backups/pre-final-cutover-20260606T025934Z.dump`。
5. 远程 `.env`/`docker.env` 初始备份：`/lanshare/.env.pg-stage1-backup-20260606T023840Z`、`/lanshare/docker.env.pg-stage1-backup-20260606T023840Z`。

回滚验证：

1. 首次切换因 PostgreSQL dict-row 兼容问题失败，脚本已恢复 `DB_ENGINE=sqlite` 并重启服务。
2. 回滚后 app 恢复健康，证明恢复路径可执行。
3. 修复后再次切换成功，当前不需要恢复到 SQLite。

当前观测结果：

1. HTTP health 与容器内 `database_backend_state()` 均确认 PostgreSQL。
2. 最近部署后 app 日志未出现新的数据库启动错误。
3. `email_worker.ok=true`，行为写入 worker 存活。
4. `background_tasks.ok=false` 来自历史失败任务计数；这不是本次 PostgreSQL 切换后的连接失败，但应作为独立运维清理项跟踪。

恢复纪律：

1. 从现在起 PostgreSQL 是生产权威库。
2. 若恢复 SQLite，必须先冻结写入、dump PostgreSQL 当前状态、比对切换后新增数据，再决定补偿方案。
3. 不得直接用切换前 SQLite 备份覆盖当前生产状态。
