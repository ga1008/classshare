# LQ 平台组件与性能改进验收记录（2026-09-27）

状态：实现、完整隔离浏览器矩阵、最终专项与生产发布验证完成；发布版本为 `20260928-001638-579ede77ac6e`。下方发布表区分已部署产品源码与随后补录的验收文档。证据按源码阶段区分，不宣称平台所有业务状态均已穷尽。

## 1. 目标与验收口径

用户要求改善旧电脑和性能一般浏览器上的液态玻璃性能，保留既有功能和使用方式，尽量维持玻璃的视觉质量。本轮随后将共享控件、动态效果与开发规范推广到平台原生 JS、Jinja、React 以及后端生成的预览 HTML。

实现依据为 [组件开发合同](lq-component-contract-2026-09-27.md)、[组件 API](lq-components.md) 和 [材质前景配对](lq-scene-ink-2026-09-26.md)。[源码审计](lq-platform-component-audit-2026-09-27.md) 与 [迁移台账](lq-migration-registry.json) 各自保留用途：前者发现入口，后者保存路由、范围、历史证据与人工状态。

源码扫描中的 `pending/unknown`、候选入口数、`data-lq-component` 命中数均不能换算为完成率。互斥模板分支、共享定义与运行时实例不是同一计量单位。显式声明只证明组件所有权意图；材质生效、原生语义、键盘与生命周期仍须实际验证。隔离浏览器场景也不能替代全部角色、数据、权限和并发业务验收。

## 2. 实现覆盖范围

下表记录本轮接入或修正的领域边界，不将整块业务标为已经穷尽验收。

| 区域 | 组件接入与保留合同 |
|---|---|
| 共用原语与呈现入口 | Jinja 宏、原生工厂、React 适配器消费共享参数及材质；显式组件声明与 `declarative.css` 防止晚加载旧样式重新覆盖控件配方。旧类保留布局与事件钩子。 |
| 领域原生控件 | `domain-controls.js` 在明确构造点接入 Button、输入、原生 select、range、checkbox、radio 和 choice；不使用全页 observer 猜测控件，不替换原节点、表单归属或业务事件。 |
| AI、会话与工作区 | 聊天、历史、捕获、工作区及命令界面的叶子操作和布局接入共享组件；保留草稿、忙碌、权限、异步结果与窗口几何的业务所有权。 |
| 课表、日程与日历 | 课表编辑、3D 展示、日程待办、学期日历、筛选和日期选择使用共享控件；课程分类色属于数据语义槽位，不能被普通 surface 背景覆盖，也不能据此豁免整个工具栏。 |
| 教案富编辑与白板 | 教案编辑器工具栏、内容控件、媒体选择、AI 面板，以及白板工具栏、面板、popover、考试白板接入共享工厂。画布文字和文档排版为 content slot；resize/rotate 为领域 handle，不改成胶囊按钮。 |
| 文件、材料与查看器 | 文件操作、预览、材料选择、阅读器和渲染壳的操作控件采用共享结构；复制用临时 textarea 保持离屏隐藏的内容槽位合同，用户文档正文保持其样式。 |
| 审批、签名与管理流程 | 审批工作流、签名管理及位置流程、过程材料弹窗、分类与文件选择使用共享控件和浮层边界；保留撤销、关闭否决、嵌套、草稿与领域状态。危险动作由 destructive 变体表达，兼容旧 ghost 类时仍保留语义颜色。 |
| 教学与管理页面 | 班级、课程、开课、学期、教材、材料、考勤、课堂、考试、作业、培养和管理模板的原生叶子及动态构造点显式声明组件；保留原条件分支、字段、动作 URL、权限和状态钩子。最后补齐课堂阶段卡、考试章节卡等实际 surface，纯布局切换容器仍为 region。 |
| 消息、反馈与搜索 | 私信、消息中心、反馈、表情、全局搜索、说明弹层及树选择表单在明确构造点接入；菜单/弹层内容面和外层 layer 分别声明，避免仅有 marker 而无实际配方。 |
| 登录、个人中心与简历 | 登录/注册、身份与状态页面、个人中心、简历及相关动态控件使用共享呈现。登录模式内部布局保持场景配色继承，字段及链接消费同一 fill/ink 对；未通过硬编码白字或放宽像素测试掩盖问题。 |
| React 页面岛与管理顶栏 | React 呈现入口、字段、工作区及命令页面岛接入共享配方；管理顶栏补齐 1024px 布局及触摸命中回归。 |
| 后端完整文档预览 | `document_render_service.py` 的独立 iframe HTML 和错误页加载现有主题 bootstrap 与 `asset_url` 正式资产；工具栏、按钮、加载面、Spinner 使用共享组件。分页纸张与高清图片保持 content slot，不改变文档像素。 |

