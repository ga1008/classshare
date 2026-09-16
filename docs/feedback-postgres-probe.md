# 反馈会话原生 PostgreSQL 并发验证

`tools/feedback_postgres_probe.py` 验证真实 PostgreSQL 行锁、事务与并发行为，使用生产服务代码及真实站内通知写入。只替换邮件入队函数，不发邮件、不启动应用或后台任务。参与者和反馈内容均为合成测试数据，不读取生产反馈。

## 运行条件

- 已启动的独立预演集群，监听地址必须严格为 `127.0.0.1`，端口不能为 `5432`。
- 提供准确的集群数据目录；脚本会查询服务端 `data_directory`、监听地址和端口进行核验。
- 集群内存在以 `lanshare_assessment_rehearsal` 为前缀的控制数据库，用户为 `rehearsal_admin`。这些限制复用 `assessment_postgres_rehearsal.connect_offline`。
- 工作区 Python 环境已安装项目依赖和 psycopg。不要用 `python -O`，因为探针依赖断言。

从仓库根目录运行，显式提供本次离线集群参数与新的证据目录：

```powershell
python tools/feedback_postgres_probe.py `
  --cluster-dir $env:ASSESSMENT_REHEARSAL_TEST_CLUSTER `
  --port $env:ASSESSMENT_REHEARSAL_TEST_PORT `
  --control-database lanshare_assessment_rehearsal `
  --output-dir $feedbackProbeOutput
```

`$feedbackProbeOutput` 应设为本次任务的全新临时目录；Windows 本机使用 E 盘临时存储。若控制数据库名称不同，传入预演实际创建的名称。脚本不接受生产 DSN，也不从应用配置发现连接信息。

脚本仅在通过身份核验的离线集群中创建一个带随机后缀的独立测试数据库，结束时删除自己成功创建的数据库；不重用、清空控制库或其他数据库。集群的启动、停止和物理目录清理由调用方的预演生命周期负责。不要在 native gate 已经停止的集群上运行。

## 验证内容与结果

1. 回复先持锁提交：旧版本关闭请求实际等待 PostgreSQL 锁，随后返回 409，不产生关闭事件。
2. 关闭先持锁提交：竞争回复实际等待锁，随后返回 409，不产生迟到回复。
3. 六路并发重试同一发送标识：仅一条消息，其余五次去重；准确通知两个活跃超管，不重复通知发送者和停用账号。
4. 并发及延迟上报已读位置：游标保持最大已读值，对应站内通知完成已读。
5. 学生与普通教师的数字 ID 相同时，角色不同仍不能访问对方反馈。
6. 回复事务回滚时，消息、反馈状态及站内通知共同回滚。

退出码 0 且 `concurrency-report.json` 中 `ok=true` 表示通过。报告包含每项结果、服务源码 SHA256 和隔离/清理证据，不含连接密码或用户反馈正文。`synthetic_database_removed=true` 表示本次合成测试数据库已删除；调用方仍应关闭自己启动的集群。

2026-09-16 的六项原生验证记录见 `feedback-postgres-concurrency-2026-09-16.json`。该记录来自抽取前的同一组测试场景；抽取只改为复用调用方的离线集群，不将尚未重跑的包装入口描述为再次通过。完整旧数据保留、迁移幂等性仍由 native PostgreSQL 双路径门禁另行验证。
