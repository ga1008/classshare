# P12 - 前端岛屿迁移与旧脚本收束目标

状态：待实施  
创建日期：2026-06-04  
优先级：P1  
目标类型：前端架构治理、Vite islands 迁移、旧 JavaScript 删除节奏、真实浏览器回归门

## 目标定义

将 LanShare 的下一阶段前端现代化收束为一个明确、可执行、可验收的 P1 目标：继续沿用现有 FastAPI/Jinja SSR 页面壳 + Vite + React + TypeScript islands 方向，但每次迁移都必须按“一个页面、一个页面级 island、一个旧脚本删除清单、一个真实浏览器回归”推进。

P12 的验收标准不是“React 组件能挂载”或“Vite build 成功”，而是迁移后的页面必须证明：

1. 旧事件绑定已经被移除或明确降级为兼容残留，不再和 React island 重复响应同一次用户操作。
2. 页面状态只存在一个权威来源，不再由旧脚本和新 island 分别维护相同 loading、selected、unread、active tab、submission、material、chat 等状态。
3. 同一业务动作不会因为新旧脚本叠加而重复调用 API、重复发消息、重复保存、重复上传、重复轮询或重复打开 WebSocket。
4. 旧脚本的模板引用、全局函数、定时器、事件监听、DOM 协议和 API 调用有可追踪删除清单。
5. 迁移页面通过真实浏览器回归，能点击、提交、刷新、切换、查看状态，并且无新增控制台错误和非预期 4xx/5xx。
6. 所有可写回归只使用复制出来的临时数据根，绝不写入线上 `/lanshare/data`、远程生产 SQLite、或本地真实 `data/classroom.db`。

## 当前基线

### 已有 Vite islands 基础

- `templates/partials/vite_islands.html` 已提供全局 island 注入点，例如 `app-shell`、`message-center-sync`、`blog-topbar-sync`、`student-security-sync`。
- `frontend/src/lib/mount-react-island.tsx` 已提供统一挂载桥：通过 `data-lanshare-island` 查找挂载点，并用 `createRoot` 渲染 React 组件。
- `classroom_app/frontend_assets.py` 与 `vite_entry_tags(...)` 已提供 Vite manifest 解析和生产构建产物注入。
- `frontend/src/lib/api-client.ts` 已作为前端 API 基础层，后续数据型 island 应优先复用，不再散落新的 `fetch` 包装。
- 现有测试包含 Vite manifest、island 注入、Vitest、TypeScript、P03 真实浏览器回归等基础。

### 当前高风险旧脚本

根据 `docs/frontend-migration-inventory.md` 的高风险传统脚本清单，P12 第一阶段聚焦以下旧脚本：

| 优先级 | 旧脚本 | 当前风险 | 主要页面 |
| --- | --- | --- | --- |
| 1 | `static/js/classroom_page.js` | 体积大，承担课堂页多领域初始化，容易和多个课堂 island 重复维护状态 | `templates/classroom_main_v4.html` |
| 2 | `static/js/chat.js` | 维护聊天、WebSocket、滚动、附件和发送状态，重复绑定风险高 | `templates/classroom_main_v4.html` |
| 3 | `static/js/materials_manage.js` | 材料管理页承担列表、上传、AI 导入、最终材料、导出等多职责 | `templates/manage/materials.html` |
| 4 | `static/js/message_center.js` | 消息中心和 profile 区域复用，容易和 `message-center-sync`、`message-center-workspace-sync` 形成重复轮询和状态 | `templates/message_center.html`、`templates/profile.html` |

### 当前模板入口线索

- `templates/classroom_main_v4.html` 当前仍显式导入 `ClassroomChat` 与 `initClassroomPage`，同时已经挂载多个课堂相关 Vite islands。
- `templates/manage/materials.html` 当前仍导入 `materials_manage.js`。
- `templates/message_center.html` 与 `templates/profile.html` 当前仍导入 `message_center.js`，同时已经挂载 `message-center-workspace-sync`。

这说明当前方向是正确的，但存在“新 island 只做同步增强，旧脚本继续完整运行”的堆叠风险。P12 的核心就是给旧脚本退场建立节奏。

## 总体原则

