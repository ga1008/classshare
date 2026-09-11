# LanShare AI 与 Agent 工程规范

> 状态：长期维护文档（living document）。首版 2026-09-11，基于 `dev` 分支 `04f0e0ab`。
> 面向对象：后续改进 AI 能力、Agent 能力或任何调用模型的业务功能的工程师与 AI 助手。
> 定位：本文是**规范与索引**，不是实现记录。具体设计与证据以第 10 节「真源索引」列出的文档、代码和测试为准；两者冲突时，以代码与测试为准，并回来修订本文。

---

## 0. 怎么使用本文

1. **改任何调用模型的代码之前**，先读第 1、3 节；改 Agent 先读第 1、4 节；新增审批/确认类流程读第 5 节。
2. 每个小节末尾的「检查清单」是提交前的硬性自检；无法满足的条目必须在 PR / 实施记录中明确写出原因。
3. 本文只记录**稳定的约定、边界和入口**。具体参数值（并发数、限额、模型 ID）会漂移，以 `docker.env.example` 和代码默认值为准，本文只说明它们的含义与调节方式。
4. 修改了本文所描述的机制（新增 task_type、新增能力层、改变授权链路）时，**必须同步更新本文**对应小节与第 10 节索引。

---

## 1. 总体架构

LanShare 有两条互相独立、但共用同一批模型厂商的智能链路：

| 链路 | 用途 | 代表入口 | 执行主体 |
|---|---|---|---|
| **AI 服务链路** | 平台功能内嵌的模型调用：批改、出题、教案/考核计划/评学表生成与导入、课堂/全局聊天、错题归集、职业规划、公文识别、博客改写等 | 主应用各 service → `ai_client` / `ai_gateway_post` → AI 微服务 `ai_assistant.py` | AI 微服务（容器 `ai`，端口 8001）直连 DeepSeek / 火山方舟 |
| **Agent 链路（数字分身）** | 用户以本人身份委托一个多步任务，由官方 DSH（deepseek-harness）理解、规划并调用平台工具执行 | AI 工作区任务中心 → `/api/agent-tasks` → `agent-worker` → DSH 隔离容器 → `/api/agent-bridge/mcp` | 每任务一个隔离 DSH runner；平台通过 MCP 工具与模型网关提供能力 |

```
┌────────────── 主应用 app (FastAPI, 8000) ──────────────┐
│  业务 service ──► ai_gateway_post / ai_client ──HTTP──►│──► ai_assistant.py (8001)
│        │                                                │        │  ai_model_policy 选档
│        └─ 持久任务账本 ai_jobs (PostgreSQL)  ◄──────────│────────┘  durable worker 领取
│                                                         │
│  /api/agent-tasks ──► agent_tasks 队列 ──► agent-worker │
│  /api/agent-bridge/mcp  ◄── MCP 工具调用 ───────────────│──┐
│  /api/agent-model/*     ◄── 模型请求（凭据在网关侧）────│──┤  DSH runner（隔离容器，--network none，
└─────────────────────────────────────────────────────────┘  └─ 经 launcher 转发的 gateway socket）
```

两条链路的共同底座：

- **模型厂商**：DeepSeek（文本 + `deepseek-flash` 多模态）与火山方舟豆包（多模态、仲裁、期中期末批改）。其他厂商（GLM、Kimi、Qwen、SiliconFlow）保留配置但**不进入默认路由**。
- **单一策略真源**：`classroom_app/services/ai_model_policy.py`。任何"哪个业务用哪个模型、什么思考强度、多大输出"的决定都在这里，其他地方只消费执行计划。
- **平台知识注入**：`classroom_app/services/platform_knowledge_service.py` 为聊天与 Agent 提供平台概览、路由表与用户画像。
- **用量与预算**：`ai_usage_log` 表（应用侧）+ `logs/ai_usage.jsonl`（AI 服务侧真实厂商用量）+ 每课堂周预算 + 仲裁日配额。

---

## 2. 术语

| 术语 | 含义 |
|---|---|
| task_type | AI 服务的任务类别（`fast_text_response` / `deep_text_reasoning` / `vision_ocr` / `vision_interactive` / `document_multimodal_understanding` / `multimodal_grading` / `multimodal_adjudication` 等，见 `ai_model_policy.TASK_POLICIES`），决定能力组与允许厂商 |
| task_label | 调用方自报的业务标签（如 `grading:submission:123`、`lesson_plan_generate`），用于日志、用量归因和关键任务识别 |
| business_context | 服务端可信的业务事实（`assessment_kind`、`source_feature`、`class_offering_id`、`expected_question_count` 等）。**只能来自数据库/服务端，不能来自用户文本或模型输出** |
| 执行计划 execution_plan | `resolve_execution_plan` 产出的冻结计划（profile、provider、model、thinking、effort、max_output_tokens、policy_version）；持久任务入队时冻结，重放不重算 |
| 档位 profile | 如 `vision_grading_flash`、`vision_assessment_high`、`text_assessment_high`、`vision_edge_low`、`text_fast`、`text_deep`；一个档位 = 厂商 + 模型 + 思考参数 + 输出上限 + 允许回退 |
| 仲裁 adjudication | 主评分出现风险信号时用豆包 pro 做的第二次评分；只对 `AI_GRADING_ADJUDICATION_KINDS`（默认 midterm,final）生效 |
| 持久任务 durable job | 记录在 PostgreSQL `ai_jobs` 账本、带租约与幂等的模型任务（批改、出题、文档生成/导入） |
| Agent 任务 | 用户在任务中心提交的一次委托，对应 `agent_tasks` 一行 + 事件 + 尝试（attempt）+ 委托凭据 |
| 能力 capability | Agent 可调用的一个平台动作；分 read / write / request / route.* / secure_input / user_confirmation / file transport 七层 |
| 回执 receipt | 平台对一次 Agent 操作的持久记录；分"事务写回执"（业务行与账本同事务）和"观察回执"（只证明看到了 HTTP 响应） |
| 本人确认 user_confirmation | 模型只能提出提案，由当前登录用户在平台确认模态核对快照后执行的操作 |

