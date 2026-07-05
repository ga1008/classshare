"""One-off, idempotent data migration: unify academic year/semester (学年学期).

Brings existing rows in line with the canonical model defined in
:mod:`classroom_app.services.semester_identity_service`:

1. **Normalize names** — rewrite ``academic_semesters.name`` to the canonical
   form (``2025-2026第二学期``) wherever it parses.
2. **Merge duplicates** — collapse rows that share one real ``(school, identity)``
   into a single school-shared row, repointing every ``semester_id`` foreign key
   and dropping the duplicate's calendar days (which carry a
   ``UNIQUE(semester_id, date)`` constraint).
3. **Normalize offering text** — rewrite ``class_offerings.semester`` to canonical.
4. **Backfill / create links** — for offerings that carry a parseable semester
   but no ``semester_id``, bind them to the matching school semester, creating a
   canonical semester (with inferred default dates) when none exists yet.

Re-running is safe: after the first pass every step is a no-op scan.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..services.academic_service import compute_semester_week_count
from ..services.organization_scope_service import load_teacher_org_scope
from ..services.semester_identity_service import (
    SemesterIdentity,
    parse_semester_identity,
)
from .connection import execute_insert_returning_id

# 所有以 semester_id 外键引用 academic_semesters.id 的表（除日历日表另行处理）。
_SEMESTER_FK_TABLES = (
    "class_offerings",
    "teacher_calendar_events",
    "teacher_academic_course_sync_items",
    "teacher_academic_course_session_occurrences",
    "teacher_academic_roster_sync_items",
    "teacher_academic_roster_memberships",
    "teacher_academic_invigilation_items",
    "teacher_academic_course_exam_items",
    "teacher_academic_exam_roster_items",
    "teacher_academic_exam_roster_students",
)


def _norm_school(value: Any) -> str:
    return str(value or "").strip().lower()


def _default_dates_for_identity(identity: SemesterIdentity) -> tuple[date, date]:
    """规范学期缺日期时的推断默认值（供补建学期用，可后续在学期管理里校准）。"""
    if identity.term == 1:
        return date(identity.start_year, 9, 1), date(identity.start_year + 1, 1, 31)
    return date(identity.start_year + 1, 2, 20), date(identity.start_year + 1, 7, 15)


def _table_has_semester_column(conn, table: str, column: str = "semester_id") -> bool:
    try:
        conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
        return True
    except Exception:  # noqa: BLE001 — 表/列不存在（不同部署裁剪）时跳过
        return False


def _normalize_semester_names(conn, report: dict[str, int]) -> None:
    rows = conn.execute("SELECT id, name FROM academic_semesters").fetchall()
    for row in rows:
        identity = parse_semester_identity(row["name"])
        if identity is None:
            continue
        canonical = identity.canonical_name
        if str(row["name"] or "").strip() != canonical:
            conn.execute(
                "UPDATE academic_semesters SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (canonical, int(row["id"])),
            )
            report["names_normalized"] += 1


def _pick_keep_row(rows: list[dict[str, Any]], calendar_counts: dict[int, int]) -> dict[str, Any]:
    """重复学期里选保留行：有校历日 > 已同步校历 > id 最小。"""

    def score(row: dict[str, Any]) -> tuple[int, int, int]:
        has_days = 1 if calendar_counts.get(int(row["id"]), 0) > 0 else 0
        synced = 1 if str(row.get("calendar_sync_status") or "") == "synced" else 0
        return (has_days, synced, -int(row["id"]))

    return max(rows, key=score)


def _merge_duplicate_semesters(conn, report: dict[str, int]) -> None:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, school_code, name, calendar_sync_status
            FROM academic_semesters
            ORDER BY id ASC
            """
        ).fetchall()
    ]
    calendar_counts: dict[int, int] = {}
    for row in conn.execute(
        "SELECT semester_id, COUNT(*) AS c FROM academic_semester_calendar_days GROUP BY semester_id"
    ).fetchall():
        calendar_counts[int(row["semester_id"])] = int(row["c"])

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        identity = parse_semester_identity(row["name"])
        if identity is None:
            continue
        groups.setdefault((_norm_school(row["school_code"]), identity.code), []).append(row)

    for group_rows in groups.values():
        if len(group_rows) < 2:
            continue
        keep = _pick_keep_row(group_rows, calendar_counts)
        keep_id = int(keep["id"])
        for dup in group_rows:
            dup_id = int(dup["id"])
            if dup_id == keep_id:
                continue
            for table in _SEMESTER_FK_TABLES:
                if not _table_has_semester_column(conn, table):
                    continue
                conn.execute(
                    f"UPDATE {table} SET semester_id = ? WHERE semester_id = ?",
                    (keep_id, dup_id),
                )
            # 日历日有 UNIQUE(semester_id, date)：删除重复行而非改指，保留行日历为准。
            conn.execute(
                "DELETE FROM academic_semester_calendar_days WHERE semester_id = ?",
                (dup_id,),
            )
            conn.execute("DELETE FROM academic_semesters WHERE id = ?", (dup_id,))
            report["semesters_merged"] += 1


