# P21-A - API 契约类型化工作单

状态：待实施  
范围：message center、homework、materials 高频 JSON API  
目标：后端 Pydantic schema 与前端 Zod schema 对齐，错误码可枚举，页面不再靠猜字符串分支。

## 背景与现状

当前前端已经具备两个重要基础：

- `frontend/src/lib/api-client.ts` 已经通过 Zod 对响应做 runtime parse，并在失败时抛出 `ApiError`。
- typed island payload 已经开始让模板注入数据不再完全依赖裸对象。

但高频业务 JSON API 仍存在以下风险：

- 很多路由返回 `JSONResponse` + 临时 dict，没有稳定 response model。
- 前端旧脚本大量通过 `payload.status`、`payload.message`、`error.message`、中文文案或字段是否存在来推断状态。
- 后端错误语义分散，403、404、409、422、429、500 对应的业务原因没有统一可枚举 code。
- React island 迁移后，前端会越来越依赖 `api-client.ts`，如果契约不先收束，页面会把旧的猜字段逻辑搬进新的 TypeScript。

P21-A 要把这个问题先在 message center、homework、materials 三个高频域收住。

## 总体原则

- 先契约化当前业务形状，再考虑统一 envelope；不要为了“格式漂亮”破坏旧页面。
- 成功响应和失败响应都必须有 schema。
- 错误分支使用枚举 code，不使用中文字符串、英文 message substring 或字段缺失来判断。
- Pydantic schema 是后端事实来源，Zod schema 是前端 runtime 防线；两边字段名、枚举、可空性必须一致。
- 文件下载、图片流、DOCX/XLSX/ZIP 导出等非 JSON 响应不纳入第一阶段，但其错误 JSON 仍要统一。
- 后端权限仍是最终边界；前端 schema 不能被当作权限控制。
- 不改数据库结构，不写线上数据。

## 建议契约分层

### 1. 通用错误契约

建议新增或等价实现：

```text
classroom_app/schemas/api_common.py
frontend/src/contracts/api-common.ts
```

后端 Pydantic 概念模型：

```text
ApiErrorCode = Literal[
  "AUTH_REQUIRED",
  "FORBIDDEN",
  "NOT_FOUND",
  "VALIDATION_FAILED",
  "CONFLICT",
  "RATE_LIMITED",
  "UPLOAD_TOO_LARGE",
  "UNSUPPORTED_FILE",
  "QUEUE_BUSY",
  "AI_JOB_NOT_FOUND",
  "ASSIGNMENT_NOT_FOUND",
  "SUBMISSION_NOT_FOUND",
  "MATERIAL_NOT_FOUND",
  "MESSAGE_NOT_FOUND",
  "SERVER_ERROR"
]

ApiErrorPayload:
  ok: false
  error:
    code: ApiErrorCode
    message: str
    details: dict | list | null
    request_id: str | null
```

前端 Zod 必须有同名 enum，并让 `ApiError` 暴露：

- `status`
- `code`
- `message`
- `details`
- `requestId`
- `payload`

兼容期要求：

- 后端如果仍返回旧 `{detail: "..."}`，`api-client.ts` 可以兼容解析为 `UNKNOWN` 或 `SERVER_ERROR`，但新 typed API 不允许继续新增这种裸错误。
- 新增业务分支时必须依赖 `code`，不能依赖 `message`。

### 2. 成功响应契约

第一阶段可以先保留各接口现有顶层形状，但必须由 Pydantic 和 Zod 明确字段。

允许的两种策略：

1. 保守策略：为现有响应逐个建 schema，不改顶层字段。
2. 收束策略：为新迁移的 TypeScript 调用提供 `{ ok: true, data, meta }` envelope，同时给旧脚本保留兼容字段。

验收重点不是必须统一成同一个 envelope，而是：

- 字段是否必填明确。
- 可空字段是否明确。
- 枚举值是否明确。
- 列表元素 shape 是否明确。
- 错误结构是否可枚举。
- 前端是否按 schema parse 后再使用。

