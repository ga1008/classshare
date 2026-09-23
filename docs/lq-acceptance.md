# Liquid Glass 实施验收记录

执行真源：[执行计划](liquid-glass-execution-plan-2026-09.md)。开始日期：2026-09-20。当前 **S0、S1、S2本地工程门禁已收口；S2最终623项组件浏览器、632项前端单元、112项隔离LQ后端通过**。S3已按[预审](lq-s3-preflight.md)开始八个管理页、学生成绩页与考试模块提取；S4–S8未开始。S2正式产物入口17,309/18,432 gzip字节；具体资源版本与实页证据见文末和[组件记录](lq-components.md)。负责人阶段签字、实机、Docker与生产发布未完成。**本地测试隔离事故的数据影响仍未关闭**，详见[事故记录](lq-test-isolation-incident-2026-09-20.md)；工程通过不代表历史数据已经恢复。

## 基线与工作区边界

- Git 基线：`23fd77e050a54e57b12e70b2c742a36536ae9142`，分支 `dev`。
- 开始时已有用户改动：`static/js/course_schedule_deck.js`、`tests/e2e/components/academic-schedule-deck.spec.ts`、`docs/frontend-redesign-2026-08.md`，以及执行计划/三份设计背景文档等未跟踪资料。上述既有改动未被回退。
- before 代码快照：`.codex-temp/lq-baseline-source`，来自 HEAD，并保留开始时课表 JS 的工作区改动。纯合成数据：`.codex-temp/lq-s0-synthetic`；不使用真实账号制造提交、评分、通知或模型任务。
- 初始前端基线：`npm test` **53文件 / 451测试通过**；`npm run typecheck` 通过。

## S0 实现

| 项目 | 已实现内容 | 证据与边界 |
|---|---|---|
| 内容哈希静态交付 | 完整原生CSS/ESM图快照、immutable、预gzip、发布版本缓存失效；统一模板和4座React岛入口 | 构建器不会自动删除旧图；旧页面可继续懒加载；`tests/test_static_asset_graph.py`、`static-asset-delivery.spec.ts` |
| nginx交付 | 仅哈希路径直出；app启动前发布到共享只读静态卷，旧图保留，非哈希仍经FastAPI；首次升级先导出旧Vite文件 | tracked seed helper及6项命令门禁；真实本地nginx引擎15项通过，详见 [记录](lq-static-nginx-validation.md)；无Docker实测 |
| 开课向导退役 | 教师教学域鉴权后301到课堂中心；导航、模板、JS、1135行CSS、专属helper清除 | 首次教师引导与`embedded_mode`保留；导航/匿名/学生/教师/无DB查询测试通过 |
| 安全减重 | 删除14个无消费者shadcn文件、47行死移动折叠、341行无消费者博客规则 | `dialog.tsx`保留；仍活跃的博客/共享表情样式保留原顺序 |
| centered样式抽出 | 原CSS位置改外链，保留全部背景变体和原有子模板覆盖方式 | 8个模板×1440/390；遍历全部非自定义computed属性 **0差异** |
| 评分门禁 | 小数和0分；两个版本令牌；busy锁；失败保留输入；409保留草稿、重载答卷后显式核对；AI/附件/延迟刷新共用保护 | 组件12项、真实服务浏览器3项、SQLite19项、原生PG22项通过 |
| 骨架与台账 | 路由/模板登记、增量保留人工状态；lint分级；玻璃/对比探针；教师预览路由 | 台账状态保持“未盘点”，自动发现不是业务/视觉验收；脚本未提供URL时明确未测 |
| 基线缺口收口 | 考试草稿测试补遗漏依赖；合班教务关联安全登记；multipart仅省略可空整数None避免根目录上传422；超时测试改确定性握手 | 文本清空、空串、false、0均保留；没有放宽容量/事务断言或跳过失败测试 |
| Escape焦点修复 | 聊天仅关闭自身已开面板时处理Escape；不抢外部弹层焦点，兼容聊天自身dialog与图片灯箱 | 7项组件、修为连续3次、聊天/草稿/动效5项通过（含reduced-motion） |
| Windows ACP启动收容 | 初始线程运行前原子加入Job；显式stdio继承；取消/管道失败仍完整回收；POSIX原流程保留 | 项目venv 21项、关键场景60次重复通过；使用CPython私有适配，实测3.14.3，其他Python/OS未实机验证 |

## 计划与代码核对后的修正

1. **legacy blog.css不是完全无用。** 评论时间、评论列表、筛选、编辑器上传、共享emoji仍有消费者。仅删除无消费者选择器；余下392规则/437选择器/1534声明的值、优先级、上下文及顺序保持不变，后续迁页再删。
2. **无消费者shadcn文件是14个。** 目录共15个；保留有消费者的`dialog.tsx`。按消费者图执行，未按原案计数误删。
3. **静态哈希必须覆盖依赖图。** 不同旧`?v=`不能令同一模块重复初始化；4座岛、内联ESM、动态入口和模板统一到同一图。代码图约10.7MB；登录背景/用户资料不复制。旧发布图不自动清除，清理需考虑长开页面。
4. **合班不能猜测官方课次映射。** 两张新教务关联表原先未登记，导致空表也阻断全部合班。现在空源记录可正常合班；源有官方同步/调停课引用时明确无写入拒绝；主课堂原记录纳入快照及确认hash并保持原状。此项不等于已实现有官方引用的自动迁移。
5. **S1颜色和兼容契约先纠偏。** 原案白字/base对比度不成立，旧同名glass令牌是完整颜色、不是HSL通道，教师局部样式还会截断用户配色继承。数值、首屏、CAS、迁移和消费者审计见 [lq-s1-preflight.md](lq-s1-preflight.md)；执行真源§22已记录约束，未开始S1业务改造。

## 构建与体积

| 产物 | before | S0 当前 | 变化 |
|---|---:|---:|---:|
| `tailwind-app.css` | 1,391,858 bytes | 1,348,893 bytes | -42,965 bytes（约3.09%） |
| `ui-system.src.css` | 63,880行 | 62,357行 | -1,523行 |
| centered独立CSS | 内联 | 3,554 bytes | 仅原消费者按需加载，替代同量内联 |

`npm run build` 包含 Tailwind、Vite、静态依赖图；发布镜像的 frontend-deps 因直接依赖 `es-module-lexer` 变化，需遵守现有 dependency-images 重建门禁。

## 自动化记录（随最终回归补齐）

| 入口 | 当前结果 | 日志 / 测试 |
|---|---|---|
| 初始 `npm test` | 53套 / 451通过 | `.codex-temp/lq-baseline-vitest.log` |
| 初始 `npm run typecheck` | 通过 | `.codex-temp/lq-baseline-typecheck.log` |
| 最终 `npm test` / typecheck | 53套 / 451通过；类型通过 | `.codex-temp/lq-s0-vitest-closure.log` / `lq-s0-typecheck-closure.log` |
| `npm run build` / `build:assets` | 完整构建通过；最终原生JS修改后资源图再构建通过 | `.codex-temp/lq-s0-build-closure.log` / `lq-s0-assets-closure.log`；最终图 `373c9bae7347989332db785a48ba5413925e86dc28ec99e85b262d34b6561a4a`（337文件） |
| `npm run lint:lq` | 0阻断；3737未迁页告警，0迁移中文件 | `.codex-temp/lq-s0-lint-final.log`；不能据此宣称全站符合新规范 |
| preview/inventory/lint边界测试 | 7通过 | `tests/test_lq_foundation.py` |
| 玻璃/对比探针fixture | 3通过 | `node --test tests/ui/test_runtime_audits.cjs` |
| 考试草稿版本 | 5通过 | `node tests/frontend/exam_draft_version.test.cjs`；HEAD原有4过/1漏依赖错误 |
| 评分控制器 | 12通过 | `tests/e2e/components/submission-grading.spec.ts` |
| 评分真实浏览器 | 3通过 | `.codex-temp/lq-s0-grade-real-service-final.log`；双标签CAS、改评分标准、越权 |
| 评分SQLite / 原生PG | 19通过 / 22通过 | `test_mp_grade_safety`；PG证据 `.codex-temp/lq-s0-pg-20260920-152349/grade-safety.log`；全新隔离簇已停止、55487无监听 |
| 导航与说明模板 | 24通过 | `test_manage_nav_service` / `test_ui_explanation_system` |
| 教师壳真实应用 | 5通过 | `.codex-temp/lq-s0-teacher.playwright.config.ts`（8157合成库） |
| 成员工作区 | 5通过 | `tests/e2e/classroom-members.config.ts` |
| 合班闭环 | 39通过 | `.codex-temp/lq-s0-merge-closure.log` |
| 材料上传/平台请求 | 45通过 | `.codex-temp/lq-s0-multipart-closure.log` |
| 最终评分/合班/上传组合 | 51通过 / 22PG环境跳过 | `.codex-temp/lq-s0-grade-audit-changes-final.log`；其22项另在原生PG全部执行通过 |
| 静态交付/模板/缓存/发布门禁 | 91通过 | `.codex-temp/lq-static-delivery-report.md`含命令与日志；缓存浏览器连续3次通过 |
| 平台容量握手测试模块 | 19通过 | `.codex-temp/lq-s0-platform-capacity-final.log`；实际线程结束前占槽，取消/超时不提前释放 |
| 初轮全Python（venv） | 3436项；10失败/17错误/206跳过 | `.codex-temp/lq-s0-python-suite.log`；包含本次fixture更新及已独立复现的HEAD缺口 |
| 中间全Python（venv） | 3457项；3失败/206跳过 | `.codex-temp/lq-s0-python-venv-final.log`；3失败均为同一个旧20ms时序测试，已用握手修复 |
| ACP修复前全Python（venv） | 3463项；3失败/206跳过 | `.codex-temp/lq-s0-python-closure.log`；历史失败保留 |
| ACP修复后全Python（venv） | 3471项；1失败/206跳过 | `.codex-temp/lq-s0-python-atomic-job-final.log`；原3项通过，新增暴露抓取超时504/502差异，历史记录保留 |
| 诊断增强后全Python（venv） | 3471项；通过/206条件跳过（324.8秒） | `.codex-temp/lq-s0-python-deadline-diagnostic-final.log`；本轮早于绝对截止watchdog改进，不用它掩盖历史偶发502 |
| S0最终全Python（venv） | **3476项；通过/206条件跳过（213.369秒）** | `.codex-temp/lq-s0-python-watchdog-final.log`；含Windows创建时收容与绝对截止watchdog最终代码 |
| 抓取绝对截止门禁 | 16项通过；连续50轮共800项通过 | `.codex-temp/lq-s0-public-fetch-fix-report.md`；确定性提前唤醒、取消、部分响应、远端提前断开和重定向总预算 |
| 默认e2e | 最终完整入口83通过/10原有条件跳过（15.6分钟） | `.codex-temp/lq-s0-p03-closure.log` / `lq-s0-p03-closure-results.json`；空库合成P03+本地mockAI，所有外部网络禁止 |
| 额外真实业务10场景 | 首轮7通过；修正旧控件2项通过；限制下载1项通过 | `.codex-temp/lq-static-delivery-report.md`；保留保存payload/version递增/读回、权限与ZIP断言，不冒称一次全套通过 |
| UI-v3 | 最终完整入口37通过（7.5分钟） | `.codex-temp/lq-s0-ui-v3-closure.log`；新建合成库完整执行，包含身份/配色、材料、草稿、监听器与动效 |
| 全组件 | 最终完整入口129通过（1.8分钟） | `.codex-temp/lq-s0-components-closure-all.log`；保留早期失败记录，不再依赖定点结果拼接 |
| nginx引擎 | 15通过，配置`-t`通过 | `.codex-temp/lq-nginx-probe-20260920-final/report.json`；实际规则/字节/gzip/缓存/回源/旧Vite保留，非Docker验收 |
| Windows ACP定向 | venv 21通过、关键重复60通过、原生解释器补充21通过 | `.codex-temp/lq-s0-acp-fix-report.md`及其中三份日志；原有13项断言未改 |

## 页面证据与未覆盖项

