# 液态玻璃动效过程验收（2026-10-08）

本记录区分源码归属、实际动画过程、业务合同与发布。源码已声明 LQ 或终态可见，均不能证明动画过程正确。全平台清单的 pending/unknown 状态不因本轮定向测试自动升级。

## 环境与证据方法

- 使用 `.codex-temp/lq-motion-20261008-runtime` 新建 SQLite 合成库、教师/学生/管理员测试账号与合成课程；HTTP 仅监听 `127.0.0.1:8361`。启动脚本禁用 dotenv、PostgreSQL 连接与外网连接，不运行真实业务后台任务。
- 组件用例仅允许加载 manifest 当前版本的不可变 CSS/JS 资源。实页用例读取健康端点并核对精确合成数据库路径，记录资源图、实际外观、玻璃开关与系统动效偏好。
- 通过自然 `requestAnimationFrame` 采样 computed opacity、transform、scale、translate、边框与阴影；不修改生产时长，不暂停/推进动画时钟。开关过程须出现 `0.03 < opacity < 0.97` 的可见中间帧，不能以 `getAnimations()` 数量代替过程证据。
- 采样器使用单独保存的原生 rAF；生产 rAF 调度单独计数。控件与实际 3D 课表静止后再次观察，确认没有持续调度。该检查不代表旧设备 FPS 实测。
- 桌面 1440×980、触屏 390×844，浅色/深色浏览器偏好；另覆盖玻璃关闭、应用 motion=off 与系统 reduced-motion。

## 验收范围

| 范围 | 实际检查 |
|---|---|
| Button / Field | 按压中间矩阵、释放恢复；聚焦边缘过程、输入框无几何移动、中文草稿保留 |
| Modal / Sheet / Drawer / Popover | 初次进入与退出，包含原生 dialog 及 data-lq-component=surface 的 dialog |
| 共享领域适配器 | 关闭否决、快速反向取消旧清理、destroy 归原 DOM 位置、重新打开 |
| 过程材料 | force 绕过保存保护后仍完成退出，退场前内容保留，重复关闭只清理一次 |
| 旧 UI 桥接 | 真实 openModal/closeModal 初次进入、退出与快速反向；原 modal-backdrop/modal-dialog 内容和焦点保留 |
| Menu / Toast / Loading | 双向过程，繁忙保留动作名，off/reduced 停止循环装饰 |
| Details / Tabs | 自然伪元素与内容帧，连续切换不替换草稿节点 |
| 课堂 | 更多菜单、课程资料、材料、活动标签切换；常驻 blur ≤2，单浮层 ≤3 |
| 首页 | 全部事项历史、新建待办、教学评价、全局搜索、3D 课表进入/退出及静止调度 |
| 管理 | 过程表单、树选择、教务比较父子弹窗、班级学生抽屉和新增学生子窗 |
| 新建课堂 | 父子层、步骤切换、保存退出状态失败时否决关闭并保留草稿 |
| 签名板 | 真实鼠标/触摸笔画，退出期间 canvas 像素持续存在，退场完成后移除 |
| AI 工作台 | 首次打开/关闭、快速反向、模式切换、历史侧栏、迟到结果不抢焦点、草稿原节点保留 |
| 页面导航 | 真实同源链接的原生 pagereveal/view-transition old/new 快照中间帧；无支持引擎保留原生导航 |

用例：`tests/e2e/components/lq-motion-process.spec.ts`、`tests/e2e/specs/lq-motion-app.spec.ts`。旧桥接用例 `lq-legacy-bridges.spec.ts` 同步 force close 返回 Promise 的合同，仍检查 veto、父子层和唯一回调。

## 分阶段结果

中间构建 `7d2376ae6ddf3bb9fe021528266cb4aebaaf5a16ea5d5706bc40b805c65e83d2`：

- 组件 20 项首次 18 通过。两项 Field 聚焦只有即时 outline、没有边缘过程，交回共享样式修复；未降低中间帧要求。
- 实页原 20 项首次 15 通过。AI 首开两项真实失败：旧 display:none 在共享首帧准备前被清除，closed 状态读取仍为 opacity=1。窗口初始化改由 hidden 保持关闭状态后，隔离诊断捕获真实中间帧，待最终正式图回归。
- 另三项测试准备问题：教师首页默认 3D 显示需先切回列表才有教学评价入口；学生登录页异步初始化焦点需在资源稳定后填写。修正后四项定向复跑通过，未修改业务实现。
- 中间证据保留：`.codex-temp/lq-motion-process-20261008-early`、`.codex-temp/lq-motion-app-20261008-early`、`.codex-temp/lq-motion-app-20261008-fixture-followup`。这些不作为最后资源版本的完成证明。

最终正式构建为 `e335ac7d8d73a4ec2c15c45702a6a5bf48b21bba1c8febe1537739dcbeb6d18e`（466 个不可变文件）：

