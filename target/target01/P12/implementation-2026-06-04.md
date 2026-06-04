# P12 实施与验收记录 - 2026-06-04

## 本批次目标

按 P12 的节奏推进“一个页面，一个页面级 island，一个旧脚本删除清单，一个浏览器回归”。本批次聚焦四个高风险页面入口：

- `classroom_page`：课堂页总体装配入口。
- `chat`：课堂讨论消息发送与 WebSocket 连接。
- `materials_manage`：教师材料管理页。
- `message_center`：消息中心与 profile 嵌入区。

本批次不改业务 URL、不改业务表结构、不迁移线上数据、不对线上 `/lanshare/data` 执行可写测试。所有浏览器写入验证均使用 `.codex-temp` 下的复制 runtime 数据根。

## 当前完成状态

| 页面/方向 | 页面级 island | 模板直挂旧脚本 | 浏览器回归 | 当前结论 |
| --- | --- | --- | --- | --- |
| classroom_page | 已新增 `frontend/src/islands/classroom-page.tsx` | 已从 `templates/classroom_main_v4.html` 移除旧 inline bootstrap 直调 | 已通过 | 页面入口已收束到 Vite island，旧模块仍作为过渡动态 import |
| chat | 已由 `classroom-page` 页面级 island 统一装配 | 已从模板直挂中移除 `chat.js` / `new ClassroomChat(...)` | 已通过单条消息发送且只渲染一次 | 可证明无明显重复发送/重复绑定，完整 React chat 仍是后续拆分项 |
| materials_manage | 已新增 `frontend/src/islands/materials-manage-page.tsx` | 已从 `templates/manage/materials.html` 移除 `materials_manage.js` 直挂 | 已通过教师上传/搜索、学生拒绝访问 | 页面入口已收束到 Vite island，旧控制器仍作为过渡动态 import |
| message_center | 已新增 `frontend/src/islands/message-center-page.tsx` | 已从 `templates/message_center.html` 和 `templates/profile.html` 移除 `message_center.js` 直挂 | 已通过教师标记已读、学生隔离渲染 | 页面入口已收束到 Vite island，旧控制器仍作为过渡动态 import |

## 代码变更范围

- 新增页面级 island：
  - `frontend/src/islands/classroom-page.tsx`
  - `frontend/src/islands/materials-manage-page.tsx`
  - `frontend/src/islands/message-center-page.tsx`
- Vite manifest 入口：
  - `vite.config.ts`
  - `static/dist/manifest.json` 由 `npm run build` 生成，包含上述页面入口。
- 模板入口收束：
  - `templates/classroom_main_v4.html`
  - `templates/manage/materials.html`
  - `templates/message_center.html`
  - `templates/profile.html`
- 测试与安全运行时：
  - `tests/test_frontend_authenticated_vite_integration.py`
  - `tests/e2e/specs/classroom.spec.ts`
  - `tests/e2e/specs/materials.spec.ts`
  - `tests/e2e/specs/message-center.spec.ts`
  - `tests/e2e/fixtures/p03.ts`
  - `tests/e2e/specs/zz-data-safety.spec.ts`
  - `tests/e2e/scripts/prepare_p03_runtime.py`
  - `tests/e2e/scripts/start-p03-server.ps1`
- 额外防护修复：
  - `classroom_app/services/agent_task_service.py`
  - `tests/test_agent_task_service.py`

## 旧代码删除与残留说明

### 已删除的模板直挂入口

- `templates/classroom_main_v4.html` 不再直接执行旧 module bootstrap 中的：
  - `initClassroomPage()`
  - `new ClassroomChat(...)`
  - `new ClassroomPrivateMessages(...)`
  - `fileApp.init(window.APP_CONFIG)`
  - `materialsApp.init(window.APP_CONFIG)`
  - `examApp.init(window.APP_CONFIG)`
- `templates/manage/materials.html` 不再直接加载 `/static/js/materials_manage.js`。
- `templates/message_center.html` 不再直接加载 `/static/js/message_center.js`。
- `templates/profile.html` 不再直接加载 `/static/js/message_center.js`。

### 过渡残留

以下旧脚本尚未删除，当前由页面级 island 动态加载，目的是在不改 URL、不改 DOM 合同、不破坏线上业务的前提下先收束入口：

