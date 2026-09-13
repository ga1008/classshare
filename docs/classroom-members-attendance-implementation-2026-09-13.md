# 课堂成员工作区与签到统计归档：实施与验收记录

日期：2026-09-13（Asia/Shanghai）

分支：`codex/classroom-attendance-archive`

开发基线：`2ac3c9fba54b81cbcd5c49e6d9dd2970e730e35e`

后续状态：已于 2026-09-13 部署；生产版本、备份与有限范围验收见[发布记录](classroom-attendance-release-2026-09-13.md)。下文保留本地实施阶段的验证范围。

本次按[改进计划](classroom-members-attendance-archive-plan-2026-09-13.md)完成本地功能开发及下述验证。本记录覆盖本地开发验收，不是生产部署报告；验证未在生产业务库中创建测试档案或修改成绩。这里的“通过”限定于各项证据明确覆盖的范围，不代表全库测试、生产环境或所有学校数据格式均已通过。

## 1. 最终行为与使用入口

### 1.1 课堂成员工作区

从课堂“更多 → 成员”进入同一个浮窗，默认显示成员名册。六个页签分别为：成员、学情概览、预警与支持、签到统计、考试名单、课堂设置。

- 成员名册独立查询、搜索和分页，不依赖学情快照成功，也不把有限的预警样本当作完整名册。
- 页签首次激活时加载所需内容；打开成员不会查询智慧课堂或发起 AI。单壳管理焦点、键盘切换、横向页签滚动和移动端布局。
- 查看学生详情后可返回原页签、搜索条件和位置；切换页签不丢失考试字段和权重草稿。
- 权重修改采用版本比较更新。发生 409 时保留草稿，让教师重新载入后核对；预警动作同时具备界面防重复和数据库幂等凭据。
- 考试名单、教务签名表、重修操作沿用原业务，按页签分隔。个人试炼仍遵守原有汇总范围。

### 1.2 导出与归档

课堂“签到统计”页签和管理端“教务归档 → 签到统计表”共用来源选择及档案接口。

1. 选择完整学年、学期，主动查询该账号的授课教学班。
2. 核对课程、教学班及学期；来源选项由服务器签名，不能通过篡改浏览器字段绑定任意远端 ID。
3. 点击“导出并解析”。接口返回持久任务，关闭浮窗后仍可在任务和归档页查看进度。
4. 原生 PDF 下载并缓存后立即可预览、下载；解析失败不影响原件访问。
5. 查看逐次签到、学生汇总和核对记录；对身份、课次、状态差异补充证据并复核。
6. 完成校验后显式确认版本。重新导出或重新解析产生新版本，不覆盖原件、旧解释、审计记录或已发布成绩。

管理页面提供学年、学期、课程、班级、状态及文本筛选，URL 保留查询条件；列表与矩阵分别分页。原件预览采用鉴权的原生 PDF 查看器，并可定位到证据页。单元格保留页码、坐标和原文；当前未建设自定义 PDF 坐标高亮层或缩略图服务。

取消任务、失败重试、重新解析、删除与恢复、确认冲突及多来源计分选择均有对应界面入口。删除为软删除，历史原件仍按权限可读。

## 2. 源站规则与实施时修正

最初调查见[协议与样本摘要](attendance-export-investigation-2026-09-13.json)。实施后的真实适配器验证见[源站验证记录](attendance-live-source-validation-2026-09-13.json)。

| 操作 | 已核实的调用约束 |
| --- | --- |
| 授课教学班 | `POST /teaching/checkinCourse/teacherScheduleList`；显式传 `year` 和 `semester`，目前支持学期 1、2 |
| 点名列表 | `POST /teaching/checkinCourse/page`；传 `page,pageSize,teacherScheduleId,field=id,order=descend`，按 `pageNumber,totalPage,totalRow` 获取并校验全部页 |
| 个人状态 | `POST /teaching/checkinCourse/checkinRecord`；传点名记录 `id`，获取完整 `stuList` 进行逐格对照 |
| 原生原件 | `POST /teaching/checkinCourse/exportPdf`；请求字段仅为所选教学班的 `teacherScheduleId`，导出该教学班全部点名 |

