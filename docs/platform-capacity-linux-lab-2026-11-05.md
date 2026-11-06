# Linux 容量验收实验环境记录

更新至2026-11-06。本文记录隔离环境和已执行的Linux实验，不替代[实施报告](platform-capacity-implementation-2026-11-05.md)的性能结论。**候选20通过185项Linux专项及9项实际业务功能门禁，并已启动完整一小时混合压力。**候选19曾在前台启动前遇到Docker地址冲突，已接收任务按原规则排空，其失败证据完整保留。未部署、未推送，没有连接真实业务库运行测试。

## 已完成的环境准备

| 项目 | 已观察事实 |
| --- | --- |
| WSL | 安装官方 stable 3.0.3 x64 MSI，Microsoft 签名 `Valid`；安装返回 0，没有请求或执行主机重启。MSI SHA256：`ae977286974255bd26bf925f00f40f5692c6b2084155cbc0563c09aea091f211`。 |
| WSL 限制 | 新建用户 `.wslconfig`，配置 2 CPU、4GB、swap 0；当前会话仍返回 `Wsl/E_ACCESSDENIED`。额外的系统提权诊断被取消，没有重复申请，没有为此修改主机安全设置。不能将 MSI 安装成功写成 WSL 已可运行。 |
| Linux 替代环境 | 使用既有 VMware Workstation 26，下载官方 Ubuntu 24.04 cloud VMDK，大小 590,749,696 字节，SHA256：`9a1e3959c9f4ad9d4752101fd4ff9a43191fb7d72e61fa20e033f9fd235ce794`；转换为本次独占的 32GB thin 磁盘。 |
| VM 归属 | `E:/LanShareLab/VMware/lanshare-capacity-20261105`，VM nonce 为 `74c2678d343445b2a0fa6cc3fdf88f06`。这与每次容器实验另建的 lab nonce 是两层归属，不能混用。 |
| 实际资源 | 2 vCPU、4096MB，实测 `nproc=2`、可见内存 4,106,104,832 字节（约 3915MiB）、swap 0、ext4；内核 `6.8.0-147-generic`，cloud-init 已结束。 |
| 网络与 SSH | NAT 地址 `192.168.101.129`；预置 ED25519 主机密钥固定校验。SSH 配置位于上述 VM 目录的 `keys/ssh_config`，Host 为 `capacitylab`，`StrictHostKeyChecking=yes`。私钥及 NoCloud ISO 只留在本次独占目录，不进入仓库或源码包。 |
| 容器工具 | 已安装 Docker 29.1.3、Compose 2.40.3、Buildx 0.30.1。已按正式工作流构建 runtime `55e9958a5479fae3d6a7`、frontend `371c536b31cff875f679` 依赖镜像；应用通过正式Dockerfile、`--network none`构建。每次实验另核镜像ID、源码及发布卷。 |

已拉取的官方基础镜像摘要如下。Python／Node 与正式依赖配方绑定；PG／Nginx 的实际运行镜像仍须在容器清单中核实。

| 镜像 | 拉取摘要 |
| --- | --- |
| Python 3.12 slim trixie | `sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea` |
| Node 22 bookworm slim | `sha256:b1e7fcc44bd47f2d186de26c1202345369e7f1028b08956e75cfb52ad8e483f9` |
| PostgreSQL 16 alpine | `sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab` |
| Nginx 1.27 alpine | `sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10` |

证据索引：工作区 `.codex-temp/capacity-work-state-20261105.json` 的 `wsl_install`／`vmware_lab`，`E:/LanShareLab/Installers/wsl303-install-result.json`，以及 VM 目录的 `bootstrap-manifest.json`。这些是本机实验记录，不是仓库内可移植的测试结果。文档不保存密码、私钥或实际 env 内容。

## 实验边界与当前范围

整个 VM 的 2 CPU／4GB／无 swap 预算共同承载 app、PG、Linux Nginx、真实调度器和受控后台压力，早期由Windows生成负载；baseline9开始改由独立Linux生成器，不计入后端预算。生成器是另一个4vCPU／2048MB／swap0的Ubuntu24.04 VM，地址192.168.101.131，工作目录在ext4；无Docker／PG／真实数据库。每轮实际CPU、内存和调度延迟单列，不能以生成器积压冒充服务器吞吐。不得把各角色分别分配的资源相加后仍称 2 CPU 验收。源码、Docker 数据与实验卷必须位于 VM 的 ext4。

