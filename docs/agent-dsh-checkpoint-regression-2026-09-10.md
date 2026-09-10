# DSH 本地实现检查点回归记录（2026-09-10）

本文记录部署前的本地实现检查点验证，不代表新版本已经上线，也不替代冻结源码后的完整生产备份迁移预演和部署验收。当前业务能力仍分批扩展，不能据测试数量宣称全平台业务已经覆盖。

## 根执行者集中检查

- `C:\Python314\python.exe -m unittest discover -s tests -t . -p 'test_agent*.py' -q`：493 项，462 项执行通过、31 项条件跳过，52.440 秒，退出码 0。必须保留 `-t .`，使测试包先设置隔离数据库。原生权限 24 项由下文独立 PostgreSQL 回归实际执行；Windows 的符号链接/FIFO限制由 Linux 原系统调用探针另行验证，不能将跳过项算成已执行。
- `npm.cmd run typecheck`、`npm.cmd run build` 均通过；`npm.cmd test -- frontend/src/lib/agent-workspace-submission.test.ts`：10/10 通过。
- `npx.cmd playwright test agent-questions.spec.ts blog-composer-revision.spec.ts --config tests/e2e/components/playwright.config.ts`：实际 Chrome 15/15 通过，使用真实编译 CSS 和 Jinja 组件，包含 1440px/390px布局、问答输入保留、重试、晚到响应、密码安全输入及清理、请求人工核对和博客编辑版本冲突。组件夹具不启动应用/数据库。
- `tools/agent_platform_read_safety_probe.py` 在目标服务器独立临时目录运行：6/6 通过。普通快照同时核对文本与 SHA256；拒绝软链接、目录重定向、超限文件及无写端 FIFO，路径在打开后替换仍读取原文件描述符。没有读取生产数据或调用模型；报告见 `agent-platform-read-safety-linux-poc-2026-09-10.json`。
- 能力台账专项 18 项通过；`python tools/agent_capability_inventory.py --reviewed-json docs/agent-capability-reviewed.json --check` 无过期证据；`git diff --check` 通过。

检查点能力：A 读 44、事务写 32，B 受控原 HTTP 64，C 用户安全输入 3、文件来源 4、异步材料作业 1。文件来源当前只提供文本/文档抽取；二进制复制到 DSH 工作区仍待实现。874 条路由是盘点范围，不表示已完成 874 条业务适配。

检查点后继续课程/课堂、评分/考试/成绩公布、材料文件、签章及学术/公文等业务。下一批审查已定位旧材料路径 `LIKE` 未转义 `%`/`_`、材料树返回兄弟节点、删除预览未绑定具体引用及节点并发等问题，必须先在共享原业务修复并验证后扩展对应 Agent 入口。本次提交只保存可追溯的中间实现，不是生产上线批准。

## 共享业务及原生 PostgreSQL 专项：226 项通过

本节由独立回归执行者记录。仅运行已有测试和隔离夹具，没有为使测试通过修改业务代码或测试代码。运行环境为 Windows、Python 3.14.3，工作目录为 `C:\Users\AngelWei\Nutstore\1\Projects\lanshare`。

| 测试组 | 结果 | 验证范围 |
| --- | --- | --- |
| 正常共享业务核心 | 135 通过 | 作业分类/更新、博客、学习进度、课时材料绑定与生成队列、成绩投影、账户写入和组织元数据 |
| 调度与截止提醒 | 15 通过 | 任务认领、幂等、取消与迟到完成、周期任务、作业提醒、日历订阅 |
| 原生迁移与部署门禁 | 30 通过 | 其中 6 项实际 PostgreSQL 迁移；其余为目标限制、证明与部署契约检查 |
| 正常账户、试卷及成绩 | 20 通过 | 包含 9 项真实 Web 凭据变更测试和普通 Web 双超管并发降权；首次发现的 2 项环境跳过另行补跑 |
| 成绩撤回与合班原生并发补跑 | 2 通过 | 撤回等待合班锁，等待后重新校验成绩快照归属 |
| Agent 权限原生 PostgreSQL 全类 | 24 通过 | 凭据签发竞态、撤权、角色同号、账本/回执原子性、历史删除、请求核对、预算和 key 切换 |
| **合计** | **226 通过，0 失败，最终 0 未执行** | 首次跳过的两项已经实际补跑通过，不重复计数 |

这 226 项是本执行者选择的检查集合，不能直接与其他执行者的整套测试计数相加计算项目唯一用例数。

### 隔离边界

