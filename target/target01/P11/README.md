# P11 - 性能指标门与后台任务台账目标

状态：待实施
创建日期：2026-06-04
优先级：P1
目标类型：运行时性能治理、后台任务可靠性治理、上线前压力门

## 目标定义

将 LanShare 的 P1 性能与后台任务治理收束为一个明确、可验收、可持续跟踪的工程目标：不把性能问题简单归因于 SQLite，而是围绕真实热路径缩短写事务、减少高频接口请求形状、用运行时指标观察 p95；同时建立统一的后台任务运行台账，让 AI 批改、材料 AI 导入、消息中心、邮件、博客爬虫、agent worker 等任务的队列深度、worker 状态、最近错误、心跳与可恢复状态可以被统一查看、测试和验收。

本目标完成后，系统需要能清楚回答：

1. 当前课堂、作业、草稿、聊天、课堂状态、AI 回调等热路径的 p95 延迟是多少，是否比优化前更稳定。
2. 当前是否仍存在长时间持有 SQLite 写事务的热接口，特别是把外部 AI 调用、文件处理、消息推送或网络等待包进写事务的路径。
3. 200 人课堂 profile 下，登录、课堂页、聊天刷新、草稿保存、作业提交、教师查看提交、AI 回调等核心路径是否能稳定通过压力门。
4. 每一种后台任务当前有多少排队、多少运行中、多少失败、哪个 worker 活跃、最近一次心跳与错误是什么。
5. 哪些任务必须跨重启恢复，哪些任务允许内存级短任务；关键任务重启后是否不会丢状态、不会重复破坏性执行、不会静默卡死。

## 当前基线

### 已有性能基础

- `classroom_app/db/connection.py` 已设置 SQLite 运行参数：`PRAGMA journal_mode=WAL`、`PRAGMA synchronous=NORMAL`、`PRAGMA busy_timeout`、`wal_autocheckpoint`、`foreign_keys=ON`、`row_factory=sqlite3.Row`。
- P02 后 `classroom_app/database.py` 已经是兼容 facade，不再是原来的超大数据库实现文件。性能治理应继续围绕连接行为、事务边界和热接口形状，而不是回退成大文件改造。
- `classroom_app/app.py` 已有运行时指标中间件：`runtime_metrics_middleware` 会记录 HTTP 请求耗时和错误。
- `/api/internal/metrics` 已返回 `get_runtime_metrics_snapshot()`，其中 `classroom_app/services/runtime_metrics_service.py` 已计算 route 维度 `avg_duration_ms`、`p95_duration_ms`、`max_duration_ms`、`recent_errors`，并包含 websocket 连接与消息计数。
- `tools/full_stack_load_test.py` 已具备完整压测雏形，默认学生数仍是 `100`，并能记录各动作 `p50_ms`、`p95_ms`、`p99_ms`、`max_ms`。
- `tools/high_concurrency_smoke.py` 仍是 100 人量级烟测。

### 已有后台任务基础

- `ai_assistant.py` 已有全局 AI 并发和队列上限配置，例如 `GLOBAL_AI_CONCURRENCY`、`AI_QUEUE_MAX_PENDING`、`AI_PROVIDER_QUEUE_MAX_PENDING`。
- AI 批改状态已经落在 `submissions.status`、`grading_started_at`、`grading_attempt_fingerprint` 等字段上，并通过 `/api/internal/grading-complete` 回调闭环。
- 材料 AI 导入在 `classroom_app/routers/materials_parts/ai_import_helpers.py` 中有内存队列、worker task 和 `parse_status` 状态：`queued`、`running`、`completed`、`ai_failed`、`quality_failed`、`unsupported` 等。
- 邮件已有 `email_outbox` 和 `email_worker_heartbeats`，`/api/internal/health` 已暴露 `email_worker` 健康快照。
- 博客爬虫已有 run 状态、worker id、heartbeat 和 stale running reclaim 逻辑。
- agent worker 已有 `agent_tasks` 状态、事件、队列位置和运行中任务记录。
- 消息中心和部分 AI 私信回复仍存在 `asyncio.create_task(...)` 触发的内存型任务，必须明确哪些可以保留，哪些需要转为可恢复台账。

