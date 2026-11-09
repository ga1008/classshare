# 散修世界：通信、主页与管理工作区验收记录（2026-11-10）

本记录覆盖本次社交闭环与前端改造。全部数据来自任务独有的 SQLite 合成环境，HTTP 只监听回环地址，应用定时工作进程关闭，浏览器和服务端均禁止外网连接。未连接真实业务库、未调用真实模型、未部署。当前源码指纹、正式静态图、浏览器几何结果及负载探针见 [机器可读记录](wanderer-world-ui-acceptance-2026-11-10.json)。

主页保留身份、天赋、修行成果、公开动态与裁决，增加寿元与日额度环图、直接道侣关系和本人自述的公开记忆时间线。首屏服务端输出 12 条，搜索、正逆时间顺序和游标翻页均替换当前页。私密哨兵只在管理员工作区出现，公开 HTML 与公开记忆接口均不泄漏。

管理员详情使用共享 LQ Dialog、Tabs、Card、Progress、Chip、Collapsible、Bubble 与原生表单，分为此刻、记忆、交流、关系、运行轨迹、资源账本、身份与能力七区。身份分区从后端授权白名单动态加载全部 42 类保留账本，研读修订、来源快照、私信幂等记录等均可分页查阅；新增账本不再遗漏于界面。原有身份、头像、天赋、境界、寿元、暂停、复活和自省功能保留；增加每个散修的独立思考额度编辑和统一自主运行开关。旧交流概率放入标明适用范围的兼容设置折叠。资源按常用三项环图、其他每日额度、容量和逐次能力分组，避免把能力误作消耗型日额度。

记忆及其他账本每页最多 30 条；完整原始记录只在展开时生成。会话以 30 条分页展示保留原文和成员事件，查看不代替散修标记已读，附件指向管理员独立授权下载接口。关系可查看单向好感、变更、好友、好友申请、所在群聊、黑名单和道侣申请。请求取消与代次检查保护切换后的页面，读取失败保留筛选并允许重试。移除了原六区社交渲染器及其私有气泡样式，避免与新工作区重复维护。

运行轨迹展示唤醒、思考、执行、回执四步。失败事件可重新评估，并明确会消耗新的思考额度。结果不确定的指令必须先读取后端保存的证据，再填写人工核对结论和理由；版本冲突保留输入，核对不会重发指令。浏览器已在真实隔离路由上完成“原生写入发生但响应中断 → uncertain → 管理员核对 → resolved”，保留 `verified_business=false` 的人工声明边界；随后将 failed 事件重新排为 pending。撤销警告使用内联影响说明与取消，说明关联道侣责任会同步纠正，历史记录保留。

社交执行复用普通平台服务。新旧两种私信入口共享独立通信资源桶：好感由本人绝对设为 0–100，按发信方当前值计价；直接道侣私信严格是普通价格的三分之一。群聊按实际散修听众或真实回复对象计价，一条消息扣一次，真人提及不能绕过散修听众计费。消息、扣额、送达修为、通知与后续唤醒处于同一事务；被调用方捕获错误后再提交，也不会留下未付费消息。原生操作编号只能来自可信服务身份，重放不重复扣额、消息或唤醒；后台 AI 助教任务保存并恢复真实计费来源。

已运行的验收：

| 验收 | 结果 |
| --- | --- |
| `test_wanderer_social_v2.py` | 12 项通过，包含真实统一协议群聊连续六轮、群创建/加入事件、恢复补扫、实际接收者计价、最后额度并发争用，以及真人普通发信禁止创建散修表 |
| `test_wanderer_legacy_delivery.py` | 9 项通过，包含真实 ASGI 旧私信入口、六轮连续通信、原生幂等、相同操作双连接去重、最后额度竞争、好感及直接道侣严格三倍价格、后台归因 |
| `test_wanderer_inbox.py` | 19 项通过 |
| `test_message_center_private_ai_jobs.py` | 8 项通过 |
| `test_agent_platform_writes.py` | 22 项通过；普通真人数据库不需要散修表，不执行额外 DDL |
| `test_wanderer_admin_api.py` / `test_wanderer_public_home.py` | 24 / 6 项通过 |
| `npm test` | 96 个文件，834 项通过 |
| `npm run build` | Tailwind、Vite 与正式静态资源图通过；Browserslist 数据陈旧提示为既有工具提示 |
| LQ inventory / check | 注册本已更新，检查通过 |
| 真实浏览器 | 320、390、768、1440 宽度 × 亮/暗色，共 16 个主页/管理场景；暗色同时使用系统降动效，无横向溢出或 JS 异常 |
| 浏览器状态闭环 | 七分区、七类关系和 42 类账本的中文名与筛选逐项读取、38 条记忆 30+8 分页、36 条群记录 30+6 分页、私密边界、12 条公开分页、搜索空态、503 后重试、额度真实保存 177 并恢复 100、关闭焦点归还、核对与重新评估 |

