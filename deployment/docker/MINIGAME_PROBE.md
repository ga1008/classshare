# 小游戏浏览器验收执行器

## 分步上线与故障恢复

当 `minigames.env` 中 `MINIGAMES_PROBE_COMMAND=` 留空（单机默认配置）时，教师或超管上传的 ZIP 在通过后端静态与安全校验后直接进入「验收就绪（`ready`）」，随后在管理页通过「教师预览」在独立无 Cookie 沙盒域完成真机试玩并登记试玩结果即可上架发布；此前已滞留在 `waiting_probe` 的版本也会由后台 worker 自动接续转为就绪。全站额度与教师额度在同一预留事务内分别检查。

如需启用独立自动化浏览器探针，填入下方队列命令并使用当前发布版本与完整 Compose 叠加参数重建专用 `game-api` 服务。已配置的执行器发生连接失败时，各包共用 120 秒退避并显示「待浏览器验收」，新上传仍可先做静态检查，执行器恢复后自动继续；包内独立素材超时则保留自身重试记录。已下架或待验收的游戏也可从教师页按原删除流程清理。

主应用负责上传和静态校验。独立 Linux 执行主机上的 broker 从受保护任务目录领取摘要，在一次性、无外网容器内挂载当前游戏的只读运行树。浏览器原生沙盒保持开启。应用、上传者和浏览器容器均无需 Docker socket。执行主机只接入游戏暂存子树和验收队列，不能挂载数据库、平台凭据或其他业务卷。

