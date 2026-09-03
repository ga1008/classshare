# 课程学习文档模板体系（LessonDoc 2.0）设计方案与施工文档

> 制定日期：2026-09-01。状态：**基础体系、迁移及 R1—R5 已有实现，R6 首轮已有验收记录；R7 编辑器 E0 初步实现、待补强，E1—E6 待施工。部署状态需单独核实**。
> 施工进度见 §8 各阶段的 ✅ 标记；实现中的口径变更已回写本文。
> 目标：把「课程学习文档」从手工编写/手工绑定，升级为**配置驱动、AI 可编写、自动绑定课次、可换肤换版式、可逆可离线**的标准化课件包体系。
> 本文档是唯一施工真源；实现过程中的口径变化必须回写本文档。

---

## 0. 一句话总纲

**每门课一个「学习文档包」：包结构沿用现行 HTML 包规范（main.html + lesson_N/lesson_N.html + assets/），但每个 HTML 文件退化为「壳 + 内嵌 JSON 配置」，由平台固化的渲染引擎（deck-engine）在浏览器端把 JSON 渲染为 PPT 式幻灯片。JSON 即真源 —— AI 只写 JSON，平台只解析 JSON，主题与版式运行时可切换，配置↔文档天然可逆，离线双击可用。**

这样做的根本理由（对照现状）：

| 现状痛点 | LessonDoc 2.0 的解法 |
|---|---|
| AI 生成整页 HTML，输出量大、易坏、校验只能整体拒收 | AI 只产出结构化 JSON（体量约为 HTML 的 1/3），逐块校验、坏块丢弃降级而非整体失败 |
| 手工上传+手工绑定，新手不友好 | 课程页一键生成 → 包落材料库 → 复用 `apply_package_session_bindings` 确定性绑定 |
| 配色/版式写死在每个文件里（cnet-course 的 SVG 硬编码色） | 主题令牌集中在引擎 themes.css，运行时切换，图示 DSL 用语义色名 |
| 无法从成品反推内容（重生成时 AI 缺上下文） | JSON 内嵌于 HTML，`course.json` 清单汇总全课程知识，重生成时全量注入 |

---

## 1. 现状盘点（事实依据，改动前必读）

### 1.1 已有资产（全部复用，不推倒）

- **HTML 包体系** `classroom_app/services/html_package_service.py`：`parse_html_package`（main.html + lesson_N 判定）、`find_html_package_root`（上溯 ≤4 层）、**`apply_package_session_bindings`（order_index ↔ lesson_N 确定性绑定，含首页绑定与课堂授权同步）**、`build_package_outline_text` / `extract_html_text`（AI 知识注入）。
- **渲染系统** `classroom_app/services/material_render_service.py`：渲染器注册表 `MATERIAL_RENDERERS`、包根锚定（`/materials/render/{包根}/{相对路径}`）、`resolve_render_file` 防穿越。
- **全屏壳页** `GET /materials/render-view/{id}`（`materials_parts/exports.py`）+ `material_render_shell.html/js`：iframe + 工具条 + 课次徽章 + 教师白板 + AI 部件 + 学生学习进度心跳。
- **课次绑定双层**：单列镜像（`class_offering_sessions.learning_material_id` / `class_offerings.home_learning_material_id`）+ 列表真源 `class_offering_learning_materials`（`session_id=0`=首页）。**任何新写入路径必须同时维护两层**（`_backfill_primary` 是懒迁移关键）。
- **AI 生成管线** `session_material_generation_service.py`：任务表 `session_material_generation_tasks`、原子领取、HTML 包模式检测 + 严格校验（`_normalize_generated_html_package_nodes`）、落库+绑定+授权一条龙。
- **zip 上传解压**（library.py，200MB/500MB/3000 条护栏）、**git 同步入口候选识别**（materials_git_service，`LEARNING_HTML_INDEX_NAMES` + `lesson_N.html`）。
- **课次数量真源**：`courses.total_hours ÷ per_session_sections`（教师填写，常为 2）→ `course_lessons`（课程级模板，`UNIQUE(course_id, order_index)`）→ 开课时映射为 `class_offering_sessions`。AI 拆课次已有：`POST /courses/ai-generate-lessons`（classes_courses_courses.py，含教材上下文 `build_textbook_prompt_context`）。
- **教材** `textbooks` 表：`introduction` + `catalog_text`（目录文本）是拆课次与生成内容的核心依据；挂在 `class_offerings.textbook_id`。

### 1.2 cnet-course 范例的可抽象结论

（范例：`…\计算机网络原理_E040016B1_计科2606\cnet-course`，规范：`…\0- 2026-2027-1\00_学习文档HTML包设计规范.md`）

- 幻灯片框架 = **1280×720 虚拟画布 + transform:scale 等比缩放 + fitSlide 二级兜底**，永不出滚动条；字号用绝对 px，缩放交给 transform。
- 五种版式互斥：`slide--title / slide--section / 内容页(默认) / slide--two-col / slide--center / slide--end`；页眉页脚由 JS 注入（作者只写 `data-section`）。
- fragment 分步显现与翻页共享同一按键优先级链；SVG 内部的 `<g class="fragment">` 也可分幕。
- 内容原子高度同构，已可完全表驱动：卡片网格、时间线、表格（逐行 fragment）、代码+输出、callout、quiz（data-answer 判分）、tasklist、figure/SVG、stepper（通用播放器 + 步骤数组）。
- 思维导图 = 嵌套 `<ul>` 数据 + CSS 伪元素连线 + JS 折叠，零 SVG，天然配置化。
- 首页 = hero 统计 + 总览思维导图 + 阶段分组课次卡片（ready/pending 二态）+ 信息标签页。
- **最大耦合点**：内联 SVG 的 fill/stroke 全是硬编码十六进制 → 模板必须改为语义色。
- 零外部依赖（机房无外网），完全离线可用 —— 这条是红线，必须保持。

### 1.3 铁律（改动守则，来自教学域总纲）

1. 新 runtime 表走 **polls 模式**（engine-aware `ensure_*_schema` + `_SCHEMA_READY`），挂 `schema.py::init_database` 双引擎分支。
2. 新表若挂 `class_offering_id` 必须登记 `offering_merge_service.MERGE_RULES`（本方案刻意让新表**不挂** offering，规避此负担）。
3. 课堂↔学生解析只走 `offering_membership_service`（本方案不涉及）。
4. 新路由后重生成 `tests/fixtures/p02_route_snapshot.json`。
5. 遍历 `course_materials` 必须过滤 `.git` 内部路径。
6. **不改任何现有表的现有列**；只做加列/加表；旧 HTML 包（cnet-course 等手写包）必须继续原样工作。

---

## 2. 总体架构

```
                    ┌─ 平台固化资产（版本化） static/lessondoc/2.0/
                    │    course.css  slides.css  course.js  slides.js   ← 从 cnet-course 提炼定稿
                    │    deck-engine.js  home-engine.js  themes.css     ← 新增：JSON→DOM 渲染引擎 + 主题
                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│  学习文档包（材料库中的一个文件夹，完全兼容现行 HTML 包规范）              │
│  {课程名}-学习文档包/                                                  │
│  ├── main.html          ← 壳 + 内嵌 course.json 渲染视图（首页）        │
│  ├── course.json        ← 课程清单：课程信息/课次划分/术语/主题/生成状态  │
│  ├── assets/            ← 引擎文件的包内副本（离线可用、版本锁定）        │
│  ├── lesson_1/
│  │   ├── lesson_1.html  ← 壳 + 内嵌 deck JSON（本课次唯一真源）         │
│  │   └── media/…        ← 本课次专属图片/视频（可选）                    │
│  ├── lesson_2/…
│  └── README.md          ← 指向 main.html
└──────────────────────────────────────────────────────────────────────┘
         ▲ 生成/重写（AI 只产出 JSON）                ▼ 渲染
  course_doc_pack_service（新）              浏览器端 deck-engine.js
  + session_material_generation_service      （壳页 iframe 内运行，
    复用任务表/队列/落库/绑定                   离线 file:// 同样工作）
```