上述接口使用学校配置的固定 API 地址及表单编码；账号通过现有加密凭据服务取得。日期、账号和教学班身份不由课程名模糊匹配替代，也不猜测远端 ID。

**关键修正：列表查询的代表 ID 与单场点名的排课 ID 可以不同。** 实际动态 Web 教学班的 15 场点名包含 5 个不同的事件 `teacherScheduleId`。选项 `id` 用于查询和导出；事件范围通过学年、学期、课程 ID、班级 ID 与所选教学班一致来验证。实现不再错误地要求每场 `teacherScheduleId` 与查询参数相等，也不通过字符串前缀推导身份。相应回归已加入适配器测试。

适配器还校验分页完整性、重复记录、明细身份、导出前后点名清单变化及账号变化。429 遵守有界等待，鉴权失效显示重新验证账号；不会把错误 HTML/JSON 当成 PDF。

旧智慧课堂同步同时修正严格学期、完整分页、同名不同教学班歧义和课次映射。候选无法唯一匹配时保留待核对状态，不选择最大的 ID 或以相同周次覆盖已冲突的日期。

## 3. 数据与服务落点

| 部件 | 主要文件及职责 |
| --- | --- |
| 源站适配器 | `classroom_app/services/smart_classroom_attendance_adapter.py`：来源身份、分页、个人状态、原件下载与格式边界 |
| 档案服务 | `classroom_app/services/attendance_report_service.py`：来源绑定、关联、档案和版本、查询、复核、确认、权限及任务发布 |
| 解析服务 | `classroom_app/services/attendance_report_parser_service.py`：完整页表格、视觉兜底、AI 核验、覆盖和证据校验 |
| 任务桥接 | `classroom_app/services/attendance_report_jobs.py`：租约检查、缓存发布、解析发布、AI 批次检查点 |
| 统一事实 | `classroom_app/services/attendance_fact_service.py`：已确认事实、适用分母、未知状态及课次聚合 |
| Web 接口 | `classroom_app/routers/attendance_reports.py`：管理页面、教师档案 API、鉴权原件 GET/HEAD/Range |
| 成员工作区 | `classroom_member_service.py`、`static/js/classroom_members.js`、`static/css/classroom_members.css` 及 `templates/partials/classroom_members/` |
| 归档前端 | `templates/manage/attendance_reports.html`、`static/js/attendance_reports.js`、`static/css/attendance_reports.css` |

计划中的来源服务与验证服务职责归入上述档案、适配器和解析模块，未为同一逻辑再建立重复封装。

新增九张表由 `db/schema_attendance_reports.py` 集中定义，注册到 SQLite 测试初始化、原生 PostgreSQL schema 和 required-columns 校验：

- `smart_attendance_source_bindings`、`smart_attendance_source_offerings`：账号/学期/教学班来源与本地课堂关联。
- `attendance_reports`、`attendance_report_versions`：稳定档案、不可变原件版本及确认指针。
- `attendance_parse_runs`：同一原件的多次解释及模型、提示版本、覆盖和验证元数据。
- `attendance_report_students`、`attendance_report_sessions`、`attendance_report_cells`：完整行列格与证据。
- `attendance_report_reviews`：人工复核、原因及前后内容。

原件 hash 独立存于 `source_file_hash`，纳入现有全局文件引用计数。来源版本、解析运行、报告之间均进行归属验证。一个来源一期最多关联一个活动课堂；课堂可拥有多个来源，计分必须明确选择其中一个。另增加课堂权重 revision 与预警动作凭据，解决浮窗原有并发覆盖及重复动作。

## 4. 事实、AI 与业务边界

### 4.1 原件和解释独立

下载采用受限临时文件、格式和尺寸校验、SHA-256、原子文件发布及短事务引用绑定。拒绝加密、损坏或需要自动修复的 PDF。缓存成功与解析成功是不同状态；缓存解析不依赖远端账号仍然登录。

