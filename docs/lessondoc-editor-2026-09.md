# 学习文档可视化编辑器（LessonDoc Editor）设计方案与施工文档

> 制定日期：2026-09-03。状态：**E0-R/E1—E6 已实装；多维度验收、远程部署和 Git 推送完成**。代码提交 `54363b12`，线上版本 `20260903-203549-cd53fdd64398`。实际结果记录在 `docs/lessondoc-editor-progress-2026-09-03.md`，不能将已有按钮或接口等同于最终验收通过。
> 2026-09-03 续建审计：见 [现状审计、继续设计与施工方案](lessondoc-editor-audit-and-construction-2026-09-03.md)。§10 的 E0 完成勾选保留为前轮施工记录；继续施工前先执行审计文档 E0-R，不将这些勾选视为本轮完整验收。
> 前置真源：`docs/course-lessondoc-template-2026-09.md`（LessonDoc 2.0 架构与进度）、`docs/lessondoc-authoring-guide.md`（deck JSON 契约）。
> 本文档是编辑器的唯一施工真源；实现过程中的口径变化必须回写本文档；deck JSON 契约的变更必须同步回写 authoring-guide（含文末 AI 摘要节的取舍，见 §4.11）。

---

## 0. 一句话总纲

### 2026-09-03 续建记录（优先于下方初稿口径）

- 已完成 E0-R：HTML/SVG 解析净化；CSS 使用 tinycss2 1.5.1 解析，JSON 保存局部 CSS、浏览器按最终块 ID 加作用域，迁移前轮重复前缀；媒体、背景和 HTML 统一包内路径规则。
- `empty:true` 只保留原本没有内容对象的有意留白页；非法内容被清空不能伪装为留白。two-col/grid 的纯浮层页可以保存。
- 模型增加 2 MiB、32 层结构、每页 160 元素（含嵌套）和全课 2000 元素上限；编辑加载专用 ID 补齐与结构化损失诊断已建立。旧读写 API 仍返回 `(clean, warnings)`。
- ID 在页、全局、首页统一去重；复制组内部动作按自己的子元素重映射；`goto.slideId` 优先并回填兼容页码；run/reset 只接受 codewalk。浏览器 DOM ID 独立命名，并重写 SVG、局部 CSS 和步骤操作中的内部引用。
- course.js 暴露幂等 mount/unmount；codewalk 可销毁、编辑时暂停、离页暂停、进入页面后才自动启动。降低动画偏好不压缩教学演示的停顿。图示布局不再修改输入，桥接复制父页模型，单页替换不再重布局整课。
- 编辑画布固定 1280×720，支持几何变动事件；首页使用独立受控长页容器，支持 heroGradient/cardRadius 和深度折叠。article 保留封面、章节浮层与一份全局正文。
- 验收：125 项 Python 相关测试通过；真实浏览器 `tools/lessondoc-sample/runtime-check.html` 的 24 项交互/重渲染/模型隔离验收通过；7 个分发与示例资产同步。验收同时修复了兼容加载器与显式 interact.js 的竞态。
- E1 已实装：按 pack 加短事务写锁，以正文文件 hash 检查版本，并在数据库条件更新中再次比较 hash；统一保存正文、首页投影、20 个历史版本和最近 128 个操作回执。恢复先保留当前版本，损坏文件不会被当成空白新稿覆盖。
- 已接入整课 AI、单页 AI、主题、阶段、课次标题/排除、旧包导入和 Git 回写。AI 在网络调用前记录版本与任务领取时间；Git 在网络操作前记录包文件和课次状态，写回前复核。迟到结果不会覆盖新修改。普通 AI 材料优化只生成独立 Markdown 或优化稿，不覆盖 LessonDoc 正文，保留原流程。
- 额外确认并接入材料库通用源码编辑：HTML/JSON 仍可读写，正文通过统一保存，HTML 壳由平台生成；GET 返回 revision/source_revision，PUT 带回版本和操作标识。源码编辑的后续输入不会被迟到保存响应清掉。普通文本与包内辅助文件保留原编码保存能力；包内引擎源码变化会标记需要刷新。
- 新增可信 preview 和 editability：前者只加载平台引擎，包内相对素材继续走原授权文件路由；后者能从嵌套文件、课次目录和 pending 包根找到登记信息。页面与 API 均验证教师及所有者，预览响应禁缓存。
- 媒体按原始请求流限制大小，图片验证文件格式及解码，SVG 净化后才写入内容寻址存储。文件原子落盘；以 hash 文件名在 assets/media 中复用，首页和课次分别返回正确相对路径。资源列表按包分页。
- 自定义元素提供按教师隔离的保存、列表、改名、删除和插入；资源保留为教师素材库中的正常材料引用。跨包复制重建目标包资源引用及块 ID，重映射内部动作而不修改图示内部节点 ID。源包或模板删除不损坏已保留/插入的素材引用。历史恢复会明确报告已经缺失的资源，暂不承诺恢复已删除的媒体字节。
- E2—E6 已实装：三栏编辑器、命令与自动保存、流式和定位元素操作、属性/动作/背景、首页、素材与模板、损坏历史恢复、源码预检、AI 候选可视预览、旧包转换及入口整合。后续细节与验收口径以 [续建进度](lessondoc-editor-progress-2026-09-03.md) 为准。

本轮全仓后端共 1,668 项（通过 1,654、环境跳过 14），前端 94 项、类型检查、生产构建、真实 PostgreSQL 5 项均通过；页面专项与独立下载包验证完成。已完成远程发布、107 个本轮文件比对、新表及公网资源验证。审计证据文件记录的是改造前状态，不能用于证明本轮修复已完成。

### 原始总纲

**在 LessonDoc 2.0「JSON 即真源」的基础上，给 deck/manifest 模型做严格加法（坐标层 `frame`、样式层 `style`、页面背景 `bg`、全局层 `globals`、新块 `button / codewalk / group / html`），再造一个运行在平台内、以同源 iframe 承载真实引擎渲染的三栏低代码编辑器（左元素栏 / 中画布 / 右属性栏），让教师不写一行代码就能拖、选、改、组、存。旧流式版式与 AI 生成通道零改动继续工作；编辑器不进离线包；所有保存都经过既有 `validate_deck` 降级校验。**

---

## 1. 现状深度分析（改动前必读）

### 1.1 文档模型是「流式块」，不是「画布对象」

- 课次页 `lesson_N.html` = 壳 + 内嵌 deck JSON；`deck-engine.js` 把 `slides[]` 渲染进 1280×720 虚拟画布（`slides.js` 负责 `transform:scale` 缩放、翻页、fragment 分步、HUD）。
- 7 种版式（`title/section/content/two-col/center/grid/end`）里，内容页的 `blocks[]` 在 `.slide-body`（flex column，`justify-content: safe center`）里**自上而下堆叠**；`two-col` 分左右两列；`grid` 是 12×8 网格的 `areas[]`。**没有任何块带坐标**。内容溢出时靠 `fitSlide` 整体缩小 `.slide-body`。
- 17 种块（`spec.BLOCK_TYPES`）由 `BLOCKS[type](b)` 渲染；渲染结果的根节点 id 只在 `b.id` 存在时才写入（`renderBlock` 末尾）。**绝大多数 AI 生成的块没有 id**，这是编辑器做选择/引用/动作绑定前必须补齐的。
- 首页 `main.html` = 壳 + course.json（manifest），`renderHome` 固定顺序渲染 hero → 总览思维导图 → 阶段卡片墙 → tabs → footer，**没有任何可配置的顺序/显隐/样式**。

**结论**：需求 4/6/10/11/12（拖到任意位置、属性栏改字体渐变描边、圈选、分组等比缩放、粘贴到旁边不出界）本质上要求「对象有坐标」。最省事的错误做法是把所有页面改成绝对定位——那会毁掉 AI 生成的流式内容与 article 手机版式。正确做法是**双轨并存**：流式页继续流式（编辑器提供排序/插入），新增「自由画布」能力（新版式 `canvas` + 任意页面的浮层 `overlays` + 全课全局层 `globals`），三者共用一套「带 `frame` 的块」结构（§4.2）。

### 1.2 可逆性与校验链已经齐备，编辑器只需接上

- `render.extract_embedded_json(html)` 无损反抽取 deck；`render.render_lesson_html(deck)` 确定性回写。**编辑器读写文档的全部工作 = 读 JSON、改 JSON、经 `validate_deck` 后写回**，不需要新的文件格式。
- `validate.py` 是「丢块不丢页」的降级校验器：未知块 → 占位 callout，坏字段 → 丢块并告警。新增的 `frame/style/bg/overlays/globals` 与新块类型必须在这里登记（§4.11），否则编辑器保存的内容会被当作未知块降级掉。
- `pack_service.write_lesson_files` / `write_manifest` 已是「校验 → 渲染壳 → 覆盖包内文件 → 刷缓存」的唯一写路径。编辑器保存端点直接复用（§6.2）。
- 单页 AI 重写（R2）`rewrite_slide_with_ai` 有「落盘前预检页数不减」的护栏——编辑器保存沿用同一护栏思想：**任何会让页数变少的降级都拒绝保存并告诉教师哪一页出了什么问题**（§6.2）。

### 1.3 引擎资产随包分发，新特性需要「刷新引擎」

- 包内 `assets/` 是生成时刻的引擎副本；平台引擎升级后 `assets_outdated` 指纹比对（R5）→ 材料页徽标 + 面板「⬆ 引擎可更新」按钮。
- 编辑器会向引擎加渲染能力（定位、样式、背景、按钮动作、代码步进…），**编辑后的文档只有在包内引擎足够新时才能正确渲染**。所以保存端点必须在检测到 `assets_outdated` 时**自动刷新包内引擎**（§6.2 第 4 步）——这是需求 14「能自动的就自动」的第一条落地。
- 引擎红线保持不变：零外部依赖、`file://` 离线可用、ES5 口径（与 slides.js 一致）。**编辑器本身不进包**（它依赖平台设计系统、登录态、API），只有「渲染新模型所需的运行时」进包。

### 1.4 宿主页面与入口

- 全屏壳页 `GET /materials/render-view/{id}?path=` + `material_render_shell.html/js`：iframe 直渲 + 工具条（返回/首页/前进/徽章/收起）+ 教师端「✏ 改这一页」（只在 `by-root` 反查到 pack 时出现）+ 白板 idle 加载 + 学生进度心跳。**这是需求 2「文档打开的任意页面进入编辑」的挂点**：工具条加「✎ 编辑」，读 iframe 当前 `lesson_N` 与 `#/页码` 深链进编辑器（与「改这一页」的 `currentLocation()` 同源）。
- 材料页 `materials_manage.js` 包根卡片：徽标「学习文档包 2.0」+ 进度 + 「管理课次」（按需 `import()` 向导，`window.openLessonDocPackManager`）；向导管理面板 `renderManageView` 逐课行有「AI 生成/重写」「排除」。**这是需求 2「管理端材料页进入编辑」的挂点**：卡片加「编辑文档」、逐课行加「编辑」、面板顶部加「编辑首页」。
- 课程页向导、课堂页分流入口不变；可选加深链（§7.4）。

