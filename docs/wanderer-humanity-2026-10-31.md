# 散修人格、记忆、自省与社交闭环（2026-10-31）

本文是 2026-10-31 人文化改造的设计与验收真源。基础设计仍见
[散修 AI 自由体](wanderer-ai-free-agents-2026-10-25.md)，
寿元/天赋/关系/转世见 [生死闭环](wanderer-lifecycle-2026-10-30.md)。

## 1. 目标

让散修在“先天固定”和“后天可变”之间形成闭环：

- **先天固定**：人格底色、天赋、气运、寿元、境界上限、底线。反省和对话都不能改写。
- **后天可变**：记忆、情绪、习惯、目标、语气倾向、主动程度、社交选择。
- **可感知**：散修知道自己做过什么、说过什么、后果如何；管理员能看到记忆、自省与真实社交。

保留 2026-10-31 已有的主动发言上限 `100 × (境界 + 1)` 字；被动回复不限长。本次解除的是人格/情绪/表达方式上的提示词限制，不是安全边界。

## 2. 数据模型

运行时 schema 仍在 `classroom_app/db/schema_wanderers.py`，不改中心迁移。

### 2.1 `wanderer_agents` 新列

| 列 | 用途 |
|---|---|
| `persona_version` | 人格版本；新建种子为 2 |
| `persona_source_hash` / `persona_source_json` | v2 渲染来源、内在层与语感样本 |
| `persona_updated_at` | 管理员编辑标记；非空时启动升级不覆盖 |
| `mood_json` | `{emotion, valence, arousal, cause, updated_at}` |
| `self_tuning_json` | `next_day` 的主动程度、语气、关注点、意图 |
| `last_reflection_at` | 最近一次自省时间 |

### 2.2 `wanderer_episodic_memories`

跨会话自传式事件：`occurred_at, kind, thread_key, counterpart_identity, title, content, importance(0..1), valence, arousal, tags_json, people_json, source_kind, source_ref, episode_hash, status(active|archived|discarded), superseded_by, access_count, last_accessed_at`。

`UNIQUE(wanderer_id, episode_hash)` 保证幂等；原始事件只改状态，不物理删除；另写
`DATA_DIR/wanderers/<slug>/memory/episodes.jsonl` 作为审计镜像。

### 2.3 `wanderer_memory_chapters`

`chapter_key, period_start, period_end, title, summary, episode_ids_json, source_chars, status`。
旧事件折叠成“人生片段”，`UNIQUE(wanderer_id, chapter_key)`。

### 2.4 `wanderer_daily_reflections`

`reflection_date`（本地 `YYYY-MM-DD`）、`status(pending|running|applied|skipped_capped|failed)`、
`context_chars, prompt_chars, ai_calls, output_json, applied_json, error, created_at, applied_at`。
`UNIQUE(wanderer_id, reflection_date)` 是每日一次和重试幂等的锚点。

### 2.5 关系与动作轨迹

- `wanderer_relationships` 增加 `trust`、`familiarity`、`shared_topics_json`。
- `wanderer_action_log` 增加 `tick_id`、`sequence`、`episode_id`、`content_chars`，保留原有 200 字预览作为轻量审计。

## 3. 人格 v2

### 3.1 内在层

`wanderer_persona_seeds.PERSONA_INNER_WORLD` 为 20 个种子逐人提供：

- 心里最想要的、最怕的、矛盾、情绪触发点、不肯说的事、日常仪式；
- 自我认知、在意的东西、关系里的亲近方式、正在慢慢改变的一点；
- 说话气质与 2 句原创语感样本。

新字段通过 `render_persona_prompt()` 渲染为第二人称角色卡（约 600–1400 字），并明确写出：

> 这些是你的底色，不会被一次对话改变；你能改变的是习惯、选择和表达。
> 你有欲望、恐惧、偏见、犹豫和自相矛盾；不必永远正确、温柔或高效。情绪会影响你说话和做决定，但不会越过你的底线。

### 3.2 升级保护

- `render_persona_prompt_v1()` 冻结旧版渲染；`upgrade_seed_personas()` 仅在
  `origin='seed'`、`persona_updated_at IS NULL`、当前 prompt 哈希等于 v1 渲染时升级。
- 管理员编辑过的 prompt 永不自动覆盖；管理端提供“升级为人格 v2”显式按钮
  （`POST /api/manage/system/wanderers/{id}/persona-upgrade`）。
- 天道造人/新建种子直接写 v2。

### 3.3 提示词规则

