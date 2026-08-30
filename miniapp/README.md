# LanShare 小程序端（uni-app Vue3）工程规约

> 推进路线真源：`docs/miniprogram/IMPLEMENTATION-PLAN-2026-08.md`（M0–M6 里程碑，做功能前先看）。
> 架构设计：`docs/miniprogram/DESIGN.md`。本文只讲"怎么写代码、怎么发版"。

## 技术栈与硬约束

- uni-app（Vue3 + Vite + TS）+ **pinia@2**（v3 与 uni 别名不兼容，禁止升级）。
- 构建：`npm run build:mp-weixin` → `dist/build/mp-weixin`；类型检查：`npm run type-check`（提交前必须过）。
- 预览/上传：`npm run mp:preview` / `npm run mp:upload -- <ver> "<desc>"`（miniprogram-ci，密钥 `private.<appid>.key` 在仓库根，已 gitignore）。
- 开发 API 地址在 `src/config.ts`（局域网 IP，换网络要更新）；生产走 `https://guardianangel.net.cn`。

## API 约定（红线）

1. **小程序直调既有 `/api/*` 端点**——`dependencies.get_active_user_from_request` 已接 mp bearer 回落。
2. `/api/mp/*` 只放"为小程序聚合/投影/URL 绝对化"的端点，**严禁复制业务逻辑**。
3. 请求一律走 `utils/api.ts` 的 `request()`/`uploadFile()`：统一 bearer 注入、错误 toast、401 处理；表单端点传 `form: true`。
4. 鉴权文件预览一律走 `utils/preview.ts`（`downloadFile` 带 bearer 拿临时路径，`<image src>` 带不了头）。
5. 新增 mp 路由后：写纯函数单测（仿 `tests/test_wechat_mp_teacher_grading.py`）+ 重生成 `tests/fixtures/p02_route_snapshot.json`。

## 页面模板约定

- 数据加载：`onShow` 里刷新 + `enablePullDownRefresh` 下拉刷新（`onPullDownRefresh` 里 finally `uni.stopPullDownRefresh()`）。
- 401 → `uni.reLaunch({ url: "/pages/welcome/index" })`。
- 角色化 tab：页面 `onShow` 调 `utils/tabs.ts` 的 `applyRoleTabs(role)`（幂等）；登出调 `resetRoleTabs()`。
- 跳转作答页统一 `/pages/task-detail/index?id=<assignment_id>`。

## UI 设计语言（磨砂玻璃）

- 全局令牌在 `App.vue`：`.glass-card` / `.glass-chip` / `.glass-btn-primary` / `.press`（backdrop-filter 半透明白，机型不支持自动退化）。新页面复用令牌，不自造卡片样式。
- 页面根元素透全局 `page` 渐变底，不自设背景色。
- 色板：主文字 `#1b2540`、次文字 `#66718f`、弱文字 `#9aa6bf`、品牌蓝紫 `#5b6ee0`、危险 `#e5484d`、"即将上线"徽标金 `#b08a2e`。
- tabBar 图标：`scripts/make_tab_icons.py` 生成（81px 线性双态），改图标改脚本重跑，不手绘。

## 版本与发布纪律

- 版本号 `v0.<里程碑序号>.<修订>`，上传描述写 `M{n} <内容>`。
- 一个里程碑 = 一个体验版 = 一次真机验收 = 一次提审窗口；验收清单见实施规划文档各里程碑"出口标准"。
- 提审/发布/订阅消息模板等 mp 后台操作只能用户手动（后台被自动化访问策略拦截）。