### 1.5 旧手写包与不可编辑材料

- 判别：`<html data-lessondoc="2.0">` 有 → 配置驱动包；无 → 旧手写包（`render.is_lessondoc_html`）。`pack_service.get_pack_by_root` 有登记行才是「在线文档」。
- 旧包升级通道已有：`POST /api/lessondoc/packs/import-legacy`（`dry_run` 先看告警；原包不动，结果落新包；已知有损：stepper 解说词、阶段分组）。**需求 15「先转换再编辑」= 把这条通道包装成一个对话框流程**（§7.3），不是新造迁移器。
- Markdown 材料有自己的源码编辑（`/api/materials/{id}/content`，`is_editable_material` 只放行文本类）；非包的散 HTML、PDF、图片等不在编辑器范围——统一给「不能编辑」的明确提示与原因（§7.3 判定表）。

### 1.6 可复用的平台部件

- 白板升级（2026-09-02）留下的 `static/js/whiteboard/popover.js`（Popover 出入动效、点外关闭、定位）与 `panels/confirm_popover.js`（内联确认）——**编辑器优先复用**；若耦合了 `twb-` 类名，则按同一模式提炼为通用 `static/js/ui_popover.js`，白板与编辑器共用（施工 E2 决定，见 §10）。
- `static/js/ui.js` 的 `escapeHtml/showToast`；全站说明浮窗 `window.LanShareExplanation`（功能说明一律接它，不再造 tooltip）。
- 设计系统：`--ls-*` 令牌、五档圆角、六档字号、`.ls-anim-*` 动效套件、`prefers-reduced-motion` 约定；新 CSS 追加在 `ui-system.src.css` 末尾并 `npm run build:css`。
- 文件写入：文本走 `_store_markdown_bytes`（sha256 去重落全局文件）；二进制上传走 `library.py` 的 `_save_payload_bytes_globally` + `infer_material_profile`。
- 测试：`tests/test_lessondoc_service.py`（33 项）、vitest（`static/js/whiteboard/*.test.js` 模式）、Playwright p03 harness（`launch.json` 的 `p03-qa`/`p03-wb`，QA 自带 mock AI）。

### 1.7 铁律（沿用教学域总纲 + LessonDoc 施工文档）

1. **不改任何现有表的现有列**；新表走 polls 模式（engine-aware ensure + `_SCHEMA_READY` + `schema.py` 双引擎 + `RUNTIME_ENSURED_SCHEMA_MODULES` 豁免），**不挂 `class_offering_id`**。
2. 旧手写包、旧 AI 生成的流式 deck、article 手机版式、学生侧权限链、确定性绑定——**全部零回归**。
3. 新路由后重生成 `tests/fixtures/p02_route_snapshot.json`；改 `static/js/*.js` 要 bump `?v=`（含 `materials-manage-page.tsx`/`classroom-page.tsx` 与契约测试 `test_process_material_workflow_contract` 的版本串断言）。
4. 引擎资产清单 `spec.ASSET_FILES` 变更 → `assets.py` 指纹自动变化 → 老包会显示「引擎可更新」，这是预期行为，不是 bug。
5. 编辑器写入的一切内容都要过服务端 `validate_deck / validate_manifest`；前端校验只是体验加速，不是安全边界。
6. 直接查懒建 runtime 表的地方必须自带 ensure（既有教训）。

---

## 2. 需求逐条对照（需求 → 方案落点）

| # | 需求 | 落点 |
|---|------|------|
| 1 | 低/无代码编辑界面 | §3 编辑器整体架构，§5 交互设计 |
| 2 | 任意页面进入 + 材料页进入 | §7.1 壳页入口，§7.2 材料页/向导入口 |
| 3 | 首页与课次页区分 | §3.3 两种编辑模式；§5.8 首页编辑器 |
| 4 | 左元素栏/中画布/右属性栏；文字样式；页面背景与填充图 | §3.1 版式；§4.3 style；§4.4 bg；§5.5 属性面板 |
| 5 | 属性栏代码模式（JSON / HTML） | §5.6 代码模式（JSON 全量可写；HTML 走「自定义 HTML 页」转换） |
| 6 | 拖入元素 + 应用到所有页面（首页除外） | §4.5 globals；§5.3 拖放；§5.5「应用到所有页面」开关 |
| 7 | 按钮绑定动作（显示/隐藏/移动） | §4.6 动作模型 + 引擎动作运行时；§5.7 动作构建器 |
| 8 | 代码逐行执行示例元素 + 执行按钮可绑额外事件 | §4.7 codewalk 块 |
| 9 | 元素分类 + 淡灰分隔 | §5.2 元素栏 |
| 10 | 圈选虚线框删除；多选显示共有属性 | §5.4 选择系统；§4.10 属性分组归类 |
| 11 | 分组/自定义元素/拆分/删除 | §4.2 group 块；§5.9 自定义元素库（新表） |
| 12 | Ctrl+C/V 复制到旁边不出界 | §5.10 剪贴板 |
| 13 | Delete 删除 | §5.10 快捷键表 |
| 14 | 系统 chrome 不可编辑；加页/删页；傻瓜式 | §3.4 系统区与内容区边界；§5.11 页面操作；§8 自动化清单 |
| 15 | 旧文档需先转换；不能编辑要提示 | §7.3 可编辑性判定 + 转换流程 |
| 16 | 美观 | §5.1 视觉规范 |
| 17 | 其他改进 | §8（自动保存草稿、版本快照、AI 改这一页/润色此元素、对齐吸附、素材上传、缩略图页轨、快捷键面板） |
| 18 | 逻辑完整、不破坏现有功能 | §1.7 铁律；§6 后端闭环；§10 施工；§11 风险；§13 checklist |

---

## 3. 总体架构

### 3.1 编辑器页面（平台内新页面）

```
GET /materials/lessondoc-editor/{pack_id}?lesson=N&slide=K     (lesson=0 → 首页 main.html)
templates/lessondoc_editor.html  (extends base.html，无侧栏，全屏三栏)

┌────────────────────────────────────────────────────────────────────────────────┐
│ 顶栏  ← 返回 │ 《课程名》· 第3课 · 第 7/24 页 │ 撤销 重做 │ 预览 │ AI改这一页 │ 保存 ●未保存 │
├──────────┬───────────────────────────────────────────────────┬─────────────────┤
│ 元素栏   │  画布区（同源 iframe：/materials/render/{root}/lesson_3/lesson_3.html?edit=1）│ 属性栏          │
│ 文本     │  ┌─────────────────────────────────────────────┐   │ [属性] [代码]   │
│ ──────── │  │  引擎真实渲染的 1280×720 舞台（缩放居中）      │   │                 │
│ 卡片列表 │  │  + 编辑层（选框/手柄/圈选虚线/吸附线/插入线）   │   │ 无选中：页面属性 │
│ ──────── │  └─────────────────────────────────────────────┘   │ 单选：元素属性   │
│ 代码     │  页轨（缩略图 1 2 3 … [+]）                          │ 多选：共有属性   │
│ ──────── │                                                   │                 │
│ 图示     │                                                   │                 │
│ ──────── │                                                   │                 │
│ 互动     │                                                   │                 │
│ ──────── │                                                   │                 │
│ 我的元素 │                                                   │                 │
└──────────┴───────────────────────────────────────────────────┴─────────────────┘
```

- **画布 = 真实引擎**：iframe 加载包内真实页面（同源 `/materials/render/{root}/…`），由包内 `assets/` 引擎渲染。所见即所得，与学生看到的完全一致；编辑器不维护第二套渲染器（消灭「编辑态和展示态不一样」这一整类 bug）。
- **状态在父页**：deck JSON、选择集、剪贴板、撤销栈都在编辑器（父窗口）；iframe 只是「渲染器 + 命中检测器」。父页通过同源直接访问 `iframe.contentWindow.LESSONDOC.edit`（§3.2 桥接 API），不用 postMessage。
- **编辑层在 iframe 里**：选框、手柄、圈选虚线、吸附线、插入指示线都画在 iframe 内当前 `.slide` 下的 `.ld-edit-layer`，天然处于 1280×720 坐标系并随舞台一起缩放——所有几何计算只在画布单位下进行，不做屏幕坐标换算（仅指针事件进入时换算一次）。

### 3.2 引擎桥接 API（进包的运行时，`static/lessondoc/2.0/interact.js` 新增资产）

`?edit=1`（或父页调用 `mount`）进入编辑态后：

```js
window.LESSONDOC.edit = {
  version: "2.1",
  mount({ slide })            // 进入编辑态：隐藏 HUD/箭头/进度条/首页角标，禁用 slides.js 键盘与触屏翻页，
                              // 展开全部 fragment，拦截所有 <a> 与按钮动作(preventDefault)，加 <html class="ld-editing">
  render(deck, slideIndex)    // 全量重渲 deck 并切到指定页(不改 hash)；返回 {slideCount}
  patchSlide(slideJson, i)    // 只重渲第 i 页(属性面板拖滑块时的高频路径)
  rects(ids?)                 // {id: {x,y,w,h}} 画布单位；缺省返回当前页全部带 id 的块
  hitTest(x, y)               // 画布坐标 → 最内层块 id(含 group 子元素，附祖先链)
  geometry()                  // {scale, originX, originY}: iframe client 坐标 ↔ 画布坐标
  layer()                     // 当前页的 .ld-edit-layer 元素(编辑器往里画东西)
  select(ids)                 // 高亮选中态(outline)，仅视觉
  previewActions(bool)        // 允许在编辑态试跑 button/codewalk 动作(属性面板「试一下」)
  measureFlowFrames(i)        // 流式页 → 自由页转换时，返回每个块的渲染框(用于赋 frame)
  on(event, fn)               // "ready" | "resize" | "keydown"(转发给父页快捷键)
}
```

- 渲染 API 直接复用 `renderDeck/renderSlide`（引擎内部已有），只是补「清空 `.deck` 后重渲」与「不触发 hash」的入口。
- 桥接层 ≤ 250 行，ES5，随包分发；无 `?edit=1` 时不执行任何东西（学生侧零开销）。

### 3.3 两种编辑模式（需求 3）

