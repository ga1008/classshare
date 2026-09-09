# 讲课白板性能深度梳理与改进方案（2026-09-09）

> 背景：`docs/whiteboard-upgrade-2026-09.md` 第 7 节决策 8 明确写着「**撤销快照机制不改（深拷贝 36 层），大板性能优化不在本次范围**」。
> 本文即是补上这一块：把「学习文档 → 讲课白板」整条链路上的功能与性能瓶颈全部盘一遍，给出可分批落地的改造方案。
> 目标机型基线：**Intel UHD 集显 / 4C8T / 8GB / 1080p~4K 外接投影**，即普通教室讲台机。

---

## 0. 结论速览

卡顿不是单点问题，是 **6 条独立的主线程长任务叠加**在同一个 rAF 周期里。按「投入产出比」排序：

| # | 根因 | 触发时机 | 量级 | 修复难度 | 预期收益 |
|---|------|----------|------|----------|----------|
| R1 | **主画布每帧全量重绘**，无缓存/无裁剪/无脏矩形 | 平移、缩放、整笔擦、撤销、resize | O(元素数 × 点数)/帧 | 中 | ★★★★★ |
| R2 | **localStorage 全量 `JSON.stringify`**（最多 24 块板）每笔一次 | 每次抬笔后 450ms | 同步阻塞 10~300ms | 低 | ★★★★★ |
| R3 | **撤销快照 `JSON.parse(JSON.stringify)`** 全量深拷贝 × 36 层 | 每一笔、每次擦除 | CPU + 内存 × 36 | 中 | ★★★★☆ |
| R4 | **整笔橡皮命中测试** O(N×P) × 每个 coalesced 点 | 按住橡皮拖动 | 每帧数十万次距离计算 | 中 | ★★★★☆ |
| R5 | **工具栏 `backdrop-filter: blur(18px)` 常驻在画布之上** | 画布每次重绘 | GPU/软件合成重算 | 低 | ★★★★☆ |
| R6 | **网格层 4 层全屏渐变 + 根元素自定义属性每帧变更** | 平移 | 全屏重绘 + 子树样式失效 | 低 | ★★★☆☆ |

次级但明确的问题另有 14 条（见第 2 章 P1/P2），其中 **考试答题板每笔 `toDataURL('image/png')`**（`exam_board.js:302`）是独立的重灾区，只是不在「学习文档」路径上。

---

## 1. 关联功能全景

### 1.1 三个宿主，两套实现

| 宿主 | 文件 | 白板类型 | 说明 |
|------|------|----------|------|
| HTML 包全屏壳（学习文档主路径） | `templates/material_render_shell.html` + `static/js/material_render_shell.js:168` | `TeacherWhiteboard`（矢量） | **覆盖在 `<iframe id="render-shell-frame">` 之上**，`requestIdleCallback` 空闲加载 |
| Markdown 材料页 | `static/js/material_viewer.js:1507` | `TeacherWhiteboard`（矢量） | 同上，覆盖在文档 DOM 之上 |
| 考试答题页 | `templates/exam_take.html` | `ExamDrawingWhiteboard`（位图） | 独立实现，共用 `teacher-whiteboard-*` 类名 |

入口 shim：`static/js/teacher_whiteboard.js`（21 行，只做 `import` + `bootstrap`）。
门禁：`board.js:41` `WHITEBOARD_ALLOWED_ROLES = new Set(['teacher'])`，学生不加载。

### 1.2 模块职责地图（`static/js/whiteboard/`，3694 行）

```
board.js        771  主类：DOM 编排、撤销栈、面板、开关、视口、画布尺寸、渲染调度
interaction.js  264  mixin：指针事件、草稿层、像素/整笔橡皮
renderer.js     204  纯绘制：stroke / shape / text / eraser + renderElements
geometry.js     197  纯数学：距离、包围盒、命中测试、RDP 抽稀
state.js        204  纯函数：创建/规范化/v1→v2 迁移/判空/clamp
constants.js    118  默认值、限额、图标
store_local.js   88  localStorage v2 读写 + 裁剪
store_remote.js 119  REST 客户端 /api/materials/{id}/whiteboards
sync.js         234  本地↔线上合并、dirty 队列、30s 定时、冲突副本
toolbar.js       58  工具栏 HTML 模板
text_editor.js   49  mixin：舞台内 textarea
fab.js           86  mixin：悬浮球拖拽与持久化
export.js       153  取景/离屏渲染/5MB 拟合/下载
exam_board.js   397  考试位图板（独立）
panels/*         7 个  popover：画笔、文字、墨迹、背景、橡皮、保存、历史、导出、确认
```

### 1.3 数据链路

```
内存 state
  ├─ 每笔抬笔 → pushUndoSnapshot（深拷贝）→ elements.push → markDirty
  ├─ markDirty → scheduleSave(450ms) → persistLocal → saveLocalState
  │                                     └─ JSON.stringify(整个 state：24 块板) → localStorage
  ├─ 30s 定时 / 关闭 / 隐藏页 → sync.flushDirty
  │     └─ prepareElements（全量 map + RDP）→ JSON.stringify 量体积 → fetch PUT（再 stringify 一次）
  └─ 后端 material_whiteboard_service.py
        MAX_ELEMENTS = 20000，MAX_ELEMENTS_BYTES = 2MB，乐观锁 version
```

### 1.4 渲染管线（当前）

```
.teacher-whiteboard-root            position:fixed inset:0 z:2400
 ├─ .teacher-whiteboard-stage       contain 无；touch-action:none
 │   ├─ ::before                    全屏 4 层 linear-gradient 网格，opacity: var(--bg-alpha)=0.78
 │   ├─ .canvas-layer               opacity: var(--ink-alpha)  ← 建独立合成组
 │   │   ├─ <canvas> 主画布          drawMainCanvas 全量重绘
 │   │   └─ <canvas> 草稿画布        进行中的笔画/形状，屏幕坐标增量
 │   └─ .twb-eraser-cursor          will-change: transform
 └─ .twb-toolbar                    backdrop-filter: blur(18px) saturate(140%)  ← 每帧重算
（下方还有 iframe：整个学习文档，同源）
```

---

## 2. 卡顿根因逐条剖析

### P0 — 必须先修

---

#### R1 主画布每帧全量重绘

**位置**：`board.js:710 scheduleRender()` → `board.js:719 drawMainCanvas()` → `renderer.js:185 renderElements()`

```js
drawMainCanvas() {
    this.setScreenTransform(this.ctx);
    this.ctx.clearRect(0, 0, this.canvasWidth, this.canvasHeight);
    renderElements(this.ctx, this.activeBoard?.elements || [], this.viewport);  // 无条件遍历全部
}
```

**问题**：
- 没有**分层缓存**：已提交的内容每帧都从头重画一遍。
- 没有**视口裁剪**：缩小到 0.35 倍或平移到画布空白处，屏幕外的元素照样全部走一遍 `beginPath/quadraticCurveTo/stroke`。
- 没有**脏矩形**：擦掉一笔要重画整块板。
- 没有 **LOD**：`viewport.scale` 很小的时候仍然按原始点密度绘制。

**触发路径**（全部会调 `scheduleRender(true)`）：
`interaction.js:158`（平移，每个 pointermove）、`board.js:747/759`（缩放/回中）、`interaction.js:101`（整笔擦，**每个 coalesced 点一次**）、`board.js:454/463`（撤销/重做）、`board.js:493`（清空）、`board.js:372`（切板）、`board.js:628`（窗口 resize）。

**量级估算**：一节课板书约 300~800 笔，一笔 60~400 个点（阈值只有 0.8px，见 R9）。取中位 500 笔 × 150 点 = 75000 个点，每帧要走 75000 次 `quadraticCurveTo` + 500 次 `stroke()`。集显机上单帧 30~80ms → **平移时 12~30fps**，元素再多就掉到个位数。

---

#### R2 localStorage 全量序列化

**位置**：`board.js:270 persistLocal()` → `store_local.js:58 saveLocalState()`

```js
const attempt = (payload) => {
    window.localStorage.setItem(keys.current, JSON.stringify(payload));  // payload = 整个 state，含全部板的全部 elements
};
```

