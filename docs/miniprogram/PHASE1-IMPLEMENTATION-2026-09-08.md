# 微信小程序阶段 1 实施与验收记录

日期：2026-09-08。范围：实施规划 §11 的“核心业务正确性与数据隔离”。

后续记录：2026-09-10 分支归并时重新审计并补齐草稿客户端轮次校验，见 [分支归并复核](BRANCH-INTEGRATION-REVIEW-2026-09-10.md)。下文保留 2026-09-08 当次实施与验收状态，不代表当前统一发布结果。

**当前状态：本地实现与自动化验证已完成，等待微信真机和候选版验收。** 本次未部署后端、上传小程序或发布正式版；不能将本记录视为线上版本已经修复的证据。

本地基准 commit：`8f23bb4caa1e9f9a3e6e50a6b65c1f4e0a8f0095`；分支 `codex/assessment-multimodal-routing`。阶段 1 改动当前保留在工作区，尚未形成新提交或发布版本。微信构建目录为 `miniapp/dist/build/mp-weixin`，本次前端最终构建时间为 2026-09-08 22:24（Asia/Shanghai）。

## 1. 已实现内容

| 对应问题 | 现在的行为 | 主要实现位置 |
|---|---|---|
| C01 批阅串卷、空分、并发覆盖 | 切人立即清空旧答卷，按请求序号和提交 ID 丢弃过期响应；加载/保存期间禁用保存和切换。空分拒绝，显式 0 有效；服务端锁内重新读取、检查修订指纹，过期评分返回 409，页面保留未保存输入 | `miniapp/src/pages/teacher-grade/index.vue`；`services/submission_grade_guard_service.py`；`routers/homework_parts/grading.py` |
| C02 迟交重复扣分 | 输入回填原始分；80 分扣 10 得 70，重开只改评语仍保存为 70，改原分 90 得 80；系统生成的迟交说明不重复拼接 | 同上；`routers/mp/teacher.py` |
| C03 草稿串账号/轮次 | 本地草稿键包含 API 环境、角色、用户、任务、重交轮次；忽略历史无账号键。页面和异步请求绑定身份，旧账号响应不可更新新账号数据；同账号同轮次仍可恢复草稿 | `pages/task-detail/index.vue`；`utils/task-submission.ts`；`utils/api.ts` |
| C04 身份边界与绑定 | 专用 MP 与共享 API 复用实时停用/删除校验；保留多微信绑定一个平台账号。退出只解绑当前微信，重绑使该微信旧会话失效；绑定票据一次消费，失败事务可重试，频控由数据库跨进程共享 | `services/wechat_mp_service.py`；`routers/mp/auth.py`、`deps.py`；`db/schema_wechat_mp.py` |
| C05 退回重交 | 按服务端 `can_submit`、个人重交窗口与轮次显示退回原因、时间、旧答案及重交入口；旧附件明确需要重新上传。重交成功后旧有效成绩和旧 AI 批改任务失效但保留历史，后续按新答卷批阅 | `routers/mp/tasks.py`；`pages/task-detail/index.vue`；共享提交及成绩修订服务 |
| C06 合班成员口径 | 教师任务进度、批阅名单、未交与催交复用主班+挂链班的有效成员口径；不因名单为空重新纳入已停用或其他课堂学生的提交 | `routers/mp/teacher.py`；复用 `offering_membership_service` |
| 阶段 1 弱网及并发提交 | 文本草稿、附件上传与提交串行执行；上传中禁提交。提交结果未知时先查询，409 不自动重放。提交、文本草稿和附件草稿共用服务端锁，过期请求不能在提交后重建草稿；恢复前台重新校准服务端时间 | `pages/task-detail/index.vue`；`services/submission_write_guard.py`；`routers/homework_parts/{submissions,common,drafts}.py` |
| C11 冷启动、过期与深链接 | 所有受保护页面先等待统一会话，再选择角色 API；并发 401 只跳转一次，登录/绑定后恢复允许的目标页并重新核对角色。旧 200/401 响应不能污染新会话；角色 tab 仅在 tab 页设置，失败可重试 | `utils/session.ts`、`session-target.ts`；`stores/auth.ts`；`App.vue`、`main.ts`；页面入口及 `utils/tabs.ts` |

### 补齐的共享业务边界

