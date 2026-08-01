# UI 文案精简候选清单

> 此文件由 `python tools/ui/audit_ui_copy.py --output docs/ui-copy-audit-candidates.md` 生成。候选项必须经过人工判断；错误、风险、权限与不可逆操作提示不得仅因文字较长而删除。

扫描文件：369；候选项：273。

| 优先级 | 来源 | 类型 | 当前文案 | 命中信号 |
| --- | --- | --- | --- | --- |
| P1 | `static/js/manage_offerings.js:243` | `empty` | 还没有选中课程 选择课程后，可在这里快速确认课程模板是否足够完整，避免开课后再返工调整。 | long, multi-clause, framing |
| P1 | `templates/partials/dashboard_agenda_widget.html:130` | `empty` | 这会儿没有临近的安排 监考、考试、作业截止和你新建的待办都会出现在这里，最紧要的排在最前面。 | long, multi-clause, framing |
| P1 | `frontend/src/lib/assignment-submit.ts:108` | `description` | 输入答案或添加附件后再提交，系统会同步记录文本和文件。 | long, multi-clause, framing, secondary-copy |
| P1 | `static/js/collaboration.js:426` | `empty` | 选择一个小组 小组的成员、文件、成果和互评会在这里展开。 | long, multi-clause, framing |
| P1 | `static/js/manage_course_schedule.js:37` | `description` | 滚轮或方向键切换周次，点击最前面的周卡片放大查看整周课表；放大后点击课程块可进入对应课堂。 | long, multi-clause, secondary-copy |
| P1 | `static/js/manage_lesson_plans.js:925` | `hint` | 教案默认私有；可设为本系部 / 本院级 / 全校公开，公开后其他老师可在自己的教案库看到并一键继承。 | long, multi-clause, secondary-copy |
| P1 | `static/js/materials_manage.js:4698` | `subtitle` | 整理为排版规整的 Markdown 文档。改动限制决定允许对原文改动多少，默认“一般”。 | long, multi-clause, secondary-copy |
| P1 | `templates/assessment_plan_editor.html:40` | `hint` | 命题教师可绑定本人签名；系（教研室）主任审核签字通常线下手写，也可从签名库选择（需使用授权）。 | long, multi-clause, secondary-copy |
| P1 | `templates/classroom_main_v4.html:1939` | `intro` | 第 5 步 · 成绩策略 设置最低平时分保护 只有真实出勤率达到 70% 的学生才参与配平。系统绝不修改出勤，只以可复现的随机均衡方式上调三次作业和一次测评，并在 Excel 隐藏审计页保留原始分与调整依据。 | long, multi-clause, secondary-copy |
| P1 | `templates/manage/classes.html:288` | `note` | 缺邮箱不会阻止导入，但系统会在列表中标出，方便后续补齐重要通知触达。 | long, multi-clause, framing |
| P1 | `templates/manage/life_tips.html:12` | `intro` | 登录一言提示库 学生/教师登录加载屏上的一句话提示：通用池全平台共享，本校/本系池由公文 AI 挖掘与手工录入。下架劣句、查看"有用/无感"反馈，让句库越用越准。 | long, multi-clause, secondary-copy |
| P1 | `templates/manage/materials.html:461` | `intro` | 第 5 步 · 成绩策略 设置最低平时分保护 只有真实出勤率达到 70% 的学生才参与配平。出勤分保持真实，系统只均衡上调所选作业和测评。 | long, multi-clause, secondary-copy |
| P1 | `templates/manage/materials.html:484` | `intro` | 第 6 步 · 重修/免修学生（可选） 为重修插班生直接设定平时分 被选中的学生不参与作业、测评与考勤计分：系统按公式把设定的总平时分精确分配到出勤、三次作业和测评，最终计算分恰好等于设定值，并写入 Excel 隐藏审计页。 | long, multi-clause, secondary-copy |
| P1 | `templates/manage/polls.html:12` | `intro` | 投票活动 创建跨班级共享的投票活动：一个投票可分配到多个班级，所有班级成员共享同一份投票数据。草稿与已结束的投票仅你自己可见。 | long, multi-clause, secondary-copy |
| P1 | `templates/manage/student_detail.html:160` | `empty` | 暂未形成可展示的学习支持摘要 系统会继续根据课堂行为、学习进度和教师补充说明积累更稳妥的支持参考。 | long, multi-clause, framing |
| P1 | `templates/manage/system/super_admin.html:174` | `hint` | 设置超管教师后，所有 Bug 修复反馈、新功能反馈和举报将只通知超管教师。建议选择对系统功能最熟悉的教师担任。 | long, multi-clause, secondary-copy |
| P1 | `templates/partials/session_material_ai_modal.html:110` | `summary` | 自动参考范围 系统会优先收集最近几篇已绑定文档，而不是把整个学期的历史材料一次性全部送给 AI。 | long, multi-clause, framing |
| P1 | `templates/resume/job_targets.html:36` | `empty` | 02 分析结果会显示在这里 你会看到核心要求、已有证据、真实缺口和每段经历的改进动作。 | long, multi-clause, framing |
| P2 | `frontend/src/islands/assignment-task-board-sync.tsx:139` | `description` | 回看已经提交、批改中或已评分的任务，便于确认结果和反馈。 | long, multi-clause, secondary-copy |
| P2 | `static/js/blog.js:3413` | `hint` | 支持 Markdown 多行评论、图片、自定义表情；@管家 后会由 AI 结合上下文回复。 | long, multi-clause, secondary-copy |
| P2 | `static/js/classroom_materials.js:496` | `summary` | …" class="btn btn-outline btn-sm" target="_blank" rel="noopener">渲染预览 ` : ''} …" data-process-export-label="…">… ` : ''} …" data-process-export-label="PDF">导出 PDF ` : ''} | long, multi-clause |
| P2 | `static/js/classroom_materials.js:1317` | `placeholder` | 例如：评分时突出脚本可执行性、截图编号一致性和例外情况；每个任务写清楚可给一半分的情形。 | long, multi-clause |
| P2 | `static/js/classroom_materials.js:1319` | `placeholder` | 例如：按机试方式拆分 Linux 服务部署、数据库授权、脚本备份等考核技能，分值合计100。 | long, multi-clause |
| P2 | `static/js/classroom_materials.js:1321` | `placeholder` | 可选：例如补充本次归档说明、课程组统一口径或需要教师后续核对的事项。成绩保护策略请使用上方开关和最低分设置。 | long, multi-clause |
| P2 | `static/js/classroom_materials.js:1325` | `placeholder` | 例如：根据本课堂最新考核计划表，围绕 Linux 服务部署、数据库授权、脚本备份设计机试任务，写清截图编号、提交物和考试时长。 | long, multi-clause |
| P2 | `static/js/classroom_page.js:1888` | `subtitle` | 首页用于课程目录、简介和后续学习文档导航，会显示在时间轴第一课之前。 | long, multi-clause, secondary-copy |
| P2 | `static/js/classroom_page.js:1919` | `subtitle` | 为当前时间轴节点绑定一个 Markdown 文档，课堂内“学习文档”按钮会直接跳转到该页面。 | long, multi-clause, secondary-copy |
| P2 | `static/js/classroom_page.js:3024` | `subtitle` | 首页用于课程目录、简介与后续导航，显示在时间轴第一课之前；可添加多份材料。 | long, multi-clause, secondary-copy |
| P2 | `static/js/classroom_page.js:3044` | `subtitle` | 为本次课添加学习材料，可同时绑定多份；课堂卡片的材料入口会列出全部材料。 | long, multi-clause, secondary-copy |
| P2 | `static/js/classroom_retake.js:31` | `empty` | 尚无重修/插班生记录。点击"AI 识别插班生"按学号前缀扫描候选，确认后系统自动处理平时分与期末材料。 | long, multi-clause |
| P2 | `static/js/dashboard.js:608` | `description` | 本学期课表 · 滚轮或方向键切换周次，点击卡片放大；放大后点击课程进入课堂。 | long, multi-clause, secondary-copy |
| P2 | `static/js/manage_academic_integrations.js:342` | `empty` | 尚未保存教务系统账号。填写并通过校验后，可同步课程、班级学生、校历、监考和教学场地。 | long, multi-clause |
| P2 | `static/js/manage_assessment_plans.js:512` | `note` | 重新上传模式 本次会新建一条解析任务，不会覆盖「…」；新任务成功后，可返回列表删除旧失败记录。 | long, multi-clause |
| P2 | `static/js/manage_assessment_plans.js:613` | `hint` | 默认私有；可设为本系部 / 本院级 / 全校公开，公开后其他老师可一键继承。 | long, multi-clause, secondary-copy |
| P2 | `static/js/manage_lesson_plans.js:824` | `note` | 重新上传模式 本次会新建一条解析任务，不会覆盖「…」；新任务成功后，可返回列表删除旧失败记录。 | long, multi-clause |
| P2 | `static/js/manage_teacher_evaluations.js:376` | `hint` | 创建后进入编辑器，用完整表单填写基础信息、为 10 项指标打分并撰写学习情况分析。 | long, multi-clause, secondary-copy |
| P2 | `static/js/manage_teacher_evaluations.js:498` | `note` | 重新上传模式 本次会新建一条解析任务，不会覆盖「…」；新任务成功后，可返回列表删除旧失败记录。 | long, multi-clause |
| P2 | `static/js/manage_teacher_evaluations.js:599` | `hint` | 默认私有；可设为本系部 / 本院级 / 全校公开，公开后其他老师可一键继承。 | long, multi-clause, secondary-copy |
| P2 | `static/js/materials_manage.js:1616` | `empty` | 正在核对来源 优先读取 30 分钟缓存；仅在缓存过期时访问教务系统，随后逐人比对两份成绩材料。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:2822` | `summary` | … …" class="btn btn-outline btn-sm" target="_blank" rel="noopener">渲染预览 ` : ''} …" data-process-export-label="…">… ` : ''} …" data-process-export-label="PDF">导出 PDF ` : ''} | long, multi-clause |
| P2 | `static/js/materials_manage.js:4320` | `placeholder` | 例如：优先关联考核计划表，再围绕课程核心能力生成期末机试试卷，包含任务、截图编号、提交要求和考试时长，分值严格继承计划表。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:4703` | `subtitle` | 按材料意思深度重写并优化排版；可选择目标课堂，让内容贴合课程、班级与专业目标。 | long, multi-clause, secondary-copy |
| P2 | `static/js/teacher_evaluation_editor.js:405` | `placeholder` | 例如：写得更详细一些，分 3 点，每点结合课堂表现、作业考试和后续改革建议，总字数约 600 字。 | long, multi-clause |
| P2 | `templates/assignment_wrong_summary.html:1802` | `summary` | 难题归集 根据逐题得分、未满分率与平均得分率排序；主观题错误原因由快速 AI 根据未满分答案归集。 | long, multi-clause |
| P2 | `templates/classroom_main_v4.html:664` | `empty` | 尚未配置课程首页教师尚未配置课程首页 尚未绑定课堂文档教师尚未配置学习文档 可绑定课程首页 Markdown，用于目录、简介和后续文档导航。当前课堂还没有可打开的课程首页。 可为本次课绑定一份 Markdown 材料，师生可从这里直接进入文档页面。当前节点还没有可打开的课堂文档。 | long, multi-clause |
| P2 | `templates/classroom_main_v4.html:1958` | `summary` | 出勤率达到 70% 的学生，若公式平时分低于 60 分，系统只上调所选作业和测评；出勤率保持真实。 | long, multi-clause |
| P2 | `templates/classroom_main_v4.html:1979` | `placeholder` | 例如：围绕本课堂的 Linux 服务部署、数据库授权、脚本备份设计机试任务，难度适中，适合专升本班级。 | long, multi-clause |
| P2 | `templates/classroom_main_v4.html:2381` | `copy` | 拖拽文件到此处，或直接点击选择文件 适合分享软件安装包、实验资料、课堂课件与示例源码。 | long, multi-clause |
| P2 | `templates/exam_editor.html:1189` | `placeholder` | 例如：按标准答案、关键步骤和附件证据综合评分，重点看核心概念、过程完整度和结论准确性。 | long, multi-clause |
| P2 | `templates/exam_editor.html:1288` | `placeholder` | 例如：计算机网络基础、TCP/IP协议栈、HTTP协议等。上传参考文件时可填写补充要求。 | long, multi-clause |
| P2 | `templates/manage/academic_final_materials.html:36` | `copy` | 期末材料 · 教务系统闭环 选择一个已有课堂，系统只登录一次教务系统并成对下载“成绩登记表”和“试卷分析表”， 完成分数复算、跨表校验、结构化入库后，再进入签名与定稿。 ↻ 同步教务双表 查看 | long, multi-clause |
| P2 | `templates/manage/academic_final_materials.html:182` | `placeholder` | 例如：控制在200字；语气更像任课教师；重点分析综合题失分原因，并给出3项可执行改进。 | long, multi-clause |
| P2 | `templates/manage/classes.html:356` | `note` | 新增学生默认为在读状态，会被纳入课堂任务、统计和通知范围；休学可在学生名单中单独设置。 | long, multi-clause |
| P2 | `templates/manage/courses.html:1316` | `empty` | 还没有创建课程模板 先新增课程，再补充课堂设置，后续开设课堂时就能直接生成排课时间轴。 | long, multi-clause |
| P2 | `templates/manage/materials.html:917` | `copy` | 检测到本次更新包含新的或已更新的 README.md。确认后会把完整目录结构、所有 README 前 10 行和课堂课次信息交给 AI 识别，再自动绑定到课程首页或对应“第几次课”的学习文档按钮。 | long, multi-clause |
| P2 | `templates/manage/student_detail.html:127` | `empty` | 暂无可归档的修为轨迹 当该学生产生修为增减、破境试炼、预警或教师共享说明后，这里会自动形成证据链。 | long, multi-clause |
| P2 | `templates/manage/student_detail.html:183` | `placeholder` | 例如：长期请病假、家庭照护压力、无障碍支持需要、情绪状态、课堂外困难、需要避开的刺激点或更适合的沟通方式。 | long, multi-clause |
| P2 | `templates/manage/student_detail.html:224` | `empty` | 暂无学生主动开放的成长档案 学生可以在个人中心把作业、证书、博客和复盘整理入档，并自主决定是否对教师可见。 | long, multi-clause |
| P2 | `templates/manage/system/blog_crawler.html:403` | `placeholder` | 名称 \| https://example.com/rss.xml \| fixed_rss \| match 课程搜索 \| https://example.com/rss?q=' }} \| keyword_rss \| all | long, multi-clause |
| P2 | `templates/manage/system/blog_crawler.html:435` | `placeholder` | [{"name":"官方源","url":"https://example.com/rss","kind":"fixed_rss"}] | long, multi-clause |
| P2 | `templates/manage/textbooks.html:306` | `intro` | 简介与目录 粘贴教材简介、目录等内容，AI 将自动整理格式。支持各种格式的粘贴内容。 | long, multi-clause, secondary-copy |
| P2 | `templates/resume/home.html:5` | `copy` | 第一版先出来，再逐步补强 用最省事的方式，得到一份能预览的简历 已有简历就直接导入；平台里已有资料就自动组合。你不需要先填完所有栏目。 导入已有简历 用已有资料生成 分析岗位描述 | long, multi-clause |
| P2 | `templates/student_login_v4.html:223` | `note` | 仅限以下场景使用： 1. 首次登录，尚未设置密码。 2. 教师已通过您的“忘记密码”申请，允许重新使用姓名和学号登录。 | long, multi-clause |
| P2 | `templates/teacher_evaluation_editor.html:44` | `placeholder` | 评价这个班级本学期在这门课程上的各项学习表现，纯文本、可分 1、2、3 点，不要使用 Markdown。 | long, multi-clause |
| P2 | `templates/wrong_book.html:185` | `empty` | 太棒了，目前没有错题！ 完成并批改考试后，做错的题会自动收进这里，方便你考前复盘。 回到首页 | long, multi-clause |
| P2 | `frontend/src/islands/assignment-task-board-sync.tsx:60` | `description` | 课堂内由教师创建并分配的作业与考试，个人试炼不混入这里。 | long, multi-clause, secondary-copy |
| P2 | `frontend/src/islands/assignment-task-board-sync.tsx:70` | `description` | 定位到有待批改提交的任务，进入详情后继续原有批改流程。 | long, multi-clause, secondary-copy |
| P2 | `frontend/src/islands/assignment-task-board-sync.tsx:80` | `description` | 定位到已退回或处于补交流程的任务，便于回看学生后续提交。 | long, multi-clause, secondary-copy |
| P2 | `frontend/src/islands/assignment-task-board-sync.tsx:119` | `description` | 定位老师退回后需要再次完善的任务，进入后按原提交流程处理。 | long, multi-clause, secondary-copy |
| P2 | `frontend/src/lib/assignment-submit.ts:100` | `description` | 服务器时间已不再允许提交，页面会继续保留当前内容供查看。 | long, multi-clause, secondary-copy |
| P2 | `static/js/blog.js:3399` | `placeholder` | 写下你的观点、代码片段或补充说明。输入 @管家 可以邀请 AI 管家参与讨论... | long, multi-clause |
| P2 | `static/js/blog.js:3499` | `empty` | 信号源保持在线，可调整筛选条件、拖动频道带，或发布一篇内容点亮这里。 | long, multi-clause |
| P2 | `static/js/classroom_materials.js:545` | `placeholder` | 例如：补齐审核人、考试时间，细化评分细则，保持总分100分。 | long, multi-clause |
| P2 | `static/js/classroom_materials.js:1323` | `placeholder` | 例如：按考试大题生成“一、二、三”列，迟交和小组互评扣分要整数分摊并核验总分。 | long, multi-clause |
| P2 | `static/js/group_assignment_config.js:106` | `hint` | 新建后学生可在课堂互动区随机加入小组；也可由你在大屏拖拽分配。 | long, multi-clause, secondary-copy |
| P2 | `static/js/manage_assessment_plans.js:383` | `hint` | 创建后进入编辑器，用完整表单填写基础信息和考核项目（分值合计须为 100）。 | long, multi-clause, secondary-copy |
| P2 | `static/js/manage_assessment_plans.js:538` | `placeholder` | 如：这是《服务器配置与管理》机试考核计划表，请忠实还原考核项与分值。 | long, multi-clause |
| P2 | `static/js/manage_courses.js:829` | `subtitle` | 浏览课程材料库中的文件夹结构，并为当前课堂节点绑定一个 Markdown 文档。 | long, multi-clause, secondary-copy |
| P2 | `static/js/manage_gongwen_integrations.js:211` | `empty` | 尚未保存统一认证账号。填写并通过校验后，可同步公文与附件。 | long, multi-clause |
| P2 | `static/js/manage_lesson_plans.js:352` | `hint` | 创建后进入编辑器逐项填写；也可改用「按课堂生成」让 AI 自动生成整学期内容。 | long, multi-clause, secondary-copy |
| P2 | `static/js/manage_lesson_plans.js:850` | `placeholder` | 如：这是 Linux 课程教案，请重点保留每节课的 PBL 表格与作业分层。 | long, multi-clause |
| P2 | `static/js/manage_smart_classroom_integrations.js:324` | `empty` | 尚未保存智慧课堂账号。填写并通过校验后，可同步点名记录和学生签到状态。 | long, multi-clause |
| P2 | `static/js/manage_teacher_evaluations.js:524` | `placeholder` | 如：这是《服务器配置与管理》软工231班的教师评学表，请忠实还原各项得分与评语。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:905` | `empty` | 没有匹配的… 清空关键词重试，或在课堂任务卡片、教师试卷管理页调整“平时成绩用途”。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:1221` | `empty` | 正在读取考试 同时核对试卷大题、满分、课堂名单和评分覆盖情况。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:1618` | `empty` | 来源状态尚未判定 …。系统不会把“核对失败”误报成“材料不存在”，请稍后重试。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:1856` | `empty` | 没有匹配的课堂 可以更换学期，或用课程名、班级名的部分文字重新搜索。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:4316` | `placeholder` | 例如：根据关联试卷逐题生成评分细则，写清每题给分点、扣分项、例外情况和截图要求。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:4318` | `placeholder` | 例如：按机试/项目实操拆分考核技能与分值，补齐课程、班级、命题教师等字段。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:4322` | `placeholder` | 例如：根据这些作业题目生成一份期末复习提纲，包含知识点、易错点和课堂练习安排。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:4693` | `subtitle` | 写下希望调整的方向；留空则基于原材料重新组织并生成新材料。 | long, multi-clause, secondary-copy |
| P2 | `static/js/prompt_pool.js:108` | `title` | 取消勾选后，本次输入只用于当前生成，不进入共享提示词池。 | long, multi-clause |
| P2 | `static/js/resume_section.js:332` | `placeholder` | 输入你的自我介绍，可使用空行分段、- 列表、**加粗**… | long, multi-clause |
| P2 | `static/js/teacher_onboarding.js:464` | `empty` | 还没有教材。可以先录入教材名称，后续再补充附件和目录。 | long, multi-clause |
| P2 | `static/js/teacher_onboarding.js:594` | `empty` | 还没有教学材料。可以先跳过，也可以马上导入文件或整个文件夹。 | long, multi-clause |
| P2 | `static/js/teacher_onboarding.js:697` | `empty` | 还没有可选班级。可以先创建一个空班级，稍后再导入学生名单。 | long, multi-clause |
| P2 | `static/js/teacher_onboarding.js:1293` | `hint` | 后面也可以使用深度思考 AI 协助生成课程材料、整理目录或把资料优化成课堂学习文档。 | long, multi-clause, secondary-copy |
| P2 | `templates/assignment_detail_student.html:664` | `summary` | 补交提交，系统已按教师规则扣分。 原始分：；补交扣分： | long, multi-clause |
| P2 | `templates/assignment_wrong_summary.html:1669` | `summary` | 错误人数归集 只统计已提交答卷；未提交学生显示在概览中，不混入单题错答人数。 | long, multi-clause |
| P2 | `templates/blog.html:344` | `hint` | 默认使用真实名字发布；使用真实名字或昵称时会自动带上班级标签和最高宗门修为。 | long, multi-clause, secondary-copy |
| P2 | `templates/classroom_main_v4.html:2035` | `placeholder` | 例如：安装前注意事项、适用系统版本、课堂使用建议、常见报错说明等。 | long, multi-clause |
| P2 | `templates/exam_editor.html:1335` | `placeholder` | 例如：实验题至少上传 1 张运行截图，可附代码文件；普通问答题不需要附件。 | long, multi-clause |
| P2 | `templates/exam_editor.html:2170` | `placeholder` | 在此输入题目内容，可使用 Markdown，例如 **重点**、列表、行内命令或代码块 | long, multi-clause |
| P2 | `templates/exam_editor.html:2210` | `placeholder` | 本题允许类型，如 .png, .jpg, .py；留空则沿用作业设置 | long, multi-clause |
| P2 | `templates/manage/classes.html:29` | `lead` | 集中维护班级、系别与学生名单，开课、查看学生与通知可达性都从这里进入。 | long, multi-clause |
| P2 | `templates/manage/classrooms.html:554` | `lead` | 教学场地来自教务系统同步，空闲教室按学期、周次、星期、节次实时查询。 | long, multi-clause |
| P2 | `templates/manage/courses.html:1190` | `summary` | 当前共归集 门课程模板。 建议优先完善“待完善”的课程，以便后续直接生成开课时间轴。 | long, multi-clause |
| P2 | `templates/manage/exams.html:1501` | `lead` | 按状态、题型、来源与标签整理试卷，归集同类试卷成清晰工作区。 | long, multi-clause |
| P2 | `templates/manage/exams.html:1912` | `placeholder` | 可补充课程性质、考核形式、评分侧重点、特殊扣分规则；留空则严格依据所选试卷反推。 | long, multi-clause |
| P2 | `templates/manage/exams.html:2012` | `hint` | 按 Enter 添加，每个标签不超过 10 个字，最多 20 个标签 | long, multi-clause, secondary-copy |
| P2 | `templates/manage/exams.html:2144` | `emptyText` | 暂无可反推的试卷，请先补齐试卷每题分值、标准答案、评分指导和扣分点。 | long, multi-clause |
| P2 | `templates/manage/materials.html:507` | `placeholder` | 可选：填写本次归档说明、课程组统一口径或需要教师后续核对的事项。 | long, multi-clause |
| P2 | `templates/manage/materials.html:613` | `placeholder` | 例如：根据这些作业题目生成一份期末复习提纲，包含知识点、易错点和课堂练习安排。 | long, multi-clause |
| P2 | `templates/manage/materials.html:691` | `placeholder` | 例如：更适合软件工程专业学生；增加表格；减少铺垫，突出课堂可执行步骤。 | long, multi-clause |
| P2 | `templates/manage/materials.html:1137` | `placeholder` | 例如：根据前三次课的材料和目录文档，生成第四次课的课程材料。留空则由 AI 自动判断。 | long, multi-clause |
| P2 | `templates/manage/offerings.html:741` | `empty` | 还没有开设任何课堂 先在上方完成课程与排课配置，系统就会自动生成对应的课堂时间轴和任务列表。 | long, multi-clause |
| P2 | `templates/manage/system/agent_keys.html:413` | `note` | DeepSeek 官方按 Key 用量明细需要在平台 Usage 页导出；此处展示的是任务中心运行时聚合用量。 | long, multi-clause |
| P2 | `templates/partials/feedback_modal.html:102` | `placeholder` | 请详细描述：您进行了什么操作？出现了什么异常？期望的结果是什么？ | long, multi-clause |
| P2 | `templates/partials/learning_material_selector.html:44` | `note` | 仅支持绑定 Markdown 文档。单击文件选中，双击文件夹继续进入。 | long, multi-clause |
| P2 | `templates/partials/session_material_ai_modal.html:52` | `placeholder` | 补充本次文档需要强调的教学目标、重点难点、输出结构、板书节奏、练习安排等。 | long, multi-clause |
| P2 | `templates/partials/session_material_ai_modal.html:106` | `placeholder` | 可补充本次课想强调的教学重点、课堂活动或文档风格；不填则按课程上下文自动生成。 | long, multi-clause |
| P2 | `templates/profile.html:353` | `empty` | 暂无新的候选成果 完成批改、获得证书或发布学习文章后，这里会自动出现。 先整理错题复盘 | long, multi-clause |
| P2 | `templates/profile.html:413` | `placeholder` | 例如：完成了实验截图、解释了关键步骤、修正了老师指出的问题。 | long, multi-clause |
| P2 | `templates/resume/job_targets.html:25` | `placeholder` | 请粘贴招聘信息中的岗位职责、任职要求、优先条件等。信息越完整，分析越有用。 | long, multi-clause |
| P2 | `static/js/ai_workspace_widget.js:1324` | `empty` | 选择一个任务查看状态。自己的任务会显示详情和执行记录。 | long, multi-clause |
| P2 | `static/js/app_exams.js:602` | `empty` | 试卷库为空 请先前往管理中心创建试卷，然后再发布到当前课堂。 前往试卷库 | long, multi-clause |
| P2 | `static/js/blog.js:1388` | `description` | 小说、随笔、校园故事、阅读札记与成长片段。 | dense, multi-clause, secondary-copy |
| P2 | `static/js/career_path_app.js:605` | `hint` | 💡 点击节点：高亮成长路径并展开定制详情 · 点击空白复位 | long, secondary-copy |
| P2 | `static/js/career_path_app.js:703` | `empty` | 该方向暂无细分知识栈，可参考右侧节奏建议先打好通用基础。 | long, multi-clause |
| P2 | `static/js/classroom_material_list.js:120` | `empty` | 这里还没有绑定材料。可在下方“添加材料”绑定 Markdown 或 HTML。 | long, multi-clause |
| P2 | `static/js/classroom_materials.js:767` | `empty` | 没有匹配的… 可以清空关键词重试，或在课堂任务卡片中调整“平时成绩用途”。 | long, multi-clause |
| P2 | `static/js/classroom_polls.js:324` | `note` | 该投票范围为当前班级。跨班级投票请到「管理中心 · 内容资产 · 投票」创建。 | long, multi-clause |
| P2 | `static/js/collaboration.js:333` | `placeholder` | 写一句具体反馈：对方做得好的地方、可以继续改进的地方 | long, multi-clause |
| P2 | `static/js/feedback.js:550` | `empty` | 尚未提交反馈 提交 Bug 反馈、新功能建议或举报内容，帮助平台变得更好 | long, multi-clause |
| P2 | `static/js/manage_academic_integrations.js:210` | `empty` | 暂无可同步功能。请先在账号管理中保存并验证教务账号。 | long, multi-clause |
| P2 | `static/js/manage_gongwen.js:183` | `empty` | 没有匹配的公文 调整筛选条件，或点击「立即同步」从统一认证账号拉取收件箱公文。 | long, multi-clause |
| P2 | `static/js/manage_gongwen_integrations.js:137` | `empty` | 暂无可同步功能。请先在账号管理中保存并验证统一认证账号。 | long, multi-clause |
| P2 | `static/js/manage_offerings.js:270` | `empty` | 该课程还没有课堂设置 请先回到课程管理页补充课堂模板，否则无法生成课堂时间轴。 | long, multi-clause |
| P2 | `static/js/manage_offerings.js:390` | `empty` | 当前没有可映射的课堂内容 可能是课程模板还未补齐，或排课日期超出了学期范围。 | long, multi-clause |
| P2 | `static/js/manage_smart_classroom_integrations.js:192` | `empty` | 暂无可同步功能。请先在账号管理中保存并验证智慧课堂账号。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:1225` | `empty` | 还没有绑定试卷的考试 请先在课堂创建考试并绑定试卷；完成评分后再生成正式登分表。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:1527` | `empty` | 暂无已解析材料 请先导入或生成该类成绩材料，再返回此处选择。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:1847` | `empty` | 暂无可用课堂，请先在“开设课堂”中创建或同步教学班级。 | long, multi-clause |
| P2 | `static/js/materials_manage.js:2328` | `title` | 按原来选好的作业/考试重新获取最新成绩，原地更新本材料，不新建也不需要删除旧材料 | long, multi-clause |
| P2 | `static/js/profile.js:586` | `empty` | 尚未配置邮箱 保存一个 SMTP 配置后，重要通知会进入邮件队列。 | long, multi-clause |
| P2 | `static/js/resume_job_targets.js:78` | `empty` | 还没有岗位分析。第一次建议从你最近真正想投的岗位开始。 | long, multi-clause |
| P2 | `templates/assignment_wrong_summary.html:1787` | `summary` | 暂未发现错答 当前已识别逐题得分的提交均为满分，或提交尚未生成可识别的逐题得分。 | long, multi-clause |
| P2 | `templates/classroom_main_v4.html:1460` | `note` | 此处仅显示教师创建并分配的练习与考试；学生个人试炼已汇总到班级成长统计。 | long, multi-clause |
| P2 | `templates/dashboard.html:515` | `empty` | 0 %} hidden> 没有匹配的课堂 可以清空搜索词，或切换筛选查看其他课堂。 | long, multi-clause |
| P2 | `templates/manage/ai.html:109` | `placeholder` | 选择课堂后自动加载已有配置，也可点击重新生成默认草稿自动填充。 | long, multi-clause |
| P2 | `templates/manage/classes.html:241` | `empty` | 还没有班级 先导入一个班级，后续开设课堂时才能直接绑定到教学流程中。 | long, multi-clause |
| P2 | `templates/manage/classrooms.html:757` | `empty` | 等待查询条件 选择周次、星期和节次后即可查看可用场地。 | long, multi-clause |
| P2 | `templates/manage/courses.html:1116` | `lead` | 沉淀可复用的课程模板，开课时直接继承并生成排课时间轴。 | long, multi-clause |
| P2 | `templates/manage/semesters.html:68` | `empty` | 没有匹配的学期 可以换个关键词，或者直接新增一个新的学期。 | long, multi-clause |
| P2 | `templates/manage/system/ai_usage.html:466` | `empty` | 尚无供应商 Token 日志。新调用完成后会自动汇总到这里。 | long, multi-clause |
| P2 | `templates/manage/textbooks.html:207` | `empty` | 没有匹配的教材 可以清空筛选条件，或者直接新建一本教材。 | long, multi-clause |
| P2 | `templates/profile.html:401` | `placeholder` | 这件作品证明了我在哪些方面有进步？下一次我会怎么做得更好？ | long, multi-clause |
| P2 | `templates/profile.html:424` | `empty` | 作品集还没有内容 先从上方候选成果中选择一件最能代表当前阶段的作品，再补一句复盘。 | long, multi-clause |
| P2 | `templates/report_card.html:123` | `empty` | 还没有已批改的成绩 完成作业或考试并由老师批改后，成绩会自动汇总到这里。 回到首页 | long, multi-clause |
| P2 | `templates/wrong_book.html:176` | `empty` | 暂无知识点数据 老师在试卷中标注知识点后，这里会自动生成你的掌握度画像。 | long, multi-clause |
| P3 | `frontend/src/islands/assignment-task-board-sync.tsx:90` | `description` | 查看当前学生可进入的任务，包含进行中的作业和考试。 | dense, multi-clause, secondary-copy |
| P3 | `frontend/src/islands/assignment-task-board-sync.tsx:109` | `description` | 定位还需要提交或重新提交的任务，优先处理这些卡片。 | dense, multi-clause, secondary-copy |
| P3 | `frontend/src/islands/assignment-task-board-sync.tsx:129` | `description` | 定位剩余时间较紧的任务，避免错过提交或补交窗口。 | dense, multi-clause, secondary-copy |
| P3 | `frontend/src/lib/assignment-submit.ts:116` | `description` | 重新提交会替换当前版本，请确认答案和附件无遗漏。 | dense, multi-clause, secondary-copy |
| P3 | `frontend/src/lib/assignment-submit.ts:131` | `description` | 还可以继续补充答案或附件，提交时会一并保存。 | dense, multi-clause, secondary-copy |
| P3 | `static/js/dashboard.js:1679` | `copy` | Overall score 总体评价 / 100 … 份有效评价 · … 条有效评语 | long |
| P3 | `static/js/dashboard_agenda_widget.js:622` | `hint` | 不填截止日期则记为“无截止”，会一直留在待办里。 | dense, multi-clause, secondary-copy |
| P3 | `static/js/global_search.js:55` | `placeholder` | 搜索课堂、材料、作业考试、博客… | dense, multi-clause |
| P3 | `static/js/manage_courses.js:358` | `hint` | 请先填写课程总学时，并保证能被每次课小节数整除。 | dense, multi-clause, secondary-copy |
| P3 | `static/js/manage_courses.js:365` | `hint` | 当前学时不能被每次课小节数整除，请调整后再生成。 | dense, multi-clause, secondary-copy |
| P3 | `static/js/manage_lesson_plans.js:469` | `placeholder` | 例如：强调组件通信、课堂演示、分层练习 | dense, multi-clause |
| P3 | `static/js/manage_lesson_plans.js:524` | `placeholder` | 输入本次课主要内容，AI 会结合前后课次润色成可生成的课次卡片 | long |
| P3 | `static/js/manage_lesson_plans.js:967` | `placeholder` | 如：Linux、专业核心、2025秋 | dense, multi-clause |
| P3 | `static/js/manage_polls.js:79` | `empty` | 还没有投票活动 点击右上角「新建投票」创建跨班级投票活动。 | long |
| P3 | `static/js/manage_teacher_evaluations.js:641` | `placeholder` | 如：软工231、2025秋、优秀 | dense, multi-clause |
| P3 | `static/js/materials_manage.js:2572` | `title` | 查看并编辑全部学生的原始成绩，系统自动重算或反推平时成绩 | long |
| P3 | `templates/assignment_detail_teacher.html:1619` | `placeholder` | 搜索学生姓名 / 学号 / 附件后缀... | dense, multi-clause |
| P3 | `templates/assignment_detail_teacher.html:1750` | `placeholder` | 例如：.pdf,.docx,image/* | dense, multi-clause |
| P3 | `templates/assignment_detail_teacher.html:1900` | `placeholder` | 例如：线下补交、网络异常、需要重新提交附件 | dense, multi-clause |
| P3 | `templates/blog.html:96` | `intro` | 全站广场 从不同方向遇见新问题、新知识和下一段旅程。 | dense, multi-clause, secondary-copy |
| P3 | `templates/classroom_main_v4.html:454` | `title` | 结课：收尾课堂内未结束的作业、测验、投票与分组 | dense, multi-clause |
| P3 | `templates/classroom_main_v4.html:2303` | `placeholder` | 选择同学或老师后发送一对一消息，可粘贴或拖入图片/文件 | long |
| P3 | `templates/classroom_main_v4.html:2431` | `placeholder` | 请描述作业目标、提交方式和截止说明... | dense, multi-clause |
| P3 | `templates/classroom_main_v4.html:2436` | `placeholder` | 填写评分细则，帮助学生理解重点与预期表现... | dense, multi-clause |
| P3 | `templates/classroom_main_v4.html:2461` | `placeholder` | 例如：.pdf,.docx,image/* | dense, multi-clause |
| P3 | `templates/classroom_main_v4.html:2578` | `placeholder` | 例如：.zip,.py,.pdf | dense, multi-clause |
| P3 | `templates/dashboard.html:650` | `title` | 帮助你确认最近一次登录设备与时间。 | dense, framing |
| P3 | `templates/exam_editor.html:2199` | `placeholder` | 提示文本 (例如: 请详细说明...) | dense, multi-clause |
| P3 | `templates/exam_editor.html:2211` | `placeholder` | 附件说明，例如：请上传实验截图、核心代码或报告 | dense, multi-clause |
| P3 | `templates/feedback_review.html:169` | `placeholder` | 例如：提交前先核对截图标注、命令输出、单位。 | dense, multi-clause |
| P3 | `templates/learning_path.html:223` | `placeholder` | 例如：复盘后再做一道同类题，或课前补完材料。 | dense, multi-clause |
| P3 | `templates/manage/ai.html:113` | `placeholder` | 填写课程目标、章节安排、知识点和考核方式等。 | dense, multi-clause |
| P3 | `templates/manage/assessment_plans.html:90` | `empty` | 还没有考核计划表 点击右上角「空白新建」「按课堂生成」或「导入解析」开始。 | long |
| P3 | `templates/manage/classes.html:408` | `placeholder` | 例如：竞赛辅导、补修答疑、校外临时学习支持 | dense, multi-clause |
| P3 | `templates/manage/exams.html:1527` | `placeholder` | 搜索试卷名称、描述、标签、状态或题型... | dense, multi-clause |
| P3 | `templates/manage/gongwen.html:407` | `title` | 把所有已解析公文重新匹配一遍当前关注设置，完成后站内信汇总提醒 | long |
| P3 | `templates/manage/lesson_plans.html:82` | `empty` | 还没有教案 点击右上角「空白新建」「按课堂生成」或「导入教案」开始。 | long |
| P3 | `templates/manage/student_detail.html:270` | `empty` | 暂无课堂进度 当前教师名下还没有与该学生所在班级绑定的课堂。 | long |
| P3 | `templates/manage/teacher_evaluations.html:90` | `empty` | 还没有教师评学表 点击右上角「空白新建」「按班级生成」或「导入解析」开始。 | long |
| P3 | `templates/manage/textbooks.html:180` | `placeholder` | 搜索教材名称、作者、出版社、简介、目录或标签 | dense, multi-clause |
| P3 | `templates/manage/textbooks.html:323` | `placeholder` | 粘贴完整的教材目录，支持任意格式。内容不会丢失。 | dense, multi-clause |
| P3 | `templates/manage/workflow.html:17` | `h2` | 按新建课堂向导，一步一步完成开课 | dense, long-heading |
| P3 | `templates/partials/session_material_ai_modal.html:40` | `placeholder` | 例如：课堂学习文档、实验指导、案例讲义、复习提纲 | dense, multi-clause |
| P3 | `templates/resume/home.html:16` | `hint` | 支持 Word、PDF 和图片，单个文件不超过 20MB | dense, multi-clause, secondary-copy |
| P3 | `templates/resume/job_targets.html:6` | `h2` | 把岗位描述变成一张可执行的简历清单 | dense, long-heading |
| P3 | `templates/resume/list.html:23` | `copy` | 把已有简历拖到这里 支持 Word / PDF / 图片，解析后自动生成可预览简历并合并资料 | long |
| P3 | `templates/wrong_book.html:161` | `subtitle` | 按你的历次作答统计，越靠上越需要优先补强。 | dense, multi-clause, secondary-copy |
| P3 | `frontend/src/lib/assignment-submit.ts:124` | `description` | 提交前再检查一次附件与文本内容即可。 | dense, secondary-copy |
| P3 | `static/js/ai_workspace_widget.js:1167` | `empty` | 暂无任务。提交后会进入全平台队列。 | dense, multi-clause |
| P3 | `static/js/blog.js:975` | `description` | 从全站内容信号中发现值得继续读下去的文章。 | dense, secondary-copy |
| P3 | `static/js/blog.js:1891` | `empty` | 还没有精选内容，发一篇高质量帖子来点亮这里。 | dense, multi-clause |
| P3 | `static/js/classroom_closeout.js:92` | `hint` | 全部已提交并批改，将直接截止。 | multi-clause, secondary-copy |
| P3 | `static/js/collaboration.js:257` | `placeholder` | 写清楚本组完成了什么、谁负责了什么、还有什么待改进 | dense, multi-clause |
| P3 | `static/js/dashboard_agenda_widget.js:653` | `placeholder` | 任务要求、材料位置，或提醒自己的话 | dense, multi-clause |
| P3 | `static/js/group_assignment_config.js:63` | `empty` | 该课堂还没有分组方案，请在下方新建一个分组方案。 | dense, multi-clause |
| P3 | `static/js/learning_material_selector.js:176` | `empty` | 正在加载材料... | multi-clause |
| P3 | `static/js/learning_progress.js:354` | `empty` | 正在查询本地教学场地... | multi-clause |
| P3 | `static/js/lesson_plan_editor.js:98` | `empty` | 还没有课次，点击右上角「+ 添加课次」。 | dense, multi-clause |
| P3 | `static/js/manage_assessment_plans.js:472` | `subtitle` | 先定位学年学期，再定位课程和班级 | dense, secondary-copy |
| P3 | `static/js/manage_assessment_plans.js:655` | `placeholder` | 如：机试、2025秋、专升本 | multi-clause |
| P3 | `static/js/manage_course_schedule.js:187` | `empty` | 暂无课程统计，请先同步或调整筛选。 | dense, multi-clause |
| P3 | `static/js/manage_gongwen.js:598` | `title` | 系统自动关注你的姓名，无需配置、不可删除 | dense, multi-clause |
| P3 | `static/js/manage_signatures.js:154` | `empty` | 正在加载签名... | multi-clause |
| P3 | `static/js/manage_teacher_evaluations.js:458` | `subtitle` | 先定位学年学期，再定位课程和班级 | dense, secondary-copy |
| P3 | `static/js/material_viewer.js:468` | `empty` | 文件夹材料请打开子文件查看，或回到材料库批量下载。 | dense, multi-clause |
| P3 | `static/js/materials_manage.js:2396` | `empty` | 材料正在生成中，完成后会自动刷新到列表。 | dense, multi-clause |
| P3 | `static/js/materials_manage.js:2726` | `empty` | 当前文件暂不支持内嵌预览，可下载后查看。 下载文件 | dense, multi-clause |
| P3 | `static/js/materials_manage.js:3244` | `empty` | 正在读取学生原始成绩... | multi-clause |
| P3 | `static/js/materials_manage.js:5716` | `empty` | 正在加载课堂与课次... | multi-clause |
| P3 | `static/js/prompt_pool.js:140` | `empty` | 正在读取共享提示词... | multi-clause |
| P3 | `static/js/resume_applications.js:86` | `placeholder` | 面试反馈、联系人、要补充的材料等 | dense, multi-clause |
| P3 | `static/js/resume_home.js:59` | `empty` | 还没有简历。导入已有文件通常是最快的开始方式。 | dense, multi-clause |
| P3 | `static/js/teacher_onboarding.js:295` | `empty` | 还没有可选学期，先新建一个学期再继续。 | dense, multi-clause |
| P3 | `static/js/teacher_onboarding.js:377` | `empty` | 没有匹配到旧课程。继续输入后将按新课程处理。 | dense, multi-clause |
| P3 | `static/js/teacher_onboarding.js:938` | `placeholder` | 课程定位、学习目标、实践方式和适用专业 | dense, multi-clause |
| P3 | `templates/assignment_detail_student.html:1001` | `placeholder` | 请在此输入你的完整答案... | multi-clause |
| P3 | `templates/assignment_detail_teacher.html:1432` | `hint` | 人已提交， 还有 - 人未交。 | multi-clause, secondary-copy |
| P3 | `templates/assignment_wrong_summary.html:1526` | `title` | 手动启动 AI 错答整理；不会修改学生答案、分数和评语 | dense, multi-clause |
| P3 | `templates/blog.html:89` | `hint` | ↔ 按住频道带拖动 旋钮 / 滚轮 / 方向键均可切换 | dense, secondary-copy |
| P3 | `templates/blog.html:144` | `placeholder` | 搜标题、正文或标签... | multi-clause |
| P3 | `templates/blog.html:148` | `intro` | 把机会变成下一步 优先展示仍可报名、来源可核验的信息 | dense, secondary-copy |
| P3 | `templates/blog.html:297` | `placeholder` | 帖子标题... | multi-clause |
| P3 | `templates/blog.html:319` | `placeholder` | 支持 Markdown 格式... | multi-clause |
| P3 | `templates/blog.html:384` | `placeholder` | 搜索用户... | multi-clause |
| P3 | `templates/classroom_main_v4.html:1009` | `empty` | 完成本周学习后，这里会沉淀你的修为走势。 | dense, multi-clause |
| P3 | `templates/classroom_main_v4.html:1314` | `empty` | 周快照正在积累，满 2 周后显示环比趋势。 | dense, multi-clause |
| P3 | `templates/classroom_main_v4.html:2144` | `summary` | 当前身份当前代号 分配中... | multi-clause |
| P3 | `templates/exam_editor.html:1105` | `placeholder` | 填写注意事项或考试说明... | multi-clause |
| P3 | `templates/exam_take.html:1659` | `placeholder` | 向考试 AI 提问... | multi-clause |
| P3 | `templates/feedback_review.html:87` | `placeholder` | 课程、任务、扣分点、反思 | multi-clause |
| P3 | `templates/learning_path.html:163` | `placeholder` | 课程、任务、材料、复盘 | multi-clause |
| P3 | `templates/learning_path.html:219` | `placeholder` | 写下这一步做完后，自己真正弄懂了什么。 | dense, multi-clause |
| P3 | `templates/manage/classrooms.html:585` | `placeholder` | 搜索 B422、知新楼、实验室或校区 | dense, multi-clause |
| P3 | `templates/manage/courses.html:1374` | `placeholder` | 补充课程定位、教学目标、授课环境和必要说明 | dense, multi-clause |
| P3 | `templates/manage/exams.html:2009` | `placeholder` | 输入新标签... | multi-clause |
| P3 | `templates/manage/exams.html:2138` | `emptyText` | 暂无可反推的试卷，请先完成试卷题目和分值设置。 | dense, multi-clause |
| P3 | `templates/manage/gongwen.html:337` | `placeholder` | 该文件暂无解析文本，可在此补充后保存。 | dense, multi-clause |
| P3 | `templates/manage/materials.html:776` | `hint` | 选择材料类型后会显示支持的文件格式。 | dense, secondary-copy |
| P3 | `templates/manage/materials.html:822` | `empty` | 暂无可分配课堂，请先在“开设课堂”中创建课堂。 | dense, multi-clause |
| P3 | `templates/manage/offerings.html:450` | `h2` | 开设课堂与生成课程时间轴 | long-heading |
| P3 | `templates/manage/signatures.html:549` | `lead` | 签名按学校与系部隔离；引用他人签名需归属人审批。 | dense, multi-clause |
| P3 | `templates/manage/signatures.html:614` | `summary` | 正在加载签名... 清空筛选 | multi-clause |
| P3 | `templates/manage/textbooks.html:146` | `lead` | 统一维护教材信息与附件，开设课堂时可直接引用。 | dense, multi-clause |
| P3 | `templates/manage/textbooks.html:257` | `placeholder` | 例如：主教材、实验课、必修 | multi-clause |
| P3 | `templates/manage/textbooks.html:319` | `placeholder` | 粘贴或填写教材简介，支持任意格式。 | dense, multi-clause |
| P3 | `templates/message_center.html:57` | `placeholder` | 搜索联系人、消息标题、内容关键词 | dense, multi-clause |
| P3 | `templates/partials/ai_workspace_widget.html:129` | `placeholder` | 把当前页面作为上下文提问... | multi-clause |
| P3 | `templates/partials/teacher_onboarding_modal.html:33` | `note` | 每一步只确认一件事，随时可以返回调整。 | dense, multi-clause |
| P3 | `templates/points_shop.html:90` | `subtitle` | 全自动记账，无需手动领取。 | multi-clause, secondary-copy |
| P3 | `templates/points_shop.html:109` | `empty` | 还没有流水，今天学习一下就有第一笔进账。 | dense, multi-clause |
| P3 | `templates/submission_detail.html:914` | `summary` | 补交提交，已按教师设置扣分。 原始分 · 扣 分 | dense, multi-clause |
| P3 | `templates/submission_detail.html:1027` | `copy` | 支持 单文件 MB，总计 MB，最多 个 | dense, multi-clause |
| P3 | `templates/submission_detail.html:1083` | `placeholder` | 输入评语，给出具体反馈... | multi-clause |
