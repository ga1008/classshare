# 远程 PostgreSQL 分阶段部署、验证、切换、恢复手册

## 目标

把远程 PostgreSQL 部署拆成可暂停、可验收、可恢复的阶段。每一阶段只推进一个风险面，上一阶段未通过时不得进入下一阶段。整个过程必须保护线上 `/lanshare/data`，不破坏原始 SQLite 数据和运行时文件。

## 全局硬约束

1. 不允许删除、覆盖、移动远程 `/lanshare/data`。
2. 不允许在迁移验证未通过前修改生产 `docker.env` 为 `DB_ENGINE=postgres`。
3. 不允许将真实 `POSTGRES_PASSWORD`、`DATABASE_URL`、Cookie、Token 写入仓库或报告。
4. 所有临时操作使用 `/tmp/lanshare-*` 或受控 PostgreSQL 持久目录。
5. 每一步都必须生成机器可读报告和人可读摘要。
6. 失败时保留现场报告，先停止推进，再决定修复或恢复。

## 阶段 0：本地可行性验证

目的：在 Win11 本地证明工具链、迁移 SQL、报告和门禁逻辑可运行。

前置条件：

- 本地 Python 环境可运行。
- 使用 `.codex-temp` 下的 SQLite 副本。
- 不连接真实生产写库。

执行内容：

1. 生成 schema、migration readiness、file integrity、backup rollback、performance acceptance、cutover gate 报告。
2. 运行相关单元测试。
3. 如果本地缺少 Docker/psql，记录缺口，并使用远程临时环境补足数据层演练。

通过条件：

- 本地可运行测试通过。
- 所有报告路径明确。
- cutover gate 能准确输出 `blocked` 或 `ready`，不能误放行。

失败处理：

- 修复本地工具或报告逻辑。
- 不允许因为本地缺少 Docker 而直接跳过远程 PostgreSQL 演练。

## 阶段 1：先部署数据库

目的：只部署 PostgreSQL 服务，不切换 app。

前置条件：

- `docker-compose.postgres.yml` 已通过 preflight。
- 远程 Docker/Compose 可用。
- PostgreSQL 镜像已准备好。
- 生产 SQLite 仍保持权威数据库。

执行内容：

1. 在远程 Docker Compose 中启动 PostgreSQL 服务。
2. 设置真实 `POSTGRES_PASSWORD` 到远程未提交 `docker.env` 或受控环境文件。
3. 确认 PostgreSQL 数据目录、备份目录、权限和健康检查。
4. 不修改 `DB_ENGINE=sqlite`。

通过条件：

- `docker compose ps` 显示 postgres healthy。
- `pg_isready` 通过。
- PostgreSQL 数据目录位于预期持久目录。
- app 仍连接 SQLite，业务不受影响。

失败处理：

- 停止 PostgreSQL 容器。
- 清理仅属于 PostgreSQL 新服务的临时数据目录。
- 不触碰 `/lanshare/data/classroom.db`。

## 阶段 2：迁移数据进入数据库

目的：把 SQLite 数据副本导入 PostgreSQL，不破坏原始数据。

前置条件：

- 阶段 1 通过。
- 当前生产 SQLite 已做只读快照。
- 写入冻结或维护窗口策略已确认。

执行内容：

1. 从 SQLite 快照生成 PostgreSQL SQL 包。
2. 导入 schema。
3. 导入 data。
4. 创建 constraints/indexes。
5. 执行 verify counts。
6. 对序列执行 setval。
7. 生成迁移报告。

通过条件：

- 源 SQLite `quick_check=ok`。
- PostgreSQL 表数量一致。
- 关键表行数一致。
- 外键约束创建成功。
- 序列对齐。
- 数据迁移报告 `status=ok`。

失败处理：

- 删除或重建 PostgreSQL 目标库。
- 保留失败日志。
- 继续使用 SQLite 原库。
- 不修改 app 配置。

