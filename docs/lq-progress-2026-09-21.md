# Liquid Glass 改造进度交接（2026-09-21）

> 用途：Codex 按 `liquid-glass-execution-plan-2026-09.md` 施工到一半中断（额度耗尽）。本文由 Claude 于 2026-09-21 对照 git 工作区、Codex 自述记录（`lq-acceptance.md`、`lq-s1~s4-preflight.md`、`lq-components.md`、`lq-migration-registry.json`）与实际运行的测试整理，供负责人决策与下一位实施者接手。**本文只陈述状态，不代表任何阶段已正式签字。**

## 0. 三件必须先知道的事

1. **全部工作未提交。** HEAD 仍是 `23fd77e0`（课表提交）；工作区有 100+ 个修改文件、236 个未跟踪文件（新目录 `static/css/lq/`、`static/js/lq/`、`templates/partials/lq_*`、`tests/lq/`、`tests/e2e/components/lq-*`、`tools/ui/*`、`docs/lq-*`）。**第一件事应是打一个检查点提交**（建议 `wip(lq): S0–S4 first package checkpoint`），否则任何误操作会丢掉几天的工作。请负责人决定是否由我来做。
2. **生产可见行为未变。** 所有新壳/新页只在服务端环境变量开启时渲染：`LANSHARE_LQ_PILOT`（九条精确路由的 S3 试点）与 `LANSHARE_LQ_FAMILIES`（S4 页族白名单：`manage-shell,navbar-shell,dashboard,calendar,profile,messages,centered,growth,manage-pages`）。两者默认关闭；查询参数、header、localStorage 都不能开启。**但**已经生效、与开关无关的改动有：静态资源内容哈希交付与 nginx 规则、开课向导 301 退役、批改页小数分数与版本令牌、14 个 shadcn 文件删除、`base_centered` 内联样式抽出、偏好 API 对教师开放、若干测试隔离修复。这些在部署时会直接上线。
3. **本地开发库发生过一次测试污染事故（2026-09-20 17:22）**，Codex 用未隔离方式跑了全量单测，真实连到本地 `127.0.0.1:5432/lanshare`：`scheduled_tasks` 表被清空（我今天只读核对：现仅 1 行 `unit_test_kind`，2026-09-20 09:25 UTC 写入），签章范围迁移 `20260908_signature_visibility_levels_v1` 被执行，另有 9 个模块对固定 fixture ID 做过写入与清理。Codex 已保全事故后 dump（`.codex-temp/lq-s1-test-isolation-incident/loopback-lanshare-incident-20260920-173459.dump`，5.13MB，SHA 见事故记录），未恢复、未连生产。它当时问过"本地库是否有独有业务数据或定时任务"，**未得到回答**。事故记录：`docs/lq-test-isolation-incident-2026-09-20.md`。此后所有 Python 测试改走 `python tools/test_backend.py`（导入前封 dotenv、独占 SQLite、拒绝 psycopg 真实连接）。

## 0.5 接手后已完成（2026-09-21，Claude）

