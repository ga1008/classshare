# 提交与评分分支归并复核（2026-09-10）

本记录用于仅保留 `main`、`dev` 前的功能完整性审计，覆盖工作区现有提交、草稿、人工批阅、AI 回写、评分修订与小组结算改动。最终 Git 提交、生产发布版本及远程同步状态另见统一发布记录。本文件不包含生产学生资料或真实成绩。

## 已复核的业务链路

- 首次提交、缺交占位替换、教师开放重交、学生再次提交共享每学生每任务写入锁；已有提交还与评分行锁协调。
- 人工批阅校验有效原始分、迟交扣分及批阅版本，保存评分与修订账本同成同败；旧 AI 任务失效，历史修订保留。
- AI 回写验证任务与答卷指纹，拒绝过期结果；失败重批保留此前有效成绩。小组结算失败或 PostgreSQL 事务已中止时，不返回虚假的保存成功。
- 替换答卷时退休旧有效评分、清空有效修订指针、失效旧 AI 任务，并撤销该成员工作分及小组公布状态；其他成员工作分与互评保留，待新答卷评分后再次结算。

## 本次补齐的遗漏

1. **草稿的客户端轮次保护。** 文本、上传和附件清空均接收 `expected_submission_version`，并在持有事务锁后校验。旧页面即使在教师再次退回之后才发起请求，也会返回 409，不写入新轮次草稿。小程序同步携带操作开始时的版本，见 [小程序归并复核](miniprogram/BRANCH-INTEGRATION-REVIEW-2026-09-10.md)。
2. **教师附件编辑的评分闭环。** 增删附件原先只清空提交表分数，可能通过有效评分修订或小组账本重新显示旧成绩。现在同事务退休旧修订、失效 AI 任务、清空迟交结果字段、撤销该成员工作分与小组公布状态。
3. **教师增删附件与学生重交的并发保护。** 两条附件写入路径加入统一任务锁并复读当前提交；上传期间答卷被替换时返回 409。同名并发上传分别保存；并发删除基于加锁后的答案清除附件引用，不恢复另一请求已经删除的引用。
4. **教师退回的完整状态转换。** 开放重交时立即退休旧评分和 AI 任务，撤销旧小组结果，不等学生真正重交后才失效。批量操作先按全部受影响小组的稳定顺序取锁，再取得各学生任务和提交行锁，防止与同组评分、重交形成相反的锁顺序。
5. **撤回和删除的文件安全。** 教师删除、学生撤回及附件删除均在任务锁内把旧路径移到唯一隔离路径；成功提交后仅删除隔离路径，回滚时先恢复文件再释放锁。旧请求的延迟清理不再删除新一轮同名文件或目录。学生撤回在取得锁后检查最新评分状态，不能删除刚刚完成批阅的答卷。

## 最终验证

最终组合运行 **121 项测试，121 项通过、0 项跳过**，实际执行约 11.6 秒。包含 SQLite 与真实 PostgreSQL 连接，不将跳过原生数据库测试的结果计为完整验证。

新增 [`tests/test_submission_write_guard.py`](../tests/test_submission_write_guard.py) 共 **24 项通过**：SQLite 和 PostgreSQL 各执行同一组 12 项真实路由回归，覆盖旧评分/AI/小组结果失效、提交和附件延迟清理、事务失败恢复、旧教师上传拒绝、并发删除引用、同名并发上传、教师开放重交、学生撤回最新评分检查、旧轮次文本与上传草稿拒绝。

其余 97 项覆盖人工批阅并发冲突、评分修订、AI 回调、迟交策略、成绩投影、成绩公布、小组重新结算、首次/退回/缺交并发提交、草稿隔离和附件策略。异常日志中的 `current transaction is aborted`、模拟小组结算失败、`division by zero` 等来自故意注入错误的回滚用例；最终测试结果为 `OK`。

执行命令（仓库根目录）：

```powershell
$phaseDsn = 'postgresql://miniapp_test_admin@127.0.0.1:55439/lanshare_miniapp_phase1'
$env:MP_PHASE1_POSTGRES_TEACHER_DSN = $phaseDsn
$env:MP_PHASE1_STUDENT_TEST_DSN = $phaseDsn
$env:PYTHONUTF8 = '1'
.\venv\Scripts\python.exe -B -m unittest tests.test_mp_grade_safety tests.test_ai_grading_service tests.test_group_assignment_service tests.test_score_projection_service tests.test_submission_question_file_policy tests.test_assignment_submission_return_url tests.test_grade_publication_service tests.test_wechat_mp_student_submission tests.test_submission_write_guard -q
```

PostgreSQL 使用本轮小程序审计创建的隔离 PG16，仅监听 `127.0.0.1:55439`；每个回归使用独立 schema 和合成样本，结束后删除测试 schema。未使用本机日常 `5432` 或生产数据库。独立实例的停止和目录清理由创建它的小程序审计流程负责。

修改范围的 `git diff --check` 与 Python 编译检查均通过。未升级依赖，没有新增业务表或 API 路径。未发送真实通知、修改生产成绩或访问生产学生数据。旧 Web 客户端未传版本时仍兼容，但新增的客户端旧页面识别需要发送版本字段；服务端锁内状态检查与文件安全适用于所有调用方。
