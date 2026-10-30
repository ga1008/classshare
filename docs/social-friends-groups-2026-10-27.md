# 好友、群组与统一在线 · 2026-10-27

> 真源：本文 + `classroom_app/services/social_*` + `tests/test_social_*.py`。实施分三块：A 后端（§1–§8）、B 前端（§9）、C 散修社交（§10）。B、C 只依赖 §7 的接口合同。

## 0. 边界与原则

- **身份**统一用 `role:pk`（`student:12`、`teacher:3`），散修是 `student:<id>`（`enrollment_status='wanderer'`）。管家/AI 不能加好友。
- **黑名单单一真源**：沿用 `private_message_blocks`（课堂私信已在用）。拉黑时同时解除好友、撤回双方待处理好友申请；被拉黑者不能加我、不能私聊我（好友私聊与课堂私信都拦）、不能把我拉进群。已同在一个群里的成员关系不变（群主可另行移出）。
- **好友聊天与课堂私信是两个场景**：课堂私信（消息中心/课堂页，`private_messages`）保持不变；好友私聊和群聊走新的 social 域。好友侧栏提供「查看课堂私信记录」跳转（若双方有课堂私信）。
- **媒体存储**：聊天媒体放独立内容寻址库 `DATA_ROOT/media/social/sha256/ab/cd/<hash>`，引用只在 social 表里计数，所以「保留 1 年」和「空间不足清理」可以安全物理删除。若字节已在全局库（`GLOBAL_FILES_DIR`）则只读引用，**永不删除全局库文件**（遵守 `docs/global-file-reference-protocol.md`）。
- **实时**：新增单一全局 WebSocket `/ws/social`（每个已登录页面一条），负责在线、推送、输入中、局域网直传信令。生产单 worker，进程内注册表即可。客户端每 25 s ping（nginx `/ws/` 读超时 60 s）。
- **生产资源**：2 核 4 GB，约 200 并发。上传/下载限速、每用户上传并发 1、大文件分块。

## 1. 数据模型（`classroom_app/db/schema_social.py`，引擎感知、幂等，仿 `schema_polls.py`；接入 `init_database` 与测试夹具 `_SCHEMA_MODULES`；`tests/test_db_postgres_schema.py` 的运行时模块清单要加）

```
social_friendships          -- 一对身份一行（identity_a < identity_b 字典序）
  id, identity_a, identity_b, status ('pending'|'accepted'),
  requester_identity, request_message TEXT DEFAULT '', created_at, responded_at,
  remark_a TEXT DEFAULT '', remark_b TEXT DEFAULT '', declined_until TEXT DEFAULT '',
  UNIQUE(identity_a, identity_b)
social_groups
  id, name, avatar_hash DEFAULT '', announcement DEFAULT '', owner_identity,
  max_members INTEGER DEFAULT 200, created_at, updated_at, dissolved_at
social_group_members
  id, group_id, member_identity, role ('owner'|'admin'|'member'), nickname DEFAULT '',
  muted INTEGER DEFAULT 0, joined_at, last_read_message_id INTEGER DEFAULT 0, left_at,
  UNIQUE(group_id, member_identity)
social_messages             -- 私聊与群聊共用
  id, conversation_type ('dm'|'group'), conversation_key,   -- dm: 'dm:<a>|<b>'（排序）；group: 'group:<id>'
  sender_identity, body TEXT DEFAULT '', body_format ('md'|'text'),
  message_type ('text'|'media'|'system'|'nudge'), reply_to_id, mentions_json DEFAULT '[]',
  client_nonce DEFAULT '', created_at, recalled_at,
  UNIQUE(sender_identity, client_nonce)  -- 空 nonce 不参与（服务端为空时生成 uuid）
  INDEX(conversation_key, id)
social_message_media
  id, message_id, blob_hash, store ('social'|'global'), original_filename, mime_type,
  file_size, media_kind ('image'|'video'|'audio'|'file'), width, height, duration_ms,
  thumbnail_hash DEFAULT '', delivered_via ('server'|'lan'), server_ready INTEGER DEFAULT 1,
  expires_at, expired_at, created_at
social_media_blobs          -- 仅社交库字节
  blob_hash PK, file_size, mime_type, storage_state ('ready'|'compressed'|'deleted'),
  ref_count INTEGER, first_seen_at, last_referenced_at, deleted_at
social_media_tokens         -- precheck/上传完成后发放的一次性引用令牌（发送消息时兑换）
  token PK, owner_identity, blob_hash, store, original_filename, mime_type, file_size, created_at, used_at
social_upload_sessions      -- 分块上传会话
  upload_id PK, owner_identity, sha256, size, mime, filename, received_bytes, chunk_size, temp_path, status, created_at, updated_at
social_dm_reads             -- 私聊已读游标
  conversation_key, reader_identity, last_read_message_id, updated_at, PRIMARY KEY(conversation_key, reader_identity)
social_upload_usage         -- 每日上传配额（真实写入服务器的新字节；去重命中不计）
  identity, usage_date, bytes_uploaded, PRIMARY KEY(identity, usage_date)
social_lan_transfers        -- 局域网直传记录
  id, message_id, media_id, sender_identity, receiver_identity, blob_hash,
  status ('offered'|'p2p_done'|'p2p_failed'|'server_backfilled'), created_at, updated_at
user_presence
  identity PK, last_seen_at
```

