from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..config import (
    AGENT_TASK_RUNTIME_WORKSPACE_PREFIX,
    AGENT_TASK_WORKSPACE_ROOT,
)
from ..db.connection import begin_immediate_transaction, execute_insert_returning_id, get_configured_db_engine


TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELED = "canceled"

ACTIVE_TASK_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
FINAL_TASK_STATUSES = {TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TASK_STATUS_CANCELED}

TASK_STATUS_LABELS = {
    TASK_STATUS_QUEUED: "排队中",
    TASK_STATUS_RUNNING: "执行中",
    TASK_STATUS_COMPLETED: "已完成",
    TASK_STATUS_FAILED: "失败",
    TASK_STATUS_CANCELED: "已取消",
}

AGENT_TASK_ADVISORY_LOCK_KEYS = (752712, 41001)

TASK_TYPE_DEFINITIONS: dict[str, dict[str, str]] = {
    "course_material_digest": {
        "label": "整理课程材料",
        "verb": "整理",
        "placeholder": "整理本课堂近期材料，输出下次课导学文档草稿。",
    },
    "lesson_document": {
        "label": "生成学习文档",
        "verb": "生成",
        "placeholder": "选中课堂时间轴中的目标课时，或写明第几次课；系统会读取前序学习文档并生成/绑定新文档。",
    },
    "assignment_blueprint": {
        "label": "生成作业/考试草案",
        "verb": "出题",
        "placeholder": "结合材料和 JSON 模板，生成下次课课堂作业草案。",
    },
    "blog_draft": {
        "label": "撰写课堂博客",
        "verb": "撰写",
        "placeholder": "围绕本课堂主题写一篇可发布的博客草稿。",
    },
    "student_notification": {
        "label": "拟定学生通知",
        "verb": "通知",
        "placeholder": "给某考试低于指定分数的学生拟定通知内容和名单规则。",
    },
    "general_teaching_task": {
        "label": "教学事务",
        "verb": "处理",
        "placeholder": "描述一个需要排队执行的教学事务。",
    },
}

AGENT_TEACHER_WORKFLOWS: tuple[dict[str, Any], ...] = (
    {
        "key": "course_roster_setup",
        "name": "课程、班级与名单准备",
        "steps": [
            "创建或同步课程、班级、教学班和学生名单",
            "核对学号、邮箱、班级归属和任课教师权限",
            "确认课堂时间表、教材和教学计划",
            "进入具体课堂后开展备课、发布任务和学习支持",
        ],
        "agent_capability": "可读取当前页面和管理中心摘要，生成核对清单；不会自动导入、删除或批量改学生名单。",
        "guardrail": "名单、课程归属、教务同步和管理员配置必须由具备权限的教师或管理员在原页面确认。",
    },
    {
        "key": "classroom_preparation",
        "name": "课前备课与课堂准备",
        "steps": [
            "确认授课课堂、课时、教学主题和既有材料",
            "读取前序学习文档、课堂材料、时间轴内容和作业反馈",
            "生成本次或下一次课的导学文档、板书提纲、作业草案",
            "由教师确认后发布或绑定到课堂",
        ],
        "agent_capability": "可安全读取课堂上下文，可自动生成并绑定学习文档；作业与发布动作默认只生成草案。",
        "guardrail": "仅操作当前教师拥有的课堂、课时和材料，不修改核心源码、数据库结构或其他教师数据。",
    },
    {
        "key": "material_operations",
        "name": "课程材料整理与复用",
        "steps": [
            "盘点课程材料、课时绑定文档和材料解析摘要",
            "识别缺失的学习文档、重复材料和可复用素材",
            "输出材料清单、下一步建议和可生成内容",
        ],
        "agent_capability": "可完整接管盘点与报告；材料重命名、删除、跨目录移动等破坏性动作暂不自动执行。",
        "guardrail": "默认只读材料库，除学习文档生成服务外不直接改动材料文件。",
    },
    {
        "key": "lesson_document_generation",
        "name": "学习文档生成与绑定",
        "steps": [
            "定位当前课堂和目标课时",
            "读取目标课时之前已绑定的学习文档",
            "生成新的 Markdown 学习文档并写入材料库",
            "把生成文档绑定到目标课时，供教师复核后给学生使用",
        ],
        "agent_capability": "可在白名单动作内自动生成、保存并绑定目标课时学习文档。",
        "guardrail": "只写当前教师材料库和当前课堂目标课时，不改历史文档、不改课程结构、不触碰源码。",
    },
    {
        "key": "assignment_exam_workflow",
        "name": "作业/考试设计与发布",
        "steps": [
            "理解课堂目标、前序材料、目标课时和评分方式",
            "生成题目要求、评分标准、提交格式和发布检查清单",
            "教师审阅后在作业或考试编辑器中发布给学生",
            "发布后跟踪提交、批改和低分学生支持",
        ],
        "agent_capability": "可生成结构化作业/考试草案；不会自动发布、改分或创建正式考试。",
        "guardrail": "任何影响学生可见状态的动作必须由教师在平台界面确认。",
    },
    {
        "key": "submission_grading_feedback",
        "name": "提交、批改与反馈复盘",
        "steps": [
            "读取作业/考试提交状态、成绩分布和批改摘要",
            "识别未交、低分、逾期、需要重交或需要人工复核的学生",
            "生成反馈建议、通知草稿、复盘清单和下一次课补救建议",
            "教师确认后执行批改、退回、通知或线下沟通",
        ],
        "agent_capability": "可生成分析报告、名单预览和通知草稿；不会自动改分、删除提交或批量退回。",
        "guardrail": "学生成绩和提交详情只对任务发起教师可见；所有影响学生记录的动作需要教师确认。",
    },
    {
        "key": "student_support",
        "name": "学生通知与学情支持",
        "steps": [
            "限定课堂、作业/考试和筛选条件",
            "读取成绩、提交状态、逾期状态等必要数据",
            "生成学生名单预览、通知文案和后续跟进建议",
            "教师确认后再发送通知或私信",
        ],
        "agent_capability": "可生成名单和通知草稿；不会直接给学生群发消息。",
        "guardrail": "学生详情仅任务发起教师可见，其他教师只看到队列公开状态。",
    },
    {
        "key": "learning_progress",
        "name": "学习进度、阶段考试与证书",
        "steps": [
            "读取学习阶段、阶段考试、证书和课堂完成状态",
            "识别学生卡点、可解锁任务和需要补充材料的阶段",
            "生成阶段复盘、个性化练习建议和教师干预清单",
        ],
        "agent_capability": "可辅助分析和生成建议；不会自动发证、创建正式阶段考试或改学习进度。",
        "guardrail": "阶段考试、证书和学习记录属于高影响数据，必须走平台既有确认流程。",
    },
    {
        "key": "discussion_collaboration",
        "name": "讨论、协作与课堂互动",
        "steps": [
            "读取当前课堂讨论、协作任务、资源上传和互动信号摘要",
            "整理学生问题、优秀观点、常见误区和下一步互动设计",
            "生成课堂讨论总结、协作反馈或活动脚本",
        ],
        "agent_capability": "可生成总结和活动草案；不会代替学生发言、删除互动内容或公开私人消息。",
        "guardrail": "互动内容默认只作为教师视角辅助，不跨课堂泄露学生表达。",
    },
    {
        "key": "blog_and_reflection",
        "name": "课堂博客与教学反思",
        "steps": [
            "读取当前课堂、材料、课时和教师输入主题",
            "生成课堂博客草稿、摘要、标签和发布建议",
            "可创建教师私有草稿，等待教师审阅后发布",
        ],
        "agent_capability": "可安全创建博客草稿；不会自动公开发布。",
        "guardrail": "只以当前教师身份创建草稿，不代学生发言，不公开敏感学生信息。",
    },
    {
        "key": "operations_admin",
        "name": "教学运营与管理中心",
        "steps": [
            "读取管理中心当前页面的筛选条件和统计上下文",
            "生成检查清单、数据核对建议和下一步操作",
            "需要管理员权限的配置由管理员在管理中心确认",
        ],
        "agent_capability": "可辅助分析与生成建议；不会改动系统配置、部署或密钥。",
        "guardrail": "任务中心教师端不能越过管理员边界，学生端无任务中心入口和接口权限。",
    },
)

