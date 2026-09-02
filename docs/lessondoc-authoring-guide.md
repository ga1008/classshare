# LessonDoc 2.0 编写指南(deck JSON 契约)

> 本文是「课程学习文档包」内容编写的唯一契约,供 AI 生成与人工精修共同遵守。
> 架构与平台集成见 `docs/course-lessondoc-template-2026-09.md`。
> 文末「AI 摘要节」是注入生成提示词的精编版,改本文时必须同步维护。

## 1. 文件形态

每个课次一个 `lesson_N/lesson_N.html`:固定壳(引 `../assets/` 四件套)+ 内嵌
`<script type="application/json" id="lessondoc-data">` 的 deck JSON。**JSON 是唯一真源**,
HTML 其余部分由平台生成、不可手改。首页 `main.html` 同构,内嵌 course.json(manifest)。

## 2. deck 顶层

```jsonc
{
  "spec": "lessondoc/2.0",          // 必填,原样
  "kind": "lesson",                 // lesson | home
  "lesson": 3,                      // 必须等于目录号 lesson_N 的 N
  "course": "《课程名》",
  "title": "课次标题",
  "subtitle": "一句话副标题",
  "badge": "第 3 课 · 理论课",       // 实验课写 "第 N 课 · ★实验课"
  "theme": "sky",                   // 可省;sky/teal/violet/amber/rose/slate,可加 " dark"
  "slides": [ ... ]
}
```

## 3. Slide 版式(7 种)

| layout | 用途 | 字段 |
|---|---|---|
| `title` | 封面,每课第 1 页 | 取顶层 badge/title/subtitle/course,无需其他字段 |
| `section` | 章节分隔 | `no`("01")、`title`、`hint?` |
| `content` | 默认内容页 | `section`(页眉小节名)、`title`、`sub?`、`blocks[]` |
| `two-col` | 左右对照 | + `ratio?`("1:1"/"3:2"/"2:3")、`left[]`、`right[]` |
| `center` | 金句页 | `blocks[]`(通常一个 bigmark) |
| `grid` | 自由网格舞台 | `areas[{area:"r1/c1/r2/c2", blocks[]}]`,12 列×8 行 |
| `end` | 结尾,每课最后 1 页 | `title?`、`summary`、`nextUp`(下节预告) |

所有版式可加 `notes`(教师备注,学生不可见)。

grid 的 `area` 用 CSS grid-area 语法 `"行起/列起/行止/列止"`(1 起,含头不含尾):
`"1/1/9/8"` = 左侧 7 列全高;`"1/8/5/13"` = 右上;`"5/8/9/13"` = 右下。

## 4. 内容块(Block)类型

通用字段:`type`(必填)、`step?`(整数,分步登场序号)、`exitStep?`(到该步退场,少用)、`id?`。

### 排版类
- `text` — `{md}` 短段落;行内只支持 `**粗**` `` `代码` `` `*斜*` 与 `\n` 换行。**≤240 字**。
- `cards` — `{cols:1|2|3|4, items:[{icon?,title,text,tone?,step?}]}` tone: primary(默认)/ok/warn/err。
- `bignum` — `{items:[{value,label,note?}]}` 大数字卡。
- `bigmark` — `{mark:"📦", line:"金句"}` 仅 center 版式。
- `timeline` — `{items:[{title,text,step?}]}` 横向流程条,≤6 站。
- `table` — `{head[], rows[][], rowStep?}` rowStep=true 逐行显现。**≤6 行×5 列**,更大就拆页。
- `callout` — `{tone:"think"|"warn"|"ok"|"err", md}` 提示框。
- `tabs` — `{tabs:[{label, blocks[]}]}` 主要用于首页。
- `details` — `{summary, blocks[]}` 折叠面板(作业要求/扩展阅读)。

### 代码与媒体
- `code` — `{lang?, code, output?}` 代码块(自动带复制按钮)+ 可选输出条。
- `media` — `{kind:"image"|"video"|"audio", src, caption?, poster?}`
  **src 只能是包内相对路径**(如 `media/x.png` 或 `../assets/x.png`),禁止网络 URL。

### 图示类(重难点必配图)
- `diagram` + `kind`:
  - `flow` — `{direction:"h"|"v", nodes:[{id,label,tone?}], edges:[{from,to,label?}], caption?}`
  - `sequence` — `{actors:[{id,label}], messages:[{from,to,label,step?,dashed?}], caption?}`
    消息带 step 即随翻页逐条显现。
  - `arch` — `{layers:[{label, nodes:[{id?,label,tone?}]}], links:[{from,to,label?}], caption?}`
  - `mindmap` — `{root, children:[{label,note?,href?,collapsed?,children?}]}`
- `svg` — `{viewBox:"0 0 640 300", body:"<g>...</g>", caption?, maxWidth?}` 逃生舱,自由手绘。
  **颜色必须用主题变量**:`var(--dg-primary)` `--dg-primary-dark` `--dg-primary-soft`
  `--dg-ok` `--dg-warn` `--dg-err` `--dg-muted` `--dg-line`(连线灰) `--dg-fill`(节点底)
  `--dg-text`。禁止十六进制色、禁止 `<script>`/事件属性(会被剥除)。
  SVG 内元素可加 `class="fragment" data-step="2"` 分幕。

