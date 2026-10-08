# 原生选择控件接入与计划调课验收

本轮以原生 select 的值、表单、校验和原事件为唯一业务来源，用共享玻璃选择模块增强呈现。源码清单、运行时所有权与业务验收分别记录；原有全平台 pending/unknown 不因增加声明而改为完成。

## 全平台入口盘点

`python tools/ui/audit_lq_selects.py --output <path>` 扫描 HTML、动态 JS、React 和共享定义；跳过块注释、整行注释、第三方压缩代码。初始快照 `.codex-temp/lq-select-inventory-20261008-initial.json` 有 100 个相关文件、346 个声明或构造候选：287 原生标签、51 个 `lq_field(control='select')`、2 个 DOM 构造、6 个 React 实例。模板分支和共享定义分别计数，不能解释为 346 个用户可见控件。每项含路径、行号、原声明和复核标记；每文件保留 SHA256。

| 接入组 | 主要入口 | 必须保留的合同 |
|---|---|---|
| 原生 SSR | 全部作者模板、lq_field 两种页族分支 | name、form、required、disabled、默认选中、真实 label；无 JS 时仍可用 |
| 课时统计 | manage/course_schedule.html 的 data-cs-term/course/class | manage_course_schedule 每次请求后重写 options；筛选、课程卡片选择、重置都同步代理，不二次请求 |
| 动态整块重绘 | collaboration、attendance_reports、dashboard_agenda_widget、career_path_app、manage_lesson_plans、resume_applications | 领域显式挂载，或声明节点由共享 installDropdowns 首次交互委托激活；销毁时释放，不做每控件全 document observer 扫描 |
| 动态表单/弹层 | academic_schedule_sync、academic_sync_dialog、classroom_polls、lessondoc_wizard、manage_assessment_plans、manage_polls、manage_teacher_evaluations、material_selection_panel、signature_scope_fields | 父层 portal、关闭焦点、destroy、disabled 条件、取消/失败草稿不变 |
| DOM 构造 | assessment_classification_batch.js | 原 adoptDomainControl 只提供材质；选项填充、value 设定、DOM 挂载后接共享选择，不改变分类确认逻辑 |
| React | LqNativeSelect，classroom-workspace 两项与 dashboard-workspace 四项 | React 唯一 state；ref/effect 挂载与销毁，value/options 变化 refresh；不在 render 期间改 DOM，不复制第二份选中状态 |
| 多选 | collaboration 的 member_student_ids multiple size=6 | FormData.getAll、选项禁用、组合选择、原默认值及 reset，不得改成单选或操作菜单 |

117 个初始候选没有直接 id/aria 命名，必须结合附近或包裹 label 人工判断；该标记不自动等于可访问性缺陷。`academic_schedule_sync` 的包裹 label 不能直接用整个 textContent 命名，否则选项文字会拼入控件名称。后端 Python 搜索未发现业务页直接拼装 select；`academic_course_sync_service` 的 select 正则属于解析上游 HTML，不是界面消费者。共享表单描述器与 Selection 工厂属于定义，禁止重复迁移。

## 现有 Selection 所有者，禁止再套一层

| 所有者 | 原 select |
|---|---|
| dashboard.js | `[data-semester-filter]`、所属 3D 面板 `[data-csd-term]`，受 data-lq-dashboard 页族控制 |
| student_dashboard_schedule.js | `[data-student-schedule-term]`、`[data-student-course-term]`、`[data-student-course-state]` |
| semester_calendar.js | `[data-semester-calendar-select]`，受 data-lq-calendar 控制 |
| ui_preferences_panel.js | `select[data-ui-palette-select]`、`select[data-ui-preference-select]`、`select[data-ui-backdrop-category]` |
| message_center.js | `#message-center-filter`，受 lqEnabled 控制 |
| lq/preview.js | `select[data-lq-selection]` |

course_schedule_editor 已使用独立 Dropdown：学期、周次、节次、星期和异步教室查询。其公共 query/setResults 票据合同必须保留；共享 bootstrap 要识别已存在绑定，避免先于 Selection 抢占同一原生节点。页族关闭分支也不能误报已经绑定。

## 已识别的共享接入风险

- 程序写 value/selectedIndex、整批替换 options、form.reset 和 fieldset.disabled 不一定产生 change；呈现同步不能发出第二次业务 change。
- 原生 option.selected 批量写入需要明确 refresh 合同，不轮询所有控件。
- 单次 SSR 扫描与动态显式 owner 是主要入口；首次交互委托只能作为声明节点的兜底，不能先弹系统菜单再打开玻璃菜单。
- Dropdown 与 Selection 独立 Symbol 的历史实现不能重复绑定；销毁须恢复 labels、tabindex、aria、原生节点与默认值。
- 长班级名与联合班级名须换行或可完整访问，面板受视口约束；触屏不得触发桌面原生下拉。
- 原实现 trigger 内嵌 button 箭头会形成嵌套按钮；应使用装饰图标槽。
- iframe 中的用户课件、简历正文、导出/打印文档、第三方内容不做全局控件扫描。周边编辑器和工具 UI 仍属于迁移范围。

## 独立验收环境与范围

使用全新 `.codex-temp/lq-selection-20261008-runtime`（空 SQLite 构建、uiV3/lqS3 合成身份、32 节课程），禁 dotenv、PostgreSQL 和外网。所有测试核对 `/api/internal/health` 的 database_path，使用有上限的就绪等待。未使用真实业务数据库。范围如下：

