# 群聊性能诊断与改进计划（2026-10-30）

> 现象：好友聊天"人越多越卡"，打开群时更卡，群越大越卡。
> 本文是诊断结论 + 改进计划。**P0-1、P0-2、P0-3 已于 2026-10-30 施工并复测**（实测见 §4「P0-1 / P0-2 施工结果」与「P0-3 施工结果」）；P0-4 及以后尚未动工。

---

## §0 结论摘要

不是渲染慢，也不是动画/毛玻璃的问题（实测开群全过程只有 1 个 backdrop-filter 宿主）。真正的原因是**每条群消息的成本双向随人数线性增长并相乘**，以及**打开群时无条件重传、重建整份名册**。

按影响排序：

| # | 根因 | 量级证据 | 位置 |
|---|------|----------|------|
| **R1** | 一条群消息 = 每成员 2 条通知 UPSERT（在发送事务内、持会话锁） | N=5→200：**29 → 419 条 SQL**，1.0 → 13.3 ms（SQLite 本地）；**P0-2 后 23 条，与 N 无关** | `social_notification_service.py:123-176` + `notification_delivery.py:214-412` |
| **R2** | 每个收件人客户端收到任意消息都 `refreshCounts()` → `/conversations` + `/friends/requests` | `/conversations` 在 40 私聊时已 **99 条 SQL**（每私聊 2 条）；**P0-1 后群消息不再触发重读** | `social_client.js:179-204`、`227-262`；`social_conversation_service.py:360-414` |
| **R3** | R1 × R2 相乘：200 人群、100 在线时，一条消息 ≈ **200 + 100×~100 = 约 2 万条 SQL**，2 核 VPS 直接饱和 | 见 §3.4 放大模型 | — |
| **R4** | 打开群必然重拉全量成员表并全量重建 DOM | payload **2.3 → 19.7 → 77.6 KB**；节点 **1734 → 2946 → 6999** | `social_group_service.py:216-257`；`chat_sidebar.js:221` |
| **R5** | 群成员/资料变更广播后，**每个在线成员各自**重拉完整群详情 + 重建名册 | 3 次 `group_changed`：N=200 时 **CPU 114–234 ms / 布局 12–27 ms**，每次附带一次 77 KB 拉取；**P0-3 后合并为 1 次 304、不重建名册，CPU 3–72 ms** | `chat_modal.js:656`、`515`；`social_group_service.py:171-173` |
| **R6** | 一次开群就发 4 次 `/conversations`、2 次 `/friends/requests`（热开也有 4 个请求） | 冷开 **15 个请求**，热开 **7 个** | 实测请求序列见 §2.2 |

**一句话**：群消息的**扇出成本**（服务端按人写通知、客户端按人拉全量列表）没有被任何批处理或增量机制吸收，人数一多就变成 N²；而开群的**名册**既没有缓存也没有增量，每次都整份往返、整份重建。

---

## §1 测量方法（隔离环境，不碰真实业务库）

| 项 | 做法 |
|----|------|
| 客户端 | Playwright + 不可变构建产物 + 桩 `WebSocket` + `page.route` 全量伪 `/api/*`；CDP `Performance.getMetrics` 取 TaskDuration/LayoutDuration/ScriptDuration；MutationObserver 计数；`longtask` + rAF 最大帧。脚本 `.codex-temp/probe_social_group_open.cjs`，N ∈ {5, 50, 200}，每个规模 3 轮。 |
| 服务端 | 复用 `tests/sqlite_database_fixture.isolated_sqlite_database()`（自建临时目录，配置里的 PostgreSQL 被 patch 掉），用 `sqlite3` 的 trace callback **逐条统计真实 SQL**并计时；40 个私聊 + 1 个 N 人群，群成员全部**非好友**（大群的真实形态）。脚本 `.codex-temp/probe_social_group_server.py`，每项 3 次取中位数。 |
| 数据 | `.codex-temp/social-group-open-20261030.json`、`.codex-temp/social-group-server-20261030.json` |

> 客户端数字偏**乐观**：测试头像是本地 SVG（无网络/解码成本），角色取 `owner`（每人多一个操作菜单，是最重的情况）。服务端数字偏**乐观**：SQLite 本地文件，线上 PG 每条语句都是一次往返。

