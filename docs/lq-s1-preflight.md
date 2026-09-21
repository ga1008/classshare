# S1 实施前审计（只读业务代码）

审计日期：2026-09-20。依据最新 `docs/liquid-glass-execution-plan-2026-09.md` §8、§9、§15.1–15.2、§16 S1 出口条件，以及当前偏好服务、模板、CSS 和迁移代码。此报告仅准备 S1，不改变 S0 或业务实现。

## 1. 必须先纠偏的硬冲突

### 1.1 五等级不能统一要求白字-on-base ≥4.5

用计划给出的精确 HSL 值转 sRGB，按相对亮度计算；soft 先与对应不透明 surface-1 进行 alpha 合成。结果在 `lq-s1-contrast-preflight.json`。

| 等级 | 亮白字/base | 暗白字/base | 亮 fg/soft | 暗 fg/soft |
|---|---:|---:|---:|---:|
| success | 3.2715 | 2.0652 | 5.3235 | 8.0795 |
| warning | 2.3817 | 1.7738 | 5.1398 | 8.3520 |
| danger | 4.2575 | 2.8389 | 6.1925 | 7.2732 |
| info | 4.5549 | 2.6176 | 6.7537 | 7.4531 |
| neutral | 4.2940 | 2.7961 | 6.9858 | 8.0445 |

计划 §8.14 的 fg-on-surface-1 和 fg-on-soft 亮暗全部达到 4.5，问题集中在“白字-on-base”。建议将 dot/icon 的 base 与实底文字配对分开：每个等级必须显式给 `on-base`，并按实际 `on-base/base` 验收；保持现有 base 色相与 chip soft/fg 设计。亮 warning/success 可配固定深墨色 `222 47% 11%`（分别 7.5153/5.4713）；亮 danger/neutral 对这档墨色仍仅 4.2042/4.1684，需要更深固定前景或调低 base 亮度；不能用“白或 --ls-ink”一句话代替测量。也可给实底按钮独立 `solid`/`on-solid` 色对。暗五等级固定深墨色均达标。

六套亮 primary 的白字都达标；计划暗 primary 配白字全部失败（1.91–3.63），但固定 `222 47% 11%` 均达标（4.94–9.37）。**暗模式的 `--ls-ink` 是浅色，不能把 `on-primary: var(--ls-ink)` 当作固定深色前景**。显式引入 `--ls-on-primary` HSL 通道，亮为白、暗为固定深墨色；旧 `--ls-primary-foreground` 别名指向它。

### 1.2 亮 ink-3 在页面底不够清楚；暗值没有同样问题

`215 16% 47%` 对 light surface-0/1/2 分别 4.3590/4.7181/4.5053。计划允许它用于次级正文，但 surface-0 上小字失败。建议 light 改 `215 16% 45%`，三面分别 4.6809/5.0665/4.8380；对每套配色各自覆盖后的页面底再测。dark `215 16% 58%` 对三个计划面分别 5.8571/5.3460/4.7787，现值可保留。玻璃、图片、渐变上不能根据这些不透明面比值推断通过。

### 1.3 现有玻璃令牌与新 HSL 规范发生类型冲突

`static/css/ui-system.src.css:62089–62092` 定义 `--ls-glass-line` 为 rgba 完整颜色，`--ls-glass-ink/muted` 为 hex 完整颜色；消费者在 62098、62124、62191、62195、62199、62246、62258、62273 直接 `var(...)`。§8.2 用相同名字改成 HSL 通道后，旧 border 声明计算失效、旧 color 退回继承。

本地 Chrome fixture 已复现：`border:1px solid var(--ls-glass-line)` 在通道值下变 `border-style:none`；`color:var(--ls-glass-ink)` 变父色。S1 必须同批把这些有限消费者改成 `hsl(var(...))`，再移除重复旧定义；或者新通道另起明确名字并给完整颜色桥接，不能同名改变类型却延后消费者到 S7。`--ls-glass-shadow` 本来是完整 shadow，不要误包 hsl。

### 1.4 soft 不是普通的三个 HSL 通道；接口必须固定

