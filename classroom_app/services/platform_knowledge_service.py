"""
平台认知服务 —— 为 AI 对话与 Agent 任务注入「全知」上下文：

- 平台是什么：域名、定位、功能版图
- 平台怎么走：面向用户的路由表（AI 可以直接给出可点击跳转链接）
- 现在是什么时候：精确到分钟的当前时间（含星期 / 时段 / 时节提示）
- 用户是谁：通过查库得到的全量画像（身份、课堂、统计、近况）

查询只读。可选功能缺表时保留其余上下文；程序错误或数据库故障向调用者报告。
"""
from __future__ import annotations

from datetime import datetime
import logging
import re
import sqlite3
from typing import Any

from .manage_nav_service import iter_platform_manage_routes
from .prompt_utils import build_time_context_text

PLATFORM_BASE_URL = "https://guardianangel.net.cn"
logger = logging.getLogger(__name__)
_READ_SAVEPOINT = "platform_knowledge_read"

# 面向用户的路由表。AI 给跳转建议时直接使用这些路径（站内相对路径即可点击）。
PLATFORM_ROUTES: list[dict[str, str]] = [
    {"path": "/dashboard", "label": "首页（日程、待办、课堂入口）", "roles": "all"},
    {"path": "/classroom/{class_offering_id}", "label": "课堂主页（研讨、材料、作业、考试、互动）", "roles": "all"},
    {"path": "/assignment/{assignment_id}", "label": "作业/考试详情", "roles": "all"},
    {"path": "/exam/take/{assignment_id}", "label": "在线考试答题页", "roles": "student"},
    {"path": "/assignment/{assignment_id}/wrong-summary", "label": "错题汇总", "roles": "all"},
    {"path": "/submission/{submission_id}", "label": "提交详情与批改", "roles": "all"},
    {"path": "/blog", "label": "博客中心（师生文章与校园资讯）", "roles": "all"},
    {"path": "/message-center", "label": "消息中心（通知与私信）", "roles": "all"},
    {"path": "/profile", "label": "个人中心（资料、签名与偏好）", "roles": "all"},
    *iter_platform_manage_routes(),
]

PLATFORM_FEATURES_TEXT = (
    "平台功能版图：\n"
    "- 课堂教学：课堂主页（实时研讨、课时安排、学习文档、课堂互动/举手/弹幕）、学习进度追踪与修为成长体系。\n"
    "- 作业与考试：作业布置、在线考试、AI 辅助出题与组卷、AI 批改与错题复盘、提交与批改流转。\n"
    "- 资料体系：课程材料库、教材库、材料解析与学习文档生成。\n"
    "- 沟通：消息中心、私信、邮件提醒（考试/监考提醒）、博客中心。\n"
    "- 教师管理中心：按教学、教务与个人设置组织。\n"
    "- 教务对接：课表/考务/监考/名册同步、智慧课堂考勤、校园公文通（表 gongwen_documents，支持关键词检索与姓名关注）。\n"
    "- AI 能力：课堂 AI 助教（每课堂可自定义提示词）、全局 AI 助手（结合当前页面内容与已授权学习材料，定位到文档页码/课次/章节并给出站内链接；"
    "教师在课堂页提问时可结合课堂名册、作业考试、投票、分组、课次与学习文档，在教师确认后协助发起或结束投票、随机分组）、教师 Agent 任务中心。\n"
    "- 管理：超级管理员可维护组织架构、用户、反馈、诊断与额度。"
)