- 一个页面一个迁移单元：每次只选择一个页面作为主要迁移对象，不把 classroom、chat、materials、message center 混在一个不可回滚的大改里。
- 一个页面级 island 一个权威入口：允许 island 内拆分 React 子组件和 hooks，但模板层面应收束为一个页面级挂载入口；已有多个细粒度 island 可在迁移中合并、归属或保留为明确子职责，不能继续无边界叠加。
- 一个旧脚本删除清单：迁移前必须列出旧脚本的职责、全局函数、事件监听、DOM 选择器、`data-*` 协议、定时器、WebSocket、API 路径和模板引用；迁移后逐项标记删除、替代、保留或延期。
- 一个真实浏览器回归：每完成一个页面迁移，必须有对应 Playwright 回归覆盖真实渲染、点击、输入、网络请求、控制台错误和关键状态可见性。
- 删除旧代码是验收条件：不能只新增 React island 后留下旧脚本继续全量执行。允许短期兼容残留，但必须有到期条件和下一步删除项。
- 后端权限仍是最终边界：前端迁移不得把权限判断从后端搬到前端，不得通过隐藏按钮替代后端 403。
- 不改业务 URL：P12 不以修改现有 URL、表单 endpoint、API 路径作为目标；若发现 API 形状必须调整，应另立后端兼容目标。
- 不损坏线上数据：所有测试、压测、浏览器回归、脚本试运行都必须使用复制数据根或临时 QA fixture，严禁对线上生产数据执行可写验证。

## 明确不做

- 不在 P12 中把整站改成 SPA。
- 不为前端迁移修改业务表结构。
- 不为了简化迁移删除课堂、聊天、材料、消息中心、AI 状态、上传、导出、私信等现有业务能力。
- 不用 React island 复制一份旧逻辑后让旧脚本继续运行。
- 不在生产站点或远程 `/lanshare/data` 上执行可写 Playwright 测试。
- 不把真实教师、学生、管理员密码、cookie、token、API key 写入测试、文档、日志、trace、截图说明或提交记录。
- 不用 `test.skip`、`test.fixme`、`only`、软断言或超长 timeout 掩盖迁移失败。
- 不以“页面肉眼能打开”替代事件绑定、网络请求、状态一致性和权限边界验证。

## 第一阶段范围

P12 第一阶段只做四个页面级迁移目标，按以下顺序推进。

### 1. 课堂页 `classroom_page`

目标：将 `classroom_main_v4.html` 中由 `classroom_page.js` 承担的课堂页初始化、工作区切换、页面状态同步、材料/资源/作业区协调等职责，逐步迁入一个页面级 classroom island。

建议新入口：

```text
frontend/src/islands/classroom-page.tsx
```

允许复用或整合已有模块：

```text
frontend/src/islands/assignment-task-board-sync.tsx
frontend/src/islands/classroom-activity-workspace-sync.tsx
frontend/src/islands/resource-workspace-sync.tsx
frontend/src/islands/material-learning-path-sync.tsx
frontend/src/islands/learning-progress-sync.tsx
frontend/src/islands/assignment-authoring-sync.tsx
frontend/src/islands/exam-assign-sync.tsx
frontend/src/lib/classroom-activity-workspace.ts
frontend/src/lib/material-learning-path.ts
```

完成条件：

- [ ] 建立 `classroom_page.js` 职责清单，至少包含初始化入口、全局函数、事件监听、工作区切换、材料刷新、资源列表、作业弹窗、考试发布弹窗、学习进度弹窗、成员入口、上传框、文件详情弹窗等。
- [ ] 新页面级 island 能读取现有模板 payload 或 `data-*`，不要求后端 URL 变化。
- [ ] 模板中课堂主区域只有一个权威页面级 island 负责协调页面状态；已有细粒度 island 若保留，必须归属到页面级 island 的职责表中，不能形成重复状态。
- [ ] `initClassroomPage` 不再被模板直接调用，或仅保留明确的兼容残留并列入下一步删除项。
- [ ] 点击课堂工作区 tab、材料入口、资源入口、作业入口、考试入口时，不出现双重 UI 更新或双重 API 请求。
- [ ] 学生和教师进入课堂页时权限可见性与迁移前一致。
- [ ] 越权课堂 URL 仍返回现有无权语义，不因为前端迁移暴露正文。

