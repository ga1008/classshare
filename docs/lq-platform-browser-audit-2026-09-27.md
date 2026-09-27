# 全平台组件浏览器审计运行说明

本审计读取真实页面 DOM，并保留 `pending`、`unknown` 和场景缺口。测试进程成功只代表清单写出；默认不宣称功能、视觉、无障碍或全平台验收通过。

## 运行边界

使用独占的 `.codex-temp` 合成运行目录。`lq-s3` fixture 强制检查 `uiV3Synthetic/lqS3Synthetic`、数据库路径和 HTTP health 返回路径；浏览器及合成服务器都限制 loopback 通信。独占锁避免相同账号的登录互相注销。不要使用复制真实数据库的旧初始化脚本，不要在同一 fixture 并行启动多个登录 runner。

静态源码清单覆盖 198 个 HTML 模板，另含原生 JavaScript、模板字符串、创建工厂和 React JSX。路由 manifest 在内存重建原台账，补充 `/manage/me` 的 7 个动态注册入口，共 102 个源码方法/路径候选；不会改写原迁移验收状态。角色候选仍须由具体权限场景确认，接口路由不作为普通页面数量。

```powershell
venv/Scripts/python.exe -B tools/ui/audit_lq_components.py --output docs/lq-platform-component-audit-2026-09-27-remaining.json --markdown docs/lq-platform-component-audit-2026-09-27-remaining.md --summary .codex-temp/lq-component-audit-summary.json --findings .codex-temp/lq-component-audit-findings.json --browser-routes docs/lq-platform-browser-routes-2026-09-27.json
venv/Scripts/python.exe -B -m unittest tests.test_lq_component_audit tests.test_lq_declaration_migration -v
npx playwright test --config tests/e2e/lq-platform-audit.playwright.config.ts --list
```

首次建立新运行目录时，顺序运行下列命令。服务器使用工作树的正式资源映射，因此改完产品源码后必须先正式构建，再重启合成服务器；只编译 CSS 不能验证新的 JavaScript。

```powershell
venv/Scripts/python.exe -B tests/e2e/scripts/prepare_ui_v3_runtime.py --runtime-root .codex-temp/lq-platform-owned-runtime
venv/Scripts/python.exe -B tools/ui/prepare_lq_s3.py .codex-temp/lq-platform-owned-runtime
venv/Scripts/python.exe -B tools/ui/prepare_lq_pages.py .codex-temp/lq-platform-owned-runtime
venv/Scripts/python.exe -B tools/ui/prepare_lq_platform_audit.py .codex-temp/lq-platform-owned-runtime
# 在独立终端启动，并使用发布计划要求的 LQ 功能开关。
venv/Scripts/python.exe -B tests/e2e/scripts/serve_ui_v3.py --runtime-root .codex-temp/lq-platform-owned-runtime --port 8341
```

```powershell
$env:P03_RUNTIME_ROOT=(Resolve-Path '.codex-temp/lq-platform-owned-runtime').Path
$env:LQ_PLATFORM_AUDIT_PORT='8341'
$env:LQ_PLATFORM_AUDIT_OUTPUT='.codex-temp/lq-platform-browser-audit-final'
npx playwright test --config tests/e2e/lq-platform-audit.playwright.config.ts
```

默认 4 个角色 × 390/1440 宽度 × 浅/深色，共 16 个顺序分组。可以设置 `LQ_PLATFORM_AUDIT_ROLES`、`LQ_PLATFORM_AUDIT_WIDTHS`、`LQ_PLATFORM_AUDIT_APPEARANCES` 和 `LQ_PLATFORM_AUDIT_ROUTE` 限制重跑；报告会显示被过滤的路由，不能把子集当成全量。完整响应矩阵可将宽度设为 `320,390,768,1024,1440`。设置 `LQ_PLATFORM_AUDIT_ENFORCE=1` 才会把未执行页面、未归属控件、缺规范类或弹层内容面、意外横向溢出、运行中源码变化、浏览器异常作为失败；即使该门禁通过也仍需业务场景和视觉审阅。

## 输出和状态

每完成一个地址就写出 `<role>-<width>-<appearance>.json`。记录包含主文档、iframe、开放 ShadowRoot 的可见/隐藏控件、语义类别、禁用/繁忙/展开状态、规范类、结构边界、横向溢出和源码候选位置。源码 SHA 在分组开始与结束对照，运行期间改动的文件明确列出。

