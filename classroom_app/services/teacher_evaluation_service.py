"""Core service for the teacher 评学表 (教师评学表 / 过程材料) content asset.

Owns CRUD, payload normalization, org-scoped sharing/visibility, tags, field
auto-fill from a class offering, comprehensive-rating computation, docx export and
HTML preview, and the clone-to-inherit flow. A *teacher evaluation sheet* is one
course-class's《广西外国语学院教师评学表》 = template fields + the FIXED 10 评价指标
(each 10 分，合计 100) + a computed 综合评价 + a free-text 学习情况分析与教学改革建议.

It mirrors :mod:`assessment_plan_service` for the table/visibility/CRUD pattern.
Export is delegated to :mod:`material_export_template_service` (template_key
``evaluation_sheet``), which precisely reproduces the official docx.

The indicator set is fixed by the official template — only the per-indicator
评价得分 and the analysis text vary — so there are no signatures and the normalizer
snaps whatever it is given back onto the canonical 10-row template.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from ..db.connection import get_configured_db_engine
from ..db.schema_teacher_evaluations import ensure_teacher_evaluation_schema
from . import material_scope_service as scope_core
from .academic_class_mapping_service import resolve_offering_display_class_name
from .class_label_service import build_academic_class_label
from .material_export_template_service import build_material_export_artifact
from .document_render_service import DocumentRenderError, document_render_service
from .organization_scope_service import load_teacher_org_scope
from .process_material_import_summary_service import build_process_import_summary
from .resource_access_service import is_super_admin_teacher

# ---------------------------------------------------------------------------
# Sharing scope (identical ladder to assessment plans / lesson plans)
# ---------------------------------------------------------------------------
SCOPE_PRIVATE = "private"
SCOPE_DEPARTMENT = "department"
SCOPE_COLLEGE = "college"
SCOPE_SCHOOL = "school"

SCOPE_ORDER = (SCOPE_PRIVATE, SCOPE_DEPARTMENT, SCOPE_COLLEGE, SCOPE_SCHOOL)
SCOPE_LABELS = {
    SCOPE_PRIVATE: "私有",
    SCOPE_DEPARTMENT: "本系部公开",
    SCOPE_COLLEGE: "本院级公开",
    SCOPE_SCHOOL: "全校公开",
}
_SCOPE_COLS = {
    "school": "school_code",
    "college": "college",
    "department": "department",
    "level": "scope_level",
    "openness": "scope_level",
}

TARGET_TOTAL_SCORE = 100
MAX_INDICATOR_SCORE = 10

# ---------------------------------------------------------------------------
# The FIXED official template (广西外国语学院教师评学表) — verbatim from the .doc.
# Only the per-indicator score + analysis text vary between records.
# ---------------------------------------------------------------------------
GROUP_ATTITUDE = "学习态度"
GROUP_PROCESS = "学习过程"
GROUP_EFFECT = "学习效果"
EFFECT_SUBNOTE = "（结合试卷、作业分析）"

# (group, indicator text). Order is authoritative and must never change.
EVALUATION_INDICATORS: tuple[tuple[str, str], ...] = (
    (GROUP_ATTITUDE, "1.尊敬师长，虚心好学，听课认真，课堂情绪饱满。"),
    (GROUP_ATTITUDE, "2.遵守教学管理制度，出勤率高，课堂秩序好。"),
    (GROUP_PROCESS, "3.自学能力较好，能做到课前预习，课后复习。"),
    (GROUP_PROCESS, "4.跟随教师思路，理解授课内容并能认真做好笔记。"),
    (GROUP_PROCESS, "5.课堂学习气氛活跃，思维活跃，踊跃发言。"),
    (GROUP_PROCESS, "6.学习自觉性高，经常阅读一些相关参考文献资料。"),
    (GROUP_PROCESS, "7.课后常和老师交流，主动提问，积极参与辅导答疑。"),
    (GROUP_EFFECT, "8.学生对该门课很感兴趣，学习积极性高."),
    (GROUP_EFFECT, "9.能较好地掌握本门课程基本知识、基本理论和基本技能。"),
    (GROUP_EFFECT, "10.活学活用，能运用本课程知识提出、分析、解决实际问题。"),
)

EVALUATION_NOTES = [
    "备注：1.承担2门或2门以上课程及不同班级的教师，须按课程及班级分别填写评学表。",
    "2.教师应根据学生实际情况对每项指标打分，并合计总得分进行综合评价。其中优秀（90分及其以上，良好（80—89分），一般（70—79分），较差（69分及其以上）。",
]

# 综合评价 buckets, high → low. Ratings are computed from the total, never AI-picked.
RATINGS = ("优秀", "良好", "一般", "较差")

# The template-field set the editor exposes / the importer must surface.
FIELD_KEYS = (
    "school",
    "course_name",
    "class_name",
    "college",
    "teacher_name",
    "teacher_title",
    "evaluate_date",
    "academic_year",
    "semester",
)

DEFAULT_SCHOOL_NAME = "广西外国语学院"


def compute_rating(total: float) -> str:
    """优秀 ≥90，良好 80–89，一般 70–79，较差 ≤69."""
    try:
        value = float(total)
    except (TypeError, ValueError):
        return ""
    if value >= 90:
        return "优秀"
    if value >= 80:
        return "良好"
    if value >= 70:
        return "一般"
    return "较差"


def normalize_scope_level(value: Any, *, default: str = SCOPE_PRIVATE) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in SCOPE_ORDER else default


def scope_label(value: Any) -> str:
    return SCOPE_LABELS.get(normalize_scope_level(value), SCOPE_LABELS[SCOPE_PRIVATE])


def scope_options() -> list[dict[str, str]]:
    return [{"value": level, "label": SCOPE_LABELS[level]} for level in SCOPE_ORDER]


def template_indicators() -> list[dict[str, Any]]:
    """The blank 10-row indicator template (no scores yet)."""
    return [
        {"group": group, "indicator": text, "max_score": MAX_INDICATOR_SCORE, "score": ""}
        for group, text in EVALUATION_INDICATORS
    ]


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------
def teacher_scope(conn: sqlite3.Connection, teacher_id: int | str) -> dict[str, str]:
    return load_teacher_org_scope(conn, int(teacher_id))


def can_view_evaluation(row: Any, viewer_scope: dict[str, str], *, is_super_admin: bool = False) -> bool:
    data = dict(row) if not isinstance(row, dict) else row
    level = normalize_scope_level(data.get("scope_level"))
    if level == SCOPE_PRIVATE:
        return bool(is_super_admin)
    proxy = {
        "attr_school_code": data.get("school_code"),
        "attr_college": data.get("college"),
        "attr_department": data.get("department"),
        "openness": level,
    }
    return scope_core.can_view(proxy, viewer_scope, is_super_admin=is_super_admin)


def visibility_where(
    viewer_scope: dict[str, str],
    teacher_id: int,
    *,
    is_super_admin: bool = False,
    alias: str = "",
) -> tuple[str, list[Any]]:
    prefix = f"{alias}." if alias else ""
    if is_super_admin:
        return "1=1", []
    shared_sql, shared_params = scope_core.build_visibility_filter(
        viewer_scope, is_super_admin=False, cols=_SCOPE_COLS, table_alias=alias.rstrip(".")
    )
    sql = (
        f"({prefix}teacher_id = ? "
        f"OR (lower(trim({prefix}scope_level)) != 'private' AND {shared_sql}))"
    )
    return sql, [int(teacher_id), *shared_params]


# ---------------------------------------------------------------------------
# Payload normalization
# ---------------------------------------------------------------------------
def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _uses_postgres_metadata(conn: Any) -> bool:
    if isinstance(conn, sqlite3.Connection):
        return False
    try:
        return get_configured_db_engine() == "postgres"
    except Exception:
        return False


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        if key in row.keys():
            return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        return None


def _table_columns(conn: Any, table_name: str) -> set[str]:
    if _uses_postgres_metadata(conn):
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = ? AND table_name = ?
            """,
            ("public", table_name),
        ).fetchall()
        return {str(_row_value(row, "column_name", 0) or "") for row in rows if _row_value(row, "column_name", 0)}
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return {str(_row_value(row, "name", 1) or "") for row in rows if _row_value(row, "name", 1)}


