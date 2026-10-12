# 博客中心闭环改进计划（2026-10-12）

> 状态（2026-10-12）：第 11 节待决事项已按推荐确认。**P0–P4 已发布**（P0 `c113b74b`，P1 `cadcd263`，P2 `8f083307`，P3 `04ad5f49` release `20261012-201621-2f01d431bdfc`，P4 `1fb52483` release `20261012-213055-9d68ecbcbe05`）；**P5 实施中**；P6–P7 未动工。
> 范围：博客中心（`/blog`）全部前后端，以及它与消息中心、个人中心、头像服务、AI 网关、调度器、作品集、Agent 平台的接口。
> 前置阅读：[液态玻璃组件开发合同](lq-component-contract-2026-09-27.md)、[组件 API](lq-components.md)、[场景前景色契约](lq-scene-ink-2026-09-26.md)、[AI 与 Agent 工程规范](ai-and-agent-engineering-standard.md)、[通知邮件降噪](notification-email-policy-2026-09-18.md)、[博客重设计（2026-08）](blog-redesign-2026-08.md)。
> 证据来源：2026-10-12 的两路代码审查（后端、前端）、三路专题调查（身份/头像、审核/通知、创作中心/回复），以及隔离 P03 运行环境中以学生和教师身份的实测。文中 `file:line` 均指当日 `dev`（`dbbf1e57`）。

---

## 0. 一页摘要

博客中心的单点功能大多存在，但几乎每条业务线都**没有闭合**：

| 业务线 | 现在断在哪里 | 闭合后的样子 |
|---|---|---|
| 头像 | 匿名帖、管家帖显示**看帖人自己的头像**；帖子头像是写入快照、评论是实时查询，两处不一致；默认头像 SVG 用真名首字，任何人可按 user_id 枚举 | 服务端统一解析“作者面孔”（persona），四种头像形态（图片 / 首字 / 匿名 / 管家），所有场景同源同形 |
| 匿名/真名 | 只有帖子能匿名；匿名作者在自己帖下评论会**露出真名、头像和身份**；@管家 把匿名作者的个人画像送进 AI 再公开回复；昵称模式实际不隐藏任何东西；教师审核时无合规途径查看匿名作者 | 帖子与评论都有身份模式；楼主自动显示为“楼主”；匿名同学在同一帖内有稳定代号；昵称改为真正隔离的“笔名”；教师揭示真实作者须填理由并留痕 |
| 内容安全 | **没有任何自动审核**；作者能自己把教师隐藏的帖子改回公开；隐藏原因永远为空；举报处理完内容不变、双方都不知道结果；审核日志只写不读 | 生命周期与审核态分离的状态机：本地规则 → AI 复核 → 可见/限制/移除；每次处置都有原因、通知、日志、申诉 |
| 通知 | 只有 4 种通知；点赞、@某人、关注、被隐藏、举报结果都不通知；加精通知可重复、会发给自己；不能定位到具体评论；读了帖子通知仍未读 | 完整事件目录：聚合、去重、匿名掩码、精确深链、读帖即销、可按类静音 |
| 个人创作中心 | “我的”只是带状态筛选的卡片列表；阅读数据采了从不展示；没有粉丝、收到的评论、关注管理；编辑 409 冲突会死循环；编辑已发布帖点“保存草稿”会**静默撤下** | 创作中心：概览数据、作品管理（行内操作 + 审核状态 + 申诉）、草稿箱、互动收件箱、粉丝与关注 |
| 回复与消息中心 | 无限缩进；不显示“回复 @X”；硬删除整棵子树；一级评论超过 100 条静默截断；消息中心只能跳转不能回复 | 两级楼中楼、软删除占位、精确深链高亮、消息中心内直接回复（遵守匿名规则） |

第一轮审查发现的立即性问题（`/logout` 图片注销攻击、审核绕过、教师可读草稿、点赞回弹、双击重复提交、性能 N+1 等）并入 **P0 止血**阶段。

**实施顺序**：P0 止血（不改表）→ P1 数据模型（一次迁移，过原生 PG 门禁）→ P2 身份与头像 → P3 内容安全 → P4 评论与回复 → P5 通知 → P6 创作中心 → P7 性能与 LQ 收敛。依赖关系见第 9 节。

---

## 1. 设计原则（全计划共用）

1. **身份由服务端一次解析，前端只渲染。** 显示名、头像、角色标签、可否点开/关注/私信，全部由 `resolve_personas(viewer, refs)` 给出。前端不再拼 `/api/profile/avatar?...`，也不再判断 `display_mode`。这是消除“同一个人在卡片、详情、评论、侧栏、通知里长得不一样”的唯一办法。
2. **三个正交维度，不再塞进一个 `status`。**
   - 生命周期 `status`：`draft / published / deleted`（作者拥有）
   - 审核态 `moderation_state`：`none / pending / passed / flagged / limited / removed / appeal_pending`（系统与管理员拥有）
   - 可见范围 `visibility`：`public / class / selected_users`（作者拥有）

   “谁能看到”是三者的合取。当前 `moderated` 混在 `status` 里，正是作者能自己解除隐藏的根因（`blog_service.py:1002`）。
3. **匿名是对同学匿名，不是对学校匿名。** 发帖界面明确告知；教师查看真实作者须填理由并写审计日志。不做“系统也不知道是谁”的伪承诺。
4. **每次处置必须闭合四件事**：可读的原因 → 给当事人的通知 → 可查的日志 → 申诉或撤销路径。缺一不可。
5. **通知是“人能处理的事件”，不是数据库变更的回声。** 点赞类聚合、自操作抑制、内容删除后通知失效、读了内容即视为已读。
6. **乐观锁只锁内容。** 评论、置顶、加精等不改正文的操作，不得推进作者编辑所依赖的版本号。
7. **AI 走网关、可重放、有上限。** 遵守 AI 规范 A1–A11：`ai_gateway_post`、明确 `task_type`/`task_label`、结果绑定内容修订号、迟到结果丢弃、失败不对外暴露错误文本。
8. **液态玻璃：领域 CSS 只做布局。** 新界面由 LQ 组件构成；文字颜色只取材质配对变量；正文可读性不依赖模糊；优先扩展已有组件（如 `Avatar`），不另造。

---

## 2. 现状问题清单（统一编号，后文引用）

### 2.1 安全与权限（S）

| 编号 | 问题 | 证据 | 严重度 |
|---|---|---|---|
| S1 | Markdown 允许任意相对路径图片，`/logout` 是 GET；`![x](/logout)` 让每个读者下线 | `markdown_runtime.js:76-83`、`auth.py:626` | 高 |
| S2 | 作者 `PUT status=published` 可解除教师隐藏，且不留日志 | `blog_service.py:1002-1005` | 高 |
| S3 | 教师/助手可按 ID 读、赞、评、收藏任意草稿（实测 200） | `blog_service.py:2296` | 中 |
| S4 | 对草稿“转为私密→恢复可见”会把草稿发布；加精对草稿也发通知 | `blog_service.py:1930-1989` | 中 |
| S5 | `users-search` 允许任意学生枚举全校姓名/班级，并可用学号前缀试探 | `routers/blog.py:765-811` | 中 |
| S6 | 头像接口可按任意 user_id 取默认 SVG，首字取**真名** | `routers/profile.py:54-80,507-520` | 中 |
| S7 | 从作者浮窗私信笔名作者，联系人卡显示真名与学号 | `message_center_service.py:1316-1360` | 中 |
| S8 | 上传图片先落盘再校验，被拒文件残留；信任客户端 MIME；`load()` 前不限像素 | `routers/blog.py:548-570` | 中 |
| S9 | 用户正文的 `id/class` 属性未清洗，可伪造站内 UI、劫持目录锚点 | `markdown_runtime.js:6` | 低 |
| S10 | 外链图片泄露读者 IP/Referer | `markdown_runtime.js:150` | 低 |

### 2.2 身份与头像（I）

| 编号 | 问题 | 证据 |
|---|---|---|
| I1 | 匿名帖、管家帖/评论请求无参 `/api/profile/avatar`，返回**看帖人自己的头像**；与发帖框“头像也会隐藏为默认样式”的承诺矛盾 | `blog_service.py:217,646`；`profile.py:521`；`blog.js:2100` |
| I2 | 帖子头像哈希是写入快照，评论实时查询；换头像后两处不一致；文件响应无缓存头 | `blog_service.py:611-647,2448-2489` |
| I3 | 帖子署名写入时快照，改名/改昵称不传播 | `blog_service.py:619-637` |
| I4 | 评论没有身份模式，永远用会话真名 | `blog_service.py:1704` |
| I5 | 匿名作者在自己帖下评论：真名、头像、可点浮窗全部暴露；回复通知标题含真名 | `blog_service.py:2715-2732`；`blog_notifications.py:77` |
| I6 | @管家 回复匿名帖时把作者昵称、简介、心情画像、学期信息送进提示词，回复公开 | `blog_ai_service.py:471-504` |
| I7 | 昵称模式仍暴露 identity、班级标签、修为徽章；浮窗“ta的帖子”把昵称帖与真名帖并列 | `blog_service.py:2597-2663`；`blog.js:1322` |
| I8 | 教师审核时没有合规途径查看匿名作者 | — |
| I9 | “活跃作者”按快照名 + 头像分组，同一人可出现多次 | `blog_service.py:1349` |
| I10 | 作品集把匿名帖作为候选与时间线事件，无模式过滤 | `portfolio_service.py:310-325,860-883` |
| I11 | 关注接口接受任意 identity，不校验匿名 | `blog_community_service.py:21-23` |
| I12 | 已删除账户的头像 404，前端无回退，出现破图 | `profile.py:518` |
| I13 | 设置昵称后需刷新页面，发帖框才能选昵称 | `routers/blog.py:74-97` |

### 2.3 内容安全与审核（M）

| 编号 | 问题 | 证据 |
|---|---|---|
| M1 | 帖子、评论、图片均无任何自动审核 | `blog_service.py:799-887,1661-1750` |
| M2 | 管家输出不经检查直接公开；失败时把错误文本作为**公开评论**发出 | `blog_ai_service.py:505-507,617-619` |
| M3 | 隐藏是开关，前端发送的原因永远为空；作者不被通知，只在“我的”里看到“私密” | `blog.js:2317`；`blog_service.py:1965-1989` |
| M4 | `blog_moderation_logs` 只写不读；删评论、关评论不记录 | `blog_service.py:2587-2594` |
| M5 | 处理举报只改举报单状态，不处置内容；举报人与作者都不知道结果；网页端不能举报评论 | `blog_community_service.py:216-231`；`blog.js:2991` |
| M6 | 权限割裂：任何教师可隐藏，只有超管能处理举报 | `routers/blog.py:708` vs `blog_service.py:205` |
| M7 | 评论只能硬删整棵子树；`POST_STATUS_HIDDEN`、`COMMENT_STATUS_HIDDEN` 定义未用 | `blog_service.py:1753-1798` |
| M8 | @管家 绕过 AI 网关与周预算，无每人限流，评论关闭时仍可发 | `blog_ai_service.py:342-358` |

### 2.4 通知（N）

| 编号 | 问题 |
|---|---|
| N1 | 不通知：点赞、收藏、@用户、关注、关注对象发新帖、被隐藏/恢复、举报结果、评论被删 |
| N2 | 帖子作者不知道自己帖子下的楼中楼回复 |
| N3 | 加精通知可重复（开关一次发一次）、会发给自己、会发给已隐藏帖 |
| N4 | 深链只到帖子，`ref_id` 未用，评论无锚点 |
| N5 | 读了帖子不消除相关通知；帖子/评论删除后通知仍指向不存在的内容 |
| N6 | 无按类型静音设置 |
| N7 | 加精通知的发起人为空，显示为“系统” |
| N8 | `_insert_notification` 不去重，无聚合能力 |

