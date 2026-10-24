# 材料知识检索与页面文字注入（2026-10-24）

> 状态：已实现，待线上验收。真源代码见文末索引；本文记录动机、设计、边界、验证层级与回滚方式（规范 `docs/ai-and-agent-engineering-standard.md` §9）。

## 1. 问题

- 课堂/全局 AI 助手此前**没有任何跨材料的文本索引**：学生问“某知识点在哪个文档的哪一页”查不到。材料正文只在材料预览页按 `materialId` 注入 Markdown/HTML，PDF/Word/PPT 一律为空；研讨室 `@助教` 只对本班绑定的 md/txt 做请求时关键词片段检索，没有页码。
- 用户在非材料页提问时，AI 只拿到页面标题、标题列表、ID 等元数据，拿不到页面正文。

## 2. 方案总览

```
course_materials ──(调度器 material_search_index，每 120s 增量)──► material_search_documents / material_search_chunks
                                                                     │  每块：locator_kind/label/url、page_number、anchor、text_lower
AI 聊天（workspace-chat / 课堂 chat / 研讨室 @助教） ──► material_knowledge_search_service（按角色圈定 + ensure_user_material_access 复核）
                                                      └──► system_prompt += 「学习材料检索结果」块；tool_status(material_search) → 前端“材料定位”卡
浏览器 collectPageText() ──► page_text 表单字段 ──► clean_page_text（去控制符/去重/脱敏/限长）──► 「当前页面可见文字」块
```

### 2.1 抽取（`material_text_extraction_service.py`）

| 类型 | 定位粒度 | 深链 |
|---|---|---|
| PDF（PyMuPDF） | 页 | `/materials/document-preview/{id}?page=N`（预览页脚本新增 `?page=` 直达） |
| Word .docx（python-docx） | 页（按 Word `lastRenderedPageBreak`/分页符）或标题节 | 同上，有页码时带 `?page=` |
| PowerPoint .pptx（zip+XML，按 `presentation.xml` 顺序） | 幻灯片 | `/materials/view/{id}`（标签含页号） |
| Excel .xlsx（openpyxl，≤400 行/表） | 工作表 | `/materials/document-preview/{id}` |
| Markdown | 标题章节 | `/materials/view/{id}#slug`（slug 与 `material_viewer.js` 的 `slugify` 完全一致：中文标题 → `section`, `section-2`…；查看器渲染后新增按 hash 滚动） |
| HTML / HTML 课件包 | 课次（`第 N 次课 · 「标题」`） | `/materials/render-view/{包根}?path=lesson_N/lesson_N.html` |
| text / code | 段 | `/materials/view/{id}` |

限制：单文件 ≤60MB、PDF ≤600 页、全文 ≤60 万字、块 700–1200 字；旧版 doc/ppt/xls、图片、音视频、压缩包记为 `unsupported`，文件不变不再重试。

### 2.2 索引维护（`material_search_index_service.py` + `material_search_schema.py`）

- 表为**运行时建表**（services 层 `CREATE TABLE IF NOT EXISTS`，派生数据可随时重建，不进 `classroom_app/db` 迁移门禁）。启动期 `app.py` 建表并 `ensure_material_search_index_task` 布置 120s 周期任务；handler 在 `scheduler` 容器运行，每轮最多 24 个文件 / 96MB，先清理已删材料的孤儿行。
- 过期判定：无索引行、`file_hash`/`updated_at`/`extractor_version` 任一不同。每个材料单独事务提交。
- 超管接口：`GET /api/manage/system/material-search/status`、`POST /api/manage/system/material-search/reindex`（可指定 `material_ids`，否则全量重建；立即布置一次扫描）。
- 环境变量：`LANSHARE_MATERIAL_INDEX_SWEEP_SECONDS` / `_BATCH_SIZE` / `_BATCH_MB`（`docker.env.example`，代码默认即可用）。

### 2.3 权限边界（`material_knowledge_search_service.py`）

- 学生：只在本人**活跃**课堂的 `course_material_assignments` 锚点子树内检索（与 `ensure_user_material_access` 同一条祖先-或-自身规则）；`class_offering_id` 只做加权与链接携带，**不扩大范围**。
- 教师：本人材料 + `_material_visibility_condition`（public/school/department/college 开放范围；超管全量）。
- 其他角色：不检索。
- 命中材料逐个再过 `ensure_user_material_access`，任何异常即丢弃；`.git` 内部路径剔除。
- 检索为 LIKE（预先小写的 `text_lower`）+ Python 打分（整句 > 标题 > 词命中 > 当前课堂加权），按材料分组、每材料最多 3 个位置。后续可换 tsvector/trigram 而不改调用方。

### 2.4 AI 接入（`routers/ai.py`）

- `_material_knowledge_retrieval_events` 作为两条聊天路由（`/api/ai/workspace-chat`、`/api/ai/chat`）的前置步骤，学生与教师都跑；命中则在 system prompt 追加“学习材料检索结果”块并下发 `tool_status(tool=material_search, documents=[…])`；用户明确问“哪个文档/哪一页”而无命中时注入“未找到，不要编造页码”的说明。失败静默降级。
- `_build_material_knowledge_block`：材料预览页的 PDF/Word/PPT/Excel 现在从索引取带位置标签的正文注入（此前为空）。
- 研讨室 `@助教`：`student_ai_tutor_context_service.build_tutor_context_block` 优先用索引（带位置），无索引时回落旧的整文件扫描。
- 全局搜索框：`search_everything` 新增“知识点定位”分组（材料名 · 位置 → 深链）。
- `PLATFORM_FEATURES_TEXT` 已告知模型这项能力。