时间戳 UTC ISO（`datetime.now(timezone.utc).isoformat()`），布尔 INTEGER 0/1。

## 2. 好友

- 申请：`request(from, to, message≤100字)`；若对方已有指向自己的 pending，直接互为好友。拒绝：不能加自己或管家/AI；任一方拉黑对方时返回统一文案「暂时无法添加该用户」（不泄露谁拉黑谁）；每人每天 ≤ 30 个申请、好友 ≤ 1000；被拒后 7 天内（`declined_until`）不能再申请。
- 通过/拒绝：只有被申请方处理。通过后双方各收消息中心通知（`category='social'`，加入学生/教师可见类别），私聊插入系统消息「你们已成为好友，开始聊天吧」，并推送 ws `friend_accepted`。
- 删除好友：一方删除即解除；私聊记录保留，发送入口变为只读并提示「已不是好友」。
- 备注：每人对每个好友一条。
- 搜索：按姓名、学号、工号、邮箱、散修名，≤ 20 条，只给公开信息（姓名、角色、班级/学院、头像、是否散修、境界标志、是否已是好友/已申请）。推荐：同课堂同学、任课老师、在线散修。

## 3. 黑名单
单独浮窗，列表 + 移出。`POST/DELETE /blocks` 复用 `add/remove_private_message_block` 并执行 §0 的联动。

## 4. 群组
- 建群：至少选 1 位好友；只能拉自己的好友进群（被拉者拉黑了拉人者则跳过并告知）。上限 200 人；每人 ≤ 50 个群、创建 ≤ 10 个。
- 角色：群主设管理员、转让、解散；管理员踢人、改公告/群名；成员退出、群昵称、免打扰。
- @某人 / @全体（仅群主管理员）；被 @ 收消息中心通知。
- 群文件：群内发过的 media 汇总列表（时间倒序、按类型筛）。
- 进群/退群/被踢/改公告产生系统消息。

## 5. 消息
- 文本：Markdown 子集，≤ 4000 字；前端 `MarkdownRuntime` 清理渲染。
- 媒体：图片、视频、音频（含录音）、任意文件；一条 ≤ 9 个附件。
- 撤回：发送者 2 分钟内；群主管理员可撤回任意群消息。
- 拍一拍：`message_type='nudge'`，每对每分钟 1 次。
- 频率：每人每 10 s ≤ 10 条、每分钟 ≤ 30 条（429）。
- 已读：私聊记录对方已读游标；群聊仅自己的未读数。
- 发送权限：私聊需为好友且未互相拉黑；群聊需为当前成员且群未解散。
- 幂等：`client_nonce` 重发不重复落库。

## 6. 媒体：去重、配额、限速、留存
- 客户端先算 SHA-256，`POST /media/precheck`：社交库或全局库已有该字节 → 直接给 `media_token`，不上传、不计配额。
- 否则分块上传（1 MB/块）；服务端校验最终 SHA-256 一致后原子发布到社交库（复用 `file_service` 的临时文件+原子发布写法，但目标目录为社交库）。
- 限制：单文件 ≤ 100 MB；每人每天新写入 ≤ 200 MB；每人同时 1 个上传会话；上传按每用户约 2 MB/s 节流；全站上传并发 ≤ 6。会话 1 小时未完成自动清理临时文件。
- 下载：鉴权（会话成员），流式、每连接约 4 MB/s、全站并发 ≤ 40，支持 Range；图片有缩略图。修复并复用 `file_handler.throttled_file_iterator`（现有节流写错位置）。
- 留存：每日任务 `social_media_retention`：`expires_at`（创建 + 365 天）到期的 media 标记 `expired_at`（消息显示「文件已过期」）；社交库 blob `ref_count` 归零即物理删除；全局库引用只解除，不删字节。
- 空间守护：同任务检查数据盘使用率，超过 `SOCIAL_MEDIA_DISK_HIGH_WATERMARK`（默认 85）时按时间从旧到新：先把 > 2 MB 的图片重压为 WebP（质量 70、长边 2048，`storage_state='compressed'`），仍不够再让最旧的视频/文件提前过期并删除，直到低于 `SOCIAL_MEDIA_DISK_LOW_WATERMARK`（默认 80）或单次处理满 500 个；写日志，超管 `GET /admin/storage` 可见摘要与最近一次运行结果。