控制器只使用明确的源码 SHA allowlist 和新随机 nonce；生成独占 Compose project、卷、内部网络、合成凭据及 TLS 证书。首次启动前拒绝预存同名卷和网络；停止保留实验卷，不执行全局 prune、任意卷清理或真实业务数据迁移。Docker daemon 固定为 VM 的本地 Unix socket。

controller 与后台 bootstrap 在导入应用前核对固定 marker、精确合成 DSN、PG system identifier／数据目录／数据库名／用户／地址、数据库内 singleton nonce 和完整 200 个合成身份；禁用 `.env`，安装外部 DNS／socket 防护。app 使用生成后复核的合成 env，整栈位于内部 Docker 网络，禁止 provider／邮件／爬虫外联。客户端使用显式 handoff、固定 TLS CA、loopback 转发和身份指纹，只允许 `run`，不读取或创建 PG 数据库，不模拟原生 `PG_VERSION`。

早期模式只覆盖 **app＋PG＋Nginx＋真实 scheduler／wanderer inbox 入口＋受控 supplier wait**。提醒使用真实站内通知和收件人核对，投影使用真实计算及提交后版本观测。1–3秒受控等待没有AI批改、授证或真实供应商效果。候选20的第二模式另外运行原AI、Agent、Mailer、Crawler和两个散修收件箱进程，使用内部受控的模型、SMTP和源站协议服务，验证实际业务提交、回执及故障处理，不能据此认证外部供应商质量或吞吐。

## 可复现的操作顺序

以下接口已经过新建实验、正式构建和DB闭环实跑；**接口运行成功不等于性能通过**。每个候选使用新的空runtime；不要复用或覆盖已有实验。运行前复核当前工具 `--help`，若接口变化先更新操作记录。

1. Windows 仓库中以已审查 release allowlist 为基线生成 Linux allowlist，检查输出的 changed／added 项，再创建只含允许源码的新 bundle。示例路径均属于 `.codex-temp`：

   ```powershell
   python tools/platform_capacity_linux_lab.py refresh-manifest --source-manifest .codex-temp/capacity-b-final-release-files.json --output .codex-temp/capacity-linux-source-files.json
   python tools/platform_capacity_linux_lab.py prepare --source-manifest .codex-temp/capacity-linux-source-files.json --runtime .codex-temp/capacity-linux-bundle
   ```

2. 使用固定 SSH 配置将 bundle 与同 SHA 的 controller 脚本传入 VM。由 `lab-plan.json` 取得本次 lab nonce，不打印其中合成凭据。Linux 上显式指定 `$CONTROLLER`、`$LAB_BUNDLE` 和新目录 `$LAB_RUNTIME`；runtime basename 必须恰为该 nonce。`$CLIENT_IP` 是 Nginx 实际见到的精确私有来源地址，须按本次转发路径核实，不能直接套用 VM NAT 地址或放宽为整个网段。

   ```sh
   python3 "$CONTROLLER" stage --bundle "$LAB_BUNDLE" --runtime "$LAB_RUNTIME" --client-ip "$CLIENT_IP"
   python3 "$CONTROLLER" build --runtime "$LAB_RUNTIME"
   python3 "$CONTROLLER" start --runtime "$LAB_RUNTIME"
   ```

   `start` 应先建立空 PG 的物理归属，再使用新建 SQLite schema 与既有 exporter 创建完整 PG foundation；全部父 UNIQUE／索引必须先于依赖它们的外键，保留所有约束。随后才执行真实 PG 初始化、200 个账号、显式材料发布和真实 social／reading 种子、正式资产清单与服务启动。该链已在Linux实跑。跨引擎仅按原生元数据证明处理两个特例：`schema_migrations.success`从0/1 bigint转boolean，空的`signature_access_requests.cancelled_at`从TEXT转timestamp；验证值与约束，不通用放宽日期列。

