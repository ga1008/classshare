# T12 - 最终切换门禁与验收

## 目标

建立 SQLite 正式切换到 PostgreSQL 的最终门禁。只有所有门禁满足，才允许生产启用 PostgreSQL 写入。任何未解决的数据、附件、部署、回滚或性能风险都必须阻断切换。

## 切换前必备条件

- [ ] T01-T11 均有执行记录。
- [ ] 当前生产 SQLite 已完成只读备份。
- [ ] 远程 `/lanshare/data` 已确认不会被部署覆盖或删除。
- [ ] PostgreSQL 服务在远程 Docker Compose 中启动并通过健康检查。
- [ ] SQLite 到 PostgreSQL 生产数据副本迁移成功。
- [ ] 表行数、外键、序列、附件引用、关键业务抽样通过。
- [ ] app 和所有 worker 使用同一 PostgreSQL 配置。
- [ ] 远程无外网部署所需镜像和依赖已准备完成。
- [ ] 回滚命令、备份位置、维护窗口已确认。
- [ ] 真实密码只在 `.env` 或远程 `docker.env` 中保存，不进入仓库。

## 生产切换步骤

生产切换采用分阶段流程，详细执行手册见 `RUNBOOK-remote-postgres-staged-cutover.md`。

1. 本地验证可行性，确认工具、报告、门禁和可运行测试稳定。
2. 先部署远程 PostgreSQL 数据库服务，不切换 app。
3. 迁移 SQLite 快照数据进入 PostgreSQL，不破坏原始数据。
4. 用脚本直连 PostgreSQL 验证数据完整性和关键接口依赖数据。
5. cutover gate 为 `ready` 后，修改配置文件切换到新数据库并重启 app/worker。
6. 执行 postflight 和业务验证。
7. 对可控问题做最小修复并重复验证。
8. 若不可用或风险不可控，冻结写入并恢复 SQLite。

任何一步失败都必须停在当前步骤，不允许跳步。

## 切换后验收

- [ ] 登录正常。
- [ ] 教师首页正常。
- [ ] 学生课堂入口正常。
- [ ] 草稿保存正常。
- [ ] 作业提交正常。
- [ ] 附件上传、下载、删除正常。
- [ ] 教师查看提交、批改正常。
- [ ] 邮件、AI、Agent、博客 crawler worker 不报数据库连接错误。
- [ ] 队列无重复领取和异常积压。
- [ ] PostgreSQL 连接数稳定。
- [ ] 没有持续锁等待或死锁。
- [ ] 新写入数据能在页面正确展示。
- [ ] 备份任务正常。

## 当前门禁报告

报告：

- `.codex-temp/db-cutover-gate-current/cutover-gate.json`
- `.codex-temp/db-cutover-gate-current/cutover-gate.md`

当前状态：

- `status=blocked`
- `production_data_modified=false`
- `remote_data_modified=false`
- `cutover_executed=false`

当前已通过或降级：

- 迁移 readiness 报告：`ok`
- 文件完整性基础报告：`ok`，但仍有缺失引用
- PostgreSQL 数据装载演练：`ok`
- PostgreSQL dump/restore 演练：`ok`
- PostgreSQL performance baseline：`ok`
- 远程 Docker Compose 数据层压测：`ok`
- SQLite 外键历史问题已在迁移副本中验证可修复，降级为 `CUT-W002`
- 邮件 outbox、Agent task、blog crawler、wrong summary、AI grading、private message AI job、material AI import、session material generation task 的关键领取/状态机路径已接入 PostgreSQL 并发语义；但历史业务 SQL、其他后台任务和全栈 PostgreSQL 运行仍未完成。
- 邮件 outbox 入队、课程资料 folder/file 创建、课堂资料分配幂等写入、消息中心通知/私信/私信附件写入、material AI import 创建、session material generation 创建已使用 PostgreSQL `RETURNING`、`ON CONFLICT ... DO NOTHING` 或 `ON CONFLICT ... RETURNING`；散落的 `BEGIN IMMEDIATE` 已收口到 PostgreSQL no-op 的统一 helper。
- 普通作业创建、试卷发布作业、草稿创建、学生提交记录创建、阶段试炼尝试/作业/证书写入和 AI 聊天 session 创建已接入统一 `execute_insert_returning_id()`。

当前硬阻塞：

1. `CUT-R003`：文件元数据完整性仍有缺失引用，`missing_references=3`。
2. `CUT-R005`：真实 `docker.env` 未请求 PostgreSQL 切换。

`CUT-R003` 当前证据：

- 3 条缺失引用都指向同一原始文件：`吴林炜 24053010232.doc`，期望 SHA256 为 `ce674830ef65c8fe0d253e37a4a020555a0f48c8114f4a15186636e1c5a2eb31`，大小 `397824`。
- 远程 `/lanshare`、本机项目目录和本机坚果云主目录均已只读搜索，未找到可恢复 doc 原件。
- 同名 PNG 与数据库记录的大小和 hash 不一致，不能作为替代文件。

为什么 `CUT-R005` 不能现在消除：

