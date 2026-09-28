# 3D 周课表堆叠清晰度与性能修复

## 选择与原因

采用“后排稳定空白玻璃外壳、仅当前周即时构造内容”。后排保留原有透光填充、边缘、位置和纵深；不再放入表头、网格、课程、空周提示或 compactSummary。滚轮、拖拽、方向键、按钮、滑杆和程序定位均在切周调用返回前生成当前周内容，不增加请求、延迟队列或离屏 DOM 缓存。

问题来自两项策略的组合：旧控制器一次生成整个学期每周的完整 DOM，而共享 content Surface 规则禁用逐卡模糊；透明卡片后的多个完整网格随之直接透出。只恢复前卡单层模糊在普通宿主上有效，但嵌套 Surface、关闭透明度或无模糊能力时仍无法保证可读。因此不能把 blur 当作隐藏后排文字的功能条件。

已比较冻结旧实现、单前卡采样、空后排三种浏览器方案，并使用真实最终控制器再次测量。当前卡显式使用共享 raised/regular 边界，最多一个采样宿主；真实管理页嵌套规则可令其为零，后排内容依然为空。没有修改共享嵌套策略或恢复多层模糊。

## 实现合同

- 复用 `createScheduleDeck`，不新增消费者 API、组件库或后台接口。所有周的 `.cs-card` 外壳身份保持，当前周仅有一个 `.cs-card__content`；离场内容立刻卸载，避免反向切换时叠字。
- 堆栈位移、淡出、内容出现消费现有共享时长，标准为 220/180/160ms，取消原 500ms 位移和常驻 `will-change`。局部 `off/quiet/standard/expressive` 与系统减少动态效果控制堆栈；不支持 `@starting-style` 的浏览器立即显示内容。
- 非当前外壳 `aria-hidden=true`，没有隐藏的可聚焦课程。旧缩略内容持有焦点时归还舞台；外部控件焦点不抢。`setOverview` 更新和销毁后调用同样有界。
- 修复舞台 Enter 默认点击新获得焦点的“返回”按钮、导致刚打开又关闭的问题。舞台忽略来自原生按钮、链接和输入的快捷键，保留调课比较与课堂跳转的键盘行为。
- 展开视图、课程 hover/touch 预览、调课连线、权限、学期筛选和原有导航保持独立控制。没有把缩略内容按需构造扩展成业务状态重建；既有展开/hover 动效仍有原参数，不宣称本次统一了其全部时长。

## 性能证据与取舍

Chrome、本地合成 32 周 × 每周 25 节课、CPU 4 倍降速。每个场景 60 次切周，计时丢弃前 6 次；布局列包含一次同步矩形读取，不包含完整 GPU 绘制。

| 指标 | 旧实现 | 最终实现 |
|---|---:|---:|
| 舞台连接元素数 | 13,697 | 461 |
| 可见内容卡层 | 6 | 1 |
| 非当前周内容/文字 | 全部其他 31 周 | 0 |
| 普通宿主模糊数 | 0（回归状态） | 1 |
| 实际嵌套宿主模糊数 | 0 | 0，仍清晰 |
| 初始 setOverview，浅/深单次样本 | 88.4 / 79.7ms | 25.3 / 24.7ms |
| 切周回调中位数，浅/深 | 2.1 / 1.7ms | 4.7 / 4.6ms |
| 切周回调加同步布局中位数，浅/深 | 55.8 / 57.5ms | 40.4 / 40.6ms |

按需构造增加了单次 JS 回调工作，但显著减少连接 DOM、初始构造及本次同步布局总工作。四个真实实现场景合计 240 次切周，节点始终 461、仅一个周内容、25 节课程同步就绪、无页面脚本异常。没有测量 heap、真实旧电脑 FPS 或 GPU，也不把单次初始样本当作固定提升倍率。

原始实验、比较图片和最终重测：`.codex-temp/lq-deck-depth-20260928/summary.md`、`production/results.json`、`production/graph-verification.json`。浅深实际嵌套截图为 `production/light-actual-nested.png`、`production/dark-actual-nested.png`。初轮浏览器 DOM 搬移方案保留为比较证据，不冒充最终控制器性能。

## 验证记录