`data-lq-component` 单独存在并不足以归属共享组件；必须检查该类型的规范类。`layer` 只负责定位/遮罩，检查其内部共享 `surface`，不要求遮罩本身有填充。`content-slot/handle/chip/domain` 独立登记，不为了凑覆盖强制变成按钮或玻璃面板。源码到 DOM 的匹配仅按 id/领域类提出候选；同名复用保持 `ambiguous-pending`，没有匹配保持 `unknown`，不伪造一一对应关系。

路由返回登录或状态页不能冒充目标页；仅当最终地址也在源路由台账中且模板身份相同时登记为明确路由别名。POST 页面、异常文档、缺少实体的 lessondoc/attendance 编辑器以及动态弹层保留场景缺口。已有领域套件列在路由 manifest 的 `domainScenarioSuites`，应在同一隔离 fixture 顺序执行对应交互并补充证据。GET 清单不会任意提交业务表单。

## 首轮旧构建基线

2026-09-27 在专属合成运行目录、8298 端口执行教师 1440px 浅色单组。正式 asset manifest 当时仍指向旧 JavaScript 构建，因此此轮只用于发现遗漏，不是本次修改的验收。

- 66 个路由候选扩展为 70 条记录：61 条 DOM 可达待验、3 条路由未覆盖、5 条缺合成场景、1 条开发入口未执行。
- 8,869 个跨页重复的控件实例，1,266 个未归属，其中 290 个可见；浏览器 `pageerror` 为 0。这个数不是独立控件数量或完成比例。
- 旧课堂、仪表盘的动态按钮/卡片，公共反馈 tablist、AI 工作区隐藏入口是主要缺口。
- 随后补齐了 3 个编辑器及独立考试样例；移除了误造的 `member-panels/workspace` 地址，只展开路由实际允许的 4 个 panel key。
- 此旧基线当时缺少 lessondoc pack、attendance report 和可渲染 HTML material。终版准备脚本已补真实合成ID，生成并绑定课件 HTML；签到 PDF 下载仍需专门领域场景。它们不会使用猜测的实体 ID 或把错误响应当作通过。

原始结果保留于 `.codex-temp/lq-platform-browser-audit/teacher-1440-light.json`。终版运行必须使用另一个输出目录，避免覆盖此基线。

## 汇总与性能宿主

`venv/Scripts/python.exe -B tools/ui/summarize_lq_platform_audit.py .codex-temp/lq-platform-browser-audit-final --output .codex-temp/lq-platform-browser-audit-final/summary.json` 汇总实际执行缺口、重复控件归并、横溢和可见模糊宿主（含有生成内容的 `::before/::after`）。叶子模糊单独列出；宿主计数是当前DOM状态快照，不能替代旧硬件帧率或动态业务场景。

汇总同时读取 Playwright 执行结果：登录前或登录期间失败而未写出路由 JSON 的分组，仍列为 `executionFailures`。`routeGaps=0` 不能代替完整 16 组执行。横溢诊断还记录超出视口的元素几何候选；内部滚动内容可以出现在候选中，仅文档本身横溢才触发门禁。

## 已执行补充合同

`docs/lq-platform-supplemental-contracts-2026-09-27.json` 记录实际命令、日志摘要及相关源 SHA：认证/首次设密/找回/注册/会话恢复 45 项，材料不可用返回 8 项，居中异常页面 SSR 6 项，均通过。材料返回最小内存 fixture 会记录可恢复的偏好表缺失日志，不影响这 8 项断言。它们为非 GET/异常响应提供具体证据，不当作所有浏览器业务交互的替代。

最终资产图 `9c124519c530160cf382ad06a9c3f37118f362406e356dd27cd8d2d95a51338f` 下，`lq-declarative-contracts.spec.ts` 的 3 项正式 CSS 合同通过：原生 hidden/表单语义、危险动作状态、共享 Surface 选中态的浅深色/透明关闭以及嵌套未选中表面隔离。产物为 `.codex-temp/lq-declarative-selected-final-settled/`。首跑同步采样选中边框遇到原有 0.16 秒过渡的中间颜色；改为等待预期 CSS 颜色，未取消过渡或改变产品，首跑 trace 保留于 `.codex-temp/lq-declarative-selected-final/`。

