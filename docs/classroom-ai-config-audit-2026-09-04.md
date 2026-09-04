# 课堂 AI 配置故障检查与修复（2026-09-04）

## 结论与发布状态

本次保存 500 的根因是线上 PostgreSQL 的 `ai_class_configs.class_offering_id` 缺少唯一约束，导致 `ON CONFLICT(class_offering_id)` 无法执行。已完成本地修复、真实 PostgreSQL 验证、Chrome 表单交互验证和实际 AI 服务生成验证。**尚未部署这些改动到线上，线上原始保存错误需要发布并执行启动升级后才能解除。**

## 故障证据

- 只读检查线上应用日志：`GET /api/manage/ai/config/11`、`GET /api/manage/ai/config/13` 返回 200；随后 `POST /api/manage/ai/configure` 连续两次返回 500。
- 查询线上 `pg_indexes`：`ai_class_configs` 只有 `ai_class_configs_pkey (id)`，没有 `class_offering_id` 的唯一索引。
- 查询重复键：没有重复的 `class_offering_id`。检查过程没有改写线上业务数据。
- SQLite 源 schema 声明了该列 UNIQUE，但历史 PostgreSQL 导入库缺少它；现有 PostgreSQL 启动约束修复清单也未包含该表。
- 在本机独立 PostgreSQL schema 中移除相同约束，真实保存接口返回 500；`EXPLAIN INSERT ... ON CONFLICT ...` 复现 SQLSTATE `42P10`。教材更新随事务回滚，没有半保存。
- 应用、AI 和 PostgreSQL 容器健康检查正常，因此仅看服务健康状态无法排除这种接口级数据库故障。

## 发现的问题及处理

| 问题 | 影响 | 修复 |
| --- | --- | --- |
| 保存所需唯一约束缺失 | 读取正常，但首次保存和更新都可能 500 | 加入 `POSTGRES_RUNTIME_UNIQUE_INDEXES`，复用启动升级机制；保留重复键检查，不删除或合并已有配置 |
| AI 生成忽略请求中的教材选择 | 刚选了新教材，生成内容仍基于旧教材，未绑定时甚至缺失教材依据 | 权限检查后序列化所选教材，并使用共享教材上下文构建函数；生成草稿不修改课堂绑定 |
| 前端配置加载响应没有时序校验 | 快速切换课堂时旧请求可能覆盖当前课堂内容，随后保存到错误课堂 | 使用请求版本号丢弃旧响应；加载时清空旧内容，禁用编辑及提交；失败提供重新加载 |
| 生成和保存期间仍可切换课堂、修改表单 | 异步结果可能覆盖新内容，保存反馈可能对应错误课堂 | 明确加载、生成、保存状态；生成/保存期间锁定冲突操作；先构造 FormData 再禁用控件 |
| 教材空值回退到页面初始绑定 | 已解绑教材在切换回来后被重新选中 | 接口返回值作为唯一依据，保留显式 null；预览和默认草稿不混入旧教材摘要 |
| 新建草稿与已保存空字段未区分 | 教师有意清空的大纲或提示词在重新加载时被自动填回 | 返回 `has_config`，只有从未保存的课堂自动填入默认草稿 |
| AI 生成结果被标为可自动刷新的本地模板 | 切换教材会把 AI 草稿替换成默认模板 | AI 草稿和本地自动模板采用不同状态；换教材保留 AI/手工内容并提示检查后保存 |
| 非法 ID、JSON 非对象、AI 返回数组/空字段等缺少校验 | 意外 500，或把不完整/错误结构写入编辑区 | 校验正整数范围、请求结构、两个生成字段的类型与非空；错误返回 400/502 |
| 上游异常直接透传、保存错误缺少服务端堆栈 | 用户只见内部错误，管理员难定位；上游细节可能泄漏 | 保存/加载异常记录带课堂 ID 的日志；连接、限流、超时分别使用 503/429/504；不透传上游原文 |
| 未提交教材字段与显式解绑未区分 | 旧 API 客户端只改提示词可能意外清空教材 | 未提交该字段保留绑定，显式空字段解绑；兼容 FastAPI 将空表单值转换为默认值的行为 |
| Nginx 默认 60 秒，生成接口等待上游 180 秒 | 思考模型较慢时网关先断开，应用还在生成 | 仅为 `/api/manage/ai/ai-generate` 设置 210 秒读取超时，普通接口保持原配置 |
| 共享 API 和页面同时弹错误提示 | 一次失败可能出现两条提示 | 本页面请求使用 silent 模式，统一由页面反馈，并保留持续可见的操作状态 |

