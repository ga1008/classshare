# 讲课白板升级方案（2026-09）

> 状态：**P0–P6 已完成（2026-09-02），仅剩部署**。真源在本文；勾选框反映当前进度。
> 涉及模块：`static/js/teacher_whiteboard.js`（拆分为 `static/js/whiteboard/`）、`static/css/ui-system.src.css` 的 teacher_whiteboard 段、新增后端 `material_whiteboards` 表 + 服务 + 路由。

---

## 1. 背景与目标

讲课白板是学习文档全屏壳页（`/materials/render-view/{id}`）和 Markdown 材料页上的教师端绘图覆盖层。本次升级围绕七条需求：

| # | 需求 | 一句话结论 |
|---|------|-----------|
| 1 | 名称区缩成「保存」按钮：线上保存 / 导出本地 | 新增后端持久化 + 导出弹窗（比例锁定、5MB 上限） |
| 2 | 新建白板自动保存上一块；空白板不新建、不保存 | 补「空白板守卫」 |
| 3 | 三横线历史白板按钮 → 历史浮窗；切换自动保存 | 用列表浮窗替代 `<select>` |
| 4 | 画笔/文字/笔迹/背景滑块默认隐藏，点按弹小浮窗；开始绘制自动收回；默认正红色 | 统一 Popover 系统 |
| 5 | 橡皮擦 + 调节浮窗（大小、边缘硬度） | 矢量模型下的像素擦 + 整笔擦 |
| 6 | 清屏按钮 | 现已存在但不显眼且用 `window.confirm`，改为内联确认浮窗 |
| 7 | 布局合理、交互人性化、浮窗有出入过程、美观 | 视觉规范 + 动效规范 + 响应式 |

---

## 2. 现状深度分析

### 2.1 模块与宿主

- 单文件 `static/js/teacher_whiteboard.js`，**1792 行**（超过 800 行上限），含两个类：
  - `TeacherWhiteboard`：矢量无限画布，教师专用（`isTeacherContext` 硬门禁），本次升级对象。
  - `ExamDrawingWhiteboard`：考试答题附图的**位图**画板（`destination-out` 橡皮），由 `templates/exam_take.html:1680` 以 `import { initExamDrawingWhiteboard } from '/static/js/teacher_whiteboard.js'` 引入，并复用同一套 CSS 类名（`teacher-whiteboard-toolbar/group/btn/control/color/range/value`），`exam_take.html:937`、`:1391` 有覆盖样式。**升级不得破坏这些类名与导出名。**
- 三个宿主：
  1. 全屏渲染壳页 `templates/material_render_shell.html` + `static/js/material_render_shell.js:168`（空闲时 `import(asset_url)` 动态加载）。
  2. Markdown 材料页 `static/js/material_viewer.js:1503`（同样方式）。
  3. 考试页（仅用 `ExamDrawingWhiteboard`）。
- 上下文来自 `window.MATERIAL_VIEWER_CONTEXT`：`userId / userRole / materialId / materialName / classOfferingId / sessionId`。已足够作为后端持久化的归属键。
- 模块内部用相对导入 `./ui.js`（`escapeHtml`、`showToast`），说明「入口走 `asset_url` 带版本号、内部相对导入不带」是既有模式，拆分子模块可沿用。

### 2.2 数据模型与存储

- 存储键 `teacher-whiteboard:v1:{userId}:{materialId}`，**仅 localStorage**，换电脑/清缓存即丢，没有任何后端接口。上限 24 块板，超限按 `updatedAt` 裁剪；写失败再裁到 8 块并 toast。
- 状态结构：`{version:1, activeBoardId, boards:[{id,name,createdAt,updatedAt,viewport:{x,y,scale},elements:[]}], settings}`。
- 元素三类，全部**世界坐标**（不随视口变化）：
  - `stroke {color,size,points[]}`，`size` 已除以当时 `viewport.scale`；
  - `shape {shape:circle|square|rectangle|rounded|diamond,color,size,x1,y1,x2,y2}`；
  - `text {text,x,y,color,fontSize}`。
- 撤销：整份 `elements` 深拷贝快照，36 层。大板（几千笔）时每笔 `JSON.parse(JSON.stringify)` 有可感知开销，但不在本次范围。
- 默认色 `#0f172a`（深墨）；`normalizeSettings` 有白名单，方便加 `eraser` 工具与新字段。

### 2.3 渲染与交互

