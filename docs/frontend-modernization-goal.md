# LanShare 前端现代化改造执行目标

## 总目标

在不丢失任何现有业务功能、权限边界、课堂实时能力和部署兼容性的前提下，将 LanShare 当前的 FastAPI + Jinja2 + 原生 JavaScript 前端，逐步改造为 **FastAPI/Jinja SSR 页面壳 + Vite + React + TypeScript Islands/MPA** 架构。

改造后的系统必须保持本地 Win11 开发测试顺畅，并能通过 Docker 部署到远程服务器稳定运行。最终效果要现代、美观、交互友好、移动端可用、浏览器兼容性好，同时保留现有后端、数据库、权限、模板路由和课堂业务流程的稳定性。

## 核心原则

1. **功能零遗漏是最高优先级**：任何页面或交互迁移前，必须先列出现有入口、按钮、表单、弹窗、异步请求、权限可见性、错误状态、空状态和移动端行为，迁移后逐项验收。
2. **渐进增强，不做一次性推倒重来**：保留 FastAPI 路由、Jinja 首屏渲染、服务端鉴权和现有业务 URL；React 以页面级或功能级 islands 的形式逐步接管复杂交互。
3. **生产环境不引入 Node 运行时依赖**：Node/Vite 只用于本地开发和构建；Docker 生产镜像仅服务 Python 后端与构建后的静态资源。
4. **后端契约优先**：前端改造不得绕过既有权限、组织、课堂、教师、学生、资源所有权和超级管理员边界；API 类型、表单字段和错误语义要显式管理。
5. **性能与并发安全优先**：前端拆分页面级 bundle，延迟加载重交互模块，不把课堂、作业、考试、文件上传、AI 请求等高频路径变成全局阻塞资源。
6. **设计系统统一但不牺牲业务密度**：管理、课堂、作业、考试等高频工作台要安静、清晰、可扫描；避免营销页式大卡片堆叠，优先信息层级、快捷操作和稳定布局。

## 目标架构

### 后端与模板边界

- FastAPI 继续负责认证、授权、数据库读写、文件存储、AI 调用、WebSocket/实时消息、后台任务和业务路由。
- Jinja2 继续承担页面壳、首屏关键数据、SEO/可访问基础结构、角色可见性和静态资源注入。
- 新增统一静态资源助手读取 Vite manifest，在模板中按入口加载 `static/dist` 构建产物。
- 保留现有 `window.APP_CONFIG` / 页面数据注入能力，先通过兼容层迁移，再逐步收敛为类型化 JSON payload。

### 前端工程

- 新建 Vite + React + TypeScript 前端工程，采用多入口 MPA/islands，而不是第一阶段改成全站 SPA。
- 使用 TypeScript、ESLint、Prettier、Vitest、Playwright 建立构建、类型、单元和端到端验证链路。
- 使用 TanStack Query 管理接口请求、缓存、重试和失效，避免页面脚本各自散落 `fetch` 逻辑。
- 使用 Zod 或 OpenAPI 生成/校验接口类型，保证前后端契约明确。
- 使用 Radix UI / shadcn/ui 思路构建可访问基础组件，使用 lucide-react 作为图标系统。
- 用 CSS tokens 承接现有 `static/css/ui-system.css` 的设计变量，形成统一的颜色、间距、字号、层级、焦点态、暗色/高对比度扩展基础。

### 页面迁移模型

- 每个页面保留原 Jinja 模板作为路由壳，按需挂载一个或多个 React island。
- 简单静态展示继续留在 Jinja；复杂交互、实时状态、筛选表格、上传、编辑器、AI 面板、工作台布局逐步迁入 React。
- 迁移期间允许旧 JS 与新 React 共存，但必须定义清晰的挂载边界、事件边界和清理机制，避免重复绑定事件。

## 改造范围

第一阶段必须覆盖并保护以下业务域，不允许因为前端重构造成入口消失、权限错位或流程断裂：

