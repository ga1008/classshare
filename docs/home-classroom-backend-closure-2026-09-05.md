# 首页与课堂改版：后端验收补证

日期：2026-09-05。对应原97项矩阵中的D12、D15、D17、D18、D22、C18、R08及A12的数据侧要求。本报告不改写原验收标准或主验收附录，不包含部署声明。

## 1. 实际执行范围

11项HTTP用例使用`.codex-temp/home-classroom-redesign-20260905/backend-closure-runtime`，由本任务既有合成`baseline-runtime`复制，测试前逐项用SQLite backup恢复。没有修改主任务正在操作的current/8132数据；测试通过ASGI TestClient执行完整应用路由、真实表单登录、session cookie与授权查询，未替换用户依赖、权限函数或SQL结果。这组用例不需要网络监听；后续D22真实浏览器另使用独立8138时钟服务，见下文归档。

测试不启动应用lifespan/workers；页面访问的行为记账与异步讨论预热被禁用，防止“读取同一快照”的探针本身产生新学习事件。`httpx.AsyncClient.request`被禁止调用，并在每项结束验证调用数为0。对时间用例注入上海本地时钟到真实生命周期、补交策略、首页投影和time-state端点；未注入浏览器时间。模板仍真实渲染，只旁路记录传入模板的上下文以和最终HTML比较。

最初9个完整路由case与联合回归111项通过；随后补“未发布→发布”和“试炼生成中/失败常显”两项，再补排序工作量回归。**最终11个完整路由case，8模块联合114/114通过、0跳过**，并在精确`generated_at`断言加入后重新通过。113项为排序回归加入前的历史结果。测试实现：[test_home_classroom_backend_closure.py](../tests/test_home_classroom_backend_closure.py)。逐case安全响应摘要与最终日志归档在[backend-closure](../artifacts/home-classroom-redesign-2026-09-05/backend-closure/)；不归档口令、cookie、原始登录输出或失败时的整页正文。

## 2. 每项直接证据

摘要文件名为下表测试方法名加`.json`，包含fixture源SHA、SQLite版本、读取路径/状态/字节、注入时刻、断言通过后的值。HTTP失败的状态存于`verified_values`；`http_reads`只记录成功返回的读取帮助方法，不能仅以该数组计算所有请求数量。

