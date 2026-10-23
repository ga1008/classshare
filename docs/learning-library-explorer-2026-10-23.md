# 学习文档库改造：A4 卡片 + 资源管理器浮窗 + 渲染/编辑器模块化 + Git 同步闭环（2026-10-23）

页面：`/manage/library/materials`（教学资源 → 学习文档）。本轮不改 `library_view=postclass` 与过程材料目录页的默认行列表（它们可手动切到卡片）。

## 1. 页面结构

| 板块 | 模板 | 说明 |
|---|---|---|
| 控制面板 | `templates/partials/materials/library_card.html` `.materials-control-panel` | 参照试卷页：搜索框 + 卡片/列表切换 + 更多筛选（开放范围/学校/系部/学院/课程/班级/排序）+ 类型 chips（服务端 facet 计数） |
| 列表板块 | 同上 `.materials-board` | 标题/摘要行 + 面包屑/仓库/AI 续写/刷新 + 选择条 + `#materials-list-body`（卡片或行） |
| 资源管理器 | `#materials-detail-modal` 内由 `static/js/materials_explorer.js` 渲染 | 见 §3 |

CSS：`static/css/materials_library.css`（页面材质别名 `--lq-page-content/--lq-page-control`，只做布局）。

## 2. 服务端契约

- `classroom_app/services/material_document_kind_service.py`：**类型真源**。`classify_material_kind(item)` → `git_repository / lessondoc_pack / html_package / html_page / markdown / pdf / office_document / spreadsheet / presentation / image / code / text / archive / media / folder / file`，`attach_material_document_kinds` 写 `document_kind / kind_label / kind_color / kind_preview_mode`。筛选 chip 组 `MATERIAL_KIND_FILTER_GROUPS`，列表接口 `kind=` 参数 + `facets.kinds` 计数。前端镜像 `static/js/materials_kinds.js`（颜色、图标）。
- `classroom_app/services/material_preview_service.py`：**预览契约**。`preview_mode`（markdown/text/image/frame/document/media/folder/download）+ `preview_url / preview_frame_url / preview_image_url / preview_media_url / preview_content_url`。列表、详情、目录树三处统一附加。
- 列表接口 `GET /api/materials/library`：
  - 学习文档视图下关键词搜索**折叠到最外层入口**（`collapse=roots` 默认；`collapse=flat` 回到扁平命中），根项带 `search_match_count / search_matches[]`；
  - 顶层文件夹补 `latest_activity_at / subtree_file_count / subtree_folder_count`（排除 `.git`），`sort_by=updated_at` 按整棵子树最近活动重排；学习文档页前端默认 `updated_at desc`；
  - `is_html_package / html_package_lesson_count / summary`（取自既有 AI 解析结果，不额外查库）。
- 新路由：`GET /materials/document-preview/{id}`（PDF/Word/Excel 页面图预览，复用 `document_render_service`）、`GET /materials/inline/{id}`（PDF/图片/音视频 inline，走下载同一大小闸门）。

## 3. 资源管理器（`materials_explorer.js`）

工具栏：后退/前进/上一级/刷新、地址栏面包屑、库内筛选、详细信息/大图标切换、详细信息窗格开关、宿主动作（分配课堂/绑定课次/移动/AI/删除…，仍走 `data-detail-action` → `materials_manage.js#dispatchDetailAction`）。左侧目录树；中间：文件夹=详细信息表格或图标网格（双击进入），文件=渲染器；右侧：属性（名称/开放范围/路径/分配…）、过程材料解析结果、Git 面板。右键菜单（打开/新标签/下载/编辑源码/重命名/新建/移动/删除）。键盘：↑↓ Home End Enter Backspace F2 Delete Alt+←/→。

宿主契约：`createMaterialsExplorer({ mount, onDetailChange, onNavigate, saveContent, renderActions, renderExtraDetails, runAction, onGitResult })`；`explorer.open/refresh/select/close`。`materials_manage.js` 的 `loadMaterialDetail / refreshActiveWorkspace / refreshRepositoryAffectedViews` 在浮窗打开时都委托给 explorer。

