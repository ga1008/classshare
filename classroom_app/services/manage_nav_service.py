from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


# 六域按教师的任务生命周期排列（docs/manage-center-improvement-plan-2026-09-11.md §5.2）：
# 首页 → 教学（课堂怎么运行）→ 资源库（东西在哪）→ 成绩与归档（材料做到第几步）
# → 教务（教务给我安排了什么）→ 我的（我自己）；超管另有「平台」域追加在最后。
MANAGE_DOMAIN_ORDER = ("home", "teaching", "library", "archive", "academic", "me")
MANAGE_ADMIN_DOMAIN = "admin"
MANAGE_HOME_DOMAIN = "home"
MANAGE_LIBRARY_DOMAIN = "library"
MANAGE_ARCHIVE_DOMAIN = "archive"
MANAGE_ME_DOMAIN = "me"

# 域身份只用一个语义色令牌表达（tone = ui-system.src.css 中 --ls-* 的名字），
# 具体色值由 CSS `.manage-layout[data-manage-domain=...]` 映射到 --ls-domain-accent。
MANAGE_DOMAIN_META: dict[str, dict[str, str]] = {
    MANAGE_HOME_DOMAIN: {
        "label": "首页",
        "short_label": "首页",
        "title": "教师首页",
        "description": "今天要做什么、我的课堂怎么样。",
        "tone": "primary",
    },
    "teaching": {
        "label": "教学",
        "short_label": "教学",
        "title": "教学域",
        "description": "课堂怎么运行、怎么开课、教谁。",
        "tone": "primary",
    },
    MANAGE_LIBRARY_DOMAIN: {
        "label": "资源库",
        "short_label": "资源",
        "title": "资源库",
        "description": "课程、教材、试卷、学习文档、教案、投票，以及跨类别的材料检索。",
        "tone": "indigo",
    },
    MANAGE_ARCHIVE_DOMAIN: {
        "label": "成绩与归档",
        "short_label": "归档",
        "title": "成绩与归档",
        "description": "按学期末流程排列：命题与考核 → 成绩链 → 教务归档 → 课后材料。",
        "tone": "warning",
    },
    "academic": {
        "label": "教务",
        "short_label": "教务",
        "title": "教务域",
        "description": "教务给我安排了什么：日程、对接同步、教室与公文。",
        "tone": "info",
    },
    MANAGE_ME_DOMAIN: {
        "label": "我的",
        "short_label": "我的",
        "title": "我的",
        "description": "个人资料、签名、凭据与账号安全，以及等我处理的申请。",
        "tone": "slate",
    },
    MANAGE_ADMIN_DOMAIN: {
        "label": "平台",
        "short_label": "平台",
        "title": "平台管理",
        "description": "超管维护用户、组织、反馈、AI 用量、监控与诊断。",
        "tone": "rose",
    },
}


# 材料检索页的分类多选（页内 chips，不再占侧栏）。
# key 与 material_hub_service 的检索器一一对应；此处保持零依赖，供导航渲染使用。
MATERIAL_HUB_CATEGORIES: tuple[dict[str, str], ...] = (
    {"key": "learning_docs", "label": "学习文档", "hint": "课程材料库中上课使用的学习文档与文件夹"},
    {"key": "postclass", "label": "课后材料", "hint": "课堂生成与上传解析归档的课后材料包"},
    {"key": "lesson_plans", "label": "教案", "hint": "整学期教案（封面 + 每课次表格）"},
    {"key": "assessment_plans", "label": "考核计划表", "hint": "课程考核计划表"},
    {"key": "grading_rubrics", "label": "评分细则表", "hint": "课程考核评分细则"},
    {"key": "ordinary_grade_records", "label": "平时成绩表", "hint": "学生平时成绩记录表"},
    {"key": "exam_grade_records", "label": "考核登分表", "hint": "期末考核登分表"},
    {"key": "final_grade_transcripts", "label": "期末成绩单", "hint": "学生成绩录入模板"},
    {"key": "teacher_evaluations", "label": "教师评学表", "hint": "教师评学表（10 项指标）"},
    {"key": "academic_grade_registers", "label": "成绩登记表", "hint": "教务期末成绩登记表"},
    {"key": "academic_exam_analyses", "label": "试卷分析表", "hint": "教务试卷分析表"},
    {"key": "exam_papers", "label": "试卷", "hint": "教师试卷库"},
    {"key": "textbooks", "label": "教材", "hint": "课程教材与参考书"},
    {"key": "gongwen", "label": "公文", "hint": "校园公文通同步的公文"},
)


