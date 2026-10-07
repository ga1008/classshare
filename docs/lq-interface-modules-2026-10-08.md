# 界面组件修复与全平台需求复核（2026-10-08）

本轮处理课堂课次导航空按钮、课程资料/材料浮窗的内容内边距和可读性、顶部更多/个人及首页日程工具菜单。修复位于共享 Button、Menu/NavMenu、Dialog、Surface 的合同和实际消费者，不创建页面私有玻璃配方。部署与 Git 推送分别验收；本文不把源码声明、扫描数量或历史结果视为本轮全平台业务通过。

## 需求与根因

| 用户场景 | 实际根因 | 共享合同与消费者 |
|---|---|---|
| 课堂课次上一课/下一课显示空胶囊 | 业务按钮仍在，图标/按钮呈现合同没有形成稳定可见内容 | 原生 Button 保留课次切换事件，使用规范图标槽与可访问名称 |
| 课程资料浮窗被下层文字干扰 | `course-popover-card` 被声明为普通 Surface，旧浮层有独立焦点/锁滚动/固定计时器，外壳与 shared raised 边界不一致 | 改用 shared Dialog 结构、raised Surface 与 `LQ.layer`，显式 portal，删除私有模糊、头部渐变和动效 |
| 课程资料内部板块内容贴边 | `course-popover-panel` 声明 Surface 后获得材质，但仅有 grid/gap 没有 inset | Surface 增加 opt-in `data-lq-padding`，stats/details 两面板 md、资料小卡 sm |
| 顶部材料浮窗内部贴边 | `.cw-dialog .cw-collection` 清零 padding 并试图透明；更高优先级的 Surface 材质恢复了背景，形成有底无内边距 | tasks/materials collection 声明 md，领域 CSS 删除材质/零 padding 覆盖，保留原节点搬运 |
| 更多/个人展开后文字穿透、必须点击 | 旧原生 details 带菜单外观但没有共享 Menu portal/悬停 owner，条目误套 Button 胶囊 | NavMenu + Menu 的原生条目和作者内容槽；精细指针悬停、触摸点击、键盘操作同 owner |
| 首页日程工具布局异常 | summary 与内部菜单继续使用旧局部结构/样式 | 改用同一 NavMenu 触发器/条目，不重写日程同步、订阅及说明业务 |

## 全平台组件需求和所有权

| 范围 | 已有共享能力 | 领域必须保留 |
|---|---|---|
| 教师/学生首页、课堂、管理、个人中心 | Button/Chip/ActionEntry；原生输入和 Selection；Surface/Card/Row；NavMenu/Menu/Popover；Dialog/Sheet/Drawer | 路由、授权角色、真实 URL、隐藏/禁用/忙碌状态及业务事件 |
| 任务/作业/考试、材料管理、审批、消息 | 同一 LQ props、Jinja 宏、JS 工厂/就地 adapter 与 React 薄适配器 | ID/name/type/form、IME、原生校验、幂等、CAS/ETag、409 草稿、413 上传恢复、异步代次 |
| 课表、白板、课件/文档编辑、简历、图表 | 工具按钮/菜单/外壳用 LQ；内容槽、数据色与几何 handle 明确登记 | 画布内容/颜色/命中区域、可导出文档本身、图表数据；不把内容强制变成按钮或玻璃 |
| 动态弹层、React portal、预览 iframe、后端生成 HTML | `LQ.layer` 是唯一焦点/滚动锁/层级 owner；正式资源图与主题引导共享 | 用户上传课件的隔离边界、预览权限、节点归位与唯一业务控制器 |
| 空/失败/无权限/窄屏/系统偏好 | Empty/Status/Alert、共享 responsive 和主题材质降级 | 清晰的状态文案、可重试动作、原生触摸与键盘路径 |

内容材质与内边距不能混为一谈：完整内容面板选择 sm/md/lg；表格/画布/分区壳可选择 none。旧有 `data-lq-component` 只是入口声明，只有规范 DOM、槽位、参数、实际 owner 和运行时证据同时吻合才构成组件接入。

## 本轮数据与业务边界