- 登录、注册、学生首次设置密码、登出、个人资料和基础账号设置。
- 教师端首页、课堂入口、课程/班级/学期/教材/课程开设管理。
- 课堂主页、课程信息、浮动快捷导航、课堂讨论、课堂聊天、私信入口、资源区、资料区、公告/时间线。
- 作业创建、发布、附件、提交、撤回、补交、批改、成绩、导出和学生端状态展示。
- 考试创建、试题/评分标准、AI 出题、学生作答、附件、AI 批改、成绩与回看。
- 课程资料库、文件上传下载、导入导出、Git 同步和素材浏览。
- AI 助手、AI 考试/作业相关能力、AI 调用状态、错误提示和成本/日志相关入口。
- 消息中心、通知、邮件配置、私信、反馈、博客/动态、用户弹层和黑名单相关交互。
- 系统管理、超级管理员入口、运维/健康/配置可见性，以及只允许超级管理员访问的页面。

## 推荐实施阶段

### 第 0 阶段：现状清点与保护网

- 建立页面、模板、JS、CSS、API、权限和用户流程矩阵。
- 为每个核心页面记录现有入口、DOM 挂载点、数据来源、事件绑定、异步请求、移动端行为和错误/空状态。
- 补齐高风险流程的 Playwright 冒烟测试，至少覆盖教师、学生、超级管理员三类角色。
- 输出“迁移前功能清单”，作为后续验收基线。

### 第 1 阶段：工程底座

- 引入 Vite + React + TypeScript 多入口构建。
- 新增 Docker 构建流程：安装前端依赖、执行前端 build、将产物写入 `static/dist`，生产运行时不依赖 Node。
- 在 Jinja 中接入 Vite manifest 静态资源解析，保留开发环境热更新或开发代理方案。
- 建立 `npm run build`、`npm run typecheck`、`npm run test`、Playwright 和后端测试的统一验证命令。

### 第 2 阶段：设计系统与基础组件

- 将现有视觉变量整理为 tokens：颜色、字体、间距、圆角、阴影、边框、层级、状态色、焦点环。
- 建立按钮、输入框、选择器、标签页、弹窗、抽屉、菜单、通知、上传、表格、空状态、加载态、错误态等基础组件。
- 所有组件必须具备键盘可达、焦点可见、触控友好、移动端不溢出、文本不遮挡和可访问语义。
- 保持业务型界面的信息密度，不用装饰性大卡片替代高频操作区。

### 第 3 阶段：低风险 islands 迁移

- 优先迁移反馈弹窗、通知铃铛、上传控件、资料选择器、用户弹层、局部筛选器等独立交互。
- 每迁移一个 island，都要保留旧行为验收记录，确认无重复事件绑定、无权限可见性变化、无接口字段遗漏。
- 建立旧 JS 到 React 的兼容桥，逐步减少全局脚本依赖。

### 第 4 阶段：核心工作台迁移

- 迁移教师首页与课堂主页：保留全部课堂入口、作业/考试/资料/讨论/聊天/AI/反馈/私信等路径。
- 迁移作业与考试流程：重点保护附件、提交状态、补交窗口、撤回重交、批改、AI 生成/评分和学生端回显。
- 迁移管理中心：重点保护超级管理员边界、教师可见性、批量操作、搜索筛选和配置表单。
- 迁移消息中心与个人空间：重点保护通知路由、私信、邮件配置、博客动态、用户弹层和黑名单。

### 第 5 阶段：全链路验收与发布

- 在 Win11 本地完成开发、构建、浏览器和移动端视口验证。
- 在 Docker 环境验证静态资源加载、manifest 解析、健康检查、文件上传下载、WebSocket、AI 请求和数据库路径。
- 对比迁移前功能矩阵，确认没有入口遗漏、按钮失效、权限扩大、数据丢失、状态错乱或移动端遮挡。
- 形成可回滚发布方案：前端入口按页面分批切换，出现问题可回退到旧模板/旧脚本。

## 验收标准

### 功能完整性

- 迁移页面的所有旧入口、按钮、表单字段、弹窗、快捷操作、空状态、错误状态和加载状态都必须存在或有明确升级替代。
- 教师、学生、超级管理员三类角色的可见性和可操作范围与迁移前一致，不能因为前端渲染变化暴露隐藏入口。
- 作业、考试、资料、聊天、消息、反馈、AI、文件上传下载等跨模块流程必须能从入口走到最终状态。
- 页面刷新、浏览器返回、移动端访问、弱网/接口失败、重复点击和并发上传等场景必须有稳定表现。

