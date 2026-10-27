# 道印与好友消息发布记录 · 2026-10-27

已于 23:06（北京时间）完成生产发布和只读核验。

| 项目 | 结果 |
|---|---|
| 功能源码提交 | `aa5336228cf62e6553ffa658e7e4dda6ae893425` |
| 线上发布标识 | `20261027-230333-abadccb653de` |
| 正式前端资源图 | `81b15e1acb4c59a57dbab94c7e8ec8557117c7fc63c5a5e09ceda45ed7ed80d1` |
| 发布包 | 干净独立 checkout，3,798 个文件，39.83 MB；逐文件与已验证主工作区字节一致 |
| 迁移门禁 | 79 个迁移源文件匹配原生 PostgreSQL 报告和备份；dry-run 通过 |
| 生产迁移 | 182/182 必需表，0 失败索引，0 跳过步骤 |
| 服务 | app、ai、mailer、scheduler、postgres、nginx、agent-worker、blog-crawler 均运行；所有有健康检查的服务均 healthy |
| 功能核验 | 48 个容器文件哈希、18 个公开资源哈希（含全部 10 枚 SVG）、9 张功能表通过 |
| 登录与权限 | 师生登录 HTTP 200；新增限额和会话设置接口匿名请求均 401 |
| 缓存 | 公网与容器发布标识一致；登录首响应仅 `Clear-Site-Data: "cache"` |
| 邮件 | 唯一已配置并正常使用的管理员 SMTP 被明确指定为平台发信者，app 与 mailer 已重启读取；未改账号或凭据，未发送生产测试邮件 |

部署遵循现有 `deployment/deploy_remote.ps1`，从干净副本经 `pwsh -NoProfile -ExecutionPolicy Bypass` 执行 `-DryRun -QuiesceForMigration`，再执行实际 `-QuiesceForMigration`。本次只新增平台发信者环境键，运行数据始终由原部署流程保护。

生产代码备份为 `/tmp/lanshare-deploy-backups/code-20261027-230347.tgz`；全部应用写入进程停止后创建最新数据库备份 `/tmp/lanshare-deploy-backups/db-cutover-20261027-230347.sql.gz`。保留当前脚本默认的最近两组备份/回滚镜像，不以旧备份代替此次停写备份。原有未提交资料和本地临时目录没有进入发布包。

健康总览中的 `background_tasks.ok=false` 来自累计 918 条历史失败记录；逐项当前状态全部 `ok`、stale 为 0，发布日志无近期错误。此历史计数未被清空。

隔离验证包括 170 项社交、180 项散修、31 项邮件策略、9 项邮件队列、8 项消息中心，以及 4 项独立 PostgreSQL 并发测试；正式资源下 17 项浏览器场景、9 项前端单元、TypeScript 与相关前端合同验证通过。详细范围见 [设计与验证](social-awards-redesign-2026-10-27.md)。全平台源码审计仍有历史 pending/unknown 和 7 项既有 lint 阻断，见 [源码审计](lq-platform-component-audit-2026-10-27.md)，未将本次限定验收冒充全站通过。

复核证据保存在主工作区 `.codex-temp/social-awards-release/{state.json,dry-run.log,deploy.log,postflight.json}`。发布副本源码和公开资源已核对，后续本发布记录提交仅补充文档，不改变已上线产品代码。