---

## 3. AI 调用规范

### 3.1 调用分层（谁可以调什么）

| 层 | 允许做的事 | 禁止做的事 |
|---|---|---|
| 业务 service（主应用） | 组装提示词与 `business_context`，通过 `ai_gateway_post(...)`（优先）或 `ai_client.post("/api/ai/chat" …)` 调 AI 服务；解析结果并做后置校验 | 直接调厂商 SDK / HTTP；在代码里写死模型 ID；把模型输出直接当业务事实写库而不校验 |
| AI 网关 `ai_gateway_service.ai_gateway_post` | 进程内优先级队列（P0/P1/P2）、周预算检查（P2 超预算抛 `AIUsageBudgetError`）、写 `ai_usage_log` | — |
| AI 微服务 `ai_assistant.py` | 归一化 task_type、按策略选档、厂商调用/重试/回退/超时、结构化输出校验与修复、仲裁、持久任务 worker、用量日志 | 依赖用户文本决定档位；接受调用方指定的厂商/模型（除策略允许的白名单开关） |
| 策略 `ai_model_policy.py` | 唯一的选档逻辑 | 读取提示词/附件内容 |

**规则 A1**：新的业务调用优先走 `ai_gateway_post`，并传齐 `task_type`、`priority`、`class_offering_id`/`teacher_id`/`student_id`、`source_ref`。只有交互式流式聊天等已有链路可继续用 `ai_client` 直连，但仍必须传 `task_type` 与 `task_label`。

**规则 A2**：`priority` 语义固定：P0 = 用户正在等待的交互（聊天、即时问答）；P1 = 教师发起的业务生成（默认）；P2 = 后台/批量/可延迟（博客采集、定时汇总）。P2 受课堂周预算约束，会被延期。

**规则 A3**：`task_type` 必须从 `ai_model_policy` 的常量或别名表中选择；新增业务若现有类型语义不匹配，先在 `TASK_POLICIES` 增加类型并补测试，再使用。**不要**用 `model_capability` 旧字段表达意图。

**规则 A4**：`task_label` 使用 `<功能>:<对象>:<id>` 形式，前缀稳定（如 `grading:`、`exam_generation:`、`lesson_plan_generate`）。关键任务识别（`_is_critical_task`）与用量归因依赖它。

### 3.2 模型路由矩阵（当前生效）

| 业务事实（服务端可信） | 档位 | 厂商 / 模型 | 思考 | 备注 |
|---|---|---|---|---|
| 批改，`assessment_kind ∈ {midterm, final}`，含图/PDF | `vision_assessment_high` | 豆包 2.1 pro | enabled, high, 16k（按题量升档） | 可仲裁 |
| 批改，其余（homework / 未分类 / legacy_unknown / 破境试炼），含图/PDF | `vision_grading_flash` | deepseek-flash | enabled, max, 32k | 允许有界回退到豆包 pro；不仲裁，只标 `needs_review` |
| 批改，纯文本，期中/期末 | `text_assessment_high` | 豆包 pro | enabled, high | |
| 批改，纯文本，其余 | flash 文本档 | deepseek-flash | enabled, max | |
| 仲裁 `multimodal_adjudication` | `vision_assessment_high` | 豆包 pro | high | 日限 40 / 课堂 10（env） |
| 出题、文档导入/生成的多模态 | `vision_pro_low` / `document` 档 | 豆包 pro, low | | 本轮未动 |
| 聊天看图、OCR、验证码、公开 @助教 | `vision_edge_low` | 豆包 lite, low | | |
| 普通文本快回复 | `text_fast` | deepseek-flash | | |
| 深度文本推理（教案、错题归因、材料精修等） | `text_deep` | deepseek（当前均映射 flash） | effort max 仅关键任务 | |

**规则 A5**：矩阵的输入只能是数据库中的业务字段（`assignments.assessment_kind`、`source_feature`、操作类型、实际输入模态）。标题里写"期末"、提示词里写"请用最好的模型"都**不能**改变档位。

**规则 A6**：多模态任务由 `_messages_contain_visual` 自动升级并强制走支持视觉的厂商；文本任务在 DeepSeek 满载时可按 `AI_TEXT_SPILLOVER_ENABLED` 溢出。批改与文本路由的厂商由业务档位决定，`AI_*_PRIORITY` 顺序**不能**屏蔽计划里的厂商。

**规则 A7**：改路由必须同时更新 `docker.env.example` 的开关与注释，并保证一键回退开关可用（当前：`AI_GRADING_STANDARD_PROVIDER=volcengine`、`AI_TEXT_ASSESSMENT_PROVIDER=deepseek`，改 env 重启 `ai` 容器即可，不用重部署）。