- **小组重交**：撤销该成员旧作业分和小组揭晓状态，保留同组其他成员作业分及已有互评；新成绩回写后由既有结算流程重新计算。避免重交后仍从历史有效修订读取旧分。
- **绑定失败恢复**：失效票据使用稳定错误码 `mp_bind_ticket_invalid`；姓名/学号或密码错误不会误触票据刷新。绑定已提交但响应丢失时重新核对微信绑定状态。按钮在任何异步操作前锁定。
- **退出失败恢复**：网络异常不伪装解绑成功；`mp_logout_session_expired` 时先重新取得微信身份再重试解绑，避免清本地状态后立即静默登录回旧账号。
- **附件事务**：附件复制进入提交临界区，上传接收在加锁前完成；加锁后不等待网络。草稿读取/提交引用过滤上一重交轮次的文件记录，避免带入旧附件。清理前在锁内将旧文件/目录移到唯一的待清理路径，提交后仅删除这些路径，回滚时恢复，避免旧清理请求误删同名新文件。
- **同组并发与保存结果**：小组重交、手动批改、AI 回调及小组结果写入统一组锁顺序；结果计算异常退出事务，提交前检查 PostgreSQL 事务可用，避免内部通知捕获 SQL 异常后回滚却向客户端返回成功。

后端路径在表中省略统一前缀 `classroom_app/`。复用已有 Web 提交、批阅、迟交、小组和成绩修订逻辑，不另建小程序成绩系统。

## 2. 接口与数据库兼容

1. 新增两个运行时表及索引：`mp_consumed_bind_tickets`、`mp_bind_rate_limits`，沿用现有 engine-aware schema 初始化方式；不删除现有绑定记录，不新增“一账号仅一微信”的唯一约束。
2. 新客户端提交/草稿携带 `expected_submission_version`，批阅携带 `expected_review_revision`。版本冲突在服务端写入前返回 409；旧 Web 客户端未传版本仍可调用原端点，并受锁内权限/提交状态检查保护。
3. 错误仍保留原 `detail` 文本，额外通过 `X-LanShare-Error-Code` 标识需恢复票据或重新验证身份的情况。
4. PostgreSQL 对小组任务先按稳定次序取得当前/历史组的事务锁，再取得每学生每任务 advisory lock 和已有提交行锁；覆盖尚无提交行的首次提交，并与评分/小组结算协调。不同组及普通个人任务仍可并发。SQLite 使用已有 `BEGIN IMMEDIATE`。提交替换、AI 任务失效和有效修订退休在同一事务完成。
5. 没有新增 API 路由；本轮不直接覆盖全站路由快照。未来发版需匹配本轮前后端，旧小程序不具备新 UI 和客户端版本保护。

## 3. 已执行验证

| 验证层 | 结果 | 实际覆盖及限制 |
|---|---|---|
| 后端组合回归 | **178/178 通过，无跳过** | 认证/绑定、提交、批阅、订阅/课堂已有契约、迟交、成绩投影、小组、成员、AI 修订、文件策略与成绩发布；包含最后补充的附件清理和小组事务回归 |
| PostgreSQL 集成 | 已包含在上述通过结果 | 独立 PG16、仅回环地址、非默认端口、合成样本；真实连接与项目 SQL adapter。覆盖并发票据只消费一次、跨连接频控/重绑、首次/退回/缺交替换只写入一份、草稿不复活、同名附件及换轮目录不被旧清理删除、并发批阅一成功一冲突、同组重交/批阅、事务回滚及新成绩投影 |
| 前端 Vitest | **42/42 通过** | 会话、绑定页、作答页、批阅页、角色 tab 以及既有考核工具契约；包含延迟与乱序响应、身份切换、断网/超时恢复、409 保留输入及详情深链后角色 tab 刷新 |
| 类型检查、微信构建 | `npm run type-check`、`npm run build:mp-weixin` 通过 | 构建成功不等于微信开发者工具/真机验收或已上传 |
| H5 实际页面交互 | 320/390 CSS 像素宽度完成操作验证，无横向溢出 | 真实编译后的 Vue 页面；接口使用拦截的合成数据，仅替换 H5 不支持的 `uni.login` 平台函数；不访问真实账号/通知/成绩。详情冷启动 tab 更新异常已修复重验，控制台错误及未处理异常均为零 |

后端组合命令（仓库根目录）：

```powershell
# 先准备一个可丢弃的隔离 PostgreSQL，不能指向正式/日常开发数据库。
# 本次实际使用的地址如下；测试结束后集群已停止，目录保留情况见下文。
$phaseDsn = 'postgresql://miniapp_test_admin@127.0.0.1:55439/lanshare_miniapp_phase1'
$env:LANSHARE_MP_AUTH_TEST_DATABASE_URL = $phaseDsn
$env:MP_PHASE1_POSTGRES_TEACHER_DSN = $phaseDsn
$env:MP_PHASE1_STUDENT_TEST_DSN = $phaseDsn
.\venv\Scripts\python.exe -B -m unittest tests.test_wechat_mp_auth tests.test_wechat_mp_auth_postgres tests.test_wechat_mp_subscribe tests.test_wechat_mp_teacher_grading tests.test_wechat_mp_submission_review tests.test_wechat_mp_classroom_live tests.test_wechat_mp_student_submission tests.test_mp_grade_safety tests.test_score_projection_service tests.test_group_assignment_service tests.test_offering_membership_service tests.test_ai_grading_service tests.test_submission_question_file_policy tests.test_assignment_submission_return_url tests.test_grade_publication_service -q
```