`--ls-primary-soft:var(--ls-primary) / .12` 含 alpha，合法消费是 `hsl(var(--ls-primary-soft))`。若套用现有 Tailwind 颜色模式 `hsl(var(--token) / <alpha-value>)` 就得到双斜线非法色；Chrome `CSS.supports` 已证实。

建议契约：普通色 = H S L；`*-soft`、`*-fill`、`*-rim`、scrim = H S L / alpha；消费者各只有一种包装方式。Tailwind soft 映射直接 `hsl(var(--ls-primary-soft))`，禁用该令牌的 opacity modifier；需要覆盖透明度时从普通色加独立 alpha 变量生成。`lq-tokens.json` 必须标注 `hsl-channels` / `hsl-alpha-channels` / `color` / `shadow` / `length`，不能统一按颜色字符串解析。

### 1.5 色相“保证不撞”与真实沿用值不一致

`static/css/user_ui_preferences.css` 实际是 sky 205、mint 172、violet 274、rose 341；§8.14 的说明用了 199/≈160/262/347。所以 info 212 与 sky 205 仅差 7°，success 152 与 mint 172 差 20°、与 teal 175 差 23°，不能宣称“最少 13°，其余≥25°”。建议保留用户已选配色，修正验收表述为文字+dot+形态可辨，再以真实六色截图验收；若要改语义色相，必须重新计算全部色对。不要为满足纸面距离静默改变旧用户配色。

同一计划还有不一致：§8.11 暗 teacher=173 70% 55%，§8.14=173 62% 52%；§8.1 的 success soft .14/info .14 与 §8.14 .13 不同。以较后“定稿值”§8.14 为唯一输入表，其他段改为引用，禁止在两份 CSS 分别编码。

### 1.6 全站主题属性不等于全站生效；后代局部值会截断继承

Chrome 直接加载当前构建 CSS + 当前 preference CSS，并给 body 设置 `data-ui-palette=sky` 的实测：

- `body.dashboard-page.role-teacher` 的 primary 仍为 `175 77% 26%`；来源 `ui-system.src.css:25210`。
- 普通 body primary 为 sky，但 `.manage-layout` 的 primary 仍 175；来源 52316。
- `.manage-layout[data-manage-domain=library]` domain-accent 仍 243；来源 52328；archive/academic/me/admin 同理。
- `.app-topbar.role-teacher` 仍取 `--ls-teal`；来源 335。
- classroom teacher / profile teacher 用 success 作为主色，分别见 15816/37079。

因此 S1 不应把角色配色纠偏延后到页面整体迁移。需要先把**角色主色与域主色的有限覆盖**改为 primary 的兼容别名，保留真正的课程/状态/笔迹色。上面的文件块由 token owner 同时处理；不要全局正则替换所有绿色。

另有变量计算时机陷阱：`:root {--primary-color:hsl(var(--ls-primary))}` 的完整颜色在根节点已计算。只在 body 改 `--ls-primary`，继承的 `--primary-color` 不会重新绑定。fixture 中直接 HSL 是 sky `rgb(23,103,161)`，旧 alias 仍 indigo `rgb(80,72,229)`。现有 `user_ui_preferences.css:20` 起已通过在 palette scope 重定义别名规避；合并时不可丢。

建议以 documentElement 为主题令牌唯一主作用域，SSR 同时给 html 挂 palette（供 CSS）和 body 保留现有 palette/version/context（兼容消费者）；runtime 原子同步两处。旧 alias 在根和显式局部 scope 定义处重新绑定。不要让 html dark + body palette 的亮色规则无条件覆盖 surface/foreground。也不要将所有 `[data-theme]` 当主题域：成长页用相同属性表示修炼阶段。

### 1.7 Raw lq CSS、Tailwind @layer 与旧无层样式必须区分

当前 build:css 是 Tailwind 3.4.17 CLI，`ui-system.src.css:1–3` 是 @tailwind；产物中的 Tailwind @layer 已展开为普通规则。§15.2 的单入口应保留：在 @tailwind 前 import lq/index，通过同一 CLI 展开。不可把带原生 `@layer base/components` 的 lq 文件额外 raw link，期待“后加载覆盖”；浏览器普通无层旧规则优先于所有普通有层规则，fixture 已证实。