**规则 A8**：`policy_version` 是已入队持久任务的兼容锚点。改它会让所有排队 payload 报错；新增档位要保留旧快照的 `_plan_from_legacy_snapshot` 兼容。

### 3.3 结构化输出与校验

- 需要机器消费的输出必须 `response_format="json"` 并给出明确 schema 说明；解析用 `_candidate_json_payloads` 一类的容错解析，**不得**用正则从自由文本里抠字段。
- 批改结果走 `_validate_grading_result_for_job` 硬校验（题量覆盖、分数范围、0 分必须有扣分点等），前置 `_soften_grading_result_format` 软规范化（按句截断超长文本、补默认 evaluation）。硬校验失败最多一次修复重试（`GRADING_RESULT_MAX_ATTEMPTS = 2`）。
- 输出规模档 `structured_output_size_tier(schema, question_count)`：题量只信任教师试卷快照或服务端写入的 `expected_question_count`；超过上限**预先拒绝**，不切割附件、不自动加大上限重跑。
- 出现 `length` / `incomplete` 时停止写回，进入人工复核；不能把截断文本当成绩。
- AI 不得覆盖确定性字段（课程名、班级、教师、教务同步的学分学时等）；导入是还原不是创作（详见 `docs/document-feature-ai-implementation-guide.md`）。

### 3.4 持久任务（durable jobs）

批改、出题、教案/考核计划/评学表生成与导入必须走 `ai_durable_job_service`，遵守 `docs/ai-durable-job-architecture-2026-07-12.md` 的 8 条不变量，核心是：

1. 业务行与任务行同事务创建；请求返回前任务已持久化。
2. worker 短事务领取（PG `FOR UPDATE SKIP LOCKED`，SQLite 条件更新），模型调用期间不持连接、不持锁；租约令牌拦截迟到结果。
3. 结果先写 `ai_job_results` 再应用到业务表；`result_ready` 重放收口。
4. 批改结果写不可变 `submission_grade_revisions`；重批失败保留旧分；首批耗尽重试进入教师复核，不把失败文本当成绩。
5. 取消即失效租约；输入文件带 SHA-256，篡改则拒绝调用。

**何时用同步调用而不是持久任务**：用户在等的交互（聊天、建议、轻量整理），且失败可直接提示重试、无需成绩类落库。其余一律持久任务。

### 3.5 超时、重试、并发

| 参数 | 含义 | 调节位置 |
|---|---|---|
| `AI_HTTP_TIMEOUT_INTERACTIVE` / `AI_HTTP_TIMEOUT_DEEP` | 厂商客户端超时分级（交互 180s / 深度 600s） | env |
| `AI_PROVIDER_HTTP_MAX_ATTEMPTS`、`AI_PROVIDER_TIMEOUT_MAX_ATTEMPTS` | 429/5xx 重试上限与超时类重试上限（超时类必须远小于前者，防重试风暴） | env |
| `GLOBAL_AI_CONCURRENCY`、`DEEPSEEK_MAX_CONCURRENT_REQUESTS`、`VOLCENGINE_MAX_CONCURRENT_REQUESTS`、`AI_VOLCENGINE_HIGH_MAX_CONCURRENT_REQUESTS` | 全局与厂商并发；high 档单独限并发 | env |
| `AI_JOB_WORKER_CONCURRENCY`、`AI_LOCAL_JOB_WORKER_CONCURRENCY` | AI 服务 worker（批改/出题）与主服务本地 worker（文档）并发 | env |
| `LANSHARE_AI_GATEWAY_MAX_CONCURRENT` | 应用侧网关并发 | env |

**规则 A9**：生产是 2 核 4GB。新增常驻并发或轮询前先给预算并实测；主应用→AI 服务的超时要 ≥ AI 服务内部超时（否则应用先超时、AI 仍在跑、费用照付）。

**规则 A10**：部署脚本**不覆盖**服务器上的 `docker.env`；`docker.env.example` 里调整的值不会自动生效，需要在 `/lanshare/docker.env` 手动追加后 `docker compose up -d`。

### 3.6 用量、预算与费用

- **真源**：生产厂商真实用量在 AI 容器 `logs/ai_usage.jsonl`（含 provider、model、tokens、估算费用、task_label、fallback 标记）；应用侧 `ai_usage_log` 只是估算 token 与业务归因。
- `_estimate_provider_cost_cny` 价表必须与厂商官方价一致（DeepSeek flash 2/0.04/8，pro 9/0.3/27，非高峰 ×0.5；2026-09-11 校正）。改价表要在实施记录中给出来源链接。
- 每课堂周预算：`ai_usage_budget_service`（`load/save_offering_ai_budget_config`、`should_defer_low_priority_ai_task`、超额通知超管）；仲裁日配额：`reserve_grading_review`（全局/课堂两级原子预约，重放不重复占额）。
- 破境试炼出题每日 `STAGE_EXAM_DAILY_LIMIT`。

**规则 A11**：任何会产生第二次付费调用的机制（修复重试、仲裁、回退）都必须：有次数上限、有配额、在日志中可辨识（`task_label` 后缀 `:fallback` / metadata `fallback_from`），且成功后重放零额外调用。

### 3.7 提示词规范

