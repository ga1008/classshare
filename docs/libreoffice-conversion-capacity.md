# LibreOffice 公共转换容量与异常恢复

所有 `convert_office_file` 调用使用 `DATA_ROOT/tmp/libreoffice/_locks/slot-N.lock`。默认并发为 1，`LANSHARE_LIBREOFFICE_MAX_CONCURRENCY` 可配置为 1–4；部署时所有进程须保持同一配置与 DATA_ROOT。该锁不同于文档预览的 PDF/图片渲染预算，也不同于简历导出的请求预算，嵌套使用不会重复获取同一把锁。`--version` 的短预检不占重型转换槽。

忙时立即抛出 `LibreOfficeBusy`，`retry_after=10`。文档预览映射为原有 `DocumentRenderQueueBusy`；Excel 导入映射为 HTTP 429 / Retry-After 10；素材抽取保留忙异常，顶层解析接口映射为 429，不能转成文件损坏或不完整结果的 warning。后台调用者应交还任务系统重试，不持有数据库连接等待容量，也不应改走其他 Office 转换通道。

## 工作进程被终止时

OS 文件锁本身会随持有进程退出释放。为了避免此时仍存活的转换子进程占用额外资源，每个槽另有原子写入的 `slot-N.json`：

1. 在创建转换子进程前记录唯一 UserInstallation 参数、转换截止时间与临时目录。
2. 创建后记录 PID 与进程创建时间，防止 PID 被复用后误杀其他进程。
3. 重获文件锁时，核验 Office 进程与已登记 PID 的命令行。旧转换仍活着时继续返回忙；即使工作进程死于创建子进程和登记 PID 之间，也能用唯一标记找回转换及携带该标记的子进程。
4. 超过原转换截止时间后，只终止创建时间再次核验通过、且仍带唯一标记的进程。当前请求仍返回忙，后续请求确认退出后才可开始转换。
5. 转换进程已退出后清除占用元数据。异常退出遗留的临时目录只在严格核验其为系统临时目录下 `lanshare-lo-*` 根目录后清理。锁文件永久保留，避免删除重建 inode 导致两份独立锁。

这里采用持久保留容量的方案，不承诺工作进程死亡时瞬间杀死所有子进程；在已验证的旧转换完成或截止时间到来前，容量不会让给新转换。正常失败与超时会主动清理本次已验证的转换进程。

## 验证与运行边界

`tests/test_libreoffice_shared_capacity.py` 使用 Windows 上真正的 spawn 进程、OS 锁及合成转换子进程验证：32 进程限 1、20 进程限 2、失败/超时/子进程终止、工作进程强杀后的子进程树不重叠、登记 PID 前竞态、截止后回收，以及无关进程/PID 复用保护。真实 LibreOffice 的 DOCX→PDF 另通过隔离 smoke，输出位于 `.codex-temp/libreoffice-shared-qa/`。

进程检查复用已锁定的 psutil 依赖；只读取 Office 候选及已登记 PID 的命令行，避免扫描所有命令行造成 Windows 延迟。若占用元数据损坏或进程身份因权限不足不能确认，保守保留忙状态，不杀无法核验的进程。运维须先确认对应转换已退出再处理此类状态；不要在进程仍活着时手动删除槽文件。进程核验针对标准 LibreOffice 启动器及携带本次 UserInstallation 标记的转换进程，不用于管理用户独立启动的 Office。

当前故障注入与真实 Office smoke 在 Windows 执行；POSIX 使用同一身份恢复逻辑及 flock 分支，尚未在本轮机器上执行 Linux 故障注入。网络文件系统和跨主机部署须另外验证其文件锁语义。本轮未启动长期负载测试。

## 冻结版本后的真实导出验收工具

`python -B tools/resume_real_export_capacity_probe.py --output .codex-temp/resume-export-20-final --versions 20 --workers 4`

目录必须为新目录。工具使用三个真实简历模板轮换生成 20 份不同冻结内容，通过 `export_resume_cached` 和本机 LibreOffice 首次导出，再对同一批内容重复导出；不连接数据库，也不替换转换器。每份 PDF 必须包含自己的独立版本标记，不能出现其他版本标记，重复导出的字节必须相同，真实转换调用次数必须保持为 20。繁忙请求按 10 秒加少量抖动退避。

每 50 毫秒采样本次独立 UserInstallation 标记对应的 Office 进程，报告重型 `soffice.bin` 并发与独立转换 profile 并发；控制台启动器单列，不能把同一转换的启动器与重型进程当作两个并发任务。最终确认本次 Office 进程、活跃槽元数据和临时写入文件清零，并实际重新获得再释放永久锁文件。报告记录开始/结束代码指纹，中途改码不能算固定版本验收通过。输出包括 PDF、原始 HTML、进程采样与 `export-capacity.json`；应在浏览器导出验收之前单独运行。
