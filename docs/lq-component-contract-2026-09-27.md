# LanShare 液态玻璃组件开发合同

本文件是新建、修改和迁移界面的开发要求。适用于教师、学生、管理员、访客页面，弹层、iframe 内工具、动态生成内容和错误页面。组件 API 的完整参数见 [lq-components.md](lq-components.md)，材质与前景配对见 [lq-scene-ink-2026-09-26.md](lq-scene-ink-2026-09-26.md)。本合同不把源码声明当作业务验收；实际覆盖和未决项分别记录在 [全平台审计](lq-platform-component-audit-2026-09-27.md)。

## 1. 一个结构合同，多种表现

组件由五部分组成：语义 DOM/槽位、参数与变体、共享材质/排版、交互状态与生命周期、可执行验收。只换颜色或复制一段 CSS 不算组件化。

同一组件允许三个渲染入口：Jinja 宏、原生 JS 工厂、React 薄适配器。它们消费同一参数/DOM 合同和 CSS，不得各自实现颜色、动画、禁用判断或焦点栈。已有领域控制器持有节点时，可在明确的构造点采用声明式入口或 `adoptDomainControl`，无需销毁重建节点。禁止在全页面 MutationObserver 中根据类名猜组件、反复扫描 DOM 或替换正在编辑的控件。

优先级：复用现有组件 → 通过参数和作者槽位组合 → 新增共享组件。确需新增时，先写状态、行为和消费者，再提供实现与测试；不得先在页面写完私有组件，再称它是“临时样式”。

### 组件边界

| 类别 | 共享组件 | 领域保留的职责 |
|---|---|---|
| 操作 | Button、Chip、ActionEntry | API 动作、权限、幂等键、提交/取消业务 |
| 输入 | Field、Input、Textarea、NativeSelect、Selection、Checkbox、Radio、Switch、Range、Upload | 真值、校验、IME、原生表单归属、上传队列 |
| 内容 | Card、Row、List、Empty、Prose、Bubble、Status、Alert、Conflict、Progress | 数据格式、文本和领域状态；正文、图表、文档本身不被当成按钮 |
| 导航 | Tabs、Segment、Crumbs、Steps、NavMenu、Toolbar | 路由与选中项；键盘策略仅有一个 owner |
| 浮层 | Dialog、Sheet、Drawer、Popover、Menu、Tooltip、Viewer | 草稿、关闭否决、预览/提交；栈、焦点、滚动锁由 LQ.layer 持有 |
| 复合布局 | PageHead、FilterBar、Split、EditorShell、Workspace | 领域布局、列宽、密度、资源权限 |
| 数据控件 | DomainChoice、颜色样本、画布 handle、画布文字编辑 | 色值、坐标、字体、命中区域等真实数据表现；外框、状态、焦点仍遵守组件合同 |

正文链接保留真实 `a` 和 URL；表单按钮保留 `button` 和原 `type`。禁止用 div 模拟原生按钮，禁止用操作菜单替换 select 的值/校验合同。下载的试卷、简历正文、用户文档、SVG/图表数据和画布内容是内容槽位，不强制涂成玻璃；它们周围的编辑、选择、预览和工具控件必须使用组件。

## 2. 构造入口

新按钮优先使用 `macros/lq/button.html::lq_btn`、`LQ.button` / `createComponent('button', props)` 或 `LqButton`。普通按钮参数为 `label, variant, size, icon, href, type, disabled, loading, attrs`。变体限于 `prominent/glass/soft/ghost/destructive/link`，尺寸为 `sm/md/lg`，颜色来自主题令牌，不能传任意颜色替代变体。

```jinja
{% from 'macros/lq/button.html' import lq_btn %}
{{ lq_btn('保存', variant='prominent', type='submit', attrs={'data-save': ''}) }}
```

```js
import { createDomainButton, adoptDomainControl } from './lq/domain-controls.js';
const save = createDomainButton({ label: '保存', variant: 'prominent', attrs: { 'data-save': '' } });
// 已有 input 的 name/form/value/disabled/selection 和事件完全保留。
adoptDomainControl(existingInput, { kind: 'input' });
```

迁移期间已有模板可显式声明规范 DOM。声明只选择共享配方，不构造第二套行为：