**问题**：
1. `JSON.stringify` 是**同步阻塞**主线程的，`localStorage.setItem` 还要**同步写盘**。
2. 序列化的是**整个 state**，不是当前板：`MAX_BOARDS = 24`，本地新建的板全都带着完整 `elements`（只有从云端拉下来的 stub 是空的）。
3. 触发频率高：`markDirty()`（每笔）、`updateSettings()`（每次拖滑块）、`zoomBy()`（每次滚轮）、`activeBoard.viewport` 变更（每次平移抬手）都会 `scheduleSave(450)`。
4. **失败路径更贵**：超配额抛异常后，`saveLocalState` 会再裁到 8 块板**再 stringify 一次**（`store_local.js:64-70`）。localStorage 配额通常 5MB，而单板上限就是 2MB —— 两三块大板就必然走这条双倍开销的路径，同时弹 toast。

**症状**：这就是老师反馈的「画着画着突然顿一下」——不是渲染慢，是抬笔后 450ms 那一次同步序列化。

---

#### R3 撤销快照全量深拷贝

**位置**：`board.js:441 pushUndoSnapshot()` + `state.js:85 cloneElements()`

```js
export function cloneElements(elements) {
    return JSON.parse(JSON.stringify(Array.isArray(elements) ? elements : []));
}
pushUndoSnapshot() {
    this.undoStack.push(cloneElements(this.activeBoard.elements));   // 每一笔一次全量深拷贝
    if (this.undoStack.length > UNDO_LIMIT) this.undoStack.shift();  // UNDO_LIMIT = 36
}
```

**问题**：
- 时间：每笔一次 `JSON.parse(JSON.stringify(全部元素))`，500 笔的板约 3~5MB 文本，单次 30~150ms。
- 空间：36 层快照 × 全量元素 = **板体积的 37 倍常驻内存**。500 笔板 ≈ 100~180MB JS 堆，8GB 机器上直接触发频繁 GC，表现为周期性长卡顿。
- `undo()` / `redo()` 里还各有一次 `cloneElements`（`board.js:451/459`）。
- `Array.shift()` 在 36 长度上无所谓，可忽略。

---

#### R4 整笔橡皮命中测试

**位置**：`interaction.js:91 eraseStrokesAt()`，由 `interaction.js:174` 对**每个 coalesced 点**调用

```js
eraseStrokesAt(worldPoint) {
    const survivors = elements.filter((el) => !hitTestElement(el, worldPoint, radius, this.measureWidth));
    ...
    this.scheduleRender(true);   // 每个点都请求一次全量重绘
}
```

`hitTestElement`（`geometry.js:139`）对 stroke 逐段做 `pointToSegmentDistance`（含 `Math.hypot`）。

**量级**：一个 pointermove 事件的 `getCoalescedEvents()` 在 240Hz 数位板/高刷鼠标上能给出 4~16 个点。500 笔 × 150 点 × 12 个点 = **90 万次 hypot / 帧**。这是整笔橡皮几乎不可用的原因。

另外：`hitTestElement` 对 text 元素会调 `textBox()` → `measureWidth()` → `ctx.measureText()`，**每个点、每个文本元素各一次**，属于 canvas API 调用（不可 JIT 内联）。

---

#### R5 工具栏 backdrop-filter 常驻画布之上

**位置**：`static/css/ui-system.src.css:37282`

```css
.teacher-whiteboard-toolbar { backdrop-filter: blur(18px) saturate(140%); }
```

**问题**：`backdrop-filter` 要求浏览器把该元素**背后的所有内容**（网格层 + 主画布 + 草稿画布 + iframe）先合成成一张位图，再做 18px 高斯模糊 + 饱和度矩阵。工具栏宽度接近全屏宽（`right: max(100px, ...)`，`min-height: 66px`）。
**主画布每重绘一次，工具栏背后的内容就变了一次，这张模糊位图就要重算一次**。在没有独立 GPU 或走软件合成（远程桌面、投影仪扩展屏、老驱动）的机器上，单次 3~15ms，且发生在合成线程，直接压掉帧率。

同一 CSS 文件里 `backdrop-filter` 出现 **123 处**，是全站性的设计语言，白板这一处是最要命的（因为背后内容每帧都在变）。

另有 `.twb-status-dot[data-status="saving"]` 的 `twb-pulse` **无限 opacity 动画**（`ui-system.src.css:37642-37651`）落在 backdrop-filter 容器内部，同步期间会持续触发该区域重栅格。

---

#### R6 网格层：全屏 4 层渐变 + 每帧自定义属性变更

**位置**：CSS `ui-system.src.css:37209`；JS `board.js:260 updateGridPosition()`

```css
.teacher-whiteboard-stage::before {
    background-image: linear-gradient(...) ×4;      /* 细网格 40px ×2 + 主网格 200px ×2 */
    background-position: var(--pan-x) var(--pan-y) ×4;
    opacity: var(--teacher-whiteboard-bg-alpha);    /* 0.78，半透明压在 iframe 上 */
}
```
```js
updateGridPosition() {
    this.rootEl.style.setProperty('--teacher-whiteboard-pan-x', ...);   // 4 个属性
    ...
}
```
被 `interaction.js:157`（平移每个 pointermove）和 `board.js:747`（每次缩放）调用。

**三重开销**：
1. 自定义属性写在 `.teacher-whiteboard-root` 上 → **整棵子树（工具栏 40+ 个按钮、所有 popover）样式失效重算**。
2. `background-position` 变化 → 全屏 4 层渐变**重新栅格化**（不是 composite-only 属性）。
3. 0.78 的 `opacity` 让这一层必须和下面的 iframe 做**逐像素混合**。

---

### P1 — 明确的次级开销

---

#### R7 每个点一次 `getBoundingClientRect()`（强制同步布局）

`interaction.js:63`：
```js
getStagePoint(event) {
    const rect = this.stageEl.getBoundingClientRect();   // 强制 layout
    return { x: event.clientX - rect.left, ... };
}
```
被 `interaction.js:164/169/174` 在 **coalesced 循环内**逐点调用。一帧 12 个点 = 12 次强制同步布局，而此时正好有 CSS 自定义属性刚被写脏（R6）→ **典型的 layout thrashing**。
另外 `handleStagePointerDown`（`interaction.js:107`）每次落笔先调 `this.resizeCanvases()`，里面又是一次 `getBoundingClientRect()`。

**修法极简**：pointerdown / resize / scroll 时缓存 `stageRect`，用 `ResizeObserver` 维护。

---

#### R8 像素橡皮软边用 `ctx.filter = blur()`

`renderer.js:102`：
```js
if (pass.blur > 0) ctx.filter = `blur(${pass.blur.toFixed(2)}px)`;
```
Canvas 2D 的 `filter` 在主流实现里走**慢路径**（每次 stroke 都要额外分配中间层做卷积）。更糟的是：这些 `eraser` 元素被存进 `elements` 数组，**每一次全量重绘都要把所有软边橡皮的 blur 重放一遍**。一块板上只要用过几次软橡皮，从此这块板的每帧成本永久上升。

不支持 `filter` 时的降级是三层递减 alpha 描边（`renderer.js:80-89`），等于 **3 倍描边成本**。

---

#### R9 笔画点没有实时抽稀

`interaction.js:208 addStrokePoint()`：屏幕距离阈值仅 **0.8px**，即高 DPI 下几乎每个 coalesced 点都入库。
`simplifyStroke()`（RDP，`geometry.js:174`）**只在上传时**用（`sync.js:10 prepareElements`，tolerance 0.35），内存模型和 localStorage 里存的一直是原始密集点。

后果：内存点数是必要点数的 3~8 倍，R1/R2/R3/R4 的成本全部按这个倍数放大。

---

#### R10 草稿层逐段绘制，未合批

`interaction.js:12 drawScreenSegment()`：每个点一次 `setTransform + save + beginPath + stroke + restore`。一帧 12 个点 = 12 组状态切换 + 12 次 `stroke()`。
应该改成：pointermove 只收点，rAF 里一次 `beginPath` 把本帧新增的所有段连成一条路径再 `stroke()`。

---

#### R11 `markDirty` 每笔全量 filter

`board.js:294`：
```js
this.activeBoard.elementCount = this.activeBoard.elements.filter((el) => el.type !== 'eraser').length;
```
O(N) 且产生一个临时数组。应改为增量计数。

---

#### R12 墨迹层 `opacity` 建组

`ui-system.src.css:37250`：`.teacher-whiteboard-canvas-layer { opacity: var(--ink-alpha); }`
只要 alpha < 1，两张 canvas 就必须先合成成一个组再整体做透明度 —— 多一次全屏 blit。alpha = 1 时也因为是变量而可能被保守处理。

