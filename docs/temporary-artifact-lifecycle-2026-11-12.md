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

2026-11-16 补齐夹具自身的收尾：Agent SDK 类级工作区由类清理栈持有，实例 patch 在建立时注册恢复；任务结果读取测试使用上下文临时目录；公文打包测试在创建 SQLite 连接后立即登记关闭，并由上下文持有文件工作区。`tests/test_temporary_test_fixtures.py` 注入类初始化、实例初始化、schema 初始化和断言失败，检查目录删除、连接关闭及全局状态恢复。这些检查覆盖夹具失败路径，不能用父进程最终清理 scratch 代替。

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

2026-11-16 分支整合的夹具失败路径验证使用 `tools/test_backend.py --pattern test_temporary_test_fixtures.py --report .codex-temp/branch-consolidation-20261116/fixture-tests.json`，结果与父进程清理状态以该报告为准；相关 Agent SDK、任务改进与公文打包回归报告保存在同一证据目录。本段记录验证入口，不扩大为全平台验收。

本轮四组隔离验证通过：夹具失败路径 6 项、Agent SDK 9 项、任务改进 48 项、公文打包 8 项，共 71 项，无跳过；四份报告均确认 worker 退出、`cleanup=removed`，对应本轮 runtime 已删除。其余三份报告为 `agent-sdk-tests.json`、`agent-task-tests.json` 与 `gongwen-package-tests.json`。未使用业务数据或真实 PostgreSQL 连接。

## 2026-11-16 维护 CLI 工作区所有权

数据库清单、备份演练、迁移 dry-run、PG lab、附件恢复计划、性能报告、PG 导出和修复计划的写入入口共用 `tools/temp_path_safety.py`：输出目录必须是 `.codex-temp` 的严格子目录，不能位于其 `evidence` 或 `scratch` 保留区，不能经过 symlink/junction。只读的路径解析、环境检查和历史报告汇总仍可读取旧目录。

首次运行在全新或空目录写入 `.lanshare-temp-workspace.json`，记录具体工具、规范路径与目录身份。只有同一工具且目录身份匹配时才允许重建自己的结果；`pg_lab` 的 `clean=False` 保留既有内容。历史非空目录没有这一标记时直接报错并保留原物：选择一个新的 `--runtime-root .codex-temp/<新目录>` 或 `--lab-root`，不要为绕过检查给旧目录手工补标记。同一固定默认路径在由新版工具创建后可继续复跑。

每次操作对其精确目录取得独占锁，父子工作区也相互隔离；遇到未确认退出的锁不自动解锁或接管。重建前核验所有权及目录树，拒绝链接、嵌套的独立工作区和位于待重建目录内的输入文件。锁只覆盖该工具调用，不是全局锁，不扫描或清扫其他任务目录。强杀后若遗留锁，使用新路径继续，旧路径须专项确认进程退出后处理。

验证报告位于 `.codex-temp/branch-review-20261116/`：`cli-path-safety.json` 覆盖误指定根/保留区、历史目录、同工具复跑、跨工具隔离、父子/并发运行和输入保留；`native-pg-lifecycle-mock.json` 与 `native-pg-probe-mock.json` 仅验证模拟原生命令的失败和收尾合同。真实自有合成 PostgreSQL 验收结果另行记录，模拟测试不代替原生集群验收。
