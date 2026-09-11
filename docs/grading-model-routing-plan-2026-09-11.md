# 批改模型分级路由改进计划（2026-09-11）

> **实施状态（2026-09-11）**：阶段一～四已按本文落地（`ai_model_policy.py`、`ai_assistant.py`、`docker.env.example`、单测 `test_ai_execution_profiles`/`test_ai_execution_health`/`test_ai_multimodal_routing` 共 71 例通过）。用户确认的细节：未分类与破境试炼走 flash；非批改多模态不动；仲裁限额 40/10。与计划的差异：`_grading_adjudication_reasons` 里“证据冲突涉及高分题”的细化未做（保留“冲突非空即触发”）；`empty_text_with_attachments` 只在存在答题框且为空并带附件时触发，且不作为仲裁后的残留风险；env 的 `AI_*_PRIORITY` 顺序对批改/文本路由改为无效（厂商由业务档位决定）。本地端到端回放（`tools/grading_bench/replay_grading_pipeline.py`，4310/1591/1575）：flash 主评 76/85/78，风险信号触发豆包 pro 仲裁（1591 复核 83），无格式失败、无降级。阶段五（部署、线上 env、观察）记录见文末。

依据：`docs/ai-grading-model-benchmark-2026-09-11.md` 横评结论 + 用户决策：**期末考试、期中测验等重要场合用豆包 2.1 pro，其余所有批改场合用 DeepSeek 4.1 flash（`deepseek-flash`）**。

本文是对 [2026-09-07 多模态分层计划](ai-multimodal-business-routing-plan-2026-09-07.md)（已实施，确立了“视觉批改只走豆包”的现有架构）的增量修订：只改模型档位决策与配套护栏，不改分类语义、成绩链路、附件合同。

## 0. 目标路由矩阵

| 场景（服务端可信事实） | 现状 | 目标 | 思考/输出 |
|---|---|---|---|
| 批改，`assessment_kind ∈ {midterm, final}`，含图/PDF | 豆包 pro（`vision_assessment_high`） | **豆包 pro，不变** | thinking, effort high, 16k（按题量升到 32k） |
| 批改，`assessment_kind = homework`，含图/PDF | 豆包 pro（`vision_pro_low`, effort low, 8k） | **deepseek-flash**（新档 `vision_grading_flash`） | thinking, effort max, **32k** |
| 批改，`assessment_kind` 为空/`legacy_unknown`，含图 | 豆包 pro（视同重要） | **deepseek-flash**（待确认，见 §7-1） | 同上 |
| 批改，`source_feature = personal_stage`（破境试炼），含图 | 豆包 pro | **deepseek-flash**（待确认，见 §7-2） | 同上 |
| 批改，纯文本（无图无 PDF），任意 kind | deepseek-v4-pro（`text_deep`, effort max） | 期中/期末 → **豆包 pro**；其余 → **deepseek-flash** | 同上 |
| 仲裁 `multimodal_adjudication`（低置信/证据冲突时二次评分） | 豆包 pro，日限 10 / 班级 3 | **豆包 pro，不变**；限额上调，触发条件收紧 | 不变 |
| 出题/文档导入/OCR/看图聊天等非批改多模态 | 豆包 pro/lite | **不变**（不在本次范围，见 §7-3） | 不变 |

补充事实：DeepSeek 官方定价页注明 **2026-09-14 起 `deepseek-v4-pro` 请求将被路由到 V4.1 Flash**。纯文本深度批改即使不切换，三天后也会自动变成 flash；本计划把它显式化，避免账目和路由标签失真。

## 1. 现状要点（代码事实）