---

#### R13 窗口 resize 未防抖

`board.js:624 handleResize()`：直接 `resizeCanvases()` + `updateGridPosition()` + `scheduleRender(true)`。
`resizeCanvases`（`board.js:688`）里 `canvas.width = ...` **会重新分配画布后备存储并清空内容**。拖动窗口/切换投影分辨率时连续触发数十次「重分配 + 全量重绘」。

---

#### R14 打开白板的瞬间做网络 I/O

`board.js:561 open()`：
```js
this.sync.start();                                        // 30s 定时器
this.sync.bootstrap().then(() => this.adoptRemoteBoardIfFresh());
```
`bootstrap()` 拉列表 → 合并 → `persistLocal()`（又一次全量 stringify）→ `flushDirty()`（可能上传若干块板）。
老师点开白板就是要立刻写字，这一串正好挤在第一笔上。

---

#### R15 30s 定时同步是周期性长任务

`sync.js:128`：
```js
const elements = prepareElements(board.elements);            // 全量 map + RDP，O(N·P·logP)
const payloadSize = JSON.stringify(elements).length;         // 第 1 次全量序列化
... store.upsert(...)                                        // fetch 内部第 2 次序列化
```
**同样的数据被遍历 1 次 + 序列化 2 次**，且 `simplifyStroke` 的结果没有被缓存，每 30 秒重算一遍。板越大，这个周期性尖峰越明显（用户感受：「每隔半分钟卡一下」）。

---

#### R16 考试板每笔 `toDataURL('image/png')`

`exam_board.js:302 / 322 / 330`：
```js
this.history.push(this.canvasEl.toDataURL('image/png'));   // 每笔一次全屏 PNG 同步编码
```
`maxHistory = 24`。1920×1080@2dpr 的画布做一次 PNG 编码在集显机上 **80~400ms**，且 24 张 base64 字符串常驻内存（每张 1~5MB 文本）。这是考试页「点一下笔就卡住」的直接原因。
`resizeCanvas({preserve:true})`（`exam_board.js:178`）还会在每次窗口尺寸变化时再来一次。

---

### P2 — 体验与工程化

| # | 问题 | 位置 |
|---|------|------|
| R17 | 没有 `pointerrawupdate` / 落点预测，笔迹跟手延迟被感知为"卡" | `interaction.js:139` |
| R18 | `stage` / `canvas-layer` 缺 `contain: strict` 与合适的 `will-change`，浏览器无法限制失效范围 | CSS 37200~37260 |
| R19 | 元素包围盒每次用到都现算（`elementBounds`），无缓存 | `geometry.js:95` |
| R20 | `sanitizeElement`（`state.js:120`）**原样透传 raw 对象**，未来给元素挂运行时字段（如 `_bbox`）会被一并写进 localStorage 并上传；后端 `_normalize_elements` 也不剥离未知字段 | `state.js:120`、`material_whiteboard_service.py:118` |
| R21 | 后端 `MAX_ELEMENTS = 20000` 与前端可流畅承载的量级（约 2000~3000）严重不匹配，没有前端软上限提示 | `material_whiteboard_service.py:29` |
| R22 | 白板打开时下方 iframe（学习文档）仍在正常参与合成；同源可访问，却没有做任何"暂停/降级"处理 | `material_render_shell.js` |
| R23 | 没有任何性能埋点，问题无法量化、无法回归 | 全局 |
| R24 | 没有低性能模式，所有机器一套参数（DPR 上限 2.5、软边橡皮、玻璃拟态全开） | `constants.js` |

---

## 3. 改进方案

### 3.1 渲染架构：三层画布 + 提交层位图缓存（对应 R1）

**核心洞察**：当前元素模型是**追加式**的 —— 新元素永远画在最上面，像素橡皮的 `destination-out` 也只影响它之前的内容。这意味着**「已提交内容」可以安全地缓存成一张位图**，新元素增量叠加即可，无需重放历史。

**新管线**：

```
┌ committed（离屏，世界坐标 → 屏幕坐标烘焙）  ← 只在"失效"时重建
├ live（草稿层，进行中的笔画/形状/橡皮预览）  ← 每帧清空重画，成本 O(1)
└ 合成：drawImage(committed) + live
```

**失效（invalidate）条件**（其余情况一律不重建）：
- 撤销 / 重做 / 清空 / 切板 / 远端 patch
- `viewport.scale` 变化超过阈值（见下）
- 画布尺寸变化

**平移 / 缩放的快路径**：
- 平移：`committed` 位图直接 `drawImage` 到新偏移，边缘露出的条带才需要补画（脏矩形）。或更简单：先整体位移显示（低清预览），`requestIdleCallback` 里再精绘。
- 缩放：先 `drawImage` 做缩放预览（模糊但即时），滚轮停止 120ms 后重建高清 committed 层。这是所有专业画板（Figma/Excalidraw）的标准做法。

**增量提交**：`finishDrawing()` 里把新元素**只画到 committed 层**，不触发全量重建。

**实现建议**：
- 用 `OffscreenCanvas`（`typeof OffscreenCanvas !== 'undefined'` 时）；不支持则用普通离屏 `<canvas>`。
- committed 层尺寸 = 视口尺寸 × dpr（不是无限画布尺寸），平移超出后按脏条带补画。
- 第二阶段可升级为 **256×256 世界瓦片 + LRU 缓存**，彻底解决超大板与快速平移。

**改动文件**：`board.js`（`drawMainCanvas` / `scheduleRender` / `resizeCanvases` / `zoomBy` / `activateBoard`）、新增 `whiteboard/render_cache.js`。

---

### 3.2 空间索引 + 视口裁剪（对应 R1、R4、R19）

新增 `whiteboard/spatial_index.js`：

```js
// 均匀网格哈希，cell = 256 世界像素
class SpatialIndex {
  insert(element)          // 用缓存的 bbox 落到若干 cell
  remove(elementId)
  queryRect({x,y,w,h})     // 视口裁剪
  queryPoint(p, radius)    // 橡皮命中候选集
}
```

配套：**元素包围盒在创建时算一次**，挂在一个 `WeakMap<element, bbox>` 上（**不要挂在元素对象上**，避免 R20 的脏字段问题）。

- `renderElements` 改为 `queryRect(viewport 可视世界矩形)` 后再绘制 → 缩小/平移时成本随可见元素而非总元素增长。
- `eraseStrokesAt` 改为 `queryPoint()` 拿候选（通常个位数）再做精确 `hitTestElement` → **从 O(N·P) 降到 O(k·P)，k≈3**。

---

### 3.3 撤销栈改为命令日志（对应 R3）

放弃全量快照，改存**逆操作**：

```js
// undoStack 元素形如：
{ type: 'add',    ids: ['stroke-x'] }                       // 撤销 = 按 id 删
{ type: 'remove', elements: [...被删元素的引用...] }         // 撤销 = 重新插回原位置
{ type: 'clear',  elements: [...整块板引用...] }             // 撤销 = 整体还原
```

- 内存从 **O(36 × 全板)** 降到 **O(变更量)**（一笔 = 一个 id）。
- 时间从「每笔 30~150ms 深拷贝」降到 **O(1)**。
- `UNDO_LIMIT` 可以从 36 提到 100+ 而内存反而更小。
- 元素对象本身是不可变的（画完不再修改），所以存引用即可，无需拷贝。
- 撤销后需要 `invalidate()` 重建 committed 层（低频操作，可接受）。

**兼容**：`clearBoard` 的 `elements` 引用要在 `activeBoard.elements = []` 之前抓住。

---

### 3.4 持久化改造（对应 R2）

分三步，收益递增：

**第一步（低风险，立刻可做）**
- **分键存储**：`teacher-whiteboard:v2:{user}:{material}:index`（只存板元信息 + settings + activeBoardId）+ `...:board:{boardId}`（每块板一条）。保存时**只写当前活动板 + index**，其余板不动。
- 提高防抖：`scheduleSave(450)` → `scheduleSave(1500)`，并在 `pointerup` 之后用 `requestIdleCallback` 触发。
- 保存前判断内容是否真的变了（`dirty` + 元素数变化），避免拖滑块也全量写盘。

**第二步（推荐）**
- 迁移到 **IndexedDB**：结构化克隆、异步、无 5MB 配额墙、天然支持按板存取。localStorage 只保留一个轻量索引用于快速冷启动。
- 保留 v2 localStorage 读路径做一次性迁移（照 `migrateLegacyState` 的成例）。