def _clamp_score(value: Any) -> str:
    """Return a clean 0–10 score string (blank when unset/invalid)."""
    text = _text(value)
    if not text:
        return ""
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return ""
    number = float(match.group(0))
    number = max(0.0, min(float(MAX_INDICATOR_SCORE), number))
    return str(int(number)) if float(number).is_integer() else str(round(number, 1))


def _normalize_field_map(fields: Any) -> dict[str, Any]:
    raw = dict(fields) if isinstance(fields, dict) else {}
    out: dict[str, Any] = {key: _text(raw.get(key)) for key in FIELD_KEYS}
    # Common aliases from AI/import → canonical keys.
    if not out["college"]:
        out["college"] = _text(raw.get("二级学院") or raw.get("所在二级学院") or raw.get("department"))
    if not out["teacher_title"]:
        out["teacher_title"] = _text(raw.get("职称") or raw.get("title"))
    if not out["evaluate_date"]:
        out["evaluate_date"] = _text(raw.get("评价时间") or raw.get("date"))
    if not out["school"]:
        out["school"] = DEFAULT_SCHOOL_NAME
    return out


def normalize_evaluation_payload(fields: Any, items: Any, analysis: Any = "") -> dict[str, Any]:
    """Snap raw teacher/AI/import input onto the canonical 评学表 shape.

    Returns ``{"fields", "items", "analysis", "score_total", "rating"}``. The 10
    indicators are always the fixed template rows (in order); only their scores are
    taken from ``items`` (matched by leading number, else by position).
    """
    norm_fields = _normalize_field_map(fields)
    provided = items if isinstance(items, list) else []

    # Map any provided scores by leading indicator number (1..10), else positionally.
    by_number: dict[int, str] = {}
    positional: list[str] = []
    for entry in provided:
        if isinstance(entry, dict):
            score = _clamp_score(entry.get("score"))
            text = _text(entry.get("indicator") or entry.get("text"))
        else:
            score = _clamp_score(entry)
            text = ""
        num_match = re.match(r"\s*(\d{1,2})", text)
        if num_match:
            by_number[int(num_match.group(1))] = score
        positional.append(score)

    norm_items: list[dict[str, Any]] = []
    for index, (group, indicator) in enumerate(EVALUATION_INDICATORS):
        number = index + 1
        if number in by_number:
            score = by_number[number]
        elif index < len(positional):
            score = positional[index]
        else:
            score = ""
        norm_items.append(
            {"group": group, "indicator": indicator, "max_score": MAX_INDICATOR_SCORE, "score": score}
        )

    score_total = _sum_scores(norm_items)
    return {
        "fields": norm_fields,
        "items": norm_items,
        "analysis": _text(analysis),
        "score_total": score_total,
        "rating": compute_rating(score_total) if _all_scored(norm_items) else "",
    }


