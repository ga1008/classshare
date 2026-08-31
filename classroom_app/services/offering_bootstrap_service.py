"""教务同步后「一键开设课堂」候选计算（方案 docs/offering-bootstrap-2026-08.md）。

以**教学班**为原子：每门有真实排课的教务课程 × 每个教学班，解析其行政班
组成为本地班级，排除已被同课程课堂覆盖的组合，产出可直接创建的课堂候选。
候选按课程自身的排课组成生成，同名不同号课程天然不会错配课程号。
"""

from __future__ import annotations

from typing import Any

from .academic_course_sync_service import (
    _admin_class_names_from_composition,
    _normalize_course_match_text,
)
from .course_planning_service import summarize_academic_teaching_classes
from .offering_membership_service import offering_class_ids


def _resolve_local_classes(
    conn: Any, teacher_id: int, class_names: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """组成班级名 → 本地班级（先精确、后归一匹配）；返回 (resolved, missing_names)。"""
    rows = conn.execute(
        "SELECT id, name FROM classes WHERE created_by_teacher_id = ?",
        (int(teacher_id),),
    ).fetchall()
    exact = {str(row["name"] or "").strip(): dict(row) for row in rows}
    normalized = {
        _normalize_course_match_text(row["name"]): dict(row)
        for row in rows
        if _normalize_course_match_text(row["name"])
    }
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    seen_ids: set[int] = set()
    for name in class_names:
        candidate = exact.get(name) or normalized.get(_normalize_course_match_text(name))
        if candidate and int(candidate["id"]) not in seen_ids:
            seen_ids.add(int(candidate["id"]))
            resolved.append({"class_id": int(candidate["id"]), "class_name": str(candidate["name"])})
        elif not candidate:
            missing.append(name)
    return resolved, missing


def _covered_class_ids(conn: Any, teacher_id: int, semester_id: int, course_id: int) -> set[int]:
    """同课程+同学期已有课堂覆盖的班级并集（含合班 links）。"""
    offering_rows = conn.execute(
        """
        SELECT id FROM class_offerings
        WHERE teacher_id = ? AND course_id = ?
          AND (semester_id = ? OR semester_id IS NULL)
        """,
        (int(teacher_id), int(course_id), int(semester_id)),
    ).fetchall()
    covered: set[int] = set()
    for row in offering_rows:
        covered.update(offering_class_ids(conn, int(row["id"])))
    return covered


def _suggested_textbook(conn: Any, teacher_id: int, course_id: int) -> dict[str, Any] | None:
    """同课程历史课堂用过的教材作为建议值（最近优先）。"""
    row = conn.execute(
        """
        SELECT tb.id, tb.title
        FROM class_offerings o
        JOIN textbooks tb ON tb.id = o.textbook_id
        WHERE o.teacher_id = ? AND o.course_id = ? AND o.textbook_id IS NOT NULL
        ORDER BY o.id DESC
        LIMIT 1
        """,
        (int(teacher_id), int(course_id)),
    ).fetchone()
    return {"id": int(row["id"]), "title": str(row["title"] or "")} if row else None


def build_offering_bootstrap_candidates(
    conn: Any, *, teacher_id: int, semester_id: int
) -> dict[str, Any]:
    course_rows = conn.execute(
        """
        SELECT DISTINCT occ.course_id,
               c.name AS course_name,
               COALESCE(c.academic_course_code, '') AS course_code
        FROM teacher_academic_course_session_occurrences occ
        JOIN courses c ON c.id = occ.course_id
        WHERE occ.teacher_id = ? AND occ.semester_id = ?
        ORDER BY c.name, occ.course_id
        """,
        (int(teacher_id), int(semester_id)),
    ).fetchall()

    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for course_row in course_rows:
        course_id = int(course_row["course_id"])
        covered = _covered_class_ids(conn, teacher_id, semester_id, course_id)
        suggested = _suggested_textbook(conn, teacher_id, course_id)
        for option in summarize_academic_teaching_classes(
            conn,
            teacher_id=teacher_id,
            semester_id=semester_id,
            course_id=course_id,
        ):
            composition = str(option.get("class_composition") or option.get("teaching_class_name") or "")
            class_names = _admin_class_names_from_composition(composition)
            if not class_names:
                class_names = [str(option.get("class_display_name") or option.get("teaching_class_name") or "")]
                class_names = [name for name in class_names if name]
            resolved, missing = _resolve_local_classes(conn, teacher_id, class_names)
            entry_base = {
                "course_id": course_id,
                "course_name": str(course_row["course_name"] or ""),
                "course_code": str(course_row["course_code"] or ""),
                "teaching_class_id": str(option.get("teaching_class_id") or ""),
                "teaching_class_name": str(option.get("teaching_class_name") or ""),
                "class_display_name": str(option.get("class_display_name") or ""),
                "session_count": int(option.get("session_count") or 0),
            }
            if missing or not resolved:
                blocked.append(
                    {
                        **entry_base,
                        "reason": (
                            "组成班级在本平台缺失："
                            + "、".join(missing or class_names)
                            + "；请先同步学生名单。"
                        ),
                    }
                )
                continue
            class_ids = [item["class_id"] for item in resolved]
            if any(class_id in covered for class_id in class_ids):
                continue  # 该教学班已有课堂覆盖（全部或部分）→ 不是候选
            placeholders = ",".join("?" for _ in class_ids)
            student_count = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) AS n FROM students
                    WHERE class_id IN ({placeholders})
                      AND COALESCE(enrollment_status, 'active') = 'active'
                    """,
                    tuple(class_ids),
                ).fetchone()["n"]
            )
            candidates.append(
                {
                    **entry_base,
                    "class_ids": class_ids,
                    "primary_class_id": class_ids[0],
                    "class_names": [item["class_name"] for item in resolved],
                    "is_combined": len(class_ids) > 1,
                    "student_count": student_count,
                    "suggested_textbook": suggested,
                }
            )

    textbook_rows = conn.execute(
        "SELECT id, title FROM textbooks WHERE teacher_id = ? ORDER BY title, id",
        (int(teacher_id),),
    ).fetchall()
    return {
        "candidates": candidates,
        "blocked": blocked,
        "textbooks": [
            {"id": int(row["id"]), "title": str(row["title"] or "")} for row in textbook_rows
        ],
        "summary": {
            "candidate_count": len(candidates),
            "course_count": len({item["course_id"] for item in candidates}),
            "student_count": sum(item["student_count"] for item in candidates),
        },
    }
