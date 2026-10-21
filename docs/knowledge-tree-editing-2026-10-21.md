# 知识树内容更新与可撤销编辑

## 行为与边界

- 文档来源版本以实际入口文件的内容哈希集合计算。同内容重导入、重新绑定、顺序变化和重复绑定不再使知识树过期。旧指纹仅在与旧材料精确匹配时兼容；Git 发布事务在改动材料前转换仍有效的旧指纹，不会把已经过期的树误标为有效。
- Git 文件回写区分内容变化与位置/类型等元数据修复。只有内容哈希变化才清空文件的 AI 派生结果。课次绑定和既有知识树完成记录保持原业务所有权。
- 更新提醒右侧采用 LQ 图标关闭按钮。关闭状态按开课班级、课次和当前文档版本记录在本浏览器；后续内容再变会重新提醒，重新生成入口仍可用。
- 画布空白处鼠标左键拖动平移，普通滚轮以指针为中心缩放，按帧合并。Ctrl/Meta 滚轮不再触发知识树缩放；触屏继续原生滑动。保留缩放按钮和键盘 `+`、`-`、`0`。
- 新增、修改、删除、移动和比重调整进入本地草稿及最多 80 步历史。`Ctrl+Z` / `Cmd+Z` 或撤销按钮恢复上一步，删除子树时一并恢复原节点 ID 与完成记录。文本输入中的撤销交给原生输入框，IME 不触发结构快捷键。
- 顶部保存按钮及 `Ctrl+S` / `Cmd+S` 一次提交完整草稿。侧栏“应用修改”只应用到本地草稿。保存成功后仍能撤销已保存的操作，并再次保存撤销结果。
- 尚未开始编辑时，完成勾选沿用即时提交；编辑期间的进度变化一起进入草稿。未保存状态可见，课堂外层进度仅接受服务端确认值。关闭时可保存并关闭、放弃修改或继续编辑；保存中防重复与关闭，页面离开有原生草稿保护。
- `PUT /api/classrooms/{offering}/sessions/{session}/knowledge-tree/draft` 仅授课教师/既有授权教师可用。服务端验证层级、容量、ID 唯一性、文字长度、权重与末梢完成引用，沿用权限检查和版本 CAS。生成中拒绝保存，其他窗口修改结构或进度会返回 409；失败保留草稿，可明确放弃后刷新。相同完整快照的网络重试不重复写入。
- 无数据库结构变更。未连接真实业务库执行测试；部署沿用 PostgreSQL 门禁、停止写入后的备份和现有数据保护流程。

## 实现与组件

控制器为 `static/js/session_knowledge_tree.js`，历史为 `knowledge_tree/history.js`，结构规则、命令、画布输入与外壳分别位于 `model.js`、`editing.js`、`gestures.js`、`chrome.js`。工具栏继续复用 LQ Button、Status、Dialog/Choose 和表单；领域 CSS 仅补布局和画布光标。

后端真源为 `session_knowledge_tree_edit_service.py`、`session_knowledge_tree_service.py`、`materials_git_service.py` 与既有 Git 课次绑定服务。原节点操作接口继续兼容旧页面，新编辑器统一使用草稿接口。

## 验收

后端测试：`test_knowledge_tree_draft.py`、`test_knowledge_tree_source_hash.py`、`test_session_knowledge_tree.py`、`test_materials_git_scope.py`、`test_git_learning_bindings_service.py`、`test_materials_git_update.py`。SQLite 内存合成行和临时 Git 工作区覆盖内容哈希不变/变化、旧指纹兼容、事务回滚、权限、保存重试、并发冲突及生成锁。

正式资源浏览器入口：`tests/e2e/components/knowledge-tree-editing.spec.ts`。使用不可变正式资源图及内存 API fixture，阻断外部网络；不启动真实业务库或 AI 生成任务。覆盖桌面编辑全流程、提醒版本、缩放锚点、平移、500/409、原生文本撤销/IME、关闭选择、重复提交、移动端教师与学生只读。

