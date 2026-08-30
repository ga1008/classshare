"""Single source of truth for offering↔class↔student membership (合班课堂).

Every "students of this offering" / "offerings of this student" resolution must
go through this module instead of the historical hard-wired join
``students.class_id = class_offerings.class_id``. The SQL fragments returned
here always include the primary ``class_offerings.class_id`` as a fallback, so
an offering whose links have not been backfilled yet (e.g. created by an older
code path mid-rollout) still resolves exactly as before — the design is
self-healing and rollback-safe.

Invariant maintained by :func:`replace_offering_class_links`:
every offering has exactly one ``is_primary = 1`` link and its ``class_id``
equals ``class_offerings.class_id`` (updated in the same transaction).
"""

from __future__ import annotations

from typing import Any

from ..db.schema_offering_class_links import (
    LINK_SOURCE_MANUAL,
    ensure_offering_class_links_schema,
)

COMBINED_NAME_SEPARATOR = "·"
MAX_LINKED_CLASS_COUNT = 12


class OfferingMembershipError(ValueError):
    """Raised when an offering↔class link operation is invalid."""


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_class_ids(class_ids: Any) -> list[int]:
    normalized: list[int] = []
    for value in class_ids or []:
        try:
            class_id = int(value)
        except (TypeError, ValueError):
            continue
        if class_id > 0 and class_id not in normalized:
            normalized.append(class_id)
    return normalized


def offering_student_where(*, offering_alias: str = "o", student_alias: str = "s") -> str:
    """SQL fragment: the student belongs to any class linked to the offering.

    The primary-class equality is kept as a fallback so unlinked offerings keep
    resolving; correlated EXISTS uses the (class_id, offering_id) index.
    """
    return (
        f"({student_alias}.class_id = {offering_alias}.class_id "
        f"OR EXISTS (SELECT 1 FROM class_offering_class_links cocl_m "
        f"WHERE cocl_m.offering_id = {offering_alias}.id "
        f"AND cocl_m.class_id = {student_alias}.class_id))"
    )


def student_offering_where(*, offering_alias: str = "o") -> str:
    """SQL fragment matching offerings visible to one class. Binds ``(class_id,
    class_id)`` — pass the student's class id twice in the parameter list."""
    return (
        f"({offering_alias}.class_id = ? "
        f"OR EXISTS (SELECT 1 FROM class_offering_class_links cocl_m "
        f"WHERE cocl_m.offering_id = {offering_alias}.id AND cocl_m.class_id = ?))"
    )


def student_offering_where_by_student_id(
    *, offering_alias: str = "o", require_active: bool = False
) -> str:
    """SQL fragment matching offerings visible to one student. Binds a single
    ``?`` — the student's primary-key id. ``require_active`` additionally gates
    on the student's enrollment status (matching the legacy discovery SQL)."""
    active_clause = (
        "AND COALESCE(st_m.enrollment_status, 'active') = 'active' " if require_active else ""
    )
    return (
        f"EXISTS (SELECT 1 FROM students st_m WHERE st_m.id = ? {active_clause}AND ("
        f"st_m.class_id = {offering_alias}.class_id "
        f"OR EXISTS (SELECT 1 FROM class_offering_class_links cocl_m "
        f"WHERE cocl_m.offering_id = {offering_alias}.id "
        f"AND cocl_m.class_id = st_m.class_id)))"
    )


def student_belongs_to_offering(conn: Any, *, student_class_id: int, offering: Any) -> bool:
    """Python-level access check: is the student's class linked to the offering?

    Primary-class equality short-circuits without touching the link table, so
    single-class offerings behave exactly as before.
    """
    offering_row = dict(offering)
    class_id = int(student_class_id or 0)
    if class_id <= 0:
        return False
    if int(offering_row.get("class_id") or 0) == class_id:
        return True
    ensure_offering_class_links_schema(conn)
    try:
        row = conn.execute(
            "SELECT 1 FROM class_offering_class_links WHERE offering_id = ? AND class_id = ? LIMIT 1",
            (int(offering_row["id"]), class_id),
        ).fetchone()
    except Exception:
        return False
    return bool(row)