即使经 CLI 展开，旧 `:root[data-theme=lanshare]` 比新单个 `:root` 更具体，后面的旧 root 又可重新写玻璃变量；应按真源计划移走重复定义。当前实际还有 50774 的 --ux-motion root、52900 的 --ls-c root、62086 的玻璃 root，不只是纸面“三处”。按 token 名/消费者盘点，不按旧行号截取。新 `.lq-*` 应专属命名并与旧类双挂探针做 computed 验收；仅改变 import 顺序不能隔离 `.card/.btn/.modal`。

### 1.8 glass=off 全站出口须有过渡期强制层

大量旧 backdrop-filter 是字面量，`static/js/course_schedule_deck.js:255` 等在运行时注入 CSS，光把 --ls-glass-blur 置 0 不会关闭。S1 出口要求全站 none，故 materials.css 需要明确兼容关闭规则，涵盖根/后代/::before/::after/原生 dialog::backdrop、webkit 属性、局部 off scope。应在 cascade 上压过旧字面量（受限的 accessibility/off !important 合理），并在动态课表/旧灯箱/弹层实际打开后测 computed。只关 lq 类不能声称全站通过。

`glassPreference` 与 `resolvedGlass` 分开：账户 tinted 在 tier C/reduced-transparency 强制 off，能力恢复时仍可恢复 tinted；这些事件不能 PATCH。forced-colors 单独去阴影/滤镜并用 Canvas/CanvasText。保留白板内容与图片颜色。

§8.12 弱设备 regular=12px 与 §8.2“四档 lint 禁止其他值（8/16/24/8）”冲突。建议弱设备使用已有 thin=8、regular=16 作为降档，或者明确定义低端 token 值 12 为受控变体并让 lint 识别令牌定义；不能一面生成 12，一面守卫拒绝它。

### 1.9 “旧页截图仅色值变化”需明确 S1 边界

S1 同时切字体、别名圆角、字号/间距会改变字宽、断行与元素尺寸；这与字面“diff 仅色值”矛盾。建议 S1 对旧字号/间距/圆角别名保持原数值，把新规格映射只用于 lq 组件；字体变更单列允许的、逐页校验的变化。旧页存在大量白底/深色文字字面量（如 `ui-system.src.css:198–208` card 白底、`37069–37076` profile 渐变、独立编辑器内联 CSS），仅翻转根 ink 会造成白卡浅字。S1 必须对涉及全站暗色的保留旧表面提供明确兼容映射并做既定截图；若这批未完成，不得把“属性已全站输出”记为“全站暗色视觉已验收”。不建议通过全页 filter invert 规避。

## 2. 可直接实施的偏好 API 与数据库接口

现状：service `user_ui_preferences_service.py:33` 仅接受 student，`:105` 有学习路由过滤；router `user_ui_preferences.py:29` palette 必填且仅 Depends(get_current_student)。表 `schema_user_ui_preferences.py:9` 的 `(user_role,user_pk)` 已能隔离 teacher/student，palette 是普通 TEXT **没有 CHECK 色表**；version ≥1，无行版本为 0。schema 在 SQLite/PG 启动路径已有调用（schema.py:64/366），请求读取不建表。

建议继续保持单行单 version CAS，S1 不引入字段版本表：

```json
PATCH /api/profile/ui-preferences
X-UI-Preferences-Context: <现有身份 HMAC>
{"version": 7, "appearance": "dark"}
```