不设置上述变量时，原生 PostgreSQL 测试会跳过；不能将这种运行称为完整阶段 1 验证。学生测试要求 URI 形式的 DSN，而非 libpq 键值形式。前端在 `miniapp` 目录分别执行 `npm test`、`npm run type-check` 和 `npm run build:mp-weixin`，无依赖升级。

H5 本地证据目录：[`../../.codex-temp/miniapp-phase1-20260908/visual-evidence/`](../../.codex-temp/miniapp-phase1-20260908/visual-evidence/)。其中截图记录原始分/最终分、切人加载禁保存、409 冲突、退回原因、窄屏、上传中禁提交及重交完成。它是本次工作区证据，未上传为公共链接。

环境收尾：本轮临时 PostgreSQL 已停止，55439 端口无监听。已核对 `pgdata` 的绝对路径、无运行 pid 文件、内部无指向外部的目录链接；删除动作仍被自动审批以 `blocked by policy` 拒绝，因此 `.codex-temp/miniapp-phase1-20260908/pgdata` 暂时保留。测试数据全部是本轮创建的隔离样本，未接触正式数据库；截图及本地 QA 脚本一并保留。

### 已知基线问题

- `tests.test_architecture_route_snapshot` 仍有历史基线差异：828 → 876，新增 48、删除 0，均非 MP 路由。需审查其他模块新增路由后更新基线，本轮未擅自接受差异。
- `tests.test_architecture_import_compatibility` 2 项通过。全站 schema 校验另有既存未注册表 `signature_material_snapshots`、`material_export_bundles`、`signature_application_batches`，与本轮两个 MP 运行时表不同，发版前单独收口。
- 上述是变更相关回归，未宣称全站全部测试通过。

## 4. 阶段 1 出口前的剩余验收

以下每项分别在至少一台 iOS 和一台 Android 微信中记录设备、基础库、候选版号、后端版本、样本、操作、实际结果、截图和必要的数据库对账。使用隔离班级和账号，避免写入正式成绩或向真实学生发送测试通知。

| 用例 | 操作与通过标准 |
|---|---|
| S1-01 身份/深链 | 从任务或批阅详情冷启动；过期 token、未绑定、错误角色分别尝试；只执行一次登录，成功到正确目标，教师底部显示“工作台”；停用账号的 MP/共享 API 均拒绝 |
| S1-02 换绑与草稿 | A 保存文字/附件并退出；B 绑定同一设备同一任务不能看到 A 内容；A 返回可恢复同轮次草稿。另一个微信绑定 A 不因本机退出失效；解绑断网和过期恢复有准确结果 |
| S1-03 批阅竞态 | 延迟并倒序返回 A/B/C 答卷、连续切人和保存；数据库写入对象与画面一致。两端同时批改同卷，一端冲突并保留输入，重新加载后才能继续 |
| S1-04 迟交 | 原分 80 扣 10，最终 70；只改评语仍 70；原分改 90 得 80；空值拒绝，主动 0 正常；迟交说明不重复 |
| S1-05 跨端重交 | 学生交作业→教师 Web 退回→小程序查看原因/剩余时间、重新上传并提交→教师小程序批改→Web/小程序核对新结果；窗口过期、旧卷批阅、缺交记零替换、小组重新结算各抽查 |
| S1-06 合班 | 主班/挂链班/停用/已退出/无权限样本逐项核对名单、应交、已交、待批、未交及催交对象，不漏有效成员，不重新纳入无效成员 |
| S1-07 弱网附件 | 大附件上传中禁提交；断网、提交已受理但响应丢失、恢复前台及计时到期分别操作；只存在一份有效提交，迟到的草稿不能复活，用户输入/附件状态明确 |
| S1-08 兼容与候选版 | 将匹配的后端与小程序作为一个候选版验收；旧 Web 客户端继续可提交/评分，旧小程序继续可登录；核对正式/体验/后端版本和全站基线问题处理记录 |

这些真机条目尚未执行，因此规划中的阶段 1 出口保持待验收。阶段 2 的通知可靠性和阶段 3 的课堂/结果投影问题仍按原排期推进。
