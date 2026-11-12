# 临时产物生命周期（2026-11-12）

目标是让本轮临时文件随本轮任务收尾，同时保留部署、恢复和验收证据。平台业务目录、业务日志、Claude 本地配置、已有 `.codex-temp` 历史资料均不由这些工具自动清扫。

## 单元测试

```powershell
venv/Scripts/python.exe tools/test_backend.py --pattern test_backend_test_runner.py --report .codex-temp/evidence/temp-lifecycle-20261111/backend-runner.json
```

测试入口在 `.codex-temp/scratch/backend-tests/unit-*` 新建独占目录。父进程只负责生命周期；子进程阻断 dotenv 和真实 PostgreSQL，使用隔离 SQLite，并将 `TMP`、`TEMP`、`TMPDIR` 与 Python 的 `tempfile.tempdir` 都指向本轮 `tmp`。子进程结束、数据库和日志句柄释放后，父进程删除本轮目录。用例失败也执行收尾；清理错误会显式报错，不再静默忽略。

`--report` 输出成功或失败的 JSON 验收摘要，必须在临时运行目录之外。内部 worker 入口只接受父进程新建目录的所有权标记，不能借其指向任意历史目录或业务目录。

测试夹具仍须及时释放自身资源，不能依赖进程退出兜底：

```python
folder = self.enterContext(tempfile.TemporaryDirectory(prefix="example-test-"))
connection = sqlite3.connect(Path(folder) / "fixture.db")
self.addCleanup(connection.close)  # 后注册先执行：先关连接，再删目录。
self.enterContext(patch.object(service, "WORKSPACE", Path(folder)))
```

类级资源使用 `cls.enterClassContext(...)` / `cls.addClassCleanup(...)`。在创建成功后立即注册清理，确保 `setUp` / `setUpClass` 中途失败也执行。`tearDown` 不是初始化失败的可靠清理入口。

## 自有原生 PostgreSQL 验证

```powershell
venv/Scripts/python.exe tools/wanderer_native_pg_probe.py
# 仅在确实需要保留数据库用于调试时：
venv/Scripts/python.exe tools/wanderer_native_pg_probe.py --keep-runtime
```

该入口创建自己的回环地址、随机端口和全新合成数据库。收尾先停止并核实集群状态，再删除本轮 `cluster` / `runtime`；报告、测试日志、命令日志和数据库日志留在运行证据目录。所有权、路径、PID、停止状态或文件清理有异常时保留残留，并记录原因；不能把清理失败报成通过。

调用者提供的迁移演练集群不自动删除。`assessment_postgres_rehearsal.py`、容量/业务 PostgreSQL probe 仍遵守各自的外部集群合同。部署门禁依赖的 report 与匹配 dump、历史行为队列恢复归档必须同时保留。

## 一次性脚本、冻结源码与部署资料

- 临时计算脚本和没有独立保存价值的源码副本：使用 `TemporaryDirectory` / `ExitStack` 包住完整任务，存放在新建的本轮 scratch。导出最终结果后关闭子进程与文件再退出上下文。重复使用的代码转为 `tools/` 中的维护工具。
- 报告、失败日志、截图、发布清单、迁移备份、恢复输入、容量实验的冻结源码和 seal：属于 evidence，直接写入独立证据目录，不放在自动删除的 scratch 中。
- 不给部署/恢复命令套单测 runner 或全局重写 TEMP 的包装器。部署脚本有自己的临时发布包生命周期与 `-KeepLocalArtifacts` 保留合同，外层自动删除会破坏该合同。
- 维护 CLI 的可重建工作区只能是 `.codex-temp` 的严格子目录，拒绝整个根目录及 symlink/junction 路径；Nutstore 的普通云文件标记不当作目录链接。该路径检查不表示目录里的所有文件都可以删除，仍须遵守各 CLI 的用途和所有权约定。
- 不增加启动时全目录扫描、按年龄清扫或服务器定时删除任务，不接管旧目录，不删除构建锁。它们会误伤并发任务或不可重建证据。

强杀、断电可能跳过正常收尾。对此只报告精确残留路径，核实所有权和进程已结束后专项处理；不以“目录很旧”或“没有 pid 文件”作为单独的删除依据。

## 验证记录

本次隔离测试、原生合成集群运行、正式构建和全平台源码审计证据保存在 `.codex-temp/evidence/temp-lifecycle-20261111/`。其结果与已知限制由该目录中的最终验证记录汇总；本变更不修改平台业务逻辑、数据库结构或线上部署配置。

本地明细见 [验收汇总](../.codex-temp/evidence/temp-lifecycle-20261111/verification.md)。该证据目录不随发布包上传。单测未指定 `--report` 时，失败/中断诊断仍保存到 `.codex-temp/evidence/backend-tests/<run_id>.json`。
