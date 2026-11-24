# 游戏运行时、并发保护与数据合同

当前部署将高频游戏 API 与课堂进程分开。课堂域名和浏览器登录会话保持连续，内容源另用独立主机名。数据库连接按主站、两个 API worker 和内容站各一席分配。各项门禁的实测值与样本范围见[运行态验收记录](runtime-verification.md)。玩家包接入应以本文的协议及[已发行 SDK](../../static/minigames/lan-game-sdk.v1.js)为准。

本文供后端服务、可信宿主页、SDK 与验收工具共同遵循，定义 V1 运行时权威协议。V1 聚焦回合制、协作解谜闯关与低频策略对战：两端浏览器各自负责 60fps 画面插值与音效播放，**服务端唯一裁定席位归属、控制代数、动作次序、胜负结局、共同里程碑推进及个人存档 CAS 写入**。

---

## 1. 接入现有身份、真人栅栏与范围权限

| 现有平台模块 | 在小游戏中的接入方式与关键边界 |
|---|---|
| [`dependencies.py`](../../classroom_app/dependencies.py)、[`db/sessions.py`](../../classroom_app/db/sessions.py)、[`account_control_service.py`](../../classroom_app/services/account_control_service.py) | 实施 `require_human_game_actor`。`get_current_user_optional` 接受 broker、platform request、user-route request 三种代理上下文；`get_active_user_from_request` 在 Cookie 失败后还会尝试小程序 Bearer。游戏依赖须排除上述代理上下文，直接验证中间件处理后的当前请求 Cookie `access_token`，同时与 `get_user_session(role:id,conn=conn)` 的有效期和当前 session_id 核对，绝不从 Bearer 或请求体补身份。学生须查数据库在籍资格及 `student_is_ai_controlled`（含遗留 `wanderer_agents` 绑定），教师须在职。管理路径也过同一真人 Web 栅栏，再套用资源库教师区域与对象权限，避免代理恰有教师角色而绕过。 |
| [`remember_login_middleware.py`](../../classroom_app/services/remember_login_middleware.py)、[`remember_login_service.py`](../../classroom_app/services/remember_login_service.py) | 可信宿主从 SSR 取得首次 CSRF 绑定，所有写请求检查 Cookie、预配置主站 `Origin` 与 `X-LanGame-CSRF`；有 `Sec-Fetch-Site` 时要求 `same-origin`。CSRF 绑定 actor、登录 session 及宿主实例与到期时间，不接纳单靠相同 actor 的旧令牌。现有保持登录 `_rotate` 会产生新的 session_id；宿主须作一次同源重新认证、轮换 launch/CSRF 并撤销旧端口，身份一致才在已确认的状态上重连。新的控制页先确认已提交的原请求回执，不能把旧会话未提交的动作静默重放。登出、换号或禁用随时截断旧绑定。 |
| [`resource_access_service.py`](../../classroom_app/services/resource_access_service.py)、[`organization_scope_service.py`](../../classroom_app/services/organization_scope_service.py)、[`offering_membership_service.py`](../../classroom_app/services/offering_membership_service.py) | 复用 `teacher_can_manage_owned_row`、教师多组织隶属 `load_teacher_org_memberships` 与合班统一入口 `offering_student_where` / `student_offering_where_by_student_id`。**严禁直接把 `can_read_scoped_resource` 原样当作玩家授权**（该函数对超管和资源属主无条件返回 `True`），必须显式区分 `play`、`preview` 与 `manage` 三种用途。 |
| [`social_scope_service.py`](../../classroom_app/services/social_scope_service.py) 的 `student_global_tier`、[`learning_progress_service.py`](../../classroom_app/services/learning_progress_service.py) 的 `STARTER_LEVEL / LEARNING_LEVELS` | 学生全站修为取当前所属有效课堂已获证书的最高 `tier`（无证书计 `0`，范围 `0–10`）。仅读取持久证书事实，修为隐藏展示开关或未结课平时分不参与权限判定。 |
| [`social_relations.py`](../../classroom_app/services/social_relations.py)、[`social_common.py`](../../classroom_app/services/social_common.py)、[`presence_service.py`](../../classroom_app/services/presence_service.py) | 双向好友取 `STATUS_ACCEPTED`；屏蔽检查必须同时覆盖 `private_message_blocks` 与 `social_blocks` 双表（复用 `blocked_either / blocked_by / blockers_of`）。“好友在玩”基于独立的小游戏运行态租约判定，不与全站聊天在线态混同。 |

高频同步、预载、网关验权和后台冲刷均计入游戏连接预算。现有 `_observe_active_student → observe_authenticated_request` 可另借 PG 连接；`classify_attendance_action` 会将未匹配的 POST 归为 `platform_active/submit`。自动 sync、预载、空续租和协调器均不产生访问记录。经当前租约和服务器时钟验证的前台游玩区间进入平台访问记录，遵循原有点名窗口、名单和网络规则；游戏时长独立于课堂在线及专注时长。实施时按可信路由/操作用途在访问采集入口区分，自动流量在借连接前返回。保持登录中间件有时先于路由借库，须纳入测量，对自动流量可返回需要重新鉴权，由可信宿主在受限并发下单次恢复，不能冒名触发真实登录。真实人工登录、既有签到及课堂提交沿用原规则。[访问采集与动作分类](../../classroom_app/services/login_attendance_access_service.py)

### 1.1 内部身份与局内匿名投影

