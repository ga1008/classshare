from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from .academic_service import china_now


SOURCE_SUBMISSION = "submission"
SOURCE_BLOG_POST = "blog_post"
SOURCE_CERTIFICATE = "certificate"
SOURCE_MANUAL = "manual"
SOURCE_TYPES = {SOURCE_SUBMISSION, SOURCE_BLOG_POST, SOURCE_CERTIFICATE, SOURCE_MANUAL}

VISIBILITY_PRIVATE = "private"
VISIBILITY_CLASS = "class"
VISIBILITY_TEACHERS = "teachers"
VISIBILITIES = {VISIBILITY_PRIVATE, VISIBILITY_CLASS, VISIBILITY_TEACHERS}

ABILITY_TAGS = (
    "知识掌握",
    "实践应用",
    "表达总结",
    "复盘改进",
    "自主学习",
    "协作贡献",
)

ARTIFACT_LABELS = {
    "homework": "作业",
    "exam": "考试",
    "lab": "实验",
    "essay": "文章",
    "project": "项目",
    "certificate": "证书",
    "reflection": "复盘",
}

VISIBILITY_OPTIONS = [
    {"value": VISIBILITY_PRIVATE, "label": "仅自己可见", "description": "默认保存为私有成长资料"},
    {"value": VISIBILITY_CLASS, "label": "班级可见", "description": "适合分享给同班同学参考"},
    {"value": VISIBILITY_TEACHERS, "label": "任课教师可见", "description": "用于课程复盘和教师评价"},
]


def _now_iso() -> str:
    return china_now().replace(tzinfo=None, microsecond=0).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_loads(value: Any, default: Any) -> Any:
    if isinstance(value, type(default)):
        return value
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _clean_text(value: Any, *, limit: int = 240, multiline: bool = False) -> str:
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if multiline:
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
    else:
        text = re.sub(r"\s+", " ", text)
    if limit > 0 and len(text) > limit:
        return text[:limit].rstrip()
    return text


