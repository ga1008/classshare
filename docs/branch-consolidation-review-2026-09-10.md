# 分支整合与发布验收记录（2026-09-10）

## 分支、历史工作树与材料功能审计

本节记录整合前的独立审计。最终分支删除、远程推送、部署版本和线上验收结果另由发布总验收补充。

### 提交覆盖关系

对全部本地分支、`origin` 分支执行提交图、可达性和补丁等价检查。以已上线白板发布分支 `codex/whiteboard-release-20260910`（`2dbbd457`）为基准：

| 分支 | 发布分支尚未包含的提交 | 审计结论 |
| --- | ---: | --- |
| `claude/epic-elgamal-72bdb7` | 0 | PostgreSQL 架构契约和 LibreOffice 探测改进已包含 |
| `claude/zen-kowalevski-daed5a` | 0 | UX 第 7–9 阶段已包含 |
| `codex/pre-home-classroom-main-20260905` | 0 | 与上述 UX 分支同点，已包含 |
| `codex/pre-home-classroom-dev-20260905` | 0 | 课堂群二维码及文件删除保护已包含 |
| `codex/ui-motion-polish` | 0 | 课程预览、课堂材料和浮层动效已包含 |
| `codex/assessment-multimodal-routing` / `origin` 同名分支 | 0 | 考核分类和多模态批改路由已包含 |
| 本地 `main` / `dev`、`origin/main` / `origin/dev` | 0 | 都是发布分支祖先 |
| `origin/perf/whiteboard-2026-09-09` | 0 | 白板性能及后续可靠性修复已包含 |
| 本地 `perf/whiteboard-2026-09-09` | 1 | 仅 `602dcd2d` 验收文档；`git cherry` 确认与发布分支文档补丁等价 |

发布分支 `681e5fdb` 保存了材料刷新和缓存等线上修复，必须随整合保留。其四个材料文件在本轮开始时与当前工作树内容一致；后续材料筛选函数抽取保留了原功能。应在所有本轮改进提交后合并发布分支，再统一 `main` / `dev`，确保所有功能提交可达后删除其他分支。

独立定向回归结果：

- PostgreSQL 契约、材料签署/快照、签署权限、课堂二维码并发与删除保护、工作流和导入兼容：134 项通过。
- 课程/课堂/学习进度/浮层动效：5 个测试文件、29 项通过。
- 发现的 PostgreSQL 契约遗漏已修复：`schema_material_signatures.py` 通过双数据库启动的签署 schema helper 建表，已登记到现有显式 runtime 清单；没有通过新增部署前强制表要求或放宽断言掩盖问题。

### 历史 detached 工作树

只读检查 `Temp/lanshare-assessment-release-20260907` 下两个部分清理的工作树。对剩余新增、修改和未跟踪文件按换行归一后的内容、Git blob 及发布分支可达历史核验；没有合并或删除这些工作树。

| 工作树 | HEAD | 与当前工作树相同 | 已在发布历史存在的精确 blob | 未发现已提交出处的业务源码 | 部分清理产生的删除项 |
| --- | --- | ---: | ---: | ---: | ---: |
| `merged-worktree` | `a55ce2e1` | 139 | 31 | 0 | 1107 |
| `worktree` | `279e207e` | 132 | 33 | 0 | 1108 |

检查使用 `--untracked-files=all`，包括未跟踪目录内的测试源码。其余 6 个文件仅旧 `static/dist` 构建输出和部署脚本等操作文件。上述批量删除是临时目录部分清理结果，不能作为业务代码删除合并。逐文件机器记录保存在忽略目录 `.codex-temp/branch-consolidation-20260910/historical-worktree-audit.json`。

### 材料筛选、Git 继承和文档缓存复核

`material_library_filter_service.py` 只装饰已经通过原可见性判断的数据。根目录组织/共享标签要求同一所有者且满足完整路径段边界；课堂课程/班级标签按祖先路径继承，既不扩散到兄弟目录和其他仓库，也不新增课堂分配关系。装饰使用两条批量查询，随后按路径深度查表，避免逐文件数据库查询。

Git 同步按当前仓库根目录继承组织、所有权和共享字段，修复旧导入行，重复同步不重复写入。LessonDoc 受保护文件保持独立统计；冲突、认证失败和本地未提交文件覆盖场景继续保持原内容。

渲染资源使用内容哈希 ETag，CSS/JS 在同 URL、同大小、同时间戳但内容变化时仍返回新资源；304 之前分别验证材料包及资产权限，并检查存储文件存在。HTML 保持 `private, no-store` 和每发布一次的缓存失效流程。

独立复核未发现新的可行动缺陷。材料筛选、Git scope、渲染缓存、二维码共享文件保护、LessonDoc 同步、真实 Git 冲突/认证失败和架构预算共 67 项通过；前端异步树、选择恢复、跨仓库切换、过期响应和目录删除回退 12 项通过。

## 原生 PostgreSQL 发布门禁

本轮从生产以一致性 `pg_dump --format=custom --no-owner --no-acl --lock-wait-timeout=5s` 新导出快照，并校验远端、本地字节数和 SHA256 一致。凭据直接由容器环境提供，没有读取输出或写入报告；生产数据和 DDL 未修改。

原生 PostgreSQL 16.14 使用本轮新建的独立集群，仅监听 `127.0.0.1:55441`。从同一原始 dump 分别恢复增量迁移库、完整启动库；未启动应用 lifespan、调度器或业务 worker。完整启动每轮使用新 Python 进程，随后执行小程序首次使用的实际 schema helper。

| 检查 | 本轮结果 |
| --- | --- |
| 快照大小 | 32,600,129 字节 |
| 快照 SHA256 | `c6ef7ae971e48ebd54bc9d4080ddf7cbf948aafaba9bcf0ca630011977c45a4d` |
| 恢复基线 | 230 张表、472,920 行、204 个序列、5,211 个 schema 对象 |
| 两份初始恢复状态 | 完全相同 |
| 增量迁移 | 执行两轮，旧数据/字段/关系/schema/序列差异均为 0 |
| 完整 `init_database()` + 小程序首次使用 schema | 执行两轮，旧数据/字段/关系/schema/序列差异均为 0 |
| 第二轮幂等性 | 增量和完整启动差异均为 0 |
| 新增表 | `mp_bind_rate_limits`、`mp_consumed_bind_tickets` |
| 完整启动必需表 | 两轮均为 158/158 |
| 性能索引 | 两轮均检查 279 项，失败为 0 |
| 启动步骤跳过 | 两轮均为 0 |
| 迁移源码 | 54 个文件，演练前后 SHA256 一致 |
| 备份演练后完整性 | 原始备份 SHA256 未变 |
| CLI 门禁 | `validate_native_pg_rehearsal.py` 返回 `status=ok`、`blockers=[]` |

没有对时间戳、旧序列或旧字段设置差异白名单。唯一增量为上述新表及其索引。独立集群在完成后已验证路径并停止，原始备份和报告保留于忽略目录：

- `.codex-temp/branch-consolidation-20260910/native-pg/native-report.json`
- `.codex-temp/branch-consolidation-20260910/native-pg/snapshot.dump`
- `.codex-temp/branch-consolidation-20260910/native-pg/cli-native-validation.json`
- 同目录 `incremental-raw.json`、`startup-raw.json`、`startup-runs.json` 和 `sources-before.json`

部署必须成对使用本轮报告和匹配备份。最终提交与发布工作树形成后，需要再次校验迁移源码哈希；附件备份、在线服务检查和 Git 远程同步由总验收分别记录。本报告不包含生产业务记录、数据库连接凭据或密钥。
