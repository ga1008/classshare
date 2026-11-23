# 小游戏平台接入与验收

## 游戏包与开发入口

| 包 | 玩法 | 验收时的具体操作 |
| --- | --- | --- |
| [航标接力](downloads/beacon-relay.zip) | 单人、2–4 人合作私房、双人随机 | 打开即从个人最远章节继续。按东、南、西的线索点灯，最后引导船队返航。组队时各人轮流操作，可以单独更换海图配色。 |
| [星图拼接](downloads/star-map.zip) | 单人 | 依次完成辨形、推演、合图。刷新后从已解锁的章节继续。 |
| [双人连线棋](downloads/three-in-line.zip) | 双人随机、双人私房、离线同屏 | 双方轮流落子，横、竖、斜线连成三子获胜。棋盘填满可平局。 |

解压任一 ZIP 后双击根目录的 `main.html` 可离线游玩。上传时选择原 ZIP。教师可在「教学资源 → 小游戏」上传，验收完成后打开真实预览，登记试玩结果，设置受众并发布。学生在「观点与交流」右侧进入大厅。大厅只列入已发布且当前账户有权游玩的游戏。

随包 SDK 位于 `js/lan-game-sdk.v1.js`。发行源在 [static/minigames](../../static/minigames/lan-game-sdk.v1.js)，三个示例共用的界面与操作逻辑在 [examples/minigames](../../examples/minigames)。运行 `python tools/build_minigame_examples.py` 可从源文件重新生成上述三个包。作者接入其他独立游戏时按[作者手册](05-game-author-guide.md)设置清单、回写契约和规则，保持 SDK 字节一致。局内的玩家标识为随机席位别名，昵称由可信宿主提供。SDK 通过独立消息端口接收只读契约并提交操作。[HTML Channel messaging](https://html.spec.whatwg.org/multipage/web-messaging.html#channel-messaging)

## 服务、数据与版本

| 责任 | 代码入口 | 故障时可观察到的行为 |
| --- | --- | --- |
| 课堂端导航、游戏大厅、游戏宿主与教师管理 | `routers/minigames.py`、`templates/minigames/`、`static/js/minigames/` | 窄屏可切换列表与管理操作。失去席位控制的标签可查看自己能访问的数据，接管时提升控制代数。 |
| 身份、受众与目录 | `services/minigame_platform.py`、`services/minigame_catalog.py` | 主站从持久登录会话确认真人身份。班级、含合班的课堂、组织及修为过滤在分页之前完成。 |
| 房间、随机队列、规则、进度 | `routers/minigame_runtime.py`、`services/minigame_runtime*.py` | 满员后按服务端时间进入 5 秒倒计时。重连回原席位。共同进度只前移，私人字段按个人修订号保存。 |
| 游戏包、教师试玩、发布和删除 | `routers/minigame_admin.py`、`services/minigame_packages.py` | 上传先进入私有暂存。浏览器执行器离线时保留作业并重试。发布前必须取得该版本预览记录并提交人工试玩结果。 |
| 资源签发与独立内容站 | `services/minigame_launch.py`、`classroom_app/minigame_content.py` | 资源路径在 90 秒授权周期及续期之间保持稳定。每次读取核验当前会话、版本和受众。下架后拒绝后续读取，正在传输的文件由共享读锁保护。 |

游戏表使用 `mg_` 前缀。素材位于 `DATA_ROOT/games/versions/<gameId>/<versionId>/`，包括原 ZIP、验收清单、重编码图标与运行树。`staging/` 持有验收中的输入，`locks/` 用于跨进程文件占用协调。发布在事务中切换当前版本；运行中且仍有权限的成员可继续访问已锁定的旧版。删除先撤销准入，再由持久作业清理该游戏的各版本与业务数据。删除登记、失败重试与恢复封存的操作见[执行器和恢复说明](../../deployment/docker/MINIGAME_PROBE.md)。

## 隔离环境部署顺序

1. 准备主站与独立内容站两个不同主机名，各自使用 HTTPS。主站登录 Cookie 使用 host-only 作用域。内容站接收浏览器资源请求，反向代理剥离 Cookie、Authorization，关闭内容路径的常规访问日志或进行脱敏。仅本机联调允许 `127.0.0.1` 与 `localhost` 的 HTTP 组合。
2. 在受控部署环境配置 `MINIGAMES_APP_ORIGIN` 和 `MINIGAMES_CONTENT_ORIGIN`。两端使用同一已配置的强会话密钥和权威业务数据库。内容服务只能接收包运行所需的文件请求，部署时使用单独的进程、数据库连接配额和文件挂载。
3. SQLite 初始化路径会建立小游戏表。PostgreSQL 可先执行 `python tools/db/migrate_minigames.py` 查看迁移状态，备份并演练后使用 `--apply` 进行增量建表。迁移提交后重新启动服务，再次执行检查。授权的回滚可撤销入口和进程；历史存档、版本和删除登记需按恢复程序处理。
4. 内容进程入口为 `python -m uvicorn classroom_app.minigame_content:app --host 0.0.0.0 --port 8014 --workers 1 --no-access-log`。可使用仓库的 [Compose 叠加文件](../../docker-compose.minigames.yml) 与 [Nginx 虚拟主机样例](../../deployment/nginx-game-content.conf.example)。按环境设置该叠加文件要求的数据库连接、密钥注入、证书目录和实际虚拟主机配置文件。主站与内容进程都要能访问同一份版本目录和文件锁，内容端版本目录只读。
5. 单独的 Linux 验收执行器在无外网、无课堂凭据的沙盒里试跑上传的包。按[执行器配置](../../deployment/docker/MINIGAME_PROBE.md)准备 broker 与队列，再从教师页验证一次离线、重试、试玩和发布。浏览器或沙盒不可用时界面会保留验收进度。
6. 叠加文件在课堂域名下将 `/api/games/v1/` 转交专用游戏 API。课堂应用负责页面与续登，专用 API 负责游戏请求和后台作业，内容进程服务无 Cookie 的独立域名。按主站 1 个进程、专用 API 2 个 worker、内容站 1 个进程配置，每进程的 `GAMES_DB_WORKERS` 为 1，总计最多 4 个游戏数据库并发席位。专用 API 在 CPU 争用时的调度权重低于课堂。更改进程数前要重新计算总额，并在最终环境复测课堂延迟与读锁退让。

启用独立内容站的 PostgreSQL 主机使用受保护的 `minigames.env`。该文件和 `docker.env` 同目录，部署管理员在主机本地配置，权限设为 0600。填写以下实际值。部署脚本在每次发布时检测此文件，沿用既有的 Compose 配置，并将内容服务加入停写范围。带备份的停写迁移会在核心表迁移成功后执行小游戏建表。

| 键 | 值 |
| --- | --- |
| `MINIGAMES_APP_ALIASES` | 已用于课堂登录的其他 HTTPS 源，以英文逗号分隔。写入校验与内容页嵌入共用这份精确名单；内容站须有独立主机名。 |
| `MINIGAMES_APP_ORIGIN`、`MINIGAMES_CONTENT_ORIGIN` | 浏览器实际访问的两个 HTTPS 源，非默认端口也需填入。 |
| `MINIGAMES_SIGNING_SECRET` | 与主应用 `SECRET_KEY` 相同的高熵密钥，UTF-8 编码至少 32 字节，经受保护的注入机制提供。 |
| `MINIGAMES_DATABASE_URL` | 内容容器可访问的权威库连接串，在 Compose 网络内使用 PostgreSQL 服务名。 |
| `MINIGAMES_VHOST_CONF`、`MINIGAMES_TLS_DIR` | 主机上的完整绝对路径。前者指向已替换域名的文件，后者存放匹配证书及密钥。 |
| `MINIGAMES_REGISTER_DIR` | 独立、持久的删除登记卷在主机上的绝对路径。由专用 API 写入，纳入独立封存及恢复演练。 |

可在隔离演练主机上执行 `docker compose --env-file minigames.env -f docker-compose.yml -f docker-compose.postgres.yml -f docker-compose.minigames.yml config --quiet` 验证配置。如果平台已有 Compose 插值文件 `.env`，在 `--env-file minigames.env` 前加上 `--env-file .env`。保留 `--quiet` 以免打印配置中的连接串与密钥。浏览器验收队列还需在部署插值文件 `minigames.env` 内设置 `MINIGAMES_PROBE_COMMAND`，由专用 API 提交验收任务；执行器与独立签封登记的卷设置见[浏览器执行器说明](../../deployment/docker/MINIGAME_PROBE.md)。

本地可运行 `python tools/minigame_compose_probe.py`，或用 `--compose` 指向独立 Compose 可执行文件。该检查只对临时合成配置运行 Compose 的模型合并，校验可信 API 进程数、总连接预算、CPU 权重、只读素材卷、外部删除登记卷及课堂代理可独立启动。2026 年 11 月 23 日使用 Compose v5.6.0 通过这九项检查。生产容器启动、Linux 沙盒、DNS、证书与实际资源余量需在目标环境核验。[配置探针](../../tools/minigame_compose_probe.py) · [Docker Compose 配置命令](https://docs.docker.com/reference/cli/docker/compose/config/)

### 首次启用、回退与上线回读

首次启用使用带 PostgreSQL 演练报告的停写发布。构建和 Nginx 配置预检在停写前执行。发布脚本记录原 PostgreSQL 容器 ID，保留各业务容器的旧镜像标签，随后停止应用写入、完成切换点备份和增量迁移。恢复时先等待 AI、课堂主应用和原业务进程就绪，再验证课堂发布号，最后启动小游戏 API 与内容进程。生产 PostgreSQL 在这条路径中由原容器持续提供服务。

上线后可在主机直接回读运行状态。以下命令检查 Nginx 配置、两个游戏容器的真实卷来源与读写模式，各执行一次容器本地探测和经过 Nginx 的 HTTPS 探测。内容站使用专用主机名，探测会核实证书和响应头。

```bash
cd /lanshare
remote_path="$PWD"
db_engine=postgres
compose_files_description="docker-compose.yml + docker-compose.postgres.yml"
source deployment/docker/minigame-compose.sh
configure_minigames_overlay
minigames_verify_running
"${compose_cmd[@]}" ps
```

迁移失败时，依据切换点备份和已执行迁移修复后恢复写入。若课堂已经恢复而小游戏探测失败，先检查对应进程、挂载和证书，保留课堂服务。需要撤回首次启用时，由操作员按本次发布记录恢复旧版应用镜像与 Nginx 配置、撤下新增游戏进程，保留新增游戏表、包目录和独立删除登记；如需实际恢复库，按删除登记重放流程处理。

## 回归与证据

在 Windows PowerShell 中可单独复跑双端联机与断网单机验收。首次运行前按仓库流程执行正式构建，配置本机 Chromium，并使用项目虚拟环境。截图输出目录须位于仓库的 `.codex-temp/evidence` 内。

```powershell
.\venv\Scripts\python.exe -m pip install -r tools/academic_browser_probe_requirements.txt
.\venv\Scripts\python.exe -m playwright install chromium
$env:MINIGAMES_BROWSER_INTEGRATION = '1'
$env:MINIGAMES_BROWSER_SCREENSHOTS = '.codex-temp/evidence/minigames-multiplayer-screens'
.\venv\Scripts\python.exe tools/test_backend.py --pattern test_minigame_multiplayer_browser.py --report .codex-temp/evidence/minigames-multiplayer-complete.json
```

2026-11-23 的双端浏览器回放通过两项测试。两名合成学生通过四位码组队，经过五秒倒计时轮流点灯，互发队聊。一端真实断网直至另一端显示等待重连，恢复后双方完成原局。重新选择历史队伍预填了原队名和人数，新局生成新码。连线棋验收先取消随机等待，再由两名学生重新匹配直至第一席获胜；断网后直接打开包内 `main.html`，完成星图三题与棋盘同机对局。报告记录了本次夹具清理及退出状态。[双端浏览器用例](../../tests/test_minigame_multiplayer_browser.py)

本地截图 `01-private-countdown.png` 到 `06-local-three-in-line.png` 位于上列截图目录。大厅与管理的 Chromium 交互用例另用模拟 API，可通过 `--output=.codex-temp/evidence/minigames-ui-final` 保留 1920px 与 390px 大厅、390px 教师属性页的截图。[大厅、宿主与教师管理用例](../../tests/e2e/components/minigames.spec.ts)

三款游戏经真实上传、隔离验收和发布后的大厅实拍位于 `.codex-temp/evidence/minigames-v21-hall/`。合成学生实际开局后的桌面图 `desktop.png` 为 1440 × 900，`mobile.png` 为 390px 窄屏全页。两者使用账户的深色外观和内置星空背景。四栏显示本人最近两款游戏、近七日人数、同意展示的好友以及教师精选。该目录的 `requests.json` 记录了开局和观察 68 秒期间大厅与宿主发起的匿名化 API 路径。

- 后端入口为 `python tools/test_backend.py --pattern "test_minigame*.py"`。启用 `MINIGAMES_BROWSER_INTEGRATION=1` 时，额外运行真实浏览器和双主机回环网络案例。夹具自行生成账号、进度、教师预览与游戏包，只使用可清理的测试库。
- 浏览器组件回归使用 `npx playwright test --config tests/e2e/components/playwright.config.ts minigames.spec.ts`；独立内容站联调还覆盖完整 SDK、包验收、真实登录、存档与多账号房间。Windows 固定样例的浏览器测试与生产 Linux 沙盒的验证环境须分别记录。
- 并发压测工具为 [tools/minigame_runtime_capacity_probe.py](../../tools/minigame_runtime_capacity_probe.py)。独占回环 PostgreSQL 和两个应用进程可复现跨进程房间、每进程连接峰值、短同步、回合与课堂读接口的响应分布。真实主机上线验收仍按[容量与故障矩阵](03-runtime-and-data.md)测量。
- 发布前执行 `npm run build`、LQ 清单生成与校验，核对 API 路由快照。全平台源审计记录组件使用情况，交互结果由浏览器回放和真实后端验证。

### 本机双 HTTPS 反向代理

完成上述 Python 和 Chromium 环境准备后，可使用本机已有的 Windows Nginx 发行目录复跑。将 `$nginx` 替换为实际的 `nginx.exe` 路径，保留同目录发行包自带的 `conf/mime.types`。

```powershell
$nginx = 'C:\tools\nginx\nginx.exe'
.\venv\Scripts\python.exe tools/minigame_https_probe.py --nginx $nginx --report .codex-temp/evidence/minigames-v21-https/three-process.json
```

探针把仓库主站和内容站的虚拟主机配置复制到独占临时目录，以 `127.0.0.1` 和 `localhost` 两个 HTTPS 源连接三个独立后端进程。实际记录显示课堂页和普通账户请求进入主站，所有小游戏 API 命中可信 API 进程，包内素材只到达内容服务。临时证书只在本次客户端和浏览器会话有效。2026-11-23 的 Windows Nginx 1.27.5 验收中，HEAD、完整 GET、字节范围分别返回 200、200、206，教师下架后原地址返回 404。浏览器在来源为 `null` 的 iframe 中完成真实游戏操作；续租返回 200。探针同时在内容服务入口检查凭据剥离，在本次 Nginx 日志中检查租约和凭据的脱敏。Linux 浏览器沙盒及目标主机证书仍需在对应环境执行验收。[本机 HTTPS 探针](../../tools/minigame_https_probe.py)