| | 课次页（lesson_N.html） | 首页（main.html） |
|---|---|---|
| 页面形态 | PPT 舞台，1280×720 固定画布 | 响应式长页面（hero / 导图 / 卡片墙 / tabs / footer） |
| 真源 | deck JSON | course.json（manifest） |
| 页导航 | 页轨缩略图 + 加页/删页/复制/拖排 | 无「页」概念：区块列表（显隐/排序） |
| 自由定位 | 有（`canvas` 版式、`overlays`、`globals`） | 无（长页面不适合绝对坐标；只做流式与样式） |
| 元素栏 | 全部元素 | 仅流式元素（text/cards/table/callout/tabs/details/code/media/diagram/quiz/reveal/button/codewalk） |
| 页面属性 | 版式/标题/小节/背景/备注 | 课程信息/统计显隐/区块顺序/主题/背景/额外区块 |
| 全局元素 | `deck.globals`（本课所有页） | 不参与（需求 6「除了首页」） |

### 3.4 系统区与内容区的边界（需求 14）

**系统区（不可选、不可删、不进 JSON）**：页眉小节名/课程名、页脚课程名/页码、进度条、翻页箭头、HUD（比例/总览/全屏/主题/文档模式）、首页角标、结尾页「返回课程首页」链接、总览网格。它们由 `slides.js`/`deck-engine.js` 注入，编辑态下 `mount()` 隐藏 HUD 类控件、保留页眉页脚但加 `pointer-events:none` 灰显，命中检测跳过 `.slide-chrome-*`。
**内容区（可编辑）**：`.slide-title/.slide-sub`（页面属性里改）、`.slide-body` 内所有块、`overlays`、`globals`、`slide-bg`。
教师看到的页码/页数由页轨与页眉页脚自动维护，永远不需要手改。

---

## 4. 文档模型扩展（deck JSON 2.1，严格加法）

> 版本策略：`spec` 保持 `lessondoc/2.0`（主版本不变，旧引擎读新文档时未知字段被忽略、未知块降级为占位卡——符合既有健壮性口径）。引擎文件头注释标 2.1 能力。**不引入 `lessondoc/3.0`**，避免触发主版本迁移逻辑。

### 4.1 稳定标识

- 每个 slide 加 `id`（`s_` + 6 位 base36），每个块加 `id`（`b_` + 6 位）。编辑器打开文档时对缺失 id 的 slide/块**一次性补齐**（补 id 不标脏；首次保存时随其他改动落盘；未改动就不保存）。
- `validate` 保证 id 唯一：重复 id 后者重排；非法字符剥除（沿用引擎 `replace(/[^\w-]/g,"")`）。
- 引擎渲染时块根节点写 `id` 与 `data-ld-id`（现只写 id；加 `data-ld-id` 便于命中检测不受子元素 id 干扰）。

### 4.2 定位块（positioned block）与三个承载位

2026-09-03 续建补充：普通定位块也允许 `natural:{w,h}`（有限数值 1—10000）。渲染器在 frame 内按 frame/natural 缩放内容；拆组保留原始 natural 或原子块尺寸，从而同时保持字体、内边距、边框和复杂组件。拆组使用矩阵分解；检测到斜切、整体样式/显隐/动作或外部动作指向组合时明确拒绝，要求先调整，不用近似尺寸掩盖信息损失。组合默认均匀拖动缩放；精确属性仍可改变宽高，之后能否拆组由矩阵可表达性决定。

```jsonc
// 任何块都可以附加 frame；有 frame 的块叫「定位块」
{ "type": "text", "id": "b_k3x9a2", "md": "…",
  "frame": { "x": 120, "y": 80, "w": 480, "h": 120, "r": 0, "z": 3 } }   // 画布单位 px；r 角度；z 层序
```

承载位：

| 位置 | 字段 | 语义 |
|---|---|---|
| 自由页 | `slide.layout = "canvas"`，`slide.objects[]` | 全页自由排版，无流式列；`title/sub` 仍可用（作为页眉标题） |
| 任意页浮层 | `slide.overlays[]` | 叠在流式内容之上的定位块（贴纸、标注、按钮、箭头…） |
| 全课全局层 | `deck.globals[]` | 每页都渲染的定位块；`skipCovers`（默认 true：封面/章节/结尾页不显示）、`excludeSlides:[slideId]` |
| 分组 | `type:"group"` 块 | `children[]` 为定位块，坐标相对组框；组框 `frame.w/h` 与 `natural:{w,h}` 之比即等比缩放系数 |

- 渲染：`.slide` 已是 `position:absolute` 容器，定位块渲染为 `<div class="ld-pos" data-ld-id style="left:x;top:y;width:w;height:h;transform:rotate(r);z-index:z">` 内嵌 `BLOCKS[type]` 结果。`.slide-body` 的 flex 列不受影响。
- `group` 渲染为 `.ld-pos.ld-group > .ld-group-inner{width:natural.w;height:natural.h;transform:scale(w/natural.w, h/natural.h);transform-origin:0 0}`，子块用相对坐标绝对定位。**等比缩放连文字一起缩**，不需要逐子块改字号（需求 11「一起放大缩小」）。
- article 版式（手机长文）：`objects/overlays` 按 `z` 升序作为流式块渲染（丢弃坐标），`globals` 不渲染；`group` 展开子块。**手机自学不受影响**。
- 限额（spec）：每页 `objects+overlays ≤ 40`，`globals ≤ 12`，`group` 嵌套 ≤ 2 层，`frame` 数值裁剪到 `[-200, 1480]×[-200, 920]`（允许略出界做出血，但不允许飞走）。

### 4.3 样式层 `style`（需求 4「文字大小字体粗细颜色渐变描边…」）

```jsonc
"style": {
  "font": "sans" | "serif" | "kai" | "mono" | "rounded",       // 白名单 → 离线可用字体栈(见下)
  "size": 28,                 // px(画布单位) 12–160
  "weight": 400|500|600|700|800,
  "italic": true,
  "color": "#0f172a" | "primary|primary-dark|ok|warn|err|muted|text|white",   // 语义色名优先
  "gradient": { "from": "#…", "to": "#…", "angle": 135 },     // 文字:background-clip 渐变；盒子:背景渐变
  "stroke": { "width": 1.5, "color": "#…" },                  // -webkit-text-stroke
  "shadow": "none|soft|hard|glow",                            // 预设，不开放自由值
  "align": "left|center|right",
  "lineHeight": 1.4, "letterSpacing": 1,
  "opacity": 0.9,
  "bg": "#…|语义色|transparent", "bgGradient": {…},
  "border": { "width": 2, "color": "#…", "radius": 12, "style": "solid|dashed" },
  "padding": 16
}
```

- **应用方式**：引擎 `applyStyle(node, style)` 把白名单键翻译成内联样式（键白名单 + 值正则：颜色 `^#[0-9a-f]{3,8}$|语义名`；数字裁剪范围；字体只从映射表取）。**不接受任意 CSS 字符串**，杜绝注入。
- 字体栈映射（离线可用、机房无外网）：`sans`=`var(--font)`，`serif`=`"Songti SC","SimSun","Noto Serif CJK SC",serif`，`kai`=`"KaiTi","STKaiti","Kaiti SC",serif`，`mono`=`var(--mono)`，`rounded`=`"Yuanti SC","YouYuan",system-ui`。
- 十六进制颜色在 `style` 里**允许**（这是教师手工设计，不是 AI 生成；换主题时手工色不跟随是可接受的、可见的选择；属性面板色板默认给主题语义色，自定义色放在「更多」里，引导优先用语义色）。`svg.body` 里的硬编码色规则不变。
- 适用范围：每种块声明自己接受的样式子集（§4.10）。

### 4.4 页面背景 `bg`（需求 4「页面本体属性」）

```jsonc
"bg": {
  "color": "#…|语义色",
  "gradient": { "from": "#…", "to": "#…", "angle": 160 },
  "image": { "src": "media/bg1.jpg", "fit": "cover|contain|stretch|tile|custom",
             "scale": 120, "x": 50, "y": 50, "rotate": 0, "opacity": 0.85, "blur": 0 },
  "tint": { "color": "#000", "opacity": 0.3 }      // 图上压一层色，保证文字可读
}
```

- 渲染为 `.slide > .slide-bg`（`position:absolute; inset:0; z-index:0; overflow:hidden`）内的 `.slide-bg-img`，用 `transform: translate(-50%,-50%) rotate(r) scale(s)` 实现旋转/缩放/定位；`fit` 的四档映射到 `background-size/repeat`。封面/章节页自带渐变，`bg` 存在时覆盖。
- `image.src` 沿用 media 块规则：**只允许包内相对路径**（`media/…` 或 `../assets/media/…`），上传走 §6.2 media 端点。
- `deck.bg` 作为全课默认背景（页级 `bg` 覆盖）；manifest 的 `home.bg` 同构（长页面用 `background-attachment` 固定）。

### 4.5 全局层 `deck.globals`（需求 6）

```jsonc
"globals": [
  { "type": "media", "kind": "image", "src": "../assets/media/logo.png", "id": "g_a1b2c3",
    "frame": { "x": 1120, "y": 640, "w": 120, "h": 48, "z": 50 },
    "skipCovers": true, "excludeSlides": ["s_x9y8z7"] }
]
```

- 引擎在 `renderDeck` 后对每个 `section.slide` 追加 `globals`（克隆渲染，id 加 `-p{n}` 后缀避免重复，`data-ld-gid` 指回原 id）；编辑态对全局块的任何修改回写到 `deck.globals[i]` 并重渲全部页。
- 属性面板开关「应用到所有页面」：开 → 块从 `overlays/objects` 迁到 `globals`（保留 frame）；关 → 迁回当前页 `overlays`。带「本页不显示」复选框写 `excludeSlides`。首页（manifest）不存在这个开关。

### 4.6 动作模型与 `button` 块（需求 7）

```jsonc
{ "type": "button", "id": "b_btn01", "label": "看答案", "icon": "👀",
  "variant": "primary|outline|ghost|link", "size": "md",
  "style": {…},
  "actions": [
    { "do": "show",   "target": "b_ans01", "ms": 400 },
    { "do": "hide",   "target": "b_hint1" },
    { "do": "toggle", "target": "b_x" },
    { "do": "move",   "target": "b_pkt", "dx": 240, "dy": 0, "ms": 600, "ease": "inout" },   // 相对位移
    { "do": "moveTo", "target": "b_pkt", "x": 800, "y": 300, "ms": 600 },                   // 绝对(画布单位)
    { "do": "goto",   "slide": 12 },                       // 跳页(1 起)
    { "do": "next" }, { "do": "prev" },
    { "do": "run",    "target": "b_cw01" },                // 触发 codewalk 运行
    { "do": "reset",  "target": "b_cw01" }
  ],
  "once": false }
```