## message center 契约范围

涉及路径：

- `classroom_app/routers/message_center.py`
- `frontend/src/islands/message-center-page.tsx`
- `frontend/src/islands/message-center-sync.tsx`
- `frontend/src/islands/message-center-workspace-sync.tsx`
- `frontend/src/lib/message-center-workspace.ts`
- `static/js/message_center.js` 与 `static/js/classroom_private_messages.js` 兼容期调用

第一阶段 JSON API：

| API | 契约目标 |
| --- | --- |
| `GET /api/message-center/bootstrap` | 当前用户的 unread summary、分类、联系人、初始 tab、权限能力、冷却状态结构稳定 |
| `GET /api/message-center/summary` | 全局铃铛 summary 稳定，未读数、最新未读、弹出提示字段明确 |
| `GET /api/message-center/items` | 通知列表 item shape 稳定，分页/筛选 meta 明确 |
| `POST /api/message-center/read` | 已读操作返回 affected count、summary、items refresh hint |
| `GET /api/message-center/private/contacts` | 联系人列表、blocked/available/capability 字段明确 |
| `GET /api/message-center/private/conversation` | 会话消息、附件、AI job、分页 cursor、summary 明确 |
| `POST /api/message-center/private/messages` | 发送成功返回 message、conversation summary、cooldown、AI job optional |
| `GET /api/message-center/private/ai-jobs/{job_id}` | queued/running/completed/failed 状态枚举化 |
| `GET/POST/DELETE /api/message-center/private/blocks` | block item、mutation result、summary 明确 |

必须枚举的状态：

- notification scope：`all`、`unread`、`classroom`、`system` 等实际支持值。
- private message status：`sent`、`failed`、`blocked`、`rate_limited`。
- AI job status：`queued`、`running`、`completed`、`failed`、`cancelled` 或现有实际值。
- attachment kind：`image`、`file`、`unknown`。

验收条件：

- 后端 response schema 覆盖上述 JSON API。
- 前端 Zod schema 覆盖上述响应。
- message center 页面和顶部消息铃不再通过中文文案判断错误；例如拉黑、冷却、AI job 不再靠 `message.includes(...)`。
- `message-center-workspace-sync` 的 snapshot schema 与 API schema 有转换函数和测试，不直接消费未验证裸 payload。

## homework 契约范围

涉及路径：

- `classroom_app/routers/homework.py`
- `classroom_app/routers/homework_parts/assignments.py`
- `classroom_app/routers/homework_parts/submissions.py`
- `classroom_app/routers/homework_parts/grading.py`
- `classroom_app/routers/homework_parts/drafts.py`
- `classroom_app/routers/homework_parts/exam_papers.py`
- `static/js/app_exams.js`
- 作业提交、教师提交列表、批改、草稿相关 islands / legacy JS

第一阶段 JSON API：

| API | 契约目标 |
| --- | --- |
| `GET /assignments/{assignment_id}/submissions` | 提交列表、状态枚举、批改状态、统计 summary 稳定 |
| `POST /assignments/{assignment_id}/submit` | 学生提交成功/重复/过期/文件错误结构稳定 |
| `GET/POST /assignments/{assignment_id}/draft` | 草稿内容、附件、保存版本、冲突状态稳定 |
| `POST /submissions/{submission_id}/grade` | 教师批改结果、分数、反馈、状态流转稳定 |
| `POST /assignments/{assignment_id}/submissions/batch-grade` | 批量/AI 批改启动结果、queued/running/failed 枚举稳定 |
| `GET /assignments/time-state` | 作业时间状态、overdue/closing/published 枚举稳定 |
| `GET /courses/{course_id}/assignment-stats` | 课堂/课程卡片统计字段稳定 |
| `POST /assignments/{assignment_id}/submissions/withdraw` | 撤回、退回、权限失败结构稳定 |

必须枚举的状态：