- 当前已具备 PostgreSQL 基础连接 adapter、启动 schema 只读校验保护，并完成邮件 outbox/Agent task/blog crawler/wrong summary/private message AI job/material AI import/session material generation task 领取样本适配、作业/试卷/学习进度/课程资料/消息中心关键写入路径主键返回适配以及 AI grading 状态机 token guard；但还没有完成历史 SQL 方言、全部业务写入路径和所有后台任务的 PostgreSQL 全栈适配。
- 强行在生产 `docker.env` 设置 `DB_ENGINE=postgres` 仍可能导致服务启动或业务请求失败。
- 必须先完成 T02-T05 的 app/worker PostgreSQL 执行适配和全栈回归，再由 gate 重新判断是否允许切换。

## 成功标准

只有满足以下条件，才能宣布迁移成功：

1. cutover gate 为 `ready`。
2. 生产 PostgreSQL 连续运行完整观察窗口。
3. 核心业务无阻断故障。
4. 新增业务数据只写入 PostgreSQL。
5. SQLite 不再作为权威写入库。
6. PostgreSQL 备份覆盖切换后状态。
7. 团队知道当前权威数据库已变更为 PostgreSQL。

## 当前结论

截至 2026-06-05，P01 不能部署 PostgreSQL 切换，不能修改远程生产 `docker.env` 启用 `DB_ENGINE=postgres`，不能合并或推送时宣称生产迁移完成。

下一步必须：

1. 恢复 3 条缺失附件，或形成业务签收豁免。
2. 完成 app/worker PostgreSQL adapter、schema 校验、业务 SQL 迁移、剩余后台任务适配和全栈回归。
3. 在 PostgreSQL 模式下跑全栈测试和远程 Compose app 压测。
4. 再次生成 cutover gate，直到状态变为 `ready`。
## 2026-06-06 增量门禁记录

本轮新增通过的门禁证据：

1. 管理端教学基础数据写入路径已开始具备 PostgreSQL 主键返回能力，覆盖班级、学生、课程、课堂、教师 onboarding、学期、教材创建链路。
2. `classroom_app/routers/manage_parts` 已无 `lastrowid` 和裸 `conn.cursor()`。
3. PostgreSQL schema gate 已扩展为 `40/40 required tables`，新增覆盖管理端学期、教材、课程课次、课堂课次、AI 配置依赖表，以及教务同步的课表、名单、监考、任课考试、考试名单、教师日历事件表。
4. 新增并通过 `tests/test_manage_postgres_writes.py`，验证管理端关键写路径不会绕过统一 insert helper。
5. 新增并通过 `tests/test_academic_sync_postgres_writes.py`，验证教务同步在 PostgreSQL 下不再发出 SQLite `INSERT OR IGNORE`、upsert 能 `RETURNING id`、运行时 schema 只读校验不会执行 SQLite DDL。

仍然阻断 cutover 的原因：

1. `CUT-R003` 仍存在 3 条附件缺失引用，未恢复原始文件或形成业务签收豁免前不能放行。
2. `CUT-R005` 仍存在：虽然已有更多 app/worker 写路径完成 PostgreSQL 适配，但历史业务 SQL、全部后台任务、全栈 PostgreSQL app 运行验证和远程 Compose app 验证仍未完成。
3. 远程阶段 1-3 可以继续按 runbook 做数据库层部署、迁移、直连验证；阶段 4 配置切换必须等待 cutover gate 为 `ready`。

强制提醒：

1. 不得损坏、覆盖、删除、移动线上 `/lanshare/data`。
2. 不得把真实数据库密码、连接串、Cookie、Token 写入仓库或报告；真实值只允许写入远程未提交 `.env`/`docker.env`。
3. 任何失败都必须优先保留现场报告和可恢复备份，而不是直接清理证据或继续推进。
4. 当前不得声明生产 PostgreSQL 迁移成功。

## 2026-06-06 后续门禁记录

新增已通过的门禁证据：

1. Agent、反馈/表情/文件、消息中心、材料、账户/待办/考勤、行为事件、博客、课堂互动、协作、讨论附件、电子签名主记录等写入路径已进一步接入 PostgreSQL 主键返回语义。
2. 新增并通过 `tests/test_agent_postgres_writes.py`、`tests/test_router_postgres_writes.py`、`tests/test_account_todo_postgres_writes.py`、`tests/test_blog_postgres_writes.py`、`tests/test_behavior_postgres_writes.py`、`tests/test_classroom_interaction_postgres_writes.py`、`tests/test_collaboration_postgres_writes.py`、`tests/test_file_related_postgres_writes.py`。
3. 全项目 `lastrowid` 命中已进一步缩小，剩余集中在统一 helper、SQLite fallback、需要整行 `RETURNING *` 的状态机分支和少数尚未完成业务域。

仍然阻断 cutover 的原因：

1. `CUT-R003` 未消除：3 条缺失附件引用仍需恢复原文件或业务签收豁免。
2. `CUT-R005` 未消除：仍有 chat handler、materials git、portfolio、signature access request、submission file alignment 等历史写入点，以及运行时 DDL/PRAGMA/SQLite fallback 需要继续审计或真实 PostgreSQL 验证。
3. 还没有完成真实 PostgreSQL app/worker 全栈运行、远程 Compose app 验证和维护窗口 postflight。