- 策略单一真源 `classroom_app/services/ai_model_policy.py::resolve_execution_plan`：多模态任务被硬编码只允许 `volcengine`（`provider_order_for_task` 的 allowed 集合、`AI_VISION_PRO_MODEL` 白名单校验会对任何非豆包模型抛 ValueError）。档位：`vision_edge_low`（lite）、`vision_pro_low`（作业）、`vision_assessment_high`（期中/期末/未知/破境/仲裁）。
- `ai_assistant.py::_build_model_routes` 额外过滤：多模态任务要求 `supports.images`，批改/仲裁要求 `supports.authoritative_grading`（deepseek 例外）。当前 deepseek 平台配置 `vision: None`、`can_force_json.vision = False`、无 `supports`，多模态任务模型为 None。
- 请求参数：deepseek 走 `_apply_openai_provider_options`（thinking enabled + `reasoning_effort` + `max_tokens` 取自 plan），豆包走 `_apply_volcengine_thinking`（`max_completion_tokens`）。消息构造 `build_vision_messages` 已有 openai 兼容分支，横评已验证 deepseek-flash 可正常收图并输出批改 JSON。
- 结果链路：`_validate_grading_result_for_job` 硬校验（题量覆盖、summary ≤120 字、evaluation ≤20 字、0 分必须写扣分点等），失败一次修复重试（`GRADING_RESULT_MAX_ATTEMPTS = 2`）；随后 `_review_grading_result_if_needed` 依据 `_grading_adjudication_reasons`（置信度 < 0.65、模型自报 needs_review、evidence_conflicts 非空、题量不全、分数和偏差 > 10、≥8 图无置信度、修复过格式、空答但有附件）决定是否调用豆包仲裁，受日限 10 / 班级 3 约束。
- 费用估算 `_estimate_provider_cost_cny`：deepseek-flash 记为 3/9 元/M（官方 2/8，缓存命中 0.04），需修正。
- 横评暴露的 flash 弱点：① effort max + 16k 输出上限在 18 张图时思考耗尽、正文为空；② 约 20% 调用触发格式校验（summary 超长、缺 evaluation/summary）；③ 对“截图齐但答题框空”的上机题偏宽松；④ 自报 needs_review 的比例高（图多的提交几乎都报），直接接入现有仲裁会把日限打满。
- DeepSeek 视觉硬限制（官方 vision 指南）：单请求 ≤600 张图，单图 ≤32 MiB，请求体 ≤48 MiB（内联 base64），单边 ≤8192 px，**≥15 张图时单边 ≤4096 px**，图片只能出现在 user 消息，每图最多 1024 token。平台现有 `AI_GRADING_MAX_FILE_COUNT = 50`、图片 ≤10 MB、PDF ≤20 页均在限制内；需补“≥15 图时压到 4096 px”。

## 2. 阶段一：策略层（`ai_model_policy.py`）

1. 新增视觉允许集合 `DEEPSEEK_VISION_MODELS = {"deepseek-flash"}`。
2. 新档位 `vision_grading_flash`：provider `deepseek`、model `deepseek-flash`、capability `vision`、thinking enabled、effort `max`、`max_output_tokens_total = 32768`、`quality_floor = "grading"`、`allowed_fallbacks = ("volcengine",)`。
3. `resolve_execution_plan` 多模态分支重写选档规则（只看可信业务事实，不看标题或提示词）：
   - `edge` → `vision_edge_low`（不变）。
   - `operation == adjudication` → `vision_assessment_high`（豆包 pro，不变）。
   - `operation == grading` 且 `kind ∈ {midterm, final}` → `vision_assessment_high`。
   - `operation == grading` 其余（homework / None / legacy_unknown / personal_stage）→ `vision_grading_flash`。
   - `operation == generation`（出题）、`document` → 不变。
   - 新增环境开关 `AI_GRADING_STANDARD_PROVIDER`（默认 `deepseek`；设为 `volcengine` 即一键回到旧行为，不需要重新部署）与 `AI_GRADING_STANDARD_MODEL`（默认 `deepseek-flash`，只允许白名单值）。