## 总体原则

- 不先怪 SQLite：WAL、`synchronous=NORMAL`、`busy_timeout` 已存在，P11 的第一判断对象是热路径事务边界、请求频率、轮询形状、外部调用等待、后台回调并发和写入聚合。
- 先量化再优化：所有性能改动必须有基线指标、改动后指标和对比结论，不能只凭主观感觉说变快。
- 缩短写事务：数据库写事务只包住必要的读写一致性片段，不能把 AI 请求、文件解析、HTTP 回调、邮件发送、长时间 sleep、复杂模板生成放进事务内等待。
- 降低热接口压力：对聊天刷新、草稿保存、课堂状态写入、材料状态轮询、AI 批改状态轮询等高频入口，优先做节流、批量、去重、条件请求、状态缓存或 server-side 合并。
- 用 p95 做上线门：平均值只能辅助判断，核心验收看 p95、错误率、队列积压、锁等待和恢复能力。
- 后台任务可解释：任何进入队列或 worker 的任务，都必须有任务类型、业务关联、状态、尝试次数、最近错误、创建/更新时间、worker/心跳信息。
- 重启不丢关键任务：材料生成、材料 AI 导入、AI 批改回调、邮件发送、agent 任务、博客爬虫等跨重启仍需恢复的任务，不能只依赖内存 `asyncio.create_task`。
- 不损坏线上数据：本目标的性能压测、队列恢复测试、崩溃重启测试都必须使用复制出来的临时数据根，严禁对线上 `/lanshare/data` 或本地真实 `data/classroom.db` 执行破坏性验证。

## 明确不做

- 不更换 SQLite 作为 P11 的第一阶段目标。
- 不因为压力测试失败就立即引入 Redis、Celery、PostgreSQL 或全新消息队列；只有在指标证明现有边界无法承载时，才另立迁移目标。
- 不改业务 URL，不改 P01 权限语义，不绕过 P03 浏览器回归门。
- 不在真实线上环境制造 200 人写入压测、批量 AI 批改、材料导入或草稿风暴。
- 不把生产凭据、真实 cookie、真实 token、真实学生/教师密码写入压测脚本、日志、README、trace 或提交记录。
- 不用 `skip`、`xfail`、超长 timeout 或软断言掩盖性能门失败。
- 不把所有后台任务粗暴塞进一个万能 worker，必须保留任务类型边界、恢复策略和业务所有权。

## 改进方向 A：性能指标门

### A1. 热路径清单

本阶段必须至少纳入以下热路径：

| 热路径 | 主要风险 | 需要观察的指标 |
| --- | --- | --- |
| 教师登录、学生登录 | session 写入、cookie、认证查询 | p95、错误率、session 表写入耗时 |
| dashboard 和课堂页首屏 | 聚合查询过多、同步查询阻塞 | p95、SQL 次数、慢 route 排名 |
| 聊天刷新和 websocket | 高频广播、房间状态写入、断连重连 | ws active、sent/received、错误数、消息延迟 |
| 草稿保存 | 高频写入、重复保存、提交冲突 | p95、写入次数、去重率、锁等待 |
| 课堂状态写入 | 心跳、行为追踪、学习进度更新 | 队列深度、批量写入耗时、丢弃/重试数 |
| 学生提交作业 | 文件/答案写入、状态更新、通知触发 | p95、提交成功率、写事务耗时 |
| 教师查看提交 | 列表聚合、状态统计、附件预览 | p95、查询耗时、分页效果 |
| AI 批改回调 | 并发回调、旧回调覆盖、防重复 | p95、状态一致性、失败/忽略数 |
| 材料 AI 导入状态轮询 | 轮询频率、队列积压、解析结果写入 | p95、队列深度、running stale 数 |
| 消息中心 summary | 高频顶部入口、已读状态写入 | p95、未读数一致性、写入节流 |

完成条件：