用户课程材料的 `/materials/render/{id}/` 与 LessonDoc `/materials/lessondoc-editor/{id}/preview` iframe 独立标为 `document-content`，全部记录仍输出；保留其原文按钮、主题工具和卡片。平台编辑器外壳和 DOCX/PDF 预览工具 chrome 仍属于 `platform-ui`，不能用内容边界掩盖平台控件遗漏。

## 第三轮候选矩阵（不是终版验收）

原始目录 `.codex-temp/lq-platform-browser-audit-final` 保持原路径，以保留 JSON 内截图与 trace 的绝对引用；该目录是修复过程中的候选证据。正式终版另写 `.codex-temp/lq-platform-browser-audit-final-clean`，使用 `LQ_PLATFORM_AUDIT_ENFORCE=1`。

本候选实际执行 16 组，15 组产出 462 条记录（438 条 GET DOM、24 条认证 POST 合同引用）；学生 1440 深色组在服务重启期间登录遭遇 `ERR_CONNECTION_REFUSED`，没有被补写成成功。运行中源码变化逐组保留。聚合发现 12 种类名/状态组合问题、4 个路径上的 10 条横溢记录，无叶子模糊宿主，单帧最多 5 个可见背景模糊宿主；这些是待修复候选结果。截图另揭示 LessonDoc 深色移动工具条的原白底边界，不能由 DOM 归属检查代替视觉审阅。

## 冻结资产的完整严格矩阵

2026-09-27 23:38 至 23:56（Asia/Shanghai），使用 8299 端口、专属 `.codex-temp/lq-platform-20260927-runtime` 合成数据及 `LQ_PLATFORM_AUDIT_ENFORCE=1` 完成 16/16 组，耗时 18.4 分钟，无跳过、重试、失败或 flaky。机器可读摘要为 [lq-platform-browser-final-2026-09-27.json](lq-platform-browser-final-2026-09-27.json)，原始结果、截图和 Playwright 结果位于 `.codex-temp/lq-platform-browser-audit-final-clean/`；此前各轮候选证据仍保留。

| 实测范围或门禁 | 结果 |
|---|---:|
| GET DOM 观察记录 / 不同实际 GET 地址 | 472 / 112 |
| 认证 POST 合同引用（不是浏览器表单提交） | 24 |
| 平台 UI / 用户文档内容 frame | 524 / 12 |
| 跨页重复的平台组件实例 | 56,724 |
| 未执行路由 / 未归属或结构不合规控件 | 0 / 0 |
| 文档横向溢出 / 叶子背景模糊 frame | 0 / 0 |
| 浏览器异常 / 分组执行失败 / UI 源码变化 | 0 / 0 / 0 |
| 单 frame 可见背景模糊宿主上限（包括有效伪元素） | 5 |

同一资产图 `9c124519c530160cf382ad06a9c3f37118f362406e356dd27cd8d2d95a51338f` 覆盖整轮：570 个纳入审计的 UI 源码及资产 SHA 在全部 16 组完全相同；正式 CSS SHA256 为 `d38c5a9ec0710b57b3945bdada2320bdae16d15731752db1fed4ec9e5329f208`，asset manifest SHA256 为 `d08a46ec02192fe2623354d40ea67e919dc3e867b6262622d67ba9150c96dd13`。这不是整个工作树的冻结声明；同时由其他任务修改的非 UI 后端文件不在此范围内，合成服务器未在运行期间重载。

运行前刷新后的源码清单为 512 个作者源码文件、198 个 HTML 模板、5326 个候选入口。所有运行时映射的候选 id/文件/行号与该清单一致，不匹配数为 0；复用造成的映射歧义、未知输出和原迁移状态仍按原样保留。静态 `pending/unknown` 没有因本轮通过自动改为完成。

本轮通过的是明确范围的结构浏览器门禁：页面初始 DOM、隐藏控件、frame、开放 ShadowRoot 的共享所有权、表面边界、文档横溢和叶子模糊。它不能代替所有动态业务操作、无障碍和逐像素视觉验收；24 条 POST 仅引用已执行认证合同，12 个用户文档 frame 保持 `document-content` 独立边界。模糊宿主计数不能作为旧电脑硬件的 FPS 测量。LessonDoc 浅色辅助文字的后续对比度复核或修复应绑定其自身版本并另做领域验证，不覆盖这轮资产图的完整矩阵证据。
