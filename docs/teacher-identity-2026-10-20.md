# 教师组织推荐、职工号与课表关联（2026-10-20）

本记录覆盖用户管理、个人基础资料和教师登录的组织与身份改进。源码清点、合成环境验证、浏览器验收、正式构建、生产核验与 Git 推送分别记录；源码中的组件声明不能作为运行时验收证明。

## 数据与身份边界

`teachers.employee_number` 保存默认学校的职工号镜像；`teacher_organization_memberships.employee_number` 保存教师在每所学校的独立职工号。两列均为 `TEXT NOT NULL DEFAULT ''`，保留前导零，输入经过 NFKC、首尾空白清理与大写归一；允许不超过 64 位的字母、数字及 `. _ / -`。不同学校可以使用相同职工号，同一学校不能被两个平台账号占用。

增量迁移在原表上添加字段及索引，不猜测历史职工号，不复制教务密码，也不重建业务表。会员表上的 `(school_code, employee_number)` 非空唯一索引约束并发冲突；独立登录索引支持按工号查找。停用归属仍保留工号占用，避免历史身份被其他账号接收。服务写入继续使用原有账号管理事务锁，校验、归属更新和默认字段同步位于同一事务。

切换默认任教归属时同步对应学校的职工号；新增另一所学校时不会复制原学校的职工号。停用默认归属后从剩余活跃归属选择默认项，并同步镜像。不同学校的归属与工号不改变原有 `(role, id)` 平台主体、超管授权或资源权限。

个人资料只能修改已存在的默认学校工号，不能通过资料接口改学校或新增权限归属。提交包含 `expected_school_code`；锁内发现默认学校已经变化时返回冲突，要求刷新资料后重试，防止旧表单把原学校工号写入新默认学校。后续资料校验失败须与工号变更一并回滚。

## 组织推荐与归一

新增 `GET /api/manage/organization-suggestions`，仅登录教师可访问。查询参数为 `school_code`、`school_name`、`college`，返回：

```json
{
  "status": "success",
  "schools": [{"school_code": "school-a", "school_name": "示例学校"}],
  "colleges": ["示例学院"],
  "departments": ["网络工程系"],
  "department_aliases": {"网络工程系": ["网工", "网工系", "网络工程"]},
  "school_code": "school-a",
  "cache_seconds": 30
}
```

推荐来自组织目录、活跃教师的活跃任教归属及无会员记录的历史主归属，经过去重，不返回其他教师的姓名、邮箱或身份编号。已有会员记录但全部停用时，不回退复活历史主归属。停用学校及学院不参与推荐。

学校为全局名称目录；学院按所选学校过滤；系别按所选学校和学院过滤，学院留空时可推荐该学校的系别。未确定学校时不混入其他学校的学院或系别。原生输入框结合 `datalist`，推荐不构成必选字典，仍可自由输入。

服务端快照缓存为 30 秒，最多保留 8 个数据库作用域，按真实 SQLite 路径或 PostgreSQL 数据库与 schema 隔离。组织目录和教师归属写入触发进程内失效；其他进程至多 30 秒到期刷新。前端请求缓存同为 30 秒，最多 64 个父级组合。未提交事务内读取不发布到共享缓存；实际保存始终读取当前事务中的学校标识，不以推荐缓存执行身份唯一性判断。

`canonicalize_membership_scope` 使用明确规则处理 `网络工程`、`网络工程系`、`网工`、`网工系`，并支持已有软件工程、电子信息工程、计算机科学与技术等确定别名及其“系”后缀。未知缩写不依赖 AI 猜测，不按拼音首字母或相似度跨学校、跨学院合并。学校名称精确匹配唯一既有目录时复用其 `school_code`；给定学校标识与名称冲突，或同名学校未指定唯一标识时，拒绝保存并提示重新选择。

## 写入与登录接口

