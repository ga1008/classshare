# 职业命令接纳波峰验收

新增独立薄适配器 `tools/career_admission_wave_probe.py`，仅复用已有 `isolated_career_postgres` 安全 fixture，没有修改或复制 mixed/soak 工具或生产业务。当前完成的是短时冒烟；正式的 1000 人 / 60 秒、300 人 / 30 秒负载 **尚未启动**，由根任务在导出和 HTTP 验收结束后统一运行。

## 两个明确场景

1. **冷专业进入**：1000 名合成学生、10 个专业，60 秒开环调用真实 `POST /api/career-path/initialize`。初始 `career_major_networks`、职业会话与 `ai_jobs` 均为空；十专业不含已内置网络的软件工程，确保全部冷启动。每个响应必须返回本人、正确专业、可操作的基础网络及可追踪的 queued 任务。同专业同 generation 必须只有一个共享任务。
2. **完整问卷增强**：300 名合成学生、10 个专业，先在测量外完成初始化。工具从真实 `GET /questions?mode=full` 读取当前完整问卷，按发布的题目和选项生成全部合法作答（当前 11 题，数量以接口为准）；30 秒开环调用 `POST /answers`，同时传 `enhance=true`。每一成功响应必须对应本人、提交 revision、input_hash、完整保存的答案和一个个人增强任务。

职业 API 当前返回 **HTTP 200**，含已入库任务及状态，这是其既有接纳语义；工具不会强行把它解释为必须 202，也不把 200 当作 AI 已完成。全程不启动 worker：必须看到所有模型任务 `attempt_count=0`、结果表为空。P50/P95/P99 衡量 ASGI 命令接纳与事务提交，不是职业网络生成耗时。

读取和命令使用一个真实 PostgreSQL 连接池，最大 12。开环按照固定到达时点调度，记录调度滞后；达到并发上限或错过窗口会明确记录 skipped 并验收失败，不静默降低负载。每个学生的主波峰响应状态、耗时、对应任务 ID 和 revision 均保留，成功任务逐条与数据库账本及所有权核对。出现异常也保留失败类型、阶段及已经完成的响应证据。

## 测量外控制组

- 对最多 20 名学生各发 3 个并发重复命令，确认任务数不增加。问卷的同一旧 revision 再提交必须为 409；正在排队的增强重复请求复用当前任务。
- 学生 2 携带学生 1 的增强 job_id 发命令必须按当前契约返回 409，且不得查询或改变另一学生的任务。
- 主波峰后单独把测试进程的 `MAX_PENDING_JOBS` 临时设为当前活动任务数量；一个额外控制学生提交增强应返回 429 和 `Retry-After: 30`，会话和任务变化全部回滚。恢复容量后，原请求可成功接纳并追踪。这个强制过载控制不计入主波峰接纳延迟，也没有修改生产默认容量。

## 命令

正式命令，仅供根任务统一启动：

```powershell
venv\Scripts\python.exe tools\career_admission_wave_probe.py --scenario entry --users 1000 --duration 60 --formal --output .codex-temp\career-entry-wave-1000-60-UNIQUE.json
venv\Scripts\python.exe tools\career_admission_wave_probe.py --scenario quiz --users 300 --duration 30 --formal --output .codex-temp\career-quiz-wave-300-30-UNIQUE.json
```

短冒烟命令：

```powershell
venv\Scripts\python.exe tools\career_admission_wave_probe.py --scenario entry --users 30 --duration 3 --output .codex-temp\career-entry-wave-smoke-UNIQUE.json
venv\Scripts\python.exe tools\career_admission_wave_probe.py --scenario quiz --users 30 --duration 3 --output .codex-temp\career-quiz-wave-smoke-UNIQUE.json
```

超过 100 学生或 10 秒需显式 `--formal`。工具拒绝覆盖已有输出，使用新文件名保存每轮源码 SHA 和结果。两个正式场景应串行运行，与其他负载错开。

只允许 fixture 校验过的 localhost PostgreSQL 和随机隔离 schema；`finally` 删除后独立核对 schema 不存在、工具命名的数据库连接为 0。没有真实 AI、Office 或邮件发送，也不读取实际学生数据。身份依赖使用合成身份，不包含 TCP/TLS、生产认证中间件与反向代理，因此桌面接纳延迟不能直接当作线上 SLA。

专项测试 `python -m unittest tests.test_career_admission_wave_probe` 覆盖完整问卷与开环失败统计；加环境变量 `RUN_LOCAL_PG_CAREER_ADMISSION_PROBE=1` 可验证异常时仍生成失败报告并完成 PG 清理。

当前最终短冒烟证据：[30 人 / 3 秒冷启动进入](career-entry-wave-smoke-2026-09-06-v2.json)、[30 人 / 3 秒完整问卷增强](career-quiz-wave-smoke-2026-09-06-v2.json)。两者分别追踪 10 / 40 条主波峰任务，主请求均为 200，没有漏发或任务归属错误；接纳 P95 分别为 20.220 / 25.890 毫秒。该数字只描述本机短冒烟，正式波峰尚未执行。