1. **系统提示词**由服务端拼装：平台概览块（`build_platform_overview_block`）+ 用户画像块（`build_user_knowledge_block`）+ 功能指令。新增大功能或路由时更新 `PLATFORM_ROUTES` / `PLATFORM_FEATURES_TEXT`。
2. **可信事实进 business_context，不进提示词**：档位、题量、课堂 ID 等由服务端字段传递，提示词只承载任务说明。
3. **禁止**把密钥、内部路径、数据库 ID、文件 URL 写进提示词或让模型输出到可见文档。
4. 非聊天类的教师自由输入使用**提示词池**（`docs/prompt-pool-guidelines.md`）：`data-prompt-pool-key` 按功能分区、默认勾选共享、只在成功后记录、不存身份。
5. 聊天 Markdown 输出经 `normalizeAIChatMarkdownText` 归一化；改 `markdown_runtime` / `tailwind_app` 必须 bump `static/vendor/manifest.json` 版本。
6. 批改系统提示的规则条目（例如"答题框空只给截图分"）是**评分政策**，改动需在 `docs/grading-model-routing-plan-*.md` 类实施记录中说明并跑回放脚本验证。

### 3.8 多模态与附件

- 图片 ≤ `AI_GRADING_IMAGE_MAX_MB`（10MB）、PDF ≤ 20 页/20MB、单次 ≤ 50 文件；DeepSeek 视觉 ≥15 张图时单边压到 4096px（`ai_grading_attachments`）。
- 扫描 PDF 不是"纯文本"；文字抽取失败不能伪装成文本成功。有充足文本且版面无关时保留文本优先路径。
- 一次低档 OCR 的结果不能永久替代正式考核的视觉证据。
- 聊天路径也会携带 PDF 渲染页与文档内嵌图（`_embedded_data_url`），注意成本。

### 3.9 新增一个 AI 功能的检查清单

- [ ] 选定 `task_type`（必要时新增策略项 + 测试）与稳定 `task_label` 前缀
- [ ] `business_context` 只含服务端可信字段；无用户文本
- [ ] 走 `ai_gateway_post`，传 priority 与归因 ID；需要落库的成绩/文档走持久任务
- [ ] 输出 JSON schema 与后置校验；失败路径不写脏数据、有用户可见提示
- [ ] 费用：估算单次成本，确认是否受周预算/配额约束；重试有上限
- [ ] 提示词：平台知识注入 + 提示词池（如有自由输入）；无密钥/路径泄露
- [ ] 前端：不新增页面私有 tooltip/历史 UI，复用说明浮窗与提示词池组件
- [ ] 测试：不调付费模型（内存 transport / stub），覆盖档位选择、校验失败、重试上限、恢复零额外调用
- [ ] 文档：在 `docs/` 写实施记录（见第 9 节），更新本文第 10 节索引（若新增机制）
- [ ] 部署：新增 env 进 `docker.env.example`，代码默认值即可用；改 `classroom_app/db/*.py` 触发原生 PG 演练门禁（见第 7 节）

---

## 4. Agent（数字分身）规范

### 4.1 架构与生命周期

1. **入口**：教师/学生在 AI 工作区任务中心提交任务与附件（`static/js/ai_workspace_widget.js`）→ `POST /api/agent-tasks`（`classroom_app/routers/agent_tasks.py`）。
2. **队列**：`agent_tasks` 表；`agent-worker` 容器公平领取（PG `SKIP LOCKED`）。全局/worker 并发由 `AGENT_TASK_GLOBAL_CONCURRENCY` / `AGENT_TASK_WORKER_CONCURRENCY` 控制，生产默认 1（同一主体同时一个任务）。
3. **运行**：`agent_dsh_task_service.run_dsh_task` 创建尝试（attempt + fencing token），签发**任务委托凭据**（tools 与 model 两种用途、独立 scope 与 TTL），经 `agent_runtime/launcher_client` 请求宿主 launcher（`tools/agent_dsh_launcher.py`，systemd 服务）启动隔离 DSH 容器（`deployment/dsh/`，固定镜像 + profile digest 校验，`--network none`，只读根文件系统，资源配额，独立 workspace）。
4. **工具**：runner 内通过 gateway socket 只能到达 `/api/agent-bridge/*` 与 `/api/agent-model/*`。MCP 工具面见 4.4。模型请求经 `agent_model_gateway`：真实 API key 不进 runner，请求按任务计数/限流（`MAX_REQUESTS_PER_TASK` 等）。
5. **结果**：事件流（SSE `/{task_id}/stream`）、产物、`proposed_actions` 提案、平台请求回执；终态后可追问（follow-up）、重试、按提案预览/执行（本人确认）。
6. **收尾**：租约对账（reconcile）决定不确定结果；容器由 launcher 回收；历史可读。

### 4.2 身份与授权铁律