| 接口 | 相关字段及行为 |
|---|---|
| `POST /api/manage/system/teachers` | 新账号要求 `employee_number`，将规范学校、系别与工号写入默认归属 |
| `POST /api/manage/system/teachers/{teacher_id}` | 编辑账号资料和默认任教归属，校验同校工号冲突 |
| `POST /api/manage/system/teachers/{teacher_id}/memberships` | 保存指定学校的独立 `employee_number`，同校只保留一个归属 |
| `PUT /api/profile/basic` | 教师基础资料要求职工号及当前 `expected_school_code`；学生资料不要求工号 |
| `POST /teacher/login` | 原有 `email` 字段可填邮箱或职工号，兼容 `identifier`；学校为可选消歧条件 |

邮箱登录继续可用，包括尚未补齐工号的历史账号。新建账号和个人资料保存要求职工号，历史空值不会在启动时被猜测填充。内部调用未传会员工号时保留既有值，新增学校不继承其他学校号码。

工号登录仅匹配活跃教师和活跃学校归属。跨学校存在多个平台账号使用同一工号时，先要求学校全称或学校标识消歧，不能以“哪个账号的密码恰好匹配”来选择账号；也可直接改用邮箱登录。停用归属的工号不能登录。

## 课表教师关联

`academic_teacher_identity_service.resolve_teacher_account_links` 在读课表时按 `school_code + employee_number` 生成平台账号关联，列表缓存和单教师详情都读取最新身份。保存工号后重查已有本地课表即可关联；同步取得课表后读取同一投影，无须另建易过期的绑定表。

明确填写的会员工号优先于旧教务登录名。只有该学校尚无工号时才保留已验证教务账号的兼容匹配；未验证凭据、停用账号、已撤销归属或其他学校的号码不能建立关联。重复候选返回 `ambiguous`，不强行选择。改号、撤销归属或停用账号后旧关联立即失效；关联结果只表示身份匹配，不授予资源访问权限。

## 源码审计

当日全平台快照：[lq-platform-component-audit-2026-10-20.json](lq-platform-component-audit-2026-10-20.json)；本次摘要：[teacher-identity-source-audit-2026-10-20.json](teacher-identity-source-audit-2026-10-20.json)。

执行命令：

```powershell
python tools/ui/audit_lq_components.py --output docs/lq-platform-component-audit-2026-10-20.json --summary docs/teacher-identity-source-audit-2026-10-20.json
```

前端冻结后重新扫描 544 个作者源码文件、203 个 HTML 模板、5468 个组件候选入口；4672 项 `pending`，796 项 `unknown`，总体验收状态仍为 `pending`。该计数是源码入口，不是页面控件实例数或通过率；本改动不会将其他页面的未验收记录批量标记通过。后续前端修改需重新生成对应快照并绑定最终来源。

## 验证与发布证据

已直接运行 `python -m unittest tests.test_organization_suggestions -v`，8 项隔离内存 SQLite/API 用例通过，覆盖确定别名、父级隔离、学校标识冲突、自由输入、停用过滤、缓存复用/过期、未提交数据隔离与登录访问要求。测试未连接真实业务数据库。

最终联调执行记录确认以下验证；各组存在交叠，不相加作为独立测试总数：

