# 作业 / 考试系统全面审计（2026-10-13）

> 范围：学生端答题与提交、教师端管理/批改/试卷编辑、后端提交与成绩链路、AI 批改与出题。
> 方法：4 路只读代码审计 + 隔离 QA 运行时（p03 SQLite 合成库）实页检查（桌面 1366 / 移动 375）。
> 标 ✅ 的条目已由主审人对照源码或实页二次确认；其余为子审计结论，已给出文件行号，动工前请复核。
> 本文只记录问题与建议，**未修改任何代码**。

## 0. 结论速览

| 优先级 | 数量 | 核心主题 |
|---|---|---|
| P0 立即修 | 5 | 附件存储型 XSS、AI 提示词注入、共用电脑草稿串号、作业重复提交、AI 分未经确认即推送学生 |
| P1 近期修 | 约 20 | 事件循环阻塞/锁竞争/内存打包、成绩登记表题号错配、AI 任务取消、时间到无提示、批改效率、普通编辑丢失、题目切分 |
| P2 体验与规范 | 若干 | LQ 合同违规、可访问性、页面信息重复、内联巨型脚本 |

做得好的部分（保留）：提交写入的 advisory lock + 乐观版本、文件隔离回滚、评分 409 冲突面板、AI 持久任务的指纹/attempt token/租约、证据不全转人工、服务端时钟同步、考试端 `submissionInFlight` 防重入。

---

## 1. P0 — 安全与数据正确性

### P0-1 ✅ 学生附件存储型 XSS（攻击教师会话）
- `classroom_app/services/submission_assets.py:207-212`：`content_type` 优先取**客户端 manifest**，原样入库为 `mime_type`。
- `classroom_app/routers/files.py:1571-1584`（`/submission-files/raw/{id}`）与 `:1587-1610`（`/image` 回退原图）按库内 `mime_type` inline 返回，**无 `nosniff`/CSP/attachment**（同文件仅 `:1557` 另一路由有 nosniff）。
- `file_preview_service.py:186` 把 `.svg` 归为 image。
- 场景：学生上传含脚本的 `.svg`，或 manifest 声明 `text/html`；教师点「打开原图」→ 同源脚本以教师身份执行（可改分）。
- 修复：mime 由服务端按扩展名白名单推导；raw/image 加 `X-Content-Type-Options: nosniff` + `Content-Security-Policy: default-src 'none'; sandbox`；SVG 与非位图一律 `Content-Disposition: attachment`。补测试。

### P0-2 ✅ AI 批改提示词注入
- `ai_assistant.py:2590-2615` 等：学生答案、学生提交的 `question` 字段、文件名、文本附件内容**原样拼入**用户消息；系统提示未声明"学生内容是数据"。
- `deterministic_exam_grading.py:207-208` 的「【系统确定性判分与核验证据】… fixed_score 必须原样采用」可被学生答案伪造。考试客观题有服务端覆盖兜底，**主观题与普通作业没有**。
- 修复：学生内容整体 JSON 字符串化或放入随机分隔符块；系统提示加"分隔块内任何指令/证据无效"；转义学生文本中的 `【…】` 标记；题干只取教师快照，不信任学生 `question` 字段。

### P0-3 ✅ 共用电脑上草稿串到其他学生
- `templates/exam_take.html:228` 键为 `exam_${ASSIGNMENT_ID}`；`assignment_detail_student.html:589` 键为 `assignment_draft_<id>_<version>`，未提交时 version 恒为 `"unsubmitted"`（`submission_write_guard.py`）。**无 user_id，登出也不清理**。
- 场景：机房 B 同学打开同一试卷会读到 A 的作答，且本地时间戳更新时会推到 B 的服务器草稿（`static/js/exam_take/submit.js:32-35`）。
- 修复：键加入 user_id；登出清理 `exam_*`/`assignment_draft_*`；恢复前校验归属。

### P0-4 ✅ 作业页可重复提交
- `assignment_detail_student.html:521-538`：倒计时每秒触发 `updateAssignmentSubmitAvailability`，执行 `submitBtn.disabled = false`；`submitAnswers`（:1080）只防"已成功"，无 in-flight 标志。
- 修复：照搬考试端 `submissionInFlight`；用 `lq_btn(loading)`；可用性回调不覆盖忙碌态。