- 有效 before/after **各84张页面 + 各10张材料/AI浮窗/白板状态**，包含师生首页/课堂/资料/消息/博客、管理页、作业/评分、成长/简历、材料库与五个独立编辑器。每页1440×900与390×844；仅适用角色采集，权限边界由测试验证。AI只打开面板，没有调用真实模型。
- **撤回早期缺构建产物的before结论。** Git archive不含ignored Vite dist，早期 `before` / `before-aligned` / `before-settled` 与8170基线只有SSR骨架，不能证明岛屿视觉或交互等价。已在冻结源码目录独立运行Vite build、重启before服务，使用 `before-built` 与 `before-overlays-final` 为唯一有效before；采集器已加“有岛屿却无Vite入口即失败”的守卫。
- 有效证据在 `.codex-temp/lq-audit/s0/{before-built,after-settled,before-overlays-final,after-overlays}/capture.json`；对照 `comparison-built.json` / `comparison-overlays.json`：0导航失败、0 JS错误、0新增页面横向溢出、0缺页。页面文本差异主要为向导导航退役及动态在线状态/合成材料元数据，**并非像素差异为零的声明**；未设置/放宽像素阈值。
- 最后Escape修复后额外采集8张课堂/试卷/批改视图（`after-focus`），师生课堂全部岛屿实际mounted。按每条记录保留来源合成 `after-consolidated.json`，最终 `comparison-final.json` 覆盖94对视图，仍为0运行/路由回归、0新增页面横向溢出；它不是单次同时拍摄或全页像素验收。
- **撤回早期exam-take两视口的答题页覆盖结论。** 原合成数据状态错误且题目shape不正确，实际拍到“该考试尚未发布”的200提示页。合成fixture已改为published及真实pages/questions形态；新采集守卫检查题干、输入框、可用交卷按钮并拒绝“操作结果”页。有效before改为 `before-exam-valid`，after改为 `after-exam-valid`，两尺寸共4张原图经人工复核及逐像素比较相同；94对最终报告已用新证据替换，旧证据保留。此处验证可见答题界面，未执行交卷/自动保存业务链。
- 已有手机横向溢出：课表557px、批改482px、评学编辑器396px（viewport390）；before/after一致。分别属于S6/S5/S6，**不宣称这些页面已完成响应式验收**。
- 已人工检查教师登录手机、评分手机、课堂中心桌面，以及完整构建下教师课堂与试卷管理的前后截图；居中样式全属性等价另见 `.codex-temp/lq-centered-parity/report.json`。
- 新增16组页面族人工抽查及真实答题页2组补证，来源逐项见 `.codex-temp/lq-s0-visual-family-review.md`。博客、消息、材料库/阅读、简历、独立编辑器、白板和AI浮窗未发现新增布局缺失；确认手机试卷/考核方案/评学编辑器原有标题竖排与操作区挤压，列入S6而不宣称其已符合新响应式要求。
- 教师登录玻璃基线为2宿主。对比度中图像/渐变/滤镜背景被探针标为未测；S1需补像素采样与全背景池测试。S0不宣称达到新材质预算/对比度。
- 白板已采集打开状态、AI已采集双角色浮窗；完整绘图/AI流式/所有弹层状态及六套偏好、真实设备、WebKit、200%缩放均未完成S1–S6验收；后续按页面施工单补齐。
- 台账覆盖135模板、151路由/模板条目，134旧模板绑定冻结源码SHA，40个路由/模板条目绑定before截图；修正考试截图后，移除status模板的错误关联，故原41减为40。新增preview明确无before。8个AST未解析模板中，全局错误页和材料回程装饰器已人工说明归属，其余6个无名称引用者保留至S7审计。向导独立登记退役记录；身份变化导致的旧盘点项归入superseded，不能冒充文件删除。台账仍为初稿，不把自动发现升级为全站验收。

## 剩余门禁与已知环境缺口

- **S0-B01已关闭：Windows ACP启动收容竞态已修复并通过最终完整回归。** 原只读观测证实启动后AssignJob过晚，venv launcher已经生成不属于Job的真实解释器（`lq-s0-acp-containment-audit.log`）。新Windows helper通过创建时JOB_LIST收容，同时限制stdio继承；21项venv测试、60次关键场景复跑及最终3476项完整入口覆盖后代、二次取消、管道失败、IOCP退出回调顺序与句柄。Windows CPython 3.14.3为实测环境，其他版本仍按升级门禁复验。
- **S0-B02工程门禁已关闭：抓取中止改为绝对截止watchdog。** 全套及整类重复曾观察到150ms测试返回502而非504；诊断证实请求头完整、已开始响应，底层Windows10053。确定性提前唤醒场景证明旧Timer在deadline前abort会被归为502；现每次唤醒重算剩余预算、到期才标记中止原因，保留总预算、SSRF和远端早断502语义。16项定向、50轮800项及最终3476项完整入口通过。记录 `lq-s0-public-fetch-fix-report.md` 及 `lq-s0-public-fetch-early-wake-repro.py`；历史10053与提前唤醒的直接因果仍未追溯证明，不把所有10053改成超时。
- 最后全Python另两项失败已定点复核：LibreOffice容量2的测试实际接纳/拒绝数量正确，但高并发启动下没有观察到两子进程重叠（maximum=1）；原断言未改，安静重跑通过，证据 `lq-s0-lo-capacity-recheck.log`。静态升级门禁测试装载后脚本正被收口到显式entrypoint，存在混合版本运行窗口；最终源码6项全部通过，证据 `lq-s0-static-seed-closure.log`。原全套失败结果保留，**不重标为全绿**。
- 全量206项条件跳过中，22项评分PG门禁已另起真实本地PG全部执行；其余按各测试环境条件保留。S0没有schema变更，不冒称已完成S1的偏好SQLite/PG迁移。
- 系统Python全套与人为拼接venv site-packages的诊断轮不是规范验收：分别出现等待未结束/路径冲突，已停止；项目venv的完整结果以上表为准。没有据这些环境实验修改产品行为。
- 修为Escape焦点问题已闭环：完整HEAD与修复前current都有抢焦点证据；最终图下7项组件、修为连续3次及聊天/草稿/动效5项均通过，无跳过。修改只约束聊天自身Escape处理，没有提前替换S2全局弹层层级。

## 回退与发布边界

S0尚未迁移lq页面，不引入数据库schema变更。回退应整体恢复HTML、原生JS、构建产物、资产helper及nginx/Compose配置；不能只换模板。发布卷保留旧原生图与Vite哈希文件，构建/发布发生资源缺失或immutable冲突时中止。新资源与本地nginx引擎已经验证；实际Docker交付和生产健康/版本一致性尚未执行，因此当前**不能宣布具备生产发布条件**。

S0本地工程出口已满足，继续用户已授权的S1实施。执行计划的负责人正式签字仍独立保留；Docker实测、生产发布和真实设备验证没有被本地结果替代。

上一轮及本轮before/after/UI/P03/mock服务、nginx探针、PG簇均已停止，证据保留；本轮8152/8156/8157/8168/8169监听为空。没有提交、推送或部署。用户原有三个已跟踪文件的改动未被覆盖；课表JS与开始时冻结副本SHA一致。完整回归时154个改动/未跟踪文件已保留只读副本和SHA清单于 `.codex-temp/lq-s0-regression-checkpoint`（后续watchdog修复不在该历史快照内）。

## S1 实施与阶段证据

S0最终watchdog两文件已另存于 checkpoint 的 `final-delta/manifest.json`；重建S0测试源码时先应用原清单，再应用此增量。S1从该本地工程结果继续；此节记录中的定向结果不等于S1出口已经全部通过。

| 工作包 | 已实现与验证 | 未完成出口 |
|---|---|---|
| 师生偏好/迁移 | 三字段部分更新、严格输入、身份HMAC、整行CAS、请求内身份缓存、nullable增列；55项定向、完整3513项及实站6项通过 | 发布迁移按正式环境复验 |
| 原生PG | PostgreSQL16.14全新独占隔离簇，7项全部通过（0.886s）：旧表/幂等迁移、只读默认、角色隔离与旧客户端SQL、并发首次插入/同字段/不同字段、事务回滚 | 发布环境启动迁移另走正式发布门禁 |
| 令牌/材质 | 六套亮暗配色、763 typed tokens、旧别名兼容、四材质与系统降级；222 computed、最终88旧表面检查及新150配色检查通过 | 全站页族迁移和实机另验收 |
| 主题首屏与控制器 | 八类文档根统一bootstrap、媒体只改显示、三字段意图和冲突隔离、显式应用iframe桥；完整前端480项、实站6项、iframe5项通过 | 用户内容/打印iframe不在主题桥范围；不是PDF导出验收 |
| 像素探针 | 原computed探针保留未测边界；新增显式纯文本标记的真实像素探针，5项工具测试通过 | 真正业务表单/占位文字/错误/交互仍按页面族补 |
| K17背景材质 | 初始6/6失败，最低1.7572；增强后正式CSS下316图×亮暗×1440/390共1264组通过，最低5.8289；最终CSS规则关联已核验 | S4真实登录表单/加载与设备业务另验收 |

证据：`.codex-temp/lq-s1-backend-integrated.log`、`lq-s1-backend-implementation-report.md`；PG日志 `lq-s1-pg-20260920/ui-preferences.log`，簇已停止，端口55488已释放。K17保留初次失败 `lq-s1-login-default-sample.json` 和完整结果 `lq-s1-login-enhanced-full.json`，报告含源CSS/manifest/图片SHA、每段文本的最差像素位置和色值。

K17采用真实浏览器的图片cover裁剪、Clear填充、高光、scrim、backdrop合成；临时隐藏文字笔画而保留背景，取每行文字矩形全部像素的最低对比度。该保守边界不把字体抗锯齿边缘当作文本颜色，也不声称覆盖未标记的表单占位、遮挡、字体变化或其他响应式尺寸。原manifest316图未删改。

针对实证的材质修正：Clear专属高光改用低alpha令牌，常规/厚玻璃高光保持；新增 `--ls-scrim-login` 用于卡片区域局部增强，避免把整个背景图压暗。S1验证材质契约，S4才将其接入登录页面的加载成功/失败和设备降级业务链。

后续集成记录：

- 完整前端 `npm test` **54文件/475项通过**（`lq-s1-vitest.log`），类型检查通过。令牌导出已生成 `docs/lq-tokens.json`，762个typed tokens；CSS继续为唯一真源。
- 实站S1独立入口 **6/6通过（1.4分钟）**，日志 `lq-s1-e2e-isolated.log`；覆盖师生三控件保存和旧palette-only更新、八类文档根无JS深色、auto首帧与媒体变化零PATCH、动态legacy/pseudo/dialog全站off、纯本地preview和390面板边界。
- 首轮实站6项曾有2项因登录后回到登录页而失败，日志保留 `lq-s1-e2e-initial.log`。现场存在多个诊断浏览器使用同一合成账号，现有`save_user_session`按身份单行更新会话；停止并发诊断后完整入口一次6项通过。后续有账号登录的验证串行，不能把只读页面诊断理解为登录态无副作用。
- 真实app iframe桥5项通过，`lq-audit/s1/iframe-bridge/report.json`；迟载入、已载入和隐藏加载同步，未标记教案/考核打印frame保持颜色和文字；前中后偏好完全相同、PATCH和其他非登录mutation为0。桥功能与表面视觉分开验证；白底浅字的后续修正和最终实图见下文。
- 首轮浅色84视图采集无页面错误；`lq-s1-default-visual-review.md`实际复核30张原图。教师引导层遮挡的初始首页/profile不能作有效上半对照，最终80图已补齐无遮挡证据。批改与评学新增溢出由字体导致，恢复旧字体后已复核为原S0宽度。曾报告的预览标题缺失经原图和computed核查确认误判，已撤回，未以此改CSS。
- 规范入口现对新lq基础文件直接执行阻断检查，不再等页面迁移才检查；当前12个基础文件0阻断，3736项旧页告警保留，不能冒称全站零告警。

### S1 测试启动隔离事故（2026-09-20，未关闭）

root误用未隔离的 `python -m unittest discover -s tests -p 'test_*.py'`，使配置读取本地 `.env`，实际连接 `127.0.0.1:5432/lanshare`。本轮3500项、9失败、739错误、202跳过（`lq-s1-python-full.log`）不计入通过记录。`tests/__init__.py` 原说明把该 discovery 命令当作会先执行 package bootstrap，实际不能保证；这是启动操作及原测试隔离设计的缺口。

只读审查已确认当前本地 `scheduled_tasks` 唯一行是 `unit_test_kind / unit:1`，对应该模块最后一个测试。其每次setUp执行无WHERE的DELETE并commit，因此已确认测试清空该表并写入fixture；运行前任务行数和内容未知，不能宣称无损。其他提交范围仍在逐项审计。没有连接远端生产库，没有执行恢复、自动清理或覆盖。

已仅新增现场完整dump：`.codex-temp/lq-s1-test-isolation-incident/loopback-lanshare-incident-20260920-173459.dump`，5,130,299 bytes，SHA256 `a04dbcf2b8f13a4854acaad7fa57cac39b77a664381579923f294b624bb8e502`；pg_dump和离线完整解析退出0。现存9/10两份旧dump已证实来自远端，不能直接充当本地事前回滚点。详细取证继续记录到 `lq-s1-test-isolation-incident-audit.md`。

新增规范入口 `tools/test_backend.py`：在任何应用/测试导入前关闭dotenv、清除继承的数据库配置/集成探针开关、使用全新临时data root，并阻止psycopg直接和连接池建立真实连接；部署preflight同步使用它。首轮安全全套3507项出现33缺表错误和1项间接模板include断言失败（213跳过），日志 `lq-s1-python-isolated.log`。缺表暴露旧测试复用默认库及schema缓存跨临时库的问题，现已通过独占fixture修复；没有改产品schema来掩盖测试隔离问题。

### S1 最终视觉回归进展

最终集成前端55文件/480项通过，类型通过，lint12基础文件零阻断；3739旧页告警另记。正式图 `1ffebba0c592b6b10987a36aae3a0a813afad848055a374116e5e7d56ac5d60f` 已包含763个typed tokens和profile canvas文字主题更新。

暗色rose/off实站复拍80视图，原白卡浅字缺陷已经针对性复核；教师实际审图23张（11族双尺寸+考试桌面），草稿badge最终构建及定点实图亦已通过。手机批改482、课表557、评学396的原S0宽度保持，没有将旧溢出记为本阶段已修复。学生简历编辑器、考试和课堂手机/桌面实际图中文字恢复可读，应用外观修复不修改打印纸、用户画作或数据色。

### 隔离证据补充纠偏

- 当前venv的旧版python-dotenv不识别 `PYTHON_DOTENV_DISABLED`。早期安全runner已用强制engine/URL/临时路径和driver guard保护主库，但打印的 `dotenv:false` 不足以证明没有读取.env。现改用 `tools/isolated_environment.py` 在应用导入前封闭两个load_dotenv入口，runner及4个合成harness共用；5项回归含真实fake.env子进程、两别名、4个首次应用导入边界及MP_PHASE1原生探针开关剥离，全部通过。
- S0历史完整命令实际是 `-s tests -t .`，确实先引导tests包强制SQLite，但没有显式全新data root。当前.env无三项路径覆盖，默认SQLite文件的16:36:14写入时间落在S0运行窗口。因此保留3476/206的历史通过结果，**撤回该全套已证明fresh数据隔离的表述**；文件时间戳不能量化表或内容影响。后续完整新入口结果应替代它作为隔离验收，不改写原日志。
- 修正8模块fixture后的历史全套3510项仍有11个认证模板集成错误（`lq-s1-python-isolated-final.log`），原因是原测试依赖默认库已有教师/课程。现已改成明确合成业务链，11项定向及下述3513项完整入口通过；没有把空库缺数据改成skip。
- 最新实站主题6/6通过（`lq-s1-e2e-final.log`）；组件完整129/129通过（`lq-s1-components.log`）。UI-v3首轮35通过/2失败，失败为折叠外观面板下的旧定位假设；补显式展开步骤后，2项定向均通过（29.6秒，`lq-s1-ui-v3-fixed-controls.log`）。这是35项首轮加2项复验，不冒称一次完整37项通过；历史失败报告保留。

