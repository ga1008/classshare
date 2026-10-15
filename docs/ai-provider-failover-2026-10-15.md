# AI 厂商动态切换（熔断 + 同档位代偿）设计与施工记录

日期：2026-10-15　状态：已实施、已上线　真源：本文 + `docs/ai-and-agent-engineering-standard.md` 规则 A12

## 1. 背景

2026-10-15 上午 DeepSeek 服务端大面积超时（"unable to start processing your request within the 900-second timeout limit"）。当时的路由对每个业务档位只允许**一个**厂商，`_provider_call_with_retry` 只做同厂商重试，`AIExecutionBudget` 两次"未知结果"即判定预算耗尽，于是批改任务在 2 次 15 分钟的等待后以 `review_required` 收场，学生看不到成绩，教师收到一堆"待人工复核"。期间豆包完全健康却没有被使用。

## 2. 目标与边界

- **自动切换**：排位第一的厂商宕机（非本地网络问题）时，短时间内把**同类型**请求切到另一厂商的同档位模型；豆包宕机时同理切回 DeepSeek。
- **切换时机**：区分"厂商死了"和"我们的请求有问题"。前者立即切；后者绝不切（否则会把格式错误、超限图片等问题带到另一家重复付费）。
- **回切依据**：不是"过了多久"，而是"探测成功"。冷却只决定什么时候允许探测。
- **不做的事**：不做按负载的常态分流（文本溢出到豆包的付费开关 `AI_TEXT_SPILLOVER_ENABLED` 仍然关闭）；Agent 运行时（openai-agents，DeepSeek only）不在本次范围。

## 3. 设计

### 3.1 失败分类（`ai_assistant._classify_provider_failure`）

| 类别 | 判定 | 计入熔断 | 本次调用立即切换 | 计入生成预算 |
|---|---|---|---|---|
| `connection` | DNS/连接/网络错误（`httpx.NetworkError`、`APIConnectionError`） | 是 | 是 | 否 |
| `timeout` | `httpx.TimeoutException` / `APITimeoutError` | 是 | 是 | 是（请求可能已被处理） |
| `server` | HTTP 5xx | 是 | 是 | 否 |
| `throttled` | HTTP 429 | 是 | 否：先按 Retry-After 同厂商重试，耗尽后再切 | 否 |
| `rejected` | 其余 4xx（我们的请求被拒） | 否 | 否 | 否 |
| `other` | 截断/空内容/解析失败/我们自己的队列满 | 否 | 否 | 是 |

`_do_provider_call` 把厂商异常包成 `HTTPException(500)` 时现在带 `from e`，分类器沿 `__cause__` 看到原始异常；自家 502/503/500 没有 cause 时归为 `other`。

### 3.2 熔断器（`classroom_app/services/ai_provider_health.py`）

每个已启用厂商一只，进程内、线程锁保护、不落库：

- `closed` → `open`：窗口 `AI_PROVIDER_BREAKER_WINDOW_SECONDS`（300s）内累计 `AI_PROVIDER_BREAKER_FAILURE_THRESHOLD`（2）次宕机类失败。
- `open` → `half_open`：冷却 `AI_PROVIDER_BREAKER_COOLDOWN_SECONDS`（60s）到期。第 n 次开闸冷却为 60×2^(n−1)，上限 `AI_PROVIDER_BREAKER_MAX_COOLDOWN_SECONDS`（600s）。
- `half_open` 只放行**一个**探测（真实流量或后台探测）；成功 → `closed`（回切，计数清零）；失败 → `open`，冷却翻倍。
- **本地网络判定**：`connection` 类失败发生时，若对端厂商在同一窗口内也有 `connection` 失败，则不开闸并标记 `network_suspected`——两家都连不上，切换无意义。5xx/超时不受此规则影响（有响应说明网络通）。
- 手动 `trip` / `reset`（诊断页按钮）与自动开闸等价，手动开闸冷却取上限。

### 3.3 同档位映射（`ai_model_policy.failover_plan`）

| 原档位（厂商） | 代偿档位 |
|---|---|
| `text_fast`（deepseek-flash，无思考） | 豆包 lite，`low` |
| `text_deep` / `vision_grading_flash`（deepseek-flash，max） | 豆包 pro，`high`（max 是 DeepSeek 独有级别） |
| `vision_assessment_high` / `text_assessment_high` / 仲裁（豆包 pro，high） | deepseek-flash，`max`，thinking enabled，vision |
| `vision_pro_low` / `vision_edge_low`（豆包 pro/lite，low） | deepseek-flash，`low` |

代偿计划 `profile_id` 加 `:failover` 后缀（用量日志可按此筛选），`allowed_fallbacks` 清空，`api_style` 固定 chat_completions，输出预算沿用原档位；它只在调用时派生，**不会**冻结进 `ai_jobs.payload_json.execution_plan`，所以重试时仍以原厂商为准、原厂商恢复即回到原档位。