本轮补齐教案、考核计划、教师评学、期末材料、签名申请共 5 个可信平台预览 iframe 的主题桥接。桥仍要求同源、显式 `app` 标记、无 `sandbox` 和无 `srcdoc`；没有为同步主题移除安全属性，也没有授权任意用户材料 iframe。预览默认跟随系统 `auto`，允许的父页面通过原有桥同步账户呈现偏好；没有另写主题解析器或增加预览专用数据库读取。

## 3. 性能与视觉约束

- 玻璃边界与内部控件分工：重复行、小按钮、字段不各自运行实时 `backdrop-filter`；内容保持共享透光填充、明暗边缘与光泽，浮动边界按统一能力策略采样。嵌套材质不叠加模糊宿主。
- 静止界面不增加装饰性常驻 rAF 或逐控件指针监听。主题桥仅对相关 iframe 变化重新发现，壳滚动状态及浮层重定位合并到帧；销毁时取消待处理帧与监听。
- AI 窗口手势合并几何绘制，结束时落盘并刷新末帧；保留取消、失去捕获、关闭、最大化、视口变化、销毁和 pagehide 的坐标收尾。没有用 transform 拖动改变玻璃采样边界。
- 统一动效参数支持 `off/quiet/standard/expressive`；系统 reduced-motion 优先，字段不因按钮形变抖动，禁用/忙碌控件不表现为可执行操作。浮层关闭与重入由已有可取消生命周期管理，不以动画时长代替业务完成。
- 文档预览移除私有大面积 blur 和私有旋转动画，复用共享 Spinner；页就绪后隐藏其占位旋转指示。下载禁用保留无可用下载 URL 的原合同，并采用原生 disabled 按钮。
- 文档白纸不跟随暗色主题变灰。位于白纸上的少量箭头、页码和状态占位消费共享可读表面配对；其背景不能直接沿用面向暗色画布的低透明度控件配方。390/1440 浅深场景均验证这些覆盖文字对比度至少 4.5:1；外围工具栏和 raised 预览层继续共享玻璃配方。

这些是实现约束与本次场景验证结果，不是所有旧设备上的 FPS 承诺。真实低端设备、不同 GPU/浏览器版本与极端数据量的长期性能仍需独立测量。

## 4. 已执行测试及证据

结果按独立运行记录列出，部分套件相互重叠，不能相加成唯一测试总数。此表中的主任务计数来自发布负责人本轮运行记录；子任务记录同时给出可定位的测试或产物。

