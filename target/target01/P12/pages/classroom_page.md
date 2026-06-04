# P12 页面迁移工作单：classroom_page

## 页面与业务边界

- 页面入口：`templates/classroom_main_v4.html`
- 主要后端边界：
  - `classroom_app/routers/classroom.py` 或课堂相关 UI 路由入口。
  - `classroom_app/routers/classroom_interactions.py`
  - `classroom_app/routers/collaboration.py`
  - `classroom_app/routers/files.py`
  - `classroom_app/routers/homework.py`
  - `classroom_app/routers/materials.py`
  - `classroom_app/routers/learning.py`
- 当前旧初始化入口：
  - 模板内 module script 负责组装 `window.APP_CONFIG` 后执行 `initClassroomPage()`。
  - `initClassroomPage()` 来自 `static/js/classroom_page.js`。

## 旧脚本职责清单

- 旧脚本：`static/js/classroom_page.js`
- 相关旧脚本：
  - `static/js/tools.js`
  - `static/js/classroom_interactions.js`
  - `static/js/classroom_materials.js`
  - `static/js/classroom_private_messages.js`
  - `static/js/chat.js`
  - `static/js/behavior_tracker.js`
  - `static/js/feedback.js`
  - `static/js/ui.js`
- 当前页面已有 island：
  - `classroom-workspace-nav-sync`
  - `assignment-task-board-sync`
  - `classroom-activity-workspace-sync`
  - `resource-workspace-sync`
  - `material-learning-path-sync`
  - `learning-progress-sync`
  - `assignment-authoring-sync`
  - `exam-assign-sync`

## P12 目标状态

- 新增页面级 island：`frontend/src/islands/classroom-page.tsx`。
- 页面级 island 只负责课堂页总装配、配置注入、生命周期清理和跨模块状态协调。
- 已有功能型 islands 保持独立职责，不把全部课堂功能塞进一个巨型 React 文件。
- `classroom_main_v4.html` 保留 URL、表单字段、DOM id 和后端数据变量，避免一次性破坏旧业务流。
- `static/js/classroom_page.js` 按职责逐段迁出，迁一段删一段入口，不允许迁完后旧事件绑定继续留在页面上重复执行。

## 迁移拆分顺序

1. 页面装配：
   - 把模板内 `initClassroomPage()` 的直接调用改为页面级 island 触发。
   - 保留 `window.APP_CONFIG` 作为兼容输入，先不改后端模板变量。
2. 课堂导航与面板状态：
   - 收束 workspace nav、活动 tab、资源 tab、材料 tab 的重复状态。
   - 删除旧脚本中与已有 `classroom-workspace-nav-sync` 重复的 DOM class 切换。
3. 作业与考试入口：
   - 明确 `assignment-authoring-sync`、`assignment-task-board-sync`、`exam-assign-sync` 的数据边界。
   - 删除旧脚本中重复的卡片刷新、重复 API 请求、重复 loading 状态。
4. 材料与资源：
   - 与 `materials_manage` 页面迁移保持一致，不共享隐式全局变量。
   - `resource-workspace-sync` 与 `material-learning-path-sync` 只接收稳定 payload。
5. 活动与学习进度：
   - `classroom-activity-workspace-sync` 负责活动区状态。
   - `learning-progress-sync` 负责学习进度弹窗和摘要。
6. 删除旧入口：
   - 页面级 island 覆盖对应职责后，从模板删除 `initClassroomPage()` 的旧直调。
   - 最终删除 `static/js/classroom_page.js` 中已迁移且无引用的代码块。

## 可删除旧代码的硬性条件

- [ ] 每删除一个旧事件绑定，必须在迁移台账中写明替代 React handler。
- [ ] 每删除一个旧 API 调用，必须证明 React 路径没有造成重复请求或丢失 loading/error 状态。
- [ ] `window.APP_CONFIG` 字段若被替换，必须保留兼容层或同步更新所有旧脚本调用点。
- [ ] 不改课堂 URL。
- [ ] 不改业务表结构。
- [ ] 不清空、不修复、不迁移线上课堂、文件、作业、材料、聊天数据。

## 测试与验收

- 单元 / 集成：
  - `npm run typecheck` 通过。
  - `npm run build` 通过。
  - `python -m unittest tests.test_frontend_authenticated_vite_integration` 通过，并断言课堂页注入 `classroom-page`。
- 浏览器回归：
  - 使用 `.codex-temp` 测试数据根。
  - 教师进入课堂页，能看到课堂导航、活动、资源、材料、作业、考试入口。
  - 学生进入课堂页，不能看到教师专属创建入口。
  - 切换 tab 不产生控制台错误。
  - 打开作业弹窗、考试发布弹窗、材料列表、资源列表均正常。
  - Network 面板或 Playwright request 统计中，同一初始化接口不应被旧脚本和 island 重复请求。