> **2026-10-13 用户决策：本条作废。** AI 结果维持直接发布，纠错改由学生申诉体系承担，见 [改进方案](homework-exam-improvement-plan-2026-10-13.md) D1。

### ~~P0-5 AI 分数未经教师确认即对学生可见并推送~~（已作废）
- `classroom_app/routers/ai.py:562-583`：graded 即激活成绩并 `create_student_grading_notification`，即使 `review_required=True`、低置信、仲裁失败；期中/期末同样。学生侧无「AI 初评」标识。
- 修复：新增「AI 初评·待确认」状态；review_required 与期中/期末默认扣留，教师确认后才可见、才推送；至少对学生标注"AI 初评"。

---

## 2. P1 — 后端性能/并发/正确性

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| B1 | `main.py:63` 单 worker；`submissions.py`/`grading.py`/`exports.py` 的 `async def` 内同步 DB | 池满时 `getconn` 在事件循环阻塞最多 10s，交卷高峰全站（含 WS）卡死 | 改 `def` 走线程池或 `asyncio.to_thread`（`drafts.py:53` 已有先例） |
| B2 | `homework_parts/exports.py:73-114` | 附件打包在 `BytesIO` 内存中 DEFLATE，60 人×10MB 峰值 600MB+，同步压缩阻塞 | 写临时文件 + `to_thread` + `FileResponse`+清理；图片 `ZIP_STORED`；或后台任务 |
| B3 | `common.py:692` advisory lock 持有至 `:1611`；`submission_image_guard_service.py:209-250` 锁内全量扫描 | 截止前带图提交全部串行且 O(N)；超 `lock_timeout` 直接 500 | `WHERE file_hash IN (...)` + 索引 `(assignment_id, file_hash)`；通知/学情刷新移到 commit 后 |
| B4 | `submission_return_service.py:107-128` 未清 `is_absence_score`；`submissions.py:520-522` | 截止后补 0 分再「全部撤回开放重交」，缺交学生仍提示"已截止" | 撤回时清缺交标志，提交检查先看 `submission_resubmission_accepts` |
| B5 | `exports.py:209-215` | 成绩导出用作业标题拼路径：含 `/` 500、含 `../` 可越目录写；文件永久残留且并发覆盖 | `_sanitize_zip_path` + 临时文件 |
| B6 | `common.py:1585-1611`、`submission_grading_service.py:166-197` | 尽力而为钩子无 SAVEPOINT，PG 上钩子 SQL 失败把事务置 aborted → 整次提交 500 | 钩子包 SAVEPOINT 或移到 commit 后 |
| B7 | `close_overdue_assignments` 被 GET 等十余处调用 | 高频轮询反复全表 UPDATE；插班补分异常被吞 | 交给 scheduler 统一关闭；请求内只刷新单条 |
| B8 | `submissions.py:13-202` | 教师提交列表不分页、返回全部 `answers_json`，GET 带写操作 | 列表去掉 answers_json，复用 `review-submissions` 分页 |
| B9 | `assignments.py:307-332` | 删除作业硬删、级联删提交成绩并 rmtree，无"已有提交"保护、无审计 | 有提交时拒绝或二次确认；软删 + 审计 |
| B10 | `nginx.conf:45` 256m vs 提交上限 10MB | 恶意请求可先落 256MB 临时文件 | 提交/草稿 location 单独 `client_max_body_size 12m` |
| B11 | `common.py:1612/1642` | 只捕获 `sqlite3.IntegrityError`（PG 永不命中）；`f"数据库错误: {e}"` 泄露内部信息 | 同时识别 PG UniqueViolation；对外通用文案 |

低优先级：`asyncio.create_task` 未保留引用（`common.py:1100`）；`assignment_revision` 整行哈希导致自动截止后评分 409（`assignment_management_service.py:27`）；组内互评无截止检查；`.__staging__` 等残留无清理任务；时间全用本地 naive `datetime.now()`；`common.py` 的 `_save_submission_payload` ~380 行、`__all__ = globals()` 星号导出。

测试缺口：附件打包导出、`submission-files/raw|image`、batch-grade、zero-unsubmitted、线下代交无 Python 测试；B4/P0-1/B6/B3 场景无覆盖。