### 2.5 创作中心与编辑器（C）

| 编号 | 问题 | 证据 |
|---|---|---|
| C1 | 评论、置顶、加精都推进 `updated_at`，作者编辑时必然 409；本地草稿保留旧版本号并在重开时覆盖服务器值，此后每次保存都 409 | `blog_service.py:82,1732,1937…`；`blog.js:1894-1896` |
| C2 | 编辑已发布帖时点“保存草稿”会静默撤下 | `blog_service.py:1002` |
| C3 | 草稿发布沿用旧 `created_at`，在信息流里排得很靠后；关注者从不被告知 | — |
| C4 | “我的”卡片没有编辑/删除/发布/撤回操作，不显示 `edited_at` 与处置原因；`hidden` 状态无法筛选 | `blog.js:2901-2971` |
| C5 | `blog_post_views` 里的阅读时长、完读率从不读出 | `blog_service.py:1423-1475` |
| C6 | 没有粉丝列表、我的评论、收到的评论/点赞、关注管理；标签关注只有后端 | `blog_community_service.py` |
| C7 | 编辑时附件预览丢失（接口缺 `file_hash`） | `routers/blog.py:814-827`；`blog.js:1915` |
| C8 | 没有公开作者主页；浮窗无头像、简介、数据 | `blog.js:1397-1430` |
| C9 | 双击“发布”“发送评论”会重复提交 | `blog.js:2106,2377` |
| C10 | 可见范围为班级但无班级、为指定用户但列表为空时，静默缺省 | `blog.js:2139-2144` |

### 2.6 评论与回复（R）

| 编号 | 问题 |
|---|---|
| R1 | 无限嵌套 + 递归渲染，每层右移；恶意链式回复可让详情接口 500 |
| R2 | 已发布回复不显示“回复 @X”；接口不返回父评论作者 |
| R3 | 一级评论超过 100 条静默截断；评论全量加载后在 Python 中分页 |
| R4 | 无评论编辑；无评论举报入口；无楼主/作者标识 |
| R5 | 评论图片以新标签打开，未接入统一灯箱 |
| R6 | 发表评论、置顶后整页重拉，滚动位置与未写完的评论丢失 |
| R7 | 消息中心不能直接回复；小程序博客通知只能预览 |

### 2.7 前端状态与可访问性（F）

| 编号 | 问题 |
|---|---|
| F1 | 点赞/收藏只改 DOM，`loadDiscovery()` 重绘后回弹（实测） |
| F2 | 快速双击“加载更多”会跳页；后退回列表只剩第 N 页 |
| F3 | 板块标签键盘切换后焦点丢失；用户浮窗无键盘、无 Esc、不随滚动关闭 |
| F4 | 错误态与空态同一外观且无重试 |
| F5 | 两个 `h1`、嵌套 `main`；发帖字段缺可访问名称；赞/收藏无 `aria-pressed` |
| F6 | 5 处原生 `window.confirm`；举报框用原生 `<dialog>`；浮窗手工定位，都绕过 LQ.layer |
| F7 | `blog-paper.css` 仍有十余处自带按钮/面板材质、固定 z-index、第二套悬浮动效 |
| F8 | 详情正文取次要灰（实测 `rgb(179,189,204)`），17px 长文对比不足 |
| F9 | “关注”页无内容时被精选区与热榜挤出首屏，空态文案错误（实测） |
| F10 | 375px 宽下全站导航占约 250px；详情返回条文案三行（实测；导航为全站问题） |

### 2.8 性能与可移植（P）

| 编号 | 问题 | 证据 |
|---|---|---|
| P1 | 每个学生作者单独计算修为徽章，每次查其全部课程（N+1） | `blog_service.py:594-608` |
| P2 | 8 个 `async def` 路由内执行同步 DB 与 PIL，阻塞事件循环 | `routers/blog.py:100,224,303…` |
| P3 | 评论图片鉴权对评论表做 `LIKE '%hash%'` 全表扫描，`LIMIT 80` 还会误判 404 | `blog_service.py:2052-2133` |
| P4 | 搜索用普通 `LIKE`，PostgreSQL 区分大小写，线上与测试结果不同；通配符未转义 | `blog_service.py:1083-1100` |
| P5 | `(? IS NULL OR …)` 在 psycopg 上可能报参数类型错误 | `routers/blog.py:779` |
| P6 | 打开页面请求 `/api/blog/summary` 三次 | 实测 |
| P7 | 点赞/收藏并发撞唯一约束返回 500 | `blog_service.py:1830-1920` |
| P8 | `platform_knowledge_service.py:196` 查询不存在的列 `blog_posts.author_id` | — |

---

## 3. 统一身份模型：作者面孔（Persona）

本节是改进项 1、2、6 的共同地基。

> **P2 实施记录（2026-10-12，已发布 `8f083307`）**。真源 `classroom_app/services/blog_persona_service.py`，所有序列化（卡片、详情、评论、活跃作者、作者筛选、通知回调）只问它。与下文原方案的差异（均为有意调整）：
> - **匿名代号不用 HMAC 密钥**：改为随机抽词并落表 `blog_anonymous_codes`（`UNIQUE(post_id, author_identity)` + `UNIQUE(post_id, code)`，SAVEPOINT 内抢占、冲突重试）。效果同 R-P2（同帖同人恒定、跨帖不可关联、同帖不重复），且无需管理与轮换 `BLOG_PERSONA_SECRET`；删除评论不释放代号。评论行同时存 `author_display_snapshot=代号`、`author_display_name=匿名同学·代号`，任何旧读取路径拿到的都是公开面孔。
> - **笔名存储值仍为 `nickname`**（不改名为 `pen`，免迁移、免 Agent/API 兼容层）；对外 `kind="pen"`。
> - **笔名 v1 收紧**：只显示笔名与首字头像（不显示上传照片，否则同一照片会把笔名与真人连起来）；不可点开、不可关注、不可私信。笔名主页与“关注笔名”（`blog_follows.target_mode`）推迟到 P6。
> - **R-P1 细化**：“作者”标记只在评论与帖子是同一面孔时出现（实名帖下实名评论、笔名帖下笔名评论）；作者在自己实名帖下用笔名/匿名评论不带标记，否则会暴露笔名身份。匿名帖作者一律“楼主”。
> - **笔名校验**：保留称呼（楼主、管家、作者、系统、管理员、老师、AI 助手、`匿名…`、含“·”）与平台上他人真名不可作笔名；个人中心只校验**改动后**的笔名，发帖/评论使用时再校验一次。
> - **隔离**：关注流的“关注作者”只含实名帖；作者筛选、活跃作者只含实名帖；作品集排除匿名帖（笔名帖仍可由本人选入）；旧数据里笔名/匿名帖的班级系统标签在标签筛选、搜索、热门标签、管家上下文中一律不读，并由启动回填清空。
> - **帖子作者可关闭匿名评论**（`allow_anonymous_comments`，发帖器“允许匿名评论”），服务端强制。
> - **头像**：实名头像改走内容寻址 `/api/blog/avatar/{hash}`（仅当该哈希是某人当前头像才返回，`immutable` 缓存，URL 不含用户 id）；匿名头像取代号首字、楼主取“楼”。LQ `Avatar` 的 kind/seed 扩展与 `avatar_file_hash` 索引推迟到 P7。
> - **管家**：被 @ 时若发起人是笔名/匿名/楼主，不注入画像与学期上下文；回复正文中出现其真名或笔名一律替换为公开面孔（`persona_leak_blocked`）。
> - **通知**：非实名评论者在通知回调里不带 role/user_pk（不可回访）。
> - **启动回填新增两项**（幂等）：`posts_shielded_system_tags`、`comments_anonymous_op`（P2 前匿名帖作者以真名写下的评论改存“楼主”）。
> - **门禁**：`classroom_app/db/schema_blog_closed_loop.py` 有改动（新表），发布前须重做原生 PostgreSQL 演练。
> - **未在 P2 做**：笔名主页/关注笔名（P6）、作者浮窗改共享 Popover 与 LQ Avatar 扩展（P7）、审核台查看真实作者（P3）。
> - 测试：`tests/test_blog_p2_persona.py`（26 项，含评审发现的 5 类泄露回归）。

### 3.1 定义

每一次出场（发帖或评论）的对外身份叫 **persona**，在读取时由服务端解析：

```
persona = {
  kind:        "real" | "pen" | "anon" | "op_anon" | "assistant" | "system",
  display:     "张三" | "风之子" | "匿名同学·青柚" | "楼主" | "管家" | "博客中心",
  role_label:  "教师" | "学生" | "AI 助手" | null,     # anon / op_anon 不给
  avatar:      { kind: "image"|"initials"|"anon"|"assistant", src?, seed? },
  profile_ref: 不透明令牌 | null,                      # 可打开作者浮窗/主页；anon/op_anon/assistant 为 null
  can_follow:  bool,
  can_message: bool,
  badges:      [...],                                   # 修为徽章、班级等，仅 kind=real
  is_op:       bool                                     # 是否本帖作者（无论是否匿名）
}
```

| kind | 适用 | 显示名 | 头像 | 班级/徽章 | 可点开 | 可关注 | 可私信 |
|---|---|---|---|---|---|---|---|
| real | 教师（强制）、学生实名 | 实时真名 | 上传头像或首字 | 是 | 是 | 是 | 是 |
| pen | 学生笔名（原“昵称”） | 实时笔名；笔名被清空时用快照 | 上传头像或**笔名**首字 | **否** | 是，进入**笔名主页**（只含笔名帖） | 是（关注笔名，见 3.4） | **否**（v1，见 D4） |
| anon | 学生匿名评论 | `匿名同学·{代号}`，同帖同人同代号 | 由代号种子生成的抽象图形 | 否 | 否 | 否 | 否 |
| op_anon | 匿名帖作者在本帖内的任何出场 | `楼主` | 匿名图形，固定“楼主”色桶 | 否 | 否 | 否 | 否 |
| assistant | 管家 / AI 资讯 | `管家` / `AI 资讯` | 平台固定管家图标 | “AI 助手” | 否 | 否 | 否 |
| system | 系统事件的发起人 | `博客中心` | 平台图标 | — | 否 | 否 | 否 |

**关键规则**

- **R-P1 楼主规则。** 匿名帖作者在该帖下的任何评论一律解析为 `op_anon`，不论作者评论时选了什么模式（强制）。实名/笔名帖作者评论时保持原 persona，并带“作者”标记（`is_op=true`）。
- **R-P2 匿名代号。** `代号 = 词表[HMAC(BLOG_PERSONA_SECRET, post_id ‖ identity) mod N]`。词表为中性名词（青柚、远山、白鹭……，≥128 个，人工审过、无冒犯词）。同帖同人恒定、跨帖不同，无法跨帖关联。同帖两人撞词时追加序号。`BLOG_PERSONA_SECRET` 缺失时启动报错，不允许默认值；轮换密钥会改变历史代号，因此只在泄露时轮换。
- **R-P3 笔名不是匿名。** 界面明确写“笔名会公开显示，同学看不到你的真名和班级；老师可在审核时查看”。笔名主页只聚合同一笔名的帖子，绝不与真名帖合并。
- **R-P4 教师视角。** 教师（含超管）浏览时看到的 persona 与学生完全一致；只在审核台通过“查看真实作者”动作显式揭示，须填理由并写审计日志（第 5.5 节）。这样教师日常阅读不会带着身份偏见。
- **R-P5 管家。** `role=assistant, user_pk=0` 永远解析为 assistant，不再触达头像接口。
- **R-P6 解析时机。** 署名与头像在读取时批量实时解析；快照字段只用于账户删除、笔名被清空等回退场景。