## 配置到使用的链路检查

检查了 `templates/manage/ai.html` → `static/js/manage_ai.js` → 管理 API → 教材/课堂权限服务 → `ai_class_configs` 和 `class_offerings` → 实际 AI 消费路径。

课堂聊天 `routers/ai.py`、研讨室 `discussion_ai_service.py`、私信 `message_center_service.py` 都在调用时读取该课堂的配置，并组合共享课堂/教材上下文。没有需要通过重启才能刷新的配置快照。保存仍将教材绑定、提示词和大纲放在同一事务内；不同课堂保持独立。并发保存沿用最后成功提交覆盖前一次的语义，保证三项配置整体一致，不新增跨标签页版本冲突弹窗。

## 验证结果

1. **70 项相关单元/API 回归测试通过**：新增配置 API、PostgreSQL schema 修复/导出、管理写入、私信 AI 任务、学生助教上下文、AI JSON 解析。
2. **3 项隐藏支持信息泄漏防护测试通过**，包含跨流式分块边界检查。
3. **真实 PostgreSQL 验收通过**：复现 `42P10`、保存失败回滚、重复数据保护、索引修复及幂等、首次保存/更新/重新读取、运行时配置读取、并发事务一致性、解绑和跨教师权限拒绝。使用独立 schema，结束后已删除并确认不存在。
4. **Chrome 验收通过**：真实配置模板、页面 JS、共享 API/UI/提示词池模块；使用可控接口响应测试乱序加载、加载失败重试、保存失败重试、空字段回显、生成期间状态锁定、生成内容保护。390 像素视口没有横向溢出，无未捕获浏览器异常。模板外围布局使用独立测试壳，未以这些结果替代线上完整页面验收。
5. **实际 AI 服务验证通过**：从线上应用容器调用现有 AI 服务，以虚构课程信息生成未保存草稿，耗时约 3.9 秒；两个字段均为非空字符串。没有保存配置或发送课堂聊天消息。
6. **Nginx 配置校验通过**：在现有 Nginx 容器中以临时独立配置执行 `nginx -t`，没有重载线上配置；临时文件随命令清理。

可重复执行：

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_classroom_ai_config tests.test_db_postgres_schema tests.test_db_postgres_export tests.test_manage_postgres_writes tests.test_message_center_private_ai_jobs tests.test_student_ai_tutor_context_service tests.test_ai_json_parsing
.\venv\Scripts\python.exe tools/validate_classroom_ai_postgres.py --run-local
node tools/validate_classroom_ai_browser.cjs
```

## 发布后的必要验收

按项目发布约定，在获得部署指令后使用现有部署入口发布，确认启动升级创建 `idx_ai_class_configs_unique_offering`，Nginx 加载新的接口超时配置，再以真实教师登录验证“加载 → 编辑/生成 → 保存 → 重新加载 → 课堂提问”。当前工作区中原有的截图与证书相关未跟踪文件不属于本次改动，发布包必须排除这些文件。

本次证据覆盖已定位的故障和相关边界，不承诺外部模型服务永不超时或整个 AI 系统不存在任何缺陷。

## 临时数据清理状态

独立 PostgreSQL schema 和 Nginx 校验临时文件已清理。本次没有在项目 `.codex-temp`、渲染输出目录或坚果云同步目录中创建临时数据副本。

首次测试的 SQLite 连接清理顺序已修正，后续执行均能正常删除测试目录。首次失败留下的 9 个 `C:\Users\AngelWei\AppData\Local\Temp\lanshare-ai-config-*` 目录各只有一个 72 KiB 的 `test.db`，合计 648 KiB。清理操作被自动审批拒绝；改为核实文件后逐个非递归删除仍被拒绝，返回原因仅为 `blocked by policy`，因此保留原状，没有绕过限制。
