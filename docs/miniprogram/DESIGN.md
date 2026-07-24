# LanShare 微信小程序端 — 架构设计与实施方案

> 2026-07-25 起草。目标：以 Vue3（uni-app）为基础，把学生端/教师端核心功能重构为微信小程序，
> 微信身份自动登录、完美复刻"人生一言"欢迎屏、大图标大板块的原生化排版、作业考试为核心模块。
> 现有 Web 端（Jinja + islands）**保留不动**，小程序是新增的第二前端，共用同一 FastAPI 后端。

---

## 1. 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 小程序框架 | **uni-app（Vue 3 + Vite + TypeScript + Pinia）** | 微信小程序不能直接跑 web Vue，必须经编译框架。uni-app 是 Vue3 原生写法（`<script setup>`、组合式 API），Vite 构建与现仓库工具链一致；Taro 偏 React，与"以 vue3 为基础"的要求不符 |
| UI | 自研轻组件 + uni-app 内置组件为主 | 遵循"大图标大板块、少堆叠"的设计目标，不引入重型 UI 库；图标用 iconfont/SVG |
| 状态 | Pinia（auth / user / tabbar badge 三个 store 起步） | Vue3 官方方案 |
| 请求 | 封装 `uni.request` → `api.ts`（统一 token 注入、错误 toast、401 自动走静默重登） | 小程序无 cookie 会话，改 Header token |
| 后端 | 现有 FastAPI 新增 `classroom_app/routers/mp/` 一组纯 JSON 路由 | 全部**薄包装现有 services**（dashboard_service、作业/考试、life_tip_service…），不重写业务逻辑 |

代码位置：仓库内新增顶级目录 `miniapp/`（独立 package.json，独立构建，不影响现有 `npm run build`）。

## 2. 后端架构增量

### 2.1 认证（微信绑定 + 自动登录）

新表 `wechat_bindings`（runtime 建表，仿 polls / life_tips 模式，engine-aware，不进 REQUIRED）：

```
id, user_type ('student'|'teacher'), user_id, openid UNIQUE, unionid NULL,
nickname NULL, bound_at, last_login_at, status ('active'|'revoked')
```

流程（微信官方 code2Session 方案）：

1. 小程序启动 `wx.login()` 拿临时 `code` → `POST /api/mp/auth/login {code}`。
2. 后端用 **AppID + AppSecret** 调 `https://api.weixin.qq.com/sns/jscode2session` 换 `openid`（AppSecret 只存在服务器 env，永不下发）。
3. `openid` 已绑定 → 直接签发平台 token（新表 `mp_sessions`：token SHA-256、user、过期 30 天、滑动续期），小程序存 `uni.setStorageSync`，后续所有请求 `Authorization: Bearer <token>`，**实现无感自动登录**。
4. 未绑定 → 返回 `need_bind`，前端展示首次绑定页：
   - 学生：学号 + 姓名（走现有学生名册校验，与现学生登录同源逻辑）；
   - 教师：现有教师账号密码（教师侧身份敏感，首绑必须密码）。
   校验通过 → 写入 `wechat_bindings`（一 openid 一账号；一账号可解绑重绑）→ 签发 token。
5. 防护：绑定接口按 openid + IP 限速（如 5 次/10 分钟）；管理中心加"微信绑定"治理页（查询/强制解绑）。

### 2.2 业务 API 层

`classroom_app/routers/mp/` 下按域拆文件（每文件 <400 行）：

- `auth.py` — login / bind / logout / me
- `home.py` — 首页聚合：复用 `dashboard_agenda_events` 统一数据源 + 个性化欢迎语（`personal_greeting_service`）
- `life_tips.py` — 欢迎屏：复用 `life_tip_service` 候选池 + 反馈接口 + 图库 manifest
- `homework.py` / `exam.py` — 作业考试（详见 §4）
- `classroom.py` — 课堂/互动最小集
- `teacher.py` — 教师端聚合

