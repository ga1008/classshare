职业领域数据与验证说明
====================

专业职业方向和实际招聘职位分别存储。专业网络可以由受校验的 AI 候选发布，也可以使用基础探索；实际职位必须由受控来源导入。没有来源数据时，学生界面显示真实职位空态，不用职业方向填充职位列表。

专业身份与版本
--------------

现有学籍记录使用专业名称，没有统一的官方 major_code。职业接口的 `major.id` 是学校范围内的内部身份，不能当成教育部专业代码。已知学制注释（如“专升本”）从知识共享键中分离，原始学籍名仍用于学制判断；“商务方向”等专业分支不自动合并。

`career_major_aliases` 只接受明确的学校、别名、规范名称和映射依据。已经生成网络或建立学生会话的旧范围不能被直接合并，须先审查已有版本和反馈。学生不能提交别名修改。

网络候选校验合格后才增加 `career_network_versions` 并切换当前网络。历史恢复会追加一个新版本，保留原记录并使旧任务失效。学生推荐按网络版本、答卷、材料、偏好、反馈、阶段和评分版本重新判断是否有效；未映射的历史收藏保留为待核对记录，不套到同名 tag 上。

受控导入与恢复
--------------

在已配置环境的项目目录执行：

```powershell
venv\Scripts\python.exe -B tools/career_catalog_import.py reviewed-data.json
venv\Scripts\python.exe -B tools/career_catalog_import.py reviewed-data.json --apply
```

第一条在事务中验证后回滚；第二条提交已经核对的数据。输入 JSON 顶层支持 `aliases`、`postings`、`network_restores` 三个数组，每类最多 200 条，整个文件最多 8 MiB。一条记录错误会回滚整个批次。

| 数组 | 必填字段 | 约束 |
| --- | --- | --- |
| aliases | school_code、alias_name、canonical_name、reason | 明确的校方名称对应关系，不猜测官方代码 |
| postings | source、external_id、source_url、title、company、city、job_description、checked_at、expires_at、status | 原链接为 HTTP(S)；核验和到期时间必须含时区；JD 30–15,000 字；status 为 open/closed/expired |
| network_restores | school_code、major_key、revision、reason | revision 必须是存在的有效历史版本；追加恢复版本 |

职位可选字段为 `school_code`、`employment_type`、`published_at`。学校为空表示公共来源，否则仅该学校学生可见。来源方的 `source + external_id` 是幂等身份；JD 内容、条件或有效期变化会增加版本，单纯刷新核验时间不增加 JD 内容版本。系统保存原文哈希、版本快照和核验时间，不自行抓取尚未确认的数据源。

学生读取与个人目标
------------------

`GET /api/career-path/job-postings` 支持 city、keyword、page、page_size（最多20）和 qualification。空 city 使用本人已保存城市偏好；keyword 只匹配岗位和公司名，通配字符按普通字符处理。

资格选项为 all、no_known_gaps、confirmed。判断复用 JD 工作台的能力证据与硬条件服务：缺失资料保持 unknown；求职意向不能当技能；自述达到条件仍需招聘方核验。为了限制多人并发计算，资格过滤在当前有界候选页内执行；`total` 明确是来源、时间与城市筛选后的候选总数，`filtered_on_page` 和 `has_more` 表示本页过滤和下一页状态，不把它说成全部资格满足数。

到期、关闭、缺少来源、尚未核验或跨学校职位不会作为在招记录返回。`POST /api/career-path/job-postings/{id}/target` 再次核对可见性和有效期，然后创建或复用本人私有目标。目标保存完整 `analysis.posting_source`，同时建立学生、职位版本、目标的关联。以后更新公共 JD 不改变已经确认的简历或投递快照。

状态、失效与资源边界
--------------------

职业命令在同步请求线程中的完整事务执行；GET 不初始化表、不更新会话、不入队。`known_result_version` 相同的状态请求只读取学籍、别名、会话与网络元数据和任务状态，省略图、材料快照与推荐结果。

材料写入调用 `invalidate_career_profile`，使个人增强失效并增加 revision；下次显式刷新重新读取材料和基础推荐。证书有效期随快照保存，评分与轻量状态的 input_epoch 包含算法、目录和月份，证书自然到期也会刷新基础结果并标记旧增强待更新。过期画像不能继续请求一份注定无法发布的 AI 增强。网络与个人任务使用共享持久任务账本；独立 `CAREER_JOBS_ENABLED` 关闭或容量不足时，基础探索和问卷继续可用。

材料读取每类最多40条；个人职位匹配的每条文本总量最多2400字、单字段最多600字，缓存最多32份并包含学生及完整输入。公共 JD 提取和职业图校验使用有界缓存。实际岗位列表每页最多20条，资格检查不在后台轮询中运行。

复现入口
--------

```powershell
venv\Scripts\python.exe -B -m unittest tests.test_career_job_postings tests.test_career_lifecycle tests.test_career_path_quiz -q
venv\Scripts\python.exe -B tools/career_state_probe.py
```

测试使用隔离内存数据库和合成来源，不向应用数据库导入职位。覆盖冷启动、纯读、共用专业任务、草稿冲突、迟到结果、资料失效、来源到期、跨学校和跨学生隔离、目标版本、CLI 回滚及网络恢复。

一次本地诊断样本为60个方向、160条材料，包含 JSON 序列化：完整冷状态 P95 67.877 ms，完整热状态 7.470 ms，轻量状态0.121 ms；响应217,134字节降为1,874字节。别名映射加入后通常5次SQL，同时有个人任务时增加一次任务元数据读取。这些是 SQLite 内存的函数测量，不代表 HTTP、PostgreSQL、生产混合负载或真实 AI 的吞吐承诺；以部署后的专门验证为准。