- 无数据库设计、迁移、API、授权或持久化变更；验证仅使用隔离合成运行目录。
- `classroom_page.js` 的统计、出勤、课程资料与考试安排业务读取保持；关闭课程浮窗不发写入请求。课程浮窗由 `getLayerSystem` 管理，返回更多菜单时焦点归还稳定的菜单触发器。异常出勤详情保持原有内联 sticky 布局，但注册为父层的 non-modal child；删除独立 document click/Escape owner，一次 Escape 只关闭最上层。
- `classroom-workspace.tsx::ExistingSurface` 继续搬运和归还原材料/任务节点，不克隆，不重绑材料选择/导航/下载事件。
- 导航菜单保留教师/学生条件分支与真实 href；领域按钮在菜单关闭成功后才执行一次原有动作；关闭否决时不执行业务。
- 共享 CSS 不给列表每行或每个小按钮增加实时模糊；raised 浮层只在边界采样。关闭透明、增加对比度、低能力及 forced-colors 使用已有降级。
- 共享材质回归另定位到 `.lq-glass` 误把每条 Toast 变成采样层，旧 `.card` 选择器也会绕过普通 Surface 的无模糊约定。统一边界现在明确禁止普通 content/control、Toast 与 Tooltip 实时模糊；raised 保持独立采样，不由领域覆盖。
- 模态遮罩关闭的焦点归还也在共享 Layer 修正：modal 的 outside 关闭归还原触发器；non-modal 的 outside 关闭继续保留用户刚点击目标的焦点。课堂消费者不安装另一份 focus owner。
- 手机课堂顶栏采用六等分布局，主操作自然分成两行（3+2），为 NavMenu 触发器保留完整可点击宽度。全局顶栏按内容宽度换行，窄屏 utility 独占一行，子项按本身宽度布局，不压缩112px NavMenu触发器。菜单 portal 中的消息节点更新仍由原消息控制器处理，移到 portal 后继续更新计数/文案。

## 全平台实际扫描补漏

12 个角色/宽度/明暗组合共 152 次 GET 是只读页面基线；覆盖 27 个路由候选中的可访问组合，不等于所有写入、权限和动态弹层业务均验收。初次扫描保留真实 unowned，人工区分后修复以下实际共享接入缺口：

| 发现 | 统一接入与保留边界 |
|---|---|
| 首页完整内容外面板有底无inset | 两模板的课程/领域/工具外面板显式接Surface；无内层Collapsible的fallback选择共享md，已有响应式Collapsible保留原内槽，不叠加padding |
| 教师首页五个领域卡仍用独立材质 | `dashboard_teacher.html` 的同一领域 article 模板接 Surface content/md，删除 ui-system 与 dashboard.css 的私有卡片底色、边框与 hover；链接、说明及领域数据不变 |
| 学期日历 family 关闭时退回旧面板与按钮 | `semester_calendar_panel.html` 无条件使用 Surface 与 Button；calendar family 继续决定既有业务增强，未强开；保留 compact、隐藏待办、新增、定位/回开头与原生 select owner；calendar.css 删除重复根材质及嵌入透明覆盖 |
| 个人设置/安全/邮箱字段只在 profile family 启用时归共享 | 三个 partial 的 Input/Textarea/Select 无条件接入共享类与声明，删除表单与 profile.css 私有材质/焦点配方。33 个 field opening tag 逐一比对，除 class/data-lq-component 外属性完全相同；id/name/type、readonly、autocomplete、required、maxlength/min/max 原样保留 |
| 消息筛选栏在旧分支没有内容模块 | 消息 partial 固定 Surface content/md，保留消息 island、筛选/搜索/标记已读 owner 和所有事件钩子 |
| 成绩单 pilot 关闭时四筛选链接及空态 CTA 仍用旧按钮 | 两个分支均接已有 Button 配方；保留 assessment_kind/class_offering_id 链接、aria-current、成绩展示与互斥 chart owner，未改变试点开关 |