### S1 完整隔离回归与弹层末轮复核

规范入口 `venv/Scripts/python.exe tools/test_backend.py` 最终完整运行 **3513项通过、202项条件跳过，214.941秒，退出0**，日志 `.codex-temp/lq-s1-python-isolated-guarded-final.log`。启动证据为 `dotenv_loaders: blocked_before_app_import`、独占临时目录 `E:\CodexTemp\lanshare-unit-ej7bpfu2`、`engine: sqlite`、`postgres_connections: forbidden`。这轮包含独占fixture和全部11项认证模板集成，没有把缺数据改成跳过。条件跳过不等于相应集成场景已执行；S1原生PG7项的证据仍另列。

最终浅色师生共80视图对照S0，0新增页面横向溢出；`.codex-temp/lq-audit/s1/default-geometry-comparison.json`记录3处整页高度差异及fixture状态差异。人工原图复核见 `lq-s1-default-final-review.md`；随机人生一言背景没有固定场景证据，不能宣称整页像素完全相同。

正式图 `c71484846c0caee22feb5a5973d886cae0748bf55abd66a7c01b03ca7ae338c8` 下，简历信任提示、成绩页顶栏与考试草稿徽标定点截图已通过人工复核，见 `lq-audit/s1/{surfaces-final,badge-canonical}`。随后 `overlays-dark-final` 发现资料阅读器和AI浮窗固定浅底继承浅字，作为失败证据保留。最终仅增加50行局部色对，修复范围限定应用chrome和应用生成Markdown的默认前景；没有整体重涂白板画布、用户HTML/PDF/iframe或数据色。

**S1 最终本地工程出口（2026-09-20）：**

- `npm run build` 成功，正式图 `de399608a5e36b35517dfc1616d6ea14225270c55cd430d5081480fca93288ed`，344文件、10,854,274 bytes；CSS SHA256 `6837e5137f27236a8e958546ff289531e273486c2919b7838f29872dae2e3277`。日志 `lq-s1-build-overlay-final.log`。
- 正式CSS下88项旧页面computed检查通过，最低4.8285689；新增阅读/AI六配色150项最低6.6290165。纸张、显式作者样式与浅色原样检查通过，日志 `lq-s1-dark-compat-overlay-final.log`。这不等于全部业务内容逐像素对比完成。
- `lq-audit/s1/overlays-corrected/capture.json` 师生资料阅读、AI面板和教师白板10张，0页面失败/JS错误；root实际检查全部10张原PNG。阅读标题/目录/正文及AI标题/说明/输入恢复可读；白板网格、工具栏与78%背景遮盖保持。只打开AI面板，未调用模型；未把此项记成流式任务或绘图业务验收。
- `lq-audit/s1/preference-matrix-initial/report.json` 为真实`/dev/lq`八组媒体/偏好×双尺寸16张；root审12张、独立审阅4张，全部原图已查。0页面失败、0横溢出、0非登录写请求，账户偏好前后完整相等。覆盖亮暗`tinted/off`、增强对比亮暗、强制颜色亮暗；仅浏览器仿真。
- 最终CSS与canonical规则对照见 `lq-s1-k17-css-readonly-final.json`：删1加13，未变规则的声明/顺序一致，差异仅固定范围且不匹配K17、222 computed与preview试件。preview额外CSS/JS亦与截图冻结图同SHA；因此承接既有1264组K17、222 computed、16张预览证据，**不虚称再次运行全池**。独立判读见 `lq-s1-exit-review.md`。
- 最后lint仍为12基础文件0阻断、3739未迁页告警（`lq-s1-lint-overlay-final.log`）；前端480项/类型、组件129项、实站主题6项及UI-v3的35+2结果见上文。最终局部CSS不涉及这些JS控制器，按受影响范围补computed与实际截图，未重复无关完整套件。

按上述本地证据进入S2；全局阶段勾选仍保留正式签字语义。S1提供兼容材质和可读性基础，不把旧页面局部亮底、原有移动溢出、未迁移业务控件宣称为最终Liquid Glass改版。隔离事故、实机与正式发布事项继续独立记录。

### S2 数据组件与离开保护集成（2026-09-20 21:00）

- Table/Pager/BulkBar/ResultCount、Combobox/Listbox、DirtyGuard加入统一lazy入口及纯宏dispatcher。统一CSS SHA256 `da9fcbd9e3c957f034e42b9191ed8b279683979f51eb5bb98edd8fe2fe472b70` 下，移除fixture的额外组件CSS后完整组合 **69/69浏览器、1.6分钟**：Table26 + Selection31 + DirtyGuard12。日志`lq-s2-data-compiled-browser.log`。Table先前定向结果现在由这次完整26项补齐。
- 96项隔离LQ后端再次通过（1.656秒），类型检查通过；新的dev数据模板已通过8项foundation。最近完整前端Vitest为20:32的67文件600项；仍待S2全部新包冻结后的最后整体运行。
- 阶段生产图`80a21b4ae2ccfd7a7e3afb6df58ca9a16e1292099fcee8fdccd71ca813f0ef10`共391文件、11,278,146字节；源码/recipe与gzip内容验证通过，11个eager模块 **18,015/18,432 gzip字节**。它是后续Clock等源码变更前的快照，不是最终S2冻结图；`lq-s2-size-data-guard.log`。
- 隔离真实应用 `/dev/lq` 桌面1440与手机390 **2/2通过、43.2秒**。新增表格全选/禁用项保留、选择器真实值与reset、DirtyGuard子确认取消保值/批准退出都经过实际入口；亮暗四次axe无serious/critical、整页零横溢、偏好前后相等，除登录外0写请求/0页面错误。日志`lq-s2-actual-data-guard.log`。
- 四张实际长图已更新，root实读其新增内容的3200px尾段：亮暗1440与390的记录卡片、局部横滚矩阵、选值控件、状态色和提示没有整页溢出。尾段为`lq-s2-app-preview/preview-{light,dark}-{1440,390}-data-tail.png`；未重新宣称对所有旧内容逐像素检查。
- DirtyGuard无Navigation API的旧浏览器保留原生beforeunload安全退化，不拦截/重发submit事件，但不保证没有浏览器自带二次提醒。此限制及原生GET/POST/sourceElement依据见`lq-s2-dirty-guard-report.md`。未迁移实际编辑器脏保护。

Clock/Job/QuestionNavigator正在最终包验收，导航壳/EditorShell与Upload并行实现，AvatarStack/Insight、Split/Viewer及内容余项仍待完成；S2与正式阶段复选框保持未完成。本地数据库隔离事故的数据影响仍独立未关闭。

### S2 业务呈现、工作区与壳集成（2026-09-20 21:45）

- 正式CSS `E57FCF43…` 下Upload29+Workspace44完整73项、Shells38和Insights20均通过；Clock/Job/QuestionNavigator 34相关单元与39个唯一浏览器门禁通过（最后clock变更采用定向复验，准确边界见组件文档）。iframe、原生表单、服务器确认与代次约束见各包报告。
- `/dev/lq?layout=`七种显式白名单预览共 **14/14实际页面用例，3.2分钟**，覆盖1440/390与各自明暗截图、无新增页面横溢、28次axe无serious/critical、偏好前后相等、除登录外零写请求/零页面错误。所有入口共用原预览开关和有效教师身份验证，响应no-store。日志`lq-s2-shell-refinement-actual.log`；28张原PNG已逐张审阅，见`lq-s2-shell-preview-visual-review.md`。
- 实页检查修正了Jinja空caller槽残留空白导致的额外栅格间距；只trim片段边界，内部pre空格/换行保留。foundation10项通过；独立Shells38项的初次证据早于此宏变更，真实14项与后续统一出口承接修订，不混淆哈希。
- iframe内Esc不会冒泡到宿主，组件不注入任意文档的键盘监听；实际编辑/作答预览用宿主可见关闭按钮验证退出，仍验证iframe内草稿保留。父document Esc的旧门禁继续保留，受控业务iframe的快捷键协议属于后续迁移。
- Composer正式CSS **22/22浏览器**、23相关单元通过；灯箱/Prose正式CSS **62/62组合浏览器**（新41+原bridge14+chat7）、3单元通过。灯箱原有pointer capture重定向使图片click误关的缺陷由旧类名基线确认后最小修复，单击图片/空白关闭/双击/拖动/取消/原图新页均经过真实指针门禁；原API和layer core保持。
- 新增全部内容的实际主预览 **2/2通过，1.3分钟**：原复制decorator、灯箱点击/切图/Esc、消息本地form/换行/emoji/busy与草稿、行操作意图不删除。复制测试在浏览器内记录clipboard调用参数，不操作用户系统剪贴板。日志`lq-s2-content-extras-actual.log`，当时CSS为`E5A682C4…`。
- 当前候选统一CSS为`E2BBE0DCA5AEEC0E6BC4E6F8FE1EB3E57AAC64FFB525BC581B99E04B0AE711FC`；正式图`55ff5e810ba088847667eb895ad39e725fd4def73b1d740d6911e20e68c0aa87`共400文件、11,386,917 bytes。42个统一来源图标，完整eager入口 **18,325/18,432 gzip字节**，源码/配方/sidecar校验通过；不是手算源码预算。后续源码若改变必须重新构建。
- 本轮完整Vitest **71文件/628项**、规范隔离runner下LQ后端 **110项/2.230秒**及typecheck均通过。日志`lq-s2-final-candidate-vitest.log`、`lq-s2-final-candidate-backend.log`、`lq-s2-final-candidate-typecheck.log`。后端仅新鲜SQLite，dotenv导入前关闭且真实PG连接拒绝。

六配色全组件实页矩阵、统一587项组件浏览器组合与S2缺项审计仍在执行，当前不签S2完整出口，不进入S3页面迁移；本地数据库事故、真机与正式发布事项仍独立保留。

### S2 本地工程出口（2026-09-20 22:40，取代以上待办状态）

- 独立规格审计的ChipRow、环形Progress、FAB/Topbar/Steps等Shell细节、日期粗指针目标与可访问名称、完整预览缺态均已补齐。最终统一 **623/623** 组件浏览器测试通过（13.9分钟，`.codex-temp/lq-s2-exit-browser.log`），统一CSS SHA256为`b890400a7db1f7f434f174a819a257faa1d8758d5f304bd1972341f8cce9d22e`。此前587项通过只是补齐前基线。
- 72文件 **632/632** Vitest、**112/112** LQ隔离后端、**17/17** 静态资源隔离后端、**10/10** 原生打印Node合同、类型检查和42图标生成一致性通过；lint为0阻断、3,740条登记的旧代码警告，没有新增豁免。后端均由`tools/test_backend.py`先阻断dotenv/PG再导入，使用新鲜SQLite。
- 最终正式资源图`f107d4d2b5f7d130fb4b452261d3c66e5ec5e92849a8c4607803a89716c58979`，recipe `e3353bf1a760d3ee067c46bdb34e59af710510da9828f934096ddd87da8a531b`，唯一eager原生模块逐响应gzip合计 **17,309/18,432 bytes**，源码指纹和实际sidecar内容均核对。打印仅压缩内部局部绑定；保留模块顶层、导出、属性和函数/class名字，不做死代码删除或合并模块。日志`lq-s2-centered-footer-size.log`。
- 实际应用分段验收：主预览桌面/手机2项、真实教师/学生React消费者2项在`b8c4c3d33c6387cc95171257e61f453415416562bbcb8e6f3104a87f2a5b1ba1`图通过；七骨架14项在`958aef...`阶段图通过；居中壳2项在最终`f107d4...`图通过。后两次图变化仅dev预览body/footer样式，组件CSS hash和业务JS相同。两个早期居中明暗对比失败已保留日志，修复后2/2通过，不能把含失败的早期6/16项日志记作全绿。
- 六配色×亮暗×两宽实页旧基线已验收；审计新增状态另有最终CSS下六配色组件axe矩阵和实际32张分段截图。独立逐图查看32张新组件截图、8张正常视口和4张居中截图，共44张；报告`.codex-temp/lq-s2-audit-preview-visual-review.md`。不将早期六配色截图冒充最新资源图。移动超长整页PNG在16,384px以后存在Chromium重复带，已剔除其证明效力，改用小于8,192px有界分段。
- 长截图内列表首行遮挡经两宽正常视口点击命中验证未复现；报告保留sticky/固定控件截图影响。旧全局回顶按钮仍可能压住移动长表格边缘，属于后续宿主页面迁移项，未为拍图隐藏它；此次没有将旧宿主视觉问题误记为组件已全站解决。

工程状态：`[x] S0 [x] S1 [x] S2 [ ] S3`。S3产品试点已开始，正式签字、真实设备、Docker/生产发布与历史数据库事故继续分别待办。阶段图及证据归档是本地证据，不是完整源代码发布/数据库恢复点；没有提交、推送或部署。

### S3 实施与首轮实页验收（2026-09-20，尚未关闭）

范围为默认关闭的九条准确路由：八个管理列表壳/宏及学生成绩页。页面API、资源鉴权和页内原controller保留，管理页旧dialogs/控件并未全部迁移。K9试卷revision票独立交付；考试submit真实模块提取和普通作业/考试成功后草稿反写修复同批验证。实现边界、动作对应与回退单元见 `lq-s3-preflight.md`、`lq-components.md`、`lq-action-registry.md` 和迁移台账九个“迁移中”条目。

