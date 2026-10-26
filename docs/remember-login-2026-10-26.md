# 保持登录（自动登录）· 2026-10-26

> 起因：学生反馈「每次都要登录」。真源：本文 + `classroom_app/services/remember_login_service.py` + `tests/test_remember_login.py`。

## 1. 行为

- 学生、教师登录页都有「30 天内保持登录」复选框，**默认不勾选**（机房电脑是公共的），本浏览器记住上一次的勾选偏好（只存偏好，不存凭据）。
- 勾选后，访问会话（24 小时）过期时自动续登，用户无感；30 天不用会失效（滑动），从最后一次输入密码起最长 180 天必须重新输入密码。
- 个人中心 → 账号安全 →「保持登录的设备」：查看设备（类型/系统/浏览器、最近使用、IP 打码、保持到何时、本机标记），可单独取消，或「退出所有设备」。

## 2. 安全与业务闭环

| 事件 | 结果 |
|---|---|
| 主动退出 | 只忘记本机；其他已记住的设备不受影响 |
| 本人修改密码 | 所有设备失效，**当前设备自动重新记住**（不用重登） |
| 找回/重置密码、超管改密、账号停用、学生学籍状态变更、超管强制下线、学生被移出 | 所有设备失效（统一走 `actor_authority_changed` / `invalidate_session_for_user`） |
| 平台「单账号单会话」规则 | 只有账号当前没有在线会话，或在线会话正是这台设备自己的，才自动续登；另一台设备正在用时不抢占，登录页提示「该账号正在另一台设备上使用」 |
| 凭据被复制（旧凭据在轮换宽限期外被再次使用） | 撤销该账号所有设备并结束会话，登录页提示「检测到保持登录凭据被重复使用」，建议改密码 |
| 同一页面并发请求 | 单进程内 120 秒宽限缓存，复用同一次续登结果，不误判盗用、不互相挤下线 |
| 每账号设备上限 | 默认 5 台，超出时自动取消最久未用的 |

凭据：cookie `ls_remember = <selector>.<validator>`（httpOnly、SameSite=Lax、HTTPS 下 Secure）；库里只存 `sha256(validator)`，每次续登轮换 validator。

## 3. 实现入口

- 服务：`remember_login_service.py`（签发/轮换/撤销/设备列表，运行时表 `remember_login_tokens`，不进 `classroom_app/db`，不触发原生 PG 门禁）。
- 中间件：`remember_login_middleware.py`（在 `app.py` 注册；无有效会话且带 `ls_remember` 时续登并改写请求 Cookie 头）。
- 登录接线：`routers/ui_parts/auth.py`、`ui_parts/common.py`（`remember_me` 表单字段）；登录页已登录同角色时直接跳 `next`。
- 设备 API：`GET /api/session/remembered`、`DELETE /api/session/remembered/{id}`、`POST /api/session/remembered/revoke-all`。
- 前端：`partials/login_remember.html`、`partials/profile/security.html`、`static/js/{student,teacher}_login.js`、`static/js/profile.js`；样式在 `ui-system.src.css` 末尾（LQ 关闭时也生效）。
- 开关（`docker.env`，改后重启 app）：`REMEMBER_LOGIN_ENABLED`（默认 true）、`REMEMBER_LOGIN_DAYS`（30）、`REMEMBER_LOGIN_MAX_DAYS`（180）、`REMEMBER_LOGIN_MAX_DEVICES`（5）。关闭开关即全站停用，已有凭据不再生效。
- 微信小程序有独立的 30 天会话体系，不受影响。
