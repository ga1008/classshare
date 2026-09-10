# DSH 0.1.5-rc.1 子代理启用评估

结论：当前部署不能仅解除 profile 中的 disabled。已增加镜像内固定的工作流 provider 与平台单调准入账本，使用官方公开扩展 API，无需 fork DSH。真实 ACP 子会话与合成模型/MCP 验证通过，但宿主隔离的子任务停止证明尚缺，因此 **workflow 仍禁用**，没有构建或替换服务器镜像。后续启用必须重新构建并验证镜像、profile digest。

## 已确认的固定版本事实

官方项目：[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)。本次判断使用本地安装的 **0.1.5-rc.1 npm 包内容**，不是 GitHub 当前分支。逐包版本、仓库子目录、npm 固定版本 URL 和实际 lib/index.js SHA256 已保存在 [合成检查证据](agent-dsh-subagent-policy-poc-2026-09-10.json)。

| 项目 | 官方固定实现 | 对当前 LanShare 的影响 |
|---|---|---|
| 普通 subagent | tool 配置有 `maxDepth`、`toolFilter`、`enableRunInBackground`、`agentOptions`；spawn provider 只有 `providerName` | 能限制深度，没有任务范围内的数量或并发准入 |
| child scope | agent-loop 使用 `createScope(loopCtx, child)`，没有 parent scope；`applyChildComposition` 继承 preset，然后加 child 的 persona/filter | preset 的公共工具可继承，父 ACP session 自己注册的 MCP 和限制不会自动继承 |
| ACP MCP | `mountAcpMcpServers` 在 session/new 的 unpublished agent scope 内安装并等待 client | root MCP ready 不等于 child MCP ready |
| 授权策略 | `captureDelegatedPolicyOverrides` 固定 child approval 为 `never`，只携带父级显式 sandbox override | child 不应发起新审批；必须将不能执行的工作返回父级 |
| workflow | worker-thread engine 有 `maxConcurrentAgents`、`maxTotalAgents`、`maxItemsPerCall`、`syncTimeoutMs`、`disposeGraceMs` | 配置确实有效，但范围是单个 workflow run |
| workflow child | host startChild 没有传 `maxDepth` 或 `toolFilter`，只传 parent/signal/prompt/schema/可选模型路由 | 不能用 tool-subagent 的 maxDepth 推断 workflow 也被限深；需要共享 provider 拦截 |
| 取消 | 官方 one-shot driver 将 signal 转发到 child.cancel，并提供 quiescent dispose；workflow abort fanout 和 dispose 已实现 | 基础协议可复用，还需实际 ACP→MCP/Shell 的完整取消验收 |

## 本次合成实测

复现：`node tools/dsh_subagent_policy_probe.mjs .codex-temp/dsh-poc docs/agent-dsh-subagent-policy-poc-2026-09-10.json`。需要该目录已经安装官方固定 npm 包，脚本不安装依赖、不读取用户数据、不使用模型或平台凭据。

1. 使用官方 Cordis scopes、Tools 和 applyChildComposition：父 scope 注册 `mcp__session__echo`，并禁止公共 `global_file`。实测父工具只有 MCP，flat child 只有公共 file。证明父工具和限制均不会自然进入 child。这是官方组件的范围实验，没有启动模型或 ACP 子会话。
2. 官方 workflow **真实 worker thread**，合成 child provider，配置并发 1、总数 3：三个 child 完成，峰值 1；第四次 agent() 触发总数上限。
3. 同一个 parent 同时建立两个 workflow，每个并发配置 1，实测总体峰值 **2**。不能把每个 run 的限制当作 task 总限制。
4. 取消时已启动 child 接收 abort，排队项没有继续启动，dispose 后活动数为 0。这里 child 为合成可取消句柄，不能作为实际 Shell/MCP 已清理的证据。

官方还明确注明 workflow VM 可逃逸，worker thread 是故障隔离而非安全边界。其脚本内 semaphore 不应承担可信的任务准入；宿主 provider 必须独立限额。当前容器的 network=none、资源与路径限制仍然必要。

## 可执行的有限启用方案

1. 增加 image-owned `lanshare-bounded-spawn` provider。使用官方 Agent factory 的 `create({parentAgent, signal, setup})` 生命周期；setup 成功之前不能发布 child。不要依靠 `agent/created` 异步补工具：该事件发生时已经发布，不能保证 readiness。
2. 在该 provider 统一做准入：最多 **1 个活跃 child/runner**，含创建中与清理中的 child；深度最多 1；内存准入总量最多 4。平台 `agent_task_children` 另外按 task 行锁与唯一 ordinal 累计最多 **4 次已准入 child，跨 attempt 不重置**。取消与失败均不退总额度。只有 workflow 使用该 provider；普通 subagent、fork、control、后台 continuable 和 child 再次委派均禁用。排队有界，父取消拒绝未开始项，实际本地 dispose 完成才归还内存活动槽。该槽是受信 provider 的生命周期语义，不能抵抗同容器内逃逸的脚本，不能描述成宿主硬并发边界。
3. setup 中显式组装 child MCP，仍使用当前 task 的同一 scoped tools token、同一 loopback gateway，等待 required discovery 成功；安装 child 执行 guard，把所有工具调用限制为父级在委派时已允许工具的交集，并再次检查父级当前限制。单用 tools.restrict 不充分：当前 tools.view 对本 scope 自己注册的工具另有处理，MCP 自己注册的工具也必须经过执行 guard。不得加入不存在于父级的新工具权限。
4. 模型固定继承 `deepseek-official` 和当前任务 gateway model；禁用 model-facing 路由选择。沿用同一 scoped model token，平台现有 model request 总数/并发、actor/task/global 预算自然合并；工具 token 也沿用现有 task/fence 和操作账本。不要为每个 child 重发独立额度。平台 gateway 对未授权 model 已返回 403。
5. 只预留 workflow 入口，指向固定 provider，配置 `maxConcurrentAgents: 1`、`maxTotalAgents: 4`、`maxItemsPerCall: 16`。普通 subagent、Ralph、fork、continuable、控制旧子会话的工具继续关闭。当前连 workflow 本身也保持 disabled，直至宿主边界与 Linux 完整验收达成。
6. 取消汇总沿用官方 run/result/dispose；宿主必须持有全部 child 句柄。root turn 取消、ACP连接关闭、MCP异常及容器停止都要收敛所有 child。无法确认清理时保留占位并终止 runner，不宣布资源已释放。工具失败只能返回明确未完成状态。
7. 文件使用同一 task workspace，首期委派优先独立分析、读取与明确分配的文件；平台写入仍走原 actor 权限与操作账本。共享文件夹不提供多 child 同一文件编辑的事务保证。