| 已执行项目 | 结果与准确边界 | 证据 |
|---|---|---|
| 默认关闭/准确路由开关 | 4项隔离后端通过；不是授权开关 | `lq-s3-flag-backend.log` |
| 合成环境入口隔离 | 5项通过；应用导入前dotenv关闭、继承PG设置清除，直接/同步类/异步类连接均拒绝 | `lq-s3-harness-isolation.log` |
| 三宏/管理壳纯合同 | 25项Python通过；21项正式CSS下route-only/六palette色对通过；不是八个实页业务结果 | `lq-s3-manage-pilot-report.md` |
| 实际nav/service回归 | 导航16项、原成绩服务4项通过 | `lq-s3-nav-regression.log`、`lq-s3-report-service-regression.log` |
| K9保存票 | 新后端8项+原相关21项；真实模板save函数4项；应用浏览器3/3通过（保存失败保值→成功读回、双页409、已有作答与越权） | `lq-s3-exam-revision-report.md`、`lq-s3-authoring-first.log` |
| import生产考试模块 | 23项Node通过；普通作业与考试成功刷新后不再写回草稿的实页路径已有通过证据 | `lq-s3-exam-import-tests.log`、`lq-s3-report-student-first.log` |
| 双批改与退回新轮次 | 各1项真实应用通过；通知/有效revision唯一、旧学生窗口/旧批改窗口拒绝 | `lq-s3-grading-first.log`、`lq-s3-return-round.log` |
| 前端统一单测/类型 | 74文件640项通过；之后新增反馈handoff两项有局部6/6证据，待统一复验 | `lq-s3-vitest.log`、`lq-s3-typecheck-second.log`、`lq-s3-report-second-fix-unit.log` |
| token/源lint | 引用守卫10项、lint合同11项通过；当时0阻断/3736旧警告/5精确例外，后续CSS修复须重跑 | `lq-s3-token-reference-backend.log`、`lq-s3-lint-contract-backend.log`、`lq-s3-pilot-lint-final.log` |
| 真实关闭开关回退 | **4/4**，同合成库另起8163且flag=false；八管理页1440/390恢复旧壳/原钩子，课程原controller失败保值，学生旧图表0/null/筛选仍有效，query不能开启 | `lq-s3-rollback-first.log`、`lq-s3-rollback-first/results.json` |

上述日志/报告均在 `.codex-temp/`。当前记录不代表S3出口。回退运行使用CSS `3aad7cab2c77630cff68070cf0bcb6035fc3f141ed644b1dc5e55a375004663e` 与资源图 `bc66dff0449f0c74cf733619ba9b58c08e2878117cbab709d03ead1bfb142bbe`；后续修复产物须另记hash。

首轮真实管理13项中四个八页矩阵通过（32视图），九条业务例存在测试错误合同、缺学期/开课前置数据、会话失效和真实材料capture交接问题，不能称整包通过。逐张截图又发现info卡整行选择器未适配、暗色正文中性面与桌面topbar过高等；壳范围axe并未覆盖全部旧正文。正在按具体源选择器/原事件所有者修复，不通过隐藏截图内容或取消断言放行。

成绩页首轮12项组合8通过/4失败，随后仅成绩页7项复验4通过/3失败：第一次h2 token修复被旧S1固定浅色面暗字规则覆盖，第二次trace已确认加载最新graph，继续修正准确specificity；移动偏好开关已修复，反馈旧body弹窗仍需原位pane交接。失败日志均保留，后续通过结果不能覆盖这些事实。

业务组合首轮20项14通过/6失败，其中四项失败发生在认证/登录阶段。该轮误将管理与业务两个runner同时指向同一fixture账号，相关结果须以新鲜独占环境串行复验；新增全局fixture锁，阻止跨runner账号会话相互干扰。另两项错题权限断言正在按实际拒绝页面/API合同核对。原fixture不重置；新管理fixture增加独立完整开课与generated学期，保留原dashboard的32课次基线。

仍待：新产物统一业务、管理/成绩视觉、CLS/缩放/触控及相关旧页回归；页面层数/交互请求预算；S3源码/资源证据归档。真实设备、Docker/发布和事故数据恢复未在本轮执行。S4目前仅有只读代码地图，没有凭S3文件存在而开始页面迁移。

### S3 独占环境复验（2026-09-21，继续验收）

- 业务组合在独占新合成环境完整 **20/20、5.2分钟** 通过，日志 `lq-s3-business-second.log`。包括原分类5项、普通草稿/旧提交、考试4项、并发批改、退回重交、旧手工批改3项及错题4项。认证失败已定位为前一轮两个runner共享单会话账号；新增fixture原子锁并分离环境，没有修改生产认证逻辑。HTML拒绝是303至拒绝页面再200，API仍403，权限测试按真实合同修正。
- K9原生PostgreSQL **6/6、5.687秒、0跳过** 通过，日志 `lq-s3-k9-native-tests.log`。新Web版本3项验证同token仅一成功、真实锁等待后重新读整行hash、对手rollback后可保存；原Agent分配/过期review/receipt回滚3项同轮通过。全新独占簇 `E:/CodexTemp/lanshare-k9-pg-20260921-000812-5fc381f9/cluster`、端口54744，导入前隔离dotenv与默认应用SQLite，driver精确连接白名单，fixture再核对服务器目录/监听/端口。完成后 `pg_ctl status` 无服务器、端口关闭且pid文件消失，证据 `lq-s3-k9-native-result.json`。没有连接5432、读取业务备份或执行应用迁移。
- 统一前端 **74文件643项** 与类型检查通过，日志 `lq-s3-vitest-second.log`、`lq-s3-typecheck-third.log`；最终head调整后的管理模板9项、成绩模板5项隔离回归通过。此结果不代替仍在进行的实页测试。
- 首载移动CLS失败推动修正：仅试点模块移到head，使用能力检测支持的 `blocking="render"`。不支持时保留SSR内联几何，由同一Shell的 `keepOpen` guard继续搜索/折叠/操作；不隐藏整页，不另起控制器。模块404、请求中断、延迟加载、无JS与能力关闭另有真实页面门禁；能力关闭仿真不等于Safari17/Firefox或真实设备验收。
- 本轮正式图 `351bebe04d20bb2329e9027211f0088112ca7a53d67dc2244c0faf611d944f42`，CSS `74792934d906bf0c28aa5cc61bc48462c8d95464234da44f56009c857ba9ba63`。布局7项已通过；后续成绩强制颜色发现固定暗色前景与系统白背景冲突，管理页对比探针也有不存在的标题选择器，均保留失败证据并继续有限修复。迁移台账更新还暴露source lint将描述文本/构建物当源码的问题，须按准确资产与迁移范围修正，当前不宣称lint/整个S3通过。

第六正式图 `d8d218739f7f296fb0035944f974f4820b2e0975179b67c2689ec1c170689ba5` 下，管理第三轮 **13/13、5.4分钟**，成绩第四轮 **7/7、2.8分钟**；旧教师壳 **5/5** 在第五图独立环境通过。对应日志 `lq-s3-manage-third.log`、`lq-s3-report-fourth.log`、`lq-s3-old-shell-first.log`。管理预览保存测试先真实刷新并核对返回版本和无阻断项，再点保存，避免把收到响应头等同于原控制器已经接收预览。

资料详情内旧LP删除确认此前未参与父LQ栈，真实取消被父层拦住。公共helper现仅在已有LQ父层时使用同一portal/focus/Esc/锁与清理，独立旧LP保持原同步合同；业务删除影响token和请求保持。首轮完整bridge **31通过/1失败**，失败为测试填写日期后未关闭原日期层；修正正常操作顺序并补独立审查发现的pending-veto强制退出、onMount移除边界后，新增子集 **4/4** 通过。日志 `lq-s3-nested-process-bridge.log`、`lq-s3-nested-process-bridge-fixed.log`。后一次源码须随第七图重建，不能把第六图写成包含之后的边界修复。

布局第五图四个首载CLS分别为管理1440/390均0、成绩1440为0.005356081、成绩390为0.032107718，均≤0.05；busy与收缩的关键几何≤1px。学生首页另记高度2114和CLS0.067390584，属于S4原页面基线，未将其当作S3成绩页通过项。摘要 `lq-s3-layout-third-summary.json`。

失败回退第二轮 **6通过/1失败**：404、中断、延迟、无JS与管理能力关闭通过，报告能力关闭后旧人生一言节点迟到插入导致顶栏增加25.40625px，正用SSR预留空间修复。新跨引擎兼容门禁又确认管理窄屏菜单按钮被顶栏挡住，继续修正真实层级；未强制点击或删除断言。WebKit引擎已装到E盘，当前引擎运行与真实Safari设备分开记录。

迁移台账只更新9个试点，其他142条不动：controller仅存实际路径，说明独立；partial scope完整扫描新源码，共享文件保留全量warning并以明确范围/测试/完整字节SHA约束后续审阅；编译物和vendor与作者源码分开。定向41项通过，审阅时lint为0阻断/3721警告/8条原例外。后续共享文件变化必须复核对应门禁再更新指纹，不自动刷新掩盖漂移。详见 `lq-s3-lint-scope-review.md`。

S4目前仅新增[分批预审](lq-s4-preflight.md)。S3仍待最后暗色局部、导航可达、能力回退、跨引擎和性能门禁；源/产物归档与最后关闭开关回退随后执行。全阶段状态继续保持未关闭。

### S3 第七图与定向收敛（继续验收）

第七图 `496d62541e905f36ec7ca17eeed35cede15ff7501e6696fc74ac254ac43df008`，CSS `0b3db5151f2c3eee0105533437e06fdc5781b3cdb0acfee8e61713565ad48075`，407文件。正式入口17,309/18,432 gzip字节，源码指纹校验通过；74文件643项Vitest、typecheck和lint通过，lint仍为0阻断/3721旧警告/8精确例外。关闭试点开关的四项回退 **4/4** 通过，日志 `lq-s3-rollback-seventh.log`。

管理第七轮 **11通过/2失败**：两亮色矩阵与九条真实业务均通过（包含最新材料子层桥接），两个暗色矩阵因新增探针把真实 `materials-filters-toggle` 拼成单数而失败。仅修正选择器后，暗色两项再次运行，发现按钮真实对比4.4754608，仍未达到4.5；没有放宽阈值，继续使用已有primary soft前景对修复。逐图审查又确认学期两种既有状态色型在暗色活动卡只有约2.18/2.21，精确映射success/warning fg与soft，原业务class/文案保持。日志 `lq-s3-manage-seventh.log`、`lq-s3-manage-dark-seventh.log`，新色对待第八图实测。

跨引擎第七轮 **5通过/3失败**：管理完整断点与768触控在Chromium/WebKit四项通过，WebKit成绩完整断点通过。Chrome成绩两项在旧600ms入场scale尚未结束时量到43.998/43.732px，修为有界等待相关有限动画后保留44px精确断言。WebKit成绩触控则是真实旧controller缺陷：正文空白和h2均产生trusted pointer/touch事件，却不合成click，旧document click关闭逻辑未执行；原点命中和事件证据见 `lq-s3-scene-touch-probe/summary.json`。不能靠改测试点击位置掩盖，正在修复同一浮层owner的外部触控与监听清理。

能力回退第七轮 **6通过/1失败**，唯一失败同样是“一言”入场scale的瞬态量尺；报告内联顶栏/lead几何已通过原≤1px门槛。报告+布局组合 **13通过/1失败**，报告7项全过；唯一管理390滚动还原位置差1.036px，初载/滚动/还原CLS均0。轨迹证明旧 `managePageEnter` 的380ms transform尾部仍在进行，修为保持初载CLS采样原点、只在滚动几何基线前等待该有限动画，原1px门槛不变。定向manage390 **1/1** 通过，日志 `lq-s3-layout-timing-seventh.log`。

第七报告indigo亮暗两宽四张原图已另行实读。正文、0/null/未公布及图表可读；原底Dock的移动遮挡/暗色样式仍明确留给S4共享壳。亮色场景顶栏品牌文字的实像素对比约1.98–2.13，背景图使axe未给出对应违规，现按真实像素继续修正report限定背板，不能将axe全绿视为所有图像背景上的文字已达标。

所有后续改动须重新构建再复验；上述通过项按准确图与源码轮次保留，不合并声称第七整轮通过。性能六项的interval/MO/RO、监听基线、Event Timing和带标记CDP窗口已补齐，尚未完成实际最终采样。S3仍未关闭。

### S3 第八／第九图实际出口证据

第八图 `50971e52086808933d94241661fbafd19ed5b6565ac86bc526457a486adb1c5b`，CSS `855fdd5c4f222609e0a68542fbca16421f91064872171e31faed6d39382c1793`：

| 实际入口 | 结果 | 日志 |
|---|---|---|
| 管理两套暗色矩阵（覆盖八页） | 2/2，2.4分钟；所有新增真实对比探针≥4.5 | `lq-s3-manage-dark-eighth.log` |
| Chromium/WebKit完整断点及768触控 | 8/8，5.0分钟；包含一言原点击/toggle/Esc/外部pointer与每路径20轮监听归零 | `lq-s3-compat-eighth.log` |
| 模块404/中断/延迟、无JS、能力回退 | 7/7，2.3分钟；44px与≤1px几何阈值保持 | `lq-s3-fallback-eighth.log` |
| 成绩七项与两宽初载/滚动布局 | 9/9，2.4分钟 | `lq-s3-report-eighth.log` |
| 关闭pilot的旧成绩导航/一言/图表 | 1/1，19.7秒；原三关闭路径与0分语义保持 | `lq-s3-legacy-scene-eighth.log` |

