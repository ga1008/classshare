# 修为投影批处理参数与配置恢复说明

本轮 B 候选将周期投影拆为较短批次，以减少与前台请求同时争用 CPU 和数据库的连续窗口。参数变更不改变计分、批改、破境和证书规则，也不把页面读取恢复为即时重算。是否达到容量目标，需要独立的原生 PostgreSQL 首次生成测试和相同负载的完整对照压测确认。

## 默认参数与边界

| 环境变量 | B 默认值 | 服务端允许范围 | 含义 |
|---|---:|---:|---|
| `CULTIVATION_SNAPSHOT_REFRESH_LIMIT` | 25 | 1–100 | 每批最多检查的脏投影键数 |
| `CULTIVATION_SNAPSHOT_REFRESH_BUDGET_SECONDS` | 0.75 | 0.1–3.0 | 单批软时间预算，秒 |
| `CULTIVATION_SNAPSHOT_DISCOVERY_LIMIT` | 25 | 1–100 | 每批首次／旧模型发现上限，独立于刷新上限 |
| `CULTIVATION_SNAPSHOT_DISCOVERY_BUDGET_SECONDS` | 0.1 | 0.01–0.5 | 发现软预算，且不超过总预算的四分之一 |
| `CULTIVATION_SNAPSHOT_REFRESH_INTERVAL_SECONDS` | 3 | 1–60 | 成功完成后，下次计划运行的间隔，秒 |
| `SCHEDULER_POLL_SECONDS` | 代码默认为 5 | 现有 worker 最少为 5 | 无到期工作时的轮询等待，秒 |

无效或非有限的投影参数使用默认值，超出范围的有限数值收敛到边界。环境示例在 `docker.env.example`。应用和 scheduler 均通过 Compose 已有的 `env_file: ./docker.env` 读取这些参数；缺失时采用代码默认值。scheduler 的 `environment` 不再二次插值覆盖它们，因此现有部署流程不需要新增 `--env-file`，修改真实 `docker.env` 后可以按正常流程生效。配置在进程导入时读取，修改示例文件不会自动改变已经运行的进程。

时间预算在投影键之间检查。已经开始的 SQL 或原子发布必须完成，因此单批可能超过预算一个键的成本；该参数不是取消事务的硬超时。发现查询也占总耗时。`3` 秒计划间隔不等于每三秒执行一次：空轮询至少五秒，还需计入本批耗时、其他 normal 任务和数据库等待。

持久任务可能仍保留旧 payload 中的 `limit=100`。handler 总是应用当前系统上限；显式较小的 payload 继续有效。手工调用 `refresh_pending_learning_snapshots` 的原有默认 `limit=100`、三秒预算及四分之一发现配额保持不变；只有显式传入新关键字参数的调用才采用独立发现配额。

## 定时维护与业务一致性

启动时保留旧定时任务的提前安排。运行期维护使用 `advance_pending=False`，不提前健康 pending 的 `run_at`，不重置 retry backoff、排队年龄和 `updated_at`，也不修改 running／cancelled 行或运行认领。缺失系统定时任务会持久创建；只有本任务已耗尽重试且冷却至少六十秒的 failed 行按原 CAS 条件恢复，保留错误诊断，不批量重放其他失败任务。

最早 `dirty_at`、脏版本、发布 CAS、每键短事务、过期结果整组回滚和旧值展示保持原样。三条调度 lane 和现有优先级／aging 规则保持原样。两个刷新之间存在其他 normal 工作的调度机会，但任意长的 handler、无限高优先级积压或持续数据库故障不能由这些参数变成硬两分钟保证。验收必须记录实际投影尾延迟、最老 dirty 年龄及其他 normal 任务的等待。

## 恢复到较大批次配置

若 B 的独立验证表明需要恢复原批处理尺度，可评审以下配置组合：刷新上限 `100`、刷新预算 `3` 秒、计划间隔 `20` 秒、发现上限 `25`、发现预算 `0.5` 秒、worker poll `20` 秒。原先运行期维护曾提前健康任务的计划时间，因此恢复这些数值不会复现每个历史执行时间点；不应为复现旧节奏而恢复该隐式提前行为。

