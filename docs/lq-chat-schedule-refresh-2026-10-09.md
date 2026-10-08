# 讨论气泡 / 共享 Composer / 3D 课表阶段标识 / 页头信息归集修复（2026-10-09）

来源：用户截图四处问题（试卷页信息归集把页头撑满、3D 课表小卡穿透与放大浮窗不是玻璃、调课申请阶段无标识、课堂讨论消息无归属且输入框简陋）。本记录登记来源文件、共享组件与变体、保留的业务钩子、状态、实际测试与剩余限制；源码声明不等于验收，浏览器证据位于 `.codex-temp/deck-harness/shots/`（临时目录，不入库）。

## 1. 页头宽侧栏（试卷页首块显示异常）

- 根因：`lq_page_head` 的 aside 槽只在 `.lq-manage-pilot` 或旧 `.page-head` 下有"整行换行"规则；生产壳是 `lq-manage-shell`，于是 `.manage-pagehead__insights` 以 383px 列挂在标题右侧，把页头撑到 739px。
- 修复：规则归属组件 owner `static/css/lq/components/content.css`：`.lq-page-head:has(> .page-head__aside > :is(.manage-pagehead__insights,[data-page-head-wide])) > .page-head__aside { flex: 1 1 100%; order: 3; display: block; }`；删除 `manage-pilot.css` 中只对 pilot 生效的重复规则。所有使用 `insight_*` 宏的管理页（课堂/班级/课程/材料/签名/教材/试卷）同受益。
- 证据：隔离 SQLite 运行目录（`p03-editor`，LQ 页族与生产一致）里 `/manage/library/exams` 页头高度 739 → 314，aside `order:3; flex:1 1 100%`，筛选面板与试卷列表回到首屏。

## 2. 3D 课表（`course_schedule_deck.js` + `course_schedule_styles.js`）

| 项 | 变化 | owner |
|---|---|---|
| 小卡/网格卡片穿透 | `.cs-lesson__surface` 改用 `--cs-lesson-fill`（accent 14% 混 `--ls-surface-1`，再 88% 不透明），不新增 backdrop-filter | styles |
| 放大浮窗 | `.cs-expand` 使用共享 `--ls-scrim`（只压暗不模糊）；`.cs-expand__card` 加共享 rim 高光；`::before` 霜层改 thick 模糊 | styles |
| 课次放大卡 | `.is-preview .cs-lesson__surface` 使用 raised 填充 + rim（网格内唯一实时模糊宿主不变） | styles |
| 阶段标识 | `scheduleChangeBadge(lesson)`（`course_schedule_presentation.js`）→ 共享 status Chip（`lq-chip lq-chip--status lq-chip--sm` + `data-tone`），deck 只定位缩放：网格卡右上角，迷你卡顶部窄条，放大卡内联 | presentation + deck |

阶段映射：`draft` 草稿（neutral）→ `pending` 审核中（warning；拟位置卡显示"拟位置"）→ `planned` 已批准（success；计划位置卡显示"计划位置"）→ 已生效时间变更不加标识（卡片已在新位置，保留原"查看原安排"按钮与箭头）；任何已批准并生效的换教室显示"已换教室"（info），待审/草稿/已批准中含换教室的追加"·换教室"。

数据层（读层派生，不改快照、不建表、不计课时）：`_planned_change_relations` 新增两类关系复用 `planned_changes` 载体——`phase='draft'/approval_status='draft'`（教师未提交草稿，仅标原卡，不画目标、不预测）与 `phase='approved'/kind='room'`（仅换教室且正式课表已体现）。学生授权读取过滤草稿。前端 `projectScheduleChanges` 对三种 phase 分别校验，草稿不生成目标卡。

保留合同：`setOverview/goToWeek/focusLesson/openExpanded/destroy`、`pendingScheduleChange` 仍只认 pending、箭头/连线、LQ layer bridge、课次业务身份与课时统计全部不变。

## 3. 课堂讨论：共享 Bubble 与共享 Composer

- Bubble（`static/css/lq/components/content.css`）：填充/描边/圆角/高光/进场动效统一；`--incoming` 左下尾角、`--outgoing` 右下尾角 + `--ls-primary-soft` 配对 `--ls-on-primary-soft`，新增 `.lq-bubble--assistant`（success 配对）；forced-colors 下无阴影。消费者：课堂研讨室（`chat.js`，`.chat-message-main` 即 bubble，引用/附件/表情/操作栏为内容槽）、课堂一对一（`classroom_private_messages.js`）、消息中心私信（`message_center.js`）。系统消息不是气泡。
- Composer（`static/css/lq/components/composer.css`）：外壳 `:focus-within` 环（去掉 textarea 内框）、`__content` 槽只在有可见子项时占位、工具图标 36px/粗指针 44px、右侧 `lq-status` 状态槽、圆形 prominent 发送。课堂研讨室与一对一两个表单改为同一 DOM 合同（`data-lq-composer` + `__content/__input/__actions/__tools`），`chat.js`/私信控制器仍是唯一行为 owner（发送、限频、上传、拖放、放大输入框、Enter 发送）；新增 IME 守卫（`isComposing`/229 不发送）。限频/上传中不再给发送按钮改色，改由 `#chat-send-state` 状态芯片说明并保持 native `disabled`。
- 清理：`ui-system.src.css` 与 `classroom_workspace.css` 中课堂私有的输入壳/操作胶囊/发送按钮配色（含 `.classroom-page .chat-composer-*` 绝对定位工具条）全部删除；消息操作按钮改 ghost、细指针悬停前 55% 透明但高度不变（滚动锚定）。

## 4. 测试与证据

- Python：`tests.test_academic_schedule_planned_changes`（新增草稿标识与已换教室标识用例，10/10）、`test_academic_schedule_predictions/overview/student_course_schedule`（50/50）、`unittest -p "test_lq*.py"`（224/224）。
- Vitest 全量 728/728（含 `course-schedule-planned-changes`、`tests/lq`）。
- Playwright 组件：`academic-schedule-deck`（断言改为"浮窗 scrim 压暗不模糊"，9/9）、`course-schedule-stack`、`course-schedule-deck`、`classroom-chat-escape`、`lq-composer`（粗指针 44px 已补）、`lq-content` 全部通过。
- `python tools/ui/lint_lq.py` 无 blocking。
- 浏览器：隔离运行目录登录 QA 教师，试卷页、课堂 972 研讨室（种子 5 条消息：教师/学生/AI 助教）、一对一页截图；3D 课表用 `course-schedule-stack` 同款离线装配加照片背景截图四态（堆叠/放大/课次放大/已生效对照）。
- 剩余限制：深色主题与六调色板只按令牌配对与既有 deck 调色板用例验证，未逐一截图；真实教务草稿需在线同步后才会出现在课表（读层派生，不需迁移）。