管理完整业务覆盖由第七图两亮色矩阵及九条业务通过、随后第八两暗色矩阵通过组成，不能写成第八同一轮13项。最终16张目标原图及一张课程导航图逐张复核，SHA/尺寸与历史失败见 `lq-s3-manage-final-visual-review.md`、`lq-s3-manage-final-image-index.json`。成绩真实partial在第八正式CSS下完成六palette×亮暗×两宽×纯黑/白场景48组，528个可见文字及品牌图标目标最低6.436014；图像/遮罩保留，详见 `lq-s3-report-scene-contrast-review.md`。局部两blur未作为整页预算结论。

随后完整390页实际审计在成绩rest测到3层：顶栏16px、场景图22px、旧bottomnav12px。第九仅在 `body.lq-report-card-page .app-bottomnav` 取消旧96%实色底栏的冗余backdrop，保留原位置、链接、状态和S4 Dock迁移边界。失败日志 `lq-s3-glass-mobile-eighth.log`。第九图 `de1603bb1755d603744b9d612d4b0808b676f53c07df6dce0f8e0d362ff4d743`，CSS `a5f78b0f0107d6e43fb680ecbd390ad71d172d4449c466990fcf10fc9c9f8570`，407文件，入口仍17,309/18,432 gzip字节且源码指纹通过。

第九独占性能运行 **6/6、2.8分钟、0跳过/重试**，九页两宽实际持续/滚动≤2、弹层≤3。四种同文档场景各先off再tinted，每段20次相同操作：

| 场景 | off p50/p95 ms | tinted p50/p95 ms | 期末 interval/MO/RO（前后均无增长） |
|---|---|---|---|
| 管理1440 | 16 / 16 | 16 / 16 | 7 / 4 / 1 |
| 管理390 | 24 / 40 | 24 / 40 | 7 / 3 / 1 |
| 成绩1440 | 16 / 24 | 24 / 32 | 4 / 4 / 2 |
| 成绩390 | 24 / 32 | 24 / 32 | 4 / 4 / 2 |

这些是固定机器、Chromium、1×CPU的实验室Event Timing（16ms报告下限），不是线上INP或低端实体设备结果。监听总数、活跃interval、MO/RO及其来源/观察目标均按基线和同关闭状态期末比较，未声称采集资源的瞬时峰值。操作网络仅管理390 tinted发生一次原15秒消息轮询GET，最高并发1；其余无新请求，WS/SSE均0。PerformanceObserver八段均未捕获>50ms任务；原始CDP标记窗口另作离线核对，不用任务数量差代替源码归因。日志 `lq-s3-performance-ninth.log`，原始附件及摘要在同名目录，离线完整结论随收口记录。

新增共享场景controller已按确切审阅SHA登记partial范围，原SSR合同引用缺失曾使12项范围检查中1项失败；补齐实际SSR与浏览器双入口后12/12、成绩SSR5/5通过。第九lint为0阻断/3721未迁警告/8原例外，14个审阅共享源；类型检查通过。源/图证据归档和最后阶段结论仍单独记述，不据此宣称真机、Docker发布或事故数据恢复完成。

**S3本地工程出口结论（2026-09-21）**：通过。离线人工复核见 `lq-s3-performance-ninth/manual-review.md`，其配套 `trace-completeness-audit.json` 确认八段trace中未闭合B事件全部开始在阶段结束标记之后19.910–139.052ms；它们没有造成交互窗口缺失。窗口内有真实完整RunTask，最大3.656–36.641ms，无>50ms待归因任务。FunctionCall有正式脚本URL及压缩raw行号，无完整stack frames；因此本轮无长任务缺口，不外推整个旧业务。62个整页blur观测最高2。自动分析器保留原截断警告，人工解释独立存档，没有删除不利证据。

机器证据 `lq-s3-performance-ninth/host.json`：Windows 11、i7-13700H（14核20线程）、约16GB内存、1×CPU。它不是计划中的低端机/iPad/微信设备。源／图检查点按最终图归档，包含保留的原用户未提交修改；不含应用数据库、不代替数据备份、远端Git或发布。S4可按预审开始本地实现；总计划正式验收框、实际设备、Docker/生产及本地事故数据恢复仍未关闭。

实际归档 `.codex-temp/lq-s3-engineering-checkpoint/manifest.json`：503条源码记录（含删除项）、743份原路径证据索引、810个图文件/压缩sidecar副本。逐份SHA复核0不符，见同目录 `verification.json`。创建日志不索引自身仍在写入的文件；证据索引引用保留的原件，未宣称复制全部trace或形成数据库恢复点。

归档补记：兼容诊断报告在原校验后追加了四份已存在日志的终态，原路径现为6692字节。已验证其前5469字节与原索引SHA完全相同，并在 `lq-s3-engineering-checkpoint/amendment-01/` 分别保存原文、追加后全文及新旧SHA说明。原manifest与verification未重写；不要将历史“0不符”误读成追加后原路径仍与旧hash一致。

## S4 第一包本地验证（2026-09-21，阶段仍进行中）

范围为三个默认关闭的页族壳manage-shell/navbar-shell/centered、Profile C0原样提取、已有密码登录的原生失败恢复。未把业务正文、C1/C2、双首页/日历、成长与其余管理页记为已完成。实际开关及下一包依赖见 `lq-s4-preflight.md`。源码仍在当前未提交工作区，HEAD为 `23fd77e050a54e57b12e70b2c742a36536ae9142`，不以HEAD伪称包含此次修改。

| 已执行门禁 | 结果与证据 |
|---|---|
| 隔离后端 | 页族精确开关3项；管理壳SSR11项；新navbar SSR11项；Profile提取9项及32份完整渲染对照；认证/恢复/凭证35项；居中模板最终6项，分别通过。`tools/test_backend.py --pattern <对应文件>` 使用导入前隔离、临时SQLite及PG拒绝守卫。 |
| 共享壳 | 第一图6/6，1.9分钟；学生10路径、教师4路径×1440/390；原草稿20次复挂、Dock模拟键盘、noJS及模块失败。`lq-s4-shell-first.log` / 同名results.json。管理组件另15/15；成绩共享adapter抽取单元7/7及SSR5/5。 |
| 共享壳视觉 | 第一图3通过/1失败，手机学生整页模糊为3（topbar16+scene image22+Dock16）；第二图移除新navbar额外image filter，失败项复验通过。第二图截图又发现旧首页palette窄宽挤掉选项文字。第三图修复后5/5，1.6分钟；双角色×双宽×六palette×明暗、另320窄屏，实际选项文字空间/边界与玻璃层数均断言。`lq-s4-shell-visual-third`。 |
| 登录真实模板 | 第二图完整20/20，状态图标修复另1/1；全页/状态52次axe、错误48色对最小6.12294；20次材质切换420帧配对及端点像素。`lq-s4-centered-formal-second`、`lq-s4-centered-status-icon`。 |
| 登录实卡背景 | 第二图316原图×亮暗×双宽1264/1264，最低5.23257；输入/placeholder不透明底最低5.05571；可见blur均1。`lq-s4-login-page-formal-second.json`。此前1264开发批9失败及过渡帧错误完整保留，不回写为成功。 |
| 真实认证 | 独立合成8167：第一图19通过/1测试文案错误，修正后该1项通过；第二图4项JS复验通过43.3秒。角色/账号状态/会话替换/受保护来源query/403注册/no-store/错误重试均实际HTTP；未发送邮件。报告 `lq-s4-auth-browser-report.md`。 |
| 颜色证据边界 | 实际认证axe保留图像背景contrast incomplete；修复反馈的四张PNG另采样约6.14。1264池用完整前景对文字线框实际背板保守像素；420帧仅computed配对，端点另测像素，不等于每帧截图。 |

静态图按轮次独立：第一图 `673c4cf078930a2397be7f4d713ebaec8e81e9341c2f5c25c5df13ee3f0d8d1f`；第二图 `d9357134027c998b8dc88b325211523081b0d1e73194e0596826a44e35334671`；第三图 `8e24e14ff3b353d2d53a9652ca6f0cba268ffe43847ec2d8347cacee9caaf2f2`（414文件11,480,100字节）。第三图只变新navbar外观面板的宽度兼容，未将第二图登录结果重新标成第三图全量运行。第二图lint零阻断、3722未迁警告、11条命中来自2条限域例外、27个审阅共享源；partial范围12项通过。更新status图标与第三图后再作指纹检查。

root已实看学生资料/教师概览的390原图，以及修复前后390暗色更多面板；旧业务正文仍有原大卡片和长导航，属于下一包，不作为本次共享壳视觉完成。D包40张正式图加2张图标修复图、真实认证19张错误图由各owner逐张实读并记录路径SHA。实体设备、真实软键盘未测；关闭服务端页族开关及新增壳性能/资源检查待本包独立记录，不由前述截图或节点测试替代。

### S4 第一包回退与性能补记

第三图关闭三个S4页族、保持S3独立开关后，6个独立场景由首轮5项及修正等待后补跑1项通过。覆盖双角色/两宽旧壳与原生登录、来源query、安全next、成绩零分/未公布/本人范围及原图表实例；未写偏好或成绩。失败来自既有场景入场尚未结束时执行axe；重试等待实际场景类、字体和有限动画完成，不改阈值。16张原图已逐张查看，其中两张学生首页仍为入场画面，不记作稳定视觉签字。报告 `.codex-temp/lq-s4-rollback-report.md`。这是服务端flag回退演练，不是代码逆向部署或数据库恢复。

新增共享壳4×CPU门禁首轮4项失败：两处fixture问题分别是首次跨断点才安装的observer基线、说明浮层先消费Esc；它们已修正，真实手机端开关导航的长任务仍存在。未提高200ms延迟或50ms长任务阈值。第一小修候选 `df7e587605b07e2c06a34b5230d96f524eddae64b17cefb306261cf33481f594` 只减少Shell同值属性写入和Dock无关焦点测量；47项组件通过后，手机390单项仍失败。其CLS=0、20轮原节点/草稿/监听/observer/timer与请求无增长，off/tinted p95=104/176ms，但真实输入长任务仍25/30个；此次关掉Playwright快照采集，不能把所有数值改善归因于产品修复。

第二小修候选 `f098c3c1024ab632d35328277c77c7b8d06e9ff39045d576b9872af4228af8c9` 将滚动锁补偿读取放在overflow/inert写入前，并在gap=0且根变量原先为空时不取得该变量所有权，避免无必要的继承变量全页重算。弹层28项及Shell47项共75/75通过，覆盖非零gap、stable gutter、原inline值/priority、嵌套native/fallback、iOS模拟与强制销毁恢复。414输入和824图文件/sidecar已按SHA隔离复核，gzip17,347/18,432通过；实际手机性能仍另测，不以组件通过代替性能出口。两候选均从第三图检查点构建，只包含明确core修复，未混入并行的C1/D2开发文件。原失败及CDP归因保留于 `lq-s4-shell-performance-attribution.md`。

## S4 续建（2026-09-21，Claude 接手 Codex 中断后）

接手时工作区状态与缺口见 [进度交接](lq-progress-2026-09-21.md)。本节只记录接手后实际跑过的结果；负责人阶段签字、实机、Docker 与生产发布仍分别未关闭。

### 接手首日修复（主任务）

| 项 | 内容 | 证据 |
|---|---|---|
| 401 会话恢复页崩溃 | `app.py:470` 在 Starlette 1.0 下把 context 字典当作第一参数，抛 `TypeError: cannot use 'tuple' as a dict key`，恢复页整页不可用。改为 `TemplateResponse(request, ...)` | `test_auth_session_recovery.py` 9/9（原 13 errors） |
| 路由快照基线 | 新增 6 条真实路由（外观分区、学生身份登录 GET/POST、找回 GET/POST、设密 POST）逐条核对后重写基线 960 条 | `test_architecture_route_snapshot.py` 1/1 |
| 考试草稿部署契约 | 断言改为跟随已抽出的 `static/js/exam_take/submit.js`，不再在模板里找被搬走的内联代码 | `test_deployment_browser_cache.py` 7/7 |
| 消息附件合同 | 改为断言恢复后的附件端口（一个隐藏 file input + 一个预览节点） | `test_profile_template_contract.py` 9/9 |
| 台账补盘 | `tools/ui/lq_registry_triage.py` 把 196 条「未盘点」推进到「已盘点」：roles/layout/assets/islands/controller（含 ESM 依赖闭包）/domHooks/apis/versionFields 由源码静态推导并标注来源；states/components 留空待迁移时填 | 台账 196 已盘点 + 12 迁移中；备份 `.codex-temp/lq-migration-registry.pre-triage.json` |
| 并行基础设施 | `tools/ui/locked_build.py`（构建串行锁）、`.codex-temp/claude-s4-runbook.md`（七包所有权/端口/测试入口/禁止事项/报告格式） | — |

### 跨包缺陷（主任务修复，各包上报）

| 缺陷 | 根因 | 修复与验证 |
|---|---|---|
| AI 助手悬浮按钮装饰光环截获页面点击 | `.ai-workspace-fab__halo` 有 `inset:-8px; z-index:-1` 但无 `pointer-events`，比按钮大 8px 且参与命中测试，`elementFromPoint` 命中它而非目标控件（个人资料包用截图证实，`force:true` 也无效） | 加 `pointer-events: none`；个人资料包原先只能改用键盘激活的保存按钮路径恢复正常 |
| 修为进度条辅助技术读不到 | `.cultivation-card__meter` 在无 role 的 div 上挂 `aria-label` | 改为 `role="progressbar"` + `aria-valuenow/min/max/valuetext`；`test_cultivation_card_partial.py` 4/4 |
| 心情按钮 1.03:1、资料侧栏副标题 2.2:1 | 深色模式配色错配：背景硬编码 `rgba(255,255,255,.72/.82)` 固定为浅色，文字用会翻转的 `--ls-ink-2/3`（27%→75% 亮度），深色下浅字落浅底 | 背景改 `hsl(var(--ls-surface-1) / …)` 使其随模式翻转；浅色模式渲染不变 |
| 修为徽记 4.16:1（灰）/ 4.25:1（琥珀） | `color: var(--cultivation-card-tone)` 直接落在同色 12% 淡底上 | 改 `color-mix(tone 78%, black)`；实算五个色调全部升至 6.07–7.31，保留色相识别 |

