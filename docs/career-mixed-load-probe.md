# 职业与简历隔离混合负载工具

`tools/career_mixed_load_probe.py` 复用本地 PostgreSQL fixture，自动创建唯一 schema 并在正常退出时删除。只允许数据库地址为 loopback，所有业务连接的 `search_path` 限制到该 schema；不读取应用学生或生产数据。不要把测试应用挂到生产服务器。

```powershell
python -B tools/career_mixed_load_probe.py --duration 60 --rps 50 --users 1000 --writers 100 --save-interval 10 --seed 42 --output .codex-temp/career-load-60.json
python -B tools/career_mixed_load_probe.py --duration 1800 --rps 50 --users 1000 --writers 100 --save-interval 10 --seed 42 --output .codex-temp/career-load-30m.json
python -B tools/career_mixed_load_probe.py --duration 3600 --rps 50 --users 1000 --writers 100 --save-interval 10 --seed 42 --output .codex-temp/career-load-1h.json
```

同一机器一次只运行一个负载测试。运行期间暂停相关代码修改，并检查报告的 `fixed_code_during_run`。已有文件可能覆盖，使用独立输出名保留每轮证据。每 30 秒写同名 `.progress.json`，最终报告含完整的逐秒资源样本。

## 负载与数据

- 1000 名合成学生，10 个专业；每人有个人资料、教育、实践经历、技能、自我介绍和一份冻结快照草稿。材料有专业与学生差异，数据密度在 `dataset` 中声明。
- 50 次读取/秒：50% 职业状态、20% 简历 compact 列表、10% readiness、10% 真实学生课表、10% 既有个人头像只读接口。教学查询替换了原职业状态的 10%，没有增加原读流量。学生顺序由 seed 打散，避免接口类型与专业绑定。
- 另有 100 人错峰、每 10 秒保存一次，交替保存测评草稿和简历。默认 5% 主动发送不匹配版本，预期返回 409；成功响应更新后续保存版本。读取与保存合计约 60 RPS。
- 另外每秒入队一项合成自我介绍建议。只替换 AI handler 的 execute，保留真实路由、claim、租约、结果写入和 apply。两个 AI 槽位，默认每个合成模型执行等待 2 秒。运行结束时未完成任务随临时 schema 删除，不用于验证全部任务清空。
- 真正的 `psycopg_pool.ConnectionPool` 最小 2、最大 12；所有业务域复用同一池，不逐请求新建物理连接。
- 混合前后分别运行两组 10 秒、50 RPS 对照：头像轻量查询与真实教学课表查询，默认各有 500 个样本，独立报告。每个端点此前有 10 次不计入延迟分布的预热；职业初始化与建数据同样排除。头像仍不能代表教学接口。
- 教学接口使用真实 `/api/dashboard/course-schedule/overview`，补充 10 名合成教师、30 门课程/课堂、1200 个课次与学期/校历表；每名学生应只看到本班 3 门课程、120 个课次，逐响应核验。既有 SQL、权限范围、序列化均保持真实。
- 每 30 秒一份合成作业通过真实 `sync_assignment_due_reminders` 布防 T-2h，首次安排在 10 秒后。默认调度节拍为 20 秒；只布防在混合窗口内还剩至少一个完整轮询周期的任务。独立调度线程运行真实 claim/dispatch/通知写入，与职业请求竞争同一个最多 12 连接池；生产调度器为独立进程，此处是明确的同池竞争场景。

## 测量点与判读

使用 HTTPX ASGITransport 调用真实 FastAPI 路由，包含路由逻辑、同步线程池排队、数据库池等待和序列化；不包含公网、TCP、TLS、反向代理或浏览器。测试应用使用合成身份依赖覆盖，也不包含生产应用的会话认证、中间件和完整启动流程。`requests` 分接口记录样本数、P50/P95/P99、状态码、字节数和 SQL 次数；`expected_409_conflicts` 与 `http_5xx_count` 分开报告。开环读取达到并发上限或调度追不上时，记录 `arrival_skipped`，不会静默降低负载。`arrival_dispatch_lag` 仅为发压器的请求发起节拍延迟，不能作为业务提醒延迟。

`assignment_reminders` 单独记录真实任务的 `run_at`、领取、handler 开始、第二连接确认通知已提交可见、任务完成五个时间点。提醒只应写给本班未提交且在读的学生；每份作业有已提交对照，另有停学与外班对照。末尾在性能样本外重放真实 handler，确认不会新增重复通知。既有时间戳存储精度为秒，报告不得宣称亚秒调度精度；20 秒轮询本来就允许接近 20 秒的等待。站内通知实际写入隔离 schema；邮件入队被关闭，邮件发送 worker 不启动。

Windows 探针进程在导入原生 PG 驱动前设置 `TZ=CST-8`，应用仍使用 `APP_TIMEZONE=Asia/Shanghai`。这是为了避免 Windows CRT 不识别 IANA `TZ=Asia/Shanghai`，使 `datetime.now()` 与提醒服务的中国时间错开；不改系统时区或生产实现，Docker 原有 Asia/Shanghai 配置保持不动。

数据库等待包括准确的连接池 checkout 时长、SQL execute 全程时长，以及每秒采样本池 PostgreSQL 会话的 `wait_event`。SQL 时长包括执行和等待，不能等同于锁等待。每个阶段/类型的耗时序列最多保留前 250000 项；默认一小时请求与 checkout 数据低于此上限，SQL 分布达到上限后不再涵盖更晚执行，需结合全程 checkout 和会话采样判断。报告中的 pool 累计统计含准备数据阶段，分阶段 checkout 分布适合判断混合阶段的竞争。

应用 RSS/CPU 包含同进程的负载发生器及累计耗时样本，不能直接把其随时间增长判断为业务内存泄漏；CPU 以一个逻辑核 100% 计量，另提供完整混合区间的 CPU 时间与平均值。本池数据库后台进程单独采样，RSS 相加可能重复计算共享页，且不包含 PostgreSQL 公用后台进程。当前桌面为约 20 逻辑核、16 GB，线上为 2 CPU、3.57 GB；不得用桌面数据宣称生产 SLA。

`ok` 表示没有非预期响应、没有漏发请求、观察到的 AI 并发不超过 2，且窗口内提醒已完成、收件人准确、重放不重复。它不替代具体延迟、内存增长、后台错误及长期稳定性验收；实际阈值应由根任务的验收方案决定。