## 3. P1 — AI 功能

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| A1 | `exam_grade_record_service.py:917-919,1052-1075` | 成绩登记表题号别名（页内序号/全局序号/id 尾数）按 set 顺序取首个命中，跨进程不稳定；`_reconcile_section_scores` 凑平总分掩盖错配 → **正式登记表可能写错分** | 只按 question_id 精确匹配；从结构化结果读分而非正则解析 feedback_md；凑平时告警 |
| A2 | `ai_grading_service.py:656-721` | 停止批改/强制重批不取消持久任务，旧任务继续调用付费模型（最多 8 次重试），强制重批时新旧并行 | 同事务 `cancel_ai_jobs_for_source`；worker 调模型前校验提交状态与 attempt token |
| A3 | `grading_feedback_service.py:27-34,197,271` | 普通作业逐题分与总分不核对、总分静默 clamp、max_score 用 AI 自报值；`score_sum_delta` 仅试卷路径计算 | 服务端重算总分，偏差超阈值标 needs_review |
| A4 | `deterministic_exam_grading.py:62-73` | 选项文本以"大写字母+空格"开头（"C 语言""A 类地址"）被当作字母；全角"Ａ"不识别 → 正确答案被固定判 0 | 先按选项全文匹配再解析字母；NFKC 归一化 |
| A5 | `deterministic_exam_grading.py:137-139,239-240` | id 匹配失败按位置兜底取答案，可能错位固定判分 | 匹配失败不定分，交模型并标审计 |
| A6 | `exam_json_service.py:443-604` | 不校验答案∈选项、id 全局唯一；AI 生成每页都用 `q1` → radio name 冲突 + 整卷转人工 | `require_complete` 补三项校验，生成后自动重编号 |
| A7 | `grading_revision_service.py:43-47,97-106` | 同答卷重批 `revision_hash` 相同，`ON CONFLICT DO UPDATE` 覆盖历史 AI 修订 | hash 加 job_id，只追加 |
| A8 | `ai_assistant.py:2558-2576` | HEIC/损坏图片转换失败返回空串，模型只见文件名仍出分（违反规范 3.8） | 转换失败抛证据错误转人工 |
| A9 | `ai_grading_service.py:465-502` | 学生心理画像进入**评分**调用，公平性与隐私风险 | 评分调用不带画像，语气另行改写 |
| A10 | `routers/ai.py:394`、`nginx.conf:108` | `/api/internal/grading-complete` 无鉴权且公网可达，仅靠 attempt token | 内部 HMAC + nginx deny `/api/internal/*` |

低优先级：PDF 渲染页/内嵌图不计入请求体上限（可能超 DeepSeek 48MiB 反复付费重试）；`json.loads` 接受 NaN/Infinity；`answers_empty` 恒为 False；错题归集绕过 `ai_gateway_post`（无课堂归因/预算）；出题提示词含课堂 ID（违反规范 3.7.3）；批量批改默认重派 review/failed 且 50 条静默截断；`round()` 银行家舍入（72.5→72）；正则兜底抠 score。

路由矩阵核对：期中/期末豆包 pro、其余 deepseek-flash、仲裁仅期中/期末——**与规范一致**；`grading-model-routing-plan-2026-09-11.md` 中"flash+pro 仲裁全覆盖"描述已过时，需标注被规范取代。

值得新增的 AI 能力：改标准答案后一键重批受影响提交；教师侧展示 AI 置信度/风险原因（数据已在 `quality_audit`）；多选部分得分可配置规则；AI 出卷后独立复算答案自检；按评分细则逐条打分。

## 4. 前端 — 学生端

实页确认（QA 作业 9064）：
- ✅ **题目切分错误**：`assignment_detail_student.html:813-847` `extractQuestions` 在浏览器里用 Markdown 启发式切题——标题 "P03 regression task" 被当成第 1 题，编号列表出现「徽章 2 + 1. Explain…」双编号；排除词只有英文，中文「## 提交要求」「## 评分说明」都会生成答题框，进度 `0/3 题` 失真。→ 由服务端/教师给出结构化题目列表，前端不再猜。
- ✅ 附件上传面板（`.assignment-upload-panel`）比上方答题卡更宽、左缘外凸，三层玻璃嵌套；移动端正文宽度只剩约 230px。
- ✅ 移动端顶栏两行约占 150px 且吸顶；"进行中/草稿"在顶栏徽章、信息格、状态卡重复三次。
- ✅ 每次页面加载 `GET /api/message-center/summary` 请求 4 次；单页约 75 个静态请求。

代码审计：

