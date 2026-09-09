# 小程序分支归并复核（2026-09-10）

本记录对应仅保留 `main`、`dev` 前的功能审计。复核对象为阶段 1 现有工作区实现及其共享提交、批阅服务；此前实施记录见 [PHASE1-IMPLEMENTATION-2026-09-08.md](PHASE1-IMPLEMENTATION-2026-09-08.md)。最终提交号、服务器版本和远程分支同步结果由本次统一发布记录登记。

## 实现完整性

- 认证：核对停用/删除身份拒绝、一个微信绑定一个平台账号、平台账号可有多个微信、退出只解绑当前微信、重绑撤销旧会话、绑定票据一次消费与失败事务恢复、跨连接频控。
- 会话：核对冷启动等待身份、登录后受控深链回跳、角色访问核对、并发 401 单次跳转、旧账号响应不覆盖新会话、角色 tab 更新与失败恢复。
- 学生：核对账号/环境/任务/重交轮次隔离草稿、退回原因和个人重交时间、文本与附件串行写入、提交结果未知时先查询、上传期间禁提交、跨端重交后旧成绩与旧 AI 结果退休。
- 教师：核对乱序答卷响应、切人即时清空旧输入、显式零分与空分区别、原始分与迟交最终分、409 保留本地输入、合班有效名单及待批口径。
- 兼容：`/api/mp/*` 继续承担聚合，提交和评分复用已有 `/api/*` 业务；新增数据库表采用扩展式初始化；未传版本的旧客户端继续适用锁内权限与提交状态检查。

本轮发现并补齐了一处遗漏：小程序的文本草稿、附件上传、清空附件原先没有携带 `expected_submission_version`，共享草稿处理也没有核对该客户端版本。教师再次退回后，旧页面的请求可能从新一轮服务端状态开始执行，因此仅比较服务端加锁前后的读取不足以识别旧页面。本轮三种写入均携带操作开始时已打开答卷的版本，服务端在事务锁内校验；上传多文件期间即使前台刷新发现新轮次，后续文件也保留原操作版本，使旧轮次请求返回 409 后不写入新轮次草稿。共享服务及旧轮次回归由本次提交/评分审计同步补齐。

## 当次验证

| 验证 | 结果 | 说明 |
|---|---|---|
| 小程序 `npm test` | 44/44 通过 | 执行实际 Vue 页面脚本和会话工具；新增三种草稿写入版本契约、多文件上传中途换轮次回归 |
| `npm run type-check` | 通过 | 最终草稿版本字段修改后执行 |
| `npm run build:mp-weixin` | 通过 | 最终代码构建，沿用锁定依赖，无依赖升级 |
| MP 既有 API 组合 | 59/59 通过 | 认证、订阅、教师任务、答卷投影、课堂聚合 |
| 阶段 1 PG/提交/评分组合 | 59/59 通过，无跳过 | 初次复核；后续共享草稿及附件写入修复的最终回归由统一发布记录登记 |
| 变更空白检查 | 通过 | `git diff --check`，覆盖小程序及 MP 认证/任务/教师后端 |

后端命令：

```powershell
.\venv\Scripts\python.exe -B -m unittest tests.test_wechat_mp_auth tests.test_wechat_mp_subscribe tests.test_wechat_mp_teacher_grading tests.test_wechat_mp_submission_review tests.test_wechat_mp_classroom_live -q

$phaseDsn = 'postgresql://miniapp_test_admin@127.0.0.1:55439/lanshare_miniapp_phase1'
$env:LANSHARE_MP_AUTH_TEST_DATABASE_URL = $phaseDsn
$env:MP_PHASE1_POSTGRES_TEACHER_DSN = $phaseDsn
$env:MP_PHASE1_STUDENT_TEST_DSN = $phaseDsn
.\venv\Scripts\python.exe -B -m unittest tests.test_wechat_mp_auth_postgres tests.test_wechat_mp_student_submission tests.test_mp_grade_safety -q
```

PG16 使用本轮创建的 `.codex-temp/branch-audit-miniapp-20260910/pgdata`，仅监听 `127.0.0.1:55439`。测试创建和清理自身的独立 schema，数据为合成样本；没有使用本机日常 `5432` 或正式数据库。测试中出现的事务异常日志来自故意注入异常的回滚用例，最终结果为 `OK`。

## 发布范围和待验收边界

本轮服务端部署可以使共享认证、提交、评分保护生效；新增小程序交互只有在对应小程序包上传和发布后才到达正式微信客户端。构建成功、API 回归和服务器部署均不代表已经完成微信上传、审核、正式发布或 iOS/Android 真机验收。阶段 1 真机清单仍按原记录保留待验收状态。

实施规划中尚未开展的通知可靠性、课堂容量、完整教师工作台、AI 助手、材料轻浏览等后续里程碑，继续保留明确范围和验收入口；本次归并不将其标为已完成，也不因为移除分支而删除相关规划或未发布源代码。
