# 修为增长模型 v2、破境试炼难度指引与 LQ 修为面板（2026-10-25）

> 状态：已实现，待线上验收。真源代码见文末索引；本文记录动机、设计、边界、验证层级与回滚。改修为计分、破境试炼出题或修为面板先读本文。

## 1. 问题

- 修为分是四项加权的线性黑盒（材料 45 / 任务 35 / 互动 15 / 稳定 5），每项内部只有一两个粗信号，学生看不出"做什么能涨"，增长也没有前期快后期慢的节奏。
- 破境试炼十境共用同一段出题提示词：题量 6–10 由模型自定，没有分档难度，也没有按课程类型给出"更难意味着什么"的指引；提示词里材料/任务满分写死 45/35，与教师自定义权重不一致。
- 修为浮窗是课堂页服务端渲染的私有弹层：自带模糊/scrim/焦点陷阱（不走 LQ.layer）、几十个 `.learning-*` 私有样式，等级卡片只能拖滚动条横向滑动；个人中心的修炼进度卡只是跳转链接。

## 2. 方案

```
四项信号 ──(share 固定)──► 四项 ratio ──(教师权重)──► 线性 raw_score（components 之和）
                                                     └──► score = 100·(1-(1-raw/100)^k)，k=1.45（成长曲线）
境界门槛（8/18/32/…/100 修为）不变 ──► 反推 raw_required 逐境递增（后期更难）；每境仍须破境试炼
```

### 2.1 修为获取方式（`cultivation_growth_service.SIGNAL_DEFINITIONS`，加载在 `learning_progress_service` 各 `_load_*`）

| 维度 | 信号（份额） | 计满条件 | 数据来源 |
|---|---|---|---|
| 学习材料 | 研读覆盖 .50 / 掌握检验 .35 / 研读深度 .15 | 读完每份必读文档；通过掌握小测（无小测读完即掌握）；有效停留 ≥3 分钟 | `learning_material_progress` |
| 作业考试 | 任务完成 .50 / 完成质量 .30 / 按时提交 .12 / 任务广度 .08 | 全部提交；批改均分；`submitted_at ≤ due_at`（无截止=按时，T/空格分隔符归一）；作业/考试/小组任务类型都参与 | `assignments.due_at`、`submissions.submitted_at`、`study_groups.assignment_id` |
| 互动求助 | AI 提问 .26（8 次）/ 研讨发言 .18（12 条）/ @助教 .12（4 次）/ 私信教师 .12（3 次）/ 同伴互助 .14（4 单位，高境界 ×2）/ 课堂活动 .18（6 次：即时互动回应 + 投票 + 小组讨论按天计） | 课堂从未开过投票/即时互动/分组时，"课堂活动"份额按比例摊到其余信号（`live_available=False`），满分仍可达 | `classroom_behavior_events`、`chat_logs`、`private_messages`、`classroom_live_responses`、`poll_ballots`+`poll_assignments`、`group_chat_messages` |
| 稳定投入 | 活跃天数 .30（5 天）/ 连续修炼 .20（最长连续 4 天）/ 专注 .22（90 分钟）/ 在线 .18（2 小时）/ 操作 .10（80 次） | 活跃天与连续天来自同一次 DISTINCT 日期扫描 | `classroom_behavior_events` 日期、`classroom_behavior_states` |

- 单份材料 `unit = reading·.50 + mastery·.35 + depth·.15`：掌握=1.0；读完未检验 0.50–0.65（原固定 0.70）；未读完上限仍 `MATERIAL_READING_CREDIT_RATIO=0.7`。
- 互动 ratio 仍乘以 `interaction_quality_factor`（0.6–1.0）。
- 可选表（投票/即时互动/小组聊天/分组）各自在 `best_effort(conn, name)` savepoint 下查询，缺表记 0，不影响其余计算；PostgreSQL 事务中单条失败不污染事务。测试夹具不必建这些表，但 `assignments.due_at`、`submissions.submitted_at` 必须存在。

### 2.2 成长曲线（`cultivation_growth_service`）