3. controller生成`client-handoff`，安全传入独立Linux生成器的本轮目录。完整源码按本轮SHA重新解包验证；不能拿旧候选的backend字节配新handoff。通过`client`生成纯客户端runtime，保持固定CA校验，不关闭TLS。生成器使用私有loopback TCP relay，透传TLS到后端VM中精确本轮NginxIP:443；后端relay只允许生成器的精确NAT地址。两个relay均设单进程FD8192、记录连接／管道错误，不记录payload。Docker内部网络和外联防护保持启用；未增加公开监听或整网段白名单。

   早期SSH转发因FD不足导致400WS未就绪，已保留失败并停用。Windows原生负载还观测到ConnectError及errno22／winerror52；根因尚未证明，因此改为Linux生成器消除Windows数据路径，不能把所有长尾归因Windows。

4. 先用`/student/login`（不是`/login`）与实际immutable资产做200及release预检。Linux上`background-start --runtime "$LAB_RUNTIME" --duration 420`启动短对照，完整一小时则使用3720秒；真实dispatcher接受任务、关闭入口后排空。短对照与一小时分别使用新nonce和证据范围，不混合吞吐。生成器执行原探针，例如完整一小时：

   ```sh
   ulimit -n 8192
   "$LOADGEN_VENV/bin/python" "$LOADGEN_SOURCE/tools/platform_capacity_probe.py" run \
     --runtime "$CLIENT_RUNTIME" --lab-manifest "$CLIENT_RUNTIME/lab-manifest.json" \
     --users 200 --duration 3600 --rps 40 --realtime --socket-workers 4 \
     --socket-connect-limit 8 --social --reading --ambient
   ```

   保留原门槛：普通 API p95 500ms、页面 p95 1000ms、登录 p95 2000ms；400 条 WS 是每人课堂和社交各一条。32个有界握手同时建立最终400条连接，30秒就绪门槛不变；此设置不减少最终连接数，不重试失败，不放宽延迟门槛。异常 string statuses 是兼容的工作流错误事件，HTTP 响应总数只统计整数 status。

5. 负载结束后，在 Linux 执行 `background-stop-intake`，等待已接受工作及投影排空，再执行 `background-stop`。将本次客户端 `capacity-report.json` 复制进 `$LAB_RUNTIME/artifacts`，由 controller `verify` 进行最终只读 DB、容器、源码、TLS、代理和资产证据核对；最后用 `stop` 停止本次实验角色并保留卷。

   ```sh
   python3 "$CONTROLLER" background-stop-intake --runtime "$LAB_RUNTIME"
   python3 "$CONTROLLER" background-stop --runtime "$LAB_RUNTIME"
   python3 "$CONTROLLER" verify --runtime "$LAB_RUNTIME" --client-report "$LAB_RUNTIME/artifacts/capacity-report.json"
   python3 "$CONTROLLER" stop --runtime "$LAB_RUNTIME"
   ```

停止信号走真实 dispatcher 的两 worker／线程排空与最终心跳；5 分钟是容器宽限预算，不是任意长处理器必完成的保证。强杀只能依据既有 lease／claim fencing 恢复，不能把未确认工作强制标成 pending 或成功。生产 quiesced migration 的既有 `--timeout 120` 会覆盖 Compose 5 分钟，此实验没有修改部署流程。

## 结果记录要求

分别保留客户端性能报告与 controller 最终核验，不能互相替代：精确 nonce／run／镜像与源码指纹、VM 资源、TLS／资产、400 WS 生命周期、所有 accepted 回执的真实入账、200 投影 dirty／版本、有限任务逐 ID 的存在与 done、提醒的指定收件人、worker exit code／OOM／state／measurement errors。秒精度 health 时间不能直接作为微秒 receipt 的包含式文本上界；短 smoke、未知提交和重复回执必须保留原范围，不能事后改写数据配平。

Windows B 已有完整一小时稳定记录：400 WS、275,416 个 HTTP 响应、0 故障事件，有限任务 1370 个完成、200 投影干净；但冷登录 p95 4785.51ms、集中进房 HTTP burst p95 2239.93ms 超过门槛，因此正式状态仍为失败。证据前缀 `.codex-temp/capacity-b-full-attempt2-`，run `f8b17e2b07c242599169351f37b44f56`。这既不等于 Linux 通过，也不能由后续 Linux 环境安装成功替代性能验收。