### 2.1 「壳 + 内嵌 JSON」文件格式（核心决策）

每个 `lesson_N.html` 的实体结构固定为：

```html
<!DOCTYPE html>
<html lang="zh-CN" data-lessondoc="2.0" data-doc-kind="lesson" data-lesson="3">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>第3课 · 差错检测与可靠传输</title>
  <link rel="stylesheet" href="../assets/course.css">
  <link rel="stylesheet" href="../assets/slides.css">
  <link rel="stylesheet" href="../assets/themes.css">
</head>
<body class="slides-page">
  <noscript>本文档需要启用 JavaScript 才能查看。</noscript>
  <script type="application/json" id="lessondoc-data">
  { …deck JSON（本文件唯一真源，见 §3）… }
  </script>
  <script src="../assets/course.js"></script>
  <script src="../assets/slides.js"></script>
  <script src="../assets/deck-engine.js"></script>
</body>
</html>
```

`main.html` 同构（`data-doc-kind="home"`，内嵌 `course.json` 的渲染视图，由 `home-engine.js` 渲染；`course.json` 同时以独立文件存在于包根，作为后端与 AI 的读取真源——两处由生成器同步写入，以包根文件为准）。

为什么内嵌而不是 `fetch('lesson_3.json')`：Chrome 在 `file://` 下禁止 fetch 本地文件，内嵌是**离线双击可用**的唯一可靠方案；同时它让「文档→配置」的抽取变成一次 `getElementById('lessondoc-data').textContent`，可逆性（需求 10）零成本达成。

### 2.2 与现有系统的兼容关系

- 包目录结构（main.html / lesson_N / assets）**原样满足** `parse_html_package` 的判定，因此：确定性课次绑定、包根锚定渲染、壳页、学生授权、git 同步识别、AI 知识注入**全部零改动直接生效**。
- `data-lessondoc="2.0"` 是新旧判别标志：有 → 走 JSON 通道（抽 JSON 给 AI、支持重写/换肤）；无 → 旧手写包，行为与今天完全一致。
- `extract_html_text` 对新格式做一处增强：检测到 `#lessondoc-data` 时改抽 JSON 内的文本字段（更干净的 AI 语料），否则走原路径。

---

## 3. Deck JSON 配置规范（AI 编写契约）

> 本节 + `docs/lessondoc-authoring-guide.md`（施工 P0 产出，含完整案例库）共同构成给生成 AI 的「设计规范、用语」注入材料（需求 8）。JSON Schema 文件落 `classroom_app/services/lessondoc/schema/deck.schema.json`。

### 3.1 顶层结构

```jsonc
{
  "spec": "lessondoc/2.0",
  "kind": "lesson",                    // lesson | home
  "lesson": 3,                         // 课次号，必须等于所在目录 lesson_N 的 N
  "course": "《计算机网络原理》",
  "title": "差错检测与可靠传输",
  "subtitle": "从比特错误到滑动窗口",
  "badge": "第 3 课 · 理论课",          // 封面徽章；实验课写 "第 N 课 · ★实验课"
  "theme": "sky",                      // 主题名（可省，省则用 course.json 的 theme）
  "layoutProfile": "slides",           // slides | article（版式族，见 §5.4）
  "slides": [ …若干 Slide 对象… ]
}
```

### 3.2 Slide 对象

```jsonc
{
  "layout": "content",       // title|section|content|two-col|center|grid|end
  "section": "可靠传输",      // 页眉小节名（title/section/end 版式忽略）
  "title": "停止等待协议",
  "sub": "最朴素的可靠传输：发一个，等一个",   // 可选副标题
  "blocks": [ …内容块… ],     // content/center/end 版式：单列自上而下
  "left":  [ … ], "right": [ … ],           // two-col 版式专用
  "areas": [ {"area":"1/1/5/7","blocks":[…]}, … ],   // grid 版式专用（§5.2）
  "notes": "教师备注：此页重点强调超时重传的定时器取值……"   // 仅教师端壳页可见
}
```

版式语义（与 cnet-course 一一对应，`slide--two-col`/`slide--center` 是范例中已定义未使用的预留版式，本规范正式启用）：

| layout | 用途 | 专属字段 |
|---|---|---|
| `title` | 课次封面（每课第 1 页） | `badge`/`title`/`sub`/`course` 取顶层值 |
| `section` | 章节分隔页 | `no`("01")、`title`、`hint` |
| `content` | 默认内容页（绝大多数） | `blocks` |
| `two-col` | 左右对照（图+文、对比） | `left`/`right`，可选 `ratio`: "1:1"\|"3:2"\|"2:3" |
| `center` | 金句/大数字页 | `blocks`（通常一个 `bigmark`） |
| `grid` | 自由网格舞台（§5.2） | `areas` |
| `end` | 结尾页 | `summary`、`nextUp`（下节预告文本）、导航链接自动生成 |

### 3.3 内容块（Block）类型清单

每个块的通用字段：`type`（必填）、`step`（整数，分步显现的登场步序，省略=随页显示）、`exitStep`（整数，该步之后退场，少用）、`id`（可选，供 stepper 引用）。

**排版类**

| type | 字段 | 说明 |
|---|---|---|
| `text` | `md` | 短段落，支持行内 Markdown 子集：`**粗**`、`` `代码` ``、`*斜*`；**禁止长文**（引擎超 240 字告警） |
| `cards` | `cols`(2\|3\|4), `items[{icon?,title,text,tone?}]` | 卡片网格；tone: `primary`(默认)\|`ok`\|`warn`\|`err` 控制左侧竖条色 |
| `bignum` | `items[{value,label,note?}]` | 大数字卡（"32 次课"/"64 学时"） |
| `bigmark` | `mark`, `line` | center 版式的巨型标语（"协议 = 约定"） |
| `timeline` | `items[{title,text}]` | 横向时间线/流程条（≤6 站） |
| `table` | `head[]`, `rows[][]`, `rowStep?`(bool) | 表格；rowStep=true 逐行 fragment；**>6 行 or >5 列时引擎告警建议拆页** |
| `callout` | `tone`("info"\|"think"\|"warn"\|"ok"), `md` | 提示框 |
| `tabs` | `tabs[{label,blocks[]}]` | 选项卡（主要用于首页与 article 版式） |
| `details` | `summary`, `blocks[]` | 折叠面板（作业要求/扩展阅读） |

**代码与媒体类**

| type | 字段 | 说明 |
|---|---|---|
| `code` | `lang?`, `code`, `output?` | 代码块（一键复制）+ 可选命令输出条 |
| `media` | `kind`("image"\|"video"\|"audio"), `src`, `caption?`, `poster?` | `src` 只允许包内相对路径（`media/xxx.png` 或 `../assets/xxx`）；引擎对缺失资源渲染占位卡而非破版 |

**图示类（需求 5 的流程图/时序图/架构图/思维导图）**

统一入口 `type:"diagram"`，`kind` 分流。全部由引擎做**简单确定性自动布局**（不追求 graphviz 级效果，追求稳定不破版），配色一律用语义色名（`primary/ok/warn/err/muted`），换主题自动换色：

