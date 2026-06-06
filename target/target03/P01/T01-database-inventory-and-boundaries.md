# T01 - 数据库清点与边界

## 目标

完整清点项目中所有数据库操作、数据文件位置、运行时目录、后台任务和部署脚本边界，形成 PostgreSQL 改造的事实基础。迁移前必须知道哪些代码读写数据库、哪些数据不在数据库内、哪些目录不能被部署覆盖。

## 必须覆盖的范围

1. 启动入口：`main.py`、`ai_assistant.py`、`classroom_app/app.py`。
2. 统一数据库入口：`classroom_app/database.py`、`classroom_app/db/connection.py`。
3. 路由层、服务层、后台 worker、导入导出工具和部署检查脚本。
4. SQLite 文件：`data/classroom.db`、`data/db/classroom.db` 兼容路径。
5. 文件存储目录：`homework_submissions`、`data/files/submissions`、`shared_files`、`storage`、`chat_logs`、`attendance`、`rosters`。
6. 远程运行时目录：`/lanshare/data` 以及项目根目录下历史兼容挂载。

## 执行步骤

1. 用 `rg` 搜索 `sqlite3`、`get_db_connection`、`execute`、`executemany`、`lastrowid`、`PRAGMA`、`INSERT OR`、`ON CONFLICT`。
2. 对搜索结果按业务域分类：认证、课堂、作业、附件、AI、邮件、Agent、教务、系统管理。
3. 输出表级 inventory，包括表名、行数、索引、外键、主键、热点读写路径。
4. 标记所有 SQLite 专用语义，包括 `PRAGMA`、`lastrowid`、弱类型、`INSERT OR IGNORE`、本地时间函数。
5. 标记所有数据库记录引用的物理文件路径。
6. 明确部署脚本中哪些路径允许同步，哪些路径必须永远保留。

## 验收条件

- [ ] 所有数据库入口都可追踪到统一连接策略或待改造清单。
- [ ] 所有 runtime 数据目录均列明保护策略。
- [ ] SQLite 专用 SQL 已列出并分配到 T02-T05。
- [ ] 附件元数据和文件系统关系已交给 T07 验证。
- [ ] 清点过程没有修改真实数据库和远程数据。

## 风险点

1. 直接把 SQLite 当成普通 SQL 替换，会漏掉 `lastrowid`、`PRAGMA`、`INSERT OR` 等行为差异。
2. 数据库迁移成功但附件目录未同步，会导致作业文件、资料和签名无法打开。
3. 部署脚本若误覆盖 `/lanshare/data`，会造成不可逆线上数据损坏。

## 当前执行记录

已完成初步清点：

- 现有统一入口为 `classroom_app.database.get_db_connection()`，底层转到 `classroom_app/db/connection.py`。
- 直接或间接 SQLite 操作大量存在于 service、router、tools、tests 中，说明不能一次性替换连接库。
- 远程权威数据库已只读复制为快照，未修改线上数据。
- 已新增 inventory、readiness、file integrity、migration、performance 等工具用于持续报告。

当前状态：允许继续后续目标，不允许声明数据库操作已经完全 PostgreSQL 兼容。

