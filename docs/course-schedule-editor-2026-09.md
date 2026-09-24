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