---

## §2 观测数据

### §2.1 服务端（SQL 条数 / 耗时 / 响应体）

| 操作 | N=5 | N=50 | N=200 | 结论 |
|------|-----|------|-------|------|
| `group_detail`（打开群） | 36 条 / 0.93 ms / **2.3 KB** | 36 / 1.54 / 19.7 KB | 36 / 4.21 / **77.6 KB** | 查询数**批处理得很好**（常数），但 payload 与 Python 处理 **O(N)** |
| ├ `group_card` | 8 / 0.11 / 239 B | 8 / 0.23 / 240 B | 8 / 0.59 / 241 B | 常数 |
| └ `_member_cards` | 26 / 0.69 / 1.9 KB | 26 / 1.24 / 19.2 KB | 26 / 3.28 / **77.1 KB** | 名册全部成本在这里 |
| `list_messages(50)` | 9 / 0.28 / 2.1 KB | 9 / 0.25 | 9 / 0.26 | 常数，无问题 |
| `list_conversations` | 99 / 1.17 / 11.6 KB | 99 / 1.10 | 99 / 1.20 | **不随群人数变，但随私聊数线性**（~2.4 条/私聊） |
| `unread_total` | 84 / 0.40 | 84 / 0.39 | 84 / 0.39 | 与 `list_conversations` **重复计算一遍** |
| **`send_message(group)`** | **29 / 1.03 ms** | **119 / 2.86** | **419 / 13.31** | **每条消息 O(N) 写入，且在发送事务内** |
| `mark_read(group)` | 5 / 0.10 | 5 / 0.10 | 5 / 0.18 | 常数，已正确（只更新自己那一行） |

`send_message` 的 419 条 ≈ 每成员 2 条（`deliver()` 的 `SELECT unread` + `UPDATE`/`INSERT`）+ 约 19 条基线。

### §2.2 客户端（一次开群的请求序列）

冷开（N 无关，结构完全相同）：

```
quota → conversations → friends → conversations/group:1/messages?limit=50
      → groups/1 → groups/1/files
      → conversations → friends/requests
      → conversations/group:1/read
      → conversations → conversations → friends/requests
```

**一次冷开：15 个请求，其中 `/conversations` 4 次、`/friends/requests` 2 次、`/groups/1` 1 次（成员表）+ `/groups/1/files` 1 次。**
热开（同一会话 30 s 内）：4 个真实请求（messages、groups/1、groups/1/files、conversations）。

### §2.3 客户端（DOM / CPU / 布局）

| 指标（3 轮） | N=5 | N=50 | N=200 |
|---|---|---|---|
| 开群后 DOM 节点 | 1734 | 2946 | **6999** |
| ├ 名册行 `.social-member` | 5 | 50 | 200 |
| └ 头像节点 | 81 | 126 | **276** |
| backdrop-filter 宿主 | 1 | 1 | **1**（不是玻璃层问题） |
| 开群 CPU（3 轮） | 215 / 164 / 268 ms | 298 / 194 / 247 ms | **317 / 178 / 381 ms** |
| 开群布局 | 39 / 27 / 47 ms | 62 / 33 / 35 ms | 55 / 29 / 60 ms |
| 3× `group_changed` 的 CPU | 54 / 68 / 94 ms | 122 / 99 / 149 ms | **148 / 114 / 234 ms** |
| 3× `group_changed` 的布局 | 6 / 8 / 13 ms | 16 / 8 / 18 ms | **21 / 12 / 27 ms** |

名册每多一人 ≈ **+27 个 DOM 节点**（头像 + 在线点 + 名字按钮 + owner/admin 的菜单）。N=200 时名册占开群后总节点的 **~77%**，是开群主线程成本的主体。

---

## §3 根因分析

### §3.1 R1 群消息的通知扇出：每成员 2 条 SQL，在发送事务里

`social_notification_service.notify_message()`（`123-168`）对 `targets`（除自己外**全部成员**，仅跳过拉黑）循环：