统一响应信封 `{success, data, error}`（与全局规则一致）。所有接口是**读现有 service 的薄壳**，SQL 一律走 `get_db_connection()` + `?` 占位（见 life-tip 生产事故教训）。

### 2.3 静态资源

人生一言图库 `static/img/life_tips/*.webp`（324 张、~19MB）经 nginx 直接服务，域名走小程序 downloadFile 合法域名。首版直接复用现图（webp 均 <200KB/张，小程序单图无压力）；如弱网体验差，二期用 `tools/tips/compress_images.py` 增产 720p 竖屏裁切变体。

## 3. 欢迎屏（人生一言）复刻要点

- 页面：登录/自动登录成功后的过渡页 `pages/welcome/index`，复刻 Web 端第五代定稿：
  - 背景图 `<image mode="aspectFill">` —— **正是需求的裁切行为**：竖屏下上下顶边、左右按原比例居中裁剪（横图取中间竖条）；
  - 色调自适应液态玻璃卡：Web 端用 canvas 采样亮度（`sampleImageTone` 阈值 148），小程序端同算法用离屏 canvas 2D 采样中央横带 → light/dark 两套玻璃卡样式（暗图黑玻璃白字/亮图白玻璃深字）；
  - 整句渐显+尾段渐隐、左上身份徽章条（教师无修为则隐藏）、底部计时条（2800ms+字数×80ms，clamp 3–8s）、点击跳过、👍/👎 反馈（复用现有 feedback API 与去重 storage）、💾保存（canvas 合成同款构图 → `wx.saveImageToPhotosAlbum`，需 scope.writePhotosAlbum 授权）；
  - 展示期间预取首页数据与 tabbar 图标（对应"后台加载资源"）。
- 候选语从 `/api/mp/auth/login` 响应内联 3 条（与 Web 登录 JSON 同模式，零额外压力），本地 storage 记最近 20 条去重。

## 4. 作业考试模块（核心，最重投入）

学生端页面流：

1. **列表页**（tab 之一）：进行中/已截止/已完成分段；大卡片（标题、课程、截止倒计时、状态角标）。
2. **详情/作答页**：
   - 题型渲染器注册表（单选/多选/填空/简答/附件题），与后端现有试卷 JSON 结构对齐；
   - **草稿自动保存**（每 30s + onHide 时 `POST draft`，断网存本地、恢复网络补传）——移动端网络不稳是最大风险点；
   - 附件/拍照上传 `wx.chooseMedia` + `uni.uploadFile`（走现有上传通道，大小/类型校验）；
   - 考试模式：服务器时间倒计时、到时强制交卷、`onHide` 切屏计数上报（供教师参考，不做强惩罚）。
3. **小组作业**：绑定分组方案的作业进入组视图；提交后 20 分制组员互评（复用 group-assignment-scoring 全套后端：互评隐藏、全组完成才揭晓、ledger 防二次混算）。
4. **结果页**：得分、AI 批语、错题；考试出分后可跳错题归集。

教师端：作业/考试列表 → 提交进度大盘（已交/未交名单）→ 批阅（逐份滑动批阅、分数+评语；AI 批阅结果确认）→ 一键催交（暂用站内提醒，订阅消息二期）。

后端：以上全部有现成 service，只加 mp JSON 壳 + 草稿接口（若现无独立草稿表则新增 `homework_drafts`，runtime 建表）。

## 5. 信息架构与设计原则（学生端/教师端）

**学生端 tabBar（4 项）**：首页 · 作业考试 · 课堂 · 我的
**教师端 tabBar（4 项）**：首页 · 作业考试 · 课堂 · 工作台
（同一小程序，登录后按角色 `wx.setTabBarItem` 动态换 tab 文案/图标，或用自绘 tabbar 组件按角色渲染。）

