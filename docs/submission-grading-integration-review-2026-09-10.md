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

共享服务首轮收口组合运行 **121 项测试，121 项通过、0 项跳过**，实际执行约 11.6 秒。随后补齐当前 Web 客户端版本传递并复核成绩公布合并后，最终定向组合为 **133 项测试，133 项通过、0 项跳过**，实际执行约 15.4 秒。包含 SQLite 与真实 PostgreSQL 连接，不将跳过原生数据库测试的结果计为完整验证。

新增 [`tests/test_submission_write_guard.py`](../tests/test_submission_write_guard.py) 最终共 **26 项通过**：SQLite 和 PostgreSQL 各执行同一组 13 项真实路由回归，覆盖旧评分/AI/小组结果失效、提交和附件延迟清理、事务失败恢复、旧教师上传拒绝、并发删除引用、同名并发上传、教师开放重交、学生撤回最新评分检查、旧轮次文本与上传草稿拒绝，以及草稿 GET 的当前轮次经响应模型序列化后仍完整返回。

其余测试覆盖人工批阅并发冲突、评分修订、AI 回调、迟交策略、成绩投影、成绩公布、小组重新结算、首次/退回/缺交并发提交、草稿隔离、附件策略、公布撤回与合并的原生数据库锁、部署缓存保留。异常日志中的 `current transaction is aborted`、模拟小组结算失败、`division by zero` 等来自故意注入错误的回滚用例；最终测试结果为 `OK`。

执行命令（仓库根目录）：

```powershell
$phaseDsn = 'postgresql://miniapp_test_admin@127.0.0.1:55439/lanshare_miniapp_phase1'
$env:MP_PHASE1_POSTGRES_TEACHER_DSN = $phaseDsn
$env:MP_PHASE1_STUDENT_TEST_DSN = $phaseDsn
$env:PYTHONUTF8 = '1'
.\venv\Scripts\python.exe -B -m unittest tests.test_mp_grade_safety tests.test_ai_grading_service tests.test_group_assignment_service tests.test_score_projection_service tests.test_submission_question_file_policy tests.test_assignment_submission_return_url tests.test_grade_publication_service tests.test_wechat_mp_student_submission tests.test_submission_write_guard tests.test_grade_publication_merge_lock tests.test_deployment_browser_cache -q
```

PostgreSQL 使用本轮小程序审计创建的隔离 PG16，仅监听 `127.0.0.1:55439`；每个回归使用独立 schema 和合成样本，结束后删除测试 schema。未使用本机日常 `5432` 或生产数据库。独立实例的停止和目录清理由创建它的小程序审计流程负责。

## 当前 Web 客户端的补齐与验证

复核发现 `exam_take.html` 的共用草稿写入没有发送客户端版本，最终草稿同步失败时，在没有服务器附件的情况下还会继续提交当前页面内容。现有重新作答入口从已提交答案开始，不会调用本地草稿恢复，但这不能保护仍然打开的上一轮页面。

现在考试页和普通作业页从授权页面的提交快照注入固定 `submission_version`，考试的文本草稿、附件上传、附件清空及最终提交、普通作业最终提交均传递该版本。草稿没有 DELETE 写路由，清空统一通过 POST 的 `replace_question_ids` 完成。草稿 GET 返回可选轮次字段；考试页不会把新轮次草稿加载到旧页面，409 会保留本页答案、停止后续自动写入，并阻止最终提交的降级路径。

本轮没有修改本地缓存键，没有删除或迁移现有学生缓存。当前客户端传递版本；未更新的旧客户端仍保持兼容，使用服务端锁内状态检查，直至刷新取得新页面。

[`tests/frontend/exam_draft_version.test.cjs`](../tests/frontend/exam_draft_version.test.cjs) 直接执行当前考试页生产函数，**5/5 通过**。覆盖三类草稿写入固定版本、拒绝异轮次 GET、409 保留答案并停止后续写入、最终提交不绕过草稿冲突、成功交卷携带版本。

```powershell
node --test tests/frontend/exam_draft_version.test.cjs
```

## 成绩公布合并的独立复核

复核了合班专用策略与成绩公布服务的学生自有分数查询、版本延续、状态保留、来源快照及撤回流程。公布记录和学生成绩行保留原 ID 与内容，多份当前公布明确阻断并要求教师自行处理，迁移的版本映射和原状态进入合并归档。

独立复现了“主课堂当前公布 + 超过 50 份迁入历史”导致教师页 `current` 为空而学生仍可查看当前成绩的问题；对应策略实现者已修复为始终包含当前公布与最近历史。另发现公布撤回未使用合班的课堂行锁，可能使归档快照和并发撤回时序不一致；现已与公布和合班共用该锁。

新增 [`tests/test_grade_publication_merge_lock.py`](../tests/test_grade_publication_merge_lock.py) **2/2 原生 PostgreSQL 测试通过**：另一连接持有课堂锁期间撤回不能改动公布状态，释放后正常撤回；如果公布已迁入目标课堂，旧课堂撤回返回 409，目标记录不被误撤。另独立运行 `tests.test_offering_merge_service` 与 `tests.test_grade_publication_service`，**20/20 通过**。复核完成后未发现尚未处理的公布合并问题。

修改范围的 `git diff --check` 与 Python 编译检查均通过。未升级依赖，没有新增业务表或 API 路径。未发送真实通知、修改生产成绩或访问生产学生数据。
