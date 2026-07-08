from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..db.connection import get_configured_db_engine


SCHOOL_CODE_GXUFL = "gxufl"
TEACHING_CLASS_MAPPING_TABLE = "teacher_academic_teaching_class_mappings"


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _as_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [_text(item) for item in parsed if _text(item)]


def _unique_aliases(values: list[Any] | tuple[Any, ...] | set[Any]) -> list[str]:
    seen: set[str] = set()
    aliases: list[str] = []
    for value in values:
        alias = _text(value)
        if not alias:
            continue
        key = alias.casefold()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(alias)
    return aliases


def _alias_lookup_key(value: Any) -> str:
    return re.sub(r"[\s\-_－—–·:：,，、;；/()（）\[\]【】]+", "", _text(value)).casefold()


def _trailing_teaching_class_suffix(value: Any) -> str:
    match = re.search(r"(?:[-_－—–]\s*)?([0-9]{3,4})$", _text(value))
    return match.group(1) if match else ""


def _without_teaching_class_suffix(value: Any, suffix: str) -> str:
    if not suffix:
        return _text(value)
    return re.sub(rf"[\s\-_－—–]*{re.escape(suffix)}$", "", _text(value)).strip()


def _teaching_class_aliases_for_mapping(mapping: dict[str, Any]) -> list[str]:
    teaching_class_name = _text(mapping.get("teaching_class_name"))
    teaching_class_id = _text(mapping.get("teaching_class_id"))
    course_code = _text(mapping.get("course_code"))
    course_name = _text(mapping.get("course_name"))
    suffix = _trailing_teaching_class_suffix(teaching_class_name)
    aliases: list[Any] = [teaching_class_name, teaching_class_id]
    if suffix:
        bases = [
            _without_teaching_class_suffix(teaching_class_name, suffix),
            course_name,
            course_code,
        ]
        for base in bases:
            base = _text(base)
            if not base:
                continue
            aliases.extend([f"{base}-{suffix}", f"{base}{suffix}"])
        aliases.append(suffix)
    return _unique_aliases(aliases)


def _admin_class_aliases_for_names(admin_names: list[str], display_name: str) -> list[str]:
    aliases: list[Any] = [display_name]
    if len(admin_names) == 1:
        name = admin_names[0]
        aliases.append(name)
        aliases.append(re.sub(r"班(?=(?:（|\(|$))", "", name).strip())
        aliases.append(re.sub(r"班(?:（.*?）|\(.*?\))?$", "", name).strip())
        if "扩招专升本" in name:
            aliases.append(name.replace("扩招专升本", "专升本"))
            aliases.append(re.sub(r"班(?=(?:（|\(|$))", "", name.replace("扩招专升本", "专升本")).strip())
    return _unique_aliases(aliases)


def _db_engine_for_connection(conn: Any) -> str:
    if isinstance(conn, sqlite3.Connection):
        return "sqlite"
    try:
        return get_configured_db_engine()
    except Exception:  # noqa: BLE001 - schema helpers should stay best-effort
        return "sqlite"