@dataclass(frozen=True)
class ManageNavItem:
    key: str
    domain: str
    group: str
    label: str
    icon: str
    href: str
    search_text: str
    ai_hint: str
    help_text: str = ""
    nav_note: str = ""
    nav_badge: str = ""
    required_flag: str = ""
    legacy_hrefs: tuple[str, ...] = ()
    # 成绩与归档域的流程步号（1..9），供页头「第 k/9 步」与侧栏排序使用。
    step: int = 0


MANAGE_NAV_ITEMS: tuple[ManageNavItem, ...] = (
    # ── 首页 ─────────────────────────────────────────────────────────
    ManageNavItem(
        key="home",
        domain=MANAGE_HOME_DOMAIN,
        group="首页",
        label="首页",
        icon="gauge",
        href="/dashboard",
        search_text="首页 仪表盘 今天 需要处理 我的课堂 dashboard home",
        ai_hint="首页：今天需要处理的事项、我的课堂与各域入口。",
    ),
    # ── 教学 ─────────────────────────────────────────────────────────
    ManageNavItem(
        key="offering_hub",
        domain="teaching",
        group="课堂运行",
        label="课堂管理",
        icon="presentation",
        href="/manage/teaching/classroom-hub",
        search_text="课堂 课堂管理 运行 进度 教材 AI offering hub 教学首页",
        ai_hint="课堂管理：教学域首页——集中查看与治理本学期全部课堂：授课进度、下次课、教材/AI 配置缺口、活动资产与本周课次日程。",
    ),
    ManageNavItem(
        key="offering_merge",
        domain="teaching",
        group="课堂运行",
        label="课堂合并",
        icon="link",
        href="/manage/teaching/offering-merge",
        search_text="课堂合并 双开 合班 merge offering",
        ai_hint="课堂合并：检测同课程同学期的疑似双开课堂，预检后把数据迁入主课堂并挂为合班（不可逆，有快照兜底）。",
    ),
    ManageNavItem(
        key="workflow",
        domain="teaching",
        group="开课准备",
        label="开课向导",
        icon="workflow",
        href="/manage/teaching/workflow",
        search_text="教学 工作台 流程 开课向导 workflow",
        ai_hint="开课向导：按开课流程检查学期、课程、班级、教材、材料和 AI 助教配置。",
    ),
    ManageNavItem(
        key="semesters",
        domain="teaching",
        group="开课准备",
        label="确认学期",
        icon="calendar",
        href="/manage/teaching/semesters",
        search_text="确认学期 学期 校历 semester calendar",
        ai_hint="确认学期：维护学期区间、周次规则与校历，供开课和课堂排期使用。",
        legacy_hrefs=("/manage/semesters",),
    ),
    ManageNavItem(
        key="offerings",
        domain="teaching",
        group="开课准备",
        label="开设课堂",
        icon="plus",
        href="/manage/teaching/offerings",
        search_text="开设课堂 课堂 offering 班级 课程 教材",
        ai_hint="开设课堂：把学期、班级、课程、教材和排课信息组合成可进入的课堂。",
        legacy_hrefs=("/manage/offerings",),
    ),
    ManageNavItem(
        key="ai",
        domain="teaching",
        group="开课准备",
        label="配置 AI 助教",
        icon="bot",
        href="/manage/teaching/ai",
        search_text="配置 AI 助教 人工智能 ai prompt",
        ai_hint="配置 AI 助教：为具体课堂维护提示词、教材与知识依据。",
        legacy_hrefs=("/manage/ai",),
    ),
    ManageNavItem(
        key="classes",
        domain="teaching",
        group="教学对象",
        label="班级",
        icon="users",
        href="/manage/teaching/classes",
        search_text="班级 学生 名册 class roster",
        ai_hint="班级：维护教学对象、学生名单、组织归属和课堂可用班级。",
        legacy_hrefs=("/manage/classes",),
    ),
    # ── 资源库 ───────────────────────────────────────────────────────
    ManageNavItem(
        key="material_hub",
        domain=MANAGE_LIBRARY_DOMAIN,
        group="检索",
        label="材料检索",
        icon="search",
        href="/manage/library",
        search_text="材料中心 材料检索 全部材料 搜索 AI 搜索 公文 教案 试卷 教材 library hub search",
        ai_hint="材料检索：资源库首页——按分类勾选（学习文档/课后材料/教案/过程材料/期末材料/试卷/教材/公文等），支持模糊搜索标题、内容、属性、标签、归属人与归属层级，也可让 AI 理解需求后筛选。",
    ),
    ManageNavItem(
        key="courses",
        domain=MANAGE_LIBRARY_DOMAIN,
        group="教学资源",
        label="课程",
        icon="book-open",
        href="/manage/library/courses",
        search_text="课程 模板 课次 course lesson",
        ai_hint="课程：维护课程模板、简介、学时学分和课次结构。",
        legacy_hrefs=("/manage/courses", "/manage/teaching/courses"),
    ),
    ManageNavItem(
        key="textbooks",
        domain=MANAGE_LIBRARY_DOMAIN,
        group="教学资源",
        label="教材",
        icon="book",
        href="/manage/library/textbooks",
        search_text="教材 textbook 参考书",
        ai_hint="教材：维护课程教材与附件，供开课和 AI 助教引用。",
        legacy_hrefs=("/manage/textbooks", "/manage/teaching/textbooks"),
    ),
    ManageNavItem(
        key="exams",
        domain=MANAGE_LIBRARY_DOMAIN,
        group="教学资源",
        label="试卷",
        icon="file-text",
        href="/manage/library/exams",
        search_text="试卷 题库 考试 exam paper",
        ai_hint="试卷：管理教师试卷库、题目、考试配置与分配入口。",
        legacy_hrefs=("/manage/exams", "/manage/teaching/exams"),
    ),
    ManageNavItem(
        key="materials",
        domain=MANAGE_LIBRARY_DOMAIN,
        group="教学资源",
        label="学习文档",
        icon="folder",
        href="/manage/library/materials",
        search_text="材料 学习文档 资料 文件 course material learning HTML 包",
        ai_hint="学习文档：整理上课使用的学习文档、HTML 包与文件夹；课堂生成或上传解析的课后材料请到成绩与归档的「课后材料」。",
        legacy_hrefs=("/manage/materials", "/manage/teaching/materials"),
    ),
    ManageNavItem(
        key="lesson_plans",
        domain=MANAGE_LIBRARY_DOMAIN,
        group="教学资源",
        label="教案",
        icon="file-text",
        href="/manage/library/lesson-plans",
        search_text="教案 备课 教学设计 lesson plan teaching",
        ai_hint="教案：空白新建 / 按课堂一键生成整学期教案 / 导入文件解析，支持渲染预览、导出 Word、系部院校级公开与一键继承。",
        legacy_hrefs=("/manage/lesson-plans", "/manage/teaching/lesson-plans"),
    ),
    ManageNavItem(
        key="polls",
        domain=MANAGE_LIBRARY_DOMAIN,
        group="教学资源",
        label="投票",
        icon="bar-chart",
        href="/manage/library/polls",
        search_text="投票 表决 调查 vote poll survey",
        ai_hint="投票：创建跨班级共享的投票活动，分配到一个或多个班级并查看统计。",
        legacy_hrefs=("/manage/polls", "/manage/teaching/polls"),
    ),
    # ── 成绩与归档（按学期末流程排步） ───────────────────────────────
    ManageNavItem(
        key="assessment_plans",
        domain=MANAGE_ARCHIVE_DOMAIN,
        group="命题与考核",
        label="考核计划表",
        icon="clipboard-list",
        href="/manage/archive/assessment-plans",
        search_text="考核计划表 过程材料 命题 考核 评分 归档 assessment plan",
        ai_hint="考核计划表：空白新建 / 完整表单填写（可按课堂自动带入）/ 按课堂深度思考生成 / 导入解析（自动归集签名到签名库并去重），支持渲染预览、导出 Word、系部院校级公开与一键继承。",
        nav_note="可空白/课堂生成/导入，导出 Word/PDF",
        nav_badge="100分校验",
        legacy_hrefs=("/manage/assessment-plans", "/manage/teaching/assessment-plans"),
        step=1,
    ),
    ManageNavItem(
        key="grading_rubrics",
        domain=MANAGE_ARCHIVE_DOMAIN,
        group="命题与考核",
        label="评分细则表",
        icon="clipboard-list",
        href="/manage/archive/grading-rubrics",
        search_text="评分细则 评分标准 课程考核评分细则 过程材料 命题 归档 rubric",
        ai_hint="评分细则：从材料库入口关联具体试卷或题目附件生成，逐题给出评分标准、扣分项、例外情况和截图/提交物要求，导出为官方模板 Word。",
        nav_note="依赖具体试卷或题目来源，导出 Word/PDF",
        nav_badge="需试卷",
        legacy_hrefs=("/manage/teaching/grading-rubrics",),
        step=2,
    ),
    ManageNavItem(
        key="ordinary_grade_records",
        domain=MANAGE_ARCHIVE_DOMAIN,
        group="成绩链",
        label="平时成绩表",
        icon="clipboard-list",
        href="/manage/archive/ordinary-grade-records",
        search_text="平时成绩表 平时成绩记录表 学生平时成绩记录表 过程材料 归档 Excel XLSX ordinary grade record",
        ai_hint="平时成绩表：集中管理由课堂考勤、作业与测评生成或从 Excel 导入解析的学生平时成绩记录表，支持导出 Excel、开放范围与课堂材料归档。",
        nav_note="从课堂数据生成，或导入学校模板 Excel",
        nav_badge="Excel",
        legacy_hrefs=("/manage/teaching/ordinary-grade-records",),
        step=3,
    ),
    ManageNavItem(
        key="exam_grade_records",
        domain=MANAGE_ARCHIVE_DOMAIN,
        group="成绩链",
        label="考核登分表",
        icon="clipboard-list",
        href="/manage/archive/exam-grade-records",
        search_text="考核登分表 期末考试登分表 机试作品设计 过程材料 归档 Excel XLSX exam grade record",
        ai_hint="考核登分表：集中管理由课堂考试生成或从 Excel 导入解析的期末考核登分表，按大题列与总分校验保存，支持导出 Excel 和开放范围。",
        nav_note="从已绑定试卷的考试生成，或导入 Excel",
        nav_badge="Excel",
        legacy_hrefs=("/manage/teaching/exam-grade-records",),
        step=4,
    ),
    ManageNavItem(
        key="final_grade_transcripts",
        domain=MANAGE_ARCHIVE_DOMAIN,
        group="成绩链",
        label="期末成绩单",
        icon="clipboard-list",
        href="/manage/archive/final-grade-transcripts",
        search_text="期末成绩单 学生成绩录入模板 过程材料 归档 Excel XLSX final grade transcript",
        ai_hint="期末成绩单：先即时同步教务考试名单，再按学年学期、班级、课程、学号和姓名关联平时成绩表与考核登分表，保持教务名单原始顺序生成 Excel。",
        nav_note="同步教务考试名单，关联平时与期末成绩",
        nav_badge="Excel",
        legacy_hrefs=("/manage/teaching/final-grade-transcripts",),
        step=5,
    ),
    ManageNavItem(
        key="academic_grade_registers",
        domain=MANAGE_ARCHIVE_DOMAIN,
        group="教务归档",
        label="成绩登记表",
        icon="file-text",
        href="/manage/archive/academic-grade-registers",
        search_text="期末材料 成绩登记表 教务系统 成绩同步 Word DOC 归档",
        ai_hint="成绩登记表：选择已有课堂后一次访问教务系统，同步并校验成绩登记表与试卷分析表；自动绑定本人签名，支持渲染预览与 Word/PDF 下载。",
        nav_note="教务双表一次同步，成绩交叉复算",
        nav_badge="教务同步",
        legacy_hrefs=("/manage/teaching/academic-grade-registers",),
        step=6,
    ),
    ManageNavItem(
        key="academic_exam_analyses",
        domain=MANAGE_ARCHIVE_DOMAIN,
        group="教务归档",
        label="试卷分析表",
        icon="bar-chart",
        href="/manage/archive/academic-exam-analyses",
        search_text="期末材料 试卷分析表 教务系统 成绩分布 AI 教学分析 审核意见 签名",
        ai_hint="试卷分析表：复用同一次教务双表同步结果，自动统计成绩分布并由思考型 AI 撰写教学分析；可补充考试属性、选择签名并导出正式 Word/PDF。",
        nav_note="AI 分析、公共同意签章、签名库",
        nav_badge="AI分析",
        legacy_hrefs=("/manage/teaching/academic-exam-analyses",),
        step=7,
    ),
    ManageNavItem(
        key="teacher_evaluations",
        domain=MANAGE_ARCHIVE_DOMAIN,
        group="教务归档",
        label="教师评学表",
        icon="clipboard-list",
        href="/manage/archive/teacher-evaluations",
        search_text="教师评学表 评学表 学生评价 综合评价 过程材料 归档 teacher evaluation",
        ai_hint="教师评学表：空白新建 / 完整表单填写（可按课堂自动带入）/ 按教学班级用快速 AI 归集全学期表现自动评分并撰写学习情况分析 / 导入文件解析，10 项指标合计 100、总分自动计算综合评价，支持渲染预览、导出与原版一致的 Word、系部院校级公开与一键继承。",
        nav_note="按班级生成/导入解析，补全后导出 Word/PDF",
        nav_badge="10项评分",
        legacy_hrefs=("/manage/teacher-evaluations", "/manage/teaching/teacher-evaluations"),
        step=8,
    ),
    ManageNavItem(
        key="postclass_materials",
        domain=MANAGE_ARCHIVE_DOMAIN,
        group="课后归档",
        label="课后材料",
        icon="folder",
        href="/manage/archive/postclass-materials",
        search_text="课后材料 课堂生成 上传解析 AI解析 导入 归档 postclass archive",
        ai_hint="课后材料：集中查看课堂结束后生成的材料与上传解析（AI解析/导入）的材料包，与上课用的学习文档分开管理。",
        nav_note="课堂生成 + 上传解析归档",
        legacy_hrefs=("/manage/teaching/postclass-materials",),
        step=9,
    ),
    # ── 教务 ─────────────────────────────────────────────────────────
    ManageNavItem(
        key="academic_overview",
        domain="academic",
        group="日程",
        label="教务总览",
        icon="gauge",
        href="/manage/academic",
        search_text="教务 总览 课表 考试 监考 academic overview 日程",
        ai_hint="教务总览：教务域首页——查看教务同步、监考考试提醒、教室和公文的聚合入口。",
    ),
    ManageNavItem(
        key="course_schedule",
        domain="academic",
        group="日程",
        label="课时统计",
        icon="calendar",
        href="/manage/academic/course-schedule",
        search_text="课时统计 课程表 课表 周课表 课时 schedule timetable hours",
        ai_hint="课时统计：同步智慧课堂教师课程表，按学年学期、课程、班级查询，3D 周课表展示并统计课程课时与学期课时。",
        legacy_hrefs=("/manage/teaching/course-schedule",),
    ),
    ManageNavItem(
        key="system_academic_integrations",
        domain="academic",
        group="数据同步",
        label="教务对接",
        icon="id-card",
        href="/manage/academic/integrations",
        search_text="教务对接 教务系统 课表 考务 名册 academic",
        ai_hint="教务对接：同步课表、考务、监考和名册等学校教务数据。",
        legacy_hrefs=("/manage/system/academic-integrations",),
    ),
    ManageNavItem(
        key="system_smart_classroom_integrations",
        domain="academic",
        group="数据同步",
        label="智慧课堂",
        icon="bar-chart",
        href="/manage/academic/smart-classroom",
        search_text="智慧课堂 点名 签到 smart classroom attendance",
        ai_hint="智慧课堂：配置智慧课堂点名、签到和课堂考勤同步能力。",
        legacy_hrefs=("/manage/system/smart-classroom-integrations", "/manage/teaching/smart-classroom-integrations"),
    ),
    ManageNavItem(
        key="classrooms",
        domain="academic",
        group="场地",
        label="教室查询",
        icon="building",
        href="/manage/academic/classrooms",
        search_text="教室查询 教室 教学场地 空闲教室 classroom room",
        ai_hint="教室查询：查询教学场地、同步教室数据并筛选空闲教室。",
        legacy_hrefs=("/manage/classrooms",),
    ),
    ManageNavItem(
        key="gongwen",
        domain="academic",
        group="公文",
        label="公文列表",
        icon="file-text",
        href="/manage/academic/gongwen",
        search_text="公文 通知 文件 红头文件 gongwen document",
        ai_hint="公文列表：检索学校和学院公文、查看正文与附件、处理关注命中。",
        legacy_hrefs=("/manage/gongwen",),
    ),
    ManageNavItem(
        key="system_gongwen_integrations",
        domain="academic",
        group="公文",
        label="公文同步",
        icon="refresh",
        href="/manage/academic/gongwen-sync",
        search_text="公文同步 校园公文通 统一认证 gongwen sync",
        ai_hint="公文同步：配置校园公文通并触发公文同步。",
        legacy_hrefs=("/manage/system/gongwen-integrations",),
    ),
    # ── 我的 ─────────────────────────────────────────────────────────
    ManageNavItem(
        key="teacher_profile",
        domain=MANAGE_ME_DOMAIN,
        group="个人",
        label="我的概览",
        icon="user",
        href="/manage/me",
        search_text="我的概览 个人中心 资料 profile me",
        ai_hint="我的概览：查看教师个人资料完整度、通知、私信和常用个人入口。",
    ),
    ManageNavItem(
        key="work_inbox",
        domain=MANAGE_ME_DOMAIN,
        group="个人",
        label="待我处理",
        icon="inbox",
        href="/manage/me/inbox",
        search_text="待我处理 收件箱 待办 审批 批改 申请 inbox todo",
        ai_hint="待我处理：首页「需要处理」的完整清单——批改、审批申请、签名申请、找回申请、课堂配置缺口、教务日程与个人待办，按来源筛选。",
    ),
    ManageNavItem(
        key="signatures",
        domain=MANAGE_ME_DOMAIN,
        group="签名",
        label="我的签名",
        icon="pen",
        href="/manage/me/signatures",
        search_text="签名 电子签名 signature",
        ai_hint="我的签名：维护教师个人电子签名，供导出、审批和签章场景使用。",
        legacy_hrefs=("/manage/signatures",),
    ),
    ManageNavItem(
        key="signature_workflows",
        domain=MANAGE_ME_DOMAIN,
        group="签名",
        label="签名审批与使用",
        icon="check",
        href="/manage/me/signature-workflows",
        search_text="材料 签名 审批 申请 批准 使用",
        ai_hint="查看申请材料原样预览，批准或拒绝签名，跟踪申请和实际应用状态。",
    ),
    ManageNavItem(
        key="teacher_credentials",
        domain=MANAGE_ME_DOMAIN,
        group="账号与安全",
        label="对接凭据",
        icon="link",
        href="/manage/me/credentials",
        search_text="对接凭据 教务 智慧课堂 公文通 账号 credential",
        ai_hint="对接凭据：集中查看教师个人教务、智慧课堂和公文通账号凭据状态。",
    ),
    ManageNavItem(
        key="system_password_resets",
        domain=MANAGE_ME_DOMAIN,
        group="账号与安全",
        label="账号找回",
        icon="lock",
        href="/manage/me/password-resets",
        search_text="账号找回 找回申请 密码 学生 password reset",
        ai_hint="账号找回：教师审核和处理自己班级学生的账号找回事务。",
        legacy_hrefs=("/manage/system/password-resets",),
    ),
    # ── 平台（超管） ─────────────────────────────────────────────────
    ManageNavItem(
        key="system_users",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="用户管理",
        icon="users",
        href="/manage/system/users",
        search_text="用户管理 教师账号 user admin",
        ai_hint="用户管理：超管教师维护教师账号和平台用户状态。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_organizations",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="学校组织",
        icon="building",
        href="/manage/system/organizations",
        search_text="学校组织 学院 系部 organization",
        ai_hint="学校组织：超管教师维护学校、学院和系部组织目录。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="life_tips",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="一言提示",
        icon="message-circle",
        href="/manage/system/life-tips",
        search_text="一言 提示 人生提示 登录提示 life tip loading",
        ai_hint="一言提示：管理学生/教师登录加载屏上的一句话提示——审核 AI 从公文挖掘的条目、手工新增本校/本系提示、下架劣句、查看有用/无感反馈。",
        required_flag="super_admin",
        legacy_hrefs=("/manage/teaching/life-tips",),
    ),
    ManageNavItem(
        key="system_feedback",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="问题反馈",
        icon="file-text",
        href="/manage/system/feedback",
        search_text="问题反馈 feedback",
        ai_hint="问题反馈：超管教师查看和处理全站用户反馈。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_blog_crawler",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="博客管家",
        icon="bot",
        href="/manage/system/blog-crawler",
        search_text="博客管家 AI博客管家 爬虫 新闻 blog crawler",
        ai_hint="博客管家：超管教师用 AI 维护新闻爬取、摘要和博客发布队列。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_ai_usage",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="AI 用量",
        icon="line-chart",
        href="/manage/system/ai-usage",
        search_text="AI 用量 预算 成本 usage budget",
        ai_hint="AI 用量：超管教师查看 AI 预算、成本和使用趋势。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_agent_keys",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="Agent Key",
        icon="key",
        href="/manage/system/agent-keys",
        search_text="Agent Key 密钥 token",
        ai_hint="Agent Key：超管教师维护 Agent 运行时密钥。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_monitor",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="监控大屏",
        icon="gauge",
        href="/manage/system/monitor",
        search_text="监控大屏 服务器监控 进程 内存优化 CPU 访问量 连接 monitor dashboard",
        ai_hint="监控大屏：超管实时查看服务器 CPU/内存/磁盘、进程树管理、内存一键优化、平台访问量与连接丢失统计，可调用快速 AI 解读运行状况。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_diagnostics",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="压测诊断",
        icon="activity",
        href="/manage/system/diagnostics",
        search_text="压测诊断 性能 diagnostics",
        ai_hint="压测诊断：超管教师查看运行健康、压测入口和后台任务状态。",
        required_flag="super_admin",
    ),
)