| 范围 | 已确认结果 | 证据与边界 |
|---|---|---|
| JS 单元回归 | 80 个文件，688/688 通过 | 主任务 `npm test` 记录；覆盖本轮相关共享与领域模块，不等同于浏览器整页验收。 |
| LQ 后端合同 | 221/221 通过 | 主任务隔离后端运行记录 `.codex-temp/lq-platform-backend-final.log`。 |
| 认证、阅读器、居中页后端 | 45、8、6 项通过 | 主任务分别运行；保留原生提交、认证与阅读路径，数量不与其他后端套件去重。 |
| LQ 核心浏览器 | 首轮 345 项中 332 通过，13 项旧期望差异完成定向复验 | Toast 完整文件 24/24，Content 完整文件 41/41。不得把该记录写成一次完整 345/345 运行；见 `.codex-temp/lq-core-final.log`、`.codex-temp/lq-toast-final.log`、`.codex-temp/lq-content-restoration-final/`。 |
| 布局与 AI 浏览器 | 首轮 36 项中 35 通过；定位选择器歧义后 History 4/4 | 主任务记录；保留首轮失败与修正后的范围，不声称整组已重新跑 36/36。 |
| 动效与领域控件 | 17/17 通过 | 主任务 motion/domain 场景；领域控件及审批前序独立回归 15/15，后者属于早期阶段证据。 |
| React 与顶栏 | React 28/28；顶栏专项 2/2 通过 | 主任务记录；顶栏含紧凑布局与触摸命中。 |
| 显式声明与旧桥接 | Declarative 最终 3/3；过程材料桥接定向 5/5 通过 | `lq-declarative-contracts.spec.ts` 覆盖 hidden/until-found、原生表单/禁用、危险动作语义与按压，以及浅深/tinted/off 下的选中 Surface 和嵌套未选中隔离；`lq-legacy-bridges.spec.ts` 覆盖确认/取消/键盘与原节点。选中态测试等待原有边框过渡完成后断言最终颜色。 |
| 登录场景配对 | Centered 完整浏览器 27/27 通过 | `.codex-temp/lq-reviewed-centered-final/`；真实照片、浅深场景、tier B、字段/链接对比及原生提交。未放宽原像素测试。 |
| 显式模板合同审查 | SSR 52 项，guard 4 项，partial scope 12 项通过 | 前序有界审查共 68 项；含导航、居中页、报告卡、认证 fallback、管理宏，保留 curated status。 |
| 文档预览后端 | 最终 11/11 通过 | `tests/test_document_render_service.py`；缓存及惰性渲染、签名 URL/用户隔离、资源版本、禁用下载、内容槽位和错误文本转义。使用 `tools/test_backend.py` 阻断 dotenv/PostgreSQL，仅隔离环境运行。 |
| 文档预览正式 CSS 浏览器 | 7/7 通过；最终不可变资产图复验 7/7 | `lq-document-preview.spec.ts`；390/1440 浅深、8 页惰性预览、翻页/键盘/滚轮、开关/缩放/平移及边界、两级失败重试、禁用下载、可信/未标记/sandbox 主题边界、reduced/off、对比度。产物 `.codex-temp/lq-document-preview-complete/` 与 `.codex-temp/lq-document-preview-immutable/`，后者含 8 张截图。 |
| 课时统计与批改页窄屏修复 | 真实 Jinja 模板 4/4；隔离应用 8 个宽度/主题/页面组合均无横溢 | `lq-narrow-pages.spec.ts` 使用正式编译 CSS 与真实附件类型说明。隔离应用 `.codex-temp/narrow-pages-verified.json` 记录两页 × 390/1440 × 浅/深全部 overflow 为 0；该次仅覆写两个 CSS 响应为当前构建，最终不可变资产矩阵仍由主任务另验。保留全部筛选器、附件按钮、局部导航横向滚动及跨尺寸批改草稿；截图 `narrow-*-after.png`。 |
| 审批详情最终有界复验 | 完整文件 6/6 通过 | `lq-approval-workflow.spec.ts` 保留撤销、请求失败草稿、关闭否决、嵌套层和迟到响应测试，新增真实 review_url 分支原生 disclosure 键盘开合及 iframe URL 保留。产物 `.codex-temp/lq-approval-disclosure-final/`。 |
| 最终文档与窄屏边界 | 15/15 通过 | `.codex-temp/lq-last-domain-final.log`：最终正式 CSS 上预览 7、窄屏页面 4、LessonDoc 编辑器 4。编辑器浅深与 390/1440 使用真实 Jinja，工具栏/侧栏共享材质、正文保持原文档内容。 |
| 透明偏好与动态材质 | 34/34 通过 | `.codex-temp/lq-material-preferences-final.log`：材质边界 3、动态过程 7、Toast 24。`material-boundaries.css` 作为最终材质裁决，深色 raised 在透明关闭时保持不透明，子控件不新增 blur。 |
| 最终 AI 与白板边界 | 13/13 通过 | FAB/dock 5，Agent 抽屉、聊天附件卡、考试白板 8。保留原子节点、表单与操作回调，抽屉使用共享 raised；浮动入口消费 dock 同一次测量，不新建监听循环。图标变体的后续几何修正另外定向复验。 |
| FAB 与管理员最终专项 | FAB 5/5、管理页 4/4 通过 | 图标容器和 SVG 保持宽高、最小尺寸；博客/监控浅深 390/1440 保留表格全部列、操作和局部滚动，共享填充与文字/SVG 前景匹配。 |
| 全平台严格矩阵 | 16/16 通过 | 固定图 `9c124519...`：472 条 GET DOM 观察、112 个实际 GET 地址，另有 24 条认证 POST 合同引用；524 个平台 frame 与 12 个用户内容 frame。未归属/无效声明/缺浮层内容/横溢/叶子 blur/源码漂移/JS 异常/执行失败均 0；最多 5 个可见 blur 宿主。来源 [精简机器结果](lq-platform-browser-final-2026-09-27.json) 与 [运行报告](lq-platform-browser-audit-2026-09-27.md)。 |
| 编辑器最终可读性与反馈 | 4/4 通过 | 最终图 `689322a2...`：浅深 × 390/1440，在实际深色场景底色合成下验证小字、Status 五种语义、Alert 均至少 4.5:1；真实 `reportError` 保留诊断内容转义、重试动作及正文 iframe。`.codex-temp/lq-lessondoc-release.log`。该有界修正接在完整矩阵后，不将旧矩阵伪称新图全量重跑。 |
| 编辑器最终真实应用 | 4/4 通过 | 发布源码 `0043c320` 的独立快照启动于隔离端口 8300，使用正式不可变图 `689322a2...`；手机/桌面 × 明暗均通过严格结构、横溢、叶子 blur、JS 异常与源冻结门禁。实际页面截图确认保存状态及辅助字清晰，正文不变。`.codex-temp/lq-lessondoc-release-app.log` 与同名目录。 |