- 后端 133 项定向回归通过，覆盖身份、资料、管理调用等相关逻辑；课表服务、API 和适配器另有 49 项 SQLite/API 用例通过。
- 课表完整用例在原生隔离 PostgreSQL 上 29 项通过，另有 4 组原生 PostgreSQL 并发身份探针通过。
- 使用正式迁移回放流程，将 2026-09-23 历史已验证备份恢复到新建本机隔离 PostgreSQL 集群，在两个独立恢复数据库中分别执行两轮增量迁移与两轮完整启动迁移：182/182 表，旧字段和序列差异为 0，重复执行差异为 0，失败索引为 0，跳过项为 0。业务测试使用纯合成数据，迁移回放使用上述历史备份恢复库；两者均未连接生产数据库运行测试。正式报告为 `E:/CodexTemp/lanshare-teacher-identity-pg-20261020/native-report.json`，最终来源匹配验证为同目录下的 `native-validation-identity-final.json`，并发探针输出为 `employee-claim-probe.stdout`；恢复输入为 `E:/CodexTemp/lanshare-lq-release-20260923/snapshot.dump`。
- 正式构建 `npm run build` 通过，最终静态资产图为 `467031947da485656b257b438007b324eebde3e4f9c51d4c46fb1a7d07dccceb`，493 个文件。构建日志位于 `.codex-temp/teacher-identity-build-final.log`。
- 隔离合成环境加载正式资产，LQ 全部 families 启用与关闭两种配置分别通过 4 项浏览器场景。场景文件为 `tests/e2e/specs/teacher-identity.spec.ts`，配置为 `tests/e2e/teacher-identity.playwright.config.ts`；验证用户管理工号与别名保存、个人资料与登录、窄屏/暗色布局。后续繁忙/IME 定向补跑是额外第 5 项，不计入前述 4 项。
- 截图已经实审；暗色归属卡白底白字的问题改为共享 LQ Surface，删除域内私有材质。登录兼容分支也使用 LQ Surface/Input。相关本地截图为 `.codex-temp/teacher-identity-browser/desktop-membership.png`、`mobile-membership.png`、`mobile-login.png`。
- `.codex-temp/teacher-identity-browser/results.json` 当前保留最后一次单项定向补跑（1 项通过）：保存中不能用 Esc 或关闭按钮关闭，409 后保留工号和学院草稿，IME 开始/结束保持输入，反复切换字段后仅有 3 个 datalist、请求不超过 5 次。该 JSON 不代表两组完整运行。
- 首次 LQ 关闭配置的 4 项原始报告已保留为 `.codex-temp/teacher-identity-browser/initial-family-off-results.json`；最终 LQ 启用配置的 4 项报告被定向补跑覆盖，成功结果保存在当次工具标准输出中，未伪造独立 JSON 报告。

## 发布闭环

实现提交 `4bf818a348545af63583578a702f736b5266226b` 已推送 `origin/dev`，并通过 `git ls-remote` 确认远端实现提交一致。本记录的补充提交随后随同分支推送。

从该实现提交创建独立发布工作树，保留经过验证的源文件字节和迁移摘要；发布目录未包含原工作区的其他未提交文件。发布目录重新正式构建得到同一静态资产图，Git 跟踪文件和暂存区均无差异。先通过 `deploy_remote.ps1 -DryRun -QuiesceForMigration`，再执行正式部署，最终 `DEPLOY_DONE`：

- 线上版本：`20261020-195714-7b6df87c75f2`。
- 切换前停写 PostgreSQL 备份：`/tmp/lanshare-deploy-backups/db-cutover-20261020-195726.sql.gz`；原代码备份由既有流程同时保存。`/lanshare/data` 全程排除于代码归档，现有数据与草稿保留。
- 线上迁移：182/182 必需表、失败索引 0、跳过步骤 0。
- 最终 8 个服务全部运行；配置了健康检查的主应用、AI、mailer、nginx、PostgreSQL、scheduler 均 healthy。
- 公网及容器发布标识一致；主服务、AI 健康检查成功，未发现新错误日志。健康摘要中的历史失败累计值未被清零，各任务当前状态均为 ok，stale 为 0。
- 只读业务上线核验：29 个改动源码文件逐字节匹配，2 个工号字段、2 个身份索引存在，无同校重复的非空职工号；公开登录页包含职工号入口和学校消歧字段，5 个相关公开资源哈希一致。

部署日志为 `.codex-temp/teacher-identity-deploy.log`，预检日志为 `.codex-temp/teacher-identity-deploy-dryrun.log`，只读上线核验为 `.codex-temp/teacher-identity-postflight.json`。上线核验不创建生产测试账号，也不修改真实教师档案。迁移与功能验收分别使用离线恢复库及隔离合成环境。