_NAV_ITEMS_BY_KEY = {item.key: item for item in MANAGE_NAV_ITEMS}
_LEGACY_KEY_ALIASES: dict[str, str] = {
    "system_super_admin": "system_users",
}

# 每个域的首页条目（域标题点击直达；也是 dashboard 域卡的主链接）。
MANAGE_DOMAIN_HOME_KEYS: dict[str, str] = {
    MANAGE_HOME_DOMAIN: "home",
    "teaching": "offering_hub",
    MANAGE_LIBRARY_DOMAIN: "material_hub",
    MANAGE_ARCHIVE_DOMAIN: "assessment_plans",
    "academic": "academic_overview",
    MANAGE_ME_DOMAIN: "teacher_profile",
    MANAGE_ADMIN_DOMAIN: "system_users",
}

ARCHIVE_STEP_TOTAL = max(item.step for item in MANAGE_NAV_ITEMS if item.domain == MANAGE_ARCHIVE_DOMAIN)


def normalize_manage_nav_key(active_key: Any) -> str:
    key = str(active_key or "").strip()
    return _LEGACY_KEY_ALIASES.get(key, key)


def get_manage_nav_item(key: Any) -> ManageNavItem | None:
    return _NAV_ITEMS_BY_KEY.get(normalize_manage_nav_key(key))