- 三个偏好字段可以省略，**version 仍必填 StrictInt**；未知字段拒绝；至少一个偏好字段；显式 null 拒绝（DB NULL 的默认兼容语义不等于 API 重置语义）。复位外观/玻璃分别发送 auto/tinted，palette 发该角色默认 key。
- 使用 `model_fields_set` 或 `model_dump(exclude_unset=True)` 提取变更字段，白名单构造 UPDATE。禁止将 Pydantic 默认 None 写回没提交的字段。无行 INSERT 时明确填 teacher teal/student indigo 与提交字段，不能靠旧 SQL DEFAULT indigo，否则教师第一次只改 appearance 会被写成 indigo。
- `get_current_preference_user` 经 `get_current_user` 验证有效登录身份，再限制 student/teacher；不采用 optional 绕过停用/无效身份验证。超管仍是 teacher 身份，无需新增 admin namespace。
- `(role,id)`、HMAC v1 上下文、private no-store、identity_changed 与 version_conflict 码保持。相同数值 teacher/student ID 不串写。业务 profile 更新不触及偏好列。
- GET 统一返回 resolved defaults 的 `{palette_key,appearance,glass,version,updated_at,context_token}`；DB NULL/未知 appearance/glass 安全落 auto/tinted，未知 palette 落角色默认。GET/SSR 不插默认行，不更新 version。
- `update_ui_preferences(conn,user,*,changes,version)` 采用一次 `UPDATE ... WHERE role/id/version RETURNING version`；version0 INSERT ON CONFLICT DO NOTHING 仍保留。确认 version 命中前不要取新版本再无条件重试。
- **字段级更新≠字段级 CAS**。版本7下 A 改 appearance 成8，B 只改 palette 仍用7，应409且不覆写。这符合保守的现有 CAS；不同字段“允许自动合并”不是计划明文要求。客户端即使 GET 到新版，也不能顺手把整份 desired 发回。
- 同字段409后保留本地预览、显示服务器现值并等待用户再次明确选择；改另一个字段不代表确认覆写旧冲突字段。三字段 controller 要记录 per-field dirty/intent/conflict，不能用现有单字符串 dirty 直接替换为全对象。
- 旧客户端仍发送 palette+version，服务端只改 palette，其余列保留；旧客户端遇新 version 仍按原逻辑409/明确重选。不要为了“兼容旧客户端”跳过 version 校验。
- 丢响应后明确重试先 GET；如果期望值已提交可清 dirty，不重复增加版本；如果同字段被其他设备改动，保留冲突而不是自动用 GET 的 version 写旧预览。现有单字段测试把显式 retry 视作覆盖确认，S1 应将文案/行为保持明确。

迁移：新增 `appearance TEXT NULL`、`glass TEXT NULL`；旧表增列与新建表都覆盖。SQLite PRAGMA table_info 判缺再 ALTER，PG ADD COLUMN IF NOT EXISTS；可参考 `schema_academic_evaluations.py:14–34` 的现有模式，不在请求路径迁移。`postgres_required_columns.py:2340` 同步新列，否则发布 schema 校验漏检。旧版本服务只更新 palette/version，不会清新列；回退无需删列。旧服务不认识 teal 时读侧会回落 indigo，这属于回退显示降级，数据列保留。

## 3. 首屏/运行时建议接口

1. `resolve_user_ui_preferences` 去路由/角色限制但保留 request.state 缓存；缓存最好带身份 tuple，避免同请求不同主体误复用。服务不可用仍返回角色默认 + available=false + 当前身份 context，不阻断业务页；第一次显式改偏好前先恢复 GET。
2. `partials/lq_theme_attrs.html` 提供明确 root/body 两个输出入口：root 输出 appearance-preference、初始 appearance、glass-preference、resolved glass、palette；body 保留现有 palette/version/context/available。不要一个字符串重复拼全部属性。
3. `partials/lq_theme_bootstrap.html` 只读 SSR 属性，同步置 resolved appearance/tier/glass + `color-scheme`；放任何 CSS link 前，不 fetch、不 localStorage 搬运前账号状态。用有限 try/catch 和保守 fallback；CSS/JS 能力分别检测，不因一种不支持整个脚本抛错。
4. 普通 module `static/js/lq/theme.js` 负责 matchMedia 监听、同源 iframe 初始化/主题事件，必须在无控件页面也运行。现 `user_ui_preferences.js:104` 没有 palette select 就 return，不能将主题监听写在它之后。init/dispose 幂等。
5. 现有 `createPaletteController` 可保留小兼容适配器；新增 `createUIPreferencesController` 的 desired/confirmed 是三字段，pending/conflict 是字段集合。`select(field,value)` 仅标该字段；串行提交本次字段快照；请求在途收到的新 intent 不能被旧响应重绘。身份变化禁用三个控件并取消全部待写请求。
6. `applyTheme({preferences,capabilities}, root)` 本地纯映射，不落库；实际用户选项才进入 controller PATCH。旧 `lanshare:ui-palette-change` 事件继续发以兼容消费者，新增统一 `lq:theme-change` 带 resolved 状态；不能用媒体变化触发保存。
7. 三文档根 base/manage/resume 与五独立文档 exam_editor/exam_take/lesson_plan_editor/assessment_plan_editor/teacher_evaluation_editor 都通过同一 SSR+bootstrap；LessonDoc 继承 base 已覆盖。iframe 不从父窗口拿身份 token：只传展示状态，校验 origin 与 source；其自身保存依然走自己的 SSR 身份。嵌入成员工作区、Portal、原生 dialog 样式继承分别验证。