预期测试结果：

- [ ] `npm run typecheck` 通过。
- [ ] `npm test` 通过，新增 classroom island 单元测试或组件行为测试。
- [ ] `npm run build` 通过并生成 Vite manifest。
- [ ] `python -m unittest tests.test_frontend_authenticated_vite_integration` 通过，确认 SSR 注入契约仍在。
- [ ] Playwright 课堂页回归通过：教师课堂页、学生课堂页、跨课堂无权访问、关键 tab 切换、至少一个材料/资源/作业入口可交互。
- [ ] 浏览器控制台无新增 error；网络面板中同一点击动作不会产生重复业务 POST。

### 2. 课堂聊天 `chat`

目标：将 `chat.js` 中的聊天初始化、WebSocket 连接、消息发送、消息列表刷新、滚动定位、附件预览、图片衍生状态、私聊入口等职责迁入一个独立但归属于课堂页的 chat island。

建议新入口：

```text
frontend/src/islands/classroom-chat.tsx
```

完成条件：

- [ ] 建立 `chat.js` 删除清单，列出 `ClassroomChat` 构造、公开方法、WebSocket 事件、DOM 选择器、发送按钮监听、附件监听、滚动监听、重连策略、消息渲染和错误提示。
- [ ] 同一课堂页面只建立一个课堂讨论 WebSocket 连接；私聊如有独立连接，也必须明确连接数和关闭策略。
- [ ] 发送一条消息只触发一次发送请求或一次 WebSocket send，不得因新旧脚本并存发送两次。
- [ ] 附件上传、图片预览、失败重试、发送中状态保留现有用户体验。
- [ ] 页面切换或销毁时能清理事件监听、定时器和连接，避免重复进入课堂后消息重复显示。
- [ ] 迁移后模板不再导入 `chat.js`，或仅保留有限兼容包装并有删除日期。

预期测试结果：

- [ ] Playwright 覆盖教师或学生在课堂页发送消息，页面能看到新消息。
- [ ] 测试记录 WebSocket 或发送 API 调用次数，单次发送不超过一次业务写入。
- [ ] 刷新课堂页后聊天仍能加载历史消息，不丢失现有滚动和空状态。
- [ ] 控制台无新增 error，网络无非预期 401/403/500。
- [ ] 如果测试使用附件，文件必须来自临时 fixture，不写真实提交目录或线上存储。

### 3. 材料管理 `materials_manage`

目标：将 `manage/materials.html` 中由 `materials_manage.js` 负责的材料库、上传、AI 导入、最终材料、导出和状态轮询逐步迁入 materials manage 页面级 island。

建议新入口：

```text
frontend/src/islands/materials-manage.tsx
```

完成条件：

- [ ] 建立 `materials_manage.js` 删除清单，至少列出材料树/列表、搜索筛选、上传、批量操作、AI 导入候选、AI 导入进度、最终材料生成、导出、错误提示和轮询逻辑。
- [ ] 材料库列表、文件夹/材料选择、上传入口和错误状态在 island 中有明确状态模型。
- [ ] AI 导入状态轮询只在存在 active job 时高频执行，终态后停止或降频。
- [ ] 上传、删除、重命名、移动、AI 导入、最终材料生成等可写动作仍全部走后端权限检查。
- [ ] 同一按钮点击只触发一次业务请求，不出现旧脚本和新 island 同时提交。
- [ ] 迁移后模板移除 `materials_manage.js` 引用，或只保留已记录的短期兼容残留。

预期测试结果：

- [ ] Playwright 材料管理回归通过：教师打开 `/manage/materials`，能看到材料列表或空状态，能执行至少一个非破坏性筛选/切换动作。
- [ ] 可写测试必须使用复制数据根；若测试上传材料，文件写入临时数据目录，测试后 quick_check 为 ok。
- [ ] AI 导入状态测试可使用 mock 或测试 fixture，不触发真实大规模 AI 消耗。
- [ ] 控制台无新增 error；材料列表相关 GET/POST 无重复请求。
- [ ] 普通无权用户不能通过直接 URL 或伪造按钮访问管理动作。