## 7. 接口合同（登录必需；前缀 `/api/social`；JSON；错误 `{detail}`）

```
GET    /friends                         -> {items:[FriendCard], counts:{online,total,pending_in}}
GET    /friends/requests                -> {incoming:[Req], outgoing:[Req]}
POST   /friends/requests {target_identity, message}         -> {status:'pending'|'accepted', request_id}
POST   /friends/requests/{id}/accept   POST /friends/requests/{id}/decline   DELETE /friends/requests/{id}（撤回）
DELETE /friends/{identity}
PATCH  /friends/{identity} {remark}
GET    /users/search?q=                 -> {items:[PublicUser]}
GET    /users/suggestions               -> {items:[PublicUser]}
GET    /users/{identity}/profile        -> ProfileSidebar
GET    /blocks   POST /blocks {identity}   DELETE /blocks/{identity}

GET    /groups                          -> {items:[GroupCard]}
POST   /groups {name, member_identities[]}                  -> GroupCard
GET    /groups/{id}                     -> GroupDetail
PATCH  /groups/{id} {name?, announcement?}
POST   /groups/{id}/members {identities[]}    DELETE /groups/{id}/members/{identity}
POST   /groups/{id}/leave   POST /groups/{id}/dissolve   POST /groups/{id}/transfer {identity}
POST   /groups/{id}/admins {identity, admin:bool}
PATCH  /groups/{id}/me {nickname?, muted?}
GET    /groups/{id}/files?kind=&before_id=

GET    /conversations                   -> {items:[ConversationItem]}
GET    /conversations/{key}/messages?before_id=&limit=50   -> {items:[Message], has_more}
POST   /conversations/{key}/messages {body, body_format, reply_to_id?, mentions?, media_tokens?, client_nonce}  -> Message
POST   /conversations/{key}/read {last_message_id}
POST   /conversations/{key}/nudge
POST   /messages/{id}/recall
POST   /media/precheck {sha256,size,mime,filename}          -> {exists, media_token?}
POST   /media/upload/init {sha256,size,mime,filename}       -> {upload_id, chunk_size, received_bytes}
PUT    /media/upload/{upload_id}/chunk?index=n   (原始字节)
POST   /media/upload/{upload_id}/complete                   -> {media_token}
GET    /media/{media_id}[?variant=thumb]
POST   /lan/{transfer_id}/status {status}                   -> 记录直传结果
POST   /media/{media_id}/backfill  (分块上传完成后调用，把 lan 媒体标记 server_ready)
GET    /presence?identities=a,b         -> {items:{identity:{online,last_seen_at}}}
GET    /quota                           -> {daily_limit, used_today, file_limit}
GET    /admin/storage   （超管）

FriendCard = {identity, name, remark, role_label, avatar_url, is_wanderer, tier_badge, mood, online, last_seen_at, conversation_key, unread, homepage_url}
PublicUser = {identity, name, role_label, org_label, avatar_url, is_wanderer, tier_badge, relation:'none'|'friend'|'pending_out'|'pending_in'|'blocked'}
ProfileSidebar = PublicUser + {mood, online, last_seen_at, remark, cultivation:{tier_name,score,emblem}|null, recent:[{kind,title,url,at}], private_message_url|null}
GroupCard = {id, name, avatar_url, member_count, online_count, my_role, conversation_key, unread, last_message}
ConversationItem = {conversation_key, type, title, avatar_url, last_message:{preview,at}|null, unread, online?, peer_identity?, group_id?}
Message = {id, conversation_key, sender:{identity,name,avatar_url,is_wanderer,tier_badge}, body, body_format, message_type,
           reply_to:{id,preview,sender_name}|null, mentions, media:[{id,kind,filename,size,mime,url,thumb_url,expired,delivered_via,server_ready}],
           created_at, recalled, mine}
```

**WebSocket `/ws/social`**（cookie 鉴权同课堂 ws）：

```
client -> {type:'ping'} | {type:'typing', conversation_key} | {type:'focus', conversation_key|null}
        | {type:'lan_offer'|'lan_answer'|'lan_ice'|'lan_cancel', to, transfer_id, payload}
server -> {type:'pong'} | {type:'hello', me, online_friends:[identity], unread_total}
        | {type:'presence', identity, online, last_seen_at}
        | {type:'message', message} | {type:'message_recalled', id, conversation_key}
        | {type:'read', conversation_key, reader, last_message_id}
        | {type:'typing', conversation_key, identity}
        | {type:'friend_request'|'friend_accepted'|'friend_removed', ...} | {type:'group_changed', group_id, reason}
        | {type:'nudge', conversation_key, from}
        | {type:'lan_offer'|'lan_answer'|'lan_ice'|'lan_cancel', from, transfer_id, payload}
```