```html
<button data-lq-component="button" class="lq-btn lq-btn--glass lq-btn--sm" type="button">操作</button>
<input data-lq-component="input" class="lq-input" id="title" name="title" required>
<select data-lq-component="select" class="lq-select" id="term" name="term"></select>
```

保留的旧类只能作为布局/业务钩子。`declarative.css` 将显式组件的背景、前景和边框绑定到共享配方，避免晚加载的旧样式夺回外观所有权；参数来自 `button.css/forms.css` 的同一配方，不新增一套颜色。新增领域 CSS 禁止重新声明控件背景、文字色、边框色、滤镜和交互动效；只允许布局、几何以及受控内容槽位。

复合选择卡使用 `kind='choice'` 和作者内容槽；原 `aria-pressed/selected` 与领域选择状态保持一致。颜色样本使用 `data-lq-visual='color'`，其颜色是用户数据，不能被普通玻璃 fill 覆盖。普通动态标签按钮应由当前子文本命名，不能把初次标签永久写入 aria-label；纯图标按钮必须提供稳定而准确的名称。

领域已有选中状态的 Surface 用 `data-lq-selected="true"` 选择共享填充、配对前景和边界，取消选择时移除此属性。业务控制器仍负责真实选择状态，组件不额外绑定点击或键盘事件；旧 `is-active` 类保留并不自动证明选中态仍可辨认。透明关闭时保留不透明背景，通过边界与文字继续区分选择。

浮动操作入口复用 `.lq-fab` 的定位与 slot 合同。底部 dock 的实际占用高度由共享 `enhanceDock` 测量；FAB 在其他 DOM 分支时通过 `floatingRoot` 发布同一高度令牌，销毁时还原。禁止另做一个 resize/scroll 测量循环，也不能以固定 bottom 覆盖 dock 避让；无 dock 页面及阅读器的既有安全边距通过共享定位变量表达。

原生文件/颜色对话框由浏览器/系统管理；不得伪装输入值或为统一外观破坏键盘、表单和操作系统能力。原生 details/summary 保留浏览器的展开、焦点和键盘行为。

### 实现定位与扩展规则

| 修改目标 | 唯一实现入口 |
|---|---|
| 跨渲染器的参数、默认值和安全属性 | `classroom_app/lq_components.py` 与 `static/js/lq/component-props.js`；同步修改合同测试 |
| 原生创建、Jinja 与 React | `static/js/lq/components.js`、`templates/macros/lq/`、`frontend/src/components/lq-presentation.tsx` |
| 现有领域控件就地接入 | `static/js/lq/domain-controls.js`；只改呈现，不重绑业务事件 |
| 按钮/输入/状态材质 | `static/css/lq/components/button.css`、`forms.css`、`control-states.css` |
| 显式声明与历史布局适配 | `declarative.css`、`domain-controls.css`；不得在这里另造一套主题颜色 |
| 动效参数、系统偏好优先级 | `static/css/lq/components/motion.css` |
| 弹层栈及可取消的进出场 | `static/js/lq/layer.js`、`static/js/ui_overlay_motion.js` |

领域控制器可调用 `createDomainButton`、`adoptDomainControl`、`domainButtonAttributes`、`createDomainPopoverSystem`，使用同一参数归一化和材质。`adoptDomainContentSlot` 用于画布/正文数据，`adoptDomainHandle` 用于缩放与旋转命中区域；它们不是绕过普通操作控件材质的通用豁免。数据色样仅允许实际色值槽位设置 `data-lq-visual="color"`；课程与考勤分类色仅用于已登记的 `category` 数据槽位。

`data-lq-component` 必须与规范 class、正确 DOM、实际 owner 同时存在，不能给任意私有组件加标记就称迁移完成。纯布局 region/toolbar 不应因声明而额外增加模糊；需要浮层材质时显式采用 raised 外壳。后端生成的独立 HTML、预览 iframe 也属于平台 UI，必须加载同一正式资产和主题引导。主题桥只授权可信的平台预览 iframe；用户上传的文档和课件保留其内容样式与隔离边界。