## 阶段 3：脚本直连验证数据完整性和关键接口依赖

目的：在 app 切换前，直接连接 PostgreSQL 验证数据是否足以支撑关键业务。

前置条件：

- 阶段 2 通过。
- 有 PostgreSQL 只读验证账号或受控连接串。

执行内容：

1. 校验表行数、外键、唯一约束、序列。
2. 校验关键业务抽样：教师、学生、班级、课堂、作业、提交、附件、课程资料、邮件队列、AI/Agent 队列。
3. 校验附件元数据：数据库引用必须能解析到真实文件，或有签收豁免。
4. 执行关键 SQL explain/analyze。
5. 生成接口依赖数据报告。

通过条件：

- 关键表抽样全部通过。
- 附件缺失为 0，或签收豁免有效。
- 关键查询耗时在 T11 阈值内。
- 没有阻断级数据异常。

失败处理：

- 若是可修复数据问题，修复 PostgreSQL 迁移脚本或重新导入。
- 若是源数据缺口，回到 T07/T12 形成恢复或豁免。
- 不允许切换 app。

补充注意：

- 本地开发测试环境是 Win11；若本机没有 `docker`、`psql`，本地只作为代码、报告和单元测试验证环境，不得把缺失工具链当作阶段 3 通过。
- 远程 Linux 环境由 Docker Compose 管理且无外网访问能力；PostgreSQL 镜像、迁移脚本和验证脚本必须提前随部署包或离线镜像准备好。
- 阶段 3 报告必须明确记录：源 SQLite 快照路径、目标 PostgreSQL 数据库标识、迁移批次、附件完整性、关键业务抽样、关键查询耗时、失败项和是否允许进入阶段 4。
- 阶段 3 结束时必须执行 `python tools/db_cutover_gate.py --phase pre-cutover ...`。只有 pre-cutover gate 为 `ready` 时，才允许进入阶段 4。

## 阶段 4：更改配置文件切换到新数据库并重启

目的：在所有数据和脚本验证通过后，切换 app/worker 到 PostgreSQL。

阶段边界：

- 阶段 4 之前，真实 `docker.env` 保持 SQLite 是正确状态。
- 阶段 4 开始后，才允许写入远程未提交 `docker.env`，设置 `DB_ENGINE=postgres`、`DATABASE_URL`、`POSTGRES_BACKEND_READY=true`。
- 阶段 4 修改配置后，必须执行 `python tools/db_cutover_gate.py --phase final-cutover ...` 和 `tools/deploy/postflight.ps1 -ExpectedDbEngine postgres -CheckPostgres`。

前置条件：

- 阶段 3 通过。
- cutover gate 为 `ready`。
- app PostgreSQL adapter 已完成并通过全栈测试。
- `init_database()` 在 PostgreSQL 模式下已验证只执行 schema 校验，不执行 SQLite 初始化器。
- 已备份 SQLite、运行时数据、PostgreSQL 当前状态。

执行内容：

1. 冻结写入或进入维护窗口。
2. 暂停写数据库的 worker。
3. 修改远程受控配置：
   - `DB_ENGINE=postgres`
   - `DATABASE_URL=...`
   - `POSTGRES_BACKEND_READY=true`
4. 重启 app、mailer、blog-crawler、agent-worker。
5. 执行 postflight。

通过条件：

- app health 正常。
- metrics 显示数据库后端为 PostgreSQL。
- 所有 worker 无数据库连接错误。
- 没有自动回退 SQLite。

失败处理：

- 若 app 无法启动，立即恢复原配置为 SQLite，重启 app/worker。
- 保留 PostgreSQL 数据和日志用于分析。
- 不删除 SQLite 备份。

## 阶段 5：验证

目的：确认切换后的真实业务可用。

验证内容：

