# P21 - API 契约类型化与部署检查产品化目标

状态：待实施  
创建日期：2026-06-04  
优先级：P2  
目标类型：API 契约治理、前后端类型对齐、部署前后质量门、生产数据保护

## 目标定义

P21 要把 LanShare 下一阶段的“接口稳定性”和“上线确定性”设定为一个明确、可执行、可验收的 P2 目标：

1. 将高频 JSON API 从“页面按经验读取字段、按字符串猜错误”收束为后端 Pydantic schema 与前端 Zod schema 对齐的稳定契约。
2. 将部署前后检查从“人工记得跑哪些命令”产品化为固定 preflight / postflight 流程，能自动产出健康快照、manifest 结论、worker 状态和核心页面 smoke 结果。

P21 不以一次性重写所有接口为目标。第一阶段只覆盖高频、回归风险最高、正在前端 island 迁移中的三个业务域：

- message center
- homework
- materials

部署检查第一阶段只把现有能力固化为流程，不改变生产部署路径：继续以 `deployment/deploy_remote.ps1` 为 canonical deploy entrypoint，先 dry run，再真实部署，再 postflight。

## 不得破坏的边界

- 不得修改线上业务表结构来适配前端类型化；如确实需要 schema/迁移，必须另立数据库迁移目标并先对复制库 dry run。
- 不得为了统一响应格式直接破坏现有 URL、HTTP method、字段名或旧脚本依赖；需要兼容期和逐端点迁移记录。
- 不得把后端权限判断移动到前端；前端 schema 只负责解析响应，不负责替代权限边界。
- 不得让部署检查对生产数据执行写入型 Playwright、作业提交、材料上传、消息发送、AI 批改或批量修复。
- 不得把真实密码、cookie、token、API key、学生数据、教师私信内容写入测试 fixture、日志、报告、trace 或提交记录。
- 不得在生产 `/lanshare/data` 上执行迁移 dry run、数据修复 dry run、可写浏览器回归或压力测试。
- 不得跳过 `deployment/deploy_remote.ps1 -DryRun` 直接部署。

## 子目标

| 编号 | 子目标 | 交付目录 | 状态 |
| --- | --- | --- | --- |
| P21-A | API 契约类型化 | `api-contract-typing.md` | 待实施 |
| P21-B | 部署前后检查产品化 | `deploy-check-productization.md` | 待实施 |

## 第一阶段验收口径

P21 第一阶段只有在以下条件全部满足时才可标记完成：

| 序号 | 条件 | 期望结果 | 状态 |
| ---: | --- | --- | --- |
| 1 | message center JSON API 有后端 Pydantic response schema | bootstrap、summary、items、read、private conversation、private messages、AI job、blocks 的成功/失败结构可验证 | 待实施 |
| 2 | homework 高频 JSON API 有后端 Pydantic response schema | submissions、submit、draft、grade、batch-grade、time-state、assignment-stats 的响应结构稳定 | 待实施 |
| 3 | materials 高频 JSON API 有后端 Pydantic response schema | library、detail、upload、assign、scope、AI import active/status/preview、classroom materials 的响应结构稳定 | 待实施 |
| 4 | 前端有与后端对应的 Zod schema | schema 能 parse 正常 fixture，拒绝缺字段、错类型、未知错误码 | 待实施 |
| 5 | API 错误码枚举化 | 页面分支使用 error code / status，不再靠中文字符串或模糊 substring 判断 | 待实施 |
| 6 | `api-client.ts` 能携带结构化错误 | `ApiError` 至少包含 `status`、`code`、`message`、`details`、`requestId` 或兼容字段 | 待实施 |
| 7 | 契约测试覆盖三个域 | 后端 unittest/TestClient + 前端 Vitest schema 测试均通过 | 待实施 |
| 8 | 旧页面兼容未破坏 | 现有 static JS 与 React islands 在兼容期仍能读取响应 | 待实施 |
| 9 | 部署 preflight 有固定入口 | 一条命令能跑 build/typecheck/test、后端测试、manifest 检查、迁移 dry run、deploy dry run、健康快照 | 待实施 |
| 10 | 部署 postflight 有固定入口 | 一条命令能检查远程 health、docker compose 状态、manifest、核心页面、worker 台账和最近错误日志 | 待实施 |
| 11 | 检查报告可留痕 | 每次 preflight/postflight 生成 `.codex-temp/deploy-checks/<timestamp>/report.json` 或等价报告 | 待实施 |
| 12 | 数据安全边界被测试证明 | 所有可写验证只发生在 `.codex-temp` 复制数据根，生产 `/lanshare/data` 只做备份和只读健康检查 | 待实施 |

## 推荐目录结构

```text
target/target01/P21/
  README.md
  api-contract-typing.md
  deploy-check-productization.md
```

后续实施时，如果开始落代码，建议同步新增：

```text
classroom_app/schemas/
  api_common.py
  message_center_contracts.py
  homework_contracts.py
  materials_contracts.py

frontend/src/contracts/
  api-common.ts
  message-center.ts
  homework.ts
  materials.ts

tools/deploy/
  preflight.ps1
  postflight.ps1
```

目录名不是强制要求，但必须保持“后端契约、前端契约、部署检查入口、验收报告”四类产物可追踪。

## 总体验证命令

P21 完成前至少应通过：

```powershell
npm run typecheck
npm test
npm run build
npm run inventory:frontend
python -m unittest discover -s tests -p "test_*.py"
npm run test:e2e:p12
npm run test:e2e:p03
powershell -NoProfile -ExecutionPolicy Bypass -File .\deployment\deploy_remote.ps1 -DryRun
```

若后续新增产品化命令，建议最终收束为：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\deploy\preflight.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\deployment\deploy_remote.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File .\deployment\deploy_remote.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\deploy\postflight.ps1
```

预期结果：

- 所有本地静态、单元、集成、浏览器回归通过。
- Vite manifest 存在且关键 entry 对应文件存在。
- 数据库迁移/初始化 dry run 只针对复制库，`PRAGMA quick_check` 返回 `ok`。
- deploy dry run 不包含受保护路径，特别是 `data/`、`.env`、`docker.env`、`logs/`、`storage/`、`node_modules/`。
- 真实部署前有远程 DB backup 记录；真实部署后 app、ai、nginx 健康。
- worker 状态中队列深度、心跳、最近错误可读；历史 failed count 可以警告，但不得掩盖新增失败或服务不健康。

## 数据安全声明

P21 的所有测试与检查必须遵守：

- 本地可写测试使用 `.codex-temp` 下的复制数据根。
- 生产只读检查允许访问 `/api/internal/health`、公开登录页、静态 manifest、worker 台账快照等不会改变业务数据的接口。
- 生产写入类 smoke 必须默认禁止；如确需验证，必须先建立专用 staging 或专用 QA 租户，并由用户明确授权。
- 部署脚本必须继续保护 `/lanshare/data` 与其他 runtime 目录；任何远程清理只允许针对明确退休的代码文件，且必须在代码/DB 备份完成后执行。

## 跟踪记录

| 日期 | 执行人 | 变更 | 验证 | 状态 |
| --- | --- | --- | --- | --- |
| 2026-06-04 | Codex | 建立 P21 目标：API 契约类型化与部署检查产品化 | 写入 `target/target01/P21` | 待实施 |
