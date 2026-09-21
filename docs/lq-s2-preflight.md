# S2 组件实施前审计

审计日期：2026-09-20。依据：[执行计划](liquid-glass-execution-plan-2026-09.md) §10、§11、§16 的 S2 出口及当前源代码。本轮只新增本文件；S1 CSS 保持冻结，没有执行 HTTP、数据库操作、构建或页面迁移。**本文件是实施输入，不代表 S1 已签收或 S2 已开始。S2 实施以负责人完成 S1 门禁为前提。**

现有基础足以支持渐进实施：保留 `ui_popover.js`、日期、灯箱、说明浮窗及业务 controller，补齐共享呈现、弹层协调和测试。最大的风险是把视觉组件同时做成第二套请求队列、弹层栈或页面控制器。S2 应交付可在预览中完整验证的组件库，加上计划明确要求的兼容桥；全站壳和业务页迁移仍按 S3–S6 分批进行。

## 1. 已核实的现状与实施边界

| 范围 | 当前实际入口 | 对 S2 的约束 |
|---|---|---|
| 样式基础 | `static/css/lq/{index,tokens,base,materials}.css`，唯一 Tailwind 输入 `static/css/ui-system.src.css` | 继续单入口展开 import；组件使用已定 typed tokens，不复制颜色表、不恢复旧全局字体切换 |
| 主题运行时 | `static/js/lq/theme.js`；SSR bootstrap 提供 `window.LanShareTheme`；`lq:theme-change` | 组件订阅现有主题；不创建第二份偏好存储、媒体监听器或账户 PATCH；只有明确标记的 app iframe 使用现有主题桥 |
| 预览 | `templates/dev/lq.html`、`static/js/lq/preview.js` | 已是 S1 材质/色对预览；扩展同一路由，保留教师可读、开关默认关闭、本地控制不写账户 |
| Jinja | `templates/macros/manage_page.html`、`manage_insights.html`、`manage_icons.html`、`app_topbar.html`、`ui_explanation.html`、`user_ui_preferences.html` | 尚无 `macros/lq/`；优先包装已有语义和数据契约，不另造管理导航、说明系统或统计计算 |
| 原生 JS | `static/js/ui.js`、`ui_popover.js`、`manage_filter_chips.js`、日期/灯箱等 | 尚无 `lq/index.js`、`LQ.layer` 和通用组件库；现有全局兼容 API 有真实消费者 |
| React | `components/action-entry.tsx`；`components/ui/dialog.tsx`；各业务 island | `components/ui/` 当前仅剩 Dialog 文件。不能重新引入已退役的整套 shadcn 控件；有实际消费者才写 LQ React 适配器 |
| 挂载 | `frontend/src/lib/mount-react-island.tsx` 使用 StrictMode 与挂载登记 | 每个增强器与适配 hook 必须幂等、可销毁；不得与原生控制器同时拥有同一状态 |
| 实施记录 | `docs/lq-components.md` 仍为 S0 占位表 | S2 更新该现有文件为正式目录；不要把本预检或私有旧组件当作已完成共享组件 |

**S2 可改的运行路径**：新组件/预览；`ui.js` 的兼容转发；共用弹层基础；日期/灯箱登记；计划指定的原生 dialog 与 React Dialog 适配。它们确实影响运行代码，因此需要回归，不能把“不迁页”解释成“不会影响现有页面”。

**留给后续阶段**：`manage_page.html` 三宏在旧页的统一输出替换和管理壳试点属于 S3；站点导航/Dock/日历 todo 属于 S4；作答/批改 controller 接入属于 S5；白板、LessonDoc、简历及复杂工作台属于 S6。S2 可准备兼容实现、预览与迁移适配样例，不能通过全局选择器提前改遍所有旧页。

## 2. 实施前需固定的契约差异

以下均来自计划与当前代码的对读，建议在第一个 S2 工作包同步写入 `lq-components.md`，不需要现在修改 S1 或主计划。

| 差异 | 明确处理方式 |
|---|---|
| §10.5 要升级旧三宏，§16 把实际升级放 S3 | S2 完成新内部实现和兼容 fixture；旧宏默认输出切换随 S3 试点。保留宏名、参数、调用者槽和旧 DOM hooks |
| §10.6 `type` 枚举没有 `viewer`，§10.9 要注册 viewer | 将 `viewer` 明确加入协调器登记类型；它复用现有灯箱 controller，不新建图片查看器。外部层另用登记适配契约，不把所有外部 DOM 都传给新 `open()` |
| 灯箱 API 在计划中被简写 | 实际是 `openImageLightbox({items,index,groupLabel})` / `window.LsImageLightbox.open(options)`；聊天类是 `open(item,siblings=[])`、`ensure()`、`isOpen()`、`close()`。两层都保留，不强制改成 `open(items,index)` |
| §10.9 题号 coarse 40px，§10.1 总触控目标至少 44px | 可保留 40px 视觉块，但实际布局内点击目标至少 44px且互不重叠；预览直接验证实际 bounding box 和相邻命中区域 |
| §10 按钮白色玻璃底、白 thumb、`primary` 文本是设计简写 | 采用当前 appearance/material 对应填充与成对前景；正文/链接不能只因用了 `--ls-primary` 就宣称达到 4.5。使用适合实际底面的已验证前景或新增具名色对，经对比计算后确定 |
| 语义 base 与操作实底并非同一用途 | `*-base` 配 `*-on-base`；`*-solid` 配 `*-on-solid`；`*-soft` 配 `*-fg`。不得把白字统一套到 warning/success base，也不得把暗色浅 ink 放到浅 primary 按钮 |
| “三入口等价”与“React 只为实际消费者实现” | Jinja、原生 JS、React 是三入口；原生 Element 与 escaped HTML 是同一入口的两条构造路径，都测。无 React 消费者的项登记 N/A 和原因，不造空壳 React 组件凑覆盖率 |
| `stage-9` 折叠可复用描述已过时 | 当前 `dashboard.js` 末尾只剩阶段注释；没有可直接导入的通用移动折叠实现。可复用实际 dashboard 分组存储、管理侧栏和考试分组的既有状态契约，不能声称通用 `LQ.collapsible` 已存在 |
| “替换 8 套 tabs / 8 个 toast / 约 40 spinner”是计划估数 | 实施按实际消费者登记、逐页清理。S2 不用搜索替换强行达到这些数量，也不先删私有实现后补页面 |
| 原生 top layer 与 body Portal 并存 | 子日期/选择器/灯箱必须挂到有效模态宿主或使用同一 top-layer 管理策略；提高 z-index 不能穿透原生 modal。父子层同一时刻只由一个控制器锁焦点 |
| 新字体会改变旧页几何 | S1 已维持旧页字体。S2 新字体仅应用到明确 opt-in 的组件/预览，壳或页面切换必须在后续迁移时重新验收布局 |

