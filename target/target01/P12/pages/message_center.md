# P12 页面迁移工作单：message_center

## 页面与业务边界

- 页面入口：
  - `templates/message_center.html`
  - `templates/profile.html` 中 `notifications` / `private` 嵌入区
- 当前业务可达入口：
  - `GET /message-center` 会保持现有行为，返回 303 并跳转到 `/profile?section=notifications#profile-message-center`。
  - 因此第一阶段验收以 profile 嵌入区为真实渲染目标，同时保持 `message_center.html` 的页面级 island 注入，避免未来恢复独立消息中心模板时重新引入旧脚本直挂。
- 后端边界：
  - `classroom_app/routers/message_center.py`
  - `classroom_app/routers/profile.py`
- 现有 React island：
  - `frontend/src/islands/message-center-sync.tsx`：全局顶部消息摘要同步。
  - `frontend/src/islands/message-center-workspace-sync.tsx`：根据旧控制器广播的 workspace snapshot 展示未读、分类、联系人、AI 回复中、附件和冷却状态，并发出快捷命令。
  - `frontend/src/islands/message-center-page.tsx`：P12 页面级控制器入口。当前先负责从 Vite 入口接管页面初始化，不再由模板直接加载旧脚本。

## 旧脚本职责清单

- 旧脚本：`static/js/message_center.js`
- 当前职责：
  - 拉取 `/api/message-center/bootstrap`，初始化通知分类、未读数、联系人、黑名单、当前会话。
  - 拉取 `/api/message-center/items`，渲染通知卡片、搜索、筛选、标记已读。
  - 拉取 `/api/message-center/private/conversation`，渲染私信会话、附件、图片预览。
  - 调用 `/api/message-center/private/messages`，发送私信和附件。
  - 调用 `/api/message-center/private/blocks`，拉黑、解除拉黑、刷新黑名单。
  - 轮询 `/api/message-center/private/ai-jobs/{job_id}`，展示 AI 助教回复生成状态。
  - 维护 Markdown 工具栏、表情选择器、附件选择器、发送冷却、URL tab 同步。
  - 向 `message-center-workspace-sync` 广播 `lanshare:message-center-workspace-change`。
  - 监听 `lanshare:message-center-workspace-command`，执行刷新、切换通知、切换私信、打开当前会话等命令。

## P12 当前状态

- [x] 新增页面级入口 `frontend/src/islands/message-center-page.tsx`。
- [x] `vite.config.ts` 已注册 `frontend/src/islands/message-center-page.tsx` 为独立 Vite entry。
- [x] `templates/message_center.html` 已挂载 `data-lanshare-island="message-center-page"`。
- [x] `templates/profile.html` 的消息中心嵌入区已挂载 `data-lanshare-island="message-center-page"`。
- [x] `templates/message_center.html` 不再直接加载 `/static/js/message_center.js`。
- [x] `templates/profile.html` 不再直接加载 `/static/js/message_center.js`。
- [x] `/message-center` 继续保持 303 跳转行为，不改变现有 URL 语义。
- [x] `docs/frontend-migration-inventory.md` 已刷新，`message_center.html` 与 `profile.html` 中 `js/message_center.js` 的模板引用次数降为 0。
- [x] 新增 `npm run test:e2e:p12`，当前覆盖 message center 教师/学生浏览器回归。
- [x] 真实浏览器验证使用 `.codex-temp` 测试数据根，未写入线上 `/lanshare/data`。
- [ ] 旧控制器逻辑尚未迁入 React 状态树；当前由 `message-center-page` 以过渡方式动态加载旧脚本。
- [ ] `static/js/message_center.js` 尚未删除。删除前必须证明没有模板、测试、动态 import 或运行时入口继续依赖它。

## 后续拆分顺序

1. 将只读 bootstrap 正规化到 `frontend/src/lib/message-center-workspace.ts` 或新的 `message-center-page-state.ts`，保持 API 返回结构不变。
2. 将通知列表、tab、搜索、筛选、标记已读迁入 React，先覆盖独立 `/message-center` 页面。
3. 将 profile 嵌入区复用同一页面级 island，使用同一状态模型，只保留容器差异。
4. 将私信联系人、会话、附件和发送流程迁入 React。
5. 将 AI 回复 job 轮询迁入 React effect，确保离开页面时清理 interval / timeout。
6. 删除 `message-center-page.tsx` 中的旧脚本动态 import。
7. 删除 `static/js/message_center.js`，并更新前端迁移清单和测试。