- 通用字段 `hidden: true`：初始隐藏（供 show/toggle）；`hidden` 的块在编辑态半透明显示并带「已隐藏」角标，展示态 `display:none`。
- 引擎动作运行时 `runActions(steps, ctx)`（`interact.js`）：目标解析先当前页（含全局克隆）再全课；`move/moveTo` 对流式块用 `transform: translate` 过渡，对定位块直接改 `left/top` 过渡；`goto/next/prev` 调 slides.js 暴露的 `window.SLIDES.goTo/next/prev`（slides.js 需补一个 3 行的导出）。`prefers-reduced-motion` 时 `ms=0`。
- 其他块也可挂 `actions`（`on:"click"`）：cards 的单张卡、media 图片、text——属性面板「点击时…」对所有块开放，`button` 只是长得像按钮的默认载体。
- 校验：`do` 白名单；`target` 必须是本 deck 内存在的 id（不存在 → 剔除该步并告警）；每块 ≤ 12 步。

### 4.7 `codewalk` 块（需求 8：代码逐步执行示例）

```jsonc
{ "type": "codewalk", "id": "b_cw01", "lang": "python", "title": "for 循环是怎么跑的",
  "lines": [
    { "code": "total = 0",                 "note": "先准备一个累加器" },
    { "code": "for i in range(1, 4):",      "note": "i 依次取 1、2、3" },
    { "code": "    total += i",            "out": "total = 1",  "note": "第一次循环" },
    { "ref": 2,                            "out": "total = 3",  "note": "第二次循环" },   // ref: 高亮第 2 行(0 起)而非新增源码行
    { "ref": 2,                            "out": "total = 6",  "note": "第三次循环" },
    { "code": "print(total)",              "out": "6" }
  ],
  "loop": false, "arrow": true, "speedMs": 900, "autoStart": false,
  "runLabel": "▶ 运行", "showOutput": true, "showNotes": true,
  "actions": [ { "do": "show", "target": "b_summary" } ],    // 运行按钮的额外事件(需求 8 末句)
  "style": {…} }
```

- 语义：`lines[]` 里带 `ref` 的行是「执行轨迹」而非源码行——渲染时源码只出现一次（`ref` 行不显示在代码区，只驱动高亮/输出），因此**循环体重复执行可以逐次演示而不必重复贴代码**。没有 `ref` 的行既是源码行也是执行步。
- 运行时（`interact.js` 的 `CodewalkPlayer`）：控制条「▶ 运行 / ⏸ 暂停 / ⏭ 单步 / ↺ 重置」+ 步数；当前步高亮 `.cw-line.active` + 左侧箭头 `→`（`arrow:false` 则只高亮）+ 输出区逐条追加（带淡入）+ 解说区显示 `note`；`loop` 到末尾回到第一步；点「运行」时先执行 `actions`（额外事件），再开始播放。fragment 兼容：块带 `step` 时仍按分步登场。
- 校验：`lines ≤ 60`，每行 `code ≤ 200` 字，`speedMs ∈ [200, 5000]`，`ref` 必须指向存在的源码行；`lang` 只用于展示标签，不做语法高亮（零依赖红线；后续若要高亮可内置一个 2KB 的关键字着色器，本期不做）。
- 属性面板：行编辑器（表格：代码/输出/解说/指向行；加行、删行、上下移）、循环、箭头、速度滑块、运行按钮文案、额外动作（复用动作构建器）。

### 4.8 `html` 块与「自定义 HTML 页」（需求 5 的 HTML 源码编辑落点）

```jsonc
{ "type": "html", "id": "b_h1", "body": "<div class='x'>…</div>", "css": ".x{…}", "frame": {…}? }
```

- 服务端 `validate_html.py` 用 lxml 解析 → 标签白名单（div/span/p/h1-h6/ul/ol/li/table 系/b/i/em/strong/code/pre/img/svg 子集/br/hr/blockquote/a）、属性白名单（class/style/src/href/alt/title/width/height + svg 几何属性）、`style` 属性值过滤（禁 `url(` `expression(` `javascript:` `@import`）、`src/href` 只允许包内相对路径或 `#`；`css` 每条规则的选择器强制加作用域前缀 `.ld-html-{id}`（防止污染全页）。前端引擎再做一次 `sanitizeSvg` 同级的正则剥除（双保险）。
- 「HTML 源码」标签页的工作方式（§5.6）：显示当前页**渲染后的 HTML**（只读参照）+ 「转换为自定义 HTML 页并编辑」按钮 → 确认后该页变为 `layout:"canvas"`，`objects=[一个 html 块占满画布]`，源码可写。这样 HTML 直编是**显式、可逆（撤销栈）、有边界**的，不会让 JSON 真源与 HTML 分叉。

### 4.9 首页 manifest 扩展（§5.8 首页编辑器的数据）

```jsonc
"home": {
  "bg": {…同 §4.4…},
  "style": { "heroGradient": {…}, "cardRadius": 12 },
  "sections": [                                    // 顺序即渲染顺序；缺省=现状顺序
    { "key": "hero",    "hidden": false, "stats": ["totalHours","sessionCount","credits","assessment"] },
    { "key": "mindmap", "hidden": false, "title": "课程知识体系总览", "collapsedDepth": 1 },
    { "key": "nav",     "hidden": false, "title": "课次导航" },
    { "key": "blocks",  "hidden": false, "title": "课程说明", "blocks": [ …流式块… ] },   // 新增可编辑区
    { "key": "tabs",    "hidden": false, "title": "课程信息" },
    { "key": "footer",  "hidden": false }
  ]
}
```

`renderHome` 改为按 `home.sections` 顺序/显隐渲染（缺省时行为与现在逐像素一致）。`tabs[]` 内容仍在 `manifest.tabs`，编辑器用同一套流式块编辑。

### 4.10 属性分组归类（需求 10「对各元素的可编辑属性进行归类」）

编辑器 `registry.js` 为每种块声明属性组；多选时取交集：

| 组 | 键 | 适用 |
|---|---|---|
| `identity` | 名称（`name`，仅编辑器显示用）、id 只读、hidden、step、exitStep | 全部 |
| `frame` | x/y/w/h/r/z、锁定比例、对齐（左中右/上中下/等距分布）、应用到所有页面 | 定位块（`canvas/overlays/globals`） |
| `text` | font/size/weight/italic/color/gradient/stroke/shadow/align/lineHeight/letterSpacing | text/bigmark/bignum/cards/timeline/callout/quiz/reveal/button/tasklist/table/details/tabs |
| `box` | bg/bgGradient/border/radius/padding/shadow/opacity | 全部（media/svg/diagram 也有） |
| `actions` | 点击时动作列表 | 全部 |
| `type:*` | 各块自有字段（cards 的 cols/items、table 的 head/rows、diagram 的 nodes/edges、quiz 的选项/答案…） | 单选时显示 |
| `codewalk` | 行编辑器/循环/箭头/速度/文案/额外动作 | codewalk |
| `group` | 拆分分组、存为我的元素、等比缩放开关 | group |

多选（例如 text + cards + button）→ 显示 `identity(hidden/step) + frame(若都为定位块) + text + box + actions`；批量改写对每个块合并 `style`。

### 4.11 校验器变更清单（`validate.py`）

- `BLOCK_TYPES` += `button/codewalk/group/html`；`SLIDE_LAYOUTS` += `canvas`。
- `_validate_block`：通用段处理 `id/frame/style/hidden/actions`（白名单 + 裁剪 + 告警）；`group` 递归子块（深度 ≤2，子块必须有 frame）；`button` 需 label；`codewalk` 需 lines；`html` 走消毒。
- `_validate_slide`：`canvas` 需 `objects` 非空；`content` 的丢页规则改为「`blocks` 与 `overlays` **都**为空才丢」；`overlays` 用 `_validate_blocks` + frame 必填；slide `id` 补齐与去重。
- `validate_deck`：`globals`（frame 必填、限额）、`bg`（结构与路径）、跨页 `actions.target` 存在性检查（收集全 deck id 集后二次过滤）。
- `validate_manifest`：`home` 段（sections key 白名单、blocks 走 `_validate_blocks`、bg）。
- **AI 摘要节取舍**：authoring-guide §4 加 `button`/`codewalk` 的说明（AI 生成互动页时可用，价值高），**不**把 `frame/overlays/globals/canvas/style/html` 写进 AI 摘要节——自由坐标由 AI 写只会产出乱糟糟的排版，这些是人的工具。guide 正文（非摘要节）记录完整契约。

---

## 5. 编辑器交互与视觉设计

### 5.1 视觉规范（需求 16）

- **色彩**：全部走 `--ls-*` 令牌。画布区底色 `hsl(var(--ls-foreground)/0.92)` 深色舞台（让 1280×720 白色页面成为唯一焦点）；左右栏 `hsl(var(--ls-card))`，分隔线 `hsl(var(--ls-border))`；强调色 `--ls-primary`（教师域青绿自动跟随）；选框 `--ls-primary`，圈选虚线 `1.5px dashed hsl(var(--ls-primary))` + `hsl(var(--ls-primary)/0.08)` 填充；吸附线 `--ls-info-strong`；危险操作 `--ls-destructive`。禁 hex 硬编码。
- **版式**：三栏 `240px | 1fr | 300px`（属性栏可拖宽至 420px）；顶栏 48px；元素栏卡片 `grid 2 列`，每项 = 24px 线性图标 + 12px 标签，hover 抬升 2px + 主色描边；分类之间 `1px hsl(var(--ls-border))` 淡灰横线 + 12px 分类小标题（需求 9）。属性栏 = 手风琴分组（默认展开前两组），控件统一 32px 高：分段选择（对齐/字重）、滑块+数值框（字号/不透明度/圆角）、色板（主题 8 色 + 最近 6 色 + 自定义）、渐变编辑器（两色 + 角度盘）、开关。
- **动效**：面板/浮窗 150ms `cubic-bezier(.2,.8,.2,1)` 出入；选框出现 120ms 缩放；拖拽中元素 `opacity .85` + 影子；`prefers-reduced-motion` 全部关闭。复用 `.ls-anim-*`。
- **字体与密度**：面板文字 `--text-sm`，标签 `--text-xs` 大写字距 0.4px；画布单位数值用 `tabular-nums`。
- **空态**：新建空白页时画布中央给一行浅字「从左侧拖入元素，或按 Ctrl+V 粘贴」；元素栏「我的元素」空态「圈选多个元素 → 组合 → 存为元素」。
- **图标**：内联 SVG（stroke 1.75、24 viewBox），与 `material_render_shell.html` 的首页图标同风格；不引入图标字体。
- **响应式**：编辑器桌面优先；宽度 < 1100px 时左右栏折成抽屉（顶栏图标按钮打开），画布保持等比。移动端只读预览，编辑入口不显示。