### 2.5 任意窗口携带本页文字

- 前端 `ai_workspace_context.collectPageText()`：主内容根（`main`/`.manage-main`/`#viewer-content`…）或 body 的可见文字 + 同源 iframe（HTML 课件壳页）文字；剔除 AI 浮窗、导航、脚本、`[hidden]`/`aria-hidden`；按行去重、限 16000 字（头 75% + 尾 25%）。随每条消息以 `page_text` 字段上送。
- 服务端 `page_context_text_service.clean_page_text`：去控制符/零宽字符、去重、噪声行、**脱敏**（bearer/api key/token/长哈希）、默认 12000 字限长（`LANSHARE_AI_PAGE_TEXT_MAX_CHARS`），以带注入守卫的块注入。学生在作业/考试页仍被 `ensure_ai_workspace_access` 整体拒绝，与页面文字无关。

### 2.6 前端

- 聊天气泡下新增“材料定位”卡（`ai_chat_component.renderMaterialSources`，复用 `data-lq-component="choice" data-lq-shape="surface"` 选择卡与玻璃材质令牌；样式在 `static/css/ai_workspace.css`），每条命中直接打开深链。
- 文档预览页（`document_render_service.render_preview_html` 内联脚本）支持 `?page=N`/`#page=N` 初始定位；Markdown 查看器渲染后按 `#slug` 滚动并高亮目录。

## 3. 验证层级

- 单测（内存 SQLite，不调模型）`tests/test_material_knowledge_search.py`：Markdown slug 与查看器一致、PDF 页码/深链、docx/pptx/HTML 课件包课次、过期检测/孤儿清理/重建、学生只见绑定子树、退学学生无结果、教师本人 vs 他人私有 vs public、提示词块、定位意图识别、研讨室/全局搜索接入、页面文字清洗/脱敏/限长、隔离 FastAPI 路由（学生检索、超管 403/200、重建）。
- 回归：`test_student_ai_tutor_context_service`、`test_global_search_service`、`test_assignment_close_sweep`、`test_chat_platform_query_service`、`test_ai_workspace*`（3 个模板用例在 HEAD 上同样报错，与本次无关）、`test_background_task_*`、`test_db_postgres_schema`、`test_architecture_route_snapshot`（快照已重生成，同时补上 4 条此前遗漏的材料路由）、`test_api_contract_schemas`。
- 隔离合成运行（P03 harness，`DB_ENGINE=sqlite` + mock AI，学生账号真实登录）：播种 Markdown + 3 页 PDF 并索引 → `collectAIWorkspacePageText()` 在课堂页采集 1139 字且不含 AI 浮窗文字 → `/api/material-knowledge/search?q=ACID 特性在哪个文档` 返回《数据库.pdf》第 2 页深链 → AI 浮窗提问后回答下方出现“材料定位 · 1 份”卡并链接 `/materials/document-preview/97002?page=2&class_offering_id=972` → 打开该链接预览甲板停在 2/3 → `/materials/view/97001#section-2` 滚动到“三次握手”；控制台无错误。
- 代码审查（code-reviewer）：无 CRITICAL；已处理 HIGH「每条消息 LIKE 全表扫描」（跳过短消息/无索引时不查、PG 上尽力建 pg_trgm GIN 索引、候选按整句命中优先截断）与 MEDIUM（页面文字块加结束标记并剔除伪造的 `--- … ---` 段头、脱敏 JWT/手机号/身份证号、Office zip 成员解压上限、研讨室路径补 .git 过滤、前端只跟随站内路径）。
- 未做：真实模型端到端对话（需 AI 服务与厂商 key）；真 PostgreSQL 上的 LIKE 性能实测（索引量级估算 ≤ 数万块，LIKE 在学生作用域过滤后扫描量很小；pg_trgm 索引在生产首次启动时尽力创建，可在日志 `[MATERIAL-INDEX]` 查看）。

## 4. 回滚

- 关掉检索注入：移除/短路 `_material_knowledge_retrieval_events` 调用即可，索引表保留无副作用。
- 停止索引：在调度任务表把 `material-search:index-sweep` 取消（或将 `LANSHARE_MATERIAL_INDEX_BATCH_SIZE=1` 降速）。
- 两张表可随时 `DROP`，下次启动自动重建。

## 5. 真源索引

`classroom_app/services/material_search_schema.py`、`material_text_extraction_service.py`、`material_search_index_service.py`、`material_knowledge_search_service.py`、`page_context_text_service.py`；`classroom_app/routers/material_knowledge_search.py`；`classroom_app/routers/ai.py`（`_material_knowledge_retrieval_events`、`_build_material_knowledge_block`、`page_text`）；`static/js/ai_workspace_context.js`（`collectPageText`）、`ai_chat_component.js`（`renderMaterialSources`）、`material_viewer.js`（`scrollToRequestedHeading`）；`classroom_app/services/document_render_service.py`（`applyRequestedPage`）。