1. 登录。
2. 教师首页。
3. 学生课堂入口。
4. 作业详情。
5. 草稿保存。
6. 作业提交。
7. 附件上传、下载、删除。
8. 教师查看提交和批改。
9. 邮件队列。
10. AI grading、wrong summary、private message AI job、material AI import、session material generation task。
11. Agent task worker。
12. 关键监控指标和慢查询。

通过条件：

- 核心页面无 5xx。
- 关键 API 返回符合契约。
- 新写入只进入 PostgreSQL。
- 队列无重复领取。
- PostgreSQL 连接、锁等待、CPU、内存、磁盘 IO 在阈值内。

失败处理：

- 将问题分为可热修复、需冻结写入修复、需恢复 SQLite 三类。
- 不允许一边失败一边继续开放写入。

## 阶段 6：修复

目的：对切换后发现但可控的问题做最小修复。

允许修复：

- 缺失索引。
- 单个 SQL 方言问题。
- 配置超时或连接池参数。
- 非破坏性数据修正。

禁止修复：

- 直接删除生产数据。
- 未备份情况下批量更新关键表。
- 临时关闭外键约束后继续运行。
- 修改线上附件目录来掩盖迁移错误。

通过条件：

- 修复前有备份。
- 修复 SQL 有审查和回滚方案。
- 修复后重复阶段 5 验证。

## 阶段 7：恢复

目的：当 PostgreSQL 切换不可用或风险不可控时，恢复到 SQLite 权威库。

触发条件：

- app 无法稳定连接 PostgreSQL。
- 核心登录、课堂、作业、提交不可用。
- 新写入读写不一致。
- 队列重复执行或严重积压。
- 数据完整性风险不可控。

恢复步骤：

1. 冻结写入。
2. 停止 worker。
3. 记录 PostgreSQL 当前状态并执行 dump。
4. 判断 PostgreSQL 是否已有新业务写入。
5. 如果无新写入，恢复 `DB_ENGINE=sqlite` 并重启 app/worker。
6. 如果已有新写入，先导出差异并制定补偿方案，不得直接丢弃。
7. 执行 SQLite postflight。
8. 发布恢复结论和遗留问题清单。

通过条件：

- app 恢复 SQLite 后核心业务可用。
- 未丢失切换窗口内需要保留的新数据，或有明确补偿记录。
- 所有恢复操作有报告。

## 当前状态

截至 2026-06-05：

- 阶段 0 已完成可运行部分。
- 阶段 1 已通过远程临时 Compose PostgreSQL 数据层演练，但生产 PostgreSQL 服务尚未正式作为持久服务启用。
- 阶段 2 已在远程临时 PostgreSQL 中用快照完成装载、约束、verify counts、dump/restore。
- 阶段 3 已完成数据层脚本验证的一部分，仍有 3 条附件缺失阻塞。
- PostgreSQL 基础连接 adapter 与启动 schema 只读校验已开始落地，邮件 outbox/Agent task/blog crawler/wrong summary/private message AI job/material AI import/session material generation task 领取路径和 AI grading token guard 已有增量；但 app/worker 全栈 PostgreSQL 适配尚未完成。
- 邮件 outbox 入队、课程资料 folder/file 创建、课堂资料分配幂等写入、消息中心通知/私信/私信附件写入、material AI import 创建、session material generation 创建和 SQLite `BEGIN IMMEDIATE` 收口已有增量；这些只能减少切换风险，不能替代阶段 4 前的完整 gate。
- 普通作业创建、试卷发布作业、草稿创建、学生提交记录创建、阶段试炼尝试/作业/证书写入和 AI 聊天 session 创建已开始使用统一主键返回 helper；仍需阶段 4 前的完整接口级 PostgreSQL 验证。
- 阶段 4-7 尚未执行。
- 生产 cutover gate 当前仍为 `blocked`。
## 2026-06-06 阶段顺序确认

远程服务器部署必须保持以下 7 个阶段的硬顺序，每一阶段都有可暂停点和失败出口：