纯 `app-topbar-menu` / `cw-topbar-menu` wrapper 只承担布局，真正 NavMenu/Menu 在内部或共享 portal，不能再声明第二个菜单 owner。课程标题/记录标题等原生内容链接继续保留链接语义；隔离 iframe 内课件自身的 card、按钮与主题选择属于文档内容，单独记录其边界。动态 HTML 与尚未实际触发的场景继续 pending，不能因扫描数下降改称“全平台完成率”。

## 源码审计的人工解释

`tools/ui/audit_lq_components.py` 只读扫描 Jinja、原生 JS、React 与后端生成 HTML，不导入应用或连接数据库。开始施工时的观察是 514 文件、198 模板、5334 候选入口；最终数量与逐文件 SHA 以本轮最终源码摘要为准。

冻结源码审计为 **518 文件、201 模板、5298 个候选入口**，其中 4537 pending、761 unknown。入口数量下降来自重复模板内容进入共享宏，不能理解为删除了业务功能或 unknown 已验收。完整记录见 [源码审计](lq-platform-component-audit-2026-10-08.md)、[源码及合同摘要](lq-platform-component-summary-2026-10-08.json) 与 [路由候选](lq-platform-browser-routes-2026-10-08.json)。

本轮逐类复核了没有显式归属的入口，不能直接把它们批量涂色或删除：

| 扫描分类 | 人工核查与处理 | 仍需的证据 |
|---|---|---|
| 动态 HTML sink（开始施工时 632 处） | 包含数据依赖的 `render*` 输出及拼接内容；静态扫描会同时计入模板里的具体控件，所以不是 632 个遗漏按钮 | 由对应领域场景产出运行时 DOM，确认权限、加载/空/错误/增量数据分支；保持 unknown |
| 工厂归属未知（20 处） | 集中在 `manage_gongwen.js` 与 `message_center.js`；人工确认其 `Promise.all(import(.../lq/...))` 命名空间调用，扫描器只解析静态命名 import | 保留 factory-ownership-unknown，不能据人工调用识别改写运行时验收状态 |
| approval_workflow 的裸字符串按钮/字段 | 构造后由局部 `adoptControls` 在明确渲染边界逐个接入 `adoptDomainControl`，不是全页面 MutationObserver；弹层已注册 LQ | 审批草稿/关闭否决/嵌套等原专项测试；本轮不据源码重标通过 |
| whiteboard/exam_board 的裸字符串控件 | buildDom 后明确采用 `adoptDomainControl`，原生 color 保留数据色；画布是内容槽 | 绘图/撤销/保存附图的专项运行时验证 |
| React 自定义组件 unknown | 包括 Context.Provider、业务岛根、ExistingSurface、对话框适配器与 IconActionButton；后者实际返回 LqButton | 检查 import 与实际消费，不把业务岛标签视作另造按钮；保留运行时待验 |
| `/dev/lq` 调试选择器、隐藏 file、fieldset | 部分原生演示控制器没有声明，另有原生结构/文件输入；它们不是全部生产页面缺陷 | 预览页自己的有理由例外或组件迁移，不影响本次用户截图对应入口的完成判断 |
| 普通页面中声明但槽位不完整的 Surface/Menu | 本次截图暴露的实际缺口，自动 canonical class 检查无法发现 | 本轮以真实页面检验背景采样、内容 inset、菜单条目与响应式几何 |

最终全平台报告继续保留 pending/unknown，不将其数字改称完成率。历史 2026-09-27 全平台运行记录和本次关键入口回归是两个时间点、两个证据范围。

## 验收清单与结果入口

本轮需同时验证以下路径，具体结果和输出由集成负责人在实际执行后补入，不以清单等同已通过：

1. 正式构建资源图：共享 CSS 和按需模块均来自当前不可变资产；类型、Jinja 语法、相关单元及源码审计通过。
2. 课堂桌面/窄屏：课次箭头可见可操作；课程资料 details/stats 首屏/长文本/空数据不贴边；材料目录、全选、返回/上一级与操作仍有效。
3. 顶部更多/个人与首页日程工具：鼠标悬停进入/离开、触摸点击、方向键/Escape/Tab；只有一份 menu/layer owner；不同菜单互斥，快速开关不遗留背景锁。
4. 焦点和模态：课程资料点击关闭、Esc、遮罩、连续重开；菜单入口打开课程资料后回到有效触发器；子弹层关闭不错误关闭父层。
5. 浅/深色与透明关闭，长文本和小屏无横溢；正文与状态可辨；实际可见 backdrop-filter 宿主数量不随条目数量增长。
6. 仅在隔离演练、构建和浏览器回归通过后执行既有发布流程；记录服务器健康/资源图一致与远端 Git 提交两项独立结果。