### 3.4 路由（`ai_assistant._build_model_routes` / `_call_ai_platform`）

1. `_build_model_routes` 在原路由后追加 `reason="failover"` 的对端路由（对端需已启用、支持图片/权威批改），再按熔断器健康度稳定排序：`closed` < `half_open`（探测位空闲） < `open`。开闸期间对端排在前面，原厂商仍留在列表末尾作为"两家都坏时的最后尝试"。
2. `_call_ai_platform` 一次只派发一条路由。宕机类失败 → 记入熔断器 → 立即换下一条；非宕机类 → 抛出，保留原有的有界同档位回退（`_bounded_fallback_routes`，现在只针对原路由，不带代偿路由）。
3. `_provider_call_with_retry` 持有 `_failover_peer_ready` 上下文变量：只要上层还有备用路由，宕机类错误不再同厂商重试（避免 2×600s 的等待）；429 仍按 Retry-After 重试。每次成功/失败都汇报熔断器；半开状态下自动占用/释放探测位。
4. 流式聊天只为排序后的第一条路由预留并发（避免恢复"文本按容量溢出到豆包"），首个 token 之前失败的既有回退逻辑自然落到对端。
5. `AIExecutionBudget.finish(billable=False)`：`connection`/`server`/`throttled`/`rejected` 记为 `rejected`，不占用"最多两次可能计费的生成"的名额，因此一次宕机不再把任务直接推进人工复核。

### 3.5 探测与回切（`_provider_probe_loop`）

AI 服务生命周期内常驻，每 `AI_PROVIDER_PROBE_INTERVAL_SECONDS`（20s）检查一次：对冷却到期的厂商用其快文本模型发一条 `max_tokens=1` 的 ping（成本可忽略；仅在有熔断时发生）。成功或得到 4xx（厂商可达）→ 回切；宕机类失败 → 继续开闸并翻倍冷却。真实流量同样可以作为探测。

### 3.6 可观测性与人工干预

- AI 服务 `GET /api/internal/health` 新增 `provider_health`；`GET /api/internal/provider-health`、`POST /api/internal/provider-health/{provider}/{trip|reset}`（仅 compose 网络）。
- 主应用超管代理：`GET /api/manage/system/ai-providers`、`POST /api/manage/system/ai-providers/{provider}/{trip|reset}`。
- 超管「压测与诊断」页新增「AI 厂商可用性与动态切换」卡片：状态、窗口内失败、熔断次数、切出/代偿计数、最近错误、距探测秒数，以及手动熔断/恢复按钮（LQ surface + 既有 `dg-*` 材质，领域 CSS 只管布局）。
- 开闸与回切事件写日志 `[AI HEALTH]`，并以站内通知发给全部超管（`message_center` `ai_feedback` 类别，链接到诊断页）。
- 用量日志：代偿请求的 `execution_plan.profile_id` 带 `:failover`，`route_reason=failover`，回调元数据带 `failover_from`。

## 4. 配置（`docker.env.example`）

```
AI_PROVIDER_FAILOVER_ENABLED=true
AI_PROVIDER_BREAKER_FAILURE_THRESHOLD=2
AI_PROVIDER_BREAKER_WINDOW_SECONDS=300
AI_PROVIDER_BREAKER_COOLDOWN_SECONDS=60
AI_PROVIDER_BREAKER_MAX_COOLDOWN_SECONDS=600
AI_PROVIDER_PROBE_INTERVAL_SECONDS=20
```

生产 `docker.env` 未设置时取以上默认值（开关默认开）。关闭只需 `AI_PROVIDER_FAILOVER_ENABLED=false` 并重启 `ai` 容器。

## 5. 验证

- 单元：`tests/test_ai_provider_health.py`（开闸/窗口/本地网络抑制/半开探测/冷却翻倍/手动控制）、`tests/test_ai_provider_failover.py`（档位映射；5xx 立即切换并在阈值后开闸、开闸后直达对端、回切；豆包宕机时期末卷切到 deepseek-flash max；两家连不上不开闸；格式错误不跨厂商；开关关闭只剩单厂商路由）。
- 既有 `tests/test_ai_execution_profiles.py` 中"持续 429"的用例改为：同厂商重试耗尽后允许一次对端尝试。
- 上线核验记录见部署备忘与 `docs/homework-exam-improvement-plan-2026-10-13.md` §15 的 2026-10-15 条目。

## 6. 本次同步处理的宕机积压

`tools/regrade_backlog.py`（容器内运行，默认 dry-run）列出并重新入队 `grading_failed`/`grading_review`/卡在 `grading`/`submitted` 无分数的真实提交；草稿不批改、不触碰；被"错误提交"申诉挂起的提交跳过。