def ensure_teaching_class_mapping_schema(conn: Any) -> None:
    """Create the reusable teaching-class -> administrative-class mapping table."""
    engine = _db_engine_for_connection(conn)
    if engine == "postgres":
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teacher_academic_teaching_class_mappings (
                id SERIAL PRIMARY KEY,
                teacher_id INTEGER NOT NULL,
                semester_id INTEGER,
                school_code TEXT NOT NULL DEFAULT 'gxufl',
                academic_year TEXT NOT NULL DEFAULT '',
                academic_term TEXT NOT NULL DEFAULT '',
                course_code TEXT NOT NULL DEFAULT '',
                course_name TEXT NOT NULL DEFAULT '',
                teaching_class_id TEXT NOT NULL DEFAULT '',
                teaching_class_name TEXT NOT NULL DEFAULT '',
                teaching_class_aliases_json TEXT NOT NULL DEFAULT '[]',
                admin_class_id INTEGER,
                admin_class_code TEXT NOT NULL DEFAULT '',
                admin_class_name TEXT NOT NULL DEFAULT '',
                admin_class_ids_json TEXT NOT NULL DEFAULT '[]',
                admin_class_codes_json TEXT NOT NULL DEFAULT '[]',
                admin_class_names_json TEXT NOT NULL DEFAULT '[]',
                admin_class_aliases_json TEXT NOT NULL DEFAULT '[]',
                admin_class_count INTEGER NOT NULL DEFAULT 0,
                student_count INTEGER NOT NULL DEFAULT 0,
                mapping_status TEXT NOT NULL DEFAULT 'active',
                source_sync_item_ids_json TEXT NOT NULL DEFAULT '[]',
                source_updated_at TEXT,
                synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teacher_academic_teaching_class_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                semester_id INTEGER,
                school_code TEXT NOT NULL DEFAULT 'gxufl',
                academic_year TEXT NOT NULL DEFAULT '',
                academic_term TEXT NOT NULL DEFAULT '',
                course_code TEXT NOT NULL DEFAULT '',
                course_name TEXT NOT NULL DEFAULT '',
                teaching_class_id TEXT NOT NULL DEFAULT '',
                teaching_class_name TEXT NOT NULL DEFAULT '',
                teaching_class_aliases_json TEXT NOT NULL DEFAULT '[]',
                admin_class_id INTEGER,
                admin_class_code TEXT NOT NULL DEFAULT '',
                admin_class_name TEXT NOT NULL DEFAULT '',
                admin_class_ids_json TEXT NOT NULL DEFAULT '[]',
                admin_class_codes_json TEXT NOT NULL DEFAULT '[]',
                admin_class_names_json TEXT NOT NULL DEFAULT '[]',
                admin_class_aliases_json TEXT NOT NULL DEFAULT '[]',
                admin_class_count INTEGER NOT NULL DEFAULT 0,
                student_count INTEGER NOT NULL DEFAULT 0,
                mapping_status TEXT NOT NULL DEFAULT 'active',
                source_sync_item_ids_json TEXT NOT NULL DEFAULT '[]',
                source_updated_at TEXT,
                synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (teacher_id) REFERENCES teachers (id) ON DELETE CASCADE,
                FOREIGN KEY (semester_id) REFERENCES academic_semesters (id) ON DELETE SET NULL,
                FOREIGN KEY (admin_class_id) REFERENCES classes (id) ON DELETE SET NULL,
                UNIQUE (
                    teacher_id, school_code, academic_year, academic_term,
                    course_code, teaching_class_id, teaching_class_name
                )
            )
            """
        )
    for column_name, column_def in (
        ("teaching_class_aliases_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("admin_class_aliases_json", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        try:
            if engine == "postgres":
                conn.execute(
                    f"ALTER TABLE {TEACHING_CLASS_MAPPING_TABLE} "
                    f"ADD COLUMN IF NOT EXISTS {column_name} {column_def}"
                )
            else:
                conn.execute(f"ALTER TABLE {TEACHING_CLASS_MAPPING_TABLE} ADD COLUMN {column_name} {column_def}")
        except Exception:  # noqa: BLE001 - old schemas may already have the column
            pass
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_teacher_academic_class_mappings_unique_teaching_class
        ON teacher_academic_teaching_class_mappings (
            teacher_id, school_code, academic_year, academic_term,
            course_code, teaching_class_id, teaching_class_name
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_teacher_academic_class_mappings_lookup
        ON teacher_academic_teaching_class_mappings (
            teacher_id, school_code, academic_year, academic_term,
            course_code, teaching_class_name
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_teacher_academic_class_mappings_name
        ON teacher_academic_teaching_class_mappings (teacher_id, school_code, teaching_class_name)
        """
    )


