# P01 - 基础资源双编辑模式改造目标

创建日期：2026-06-05

## 目标定位

在教师管理中心的基础资源板块，把班级、课程、教材、试卷、材料五类核心资源改造成清晰的双编辑模式：

- 属性编辑：维护资源身份、组织归属、可见范围、状态、标签、来源、引用关系、统计信息和分配关系等资源元数据。
- 内容编辑：维护资源内部教学内容、学生名单、课次结构、教材正文、试题、评分标准、材料文件正文和 AI 生成内容等业务正文。

这份文档是 `target/target02/P01` 的可跟踪目标文档，不直接改线上数据，不直接运行迁移，不直接部署。后续实现必须逐条对照本文验收。

## 数据安全红线

1. 严禁为了验证目标直接修改线上数据库、线上材料文件、线上提交文件或远程 `/lanshare/data`。
2. 所有涉及字段新增、字段回填、删除策略、材料文件迁移、试卷版本迁移的验证，必须先复制到 repo 根目录 `.codex-temp/` 下运行。
3. 不得把 `data/classroom.db` 当作实验库直接做破坏性测试；受保护页面浏览器验证必须使用临时数据根和临时 QA 账号。
4. 不得把明文密码、cookie、token、远程凭据、教师账号、学生敏感信息写入目标文档、日志、提交或 PR。
5. 所有删除动作必须先证明无引用或走软删除/归档流程；已被课堂、作业、提交、材料分配、AI 任务引用的资源不得静默硬删除。
6. 后续如果需要部署，必须单独得到明确部署请求，并先执行 `deployment/deploy_remote.ps1 -DryRun`，同时保留线上数据目录。

## 现有系统事实

### 管理中心入口

基础资源导航位于 `templates/manage/layout.html`，当前包括班级、课程、教室、教材、试卷、签名、材料。本期双模式改造主范围只覆盖用户点名的五类：班级、课程、教材、试卷、材料。

教室、签名、学期、开设课堂是强关联边界：

- 学期和开设课堂负责把班级、课程、教材、教师、课次、材料、试卷作业串起来。
- 教室更多是教务或智慧课堂同步出来的场地资源，本期只在关联关系中考虑，不纳入双编辑主范围。
- 电子签名是安全与文档签署资源，本期只要求不要被基础资源改造误伤。

### 后端和数据链路

- 班级和学生：`classes`、`students`，页面 `/manage/classes`，接口在 `classroom_app/routers/manage_parts/classes_courses_classes.py`。
- 课程和课次模板：`courses`、`course_lessons`，页面 `/manage/courses`，接口在 `classroom_app/routers/manage_parts/classes_courses_courses.py`。
- 教材：`textbooks`，页面 `/manage/textbooks`，接口在 `classroom_app/routers/manage_parts/semesters_textbooks.py`。
- 试卷：`exam_papers`，页面 `/manage/exams`，接口在 `classroom_app/routers/homework_parts/exam_papers.py`，分配后生成 `assignments`。
- 材料：`course_materials`、`material_ai_import_records`、`course_material_assignments`、`session_material_generation_tasks`，页面 `/manage/materials`，接口在 `classroom_app/routers/materials_parts/*`。
- 统一权限服务：`classroom_app/services/resource_access_service.py` 已有 `teacher_can_use_*`、`teacher_can_manage_*`、`can_read_scoped_resource`、`can_manage_scoped_resource` 等基础能力。

### 权限基线

后续实现必须沿用并强化这条顺序：

1. 身份有效：教师、学生或超管身份真实有效，账号未停用。
2. 组织范围：教师按所有 active `teacher_organization_memberships` 判断，不只看主学校；学生按自己的学校、班级和课堂判断。
3. 资源归属：明确 owner、上传者、创建者、归属管理员、课堂教师或学生本人。
4. 课堂分配或发布：学生只能访问已发布、已分配到本人课堂或明确绑定给本人的资源。
5. 动作权限：`view/use` 与 `manage/edit/delete/publish/assign/grade/admin` 必须分离。
6. 副作用前置校验：任何写库、写文件、入队 AI 任务、删除、分配、改分之前先完成权限校验。

## 双模式定义

### 属性编辑

属性编辑面向“这个资源是谁的、给谁看、处在什么状态、如何被业务引用”。它应当低频、稳定、可审计，并且不会直接改写资源正文。

属性字段包括但不限于：

- 资源名称或展示标题。
- 所属学校、学院、系部、专业或部门。
- owner、上传者、创建者、归属管理员、出题人、共享维护人。
- 可见范围：私有、本班、本课堂、本系部、本学院、本学校、公开等。
- 状态：草稿、就绪、已发布、已截止、已归档、已删除、AI 生成中、AI 生成失败等。
- 标签、来源、教务同步标记、同步时间、错误信息。
- 创建时间、更新时间、发布时间、归档时间、删除时间。
- 引用和分配：开设课堂、课次、试卷分配、材料分配、教材绑定、作业绑定。
- 统计字段：人数、使用次数、分配次数、提交数、附件大小等，只可查看或由系统自动计算。

