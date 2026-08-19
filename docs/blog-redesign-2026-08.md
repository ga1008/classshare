# 博客中心前端重设计施工方案（2026-08）

> 目标：把现在的赛博朋克「数据终端」皮肤，改造为**简约、易用、轻拟物化**的「纸感阅读台」。
> 本文档是施工真源；执行时逐期勾选状态。遵守 `docs/frontend-redesign-2026-08.md` 的设计系统铁律（--ls-* 令牌、禁 hex 硬编码）。

## 一、现状诊断

| 文件 | 行数 | 角色 |
|---|---|---|
| `templates/blog.html` | 454 | 页面结构，隐喻文案遍布（CYBERDECK/JACK-IN/CH 00/NO SIGNAL/HOLO PLAYER） |
| `static/css/blog-cyberdeck.css` | 2223 | 全页皮肤（碳纤维机箱+霓虹+扫描线+雪花），后加载覆盖基础层 |
| `static/css/ui-system.src.css` blog 段 | ~3100 行 | 基础结构样式（布局/卡片/评论/编辑器），皮肤靠覆盖生效 |
| `static/js/blog.js` | 3518 | 全部交互：feed/detail/composer/评论/关注/收藏/就业筛选 + **大量隐喻性交互**（旋钮拖动、模拟调谐、频道雪花、RGB 撕裂换台） |

核心问题：
1. **认知负担**：读文章的页面被包装成「调频收音机」——旋钮、CH 00、NO SIGNAL 对学生是噪音不是乐趣，关键操作（切板块、搜索）被隐喻埋没。
2. **阅读疲劳**：暗底霓虹 + 扫描线动效不适合中文长文阅读场景（学生主用途是读 AI 新闻）。
3. **维护成本**：换台故障动效、拨盘物理交互在 blog.js 里约占 600-800 行，与内容功能无关。
4. 与全站正在收敛的 --ls-* + shadcn 设计语言完全脱节（皮肤内自带 --neon-* 色板、hex 硬编码）。

### 必须保留的 JS↔DOM 契约（换皮红线）

- 所有 `data-blog-*` 属性（视图切换、feed 容器、composer、评论、就业筛选、举报 dialog、用户浮窗）。
- JS 切换的状态 class：`is-active` / `is-compact` / `is-detail-compact` / `is-tuner-compact` / `is-detail-player` / `is-detail-mode` / `is-following` / `is-active--like` / `is-active--bookmark` / `is-preview` / `is-dragging` / `is-grabbed` / `blog-composer-open`，以及（隐喻期仍在用的）`is-channel-tuning` / `is-analog-tuning` / `is-channel-no-signal` / `is-between-channels` / `is-switching-channel` / `is-channel-reveal`。
- JS 渲染模板中的 class：`blog-post-card*` / `blog-badge*` / `blog-empty*` / `blog-tag` / `blog-comment-*` / `blog-interact-btn*` / `blog-action-btn*` / `blog-user-*` / `blog-toolbar-chip*` 等。
- `.blog-shell [hidden] { display:none !important }` 必须保留（历史缺陷修复，JS 全靠 hidden 属性切视图）。
- 深链 `?post=`、`?section=` 行为不动。

## 二、设计方向：「纸感阅读台」（Paper Desk）

### 借鉴对象

| 参考 | 借什么 |
|---|---|
| **Ghost Casper / Edition**（MIT） | 列表页「1 篇大图精选 + 卡片网格」节奏；卡片信息层级（封面→分类→标题→摘要→作者·时长） |
| **Astro AstroPaper**（MIT） | 极简克制的排版优先风格；标签/搜索的轻量呈现 |
| **Hugo Stack** | 中文社区验证过的卡片流 + 左侧分类导航；圆角卡片 + 柔和阴影气质 |
| **Medium / 微信读书** | 长文阅读版式：窄栏、大行高、阅读进度条、克制的互动栏 |
| **Stripe / Vercel 阴影配方** | 轻拟物的技术实现：多层低透明度阴影叠加，而非 neumorphism 双向凹凸 |

### 设计语言规范（写进新皮肤 CSS 顶部注释）

- **底色**：暖白纸感 `hsl(var(--ls-background))` 微调暖色调，卡片纯白微微抬升；不再默认暗色（暗色作为后续增强）。
- **轻拟物 = 分层柔影 + 触感反馈**，禁用重纹理/大渐变/霓虹：
  ```css
  --blog-shadow-sm: 0 1px 2px hsl(var(--ls-foreground) / .06), 0 1px 1px hsl(var(--ls-foreground) / .04);
  --blog-shadow-md: 0 1px 2px hsl(var(--ls-foreground) / .05), 0 4px 12px hsl(var(--ls-foreground) / .08);
  --blog-shadow-lift: 0 2px 4px hsl(var(--ls-foreground) / .06), 0 12px 24px hsl(var(--ls-foreground) / .10);
  ```
  卡片 hover 上浮 2px + shadow-md→lift + 120ms ease-out；按下态 translateY(1px) + shadow 收紧（pressed 触感）。