隔离后端 85 个不同用例通过；另核对并补齐先前已提交的教师课表、教师身份与账号管理 11 个路由基线，加上本轮草稿接口，隔离路由快照检查通过。前端模型/历史 5 项通过，类型检查、完整生产构建、Vite manifest 与正式图的 495 个源文件 SHA 检查通过。入口 gzip 17,839 / 18,432 字节，未增加全站首屏入口依赖。

正式资源图：`09956bafa277114bcb367c3ade03bb9917cd452b97df8554026ce3165ab3b6b2`。全平台源码审计更新为 546 个作者文件、203 个模板、5,454 个候选入口；注册表 225 条，无模板/入口数量漂移，pending/unknown 原样保留。完整审计与摘要分别为 `lq-platform-component-audit-2026-10-21.json`、`lq-platform-component-summary-2026-10-21.json`。

LQ lint 仍有 6 项本次未改动的历史阻塞：学生/教师登录 JS 与模板、用户管理模板的审查 SHA 过期，以及共享 search.css 的字面量 z-index。本轮没有新增此类阻塞，不把全平台声明为完全验收通过。

独立复核与浏览器验证中修正了生成请求等待时的草稿覆盖风险、进度写入响应丢失后的真值重读、嵌套关闭选择的焦点归还、隐藏空态占位、窄屏标题与动作布局、侧栏切换后的滚动位置，以及错误 toast 遮住重试按钮。错误现在在通知区内联显示。

最终正式资源浏览器 15 项全部通过。桌面教师、390px 教师深色及学生浅/深色四组截图已人工检查，保存和关闭动作均可达；运行期错误与意外请求均为零，只有一个可见模态层。全场景采样的最长任务为桌面 119ms、手机教师 98ms、学生浅色 94ms、深色 108ms；这不是帧率测量。初次打开 35 个请求（34 个资源/文档及 1 个读取），连续本地编辑不写服务器，手机保存仅增加 1 个 PUT。证据位于本地 `.codex-temp/knowledge-tree-20261021-final-browser/`。

原生 PostgreSQL 门禁采用已实际运行的 `lanshare-teacher-cards-pg-20261021/native-report.json` 与原始自定义格式备份，当前 77 个迁移源文件及备份 SHA 复核通过；没有新增 schema。

## 上线记录

- 功能提交：`49e81f311596b5584aeb41fa17ac2a85b6d4fa93`。独立受管工作区冻结 3,531 个受控文件，源码逐文件 SHA 与已验收工作区一致；未跟踪的用户资料未进入发布包。现有部署脚本为 Git 忽略文件，单独复制并核验原始字节，未改动部署逻辑。
- 发布预演通过后正式发布，发布号 `20261021-213303-51e999c63c40`，部署脚本退出 0 并输出 `DEPLOY_DONE`。服务器重建的正式资源图与上述本地验收图完全一致。
- 停止应用写入后的 PostgreSQL 备份：`/tmp/lanshare-deploy-backups/db-cutover-20261021-213319.sql.gz`。迁移初始化报告 182/182 必需表、0 失败索引、0 跳过步骤；`/lanshare/data` 保留，未创建生产测试账号或业务记录。
- 切换后 app、AI、mailer、nginx、PostgreSQL、scheduler 均 healthy；agent-worker、blog-crawler 均运行。主服务与 AI 健康状态为 ok，AI 批改队列存在且 pending=0，近期 app/AI/mailer 日志检查无错误。
- 生产容器内本轮 14 个产品源码文件 SHA、5 个公开知识树资源 SHA 与冻结版本完全一致。公网和容器发布号一致；匿名 GET 知识树与 PUT 草稿均返回 401。首次 HTML 仅清除 HTTP cache，同一浏览器下一次不再清除，未清除 storage/cookies。
- 后台任务总览仍累计 872 次历史失败而显示 `ok=false`；逐项当前 status 均为 ok、stale=0，未将累计值误报为本次部署失败。
- 本节为部署后的文档补录，产品代码仍为上述功能提交。Git 交付包含功能提交与此上线记录，最终核验以本地 HEAD 与 `origin/dev` 一致为准。

本地发布证据：`.codex-temp/knowledge-tree-frozen.json`、`knowledge-tree-deploy-dry-run.log`、`knowledge-tree-deploy.log`、`knowledge-tree-postflight.json`、`knowledge-tree-containers.log`。
