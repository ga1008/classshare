# 三分类发布的离线迁移演练

`tools/assessment_migration_rehearsal.py` 只接受明确传入的 **离线 SQLite 备份**，以只读连接复制到一个尚不存在的工作文件。它不寻找项目数据库、不调用 `init_database()`、不连接网络、不删除目录、不启动任务或模型。

```powershell
venv/Scripts/python.exe tools/assessment_migration_rehearsal.py --sqlite-backup C:/offline-lab/backup.sqlite --working-copy C:/offline-lab/rehearsal.sqlite --offline-attachment-root C:/offline-lab/attachments --report C:/offline-lab/report.json
```

三个文件路径必须显式指定，工作库和报告不得已存在；脚本不会覆盖它们。附件目录可省略，省略时报告明确记录尚未验证文件。附件路径以传入的离线目录为根解析，拒绝逃出该目录的绝对路径、`..` 和符号链接。

实际复用本版本的三项增量迁移：权威三分类及审计、持久 AI／复核台账、正式成绩公布快照。不会执行其他历史启动修复或种子数据写入。

验证项目：

- 迁移前每张旧表的旧列、记录数、主键集合及顺序摘要、关联列摘要、字段定义与外键定义。
- 全部旧列的确定性 SHA-256；保留文本、NULL、零分、浮点数和二进制内容的存储差异，不重写 JSON。报告仅含结构、计数和摘要，不输出学生答案、成绩明细或材料正文。
- 两次迁移分别对照同一旧字段基线；第二次强制重跑迁移函数，比较第一次之后的全部表、全部字段和 schema，排除内存 ready 缓存造成的假幂等。
- 原备份文件摘要保持；可选核对 `submission_files.relative_path` 映射的离线附件是否存在、能读取且迁移前后内容摘要一致。不会搬迁、重命名或修改附件。

`status=ok` 只表示本次范围内的数据库保护检查没有差异，不代表完整上线门槛已满足。`deployment_gate_complete` 始终为 false，避免把 SQLite 或合成样例结果误认为生产验证。

**PostgreSQL 部署必须做 PostgreSQL 原生备份副本演练。** 原生适配器为 `tools/assessment_postgres_rehearsal.py`。先将一致性 `pg_dump --format=custom --no-owner --no-acl --lock-wait-timeout=5s` 备份恢复到同版本、全新且隔离的本机集群；备份和集群目录应在同步仓库之外，服务只监听 `127.0.0.1`，使用专用非默认端口和 `rehearsal_admin` 用户。不要复用现有本机集群。备份／恢复是独立的显式操作，脚本不提供远端复制、自动发现 DSN 或数据库覆盖功能。

```powershell
venv/Scripts/python.exe -u tools/assessment_postgres_rehearsal.py --cluster-dir C:/offline-lab/cluster --port 55437 --database lanshare_assessment_rehearsal --backup-file C:/offline-lab/snapshot.dump --report C:/offline-lab/incremental-report.json
venv/Scripts/python.exe -u tools/assessment_postgres_rehearsal.py --cluster-dir C:/offline-lab/cluster --port 55437 --database lanshare_assessment_rehearsal --backup-file C:/offline-lab/snapshot.dump --scope full-startup --report C:/offline-lab/full-startup-report.json
```

脚本拒绝默认端口、非专用库名前缀及已存在的报告；连接后核对服务端真实 `data_directory`、端口和监听地址，只有与显式参数一致才继续。它不读项目 `.env`，不启动应用 lifespan、AI、调度或业务 worker。`full-startup` 每次调用都用全新的隐藏 Python 子进程执行实际 `init_database()`，没有复用 ready 缓存，也不启动 ASGI 应用。

PG 核验覆盖 `public` 的全部旧表旧列、行数、主键、关联、外键验证状态，列／默认值／约束／索引／视图／触发器／函数／策略定义摘要和序列定义、序列当前值及 `is_called`。固定会话文本格式后按 PostgreSQL 可往返文本表示读取字段（JSON 原文本保留、JSONB 使用服务端规范表示），逐行只保留固定长度摘要并排序，支持没有主键和重复行的表。`--no-owner --no-acl` 的恢复有意使用专用本机所有者，生产角色及授权不是此演练的比较对象。PG 数据文件和原始 SQL 不进入报告。