### 5.2 元素栏（需求 9）

分类与内容（每类之间淡灰横线分隔）：

| 分类 | 元素 |
|---|---|
| 文本 | 标题（text，预设 size 42/weight 700）、正文（text）、金句（bigmark）、大数字（bignum）、提示框（callout） |
| 卡片与列表 | 卡片组（cards）、时间线（timeline）、表格（table）、任务清单（tasklist）、折叠面板（details）、选项卡（tabs）、揭示（reveal） |
| 代码 | 代码块（code）、代码步进（codewalk） |
| 图示与媒体 | 流程图（flow）、时序图（sequence）、架构图（arch）、思维导图（mindmap）、手绘 SVG（svg）、图片/视频/音频（media，拖入即弹上传/选择包内素材） |
| 互动 | 按钮（button）、测验（quiz）、步骤演示（stepper） |
| 自定义 HTML | HTML 片段（html） |
| 我的元素 | 教师自定义元素（§5.9），可删除、可重命名 |

- 每项 hover 显示 240px 预览缩略图（引擎在 iframe 隐藏区渲染该块的默认样例并 `cloneNode` 到父页缩放显示——缩略图与真实渲染同源），点击 ⓘ 走全站说明浮窗。
- 拖动源用原生 HTML5 DnD（`dataTransfer` 携带 `application/x-lessondoc-element`），跨 iframe 拖放同源可行；拖入画布时按 §5.3 规则落位。**点击**元素 = 插入到当前页默认位置（流式页追加到末尾；自由页放在画布中心），满足键盘/触控板用户。
- 顶部搜索框（按名称/别名过滤）。

### 5.3 拖放落位规则（需求 6、傻瓜式）

| 当前页版式 | 拖放行为 |
|---|---|
| `content/center/end` | 指针在 `.slide-body` 列内 → 显示水平**插入线**（按各块中线判定索引）→ 松手插入 `blocks[i]`；指针在列外（页边距/页眉页脚区）→ 作为 `overlays` 定位块，落在指针位置 |
| `two-col` | 同上，先按 x 判左右列 |
| `grid` | 命中某个 `grid-area` → 插入该区块末尾；未命中 → overlays |
| `canvas` | 一律定位块，落在指针位置（中心对齐指针），自动吸附 8px 网格与其他块边缘/中线 |
| 首页 | 只允许落入「课程说明」区块或 tabs 面板（插入线） |

拖入的定位块默认尺寸取该类型的 `defaults().frame`（如 text 480×120、cards 720×260、button 200×56）；落位后若出界自动回推到画布内。按住 `Alt` 拖放强制走 overlays（给高级用户），但不做任何提示依赖——不按也能用。

### 5.4 选择系统（需求 10）

- **单击**选中最内层块（group 内子块需双击进入组、或在组选中态再单击子块）；`Shift+单击` 加选/减选；`Ctrl+A` 全选当前页。
- **圈选**：在空白处按下拖动 → `.ld-edit-layer` 内画 `1.5px dashed` 虚线矩形；松手时选中所有与矩形**相交**的块（流式块用其渲染框；定位块用 frame；group 作为整体）。圈选中按住 `Alt` 改为「完全包含」判定。
- 选中态：块外 2px 主色描边 + 8 个缩放手柄（仅定位块）+ 顶部旋转手柄 + 左上角类型徽标（如「文本」「按钮」「全局」「已隐藏」）。多选：每个块细描边 + 总包围框粗描边 + 只有整体拖动手柄。
- 流式块被选中时：手柄不显示，可 **拖动重排**（拖起 → 插入线 → 松手），`Alt+↑/↓` 也能移动位置。
- 定位块：拖动（吸附：画布中线、其他块边/中线、8px 网格；`Alt` 临时关吸附）、缩放（角手柄等比、边手柄单向、`Shift` 反转等比）、旋转（15° 步进吸附，`Shift` 自由）、方向键 1px / `Shift+方向键` 10px。
- 属性栏随选择集变化：空选 → 页面属性；单选 → 全属性；多选 → 交集（§4.10）。

### 5.5 属性面板（需求 4）

- 顶部：`[属性] [代码]` 分段切换 + 当前对象标题（如「文本 · b_k3x9a2」可改名）。
- **无选中（页面属性）**：
  - 版式（segmented：内容/左右/居中/网格/自由/章节/结尾；封面页不可改）。切换时按 §5.11 迁移规则搬运块并可撤销。
  - 标题 / 副标题 / 小节名 / 教师备注。
  - 背景：颜色 / 渐变 / 图片（上传或选包内素材 → 缩放、位置九宫格 + 拖 xy、旋转、透明度、模糊、压色）；「应用为全课默认背景」。
  - 页面操作：在前插入 / 在后插入（封面页只有「在后」）/ 复制本页 / 删除本页（页轨也有同样操作）。
  - 全局元素列表（本课）：逐项显示、跳到其属性、「本页不显示」。
- **单选**：按 §4.10 分组手风琴。文本组含：字体（5 档预览）、字号滑块（12–160）、字重分段、斜体、颜色（色板）、渐变（开关 + 两色 + 角度）、描边（宽度 + 色）、阴影预设、对齐、行高、字距。框组：填充色/渐变、边框（宽/色/圆角/虚实）、内边距、阴影、不透明度。位置组：x/y/w/h/旋转/层级（上移/下移/置顶/置底）、锁定比例、对齐工具（相对画布）、「应用到所有页面」开关。动作组：§5.7。类型组：该块自有字段的表单（cards 的 items 用可排序行编辑器；table 用网格编辑；diagram 用节点/连线两张表 + 「从文本快速生成」——一行一节点、`A -> B: 标签` 一行一边；quiz 的选项/答案/解析；media 的素材选择与说明；svg 的 viewBox 与 body 文本域；stepper 的步骤表）。
- **多选**：交集分组 + 顶部对齐/分布工具条 + 「组合」「删除」。
- 任何改动 → `state.update()`（不可变更新，产生新 deck 引用）→ `bridge.patchSlide()` 即时重渲 → 标脏；高频控件（滑块）节流 16ms。

### 5.6 代码模式（需求 5）

- `[代码]` 标签下再分 `JSON` / `HTML`：
  - **JSON**：当前页（或选中块、或首页 manifest）的 JSON，等宽文本域（Tab 缩进、Ctrl+Enter 应用、格式化按钮、行号、错误行高亮）；应用时前端 JSON.parse 失败给出行列；成功 → 进入撤销栈并重渲。顶部切换范围：`本页 / 选中元素 / 整课(只读大文本，慎改)`。
  - **HTML**：只读显示当前页渲染后的 HTML（复制按钮，方便拿去别处）+ 「转换为自定义 HTML 页并编辑」（§4.8）；已是 HTML 页则直接可写 `body/css` 两个文本域。
- 零依赖：不引入 CodeMirror/Monaco；文本域 + 30 行的轻量行号/缩进辅助足够。

### 5.7 动作构建器（需求 7）

- 「点击时…」列表：每行 = 动作类型下拉（显示/隐藏/切换/移动(相对)/移动到/跳到第 N 页/上一页/下一页/运行代码步进/重置） + 目标选择器 + 参数（位移 dx/dy 或 x/y、时长、缓动）+ 上下移 + 删除。
- **目标选择器**：下拉列出当前页与全局层所有块（显示 `名称/类型/id`），并提供「在画布上点选」模式（进入拾取态，画布 hover 高亮，点击回填）。目标块若无名称，自动生成「文本 #3」这类可读名。
- 「▶ 试一下」按钮：`bridge.previewActions(true)` 后在编辑态执行一次并可「↺ 复位」（复位 = 重渲当前页）。
- 校验：目标被删除时动作行显示红色「目标不存在」，保存时服务端剔除并告警。

### 5.8 首页编辑器（需求 3）

- 画布 iframe 加载 `main.html?edit=1`，长页面可滚动；编辑层按区块给虚线框与「区块名 · 显隐 · 上移/下移」悬浮把手。
- 元素栏只显示流式元素；拖放只允许落入「课程说明」区块与 tabs 面板。
- 属性面板（无选中 = 首页属性）：课程信息（名称/代码/学分/学时/考核/导语——编辑器写 manifest.course，**不回写 courses 表**，文案注明「只影响学习文档首页展示」）、统计卡显隐、区块顺序与显隐、主题、背景、教材信息、阶段分组（复用向导 `parseStagesText`）。
- 保存 → `PUT /api/lessondoc/packs/{id}/manifest`（§6.2）。

### 5.9 分组与自定义元素（需求 11）

- 多选 → `Ctrl+G` / 工具条「组合」→ 生成 `group`：frame = 各块 frame 并集，子块坐标改为相对；`natural = {w,h}` 记录生成时尺寸。**只有定位块能组合**；若选中包含流式块，提示「先把元素转为自由元素」并提供一键转换（把该流式块从 blocks 移到 overlays，frame 用 `measureFlowFrames` 测得）。
- 选中组：整体拖/缩（子块等比）；属性栏显示交集 + 「拆分分组」（`Ctrl+Shift+G`，子块坐标换回绝对，若组被缩放则把缩放烘焙进子块 frame 与 `style.size`）+ 「存为我的元素」。
- **存为我的元素**：弹出命名（默认「元素 N」）+ 分类（默认「我的元素」）→ `POST /api/lessondoc/custom-elements`（payload = 组 JSON 深拷贝、去掉 id、去掉 actions 里指向组外的目标并提示）+ 自动生成缩略图（前端把子块按类型画成带色块的示意 SVG，60×40，存 `thumb_svg`）。也支持把选中的组**拖到元素栏「我的元素」区**（drop zone 高亮）触发同一流程。
- 使用：从元素栏拖入 → 深拷贝 payload、为所有块与子块分配新 id、落位。**编辑细节需先拆分分组**（与需求一致：自定义元素实例落地后就是普通 group）。
- 删除自定义元素：元素栏项悬浮 ✕ → 内联确认浮窗 → `DELETE`。已放入文档的实例不受影响（payload 已拷贝）。

### 5.10 剪贴板、删除与快捷键（需求 12、13）