- 双 canvas：`canvas`（已提交元素，`drawMainCanvas` 全量重绘）+ `draft-canvas`（进行中的笔画/形状，屏幕坐标增量绘制）。DPR 感知（上限 2.5）。
- 视口平移/缩放（0.35–2.6），网格用 CSS 背景 + 自定义属性跟随视口。
- 指针事件走 `setPointerCapture`，笔画使用 `getCoalescedEvents`，二次贝塞尔平滑。
- 文本工具：在舞台上放 `textarea`，Enter 提交、Esc 取消、失焦提交。
- 键盘：Esc 关闭、Ctrl/Cmd+Z/Y 撤销重做。
- 墨迹层透明度 `--teacher-whiteboard-ink-alpha` 作用于整个 canvas 层，背景透明度作用于 `::before` 网格层，两者互不影响 —— 这意味着**橡皮的 `destination-out` 只会挖透墨迹层，不会挖到背景**，是矢量橡皮的可行前提。

### 2.4 工具栏与 UI

- 一整条固定顶栏 `position:fixed`，五个组：板名（`<select>`+`<input>`+新建）| 工具（手/笔/文字/五种形状）| 画笔色+粗细滑块 | 文字色+字号滑块 | 笔迹/背景透明度滑块 | 撤销/重做/缩放/回中/清屏。
- 问题：
  - 板名组 `flex:1 1 320px` 吃掉大量宽度；四条滑块常驻，1180px 以下退化成横向滚动。
  - `<select>` 历史列表信息密度低（只有「名称 · 时间」），无法看到笔数/是否已同步。
  - 清屏在最右端用垃圾桶图标，配 `window.confirm` 原生弹窗，与整体视觉割裂。
  - 「新建」无条件新建（违反需求 2），且总是产生「材料名 白板」的同名板。
- 视觉：白色玻璃拟态（`backdrop-filter`）、`--ls-teal` 强调色、`--radius-xl`，与设计系统一致；动效仅 180ms 淡入，无 `prefers-reduced-motion` 处理。
- CSS 源已并入 `static/css/ui-system.src.css`（`/* --- Source: static/css/teacher_whiteboard.css --- */` 段，约 38037–38424 行，原独立文件已删除），改完必须 `npm run build:css`。

### 2.5 问题清单（对照需求 + 额外发现）

| 类别 | 问题 | 处理 |
|------|------|------|
| 需求 1 | 无后端存储 | 新增表/服务/路由 + 前端 RemoteStore + 同步策略 |
| 需求 1 | 无导出 | 新增边界计算 + 离屏渲染 + 5MB 拟合 |
| 需求 2 | 新建不判空 | 空白板守卫 |
| 需求 3 | `<select>` 历史 | 列表浮窗（含重命名/删除/同步状态） |
| 需求 4 | 滑块常驻、默认深色 | 芯片按钮 + Popover；默认 `#ff0000`，旧本地设置迁移 |
| 需求 5 | 矢量板无橡皮 | `eraser` 元素（像素擦）+ 命中删除（整笔擦） |
| 需求 6 | 清屏用原生 confirm | 内联确认浮窗 |
| 需求 7 | 无出入动效、无 reduced-motion | 动效规范 |
| 额外 | 文件 1792 行 | 拆分为 `static/js/whiteboard/` 子模块，入口保留为 shim |
| 额外 | 考试板共用类名 | 类名不变，新样式全部用新前缀 `twb-` 追加 |
| 额外 | `saveNow` 里 `updatedAt = updatedAt || now` 只在首次赋值 | 改为每次 dirty 都更新，用作同步比较 |

---

## 3. 前端设计

### 3.1 架构与文件拆分

```
static/js/teacher_whiteboard.js          ← 保留：thin shim，re-export + bootstrap（宿主与 exam_take 零改动）
static/js/whiteboard/
  constants.js        默认值、限额、ICONS、允许的元素类型
  state.js            createBoard / normalizeState / migrateV1ToV2 / isBoardEmpty（纯函数）
  geometry.js         bounds / hitTest / pointToSegment / simplifyStroke（纯函数，单测）
  renderer.js         drawElement 家族（含 eraser）、renderElementsTo(ctx, elements, transform)
  export.js           computeExportSize / lockAspect / renderExportBlob / fitUnderLimit / buildFileName
  popover.js          Popover 管理器（锚定、动效、唯一打开、关闭规则、焦点）
  panels/save_menu.js        保存二级菜单
  panels/export_dialog.js    导出弹窗
  panels/history_panel.js    历史白板浮窗
  panels/style_popovers.js   画笔/文字/笔迹/背景四个小浮窗
  panels/eraser_popover.js   橡皮浮窗
  panels/confirm_popover.js  清屏/删除内联确认
  store_local.js      localStorage v2 读写 + 裁剪
  store_remote.js     /api/materials/{id}/whiteboards 客户端（预留 share/collab 方法签名）
  sync.js             合并策略、dirty 队列、定时/事件触发
  board.js            TeacherWhiteboard 主类（编排，目标 < 800 行）
  exam_board.js       ExamDrawingWhiteboard 原样迁移
```