### 内容编辑

内容编辑面向“这个资源里面实际教什么、考什么、给学生看什么、学生名单是什么”。它更容易影响学生学习记录、考试公平、材料可读性和评分一致性。

内容字段包括但不限于：

- 班级学生名单、学生身份信息、学习状态、共享教师备注。
- 课程课次模板、每次课标题、每次课内容、课次顺序、材料绑定。
- 教材简介、目录、附件正文或附件替换。
- 试卷标题、说明、题目、题型、选项、附件要求、总分、评分标准、评分配置。
- 材料文件正文、目录树、README、AI 解析内容、AI 优化内容、最终材料导出内容。

## 全局改造原则

1. 每个资源页面都必须能明显区分“属性”和“内容”两个编辑入口，避免教师误把共享属性改成正文修改，或误把正文修改当成发布范围修改。
2. 属性保存和内容保存必须使用不同的接口或至少不同的后端命令分支，返回不同的审计信息。
3. 所有列表、详情、预览、编辑、删除、分配接口必须使用同一套权限语义；不能出现列表不可见但直接 URL 可编辑的情况。
4. 可以查看不等于可以编辑，可以引用不等于可以管理，可以分配不等于可以改内容。
5. 同校、同院、同系资源默认只授予查看/复用，不自动授予编辑/删除。
6. 归属管理员或超管可以调整属性，但内容编辑仍应尊重资源业务上下文，例如已经发布的试卷、已经提交的作业、已经分配的材料不得随意改正文。
7. 删除优先改为归档或软删除；硬删除必须先证明没有课堂、作业、提交、材料、AI 任务、附件、导出记录引用。
8. 查询和统计必须走已有索引或可控分页，不允许为了展示双模式在教师中心一次性拉取全校全量学生、全量提交或全量材料正文。
9. 前端只负责隐藏不可用入口和给出清晰反馈，后端必须再次校验权限。
10. 所有新字段要通过迁移脚本兼容旧数据，默认值必须保守，不能扩大可见范围。

## 资源总览矩阵

| 资源 | 属性编辑核心 | 内容编辑核心 | 默认可见/可用目标 | 管理目标 |
| --- | --- | --- | --- | --- |
| 班级 `classes` | 班级身份、入学/毕业/学制、组织归属、归属管理员、可见范围、统计 | 学生名单、学生状态、学生资料、批量导入/同步 | 默认本校教师可查看/复用，学生只属于自己的班级 | 归属管理员或超管管理属性；任课教师可按业务查看学生，内容编辑需明确授权 |
| 课程 `courses` | 课程名称、系别、学分、总学时、组织归属、共享范围、教务来源 | 课次模板、课次内容、课次材料绑定、AI 拆课 | 默认本校或本系可复用，视 scope 决定 | 创建者或超管管理；共享教师可开课但不可改模板内容 |
| 教材 `textbooks` | 书目信息、作者、出版社、出版日期、标签、归属、可见范围、引用统计 | 简介、目录、附件、AI 整理结果 | 目标支持本校/本系可引用；当前实现偏教师个人 | 创建者或超管管理；被课堂引用后删除受限 |
| 试卷 `exam_papers` | 归属、组织、scope、标签、库状态、分配状态、AI 生成状态、默认批改策略 | 试题、标题、说明、总分、评分标准、题目附件要求 | 私有/本系/本校；分配后学生按作业访问 | owner 或超管管理；已分配试卷内容修改需版本化或冻结 |
| 材料 `course_materials` | 名称、路径、类型、scope、owner、Git 状态、AI 状态、课堂分配 | 文件正文、目录树、上传替换、AI 改写/导入、最终材料导出 | 私有/本系/本校；学生必须经课堂分配访问 | owner 或超管管理；分配给课堂前需同时校验材料可用和课堂可管理 |

## 班级目标

### 现有字段和链路

当前 `classes` 已有 `name`、`created_by_teacher_id`、`school_code`、`school_name`、`college`、`department`、`description`、`academic_source`、`academic_class_code`、`academic_class_name`、`academic_college`、`academic_grade`、`academic_major`、`academic_sync_at`、`academic_sync_message`、`academic_metadata_json`、`created_at`。

学生内容在 `students` 表中，核心字段包括 `student_id_number`、`name`、`class_id`、`gender`、`email`、`phone`、`wechat`、`qq`、`homepage_url`、`profile_info`、`nickname`、`description`、`enrollment_status`、`enrollment_status_updated_at`、`enrollment_note`、教务同步字段、组织字段和 `created_at`。