**iframe边界补充**：不能对所有同源iframe直接写主题。成员统计、课堂配置抽屉、审批中的批改详情是应用界面，可显式登记主题桥接；教案/考核/评学打印预览、LessonDoc画布、材料HTML/PDF、签到证据、已生成简历、签名文档则是用户内容或产物，保持内容颜色/画布，不因宿主暗色重解释。应用iframe用显式标记/握手及origin+source验证，只传显示字段，绝不传context_token、version或账户数据。

**统一属性**：继续使用计划的 `data-appearance`（解析后的light/dark）和 `data-appearance-preference`，不要另建竞争的 `data-lq-appearance`。`html`也输出 `data-ui-palette`；`body`保留旧palette/version/context/available钩子。glass保存意图与设备解析后的 `data-lq-glass` 必须分开存放，避免把能力回退写成用户选择。

**构建边界补充**：Docker前端依赖镜像是固定Node基础镜像，没有Python；不要把 `export_tokens.py` 无条件加入 `npm run build` 后破坏无网络镜像构建。令牌真源仍为CSS、导出JSON为生成物；导出器可作为显式开发/验收命令，常规Tailwind构建不依赖该JSON和Python。若需要构建内一致性门禁，应使用已有Node/PostCSS能力并由主集成统一调整复制清单，不新增下载或第二套颜色定义。

## 4. 文件分工与施工依赖（避免共享文件互踩）

| 工作包 | 独占文件 | 交付/依赖 |
|---|---|---|
| A：令牌/兼容材质 | `static/css/lq/{index,tokens,base,materials}.css`、`ui-system.src.css`、`tailwind.config.js`、`user_ui_preferences.css` 合并、`tools/ui/export_tokens.py` | 先给 typed token 表/配对值；处理旧 glass 类型与角色局部覆盖；只动必要旧消费，不重排业务页面 |
| B：偏好后端/迁移 | `schema_user_ui_preferences.py`、`postgres_required_columns.py`、`services/user_ui_preferences_service.py`、`routers/user_ui_preferences.py`、`dependencies.py`、`tests/test_user_ui_preferences.py` | 首先锁定 API 默认/null/版本/冲突契约，再实现 SQLite+PG 增量迁移；不改共享模板 |
| C：SSR/首屏/前端同步 | 三个布局根、五独立文档 head、`partials/lq_theme_attrs.html`/bootstrap/editor_head、`static/js/lq/theme.js`、`static/js/user_ui_preferences.js`、偏好宏与 frontend controller 单测 | 依赖 A token 命名和 B 响应结构；保留老 palette DOM 钩子；profile 外观区全布局重构仍按 S4，但 S1 可用现有控件和预览页验证三项接口 |
| 主集成/验收 | `/dev/lq`、tokens JSON/报告、lq 台账、组件/E2E fixture、构建产物 | 统一组织一次构建；六配色×亮暗×语义配对探针；核实 build CSS 没 raw @import；业务合成账号验证 |

关键依赖顺序：先确定色对与类型 → B API/迁移与 A tokens 并行 → C root/监听/字段同步 → 主集成构建 + computed/权限/并发/首屏 → 再截图闭环。`ui-system.src.css`、共享 layout、package/build 产物必须单 owner。

## 5. 必要验收（不是只测令牌存在）