| 原ID | 测试方法 | 直接结果 |
|---|---|---|
| D12 | `test_d12_same_snapshot_course_profile_stage_rank_and_ssr_rounding` | 相同合成快照：课程修为2.3、profile2.3、best_course2.3、排名图本人2.3/第1；当前阶段29%，与课程得分2.3明确不同。真实API、首页上下文、课堂上下文及SSR首层显示相同1位小数。课堂模板原整数取整已由主任务修复 |
| D18 | `test_d18_missing_and_dirty_snapshots_recalculate_real_sources_not_zero` | 删除快照、将快照99.9标dirty两种情况均读取真实材料/任务源重算16.4；数据库回读score16.4/dirty0，未返回占位0或陈旧99.9。此处没有mock计分函数 |
| D18/A12 | `test_d18_a12_failed_snapshot_returns_unavailable_not_zero_and_retry_recovers` | 模拟真实计分源异常：API503且无progress成功对象；课堂上下文progress=null并附明确错误，HTML不出现“修为0”。移除故障后原API恢复16.4。错误一行的前端可见/点击重试验收交由主任务 |
| C18 | `test_c18_mixed_generated_and_teacher_content_keeps_summary_and_full_http_details` | 系统占位+时间/地点+教师正文混合；真实课堂SSR/预加载保留教师正文，摘要只留下教师内容，完整detail_content与原文逐字相同。另在`test_classroom_workspace`补“教师正文上课地点：…”不被关键词删除的回归 |
| D17/R08 | `test_d17_r08_unrevealed_group_feedback_absent_from_two_students_html_and_json` | 两个同班学生真实登录，各自97.431/91.827及不同评语标记；课堂、普通详情、考试详情、workspace、calendar均不输出未揭示评语，详情上下文分数为空。释放本人结果后其评语恢复，另一学生评语仍缺席 |
| D17/R08 | `test_d17_r08_group_visibility_failure_fails_closed_without_feedback` | 小组可见性服务异常时，课堂/普通详情返回503而不输出评语；不再用异常fallback到“非小组”放行 |
| D17/R08 | `test_d17_r08_personal_trials_and_todos_stay_private_in_both_students_ssr_and_json` | 两学生各有私人待办与个人试炼；首页/课堂SSR及预加载、workspace/calendar均无同学标记，课堂投影只含本人试炼ID；同学试炼直链依原HTML权限合同303转`/auth/forbidden`，没有把该重定向误记为JSON403 |
| D15/D22 | `test_d15_d22_home_classroom_detail_and_time_api_share_exact_effective_windows` | 10:04:59正常、10:05:00进入补交、10:10:00补交关闭：首页actionable、课堂accepting、详情真实接受状态及time-state一致；首页M47不变、待处理42→42→41，课堂M9不变。个人重交10:19:59可提交、10:20:00不可提交，三处一致；补交规则/80分封顶数据保留 |
| D22 | `test_d22_future_start_preserves_existing_published_acceptance_rule` | starts_at=11:00、已发布且12:00截止的任务在10:00仍按既有规则接收，首页与实际详情一致；没有在新页面凭未来starts_at另造开放门槛 |
| D22 | `test_d22_unpublished_to_published_is_shared_by_home_classroom_and_detail` | 同一时刻下未发布任务不在首页/课堂学生集合，详情走原权限拒绝页；数据库发布后两集合均出现且详情允许提交 |
| A12 | `test_a12_trial_generation_and_failure_are_visible_without_starting_ai` | 同一个人试炼generating状态SSR常显“试炼生成中”；failed状态常显“试炼未完成·可重试”，保留原条件和详情；整个读取过程中AI请求数0 |

## 3. 本轮实际修复