增量范围只复用本版本三分类／审计、AI／复核台账与成绩公布的实际 PG DDL；`full-startup` 另验证完整入口及所有启动修复。严格比较不会静默白名单掉历史启动写入；出现差异仍标记 `failed`，需要逐字段解释。原生测试使用显式 `ASSESSMENT_REHEARSAL_TEST_CLUSTER`、`ASSESSMENT_REHEARSAL_TEST_PORT` 指向上述隔离集群，创建并清理独立的合成测试库；未设置时会跳过原生测试，不能将跳过当作 PG 通过。

2026-09-07 已对真实一致性备份完成本机 PG 16.14 恢复：220 张旧表、469,672 行，两次本次增量迁移的旧字段／关系／schema／序列差异均为 0，第二次全库幂等差异为 0。98 个历史任务均保留 NULL 分类、版本 0，分类审计为空。随后两次完整初始化均成功（158/158 必需表、无跳过步骤或索引失败），但严格幂等检查保留为失败：现有 `schema_signature_workflow.py::_seed_function_points` 无条件 UPSERT，使 `signature_function_points.updated_at` 的 5 行每次更新，序列两次累计增加 10；从原始 dump 逐列核实其余字段未变，其他所有表均未变。匿名结果见 `docs/assessment-postgres-rehearsal-2026-09-07.json`。这属于原有启动种子写入，不是批改、分类或旧成绩被重写。

随后修复了上述启动副作用：只更新实际变化的签名功能元数据，仅对缺失键执行插入，保留已有启停设置和创建时间。重新核验专用集群身份后，从**同一原始 dump** 重新建库恢复；新基线与初次未迁移基线完全相等。再次执行两次增量迁移和两个全新进程的完整初始化，全部旧字段／关系／schema／序列差异与幂等差异均为 0，没有放宽白名单。通过记录另存 `docs/assessment-postgres-rehearsal-fixed-2026-09-07.json`；此前失败记录保持原样。原生套件 5 项（4 项真实 PG、1 项隔离目标保护）与 52 项 SQLite 迁移／签名回归通过，覆盖未改种子不写入、变更标签仍更新、缺失种子仍新增且不重新启用已禁用项目。

计划第 5.9 节余下门槛仍须单独完成：附件备份与实际打开历史答卷／材料，以及有新提交时的增量保全方案。数据库 dump 只保护附件路径映射，不包含附件字节；不能将其称为完整文件备份。任何未解释的旧字段差异应阻止上线；历史数据本身已有的缺失文件应查明，不能被“迁移没改变它”掩盖。

部署预演可显式传入原生报告及其真实备份：`deployment/deploy_remote.ps1 -DryRun -MigrationReport C:/offline-lab/native-report.json -MigrationBackup C:/offline-lab/snapshot.dump`。新增只读校验器 `tools/deploy/validate_native_pg_rehearsal.py` 必须同时确认：PG custom dump 的实际字节摘要与大小、两次增量及两次新进程完整初始化均通过、无旧字段或幂等差异、隔离恢复来源，以及迁移源码集合和逐文件 SHA-256 完全一致。当前通过报告绑定了 53 个数据库／配置依赖源码文件；新增、删去或修改这些文件都会拒绝复用旧报告。首次补录摘要前已确认这些文件修改时间早于本次通过记录的验证时间；这只是本次已验证源码的关联证据，不允许对随后新 schema 重新填 hash 来免除复验。

`-MigrationReport` 与 `-MigrationBackup` 必须成对提供。明确配置 PostgreSQL 时，缺少原生报告会停止，不能自动用 SQLite 检查替代；原有 SQLite 部署路径仍保留。该校验只负责数据库迁移证明，不把未打包的真实运行配置视为已检查，也不替代附件及部署时的写入保全。DryRun 只构造本机清单和归档，返回后不会上传或启动 Docker。需要审阅实际清单和压缩包时追加 `-KeepLocalArtifacts`；默认仍清理本次生成的打包临时目录，清理前会检查其准确边界。

`deployment/deploy_remote.ps1` 仍遵守原 `.gitignore`，是包含本机部署参数的本地操作脚本；本次没有取消忽略、force-add 或将其放进服务器包。隔离交付目录会显式复制它来运行 DryRun。可复用的原生报告校验器、跨平台门禁测试和本说明正常交付；两项 PowerShell 本地脚本合同测试只在 Windows 且该本地脚本存在时执行，干净 checkout 不会因缺少它而失败。签入的源码和报告不会包含真实密钥或数据库备份。