服务层推送接口（供调度器容器里的散修调用）：`social_push_service.push(identities, event)` 在 app 进程内直接推送；在 scheduler 进程中改走内部 HTTP `POST /api/internal/social/push`（令牌同散修内部接口）。

## 8. 统一在线（`presence_service`）
- 进程内注册表 `identity -> {connections:{conn_id:source}, last_seen_at}`；来源 `social_ws`、`classroom_ws`。课堂 `MultiRoomConnectionManager.connect/disconnect` 同步上报（课堂在线列表仍按房间显示，「是否在线」真源在这里）。
- 在线 = 有任一连接；断开后 30 s 宽限再广播离线。`last_seen_at` 节流 60 s 写 `user_presence`。
- 散修在线为模拟：`status='active'` 且当前处于 `active_hours`。
- 在线变化只推给其好友与共同群成员。超管监控可读 `presence_service.snapshot()`。

## 9. 前端（B）
- 顶栏两套模板都加「好友」按钮 + 未读徽标；所有已登录页面加载 `static/js/social/social_client.js`（全局 ws、在线、推送、徽标、桌面标题闪烁提示）。
- 好友浮窗（`createDomainModal`）：选项卡「好友 / 新朋友 / 群组 / 添加」；好友卡片网格，在线：亮起、上浮、背光（`--lq-material-accent` 光晕、`translateY(-2px)`、阴影）；离线：去饱和、下沉；尊重 `prefers-reduced-motion`；卡片显示头像+境界标志、备注/姓名、心情、未读；在线优先排序。「黑名单」按钮打开独立黑名单浮窗。
- 聊天浮窗：左侧会话列表（可折叠）、中间消息区（`lq-bubble`、Markdown、图片灯箱 `data-ls-lightbox`、视频/音频播放器、文件卡片、过期占位、局域网直传徽标、引用回复、撤回、已读、输入中、拍一拍动画）、底部 `lq_composer`（表情 `createEmojiPicker`、粘贴图片、附件、录音、Markdown 工具条、@成员、Enter 发送/Shift+Enter 换行、上传进度、配额提示）；右侧栏（好友：头像、在线、心情、修为摘要、最新动态、备注、拍一拍、删除、拉黑、课堂私信入口、散修主页；群：公告、成员在线优先、群文件、群设置）。
- 在线标识（2026-10-30）：头像右下角不再叠加绿点（会压住境界标志），改为头像自身的绿色发光外环（`.social-avatar[data-online="true"] > .lq-avatar`，静态 box-shadow，无逐头像动画/filter）；会话列表、好友卡片、群成员、资料卡共用同一状态钩子。`/ws/social` 的 presence 突发由 `social_client` 合并为每帧一次 `presence-settled`，列表与侧栏整表重绘最多 1 次/帧；`hello` 重建在线缓存以清掉重连前的陈旧在线。
- 发文件：算 SHA-256 → precheck → 不存在再分块上传；超限给出清晰提示。
- 局域网直传：对方在线且文件 ≥ 5 MB 时先试 WebRTC DataChannel（`iceServers: []` 只用本地候选，8 s 未连通退回服务器上传）；成功后发送方后台 ≤ 512 KB/s 补传服务器并调 backfill，其他设备在补传前看到「源设备补传中」。
- 只用 LQ 组件/令牌，玻璃上文字遵守场景墨色；改完 `npm run inventory:lq-modules` 并维护 notes；在生产同款 LQ 开关（`main-dev-lq` 启动配置）下验证。

## 10. 散修社交（C）
- 好友申请：散修按性格决定通过（社牛秒过、社恐慢且可能拒），一次性调度任务延迟 1–30 分钟处理；偶尔主动加评论过/回复过它的真实学生（每散修每周 ≤ 2）。
- 私聊：收到消息后按性格安排一次性回复任务（延迟 20 s–10 min），可多条，条间按字数模拟打字（推送 typing），可能不回或只回表情；遵守每日 AI 上限；记忆线程 `social:<conversation_key>`。
- 群聊：散修实时「看到」群消息（事件触发、按群去抖 30–120 s 合并），自行决定是否发言（性格、是否被 @、话题兴趣）；被 @ 必然考虑；每群每散修每日 ≤ 20 条；多个散修错峰。
- 散修会拍一拍、发表情；不发文件。
- 天道纪律证据纳入社交行为（刷屏、辱骂同样警告）。

### 10.1 实施记录（2026-10-27，与上文差异以此为准）