def canonical_manage_href(key: str, fallback: str = "/manage/teaching/classroom-hub") -> str:
    item = get_manage_nav_item(key)
    return item.href if item else fallback


def iter_archive_steps() -> list[ManageNavItem]:
    """成绩与归档域的九步，按流程顺序。"""
    return sorted((item for item in MANAGE_NAV_ITEMS if item.domain == MANAGE_ARCHIVE_DOMAIN), key=lambda item: item.step)


def _can_view_item(item: ManageNavItem, *, is_super_admin: bool) -> bool:
    if item.required_flag == "super_admin":
        return is_super_admin
    return True


def _fallback_help_text(item: ManageNavItem) -> str:
    if item.help_text:
        return item.help_text
    hint = item.ai_hint or ""
    # ai_hint keeps a "标签：" prefix for AI context; the popover already shows
    # the label as its title, so strip the duplicate.
    prefix = f"{item.label}："
    base = hint[len(prefix):] if hint.startswith(prefix) else hint
    # The sidebar renders titles only; workflow notes surface in the popover.
    note = (item.nav_note or "").strip()
    if note and note not in base:
        return f"{base}（{note}）" if base else note
    return base


def _item_to_template_dict(item: ManageNavItem, *, active_key: str) -> dict[str, Any]:
    meta = MANAGE_DOMAIN_META[item.domain]
    search_text = " ".join(
        part.strip()
        for part in (item.search_text, item.nav_note, item.nav_badge)
        if str(part or "").strip()
    )
    return {
        "key": item.key,
        "domain": item.domain,
        "domain_label": meta["label"],
        "group": item.group,
        "label": item.label,
        "icon": item.icon,
        "href": item.href,
        "search_text": search_text,
        "ai_hint": item.ai_hint,
        "help_text": _fallback_help_text(item),
        "nav_note": item.nav_note,
        "nav_badge": item.nav_badge,
        "required_flag": item.required_flag,
        "legacy_hrefs": list(item.legacy_hrefs),
        "step": item.step,
        "active": item.key == active_key,
    }