| 项 | 内容 | 证据 |
|---|---|---|
| 401 会话恢复页崩溃 | `classroom_app/app.py:470` 的 `templates.TemplateResponse("session_expired.html", {...})` 在 Starlette 1.0 下把 dict 当作 context-as-first-arg，触发 `TypeError: cannot use 'tuple' as a dict key`。改为 `TemplateResponse(request, "session_expired.html", {...})` | `test_auth_session_recovery.py` 9/9 通过（原 13 errors 全清） |
| 路由快照基线 | 新增 6 条真实路由（`/manage/me/appearance`、`/student/login/identity` GET+POST、`/student/password/forgot` GET+POST、`/student/password/setup` POST）均为 C1/D2 已实现的票，逐条核对后重写 `tests/fixtures/p02_route_snapshot.json`（960 条） | `test_architecture_route_snapshot.py` 1/1 通过 |
| 考试草稿部署契约 | S3 已把 `/draft` 往返抽到 `static/js/exam_take/submit.js`，旧断言仍在模板里找 `` /draft`, { method: 'GET' ``。改为断言模板通过 `asset_url('js/exam_take/submit.js')` 引入，并在模块里断言 GET/POST | `test_deployment_browser_cache.py` 7/7 通过 |
| 消息附件合同 | C2 已恢复附件端口，`test_profile_template_contract.py` 仍断言"没有 file input"。改为断言恰好一个隐藏 `#message-center-file-input`（有 `aria-label`）+ 一个 `#message-center-attachment-preview` | 该文件 9/9 通过（4 组合全过） |
| lint 三项阻断 | `student_login.js`、`session_expired.html`、`student_login_v4.html` 在审阅指纹后又被 D 包改动。逐份 diff 复核（原生 POST action、就地失败反馈、lq 组件化、模块化初始化，无越权改动）后刷新 `docs/lq-migration-registry.json` 中的 sha256 | `lint_lq.py` blocking 为空 |
| 台账补盘 | 新增 `tools/ui/lq_registry_triage.py`，把 196 条"未盘点"推进到"已盘点"：由源码静态推导 roles/layout/assets/islands/controller（含 ESM 依赖闭包）/domHooks/apis/versionFields，`states`/`components` 留空待迁移时填，每条加注来源说明 | 台账现 196 已盘点 + 12 迁移中；备份 `.codex-temp/lq-migration-registry.pre-triage.json` |
| 并行施工基础设施 | `tools/ui/locked_build.py`（构建串行锁，输出 graph revision）、`.codex-temp/claude-s4-runbook.md`（七包所有权/端口/测试入口/禁止事项/报告格式） | — |

## 0.6 跨包事项（各包上报，主任务处理）

| 事项 | 来源 | 裁定 / 状态 |
|---|---|---|
| `lq_card(attrs=)` 丢弃 `data-lq-*` 键 | 成长页包 | **设计使然，不改**。`classroom_app/lq_content.py:59` 显式剔除，防止页面伪造组件内部属性（插槽/禁用态）。实测 `data-foo` 正常保留。页面需自有钩子时用非 `lq` 前缀属性名。唯一可改进处是"静默丢弃"改为报错，但多包正在编辑消费方，暂不动共享行为 |
| `lq_empty` 标题须关键字传参 | 成长页包 | **非缺陷**。`lq_props(component, **props)` 签名使然 |
| 分段控件 `aria-selected` 与既有 `aria-pressed` 冲突 | 首页包 | **已裁定**：两种语义按开关共存——关闭分支保持 `role=group`+`aria-pressed`（既有 7 处断言不动），开启分支用 `role=tablist/tab`+`aria-selected`+roving tabindex；`dashboard.js` 只保留一个状态机，按元素实际 `role` 决定写哪个属性 |
| `.cultivation-card__mark` 对比度不足 | 成长页包 | 待处理。位于 `ui-system.src.css`（管理页包正在编辑该文件），待其收口后由主任务修复 |
| 390px 下 `lq-btn--soft`/`--prominent` 疑似对比度 | 管理页包 | 待该包提交复现数据后处理 |
| AI 悬浮按钮命中区域、心情按钮对比度 | 个人资料包 | 待该包提交复现数据后处理 |
| emoji popover 未接入 `LQ.layer` | 消息中心包 | 已记录路径与调用点（`message_center.js:765-793`、`1860-1861`）；该文件为 `blog.js`/`feedback.js` 共用，属后续独立票 |

## 1. 今天实际跑过的验证（2026-09-21，当前工作区）

| 入口 | 结果 |
|---|---|
| `npm run typecheck` | 通过 |
| `npx vitest run` | 78 文件 / 668 测试全部通过（含 `tests/lq/*.test.mjs` 20 个文件） |
| `python tools/ui/lint_lq.py` | **3 项阻断**：`static/js/student_login.js`、`templates/session_expired.html`、`templates/student_login_v4.html` 触发 `reviewed-source-changed`（S4 D 包登录文件在审阅指纹后又改动，需复核后刷新指纹）；3,722 条旧代码警告（未迁页，预期） |
| `python tools/test_backend.py`（隔离全量后端） | 见文末附记 |
| `npm run build` / Playwright 全套 | 未跑（耗时长；Codex 最近一次完整记录见 §3） |