**第三步（可选）**
- 序列化 + 抽稀放进 **Web Worker**，主线程只传结构化克隆的元素数组。

---

### 3.5 同步链路优化（对应 R15、R14）

- **抽稀结果缓存**：笔画在 `finishDrawing` 时就生成一份 `simplified points` 存在 `WeakMap` 里，`prepareElements` 直接取，不再每 30 秒重算。
- **只序列化一次**：`store_remote.upsert` 改为接受已序列化的 body 字符串（`fetch` 的 `body` 支持 string），量体积和发送共用同一份。
- **触发策略**：把固定 30s 定时改为「**变更量阈值 + 空闲**」：累计新增 ≥ 50 个元素，或距上次同步 ≥ 60s，且 `requestIdleCallback` 有空闲时才发。绘制过程中（`activePointer !== null`）一律不同步。
- **打开时延后**：`open()` 里的 `sync.bootstrap()` 改为 `requestIdleCallback(..., {timeout: 3000})`，让第一笔先落下去。
- **第二阶段（需后端配合）**：增加 `POST /api/materials/{id}/whiteboards/{key}/ops` 追加式增量端点，只传新增元素 + 版本号，服务端 append。全量 PUT 只在冲突修复/首次创建时用。

---

### 3.6 CSS 与合成层优化（对应 R5、R6、R12、R18）

| 项 | 现状 | 改法 |
|----|------|------|
| 工具栏毛玻璃 | `backdrop-filter: blur(18px) saturate(140%)` 常驻 | ① 默认改为**不透明渐变 + 内阴影**模拟玻璃质感（视觉差异极小）；② 或保留但在 `.is-drawing`（落笔时加、抬笔 200ms 后移除）期间临时置 `backdrop-filter: none`；③ 低性能模式下永久关闭。同时加 `contain: paint` |
| 网格层 | 4 层全屏渐变 + `background-position` 跟随平移 | ① 减到 **2 层**（细网格用 1 层双向重复的 `conic`/`repeating-linear-gradient`，主网格单独 1 层）；② 平移改用 **`transform: translate3d()`** 驱动一个比视口大 1 个网格周期的层（composite-only，不重栅格）；③ 或干脆把网格画进 committed canvas 的最底层，彻底去掉这个 DOM 层 |
| 自定义属性写在 root | 导致整棵子树样式失效 | 把 `--pan-x/--pan-y/--grid-size` 挪到**网格层自己的元素**上（`.teacher-whiteboard-stage::before` 需要改成真实元素 `.twb-grid`），隔离失效范围 |
| 墨迹层 opacity | `opacity: var(--ink-alpha)` 恒定存在 | alpha ≥ 0.99 时**移除 opacity 声明**（用类切换）；alpha < 1 时改为在 `drawMainCanvas` 里设 `ctx.globalAlpha`，避免建合成组 |
| 容器 containment | 无 | `.teacher-whiteboard-stage { contain: strict; }`、`.twb-toolbar { contain: layout paint; }` |
| 状态点脉冲动画 | backdrop-filter 容器内无限 opacity 动画 | 移到工具栏外层，或改为 `transform: scale()`（仍是 composite-only 但脱离模糊区），或同步中只改颜色不做动画 |

**注意**：改完必须 `npm run build:css`（源在 `static/css/ui-system.src.css` 的 `/* --- Source: static/css/teacher_whiteboard.css --- */` 段，约 37172~38600 行；产物 `static/css/tailwind-app.css`）。

---

### 3.7 输入链路优化（对应 R7、R9、R10、R17）

```js
// 1) 缓存 stage 矩形
init() { this.stageRect = null; new ResizeObserver(() => this.stageRect = null).observe(this.stageEl); }
getStagePoint(e) {
    if (!this.stageRect) this.stageRect = this.stageEl.getBoundingClientRect();
    return { x: e.clientX - this.stageRect.left, y: e.clientY - this.stageRect.top };
}
// 失效点：pointerdown、resize、scroll、open()
```

```js
// 2) pointermove 只收点，绘制交给 rAF 合批
handleStagePointerMove(e) {
    for (const pe of coalesced) this.pendingPoints.push(this.getStagePoint(pe));
    this.scheduleDraftFlush();          // rAF，一帧一次
}
flushDraft() {
    // 一次 beginPath，把 pendingPoints 连成一条路径，一次 stroke()
}
```

```js
// 3) 入库前抽稀：阈值按 devicePixelRatio 和 brushSize 自适应
const minDist = Math.max(1.2, this.settings.brushSize * 0.25);   // 原来固定 0.8
// 4) 抬笔时对整笔做一次 RDP（tolerance 0.5 世界像素），入库存精简版
```

```js
// 5) 可选：pointerrawupdate 降低跟手延迟（只用于草稿层预览，不入库）
this.stageEl.addEventListener('pointerrawupdate', ..., { passive: true });
```

---

### 3.8 橡皮擦优化（对应 R4、R8）

- **软边不用 `ctx.filter`**：启动时用一个小离屏 canvas 生成 **径向渐变笔刷贴图**（如 64×64，中心不透明→边缘透明，按 `hardness` 生成 3~4 档并缓存），绘制时沿路径按间距 `drawImage` 盖章（`globalCompositeOperation = 'destination-out'`）。这是位图橡皮的标准做法，比 blur 快一个数量级且效果一致。
- **整笔擦**接空间索引（3.2），并从「每个点一次全量重绘」改为「每帧一次、只重绘被删元素的并集脏矩形」。
- 低性能模式下 `eraserHardness` 强制为 1（硬边，单层描边）。

---

### 3.9 考试答题板（对应 R16）

`exam_board.js` 是位图板，改造独立且简单：

- **快照改双缓冲**：`history` 存 `ImageBitmap`（`createImageBitmap(canvas)`，异步、GPU 侧）或离屏 canvas 的 `drawImage` 拷贝，**不要 `toDataURL`**。
- `maxHistory` 从 24 降到 12（位图快照内存大），或改为「每 5 笔一个关键帧 + 中间存笔画向量」。
- `resizeCanvas({preserve:true})` 的保存改用 `drawImage(oldCanvas)`，同样避开 PNG 编码。
- 最终提交（`exam_board.js:373`）保留 `toDataURL`，那是必须的一次。

---

### 3.10 低性能模式（对应 R24）

新增 `whiteboard/perf_profile.js`：

```js
export function detectProfile() {
    const cores = navigator.hardwareConcurrency || 4;
    const mem = navigator.deviceMemory || 4;
    // 首次打开时用 20 帧渲染采样兜底校正
    if (cores <= 4 || mem <= 4) return 'lite';
    return 'full';
}
```

`lite` 档降级项（全部可由用户在设置里手动覆盖）：

| 项 | full | lite |
|----|------|------|
| DPR 上限 | 2.5 | 1.25 |
| 工具栏 backdrop-filter | 开 | 关 |
| 网格层 | 细 + 主两级 | 只留主网格 |
| 软边橡皮 | 开 | 强制硬边 |
| 笔画抽稀 tolerance | 0.5 | 1.2 |
| undo 层数 | 100 | 40 |
| 缩放预览 | 高清重建 120ms | 300ms |
| 自动同步 | 60s / 50 元素 | 120s / 200 元素 |

**入口**：工具栏「背景」芯片旁加一个「流畅模式」开关；偏好持久化复用已有的 `user_ui_preferences`（`static/js/user_ui_preferences.js` + `classroom_app/routers/user_ui_preferences.py`），跨设备生效。

**补充**：`constants.js` 的 `clamp(window.devicePixelRatio, 1, 2.5)`（`board.js:693`）在 4K 投影上会创建 3840×2160×2.5 的后备存储——**8300 万像素、约 133MB 显存**。这一条单独就值得降到 1.5。

---

### 3.11 与学习文档 iframe 的协同（对应 R22）

iframe 与壳页**同源**（`material_render_shell.js` 已在读 `frame.contentDocument`），可以直接协同：

```js
// 白板打开/关闭时，通知文档侧降级
function setDocQuiet(on) {
    const doc = frameEl.contentDocument;
    doc?.documentElement.classList.toggle('ld-quiet', on);
}
```

在 `lessondoc/2.0/slides.css` / `course.css` 里加：

