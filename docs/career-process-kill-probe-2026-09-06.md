# 职业任务真实进程故障验收

本工具使用本地 PostgreSQL、`multiprocessing` 的 `spawn` 模式、实际共享 durable worker 的领取/执行/结果持久化/发布代码。只生成合成任务和临时业务表，不调用模型、不修改生产代码或数据。

运行：

```powershell
venv\Scripts\python.exe tools\career_postgres_process_kill_probe.py --output docs\career-process-kill-evidence-2026-09-06.json
$env:RUN_LOCAL_PG_CAREER_KILL_PROBE = '1'
venv\Scripts\python.exe -m unittest tests.test_career_process_kill_probe
Remove-Item Env:RUN_LOCAL_PG_CAREER_KILL_PROBE
```

工具创建 `career_kill_probe_<32 位随机十六进制>` schema，连接只允许 `localhost`、`127.0.0.1` 或 `::1`，每条业务连接的 `search_path` 只有该 schema。子进程也重新验证边界；后台任务类型仅为本工具合成类型。父进程通过私有 Pipe 收到检查点消息、核对实际 PID 后，只调用自己持有的 `Process.kill()`。没有按名称或任意 PID 查杀，没有结束数据库会话的权限操作。

| 强杀位置 | 已提交状态 | 恢复断言 |
| --- | --- | --- |
| 领取后、执行前 | `running`，无结果、无业务写入 | 有效租约阻止重复领取；测试租约过期后以新 token 重新执行，旧 token 无法存结果 |
| execute 等待中 | 执行记录已创建，无结果、无业务写入 | 终止真实运行中的 worker，租约恢复后执行 1 次有效结果发布 |
| 结果已持久化、尚未应用 | `result_ready`，1 条候选结果 | 直接重放持久化结果，execute 总次数保持 1 |
| apply 已更新业务行、事务尚未提交 | 业务更改与发布记录均未提交 | 进程退出令 PostgreSQL 回滚事务；恢复 delivery 后仅提交 1 次业务发布 |

另外强杀 2 个子进程验证：执行中取消仍保留已接纳任务，旧 token 的迟到结果被拒绝；业务发布事务崩溃后更新到 revision 2，即使故意遗漏主动 supersede 通知，领域的 revision/current_job_id 条件仍阻止旧结果覆盖编辑，新 revision 最终仅发布 1 次。旧候选结果保留供审计。

发布日志表故意不设置 `(document_id, revision)` 唯一约束，以免测试约束代替真实发布协议挡住重复。实际核对每个 revision 的发布次数，重放同一 delivery token 和已终止 worker token 均不得重复发布。全部 7 条已接纳任务必须仍在账本中，终态为 5 succeeded / 1 cancelled / 1 superseded。

测试在确认有效租约确实阻止领取后，仅把已被终止 worker 对应的隔离任务行 `lease_expires_at` 推进到过去。生产租约仍为 **120 秒**，心跳仍为 **30 秒**；工具耗时不能解释为生产恢复耗时。本验收也没有测量真实 AI 上游取消、主机断电、PostgreSQL 崩溃或生产混合负载。

无论主体成功或抛出异常，`finally` 都移除随机 schema，并核对 schema 不存在且工具专属 `application_name` 的活动数据库连接为 0；所有自建子进程都会 join。专项测试另用主动抛出的验收异常验证失败路径清理。

本次实际执行的计数、子进程强杀退出码、清理结果、运行耗时及所测源码 SHA256 见 [JSON 证据](career-process-kill-evidence-2026-09-06.json)。这是自动化故障注入验收，没有人工专家质量评审含义。