- [ ] 建立 `docs` 或 `target/target01/P11` 下的热路径基线表，记录每条路径的 route、触发页面、写入表、后台任务、风险说明。
- [ ] `/api/internal/metrics` 中能稳定看到上述热路径 route 的 `count`、`p95_duration_ms`、`error_count`、`recent_errors`。
- [ ] 200 人 profile 跑完后，必须生成包含每个动作 p95/p99/max/error 的 JSON 或 JSONL 结果。
- [ ] 每个热路径至少明确一个“缩短写事务”或“减少请求形状”的候选优化点。

### A2. 写事务收缩要求

所有热路径改造必须检查事务边界：

- [ ] 数据库写事务中不得执行外部 HTTP 请求、真实 AI 调用、邮件发送、长时间文件解析、DOCX/PDF 导出、sleep、轮询等待。
- [ ] 写事务中不得批量循环执行可拆分的慢操作；能预计算的内容在事务外完成，事务内只做最终一致写入。
- [ ] AI 回调必须先校验 submission 当前状态和 fingerprint，再执行短事务更新，旧回调只能被忽略，不能覆盖新状态。
- [ ] 草稿保存必须有去重/节流策略，连续输入不得形成无限制写风暴。
- [ ] 聊天、课堂状态、行为追踪写入优先走队列或批量落库，不能让每个心跳都占用长写事务。
- [ ] 材料 AI 导入记录状态更新要短事务化；解析和外部 AI 调用必须在事务外执行。

### A3. 请求形状收束要求

高频接口必须明确请求形状预算：

- [ ] 课堂页首屏不得因为一个班级 200 人而触发 200 个学生级独立请求。
- [ ] 教师查看提交列表必须支持分页、聚合或批量状态返回，不能每个学生单独请求提交状态。
- [ ] 草稿保存必须有前端 debounce 和后端幂等保护。
- [ ] 状态轮询必须有最小间隔、终态停止、页面隐藏降频策略。
- [ ] 消息中心 summary 不得在多个 island 重复并发请求同一接口。
- [ ] 材料 AI 导入 active/status 轮询必须只在存在活跃任务时高频执行，终态后停止或降频。
- [ ] AI 批改状态刷新不得对每个 submission 单独无节制轮询。

### A4. 200 人课堂 profile

在现有 100 人量级工具基础上新增明确的 200 人课堂 profile，作为上线前压力门。

建议命令形态：

```powershell
.\venv\Scripts\python.exe tools\full_stack_load_test.py --profile classroom-200 --students 200 --runtime-root .codex-temp\p11-load-runtime --json-output .codex-temp\p11-artifacts\classroom-200.json
```

如果当前工具暂不支持这些参数，实现时必须补齐等价能力，且仍使用复制数据库。

profile 至少覆盖：

- [ ] 200 名学生并发或分批登录。
- [ ] 200 名学生打开 dashboard 或课堂页。
- [ ] 200 名学生读取课堂材料/作业摘要。
- [ ] 200 名学生进行草稿保存或等价轻量写入。
- [ ] 至少 50 名学生提交作业。
- [ ] 教师打开提交列表和提交详情。
- [ ] 聊天或课堂状态刷新产生可控频率的读写压力。
- [ ] 可选：触发少量 mock AI 批改回调，验证回调并发不会破坏提交状态。

预期结果：

- [ ] 总体成功率不低于 99%。
- [ ] HTTP 5xx 为 0；如测试本身模拟可恢复错误，必须在结果中明确分类，不能混入服务端异常。
- [ ] 核心页面类接口 p95 不超过验收阈值；首期建议：dashboard/课堂页/作业页 p95 <= 1500ms，提交/草稿/消息 summary p95 <= 800ms，AI 回调 p95 <= 1000ms。若现有基线高于该值，必须先记录基线，再以“优化后不劣化且下降比例明确”为阶段验收。
- [ ] `/api/internal/metrics` 的 `recent_errors` 为空或只包含明确可解释的测试输入错误。
- [ ] 测试结束后复制库 `PRAGMA quick_check` 返回 `ok`。
- [ ] 行为队列、邮件队列、材料导入队列、AI 队列无异常积压；如果存在积压，必须有可恢复状态和预计消化速度。