1. **先部署数据库**：只启动 PostgreSQL 服务，验证容器健康、持久化目录、备份目录和凭据读取；此时 app/worker 继续使用 SQLite。
2. **迁移数据进入数据库**：从 SQLite 快照导入 PostgreSQL，不修改原始 SQLite，不覆盖、不删除、不移动 `/lanshare/data`。
3. **脚本直连验证**：在 app 切换前，直接连接 PostgreSQL 验证行数、约束、序列、附件引用、关键接口依赖数据和热点查询。
4. **更改配置并重启**：只有阶段 1-3 全部通过且 cutover gate 为 `ready`，才允许写入受控 `.env`/`docker.env`，设置 `DB_ENGINE=postgres`、`DATABASE_URL`、`POSTGRES_BACKEND_READY=true` 并重启 app/worker。
5. **验证**：执行 postflight、登录、教师首页、学生课堂、作业、提交、附件、邮件队列、AI/Agent/Blog worker、管理端创建课程/课堂、教务同步当前学期/课表/名单/任课考试/监考等验证。
6. **修复**：只允许最小、可回滚修复；修复前必须备份和冻结风险写入，修复后重复阶段 5。
7. **恢复**：若不可用或风险不可控，冻结写入，保留 PostgreSQL dump 和差异报告，切回 `DB_ENGINE=sqlite`，重启 app/worker 并执行 SQLite postflight。

当前阶段状态：

1. 阶段 0 本地 Python 可运行验证继续推进；新增管理端和教务同步 PostgreSQL 写路径单元测试已通过。
2. 阶段 1-3 只允许继续以受控数据库层演练推进，不允许直接进入阶段 4。
3. 阶段 4-7 尚未执行。
4. 生产 cutover gate 当前仍为 `blocked`，因此不得修改远程生产 `docker.env` 启用 PostgreSQL。

## 2026-06-06 后续执行记录

本轮在不触碰线上 `/lanshare/data`、不修改生产 `docker.env`、不执行阶段 4 切换的前提下，补充以下可验收证据：

1. 本地 PostgreSQL 适配回归集合已扩展并通过：`Ran 218 tests ... OK`，覆盖 Agent、反馈/表情/文件、消息中心、材料、账户/待办/考勤、行为事件、博客、课堂互动、协作、讨论附件、电子签名主记录、管理端、教务同步和既有数据库门禁工具。
2. `python tools\db_cutover_gate.py --json-output .codex-temp\db-cutover-gate-current\cutover-gate.json --markdown-output .codex-temp\db-cutover-gate-current\cutover-gate.md` 已重新生成门禁，状态仍为 `blocked`。
3. `deployment\deploy_remote.ps1 -DryRun` 已通过，生成 659 个待部署文件清单和 5.20 MB 代码归档；dry run 明确未上传文件、未触碰 Docker Compose。
4. 当前只允许继续阶段 1-3 的数据库层部署、迁移副本和脚本直连验证；阶段 4 配置切换仍必须等待 `CUT-R003`、`CUT-R005` 消除且 cutover gate 变为 `ready`。

## 2026-06-06 后续推进记录

本轮继续推进阶段 0 的本地可行性验证和阶段 4 前置代码收口：

1. app/service/router 普通写入路径已不再裸用 `cursor.lastrowid` 或 `last_insert_rowid()`。
2. 新增 `tests/test_remaining_postgres_write_paths.py`，覆盖本轮收口的聊天、资料 Git、作品集、签名申请、提交文件对齐、Blog crawler 写入路径。
3. P01 回归集合通过：`Ran 224 tests ... OK`。
4. cutover gate 重新生成后仍为 `blocked`，因此阶段 4-7 仍未执行。

可继续推进：

1. 远程阶段 1：部署 PostgreSQL 服务本身，验证容器、healthcheck、持久化目录、备份目录。
2. 远程阶段 2：将 SQLite 快照迁移进入 PostgreSQL，不修改原始 SQLite，不触碰 `/lanshare/data`。
3. 远程阶段 3：脚本直连验证行数、约束、序列、附件引用、关键接口依赖数据和热点查询。