- **R1 权限 = 本人实时权限**。Agent 执行任何平台操作时走：任务委托凭据 → 用户实时登录会话 → 进程内 ASGI 调用目标路由，由路由自身依赖鉴权。用户在网页上做不到的，Agent 也做不到；反之亦然。撤权/注销后等待中的操作立即失效。
- **R2 模型文本不能提升权限**。硬排除、破坏性判定、确认要求全部由路由元数据和服务端规则决定，提示词只能帮助使用工具。
- **R3 凭据不进 runner**。模型 key 留在网关；任务凭据形如 `lsagt_…`，日志与事件经 `redact_runtime_value` 脱敏；密码等安全输入只能由用户在确认界面填写，不进提案/任务/回执。
- **R4 三级确认**：
  - 普通可逆读写：继承用户提交任务时的授权，不逐步确认。
  - 破坏性/不可逆/外发/公开（DELETE，或命名含 delete、remove、purge、reset、clear、revoke、close-out、merge、publish、archive、disable、force、bulk、batch、transfer、import、sync、approve、reject 等）：模型只能提 `platform_route_request` / 领域提案，由本人在确认模态核对平台生成的快照、勾选提示、填写说明后执行（`agent_route_confirmation_service`、`agent_business_confirmation_service`）。
  - 安全输入（密码、密钥、邮箱配置）：`secure_input` 层，仅本人在界面填写。
- **R5 超管可代批但记在真实账号下**，且必须接受明确提示并说明。

### 4.3 能力分层

| 层 | 注册位置 | 语义 | 回执类型 |
|---|---|---|---|
| read | `agent_platform_registry.READ_OPERATIONS` | 已审核只读接口，参数白名单 | 无副作用 |
| write | `agent_action_registry` / `agent_platform_write_service` | 已审核事务写；`operation_id` 幂等 | 事务写回执（业务行与账本同事务） |
| request | `agent_platform_request_registry` + `agent_platform_request_*.py` | 已审核的普通 HTTP 请求（含表单/上传等 JSON 之外形态） | 观察回执（`observed_http_result`），不确定时 uncertain，不自动重试 |
| route.* | `agent_platform_route_capability` | 由挂载路由 + OpenAPI 自动推导的全站 JSON 路由；键 `route.<sha256(method\npath)[:20]>` | 观察回执，`verified_business=false` |
| secure_input | `agent_secure_account_actions` | 密码/凭据类，仅本人 | — |
| user_confirmation | `agent_user_confirmation_actions` + 各 `*_confirmation_service` | 成绩公布、签章审批、删除空班/课程、合班、破坏性路由 | 本人确认回执 |
| file transports | `agent_file_capability_catalog`（`platform_file` / `platform_download`） | 按正常下载权限取文本抽取或复制原始字节到任务 inputs | 含 SHA-256 |

**规则 R6**：已审核适配优先。路由若已有 read/request 审核能力，`route.*` 调用返回 409 并指出应使用的能力名，避免绕过更强的响应契约。

**规则 R7**：观察回执不等于业务完成。异步领域作业（如材料生成）排队成功不等于生成完成；模型必须用 `platform_request_status` / `platform_task_context` 核对，不能换 `operation_id` 重放。

### 4.4 MCP 工具面（`/api/agent-bridge/mcp`）

| 工具 | 作用 | 边界 |
|---|---|---|
| `platform_overview` | 当前身份与平台概览 | 只读 |
| `platform_capabilities` | 能力索引；`query` 检索，`keys`（1–8 个）取完整参数 | 目录可见不等于有权 |
| `platform_read` | 已审核只读接口 | 参数白名单、≤2MB、15s |
| `platform_write` | 已审核事务写 | 同一操作重试须复用 `operation_id` |
| `platform_request` | 审核请求能力或 `route.*` | 破坏性 403 → 提案；JSON 体 ≤64KB |
| `platform_request_status` / `platform_task_context` | 读取本任务/父任务的回执与产物 | 不重发请求 |
| `platform_query_catalog` / `platform_query` | 教师命名统计查询（白名单视图） | 不支持任意 SQL |
| `platform_file` / `platform_download` | 有界文本抽取 / 原始字节复制到任务 inputs | 复核身份与来源 |
| `children/*`、`questions/*`（HTTP） | 子任务准入、向用户提问 | 子代理/工作流插件**当前禁用** |
| `/query` `/schema` `/file` `/web`（旧桥接） | 只读 SQL（单条 SELECT、≤200 行、敏感表拒绝、列脱敏）、文件、联网（SSRF 防护） | 按任务主体过滤 |

### 4.5 新增/修改业务路由时对 Agent 的要求

1. **默认无需写适配**：新 JSON 路由挂载后即被 `route.*` 层发现并按元数据分类。
2. **必须做**：
   - 路由不可逆但命名不含破坏性关键词 → 改为 DELETE 语义或把关键词加入 `DESTRUCTIVE_PATTERN`。
   - 路由属于会话/凭据/控制面/安全输入 → 确认落在硬排除集合（路径前缀或 tag），不要用普通 JSON 路由承载。
   - 表单/多部分上传、下载、HTML 页面 → 需要 Agent 使用时写审核适配（request 层），不会自动可达。
3. **需要更强契约时**再写 read/write 审核能力（带 `verified_business` 回执）。
4. 修改已被审核适配钉住的 router 源码后，重新审核 `docs/agent-capability-reviewed.json` 的证据哈希（不能只刷哈希）。
5. 新增大功能同步更新 `platform_knowledge_service.PLATFORM_ROUTES`，让模型知道入口。
6. 跑 `python tools/agent_capability_inventory.py --check` 核对能力台账未过期。

### 4.6 运行时与部署