1. 课时统计三下拉的长班级选项、学期/课程/班级实际请求、课程卡程序选择、重置、结果更新；核对原 select 值与代理一致、没有重复请求。
2. 实际管理表单的原生提交与 reset、required、disabled、键盘、触屏及父子弹层；fieldset 联动与复杂销毁边界由共享组件回归补充。
3. React 课堂任务及首页历史筛选，选项和 state 更新、卸载重开，无双代理和旧列表泄漏。
4. 学生页面三个既有 Selection 筛选保留唯一 owner，其余可见页面控件由只读巡检记录。没有把偏好保存/冲突和完整日历业务算成本轮实际页面覆盖。
5. 3D 合成快照同时包含待审预测、已批准未落实的计划、已落实历史与节假日调休；双向定位、标签区分、非正式端点不计课时且不可编辑，师生页面一致。
6. 桌面/390 触屏、浅/深、关闭透明、键盘和 reduced-motion；浮层 blur 预算、无叶级重复模糊、静止无持续 rAF。

现有 Selection/Dropdown 组件测试由共享组件负责人维护，本任务新增独立真实页面用例。原生同步 POST 仅由合成请求夹具返回 409 验证冲突后旧课表保留；身份编辑只生成未保存草稿；不会调用外部教务服务。

## 本轮已确认的边界

- 源码初始及最终扫描都包含 346 个声明/构造候选；最终每个业务原生标签都有明确声明，剩余未带 Dropdown 声明的两处是共享 Selection 工厂定义。React 实例、两种模板分支、组件定义不等于独立的可见控件。
- 112 次只读 GET 覆盖教师、学生、管理身份及 390/1440 两宽。初始 18 次“可见原生且未立即绑定”的观测对应 9 个动态声明控件在两宽重复出现；这些控件由共享首次交互委托拥有，必须真实 click/tap 后确认，不能据初始 WeakMap 计数直接判定漏迁移。
- 9 个入口是试卷编辑题型 1 个、出勤档案列表每页数量 1 个、3 个出勤档案详情的原件/解析版本共 6 个、教学课堂页动态星期 1 个。专项同时检查唯一 popup、玻璃背景、可访问名、选项、原值及只开关时不产生 input/change。个人设置通过“添加身份”产生动态字段后验证。
- 3D 计划是只读视图投影，官方快照仍 32 节/64 课时；计划、待审预测和已落实历史分别有两端关系，节假日调休独立。师生均展示计划，学生教师编辑 API 为 403；虚拟计划卡不触发材料、时段查询或保存。
- 实际浏览器为 Chrome 桌面与触屏模拟，覆盖浅/深及 off/reduced/无透明；没有宣称 Safari、Firefox、物理手机帧率或全平台每种业务状态全部通过。共享控件仍保留无 JS 原生回退。

## 验收记录

最终正式图、逐次结果、源码哈希与历史失败说明见 `docs/lq-select-acceptance-evidence-2026-10-08.json`。只读 GET 是页面可达性和 DOM 归属证据，不替代该页面完整业务验证。下拉浮层静止 400ms 期间无新增 rAF，视口玻璃宿主不超过 3、按钮/选项等叶节点没有独立 blur。

首轮验收发现 Field 弹性槽中的 Dropdown 未撑满，及 searchable 通过受保护 attrs 传入失效；两项由共享组件修复。最终实际页增加三个筛选完整槽宽、长班级真实搜索与清空、桌面/触屏截图断言。原始截图和旧资源图记录保留，不用终态通过覆盖历史证据。

最终正式图：`384fb20570aebb392dd0f34781fa8bd4340a13653f7fe5f678b18e049fe06a39`。本轮最终完整运行 **22/22** 实际页场景、**17/17** 不可变资源 Dropdown 场景通过。此前 Selection/Form **57** 项及 React 开发夹具 **32** 项作为补充证据，分别标明运行环境。

只读巡检 **112 GET**，路由错误、浏览器错误/初始化警告、运行中源码漂移、页面横溢、叶级 blur、无效声明、双所有者与无名代理均 **0**。该巡检绑定旧图 `475a46c…`；最后共享 Field 修复后由 22 实页及 17 组件定向重验，不声称全量 GET 重跑。

历史主跑的 3 次启动未就绪和各项测试定位/合成空数据修正均保留原日志；没有以放宽断言或修改产品状态强行通过。实际验收发现的 Field 宽度/searchable 问题已在共享模块修复并重验。发布与 Git 结果由主任务记录。

## 上线核验

2026-10-08 已部署发布 `20261008-210800-be43d5e5933c`，生产代码提交 `edf5318ea31abb913331bf47ac63c4864ab955e8`。新隔离目录通过 DryRun，原生 PostgreSQL 迁移凭据及 72 个迁移源校验通过；打包清单排除运行数据、上传目录、环境文件和用户未提交文件。部署在停止应用写入后完成数据库备份，并保留代码回滚包。

线上资源图与上述最终验收图一致；11 个原生 JS/CSS 文件、Vite manifest 及 3 个 React 包逐字节一致，教师/学生登录页使用新资源。部署后 8 项检查通过，8 个容器运行。测试服务器已停止，原始验收资料保留。Git 交付分支为 `dev`，验收资料提交不改变已上线业务代码，远端推送在最终交付时单独核验。

既有后台任务汇总 `ok=false` 在部署前后均为历史失败 742、排队 28、运行 0、过期 0；汇总未变化。本次页面修复不将该历史状态算作已修复。上线证据已附入 `lq-select-acceptance-evidence-2026-10-08.json` 的 `release` 字段。