### 3.2 服务端实现

新增 `classroom_app/services/blog_persona_service.py`：

- `resolve_personas(conn, viewer, refs: list[PersonaRef]) -> dict[key, Persona]`
  - `PersonaRef = (post_id, actor_identity, display_mode, snapshot_name, snapshot_avatar_hash, post_author_identity, post_display_mode)`。
  - 一次批量查询 `students`/`teachers` 的姓名、昵称、头像哈希，取代 `_load_comment_avatar_map` 与帖子快照两套逻辑（解决 I2、I3）。
  - 修为徽章不在这里实时计算，改为带 TTL 的批量缓存，且只对 `real` 计算（同时修复 P1）。
- 所有序列化入口（帖子摘要、详情、评论、发现页、活跃作者、我的、收藏、关注流、通知文本、全局搜索、作品集候选）改为只消费 persona，**删除**各自对 `display_mode` 的判断。
- API 中帖子/评论的 `author` 对象只含 persona 字段；对非本人不再下发 `identity`、`user_pk`。`is_author` 仍按查看者计算，供“编辑/删除”按钮使用。
- 源码守卫测试：博客模块内禁止出现无参 `/api/profile/avatar`、禁止直接读取 `author_display_name` 输出到响应。

### 3.3 头像（改进项 1）

**头像形态由服务端决定，前端只用 LQ `Avatar` 渲染。**

现有 LQ `Avatar`（`docs/lq-components.md:35`）参数为 `name, src=null, size=40, attrs={}`，已具备首字回退、六色桶与图片失败回退。本计划**扩展**它，不另造组件：

| 新增参数 | 取值 | 行为 |
|---|---|---|
| `kind` | `person`（默认，兼容现状）/ `anon` / `assistant` | `anon`：以 `seed` 生成确定性抽象 SVG（几何图形 + 六色桶之一），不发网络请求；`assistant`：固定平台图标 |
| `seed` | 字符串 | 仅 `anon` 使用；决定图形与色桶 |
| `label` | 字符串 | 无障碍名称；默认取 `name`。`anon` 时为“匿名同学·青柚的头像” |

按组件合同第 2 节同步三处入口（`classroom_app/lq_components.py`、`static/js/lq/component-props.js`、`templates/macros/lq/`）与合同测试；颜色只取六色桶的 soft/fg 配对令牌，不内联色值。

| persona.avatar.kind | 来源 | 渲染 |
|---|---|---|
| image | 用户上传头像 | `Avatar(name, src=/api/blog/avatar/{opaque}?v={hash})`，失败回退首字 |
| initials | 未上传 | `Avatar(name)`，首字取 **persona.display**（笔名用笔名首字），纯前端绘制，不请求服务器 SVG |
| anon | anon / op_anon | `Avatar(kind='anon', seed)` |
| assistant | 管家 / AI 资讯 | `Avatar(kind='assistant')` |

后端要点：

1. **新增博客头像路由** `/api/blog/avatar/{opaque}`。`opaque` 是服务端签发的不透明令牌（HMAC(identity, 当日)），不暴露 user_id；只返回已上传的头像文件，没有文件返回 404（前端回退首字）；`v` 与当前哈希一致时返回 `Cache-Control: private, max-age=31536000, immutable`，否则 `no-cache`。解决 I1、I2、I12 以及 S6 在博客内的部分。
2. **现有 `/api/profile/avatar`**：无参调用保持“返回本人头像”（导航栏、个人中心在用），但博客不再调用它。带 `user_id` 且请求者非本人时默认 SVG 是否改用角色字形，影响全站其它页面，见待决 D3。
3. **一致性**：卡片、详情头、评论、楼中楼“回复 @”、活跃作者（同时修复 I9：按 identity + mode 分组）、作者浮窗、作者主页、消息中心通知卡、创作中心粉丝列表，全部用同一 persona 与同一 `Avatar`。多人场景（如“X 等 N 人”）用已有 `AvatarStack`。

### 3.4 署名模式（改进项 2）

**发帖**

| 用户 | 可选模式 | 默认 |
|---|---|---|
| 教师 | 实名（强制） | 实名 |
| 学生 | 实名 / 笔名 / 匿名 | 上次选择（按用户记忆，存 `user_ui_preferences`） |
| 管家 | assistant（强制） | — |

- 用 LQ 复合选择卡（`kind='choice'`）呈现三种模式，每张卡含左侧 `Avatar` 预览（将以什么面孔出现）+ 名称 + 一句说明：
  - 实名：“显示你的姓名、班级和修为徽章。”
  - 笔名：“显示你的笔名，同学看不到真名和班级；老师可在审核时查看。”
  - 匿名：“显示为匿名同学，你在本帖的回复显示为‘楼主’；老师可在审核时查看。”
- 未设笔名时，笔名卡显示“去设置笔名”，打开个人中心昵称设置浮层（LQ Dialog），保存后派发 `lq:profile-updated`，发帖框即时刷新，无需刷新页面（I13）。
- 匿名卡附带复选“允许匿名评论”（默认开，见 D13）。
- **发布后修改模式**：允许。通过 `LQ.confirm` 提示后果：
  - 实名/笔名 → 匿名：“已经看过此帖的人可能已经知道作者是谁。”
  - 匿名 → 实名/笔名：“你在本帖下以楼主身份发的回复，也会显示为新的署名。”
  - 因为 persona 在读取时解析，评论与通知里的显示名会随之改变——这是期望行为。每次切换写入审核日志（`action=persona_change`，仅本人与超管可见），用于纠纷追溯。

**评论**

- 学生评论可选 实名 / 笔名 / 匿名（帖子禁止匿名评论时只有前两项）。默认取该用户在**本帖**上一次的选择，否则取全局上一次选择。
- 楼主规则优先：匿名帖作者评论时，选项区折叠为一行说明“你是楼主，将以‘楼主’身份回复”。
- 评论框左侧 `Avatar` 实时预览将要使用的 persona，消除“我到底以什么身份发出去”的不确定感。

**笔名与其它系统的隔离（I7、S7、I10、I11）**

- 作者浮窗：`pen` 只显示笔名、头像、笔名简介、笔名主页入口、关注按钮；**不**显示私信按钮与班级。
- 私信：v1 只对 `real` 开放（D4）。
- 作品集：`real` 帖照常；`pen` 帖作为可选候选，勾选时提示“将以真名展示在作品集中”；`anon` 帖一律排除。
- 关注：接口拒绝关注 `anon/op_anon`；关注 `pen` 以 `(identity, mode=pen)` 为键，关注流只推该笔名的帖子。
- `users-search`（用于 @ 与“指定可见用户”）：教师可搜任意用户；学生只能搜到同班同学和本人任课教师；结果只返回实名显示名，不匹配学号，至少 2 个字符（S5）。
- 全局搜索：结果卡片的作者使用 persona。

### 3.5 管家与匿名（I6）

- 构造管家提示词时，若触发者在该帖的 persona 为 `anon/op_anon/pen`，**不注入**用户画像块与学期上下文，只注入帖子正文与可见评论（评论作者同样以 persona 呈现）；提示词中以 persona 显示名称呼对方。
- 服务端后处理：若管家回复中出现触发者的真名或昵称（与 persona 不一致），替换为 persona 显示名，并记录 `persona_leak_blocked` 日志。

---

## 4. 帖子与评论的状态机

### 4.1 帖子

```
生命周期（作者）：
  (new) ──► draft ──发布──► published ──撤回为草稿──► draft
                               └── 删除 ──► deleted（软删，30 天后物理清理）

审核态（系统 / 管理员）：
  none ──提交──► pending ──AI 通过────────► passed
                   ├──AI 中风险 / L1 标记──► flagged（可见，进复核队列）
                   ├──AI 高风险──────────► removed（自动，通知作者）
                   └──超时 / 故障────────► flagged（对外可见性见 D1）
  passed / flagged ──管理员──► limited（仅作者与管理员可见）/ removed
  passed / flagged ──≥3 人举报──► limited（自动暂限，进队列）
  limited / removed ──作者申诉──► appeal_pending ──裁决──► passed / 维持原状
  limited / removed ──作者修改正文──► pending（重新审核）
```

新增/调整字段（P1 迁移，见第 8 节）：

| 字段 | 说明 |
|---|---|
| `status` | `draft / published / deleted`；去掉 `hidden`、`moderated` 两个旧值 |
| `moderation_state` | `none / pending / passed / flagged / limited / removed / appeal_pending` |
| `moderation_reason_code` | `spam / abuse / privacy / illegal / misinformation / off_topic / other` |
| `moderation_note` | 给作者看的说明（管理员填写，或由 AI 结论按模板生成） |
| `moderated_by` / `moderated_at` | 最后处置人（`system:ai`、`system:reports` 表示自动） |
| `content_revision` | 整数，仅标题、正文、标签、可见范围、署名模式变化时 +1；乐观锁用它（根治 C1） |
| `published_at` | 首次发布时间，信息流排序用它（修复 C3） |
| `deleted_at` | 软删时间 |

**可见性规则**：唯一实现 `can_view_post`，并有一个等价的 SQL 片段生成器供列表查询使用；两者由同一组参数化测试约束，避免 Python 与 SQL 规则漂移。

| 查看者 | draft | published + none/passed/flagged | published + pending | limited | removed | deleted |
|---|---|---|---|---|---|---|
| 作者 | ✓ | ✓ | ✓（标“审核中”） | ✓（显示原因） | ✓（显示原因） | ✗ |
| 博客管理员（见 5.5） | ✗（修复 S3） | ✓ | ✓ | ✓ | ✓（仅审核台） | ✗ |
| 其他人 | ✗ | ✓（再叠加 visibility） | 见 D1 | ✗ | ✗ | ✗ |

**作者能做的**：只有 draft↔published 与删除。请求在 `limited/removed/appeal_pending` 下要求发布，返回 409 `moderation_locked`（修复 S2）。允许作者编辑被处置的帖子以便改正，保存后进入 `pending` 重新审核；申诉与“修改后重审”是两条并行出路。

**管理员动作**（取代旧的隐藏开关，修复 S4、M3）：限制可见、移除、恢复、加精/取消、置顶/取消。限制与移除**必须**选原因码；恢复必须填一句备注。只作用于 `status=published` 的帖子。

### 4.2 评论

| 字段 | 说明 |
|---|---|
| `status` | `active / deleted`（软删） |
| `moderation_state` | 与帖子同枚举，不含 `appeal_pending`（见 D6） |
| `author_display_mode` | `real / pen / anon`；`op_anon` 在读取时推导，不存 |
| `author_display_snapshot` | 署名快照，仅作回退 |
| `root_comment_id` | 所属一级评论（楼层），一级评论指向自身 |
| `reply_to_comment_id` | 直接回复的评论（可为楼内任意一条） |
| `content_revision` / `edited_at` | 评论编辑（发布后 15 分钟内） |
| `deleted_at` / `deleted_by_role` | 软删信息，用于占位文案 |

- 软删：有回复的评论显示占位“该评论已被作者删除 / 已被管理员移除”，无回复的直接从列表消失（修复 M7）。
- 评论可见 = 帖子可见 ∧ `status=active` ∧ `moderation_state ∉ {limited, removed}`。评论作者本人始终能看到自己的评论及处置原因。

---

## 5. 内容安全闭环（改进项 3）