4. 纯文本分支：`operation == grading` 且 kind 期中/期末 → 新档 `text_assessment_high`（provider volcengine、model 豆包 pro、capability thinking、16k）；其余深度文本批改 → `deepseek-flash`。`DEEPSEEK_MODEL_DEEP_TEXT` 默认值改为 flash，pro 仍在白名单可手动指回；非批改深度文本任务（博客、教案等）沿用该默认，理由是 9/14 后 pro 即并入 flash。
5. `provider_order_for_task`：`multimodal_grading` 路由组的 allowed 集合改为 `{volcengine, deepseek}`；`text_deep` 允许 `{deepseek, volcengine}`。默认顺序不变，实际由 plan.provider 决定，不引入隐式溢出。
6. 执行快照兼容：`resolve_execution_plan(..., execution_snapshot=旧计划)` 现在对 `profile_id/provider/model` 不一致直接抛错。改为：若快照的 `profile_id` 属于已知历史档位集合（`vision_pro_low`、`vision_assessment_high`、`text_deep`）且 policy_version 相同，则**信任快照**继续用原厂商跑完；仅未知档位报错。这样部署瞬间正在跑或排队中的作业不会失败。`AI_EXECUTION_POLICY_VERSION` 保持 `business-routing-2026-09-v2` 不变（`AIBusinessContext.from_mapping` 会拒绝其他版本，改版本会让已入队 payload 全部报错）。
7. 输出档位 `size_structured_execution_plan`：flash 档基线已是 32k，medium/large 题量不再抬高；`AI_PROFILE_VISION_GRADING_FLASH_MAX_OUTPUT_TOKENS` 按现有命名约定自动生效。

## 3. 阶段二：AI 服务层（`ai_assistant.py`）

1. `PLATFORMS_CONFIG["deepseek"]`：`models.vision = deepseek-flash`；`task_models` 补 `multimodal_grading`（`deep_multimodal`/`document_multimodal` 仅注册不路由）；`can_force_json.vision = True`；新增 `supports = {images: True, native_pdf: False, structured_json: True, authoritative_grading: True}`。
2. `_estimate_provider_cost_cny` deepseek 分支：flash 基价改为输入 2.0 / 缓存 0.04 / 输出 8.0（高峰），pro 9.0 / 0.30 / 27.0，非高峰 ×0.5 逻辑保留；`price_version = "deepseek-2026-09-11"`。补单测。
3. 图片预处理：图片转 base64 处增加“当请求图片数 ≥15 时把单边 >4096 px 的图缩到 4096”；估算请求体超过 40 MiB 时按现有压缩路径再压一档；仍超限抛 `AIGradingEvidenceError` 走人工，不静默截断。
4. 有界降级：`_call_ai_platform` 候选路由目前只含 `plan.provider`。为 `vision_grading_flash` 增加一条规则：当 deepseek 返回 4xx 图片/请求体类错误、或返回空正文且 `finish_reason == length`（思考耗尽）时，按 `plan.allowed_fallbacks` 用豆包 pro 重跑一次，并在 usage 日志 `extra.fallback_from = deepseek` 标记；其他错误维持现有厂商内重试，不跨厂商。降级次数计入 `AIExecutionBudget`（现有上限 3 次尝试 / 2 次计费），不会失控。
5. 格式软规范化（压低 flash 20% 的格式重试率）：在 `_validate_grading_result_for_job` 之前加 `_soften_grading_result_format`：summary 超过 120 字按句号/分号截断到 ≤120；evaluation 超 20 字截到 20；deduction_points 超 80 字截断；evaluation 缺失且该题满分时补“达标”。分数、题量、0 分扣分点等**实质校验保持硬失败**。规则对所有模型生效，属于显示层修剪。
6. 空正文防线：`_do_provider_call` 收到 `finish_reason == length` 且正文为空时抛可识别异常 `AIProviderOutputExhausted`，供第 4 点降级判断；usage 日志 status 记 `error`。

## 4. 阶段三：仲裁（豆包 pro 兜底）与上机题宽松问题