颜色通道保持 Tailwind 3.4 的 HSL 契约：`hsl-channels` 由 `hsl(var(--token))` 消费；`hsl-alpha-channels` 已含 `/ alpha`，不能再拼一个透明度；完整 `color` 别名不能再嵌套 `hsl()`。复用 `tools/ui/export_tokens.py:resolve_theme()` 和 `tests/test_lq_tokens.py` 的真实底面合成方法，避免在 JS、React 中各存一套 palette/状态色表。

## 3. 统一入口、状态与所有权

### 3.1 最小公共约定

| 维度 | 统一契约 |
|---|---|
| 语义 | 原生 button/a/input/select 优先；同组件各入口的 role、可访问名称、值、disabled/busy/invalid/current/selected、label 与 description 关系一致 |
| 结构 | 统一 `lq-*` 类、计划规定槽名、`data-tone` 与状态类；允许 React wrapper、Portal 和动态 id，不做逐字 HTML 等价 |
| 构造 | Jinja 宏负责 SSR；`LQ.<component>()` 返回 Element；需要模板字符串的组件另有 `LQ.html.<component>()`；React 负责 props/refs 与同一 DOM 契约，不复制协调器 |
| 安全 | 文本/属性默认转义；href 经协议校验。新 `attrs` 使用结构化允许列表，允许所需 `aria-*`/`data-*`，拒绝事件处理器字符串与任意 HTML。旧宏已有 `attrs\|safe` 不能复制成新 API 的通用旁路 |
| 初始化 | `LQ.ready(fn)` 兼容 module 加载前后调用，回调只执行一次；ESM 导出与全局对象引用同一实例；不同版本 URL 与相对 import 不能重复安装监听器 |
| 生命周期 | 有行为的增强器返回明确的更新/销毁能力；销毁释放事件、observer、timer、焦点/滚动锁。重复 init/destroy、节点移除、异步完成晚于关闭均可验证 |
| 输入 | 事件回调携带用户意图，值由现有 form/controller 持有；不偷偷 fetch，不引入组件自己的持久化/权限判断 |
| 动态状态 | SaveStatus、JobStatus、UploadQueue、Clock 是呈现层。状态转换、服务器确认、版本冲突、重试、轮询和 SSE 在业务 controller |
| 主题与材质 | 读取 documentElement 的 S1 属性；宿主 tone 用 `data-lq-tone`，语义 tone 用 `data-tone`，不可混为一个状态。控件不能自建 blur 叠层 |

`LQ.tone` 的最小实现是具名 tone/已知宿主的映射辅助，不需要在 S2 另建全站图片采样、跨源 canvas 或每帧背景检测。透明 Clear 场景沿用明确 tone + 局部 scrim + 实际像素验收；登录背景的 S1 证明不能自动外推到任意组件或用户图片。

图标沿用项目已有 SVG/currentColor 与 `lucide-react` 能力，从一份允许的图标名称清单生成宏/JS 可用登记；不要同时维护 `manage_icons`、`app_topbar_icon` 和一套独立手写 LQ 路径表。保留旧图标名称的兼容映射，未知名称应有受控回退；装饰图标 aria-hidden，独立按钮的名称由按钮提供。

### 3.2 React 实际消费者清单

| 现有消费者 | 可复用能力 / 首批适配目标 | 不能改掉的行为 |
|---|---|---|
| `components/action-entry.tsx` → `islands/blog-launcher.tsx`、`feedback-launcher.tsx`、`profile-launcher.tsx` | `IconActionLink`、`IconActionButton`、`AvatarActionLink`；可作为 LqButton/Avatar 的兼容包装入口 | 原生链接/按钮、默认 `type=button`、既有回调与 href；不能为了视觉统一把链接变 click div |
| `islands/classroom-workspace.tsx` → `components/ui/dialog.tsx` | `useLqLayer` / Dialog 外壳适配 | `ExistingSurface` 移动真实业务 DOM 并在退出恢复；保存滚动、任务定位、焦点与旧编辑器交接；不能克隆内容或重挂原生业务节点 |
| `islands/dashboard-workspace.tsx` → 同一 Dialog | 同一适配器；已有视图切换可作为 Tabs/Segment 真实场景 | 待办/日历交接回调、scrollPosition、returnFocus；不得接管日历 controller |
| `islands/assignment-authoring-sync.tsx` | 按钮、状态提示、progress、配置 chips 呈现 | `ASSIGNMENT_AUTHORING_EVENT/COMMAND_EVENT` 与 readiness 计算仍在既有 lib/controller；这些配置 chip 是定位命令，不冒充 filter 或 tab |
| `islands/assignment-submit-sync.tsx` | SaveStatus/Alert/Progress 呈现 | 答题/附件/availability 快照、MutationObserver 清理、`data-assignment-submit-managed` 的所有权边界 |
| `islands/submission-jump-nav.tsx` | LqQuestionNavigator；最明确的业务三入口等价样例 | `data-jump-question`、`submission-q-*` 目标、作答统计与滚动定位；原生 fallback 不双绑定 |
| `islands/app-shell.tsx` | 复用现有 details 导航增强 | 当前渲染 `null`，只增强原生菜单并清理监听；不要再渲染第二套 React 顶栏 |

这里只登记实际使用点，不意味着 S2 应提前改它们的所有页面外观。除计划指定 Dialog 桥以外，适配器可先在对应数据 fixture/预览中验证，业务接入随页族迁移。

## 4. §10.1–10.9 逐项消费与验收

### 4.1 §10.1 按钮

当前入口包括 `manage_page.html:page_head` 的 actions、`ui.js`/各原生模块生成的 `.btn`、React `action-entry.tsx`，以及 `assignment-authoring-sync.tsx` 的保存/定位命令。没有现成通用 LQ 按钮工厂。