### 交互类
- `quiz` — `{q, options:[{k:"A",text}], answer:"B", explain}` 一题一页,点选即判分。
- `tasklist` — `{items:["文字" 或 {text,step?}]}` 实验清单,真勾选框。
- `reveal` — `{items:[{label:"问题", md:"答案"}]}` 点击揭示。
- `stepper` — 步骤演示(算法/协议过程):
  ```jsonc
  { "type":"stepper",
    "stage": { "type":"svg", "viewBox":"0 0 640 150", "body":"...静态舞台,动元素挂 id..." },
    "steps": [
      { "text":"解说词",
        "show":["#pkt"], "hide":[],
        "set":[{"target":"#pkt","attr":"transform","value":"translate(105,0)"},
               {"target":"#note","attr":"textContent","value":"状态说明"}] }
    ] }
  ```
  每步 = 对舞台内 `#id` 元素的属性操作集(show/hide/set);`attr:"textContent"` 改文字。

## 5. 编排守则(硬规则)

1. 一课 **18—25 页**。结构:封面 → 学习目标(cards 四张带①②③④) → 本课地图(timeline)
   → [章节分隔 + 内容页]×3~6 → 随堂测验(每题一页,4~6 题,首题 sub="点击选项即时判分")
   → 课后作业/实验报告页 → end(含 nextUp)。
2. **一页一个概念**;能画图不写字,能分步不整放;超过 4 行的说明改图/表/折叠。
3. 重难点必配 `diagram` 或 `svg`;figure 必有 caption。
4. 作业/考试提交只写「在 lanshare 平台完成提交」,禁止出现其他提交渠道;
   禁止出现具体班级/学生/学校信息。
5. 学科适配靠块型组合,不改结构:
   - **文科**:timeline(史实线索)、cards(观点对比)、reveal(设问→揭示)、quiz、table(流派对比)
   - **理科**:svg(推导示意)、stepper(算法/证明步骤)、code(计算示例)、bignum(关键常数)
   - **工科**:arch(系统分层)、sequence(协议交互)、flow(工艺流程)、tasklist(实验步骤)、code+output
6. 实验课(badge 带★):必有 tasklist 页 + 实验报告要求页(details 或 cards)。
7. **R6 三科实测回灌(2026-09-02,文科/理科/实验课各 1 课真 AI 生成后归纳)**:
   - `svg.body` 只写 `<svg>` 里面的内容,**不要再套 `<svg …>` 外壳**、不要 width/height、不要整幅背景 rect(校验会剥壳,但别依赖它)。
   - 同页有文字时 svg 用**宽扁 viewBox(宽:高 ≥ 2:1)**,否则满宽缩放后图太高顶出画布。
   - 例题/推导/证明用 `stepper` 或 `cards(step)`,**禁止连续堆 3 个以上 text 块**当步骤用。
   - 测验页 **title 必填**(如「第 2 题 · 手法判断」),空标题页面顶部会空一块。
   - 作业页用 `tasklist`(每项一条可勾任务),不要用 text 写编号列表。
   - 多行文本(诗歌原文/分步说明)可以在 `md` 里用 `\n`,引擎按换行显示。

## 6. 三科示范页

文科(历史课·观点对比页):
```jsonc
{ "layout":"content", "section":"史学争鸣", "title":"洋务运动:成还是败?",
  "blocks":[
    { "type":"cards", "cols":2, "items":[
      { "icon":"👍", "title":"近代化起点说", "text":"引入机器生产与新式教育,开启工业化。", "tone":"ok", "step":1 },
      { "icon":"👎", "title":"失败改良说", "text":"只学器物不改制度,甲午一役见分晓。", "tone":"err", "step":2 } ] },
    { "type":"reveal", "items":[
      { "label":"你更支持哪种?先想 30 秒再点开", "md":"两说不矛盾:**经济史**维度看是起点,**政治史**维度看是失败。史学结论取决于提问维度。" } ], "step":3 } ] }
```

理科(数学课·推导步骤页):
```jsonc
{ "layout":"content", "section":"极限定义", "title":"ε-δ 语言:一步步逼近",
  "blocks":[
    { "type":"stepper",
      "stage": { "type":"svg", "viewBox":"0 0 640 200",
        "body":"<g><line x1='40' y1='170' x2='600' y2='170' stroke='var(--dg-line)' stroke-width='2'/><path d='M 60 150 Q 320 20 580 150' fill='none' stroke='var(--dg-primary)' stroke-width='2.5'/><g id='band' visibility='hidden'><rect x='40' y='60' width='560' height='40' fill='var(--dg-primary-soft)' opacity='0.6'/></g><text id='note' x='320' y='195' text-anchor='middle' font-size='13' fill='var(--dg-primary-dark)'></text></g>" },
      "steps":[
        { "text":"任给一个误差带 ε,画出 L±ε 的横带。", "show":["#band"],
          "set":[{"target":"#note","attr":"textContent","value":"横带宽 2ε,ε 可以任意小"}] },
        { "text":"总能找到 δ,使 |x-a|<δ 时函数值都落在带内。",
          "set":[{"target":"#band","attr":"opacity","value":"1"},
                 {"target":"#note","attr":"textContent","value":"这就是「要多近有多近」的严格表述"}] } ] } ] }
```