- 数据库存储的 `actor` 统一规范化为 `student:<pk>` 或 `teacher:<pk>`，彻底隔离相同整型主键的师生数据。
- 可信宿主持有绑定 `(actor, session_id_hash, game_id, version_id, purpose, host_instance)` 的启动上下文；沙盒子帧仅能通过已握手的 `MessagePort` 获得：
  - 本房或本单人局新生成的随机不透明 `playerId`（同房候场、开局与断线重连保持不变，跨局重新生成，不可反推账号）；
  - 可展示称呼 `displayName`（优先取本人设置的游戏昵称，留空取平台档案可展示名，再空回退“玩家 1/2”，同房重名自动追加 `·2号位` 等席号后缀）；
  - 本局座位号 `seat`（`1..N`）与宿主静音状态 `audioMuted`。
- **绝对红线**：学号、工号、数据库主键、`access_token`、`session_id`、私房四位邀请码、好友图谱与未脱敏聊天记录一律不得跨过 `MessageChannel` 进入游戏包。教师预览运行于独立的 `preview:` 命名空间与临时内存/测试槽位，零污染正式存档与热度统计。

### 1.2 统一授权判定 `can_play` 与批量投影

服务端提供单对象判定 `can_play(conn, actor_ctx, game, version, *, purpose='play')` 与等价的目录批量过滤 SQL 构建器（由单元测试用同一组正反向量钉死等价性）：

1. `purpose == "preview"`：仅允许对该 `game` 具备后台管理权的在职教师（归属教师或在职超管），且目标 `version.status == "ready"`。
2. `purpose == "play"`：
   - 账户为在籍真人（学生 `enrollment_status == 'active'` 且非散修/AI 托管；教师 `is_active == 1`）；
   - `game.status == "listed"` 且版本为当前发布版（或本人正在运行中且未下架的原锁定 `ready` 版本）；
   - **组织可见范围命中**（单选其一）：
     - `private`：仅该游戏的归属教师与在职超管；
     - `classroom`：命中 `game_scope_targets` 任一指定课堂（学生经 `offering_student_where` 含合班判定，教师为该课堂授课教师）；
     - `class`：命中任一指定行政班（学生 `class_id` 命中，教师任教或创建该班）；
     - `department / college / school`：玩家真实组织隶属（`school_code` 先行对齐，防止跨校同名学院串权）与游戏组织范围一致；
     - `public`：全体有效登录真人师生；
   - **修为门槛**：若玩家为学生，须满足 `game.min_tier <= student_global_tier <= game.max_tier`（默认 `0..10` 闭区间）；教师按其作品受众范围游玩，超管可访问任意受众范围内的已上架游戏并在管理台核查待审版本。

目录、四个旁栏、图标读取、启动租约、建房/加房、排队、动作、存档与增量同步全部走服务端复核；任何变更 `status`、受众范围或修为区间的管理提交都会原子递增 `games.policy_revision`，使失权者的在途租约与会话立即失效。对无权获知的私有游戏或房间统一返回 `404`。

`manage` 要求在职真人教师且为游戏属主或现有平台超管，检查目标的可授予范围；草稿/退架查询走管理投影。预览只访问该教师可管理的 `ready` 包，`deleting/deleted` 一律拒绝。班级教师关联含其任教合班的附属班；教师所属系与学院结合学校和可得的上级学院匹配，无合法 scope 目标、空学校及同名跨校默认拒绝。状态或受众变更后只有失权玩家失去正式访问；下架与删除则对全体生效。已结束且无权的游戏也不进入历史、存档、模板、头像或榜单投影。

---

## 2. 持久化模型、分离式占位与共享主机并发保护

### 2.1 关键架构改进：单人按游戏占位 vs 多人全局单占位

若将无等待的单人休闲局与需要队友守候的多人局塞进同一张以 `actor` 为主键的全局互斥表，学生在大厅随手点开 A 游戏单人拼图后，未显式退出便切回大厅点开 B 游戏，就会频繁撞上令人困惑的 `409 ACTIVITY_EXISTS` 阻塞弹窗（尤其浏览器关闭标签页时 `pagehide` 网络请求并不保证送达）。

因此 V1 将活动控制权拆分为两层正交结构：

- **`game_multiplayer_claims`（主键 `actor`）**：全平台每个人同时**最多参与一个**多人承诺（随机排队票 `queue`、候场/倒计时房间 `room` 或进行中的多人对局 `multiplayer_run`）。进入新多人活动前若有其他多人未完活动，明确返回 `409 MULTIPLAYER_ACTIVITY_EXISTS` 供用户回到原房间或确认退出。
- **`game_solo_sessions`（联合主键 `(actor, game_id)`）**：记录该玩家在**当前游戏**的活跃单人 `run_id`、`control_epoch` 与更新时间（单账户保留最近活跃的至多 5 款单人未完局，超限按 LRU 自动温和归档为 `paused`，已确认私档与共同 `rank` 丝毫无损）。这样：
  1. 跨标签打开两款**不同**的单人游戏互不干扰、零 `409` 摩擦；
  2. 同一款单人游戏打开两个标签页时，由 `(actor, game_id)` 上的 `control_epoch` 严格拦截旧标签页的并发覆写（`409 CONTROL_MOVED`）；
  3. 同款游戏从单人切换至组队时，宿主用 `progress.onFlush(handler)` 取得待存私档及 `expectedPrivateRevision`；在实际建房、加房或入队命令内同事务完成保存、挂起原单人会话与取得多人占位。没有改动时省略私档，不能跳过控制权核对。

