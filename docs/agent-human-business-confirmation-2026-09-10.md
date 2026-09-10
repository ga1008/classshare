# Agent 本人业务确认接入与验证（2026-09-10）

本批位于本地检查点 `0283be34` 之后。生产仍运行原后端；本文的本地、浏览器组件和隔离 PostgreSQL 结果不能替代冻结源码后的 DSH 完整任务或生产验收。

## 业务实现

确认分发从单一成绩公布实现拆分为稳定 facade 和领域模块。原有 `publish_classroom_grades` 保持参数及回执合同；新增 `review_signature_request`、`delete_empty_class`、`delete_unreferenced_course`、`merge_class_offerings`。目录将它们标记为 `user_confirmation`、`human_only`，模型只能提出公共参数，无法通过 MCP 直接执行，也不能将本人的声明放入模型参数。

签章确认只处理材料使用申请，不处理认领和身份转移。平台按原申请权限显示冻结文档、申请人、签章点和用途，当前有权审批的教师、学生或管理员本人选择批准或拒绝。管理员代批须接受明确提示并说明，决定记录在实际管理员账号下。材料、签章图片、状态或申请人权限变化会使旧核对失效；新核对中可以禁止批准而保留有说明的拒绝。批准仍由普通签章服务执行，不直接把签章应用到材料。

教学确认复用普通 Web 的影响预览和执行服务。存在学生、课堂、材料包等阻断时不能删除。合班保留普通业务的作业并存、课次映射、答卷和材料绑定及人工恢复档案；不能以合班扩大定向内容的读者范围。本人需逐项接受提示、填写说明和手工输入完整名称。具体父资源写入锁、引用表范围及普通 Web 回归由教学生命周期报告分别证明。

公共确认界面复用平台 modal；教学影响内容和名称字段与普通 Web 共用渲染器。初始不选决定、不勾选确认、不代填名称。503 保留已填内容供显式重试；新快照改变时保留说明，清空决定、名称和勾选。提交中禁止重复和关闭。响应丢失后先读取提案回执，不能自动重复执行业务。移动端表格可横向滚动，文本转义，文档链接由请求 ID 固定构造。

## 事务与身份边界

新的 `agent_business_confirmation_service.claim_business_confirmation` 仅用于不会变更账户授权的领域确认。锁顺序为：已结束的本人任务 → 当前 actor 授权转换锁 → 原登录会话行 → 领域资源 → 回执。它仍调用原有 fresh-user claim 校验；不创建或续期运行器授权，不代替账户管理和安全输入操作各自的锁协议。

先撤权或退出登录，等待中的确认读取新状态并拒绝；先取得确认事务的锁，后续撤权或退出登录等待确认事务结束。领域变更与声明回执同事务提交或回滚。相同操作编号及规范化声明读取既有回执，改变决定、说明或名称不能冒充原请求重放。已完成回执在新的合法登录下可读取，不授权新的业务操作。

签章回执沿用 FastAPI 的 JSON 编码规则转换 PostgreSQL 日期时间。该问题在真实原生测试中发现：SQLite 文本时间通过不代表 PostgreSQL 原生时间可以直接写入严格 JSON 回执。业务预览中的同步 SQL 也已移入线程池，避免占用服务异步事件循环。

## 已完成的验证

- 31 项后端合并通过：12 项签章实际 proposal HTTP、8 项教学实际 proposal HTTP、10 项既有考核行为、1 项既有通知预览校验。数据库、权限和回执均真实运行；签章夹具仅替换学校文档渲染为实际合成 DOCX，以及邮件投递。
- 5 项真实 PostgreSQL 通过：材料先变更、账号先撤权、会话先注销均使等待确认失败；确认先提交时，后续撤权及注销分别等待。原生测试以已验证的专用 loopback 集群独占创建合成数据库，结束后删除自己的库，不读取生产 DSN、密钥或业务数据。
- 11 项 Chrome 组件测试通过：原有成绩确认 5 项、签章确认 3 项、教学确认 3 项。使用真实 JS、平台 modal 和样式，接口响应由组件测试控制。这些结果不声称是整站真实服务浏览器验收。
- `npm run typecheck` 和构建通过。已实际查看签章与教学确认的 390px 手机截图；成绩界面此前亦已核对桌面和手机截图。

```powershell
python -m unittest tests.test_agent_task_improvements.AgentTaskImprovementTests.test_notification_preview_requires_explicit_recipients_without_sending tests.test_agent_signature_confirmation tests.test_agent_teaching_confirmation tests.test_agent_assessment_actions -q
python -m unittest tests.test_agent_signature_confirmation_postgres -q
npx playwright test agent-user-confirmation.spec.ts --config tests/e2e/components/playwright.config.ts
```

原生命令须显式设置专用集群 `.codex-temp/dsh-pg-rehearsal/agent-20260910-172238-3bac5373` 和端口 `55439`；没有这些变量会跳过，而非视为原生通过。

一次本批中途全量 Agent 测试运行 622 项，发现旧通知预览单元测试跨线程复用了其 SQLite 内存连接。已把该业务单元测试指向新的同步预览实现；真实 HTTP 测试继续覆盖线程池入口。该中途失败不得计为全量通过，最终全量回归需另附结果。

## 仍需完成

教学普通写入父资源锁与 B 路由来源复核、统一能力台账、新检查点完整回归、准确源码/备份匹配的原生迁移门禁、冻结版本 DSH 端到端、实际部署及线上专项验收。签章认领/身份转移和已占用课堂硬删除不由本批能力冒充覆盖。
