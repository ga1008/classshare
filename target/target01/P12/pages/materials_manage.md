# P12 页面迁移工作单：materials_manage

## 页面与业务边界

- 页面入口：`templates/manage/materials.html`
- 后端边界：
  - `classroom_app/routers/materials.py`
  - 材料库、AI 导入、最终材料、导出相关服务与后台任务。
- 旧脚本：`static/js/materials_manage.js`

## 旧脚本职责清单

- 材料库列表、搜索、筛选、分页和状态展示。
- 教材、课程、章节、学期上下文选择。
- 材料上传、资料解析、AI 导入候选、生成状态轮询。
- 最终材料生成、预览、下载、导出。
- 批量操作、表单校验、toast、modal、loading、错误提示。
- 与后台 AI 任务、材料生成 worker 的状态轮询存在耦合。

## P12 目标状态

- 新增页面级 island：`frontend/src/islands/materials-manage-page.tsx`。
- 保留 `/manage/materials` URL、后端 API 和业务表结构。
- React 侧拥有页面主状态，旧脚本不再重复绑定筛选、上传、AI 导入、最终材料生成按钮。
- AI 导入和最终材料生成必须继续依赖后台可恢复状态，不能因为前端迁移改成只存在浏览器内存的临时状态。
- 与 P11 后台任务台账方向保持一致：前端展示任务状态，不创造无法恢复的新任务状态。

## 迁移拆分顺序

1. 建立 `materials-manage-page` island 和 manifest entry。
2. 迁移只读 bootstrap、材料列表、筛选、分页。
3. 迁移上传入口和基础校验，继续使用现有文件上传/材料 API。
4. 迁移 AI 导入候选、任务状态轮询和错误状态。
5. 迁移最终材料生成、预览、导出入口。
6. 删除旧脚本中已经被 island 覆盖的事件绑定和重复 API 调用。
7. 最终从 `templates/manage/materials.html` 删除 `js/materials_manage.js` 直挂。

## 可删除旧代码的硬性条件

- [ ] React island 覆盖材料列表读取、筛选、分页。
- [ ] React island 覆盖上传入口和错误提示。
- [ ] React island 覆盖 AI 导入任务状态显示。
- [ ] React island 覆盖最终材料生成、预览、导出入口。
- [ ] 后台任务状态以服务端为准，刷新页面后仍能恢复。
- [ ] 删除旧事件绑定后，按钮点击只触发一次 API 请求。
- [ ] 不改线上材料表结构。
- [ ] 不批量重写、清空或重算线上材料。

## 测试与验收

- 单元 / 集成：
  - `npm run typecheck` 通过。
  - `npm run build` 通过。
  - 添加 materials manage authenticated smoke test，断言 `materials-manage-page` 注入，并且旧脚本删除后关键 DOM 或 React 容器仍存在。
- 浏览器回归：
  - 使用 `.codex-temp` 测试数据根。
  - 教师进入 `/manage/materials`，能看到材料列表。
  - 筛选、搜索、分页可操作。
  - 上传测试文件后能看到明确成功或失败状态。
  - 启动 AI 导入后能看到排队、处理中、成功或失败状态。
  - 页面刷新后 AI 导入状态仍能恢复。
  - 最终材料可预览或导出。
- 数据安全：
  - 不对线上材料库执行上传、AI 导入、删除、批量生成或导出压力测试。
  - 测试文件放在 `.codex-temp`，不污染生产数据目录。