- `vite.config.ts` 的 `test.include` 追加 `'static/js/whiteboard/**/*.test.js'`，让 vitest 覆盖纯函数模块。
- CSS：在 `ui-system.src.css` 的 teacher_whiteboard 段末尾追加新段 `/* --- twb: toolbar chips / popover / panels / dialog / eraser cursor --- */`，全部用 `twb-` 前缀，不动既有类。

### 3.2 工具栏新布局

桌面（≥ 1180px）单行：

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ [☰] [💾 保存 ▾●] [＋] │ [✋][✎][◧][T] [○][□][▭][▢][◇] │ [● 画笔 5] [T 文字 28] [◐ 笔迹 100%] [▦ 背景 95%] │ [↶][↷][－][＋][⟲] [🗑 清屏] │
└──────────────────────────────────────────────────────────────────────────────────────────┘
   板组                    工具组（◧ = 橡皮）              样式芯片组                                   操作组
```

- **板组**：三个 34px 按钮。保存按钮带下拉箭头和 **同步状态点**（灰=本地、绿=已线上保存、橙=有未同步改动、红=上次失败）。
- **样式芯片**：32px 高胶囊按钮，左侧色点/图标 + 短标签 + 当前值。点击打开对应小浮窗；浮窗打开时芯片 `is-active`。
- **板名**不再常驻工具栏：显示在保存菜单标题与历史浮窗当前行，历史浮窗内可内联重命名。
- **清屏**保持在最右，改为「图标 + 文字」的次级按钮，与撤销重做用分隔线隔开，避免误触。
- 响应式：
  - ≤ 1180px：芯片只保留图标 + 值（隐藏文字标签）；工具栏保留 `overflow-x:auto` 兜底。
  - ≤ 760px：`flex-wrap` 两行，第一行板组 + 工具组，第二行芯片 + 操作组；浮窗改为贴底抽屉（`placement: 'sheet'`）。

### 3.3 Popover 系统（统一规范）

`popover.js` 导出 `createPopover({ id, anchor, panel, placement, closeOn })` 与单例管理器 `popoverManager`。

- **锚定**：`position:fixed`，按锚点 `getBoundingClientRect()` 放在下方 8px，水平 `start` 对齐并夹在视口 12px 安全边内；空间不足时翻转到上方。
- **动效**：打开 `opacity 0→1, translateY(-6px→0), scale(.98→1)`，160ms `cubic-bezier(.2,.8,.2,1)`；关闭反向 120ms 后 `hidden`。`@media (prefers-reduced-motion: reduce)` 下只保留 opacity 80ms。
- **唯一打开**：管理器保证同一时刻至多一个浮窗；打开另一个自动关闭前一个。
- **关闭触发**（需求 4「开始绘制自动收回」）：
  1. 舞台 `pointerdown`（任何工具开始操作）；
  2. 点击浮窗与锚点之外；
  3. `Escape`（此时 Esc 不再关闭整个白板，先关浮窗）；
  4. 切换工具、切换/新建白板、`window.resize/blur`。
- **可访问性**：面板 `role="dialog"` + `aria-labelledby`；菜单类用 `role="menu"/"menuitem"`；锚点 `aria-expanded` / `aria-controls`；打开聚焦首个控件，关闭焦点回锚点；浮窗内 Tab 循环。
- **三种形态**：`popover`（小设置面板，宽 240–280px）、`panel`（历史列表，宽 360px、最高 60vh 内滚动）、`dialog`（导出弹窗，居中 + 半透明遮罩，宽 520px；移动端全屏抽屉）。

### 3.4 保存

#### 3.4.1 保存菜单（点击 💾）

```
┌ 当前：计算机网络 白板 · 09-02 14:20 ────────┐
│ ● 已线上保存（14:18）                       │
├─────────────────────────────────────────────┤
│ ☁ 线上保存            Ctrl+S                │
│ ⬇ 导出本地…                                 │
└─────────────────────────────────────────────┘
```

- 空白板时两项禁用并提示「白板为空」。
- 线上保存进行中按钮转圈、禁用；成功 toast「已保存到云端」，失败 toast 带「重试」。

#### 3.4.2 线上保存：数据与同步策略

**归属与可见性**：每块板归属 `(owner_role, owner_user_pk, material_id)`。其他账号打开同一材料看不到；**可见性字段 `visibility` 默认 `private`**，`shared` / `collab` 与 `share_token`、未来的分享/协作端点一起预留（见 4.4），本次不实现。

**同步模型**（推荐方案，见第 7 节决策 5）：

- localStorage 升级为 **v2 写后缓存**：立刻响应、离线可用；键 `teacher-whiteboard:v2:{userId}:{materialId}`，首次运行把 v1 数据迁入（并把等于旧默认色 `#0f172a` 的画笔/文字色改为 `#ff0000`）。
- 每块板新增本地字段：`remoteVersion`（服务端乐观锁版本，0=从未上传）、`syncedAt`、`dirty`。
- **自动同步**（静默，失败不打扰）：新建/切换/关闭白板、`visibilitychange→hidden`、每 30s 扫描一次 dirty 且非空的板 → `PUT` 上传。`pagehide` 用 `fetch(keepalive)` 尽力而为（受 64KB 限制），未成功的下次打开时由 dirty 标记补传。
- **显式「线上保存」**：立即上传当前板并给反馈。这是需求 1 的按钮，也是老师确认「已存好」的心理锚点。
- **打开时合并**：先渲染本地缓存（零等待），再 `GET` 列表：
  - 仅远端有 → 加入历史索引（元素按需 `GET` 单板后缓存）；
  - 两边都有：远端 `version` > 本地 `remoteVersion` 且本地不 dirty → 用远端覆盖；本地 dirty → 上传（带 `base_version`）；
  - 冲突（服务端版本已前进且本地也改过）→ 服务端返回 409 + 服务端副本：当前板原地改名为「原名（本机副本）」、换新 key 继续 dirty（画布不变），远端副本以原 key 作为新条目加入。**永不静默丢数据，也不替换正在看的画布。**