因此当前仍不得执行远程阶段 4 配置切换；不得修改生产 `docker.env` 设置 `DB_ENGINE=postgres`、`DATABASE_URL`、`POSTGRES_BACKEND_READY=true`。

## 2026-06-06 后续复核记录

本轮复核新增以下验收证据：

1. 本地 P01 回归集合已通过：`Ran 218 tests ... OK`。
2. `git diff --check` 通过，仅出现既有换行提示，无空白错误。
3. `python tools\db_cutover_gate.py --json-output .codex-temp\db-cutover-gate-current\cutover-gate.json --markdown-output .codex-temp\db-cutover-gate-current\cutover-gate.md` 已重新生成门禁，结果仍为 `status=blocked`。
4. `deployment\deploy_remote.ps1 -DryRun` 通过，输出确认没有上传文件，也没有触碰 Docker Compose。
5. 敏感配置扫描未发现真实 PostgreSQL 连接串或真实密码进入代码、目标文档、报告或日志。

当前仍然阻断 cutover：

1. `CUT-R003`：附件元数据仍有 `missing_references=3`，必须恢复原始附件或形成业务签收豁免。
2. `CUT-R005`：生产 `docker.env` 未请求 PostgreSQL 切换；且仍有少数历史写入点、SQLite fallback、运行时 PostgreSQL 全栈 app/worker 验证和远程 Compose app 验证未完成。

结论：可以继续远程阶段 1-3 的数据库层部署、迁移副本和脚本直连验证；不得进入阶段 4 配置切换，不得宣布生产 PostgreSQL 迁移成功。

## 2026-06-06 继续推进后的门禁记录

本轮新增通过的门禁证据：

1. 普通业务 service/router 写入路径中已不再出现裸 `cursor.lastrowid` 或 `last_insert_rowid()`；残留仅在统一 helper、迁移标记和 SQL helper 元数据中。
2. 聊天、资料 Git、作品集、签名申请、提交文件对齐恢复、Blog crawler、private message AI job、session material generation task、教务同步 SQLite fallback 等写入路径进一步收口到统一主键返回或业务唯一键查回。
3. 新增 `tests/test_remaining_postgres_write_paths.py` 并纳入 P01 回归。
4. P01 回归集合通过：`Ran 224 tests ... OK`。
5. `git diff --check` 通过；敏感配置扫描未发现真实 PostgreSQL 密码或生产连接串泄露。
6. cutover gate 已重新生成，安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。

仍然阻断 cutover：

1. `CUT-R003`：3 条附件缺失引用未恢复或签收豁免。
2. `CUT-R005`：生产 `docker.env` 未请求 PostgreSQL 切换；运行时 `PRAGMA`/SQLite fallback、真实 PostgreSQL app/worker 全栈运行和远程 Compose app 验证仍未全部完成。

结论不变：当前不得执行远程阶段 4 配置切换，不得修改生产 `docker.env` 启用 PostgreSQL，不得声明生产迁移成功。

## 2026-06-06 元数据 helper 门禁记录

本轮新增通过的门禁证据：

1. discussion attachment、background task ledger、base resource、organization management、organization scope 的 PostgreSQL 元数据读取已开始脱离 `sqlite_master`/`PRAGMA`。
2. 新增 `tests/test_postgres_metadata_helpers.py`，并扩展相关测试。
3. P01 回归集合通过：`Ran 229 tests ... OK`。
4. `deployment\deploy_remote.ps1 -DryRun` 通过，未上传文件，未触碰 Docker Compose。
5. cutover gate 已重新生成，状态仍为 `blocked`，安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。

仍然阻断 cutover：

1. `CUT-R003`：3 条附件缺失引用未恢复或签收豁免。
2. `CUT-R005`：生产 `docker.env` 未请求 PostgreSQL 切换；真实 PostgreSQL app/worker 全栈运行、远程 Compose app 验证和剩余 SQLite fallback 审计仍未完成。

结论不变：只能继续阶段 1-3 的数据库层部署/迁移/直连验证；不得进入阶段 4。

## 2026-06-06 高并发烟测工具门禁记录

本轮新增通过的门禁证据：

1. `tools/high_concurrency_smoke.py` 的 app 连接型种子数据写入已接入 `execute_insert_returning_id()`，不再在 PostgreSQL engine 下依赖 SQLite `lastrowid`。
2. 新增并通过 `tests/test_high_concurrency_smoke_postgres.py`，证明 PostgreSQL engine 下种子写入均使用 `RETURNING id`。
3. 性能/烟测相关本地集合通过：`Ran 6 tests ... OK`。

仍然阻断 cutover：

1. `CUT-R003`：3 条附件缺失引用未恢复或签收豁免。
2. `CUT-R005`：生产 `docker.env` 未请求 PostgreSQL 切换；真实 PostgreSQL app/worker 全栈运行、远程 Compose app 验证和观察窗口 postflight 仍未完成。

结论不变：该工具链改进只减少 T11/T12 的一个技术风险，不能替代生产阶段 4 切换验证。

## 2026-06-06 附件豁免门禁增强记录

本轮新增通过的门禁支撑能力：