## 改进方向 B：统一后台任务运行台账

### B1. 台账目标

建立统一“后台任务运行台账”，用于汇总系统内所有后台任务的运行状态，不要求第一阶段完全替换每个 worker 的内部实现，但必须让任务状态可观测、可追踪、可恢复。

建议新增服务边界：

```text
classroom_app/services/background_task_registry_service.py
classroom_app/services/background_task_ledger_service.py
classroom_app/routers/manage_parts/background_tasks.py
```

也可以采用其他命名，但必须清楚表达“任务类型注册”和“运行台账快照”两个职责。

### B2. 统一字段契约

台账中的每类任务至少输出：

| 字段 | 含义 |
| --- | --- |
| `task_type` | 稳定任务类型，例如 `ai_grading`、`material_ai_import`、`message_center_ai_reply`、`email_outbox`、`blog_news_crawler`、`agent_task` |
| `display_name` | 面向管理页的中文名称 |
| `queue_depth` | 排队或待处理数量 |
| `running_count` | 正在处理数量 |
| `failed_count` | 失败且未恢复数量 |
| `stale_count` | running 但已超过心跳或更新时间阈值的数量 |
| `active_worker_count` | 活跃 worker 数量 |
| `worker_ids` | 最近活跃 worker 标识，脱敏且有限长度 |
| `last_heartbeat_at` | 最近心跳时间 |
| `last_error_at` | 最近错误时间 |
| `last_error` | 最近错误摘要，必须截断并避免泄露密钥 |
| `oldest_queued_at` | 最早排队任务时间 |
| `recoverable` | 是否支持重启恢复 |
| `recovery_action` | 恢复策略，例如 reclaim stale、requeue、manual retry、worker restart |
| `source` | 状态来源表或内存队列说明 |

完成条件：

- [ ] 每个任务类型都有注册定义，不在页面或路由里手写散落映射。
- [ ] 管理接口能一次性返回所有任务类型的台账快照。
- [ ] `/api/internal/health` 或 `/api/internal/metrics` 至少能暴露摘要字段，便于部署后快速诊断。
- [ ] 超管页面或系统管理页面能查看台账；普通教师和学生不得访问后台任务全量状态。
- [ ] 最近错误必须截断，不能泄露 API key、cookie、token、真实密码、请求全文。

### B3. 首期必须纳入的任务类型

| 任务类型 | 当前来源 | 恢复要求 |
| --- | --- | --- |
| AI 批改 | `submissions.status='grading'`、AI assistant 队列、回调 fingerprint | 重启后不能永久卡在 `grading`；stale 任务可恢复为失败或重新排队，旧回调不能覆盖新结果 |
| 材料 AI 导入 | `material_ai_import_records.parse_status`、内存 queue worker | `queued/running` 重启后必须能重新入队或被标记为可恢复状态 |
| 材料最终生成 | 最终材料生成记录、材料导出相关状态 | 生成中断不能丢记录；可重试或明确失败 |
| 消息中心 AI 回复 | 消息中心私信/AI reply task | 不应只依赖内存 task；重启后未完成回复必须可查、可重试或明确失败 |
| 邮件发送 | `email_outbox`、`email_worker_heartbeats` | 已具备持久化队列，需接入统一台账 |
| 博客爬虫 | blog crawler run/config heartbeat | 已具备 run 状态和 stale reclaim，需接入统一台账 |
| agent worker | `agent_tasks`、task events、worker id | 已具备持久化任务，需接入统一台账 |
| 行为/课堂状态写入 | behavior write pipeline、课堂心跳/状态写入 | 至少暴露队列深度、worker alive、丢弃或失败数 |

### B4. 内存任务治理规则

出现 `asyncio.create_task(...)` 的地方必须分类：