def _fetch_mapping_rows(
    conn: Any,
    *,
    teacher_id: int,
    academic_year: str = "",
    academic_term: str = "",
) -> list[dict[str, Any]]:
    clauses = ["teacher_id = ?", "mapping_status = 'active'", "COALESCE(teaching_class_name, '') <> ''"]
    params: list[Any] = [int(teacher_id)]
    if academic_year:
        clauses.append("academic_year = ?")
        params.append(academic_year)
    if academic_term:
        clauses.append("academic_term = ?")
        params.append(academic_term)
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM teacher_academic_teaching_class_mappings
            WHERE {' AND '.join(clauses)}
            ORDER BY academic_year DESC, academic_term DESC, updated_at DESC, id DESC
            """,
            tuple(params),
        ).fetchall()
    except Exception:  # noqa: BLE001 - table may not exist on older local DBs
        return []
    return [dict(row) for row in rows]


def _fetch_membership_rows(
    conn: Any,
    *,
    teacher_id: int,
    semester_id: int | None = None,
    academic_year: str = "",
    academic_term: str = "",
) -> list[dict[str, Any]]:
    clauses = ["m.teacher_id = ?", "COALESCE(m.teaching_class_name, '') <> ''"]
    params: list[Any] = [int(teacher_id)]
    if semester_id:
        clauses.append("m.semester_id = ?")
        params.append(int(semester_id))
    if academic_year:
        clauses.append("m.academic_year = ?")
        params.append(academic_year)
    if academic_term:
        clauses.append("m.academic_term = ?")
        params.append(academic_term)

    full_sql = f"""
        SELECT m.teacher_id,
               m.semester_id,
               COALESCE(m.school_code, 'gxufl') AS school_code,
               m.academic_year,
               m.academic_term,
               m.course_code,
               m.course_name,
               m.teaching_class_id,
               m.teaching_class_name,
               m.sync_item_id,
               m.class_id,
               m.admin_class_code,
               COALESCE(NULLIF(c.name, ''), NULLIF(m.admin_class_name, '')) AS admin_class_name,
               m.student_number,
               m.synced_at
        FROM teacher_academic_roster_memberships m
        LEFT JOIN classes c ON c.id = m.class_id
        WHERE {' AND '.join(clauses)}
        ORDER BY m.academic_year DESC, m.academic_term DESC, m.course_code, m.teaching_class_name, m.student_number
    """
    try:
        rows = conn.execute(full_sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]
    except Exception:  # noqa: BLE001 - tolerate lean test/legacy tables
        pass

    minimal_sql = f"""
        SELECT m.teacher_id,
               NULL AS semester_id,
               'gxufl' AS school_code,
               '' AS academic_year,
               '' AS academic_term,
               COALESCE(m.course_code, '') AS course_code,
               '' AS course_name,
               '' AS teaching_class_id,
               m.teaching_class_name,
               NULL AS sync_item_id,
               m.class_id,
               '' AS admin_class_code,
               COALESCE(NULLIF(c.name, ''), '') AS admin_class_name,
               '' AS student_number,
               NULL AS synced_at
        FROM teacher_academic_roster_memberships m
        LEFT JOIN classes c ON c.id = m.class_id
        WHERE {' AND '.join(clauses)}
        ORDER BY m.course_code, m.teaching_class_name
    """
    try:
        rows = conn.execute(minimal_sql, tuple(params)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [dict(row) for row in rows]


def _sort_names(values: set[str]) -> list[str]:
    return sorted((value for value in values if value), key=lambda value: (len(value), value))


def _split_admin_class_names(value: Any, teaching_class_name: Any = "") -> list[str]:
    text = _text(value)
    teaching_name = _text(teaching_class_name)
    if not text or text in {"无", "未分班", "无行政班"} or text == teaching_name:
        return []
    parts = [_text(part) for part in re.split(r"[,，、;；/]+", text) if _text(part)]
    if not parts:
        parts = [text]
    return [
        part
        for part in dict.fromkeys(parts)
        if part and part not in {"无", "未分班", "无行政班"} and part != teaching_name
    ]


def _fetch_roster_item_rows(
    conn: Any,
    *,
    teacher_id: int,
    semester_id: int | None = None,
    academic_year: str = "",
    academic_term: str = "",
) -> list[dict[str, Any]]:
    clauses = ["r.teacher_id = ?", "COALESCE(r.teaching_class_name, '') <> ''"]
    params: list[Any] = [int(teacher_id)]
    if semester_id:
        clauses.append("r.semester_id = ?")
        params.append(int(semester_id))
    if academic_year:
        clauses.append("r.academic_year = ?")
        params.append(academic_year)
    if academic_term:
        clauses.append("r.academic_term = ?")
        params.append(academic_term)
    try:
        rows = conn.execute(
            f"""
            SELECT r.teacher_id,
                   r.semester_id,
                   COALESCE(r.school_code, 'gxufl') AS school_code,
                   r.academic_year,
                   r.academic_term,
                   r.course_code,
                   r.course_name,
                   r.teaching_class_id,
                   r.teaching_class_name,
                   r.id AS sync_item_id,
                   r.class_id,
                   '' AS admin_class_code,
                   COALESCE(NULLIF(c.name, ''), NULLIF(r.class_composition, '')) AS admin_class_name,
                   '' AS student_number,
                   r.synced_at
            FROM teacher_academic_roster_sync_items r
            LEFT JOIN classes c ON c.id = r.class_id
            WHERE {' AND '.join(clauses)}
            ORDER BY r.academic_year DESC, r.academic_term DESC, r.course_code, r.teaching_class_name
            """,
            tuple(params),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []

    expanded: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row)
        names = _split_admin_class_names(row_dict.get("admin_class_name"), row_dict.get("teaching_class_name"))
        for name in names:
            expanded.append({**row_dict, "admin_class_name": name})
    return expanded


def _mapping_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        teaching_class_name = _text(row.get("teaching_class_name"))
        if not teaching_class_name:
            continue
        key = (
            _as_int(row.get("teacher_id")),
            _text(row.get("school_code")) or SCHOOL_CODE_GXUFL,
            _text(row.get("academic_year")),
            _text(row.get("academic_term")),
            _text(row.get("course_code")),
            _text(row.get("teaching_class_id")),
            teaching_class_name,
        )
        group = grouped.setdefault(
            key,
            {
                "teacher_id": key[0],
                "school_code": key[1],
                "academic_year": key[2],
                "academic_term": key[3],
                "course_code": key[4],
                "teaching_class_id": key[5],
                "teaching_class_name": key[6],
                "semester_ids": set(),
                "course_names": set(),
                "admin_class_ids": set(),
                "admin_class_codes": set(),
                "admin_class_names": set(),
                "students": set(),
                "sync_item_ids": set(),
                "source_updated_at": "",
            },
        )
        if row.get("semester_id") not in (None, ""):
            group["semester_ids"].add(_as_int(row.get("semester_id")))
        if _text(row.get("course_name")):
            group["course_names"].add(_text(row.get("course_name")))
        if _as_int(row.get("class_id")):
            group["admin_class_ids"].add(_as_int(row.get("class_id")))
        if _text(row.get("admin_class_code")):
            group["admin_class_codes"].add(_text(row.get("admin_class_code")))
        if _text(row.get("admin_class_name")):
            group["admin_class_names"].add(_text(row.get("admin_class_name")))
        if _text(row.get("student_number")):
            group["students"].add(_text(row.get("student_number")))
        if row.get("sync_item_id") not in (None, ""):
            group["sync_item_ids"].add(_as_int(row.get("sync_item_id")))
        synced_at = _text(row.get("synced_at"))
        if synced_at and synced_at > group["source_updated_at"]:
            group["source_updated_at"] = synced_at

    results: list[dict[str, Any]] = []
    for group in grouped.values():
        admin_names = _sort_names(group["admin_class_names"])
        admin_ids = sorted(group["admin_class_ids"])
        admin_codes = _sort_names(group["admin_class_codes"])
        display_name = "、".join(admin_names)
        admin_count = len(admin_names)
        base_mapping = {
            "course_code": group["course_code"],
            "course_name": next(iter(_sort_names(group["course_names"])), ""),
            "teaching_class_id": group["teaching_class_id"],
            "teaching_class_name": group["teaching_class_name"],
        }
        teaching_aliases = _teaching_class_aliases_for_mapping(base_mapping)
        admin_aliases = _admin_class_aliases_for_names(admin_names, display_name)
        results.append(
            {
                **group,
                "semester_id": min(group["semester_ids"]) if len(group["semester_ids"]) == 1 else None,
                "course_name": base_mapping["course_name"],
                "admin_class_id": admin_ids[0] if len(admin_ids) == 1 else None,
                "admin_class_code": admin_codes[0] if len(admin_codes) == 1 else "",
                "admin_class_name": display_name,
                "teaching_class_aliases_json": _json_dumps(teaching_aliases),
                "admin_class_ids_json": _json_dumps(admin_ids),
                "admin_class_codes_json": _json_dumps(admin_codes),
                "admin_class_names_json": _json_dumps(admin_names),
                "admin_class_aliases_json": _json_dumps(admin_aliases),
                "admin_class_count": admin_count,
                "student_count": len(group["students"]),
                "mapping_status": "active" if display_name else "unresolved",
                "source_sync_item_ids_json": _json_dumps(sorted(group["sync_item_ids"])),
            }
        )
    return results


def refresh_teaching_class_mappings_from_roster(
    conn: Any,
    *,
    teacher_id: int,
    semester_id: int | None = None,
    academic_year: str = "",
    academic_term: str = "",
    synced_at: str = "",
) -> dict[str, Any]:
    """Refresh the mapping table from roster memberships after a roster sync."""
    ensure_teaching_class_mapping_schema(conn)
    source_rows = _fetch_membership_rows(
        conn,
        teacher_id=int(teacher_id),
        semester_id=semester_id,
        academic_year=academic_year,
        academic_term=academic_term,
    )
    source_rows.extend(
        _fetch_roster_item_rows(
            conn,
            teacher_id=int(teacher_id),
            semester_id=semester_id,
            academic_year=academic_year,
            academic_term=academic_term,
        )
    )
    mappings = _mapping_groups(source_rows)
    refreshed_at = synced_at or ""

    stale_clauses = ["teacher_id = ?"]
    stale_params: list[Any] = [int(teacher_id)]
    if semester_id:
        stale_clauses.append("semester_id = ?")
        stale_params.append(int(semester_id))
    if academic_year:
        stale_clauses.append("academic_year = ?")
        stale_params.append(academic_year)
    if academic_term:
        stale_clauses.append("academic_term = ?")
        stale_params.append(academic_term)
    conn.execute(
        f"""
        UPDATE teacher_academic_teaching_class_mappings
        SET mapping_status = 'stale', updated_at = ?
        WHERE {' AND '.join(stale_clauses)}
        """,
        (refreshed_at, *stale_params),
    )

    upserted = 0
    for mapping in mappings:
        conn.execute(
            """
            INSERT INTO teacher_academic_teaching_class_mappings (
                teacher_id, semester_id, school_code, academic_year, academic_term,
                course_code, course_name, teaching_class_id, teaching_class_name,
                teaching_class_aliases_json, admin_class_id, admin_class_code, admin_class_name,
                admin_class_ids_json, admin_class_codes_json, admin_class_names_json, admin_class_aliases_json,
                admin_class_count, student_count, mapping_status,
                source_sync_item_ids_json, source_updated_at, synced_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                teacher_id, school_code, academic_year, academic_term,
                course_code, teaching_class_id, teaching_class_name
            )
            DO UPDATE SET
                semester_id = excluded.semester_id,
                course_name = excluded.course_name,
                teaching_class_aliases_json = excluded.teaching_class_aliases_json,
                admin_class_id = excluded.admin_class_id,
                admin_class_code = excluded.admin_class_code,
                admin_class_name = excluded.admin_class_name,
                admin_class_ids_json = excluded.admin_class_ids_json,
                admin_class_codes_json = excluded.admin_class_codes_json,
                admin_class_names_json = excluded.admin_class_names_json,
                admin_class_aliases_json = excluded.admin_class_aliases_json,
                admin_class_count = excluded.admin_class_count,
                student_count = excluded.student_count,
                mapping_status = excluded.mapping_status,
                source_sync_item_ids_json = excluded.source_sync_item_ids_json,
                source_updated_at = excluded.source_updated_at,
                synced_at = excluded.synced_at,
                updated_at = excluded.updated_at
            """,
            (
                int(mapping["teacher_id"]),
                mapping["semester_id"],
                mapping["school_code"],
                mapping["academic_year"],
                mapping["academic_term"],
                mapping["course_code"],
                mapping["course_name"],
                mapping["teaching_class_id"],
                mapping["teaching_class_name"],
                mapping["teaching_class_aliases_json"],
                mapping["admin_class_id"],
                mapping["admin_class_code"],
                mapping["admin_class_name"],
                mapping["admin_class_ids_json"],
                mapping["admin_class_codes_json"],
                mapping["admin_class_names_json"],
                mapping["admin_class_aliases_json"],
                int(mapping["admin_class_count"]),
                int(mapping["student_count"]),
                mapping["mapping_status"],
                mapping["source_sync_item_ids_json"],
                mapping["source_updated_at"],
                refreshed_at,
                refreshed_at,
            ),
        )
        upserted += 1
    return {
        "status": "success",
        "mapping_count": upserted,
        "source_row_count": len(source_rows),
        "single_class_mapping_count": sum(1 for item in mappings if int(item.get("admin_class_count") or 0) == 1),
    }


def _mappings_from_rows(rows: list[dict[str, Any]], *, single_only: bool = False) -> dict[Any, str]:
    mappings: dict[Any, str] = {}
    course_groups: dict[tuple[str, str], set[str]] = {}
    fallback_groups: dict[str, set[str]] = {}
    normalized_course_groups: dict[tuple[str, str], set[str]] = {}
    normalized_fallback_groups: dict[str, set[str]] = {}
    for row in rows:
        teaching_class_name = _text(row.get("teaching_class_name"))
        display_name = _text(row.get("admin_class_name"))
        if not teaching_class_name or not display_name:
            continue
        if single_only and _as_int(row.get("admin_class_count")) != 1:
            continue
        course_code = _text(row.get("course_code"))
        aliases = _unique_aliases(
            [
                teaching_class_name,
                *_json_list(row.get("teaching_class_aliases_json")),
                *_json_list(row.get("admin_class_aliases_json")),
            ]
        )
        for alias in aliases:
            if course_code:
                course_groups.setdefault((course_code, alias), set()).add(display_name)
                normalized = _alias_lookup_key(alias)
                if normalized:
                    normalized_course_groups.setdefault((course_code, normalized), set()).add(display_name)
            fallback_groups.setdefault(alias, set()).add(display_name)
            normalized = _alias_lookup_key(alias)
            if normalized:
                normalized_fallback_groups.setdefault(normalized, set()).add(display_name)
    for key, display_names in course_groups.items():
        if len(display_names) == 1:
            mappings[key] = next(iter(display_names))
    for teaching_class_name, display_names in fallback_groups.items():
        if len(display_names) == 1:
            mappings[teaching_class_name] = next(iter(display_names))
    for key, display_names in normalized_course_groups.items():
        if len(display_names) == 1:
            course_code, normalized = key
            mappings[("__normalized_course__", course_code, normalized)] = next(iter(display_names))
    for normalized, display_names in normalized_fallback_groups.items():
        if len(display_names) == 1:
            mappings[("__normalized__", normalized)] = next(iter(display_names))
    return mappings


def load_teaching_class_display_mappings(
    conn: Any,
    teacher_id: int,
    *,
    academic_year: str = "",
    academic_term: str = "",
    single_only: bool = False,
) -> dict[Any, str]:
    """Load reusable display-name mappings keyed by (course_code, teaching_class_name) and by name."""
    table_rows = _fetch_mapping_rows(
        conn,
        teacher_id=int(teacher_id),
        academic_year=academic_year,
        academic_term=academic_term,
    )
    if table_rows:
        return _mappings_from_rows(table_rows, single_only=single_only)
    membership_rows = _fetch_membership_rows(
        conn,
        teacher_id=int(teacher_id),
        academic_year=academic_year,
        academic_term=academic_term,
    )
    return _mappings_from_rows(_mapping_groups(membership_rows), single_only=single_only)


def resolve_teaching_class_display_name(
    conn: Any,
    *,
    teacher_id: int,
    teaching_class_name: str,
    course_code: str = "",
    academic_year: str = "",
    academic_term: str = "",
    default: str = "",
) -> str:
    raw_name = _text(teaching_class_name)
    if not raw_name:
        return _text(default)
    mappings = load_teaching_class_display_mappings(
        conn,
        int(teacher_id),
        academic_year=_text(academic_year),
        academic_term=_text(academic_term),
    )
    normalized = _alias_lookup_key(raw_name)
    course_code_text = _text(course_code)
    resolved = mappings.get((course_code_text, raw_name)) if course_code_text else ""
    if not resolved and course_code_text and normalized:
        resolved = mappings.get(("__normalized_course__", course_code_text, normalized))
    resolved = resolved or mappings.get(raw_name)
    if not resolved and normalized:
        resolved = mappings.get(("__normalized__", normalized))
    return resolved or _text(default) or raw_name
