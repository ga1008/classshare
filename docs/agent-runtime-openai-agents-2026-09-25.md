# Agent 重构：OpenAI Agents SDK 运行时（2026-09-25）

> 状态：已实施。取代 DSH（deepseek-harness）运行时；`docs/agent-dsh-*.md` 仅作历史参考。
> 规范入口：`docs/ai-and-agent-engineering-standard.md` 第 4 节。

## 1. 需求与结论

| # | 需求 | 落地 |
|---|---|---|
| 1 | Agent 仅教师可用；全平台排队；超管可暂停/停止/清空 | 路由层 `_current_agent_user` 只放行教师；前端只给教师渲染 Agent；全局队列 + `agent_queue_control_service`（暂停/恢复队列、挂起/暂停/恢复/停止任务、清空排队） |
| 2 | DeepSeek V4.1 Flash 多模态；以用户全部权限操作平台；可联网；确认后不再过问 | `agent_sdk` 运行时，模型 `deepseek-flash`（思考模式、图片输入）；平台操作经 `/api/agent-bridge` 以用户实时会话执行；`web_search`/`web_fetch`；破坏性操作经自检后直接执行 |
| 3 | 采用成熟 Agent 后端，不用 DSH；Docker 镜像可装 | **OpenAI Agents SDK `openai-agents==0.16.1`**（纯 Python，运行在现有 `agent-worker` 容器） |
| 4 | 超管也要硬性拦截 | `agent_danger_guard` 硬性拦截清单对所有人生效 |
| 5 | 大量修改/删除前自问是否合理 | `safety_check` 服务端核验（见 §5） |
| 6 | 打通平台各接口 | 复用既有能力层：审核读/写/请求能力 + `route.*` 全站 JSON 路由 + 文件抽取 + 命名统计查询 |
| 7 | 疑问以选项呈现，最后一项自定义 | `ask_user` 工具 → 任务停靠 → 选项卡片（首项“推荐”、末项“自定义输入…”） |
| 8 | 思考/决定/工具/操作/疑问分明 | 结构化事件 + 时间线分类渲染（§6） |
| 9 | 液态玻璃 | Agent 面板用 LQ 组件（`lq-btn`/`lq-chip`/`lq-spinner`/玻璃确认框）与玻璃令牌；升级窗口模板，让 `lq-*` 控件不再被旧的按钮全局样式覆盖 |

## 2. 为什么选 OpenAI Agents SDK

候选：OpenAI Agents SDK、LangGraph、Pydantic AI、smolagents、继续用 DSH。

- **DSH**：每任务一个隔离容器 + 宿主 launcher（systemd）+ Node 中继，2c/4GB 主机只能并发 1；能力边界靠提案/本人确认，达不到“确认后直接执行”；运维链路长（镜像 digest、launcher、quiesce 门禁）。
- **LangGraph**：人机中断与检查点成熟，但引入 langchain-core 体系，依赖重，且与现有平台工具层重复。
- **Pydantic AI**：轻量，有 DeepSeek provider 与 deferred tools，但 DeepSeek 思考模式 + 工具调用的 `reasoning_content` 回传需要额外适配。
- **OpenAI Agents SDK（选定）**：官方维护，只依赖已锁定的 `openai` 包；Chat Completions 适配器原生支持 DeepSeek `reasoning_content` 回传（思考模式下调用工具的硬要求）；流式事件天然区分 `reasoning_item / tool_called / tool_output / message_output`，正好对应“思考/工具/说明”；`StopAtTools` 支持提问即停；`cancel(mode="after_turn")` 支持安全暂停；`call_model_input_filter` 可做厂商兼容。
- **版本**：`openai-agents==0.16.1`（兼容锁定的 `openai==2.30.0`；pydantic 2.12.0→2.12.5 为补丁升级）。清华源对该包返回 403，`DockerfileBase` 改用阿里云镜像（全量锁定依赖已验证可解析为 linux/py3.12 wheel）。

## 3. 架构