- [ ] 纯 UI 辅助、可丢弃、无业务持久状态要求的短任务，可以保留内存 task，但需要说明失败影响。
- [ ] 会生成材料、写提交状态、写消息、触发邮件、改变 AI 状态、写系统配置、写课堂业务结果的任务，不得只依赖内存 task。
- [ ] 对于必须可恢复的任务，应先写持久化记录，再由 worker 领取执行。
- [ ] worker 领取任务必须具备幂等保护，重复领取不得造成重复发送、重复扣分、重复覆盖结果或重复生成不可辨认文件。
- [ ] running 状态必须有 stale reclaim 策略，不能重启后永久卡住。
- [ ] 取消、停止、失败、重试必须进入终态或可恢复状态，不能只写日志。

### B5. 后台任务管理接口

建议新增或扩展：

```text
GET /api/manage/system/background-tasks
GET /api/internal/background-tasks
```

完成条件：

- [ ] 超管可访问完整台账。
- [ ] 内部 health/metrics 可访问安全摘要。
- [ ] 普通教师访问返回 403 或既有无权语义。
- [ ] 学生访问返回 403、401 或既有无权语义。
- [ ] 接口响应不包含真实密钥、完整 prompt、学生隐私正文、真实 cookie/token。
- [ ] 接口响应按任务类型稳定排序，方便前端和监控比对。

## 数据安全要求

P11 的所有验证必须严格保护线上数据：

- [ ] 性能压测默认使用 `.codex-temp/p11-load-runtime` 下复制出的数据库，不能直接写本地真实 `data/classroom.db`。
- [ ] 后台任务恢复测试默认使用 `.codex-temp/p11-task-runtime` 下复制出的数据库，不能连接远程 `/lanshare/data/classroom.db`。
- [ ] 不得在远程生产站点执行 200 人写压测、批量材料导入、批量 AI 批改或 crash/restart 恢复测试。
- [ ] 若后续明确要求远程部署，必须先执行 `deployment/deploy_remote.ps1 -DryRun`，并确认部署不会删除或覆盖 `/lanshare/data`。
- [ ] 不得把真实线上任务状态强行改为 failed、queued、completed 来做测试。
- [ ] 若需要验证 stale reclaim，必须在复制库中构造测试任务。
- [ ] 压测和台账日志输出到 `.codex-temp/p11-artifacts`，不得提交包含凭据、cookie、token 或真实个人隐私的日志。
- [ ] 每次运行可写测试前后都要对复制库执行 `PRAGMA quick_check`，结果必须是 `ok`。

## 推荐实施顺序

### 第 0 步：建立基线

- [ ] 记录当前 `/api/internal/health` 输出。
- [ ] 记录当前 `/api/internal/metrics` 输出。
- [ ] 运行现有 100 人工具，保存结果到 `.codex-temp/p11-artifacts/baseline-100.json`。
- [ ] 记录当前后台任务来源表和内存队列清单。
- [ ] 搜索并登记当前所有 `asyncio.create_task(...)` 入口，按“可丢弃/需持久化”分类。

### 第 1 步：扩展 200 人 profile

- [ ] 为 `tools/full_stack_load_test.py` 增加或确认 `--students 200`、`--profile classroom-200`、`--json-output`、`--runtime-root` 能力。
- [ ] 保证压测复制数据库，而不是写真实库。
- [ ] 在结果中输出每个动作的 p50/p95/p99/max/error。
- [ ] 压测结束后拉取 `/api/internal/metrics` 快照并写入同一 artifacts 目录。

### 第 2 步：性能热点改造

- [ ] 从 200 人 profile 中选 p95 最高且请求量高的 3 到 5 个热接口。
- [ ] 对每个热接口标记事务边界、SQL 聚合、轮询频率和写入次数。
- [ ] 优先处理草稿保存、课堂状态写入、聊天刷新、教师提交列表、AI 回调这类热路径。
- [ ] 每个优化都必须保留前后指标对比，不得只写“已优化”。

### 第 3 步：建立后台任务注册与台账服务

- [ ] 新增统一任务类型注册。
- [ ] 接入 AI 批改、材料 AI 导入、邮件、博客爬虫、agent worker、行为写入队列的状态快照。
- [ ] 为消息中心 AI 回复和材料最终生成补齐可恢复状态来源。
- [ ] 新增后台任务台账接口和权限门。

