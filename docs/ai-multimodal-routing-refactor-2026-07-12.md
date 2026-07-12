# LanShare 多模态 AI 调度重构计划与实施说明（2026-07-12）

## 1. 目标与不变量

本轮改造的目标是在不降低学生作业评分和文档理解质量的前提下，将常规多模态成本从“全量豆包”调整为“千问主链路、豆包按风险兜底、GLM 非权威备用”，并让后续模型替换不再需要修改业务代码。

必须保持的不变量：

- `fast_text_response` 与 `deep_text_reasoning` 继续使用现有 DeepSeek 快速/深度模型；千问和 GLM 默认不进入文本候选池。
- 已有 API 请求字段和旧任务名继续兼容，旧调用不会因任务类型重命名而失败。
- 任一供应商失败不能覆盖已产生的用户内容、学生答案或教师分数。
- AI 结果必须通过结构校验；确定性客观题不得被模型自由改分。
- 高成本仲裁只在可解释的风险条件下触发，不能演变为双模型全量调用。
- API Key 不进入源码、日志、健康检查、报告和 Git。

## 2. 功能审计与模型分配

| 业务功能 | 新任务类型 | 主模型 | 回退顺序 | 选择依据 |
|---|---|---|---|---|
| 公文 OCR、验证码 | `vision_ocr` | Qwen 3.6 Flash | 豆包 Turbo → GLM Flash | 交互延迟优先，基础识别已接近同质化 |
| 课堂工作区图片问答、讨论区图片 | `vision_interactive` | Qwen 3.6 Flash | 豆包 Turbo → GLM Flash | P50 低、成本低；失败需首字前回退 |
| 教案导入 | `document_multimodal_understanding` | Qwen 3.7 Plus | 豆包 Pro → GLM-4.6V | 结构化文档需要深度视觉推理 |
| 考核计划导入 | 同上 | Qwen 3.7 Plus | 豆包 Pro → GLM-4.6V | 表格、页面布局和长上下文并存 |
| 教师评学表导入 | 同上 | Qwen 3.7 Plus | 豆包 Pro → GLM-4.6V | 忠实抽取优先，禁止补写 |
| 简历导入 | 同上 | Qwen 3.7 Plus | 豆包 Pro → GLM-4.6V | 多栏版式和时间字段需要深度理解 |
| 材料视觉兜底 | 同上 | Qwen 3.7 Plus | 豆包 Pro → GLM-4.6V | 仅在文本提取不足时触发 |
| 参考图片生成试卷 | `deep_multimodal_reasoning` | Qwen 3.7 Plus | 豆包 Pro → GLM-4.6V | 需要理解材料并完成复杂生成 |
| 学生多模态作业评分 | `multimodal_grading` | Qwen 3.7 Plus | 豆包 Pro | GLM 不进入权威评分链 |
| 低置信度/证据冲突仲裁 | `multimodal_adjudication` | 豆包 Pro | Qwen 3.7 Plus | 仅使用本轮独立复核最稳定模型仲裁 |
| 所有纯文本功能 | 原文本任务类型 | DeepSeek 原模型 | 原豆包文本溢出链 | 按用户要求不改变文本模型体系 |

## 3. 重构分层

### 3.1 纯策略层

`classroom_app/services/ai_model_policy.py` 负责：

- 规范任务类型、旧别名和能力等级。
- 将任务映射为 `text_fast`、`text_deep`、`multimodal_light`、`multimodal_deep`、`multimodal_grading`、`multimodal_adjudication` 六类路由组。
- 每个路由组拥有独立的环境变量优先级，不再用一个 `AI_PLATFORM_PRIORITY` 同时控制文本和视觉。
- 输出不含密钥和 Base URL 的安全策略快照，供健康检查与运维核对。

### 3.2 供应商能力注册

AI 服务为 DeepSeek、Qwen、Volcengine、Zhipu 和兼容保留的 SiliconFlow 注册：

- 每类任务的实际模型名。
- 是否支持图片、原生 PDF、结构化 JSON、权威评分。
- 并发上限、思考开关和价格层级。
- Qwen 使用 `enable_thinking`，GLM/豆包使用各自的 thinking 参数；轻量与深度任务不会误开同一思考模式。

### 3.3 统一消息与文档证据

作业评分不再因 PDF 强制走 Volcengine Responses：

- DOCX 等可提取文档统一抽取文本和嵌入图片。
- PDF 统一抽取文本，并渲染有限数量页面作为跨厂商图像证据。
- 同一份 `text + image_url` 消息可被 Qwen、豆包或 GLM 消费，回退时不重建业务输入。
- 原始上传大小和派生页面证据分开计算，避免 PDF 渲染后被错误计为重复上传而超过总大小限制。

