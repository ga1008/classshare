# 界面模板收敛与全站扫描（2026-10-13）

用户反馈九类问题（通知看不清、胶囊板块、表单越界、班级排序、搜索框不统一、未用玻璃、按钮与浮窗错位等）。本轮不逐页补丁：先归纳同类情况，把修复落在共享模板（LQ 组件 CSS / 页面材质别名），再把消费者迁到模板上。组件 API 见 [lq-components.md](lq-components.md) 末节，开发合同见 [lq-component-contract-2026-09-27.md](lq-component-contract-2026-09-27.md)。

## 归纳出的模板与根因

| 现象 | 根因（同类情况） | 模板 | 消费者 |
|---|---|---|---|
| 右上角通知看不清 | `material-boundaries.css` 把 toast 排除出采样，只剩 .58 raised 填充；且盖在顶栏上 | Toast：raised 填充 + thick 实时模糊（栈上限 3）；有 shell 顶栏时下移到顶栏下方；图标改为成对 tone 小胶囊 | 全站 `LQ.toast` / `showToast` |
| 大块内容被画成胶囊（学期卡、引导卡、课次导航…） | `control-geometry.css` 对所有 `.lq-btn` 强制 capsule；卡片式选择用了普通 Button | 选择卡：`lq-domain-choice` + `data-lq-shape="surface"` + `data-lq-component="choice"`；选中/按下为 soft primary + 主色描边；ghost 选择卡交给 `data-lq-selected` Surface 表达选中 | 学期列表、开课排课页签、教学引导选项/课次页签、课程课次导航、提示词池、首页日程提醒、考场选项、错题归集两处、评分题目页签 |
| 表单越出卡片（AI 配置） | 表单 grid 隐式 `auto` 列 + 不换行按钮组撑宽列，卡片 `overflow:hidden` 裁掉 | 表单自适应：载体内 form/fieldset 隐式列 `minmax(0,1fr)`、直接子项可收缩（零特异性）；`.academic-form-grid` 单列、`.academic-field` / 按钮组可换行 | 全部 `academic-form-grid` 与载体内表单 |
| 搜索框形态各异（九种） | 包装层自画一层框 + 共享输入本身胶囊 = 框中框；图标压住占位文字；部分没有图标 | 搜索：`.lq-input[type=search]` 自带放大镜（共享控件底色的首层，亮/暗/场景色调成对）；可选 `.lq-search` 包装只清除包装层底色并提供尾部槽 | 所有搜索输入（含 JS 渲染、lq_field、lq_filter_bar）；去掉 14 处私有图标/框 |
| 筛选栏标签忽明忽暗、排列错乱 | FilterBar 直接放在背景图上，标签取背景场景色，亮处褪色；字段 flex 换行参差 | FilterBar：无载体时自带 content 材质与成对前景；字段行改为 auto-fit 网格，多行时搜索与首行对齐 | 教案/考核计划/评学/签名审批等全部 `lq_filter_bar` |
| 部分板块仍是实心白 | 页面私有调色板（签到统计）、旧别名 `--surface-color`、旧暗色补丁强制 `surface-1`、`--exam-panel` 白底 + 私有 blur | 面板改用 Surface 组件或页面材质别名 `--lq-page-content/control`（玻璃，透明度/对比度偏好下回到不透明） | 签到统计、材料中心分组、过程材料侧栏、签名审批预览、开课各面板、试卷页、签名卡、旧 `.filter-bar` |
| 按钮与浮窗错位（首页“评价”） | 全宽 `<details>` + `right:0`，浮窗贴到整行最右；嵌套在内容载体中失去模糊、被后续面板盖住 | 展开浮层：`details.lq-disclosure-popover`（贴合触发器，`data-lq-align`/`data-lq-placement`）、raised 面板保留采样、含打开浮层的载体临时抬升 | 首页评价菜单（两版首页）、作业详情“更多”、课堂“管理课次” |
| 班级默认排序 | 默认按系别 | 默认“最近导入/编辑优先”：`max(学生导入时间, 班级 updated_at, created_at)`，服务端首屏与前端一致 | 班级管理；芯片行为可见控件，真实 select 仍是值载体（课程页先例） |

## 扫描方法与结果

在隔离合成运行目录（P03 SQLite，不连真实库）以 1440×900 加载 45 个路由（42 个管理页 + 首页/课堂/个人中心/博客），检测：控件越出卡片、高度 >56px 且半径 ≥40px 的按钮、不透明（α≥.9）面板、搜索输入结构。亮、暗各一轮。

- 修复前（亮）：胶囊块 2、不透明面板 9 处、搜索形态 9 种（其中 6 处框中框、4 处缺图标、1 处图标压字）。
- 修复后（暗，含新增路由）：管理页越界 0、胶囊块 0；剩余为有意保留项（见下）。

保留项：签名预览格是白底内容槽（签名墨迹需白底）；课堂研讨室“广播模式”为既有不透明阅读面；首页“去哪里”链接是透明 link 变体，半径不可见。

## 验证

- 后端隔离：`tools/test_backend.py` `test_lq*` 224 项、`test_manage*` 38 项、签到相关通过。
- 前端单元：vitest 84 文件 729 项通过。
- 组件浏览器：toast、S8 浮层材质、content、forms、selection、presentation 定向 175 项通过（含 44px 触控回归修复）；全量组件套件结果见提交说明。
- 实页：学期卡、AI 配置长选项、班级排序、签到统计、教材/教案筛选、材料搜索、首页评价浮窗、通知（亮/暗）均在正式构建下截图核对。

迁移台账 `docs/lq-migration-registry.json` 中 7 个共享源文件的指纹在逐行审阅本轮 diff 后更新（工作树 CRLF 字节，与原登记口径一致）。