1. `tools/db_attachment_restore_plan.py` 的缺失附件豁免模板已从单纯 ID 列表增强为带业务上下文、文件 hash、文件大小、规范目标路径和风险确认项的可审计清单。
2. 有效豁免清单必须包含固定 scope、manifest version、批准人、批准时间、原因、业务确认，以及四项风险确认全部为 `true`。
3. 新增/更新测试通过：`python -m unittest tests.test_db_attachment_restore_plan tests.test_db_cutover_gate`，结果为 `Ran 8 tests ... OK`。
4. 重新生成 `.codex-temp/db-attachment-restore-plan-current/reports/attachment-restore-plan.json` 后，安全字段仍为 `production_data_modified=false`、`filesystem_modified=false`、`remote_data_modified=false`。

仍然阻断 cutover：

1. `CUT-R003`：当前未提供有效豁免清单，3 条附件缺失仍未恢复，因此附件恢复计划仍为 `blocked`。
2. `CUT-R005`：生产 `docker.env` 未请求 PostgreSQL 切换；真实 PostgreSQL app/worker 全栈运行和远程 Compose app 验证仍未完成。

结论不变：增强模板是为了让后续签收更严谨，不是自动放行。

## 2026-06-06 高并发烟测重复运行门禁记录

本轮新增通过的门禁证据：

1. `tools/high_concurrency_smoke.py` 不再用固定教师邮箱和固定学生学号作为唯一种子数据。
2. 同一 PostgreSQL 测试库中重复运行烟测时，可以通过 `run_id` 后缀生成新的教师、班级、课程、课堂和学生。
3. 教师登录改为使用本轮 seed 返回的动态账号，避免登录到旧测试账号导致验证结果串线。
4. 相关测试通过：`Ran 7 tests ... OK`。

仍然阻断 cutover：

1. `CUT-R003`：3 条附件缺失引用未恢复或签收豁免。
2. `CUT-R005`：生产 `docker.env` 未请求 PostgreSQL 切换；真实 PostgreSQL app/worker 全栈运行和远程 Compose app 验证仍未完成。

结论不变：该改进只让后续全栈验收更可重复，不代表阶段 4 已允许。
## 2026-06-06 数据库后端切换验收补充

最终切换不再接受“服务能访问”作为充分证据。阶段 4/5/7 必须同时满足以下后端确认条件：

1. 阶段 4 前：`tools/db_cutover_gate.py` 输出必须为 `ready`；若仍为 `blocked`，不得修改生产 `docker.env` 中的 `DB_ENGINE`、`DATABASE_URL`、`POSTGRES_BACKEND_READY`。
2. 阶段 5 后：`tools/deploy/postflight.ps1 -ExpectedDbEngine postgres -CheckPostgres` 必须通过，且报告中 `health-database-backend.json`、`remote-database-backend.json` 均显示 PostgreSQL。
3. 阶段 5 后：`/api/internal/health` 的 `database_backend.configured` 必须为 `true`，否则判定为配置未闭环。
4. 阶段 5 后：postflight 不得在报告中写入真实 PostgreSQL 密码或未打码连接串；如检查器发现明文 URL，必须阻断验收。
5. 阶段 7 恢复后：必须执行 `tools/deploy/postflight.ps1 -ExpectedDbEngine sqlite` 并通过，证明服务已回到 SQLite 权威库。
6. 任一阶段失败时，必须停止推进、保留报告和 dump，不得删除或移动 `/lanshare/data` 来“清理现场”。

本轮新增并通过的门禁支撑：

1. `tools/deploy/health_backend_check.py` 可校验 health payload 的数据库后端、配置状态和连接串打码。
2. `tools/db/run_dual_backend_tests.ps1` 支持本地 `-ExpectedDbEngine`，用于 Win11 临时 app 的 SQLite/PostgreSQL 双后端烟测。
3. `tools/deploy/postflight.ps1` 支持 `-ExpectedDbEngine`，用于远程 Linux Docker Compose app/worker 切换后验收和 SQLite 恢复验收。

当前状态仍未放行生产切换：`CUT-R003` 与 `CUT-R005` 未消除前，阶段 4 不得执行。
## 2026-06-06 health 后端验收增强后的门禁结果

本轮在新增 health 数据库后端检查后重新执行门禁与回归：

1. 焦点测试 `tests.test_deploy_check_tools tests.test_db_cutover_gate` 通过：`Ran 13 tests ... OK`。
2. P01 回归集合通过：`Ran 235 tests ... OK`。
3. 本地双后端工具链脚本通过：`tools/db/run_dual_backend_tests.ps1 -SkipApiSmoke`，`Ran 37 tests ... OK`。
4. cutover gate 重新生成后仍为 `status=blocked`。
5. 当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`。
6. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
7. dry run 通过且未上传、未触碰 Docker Compose；本轮 deployable files 为 `663`，归档约 `5.22 MB`。

结论：新增验收逻辑提高了阶段 5/7 的可验证性，但没有放宽生产切换条件；当前仍不得执行阶段 4。
## 2026-06-06 聊天 gate 后最终门禁复核

聊天运行时 PostgreSQL schema 和 upsert 增强后，已重新执行完整复核：

1. P01 回归集合通过：`Ran 238 tests ... OK`。
2. schema gate 输出：`PostgreSQL schema verified: 43/43 required tables`。
3. cutover gate 重新生成后仍为 `status=blocked`。
4. 当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`。
5. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
6. dry run 通过且未上传、未触碰 Docker Compose；deployable files 为 `663`，归档约 `5.22 MB`。
7. 敏感配置扫描未发现真实 PostgreSQL 密码或生产连接串进入代码、文档、报告或日志。