### 3.4 确定性评分层

`classroom_app/services/deterministic_exam_grading.py` 将确定性部分从模型中剥离：

- 空答且无附件固定为 0 分。
- 单选题精确判分。
- 多选完全一致固定满分；部分正确仍交给评分规则处理。
- 数值/短文本完全一致固定满分；不等价答案仍由评分规则决定过程分。
- 模型逐题分数按题目上限裁剪，固定分覆盖模型分，最终总分按逐题结果重新计算。
- 确定性改分、模型总分与逐题和的差值进入内部质量审计，不进入学生可见反馈。

### 3.5 风险仲裁

以下任一条件触发豆包 Pro 仲裁：

- 模型置信度低于 0.65。
- 模型主动返回 `needs_review=true`。
- 检出跨文件或证据冲突。
- 模型总分与确定性重算结果相差超过 10 分。
- 结构修复后才得到合法结果。
- 8 张以上图片且模型没有返回置信度。

仲裁失败时保留已经通过确定性校验的主模型结果，不把作业置为失败。固定客观题分数在仲裁后再次覆盖，防止仲裁模型改写确定性事实。

### 3.6 并发、失败与费用

- Qwen、豆包、GLM 分别拥有独立并发槽；全局优先级队列仍保留交互、默认、后台三级。
- 非流式调用继续执行跨厂商回退。
- 流式调用只允许在“尚未产生任何思考或回答 Token”时切换供应商；一旦开始输出，禁止第二模型混写。
- Qwen/GLM 流式请求要求返回 usage；AI JSONL 用量日志新增按任务层级估算的人民币成本。
- 健康检查展示任务策略、启用供应商、实际模型与并发上限，但不展示密钥。

### 3.7 管理端成本闭环

- 既有 `/manage/system/ai-usage` 页面新增“供应商与模型实际用量”面板，按供应商和实际模型展示调用数、成功率、输入/输出 Token、平均耗时和人民币估算费用。
- 聚合只读取共享 `logs/ai_usage.jsonl` 的有界尾部，默认最多 8 MiB、10,000 条事件，避免日志增长后拖慢管理页面。
- 没有供应商 usage 的旧调用仍计入调用数，但明确显示“可计价次数”，不会把未知费用伪装成零成本。
- 日志缺失、坏行、过期记录或读取异常均安全降级为空面板，不影响课程预算和系统管理页。

## 4. 配置计划

```dotenv
# 纯文本保持 DeepSeek 优先
AI_PLATFORM_PRIORITY=deepseek,volcengine

# 多模态独立路由
AI_MULTIMODAL_LIGHT_PRIORITY=qwen,volcengine,zhipu
AI_MULTIMODAL_DEEP_PRIORITY=qwen,volcengine,zhipu
AI_MULTIMODAL_GRADING_PRIORITY=qwen,volcengine
AI_MULTIMODAL_ADJUDICATION_PRIORITY=volcengine,qwen

QIANWEN_MODEL_MULTIMODAL_LIGHT=qwen3.6-flash
QIANWEN_MODEL_MULTIMODAL_DEEP=qwen3.7-plus
ZHIPU_MODEL_MULTIMODAL_LIGHT=glm-4.6v-flash
ZHIPU_MODEL_MULTIMODAL_DEEP=glm-4.6v

AI_GRADING_ADJUDICATION_ENABLED=true
AI_GRADING_ADJUDICATION_CONFIDENCE_THRESHOLD=0.65
AI_GRADING_ADJUDICATION_SCORE_DELTA=10

# 管理端供应商用量聚合边界
AI_PROVIDER_USAGE_TAIL_BYTES=8388608
AI_PROVIDER_USAGE_MAX_EVENTS=10000
```

`docker.env.example` 只包含空 Key 和安全默认值；本机真实 `docker.env`、`.env` 继续受 Git 忽略规则保护。

## 5. 测试与上线门槛

### 5.1 自动化测试

- 任务别名与六类路由组。
- 文本候选池不出现 Qwen/GLM。
- 轻量、深度、评分、仲裁的模型顺序。
- Qwen/GLM 思考参数。
- 成本估算和 Token 字段。
- 供应商用量日志的过期过滤、坏行跳过、未知计价、尾部截断和管理页契约。
- 8 个实际业务调用点的任务类型契约。
- 单选、多选、数值、空答的确定性证据。
- 固定分覆盖、题目上限和总分重算。
- 多模态评分任务到回调的集成流程。
- 流式首字前跨厂商回退，且只产生一个 `done`。
- 现有 AI JSON、工具调用、作业队列、回调指纹与 stale-job 防护回归。