当前最终正式构建资源图：`c36eca4fb8a4c7d3c22bd2ecaface4d1e909372ce9086531ab508e31ad0d0676`，464 个资产；资源大小检查 `17776 / 18432`、`sourceHashesVerified=true`。

| 证据范围 | 已取得结果 | 边界 |
|---|---|---|
| 静态与合同 | classroom JS 语法、真实 Jinja 解析通过；lint:lq blocking 0（保留 3269 条旧范围 warning）；最终guard 12 / 12 与 diff --check 通过 | 源码声明不是运行验收，不自动提高迁移状态 |
| 新补漏 SSR | profile presentation 7、profile原生合同 9、navbar 12、report-card 5 通过 | 包含双方 family 分支、calendar compact/隐藏业务、33字段属性保留、筛选 URL/当前状态与原数据投影 |
| 共享 JS/浏览器组件 | LQ 单元172、类型检查；Layer44、声明式/前景配对/材质22、S8 7、长菜单12通过 | 在阶段正式构建验证共享合同；长菜单等待真实 transform settle，不以固定延时猜测 |
| 消息真实 React owner | portal 未读节点遗漏已通过共享 DOM helper 修复；React生命周期5项、真实菜单路径6项通过 | React 与 legacy共用节点查找；保持两个业务owner互斥 |
| 全平台只读基线 | 12矩阵、152 GET：route gap 0、JS error 0、leaf blur 0、invalid 0，记录2次源码变化与2条materials 3px溢出 | 发现实际unowned与materials顶栏3px溢出，已按上表修复并由共享topbar wrap收敛；受影响页面在下列64 GET与最后视觉例中复验；GET不是所有业务闭环验收 |
| 后端隔离 | 最终 `test_lq*.py` 223 / 223 通过（25.339s）；最终两首页inset再次定向SSR 12通过（1.900s） | dotenv阻断，仅合成SQLite，禁止PostgreSQL；完整模板审查与registry SHA同步 |
| 受影响页面复扫 | 774bc图8矩阵64 GET：错误、横溢、leaf blur、invalid、source drift均0；64条unowned均为已解释的app/cw-topbar几何壳 | 真实字段、日历、领域卡和报告卡缺口消失；截图另外发现首页边距与手机顶栏相交，最后c36eca图定向验证已修复 |
| 本轮页面关键路径 | 原28个去重项目例在各阶段均取得通过：课堂资料/材料、菜单悬停/键盘/触摸、菜单到dialog焦点、子层Escape、颜色与透明偏好；最后c36eca图8 / 8通过（1.5分钟） | 最后8项含新增师生×桌面/触控4项目例，遍历320/390/700宽，另4项日程菜单/订阅/API/日历业务；每项afterEach核对严格c36eca资源图 |
| 最终视觉复看 | 可见顶栏无相交/越界；teacher课程区与领域卡padding至少12px；师生profile字段截图正常 | 同时检查元素相交与外部横溢，页宽正常本身不能证明控件不重叠 |
| 发布 | 待集成负责人完成既有部署演练、服务器资源图/健康及远端Git确认 | 服务器上线与Git推送分开记录 |

探索阶段的Toast额外blur、遮罩关闭焦点和React菜单未读节点遗漏均已定位到共享owner并修复回归；初始记录保留，后续通过不改写最初失败。

本地完整证据保留于 `.codex-temp/lq-ui-20261008-*`：平台初始基线为 `lq-ui-20261008-platform/measurement-summary.json`，最后关键路径为 `lq-ui-20261008-dashboard-final/playwright-results.json`（8 / 8），受影响复扫为 `lq-ui-20261008-platform-final/measurement-summary.json`。不将全部临时日志或合成数据库提交 Git。