正常文本表格使用矢量边界和水平字符几何提取，排除旋转水印干扰。所有状态格仍经过 AI 结构核验；不能把本地提取冒充 AI 已完成。缺失页进入视觉兜底，遗漏行列、同分钟歧义、越界坐标和 AI 差异均阻止静默确认。

解析内容被视作数据，提示中明确禁止执行 PDF 内的指令。文本状态核验只发送行列标识和必要状态原文；视觉兜底可能需要原页图像，因此需遵循现有 AI 配置与相应数据处理范围。本轮真实模型测试未发送学生姓名、学号或图片。

原文、归一状态、AI 解释、API 对照和人工修订分别保留。`UNKNOWN` 表示未知，不能算作缺课；请假也不等于出勤。校验不依赖模型自报置信度。

### 4.2 下游计分

统一事实服务对完整与未知分母作出明确区分。原始归档显示全部点名；课堂成绩消费按显式本地课次映射选择每课次最新点名，出现同时间歧义即停止计分。

已有归档却未确认、来源多选未确定、映射已变化、缺少学生或状态未知时，消费者给出明确不可用原因，不静默退回旧接口或补零。没有新归档的课堂仍可使用标明来源的旧业务路径。普通成绩与旧签到导出已接入统一事实，原重修政策保留。

新确认版本不会自动改写已发布成绩、历史归档、课堂成员或学生名册。教务归档流水线中的签到统计为可选项，不增加旧必需材料的完成率分母。

## 5. 持久任务、权限和并发

- 复用 `ai_jobs` 持久任务，注册 `attendance_export` 与 `attendance_parse` 及后台台账；导出、解析、既有文档任务分通道公平领取。
- 当前跨 worker 保守上限为导出 1、解析 1，通过 PostgreSQL advisory lock 和持久容量预留协调。网络、PDF 与 AI 操作不放在长数据库事务中。
- 心跳、续租、结果发布均校验租约。过期 worker 无法写入新结果；续租失败终止其子任务。无法证明上游已结束时保留容量到期保护，不因本地取消立即释放名额。
- 每个 AI 批次在调用前持久登记。已完成结果可恢复复用；未确定结果的旧批次不会由签到任务自动再次收费发送。批次、响应体与总调用数量均有上限。此约束针对签到任务恢复，不替代 AI 网关自身的供应商调用策略。
- 报告、版本、运行、矩阵、筛选项、任务、复核及原件均独立校验教师所有权。已登录的其他教师不能凭 ID 或全局文件 hash 访问；学生不能访问整班档案。
- 报告确认、来源变更、复核、删除恢复及权重修改采用 revision 比较；409 保留前端编辑内容，不用旧响应覆盖新状态。
- 日志、验收文档不保存密码、JWT、请求授权头或整班原文。签到 AI 网关日志对模型正文做专项脱敏。

## 6. 已执行验证及证据

### 6.1 自动化、构建与交互

| 验证 | 结果 | 证明范围 |
| --- | --- | --- |
| 本轮最终后端回归 | **213/213 通过**，11.298 秒 | 适配器、解析边界、任务恢复、档案服务/路由、统一计分、成员、重修、权重、文件引用、后台权限与台账、归档菜单等 |
| 原生 PostgreSQL | **34/34 通过** | 隔离 schema、多连接竞争、任务去重/容量、过期租约、发布恢复、并发确认及真实 ASGI 路由 |
| 原生 PostgreSQL 大表探针 | **10/10 通过** | 300 人 × 100 次点名，矩阵窗口、分页、分母与清理校验 |
| 成员浏览器 | **5/5 通过** | 单壳、懒加载、详情返回、草稿、409、学情失败、预警重复操作、移动端及键盘可达 |
| 归档浏览器 | **10/10 通过** | 筛选分页、详情矩阵、原件、核对确认、恢复、任务与来源入口、开关、移动端 |
| 学情动效测试 | **5/5 通过** | 已有前端相关测试 |
| 前端类型检查 | 通过 | `npm run typecheck` |
| 生产前端构建 | 通过 | `npm run build`；CSS 和 Vite 构建完成 |
| 补丁格式 | 通过 | `git diff --check`；仅工作区换行转换提示 |