```css
.ld-quiet *, .ld-quiet *::before, .ld-quiet *::after {
    animation-play-state: paused !important;
    transition: none !important;
}
.ld-quiet .ld-nav { backdrop-filter: none; }   /* course.css:71 */
```

进一步：当「背景不透明度」调到 ≥ 0.98（白板完全遮住文档）时，直接给 iframe 加 `visibility: hidden` —— 浏览器就完全不再合成这一整棵文档树，收益非常大。调回半透明时恢复。

---

### 3.12 容量治理（对应 R21）

- 前端在**当前板非橡皮元素数 ≥ 1500** 时，在保存菜单里出现浅提示「这块板笔迹较多，新建一块会更流畅」；≥ 3000 时 toast 一次。
- `MAX_ELEMENTS` 后端保持 20000 作为硬闸，但前端不应该让用户走到那里。
- 历史面板已经显示「N 笔」（`panels/history_panel.js:38`），把超阈值的行标黄即可，几乎零成本。

---

## 4. 落地计划

四个批次，每批可独立发布、独立回滚。

### 批次 A — 「不改架构的立竿见影」（约 1~1.5 天）

| 改动 | 文件 | 对应 |
|------|------|------|
| 缓存 `stageRect`，ResizeObserver 失效 | `interaction.js`、`board.js` | R7 |
| 草稿层 rAF 合批 + 抽稀阈值自适应 + 抬笔 RDP | `interaction.js` | R9、R10 |
| `markDirty` 增量计数 | `board.js:294` | R11 |
| `scheduleSave` 防抖 450→1500 + idle 触发 | `board.js:286` | R2（部分） |
| localStorage 分键存储（只写活动板） | `store_local.js`、`board.js` | R2 |
| resize 防抖 200ms | `board.js:624` | R13 |
| `open()` 里 bootstrap 延后到 idle | `board.js:561` | R14 |
| 工具栏 backdrop-filter：绘制时临时关闭 + `contain: paint` | `ui-system.src.css` | R5 |
| DPR 上限 2.5 → 1.75 | `board.js:693` | R24 |
| 考试板 `toDataURL` → canvas 拷贝 | `exam_board.js` | R16 |

**预期**：抬笔顿挫基本消失；考试板恢复可用；平移仍慢（等批次 B）。

---

### 批次 B — 「渲染架构」（约 3~4 天）

| 改动 | 文件 | 对应 |
|------|------|------|
| 新增 `render_cache.js`：committed 位图层 + 增量提交 + 失效机制 | 新文件、`board.js` | R1 |
| 平移/缩放快路径（位移预览 + 空闲精绘） | `board.js`、`interaction.js` | R1 |
| 新增 `spatial_index.js` + bbox WeakMap 缓存 | 新文件、`geometry.js` | R1、R4、R19 |
| 视口裁剪接入 `renderElements` | `renderer.js` | R1 |
| 整笔橡皮走索引 + 帧节流 + 脏矩形 | `interaction.js` | R4 |
| 网格层改真实元素 + transform 驱动（或并入 canvas） | `ui-system.src.css`、`board.js` | R6 |
| 墨迹 opacity 改 `globalAlpha` | `ui-system.src.css`、`board.js` | R12 |

**预期**：平移/缩放稳定 60fps；整笔橡皮从不可用变流畅。

---

### 批次 C — 「内存与持久化」（约 2 天）

| 改动 | 文件 | 对应 |
|------|------|------|
| 撤销栈改命令日志 | `board.js`、新增 `history_stack.js` | R3 |
| IndexedDB 存储 + v2 localStorage 一次性迁移 | `store_local.js` → 新增 `store_idb.js` | R2 |
| 抽稀结果缓存 + 单次序列化 + 阈值触发同步 | `sync.js`、`store_remote.js` | R15 |
| 软边橡皮改笔刷贴图 | `renderer.js` | R8 |
| 元素脏字段防护（`sanitizeElement` 白名单化 + 后端剥离未知字段） | `state.js`、`material_whiteboard_service.py` | R20 |

**预期**：大板内存从 150MB+ 降到 10MB 量级，周期性 GC 卡顿消失。

---

### 批次 D — 「降级与治理」（约 1.5 天）

| 改动 | 文件 | 对应 |
|------|------|------|
| `perf_profile.js` + 流畅模式开关 + 偏好持久化 | 新文件、`toolbar.js`、`user_ui_preferences` | R24 |
| iframe 协同降级（`ld-quiet` / 不透明时隐藏） | `material_render_shell.js`、`lessondoc/2.0/*.css` | R22 |
| 容量提示 | `panels/history_panel.js`、`panels/save_menu.js` | R21 |
| 性能埋点（见第 5 章） | `board.js`、新增 `perf_probe.js` | R23 |

---

### 后端（可选，第二阶段）

- 增量 ops 端点（3.5）。
- `_normalize_elements` 增加字段白名单，避免前端运行时字段落库。
- `elements_json` 考虑存压缩（gzip/br）列，减少 2MB 上限下的实际网络与磁盘开销。

---

## 5. 度量与验收

### 5.1 基准场景

| 场景 | 数据量 | 操作 |
|------|--------|------|
| S1 轻 | 100 笔 / 每笔 80 点 | 连续书写 60s |
| S2 中 | 800 笔 / 每笔 150 点 | 连续书写 + 平移 20 次 + 缩放 10 次 |
| S3 重 | 3000 笔 / 每笔 200 点 | 同上 + 整笔橡皮拖动 10s |
| S4 混合 | S2 + 5 个文本 + 20 个形状 + 10 次软边橡皮 | 同上 |

**低端模拟**：Chrome DevTools `Performance → CPU: 6× slowdown`，配合 `--force-device-scale-factor=2`；另建议实机验证一台无独显的 8GB 讲台机。

### 5.2 指标与门槛

| 指标 | 采集方式 | 目标（S2，6× 降速） |
|------|----------|----------------------|
| 书写帧率 | rAF 间隔 P95 | ≥ 50fps（现状约 15~25） |
| 平移帧率 | 同上 | ≥ 55fps（现状约 12~30） |
| pointerdown → 首像素 | `performance.mark` | ≤ 32ms |
| 抬笔后最长任务 | `PerformanceObserver('longtask')` | ≤ 50ms（现状 100~400ms） |
| 整笔橡皮拖动帧率 | rAF 间隔 P95 | ≥ 45fps（现状 <10） |
| 切板耗时 | `performance.measure` | ≤ 200ms |
| JS 堆峰值 | `performance.memory.usedJSHeapSize` | ≤ 60MB（现状 150MB+） |
| 30s 同步尖峰 | longtask | ≤ 30ms |

### 5.3 自动化

- **单测（vitest，`npm test`）**：现有 `geometry.test.js` / `state.test.js` / `export.test.js` 可直接扩展。新增：
  - `spatial_index.test.js`：插入/删除/查询正确性 + 与暴力遍历结果一致性（属性测试）。
  - `history_stack.test.js`：命令日志与旧快照语义等价（对同一操作序列，最终 elements 一致）。
  - `render_cache.test.js`：失效条件覆盖（撤销/清空/切板/缩放阈值）。
  - `store_idb.test.js`：v2 localStorage → IndexedDB 迁移幂等。
- **E2E（playwright，`npm run test:e2e`）**：录一段固定的 pointer 轨迹回放，断言 `longtask` 数量与总时长不回退；把指标写进 `docs/whiteboard-performance-evidence-YYYY-MM-DD.json`（沿用本仓库既有的 release-evidence 约定）。
- **埋点**：`perf_probe.js` 采集上表指标，仅在 `?wbperf=1` 时开启，输出到 console + 可选上报，避免常态开销。

---

## 6. 风险与兼容

| 风险 | 说明 | 对策 |
|------|------|------|
| 类名不可改 | `teacher-whiteboard-toolbar/group/btn/control/color/range/value` 被 `exam_take.html`（:937、:1391）覆盖样式复用 | 只增不改；新样式继续走 `twb-` 前缀 |
| 导出名不可改 | `initTeacherWhiteboard` / `initExamDrawingWhiteboard` 是外部入口 | shim `teacher_whiteboard.js` 保持不变 |
| 状态结构兼容 | `STATE_VERSION = 2` 已在线上 | IndexedDB 迁移必须保留 v2 localStorage 读路径，并**不删除**原键（照 v1 的成例保留以便回滚） |
| 运行时字段污染 | `sanitizeElement` 原样透传（`state.js:120`） | bbox/simplified 一律放 `WeakMap`，不挂元素；同时把 `sanitizeElement` 改成字段白名单 |
| 位图缓存与橡皮语义 | committed 层缓存依赖「追加式渲染」 | 一旦将来引入「选中/移动/编辑已有元素」，必须同时引入失效逻辑；本次在 `render_cache.js` 里显式写明这个前提 |
| 导出路径 | `export.js` 走独立离屏全量渲染，不受缓存影响 | 保持现状，但可复用 bbox 缓存加速 `boardBounds` |
| 冲突处理 | 增量 ops 端点会改变冲突语义 | 第二阶段再做；先只做客户端优化，协议不变 |