实施一份槽位/变体规格：prominent、glass、soft、ghost、destructive、link；sm/md/lg/icon；`label/icon/badge/loading/type/href/id/attrs`。只在显式 href 时产生导航链接，submit 必须显式指定；loading 保留名称、宽度和 `aria-busy`，不靠隐藏文本实现。危险确认的实底色使用 danger solid 色对，普通危险次操作用 soft/fg。

门禁：三入口的 button/anchor、默认 type、提交次数、原生 disabled、aria-disabled 的键鼠执行拦截、图标名称、loading 名称/焦点/宽度、实际粗指针目标、focus-visible 无裁切；`LQ.html.btn` 注入 payload 和危险 href 拒绝。所有变体按真实底面测试 default/hover/focus/active，而不是只检查 token 名。

### 4.2 §10.2 Chip

可复用 `static/js/manage_filter_chips.js`；当前真实使用为 `templates/manage/courses.html` 的 `#courseFilterSelect` 与 `templates/manage/offering_hub.html` 的 `#offeringHubStatusFilter`。脚本以原生 select 为真值，点击 `.filter-chip` 后发 `change`，反向 change 更新 `.is-active`；目前没有完整通用 aria-pressed/销毁契约。

S2 增强 filter 的 `aria-pressed` 和生命周期，保留 `data-filter-chips/data-filter-target` 与一次 change 语义。status 输出普通 span；tag 的删除是有名称的独立按钮。`assignment-authoring-sync` 的“定位字段”chip 是命令，不使用 filter 的选中语义。

门禁：select→chip 与 chip→select 双向同步、重复 init 不双发 change；status 不产生多余 live 播报；删除键盘/44px触控；长文本、>8 个“更多”、横滚后的焦点可见和选择保留；选中态配色在六 palette 亮暗下可读。

### 4.3 §10.3 Segment / Tabs

当前具体消费者：

- `templates/classroom_main_v4.html` 的 `data-classroom-activity-tab/panel`；`static/js/classroom_page.js:initClassroomActivitySidebar` 已处理 aria-selected、roving tabindex、hash、面板存在性、resize 与 `classroom:activity-visible`。共享组件只接切换语义，不能吞掉业务可见事件。
- `static/js/classroom_members.js` 已有方向键/Home/End；它是可复用的键盘行为参考，不是全站状态仓库。
- `templates/dashboard.html` 的 `data-group-mode` 和 `dashboard.js` 分组模式；React `dashboard-workspace.tsx` 的事项/日历切换；`dashboard.js` 的评价来源 tabs。
- `static/js/classroom_workspace.js:bindClassroomLessonRail` 负责课次横向浏览与进入命令；它不是互斥内容 tab，不应硬替换成 Segment。

`LQ.tabs(root,{persist,hash})` 与 Segment 共享选项/面板识别、roving focus、销毁能力；在契约中明确自动或手动激活策略。延迟/异步面板应支持手动 Enter/Space 激活。持久化 key 由调用者按身份/资源提供，hash 只接受当前已授权面板。面板切换不重挂子树，不重置输入、IME、附件和滚动。

门禁：箭头/Home/End、单一 tabstop、aria-controls/labelledby、disabled tab 跳过、hash 无效/重复值、浏览器返回、持久化失败回退；快速连切与 reduced-motion 不留下半显面板；焦点在隐藏面板前被合理转移；classroom 可见事件仍只针对最终活动面板触发。

### 4.4 §10.4 导航组件

| 项目 | 当前入口/消费者 | S2 可复用与缺口 | 必测行为 |
|---|---|---|---|
| Topbar | `base_navbar.html`、`macros/app_topbar.html`；React `app-shell.tsx` 的 details 菜单增强 | 保留 server 导航/原生 details；准备三区与 immersive 变体，旧页替换留 S4 | sticky/condensed 不遮焦点；返回链接、长标题、3 个操作+更多；Esc 与点击外部不抢错误焦点 |
| Sidebar / NavItem | `manage/layout.html`；`manage_nav_service.py` | 复用权限过滤、当前域、单域展开、搜索与 `lanshare:manage-sidebar-collapsed`；不另造菜单注册服务 | current/expanded、搜索不丢选中；drawer 受 layer 管理；无权限项不能由前端补回 |
| Dock | `partials/app_bottomnav.html`；课堂活动面板入口 | 准备共享呈现和 viewport 订阅，实际替换 S4/S6 | ≤5 项+更多；safe-area、键盘缩小后隐藏；唯一操作另有等价入口；底部内容可完整滚到可见 |
| FAB | `ai_workspace_widget.js` 的 `#ai-agent-fab-queue-badge` 及工作台私有入口 | 抽出按钮/菜单呈现，任务状态仍由原 controller 控制 | 展开/收回、Esc、最多3项、Dock避让、触控目标不重叠 |
| Crumbs / Steps | 当前无同名共享宏；页面各自的返回链接/流程说明是迁移消费者 | 新建小型语义宏/原生构造，不造业务流程状态机 | nav 名称、当前项 aria-current、移动只显示上一级时仍能返回；流程文字不只依赖颜色，≤640纵向 |

侧栏断点需一次写清：§10.4“≤1024 drawer”与 §11.4“1024–1279 rail、768–1023 drawer”有边界冲突，建议以 §11.4 的完整断点表为准，并测 1023/1024/1025。S2 不通过临时 CSS 同时执行两套断点。

### 4.5 §10.5 内容与表单组件