### 视觉与交互

- 桌面端、平板端、手机端均无横向溢出、文本遮挡、按钮挤压、层级错乱和固定元素覆盖内容。
- 高频页面优先清晰的信息结构、稳定的导航和可扫描布局；移动端优先常用操作和状态反馈。
- 所有交互控件具备 hover、focus、active、disabled、loading、error 状态。
- 图标、按钮、菜单、标签页、抽屉、弹窗和表格行为统一，避免同类操作在不同页面表现不一致。

### 性能与兼容

- 首屏不加载全站级巨型 bundle；按页面和功能拆分资源。
- 重型编辑器、图表、AI 面板、文件预览和批量操作按需加载。
- 支持现代 Chromium、Edge、Firefox、Safari，以及主流移动端浏览器。
- Docker 远程部署后，静态资源带 hash，缓存策略明确，旧版本资源不会污染新版本页面。

### 验证命令

每个阶段完成后，至少执行并记录以下验证：

```powershell
npm run build
npm run typecheck
npm run test
pytest
```

并按变更范围补充：

```powershell
python -B -m py_compile <changed-python-files>
npx playwright test
docker compose build
docker compose up -d
```

## 不做的事情

- 第一阶段不把项目整体改成 Next.js、Nuxt、纯 SPA 或独立前后端双服务部署。
- 不为了视觉统一删除现有业务入口、隐藏低频但必要的教师/学生/管理功能。
- 不把权限判断从后端迁到前端；前端只做展示约束，后端仍是最终权限边界。
- 不为了重构前端修改数据库结构，除非业务需求明确要求并经过迁移设计。
- 不引入多个 UI 框架并行使用，避免样式、交互和依赖体系长期分裂。

## 完成定义

当前目标只有在满足以下条件时才视为完成：

1. 所有核心业务页面已按功能矩阵完成迁移或明确保留原实现。
2. 所有迁移页面通过桌面端和移动端浏览器验收。
3. 教师、学生、超级管理员全角色关键路径通过端到端测试。
4. Docker 远程运行路径验证通过，包括静态资源、上传下载、WebSocket、AI 请求和健康检查。
5. 项目中不存在“迁移后入口消失但未记录”的业务功能。
6. 新架构有清晰的维护规范：如何新增页面入口、如何写 API client、如何挂载 island、如何写组件、如何验证发布。

## 当前落地基线

截至 2026-06-02，第一批前端现代化底座已经进入项目：

