# 全屏背景生命周期与外观偏好

本模块延续原有登录和欢迎语，不为每个页面创建一份背景控制器，也不为每条路由增加数据库设置。背景是当前账号的全站外观偏好，教师和学生使用相同保存接口与并发版本协议。

## 模块边界

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 首屏 | `templates/partials/lq_page_backdrop.html` | 输出唯一固定全屏背景、当前账号设置和分类目录；读取短期登录交棒，在主模块执行前保持同一张原图 |
| 生命周期 | `static/js/page_backdrop.js` | 每个 document 的单例；校验图库、预载模糊图、原图兜底、欢迎交棒、取消过期异步结果 |
| 欢迎语 | `static/js/cultivation_identity.js` | 保留反馈、保存、暂停、跳过与重放；结束时调用单例 adopt，淡出原图覆盖层 |
| 偏好界面 | `static/js/user_ui_preferences.js`、`templates/macros/user_ui_preferences.html` | 复用既有 CAS 保存队列；分类、固定图片、纯色；图库只在展开后渲染 |
| 持久化 | `classroom_app/services/user_ui_preferences_service.py`、`classroom_app/routers/user_ui_preferences.py` | 使用现有 backdrop 字段与 version；固定图片必须在真实 manifest 白名单内 |
| 视觉 | `static/css/lq/components/page-backdrop.css` | 全屏固定 cover/center；消费共享 `--ls-scene-opacity`；优先预模糊图，兜底单层 Gaussian + bleed |

共享材质只叠透明基色与边缘。背景图的尺寸、裁切和模糊只有一个事实来源，内容面板不分别重建图像。导航和浮层仍可模糊实时下方内容。

## 选择与交棒规则

- `scene`：继承本次登录背景，同一账号会话跨页面保持；没有交棒时继续原有确定性默认选择。
- 原有 `scene-*` 分类：按当前账号与日期种子选择分类图片；欢迎重放不覆盖显式分类。
- `image:<filename>`：固定真实图库图片，刷新、跨页和重新登录后由账号设置恢复。
- `off`：使用已保存纯色，欢迎重放也不覆盖此选择。
- 登录交棒仅接受站内图片路径，45 秒内有效。会话图片另带账号 context，禁止跨账号继承。
- 原图和模糊图保持相同 `inset: 0 / center / cover`，交棒只改透明度；不缩成顶栏、不缩放页面内容。减少动效时立即结束过渡。

## 网络、并发与退化

图库成功请求在当前页面缓存；失败、非法 JSON/目录或空目录不永久缓存。HTTP 使用重新校验语义，失败后重新展开图库可以再取。请求包括响应体解析有 2.5 秒上限，并通过 AbortController 取消，避免阻塞欢迎结束。卸载也取消尚未完成的请求。

模糊衍生图预载上限 1.8 秒；不可用时先验证原图，再使用 24px Gaussian 与 48px bleed。两种来源都不可用时保留当前背景。每个异步边界校验 generation，较早完成的加载不能覆盖用户随后选择的纯色或另一张图片。

关闭背景同步生效；保存仍使用原有账号 context 和 version。无效图片返回 422、陈旧版本返回 409，前端不绕过原有冲突与错误状态。图库无效不影响正文和现存 SSR 图。

## 验证范围

专用合成运行环境位于 `.codex-temp/lq-background-20260924`，端口 8295，使用独立账号和数据库。`tests/e2e/lq-background.playwright.config.ts` / `specs/lq-background.spec.ts` 包含以下 7 个 Chromium 场景：

1. 真实登录、键盘反馈、跳过、同图全屏交棒、跨简历根模板和固定滚动背景。
2. 学生图库保存、刷新、跨页、422/409 与移动端布局。
3. 教师图库保存、刷新、跨页、422/409 与移动端布局。
4. 减少动效下重放保持纯色，账号 context 隔离。
5. 缺少衍生图时 Gaussian 与 bleed 生效，后续纯色取消延迟图像加载。
6. 503/非法目录后重新展开恢复；原图与衍生图双缺时保留已显示背景。
7. 图库接口不回应时欢迎和覆盖层按时结束，之后重新请求可恢复。

预构建诊断可用 `LQ_BACKGROUND_SOURCE_OVERLAY=1`，它只替换静态 JS/CSS，认证和保存仍使用真实合成 API。发布验收必须去掉该变量，验证最终不可变资源图；不能把源码覆盖模式当作生产资产验收。后端定向 unittest 有 50 例，前端 profile appearance mock 补齐后有 11 例。最终资源哈希与实际运行结果由统一发布验收记录确认。

这些证据覆盖选定角色与根模板的 Chromium 流程，不等于全部页面、真实用户数据或 iOS Safari 实机验收。视觉核验样例在 `.codex-temp/glass2-bright-visual/`，图库图片为 `biye-sunny-blossom-path03-a575c683.webp`，浅色 rose 配色，包括桌面与手机首页及顶栏外观面板。