### 4. 消息中心 `message_center`

目标：将 `message_center.js` 中的消息列表、已读、筛选、私信表单、Markdown 工具栏、summary 刷新和 profile 通知区域同步，迁入 message center 页面级 island，并与现有 `message-center-sync`、`message-center-workspace-sync` 明确分工。

建议新入口：

```text
frontend/src/islands/message-center-page.tsx
```

完成条件：

- [ ] 建立 `message_center.js` 删除清单，列出消息列表加载、summary 刷新、已读操作、私信发送、Markdown 工具栏、筛选切换、profile 嵌入区初始化、轮询策略和错误提示。
- [ ] 明确 `message-center-sync` 只负责全局入口摘要，页面级 island 负责消息中心正文；两者不能重复高频请求同一 summary。
- [ ] `message-center-workspace-sync` 若继续存在，必须说明它服务 profile 嵌入区还是消息中心页；不能和页面级 island 重复绑定同一 DOM。
- [ ] 已读、全部已读、筛选切换、私信发送等操作只触发一次业务请求。
- [ ] 消息未读数在全局入口、消息中心页、profile 嵌入区之间最终一致。
- [ ] 迁移后 `message_center.html` 与 `profile.html` 移除 `message_center.js` 引用，或有明确短期兼容残留与删除条件。

预期测试结果：

- [ ] Playwright 消息中心回归通过：打开消息中心、切换筛选、标记已读或发送测试私信、未读状态刷新。
- [ ] profile 通知区域仍能渲染，旧消息列表、已读按钮和私信入口不丢失。
- [ ] 网络请求计数证明 summary 不被多个 island 重复轮询。
- [ ] 普通学生、教师、超管看到的消息范围符合后端权限与当前业务语义。
- [ ] 控制台无新增 error，无非预期 401/403/500。

## 每个页面必须交付的文件或记录

每迁移一个页面，必须在 `target/target01/P12` 下补充对应记录。建议结构：

```text
target/target01/P12/
  README.md
  pages/
    classroom_page.md
    chat.md
    materials_manage.md
    message_center.md
```

每个页面记录至少包含：

- [ ] 迁移前模板入口：模板路径、旧脚本引用、Vite entry、`data-lanshare-island` 挂载点。
- [ ] 旧脚本职责清单：函数、类、事件监听、选择器、全局变量、定时器、WebSocket、API 路径。
- [ ] 新 island 职责清单：入口文件、子组件、hooks、共享 lib、payload 来源、错误状态。
- [ ] 旧代码删除清单：已删除、保留、延期、原因、下一步删除条件。
- [ ] API 请求对照：迁移前后同一操作的请求数量、路径、方法、是否重复。
- [ ] 权限边界检查：教师、学生、超管、无权用户分别能看见和不能看见什么。
- [ ] 浏览器回归证据：命令、结果、trace/screenshot 路径、控制台错误结论。
- [ ] 数据安全说明：使用的数据根、是否复制库、是否 quick_check、是否未触碰线上数据。

## 测试与验收命令

P12 页面迁移完成后，至少需要运行以下命令。若某项暂时无法运行，必须说明原因、风险和替代验证，不能静默跳过。

### 基础静态与单元测试

```powershell
npm run typecheck
npm test
npm run build
npm run inventory:frontend
```

预期结果：

- TypeScript 无错误。
- Vitest 全部通过。
- Vite production build 成功，`static/dist/manifest.json` 生成。
- `docs/frontend-migration-inventory.md` 更新后能显示迁移后的 Vite entry、模板引用和旧脚本体积变化；已迁移旧脚本的模板引用次数应下降到 0，或残留原因在 P12 页面记录中明确说明。

### 后端兼容与 SSR 注入测试

```powershell
python -m unittest tests.test_frontend_authenticated_vite_integration
python -m unittest discover -s tests -p "test_*.py"
```

预期结果：

- authenticated Vite integration 通过，证明 SSR 页面壳、Vite entry、旧入口兼容断言未破坏。
- 后端全量 unittest 通过，特别是权限、材料、作业、消息、后台任务相关测试不得回退。

### 真实浏览器回归

```powershell
npm run test:e2e:p03
```