- **触发（app 进程，业务事务内，只登记任务）**：`wanderer_social_triggers`。`send_message`/`nudge` 与 `send_request` 在 savepoint 里调用 `on_message_created`/`on_friend_request`，失败不影响发送。系统总开关关闭、散修非 active、`behaviors_json.social=0` 时不登记任何任务。
- **任务种类**（均注册在 `scheduled_task_handlers.py`，一次性，`max_attempts=2`）：
  - `wanderer_friend_request`（dedupe `wanderer:friend:<wid>:<request_id>`）：延迟 = 60 s + 1740 s ×（1−社交度）²，再乘 0.6–1.3 的随机系数，夹在 1–30 min 之间；当前不在 active_hours 时推到下一个作息窗口（12 h 内）开始后再加 1–15 min。处理时：老师必过；骂人附言必拒；社恐（社交度 < 0.35）有 40% 概率先搁置 1–4 h（仅一次），其余按概率 0.3 + 0.65 × 社交度 决定通过。社交度由 activity_level、reply_policy.ignore_prob、原型关键词算出，取值 0–1。
  - `wanderer_social_reply`（dedupe `wanderer:social:<wid>:<conversation_key>`）：私聊延迟 20 s–10 min（同样按社交度平方缩放），睡眠时推到下一窗口，12 h 内都不醒则不登记。若已有 pending 任务则合并，不推后执行时间；同一任务带 `mentioned` 标记（各次按 OR 合并）。
  - `wanderer_group_watch`（dedupe `wanderer:group-watch:<gid>`）：首条消息起 30–120 s 后触发，期间的群消息都合并进这一次。每个群成员散修各自掷概率：被 @ 时为 1；否则 0.05 + 0.3 × 社交度 + 兴趣加成（≤ 0.3）；最新一条若是散修发的再 ×0.25；概率上限 0.85。选中的散修错峰 5–40 s 累加，各登记一个 `wanderer_social_reply`；没被选中的只把已读游标推进（视为看过、选择不说话）。
- **回复执行**：读最近 30 条消息加上记忆线程 `social:<key>`，先标已读（对方能看到已读回执），再按 reply_policy 掷模式（被 @ 时不会掷出 ignore），然后调用一次 `generate_turn`（场景 `social_chat`/`group_chat`）。每条输出单独发送：先推 `{type:'typing'}`，等待 `len×0.12 s`（夹在 1–8 s），再发出。`client_nonce` = `wanderer:<wid>:<触发消息id>:<序号>`，任务重试不会重复发。私聊里有 4% 概率回拍一拍；对方先拍了自己时，回拍概率为 0.5 × 社交度。
- **成本与防循环**：遵守每日 AI 上限（超限后私聊只回表情，不调 AI；群聊保持沉默）；每会话 10 min 内最多 6 条；每群每散修每天最多 20 条；不回复自己；散修对散修的私聊只以 0.25 的概率接话；末尾已有连续 2 条散修发言时，任何散修都不再接话，被 @ 也一样。社交动作计入 `wanderer_action_log`：`social_friend`、`social_message`，跳过记为 `skip` 并写明原因。
- **主动加好友**：只在常规节拍里进行（管理员「指定散修立即运行」不做），每个本轮醒着的散修有 4% 概率。每散修每周最多 2 次，对象是近 30 天以实名评论过它的帖子、回复过它的评论、或给它发过课堂私信的真实学生，排除已有好友关系行和拉黑。附言由一次 AI 生成，超限时改用模板。
- **在线**：`presence_service` 的模拟规则不变（active 且在 active_hours 内即在线），好友卡片与 `/presence` 用同一个 `presence_map`。每轮节拍由 `sync_wanderer_presence` 对比上轮状态（runtime setting `social_presence_state`），只把变化推给好友和同群成员，离线时写入 `user_presence.last_seen_at`；首轮只记录状态，不推送。
- **提示词**：`wanderer_prompt_service` 新增 `scene_type`：`social_chat`/`group_chat`，提示块里有聊天守则和在场人员名单（自己 / 散修 + 境界 / 老师——要尊重 / 真实学生），名单最多 30 人。反 AI 腔规则照常生效。
- **天道与管理端**：`collect_evidence` 新增 `social_24h`，包含 24 h 发言量、会话数、发过言的群数、单会话 10 min 峰值、刷屏会话数、撤回数、所在群数、7 日内被拉黑次数，以及 `recent_chat`（自己最近 5 条文字）。天道规则六改为「在课堂进行中或私聊/群聊里刷屏」。管理端详情 `stats.social` 给出 friends/groups/messages_today/pending_requests。
- **后端修复**：① `social_message_media` 与 `social_media_tokens` 新增 `global_file_hash`，仅在 store='global' 时填写；写入前对整批 hash 调用 `lock_global_file_references`（文件丢失则返回 409）；令牌兑换后由媒体行接过引用，过期时清空该列。全局引用计数器因此能看到社交引用。② 两个请求并发用同一 `client_nonce` 发送时，落败的一方（唯一约束冲突，或令牌已被另一方兑换）返回已存的那条 Message（200），不再报 409。

### 10.2 安全复审修复（2026-10-27，§7 合同只做增量）