def count_offering_active_students(conn: Any, offering_id: int) -> int:
    """Active student head-count across every linked class."""
    ensure_offering_class_links_schema(conn)
    class_ids = offering_class_ids(conn, offering_id)
    if not class_ids:
        return 0
    placeholders = ",".join("?" for _ in class_ids)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM students s
        WHERE s.class_id IN ({placeholders})
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        """,
        tuple(class_ids),
    ).fetchone()
    return int(row["n"])


def offering_class_ids(conn: Any, offering_id: int) -> list[int]:
    """All linked class ids, primary first; falls back to the legacy column.

    Read path is self-healing: if the link table is unreachable on this
    connection (e.g. hand-rolled test schemas), resolution degrades to the
    legacy primary class instead of failing.
    """
    ensure_offering_class_links_schema(conn)
    try:
        rows = conn.execute(
            """
            SELECT class_id
            FROM class_offering_class_links
            WHERE offering_id = ?
            ORDER BY is_primary DESC, id
            """,
            (int(offering_id),),
        ).fetchall()
    except Exception:
        rows = []
    class_ids = [int(row["class_id"]) for row in rows]
    if class_ids:
        return class_ids
    fallback = conn.execute(
        "SELECT class_id FROM class_offerings WHERE id = ?",
        (int(offering_id),),
    ).fetchone()
    if fallback and fallback["class_id"]:
        return [int(fallback["class_id"])]
    return []


def offering_class_links(conn: Any, offering_id: int) -> list[dict[str, Any]]:
    """Linked classes with names and active student counts (for serialization)."""
    ensure_offering_class_links_schema(conn)
    class_ids = offering_class_ids(conn, offering_id)
    if not class_ids:
        return []
    placeholders = ",".join("?" for _ in class_ids)
    try:
        rows = conn.execute(
            f"""
            SELECT c.id AS class_id,
                   c.name AS class_name,
                   COALESCE(l.is_primary, 0) AS is_primary,
                   COALESCE(l.source, '') AS source,
                   (
                       SELECT COUNT(*) FROM students s
                       WHERE s.class_id = c.id
                         AND COALESCE(s.enrollment_status, 'active') = 'active'
                   ) AS student_count
            FROM classes c
            LEFT JOIN class_offering_class_links l
                   ON l.class_id = c.id AND l.offering_id = ?
            WHERE c.id IN ({placeholders})
            """,
            (int(offering_id), *class_ids),
        ).fetchall()
    except Exception:
        rows = conn.execute(
            f"""
            SELECT c.id AS class_id,
                   c.name AS class_name,
                   1 AS is_primary,
                   '' AS source,
                   (
                       SELECT COUNT(*) FROM students s
                       WHERE s.class_id = c.id
                         AND COALESCE(s.enrollment_status, 'active') = 'active'
                   ) AS student_count
            FROM classes c
            WHERE c.id IN ({placeholders})
            """,
            tuple(class_ids),
        ).fetchall()
    by_id = {int(row["class_id"]): dict(row) for row in rows}
    ordered = [by_id[class_id] for class_id in class_ids if class_id in by_id]
    for item in ordered:
        item["is_primary"] = bool(item.get("is_primary"))
    return ordered


def load_offering_students(
    conn: Any,
    offering_id: int,
    *,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """Union of students across every linked class, ordered by class then name."""
    ensure_offering_class_links_schema(conn)
    class_ids = offering_class_ids(conn, offering_id)
    if not class_ids:
        return []
    placeholders = ",".join("?" for _ in class_ids)
    status_clause = (
        "AND COALESCE(s.enrollment_status, 'active') = 'active'" if active_only else ""
    )
    rows = conn.execute(
        f"""
        SELECT s.*, c.name AS class_name
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.class_id IN ({placeholders})
          {status_clause}
        ORDER BY c.name, s.name, s.id
        """,
        tuple(class_ids),
    ).fetchall()
    return [dict(row) for row in rows]


def offering_display_class_name(conn: Any, offering: Any) -> str:
    """Combined display name: cached value, else joined link names, else the
    primary class name."""
    ensure_offering_class_links_schema(conn)
    offering_row = dict(offering)
    cached = _normalize_text(offering_row.get("combined_class_names"))
    if cached:
        return cached
    links = offering_class_links(conn, int(offering_row["id"]))
    names = [_normalize_text(item.get("class_name")) for item in links]
    names = [name for name in names if name]
    if names:
        return COMBINED_NAME_SEPARATOR.join(names)
    row = conn.execute(
        """
        SELECT c.name FROM classes c
        JOIN class_offerings o ON o.class_id = c.id
        WHERE o.id = ?
        """,
        (int(offering_row["id"]),),
    ).fetchone()
    return _normalize_text(row["name"]) if row else ""


def find_conflicting_offerings(
    conn: Any,
    *,
    teacher_id: int,
    course_id: int,
    semester_id: int | None,
    semester_name: str,
    class_ids: list[int],
    exclude_offering_id: int | None = None,
) -> list[dict[str, Any]]:
    """Other offerings of the same course+semester already covering any of the
    given classes (via link or legacy primary column)."""
    ensure_offering_class_links_schema(conn)
    normalized_ids = _normalize_class_ids(class_ids)
    if not normalized_ids:
        return []
    placeholders = ",".join("?" for _ in normalized_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT o.id AS offering_id, hit.class_id AS class_id, c.name AS class_name
        FROM class_offerings o
        JOIN (
            SELECT l.offering_id, l.class_id
            FROM class_offering_class_links l
            WHERE l.class_id IN ({placeholders})
            UNION
            SELECT o2.id, o2.class_id
            FROM class_offerings o2
            WHERE o2.class_id IN ({placeholders})
        ) hit ON hit.offering_id = o.id
        JOIN classes c ON c.id = hit.class_id
        WHERE o.teacher_id = ?
          AND o.course_id = ?
          AND (
                (? IS NOT NULL AND o.semester_id = ?)
             OR (? IS NULL AND o.semester_id IS NULL AND COALESCE(o.semester, '') = ?)
          )
          AND (? IS NULL OR o.id != ?)
        ORDER BY o.id
        """,
        (
            *normalized_ids,
            *normalized_ids,
            int(teacher_id),
            int(course_id),
            semester_id,
            semester_id,
            semester_id,
            _normalize_text(semester_name),
            exclude_offering_id,
            exclude_offering_id,
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def replace_offering_class_links(
    conn: Any,
    *,
    offering_id: int,
    teacher_id: int,
    class_ids: list[int],
    primary_class_id: int | None = None,
    source: str = LINK_SOURCE_MANUAL,
    academic_class_names: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Rewrite the class set of an offering, keeping every invariant.

    Same-transaction updates: link rows, ``class_offerings.class_id`` (primary),
    ``is_combined`` and the ``combined_class_names`` display cache. Raises
    :class:`OfferingMembershipError` on empty input, unknown classes, or when a
    class is already covered by another offering of the same course+semester.
    """
    ensure_offering_class_links_schema(conn)
    normalized_ids = _normalize_class_ids(class_ids)
    if not normalized_ids:
        raise OfferingMembershipError("课堂至少需要绑定一个班级")
    if len(normalized_ids) > MAX_LINKED_CLASS_COUNT:
        raise OfferingMembershipError(f"课堂最多绑定 {MAX_LINKED_CLASS_COUNT} 个班级")

    offering = conn.execute(
        "SELECT * FROM class_offerings WHERE id = ? AND teacher_id = ?",
        (int(offering_id), int(teacher_id)),
    ).fetchone()
    if not offering:
        raise OfferingMembershipError("课堂不存在或无权操作")
    offering = dict(offering)

    resolved_primary = int(primary_class_id or 0)
    if resolved_primary and resolved_primary not in normalized_ids:
        raise OfferingMembershipError("主班级必须包含在所选班级中")
    if not resolved_primary:
        current_primary = int(offering.get("class_id") or 0)
        resolved_primary = (
            current_primary if current_primary in normalized_ids else normalized_ids[0]
        )

    placeholders = ",".join("?" for _ in normalized_ids)
    class_rows = conn.execute(
        f"SELECT id, name FROM classes WHERE id IN ({placeholders})",
        tuple(normalized_ids),
    ).fetchall()
    names_by_id = {int(row["id"]): _normalize_text(row["name"]) for row in class_rows}
    missing = [class_id for class_id in normalized_ids if class_id not in names_by_id]
    if missing:
        raise OfferingMembershipError("所选班级不存在，请刷新后重试")

    conflicts = find_conflicting_offerings(
        conn,
        teacher_id=teacher_id,
        course_id=int(offering["course_id"]),
        semester_id=int(offering["semester_id"]) if offering.get("semester_id") else None,
        semester_name=str(offering.get("semester") or ""),
        class_ids=normalized_ids,
        exclude_offering_id=int(offering_id),
    )
    if conflicts:
        conflict_names = COMBINED_NAME_SEPARATOR.join(
            sorted({_normalize_text(item.get("class_name")) for item in conflicts} - {""})
        )
        conflict_ids = sorted({int(item["offering_id"]) for item in conflicts})
        conflict_id_text = "、#".join(str(item) for item in conflict_ids)
        raise OfferingMembershipError(
            f"班级 {conflict_names} 已属于该课程本学期的其他课堂"
            f"（课堂 #{conflict_id_text}），同一课程每个班级只能进入一个课堂"
        )

    academic_names = academic_class_names or {}
    conn.execute(
        "DELETE FROM class_offering_class_links WHERE offering_id = ?",
        (int(offering_id),),
    )
    for class_id in normalized_ids:
        conn.execute(
            """
            INSERT INTO class_offering_class_links (
                offering_id, class_id, teacher_id, is_primary, source,
                academic_admin_class_name
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(offering_id),
                int(class_id),
                int(teacher_id),
                1 if class_id == resolved_primary else 0,
                _normalize_text(source) or LINK_SOURCE_MANUAL,
                _normalize_text(academic_names.get(class_id)),
            ),
        )

    is_combined = 1 if len(normalized_ids) > 1 else 0
    combined_names = (
        COMBINED_NAME_SEPARATOR.join(
            names_by_id[class_id] for class_id in normalized_ids if names_by_id.get(class_id)
        )
        if is_combined
        else ""
    )
    conn.execute(
        """
        UPDATE class_offerings
        SET class_id = ?, is_combined = ?, combined_class_names = ?
        WHERE id = ? AND teacher_id = ?
        """,
        (resolved_primary, is_combined, combined_names, int(offering_id), int(teacher_id)),
    )

    return {
        "offering_id": int(offering_id),
        "class_ids": normalized_ids,
        "primary_class_id": resolved_primary,
        "is_combined": bool(is_combined),
        "combined_class_names": combined_names,
    }