P12 实施时应新增或扩展页面级 Playwright spec。建议后续增加独立命令：

```powershell
npm run test:e2e:p12
```

预期结果：

- Chromium 下所有 P12 页面用例通过。
- 每个迁移页面至少有桌面端真实渲染回归；课堂页和消息中心应补移动端或窄屏关键布局检查。
- 每个页面测试必须监听 console error、pageerror、failed request。
- 单次点击、提交、发送、已读、上传等动作不得产生重复业务写入。
- 测试 trace 或截图能定位失败原因，不能只有“timeout”。

### 数据安全验证

所有可写浏览器测试必须使用临时数据根，例如：

```powershell
$env:LANSHARE_DATA_ROOT = ".codex-temp\\p12-runtime"
$env:MAIN_DATA_DIR = ".codex-temp\\p12-runtime"
```

预期结果：

- 测试运行前复制或初始化临时数据库。
- 测试结束后临时数据库 `PRAGMA quick_check` 返回 `ok`。
- 本地真实 `data/classroom.db` 未被写入。
- 远程 `/lanshare/data` 未被用于测试。
- 文档、日志、trace、截图说明中不包含真实密码、cookie、token、API key。

## 验收清单

P12 第一阶段只有在以下条件全部满足时才能标记完成：

| 序号 | 条件 | 状态 | 证据 |
| ---: | --- | --- | --- |
| 1 | `classroom_page` 页面迁移记录完成 | 已完成 | `pages/classroom_page.md` |
| 2 | `classroom_page.js` 模板引用已删除或残留理由明确 | 待实施 |  |
| 3 | 课堂页浏览器回归通过，无重复事件/API | 待实施 |  |
| 4 | `chat` 页面迁移记录完成 | 已完成 | `pages/chat.md` |
| 5 | `chat.js` 模板引用已删除或残留理由明确 | 待实施 |  |
| 6 | 聊天浏览器回归通过，单次发送不重复 | 待实施 |  |
| 7 | `materials_manage` 页面迁移记录完成 | 已完成 | `pages/materials_manage.md` |
| 8 | `materials_manage.js` 模板引用已删除或残留理由明确 | 待实施 |  |
| 9 | 材料管理浏览器回归通过，AI 导入/轮询不重复 | 待实施 |  |
| 10 | `message_center` 页面迁移记录完成 | 已完成 | `pages/message_center.md` |
| 11 | `message_center.js` 模板引用已删除或残留理由明确 | 已完成 | 模板直挂已删；动态过渡残留见 `pages/message_center.md` |
| 12 | 消息中心浏览器回归通过，summary 不重复轮询 | 已完成（message_center） | `npm run test:e2e:p12`，2 passed |
| 13 | `npm run typecheck` 通过 | 已完成 | 2026-06-04 通过 |
| 14 | `npm test` 通过 | 待实施 |  |
| 15 | `npm run build` 通过 | 已完成 | 2026-06-04 通过 |
| 16 | `npm run inventory:frontend` 已更新清单 | 已完成 | `docs/frontend-migration-inventory.md` |
| 17 | `tests.test_frontend_authenticated_vite_integration` 通过 | 已完成 | 2026-06-04 通过 |
| 18 | 后端全量 unittest 通过 | 待实施 |  |
| 19 | P03/P12 Playwright 回归通过 | 部分完成 | `npm run test:e2e:p12` 已覆盖 message_center |
| 20 | 所有可写测试使用 `.codex-temp` 临时数据根 | 已完成（message_center） | `.codex-temp/p12-playwright-runtime3` |
| 21 | 临时数据库 quick_check 为 ok | 待实施 |  |
| 22 | 无真实凭据、cookie、token、线上数据写入测试记录 | 已完成（当前批次） | 未记录 fixture 密码、cookie、token |
| 23 | P12 实施证据回填到本目录 | 部分完成 | `pages/message_center.md` 已回填 |

## 不得验收的情况

出现以下任一情况，P12 不得验收：