> **P3 实施记录（2026-10-12，已发布 `04ad5f49`）**。代码：`blog_moderation_states.py`（状态与公开 SQL 片段，所有跨用户读取都用它）、`blog_safety_service.py`（L1、限流、初始状态、入队）、`blog_safety_review.py`（L2 调度任务与超时兜底）、`blog_moderation_service.py`（处置、举报、申诉、揭示、审计）、`blog_review_queue_service.py`（审核台）、`routers/blog_moderation.py`；前端 `blog_moderation_ui.js`（LQ 表单弹层、横幅、隐私预检）、`manage_blog_review.js` + `templates/manage/system/blog_review.html`（管理中心“平台管理 → 博客审核”）。与下文原方案的差异（均为有意调整）：
> - **`status` 已收敛**：读取全部改用 `moderation_state`；启动回填把旧 `moderated/hidden` 帖子改为 `published` + 原审核态，旧 `hidden` 评论改为 `active` + `limited`。`deleted` 只用于软删（见下）。
> - **L2 路由**：复用现有 `fast_text_response` / `vision_interactive`（`source_feature=blog` 自动走边缘视觉档），经 `ai_gateway_post` 计量，用量标签 `blog_content_safety`，未新增 `TASK_POLICIES` 条目。回执表 `blog_content_reviews` 按修订号绑定；迟到/旧修订结果丢弃；3 次失败或 10 分钟超时 → `flagged` 可见并进审核台；日额度 `BLOG_SAFETY_DAILY_LIMIT`（默认 2000）用尽 → 待审帖直接 `flagged`；`BLOG_SAFETY_AI_ENABLED=false` 时只按 L1 决定。
> - **已公开帖子的修改**不再隐藏：状态回到 `none` 可见并重新检查；首次发布才先审后发（D1）。
> - **被处置帖子的修改**（评审 H2）：进入 `pending` + `system:resubmit`，AI 结论只作参考，超时/额度/失败兜底都不会放行，只能由博客管理员在审核台“修改待复核”中恢复或再处置；作者横幅显示“修改已提交，等待博客管理员复核”。
> - **博客管理员** = 超管 + `blog_moderators` 授权教师（撤销保留行 `revoked_at/revoked_by` 作记录）；普通教师只有“紧急限制”，且只能作用于公开内容（评审 H1）。管理中心导航项仅超管可见，被授权教师从博客页“审核台”入口进入。
> - **“确认”** 只用于已定论的 `flagged/limited/removed`，`pending/appeal_pending` 必须明确恢复或处置（评审 M1）。超时/额度放行的 `flagged` 仍可被迟到的 AI 结论处理（评审 M2）。
> - **软删**（评审 H3）：有处置记录的帖子删除时改为 `status='deleted'`，审计日志（含身份揭示）不随帖子级联删除；无处置记录的帖子仍物理删除。30 天物理清理留待 P4/P7。
> - **评论**：L1 flag 的评论先隐藏（作者可见“审核中”），通过后补发原本跳过的新评论通知；被处置但下有可见回复的评论显示为无作者、无内容的占位；其图片不再对他人开放（评审 M3）。评论不支持申诉（D6）。
> - **举报**：新增“暴露了我的身份或个人信息”（审核台置顶）；≥3 个不同举报人自动暂限（`BLOG_REPORT_AUTO_LIMIT`）；处理即处置并通知每位举报人；同一举报人再次举报不再撞唯一约束。
> - **管家**：经网关 P2；每人每日 @管家 上限 `BLOG_HOUSEKEEPER_DAILY_LIMIT`（默认 20，超限只回给本人提示）；关闭评论的帖子不再回复；输出过 L1，命中即丢弃并私信说明；触发帖仍在审核时等待（调度重试），被处置则放弃。
> - **通知**：新增消息类别 `blog_moderation`（博客审核）；发起人一律显示“博客中心”，举报人不会得知处理人。
> - **身份揭示**仅限已发布内容，写 `identity_reveal` 日志；审计页（超管）把学生执行人显示为“作者”，避免绕过揭示审计。
> - **未在 P3 做**：评论编辑/软删（P4）、通知聚合与偏好（P5）、审核台的词表管理界面（目前直接维护 `blog_safety_terms` 表）、外链图片“点击加载”（P7）、资讯采集帖图片 L2（采集帖只过 L1）。
> - 测试：`tests/test_blog_p3_moderation.py`（43 项，含安全评审 10 项回归）；P0–P2 测试按新语义更新（学生帖先审、`status` 收敛、教师 7 为博客管理员）。

### 5.1 流水线

```
写入请求 ─► L0 结构校验 ─► L1 本地规则 ─► 落库 + 同事务入队审核任务 ─► 返回
                                                     │ scheduler 领取
                                                     ▼
                                          L2 AI 审核（文本 / 图片）
                       ┌─────────────────────────────┼────────────────────────┐
                     通过                          中风险                    高风险
                state=passed                  state=flagged             state=removed
          （帖子：此刻起对外可见）           进管理员队列，作者无感知     通知作者 + 原因 + 申诉入口
                                                                       同时进管理员队列复核
```

**L0 结构校验**（同步）：长度、标签数、可见范围完整性（C10）、图片地址白名单（S1）、附件数量。

**L1 本地规则**（同步，零成本，<5ms）：

- 词表命中（违法、色情、辱骂、广告引流），存 `blog_safety_terms`，超管可维护，内置初始表。分两级：
  - `block`：拒绝写入，返回 422 和类别说明（不复述原词）；
  - `flag`：写入，但强制进入 L2，且审核完成前对外不可见（评论也一样）。
- 隐私模式：手机号、QQ/微信号、身份证号、学号格式。前端发布前先用同一组规则提示“检测到可能的个人信息，确定公开吗？”，确认后提交；后端命中则 `flag`。
- 频率：同一用户 1 分钟内发帖 ≥3 或评论 ≥10 返回 429（复用 `rate_limit_service`，同时收窄 R1 的滥用面）。

**L2 AI 审核**（异步）：

- 在 `ai_model_policy.TASK_POLICIES` 新增 `task_type = content_safety_review`：纯文本映射 `text_fast`（deepseek-flash），含图映射 `vision_edge_low`（豆包 lite）。`task_label = blog_safety:{post|comment}:{id}:r{revision}`。经 `ai_gateway_post` 调用（规则 A1），优先级见 D2。
- `response_format=json`，schema：
  ```json
  {"risk": "none|low|medium|high",
   "categories": ["spam", "abuse", "privacy", "illegal", "misinformation", "off_topic"],
   "evidence": "≤60 字，不复述违规原文",
   "author_note": "≤80 字，给作者的中性说明"}
  ```
  解析用容错 JSON 候选解析；schema 不合规按失败处理，不猜测。
- 任务载体沿用 `scheduled_tasks`（与 @管家 同一机制，同事务入队、去重键 `blog-safety:{type}:{id}:r{revision}`）。新增回执表 `blog_content_reviews`，唯一键 `(target_type, target_id, content_revision)`。**结果只对当前修订号生效**；修订号已变的迟到结果丢弃并记日志（AI 规范持久任务不变量 2、3）。
- 失败：最多 3 次，指数退避；最终失败转 `flagged` 进队列。
- 审核范围：学生帖、学生评论、管家输出（只走 L1，见 5.3）、资讯采集帖（L1 + 图片 L2）、教师评论（见 D5）。

### 5.2 待审期间的体验

推荐（D1）：**帖子先审后发，评论先发后审**。

- 帖子：作者提交后跳到详情页，能看到自己的帖子，顶部 LQ `Status` 横幅：“正在进行内容检查，通常 1 分钟内完成，完成后自动对大家可见。”其他人不可见。超过 10 分钟没有结果，自动转 `flagged` 并**对外可见**（避免 AI 故障让博客停摆），同时进入管理员队列。
- 评论：立即可见；判定高风险后自动移除并通知作者。评论是对话，等待会破坏交流，单条评论影响面也小。L1 `flag` 的评论例外，审完才可见，作者本人看到“审核中”。
- 作者端状态更新：详情页在 `pending` 时每 5 秒请求轻量接口 `/api/blog/posts/{id}/moderation`（只返回状态），最多 3 分钟，之后改为提示“稍后刷新查看”。不新增全站常驻轮询（规则 A9）。审核通过且耗时超过 30 秒时，补发一条 `blog.review_passed` 通知。

### 5.3 管家输出（M2、M8）

- 迁移到 `ai_gateway_post`，`task_type` 不变，`priority=P2`（受课堂周预算约束）。
- 每用户每日 @管家 上限，默认 20（env `BLOG_HOUSEKEEPER_DAILY_LIMIT`）；超限时只在本人评论框下方提示，不发公开评论。
- 评论已关闭的帖子不再响应 @管家（移除 `bypass_comment_lock`）。
- 失败**不再发公开评论**，改为给触发者发私人通知“管家这次没能回复，可稍后再试”，附“重试”动作（重新入队，计入限额）。
- 管家回复写入前过 L1；命中则丢弃并记录。管家回复不走 L2（成本与时延），但可被举报。

### 5.4 举报（M5）

- 网页端在每条评论的“更多”菜单中加入“举报”（后端已支持评论目标）。
- 举报理由“侵犯隐私”下增加子项“暴露了我的身份或个人信息”，命中时在队列中置顶。
- 同一内容累计 ≥3 个不同举报人（env 可调）自动转 `limited` 并进队列，通知作者：“内容收到多次举报，暂时仅你可见，管理员将尽快处理。”
- **处理即处置**：管理员在队列中直接选择“驳回 / 限制可见 / 移除”，同时写举报单与审核日志，并：
  - 通知举报人处理结果（“已处理：内容已移除”或“未发现违规”），不透露对作者的其它处置细节；
  - 有处置时通知作者：原因码说明 + 管理员备注 + 申诉入口。
- 队列中举报人显示实名（管理员视角），作者显示 persona；揭示真实作者见 5.5。

### 5.5 管理员、审核台与审计

**谁是博客管理员**（M6，见 D7）：推荐 = 超管 + 被授予 `blog_moderator` 能力的教师。普通教师保留“紧急限制可见”（对任何已发布帖），但必须选原因码，动作进入审核台等待复核。

**审核台**：管理中心“平台管理 → 博客审核”。现有 `manage_blog_crawler.js` 中的举报列表迁入并重做。

- 队列来源：AI flagged/removed、L1 flag、举报、紧急限制、多人举报暂限、申诉。按来源用 LQ `Tabs` 分组，每组显示数量。
- 每条（LQ `Card`）：内容预览（只读 Prose 槽）、AI 结论与证据、举报理由与人数、作者 persona、该作者历史处置次数、动作按钮。
- **查看真实作者**：仅对 `pen/anon/op_anon` 显示。点击后 LQ `Dialog` 要求选择理由（处理举报 / 安全风险 / 学业纪律 / 其他 + 说明），确认后显示真实姓名与班级，写 `blog_moderation_logs(action='identity_reveal', reason, actor, target)`。是否通知作者见 D8。
- **审计页**（仅超管）：按时间列出所有处置与揭示，可按执行人、动作、目标筛选。修复 M4。

### 5.6 申诉

- 作者在帖子横幅或创作中心作品行点击“申诉”，填写 ≤300 字理由；每次处置只能申诉一次。
- 状态转 `appeal_pending`，进审核台“申诉”分组；裁决结果通知作者（强制类通知）。
- 不复用[通用审批流](approval-workflow-plan-2026-09-11.md)：那是多人多步、带签名与邮件的重型流程，申诉只有一个裁决人和一个结果，放在审核台里更简洁（D9）。

### 5.7 图片安全（S8、S10）

