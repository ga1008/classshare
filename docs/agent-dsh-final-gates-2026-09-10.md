# DSH 冻结版本上线门禁进展

本文件续接本地收尾检查点，记录实际完成的剩余上线门禁，不扩展功能。产品源码固定为 `476fc7bdb3a0a4758a97e33bda496b6bcc812594`；独立 worktree 保留主目录干净提交的原始文件字节，避免换行转换改变来源摘要。后续证据和测试新增不改变该产品源码。

## 已完成

- 教学上下文锁原生 PostgreSQL 7/7 通过、无跳过：6 组观察到 `pg_blocking_pids` 真实等待，1 组真实 NOWAIT 冲突。覆盖课程删除与文档/作业创建的前后顺序、等锁后课堂已删除时拒绝写入、文档元数据重绑时回滚。实际业务源码前后摘要一致，专用随机测试库已删除。见 `agent-teaching-context-locks-postgres-2026-09-10.json`。该项解除此前相应的原生并发验收阻断，不代表完整合班或生产联测完成。
- 生产应用与 PostgreSQL 容器复核均健康；暂未切换生产。
- 最终原生迁移门禁通过：同一生产 custom dump 恢复到两个新隔离数据库，分别执行增量与完整启动迁移各两遍；原基线、幂等性、Agent/签章迁移证明和 64 个迁移源文件摘要全部通过，blockers 为空。专用集群已停止。报告位于 `.codex-temp/dsh-release/final-native-476fc7bd-v2/native-report.json`。首轮因 Windows 后台进程继承管道而退出，保留原诊断；修复仅涉及私有演练启动脚本。
- 从冻结 worktree 执行 `-DryRun -QuiesceForMigration` 通过，2,258 个部署文件、31.82 MB 归档，数据库报告与原始备份匹配。日志为 `.codex-temp/dsh-release/deploy-dryrun-476fc7bd.log`。预演没有上传文件或改动 Docker Compose。
- c3 新隔离环境三身份真实任务完成，22 次模型请求、无付费重跑，真实草稿/学校写入和本人文件产物成功，8 项生命周期边界通过。原报告因只识别 `platform_read` 而拒绝等价的 `platform_query(my_classrooms)`；保留该失败记录，实际回执经独立离线复核通过。证据见 `agent-dsh-isolated-e2e-c3-2026-09-10.json`。实验环境已停止；二进制文件转换和每项平台业务不属于该最小真实模型任务组，具体范围不夸大。
- 同服务器私有目录已生成附件基线备份：`/lanshare/.codex-temp/dsh-migration-20260910/attachments-476fc7bd-20260910-134500.tgz`。大小 2,792,411,855 字节，SHA256 `9c5a404f7c7385ce28f1dbb3612d2cbb3163093d4bcc3ffa1ff55118fc6c40e6`，29,506 个条目完整读取通过，tar 返回 0。范围为 storage、shared_files、homework_submissions、rosters、attendance、data，排除 PostgreSQL 数据目录/备份及 data 的 backups/tmp/logs。此为运行中的切换前附件基线，不声称与停写切换数据库具有同一时间点一致性。

## 待完成

停写迁移/切换、线上功能验收及 Git 远端收口。旧运行器只有在新服务验收后才退役；不因局部测试通过标记整个平台数字分身能力完成。