```python
for target in targets:
    ...
    deliver(conn, payload, aggregate_key="" if mentioned else AGGREGATE_PREFIX + parsed["key"])
```

`notification_delivery.deliver()`（`209-257`）每次要么 `_merge_into_unread`（`SELECT ... unread` + `UPDATE`）要么 `_insert_notification`。于是：

- **N 个成员 = 2N 条 SQL**，实测 419 条 @ N=200；
- 这些写在 `send_message` 事务内，而该事务此前已 `serialize_on(conn, "social-attention", [parsed["key"]])`——**同一群的所有并发发送会串行**，锁持有时间随 N 增长（SQLite 13 ms；PG 每条一次往返，量级更大）；
- 同一事务里 `push_message()`（`social_message_service.py:169-178`）还为每个 viewer 生成一个 `message` 事件，`push_events`（`social_push_service.py:134-174`）在 app 进程内**逐 pair 调 `push()`**，同一份事件被重复序列化 N 次。

### §3.2 R2 客户端每条消息都全量刷新计数

`social_client.js`：

```js
onMessage(message) {
    ...
    this.refreshCounts();      // line 170
    this.refreshBellSoon();    // line 171
```

`refreshCounts()`（`204-217`）600 ms 去抖后请求 `GET /conversations` + `GET /friends/requests`，并 `emit('counts')` 触发会话列表**全量重绘**。

而 `/conversations`（`social_conversation_service.py:360-407`）对每个私聊调用 `dm_unread()` → `dm_read_cursor`（1 条）+ `unread_count`（1 条）= **每私聊 2 条**，外加每群 1 条。40 私聊 = 99 条；200 私聊 ≈ 499 条。

**关键**：群消息也会走到这里——**即使我根本没打开这个群、甚至这个群已被我静音**。200 人的群里有 100 人挂着页面，一条消息就是 100 个客户端各自去查一遍自己的全部会话。

### §3.3 R4 打开群：整份名册往返 + 整份 DOM 重建

`group_detail`（`social_group_service.py:241-257`）= `_active_group` + `_require_member` + `group_card`（对全体成员 `presence_map` 一次）+ `_member_cards`（**再对全体成员 `presence_map` 一次**）。`_member_cards`（`216-238`）：

```python
rows = SELECT member_identity, role, nickname, joined_at ... ORDER BY id
users = public_users(conn, me, identities)          # ← 每个非好友成员跑 add_capability
visible = presence_visible(conn, me, identities)
presence = presence_map(conn, [i for i in identities if i in visible])
members.sort(key=lambda item: (not online, role, name))
```

`public_users` → `add_capability` → `scope_context` + `evaluate_targets`（`social_scope_service.py`）：SQL 是批量的（这点对），但整体仍是 O(N) 的 Python 组装 + O(N) 的字段输出。SQL 只有 36 条，**成本在 Python 与序列化**：0.93 → 4.21 ms，payload 77 KB。

客户端拿到后（`chat_sidebar.js:221`）：

```js
group.members.replaceChildren(...(detail.members || []).map(member => memberRow(member, detail, client.me)));
```

一次性创建 N 个 `memberRow`（各含 `lqAvatar` 组件、在线点、名字按钮、owner/admin 的 `createMenu`），**无分页、无虚拟化、无复用**。N=200 → ~5400 节点。

### §3.4 R3 放大模型（为什么"人越多越卡"是超线性）

设群成员 N，在线比例 p：

```
一条群消息的服务端成本    ≈ 2N 条 SQL（事务内、串行于会话锁）
每个在线成员的客户端成本  ≈ 2 条 HTTP → 服务端 ~2.4 × D 条 SQL（D = 私聊数）
总服务端 SQL ≈ 2N + pN × 2.4 × D
```

N=200、p=0.5、D=40 → 400 + 100×96 ≈ **1 万条**；D=200 → **5 万条**。全部落在 2 核、4 线程社交库池上，且每个请求都还要过一次鉴权与 ORM 层。这就是"人越多越卡"的直接来源。

### §3.5 R5 群事件广播的二次放大