- **空板规则**：`isBoardEmpty(board)`（无非橡皮元素）为真时不上传、不新建；被清空后显式保存的板允许以 `element_count=0` 更新（尊重用户清空意图）。
- **体积**：上传前对 stroke 做 Ramer–Douglas–Peucker 简化（容差 0.35 世界像素，视觉无损），单板 JSON 超过 2MB 时 toast 提示并拒绝上传（本地仍保存）。

#### 3.4.3 导出本地：弹窗规格

```
┌ 导出白板 ─────────────────────────────────────────┐
│ [预览缩略图（实时渲染，含所选背景）]  预计 0.4 MB   │
│                                                    │
│ 格式   (●) PNG 白底   ( ) PNG 透明   ( ) JPG        │
│ 尺寸   宽 [ 512 ] px  ×  高 [ 318 ] px   🔒比例 1.61 │
│        快捷：512 · 1024 · 2048 · 4096                │
│ 文件名 [ 计算机网络-白板-20260902-1420 ]  .png       │
│                                                    │
│                          [取消]  [下载]             │
└────────────────────────────────────────────────────┘
```

- **比例**：由 `geometry.bounds(elements)` 决定，不可调。边界 = 所有非橡皮元素的包围盒（stroke 按点集 ± size/2；shape 按 `getShapeBox` ± size/2；text 用离屏 `measureText` 逐行量宽，高 = 行数 × fontSize × 1.28），四周补白 `pad = max(24, 长边 × 4%)` 世界像素。
- **默认**：PNG 白底，**长边 512px**，短边按比例四舍五入。输入宽自动算高，输入高自动算宽（`lockAspect`），整数、最小 64、最大 8192，总像素 ≤ 3200 万。
- **5MB 上限**：尺寸/格式变化后 300ms 防抖真实离屏渲染 → `canvas.toBlob` → 显示「预计 x MB」。超限时数字变红、下载禁用，并出现「自动缩到 5MB 内」按钮：按 `sqrt(5MB / size) × 0.95` 缩放重试，最多 3 轮。JPG 质量 0.92。
- **渲染管线**（关键：白底不能被橡皮挖穿）：先在透明离屏层上按 `translate(-bounds.x*scale+pad) scale(scale)` 渲染全部元素（橡皮走 `destination-out`），再把该层 `drawImage` 到填充了背景色的输出画布上；透明 PNG 直接输出该层。
- **文件名**：`{材料名}-{板名}-{yyyyMMdd-HHmm}`，剔除 `\ / : * ? " < > |` 与控制字符，折叠空白为 `-`，截到 80 字符，扩展名随格式。用户可改。
- **下载**：`URL.createObjectURL(blob)` + `<a download>`，完成后 `revokeObjectURL`；toast「已导出 xxx.png（0.4 MB）」。
- 空板时菜单项禁用，不进入弹窗。

### 3.5 新建 / 历史 / 切换

- **新建（＋）**：`if (isBoardEmpty(active)) { toast('当前白板还是空的，直接在上面画吧'); focus stage; return; }` 否则：提交文本编辑器 → `saveNow()`（本地）→ 标记 dirty 触发远端同步 → 创建新板。新板命名：`{材料名} · 白板 {序号}`，序号 = 该材料下现有板数 + 1，避免同名。
- **历史浮窗（☰）**：