浏览器测试运行生产模板/前端模块并模拟 API，未启动完整应用或其后台任务；不能替代生产浏览器验收。PostgreSQL 路由测试使用真实每请求连接和合成 PDF/学生，不宣称证明 PDF 内容识别。

原生 PostgreSQL 明细见[34 项验证报告](attendance-postgres-validation-2026-09-13.json)，执行工具为 `tools/validate_attendance_postgres.py`。它仅创建自有隔离 schema，结束已验证 schema 清除及测试文件清理，未修改 public 业务表。

[大表性能报告](attendance-postgres-performance-2026-09-13.json)使用同一工具的 `--performance-only` 模式独立执行一次，未重复运行前述 34 项。合成数据为 300 名学生、100 场点名、30,000 格：报告列表 11.712 ms，25 人分页及全 100 场汇总 5.894 ms，20 场列分页 1.365 ms，25×20 矩阵 4.866 ms 且仅返回 500 格。以上是单次本地测量，不是分位延迟或吞吐基准。学生分页与汇总使用索引；矩阵在该规模由优化器选择顺序扫描 30,000 格、过滤 29,500 格，EXPLAIN 执行 2.043 ms，未误报为所有查询均走索引。

最终后端结果保存在本地 `.codex-temp/attendance-plan/final-targeted-tests.log`。主要复现命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\venv\Scripts\python.exe -m unittest tests.test_attendance_parser_and_source tests.test_attendance_parser_edge_cases tests.test_attendance_report_jobs tests.test_attendance_report_service tests.test_attendance_reports_router tests.test_smart_classroom_attendance_freshness tests.test_ordinary_grade_record_service tests.test_offering_membership_service tests.test_classroom_retake_service tests.test_global_file_reference_protocol tests.test_background_task_permissions tests.test_background_task_ledger tests.test_ai_durable_job_service tests.test_manage_nav_service tests.test_attendance_material_hub tests.test_material_hub_service tests.test_classroom_members_workspace tests.test_cultivation_weights
npx playwright test --config tests/e2e/classroom-members.config.ts
node --test tests/frontend/attendance_reports_browser.test.cjs
```

### 6.2 真实源站

[真实源站报告](attendance-live-source-validation-2026-09-13.json)使用现有已验证账号查询 2025-2026 第二学期“动态 Web 程序设计-0001”，获取 15 次完整个人签到明细并下载源站原生 PDF。

| 项目 | 实测 |
| --- | --- |
| 原件 | 454,359 字节、2 页 |
| 内容 | 37 名学生 × 15 次点名 = 555 格 |
| 出勤 / 缺课 / 事假 / 病假 | 485 / 43 / 18 / 9 |
| API 完整对照 | 555/555 格，未知 0，差异 0 |
| 本探针模型调用 | 0 |
| 本探针耗时 | 8.61 秒 |

该探针不调用 AI、不发布业务档案，因此 `can_confirm=false` 符合设计，并非内容验证失败。临时 PDF 已清除。重新导出的二进制 hash 与用户原样本不同，不能仅凭 hash 推断考勤变化；结构化状态实际一致。

### 6.3 真实 AI

[真实 AI 报告](attendance-real-ai-validation-2026-09-13.json)对用户提供的两页 PDF 调用现有 AI 网关：实际模型 `deepseek-v4-pro`、提示版本 `attendance-evidence-v1`，555 格全部核验完成，无未知或差异。

- 3 次逻辑调用、3 次供应商请求，单次最多一次 HTTP 尝试，关闭 fallback；无付费重试。
- 耗时 149.63 秒；输入 13,086 token，输出 22,762 token，合计 35,848 token。
- 按项目当时计费配置估算 **¥0.3650604，约 ¥0.37**；不是供应商账单核销金额。
- 不发送学生身份或图像、不再访问源站、不在真实业务库发布档案。

真实源站与真实 AI 是两项独立探针，结合自动化及 PostgreSQL 发布测试构成分层证据；没有把它们写成一次经生产 UI、真实队列、模型到业务归档的端到端上线验证。

## 7. 开关、容量与回退

以下四个环境开关默认为 `true`，修改后需按项目运行方式重启对应进程：

| 开关 | 关闭效果 |
| --- | --- |
| `CLASSROOM_MEMBERS_WORKSPACE_ENABLED` | 暂停新的教师成员入口和工作区片段，片段 API 返回受控 503；学生个人学情保留。不会自动恢复旧的整页长浮窗 |
| `ATTENDANCE_ARCHIVE_ENABLED` | 暂停新来源查询、绑定及导入；历史档案、原件与已有结果仍按权限可读 |
| `ATTENDANCE_PARSE_ENABLED` | 暂停新解析及自动排队，允许仅缓存原件；已有结果保留 |
| `ATTENDANCE_CONFIRMED_FACTS_ENABLED` | 暂停新确认事实消费者，沿用既有业务读取路径；不改写历史成绩 |

容量参数：`ATTENDANCE_MAX_PDF_BYTES` 默认 50 MiB、`ATTENDANCE_MAX_PDF_PAGES` 默认 100、`ATTENDANCE_MAX_CHECKINS` 默认 500、`ATTENDANCE_MAX_CELLS` 默认 500,000、`ATTENDANCE_REQUEST_INTERVAL` 默认 0.35 秒。

`ATTENDANCE_MAX_AI_CALLS` 默认 24、硬上限 64，每个文本批次最多 240 格，视觉页还会占用调用名额。因此文件/单元格格式上限不等于可付费完整解析的容量；超过本次调用预算时明确停止并保留原件，不能截断后标成功。提高预算应同时评估调用成本，不自动扩增预算。

回退保持新表、原件和解释历史，不删除数据或回写旧值。未来发布仍需使用匹配源码的原生 PostgreSQL 预演报告和备份，执行项目现有 DryRun 与部署门禁。四个开关支持分阶段开放，但不替代发布前验证。

## 8. 仍需在发布阶段完成的验证

1. **完整应用及生产链路**：尚未运行具有真实 fixture 的 `HOME_CLASSROOM_BUSINESS_ACCEPTANCE` 门控全流程、生产浏览器、生产容器替换后的文件持久性或线上 worker 重启。门控 skip 不算通过。
2. **更广的真实资料**：超过 8 页、横向续页、扫描 PDF、未知新状态等已用合成/模拟 AI 用例检查覆盖与失败边界；未付费验证这些格式的真实视觉识别准确率。真实样本仅覆盖当前两页文本表。
3. **生产负载**：本地并发与分页验证不等于生产聊天、作业提交和批改混合负载测试；不承诺未经测量的吞吐量。
4. **既有数据库测试问题**：`tests.test_db_postgres_schema` 27 项中 25 项通过、1 failure、1 error；在上述原始基线的独立源码副本中复现相同两项，未归因于本次改动。具体为 Agent 的 11 张已有表未列入 required-table registry，以及 FakePostgresConnection 不支持已有 Agent identity 回填 SQL。当前签到新增 schema 已通过单独原生 PostgreSQL 测试，但全库门禁不能据此称为全绿。

这两项既有问题的当前/基线日志分别为本地 `postgres-schema-tests.log` 与 `baseline-postgres-schema-tests.log`，均位于 `.codex-temp/attendance-plan/`。本次未扩大范围修改 Agent 子系统；实际发布前应在独立修复或基线问题处置后重新执行全库门禁。

发布前更新：两项失败已在 `a98914c3` 中通过精确修复测试注册和替身 SQL 支持解决，相关 67 项通过；生产 Agent schema 未改动。发布还通过了真实数据库恢复及两次启动预演，见上述发布记录。

## 9. 交付状态

P0–P5 的功能已落地，本地核心验收及真实源站/AI 样本验证已完成；P6 的生产部署、全应用门控和生产负载部分保留为明确的发布检查。按后续 Git 收尾指令，将代码、测试、计划和脱敏验收证据纳入上述开发分支提交；具体提交及远端状态以 Git 为准。Git 提交不代表生产发布通过。