def _load_school_semester_index(conn) -> dict[tuple[str, str], int]:
    """(school_norm, identity.code) → semester_id（合并后每键唯一）。"""
    index: dict[tuple[str, str], int] = {}
    for row in conn.execute("SELECT id, school_code, name FROM academic_semesters").fetchall():
        identity = parse_semester_identity(row["name"])
        if identity is None:
            continue
        index.setdefault((_norm_school(row["school_code"]), identity.code), int(row["id"]))
    return index


def _teacher_scope_cached(conn, teacher_id: int, cache: dict[int, dict[str, Any]]) -> dict[str, Any]:
    if teacher_id not in cache:
        try:
            cache[teacher_id] = load_teacher_org_scope(conn, teacher_id)
        except Exception:  # noqa: BLE001
            cache[teacher_id] = {"school_code": "", "school_name": ""}
    return cache[teacher_id]


def _create_semester(conn, *, identity: SemesterIdentity, teacher_id: int, scope: dict[str, Any]) -> int:
    start_date, end_date = _default_dates_for_identity(identity)
    week_count = compute_semester_week_count(start_date, end_date)
    return int(
        execute_insert_returning_id(
            conn,
            """
            INSERT INTO academic_semesters (
                teacher_id, school_code, school_name, name, start_date, end_date, week_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(teacher_id),
                scope.get("school_code") or "",
                scope.get("school_name") or "",
                identity.canonical_name,
                start_date.isoformat(),
                end_date.isoformat(),
                week_count,
            ),
        )
    )


def _backfill_offering_links(conn, report: dict[str, int]) -> None:
    semester_names = {
        int(row["id"]): str(row["name"] or "")
        for row in conn.execute("SELECT id, name FROM academic_semesters").fetchall()
    }
    index = _load_school_semester_index(conn)
    scope_cache: dict[int, dict[str, Any]] = {}

    offerings = [
        dict(row)
        for row in conn.execute(
            "SELECT id, teacher_id, semester, semester_id FROM class_offerings"
        ).fetchall()
    ]
    for offering in offerings:
        offering_id = int(offering["id"])
        current_semester_id = offering.get("semester_id")
        by_id_name = semester_names.get(int(current_semester_id)) if current_semester_id else None
        identity = parse_semester_identity(by_id_name, offering.get("semester"))

        # 1) 规范化 offering.semester 文本（可解析时）。
        if identity is not None:
            canonical = identity.canonical_name
            if str(offering.get("semester") or "").strip() != canonical:
                conn.execute(
                    "UPDATE class_offerings SET semester = ? WHERE id = ?",
                    (canonical, offering_id),
                )
                report["offering_text_normalized"] += 1

        # 2) 补建/回填 semester_id。
        if current_semester_id or identity is None:
            continue
        teacher_id = int(offering.get("teacher_id") or 0)
        scope = _teacher_scope_cached(conn, teacher_id, scope_cache)
        school = _norm_school(scope.get("school_code"))
        key = (school, identity.code)
        target_id = index.get(key)
        if target_id is None:
            target_id = _create_semester(conn, identity=identity, teacher_id=teacher_id, scope=scope)
            index[key] = target_id
            semester_names[target_id] = identity.canonical_name
            report["semesters_created"] += 1
        conn.execute(
            "UPDATE class_offerings SET semester_id = ?, semester = ? WHERE id = ?",
            (int(target_id), identity.canonical_name, offering_id),
        )
        report["offering_links_backfilled"] += 1


def unify_semesters(conn) -> dict[str, int]:
    """Run the full idempotent unification on ``conn``; returns a counts report."""
    report = {
        "names_normalized": 0,
        "semesters_merged": 0,
        "offering_text_normalized": 0,
        "offering_links_backfilled": 0,
        "semesters_created": 0,
    }
    _normalize_semester_names(conn, report)
    _merge_duplicate_semesters(conn, report)
    _backfill_offering_links(conn, report)
    return report


def main() -> None:  # pragma: no cover - manual entry point
    from ..database import get_db_connection

    with get_db_connection() as conn:
        report = unify_semesters(conn)
        conn.commit()
    print("[semester-unification]", report)


if __name__ == "__main__":  # pragma: no cover
    main()