## 2. 各阶段状态（对照执行版 §16）

| 阶段 | Codex 自述 | 我核对到的证据 | 判定 |
|---|---|---|---|
| **S0 基线·决策·首胜** | 本地工程门禁收口 | 静态交付：`nginx.conf` 哈希路径直出 + `gzip_static`，`deployment_cache_service.py` 哈希 `immutable`/其余 `no-cache`；`tools/build_static_assets.mjs`、`tools/publish_static_assets.py`；`package.json` 新增 `build:assets`。向导退役：模板/JS/CSS 段/导航项已删，301 存在。shadcn：只剩 `dialog.tsx`。`data-mobile-collapse` 0 处。`base_centered` 无内联 `<style>`。批改页 `expected_review_revision` 已接入。legacy `blog.css` 段**未整段删除**（Codex 核实评论/表情样式仍有消费者，只删了 48 个无消费者选择器，计划 K13 措辞已被纠正）。台账 208 条：196"未盘点"、12"迁移中" | **本地完成，未签字** |
| **S1 令牌·状态色·材质·偏好** | 收口 | `static/css/lq/tokens.css`、`base.css`、`index.css`；`ui-system.src.css` 首行 `@import "./lq/index.css"`；`tailwind.config.js` `darkMode: selector`；`docs/lq-tokens.json`；`tools/ui/generate_lq_tones.py`、`export_tokens.py`；偏好：`get_current_preference_user`、`appearance/glass` 字段、`schema_user_ui_preferences.py`/`postgres_required_columns.py` 改动，`tests/test_user_ui_preferences*.py` 新增；三个文档根经 `partials/lq_theme_attrs.html`/`lq_theme_bootstrap.html` 输出主题属性。Codex 记录了对 §8.14 的纠偏（新增 `on-base`/`on-primary` 色对；sky 实际 205°；alpha 令牌类型分离） | **本地完成，未签字** |
| **S2 组件库与协调层** | 623 项组件浏览器 + 632 前端单元 + 112 隔离后端通过；入口 17,309/18,432 gzip | `static/css/lq/components/` 26 个文件、`static/js/lq/` 30 个模块（layer/toast/dialogs/forms/tables/selection/upload/status/composer/navigation/shells/insights/dirty-guard/schedule-bridge/preview…）；`templates/macros/lq/`；`tests/e2e/components/lq-*.spec.ts` 32 个；`/dev/lq` 预览路由（`templates/dev/lq_shell.html`）；`docs/lq-components.md` 233 行 | **本地完成，未签字** |
| **S3 端到端试点 + 测试补齐** | 本地工程出口通过（第九图 `de1603bb…`） | 九条试点路由（8 管理列表页 + `/report-card`）在 `classroom_app/lq_pilot.py`，默认关闭；台账 12 条"迁移中"；新增 `tests/e2e/components/submission-grading.spec.ts`、`manage-pilot-adapter.spec.ts`、`tests/e2e/specs` 下 99 个新文件（含 Codex 命名的业务 spec）；K9 试卷 `expected_revision` 票（`exam_papers.py` 改动 + `tests/lq/exam-editor-revision.test.mjs`）；`exam_take.html` submit 模块提取（`tests/frontend/exam_draft_version.test.cjs` +405 行改为 import）；性能 9 页两宽 p95 16–40ms、blur ≤2、无 >50ms 任务（i7 台式，非低端机） | **本地完成，未签字** |
| **S4 壳与常规页** | 第一包进行中 | 见 §3；**2026-09-21 七包全部推进并回报，证据见 [验收记录](lq-acceptance.md) 的「S4 续建」节** | **主体完成，三项遗留见下** |
| S5 教学核心链 | 未开始 | K9 票与 submit 模块提取已在 S3 做完；六模板未迁 | 未开始 |
| S6 复杂工作台 | 未开始 | `course_schedule_deck.js` 有 +84/-10 改动（`.d.ts` 补全 + `schedule-bridge.js`），`chat.js` Escape 焦点最小修复；其余未动 | 未开始 |
| S7 / S8 | 未开始 | — | 未开始 |