def _group_template_items(items: Iterable[ManageNavItem], *, active_key: str) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_group: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_group.setdefault(item.group, []).append(_item_to_template_dict(item, active_key=active_key))
    for group, group_items in by_group.items():
        groups.append({
            "label": group,
            "items": group_items,
            "active": any(item["active"] for item in group_items),
        })
    return groups


def build_manage_nav(
    user: dict[str, Any] | None,
    active_key: str,
    *,
    is_super_admin: bool = False,
) -> dict[str, Any]:
    del user
    normalized_active_key = normalize_manage_nav_key(active_key)
    active_item = get_manage_nav_item(normalized_active_key)
    active_domain = active_item.domain if active_item else "teaching"

    visible_items = [
        item
        for item in MANAGE_NAV_ITEMS
        if _can_view_item(item, is_super_admin=is_super_admin)
    ]
    hrefs = {item.key: item.href for item in visible_items}

    # 平台域只对超管出现；普通教师看到六个域。
    domain_order = list(MANAGE_DOMAIN_ORDER)
    admin_items = [item for item in visible_items if item.domain == MANAGE_ADMIN_DOMAIN]
    if admin_items:
        domain_order.append(MANAGE_ADMIN_DOMAIN)

    domains = []
    for domain_key in domain_order:
        domain_items = [item for item in visible_items if item.domain == domain_key]
        meta = MANAGE_DOMAIN_META[domain_key]
        home_item = get_manage_nav_item(MANAGE_DOMAIN_HOME_KEYS.get(domain_key, ""))
        first_href = home_item.href if home_item else (domain_items[0].href if domain_items else "#")
        groups = _group_template_items(domain_items, active_key=normalized_active_key)
        domains.append({
            "key": domain_key,
            **meta,
            "href": first_href,
            "active": domain_key == active_domain,
            "item_count": len(domain_items),
            "groups": groups,
        })

    return {
        "active_key": normalized_active_key,
        "active_domain": active_domain,
        "active_item": _item_to_template_dict(active_item, active_key=normalized_active_key) if active_item else None,
        "domains": domains,
        "hrefs": hrefs,
        "domain_meta": MANAGE_DOMAIN_META,
        "library_categories": [dict(category) for category in MATERIAL_HUB_CATEGORIES],
        "archive_step_total": ARCHIVE_STEP_TOTAL,
    }