- 新增 `frontend/src` 作为 Vite + React + TypeScript islands 源码目录。
- 新增 `vite.config.ts`、`tsconfig.json`、`npm run build:frontend`、`npm run dev:frontend`、`npm run typecheck` 和 `npm run test`。
- FastAPI/Jinja 侧新增 `vite_entry_tags(...)`，可读取 `static/dist/manifest.json` 并为 Vite 入口输出 `modulepreload` 与 `script type="module"`。
- `templates/base.html` 与 `templates/manage/layout.html` 已接入 authenticated app-shell island，先增强顶栏菜单的关闭、互斥打开和 Escape 焦点返回。
- `templates/partials/feedback_button.html` 已接入第一个低风险业务 island：React/lucide 版反馈入口，但继续保留 `data-open-feedback` 协议和原 `feedback.js` 弹窗、附件、提交、我的反馈流程。
- 新增轻量全局入口组件层：`frontend/src/components/action-entry.tsx`，并将 `blog_button`、`profile_button`、`feedback_button` 接入 Vite/React island。旧链接、头像、标题、`aria-label` 和反馈触发协议仍保留。
- 新增 `frontend/src/islands/message-center-sync.tsx`，将全局消息中心铃铛的未读轮询、计数、toast、`aria-label` 和 `message-center:summary-updated` 事件同步迁移到 React island。旧 `static/js/message_center_bell.js` 继续作为回退，只处理未被 React 标记接管的 shell，避免双重轮询。
- 新增 `frontend/src/islands/blog-topbar-sync.tsx`，将顶栏博客今日新增计数、标题、`aria-label` 和 60 秒刷新从 `message_center_bell.js` 拆分出来。旧脚本继续作为回退，只处理未被 React 标记接管的博客入口，避免“消息中心脚本兼管博客状态”的长期耦合。
- 新增 `frontend/src/islands/student-security-sync.tsx`，将学生账号安全入口的动态触发、改密表单提交、提交中状态、toast 和弹窗关闭迁移到全局 React island。旧 `static/js/student_security.js` 继续作为回退，并在检测到 React 接管后自动让位。
- 新增 `frontend/src/islands/assignment-submit-sync.tsx`，在学生普通作业提交表单中接管“答案数量 / 附件数量 / 提交窗口 / 重交状态”的实时可视化同步。旧 `SubmissionUploadManager`、提交 API、撤回 API、补交判断和上传字段保持原业务路径，并通过 `lanshare:assignment-upload-change` / `lanshare:assignment-submit-availability-change` 事件向 React island 暴露状态，避免重写核心提交逻辑时遗漏功能。
- 新增 `frontend/src/islands/submission-jump-nav.tsx`，将提交详情页左侧批阅导航的题目分组、已答计数、逐题跳转迁移为 React island。旧内联 `renderAnswers()`、附件预览、附件删除、评分提交和 AI 重批仍保留原实现；如果 React 未接管，旧 `renderSubmissionJumpNav(...)` 仍会回退渲染，避免批阅页导航丢失。
- 新增 `frontend/src/islands/teacher-submission-workbench-sync.tsx`，在教师作业详情页接入现代化批阅工作台，集中展示待批改、未提交、待重交、提交率、平均分、及格率、AI 可批改数量和已选数量。旧筛选、表格渲染、成绩分布图、AI 批量批改、未交记 0、撤回重交、线下代交和详情页跳转继续由原页面函数托管，并通过 `lanshare:teacher-submission-workbench-change` 事件向 React island 同步状态，避免重写教师批阅主流程时遗漏入口。
- 新增 `frontend/src/islands/message-center-workspace-sync.tsx`，在个人中心通知/私信区域和独立消息中心模板接入消息工作台，集中展示未读、当前分类、联系人、黑名单、当前会话、AI 回复中、待发附件和发送冷却等状态。旧 `static/js/message_center.js` 继续托管通知列表、已读、私信会话、Markdown、表情、附件、黑名单、AI 助教轮询和 URL 同步，并通过 `lanshare:message-center-workspace-change` / `lanshare:message-center-workspace-command` 事件与 React island 双向桥接，避免拆消息中心时破坏通知和私信共享链路。
- 新增 `frontend/src/islands/classroom-workspace-nav-sync.tsx`，在课堂首页接入课堂导航工作台，集中展示当前区块、教师/学生视图、课程班级、活动计数和各区块快捷入口。旧 `templates/classroom_main_v4.html` 的 `data-workspace-nav`、`#assignment-panel`、`#materials-panel`、`#discussion-room`、课堂活动区、`initWorkspaceNav()`、`focusSection()`、`spotlightSection()`、`fileApp.init(window.APP_CONFIG)`、`materialsApp.init(window.APP_CONFIG)`、`examApp.init(window.APP_CONFIG)` 与聊天/私信初始化继续保留，并通过 `lanshare:classroom-workspace-nav-change` / `lanshare:classroom-workspace-nav-command` 事件桥接，避免课堂主页迁移时丢失原有作业、资料、讨论、资源和活动入口。
- 新增 `frontend/src/islands/assignment-task-board-sync.tsx`，在课堂首页作业/考试区接入任务主线，集中展示作业数、考试数、临近截止、补交窗口、教师待批或学生待办，并把高优先级任务作为快捷定位/打开入口。旧 `templates/classroom_main_v4.html` 的原作业卡片、考试/作业入口、状态 badge、阶段 badge、教师批改统计、学生提交状态和 `assignment_time.js` 倒计时继续保留；`static/js/classroom_page.js` 只从旧卡片与倒计时状态读取数据，通过 `lanshare:assignment-task-board-change` / `lanshare:assignment-task-board-command` 与 React island 桥接，避免任务主线现代化时替换原卡片或覆盖阶段信号。
- 新增 `frontend/src/islands/classroom-activity-workspace-sync.tsx`，在课堂首页活动侧栏接入活动工作台，集中展示当前互动/研讨/协作/资源区、实时活动总数、资源数量和刷新/定位操作。旧 `initClassroomActivitySidebar()` 继续管理四个 tab、panel 显隐、hash 同步、移动端滚动、顶栏总数和 `classroom:activity-counts` 动态计数；互动创建/刷新、研讨室实时聊天与一对一、协作创建/刷新、资源上传/刷新仍由原模块托管，并通过 `lanshare:classroom-activity-workspace-change` / `lanshare:classroom-activity-workspace-command` 事件桥接，避免活动区现代化时拆断课堂实时链路。
- 新增 `frontend/src/islands/resource-workspace-sync.tsx`，在课堂首页资源区接入资源工作台，集中展示资源总数、容量、详情覆盖、外链覆盖、下载限制和上传进度，并提供刷新、上传、定位列表操作。旧 `static/js/app_files.js` 继续托管文件列表渲染、分块上传、下载、受限下载提示、详情弹窗、教师保存/删除/AI 补全和后端权限路径；React island 只通过 `lanshare:resource-workspace-change` / `lanshare:resource-workspace-command` 事件同步状态与触发旧模块命令，避免资源区现代化时绕开课程文件权限和共享文件存储链路。
- 新增 `frontend/src/islands/material-learning-path-sync.tsx`，在课堂首页材料区接入材料学习路径工作台，联动课程进度时间轴、课程首页文档、当前课次学习文档、材料目录、README/预览覆盖、下载限制和已选材料数量。旧 `templates/classroom_main_v4.html` 的时间轴按钮、课程首页入口、学习文档入口、教师选择材料、AI 助教生成课次材料、`static/js/classroom_materials.js` 的材料加载、面包屑、目录进入、详情弹窗、批量下载和 AI 生成期末材料流程继续保留；`static/js/classroom_page.js` 只通过 `lanshare:material-learning-path-change` / `lanshare:material-learning-path-command` 将旧 DOM 状态桥接给 React island，避免材料路径现代化时漏掉课次绑定、课程首页、期末材料生成或材料权限语义。
- 新增 `frontend/src/islands/learning-progress-sync.tsx`，在课堂首页接入学习进度总览，学生侧集中展示修为、阶段进度、材料/任务/互动/证书/排名和可破境状态，教师侧集中展示学生总数、活跃、待关注、材料/任务均值和个人试炼概况。旧 `templates/classroom_main_v4.html` 的修为/班级成员弹窗、阶段节点、个人破境试炼按钮、继续试炼链接、教师成员搜索、学生详情 iframe、教务考试名单同步与签名表导出继续由 `static/js/learning_progress.js` 托管；React island 只读取 `window.APP_CONFIG.learningProgress / learningOverview` 并通过 `lanshare:learning-progress-command` 触发旧入口，避免学习进度现代化时绕开证书、阶段考试和教师成员详情权限链路。
- 新增 `frontend/src/islands/assignment-authoring-sync.tsx`，在课堂首页教师作业编辑弹窗接入发布检查面板，集中展示标题、要求、评分标准、批改模式、阶段绑定、附件类型、通知方式、时间策略和补交策略的完整度，并提供字段定位与保存操作。旧 `static/js/app_exams.js` 继续托管 `editAssignment(...)`、`saveAssignment()`、时间策略校验、迟交扣分校验、阶段字段、邮件通知字段和最终 POST/PUT API；React island 只通过 `lanshare:assignment-authoring-change` / `lanshare:assignment-authoring-command` 事件读取状态和触发旧保存按钮，避免作业发布现代化时遗漏学习阶段、迟交或通知链路。
- 新增 `frontend/src/islands/exam-assign-sync.tsx`，在课堂首页教师“从试卷库添加考试”弹窗接入考试发布检查面板，集中展示试卷选择、试卷库加载状态、阶段绑定、附件类型、通知方式、答题时间策略和补交扣分策略，并提供刷新、定位和发布操作。旧 `static/js/app_exams.js` 继续托管 `loadExamPapers()`、`confirmExamAssign()`、试卷库 API、后端评分标准校验、重复发布校验、阶段字段、附件字段、邮件通知字段、时间/补交校验和最终 `/api/exam-papers/{paper_id}/assign` 请求；React island 只通过 `lanshare:exam-assign-change` / `lanshare:exam-assign-command` 事件同步状态和触发旧发布按钮，避免考试分发现代化时漏掉课堂考试、阶段试炼、补交和通知链路。
- `templates/dashboard.html` 已接入 `dashboard-quick-actions` island，通过内联 JSON payload 增强教师/学生首页快捷入口，同时保留旧 SSR 快捷操作 HTML 和学生账号安全弹窗触发协议。
- 新增前端 API 基础层：`frontend/src/lib/api-client.ts`，为后续数据型 island 提供同源凭据、JSON/FormData 处理、Zod 响应校验和统一 `ApiError`。
- 新增统一 island 挂载工具：`frontend/src/lib/mount-react-island.tsx`。后续 React island 不允许重复手写 DOM 查询、重复挂载判定、`StrictMode` 包裹和 mounted registry 注册逻辑。
- `Dockerfile` 已新增 `frontend-builder` stage，生产镜像构建时会在容器内执行前端 build，再把 `static/css/tailwind-app.css` 与 `static/dist` 复制进最终 Python 运行镜像。
- 新增 `tools/frontend_migration_inventory.py`，用于自动生成 `docs/frontend-migration-inventory.md`，记录模板、脚本、路由线索、Vite 入口和高风险传统脚本。
- 新增 `tests/test_frontend_assets_vite.py`，覆盖 Vite manifest 解析、共享 chunk 预加载、开发服务器模式、路径穿越防护和缺失构建产物报错。
- 新增 `tests/test_frontend_authenticated_vite_integration.py`，使用 FastAPI dependency override 做 authenticated smoke test，验证 `/dashboard` 注入 authenticated islands（含学生安全入口同步），`/api/blog/summary` 与 `/api/message-center/summary` 在登录身份下仍返回前端同步所需字段，并验证学生普通作业页注入 `assignment-submit-sync` 时没有移除旧的提交、撤回和上传脚本链路；提交详情页注入 `submission-jump-nav` 时仍保留旧的答题渲染、附件预览、评分提交与 AI 重批入口；教师作业详情页注入 `teacher-submission-workbench-sync` 时仍保留旧的刷新、AI 批改、未交记 0 和撤回已选批量入口；课堂首页注入 `classroom-workspace-nav-sync`、`assignment-task-board-sync`、`classroom-activity-workspace-sync`、`resource-workspace-sync`、`material-learning-path-sync`、`learning-progress-sync`、`assignment-authoring-sync` 与 `exam-assign-sync` 时仍保留旧 `data-workspace-nav`、课堂区块、作业/考试卡片、活动 tab、活动计数、聊天、资源列表、材料列表、材料刷新、材料面包屑、学习进度弹窗、修为/成员入口、上传框、文件详情弹窗、作业弹窗、考试发布弹窗、阶段字段、迟交字段、旧保存/发布入口和文件/资料/考试初始化；个人中心通知区域注入 `message-center-workspace-sync` 时仍保留旧消息列表、已读按钮、私信表单和 Markdown 工具栏。

本地开发的基础命令：

```powershell
npm install
npm run build
npm run inventory:frontend
python -m unittest tests.test_frontend_assets_vite
python -m unittest tests.test_frontend_authenticated_vite_integration
python main.py
```

如需 Vite 开发服务器模式：

```powershell
npm run dev:frontend
$env:LANSHARE_VITE_DEV_SERVER = "http://127.0.0.1:5173"
python main.py
```

后续迁移必须继续沿用这个基线：优先新增页面级或功能级 island，旧 Jinja 页面壳、后端权限、现有 URL、旧业务流程和已验证的静态资源注入方式保持稳定。

每迁移一个新页面或组件，都必须同步更新并复查 `docs/frontend-migration-inventory.md`。如果某个旧模板、旧 JS、旧按钮或旧接口从清单中消失，必须能解释它是被新 island 完整替代，还是被明确保留在旧实现中；不能出现“为了改造而丢入口”的情况。