| 项目 | 现有入口与消费位置 | 最小共享范围与保留部分 | 必测重点 |
|---|---|---|---|
| Card / List / Row | `dashboard-workspace.tsx` 的事项卡；`classroom-workspace.tsx` 的课次索引；各管理列表 | 结构、槽位、表面、动作语义；仍用原列表数据/权限/分页 | 整卡动作与次操作不嵌套；键盘能分别到达；触控次操作可见；长标题/空元信息 |
| Table / Pager / BulkBar / ResultCount | 管理课程/开课列表；学生成绩与评分表格 | 提供 record 与 matrix 两类 responsive 模式；不创建第二份筛选或选中数据 | 表头关联、排序状态、混合选择、页码边界；手机记录卡保留字段名；矩阵仅容器横滚、页面不溢出 |
| Field / Input / Textarea / Select / Checkbox / Radio / Range / Switch | `manage_page.html:filter_bar`；课程筛选；五独立编辑器；现有偏好 select | 原生控件增强、标签/帮助/错误关联；select 默认原生值载体，radio 保持组语义；switch 可用原生 checkbox 加正确角色 | label点击、键盘、required/disabled/readonly、错误不清输入；清除按钮有名；range键盘和数值；IME Enter 不提交 |
| Combobox / Listbox | 当前没有可直接复用的通用 LQ 实现 | 与操作 Menu 分开；先确定真实搜索/选择消费需求、同步/异步所有者再实现对应模式 | expanded/controls/activedescendant、输入组合态、上下键/Enter/Esc、空结果、异步结果过期不覆盖新查询；原生 select fallback |
| ErrorSummary / FormActions / FormSection | 各编辑器和作业 form 当前各自呈现 | 共用定位和描述，不接管服务器校验或原生 submit | 一次提交后列错、摘要链接定位控件、首错可见、禁用不隐藏原因；400/409/权限错误有就地提示 |
| 日期时间 | `ls_date_picker.js` 自动增强原生输入；`window.LsDatePicker.enhance/close` | 单例面板换皮与 layer 登记；`data-dp-pair` 最近 form→document 解析、`data-ls-native` opt-out、min/max/step和原生值事件保留 | modal 中打开、Esc 顺序、配对字段、禁用日期/时段、原生退出；回焦可见的`.ls-dp-display`，避免focus原输入重新打开面板 |
| Empty | `manage_page.html:empty_state`；管理列表/任务列表空结果 | 统一 reason=`empty/no-results/error/forbidden/offline`；旧宏签名兼容 | error不能显示“暂无”；no-results可清筛选；权限态不出现无权动作；inline/card/page长文案 |
| PageHead / FilterBar | `manage_page.html:page_head/empty_state/filter_bar`；`manage/courses.html` 等 | 三宏兼容，S2准备新实现，S3实际旧页切换 | 所有参数和 caller 槽；`data-page-head`、`.page-head__copy/__desc/__aside/__actions` 保留；主操作数量、手机换行 |
| Insight Ring / Bars / Meter | `macros/manage_insights.html` 三宏，SVG/CSS已有计算 | 沿用 value/total/percent、aria-label、零分母保护；颜色改 data-tone，不加图表库 | total=0/空列表/负数和超界值受控；真实数值 SSR；无数据省略或降级；可访问说明与视觉值一致 |
| Prose | `markdown_runtime.js` 的 `parse/renderIntoElement/sanitizeHtml`；AI/chat Markdown | 只统一排版、代码复制和灯箱委托，保留安全 renderer | XSS/链接过滤不退化，长代码/表格仅局部横滚；复制有名；图片仍遵守原来源/下载权限 |
| Bubble / Composer | `classroom_main_v4.html` 聊天；`ai_workspace_widget.js`；`chat_image_preview.js` | 共享外壳/气泡/输入呈现；消息流、发送、附件与 Agent 状态仍在业务层 | 中文IME、Shift+Enter、上传中发送、失败重试、长消息/图片分组、无重挂输入；玻璃宿主内composer不再blur |

旧宏精确签名：

```text
page_head(title, description='', explain='', explain_label='', actions=[], eyebrow='', title_id='')
empty_state(title, description='', action_label='', action_href='', action_attrs='')
filter_bar(search_id='', search_placeholder='搜索…', search_attrs='')
```

不要把 `eyebrow` 删除成不兼容参数；只停止新组件填充装饰性眉题。旧 attrs 字符串仅留在兼容 wrapper，新增 LQ 接口使用结构化属性。

### 4.6 §10.6 Layer / Modal / Sheet / Drawer / Popover / Menu / Confirm / Choose / Tooltip

**已有基础**：`static/js/ui_popover.js:createPopoverSystem({prefix})` 已包含栈、anchor-parent、焦点圈闭、外部点击、Esc、定位、开关动画。`static/js/whiteboard/popover.js` 用 `prefix:'twb'`；`static/js/lessondoc_editor/ui.js` 使用同一基础。保留导出和各实例隔离，不能另建新层系统后让旧基础继续无协调地锁焦点。

动画不必重写：`static/js/ui_overlay_motion.js:setOverlayOpen(element,open)` 已用 WeakMap 管理当前操作，重复同状态返回同一 Promise，被新操作取代时返回 false，支持 reduced-motion 变化及动画超时兜底。`classroom_material_list.js`、`classroom_members.js`、`dashboard_agenda_widget.js`、`ui_explanation.js` 等已有消费者，`frontend/src/lib/ui-overlay-motion.test.ts` 有对应测试。复用它负责 presence，协调器只负责焦点、关闭意图与锁，不在第二层复制定时动画状态机。

**实际缺口**：当前没有完整 owner/beforeClose/父关闭级联、inert 回退、滚动锁计数、iOS位置恢复、原生 top-layer 登记、深链、异步取消。`destroy()` 调用 close 后移除节点，关闭 timer 与焦点恢复需要统一终止；无可聚焦子项时也需要面板兜底。现有 close reason 包含 `manual/replaced/resize/blur/backdrop`，不得静默丢弃原有回调语义。

建议协调层采用同一内部 stack record，区分“自己创建/显示的层”与“外部 controller 管理、只登记的层”；公开 `open` 使用计划 API，适配器内部提供登记/注销能力。登记至少包含 owner/root/trigger/type/modality/parentLayer/returnFocus/beforeClose/closeReason，以及外部 `isOpen/requestClose/destroy` 回调。原生或第三方已拥有的焦点圈闭必须明确移交或禁用其中一方，不能双 trap。

关闭须有明确的接受/拒绝结果及 checking-close/closing/closed/destroyed 生命周期。同一handle的重复关闭复用一次操作；`closeAll`按快照逆序请求并明确遇否决的停止策略。旧manager的`while(current) current.close()`假定每次同步出栈，直接在close里加入否决会让循环无法前进。退场期间也不能过早释放滚动锁或让旧回焦覆盖后来新开的层。灯箱在已打开时更新图片，应保留首次opener，不能改为当前内部关闭按钮。

有两项可直接由代码定位的竞争条件，应先写确定性测试再做桥接：