仍禁止：

1. 不得修改生产 `docker.env` 设置 `DB_ENGINE=postgres`、`DATABASE_URL`、`POSTGRES_BACKEND_READY=true`。
2. 不得进入阶段 4 app/worker 切换。
3. 不得删除、覆盖、移动 `/lanshare/data`。

## 2026-06-06 元数据与部署 dry run 复核记录

本轮继续补强阶段 4 前置条件中的运行时元数据读取与部署预检证据：

1. `discussion_attachment_service.py`、`background_task_ledger_service.py`、`base_resource_modes_service.py`、`organization_management_service.py`、`organization_scope_service.py` 的 PostgreSQL 分支已开始使用 `information_schema` 做运行时表/列存在性检查，不再在 PostgreSQL 模式下执行 `sqlite_master` 或 `PRAGMA table_info`。
2. 新增 `tests/test_postgres_metadata_helpers.py`，并扩展 `tests/test_file_related_postgres_writes.py`、`tests/test_background_task_ledger.py`，证明上述元数据 helper 在 PostgreSQL 分支不会触发 SQLite 专属 SQL。
3. P01 当前本地回归集合通过：`Ran 229 tests ... OK`。
4. `deployment\deploy_remote.ps1 -DryRun` 最新通过，deployable files 为 660，代码归档约 5.21 MB；dry run 明确未上传文件、未触碰远程 Docker Compose。
5. `tools\db_cutover_gate.py` 重新生成后仍为 `blocked`，因此阶段 4 配置切换继续禁止。

当前可执行边界不变：可以继续准备或演练远程阶段 1-3，但只有 `CUT-R003`、`CUT-R005` 全部消除且 cutover gate 变为 `ready` 后，才允许修改生产 `docker.env` 并重启 app/worker。

## 2026-06-06 高并发烟测工具后部署预检记录

本轮在新增高并发烟测工具 PostgreSQL 主键返回适配后，重新执行 `deployment\deploy_remote.ps1 -DryRun`：

1. deployable files 为 662。
2. 代码归档约 5.21 MB。
3. dry run 明确未上传文件、未触碰远程 Docker Compose。
4. cutover gate 仍为 `blocked`，所以该 dry run 不能作为阶段 4 切换许可。

远程执行顺序仍必须保持：阶段 1 数据库服务、阶段 2 数据迁移副本、阶段 3 脚本直连验证，全部通过且 T12 gate ready 后才允许阶段 4。

## 2026-06-06 可重复烟测后部署预检记录

本轮在高并发烟测工具支持唯一 `run_id` 后，重新执行 `deployment\deploy_remote.ps1 -DryRun`：

1. deployable files 为 662。
2. 代码归档约 5.22 MB。
3. dry run 明确未上传文件、未触碰远程 Docker Compose。
4. cutover gate 仍为 `blocked`，因此仍不得修改生产 `docker.env` 或重启 app/worker 切到 PostgreSQL。
## 2026-06-06 数据库后端验收硬门槛补充

为落实“先部署数据库、迁移副本、脚本直连验证、再改配置重启、验证、修复、恢复”的远程顺序，本 runbook 增加以下强制检查：