结论：聊天运行时风险进一步降低，但最终 gate 仍未放行；不得修改生产 `docker.env` 启用 PostgreSQL。
## 2026-06-06 邮件 worker gate 增量结果

本轮新增通过的门禁支撑：

1. PostgreSQL required schema 从 `43/43` 扩展为 `45/45 required tables`。
2. 新增 required tables：`teacher_email_configs`、`email_worker_heartbeats`。
3. `email_outbox` required columns 增加 `attempt_count`、`sent_at`、`last_error`。
4. `python -m unittest tests.test_db_postgres_schema tests.test_email_notification_queue_claim tests.test_api_contract_schemas` 通过，`Ran 20 tests ... OK`。
5. `python -m py_compile classroom_app/db/postgres_schema.py classroom_app/services/email_notification_service.py` 通过。

当前结论不变：该项降低 health/mailer 在 PostgreSQL app/worker 全栈运行时的失败风险，但 `CUT-R003` 与 `CUT-R005` 未完全消除前，阶段 4 仍不得执行。
## 2026-06-06 邮件 gate 后最终门禁复核

邮件 worker schema gate 增强后，已重新执行完整复核：

1. P01 回归集合通过：`Ran 239 tests ... OK`。
2. schema gate 输出：`PostgreSQL schema verified: 45/45 required tables`。
3. cutover gate 重新生成后仍为 `status=blocked`。
4. 当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`。
5. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
6. dry run 通过且未上传、未触碰 Docker Compose；deployable files 为 `663`，归档约 `5.23 MB`。
7. 敏感配置扫描未发现真实 PostgreSQL 密码或生产连接串进入代码、文档、报告或日志。

结论：邮件 worker 前置风险进一步降低，但最终 gate 仍未放行；不得修改生产 `docker.env` 启用 PostgreSQL。
## 2026-06-06 AI 聊天 gate 增量结果

本轮新增通过的门禁支撑：

1. PostgreSQL required schema 从 `45/45` 扩展为 `47/47 required tables`。
2. 新增 required tables：`ai_chat_messages`、`ai_psychology_profiles`。
3. `ai_chat_messages` required columns 增加对 `thinking_content`、`final_answer`、`attachments_json` 的硬校验。
4. `ai_psychology_profiles` required columns 增加对 `hidden_premise_prompt`、`support_strategy`、`raw_payload` 的硬校验。
5. `python -m unittest tests.test_db_postgres_schema tests.test_api_contract_schemas` 通过，`Ran 14 tests ... OK`。
6. `python -m py_compile classroom_app/db/postgres_schema.py classroom_app/routers/ai.py` 通过。

当前结论不变：该项降低 AI 聊天在 PostgreSQL app 全栈运行时的失败风险，但 `CUT-R003` 与 `CUT-R005` 未完全消除前，阶段 4 仍不得执行。
## 2026-06-06 聊天运行时 gate 增量结果

本轮新增通过的门禁支撑：

1. PostgreSQL required schema 从 `40/40` 扩展为 `43/43 required tables`，新增 `chat_logs`、`chat_log_migrations`、`discussion_attachments`。
2. `ensure_chat_log_schema()` 在 PostgreSQL 下只读校验 schema，不执行 SQLite 兼容 DDL。
3. `chat_log_migrations` 在 PostgreSQL 下使用 `ON CONFLICT` upsert，不再触发 `INSERT OR REPLACE`。
4. `python -m unittest tests.test_remaining_postgres_write_paths tests.test_db_postgres_schema` 通过。
5. `python -m py_compile classroom_app/services/chat_handler.py classroom_app/db/postgres_schema.py` 通过。

当前结论不变：`CUT-R003` 和 `CUT-R005` 未完全消除前不得进入阶段 4；本项只是继续降低 `CUT-R005` 中的 app/worker 全栈运行风险。
## 2026-06-06 AI gate 后最终门禁复核

AI 聊天 runtime schema gate 增强后，已重新执行完整复核：

1. P01 回归集合通过：`Ran 240 tests ... OK`。
2. schema gate 输出：`PostgreSQL schema verified: 47/47 required tables`。
3. cutover gate 重新生成后仍为 `status=blocked`。
4. 当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`。
5. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
6. dry run 通过且未上传、未触碰 Docker Compose；deployable files 为 `663`，归档约 `5.23 MB`。
7. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误。
8. 敏感配置扫描仅命中占位值、协议校验和测试用假连接串，未发现真实 PostgreSQL 密码或生产连接串。

结论：AI 聊天运行时 schema 风险进一步降低，但最终 gate 仍未放行；不得修改生产 `docker.env` 启用 PostgreSQL，不得执行阶段 4，不得触碰或破坏线上 `/lanshare/data`。
## 2026-06-06 worker/runtime schema gate 后最终门禁复核

本轮继续降低 PostgreSQL app/worker 全栈运行风险后，已重新执行完整复核：

