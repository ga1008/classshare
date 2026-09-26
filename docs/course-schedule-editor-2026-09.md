# 3D 课表 · 编辑模式与教务调课草稿（2026-09-25）

## 目标

在现有 3D 课表之上增加「编辑模式」：教师在平台上拖拽/设置课次的新时间与教室，平台把变更先记录为本地草稿，再以教师本人的教务账号把草稿**保存**进教务系统（正方）调停课申请的「待提交」列表。平台**不提交申请**：提交需要教师登录教务系统核对并点击「提交申请」（附件、原因可在教务补充）。

## 入口

- 首页 3D 课表头部与放大视图导航新增「编辑模式」链接（`createScheduleDeck` 的 `editorUrl` 选项，教师端才传）。
- 课时统计页 3D 牌组同样带该入口。
- 页面：`GET /manage/academic/course-schedule/editor?year=&term=`（`templates/manage/course_schedule_editor.html`，`static/js/course_schedule_editor.js`，样式 `course_schedule_editor_styles.js`，全部使用 `--ls-*` 玻璃令牌）。

## 页面结构与交互

- 顶栏：学年学期下拉、学期/草稿统计、「同步教务课表」、「保存到教务 (N)」。
- 左侧：整学期周列表（当前周标记、每周节数、调整数徽标）。
- 中间：选中周的完整周表格（早读/上午/下午/晚上分区，第 1 节标记为禁放）。
- 右侧：课次属性抽屉（原安排、周次/星期/起始节、教室（教务场地搜索）、调课原因、课次材料链接）。
- 底部：变更清单（状态标签、定位、撤销、从教务撤回、冲突仍保存；已保存到教务后显示下一步提示与教务入口链接）。
- 拖拽：按住课次 → 同周格子放置；拖到左侧周次悬停 320ms 该周「摊开」到中间后继续放置即跨周。放置规则：起始节 ≥ 2、不超过学期最大节次、占用节数与原课次一致、不与本人其他课程/其他草稿目标重叠；已保存到教务的草稿需先撤回才能再拖动。
- 每次放置/保存都立即写入本地草稿表；离开页面不丢。

## 后端

- 表 `teacher_schedule_edit_drafts`（`classroom_app/db/schema_schedule_editor.py`，运行时 engine-aware 建表，仿 polls 模式）：原/新安排 JSON、原因、状态 `draft|pushed|conflict|failed`、教务 `ttk_id`/`ttkxx_id`、教务返回文本、冲突 JSON。
- `services/schedule_editor_service.py`：校验（周次/节次范围、连续、早读禁放、占用节数一致、本人课表与草稿重叠）、草稿 CRUD、周表装饰（原课次 `edit_draft`、目标周 `edit_ghost` 幽灵卡）、教务场地搜索（`teacher_academic_teaching_places`，全校共享）。
- `services/academic_schedule_draft_push_service.py`：教务写入适配器，**只**调用四个端点：
  1. `POST /tkgl/ttksq_cxTtksqView.html?jxb_id&xnm&xqm` 打开申请表单——教务为该教学班分配/复用草稿 `ttk_id`，页面内联 `modelList`（正式课位：`xqj`、`zc` 周次位掩码、`jcarr`、`cd_id`、`jgh_id`）与 `tkxxList`（已保存的待提交明细）；
  2. `POST /tkgl/ttksq_cxConflictCtzt.html` 冲突检测（`conflictNum` 位：2 教师、4 场地、8 已申请、16 课表、32 学生、64 已补课、128 实践课；8/64 为硬冲突不可强制）；
  3. `POST /tkgl/ttksq_cxSaveTtksj.html` 保存草稿明细（multipart，字段与浏览器 `getDatas()` + `#ajaxForm` 完全一致：`yzcd/xzcd` 周次位掩码 `2^(周-1)`，`yjc/xjc` 节次位掩码，`yxqj/xxqj` 星期，`ycd_id/xcd_id` 场地，`tklxdm=01`、`spl_id=TKGL_TK`、`tkyy` 原因）；返回 `{bcName, ttkxx_id}`；
  4. `POST /tkgl/ttksq_scTtksqsj.html` 撤回一条明细。
  源码中不出现提交端点 `ttksq_tjTtksq`（单测钉死）。同一原课次若教务已有草稿明细则直接关联、不重复。
- API（`routers/manage_parts/schedule_editor.py`，前缀 `/api/manage/academic/course-schedule/editor`）：`GET` 编辑载荷、`POST /drafts`、`DELETE /drafts/{id}`、`POST /push`、`POST /drafts/{id}/withdraw`、`GET /rooms`。
- 周表现在透传 `teaching_class_id`/`teaching_class_name`；学术总览的重复警告文案去重（修复「申请已通过…」三连显示）。

## 可调时段（2026-09-25 第二轮，教务停机期间按正方经验实现，接口待联调）

问题：调整时间/教室可能与其他老师的课、学生的课冲突，教务保存时才报错，反复试错效率低。