| 级别 | 位置 | 问题 | 建议 |
|---|---|---|---|
| HIGH | `exam_take.html:462-475`、`assignment_time.js:113-116` | 考试到时无任何提示、不自动交卷、无"最后 5/1 分钟"提醒；输入仍可编辑；顶栏翻页被禁而页脚可用 | phase→closed 时 LQ Dialog；产品决定自动交卷；阈值提醒 |
| HIGH | `exam_take.html:30` | 倒计时位于 `aria-live` 容器内，读屏每秒播报 | 只在阈值跨越时用独立 live 节点播报 |
| HIGH | `assignment_detail_student.html:96,1080-1121` | 写着"提交后不可修改"却无确认、不提示未答题 | `LQ.dialogs.confirm` 列出未答题号与附件数；考试端替换原生 `confirm()` |
| MED | `exam_take.html:2429` | 未上传完的附件离开页面直接丢失，无挽留提示 | 有 syncing/failed 附件时 beforeunload 拦截 |
| MED | `submit.js:262` | 最终提交一次性上传全部附件，无进度、不可取消 | XHR 进度 + `lq_progress` |
| MED | `exam_take.html:1052,1069`；`exam-take.css:373` | 答题卡用 `section role=button` 嵌套不可聚焦 div；`:focus-visible{outline:none}` | 真 `<button>` + `lq-domain-choice` 选择卡模板 |
| MED | `exam_take.html:484,2434` | 移动端答题卡抽屉自实现，无焦点陷阱，关闭后仍在 Tab 序列 | 改 LQ Sheet（`LQ.layer` 管理） |
| MED | `assignment-student.css`/`exam-take.css` | 玻璃上文字用 `--text-*`（`--ls-ink`）21+58 处，`--lq-material-*` 0 处；私有阴影/hover 上浮/关键帧/hex | 按 scene-ink 合同改 `--lq-material-ink/muted/accent`，删私有材质与动效 |
| MED | `exam_take.html:184-2478` | ~2300 行内联模块脚本；静态导入整个教师白板（~287KB）即使无绘图题 | 抽到 `static/js/exam_take/*`，白板按需 `import()` |
| MED | `assignment_detail_student.html:163-256,296` | 状态卡/附件行用内联背景色实底块 | `lq_alert`/`lq_status`/`lq_file_chip` |
| LOW | `assignment_detail_student.html:861,868,1097…` | 学生可见英文文案（`Submit failed:`、`(No answer)`、`Question n`） | 中文化 |
| LOW | `classroom_main_v4.html:598-619` | 作业卡 `article role=link` 内嵌真 `<a>`；摘要截 `requirements_md[:90]` 露出 `#`/`**` | 单一链接；摘要先去 Markdown |
| LOW | `exam_take.html:1086,1338,1451` | 每次输入/选择整体重写侧栏 innerHTML，大卷卡顿丢焦点 | 只更新状态点 |

## 5. 前端 — 教师端

实页确认（QA 作业 9066 / 提交 6943）：
- ✅ 作业管理页顶栏 9 个控件分两行 + 面包屑折 3 行；统计信息重复三处（顶栏 1已交/1待批/0未交 → 待处理卡+提交率环 → 6 枚统计胶囊）。
- ✅ **主任务被埋到页底**：学生提交列表要滚过信息卡、作业要求、评分标准、设置后才出现；筛选行 9 个胶囊 + CSV + 2 按钮拥挤。
- ✅ 批改详情页：「添加附件」黑色实底块位于评分面板之上，把评分推到首屏之外；「批改作业」标题条为独立纯色材质。

代码审计：