1. PostgreSQL required schema 从 `47/47` 扩展为 `57/57 required tables`。
2. 新增覆盖 wrong-summary AI cache、行为追踪状态/画像、智慧教室凭据/课表/签到、智能考勤任务/学生建议任务。
3. P01 回归集合通过：`Ran 243 tests ... OK`。
4. schema gate 输出：`PostgreSQL schema verified: 57/57 required tables`。
5. cutover gate 重新生成后仍为 `status=blocked`。
6. 当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`。
7. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
8. dry run 通过且未上传、未触碰 Docker Compose；deployable files 为 `663`，归档约 `5.23 MB`。
9. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误。
10. 敏感配置扫描仅命中占位值、协议校验和测试用假连接串，未发现真实 PostgreSQL 密码或生产连接串。

结论：该项继续缩小 `CUT-R005`，但最终 gate 仍未放行。`CUT-R003` 和 `CUT-R005` 未完全消除前，仍不得修改生产 `docker.env`，不得执行阶段 4，不得声称生产 PostgreSQL 迁移成功，不得触碰或破坏线上 `/lanshare/data`。

## 2026-06-06 75/75 schema gate 后最终门禁复核

本轮在 account/support/integration 与课堂协作/实时互动 schema gate 补强后，已重新执行最终门禁相关复核。

新增通过的门禁支撑：

1. PostgreSQL required schema 从 `57/57` 扩展为 `75/75 required tables`。
2. 新增覆盖学生登录审计、学生找回密码、课堂待办、反馈、反馈附件、教师 Git 凭据、教务系统凭据、教务教学地点。
3. 新增覆盖小组协作、成员、文件、小组提交、互评，以及课堂实时活动、选项、回答、问答、求助信号。
4. 课堂协作和实时互动属于高频课堂写入路径，已纳入启动前只读 schema gate，但仍需真实 PostgreSQL app/worker 全栈运行和远程 Compose app 验证。

本轮复核结果：

1. 聚焦 account/support/integration 集合通过：`Ran 33 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 65/65 required tables`。
2. 聚焦 classroom collaboration/live 集合通过：`Ran 26 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 75/75 required tables`。
3. 本地 P01 回归集合通过：`Ran 225 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 75/75 required tables`。
4. `python tools\db_cutover_gate.py --json-output .codex-temp\db-cutover-gate-current\cutover-gate.json --markdown-output .codex-temp\db-cutover-gate-current\cutover-gate.md` 已重新生成门禁报告，结果仍为 `status=blocked`。
5. 当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`。
6. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
7. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 `663`，归档约 `5.24 MB`，明确未上传文件、未触碰 Docker Compose。
8. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误。
9. 严格敏感配置复核后，命中项均归类为协议校验、脱敏 health fixture 或单元测试假连接串，未发现真实 PostgreSQL 密码或生产连接串进入代码、文档或报告。

仍然阻断 cutover：

1. `CUT-R003`：3 条缺失附件引用仍未恢复原始文件，也没有有效业务签收豁免。
2. `CUT-R005`：真实生产 `docker.env` 未请求 PostgreSQL cutover；且真实 PostgreSQL app/worker 全栈运行、远程 Compose app 验证和观察窗口 postflight 仍未完成。

结论不变：当前只允许继续阶段 1-3，即先部署数据库、迁移 SQLite 副本、脚本直连验证数据完整性和关键接口依赖数据。不得进入阶段 4，不得修改生产 `docker.env` 启用 `DB_ENGINE=postgres`，不得声称生产 PostgreSQL 迁移成功，不得触碰或破坏线上 `/lanshare/data`。

## 2026-06-06 113/113 schema gate 后最终门禁复核

本轮在静态 SQLite schema 表全量纳入 PostgreSQL required gate 后，重新记录最终门禁状态。

新增通过的门禁支撑：

1. PostgreSQL required schema 从 `75/75` 扩展为 `113/113 required tables`。
2. `classroom_app/db/schema_*.py` 中静态声明的 113 张业务表，已全部进入 `REQUIRED_POSTGRES_TABLES`。
3. `tests/test_db_postgres_schema.py` 已加入自动库存测试，后续新增 SQLite schema 表但未纳入 PostgreSQL gate 会直接失败。
4. 本轮只增强只读 schema gate，不写入生产数据库，不修改生产 `docker.env`，不触碰线上 `/lanshare/data`。

复核结果：