```jsonc
// 流程图：纵向/横向分层布局
{ "type":"diagram", "kind":"flow", "direction":"h",
  "nodes":[{"id":"a","label":"应用层","tone":"primary"},{"id":"b","label":"传输层"}],
  "edges":[{"from":"a","to":"b","label":"报文段"}] }

// 时序图：参与者泳道 + 有序消息（自动画生命线与箭头）
{ "type":"diagram", "kind":"sequence",
  "actors":[{"id":"c","label":"🖥 客户端"},{"id":"s","label":"☁ 服务器"}],
  "messages":[{"from":"c","to":"s","label":"SYN","step":1},
              {"from":"s","to":"c","label":"SYN+ACK","step":2}] }
  // 消息可带 step → 与 fragment 联动逐条显现

// 架构图：分层容器 + 内部节点（container 可嵌一层）
{ "type":"diagram", "kind":"arch",
  "layers":[{"label":"边缘部分","nodes":[{"label":"主机A"},{"label":"主机B"}]},
            {"label":"核心部分","nodes":[{"label":"路由器","tone":"warn"}]}],
  "links":[{"from":"主机A","to":"路由器"}] }

// 思维导图：嵌套树（复用 cnet 的 ul+CSS 连线渲染器，叶子可带 href）
{ "type":"diagram", "kind":"mindmap", "root":"计算机网络原理",
  "children":[{"label":"概述","note":"第1—4课","collapsed":false,
               "children":[{"label":"第1课 互联网组成","href":"../lesson_1/lesson_1.html"}]}] }
```

**逃生舱**：`type:"svg"`（`viewBox`,`body`,`caption?`）允许 AI 手绘任意 SVG（复杂原理图仍是 SVG 表达力最强）。硬约束：`body` 内禁止 `<script>`/事件属性（解析器强制剥除）；**颜色必须写 `var(--dg-primary)` 等主题变量**（解析器把违规十六进制替换为最近的语义色并记告警）。SVG 内元素可带 `class="fragment" data-step="2"` 分幕。

**交互类**

| type | 字段 | 说明 |
|---|---|---|
| `quiz` | `q`, `options[{k,text}]`, `answer`, `explain` | 随堂测验，点选即判分显示解析；一题一页 |
| `tasklist` | `items[]` | 实验任务清单（真 checkbox） |
| `reveal` | `items[{label,md}]` | 按钮点击揭示（"猜猜看→答案"），替代范例中零散的按钮交互 |
| `stepper` | `stage`(一个 svg 或 diagram 块), `steps[{text, set[{target,attr,value}] , show[], hide[]}]` | 步骤演示：**声明式**替代范例的 JS 回调 —— 每步为对舞台内 `#id` 元素的属性赋值/显示/隐藏操作集，引擎通用播放器执行（上一步/下一步/计数/解说区自动生成） |

stepper 案例（对应 cnet lesson_1 的存储转发演示）：

```jsonc
{ "type":"stepper",
  "stage": { "type":"svg", "viewBox":"0 0 640 150",
    "body":"<g>…主机A/R1/R2/主机B 静态舞台…<g id='pkt' visibility='hidden'>…</g><text id='note' x='320' y='135'/></g>" },
  "steps": [
    { "text":"主机 A 把分组交给链路，发往 R1。",
      "show":["#pkt"],
      "set":[{"target":"#pkt","attr":"transform","value":"translate(0,0)"},
             {"target":"#note","attr":"textContent","value":"分组在 A→R1 链路上传输"}] },
    { "text":"R1 完整收下分组放进缓存——这一步叫“存储”。",
      "set":[{"target":"#pkt","attr":"transform","value":"translate(108,-5)"},
             {"target":"#note","attr":"textContent","value":"分组暂存在 R1 缓存"}] }
  ] }
```

### 3.4 course.json（课程清单，包根）

既是首页数据源，也是**AI 的课程全景知识包**（需求 8）：

```jsonc
{
  "spec": "lessondoc/2.0",
  "kind": "home",
  "course": { "name":"计算机网络原理", "code":"E040016B1", "credits":3,
              "totalHours":64, "sessionCount":32, "perSessionSections":2,
              "assessment":"平时 40% + 期末 60%", "intro":"一句话导语" },
  "textbook": { "title":"计算机网络（第8版）", "author":"谢希仁", "publisher":"电子工业出版社" },
  "theme": "sky",                       // 全课程默认主题
  "stages": [                           // 阶段分组（首页卡片墙 + 思维导图共用数据）
    { "label":"总纲 · 概述", "lessons":[1,2,3,4] },
    { "label":"物理层", "lessons":[5,6] }
  ],
  "lessons": [                          // 与 course_lessons 逐条对应，order_index=n
    { "n":1, "title":"概述：互联网的组成与工作方式", "lab":false,
      "topics":["边缘/核心","分组交换","三种交换方式对比"],
      "summary":"≤120字的本课摘要（生成后由 AI 回填，供相邻课次上下文注入）",
      "status":"ready",                 // ready | pending
      "userHint":"多讲存储转发的直观例子"  // 教师给本课的生成提示（需求 3/8）
    }
  ],
  "conventions": {                      // 全课程统一用语（注入每次生成）
    "submit":"作业/实验报告一律在 lanshare 平台完成提交",
    "aiPolicy":"允许用 AI 辅助理解，禁止直接抄答案",
    "terms":{ "课次":"每次课=2课时", "分组":"packet 统一译作“分组”" }
  },
  "tabs": [ {"label":"环境准备","blocks":[…]}, {"label":"考核方式","blocks":[…]} ]
}
```

首页渲染 = hero（course 数字卡）+ mindmap（stages×lessons 自动生成，叶子链到 ready 课次）+ 阶段卡片墙（pending 显示"待发布"不可点）+ tabs + footer（教材）。**课次新增/重生成后只需改 course.json 并重渲 main.html，首页导航与导图永不失同步**（范例中手工维护两处易漏的问题就此消除）。

### 3.5 内容编排守则（写进 AI 提示词）

沿用《学习文档HTML包设计规范》§二/§六，并量化为可校验规则：

1. 一课 18—25 页；开头三件套（封面→学习目标 cards→本课地图 timeline/diagram）+ 结尾三件套（quiz 每题一页→作业/实验 details 或 cards→end 页含 nextUp）。
2. 一页一个概念；`text` 块 ≤240 字；表格 >6 行拆页；多用 `step` 分步。
3. 重难点必配 diagram 或 svg；figure 必带 caption。
4. 文理工科适配靠**块型组合**而非分支模板：文科多用 timeline/cards/reveal/quiz（史实线索、观点对比、文本细读），理科多用 svg/stepper/code（推导、演算），工科多用 arch/sequence/tasklist（系统结构、协议交互、实验步骤）。authoring-guide 各给一节完整案例。
5. 禁止出现班级/学生/学校具体信息；提交渠道只写 lanshare 平台。

---

## 4. 健壮性规范（需求 8：绝不因小问题整体报错）

分三层，口径统一为「**丢块不丢页，丢页不丢课，永远渲出东西 + 明示告警**」：