- 上传：先校验魔数与像素数（≤40MP），再落盘；MIME 以服务端识别为准；去除 EXIF（防止泄露拍摄位置）。
- 含图帖子的 L2 走视觉档；单帖最多送审 4 张，超出的以缩略图拼版送审一次。
- 外链图片：渲染为“点击加载外部图片”占位，加载时带 `referrerpolicy="no-referrer"`。

---

## 6. 通知闭环（改进项 4）

> **P5 实施记录（2026-10-12，未提交、未发布）**。代码：通用投递 `classroom_app/services/notification_delivery.py`（聚合、静音开关、按帖已读、内容失效）；博客事件 `classroom_app/services/blog_notifications.py`（事件目录 `BLOG_EVENTS`、各事件、读取时 persona 渲染）；内联回复上下文 `blog_comment_service.reply_context`；接口 `POST /api/message-center/read-by-ref`、`GET/PUT /api/message-center/preferences`、`GET /api/blog/comments/{id}/reply-context`、`POST /api/blog/following/seen`，`/api/blog/summary` 增加 `following_unread_count`；前端 `static/js/message_center_blog.js`（博客卡片、内联回复、通知设置弹层）、`blog.js`（读帖即已读、关注数、评论失效提示、关注空态）、顶栏 `frontend/src/lib/blog-topbar.ts` 与 `message_center_bell.js`。与下文原方案的差异与补充：
> - **不改表**：P1 已加 `aggregate_key/aggregate_count/ref_post_id/target_state` 与 `user_notification_preferences`。结构化载荷沿用已有 `metadata_json`（`event/post_id/comment_id/actor/title_template`），未新增 `payload_json`；PG 部分唯一索引兜底未做，聚合只在应用层合并（并发两条同时到达时最多多出一张未读卡）。因此 P5 不需要原生 PG 演练门禁。
> - **聚合**：写入时只并入同 `aggregate_key` 的**未读**行（PG 上 `FOR UPDATE` 锁住该行，计数在 SQL 内自增；行在此期间被读则另起新卡，不丢事件）；`aggregate_member` 以服务端密钥 HMAC 的 16 位摘要记录参与者，同一键下（含已读卡）每人只计一次——同一人当天取消再点赞不会再提醒；`aggregate_recent` 保留最近 4 个评论/关注者用于 AvatarStack，二者都不下发到前端，`aggregate_key` 也不下发。楼中楼回复按帖、间隔 1 小时内连续合并，点赞按“帖/评论 + 自然日”，关注按自然日。
> - **读取时渲染**：卡片标题用 `title_template` + 当前 persona 渲染；非实名面孔一律清空 `actor_identity/actor_role/actor_user_pk`。作者事后把帖子改为匿名，旧回复卡也显示“楼主”。首页“最近动态”和个人中心最近通知改走同一序列化（安全评审 M1），不再直接读存储标题。
> - **@ 提及**：没有全站人名解析，只认本帖已公开的面孔（帖子作者与评论者的实名/笔名）和发起人关注的作者；匿名面孔永远不是候选；同名两人时视为歧义、谁都不提醒；最长名字优先，无需分隔符，每条最多 10 人。同一条评论已经给某人发过评论/回复卡就不再发提及卡；同一人在同一帖子里反复 @ 同一人合并为一张未读卡（“……（3 次）”），防止刷屏。帖子内提及在帖子（再次）公开时发送，每帖每人一次。
> - **内容失效**：`target_state` 分为 `gone_post`（帖子对该接收人不可见）与 `gone_comment`（评论删除/被处置）。帖子删除对所有人失效；帖子被限制/移除/撤回草稿只对作者以外的人失效（作者仍要能打开去申诉），恢复公开时只复原 `gone_post`，已删除评论的卡不会被帖子编辑或恢复“复活”。审核类卡（`blog_moderation`）不随之失效，读帖即已读也不会把它们置读。失效卡去掉跳转，打开通知回到消息中心。
> - **通知是副作用**：所有新增触发点经 `best_effort`（SAVEPOINT 隔离）调用，通知失败只记日志，点赞、评论、关注、发帖本身照常成功。
> - **关注**：只能关注真实存在的账号，每人最多 500 个关注对象（安全评审 M2）。
> - **举报结果**：内容已被处理时卡片不带链接。**加精**改为“博客中心”发出，`ref_type=blog_featured` 每帖一次。
> - **审核兜底补发**：P3 遗漏——待审评论因超时或审核失败被放行时没有补发通知；现在与通过审核一致，补发评论/回复/提及通知，帖子补发提及。
> - **关注未读**：`last_seen_following_at` 存在 `user_notification_preferences` 的 `seen:blog.following` 行（`updated_at`），不进开关列表；从未打开过关注页时按近 7 天计。顶栏每分钟轮询，计数按人进程内缓存 45 秒，打开关注页或新关注时清掉。关注页空态区分“还没有关注”与“暂无新帖子”。
> - **通知设置**：放在消息中心工具栏“通知设置”（个人中心 → 通知中心），LQ Switch 按事件键开关；“内容被处理”“申诉结果”为强制项只展示不可关，服务端也拒绝。
> - **内联回复**只在 LQ 消息中心（生产已开 `messages` 家族）出现；关注通知暂时链接到“我的”，P6 创作中心粉丝页上线后改链。深链高亮沿用 P4 的 `is-focused`。邮件：博客事件全部不发，已在《通知邮件降噪》补记。
> - 评审：代码评审与安全评审均无 CRITICAL/HIGH，未发现匿名/笔名身份泄露；采纳的 MEDIUM/LOW 修复见上（失效状态分型、聚合并发、通知副作用隔离、读帖不置读审核卡、关注计数缓存、提及合并与歧义、关注存在性与上限、首页/个人中心按 persona 渲染、回复上下文不泄露帖子存在、引用标签区分“你写的/回复的是”、关注数竞态）。
> - 测试：`tests/test_blog_p5_notifications.py`（36 项，覆盖 10.4 通知矩阵与评审回归）；顶栏 vitest 3 项。

### 6.1 事件目录

| 事件键 | 触发 | 接收人 | 聚合 | 发起人显示 | 深链 | 默认 |
|---|---|---|---|---|---|---|
| `blog.comment` | 一级评论 | 帖子作者 | 不聚合 | 发起人 persona | `?post=&comment=` | 开 |
| `blog.reply` | 楼中楼回复 | 被回复者 | 不聚合 | 发起人 persona | 同上 | 开 |
| `blog.thread_reply` | 楼中楼回复 | 帖子作者（非被回复者、非回复者本人时） | 同帖 1 小时内聚合为“你的帖子下有 N 条新回复” | `AvatarStack` | 帖子评论区 | 开 |
| `blog.like_post` | 赞帖子 | 帖子作者 | 同帖按自然日聚合；取消赞不撤销 | **只显示人数**（D11） | `?post=` | 开 |
| `blog.like_comment` | 赞评论 | 评论作者 | 同评论按自然日聚合 | 只显示人数 | `?post=&comment=` | 开 |
| `blog.mention` | 正文/评论 @用户 | 被提及者（须对帖子可见） | 不聚合 | 发起人 persona | 对应位置 | 开 |
| `blog.follow` | 关注我（实名或笔名） | 被关注者 | 按日聚合“N 人关注了你” | 关注者实名（D10） | 创作中心粉丝页 | 开 |
| `blog.followed_post` | 关注对象发帖 | 关注者 | **不推送**，见 6.4 | — | — | — |
| `blog.featured` | 加精 | 作者 | 同帖只发一次（去重键） | “博客中心” | `?post=` | 开 |
| `blog.hot` | 进入热门 | 作者 | 同帖一次（现有） | “博客中心” | `?post=` | 开 |
| `blog.moderation` | 限制、移除、恢复、自动移除、多人举报暂限 | 作者 | 不聚合 | “博客中心” | 创作中心作品行 | **强制** |
| `blog.appeal_result` | 申诉裁决 | 作者 | 不聚合 | “博客中心” | 同上 | **强制** |
| `blog.report_result` | 举报处理完毕 | 举报人 | 不聚合 | “博客中心” | 内容仍可见时到内容 | 开 |
| `blog.review_passed` | 审核通过（pending 超过 30 秒时） | 作者 | 不聚合 | “博客中心” | `?post=` | 开 |
| `blog.housekeeper_failed` | 管家回复失败 | 触发者 | 不聚合 | “管家” | 触发位置 | 开 |
| `blog.career_deadline` | 收藏的机会将截止（现有） | 收藏者 | 现有 | 现有 | 现有 | 开 |

通用规则：

- **自操作抑制**：发起人与接收人为同一 identity 时不发（含楼主匿名场景——楼主回复自己帖子不通知自己）。
- **可见性复核**：发送前确认接收人仍能看到目标内容；不可见则不发。
- **点赞不承载 persona**：点赞不是发言。在匿名帖里展示点赞人会让作者推断社交关系，而点赞人名单对作者价值很低，因此只显示人数。
- **邮件**：博客全部事件维持不发邮件，与《通知邮件降噪》一致；P5 完成后在该文档中补一条明确条目。

### 6.2 消息中心改造

- **聚合（通用能力）**：`message_center_notifications` 新增 `aggregate_key`、`aggregate_count`。写入时按 `(recipient_identity, aggregate_key)` 查找**未读**行：找到则累加计数、更新最新发起人与时间并保持未读；找不到则插入。应用层去重为主，PG 再加部分唯一索引兜底。所有博客事件改走 `_insert_notification_if_allowed` 通道（修复 N3、N8）。该能力放在 `message_center_service`，对其它业务通用，不在博客里另造。
- **结构化载荷**：通知行保存 `payload_json = {post_id, comment_id, event, actor_ref}`，**不保存渲染好的发起人姓名**。卡片标题在读取时按当前 persona 渲染：作者后来把帖子改为匿名，旧通知里的名字也随之变成“楼主”。这是隐私优先的取舍，代价是读取时多一次批量 persona 解析（同一页通知共用一次查询）。
- **匿名发起人**：发起人为 `anon/op_anon` 时，通知行的 `actor_identity` 置空，`actor_ref` 只存 `(post_id, alias_seed)`。消息中心据此**无法**发起私信或跳转主页（S7 的通知侧防线）。
- **类别**：`blog_comment`（评论、回复、楼中楼、提及）、`blog_like`（新）、`blog_follow`（新）、`blog_hot`、`blog_moderation`（新）、`blog_career`。用于筛选与静音。
- **加精发起人**（N7）：显示“博客中心”，不再是空白的“系统”。

### 6.3 深链与已读同步（N4、N5）

- 链接统一为 `/blog?post={id}&comment={id}`。博客前端：
  1. 读取 `comment`，请求 `/api/blog/posts/{id}/comments?around={comment_id}`，拿到该评论所在楼层及所在页；
  2. 渲染后滚动到 `#blog-comment-{id}`，以共享选中态（`data-lq-selected="true"`）短暂标记 2 秒，时长取 `--lq-motion-feedback-duration`，reduced-motion 下只保留静态标记；
  3. 目标评论已删除或不可见：评论区顶部显示 LQ `Alert`“你要找的评论已不存在”。
- **读帖即已读**：详情加载成功后，前端调用 `POST /api/message-center/read-by-ref {source:'blog', post_id}`，服务端把该用户与此帖相关的未读博客通知一次性置读。为此通知表增加可索引列 `ref_post_id`（不依赖 JSON 表达式索引，SQLite 与 PG 同构）。
- **内容失效**：帖子删除/移除、评论删除时，相关通知置 `target_state='gone'`；消息中心卡片显示“内容已不可见”，去掉跳转。

### 6.4 关注更新（不推送）

关注对象发新帖不发通知，避免 200 人规模下的扇出写放大和打扰。改为：

