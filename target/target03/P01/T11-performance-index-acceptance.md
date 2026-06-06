# T11 - 性能、索引、容量和压测验收

## 目标

建立 SQLite 与 PostgreSQL 的性能基线、索引建议和远程 Docker Compose 环境验证，确保迁移后不仅能运行，而且能承载课堂并发、后台任务和数据查询。

## 性能预估

1. 单请求读取：PostgreSQL 可能增加网络和连接池开销，但应控制在可接受范围内。
2. 并发写入：PostgreSQL 行级锁预计优于 SQLite 全库写锁。
3. 队列领取：PostgreSQL 可通过 `FOR UPDATE SKIP LOCKED` 降低重复领取风险。
4. 大表查询：如果索引不足，PostgreSQL 会暴露慢查询。
5. 资源占用：PostgreSQL 会增加常驻内存和磁盘占用，远程服务器需要持续观察。

## 必须验证的场景

1. 作业提交列表和单学生提交定位。
2. 提交附件列表。
3. 课堂行为事件按课堂查询。
4. 邮件 outbox due 查询。
5. 课程材料按教师查询。
6. 消息中心近期通知查询。
7. 后续 app adapter 完成后，必须补充 30-50 学生并发全栈压测。

## 验收条件

- [ ] SQLite baseline 已记录。
- [ ] PostgreSQL baseline 已记录。
- [ ] 远程 Docker Compose 环境已记录。
- [ ] 核心查询无不可接受 full scan。
- [ ] 新增索引说明服务的查询和写入成本。
- [ ] app PostgreSQL adapter 完成后，全栈压测无 5xx、无队列重复领取、无明显锁等待。

## 当前执行记录

### 2026-06-05 远程 Docker Compose PostgreSQL 数据层压测

数据来源：

- SQLite 快照导出的 PostgreSQL SQL 包。
- 远程临时 Docker Compose 项目。
- PostgreSQL 镜像：`postgres:16-alpine`。

装载结果：

- public table count：118
- `classroom_behavior_events=304021`
- `01-schema.sql`：965ms
- `02-data.sql`：6574ms
- `03-constraints-indexes.sql`：2838ms
- `04-verify-counts.sql`：224ms

热点查询结果：

| 查询 | 平均耗时 | 最大耗时 | 返回行数 |
| --- | ---: | ---: | ---: |
| `submissions_lookup` | 2.472ms | 2.627ms | 1 |
| `submission_files_by_submission` | 1.428ms | 1.561ms | 6 |
| `behavior_events_by_class` | 14.236ms | 16.212ms | 100 |
| `email_outbox_due` | 2.837ms | 3.897ms | 50 |
| `course_materials_teacher` | 3.552ms | 5.154ms | 50 |
| `message_center_recent` | 3.742ms | 4.096ms | 10 |

索引候选：

- `submissions(assignment_id, student_pk_id, status)`
- `agent_tasks(status, updated_at)`
- `assignment_wrong_summary_jobs(status, updated_at)`
- `private_message_ai_jobs(status, updated_at)`

报告路径：

- `.codex-temp/remote-pg-compose-load-current/report.json`
- `.codex-temp/remote-pg-performance-current/report.json`
- `.codex-temp/db-performance-acceptance-current/reports/performance-acceptance.json`

安全结论：

- `production_data_modified=false`
- `remote_data_modified=false`
- `remote_runtime_data_modified=false`
- 临时 Compose 项目、容器、网络、工作目录和上传包已清理。

限制说明：

本次为远程 Docker Compose PostgreSQL 数据层压测，不是 LanShare app PostgreSQL 全栈压测。当前应用 PostgreSQL adapter 仍 fail-closed，因此后续必须补充 app/worker 真实连接 PostgreSQL 的全栈压测。

当前状态：T11 的数据层性能基线和远程 Compose 证据已完成；全栈性能验收仍依赖 T02-T05 的 app adapter 完成。

## 2026-06-06 高并发烟测工具 PostgreSQL 适配记录

本轮继续补强全栈压测前置工具链：