| 键 | 行为 |
|---|---|
| `Ctrl+C` / `Ctrl+X` | 复制/剪切选中块（含 group 与其全部属性）到内存剪贴板 + `localStorage['lessondoc-clipboard']`（跨课次/跨标签页粘贴） |
| `Ctrl+V` | 粘贴：定位块 → 原位置偏移 `(+24,+24)`，超出画布右/下边界则回折到 `(24,24)` 起再找空位（与已有块框不重叠优先，最多尝试 8 个候选位）；流式块 → 插到原块之后（若原块不在当前页则追加末尾）；全局块粘贴为普通 overlays（不自动变全局） |
| `Ctrl+D` | 原地复制（同 V 的偏移规则） |
| `Delete` / `Backspace` | 删除选中（焦点在输入控件内时不触发）；删除全局块弹内联确认「将从所有页面移除」；删除被动作引用的块 → 提示引用数并继续（服务端保存时剔除失效动作） |
| `Ctrl+Z` / `Ctrl+Y` / `Ctrl+Shift+Z` | 撤销/重做（deck 级快照 + 选择集，上限 100 步；快照为结构共享的新对象，不深拷贝整 deck） |
| `Ctrl+S` | 保存 |
| `Ctrl+G` / `Ctrl+Shift+G` | 组合/拆分 |
| `Ctrl+A` | 全选本页 |
| `Esc` | 清空选择 / 退出拾取态 / 关闭浮窗 |
| 方向键 / `Shift+方向键` | 移动 1px / 10px |
| `Alt+↑/↓` | 流式块上下移位 |
| `PageUp/PageDown` / `Ctrl+←/→` | 上一页/下一页 |
| `?` | 快捷键面板 |

快捷键监听在父页；iframe 内的 keydown 由桥接层 `on("keydown")` 转发。

### 5.11 页面操作（需求 14）

- 页轨（画布下方，可折叠）：首期用「页号 + 版式图标 + 标题」文字卡片；缩略图（复用 slides.js `openOverview` 的克隆缩放法在父页隐藏 iframe 中按需刷新）作为 E6 打磨项。
- 操作：`+` 在后插入（默认 `content` 空页；封面页只能在后插）、悬浮菜单：在前插入、复制、删除（内联确认；本课至少保留封面与 1 页内容）、拖拽排序（封面固定第一、结尾页固定最后，拖不过去）。
- 版式切换迁移规则：`content↔two-col`（blocks ↔ left+right 对半）、`content/two-col → grid`（每块一个 area，纵向平分）、`* → canvas`（`measureFlowFrames` 测框后全部转 objects）、`canvas → content`（objects 按 y 再 x 排序变 blocks，坐标丢弃，弹确认「将丢失自由位置」）。所有迁移进撤销栈。
- 页码/页数/页眉页脚由引擎维护；编辑器不提供任何相关输入。

---

## 6. 后端设计

### 6.1 数据模型（新增两张 runtime 表，polls 模式，不挂 offering）

```sql
CREATE TABLE IF NOT EXISTS lessondoc_custom_elements (
  id           <engine id>,
  teacher_id   INTEGER NOT NULL,
  name         TEXT NOT NULL,
  category     TEXT NOT NULL DEFAULT '我的元素',
  payload_json TEXT NOT NULL,            -- group 块 JSON(已 validate)
  thumb_svg    TEXT NOT NULL DEFAULT '', -- ≤ 8KB 示意图
  created_at   TEXT NOT NULL, updated_at TEXT NOT NULL      -- ISO-8601
);
CREATE INDEX IF NOT EXISTS idx_ld_custom_elements_teacher ON lessondoc_custom_elements(teacher_id, updated_at);

CREATE TABLE IF NOT EXISTS lessondoc_doc_revisions (
  id          <engine id>,
  pack_id     INTEGER NOT NULL,
  lesson_no   INTEGER NOT NULL,          -- 0 = 首页 manifest
  source      TEXT NOT NULL,             -- editor|ai_generate|ai_rewrite|ai_slide|import|restore
  doc_json    TEXT NOT NULL,             -- 保存前的旧版本(用于恢复)
  summary     TEXT NOT NULL DEFAULT '',  -- 「第 7 页改了 3 处」/「AI 重写第 2 页」
  created_by  INTEGER NOT NULL,
  created_at  TEXT NOT NULL              -- ISO-8601
);
CREATE INDEX IF NOT EXISTS idx_ld_revisions_doc ON lessondoc_doc_revisions(pack_id, lesson_no, id);
```

- 版本快照**每课保留最近 20 条**（插入后按 id 倒序裁剪），单条 doc_json ≤ 2MB（超限不存并告警）。AI 生成/重写链路也顺手写一条（`generate.py` 落盘前），教师在编辑器「历史」里能一键回到 AI 原版——这是需求 17 的重要闭环：**可视化编辑不再是单行道**。
- 文件：`classroom_app/db/schema_lessondoc_editor.py`（两表 + `ensure_lessondoc_editor_schema` + `reset_schema_ready_for_tests`），`schema.py` 双引擎注册 + `RUNTIME_ENSURED_SCHEMA_MODULES` 豁免。

### 6.2 API（新路由文件 `routers/lessondoc_editor.py`，注册紧跟 `lessondoc.py`、仍在 materials 之前）

```
GET  /api/lessondoc/editability/{material_id}
     → {editable, reason, kind: "lesson"|"home"|null, pack_id, lesson_no, legacy_convertible, legacy_root_id}
GET  /api/lessondoc/packs/{id}/lessons/{n}/deck
     → {deck, updated_at, assets_outdated, slide_count}     (课次无文件时返回最小骨架 deck + skeleton:true)
PUT  /api/lessondoc/packs/{id}/lessons/{n}/deck      body {deck, base_updated_at, summary?}
     → {warnings, updated_at, assets_refreshed}
GET  /api/lessondoc/packs/{id}/manifest               → {manifest, updated_at}
PUT  /api/lessondoc/packs/{id}/manifest               body {manifest, base_updated_at}
GET  /api/lessondoc/packs/{id}/lessons/{n}/revisions  → 列表(不含 doc_json)
GET  /api/lessondoc/packs/{id}/lessons/{n}/revisions/{rid}   → 含 doc_json(预览用)
POST /api/lessondoc/packs/{id}/lessons/{n}/revisions/{rid}/restore
POST /api/lessondoc/packs/{id}/media   multipart(file, scope: "lesson_N"|"shared")
     → {src: "media/xxx.png" | "../assets/media/xxx.png", material_id}
GET  /api/lessondoc/packs/{id}/media?lesson=N        → 包内可用素材列表(lesson_N/media + assets/media)
GET  /api/lessondoc/custom-elements
POST /api/lessondoc/custom-elements                  body {name, category, payload, thumb_svg}
PUT  /api/lessondoc/custom-elements/{id}             body {name?, category?}
DELETE /api/lessondoc/custom-elements/{id}
GET  /materials/lessondoc-editor/{pack_id}?lesson=N&slide=K&return=      (HTML 页，教师)
```

路由顺序：`/api/lessondoc/editability/…` 与 `/api/lessondoc/custom-elements` 前缀独立，不与 `packs/{pack_id}` 冲突；`packs/{id}/lessons/{n}/deck` 等固定子路径与既有 `by-root` 顺序规则一致（固定段在前）。

**PUT deck 的执行顺序（闭环关键）**：
1. `_load_owned_pack` 鉴权；`base_updated_at` 与包内 `lesson_N.html` 行 `updated_at` 比对，不一致 → `409 {conflict: true, server_updated_at}`（前端弹「文档已在别处修改：覆盖 / 放弃我的改动 / 另存快照」）。旧口径「最后写入者赢」只对 git 手改保留；编辑器之间必须有冲突提示。
2. `validate_deck(deck, expected_lesson=n)`；对比提交页数与净化后页数，**少页即拒绝**（`422 {dropped_slides:[{index, reasons}]}`），原文件不动——沿用 R2 护栏。
3. 写快照：读现有 deck（`_load_lesson_deck`）→ `lessondoc_doc_revisions(source="editor")`。
4. `pack_service.write_lesson_files` → `update_lesson_state(warnings)`；若 `assets_outdated` 或 deck 使用了 2.1 特性（含 frame/style/bg/globals/button/codewalk/group/html/canvas 任一）→ `refresh_pack_assets`，返回 `assets_refreshed: true`。
5. 若课次原为 `pending/failed`（编辑器可以从空白页开始建课）→ 置 `ready` 并 `refresh_home`（manifest `lessons[n].status = ready`，`summary` 取封面副标题或前 3 页标题拼接）。
6. `commit`；返回 warnings（前端在保存按钮旁显示「已保存 · N 处内容被自动修正（查看）」）。

**PUT manifest** 同构（validate_manifest → 快照 lesson_no=0 → write_manifest → refresh assets if needed）。

**media 上传**：复用 `library.py` 的 `_save_payload_bytes_globally` 与 `infer_material_profile`（二进制落全局文件 + `course_materials` 行挂在 `lesson_N/media/` 或 `assets/media/` 目录下，目录不存在则 `_create_folder_row` 建）；护栏：图片 ≤ 8MB、视频 ≤ 100MB、音频 ≤ 20MB、扩展名白名单、同名自动加后缀、返回包内相对路径。删除素材走材料库现有删除链（编辑器只提供「在材料库中查看」链接，不做删除）。

**editability** 判定表（§7.3）在服务端实现，前端只展示。

### 6.3 服务层（`services/lessondoc/`）

| 文件 | 新增职责 |
|---|---|
| `spec.py` | 新块类型/版式/限额/样式白名单/字体映射/动作白名单/HTML 标签属性白名单；`ASSET_FILES` += `interact.js` |
| `validate.py` | §4.11 全部；拆出 `validate_style.py`（style/frame/bg/actions 净化）与 `validate_html.py`（html 块消毒），保持单文件 <800 行 |
| `render.py` | 壳加载 `interact.js`；`extract_deck_text` 覆盖新字段（button.label、codewalk.lines[].code/out/note、html 去标签文本） |
| `editor_service.py`（新） | `load_lesson_deck`（带 updated_at；无文件时骨架）、`save_lesson_deck`（§6.2 六步）、`save_manifest`、`uses_v21_features(deck)`、`ensure_lesson_ready_after_edit`、`editability(material_id)` |
| `revisions.py`（新） | 快照写入/裁剪/读取/恢复 |
| `custom_elements.py`（新） | CRUD + payload 二次 validate（作为 group 块过 `_validate_block`） |
| `media_service.py`（新） | 上传落包、素材列表 |
| `generate.py` | AI 生成/重写落盘前调用 `revisions.snapshot(source="ai_*")`（3 处：整课生成、单页重写、批量） |
| `pack_service.py` | 新增 `lesson_entry_row(conn, pack, n)`（复用 `generate._find_lesson_entry_row` 的逻辑上收） |