### 5.2 运行时门槛

- `/api/internal/health` 必须能看到 Qwen 轻/深模型、豆包兜底模型和正确顺序。
- 使用极小无敏感图片分别完成 Qwen Flash 与 Qwen Plus 的真实调用。
- 使用模拟厂商失败证明首字前回退；不通过真实故障消耗生产用户请求。
- 使用匿名化真实作业回放，验证新确定性评分不会破坏反馈格式。
- 不在本轮自动部署；部署前先执行现有部署 DryRun、备份和远程健康检查流程。

## 6. 成本预期与控制

基于本项目 2026-07-12 的 240 次实测：

- Qwen 3.6 Flash 主评分平均约 0.0107 元/份。
- Qwen 3.7 Plus 主评分平均约 0.0239 元/份。
- 豆包 Pro 主评分平均约 0.2253 元/份。

若 70% 请求使用 Qwen Flash、20% 使用 Qwen Plus、10% 触发豆包 Pro，估算比全量豆包 Pro 节省约 84.6%。确定性客观题与一次提取多次复用会继续减少模型推理负担，但正式节省比例必须以上线后的 provider usage 和实际账单为准。

## 7. 回滚边界

- 将 `AI_MULTIMODAL_*_PRIORITY` 调回 `volcengine,qwen` 即可在不改代码的情况下恢复豆包主链。
- 将 `AI_GRADING_ADJUDICATION_ENABLED=false` 可关闭双模型仲裁，确定性评分仍保留。
- 旧任务名仍被策略层接受，因此回滚业务调用点不要求数据库迁移。
- 本轮没有新增数据库列或不可逆迁移。

## 8. 本轮运行时验证结果

### 8.1 无敏感图片真实接口冒烟

使用本地生成的红/蓝方块小图，经新路由实际调用：

| 路由 | 实际模型 | 结果 | 延迟 | Token（入/出） | 估算费用 |
|---|---|---|---:|---:|---:|
| `vision_ocr` | Qwen 3.6 Flash | 正确识别 2 个色块，合法 JSON | 1.71s | 104 / 7 | ¥0.0001752 |
| `document_multimodal_understanding` | Qwen 3.7 Plus | 正确推理 1+1=2，合法 JSON | 4.54s | 112 / 99 | ¥0.0008128 |

这证明新代码中的 Base URL、模型名、Qwen thinking 参数、结构化输出、图片 Token 和费用估算均与真实接口兼容，而不只是单元测试 mock。

### 8.2 11 图复杂网络作业匿名回放

回放此前独立审计合理区间为 45–65 分的 S019：

1. Qwen 3.7 Plus 首轮报告 62 分并发现姓名、端口等冲突，但逐题分实际合计为 80 分。
2. 服务端确定性重算识别到 18 分总分不一致，同时检测到 `evidence_conflicts`，自动触发豆包 Pro 仲裁。
3. 豆包进一步识别出 S 值错误、截图显示 100% 丢包、NAT 抓包为空以及正文/截图互相矛盾，最终给出 52 分。
4. 52 分准确落入 45–65 的独立审计区间；最终反馈逐题列出了可核验扣分证据。

Qwen 主评分用时 60.1 秒，13,559 / 3,135 Token，估算 ¥0.0417584；整条含仲裁链路约 4 分 44 秒。这个样本证明高成本豆包确实只在复杂冲突中提供增量价值，而不是常规全量调用。

### 8.3 豆包 Token 记账

将后台非流式豆包调用调整为直接获取完整 completion 后，真实小请求成功回传 prompt、completion 和 reasoning Token，并生成费用估算；用户可见的流式接口仍保持流式，不受此调整影响。

### 8.4 自动化回归现状

- 新增路由、确定性评分、仲裁、回调提醒、流式回退和供应商成本面板测试全部通过。
- AI 核心、作业队列、回调指纹、用量预算、供应商聚合等 46 项针对性测试全部通过。
- 考核计划、教案、教师评学、材料导入、公文、简历非 Office 导出和健康契约等 185 项业务回归全部通过；本轮合计 231 项模块隔离测试通过。
- 更早的扩展运行中，唯一相关环境失败为本机既有 LibreOffice 简历 PDF 转换未产出文件，与 AI 改动无关；因此最终回归将该 Office 环境测试单独列为已知环境问题，而未将其伪装成通过。
- 全仓 904 项测试在同一进程内运行时出现大量既有测试隔离/数据库后端污染错误；其中 API 健康契约单独运行通过。该全仓命令不能作为本轮回归结论，最终验收以独立模块运行和本轮真实接口回放为准，并单独记录既有环境失败。