- assignment status：`draft`、`published`、`closed`、`archived` 或现有实际值。
- submission status：`not_submitted`、`submitted`、`late`、`withdrawn`、`graded`、`grading`、`grading_failed`。
- grading mode/status：`manual`、`ai_queued`、`ai_running`、`ai_failed`、`ai_completed`、`stopped`。
- file policy error：`UPLOAD_TOO_LARGE`、`UNSUPPORTED_FILE`、`QUESTION_FILE_REQUIRED`、`QUESTION_FILE_FORBIDDEN`。

验收条件：

- 教师提交页不再靠 `status === "success"` 和中文 `message` 判断关键分支。
- 学生提交页对过期、重复提交、缺文件、文件类型错误有明确 code 分支。
- AI 批改状态轮询只依赖枚举状态，不依赖按钮文案或字符串猜测。
- 统计字段与提交列表字段有后端契约测试，防止卡片数量和详情数量再次漂移。

## materials 契约范围

涉及路径：

- `classroom_app/routers/materials.py`
- `classroom_app/routers/materials_parts/common.py`
- `classroom_app/routers/materials_parts/library.py`
- `classroom_app/routers/materials_parts/ai_import.py`
- `classroom_app/routers/materials_parts/learning.py`
- `classroom_app/routers/materials_parts/final_materials.py`
- `static/js/materials_manage.js`
- `static/js/classroom_materials.js`
- `frontend/src/islands/materials-manage-page.tsx`
- `frontend/src/lib/material-learning-path.ts`

第一阶段 JSON API：

| API | 契约目标 |
| --- | --- |
| `GET /api/materials/library` | 材料列表、面包屑、facets、排序、分页/父目录状态稳定 |
| `GET /api/materials/{material_id}` | 材料详情、预览能力、下载能力、AI parse 状态稳定 |
| `POST /api/materials/upload` | 上传结果、失败文件、容量/类型错误 code 稳定 |
| `POST /api/materials/{material_id}/assign` | 分配结果、课堂/课次影响范围、权限错误稳定 |
| `PATCH /api/materials/{material_id}/scope` | 可见范围、组织范围、冲突/越权错误稳定 |
| `GET /api/materials/ai-import-records/active` | active job 列表与 terminal 状态稳定 |
| `GET /api/materials/ai-import-records/{record_id}/status` | AI 导入状态、进度、错误、生成材料 ID 稳定 |
| `GET /api/materials/{material_id}/ai-import/preview` | 解析预览结构稳定 |
| `GET /api/classrooms/{class_offering_id}/materials` | 课堂材料列表、学习路径关联字段稳定 |
| `POST /api/classrooms/{class_offering_id}/final-materials/generate` | 最终材料任务创建/恢复状态稳定 |

必须枚举的状态：

- material node type：`file`、`folder`、`repository` 或现有实际值。
- material visibility/scope：`private`、`department`、`school`、`public` 或现有实际值。
- AI import status：`queued`、`running`、`completed`、`failed`、`ai_failed`、`quality_failed`、`unsupported`。
- final material status：`idle`、`queued`、`running`、`completed`、`failed`。
- upload error：`UPLOAD_TOO_LARGE`、`UNSUPPORTED_FILE`、`EMPTY_FILE`、`STORAGE_ERROR`。

验收条件：

- 材料管理页列表、上传、搜索、AI 导入状态不再靠中文提示判断成功/失败。
- AI 导入轮询只依赖 active/terminal 状态 enum，不依赖 `message` 或按钮文字。
- 课堂材料和材料管理页共享同一套 normalizer 或契约 schema，避免同一字段在两个页面取不同名字。
- 可写测试仍然只使用 `.codex-temp` 复制库与临时上传目录。

## 错误码验收表

第一阶段至少要形成以下错误码集合，后续可扩展但不得随意拼字符串：

