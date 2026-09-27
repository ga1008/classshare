# 液态玻璃性能优化验收（2026-09-27）

本轮保留主玻璃的模糊半径、透明度、饱和度、边缘高光和现有交互，减少重复过滤、重复 DOM 查询、重复几何写入及同步存储。没有修改后端、数据库、权限、账号偏好格式或业务请求，也没有新增按设备型号强制关闭玻璃的策略。

## 变更

- `static/css/ai_workspace.css`：普通按钮和标题标记使用所在窗口提供的霜面，移除逐控件的 8px backdrop-filter。原填充、高光、描边、阴影和操作状态保留；主窗仍为 34px，独立历史抽屉仍为 32px。
- `static/js/ai_workspace_window.js`：鼠标/触屏移动只记录最新坐标，每帧合并一次几何更新；数值未变化不写样式，结束手势才同步保存位置。松手末点、取消、失去捕获、关闭、最大化、视口变化、销毁和 pagehide 均刷新待处理坐标并释放待帧。继续使用真实 left/top/width/height，保持玻璃采样及窗口边界。
- `static/js/lq/shells.js`：顶栏滚动每帧只更新两个阈值状态，不再执行整套抽屉布局、遍历面板、查询操作区或处理焦点。resize/refresh 保留原有完整同步；销毁取消待帧并还原属性。
- `static/js/lq/theme.js`：主题桥仅在 iframe 相关变化时发现/清理应用内嵌页面，普通文本和控件更新不再全页扫描。新增 iframe、撤销标记、sandbox/srcdoc/跨域变更、移出原文档仍重新验证/清理。使用原根的 contains 检查，覆盖仍 connected 的跨文档或 ShadowRoot 移动。
- `static/js/lq/layer.js`：浮层坐标及翻转状态未变时不重复写入；没有需要锚点定位的浮层时，滚动不再调度空的定位帧。父子菜单仍依次定位，保留焦点、滚动锁及 portal 所有权。

生产构建同时刷新了已提交源样式对应的 `static/css/tailwind-app.css`。原编译产物落后于 HEAD 中的玻璃下拉与提示可读性改动；这些源规则没有在本轮重新设计。

## 可重复的性能证据

基线为 `edb69dc3ccd7b545b0272ca069e8b43044922ee9`。实验以本地 route fixture 加载真实模块，无应用服务器、数据库或外部请求；固定 12,000 个内容元素、4 个应用 iframe，1440/390px，Chrome 154.0.8037.57，每场景 5 个样本。JS 对比固定 HEAD 的 CSS 和主题 bootstrap。

| 场景 | 优化前 | 优化后 |
|---|---:|---:|
| 300 批普通 DOM 更新：整页 iframe 查询 | 300 | 0 |
| 200 次顶栏 scroll：面板查询 | 200 | 0 |
| 同上：桌面/手机操作区查询 | 400 / 200 | 0 / 0 |
| 同上：无变化的 classList.toggle 调用 | 200 | 0 |
| AI 普通窗口实际模糊宿主，桌面/触屏 | 9 / 8 | 1 / 1 |
| AI 历史从 3 条增加至 180 条 | 控件分别创建过滤层 | 始终 2 个宿主 |
| 同帧 40 次拖动事件 | 每事件写几何并保存 | 一次绘制、2 次变化的属性写入；期间 0 次保存，结束 1 次 |

4 倍 CPU 降速实验的中位数：300 批主题观察回调累计 31.9 → 2.3ms；200 次滚动事件派发处理桌面 6.3 → 0.8ms、手机 5.2 → 1.1ms。滚动优化后的延迟状态另外等待帧并检查真实 scrollTo(0/40/100/0)。这些数字只描述该实验的 JS 开销，不能换算为整页提速倍数、GPU 帧率或旧电脑实测。

原始来源、文件 SHA256、限制和测量脚本保留于 `.codex-temp/lq-performance-20260927/{before.json,after.json,comparison.json,measure-runtime.cjs}`。三个被测运行时模块的最终源文件哈希与 after 报告核对一致。确定性工作量计数是主要证据，毫秒数受 JIT 和机器调度影响。

## 回归

- `npm run test:lq`：22 文件，172 项通过。
- `npx vitest run frontend/src/lib/lq-theme.test.ts -t 'synchronous theme|explicit app-only'`：12 项通过，包含动态 iframe 和安全边界。
- 共享 shell、layer、nav-menu、material-boundaries：123 个不同用例。初跑 122 通过，1 项旧测试要求 scroll 同步重建整个 shell；改为保留 refresh/resize 的完整读写顺序断言，由新增测试验证 scroll 的按帧及焦点/草稿契约。相关 3 项复验全部通过。
- AI window：24 项通过，涵盖 8 方向缩放、边界限制、非模态窗口外点击/选择/滚动、重开、账号内状态和手势中断。
- AI material performance：浅/深 × 桌面/触屏 4 项通过。真实历史控制器从 3 条增至 180 条；宿主数不增长，历史选择、草稿保留和窗外操作通过，关闭透明时过滤层为 0。
- 最终构建后另复验 5 项材质/滚动用例及 1 项不可变资源交付测试，6 项全部通过；包含原生模块单例、浏览器缓存和旧页面延迟导入。
- 共 152 个不同浏览器用例完成通过验证。前后截图位于 `.codex-temp/ai-workspace-material-before/`、`.codex-temp/ai-workspace-material-after/`；已查看浅深及触屏截图，主窗霜面、高光、边缘和文字仍保留。
- `npm run typecheck`、`npm run build`、`npm run check:lq-size`、`git diff --check` 通过。LQ 原生入口实际 gzip 17,757 bytes，预算 18,432；静态图 456 个文件，源哈希和 gzip 校验通过。

最终静态资源图：`a81d1bebc4973d76c1fc0d415142847b3e0f028663e23ccf0528471d44aa9e21`。

## 已有问题与边界

- 完整 `lq-theme.test.ts` 中 4 项偏好面板测试因模拟 DOM 缺少 `classList.contains` 失败。在单独取出的 HEAD 测试文件上也复现相同失败；本轮未改偏好面板或选择器实现。
- `npm run lint:lq` 仍报告 `templates/dashboard.html: reviewed-source-changed`。该模板、迁移台账、lint 工具及豁免文件均与 HEAD 一致，未为使门禁变绿重写审阅哈希。
- 构建暴露的已提交下拉/提示样式与旧编译产物差异属于既有产物滞后。全量项目测试未重跑，不能把上述定向通过表述为全项目通过。
- 既有 AI 深色关闭透明时部分填充仍半透明，以及触屏历史列表高度偏小，不由本次删除控件过滤层引起；未扩展到这些视觉布局变更。
- 未做实体旧电脑、老版本浏览器或 Safari 实机验收。CPU 降速和触屏模拟不代替真机；未执行线上部署或 Git 推送。

## 回退

按上述 5 个生产源文件（一个 CSS、四个 JS）的本轮差异回退，重新执行 `npm run build`，以完整静态资源图交付。无数据迁移、账号偏好转换或服务端回滚步骤。