### 各包结果（每包报告在 `.codex-temp/claude-s4-<pkg>-report.md`）

| 包 | 结果 | 关键证据 |
|---|---|---|
| **A 共享壳性能** | 4 场景 2 通过（两个 1440 长任务归零），两个 390 场景未达长任务门槛但 p95 ≤200ms | 根因是 `--lq-scrollbar-w` 未注册：未注册自定义属性默认继承，写在根元素令全文档 446 元素失效，实测 22.9ms → 1.8–2.4ms。用 `CSS.registerProperty({inherits:false})` 修复，协调层契约零改动。剩余成本经隔离探针归因于原生 `showModal` 本身（空对话框 16.9–19.6ms 安静 / 44–122ms 负载），非壳代码；1440 不走该分支故为 0。一处预写优化经 trace 证明为负优化已逐字节回退。`npm run test:lq` 159/159、组件 spec 108/108 + 107/107、`lq-s4` 11/11 |
| **C1 个人资料与外观** | 全部通过 | 12/12 外观真实场景（字段级 CAS 落库、409 不覆盖、失败本地预览重试、身份过期拦写、跨页配色作用域）；关闭分支回归 7/7；业务失败路径 11/11（教师任职身份、头像、密码、邮箱语义走真实后端）；强制颜色与减少动效 4/4（并修掉 `lq/pages/profile.css` 强制颜色下帮助/状态文字未覆盖的真实缺陷）；六配色 24 组合 + 24 图 |
| **C2 消息中心** | 全部通过 | 11/11 端到端（三轮独立运行）、65 条组件 fixture、后端 9/9。所报「草稿未清除」经根因定位为其自身 spec 选择器误匹配空占位 option，**未为掩盖测试改业务代码**；`aria-disabled` 用文档级捕获 + `sendMessage` 顶部双层拦截，严格按开关分流 |
| **D 登录与状态页** | 全部通过 | 迁移后原生认证 8/8（新播 8 个互不相交合成身份）；新增会话恢复 spec 8/8；人生一言 705 行迁出 `ui-system.src.css` 至 `lq/pages/life-tip.css`，主任务独立核实两文件选择器零重叠；揭示过渡验证背景 `animation-name: none` 且 300ms 窗口像素一致，减少动效直达终态；四状态页 16 组合 axe 全清；390×660 软键盘可达。33 张图 |
| **E 成长页族** | 4 通过 1 主动跳过 0 失败 | 5 轮真跑，修掉 3 个真实无障碍缺陷（两处筛选选中态子元素显式颜色盖过父级继承、一处无角色 div 上的 aria-label）；兑换全链验证（确认弹层、余额只来自服务端、请求期锁按钮、重复点击只发一次 POST）；关闭分支另起第二服务验证 5 路由回落旧 DOM；40 图矩阵；后端 28/28 |
| **B 双首页与日历** | 议程待办闭环 e2e 通过；三条硬指标通过 | 双 aria 词汇按裁定共存（关闭分支 `role=group`+`aria-pressed` 字节不变、既有 7 处断言不动；开启分支 `role=tablist/tab`+`aria-selected`+`aria-controls`+roving tabindex），`dashboard.js`/`student_dashboard_schedule.js` 只保留一个状态机按元素实际 `role` 分流。硬指标：3D 面板跨组模式切换（含 20 轮）为**同一 DOM 元素实例**、学生 390 首载 CLS ≤0.05、移动端文档高度 ≤4200px。待办弹层接 `LQ.layer` 并保留 `.semester-todo-modal-card` 类名与 160ms 时序；议程待办新增/完成/删除/500 失败路径/Esc 焦点回归均真跑。真跑中发现并修复 `lq_page_head` 破坏 `[data-ls-open="calendar"]` 选择器的真实回归（两条既有 spec 曾 5 项失败） |
| **F 其余管理页** | 8/8 其自有 spec；后端 38/38 | 40 个模板页头接开关（逐行追加，主任务核对无内容丢失）；`window.confirm`/`alert` 在其范围内归零；发现并修复真实缺陷：投票摘要 chip 对比度 2.51:1、Escape 与协调层竞争破坏焦点回归，且三个页面的弹层关闭路径**本就从未恢复焦点**；筛选改 `lq-chip-row` 后原生 select 代理经真实浏览器验证仍生效。正确判定这些页面无 `<table>` 标记、未硬套表格组件 |

### 环境发现（影响所有性能测量）

仓库位于坚果云同步目录，每次构建写入约 11.6MB / 420 文件触发同步，同步进程空闲即占 0.5–1.5 核。同一份代码同一构建：机器繁忙轮 student 1440 为 19 个长任务，安静轮为 0。本机比参考机慢约 2–2.5×。**性能门禁的移动端两项标记为「环境受限、待安静主机或真机复测」，未放宽任何阈值**；计划 §20 原本就要求实机记录，该项仍为未测。

### B 包三项独立归因（2026-09-21）

1. **学期日历待办弹层当前不可达**：两个首页模板都传 `semester_calendar_compact=true`，而唯一能打开它的 `[data-semester-todo-add]` 被 `{% if not compact %}` 挡住，因此无法通过真实点击端到端验证。已按模板行号如实记录，未以"已覆盖"含糊带过；改为对真正可达的 `.agenda-todo-modal` 做完整闭环覆盖。
2. **20 轮后多出的 1 个 MutationObserver 不是应用泄漏**：用资源探针抓到创建栈为 `InjectedScript._setupGlobalListenersRemovalDetection`，属 Playwright 测试框架内部；绕过 `@playwright/test` runner 的独立脚本两次运行均不复现。
3. **`dashboard-schedule.spec.ts` 的 3 个失败与本次改造无关**：在 `LANSHARE_LQ_FAMILIES` 置空（全部开关关闭）的同一运行时上复跑，失败完全相同。疑似根因为 `prepare_ui_v3_runtime.py` 与 `prepare_schedule_fixture.py` 的播种顺序，因未直接查 sqlite 证实，标为未确认。

### 主任务终检（2026-09-21）

令牌守卫捕获并修复首页包新建样式中三个凭空发明的令牌名：`--ls-t-caption1`→`--ls-t-caption`、`--ls-ease-standard`→`--ls-ease-out`、`--ls-primary-ink`（带浅色回退，会在深色模式失效）→`--ls-on-primary`。修复后全量回归：隔离后端 **3744 项全过**（205 条件跳过）、前端 **78 文件 668 项**、`npm run typecheck` 干净、`lint_lq.py` **blocking 为空**（3912 条未迁旧代码警告、27 个审阅共享源）。注册表三处过期指纹（两个首页模板、课堂运营台）经逐 diff 复核后刷新。最终静态图 `a815bad04945b17ef891c6d0a770eb6506495165ff39710d71c5291f635854be`，422 文件 11,645,516 字节。

## 跨包遗留项结案（2026-09-21，主任务）

前三轮各包上报、无人有权限独立结案的两项疑点，本轮由主任务查清。两项都不是改造引入的缺陷，但其中一项暴露了一个真实产品缺陷。

### 一、`dashboard-schedule.spec.ts` 的 3 个失败：已修复，现 11/11 全绿

B 包三轮都把这 3 个失败标为"与任何 LQ 开关无关的既有失败"，并推测是学期行被误复用导致课堂挂到不覆盖今天的学期上。直接查合成运行时的 SQLite 后，**推测的方向对、机制不对**：两个学期都覆盖今天。

真实链路：

1. `tools/ui/prepare_lq_s3.py:95` 把夹具课堂所属的学期行**克隆**一份，命名"S3 独立表单学期"，起止日期与原学期完全相同、id 更大。该处注释明确写着这份克隆应与首页学期互不干扰。
2. `tests/e2e/scripts/prepare_schedule_fixture.py` 的注释写"复用夹具自己的当前学期"，实现却是"挑任意一个覆盖今天、id 最大的学期"，于是把夹具课堂改挂到了克隆学期上。教师端与管理端课表因此取不到课次。

修复两处：

- `tests/e2e/scripts/prepare_schedule_fixture.py`：优先复用课堂自身的 `semester_id`（当它确实覆盖今天），否则才回退到原查询。实现与既有注释的意图对齐。
- `classroom_app/services/student_course_schedule_service.py`：**真实产品缺陷**。多个学期同时覆盖今天时，学生课表按列表顺序取第一个，恰好会挑中名称无法解析出学年学期的那个，已选课学生因此看到空课表。改为在所有"当前"学期中优先选择确实承载该学生课堂、且名称能解析出学年学期的那个。

回归证据：

| 项 | 结果 |
|---|---|
| `dashboard-schedule.spec.ts`（全部开关关闭） | 11 passed（修复前 8 passed / 3 failed） |
| `tests/test_student_course_schedule.py` | Ran 11 tests, OK |
| 新增用例在无修复时 | FAILED（确认能抓住该缺陷） |
| `test_dashboard*.py` / `test_course_schedule_service.py` / `test_semester*.py` / `test_academic*.py` | 64 OK / 45 OK / 24 OK / 247 OK(skipped=4) |

新增回归用例 `test_overlapping_custom_term_does_not_hide_the_real_timetable`：学生同时属于学校学期与一个共享相同起止日期的自定义学期时，课表必须落在承载其课次的那个学期上。

### 二、`lq-btn--soft` / `lq-btn--prominent` 对比度：令牌无问题，测试夹具已加固

F 包上报 teal 调色板 390px 下实测 3.17 与 3.13，低于 AA 的 4.5:1，但复现不稳定。

直接从 `static/css/lq/tokens.css` 计算全部 12 个调色板 × 明暗共 24 种组合的真实对比度：prominent 最低 4.94，soft 最低 5.96，**全部达标**。teal 亮色实际为 `#115f5a` / `#e2eeed`，对比度 6.34。

F 包测到的 `#558c8a` / `#e2ecef` 恰好等于真实颜色按约 70% 不透明度合成的结果。根因在 `static/css/ui-system.src.css:51926`：`.manage-content:not(.is-embedded)` 挂有 `managePageEnter` 动画，opacity 由 0 到 1、时长 380ms，每次加载管理页都会跑。axe 在这段时间内采样读到的就是整棵子树的混合色，这解释了为什么它只出现过一次、重跑即消失。

处置：**不改任何 CSS**。在 `tests/e2e/fixtures/lq-s3.ts` 新增导出的 `settleEntranceAnimations(page)`，在扫描颜色前等待管理页入场动画结束，避免这类间歇性假阳性。

### 三、`process_material_modal.js` 双 Escape 控制器嫌疑：不成立，无需改动

F 包上报该文件第 58–78 行疑似同时挂载协调层与自有 `document` keydown，与文件自身注释矛盾。逐行核对后确认两条路径互斥：

- `static/js/process_material_modal.js:112` 的自有键盘处理器带 `if (!parentLayer)` 守卫，只在没有父层（独立弹层、尚未迁移的页族）时挂载。
- 同文件 `layer.open(...)` 只在 `parentLayer` 存在时调用。

因此任一时刻只有一个焦点/Escape 控制器在运行，文件第 55–57 行的注释与实现一致。不改动。

## S4 第四轮：B 包与 F 包遗留项收口（2026-09-21）

第三轮各包如实标注的遗留项，本轮由两个施工包并行推进，主任务复核合并。

### B 包（首页与日历）

| 项 | 结果 |
|---|---|
| 工具行改用 `lq_filter_bar` | 完成。13 个钩子迁移前后逐一 grep 核对，`dashboard.js` 一行未改 |
| 移动端折叠 `lq_collapsible` | 完成。`mode='responsive'` 桌面锁定常开，390px 折叠，"我的课堂"用守卫态强制展开 |
| 评估菜单改 `lq-menu` | **未做，停在冻结契约边界**（见下） |

`lq-menu` 不做的理由经复核成立，且比工作量估算更硬：`classroom_app/lq_menu_tooltip.py` 拒绝根节点 `attrs`，状态机面板的钩子无处挂载；菜单宏只渲染 items、没有插槽，面板里的三态标题与 `aria-live` 状态区既不是菜单项也拿不到该属性。要接入必须先扩展共享组件，属独立的 A 包票，不在本轮范围内。

B 包同时纠正了第三轮报告的一处错误：此前点名的 `manage_page.filter_bar` 兼容宏不转发 `name` 与 `value`，用它会直接破坏 GET 提交与搜索回填，实际应使用冻结组件 `lq_filter_bar`。

### F 包（常规管理页）

| 项 | 结果 |
|---|---|
| `lq-field` 迁移 | 学期页 3 个控件、组织架构页 10 个控件 + 5 个按钮。投票页**明确不做** |
| `classes.html` 三个下拉改筛选芯片 | 完成，真实下拉全部保留，复用既有代理机制 |
| 材料库域 | 覆盖层审计与修复完成，结构层未做 |

投票页不做的理由成立：该表单由 `manage_polls.js` 在运行时用模板字符串拼装，服务端宏够不着；在客户端手写等价 DOM 等于绕过服务端属性校验，违反冻结契约。需先由共享组件提供客户端字段工厂。

F 包在审计中修复三个真实缺陷：教材页两个自造覆盖层既无 Escape 处理也不返还焦点；班级页清除筛选不派发变更事件，导致新芯片高亮不归位；课程页无条件吞掉 Escape 键。前两项有真实浏览器用例锁住，第三项为同类模式修复、无专属用例（该文件当前无 LQ 弹层调用点），已如实标注。

### 主任务在本轮修掉的两处