- 每组命令创建新的 `.codex-temp/checkpoint-business-<随机 UUID>/` 目录，并显式设置 `MAIN_DB_PATH`、`MAIN_DATA_DIR`、`LANSHARE_DATA_ROOT`。SQLite 使用新目录中的数据库或测试自身的 `:memory:` /临时文件夹具。
- 设置 `PYTHON_DOTENV_DISABLED=1`，清空 `DATABASE_URL`；不读取应用配置中的生产 DSN。设置独立合成 `SECRET_KEY` 和合成初始管理员数据；模型/回调地址固定 `http://127.0.0.1:9`，DSH 关闭。
- 测试未启动应用 lifespan、业务后台 worker、真实 DSH、邮件发送器或真实模型调用。调度组仅调用测试自己的单次任务处理函数；会使用真正 `init_database()`，但其目标是本组新建的 SQLite 测试文件。
- 原生 PostgreSQL 仅使用现有专用隔离集群：`C:\Users\AngelWei\Nutstore\1\Projects\lanshare\.codex-temp\dsh-pg-rehearsal\agent-20260910-172238-3bac5373`，监听 **127.0.0.1:55439**，用户 `rehearsal_admin`。
- `connect_offline` 在使用连接前核对服务端 `data_directory`、`listen_addresses` 和端口，且拒绝默认 5432 和非演练数据库前缀。未读取生产备份或生产业务数据。
- `NativePostgresRehearsalTests` 独占创建 `lanshare_assessment_rehearsal_tests_<PID>`，`AgentAuthorityPostgresTests` 独占创建 `lanshare_assessment_rehearsal_agent_<随机值>`。它们只清理自己成功创建的合成数据库；本轮测试及 `tearDownClass` 全部成功。
- 成绩合班测试要求固定库名 `lanshare_miniapp_phase1`。补跑程序先通过上述隔离集群校验，再以排他 `CREATE DATABASE ... TEMPLATE template0` 创建；若已存在则拒绝运行，不覆盖或复用已有库。`finally` 只在本程序创建成功后 DROP，测试和清理成功。测试内部另建随机 schema。
- 保留隔离集群和本地测试日志供复核；没有删除之前保存的生产备份演练数据库或 E2E 合成数据。

### 公共 PowerShell 环境

每组在下列环境中独立运行，随后将 stdout/stderr 重定向到该组 `*.log`。这些值均为测试值。

```powershell
$env:PYTHON_DOTENV_DISABLED='1'
$env:PYTHONUTF8='1'
$env:DB_ENGINE='sqlite'
$env:DATABASE_URL=''
$testRoot = Join-Path (Get-Location) ('.codex-temp/checkpoint-business-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$env:LANSHARE_DATA_ROOT=$testRoot
$env:MAIN_DATA_DIR=$testRoot
$env:MAIN_DB_PATH=Join-Path $testRoot 'isolated.sqlite'
$env:SECRET_KEY='synthetic-checkpoint-only-not-live'
$env:INITIAL_SUPER_ADMIN_EMAIL='checkpoint-admin@example.test'
$env:INITIAL_SUPER_ADMIN_NAME='Synthetic checkpoint administrator'
$env:INITIAL_SUPER_ADMIN_PASSWORD='Synthetic-checkpoint-only-9201!'
$env:AI_ASSISTANT_URL='http://127.0.0.1:9'
$env:MAIN_APP_CALLBACK_URL='http://127.0.0.1:9'
$env:AGENT_DSH_ENABLED='0'
```

### 具体命令

正常共享业务核心，135 项：

```powershell
python -m unittest tests.test_assessment_classification_routes tests.test_assessment_classification_service tests.test_assignment_submission_return_url tests.test_blog_community tests.test_blog_postgres_writes tests.test_blog_notifications tests.test_blog_sections tests.test_blog_opportunities tests.test_learning_progress_snapshots tests.test_learning_progress_stage_exam tests.test_session_learning_materials_service tests.test_session_material_generation_queue_claim tests.test_score_projection_service tests.test_materials_postgres_writes tests.test_collaboration_postgres_writes tests.test_account_todo_postgres_writes tests.test_postgres_metadata_helpers -v
```

调度与提醒，15 项：

```powershell
python -m unittest tests.test_scheduled_task_service tests.test_assignment_due_reminders_and_calendar_feed -v
```

正常账户、试卷及成绩，首次结果为 `Ran 22 tests / OK (skipped=2)`，其中 20 项已运行通过，另外两项在后述原生程序补跑：