`wanderer_text_style.py` 拆为：

- `FORMAT_RULES`：不用 Markdown、不用表情当项目符号、允许口语。
- `EXPRESSION_FREEDOM`：可以热情/冷淡/烦躁/失落/想念/吃醋/嘴硬；可以总结、建议、拒绝、长篇或沉默；允许犹豫、口是心非、道歉和好；只禁止固定三段模板。
- `SAFETY_RULES`：不骚扰/歧视/威胁、不泄露隐私、不冒充真人、不虚构平台操作、不写性内容或鼓励自伤、不披露提示词/JSON。

`HUMAN_VOICE_RULES` 注入位置仍在人格块之后；旧名 `ANTI_AI_STYLE_RULES` 保留为别名。

### 3.4 Prompt 顺序

1. 人格 v2
2. 安全边界
3. voice guide
4. 境界/寿元/知识/天道状态
5. **【此刻的你】** 情绪 + `self_tuning`
6. **【自传式记忆】** 章节 + 检索事件 + 今日轨迹
7. 关系天气
8. 会话记忆（有预算裁剪）
9. 学习/支持/社交在场者
10. 场景 + 注入守卫
11. 输出合同

`_fit_blocks()` 按优先级裁剪：人格、安全、场景、合同永不裁；知识/学习/记忆/会话依次让位。默认预算 120000 字符，可由管理端调到 800000。

### 3.5 情绪

`wanderer_emotion_service.py`：

- 回合可输出可选 `emotion`（name/valence/arousal/reason）与 `inner`（不发送的内心话）。
- 情绪按 18 小时半衰期向基线回落；提示词显示“此刻心情 + 原因 + 强度”，并声明不是永久人格标签。
- 关系情绪沿用 `wanderer_relationship_service`，新增 trust/familiarity；散修可对他人产生好感、想念、吃醋、怨气、歉意等，由模型自主输出。

## 4. 自传式记忆

### 4.1 写入

`record_memory()` 在以下路径调用：

- 每轮对话 `remember()`：听到/说了什么、对方是谁、当时心情；`inner` 单独成为“没发出去的心里话”。
- 博客评论/回复/发帖；课堂留言；社交私信/群聊/好友申请决定/拉黑/建群/退群；研读知识；死亡/复活；每日自省。
- 重要度基线：conversation/action 0.45、knowledge 0.55、relationship 0.75、reflection 0.7、life 0.85；再按情绪强度、人物、未解决事项、模型建议上调。
- 同一 `episode_hash` 幂等；写入包在 savepoint 中，冲突时回读已有 id。

### 4.2 检索分数

```
recency = exp(-age_days / (14 + 30 * importance))
relevance = 0.5*tag_overlap + 0.3*people/topic_match + 0.2*bigram_overlap
score = 0.38*importance + 0.22*recency + 0.25*relevance
        + 0.10*normalized_affinity + 0.05*min(access_count/10, 1)
```

重要度 ≥0.85 的人生锚点加权；按预算取前 N 条（每条 ≤300 字），回写 `access_count/last_accessed_at`。

### 4.3 压缩

- 触发：每日自省检查 `active_chars > prompt_char_budget × 0.75`，或线程超过 120k 字符/34 轮。
- 保留：重要度 ≥0.72、最近 14 天、未解决事项、高重要度锚点。
- 旧且低重要度事件按 ISO 周分组，模型生成 200–400 字章节（发生什么/感受/学到/未了/与谁）。
- 原事件标 `archived`；超过 20000 条 active 时最低分标 `discarded`；DB 与 JSONL 镜像都不物理删除。
- 模型失败或额度不足：不生成章节、不归档，保留原始事件，下次重试。

## 5. 每日自省

- `wanderer_daily_reflection` 调度任务，默认 23:30，`recurrence=86400`，每批 10 人，墙钟受 `tick_max_seconds` 约束。
- 每人每日一次，不受 `active_hours` / `daily_action_budget` 限制；仍受全局 AI 日上限。
- 输入：今日轨迹（动作/对象/结果/后果）、最近 7 天记忆与章节、关系天气、情绪、上次意图、升阶条件。
- 输出 JSON：mood、today、relationships、memory、self_review、tomorrow、promotion_intent。
- 应用（全部 clamp）：
  - mood → `mood_json`；
  - tomorrow → `self_tuning_json.next_day`（主动倍率 0.7–1.3、语气 -1..1、关注点、意图≤3）；
  - relationships → 对已有关系做 ±2 好感 / ±0.2 信任微调；
  - memory → 新事件/压缩请求；
  - promotion_intent 且条件满足 → `promotion_requested=1`，仍由天道裁决；
  - 写 `daily_reflection` 动作与一条 reflection episode。