- `score = apply_growth_curve(raw)`，`k = CULTIVATION_GROWTH_EXPONENT`（默认 1.45，环境变量可调，钳在 1.0–2.5；=1.0 即线性）。单调、0→0、100→100，边际收益 `k·(1-raw/100)^(k-1)` 随 raw 递减：raw 10 → 14.2 修为，raw 50 → 63.4，raw 90 → 96.4。
- `components` 保持线性（和 = `raw_score`），修为日志（`cultivation_score_events`）口径不变；`metrics` 新增 `raw_score`、`growth_exponent`、`material.reading_ratio/mastery_ratio/depth_ratio`、`assignments.on_time_count/punctuality_ratio/breadth_ratio/kinds_*`、`interactions.live_participation_count/live_available/streak_days/...`。
- 状态新增：`breakdown`（四维度 × 信号：ratio/percent/points/max_points/value_label/hint，信号分按份额折算到组件实际得分，容忍旧 metrics）、`growth`（raw/score/bonus/marginal/phase/next_stage.raw_gap）、`stage_effort`（每境 raw_required 与较上一境增量：前几境 5.6→7.2→10.6，后几境 9.8→10.5→12.7）。
- 迁移效应：旧快照（metrics 无 `growth_exponent`）由 `get_student_learning_state` 读到即重算（`snapshot:model-upgrade`）；全班排名里尚未重算的旧线性分按曲线换算显示并标 dirty，由调度器 `cultivation_snapshot_refresh`（180 s）陆续刷新，不会出现旧线性分与新曲线分混排。信号份额变化可能让个别组件小幅下降并记 `recalibration`；境界只由证书决定，不会掉级。

### 2.3 破境试炼难度（`stage_exam_guidance_service`）

- 十境分五档：入门(1–2)/基础(3–4)/应用(5–6)/进阶(7–8)/精通(9–10)。每档给出：题量（6→10，按境界线性）、题型分布（单/多选/填空/综合，每类 ≥1）、认知层级分布（记忆理解→应用→分析→评价创造，百分比按档位单调上移）、情境与干扰项规则、回顾早期境界比例（0→30%）、主观题评分要求、难度校准口径（认真学完的学生 80–90 分，背诵猜测明显低于 80），并明令"高境界绝不能比低境界更容易"。
- 课程家族按课程名/简介/系部关键词投票（课程名命中加倍）：programming / language / math_stats / science_lab / business_management / humanities_social / arts_design / engineering_tech / general，各自 4 条"更难意味着什么"的指引（题干素材、三层级题的具体形态、干扰项来源、主观题 explanation 要求）。
- `_build_stage_exam_prompt` 硬性要求第 6 条改为按指引执行；注入 `【试炼难度指引】【课程类型指引】`；学习指标改用真实教师权重并加入按时提交、课堂活动、基础积累分与个性化提示；`expected_question_count = build_tier_profile(level).question_count`（无 tier 时 10）。
- 现有质量门禁（选项校验、历史重复率 ≥40% 拒绝、每日 3 次/每周 50 次预算）不变。

### 2.4 LQ 修为面板