1. `ui_explanation.js`、`ls_date_picker.js`、`ls_image_lightbox.js` 都安装 document capture keydown；日期与灯箱不检查 `defaultPrevented`。说明的 `stopPropagation()` 不能阻止同一 document 上其他已登记的监听器继续处理，同一次 Esc 存在关闭多个层的路径。不能仅在新协调器末尾补一个监听器；接入时需要统一仲裁和外部层“已消费”协议，保持未接入单例可独立运行。
2. `ls_date_picker.js` 选日后用 140ms timer 调用全局 `closePopup(true)`，回调未绑定原面板实例。若期间打开另一个输入，旧回调可关新面板。登记时绑定实例/generation并取消旧timer；测试先选A再立即打开B并推进时钟，B必须保持打开。此处是源代码推导出的可达路径，本次没有做浏览器复现。

`open(el|html)` 的 HTML 分支也必须有受控来源：接受组件宏/安全构造器产物，不把URL、业务返回文本或用户富文本直接当层结构插入；用户富文本仍走既有安全renderer。

| 接入对象 | 现有入口 | S2 处理 | 兼容门禁 |
|---|---|---|---|
| 普通旧 modal | `ui.js:openModal(id)/closeModal(id)`；`window.UI` | 保留签名；内部转协调层；保留 `data-feedback-managed` 排除规则 | 打开/关闭同步调用可用；display/show类兼容；旧 backdrop/data-dismiss不双执行；嵌套不提前解锁背景 |
| 白板 / LessonDoc | `whiteboard/popover.js`、`lessondoc_editor/ui.js` | 扩展共用 seed、保留旧导出/调用；其页族外观接入留S6 | 各 prefix 不串栈；原 `onClose` 原因适配明确；旧定位/resize/preserveOnResize行为测试不丢 |
| 日期 | `ls_date_picker.js` | 给现有单例增加登记钩子，不复制解析/选择逻辑 | 父模态内可操作，Esc只关闭日期，随后才关父；移除父时清理日期监听 |
| 灯箱 | `ls_image_lightbox.js` / `ChatImagePreviewController` | 登记 viewer，保持现有变换/加载 token/指针清理 | 图片迟到不复活关闭层；缩放/拖拽/捏合/组切换保持；焦点与滚动锁正确 |
| 说明浮窗 | `ui_explanation.js` / `window.LanShareExplanation` | 仍是外部单例；协调器关闭顶层前优先关闭它 | 当前 API只有attach/close/open/register，没有isOpen。应补非破坏性查询/关闭结果或适配通知，不能复制私有 state；现有 capture Esc只消耗一次 |
| 原生 dialog | `assignment_detail_teacher.html:#assignment-kind-modal` | 原生 top-layer 登记适配 | cancel/Esc/default关闭仅一次，关闭后恢复原焦点；子popovers位于可交互宿主 |
| React Dialog | `components/ui/dialog.tsx` → classroom/dashboard workspace | `useLqLayer` 后台阶式替换；两真实消费者通过后再删 Radix 文件/依赖消费者 | controlled open变化、StrictMode双effect、DOM迁移恢复、返回旧编辑器、迟到退出动画、滚动位置 |
| 课表展开 | `course_schedule_deck.js` 的 `.cs-expand` | 仅外部层适配，受保护文件不改 | Esc顺序与焦点合理；关闭外层不能吞掉课表事件或重置画布/课次 |
| 页族私有层 | `poll-/collab-/ga-`、`materials-editor-shell`、`rz-`、`lde-`、日历todo | 登记未来迁移清单；不在S2全替换 | 后续各族接入才删除旧栈/锁；S2保证未迁页兼容 |

课表外部桥有一个尚未具备的入口：当前公共API没有`isExpanded/dismissTop`，而`course_schedule_deck.js`仍受保护。S2包开始前须明确允许的宿主登记/关闭协议；不能用模拟按钮点击、合成Esc或直接改hidden伪装成已有API，也不能未获范围授权就改保护文件。独立深审记录在`.codex-temp/lq-s2-layer-preflight.md`，其中核心所有权、竞态与销毁要求已纳入本文；临时报告不替代长期组件契约。

五种主要层复用一套所有权/生命周期，不各写独立 Esc 与 body overflow。Menu 仅用于操作命令，测试上下键/Home/End/Enter/Esc及禁用项；Popover 的非模态行为不应强制圈闭整个页面。Tooltip 不承载交互内容/功能说明，键盘即时显示且可关闭，已有说明系统继续负责富说明。

Confirm 是二态 Promise，Choose 显式返回选项或 `dismissed`；Esc/遮罩不会变成默认确认。先 preventDefault 再 await，确认期间防重复执行，危险操作初焦点在安全动作。`beforeClose` 应明确允许同步/异步否决、关闭中的第二次请求与父销毁的强制清理规则：普通关闭可被拒，宿主销毁必须清资源且不得执行业务“确认”。`?open=` 只登记允许的层标识，不直接插入URL内容。

核心层测试必须覆盖：modal→日期→说明→连续Esc、modal→viewer、modal→confirm、native dialog→popover、父销毁、有脏内容否决、触发器被删除、动画事件不触发、关闭后异步完成、反复20次开关后监听/锁计数归零。具体动作断言见第7节。

### 4.7 §10.7 Toast 与 live 状态

当前 `static/js/ui.js:showToast(msg,type='success',duration=3000)` 负责创建 `#toast-container`、截断/转义消息、定时关闭，`showMessage` 为别名；`window.UI` 暴露这些函数，当前直接全局绑定的是 `window.showMessage`。`resume_common.js` 有本地 toast；`exam_editor.html` 有私有容器及 `window.showToast` 绑定；`exam_take.html` 有自己的 `showMessage` 路径。这些是迁移登记点，不应假定已有唯一全局实现。

S2 将 canonical 实现归到 `LQ.toast(message,{tone,duration,action,icon})`，旧函数兼容转发并保留 error→danger、默认参数与纯文本安全。局部页面绑定只能在实际接入该页时删除，防止脚本顺序覆盖别名或独立文档缺入口。

门禁：最大3条、去重、hover和键盘焦点停留时暂停、关闭动画超时兜底、action仅执行一次；普通polite/危险assertive语义，不双播报；页面字段校验、SSE每次变化和后台完成仍由就地状态呈现。React 调同一 singleton，不另写 toast provider/队列。