def build_platform_overview_block(user_role: str = "", *, include_time: bool = True) -> str:
    """平台总览 + 路由表（可选附带当前时间），注入 AI/Agent 的 system prompt。"""
    role = str(user_role or "").strip() or "all"
    route_lines = []
    for route in PLATFORM_ROUTES:
        if route["roles"] != "all" and role != "all" and route["roles"] != role:
            continue
        route_lines.append(f"- {route['path']} —— {route['label']}")
    body = (
        "--- 平台信息（LanShare 站内事实） ---\n"
        "平台名称：LanShare 课堂互动平台（高校教学协作与 AI 辅助学习平台）。\n"
        f"正式域名：{PLATFORM_BASE_URL}（站内跳转直接用相对路径，如 /manage/academic/gongwen）。\n"
        f"{PLATFORM_FEATURES_TEXT}\n"
        "常用路由（给用户跳转建议时直接引用路径）：\n"
        + "\n".join(route_lines)
    )
    if include_time:
        body += "\n" + build_time_context_text()
    return body


def _sqlstate(exc: BaseException) -> str:
    try:
        value = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None) or ""
    except Exception:
        return ""
    return value if isinstance(value, str) and re.fullmatch(r"[0-9A-Z]{5}", value) else ""


def _missing_optional_table(exc: BaseException) -> bool:
    if _sqlstate(exc) == "42P01":
        return True
    return (isinstance(exc, sqlite3.OperationalError)
            and getattr(exc, "sqlite_errorcode", sqlite3.SQLITE_ERROR) == sqlite3.SQLITE_ERROR
            and str(exc).startswith("no such table:"))


def _diagnostic(level, query_name, phase, exc):
    # Query labels are fixed at the call sites. Never log SQL, bind values,
    # profile text, connection strings or arbitrary exception messages.
    logger.log(level, "Platform knowledge query=%s phase=%s error=%s sqlstate=%s",
               query_name, phase, type(exc).__name__, _sqlstate(exc))


def _read_query(conn, sql, params, read, default, *, query_name, optional_schema=False):
    # Both SQLite and PostgreSQL target the newest savepoint with this name.
    # Each synchronous read releases its own level; a caller's same-named
    # level survives. Fixed SQL also preserves adapter/driver cache reuse.
    savepoint = _READ_SAVEPOINT
    try:
        conn.execute("SAVEPOINT " + savepoint)
    except BaseException as exc:
        _diagnostic(logging.ERROR, query_name, "savepoint_failed", exc)
        raise
    try:
        value = read(conn.execute(sql, params))
    except BaseException as primary:
        restored = True
        for command, phase in (("ROLLBACK TO SAVEPOINT ", "rollback_failed"), ("RELEASE SAVEPOINT ", "release_failed")):
            try:
                conn.execute(command + savepoint)
            except BaseException as cleanup:
                restored = False
                primary.add_note("Platform knowledge " + phase + ": " + type(cleanup).__name__)
                _diagnostic(logging.ERROR, query_name, phase, cleanup)
        if restored and optional_schema and _missing_optional_table(primary):
            _diagnostic(logging.WARNING, query_name, "optional_table_unavailable", primary)
            return default
        _diagnostic(logging.ERROR, query_name, "query_failed", primary)
        raise
    try:
        conn.execute("RELEASE SAVEPOINT " + savepoint)
    except BaseException as exc:
        _diagnostic(logging.ERROR, query_name, "release_failed", exc)
        raise
    return value


def _safe_scalar(conn, sql: str, params: tuple = (), *, query_name="scalar", optional_schema=False) -> int:
    def read(cursor):
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    return _read_query(conn, sql, params, read, 0, query_name=query_name, optional_schema=optional_schema)


def _safe_rows(conn, sql: str, params: tuple = (), *, query_name="rows", optional_schema=False) -> list[Any]:
    return _read_query(conn, sql, params, lambda cursor: cursor.fetchall(), [],
                       query_name=query_name, optional_schema=optional_schema)


def _safe_row(conn, sql: str, params: tuple = (), *, query_name):
    return _read_query(conn, sql, params, lambda cursor: cursor.fetchone(), None, query_name=query_name)