新增组件需要同时交付参数表、DOM/槽位结构、状态表、键盘/焦点合同、销毁/异步合同、材质层级、动效覆盖与真实消费者。不要为了单页新增重复 Button/Card/Dialog。新增可复用领域组件前先确定至少一个真实用例，保持核心入口按需加载，避免把白板、文档或审批等完整控制器塞进全站首屏。

## 3. 材质和可读性

材质层级只使用 `control/content/chrome/raised/clear`。小按钮、字段、重复行不单独创建 backdrop-filter；视口背景与边界载体提供玻璃采样，控件共享半透明填充、明暗边缘和光泽。普通内容面板不因记录数增长而增加实时模糊层。浮动导航、菜单和弹层只在边界采样真实背景，嵌套材质不得重复模糊。

颜色必须成对：背景与正文、背景与次要文字、语义状态 fill/ink 一起变化。不能用单独白字、降低整个控件 opacity 或透明 CTA 获得玻璃感。主操作保证对比；照片背景下使用场景前景配对；保留选择、错误、权限不足和繁忙的辨识度。主题、配色、透明度关闭、增加对比度、forced-colors、减少透明度和打印均需验证。

小标签、辅助说明和保存状态同样需要清晰，项目对普通小字的对比目标为至少 4.5:1。验证透明表面与实际场景合成后的背景，不能只在白底上比较令牌。强前景仍由共享 ink 提供；警告/错误/保存语义优先复用 Status/Alert 的填充与前景配对，禁止仅把有色前景放到任意透明背景。领域报错构造点可就地声明 Alert，保留原节点、诊断转义、重试动作和草稿恢复。

新组件禁止固定 blur 数值、逐行滤镜、内联硬编码品牌色、永久 will-change、为每个小控件开启独立合成层。数据颜色、图表和文档内容必须有明确内容槽位，不能用“领域特殊”豁免整个工具栏。

堆叠内容的可读性不能依赖模糊。3D 周课表采用稳定 Surface 外壳，仅当前周连接表头、网格和课程内容；后排为空壳且 `aria-hidden=true`。切换时同步卸载离场内容、生成当前内容，保留外壳节点及反向过渡；不能延迟到网络请求或动画结束后才支持定位和点击。当前周可使用共享 raised 边界，采样宿主最多一个；嵌套材质、关闭透明或不支持模糊时允许为零，仍须清晰。不要为了恢复模糊绕过共享嵌套规则。卸载包含焦点的内容时归还稳定的舞台入口，不抢其他控件焦点；舞台快捷键不得截获子按钮、链接和输入的原生操作。

## 4. 动态液态过程与定制

组件自身提供按压形变、释放回弹、状态边缘反馈和浮层出现/消失过程。默认动作应立即回应输入，快速完成；不延迟业务事件，不用动画时长当作网络完成信号。动画采用 transform/opacity；不动画 blur/filter、宽高或大面积持续背景。

`data-lq-motion` 可设置在 html、组件或子树上，支持 `off/quiet/standard/expressive`。默认 `standard`；每个子树可覆盖。现有 CSS 定制变量为：

| 变量 | 默认 standard | 含义 |
|---|---|---|
| `--lq-motion-press-duration` | 80ms | 按下反馈 |
| `--lq-motion-control-duration` | 180ms | 释放和状态过渡 |
| `--lq-motion-presence-duration` | 220ms | 弹层出现/消失 |
| `--lq-motion-menu-duration` | 160ms | 菜单过渡 |
| `--lq-motion-feedback-duration` | 280ms | 短暂反馈 |
| `--lq-motion-hover-y` | -1px | 精细指针轻微上浮 |
| `--lq-motion-press-x/y` | 1.012 / .965 | 轻微横向展开、纵向压缩 |
| `--lq-motion-overlay-scale/distance` | .98 / 24px | 浮层起始尺度/位移 |
| `--lq-motion-ease/spring` | 共享曲线 | 按压与自然回弹 |

quiet 更短更轻，expressive 幅度稍大，off 时长归零、形变归一。系统 `prefers-reduced-motion` 优先于局部自定义，变化时立即生效。字段不缩放、不抬升，避免文本抖动。disabled/loading 不执行可操作的按压反馈；触摸不保留 hover。画布/窗口的业务定位 transform 不得被按钮动效覆盖。