1. 聚焦 Agent/博客/社交集合通过：`Ran 51 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 94/94 required tables`。
2. 聚焦学习/上传/签名集合通过：`Ran 41 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 113/113 required tables`。
3. 静态 schema 差集复核为 `sqlite_schema_tables=113`、`required_postgres_tables=113`、`missing_from_required_count=0`。
4. 本地 P01 回归集合通过：`Ran 231 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 113/113 required tables`。
5. cutover gate 重新生成后仍为 `status=blocked`，当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`。
6. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
7. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 `663`，归档约 `5.24 MB`，明确未上传文件、未触碰 Docker Compose。
8. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误；严格敏感扫描剩余命中仅为 `postgres.py` 的协议校验字面量，未发现真实 PostgreSQL 密码或生产连接串。

仍然阻断 cutover：

1. `CUT-R003` 仍未消除：3 条缺失附件引用仍需恢复原始文件或形成有效业务签收豁免。
2. `CUT-R005` 仍未消除：虽然静态 schema gate 已全覆盖，但真实 PostgreSQL app/worker 全栈运行、远程 Compose app 验证、观察窗口 postflight、生产 `docker.env` 受控切换均未完成。

结论不变：不得进入阶段 4，不得修改生产 `docker.env` 启用 PostgreSQL，不得声称生产迁移成功；当前只允许继续阶段 1-3 的数据库层部署、迁移副本和脚本直连验证。

## 2026-06-06 118/118 schema gate 后最终门禁复核

本轮将迁移/修复层的登录态与组织作用域支撑表纳入 PostgreSQL required schema gate 后，重新记录最终门禁状态。

新增通过的门禁支撑：

1. PostgreSQL required schema 从 `113/113` 扩展为 `118/118 required tables`。
2. 新增覆盖 `user_sessions`、`organization_schools`、`organization_colleges`、`organization_departments`、`teacher_organization_memberships`。
3. 该项直接服务于登录态可用性和权限边界完整性，符合 `identity -> organization scope -> ownership -> classroom assignment/publication -> action-specific permission` 的迁移验收顺序。

复核结果：

1. 聚焦组织/会话集合通过：`Ran 46 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 118/118 required tables`。
2. 本地 P01 回归集合通过：`Ran 246 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 118/118 required tables`。
3. cutover gate 重新生成后仍为 `status=blocked`，当前 blocker 仍为 `CUT-R003` 和 `CUT-R005`。
4. 安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
5. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 `663`，归档约 `5.24 MB`，明确未上传文件、未触碰 Docker Compose。
6. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示，无空白错误；严格敏感扫描剩余命中仅为 `postgres.py` 的协议校验字面量，未发现真实 PostgreSQL 密码或生产连接串。

仍然阻断 cutover：`CUT-R003` 和 `CUT-R005` 尚未消除；不得进入阶段 4，不得修改生产 `docker.env`，不得声称生产迁移成功。

## 2026-06-06 运行时 SQL 方言门禁后最终门禁复核

本轮在 PostgreSQL runtime 方言审计后，新增一项可持续门禁，但最终 cutover 仍不放行。

新增通过的门禁支撑：

1. `tests/test_db_sql_dialect_guard.py` 已将 service/router 中的 SQLite-only SQL 命中点纳入受控清单。
2. 新增 SQLite-only SQL 命中时，测试会要求先进行引擎分支审计、补充 PostgreSQL 用例，再更新 allowlist。
3. `wrong_question_summary_service` 对非支持引擎显式 fail-fast，不再可能因异常 engine 值进入 SQLite DDL fallback。
4. 本轮审计确认现有 `INSERT OR IGNORE`、`PRAGMA`、`sqlite_master`、运行时 `CREATE/ALTER TABLE` 命中点均处于 PostgreSQL 分支保护或只读 schema 校验路径。

阶段边界复核：

1. 当前允许继续阶段 1：远程只部署 PostgreSQL 数据库服务，app 仍连接 SQLite。
2. 当前允许继续阶段 2：从 SQLite 快照迁移数据进入 PostgreSQL，不破坏原始 SQLite 数据。
3. 当前允许继续阶段 3：脚本直连 PostgreSQL 验证数据完整性、附件引用、关键接口依赖数据和关键查询性能。
4. 当前禁止阶段 4：不得修改生产 `docker.env` 切换 `DB_ENGINE=postgres`，不得重启 app 指向新库。
5. 当前禁止阶段 5/6/7 的生产演练宣称：未切换前只能准备验证、修复和恢复脚本，不能声称已完成生产验证或恢复闭环。

最新局部验证：

1. `python -m unittest tests.test_db_sql_dialect_guard tests.test_wrong_question_summary_service tests.test_remaining_postgres_write_paths tests.test_academic_sync_postgres_writes tests.test_file_related_postgres_writes tests.test_materials_postgres_writes tests.test_postgres_metadata_helpers` 通过，`Ran 42 tests ... OK`。
2. 完整 P01 回归集合通过，`Ran 247 tests ... OK`，schema gate 输出 `PostgreSQL schema verified: 118/118 required tables`。
3. cutover gate 重新生成后仍为 `status=blocked`，安全字段仍为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
4. `deployment\deploy_remote.ps1 -DryRun` 通过，deployable files 为 `664`，归档约 `5.25 MB`，明确未上传文件、未触碰 Docker Compose。
5. `git diff --check` 通过，仅有 Win11 工作区 LF/CRLF 提示；敏感扫描未发现真实 PostgreSQL 密码或生产连接串。

仍然阻断 cutover：

1. `CUT-R003`：3 条缺失附件引用仍未恢复原始文件，也没有业务签收豁免。
2. `CUT-R005`：真实 PostgreSQL app/worker 全栈运行、远程 Compose app 验证、观察窗口 postflight、生产 `docker.env` 受控切换仍未完成。

结论：本轮门禁增强让 SQL 方言风险可追踪，但不改变最终状态。阶段 4 前必须继续保护线上 `/lanshare/data` 和原始 SQLite；任何切换动作必须带有快照、回滚配置、PostgreSQL 验证报告和 SQLite 恢复路径。

## 2026-06-06 pre-cutover/final-cutover gate 拆分记录

本轮修正 gate 阶段语义，避免“阶段 4 才应该发生的配置切换”反过来阻断阶段 1-3 的验收判断。

新增规则：

1. `tools/db_cutover_gate.py --phase pre-cutover` 用于阶段 1-3：数据库服务准备、SQLite 快照迁移、PostgreSQL 直连验证、附件完整性、备份恢复和性能基线。
2. pre-cutover 阶段下，真实 `docker.env` 仍为 SQLite 是正常状态，记录为 `CUT-W004`，不作为 blocker。
3. pre-cutover 阶段下，如果真实 `docker.env` 已经请求 PostgreSQL cutover，会触发 `CUT-R011`，因为这表示配置切换过早发生。
4. `tools/db_cutover_gate.py --phase final-cutover` 保留最终切换判断；此时真实 `docker.env` 必须显式请求 PostgreSQL，否则继续触发 `CUT-R005`。
5. 默认 phase 保持 `final-cutover`，避免旧命令误把最终切换判定放宽。

当前生成结果：

1. pre-cutover gate：`status=blocked`，blocker 仅剩 `CUT-R003`；warning 为 `CUT-W002` 和 `CUT-W004`。
2. final-cutover gate：`status=blocked`，blocker 为 `CUT-R003` 和 `CUT-R005`。
3. 两份报告安全字段均为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。

当前含义：

- 若能恢复缺失附件或取得有效业务签收豁免，pre-cutover gate 才可进入 `ready`，随后才能考虑阶段 4。
- final-cutover gate 只有在阶段 4 受控修改远程未提交 `docker.env` 并验证配置后才可能解除 `CUT-R005`。

本轮复核结果：

1. P01 回归集合通过：`Ran 250 tests ... OK`。
2. pre-cutover gate 重新生成后仍为 `status=blocked`，唯一 blocker 为 `CUT-R003`。
3. final-cutover gate 重新生成后仍为 `status=blocked`，blocker 为 `CUT-R003` 和 `CUT-R005`。
4. 两份 gate 安全字段均为 `production_data_modified=false`、`remote_data_modified=false`、`cutover_executed=false`。
5. `deployment\deploy_remote.ps1 -DryRun` 通过，未上传文件、未触碰 Docker Compose。
6. 真实密码、token、API key、连接串反扫通过，未进入代码、目标文档或当前 gate/preflight 报告。

## 2026-06-06 最终切换验收结论

用户已授权补齐 3 个缺失历史附件并继续推进生产切换。本轮执行后，T12 最终结论更新为：生产 PostgreSQL 切换已完成，当前权威数据库为 PostgreSQL。

最终验收结果：

1. PostgreSQL 服务在远程 Docker Compose 中启动并健康。
2. SQLite 到 PostgreSQL 生产数据副本迁移成功，迁移时点表数 `118`、总行数 `346662` 一致。
3. 缺失附件阻断已通过审计占位文件和签收豁免处理；文件完整性报告显示 `missing_submission_files=0`，但原始 3 个 doc 文件仍未找回。
4. app、mailer、ai、agent-worker、blog-crawler 已使用同一 PostgreSQL 配置。
5. `tools/deploy/postflight.ps1 -ExpectedDbEngine postgres -CheckPostgres` 通过，最新报告目录为 `.codex-temp/deploy-checks/postflight-20260606-112128`。
6. 首次失败切换已自动回滚，修复后第二次切换成功；恢复路径经过实际验证。
7. 本地 P01 回归集合通过：`Ran 252 tests ... OK`。

成功切换报告：

1. `.codex-temp/pg-remote-cutover/cutover-report-retry1.json`
2. `.codex-temp/deploy-checks/postflight-20260606-112128`

可恢复条件：

1. SQLite 切换前备份：`/tmp/lanshare-cutover-backups/20260606T025934Z/classroom-before-pg-cutover-20260606T025934Z.db`。
2. PostgreSQL 切换前 dump：`/lanshare/data/postgres-backups/pre-final-cutover-20260606T025934Z.dump`。
3. `.env`/`docker.env` 备份：`/lanshare/.env.pg-stage1-backup-20260606T023840Z`、`/lanshare/docker.env.pg-stage1-backup-20260606T023840Z`。

不得违反的后续红线：

1. 不得删除 `/lanshare/data/postgres` 或运行会移除 PostgreSQL 容器的 `--remove-orphans`。
2. 不得把切换前 SQLite 备份直接恢复为生产库，除非先冻结写入并导出 PostgreSQL 差异。
3. 不得把占位附件当作原始提交证据。
4. 不得把真实 PostgreSQL 密码、连接串、token、cookie 写入仓库或目标文档。
5. 后续任何业务修复都必须以 PostgreSQL 为当前权威库进行验证。

剩余非阻断事项：

1. 后台任务健康接口中的 `ok=false` 来自历史失败计数，需要单独运维清理或重放，不影响本次数据库后端验收。
2. 部署脚本已完成 PostgreSQL overlay 自动选择；后续人工 compose 操作仍必须避免 `--remove-orphans` 误删 PostgreSQL 服务。