---

## 7. 一句话优先级

**先做批次 A（一天半，解决"抬笔顿挫"和考试板卡死），再做批次 B（渲染架构，解决"平移擦除卡"），批次 C/D 收尾内存与降级。** 只做 A + B 就能覆盖老师日常反馈的绝大部分场景。

---

*代码位置基于 2026-09-09 工作区快照；行号如与后续修改不符，以函数名为准。*

---

## 附录 A：批次 A 实施记录（2026-09-09）

已落地，涉及 R2 / R5 / R6 / R7 / R9 / R10 / R11 / R12 / R13 / R14 / R16 / R19 与 R4 的常数级优化。
渲染架构（R1）、撤销命令日志（R3）、IndexedDB 与同步链路（R15）留在批次 B / C。

### 改动清单

| 文件 | 改动 |
|------|------|
| `static/js/whiteboard/constants.js` | 新增 v3 分键存储命名空间、`MAX_DPR=1.75`、`MAX_CANVAS_PIXELS=8e6`、`TIMING`（防抖/空闲/兜底）、`INPUT`（采点与抽稀） |
| `static/js/whiteboard/store_local.js` | **重写为分键存储**：索引键存 settings + 板元信息，板体键按 `boardId` 分开；保存只写活动板 + 同步流程改过的板；孤儿板体按签名变化回收；配额不足时裁到 8 块并先清板体再重试；v2 / v1 旧键原样保留供回滚 |
| `static/js/whiteboard/board.js` | 缓存舞台矩形（`getStageRect` + `ResizeObserver`）；`scheduleSave` 防抖 450→1500ms 且改为空闲落盘；`markDirty` 去掉 `filter` 分配；网格自定义属性改写到舞台元素；`open()` 的 `sync.bootstrap` 推迟到空闲帧（2.5s 兜底）；窗口 resize 防抖 180ms；DPR 上限与总像素双重设限；落笔期挂 `is-drawing`；关闭时清理全部定时器与句柄；删板/换 key 时回收本地板体 |
| `static/js/whiteboard/interaction.js` | **重写**：指针事件只入队，绘制统一在一个 rAF 里合批；笔段一次 `beginPath`+`stroke`；采点阈值随笔宽自适应；抬笔按当前缩放做一次 RDP 抽稀再入库；整笔橡皮改为「一帧一批点、对每个元素只遍历一次」并加包围盒 AABB 预筛 |
| `static/js/whiteboard/geometry.js` | 新增 `cachedElementBounds`（WeakMap 缓存，**不挂到元素对象上**以免污染落盘/上传数据）、`pointOutsideBounds`、`boundsIntersectRect`（供批次 B 视口裁剪用）；`boardBounds` 改用缓存 |
| `static/js/whiteboard/state.js` | `countInkElements` 改裸循环去分配；新增 `hasInkElements`，`isBoardEmpty` 提前返回 |
| `static/js/whiteboard/exam_board.js` | **撤销模型换掉**：不再每笔 `toDataURL('image/png')`，改为「底图引用 + 矢量笔画引用」的文档快照；新增设计空间等比缩放，改尺寸不再靠位图缩放；落笔改为只描新增线段（原实现是累积路径全量重描，单笔 O(N²)）；DPR 上限同步下调 |
| `static/css/ui-system.src.css` | 舞台/画布层/工具栏加 `contain: layout paint`；墨迹不透明时不施加 `opacity`（`.has-ink-alpha` 才加）；`.is-drawing` 期间关闭工具栏 `backdrop-filter` 并换成近乎不透明的等效背景 |

### 新增/更新测试

- `static/js/whiteboard/store_local.test.js`（新）：分键写入范围、索引不含 elements、重载还原、远端 stub 不写板体、降级时回收旧板体、删板回收、v2/v1 迁移、配额裁剪后重试成功。
- `static/js/whiteboard/interaction.test.js`（新）：采点阈值与抽稀容差、笔画合批的丢点与折线接续、整笔橡皮的批处理/撤销点合并/橡皮元素豁免/缩放折算。
- `static/js/whiteboard/geometry.test.js`（补充）：包围盒缓存一致性、**AABB 预筛不会误杀真实命中**的随机用例、明显在外的点会被预筛。

`npm test` 全绿（含原有 25 条）。

### 发布前必做

1. **`npm run build:css`** —— 页面加载的是 `static/css/tailwind-app.css`，不重新构建则本次 CSS 改动不会生效。
2. `npm test` 复跑一遍。
3. 手工回归：教师端 HTML 包壳页与 Markdown 材料页各画一块板 → 平移/缩放/整笔擦/像素擦/撤销重做/清屏/切板/新建/重命名/删除/导出；考试页答题附图的画、擦、撤销、重做、清空、保存、改窗口尺寸。
4. 重点验证本地存储迁移：用**已有 v2 数据的账号**打开一次，确认历史白板都在、内容没丢，且 `teacher-whiteboard:v2:*` 旧键仍在（回滚用）。

### 已知遗留

- `sync.js` 的 `prepareElements` 仍会在每次上传时重算 RDP 并二次序列化（R15），留待批次 C。
- 平移/缩放仍是全量重绘（R1），大板下依旧会掉帧 —— 这是批次 B 的主目标。

---

## 附录 B：批次 B 实施记录（2026-09-09）

已落地，解决 R1（全量重绘）与 R6（网格层重栅格），并把 R4 的重绘代价从「整块板」降到「脏矩形」。

### 核心变化：提交层位图缓存

新增 `static/js/whiteboard/render_cache.js`。

前提是元素模型的**追加性**：新元素永远画在最上面，像素橡皮的 `destination-out` 也只影响它之前的内容 —— 所以「已提交的全部元素」可以安全地烘焙成一张位图。**将来一旦引入「选中 / 移动 / 编辑已有元素」，这个前提就不成立，必须同时补失效逻辑**（这一条已写在模块头部注释里）。

缓存按某个视口烘焙（`baked`）。视口变化时不立刻重建：

1. 按两视口的差量变换把缓存 blit 过去（纯位图搬运）；
2. 屏幕上缓存没盖住的「露出条带」用**裁剪后的实时渲染**补画，向外扩 1px 并先 `clearRect`，消除 blit 边缘的抗锯齿缝；
3. 手势停下 140ms 后回正重建一张清晰的缓存。

于是平移/缩放每帧的代价只与**露出面积**相关，而不是与总元素数相关。露出超过 28%、或缩放差量超过 1.6 倍（位图会糊）时直接重建。

其余接入点：

- **新元素增量提交**：`finishDrawing` / `commitTextEditor` 把新元素用**烘焙视口**直接叠加进缓存，一笔的代价是 O(1) 而不是重放整块板。这是最常见场景（老师不平移、只写字）的主要收益。
- **整笔橡皮脏矩形**：删掉元素后只重画它们并集的那块世界矩形（`repaintRegion`），不再让整张缓存失效。局部重放与全量重放等价 —— 区域内的合成结果只取决于碰到它的元素及其先后顺序。
- **失效点**：撤销 / 重做 / 清屏 / 切板 / 远端 patch / 画布尺寸变化。
- **兜底**：拿不到离屏画布时退回原来的全量重绘路径。

### 视口裁剪

`renderElements(ctx, elements, viewport, { worldClip, measureWidth })`：按元素包围盒跳过不相交的元素。**只是跳过绘制，不改变顺序**，所以橡皮的先后语义不受影响（被跳过的橡皮本来也影响不到这块区域）。包围盒算不出来的元素一律照画。

`geometry.js` 新增 `paintBounds` / `cachedPaintBounds`：与命中测试用的包围盒分开，因为橡皮不参与命中测试（返回 `null`）但必须参与绘制，且要为软边模糊留余量。

### 网格层改 transform 驱动