- [ ] 只新增 React island，但旧脚本仍全量执行，且没有删除清单。
- [ ] 同一按钮点击会触发两次 POST、两次 WebSocket send、两次已读、两次上传或两次保存。
- [ ] 同一页面存在两个来源维护同一 loading、selected、active、unread、chat messages、materials list、submission state。
- [ ] 迁移导致教师、学生、超管可见性或操作范围变化，但没有对应权限测试证明合理。
- [ ] 迁移导致现有业务 URL、API 字段或错误语义变化，却没有兼容层和回归说明。
- [ ] Playwright 只验证页面打开，不验证点击、输入、提交、状态可见性和控制台错误。
- [ ] 可写测试连接到远程生产站点或真实 `/lanshare/data`。
- [ ] 测试或文档泄露真实密码、cookie、token、API key。
- [ ] 使用 skip/fixme/only 或软断言掩盖核心回归失败。
- [ ] `docs/frontend-migration-inventory.md` 未更新，无法证明旧脚本引用数量是否下降。

## 推荐实施顺序

### 第 0 步：建立 P12 页面记录模板

- [ ] 新建 `target/target01/P12/pages/`。
- [ ] 为四个页面创建迁移记录文件。
- [ ] 每个记录先填迁移前基线，不写代码。

### 第 1 步：课堂页收束

- [ ] 先收 `classroom_page.js` 的职责和模板入口。
- [ ] 建页面级 classroom island。
- [ ] 把已有课堂细粒度 island 纳入页面级职责表。
- [ ] 删除或降级 `initClassroomPage` 模板调用。
- [ ] 跑课堂页 Playwright 回归。

### 第 2 步：聊天收束

- [ ] 建 `chat.js` 删除清单。
- [ ] 建 chat island。
- [ ] 验证单 WebSocket、单发送、单消息渲染。
- [ ] 删除 `chat.js` 模板引用。
- [ ] 跑聊天 Playwright 回归。

### 第 3 步：材料管理收束

- [ ] 建 `materials_manage.js` 删除清单。
- [ ] 建 materials manage island。
- [ ] 验证列表、筛选、上传、AI 导入状态、最终材料入口。
- [ ] 删除旧脚本引用。
- [ ] 跑材料管理 Playwright 回归。

### 第 4 步：消息中心收束

- [ ] 建 `message_center.js` 删除清单。
- [ ] 明确全局 summary island 与页面级 message center island 分工。
- [ ] 验证已读、私信、筛选、profile 嵌入区。
- [ ] 删除 `message_center.js` 模板引用。
- [ ] 跑消息中心 Playwright 回归。

### 第 5 步：总验收

- [ ] 跑基础前端命令。
- [ ] 跑后端兼容测试。
- [ ] 跑 P03/P12 浏览器回归。
- [ ] 更新 `docs/frontend-migration-inventory.md`。
- [ ] 回填每页迁移证据和旧脚本删除结论。

## 预期最终结果

完成 P12 第一阶段后，项目应该达到以下状态：

- `classroom_page.js`、`chat.js`、`materials_manage.js`、`message_center.js` 不再作为对应页面的主逻辑脚本被模板直接加载，或残留项有明确删除时间表。
- classroom、chat、materials manage、message center 四个页面都有页面级 island 作为权威前端入口。
- 旧脚本的事件监听、定时器、WebSocket、API 调用、全局函数有删除证据。
- 高风险旧脚本在 `docs/frontend-migration-inventory.md` 中的模板引用次数明显下降。
- P03/P12 真实浏览器回归能覆盖迁移页面的主要用户路径。
- 迁移没有改变业务 URL、后端权限、数据库结构或线上数据。
- 可写验证全部发生在 `.codex-temp` 临时数据根中，线上 `/lanshare/data` 安全无损。

## 跟踪记录

| 日期 | 执行人 | 变更 | 验证 | 状态 |
| --- | --- | --- | --- | --- |
| 2026-06-04 | Codex | 建立 P12 前端岛屿迁移与旧脚本收束目标 | `target/target01/P12/README.md` | 待实施 |
| 2026-06-04 | Codex | 建立四个页面迁移工作单；先完成 message_center 页面级 island 入口收束、旧脚本模板直挂删除、P12 e2e 样板回归 | `npm run typecheck`; `npm run build`; `python -m unittest tests.test_frontend_authenticated_vite_integration tests.test_agent_task_service`; `npm run test:e2e:p12` | message_center 样板完成，其他页面待迁移 |