```
浏览器 Agent 面板 ──► /api/agent-tasks/*（app 容器）──► agent_tasks 队列
                                                          │ claim（全局并发 2，每人 1；停靠任务不占槽）
agent-worker 容器：agent_task_worker ─► agent_sdk.runner.run_agent_task
    ├─ state.setup_attempt：fenced attempt + tools 委托凭据（绑定用户实时会话）
    ├─ model：DeepSeek deepseek-flash（OpenAI 兼容，thinking=enabled，effort high/max）
    ├─ tools：FunctionTool ─HTTP─► app /api/agent-bridge/mcp（按用户实时权限执行）
    │                       └────► ai 容器 /api/ai/web-search（联网搜索）
    ├─ recorder：结构化事件 → agent_task_events → SSE → 前端时间线
    └─ state：停靠（提问/暂停）/ 恢复 / 结束（租约围栏）
```

- 模型密钥：优先使用超管在平台配置的 Agent Key（加密存储），否则 `DEEPSEEK_API_KEY`；密钥只在 worker 进程内，模型永远看不到。
- 身份链不变（规范 R1）：任务委托凭据 → 用户实时登录会话 → 目标路由自身鉴权。登出或权限变更后凭据立即失效。

## 4. 任务生命周期

```
queued ─claim─► running ──最终回答──► completed
                  │ ├─ask_user──► queued+waiting_input ─回答─► queued+resume_pending ─claim(优先)─► running
                  │ ├─暂停/超时/步数上限─► queued+paused ─继续─► queued+resume_pending ─claim(优先)─► running
                  │ └─取消/授权失效──► canceled / failed
超管：挂起未开始任务 queued+held；暂停队列（不再领取）；清空排队（取消未开始的，可选含停靠任务）
```

- 停靠时保存 SDK 对话（`agent_run_states.history_json`），恢复时追加“回答/补充/继续”消息后续跑；停靠任务不占执行槽。
- 停靠超过 `AGENT_TASK_PARKED_TTL_HOURS`（默认 72h）自动关闭；worker 崩溃（租约过期）→ 标记失败并保留回执，不自动重放。
- 执行中收到的补充说明在本段结束前并入对话（最多 3 轮）。
- 表：`agent_run_states`、`agent_queue_controls`（`services/agent_runtime_schema.py`，运行时 `CREATE TABLE IF NOT EXISTS`，应用启动时建好；不进迁移体系，不触发原生 PG 演练门禁）。

## 5. 安全策略（`agent_danger_guard.py`）

1. **硬性拦截（对所有人，含超管）**：`/api/manage/system/*` 的 DELETE、删除学生/行政班/学期/账号、停用教师、一键清空/重置/抹除/迁移/回滚、超管授权变更、提交文件修复等。这些路由在能力目录中不可见，执行一律 403。
2. **破坏性操作自检**：删除/撤销/清空/合并/发布/导入/同步等必须携带 `safety_check{user_requested, data_state, reason, target_count, targets}`：
   - 单条：用户明确要求，或数据确认无效/过期 → 直接执行；
   - 大量（批量类路由、多条、或本任务第 5 次起）：须“用户要求”且“数据无效/过期”；否则先 `ask_user`，用户回答后以 `user_confirmed` 执行——服务端核对本任务确有已回答的疑问；
   - 每任务破坏性操作上限 40 次，超过熔断；
   - 已审核适配（单对象语义已审计）仅在批量时要求自检；`route.*` 通用路由只要是破坏性就必须自检。
3. 自检记录写入平台请求回执（`normalized.safety_check`），并在前端“操作”步骤显示为“执行前自检”。
   - `user_confirmed` 必须携带 `question_id` 且等于本任务**最近一次已回答**的疑问编号（回答消息会告知模型该编号），避免拿无关的旧回答放行批量删除；
   - 破坏性计数先原子自增再判定（行锁串行化并行工具调用），被拒则回滚；
   - 密码/凭据/权限类写路由与已审核事务写中的“删除组织、停用账号/归属、授予/撤销超管”同样硬性拦截（`HARD_BLOCKED_WRITE_ACTIONS`，桥接 `platform_write` 前校验）；不变量测试要求新增的破坏性写动作必须显式归类。
4. 密码/凭据类路由仍硬排除（只能本人在页面填写）；Agent 控制面路由不可调用。

## 6. 输出语义（事件 → 时间线）