## 已实跑的Linux对照与诊断

完整source7含2469个允许文件（包括104个tracked vendor），源码指纹`96b1767f1f68979ad02a7a8d4c660ae65e30c62f86e5f99ecbeb82b2be5c2528`。正式frontend graph为`e862ca4339cf144673b533d00694f3852b4398b315d457a71da5a49034463d69`。早期source6实验漏导出vendor并产生HTML500，属于实验包缺陷；已保留失败，不能说产品Dockerfile丢失vendor。

| 实验 | 实际结果与界限 |
| --- | --- |
| Linux7／Windows生成器 | 306.05秒，11763 offered、3451 cap-skips、375 ConnectError；400WS曾全部就绪。页面／API同时有服务器长尾，不能全归因网络。475+823批全部applied、200快照clean、160有限后台任务done。整体失败，已停止本轮角色／relay并保留卷和工件。 |
| Linux8插桩诊断 | 200真实登录、400WS及60秒混合负载。主／ambient483批全部applied、94有限任务done、200快照clean。199个完整服务端timeline，另一个请求跨度超限，不能拼出200人的完整服务端P95。发现材料async入口同步PG等待与重复SQL转换；采样不是CPU百分比，也不证明全部冷登录长尾原因。非正式性能验收。 |
| Linux baseline9／独立Linux生成器 | nonce`b544282ade7b44f193996b30a8362b8a`，run`525bf579e8144a16bfa7a8ba43fc1e08`。306.67秒，11832 offered、905 cap-skips、HTTP／ambient异常0，但1条social连接提前断开；dispatch lag P95约12.57秒。642+947批全部applied、200快照clean/current、160有限任务done、pending／Q0。**整体失败**，不能称稳定40RPS。 |

baseline9后端App exit143，日志同时记录真实FastAPI关闭、Uvicorn Application shutdown complete和Finished server process，未OOM；Uvicorn0.37优雅退出后会重抛原SIGTERM，因此保留143及有序关闭证据，不篡改为0。两relay停止后active0；生成器relay最终0错误，后端relay最终35个pipe_errors、0connect/rejected，错误所处精确阶段尚未确定。证据为`.codex-temp/capacity-baseline9-server-evidence.tgz`和`capacity-baseline9-gen-private-evidence.tgz`，后者含仅限合成环境的私有handoff，不进入源码或Git。

新candidate10仅改变SQL纯转换缓存、材料完整read工作单元下沉和Nginx按server覆盖默认重复日志；与baseline9源包仅4个文件不同。bundle为`.codex-temp/capacity-linux-bundle10-20261106`，nonce`61605e70542c4e54ae3b3a23b98f6ed0`；指纹`46871368dd430624a0d549f2ee4cbd517a7a7cf3be67ef26a8a0d48ae4d8191c`。实测run`56545d3756bc4cfbb16d099b7d0c9e7e`：302.85秒、12001actions、skip0，17340主HTTP+5612ambient=22952响应，HTTP/ambient错误0；但登录P952775.42ms、dashboard5353.88ms、材料内容8110.64ms，仍有1social早断和dispatch lag P951450.43ms，**整体仍失败**。706+984批全部applied、200投影clean/current、160finite done、pending/Q0、无handler/measurement失败，独立controller源/资产/config/DB闭环通过；两种结论分别保留。两relay最终Gen pipe0／App pipe4，connect/rejected0，均停且active0。证据包`.codex-temp/capacity-candidate10-server-evidence.tgz` SHA`25b2bab37e3afa9b4da2b5abf82b381d0bdbce6be30c63fd83c1aed7dc91766e`，对照细表`.codex-temp/capacity-candidate10-analysis/COMPARISON.md`。对照实现到达流不同（baseline有漏发/跳过），不能把观察到的延迟差归于单一改动或当作严格等效到达率因果实验。