1. `tools/high_concurrency_smoke.py` 的 `_seed_test_data()` 通过应用数据库连接创建教师、班级、课程、课堂和学生测试数据，因此它必须具备 PostgreSQL 主键返回语义。
2. 该工具已改为使用 `execute_insert_returning_id()` 获取新记录 ID；SQLite 继续使用 `lastrowid`，PostgreSQL 使用 `RETURNING id`。
3. 新增 `tests/test_high_concurrency_smoke_postgres.py`，在 PostgreSQL engine fake connection 下验证 6 次种子插入都包含 `RETURNING id`，且 fake cursor 不提供 `lastrowid`。
4. 已通过 `python -m unittest tests.test_high_concurrency_smoke_postgres tests.test_full_stack_load_profile`，结果为 `Ran 6 tests ... OK`。
5. `tools/full_stack_load_test.py` 仍保留 SQLite `lastrowid`，原因是该工具面向隔离 SQLite 副本和本地临时 runtime root；其安全边界由 `assert_safe_runtime_root()` 和只复制源库的流程保护，不能用于证明 PostgreSQL app 全栈通过。

当前 T11 结论不变：数据层性能基线和烟测工具链继续增强，但正式验收仍需要真实 PostgreSQL app/worker 全栈运行、远程 Compose app 验证和观察窗口内性能指标。

## 2026-06-06 高并发烟测可重复运行记录

本轮继续补强 `tools/high_concurrency_smoke.py`，使它更适合后续真实 PostgreSQL app 验证反复执行：

1. `_seed_test_data()` 新增 `run_id` 语义，教师邮箱、班级名、课程名、学生姓名、学号和学生邮箱都会带唯一后缀。
2. `_login_teacher()` 改为使用 seed 返回的教师邮箱和密码，不再依赖固定 `TEACHER_EMAIL`。
3. 这样同一 PostgreSQL 测试库可以重复运行烟测，避免第二次执行时撞 `teachers.email`、`students.student_id_number` 等唯一约束。
4. `tests/test_high_concurrency_smoke_postgres.py` 已覆盖 PostgreSQL `RETURNING id`、唯一 run 后缀和动态教师登录。
5. 相关测试通过：`python -m unittest tests.test_high_concurrency_smoke_postgres tests.test_full_stack_load_profile`，结果为 `Ran 7 tests ... OK`。

当前 T11 结论仍不变：工具链更接近正式验收要求，但还没有完成真实 PostgreSQL app/worker 远程全栈压测。

## 2026-06-06 切换后性能与稳定性观察

正式切换后，基础性能风险低于 SQLite 单文件模式的并发写入上限，但仍需要持续观察真实课堂负载。

已取得的验收信号：

1. 迁移前远程 PostgreSQL 热点查询基线最大耗时约 `16.212ms`。
2. PostgreSQL schema gate 覆盖 `118/118 required tables`，降低启动后缺表缺列导致慢失败的风险。
3. 队列领取路径已使用 PostgreSQL `FOR UPDATE SKIP LOCKED`、`RETURNING` 或 advisory lock，预期会减少 SQLite `database is locked` 类竞争。
4. 切换后 postflight 通过，app、mailer、ai 等核心容器健康。
5. 本地 P01 完整回归通过：`Ran 252 tests ... OK`。

性能影响预估：

1. 常规读请求：预计持平或略有下降/上升，主要取决于网络与连接池；单次查询通常不会成为瓶颈。
2. 并发写入与 worker 队列：预计明显优于 SQLite，尤其是邮件、AI grading、Agent、blog crawler 等多 worker 领取场景。
3. 启动阶段：PostgreSQL schema 只读校验会增加少量启动检查成本，但换来 fail-fast 能力。
4. 磁盘 IO：PostgreSQL 会把写入压力从单 SQLite 文件转为 WAL/数据目录，需要观察 `/lanshare/data/postgres` 磁盘容量和 IO。

后续正式性能验收窗口：

1. 观察至少一个真实教学高峰周期。
2. 记录连接数、慢查询、锁等待、CPU、内存、磁盘 IO、PostgreSQL 容器重启次数。
3. 若出现慢查询，优先加索引或改查询，不得通过关闭约束、丢弃数据或绕过事务来“提速”。