1. **后端校验器**（`lessondoc_service.validate_deck`，生成落库前）：
   - 致命错（任务失败）仅 4 种：整体非合法 JSON / `spec` 缺失或主版本不符 / `slides` 非数组或为空 / `lesson` 与目标课次号不符。
   - 其余一律**降级修复 + 记录**：未知 block type → 替换为占位 `callout(warn)`；块字段缺失 → 丢弃该块；未知 layout → 按 `content`；svg 含 script/事件 → 剥除；硬编码色 → 替换语义色；页数超 40 → 截断。产出 `warnings[]` 随任务结果返回，前端在材料卡与生成结果面板展示「生成完成，N 处内容已降级（查看详情）」。
   - 与现有 `_normalize_generated_html_package_nodes` 的「逐条 500 拒收」范式**刻意不同**：那是给整页 HTML 的（坏一处毁全文件）；JSON 逐块可局部止损，宽容才是正确策略。文件路径层面（目录名/入口名/扩展名白名单）仍复用原有严格校验。
2. **前端引擎**（deck-engine.js）：每个 slide、每个 block 的渲染都包在 try/catch；单块异常 → 渲染占位卡「此内容块加载失败」；JSON 解析失败 → 整页显示友好错误页（含"联系教师重新生成"）。引擎自身零外部依赖、ES5 兼容口径与 slides.js 一致。
3. **AI 输出解析**：`response_format="json"` + 常规三连（剥 markdown 围栏/截取首尾花括号/`json.loads` 失败重试一次），复用平台既有做法。

---

## 5. 舞台、网格与自适应（需求 6）

### 5.1 舞台模型

沿用 cnet 框架：虚拟画布 **1280×720**（16:9 基准），整页 `transform:scale` 等比缩放居中，`fitSlide` 对超高内容二次缩放兜底（下限 0.5），HUD 支持 16:9/4:3/16:10/适应窗口（localStorage 记忆）。**「元素=表演者、页面=舞台」的登退场语义由 `step`/`exitStep` 承载**：引擎把每页所有块按 step 排序生成 fragment 序列，翻页键先消化步序再翻页（与现行 slides.js 行为一致）；`exitStep` 实现为到步后加 `.fragment-exit`（透明+位移离场）。

### 5.2 grid 版式的网格法

`layout:"grid"` 时舞台划分为 **12 列 × 8 行**（内容区 1152×600，去掉页眉页脚与内边距后的净空），`area` 用 CSS grid-area 语法 `"rowStart/colStart/rowEnd/colEnd"`：

```jsonc
{ "layout":"grid", "section":"传输层", "title":"TCP 三次握手全景",
  "areas":[
    { "area":"1/1/9/8",  "blocks":[{ "type":"diagram","kind":"sequence", … }] },   // 左 7 列通栏放时序图
    { "area":"1/8/5/13", "blocks":[{ "type":"cards","cols":1, … , "step":1 }] },   // 右上要点卡，第1步登场
    { "area":"5/8/9/13", "blocks":[{ "type":"callout","tone":"think", …, "step":2 }] }
  ] }
```

引擎渲染为 `display:grid; grid-template:repeat(8,1fr)/repeat(12,1fr)`，区块溢出各自 `fitSlide` 局部缩放。**不同显示器尺寸的适配完全由虚拟画布缩放解决**——网格坐标永远相对 1280×720，投影所见即所得；手机上引擎额外提供「逐页竖排阅读模式」（见 5.4）。

### 5.3 主题切换（需求 4）

- `themes.css` 定义命名主题集，每个主题 = 一组 CSS 变量（`--primary/--primary-dark/--primary-soft/--dg-*` 图示色 + 舞台底色）：首发 6 个 —— `sky`(蓝,理工默认)、`teal`(青绿)、`violet`(紫)、`amber`(暖橙,文科)、`rose`、`slate`(素黑白,打印友好)；外加 `dark` 修饰符（深色舞台）。
- 作用机制：`<html data-theme="teal">`。优先级：URL 参数 `?theme=` > localStorage（按包记忆）> deck JSON `theme` > course.json `theme`。
- 切换入口：① 幻灯片 HUD 加主题下拉（学生/教师均可临时切，存 localStorage）；② 壳页工具条加「外观」按钮（同能力）；③ 生成向导选默认主题写入 course.json。**图示 DSL 与 svg 块的语义色约束（§3.3）是换肤彻底性的保证**。

### 5.4 版式族切换（layoutProfile）

- `slides`（默认）：PPT 舞台模式，上述全部。
- `article`：同一份 JSON 渲染为纵向长文模式（每 slide 变一个 section，fragment 全展开，quiz/stepper 照常交互）——手机自学与打印场景。HUD 提供「幻灯/文档」一键切换（不改文件，纯前端重渲）。
- 这实现了「已生成材料在渲染使用时也可切换版式」（需求 4）且零存储成本。

---

## 6. 可逆性（需求 10）

- **配置→文档**：确定性渲染（同一 JSON + 同一引擎版本 → 同一 DOM）。
- **文档→配置**：`lessondoc_service.extract_deck(html_text)` 抽 `#lessondoc-data` 即得全量 JSON，无损。
- **旧手写包→配置**（迁移工具，P4 可选）：`import_legacy_package` 对无 `data-lessondoc` 标志的包做尽力抽取（slide 骨架/标题/表格/quiz/文本可靠还原；手写 SVG 原样进 `svg` 块；无法识别的结构进 `text` 块并列入告警清单）——**允许有损，逐项提醒用户**，符合需求 8 的口径。cnet-course 可作为迁移验收样本。
- 引擎版本策略：包内 assets 是生成时刻的副本（离线锁版）；`spec` 主版本不兼容才需要迁移；平台提供「刷新包内引擎」按钮（只覆盖 assets/，不动内容文件）。

---

## 7. 平台集成设计

### 7.1 数据模型（新增 runtime 表，零改旧列）

`classroom_app/db/schema_course_doc_packs.py`（polls 模式，engine-aware，`_SCHEMA_READY`；挂 `schema.py::init_database` 双引擎分支 + `RUNTIME_ENSURED_SCHEMA_MODULES` 豁免）：

```sql
CREATE TABLE IF NOT EXISTS course_doc_packs (
  id                 <engine id>,
  root_material_id   INTEGER NOT NULL UNIQUE,   -- 包根 course_materials.id
  course_id          INTEGER NOT NULL,          -- → courses.id
  teacher_id         INTEGER NOT NULL,
  spec_version       TEXT NOT NULL DEFAULT 'lessondoc/2.0',
  theme              TEXT NOT NULL DEFAULT 'sky',
  status             TEXT NOT NULL DEFAULT 'active',   -- active|archived
  manifest_cache_json TEXT NOT NULL DEFAULT '{}',      -- course.json 缓存（读加速，真源仍是包内文件）
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_course_doc_packs_course ON course_doc_packs(course_id, teacher_id);

CREATE TABLE IF NOT EXISTS course_doc_pack_lessons (
  id           <engine id>,
  pack_id      INTEGER NOT NULL,
  lesson_no    INTEGER NOT NULL,
  gen_status   TEXT NOT NULL DEFAULT 'pending',  -- pending|queued|running|ready|failed|excluded
  user_hint    TEXT NOT NULL DEFAULT '',         -- 教师对本课次的生成提示
  last_task_id INTEGER,                          -- → session_material_generation_tasks.id
  warnings_json TEXT NOT NULL DEFAULT '[]',
  updated_at   TEXT NOT NULL,
  UNIQUE(pack_id, lesson_no)
);
```

设计要点：**刻意不挂 `class_offering_id`**（免 MERGE_RULES 负担）——pack 归属课程与教师；具体课堂绑定完全复用现有 `apply_package_session_bindings` + 双层绑定表。`excluded` 状态实现需求 3 的「用户可排除内容」。时间戳一律 ISO-8601 TEXT（与 polls 模式一致）。

### 7.2 后端模块

新增 `classroom_app/services/lessondoc/`（目录化，遵守 <800 行/文件）：