candidate11仅再增加Linux CPython条件的`uvloop==0.23.0`，正式runtime依赖指纹`99cd227caff9d30225eb13190c3023d0d6818bf565d6291134cacc15241d4acd`、应用image`a32fd87decf599aa18aea1214c319f957d71c1b72b51e8b5d2b3037fda41b13d`。真实Linux Uvicorn0.37 auto loop factory返回uvloop.Loop；隔离21tests全部通过，11项材料异步用例逐一核实实际使用uvloop，0.514秒，无network/PV、无skip。此时没有BG、学生登录或主负载，不能先宣称性能提升。Windows条件marker明确跳过新包，原asyncio实现保留。依据为[Uvicorn原实现](https://github.com/encode/uvicorn/blob/0.37.0/uvicorn/loops/auto.py)和[uvloop发布记录](https://github.com/MagicStack/uvloop/releases/tag/v0.23.0)，最终收益仍须实测。

## 实时连接的协议复现

baseline9/candidate10唯一早断连接分别约30.135／30.041秒，贴近原30秒HTTP keepalive，但这本身不证明因果；两轮现有writer统计均不支持队列溢出／过期／发送失败。随后用独立极小ASGI服务（无应用和DB导入）实证Uvicorn0.37＋httptools协议路径：先输出Content-Length完整HTTP字节而more_body仍为true，复用同TCP升级WS并收到101/hello，再释放旧HTTP的末尾空body；旧HTTP会给已升级transport启动keepalive，1秒后TCP直接EOF、没有WS closeframe。新TCP建立WS的对照1.6秒仍开。实验仅把自身timer设为1秒便于复现，没有改变产品门槛、30秒配置或真实验收负载。脚本/输出为`.codex-temp/reproduce-http-keepalive-websocket-upgrade.{py,log}`。

下一候选将为`/ws/`提供独立无keepalive upstream，保留普通HTTP的32条复用池，不改协议/鉴权/timeout、不patch第三方源码。还须由candidate11的被动连接元数据证实真实压力中的触发顺序，再以新nonce无插桩混合负载验证修复。生成器主HTTP进程也有约1核忙的采样，两端均需profile；不能把全部服务器upstream长尾改写成生成器原因。

真实AI批改、散修、Agent、mailer、crawler及自动画像的全部后台角色受控供应商联合实验另有独立draft，仍待新nonce VM实跑；真实SDK／队列／回调／恢复／数据库事实与外部协议stub必须分别披露，不能把mode1的sleep任务改名为完整业务验收。


### 已确认的断连链与发生器瓶颈

source11带被动metadata的真实120秒实验run`5c6e61b5157c4cf38b95f8dbd87045f6`捕获同一transport：旧HttpToolsProtocol完成HTTP响应时transport已是WebSocketProtocol，30秒后旧HTTP keepalive回调关闭它，77微秒后legacy connection_lost且无WS closeframe，继而ASGI1006。对应课堂连接ordinal170；另有客户端typed关闭及hashed Nginx请求关联，400条诊断记录无drop。证据`.codex-temp/capacity-candidate11-analysis/ws-causal-chain.json`，不依赖“恰好30秒”的推测。这一机制同时影响课堂／社交，后续独立WS upstream修复仍须无插桩负载验证。

该轮129.33秒含排空、4052actions、10skip、6107主HTTP+2069ambient。原报告保持diagnostic_only与原门禁failed。238+390批全部applied，200投影clean/current，94有限后台任务done，pending/Q0；诊断DB事实读入使用另存绑定原SHA的派生business-input，不改原报告，不把durability成功称容量通过。采样窗口未对齐：Gen为steady+5.642至46.050秒，App为+89.455至131.072秒、后者含排空；首次App PID筛选因docker-init同含脚本名而拒绝，未错挂，原拒绝日志保留。

Gen179条活动线程记录中134条含httpcore连接池分配及idle／expire／socket-readable子调用链。这是记录样本，不等于CPU时间百分比；独立无网络实测原函数存在C×I与Q×C同步扫描。新工具显式`--http-pool-shards 16`，默认1保旧实现：16池合计仍400max／200idle、原30秒四阶段超时／5秒idle期限、同一TLS context，原200独立登录、400WS、开放40actions/s、ambient与SLO门槛不变。按已验证token的cohort ordinal固定路由，报告记录新工具SHA与预算；这会改变池队列公平性，必须作为新发生器版本验收，不回改旧失败。19项HTTP及10项ambient隔离回归通过；实际Linux全负载结果尚待验证。

### 冷提示语的真实事务复现

运行时合并包为327条（252学生／75教师），不能只数首次字面量得到187。旧ensure在调用者commit前发布进程ready，且把该事务视图写入共享pool。两条真实SQLite连接、独立隔离文件复现三种错误：种子回滚后ready仍true；另一连接提前缓存空池、即使后来commit仍空600秒；未提交252学生提示被缓存后回滚，另一连接实际DB0却返回幽灵提示。证据`.codex-temp/life-tip-precommit-repro/`，禁止PG/网络且原源SHA前后一致。新修复按自有短事务启动预热、提交关闭后发布、supplied-conn不publish共享cache、已知写入commit owner后驱逐与generation fencing设计，尚在实施。没有削减提示语、范围、配图或登录响应字段。

## Final15功能失败与后续后端补丁（新PG待验）

真实Final15功能轮保持 **Final15_FAILED**，没有进入capacity阶段或前台混合压力；旧镜像144项Linux定向通过不是全角色功能通过，更不是200人联合通过。专用邮件的受控450因自然邮件journal碰撞未触发、散修活动原600秒门禁失败、爬虫真实career首选与旧网络新闻fixture不匹配，均保留原失败。部分批改、Agent、自然邮件、poll及保存响应后的散修恢复通过，不能覆盖这些失败。该轮全部角色和relay已停止，证据、最终数据库收口与源码指纹封存在`.codex-temp/capacity-final15-functional-summary.json`及原失败证据包；供应商仍为明确的内部协议stub，不认证真实外部模型吞吐。

该轮实际PG错误推动两项产品修复：平台认知查询改用权威`submissions.student_pk_id`，局部SAVEPOINT恢复可选缺表并保留调用者事务，其他程序／连接／压力错误继续报告；散修完整结构的ensure改成一次只读catalog缺项检查，避免新worker无条件44项ALTER与正在运行的tick发生锁反序。缺失PG结构只允许owned startup准备，真正升级须暂停业务；不靠提交前ready缓存，不删除原表、字段、索引或数据，SQLite独立内存及回滚重试合同保留。修改仅涉及`platform_knowledge_service.py`、`schema_wanderers.py`和中央`schema.py`。

Host验证分别为认知44项、散修544项及48项相关回归通过；新增12项SQLite事务测试已包含在散修组中。原生PG测试保持显式跳过，另新增3项owned交错复验待执行。两份freeze清单分别为`.codex-temp/platform-knowledge-fix-freeze.json`和`.codex-temp/wanderer-schema-fix-final.json`；新源码的真实PG执行、再次全角色功能轮与最终200人联合压力均尚未验收，v9后端全量运行中，本文不预填数量或通过结论。后续必须用新nonce／镜像及对应测试载荷验证，不能覆盖Final15旧工件。

相对Final16只读核实2470份源码仅上述3份变化，其余2467份不变；588个UI作者输入和1647个前端／模板／静态／构建输入未变，6份生成审计／台账及230条人工状态保持原样，没有重复运行整组前端命令。精确证明为`.codex-temp/post-final16-ui-audit-delta.json`，既有UI审计仍为pending。


## 候选18／19的功能证据与本轮容量启动边界

候选18在正式镜像实际通过185项（17.550秒，0跳过／失败）和两个严格归属绑定的PG组件复验：知识查询的真实局部保存点恢复与外层事务保留，散修完整结构在两连接业务锁交错下各1条catalog查询、0 DDL，以及只在nonce临时结构内补列／索引、保原数据／约束／序列并清理。它在日历表尚未由原handler按需创建时，提前读表得到真实42P01；之后原刷新及独立邮件、批改、Agent、爬虫、投票、画像和150秒恢复通过。整轮只作失败诊断，未开启容量或前台；原日志和全部排空事实封存于`.codex-temp/capacity-final18-diagnostic-summary.json`。

候选19与18的2470个基础文件字节相同，唯一变更是受控日历验收脚本缺表等待。实际PG初态表前后均不存在时，读取返回pending，连接可用且零DDL；真实原任务随后自然建表，两个年份共4行、503切换镜像和原604800秒续排均通过。19重新绑定镜像执行185项全通过，另用原五项白板PG用例证明并发保存、重命名／删除竞争和版本隔离（第一次包装器私有文件权限错误保留，第二次仅按实际UID启动后5项全通过，私有schema已清理，public／fixture／源码未变）。这5项不改写全量v9报告的303项跳过。

19保持Asia/Shanghai、原20散修作息和600秒周期。04:00–06:00无自主活动是原规则；06:05:13→06:05:23真实周期产生5次自主动作、失败0。之后20人私信44.617秒逐条完成回复／已读／handled，待办唤醒1、反思10、审核闭环、36/4096原AI额度。包括投票200、日历、画像、批改、Agent、邮件、爬虫、散修和原150秒保存回复恢复在内的9项功能门禁全部通过。

容量前置随后接收200份批改和200条私信并持久建立Agent／专用邮件任务。实测内部Docker `/24` 的 `IPRange` 为空；两个正在等待业务完成的controller分别动态占用固定Agent`.7`和Mailer`.8`，常驻容器启动报`Address already in use`。未启动发生器正式前台，也未提前占用爬虫负载期去重报告。原200份批改142.517秒全过，原200条私信408.409秒全过；两controller自然退出释放地址后，仅启动既有Agent／邮件工作排空，不重新投递、不把排空记为一小时达标。

隔离测试工具已冻结最小网络修复：从首次创建mode2隔离网就将动态分配限制在相同`/24`的上半`/25`，固定角色仍用`.2–.11`，保留原网络隔离和精确归属。启动检查实际Docker的Subnet、Gateway、IPRange；阶段切换复用同一网络和PG定义。基础工具32项、控制器31项离线通过；其真实Docker分配另验。生产业务源码和配额没有因这项测试网络修复改变。

19已排空并正常停止：实际440封两类邮件均提交sent、220行为回执全applied、200投影全clean/current、未完成工作和运行定时任务均为0。10角色和双relay实际退出、无OOM；App／AI保留真实143及完整优雅关闭证据，其他业务worker原退出0。候选仍为`capacity_setup_failed`，没有任何前台一小时数据。原终态 `.codex-temp/capacity-final19-setup-failed-summary.json`，AppVM证据SHA256 `074769b1ea8f4befbf90d8922eebf4b5aa4300f6d24158157e7f5b8823384756`，Gen证据SHA256 `8f40842903a43dd6d77060d08ea715cf1d73852b5a3a7a385418d977990930e4`。

独立真实Docker组件使用新隔离网，无PV、端口、应用或PG请求，先实际分配动态`.128`／`.129`，然后固定`.7`／`.8`均成功运行，全部临时资源清理、原网络ID不变。候选20源码2486文件，指纹 `2bc49d08a33aeb1a3b038d91cd96b02cc67f57548c0a3a1b813f0194d411b398`；相对19精确仅隔离工具`tools/platform_capacity_linux_lab.py`一项变化，原业务代码及16个业务联测文件未变。它须重新通过实际新网、正式镜像和完整功能门禁，才运行原一小时。

## 候选20的实际混合运行

真实Compose内两个动态controller同时取得`.128`／`.129`，原Agent／Mailer同时占用固定`.7`／`.8`，网络及PG身份不变，两个临时controller自然退出0。正式镜像专项185项全部通过（14.349秒，无跳过）；原9项实际功能门禁全部通过。独立Ubuntu发生器于北京时间07:19:19.844实际启动200用户、400长连接、每秒40开放动作的3600秒运行，200登录和400应用级连接全部成功，最后一条连接7.353秒就绪。负载窗内实际爬虫复查同一来源，没有重复发布。

第45分钟观测108,021个计划动作、152,866个前台及53,922个ambient HTTP请求，无调度跳过、请求故障、异常或长连接提前关闭。开场200个课堂快照的精确Nginx记录显示请求耗时P95为991ms、上游响应头P95为990ms、上游连接P95为178ms；同请求集合的不同阶段仍需逐请求关联，不能相减独立P95推断因果。保留500ms接口目标，待终态客户端报告与业务数据库事实分别核对。