- 数据层（`db/schema_schedule_availability.py`，运行时建表）：`academic_class_timetable_slots`（行政班/教学班整学期课表段）、`academic_room_timetable_slots`（教室课表段）、`academic_room_slot_checks`（按 周/星期/节次 的教室占用结论缓存）、`academic_availability_sync_state`。草稿表新增 `room_status`、`availability_json`。
- 判定（`services/schedule_availability_service.py`）：`build_lesson_availability` 把 学生课表（教学班 → 名单表 `admin_class_code` 推出行政班 → 行政班课表，自动剔除该课次本身）、本人课表（总览）、教室占用（课表或逐时段查空结论）合成紧凑忙碌表；`check_slot` 四级结论 `block`（学生/本人有课，禁止）> `room`（学生有空但教室被占，允许但需换教室）> `unknown`（教室未查）> `ok`。`save_draft` 对学生有课直接 409，教室被占则保存并标记 `room_status=busy`。
- 教务适配（`services/academic_availability_sync_service.py`）：**候选路径按序探测**、首个返回 `kbList` JSON 的生效并记入 `sources`；可用环境变量 `LANSHARE_ZF_CLASS_TIMETABLE_PATHS` / `LANSHARE_ZF_ROOM_TIMETABLE_PATHS` 覆盖。班级课表沿用教师课表的 `xszd[...]`+`kzlx=ck` 表单并附 `bj_id`；教室课表附 `cd_id`。全部候选未响应 → 状态 `endpoint_unverified`（界面提示“接口待联调”），不影响其他功能。二次搜索 `search_free_rooms` 复用现有空闲教室查询（`cdjy_cxKxcdlb`），并把原教室在该时段的结论写入缓存。
- API：`GET editor/availability?event_key`、`POST editor/availability/sync`、`GET editor/free-rooms?week&weekday&sections&room_id`。`教室查询` 页的实时查空接口异常改为 502 可读提示（原为 500）。
- 界面：选中/拖动课次即叠加热力层（红斜纹=学生/本人有课禁放，琥珀=教室已占用需换教室，绿=可放，灰=教室未查）+ 图例与覆盖说明；左侧周列表显示每周“N 可放”（学生与本人都有空的时段数）；放到琥珀格自动保存并弹出该时段空闲教室二次搜索，选中即换教室；抽屉内实时显示当前周/星期/节次的结论与「查询空闲教室」；变更清单显示教室状态与教务冲突明细；顶栏「同步可调时段」显示同步状态与时间。
- 联调清单（教务恢复后）：① 班级课表查询真实路径与 `bj_id` 参数名；② 教室课表查询是否存在；③ `ttksq_cxConflictCtzt` 返回的 `ctxxList`/`conflictXs` 字段名（前端按 kcmc/jxbmc/jsxm/xm/cdmc 友好展示，其余原样列出）。

## 逆向依据

教务 `index_ttksq.js`（`showSqView` 的「保存草稿」回调 → `getDatas()` → `saveDatas()` → `checkConflict()` → `ttksq_cxSaveTtksj.html`）与 `cxTtksqView.js`；用户提供的 DevTools 截图（`ttksq_tjTtksq.html` 仅在「提交申请」时调用）。

## 验证

- 单测 `tests/test_schedule_availability.py`（7 项：忙碌表/四级结论/草稿阻断与标记/候选探测/待联调状态/二次搜索缓存）与 `tests/test_schedule_editor.py`（16 项：校验、装饰、锁定、表单解析、位掩码、假教务传输的保存/冲突/强制/去重/撤回）；路由快照已更新。
- Playwright 审计（P03 运行时，注入合成教务快照）：首页入口、同周拖拽、抽屉教室搜索与原因保存、早读禁放、跨周摊开拖拽、保存确认与缺凭据提示、撤销、移动端布局，控制台零错误。

## 第三轮（2026-09-26）：放置规则、节假日/调休、课次重排

### 放置规则（`schedule_editor_service._normalize_proposed` + 前端 `dropTarget`/抽屉）

- **两小节为单位**：起始节只能是 2、4、6、8、10（`rules.pair_starts`，按学期最大节次推算）；两小节课落在 2-3 / 4-5 / 6-7 / 8-9 / 10-11；四小节课=两个连续单元，可跨上午/下午/晚上。抽屉节次下拉只列合规起点；拖拽到奇数起点直接拒绝。
- **已过去不可放置**：目标日期 < 今天（`china_today`）拒绝；**已上过的课次不能调整**（原课次日期 < 今天，409）。编辑器把已过去的列/课次打上「已过」并锁定，抽屉只读。
- **节假日不可放置**：目标日期在学期日历中为 `holiday` 时拒绝；列头标签+红色斜纹。
- **调休上课日**：目标日期为 `workday` 且知道补哪天时，草稿 `proposed` 记录 `follows_date/follows_weekday/calendar_kind`，本人课表冲突、学生课表/教室占用、空闲教室二次搜索都按**被补那天**的周次/星期判断；编辑器把被补那天的课次以「镜像卡」（虚线、不可拖，点击跳到原课次）显示在调休日列。