- 颜色静态：每套亮暗 foreground/surface0/1/2、on-primary/primary、五等级 fg/soft、on-base/base 全部计算；填明 soft 合成基底；图/渐变报告 unmeasured 并转截图像素探针。亮/暗只测 CSS 变量字符串不能算对比通过。
- 浏览器 CSS：旧 alias 与新 primary 一致；教师切非 teal 的 dashboard/manage/topbar/profile/classroom 真正改变；域色跟 primary；旧 ls-glass/lightbox 文字和 border computed 有效；双 alpha 非法值为零；Tailwind 与 raw 文件 probe；Portal/dialog/iframe 不丢主题。
- 材质：四类正常/reduced-transparency/forced-colors/tier C/off；打开旧弹层、图片灯箱、课表放大等动态宿主后全站 backdrop-filter 与 webkit 实际 none；局部 off 不被父 tinted/后注入样式恢复。
- 首屏：八文档 SSR 输出正确；在阻断普通 module/关闭 JS 下 SSR 明确 light/dark 仍合理；auto 在首次 requestAnimationFrame 前就解析系统 dark，后续媒体改变更新但 PATCH=0；无 palette 控件页面监听仍工作；设备未知/缺API/JS异常不白屏。系统 light↔dark、reduce transparency 改变、tier 回退不变更账户 version。
- SQLite：旧模式表带旧数据增列，幂等第二遍，无行 teacher 仅 appearance 首写得到 teal；旧客户端只 palette 保留 appearance/glass；null/未知enum/空变更/unknown字段/bool/stringversion 拒绝；GET query_only 不写。
- PG：真实临时原生 PG 上做新建/旧表迁移两次、并发首次插入一胜一409、同版本更新一胜一409、提交原子性；验证 required-columns 和回退旧 SQL 后新列保留。SQLite测试不能代替PG证据。
- 身份：匿名401、teacher/student各200且相同id隔离、超管按teacher、停用身份拒绝、旧tab换账号HMAC409且无任何列被改；SSR异常只显示默认，不写覆盖。
- Controller：快速跨字段改变合并串行、在途新版不被旧响应重绘、只重发仍dirty字段、409不自动覆盖、另一字段选择不顺带确认冲突字段、响应丢失后GET恢复、同字段远端改变、identity_changed停止队列、未保存错误常显到明确解决。
- 回归：现 `tests/test_user_ui_preferences.py` 中教师403/只学生学习页/5个选项断言同步到新契约；`frontend/src/lib/user-ui-preferences.test.ts` 原串行/身份/失败测试保留；`home-classroom-ui-v3.spec.ts` 增加跨所有壳范围，保留旧账号标签不串写。采用现合成库，不复制或写真实教学数据。

## 6. 本次只读审计证据与限制

2026-09-20 补充精确契约：`.codex-temp/lq-s1-token-contract.json` / `.md` / `.py` 已按现有源码逐项生成；现有 **126个 `--ls-*` 已包含86个 `--ls-c-*`**，不是212个。六配色亮暗、语义五等级及悬停共792项数值配对通过（最低4.554903:1），759个别名节点无缺失引用或循环。保留既定base/fg/soft，补on-base及实底solid/on-solid；列明8处旧glass消费、54条角色/域覆盖，以及课堂按钮硬编码白字的有限消费修正。这些仍是设计输入，不能替代实际旧页暗色与图像背景验收。

后端最终只读复核见 `.codex-temp/lq-s1-backend-contract-review.md`。额外约束：HTTP测试不能只override `get_current_user`后宣称覆盖停用身份，须补真实根依赖校验；`/dev/lq` 当前optional角色检查在S1全站偏好解析前须校验有效身份；同一个偏好测试文件同时含后端、旧CSS和SSR断言，后端与令牌/模板实施必须协调更新，不能各自把局部通过称为整文件通过。

已执行：

```powershell
venv/Scripts/python.exe .codex-temp/lq-s1-contrast-preflight.py
node .codex-temp/lq-s1-css-preflight.cjs
```

产物：`.codex-temp/lq-s1-contrast-preflight.json`（数值）、`.codex-temp/lq-s1-css-preflight.json`（Chrome computed/语法/当前 CSS 角色覆盖复现）。脚本只生成临时报告/页面，不写业务、数据库、构建产物。不依赖运行中的合成业务服务，不影响其登录态。

未执行：S1 新实现测试、真实 PG 新迁移、全站暗色截图、登录图片像素探针、移动真机。因为本次任务是实施前审计，这些均为后续出口条件，不记为已通过。
