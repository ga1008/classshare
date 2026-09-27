# LQ React 组件与管理顶栏验收（2026-09-27）

本记录覆盖真实 React 组件和正式 CSS 的隔离浏览器验收，不替代应用服务器上的完整页面矩阵或部署验收。Fixture 在内存中编译实际 TSX，原生 DOM、事件、File/FormData 与浏览器层管理保持真实；网络和业务数据由隔离 fixture 提供。

## 资源与结果

- 首轮正式资源图：`1696887b96374ff6870c1b71415487dc6157d89d6a78fe00b13006c84803d9b3`。
- `static/css/tailwind-app.css` SHA-256：`57b73e0786a366ad02048925b848161a4567c2b54fd0de7523de17ea2365f381`。
- `static/css/classroom_workspace.css` SHA-256：`926bb7fdb8cd0b692a2b0899cc890c3409886da5a01ac526fad8bf722ed1e7c7`。
- 本轮未使用源 CSS 注入开关，未重建共享资产。

| 检查 | 结果 | 关键保障 |
| --- | --- | --- |
| `lq-react-dialog.spec.ts` | 12/12 | StrictMode、原生/React 混合层级、焦点恢复、关闭否决、中断重开、卸载和异步竞态 |
| `lq-react-workspaces.spec.ts` | 11/11 | 原始业务节点/草稿/顺序、外部编辑器与日历交接、受控原生字段、共享 Card、raised 对话框、浅深与手机布局 |
| `lq-react-command-islands.spec.ts` | 5/5 | 作业/考试/消息命令准确传递、原生禁用、链接导航、题目标记/计数/滚动、4 种截图组合 |
| `manage-pilot-adapter.spec.ts` focused | 2/2 | 8 个真实 SSR 顶栏在 1024/1440px 不换行；1024px 触屏 10 个操作全部可见且命中区域至少 44×44px |
| 最终类型检查 | 通过 | `npm run typecheck` |

首轮 28 项 React 测试和两项顶栏测试全部通过。人工截图检查额外发现消息概况备用分支的旧白色渐变与深色文字 token 冲突。该分支已改为复用 `LqCardFrame`，保留 section、布局和业务状态；新增共享 surface、无独立渐变和无模糊宿主的断言。修复后该文件 5/5 再次通过，四张截图重新生成并检查；只改变 React 源码，CSS 哈希保持不变，由发布流程重新构建 frontend/assets。

课堂 Card 另补四张无遮罩的独立截图，对应工作区四个组合再次 4/4 通过。截图均检查了文字、图标、按钮布局、裁切、水平溢出和玻璃层次。

## 材质与交互边界

- `DialogContent` 默认使用共享 `raised` 外壳与 `.lq-scrim`；实际样式测得外壳 `blur(24px)`，根容器和 scrim 无 blur。透明关闭时外壳无 blur。保留原有定位、尺寸与业务生命周期。
- 四个命令岛使用 `LqButton`；原 CustomEvent 命令、disabled、锚点与题目滚动保持原接口，不增加交互状态机。
- `LqCardFrame` 从既有 `contentProps('card')` 获取根契约，用于保留领域内容结构的 list item/section；课堂任务卡与消息备用面板无独立 backdrop-filter。
- `ls-filter-group` 仅为 flex/tablist 组合；`dashboard-evaluation-menu` 为原生 details 定位壳，其内部 popover 已共享 raised。二者不应被当成需要额外玻璃的独立面板。
- 顶栏只在已有 1024–1279px 紧凑区间减少横向间距和内边距。classes/1024 从 110px 恢复到 58px，全部动作及 44px 触控目标保留。

## 可复现命令

```powershell
npx playwright test --config tests/e2e/components/playwright.config.ts lq-react-dialog.spec.ts lq-react-workspaces.spec.ts lq-react-command-islands.spec.ts --output .codex-temp/lq-react-final-1696887b-tests
npx playwright test --config tests/e2e/components/playwright.config.ts manage-pilot-adapter.spec.ts --grep 'eight real SSR|compact 1024px' --output .codex-temp/lq-topbar-final-1696887b-tests
npx playwright test --config tests/e2e/components/playwright.config.ts lq-react-command-islands.spec.ts --output .codex-temp/lq-react-command-surface-final-tests
npx playwright test --config tests/e2e/components/playwright.config.ts lq-react-workspaces.spec.ts --grep 'shared components' --output .codex-temp/lq-react-cards-final-tests
```

## 本地证据

- 四岛：`.codex-temp/lq-react-command-islands/{light,dark}-{1440,390}.png`（4 张）。
- 工作区：`.codex-temp/lq-react-workspaces/{dashboard,classroom}-{light,dark}-{1440,390}.png`（8 张）及同名 `-material.json`。
- 独立 Card：`.codex-temp/lq-react-workspaces/classroom-cards-{light,dark}-{1440,390}.png`（4 张）。
- 结果索引：`.codex-temp/lq-react-final-verification.json`。

这些证据证明本批材质、几何与已列交互契约；没有据此宣称所有旧硬件的帧率或全部平台业务都已验证。