页面 `/manage/classes` 已展示班级统计、学生列表、教务同步、创建班级并导入学生、添加单个学生、学生状态修改、学生详情入口。

### 属性可查看

- 班级名称、班级简介。
- 学校、学院、系部、专业或班级所属教学单位。
- 教务来源、教务班级代码、教务班级名、教务年级、专业、同步时间、同步提示。
- 归属管理员：当前兼容 `created_by_teacher_id`，目标应显示为“归属管理员/维护人”。
- 可见范围：目标新增 `scope_level` 或等价字段，建议默认学校范围；可选本班、本系部、本学院、本学校。
- 入学年份、预计毕业年份、学制年限：现有表未直接存储，目标新增字段或从教务元数据稳定解析。
- 人数统计：总人数、在读人数、休学/暂停人数、缺邮箱人数、教务同步人数。
- 开课引用：被多少 `class_offerings` 使用、最近开课时间、关联教师。
- 创建时间、更新时间、归档/删除状态。

### 属性可编辑

- 班级名称、简介。
- 学校、学院、系部、专业。
- 入学年份、预计毕业年份、学制年限。
- 可见范围。
- 归属管理员或共享维护人，只有超管或现归属管理员可移交。
- 归档/恢复状态。

属性编辑不得直接新增、删除或改写学生记录。属性保存成功后，只允许更新 `classes` 及必要的组织/审计字段。

### 属性只读或系统计算

- 人数、缺邮箱数、在读/休学人数。
- 教务同步来源、同步时间、同步错误的原始证据。
- 创建时间、最后一次学生同步时间。
- 是否已经开课、被多少课堂引用。

这些字段不允许教师在属性表单中手填。

### 内容可查看

- 学生名单：姓名、学号、性别、邮箱、手机号、学习状态、同步来源。
- 学生详情：学习概览、课堂进度、登录安全摘要、教师共享备注。
- 批量导入预览、教务同步差异预览。
- 与该班级相关的课堂、材料、作业、考试概览，但不能泄露无权查看的提交正文。

### 内容可编辑

- 单个添加学生。
- 批量导入学生名单。
- 教务系统同步班级和学生。
- 修改学生基础信息：姓名、学号、性别、邮箱、手机号等。
- 修改学生学习状态：在读、休学、退学、暂停、恢复等，当前已有 `active`、`suspended`，目标可扩展但必须兼容旧值。
- 填写教师共享备注。
- 批量状态变更和批量资料修正。

内容编辑必须在学生级别校验：教师能管理班级，不代表一定可以查看其他班级学生；任课教师可以查看与自己课堂有关的学生，但批量改学生身份和状态应限归属管理员或超管。

### 先后关系

1. 先保存班级属性，确认组织范围和归属管理员。
2. 再导入或同步学生，学生组织字段继承班级属性。
3. 再开设课堂，课堂绑定班级、课程、教材和学期。
4. 再发布材料或试卷到课堂。

如果班级组织归属变更，必须检查学生组织字段、课堂归属、材料/作业访问边界是否需要同步或保留历史快照。

### 班级测试预期

- 同校教师可以在允许范围内看到班级卡片，但无管理权时不能编辑属性、删除班级、批量改学生。
- 任课教师可以查看自己课堂里的学生学习信息，但不能把非归属班级的学生转出、删除或改学号。
- 修改班级属性不会改变学生数量、提交记录、作业成绩和材料分配。
- 学生状态改为休学/暂停后，该学生不能继续进入课堂学习流程，但历史提交和成绩保留。
- 删除班级前如果存在学生、课堂、提交、材料分配或登录记录，接口返回阻止信息，不能直接硬删。

## 课程目标

### 现有字段和链路

当前 `courses` 已有 `name`、`description`、`sect_name`、`department`、`school_code`、`school_name`、`college`、`credits`、`total_hours`、`created_by_teacher_id`、`academic_source`、`academic_course_code`、`academic_sync_at`、`academic_sync_message`、`academic_metadata_json`、`created_at`。

课程内容模板在 `course_lessons`，字段包括 `course_id`、`order_index`、`title`、`content`、`section_count`、`source_type`、`learning_material_id`、`created_at`、`updated_at`。

课程被 `class_offerings` 引用，材料可通过 `learning_material_id` 绑定到课次。

### 属性可查看

- 课程名称、课程简介短说明。
- 课程所属学校、学院、系部。
- 课程类别或课程门派称号字段 `sect_name`。
- 学分、总学时。
- 教务课程号、教务同步来源、同步时间、同步消息。
- 创建者/归属教师、可见范围、是否共享课程。
- 被开设课堂数量、课次数、课次总小节数、材料绑定数量。
- 结构完整度：总学时和课次小节数是否一致。
- 创建时间、更新时间、归档/删除状态。

### 属性可编辑

