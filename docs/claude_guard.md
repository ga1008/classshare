# Claude Guard

Claude Guard 是面向 Windows 上 Claude App 与 Claude Code 的本机持续监测器。它解决的是“可观察、可留证、少泄露”，不是用另一个高权限程序去读取更多机密。

## 设计边界

- 监测 Claude 核心进程、原生桥接程序及其子进程，核验安装位置、文件版本、SHA-256 和 Authenticode 签名。
- 每 5 秒采集 Windows TCP 元数据，记录目的 IP、端口、可用的 DNS 映射、协议预期和进程归因。
- 识别 Claude 先连接本机代理的情况。没有代理控制器权限时，只把代理出口称为“共享代理候选”，绝不把其他软件的流量硬归因给 Claude。
- 不采集包体、提示词、代码、文件名、命令行全文、Cookie、令牌或代理订阅信息。
- 不安装根证书，不做 TLS 中间人解密。`443` 只会标记为“预期 TLS，未从端口层证明”。
- 事件详情和 AI 结果使用 Windows DPAPI `CurrentUser` 加密后写入 SQLite；索引只保留时间、风险级别、类型和不可逆指纹。
- 仪表盘只监听 `127.0.0.1`，校验 `Host` 并设置 CSP、禁止跨域、禁止嵌入、禁止缓存。

## 快速使用

先做一次只读检查：

```powershell
.\venv\Scripts\python.exe .\tools\claude_guard.py doctor
```

前台运行并打开本机仪表盘：

```powershell
.\venv\Scripts\python.exe .\tools\claude_guard.py run
.\venv\Scripts\python.exe .\tools\claude_guard.py show
```

监测数据默认保存在 `%LOCALAPPDATA%\ClaudeGuard\events.sqlite3`，仪表盘默认地址为 `http://127.0.0.1:17843/`。

安装当前用户开机启动：

```powershell
.\venv\Scripts\python.exe .\tools\claude_guard.py install-startup
```

卸载启动项不会删除证据库：

```powershell
.\venv\Scripts\python.exe .\tools\claude_guard.py uninstall-startup
```

## AI 复核的隐私策略

程序只从 LanShare 的 `.env` 读取受支持供应商的 Key；Key 仅用于 HTTPS `Authorization` 请求头，不写入数据库、启动项、日志或 AI 提示词。默认优先使用 `DEEPSEEK_API_KEY`，并只允许代码内固定的官方 HTTPS 主机，`.env` 不能把请求重定向到其他服务器。

发送给模型的字段被固定为：

- Claude 产品角色、版本、签名状态、通告范围状态和哈希前缀；
- 公网域名最多保留末三级，私有域名改为哈希；
- IP 仅保留 IPv4 `/24` 或 IPv6 `/48` 网段；
- 端口、协议判断、直接/代理归因类型以及本地规则告警代码；
- 采集能力边界。

不会发送包体、代码、提示词、完整路径、命令行、凭据或 Cookie。AI 只提供复核意见，不能自动阻断网络，也不能覆盖本地确定性高风险规则。

如需完全离线运行：

```powershell
.\venv\Scripts\python.exe .\tools\claude_guard.py run --no-ai
```

## 本机代理

Claude Guard 会自动发现 Claude 到本机代理端口的 TCP 连接。若 Clash/Mihomo 的只读控制器可用，可以按源端口把最终域名精确关联回来；若控制器要求认证，默认不会读取配置文件或尝试提取密钥。

用户可自行把控制器密钥临时放入进程环境（不会写入启动项）：

```powershell
$env:CLAUDE_GUARD_PROXY_CONTROLLER_SECRET = '<controller secret>'
.\venv\Scripts\python.exe .\tools\claude_guard.py run
```

开机启动如需代理精确归因，应通过 Windows 凭据管理或专门的受限控制器配置提供密钥，不要把密钥写进 `ClaudeGuard.cmd`。

## 风险解释

- **严重**：通告范围内的 Claude Code 版本、签名不可信/哈希不匹配、疑似明文公网传输。
- **高**：签名无法验证、非常规位置运行、使用 `--dangerously-skip-permissions`。
- **中**：直连公网的未知应用协议端口。
- **低**：未知公网目的地，或无法精确归因的共享代理候选出口。
- **信息**：本机代理、已披露服务、常规加密端口和其他基线事件。

“未知”不等于“恶意”；“官方域名”也不等于具体请求内容一定合规。判断必须结合版本、签名、归因可靠性、时间模式和组织规则。

## 不能解决的问题

在不解密 TLS、不注入 Claude 进程、也不启用内核级文件审计的前提下，无法直接证明某个 HTTPS 请求内部是否包含某个源文件。这是刻意的安全边界。若组织需要内容级 DLP，建议把 Claude Code 放在隔离虚拟机/开发容器中，并通过组织自有、受审计的专用 LLM Gateway 出口执行允许列表、内容脱敏和审计；不要在个人电脑上临时安装抓包根证书。

轮询可能漏掉存活时间短于采样间隔的连接。更高等级取证应由管理员在隔离环境中启用 Windows Filtering Platform/Sysmon 或企业 EDR，并把日志交给 Claude Guard 之外的受控 SIEM；不要在日常终端上无边界开启全系统包体抓取。

## 参考边界

- Anthropic 的 Claude Code 数据使用文档说明：模型交互会经网络传输，传输使用 TLS；Statsig 与 Sentry 属于其披露的非必要遥测/错误上报流量，并提供关闭相关流量的环境变量。
- Anthropic 的企业代理文档列出 `api.anthropic.com`、`statsig.anthropic.com`、`sentry.io` 等网络需求，并建议组织通过代理进行安全、合规和监测。
- 校内风险通告所列 `2.1.91–2.1.196` 仅适用于 Claude Code 版本体系，不能套用到 Claude 桌面端的 `1.x` 版本号。
