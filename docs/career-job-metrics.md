# 职业与简历队列运维快照

`career_job_metrics_service.py` 复用 `ai_jobs` / `ai_job_attempts`，只读取已注册的职业与简历任务类型。它不创建表、注册任务、重新入队或读取任务正文。学校、学生 ID、简历内容、任务 ID、供应商错误正文均不进入快照。错误类型经过固定白名单聚合，未知类型统一为 `other_error`。

维护入口为 `refresh_career_job_metrics()`，在本地 maintenance 的线程池里每 60 秒调用。函数自行使用短生命周期的只读、可重复读 PostgreSQL 事务；每条 SQL 的局部超时为 2 秒，失败保留上次成功快照并标记过期。不要把此函数放到 health/metrics HTTP 请求里，也不要传入其他业务正在使用的事务。

健康接口仅调用 `career_job_metrics_snapshot()`。它只复制当前进程内存里的聚合数据；进程初次启动尚无采样时返回 `available=false`。超过 180 秒未成功更新或刷新失败均为 `stale=true`，并返回数据年龄。多进程实例各自维护快照，不应把多个进程对同一数据库的队列数量累加。

采样口径：

- 活跃状态为 queued/running/retry_wait/result_ready。每个任务类型和状态通过 `(task_type,status,created_at,id)` 索引按创建时间有界读取，总计最多保留 2,500 条。若超出，`truncated=true`、`counts_are_lower_bounds=true`；最早排队时间仅代表样本中的 queued/retry_wait，以最初入队时间计算，包含重试等待。
- 最近任务只扫描全账本最新 1,001 个 ID，保留前 1,000 条再过滤职业类型；不按最近完成时间无限向历史追溯。最近执行尝试独立使用同样的全账本尾部样本策略。其他业务占满尾部时，职业样本可以为零；此时耗时为 null，不推算成功率。
- 排队耗时为 `created_at → first started_at`，只包含任务样本里已启动的任务。执行耗时使用尝试表中已结束的 execute 阶段，包含失败尝试，排除未结束尝试和 apply 阶段；因此不会把重试等待或渲染结果发布耗时混入执行时长。旧账本无时区时间按现有服务端本地时区解析。无效或逆序时间排除，并通过 `sample_count` 显示有效分母。
- 快照报告各样本上限、实际账本行数、职业行数、是否截断及时间覆盖范围。指标属于抽样观察，不是完整历史成功率或生产 SLA。

默认每次最多读取 4,503 行，SQL 为最多 `4 × 已注册类型数 + 2` 条查询，加 2 条事务设置；数据载荷是有界字段，未查询 payload/result JSON。每次使用一个数据库连接，完成后释放；健康请求额外数据库查询数为零。新增索引由正常 schema 迁移管理，不在采样或 HTTP 热路径建立。

本地验证：`venv\Scripts\python.exe tools\career_job_metrics_postgres_probe.py`。该工具复用现有 localhost 限制和临时 PostgreSQL schema，验证索引计划、只读事务、跨业务尾部采样、隐私、截断下界、执行耗时、1,000 次健康内存读取及失败保留。全部测试数据是合成数据，结束后验证 schema 已删除。