- 共享组件首跑 22 项，21 通过；首项桌面领域 Popover 的自然 rAF 在 20.8→252.4ms 出现 231.6ms 采样空窗，跨过完整进场。保留首跑原始失败，不改生产时长或断言阈值，独立复跑该项 1/1 通过；同项触屏在首跑已通过。合计 22 个独立场景均有正式图通过证据。
- Field 聚焦、AI 首开与旧 UI bridge 的根因修复均使用正式资源重新验证。原生 dialog、surface dialog、退出清理、快速反向、输入草稿、off/reduced 与静止调度检查保留。
- 实页首次新增 onboarding 步骤测试错误采样了不负责动效的外层布局容器。实际 owner 是 `.onboarding-step-shell`；仅修正采样选择器，保留原失败与后续复测，不降低中间帧要求。
- 实页首跑 26 项，24 通过；修正 onboarding 实际采样层后，桌面通过，触屏在错误提示保留期间被 Toast 挡住右上关闭按钮。最终按真实操作先关闭“合成保存失败”通知，再重试退出，双端 2/2 通过。没有强制点击、绕过 veto 或改变生产样式。26 个独立实页场景均有正式图通过证据，所有通过场景记录的 uncaught browser errors 为空。
- 正式课堂双端模糊宿主数实测：常驻 1，课程资料 2，课堂材料 2。共享组件静止时 rAF 计数保持 1/1；实际 3D 课表静止追加观察 450ms，桌面保持 41/41、触屏保持 55/55，没有新增生产调度。
- 原始过程证据：`.codex-temp/lq-motion-process-20261008-final/results.json`、`lq-motion-process-20261008-resample/results.json`、`lq-motion-app-20261008-final/results.json`、`lq-motion-app-20261008-onboarding-final/results.json`、`lq-motion-app-20261008-onboarding-complete/results.json`。可审阅摘要保存在 [自然帧与业务证据](lq-motion-process-evidence-2026-10-08.json)，包含帧区间、中间帧数量、最大采样间隔、焦点/草稿/像素及实际渲染偏好。
- 发布由主任务使用既有部署流程完成，本验收任务没有连接或写入真实业务库。

## 全平台审计

重新扫描 520 个作者源文件、全部 201 个 HTML 模板和 5302 个源码候选；其中 4540 pending、762 unknown 原样保留。新增两处试卷菜单条目声明已按共享 menu renderer 的 Button ghost/md 合同修正，最终 `declared-missing-canonical` 为零。201 HTML 的 Jinja 解析通过；201 模板 / 290 非压缩 JS 的领域 owner 和即时行为例外另存 [动效源码清单](lq-motion-source-inventory-2026-10-08.json)。不能用这些数字宣称所有运行时业务状态都已完成。

隔离合成环境的教师、学生、管理员 × 390/1440 × 浅/深只读 GET 巡检完成：12/12 矩阵、108 次页面观察，路由缺口、脚本错误、横向溢出、leaf blur 和检查期间源码变化均为零。结果在 `.codex-temp/lq-motion-platform-20261008/summary.json`。仅两类 unowned 为 `app-topbar-menu` / `cw-topbar-menu` 布局壳，已核对其子 NavMenu 具有共享 owner；没有用补标签掩盖真实归属。试卷页面四矩阵均正常加载，菜单条目 canonical 声明有效。

个人中心保留一项既有性能边界：扫描文档全部已渲染块时最多 8 个模糊宿主；独立合成账号实际视口内为手机 3、桌面 4，来自顶底栏与原有 profile-hero/profile-band。这不满足全平台统一常驻 ≤2 的理想预算，未放宽阈值或归为本轮新回归。追加 500ms 静止观察的生产 rAF 均无新增（末轮手机 240/240、桌面 260/260），仍存在原有头像环 `cultivation-avatar-spin` 的 9000ms CSS 循环。该边界保留在 `.codex-temp/lq-motion-platform-20261008/profile-viewport-idle.json` 和对应视口截图，不能声称全平台装饰循环已全部消除。

兼容与领域回归由独立代理执行：9 个 motion、34 个 legacy bridge、11 个 domain controls、1 个博客草稿冲突、1 个作业状态场景，共 56 个场景均有通过证据。首跑 55/56，新作业状态 HTML 夹具缺 UTF-8 导致中文定位失败，补正确字符集后该项 1/1 通过，生产源码未变。证据在 `.codex-temp/lq-motion-compat-20261008/acceptance.json`。另共享单元 178、履历浏览器函数 28、Python 合同 78 + partial-scope 12 均通过；构建源哈希校验通过，LQ gzip 17786 / 18432 字节。

上述文档与 QA 文件已冻结；运行中的合成服务保留供发布前复看，由主任务确认后停止。Git 提交、推送和服务器部署结果由主任务的发布记录填写。

## 有意保留的即时行为

截图采集取消必须立即停止屏幕流、清除敏感画布并恢复助手，不为视觉过渡延迟资源释放。原生 dialog 的 close 事件与 returnValue 仍立即发生，视觉退场及 iframe/临时 DOM 清理由原生 presence 适配器延后；业务取消不能等待装饰动画才生效。强制父销毁、页面离开、权限失效的资源清理不等同于用户确认。

## 边界

目前仅 Chromium 桌面/移动模拟环境，不能声称 Safari、Firefox 或真实低性能手机已全部验收。全平台源码审计与只读 GET 扫描负责发现入口和布局回归；每一种有数据、权限、冲突或上传状态仍需要各领域既有业务门禁，不把本轮动画场景计数冒充全平台业务完成率。