| 文件 | 职责 |
|---|---|
| `spec.py` | 常量：spec 版本、block 类型注册表、主题清单、限额（页数/字数/文件数） |
| `validate.py` | `validate_deck` / `validate_manifest`（§4 降级式校验，返回 `(clean_json, warnings)`） |
| `render.py` | `render_lesson_html(deck)` / `render_home_html(manifest)`：壳 HTML 拼装（模板字符串，非 Jinja——包文件是静态资产）；`extract_deck(html)` 反抽取 |
| `assets.py` | 平台内置引擎资产的读取与「复制进包」；`static/lessondoc/2.0/` 清单与 sha 校验 |
| `pack_service.py` | pack CRUD、`create_pack_skeleton`（建包根/assets/README/course.json/main.html + 材料库落库）、`refresh_home`（course.json 变更后重渲 main.html）、`plan_lessons_from_textbook`（AI 拆分课次建议，复用 `build_textbook_prompt_context` + `course_lessons`）、`build_generation_context`（§7.4 知识包） |
| `legacy_import.py` | （P4）旧包尽力抽取 |

生成执行落在**新模块** `lessondoc/generate.py`，**不复用** `session_material_generation_tasks`。

> **实现期口径修正（2026-09-01）**：原计划复用该任务表，但它的 `class_offering_id`
> 是 NOT NULL + 外键，要求任务必须挂在具体课堂上；而 lessondoc 任务属于「课程 × 包」，
> 与课堂无关（施工中实测触发 `FOREIGN KEY constraint failed`）。改为**任务态即
> `course_doc_pack_lessons.gen_status`**（pending/queued/running/ready/failed/excluded），
> `_claim_lesson` 做 queued→running 原子领取防重复派发，前端轮询 `GET /api/lessondoc/packs/{id}`。
> 好处：旧表零污染、旧生成通道零改动、无需为无课堂场景造假数据。

### 7.3 API（新路由文件 `classroom_app/routers/lessondoc.py`，注册在 materials 之前防路由遮蔽）

```
POST /api/lessondoc/packs                    创建包（course_id, theme, lesson_plan[], 生成范围）
GET  /api/lessondoc/packs?course_id=         查询课程的包 + 逐课状态
GET  /api/lessondoc/packs/{pack_id}          详情（manifest + lessons 状态 + warnings）
POST /api/lessondoc/packs/{pack_id}/plan     AI 按教材拆分课次建议（预览，不落库）
PUT  /api/lessondoc/packs/{pack_id}/lessons/{n}   更新单课（user_hint / excluded / 标题主题）
POST /api/lessondoc/packs/{pack_id}/lessons/{n}/generate    生成/重生成单课（enqueue）
POST /api/lessondoc/packs/{pack_id}/generate-batch          批量补齐（顺序入队，见 §7.5）
POST /api/lessondoc/packs/{pack_id}/bind     绑定课堂（body: class_offering_ids → apply_package_session_bindings）
POST /api/lessondoc/packs/{pack_id}/refresh-assets   刷新包内引擎副本
POST /api/lessondoc/packs/import-legacy      （P4）旧包升级
```

鉴权：全部教师本人（pack.teacher_id）；绑定另校验课堂 owner。新路由后重生成 p02 路由快照。

### 7.4 AI 生成上下文包（需求 8 的「AI 知道课程的所有信息」）

`build_generation_context(pack, lesson_no, mode)` 组装，注入每次生成/重写（预算约 30k 字符，超限按下表优先级截断）：

| 优先级 | 内容 | 来源 |
|---|---|---|
| 1 | 编写规范精编版（block 用语表 + 编排守则 + 3 个典型页案例） | `docs/lessondoc-authoring-guide.md` 的 AI 摘要节（静态文本） |
| 2 | course.json 全量（课程/教材/阶段/全部课次标题+topics+summary/术语约定） | 包根文件 |
| 3 | 本课次：course_lessons 的 title/content + 教师 user_hint + 重写时的现有 deck JSON | DB + 包文件 |
| 4 | 前一课完整 deck JSON（风格参照）+ 前后各 2 课的 summary | 包文件 |
| 5 | 教材目录相关章节（catalog_text 按课次主题模糊截取）+ introduction | textbooks |
| 6 | 课堂上下文（若从课堂入口发起）：专业/年级/近期作业错点 | 复用 `_load_final_material_classroom_context` |

生成完成后回写 `lessons[n].summary`（让后续课次生成时能引用）并 `refresh_home`。AI 参数：`task_type="deep_text_reasoning"`、`task_label="lessondoc_generate"`（进 ai_assistant 关键任务名单 → `reasoning_effort=max`）、`response_format="json"`、`task_priority="background"`。

### 7.5 批量生成策略

32 个课次不一次性生成（成本/时长/教材理解漂移）。默认策略：**建包时生成 首页 + 前 2 课**；其余 `pending`，三种补齐方式——① 单课手动生成；② 「补齐后面 N 课」批量（顺序入队，每课完成后其 summary 进入下一课上下文，保证前后连贯）；③ 课堂页「AI 生成下次课」（§7.6-D）。批量任务失败单课标 `failed` 不阻断队列（复用现有任务隔离）。

### 7.6 各页面入口（需求 11/12/13/14）

**A. 内容资产 > 课程页**（`manage/courses.html` + `manage_courses.js`，需求 13）
- 课程卡片新增状态位：已确定课次数（course_lessons 存在）且有可用教材（同课程任一课堂 textbook_id 非空，或向导内现选）→ 显示「生成学习文档包」按钮；已有 pack → 显示「学习文档包 · N/M 课就绪」徽章（点击进材料页定位包）。
- 教务同步结果面板（`academic_sync_dialog.js` `renderResult`）追加一张卡：「课程学习文档 —— 可为 X 门已确定课次的课程生成学习文档包」+ 逐课程「去生成」按钮（深链 `/manage/teaching/courses?lessondoc=<course_id>` 自动开向导）。
- **建包向导**（新 JS 模块 `static/js/lessondoc_wizard.js`，模态三步）：
  1. 课次划分：默认取 course_lessons；无则调 `/plan` 由 AI 按教材目录拆分（总学时/每课节数沿用现有校验）；每行可编辑标题/topics/排除/填 user_hint；可整体加「课程级生成提示」。
  2. 外观：主题六选一（活预览色卡）+ 默认版式族。
  3. 确认：包名（默认 `{课程名}-学习文档包`，落教师材料库根目录）、立即生成范围（首页+前2课 / 全部 / 仅骨架）。
- 提交 → `POST /api/lessondoc/packs` → 材料页可见 + 生成任务排队。

**B. 内容资产 > 材料页**（`manage/materials.html` + `materials_manage.js`，需求 11）
- 顶栏「新建」菜单加「课程学习文档包」（打开同一向导）。
- pack 包根文件夹卡片：徽章「学习文档包 lessondoc/2.0」+ 进度「N/M 课就绪」+ 操作「管理课次」（抽屉：逐课状态/告警/生成/重写/排除/改 hint）、「绑定课堂」、「切换主题」、「刷新引擎」。识别方式：`course_doc_packs.root_material_id` 反查（列表序列化时批量 attach，仿 `attach_render_metadata`）。
- 生成中状态复用现有 `aiPending` 提示卡模式。

**C. 课堂运行 > 课堂管理**（offering hub，`manage_offering_hub.js`，需求 12）
- 课堂详情抽屉「学习文档」小节：显示当前绑定来源（无 / 手动材料 / lessondoc 包）；若本课程存在 pack 且未绑 → 一键「绑定学习文档包」（调 `/bind`，确定性绑齐首页+全部就绪课次）；已绑 → 显示逐课就绪度 + 缺失课次「去生成」深链。保持该页「聚合展示+深链跳转」的既有定位，不在此页做重编辑。

