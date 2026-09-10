# Agent 考核业务接入与验证（2026-09-10）

本批在本地检查点 `4958a4a5` 之后完成，仅为实现和隔离验证，尚未上线，也不是最终源码冻结后的生产迁移门禁。没有改动数据库结构、生产数据、真实用户会话或模型密钥。

## 实际能力与业务边界

新增 5 个直接业务动作：`create_exam_paper`、`update_unassigned_exam_paper`、`assign_exam_paper`、`manual_grade_submission`、`withdraw_grade_publication`。它们使用当前真实教师身份，调用普通 Web 共用业务服务；业务记录与 Agent 回执同事务提交，失败一起回滚，相同操作编号和参数重试只读取既有回执。

新增 5 个经过实际挂载路由验证的读取：`exam.catalog`、`exam.review`、`assignment.review_submissions`、`submission.review`、`submission.review_files`。试卷目录按候选行分页后复用正常可用范围过滤；答卷附件按 50 项分页，仅返回文件标识与名称、类型、大小，平台存储路径不进入模型。附件内容通过已有 `platform_file` / `platform_download` 再次按原下载权限读取。

新增 `publish_classroom_grades`，执行模式为 `user_confirmation`。它在模型能力目录中标记为需本人确认，不能进入 MCP 直接写入分发器。模型可以提出课堂及来源材料，当前用户须打开平台生成的真实快照、逐项核对警告、填写说明并点击确认。服务器执行端才赋予普通公布服务的 `confirmed=True`，不接受模型传入此字段，也不混用账号密码安全输入模式。

确认令牌绑定课堂、材料、来源哈希、公布版本和完整核对快照哈希。完整哈希覆盖实际学生名单、姓名、分数、公式、学期及警告；普通公布服务对其最终用于落库的同一个快照再次比对，避免“材料未变、名单已变”沿用旧确认。核对说明和警告选择进入幂等回执参数，改变声明不能冒充同一次请求重放。

试卷创建、内容保存与布置提取到 `exam_paper_management_service.py`；普通 Web 对应入口也调用该服务。题目沿用原生 JSON 格式与完整评分校验；布置保留原有 AI 评分模式、正式作业/期中/期末分类及修订、学生通知、截止提醒、迟交政策、课堂标签。普通试卷编辑与布置先锁同一试卷行，重复布置和陈旧版本不会同时成功。

Agent 仅原地修改尚未布置的试卷；已经布置的试卷要求创建新版本，即使暂时没有学生答卷。普通 Web 已布置试卷的原有内容校验保持不变。本批未宣称修复其计数检查与首次学生草稿之间的所有历史竞争，也未将整个普通内容路由标记为无限制直接执行。

人工评分提取到 `submission_grading_service.py`。普通 Web 与 Agent 复用答卷权限、答卷版本、迟交扣分、AI 任务失效、历史成绩版本、小组结算和通知。Agent 评分额外要求携带作业/评分标准版本；按作业、组、答卷顺序锁定并复核，避免在教师修改评分标准时提交旧标准的评分。保存单份答卷分数不等同于课程最终成绩公布。

## 验证结果

本批最终相关验证：77 项 SQLite/HTTP/普通业务测试、22 项真实 PostgreSQL 测试、9 项浏览器测试通过。另独立复验账户批量授权重构后的 5 项原生 PostgreSQL 凭据竞争测试通过，不计入上述考核测试数量。

SQLite/HTTP 合并命令运行 76 项，通过后新增撤回原子性用例单独运行通过，合计 77 项：

```powershell
python -m unittest tests.test_agent_exam_actions tests.test_agent_assessment_actions tests.test_agent_assessment_mcp tests.test_mp_grade_safety.ManualGradeSafetyTests tests.test_wechat_mp_teacher_grading tests.test_assessment_classification_routes tests.test_exam_paper_scope_access tests.test_grade_publication_service tests.test_agent_secure_confirmation_http tests.test_agent_mcp_bridge -v
python -m unittest tests.test_agent_assessment_actions.GradePublicationConfirmationHTTPTests.test_authorized_withdrawal_keeps_snapshot_and_receipt_in_the_same_transaction -v
```

测试显式设置 `PYTHON_DOTENV_DISABLED=1`、`DB_ENGINE=sqlite`、空 `DATABASE_URL`、随机 `.codex-temp/checkpoint-business-*` 数据根、合成 `SECRET_KEY`、空初始管理员密码和不可用的 loopback AI 地址，禁用 DSH。每个测试实际业务数据来自独立 SQLite fixture，没有复用应用数据库。测试初始化过程中发现的缺少合成字段、旧测试补丁位置和令牌 scope 问题已经修正，没有放松业务权限或断言。

真实 PostgreSQL 使用已有专用集群 `.codex-temp/dsh-pg-rehearsal/agent-20260910-172238-3bac5373`，监听仅 `127.0.0.1:55439`。连接先通过 `connect_offline` 校验实际 data_directory、监听地址、端口与专用 DB 名，不读取应用 DATABASE_URL。考试测试创建随机 `lanshare_assessment_rehearsal_agent_*` 数据库；评分与合班测试独占创建 `lanshare_miniapp_phase1`，在 `finally` 中删除本次创建的库，不复用或删除已有同名数据库。所有本批临时 DB 已清理，专用集群继续保留供其他隔离验证。

```powershell
$env:ASSESSMENT_REHEARSAL_TEST_CLUSTER = Join-Path (Get-Location) '.codex-temp/dsh-pg-rehearsal/agent-20260910-172238-3bac5373'
$env:ASSESSMENT_REHEARSAL_TEST_PORT = '55439'
python -m unittest tests.test_agent_exam_postgres -v
```

经验证连接后独占创建上述 phase1 合成库，再设置仅指向该库的 `MP_PHASE1_POSTGRES_TEACHER_DSN`，运行：

```powershell
python -m unittest tests.test_mp_grade_safety.NativePostgresGradeSafetyTests -v
python -m unittest tests.test_grade_publication_merge_lock -v
```

原生结果分别为考试 3 项、评分 17 项、撤回/合班 2 项。关键证据包括：普通 Web 与 Agent 竞争布置同卷只产生一份业务记录；编辑提交后等待中的 Agent 版本失效；业务与分类在回执失败时一起回滚；评分等待评分标准编辑后返回 409；小组重交与评分保持既有锁序；撤回等待合班后重新验证公布记录归属。

```powershell
npx playwright test --config tests/e2e/components/playwright.config.ts agent-user-confirmation.spec.ts grade-publication.spec.ts
node --check static/js/agent_user_confirmation.js
node --check static/js/ai_workspace_widget.js
npm run typecheck
git diff --check
```

浏览器 9 项通过，使用真实确认模块、平台 modal 与编译后的现有样式，模拟接口而不启动业务服务。覆盖未默认勾选、警告/说明/最终确认联动、零分与缺分区分、HTML 转义、390px 手机宽度、提交期间禁止重复与关闭、503 保留输入、材料变化或仅核对名单变化重新勾选、丢失响应后查询真实提案回执而不重复公布。桌面/手机截图已实际查看：`.codex-temp/agent-grade-confirmation-desktop.png`、`.codex-temp/agent-grade-confirmation-mobile.png`。本批未声称已进行真实 DSH 完整任务或线上浏览器验收。

## 后续集成要求

由统一能力台账整合者更新 5 读、5 写和 `user_confirmation` 1 项的来源映射与限制；保持剩余普通业务操作可见且待适配，不能由这批结果推导“全平台已覆盖”。最终源码冻结后仍需完整回归、原生生产备份迁移门禁和独立 DSH 任务验收。