打开第二个同游戏页签先恢复只读快照；只有本人在新页明确接管才提升 epoch，旧页收到 `CONTROL_MOVED` 后停止输入与续租。跨游戏 LRU 淘汰在每 actor 锁内按 `(last_control_activity_at, game_id)` 选出第六款需要挂起的会话，先改变控制代数再释放活跃记录，旧标签进入只读。只保留最后服务端确认的数据，未确认草稿在原标签提示恢复；已处多人局时仍可玩其他单人游戏，同款单人与多人不并行写。取消尚未提交的组队表单不改动单人会话。切换动作若遇满房、错码、已有其他多人活动、私档 CAS 冲突或保存失败则整笔回滚，原局仍可操作；已另行成功的自动保存继续有效。响应不确定时重试原键，不能既另开一间房又恢复原局。

### 2.2 核心运行态逻辑表

| 逻辑表 | 主键、关键字段、索引与生命周期 |
|---|---|
| `game_launch_leases` | `lease_id` 主键，含 `actor, session_binding_hash, game_id, version_id, purpose, host_instance, policy_revision, handle_digest, expires_at, revoked_at`。对外资源前缀使用稳定随机 handle，摘要唯一；内部 HMAC 按[包运维合同](04-package-operations.md)保护授权记录。依据强一致状态续签同一前缀或撤销。索引 `(actor,game_id)`、`(expires_at)`。 |
| `game_multiplayer_claims` | `actor` 主键，含 `game_id, version_id, kind(queue/room/run), ticket_id?, room_id?, run_id?, control_epoch, lease_until`。保障单人同时只占一个多人队列或房间席位。 |
| `game_solo_sessions` | `(actor, game_id)` 主键，含 `run_id, version_id, host_instance, control_epoch, last_control_activity_at, phase`。旧主控被隔离。保留挂起快照直至该用户恢复或游戏删除，限五款约束只统计活跃局。 |
| `game_queue_tickets` | `ticket_id` 主键，含 `actor, game_id, version_id, bucket_key, target_size, state(waiting/assigned/canceled/expired), queued_at, lease_until, room_id?`。索引 `(bucket_key, state, queued_at, ticket_id)`。 |
| `game_rooms, game_room_members` | 房含 `room_id, game_id, version_id, kind(coop/versus), source(private/random), owner_actor?, target_size, requested_start_rank?, planned_start_rank?, plan_revisions?, phase(waiting/countdown/running/closed), generation, starts_at?, run_id?, revision, stream_id, expires_at`。成员 `(room_id, actor)` 与活跃 `(room_id, seat)` 各唯一，上限 10 席，含 `player_alias_id, display_name, status(active/reconnecting/left), lease_until`。 |
| `game_room_codes` | 主键 `code_lookup`（HMAC-SHA256 确定性索引），含 `room_id`（唯一）、宿主展示用受控密文与 `expires_at`。仅私房 `waiting` 阶段有效；换码、开局或解散后转入短期冷却防重用，日志严禁打印明文码。 |
| `game_runs, game_run_players` | 局 `run_id` 主键，含 `room_id?, generation?, game_id, version_id, mode, stream_id, revision, start_rank, rules_node, turn_seat, canonical_state, started_at, last_activity_at, ended_at?, outcome?`；唯一索引 `(room_id, generation)` 杜绝并发重复开局。玩家表 `(run_id, actor)` 主键，索引 `(actor, started_at DESC, run_id)` 支撑个人历史。 |
| `game_stream_events` | `(stream_id, seq)` 主键，含 `game_id, room_id?, run_id?, kind, generation, revision, server_time, public_payload`。仅记录权威状态跃迁事实（严禁写入逐帧坐标）；裁剪旧明细前先固化最新快照。队列票、房、单人局均分配独立 stream；多人 run 使用所在房的 stream。 |
| `game_room_messages` | `message_id` 主键，含 `room_id, sender_actor, body, created_at, expires_at`，索引 `(room_id, message_id)`。正文与公共事件流分离，按接收者实时双向屏蔽过滤组装，最长保留 7 天，彻底删除游戏时同步清空。 |
| `game_player_progress, game_progress_changes` | 主键 `(game_id, save_schema, actor)`。私档 `private_json, private_revision` 与共同里程碑 `shared_rank, shared_revision` **双修订号完全解耦**。变更审计表记录多人开局对齐前后的 `rank` 与幂等引用。 |
| `game_team_templates, game_user_preferences` | 历史私队模板 `(owner_actor,template_id)` 存 `game_id,kind,target_size,team_name,last_run_id,roster_fingerprint,last_used_at`。稳定指纹由服务端对排序后的正式私队参赛 actor 列表做带密钥摘要，不能回传；每人最近 20 项。用户偏好 `actor` 主键含 `display_alias,share_playing`（默认关）、`audio_muted,revision`。 |
| `game_command_receipts, game_aggregate_daily` | 回执以 `(scope,actor,operation,idempotency_key)` 唯一，`scope` 为游戏或明确的账户级命名空间，存规范化请求摘要、目标对象、已提交结果引用和期限。避免偏好/接管等无 gameId 的命令因 SQL NULL 绕开唯一索引。至少保留至活动结束后 24 小时。日活 `(game_id,day,actor)` 去重，近 7 个展示日取 distinct 真人并集（若采用滚动 168 小时则须额外查询边界开局事实）。 |

### 2.3 数据库连接预算、事务与锁序

`docker-compose.yml` 的主应用配置了 `POSTGRES_POOL_MAX: 12`，[池实现](../../classroom_app/db/postgres.py) 在每个进程内持有自己的池。2 vCPU／4 GiB 为本方案的混压配置。小游戏合计同时占用最多 4 条业务库连接，须在目标拓扑验证。