```
┌ 历史白板（6）              [＋ 新建] ┐
│ ● 计算机网络 · 白板 3   ✎  ☁  🗑     │  ← 当前（高亮）
│   14:20 · 128 笔 · 已同步             │
│   计算机网络 · 白板 2                 │
│   昨天 10:02 · 41 笔 · 本机未同步 ⚠   │
│   …                                   │
└───────────────────────────────────────┘
```

  - 按 `updatedAt` 倒序；每行：名称（点击 ✎ 内联重命名，Enter 确认/Esc 取消）、相对时间、笔数、同步状态、删除（内联确认，删除远端 + 本地，可撤销 5s 内 toast「撤销」）。
  - 点击行 → `selectBoard(id)`：提交文本编辑器 → 保存并同步旧板 → 加载新板（本地无元素则 `GET` 单板，期间行内 spinner）→ 关闭浮窗。
  - 缩略图：不做（YAGNI，笔数 + 时间足够辨识；后续可加）。
- **裁剪**：本地上限 24 块保持，但**已线上保存的板裁剪本地副本不丢数据**（可随时再拉）；本地未同步板永不被裁剪掉（改为提示）。

### 3.6 样式浮窗与默认颜色

四个芯片各开一个 240px 小浮窗：

| 芯片 | 浮窗内容 |
|------|---------|
| 画笔 | 8 色速选（正红 `#ff0000`、橙、黄、绿、青、蓝、紫、墨黑）+ `<input type=color>` 自定义；粗细滑块 1–32 + 实时圆点预览 |
| 文字 | 同色板；字号滑块 12–72 + 「Aa」实时预览 |
| 笔迹 | 透明度 35–100% 滑块 + 说明「只影响墨迹，不影响背景」 |
| 背景 | 透明度 0–95% 滑块 + 说明「0% 透视文档」 |

- 滑块 `input` 事件即时生效（与现状一致），浮窗不需要「确定」。
- **默认色**：`DEFAULT_SETTINGS.brushColor = textColor = '#ff0000'`；v1→v2 迁移时旧默认值替换为红色，用户自定义过的颜色保留。

### 3.7 橡皮擦

- 工具按钮位于画笔右侧；快捷键 `E`（画笔 `B`、文字 `T`、手 `H`/按住空格临时平移）。
- **橡皮浮窗**（橡皮已选中时再点一次按钮打开）：
  - 模式：**像素擦**（默认）/ **整笔擦**；
  - 大小滑块 4–120px（屏幕像素）；
  - 边缘硬度 0–100%（仅像素擦）；
  - 实时圆形预览。
- **像素擦（数据模型）**：新增元素 `{ id, type:'eraser', points[], size, hardness, createdAt }`，size 同样除以 `viewport.scale` 存世界单位。渲染在 `drawMainCanvas` 顺序中按 `globalCompositeOperation='destination-out'` 画，因此只擦除其之前的元素，之后再画的笔迹不受影响 —— 与真实白板一致。
  - 硬度实现：`hardness < 1` 且 `'filter' in ctx` 时 `ctx.filter = blur(${size × (1 − hardness) × 0.35}px)`；不支持 `filter`（旧 Safari）时退化为三层递减 alpha、递增线宽的软边。
  - 实时预览：拖动过程中直接在**主 canvas** 上增量 `destination-out`（草稿层无法预览挖空），`pointerup` 时把 eraser 元素入栈并全量重绘，结果与预览一致。
  - 光标：舞台内跟随指针的圆形 `div.twb-eraser-cursor`（直径 = size），隐藏系统光标。
- **整笔擦**：`pointerdown` 时推一次撤销快照；拖动中对指针半径内的元素做命中测试并移除（同一拖动只产生一条撤销记录）：
  - stroke：任一线段与指针距离 ≤ 半径 + stroke.size/2（`geometry.pointToSegment`）；
  - shape：到轮廓距离 ≤ 半径 + size/2（矩形族按四边，椭圆按归一化径向距离，菱形按四条边）；
  - text：指针落入文本包围盒；
  - `eraser` 元素不参与命中（否则会「反擦」）。
- 撤销/重做对两种模式都走现有快照机制。
- 导出与线上保存都携带 eraser 元素；服务端白名单类型加 `eraser`。

### 3.8 清屏

- 点击 → 锚定在按钮下方的确认浮窗：「清空当前白板？可用撤销恢复」[取消][清空]，`Enter` 确认、`Esc` 取消；清空后 toast「已清空，Ctrl+Z 可恢复」。
- 空板时按钮禁用。

### 3.9 视觉与动效规范