- 课程名称。
- 课程简介短说明。
- 课程所属学校、学院、系部。
- 课程类别或 `sect_name`。
- 学分、总学时。
- 可见范围：私有、本系、本学院、本校，默认建议本校或本系，按学校实际业务配置。
- 归属教师/维护人移交。
- 归档或恢复。

属性编辑不得改写 `course_lessons`，不得改变课次顺序、课次正文或材料绑定。

### 内容可查看

- 课次模板列表。
- 每次课标题、教学内容、节数。
- 课次绑定的 Markdown 材料。
- AI 拆课生成结果、教务课表匹配结果、课次覆盖状态。

### 内容可编辑

- 新增、删除、排序课次。
- 修改课次标题、课次内容、课次节数。
- 绑定或清除课次材料。
- 使用教材和总学时执行 AI 拆课。
- 将课程模板用于开设课堂时生成 `class_offering_sessions`。

共享教师可以复用课程开课，但不能修改课程内容模板，除非成为归属维护人或超管授权。课程内容变更如果已被课堂使用，必须明确是“仅改课程模板”还是“同步更新未开始课堂的课次”，不能自动覆盖已运行课堂。

### 先后关系

1. 先确定课程属性：组织范围、学时、学分、可见范围。
2. 再配置课程内容模板：课次、小节、材料绑定。
3. 再开设课堂，生成课堂时间轴。
4. 课堂运行后，课程模板变更默认不回写已生成课堂，除非教师执行显式同步并确认影响。

### 课程测试预期

- 同校可用课程可用于开设课堂，但 `can_manage=false` 时编辑按钮不可用，伪造保存请求返回 403。
- 课程总学时与课次小节数不一致时，内容保存返回明确错误，不生成错误课堂时间轴。
- 删除课程前如存在 `class_offerings` 引用，返回阻止信息。
- 课次材料绑定必须校验材料可读且为合法 Markdown；无权材料不能被绑定。
- 修改课程属性不会改变已存在课次正文、课堂课次、材料文件和学生作业。

## 教材目标

### 现有字段和链路

当前 `textbooks` 已有 `teacher_id`、`title`、`authors_json`、`publisher`、`publication_date`、`introduction`、`catalog_text`、`attachment_name`、`attachment_path`、`attachment_size`、`attachment_mime_type`、`tags_json`、`created_at`、`updated_at`。

教材可在开设课堂时绑定到 `class_offerings.textbook_id`，也可用于课程 AI 拆课。

当前权限更偏教师个人：`teacher_can_use_textbook` 基本等同 `teacher_can_manage_textbook`。目标应支持与班级、课程、试卷、材料一致的组织 scope，但默认必须保守，不得因为新增 scope 让旧教材突然全校可见。

### 属性可查看

- 教材名称。
- 作者列表。
- 出版社。
- 出版日期或年份。
- 标签。
- 归属教师/上传教师。
- 所属学校、学院、系部，目标新增。
- 可见范围，目标新增，默认私有或沿用当前个人归属。
- 附件名称、附件大小、附件 MIME 类型。
- 被多少课堂绑定、被多少课程 AI 拆课使用。
- 创建时间、更新时间、归档/删除状态。

### 属性可编辑

- 教材名称。
- 作者列表。
- 出版社。
- 出版日期。
- 标签。
- 所属组织范围和可见范围。
- 归属教师移交。
- 归档/恢复。

属性编辑不得改写教材简介、目录、附件文件内容。

### 内容可查看

- 教材简介。
- 教材目录。
- 附件下载或预览入口。
- AI 整理后的简介/目录预览。
- 课堂和课程生成时引用的教材上下文摘要。

### 内容可编辑

- 修改教材简介。
- 修改教材目录。
- 上传、替换或移除附件。
- 使用 AI 整理简介和目录。

附件文件本身属于内容编辑；附件名称、大小、类型是系统属性，只能随附件更新自动变化。

### 先后关系

1. 先录入教材属性，确保书目身份稳定。
2. 再编辑简介、目录和附件。
3. 再用于课程 AI 拆课或开设课堂。
4. 已被课堂绑定的教材，删除或替换附件必须给出影响确认，至少保留历史可追溯信息。

### 教材测试预期

- 非归属教师在未开放 scope 时不可查看教材详情和附件。
- 共享可用教材可以被引用到课堂或课程生成，但不可被非 owner 修改简介、目录、附件。
- 删除教材前如存在 `class_offerings.textbook_id` 引用，返回阻止信息。
- AI 整理简介和目录只更新教材内容字段，不改变归属、scope、课堂绑定。
- 替换附件失败时不能丢失旧附件引用。

## 试卷目标

### 现有字段和链路

当前 `exam_papers` 已有 `id`、`teacher_id`、`title`、`description`、`questions_json`、`exam_config_json`、`status`、`ai_gen_task_id`、`ai_gen_status`、`ai_gen_error`、`tags_json`、`created_at`、`updated_at`。