- 模板 `templates/partials/cultivation_panel.html`：共享 `lq-dialog-root / lq-scrim / lq-dialog__surface lq-modal--xl lq-glass--thick`，头/体/脚槽；课堂页（学生）与个人中心 overview 各 include 一次。
- 渲染 `static/js/cultivation_panel.js`：`initCultivationPanel(defaults, scope)` 绑定 `[data-cultivation-panel-open][data-class-offering-id][data-classroom-href][data-course-name][data-panel-title]`；`openCultivationPanel({classOfferingId, progress?, trigger, classroomHref})`；同课堂首次打开用页面预载 `APP_CONFIG.learningProgress`，之后每次打开与"刷新状态"都 `GET /api/classrooms/{id}/learning/progress`（请求序号守卫，迟到响应丢弃）。弹层经 `createDomainModal` 注册到 LQ.layer（栈/焦点/滚动锁/焦点归还），纯 DOM 构建，链接只接受站内路径；继续试炼走 `/exam/take/{assignment_id}`。
- 版式：① 英雄区（境界印记 + 境界/灵气 + 进度环与修为 + 下一境动作卡：生成试炼/继续试炼/删除重生成/生成中刷新/还差 X 分，上次未过线提示）；② 修行之路：起点 + 十境的爬升轨迹（节点按序抬高、连线），scroll-snap + 左右箭头 + 方向键/Home/End + 点击节点，自动居中当前境，下方境界详情（解锁分、较上一境增量、破境日期）；③ 左列"修为构成"四张信号卡（环形百分比 + 得分/满分 + 折叠的获取方式明细条形与数值）与"成长曲线"（基础积累/曲线加成/当前增速 + 说明）；④ 右列全班山峰、成长趋势、最快路径、修为日志折叠。
- 样式 `static/css/lq/pages/cultivation.css`（已入 `lq/index.css`）：只做布局与数据表现，填充走 `--lq-page-*`，色彩走 `--ls-*` 令牌，境界色 `[data-theme]→--lq-cultivation-tone`，无 backdrop-filter、无固定品牌色；≤960px 单列、≤640px 隐藏箭头仅滑动/手势；`prefers-reduced-motion` 关脉冲。
- 课堂页：`classroom_main_v4.html` 原 200 行服务端弹层替换为 include；顶栏"本课修为"按钮同时带 `data-cultivation-panel-open`（保留 `data-learning-modal-open` 供既有测试/教师工作台），`learning_progress.js` 的 `initLearningProgressModal` 只做委托并在刷新后回写顶栏分数；旧 `initStageExamButton/initLearningMountain` 删除（逻辑并入面板）。教师成员工作台仍复用旧 `#learning-progress-modal` 壳与 `.learning-*` 样式，故旧 CSS 暂未删。
- 个人中心：`partials/profile/overview.html` 每张修炼卡保留 `href=/classroom/{id}`（无 JS 可跳转），JS 拦截后打开同一面板，页脚"前往课堂"。

## 3. 验证层级

- 单测：`tests/test_cultivation_growth_model.py`（13：曲线单调/两端固定/早期收益更高/反函数、指数钳制、后期境界 raw 增量递增、连续天数；信号份额和为 1、明细与组件分一致且容忍旧 metrics；分档题量/创造层级/回顾比例单调、题型和为题量、课程家族识别、指引文本、提示词含指引与真实权重）；更新 `test_cultivation_weights`（raw 37.5 → 曲线）、`test_interaction_quality_scoring`（新份额 + 无课堂活动时重分配）、`test_material_mastery_checks`（读完未检验 29.25 raw）、`test_learning_progress_snapshots`（夹具带模型标记）；回归 `test_learning_progress_stage_exam/alert/student_insight/card_partial/peer_help/route_snapshot/api_contract/frontend_authenticated_vite/home_classroom_backend_closure/dashboard_workspace/teacher_classroom_ai/chat_platform_query` 全绿。`test_profile_template_contract::test_signature_placeholder_styles…` 与 `test_classroom_members_workspace::test_workspace_switch…` 两项在改动前即失败（与本次无关）。
- 代码审查（code-reviewer）：无 CRITICAL；已处理 HIGH（继续试炼链接 `/exam/take/`、无课堂活动的班级第十境不可达 → 份额重分配）与 MEDIUM/LOW（旧快照混排 → 懒升级 + 曲线换算、迟到响应、日期扫描合并、小组聊天按天计、时间戳分隔符、预载数据只用一次）。
- 隔离合成运行（P03 harness，sqlite 种子库，学生真实登录课堂 972）：课堂顶栏打开面板→英雄区/轨迹/构成/曲线/山峰/日志全部渲染，箭头切换境界并更新详情，明细折叠展开，Esc 关闭且焦点回到触发按钮、`aria-expanded` 复位；个人中心点击修炼卡→同一面板经 API 拉取并显示"前往课堂"。无控制台错误。
- 建材：`npm run build:css && npm run build:assets`（新 JS/CSS 必须重建哈希图）；`npm run inventory:lq-modules` 记录本已更新并在 notes 的 dialogs 条目登记面板样板，`check:lq-modules` 通过。
- 未做：真实模型出题端到端（需厂商 key）；真 PostgreSQL 实测（SQL 只用可移植构造；可选表走 savepoint）。

## 4. 回滚

- 关曲线：`CULTIVATION_GROWTH_EXPONENT=1.0` 即退化为线性（无需改码）。
- 关指引：`_build_stage_exam_prompt` 去掉 `guidance['text']` 注入并把 `expected_question_count` 改回常量。
- 面板：课堂页 include 换回 git 历史中的服务端弹层并恢复 `learning_progress.js` 的两段 init；个人中心去掉 `data-cultivation-panel-open` 即回到纯链接。