| 事件 | 呈现 |
|---|---|
| `thinking` | 思考（虚线、折叠，点击展开完整推理） |
| `decision` | 决定（主色卡片：决定 + 理由 + 接下来几步），来自 `record_decision` 工具 |
| `tool_call`/`tool_result` | 工具（按 call_id 合并为一行：做什么 → 结果/完成/未成功；联网用地球图标） |
| `operation`/`operation_result` | 操作（强调卡片：意图、方法+路径、自检条、已执行/未成功/待核对） |
| `guard` | 安全拦截（警示色） |
| `question_requested`/`question_answered` | 疑问（待答时为可点选卡片；答后显示“问题 → 回答”） |
| `assistant_text` / `artifact` | 说明 / 文件（可下载） |
| 生命周期 | 细分隔线（排队、领取、暂停、恢复、取消…） |
| 终态 | 结果卡：Markdown 成品、已执行操作清单、产物文件、用量、失败原因与重试 |

## 7. 前端

- `static/js/ai_workspace_widget.js`：窗口 + 对话 + 模式切换（约 250 行，原 3221 行）。
- `static/js/agent_workbench.js`：Agent 面板控制器（SSE/轮询、键控增量渲染；四态输入框：新任务/回答/补充/追问；我的任务、定时任务、超管队列抽屉、深链、截图/附件/粘贴）。
- `static/js/agent_workbench_render.js`：纯渲染；`static/js/ai_workspace_context.js`：页面上下文，聊天与 Agent 共用。
- `static/css/agent_workbench.css`：仅教师加载；`ai_workspace.css` 的旧按钮全局样式排除 `lq-*` 控件。
- 学生：只保留 AI 对话（含“我的对话”），`/api/agent-tasks/*` 返回 403；聊天中的“转为 Agent 任务”按钮仅对教师显示。

## 8. 验证层级

- 单测（SQLite，不调付费模型）：`tests/test_agent_sdk_runtime.py`。脚本化 DeepSeek 流（`reasoning_content`/工具调用，并按 DeepSeek 规则校验消息顺序）+ 真实 FastAPI 桥（ASGI），覆盖：决定→工具→疑问→停靠→回答→优先恢复→结果；暂停/恢复；取消后不调用模型；暂停队列/清空；危险守卫规则。Agent 全套 `test_agent_*.py` 与基线对比无新增失败。
- **真实 DeepSeek 契约**（2026-09-25，一次性，约 1k tokens）：思考模式 + 工具调用 + `reasoning_content` 回传通过；`reasoning_effort=max` + 图片输入通过。发现并修复一个问题：SDK 流式产生的空 assistant 消息被插在 tool_calls 与 tool 结果之间，导致 DeepSeek 返回 400。`model.deepseek_input_filter` 在每次调用前剔除空消息。
- 浏览器（P03 种子库 + Playwright）：欢迎页、全部步骤类型的时间线、结果卡、选项高亮 + 自定义输入并提交（库内变为 resume_pending）、超管队列抽屉暂停/恢复、学生仅对话且接口 403。
- 未做：生产环境真实长任务压测（上线后通过超管队列与 `ai_usage` 观察）。

## 9. 配置（`docker.env.example`）

`AGENT_RUNTIME_ENABLED`、`AGENT_MODEL_DEFAULT=deepseek-flash`、`AGENT_TASK_GLOBAL_CONCURRENCY=2`、`AGENT_TASK_WORKER_CONCURRENCY=2`、`AGENT_TASK_MAX_RUNTIME_SECONDS=1800`（单段，超时自动暂停，可继续）、`AGENT_TASK_MAX_TURNS=60`、`AGENT_TASK_MAX_WEB_SEARCHES=12`、`AGENT_TASK_PARKED_TTL_HOURS=72`；compose 为 agent-worker 设置 `AGENT_BRIDGE_BASE_URL=http://app:8000`、`AI_ASSISTANT_URL=http://ai:8001`。已移除 `AGENT_DSH_*`。

## 10. 已知边界

- 平台内图片（作业截图等）目前以文本抽取方式读取；直接看图只支持用户附件与截图（模型工具结果无法携带图片）。
- `/api/agent-model/*` 模型网关与旧 `agent_question_service` 已不被新运行时使用（保留以兼容历史数据，后续可清理）。
- 旧 DSH 任务的 `proposed_actions` 只展示说明，不再提供一键执行。

## 11. 回滚

回滚代码到上一发布提交并重新部署即可（运行时表只新增、不改动既有表）。宿主 `lanshare-agent-launcher.service` 已被部署钩子停用；若需回到 DSH，需执行 `systemctl enable --now lanshare-agent-launcher.service` 并恢复 compose 中的 launcher socket 挂载。