**D. 首页 > 具体课堂（教师视角）**（`classroom_page.js` + `session_material_ai_modal`，需求 14）
- 课次时间轴的「AI 生成材料」入口智能分流：该课堂绑定的是 lessondoc 包 → 模态切换为包模式，提供两个按钮：
  - **「AI 重写本课」**：带 user_hint 输入框（预填历史 hint），走 `/lessons/{n}/generate`（mode=rewrite，注入现有 deck）；
  - **「AI 生成下次课」**：定位第一个 `pending` 课次生成。
  - 完成后包内文件落在原路径，绑定无需变更，仅刷新缓存/blurb。
- 非包课堂维持现有 HTML 包/散文件生成逻辑不变。

**E. 材料中心 /manage/library**：学习文档分类的搜索结果对 pack 包根附加「学习文档包」徽标（`material_hub_service` 学习文档 searcher 顺带 join packs 表），不新增分类。

### 7.7 git 同步与外部编辑的共存

教师仍可把 pack 推到 git 仓库手工精修（内嵌 JSON 可直接编辑）。git pull 后：入口候选识别不变；新增一步——若变更文件属于 pack，`refresh` 时重抽 JSON 校验，告警进 pack lessons 的 warnings_json。平台生成与手工编辑以「最后写入者赢」处理，不做合并（UI 文案明示）。

---

## 8. 施工计划

> 顺序即依赖序。每阶段结束跑全量单测 + 路由快照，UI 阶段用 Claude Browser 真机截图验收。预估总量：新增 ~6k 行（含引擎 JS ~1.8k、服务 ~1.5k、文档/案例 ~1.5k）。

### P0 规范与引擎（纯前端资产，无 DB/路由改动）✅ 已完成
1. 定稿本文档 + 编写 `docs/lessondoc-authoring-guide.md`：全部 block 的 JSON 案例、文/理/工三科各一节完整示范页、AI 摘要节（供提示词注入）。
2. 从 cnet-course 提炼定稿 `static/lessondoc/2.0/`：course.css/slides.css/course.js/slides.js（修范例已知瑕疵：补 `.grid-1`、收敛 inline style 为工具类、保留预留版式）+ 新写 `themes.css`（6 主题+dark）+ `deck-engine.js` + `home-engine.js`。
3. `schema/deck.schema.json` + `manifest.schema.json`。
4. 验收：手工构造一份 3 课次 deck JSON，本地 file:// 双击全功能可用（翻页/fragment/quiz/stepper/diagram/主题切换/article 模式/手机宽度）。

### P1 解析与渲染服务（后端，无 UI）✅ 已完成（28 项单测全绿）
1. `services/lessondoc/` 五模块（§7.2，暂缺 legacy_import）。
2. runtime 表 `schema_course_doc_packs.py` + init_database 双引擎注册。
3. `extract_html_text` 的 lessondoc 分支增强。
4. 材料删除链（delete-impact）级联置 pack `archived`。
5. 单测：validate 降级矩阵（每类坏输入→期望降级+告警）、render/extract 往返相等性、pack skeleton 落库形状、`.git` 过滤。

### P2 生成管线 ✅ 已完成（9 条路由 + 快照重生成 + 真机端到端）
1. `session_material_generation_service` 增 `document_type="lessondoc"` 分支 + `build_generation_context` + summary 回写 + refresh_home。
2. `routers/lessondoc.py` 全部 API + 路由快照重生成。
3. 批量顺序队列（§7.5）。
4. 单测：任务领取/失败隔离/上下文组装截断/绑定复用（mock AI 返回固定 deck）。

### P3 前端入口 ✅ 已完成（四入口全部真机实测）
1. ✅ 建包向导 `lessondoc_wizard.js` + 课程页接入（A，含深链 `?lessondoc=`）+ 材料页接入（B：包根卡片徽标「学习文档包 2.0」/进度「N/M 课就绪」/「管理课次」按需加载向导）。
2. ✅ 主题/版式切换：由引擎 HUD 在包内提供（壳页 iframe 中已验证可用）。**刻意不加壳页「外观」按钮**——会与 iframe 内 HUD 形成重复控件。
3. ✅ offering hub（C：metrics 加「学习文档」格 + 未绑定时「绑定学习文档」一键绑，批量 join 见 `_lessondoc_pack_map`）、课堂页分流（D：`AI 重写本课` / `AI 生成第 N 课`，包模式下隐藏通用 AI 入口，数据源 `GET /api/lessondoc/classrooms/{id}/pack`）、材料中心徽标（E）。
4. ✅ island `?v=` 已 bump（materials-manage-page / classroom-page 均为 `lessondoc-20260901`）；契约测试版本串断言同步改。
5. ✅ 真机验收（p03-qa，2026-09-01）：建包 → 生成 2 课 → 绑定课堂 → 材料页徽标/进度/管理课次 → hub 未绑定→一键绑→已绑直达 → 课堂页按课次状态分流文案 → 点击真实发起生成（hint 落库、状态流转、AI 输出不合规时 failed + 人话告警）→ 壳页内换主题计算样式生效。

### P4 真实 AI 验收（2026-09-01，本地真库 + 真 key）

用真实课程「计算机网络」（course_id=4，16 课次、教材目录 3136 字）跑第 1 课生成：

- **一次通过，零告警**（`gen_status=ready`，warnings 为空）——AI 首次输出即完全合规，校验器无需降级任何块。
- **结构**：24 页 / `title 1 + content 18 + section 4 + end 1`；开头三件套 + 4 个章节分隔 + 5 题测验（每题一页）+ 作业页 + 结尾页，与 authoring-guide 的编排守则完全一致。
- **块型丰富度**：diagram×4（含时序图）、svg×1、callout×8、quiz×5、code×2、cards/timeline/table/bignum/reveal/tasklist 各若干。
- **内容对齐**：逐页标题与该课教材要点（边缘/核心、性能指标、五层协议、ping/tracert 实验）严格对应。
- **暴露并修复一个真实可用性问题**：AI 为「数据封装与解封装」用了 12 个节点的单链 flow，横向排布被画布缩放压到文字不可读 → 引擎改为**单链且节点数 > 6 时自动折行**（每行 ≤6，跨行边走折线绕行），实测宽度 2000+ → 1100，文字清晰。多分支图不折行（会让连线含义混乱）。

结论：**AI 生成质量达标，可交付教师使用**；`load_guide_summary()` 注入的编写契约是有效的。

### P4 收尾与迁移
1. ✅ `legacy_import.py` + cnet-course 真机迁移验收（2026-09-01）：真实包导入 QA → `POST /api/lessondoc/packs/import-legacy`（先 `dry_run` 预览告警再落库）→ 25 页全量还原、版式/卡片/表格/SVG/测验/代码块逐项对齐、首页统计（64 学时 / 32 课次 / 4 学分 / 考试）全对。**原包纹丝不动**，抽取结果落到新包，教师自行对比后再删旧包。
   - 已知有损（诚实告警，非缺陷）：stepper 的解说词写在页面内联 JS 里抽不回来（只保留静态舞台图）；首页阶段分组无法可靠还原（合并为「全部课次」）。
   - 迁移中发现并修复两个真 bug：① lxml 会把属性名小写化导致 `viewBox` 全丢（图形变形）；② 颜色收敛只有单一兜底色，浅底 `#e0f2fe` 与深字 `#075985` 被映射成同一个主色把图糊死 —— 改为**按亮度分档**（纯白→fill / 浅色→primary-soft / 深色→primary-dark / 其余→primary），两处均有单测锁定。