`_changed()`（`171-173`）向全群发 `group_changed`；客户端 `chat_modal.js:656` 收到后 `refreshListSoon() + reloadGroupDetail()`——**没有去抖**。一个批次加 30 人只发 1 次 `group_changed`（`add_members` 做得对），但**每个在线成员都会各自重拉一次 77 KB 并重建名册**，于是单次改动的总流量是 O(N²)。实测 3 次 `group_changed` 在 N=200 时占 114–234 ms CPU。

### §3.6 R6 重复请求

一次冷开 15 个请求中，`/conversations` 出现 4 次、`/friends/requests` 2 次（`hello` → `refreshCounts`、`markReadSoon` → `refreshCounts`、`onSidebarChange` → `loadConversations(true)`、`counts` → 列表重绘）。热开 4 个请求里 `groups/1`（77 KB）**无条件重拉**。

---

## §4 改进方案

原则：**先砍放大系数（P0），再砍单次成本（P1），最后才是产品层收敛（P2）**。P0 全部是客户端 + 少量服务端批量化，不动权限、幂等、草稿合同。

### P0 止血（预计 1–2 天，收益最大）

**P0-1 客户端不再"每条消息刷全量计数"**（对应 R2/R3，风险低、改动小）——已完成 2026-10-30

- `social_client.js:170`：`onMessage` 不再调用 `refreshCounts()`；改为本地增量：`unreadMessages += 1` + 更新该会话的 unread 映射 + `paintBadge()`（已有逻辑，`181-186`）。服务端事件已带 `conversation_key`/`muted`，足够本地算。
- `/conversations` 只在**打开聊天面板**、**标记已读完成后**、**`visibilitychange` 回前台**时调用，并加最小间隔（如 10 s）。
- `refreshBellSoon()`（195-201）改为：**仅**私聊消息、或 `mentions` 含我/`all`、或 `friend_request`/`read` 时触发；普通群消息不刷新消息中心铃铛。
- `read` / `message_recalled` 事件（155-158）本地更新该会话即可，不整体刷新。
- **预期**：群消息对未打开该群的客户端产生的 HTTP 请求从 2 → 0；服务端 SQL 直接去掉 §3.4 里的 `pN × 2.4 × D` 项（约 90%+）。

**P0-2 服务端通知批量写**（对应 R1）——已完成 2026-10-30

- 在 `notify_message` 里把逐人 `deliver()` 改为三段批量：
  1. 一条 `SELECT id, recipient_identity ... WHERE ref_type='social_message' AND aggregate_key=? AND read_at IS NULL AND recipient_identity IN (...)`；
  2. 命中的用**一条多行 `UPDATE`**（或按 id 列表分块）累加 unread；
  3. 未命中的用**一条多行 `INSERT`**。
- 未 @ 的成员共用同一个 `aggregate_key`，天然可按"每人一行聚合卡"处理；@提及仍逐人建非聚合通知（人数受 `MENTION_LIMIT` 约束，可接受）。
- 保持 `serialize_on(conn, "social-attention", ...)` 与事务边界不变（幂等/一致性合同不破）。
- **预期**：`send_message` 从 419 条 → **≤ 25 条**（N=200），事务时间同量级下降，会话锁持有时间缩短一个数量级。

**P0-3 群详情缓存 + 条件请求**（对应 R4/R6）

- 服务端 `GET /api/social/groups/{id}` 增加一个**计算出来的版本号**（不改表结构）：`hash(updated_at, member_count, max(member_id), max(joined_at))`，作为 `ETag`；`If-None-Match` 命中返回 `304`。
- 客户端 `reloadGroupDetail()`（`chat_modal.js:515`）加 **500 ms 去抖 + 请求序号丢弃过期响应**；同会话 30 s 内已有 detail 时，开群直接用缓存，后台校验 ETag。
- `refreshListSoon()` 与 `reloadGroupDetail()` 合并为一次"会话变更"批处理。
- **预期**：热开的重拉从 77 KB → 0 字节（304）；`group_changed` 风暴合并为一次。

**P0-4 名册首屏只渲染可见部分**（对应 R4）