迁移中已经为试卷补充 `owner_role`、`owner_user_pk`、`scope_level`、`school_code`、`school_name`、`college`、`department`、`published_at`。试卷列表会按 `teacher_can_use_exam_paper` 过滤，卡片显示 `can_manage`、`is_owned`、`scope_label`、`assigned_count`。

试卷分配接口会创建 `assignments`，并把试卷转成课堂作业。`assignments` 才承担开始时间、截止时间、自动关闭、迟交策略、班级课堂、学生提交和 AI 批改运行状态。

### 属性可查看

- 归属教师、owner、出题人或上传者。
- 所属学校、学院、系部。
- 可见范围：私有、本系部、本学校。
- 试卷库状态：草稿、就绪、已发布、归档。
- 分配状态：已分配次数、分配到哪些课堂、对应作业状态。
- 标签。
- AI 生成状态：排队、运行、完成、失败、错误信息。
- 默认批改策略：是否默认 AI 批改、是否允许学生端 AI 辅助、附件要求默认策略，目标应归入 `exam_config_json` 或独立配置。
- 删除状态：目标建议新增 `deleted_at` 或 `archived_at`，避免已分配试卷硬删除。
- 创建时间、更新时间、发布时间。

### 属性可编辑

- 归属教师/维护人移交。
- 学校、学院、系部和可见范围。
- 标签。
- 试卷库状态。
- 分配到课堂的配置：目标课堂、作业标题、开始时间、截止时间、考试时长、自动关闭、迟交策略、通知策略。
- 默认 AI 批改和学生 AI 辅助策略。
- 归档、恢复、软删除。

属性编辑不得直接改写 `questions_json`、题目总分或评分标准。

### 内容可查看

- 试卷标题和说明。
- 试题结构：页、题型、题干、选项、附件要求。
- 总分和各题分值。
- 标准答案、评分指导、扣分点。
- 学生视角预览。
- 原生 JSON 导入结果预览。

### 内容可编辑

- 新建或修改试卷标题和说明。
- 新增、删除、排序题目和试卷页。
- 修改题干、题型、选项、附件要求。
- 修改分值、总分、标准答案、评分指导和扣分点。
- 导入原生 JSON 试卷。
- AI 生成或补全试卷结构。

标题在试卷中既是展示身份又是学生答题内容的一部分。目标上建议试卷库卡片的“展示名称”归属性字段，考试正文中的“试卷标题”归内容字段；两者默认同步，但允许版本化后分离。

### 已分配试卷的特殊规则

1. 试卷未分配时，owner 可以自由修改内容。
2. 试卷已分配但无人提交时，可以修改内容，但必须重新生成对应作业 rubric，并记录更新时间。
3. 试卷已产生提交、草稿、AI 批改或成绩后，内容编辑必须走版本化：复制新版本、保留旧版本给历史提交使用。
4. 修改属性中的标签、scope、归属、分配状态，不得改变历史学生答题页面和评分标准。
5. 删除已分配试卷必须阻止或软删除，不能影响学生查看成绩。

### 先后关系

1. 先设定试卷属性：归属、组织、scope、标签、默认批改策略。
2. 再编辑试卷内容：题目、分值、评分标准。
3. 再验证总分和评分标准完整性。
4. 再分配到课堂生成作业。
5. 再进入学生提交和教师批改流程。

### 试卷测试预期

- 无权教师不能通过 `/api/exam-papers/{id}` 读取非开放试卷。
- 同系/同校可用试卷可以预览和分配到自己课堂，但不能编辑内容、标签或删除，除非具有 manage 权限。
- 分配时必须同时校验试卷可用和课堂可管理。
- 评分标准不完整时禁止分配，错误提示指向缺失题目、答案、分值或扣分点。
- 已有提交的试卷内容修改必须生成新版本或被阻止；历史提交评分不被覆盖。
- 删除已分配试卷返回阻止信息，或只执行软删除且历史作业仍可查看。

## 材料目标

### 现有字段和链路

当前 `course_materials` 已有 `teacher_id`、`parent_id`、`root_id`、`material_path`、`name`、`node_type`、`mime_type`、`preview_type`、`ai_capability`、`file_ext`、`file_hash`、`file_size`、`ai_parse_status`、`ai_parse_result_json`、`ai_optimize_status`、`ai_optimized_markdown`、`created_at`、`updated_at`。

迁移中已为材料补充 `owner_role`、`owner_user_pk`、`scope_level`、`school_code`、`school_name`、`college`、`department`、`published_at`，并有 Git 仓库状态字段。材料通过 `course_material_assignments` 分配到课堂，学生访问必须经课堂分配锚点。

