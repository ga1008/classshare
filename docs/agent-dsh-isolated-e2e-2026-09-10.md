# DSH 服务器隔离端到端验收记录

2026-09-10，在目标服务器的独立测试环境中，教师、学生和超级管理员三条真实 DSH 任务全部完成。此次验证使用真实 DeepSeek 模型、官方 DSH 镜像、实际网关、MCP、正常登录和平台业务服务；没有切换生产服务，也不能代替最终冻结版本的生产迁移与上线验收。

## 验收结果

| 当前用户 | 任务 | 真实业务结果 | 最终类型 | 文件 |
| --- | --- | --- | --- | --- |
| 教师 `teacher:900001` | 1 | 通过 MCP 创建唯一博客草稿，作者为本人、状态为 `draft`；实际读取本人课堂 | `verified_business` | `e2e-teacher.md`，1759 字节 |
| 学生 `student:900001` | 2 | 通过 MCP 读取本人课堂与消息统计，两项正常接口返回 HTTP 200；无平台写回执 | `deliverable` | `e2e-student.md`，1090 字节 |
| 超管 `teacher:900002` | 3 | 正常组织服务创建唯一学校 `dsh-e2e-created`，并执行管理员读取 | `verified_business` | `e2e-admin.md`，1846 字节 |

三条任务的 18 项业务检查全部通过。任务完成判定来自真实业务回执与数据表复核；教师和管理员各有一个 `agent_action_executions.completed` 回执。学生结果按读取和文件交付完成处理。

模型网关记录 17 次真实 `deepseek-v4-pro` 请求，全部 `completed`、上游 HTTP 200；供应商返回的累计输入 414112 tokens、输出 4752 tokens。该数字包含各轮上下文，不代表独立文本量或费用。17 次请求使用同一实际配置代次 `ff1fa268c4bee1e0a707133d`。另观察到一次平台预算 HTTP 429，官方运行时随后重试成功，未增加重复业务写入。

## 权限与执行收尾

另外 8 项实际 HTTP / 数据库边界检查全部通过：

- 学生可以下载自己的结果文件，下载字节与磁盘文件完全一致。
- 同编号学生不能下载教师文件，接口返回 403；其他用户可见的任务公开摘要不含私有指令、结果详情或事件。
- 应用数据库角色不是超级用户，没有创建数据库、创建角色或复制权限，也没有连接 `postgres` / `template1` 的权限。
- 三条任务共 3 次执行尝试、6 个工具/模型临时授权；结束后无运行中的尝试、无有效临时授权、无活动请求预算占用。

真实运行中的 DSH 容器检查通过：固定镜像、网络 `none`、只读根文件系统、`cap-drop ALL`、`no-new-privileges`、1 GiB 内存、128 PID；仅挂载独立任务目录、独立 HOME、只读配置和专用网关 socket。没有数据库环境变量、真实 DeepSeek Key、生产数据挂载或 Docker socket。

## 隔离范围与版本

- 隔离根：`/lanshare/.codex-temp/dsh-migration-20260910/e2e`。
- PostgreSQL 使用独立容器 `lanshare-dsh-e2e-pg`、独立数据目录、独立数据库和角色；只从生产 PostgreSQL 获取 `pg_dump --schema-only --no-owner --no-acl` 表结构，没有复制生产业务行或会话。
- 应用 `lanshare-dsh-e2e-app` 仅加入 `lanshare_dsh_e2e` 网络；只发布 `127.0.0.1:18001`。源码只读挂载，数据和 IPC 分离。
- 使用完整应用路由，关闭 lifespan，显式初始化数据库；没有启动生产 AI、邮件、调度或普通后台 worker。每条任务使用一次手动 worker 执行。
- 新 SECRET、数据库密码与合成账号密码只保存在权限 0600 的隔离文件内。生产活动模型 Key 仅在可信应用进程的只读事务中读取和解密，立即用新的测试 SECRET 重加密，再导入隔离数据库。没有打印原始 Key，也没有传给 DSH 容器。

固定版本：

| 内容 | SHA256 |
| --- | --- |
| 应用依赖镜像 | `c30959ea1dc62b4214fe6b1a2662bd60c880fd8b5573aa9b5a93c8dccb893726` |
| 官方 DSH 镜像 | `15312d67bf62400d238ebd74034b29795064cca199ffbdf9a8ad1b2e6b2d8a47` |
| DSH profile | `8c3853e5181e3ea9365ace7dcb70d2abfd56c09b2786c341efd73f199b288594` |
| 868 文件中间源码压缩包 | `7cc44f37496979d749aa4373eaaadb118ae26998ab505fff340ecf0f45f2f208` |
| 生产 schema-only 输入 | `2c66375e40d60ba4dc3b80fc40787acbf50a8491a5ad476a3fba0b84e3508a33` |
| 验收时单项源码补丁 `schema_agent_ext.py` | `94851a18910bcbd10bc18e478ae21522a46d0fdf0b697a6925b136c6b374ac9e` |