**`settleEntranceAnimations` 的真实缺陷。** 我上一节新增的这个辅助函数会等待所有运行中的动画，而管理页存在常驻的无限循环加载动画，谓词永远不成立，五秒必然超时。改为只等待有限次数的动画。验证方式是在 `/manage/teaching/semesters` 页面内主动注入一个 `iterations: Infinity` 的动画，函数仍在 321ms 内返回。F 包随后删除了它的固定等待兜底，改为直接调用，重跑 16 passed / 0 skipped、`flaky: 0`，且此前唯一复现过按钮对比度假阳性的 390px 组合未再复现。

**`dashboard-schedule.spec.ts` 在开启分支下的失败。** B 包三轮都归因为"第二轮语义切换的既有后果"，复核后发现不完全准确：其中一条是本轮 `lq_filter_bar` 迁移带来的 DOM 归属变化。分两处修正：

- 四处分段控件断言改为按元素实际 `role` 选择断言 `aria-selected` 还是 `aria-pressed`，一份用例同时覆盖开关两个分支。
- 布局断言此前测量搜索表单，而迁移后的表单同时包住了筛选块，测得的盒子跨越两行。改为测量搜索输入本身，两个分支共有的 `data-dashboard-search` 钩子。像素容差保持原值 2，未放宽。

### 本轮回归（主任务合并后全量重跑）

| 入口 | 结果 |
|---|---|
| `tools/test_backend.py`（隔离全量） | Ran 3745 tests, OK (skipped=205) |
| `npx vitest run` | 78 files / 668 tests passed |
| `npx tsc --noEmit` | 通过 |
| `tools/ui/lint_lq.py` | blocking 为空 |
| `dashboard-schedule.spec.ts` 开启分支 | 11 passed |
| `dashboard-schedule.spec.ts` 关闭分支 | 11 passed |
| `lq-s4-f.playwright.config.ts` | 16 passed / 0 skipped / flaky 0 |
| `locked_build.py` | `LQ_GRAPH=98ebd8bc3d3a…5db628`，422 files |

审阅指纹已刷新四份：`templates/dashboard.html`、`templates/dashboard_teacher.html`、`templates/manage/classes.html`、`templates/manage/semesters.html`。

### 本轮新增的已知问题（未修，如实记录）

1. **桌面首屏折叠抖动**：`.ls-domains` 与 `.ls-tools` 在桌面首屏有一次折叠到展开的跳动，是让移动端默认折叠真正生效的代价。主内容"我的课堂"无跳动。属共享折叠组件的渲染时序问题，需 A 包处理。
2. **搜索框标签由视觉隐藏变为可见**：`classroom_app/lq_forms.py` 显式拒绝无可见标签的控件，这是冻结契约的既定无障碍决策，无法在页面侧退回。需产品确认这一视觉变化是否接受。
3. **材料库、归档、教务、我的四域**仍只完成页头开关接线，结构层未迁移。
4. **评估菜单与投票表单**两项组件化都卡在共享组件能力缺口上，需先开 A 包票扩展组件。

## S5：资源库 / 归档 / 教务 / 我的 四域结构迁移（2026-09-21）

四个域此前只完成页头开关接线，本轮做结构层。四个施工包并行推进，主任务复核合并。全部新结构只在 `manage-pages` 页族开关的 true 分支渲染，关闭分支保持原有 DOM。

### 分包与结果

| 包 | 域 | 迁移模板 | 真实页 e2e |
|---|---|---|---|
| L | 资源库 | 材料中心、课程、教材、教案（试卷/学习文档正文在 partials，投票正文全由 JS 生成） | 10 passed |
| R | 成绩与归档 | 归档流水线、考核计划表、签到统计表、教师评学表、教务期末材料 | 13 passed |
| X | 教务 | 三个对接页、教室查询、课时统计 | 14 passed |
| M | 我的 | 统一收件箱、账号找回、对接凭据、签名、签名工作流 | 13 passed |

**工作量口径更正**：归档域的 11 条路由实际只有 6 份模板，其中 5 条共用材料页（归 L 包所有）；「我的」域的 11 条路由有 6 条共用个人资料模板（归已完成的 C1 包，M 包正确跳过未触碰）。按路由条数记工作量会严重高估。

### 挖出并修复的真实缺陷

| 位置 | 缺陷 |
|---|---|
| 教务三个对接页（8 个覆盖层） | 完全没有 Escape 处理、完全不返还焦点；公文页有一个覆盖层是全页唯一键盘无法关闭的 |
| 教务教室查询 | 筛选芯片只切换高亮类，选中态无障碍属性从不生效 |
| 归档期末材料预览弹层 | 释放内嵌框架只挂在关闭按钮上，按 Escape 完全绕开，上一份成绩材料的文档残留在框架里 |
| 智慧课堂对接页（主任务修） | 与上述同构，但该文件不匹配任何包的归属规则而无人认领，导致三个对接页只修好两个 |

智慧课堂那处已做真实浏览器验证：按 Escape 关闭覆盖层后，焦点准确回到触发按钮。

### 组件契约增补（主任务，均附回归测试）

迁移过程暴露三处共享组件能力缺口，都在组件层补齐，详见 `docs/lq-components.md` 同日增补节：

1. **筛选芯片可以是真链接**（`lq_chip` 与 `lq_chip_row` 的 `href`）。URL 驱动的筛选用 `aria-current` 而非 `aria-pressed`，禁用时退回按钮。URL 复用既有校验器，脚本协议、数据协议、协议相对地址一律拒绝。
2. **输入框可以引用候选列表**（`lq_field` 的 `datalist`）。非输入控件传入会报错而不是静默丢弃。
3. **筛选栏内的字段共享行宽**。字段按契约是满宽，放进弹性容器后成为换行基准，导致每个筛选独占一行。实测教材页修复前 3 字段 3 行、每个 357px，修复后 2 行、宽度 172/172/356；教案页前后一致无副作用。

第一项最初**只落地了一半**：芯片行的 Python 校验器未同步键名，而新增测试全部直接调用单个芯片，芯片行在整个仓库没有任何 Python 单测。由 M 包发现，已补键名并新建 `tests/test_lq_chip_row.py`。

### 开关口径统一

管理页正文结构此前有两种写法。已裁定并统一为**页头与正文用同一个布尔表达式** `lq_pilot_enabled|default(false) or lq_family_enabled('manage-pages')`。试点开关按路由计算，非试点页恒为假，因此该析取项无副作用；反之若正文只认页族开关，试点开关单独打开时会出现页头新、正文旧的割裂。5 份模板已对齐，规则写入运行手册 §9。

### 配色裁定：收件箱来源色

服务端来源注册表登记了 `slate` 与 `rose`，两者都不在六个语义色之列。核查后：

- `slate` 在全站**没有任何芯片样式**，是死值。已改为 `neutral`，各处渲染结果不变，同时移除一半陷阱。
- `rose` 在教师首页确有样式，保留。液态玻璃分支渲染为 `neutral` 属**有意决定**：它是域强调色而非六种语义状态之一，语义色注册表不应被色相污染。

### 本轮回归（主任务合并后全量重跑）

| 入口 | 结果 |
|---|---|
| `tools/test_backend.py`（隔离全量） | Ran 3753 tests, OK (skipped=205) |
| `npx vitest run` | 78 files / 668 tests passed |
| `npx tsc --noEmit` | 通过 |
| `tools/ui/lint_lq.py` | blocking 为空 |
| `test_lq_*.py` | 178 OK |
| `locked_build.py` | `LQ_GRAPH=99f7bff9dfb2…0d64e136` |

### 未完成与已知问题

1. **运行时拼装的表格与表单迁不动**：公文列表（每行由 JS 拼装）、投票表单、归档签到统计的三张表都由控制器用模板字符串生成，服务端宏够不着；在客户端手写等价 DOM 等于绕过服务端校验。需先由共享组件提供客户端行/字段工厂。这是四个包反复撞上的同一堵墙。
2. **`/manage/academic/course-schedule` 窄屏横向溢出**：X 包报告为既有缺陷（关闭分支 550px、开启 533px），把该路由断言改判为「不得宽于关闭分支基线」，其余六条保持严格。主任务在缺少课表数据的运行时复测未能复现该数值，**该判断仅凭 X 包一方证据**，未独立证实。
3. **`tests/frontend/signature_scope_browser.test.cjs` 7 条中 6 条超时**，基线同样失败，且该文件未接入任何运行器。主任务修掉其中两处真问题（模板内联样式早被抽走导致夹具必然抛异常；正则剥除模板语法会把开关两个分支拼进一份文档而产生重复 id），但失败数未变，**仍有一处未定位的原因**，如实记录不声称修复。
4. **教务课时页**只迁了三个筛选，其三维课表控制器不属于任何包且不得修改。
5. 「每页」下拉在教务域**迁了又退回**：字段的块级布局会把分页摘要挤到上一行，由截图肉眼比对发现。

## S6：运行时拼装消费方迁移（2026-09-22）

前四个域反复上报的「运行时拼装迁不动」是一个**被误判的前提**。勘察后确认：客户端工厂早已存在于 `static/js/lq/{forms,tables,content,components}.js`，自带与服务端同源的校验，并由组件对照用例**逐例断言工厂产出与 Jinja 宏产出完全相等**。因此在控制器里调用工厂是走契约，只有手写字符串或 `innerHTML` 拼 `lq-*` 类名才是绕过。四个包不是撞了能力缺口，是没发现能力已存在。

### 三处消费方全部迁完

| 包 | 对象 | 结果 | 真实页 e2e |
|---|---|---|---|
| P | 投票新建/编辑表单（含动态增删的选项行） | 完成 | 7 passed |
| G | 公文列表表格 + 计数 + 分页 + 空态 | 完成 | 10 passed |
| A | 签到统计三张表（归档列表、课次矩阵、学生汇总） | 完成 | 11 passed |

三包均验证了关闭分支与基线的一致性：P 包用 `outerHTML` 字面量比对，G 包用 `git show HEAD:` 取回基线模板同上下文渲染比对（`baseline 15348 / current 15348, IDENTICAL`），A 包逐行比对三处旧渲染函数并对 `badge()→tone()` 做了 22 组穷举等价验证。

### 主任务修复的契约漂移（自查）

2026-09-21 我给服务端补的链接芯片与候选列表输入**未同步到客户端**，而对照夹具没有这两种形态，所以无人发现。已补齐客户端实现并逐项核对两端一致（链接形态用 `aria-current`、禁用退回按钮、脚本协议与协议相对地址拒绝、非输入控件引用候选列表报错），**并把两种形态加入对照夹具**：表单 26 passed、呈现类 47 passed。

### 新增组件能力：表头按列开启插槽

签到课次矩阵的列表头承载映射状态行与唯一的「复核课次」入口，没有表头插槽只能删掉该钩子。新增列定义布尔 `slot`，两端同步，插槽名 `col:<列key>`。**按列开启**，未声明的列表头 DOM 一字不差，全站既有表格零变化。配套 `.lq-table__colhead` 纵向排列与矩阵行表头吸附（背景取 `--ls-surface-1`，实测亮色 `rgb(255,255,255)`、暗色 `rgb(24,27,37)`）。

### 顺带修复的既有缺陷

| 位置 | 缺陷 | 处置 |
|---|---|---|
| 公文工具条 4 个下拉 + 搜索框 | 两分支均无可访问名称 | 加 `aria-label`；该包把基线违规钉成常量并三重断言，修复后清单清空、10 项仍全绿 |
| `.gwlist-card` / 搜索框 / 筛选芯片 | 硬编码白底，文字色随主题，暗色下近乎不可读 | 改用 `--ls-surface-1`；亮色取值同为白色，亮色像素不变，只修好暗色 |
| `.att-chips` | 无角色容器带 `aria-label`，ARIA 禁止 | 加 `role="group"`；同步把该包「两分支一致」的断言改为两分支均为空 |
| `manage_gongwen.js` 的 `setBusy()` | 直接写 `textContent`，会永久删掉按钮内的标签元素 | 纯加法修复 |
| `tests/frontend/attendance_reports_browser.test.cjs` | 基线即 2/10 失败：夹具剥除模板语法后两分支同时留在文档里 | A 包修复，现 10/10 |

### 三条被误判为缺陷、实为两端一致的行为

已写入 `docs/lq-components.md`「客户端工厂的使用边界」：字符串模式无法填插槽（与宏无 `{% call %}` 时一致）、控件强制可见标签、按钮总是写出 `aria-label`。

### 本轮回归

| 入口 | 结果 |
|---|---|
| `tools/test_backend.py`（隔离全量） | Ran 3753 tests, OK (skipped=205)，连续 4 次全绿 |
| `npx vitest run` | 78 files / 668 tests passed |
| `npx tsc --noEmit` | 通过 |
| `tools/ui/lint_lq.py` | blocking 为空 |
| 组件对照 `lq-forms` / `lq-tables` / `lq-presentation` | 26 / 26 / 47 passed |
| `locked_build.py` | `LQ_GRAPH=80ebc16555203…d45625d8` |

### 未完成与已知问题

1. **一次无法复现的后端失败**：全量套件首次运行出现 1 项失败，随后连续 4 次全绿。**我没有保留那次输出，无法定位到具体用例**，只能如实记录为疑似不稳定用例，未确认根因。
2. **签到矩阵单元格保持旧类名**：`att-cell att-status-*` 与 `[data-att-legend]` 图例共用同一套状态配色，只换单元格会与图例失同步，而图例样式不属该包。已加断言锁定图例仍为 7 项，漂移即失败。
3. **投票表单在新分支由两列变单列**：旧样式选择器特异度会压过组件类名，该包选择不挂旧类名。需要两列需另加栅格规则。
4. **学生汇总表 14 列在矩阵模式下比基线更早出现横向滚动**，未用页面级 CSS 掩盖。

## S7：顶栏收敛 + 导航菜单组件 + 页面背景图（2026-09-22）

### 交付