Content 的初始 disabled/busy 恢复用例曾以 `outerHTML` 字符串比较属性顺序。采集恢复前后差异确认属性值及节点未丢失后，改用 `isEqualNode` 比较完整结构/属性值，并显式检查全部原节点身份与顺序；只沿用既有空 style 容忍，不绕过清理合同。文档预览测试也按原平移边界计算期望值，等待分页自身过渡完成；没有修改业务几何以迎合测试。

候选矩阵在教师 390px 页面发现两处真实溢出：课时统计校历说明禁止换行导致 166px，批改页长附件格式说明撑宽移动端网格导致 125px。修复限定为前者容器换行与文本收缩、后者 `minmax(0, 1fr)` 和子项收缩；没有隐藏列或按钮、取消原导航横向滚动或缩减玻璃材料。课表编辑的课次外层只补无材质 region 契约，原有内部课程色表面与拖动定位保持不变；此项声明复核不替代完整调课写入业务验收。

另两处真实溢出为博客爬虫 390px 网格固有最小宽度，以及系统监控 390/1440px 的旧负边距。修复使用可收缩网格与正常容器边界，保留表格全部列和局部横向滚动。监控文字与 SVG 标签改为随表面配对的共享前景；博客统计、开关容器、表格及编辑卡共享 Surface，避免深色残留私有白底。AI FAB 使用现有图标按钮变体及共享 dock 占用高度，保留按钮尺寸、任务状态灯、入口行为与阅读器安全边距。

## 5. 构建与台账

本轮主任务确认的资产图 revision：

```text
689322a2ee20114e6f9b4643c3c670c4db9210e11af76917b94ffa04ca411a1e
```

LQ 原生入口及其同步依赖的正式 gzip 响应合计为 **17,771 bytes / 18,432 bytes** 预算。测量工具为 `tools/ui/measure_lq_entry.mjs --production --check`，使用实际发布 gzip sidecar，并校验来源、构建 recipe 和不可变图；该数字不是整个页面 CSS/JS 总传输量，也不含按需加载的领域功能。