1. **未揭示结果进入预加载脚本。** 原`assignment_detail_student.html`与`exam_take.html`无条件序列化`submission.feedback_md`，考试还序列化分数；仅隐藏可见面板不能保密。新增[group_assignment_service.py:711](../classroom_app/services/group_assignment_service.py#L711)的`student_visible_submission`，普通作业和考试路由在序列化之前清空未公布score、feedback、原始分和扣分值，保留本人答案、文件、业务状态及重交窗口。小组状态读取失败明确503，课堂同样fail-closed。已公布、非小组结果仍沿原路径展示，不删除数据库结果。
2. **截止整秒存在一秒空档。** 原普通接收要求`due>now`，补交却要求`now>due`；due等于now时两者均关闭。现在补交统一为`due<=now<late_until`，与自动关闭一致；提交时保存的补交快照也采用同一下界。新增纯函数回归验证整秒接收、结束拒绝、固定扣5分及最高80分，避免展示与批改快照分歧。
3. **混合正文按关键词被删。** 新摘要原先对所有后续行过滤“上课时间/地点/教学班”前缀，可能误删教师正文。现仅移除开头连续且可和结构化排课值核对的生成元信息；无法确认来源的文本保留，完整正文不改。
4. **读取失败欠缺明确数据状态。** 学生修为API重算失败返回503和可重试文案，课堂保留`learning_progress_error`并不伪造0；正常missing/dirty仍沿既有真实重算机制。未修改计分、阶段门槛、排名或MP共享首页默认字段。

## 4. 可复现命令与限制

先重新建立任务拥有的`backend-closure-runtime`及其相邻合成`baseline-runtime`，再设置`HOME_CLASSROOM_CLOSURE_RUNTIME`为前者绝对路径。可选`HOME_CLASSROOM_CLOSURE_EVIDENCE`必须位于同一隔离任务目录下，输出每项安全JSON。单独运行此模块，避免其他模块已经按不同环境导入应用配置。未设置专用变量时，这11项明确skip，不会碰默认工作库。

```powershell
venv/Scripts/python.exe -m unittest tests.test_home_classroom_backend_closure tests.test_dashboard_workspace tests.test_dashboard_workspace_api tests.test_classroom_workspace tests.test_group_assignment_service tests.test_classroom_closeout_service tests.test_classroom_retake_service tests.test_learning_progress_snapshots -v
```

原closeout/retake最小schema测试仍打印预期的best-effort缺表诊断，断言通过；最终日志保留这些诊断，不将它们描述为完整schema故障。完整路由case使用完整合成schema，没有用最小表mock权限结果。

本报告后续已补D22真实Chrome六阶段，见[时钟跨界报告](../artifacts/home-classroom-redesign-2026-09-05/backend-clock/report.md)及12张截图：首页/课堂无reload，正常→补交→个人重交关闭→补交关闭的课堂N为2→2→1→0，首页M42/课堂M10恒定，两处完整列表始终保留2个目标；程序化挂起计时器/恢复也2→0通过。浏览器取边界后1秒，精确整秒由HTTP硬断言承担；不冒充真实操作系统休眠。D18错误提示点击重试、A12页面所有异常组合的交互由主任务补证。此轮没有新的真实PostgreSQL执行；此前PG16读取/并发结论不自动覆盖这些新增修复，全新PG历史初始化限制仍在。

P02/P03/P05另见[F02/F04完整页性能报告](../artifacts/home-classroom-redesign-2026-09-05/backend-fullpage/report.md)。初批740次请求原始观察保留，但测试harness过度替换datetime类，导致current canonical时分秒被当日期丢失；因此**已撤回其统一时刻配对及p95比较结论（包括原+12.7%）**，不能据此认定产品退化或通过。修正后只mock明确时钟提供者，对实际generated_at/assignment server_now逐条断言，并在无其他浏览器CPU任务的独占窗口重测。提前与辅助浏览器重叠的片段另存且不计数。

**有效性能证据为首轮320次、首次越线的3格各30/版本独立复验180次，合计500次；SQL/正文另41次。** 每条均通过同一seed SHA、10:00实际时刻、HTTP200与外部请求0断言。F02教师课堂cold/hot初次越线在复验未持续；F04学生首页hot仍为62.484→69.829毫秒，**+7.345毫秒／+11.76%**，超过原P05的10%标准，保留实测B级偏差，不继续抽样或扩大源码优化。该格中位数40.22→49.93毫秒，最小—最大分别35.31—66.09与42.85—71.91毫秒；原始样本和首次偏差全部保留。

P02学生首页F02/F04新增均为7条常数查询、课堂新增1条材料绑定表存在性只读检查；教师6课堂首页SQL88→41。原共享context内的逐课堂todo/cockpit/旧续读，以及F04课堂旧逐任务group判断仍保留，不能称为完整同源计算全部去重。P03已补完整HTML、显式JSON和统一gzip体积、SSR元素、SQL数及DB-API执行/取行耗时；真实hydration DOM、JS执行和退役计时器的前端证据由主任务报告承接。这些边界不会以功能通过替代性能标准。

排序key复用另加1000来源的工作量上界回归，最终本报告八模块联合**114/114、0跳过**。11个完整路由case不变，113为该局部性能修复之前的历史执行结果。该性能证据使用冻结源码和原匹配fixture，不把后续JS/CSS控件小修或合成星期修正说成已经重测过。

11个HTTP用例的时钟注入只替换china_now/_utc_like_now函数，从未替换datetime类，未受上述性能harness问题影响。仍补充每个冻结时刻的workspace响应`generated_at == 注入时刻 +08:00`硬断言，并再次114/114通过；安全JSON已记录10:04:59、10:05:00、10:10:00、10:19:59、10:20:00的真实输出时刻。
