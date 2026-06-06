# T09 - 远程 Linux Docker Compose 部署

## 目标

为 PostgreSQL 切换准备远程 Linux Docker Compose 部署方案，确保新增 PostgreSQL 服务不会破坏现有 SQLite 生产运行，也不会覆盖 `/lanshare/data`。

## 部署原则

1. 默认 `docker-compose.yml` 继续保持现有 SQLite 运行形态。
2. PostgreSQL 使用显式 overlay：`docker-compose.postgres.yml`。
3. 真实 `docker.env` 只有在 T12 gate ready 后才允许设置 `DB_ENGINE=postgres`。
4. PostgreSQL 数据目录必须挂载到受控持久目录，例如 `./data/postgres`。
5. 远程无外网时，镜像必须提前拉取或离线导入。
6. 部署脚本不得删除远程 `/lanshare/data`。

## 必须准备的文件

- `docker-compose.yml`
- `docker-compose.postgres.yml`
- `docker.env.example`
- `DOCKER_DEPLOYMENT.md`
- `tools/deploy/preflight.ps1`
- `tools/deploy/postflight.ps1`
- `tools/deploy/postgres_preflight.py`
- `deployment/deploy_remote.ps1`

## 验收条件

- [ ] `deployment/deploy_remote.ps1 -DryRun` 通过。
- [ ] 部署脚本明确保护 `/lanshare/data`。
- [ ] PostgreSQL overlay 有 healthcheck。
- [ ] 真实 `docker.env` 不提交仓库。
- [ ] postflight 能检查数据库后端状态。
- [ ] 切换前远程 Docker/Compose 版本已记录。

## 远程分阶段部署流程

远程部署必须按以下阶段执行，每个阶段失败都必须停止推进：

1. 先部署 PostgreSQL 数据库服务，只验证容器健康、持久化目录和备份目录，不切换 app。
2. 迁移 SQLite 快照数据进入 PostgreSQL，迁移过程不修改原始 SQLite，不触碰 `/lanshare/data`。
3. 使用脚本直连 PostgreSQL 验证表行数、外键、序列、附件引用和关键业务数据。
4. 在 cutover gate 为 `ready` 后，才修改远程配置文件切换数据库并重启 app/worker。
5. 重启后执行 postflight、关键 API、关键页面和 worker 验证。
6. 对可控问题做最小修复，并重复验证。
7. 若不可用或风险不可控，执行恢复步骤，优先保护原始 SQLite 和切换窗口内新写入。

详细执行手册见 `RUNBOOK-remote-postgres-staged-cutover.md`。

## 当前执行记录

已完成：

- 新增 `docker-compose.postgres.yml`，提供 PostgreSQL 服务、healthcheck 和持久化目录。
- 新增 `docker.env.example` PostgreSQL 配置模板，密码字段保持为空。
- 新增 `tools/deploy/postgres_preflight.py`。
- 更新 `deployment/deploy_remote.ps1`，保留 dry run 和数据目录保护。
- 远程 Docker 版本：`Docker version 29.4.0`。
- 远程 Compose 版本：`Docker Compose version v5.1.2`。
- 远程已能使用 `postgres:16-alpine` 镜像完成临时演练。

当前状态：部署准备可继续推进，但不得执行生产 PostgreSQL 切换。真实 `.env`/`docker.env` 的 PostgreSQL 密码只应在 gate ready 后写入远程未提交配置。
## 2026-06-06 增量部署约束

结合当前迁移进度，远程 Linux Docker Compose 部署只能按以下边界推进：

1. 可以先部署 PostgreSQL 服务本身，验证镜像、容器、healthcheck、持久化目录和备份目录。
2. 可以把 SQLite 快照迁移到 PostgreSQL 并做数据库层直连验证。
3. 不允许在 cutover gate 为 `blocked` 时修改生产 `docker.env` 的 `DB_ENGINE=postgres`、`DATABASE_URL`、`POSTGRES_BACKEND_READY=true`。
4. 远程无外网访问能力时，必须提前确认 `postgres:16-alpine` 等镜像已经存在或通过离线方式准备；不能在切换窗口才临时拉取。
5. 部署脚本和临时演练目录不得覆盖 `/lanshare/data`；所有临时实验目录必须位于 `/tmp/lanshare-*` 或明确的新 PostgreSQL 持久化目录。

当前只允许继续执行 dry run、preflight、数据库层演练和脚本验证，不允许生产 app/worker 切换。

## 2026-06-06 Dry Run 复核记录

本轮执行 `deployment\deploy_remote.ps1 -DryRun` 已通过：

1. 本地工具检查通过。
2. PostgreSQL deployment gate preflight 报告已生成到临时目录。
3. 部署清单生成 659 个文件。
4. 代码归档大小约 5.20 MB。
5. dry run 明确未上传文件，未触碰远程 Docker Compose。

该结果只证明部署脚本的预检、打包和清单路径可执行，不代表允许生产 cutover。当前仍不得修改生产 `docker.env` 启用 `DB_ENGINE=postgres`，也不得进入远程阶段 4 配置切换。

## 2026-06-06 最新 Dry Run 复核记录

在补充运行时元数据 helper 和测试文件后，再次执行 `deployment\deploy_remote.ps1 -DryRun` 通过：