### 第 4 步：任务恢复与重启验证

- [ ] 在复制库中构造 queued/running/stale/failed 任务。
- [ ] 启动服务，确认 queued 任务被恢复或显示为可恢复。
- [ ] 模拟 worker 停止和重启，确认 running stale 不会永久卡死。
- [ ] 验证 AI 批改旧回调不会覆盖停止或新一轮批改结果。
- [ ] 验证材料 AI 导入重启后不会丢记录，也不会重复生成不可追踪结果。

### 第 5 步：总验收

- [ ] 后端全量测试通过。
- [ ] 前端 typecheck/test/build 通过。
- [ ] P03 Playwright 业务回归通过。
- [ ] 200 人 profile 通过。
- [ ] 台账接口和权限测试通过。
- [ ] 复制库 quick_check 通过。
- [ ] P11 文档回填实际实施证据。

## 必须通过的测试命令

### 基础回归

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
npm run typecheck
npm test
npm run build
npm run test:e2e:p03
```

预期结果：

- 所有后端测试通过。
- TypeScript 无错误。
- Vitest 全部通过。
- Vite production build 成功。
- P03 Playwright 全部通过。
- 不新增 skip、xfail、only 或用 warning 掩盖失败。

### 性能 profile

建议新增：

```powershell
.\venv\Scripts\python.exe tools\full_stack_load_test.py --profile classroom-200 --students 200 --runtime-root .codex-temp\p11-load-runtime --json-output .codex-temp\p11-artifacts\classroom-200.json
```

预期结果：

- 复制库启动成功，health 显示数据库路径位于 `.codex-temp/p11-load-runtime`。
- 200 人 profile 完整执行。
- 总成功率 >= 99%。
- HTTP 5xx = 0。
- 各动作输出 p50/p95/p99/max。
- 核心热路径 p95 达到阶段阈值或相对基线有明确改善。
- 测试后复制库 `PRAGMA quick_check` 为 `ok`。

### 指标快照

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/internal/metrics" -UseBasicParsing
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/internal/health" -UseBasicParsing
```

预期结果：

- `/api/internal/metrics` 返回 `status=ok`。
- `runtime.http.top_routes` 包含热路径 p95。
- `runtime.http.recent_errors` 无新增 500。
- `runtime.websocket` 包含 active、received、sent、errors。
- `/api/internal/health` 返回行为写入队列、邮件 worker 状态，以及 P11 新增后台任务摘要。

### 后台任务台账