材料 AI 导入记录在 `material_ai_import_records`，最终材料生成和导出也依赖该链路。

### 属性可查看

- 材料名称。
- 目录路径 `material_path`。
- 节点类型：文件或文件夹。
- 预览类型、MIME、扩展名、文件大小。
- owner、上传教师、所属学校、学院、系部。
- 可见范围：私有、本系、本学校、课堂等。
- Git 仓库状态、远程地址摘要、分支、检测错误、检测时间。
- AI 解析状态、AI 优化状态、AI 导入任务状态。
- 是否 README 学习文档、是否可预览、是否可编辑源码、是否可下载。
- 被哪些课堂分配、分配次数。
- 创建时间、更新时间、发布时间、归档/删除状态。

### 属性可编辑

- 材料名称或路径重命名，目标新增时必须保持树结构一致。
- 可见范围。
- 归属教师/维护人移交。
- 课堂分配。
- Git 远程凭据和仓库命令配置，但必须复用加密凭据模式。
- 归档、恢复、软删除。

属性编辑不得改写 `file_hash` 指向的文件正文，不得清空 AI 解析结果，除非是内容变更引起的系统自动重置。

### 内容可查看

- 文件预览：Markdown、文本、图片、PDF、文档、表格、PPT 等。
- 文件下载。
- 目录树和 README 学习文档。
- AI 解析结果和最终材料预览。
- AI 导入导出的 DOCX/PDF 等结果。

### 内容可编辑

- 上传文件或文件夹。
- 替换文本材料正文。
- 删除目录或文件，必须先检查课堂分配和全局文件引用。
- AI 解析、AI 优化、AI 改写。
- AI 生成课程材料和期末归档材料。
- Git 更新、拉取、推送、自动绑定 README 到课次。

文本内容编辑必须更新 `file_hash`、`file_size`、`updated_at`，并重置 `ai_parse_status`、`ai_parse_result_json`、`ai_optimize_status`、`ai_optimized_markdown`。旧文件只有在全局引用计数为 0 时才能删除。

### 课堂分配规则

1. 教师必须能读取该材料。
2. 教师必须能管理目标课堂。
3. 学生访问材料必须经 `course_material_assignments`，不能仅凭材料 scope 直接访问。
4. 分配文件夹时，子文件继承可访问锚点，但 `.git` 内部路径永远不可暴露。
5. 自动绑定 README 既改变课堂分配，也可能改变课次学习材料入口，必须显示结果并可回滚。

### 材料测试预期

- 学生不能通过材料 ID 直接打开未分配给本人课堂的材料，即使材料为本校或本系可见。
- 同系/同校教师可读取开放材料，但 `can_manage=false` 时不能改内容、删文件、改 scope、改 Git 凭据。
- 材料内容更新失败时不更新数据库 `file_hash`。
- 材料删除后，如果旧文件仍被其他材料引用，不删除全局文件。
- 课堂分配只影响当前教师管理的课堂，不得分配到其他教师课堂。
- `.git` 内部路径不出现在列表、详情、下载、学生端材料树中。

## 属性和内容接口目标

每类资源建议形成明确的接口分组。命名可以按现有路由风格调整，但语义必须清楚。

### 班级

- `GET /api/manage/classes/{id}/attributes`：查看班级属性。
- `PATCH /api/manage/classes/{id}/attributes`：更新班级属性。
- `GET /api/manage/classes/{id}/students`：查看学生内容。
- `POST /api/manage/classes/{id}/students/import`：批量导入学生。
- `POST /api/manage/classes/{id}/students`：新增学生。
- `PATCH /api/manage/students/{id}`：编辑学生信息。
- `PATCH /api/manage/students/{id}/status`：编辑学习状态。

### 课程

- `GET /api/manage/courses/{id}/attributes`。
- `PATCH /api/manage/courses/{id}/attributes`。
- `GET /api/manage/courses/{id}/content`：课次模板和材料绑定。
- `PUT /api/manage/courses/{id}/content`：替换课次模板。
- `POST /api/manage/courses/{id}/content/ai-generate-lessons`。

### 教材

- `GET /api/manage/textbooks/{id}/attributes`。
- `PATCH /api/manage/textbooks/{id}/attributes`。
- `GET /api/manage/textbooks/{id}/content`。
- `PUT /api/manage/textbooks/{id}/content`：简介、目录、附件替换。
- `POST /api/manage/textbooks/{id}/content/ai-format`。

### 试卷

- `GET /api/exam-papers/{id}/attributes`。
- `PATCH /api/exam-papers/{id}/attributes`。
- `GET /api/exam-papers/{id}/content`。
- `PUT /api/exam-papers/{id}/content`。
- `POST /api/exam-papers/{id}/assign`：属性侧的分配动作，必须校验内容完整性。
- `POST /api/exam-papers/{id}/content/version`：已发布或已提交后创建新版本。