工科(软件课·系统分层页):
```jsonc
{ "layout":"content", "section":"系统架构", "title":"Web 应用的三层结构",
  "blocks":[
    { "type":"diagram", "kind":"arch", "caption":"每层只和相邻层说话",
      "layers":[
        { "label":"表现层", "nodes":[{"label":"浏览器"},{"label":"小程序"}] },
        { "label":"业务层", "nodes":[{"id":"api","label":"API 服务","tone":"warn"}] },
        { "label":"数据层", "nodes":[{"id":"db","label":"数据库","tone":"ok"},{"label":"缓存"}] } ],
      "links":[{"from":"浏览器","to":"api","label":"HTTP"},{"from":"api","to":"db","label":"SQL"}] } ] }
```

## 7. course.json(manifest)要点

见设计文档 §3.4。生成课次后必须回填该课 `lessons[n].summary`(≤120 字),
并保持 `stages` 与 `lessons` 一致;`status` 只在文件真实存在时才可为 `ready`。

---

## 8. AI 摘要节(注入生成提示词的精编版)

<!-- AI-SUMMARY-BEGIN -->
你在为「课程学习文档包」编写一个课次的 deck JSON(spec=lessondoc/2.0)。只输出一个 JSON 对象,不要任何解释或 markdown 围栏。

顶层:{"spec":"lessondoc/2.0","kind":"lesson","lesson":N,"course":"《课程名》","title":..,"subtitle":..,"badge":"第 N 课 · 理论课|★实验课","slides":[..]}

版式 layout:title(封面,第1页,无需字段) | section(章节页:no/title/hint) | content(默认:section/title/sub?/blocks) | two-col(+ratio "3:2"等/left/right) | center(blocks,放 bigmark) | grid(areas:[{area:"行起/列起/行止/列止"(12列×8行),blocks}]) | end(最后1页:summary/nextUp)。

块类型(通用字段 type/step?):
text{md,≤240字,只许 **粗** `码` *斜*} cards{cols,items:[{icon?,title,text,tone:primary|ok|warn|err,step?}]} bignum{items:[{value,label,note?}]} bigmark{mark,line} timeline{items≤6:[{title,text}]} table{head,rows≤6行,rowStep?} callout{tone:think|warn|ok|err,md} tabs{tabs:[{label,blocks}]} details{summary,blocks} code{code,output?} media{kind,src仅包内相对路径,caption} quiz{q,options:[{k,text}],answer,explain} tasklist{items} reveal{items:[{label,md}]}
diagram{kind:flow{direction,nodes:[{id,label,tone?}],edges:[{from,to,label?}]} | sequence{actors:[{id,label}],messages:[{from,to,label,step?,dashed?}]} | arch{layers:[{label,nodes}],links} | mindmap{root,children:[{label,note?,href?,children?}]},caption}
svg{viewBox,body,caption} 自由手绘,颜色只许 var(--dg-primary/--dg-primary-dark/--dg-primary-soft/--dg-ok/--dg-warn/--dg-err/--dg-muted/--dg-line/--dg-fill/--dg-text),禁 script/事件属性,元素可 class="fragment" data-step 分幕
stepper{stage:svg块(动元素挂id),steps:[{text,show?,hide?,set:[{target:"#id",attr,value}]}]} attr:"textContent" 改文字。

硬规则:一课18—25页;结构=封面→学习目标(cards×4,①②③④,逐step)→本课地图(timeline)→[section分隔+内容页]×3~6→随堂测验(每题一页,4~6题,每页title如"第2题 · 考点",首题sub="点击选项即时判分")→课后作业页(tasklist,sub含"在 lanshare 平台完成提交")→end(summary+nextUp)。一页一概念;重难点必配 diagram/svg 且带caption;大表拆页;多用step分步;实验课必有tasklist页。禁止:其他提交渠道、具体班级/学生/学校信息、网络图片URL、十六进制颜色。
版式细则:svg.body 只写 <svg> 内部元素(不套 <svg> 外壳、不写 width/height、不画整幅背景);同页有文字时 svg viewBox 取宽扁比例(宽:高≥2:1);例题/推导/证明用 stepper 或 cards(step),不要连续堆 3 个以上 text 块;多行文本在 md 里用 \n 换行。
学科手法:文科重timeline/cards对比/reveal设问;理科重svg推导/stepper步骤/code;工科重arch/sequence/flow/tasklist/code+output。
<!-- AI-SUMMARY-END -->