### 4.8 §10.8 Badge / Avatar / Progress / Spinner / Skeleton

| 项目 | 现有消费 | 复用/新建范围 | 验收 |
|---|---|---|---|
| Badge / Dot | classroom activity count/total；AI队列徽标；文件上传 `.file-chip__badge` | 共享尺寸/隐藏0/状态文字，不接管计数源 | 0隐藏但不产生空名称；更新不过度live；数字变宽不挤掉主操作 |
| Avatar / Stack | `AvatarActionLink`、profile launcher、聊天头像 | 保留头像URL权限和加载；新增首字/hash6软色及stack呈现 | 图片失败fallback、姓名可访问、首字安全转义、最多4+N、实际点击目标 |
| Progress / Ring | `manage_insights.html` SVG；`assignment-authoring-sync` readiness；`exam_editor.html:#ai-task-progress` | 复用ring几何；进度值仍由controller给，未知进度不得伪造百分比 | determinate/indeterminate区分、value上下界、无持续逐帧播报、0与100标签 |
| Spinner | `assignment_detail_student.html` 提交中；`exam_editor.html` 任务加载 | 新共享16/20/32呈现，逐页替换 | 装饰spinner aria-hidden，关联按钮/状态保留名字；reduced-motion仍能理解等待 |
| Skeleton | 未发现可直接复用的通用共享实现 | 新纯呈现占位，不建立数据加载器 | 容器busy语义；不读出虚假内容；reduced-motion静止；加载完成无多余占位焦点 |

### 4.9 §10.9 业务组件

| 组件 | 实际入口/消费者 | 复用边界与最小适配 | 必须的状态/交互门禁 |
|---|---|---|---|
| SaveStatus | `lesson_plan_editor.js` 的 `#lp-save-state`；`assessment_plan_editor.js:#ap-save-state`；`teacher_evaluation_editor.js:#te-save-state`；assignment/exam保存提示；React submit-sync | `set(state,{time,action})` 仅呈现9态：dirty/local_saved/syncing/synced/offline/error/conflict/submitting/submitted；不重建请求队列 | 所有注册状态可呈现；冲突/错误action常显；syncing↔synced不反复播报；本地保存不声称服务器确认 |
| DeadlineClock | `assignment_time.js:initAssignmentClocks`；`assignment_detail_student.html`、`exam_take.html` 的 `data-assignment-clock` | 保留serverNow/countdownAt/startsAt、作业id、补交/迟交/可提交等全部data契约及服务器同步 | 绝对截止时间常显；倒计时不每秒live；时钟偏差/截止边界布局稳定；可提交权限不由客户端计时自行决定 |
| JobStatus | `ai_workspace_widget.js` SSE + poll fallback；试卷生成/导出任务 | job/agent状态与按钮呈现；连接、轮询、取消、superseded归属仍由controller | queued/retry_wait/running/result_ready/superseded/failed/canceled；Agent waiting_input等注册状态；旧任务迟到不能覆盖新任务；结果不自动应用 |
| QuestionNavigator | `submission-jump-nav.tsx`；`submission_detail.html` 原生fallback；`exam_take.html`题号导航 | 共用nav/button状态槽，原answered统计与目标id不改 | current/answered/flagged/error/pending-upload可组合；有文字/图形非仅色；44px粗指针；键盘定位后焦点可见；fallback/island不双处理 |
| EditorShell | `exam_editor.html`、`exam_take.html`、`lesson_plan_editor.html`、`assessment_plan_editor.html`、`teacher_evaluation_editor.html`；`partials/lq_editor_head.html` | 只建bar/rail/main/aside与responsive壳fixture；不移动实际表单/打印内容直到S5/S6 | 320可编辑宽；移动rail/aside入口；主保存可达；独立文档不加载站点bottomnav；error/dirty不折叠 |
| UploadQueue / Dropzone / FileChip | `submission_upload.js:SubmissionUploadManager`；`upload.js:ChunkedUploader`；assignment/exam模板草稿链 | 同一呈现模型适配两类已有传输controller，不能合并成新网络队列；保留`draftSyncChain`与`questionDraftUploadTimers` | selected/validating/rejected/uploading/uploaded/failed/removing；队列idle/busy/partial-failed；上传服务器确认后才uploaded；重复截图原因+题号；限制常显；失败重试/移除/粘贴/拖放 |
| Choose | 共用§10.6协调器；现有业务confirm调用是后续消费者 | 三态或≤3选项结果，不复用agent三级确认业务 | 取消/返回明确dismissed，连续点击只解决一次Promise；关闭不触发默认业务分支 |
| Split / Viewer | `material_render_shell.html/js`、三文档编辑器预览外壳、`process_material_editor_preview.js`；LessonDoc外壳 | 新共享分栏与工具条；保留历史导航、下载/新窗口流程及iframe内部文档 | 分隔条键盘±8px/aria值/minmax，拖动可释放；<1024上下/segment；工具条不遮iframe；用户纸张/课件不注入主题 |
| Lightbox | `ls_image_lightbox.js`、`chat_image_preview.js`、Markdown/附件声明式消费者 | 原单例换皮+viewer登记；保留`data-ls-lightbox*`、原始/预览/下载信息、`.ls-glass/.ls-glass-pill`过渡别名 | 声明/程序两路同分组；仅本消息图片组；缩放、拖拽、触控、多图/错误/迟到加载；clear+scrim的真实像素对比 |
| DirtyGuard | 三文档编辑器beforeunload；`exam_take.html`等 | 保留浏览器原生beforeunload；站内层关闭接beforeClose，不成为新的全站路由器 | 脏/已保存切换、确认离开/取消、父关闭；原生form提交不被误拦；关闭后移除监听 |
| ConflictNotice | `submission_grading.js`；assignment/exam的expected version路径 | 纯提示+“重新核对”动作；controller继续持有本地草稿/服务器版本 | 409保留输入、停止无效后续写入、手动核对、不自动覆盖/无限重试；焦点/错误summary可达 |
| Alert | 表单400/409/权限/约束、业务失败提示 | 常驻error/warning/info呈现；与Empty.error区别清楚 | 不定时消失；语义等级合适且不重复播报；动作有名，长文本/链接/手机换行 |