- **中文长文排版**（详情页 prose）：正文 17px（--text-rg 档就近），`line-height: 1.85`，栏宽 `max-width: 42rem`（约 38 汉字/行），段距 1.25em，`letter-spacing: .01em`；标题层级只用 2 档字重差 + 尺寸差；图片圆角 --radius-lg + caption 灰字居中。
- **色彩**：全部走 `hsl(var(--ls-*) / α)`；板块 accent 色仍由后端 `--section-accent` 注入，但只用于细节（tab 下划线、卡片分类角标），不再整块染色。
- **圆角**：统一走 --radius 令牌五档。
- **动效**：只保留功能性过渡（视图切换 fade/slide 150-200ms、卡片入场 stagger ≤300ms）；删除雪花/撕裂/扫描线。

## 三、施工分期

### P0 基线与契约固化 ✅ 2026-08-19
- [x] 用 tmpspec + Playwright + sqlite 播种法截当前四态基线：feed / detail / composer / 空态（移动+桌面）。
- [x] 将上文「JS↔DOM 契约」逐项 grep 复核一遍，补漏。

### P1-P3 合并执行 ✅ 2026-08-19（一次会话内完成，未分批上线）
- [x] 新建 `static/css/blog-paper.css`（预计 1200-1500 行），`blog.html` 第 5 行换引用；**不动模板结构、不动 blog.js**。
- [x] 隐喻元素在此期用 CSS 中性化：旋钮/仪表照常存在但改为浅色简洁样式；`is-channel-*` 等动效 class 一律映射为柔和 fade（保证 JS 加 class 不报错也不闪雪花）。
- [x] 保留 `[hidden]` 修复规则、`--section-accent` / `--dial-angle` 等 CSS 变量消费。
- [x] 截图对比基线，肉眼过四态；部署（记得 ssh 清理远端旧 blog-cyberdeck.css——部署脚本不删远端文件）。

- [x] `blog.html`：删除全部隐喻文案（CYBERDECK 01 / JACK-IN / CH 00 / NO SIGNAL / HOLO PLAYER / POWER / ON AIR），换为直白中文（「博客中心」「暂无内容」等）。
- [x] 头部简化为：标题 + 一句话副标 + 统计条 + 导航键（节目→最新，去电视机术语）。
- [x] 板块切换从「旋钮 + 频道带」简化为**普通横向 tabs**（保留 `data-blog-section` / role=tablist 契约）；旋钮、±步进按钮、频道 readout 从模板移除。
- [x] 空态从「NO SIGNAL 雪花」改为插画级轻空态（图标 + 一句引导 + 写帖子按钮）。

- [x] 删除拨盘物理交互（drag/惯性/滚轮调谐）、模拟调谐、雪花/撕裂换台逻辑（约 600-800 行），保留 tab 点击 + 方向键切换（可及性）。
- [x] 视图切换动效统一为 fade；`is-channel-*` class 写入点随之清理。
- [x] 跑现有 blog 相关单测 + tmpspec 截图回归；确认 `?post=` / `?section=` 深链、compact 滚动态、composer 全流程无损。

### P4 阅读体验增强 ✅ 部分完成 2026-08-19（进度条/时长/prose 排版/骨架屏/精选区已落地；TOC 未做）
- [x] 详情页：prose 排版规范落地（见设计语言）、顶部细阅读进度条、预计阅读时长（composer 已算字数，详情页补显示）、封面图 hero 化 + caption。
- [x] 列表页：第一屏「精选大卡」（复用现有 data-blog-spotlight）+ 双列卡片网格（移动单列）；骨架屏替代空白加载。
- [x] 侧栏 rail 卡片同语言轻拟物化；移动端 rail 折叠到底部。

### P5 收尾 ◐ 2026-08-19（本地完成；**部署时须 ssh 清理远端与容器内 blog-cyberdeck.css**；暗色模式未单独设计）
- [x] 响应式两档已截图验证（1440/390），768 档由 CSS 断点覆盖（375/768/1280）+ 无障碍点检（tab 焦点环、aria 不回退）。
- [ ] 暗色模式：本期可只做「跟随全站令牌自动成立」的验证，不单独设计。
- [x] 本地已 git rm blog-cyberdeck.css；远端+容器内待部署时手动清理；`ui-system.src.css` blog 段中被新皮肤完全取代的覆盖项酌情清理（动 src.css 必须 `build:css`）。
- [x] 更新记忆 blog-cyberdeck-skin → 新皮肤要点；提交 conventional commit 分期落 dev。

## 四、风险与决策记录

- **最大风险在 P3**（动 blog.js）。P1/P2 独立可上线，若 P3 出问题可长期停留在 P2 形态。
- 旋钮交互彻底移除是本方案的关键取舍：它是上一版的灵魂，但与「简约易用」直接冲突。若想保留趣味，P4 可在卡片 hover 微倾斜、点赞粒子等**不挡路的微交互**上找补。
- 部署三坑：脚本不删远端文件（旧 css 需手动清）、动 src.css 要 build、上线前真 PG 环境验证。