| 级别 | 位置 | 问题 | 建议 |
|---|---|---|---|
| HIGH | `static/js/submission_grading.js:188`；`assignment_detail_teacher.html:1618` | 无上一份/下一份，保存后跳回列表且丢筛选——批 40 份要往返 80 次 | sessionStorage 保存筛选后 id 序列；「保存并下一份」主操作 + J/K、Ctrl+Enter |
| HIGH | `submission_grading.js:79-83,205-207` | 普通评分编辑无 dirty 拦截，返回/关页静默丢失 | dirty 比较 + beforeunload + sessionStorage 草稿 |
| HIGH | `submission_detail.html:315` | AI 建议分直接预填输入框，与教师确认分无区分 | `grading_review` 下单列「AI 建议分」，列表标注分数来源（配合 P0-5） |
| HIGH | `exam_editor.html` | 无 beforeunload、无自动保存，「取消」是普通链接 | dirty + 草稿 / 按 expected_revision 定时存草稿 |
| HIGH | `exam_editor.css:3,74-80,8-14` | 侧栏固定 280px、body overflow hidden，375px 主区仅 ~95px | <900px 侧栏改 LQ Drawer，次要操作入菜单，骨架用 EditorShell |
| HIGH | `exam_paper_preview.css`；`assignment_wrong_summary.html:40-1492` | 私有浅色材质（24 hex/大量 rgba 白）、错题页 1452 行内联样式 100 个 hex，深色/照片背景下可读性风险 | 外壳 Dialog + raised；试卷作为声明的内容槽位保留白底配对墨色；错题页拆 CSS 用 PageHead/Surface |
| MED | `assignment_detail_teacher.html:945,1088-1104,1489` | 全量渲染 + 30s 轮询整表重建，焦点被冲掉，后台标签不暂停 | 分页/按 id 增量更新，visibilitychange 暂停 |
| MED | 同上 434-448 | 缺「补交 / AI 失败 / 批改中」筛选；批量只有撤回，AI 批改只能全部 | 补筛选（`lq_filter_bar`+aria-pressed）；选择工具栏加「AI 批改选中/导出选中」 |
| MED | `submission_detail.html:314-317`；`submission_grading.js:157` | 满分写死 100 | 读作业/试卷满分（若后端已归一化百分制则在 UI 标明） |
| MED | `exam_editor.html:1244-1251,1279,1384,1449` | 移题整页重绘丢焦点、图标按钮无 aria-label；保存只校验标题、题号可重复；`confirm()` 承载"确定=去补标准/取消=存草稿"反直觉二选一 | 焦点归还；保存前汇总校验 Alert 可跳转；改两按钮 Dialog |
| MED | `partials/exams/scripts.html:1099-1124` | AI 出题进度随机涨到 90%（假进度）+ 3s 轮询叠加 | 真实阶段或不确定态 Progress |
| MED | `exam_editor.css:46-60`、`assignment_teacher.css:657-706`、`partials/exams/board.html` | 自绘按钮/菜单/筛选材质、hover 上浮、内联 hex、`opacity` 做禁用态 | 换 `lq-btn` 变体、`lq-menu`、tone 令牌 |
| MED | 顶栏菜单 40-200 | 18 个按钮与卡片工具栏重复；"自动刷新"常显但实际只在批改中轮询 | 每个操作单入口 |
| LOW | 多处 | label 未关联（exam_editor 27 个 label 仅 1 个 for）；弹窗缺 role/aria-modal，两套弹窗机制；逐题分靠正则解析 Markdown 评语 | 统一 LQ.layer；逐题分结构化输入实时求和 |

内联代码体量：`assignment_detail_teacher.html` 内联脚本 ~1916 行、`exam_editor.html` ~1796 行、`partials/exams/scripts.html` ~1433 行、`submission_detail.html` ~755 行。

---

## 6. 建议路线

1. **安全补丁（先上）**：P0-1 XSS、P0-2 注入、P0-3 草稿键、P0-4 重复提交、A10 内部回调鉴权、B5 导出路径。均为小改动 + 回归测试。
2. **AI 可信度**：P0-5「AI 初评·待确认」状态（前后端 + 通知）→ A1 登记表精确匹配 → A2 任务取消 → A3/A4/A6 校验补强。
3. **高峰稳定性**：B1 async→线程池、B3 查重锁、B2 流式打包、B7 截止关闭交 scheduler、B10 nginx 限额。
4. **批改效率模板**：上一份/下一份 + 保存并下一份 + dirty 保护 + 筛选/批量补齐；作业管理页把提交列表提为首屏主区，统计合并为一条 stat-strip。
5. **学生答题体验**：结构化题目替代 `extractQuestions`；提交确认 + 未答提示；考试到时提醒/自动交卷；附件进度；读屏修复。
6. **LQ 收敛**：exam_take / assignment-student / exam_editor / 错题页 / 试卷预览按 2026-10-13 模板（stat-strip、filter bar、choice card、Drawer、Dialog）统一，内联脚本外置。

每批按 AGENTS.md：隔离合成运行时 + 正式构建验证，更新全平台源码审计，部署需授权。