### 6.4 权限

- 全部端点 `get_current_teacher` + `pack.teacher_id == user.id`（与现有 lessondoc 路由一致）；编辑器页面对非 owner 教师返回 403 页（不是登录页）；学生任何情况下无入口、无 API。
- 自定义元素按 `teacher_id` 隔离；不做校级共享（YAGNI，留字段扩展）。

---

## 7. 入口与可编辑性闭环

### 7.1 壳页入口（需求 2「文档打开的任意页面」）

`material_render_shell.js` 新增 `initEditEntry()`（与 `initSlideRewriteEntry` 共用 `by-root` 探测结果，合并为一次请求）：
- 教师 + 探测到 pack → 工具条注入「✎ 编辑」（放在「改这一页」左侧）。点击：读 iframe 当前 `lesson_N` 与 `#/K` → 跳 `/materials/lessondoc-editor/{pack_id}?lesson=N&slide=K&return=<当前壳页 URL>`；在首页时 `lesson=0`。
- 教师 + 是 HTML 包但**不是** pack（旧手写包）→ 注入「✎ 编辑」但点击进入 §7.3 转换对话框。
- 非包 HTML 材料 / 学生 → 不注入。
- 编辑器「预览」按钮反向回到壳页同一页（`?path=lesson_N/lesson_N.html#/K`）；编辑器「返回」用 `return` 参数，缺省回材料页。

### 7.2 材料页与向导入口

- `materials_manage.js` 包根卡片：操作区加「编辑文档」（`data-action="lessondoc-edit"` → 直接进编辑器首页模式 `lesson=0`）；「管理课次」面板（`lessondoc_wizard.js` `renderManageView`）：顶部加「编辑首页」，逐课行在 `ready` 时加「编辑」、在 `pending/failed` 时加「从空白创建」（进入编辑器时后端 `GET deck` 返回骨架：封面 + 1 空白内容页 + 结尾页，保存时按 §6.2 第 5 步转 ready）。
- 课程页向导管理面板同上（同一模块）。
- offering hub / 课堂页：不加编辑入口（保持「聚合展示 + 深链」定位）；课堂页「AI 重写本课」旁可加「打开编辑器」链接（可选，E6）。
- 材料页包内文件行（lesson_N.html 本身）：点击预览仍走壳页；不在文件行加编辑按钮（避免教师以为能编辑单个 HTML 文件）。

### 7.3 可编辑性判定与旧文档转换（需求 15）

| 材料 | 判定 | 反馈 |
|---|---|---|
| pack 包根 / 包内 main.html / lesson_N.html | `editable`（kind 由文件决定） | 进编辑器 |
| pack 包内其他文件（assets/media/README） | 不可编辑 | 「这是学习文档包的资源文件，请编辑首页或课次页」+ 按钮「去编辑首页」 |
| 旧手写 HTML 包（`parse_html_package` 通过、无 pack 行） | `legacy_convertible` | 对话框（下文） |
| 单个 HTML 文件（非包） | 不可编辑 | 「该文档不是学习文档包，无法进入可视化编辑。你可以：把它整理成 `main.html + lesson_N/` 的包结构再转换，或用『新建 → 课程学习文档包』重新创建」 |
| Markdown | 不可编辑（此编辑器） | 「Markdown 材料请使用材料详情中的源码编辑」+ 跳转按钮 |
| PDF/图片/Office 等 | 不可编辑 | 「该类型材料不支持编辑」 |
| 学生 | — | 无入口 |

**转换对话框**（复用 `import-legacy`）：
1. 「此文档是手写 HTML 包，需先转换为在线文档才能编辑。转换会创建一个新的学习文档包，原文档保持不变。」
2. 选课程（该教师有权的课程下拉；若该包已绑定过课堂，预选其课程）、主题、包名（默认「{原包名}-在线版」）。
3. 「预检」→ `dry_run=true` → 展示课次数 + 告警清单（如 stepper 解说词丢失）。
4. 「转换并编辑」→ 正式导入 → 成功后直接跳编辑器（首页），并 toast「已转换；原文档未改动，可在材料页对比后删除」。
5. 若该旧包已被课堂绑定：转换后提示「是否把课堂绑定切换到新包」→ 调 `/bind`（确定性绑定），否则课堂继续用旧包。

### 7.4 深链与状态回传

- 编辑器 URL 记录 `lesson/slide`；保存成功后不跳转；`return` 参数指定返回地址（壳页/材料页/课堂页）。
- 材料页与向导面板在编辑器关闭回来时**自动刷新**该包的进度（`BroadcastChannel('lessondoc')` 广播 `{pack_id, lesson_no, saved}`，加 `pageshow` 兜底）。

---

## 8. 「傻瓜式」自动化清单（需求 14/17）

| 场景 | 自动行为 |
|---|---|
| 打开文档 | 缺失 id 自动补齐；引擎过旧自动提示并在首次保存时刷新 |
| 拖入元素 | 默认尺寸/样式/示例内容；出界回推；吸附对齐 |
| 改字号/内容后溢出 | 编辑态实时显示「内容可能溢出画布」黄色角标（用 `fitSlide` 相同度量：scrollHeight > clientHeight）；展示态仍由 `fitSlide` 兜底 |
| 保存 | 服务端降级校验 + 告警可视化；页数不减护栏；自动快照；`pending` 课次自动转 `ready` 并同步首页导航/导图 |
| 长时间编辑 | 草稿每 10s 自动存 `localStorage['lessondoc-draft:{pack}:{lesson}']`；重开时若草稿比服务端新 → 提示恢复 |
| 关闭/刷新 | 未保存改动 `beforeunload` 提示 |
| 动作目标失效 | 编辑器标红；保存端剔除并告警 |
| 全局元素 | 封面/章节/结尾默认不显示，无需教师逐页排除 |
| AI 协作 | 顶栏「AI 改这一页」= R2 `rewrite` 端点（先保存当前改动再调用，成功后重载 deck）；属性栏文本组「AI 润色这段」= 同端点带 hint「只改第 K 页 id=b_x 的文本块，其余不动」（后端 `build_slide_rewrite_context` 加可选 `focus_block_id`，把该块单独标出）——低成本复用，不新建 AI 通道 |
| 命名 | 未命名块自动显示「类型 #序号」，动作目标选择器可读 |
| 页码/页眉页脚 | 引擎维护，编辑器无入口 |

---

## 9. 文件地图与体量估算

```
templates/lessondoc_editor.html                              ~120 行
static/js/lessondoc_editor/                                  (plain ES modules，仿 whiteboard/ 拆分)
  index.js            启动/布局/加载/保存/顶栏                 ~350
  state.js            不可变 store + 撤销重做 + 脏标记 + 草稿   ~300  (+ state.test.js)
  registry.js         元素注册表(类型→分类/图标/默认值/属性组)  ~400
  props_schema.js     属性组定义 + 交集 + 控件描述               ~250  (+ test)
  bridge.js           iframe 桥接、坐标换算、事件转发           ~200
  canvas_controller.js 选择/圈选/拖移/缩放/旋转/吸附/插入线     ~650  (+ geometry.test.js)
  element_bar.js      左栏、分类、拖源、预览、搜索、我的元素      ~350
  props_panel.js      右栏手风琴与控件渲染                      ~600
  controls/           color_picker.js gradient_editor.js slider.js segmented.js  ~400
  actions_builder.js  动作列表 + 目标拾取 + 试跑                 ~250
  code_panel.js       JSON/HTML 标签页                          ~200
  page_rail.js        页轨 + 页面操作 + 迁移规则                 ~300
  home_editor.js      首页模式差异化(区块把手/属性)              ~250
  clipboard.js        复制/粘贴/位置回折                          ~150  (+ test)
  shortcuts.js        快捷键 + 面板                              ~150
  api.js              fetch 封装 + 冲突/告警处理                 ~150
  media_picker.js     上传/选择包内素材                          ~150
  ui_popover.js (static/js/)  由 whiteboard/popover.js 提炼的通用浮窗   ~200
static/lessondoc/2.0/
  deck-engine.js      +定位块/style/bg/globals/group/html/hidden/canvas 版式/home.sections   +450
  interact.js (新)    动作运行时 + CodewalkPlayer + button 渲染 + 编辑桥接                    ~600
  slides.js           暴露 window.SLIDES {goTo,next,prev,current} + 编辑态门禁               +30
  slides.css/course.css  定位块/背景/按钮/codewalk/组/编辑层/隐藏态                          +350
static/css/ui-system.src.css   `lde-` 编辑器样式段                                          +500
classroom_app/db/schema_lessondoc_editor.py                                                  ~120
classroom_app/services/lessondoc/{editor_service,revisions,custom_elements,media_service,validate_style,validate_html}.py  ~900
classroom_app/routers/lessondoc_editor.py                                                    ~450
tests/test_lessondoc_editor_*.py + vitest + e2e                                              ~900
docs: 本文 + authoring-guide 回写
合计新增约 9k 行；引擎资产增量约 1.4k 行(仍零依赖)。
```

---

## 10. 施工计划（顺序即依赖序；每阶段末跑全量单测 + 路由快照 + 真机验收）

