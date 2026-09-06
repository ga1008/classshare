# 职业功能低强度浸泡工具

`tools/career_soak_probe.py` 已准备，当前只完成短时冒烟，**尚未启动或通过 24 小时验收**。长跑应由根任务在生产代码冻结、导出/HTTP/长混合验收结束后统一启动，同一机器一次只运行一项负载测试。

## 设计与复用边界

工具直接复用 `career_mixed_load_probe.exercise`、它的连接池计时适配器，以及 `career_teaching_reminder_probe` 的真实教学与提醒链路。只初始化一次合成学生、10 个专业、简历草稿、材料和教学数据；在一个应用进程、一个 ASGI app、一个随机 PostgreSQL schema 内持续运行。没有每小时新建 schema、重新初始化应用或复制实现领域逻辑。

应用、负载发生器、教学接口与提醒线程共用最大 **8** 连接的池。唯一职业 worker 子进程使用最大 **4** 连接的池；所有业务连接上限合计 **12**。setup 的非池连接会在流量开始前关闭，排空和两个池关闭后才执行 schema 清理验证。每次轮换只重启已持有的自建 `Process`，先强杀并 join、确认旧进程退出，才创建下一代；没有按名称或任意 PID 终止其他进程。

模型 execute 使用确定性 stub；职业/简历请求、队列账本、claim、租约、结果持久化、领域 apply、课程课表权限查询、作业提醒筛选/通知提交/去重均使用真实实现。不会调用真实 AI、发 SMTP 邮件或启动 Office 转换。提醒验证还包括已提交、停学及外班学生不收通知，以及重放真实 handler 不新增重复通知。

## 指标保留与故障轮换

- 延迟使用固定数量桶的直方图，统计全程每一条观测；超过 25 万条仍会影响分位数，不采用“只保留最早样本”。分位数明确是 **直方图上界**，不能当作精确 P95/P99。
- 默认每 5 分钟把当前桶的请求、状态码、字节数、连接池等待、SQL 时长及最近最多 300 条逐秒资源样本写入 `.buckets.jsonl`，随后清空该桶。全程总计数和直方图使用固定内存，进程内不保存 24 小时逐请求或逐秒列表。
- JSONL 另记录开始配置、源码 SHA、每次强杀、已验证清理及最终状态。每桶复查源码，最终报告合并观察到的变化；代码发生变化不能给出 `ok=true`。
- 应用进程保持 24 小时连续运行。worker 默认每小时用一个合成 canary 依次触发：领取后、execute 等待中、结果已持久化、apply 已写业务行但事务未提交。每次记录 PID、generation、退出码及 join；重启后验证 canary 只发布一次。
- worker 的 RSS 按 generation 观察。重启会重置其内存，不能用新一代低 RSS 证明旧一代没有泄漏。应用 RSS 包含负载发生器、提醒测量和固定容量指标缓存；提醒最多 288 个长跑记录、每小时故障记录以及连接池生命周期 PID 对照的有界增长也需与业务增长区分。
- 长跑默认保留真实 **120 秒租约 / 30 秒心跳**，等待实际租约自然过期；短冒烟可显式 `--fast-fault-recovery` 只推进隔离任务行的租约时间。超过 10 分钟禁止该加速选项。
- 结束时先停止新请求和故障入队，允许 worker 排空全部已接纳任务，再关闭 worker、提醒线程和两个连接池。最终核对“初始化共享任务数 + 成功 202 请求数 + canary 数”等于保留的账本任务数，且没有待处理任务或失败终态。默认排空预算 300 秒，超出就验收失败，不把清理 schema 当成已完成任务。

## 运行方式

短时覆盖全部故障点的冒烟（使用新的输出名，不覆盖旧证据）：

```powershell
venv\Scripts\python.exe tools\career_soak_probe.py --duration 24 --users 20 --writers 2 --save-interval 4 --rps 2 --job-rps 0.2 --stub-seconds 0.1 --baseline-duration 1 --bucket-seconds 5 --fault-interval 5 --reminder-interval 10 --reminder-offset 2 --scheduler-poll-seconds 5 --fast-fault-recovery --drain-seconds 30 --output docs\career-soak-smoke-evidence-2026-09-06.json
```

拟定 24 小时命令，**本次未执行**：

```powershell
venv\Scripts\python.exe tools\career_soak_probe.py --duration 86400 --allow-long-run --users 1000 --rps 2 --writers 20 --save-interval 60 --job-rps 0.02 --stub-seconds 0.2 --baseline-duration 10 --bucket-seconds 300 --fault-interval 3600 --reminder-interval 600 --reminder-offset 30 --scheduler-poll-seconds 20 --drain-seconds 300 --output .codex-temp\career-soak-24h-UNIQUE.json
```

工具默认只跑 60 秒。超过 10 分钟必须显式传 `--allow-long-run`，并限制：至少 5 分钟分桶、至少 1 小时一次故障、每 5 分钟最多一次提醒、最多 100 保存者且保存间隔至少 30 秒、AI 入队最多 0.1 RPS。长跑前应检查磁盘空间与机器可用性，但这不是在共享生产服务器上压测的许可。

当前短冒烟原始证据为 [首轮 24 秒结果](career-soak-smoke-evidence-2026-09-06.json)；加入启动 schema/PID 记录和故障覆盖显式字段后的最终冒烟见 [最终结果](career-soak-smoke-evidence-2026-09-06-v2.json)。两个报告均保留自己的源码 SHA 和 JSONL，互不覆盖。

专项测试：

```powershell
venv\Scripts\python.exe -m unittest tests.test_career_soak_probe
$env:RUN_LOCAL_PG_CAREER_SOAK_PROBE = '1'
venv\Scripts\python.exe -m unittest tests.test_career_soak_probe
Remove-Item Env:RUN_LOCAL_PG_CAREER_SOAK_PROBE
```

可选 PG 测试主动令 exercise 抛错，核验仍写出 `cleanup` 事件、schema 不存在、工具专属数据库连接为 0。正常退出也进行独立核对。若整个父进程遭系统强杀，Python `finally` 无法执行；JSONL 提供已有证据，需要依据此次随机 schema 标识做人工确认后的清理，工具不会后台删除其他 schema。

## 验收解释

短冒烟验证了启动器、四个强杀位置、分桶、课程/提醒、任务排空和资源清理的接线正确，不能替代 24 小时趋势结论。`ok` 检查请求异常、漏发、队列失败/排空、canary 重复、连接与模型槽位、提醒接收者、源码变化和清理；仍需审阅每桶 RSS、延迟及数据库等待走势。

建议长跑结束后比较预热后首小时与最后一小时：请求错误或任务丢失必须为 0；应用 RSS 应进入可解释的稳定区间；教学与职业接口的延迟不能持续恶化；按 worker generation 分析其内存走势。具体 RSS/延迟上限由总体方案与机器资源预算确定，本工具不把桌面 ASGI 数据解释为生产 SLA，也不把自动化结果标成人类专家质量通过。