- `chat_sidebar.js:221` 改为：先渲染 **前 50 名**（排序：我 → 在线 → 管理 → 其余），底部一行"显示全部 N 人"，点击后分两批补齐。
- 成员过滤/搜索框已存在的话复用它作为补齐入口；不新增第二套搜索。
- **预期**：N=200 首次开群的 DOM 从 ~7000 → ~2400 节点，开群 CPU 约减半。

**P0-5 `group_changed` 走 keyed diff**（对应 R5）

- `showGroup` 里用 `Map<identity, HTMLElement>` 维护已渲染行，变更时只增删/更新变化的行，不再 `replaceChildren` 整份。
- **预期**：单次 `group_changed` 在 N=200 时从 ~40–78 ms → < 10 ms。

#### P0-1 / P0-2 施工结果（2026-10-30 复测）

两处均已落地并用同一套探针复测。前端构建 revision `f95fc002baa1d22c985c62937bc1d89b579c8e1d2494a97ec112de8aa8437b0a`。客户端"施工前"一列由 `SOCIAL_CLIENT_SRC=<git ref>` 把改动前的 `social_client.js` 在同一哈希 URL 上回放得到，因此是**同一 harness 的前后对照**，而不是两次测量方法不同的比较。

| 指标 | 施工前 | 施工后 | §5 目标 |
|---|---|---|---|
| `send_message(group)` SQL 条数 N=5 | 29 | 23 | — |
| `send_message(group)` SQL 条数 N=50 | 119 | **23** | — |
| `send_message(group)` SQL 条数 N=200 | 419 | **23** | ≤ 25（达标） |
| `send_message(group)` 耗时 N=200（SQLite 基准） | 13.3 ms | 9.6 ms | ≤ 3 ms（未达） |
| 20 条到达消息触发的全量重读（`/conversations` + `/friends/requests`） | 40 | **8–10** | 每条群消息 0（达标） |
| 到达期 CPU N=200 | 7 399 ms | **5 058 ms** | — |
| 开群 DOM 节点 / CPU N=200 | 6999 / ~317 ms | 6999 / ~342 ms | 属 P1（R4），未变即符合预期 |

结论与偏差：

- **SQL 条数已与 N 解耦**：29/119/419 → 23/23/23，命中 §5 的核心目标。剩余 23 条为发送路径固定成本（消息插入、参与者查询、@ 上下文、推送事件），不再随人数增长。
- **耗时目标 ≤ 3 ms 未达**（13.3 → 9.6 ms）。降幅主要来自省掉的 396 条 `message_center_notifications` 往返；剩下的绝对值是 SQLite 单文件写事务 + 发送侧固定开销，属于 P1-4（推送合并）与真实 PG 复测的范畴，不应在 P0 追。
- **客户端重读从 40 → 8–10 次**：每 14 s 窗口内稳定为 4 组 `/conversations` + `/friends/requests`（对应 `COUNT_CHATTER_MS = 4 s` 的噪声地板），外加按设计保留的 `/read` 游标 POST。`social_client.js` 的 `onMessage` 不再发起任何全量请求，徽标由 socket 负载本地增减；`chat_modal.js` 标记已读后改走 `client.settleRead(cleared)`，本地扣减 + 一次节流复核，不再每条消息各打一次。
- **开群指标未变是预期结果**：开群成本在 R4/R5（`group_detail` 体量与名册整表重建），对应 P0-3～P0-5 与 P1-1/P1-2，不在 P0-1/P0-2 的作用面内，所以这两项不能用来判断 P0 是否成功。
- **回归**：`tests.test_social_attention` / `test_social_messages` / `test_social_groups` / `test_social_ws` / `test_social_frontend_contract` 63 项通过；`test_social_authorization_races` + `test_blog_p5_notifications` 47 项通过；LQ 社交 sha256 契约 3 项通过。`test_social_attention_postgres.py` 未跑（需真实 PG，未连业务库）。
- **原始数据**（隔离 SQLite fixture + Playwright，未连业务库）：`.codex-temp/social-group-server-20261030{,-before}.json`、`.codex-temp/social-group-open-20261030{,-before}.json`、`.codex-temp/social-group-arrival-{before,after}.json`。

#### P0-3 施工结果（2026-10-30 复测）

三条均已落地，未改 `schema_social.py`：