def _plain_markdown(value: Any, *, limit: int = 220) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"`{3}[\s\S]*?`{3}", " ", text)
    text = re.sub(r"!\[[^\]]*]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*_~`|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: max(limit - 3, 0)].rstrip() + "..."
    return text


def _datetime_sort_value(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return datetime.fromisoformat(text.replace("Z", "")).timestamp()
    except ValueError:
        return 0


def _normalize_source_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in SOURCE_TYPES:
        raise ValueError("作品来源类型不正确。")
    return normalized


def _normalize_visibility(value: Any) -> str:
    normalized = str(value or VISIBILITY_PRIVATE).strip().lower()
    return normalized if normalized in VISIBILITIES else VISIBILITY_PRIVATE


def _normalize_ability_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[,，、\n]+", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    normalized: list[str] = []
    seen: set[str] = set()
    allowed = set(ABILITY_TAGS)
    for item in raw_items:
        tag = _clean_text(item, limit=16)
        if not tag or tag in seen:
            continue
        if tag not in allowed and len(tag) > 12:
            tag = tag[:12]
        seen.add(tag)
        normalized.append(tag)
        if len(normalized) >= 8:
            break
    return normalized


def _source_key(source_type: str, source_id: Any) -> str:
    return f"{source_type}:{str(source_id or '').strip()}"


def _score_label(value: Any) -> str:
    if value in (None, ""):
        return ""
    score = _safe_float(value)
    return str(int(score)) if float(score).is_integer() else f"{score:.1f}".rstrip("0").rstrip(".")


def _artifact_label(kind: Any) -> str:
    return ARTIFACT_LABELS.get(str(kind or "").strip().lower(), "作品")


def _load_existing_source_keys(conn: sqlite3.Connection, student_id: int) -> set[str]:
    rows = conn.execute(
        """
        SELECT source_type, source_id
        FROM student_portfolio_items
        WHERE student_id = ?
        """,
        (int(student_id),),
    ).fetchall()
    return {_source_key(row["source_type"], row["source_id"]) for row in rows}


def _submission_artifact_type(row: dict[str, Any]) -> str:
    if row.get("exam_paper_id"):
        return "exam"
    title = str(row.get("assignment_title") or "").lower()
    if any(token in title for token in ("实验", "lab", "实训")):
        return "lab"
    if any(token in title for token in ("项目", "project", "设计")):
        return "project"
    return "homework"


def _submission_summary(row: dict[str, Any]) -> str:
    score_text = _score_label(row.get("score"))
    feedback = _plain_markdown(row.get("feedback_md"), limit=140)
    if score_text and feedback:
        return f"得分 {score_text}。{feedback}"
    if score_text:
        return f"这份提交已经完成批改，得分 {score_text}。"
    return feedback or "这是一份已经提交的课程成果，可以补充反思后收入成长档案。"


def _candidate_from_submission(row: dict[str, Any], *, selected: bool) -> dict[str, Any]:
    artifact_type = _submission_artifact_type(row)
    score = row.get("score")
    score_value = _safe_float(score, -1)
    recommended_reason = "已完成批改"
    if score_value >= 90:
        recommended_reason = "高分成果，适合展示"
    elif score_value >= 80:
        recommended_reason = "表现稳定，可作为阶段成果"
    elif row.get("feedback_md"):
        recommended_reason = "包含反馈，可整理成复盘作品"
    return {
        "source_type": SOURCE_SUBMISSION,
        "source_id": str(row["submission_id"]),
        "source_key": _source_key(SOURCE_SUBMISSION, row["submission_id"]),
        "title": str(row.get("assignment_title") or "课程提交"),
        "summary": _submission_summary(row),
        "artifact_type": artifact_type,
        "artifact_label": _artifact_label(artifact_type),
        "course_id": _safe_int(row.get("course_id")),
        "class_offering_id": _safe_int(row.get("class_offering_id")),
        "course_name": str(row.get("course_name") or "课程"),
        "class_name": str(row.get("class_name") or ""),
        "created_at": str(row.get("submitted_at") or ""),
        "score": score,
        "score_label": _score_label(score),
        "href": f"/submission/{int(row['submission_id'])}",
        "selected": selected,
        "recommended_reason": recommended_reason,
    }


def _candidate_from_blog(row: dict[str, Any], *, selected: bool) -> dict[str, Any]:
    return {
        "source_type": SOURCE_BLOG_POST,
        "source_id": str(row["id"]),
        "source_key": _source_key(SOURCE_BLOG_POST, row["id"]),
        "title": str(row.get("title") or "学习文章"),
        "summary": str(row.get("summary") or _plain_markdown(row.get("content_md"), limit=160) or "这篇学习记录可以作为表达与总结能力的证据。"),
        "artifact_type": "essay",
        "artifact_label": _artifact_label("essay"),
        "course_id": 0,
        "class_offering_id": 0,
        "course_name": "博客",
        "class_name": "",
        "created_at": str(row.get("created_at") or ""),
        "score": None,
        "score_label": "",
        "href": f"/blog?post={int(row['id'])}",
        "selected": selected,
        "recommended_reason": "公开表达与学习总结",
    }


def _candidate_from_certificate(row: dict[str, Any], *, selected: bool) -> dict[str, Any]:
    return {
        "source_type": SOURCE_CERTIFICATE,
        "source_id": str(row["id"]),
        "source_key": _source_key(SOURCE_CERTIFICATE, row["id"]),
        "title": str(row.get("title") or row.get("level_name") or "课程证书"),
        "summary": f"{row.get('course_name') or '课程'} 达到 {row.get('level_name') or '阶段'}，证书编号 {row.get('certificate_code') or ''}".strip(),
        "artifact_type": "certificate",
        "artifact_label": _artifact_label("certificate"),
        "course_id": _safe_int(row.get("course_id")),
        "class_offering_id": _safe_int(row.get("class_offering_id")),
        "course_name": str(row.get("course_name") or "课程"),
        "class_name": str(row.get("class_name") or ""),
        "created_at": str(row.get("issued_at") or ""),
        "score": row.get("tier"),
        "score_label": f"{_safe_int(row.get('tier'))} 层",
        "href": f"/classroom/{int(row['class_offering_id'])}",
        "selected": selected,
        "recommended_reason": "系统认证的阶段里程碑",
    }


def _load_submission_candidate(conn: sqlite3.Connection, student_id: int, source_id: Any) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT s.id AS submission_id,
               s.score,
               s.feedback_md,
               s.submitted_at,
               a.id AS assignment_id,
               a.title AS assignment_title,
               a.exam_paper_id,
               a.class_offering_id,
               a.course_id,
               c.name AS course_name,
               cl.name AS class_name
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        LEFT JOIN classes cl ON cl.id = o.class_id
        WHERE s.id = ?
          AND s.student_pk_id = ?
          AND COALESCE(s.is_absence_score, 0) = 0
        LIMIT 1
        """,
        (_safe_int(source_id), int(student_id)),
    ).fetchone()
    if not row:
        return None
    return _candidate_from_submission(dict(row), selected=False)


def _load_blog_candidate(conn: sqlite3.Connection, student_id: int, source_id: Any) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, title, content_md, summary, visible_class_id, created_at
        FROM blog_posts
        WHERE id = ?
          AND author_role = 'student'
          AND author_user_pk = ?
          AND status IN ('published', 'draft')
        LIMIT 1
        """,
        (_safe_int(source_id), int(student_id)),
    ).fetchone()
    if not row:
        return None
    return _candidate_from_blog(dict(row), selected=False)


def _load_certificate_candidate(conn: sqlite3.Connection, student_id: int, source_id: Any) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT lc.id,
               lc.class_offering_id,
               lc.student_id,
               lc.level_name,
               lc.tier,
               lc.title,
               lc.certificate_code,
               lc.issued_at,
               c.id AS course_id,
               c.name AS course_name,
               cl.name AS class_name
        FROM learning_certificates lc
        JOIN class_offerings o ON o.id = lc.class_offering_id
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE lc.id = ?
          AND lc.student_id = ?
        LIMIT 1
        """,
        (_safe_int(source_id), int(student_id)),
    ).fetchone()
    if not row:
        return None
    return _candidate_from_certificate(dict(row), selected=False)


def _load_source_candidate(conn: sqlite3.Connection, student_id: int, source_type: str, source_id: Any) -> dict[str, Any] | None:
    if source_type == SOURCE_SUBMISSION:
        return _load_submission_candidate(conn, student_id, source_id)
    if source_type == SOURCE_BLOG_POST:
        return _load_blog_candidate(conn, student_id, source_id)
    if source_type == SOURCE_CERTIFICATE:
        return _load_certificate_candidate(conn, student_id, source_id)
    return None


def list_portfolio_candidates(
    conn: sqlite3.Connection,
    student_id: int,
    *,
    limit: int = 18,
) -> list[dict[str, Any]]:
    selected_keys = _load_existing_source_keys(conn, int(student_id))
    candidates: list[dict[str, Any]] = []

    cert_rows = conn.execute(
        """
        SELECT lc.id,
               lc.class_offering_id,
               lc.student_id,
               lc.level_name,
               lc.tier,
               lc.title,
               lc.certificate_code,
               lc.issued_at,
               c.id AS course_id,
               c.name AS course_name,
               cl.name AS class_name
        FROM learning_certificates lc
        JOIN class_offerings o ON o.id = lc.class_offering_id
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE lc.student_id = ?
        ORDER BY lc.tier DESC, lc.issued_at DESC, lc.id DESC
        LIMIT 12
        """,
        (int(student_id),),
    ).fetchall()
    for row in cert_rows:
        item = _candidate_from_certificate(dict(row), selected=_source_key(SOURCE_CERTIFICATE, row["id"]) in selected_keys)
        if not item["selected"]:
            candidates.append(item)

    submission_rows = conn.execute(
        """
        SELECT s.id AS submission_id,
               s.score,
               s.feedback_md,
               s.submitted_at,
               a.id AS assignment_id,
               a.title AS assignment_title,
               a.exam_paper_id,
               a.class_offering_id,
               a.course_id,
               c.name AS course_name,
               cl.name AS class_name
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        LEFT JOIN classes cl ON cl.id = o.class_id
        WHERE s.student_pk_id = ?
          AND COALESCE(s.is_absence_score, 0) = 0
          AND (s.status = 'graded' OR s.score IS NOT NULL OR s.feedback_md IS NOT NULL)
        ORDER BY
          CASE WHEN s.score IS NOT NULL THEN s.score ELSE 0 END DESC,
          COALESCE(s.submitted_at, '') DESC,
          s.id DESC
        LIMIT 28
        """,
        (int(student_id),),
    ).fetchall()
    for row in submission_rows:
        item = _candidate_from_submission(dict(row), selected=_source_key(SOURCE_SUBMISSION, row["submission_id"]) in selected_keys)
        if item["selected"]:
            continue
        if item["score"] is not None and _safe_float(item["score"]) >= 75:
            candidates.append(item)
        elif item["summary"] and len(candidates) < limit:
            candidates.append(item)

    blog_rows = conn.execute(
        """
        SELECT id, title, content_md, summary, visible_class_id, created_at
        FROM blog_posts
        WHERE author_role = 'student'
          AND author_user_pk = ?
          AND status = 'published'
        ORDER BY updated_at DESC, id DESC
        LIMIT 12
        """,
        (int(student_id),),
    ).fetchall()
    for row in blog_rows:
        item = _candidate_from_blog(dict(row), selected=_source_key(SOURCE_BLOG_POST, row["id"]) in selected_keys)
        if not item["selected"]:
            candidates.append(item)

    candidates.sort(
        key=lambda item: (
            item["artifact_type"] != "certificate",
            -_safe_float(item.get("score"), 0),
            -_datetime_sort_value(item.get("created_at")),
        )
    )
    return candidates[: max(1, min(int(limit), 40))]


def _load_portfolio_items(conn: sqlite3.Connection, student_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT i.*,
               r.reflection_text,
               r.ability_tags_json,
               r.evidence_notes,
               r.updated_at AS reflection_updated_at
        FROM student_portfolio_items i
        LEFT JOIN student_portfolio_reflections r ON r.portfolio_item_id = i.id
        WHERE i.student_id = ?
        ORDER BY i.featured DESC,
                 i.sort_order ASC,
                 COALESCE(i.updated_at, i.created_at) DESC,
                 i.id DESC
        """,
        (int(student_id),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        metadata = _json_loads(item.get("metadata_json"), {})
        tags = _normalize_ability_tags(_json_loads(item.get("ability_tags_json"), []))
        item.update(
            {
                "metadata": metadata,
                "artifact_label": _artifact_label(item.get("artifact_type")),
                "visibility_label": next(
                    (option["label"] for option in VISIBILITY_OPTIONS if option["value"] == item.get("visibility")),
                    "仅自己可见",
                ),
                "featured": bool(item.get("featured")),
                "teacher_recommended": bool(item.get("teacher_recommended")),
                "student_reflection": str(item.get("reflection_text") or ""),
                "ability_tags": tags,
                "ability_tags_text": "，".join(tags),
                "evidence_notes": str(item.get("evidence_notes") or ""),
                "source_key": _source_key(item.get("source_type"), item.get("source_id")),
                "href": _source_href(item),
                "score_label": str(metadata.get("score_label") or ""),
                "course_name": str(metadata.get("course_name") or ""),
                "class_name": str(metadata.get("class_name") or ""),
            }
        )
        items.append(item)
    return items


def _source_href(item: dict[str, Any]) -> str:
    source_type = str(item.get("source_type") or "")
    source_id = item.get("source_id")
    if source_type == SOURCE_SUBMISSION:
        return f"/submission/{_safe_int(source_id)}"
    if source_type == SOURCE_BLOG_POST:
        return f"/blog?post={_safe_int(source_id)}"
    if source_type == SOURCE_CERTIFICATE and _safe_int(item.get("class_offering_id")):
        return f"/classroom/{_safe_int(item.get('class_offering_id'))}"
    if _safe_int(item.get("class_offering_id")):
        return f"/classroom/{_safe_int(item.get('class_offering_id'))}"
    return "/profile?section=portfolio"


def _record_growth_event(
    conn: sqlite3.Connection,
    *,
    student_id: int,
    class_offering_id: int | None = None,
    event_type: str,
    source_type: str,
    source_id: Any,
    title: str,
    description: str = "",
    importance: str = "normal",
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO student_growth_events (
            student_id, class_offering_id, event_type, source_type, source_id,
            title, description, occurred_at, importance, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(student_id),
            int(class_offering_id) if class_offering_id else None,
            _clean_text(event_type, limit=40),
            _clean_text(source_type, limit=40),
            str(source_id or ""),
            _clean_text(title, limit=180),
            _clean_text(description, limit=360),
            _now_iso(),
            importance if importance in {"normal", "milestone", "highlight"} else "normal",
            _json_dumps(metadata or {}),
        ),
    )


def add_portfolio_item(
    conn: sqlite3.Connection,
    student_id: int,
    *,
    source_type: str,
    source_id: Any,
    featured: bool = False,
) -> dict[str, Any]:
    normalized_source_type = _normalize_source_type(source_type)
    if normalized_source_type == SOURCE_MANUAL:
        raise ValueError("暂不支持空白手动作品，请先从作业、博客或证书加入。")
    candidate = _load_source_candidate(conn, int(student_id), normalized_source_type, source_id)
    if not candidate:
        raise ValueError("作品来源不存在，或不属于当前学生。")

    metadata = {
        "source_href": candidate.get("href") or "",
        "source_created_at": candidate.get("created_at") or "",
        "score": candidate.get("score"),
        "score_label": candidate.get("score_label") or "",
        "course_name": candidate.get("course_name") or "",
        "class_name": candidate.get("class_name") or "",
        "recommended_reason": candidate.get("recommended_reason") or "",
    }
    now = _now_iso()
    cursor = conn.execute(
        """
        INSERT INTO student_portfolio_items (
            student_id, class_offering_id, course_id, source_type, source_id,
            title, summary, artifact_type, visibility, featured, sort_order,
            created_at, updated_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_id, source_type, source_id) DO UPDATE SET
            title = excluded.title,
            summary = CASE
                WHEN TRIM(student_portfolio_items.summary) = '' THEN excluded.summary
                ELSE student_portfolio_items.summary
            END,
            artifact_type = excluded.artifact_type,
            class_offering_id = excluded.class_offering_id,
            course_id = excluded.course_id,
            featured = CASE
                WHEN excluded.featured = 1 THEN 1
                ELSE student_portfolio_items.featured
            END,
            updated_at = excluded.updated_at,
            metadata_json = excluded.metadata_json
        """,
        (
            int(student_id),
            candidate.get("class_offering_id") or None,
            candidate.get("course_id") or None,
            normalized_source_type,
            str(candidate["source_id"]),
            _clean_text(candidate.get("title"), limit=180),
            _clean_text(candidate.get("summary"), limit=800, multiline=True),
            candidate.get("artifact_type") or "homework",
            VISIBILITY_PRIVATE,
            1 if featured else 0,
            100,
            now,
            now,
            _json_dumps(metadata),
        ),
    )
    row = conn.execute(
        """
        SELECT id
        FROM student_portfolio_items
        WHERE student_id = ? AND source_type = ? AND source_id = ?
        LIMIT 1
        """,
        (int(student_id), normalized_source_type, str(candidate["source_id"])),
    ).fetchone()
    item_id = int(row["id"] if row else cursor.lastrowid)
    _record_growth_event(
        conn,
        student_id=int(student_id),
        class_offering_id=candidate.get("class_offering_id") or None,
        event_type="portfolio_added",
        source_type=normalized_source_type,
        source_id=candidate["source_id"],
        title=f"收入成长档案：{candidate['title']}",
        description=candidate.get("recommended_reason") or "",
        importance="highlight" if featured or candidate.get("artifact_type") == "certificate" else "normal",
        metadata={"portfolio_item_id": item_id},
    )
    return {"id": item_id, "item": get_portfolio_item(conn, int(student_id), item_id)}


def get_portfolio_item(conn: sqlite3.Connection, student_id: int, item_id: int) -> dict[str, Any] | None:
    rows = _load_portfolio_items(conn, int(student_id))
    return next((item for item in rows if int(item["id"]) == int(item_id)), None)


def update_portfolio_item(
    conn: sqlite3.Connection,
    student_id: int,
    item_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    item = get_portfolio_item(conn, int(student_id), int(item_id))
    if not item:
        raise ValueError("作品不存在或不属于当前学生。")

    title = _clean_text(payload.get("title", item.get("title")), limit=180)
    summary = _clean_text(payload.get("summary", item.get("summary")), limit=900, multiline=True)
    visibility = _normalize_visibility(payload.get("visibility", item.get("visibility")))
    featured = 1 if payload.get("featured") else 0
    sort_order = max(0, min(_safe_int(payload.get("sort_order"), _safe_int(item.get("sort_order"), 100)), 9999))
    reflection = _clean_text(payload.get("reflection"), limit=1600, multiline=True)
    evidence_notes = _clean_text(payload.get("evidence_notes"), limit=700, multiline=True)
    ability_tags = _normalize_ability_tags(payload.get("ability_tags"))
    now = _now_iso()

    conn.execute(
        """
        UPDATE student_portfolio_items
        SET title = ?,
            summary = ?,
            visibility = ?,
            featured = ?,
            sort_order = ?,
            updated_at = ?
        WHERE id = ? AND student_id = ?
        """,
        (
            title,
            summary,
            visibility,
            featured,
            sort_order,
            now,
            int(item_id),
            int(student_id),
        ),
    )
    conn.execute(
        """
        INSERT INTO student_portfolio_reflections (
            portfolio_item_id, student_id, reflection_text, ability_tags_json,
            evidence_notes, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(portfolio_item_id) DO UPDATE SET
            reflection_text = excluded.reflection_text,
            ability_tags_json = excluded.ability_tags_json,
            evidence_notes = excluded.evidence_notes,
            updated_at = excluded.updated_at
        """,
        (
            int(item_id),
            int(student_id),
            reflection,
            _json_dumps(ability_tags),
            evidence_notes,
            now,
        ),
    )
    return {"item": get_portfolio_item(conn, int(student_id), int(item_id))}


def remove_portfolio_item(conn: sqlite3.Connection, student_id: int, item_id: int) -> int:
    item = get_portfolio_item(conn, int(student_id), int(item_id))
    if not item:
        raise ValueError("作品不存在或不属于当前学生。")
    conn.execute("DELETE FROM student_portfolio_reflections WHERE portfolio_item_id = ?", (int(item_id),))
    cursor = conn.execute(
        "DELETE FROM student_portfolio_items WHERE id = ? AND student_id = ?",
        (int(item_id), int(student_id)),
    )
    _record_growth_event(
        conn,
        student_id=int(student_id),
        class_offering_id=item.get("class_offering_id") or None,
        event_type="portfolio_removed",
        source_type=str(item.get("source_type") or ""),
        source_id=item.get("source_id"),
        title=f"移出成长档案：{item.get('title') or '作品'}",
        importance="normal",
        metadata={"portfolio_item_id": int(item_id)},
    )
    return int(cursor.rowcount or 0)


def _load_certificate_events(conn: sqlite3.Connection, student_id: int, limit: int = 12) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT lc.id,
               lc.class_offering_id,
               lc.title,
               lc.level_name,
               lc.tier,
               lc.issued_at,
               c.name AS course_name
        FROM learning_certificates lc
        JOIN class_offerings o ON o.id = lc.class_offering_id
        JOIN courses c ON c.id = o.course_id
        WHERE lc.student_id = ?
        ORDER BY lc.issued_at DESC, lc.id DESC
        LIMIT ?
        """,
        (int(student_id), int(limit)),
    ).fetchall()
    return [
        {
            "type": "certificate",
            "label": "证书",
            "title": str(row["title"] or row["level_name"] or "获得课程证书"),
            "description": f"{row['course_name']} 达到第 {_safe_int(row['tier'])} 层",
            "occurred_at": str(row["issued_at"] or ""),
            "href": f"/classroom/{int(row['class_offering_id'])}",
            "importance": "milestone",
        }
        for row in rows
    ]


def _load_submission_events(conn: sqlite3.Connection, student_id: int, limit: int = 12) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.id,
               s.score,
               s.submitted_at,
               a.title AS assignment_title,
               c.name AS course_name
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        WHERE s.student_pk_id = ?
          AND COALESCE(s.is_absence_score, 0) = 0
        ORDER BY COALESCE(s.submitted_at, '') DESC, s.id DESC
        LIMIT ?
        """,
        (int(student_id), int(limit)),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        score = row["score"]
        score_text = _score_label(score)
        events.append(
            {
                "type": "submission",
                "label": "提交",
                "title": str(row["assignment_title"] or "完成课程任务"),
                "description": f"{row['course_name']} · 得分 {score_text}" if score_text else str(row["course_name"] or ""),
                "occurred_at": str(row["submitted_at"] or ""),
                "href": f"/submission/{int(row['id'])}",
                "importance": "highlight" if score is not None and _safe_float(score) >= 90 else "normal",
            }
        )
    return events


def _load_review_events(conn: sqlite3.Connection, student_id: int, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT n.submission_id,
               n.question_key,
               n.status,
               n.reviewed_at,
               n.mastered_at,
               n.updated_at,
               a.title AS assignment_title,
               c.name AS course_name
        FROM student_feedback_review_notes n
        JOIN submissions s ON s.id = n.submission_id
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        WHERE n.student_id = ?
          AND n.status IN ('reviewing', 'mastered')
        ORDER BY COALESCE(n.mastered_at, n.reviewed_at, n.updated_at) DESC, n.id DESC
        LIMIT ?
        """,
        (int(student_id), int(limit)),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        mastered = str(row["status"] or "") == "mastered"
        events.append(
            {
                "type": "review",
                "label": "复盘",
                "title": "掌握一条反馈" if mastered else "开始一条反馈复盘",
                "description": f"{row['course_name']} · {row['assignment_title']}",
                "occurred_at": str(row["mastered_at"] or row["reviewed_at"] or row["updated_at"] or ""),
                "href": "/feedback-review",
                "importance": "highlight" if mastered else "normal",
            }
        )
    return events


def _load_blog_events(conn: sqlite3.Connection, student_id: int, limit: int = 8) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, title, summary, created_at
        FROM blog_posts
        WHERE author_role = 'student'
          AND author_user_pk = ?
          AND status = 'published'
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (int(student_id), int(limit)),
    ).fetchall()
    return [
        {
            "type": "blog",
            "label": "文章",
            "title": str(row["title"] or "发布学习文章"),
            "description": str(row["summary"] or "完成一次公开表达"),
            "occurred_at": str(row["created_at"] or ""),
            "href": f"/blog?post={int(row['id'])}",
            "importance": "normal",
        }
        for row in rows
    ]


def _load_portfolio_events(conn: sqlite3.Connection, student_id: int, limit: int = 12) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, event_type, title, description, occurred_at, importance, source_type, source_id
        FROM student_growth_events
        WHERE student_id = ?
        ORDER BY occurred_at DESC, id DESC
        LIMIT ?
        """,
        (int(student_id), int(limit)),
    ).fetchall()
    events = []
    for row in rows:
        events.append(
            {
                "type": str(row["event_type"] or "portfolio"),
                "label": "档案",
                "title": str(row["title"] or "成长档案更新"),
                "description": str(row["description"] or ""),
                "occurred_at": str(row["occurred_at"] or ""),
                "href": "/profile?section=portfolio",
                "importance": str(row["importance"] or "normal"),
            }
        )
    return events


def build_growth_timeline(conn: sqlite3.Connection, student_id: int, *, limit: int = 28) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    events.extend(_load_portfolio_events(conn, int(student_id)))
    events.extend(_load_certificate_events(conn, int(student_id)))
    events.extend(_load_review_events(conn, int(student_id)))
    events.extend(_load_submission_events(conn, int(student_id)))
    events.extend(_load_blog_events(conn, int(student_id)))

    seen: set[tuple[str, str, str]] = set()
    unique_events: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda item: -_datetime_sort_value(item.get("occurred_at"))):
        key = (str(event.get("type")), str(event.get("title")), str(event.get("occurred_at")))
        if key in seen:
            continue
        seen.add(key)
        unique_events.append(event)
        if len(unique_events) >= max(1, min(int(limit), 60)):
            break
    return unique_events


def _load_learning_evidence(conn: sqlite3.Connection, student_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT CASE WHEN lmp.completed = 1 THEN lmp.material_id END) AS material_completed,
               COALESCE(SUM(lmp.active_seconds), 0) AS material_active_seconds,
               COUNT(DISTINCT lc.id) AS certificate_count,
               COALESCE(MAX(lc.tier), 0) AS highest_tier
        FROM students s
        LEFT JOIN class_offerings o ON o.class_id = s.class_id
        LEFT JOIN learning_material_progress lmp
               ON lmp.class_offering_id = o.id AND lmp.student_id = s.id
        LEFT JOIN learning_certificates lc
               ON lc.class_offering_id = o.id AND lc.student_id = s.id
        WHERE s.id = ?
        """,
        (int(student_id),),
    ).fetchone()
    return dict(row) if row else {}


def build_ability_summary(
    conn: sqlite3.Connection,
    student_id: int,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tag_counts = {tag: 0 for tag in ABILITY_TAGS}
    for item in items:
        for tag in item.get("ability_tags") or []:
            if tag in tag_counts:
                tag_counts[tag] += 1

    learning = _load_learning_evidence(conn, int(student_id))
    blog_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM blog_posts
            WHERE author_role = 'student' AND author_user_pk = ? AND status = 'published'
            """,
            (int(student_id),),
        ).fetchone()[0]
        or 0
    )
    mastered_reviews = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM student_feedback_review_notes
            WHERE student_id = ? AND status = 'mastered'
            """,
            (int(student_id),),
        ).fetchone()[0]
        or 0
    )
    high_score_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM submissions
            WHERE student_pk_id = ?
              AND COALESCE(is_absence_score, 0) = 0
              AND score >= 90
            """,
            (int(student_id),),
        ).fetchone()[0]
        or 0
    )

    computed = {
        "知识掌握": _safe_int(learning.get("certificate_count")) + tag_counts["知识掌握"],
        "实践应用": high_score_count + tag_counts["实践应用"],
        "表达总结": blog_count + tag_counts["表达总结"],
        "复盘改进": mastered_reviews + tag_counts["复盘改进"],
        "自主学习": _safe_int(learning.get("material_completed")) + tag_counts["自主学习"],
        "协作贡献": tag_counts["协作贡献"],
    }
    evidence_text = {
        "知识掌握": f"证书 {learning.get('certificate_count') or 0} 枚，最高第 {learning.get('highest_tier') or 0} 层",
        "实践应用": f"90 分以上成果 {high_score_count} 项",
        "表达总结": f"已发布学习文章 {blog_count} 篇",
        "复盘改进": f"已掌握反馈 {mastered_reviews} 条",
        "自主学习": f"完成材料 {learning.get('material_completed') or 0} 份",
        "协作贡献": "可通过小组项目、同伴互评继续沉淀",
    }
    max_value = max(computed.values()) if computed else 0
    return [
        {
            "label": label,
            "value": computed[label],
            "percent": 0 if max_value <= 0 else min(100, int(round(computed[label] / max_value * 100))),
            "evidence": evidence_text[label],
        }
        for label in ABILITY_TAGS
    ]