1. 设有界执行队列与入场许可，再调用专用 `games-db` 执行器；标准 `ThreadPoolExecutor(max_workers=4)` 本身并不限制积压。每项工作最多借一条连接且显式传给子服务，含鉴权、网关校验、匹配器及续租冲刷。单进程可分配 4；多进程静态分摊所有参与者预算（例如两个各 2），或由统一有界服务持有数据库访问。不得为每个 worker/上传作业再各开 4 条连接。超过排队/签出预算 1.5 秒回 `503 Retry-After: 2`；等待方取消时已运行的任务保留许可至事务结束。用实际上限与课堂对照组延迟验收余量。
2. PostgreSQL 在游戏事务内用 `SET LOCAL` 或 `set_config(..., true)` 设置 `lock_timeout=500ms`、`statement_timeout=2000ms`，结束后恢复池默认值。SQLite 写路径在先读后写之前用 `begin_immediate_transaction`，设置有界 busy/retry 预算；超时退出整笔事务再以同键重试。文件 I/O、ZIP 解包、网络发送与等待客户端不持有数据库锁。参见[连接抽象](../../classroom_app/db/connection.py)。
3. 所有并发操作采用下列顺序。PG 的 `social_common.serialize_on` 只取排他 advisory 锁，适合桶、缺行 actor 和回执等互斥键。游戏授权栅栏使用同一游戏行 `FOR SHARE`（游玩）/`FOR UPDATE`（发布、删除）或同一稳定 advisory key 的共享/排他变体；项目已有[两种锁模式的实现范式](../../classroom_app/services/group_assignment_service.py)。
   游戏栅栏 → 排序的匹配桶 → 房/局 → 排序的 actor → 存档/幂等回执。同层多资源按相同稳定顺序；领取席位、限五活跃单人和重试在行不存在时也先锁键，唯一约束兜底。转换过程中若发现需要更早层的锁，回滚重试；禁止在持 actor 锁时再回头锁另一游戏。单人 LRU 先推定受影响游戏集、按序锁游戏并在 actor 锁内复查。PG 解锁/SQLite busy 发生时有界退避。数据库迁移按[显式迁移要求](../../classroom_app/db/migrations/README.md)登记、验证双引擎索引与可回滚路径。

4. 与下架、撤权竞争的写入以栅栏内重新读取的当前事实线性化；进程内缓存不可批准已撤销请求。数据库写入、事件、回执一起提交后才唤醒宿主。对账号停用、换号、屏蔽或班级变更等外部事实，在提交前核对相应事实/修订；需要相同提交顺序的写路径须与事实更新共用锁或以事务版本检查，避免读取旧快照后提交。

---

## 3. 组队、无歧义房码、公平匹配与对局状态机

### 3.1 无歧义四位私房邀请码规范

课堂投屏或口头报码时，`0/O` 与 `1/I/l` 极易混淆。服务端仅从 **31 个零歧义字符集**均匀抽取 4 位码：
- 字母集（23 个，剔除 `I, L, O`）：`ABCDEFGHJKMNPQRSTUVWXYZ`
- 数字集（8 个，剔除 `0, 1`）：`23456789`
- 至少含一个字母及一个数字，有效空间 `31⁴ − 23⁴ − 8⁴ = 639,584`。创建房间、占位、插入码索引为一个事务；使用安全随机源均匀抽样并对不符组合重抽，遇唯一键碰撞回滚至 savepoint 后重新抽取，达到有界次数才返回可重试的暂不可用。到期、换码、开局后保留无身份关联的短期冷却摘要至原有效期，避免旧码意外打开新房。
- 输入归一化：去首尾空白、剥离空格与短横线 `-`、转为大写后校验长度与字符集。数据库以带服务端密钥的 `HMAC-SHA256(code)` 作为主键索引；默认 15 分钟未开局自动过期，房主候场可幂等换码。
- 防枚举限流：仅对**失败尝试**计数——单 `actor` 每分钟最多 6 次失败（超限冷却 60 秒，跨进程共享），校园网共享出口 IP 另设宽阈值（每分钟 600 次失败），保证整班同学同一路由器下正常扫码/输码入房绝不误伤。

建房必须 `privateRooms=true` 且人数在本版 `[minPlayers,maxPlayers]`，加入须绑定详情页的 gameId、版本、成员资格和双方无屏蔽，锁定房后重新检查剩余席位。码不进入 URL、日志、推送或游戏脚本。无效、已满、过期、跨游戏和不可见房返回统一结果；本人同一次加入重试由幂等回执认领原席位。历史模板以本人已正式参与过的私队阵容区分，同人数不同成员分开。列表展示队名、规模、上次使用时间；仅本人当时的队友且目前仍是已接受好友、无屏蔽、可见时，才可展示其可展示名，其他只显示“历史队友”。重新建队时按现行版本校验条件，生成新房新码，各人自行加入，旧码、旧席位和成员资格不能继承。

### 3.2 公平抗饥饿随机匹配算法