- **服务端版本号**：`GET /api/social/groups/{id}` 返回 `ETag: "<32 位十六进制>"`，取自 `sha256(updated_at | 活跃成员数 | max(member_id) | max(joined_at))`；`If-None-Match` 命中（含 `W/` 弱校验与 `*`）返回 `304`，附 `Cache-Control: private, no-cache`（可存但每次重校验）。**权限与存在性检查在比对之前**，非成员仍是 403 而非 304；**版本比对也在名册投影之前**，304 不组装任何成员卡。
- **客户端**：`reloadGroupDetail()` 加 500 ms 去抖 + `groupVersion` 序号丢弃过期响应；`group_changed` 与列表刷新合并为一次 `conversationChanged` 批处理；同会话 30 s 内已有 detail 时开群直接用缓存，后台带 ETag 校验。缓存按群 id 记账、上限 8 份（200 人 detail ≈ 76 KB），403/404 时删除该条目。

| 指标 | 施工前 | 施工后 | §5 目标 |
|---|---|---|---|
| `group_detail` 无变更时（服务端直测 N=200） | 37 条 SQL / 3.96 ms / 77.6 KB | **3 条 SQL / 0.05 ms / 0 字节** | 304 / 0 字节（**达标**） |
| 热开 `group_detail`（客户端探针 N=200） | 200 + 76 159 B | **304 / 0 B** | 304 / 0 字节（**达标**） |
| 一个开群周期内 `group_detail` 拉取（开群 + 3× `group_changed`）N=200 | 4 次，4 × 76 159 = 304 636 B | **2 次**（1×200 + 1×304；热开 0 B） | 风暴合并为一次（**达标**） |
| 3× `group_changed` CPU / 布局 N=200 | 89–500 ms / 10–25 ms | **3–72 ms / 0–1 ms** | ≤ 40 ms（3 轮中 2 轮达标） |
| 一次开群请求数 冷 / 热 | 8–9 / 3–4 | 8–9 / **3** | 冷 ≤ 8 / 热 ≤ 3（热端达标） |
| 开群窗口 `/conversations` | 冷 1–2 / 热 0 | 冷 1–2 / 热 0 | 只出现 1 次（冷端仍有 2） |
| 开群 DOM 节点 / 冷开 `visible` N=200 | 6996 / 100 ms | 6996 / 92 ms | 属 P0-4/P0-5，未变即符合预期 |

结论与偏差：

- **`group_changed` 风暴确实合并了**：3 次事件从"3 次全量拉取 + 3 次名册整表重建"变成"1 次 304 + 不重建"，N=200 时峰值 CPU 从 500 ms 降到 72 ms、布局从 25 ms 降到 1 ms 以内。这是本次最大收益。
- **304 不再是"伪省流"**：版本号比名册投影先算，所以热开在服务端也只花 3 条 SQL（比 200 路径的 37 条少 34 条，其中 26 条是 `member_cards`）。
- **请求条数没降，这是预期**：§5「冷 15 / 热 4」的基数含页面初始化的 `quota`/`conversations`/`friends`；按"开群窗口"同一口径复测，施工前本来就只有 6–9 条。P0-3 省的是**字节与重建次数**，不是请求条数。
- **冷开 `/conversations` 仍会出现 2 次**：一次来自初始化，一次是 `COUNT_CHATTER_MS = 4 s` 的计数节流恰好落在 30 s 内窗口里，属 P1-2 作用面。
- **开群渲染成本未动**（DOM 仍 6996 节点、`visible` 90–151 ms）：名册首屏与 keyed diff 分别是 P0-4 / P0-5。
- **回归**：`test_social_groups` 14、`test_social_messages` / `security` / `authorization_races` / `attention` / `wanderer_social` 113、`test_social_frontend_contract` / `test_social_ws` 25、`test_social_friends` 12、`test_architecture_route_snapshot` 1，全绿。新增两测：换人不改人数时版本号必须变；带命中 ETag 的非成员仍须 403。
- **原始数据**：`.codex-temp/social-group-open-after-20261030.json`、`.codex-temp/social-group-open-p03-before-20261030.json`、`.codex-temp/social-group-server-20261030.json`。「施工前」一列是把 HEAD 版 `chat_modal.js` / `social_api.js` 放回同一哈希 revision 目录后跑的同一套 harness（跑完已 `build:assets` 还原，revision 仍为 `474d882b…`）。

