# 项目开发要求

先理解现有架构、权限和业务闭环，再计划和修改；保护原有功能、并发性能、用户数据与其他未提交工作。界面保持现代、自然、清晰，不能为局部视觉破坏布局或交互。

任何页面、按钮、输入、菜单、弹层和工具栏改动，先阅读 [液态玻璃组件开发合同](docs/lq-component-contract-2026-09-27.md) 与相关 [组件 API](docs/lq-components.md)；改动或新增共享模块前先查 [LQ 模块使用记录本](docs/lq-module-registry.md) 评估全部消费者，改完运行 `npm run inventory:lq-modules` 更新记录本并在 `docs/lq-module-registry.notes.json` 维护设计说明。复用 LQ 组件、变体和槽位；领域 CSS 只负责布局和数据表现，不再创建独立按钮/面板材质或第二套动效。保留原生表单、IME、焦点、权限、幂等、冲突、草稿与取消合同。

源码声明不等于验收。更新全平台源码审计，使用隔离合成运行环境和正式构建做相关业务、响应式、状态及性能验证；不要连接真实业务库运行测试。部署须有用户授权，使用现有部署流程，保护 `/lanshare/data`，分别报告上线核验与 Git 推送结果。

临时产物必须在创建时定义退出清理：单元测试统一使用 `python tools/test_backend.py`，夹具使用 `TemporaryDirectory` 配合 `enterContext` / `enterClassContext`，数据库连接、补丁与子进程先关闭再删除目录。一次性脚本和可重建副本放本轮自有的 scratch，报告、截图、发布包、迁移 dump、恢复归档和验收再现输入单独保留；不得把部署/恢复命令包入统一 TEMP 自动清理环境，不得按目录名、年龄或 `synthetic` 标记批删 `.codex-temp`。强制结束后无法确认所有权或进程状态的残留先保留。具体边界见 [临时产物生命周期](docs/temporary-artifact-lifecycle-2026-11-12.md)。