按 `(game_id, version_id, kind, exact_target_size)` 严格隔离分桶（绝不跨版本混配、绝不塞入散修机器人充数、绝不擅自缩减人数）：
- 入队需 `targetSize ∈ randomSizes`，记录回队用的原始 `queued_at`。每次入队或协调器补扫先按 `(queued_at,ticket_id)` 稳定分页，每窗口最多 32 张有效票，持久化桶扫描游标及轮转代数，尾部回绕。
- 优先最早可组的锚点，在等待时长相近的候选中以服务端随机源抽取；批量查屏蔽后贪心挑选两两兼容的 N 人，有界重排尝试，禁止穷举 32 选 10 的组合。拿到规定顺序的桶、房、actor 锁后复核身份、游戏可见、精确人数、活动控制、租期、票状态及双向屏蔽。一个事务分配 N 张票、固定席号、产生房及 countdown 事件；任一失效则重选，不部分占座。随机房无房主、无邀请码。
- 每次最多处理 4 个窗口；未配成的老票保留原等待时间并在后续轮次与窗外候选重试，同时允许后方已兼容组合成局。到队尾回绕而非反复只查前 128 人。取消与匹配竞争共用桶/actor 栅栏；若取消先提交则不分配；若已分配，按本人离开该代次语义处理，保留其他合格成员原时间。没有可兼容组合时保持可取消的等待态，不许诺等待上界。持久队列负责进程重启后的续扫。

当前版本/可见范围资格变化后的明细处理以 §3.3 为准。

### 3.3 房间与对局权威状态迁移表

| 当前状态与触发条件 | 原子事务动作与对外可观察结果 |
|---|---|
| **私房 `waiting` 增减成员** | 入房分配最小空闲 `seat (1..targetSize)`；成员离开释放席位与 `game_multiplayer_claims`；若房主离开，由在席最早成员自动接任房主，全空则置 `closed` 并销毁活码。 |
| **私房满员 / 随机房凑齐 N 人** | 原子冻结人数、座位和按 §5.2 求出的本局起点 `S`、`generation += 1`、`startsAt=serverNow+5000ms`，写 `countdown.started`。事务提交后唤醒订阅并返回快照。倒计时只做预告，此时不写个人进度。 |
| **倒计时有人退出／失联／失权或换版** | 作废该 `generation/startsAt`。私房剩余合格者回 waiting，必要时换房主并签新码；原房主所选起点须由新房主重新确认且不超其解锁值，否则回默认。随机房关闭，仍在线有意愿的合格者携原排队时间回同桶。整款下架或当前版本替换时，两种来源的全部未开局活动均撤销，禁止回到旧房或旧队列。 |
| **到达 startsAt** | 有界到期扫描器或第一方显式命令推进；GET 仅观察。锁定房、代次及相关 actor 后复核资格、版本、全员有效租期、双向屏蔽及 `S`。同一事务唯一创建 `(room_id,generation)` 的 run、初始化局面及共同档，失效房码并写 `run.started`。客户端显示倒计时按服务器时钟偏移，零秒等权威快照；后台返回不重复播 5 秒。 |
| **running 收到意图** | 校验当前主控、成员座位和局修订后，应用一条合法规则，写局状态、修订、事件及回执。仅抵达新里程碑时向上推进成员共同进度，保持房和占位继续运行；多人局仅到终态时结算、关闭房、释放占位。二者可以在同一步发生。 |
| **运行中断线或离开** | 控制租约过期将该席标记 `reconnecting` 并暂停动作；以持久 `lease_until+60s` 为恢复截止，调度延迟不额外延长。本人在期限内重新鉴权并取回控制，只在所有必需席位都恢复时发 `run.resumed`。主动离开或任一席超时则全局 `interrupted`、无胜负、保留已确认档案，释放该局占位，不补人。休眠/进程重启先核对资格、租约与截止时间再推进局面。 |
| **下架、范围收紧或删除** | 更新 `policy_revision`。下架/删除终止整款游戏的参与、资源读取/续租、存档与历史投影；范围收紧只撤销失权者，受其退出影响的房按等待/运行规则处理，仍有权限的其他房正常。给被撤权页返回无敏感内容的终止结果，不以事件到达作为生效条件。 |

---

## 4. API 契约、合并续租邮箱与零占池增量同步

以下是可信宿主的 `/api/games/v1` 合同。普通业务写入携带 `Idempotency-Key`、`X-LanGame-CSRF`、所属控制代数；资源续期和活跃脉冲按现有租约与控制代数更新单调截止，同代重复请求不增加业务事实。每个 SDK 调用由宿主限定当前游戏、会话、端口代次与许可。

