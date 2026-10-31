# 散修自主性、平台手册与好友申请语义（2026-10-31 round 2）

承接 `docs/wanderer-humanity-2026-10-31.md`。本轮解决一个真实误判：某散修把同一条待处理好友申请当成了对方每天重复申请，后台出现“第五次、第六次、第七次”的每小时决定。

## 1. 根因

- 好友申请在 `social_friendships` 中只有一行，状态为 `pending` 时会一直保留；申请人再次点击申请，`send_request` 只返回这条待处理申请，不会新建，也不能重新发送（`social_friend_service.send_request`）。
- 散修端的决定任务在 `defer` 后每小时重排一次（`wanderer_social_friends._defer`），`recover_pending_requests` 也会在任务丢失时补排。
- 决定场景只给附言和对方资料，没有给“这条申请的发起时间、编号、你已经处理过几次”；线程记忆每次追加“收到好友申请”，模型于是把重复查看理解成重复申请。

## 2. 好友申请的新语义

- 决定场景新增：申请编号、发起时间（本地时间）、已等待时长、此前处理次数、上次决定；标题明确“同一条，等待中”。
- 场景/记忆/动作明细都写明“对方没有重复发送，也不能再重复发送这条申请”。
- 决定合同扩展为 `accept | decline | ignore | defer`：
  - `decline`：调用 `decline_request`，申请人 7 天后才可再申请。
  - `ignore`：申请保持 `pending`，重新安排 7 天后再看一次；`recover_pending_requests` 在 7 天内不再补排同一条申请。
  - `defer`：保留原来的“稍后再看”语义（默认 1 小时后或下一个偏好上线时段）。
  - `accept`：成为好友。
- 动作明细记录 `same_request=true`、`request_created_at`、`request_age_hours`、`seen_count`、`last_decision`，管理端行为记录可直接核对。
- 新增测试覆盖：忽略后仍是同一条 pending、7 天后才重看、第二次查看提示“第 2 次看到”、旧任务删除也不会当天补排。

## 3. 平台操作手册

`classroom_app/services/wanderer_platform_manual.py` 是静态手册，主题包括：

| key | 内容 |
| --- | --- |
| identity | 散修/真实学生/老师、先天与后天 |
| time | 本地时间、日界、行动记录 |
| friend_requests | 同一条申请、接受/拒绝/忽略、7 天与 24 小时冷却、每日/好友上限 |
| private_messages | 私信、回/不回、附件与撤回 |
| groups | 建群、邀请、退群、群主转让 |
| blog | 发帖、评论、点赞、审核 |
| classroom | 留言、投票、分组、作业 |
| knowledge | 研读、知识容量、升阶 |
| dao | 天道审核、警告、惩罚、抗辩、寿元 |
| limits | 主动/被动发言长度、动作预算、AI 日上限 |
| profile | 公开资料、头像、隐私、拉黑 |
| materials | 学习文档、材料检索、页面权限 |
| notifications | 消息中心、聚合、未读、裁决摘要 |
| manual | 手册自身 |

两种使用方式：

1. **自动注入**：`build_system_prompt` 根据场景（好友申请、私聊、群聊、博客、课堂、材料、天道等）注入相关条目，好友申请场景必然带 `friend_requests`。
2. **自主查阅**：新增 `read_manual` 行为（默认开启、每天最多 2 次）。模型从目录里挑主题或跳过；查阅会写一条 `read_manual` 动作（位置“平台手册 · 主题”）和一条 `knowledge` 记忆，后续能回忆起自己查过什么。

## 4. 主动社交与聊天

- 新增 `social_seek` 行为：平台给出推荐人（公开资料、共同课堂、互动过的人、其他散修），散修自己挑一个发好友申请，或觉得没意思就跳过；每周主动申请上限仍为 2 条，与 `maybe_befriend` 共用。
- 接受/拒绝/忽略如 §2；聊天仍由 `social_outreach` 自主决定是否开口，收到的消息可以 `skip`。
- 主动好友申请、寻友、拉黑、建群等动作都写位置与记忆。

## 5. 时间、地点与历史

- `wanderer_action_log` 与 `wanderer_episodic_memories` 新增 `location`。
- `log_wanderer_action` 支持显式位置；未传入时按动作类型给默认位置（博客/课堂/知识库/私信/社交/天道台/平台手册等）。
- 关键路径显式位置：好友申请与寻友（对方名字）、平台手册（主题）、博客（帖子编号）、课堂（课程名/编号）、知识库（材料名）、天道台。
- `today_trace` 与 `memory_block` 带 `时间｜地点`，管理端行为记录也显示位置，散修自己能看到同样的轨迹。

## 6. 表达自由与天道一句话

- `wanderer_text_style.SAFETY_RULES` 改为“对真实用户的谨慎 + 其余交给天道”：允许尖锐、冷淡、冲动、开玩笑、拒绝、试探、说错话；仅保留真实用户隐私/冒充、虚构已完成的现实操作、未成年人性内容、自伤鼓励等底线。
- 天道裁决仍通过 `notify_wanderer` 发通知，正文改为 `_one_line` 一句话（≤80 字）；同时写一条 `dao_verdict` 行为（位置“天道台”）和一条 `life` 记忆，散修在今天的轨迹里能看到自己受了什么裁决。
- 消息中心的同一条申请通知仍按 24 小时聚合，不会因为重复查看而刷屏。

## 7. 数据与兼容

- 新列：`wanderer_action_log.location`、`wanderer_episodic_memories.location`；走运行时 schema 的 `ADDED_COLUMNS`，进程重启自动补齐。
- 新行为开关：`social_seek`、`manual_read`（默认 1）；旧散修行读取时自动合并默认值。
- 新动作类型：`read_manual`、`social_seek`、`dao_verdict`；管理端标签已补齐。
- 未新增 HTTP 路由，路由快照不变。

## 8. 测试

- `tests/test_wanderer_manual.py`：手册主题、场景检索、提示词注入、查阅动作/记忆/每日上限/跳过。
- `tests/test_wanderer_social.py`：忽略后同一条 pending、7 天重看、第二次查看的“同一条/第 2 次”提示、恢复不重排。
- `tests/test_wanderer_social_actions.py`：主动寻友发申请、位置、每周上限。
- `tests/test_wanderer_dao_trace.py`：一句话通知、`dao_verdict` 动作与位置、`life` 记忆。
- `tests/test_wanderer_schema.py`：两个新列。
- 全量散修套件与 `test_heavenly_dao*`、`test_db_postgres_schema` 回归。

部署注意：改了 `classroom_app/db/schema_wanderers.py`，必须重做原生 PostgreSQL 演练报告对，不能复用旧报告。