- 博客导航“关注”标签显示未读数 `Badge`：`count(关注对象 published_at > last_seen_following_at 的帖子)`；
- 进入关注页后更新 `last_seen_following_at`（存 `user_ui_preferences`）；
- 导航栏“博客”入口：有关注未读时显示“关注更新 N”，否则显示“今日新增 N”。
- 关注页空态（F9）：精选区与热榜在关注页隐藏；空态用 LQ `Empty`，文案“还没有关注任何作者或板块”，动作“去发现”。

### 6.5 通知设置（N6）

- 个人中心 → 通知设置新增“博客”分组，按事件键开关；强制类不显示开关。
- 存 `user_notification_preferences(identity, event_key, enabled)`，写入前在 `_insert_notification_if_allowed` 内检查。
- 默认值见 6.1 “默认”列。

---

## 7. 评论、回复与消息中心闭环（改进项 6）

> **P4 实施记录（2026-10-12，已发布 `1fb52483` / release `20261012-213055-9d68ecbcbe05`）**。代码：`classroom_app/services/blog_comment_service.py`（楼层、回复分页、编辑、软删、单条视图）、`classroom_app/routers/blog_comments.py`（`GET /api/blog/posts/{id}/floors?page&around`、`GET .../floors/{root}/replies?offset`、`PUT /api/blog/comments/{id}`、`POST /api/blog/comments/{id}/delete`、`GET /api/blog/comments/{id}`）；前端 `static/js/blog.js`。与下文原方案的差异与补充：
> - **楼层在 SQL 中分页**（每页 20，按时间升序）；每层用 `ROW_NUMBER` 窗口预载前 3 条回复，其余“展开 N 条回复”每次 20 条。被删除/被处置的楼层只在下面仍有可见回复时显示为无作者、无内容的占位（文案区分作者删除/帖子作者删除/管理员移除）。合成 1000 条评论的楼层接口 P95 < 300ms（单测实测）。
> - **深链** `?comment=` → `around` 计算所在页并把目标回复一并载入；落在后面页时显示“加载更早的评论”。看不到的评论（被处置、他人待审）深链回到第一页，`GET /api/blog/comments/{id}` 对其返回 404，也不能被回复。
> - **“回复 @X”**：X 是被回复评论的公开面孔；回复楼层本身不加标签；被回复的评论已不可见时写“回复 一条已处理的评论”。
> - **编辑**：作者 15 分钟内可改正文（图片与表情不变），`content_revision` 乐观锁；重新过 L1 并按新修订号排 AI 复审，清空上一次的处置原因与说明；被处置的评论不能编辑。编辑会让尚未执行的 @管家 回复因原文变化而放弃（已知行为）。
> - **软删**：`status='deleted'` + `deleted_by_role`，回复保留；删除他人评论（帖子作者 / 博客管理员）必须写原因并通知评论者，管理员删除写审计日志。旧 `DELETE /api/blog/comments/{id}`（Agent 能力，源码已钉住未改）不能携带原因，因此只能删自己的评论；能力说明已更新为“软删、回复保留”。软删不清点赞（`like_count` 保留）。
> - **旧读取接口**（`GET /api/blog/posts/{id}/comments`、帖子详情 `_comments`）改为同一楼层模型，但保留整层回复（最多 100 层、每层 200 条），避免 Agent 读取丢回复；网页改用 `/floors`。
> - **前端**：评论只保留“赞”“回复”两个直达按钮，其余（复制链接、编辑、举报、处理、删除）在 LQ Menu 里；回复框就地移入该楼层，发送/取消后归位；新评论按服务端返回的对象就地插入（不重拉详情，重复提交不重复插入，未载入的后续页加载时插到本地新楼层之前）；删除就地变占位或移除；评论区加载用请求令牌，切换帖子不会卡在“正在加载评论”。
> - **未在 P4 做**：消息中心内联回复（§7.5，归 P5）、联系人卡学号隐藏（§7.6）、评论按热度排序、软删 30 天物理清理。
> - 测试：`tests/test_blog_p4_comments.py`（16 项，含 1000 条性能与评审回归）；Agent 删除能力测试按软删语义更新。

### 7.1 楼层结构（R1、R2、R3）

采用“两级楼中楼”：

- 一级评论即楼层；楼层内所有回复**平铺**，不递归。每条回复显示“回复 @{被回复者 persona}”。
- 数据：写入时若 `reply_to` 属于楼层 X，则 `root_comment_id = X`。深度天然为 2，渲染不递归，彻底消除 R1 的 500 风险，也不再无限右移。
- 分页：一级评论在 SQL 中分页（每页 20，按时间或热度）；每个楼层默认预载前 3 条回复，其余“展开 N 条回复”按需加载（每次 20）。接口返回 `total`、`has_more`；列表底部显示“加载更多评论”（修复 R3 的静默截断）。
- 迁移：现有多层嵌套评论沿祖先链回填 `root_comment_id`，`reply_to_comment_id = parent_comment_id`（见 8.2）。

### 7.2 楼层内的身份显示

- 评论头部：`Avatar` + persona 显示名 + 角色标签（如有）+ “作者”徽标（`is_op=true` 且非匿名帖时；匿名帖楼主本身叫“楼主”，不再加徽标）+ 时间 + “已编辑”。
- “回复 @X”中的 X 是被回复评论的 **persona**（匿名同学·青柚 / 楼主），永远不是真名。
- 点击 persona：`real/pen` 打开作者浮窗；`anon/op_anon/assistant` 渲染为纯文本，不是禁用的按钮（合同：不用 div 模拟按钮，也不放无用的可聚焦元素）。

### 7.3 评论操作

| 操作 | 谁可以 | 说明 |
|---|---|---|
| 回复 | 帖子可见且评论开放者 | 评论框就地展开在该楼层下方，而不是页面底部 |
| 编辑 | 评论作者，发布后 15 分钟内 | 显示“已编辑”，重新过 L1/L2 |
| 删除 | 评论作者；帖子作者（仅限自己帖子下）；博客管理员 | 软删（4.2）；帖子作者删除他人评论须选一句原因，自动通知评论者 |
| 举报 | 非评论作者 | 见 5.4 |
| 点赞 | 所有人 | 幂等（P7） |
| 复制链接 | 所有人 | 复制带 `comment=` 的深链 |

每条评论只保留“回复”“赞”两个直接按钮，其余放进 LQ `Menu`（由 LQ.layer 管理焦点与关闭），降低视觉噪声。评论图片接入统一灯箱 `data-ls-lightbox`（R5）。

### 7.4 发表后的局部更新（R6、F1）

- 发表评论成功后，把服务端返回的评论对象插入对应楼层并滚动到它，**不重拉整个详情**。
- 置顶、加精、关闭评论等帖子级操作只更新对应徽标与按钮。
- 所有列表状态（`state.posts`、`myPosts`、`bookmarkPosts`、`detailPost`）通过统一的 `patchPost(id, fields)` 更新；`loadDiscovery()` 只更新侧栏，**不再重绘信息流**（修复 F1）。

### 7.5 消息中心内回复（R7）

- 博客通知卡展开后：被回复的原文引用（≤3 行，只读 Prose 槽）+ 对方的新评论 + 内联回复框（共享 Composer）。
- 发送时调用同一个博客评论接口，`reply_to_comment_id` 为对方评论。persona 规则与 3.4 完全一致：
  - 接收者是该匿名帖作者 → 自动以“楼主”回复，回复框上方一行说明；
  - 否则显示 实名 / 笔名 / 匿名 选择，默认取本帖上次选择。
- 发送成功后通知置读，卡片显示“已回复”与“去帖子查看”。
- 帖子已关闭评论、内容已删除、或接收者已看不到该帖时，不显示回复框，替换为说明文字。
- 小程序：本期只修通知深链（点击打开 H5 博客详情），原生博客页不在本计划内（D12）。

### 7.6 私信入口

- 作者浮窗中的“私信”只对 `real` 显示（3.4、D4）。
- 消息中心联系人卡：学生之间的跨班联系不再显示学号（教师仍可见）。这是消息中心改动，需同步回归私信模块测试。

---

## 8. 数据模型与迁移（P1 一次完成）

> **P1 实施记录（2026-10-12）**，与下文原方案的差异（均为有意调整）：
> - **不改写旧 `status`**：下文 8.2 把 `moderated/hidden` 映射为 `published + limited`，但 P1 时所有可见性判断仍读 `status`，提前改写会让已隐藏帖子在 P3 上线前变成公开。因此 P1 只**新增列 + 回填 + 双写**：隐藏帖 `moderation_state='limited'`（原因码 `other`、说明为“系统升级前被处理”），`status` 保持原值；P3 切换读路径后再收敛 `status`。
> - **`nickname`→`pen` 改名推迟到 P2**（与 persona 一起做；P1 不改存储值，评论的 `author_display_mode` 沿用帖子的取值 `real_name/nickname/anonymous`）。
> - **通知表**已有 `metadata_json` 与 `read_at`，故不新增 `payload_json`；只新增 `aggregate_key / aggregate_count / ref_post_id / target_state`。
> - **`content_revision` 已在 P1 生效为编辑锁**（只有作者保存会推进；评论、置顶、加精不再触发 409），彻底消除 C1；`expected_updated_at` 仍兼容 Agent 等旧调用方。
> - **`published_at` 已在 P1 生效**：首次发布写入，“最新”排序改用 `COALESCE(published_at, created_at)`（修复 C3 的排序部分）。
> - **新表**在运行时模块 `classroom_app/db/schema_blog_closed_loop.py`（engine-aware，两套启动路径均调用；SQLite 博客 schema 也会显式以 SQLite 方言建表，保证 PostgreSQL 索引采集的内存库一致）。PostgreSQL 端必须**先建表再采集索引**——首次演练正因顺序相反而失败，已修正。
> - **回填** `backfill_blog_closed_loop_data` 每次启动幂等执行；`tools/db/migrate_blog_closed_loop.py` 提供 dry-run / apply 报告。`blog_author_daily_stats` 与通知 `ref_post_id` 属派生数据，分别由 P6 / P5 回填。

### 8.1 变更清单

> 改动 `classroom_app/db/*.py` 会触发原生 PostgreSQL 演练门禁，必须重做演练再发布（参见部署流程记录）。新表按引擎无关的运行时 schema 写入对应模块；运行时建表的模块加入 `RUNTIME_ENSURED_SCHEMA_MODULES` 豁免。

**blog_posts**：新增 `moderation_state, moderation_reason_code, moderation_note, moderated_by, moderated_at, content_revision INTEGER NOT NULL DEFAULT 1, published_at, deleted_at, allow_anonymous_comments INTEGER NOT NULL DEFAULT 1`；`status` 取值收敛为 `draft/published/deleted`；`author_display_mode` 的 `nickname` 更名为 `pen`。索引 `(status, moderation_state, published_at DESC)`。

**blog_comments**：新增 `moderation_state, author_display_mode, author_display_snapshot, root_comment_id, reply_to_comment_id, content_revision, edited_at, deleted_at, deleted_by_role`。索引 `(post_id, root_comment_id, created_at)`、`(post_id, status, created_at)`。

**blog_follows**：新增 `target_mode`（`real/pen`）。

**blog_moderation_logs**：新增 `reason_code, note, target_persona_kind, extra_json`；动作枚举扩展为 `limit / remove / restore / auto_remove / auto_flag / auto_limit_reports / identity_reveal / persona_change / appeal_open / appeal_decide / comment_remove / comments_toggle / report_resolve`。

**message_center_notifications**：新增 `aggregate_key, aggregate_count, payload_json, ref_post_id, target_state`；索引 `(recipient_identity, ref_post_id, is_read)`、`(recipient_identity, aggregate_key, is_read)`。