| 业务域 | 方法与相对路径 | 关键请求参数与响应约束 |
|---|---|---|
| 目录与四维旁栏 | `GET /catalog`、`GET /games/{gameId}`、`GET /sidebar`、`GET /games/{gameId}/icon` | 统一走服务端可见集合过滤与游标分页；图标路由返回主站重编码安全小图并校验可见性。 |
| 启动与资源租约 | `POST /games/{gameId}/launches`、`POST /launches/{leaseId}/renew`、`POST /launches/{leaseId}/revoke` | 传 `hostInstance,purpose`，返回 `leaseId,versionId,csrfToken,expiresAt,contentEntryUrl`。外部稳定随机 handle 由隔离内容源解析，内部 HMAC 与后端可撤销记录分开。默认 90 秒，提前 30 秒续签同一前缀。第一方业务 API 不接受内容 handle 作为身份。内容源独立主机名要求和相对资源续期见[包运维](04-package-operations.md)。 |
| 偏好、活动恢复与模板 | `GET/PUT /me/preferences`、`GET /me/activity`、`GET /me/history`、`GET /me/teams`、`DELETE /me/teams/{templateId}` | `PUT /me/preferences` 带 `expectedRevision`；SDK 查历史时宿主强制锁定当前 `gameId`（每页上限 20）。 |
| 本人进度与私档导入 | `GET /me/progress/{gameId}`、`PUT /me/progress/{gameId}/private` | 写入携带 `runId, expectedRevision, privateSave`，校验 `save.private` 能力与 `writeback.json` 类型/大小上限（32 KiB），仅对 `private_revision` 做 CAS。 |
| 单人开局、选章与离开 | `POST /games/{gameId}/runs`、`POST /runs/{runId}/leave`、`POST /runs/{runId}/finish`、`POST /games/{gameId}/takeover` | `POST /runs` 支持可选 `startRank`（`0 <= startRank <= unlockedRank`，省略则取最高已解锁 `rank`）。`/leave` 可顺带原子提交最后一次私档。`/finish` 仅用于无规则纯单人局记录一次“自主完成”。 |
| 私房与随机匹配 | `POST /games/{gameId}/rooms`、`POST /games/{gameId}/rooms/join`、`POST /rooms/{roomId}/rotate-code`、`POST /rooms/{roomId}/leave`、`POST /games/{gameId}/queue`、`POST /queue/{ticketId}/cancel` | 建房传 targetSize、可选 `teamName/templateId/startRank`；加入传 code；随机传 targetSize。三种进入多人操作均可附 `fromSolo:{runId,controlEpoch,pendingPrivateSave?,expectedPrivateRevision?}`，依 §2.1 原子切换。已提交同键认回原结果。 |
| 局内意图、房聊、同步 | `POST /runs/{runId}/intents`、`POST /rooms/{roomId}/chat`、`POST /me/heartbeat`、`GET /sync`、`POST /sync` | 恰好一个 `roomId / runId / ticketId`，`after` 为该 stream 游标，`waitMs` 最多 8000。GET 只读（旁观/恢复），控制页可使用同源、CSRF 保护的 POST sync 将读取与心跳合并。多人房与 run 共用事件流，单人 run、排队票分别有自己的游标。每页最多一条在途请求。 |

**继续与重新开局的命令语义。** 单人页刷新省略 startRank，已有同游戏有效会话时恢复原 run。正在玩的用户显式选择重玩章节时，同一 `POST /games/{gameId}/runs` 传 startRank、原 runId/controlEpoch/expectedRevision 及切换前的私档修订。按 §2.1 的存档屏障校验，原子保存草稿、以 `replayed` 原因结束原局（不记为通关）、创建并绑定新 run。拒绝陈旧控制者/未解锁章节时原局仍可玩；同键重发只返回已经创建的新局。私房在创建时确定选章；原房主候场离开后，清除其显式选章并告知继任者将采用默认最高进度，重新满员再生成预告与五秒代次。

**并行游玩与好友动态。** 每位好友最多一条。先过滤当前玩家和好友均可玩、对方开启展示且房/局与控制租约有效的记录；在候选中优先已开局的多人局，其次按最近的真实单人交互和稳定 runId 排序。仅预加载、仅后台续租、已挂起或已失权的活动不能进入展示。活动记录区分 `last_player_action_at` 与 `lease_until`，心跳不更新前者。关闭展示后后续服务端响应立即生效，已打开的大厅按第二章的刷新期限移除原行。

### 4.1 有界合并续租、唤醒与进程重启

控制页通过 POST sync 附 `(activityId,hostInstance,controlEpoch)` 合并活跃报告。只有已经写入数据库且仍有效的当前控制者可以续租；无活跃同步的前台单人每 25 秒调用一次 heartbeat。服务端接收时间作为到期计算依据。只读 GET、旧页、过期租约和下架数据不得延长活动。显式接管要求本人当前合法会话、目标 ID 和预期 epoch，事务提升代数，原页进入只读。

1. 每个实例的 `_GameLeaseRuntime` 对 `(activityId,actor,sessionBinding,controlEpoch)` 保留单调最新确认，至多 15 秒按有界批次冲刷。数据库用条件 UPDATE 复核游戏状态、当前占位、epoch 与 session，取 `max(stored,observed_server_time+45s)`；已离开的行不允许 UPSERT 复活。仅在成功提交后移除本批已认领版本，新到的更晚确认须保留。崩溃可能丢失内存心跳，客户端恢复同步时须读持久期限。45 秒仅用于控制租约，另行使用 90 秒资源租约。活跃时的批量写入率取决于 worker、分批及负载，应在混压中实测。
2. 有界 `asyncio.Event`/LRU 仅提示“可能有新事件”。注册等待再复核持久游标，防止检查与订阅间漏掉最后一席。挂起前释放连接；取消时退订并回收空监听。多 worker 按已订阅流 ID 与索引批量追赶（建议至多每 500ms 一批），禁止全表扫；发响应前重查当前对象、会话、游戏权限及个人投影，允许同批复用授权事实。TTL 命中不能绕过注销/撤权。跨进程漏唤醒、缓存清空或广播失败都可通过持久事件序列补偿。单 worker 的本地唤醒速度不能作为跨进程承诺。
3. 响应 `{streamId,serverNow,throughSeq,events,more}` 或 `{streamId,serverNow,snapshot,throughSeq}`。事件带 `seq,type,generation,revision,serverTime`；`seq` 随流增长，`revision` 是目标业务实体版本，聊天/心跳不得使游戏动作的修订过期。同一一致性读取边界产出快照与游标；额外一页最多 100 事件或 64 KiB，游标过期、流不符或裁剪断档直接发快照。客户端丢弃重复 seq，用 `throughSeq` 继续，空过滤事件也可前进。只在提交后通知，定时器与重连据实恢复。