最终 `npm run build`、`npm run typecheck`、`npm run lint:lq` 与体积守卫通过；最终 80 文件/688 单元测试再次通过。资源图含 460 个文件、12,280,367 bytes 原始内容；lint blocking 为 0，保留 3,279 个历史/待审提示，不把提示数当作实际运行时缺陷或已验收状态。完整矩阵使用 `9c124519...`；随后只有 LessonDoc 标签/反馈组件变更，独立和真实应用专项针对本节最终 revision 复验，早期 `1e92d229...` 等图保留其原证据范围。

截图复核发现 LessonDoc 辅助字在场景背景上实际仅为 1.623～2.313:1。最终修正用共享强前景显示小标签，保存状态消费 Status 的语义填充/前景配对，错误及警告消费 Alert；未改保存/重试/冲突/草稿流程。最终源审计来自隔离发布 checkout：512 个作者文件、198 个模板、5,329 个源码入口；[发布摘要](lq-platform-component-audit-2026-09-28-release-summary.json) 保留 pending/unknown，完整本地报告 `.codex-temp/lq-platform-release-source-audit.json`。并行的微信订阅/通知服务工作未混入本发布源码。

台账本轮针对有界审查的 sharedSources 更新最终字节哈希与证据；未通过验收的页面 status 不上调。文档预览 5 个消费者没有既有 sharedSources 范围，因此仅在 6 个对应路由条目补 notes/tests 与源哈希，不创建虚假的全页验收或修改历史 before/inventory 快照。发布前仍须核对最终资产图和源字节，后续源码变化必须重新关联证据。

## 6. 已知限制与未决记录

1. 早期课表组件回归存在一个已识别的旧基线比例期望差异，曾记录 35/36；该旧用例未纳入本轮完整复验，不标为已消除，也不改变本次实际路由矩阵的独立结果。
2. 核心和 AI 套件保留首轮旧期望/选择器差异及定向复验记录。定向复验不等于完整矩阵重跑，不能把不同源码阶段和不同运行结果合并为一次“全绿”。
   早期旧签名 fixture 的 6 个过时选择器用例未纳入通过统计；当前审批/签名相关的现行控制器合同以单独列出的作用域为准，不据此宣称旧 fixture 已修复。
3. 隔离测试使用合成数据、授权 fixture、路由模拟或临时数据库；不访问生产账号数据。它们无法证明所有真实文档、真实账号权限组合、第三方服务异常、并发提交和全部历史数据状态。
4. 全平台源码报告中的 pending/unknown 继续作为待核查入口。动态节点声明、组件 marker 和材质 recipe 命中均不能单独证明业务流程完成。
5. 生产主服务和 AI 服务健康，原生 PostgreSQL 正常；后台汇总 `background_tasks.ok=false` 在发布前已存在。发布前后均为累计失败 736、排队 28、运行 0、陈旧 0、活跃 worker 4；本轮未将这些历史任务重置或记为已修复。

## 7. 发布负责人补录区