## 5. 真源索引

`classroom_app/services/cultivation_growth_service.py`、`stage_exam_guidance_service.py`、`learning_progress_service.py`（`_material_unit_signals`、`_load_assignment_metrics`、`_class_offers_live_activities`、`_load_live_participation_count`、`_load_interaction_metrics`、`_build_learning_metrics`、`_build_learning_state_from_metrics`、`get_student_learning_state`、`_class_learning_score_rows`、`_build_stage_exam_prompt`、`generate_personal_stage_exam_from_attempt`）、`templates/partials/cultivation_panel.html`、`static/js/cultivation_panel.js`、`static/css/lq/pages/cultivation.css`、`static/js/learning_progress.js`、`templates/classroom_main_v4.html`、`templates/partials/profile/overview.html`、`templates/partials/profile/scripts.html`；规范 `docs/ai-and-agent-engineering-standard.md` §10；目标文档 `docs/cultivation-system-improvement-goals.md` v1.1。

## 6. 第二轮（2026-10-25）：境界标志、隐藏修为、修行天梯、宗门实力排行

- **十枚境界标志**：`tools/ui/build_cultivation_emblems.py` 单一真源生成 `static/img/cultivation/emblems/<level_key>.svg`（64×64，自带深色底，亮/暗页面都可读；层级越高越炫：启蒙嫩芽 → 基础太极六边形 → 筑基塔盾 → 迁移旋环灵种 → 融会八芒金丹 → 掌握烈焰剑笔 → 贯通三轨星核 → 完全莲台元婴双光环 → 自主凤翼流光 → 师者极光日冠；动效全在 SVG 内，`prefers-reduced-motion` 停止）。改图只改脚本再运行。
- **头像旁标志**（`static/js/cultivation_emblems.js`，`base.html` 对已登录页面加载）：自动识别 `img[src*="/api/profile/avatar"]`（`role=student&user_id=N` 或无参数=本人）与 `[data-cultivation-student-id]`（课堂研讨室首字头像由 `chat.js` 打标）；批量 `GET /api/cultivation/emblems?students=self,12,…`（服务端 60 秒缓存、单次≤200，前端 sessionStorage 2 分钟）；显示**所有课堂最高境界**（证书 tier），悬停/聚焦用 LQ Tooltip 显示“境界 · 第 N 境 · 修为 x · 持「证书」”。`data-cultivation-emblem="off"` 可退出；修为面板内部不装饰。共享修炼卡 `partials/cultivation_card.html` 的境界印记也换成标志。
- **隐藏修为**：个人中心 → 基础信息 → “修为展示”开关（`PUT /api/profile/cultivation-visibility`，运行时表 `cultivation_display_settings`，不进 `classroom_app/db`）。隐藏后：头像旁无标志（他人无法探查），宗门排行不列名，协作区成员卡不显示修为值；本人修为面板照常并提示“修为已隐藏 · 设置”。
- **修行天梯**：台阶式（每境台阶抬高、石阶面色随境界）、云海底、`canvas` 罡风粒子从右上吹向左下（阵风强弱变化，离屏/切后台/关闭面板即停，减少动效时静态）；按住鼠标拖动平移（拖动不触发点击），箭头/方向键/点击台阶切换境界详情；文案改为“越高越难”。
- **null 修复**：境界详情行把 `null` 当子节点 append 导致显示 “null”，改为过滤后 `replaceChildren`。
- **宗门实力排行**（原全班山峰）：`build_student_class_position` 返回 `leader{is_tianjiao, rank, level, anonymous}`、`ranking`（前 5 名公开修士，含本课境界标志）、`hidden_count`、`self_hidden`、`current_level`。真正第一名公开时称“本宗天骄”；若其隐藏修为则顺位第二名领先，但只称“宗门领先”。
- 验证：`tests/test_cultivation_display.py`（7：跨课堂最高境界与分、旧快照分走曲线、隐藏即无标志与缓存失效、天骄规则/隐藏顺位/本人隐藏仍见名次、路由 self 解析与开关、非法载荷与教师 403）；P03 隔离环境播种 5 名合成同学验证天梯拖动、罡风、排行（隐藏/公开两种）、头像标志与悬停、隐藏开关即时生效。