- 版本固定：官方 `@deepseek-ai/dsh 0.1.5-rc.1`，Node 镜像按 digest 固定，`deployment/dsh/release.json` 记录镜像 ID 与 profile digest。启动前 launcher 用 `--evidence` 探针比对版本与 digest。
- 运行时开关：`AGENT_TASKS_ENABLED`、`AGENT_DSH_ENABLED`、`AGENT_DSH_LAUNCHER_SOCKET`、`AGENT_TASK_MAX_RUNTIME_SECONDS`、`AGENT_MODEL_DEFAULT`、`AGENT_MODEL_SEARCH_MODEL`、`AGENT_MODEL_ALLOWED_BASE_URLS`（模型 base URL 白名单，管理员配置仍受其约束）。
- Agent 模型 key 由超管在平台内配置（`agent_key_service`，加密存储），不是 `docker.env` 的 `DEEPSEEK_API_KEY`。
- 子代理与工作流插件禁用（取消后远端工作停止尚无完整证明，见 `docs/agent-dsh-subagent-enablement-2026-09-10.md`）；启用需重建镜像并重新验证 digest。
- 旧 deepseek-tui 已退役，不得重新引入其 HTTP 协议适配；`agent-improvement-goals.md` 仅作历史参考。

### 4.7 轻量聊天查询（G9）边界

`chat_platform_query_service`：教师在聊天里问"几个人没交作业"类问题时，正则粗筛 → 快速 AI 从白名单视图规划最多 2 轮 `platform_query` → 以当前教师身份执行 → 结果拼进提示词。任何环节失败降级为普通对话。学生角色不触发。**不要**把这条链路扩展成任意 SQL 或写操作；复杂需求引导到 Agent 任务。

### 4.8 修改 Agent 的检查清单

- [ ] 授权链路未变：仍是 委托凭据 → 实时会话 → 路由自身鉴权；新增身份来源必须绑定 nonce 并重新核对权限指纹
- [ ] 新工具/能力有注册表条目、参数校验、响应上限、超时、回执类型
- [ ] 破坏性判定与确认层未被绕过；模型参数不含本人声明字段
- [ ] 幂等：`operation_id` 语义、不确定结果不自动重试、重放读既有回执
- [ ] 日志/事件脱敏（`redact_runtime_value`）；凭据不进 runner、不进产物
- [ ] 测试：`tests/test_agent_*`（sqlite）+ 涉及锁/并发/时间类型的必须补 `*_postgres` 原生用例；浏览器确认模态用 `tests/e2e/components/agent-user-confirmation.spec.ts`
- [ ] 能力台账 `--check` 通过；`docs/agent-capability-reviewed.json` 证据哈希与实现一致
- [ ] 若动了 runner 镜像/profile/launcher：重建镜像、更新 `release.json`、重做隔离端到端与生产只读验收
- [ ] 实施记录 + 证据 JSON 落在 `docs/`（第 9 节）

---

## 5. 审批与确认类流程

平台有两套"人来拍板"的机制，职责不同，不要混用：

| 机制 | 适用 | 真源 |
|---|---|---|
| **通用审批流** `approval_workflow_service`（`approval_requests` 三表 + 类型注册表 `ApprovalRequestType`） | 用户之间的业务申请：学生申请撤回重做、（未来）请假、补交、加入课堂等；有审批人、消息+邮件通知、48h 提醒/7 天过期、`/api/approvals` 与前端 `approval_workflow.js` | `docs/approval-workflow-plan-2026-09-11.md` |
| **Agent 本人确认** `agent_*_confirmation_service` + `user_confirmation` 动作 | Agent 提案需要**本人**核对快照后执行的操作；没有第三方审批人 | `docs/agent-human-business-confirmation-2026-09-10.md` |
| **签名申请流** `signature_service` | 材料签名点的使用授权（设计参照，保持独立） | 记忆 `signature-point-workflow` |

新增"申请—审批"类需求：注册一个 `ApprovalRequestType`（prepare / on_approve / on_reject / on_cancel / detail / inbox_link），必要时加前端 `DETAIL_RENDERERS` / `DECISION_FORMS`；不要再建新表和新通知分类。若 Agent 需要代用户发起申请，走 `route.*` 或审核 request 能力调用 `/api/approvals`；审批决定属于破坏性关键词（approve/reject），只能由本人确认。

---

## 6. 测试与验证规范

```bash
# 单测（内存 SQLite；-t . 不可省略）
venv/Scripts/python.exe -m unittest discover -s tests -t . -p "test_ai_*.py"
venv/Scripts/python.exe -m unittest discover -s tests -t . -p "test_agent_*.py"
venv/Scripts/python.exe -m unittest tests.test_approval_workflow -q
```

- **不调付费模型**：AI 单测用内存 transport / stub 捕获真实 SDK 请求；Agent 单测用合成 MCP 与模型。需要真模型验证时用 `tools/grading_bench/replay_grading_pipeline.py`（走完整批改链路）或 `grading_model_bench.py`（直连横评），并把费用与样本数写入记录。
- **必须在真 PostgreSQL 验证**的场景：锁与并发（`pg_blocking_pids`）、`SKIP LOCKED` 领取、时间类型写入 JSON 回执、`ON CONFLICT`、`DISTINCT + ORDER BY`。相关用例以 `*_postgres.py` 命名，未设置专用集群环境变量时跳过而非视为通过。
- **浏览器验证**：P03 harness（`DB_ENGINE=sqlite`，Playwright）截图确认模态、任务卡片、审批抽屉；临时 spec 用完删除，截图留 `.codex-temp/ui-audit/`。
- **不得宣称**：合成/隔离/本地通过 ≠ 生产验收；观察回执 ≠ 业务成功；"模型说完成" ≠ 落地。记录里要写清验证层级。