| 项目 | 最终结果与证据 |
|---|---|
| 最终源冻结时间、提交 SHA | 发布源码 `0043c320c4c39185f1c90e1311c1691b93ba3c1d`，包含组件提交 `a1d49d1a` 与性能提交 `0351edf9`。FAB 使用已有图标变体，最终 LessonDoc 修正独立提交并验证。 |
| 最终 build / lint / typecheck / 体积与资产图复核 | 全部通过；graph `689322a2...`（完整值见第 5 节），LQ 核心 gzip 17,771 / 18,432 bytes；`.codex-temp/lq-platform-{build,typecheck,lint,size,vitest}-release.log`。 |
| 隔离应用重启与最终路由/角色/宽度/主题矩阵 | 完整矩阵 16/16 通过，0 失败/跳过，报告 `.codex-temp/lq-platform-browser-audit-final-clean/`；最终 LessonDoc 独立 4/4，真实发布源码另从 `.codex-temp/lq-platform-release-source` 隔离启动于 8300，真实应用 4/4 通过。原 8299 矩阵证据完整保留。 |
| pending/unknown 与旧 fixture 最终处置 | 源审计不自动修改人工迁移状态；760 个 unknown 源码入口包含 631 个动态 HTML 写入点，不能换算为未实现功能或组件完成率。完整实际矩阵的结构缺口为 0；历史测试限制见第 6 节。 |
| 部署授权、演练、备份/迁移门禁 | 用户明确授权部署和推送。干净 managed worktree `C:/Users/AngelWei/.codex/worktrees/lq-platform-release/lanshare` 与测试源码逐文件字节一致；只复制 Git 跟踪来源及正式构建，不含用户未跟踪文档或并行小程序改动。最终 2026-09-28 00:08 dry-run 通过，3,307 个发布文件、35.26 MB；71 个迁移来源与原生 PostgreSQL 演练报告及备份摘要匹配。最终证据 `.codex-temp/lq-release-checkout-final-parity.json`、`.codex-temp/lq-platform-deploy-release-dryrun.log`、`E:/CodexTemp/lanshare-deploy-20260928-000820/`；23:43 的旧阶段证据保留。干净目录无生产 docker.env，预检仅给出本地配置提示；实际远端从受保护配置确认 `DB_ENGINE=postgres`。最终部署暂停写入后备份，迁移要求表 177/177、索引失败 0、跳过 0。 |
| 生产健康、发布版本与资源哈希、重点业务 postflight | 实际 release `20260928-001638-579ede77ac6e`，主/AI 服务均 `ok`，`NO_RECENT_ERROR_LOGS`，`DEPLOY_DONE`。公网 manifest 与本地完整 graph `689322a2...` 一致；抽查 9 项公网不可变资产及 19 项运行镜像源码摘要全部匹配。教师 1440/light 与学生 390/dark 两个匿名入口实际浏览器 GET 验证通过：正式资产、原生共享字段/按钮、0 横溢、0 页面脚本异常，截图已复核。缓存失效仅 `"cache"`，不清 cookie/用户存储。证据 `.codex-temp/lq-platform-deploy-release-corrected.log`、`lq-platform-postflight-final.json`、`lq-platform-source-postflight-final.json`、`lq-public-postflight/result.json`；后三项均位于 `.codex-temp/`。没有在生产账号上执行提交。历史后台任务指标见第 6 节。 |
| Git 本地提交、远程分支与推送确认 | 产品源码为 `0043c320c4c39185f1c90e1311c1691b93ba3c1d`。最终验收文档在以此为父提交的独立发布分支提交，推送目标 `origin/dev`；最终提交及 `ls-remote` 确认保存于 `.codex-temp/lq-platform-git-final.json`。发布链不包含主工作目录并行产生的小程序提交 `cd921654`，不将后来的文档 SHA 当成已部署产品源码。 |
| 未决项、回滚指针与发布结论 | 本轮发布已通过上述门禁；未穷尽的业务状态及历史测试限制继续保留。改动前回滚组为 `20260928-001209`，app 镜像 `lanshare-app:rollback-20260928-001209-app`，代码 `/tmp/lanshare-deploy-backups/code-20260928-001209.tgz`，停写备份 `/tmp/lanshare-deploy-backups/db-cutover-20260928-001209.sql.gz`。最终发布前另有 `20260928-001649` 同类镜像/代码/数据库备份；两个备份组按现有策略保留，回滚须遵循原部署流程并保护 `/lanshare/data`。 |

第一次上线的资源一致性检查拒绝了服务器遗留的 `static/js/agent_user_confirmation.js`：该文件已在 Git 提交 `891cd78c` 中删除，当前模板和脚本无引用，远端字节与被删文件的 CRLF 版本一致，且已存在代码备份。经路径与 SHA-256 双重核验，仅将此文件可逆迁到 `/tmp/lanshare-deploy-backups/retired-agent_user_confirmation-20260928-001154.js`，重新执行完整部署后资源图精确匹配。未放宽摘要门禁、未清理业务数据，也未删除供已打开页面使用的旧不可变资产。差异和迁移凭据见 `.codex-temp/lq-production-asset-difference.json`、`.codex-temp/lq-retired-asset-quarantine.json`。

公网浏览器检查的初始临时脚本只按 `data-lq-component="input"` 计数字段，与登录页既有规范 `input.lq-input` 不符。核对模板与共享字段合同后更正选择器，并增加“所有可见输入均使用共享字段配方”的断言；产品代码未因此修改。最终两页检查与截图均通过。