- 首页 = "今天"：顶部个性化欢迎语（复用 personal_greeting），中间**一张大议程卡**（今天要做的事，数据源 `dashboard_agenda_events`），下面最多 2–3 张功能大卡（如修为、投票进行中提醒）。不做九宫格小图标堆叠。
- 每页一个主任务；次要功能收进"我的/工作台"二级列表；Web 端旁支功能（博客、职业星图等）首版**不搬**，在"我的"里留 H5 跳转位或干脆不放。
- 多用原生能力：下拉刷新、`wx.share` 转发作业、`wx.previewImage`、订阅消息（截止提醒，二期）、深色模式跟随系统。

## 6. 部署方案

### 6.1 硬性前置（微信平台合规，缺一不可上线）

1. **小程序账号**：mp.weixin.qq.com 注册，拿 AppID/AppSecret。主体建议**企业/组织（学校）**；个人主体可先行开发内测，但教育类目与部分能力受限，转正式前需确认类目可用。
2. **域名**：小程序 request/uploadFile/downloadFile 合法域名**必须是已 ICP 备案的 HTTPS 域名，不能用 IP**。当前生产是裸 IP `106.53.153.171` —— 这是最大阻塞项。VPS 在腾讯云，走腾讯云备案通道即可（个人/单位均可，周期约 1–3 周）。
3. **TLS 证书**：域名就绪后 Let's Encrypt（nginx 容器加 certbot 或 acme.sh），我来配。
4. 微信后台配置：服务器合法域名（request + uploadFile + downloadFile 各填 `https://<域名>`）、隐私保护指引（声明收集：微信 openid、姓名学号、相册写入权限）。

### 6.2 服务器侧

- **零新增容器**：mp 路由并入现有 FastAPI 主应用（1 worker 内），静态图库走现有 nginx。2c/4GB 承载 ~200 并发的预算不变（mp 接口全是短 JSON 请求，比整页渲染更轻）。
- nginx 增 `server_name <域名>` + 443 证书；IP 访问保留（Web 端过渡）。

### 6.3 小程序侧 CI/CD

- 开发预览：微信开发者工具（你本机扫码登录）导入 `miniapp/dist/dev/mp-weixin`。
- **自动化上传（推荐，也是我能自助操作的方式）**：`miniprogram-ci`——你在小程序后台"开发设置"下载**代码上传密钥**（一个 private key 文件）并配置 IP 白名单（或关闭白名单），我即可脚本化 `build → 上传体验版 → 生成预览二维码`，全程不需要你的微信密码。
- 发布：体验版 → 你扫码验收 → 提交审核（审核需在后台由管理员操作，首次审核注意教育类目材料）→ 发布。

## 7. 实施阶段

| 阶段 | 内容 | 出口标准 |
|---|---|---|
| P0 资源就绪 | 域名备案、AppID/Secret、上传密钥（见 §8） | 凭据写入 `wechat_mp.env`，域名可 HTTPS 访问后端 |
| P1 骨架+登录 | miniapp 脚手架、api 封装、`/api/mp/auth/*` 全流程（code2Session、绑定、token、自动登录）、welcome 欢迎屏复刻 | 真机：首次绑定→杀进程重进→无感直达首页含欢迎屏 |
| P2 学生核心 | 首页议程、作业考试全流程（含草稿、上传、小组互评）、课堂最小集 | 一名学生全流程在真机跑通 |
| P3 教师核心 | 教师首页、作业考试进度/批阅、工作台 | 教师真机验收 |
| P4 打磨上架 | 深色模式、弱网重试、订阅消息、隐私指引、提审 | 审核通过发布正式版 |

每阶段照常走：TDD（mp 路由单测 + 现有 P02 路由快照基线重生成）→ code review → 真 PG 冒烟 → deploy-workflow 部署。

## 8. 所需资源清单（凭据填入根目录 `wechat_mp.env`，已被 `*.env` gitignore）

见 `wechat_mp.env.example` 模板。明确**不需要**你的微信账号密码/小程序后台密码——后台登录本来就是扫码，且自动化上传只靠上传密钥。
