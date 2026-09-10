# DSH 冻结版本上线门禁进展

本文件续接本地收尾检查点，记录实际完成的剩余上线门禁，不扩展功能。产品源码固定为 `476fc7bdb3a0a4758a97e33bda496b6bcc812594`；独立 worktree 保留主目录干净提交的原始文件字节，避免换行转换改变来源摘要。后续证据和测试新增不改变该产品源码。

## 已完成

- 教学上下文锁原生 PostgreSQL 7/7 通过、无跳过：6 组观察到 `pg_blocking_pids` 真实等待，1 组真实 NOWAIT 冲突。覆盖课程删除与文档/作业创建的前后顺序、等锁后课堂已删除时拒绝写入、文档元数据重绑时回滚。实际业务源码前后摘要一致，专用随机测试库已删除。见 `agent-teaching-context-locks-postgres-2026-09-10.json`。该项解除此前相应的原生并发验收阻断，不代表完整合班或生产联测完成。
- 正式生产切换完成，发布号 `20260910-215713-b420b6c46e55`，运行源码为冻结的 `476fc7bd`。停写后生成数据库备份，启动迁移 158/158 必需表、0 跳过、0 索引失败；DSH preflight、activate、retire-legacy 通过，部署命令返回成功且输出 `DEPLOY_DONE`。
- 最终原生迁移门禁通过：同一生产 custom dump 恢复到两个新隔离数据库，分别执行增量与完整启动迁移各两遍；原基线、幂等性、Agent/签章迁移证明和 64 个迁移源文件摘要全部通过，blockers 为空。专用集群已停止。报告位于 `.codex-temp/dsh-release/final-native-476fc7bd-v2/native-report.json`。首轮因 Windows 后台进程继承管道而退出，保留原诊断；修复仅涉及私有演练启动脚本。
- 从冻结 worktree 执行 `-DryRun -QuiesceForMigration` 通过，2,258 个部署文件、31.82 MB 归档，数据库报告与原始备份匹配。日志为 `.codex-temp/dsh-release/deploy-dryrun-476fc7bd.log`。预演没有上传文件或改动 Docker Compose。
- c3 新隔离环境三身份真实任务完成，22 次模型请求、无付费重跑，真实草稿/学校写入和本人文件产物成功，8 项生命周期边界通过。原报告因只识别 `platform_read` 而拒绝等价的 `platform_query(my_classrooms)`；保留该失败记录，实际回执经独立离线复核通过。证据见 `agent-dsh-isolated-e2e-c3-2026-09-10.json`。实验环境已停止；二进制文件转换和每项平台业务不属于该最小真实模型任务组，具体范围不夸大。
- 同服务器私有目录已生成附件基线备份：`/lanshare/.codex-temp/dsh-migration-20260910/attachments-476fc7bd-20260910-134500.tgz`。大小 2,792,411,855 字节，SHA256 `9c5a404f7c7385ce28f1dbb3612d2cbb3163093d4bcc3ffa1ff55118fc6c40e6`，29,506 个条目完整读取通过，tar 返回 0。范围为 storage、shared_files、homework_submissions、rosters、attendance、data，排除 PostgreSQL 数据目录/备份及 data 的 backups/tmp/logs。此为运行中的切换前附件基线，不声称与停写切换数据库具有同一时间点一致性。

## 生产回滚与旧后端隔离

旧 TUI 容器在新 DSH 启动器与应用健康检查通过后删除。其专属 `data/agent_tasks/deepseek_home` 移入私有目录 `.codex-temp/dsh-migration-20260910/retired-tui-20260910-135651/`，保留全部状态；业务 `data/agent_tasks` 父目录及其他任务文件未移动。旧 `agent_task_progress_service.py` 在构建前移入同一目录，避免覆盖式部署把已删除模块重新打包进镜像。

旧 TUI 镜像保留，原应用镜像 `sha256:c30959ea1dc62b4214fe6b1a2662bd60c880fd8b5573aa9b5a93c8dccb893726` 保留为 `lanshare-app:rollback-pre-dsh-20260910`。

切换备份位于 `/tmp/lanshare-deploy-backups/`，均限制为 0600：

- `code-20260910-215724.tgz`：2,997,233,630 字节，SHA256 `60858bfa6bd2a38521a2c54da3c91ca7bffaa30e0b19f3cfe840edf6fe4d8bb3`。
- `db-cutover-20260910-215724.sql.gz`：32,056,293 字节，SHA256 `95c21e099501ef789931921ce50e9689d0732bc9b40474538cdd5eea2f3e75d9`，在所有应用写入进程停止后生成并通过 gzip 完整性检查。

## 剩余边界

生产 DSH 只读验收 11 项全部通过：installer verify、固定镜像/profile、systemd launcher、真实 worker 主进程及连续 PostgreSQL 轮询活动均正常。6 个无凭据的 Agent/模型网关请求返回 401，必要配置只核验布尔存在性，没有创建生产测试任务、调用付费模型或输出密钥。worker 未实现独立闲时心跳，本次不把数据库活动冒称心跳。证据见 `agent-dsh-production-runtime-acceptance-2026-09-10.json`。

生产来源与健康验收通过：宿主和新容器的 64 个迁移文件及 9 个 Agent 核心文件，共 73 个文件均匹配冻结源码；公网 HTTP 200 与发布号一致，8 个关键容器正常，旧 TUI 容器及退休模块在宿主/新镜像中均不存在。Agent 当前 queued/running/stale 均为 0、状态正常；历史失败数与当前运行状态分开记录。证据见 `agent-dsh-production-postflight-2026-09-10.json`。

Git 交付使用 `codex/agent-dsh-migration` 分支：产品部署基于 `476fc7bd`，后续提交仅补充验证工具、测试及上线证据。最终推送结果以本次任务回执和远端分支为准。

子代理/工作流仍禁用；能力台账列出的未接入业务不因后端已上线而视为完成，不宣称已经覆盖整个平台全部操作。