1. 仲裁模型保持豆包 pro。primary 为 flash 档时形成“flash → pro”两级；期中/期末 primary 已是 pro，仲裁维持现有同模型二次评分。
2. 触发条件收紧，避免日限被 flash 的 needs_review 打满：`model_requested_review` 单独出现不再触发仲裁，只标记 `needs_review` 交教师；触发仲裁改为 ①置信度 < 0.65 且含图 ≥1，②`evidence_conflicts` 非空且涉及得分 ≥ 满分 80% 的题，③分数和偏差 > 10，④题量不全修复后仍缺，⑤答题框全空但有附件（新原因 `empty_text_with_attachments`）。
3. 限额从 10/3 调到 40/10（`docker.env.example` + 代码默认值同步）。按横评费用一次仲裁约 0.4–0.8 元，日 40 次上限约 30 元/天，是可接受的最坏情况。
4. 上机题宽松问题用提示词硬规则而不是换模型：批改系统提示新增一条“上机操作题答题框为空、仅有截图时，文字记录与分析部分不得给分，只能给截图证据分，并在 deduction_points 写明‘答题框未填写’”。
5. 健康自检 `_public_execution_policy_health` 的 cases 增加 `vision_homework → deepseek/vision_grading_flash`、`text_final → volcengine`、`vision_legacy_unknown → deepseek`，`ok` 判定包含新档位可用。

## 5. 阶段四：测试、配置、文档

- 单测（先改期望再改实现）：`tests/test_ai_execution_profiles.py`（档位表：homework/None/personal_stage → deepseek flash 32k max；midterm/final → 豆包 pro；文本期末 → 豆包 pro；快照兼容；env 回退开关）、`tests/test_ai_execution_health.py`（新 cases）、`tests/test_ai_multimodal_routing.py`（deepseek 多模态路由可用、无 supports 时不可用、降级仅在 allowed_fallbacks 内）、`tests/test_ai_grading_service.py`（补 assessment_kind 透传断言）、新增 `tests/test_ai_grading_result_softening.py`（软规范化规则）、`_estimate_provider_cost_cny` 新价表断言。
- 配置：`docker.env.example` 新增 `AI_GRADING_STANDARD_PROVIDER`、`AI_GRADING_STANDARD_MODEL`、`AI_PROFILE_VISION_GRADING_FLASH_MAX_OUTPUT_TOKENS=32768`、仲裁限额新默认；`DEEPSEEK_MODEL_DEEP_TEXT=deepseek-flash`。生产 `docker.env` 需手动改：`DEEPSEEK_MODEL_DEEP_TEXT`、`DEEPSEEK_MODEL_THINKING` 改 flash，`DEEPSEEK_MAX_CONCURRENT_REQUESTS` 4 → 8（flash 官方并发充足，2c/4G 主机才是瓶颈，故不再高），`AI_GRADING_ADJUDICATION_*` 新限额。部署脚本排除 docker.env，这些必须 SSH 手改。
- 文档/记忆：更新横评报告末尾“已实施”状态；记忆 `ai-scheduling-architecture` 追加分级路由段；`grading-model-benchmark` 标记已实施。

## 6. 阶段五：部署与验证

1. 部署前：查 `ai_jobs` 是否有 `queued/running/leased` 的 `ai_grading`（有则等其跑完，避免快照兼容路径首次上线就承压）。
2. 按 `deploy-workflow` 记忆的标准命令部署；随后 SSH 改 `docker.env` 并 `docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d ai app`。
3. 验证：① `/api/ai/health` 的 execution policy profiles 全部 `available`，homework 档显示 deepseek；② 用横评脚本 `tools/grading_bench/grading_model_bench.py` 对 4310/1591 再跑一次 flash 档，确认 32k 与软规范化后无校验失败；③ 在测试班级或征得教师同意的一份作业上 `force_submit_submission_for_ai_grading`，观察 `ai_usage.jsonl` 出现 `model=deepseek-flash, task_type=multimodal_grading`，耗时 < 90 s，`cost_estimate` 用新价表；④ 期中/期末类型的提交仍显示豆包 pro。
4. 观察 3 天：统计 `ai_usage.jsonl` 中 grading 的模型分布、平均耗时、格式重试比例、仲裁次数、降级次数；若格式重试 > 15% 或降级 > 5%，回到 §3-5 加规则。
5. 回滚：`AI_GRADING_STANDARD_PROVIDER=volcengine` + 重启 ai 容器即恢复全部豆包 pro，无需回退代码。

## 7. 需要用户确认的决策