上传有一个需要显式映射的旧状态差异：`submission_upload.js` 当前文件徽标使用 `synced/syncing/failed`，其中 failed 文案为“待上传”，允许最终提交带上文件。不能只把该状态重命名成“永久失败”或把本地选择误标 uploaded；适配器应同时保留传输状态、可重试/提交语义和服务端确认结果。

`ui_explanation.js`、`prompt_pool.js`、`agent_user_confirmation.js`、`approval_workflow.js`、`SignaturePointControl` 的业务契约继续保持。尤其 Agent 确认的快照/勾选/备注/版本校验、签名 selected/confirmed 与申请/应用的互斥动作，不能降成通用 `LQ.confirm` 的一个布尔值。

## 5. §11 ShellContract 与布局准备

计划标题称“四种布局”，实际表格是四种站点壳加一种独立 editor。S2 应为五种情形建立 fixture/契约，不能只验 `base.html` 就宣称所有根已具备组件能力。

| 根/能力 | 当前实际路径 | S2 必须记录或验证的项目 |
|---|---|---|
| 管理sidebar | `manage/layout.html`，教师dashboard/profile | head资产顺序、导航service权限、当前域、sidebar存储、main/skiplink、island roots；不提前替换整个导航 |
| 学生topbar | `base_navbar.html` → `base.html`，消息/博客/个人页 | 原生details与React增强只绑定一次；导航schema共用呈现而不强塞teacher service |
| immersive | `base.html`派生classroom/material/监控 | `data-lq-lock-nav`、单实例活动面板、fullscreen、局部scope、正文与侧栏滚动边界 |
| centered | `base_centered.html` | 登录/异常/权限根组件依赖最小化，不能强依赖登录账户或完整站点导航 |
| editor | `partials/lq_editor_head.html` + 五独立html | 当前partial是S1主题/资源入口；S2按需补组件入口；独立title/lang/viewport、main/skiplink、层和toast宿主，不加载站点bottomnav |
| embedded / print / 用户内容 | 各embedded模板；预览/打印iframe；`resume/layout.html` | embedded只内容；主题桥仅app显式授权iframe；打印/用户纸张保持独立；resume整壳迁移先完成rz层接入 |

`partials/lq_head_assets.html` 和 topbar/sidebar/dock partial 是计划产物，并非当前已经存在的公共实现。引入时保持 S1 SSR属性→同步bootstrap→CSS→模块入口的先后关系，native经典脚本通过ready协议使用组件，不能新增一条晚于首屏的主题重算链。

布局验收覆盖列表、总台、详情、编辑器、作答、沉浸、阅读七骨架。Editor三栏在≥1280展开，1024–1279收aside、768–1023再收rail、<768单列；主编辑宽≥320。断点两侧都测，且错误/当前步骤/未保存内容始终可见或其入口明确可达。Collapsible 的存储key必须显式注入身份/页面/资源范围，并遵守旧分组存储兼容；不可沿用一个全局布尔值影响所有用户/页面。

## 6. 最小工作包与依赖顺序

| 工作包 | 产物与范围 | 前置 | 完成条件 / 不包含 |
|---|---|---|---|
| P0 契约与测试装配 | 更新`lq-components.md`的API/状态/实际消费者矩阵；确定viewer/外部层/close reason；测试fixture与runner入口；图标清单 | S1正式通过 | 能真正收集并运行`tests/lq/*.test.mjs`；真实Jinja fixture路径与React适配范围已定；不改业务页 |
| P1 基础呈现与安全构造 | index/ready/html/icon、Button/Chip/Badge/Avatar/Spinner/Skeleton/Progress、基础field和surface槽 | P0 | Jinja/Element/HTML/实际React语义、转义、tokens和状态门禁过；不新增请求队列 |
| P2 协调层与兼容桥 | 扩展ui_popover基础、复用ui_overlay_motion；LQ.layer、confirm/choose、toast、ui.js桥；日期/灯箱/原生dialog/ReactDialog适配 | P0；使用P1的按钮/状态 | 层链、焦点、锁、生命周期、旧API消费者回归通过；不批量迁私有overlay |
| P3 控件与内容 | Tabs/Segment/Collapsible、Menu选择区别、表单/error summary、Card/List/Table/Empty/PageHead/Insights/Prose/Bubble | P1；交互浮层依赖P2 | 状态/键盘/响应式通过；旧三宏默认输出与业务控制器保持原路径，准备S3适配 |
| P4 业务呈现 | Status/Clock/Job/NavGrid/Upload/Conflict/Alert/DirtyGuard、Split/Viewer、业务React薄适配 | P1–P3 | 用现有controller事件/快照fixture驱动；不重写版本/权限/同步/上传逻辑 |
| P5 壳与完整预览 | Topbar/Sidebar/Dock/FAB/Crumbs/Steps、EditorShell及七骨架fixture；扩展/dev/lq全组件状态 | P2–P4 | 5根、embedded、独立编辑场景与六套偏好可操作；预览不产生业务写入；无全站壳切换 |
| P6 出口与交接 | axe/keyboard/三入口/生命周期/截图/对比/lint/build/typecheck；记录消费者和回滚边界 | P1–P5 | S2出口证据完整；实际页族接入清单交S3，不按“组件看起来完成”跳过业务回归 |

P1与P2的内部开发可在P0接口冻结后并行；P3的纯内容宏可并行，依赖弹层的交互必须等待P2。共享入口、token新增、测试配置、package锁文件由单一集成者合并，避免两个工作包各建一个 `window.LQ` 或各添加一组 document Esc监听器。

S2不需要数据库迁移，也不把Python加入npm生产构建。需要真实Jinja输出时，独立测试准备命令调用现有Python/Jinja环境生成fixture，或测试直接由Python渲染；资产构建仍使用现有Node链。

## 7. 三入口、axe、键盘与回归门禁

### 7.1 测试装配的真实缺口