- 令牌：继续用 `--ls-teal` 系、`--gray-*`、`--radius-md/lg/xl`、`--text-xs/sm/md`，浮窗背景 `rgba(255,255,255,.96)` + `backdrop-filter: blur(18px)`，阴影 `0 18px 40px -22px rgba(15,23,42,.45)`。
- 芯片：高 32px，圆角 999px，`border 1px rgba(148,163,184,.22)`，hover 上浮 1px，active 用 `--ls-teal` 淡底。
- 分组间用 1px 竖分隔线（`rgba(148,163,184,.3)`）替代现有的每组独立卡片底色，减少视觉噪音。
- 工具栏进入：从 `translateY(-10px)` 到 0，同现状；浮窗见 3.3。
- 同步状态点 6px，颜色：`--gray-400` / `--ls-success` / `--ls-warning` / `--ls-danger`；颜色变化 200ms 过渡。
- `prefers-reduced-motion` 全局尊重。

### 3.10 快捷键（全屏白板打开时）

| 键 | 动作 |
|----|------|
| B / E / T / H | 画笔 / 橡皮 / 文字 / 手 |
| 空格按住 | 临时手工具 |
| Ctrl+S | 线上保存 |
| Ctrl+Shift+E | 打开导出 |
| Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z | 撤销 / 重做 |
| [ / ] | 当前工具变细 / 变粗 |
| Esc | 关浮窗 → 关文本编辑 → 关白板 |

---

## 4. 后端设计

### 4.1 表：`material_whiteboards`

新文件 `classroom_app/db/schema_material_whiteboards.py`，仿 `schema_polls`（engine-aware、幂等、`_SCHEMA_READY`、运行时 ensure、不进 REQUIRED），并在 `classroom_app/db/schema.py` 与 polls 相同的两处调用点登记。

```sql
CREATE TABLE IF NOT EXISTS material_whiteboards (
    id              {id_column},
    owner_role      TEXT    NOT NULL DEFAULT 'teacher',
    owner_user_pk   INTEGER NOT NULL,
    material_id     INTEGER NOT NULL,
    board_key       TEXT    NOT NULL,           -- 客户端生成的 board id
    name            TEXT    NOT NULL DEFAULT '',
    viewport_json   TEXT    NOT NULL DEFAULT '{}',
    elements_json   TEXT    NOT NULL DEFAULT '[]',
    element_count   INTEGER NOT NULL DEFAULT 0,
    schema_version  INTEGER NOT NULL DEFAULT 2,
    version         INTEGER NOT NULL DEFAULT 1, -- 乐观锁
    visibility      TEXT    NOT NULL DEFAULT 'private', -- 预留 private|shared|collab
    share_token     TEXT,                       -- 预留
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at      TEXT,
    UNIQUE (owner_role, owner_user_pk, material_id, board_key)
);
CREATE INDEX IF NOT EXISTS idx_material_whiteboards_owner
    ON material_whiteboards (owner_role, owner_user_pk, material_id, updated_at DESC);
```

时间戳为 ISO-8601 TEXT，与全库一致。

### 4.2 服务：`classroom_app/services/material_whiteboard_service.py`

- `list_boards(conn, user, material_id) -> list[meta]`（不含 elements）
- `get_board(conn, user, material_id, board_key) -> board`
- `upsert_board(conn, user, material_id, board_key, payload, base_version) -> board`
  - 校验：`name ≤ 60`；`elements` 为列表、≤ 20000 项、序列化 ≤ 2MB；每项 `type ∈ {stroke, shape, text, eraser}`；数值字段有限（`math.isfinite`）；`viewport.scale ∈ [0.35, 2.6]`。
  - 乐观锁：存在且 `base_version != version` → `WhiteboardConflict(server_board)` → 路由返回 409 + 服务端副本。
  - 成功 `version += 1`，`updated_at = now`。
- `rename_board`、`delete_board`（软删 `deleted_at`）。
- 材料访问复用 `classroom_app/routers/materials_parts` 的 `ensure_user_material_access`；角色门禁常量 `WHITEBOARD_ALLOWED_ROLES = {"teacher"}`，放开学生只改一处。
- SQL 全部参数化；所有返回走 `{"status":"ok", ...}` 信封。

### 4.3 路由：`classroom_app/routers/material_whiteboards.py`

前缀 `/api/materials/{material_id}/whiteboards`，`response_class=JSONResponse`，`Depends(get_current_user)`，在 `app.py` 与其他路由一起 `include_router`。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 列表（meta） |
| GET | `/{board_key}` | 单板（含 elements） |
| PUT | `/{board_key}` | 全量 upsert，body `{name, viewport, elements, schema_version, base_version}` |
| PATCH | `/{board_key}` | `{name}` 重命名 |
| DELETE | `/{board_key}` | 软删 |

错误：400 校验失败、403 角色/材料无权（注意 `app.py` 会把 403 重写成 401，测试按 401 断言）、404 不存在、409 版本冲突、413 超体积。

### 4.4 预留的分享/协作接口（本次不实现，只定契约）