## 3. S4 明细：已完成 / 进行中 / 未开始

S4 按 `lq-s4-preflight.md` 分六包 A–F。

**已完成（本地，有测试证据）**
- **A 共享壳第一包**：`manage/layout.html` 与 `base_navbar.html` 接入 `lq_family_enabled('manage-shell'|'navbar-shell')`；新 partial `lq_navbar_topbar.html`、`lq_app_bottomnav.html`（Dock）、`lq_shell_prepaint.html`、`lq_ready_core.js`、`lq_theme_core.js`；CSS `static/css/lq/manage-shell.css`、`navbar-shell.css`、`pages/navbar.css`。真实 14 路由 × 双宽、20 轮原节点复挂、无 JS/模块失败 6/6；视觉第三图 5/5（六配色 × 明暗 × 320 窄屏）；管理 SSR 11、navbar SSR 11。
- **C0 Profile 原样提取**：`profile.html` 拆为学生/教师薄壳 + `templates/partials/profile/*`（含 `hero_lq.html`）；32 份新旧渲染对照一致、9 项隔离测试通过。
- **D 第一包登录原生失败恢复**：`base_centered.html` 薄壳 + `pages/centered.css`、`pages/login.css`、`pages/status.css`；`auth.py` +203 行（就地失败 SSR、safe-next、POST 缓存头）；登录真实模板 20/20；316 张场景背景 × 亮暗 × 双宽 1264 项实卡对比全部 ≥5.23（**学生登录 Clear 卡已按 K17 验证**）；真实认证 19+4 项。
- **D2 原生身份/设密/找回路径**：51 Python + 3 JS 用例通过；8 个真实浏览器用例已写、类型通过、**未运行**。
- **C1 路由票**：教师外观分区路由、双角色 canonical href、profile family 开关（隔离 7 + 9 + 16 项通过）。
- **C2 消息异步正确性**：`message_center.js` +597 行（请求 key + 代次、可见失败/重试、草稿按会话与 scope、发送快照、附件 5 端口、AI 轮询 lease）；组件 spec `message-center-{actions,ai-poll,drafts,races}.spec.ts` 共 29 + 17 项通过。
- 页族关闭回退演练 6/6（第三图）；20 轮节点/资源/请求无增长；CLS=0。

**进行中（有代码，未收口）**
- **A 共享壳性能门禁**：手机 390 学生壳 4× CPU 下仍有 >50ms 长任务（最新候选 p95 72/88ms，长任务 10/9 个，余项在"关闭弹层回焦"）。两个候选图与 CDP 归因在 `.codex-temp/lq-s4-shell-performance-attribution.md`。**未通过，不能进入 B/E/F。**
- **C1 外观分区前端**：`user_ui_preferences.js` +222 行、`profile.js` +94 行、`user-ui-preferences.test.ts` +157 行；43 + 16 + 4 项单元通过；**真实浏览器保存/失败/冲突与签名业务未验收**（报告 `.codex-temp/lq-s4-profile-c1-step2-report.md`）。
- **C2 消息呈现**：异步层已过，actions 与消息皮肤"仍在施工"。
- **D 剩余**：会话过期页无真实入口；人生一言 CSS 块（`ui-system.src.css` 50348–52152）未迁；D2 的 8 个浏览器用例未跑。lint 的 3 项阻断即此包文件。
- 迁移台账仍 196 条"未盘点"（Codex 只维护了 12 条试点条目）。

**未开始**
- B 双首页与日历（含 3D 课表宿主接线、CLS 0.067→≤0.05 基线修正）。
- E 成长页族。
- F 其余管理页（三宏调用点仍是旧 DOM；S3 试点只换了壳与宏合同，页内 dialogs/controller 未迁）。
- `app_bottomnav.html` 旧 fixed 层与 body 补白仍在（Dock 为并行新实现，开关切换）。

