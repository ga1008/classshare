# P11 实施与验收记录

状态：第一阶段已落地并通过本地验收
日期：2026-06-04
执行人：Codex

## 本轮落地范围

本轮把 P11 收束为两个可执行成果：

1. 性能方向不先归因 SQLite，而是建立 200 人课堂 profile、复制库数据安全门、p95 指标输出和上线前失败门。
2. 建立统一后台任务运行台账，把 AI 批改、材料 AI 导入、课堂材料生成、消息中心 AI 回复、邮件、博客爬虫、agent worker、行为写入管线纳入同一套可观测快照。

本轮没有变更线上业务表结构，没有改业务 URL，没有对真实 `data/classroom.db` 或远程 `/lanshare/data` 执行写入压测。

## 关键代码变化

- 新增 `classroom_app/services/background_task_registry_service.py`：统一注册后台任务类型、展示名、恢复策略和状态来源。
- 新增 `classroom_app/services/background_task_ledger_service.py`：生成后台任务台账快照，汇总队列深度、运行中、失败、疑似卡住、worker、最近错误、心跳和恢复能力。
- 扩展 `classroom_app/app.py`：`/api/internal/health`、`/api/internal/metrics` 增加后台任务摘要；新增 `/api/internal/background-tasks` 安全摘要接口。
- 扩展 `classroom_app/routers/manage_parts/system_config.py`：新增 `GET /api/manage/system/background-tasks`，仅超管可访问完整台账。
- 扩展 `templates/manage/system/diagnostics.html`：系统诊断页展示后台任务运行台账，并给出 P11 200 人 profile 推荐命令。
- 扩展 `tools/full_stack_load_test.py`：新增 `--profile classroom-200`、`--runtime-root`、`--json-output`、数据安全检查、复制库 `PRAGMA quick_check`、门禁汇总、密码脱敏、场景并发整形。
- 调整 `classroom_app/routers/files.py`：过滤真实断开连接导致的良性 WebSocket 噪声，避免把压测期间的正常断连误记为运行时错误。
- 新增回归测试：后台任务台账聚合、权限、压测 profile、P11 诊断页真实渲染。

## 200 人 profile 验收结果

执行命令：

```powershell
python tools/full_stack_load_test.py --profile classroom-200 --runtime-root .codex-temp\p11-load-runtime --json-output .codex-temp\p11-artifacts\classroom-200.json --keep-artifacts --ai-mode mock --port 18720 --ai-port 18721 --startup-timeout 120 --request-timeout 60 --max-connections 500
```

结果摘要：

- profile：`classroom-200`
- 学生数：200
- 场景并发：50
- 执行时长：242.2 秒
- 总场景成功率：100%
- HTTP 5xx：0
- action failures：0
- WebSocket 运行时错误：0
- gate summary：passed
- 源库写入：false
- 源库 quick_check：ok
- 复制库压测前 quick_check：ok
- 复制库压测后 quick_check：ok
- 产物：`.codex-temp/p11-artifacts/classroom-200.json`

主要 p95 热点：

| 动作 | 成功率 | p95 |
| --- | ---: | ---: |
| student_login | 100% | 18967.82ms |
| dashboard_page | 100% | 14386.60ms |
| private_message_send | 100% | 13016.48ms |
| classroom_page | 100% | 8713.65ms |
| discussion_websocket | 100% | 6236.38ms |
| assignment_submit | 100% | 4711.83ms |
| message_center_summary | 100% | 3599.05ms |

结论：本轮 200 人 profile 已经形成可重复上线前压力门，并且在复制库、HTTP 5xx、WebSocket 错误和 quick_check 维度通过。p95 仍暴露出登录、dashboard、消息发送、课堂页等下一轮性能优化靶点，不能宣称这些链路已经达到最终性能目标。

## 后台任务台账验收结果

已纳入的任务类型：

- `ai_grading`
- `material_ai_import`
- `session_material_generation`
- `private_message_ai_reply`
- `email_outbox`
- `blog_news_crawler`
- `agent_task`
- `behavior_write_pipeline`

接口与权限：

- `GET /api/internal/background-tasks`：返回安全摘要，不暴露完整错误细节。
- `GET /api/manage/system/background-tasks`：超管返回完整台账；普通教师返回 403。
- `/api/internal/health` 与 `/api/internal/metrics`：返回后台任务摘要，部署后可用于快速诊断。

脱敏要求：

- 最近错误会截断。
- `api_key`、`token`、`cookie`、`password`、`secret`、`sk-...` 等敏感字段会脱敏。
- 诊断页不输出真实密码、cookie、token 或完整请求正文。

当前复制库快照中可见的历史问题：

- `ai_grading` 存在既有 failed 记录。
- `blog_news_crawler` 存在既有 failed 记录。

这些问题被台账明确暴露为 `problem_task_types`，不是本轮压测新增失败。后续处理时应在复制库先验证恢复策略，再对线上状态做人工确认，严禁为了让健康检查变绿而直接篡改线上任务状态。

## 已通过测试

```powershell
python -m unittest discover -s tests -p "test_*.py"
npm run typecheck
npm test
npm run build
npm run test:e2e:p03
```

结果：

- 后端 unittest：71 passed
- TypeScript typecheck：passed
- Vitest：18 files / 43 tests passed
- Vite production build：passed
- P03 Playwright：21 passed

说明：

- 后端测试中仍有既有 `ResourceWarning` 和 `datetime.utcnow()` deprecation warning，未阻塞本轮 P11 验收。
- build 中仍有 Browserslist 数据过期提示，未阻塞产物生成。
- P03 已新增 P11 后台任务诊断页回归：超管可渲染台账，普通教师不能读取完整台账。

## 数据安全确认

- 压测 runtime root：`.codex-temp/p11-load-runtime`
- 压测复制库：`.codex-temp/p11-load-runtime/isolated/classroom.db`
- 压测报告：`.codex-temp/p11-artifacts/classroom-200.json`
- `writes_to_source_db=false`
- 源库和复制库 quick_check 均为 `ok`
- 未在远程生产站点执行 200 人写入压测
- 未对远程 `/lanshare/data` 执行破坏性验证

## 后续优化入口

P11 下一阶段应优先处理以下真实热点，而不是泛泛替换 SQLite：

1. `student_login` 与 session 写入路径：p95 最高，需要拆解认证查询、session 写入和登录后跳转链路。
2. `dashboard_page`：p95 偏高，需要审计首屏聚合查询和重复请求形状。
3. `private_message_send` 与消息中心：需要继续收束同步写入、AI 回复任务触发和轮询形状。
4. `classroom_page`：需要继续拆解课堂首屏模板、材料/作业/聊天状态聚合。
5. `assignment_submit`：成功率稳定，但 p95 仍高，需要审计提交写事务和通知/批改触发边界。

后续任何优化都必须继续使用复制库、保留 p95 前后对比，并在部署前重新跑 `classroom-200` profile 与 P03 Playwright。
