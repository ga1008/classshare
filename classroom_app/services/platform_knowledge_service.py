"""
平台认知服务 —— 为 AI 对话与 Agent 任务注入「全知」上下文：

- 平台是什么：域名、定位、功能版图
- 平台怎么走：面向用户的路由表（AI 可以直接给出可点击跳转链接）
- 现在是什么时候：精确到分钟的当前时间（含星期 / 时段 / 时节提示）
- 用户是谁：通过查库得到的全量画像（身份、课堂、统计、近况）

所有查询只读、轻量（带 LIMIT），任何单项失败都静默降级，不影响主流程。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .prompt_utils import build_time_context_text

PLATFORM_BASE_URL = "https://guardianangel.net.cn"

# 面向用户的路由表。AI 给跳转建议时直接使用这些路径（站内相对路径即可点击）。
PLATFORM_ROUTES: list[dict[str, str]] = [
    {"path": "/dashboard", "label": "工作台（教师/学生主页，日程、待办、课堂入口）", "roles": "all"},
    {"path": "/classroom/{class_offering_id}", "label": "课堂主页（讨论、材料、作业、考试、互动）", "roles": "all"},
    {"path": "/assignment/{assignment_id}", "label": "作业/考试详情", "roles": "all"},
    {"path": "/exam/take/{assignment_id}", "label": "在线考试答题页", "roles": "student"},
    {"path": "/assignment/{assignment_id}/wrong-summary", "label": "错题汇总", "roles": "all"},
    {"path": "/submission/{submission_id}", "label": "提交详情与批改", "roles": "all"},
    {"path": "/blog", "label": "博客广场（师生文章、新闻播报）", "roles": "all"},
    {"path": "/message-center", "label": "消息中心（通知、私信）", "roles": "all"},
    {"path": "/profile", "label": "个人中心（资料、签名、偏好）", "roles": "all"},
    {"path": "/manage", "label": "教师管理中心首页（工作流导航）", "roles": "teacher"},
    {"path": "/manage/classes", "label": "班级管理", "roles": "teacher"},
    {"path": "/manage/courses", "label": "课程模板管理", "roles": "teacher"},
    {"path": "/manage/offerings", "label": "开课课堂管理", "roles": "teacher"},
    {"path": "/manage/classrooms", "label": "教室管理", "roles": "teacher"},
    {"path": "/manage/semesters", "label": "学期与校历管理", "roles": "teacher"},
    {"path": "/manage/textbooks", "label": "教材管理", "roles": "teacher"},
    {"path": "/manage/exams", "label": "试卷库管理", "roles": "teacher"},
    {"path": "/manage/signatures", "label": "电子签名管理", "roles": "teacher"},
    {"path": "/manage/ai", "label": "课堂 AI 助教配置", "roles": "teacher"},
    {"path": "/manage/gongwen", "label": "公文中心（学校/学院红头文件、通知检索）", "roles": "teacher"},
    {"path": "/manage/system/academic-integrations", "label": "教务系统对接（课表/考务/名册同步）", "roles": "teacher"},
    {"path": "/manage/system/gongwen-integrations", "label": "公文通对接（公文同步设置）", "roles": "teacher"},
    {"path": "/manage/system/smart-classroom-integrations", "label": "智慧教室对接（考勤/签到）", "roles": "teacher"},
    {"path": "/manage/system/agent-keys", "label": "Agent 运行时密钥管理", "roles": "teacher"},
]

PLATFORM_FEATURES_TEXT = (
    "平台功能版图：\n"
    "- 课堂教学：课堂主页（实时讨论室、课时安排、学习文档、课堂互动/举手/弹幕）、学习进度追踪、修仙式成长激励体系。\n"
    "- 作业与考试：作业布置、在线考试、AI 出题与组卷、AI 批改与错题分析、提交与批改流转。\n"
    "- 资料体系：课程材料库、教材库、AI 解析材料、学习文档（导学）自动生成。\n"
    "- 沟通：消息中心、私信、邮件提醒（考试/监考一次性提醒）、博客（含新闻爬取与 AI 摘要）。\n"
    "- 教务对接：课表/考务/监考/名册自动同步、智慧教室考勤、校园公文通（公文中心，表 gongwen_documents，支持关键词检索与姓名关注）。\n"
    "- AI 能力：课堂 AI 助教（每课堂可配置提示词）、全局 AI 助手（懂当前页面）、教师 Agent 任务中心（全平台单任务队列）。\n"
    "- 管理：班级/课程/课堂/教室/学期/签名管理，超级管理员有组织架构、用户、反馈、诊断面板。"
)


def build_platform_overview_block(user_role: str = "") -> str:
    """平台总览 + 路由表 + 当前时间，注入任何 AI/Agent 的 system prompt。"""
    role = str(user_role or "").strip() or "all"
    route_lines = []
    for route in PLATFORM_ROUTES:
        if route["roles"] != "all" and role != "all" and route["roles"] != role:
            continue
        route_lines.append(f"- {route['path']} —— {route['label']}")
    return (
        "--- 平台认知（LanShare 全局事实） ---\n"
        f"平台名称：LanShare 智慧课堂（高校 AI 辅助教学平台）。\n"
        f"正式域名：{PLATFORM_BASE_URL} （站内跳转直接用相对路径，如 /manage/gongwen）。\n"
        f"{PLATFORM_FEATURES_TEXT}\n"
        "常用路由（给用户跳转建议时直接引用路径）：\n"
        + "\n".join(route_lines)
        + "\n"
        + build_time_context_text()
    )


def _safe_scalar(conn, sql: str, params: tuple = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _safe_rows(conn, sql: str, params: tuple = ()) -> list[Any]:
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def build_teacher_knowledge_block(conn, teacher_id: int) -> str:
    """教师全量画像：基础信息 + 教学规模 + 近期动态（全部查库，只读）。"""
    lines = ["--- 当前用户全量画像（来自平台数据库，真实可信） ---"]
    try:
        info = conn.execute(
            "SELECT name, email, description FROM teachers WHERE id = ?",
            (teacher_id,),
        ).fetchone()
    except Exception:
        info = None
    if info:
        lines.append(f"身份：教师 {info['name']}（ID {teacher_id}，邮箱 {info['email'] or '未填写'}）")
        if info["description"]:
            lines.append(f"画像摘要：{str(info['description'])[:400]}")

    course_count = _safe_scalar(conn, "SELECT COUNT(*) FROM courses WHERE created_by_teacher_id = ?", (teacher_id,))
    offering_count = _safe_scalar(conn, "SELECT COUNT(*) FROM class_offerings WHERE teacher_id = ?", (teacher_id,))
    student_count = _safe_scalar(
        conn,
        """
        SELECT COUNT(DISTINCT s.id)
        FROM students s
        JOIN class_offerings co ON co.class_id = s.class_id
        WHERE co.teacher_id = ?
        """,
        (teacher_id,),
    )
    assignment_count = _safe_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM assignments a
        JOIN class_offerings co ON co.id = a.class_offering_id
        WHERE co.teacher_id = ?
        """,
        (teacher_id,),
    )
    gongwen_follow_hits = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM gongwen_follow_hits WHERE teacher_id = ?",
        (teacher_id,),
    )
    agent_task_count = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM agent_tasks WHERE teacher_id = ?",
        (teacher_id,),
    )
    lines.append(
        f"教学规模：{course_count} 门课程模板、{offering_count} 个开课课堂、约 {student_count} 名学生、"
        f"{assignment_count} 份作业/考试；公文关注命中 {gongwen_follow_hits} 条；历史 Agent 任务 {agent_task_count} 个。"
    )

    offerings = _safe_rows(
        conn,
        """
        SELECT co.id, c.name AS course_name, cl.name AS class_name
        FROM class_offerings co
        JOIN courses c ON c.id = co.course_id
        JOIN classes cl ON cl.id = co.class_id
        WHERE co.teacher_id = ?
        ORDER BY co.id DESC
        LIMIT 8
        """,
        (teacher_id,),
    )
    if offerings:
        lines.append("在教课堂（课堂主页路径 /classroom/<ID>）：")
        for row in offerings:
            lines.append(f"  - [{row['id']}] 《{row['course_name']}》 {row['class_name']}")

    upcoming = _safe_rows(
        conn,
        """
        SELECT title, event_type, start_time
        FROM teacher_calendar_events
        WHERE teacher_id = ? AND start_time >= ?
        ORDER BY start_time ASC
        LIMIT 6
        """,
        (teacher_id, datetime.now().strftime("%Y-%m-%d 00:00:00")),
    )
    if upcoming:
        lines.append("近期日程：")
        for row in upcoming:
            lines.append(f"  - {row['start_time']} {row['event_type'] or ''} {row['title'] or ''}".rstrip())
    return "\n".join(lines)