_CORE_CODE_DENY_PATTERNS = (
    r"\bgit\s+(commit|push|pull|reset|checkout|merge|rebase|clean|rm)\b",
    r"\bdocker\s+(compose|run|exec|build|rm|rmi|stop|restart)\b",
    r"\b(rm\s+-rf|del\s+/f|format\s+|drop\s+table|truncate\s+table)\b",
    r"\b(classroom_app|templates|static/js|static/css|Dockerfile|docker-compose|main\.py|ai_assistant\.py)\b",
    r"(修改|重构|删除|覆盖|提交|推送|部署).{0,16}(核心代码|源码|项目代码|代码库|路由|模板|数据库结构|数据表|迁移)",
    r"(核心代码|源码|项目代码|代码库|路由|模板|数据库结构|数据表|迁移).{0,16}(修改|重构|删除|覆盖|提交|推送|部署)",
)

MAX_INSTRUCTION_CHARS = 4000
MAX_CONTEXT_TEXT_CHARS = 16000
MAX_RESULT_DETAIL_CHARS = 40000
MAX_RUNTIME_TEXT_OUTPUTS = 12
COMPOSER_TTL_SECONDS = 35

_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, *, max_chars: int = 0) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _load_json(raw_value: Any, fallback: Any) -> Any:
    if raw_value in (None, ""):
        return fallback
    try:
        return json.loads(str(raw_value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _summarize_text(text: str, *, limit: int = 96) -> str:
    normalized = re.sub(r"\s+", " ", _clean_text(text))
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _compact_title_text(text: str, *, limit: int = 24) -> str:
    normalized = _clean_text(text, max_chars=160)
    normalized = re.sub(r"^[#*\-_\s\"'`“”‘’]+", "", normalized)
    normalized = re.sub(r"^(任务标题|标题|题目)\s*[:：]\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.split(r"[\n\r。！？!?；;]", normalized, maxsplit=1)[0]
    normalized = re.sub(r"\s+", "", normalized).strip("：:，,、.。")
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip("：:，,、.。")


def _recent_agent_task_titles(conn, *, exclude_task_id: int = 0, limit: int = 60) -> list[str]:
    rows = conn.execute(
        """
        SELECT title
        FROM agent_tasks
        WHERE id <> ?
          AND COALESCE(title, '') <> ''
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (int(exclude_task_id or 0), max(1, min(int(limit or 60), 120))),
    ).fetchall()
    return [_clean_text(row["title"], max_chars=80) for row in rows if _clean_text(row["title"], max_chars=80)]


def _ensure_unique_task_title(title: str, existing_titles: list[str]) -> str:
    base = _compact_title_text(title, limit=18) or "教学任务"
    existing = {str(item).strip() for item in existing_titles if str(item or "").strip()}
    if base not in existing:
        return base
    for index in range(2, 100):
        candidate = f"{base}{index}"
        if candidate not in existing:
            return candidate
    return f"{base}{uuid.uuid4().hex[:4]}"


def _fallback_agent_task_title(instruction: str, task_type: str, existing_titles: list[str] | None = None) -> str:
    label = TASK_TYPE_DEFINITIONS.get(task_type, TASK_TYPE_DEFINITIONS["general_teaching_task"])["label"]
    stem = _compact_title_text(instruction, limit=18)
    lesson_match = re.search(r"第\s*([0-9一二两三四五六七八九十]{1,6})\s*(?:次课|节课|课|节)", instruction)
    if task_type == "lesson_document" and lesson_match:
        stem = f"第{lesson_match.group(1)}课"
    elif task_type == "assignment_blueprint" and re.search(r"(考试|试卷|测验)", instruction):
        stem = "考试草案"
    elif task_type == "student_notification" and re.search(r"(低分|未交|逾期)", instruction):
        stem = "学生通知"
    if not stem:
        stem = label
    title = stem if label in stem else f"{label}-{stem}"
    return _ensure_unique_task_title(title, existing_titles or [])


def _public_summary_for_task(task_type: str) -> str:
    return TASK_TYPE_DEFINITIONS.get(task_type, TASK_TYPE_DEFINITIONS["general_teaching_task"])["label"]


def _parse_chinese_number(token: str) -> int | None:
    raw = _clean_text(token, max_chars=16)
    if not raw:
        return None
    if raw.isdigit():
        value = int(raw)
        return value if value > 0 else None
    if raw in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[raw]
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = _CHINESE_DIGITS.get(left, 1 if not left else 0)
        ones = _CHINESE_DIGITS.get(right, 0 if not right else -1)
        value = tens * 10 + ones
        return value if value > 0 else None
    return None


def _parse_requested_session_order(instruction: str) -> int | None:
    normalized = _clean_text(instruction)
    patterns = (
        r"第\s*([0-9一二两三四五六七八九十〇零]{1,8})\s*(?:次课|节课|课|讲)",
        r"([0-9]{1,3})\s*(?:次课|节课|课|讲)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            value = _parse_chinese_number(match.group(1))
            if value:
                return value
    return None


def _instruction_requests_next_session(instruction: str) -> bool:
    normalized = _clean_text(instruction)
    return bool(re.search(r"(下一|下次|下节|下一节|下一次|下一个)\s*(?:课|课时|课堂|学习文档|文档)?", normalized))


def _session_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "order_index": int(row.get("order_index") or 0),
        "title": row.get("title") or "",
        "content_excerpt": _summarize_text(row.get("content") or "", limit=900),
        "section_count": int(row.get("section_count") or 0) or 1,
        "session_date": row.get("session_date") or "",
        "learning_material_id": int(row.get("learning_material_id") or 0) or None,
        "learning_material_name": row.get("learning_material_name") or "",
        "learning_material_path": row.get("learning_material_path") or "",
        "has_learning_material": bool(row.get("learning_material_id")),
    }


def _resolve_lesson_document_target(
    sessions: list[dict[str, Any]],
    *,
    selected_session_id: int | None,
    selected_order_index: int | None,
    instruction: str,
) -> dict[str, Any] | None:
    lesson_sessions = [
        _session_payload(item)
        for item in sorted(sessions, key=lambda row: int(row.get("order_index") or 0))
        if int(item.get("order_index") or 0) > 0
    ]
    if not lesson_sessions:
        return None

    by_id = {int(item["id"]): item for item in lesson_sessions if item.get("id")}
    by_order = {int(item["order_index"]): item for item in lesson_sessions if item.get("order_index")}
    selected = by_id.get(int(selected_session_id or 0)) or by_order.get(int(selected_order_index or 0))
    explicit_order = _parse_requested_session_order(instruction)
    requests_next = _instruction_requests_next_session(instruction)

    reason = "first_unbound_session"
    target: dict[str, Any] | None = None
    if explicit_order and explicit_order in by_order:
        target = by_order[explicit_order]
        reason = "explicit_order"
    elif requests_next and selected:
        target = next(
            (item for item in lesson_sessions if int(item["order_index"]) > int(selected["order_index"])),
            None,
        )
        reason = "next_after_selected"
    elif selected:
        target = selected
        reason = "selected_session"
    elif requests_next:
        last_bound_order = max(
            [int(item["order_index"]) for item in lesson_sessions if item.get("learning_material_id")] or [0],
        )
        target = next(
            (item for item in lesson_sessions if int(item["order_index"]) > last_bound_order),
            None,
        )
        reason = "next_after_last_bound"

    if not target:
        target = next((item for item in lesson_sessions if not item.get("learning_material_id")), None)
        reason = "first_unbound_session"
    if not target:
        target = lesson_sessions[-1]
        reason = "last_session_fallback"

    previous_sessions = [
        item
        for item in lesson_sessions
        if int(item["order_index"]) < int(target["order_index"]) and item.get("learning_material_id")
    ]
    next_session = next(
        (item for item in lesson_sessions if int(item["order_index"]) > int(target["order_index"])),
        None,
    )
    return {
        **target,
        "reason": reason,
        "selected_session": selected,
        "previous_bound_sessions": previous_sessions[-8:],
        "previous_bound_count": len(previous_sessions),
        "next_session": next_session,
    }


def validate_business_task(instruction: str) -> None:
    normalized = instruction.strip()
    if len(normalized) < 6:
        raise HTTPException(status_code=400, detail="请补充更明确的任务内容。")
    if len(normalized) > MAX_INSTRUCTION_CHARS:
        raise HTTPException(status_code=400, detail="任务描述过长，请压缩到 4000 字以内。")

    lowered = normalized.lower()
    for pattern in _CORE_CODE_DENY_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            raise HTTPException(
                status_code=400,
                detail="任务中心只处理教学业务事务，不能执行核心代码、部署、数据库结构或项目源码改动。",
            )


def task_type_options() -> list[dict[str, str]]:
    return [
        {"value": key, **value}
        for key, value in TASK_TYPE_DEFINITIONS.items()
    ]


def agent_workflow_catalog() -> list[dict[str, Any]]:
    return [dict(item) for item in AGENT_TEACHER_WORKFLOWS]


def _teacher_display_name(user: dict[str, Any]) -> str:
    return _clean_text(user.get("name") or user.get("nickname") or f"教师{user.get('id') or ''}", max_chars=80)


def _normalize_context_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        safe_key = _clean_text(key, max_chars=64)
        if not safe_key:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            normalized[safe_key] = _clean_text(value, max_chars=MAX_CONTEXT_TEXT_CHARS) if isinstance(value, str) else value
        elif isinstance(value, list):
            normalized[safe_key] = value[:20]
        elif isinstance(value, dict):
            normalized[safe_key] = {
                _clean_text(child_key, max_chars=64): (
                    _clean_text(child_value, max_chars=2000)
                    if isinstance(child_value, str)
                    else child_value
                )
                for child_key, child_value in list(value.items())[:40]
            }
    return normalized


def _resolve_optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def build_teacher_page_context(
    conn,
    teacher_id: int,
    page_context: dict[str, Any],
    *,
    task_type: str = "",
    instruction: str = "",
) -> dict[str, Any]:
    """Enrich client page hints with server-verified teaching context."""
    context = _normalize_context_payload(page_context)
    context.setdefault("server_context", {})
    server_context: dict[str, Any] = {}

    class_offering_id = _resolve_optional_int(
        context.get("classOfferingId")
        or context.get("class_offering_id")
        or (context.get("materialContext") or {}).get("classOfferingId")
    )
    assignment_id = _resolve_optional_int(context.get("assignmentId") or context.get("assignment_id"))
    material_id = _resolve_optional_int(
        context.get("materialId")
        or context.get("material_id")
        or (context.get("materialContext") or {}).get("materialId")
    )
    selected_session_hint = (
        (context.get("classroomContext") or {}).get("selectedSession")
        or (context.get("materialContext") or {})
        or {}
    )
    selected_session_id = _resolve_optional_int(
        context.get("sessionId")
        or context.get("session_id")
        or selected_session_hint.get("id")
        or selected_session_hint.get("sessionId")
    )
    selected_order_index = _resolve_optional_int(
        context.get("sessionOrderIndex")
        or context.get("session_order_index")
        or selected_session_hint.get("orderIndex")
        or selected_session_hint.get("order_index")
    )

    if assignment_id:
        row = conn.execute(
            """
            SELECT a.id, a.title, a.status, a.class_offering_id, a.requirements_md, a.rubric_md,
                   c.name AS course_name, cl.name AS class_name,
                   COUNT(s.id) AS submission_count
            FROM assignments a
            JOIN courses c ON c.id = a.course_id
            LEFT JOIN class_offerings co ON co.id = a.class_offering_id
            LEFT JOIN classes cl ON cl.id = co.class_id
            LEFT JOIN submissions s ON s.assignment_id = a.id
            WHERE a.id = ?
              AND (co.teacher_id = ? OR c.created_by_teacher_id = ?)
            GROUP BY a.id
            LIMIT 1
            """,
            (assignment_id, teacher_id, teacher_id),
        ).fetchone()
        if row:
            server_context["assignment"] = {
                "id": int(row["id"]),
                "title": row["title"],
                "status": row["status"],
                "class_offering_id": row["class_offering_id"],
                "course_name": row["course_name"],
                "class_name": row["class_name"],
                "submission_count": int(row["submission_count"] or 0),
                "requirements_excerpt": _summarize_text(row["requirements_md"] or "", limit=600),
                "rubric_excerpt": _summarize_text(row["rubric_md"] or "", limit=600),
            }
            class_offering_id = class_offering_id or _resolve_optional_int(row["class_offering_id"])

    if material_id:
        row = conn.execute(
            """
            SELECT id, name, material_path, node_type, preview_type, ai_parse_status,
                   ai_parse_result_json
            FROM course_materials
            WHERE id = ? AND teacher_id = ?
            LIMIT 1
            """,
            (material_id, teacher_id),
        ).fetchone()
        if row:
            parsed = _load_json(row["ai_parse_result_json"], {})
            server_context["material"] = {
                "id": int(row["id"]),
                "name": row["name"],
                "path": row["material_path"],
                "node_type": row["node_type"],
                "preview_type": row["preview_type"],
                "ai_parse_status": row["ai_parse_status"],
                "ai_summary": _summarize_text(parsed.get("summary") or "", limit=800)
                if isinstance(parsed, dict)
                else "",
            }

    if class_offering_id:
        row = conn.execute(
            """
            SELECT co.id, co.semester, co.schedule_info, co.first_class_date,
                   c.name AS course_name, cl.name AS class_name
            FROM class_offerings co
            JOIN courses c ON c.id = co.course_id
            JOIN classes cl ON cl.id = co.class_id
            WHERE co.id = ? AND co.teacher_id = ?
            LIMIT 1
            """,
            (class_offering_id, teacher_id),
        ).fetchone()
        if row:
            assignments = conn.execute(
                """
                SELECT id, title, status, due_at, created_at
                FROM assignments
                WHERE class_offering_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 8
                """,
                (class_offering_id,),
            ).fetchall()
            materials = conn.execute(
                """
                SELECT m.id, m.name, m.material_path, m.preview_type
                FROM course_material_assignments a
                JOIN course_materials m ON m.id = a.material_id
                WHERE a.class_offering_id = ?
                  AND m.teacher_id = ?
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT 8
                """,
                (class_offering_id, teacher_id),
            ).fetchall()
            sessions = [
                dict(item)
                for item in conn.execute(
                    """
                    SELECT s.id,
                           s.order_index,
                           s.title,
                           s.content,
                           s.section_count,
                           s.session_date,
                           s.learning_material_id,
                           lm.name AS learning_material_name,
                           lm.material_path AS learning_material_path
                    FROM class_offering_sessions s
                    LEFT JOIN course_materials lm ON lm.id = s.learning_material_id
                    WHERE s.class_offering_id = ?
                    ORDER BY s.order_index ASC
                    LIMIT 120
                    """,
                    (class_offering_id,),
                ).fetchall()
            ]
            selected_session = None
            for item in sessions:
                if selected_session_id and int(item.get("id") or 0) == selected_session_id:
                    selected_session = _session_payload(item)
                    break
                if selected_order_index and int(item.get("order_index") or 0) == selected_order_index:
                    selected_session = _session_payload(item)
                    break
            lesson_document_target = None
            target_useful_task_types = {"lesson_document", "course_material_digest", "assignment_blueprint", "blog_draft"}
            if task_type in target_useful_task_types or _instruction_requests_next_session(instruction):
                lesson_document_target = _resolve_lesson_document_target(
                    sessions,
                    selected_session_id=selected_session_id,
                    selected_order_index=selected_order_index,
                    instruction=instruction,
                )
                if lesson_document_target:
                    lesson_document_target["class_offering_id"] = int(class_offering_id)
            server_context["classroom"] = {
                "id": int(row["id"]),
                "course_name": row["course_name"],
                "class_name": row["class_name"],
                "semester": row["semester"],
                "first_class_date": row["first_class_date"],
                "schedule_info": _summarize_text(row["schedule_info"] or "", limit=500),
                "recent_assignments": [dict(item) for item in assignments],
                "recent_materials": [dict(item) for item in materials],
                "session_count": len(sessions),
                "sessions_overview": [_session_payload(item) for item in sessions[:24]],
            }
            if selected_session:
                server_context["selected_session"] = selected_session
            if lesson_document_target:
                server_context["lesson_document_target"] = lesson_document_target

    context["server_context"] = server_context
    return context


def create_agent_task(conn, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    teacher_id = int(user["id"])
    task_type = _clean_text(payload.get("task_type"), max_chars=64) or "general_teaching_task"
    if task_type not in TASK_TYPE_DEFINITIONS:
        task_type = "general_teaching_task"

    instruction = _clean_text(payload.get("instruction"), max_chars=MAX_INSTRUCTION_CHARS)
    validate_business_task(instruction)

    context_snapshot = build_teacher_page_context(
        conn,
        teacher_id,
        payload.get("page_context") or {},
        task_type=task_type,
        instruction=instruction,
    )
    context_snapshot["agent_options"] = {
        "deep_thinking": bool(payload.get("deep_thinking")),
    }
    existing_titles = _recent_agent_task_titles(conn)
    title = _fallback_agent_task_title(instruction, task_type, existing_titles)
    public_summary = _public_summary_for_task(task_type)

    now = utcnow_iso()
    task_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO agent_tasks (
            task_uuid, teacher_id, teacher_name, task_type, title, public_summary,
            private_instruction, context_snapshot_json, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            teacher_id,
            _teacher_display_name(user),
            task_type,
            title,
            public_summary,
            instruction,
            _json_dumps(context_snapshot),
            TASK_STATUS_QUEUED,
            now,
            now,
        ),
    )
    append_task_event(
        conn,
        task_id,
        "queued",
        "任务已进入全平台队列。",
        {"task_type": task_type},
        commit=False,
    )
    return get_agent_task(conn, task_id, teacher_id=teacher_id)


def append_task_event(
    conn,
    task_id: int,
    event_type: str,
    message: str,
    detail: dict[str, Any] | None = None,
    *,
    commit: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_task_events (task_id, event_type, message, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            int(task_id),
            _clean_text(event_type, max_chars=40) or "status",
            _clean_text(message, max_chars=1000),
            _json_dumps(detail or {}),
            utcnow_iso(),
        ),
    )
    if commit:
        conn.commit()


def _page_label_from_context(page_context: dict[str, Any] | None) -> str:
    context = _normalize_context_payload(page_context or {})
    pieces = [
        ((context.get("materialContext") or {}).get("materialName") or ""),
        ((context.get("assignmentContext") or {}).get("title") or ""),
        ((context.get("classroomContext") or {}).get("courseName") or ""),
        ((context.get("manageContext") or {}).get("pageTitle") or ""),
        ((context.get("page") or {}).get("title") or ""),
    ]
    return _summarize_text(next((str(item) for item in pieces if str(item or "").strip()), "当前页面"), limit=48)


def _purge_stale_composers(conn, *, tolerate_write_failure: bool = False) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=COMPOSER_TTL_SECONDS)).isoformat()
    try:
        conn.execute("DELETE FROM agent_task_composers WHERE updated_at < ?", (cutoff,))
    except sqlite3.OperationalError:
        if not tolerate_write_failure:
            raise
        conn.rollback()
    return cutoff


def set_agent_task_composer(
    conn,
    user: dict[str, Any],
    *,
    active: bool,
    page_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    teacher_id = int(user["id"])
    _purge_stale_composers(conn)
    if not active:
        conn.execute("DELETE FROM agent_task_composers WHERE teacher_id = ?", (teacher_id,))
        conn.commit()
        return get_agent_queue_state(conn, viewer_teacher_id=teacher_id)

    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO agent_task_composers (teacher_id, teacher_name, page_label, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(teacher_id) DO UPDATE SET
            teacher_name = excluded.teacher_name,
            page_label = excluded.page_label,
            updated_at = excluded.updated_at
        """,
        (teacher_id, _teacher_display_name(user), _page_label_from_context(page_context), now),
    )
    conn.commit()
    return get_agent_queue_state(conn, viewer_teacher_id=teacher_id)


def get_agent_queue_state(conn, *, viewer_teacher_id: int) -> dict[str, Any]:
    composer_cutoff = _purge_stale_composers(conn, tolerate_write_failure=True)
    queued_count = int(
        conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE status = ?", (TASK_STATUS_QUEUED,)).fetchone()[0]
    )
    running = conn.execute(
        """
        SELECT id, teacher_id, teacher_name, task_type, public_summary, started_at
        FROM agent_tasks
        WHERE status = ?
        ORDER BY started_at ASC, id ASC
        LIMIT 1
        """,
        (TASK_STATUS_RUNNING,),
    ).fetchone()
    composer = conn.execute(
        """
        SELECT teacher_id, teacher_name, page_label, updated_at
        FROM agent_task_composers
        WHERE teacher_id <> ? AND updated_at >= ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (int(viewer_teacher_id), composer_cutoff),
    ).fetchone()

    running_payload: dict[str, Any] | None = None
    if running:
        running_payload = {
            "task_id": int(running["id"]),
            "teacher_id": int(running["teacher_id"] or 0),
            "teacher_name": running["teacher_name"] or "某位老师",
            "task_type_label": _public_summary_for_task(running["task_type"] or "general_teaching_task"),
            "public_summary": running["public_summary"] or _public_summary_for_task(running["task_type"] or "general_teaching_task"),
            "started_at": running["started_at"] or "",
        }

    composer_payload: dict[str, Any] | None = None
    if composer:
        composer_payload = {
            "teacher_id": int(composer["teacher_id"] or 0),
            "teacher_name": composer["teacher_name"] or "某位老师",
            "page_label": composer["page_label"] or "当前页面",
            "updated_at": composer["updated_at"] or "",
        }

    return {
        "queued_count": queued_count,
        "is_running": bool(running_payload),
        "is_composing": bool(composer_payload) and not running_payload,
        "running": running_payload,
        "composer": composer_payload,
    }


async def generate_agent_task_title(task_id: int) -> None:
    from ..core import ai_client
    from ..database import get_db_connection

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT id, task_type, title, private_instruction
            FROM agent_tasks
            WHERE id = ?
            LIMIT 1
            """,
            (int(task_id),),
        ).fetchone()
        if not row:
            return
        task_type = row["task_type"] or "general_teaching_task"
        instruction = _clean_text(row["private_instruction"], max_chars=MAX_INSTRUCTION_CHARS)
        existing_titles = _recent_agent_task_titles(conn, exclude_task_id=int(task_id), limit=80)

    fallback_title = _fallback_agent_task_title(instruction, task_type, existing_titles)
    generated_title = ""
    label = TASK_TYPE_DEFINITIONS.get(task_type, TASK_TYPE_DEFINITIONS["general_teaching_task"])["label"]
    title_prompt = (
        "请为一个教师 Agent 任务生成一个不超过 14 个汉字的短标题。\n"
        "要求：只返回标题本身；不要包含学生姓名、学号、邮箱、具体分数或隐私细节；"
        "避免和已有标题重复；标题要像任务名，不要写完整句子。\n\n"
        f"任务类型：{label}\n"
        f"已有标题：{json.dumps(existing_titles[:60], ensure_ascii=False)}\n"
        f"任务内容：{instruction[:1600]}"
    )
    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": "你是 LanShare 教学平台的任务标题生成器，输出必须简短、安全、无隐私。",
                "messages": [],
                "new_message": title_prompt,
                "model_capability": "standard",
                "task_type": "fast_text_response",
                "web_search_enabled": False,
            },
            timeout=18.0,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "success":
            generated_title = _compact_title_text(payload.get("response_text") or payload.get("text") or "", limit=18)
    except Exception as exc:
        print(f"[AGENT_TASK] title generation fallback for task {task_id}: {exc}")

    title = _ensure_unique_task_title(generated_title or fallback_title, existing_titles)
    public_summary = _public_summary_for_task(task_type)
    with get_db_connection() as conn:
        current = conn.execute("SELECT title FROM agent_tasks WHERE id = ? LIMIT 1", (int(task_id),)).fetchone()
        if not current:
            return
        if _clean_text(current["title"], max_chars=80) == title:
            return
        now = utcnow_iso()
        conn.execute(
            """
            UPDATE agent_tasks
            SET title = ?, public_summary = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, public_summary, now, int(task_id)),
        )
        append_task_event(
            conn,
            int(task_id),
            "title_ready",
            "任务短标题已生成。",
            {"title": title, "source": "ai" if generated_title else "fallback"},
            commit=False,
        )
        conn.commit()


def _queue_positions(rows: list[dict[str, Any]]) -> dict[int, int]:
    queued = [
        item
        for item in sorted(rows, key=lambda value: (value.get("created_at") or "", int(value.get("id") or 0)))
        if item.get("status") == TASK_STATUS_QUEUED
    ]
    return {int(item["id"]): index + 1 for index, item in enumerate(queued)}


def _queue_position_for_task(conn, item: dict[str, Any]) -> int:
    if str(item.get("status") or "") != TASK_STATUS_QUEUED:
        return 0
    priority = int(item.get("priority") or 0)
    created_at = str(item.get("created_at") or "")
    task_id = int(item.get("id") or 0)
    row = conn.execute(
        """
        SELECT COUNT(*) AS ahead
        FROM agent_tasks
        WHERE status = ?
          AND (
            priority > ?
            OR (
              priority = ?
              AND (
                created_at < ?
                OR (created_at = ? AND id < ?)
              )
            )
          )
        """,
        (TASK_STATUS_QUEUED, priority, priority, created_at, created_at, task_id),
    ).fetchone()
    return int(row["ahead"] if row else 0) + 1


def _elapsed_seconds(item: dict[str, Any]) -> int:
    started_at = item.get("started_at")
    completed_at = item.get("completed_at")
    if not started_at:
        return 0
    try:
        start_dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00")) if completed_at else datetime.now(timezone.utc)
    except ValueError:
        return 0
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    return max(0, int((end_dt - start_dt).total_seconds()))


def _serialize_event(row) -> dict[str, Any]:
    item = dict(row)
    return {
        "id": int(item.get("id") or 0),
        "event_type": item.get("event_type") or "status",
        "message": item.get("message") or "",
        "detail": _load_json(item.get("detail_json"), {}),
        "created_at": item.get("created_at") or "",
    }


def serialize_agent_task(row, *, viewer_teacher_id: int, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    item = dict(row)
    task_id = int(item.get("id") or 0)
    owner_teacher_id = int(item.get("teacher_id") or 0)
    is_owner = owner_teacher_id == int(viewer_teacher_id)
    status = item.get("status") or TASK_STATUS_QUEUED
    payload = {
        "id": task_id,
        "task_uuid": item.get("task_uuid") or "",
        "task_type": item.get("task_type") or "",
        "task_type_label": TASK_TYPE_DEFINITIONS.get(item.get("task_type") or "", {}).get("label", "教学事务"),
        "title": item.get("title") if is_owner else item.get("public_summary"),
        "public_summary": item.get("public_summary") or "",
        "teacher_name": item.get("teacher_name") or "某位老师",
        "status": status,
        "status_label": TASK_STATUS_LABELS.get(status, "处理中"),
        "is_owner": is_owner,
        "is_active": status in ACTIVE_TASK_STATUSES,
        "is_terminal": status in FINAL_TASK_STATUSES,
        "queue_position": int(item.get("queue_position") or 0),
        "elapsed_seconds": _elapsed_seconds(item),
        "runtime_provider": item.get("runtime_provider") or "deepseek-tui",
        "runtime_status": item.get("runtime_status") or "",
        "created_at": item.get("created_at") or "",
        "started_at": item.get("started_at") or "",
        "completed_at": item.get("completed_at") or "",
        "updated_at": item.get("updated_at") or "",
    }
    if is_owner:
        payload.update(
            {
                "private_instruction": item.get("private_instruction") or "",
                "context_snapshot": _load_json(item.get("context_snapshot_json"), {}),
                "runtime_task_id": item.get("runtime_task_id") or "",
                "runtime_thread_id": item.get("runtime_thread_id") or "",
                "runtime_turn_id": item.get("runtime_turn_id") or "",
                "result_summary": item.get("result_summary") or "",
                "result_detail": _load_json(item.get("result_detail_json"), {}),
                "error_message": item.get("error_message") or "",
                "events": events or [],
            }
        )
    return payload


def list_agent_tasks(conn, *, viewer_teacher_id: int, limit: int = 30) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM agent_tasks
            ORDER BY
              CASE status
                WHEN 'running' THEN 0
                WHEN 'queued' THEN 1
                ELSE 2
              END,
              created_at ASC,
              id ASC
            LIMIT ?
            """,
            (max(1, min(int(limit), 80)),),
        ).fetchall()
    ]
    queue_positions = _queue_positions(rows)
    for row in rows:
        row["queue_position"] = queue_positions.get(int(row["id"]), 0)
    counts = {
        status: int(
            conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE status = ?", (status,)).fetchone()[0]
        )
        for status in (
            TASK_STATUS_QUEUED,
            TASK_STATUS_RUNNING,
            TASK_STATUS_COMPLETED,
            TASK_STATUS_FAILED,
            TASK_STATUS_CANCELED,
        )
    }
    return {
        "tasks": [
            serialize_agent_task(row, viewer_teacher_id=viewer_teacher_id)
            for row in rows
        ],
        "counts": counts,
        "queue_state": get_agent_queue_state(conn, viewer_teacher_id=viewer_teacher_id),
    }


def get_agent_task(conn, task_id: int, *, teacher_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_tasks WHERE id = ? LIMIT 1", (int(task_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在。")
    events = [
        _serialize_event(event)
        for event in conn.execute(
            "SELECT * FROM agent_task_events WHERE task_id = ? ORDER BY id ASC",
            (int(task_id),),
        ).fetchall()
    ]
    item = dict(row)
    item["queue_position"] = _queue_position_for_task(conn, item)
    serialized = serialize_agent_task(item, viewer_teacher_id=int(teacher_id), events=events)
    if not serialized["is_owner"]:
        return serialized
    return serialized


def delete_agent_task(conn, task_id: int, *, teacher_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_tasks WHERE id = ? LIMIT 1", (int(task_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if int(row["teacher_id"] or 0) != int(teacher_id):
        raise HTTPException(status_code=403, detail="只能删除自己的任务历史。")
    status = str(row["status"] or "")
    if status in ACTIVE_TASK_STATUSES:
        raise HTTPException(status_code=400, detail="任务仍在排队或执行中，请先取消或等待结束后再删除。")

    conn.execute("DELETE FROM agent_task_events WHERE task_id = ?", (int(task_id),))
    conn.execute("DELETE FROM agent_tasks WHERE id = ? AND teacher_id = ?", (int(task_id), int(teacher_id)))
    conn.commit()
    return {"deleted": True, "task_id": int(task_id)}


def delete_agent_task_history(conn, *, teacher_id: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT id
        FROM agent_tasks
        WHERE teacher_id = ?
          AND status IN (?, ?, ?)
        """,
        (int(teacher_id), TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TASK_STATUS_CANCELED),
    ).fetchall()
    task_ids = [int(row["id"]) for row in rows]
    if not task_ids:
        return {"deleted_count": 0, "task_ids": []}

    placeholders = ",".join("?" for _ in task_ids)
    conn.execute(f"DELETE FROM agent_task_events WHERE task_id IN ({placeholders})", task_ids)
    conn.execute(f"DELETE FROM agent_tasks WHERE id IN ({placeholders}) AND teacher_id = ?", [*task_ids, int(teacher_id)])
    conn.commit()
    return {"deleted_count": len(task_ids), "task_ids": task_ids}


def cancel_agent_task(conn, task_id: int, *, teacher_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_tasks WHERE id = ? LIMIT 1", (int(task_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if int(row["teacher_id"] or 0) != int(teacher_id):
        raise HTTPException(status_code=403, detail="只能取消自己的任务。")
    status = str(row["status"] or "")
    if status in FINAL_TASK_STATUSES:
        return get_agent_task(conn, task_id, teacher_id=teacher_id)

    now = utcnow_iso()
    if status == TASK_STATUS_RUNNING:
        conn.execute(
            """
            UPDATE agent_tasks
            SET cancel_requested_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, int(task_id)),
        )
        append_task_event(conn, task_id, "cancel_requested", "已请求取消，正在等待执行器响应。", commit=False)
    else:
        conn.execute(
            """
            UPDATE agent_tasks
            SET status = ?, cancel_requested_at = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_CANCELED, now, now, now, int(task_id)),
        )
        append_task_event(conn, task_id, "canceled", "任务已取消。", commit=False)
    conn.commit()
    return get_agent_task(conn, task_id, teacher_id=teacher_id)


def _claim_next_agent_task_sqlite(conn, *, worker_id: str, now: str) -> dict[str, Any] | None:
    begin_immediate_transaction(conn)
    running = conn.execute(
        "SELECT id FROM agent_tasks WHERE status = ? LIMIT 1",
        (TASK_STATUS_RUNNING,),
    ).fetchone()
    if running:
        conn.commit()
        return None
    row = conn.execute(
        """
        SELECT *
        FROM agent_tasks
        WHERE status = ?
        ORDER BY priority DESC, created_at ASC, id ASC
        LIMIT 1
        """,
        (TASK_STATUS_QUEUED,),
    ).fetchone()
    if not row:
        conn.commit()
        return None
    task_id = int(row["id"])
    conn.execute(
        """
        UPDATE agent_tasks
        SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ?, worker_id = ?
        WHERE id = ? AND status = ?
        """,
        (TASK_STATUS_RUNNING, now, now, worker_id, task_id, TASK_STATUS_QUEUED),
    )
    append_task_event(conn, task_id, "started", "Agent 执行器已领取任务。", {"worker_id": worker_id}, commit=False)
    conn.commit()
    return dict(conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone())


def _claim_next_agent_task_postgres(conn, *, worker_id: str, now: str) -> dict[str, Any] | None:
    lock_row = conn.execute(
        "SELECT pg_try_advisory_xact_lock(?, ?) AS acquired",
        AGENT_TASK_ADVISORY_LOCK_KEYS,
    ).fetchone()
    acquired = bool(lock_row.get("acquired") if isinstance(lock_row, dict) else lock_row[0])
    if not acquired:
        conn.commit()
        return None

    row = conn.execute(
        """
        WITH candidate AS (
            SELECT id
            FROM agent_tasks
            WHERE status = ?
            ORDER BY priority DESC, created_at ASC, id ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        UPDATE agent_tasks
        SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ?, worker_id = ?
        FROM candidate
        WHERE agent_tasks.id = candidate.id
          AND NOT EXISTS (
              SELECT 1
              FROM agent_tasks running
              WHERE running.status = ?
          )
        RETURNING agent_tasks.*
        """,
        (TASK_STATUS_QUEUED, TASK_STATUS_RUNNING, now, now, worker_id, TASK_STATUS_RUNNING),
    ).fetchone()
    if not row:
        conn.commit()
        return None

    task_id = int(row["id"])
    append_task_event(conn, task_id, "started", "Agent 执行器已领取任务。", {"worker_id": worker_id}, commit=False)
    conn.commit()
    return dict(row)


def claim_next_agent_task(conn, *, worker_id: str) -> dict[str, Any] | None:
    now = utcnow_iso()
    engine = get_configured_db_engine()
    if engine == "postgres":
        return _claim_next_agent_task_postgres(conn, worker_id=worker_id, now=now)
    if engine == "sqlite":
        return _claim_next_agent_task_sqlite(conn, worker_id=worker_id, now=now)
    raise ValueError(f"Unsupported agent task database engine: {engine!r}")


def mark_task_runtime_started(
    conn,
    task_id: int,
    *,
    runtime_task_id: str,
    runtime_thread_id: str = "",
    runtime_turn_id: str = "",
) -> None:
    now = utcnow_iso()
    conn.execute(
        """
        UPDATE agent_tasks
        SET runtime_task_id = ?, runtime_thread_id = ?, runtime_turn_id = ?,
            runtime_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (runtime_task_id, runtime_thread_id, runtime_turn_id, TASK_STATUS_RUNNING, now, int(task_id)),
    )
    append_task_event(
        conn,
        task_id,
        "runtime_started",
        "已接入 DeepSeek-TUI 独立运行时。",
        {"runtime_task_id": runtime_task_id},
        commit=False,
    )
    conn.commit()


def update_task_runtime_snapshot(conn, task_id: int, runtime_task: dict[str, Any]) -> None:
    now = utcnow_iso()
    status = str(runtime_task.get("status") or "")
    conn.execute(
        """
        UPDATE agent_tasks
        SET runtime_status = ?, runtime_thread_id = COALESCE(?, runtime_thread_id),
            runtime_turn_id = COALESCE(?, runtime_turn_id), updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            runtime_task.get("thread_id"),
            runtime_task.get("turn_id"),
            now,
            int(task_id),
        ),
    )
    conn.commit()


def _extract_runtime_text_outputs(runtime_task: dict[str, Any]) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []
    seen: set[str] = set()
    preferred_keys = {
        "result",
        "result_summary",
        "summary",
        "output",
        "response",
        "response_text",
        "final",
        "final_answer",
        "assistant_message",
        "last_message",
        "message",
        "error",
    }

    def add(path: str, value: Any) -> None:
        text = _clean_text(value, max_chars=6000)
        if not text or text in seen:
            return
        seen.add(text)
        outputs.append({"path": path, "text": text})

    def visit(value: Any, path: str, depth: int) -> None:
        if len(outputs) >= MAX_RUNTIME_TEXT_OUTPUTS or depth > 5:
            return
        if isinstance(value, str):
            key = path.rsplit(".", 1)[-1].lower()
            if key in preferred_keys or len(value.strip()) >= 40:
                add(path, value)
            return
        if isinstance(value, dict):
            ordered_items = sorted(
                value.items(),
                key=lambda item: 0 if str(item[0]).lower() in preferred_keys else 1,
            )
            for key, child in ordered_items:
                safe_key = _clean_text(key, max_chars=48) or "item"
                visit(child, f"{path}.{safe_key}" if path else safe_key, depth + 1)
                if len(outputs) >= MAX_RUNTIME_TEXT_OUTPUTS:
                    break
            return
        if isinstance(value, list):
            start_index = max(0, len(value) - 16)
            for index, child in enumerate(value[start_index:], start=start_index):
                visit(child, f"{path}[{index}]", depth + 1)
                if len(outputs) >= MAX_RUNTIME_TEXT_OUTPUTS:
                    break

    visit(runtime_task, "", 0)
    return outputs


def runtime_result_summary(runtime_task: dict[str, Any]) -> str:
    for key in ("result_summary", "summary", "final_answer", "output", "response_text", "error"):
        value = _clean_text(runtime_task.get(key), max_chars=1800)
        if value:
            return value
    outputs = _extract_runtime_text_outputs(runtime_task)
    if outputs:
        return _summarize_text(outputs[0]["text"], limit=480)
    status = _clean_text(runtime_task.get("status"), max_chars=40) or "unknown"
    if status == "completed":
        return "DeepSeek-TUI 已标记任务完成，但没有返回明确的业务结论或产物。请查看执行记录；如果没有生成结果，需要调整任务要求后重试。"
    if status == "failed":
        return "DeepSeek-TUI 已标记任务失败，但没有返回具体错误。请查看运行时状态或稍后重试。"
    return f"DeepSeek-TUI 任务结束，运行时状态：{status}。"


def _final_event_message(status: str, *, result_summary: str, error_message: str) -> str:
    if status == TASK_STATUS_COMPLETED:
        summary = _summarize_text(result_summary, limit=180)
        return f"任务成功完成：{summary}" if summary else "任务成功完成，但未返回可展示的详细结论。"
    if status == TASK_STATUS_FAILED:
        reason = _summarize_text(error_message or result_summary, limit=180)
        return f"任务失败：{reason}" if reason else "任务失败，未返回具体原因。"
    if status == TASK_STATUS_CANCELED:
        return "任务已取消。"
    return TASK_STATUS_LABELS.get(status, "任务结束")


def finish_agent_task(
    conn,
    task_id: int,
    *,
    status: str,
    result_summary: str = "",
    result_detail: dict[str, Any] | None = None,
    error_message: str = "",
) -> None:
    now = utcnow_iso()
    safe_status = status if status in FINAL_TASK_STATUSES else TASK_STATUS_FAILED
    conn.execute(
        """
        UPDATE agent_tasks
        SET status = ?, result_summary = ?, result_detail_json = ?, error_message = ?,
            completed_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            safe_status,
            _clean_text(result_summary, max_chars=2000),
            _json_dumps(result_detail or {}),
            _clean_text(error_message, max_chars=2000),
            now,
            now,
            int(task_id),
        ),
    )
    append_task_event(
        conn,
        task_id,
        safe_status,
        _final_event_message(safe_status, result_summary=result_summary, error_message=error_message),
        {
            "status": safe_status,
            "summary": _clean_text(result_summary, max_chars=1200),
            "error": _clean_text(error_message, max_chars=1200),
        },
        commit=False,
    )
    conn.commit()


def task_workspace_paths(task: dict[str, Any]) -> tuple[Path, str]:
    task_id = str(task.get("id") or task.get("task_uuid") or uuid.uuid4())
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "-", task_id)
    host_path = AGENT_TASK_WORKSPACE_ROOT / "tasks" / safe_name
    runtime_path = f"{AGENT_TASK_RUNTIME_WORKSPACE_PREFIX}/{safe_name}"
    return host_path, runtime_path