def _sum_scores(items: list[dict[str, Any]]) -> float:
    total = 0.0
    for item in items or []:
        try:
            total += float(str(item.get("score") or "").strip() or 0)
        except (TypeError, ValueError):
            continue
    return total


def _all_scored(items: list[dict[str, Any]]) -> bool:
    return bool(items) and all(_text(item.get("score")) for item in items)


def _score_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(round(value, 1))


def _format_cn_date(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    match = re.search(r"(20\d{2})\D+(\d{1,2})\D+(\d{1,2})", text)
    if not match:
        return text
    return f"{match.group(1)}年{int(match.group(2)):02d}月{int(match.group(3)):02d}日"


# ---------------------------------------------------------------------------
# Field auto-fill from a class offering
# ---------------------------------------------------------------------------
def build_fields_from_offering(
    conn: sqlite3.Connection, class_offering_id: int, *, teacher: dict[str, Any]
) -> dict[str, Any]:
    """Auto-fill template fields from offering → course → class → teacher + org."""
    org = teacher_scope(conn, int(teacher["id"]))
    fields: dict[str, Any] = {
        "school": _text(org.get("school_name")) or DEFAULT_SCHOOL_NAME,
        "college": _text(org.get("college")),
        "teacher_name": _text(teacher.get("name") or teacher.get("username")),
        "evaluate_date": datetime.now().strftime("%Y年%m月%d日"),
    }
    offering_cols = _table_columns(conn, "class_offerings")
    course_cols = _table_columns(conn, "courses")
    academic_teaching_class_expr = (
        "o.academic_teaching_class_name" if "academic_teaching_class_name" in offering_cols else "''"
    )
    academic_course_code_expr = "co.academic_course_code" if "academic_course_code" in course_cols else "''"
    row = conn.execute(
        f"""
        SELECT o.semester AS semester,
               {academic_teaching_class_expr} AS academic_teaching_class_name,
               co.name AS course_name,
               {academic_course_code_expr} AS academic_course_code,
               co.college AS course_college,
               co.department AS course_department,
               co.school_name AS course_school_name,
               cl.name AS class_name,
               cl.academic_class_name AS academic_class_name,
               cl.academic_major AS class_academic_major,
               cl.major AS class_major,
               cl.department AS class_department,
               cl.description AS description,
               cl.academic_metadata_json AS academic_metadata_json,
               t.department AS teacher_department
        FROM class_offerings o
        LEFT JOIN courses co ON co.id = o.course_id
        LEFT JOIN classes cl ON cl.id = o.class_id
        LEFT JOIN teachers t ON t.id = o.teacher_id
        WHERE o.id = ?
        LIMIT 1
        """,
        (int(class_offering_id),),
    ).fetchone()
    if row:
        row = dict(row)
        fields["course_name"] = _text(row.get("course_name"))
        fields["class_name"] = resolve_offering_display_class_name(
            conn,
            teacher_id=int(teacher["id"]),
            row={
                **row,
                "display_class_name": build_academic_class_label(row),
            },
        )
        if row.get("course_college"):
            fields["college"] = _text(row.get("course_college")) or fields["college"]
        if row.get("course_school_name"):
            fields["school"] = _text(row.get("course_school_name"))
        semester_text = _text(row.get("semester"))
        year_match = re.search(r"(20\d{2})\s*[-—－]\s*(20\d{2})", semester_text)
        if year_match:
            fields["academic_year"] = f"{year_match.group(1)}-{year_match.group(2)}"
        if re.search(r"第一|(?:^|[-_\s])1(?:$|[-_\s])", semester_text):
            fields["semester"] = "第一学期"
        elif re.search(r"第二|(?:^|[-_\s])2(?:$|[-_\s])", semester_text):
            fields["semester"] = "第二学期"
        elif semester_text:
            fields["semester"] = semester_text
    return fields


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now().isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _normalize_tags(tags: Any) -> list[str]:
    if isinstance(tags, str):
        tags = _load(tags, [])
    if not isinstance(tags, list):
        return []
    seen: list[str] = []
    for tag in tags:
        text = _text(tag)
        if text and text not in seen:
            seen.append(text)
    return seen[:24]


def create_evaluation(
    conn: sqlite3.Connection,
    *,
    teacher: dict[str, Any],
    title: str,
    fields: Any = None,
    items: Any = None,
    analysis: Any = "",
    course_id: int | None = None,
    class_offering_id: int | None = None,
    source_type: str = "blank",
    status: str = "draft",
    scope_level: str = SCOPE_PRIVATE,
    tags: Any = None,
    ai_gen_task_id: str | None = None,
    ai_gen_status: str | None = None,
    ai_gen_progress: Any = None,
    inherited_from: str | None = None,
) -> str:
    ensure_teacher_evaluation_schema(conn)
    evaluation_id = _new_id()
    org = teacher_scope(conn, int(teacher["id"]))
    normalized = normalize_evaluation_payload(fields or {}, items or [], analysis)
    now = _now()
    conn.execute(
        """
        INSERT INTO teacher_evaluations (
            id, teacher_id, title, course_id, class_offering_id,
            fields_json, items_json, analysis,
            tags_json, scope_level, source_type, status,
            ai_gen_task_id, ai_gen_status, ai_gen_error, ai_gen_progress,
            inherited_from, school_code, school_name, college, department,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evaluation_id,
            int(teacher["id"]),
            _text(title) or "教师评学表",
            int(course_id) if course_id else None,
            int(class_offering_id) if class_offering_id else None,
            _dump(normalized["fields"]),
            _dump(normalized["items"]),
            normalized["analysis"],
            _dump(_normalize_tags(tags)),
            normalize_scope_level(scope_level),
            source_type,
            status,
            ai_gen_task_id,
            ai_gen_status,
            None,
            _dump(ai_gen_progress or {}),
            inherited_from,
            _text(org.get("school_code")),
            _text(org.get("school_name")),
            _text(org.get("college")),
            _text(org.get("department")),
            now,
            now,
        ),
    )
    return evaluation_id


def get_evaluation(conn: sqlite3.Connection, evaluation_id: str) -> dict[str, Any] | None:
    ensure_teacher_evaluation_schema(conn)
    row = conn.execute("SELECT * FROM teacher_evaluations WHERE id = ?", (str(evaluation_id),)).fetchone()
    if not row:
        return None
    return _hydrate(dict(row))


def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
    fields = _load(row.get("fields_json"), {}) or {}
    items = _load(row.get("items_json"), []) or []
    normalized = normalize_evaluation_payload(fields, items, row.get("analysis") or "")
    row["fields"] = normalized["fields"]
    row["items"] = normalized["items"]
    row["analysis"] = normalized["analysis"]
    row["notes"] = list(EVALUATION_NOTES)
    row["tags"] = _normalize_tags(_load(row.get("tags_json"), []))
    row["ai_gen_progress_data"] = _load(row.get("ai_gen_progress"), {})
    row["import_preview"] = _load(row.get("import_preview_json"), {})
    row["scope_level"] = normalize_scope_level(row.get("scope_level"))
    row["scope_label"] = scope_label(row["scope_level"])
    row["score_total"] = normalized["score_total"]
    row["rating"] = normalized["rating"]
    row["is_complete"] = _is_complete(normalized)
    return row


def _is_complete(normalized: dict[str, Any]) -> bool:
    """Every score filled + required identity fields present + analysis written."""
    fields = normalized.get("fields") or {}
    required = ("course_name", "class_name", "college", "teacher_name", "evaluate_date")
    if not all(_text(fields.get(key)) for key in required):
        return False
    if not _all_scored(normalized.get("items") or []):
        return False
    return bool(_text(normalized.get("analysis")))


def missing_fields(evaluation: dict[str, Any]) -> list[str]:
    """Human-readable list of what still blocks a clean export."""
    fields = evaluation.get("fields") or {}
    labels = {
        "course_name": "课程名称",
        "class_name": "授课班级",
        "college": "所在二级学院",
        "teacher_name": "任课教师",
        "evaluate_date": "评价时间",
    }
    missing = [label for key, label in labels.items() if not _text(fields.get(key))]
    items = evaluation.get("items") or []
    unscored = [item for item in items if not _text(item.get("score"))]
    if unscored:
        missing.append(f"{len(unscored)} 项评价得分")
    if not _text(evaluation.get("analysis")):
        missing.append("学习情况分析与教学改革建议")
    return missing


def list_evaluations(conn: sqlite3.Connection, *, teacher: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_teacher_evaluation_schema(conn)
    teacher_id = int(teacher["id"])
    is_super = is_super_admin_teacher(conn, teacher_id)
    viewer = teacher_scope(conn, teacher_id)
    where_sql, params = visibility_where(viewer, teacher_id, is_super_admin=is_super, alias="te")
    rows = conn.execute(
        f"""
        SELECT te.*, t.name AS owner_teacher_name
        FROM teacher_evaluations te
        LEFT JOIN teachers t ON t.id = te.teacher_id
        WHERE {where_sql}
        ORDER BY te.updated_at DESC, te.id DESC
        """,
        params,
    ).fetchall()
    result: list[dict[str, Any]] = []
    for raw in rows:
        row = _hydrate(dict(raw))
        row["is_owned"] = int(row.get("teacher_id") or 0) == teacher_id
        row["can_manage"] = row["is_owned"] or is_super
        result.append(serialize_card(row))
    return result


def serialize_card(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("fields") or {}
    return {
        "id": row["id"],
        "title": row.get("title") or "教师评学表",
        "class_offering_id": row.get("class_offering_id"),
        "school": fields.get("school") or row.get("school_name") or "",
        "school_name": row.get("school_name") or fields.get("school") or "",
        "course_name": fields.get("course_name") or "",
        "class_name": fields.get("class_name") or "",
        "college": fields.get("college") or "",
        "department": row.get("department") or "",
        "teacher_name": fields.get("teacher_name") or "",
        "semester_label": " ".join(
            p for p in (fields.get("academic_year") or "", fields.get("semester") or "") if p
        ).strip(),
        "score_total": row.get("score_total") or 0,
        "rating": row.get("rating") or "",
        "is_complete": bool(row.get("is_complete")),
        "tags": row.get("tags") or [],
        "scope_level": row.get("scope_level"),
        "scope_label": row.get("scope_label"),
        "source_type": row.get("source_type") or "blank",
        "status": row.get("status") or "draft",
        "ai_gen_status": row.get("ai_gen_status") or "",
        "ai_gen_error": row.get("ai_gen_error") or "",
        "ai_gen_progress": row.get("ai_gen_progress_data") or {},
        "import_summary": build_process_import_summary(row),
        "is_owned": bool(row.get("is_owned")),
        "can_manage": bool(row.get("can_manage")),
        "owner_teacher_name": row.get("owner_teacher_name") or "",
        "inherited_from": row.get("inherited_from") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def update_content(
    conn: sqlite3.Connection,
    evaluation_id: str,
    *,
    fields: Any,
    items: Any,
    analysis: Any = "",
    status: str | None = None,
) -> dict[str, Any]:
    ensure_teacher_evaluation_schema(conn)
    normalized = normalize_evaluation_payload(fields or {}, items or [], analysis)
    set_fields = ["fields_json = ?", "items_json = ?", "analysis = ?", "updated_at = ?"]
    params: list[Any] = [
        _dump(normalized["fields"]),
        _dump(normalized["items"]),
        normalized["analysis"],
        _now(),
    ]
    if status is not None:
        set_fields.append("status = ?")
        params.append(status)
    params.append(str(evaluation_id))
    conn.execute(f"UPDATE teacher_evaluations SET {', '.join(set_fields)} WHERE id = ?", params)
    return normalized


def update_analysis_only(
    conn: sqlite3.Connection,
    evaluation_id: str,
    *,
    analysis: Any,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Update only the free-text analysis, preserving fields and scores."""
    ensure_teacher_evaluation_schema(conn)
    set_fields = ["analysis = ?", "updated_at = ?"]
    params: list[Any] = [_text(analysis), _now()]
    if status is not None:
        set_fields.append("status = ?")
        params.append(status)
    params.append(str(evaluation_id))
    conn.execute(f"UPDATE teacher_evaluations SET {', '.join(set_fields)} WHERE id = ?", params)
    return get_evaluation(conn, evaluation_id)


def update_attributes(
    conn: sqlite3.Connection,
    evaluation_id: str,
    *,
    title: str | None = None,
    scope_level: str | None = None,
    course_id: int | None = None,
    class_offering_id: int | None = None,
) -> None:
    ensure_teacher_evaluation_schema(conn)
    set_fields: list[str] = []
    params: list[Any] = []
    if title is not None:
        set_fields.append("title = ?")
        params.append(_text(title) or "教师评学表")
    if scope_level is not None:
        set_fields.append("scope_level = ?")
        params.append(normalize_scope_level(scope_level))
    if course_id is not None:
        set_fields.append("course_id = ?")
        params.append(int(course_id) if course_id else None)
    if class_offering_id is not None:
        set_fields.append("class_offering_id = ?")
        params.append(int(class_offering_id) if class_offering_id else None)
    if not set_fields:
        return
    set_fields.append("updated_at = ?")
    params.append(_now())
    params.append(str(evaluation_id))
    conn.execute(f"UPDATE teacher_evaluations SET {', '.join(set_fields)} WHERE id = ?", params)


def update_tags(conn: sqlite3.Connection, evaluation_id: str, tags: Any) -> list[str]:
    ensure_teacher_evaluation_schema(conn)
    normalized = _normalize_tags(tags)
    conn.execute(
        "UPDATE teacher_evaluations SET tags_json = ?, updated_at = ? WHERE id = ?",
        (_dump(normalized), _now(), str(evaluation_id)),
    )
    return normalized


def delete_evaluation(conn: sqlite3.Connection, evaluation_id: str) -> None:
    ensure_teacher_evaluation_schema(conn)
    conn.execute("DELETE FROM teacher_evaluations WHERE id = ?", (str(evaluation_id),))


def set_generation_status(
    conn: sqlite3.Connection,
    evaluation_id: str,
    *,
    status: str | None = None,
    ai_gen_status: str | None = None,
    ai_gen_error: str | None = None,
    progress: Any = None,
    task_id: str | None = None,
    import_preview: Any = None,
) -> None:
    ensure_teacher_evaluation_schema(conn)
    set_fields: list[str] = []
    params: list[Any] = []
    if status is not None:
        set_fields.append("status = ?")
        params.append(status)
    if ai_gen_status is not None:
        set_fields.append("ai_gen_status = ?")
        params.append(ai_gen_status)
    if ai_gen_error is not None:
        set_fields.append("ai_gen_error = ?")
        params.append(ai_gen_error)
    if progress is not None:
        set_fields.append("ai_gen_progress = ?")
        params.append(_dump(progress))
    if task_id is not None:
        set_fields.append("ai_gen_task_id = ?")
        params.append(task_id)
    if import_preview is not None:
        set_fields.append("import_preview_json = ?")
        params.append(_dump(import_preview))
    if not set_fields:
        return
    set_fields.append("updated_at = ?")
    params.append(_now())
    params.append(str(evaluation_id))
    conn.execute(f"UPDATE teacher_evaluations SET {', '.join(set_fields)} WHERE id = ?", params)


def apply_generated_payload(
    conn: sqlite3.Connection,
    evaluation_id: str,
    *,
    fields: Any,
    items: Any,
    analysis: Any,
    import_preview: dict[str, Any] | None = None,
    title: str | None = None,
    ai_gen_status: str = "completed",
    ai_gen_error: str = "",
) -> dict[str, Any]:
    normalized = update_content(conn, evaluation_id, fields=fields, items=items, analysis=analysis, status="ready")
    if title:
        update_attributes(conn, evaluation_id, title=title)
    set_generation_status(
        conn,
        evaluation_id,
        ai_gen_status=ai_gen_status,
        ai_gen_error=ai_gen_error,
        import_preview=import_preview if import_preview is not None else {},
        progress={"done": 1, "total": 1, "current_label": "完成"},
    )
    return normalized


def clone_for_inherit(conn: sqlite3.Connection, source_id: str, *, teacher: dict[str, Any]) -> str:
    source = get_evaluation(conn, source_id)
    if not source:
        raise ValueError("教师评学表不存在")
    org = teacher_scope(conn, int(teacher["id"]))
    fields = dict(source.get("fields") or {})
    fields["teacher_name"] = _text(teacher.get("name") or teacher.get("username"))
    fields["school"] = _text(org.get("school_name")) or fields.get("school", "")
    fields["college"] = _text(org.get("college")) or fields.get("college", "")
    new_title = f"{source.get('title') or '教师评学表'}（继承）"
    return create_evaluation(
        conn,
        teacher=teacher,
        title=new_title,
        fields=fields,
        items=source.get("items"),
        analysis=source.get("analysis"),
        source_type=source.get("source_type") or "blank",
        status="ready",
        scope_level=SCOPE_PRIVATE,
        tags=source.get("tags"),
        inherited_from=str(source_id),
    )


# ---------------------------------------------------------------------------
# Export + preview
# ---------------------------------------------------------------------------
def build_export_payload(evaluation: dict[str, Any]) -> dict[str, Any]:
    fields = dict(evaluation.get("fields") or {})
    items = evaluation.get("items") or []
    score_total = _sum_scores(items)
    rating = compute_rating(score_total) if _all_scored(items) else _text(evaluation.get("rating"))
    return {
        "document_group": "process_material",
        "document_type": "evaluation_sheet",
        "document_type_label": "教师评学表",
        "metadata": fields,
        "content_markdown": "",
        "tables": [],
        "export_payload": {
            "template_key": "evaluation_sheet",
            "document_type": "evaluation_sheet",
            "fields": fields,
            "structured": {
                "indicators": items,
                "score_total": _score_text(score_total),
                "rating": rating,
                "analysis": _text(evaluation.get("analysis")),
                "notes": list(EVALUATION_NOTES),
            },
        },
    }


def export_evaluation_artifact(evaluation: dict[str, Any], *, requested_format: str = "docx"):
    payload = build_export_payload(evaluation)
    base_title = (evaluation.get("title") or "教师评学表").replace("/", "_").replace("\\", "_")
    return build_material_export_artifact(
        payload, fallback_filename=base_title, requested_format=requested_format
    )


def render_preview_html(evaluation: dict[str, Any], *, user: dict[str, Any]) -> str:
    """Image-backed preview generated from the same DOCX used for export."""
    title = _text(evaluation.get("title")) or "教师评学表"
    missing = missing_fields(evaluation)
    download_disabled_reason = (
        "评学表尚未填写完整，请先补全后再导出：" + "、".join(missing)
        if missing
        else ""
    )
    try:
        artifact = export_evaluation_artifact(evaluation, requested_format="docx")
        job = document_render_service.render_artifact(
            artifact.content,
            filename=artifact.filename,
            media_type=artifact.media_type,
            source_format="docx",
        )
    except (RuntimeError, DocumentRenderError) as exc:
        return document_render_service.render_error_html(title=title, message=str(exc))
    return document_render_service.render_preview_html(
        job,
        title=title,
        user=user,
        eyebrow="教师评学表 · 导出一致预览",
        download_label="下载 Word",
        download_disabled_reason=download_disabled_reason,
    )