## 4. 与执行版计划的偏差（Codex 已记录，需负责人知悉）

| 计划条款 | 实际处理 | 影响 |
|---|---|---|
| K13 删 legacy `blog.css` 段 | 只删 48 个无消费者选择器；段落保留 | 计划措辞已由 Codex 在 §22 纠正 |
| K2 删 13 个 shadcn 文件 | 实删 14 个（`dialog.tsx` 保留并改写为 LQ 适配） | 无 |
| §8.14 "白字-on-base 全 ≥4.5" | 与定稿色相矛盾，新增 `on-base`/`on-primary` 色对；`ink-3` L47→L45 | 令牌真源以 `tokens.css` 为准，§8.14 表需回写 |
| §8.15 "全站配色作用域" | 首屏令牌主作用域在 `html`，`body` 保留旧身份属性；教师首页/管理壳局部主色覆盖在 S1 桥接 | 无 |
| S3 "六条 spec" | 第六条命名为 `grading-return-resubmit.spec.ts` | 无 |
| K1 静态交付 | 增加"pre-S0 首次升级须先导出旧容器 Vite 资源到共享卷，失败停止切换"；Docker 挂载/发布**未实测** | 部署前必须做 Docker 演练 |
| §16 S0 "首胜" | 增加了几处计划外的最小修复：聊天 Escape 抢焦点、合班教务空表拒绝、multipart 可空整数 422、考试草稿测试依赖 | 均有测试 |

## 5. 待继续（按依赖顺序）

1. **负责人决策**：(a) 回答本地库是否有独有数据/定时任务，决定 `scheduled_tasks` 与签章 scope 是否需要从 dump/远端恢复；(b) 是否现在打检查点提交；(c) 是否认可 S0–S3 的本地证据作为阶段签字（计划 §22 的总进度框仍全空，Codex 刻意留给负责人）。
2. **S4-A 性能收口**：解决手机壳"关闭弹层回焦"长任务；跑 `lq-s4` 性能门禁到 6/6；然后 B/E/F 才能开工。
3. **S4-D 收尾**：复核三个登录文件并刷新 lint 指纹；运行 D2 的 8 个浏览器用例；迁一言 CSS 块；做会话过期真实入口。
4. **S4-C1/C2 收尾**：外观分区真实浏览器保存/409；消息 actions 与皮肤；然后 `/profile?section=notifications` 与教师 `/manage/me/notifications` 两个消息入口验收。
5. **S4-B**：双首页 + 日历 + 3D 课表宿主（`schedule-bridge.js` 已有）；学生首页 CLS 基线 0.067 必须降到 ≤0.05。
6. **S4-E / S4-F**：成长页族；其余管理页三宏调用点与页内 dialogs。
7. **台账补盘**：196 条"未盘点"至少推进到"已盘点"，否则 S5/S6 无法按 SOP 登记。
8. **S5 起**按计划 §16。

## 6. 接手者必读文件

- 计划真源：`docs/liquid-glass-execution-plan-2026-09.md`（§22 有 Codex 的施工记录与纠偏）。
- Codex 记录：`docs/lq-acceptance.md`（340 行，按阶段/轮次的证据与哈希）、`docs/lq-s4-preflight.md`（S4 分包、开关合同、当前进度）、`docs/lq-components.md`（组件冻结契约）、`docs/lq-action-registry.md`（69 行）、`docs/lq-migration-registry.json`、`docs/lq-lint-exceptions.json`、`docs/lq-static-nginx-validation.md`、`docs/lq-test-isolation-incident-2026-09-20.md`。
- 证据目录：`.codex-temp/lq-*`（日志、截图、results.json、源码检查点 `lq-s3-engineering-checkpoint/`、`lq-s4-first-package-source-checkpoint/`）。
- 开关：`classroom_app/lq_pilot.py`（S3 九路由）、`classroom_app/lq_migration.py`（S4 页族）。
- 测试入口：`python tools/test_backend.py [--pattern test_x.py]`（**唯一**允许的后端单测入口）；`npm test`；`npm run test:lq`；`npm run test:e2e:lq`；`npm run lint:lq`；`npm run check:lq-size`。
- 构建：`npm run build`（现含 `build:assets` 生成内容哈希静态图）。