恢复需要修改真实 `docker.env`，通过现有部署流程重建相关应用／scheduler 容器，使新进程读取有效配置；单纯重启旧容器不会替换其环境变量。先核对配置来源、备份和当前在途任务，等待正常 drain，保护数据目录，再确认新进程实际采用的参数、任务认领和页面读取。这里不是一键恢复操作，也不授权部署。配置恢复不需要清空投影、行为回执、业务事件或重建用户数据；不得使用 `replace=True` 重置运行认领来应用参数。

## 合成验收的测量边界

A 轮保留原始证据：旧 helper 在发布函数返回时记录事件，早于 owner 事务提交。不能事后把这些时间改称提交确认时间。B helper 只在 owner 提交成功后，重新读取该键的可见 `calculated_version` 并确认其覆盖待观测版本，才记录较晚的观测时间；这也排除了外层 SAVEPOINT 撤销投影后仍提交其他业务的假确认。验证读取事务由正常 exit／close 收口，不修改产品事务实现。

回滚、关闭、提交结果不明或验证读取失败均不能伪记成功。观测失败单列报告，不能把已提交业务变成重试；最终还要独立核对每人的 `dirty`、`dirty_version`、`calculated_version`。内存／SQLite 受控时钟用例只证明预算和 building→ready 的业务闭环，不代替原生 wall-clock 结果。合成供应商等待也不等于真实 AI 批改或证书生成容量实测。

## Scheduler进程信号与停机预算（候选，需专项验收）

正常启动链为Docker的init进程转发信号，经`deployment/docker/entrypoint.sh`
的`exec python -u scheduled_task_dispatcher.py`到Python。入口显式接管SIGTERM和
SIGINT：首次请求停止业务root；重复信号不再取消正在排空的任务。入口等待两个
既有worker的cleanup，Runner排空执行器后再同步写stopped心跳并关闭连接池；入口
可观察的真实异常保持error诊断和非零
退出，不把有异常原因的取消伪报成功。单个业务任务的failed／retry结果仍按原合同。
正常`--once`仍仅执行一批并记录once，收到停止信号后只等待当前owned工作，不继续
认领后续项，也不伪报once完成；正常once终态同样在Runner退出后写入，避免旧心跳
迟到覆盖stopped。初始化完成后还会再次检查停止请求，不因初始化期间的信号而启动
新的claim／worker。关闭本进程连接池后最后
恢复原信号处理器。未初始化时不为记录停止而另开数据库连接。

该调整只补scheduler进程入口，不修改业务handler、三lane、lease/fence或其他worker。
同进程散修inbox仍按其既有策略取消active并等待cleanup；不能宣称全部AI都等待到
供应商完成。其持久response_json/next_part和150秒lease按原合同恢复。

只有scheduler的Compose停机宽限改为5分钟，适用于没有显式覆盖超时的普通stop／
recreate。它是容器预算，不是最长任务或硬完成保证：阶段试炼单次AI请求配置300秒
（learning_progress_service.py），课时材料单次240秒，职业路径默认240秒；处理器
还包含队列等待、数据库、文件和结果提交，AI gateway没有统一的整项等待截止。
发布前应先核对并等待长任务／排空，不能只依赖五分钟后停止。

现有`deployment/deploy_remote.ps1`的quiesced migration路径显式对writer_services
（包含scheduler）执行`compose stop --timeout 120`，会覆盖Compose五分钟。本补丁
不改该部署流程，不能宣称所有部署都有五分钟或保证全部完成；迁移发布前尤其需要
先等待长任务排空。显式CLI timeout、强制停止、机器故障或SIGKILL均不会执行完整
finally，不能伪记已安全停止／归还任务。未确认claim／提交只保留诊断，后继worker
依现有lease过期（scheduler默认15分钟）和claim token CAS接管，不新增自动重放或
批量重置pending，更不能回退已经提交的学生业务数据。

Windows离线signal callback测试只验证入口合同，不等于Docker/Linux信号实测；
Windows TerminateProcess也不等于可处理的SIGTERM。后续需独立验证真正子进程信号、
Compose配置和停止结果，保留强杀边界。本轮稳态一小时不因入口草案被改写为已验证。