### 材料

- `GET /api/materials/{id}/attributes`。
- `PATCH /api/materials/{id}/attributes`。
- `GET /api/materials/{id}/content`。
- `PUT /api/materials/{id}/content`。
- `POST /api/materials/{id}/assign`。
- `POST /api/materials/{id}/ai-rewrite`。
- `POST /api/materials/{id}/ai-import/optimize`。
- `POST /api/materials/{id}/repository/command`。

现有接口不必一次性全量改名，但必须在后续实现中保证请求 payload、后端函数和前端入口明确区分属性保存与内容保存。

## 前端目标

1. 每个资源详情或编辑弹窗提供两个清楚的模式入口：属性、内容。
2. 默认进入属性概览，显示资源归属、scope、状态、引用和风险提示。
3. 内容模式进入前检查 `can_manage_content`，不能只用 `can_manage` 笼统判断。
4. 属性保存按钮和内容保存按钮视觉上分离，文案清楚说明影响范围。
5. 对已引用资源显示影响提醒：
   - 班级已开课。
   - 课程已被课堂使用。
   - 教材已被课堂绑定。
   - 试卷已分配或已有提交。
   - 材料已分配到课堂。
6. 所有长任务提供忙碌态和失败态：教务同步、AI 拆课、AI 生成试卷、AI 导入材料、Git 操作。
7. 禁止在页面加载时静默触发会写库或会入队的 AI 任务。
8. 前端隐藏按钮只是体验优化，后端拒绝才是验收依据。

## 数据模型目标

后续实现如需新增字段，建议最小增量如下。实际迁移必须先在 `.codex-temp` 复制库验证。

### 通用资源属性

优先为五类资源统一或等价支持：

- `owner_role`
- `owner_user_pk`
- `scope_level`
- `school_code`
- `school_name`
- `college`
- `department`
- `published_at`
- `archived_at`
- `deleted_at`
- `updated_at`

当前试卷、材料、课程文件已有部分字段；班级、课程、教材需要评估是否补齐。

### 班级新增建议

- `enrollment_year`
- `expected_graduation_year`
- `program_years`
- `owner_role`
- `owner_user_pk`
- `scope_level`
- `archived_at`
- `updated_at`

人数不入库，由 `students` 聚合计算。

### 教材新增建议

- `owner_role`
- `owner_user_pk`
- `scope_level`
- `school_code`
- `school_name`
- `college`
- `department`
- `published_at`
- `archived_at`
- `deleted_at`

附件内容不要直接进数据库，只存安全路径、hash、大小、MIME 和显示名。

### 试卷版本建议

- 新增 `exam_paper_versions`，保存 `paper_id`、`version_number`、`title`、`description`、`questions_json`、`exam_config_json`、`created_by_teacher_id`、`created_at`。
- `assignments` 保存 `exam_paper_version_id` 或版本快照，避免试卷内容修改影响历史提交。
- 在版本化未完成前，已产生提交的试卷内容编辑必须被阻止。

### 审计建议

新增轻量 `resource_change_events` 或复用现有后台任务/日志：

- `resource_type`
- `resource_id`
- `mode`: `attributes` 或 `content`
- `action`
- `actor_role`
- `actor_id`
- `before_summary`
- `after_summary`
- `created_at`

不要记录学生隐私正文、试题答案全文、密码、token 或附件原文。

## 权限目标

需要把资源权限拆成至少四类：

- `can_view_attributes`
- `can_edit_attributes`
- `can_view_content`
- `can_edit_content`

再按业务补充：

- `can_assign`
- `can_delete`
- `can_archive`
- `can_transfer_owner`
- `can_run_ai`
- `can_download`

不能再只用单一 `can_access` 混合所有语义。现有 `can_manage` 可以保留作为兼容字段，但新增 UI 和接口必须使用更明确的能力字段。

## 实施阶段目标

### 阶段 1：目标和矩阵落地

- [ ] 完成本文件并经确认。
- [ ] 为五类资源补一张当前字段到目标字段的差异表。
- [ ] 明确哪些字段本期只展示、哪些字段本期可编辑、哪些字段需要新迁移。
- [ ] 明确已引用资源的阻止/确认/版本化策略。

### 阶段 2：权限服务扩展

- [ ] 在 `resource_access_service.py` 增加属性/内容分离的权限 helper。
- [ ] 保留旧接口兼容，但把旧 helper 改成新 helper 的薄封装。
- [ ] 覆盖多组织教师、同校只读、同系复用、超管、学生课堂分配等场景。

### 阶段 3：后端接口拆分

- [ ] 班级属性接口和学生内容接口分离。
- [ ] 课程属性接口和课次内容接口分离。
- [ ] 教材属性接口和简介/目录/附件内容接口分离。
- [ ] 试卷属性接口和题目/评分内容接口分离，并完成已分配保护。
- [ ] 材料属性接口和文件正文/AI/Git 内容接口分离。