**新表**

| 表 | 用途 |
|---|---|
| `blog_content_reviews` | AI 审核回执（5.1） |
| `blog_comment_media(comment_id, file_hash)` + `(file_hash)` 索引 | 替代 `LIKE '%hash%'`（P3） |
| `blog_appeals` | 申诉（5.6） |
| `blog_safety_terms` | L1 词表（5.1） |
| `blog_author_daily_stats(identity, mode, day, views, readers, completed, likes, comments, followers_delta)` | 创作中心概览（9.4） |
| `user_notification_preferences(identity, event_key, enabled, updated_at)` | 通知设置（6.5，通用） |

### 8.2 存量数据映射（幂等迁移）

| 旧数据 | 新值 |
|---|---|
| `status='moderated'` 或 `'hidden'` | `status='published', moderation_state='limited', moderation_reason_code='other', moderation_note='此帖在系统升级前被处理，原因未记录。如有疑问可申诉。'` |
| 其它 `published` | `moderation_state='passed'`（存量不补审） |
| `draft` | `moderation_state='none'` |
| `published_at` | 已发布帖取 `created_at` |
| `content_revision` | `1` |
| `author_display_mode='nickname'` | `'pen'`（代码常量同步） |
| 评论 `author_display_mode` | `'real'` |
| 评论 `root_comment_id` / `reply_to_comment_id` | 沿 `parent_comment_id` 上溯到一级评论；`reply_to = parent_comment_id` |
| 评论图片 | 从 `content_md` / `attachments_json` 解析回填 `blog_comment_media` |
| `blog_author_daily_stats` | 从 `blog_post_views`、`blog_likes`、`blog_comments`、`blog_follows` 按日回填 |

迁移脚本 `tools/db/migrate_blog_closed_loop.py`：默认 dry-run 输出统计，`--apply` 才写；先在原生 PG 演练库上跑，统计与预期一致后才进入发布。

### 8.3 兼容

- P1–P4 期间 API 同时提供旧语义的 `legacy_status` 字段一个发布周期，便于前端分批切换；P7 删除。
- Agent 平台 `agent_platform_request_blog.py` 与写入路径同步改为新字段，更新 `docs/agent-capability-matrix.*`；Agent 写入同样走 L1/L2。
- 小程序当前不消费博客 API，无兼容负担。

---

## 9. 阶段划分与依赖

### 9.1 依赖图

```
P0 止血（不改表，可单独发布）
 │
P1 数据模型（一次迁移 + 原生 PG 门禁）
 ├──► P2 身份与头像 ──┬──► P4 评论与回复 ──┐
 │                    │                    ├──► P5 通知 ──┐
 ├──► P3 内容安全 ────┼────────────────────┘              │
 │                    └──────────────────────────────────►├──► P6 创作中心
 └──► P7 性能与 LQ 收敛（与 P2–P6 并行，最后清理兼容字段）◄─┘
```

关键因果：

- **P2 必须先于 P4、P5**：评论与通知的显示名都来自 persona。先做通知再做 persona，会产生一批写死真名的通知，事后要回改。
- **P3 必须先于 P6 的作品管理**：作品行要展示 `moderation_state` 与申诉；用旧 `status=moderated` 先做一版再改是浪费。
- **P1 一次做完全部表结构**：每次改 `classroom_app/db/*.py` 都要重做约 6 分钟的原生 PG 演练和一次发布，拆成多次迁移代价更高。
- **C1 分两步**：P0 先在前端止血（409 时给出“载入服务器版本 / 保留我的版本覆盖”两个选项，并清除本地陈旧版本号）；P1 引入 `content_revision` 后从根上消除冲突来源。
- **P0 的 I5 是过渡实现**：在序列化层对“评论者 = 匿名帖作者”做掩码；P2 的 persona 服务上线后由楼主规则取代。

### 9.2 P0 止血（不改表）

> **状态（2026-10-12）：✅ 已实现、验收并发布（提交 `c113b74b`）。** 与本节原方案的差异（均为有意调整）：
> - **S1 登出**：没有把 `/logout` 改为 POST（会牵动全站 7 处导航宏/菜单）。改为依据浏览器 Fetch Metadata：仅顶层、非跨站的导航（`Sec-Fetch-Dest: document`）才注销；`<img>/<iframe>` 发出的请求一律 204 忽略；无该请求头的旧浏览器保持原行为。同时博客正文的“不可信内容”清洗策略剥离指向本站 `/logout` 的链接与任何同源非媒体图片。
> - **S2 审核锁的状态码**：更新接口对“管理员已隐藏”返回 **403**（权限），409 只用于版本冲突，编辑器据此区分处理；置顶/加精/隐藏作用于草稿时返回 409。
> - **C9 幂等**：未引入 `request_id` 列（属 P1 改表），改为服务端“同作者、同内容、10 秒内”去重 + 前端进行中锁。真正并发的两次请求在 P7 把 `async def` 改为 `def` 后需由 P1 的幂等键兜底——已记入 P1。
> - **S8 图片元数据**：除 JPEG 外，PNG/WebP 的 EXIF/XMP 也一并去除（动图保持原样）。
> - **M2 管家失败**：失败改为终态 `failed_final` + 仅对发起人的站内通知；作者重新保存帖子后才会重试（同时重新激活已结束的调度任务）。
>
> 证据：`tests/test_blog_p0_hardening.py`（39 项）；博客/Agent/调度/认证/通知相关 30 余个测试模块全部通过；全量单测 3964 项中 26 项失败与未改动的干净工作树完全一致（预先存在）；`blog-composer-revision` 组件测试通过；浏览器中以第三方学生、作者、教师三种身份逐项复核。

| 项 | 修复 | 涉及 |
|---|---|---|
| S1 | 图片 src 白名单（`/api/blog/image/`、`/static/`、`data:image/`、https 外链）；`/logout` 改 POST，GET 返回“确认退出”页而不是直接退出；全站登出入口改为表单提交 | `markdown_runtime.js`、`auth.py`、导航栏与个人中心 |
| S2 | `update_post` 在当前为 `moderated/hidden` 时拒绝改 status | `blog_service.py` |
| S3 | `_can_view_post` 的管理员分支排除他人草稿 | 同上 |
| S4 | 隐藏/置顶/加精只作用于 published；加精去重、不发给自己 | 同上、`blog_notifications.py` |
| S5 | `users-search` 收窄范围、去学号匹配、至少 2 字；拆掉 `? IS NULL`（P5） | `routers/blog.py` |
| S9 | 清洗器去掉用户内容的 `id/class`；目录锚点改用 `data-toc-id` | `markdown_runtime.js`、`blog.js` |
| I1 | 匿名/管家头像改用静态资源与前端首字（persona 之前的过渡） | `blog_service.py`、`blog.js` |
| I5 | 评论序列化：评论者 = 匿名帖作者时显示“楼主”，去身份、去头像；通知文本同样处理 | `blog_service.py`、`blog_notifications.py` |
| I6 | 管家对匿名/昵称触发者不注入画像 | `blog_ai_service.py` |
| M2 | 管家失败不发公开评论 | 同上 |
| C1 | 409 冲突界面（LQ `Conflict`）+ 清除陈旧本地版本号 | `blog.js` |
| C2 | 编辑已发布帖时隐藏“保存草稿”，在更多菜单提供“撤回为草稿”（`LQ.confirm`） | `blog.js`、`blog.html` |
| C9 | 发布/评论进行中禁用按钮 + `request_id` 幂等键 | `blog.js`、`routers/blog.py` |
| F1 | 点赞/收藏改为 `patchPost`；`loadDiscovery` 不重绘信息流 | `blog.js` |
| F2 | 加载更多进行中忽略重复点击；后退恢复为 `limit=page*20` | `blog.js` |
| P4 | 搜索改 `LIKE ? COLLATE NOCASE` + 通配符转义 | `blog_service.py` |
| P7 | 点赞/收藏改 `ON CONFLICT DO NOTHING` 或捕获完整性错误返回当前状态 | `blog_service.py` |
| P8 | `author_id` → `author_user_pk` | `platform_knowledge_service.py` |
| S8 | 上传先校验后落盘、像素上限、去 EXIF | `routers/blog.py` |

### 9.3 P1–P7 交付物

| 阶段 | 后端 | 前端 | 验收要点 |
|---|---|---|---|
| P1 | 第 8 节全部表结构、迁移脚本、兼容字段 | 无 | 原生 PG 演练通过；迁移 dry-run 统计符合预期；既有博客测试全过 |
| P2 | persona 服务、博客头像路由、全部序列化改用 persona、作品集/关注/搜索隔离、管家匿名保护 | 扩展 LQ `Avatar`（kind/seed/label）；发帖与评论身份选择卡；笔名设置浮层联动；作者浮窗改用共享 Popover | 第 10.2 节身份矩阵逐格通过 |
| P3 | 状态机、L1、L2 任务与回执、管家迁网关与限额、举报即处置、申诉、审核台与审计 | 待审横幅；作者端处置说明与申诉；审核台；评论举报入口 | 第 10.3 节审核场景通过；AI 全部 stub，无付费调用 |
| P4 | 楼层分页接口、`around` 定位、评论编辑、软删、`blog_comment_media` | 楼层 UI、就地回复、局部更新、灯箱、评论菜单 | 合成 1000 条评论的帖子，详情接口 P95 < 300ms |
| P5 | 事件目录、聚合、偏好、读帖即已读、内容失效、关注未读数 | 消息中心博客卡片与内联回复；通知设置博客分组；深链高亮 | 第 10.4 节通知矩阵逐行通过 |
| P6 | 创作中心聚合接口、每日统计汇总任务、粉丝/关注列表、作者主页、笔名简介 | 创作中心全部视图、作者主页、编辑器改进 | 第 10.5 节 |
| P7 | 徽章批量缓存、`async def` 改 `def`、summary 合并为一次、移除兼容字段 | `blog-paper.css` 材质收敛、原生 confirm/dialog 替换、可访问性、正文墨色、移动端 | LQ 源码审计无新增私有材质；浏览器矩阵通过 |

### 9.4 个人创作中心（改进项 5，P6 详细设计）

入口：博客导航“我的”改名为“创作中心”；个人中心概览增加“我的创作”卡片链接到这里。路由 `?view=studio&tab=...`，旧 `?view=my-posts` 重定向。

子视图用 LQ `Segment` 切换：

**1. 概览**

- 指标（LQ `Insight` 统计工厂，不用私有大数字样式）：累计阅读、近 7 天阅读、完读率、获赞、评论、粉丝。`null` 表示未知，`0` 走空态（遵守 Insight 合同）。
- 近 30 天阅读趋势：迷你折线，颜色取主题令牌，附可访问的数据表替代。
- 待处理（`ActionEntry`）：被处置的作品、申诉进度、未回复的评论数。
- 口径：阅读 = `blog_post_views` 去重会话；完读 = `completed=1`；按身份 + 模式分开统计（笔名与实名的数据不合并，避免笔名主页反推实名）。每日 02:00 由调度器增量汇总到 `blog_author_daily_stats`，当日数据实时补齐，避免实时扫描。

**2. 作品**

- LQ `List` + `Row`，每行：标题、板块、署名徽标（实名/笔名/匿名，`Chip` status 类）、状态（LQ `Status`：草稿 / 审核中 / 已发布 / 仅自己可见 / 已移除 / 申诉中）、发布时间与“已编辑”、阅读/赞/评论。
- 行内操作：编辑、查看、发布 / 撤回为草稿、删除（`LQ.confirm` danger），处置态额外“申诉”。
- 处置态行展开显示 LQ `Alert`：原因码中文说明 + 管理员备注 + 处置时间 + 申诉入口或进度。
- 筛选：全部 / 已发布 / 草稿 / 需要关注（审核相关）；排序：最新 / 阅读 / 互动。

