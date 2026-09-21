# 2026-09-20 本地测试隔离事故

状态：已完成现场保全和有界只读调查；未来测试入口已加隔离；历史数据影响尚未关闭。责任：本次执行改进计划的 Codex root。

## 已发生的事实

17:22:22–17:25:24（Asia/Shanghai），root 未使用明确隔离入口，执行 `python -m unittest discover -s tests -p 'test_*.py'`。该 discovery 方式不能保证先执行 `tests/__init__.py`，配置读取本地 `.env`，真实访问 `127.0.0.1:5432/lanshare`。结果为3500项、9失败、739错误、202跳过；不是有效的测试通过记录。

- `ScheduledTaskServiceTests` 每次setUp对默认连接执行无WHERE的 `DELETE FROM scheduled_tasks` 并提交。现场唯一行是 `unit_test_kind / unit:1`，状态done，时间09:25:05 UTC，对应该类最后一项测试。已确认本轮清空该表并写入测试记录；清空前任务数量和内容未知。
- 本地 `schema_migrations` 记录签章范围迁移 `20260908_signature_visibility_levels_v1` 于17:25:14成功提交。其逻辑会按组织字段重新调整部分签章的scope；实际受影响行数未知。
- 另8个模块对固定fixture ID执行默认库写入和清理，合计9模块39测试。本轮没有这些模块的失败记录；当前主表相应ID不存在，不能据此否认历史写入，或排除原有同ID数据被删除。
- 多次 `init_database()` 还可能提交幂等DDL和开课关联回填。日志中的“schema verified”不是只读保证，“269 created”也不代表实际新建269个索引。

当前核心记录仍存在（教师6、学生284、作业47、提交454），但计数不能证明内容完整。服务器未记录成功SQL，未启用WAL归档或提交时间追踪，且未找到事故前即时快照，因此无法完整重建原始任务和每条变更。

未连接远端生产数据库，未执行恢复、清理或回滚。

## 现场与历史证据

完整调查见 `.codex-temp/lq-s1-test-isolation-incident-audit.md`；源码提交风险表见 `.codex-temp/lq-s1-test-isolation-test-risk.md`。调查SQL使用明确loopback校验及只读事务，连接已关闭。

事故后现场保全文件：

`.codex-temp/lq-s1-test-isolation-incident/loopback-lanshare-incident-20260920-173459.dump`

文件5,130,299 bytes，SHA256 `a04dbcf2b8f13a4854acaad7fa57cac39b77a664381579923f294b624bb8e502`。pg_dump、TOC解析和完整离线解析均退出0。它是事故后的现场，不是回滚点，也未做恢复演练。

6月21日本地SQLite源 `.codex-temp/dev_source.db` 及其导出源副本均可读，核心计数与当前PG相同，但缺少实际PG导入回执，且距事故约三个月。9月10日两份可读PG dump明确来自远端，不能自动覆盖本地库。所有候选仅作参考，未导入。

## 防止再次发生

规范单测入口改为 `python tools/test_backend.py`，部署preflight及Liquid Glass执行计划同步更新。该入口在导入应用或测试前：

1. 关闭dotenv，清除继承的数据库配置与原生PG测试开关。
2. 建立独占临时data root并验证实际SQLite路径。
3. 拒绝psycopg直接连接及连接池使用的真实连接方法。

原生PG测试继续使用单独的显式独占演练簇。新 `tests/sqlite_database_fixture.py` 为直接使用应用连接的测试类提供独占SQLite库，恢复配置和schema缓存，并在缺表或路径不符时拒绝继续。它只改变测试环境，不修改产品schema，不删除业务断言。修正前后结果分别保留，详见 `lq-acceptance.md`。

补充核验：当前旧版 python-dotenv 不支持 `PYTHON_DOTENV_DISABLED`，因此早期 runner 的该环境变量及 `dotenv:false` 标签不足以证明没有读取 `.env`。实际修复使用 `tools/isolated_environment.py` 在应用导入前封闭 `dotenv.load_dotenv` 和 `dotenv.main.load_dotenv` 两个入口；规范 runner 及四个合成 harness 共用此守卫。新日志明确记录 `dotenv_loaders: blocked_before_app_import`。驱动阻断与继承数据库配置清理是完整单测 runner 的额外保护，不把它们误记为每个合成 harness 已实现的能力。

固定ID风险模块已改用 class-scoped 独占 SQLite；认证模板集成另显式创建教师、学生、课堂、作业和提交，不再借用默认业务库或缺数据时跳过。第二批四模块共23项通过，包含11项实际认证模板测试、0跳过；最终完整回归仍以验收文档中的独立结果为准。

另撤回 S0 历史完整回归已证明全新数据隔离的结论。其实际命令含 `-t .`，能先经 tests 包强制 SQLite，却没有设置独占 data root；当前默认SQLite文件的写入时间位于该轮运行窗口。保留3476项通过/206项跳过的原始结果，但不把文件时间推断成确切表级影响，也不以S0结果替代新入口的隔离证明。

## 尚待解决

已询问用户该本地库是否保存独有业务数据或定时任务。未收到答复前保留现场，不推断它可随意重建。任何恢复需先明确来源、需要保留的现状和具体差异，再形成单独可审阅方案；不会把修正测试入口当作已经消除本次数据影响。