### 4.2 幂等回执、投影与房间对话
1. 对建房、入队、选章、接管、动作、聊天、存档和完成等请求，按认证身份、作用域、操作与键查回执。摘要覆盖方法、对象、规范化正文与该操作的预期修订。同键同摘要回原结果引用；同键不同摘要 `409 IDEMPOTENCY_MISMATCH`。先复核当前登录和可读对象/当前游戏授权；命中后不重复验证首次写条件（原动作可能已释放席位）。未命中才检查主控、状态、修订及命令资格，效果、回执、事件在同一事务内。并发重复命令等首笔提交/回滚后再判。回执不重发失效凭据、已删除信息。超时沿用原键，`409 STALE_REVISION` 回可恢复快照；新意图须新键。`401/403/404/422/429/503` 分别处理登录、权限、隐藏资源、无效命令、限流和暂忙。
2. 房状态投影给成员 seat、随机场内 playerId、展示名、必要的在线状态、共同局面与自己私档。本人历史仅含当前游戏的起止/模式/结果/进度，不能从 SDK 扩大到其他游戏。房聊仅对在席且双向无屏蔽的用户开放，`room.chat` 能力只允许包打开宿主面板，正文不进游戏桥、公共快照或公共事件。通过可信宿主读取/发送，消息是纯文本（如 300 字、每人 5 秒最多 3 条），提交后公共流最多有消息引用，宿主按收件人重做授权；离房即失权。好友在玩默认关闭，仅展示获准分享、真实活跃、主动控制未失效且双方均可见的已接受好友；不同游戏同时单机时按最后前台操作仅展示一款，防止重复或错误在玩。最近、热门、推荐应先过滤再排序分页，遵循[页面规范](02-interface-and-management.md)。

---

## 5. 确定性规则引擎、共同里程碑原子对齐与自由重玩

### 5.1 内置确定性规则引擎（绝不执行上传脚本）

- **`finite-turn-v1`（轮次、协作闯关与有限状态图）**：由服务端校验通过的有向状态图驱动。每个状态声明 `outcome`（`null / completed / draw / actorWins`）；每条转移边声明 `from, actor(turn/any), type, payload, to, turn(keep/next)`。首回合为最小在席座号，`"next"` 在本局实际参与座号集合上循环推进（单人 `[1]` 循环回 `1`；四人 `[1,2,3,4]` 依次轮转）。当进入的状态匹配 `milestones[k].resumeState` 且 `k > current_run_max_rank` 时推进本局里程碑。验收按固定规则图验证不可越级；对于轮流执舵并承诺人人行动的玩法，检查从初始节点按实际席位顺序首次通关时覆盖团队，不以边数替代轮转校验。已解锁关卡续玩可能只剩一两步，应展示剩余流程或允许从头重玩，勿承诺后段自动为所有席位新增回合。
- **`grid-line-v1`（双人连线棋盘引擎）**：校验 `3 <= line <= size <= 19`，双人交替向空合法格 `(x, y)` 落子，落子后沿横、纵、正斜、反斜四轴统计连续同色棋子数：达 `line` 判当前席位胜，盘满无连线判平局，先判胜后判平，终局后拒绝后续落子。

所有引擎从认证端口与绑定记录派生席号，命令只收 `{intent:{type,payload},expectedRevision}` 与宿主提供的重试键。非成员、非 running、旧控制者、错误回合、未声明/超限载荷或过期修订不得改变状态。自由单人无权威规则时 `finish` 仅记本人自主完成，禁止代替联机结算。

### 5.2 共同进度结构、开局原子对齐与选章重玩闭环

1. **双轴完全解耦**：同一存档谱系 `saveSchema = (writeback.series, writeback.saveVersion)` 下，每位玩家的 `privateSave`（音效、配色、个人地图标记、分支收集品，受 `private_revision` CAS 保护）与共同线性里程碑 `shared_rank`（`0 .. len(milestones)-1`，受 `shared_revision` 保护）完全独立，一方更新绝不导致另一方报 `409 STALE_REVISION`。
2. **解决“通关后卡在终章前”的重玩设计**：
   - 多人合作默认取全队成员已确认 `shared_rank` 的最大值 $M = \max_i(\text{rank}_i)$ 作为本局共同起点 $S = M$；
   - 单人或合作私房可由发起者请求 `startRank`，数据库字段 `requested_start_rank`，须为非负整数且不超过该发起者当前已解锁值。对战固定使用引擎规定的初始局面；随机不接收客户端指定起点。
   - 满员时锁定成员、版本、所选章、各人同谱系的 shared_revision，计算并持久保存 `planned_start_rank=S` 与相应依据。默认 `S=M`；选章 `S=startRank`。全组只见本局起点，每人仅见自己的前后变化。开局锁定房和排序的成员/进度行，确认成员、发布状态、权属、资格、租约、代次和预告依据；若共同进度/名单变化足以改变预告，取消原代次、重新预告及倒数，不静默换起点。仅在开始事务同时写 run、席位、`canonical_state=resumeState[S]`、各人 `new_rank=max(old_rank,S)` 及有实际变化的审计（保存本人旧 rank、修订、runId）；私档字段原样。规则无共享轴时 rank 恒为 0。局中只按已验证的本局下一里程碑向上更新，重玩旧关不降低最高 rank。失败任一环全部回滚，乐观锁/唯一键守住重复提交。