def build_student_portfolio_context(
    conn: sqlite3.Connection,
    student_id: int,
    *,
    include_candidates: bool = True,
) -> dict[str, Any]:
    items = _load_portfolio_items(conn, int(student_id))
    candidates = list_portfolio_candidates(conn, int(student_id), limit=18) if include_candidates else []
    timeline = build_growth_timeline(conn, int(student_id), limit=30)
    abilities = build_ability_summary(conn, int(student_id), items)
    featured_items = [item for item in items if item.get("featured")]
    if not featured_items:
        featured_items = items[:3]
    certificate_count = sum(1 for item in items if item.get("artifact_type") == "certificate")
    reflection_count = sum(1 for item in items if item.get("student_reflection"))
    teacher_recommended_count = sum(1 for item in items if item.get("teacher_recommended"))
    public_ready_count = sum(1 for item in items if item.get("visibility") in {VISIBILITY_CLASS, VISIBILITY_TEACHERS})
    completion_percent = 0
    if items:
        completion_percent = int(round((reflection_count + len(featured_items)) / (len(items) * 2) * 100))
    next_action = "从候选成果里收入第一件作品"
    if items and reflection_count < len(items):
        next_action = "给已入档作品补一句复盘"
    elif items and not any(item.get("featured") for item in items):
        next_action = "精选 1 件最能代表自己的作品"
    elif items:
        next_action = "继续把新成果沉淀进成长档案"

    return {
        "title": "成长档案",
        "subtitle": "把作业、证书、博客和复盘整理成可回看的学习证据。",
        "items": items,
        "featured_items": featured_items[:6],
        "candidates": candidates,
        "timeline": timeline,
        "abilities": abilities,
        "ability_options": list(ABILITY_TAGS),
        "visibility_options": VISIBILITY_OPTIONS,
        "next_action": next_action,
        "stats": [
            {"label": "入档作品", "value": len(items), "hint": "已选择沉淀的成果"},
            {"label": "精选展示", "value": sum(1 for item in items if item.get("featured")), "hint": "个人首页优先展示"},
            {"label": "证书里程碑", "value": certificate_count, "hint": "来自修为体系"},
            {"label": "反思完成", "value": reflection_count, "hint": f"完整度 {completion_percent}%"},
            {"label": "教师推荐", "value": teacher_recommended_count, "hint": "后续可用于评价"},
            {"label": "可分享", "value": public_ready_count, "hint": "班级或教师可见"},
        ],
        "summary": {
            "item_count": len(items),
            "candidate_count": len(candidates),
            "featured_count": sum(1 for item in items if item.get("featured")),
            "reflection_count": reflection_count,
            "teacher_recommended_count": teacher_recommended_count,
            "completion_percent": completion_percent,
            "href": "/profile?section=portfolio",
            "next_action": next_action,
        },
    }