## 附记：隔离后端全量结果

由 Claude 在 2026-09-21 运行 `python tools/test_backend.py`（隔离 SQLite，251 秒）：**3744 项，6 失败，13 错误，205 跳过**。日志 `.codex-temp/claude-backend-run-20260921.log`。四组失败全部落在 Codex 进行中的包或其抽取后未更新的旧测试，不涉及 S0–S2 已收口范围：

| 组 | 用例 | 原因 | 归属 |
|---|---|---|---|
| 13 错误 | `test_auth_session_recovery.SessionRecoveryTests`（全部） | `app.py:470` 的 401 恢复页 `TemplateResponse("session_expired.html", {...})` 在 Jinja 模板缓存抛 `TypeError: cannot use 'tuple' as a dict key (unhashable type: 'dict')`——S4-D3"会话不可用入口"尚未完成，与 lint 阻断的 `session_expired.html` 同源 | S4-D 进行中 |
| 1 失败 | `test_architecture_route_snapshot` | 路由基线未更新：新增 `/manage/me/appearance` 等 6 条（C1 路由票、教师评学预览等） | 维护项：复核后更新基线 |
| 1 失败 | `test_deployment_browser_cache.test_deploy_contract_rotates_release…` | 断言 `exam_take.html` 内联含 `` /draft`, { method: 'GET' ``；S3 已把 submit 逻辑抽到模块，旧测试未随之改 | S3 遗留：改断言指向新模块 |
| 4 失败 | `test_profile_template_contract.test_messages_keep_single_native_owner…`（学生/教师 × notifications/private） | 消息分区出现 `#message-center-file-input`，与"缺附件边界"合同不符——C2 附件端口接线中 | S4-C2 进行中 |

上述与 §1 的 lint 三项阻断一致：当前工作区可通过类型检查与前端单测，但**后端全量并非全绿**，接手者应先修这四组再继续新包。

## 7. S4 续建后的状态（2026-09-21 收尾）

七个施工包全部回报，工作区全量回归通过：隔离后端 3744 项全过、前端 78 文件 668 项、typecheck 干净、lint blocking 为空。逐包结果与证据见 [验收记录](lq-acceptance.md) 的「S4 续建」节。

**仍未完成（已如实记录，非隐瞒）**
1. **共享壳移动端性能门禁**：两个 390 场景未达 >50ms 长任务门槛（p95 均 ≤200ms）。根因已隔离到原生 `showModal` 本身，且本机因仓库位于坚果云同步目录而受同步进程干扰（同代码同构建，繁忙轮 19 个长任务、安静轮 0 个）。标记为「环境受限、待安静主机或真机复测」，未放宽阈值。建议把仓库移出同步目录后复测。
2. **B 包剩余项**：评估同步菜单改 `lq-menu`、工具行采用 `filter_bar` 宏、移动端 `lq-section--collapsible`。已有可直接执行的技术方案（见 `.codex-temp/claude-s4-b-report.md` 第三轮）。
3. **F 包剩余域**：材料库、归档、教务、我的四域仅完成页头开关接线，未做结构迁移；教学域与系统域已完成并验证。六个已验证页面本就无 `<table>` 标记，表格组件不适用，已如实记录。

**另行确认的既有缺陷（与本次改造无关）**：`dashboard-schedule.spec.ts` 的 3 个失败在全部开关关闭时同样出现，疑似合成运行时播种顺序问题，未确认根因。

**下一步建议**：先由负责人决定本地库事故的数据处置；再按计划 §16 进入 S5 教学核心链（六个模板 + 已就绪的后端票）。