### E0 模型与引擎（纯资产 + 校验，无 UI）✅ 完成（2026-09-03）
1. [x] `spec.py` 常量；`validate.py` 接入 `validate_style.py`（frame/style/bg/actions/id）与 `validate_html.py`（lxml 白名单 + CSS 作用域）；`tests/test_lessondoc_editor_model.py` 24 项（新块矩阵、frame 裁剪、style 注入样本、html 消毒、动作目标存在性、canvas/overlays 丢页规则、globals 限额、id 去重、codewalk `ref` 越界、manifest.home、**2.0 deck 零告警零新键**）。全量 1605 项绿。
2. [x] `deck-engine.js`（1396 行）：`applyStyle`（语义色/字体映射/文字渐变 clip/描边/阴影预设）、`renderPositioned`（包裹层携带 `data-ld-id`，动作属性上提到包裹层）、`renderBg`（负 z 序层）、`applyGlobalsTo`（封面默认跳过、`excludeSlides`）、`group`（natural×scale）、`html`（二次消毒）、`hidden`、`canvas` 版式、`home.sections` 构建器（缺省 DOM 与 2.0 一致，单 `.wrap`）；article 模式 `positionedToFlow`；`buildDeck/rerenderDeck/patchSlide` 暴露在 `LESSONDOC.__engine`；旧壳页缺 interact 标签时按自身 src 自动注入。
3. [x] `interact.js`（397 行）：`runActions`（show/hide/toggle/move/moveTo/goto/next/prev/run/reset，事件委托 `[data-ld-actions]`）、`CodewalkPlayer`（运行/暂停/单步/重置、`ref` 轨迹、额外动作先于播放）、`LESSONDOC.edit`（mount/unmount/render/patchSlide/rects/hitTest/geometry/toCanvas/layer/select/previewActions/measureFlowFrames/on）；`slides.js` 暴露 `window.SLIDES`（goTo/next/prev/current/count/reinit/setLocked），`bindDeck` 可重入、`injectChrome` 幂等、锁定态不写 hash；`render.py` 壳追加 interact 标签；`spec.ASSET_FILES` 7 项。
4. [x] 黄金样本 `tools/lessondoc-sample/lesson_3/`（样式层 + 浮层 / canvas + group + move 动作 / codewalk + 额外动作 / html 块 / globals）+ `main.html` 加 `home.sections.blocks`；lesson_1/2 刻意保留旧壳（验证自动注入）。真机（8779）逐项通过：渐变文字 clip、楷体/宋体映射、旋转贴纸、隐藏块 toggle、分组 move/moveTo、codewalk 逐行高亮/输出/解说、html 作用域 CSS 且无 script、全局块只在内容页、编辑态 HUD 隐藏/键盘锁定/隐藏块半透明、patchSlide/render 页眉页脚不重复、article 无定位包裹/组展平/无 globals、首页单 wrap + 课程说明区块、旧壳 lesson_1 自动注入 interact 且 15 页正常。
5. [x] authoring-guide §4 补 `button/codewalk/actions` 正文 + 「编辑器专用字段 AI 勿输出」提示；§8 AI 摘要节加 button/codewalk 一行与禁输出字段清单。
**验收**：见下节「E0 回归对比」。
**踩坑**：① `slides.js` 拆出 `bindDeck` 后 `pageFromHash` 在 `slides` 绑定前被调用 → 改为按 DOM 计数；② 定位块的动作属性原本挂在内层元素，按 id 寻址到的是包裹层，`wrapper.click()`/`run` 都会落空 → 上提到包裹层（codewalk 例外）。

### E1 后端闭环
1. [ ] 两张表 + schema 注册 + 豁免；`revisions.py`（含 AI 三处落快照）；`custom_elements.py`；`media_service.py`；`editor_service.py`（六步保存 + editability）。
2. [ ] `routers/lessondoc_editor.py` 全部端点 + 编辑器页面路由；p02 快照重生成。
3. [ ] 单测：保存冲突 409、少页 422 原文件不动、快照裁剪 20、自动刷新引擎、pending→ready 联动首页、越权 403（禁重定向 + 响应体特征）、media 护栏、自定义元素 payload 二次校验、editability 判定表七种情形、骨架 deck。
**验收**：`unittest discover -s tests -t .` 全绿；本地真 PG 建表 + 保存链路跑通。

### E2 编辑器骨架：加载 → 选择 → 属性 → 保存
1. [ ] 模板 + `index/state/bridge/api/shortcuts`；三栏布局与 `lde-` 样式；`ui_popover.js` 提炼（白板回归：`npx vitest run static/js/whiteboard` + `whiteboard.spec.ts`）。
2. [ ] `canvas_controller`：单击/Shift 选择、圈选虚线、定位块拖/缩/旋/吸附、流式块插入线重排、方向键。
3. [ ] `props_panel` + `controls/`：页面属性（版式/标题/背景/页面操作）、通用组（identity/frame/text/box）、多选交集；`registry/props_schema` 全部块的类型组表单。
4. [ ] 保存/冲突/告警/草稿/beforeunload；撤销重做。
**验收（p03-qa 真机）**：打开 QA 库某课 → 改标题字号颜色渐变描边 → 拖流式块换序 → 页面背景上传图并旋转 → 保存 → 壳页刷新所见一致 → 学生账号打开一致；重复保存 `unchanged` 不产生快照。

### E3 元素栏与内容能力
1. [ ] `element_bar`：分类/分隔线/搜索/预览/拖源/点击插入；`media_picker`；拖放落位规则全表。
2. [ ] `page_rail`：加页(前/后/封面限制)/删页/复制/排序/版式切换迁移。
3. [ ] `code_panel`：JSON（本页/选中/整课）、HTML（只读 + 转换为自定义 HTML 页）。
**验收**：从空白课次（pending）用元素栏搭出 5 页并保存 → 课次转 ready、首页导航自动出现该课；JSON 改坏时行列提示、保存被服务端降级时告警面板可读。

### E4 互动与组合
1. [ ] `actions_builder` + 目标拾取 + 试跑；`button` 属性；任意块「点击时」。
2. [ ] `codewalk` 行编辑器与属性；引擎播放器真机（循环/箭头/单步/额外动作/`ref` 轨迹）。
3. [ ] `globals`：「应用到所有页面」开关、封面默认跳过、本页排除、全局列表。
4. [ ] `group`/拆分/等比缩放烘焙；`clipboard`（偏移回折规则单测）；Delete 语义；自定义元素库（存/拖入/删除/缩略图）。
**验收**：做一页「点击按钮 → 提示卡显示 + 分组移动 240px」+ 一页 codewalk（含 `ref` 循环轨迹）+ 一个全局 logo；存为自定义元素后在另一课拖入使用；壳页展示态动作正确、article 模式无报错。

### E5 首页编辑器
1. [ ] `home_editor`：区块把手/显隐/排序、首页属性、课程说明区块、tabs 编辑、阶段分组、背景。
2. [ ] `renderHome` sections 逻辑真机；manifest 保存链路。
**验收**：调整区块顺序 + 隐藏统计卡 + 加「课程说明」区块 → 保存 → 首页与课次结尾页「返回课程首页」链接正常；旧 manifest（无 home 段）渲染逐像素不变。

### E6 入口、转换、打磨、上线
1. [ ] 壳页「✎ 编辑」（合并 by-root 探测）；材料页/向导入口；`BroadcastChannel` 回传刷新；island `?v=` bump + 契约测试串。
2. [ ] `editability` 前端呈现 + 旧包转换对话框（dry_run → 转换 → 绑定切换询问）。
3. [ ] 页轨缩略图、快捷键面板、AI 改这一页/润色这段（`focus_block_id`）、响应式抽屉、reduced-motion 复核、说明浮窗接入。
4. [ ] e2e `tests/e2e/specs/lessondoc-editor.spec.ts`：登录教师 → 建包(mock AI) → 进编辑器 → 拖入按钮 → 绑定显示动作 → 保存 → 壳页 frameLocator 断言点击后目标可见 → 学生账号看同一页；旧手写包 → 编辑 → 转换流程断言新包创建且原包文件数不变。
5. [ ] 部署（deploy-workflow；`static/lessondoc/` 在同步清单内）；生产存量包会显示「引擎可更新」——发布说明写明「打开编辑器保存一次即自动更新，或在材料页点刷新」。
6. [ ] 回写本文进度、`course-lessondoc-template-2026-09.md` §11 追加 R7 指向本文、更新记忆 `lessondoc-template-system.md` 与 MEMORY.md。

---

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| 引擎资产膨胀影响离线包体积 | 增量 ~1.4k 行（≈45KB 未压缩），仍零依赖；`interact.js` 无编辑态时只注册运行时 |
| 旧引擎（未刷新的包）渲染新文档 | 保存端自动刷新引擎；未知字段被忽略、未知块降级为占位卡（既有口径）；材料页徽标兜底 |
| `style`/`html` 块成为注入面 | 键白名单 + 值正则 + lxml 标签属性白名单 + 前端二次剥除；单测含注入样本；不接受任意 CSS 字符串 |
| 同源 iframe 依赖登录态与 render 路由 | 编辑器页与 iframe 同站同 cookie；render 路由 `ensure_user_material_access` 对 owner 恒通过 |
| 两个标签页同时编辑 | `base_updated_at` 乐观锁 409 + 三选一对话框；快照可找回 |
| 撤销栈内存 | 结构共享（只替换被改路径上的对象），100 步上限；deck 通常 <200KB |
| 圈选/拖拽在缩放舞台上的精度 | 全部几何在画布单位下计算，`geometry()` 只在指针事件入口换算一次；resize 时重取 scale |
| 流式页与自由页混用造成教师困惑 | 页面属性用版式分段清晰标注「自由排版」；浮层块带「浮动」徽标；转换有确认与撤销 |
| article/手机回退丢失布局意图 | 明示：自由排版页在手机长文模式按层序线性显示；封面/章节等系统版式不受影响 |
| AI 生成通道受模型扩展影响 | AI 摘要节只加 button/codewalk；validate 对 AI 输出的行为不变（新增字段缺省即旧行为） |
| 自定义元素引用了包内素材路径 | 存元素时若 payload 含 `media.src`/`bg.image` 相对路径 → 提示「素材不会随元素复制，跨包使用需重新选择素材」；实例落地时缺资源渲染占位卡（既有行为） |
| 生产部署约 20 分钟 | E0 引擎与 E1 后端可先合并上线（对现有功能无感知），编辑器 UI 随 E6 上线 |
| Nutstore 目录下 Playwright 发现不到 spec | 沿用 `tmpspec/` 临时目录法（见 ui-verification-harness 记忆） |

## 12. 明确不做（YAGNI）

- 不做多人协同/实时光标；不做 diff 合并（乐观锁 + 快照足够）。
- 不做语法高亮库、不引入 CodeMirror/Monaco、不引入 html2canvas。
- 不做校级共享自定义元素、不做元素市场。
- 不做首页自由排版。
- 不做动画时间轴/关键帧（动作模型的 `ms/ease` 已覆盖课堂演示需要）。
- 不做旧手写包的「原地编辑」（一律先转换，原包不动）。
- 不改 `_normalize_generated_html_package_nodes` 旧分支、不改任何现有表列。

## 13. 上线前 checklist

- [ ] p02 路由快照重生成；`RUNTIME_ENSURED_SCHEMA_MODULES` 豁免；`_SCHEMA_READY` 重置夹具
- [ ] `spec.ASSET_FILES` 含 `interact.js`；`load_all_assets` 单测通过；黄金样本 file:// 离线验收
- [ ] 旧 deck 重渲 DOM 对比零差异；cnet 迁移包与 QA 三课回归
- [ ] 学生越权探测（禁重定向）：编辑器页/全部新 API 403
- [ ] island `?v=` 与契约测试串同步；`npm run build`（css + vite）；`npx tsc --noEmit`；`node --check` 全部新 JS
- [ ] 真 PostgreSQL 本地建表 + 保存/快照/素材上传跑通
- [ ] 白板 vitest + e2e 回归（popover 提炼不破坏）
- [ ] 记忆与本文进度回写