1. 阶段 3 只能验证 PostgreSQL 数据层，不允许修改 app/worker 的生产配置；所有直接连接验证报告必须继续标记 `production_data_modified=false`、`remote_data_modified=false`。
2. 阶段 4 修改 `docker.env` 前，cutover gate 必须为 `ready`；真实 `DATABASE_URL` 和 `POSTGRES_PASSWORD` 只允许写入远程未提交环境文件，不得进入仓库、目标文档、日志或报告。
3. 阶段 5 postflight 必须执行 `tools/deploy/postflight.ps1 -ExpectedDbEngine postgres -CheckPostgres`，同时验证 HTTP `/api/internal/health` 与容器内 `database_backend_state()` 都报告 `engine=postgres` 且 `configured=true`。
4. 阶段 5 的 postflight 报告必须包含 `health-database-backend.json` 和 `remote-database-backend.json`；任一报告失败都视为切换失败，不得继续开放写入。
5. 阶段 7 恢复到 SQLite 后必须执行 `tools/deploy/postflight.ps1 -ExpectedDbEngine sqlite`，确认 HTTP health 和容器内状态都回到 SQLite，再恢复正常业务。
6. 如果 health payload 意外暴露明文 PostgreSQL URL，`tools/deploy/health_backend_check.py` 必须失败；报告中只允许出现打码后的连接信息。

该补充不改变当前结论：在 `CUT-R003` 和 `CUT-R005` 未消除前，不得进入阶段 4，不得修改生产 `docker.env` 启用 PostgreSQL。

## 2026-06-06 实际切换执行记录

本节记录已经完成的生产执行结果，后续再操作远程数据库必须以本节为最新基线。

执行顺序与结果：

1. 先部署数据库：PostgreSQL 服务已在远程 Docker Compose overlay 中启动并健康，持久化目录为 `/lanshare/data/postgres`，备份目录为 `/lanshare/data/postgres-backups`。
2. 迁移数据进入数据库：SQLite 快照导入 PostgreSQL 成功，导入时点表数 `118`、总行数 `346662` 与源快照一致，外键违规 `0`。
3. 直接脚本连接验证：直连 PostgreSQL 和一次性 app 容器校验通过，关键表行数、附件路径、占位附件签收、schema gate 均满足进入切换的条件。
4. 更改配置切换并重启：第一次切换暴露 row/scalar 兼容问题，脚本按预案回滚到 SQLite；修复后第二次切换成功，远程 app/worker 当前使用 PostgreSQL。
5. 验证：`tools/deploy/postflight.ps1 -ExpectedDbEngine postgres -CheckPostgres` 已通过，最新报告目录为 `.codex-temp/deploy-checks/postflight-20260606-112128`。
6. 修复：已完成 email worker health、background task ledger、submission file alignment、postflight Win11/SSH 引号兼容修复，并已重新部署。
7. 恢复：恢复流程在第一次失败时已被实际验证，回滚后 SQLite 可用；当前因第二次切换成功，没有执行最终恢复到 SQLite。

关键报告：

1. 数据库服务部署：`.codex-temp/pg-remote-stage1/postgres-service-verify.json`
2. PostgreSQL 导入：`.codex-temp/pg-remote-stage2/postgres-import.json`
3. 直连验证：`.codex-temp/pg-remote-stage3/postgres-direct-verify.json`
4. 首次失败与回滚：`.codex-temp/pg-remote-cutover/cutover-report.json`
5. 第二次成功切换：`.codex-temp/pg-remote-cutover/cutover-report-retry1.json`
6. 切换后 postflight：`.codex-temp/deploy-checks/postflight-20260606-112128`

恢复注意事项：

1. 切换前 SQLite 备份位于 `/tmp/lanshare-cutover-backups/20260606T025934Z/classroom-before-pg-cutover-20260606T025934Z.db`。
2. PostgreSQL 切换前 dump 位于 `/lanshare/data/postgres-backups/pre-final-cutover-20260606T025934Z.dump`。
3. 如果必须恢复 SQLite，先冻结写入，再导出 PostgreSQL 当前差异；切换成功后的 PostgreSQL 新写入不得被直接丢弃。
4. 不得为了消除 orphan 提醒而运行 `docker compose --remove-orphans`；当前部署脚本已自动选择 PostgreSQL overlay，但 PostgreSQL 容器仍是生产数据库依赖，人工 compose 操作必须显式保留。
5. `.env`/`docker.env` 中的真实数据库密码只保存在远程未提交配置中，不得复制到仓库或文档。