1. **未分类作业（`assessment_kind` 为空 / `legacy_unknown`）默认走 flash。** 现状是视同重要走豆包 pro。选 flash 符合“其他所有场合”，但如果教师忘了把某次期末标成 `final`，就会用 flash 批。管理端已有分类入口（`assessment_classification_service`），建议在发起批改时若 kind 为空给教师一条提示。若你更保守，可改为“未分类走豆包 pro”，只需改 §2-3 一条规则。
2. **破境试炼（personal_stage）走 flash。** 它是学生个人阶段考核，通过线 80 分；横评中 1295 这类纯文本 flash 表现良好。若你认为它属于“重要场合”，改回 pro 同样只是一条规则。
3. **非批改多模态任务（出题、教案/评学表导入、简历解析、看图聊天、公文验证码 OCR）本次不动。** 如果“其他所有场合”包含这些，我会在阶段一多加一个 `document_flash` 档并做一次小样本回放验证再切。
4. 仲裁限额 40/10 与触发条件收紧是否接受。

## 8. 工作量与顺序

阶段一+二约 6–8 个文件、新增/修改约 25 个单测；阶段三 2 个文件；阶段四五各半天。顺序：策略层单测 → 策略实现 → AI 服务层 → 仲裁 → 本地回放验证（复用 `/tmp/gbench` 数据）→ 部署 → 观察。全程不改数据库结构，无迁移。

## 9. 阶段五实施记录（2026-09-11 14:20–14:35）

- 提交 `269fee7e feat(ai): tier grading models by assessment kind`，已推送 `origin/dev`。
- 线上 `docker.env`（备份 `docker.env.bak-20260911`）：`DEEPSEEK_MODEL_THINKING/DEEP_TEXT=deepseek-flash`、`DEEPSEEK_MAX_CONCURRENT_REQUESTS=8`、仲裁限额 40/10、新增 `AI_GRADING_STANDARD_PROVIDER=deepseek`、`AI_GRADING_STANDARD_MODEL=deepseek-flash`、`AI_TEXT_ASSESSMENT_PROVIDER=volcengine`、`AI_PROFILE_VISION_GRADING_FLASH_MAX_OUTPUT_TOKENS=32768`。
- 部署方式：从冻结的 LF 工作树 `.codex-temp/grading-tier-release/frozen-269fee7e`（`git -c core.autocrlf=false worktree add`）运行 `deploy_remote.ps1 -QuiesceForMigration -MigrationReport <dsh-release/final-native-7131c35f/native-report.json> -MigrationBackup <db-inputs/agent-dsh-20260910-095830.dump>`。原因：① 原生 PG 门禁按 64 个 DB 源码文件的 SHA-256 绑定旧报告，CRLF 工作树会全部不匹配；本次未改任何 DB 源码，旧报告在 LF 检出下校验通过；② DSH 集成钩子 `deployment/dsh/deploy_integration.sh` 要求每次部署都走停写迁移流程。停服约 4 分钟，`DEPLOY_DONE`，release `20260911-142034-b00fdd8ca441`。
- 健康检查：`/api/ai/health` 全部档位 `available`，`vision_homework/legacy_unknown/personal_stage → deepseek-flash 32k(+volcengine 降级)`，`vision_midterm/final、text_assessment → 豆包 pro`，`review_quota 40/10`，`provider_max_concurrent deepseek=8`。
- 真实验证：`force_submit_submission_for_ai_grading(4310)` → 主评 deepseek-flash 22.3 s、¥0.039（新价表 `deepseek-2026-09-11`）；触发 `empty_text_with_attachments` 仲裁，豆包 pro 184 s、¥0.36；终分 76 与部署前一致，回调成功。
- 观察项（3 天）：`ai_usage.jsonl` 中 `grading:*` 的模型分布/耗时/格式重试；`:adjudication` 次数与费用。**注意**：答题框留空只传截图的学生很多时，仲裁费用（≈0.36 元/次）会高于 flash 主评本身，日限 40/10 会兜底；若某班级频繁触顶，考虑把 `empty_text_with_attachments` 改为只标记 needs_review 不仲裁。
- 回滚：`AI_GRADING_STANDARD_PROVIDER=volcengine` + `AI_TEXT_ASSESSMENT_PROVIDER=deepseek` 后 `docker compose ... up -d ai`。