| code | HTTP | 适用域 | 说明 |
| --- | ---: | --- | --- |
| `AUTH_REQUIRED` | 401 | 全局 | 未登录或 session 失效 |
| `FORBIDDEN` | 403 | 全局 | 登录有效但无权访问 |
| `NOT_FOUND` | 404 | 全局 | 资源不存在或不可见 |
| `VALIDATION_FAILED` | 422 | 全局 | 参数、表单、JSON 校验失败 |
| `CONFLICT` | 409 | homework/materials/message center | 状态冲突、重复提交、版本冲突 |
| `RATE_LIMITED` | 429 | message center/homework/chat | 冷却或限流 |
| `QUEUE_BUSY` | 409/429 | AI/worker | 后台任务忙或已存在 active job |
| `UPLOAD_TOO_LARGE` | 413 | homework/materials/message center | 上传超过限制 |
| `UNSUPPORTED_FILE` | 415/422 | homework/materials/message center | 文件类型不支持 |
| `ASSIGNMENT_NOT_FOUND` | 404 | homework | 作业不存在或不可见 |
| `SUBMISSION_NOT_FOUND` | 404 | homework | 提交不存在或不可见 |
| `MATERIAL_NOT_FOUND` | 404 | materials | 材料不存在或不可见 |
| `MESSAGE_NOT_FOUND` | 404 | message center | 消息不存在或不可见 |
| `AI_JOB_NOT_FOUND` | 404 | message center/materials/homework | AI job 不存在或不可见 |
| `SERVER_ERROR` | 500 | 全局 | 未分类服务端错误 |

## 测试要求

### 后端

建议新增：

```text
tests/test_api_contract_message_center.py
tests/test_api_contract_homework.py
tests/test_api_contract_materials.py
tests/test_api_error_contract.py
```

预期结果：

- TestClient 调用成功路径，返回 payload 能通过对应 Pydantic model。
- TestClient 构造 401/403/404/409/422/429 场景，错误 payload code 在枚举内。
- response schema 不允许关键字段静默缺失。
- 对兼容字段的测试要标明“deprecated but still required until legacy JS retired”。

### 前端

建议新增：

```text
frontend/src/contracts/api-common.test.ts
frontend/src/contracts/message-center.test.ts
frontend/src/contracts/homework.test.ts
frontend/src/contracts/materials.test.ts
```

预期结果：

- Zod schema 能 parse 后端 fixture。
- Zod schema 对缺字段、错误 enum、错类型返回失败。
- `api-client.ts` 在错误响应中能解析 `error.code`，并保留原始 payload。
- 页面状态机测试使用 error code 分支，而不是 message 文案。

### 浏览器回归

至少保持：

```powershell
npm run test:e2e:p12
npm run test:e2e:p03
```

预期结果：

- message center 教师/学生路径通过。
- homework 学生提交、教师查看、AI 批改路径通过。
- materials 教师上传/搜索、学生拒绝访问路径通过。
- 控制台无新增 schema parse error。
- 网络无非预期 401/403/500。

## 验收清单

| 条件 | 验收方式 | 状态 |
| --- | --- | --- |
| 后端 schema 覆盖 message center 高频 JSON API | `tests/test_api_contract_message_center.py` | 待实施 |
| 后端 schema 覆盖 homework 高频 JSON API | `tests/test_api_contract_homework.py` | 待实施 |
| 后端 schema 覆盖 materials 高频 JSON API | `tests/test_api_contract_materials.py` | 待实施 |
| 前端 Zod schema 与后端 fixture 对齐 | `npm test` | 待实施 |
| `ApiError` 支持枚举 code | `frontend/src/lib/api-client.test.ts` | 待实施 |
| 页面不靠中文字符串判断核心错误 | grep + Vitest 状态机测试 | 待实施 |
| 旧脚本兼容期不破坏 | P03/P12 Playwright | 待实施 |
| 所有可写测试使用 `.codex-temp` | `zz-data-safety.spec.ts` 或等价检查 | 待实施 |

## 数据安全提醒

API 契约化本身不应需要真实生产写入。任何提交、上传、批改、消息发送、AI job 创建测试都必须使用复制数据根。生产环境只允许做只读契约 smoke，例如 health、manifest、登录页、受保护页面拒绝访问、worker 台账读取。
