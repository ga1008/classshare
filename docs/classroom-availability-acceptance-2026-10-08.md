# 空闲教室查询：隔离验收记录（2026-10-08）

## 验收边界

本轮使用全新合成 SQLite 应用库和专用本机 PostgreSQL 数据库。浏览器加载真实路由、权限、SQL、模板与正式资产；仅教务网站的 HTTP 边界使用合成传输。没有对真实业务库运行测试，也没有向真实教务系统提交调课、同步或写入。

正式资产图：`2dcac58147515a2c9021d34109035f3dc675797511c415fa0e06e2ae08ac833d`。管理页模板使用 `asset_url` 引用正式模块；每个浏览器用例校验实际加载的资产图。

## 原生 PostgreSQL

`tests/test_academic_classroom_postgres.py` 在显式授权的本机隔离集群 `127.0.0.1:55448` 中创建随机命名的全新数据库，结束后删除该测试库。

| 验证 | 结果 |
| --- | --- |
| 将 HEAD 旧版过滤函数注入同一测试进程，实际执行 `CHAR(9)` 查询 | 2 项均重现 PostgreSQL `SyntaxError`，证明旧版缺陷可复现 |
| B310、多词、制表符、全角空格搜索，且不越过学校范围 | 通过 |
| 精确空闲查询经过真实 PG 本地教室解析，再到合成教务 HTTP | 通过，周6/周四/2–3节发送位图 `zcd=32,xqj=4,jcd=6` |
| 同校、学期、教室、周、星期、节次组合重复记录 busy→free | 通过，仅保留1行，更新状态和说明 |

最终 3/3 通过。负例日志：`.codex-temp/classroom-native-business-old-negative.log`；最终正例：`.codex-temp/classroom-native-business-final.log`。

数据库 schema 没有变化。现有完整原生迁移演练报告通过当前源哈希校验（72 文件）；验证结果 `.codex-temp/classroom-availability-native-gate.json` 为 `status=ok`、`blockers=[]`。完整报告与备份留在先前专用目录，由发布流程复核使用。

## 真实应用浏览器

可重复夹具：`tests/e2e/scripts/prepare_classroom_availability_runtime.py`，依赖已有 `prepare_ui_v3_runtime.py` 创建基础合成环境。启动器：`tests/e2e/scripts/serve_classroom_availability.py`。测试：`tests/e2e/specs/classroom-availability.spec.ts`。

隔离目录：`.codex-temp/classroom-availability-20261008-runtime`。服务仅监听本机8360，禁止外部 socket/DNS，禁止 PostgreSQL 和 dotenv 加载。教务传输记录仅含合成请求参数，不含真实凭据。

桌面1440×980和触屏390×844，共 **8/8 通过，2.6分钟**：

| 业务路径 | 已验证行为 |
| --- | --- |
| 教室查询页精确条件 | 2026–2027第一学期、五合、B310、第6周周四第2/3节，UI与上游位图一致 |
| 查询失败与恢复 | 登录失效、上游503、非JSON响应显示明确错误；空结果显示空态；可再次查询 |
| 参数变化与异步响应 | 慢请求中修改条件，旧响应不回写；重试使用新条件 |
| 管理页分页 | 125条合成候选按100/25分页，第2页包含目标B310 |
| 3D编辑器查询与选择 | 相同学期和时段参数，每页40；载入第2页累计80条并选择第43位B312，保留周/星期/节次 |
| 原教室独立判断 | B310位于第125位，首40条不含它时仍经独立精确查询判定free；定向查询失败则unknown且候选保留 |
| 不完整身份 | 空ID、未知范围教室保持unknown，不误报free/busy |
| 权限 | 学生两个API均403；未绑定教务账号的教师得到明确错误 |

结果 JSON：`.codex-temp/classroom-availability-20261008-final2/results.json`。各用例附合成上游请求记录。最终主路径无浏览器运行时错误。

代表截图位于上述目录 `test-results` 下：

- `classroom-availability-cla-903fc-te-response-and-second-page-desktop/exact-query-success.png`
- `classroom-availability-cla-903fc-te-response-and-second-page-touch/exact-query-success.png`
- `classroom-availability-cla-903fc-te-response-and-second-page-desktop/query-second-page.png`
- `classroom-availability-3D--94640-s-and-rejects-stale-results-desktop/editor-room-selection.png`
- `classroom-availability-3D--94640-s-and-rejects-stale-results-touch/editor-room-selection.png`

已人工查看桌面查询/编辑器及触屏编辑器内容。截图为整页，固定顶栏位置取决于截图时滚动位置，不代表顶栏在文档流中的位置。

本记录证明隔离功能验收通过；Git提交、发布与线上核验由主发布流程单独记录。