---

## 7. 部署与运维要点

- **部署流程**见记忆 `deploy-workflow` 与 `DEVELOPMENT.md` §5：从 LF 冻结 worktree 打包；`-QuiesceForMigration` + 原生 PG 演练报告；报告按 `classroom_app/db/*.py` 的 SHA-256 绑定——因此**运行时建表的新功能把 DDL 放在 `services/*_schema.py`**（engine-aware `CREATE TABLE IF NOT EXISTS`，操作前 ensure，并对"进程已标记就绪但库里没表"做二次 ensure），只有真正需要进入迁移体系的表才改 `db/`。
- **健康检查**：AI 服务 `/api/internal/health`（`grading_queue.pending`、durable 汇总）；应用 `/api/internal/health`（含 `db_pool`）；系统诊断页可取消/重排持久任务。
- **AI 止血 runbook**（不重部署）：改 `/lanshare/docker.env` 开关 → `docker compose restart ai`；卡住的提交用 `force_submit_submission_for_ai_grading` 重新派发（先 force 再 submit，`status='grading'` 直接 submit 会被视为 already_grading）。详见记忆 `ai-scheduling-architecture`。
- **Agent 运维**：launcher 为 systemd 服务；生产只读验收脚本与证据在 `docs/agent-dsh-production-runtime-acceptance-2026-09-10.json`；回滚镜像 `lanshare-app:rollback-pre-dsh-20260910`。
- **日志真源**：AI 费用看 `logs/ai_usage.jsonl`；Agent 任务看 `agent_task_events` 与 `agent_platform_requests`。

---

## 8. 安全红线（汇总）

1. 模型 key、平台 `SECRET_KEY`、数据库口令不进 runner、不进提示词、不进产物、不进日志。
2. Agent 的任何写操作都经用户实时会话与目标路由鉴权；不存在"系统身份"执行的用户业务。
3. 破坏性、公开、外发、审批决定、安全输入四类永远需要本人在平台确认。
4. 旧桥接 `/query` 只读单条 SELECT，敏感表拒绝、敏感列脱敏、按任务主体过滤；`/web` 拒内网与逐跳重定向校验；`/file` 限白名单目录与大小。
5. 模型输出永远是"待校验数据"：成绩、文档字段、路由参数都要经服务端校验；解析失败不写库。
6. 学生答案、隐私字段不进联网检索与提示词池；提示词池自动跳过疑似凭据文本。
7. 任何自动重试都有上限与配额；不确定结果先核对再决定，不换编号重放。

---

## 9. 文档与记录约定

- 计划/实施记录放 `docs/`，命名 `<主题>-<yyyy-mm-dd>.md`；证据（脚本输出、验收快照）放同名 `.json`。规范性长期文档不带日期（如本文、`prompt-pool-guidelines.md`、`document-feature-ai-implementation-guide.md`）。
- 实施记录必须写：改动点、与计划的差异、验证层级（单测/原生 PG/隔离端到端/生产只读）、未完成项、回滚方式。
- 历史方案被替代时在文首加"历史方案归档"提示并指向现行文档，不删除。
- 个人记忆（`~/.claude/projects/.../memory/`）只记非代码可推导的决策与坑，并指向 `docs/` 真源。

---

## 10. 真源索引