连续按压/反向操作从当前帧自然衔接，不能堆积动画队列。浮层使用 `ui_overlay_motion` 的可取消过程及有限超时；退场完成才清理，新的打开能够取消旧关闭。静止时不得有装饰性 rAF 循环、逐控件移动监听或持续布局读取。loading 的有限范围状态指示可旋转；off/reduced 下保留静态状态和文字。

动效接口为后续统一偏好提供扩展点。未接入账户偏好持久化的参数不能在文案中宣称已经跨设备保存。原生平台设计参考 [Apple Materials](https://developer.apple.com/design/human-interface-guidelines/materials) 与 [Motion](https://developer.apple.com/design/human-interface-guidelines/motion)，不承诺网页与原生光学渲染完全一致。

## 5. 状态和完整业务流程

所有交互组件需要覆盖：默认、hover、focus-visible、active、选中/展开、disabled、loading、empty、error、无权限；没有该状态时在组件说明中解释。禁用不只改变样式；按钮用 native disabled，aria-disabled 入口由唯一控制器阻止执行且保留说明可达性。繁忙时保留动作名称，防止重复提交，结束后恢复真实可用状态。

组件只发出用户意图，业务控制器负责请求和事务。不得为换组件修改以下合同：账号/session 隔离、权限边界、request_id 幂等、expected_version/ETag 并发检查、409 冲突保留草稿、413 上传/草稿保留、202 任务轮询、截止时间和考试限制、异步结果代次检查。提交失败不清空输入；后到的旧响应不能覆盖新页面或新选择。

表单迁移须保持 id/name/type/form/autocomplete/min/max/step/required/readonly、选区、滚动、IME composition、input/change 次数与原生校验。不要把点击保存改成隐式 submit，也不要意外取消原有 submit。上传、预览、提交和撤销是不同阶段，不能只测试控件能点。

弹层有且只有一个所有者。LQ.layer 管理层级、焦点、背景 inert、滚动锁、嵌套顺序和关闭；可移植领域弹层须显式注册，不双重持锁。关闭可能被保存中/未保存草稿否决，否决时保持 DOM、焦点、父子关系和草稿。退出后焦点归还有效触发器；销毁是强制清理，不冒充用户确认。全屏工作区不得遮挡本应仍可用的外部内容。

## 6. 验收与守卫

每个组件/消费者提交必须给出：来源文件与 owner、共享组件/变体、保留的业务钩子、状态清单、实际测试与证据、剩余限制。源码 occurrence 数量不是运行时控件数量，也不是完成率。

必须执行与改动相关的检查：

1. 源码审计涵盖直接页面、动态路由、partials、异常文档、JS 工厂和 React。未知不能自动视为通过。
2. 单元/类型/真实 Jinja 宏语法与关键跨入口合同验证。构建产物必须更新，检查正式资源图和首屏预算。
3. 真实浏览器验证桌面、触屏与窄屏，浅/深色，长文本、空数据、错误和权限分支。共享 CSS 的测试必须加载正式构建，不能只加载不经构建的源文件。
4. 键盘、触摸、焦点归还、嵌套、关闭否决、连开连关、重复挂载/销毁和异步竞态。按业务场景检查保存后的服务端状态。
5. 统计实际 backdrop-filter 宿主、重复布局读取、长任务与请求数。旧电脑优化不得凭“GPU 加速”或 class 数量下结论，不得编造 FPS。
6. 对数据/权限测试只用已确认的隔离合成运行目录，禁止因运行测试连接本地真实或生产数据库。

自动扫描只产生“共享调用/显式声明/未知/待审”，业务与视觉验收必须有浏览器证据。源码 SHA 变化后更新相关证据，不把历史验收计数反写为新版本全量通过。

## 7. AI 修改前后的检查顺序

修改前先追踪路由 → 模板/岛 → controller → API/service → 持久化/权限；查找现有 LQ 组件与实际消费者。列出要保留的值、事件、焦点和关闭合同。修改后检查完整 diff，运行相关隔离测试及正式构建，更新审计和本合同对应条目。不要添加重复样式、全局 DOM 猜测器、第二个焦点栈或未经验证的“已全平台完成”描述。

发布按既有 PostgreSQL 演练、备份摘要、部署预检、健康/资源图核验执行；保护运行数据。Git 推送与服务器部署是两个独立结果，分别确认。