def write_task_workspace(task: dict[str, Any]) -> str:
    host_path, runtime_path = task_workspace_paths(task)
    host_path.mkdir(parents=True, exist_ok=True)
    context = _load_json(task.get("context_snapshot_json"), {})
    context_text = json.dumps(context, ensure_ascii=False, indent=2)
    instructions = _clean_text(task.get("private_instruction"), max_chars=MAX_INSTRUCTION_CHARS)
    readme = f"""# LanShare Agent Task {task.get('id')}

## Task

{instructions}

## Verified Page Context

```json
{context_text[:MAX_CONTEXT_TEXT_CHARS]}
```

## Safety Boundary

- Do not modify LanShare core source code, deployment files, database schema, or runtime configuration.
- Work only with the task context and produce business-facing drafts, checklists, or validated action proposals.
- If platform state changes are needed, describe the exact whitelisted action and wait for LanShare to execute it.
"""
    (host_path / "TASK.md").write_text(readme, encoding="utf-8")
    (host_path / "context.json").write_text(context_text, encoding="utf-8")
    return runtime_path


def build_runtime_prompt(task: dict[str, Any], runtime_workspace: str) -> str:
    context = _load_json(task.get("context_snapshot_json"), {})
    task_type = str(task.get("task_type") or "general_teaching_task")
    definition = TASK_TYPE_DEFINITIONS.get(task_type, TASK_TYPE_DEFINITIONS["general_teaching_task"])
    instruction = _clean_text(task.get("private_instruction"), max_chars=MAX_INSTRUCTION_CHARS)
    agent_options = context.get("agent_options") if isinstance(context.get("agent_options"), dict) else {}
    thinking_line = (
        "本任务已开启深度思考：请使用更充分的推理、验证和风险检查，最后只向教师展示清晰结论。"
        if agent_options.get("deep_thinking")
        else "本任务未强制开启深度思考：优先保持执行简洁，但仍需做必要的安全检查。"
    )
    workflow_lines = "\n".join(
        f"- {item['name']}：{item['agent_capability']} 安全边界：{item['guardrail']}"
        for item in AGENT_TEACHER_WORKFLOWS
    )
    return f"""
你是 LanShare 内置的教师任务中心 Agent，当前任务类型是：{definition["label"]}。

必须遵守：
1. 只处理教学业务相关任务：课程材料整理、学习文档草案、作业/考试草案、博客草稿、学生通知草稿、教学事务分析。
2. 严禁修改、生成补丁、删除、重构或部署 LanShare 核心代码；严禁修改数据库结构、Docker 配置、运行脚本、源码目录。
3. 你所在 workspace 是隔离任务目录：{runtime_workspace}。只能在此目录内阅读上下文、整理产物。
4. 涉及发布博客、发送通知、创建作业/考试等平台状态变更时，先输出结构化草案和执行建议，不要假装已经修改平台数据。
5. 输出必须面向教师，清楚列出：任务理解、已使用的上下文、执行结果/草案、需要教师确认的动作、风险提醒。
6. {thinking_line}

你能安全接管的教师业务流程边界：
{workflow_lines}

教师任务：
{instruction}

平台已验证的页面和课堂上下文如下，若上下文不足请明确说明，不要编造不存在的数据：
```json
{json.dumps(context, ensure_ascii=False, indent=2)[:MAX_CONTEXT_TEXT_CHARS]}
```
""".strip()


def compact_runtime_detail(runtime_task: dict[str, Any]) -> dict[str, Any]:
    detail = {
        "runtime_task_id": runtime_task.get("id"),
        "runtime_status": runtime_task.get("status"),
        "thread_id": runtime_task.get("thread_id"),
        "turn_id": runtime_task.get("turn_id"),
        "result_summary": runtime_task.get("result_summary"),
        "summary": runtime_task.get("summary"),
        "error": runtime_task.get("error"),
        "duration_ms": runtime_task.get("duration_ms"),
        "text_outputs": _extract_runtime_text_outputs(runtime_task),
        "timeline": runtime_task.get("timeline") or [],
        "tool_calls": runtime_task.get("tool_calls") or [],
        "artifacts": runtime_task.get("artifacts") or [],
        "raw_keys": sorted(str(key) for key in runtime_task.keys()),
    }
    encoded = json.dumps(detail, ensure_ascii=False)
    if len(encoded) > MAX_RESULT_DETAIL_CHARS:
        detail["timeline"] = detail["timeline"][-20:]
        detail["tool_calls"] = detail["tool_calls"][-20:]
        detail["truncated"] = True
    return detail
