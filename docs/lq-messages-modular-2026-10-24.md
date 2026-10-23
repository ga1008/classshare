# 私信 / 通知页模块化改造与 LQ 模块使用记录本（2026-10-24）

用户反馈私信页：板块内间隙过大；板块元素不走液态玻璃模块化，背景固定、文字难读；深色主题下部分文字完全不可见；要求"所有元素皆组件"、每个模块的使用位置可追查、改模块前先评估影响。本轮不逐处补丁，而是去掉页面私有样式、把页面交还给共享模块，并建立模块使用记录本。

## 根因

| 现象 | 根因 | 处理 |
|---|---|---|
| 深色下边栏、会话区白板白字；输入区整块白 | `ui-system.src.css` 遗留 `.message-center-*` 约 1150 行：`rgba(255,255,255,.96)` 渐变固定白底、`rgba(148,163,184,…)` 描边、`--text-*` 旧墨色；它们排在 lq 之后、特异性相同，压住共享材质 | 整块删除（含两份旧 `.emoji-picker`、`.profile-message-*`），不再用覆盖对抗 |
| 工具栏与输入框之间一大段空白 | 遗留 `.message-center-composer__actions { flex-direction: column; min-height: 100% }` 把 4 个按钮竖排，编辑列 `align-items: flex-end` 被撑高 | 输入区改用共享 Composer 槽位：`lq-composer__actions` 一行（tools 槽 + 发送） |
| 「图片」按钮显示问号 | `icon='image'` 不在 lucide 允许清单，宏回退到 circle-question-mark | 允许清单增加 ban/bold/braces/code/heading/image/italic/list-ordered/quote/user-x 并重新生成 |
| 模板同时维护 LQ / 旧两套 DOM | `messages_lq_enabled` 双分支；组件 spec 固定渲染旧分支 | 私信家族无条件 LQ，删除旧分支与未路由的 `templates/message_center.html`；fixture 渲染器注册 `lq_props` |
| 黑名单行、当前联系人卡、AI 轮询错误是私有 innerHTML | 未走内容工厂 | 分别改为 `lqContent` List（trail 动作 `data-unblock`）、Card（`data-lq-selected` 选中配对 + Chip）、Empty(error) + 重试动作 |

## 模块与消费者

- 页面：`templates/partials/profile/messages.html`（结构声明，无配色）、`static/css/lq/pages/message-center.css`（只排版）。
- 复用：Surface(content ×4 顶层面板)、PageHead、Field/Input/Select(+Dropdown)、Button/Chip/Badge、List/Row、Card、Empty、Bubble、Composer、Toast、EmojiPicker。
- 新增共享模块：`static/css/lq/components/emoji-picker.css`（raised 浮层几何；消费者 message_center.js / blog.js / feedback.js）。
- 记录本：`docs/lq-module-registry.md` / `.json`（生成器 `tools/ui/lq_module_registry.py`；`npm run inventory:lq-modules`、`npm run check:lq-modules`），设计说明 `docs/lq-module-registry.notes.json`。AGENTS.md 与组件合同 §7 已要求改模块前查、改后更新。

## 保留的业务合同

`message_center.js` 仍是唯一 owner：id / `data-*` 钩子全部保留；草稿、附件队列（≤8、100MB）、发送冷却、AI 轮询、黑名单、412/409/413 处理、焦点与 Enter 换行策略均未改。`data-lq-auto-grow` 由 `enhanceForms` 处理；composer 不重复调用 `enhanceComposer`。

## 验证（隔离合成环境，正式构建）

- `npm run typecheck`、`npm run build:css/frontend/assets` 通过；`tsc` 无未使用项。
- 组件 spec（真实 Jinja 渲染的新 partial + 隔离路由）：`message-center-actions / drafts / races / ai-poll` 65/65 通过。
- vitest `tests/lq` + workspace lib 209/209；后端 `test_lq*` 226 项中 224 通过，2 项失败为 `test_lq_partial_scope` 对 `manage/materials.html`、`system/users.html` 的已登记源码指纹过期（上游 40b94088 / d91d2581 改动，未在本轮触碰；`npm run lint:lq` 同样提前报告这两项与 `search.css` 的既有 z-index 字面量）。
- 浏览器（p03-qa-lq，1440 / 390 宽，深色与浅色）：私信面板 `rgba(21,26,40,.5)` / `rgba(255,255,255,.5)` 配对主题墨；边栏标签、提示、标题全部可读；composer 一行动作；390 宽无横向溢出；通知页列表/芯片/设置按钮正常。
- 页面 spec `lq-message-layout.spec.ts`（专用配置 8295 端口 + `prepare_ui_v3_runtime.py` 合成库 + `serve_ui_v3.py`）：学生 / 教师各 1 项通过（照片背景浅色 1440 宽、390 宽深色、通知↔私信切换、阅读状态 Selection 弹层模糊）。首轮教师项暴露 **阅读状态 select 双 owner**（message_center.js 的 Selection 与平台 Dropdown 同时接管），已按合同改为 `data-lq-selection-owner`；首轮学生项失败为冷启动服务器登录超时，预热后通过。

## 发布结果

| 项目 | 结果 |
|---|---|
| 产品代码提交 | `3b6c6c49`，已推送 `origin/dev`（推送与部署分别完成） |
| 服务器发布号 | `20261024-020933-9b215a717e3e`（冻结工作树 `frozen-3b6c6c49` 发布，清单 3538 个文件，不含未跟踪个人文档与缓存目录） |
| PostgreSQL 门禁 | 未改 `classroom_app/db/*` 与 `config.py`，复用 `.codex-temp/library-cards-gate/native-report.json`（77 个迁移源文件）+ 同一 dump，validate ok |
| 服务恢复 | app / ai / mailer / postgres healthy，scheduler / crawler / agent-worker 启动，Nginx 已刷新；主应用与 AI 健康检查 ok，近期无错误日志 |
| 公网核验 | 登录页引用新资源图；线上 `tailwind-app.css` 与本地验收构建逐字节一致，遗留 `.message-center-composer__actions{min-height:100%}` 规则已消失，新增 `.emoji-picker` 组件规则与 `message-center-conversation__identity` 页面规则均在线 |
| 既有告警 | `background_tasks.ok=false` 为历史失败计数，与本次发布无关 |

