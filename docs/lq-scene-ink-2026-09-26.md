# 玻璃文字场景色调契约（Scene Ink）— 2026-09-26

> 真源。改任何"玻璃上的文字颜色"先读这份；改了机制必须同步更新这里、`materials.css` 头注与 `static/js/lq/scene_tone.js` 头注。

## 1. 问题

登录"人生一言"玻璃卡在亮图上出现白字压白玻璃。根因不是某张图，而是**颜色来源错误**：卡片在亮图分支用了 `--gray-900 / --gray-600 / --ls-info-strong` 这类**随账号外观（light/dark）翻转的主题令牌**。系统处于深色模式时它们全部变成近白，而玻璃填充仍按照片亮度取白，于是同色。任何把"背景上的文字"绑到主题令牌的地方都会复现同类问题。

## 2. 契约（模板级，适用于全站）

**玻璃上的每一种前景色只能来自与填充配对的材质变量，禁止直接用主题灰阶：**

| 变量 | 用途 | 来源 |
|---|---|---|
| `--lq-material-ink` | 正文 | `--ls-glass-scene-{light,dark}-ink` |
| `--lq-material-muted` | 次要文字 | `--ls-glass-scene-{light,dark}-muted` |
| `--lq-material-accent` | 眉题/强调 | `--ls-glass-scene-{light,dark}-accent`（本次新增） |
| `--lq-control-fill/ink` | 内嵌控件 | `--ls-glass-scene-{light,dark}-control/ink` |

`scene-*` 令牌**不随外观模式重定义**，它们是固定配对：亮场景 = 白填充 + 深墨，暗场景 = 深填充 + 白墨。每种材质（`.lq-glass`、`[data-lq-material]`）先把 ink/muted 重置为自己主题填充的配对值，再由场景色调**同时**覆盖填充与全部前景。

### 场景色调从哪里来（由便宜到具体）

1. **页面级** `<html data-lq-scene-tone="light|dark">`
   - 图库每张图在入库时量出中央横带亮度写入 `manifest.json`（`luma` 0–255、`tone`），工具 `tools/tips/compress_images.py`（`--tone-only` 补齐存量）。
   - SSR：`user_ui_preferences_service.resolve_backdrop()` 返回 `tone`（图片取 manifest；纯色模式 `tone_of_hex(color)`），`partials/lq_theme_attrs.html` 写到 `<html>`，无闪烁。
   - 客户端：`page_backdrop.js paint()` 每次换图/换色后 `publishSceneTone()` 重新发布，并派发 `lq:scene-tone` 事件。**用户手动指定图片或纯色走的是同一条路径**，所以偏好设置仍然生效。
   - 消费：`materials.css` 里 `:root[data-lq-scene-tone] .lq-glass--clear / [data-lq-material="clear"]`（未显式声明 `data-lq-tone` 者）自动继承页面色调。降级根（`data-lq-glass="off"`、Tier B/C、高对比）不继承，保持不透明主题配对。
2. **元素级显式** `data-lq-tone="light|dark"`：调用方已知背后是什么（登录卡、一言卡）。
3. **元素级自动** `data-lq-scene="auto"`：`scene_tone.js watchAutoTone({ getImage })` 把元素视口矩形映射到 `background-size: cover` 的背景图上采样，写 `data-lq-tone`；无图时退回页面色调；`lq:scene-tone`/resize 后重算。

阈值统一为 **Rec.709 亮度 148**，三处（Python 工具、Python 服务、JS）由单测互相钉死。

## 3. 本次接入

- 登录一言卡 `cultivation_identity.js buildTipReveal` 同时写 `data-tip-tone`（保留背景滤镜用）和 `data-lq-tone`；`life-tip.css` 全部前景改读材质变量；固定深色胶囊（身份条、反馈按钮）用固定 `scene-dark-*`；一言浮窗改为主题表面（填充与墨同翻）。
- `login_scene.js` 优先用 manifest 的 tone，图未标注才 canvas 采样；同时发布页面色调。
- `login.css` 卡内文案改读 `--lq-material-muted`；自带主题表面的芯片保持主题墨。
- `[data-lq-material]` 的 `color` 改为读 `--lq-material-ink`（原先写死 `--ls-ink`，导致材质无法被场景覆盖）。

## 4. 新写玻璃组件的守则

1. 文字色只写 `hsl(var(--lq-material-ink|muted|accent))`；需要兜底时 `var(--lq-material-ink, var(--ls-glass-ink))`。
2. 自带**固定**填充（如永远深色的胶囊）就用固定的 `--ls-glass-scene-dark-*`；自带**主题**填充（`--ls-surface-*`）就用主题墨 `--ls-ink*`。判断标准只有一个：墨和它下面那层填充是否一起变。
3. 直接铺在页面背景上的透明材质加 `.lq-glass--clear` 或 `data-lq-material="clear"` 即可自动跟随页面色调；位置固定且需要精确判定时加 `data-lq-scene="auto"` 并调用 `watchAutoTone`。
4. 新增图库图片必须经 `compress_images.py` 入库（自动写 tone）；手工塞图后跑 `--tone-only`。

## 5. 验证

- `python -m unittest tests.test_user_ui_preferences_backdrop`（SceneToneTests + SSR 属性）
- `npx vitest run tests/lq/scene-tone.test.mjs`
- 浏览器：登录页亮/暗图 × 系统深色/浅色外观，一言卡与登录卡文字对比正常；偏好面板切换"页面背景"到纯色深色/浅色后 `<html data-lq-scene-tone>` 随之变化。