| 项 | 结果 |
|---|---|
| 学生端顶栏 | 收成**一行**：学习 / 职业（下拉）· 博客 · 通知 · 我的（下拉）· 外观。实测 1440 与 1600 宽均为 **72px**，此前 130px，六个入口中心同在一条基线 |
| 教师端 | 渲染「管理中心」独立按钮 + 博客/通知/我的/外观，**不渲染空菜单** |
| `lq_nav_menu` 组件 | 触发器 + 一级下拉，严格两层；键鼠交互、发光悬停、展开动画、配色/形状/尺寸/对齐可配 |
| 页面背景图 | 视口底部固定层，`contain` 保持比例，随视口变化，饱和度与面纱双重减色；默认取开屏图库；可关闭为纯色 |

### 主任务处理的四件事

**1. 冻结组件输出上的字符串替换（最高风险项，已消除）。** 顶栏包需要给两个菜单项挂 data 钩子，而条目词汇表是冻结的，它用 `|replace` 往渲染结果里注入属性，并**主动标为自己最高风险项、建议改用正规通道**。我采纳：给 Menu 条目加受限 `attrs`（只允许非保留的 `data-*`，组件自有键始终覆盖调用方），服务端与客户端同步，加入对照夹具（合法 1 例 + 非法 4 例），菜单两套用例 59 项全绿。随后删除替换，并把对应断言从「渲染文本包含某个属性顺序」改为「钩子与菜单项落在同一元素上」的 DOM 断言——原断言依赖的属性顺序没有任何契约保证。

**2. 悬停抢焦点（组件包自行发现并正确规避）。** 悬停打开菜单时既有控制器会强制聚焦首项、关闭时还焦点，鼠标划过顶栏即可夺走正在编辑的输入焦点。它没有改冻结的控制器，而是用协调层已有的两个可取消钩子在悬停路径上拦掉。我核实了钩子存在、用法正确，且组件文件内**零个键盘监听**，确属复用而非重写。

**3. 管理壳未接背景图（已修）。** 教师首页与全部管理页走另一个文档根，不继承主模板。背景图包因指纹门禁而正确放弃，我按字节插入（该文件混合行尾：427 CRLF + 15 纯 LF，文本方式读写会静默改字节而 `git diff` 看不出，只有指纹会炸），刷新 9 条指纹，实测管理页渲染出恰好一层且带图。新增回归测试同时钉住「每个文档根恰好一次」与「其余模板一次都不得有」。

**4. 宽屏上背景图可见面积有限（不改，设计约束）。** 1440 下两栏内容卡片几乎占满，图只在栏间空隙与顶栏/Dock 玻璃处透出。背景图包建议让内容面在开背景时转半透明，**我未采纳**：设计系统明确规定玻璃只给顶栏、侧栏、Dock、工具条与弹层，内容一律用不透明表面，改它会违反冻结原则并把可读性置于照片之上。是否让内容也用玻璃属设计系统层面的取舍，留给负责人决定。

### 安全相关的设计

- 背景图**不做用户上传**，只在既有开屏图库内选择，避免引入存储配额与文件校验面。
- 自定义颜色严格 `^#[0-9a-f]{6}$`，大写、简写、含分号一律拒绝且**不归一化**；颜色与图片只经受控自定义属性写入样式。
- Menu 条目 `attrs` 拒绝 `aria-*`、事件属性与 `data-lq-` 保留前缀。

### 本轮回归

| 入口 | 结果 |
|---|---|
| `tools/test_backend.py`（隔离全量） | Ran 3789 tests, OK (skipped=205) |
| `npx vitest run` | 78 files / 672 tests passed |
| `npx tsc --noEmit` | 通过 |
| `tools/ui/lint_lq.py` | blocking 为空 |
| 组件用例（nav-menu + menu-tooltip） | 59 passed |
| 顶栏真实页（开/关两分支） | 8 passed |
| 背景图真实页 | 12 passed（连跑两遍） |

### 已知项

1. **axe `region`（moderate，最佳实践）命中 `#lq-layers`**：协调层把传送宿主挂在 `<body>` 直下，页面地标是它的兄弟而非祖先，真实页面同样存在。两套用例均**钉死为恰好一条**而非过滤。修它要动协调层的传送语义，属独立票。
2. **全页持续模糊宿主实测 5 个**（顶栏、Dock、两个隐藏的模态背板、一个展开容器），超出计划的 ≤2 预算。顶栏内部为 0（硬断言）。多出的三个不在本轮包的文件范围内，如实记录。
3. **PostgreSQL 相关 7 条用例本地全部跳过**（无隔离集群），背景图新增了两列，发布前必须重做原生演练。

## S8：全站统一液态磨砂玻璃材质（2026-09-23）

负责人决定所有板块统一为玻璃，包括按钮与弹窗，只在不透明度与基色上分级。**这取代了此前「玻璃只给外壳、内容用不透明表面」的冻结规定**，也正是 S7 遗留的那道待决问题。

### 材质标度（主任务定义并落地）

五级：clear .22 / control .52 / chrome .58 / content .74 / raised .78（暗色各自略高）。完整定义与理由见 `docs/lq-components.md` 同日新增节。

**两条工程原则**：模糊只在材质边界出现（嵌套一律强制关闭，控件级永不自带模糊，toast 与 tooltip 为刻意例外）；对比度按页面背景层的真实边界论证，而非纯黑纯白——后者需要 .94 的不透明度，那已不是玻璃。

### 收紧的令牌与新增的守卫

为让半透明材质上的文字达标，收紧了 `--ls-ink-3`、`--ls-glass-muted` 与**全部 34 个语义前景**，其中 10 个此前在控件材质上不达标（最低 1.70）。调整只动明度。

`tests/test_lq_tokens.py::GlassMaterialContrastTests` 三条断言钉死：六调色板 × 明暗 × 四材质的正文与次级文字、全部语义前景在控件材质上、材质标度的顺序。**这些守卫是本轮的主要产出之一**——第一版只覆盖了墨色令牌，正因如此才漏掉了语义前景，由控件包发现。

### 三包交付

| 包 | 结果 |
|---|---|
| CTRL 控件 | 按钮、芯片、输入、开关走 control 级；主按钮**刻意保持不透明**；禁用态改用墨色与虚线边而非降透明度；占位符从 ink-3 改 ink-2（ink-3 的担保只在 content 级） |
| OVL 弹层 | 三级语言：焦点层 28px 圆角加遮罩模糊、浮层 20px、提示 10px，同级描边高光投影逐像素一致；零 JS、零模板改动 |
| PAGE 页面 | 盘点 42 条需改、2 处可删、8 条保留不透明；新增页面层材质别名并集中降级出口（含 `prefers-reduced-transparency`） |

### 主任务修掉的问题

1. **语义前景全面失真**（CTRL 上报）：success/warning 在最厚材质上都只有 4.10/4.00。我按材质真实边界解出并系统性调整了 10 个值，同时把守卫扩展到全部 34 个前景声明。
2. **`.lq-list` 与 `.lq-empty--card` 仍不透明**：内容组件里最后两个未入标度的，已对齐。
3. **暗色消息中心是一整片白板**：23 处写死浅色填充的规则在暗色下不翻转，正文对比度低至 1.08。逐条改为随主题的表面令牌并**保留每个原有的透明度**，实测低对比项由 10+ 降为 0。
4. **主色被当作文字色**：首页事项类型标签用 `--ls-primary`，而该令牌在暗色下是为做背景而定的浅色，实测 3.4。改用 `--ls-on-primary-soft`。
5. **教务同步按钮在暗色下仍是浅色药丸**：4.48，差 0.02。改为控件材质加主色文字令牌。
6. **一个夹具脚本自 S0–S4 检查点起就无法运行**：它按路径加载服务模块的层级比真实包浅一层，跨包相对导入越界。只有整套组件用例一起跑才会暴露，单跑 `lq-*` 永远碰不到。已修复。

### 一处需要如实说明的报告不实

页面包报告称"顺手修掉 `ui-system.src.css:4040` 写死的白色填充"，但**该文件在工作区中完全未被修改**，那处仍是原值。我逐条核实后自行修复。其余各包的声明经抽查属实。

### 本轮回归

| 入口 | 结果 |
|---|---|
| `tools/test_backend.py`（隔离全量） | Ran 3792 tests，1 项环境性失败（见下） |
| `npx vitest run` | 78 files / 672 tests passed |
| `npx tsc --noEmit` | 通过 |
| `tools/ui/lint_lq.py` | blocking 为空 |
| 全部组件用例（整份 config） | 通过（退出码 0） |
| 页面材质真实页 6 场景 | 6 passed |
| 迁移门禁 | ok（本轮未改数据库源码，沿用 20260923 演练） |

**那一项失败**是 `test_agent_acp_windows_process` 的并发句柄计数用例，在本机同时跑着服务与构建时失败于预热阶段，单独复跑两次均通过。与样式改动无关，如实记录为环境性。

### 已知未完成

1. `manage-pilot.css`、`report-card-pilot.css`、`preview.css` 未纳入本轮清扫，仍含页面级不透明表面。
2. 旧样式表中**仍有约 600 处写死的浅色填充**，全部不在暗色块内，属既有欠账。本轮只修了消息中心与首页实测可见的 23 处，其余需要单独一轮并按页验证。
3. 多数管理列表页的正文面板尚未迁移到组件，仍是旧表面，因此材质统一在那些页面上尚未体现。

## S9：背景图铺满 + 材质模块化 + 全面采用（2026-09-23）

用户反馈两点：背景图只占框选的一小块；其他板块出不来毛玻璃。**两者同源**——背景图用 `contain` 塞在一条 46vh 的带里，16:9 的图按高度缩放后只剩视口一半宽居中成一条，玻璃背后没有东西可透。

### 主任务修的核心

| 项 | 内容 |
|---|---|
| 背景层 | `inset: 0` + `background-size: cover`，面纱同步铺满。`cover` 同样保持比例，只裁切 |
| 材质缺件 | 内容材质此前只有半透明填充，既无模糊也无高光边，所以是"半透明白块"而非玻璃 |
| 采用式 API | 任意板块写 `data-lq-material="<level>"` 即获完整配方；尺寸与色彩仍由各自用自定义属性覆盖 |
| 统一交互 | `data-lq-interactive` 提供统一过渡、悬停微抬与发光、按下、聚焦环 |
| 顶层面板即边界 | 面板直接坐在背景之上，由它们把背景磨成玻璃；内部元素受嵌套守卫保护 |

### 三条由本轮确立的不变量

1. **填充与模糊是两个独立决定。** 我把 `.lq-surface` 移出模糊清单时，连它的半透明填充一起移除了——因为两者写在同一条规则里。已拆开。
2. **宿主数不得随数据增长。** 每个 `lq_card` 都带 `.lq-surface`，一面四十张卡片的墙就是四十个合成层。`.lq-surface` 的模糊改为按需开启，承载面板做边界。学习路径页实测由 22 个宿主降到 6 个。
3. **底色与文字色必须成对翻转。** `--gray-*` 写死十六进制、无暗色分支，而压在其上的文字色会翻转，实测出现 1.02 的深字压深底。已补暗色映射到既有语义令牌。

### 三包交付与各自的发现

**U3 遗留清扫**最有价值：把底色换成随主题后，无障碍违规从 17 暴涨到 224。它**没有绕过去**，而是还原文件、重新构建、跑同一套用例做基线对照，确认是自己引入的，并定位到灰阶令牌这个根因。盘点 906 条写死浅色填充，改 709、保留 197 并逐类说明；暗色近白面板 10→0，严重违规 21→8 且剩余逐条与基线签名一致。

**U1 组件**发现减少动效的取消规则会因选择器特异度失效（悬停规则带 `:not()` 后特异度更高），修了自己那份并**指出主任务那份有同样隐患**。已改为把位移放进自定义属性，特异度不再参与。

**U2 页面族**在主任务写的那段里找出三处缺陷：嵌套守卫只覆盖三个选择器且只挡一种写法、`[data-lq-calendar] .semester-calendar-panel` 是从未匹配过的死选择器、以及把列表项当成了边界。它还给出一个根因判断：**不是模糊宿主的半透明块直接压在背景照片上会被提亮**，关掉背景图即可让违规归零——这解释了为什么若干分区必须升格为真正的面板。

### 主任务补上的覆盖缺口

U3 如实标注其合成运行时只覆盖学生端，`classroom.css`（200 条）与 `manage_classes.css`（44 条）的改动**从未跑到**。新增 `tests/e2e/specs/lq-s9-teacher-dark.spec.ts` 与对应 config，在暗色加背景图下扫描教师端与三个管理页：断言无近白面板、无严重无障碍违规。首轮发现 `.btn-danger` 把填充红当文字色用（暗色下 3.8），改用语义前景后 **4 passed**。

### 本轮回归

| 入口 | 结果 |
|---|---|
| `tools/test_backend.py`（隔离全量） | Ran 3792 tests, OK (skipped=205) |
| `npx vitest run` | 78 files / 672 tests passed |
| 整份组件 config（干净单跑） | 通过（退出码 0） |
| 页面材质 12 场景 | 12 passed |
| 教师端暗色扫描 | 4 passed |
| `npx tsc --noEmit` / `lint_lq.py` | 通过 / blocking 为空 |

U1 曾报告整份组件 config 有 18 项模块加载失败并**主动声明无法做基线对照**。我在三包收工后干净单跑，退出码 0，证实是三包并发构建时的资源竞争，非回退。

### 已知未完成

1. `manage-semesters` 的 `nested-interactive`（2 处）：行容器带 `aria-current` 却包着可聚焦控件，属既有结构问题，需改模板。已**钉死签名**而非过滤。
2. U3 保留的 197 条不透明填充：语义色浅底 163、低透明度白纱 25、必须实心 9，均有理由。
3. 成就墙的卡片墙现在有可见的承载面板（此前卡片浮在空白上），属视觉变化，需产品确认。