## 镜像与上线门槛

现 Dockerfile 已 COPY `deployment/dsh/plugins` 和 profile，无需新增 npm 依赖即可加载镜像内 provider。修改 plugin/profile 后仍必须增量重建，因为 entrypoint/launcher 比对 image-owned profile digest；旧镜像不能接受改过的外部 profile。只有新增运行时依赖时才需要更新 package-lock。

启用前仍需宿主独立子执行身份、生命周期观察与 Linux network=none 的新镜像验收；不能只依据容器内的 finish 请求开放硬并发容量。当前 profile 与 plugin 输入已变化，旧镜像的已有报告仍只证明旧输入，禁止用新目录冒充旧镜像的 profile。

## 已实现与实际验证

`deployment/dsh/plugins/bounded-workflow/` 包含单一 provider、共享 FIFO admission、子 scope 交集、严格结构化终态与有界 HTTP ledger client。使用官方 `agents.create({setup,parentAgent,signal})`，在发布前完成必需 MCP discovery；`system-prompt/assemble` 过滤模型展示，`tools.guard` 拒绝实际越界调用。结构化结果只有 `tools/result` 成功才记录；模型私选 provider/model、额外 persona/filter、嵌套委派和后台 Shell 参数均拒绝。子请求沿用父 reasoning、最多不超过父级输出上限的 16384 token 限制、同一 scoped model/tools token。

平台接口为 `POST /api/agent-bridge/children/admit` 与 `POST /children/{uuid}/finish`。准入绑定 task、attempt、fence、delegation、actor、父子会话及 UUID 幂等键。task 行锁串行化计数，数据库唯一 `(task_id,ordinal)` 且 ordinal 只能 1..4；不接受“完成后退款”。finish 只保存 `runtime_reported_status`，固定返回 `host_execution_verified=false, capacity_refunded=false`。失去任务授权后不能写 finish，保留未观察的准入事实；这不是 host settlement API。

复现与证据：

- `node --test tests/test_agent_dsh_workflow.mjs`：9 项通过，使用固定官方 Cordis/Tools/Scope，child factory 为显式合成句柄。涵盖真实 tool execute 拒绝、共享槽直到 disposal 完成、总额、嵌套、MCP 初始化失败、创建中关闭、清理失败保留占位、结构化终态与禁止后台 Shell。
- `python -m unittest tests.test_agent_child_admission tests.test_agent_dsh_launcher`：22 项运行，21 通过、1 项 Windows symlink 权限跳过。实际 HTTP/SQLite 验证准确幂等、跨 attempt 总额、父子绑定、撤销、角色/任务隔离；真实 Node UDS relay 与宿主 HTTP gateway 验证新增路径和方法 allowlist。
- `python -m unittest tests.test_agent_child_admission_postgres.AgentChildAdmissionPostgresTests`：在显式离线 PG 16 集群新建独占数据库，3 项通过。6 个同时准入仅 4 个提交；相同 UUID 并发只保留一行；4 个 runtime finish 后仍拒绝第五次；再次 schema startup 保留原行。
- `python tools/dsh_workflow_poc.py`：[真实 ACP/工作流报告](agent-dsh-bounded-workflow-poc-2026-09-10.json)。固定官方 DSH 0.1.5-rc.1，模型与 MCP/ledger 为本地确定性 fixture，**没有调用真实 DeepSeek 模型或付费服务**。两个实际 child 完成，包括一个结构化结果；每个 child 显式 MCP 初始化并实际调用 echo，父禁止的 write 没有进入 child；child 不展示 workflow。第三个 child 执行慢 MCP 时 ACP 取消，实际本地 child aborted、dispose 完成，第四个排队 child 没有启动。

## 已实测的剩余边界

真实 ACP 取消返回时，合成远端 MCP HTTP handler 仍在运行，未收到 `notifications/cancelled`。因此 **本地 child dispose 不等于远端业务停止**；平台已有 operation/request-budget 账本需独立收尾已接纳的工作，未知结果保留 uncertain，不能重试冒充未发生。PoC 专门保存取消返回时远端活动数，fixture 最后由实验脚本显式清理，不将该清理伪称 DSH 完成。

此外，官方 workflow VM 不是安全边界，Shell 与工作流处于同一 runner 信任域。数据库的 4 个准入名额是持久单调限制，但不能凭它证明模型绕开 provider 后不存在别的进程；runner 自报 finish 也不能释放宿主硬容量。完整启用需要将实际 child 执行托管给宿主窄 launcher：由宿主为每个 child 创建独立可观察的执行单元，派生 task/actor/attempt/fence 和交集授权，只有宿主确认 child 单元及关联工具调用完成后才能归还并发容量。控制能力必须与模型可读的 scoped tools token 分离，且不能为 child 发新预算。当前不新增这种不完整的宿主接口，也不宣称已激活工作流。