- 预检 `exists:true` 的前提：请求者本来就能读到这些字节。具体有两种：一是在自己当前所在会话里有一条未过期的社交库附件，二是自己完整上传过同样的字节。全局库永远不通过预检发放；上传完成后若字节已在社交库或全局库，直接复用，不写第二份。
- 分块上传按以下顺序检查：先校验会话和归属；每人同时只允许 1 个在途分块，否则返回 429；令牌桶约 2 MB/s，突发 2 块，超出返回 429 并带 Retry-After，不再用睡眠限速；全局上传槽最多等 2 s，等不到返回 503。
- `/ws/social`：
  - 每个 socket 有帧令牌桶：20 帧/s，突发 40；超出的帧直接丢弃，10 s 内丢弃满 100 帧则以 1008 关闭。
  - 每个身份最多 5 个 socket，第 6 个连上时以 4008 关闭最旧的一个。
  - typing 按（发送者，会话）每 3 s 最多转发一次。
  - lan_* 的 payload 不超过 16 KB，且只转给在线、未被任一方拉黑的好友或同群成员。
  - 社交 DB 操作都放在专用的 4 线程池里执行。
  - 推送失败时关闭该 socket，让客户端重连。
- 好友申请：每日上限按只追加的 `social_friend_request_log` 计数，撤回不退还名额；撤回后同一对人 24 h 内不能再申请；同一对人 24 h 内的申请通知合并为一条；散修决策任务按（散修，申请人）去重。
- 留存：每个 blob 加锁（PG 用 advisory xact lock，SQLite 用写事务），预检发令牌、发送附件、上传发布、清理都走这把锁；在锁内复查 ref_count 和未使用令牌，从不强制把 ref_count 置 0；按批或按 blob 分别提交。WebP 重压生成以自身哈希命名的新 blob，把媒体行改指过去，再删除原文件。下载用打开的同一文件句柄做 fstat，再从该句柄读取。
- 在线状态、最近登录时间和心情只对本人、好友和同群成员可见，且任何一方拉黑后都不可见；其他人看到 `online:null`（`/presence`、资料侧栏、会话列表、群成员均适用）。群里拍一拍同样受拉黑限制。
- 新增 `social_dm_participants` 索引表：私聊列表和未读数据都改从这张表取，不再用前导通配符 LIKE。同一发送者的发送频率计数会先按身份串行化。schema 标志只在 DDL 确已提交后才置位。
- 教师可以被拉黑（`social_blocks`，scope='social'），课堂私信不受影响；`GET /blocks` 每项带 `scope:'all'|'social'`，`created_at` 统一为 UTC ISO。系统消息（包括建群提示）和自己发的消息都不计入未读。LAN 补传后的重推按接收者分别序列化，发送者自己那份 `mine` 为 true。

## 11. 验收
单测覆盖好友/黑名单/群/消息/撤回/幂等/配额/预检去重/分块哈希校验/留存/空间守护/在线；前端 `node --check`；本地 PostgreSQL 两账号互聊、建群、传图、传 20 MB 文件、局域网直传（同机两浏览器）、散修私聊与群聊回复；新增 `schema_social.py` 需重做原生 PG 门禁。

## 12. 境界门槛、推荐、AI 找人、个人信息面板（2026-10-27 第二轮）

### 12.1 加好友范围（只限制「发起申请」；接收、通过、拒绝、拉黑任何境界都可以）
学生的境界 = 全局修为档案的当前境界 tier（跨课程取最高，`build_student_global_cultivation_profile`）。

| tier | 可以向谁发起申请（累加） |
|---|---|
| 0 凡人 | 无。「添加」页列表为空并提示突破第一境界后解锁；仍可被加、可拉黑 |
| 1 | 本行政班同学（`students.class_id` 相同）、本校教师 |
| 2 | 同课程同学：双方各自所在课堂的课程**名称相同**（规范化：去空格、全半角统一、去「（上/下）」等版本括注）即算同课程，后端匹配 |
| 3 | 本学院同学（`students.college` 或班级 `academic_college`/`college` 相同）、散修 |
| 4 | 本校同学（`school_code` 相同） |
| ≥5 | 平台任意注册用户，并解锁「AI 找人」 |

- 教师不受境界限制（视同 ≥5）。散修主动加人沿用原有规则（只加与它互动过的真实学生）。
- 搜索、推荐、AI 找人的结果只返回当前范围内的人；`POST /friends/requests` 服务端再次校验，越界返回 403「当前境界尚不能添加该用户」+ 解锁提示。对方先向我发来的申请永远可以通过（不受我的境界限制）。
- 申请对方同意才成为好友（不变）。