在独立执行主机准备与部署版本配套的 Chromium 沙盒 seccomp 配置，将其放入只读配置路径，确认宿主允许非特权用户创建所需用户命名空间。镜像与浏览器使用相同 Playwright 发行版。更新镜像后重新执行合成包，包括沙盒启动失败、超时和资源不足场景。[Playwright 容器与 seccomp 说明](https://playwright.dev/python/docs/docker)

本镜像固定 Playwright 1.59.0。该标签的官方 seccomp 文件共 12,997 字节，SHA-256 为 `cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849`。部署者将下载的原文件放到下例的配置路径后，用 `sha256sum` 核对。更新发行版时一起复核文件和宿主兼容性。[与镜像版本对应的原始配置](https://raw.githubusercontent.com/microsoft/playwright/v1.59.0/utils/docker/seccomp_profile.json)

```sh
docker build --pull -f deployment/docker/Dockerfile.minigame-probe -t lanshare-minigame-probe:1 .
python3 tools/minigame_probe_queue.py serve \
  --game-root /srv/lanshare-games \
  --queue /srv/lanshare-probe-queue \
  --seccomp /etc/lanshare-probe/playwright-seccomp.json
```

上例中 `/srv/lanshare-games/staging` 是应用 `DATA_ROOT/games/staging` 在 broker 一侧的只读挂载。队列在两者间共享，应用容器内挂载为 `/app/data/games/probe-requests`。部署账号配置一个 broker 进程，预热已固定版本的镜像，对工作目录实施磁盘限额。将下面的 JSON 数组作为应用环境变量 `MINIGAMES_PROBE_COMMAND` 的完整值。

```json
["/usr/local/bin/python","-I","/app/tools/minigame_probe_queue.py","submit","--queue","/app/data/games/probe-requests"]
```

broker 只接受固定格式的暂存 ID 和 SHA-256，单次只挂载这个任务的 `prepared/runtime`。容器使用非 root 用户、只读根、512 MiB 内存、独立 tmpfs 和 `--network none`。探针重新计算清单摘要，分别双击 `main.html`、在不透明 iframe 中完成握手并检查 SDK 就绪结果。网络目的地仅有本次 Playwright 拦截提供的两个虚拟站点。执行器不就绪时，教师能看到「待浏览器验收」及自动接续说明。包本身的加载或握手失败会以阻断结果反馈。临时浏览器进程与容器先关闭，再删除本次工作区。

开发机独立回归允许通过 `--synthetic-digest` 指定一份已逐文件审阅的测试夹具指纹。探针在启动浏览器前复算全部文件，对其他摘要拒绝执行。生产使用上面的容器路径。

独立主机由服务管理器长期运行一个 broker，挂载就绪后再启动。通过两台主机间的私网共享暂存区与队列，并保持时钟同步。课堂端向队列原子提交任务，执行机仅需读取暂存树、在队列内原子改名并回写结果。生产 Compose 从受保护的 `minigames.env` 向 `game-api` 注入上述命令。首次上线用合成游戏验证一次完整验收，随后停止 broker，确认教师能看到「待浏览器验收」，重启后同一版本应进入可试玩状态。执行器断电后，已过期的任务会释放；无人领取的结果和写入中断的临时文件经五分钟宽限后回收。

启动成功后，探针在自己的空白文档中逐个检测 `static/` 下的全部文件。在线文档使用和游戏一致的 opaque iframe、CSP 及 CORS；双击环境保持浏览器默认文件权限。图片需要解码并读到一个画布像素，字体需要通过 FontFace 加载，音效需要得到媒体元数据并由 Web Audio 解出样本，视频需要解出有宽高的第一帧，关卡 JSON 需要通过浏览器 UTF-8 解码与解析。离线音视频用相对或绝对 `file:` 媒体地址，字体、像素和 JSON 采用浏览器 File/Blob API 从同一批本地字节读取。这个步骤证明素材可用，作者仍需在游戏的真实操作路径验证字体应用、画面和音量联动。

单个展开文件最多 32 MiB，全部文件合计最多 192 MiB、最多 1,000 个。素材串行解码，单项预算 3 秒、两种环境共 14 秒，音频先用元数据估计 PCM 体积。超出时间、内存或暂时无法启动的作业保留待重试。确定性解码失败在教师报告中显示资源路径、环境和专用错误码；成功报告保存平台、离线和全部素材数量。测试夹具中的方块字形、纯色影片、静音 WAV、纯色 PNG 和关卡坐标全部为合成数据。

把 `MINIGAMES_DELETION_REGISTER` 指向独立、持久且有保留政策的审计卷。缺省目录为 `DATA_ROOT/audit/minigame-deletions`，同卷默认值仅满足本机保存，异地恢复需另行复制至独立备份介质。数据库 dump、游戏版本文件和删除回执必须配套备份并演练。

教师可在管理页主动请求修复指导或编目草稿。默认返回确定性提示。部署账号设置 `MINIGAMES_AI_SUGGESTIONS=1` 后，后台持久作业会通过既有 AI 网关低优先级生成草稿。修复仅传错误码和标准指导，编目仅传名称、简介、玩法和人数。教师核对后手动保存。引擎超时、不可用或响应格式不符时，作业退回标准指导；返回值带有来源标记。

## 删除登记与离线恢复

由备份系统封存**最新**独立登记的只读快照，在独立且有访问控制的备份目录中记录 `seal` 返回的条目数和摘要。完整性以该独立记录为依据。不要从旧数据库随附的旧登记重新计算摘要来替代最新封存。封存时停止受理新的删除，或对登记卷取一致快照。

```sh
python tools/replay_minigame_deletions.py seal --register /backup/latest-delete-register
python tools/replay_minigame_deletions.py reconcile \
  --register /backup/latest-delete-register --expected-digest "$SEALED_DIGEST" \
  --restore-data-root /srv/restore/lanshare-copy --postgres-copy lanshare_restore
```

后一个命令默认只核对。将隔离 PostgreSQL 的连接串放入 `MINIGAMES_RESTORE_DATABASE_URL`，保持应用和内容主机关闭，由专用恢复角色连接该副本。SQLite 演练改用 `--sqlite-copy /srv/restore/lanshare-copy/db/classroom.db`。检查数据库名、封存摘要、将清理的游戏数和保留版本校验数后，在同一命令上加入 `--apply --confirm-offline` 重放。物理清理复用在线所有权清单；所有旧内容租约及试听回执一同失效。完成后再次运行只读核对，此时 `gamesToErase`、`previousLeases` 均为零，并执行合成玩家探针后才开放流量。未封存的删除记录、未知文件或保留版本的素材摘要不符时，请先核对匹配备份并保持该副本离线。