**3. 草稿箱**

- 服务器草稿与本机未提交的本地草稿合并列出，本地草稿标“仅本机”，可“恢复”“丢弃”。
- 打开草稿时，若本地与服务器都存在且修订号不同，弹出 LQ `Conflict` 比较选择。

**4. 互动**

- 收到的：别人在我帖子下的评论与对我的回复（时间倒序），每条可就地回复（与 7.5 同一组件）、赞、跳转。
- 我发出的：我的评论（含被处置的，显示原因）。
- 匿名规则：作为楼主收到的评论照常显示；以匿名发出的评论标注“你以 匿名同学·青柚 发表”，帮助本人分辨。

**5. 粉丝与关注**

- 粉丝：`Avatar` + 实名（D10）+ 关注时间 + 回关；笔名的粉丝单独一栏。
- 我的关注：作者 / 笔名 / 板块 / 标签四类，可取消；在此提供标签关注入口（C6）。

**作者主页**（C8）：`?view=author&ref={opaque}`。显示 `Avatar`、persona、简介（实名用个人简介；笔名用新增的“笔名简介”，≤60 字，在个人中心设置）、公开作品、粉丝数、关注按钮。笔名主页只含笔名帖。

**编辑器改进**（C1–C3、C7、C10）

- 编辑已发布帖：主按钮“更新”“取消”；“撤回为草稿”放在更多菜单。
- 本地自动保存带 `base_revision`；打开时比较，冲突用 `Conflict`。
- 草稿首次发布写 `published_at`，按发布时间进入信息流。
- 附件接口补 `file_hash`，编辑时图片预览正常。
- 可见范围不完整时阻止发布，并在字段旁显示错误。
- 发布后若进入 `pending`，跳转详情并显示审核横幅。
- 编辑器外壳迁移到 LQ `EditorShell`。

### 9.5 前端液态玻璃要求（贯穿 P2–P7）

| 区域 | 使用的共享组件 | 领域 CSS 只负责 |
|---|---|---|
| 页头、板块导航、排序 | PageHead、Tabs、Segment、Badge | 版心宽度、栅格 |
| 信息流卡片 | Card、Avatar、Chip、Button(ghost) | 封面比例、两栏/单栏布局 |
| 详情正文 | Prose 内容槽（正文用 `--lq-material-ink`，修复 F8） | 46rem 栏宽、行高 |
| 评论区 | List/Row、Avatar、Menu、Composer、Status | 楼层缩进（仅一级） |
| 发帖 / 编辑 | Dialog/EditorShell、Field、Input、Textarea、Selection 选择卡、Upload、Conflict、SaveStatus | 字段栅格 |
| 作者浮窗 | Popover（`createDomainPopoverSystem`） | 无 |
| 举报、确认、申诉 | Dialog、`LQ.confirm` | 无 |
| 创作中心 | Segment、Insight、List/Row、Status、Alert、ActionEntry、Empty | 面板栅格 |
| 审核台 | Tabs、Card、Status、Dialog、Button(destructive) | 双栏布局 |

可读性与美观：

- 文字颜色只取 `--lq-material-ink / muted / accent`；正文、评论正文一律 `ink`，时间与计数用 `muted`，在真实背景合成后对比度 ≥4.5:1。
- 长文阅读面使用 `content` 材质且透明度足够，不依赖模糊维持可读性；嵌套层不重复模糊。
- 审核相关的语义色（审核中、仅自己可见、已移除）只用 `Status/Alert` 的 fill/ink 配对。
- 动效全部来自共享动效变量，删除 `blog-paper.css` 中的第二套悬浮上浮与入场动画；遵守 reduced-motion。
- 移动端：详情返回条只保留图标 + “返回”，二级说明在 ≥768px 才显示；评论操作菜单在触屏用 Sheet；全站导航占高问题另开任务（不在本计划内改导航）。

---

## 10. 验收矩阵

全部在隔离合成运行环境（P03 SQLite）完成功能验收；涉及 SQL 方言的项在原生 PG 演练库再跑一次；AI 一律用 stub transport；不连接真实业务库。

### 10.1 P0 回归

- `![x](/logout)` 不再发出请求；登出改 POST 后全站登出入口可用。
- 作者对被隐藏帖 `PUT status=published` 返回 409。
- 教师 GET 他人草稿返回 404。
- 列表点赞后 3 秒内数字不回弹；双击发布只产生一篇；双击加载更多不跳页。
- 匿名帖作者在自己帖下评论，评论与通知中不出现真名。

### 10.2 身份矩阵（P2）

对 {实名帖, 笔名帖, 匿名帖} × {作者本人, 同班同学, 外班同学, 教师, 审核台} × {卡片, 详情头, 评论, “回复 @”, 活跃作者, 作者浮窗, 作者主页, 全局搜索, 关注流, 通知卡, 消息中心内联回复, 作品集候选} 逐格断言显示名、头像形态、是否含 identity/user_pk、可否点开/关注/私信。服务端序列化单测覆盖全部组合；浏览器抽测 3 条主路径。

专项：

- 匿名帖作者评论后，任何接口响应体全文不出现其 identity、user_pk、真名、头像哈希（全文扫描断言）。
- 同帖同匿名评论者代号稳定；跨帖不同；撞词时追加序号。
- 管家回复匿名帖：提示词不含画像块（stub transport 断言）。
- 头像：匿名、管家、已删除账户、未上传头像、上传后更换头像五种情况在所有出现位置一致。

### 10.3 审核场景（P3）

- L1 block → 422 且未落库；L1 flag → pending，他人不可见。
- L2 返回 high → removed + 作者通知 + 日志；medium → flagged + 进队列；超时 → 10 分钟后 flagged 且可见。
- 修订号变化后迟到的审核结果被丢弃。
- 3 人举报 → 自动 limited + 作者通知；管理员驳回 → 恢复 + 举报人通知。
- 申诉一次后入口消失；裁决通知送达。
- 查看真实作者写入审计日志。
- 管家：超出每日上限不发公开评论；失败只发私人通知。

### 10.4 通知矩阵（P5）

6.1 表逐行验证：触发 → 接收人 → 标题（含 persona）→ 深链定位到评论并高亮 → 读帖后置读 → 删除内容后卡片显示“内容已不可见”。聚合：10 次点赞只有 1 条未读通知且计数为 10；已读后再来 1 次赞重新浮现为未读。静音后不再产生该类通知。自操作不产生通知。

### 10.5 创作中心（P6）

- 概览数字与合成数据手算一致；笔名与实名数据分开。
- 作品行操作全部可用，处置原因与申诉可见。
- 本地草稿与服务器草稿冲突时出现比较选择，选择后不再 409。
- 作者主页：笔名主页不出现实名帖。

### 10.6 前端通用（每阶段）

按 LQ 合同第 6 节：桌面 1440 / 平板 768 / 手机 375；浅/深场景；长文本、空、错误、无权限；键盘全流程（标签、菜单、对话框、浮窗、评论回复）；焦点归还；reduced-motion；对比度 ≥4.5:1（在真实场景背景合成后测）；backdrop-filter 宿主数量不增加；正式构建资源图更新。

---

## 11. 待决事项（需用户确认）

| 编号 | 问题 | 选项 | 推荐 |
|---|---|---|---|
| D1 | 帖子审核期间他人是否可见 | A 先审后发（最多 10 分钟，超时放行）/ B 先发后审 | **A**：以学生为主的课堂平台，事后撤回很难消除影响；10 分钟兜底保证可用 |
| D2 | AI 审核成本与优先级 | 估算：每条约 0.5–1.5k token，日均 50 帖 + 300 评论，deepseek-flash 约 ¥0.5–1/天，含图走豆包 lite 另计。P1（不受周预算延期）或 P2 | **P1**，另设全局日上限 `BLOG_SAFETY_DAILY_LIMIT`（默认 2000 次），超限退化为仅 L1 且全部 flagged |
| D3 | 全站头像默认 SVG 改为角色字形（不用真名首字） | A 全站改 / B 仅博客与消息中心不再用该 SVG | **B** 先行，A 另议（影响其它页面观感） |
| D4 | 笔名作者能否被私信 | A v1 不可 / B 做化名会话 | **A** |
| D5 | 教师评论是否走 AI 审核 | 只走 L1 / 全量 | **只走 L1** |
| D6 | 评论是否支持申诉 | 支持 / 不支持（15 分钟内可编辑或重发） | **不支持** |
| D7 | 博客管理员范围 | 超管 / 超管 + 授权教师 / 所有教师 | **超管 + 授权教师**，普通教师保留紧急限制 |
| D8 | 揭示匿名作者时是否通知作者 | 通知 / 不通知（审计可查） | **不通知**；发帖说明已告知“老师可查看” |
| D9 | 申诉是否复用通用审批流 | 复用 / 审核台内轻量实现 | **轻量实现** |
| D10 | 关注行为是否匿名 | 关注者实名出现在粉丝列表 / 可匿名关注 | **实名**（匿名关注容易被用于跟踪） |
| D11 | 点赞通知是否显示点赞人 | 显示 / 只显示人数 | **只显示人数** |
| D12 | 小程序博客 | 本期只修深链 / 做原生页 | **只修深链** |
| D13 | 匿名评论是否默认开放 | 所有帖开放 / 由帖子作者决定（默认开） | **由帖子作者决定，默认开** |

---

## 12. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 迁移把历史 `moderated` 帖的原因记为“未记录” | 作者看到语焉不详的原因 | 文案说明“系统升级前被处理”，并开放一次申诉 |
| persona 实时解析增加读取开销 | 列表接口变慢 | 一页最多 20 帖 + 60 评论，一次批量查询；徽章 TTL 缓存；P4 验收含性能指标 |
| AI 审核误判 | 正常内容被移除 | 只有高风险才自动移除；中风险只进队列；申诉闭环；审核台统计命中与推翻率，每月复盘词表与提示词 |
| AI 故障 | 帖子卡在审核中 | 10 分钟放行 + 全局日上限退化 |
| 通知聚合在 SQLite 与 PG 行为不同 | 测试通过、线上重复 | 应用层去重为主、PG 部分唯一索引兜底；在原生 PG 演练库跑聚合测试 |
| `/logout` 改 POST 影响全站 | 某处登出失效 | P0 内找全部调用点并浏览器回归；GET 返回确认页而不是 404 |
| 改署名模式后通知文本随之改变 | 用户困惑“谁发的” | 这是隐私优先的有意取舍；在模式切换确认框中说明 |
| 匿名代号密钥泄露 | 可跨帖关联匿名者 | 密钥只在环境变量；泄露时轮换（会改变历史代号，属可接受代价） |
| 主工作树常有其它会话的未提交工作 | 发布夹带他人改动 | 按部署流程从冻结 worktree 发布 |

---

## 13. 文档与记录

- 本文是博客闭环改造的**执行真源**；每阶段完成后在第 9 节对应行标注 ✅、发布号与提交号。
- P2 完成后更新 `docs/lq-components.md` 的 Avatar 参数表与组件审计。
- P3 完成后在 `docs/ai-and-agent-engineering-standard.md` 的路由矩阵与真源索引中登记 `content_safety_review`。
- P5 完成后在 `docs/notification-email-policy-2026-09-18.md` 补充博客事件“不发邮件”的明确条目。
- 记忆文件中关于博客皮肤“暗色模式不适用”的说法已过时（现有场景色调机制），P7 时更新。