def build_student_knowledge_block(conn, student_id: int) -> str:
    """学生全量画像：基础信息 + 班级 + 学习概况。"""
    lines = ["--- 当前用户全量画像（来自平台数据库，真实可信） ---"]
    try:
        info = conn.execute(
            """
            SELECT s.name, s.student_id_number, s.description, c.name AS class_name
            FROM students s
            LEFT JOIN classes c ON c.id = s.class_id
            WHERE s.id = ?
            """,
            (student_id,),
        ).fetchone()
    except Exception:
        info = None
    if info:
        lines.append(
            f"身份：学生 {info['name']}（学号 {info['student_id_number'] or '未知'}，班级 {info['class_name'] or '未知'}）"
        )
        if info["description"]:
            lines.append(f"画像摘要：{str(info['description'])[:400]}")

    submission_count = _safe_scalar(conn, "SELECT COUNT(*) FROM submissions WHERE student_id = ?", (student_id,))
    blog_count = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM blog_posts WHERE author_role = 'student' AND author_id = ?",
        (student_id,),
    )
    lines.append(f"学习概况：累计提交作业/考试 {submission_count} 次，发表博客 {blog_count} 篇。")
    return "\n".join(lines)


def build_user_knowledge_block(conn, user_pk: int, user_role: str) -> str:
    try:
        if user_role == "teacher":
            return build_teacher_knowledge_block(conn, int(user_pk))
        return build_student_knowledge_block(conn, int(user_pk))
    except Exception:
        return ""