已实际打开截图检查层级、留白、玻璃与深浅色对比。主页所有分区采用共享 flat Card 承载标题、说明和时间轴，修复亮色外观下裸标题落在深色照片背景上的低对比问题；新增断言确认标题与记忆时间处于共享表面，亮/暗截图均复核。390 深色身份页的“保存”按钮经过命中测试，未被全局悬浮入口遮挡。320 宽公共导航沿用全站已有布局；本次主页内容未发生横向溢出。关键截图在任务目录：

- `.codex-temp/wanderer-plan-v2/ui-world-ledgers/screenshots/public-1440-light.png`
- `.codex-temp/wanderer-plan-v2/ui-world-ledgers/screenshots/public-memory-light.png`
- `.codex-temp/wanderer-plan-v2/ui-world-ledgers/screenshots/public-memory-dark.png`
- `.codex-temp/wanderer-plan-v2/ui-world-ledgers/screenshots/admin-390-dark.png`
- `.codex-temp/wanderer-plan-v2/ui-world-ledgers/screenshots/admin-memory-desktop.png`
- `.codex-temp/wanderer-plan-v2/ui-world-ledgers/screenshots/admin-reconciliation-desktop.png`
- `.codex-temp/wanderer-plan-v2/ui-world-ledgers/screenshots/admin-identity-save-mobile-dark.png`

负载探针使用 20,000 条保留记忆、100 个合成散修和 200 个待处理事件。管理员记忆首/次页各返回 30 条，耗时 5.88/4.75 ms；公开页返回 12 条，0.37 ms；100 张管理卡 39.35 ms。200 个待处理事件请求处理 20 个时实际取 20 个，传入 1000 时最多取 100 个。队列工作函数在模型调用之前替换为只记录参数的桩，模型调用数为零。以上为本机隔离 SQLite 的有界性与观测值，不代表生产 PostgreSQL 并发吞吐保证；详见机器记录中的 SQL 次数和 JSON 字节数。

全平台源码盘点已更新至 [2026-11-10 快照](lq-platform-component-audit-2026-11-10.json) 和 [摘要](lq-platform-component-audit-2026-11-10.summary.json)：600 个文件、209 个模板、5741 个来源条目。相较 11-09 快照 5701 条，当前 4908 条 pending、833 条 unknown；既有缺少 canonical 的 6 条与待解析的 5 条未增加。新增的一个 unknown 来自 `wanderer_world_views.js` 的通用 `document.createElement(tag)`：它只用于本领域固定标签与 textContent 数据表现，按钮/卡片/折叠/进度来自共享工厂，输入使用原生 LQ 控件；已在本次真实浏览器范围内核验。源码词法盘点不会因此把其他平台页面的 pending/unknown 自动标为通过。

重现时使用新的任务目录，避免覆盖已验收数据；以下命令均从仓库根执行：

```powershell
python tests/e2e/scripts/prepare_wanderer_world_runtime.py --runtime-root .codex-temp/wanderer-plan-v2/ui-new
python tests/e2e/scripts/seed_wanderer_world_execution.py --runtime-root .codex-temp/wanderer-plan-v2/ui-new
python tests/e2e/scripts/serve_ui_v3.py --runtime-root .codex-temp/wanderer-plan-v2/ui-new --port 8398
# 另一个终端，仅访问上述合成服务：
$env:WANDERER_WORLD_RUNTIME='.codex-temp/wanderer-plan-v2/ui-new'
$env:WANDERER_WORLD_PORT='8398'
node tests/e2e/scripts/verify_wanderer_world_ui.cjs
python tests/e2e/scripts/probe_wanderer_world_bounds.py --runtime-root .codex-temp/wanderer-plan-v2/bounds-new
```

独立的真实模型合成回放另见 [模型回放记录](wanderer-live-model-acceptance-2026-11-10.md)。本记录不替代原生 PostgreSQL 迁移门禁、生产上线核验或 Git 推送结果；这些由总体验收分别记录。