2. 部署（走 deploy-workflow；`static/lessondoc/` 纳入同步清单）、推送。
3. 更新记忆 `lessondoc-template-system.md` + MEMORY.md 索引；本文档回填实际口径。

---

## 11. 后续改进路线（P0—P4 已完成后的下一批）

按「风险优先 → 高频需求 → 补齐有损 → 体验打磨」排序。每项标注依据，**不是凭空规划**。

### R1 学生侧端到端验证 ✅ 已完成（2026-09-02，干净 QA 环境真机实测）
**依据**：P0—P4 全程用**教师账号**验证，学生视角一次都没跑过。
**实测结果（学生账号全流程）**：
- **权限链** ✓：包根锚定授权让学生能取到包内全部资源（壳页/课次页/assets 引擎/主题 CSS/course.json 全 200）。
- **越权防护** ✓：其他班学生访问未授权包 → 全部 303 拒绝（重定向登录页，平台既有 403→登录页行为）；学生访问 7 个教师端 `/api/lessondoc/*` 与材料库接口 → 全部 403。
  ⚠ **测试方法教训**：首轮探测误报「越权可读」——urllib 默认跟随重定向，把 303→登录页读成 200。**测权限必须禁重定向 + 检查响应体特征**，双证据链（HTTP 层 + 服务层直调 `ensure_user_material_access`）交叉确认。
- **交互** ✓：翻页/fragment 分步（0→2 逐个显现）/quiz 判分（错项红、对项绿、禁重答、解析显现）/flow 图渲染。
- **学习进度心跳** ✓：`learning_material_progress` 落库（accumulated_seconds/active_seconds，page_key=material_render_shell），并计入修为快照 material 分量。
- **article + 手机** ✓：375px 无横向溢出；发现并修复「多列卡片在手机被压成一字一行」→ slides.css 对 article 模式 ≤640px 统一降单栏、时间线转纵向、宽表横滚（幻灯模式不受影响，虚拟画布语义保持）。修复经「刷新引擎」API 实测下发到包内副本（16009B→16780B）。
- **首页导航** ✓：ready 课次可点、pending 不可点、思维导图正常。

### R2 单页重写 ✅ 已完成（2026-09-02，真 AI 黄金验收通过）
**落地**：
- `POST /api/lessondoc/packs/{id}/lessons/{n}/slides/{slide_no}/rewrite`（**1 起页码，与 HUD 页码一致**）+ `GET /api/lessondoc/packs/by-root/{root_material_id}`（壳页只知 nodeId，按包根反查 pack；**必须注册在 `GET /{pack_id}` 之前**，否则 "by-root" 被当 int 解析 422）。
- **同步执行，刻意不走任务队列**：交互式优先级 + 240s 上限，教师改完立即可见；失败直接报错，零状态残留。
- **落盘前内存校验**：新页替换后先 `validate_deck` 预检，页数变少（新页被降级整页丢弃）→ 拒绝且**原文件纹丝不动**——绝不悄悄少一页。
- **剥壳 + 自动纠错重试**：AI 有随机性，`_unwrap_slide_payload` 接住裸对象/整 deck（按下标取页）/任意键包装；识别不了则带「上次你输出了 X」的纠正提示自动重试一次，两次失败才报错且**错误信息带实际输出键**（可观测性实测有用——正是靠它定位了 QA mock AI）。
- 壳页工具条注入「✏ 改这一页」（教师 + LessonDoc 包才显示，旧手写包无入口）；课次号/页码从 iframe 路径与 hash 实时读取；成功后刷新 iframe 保持页码。
- **QA 环境有坑**：p03-qa 自带 mock AI（`tools/mock_ai_assistant.py`，端口 8024，`AI_ASSISTANT_URL` 指向它），返回固定假 JSON——在 QA 里点重写永远走到「AI 输出不可用」的失败路径（这本身验证了错误呈现）。内容验收需本地进程连真 AI（8001）操作 QA 库。
- **黄金验收**：真 AI 按「就业导向、接地气」hint 重写第 2 页——内容完全达意、卡片版式与 step 保留、前后页与总页数不动、零告警。

### R5 引擎版本治理 ✅ 已完成（2026-09-02，UI 全闭环实测）
**落地**：`assets_fingerprint()` 加进程内缓存；`attach_pack_metadata` / `_pack_summary` 附加 `assets_outdated`（指纹比对）；材料页包根卡片显示「引擎可更新」徽标；向导管理面板刷新按钮 outdated 时高亮为「⬆ 引擎可更新」，刷新成功后面板重载恢复。闭环实测：篡改指纹 → 列表/详情 True → 徽标+高亮出现 → 点击刷新 → 全部恢复 False。单测锁定三态生命周期。

### R3 补齐迁移有损（stepper 解说词 / 阶段分组）✅（2026-09-02 完成）
**依据**：cnet-course 迁移实测的两条已知有损。
- **stepper 补全**：走 R2 的「改这一页」链路——迁移告警文案直接告诉教师「打开该页点『✏ 改这一页』，要求 AI 把静态图改成分步演示」。真机验收（真实 AI，QA 库 CRC 校验静态 svg 页）：AI 产出 3 步 stepper，舞台 SVG 原元素（dividend/divisor/remainder/rtext/note 五个 id）全保留，steps 用 set/show 驱动原元素，浏览器实测控件 1/3→3/3、余数从隐藏到显示"余数 011"、note 给出完整 CRC 码，零告警。
- **阶段分组**：建包向导加「③ 阶段分组(可选)」textarea（`入门篇: 1-3` 一行一组，`parseStagesText` 容错各种分隔符/区间/顿号）；管理面板加「编辑分组」（`stagesToText` 回填时连号压缩 1,2,3→1-3），`PUT /api/lessondoc/packs/{id}/stages` 保存并重渲首页；漏配课次由 validate_manifest 兜底进「其他课次」（QA 实测：8 课配 1-7 → 第 8 课自动落入，1 条提醒）。
- **顺手修掉的两个真 bug**：① AI 漏写 `stage.type` 时 validate 把舞台图换成占位卡——现按形状推断（有 body→svg，有 kind/nodes/layers→diagram），单测覆盖；② deck-engine 用 rAF 延迟初始化 stepper 控件，**后台标签页 rAF 不触发**导致控件被吞——改 `setTimeout 0`。

### R4 批量生成的可观测性与韧性 ✅（2026-09-02 完成）
**依据**：32 课顺序生成耗时长（为保证 summary 衔接是有意为之），但目前教师只能看轮询状态，不知道「还要多久」「卡在哪一课」；中途失败要手动补。
**落地**：① 管理面板批量进度条「⏳ 批量生成中：第 N 课编写中 · 已就绪 X/Y · 预计还需约 M 分钟」（按 90s/课估算，`syncBatchProgress` 随轮询刷新，结束自动隐藏）；② `run_lessondoc_batch` 失败课次自动重试一次（QA 实测 mock-AI 全败场景：日志恰好 8 条 "retrying once"，不多不少）；③ 重试后仍失败则保持 `failed` 留给断点续跑（已有「重新生成」按钮即续跑入口），单测 3 条覆盖重试/停止/跳过语义。

