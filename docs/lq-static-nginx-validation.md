# S0 nginx 本地引擎验证

2026-09-20。使用仓库 `nginx.conf`、真实构建产物和 `publish_static_assets.py`，在隔离 loopback 环境执行 **15/15 通过**。只把上游地址、HTTP/HTTPS 端口、测试证书和静态根目录替换为任务临时值；location、正则、try_files、gzip 和响应头规则保持仓库实现。

命令：

```powershell
venv/Scripts/python.exe tools/ui/probe_static_nginx.py --nginx .codex-temp/nginx-runtime/nginx-1.27.5/nginx.exe --openssl 'C:/Program Files/Git/usr/bin/openssl.exe' --output .codex-temp/lq-nginx-probe-20260920-final
```

证据：`.codex-temp/lq-nginx-probe-20260920-final/report.json`、`nginx-test.log`、`logs/access.log`。配置源 SHA256 `9ee554882a942c5fdf32c4f103b65b7b4928ded4aea6e0060a99013b498d3322`；资源图 `373c9bae7347989332db785a48ba5413925e86dc28ec99e85b262d34b6561a4a`。

| 验证项 | 结果 |
|---|---|
| 仓库 nginx 配置 `-t` | 通过 |
| 原生哈希 CSS | 200、MIME 正确、字节相等、immutable、未触发上游 |
| gzip_static | Content-Encoding=gzip、Vary=Accept-Encoding、预压缩字节及解压后原文一致 |
| If-None-Match | 304、空响应体 |
| 当前 Vite 文件 | 直接交付，字节相等、immutable |
| 首次升级前 Vite | 经真实 tar seed 后发布当前资源，旧文件仍直接可读 |
| 发布幂等 | 首次729个文件，重复发布0个；旧文件保留 |
| 稳定 CSS、两个 manifest、非法 hash | 均回到合成上游，不带 immutable |
| 合法 hash 下缺失原生/Vite 文件 | 回源404，不缓存为 immutable |
| 清理 | nginx、合成上游退出；三个临时端口已释放 |

运行时是 [nginx 官方 Windows 1.27.5 包](https://nginx.org/download/nginx-1.27.5.zip)，与 Compose 的 `1.27` 分支一致，没有修改部署镜像。下载包 SHA256 `7ca07528f52c02df33735fedbb9cf822d38079f26c3d9f2774ed512af4fdafd4`，仅用于记录本次获取的文件，不代表独立签名验证。`nginx -V` 确认包含 `http_gzip_static_module`；[官方模块文档](https://nginx.org/en/docs/http/ngx_http_gzip_static_module.html)说明其预压缩交付机制。

本机没有 Docker/Podman/nginx 安装，WSL 未安装。本次可验证 nginx 引擎的上述规则，**不能替代 Linux 容器挂载、权限、Compose 启动次序或生产发布验收**。nginx 官方亦说明 [Windows 实现的性能与功能边界](https://nginx.org/en/docs/windows.html)；本次不做吞吐、并发容量或生产环境推断。没有读取生产证书、应用配置或业务数据库，也没有安装系统服务、修改 PATH。