建议新增测试：

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_background_task_ledger tests.test_background_task_recovery tests.test_background_task_permissions
```

预期结果：

- 台账包含 AI 批改、材料 AI 导入、消息中心/AI 回复、邮件、博客爬虫、agent worker、行为写入队列。
- 每类任务都有 `queue_depth`、`running_count`、`failed_count`、`stale_count`、`active_worker_count`、`last_heartbeat_at`、`last_error`、`recoverable`。
- 超管可以读取完整台账。
- 普通教师和学生不能读取完整后台任务台账。
- 最近错误被截断并脱敏。
- queued/running/stale 任务在复制库重启验证中可恢复或进入明确失败状态。
- 不存在“任务只在内存 task 中，重启后业务状态永远卡住”的关键路径。

## 验收清单

P11 只有在以下条件全部满足时才能标记完成：

- [ ] 已完成热路径基线表。
- [ ] 已完成 200 人课堂 profile。
- [ ] 200 人 profile 输出 p50/p95/p99/max/error 结果。
- [ ] `/api/internal/metrics` 能观察热路径 p95 和 websocket 指标。
- [ ] 至少 3 个高风险热路径完成事务收缩或请求形状收束，并有前后指标对比。
- [ ] 草稿保存、聊天刷新、课堂状态写入、AI 回调、材料 AI 导入轮询不再无节制放大请求或写事务。
- [ ] 已建立后台任务统一注册和台账服务。
- [ ] 首期任务类型全部接入台账。
- [ ] 超管可查看完整台账，普通教师和学生不能越权查看。
- [ ] 重启恢复测试覆盖材料生成、材料 AI 导入、AI 批改回调关键状态。
- [ ] 关键任务不再只依赖内存 `asyncio.create_task`。
- [ ] 邮件、博客爬虫、agent worker 的既有持久化状态被统一纳入台账，而不是重复造一套不一致状态。
- [ ] 后端全量测试通过。
- [ ] 前端 typecheck/test/build 通过。
- [ ] P03 Playwright 回归通过。
- [ ] 复制库 quick_check 通过。
- [ ] 未触碰线上 `/lanshare/data`，未对本地真实 `data/classroom.db` 执行破坏性测试。

## 明确失败条件

出现以下任一情况，P11 不得验收：

- [ ] 把 SQLite 作为结论性瓶颈，但没有热路径 p95、锁等待、事务边界或请求形状证据。
- [ ] 200 人 profile 仍只跑 100 人或缺少真实课堂/作业/草稿/消息/提交路径。
- [ ] 压测直接写真实 `data/classroom.db` 或远程 `/lanshare/data/classroom.db`。
- [ ] 核心热路径 p95 劣化但仍宣称性能优化完成。
- [ ] 存在新增 HTTP 5xx 或 websocket 错误却未解释。
- [ ] 材料 AI 导入、AI 批改、材料生成等关键任务重启后丢状态或永久 running。
- [ ] 后台任务台账只返回静态假数据，不能反映真实队列和 worker 状态。
- [ ] 普通教师或学生可以查看后台任务全量台账。
- [ ] 最近错误或 artifacts 泄露 token、cookie、API key、真实密码、真实学生隐私。
- [ ] 为通过测试删除或弱化 P01 权限、P03 浏览器回归或现有业务校验。

## 跟踪清单

| 序号 | 条件 | 状态 | 证据 |
| --- | --- | --- | --- |
| 1 | 热路径基线表完成 | 待实施 |  |
| 2 | 现有 100 人基线结果归档 | 待实施 |  |
| 3 | 200 人课堂 profile 落地 | 待实施 |  |
| 4 | 200 人 profile 成功率和 p95 结果达标 | 待实施 |  |
| 5 | `/api/internal/metrics` 热路径 p95 可观察 | 待实施 |  |
| 6 | 写事务边界审计完成 | 待实施 |  |
| 7 | 草稿保存请求形状收束 | 待实施 |  |
| 8 | 聊天刷新/课堂状态写入压力收束 | 待实施 |  |
| 9 | AI 回调并发与旧回调保护验证 | 待实施 |  |
| 10 | 材料 AI 导入轮询和 worker 状态收束 | 待实施 |  |
| 11 | 后台任务类型注册完成 | 待实施 |  |
| 12 | 后台任务台账服务完成 | 待实施 |  |
| 13 | AI 批改接入台账 | 待实施 |  |
| 14 | 材料 AI 导入接入台账 | 待实施 |  |
| 15 | 材料最终生成接入台账 | 待实施 |  |
| 16 | 消息中心/AI 回复接入台账 | 待实施 |  |
| 17 | 邮件 worker 接入台账 | 待实施 |  |
| 18 | 博客爬虫接入台账 | 待实施 |  |
| 19 | agent worker 接入台账 | 待实施 |  |
| 20 | 行为/课堂状态写入队列接入台账 | 待实施 |  |
| 21 | 后台任务权限测试通过 | 待实施 |  |
| 22 | 重启恢复测试通过 | 待实施 |  |
| 23 | 后端全量测试通过 | 待实施 |  |
| 24 | 前端 typecheck/test/build 通过 | 待实施 |  |
| 25 | P03 Playwright 回归通过 | 待实施 |  |
| 26 | P11 实施证据回填 | 待实施 |  |

## 跟进记录

| 日期 | 负责人 | 进展 | 证据/命令 | 结论 |
| --- | --- | --- | --- | --- |
| 2026-06-04 | Codex | 建立 P11 性能指标门与后台任务台账目标 | `target/target01/P11/README.md` | 待实施 |