**闭环审计补丁（2026-09-02 下午，独立代码评审 + 全链路复盘后）**：
- **卡死回收（真漏洞）**：生成任务是进程内 asyncio 任务，服务重启（每次部署）即消失，但库里 `queued/running` 不会自己变——单课生成被 `already_running` 去重拒绝、批量又只挑 pending/failed，教师无路可走。加 `pack_service.reclaim_stale_lessons`：`updated_at` 超过 15 分钟（单课超时 600s+余量）的 queued/running 判为 `failed` 并附「生成中断(服务重启或超时),请重新生成」提示；在读包详情、单课建任务、批量候选三处入口统一回收。进程无关，不会误杀别处正跑的任务。QA 真机：过期 running → failed+⚠ → 「AI 生成」重新受理 `already_running:false`；新鲜 queued 不受影响。生产库此时尚无任何包，无历史卡死数据。
- **阶段分组重叠**：同一课次配进两个阶段会在首页导图/卡片墙重复渲染 → validate_manifest 先到先得去重并告警（`stages[i] 的课次 [..] 已属于前面的阶段,已去重`）。
- **stages 请求上限**：label ≤60、每阶段 ≤200 课、≤50 阶段（超限 422）。
- **前端轮询防误停**：批量重试瞬间 failed→queued 可能被 5s 轮询撞上而误判空闲，改为连续两次空闲才停；ETA 把 failed（将自动重试）计入。

### R6 内容质量的持续打磨（长期）— 第一轮 ✅（2026-09-02）
**依据**：真实 AI 验收一次通过，但样本只有 1 课 1 门课。
**做法**：多跑几门不同学科（文科/理科/实验课）各 1—2 课，把 AI 常犯的结构问题回灌进 `docs/lessondoc-authoring-guide.md` 的 AI 摘要节——**该文件是提示词真源，改它等于改 AI 行为**。flow 折行就是这么发现并修掉的。
**成本**：持续投入，按需。

**第一轮实测（真 AI，QA 库 pack 2/3/4）**：大学语文《诗经》选读（25 页）、高等数学 函数极限（23 页，238s）、计算机网络实验 Wireshark 三次握手（25 页，116s）——三课**全部一次通过、零校验告警**，结构合规（目标/地图/分节/测验/作业/end 齐全，实验课 badge ★ 且有 tasklist + 报告要求页），测验 13 题题干/答案/解析全部正确。
**浏览器全页溢出审计**（DOM 度量每页内容底 vs 画布底）发现并修掉：
- **真 bug：text+svg 同页时 SVG 满宽缩放后太高顶出画布 170px 被裁**，且 `fitSlide` 溢出保险走 rAF（后台标签页不触发）。修：`slides.css` 给 `figure > svg` 加 `max-height:400px`（先压图高再考虑整体缩放，字号不变）+ `slides.js` 改 `setTimeout`。修后三课 73 页零溢出，保险只轻微触发（0.77–0.92）。
- **AI 共性毛病 → 校验兜底 + 提示词回灌**：① `svg.body` 套完整 `<svg width height viewBox>` 外壳（3 课里 2 课出现）→ validate 剥壳并沿用其 viewBox；② 测验页漏 title（实验课 5 页全空）→ validate 按序补「第 N 题」；③ 例题/推导堆 3–4 个 text 块、作业页用 text 编号列表而非 tasklist、同页有字时 svg 该用宽扁 viewBox → 写进 §5 第 7 条与 §8 AI 摘要节。
- 无害确认：多行 `\n` 文本引擎按换行显示（诗歌原文正常）；长文本只告警不截断。
**下一轮建议**：换 2 门课（如经管案例课、艺术鉴赏课）+ 第 2 课（验证 summary 衔接），看新提示词是否消掉上述三类毛病；数学公式目前只能用行内代码字体写 `lim (x→x₀)`，若教师反映可读性差再评估内置 KaTeX（离线可打包，但会破「零依赖」）。

### R7 可视化编辑器（低代码/无代码）— E0 初步实现、待补强；E1—E6 待施工（2026-09-03）
**真源**：`docs/lessondoc-editor-2026-09.md`。要点：deck 模型严格加法（`frame/style/bg/globals` + `button/codewalk/group/html` 块 + `canvas` 版式），编辑器为平台内三栏页面、以同源 iframe 承载真实引擎渲染，不进离线包；保存走 `validate_deck` + 乐观锁 + 版本快照 + 自动刷新引擎；旧手写包先经 `import-legacy` 转换再编辑。施工分 E0—E6。
**续建依据**：见 [2026-09-03 现状审计与施工方案](lessondoc-editor-audit-and-construction-2026-09-03.md)，先完成 E0-R 的净化、往返一致性、重渲生命周期与数据链补强，再接编辑器保存和 UI。

### 明确不做（YAGNI）
- 不做协作编辑/版本 diff（git 已覆盖）；不做运行时 fetch 拆分 JSON（离线红线）；不做 graphviz 级图布局；不做学生答题持久化（现有测验/作业系统职责）；不动 `_normalize_generated_html_package_nodes` 旧分支；不强迁存量手写包（教师主动点升级才做）。

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| AI 产出 deck 质量不稳（页数失衡/图示乱） | authoring-guide 的少样本案例 + 降级校验兜底 + 单课重写成本低（只重生成一课） |
| 32 课批量生成占用 AI 并发 | background 优先级 + 顺序入队 + 默认只生成前 2 课 |
| 引擎升级破坏旧包 | 包内 assets 锁版；spec 主版本化；「刷新引擎」是显式动作 |
| 教师手改 JSON 写坏 | 引擎友好错误页 + 壳页可回退 git；validate 在下次平台写入时修复 |
| `course_doc_packs` 与材料删除脱钩 | 删除包根材料时（delete-impact 链）级联置 pack `archived`（P1 实现） |
| 双真源漂移（course.json 文件 vs manifest_cache_json） | 写路径唯一（pack_service 统一写文件后刷缓存）；读以文件为准，缓存仅列表页加速 |

## 10. 上线前 checklist

- [x] p02 路由快照重生成（796 条，含 11 条 `/api/lessondoc/*`）
- [x] `RUNTIME_ENSURED_SCHEMA_MODULES` 豁免登记；`_SCHEMA_READY` 有 `reset_schema_ready_for_tests()` 且测试夹具已调用
- [x] 新表无 class_offering_id → MERGE_RULES 守卫单测绿
- [x] 双层绑定镜像（单列+列表）在 bind 路径验证（实测 main.html→home_learning_material_id，课堂侧列表可读）
- [x] `.git` 路径过滤：新增查询均为按 id 精确匹配或 join，不做子树遍历；迁移走 `parse_html_package`（自带过滤）
- [x] island `?v=` bump + 契约测试同步（`lessondoc-20260901`）
- [x] 真 PostgreSQL 本地验证（本地库 ensure 建表 + 真实 AI 生成跑通）
- [x] cnet-course 原样包回归（旧通道零回归，见下）

**旧上传链路回归实测（2026-09-01，干净 QA 环境，旧式手写包）**：zip 自动解压（1→6 文件）✓ / 列表元数据 `is_renderable=True` 且 `lessondoc_pack=None` 不误判 ✓ / 包内浏览 ✓ / 确定性绑定 `binding_mode=html_package` ✓ / 课堂侧读取与包根锚定 `open_url` ✓ / 渲染四通道全 200 ✓ / 目录穿越仍拒绝(400) ✓ / 删除链路（影响预览 2 引用→解除→清理 6 文件）✓。

期间暴露一个**既有缺陷**（非本次引入，该函数自 `f1ac1a93` 起就缺 ensure）：`_load_material_learning_binding_context` 直接查懒建表 `class_offering_learning_materials`，全新环境首次访问 `/api/materials/{id}/learning-bindings` 报 500 → 已补幂等 ensure（commit `397aec4c`）。
