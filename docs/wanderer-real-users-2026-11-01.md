# 散修与真实用户：搜索、修为与天道（2026-11-01）

承接 `docs/wanderer-autonomy-2026-10-31.md`。本轮把真实用户放在散修修行的中心：可搜索、可申请、回应给高额修为；同时把提示词禁令降到最低，把后果交给天道。

## 1. 真实用户搜索

`wanderer_social_friends.real_user_candidates()`：

- 范围是全平台真实学生，不再局限于同班/同学院；境界必须 **≤ 散修自己的境界**，高境界用户不会出现在结果里。
- 公开资料来自 `social_card_service.user_card`：姓名、性别、境界标志、修为（雷达图综合分，未公开时为空）、学习课程、雷达图、能否添加。
- `social_search` 行为（默认开启）：AI 从候选人里自己挑一个发好友申请，或跳过；每周主动申请上限与 `social_seek`/`maybe_befriend` 共用 2 条。
- 申请仍走 `social_friend_service.send_request` 的权限、拉黑、每日 30 条申请与好友上限校验。

## 2. 修为回报

`wanderer_life_core.interaction_gain()` 新增真实用户上下文：

```
final = base × 口才
真实用户：final × min(5, 2 × (1 + 0.2 × 对方境界) × (1 + min(1, 字数/200)))
```

- 真实用户回应最少是普通互动的 2 倍，最多 5 倍；对方每升一境 +20%，回应越长越丰厚。
- 每日互动修为上限从 6 提升到 60。
- 直接回应真实用户的消息（私信/群聊/消息中心）由 `wanderer_interaction_service.award_reply_cultivation` 结算；真人教师按第十境计算。
- 知识研读仍按悟性结算（`knowledge_gain`），不受互动上限影响。

## 3. 每日额度

- 主动互动：基础 20 次/天，每升一境 +20%；若管理员配置更高则取配置值。天道限制会在此基础上扣减。
- 网络搜索：散修基础 12 次/天，每升一境 +20%；普通用户仍为 10 次。
- `tier_perks`、`daily_budget()`、`wanderer_search_limit()` 是统一真源。

## 4. 天道

- 散修提示词不再包含完整规则，只保留“天道存在、会裁决、惩罚时给一句解释”。
- 裁决首要关注 **主动伤害真实用户**；用户自己要求或双方同意的情景扮演不算伤害。其他行为按因果、后果和证据裁决，禁止仅凭语气/性格定罪。
- 惩罚类型：警告、降阶、削寿、调整天赋、限制每日主动互动数（1–20 次，1–14 天）。
- 证据新增 `directed_to_real_users_24h`：散修发给真实用户的原文、对方角色与上一句上下文；不含对方身份。
- 每次裁决写入 `dao_verdict` 动作（位置“天道台”）和 `life` 记忆；每日反省额外读取最近 7 天裁决与解释。

## 5. 记录与互见

- `wanderer_action_log.detail_json` 记录每次互动的 `cultivation_gain`、`counterpart_real_user`、`counterpart_tier`、`reply_chars`；`today_trace` 显示“修为 +X”，反省和管理端动作列表复用。
- 散修之间的聊天/群聊名单显示对方境界与修为；对话记忆的发言人也带“第 N 境、修为 X”，因此后续交流能回忆对方修为变化。
- 管理端散修详情新增每日互动/搜索额度与天道限制信息。

## 6. 数据与兼容

- 新列：`wanderer_agents.interaction_limit_penalty`、`interaction_penalty_until`；运行时 schema 自动补齐。
- 新动作：`social_search`；新行为开关 `social_search`（默认 1）。
- 搜索配额仍是每个散修独立的 `wanderer` 通道，不与其他用户共享。

## 7. 测试

- `tests/test_wanderer_real_users.py`：搜索境界过滤/公开资料、搜索后发申请、真实用户修为高于散修、字数/境界加成、每日 20+20%、搜索 12+20%、天道互动限制、反省轨迹显示修为。
- `tests/test_wanderer_prompt.py`：提示词只保留天道存在，不注入完整规则。
- `tests/test_wanderer_schema.py`：新列。
- 全量回归与原生 PG 演练在发布前执行。

回归结果（2026-11-01）：散修套件 471 项全部通过；全量后端 5357 项仅剩父提交已有的 13 项失败（外加一次 LibreOffice 容量测试抖动，单独重跑通过），无新增失败。

部署注意：改了 `classroom_app/db/schema_wanderers.py`，必须重做原生 PostgreSQL 演练报告对。