- `POST /{board_key}/share` → 生成 `share_token`、`visibility='shared'`；`GET /api/whiteboards/shared/{token}` 只读加载。
- `visibility='collab'` + 未来 WebSocket 通道 `/ws/whiteboards/{board_key}`，元素级操作日志表 `material_whiteboard_ops` 届时再建。
- 前端 `store_remote.js` 只声明 `share()` / `subscribe()` 的方法签名与 JSDoc，不实现。

### 4.5 后端测试：`tests/test_material_whiteboards.py`（sqlite，`unittest discover -s tests -t .`）

- ensure schema 幂等（调用两次）；
- upsert → list → get 回读一致，`element_count` 正确；
- 另一账号列表为空（隔离）；
- `base_version` 过期 → 409 且返回服务端副本；
- 非法类型 / 超 20000 项 / NaN → 400；> 2MB → 413；
- 学生角色 → 401（403 重写）；
- 软删后列表不可见；
- 记得重置 `_SCHEMA_READY`（见 memory「classroom-retake-system」的坑）。

---

## 5. 施工计划

按阶段推进，每阶段独立可回归、可提交。勾选框施工时回填。

### P0 拆分基线（无行为变化）
- [x] 建 `static/js/whiteboard/`，把两个类与工具函数按 3.1 拆出；`teacher_whiteboard.js` 改为 shim。
- [x] `vite.config.ts` 追加 vitest include；为 `geometry.js` 现有函数（`getShapeBox`、`distance`）补首批单测。
- [x] 回归：壳页教师端打开/画/关、考试页附图板、Markdown 页；Playwright 截图对比。

### P1 浮窗系统 + 工具栏重排 + 样式浮窗 + 清屏
- [x] `popover.js` 管理器 + CSS（含 reduced-motion）。
- [x] 工具栏 DOM 按 3.2 重排；四个芯片 + `style_popovers.js`；`confirm_popover.js` 接管清屏。
- [x] 默认色 `#ff0000`；`store_local.js` v1→v2 迁移。
- [x] 舞台 `pointerdown` / 切工具自动收回浮窗。
- [x] 快捷键表 3.10。
- 验收：四滑块默认不可见；点芯片 160ms 内出现、开始画立即收回；Esc 层级正确；1180/760 断点布局无横向溢出（芯片折叠后）。

### P2 橡皮擦
- [x] `eraser` 元素 + 渲染（`destination-out`、硬度、filter 退化）。
- [x] 像素擦实时预览；整笔擦命中测试（`geometry.hitTest` 单测覆盖 stroke/shape/text 与 eraser 排除）。
- [x] 橡皮浮窗、圆形光标、`[`/`]` 调大小。
- 验收：擦除只影响之前笔迹；撤销恢复；硬度 0/50/100 边缘肉眼可辨；Safari 无 filter 时不报错。

### P3 新建 / 历史 / 切换
- [x] `isBoardEmpty` 守卫；新板序号命名。
- [x] `history_panel.js`：列表、内联重命名、删除（可撤销 toast）、当前高亮、状态列。
- [x] 切换/新建前自动 `saveNow` + dirty 标记。
- 验收：空板点＋不新建且有提示；历史列表按更新时间倒序；重命名即时反映到保存菜单标题。

### P4 导出本地
- [x] `geometry.bounds`（含 text measure）+ 单测。
- [x] `export.js`：尺寸联动、5MB 拟合、文件名；`export_dialog.js` 预览与实时体积。
- [x] 白底/透明/JPG 三格式在有橡皮元素的板上验证（白底不被挖穿）。
- 验收：默认 512 长边白底 PNG；改宽自动改高；4096 长边大板超 5MB 时禁用下载并可一键缩放；文件名合法。

### P5 线上保存
- [x] 后端表/服务/路由/测试（第 4 节）。
- [x] `store_remote.js` + `sync.js`：合并、冲突、dirty 队列、定时器、`keepalive`。
- [x] `save_menu.js` + 同步状态点；Ctrl+S。
- [x] 本地真 PostgreSQL 验证（memory「local-dev-postgres」：上线前必须）。2026-09-02 以回滚事务跑通 upsert/list/get/409/rename/软删复活/隔离。
- 验收：A 电脑画 → B 电脑同账号打开可见；另一教师账号不可见；断网时本地照常、恢复后自动补传；人为制造冲突产生「本机副本」而非丢失。

### P6 打磨与收尾
- [x] 动效/间距/深浅对比走查；`prefers-reduced-motion`；键盘可达性（Tab 循环、aria）。
- [x] `npm run build:css`；Playwright e2e：`tests/e2e/specs/whiteboard.spec.ts`（开 → 画 → 浮窗收回 → 橡皮 → 清屏确认 → Ctrl+S → 新建守卫 → 历史切换 → 导出下载 → 清缓存刷新云端恢复），运行：`P03_PORT=8031 P03_AI_PORT=8032 P03_RUNTIME_ROOT=<abs>/.codex-temp/p03-runtime-wb npx playwright test tests/e2e/specs/whiteboard.spec.ts --project=chromium`。
- [x] 更新本文勾选、新增 memory「whiteboard-system」并在 `MEMORY.md` 登记。
- [ ] 按 memory「deploy-workflow」部署并线上抽验。

