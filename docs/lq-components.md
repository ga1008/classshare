# LanShare Glass 组件实施记录

执行真源：[liquid-glass-execution-plan-2026-09.md](liquid-glass-execution-plan-2026-09.md) §8–§10。**S2 本地工程出口已收口：审计缺项补齐后的统一623项浏览器、632项前端单元、112项隔离LQ后端及类型检查通过**。正式入口实测17,309/18,432 gzip字节。S3已按[试点边界](lq-s3-preflight.md)开始实施；正式签字、实机与发布仍单列。下文早期分时记录保留为历史，以文末最新出口记录为准。

最新出口详见 [验收台账的S2本地工程出口](lq-acceptance.md#s2-本地工程出口2026-09-20-2240取代以上待办状态)：最终CSS `b890400a…`，正式图`f107d4d2…`；全套623项已覆盖本文最后的规格补齐，截图/实页不同资源图版本与已知宿主限制均在台账明确区分。进入S3后修改页面的验证不得反写成S2历史证据。

| 对象 | 当前状态 | 验收要求 / 证据 |
|---|---|---|
| 令牌、语义状态色、四种材质 | S1本地工程通过 | 763 typed tokens、222 computed、六配色亮暗、四材质/降级、偏好SQLite/PG；见lq-acceptance |
| 首批呈现组件 | Button/Chip/Badge/Avatar/Spinner/Progress/Skeleton 本地门禁通过 | 79有效/38非法输入三入口；24次整页axe；实际业务消费者迁移仍在后续阶段 |
| 原生表单组件 | 独立包通过，已接全局 props 与按需入口 | 16个真实宏测试、26浏览器、24次六配色亮暗/两宽axe；控件保留原生值与校验 |
| 导航、折叠、菜单与提示 | 独立包通过，已接共享入口与CSS | Tabs/Segment 18、Collapsible 14、Menu/Tooltip 32浏览器；原筛选select真值保留 |
| 内容组件 | Card/List/Row/Empty/PageHead/FilterBar/Prose/Bubble通过 | 10 Python、19浏览器、24次六配色亮暗/两宽axe；旧三宏默认输出未切换 |
| 业务呈现 | Status/SaveStatus/Alert/Conflict、Clock/Job/QuestionNavigator、Upload已通过 | 原控制器继续持有时钟、任务请求、上传队列与版本；组件只消费已确认快照 |
| 统计、工作区与壳 | AvatarStack/Insight、Split/Viewer、导航壳/EditorShell/七骨架已通过 | 正式CSS下20、44、38浏览器；iframe/表单原位、移动操作可达 |
| `LQ.layer` 及兼容桥 | 核心、旧桥、React、分类、课表与Toast包通过 | 含Toast伴随根的129项组合浏览器回归通过；非新建第二个层栈 |
| Modal / Sheet / Drawer / Popover / Confirm / Choose | 独立包通过，已接CSS/global/懒入口 | 20浏览器、21单元；现有Node原位归还，选择在退出后结算，取消不执行分支 |
| `/dev/lq` | S2全组件及七骨架、居中壳已接入预览 | 默认404；开启后仅有效教师可读；本地交互、草稿保留、六配色矩阵及亮暗两宽实页验证，证据版本见最新出口 |
| 现有 centered 页面样式 | 仅完成等价抽出 | `static/css/lq/pages/centered.css` 是旧样式的搬迁，不是新的 lq 组件；8模板×2视口 computed style 等价 |
| 评分并发提示 | S0 业务门禁 | 继续使用原视觉；`submission_grading.js` 管理 busy/error/conflict/review；S5再接入标准组件 |

新组件实施时逐项补充：语义标签与槽位、状态表、键盘/触控行为、色对及降级、Jinja/JS/React 示例、对应测试、消费者与删除条件。基础组件未验收前不推进页面换皮。

## S2 P0 冻结契约

实施拆分与真实消费者见 [S2预审](lq-s2-preflight.md)。以下契约是当前工作包的共同输入，完成列只依据实际测试更新。

### 呈现与构造

| 组件 | 规范参数 / 默认值 | 输出约束 |
|---|---|---|
| Button | label='', variant='soft', size='md', icon=null, href=null, id=null, attrs={}, badge=null, loading=false, type='button', disabled=false, ariaDisabled=false | `button`或真实`a`；`lq-btn/__icon/__label/__badge`；变体prominent/glass/soft/ghost/destructive/link；sm/md/lg；icon-only必须有名称；loading不隐藏原名称 |
| Chip | label, kind='status', tone='neutral', size='md', pressed=false, removable=false, removeLabel=null, id=null, attrs={}, disabled=false | filter为button/aria-pressed；status为静态span，无live；tag移除按钮与标签并列、单独命名 |
| Badge | value=null, tone='neutral', dot=false, label=null, attrs={} | 数值0不渲染；dot须有可访问名称 |
| Avatar | name, src=null, size=40, attrs={} | 尺寸24/32/40/56；首字回退，Unicode码点hash `h=(h*31+point)>>>0`、bucket=h%6 |
| Spinner | size='md', attrs={} | sm16/md20/lg32；始终aria-hidden，不替代loading可访问名称 |
| Progress | label, value=null, max=100, attrs={}, variant='bar' | 默认原生progress；ring为独立progressbar+装饰SVG；未知value省略now且不造百分比 |
| Skeleton | shape='text', lines=1, attrs={} | text/avatar/block；text最多8行，其他只1块；装饰性aria-hidden，不伪造名称/进度；业务容器自行管理busy |

Jinja使用 `lq_btn/lq_chip/lq_badge/lq_avatar/lq_spinner/lq_progress`，Python参数用snake_case；JS使用camelCase。Jinja真实宏负责DOM，`lq_props(kind, **kwargs)`只规范化普通数据。Element与`LQ.html.*`字符串构造使用相同语义；React只为实际消费者实现薄包装。

文本/属性一律转义，连输入的Markup对象也视为普通文本；禁止任意HTML/SVG旁路。attrs只允许明确属性及必要aria-/data-，拒绝事件字符串、style和未知结构；组件定义的role/type/状态优先，透传不能破坏语义。URL允许http/https/mailto/tel或无scheme相对链接，拒绝控制字符、反斜杠、网络路径及未知协议。确禁用button在SSR即native disabled；aria-disabled解释入口由统一增强器阻止键鼠执行，不能仅设pointer-events:none。

P1 tone先支持primary/success/warning/danger/info/neutral，均复用S1色对。P4业务状态仍输出注册名data-tone，并经统一映射提供data-tone-level；不创建第二套状态机。Avatar六桶亦复用这六套soft/fg色对。默认字体/材质仅作用lq组件，保留旧页S1兼容边界。

图标由 `tools/ui/generate_lq_icons.mjs` 从现有安装的Lucide有限allowlist生成 `icons.generated.js` / `icons.generated.html`，不手抄第二套路经。宏`lq_icon(name)`和原生工厂共用37图标/8别名登记；未知名回退问号圆圈。SVG统一currentColor、aria-hidden、focusable=false；按钮提供名称。生成器`--check`验证源码未过时。

### 协调层与所有权

`getLayerSystem(document)`返回Document级Symbol单例，原生ESM/哈希URL/React使用同一实例。`open(Element, options)`同步返回handle；核心不自行解析HTML。`type`支持modal/sheet/drawer/popover/menu/viewer。options保存owner/root/trigger/modality/parentLayer/returnFocus，surface指定内部活动面板、默认root。

- `close(handle,reason)`返回`Promise<boolean>`；checking/closing复用同一请求。beforeClose否决或失败时保留DOM、栈、焦点、锁。closeAll逆序快照、遇否决停止，不以while重试。
- 状态opening/open/checking/closing/closed/destroyed；父销毁强制清理子孙。handle.update只更新选项，不重复入栈；destroy幂等强制回收，不当作用户关闭。
- 回调为beforeClose(reason,handle)、onCloseRequested（接受关闭后）、onClose（退场完成一次）、onDestroy（强制清理独立事件）。onReturnFocus(event,handle)允许preventDefault；returnFocus可Element/false/函数。
- `getPortalHost({trigger,parentLayer})`返回活动原生dialog内合法宿主或中央`#lq-layers`；已有连通DOM默认不搬，React先取宿主再Portal。不能靠更高z-index越过native top-layer。
- 焦点、inert/fallback、滚动锁均由协调器持有；popover/menu不全局锁滚。模态背景恢复原属性/样式；关闭动画复用ui_overlay_motion并有取消/超时，不等待无限子动画。
- 保留createPopoverSystem旧签名与白板/LessonDoc行为；ui.js、日期、灯箱、说明、原生dialog、React桥在核心门禁后逐个接入。课表外部登记必须有明确可移植接口，不能假点击或合成Escape，也不能覆盖开始时用户改动。

React桥完全退出Radix行为后才登记LQ，禁止双重焦点/锁所有权。仅两实际Dialog消费者迁移通过后删除直接依赖。共享bootstrap Promise、显式island dispose与迟到回调代次是桥接的必要生命周期支持；不借此重写业务请求或native controller。

### 测试装配与证据边界

`npm run test:lq`实际收集`tests/lq/**/*.test.mjs`；主Vitest也收集新tsx。临时故意失败探针已实际失败后删除，日志`.codex-temp/lq-s2-collection-negative.log`，不是只看配置断言“会执行”。`npm run test:e2e:lq`运行独立LQ组件fixture，不连接应用/DB；实际业务浏览器另排期串行登录合成账号。

无障碍依赖固定为dev-only `@axe-core/playwright@4.13.0`，按[官方用法](https://github.com/dequelabs/axe-core-npm/blob/develop/packages/playwright/README.md)运行实际扫描。尚未执行扫描时不记为axe通过；不得关闭颜色规则或全页exclude掩盖问题。axe与键盘/触控/焦点/关闭竞态、Jinja/Element/HTML/React语义对比是不同门禁。

S1检查点：`.codex-temp/lq-s1-engineering-checkpoint/manifest.json`，220个源码文件/删除记录、8,405,117 bytes，保留原用户改动及S0/S1变更；包含正式资源图和Vite manifest。它不是业务库备份，也不是已发布版本。

## S2 首批已完成门禁（2026-09-20）

- 呈现组件：`.codex-temp/lq-s2-p1-presentation-report.md` 和 `lq-s2-p1-final-evidence.json`。真实Jinja纯helper、HTML解析、Element树的79个有效案例一致，38个非法输入均拒绝；21个浏览器测试通过，含三入口键鼠禁用、不隐式提交、18对loading宽度、头像失败回退、coarse 44px、reduced/forced。真实六palette×亮暗×两宽共24次整页axe零违规，未禁用规则。每次扫描验证实际computed token与按钮色，防止未知palette回退冒充覆盖。
- 该呈现扫描使用实际 `tailwind-app.css`，SHA256 `20a4607e634c42a14a50fad738cdeff0c9391d330166169feae9a359c7ab6538`。它是S2阶段的CSS构建，不是完整发布资源图。普通应用surface通过不代表任意照片/Clear宿主通过。
- 共享入口：`partials/lq_ready_core.js` 同步队列先于页面脚本；`lq/index.js` 提供同一Document上的ESM/global API。`lq-runtime.spec.ts` 2项通过：加载前/后/保留ready引用/重复bootstrap/不同URL只安装一次；DOMContentLoaded前不提前ready；一个回调失败不吞掉其他回调；Skeleton 1.6s且reduced/forced静止。
- 宏和隔离门禁：28个首批宏测试；Skeleton另2项；追加宏/预览partial的lint覆盖回归3项通过。测试均使用 `tools/test_backend.py`，在应用import前阻断dotenv、禁止PG且创建独立SQLite运行目录。此前47项LQ基础/宏组合通过；后续新增项不冒充同次全跑。
- 源码守卫已覆盖 `templates/macros/lq/**/*.html` 与拆分的预览partial；当前30个active files、0 blocking。旧页warning仍保留，未将未迁页当作已完成。
- 壳JS体积按真实静态依赖闭包逐文件gzip求和，包含layer/共享motion/icon数据，不能只数index文件。`npm run check:lq-size` 执行18KiB门禁；geometry从旧popover原样抽离并保留re-export后，当前17,729 bytes。Lucide完整ISC/MIT许可随静态包保存，生成器验证三份输出。后续非首屏组件必须按需加载，不能不断扩大同步入口。
- `/dev/lq` 已加首批真实宏与动态工厂的本地演示。默认404、有效教师限定、no-store与零偏好写入的原门禁仍通过。完整S2预览和业务路由截图尚待后续集成。

上述首批17,729 bytes与2项runtime结果是当时快照。后续ordered协调和懒确认接口曾使未打印源码超预算；现已接入保留语义的原生模块打印和实际产物门禁，见文末。完整S2出口仍须在所有源码冻结后重测。

## S2 后续已完成工作包（2026-09-20 19:35 后）

### 原生表单

`LQ.load('forms')` 加载显式增强器，导入不扫描旧页。`input/textarea/nativeSelect/checkbox/radio/range/switchControl` 均返回含可见label、help/error关系的完整Field；`formSection`是fieldset/legend，`formActions`提供作者内容槽，`errorSummary`提供错误定位，`focusFirstError`由业务显式调用。通用工厂`createForm/formMarkup`和`html`对应同一纯树。Python/Jinja通过`classroom_app/lq.py`派发到`lq_form_props`，避免导入应用状态。

所有控件要求唯一id和可见label；保留name/form/value/required/disabled。readonly仅用于支持它的Input/Textarea，Radio必须有组name。清除按钮一次input/change；IME不触发隐式提交；字数遵循原生UTF-16 maxlength；autoGrow仅增强呈现。`enhanceForms(root)`按根引用计数，返回refresh/dispose，释放observer/listener/借用样式。原生select是默认实现，未用操作菜单代替；Combobox/Listbox已在后续工作包完成，见下文。

验证：`.codex-temp/lq-s2-forms-report.md`、`lq-s2-forms-evidence.json`，16 Python真宏；26 browser通过，24有效/29非法在真Jinja、HTML、Element一致；六真实palette×亮暗×两宽24次完整axe零违规。此次扫描使用真实基础CSS加组件源CSS，最终统一构建后仍需整体验收。

### 协调器、桥与外部层

`ui.js`同步旧modal接口委托共享core；日期保留原生输入值/配对/min/max和一次事件，灯箱保留loadToken/图片组/缩放/拖拽。说明浮窗通过外部登记保留自身controller，优先处理Esc、新owned层打开时被supersede。实际product CSS现在消费`--lq-layer-order`，日期旧高z值被包在独立定位root内，`#lq-layers`本身不新建隔离堆叠。测试不再用fixture私有z-index修正模拟产品。

`registerExternal`新增`mode:'ordered'`供portable课表按真实打开顺序协调；默认说明模式保持。外部层显式提供isOpen/isActive/isPresent/root/trigger/dismissTop，返回refresh/isTop/destroy。关闭动画仍占位；owned child先退出，再回到课表；dirty veto保留父子链。课表继续拥有focus/Tab/动画，不增加LQ import、不靠MutationObserver猜内部状态、不合成点击。三宿主只接`connectScheduleLayer(deck)`。

`assignment-kind-modal`以native dialog登记，保存中beforeClose否决关闭，CAS冲突保留重选流程，迟到读取不抢子层焦点。React Dialog已经替换Radix行为，保留实际6个导出、可取消焦点事件、controlled close否决恢复、ExistingSurface/CalendarHost真实节点与草稿；旧编辑器与todo交接等待退出及DOM归位。StrictMode挂载登记和bootstrap readiness共享Promise，迟到回调使用代次保护。无代码引用后已移除直接依赖`@radix-ui/react-dialog`，随后typecheck通过。

完整证据：`.codex-temp/lq-s2-native-schedule-report.md`、`lq-s2-ordered-final.log`。**105/105 browser，1.6m**：44 core/旧桥/chat +19 React +7原课表 +5原生分类 +10新课表桥 +20 dialogs。同包110相关单测、typecheck通过。原课表`DECK_CSS`23,155字符完全不变；原课表测试完整文件SHA未变，实际收集7项（纠正此前口述数量）。这些是合成请求的真实浏览器行为门禁，不代表真实业务页全链路、真机或生产发布。

### 四种弹层与选择结果

`LQ.load('dialogs')`提供`createDialog/dialogMarkup/openDialog/disposeDialog`。`openDialog`只复用core；DOM body/footer可传同Document的既有Node，退出后按parent+next归位，外部已自行搬走的Node不夺回，原parent已脱离页面不重新挂回body。HTML与Jinja只接受安全纯文本。Jinja宏`lq_dialog`使用纯global `lq_dialog_props`。

`LQ.confirm({title,message,confirmLabel,cancelLabel,danger},layerOptions)`返回boolean Promise；`LQ.choose({title,message,choices,cancelLabel},layerOptions)`返回`{status:'chosen',value}`或`{status:'dismissed'}`。最多3个唯一选项，默认焦点在取消/返回。否决不结算，重复点击只执行一次；成功退出后才解决Promise，父毁得到取消。全局懒Promise有只读handle（加载前null）和destroy；下载完成前取消不会突然打开。直接ESM也使用同一core。

独立20项browser+21unit通过；390长文四结构、8桌面尺寸、Node/Fragment归位、20轮无剩余监听/observer/timer/锁；亮暗手机axe serious/critical=0，6张原PNG已逐张审阅。日志与哈希见`.codex-temp/lq-s2-dialogs-report.md`。后续全局runtime **3/3**通过，新增懒加载前/后取消、无迟到弹窗与非法参数拒绝，日志`lq-s2-lazy-dialog-runtime.log`。

### 当前集成状态与剩余出口

`/dev/lq`已加入表单、错误保值、视图草稿、四类弹层、嵌套确认和三项选择的本地演示；展示控件不写服务器。73项LQ后端组合测试通过（`lq-s2-backend-targeted.log`，新鲜SQLite、dotenv与PG前置阻断），包含实际预览模板200/403/404/no-store及零偏好写入；尚未重新启动合成服务并完成S2应用截图。

源码守卫当前46 active files、0 blocking、3743旧页warning。native-confirm修正规则区分组件函数声明/`LQ.confirm`与真实bare/window/globalThis/self调用，声明同一行函数体中的原生调用仍阻断；没有为新组件豁免整个文件。

本节以上为19:35后的阶段记录，Toast/导航/折叠/首批内容和状态组件的后续结果见下文。五种ShellContract/七骨架预览和完整S2出口仍未完成。S1正式签认/真实设备及本地测试误触数据库恢复边界继续单列，不能据工作包结果勾选S2完整出口。

## S2 后续集成与生产产物门禁（2026-09-20）

### 导航、折叠与通知

Tabs/Segment共享`navigationProps/navigationMarkup/createNavigation`和显式`tabs/segment(root,options)`。真实面板不重挂，手动激活、RTL/竖排、禁用后refresh、全禁用、hash/history、显式身份/资源持久化、强制色与横滚thumb均经过验证；Node槽先全量检查再移动。最终18浏览器加1稀疏数组单测通过，12次六palette亮暗axe；`.codex-temp/lq-s2-navigation-review-report.md`。

Collapsible使用原生details/summary；responsive在768px切换，dirty/error/current/keepOpen保护内容可见。存储必须显式提供identity/resource/key；`enhanceCollapsible`提供setOpen/refresh/destroy。原`manage_filter_chips.js`继续代理select，保留没有对应chip的合法值；更多/横滚仅显式开启。14浏览器、6 Python通过，见`lq-s2-collapsible-report.md`。

Toast的直接ESM接口同步返回handle，`LQ.toast`按需下载后返回Promise<handle>。Document singleton最大3条、去重、hover/focus/visibility暂停、异步操作代次和失败常显；通知动作在live区外。`registerCompanion`仅负责合法native宿主/inside/inert豁免，不入Esc层栈、不加锁、不自动聚焦。旧`showToast/showMessage`保持undefined返回及error→danger。129项组合浏览器、61相关单元通过（含此前105项和24项Toast，不重复累加为234项）；20轮DOM/监听/计时资源归零。见`lq-s2-toast-report.md`。

全局`LQ.tabs/segment/collapsible`是可取消的懒Promise，具有handle/destroy；加载前取消或root已被移除会返回null，直接ESM控制器仍同步。最新runtime 3项通过，同时验证同一导航owner、通知singleton和同步`LQ.tone`，见`lq-s2-lazy-runtime-integrated.log`。

### 内容、菜单与保存提示

`LQ.load('content')`提供8种纯呈现树/HTML/Node构造；Card主动作是标题原生控件，次操作并列。新PageHead/FilterBar保留旧签名和钩子，支持具名caller与旧零参caller；旧管理宏尚未迁移。Stat真值0保留，数值要求安全整数或调用者已格式化文本。Prose只排版既有安全渲染后的内容，Bubble时间在触控常显；outgoing时间改用已有ink-2以满足实际配色对比。23有效/25非法三入口、10 Python、19浏览器、24次整页axe零违规；见`lq-s2-content-report.md`。

`LQ.load('menus')`的`bindMenu`复用core，命令等待关闭成功后才执行`onAction(id,itemElement)`，第二参是HTMLElement；链接保留原生导航和修饰键，不能被异步beforeClose阻止。禁用menuitem可聚焦但不能执行；Tab、方向、typeahead、IME、父宿主与关闭竞态均验收。`LQ.load('tooltips')`显式绑定图标名称提示，鼠标400ms、键盘即时、不抢焦点；说明仍用data-explain。32浏览器、19相关单元通过。Chrome Ctrl+新页route拦截限制及替代证据准确记录于`lq-s2-menu-tooltip-report.md`，不声称该组合已完成真实新页内容加载。

`LQ.tone(family,state)`同步读取由现有CSS语义别名生成的9族62状态；未知值返回neutral/known=false，不生成任意属性名，不另存颜色表。生成器check、3 Python和3 JS通过。

`LQ.load('status')`提供status/save_status/alert/conflict纯树及`saveStatus(root).set(state,options)`。保存九态区分本机留存和服务端确认，error/conflict必须有可见核对动作；未知状态不能沿用成功文字。视觉区与polite live区分开，仅跨quiet/warning/danger组更新播报文本，syncing↔synced保持静默；动作不在live区。set先验证再更新，保留动作监听/焦点与调用者输入，常驻提示不计时消失。24有效/24非法三入口、19浏览器、3 JS、原真实评分409门禁通过；最终浏览器只用统一CSS，SHA `8740d680d14406f765b0267690fea362ae064bed9e6d110343edd8aebf585b39`。验证的是ARIA/live DOM变化，未宣称实际读屏结果；见`lq-s2-status-report.md`。

### 有消费者的React薄适配

`components/lq-presentation.tsx`直接消费原生`componentTree`和生成Lucide节点，复用校验/结构，不建立全局增强器。`action-entry.tsx`保留现有博客链接、反馈按钮、头像链接API、href、回调和data-open-feedback；原生表单type、ARIA禁用与busy一致。旧头像/图标样式的白底通过限定已迁节点的`react-bridges.css`配对修复，保留旧钩子与尺寸；头像失败显示居中首字，src更换可恢复。兼容nativeProps不能覆盖组件拥有的名称/disabled/busy语义。

32项组合浏览器通过：原呈现21 + React 8 + runtime 3；React含真实Jinja/HTML/Element语义、非法输入、原生提交、旧回调、重渲染焦点/宽度、20轮StrictMode挂卸载、44px粗指针和强制色。六palette×亮暗×两宽24次axe，截图发现并修复白底覆盖后再次检查。实际隔离应用中的教师批改页与学生作业页，头像URL/个人中心href、React挂载及原反馈打开/Esc关闭各1项通过，无业务写入；预览接线另行测试。日志`lq-s2-react-presentation-final.log`、`lq-s2-actual-content-status-first.log`（该日志同时保留后来修复的菜单预览失败，不当作4项全绿）。

### 构建和剩余范围

构建仅处理`js/lq/*.js`、共享motion/geometry：Rolldown 1.0.3、compress=false、保留合法注释、不合包、不删代码。初版仅打印；补齐ring后为保持18KB预算，改为 `mangle:{toplevel:false,keepNames:true}`，只缩短内部局部绑定，保留模块顶层、导出、对象属性、函数和类的name。先完成URL重写，再打印；recipeHash绑定构建器、打印器与编译器版本。manifest保留schema1并增加sourceHashes/recipeHash。`npm run check:lq-size`验证完整源码/配方指纹、当前不可变图和实际gzip sidecar的内容；缺失/过期会失败，源码估算不作为通过。10项构建门禁包括闭包隔离、shorthand属性、反射名称、未使用代码保留、URL/旧图与失效检测。

9个打印/预算Node门禁、11个原静态图Python、1个实际delivery浏览器及typecheck通过。完整build通过；预览修复后的阶段图`fe0b771511b7eb9e99f1d6c6d9700e437628def01b8be03b116fa0f6f12cce42`实测11个入口依赖合计 **17,973/18,432 gzip字节**，逐响应相加。后续并行源文件变化会使这份图过期；最终阶段必须重新构建核对，不能复用此数字签完整S2。日志`lq-s2-production-size-preview-menu-fix.log`与`lq-s2-static-print-report.md`。

尚待完成：AvatarStack/Insight基元、Clock/Job/QuestionNavigator、Upload/Dropzone/FileChip、Split/Viewer、导航壳/EditorShell/七骨架、全状态预览与全部出口回归。现有私有组件后续迁移和删除仍以实际消费者为门禁；本地数据库事故的恢复边界依然待确认。

### 表格、选值和离开保护（20:53 阶段记录）

Table/Pager/BulkBar/ResultCount已冻结并接统一dispatcher、lazy与CSS入口。记录模式<=768转卡片仍保留真实AX row/columnheader/cell及headers关系；矩阵只局部横滚。enhanceTable默认只反映控制器DOM值，显式native模式才变更可操作的行，disabled选中项保留；销毁中止后续批量写入。Pager最多7控件，安全整数/0和未知分开。10 Python及26个唯一browser用例实际通过，24次六palette亮暗两宽全页axe0；最后变化采取定向复验，未声称末轮一遍完整26项。见`lq-s2-tables-report.md`。

Combobox/Listbox的SSR是可独立工作的原生select，bindSelection显式增强；原生select仍为name/value/required/FormData唯一载体。异步query票据与代次阻止旧候选回写，不自行请求；销毁恢复原节点/标签/默认值。31 browser、18unit与四张390原图实审通过。原生reset的默认动作之后用下一task刷新代理；初始空候选的同同步栈reset→FormData边界详见`lq-s2-selection-report.md`，不能改写为无限制同步保证。

DirtyGuard只读页面isDirty，提供requestLeave/beforeClose/refresh/destroy；input/change或refresh使迟到确认失效，重复请求只有一个确认，destroy结算为不离开。默认不代理链接，显式navigation才处理同源普通链接，表单不拦截/不重发。原生Navigation sourceElement支持时避免表单的二次卸载提醒；不支持时保留浏览器beforeunload安全退化，不承诺所有旧浏览器无提醒。12 browser通过，含原生GET/POST、点击/requestSubmit/submit、新窗口、多owner、20轮监听归零；`lq-s2-dirty-guard-report.md`。

最新完整Vitest为 **67文件/600项**（20:32，`lq-s2-vitest-integrated.log`），新增并行包在其后另测，不能把该结果称为最终所有源码全套。规范隔离runner `test_lq*.py` **96项/1.366秒**通过（20:40阶段快照）；表格/选值预览接线后另有foundation8项通过。之前修复菜单接线的真实应用预览2项通过，亮暗1440/390四幅长图的contact和原尺寸切片已逐幅查看，0新增整页横溢；`lq-s2-actual-content-status-fixed.log`，不含后来新增的表格/选值预览。本地事故仍未关闭。

### 上传、业务状态、统计与工作区（21:40 工作包记录）

以下包均已接入纯 `lq_props` dispatcher、按需 `LQ.load` 和统一CSS入口；这里记录组件契约，实际页面迁移仍按S3–S6进行。

| 模块 | 核心接口 | 所有权与业务约束 |
|---|---|---|
| business | `businessProps/Markup/createBusiness`；`deadlineClock/jobStatus/questionNavigator` | Clock被动订阅原assignment_time所有者；Job与答题卡不创建轮询、SSE或隐式跳题 |
| upload | `uploadProps/Markup/createUpload`；`bindUpload(root,{snapshot,onFiles,onAction,onError})` | 初始快照必需；队列generation严格递增；请求、去重、服务器确认由原业务控制器持有 |
| workspace | `workspaceProps/Markup/createWorkspace`；`enhanceSplit(root,{onResize})` | 原位分栏与Viewer，不持久化草稿，不给嵌入文档注入主题 |
| insights | `insightProps/Markup/createInsight`；AvatarStack与三统计工厂 | 明确空值/零值；图形是装饰，真实名称与数值只读一次 |
| shells | `shellProps/Markup/createShell`；`enhanceShell/enhanceDock` | 原位导航壳与七骨架片段，沿用已有权限过滤结果；不创建导航注册表或业务命令 |

Clock保留服务端偏移、原截止规则、1秒ticker和60–300秒同步，不在新组件绑定时重新初始化。Document Symbol保证不同模块URL共用所有者，旧请求/JSON不能回写替换后的节点；refresh保留排队中的开始边界同步。绝对时间常显，compact只增加呈现，urgent入态脉冲一次；不逐秒live播报。Job拒绝旧generation及相同generation的新identity；同一identity同generation的顺序仍由调用者保证。QuestionNavigator只回调选择意图，current由控制器回传，组合状态保留可访问说明。34相关单元和39个唯一浏览器用例通过；其中最后clock变更是5项定向复验，不能说最后一轮完整39项。见`lq-s2-business-report.md`。

Upload的`uploading:100`仍是等待服务器确认；`uploaded`必须携带真实服务器confirmation.id。重复图片拒绝必须带原因与归属题号，禁止仅用红色表达。`onFiles`保留原FileList并另给冻结的`context.files`引用数组以跨越drop事件寿命；不合成change、不重写input.files。`onAction`带item与queue generation、isCurrent；Promise失败只释放忙态，成功也不推断删除或上传完成。所有队列共享一个document生命周期MutationObserver，最后一队销毁即断开；单队移除不影响其他队。原`SubmissionUploadManager.getSnapshot`不含确认ID，未来适配必须读取真正remote元数据，不能把syncedCount当确认。29浏览器、16单元；见`lq-s2-upload-report.md`。

Split在视口<1024或容器装不下两区最小宽时复用既有Segment；宽屏拖动或←/→8px，RTL反向，Home/End到边界。两区required同时失败时保留首个浏览器提示；填完首项再提交定位下一项，不绕过原生验证。增强、断点切换、选段和destroy不搬iframe，内部document、草稿和load计数保持；初次工厂把已加载iframe移入新父仍可能触发浏览器重载，已有页面应增强原节点。Viewer的iframe工具条是独立文档流行，直子iframe满宽，不覆盖正文；普通文档工具条才浮顶。44浏览器、5有效/14非法三入口、24次配色/明暗/两宽axe通过；见`lq-s2-workspace-review.md`。

Insight新宏保持旧三个参数签名，但旧`manage_insights.html`及九消费者未改。`null`表示未知，`0`默认空态；`zero='value'`明确保留有效0分等意义。total=0不算百分比，负值/超比例拒绝，旧十六进制tone需将来在页面adapter映射为语义色，组件不私存兼容颜色。AvatarStack最多四个头像加N，完整成员名称仍可访问，图片失败沿用P1回退。27有效/33非法三入口、24相关单元、20正式CSS浏览器通过；见`lq-s2-insights-report.md`。

Shells支持Topbar/NavItem/Sidebar/Dock/FAB/Crumbs/Steps/Editor/PageLayout。侧栏以§11.4完整断点表为准：≥1280完整264、1024–1279 rail72、<1024抽屉。Editor保持原pane、form owner、disabled fieldset、文件输入与iframe；不安全裁切/transform/独立堆叠祖先会明确拒绝，不能直接套任意旧宿主。Dock只测量和补偿显式contentRoot；软键盘候选隐藏前全部命令必须有同key的可用外部fallback，否则保留。每window共用viewport监听，更多Sheet借用并归还原命令节点；不代理click执行业务。9 Python、26有效/21非法三入口、38正式CSS浏览器、48次完整axe零违规；真实设备键盘仍未验收。见`lq-s2-shells-report.md`。

统一CSS `E57FCF43E1C31946F26AD68D092C6CA8ADAF591010B7FB9DD95B82CE51C02168` 下，Upload29+Workspace44完整73项、Shells38和Insights20均重新通过；已实际查看各包原尺寸截图。该段为21:40记录，后续列表分组/滑出、Composer、灯箱与Prose已实现，见下文。完整S2出口未签认。

### 列表、消息输入与既有内容工具（22:15 集成记录）

List 的 `groups=[{key,title,items}]` 与平铺items互斥；分组标题保留原生列表阅读顺序。Row 的 `swipe={key,label,disabled,busy}` 创建唯一破坏动作按钮；`enhanceRow(root,{onAction,onError})` 提供 `refresh/reveal/close/destroy`。触控横滑只展开，不直接执行；常显的键盘入口与原按钮均可达，纵向滚动和取消仍由浏览器处理。Promise只控制忙态与重复点击，不推断业务删除或成功。41项组件门禁、最后23项受影响复验、13项Python通过；之后统一587项组合再次包含完整列表用例。报告 `lq-s2-content-swipe-report.md` 保留真实CDP触控和原事件测试的证据边界。

Composer 通过 `LQ.load('composer')`、`composerProps/Markup/createComposer`、`lq_composer` 生成同一结构；`enhanceComposer(root,options)` 返回 `set/refresh/destroy`。它属于调用者原生form或明确form ID，textarea/name/required/maxlength与原生submitter保留。默认Enter换行，显式 `enter='send'` 才requestSubmit；isComposing、229、组合结束同task和修饰键不误发送。busy用readonly保持FormData，真正disabled使用原生语义；附件状态仅来自调用者hasContent，不自建上传队列。fixed模式只对已定位的直接父容器和单一contentRoot补偿高度/键盘，禁止假定任意旧宿主安全。Node槽全量预检；destroy不清用户输入。22浏览器、23相关单元、11有效/18非法三入口通过，正式CSS与390亮暗图已核对；未把模拟IME/VV称为真实手机键盘验收。

Lightbox 沿用 `ls_image_lightbox.js` 的声明式 `data-ls-lightbox*` 和真实程序接口 `openImageLightbox({items,index,groupLabel})`；`ChatImagePreviewController.open(item,siblings)` 不改签名。新增 `lq-lightbox*` 别名、Clear+scrim与降级材质，原图片保持自身颜色。既有图像pointerdown后捕获click误被当空白关闭的问题已用最小来源标记修复，缩放/拖拽/取消与空白关闭分别验证。Prose只给原 `decoratePreviewCodeBlocks` / AI内容复制按钮换皮，继续用原安全渲染和复制实现。62项组合浏览器（新41+原桥14+聊天7）、3单元和8张原图通过；应用预览验证的是注入clipboard记录器收到正确文本，不是系统剪贴板实测。

当前基线全组件回归 **587/587**，正式CSS `E2BBE0DCA5AEEC0E6BC4E6F8FE1EB3E57AAC64FFB525BC581B99E04B0AE711FC`，日志 `lq-s2-final-candidate-browser.log`。随后独立规格审计列出10项有界整改：FAB/Topbar/Sidebar/Steps呈现、ChipRow、环形Progress、Slider气泡、预览缺态和日期兼容。审计是对规格的复核；该587通过不能作为后续修改的最终回归结果。

### 独立审计后的合同补齐（2026-09-20）

`LQ.load('chipRow')` 提供 `chipRowProps/chipRowMarkup/createChipRow` 与 `bindChipRow(root)`；Jinja为 `lq_chip_row(id,label,items)`。typed items复用Chip，已有业务节点仅原位增强。第9项以后通过“更多 (N)”原位展开/收起，无JS全部可横滚访问；不另造menu、选择状态或select值。控制器提供refresh/setExpanded/destroy，处理焦点、原生invalid展开、动态项数和自动移除；Document共享observer，最后owner释放。原来的筛选代理仍由 `manage_filter_chips.js` 持有。

Progress新增 `variant='ring'`，默认bar继续原生progress；环形单一progressbar拥有名称/min/max/now，装饰SVG和百分比不重复读屏。未知值省略now，显示省略号；0仍是真实0。复用Insight的圆形几何，不改统计语义。正式新包14项与旧presentation受影响项共15项通过，24相关单元、12有效/18非法三入口、24次六palette亮暗两宽axe、20轮observer回收均通过；初次合并发现的bar reduced-motion优先级回归已修复并保留失败日志。见 `lq-s2-chip-row-progress-report.md`。

Shell补齐FAB prominent、Sidebar strong fill、28px步骤节点与一次pulse、14px面包屑分隔。Topbar声明 `viewTransition/view_transition` 默认false，显式开启才占用唯一document名称；不启动可选ViewTransition动效。窄屏更多从三入口初始就具有原位dialog，缺JS/showModal时inline可达，增强后复用现有layer原生top-layer分支；动作/iframe不搬家，form/fieldset保留。更多开启时仅其面板为玻璃宿主。45个正式CSS场景均有通过证据（整轮44+修正测试键盘输入后1），61次axe通过，11 Python；见 `lq-s2-shells-completion-report.md`。

Slider使用原output作胶囊数值气泡，原生range/update/FormData不变；26项正式表单浏览器复验通过。日期兼容层实测后仅补44px触控/窄屏容纳、可选相邻月日期对比和时间列定位；原时间按钮改为有名group+aria-pressed，保留原生button操作，避免伪listbox。29项正式兼容/触控/六palette明暗门禁、33次打开日期与说明链axe通过，320/390/768所有实际目标≥44px；未改日期值、input/change或请求流程。见 `lq-s2-date-accessibility-report.md`。

主预览补agent十态、队列busy/partial-failed、danger Alert、pill/vertical Tabs、flat/hero Card及card/page Empty。七骨架增加skip link；`/dev/lq?shell=centered`复用原base_centered文档根和同一教师鉴权/开关/no-store，在dev专用body类下为背景和备案区使用主题表面，不影响旧业务页。无业务请求、数据库或偏好写入。

### S3 实页适配合同（实施中）

管理兼容宏 `page_head/filter_bar/empty_state` 增加显式 `lq_enabled=false` 参数。八个路由调用传入pilot上下文，其他31个调用点保持原输出。page-head保留title ID、说明按钮、aside caller及原动作；filter wrapper仍为div而非新form，保持search ID/autocomplete和原生form归属；empty支持明确reason，不由失败伪造空结果。旧可信模板的raw attrs仅保留在旧分支，新分支使用结构化属性校验。JS和React组件仍沿用S2接口，不另复制业务宏。

管理壳 `manage/lq_sidebar.html`、`lq_topbar.html` 和 `manage_lq_pilot.js` 只适配现有服务端导航payload与作者定义动作。native details的open状态替代旧is-open样式所有者；query、搜索、原生href和权限范围保持真实来源。移动业务按钮关闭本次原位actions pane，再由原监听打开其原弹层，禁止克隆动作/iframe、重发click或再造业务controller。新的折叠偏好按teacher:id隔离，旧全局key只在无新值时用于初值；销毁释放listener并恢复属性。

成绩页是单路由的topbar组合，不改变 `base_navbar` 的其他消费者。`report_card.js` 使用document/element单owner、原ECharts数据、受控主题更新、resize及dispose；图表是装饰，SSR成绩/状态文字在脚本或图表失败时继续可读。0与null、退回待重交、未揭晓小组和冻结公布不由组件重算。

S3实页审计发现S2 Shell的primary/success前景引用了未定义别名；现改用 `--ls-on-primary-soft`、`--ls-tone-success-fg/soft` 的已定义色对，并以全LQ CSS变量引用检查阻止同类静默继承。原S2通过证据保留其历史产物hash，S3修正另验，不回写旧截图为新产物结果。

试点首屏在head独立检测 `blocking="render"` 能力。支持时阻塞该入口的首次渲染；不支持时SSR保留内联导航/动作几何，原Shell以 `keepOpen` 继续处理，不新增控制器、不隐藏正文。成绩顶栏为原 `cultivation_identity.js` 的人生一言按钮预留44px槽，保留原可访问名称与点击处理；按钮处于玻璃顶栏内部，不再自身声明模糊。模块失败、迟到增强、无JS分别验证，能力关闭仿真不代表旧浏览器实测。

`openProcessMaterialModal` 的旧LP弹层从已协调父层打开时，使用同一LQ portal、父子关系、关闭守卫和焦点/锁所有者；此分支不安装旧document Escape/Tab监听。正常关闭由LQ恢复焦点，强制销毁使待决关闭失效，仅在原父层仍为顶层时恢复原触发器；onMount同步移除或父销毁不能留下孤立子层。独立旧LP仍保持原同步关闭合同，材料影响token、删除请求和确认结果未重写。浏览器覆盖实际材料详情、20次子层取消、日期子层顺序、dirty veto、force及同步移除；测试结果按产物轮次记入验收资料。

S3迁移台账采用明确的partial范围：新适配源码完整执行阻断lint，共享旧文件仍完整产生警告，并绑定已审阅的字节SHA、范围说明与真实测试路径。编译CSS/vendor仅列实际依赖，不假充作者源码。此合同不会把整个管理正文或旧弹层登记为已迁移；共享文件漂移须重新审阅，不能自动刷新指纹。

WebKit真实触控暴露旧人生一言浮层仅依赖document click的问题。`cultivation_identity.js` 仍拥有原场景与按钮，仅将其浮层close统一为单个幂等owner：外部pointerdown、原click/Esc与按钮toggle均释放同一组三个监听并只移除自己；移除迟注册timer，避免关闭后监听复活。此为旧controller的兼容修复，不代表人生一言全页或全部旧popover已迁成LQ。试点关闭后的旧report导航也复验原关闭路径。

### S4 第一包共享壳与居中页合同

`lq_family_enabled` 只读取服务端精确逗号列表 `LANSHARE_LQ_FAMILIES`，缺省关闭；当前实际接线manage-shell/navbar-shell/centered。原S3试点开关独立。管理Shell的原节点增强与搜索owner不变，正文CSS留在独立pilot文件，嵌入页不因新family启用而换壳。`navbar_lq.js`用Symbol幂等安装共享Topbar及Dock；`lq/navbar-shell.js`仅负责原位反馈层交接，`report_card.js`继续独占图表。当navbar family开启时，成绩模块不再安装另一Topbar。pagehide/pageshow与移除分别释放或刷新其自有句柄，不重建业务节点。

四项Dock为真实SSR链接；激活态按当前pathname/section投影，私信与通知均激活消息。补白归属于显式main contentRoot，关闭增强后保留SSR补白；不叠加旧has-bottomnav。软键盘候选由原S2能力合同判定，当前仅仿真验证。新navbar顶部保留原场景和44px一言槽，移除图像上的额外filter blur，使手机顶栏和Dock为两个持续玻璃宿主。学生旧首页palette窄宽样式必须被新面板的完整宽度覆盖，验收同时检查三个当前选项的可读空间。

居中壳保留原登录认证controller。学生仅在背景成功、Tier A及tinted等允许条件下从厚玻璃切Clear；教师及状态保持厚玻璃。材质切换的卡底、前景、遮罩与高光同步生效，不逐渐过渡成错误色对；错误语义软色叠不透明主题底。已有密码的原生POST与JS路径共用后端认证编排，失败SSR保留账号/安全next且不回填密码。此合同不包含尚待实施的完整无JS首次设密/找回、一言页迁移或实体键盘验收。

## 2026-09-21 组件契约增补（主任务）

S5 四域迁移中暴露的三处能力缺口，已在组件层补齐并附回归测试。三项都是纯加法，既有调用点行为不变。

### `lq_chip` / `lq_chip_row`：筛选芯片可以是真链接

新增顶层 `href` 参数（`lq_chip_row` 的 items 支持 `'href'` 键）。

| 条件 | 渲染 | 选中态属性 |
|---|---|---|
| `kind='filter'` 且传 `href` 且未禁用 | `<a>` | `aria-current` |
| `kind='filter'` 未传 `href` | `<button>` | `aria-pressed` |
| `kind='filter'` 传 `href` 且 `disabled` | `<button>`，不带 href | `aria-pressed` |
| `kind` 非 `filter` 传 `href` | 报错 | — |

链接不是开关，所以用 `aria-current` 而非 `aria-pressed`。URL 复用既有 `_url()` 校验器，`javascript:`、`data:`、协议相对地址一律拒绝。动因是统一收件箱的筛选由 URL 驱动，原本是 `<a href>`，迁进芯片会丢失 href。

### `lq_field` / `lq_input`：输入框可以引用 datalist

`control_props` 新增 `datalist='<datalist 的 id>'`，由组件写出 `list` 属性。id 必须匹配 `[A-Za-z][\w:.-]*`。非 input 控件传它会**报错而不是静默丢弃**——静默丢弃会把调用方的错误藏起来。`<datalist>` 元素本身仍由页面模板自行书写。

### `.lq-filter-bar__controls > .lq-field`：字段在筛选栏内共享行宽

`.lq-field` 按冻结契约是 `width: 100%`，适合表单列。放进筛选栏的弹性容器后，这个宽度成为换行基准，导致每个字段独占一行。新增 `.lq-filter-bar__controls > .lq-field { width: auto; flex: 1 1 10rem; }`。

实测对照（1440 宽，教材页）：修复前 3 个字段 3 行、每个 357px；修复后 2 行、宽度 172/172/356。教案页修复前后完全一致，无副作用。三个施工包独立撞上过这个问题，其中两个各自写了页面级覆盖，因此修在组件层。

## 客户端工厂的使用边界（2026-09-22）

`static/js/lq/{forms,tables,content,components}.js` 的工厂与服务端同源校验，并由 `tests/e2e/components/lq-{forms,tables,content,presentation}.spec.ts` 逐例断言**工厂产出与 Jinja 宏产出完全相等**。因此在控制器里调用工厂是走契约；手写字符串或 `innerHTML` 拼 `lq-*` 类名才是绕过契约。

三条经常被误判为缺陷、实为两端一致的行为：

1. **字符串模式无法填插槽。** `formMarkup('form_section'|'form_actions', props)` 产出的插槽容器是空的。这**不是静默错误**：Jinja 宏在没有 `{% call %}` 时产出完全相同的空容器，两端一致。字符串接口本就没有传子节点的入口。**需要插槽内容时用 DOM 工厂** `createForm(kind, props, children)`。
2. **控件强制可见标签。** `lq_field` 与客户端 `field` 都要求非空 label（服务端 `lq_forms.py`、客户端 `forms.js` 各自 raise）。重复性列表（如投票选项行）因此会出现"选项 N"这类标签。这是冻结契约的既定无障碍决策，不可在页面侧退回。
3. **按钮总是写出 `aria-label`，即使已有可见文案。** 服务端 `lq_components.py` 与客户端 `component-props.js` 行为相同，写入的字符串就是可见文案。属契约级设计选择，不是两端漂移。

## Table 增补：表头按列开启插槽（2026-09-22）

列定义新增布尔 `slot`（服务端 `classroom_app/lq_tables.py`、客户端 `static/js/lq/tables.js` 同步）：

```
columns: [{ key: 's1', label: '第 1 次课', slot: true }]
→ <th><div class="lq-table__colhead" data-lq-slot="col:s1">标签 + 插槽</div></th>
```

插槽名 `col:<列key>`，与既有 `cell:<行key>:<列key>` 同构。**按列开启**：没有声明 `slot` 的列，表头 DOM 与此前一字不差，因此全站既有表格形状零变化。未知键仍然拒绝。

动因：签到统计的课次矩阵，每个课次列的表头承载映射状态行与唯一的「复核课次」按钮；没有表头插槽就只能删掉这个功能钩子。

配套样式（`static/css/lq/components/tables.css`，均为布局与令牌，无硬编码颜色）：

- `.lq-table__colhead` 纵向排列，否则插槽内容会与列标签挤在同一行。
- `.lq-table-shell--matrix` 的行表头列改为吸附（sticky），背景取 `--ls-surface-1`。矩阵横向滚动时行表头必须留在视野内；它替代的旧选择器把浅色背景写死，在暗色下会造成缺陷。实测亮色 `rgb(255,255,255)`、暗色 `rgb(24,27,37)`，跟随主题。

## NavMenu 与 Menu 条目属性通道（2026-09-22）

### `lq_nav_menu`：导航菜单按钮（触发器 + 一级下拉）

`classroom_app/lq_nav_menu.py` / `templates/macros/lq/nav-menu.html` / `static/js/lq/nav-menu.js` / `static/css/lq/components/nav-menu.css`。

签名：`lq_nav_menu(id, label, items, icon=none, variant='glass', tone='neutral', size='md', shape='capsule', align='start')`。条目结构与 `lq_menu` **完全相同**，校验直接委托 `lq_menu_props`；面板就是 `lq_menu` 的产物，不另造一套。

**严格两层**：触发按钮 → 一级菜单为止，不支持再嵌套。

客户端 `enhanceNavMenus(root, { hoverOpenDelay = 120, hoverCloseDelay = 220 })`：
- 键盘、焦点、层级协调**全部复用** `menus.js` 的 `bindMenu`，组件自身零 `keydown` 监听。
- 悬停开合只在 `(hover: hover) and (pointer: fine)` 生效；从触发器移动到面板的途中不关闭。
- **悬停打开不抢焦点**：用 `layer.js` 的 `onInitialFocus` / `onReturnFocus` 两个可取消钩子，在悬停路径上阻止「聚焦首项」与「关闭时还焦点」，否则鼠标划过顶栏会夺走用户正在编辑的输入框焦点。点击与键盘路径行为不变。
- 面板**不加** `backdrop-filter`：宿主壳已是模糊宿主。

已知项：裸组件夹具页打开菜单时，axe 的 `region`（moderate，最佳实践）会命中 `#lq-layers`。根因是 `layer.js` 把传送宿主挂在 `<body>` 直下，页面地标是它的兄弟而非祖先，**真实页面同样存在**。用例把它钉死为「恰好一条」，不是过滤。

### Menu 条目新增受限的 `attrs` 通道

`lq_menu` 与 `lq_nav_menu` 的条目新增可选 `attrs`，两端同步：

- 只接受 `data-` 开头且**不以 `data-lq-` 开头**的属性名；`aria-*`、事件属性、任意其他名称一律报错。
- 值必须是标量；`true` 渲染为空字符串。
- 调用方的键先展开，**组件自有键（`data-lq-menu-item`、`data-danger`、`target`）总是覆盖它**。

动因：学生顶栏「我的」菜单里的修改密码与问题反馈由页面脚本按 data 属性打开弹窗。此前的做法是对冻结组件的输出做字符串替换注入属性——依赖渲染出的属性顺序，任何组件内部调整都会静默失效。改为正规通道后那处替换已删除，对应断言也改为「钩子与菜单项落在同一元素上」的 DOM 断言，不再依赖属性顺序。

## 全站统一玻璃材质：五级材质标度（2026-09-23）

负责人决定所有板块统一为玻璃，包括按钮与弹窗。**这取代了此前「玻璃只给外壳、内容用不透明表面」的规定。**

| 级别 | 令牌 | 亮 / 暗 | 用途 | 模糊 |
|---|---|---|---|---|
| clear | `--ls-glass-fill-clear` | .22 / .22 | 图上浮层、登录卡、灯箱工具条 | regular |
| control | `--ls-glass-fill-control` | .52 / .54 | 按钮、芯片、输入、开关 | **无** |
| chrome | `--ls-glass-fill` | .58 / .62 | 顶栏、侧栏、Dock、工具条 | regular |
| content | `--ls-glass-fill-content` | .74 / .76 | 卡片、列表、表格、表单容器 | thin |
| raised | `--ls-glass-fill-strong` | .78 / .80 | 弹窗、菜单、抽屉 | thick |

### 两条工程原则

**一、模糊只在材质边界出现。** `materials.css` 强制：嵌套在 `.lq-glass` / `.lq-surface` 内的材质，`backdrop-filter` 一律 `none !important`。面板内的面板看到的本就是已模糊的底，再来一层只多一个合成层。控件级永不自带模糊——那个尺寸上模糊看不见而代价照付。

**两个刻意的例外**：toast 与 tooltip 取 raised 的填充与描边但不做模糊宿主。toast 栈无数量上界，每条一个宿主会把成本乘以 N；tooltip 是控件尺寸。

**二、对比度有可论证的边界。** 若要保证纯黑或纯白直接压在面板背后仍达标，不透明度需约 .94，那已不是玻璃。面板实际能看到的是页面背景层，其图片层有自己的不透明度（亮 .55 / 暗 .42）叠在页面表面上。按这个边界，四个承载文字的材质层级上正文均在 8–12 之间。

为此收紧了三组令牌：`--ls-ink-3`（亮 45%→38%，暗 58%→62%）、`--ls-glass-muted`（亮 43%→32%）、以及**全部 34 个语义前景**（`--ls-tone-*-fg`、`--tone-agenda-*-fg`、`--ls-on-primary-soft`），其中 10 个此前在控件材质上不达标。调整只动明度，色相与饱和度是品牌的。

`tests/test_lq_tokens.py::GlassMaterialContrastTests` 把这些钉死：六调色板 × 明暗 × 四材质的正文与次级文字全部断言达标、全部语义前景在控件材质上达标、材质标度必须保持从透到实的顺序。**改任何相关令牌都会立刻变红。**

### 主操作按钮是刻意的例外

`lq-btn--prominent` 保持不透明主色。它是号召性操作，半透明会同时丢掉 `--ls-on-primary` 的对比度保证与视觉分量；玻璃语言改由顶边高光承担。