### P1 结构性（预计 3–5 天）

**P1-1 名册字段瘦身 + `can_add` 惰性化**（对应 R4）

- `_member_cards` 只返回 `identity / name / role / nickname / online / avatar_url`；`org_label`、`relation`、`can_add`、`add_blocked_reason` 全部移除。
- 加好友能力改为点开成员卡时由已有的 `GET /api/social/users/{id}/card` 提供（该端点本就按需跑 `can_add`）。
- 这同时去掉 `_member_cards` 里 `public_users` 的 `add_capability`/`evaluate_targets` 开销。
- **预期**：`group_detail` 77 KB → **≤ 15 KB**，Python 时间 ~3.3 ms → < 1 ms。

**P1-2 `presence_map` 只算一次**（对应 R4）

- `group_detail` 把 `group_card` 与 `_member_cards` 合并为一次成员扫描、一次可见性判定、一次 `presence_map`。

**P1-3 `/conversations` 与 `unread_total` 合并成常量条数**（对应 R2/R6）

- 用聚合 SQL 同时算出所有会话未读：
  - 群：`social_group_members.last_read_message_id` + 每群 `COUNT(*)`（`GROUP BY conversation_key`，一条）；
  - 私聊：`social_dm_participants.last_message_id` 与 `social_dm_reads.last_read_message_id`（一条 JOIN），未读用 `COUNT(*)` 精确值再一条 `GROUP BY`。
- `unread_total` 直接复用 `list_conversations` 的结果求和，不再自己跑一遍（`social_conversation_service.py:410-414`）。
- `_hello_payload`（`social_ws.py:115`）只算一次 `unread_total`。
- **预期**：40 私聊时 99 → **≤ 10 条**；200 私聊时 ~499 → **≤ 10 条**。这顺带解决"好友多的人更卡"。

**P1-4 推送合并**（对应 R1 尾部）

- `push_events` 在 app 进程内：先按 event 序列化**一次**，再对每个 identity 复用同一字符串；`deliver_local` 的 gather 折成一批。
- **预期**：一条 N 人群消息的推送开销从 N 次 `json.dumps` → 1 次。

### P2 产品层（可选，需产品决策）

- **P2-1 大群成员状态降频**：成员数 > 100 的群，侧栏在线状态改为 60 s 轮询/随消息附带，不随 presence 事件实时刷新。
- **P2-2 群消息合并渲染**：同一群 200 ms 内的多条消息合并为一次 DOM 追加（现有 `reconcileChildren` 已能避免整表重建，这里是进一步减少追加次数）。
- **P2-3 群上限评估**：`GROUP_MAX_MEMBERS = 200` 是当前 O(N²) 的乘数上限；若业务上 200 是常态而非极端，P0/P1 完成后应重新压测再决定是否调整。

---

## §5 验收门槛（可量化，施工后必须复测同一套探针）

| 指标 | 现状（N=200） | 目标 |
|---|---|---|
| `send_message(group)` SQL 条数 | 419 | **≤ 25**，且与 N 基本无关 |
| `send_message(group)` 耗时（SQLite 基准） | 13.3 ms | **≤ 3 ms** |
| `group_detail` 响应体 | 77.6 KB | **≤ 15 KB** |
| `group_detail` 无变更时 | 200 + 77 KB | **304 / 0 字节** |
| 一次开群请求数 | 冷 15 / 热 4 | **冷 ≤ 8 / 热 ≤ 3**，`/conversations` 只出现 1 次 |
| 收到群消息（未打开该群）的网络请求 | 2 | **0** |
| 开群后 DOM 节点（N=200） | 6999 | **≤ 2500** |
| 开群 CPU（N=200） | ~317 ms | **≤ 150 ms** |
| 3× `group_changed` CPU（N=200） | 114–234 ms | **≤ 40 ms** |
| 200 人在线、一条群消息的服务端总 SQL | ~1 万 | **≤ 1500** |