def iter_manage_legacy_redirects() -> list[dict[str, str]]:
    redirects: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in MANAGE_NAV_ITEMS:
        for legacy_href in item.legacy_hrefs:
            if not legacy_href or legacy_href == item.href or legacy_href in seen:
                continue
            redirects.append({
                "key": item.key,
                "legacy_href": legacy_href,
                "canonical_href": item.href,
            })
            seen.add(legacy_href)
    return redirects


def iter_platform_manage_routes(*, include_admin: bool = False) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    for item in MANAGE_NAV_ITEMS:
        if item.domain == MANAGE_HOME_DOMAIN:
            # 首页由平台知识的 dashboard 条目单独描述。
            continue
        # 平台维护（超管专属）按权限标记过滤，而不是按域整体排除。
        if item.required_flag == "super_admin" and not include_admin:
            continue
        domain_label = MANAGE_DOMAIN_META[item.domain]["label"]
        routes.append({
            "path": item.href,
            "label": f"教师管理中心 · {domain_label} · {item.group} · {item.label}（{item.ai_hint}）",
            "roles": "teacher",
        })
    return routes


def build_dashboard_domain_cards(*, is_super_admin: bool = False) -> list[dict[str, Any]]:
    """首页「去哪里」域卡：每域一张，带 2–3 个高频入口（摘要在 P1 由服务层补充）。"""
    card_items = {
        "teaching": ("offering_hub", "offerings", "ai"),
        MANAGE_LIBRARY_DOMAIN: ("material_hub", "materials", "exams"),
        MANAGE_ARCHIVE_DOMAIN: ("ordinary_grade_records", "academic_grade_registers", "teacher_evaluations"),
        "academic": ("academic_overview", "system_academic_integrations", "gongwen"),
        MANAGE_ME_DOMAIN: ("teacher_profile", "signatures", "system_password_resets"),
        MANAGE_ADMIN_DOMAIN: ("system_users", "system_feedback", "system_monitor"),
    }
    domain_keys = [key for key in MANAGE_DOMAIN_ORDER if key != MANAGE_HOME_DOMAIN]
    if is_super_admin:
        domain_keys.append(MANAGE_ADMIN_DOMAIN)
    cards: list[dict[str, Any]] = []
    for domain_key in domain_keys:
        meta = MANAGE_DOMAIN_META[domain_key]
        actions = []
        for item_key in card_items.get(domain_key, ()):
            item = get_manage_nav_item(item_key)
            if item:
                actions.append({
                    "label": item.label,
                    "href": item.href,
                    "hint": item.ai_hint,
                })
        home_item = get_manage_nav_item(MANAGE_DOMAIN_HOME_KEYS[domain_key])
        cards.append({
            "domain": domain_key,
            "label": meta["label"],
            "title": meta["title"],
            "description": meta["description"],
            "tone": meta["tone"],
            "href": home_item.href if home_item else (actions[0]["href"] if actions else "/manage/teaching/classroom-hub"),
            "actions": actions,
        })
    return cards