- 新堆栈浏览器合同 **14/14**：正式 CSS、浅深/390/1440、180 周、空后排、单 payload、同步定位、反向过渡、全部导航输入、原生子控件、焦点、刷新、空态、销毁及真实管理页嵌套宿主。后者包含浅深 × standard/off/reduced/glassoff 八组合。证据 `.codex-temp/course-schedule-stack-verified.log` 与同名目录。
- 既有五个课表浏览器完整文件 **55/56**：修复保留 standalone 共享 token 采样后两个 palette 用例已通过。剩余 density 比例用例在冻结改动前模块和最终模块中均为 `0.050053399400184384`，旧期望为 `<0.02`；未改旧测试或扩大修复范围。证据 `lq-deck-stack-existing-final.log`、`lq-deck-stack-old-baseline.log`（均在 `.codex-temp/`）。
- 相关纯单元 **5 文件/99 项**通过，覆盖 wheel、课表呈现、调课路径和连接；类型、正式构建、入口预算及源码审计通过。
- 现有真实应用 DOM 矩阵首轮 **7/8**：最后学生 1440/dark 停在登录，合成密码被写到标识符字段，未进入课表；单独重试仍失败。该项不记为通过，也不修改生产登录逻辑来迁就本轮课表测试。
- 真实消费者独立复验 **12/12**：教师课时统计、教师首页 3D、学生首页 3D × 390/1440 × 浅深；通过隔离账号的表单请求登录并验证 active session/role，读取合成库已有 16/20 周数据，**0 overview mock**。48 状态快照均为单 payload/单缩略网格/后排空、无整页溢出，下一周/上一周/展开/展开内切周/返回全部通过；0 页面异常，24 张截图已复核，源码前后摘要一致，账号锁已释放。证据 `.codex-temp/lq-stack-realapp-20260929/results.json` 与同名日志。该记录独立覆盖上述学生场景，不改写首轮登录失败。
- [全平台源码审计摘要](lq-schedule-stack-source-summary-2026-09-29.json)：512 作者文件、198 模板、5,329 源码入口；保留 pending/unknown，不自动提高页面验收等级。完整报告 `.codex-temp/lq-deck-stack-source-audit-final.json`。

最终资源图 `e83171d79889568839ba774e6981cda6f09d3e8c0e6a253e36ddbdec37ec3e0a`。控制器 SHA-256 `a447b666eb9d2e47036d3356c9c001d47aa239728cf5fb6588440497f9780d49`，样式模块 `51395fac9bb9c974d3233c6f559fd417618bd11c9eeff043c1e68c478ecfb4f3`；正式 Tailwind CSS 未改变。

## 发布记录

本次是已授权上线的液态玻璃改动的回归修复，发布产品源码为 `96d1a5c8ff3cf940e66add24fccb6cd3444ed166`，后续验收文档提交不作为已部署源码。复用干净 managed release worktree，3,297 个 Git 跟踪文件与测试目录逐文件字节相同；未复制用户未跟踪文档。原有小程序和每日文案更新已在本轮基线 `0fef34af` 中，保留其最新代码。

2026-09-29 00:44 dry-run 通过，3,319 个发布文件、35.54MB；原生 PostgreSQL 演练来源和匹配 dump 摘要门禁通过。00:48 执行停写备份及部署，release **`20260929-004805-73a353ad7c3d`**；迁移必需表 **177/177**，失败索引 0、跳过 0，主服务和 AI 服务健康，`NO_RECENT_ERROR_LOGS`、`DEPLOY_DONE`。数据目录保持受保护。

公网 manifest 与最终 `e83171d7...` 图一致；6 项正式公网资产与 6 项运行 app 镜像源码抽查全部字节匹配（包括 deck/styles 和三个消费者）。两个匿名登录入口版本头正确，失效范围仅 `Clear-Site-Data: "cache"`，未清 cookie/用户存储。后台 `background_tasks.ok=false` 发布前已存在，前后均为累计失败 736、排队 29、运行 0、陈旧 0、活跃 worker 4，未因 UI 修复将历史任务重置或记为已修复。没有在生产账号中执行业务提交。

证据均在 `.codex-temp/`：`lq-deck-stack-release-parity.json`、`lq-deck-stack-deploy-dryrun.log`、`lq-deck-stack-deploy.log`、`lq-deck-stack-health-before.json`、`lq-deck-stack-postflight.json`、`lq-deck-stack-source-postflight.json`。远端回滚镜像组为 `20260929-004815`，app 镜像 `lanshare-app:rollback-20260929-004815-app`，代码备份 `/tmp/lanshare-deploy-backups/code-20260929-004815.tgz`，停写备份 `/tmp/lanshare-deploy-backups/db-cutover-20260929-004815.sql.gz`。沿用现有保留 2 组策略，回滚须按既有流程保护运行数据。

Git 推送目标 `origin/dev`，产品提交和本验收补录分别保留；最终 SHA 与远端 `ls-remote` 一致性回执见 `.codex-temp/lq-deck-stack-git-final.json`。
