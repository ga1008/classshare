# Agent 数字分身：全站路由能力层（2026-09-11）

本文记录在 DSH 生产分支上新增的通用路由能力层。目标是让 Agent 能以用户本人身份完成任何平台操作，权限严格等于用户本人（含超管）的实时权限，同时保留原有的凭据、会话、账本与人工确认边界。

## 为什么要加这一层

改造前，Agent 只能调用逐条手写并以源码摘要钉住的适配能力：46 项只读、37 项事务写、93 项审核 HTTP 请求。能力台账显示 742 项用户业务接口中只有 178 项可达（约 24%），教案、考核计划、评学表、职业规划、学习文档编辑器等整域为零。每新增一个 Web 路由都要再写一个适配器，任何一次普通的 router 修改都会让对应能力因摘要变化而 503，直到有人重新审核。这个结构本身决定了"能做任何操作"永远无法闭环。

## 设计

新增 `classroom_app/services/agent_platform_route_capability.py`。它从已挂载的 FastAPI 路由和 OpenAPI 描述推导能力，而不是靠人工登记：

- **身份与授权不变**：仍走任务委托凭据 → 用户实时登录会话 → 进程内 ASGI 调用，由目标路由自己的依赖执行鉴权。Agent 能做的事恰好等于该用户在网页上能做的事。
- **硬排除由路由元数据决定**，模型文本无法改变：登录/注销/注册等会话切换；密码、凭据、密钥、邮箱配置等安全输入；`/api/agent-*`、系统监控等控制面；HTML 页面与重定向；文件下载与表单/多部分上传；未进入 OpenAPI 的路由；`:path` 开放段。
- **已审核适配优先**：路由若已有 read/request 审核能力，只能通过原能力名调用（返回 409 并给出应使用的能力名），保留其更强的响应契约。
- **读与普通写**：直接由 `platform_request` 执行，复用既有 `agent_platform_requests` 持久账本、UUID 操作编号幂等、观察回执、不自动重试。响应契约为 `generic_json`：2xx JSON 记为 `observed_http_result`，`verified_business` 恒为 false。
- **破坏性路由**：`DELETE`，或路径/处理器名含 delete、remove、purge、reset、clear、revoke、close-out、merge、publish、archive、disable、force、bulk、batch、transfer、import、sync、approve、reject 等词的写操作，模型不能执行（403 并指示提案）。只能在最终输出中提出 `platform_route_request` 提案，由用户本人在平台确认。
- **参数校验来自 OpenAPI**：path/query 按声明的类型、范围、枚举校验；请求体只允许 JSON 对象或数组并限长 64KB，字段级校验交给路由自己的 pydantic 模型（422 会被记录为 uncertain 回执）。

能力键为 `route.` + sha256(方法 + 换行 + 路径) 前 20 位，与能力台账 `build_platform_route_inventory` 使用的键完全一致。

## 本人确认执行

新增 `agent_route_confirmation_service.py`，注册为 user_confirmation 动作 `platform_route_request`，复用既有确认模态（`static/js/agent_user_confirmation.js` 新增表单）。流程：

1. 预览：解析路由、校验参数，返回方法、路径、参数与破坏性提示，并给出 `expected_review_hash`（绑定路由键、方法、解析后路径、查询串、请求体摘要、处理器源码摘要、用户身份）。
2. 确认：重新计算并比对 hash；必须勾选 `destructive_route` 提示并填写说明；`claim_business_confirmation` 占用操作编号后先提交，再以本人身份执行一次进程内请求。
3. 执行身份：新增 `agent_user_route_request_context.py`，是 `dependencies.get_current_user_optional` 的第三个 nonce 绑定身份来源，每次都重新核对实时会话与权限指纹。
4. 回执：观察结果写入 `agent_action_executions`；HTTP 调用异常时占位保持 executing，界面提示"先核对是否已生效，不自动重试"。

## 生产分类结果

在当前分支上，910 条挂载路由中：394 条 `route_ready`、73 条 `route_confirmation_required`、123 条交由已审核适配、其余 320 条按硬排除阻断（HTML 页面 112、表单上传 46、安全输入 37、OpenAPI 外 28、控制面 27、页面路由 24、内部/递归 20、会话 15、其他 11）。加上原有审核能力，用户业务接口已全部可达或被明确拒绝，不再有"未适配"的灰区。

## 边界与后续

- 表单/多部分上传路由仍需审核适配（文件来源与病毒扫描口径不同于 JSON）。
- 破坏性判定按方法与命名规则保守划分；若某路由命名不含上述词但实际不可逆，应在路由上补 DELETE 语义或加入模式。
- 子代理与工作流插件仍禁用，原因不变（取消后远端 MCP 工作停止尚无完整证明）。
- 回归：`tests/test_agent_platform_route_capability.py` 覆盖分类、参数校验、账本幂等、破坏性拒绝、本人确认执行与重放。