### 12.2 推荐（从 tier 1 起；教师显示其任课班级学生的普通推荐，不走 AI）
- **数据来源仅后台可见**：课堂 AI 对话形成的心理画像（`ai_psychology_profiles` 最新一条）、行为画像、职业测试结果、散修人格。用户界面、接口响应、推荐理由中**绝不出现**这些内容或「侧写」二字；推荐理由只用固定文案：互补 =「你们擅长的方向正好互补」，同向 =「你们的兴趣和节奏很接近」。
- 每日任务 `social_recommendations_daily`：
  1. 特质卡：对来源有变化的人（按来源内容哈希），用快速 AI 抽取结构化特质 `{traits:{openness,conscientiousness,extraversion,agreeableness,stability ∈0..1}, learning_style, interests:[≤6 标签]}`，存 `social_trait_cards`（内部表，任何接口不返回）。
  2. 按范围桶各算一次：班级、同名课程组、学院、全校、全平台（含散修）。桶内 ≤ 120 人时一次深度 AI 调用，只给匿名编号+特质卡，要求每人返回互补 Top8、同向 Top8；桶更大时由后端按特质向量打分（同向=余弦相似，互补=社交维度互补+兴趣有交集）。结果存 `social_recommendation_pairs(scope_key, identity, candidate_identity, kind, rank, computed_on)`。
  3. 服务：按用户当前 tier 合并允许的桶，排除自己、已是好友、待处理、拉黑任一方、被拒冷却期内、超出范围的人，互补与同向各取前 5。
- 成本：每日 AI 调用有上限（`SOCIAL_REC_DAILY_AI_CAP`，默认 60），P2，失败保留昨日结果。

### 12.3 AI 找人（tier ≥ 5 或教师）
- `POST /api/social/users/ai-search {prompt}`：AI 把自然语言转成结构化条件（学院、专业、年级、班级、课程名、兴趣关键词、境界区间、角色、是否散修），后端只在**公开字段**里查询（姓名、角色、学校、学院、专业、年级、班级、心情、参与课程、境界——隐藏修为者不参与境界条件），取前 30 交给 AI 依据公开字段重排并写一句理由，返回前 10。不读取任何画像、对话、作业、联系方式。每人每天 10 次。

### 12.4 个人信息面板
- 全站点击头像或带 `data-user-card="role:pk"` 的名字，弹出 LQ 浮窗。公开信息：头像+境界标志、姓名、角色、学校/学院/班级、心情、参与课程及每门课的境界、能力雷达（学生）。教师显示学院/系部与任教课程。散修显示原型与散修主页链接。
- **永不出现**：电话、QQ、微信、邮箱、学号全号（可显示后 4 位）、任何画像/对话内容。
- 隐藏修为的学生：不显示境界、各课境界、能力雷达。
- 成为好友后额外开放：近期动态（博客、成就、境界突破）、创作（博客帖子列表）。
- 面板底部操作：加好友（按境界与关系显示「加好友 / 已申请 / 发消息 / 尚未解锁」）、拉黑。
- **匿名不穿透**：课堂讨论的临时别名、博客笔名/匿名作者、匿名评论**不能**挂面板（否则可反查真实身份）；只在实名场景挂。

### 12.5 其他
- 「添加」页顶部显示「境界解锁阶梯」：当前境界、已解锁范围、下一境界解锁什么。
- 境界提升解锁新范围时发一条消息中心通知（`social` 类别）。
- 接口：`GET /api/social/scope`、`GET /api/social/recommendations`、`POST /api/social/users/ai-search`、`GET /api/social/users/{identity}/card`；`/users/search`、`/users/suggestions` 改为按范围过滤（`suggestions` 委托推荐结果）。

### 12.6 接口合同（第二轮）
```
GET  /api/social/scope -> {tier, tier_name, is_teacher, can_add:bool, ai_search:bool,
       ladder:[{tier, tier_name, label, unlocked:bool}],      -- 六级阶梯，label 如「本班同学与本校教师」
       next_unlock:{tier, tier_name, label}|null}
GET  /api/social/users/search?q=          -> {items:[PublicUser], locked:bool, locked_hint}   -- tier0: items=[], locked=true
GET  /api/social/recommendations          -> {complement:[RecUser], similar:[RecUser], generated_on, locked:bool}
     RecUser = PublicUser + {match:'complement'|'similar', hint}   -- hint 只用 §12.2 的两句固定文案
GET  /api/social/users/suggestions        -> 保留：{items:[PublicUser]}（= complement+similar 去重，供旧前端）
POST /api/social/users/ai-search {prompt}  -> {items:[PublicUser + {reason}], remaining_today, conditions:{...用户可读的条件摘要}}
                                             403 当境界不足；429 当日次数用完
GET  /api/social/users/{identity}/card    -> InfoCard
InfoCard = {identity, name, role, role_label, avatar_url, is_wanderer, tier_badge|null, archetype|null, homepage_url|null,
            org:{school, college, class_name, major, grade}, student_no_tail|null, mood|null,
            courses:[{course_name, tier_name|null, emblem|null}], radar:{...ability radar payload}|null,
            cultivation_hidden:bool, relation:'self'|'none'|'friend'|'pending_out'|'pending_in'|'blocked',
            can_add:bool, add_blocked_reason|null, online|null,
            friend_extras:{recent:[{kind,title,url,at}], creations:[{title,url,at,likes,comments}]}|null}
PublicUser 增加 can_add:bool 与 add_blocked_reason（越界时显示「突破至「X」后可添加」）
```