def build_student_portfolio_summary(conn: sqlite3.Connection, student_id: int) -> dict[str, Any]:
    context = build_student_portfolio_context(conn, int(student_id), include_candidates=False)
    return context["summary"]


def build_teacher_portfolio_snapshot(
    conn: sqlite3.Connection,
    student_id: int,
    *,
    class_offering_ids: list[int] | tuple[int, ...] | set[int] | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    allowed_offerings = {int(item) for item in (class_offering_ids or []) if _safe_int(item)}
    items = _load_portfolio_items(conn, int(student_id))
    visible_items: list[dict[str, Any]] = []
    for item in items:
        if item.get("visibility") not in {VISIBILITY_CLASS, VISIBILITY_TEACHERS}:
            continue
        item_offering_id = _safe_int(item.get("class_offering_id"))
        if allowed_offerings and item_offering_id and item_offering_id not in allowed_offerings:
            continue
        visible_items.append(
            {
                "id": item.get("id"),
                "title": item.get("title") or "作品",
                "summary": item.get("summary") or item.get("student_reflection") or "",
                "artifact_label": item.get("artifact_label") or _artifact_label(item.get("artifact_type")),
                "visibility_label": item.get("visibility_label") or "",
                "course_name": item.get("course_name") or "",
                "student_reflection": item.get("student_reflection") or "",
                "ability_tags": item.get("ability_tags") or [],
                "href": item.get("href") or "/profile?section=portfolio",
                "featured": bool(item.get("featured")),
                "updated_at": item.get("updated_at") or item.get("created_at") or "",
            }
        )
    visible_items = visible_items[: max(1, min(int(limit), 12))]
    reflected_count = sum(1 for item in items if item.get("student_reflection"))
    return {
        "items": visible_items,
        "summary": {
            "item_count": len(items),
            "visible_count": len(visible_items),
            "featured_count": sum(1 for item in items if item.get("featured")),
            "reflection_count": reflected_count,
            "private_count": max(len(items) - len(visible_items), 0),
        },
    }