| 主题 | 代码 | 文档 | 测试 |
|---|---|---|---|
| 模型策略与档位 | `classroom_app/services/ai_model_policy.py`；`ai_assistant.py` `_build_model_routes` / `_bounded_fallback_routes` / `_plan_from_legacy_snapshot` | `docs/grading-model-routing-plan-2026-09-11.md`、`docs/ai-multimodal-business-routing-plan-2026-09-07.md`、`docs/assessment-routing-implementation-2026-09-07.md` | `tests/test_ai_execution_profiles.py`、`test_ai_multimodal_routing.py`、`test_ai_execution_health.py` |
| 批改链路、校验、仲裁 | `ai_assistant.py` `run_grading_job` / `_validate_grading_result_for_job` / `_review_grading_result_if_needed` / `_adjudication_allowed_for_context`；`classroom_app/services/ai_grading_service.py`、`ai_grading_attachments.py` | `docs/ai-structured-output-and-repair-2026-09-07.md`、`docs/ai-grading-model-benchmark-2026-09-11.md` | `tests/test_ai_grading_service.py`、`test_ai_adjudication_budget.py`、`test_ai_json_parsing.py` |
| 持久任务 | `classroom_app/services/ai_durable_job_service.py`；`ai_assistant.py` `_execute_durable_ai_job` | `docs/ai-durable-job-architecture-2026-07-12.md` | `tests/test_ai_durable_job_service.py` |
| 网关、预算、用量 | `ai_gateway_service.py`、`ai_usage_budget_service.py`、`ai_provider_usage_service.py` | 记忆 `deepseek-cost-and-local-guard`、`ai-scheduling-architecture` | `tests/test_ai_gateway_service.py`、`test_ai_usage_budget_service.py`、`test_ai_provider_usage_service.py` |
| 厂商调用、重试、超时、溢出 | `ai_assistant.py` `_call_ai_platform` / `_do_provider_call` / `_provider_call_with_retry` / `_provider_timeout_for_task` | 记忆 `ai-scheduling-architecture`（含止血 runbook） | `tests/test_ai_assistant_tool_calls.py` |
| 平台知识注入 | `platform_knowledge_service.py` | 记忆 `agent-bridge-and-knowledge` | — |
| 提示词池 | `prompt_pool_service.py`、`static/js/prompt_pool.js` | `docs/prompt-pool-guidelines.md` | `tests/test_prompt_pool_service.py` |
| 文档类 AI 功能 | `*_generation_service.py`、`*_import_service.py`、`material_final_document_service.py` | `docs/document-feature-ai-implementation-guide.md` | 各 `tests/test_*_plan*` / `*_evaluation*` |
| 联网检索 | `ai_web_research.py`；`ai_assistant.py` `/api/ai/web-search` | 模块 docstring | — |
| 轻量聊天查询 | `chat_platform_query_service.py` | 模块 docstring | `tests/test_agent_g9_light_query_eval.py` |
| Agent 任务与队列 | `routers/agent_tasks.py`、`services/agent_task_service.py`、`agent_dsh_task_service.py`、`agent_task_worker.py` | `docs/agent-dsh-migration-plan-2026-09-10.md`、`docs/agent-dsh-final-gates-2026-09-10.md` | `tests/test_agent_task_service.py`、`test_agent_dsh_task_service.py`、`test_agent_task_worker.py` |
| DSH 运行时 | `services/agent_runtime/`（`acp_client`、`dsh_provider`、`launcher_client`、`contracts`）、`tools/agent_dsh_launcher.py`、`deployment/dsh/` | `deployment/dsh/README.md`、`docs/agent-dsh-subagent-enablement-2026-09-10.md` | `tests/test_agent_acp_client.py`、`test_agent_dsh_provider.py`、`test_agent_dsh_launcher.py`、`test_agent_dsh_deployment.py` |
| 委托与身份 | `agent_delegation_service.py`、`agent_actor_service.py`、`agent_request_context.py`、`agent_platform_request_context.py`、`agent_user_route_request_context.py`、`dependencies.py` | `docs/agent-digital-twin-route-capability-2026-09-11.md` | `tests/test_agent_delegation_service.py`、`test_agent_authority*.py` |
| 能力层（read/write/request/route） | `agent_platform_registry.py`、`agent_platform_broker.py`、`agent_action_registry.py`、`agent_platform_write_service.py`、`agent_platform_request_*.py`、`agent_platform_route_capability.py`、`agent_capability_catalog_service.py` | `docs/agent-capability-matrix.md`（生成）、`docs/agent-capability-reviewed.json` | `tests/test_agent_platform_*.py`、`test_agent_capability_inventory.py` |
| 本人确认 | `agent_business_confirmation_service.py`、`agent_route_confirmation_service.py`、`agent_grade/signature/teaching_confirmation_service.py`、`agent_user_confirmation_actions.py`、`static/js/agent_user_confirmation.js` | `docs/agent-human-business-confirmation-2026-09-10.md` | `tests/test_agent_*_confirmation*.py`、`tests/e2e/components/agent-user-confirmation.spec.ts` |
| 模型网关与密钥 | `agent_model_gateway_service.py`、`routers/agent_model_gateway.py`、`agent_key_service.py` | — | `tests/test_agent_model_gateway.py`、`test_agent_key_gateway.py` |
| 旧桥接（SQL/文件/联网） | `agent_bridge_service.py`、`routers/agent_bridge.py` | 记忆 `agent-bridge-and-knowledge` | `tests/test_agent_bridge_service.py`、`test_agent_mcp_bridge.py` |
| 通用审批流 | `approval_workflow_service.py`、`approval_workflow_schema.py`、`approval_request_types/`、`routers/approval_workflow.py`、`static/js/approval_workflow.js` | `docs/approval-workflow-plan-2026-09-11.md` | `tests/test_approval_workflow.py` |
| 基准与回放脚本 | `tools/grading_bench/`、`tools/agent_capability_inventory.py`、`tools/ai_durable_job_load_test.py` | `docs/ai-grading-model-benchmark-2026-09-11.md` | — |

---

## 11. 已知边界与待办（截至 2026-09-11）

- 生产 `docker.env` 中的 AI 并发仍为代码默认值；调高需手动改服务器 env。
- DeepSeek 官方 2026-09-14 起把 `deepseek-v4-pro` 请求路由到 V4.1 Flash；账目标签已显式化，但若官方后续再调整需复核价表。
- Agent 子代理/工作流插件禁用；表单/多部分上传路由仍需审核适配；破坏性判定按命名保守划分。
- flash 档已知弱点：多图时思考耗尽正文为空（已用 32k 输出 + 有界回退缓解）、对"截图齐文字空"偏宽松（系统提示第 9 条约束）、格式校验失败率约 20%（软规范化缓解）。
- 全量单测存在既有失败（lessondoc、agent 路由快照、`test_db_postgres_schema` agent_* 表等），与 AI/Agent 规范无关，但新改动需用 clean worktree 对照确认未新增失败。
- `_grading_adjudication_reasons` 的"证据冲突涉及高分题"细化未做。