3. **版本兼容与离线导入安全边界**：
   - 同谱系追加带默认值的私档字段或末尾里程碑须证明老读写器和在途局兼容。旧规则看不到后来添加的字段，旧版私档“全量替换”仅覆盖其可识别字段，由服务端保留未知新字段并检查修订。只提高 `saveVersion` 不能授权跨谱系映射；同一游戏有存档时，无经平台验证的可回滚迁移则拒绝不兼容更新或回滚，可另发新游戏。迁移至少按 actor、源/目标版本及原修订幂等，原档可找回。旧版在途若无法与新语义共存，应待结束后再发布。
   - 离线导出的本地存档 JSON 如需带回云端，须由本人在可信宿主选择同游戏同谱系文件、预览白名单 `privateFields` 差异后按 `expectedRevision` 显式确认导入；**共同 `shared_rank`、里程碑与对局胜负记录严禁由外部导入文件改写**。

---

## 6. 后端与并发必测验收矩阵

后端单元与状态机测试统一通过 `python tools/test_backend.py` 在隔离临时目录运行；原生 PostgreSQL 锁序、咨询锁与多 worker 恢复在自有隔离合成集群验证：

| 验收场景 | 可观测通过判据 |
|---|---|
| **真人栅栏与同号师生隔离** | 构造 `student:1`、`teacher:1`、散修 `wanderer`、退学学生与 Agent 代理令牌：非真人/失籍请求 100% 被 `require_human_game_actor` 拒绝；同数值主键师生私档、历史与占位零串档。 |
| **五款单人上限与原子切多人** | A、B 可同时玩；同游戏第二页先只读，明确接管后旧 epoch 的写入/心跳被拒。并发打开第六、第七款仍最多五款活跃，同款参与多人时单人旧页不能写。强制最后私档 CAS 失败、错码、满席或跨游戏已有多人承诺时，离开单人/创建房间/增加席位全部回滚。 |
| **无歧义码并发抢席与防枚举** | 20 个并发请求争抢 10 人房最后 2 席：绝不超员，恰好触发唯一一次 `generation` 与 `startsAt`；连续输错 6 次触发单账号 60 秒冷却，同班同 IP 的 50 名合成人分别加入有余位的其他房，不被单账号失败冷却误伤。 |
| **匹配抗拉黑饥饿与换版清理** | 200 名合成玩家混含多对双向拉黑、跨版本与 2/4 人桶：零跨桶、零拉黑同房、排首拉黑者不阻塞后方兼容玩家成局；发布新版瞬间旧版候场与队列全部安全退回提示重进，已运行局按原版完赛。 |
| **共同进度原子对齐与通关重玩** | 进度 `(0, 2, 2)` 三人组队：倒计时第 4 秒取消时 0 号玩家仍为 `0`；正式开局事务注入中途异常时全员回滚；正常开局后仅 0 号升至 `2` 且三人私档不变；通关后选 `startRank=0` 重玩全程正常且最高 `rank=2` 不回退。 |
| **共享主机混压** | 在 2c/4G 隔离环境做课堂无游戏基线，再叠加 200 玩家、20 个十人房、至少 30 分钟，交错上传/下架。跨进程合计游戏 DB checkout 峰值 ≤4，课堂 P95 相对基线劣化 ≤10%，非故障注入 5xx <0.5%。目标命令及短同步 P95 ≤500ms、动作对端可见 P95 ≤2.0s、全员首次显示 5 的 P95 ≤600ms、服务端启动偏差 P95 ≤300ms。有意挂起的空 long poll 分别记录等待时长和唤醒至回包耗时。记录写事务实际频率、队列、超时、峰值及异常恢复，不把每进程预算写成全机实测。 |
| **认证来源、静默续登与控制权** | Cookie 缺失但有有效 MP Bearer，或存在任一代理上下文，均不可进入玩家/管理 API；普通登录真人可进入。进程 A 注销或数据库禁用后，进程 B 缓存命中、挂起 sync、预览与网关 Range/304 均拒绝。自动续登换 session_id 后可在同一 actor 下重握手；换号不继承，旧租约/端口/CSRF 全失效。 |
| **游戏后台与课堂访问分离** | 对开启保持登录的合成学生在两进程持续 POST/GET sync、挂起、预载及批续租。检查签到访问/提交分类和各来源 PG checkout：自动流量没有新增主动课堂提交或考勤证据、没有绕开配额的第二条连接。用同一账号真实登录、签到、提交课堂作业仍产生既有记录。 |
| **队列公平与租约崩溃恢复** | 两个 worker，128 张以上票，前窗受屏蔽阻塞且后窗可配，扫描有界且后窗仍成局。取消与分配相撞不残留半房。接管后旧 epoch 的迟到批量续租、错误实例和已退房 POST 不得复活席位；停止冲刷/重启后按已有截止判定，60 秒后原房终止。 |
| **回执、隐私与游标一致性** | 并发同键同内容只成功一次，同键异内容 409；开局/离开提交后丢响应，再发原键返回原结果；下架/删除后的旧回执不返回敏感内容。流裁剪后用快照无漏事件，退出或拉黑后旧端口无房聊正文。两个同人数不同阵容历史模板分开，移除好友不会泄露其昵称，重建产生新码。 |
| **预告、重玩、里程碑与回滚** | 三人成员最高 `(0,2,2)`，默认从 2 开局，若房主选 1 则仅把 0 推到 1；选 0 则均不升降。取消或写到第二人故障时零人改变；仅第一个里程碑时继续保持房间与占位，终态才全部释放。断线重试不重复结算。旧版进行中与新版新增私档字段并存不丢字段，未审查迁移不得发布。 |