- `static/js/classroom_page.js`
- `static/js/chat.js`
- `static/js/classroom_private_messages.js`
- `static/js/classroom_materials.js`
- `static/js/classroom_interactions.js`
- `static/js/collaboration.js`
- `static/js/materials_manage.js`
- `static/js/message_center.js`

后续删除这些文件前必须满足：对应功能已经进入 React 状态树或共享 lib，相关事件监听、定时器、WebSocket、API 请求数通过测试证明未重复，且没有模板、Vite entry、动态 import 或运行时路径继续依赖。

## 浏览器回归覆盖

`npm run test:e2e:p12` 已覆盖：

- 教师课堂页真实渲染。
- 学生课堂页真实渲染。
- 普通教师直接访问他人课堂被拒绝。
- 教师课堂讨论区发送唯一消息，页面只渲染一次。
- 教师材料管理页打开、刷新、上传临时 QA 文件、搜索可见。
- 学生不能打开教师材料管理页。
- 教师从消息铃进入消息中心并标记已读。
- 学生消息中心只渲染当前学生会话范围。

`npm run test:e2e:p03` 已覆盖更宽业务链：

- 教师登录、学生登录、匿名访问保护。
- 学生提交作业、教师查看提交与详情页。
- 课堂页、材料页、消息中心。
- P11 后台任务台账。
- 超管系统权限页与普通教师/学生拒绝访问。
- AI 批改成功、停止批改、防 stale callback、越权批改拒绝。
- 复制 runtime 数据库隔离与 `quick_check=ok`。

## 已运行测试

| 命令 | 结果 |
| --- | --- |
| `npm run typecheck` | 通过 |
| `npm test` | 通过，18 files / 43 tests |
| `npm run build` | 通过 |
| `npm run inventory:frontend` | 通过，已刷新 `docs/frontend-migration-inventory.md` |
| `python -m unittest tests.test_frontend_authenticated_vite_integration tests.test_agent_task_service` | 通过，9 tests |
| `python -m unittest discover -s tests -p "test_*.py"` | 通过，75 tests |
| `npm run test:e2e:p12` | 通过，7 tests |
| `npm run test:e2e:p03` | 通过，21 tests |

## 数据安全验收

- 浏览器可写回归使用 `.codex-temp/p12-playwright-runtime7`、`.codex-temp/p12-p03-full-runtime2` 等临时 runtime。
- `tests/e2e/scripts/prepare_p03_runtime.py` 会复制数据库到临时 root，设置 `LANSHARE_DATA_ROOT`、`MAIN_DATA_DIR`、`MAIN_DB_PATH`，并做写探针。
- `tests/e2e/scripts/start-p03-server.ps1` 同步设置 `MAIN_DB_PATH`，避免 `.env` 或外部环境把测试服务带到真实库。
- 数据安全测试断言数据库路径位于 `.codex-temp`，以 `db/classroom.db` 结尾，并且等于 fixture 中记录的临时数据库路径。
- 已通过 `zz-data-safety.spec.ts` 的 runtime 隔离与 `quick_check=ok` 验证。
- 本批次未对线上 `/lanshare/data`、远程生产 SQLite、真实本地 `data/classroom.db` 执行 Playwright 写入验证。

## 后续拆分条件

下一阶段不能只继续新增 React 组件，必须按以下条件逐段删除旧逻辑：

- `message_center`：把 bootstrap、通知列表、私信会话、AI job 轮询迁入 React 后，删除 `message-center-page.tsx` 中的动态 import，再删除 `static/js/message_center.js`。
- `materials_manage`：把材料列表、上传、AI 导入轮询、最终材料入口迁入 React 后，删除 `materials-manage-page.tsx` 中的动态 import，再删除 `static/js/materials_manage.js`。
- `chat`：把公开讨论、私信、附件、WebSocket 生命周期迁入 React 后，删除 `classroom-page.tsx` 中对 `chat.js` 和 `classroom_private_messages.js` 的动态 import。
- `classroom_page`：把课堂页装配职责迁入稳定的 page controller / hooks 后，继续减少 `classroom_page.js`、`classroom_materials.js`、`classroom_interactions.js` 等旧模块依赖。

每删除一段旧逻辑，都必须重跑至少 `npm run typecheck`、`npm run build`、`python -m unittest tests.test_frontend_authenticated_vite_integration`、`npm run test:e2e:p12`，涉及作业/AI/权限时还要重跑 `npm run test:e2e:p03`。