### 阶段 4：前端页面改造

- [ ] 五类资源页面都展示属性/内容双模式入口。
- [ ] 非管理者只能进入可查看模式，不能看到误导性编辑按钮。
- [ ] 已引用资源展示影响提示。
- [ ] 保存失败、权限不足、长任务失败有清晰反馈。

### 阶段 5：数据安全和验收

- [ ] 所有迁移先在 `.codex-temp` 复制库 dry run。
- [ ] 无权写请求验证无数据库、文件、AI 任务副作用。
- [ ] 受保护页面用临时数据根浏览器验证。
- [ ] 不执行远程部署，除非用户另行明确要求。

## 测试计划

### 单元测试

建议新增或扩展：

- `tests/test_resource_dual_mode_permissions.py`
- `tests/test_manage_class_attribute_content_modes.py`
- `tests/test_manage_course_attribute_content_modes.py`
- `tests/test_manage_textbook_attribute_content_modes.py`
- `tests/test_exam_paper_attribute_content_modes.py`
- `tests/test_material_attribute_content_modes.py`

预期结果：

- owner 可以查看和编辑属性、内容。
- 同校或同系共享教师可以查看允许范围内属性和内容预览，但不能编辑内容。
- 可分配资源时，必须同时具备资源可用和目标课堂可管理。
- 无权写请求返回 403 或兼容的无权响应。
- 无权写请求后，相关表记录数、文件 hash、AI 任务队列数量不变。

### 集成测试

建议命令：

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_permission_resource_access tests.test_permission_materials_service tests.test_permission_course_files tests.test_permission_homework_routes tests.test_exam_paper_scope_access
```

预期结果：

- 现有权限回归全部通过。
- 新增双模式权限不削弱 P01 权限边界。
- 旧客户端仍能读取必要字段，但新增能力字段更明确。

### 前端类型和构建

建议命令：

```powershell
npm run typecheck
npm test
npm run build
```

预期结果：

- 类型检查通过。
- 前端测试通过。
- Vite build 成功。
- 无因双模式改造导致的 island payload 破坏。

### 浏览器验收

使用 `.codex-temp` 复制数据库和临时数据根，验证：

- `/manage/classes`：属性入口和学生内容入口分离。
- `/manage/courses`：课程属性和课次模板分离。
- `/manage/textbooks`：书目信息和简介/目录/附件分离。
- `/manage/exams`：试卷属性、分配、题目内容分离。
- `/manage/materials`：材料属性、课堂分配、文件内容、AI 操作分离。

预期结果：

- 文字不重叠，按钮状态不误导。
- `can_edit_attributes=false` 时不能保存属性。
- `can_edit_content=false` 时不能保存内容。
- 伪造请求被后端拒绝。
- 页面加载不会自动触发写库或 AI 任务。

## 验收条件

本目标只有在以下条件全部满足时才能验收：

- [ ] 五类资源都能清晰区分属性查看、属性编辑、内容查看、内容编辑。
- [ ] 每类资源都有字段级边界说明，且实现和说明一致。
- [ ] 后端接口在写副作用之前完成权限校验。
- [ ] 共享可见/可用不扩大为编辑/删除/发布/改分权限。
- [ ] 已被引用资源不会因内容编辑破坏历史课堂、学生提交或材料下载。
- [ ] 已分配试卷的内容修改有阻止或版本化策略。
- [ ] 删除动作不会损坏线上历史数据。
- [ ] 所有新增字段迁移通过复制库 dry run。
- [ ] 单元、集成、前端、浏览器验收都通过。
- [ ] 未触碰线上数据，未泄露敏感信息。

## 明确失败条件

出现以下任意情况，本目标不得验收：

- 同校或同系教师能编辑、删除、发布、改分他人资源。
- 学生可通过直接 URL 访问未分配材料、其他班级资源或其他学生提交。
- 属性编辑接口改写了内容正文。
- 内容编辑接口静默改变了 owner、scope 或组织归属。
- 无权请求虽然返回错误，但已经写库、写文件、入队 AI 任务或删除文件。
- 已有提交的试卷被原地改题，历史评分标准被覆盖。
- 删除班级、课程、教材、试卷、材料时影响课堂、作业、提交、成绩或学生历史记录。
- 为了通过测试而放宽权限、跳过测试或扩大超管以外角色权限。
- 在未明确授权的情况下运行远程部署或修改线上数据。

## 跟进记录

| 日期 | 负责人 | 进展 | 证据/命令 | 结论 |
| --- | --- | --- | --- | --- |
| 2026-06-05 | Codex | 建立基础资源双编辑模式目标文档 | `target/target02/P01/README.md` | 待确认和后续实现 |
