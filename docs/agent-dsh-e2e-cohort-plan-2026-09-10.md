# 下一冻结版本的独立 DSH E2E 复验

此文与本次脚本改动只准备复验；没有运行 Docker、连接服务器、读取生产 key 或调用模型。旧 `/lanshare/.codex-temp/dsh-migration-20260910/e2e` 及已有验收报告保持原样。新代码需要新的 cohort，不能覆盖旧证据。

## 已改进的实验边界

`tools/dsh_isolated_e2e.py` 现在必须显式传入 cohort、端口、app/DSH image ID、冻结 commit、archive/manifest/profile SHA256 和预期 active 系统 Agent key ID。不再默认沿用旧镜像与旧源码摘要。`tools/dsh_e2e_cohort.py` 统一派生所有名称；例如 `checkpoint-b` 使用：

| 资源 | 新位置 |
|---|---|
| 实验目录 | `/lanshare/.codex-temp/dsh-migration-20260910/e2e-checkpoint-b` |
| 冻结输入 | `/lanshare/.codex-temp/dsh-migration-20260910/e2e-inputs/checkpoint-b` |
| app / PG | `lanshare-dsh-e2e-checkpoint-b-app` / `lanshare-dsh-e2e-checkpoint-b-pg` |
| 网络 / DB | `lanshare_dsh_e2e_checkpoint_b` |
| app 端口示例 | `127.0.0.1:18002` |

端口只接受 18002..18999；18000 生产 gateway 与 18001 旧 E2E 被排除。实际 prepare 还要验证端口空闲、容器和网络不存在、精确 lab 路径未重定向。已存在的 cohort 永不被 prepare 覆盖。

冻结输入必须是 `source.tar.gz`、`source.manifest.json` 和仅含 `package.json`、`cordis.patch.yml` 的 `profile/`。manifest 需要 `source_commit`、`archive_sha256`、`files`（相对文件名到 SHA256）与 `removed`。逐个 tar member 校验 regular-file 类型、唯一名称、摘要与全量集合，单文件不超过 64 MiB、总计不超过 512 MiB；不接受 symlink、重复、遗漏、路径穿越及未列明文件。必须把三个 E2E helper 和 launcher 一起纳入冻结 archive，不能解包后再用当前工作区文件覆盖。

prepare 会先执行无网络、非特权、受资源限制的固定 DSH image `--evidence`，比对版本 0.1.5-rc.1 和 profile digest；不匹配时，不进行 DB schema/key 转移。源码每个保留文件与 removed 集合在 seed/start/model 阶段重新核验。cohort 配置落盘后，后续阶段参数必须完全一致。

app helper 的 DB guard 同时核对 cohort 派生 hostname、database、`e2e_app` 用户和 `/e2e-data`。生产仅允许 schema-only dump，以及可信 app 内的只读 active-key 查询、解密后以新实验 secret 重新加密；查询结果必须等于显式 expected key ID。原始 key 不输出、不挂给 runner，真实用户会话不进入实验。

teacher/student/admin 模型阶段各需显式 `--allow-paid`，每个角色每 cohort 只启动一次（失败或结果不明时保留启动标记，不自动再收费重跑）。报告必须同时具有三个角色，不能以空集或部分角色判定成功；学生只读检查同时覆盖 A 事务写回执与 B HTTP mutating admission。文件越权检查从实际保存的 task ID 读取，不假设编号一定为 1/2。

## 本地验证与后续执行顺序

`python -m unittest tests.test_dsh_e2e_cohort -q`：7 项通过。覆盖名称与旧资源隔离、端口/摘要强制、真实 CLI dry run 无外部进程或文件修改、DB guard、完整输入预检、重复/缺失 tar 项拒绝、无明确 paid 标记时停止于任何宿主/key 操作之前。语法编译和 diff whitespace 检查也通过。

`--dry-run` 只输出计划；`--dry-run --check-inputs <本地冻结输入目录>` 可额外校验所有 tar/manifest/profile 字节。当前检查使用明确标为 synthetic 的本地参数，不能作为待发布 commit 或新镜像通过证据。

待 root 冻结版本后：

1. 从冻结源码生成完整 source archive/manifest（包含本次 helper）；保存 commit 与两个摘要。选择与目标 profile 完全匹配、已构建并独立验证的新 DSH 镜像 ID。不要对新的 profile 重用旧镜像验收报告。
2. 选择新的 cohort 和空闲 loopback 端口；只读核对服务 active key ID。用同一组完整参数先本地 dry run，再把唯一输入目录上传至对应 `e2e-inputs/<cohort>`。上传工具必须来自同一 frozen archive。
3. prepare 创建新的私有目录/PG/网络并显式 seed，start 创建独立 app 与 launcher。先 attest 保存全部 mount、网络、权限与 image 证据；不改生产 compose/env/service。
4. 获得 root 的执行指令后，以同一 cohort/pins 依次运行 teacher、student、admin（每项带 `--allow-paid`）；生成 report、boundaries，再 attest。任一失败保留原 task、receipt 和日志，不用新 task 掩盖失败。
5. 三角色及边界均通过后 quiesce，停止该 cohort 的 app/PG/launcher并保留 data/source/report；脚本不删除数据。生成新的独立验收报告，和旧 cohort 分开列出。

子代理 workflow 在当前 profile 中仍禁用；此次正常 Agent 复验不能被写成它已激活。若后续需要验证 workflow，先解决宿主可观察的子执行边界与远端 MCP 未停止问题，再单独设计验收。