1. deployable files 更新为 660。
2. 代码归档约 5.21 MB。
3. dry run 仍明确未上传文件、未触碰远程 Docker Compose。
4. 该结果只更新“部署包可生成、远程切换未发生”的证据，不改变 cutover 结论。

当前部署目标仍停留在可控预检与阶段 1-3 数据库层演练；生产 app/worker 切换必须等待 T12 门禁变为 `ready`。

## 2026-06-06 高并发烟测工具后 Dry Run 记录

在新增 `tools/high_concurrency_smoke.py` PostgreSQL 适配和 `tests/test_high_concurrency_smoke_postgres.py` 后，再次执行 `deployment\deploy_remote.ps1 -DryRun`：

1. deployable files 更新为 662。
2. 代码归档约 5.21 MB。
3. dry run 明确未上传文件，未触碰远程 Docker Compose。
4. PostgreSQL deployment gate preflight 报告已生成到临时目录。

该记录只证明最新工作区可以被部署脚本预检和打包，不代表生产 cutover 已允许或已执行。

## 2026-06-06 高并发烟测可重复运行后 Dry Run 记录

在高并发烟测工具改为唯一 `run_id` 种子后，再次执行 `deployment\deploy_remote.ps1 -DryRun`：

1. deployable files 仍为 662。
2. 代码归档约 5.22 MB。
3. dry run 明确未上传文件，未触碰远程 Docker Compose。

该结果继续证明预检和打包路径可执行，但不改变 T12 gate 的阻断状态。
## 2026-06-06 远程 Docker Compose postflight 补充

远程 Linux Docker Compose 环境在无外网访问能力的前提下，切换前必须确认 PostgreSQL 镜像、app 镜像、依赖包和部署归档都已提前准备，不能在维护窗口内依赖在线拉取。

阶段化部署验收补充：

1. 阶段 1 只启动 PostgreSQL 服务，不修改 app/worker 的数据库配置。
2. 阶段 2 只把 SQLite 快照导入 PostgreSQL，不覆盖、不删除、不移动 `/lanshare/data`。
3. 阶段 3 使用直连脚本验证 PostgreSQL 数据完整性和关键业务依赖数据。
4. 阶段 4 只有 T12 gate 为 `ready` 后才允许写入远程未提交 `docker.env`，设置 `DB_ENGINE=postgres`、`DATABASE_URL`、`POSTGRES_BACKEND_READY=true` 并重启 app/worker。
5. 阶段 5 必须执行 `tools/deploy/postflight.ps1 -ExpectedDbEngine postgres -CheckPostgres`，确认 HTTP health 与容器内状态均为 PostgreSQL。
6. 阶段 7 恢复后必须执行 `tools/deploy/postflight.ps1 -ExpectedDbEngine sqlite`，确认 app/worker 实际回到 SQLite。

真实数据库密码只允许记录在远程受控 `.env`/`docker.env`，不得提交到仓库或写入 P01 文档。

## 2026-06-06 gate 阶段语义补充

远程 Docker Compose 切换现在按两类 gate 记录：

1. `pre-cutover`：验证阶段 1-3 是否可以进入阶段 4。此时 `docker.env` 仍为 SQLite 是正常状态，只记录 `CUT-W004`。
2. `final-cutover`：验证阶段 4 后的最终切换条件。此时 `docker.env` 必须显式请求 PostgreSQL，否则 `CUT-R005` 保持阻断。

当前最新结果：

1. pre-cutover gate 仍为 `blocked`，唯一 blocker 为 `CUT-R003`。
2. final-cutover gate 仍为 `blocked`，blocker 为 `CUT-R003` 和 `CUT-R005`。
3. 两个 gate 均确认未修改生产数据、未修改远程数据、未执行 cutover。

## 2026-06-06 远程 PostgreSQL Compose 上线结果

远程 Docker Compose PostgreSQL 已部署并成为当前生产数据库后端。

部署结果：

1. `docker-compose.postgres.yml` overlay 已在远程使用，PostgreSQL 容器 `lanshare-postgres-1` 健康。
2. PostgreSQL 数据目录：`/lanshare/data/postgres`。
3. PostgreSQL 备份目录：`/lanshare/data/postgres-backups`。
4. app、mailer、ai、agent-worker、blog-crawler 均已重新启动并连接 PostgreSQL。
5. `database_backend_state()` 返回 `engine=postgres`、`configured=true`，连接串在报告中已脱敏。
6. 最新 postflight 报告目录：`.codex-temp/deploy-checks/postflight-20260606-112128`。

部署脚本注意事项：

1. `deployment/deploy_remote.ps1` 已在远程 `DB_ENGINE=postgres` 且 `docker-compose.postgres.yml` 存在时自动使用 `docker-compose.yml + docker-compose.postgres.yml`。
2. 仍不得使用 `--remove-orphans` 删除 PostgreSQL 容器；人工 compose 操作也必须显式带上 PostgreSQL overlay。
3. 最新真实部署输出已显示 `COMPOSE_FILES=docker-compose.yml + docker-compose.postgres.yml`，并且 `lanshare-postgres-1` 保持 healthy。
4. 远程无外网访问能力的约束仍然有效；任何新镜像或依赖都必须提前准备，不得在维护窗口内临时依赖公网拉取。