**当前状态（2026-10-30）**：P0-1/P0-2/P0-3 已施工并复测。已达标：`send_message(group)` SQL 条数（419 → 23）、"收到群消息的网络请求"（2 → 0）、"`group_detail` 无变更时"（200 + 77 KB → 304 / 0 字节）。部分达标：一次开群请求数（热 4 → 3 达标，冷端 8–9 与 §5 的 15 基数口径不同，见 §4）；`group_changed` CPU（N=200 从 114–234 ms 降到 3–72 ms，3 轮中 2 轮进 40 ms 内）。其余行属 P0-4/P0-5 与 P1，尚未动工。逐项实测见 §4「P0-1 / P0-2 施工结果」与「P0-3 施工结果」。

复测命令：

```bash
node .codex-temp/probe_social_group_open.cjs
```

```bash
PYTHONPATH=. python .codex-temp/probe_social_group_server.py
```

---

## §6 风险与不变量

**必须保持不变的**（改任何一条都要单独评审）：

- 权限：`require_access` / `_require_member` / `blocked_either` / `presence_visible` 的判定范围与结果；
- 幂等：`client_nonce` 去重、`serialize_on` 的锁序（`social-send` → `social-attention` → `social-group-membership`）；
- 已读语义：群消息只更新**自己那一行** `social_group_members.last_read_message_id`（现状正确，勿改）；
- 撤回/系统消息不计未读（`unread_count` 的 `message_type <> 'system'`）；
- 通知的聚合语义（未 @ 的成员共用 `aggregate_key`，@ 提及单条不聚合）；
- 前端：IME/焦点/草稿/取消合同，LQ 组件与场景墨色约定。

**主要风险点**：

1. P0-1 之后未读数靠本地累加，若漏收事件会漂移 → **必须**在打开聊天面板时以服务端结果校准，且 `hello` 帧照常覆盖。
2. P0-2 的批量 UPSERT 若与 `deliver` 的"未读卡合并"逻辑不一致，会出现重复卡 → 用现有 `settle_read` 的测试集做回归。
3. P0-3 的 ETag 若只取 `updated_at` 会漏掉"成员数不变但换人"的情况 → 版本号必须包含 `max(member_id)` 与 `max(joined_at)`。
4. P1-1 移除 `can_add` 会影响名册上的"加好友"按钮 → 需同步改 `chat_sidebar.js:136-167` 的 `memberRow`，改走用户卡端点。

**回滚**：P0 全部可独立开关（前端常量 + 一个服务端批次开关），可单独回退；P1-3 的 SQL 改写保留旧实现一个版本，以环境变量选择。

---

## §7 施工注意（本项目约定）

- 本文档只涉及 `static/js/social/*`、`classroom_app/services/social_*`；**不新增/不修改 LQ 共享模块**，因此不需要动 `docs/lq-module-registry.md`；若最终改到共享样式，必须按合同先查消费者并运行 `npm run inventory:lq-modules`。
- **不要修改 `schema_social.py`**（P0-3 用计算版本号即可）——新增列会触发原生 PG 门禁报告对流程。
- `static/assets/` 是 gitignore 的本地构建产物，本机常落后于源码：改动后必须 `npm run build:assets`，否则本地探针测的是旧包（2026-10-30 已踩过一次）。
- 源码声明不等于验收：P0/P1 每完成一项，必须用 §5 的同一套探针复测，并分别在隔离合成环境与正式构建下各跑一次。
- 部署需用户授权；上线核验与 Git 推送分别报告，保护 `/lanshare/data`。

---

## §8 待确认

1. **P0-1 的产品语义**：群消息不再实时刷新未读角标以外的东西——聊天面板打开时列表会稍晚一点更新（打开即拉）。可以接受吗？
2. **P0-4 的名册默认只显示 50 人**：200 人群里找第 120 位需要点"显示全部"或搜索。可接受吗，还是改成虚拟滚动（成本更高）？
3. **P2-1 大群在线状态降频**：100 人以上群不再实时显示成员在线变化。这是可见的行为变化，需要产品确认。
4. 是否需要先做 **P0-1 + P0-2**（收益最大、可独立上线）再评估？