`.twb-grid` 独立元素，向四周多出一个主网格周期（200 × scale），平移只改 `transform: translate3d(...)` —— 纯合成，不重新栅格化那 4 层全屏渐变，也不再让整棵子树样式失效。只有缩放才需要改 `background-size`。
考试画板仍走原来的 `::before`（`display:none` 只作用于 `.twb-root`），外观不变。

### 改动清单

| 文件 | 改动 |
|------|------|
| `static/js/whiteboard/render_cache.js` | **新增**。`RenderCache` 类 + 纯几何函数 `blitGeometry` / `subtractRect` / `screenRectToWorld` / `worldRectToScreen` / `viewportWorldRect` / `shouldRebuild` |
| `static/js/whiteboard/renderer.js` | `renderElements` 支持 `worldClip` 视口裁剪 |
| `static/js/whiteboard/geometry.js` | 新增 `paintBounds` / `cachedPaintBounds`（绘制包围盒，含橡皮） |
| `static/js/whiteboard/constants.js` | 新增 `CACHE`（覆盖率阈值、blit 缩放上限、回正延时） |
| `static/js/whiteboard/board.js` | `drawMainCanvas` 改为 blit + 补画；新增 `paintScreenRect` / `scheduleCacheSettle` / `commitToCache` / `repaintCacheRegion` / `invalidateRenderCache`；网格改 transform 驱动；补齐全部失效点 |
| `static/js/whiteboard/interaction.js` | 新元素提交进缓存；整笔橡皮产出脏矩形 |
| `static/js/whiteboard/text_editor.js` | 文字元素提交进缓存 |
| `static/css/ui-system.src.css` | 新增 `.twb-grid`；`.twb-root` 下停用原 `::before` |

### 新增测试

- `render_cache.test.js`：矩形相减（覆盖/不相交/部分，条带互不重叠、都在屏内）、blit 几何（平移/缩放/覆盖率）、**世界点经 blit 变换后落点不变**、重建阈值、坐标换算互逆。
- `render_cache_canvas.test.js`：用假 canvas 验证 `resize` 分配与失效、`rebuild` 的变换与裁剪、**`commit` 用的是烘焙视口而非当前视口**、`repaintRegion` 的区域换算与元素筛选、`blitTo` 的变换矩阵（含 dpr 折算）。
- `renderer.test.js`：裁剪只影响绘制不影响顺序、橡皮同样参与、**裁剪结果与包围盒相交判定逐例一致**（随机用例）、缩放不影响裁剪结果。
- `interaction.test.js`（补充）：脏区覆盖被删元素、未命中不产生脏区。

`npm test` 全绿，累计 83 条。

### 发布前必做

除附录 A 的四条外，额外重点回归：

1. **平移/缩放的接缝**：快速平移、滚轮连续缩放，条带补画处不应有断线或色差；停手约 140ms 后画面应变清晰。
2. **整笔橡皮**：擦掉一笔后周围的笔迹不能被连带擦掉（脏矩形重放正确性）。
3. **撤销/重做/清屏/切板** 之后画面必须与元素列表一致（这些是缓存失效点）。
4. 缩小到 0.35、放大到 2.6 两个极值各走一遍。

### 已知遗留

- 撤销栈仍是 36 层全量深拷贝（R3），大板内存占用没变 —— 批次 C。
- `sync.js` 每次上传仍重算 RDP 并二次序列化（R15）—— 批次 C。
- 墨迹透明度 <1 时仍走 CSS 合成组（R12 的剩余部分）：改成 `globalAlpha` 会让草稿层的分段描边在重叠处出现接缝，权衡后保留现状。

---

## 附录 C：批次 C 实施记录（2026-09-09）

覆盖 R3（撤销深拷贝）、R15（同步长任务）、R8（软边橡皮 filter）、R20（字段污染）。

### R3 用了比原方案更简单的解法

方案里写的是「撤销栈改命令日志」。实际动手时发现有个前提被忽略了：**元素一旦提交进 `board.elements` 就不再被修改** —— 绘制中的元素是独立对象，提交前才入列；`patchBoard` 换的是整个数组；从存储读回来的是全新对象。（已逐处核对：唯一会就地改元素的是 `activeShape.x2/y2` 和 `stroke.points = simplifyStroke(...)`，都发生在入列之前。）

有了这个前提，快照只需要复制**引用数组**：

```js
export function cloneElements(elements) {
    return Array.isArray(elements) ? elements.slice() : [];   // 原来是 JSON.parse(JSON.stringify(...))
}
```

- 时间：从「每笔 3~5MB 文本的 JSON 来回、30~150ms」变成 n 个指针的 memcpy，几十微秒。
- 内存：5000 笔的板一层快照约 40KB，80 层合计 ~3MB；原来 36 层是 ~150MB。

既然内存不再是约束，`UNDO_LIMIT` 从 36 提到 **80**。

命令日志能把 push 降到 O(1)，但浅拷贝已经是微秒级，多出来的复杂度（三种操作类型、插回原索引、与 clear/切板的交互）换不回等价的收益，所以没做。**代价是这个不可变契约变成了硬约束**：将来要支持「选中并编辑已有元素」，必须改成写时复制（替换元素而不是就地改），否则撤销会看到改后的内容。这一条写进了 `cloneElements` 的注释，并有单测固化。

### R15 同步链路

- **抽稀结果按元素缓存**（WeakMap，元素不可变所以结果稳定）。原来每 30 秒把整块板重算一遍 RDP。抽不掉点时原样返回，连新对象都不造。
- **只序列化一次**：量体积和实际发送共用同一份字符串（原来各 `JSON.stringify` 一遍）。`store_remote.request` 现在接受已序列化的字符串体。
- **体积按 UTF-8 字节算**（原来用 `String.length`，中文会低估到三分之一，导致客户端放过、服务端返回 413）。
- **绘制期让路**：定时同步移进空闲帧，且 `isBusy()`（有活跃指针）时直接跳过这一轮。显式保存（Ctrl+S / 保存菜单）不受影响，仍然立即执行。

### R8 软边橡皮

去掉 `ctx.filter = blur(...)`，改为预生成径向渐变贴图沿路径盖章，贴图按 (size, hardness) 量化后缓存（上限 24 张，2 倍过采样）。

原来的问题不只是 filter 走慢路径，而是**这些橡皮元素存在元素列表里，每次重建都要把所有软边橡皮的模糊重放一遍** —— 一块板上用过几次软橡皮，此后每次重建都永久变慢。

- 硬边（`hardness = 1`，也是默认值）路径完全不变，仍是单次描边 —— 所以绝大多数用户看不到任何变化。
- 拿不到 `document` 时退回三层递减 alpha 描边，不再碰 `ctx.filter`。
- 加了盖章数量兜底（`MAX_ERASER_STAMPS = 4000`），避免「极细橡皮 + 极长路径」退化成几万次 `drawImage`。

### R20 元素字段白名单

`ELEMENT_FIELDS` 按类型列出允许持久化的字段，`sanitizeElement` 据此剥离未知字段：未知类型丢弃，已知类型清洗，**没有多余字段时原样返回**（载入路径上不额外分配）。

这样即使将来有人图省事把运行时数据挂到元素上，也不会被写进 localStorage 或上传。本次所有运行时缓存（包围盒、绘制包围盒、抽稀结果）一律走 WeakMap，本来就不会污染。

**后端没有跟着加白名单**：`_normalize_elements` 目前只校验类型，服务端剥离未知字段有拒掉旧客户端数据的风险，收益也不大，暂不动。

### 改动清单

| 文件 | 改动 |
|------|------|
| `static/js/whiteboard/state.js` | `cloneElements` 改浅拷贝并写明不可变契约；`sanitizeElement` 类型 + 字段双重白名单 |
| `static/js/whiteboard/constants.js` | `UNDO_LIMIT` 36→80；新增 `ELEMENT_FIELDS`（`ELEMENT_TYPES` 由它派生）；`REMOTE.IDLE_FLUSH_TIMEOUT_MS` |
| `static/js/whiteboard/sync.js` | 抽稀缓存 + 导出 `prepareElements` / `byteLength`；单次序列化；`requestIdleFlush`；`flushDirty` 支持 `respectBusy` |
| `static/js/whiteboard/store_remote.js` | `request` 接受字符串体；`upsert` 支持 `serialized` |
| `static/js/whiteboard/board.js` | 给 SyncController 提供 `isBusy()` |
| `static/js/whiteboard/renderer.js` | 软边橡皮改贴图盖章；新增 `stampPoints` / `eraserSpriteKey` / `MAX_ERASER_STAMPS`；移除 `supportsCanvasFilter` 与 `eraserPasses` |