该压缩包是并行开发期间的中间快照，不包括后续安全输入及其他新增能力。实际运行中还保留了没有活跃引用的旧 `agent_task_progress_service.py`：脚本原先写错了退休文件名；三条任务结束后，已经按原 manifest 精确移除此文件并记录旧 SHA。该偏差及修正时间明确保存在 `shutdown-evidence.json`，没有把中间验收包装成最终源码一致性验收。

## 实测发现并修复的原生迁移问题

生产 PostgreSQL 表结构中，`agent_task_composers.teacher_id` 是 identity 列。原迁移直接移除 `NOT NULL` 会报错，隔离初始化据此失败并回滚。修复先移除该列的 identity 生成行为，再移除非空约束：旧教师编号及外键完全保留，新的 `(actor_role, actor_id)` 唯一索引允许同编号学生创建自己的编辑状态。

独立证明器通过 `pg_depend` 捕获该列独占的 identity sequence，只允许该列 identity 清空与这一条已退休 sequence 消失；没有放宽其他列、外键、序列或历史值。包含原生 PostgreSQL 的 30 项迁移/门禁测试通过，验证两遍迁移幂等、旧值完整、同角色去重、跨角色同编号、外键拒绝以及未伪造历史授权。

## 证据与收尾

另完成了一轮独立的生产旧数据预演：将已授权的原生 custom dump `agent-dsh-20260910-095830.dump`（40647940 字节，SHA256 `c58340459c70bc337204cd29fe21e3c48206ee24c5501ee9ff1be1e026cfc4fc`）恢复到本机仅监听回环地址 55439 的独立随机名数据库。232 张原表、474456 条原记录经过两次完整 `init_database()` 后，两遍未预期旧值/结构差异均为空，Agent 独立证明无阻断，第二遍幂等差异为空；原 dump 字节及运行期间的迁移源码均未改变。没有启动应用 lifespan、worker 或外部服务，也没有输出历史原行。

这轮完整旧数据预演位于 `.codex-temp/dsh-release/intermediate-native-20260910-191317-3e2888fe/`：`report.json` SHA256 为 `7e5718d01d6f6d185d9a56fcaabf280a0a200a7a52eb710179288df7abc8693c`，`run-metadata.json` SHA256 为 `d9f57116cd575308566be296a54780bc7773772b8b46b7c74504dc4dfbf80285`。这仍是中间源码预演，最终冻结版本必须另行执行既有完整门禁。

随后对改密与 Agent 授权签发新增 5 项真实 PostgreSQL 竞态验证并修复共享锁：先提交的签发必被随后的改密撤销；改密先提交并保留当前登录会话时，新的明确持续授权可正常签发；重置登录会话后，等待中的持续授权签发必须 401；在改密枚举运行任务后才启动的新任务也不能跨过重置取得工具授权；同编号教师/学生互不误锁。凭据 HTTP、授权服务与原生 PostgreSQL 合计 45 项测试通过。签发与撤销使用同一 actor 事务锁并在等待后重新校验来源，锁序为已有任务锁在前、actor 锁在后；普通退出登录保持原有持续授权语义。

本地无密钥证据位于 `.codex-temp/dsh-e2e-evidence-20260910/`；服务器证据保存在隔离根及其 `data/` 目录。

| 文件 | SHA256 |
| --- | --- |
| `e2e-verification.json` | `e2c217bdf8cb825562e8724b13ad03c42856496b7c610ac1eebdbe16cc3ec3d4` |
| `e2e-lifecycle-boundaries.json` | `93054b56238034e72f1d884f7b7ad4958442c309acb8d8148c08b89684e073f5` |
| `container-boundaries-1789038272.json` | `6f31dcafbc11bca163a373792ef18e89fd1c588879863ebaf1496f094453789b` |
| `shutdown-evidence.json` | `59b02f71de53d2b808494203dd5b1d794f56918d7e06eed376aa5ec1fe4ca7e1` |

任务结束后已停止隔离应用、PostgreSQL 和 launcher，确认无遗留隔离 DSH runner；独立数据保留以供审阅和后续冻结复验。生产容器和业务数据没有因本验收而修改。

执行脚本：`tools/dsh_isolated_e2e.py` 与 `tools/dsh_isolated_e2e_app.py`。它们是固定日期、固定隔离路径的验收工具，不是生产部署入口；重新准备拒绝覆盖已有测试环境，恢复步骤也不会默默删除数据库。