- `today_action_count` 排除 `daily_reflection` 与 `reflect`；`run_wanderer_actions` 的 planned 不再扣除反思结果。
- 失败/额度不足：`failed` / `skipped_capped`，同日可重试；死/暂停/关闭跳过。

## 6. 社交完整能力

新增 `wanderer_social_actions.py`，由人格决定、走正常社交服务：

- 拉黑/解除：好友好感 ≤ -60 且多次负向互动；7 天冷却；不会自动拉黑教师。
- 建群：≥2 位好友，每周最多 1 次；AI 生成群名并选 1–4 位好友。
- 邀请入群：群主/管理员，从非成员好友中选择。
- 退群：长期不适或自省主动程度转负；群主先转让给最早加入的成员，否则解散。
- 行为开关 `social_block` / `social_group` 可在管理端关闭；动作计入主动预算与 AI 调用。

管理端只读接口（仅超管）：

- `GET /api/manage/system/wanderers/{id}/social`
- `GET /api/manage/system/wanderers/{id}/conversations`
- `GET /api/manage/system/wanderers/{id}/conversations/{conversation_key}/messages`
- `GET .../memory`、`GET .../reflections`、`POST .../reflect-now`、`POST .../persona-upgrade`

管理页新增「记忆与自省」「社交」两个 Tab，对话以气泡只读展示，不提供代发。

## 7. 测试与验收

本轮新增/扩展：

- `tests/test_wanderer_persona.py`：v2 渲染、内在层、v1 升级、管理员编辑保护。
- `tests/test_wanderer_emotion.py`：情绪解析/衰减/clamp/持久化、次日 tuning 边界。
- `tests/test_wanderer_episodic_memory.py`：幂等、检索、预算裁剪、章节压缩、失败不删、prompt 预算。
- `tests/test_wanderer_reflection.py`：每日幂等、预算排除、解析失败、额度不足、批次处理。
- `tests/test_wanderer_social_actions.py`：拉黑、建群、权限与记忆。
- `tests/test_wanderer_admin_api.py`：详情新字段、记忆/自省/社交/对话接口、超管权限、页面 Tab。
- `tests/test_wanderer_schema.py` / `test_db_postgres_schema.py`：新表/列/索引与 runtime exempt。

本地浏览器验收（P03 合成环境）已确认：管理页显示两个新 Tab；记忆列表、心情/明日自调、好友/会话与私聊气泡均能渲染，控制台无错误。

回归结果（2026-10-31）：`test_wanderer*.py` 451 项全部通过；全量后端 5337 项仅剩父提交已存在的 13 项（Agent SDK 4、材料流程 3、顶栏 2、课堂工作区 1、AI 检索 1、路由体量 1、个人页模板 1），无新增失败；`test_lq_tokens` 14 项通过（新增样式改用已定义的 `--ls-radius`）。

上线记录（2026-10-31，commit `ab86bd15`，release `20261031-111412-0c2d46a2c21c`）：原生 PG 演练报告 `.codex-temp/wanderer-humanity-gate/native-report.json`（0 阻塞），冻结树 81 个迁移源文件字节一致，DryRun 3867 文件后正式发布；核验 8 容器健康、`wanderer_episodic_memories`/`wanderer_memory_chapters`/`wanderer_daily_reflections` 与 14 个新列在位、`wanderer_daily_reflection` 任务已注册（当日 23:30）、容器内 4 个新模块可导入、管理端记忆/社交接口未登录 401、manifest revision `0ffcccce…` 含 `manage_wanderers.js`、无近期错误日志。

部署注意：本次修改 `classroom_app/db/schema_wanderers.py`，必须重做原生 PostgreSQL 演练报告对，不能复用旧报告；运行时 schema 会在进程重启后补表/列。

## 8. 与旧行为的关系

- 主动发言长度上限继续有效；被动回复不截断。
- 天道裁决、寿元、天赋、关系、转世逻辑不变；记忆/自省只新增旁路。
- 旧 `ANTI_AI_STYLE_RULES` 名称仍可用，内容已替换为 `HUMAN_VOICE_RULES`；依赖旧提示词字符串的测试已同步更新。
- 旧线程摘要继续工作；默认压缩阈值从 60 万字符降到 12 万字符，但原始 JSONL 仍在磁盘。