### 新增测试

- `state.test.js`（补充）：浅拷贝快照的引用语义、撤销/重做一个来回的等价性、字段白名单四类元素各自的字段集、载入时清洗。
- `sync.test.js`（新）：抽稀正确性与首尾保留、**重复调用命中缓存**、无变化时不造新对象、非笔画类型原样通过、UTF-8 字节计量。
- `eraser.test.js`（新）：盖章取点（首尾、间隔不超过间距、间距大于路径、重复点不死循环）、贴图量化键、硬边不走贴图、软边盖章次数与取点一致、**退化路径不再触碰 `ctx.filter`**、盖章数量兜底。

`npm test` 全绿，累计 113 条。

### 回归重点

1. **撤销/重做**：连续画 10 笔后连按 10 次撤销再 10 次重做，内容必须完全一致；擦除、清屏、切板混在中间再走一遍。
2. **软边橡皮**：把橡皮硬度拉到 0.3 左右擦几笔，边缘应当是平滑过渡而不是硬边或分层；硬度 1（默认）的观感必须与改动前一模一样。
3. **云端保存**：画一块中等大小的板 → 手动保存 → 刷新 → 从历史白板切回来，内容一致；把板画到接近 2MB 观察是否给出「内容过大」提示（现在按 UTF-8 字节判定，中文板会比以前更早提示，这是修正而非退化）。
4. 边画边等 30 秒自动同步，不应出现掉帧。

### 仍未做

- **IndexedDB**（R2 的最后一段）：批次 A 的分键存储已经把单次落盘从「全部板」降到「当前板」，再叠加 1500ms 防抖 + 空闲帧执行，剩下的同步序列化只在空闲时发生。改 IndexedDB 收益已经不大，而迁移风险（数据丢失）实打实，因此暂缓；等出现单板经常超过 1MB 的实际反馈再做。
- 增量 ops 上传端点（需要后端配合）。
- 墨迹透明度 <1 时的 CSS 合成组（见附录 B）。

---

## 附录 D：批次 D 实施记录（2026-09-09）

覆盖 R24（低性能模式）、R22（iframe 协同）、R21（容量治理）、R23（性能埋点）。

### 低性能模式

新增 `static/js/whiteboard/perf_profile.js`：两档参数集中在一处，默认按设备探测自动选，用户可在**背景浮窗**里手动锁定（自动 / 高画质 / 流畅）。

| 项 | 高画质 | 流畅 |
|----|--------|------|
| DPR 上限 | 1.75 | 1.25 |
| 工具栏毛玻璃 | 开 | 关 |
| 网格 | 细 + 主两级 | 只留主网格 |
| 软边橡皮 | 开 | 强制硬边 |
| 入库抽稀容差 | 0.5 | 1.2 |
| 撤销层数 | 80 | 40 |
| 缓存回正延时 | 140ms | 300ms |
| 自动同步周期 | 30s | 120s |

探测规则：逻辑核心 ≤ 4 或内存 ≤ 4GB 判为流畅档；两个信号都拿不到就按高画质 —— **宁可保画质也不凭空降级**。切换即时生效（画布按新 DPR 重建、缓存失效、同步周期换挡）。

**没有接 `user_ui_preferences`。** 方案里原本写的是复用它做跨设备同步，实际看了接口才发现那是个只认 `palette_key` 的专用端点（`extra="forbid"` + 服务端白名单），要加字段得动表结构、服务、Pydantic 模型和整套 version/context-token 冲突处理。更重要的是**跨设备同步这件事本身是错的**：慢的是这台讲台机，不是这个人，把「流畅模式」同步到老师自己的笔记本上反而帮倒忙。所以档位按设备存 localStorage。

### 与学习文档 iframe 协同

白板是覆盖在 iframe 之上的半透明层，白板每重绘一帧，浏览器都要把 iframe 里整棵文档树重新合成一次。

白板现在会广播 `teacher-whiteboard:state`（开关状态 + 背景透明度），`material_render_shell.js` 据此：

1. 往 iframe 文档里注入一小段样式（同源），白板打开期间给 `<html>` 挂 `ld-quiet`，暂停文档的动效与过渡，并关掉 `.stat` 的毛玻璃；
2. 背景透明度 ≥ 0.98（文档已经完全看不见）时，把 iframe `visibility: hidden` —— 整棵文档树直接退出合成。

样式是**注入**而不是改 `lessondoc/2.0/*.css`：这样对已经发布出去的、CSS 版本更老的课程包同样生效。

### 容量治理

- `CAPACITY = { HINT: 1500, WARN: 3000 }`。越过阈值时提示新建白板，每块板每场最多两次且只升不降。
- 历史白板列表里，笔数超过软上限的板把「N 笔」标成警示色并带 title 说明。
- 后端 `MAX_ELEMENTS = 20000` 保持不变作为硬闸 —— 但那个量级早就不流畅了，这里是在体感下滑之前先给出口。

### 性能埋点

新增 `static/js/whiteboard/perf_probe.js`，**默认完全关闭**：`createPerfProbe()` 返回 `null`，所有调用点都是 `this.probe?.xxx()`，关掉时连一次属性查找都没有。

开启：地址栏 `?wbperf=1`，或 `localStorage.setItem('teacher-whiteboard-perf-probe','1')` 后刷新。
采集：帧间隔 P50/P95/最差帧、由 P95 折算的帧率、longtask 数量/最长/合计、JS 堆峰值，以及渲染计数器（render / rebuild / blit / patch / commit / repaintRegion）。
读数：控制台 `teacherWhiteboard.perfReport()`，关闭白板时也会自动打印一次。

计数器是判断优化是否真的生效的直接依据 —— 正常写字时应该看到 `commit` 增长而 `rebuild` 基本不动；平移时 `blit` 和 `patch` 增长、`rebuild` 偶尔一次。

### 改动清单

| 文件 | 改动 |
|------|------|
| `static/js/whiteboard/perf_profile.js` | **新增**。两档参数、设备探测、模式解析 |
| `static/js/whiteboard/perf_probe.js` | **新增**。可关闭的埋点，含 `percentile` / `summarize` |
| `static/js/whiteboard/store_local.js` | 档位按设备持久化（`loadPerfMode` / `savePerfMode`） |
| `static/js/whiteboard/constants.js` | `PERF_STORAGE_NAMESPACE`、`CAPACITY` |
| `static/js/whiteboard/board.js` | 档位接入（DPR / 撤销层数 / 回正延时 / 同步周期）、`setPerfMode`、`notifyHostState`、容量提示、埋点计数、`perfReport()` |
| `static/js/whiteboard/interaction.js` | 抽稀容差按档位；流畅档强制硬边橡皮 |
| `static/js/whiteboard/sync.js` | `start(intervalMs)` / `restart(intervalMs)` |
| `static/js/whiteboard/panels/style_popovers.js` | 背景浮窗加性能档位分段控件 |
| `static/js/whiteboard/panels/history_panel.js` | 笔数超阈值标警示色（顺带把这段拼 HTML 改成结构化构造） |
| `static/js/material_render_shell.js` | `initWhiteboardDocQuiet()`：注入降级样式、按背景透明度隐藏 iframe |
| `static/css/ui-system.src.css` | `[data-perf="lite"]` 的毛玻璃/网格/动画降级；`.twb-history-strokes.is-heavy`；`.twb-section-hint` |

### 新增测试

`perf_profile.test.js`：设备探测三种情形、非法模式退回、手动锁定忽略探测、解析结果完整性、**流畅档在每一项上都不比高画质更费**（防止以后改参数改反）；以及埋点的分位数与汇总（含空样本）。

`npm test` 全绿，累计 **123 条**。

### 回归重点

1. 背景浮窗切「流畅」→ 工具栏毛玻璃消失、细网格消失、画面略糊但更跟手；切回「高画质」应完全恢复；刷新后档位保持。
2. 打开白板时下方文档的动效应停住；把背景透明度拉到 100% 时文档应完全消失（不是变白，是不再合成）；关闭白板后恢复。
3. 画到 1500 / 3000 笔时各弹一次提示，且不重复骚扰。
4. `?wbperf=1` 打开后画一分钟，`teacherWhiteboard.perfReport()` 能出表。