## 4. 渲染器与编辑器（全平台共用）

- `static/js/material_renderers.js`：`renderMaterialPreview(container, item, ctx)`；按 `preview_mode` 分发：markdown（`file_preview.js` 全功能：目录/mermaid/代码块）、text/code（行号）、image（灯箱）、frame（渲染壳 iframe：HTML 单页/课件包/LessonDoc）、document（页面图预览 iframe）、media（原生播放器）、folder（宿主可注入目录列表）、download 兜底。`registerMaterialRenderer` 可扩展。
- `static/js/material_editors.js`：`mountMaterialEditor(container, item, { content, onDirty, onSave, onCancel })`；markdown（工具栏 + 实时渲染）、html（沙箱 srcdoc 实时预览）、text/code；Ctrl+S、Tab 缩进、脏态/放弃确认。持久化仍由宿主 `PUT /api/materials/{id}/content`（revision 乐观锁）完成。
- 其他页面接入方式：拿到带 `preview_*` 字段的材料 JSON → `renderMaterialPreview`；可编辑 → `mountMaterialEditor`。LessonDoc 包仍走专用编辑器。

## 5. Git 同步闭环

- 进度：`classroom_app/services/materials_git_progress.py` 进程内阶段板（queued→export→identity→pull→prepare→publish→bindings→done），`GET /api/materials/{id}/repository/progress`；前端 `materials_sync_report.js#startRepositoryUpdate` 边请求边轮询，卡片内 `paintCardSyncProgress`，资源管理器 Git 面板同款。
- 落地状态：`course_materials.git_last_sync_at / git_last_sync_status / git_last_sync_summary(JSON) / git_head_commit`（SQLite ALTER + PostgreSQL runtime columns）。**只在成功发布时写入**，失败/未认证不碰库，保持"失败不发布"的既有保证。
- 结果：`changed_entries`、`learning_bindings.offerings[]`（课堂标签、课次数、新增数、提醒数）、每条 assignment 带 `course_name / class_name / session_date / newly_bound`；`renderSyncReport` 按课堂分组展示课次对应、保留提醒、无法判定入口。
- 课次对应预览：`GET /api/materials/{id}/repository/binding-plan`（`plan_repository_learning_bindings`，只读，复用同一入口解析与序号规则，经 `plan_offering_resequence` 预演课次编号而不落库），每课次状态 `bound / will_bind / will_move / suppressed / missing_session / will_release`。
- 安全修复：`_git_command_env` 固定 `GIT_DIR / GIT_WORK_TREE / GIT_CEILING_DIRECTORIES`。此前导出工作区的 `.git` 不完整时，git 会向上发现临时目录之外的仓库（本地验证时真的拉到了项目自身的 checkout）；现在直接以"不是 git 仓库"失败，不发布任何内容。

## 6. 验证

- 后端隔离：`tools/test_backend.py` — `test_material_library_cards.py`（类型/折叠/活动排序/预览契约/进度板/对应预览/git 环境钉死，16 项）、`test_material_*`、`test_materials_git_scope`、`test_lessondoc_*`、`test_git_learning_bindings_service`、`test_html_package_service`、`test_session_learning_materials_service`、`test_permission_materials_service`、`test_document_render_service`、`test_agent_material_*` 全部通过；`test_process_material_workflow_contract` 与 `test_manage_lq_pilot_templates` 只剩与本轮无关的 3 项既有失败；`test_materials_git_update`（真实 git clone）在本机因 Git for Windows 的 msys `NtCreateDirectoryObject` 错误无法运行，基线同样失败。
- Playwright：`tests/e2e/specs/materials.spec.ts` 3 项通过。
- 浏览器（P03 隔离 SQLite 运行时，合成材料）：卡片/类型色/类型 chips/折叠搜索（`lesson` → 1 个文档库 4 处命中）/列表切换/卡片菜单/资源管理器（目录树、文件夹列表、Markdown 渲染与编辑保存、HTML 渲染与实时预览、右键菜单、Git 面板、课次对应预览、一键更新失败路径）/PDF 页面图预览 200/inline PDF；移动端布局。