### 全国节假日/调休自动获取（`national_holiday_service` + `db/schema_national_holidays.py`）

- 数据源 holiday-cn（国务院放假通知 JSON，三个镜像按序尝试），每年一份 `{name, date, isOffDay}`；表 `national_holiday_days(year, date, kind holiday|workday, name, label, makeup_for_date, makeup_for_weekday, inferred, source, source_url, fetched_at)`，`UNIQUE(date, source)`。
- **补课星期推断**：同一假期块内的调休上班日按日期顺序对应该块最后 k 个工作日假期（例：国庆 9/20→10/6 周二、10/10→10/7 周三），`inferred=1` 并在标签/说明注明「推断，以学校通知为准」。
- 定时任务 `national_holiday_refresh`（每周，启动后 3 分钟首跑，`app.py` 启动注册），API：`GET editor/holidays/status`、`POST editor/holidays/refresh`（编辑器顶栏「更新节假日/调休」）。
- **接入点**：`academic_service.build_holiday_lookup(years, include_national_feed=True)` 底层先铺自动获取数据，再覆盖内置官方表与校内核验的 `ACADEMIC_MAKEUP_DATA`（人工核验优先于推断；内置表只知道"调休上班"时补上推断的补课星期）。学期日历同步 `_built_in_events`、首页/学期管理日历、博客节日提示都自动获得 2027+ 年份与补课映射。5 分钟进程内缓存，读取失败静默为空。

### 日历系统调休标记

- 学期日历面板（`semester_calendar.js`）：学校日历行缺补课星期时从共享 lookup 补齐；`renderSwapArrows` 在 board 上叠加 SVG，从调休上课日格子画**曲线箭头**指向被补那天的格子，标签「补周X」（推断加 `?`），两端格子描边同色，多组调休按色序区分。
- 3D 课表编辑器：载荷新增 `today`、`rules.pair_starts/pair_unit`、`calendar {days, swaps}`（学期日历表 holiday/workday 行覆盖共享 lookup，附周次/星期与被补周次/星期）。周列表卡片显示「假 N」「调休」「被补」徽标；本周说明条列出节假日与调休；`.cse-swaps` SVG 覆盖层画箭头：同周=列头→列头弧线，跨周=列头→左侧周卡（或周卡→列头），两周都不在当前周=周卡之间的括线；周列表滚动/布局变化重画；不同调休不同颜色。

### 课次重排（`offering_session_resequence_service`）

- 不变量：课次 id / `order_index` / 标题 / 内容 / 材料绑定（`class_offering_learning_materials`、HTML 包 `lesson_N`、git 同步入口）永不改变，**只重新分配日期**。已发生（日期 < 今天）且未被直接调整的课次冻结；其余课次按 (日期, 起始节) 排序后按 `order_index` 顺序依次领取时段；`cancelled` 课次不参与。例：第 3 周第 3 次课调到第 17 周 → 第 4 周变第 3 次 … 第 17 周变最后一次。
- 自动触发：`academic_schedule_prediction_service._publish` 在应用已审批通过的调整后调用 `resequence_offerings_after_publish`（同一事务、savepoint 内；失败只降级为 `resequence_failed` 警告，成功加 `sessions_resequenced` 提示）。同时刷新 `academic_schedule_session_bindings.current_json`（evidence `resequenced_after_adjustment`），下次同步不会报本地冲突。
- 编辑器：「课次重排」面板 `GET editor/resequence-preview`（把当前草稿当作已生效模拟，列出每门课的新顺序）与 `POST editor/resequence/apply`（按当前日期立即重排，用于日期在课堂中手动改过或审批已生效但顺序未更新的情况；只能重排本人课堂）。
- `schedule_metadata_json.resequence_history` 记录最近 10 次改动。

### 验证

- 单测：`tests/test_national_holidays_and_resequence.py`（推断/入库/lookup 覆盖优先级/多镜像容错/重排计划与应用/取消课次）、`tests/test_schedule_editor.py`（两小节、过去日期、节假日、已上过、载荷 today/calendar）、`tests/test_academic_schedule_overview.py`（审批生效后重排：第 1 次课落到最早剩余日期、材料随序号不变）。
- Playwright 审计（P03 运行时 + 合成快照，`.codex-temp/tk/editor-audit.spec.ts`）：学期管理日历曲线箭头、当前周已过锁定与只读抽屉、第 3 周国庆四天斜纹、学生有课/节假日/奇数起点三种拒绝、同周拖拽+教室二次搜索、抽屉换教室、跨周摊开、第 4 周同周箭头+第 1 周跨周箭头与镜像卡、重排预览与按日期重排、可调时段同步与保存到教务的缺凭据提示、撤销、移动端；控制台零错误。