## 可删除旧脚本的硬性条件

- [ ] `rg "/static/js/message_center.js|message_center.js" templates frontend static tests docs` 只允许在历史文档或迁移说明中出现，不允许存在运行时入口。
- [ ] `/message-center` 在真实浏览器中可以完成：
  - 加载页面。
  - 切换通知分类。
  - 搜索或筛选通知。
  - 标记已读。
  - 切换私信 tab。
  - 选择联系人。
  - 输入 Markdown 内容。
  - 选择表情。
  - 发送私信。
  - 查看发送后会话状态。
  - 触发 AI 回复后看到“生成中/完成/失败”之一的明确状态。
- [ ] `/profile?section=notifications#profile-message-center` 在真实浏览器中可以看到消息中心嵌入区，并且未读、通知列表、私信入口正常。
- [ ] `message-center-sync`、`message-center-workspace-sync` 与页面级 island 的职责不重复：
  - 顶部消息铃铛只做摘要提醒。
  - workspace island 只做页面状态摘要和快捷命令。
  - page island 拥有页面数据加载、事件绑定、表单提交和状态更新。

## 测试与验收

- 单元 / 集成：
  - [x] `npm run typecheck` 通过。
  - [x] `npm run build` 通过，manifest 中包含 `frontend/src/islands/message-center-page.tsx`。
  - [x] `python -m unittest tests.test_frontend_authenticated_vite_integration tests.test_agent_task_service` 通过。
- 浏览器回归：
  - 使用测试数据根或 `.codex-temp` 复制数据根，不允许直接对线上数据库写入。
  - [x] 教师账号打开 `/message-center`，保持 303 跳转到 profile 消息中心，Vite page island 与 workspace island 正常挂载。
  - [x] 学生账号打开 `/profile?section=notifications#profile-message-center`，通知区可见。
  - [x] 教师标记已读写操作只在 `.codex-temp` 测试库执行，未读数刷新为 0。
  - [x] `npm run test:e2e:p12` 通过，包含教师消息铃铛进入消息中心并标记已读、学生消息中心隔离渲染两个用例。
- 数据安全：
  - 不修改业务表结构。
  - 不迁移、不清空、不重建线上消息、私信、附件或 AI job 数据。
  - 不把测试附件、测试消息写入 `/lanshare/data` 或真实线上数据库。

## 2026-06-04 执行证据

- 代码入口：
  - `frontend/src/islands/message-center-page.tsx`
  - `templates/message_center.html`
  - `templates/profile.html`
  - `vite.config.ts`
- 删除结果：
  - 模板不再直挂 `/static/js/message_center.js`。
  - `static/js/message_center.js` 仍由 page island 动态加载，属于过渡残留，后续需要继续迁移内部逻辑后删除。
- 额外修复：
  - `classroom_app/services/agent_task_service.py` 的队列状态 GET 不再因为过期 composer 清理写失败导致页面 500；读取时按 TTL 过滤 stale composer，真实写接口仍保留失败暴露。
  - `tests/e2e/scripts/start-p03-server.ps1` 现在会在 P03 runtime 准备失败时立即中止。
  - `tests/e2e/scripts/prepare_p03_runtime.py` 复制测试数据库后会显式 materialize、设为可写并做写入探测。
- 已跑命令：
  - `npm run typecheck`：通过。
  - `npm run build`：通过。
  - `python -m unittest tests.test_frontend_authenticated_vite_integration tests.test_agent_task_service`：通过。
  - `npm run inventory:frontend`：通过。
  - `npm run test:e2e:p12`：通过，2 passed。
- 浏览器手工验证：
  - 本地测试服务使用 `http://127.0.0.1:8123` 和 `.codex-temp/p03-runtime`。
  - 教师登录后点击消息入口，跳转到 `/profile?section=notifications#profile-message-center`。
  - `message-center-page` attached，`message-center-workspace-sync` visible，消息列表非空，无 console error/warn。
  - 点击“标记当前已读”后，未读数为 `0`，消息状态显示已读。