def build_teacher_knowledge_block(conn, teacher_id: int) -> str:
    """教师基础信息、教学规模与近期日程（全部查库，只读）。"""
    lines = ["--- 当前用户教学资料与日程（来自平台数据库） ---"]
    info = _safe_row(conn,
            "SELECT name, email, description FROM teachers WHERE id = ?",
            (teacher_id,),
            query_name="teacher_identity",
        )
    if info:
        lines.append(f"身份：教师 {info['name']}（ID {teacher_id}，邮箱 {info['email'] or '未填写'}）")
        if info["description"]:
            lines.append(f"个人简介：{str(info['description'])[:400]}")

    course_count = _safe_scalar(conn, "SELECT COUNT(*) FROM courses WHERE created_by_teacher_id = ?", (teacher_id,), query_name="teacher_courses")
    offering_count = _safe_scalar(conn, "SELECT COUNT(*) FROM class_offerings WHERE teacher_id = ?", (teacher_id,), query_name="teacher_offerings")
    student_count = _safe_scalar(
        conn,
        """
        SELECT COUNT(DISTINCT s.id)
        FROM students s
        JOIN class_offerings co ON (s.class_id = co.class_id OR EXISTS (SELECT 1 FROM class_offering_class_links cocl_m WHERE cocl_m.offering_id = co.id AND cocl_m.class_id = s.class_id))
        WHERE co.teacher_id = ?
        """,
        (teacher_id,),
        query_name="teacher_students",
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
        query_name="teacher_assignments",
    )
    gongwen_follow_hits = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM gongwen_follow_hits WHERE teacher_id = ?",
        (teacher_id,),
        query_name="teacher_gongwen_hits", optional_schema=True,
    )
    agent_task_count = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM agent_tasks WHERE teacher_id = ?",
        (teacher_id,),
        query_name="teacher_agent_tasks", optional_schema=True,
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
        query_name="teacher_recent_offerings",
    )
    if offerings:
        lines.append("在教课堂（课堂主页路径 /classroom/<ID>）：")
        for row in offerings:
            lines.append(f"  - [{row['id']}] 《{row['course_name']}》 {row['class_name']}")

    upcoming = _safe_rows(
        conn,
        """
        SELECT title, source_type AS event_type, starts_at AS start_time
        FROM teacher_calendar_events
        WHERE teacher_id = ? AND starts_at >= ? AND status = 'active' AND deleted_at IS NULL
        ORDER BY starts_at ASC
        LIMIT 6
        """,
        (teacher_id, datetime.now().strftime("%Y-%m-%d 00:00:00")),
        query_name="teacher_calendar", optional_schema=True,
    )
    if upcoming:
        lines.append("近期日程：")
        for row in upcoming:
            lines.append(f"  - {row['start_time']} {row['event_type'] or ''} {row['title'] or ''}".rstrip())
    return "\n".join(lines)


def build_student_knowledge_block(conn, student_id: int) -> str:
    """Student-owned identity and factual aggregate counts, without inferences."""
    lines = ["--- 当前用户资料与学习记录 ---"]
    info = _safe_row(conn,
            """
            SELECT s.name, s.student_id_number, c.name AS class_name
            FROM students s
            LEFT JOIN classes c ON c.id = s.class_id
            WHERE s.id = ?
            """,
            (student_id,),
            query_name="student_identity",
        )
    if info:
        lines.append(
            f"身份：学生 {info['name']}（学号 {info['student_id_number'] or '未知'}，班级 {info['class_name'] or '未知'}）"
        )

    submission_count = _safe_scalar(conn, "SELECT COUNT(*) FROM submissions WHERE student_pk_id = ?", (student_id,), query_name="student_submissions")
    blog_count = _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM blog_posts WHERE author_role = 'student' AND author_user_pk = ?",
        (student_id,),
        query_name="student_blogs",
    )
    lines.append(f"学习概况：累计提交作业/考试 {submission_count} 次，发表博客 {blog_count} 篇。")
    return "\n".join(lines)


def build_user_knowledge_block(conn, user_pk: int, user_role: str) -> str:
    if user_role == "teacher":
        return build_teacher_knowledge_block(conn, int(user_pk))
    return build_student_knowledge_block(conn, int(user_pk))