- `vite.config.ts` 当前只收集 `frontend/src/**/*.test.ts`、白板和LessonDoc的 `*.test.js`；不会自动执行计划的 `tests/lq/*.test.mjs`，也不会匹配新 `*.test.tsx`。P0必须显式纳入需要的模式/环境，并用一项故意失败的临时探针确认runner确实收集，随后删除探针。
- `tests/e2e/components/playwright.config.ts` 已提供无应用服务器/DB/API的 standalone fixtures；适合新组件、layer、shell与axe。现有 `tests/e2e/specs` 保留实际页面回归职责，不把静态fixture冒充真实业务成功。
- `package.json` 当前没有axe依赖/命令。S2添加一个明确版本的dev-only axe运行入口，例如 Playwright集成；不要仅在报告写“无错误”却未执行扫描。
- 真实Jinja宏渲染、原生Element工厂、HTML字符串解析结果、React适配器都须进入浏览器DOM后做语义断言。手抄一份“类似宏输出”的HTML不能证明三入口等价。

### 7.2 必须具备的测试组

| 测试组（建议文件） | 最少断言 | 适用范围 |
|---|---|---|
| `tests/lq/semantics.test.mjs` + 浏览器fixture | role/name/value、disabled/busy/invalid/selected/current、label/description/control关系、槽位、默认type；动态id归一化，允许合法wrapper/Portal | §10全部；React只对第3.2表实际需要的适配器，N/A必须登记 |
| `tests/lq/html.test.mjs` | 文本/属性注入、危险href、未知icon、attrs过滤、HTML/Element路径语义一致 | 所有`LQ.html.*`，不只按钮 |
| `tests/lq/lifecycle.test.mjs` | init两次只绑定一次、ready早/晚调用、destroy幂等、节点删除、StrictMode、重复open/close、计时器/observer清理 | 全部有行为组件 |
| `tests/e2e/components/lq-components.spec.ts` | 鼠标/触控/键盘状态，表单真实submit次数，chip-select、tabs焦点/内容保留、IME、error summary、combobox/listbox/menu分别验证 | §10.1–10.5、10.8–10.9 |
| `tests/e2e/components/lq-layers.spec.ts` | modal→date→explain每次Esc只关顶层并正确returnFocus；native top-layer子层可点；父销毁、dirty否决、confirm/choose返回、锁计数、无focusable兜底、动画超时与迟到异步 | §10.6、日期/灯箱/说明/React桥 |
| `tests/e2e/components/lq-shell.spec.ts` | 5根与7骨架；320/375/390、断点±1、200%zoom；正文无横溢/遮挡，matrix局部横滚；Dock软键盘/安全区；Editor独立根 | §11 |
| `tests/e2e/components/lq-a11y.spec.ts` | 每组件每关键状态及每活动层进行axe；零serious/critical，不用全页排除掩盖组件违规；记录moderate/minor并逐项处理 | 六套偏好、桌面/手机；动态打开/错误/加载状态也扫描 |
| computed/像素对比 | 六palette×亮暗，正文≥4.5、必要控件边界/焦点≥3；透明层合成真实底面；selected/hover/loading/error也测；off下无blur | 复用S1tokens/materials与探针；静态token对比不能替代玻璃背景像素证明 |
| 业务薄适配fixture | 已有快照/事件驱动status/job/upload/navgrid；服务端未确认不显示成功；409保留本地值；superseded不覆盖；不新增网络请求 | §10.9及React适配器 |

“六套偏好”应写成可复现组合，而不是把四个维度误当互斥选项：light+tinted、dark+tinted、light+off、dark+off、contrast-more、forced-colors；后两套至少复测亮暗文字方向。另测reduced-motion/reduced-transparency、粗指针和能力降级。六palette的完整对比矩阵与这六组截图互补，不能用一个默认palette截图替代。

键盘人工/自动遍历至少包括：Tab/Shift+Tab的自然顺序；focus-visible不被圆角/overflow裁切；button/anchor的Enter/Space差别；radio/range/select原生键盘；tabs/segment/menu/listbox不同方向键语义；Escape顶层顺序；表单错误定位；拖动分隔条的键盘替代；disabled解释入口；屏幕阅读器名称/状态/值。axe无法证明这些行为，也不能验证iOS滚动恢复，后者仍需计划指定的真实设备记录。

### 7.3 必须保留的现有回归入口

按实际改动选择，不无差别扩大S2页面迁移：

- 共用弹层seed：白板/LessonDoc现有 Vitest，包括 `static/js/lessondoc_editor/color_controls.test.js`、`frontend/src/lib/ui-overlay-motion.test.ts`；`tests/e2e/components/agent-user-confirmation.spec.ts`、`classroom-chat-escape.spec.ts`；`tests/e2e/specs/ui-explanation.spec.ts`。
- React/Dialog桥：`semester-calendar-dialog.spec.ts`、`dashboard-todo-modal.spec.ts`、`home-classroom-workspace.spec.ts`；`mount-react-island.test.ts`；保留当前DOM移动/返回流程而非仅截图。
- 宏/导航：`teacher-app-shell.spec.ts`、page-head相关现有断言；`classroom-members-tabs.spec.ts`。S2若只准备opt-in实现，旧默认输出仍应通过。
- Markdown/用户确认/签名：`ai-chat-markdown.spec.ts` 与既有确认/签名针对性门禁；只有桥接实际影响时扩展测试，不重写它们的业务实现。
- S1防退化：`tests/test_lq_tokens.py`、`test_lq_foundation.py`、`test_lq_guard_sources.py`、`frontend/src/lib/lq-theme.test.ts`、`tests/e2e/specs/lq-s1-theme.spec.ts`；保留off/首帧/SSR/偏好不写入预览/iframe边界。

S2集成时执行现有 `npm run typecheck`、`npm test`、`npm run build`、`npm run lint:lq`，加新组件Playwright入口；测试命令和实际收集数写到验收记录。**本次审计没有执行这些命令，也没有把待新增测试列为已通过。**

## 8. S2 出口与交给 S3 的内容

S2完成须同时满足：§10.1–10.9组件目录逐项有API/槽/状态/键盘/消费者；所有实际需要的三入口等价通过；兼容桥旧消费者通过；`/dev/lq`完整状态与六组偏好桌面/手机证据；指定层链、axe、键盘、typed色对、lint、构建/类型检查通过；未迁页没有因共享基础改变而退化。页面私有组件尚未删除不是S2失败，未登记消费者就删除才是风险。

交接S3时提供：旧三宏的兼容调用映射、五种ShellContract、实际React适配范围、层/单例消费者清单、每个组件的已测状态与例外、后续页面切换/回退开关位置。`lq-components.md`应承担长期组件真源，本文件保留为实施前审计依据。主计划的S2进度和正式验收由负责人更新。