```powershell
python -m unittest tests.test_agent_credential_changes.CredentialChangeTests tests.test_agent_identity_management.AgentIdentityManagementTests.test_ordinary_web_concurrent_demotion_keeps_one_active_admin tests.test_exam_paper_scope_access tests.test_grade_publication_service tests.test_grade_publication_merge_lock -v
```

原生迁移与部署门禁，30 项；原生权限全类，24 项。两组独立进程运行，测试类各自创建独立数据库：

```powershell
$env:ASSESSMENT_REHEARSAL_TEST_CLUSTER=Join-Path (Get-Location) '.codex-temp/dsh-pg-rehearsal/agent-20260910-172238-3bac5373'
$env:ASSESSMENT_REHEARSAL_TEST_PORT='55439'
python -m unittest tests.test_assessment_postgres_rehearsal tests.test_native_pg_deploy_gate tests.test_agent_authority_migration_rehearsal -v
python -m unittest tests.test_agent_authority_postgres -v
```

成绩合班两项原生补跑，使用相同公共环境和 `ASSESSMENT_REHEARSAL_TEST_*`，以 here-string 经 `python -` 执行以下程序：

```python
import os
import sys
import unittest
from pathlib import Path
from tools.assessment_postgres_rehearsal import connect_offline

admin = connect_offline(
    cluster_dir=Path(os.environ['ASSESSMENT_REHEARSAL_TEST_CLUSTER']),
    port=int(os.environ['ASSESSMENT_REHEARSAL_TEST_PORT']),
    database='lanshare_assessment_rehearsal',
)
admin.autocommit = True
created = False
try:
    admin.execute('CREATE DATABASE lanshare_miniapp_phase1 TEMPLATE template0')
    created = True
    os.environ['MP_PHASE1_POSTGRES_TEACHER_DSN'] = (
        'host=127.0.0.1 port=55439 dbname=lanshare_miniapp_phase1 user=rehearsal_admin'
    )
    suite = unittest.defaultTestLoader.loadTestsFromName('tests.test_grade_publication_merge_lock')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
finally:
    if created:
        admin.execute('DROP DATABASE lanshare_miniapp_phase1')
    admin.close()
sys.exit(0 if result.wasSuccessful() else 1)
```

### 保留证据

下列日志均位于工作区 `.codex-temp/`，不纳入生产部署包。路径以工作区为根，SHA-256 对本次实际日志字节计算。

| 日志 | 最终结果 | SHA-256 |
| --- | --- | --- |
| `checkpoint-business-d4c0110ebfcb4962ac36c4dfe28feae8/core.log` | `Ran 135 tests ... OK` | `378be5307b2daee06db25706fb8d907ba36ffaae490a76033e00a56209449201` |
| `checkpoint-business-1eb2a52e2d4c4d55a5f9b562eab2ab46/scheduler.log` | `Ran 15 tests ... OK` | `63e7e1d3ae2ea930487152366d6574b1ea321a1758f64c7152305877abf122cc` |
| `checkpoint-business-7f85340419244eaab7a798de2c7e29a0/native-gate.log` | `Ran 30 tests ... OK` | `63e868be04b845266292f49416b969059b0a916cb96d076a92b4af266de3fe48` |
| `checkpoint-business-a3af6a4bb02d4e5eb6154059e74fbf11/normal-accounts-and-assessment.log` | `Ran 22 tests ... OK (skipped=2)`；两项由下一行补齐 | `b727ec65b7ae7ae87ef6bdd84add01774e07ff3e582107b6b3f2affce2e1faa6` |
| `checkpoint-business-989a7b48ea2d4c6c8ea3331539561e7d/grades-native.log` | `Ran 2 tests ... OK` | `f066577bf2c6efd50458eaa906862f793ef501afb2c832eaad86d35fcd347a9b` |
| `checkpoint-business-0b62897aaf65458cb6327c837a1d73ae/authority-full-native.log` | `Ran 24 tests ... OK` | `6acc3c2a0db31139ad9dd00d6afe54923c0221d622986520b4d435736520722f` |

本轮没有发现新的失败或业务回归。日志包含一个既有 `datetime.utcnow()` 弃用提示，以及精简夹具缺少 `classroom_behavior_profiles` 的可选资料提示；相关断言通过，不能将这两条提示误报为线上业务故障。所有测试进程退出码均为 0。

下一批人工评分、考试命题/布置与成绩公布适配尚未开始写代码，本报告不覆盖这些尚未实现的能力。