---

## 6. 测试与验收矩阵

| 层 | 工具 | 覆盖 |
|----|------|------|
| 纯函数单测 | vitest | geometry（bounds/hitTest/simplify）、export 数学（aspect lock、limit fit、filename）、state（isBoardEmpty、v1→v2 迁移、normalize eraser） |
| 后端单测 | unittest + sqlite | 4.5 全部用例 |
| 组件/交互 | Playwright（Claude Preview 手动 + e2e 脚本） | 浮窗出入与收回、橡皮两模式、历史切换、导出下载（拦截 download 事件校验文件名/大小）、跨刷新线上加载 |
| 视觉 | Playwright 截图 | 1440 / 1180 / 760 三断点，浅色主题 |
| 兼容 | 手动 | Chrome/Edge（主）、Safari（filter 退化）、触屏（pointer 事件、抽屉浮窗） |

---

## 7. 决策与假设（施工前请确认或默认采纳）

1. **白板仍为教师专用**（沿用现状门禁）。后端/前端各留一处常量，日后开放学生只改两行。
2. **正红色取 `#ff0000`**；若偏好中国红 `#e60012`，改 `constants.js` 一处。
3. **五个形状按钮保持平铺**，不折叠成一个下拉（需求未提，且课堂中一键直达更顺手）。
4. **旧本地数据自动迁移**（v1→v2），不清空老师已有的本地白板。
5. **自动同步 + 显式保存并存**（推荐）：切换/新建/关闭/定时都会静默上传非空板，「线上保存」按钮是立即保存 + 反馈。备选是「只有点按钮才上云」，实现更简单但「换电脑就能看」会依赖老师记得点；如需备选，只需把 `sync.js` 的自动触发器关掉。
6. **导出比例锁定、长边默认 512、上限 8192px / 3200 万像素 / 5MB**。
7. **历史浮窗不做缩略图**（笔数 + 时间足够；后续可加，`export.js` 的离屏渲染可直接复用）。
8. **撤销快照机制不改**（深拷贝 36 层），大板性能优化不在本次范围。
9. **板对象就地可变**（接受的风格偏离）：`board.js`/`sync.js` 对 board/settings 采用就地修改，因为画布交互需要 `activeBoard` 引用稳定且每笔全量拷贝代价大；已在 code review 中记录，无正确性问题。
10. **冲突处理的最终语义**：当前板保持内容与选中状态不变，仅换新 key 并改名「（本机副本）」待上传；服务端版本以原 key 作为独立历史条目加入（2026-09-02 实测通过）。

---

## 7.1 施工备注（2026-09-02）

- 验收环境：`.claude/launch.json` 新增 `p03-wb`（端口 8031、独立运行时根 `.codex-temp/p03-runtime-wb`），避免与其他会话的 p03 服务共用数据库。浏览器验证用 `127.0.0.1` 而非 `localhost`，因为 localhost 上其他端口的 httpOnly `access_token` 会阻止注入测试会话 cookie。
- 实测发现并已修复：导出弹窗改尺寸后立刻点「下载」被 change 事件重渲染吞掉（去掉 change 监听，下载前等待渲染）；`hidden` 属性被 `.twb-btn/.twb-row` 的 display 规则覆盖（加 `.twb-layer [hidden] { display:none !important }`）；导出默认文件名重复材料名并带 `.html` 扩展；服务端 `element_count` 需与前端一致只计墨迹元素；新电脑首开时空板应自动切到最近云端白板（`adoptRemoteBoardIfFresh`）。
- 浮窗改为纯白底（去掉 backdrop-filter）：Browser 截图下带模糊背景的浮窗呈半透明，为稳妥起见不依赖该效果。
- `board.js` 拆为主类 + `fab.js` / `interaction.js` / `text_editor.js` 三个 prototype mixin（主文件 772 行）。
- 单测：`npx vitest run static/js/whiteboard`（25 例）；后端 `DB_ENGINE=sqlite python -m unittest tests.test_material_whiteboards`（16 例）。

## 8. 不在范围内

- 在线分享 / 协作绘图（只留字段与契约，见 4.4）。
- 学生端白板。
- 考试附图板（`ExamDrawingWhiteboard`）功能变更，仅原样迁移文件。
- 元素选择/移动/编辑（选择工具）。
- 图片粘贴、激光笔、录制回放。