### 12.7 后端实施记录（2026-10-27，§12.6 合同只做增量）

- **境界真源** `social_scope_service`：学生 tier = 当前所在课堂（`offering_student_where`）里 `learning_certificates.tier` 的最大值（即全局修为档案的「当前境界」，不重算整份档案）；教师视同 5；散修取 `wanderer_agents.tier`，但散修发申请不走境界门槛（沿用 §10 规则）。规则有两种等价形式：`required_tier`（Python，单个目标与解锁提示）与 `student_scope_sql`/`teacher_scope_sql`（SQL 候选过滤，搜索用），`tests/test_social_scope.py` 逐档对比两者。
- **范围细则**：学院 = `students.college` → `students.academic_college` → 班级 `academic_college` → 班级 `college` 第一个非空值，且**必须同校**（学院名在不同学校会重名）；学校 = `students.school_code`（空时取班级）；本校教师按 `teachers.school_code`。课程名规范化 `normalize_course_name`：NFKC（全半角、Ⅱ→II）、去空白、小写，去掉尾部「（上/中/下/一…六/I…IV/数字/A/B）」括注、「上/下册」、紧跟汉字的罗马数字或 1–6 / 一–六。
- **门槛只拦发起**：`send_request` 在既有校验后调用 `can_add`；对方已有待处理申请（我在「回应」）时不校验。403 文案「当前境界尚不能添加该用户。突破至「X」后可添加」。
- **PublicUser 增量**：所有 `public_users` 结果（搜索、推荐、群成员、申请列表、资料侧栏）都带 `can_add`/`add_blocked_reason`；`can_add` = 关系为 none 且在范围内，或关系为 pending_in；被对方拉黑不在此暴露。
- **搜索**：`{items, locked, locked_hint}`；tier 0 学生 `locked:true`。
- **推荐**：表 `social_trait_cards`、`social_recommendation_pairs`、`social_recommendation_buckets`（桶成员+特质哈希与方法，未变化的桶跳过）、`social_daily_counters`（每日 AI 计数、AI 找人次数）。特质抽取按 12 人一批（匿名编号）用快速模型；桶键 `class:<id>`、`course:<规范名>`、`college:<school>:<学院>`、`school:<code>`、`platform`；≤120 人一次深度调用，超出或额度用尽走后端打分（`backend` / `backend_fallback`，后者在次日有额度时重试 AI）；AI 失败且已有旧结果则保留。服务时 tier≥3 也读 `platform` 桶（散修只在其中），再统一按范围裁剪；互补优先，两表去重；`suggestions` = 两表合并，若为空回退到「同班同学/任课老师/在线散修」并按范围过滤。超管手动运行：`POST /api/social/admin/recommendations/run`（只返回计数）。每日任务 03:40（本地时间）由 `ensure_social_recommendation_task` 在启动时登记。
- **AI 找人**：`{prompt}` → 快速模型条件（白名单键：college/major/grade/class_name/course_name/interests/tier_min/tier_max/role/is_wanderer）→ 公开字段候选（不读心情、画像、联系方式；隐藏修为者不参与境界条件）→ 深度模型按公开字段（不含姓名）重排写理由 → 理由去联系方式样式文本与内部标记、截 40 字。`conditions` 返回校验后的条件并附 `summary`（中文摘要）。额度在调用模型前扣除。
- **InfoCard**：`role` 为 `student`/`teacher`（散修用 `is_wanderer`）；教师 `org` 额外带 `department`；拉黑时仍返回完整键集，但除姓名/头像外均为空值；心情与在线沿用 §10.2 可见性（陌生人为 null）；`friend_extras.recent` 含实名博客、成就、境界突破（隐藏修为不含突破），`creations` 只含实名帖子；雷达按学生所有课堂计算并缓存 10 分钟。
- **境界提升通知**：`social_scope_state(identity, notified_tier)`。发证书时（`learning_progress_service` 两处插入后）调用 `on_certificate_issued`：以「除本证书外的境界」为基线比较；`GET /scope` 也做一次惰性检查（首次见到的学生只记基线、不发通知，避免上线时群发）。境界上升且跨入新的范围档时发一条 `social` 通知「新的交友范围已解锁：…」，`ref_id` 按（学生, 档位）去重。
- **配置**：`SOCIAL_REC_DAILY_AI_CAP`（默认 60）。`schema_social.py` 新增 5 张表与 1 个索引，需重做原生 PG 门禁。
