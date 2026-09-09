# LanShare 小程序端（uni-app Vue3）工程规约

> 推进路线真源：`docs/miniprogram/IMPLEMENTATION-PLAN-2026-08.md`（M0–M6 里程碑，做功能前先看）。
> 架构设计：`docs/miniprogram/DESIGN.md`。本文只讲"怎么写代码、怎么发版"。

## 技术栈与硬约束

- uni-app（Vue3 + Vite + TS）+ **pinia@2**（v3 与 uni 别名不兼容，禁止升级）。
- 本地验证：在 `miniapp` 运行 `npm test`、`npm run type-check`、`npm run build:mp-weixin`；构建输出 `dist/build/mp-weixin`。测试复用仓库根目录锁定的 Vitest，需先安装根目录及 `miniapp` 的现有依赖。
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
- 受保护页面在加载数据或选择角色 API **之前** `await ensurePageSession(requiredRole?)`（`utils/session.ts`）。冷启动会话初始化只发起一次；无需各页重复实现静默登录。
- 401 由 `api.ts` / `preview.ts` 统一清认证状态并合并登录跳转；需要主动回登录页时用 `redirectToLogin()`，登录或绑定成功用 `finishSessionLogin()` 恢复经过白名单和角色校验的原目标。旧账号在途响应不得应用到新账号页面。
- 角色化 tab 由登录守卫及 tab 页 `onShow` 调 `utils/tabs.ts` 的 `applyRoleTabs(role)`；非 tab 页不得调用平台 tab 更新，失败可在进入 tab 页后重试；登出调 `resetRoleTabs()`。
- 跳转作答页统一 `/pages/task-detail/index?id=<assignment_id>`。

## 阶段 1 的状态与写入契约

- 同一平台账号保留多微信绑定能力；“退出登录”解除**当前微信**的绑定及其会话，其他微信不受影响。网络失败时不显示解绑成功；过期会话先重新验证微信身份再解绑。
- 草稿按 API 环境、角色、用户、任务和重交轮次隔离；不恢复旧的无账号草稿。作答状态、重交窗口和计时以服务端投影为准。
- 提交和草稿携带 `expected_submission_version`；评分携带 `expected_review_revision`。409 必须提示刷新核对并保留尚未保存的输入，不得自动重放写请求。
- 评分输入是“原始评分（迟交扣分前）”；最终分仅用于展示，空输入不得转换成 0。切换答卷、加载中和保存中均应防止误写其他答卷。
- 学生上传、草稿保存、提交串行执行；超时后先核对服务端提交状态。后端共用提交锁保护首次提交、退回重交及迟到的草稿请求。
- 本轮实现、自动化命令和待真机清单见 [阶段 1 实施记录](../docs/miniprogram/PHASE1-IMPLEMENTATION-2026-09-08.md)。本地测试通过与真机验收、发布分别登记。

## UI 设计语言（磨砂玻璃）

- 全局令牌在 `App.vue`：`.glass-card` / `.glass-chip` / `.glass-btn-primary` / `.press`（backdrop-filter 半透明白，机型不支持自动退化）。新页面复用令牌，不自造卡片样式。
- 页面根元素透全局 `page` 渐变底，不自设背景色。
- 色板：主文字 `#1b2540`、次文字 `#66718f`、弱文字 `#9aa6bf`、品牌蓝紫 `#5b6ee0`、危险 `#e5484d`、"即将上线"徽标金 `#b08a2e`。
- tabBar 图标：`scripts/make_tab_icons.py` 生成（81px 线性双态），改图标改脚本重跑，不手绘。

## 版本与发布纪律

- 版本号 `v0.<里程碑序号>.<修订>`，上传描述写 `M{n} <内容>`。
- 一个里程碑 = 一个体验版 = 一次真机验收 = 一次提审窗口；验收清单见实施规划文档各里程碑"出口标准"。
- 提审/发布/订阅消息模板等 mp 后台操作只能用户手动（后台被自动化访问策略拦截）。
