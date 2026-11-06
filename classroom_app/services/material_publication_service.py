"""Classroom grants, live lesson references and teacher-only archive associations."""

from datetime import datetime
import sqlite3

from fastapi import HTTPException

from .materials_service import (
    get_learning_material_assignment_anchor,
    is_descendant_path,
    is_git_internal_material_path,
)


def _sqlite_columns(conn, table: str) -> set[str] | None:
    if isinstance(conn, sqlite3.Connection):
        return {row[0] for row in conn.execute("SELECT name FROM pragma_table_info(?)", (table,))}
    return None


def _binding_sources(conn) -> str:
    from .session_learning_materials_service import has_material_bindings_table

    sources = []
    columns = _sqlite_columns(conn, "class_offerings")
    if columns is None or "home_learning_material_id" in columns:
        sources.append("SELECT id AS class_offering_id, home_learning_material_id AS material_id FROM class_offerings")
    columns = _sqlite_columns(conn, "class_offering_sessions")
    if columns is None or "learning_material_id" in columns:
        sources.append("SELECT class_offering_id, learning_material_id AS material_id FROM class_offering_sessions")
    if has_material_bindings_table(conn):
        sources.append("""
            SELECT b.class_offering_id, b.material_id
            FROM class_offering_learning_materials b
            WHERE b.session_id = 0 OR EXISTS (
                SELECT 1 FROM class_offering_sessions s
                WHERE s.id = b.session_id AND s.class_offering_id = b.class_offering_id
            )
        """)
    return " UNION ".join(sources)


def _has_publication_mode(conn) -> bool:
    columns = _sqlite_columns(conn, "course_material_assignments")
    return columns is None or "publication_mode" in columns


def get_restricted_material_paths(conn, root_ids) -> dict[int, list[str]]:
    """Process material roots and files stay in the teacher archive, even inside published folders."""
    ids = sorted({int(value) for value in root_ids if value})
    if not ids:
        return {}
    columns = _sqlite_columns(conn, "material_ai_import_records")
    if columns is not None and "document_group" not in columns:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT m.root_id, m.material_path
        FROM course_materials m
        WHERE m.root_id IN ({placeholders}) AND (
            EXISTS (SELECT 1 FROM material_ai_import_records r
                    WHERE r.package_material_id = m.id AND r.document_group = 'final_material')
            OR EXISTS (SELECT 1 FROM material_ai_import_records r
                       WHERE r.source_material_id = m.id AND r.document_group = 'final_material')
            OR EXISTS (SELECT 1 FROM material_ai_import_records r
                       WHERE r.parsed_material_id = m.id AND r.document_group = 'final_material')
        )
        """,
        ids,
    ).fetchall()
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(int(row["root_id"]), []).append(str(row["material_path"]))
    return result


def is_restricted_material(material, restricted_paths) -> bool:
    return any(
        is_descendant_path(material["material_path"], path)
        for path in restricted_paths.get(int(material["root_id"] or 0), ())
    )


def get_classroom_publication_nodes(conn, class_offering_id: int) -> list[dict]:
    return get_classroom_publication_nodes_for_offerings(conn, [class_offering_id]).get(class_offering_id, [])


def get_classroom_publication_context(conn, class_offering_id: int) -> tuple[list[dict], dict[int, list[str]]]:
    """Return publication nodes and the same request's archive exclusions."""
    nodes, restricted_paths = _load_classroom_publication_nodes_for_offerings(conn, [class_offering_id])
    return nodes.get(class_offering_id, []), restricted_paths


def get_classroom_publication_nodes_for_offerings(
    conn, offering_ids, *, root_id=None, root_ids=None, restricted_paths=None,
) -> dict[int, list[dict]]:
    nodes, _ = _load_classroom_publication_nodes_for_offerings(
        conn, offering_ids, root_id=root_id, root_ids=root_ids, restricted_paths=restricted_paths,
    )
    return nodes


def _load_classroom_publication_nodes_for_offerings(
    conn, offering_ids, *, root_id=None, root_ids=None, restricted_paths=None,
) -> tuple[dict[int, list[dict]], dict[int, list[str]]]:
    """Read the same published roots for every classroom member, including its teacher."""
    ids = sorted({int(value) for value in offering_ids if value})
    if not ids:
        return {}, {}
    placeholders = ", ".join("?" for _ in ids)
    roots = [root_id] if root_id is not None else sorted({int(value) for value in (root_ids or ()) if value})
    if root_ids is not None and not roots:
        return {}, {}
    root_filter = "AND m.root_id IN (" + ", ".join("?" for _ in roots) + ")" if roots else ""
    params = [*ids, *roots]
    mode = "a.publication_mode" if _has_publication_mode(conn) else "NULL"
    assignments = conn.execute(
        f"""
        SELECT m.*, a.class_offering_id, a.created_at AS assigned_at,
               a.assigned_by_teacher_id AS publication_actor, {mode} AS publication_mode
        FROM course_material_assignments a
        JOIN course_materials m ON m.id = a.material_id
        WHERE a.class_offering_id IN ({placeholders}) {root_filter}
        """,
        params,
    ).fetchall()
    binding_sources = _binding_sources(conn)
    bindings = conn.execute(
        f"""
        SELECT m.*, b.class_offering_id
        FROM ({binding_sources}) b
        JOIN class_offerings o ON o.id = b.class_offering_id
        JOIN course_materials m ON m.id = b.material_id AND m.teacher_id = o.teacher_id
        WHERE b.class_offering_id IN ({placeholders}) {root_filter}
        """,
        params,
    ).fetchall() if binding_sources else []
    if restricted_paths is None:
        restricted_paths = get_restricted_material_paths(
            conn, (row["root_id"] for row in (*assignments, *bindings))
        )
    by_offering: dict[int, dict[int, dict]] = {}
    bound_paths: dict[tuple[int, int], list[str]] = {}
    anchor_cache: dict[int, dict] = {}
    # Resolve the same package root once for all lesson references in this
    # request. This is structural data only; each row and resulting anchor
    # still passes the archive exclusion checks below.
    from .html_package_service import is_package_entry_candidate_name
    from .material_render_service import MaterialRenderReadIndex

    package_candidates = [row for row in bindings if row["parent_id"]
                          and not (row["node_type"] == "file" and row["preview_type"] == "markdown")
                          and is_package_entry_candidate_name(row["name"])
                          and not is_git_internal_material_path(row["material_path"])
                          and not is_restricted_material(row, restricted_paths)]
    package_index = MaterialRenderReadIndex(conn, package_candidates) if package_candidates else None
    for row in bindings:
        offering_id = int(row["class_offering_id"])
        material_id = int(row["id"])
        if is_git_internal_material_path(row["material_path"]) or is_restricted_material(row, restricted_paths):
            continue
        key = (offering_id, int(row["root_id"] or 0))
        bound_paths.setdefault(key, []).append(row["material_path"])
        if material_id not in anchor_cache:
            anchor_cache[material_id] = dict(get_learning_material_assignment_anchor(conn, row, package_read_index=package_index))
        anchor = anchor_cache[material_id]
        if not is_restricted_material(anchor, restricted_paths):
            by_offering.setdefault(offering_id, {})[int(anchor["id"])] = anchor
    for row in assignments:
        offering_id = int(row["class_offering_id"])
        mode = row["publication_mode"]
        if mode not in (None, "explicit", "explicit_archive"):
            continue
        if mode is None:
            if row["publication_actor"] != row["teacher_id"]:
                continue
            # Historical rows shared both purposes. An active lesson within
            # this node is evidence of a derived grant, so keep its narrow span.
            paths = bound_paths.get((offering_id, int(row["root_id"] or 0)), ())
            if any(is_descendant_path(path, row["material_path"]) for path in paths):
                continue
        if is_git_internal_material_path(row["material_path"]) or is_restricted_material(row, restricted_paths):
            continue
        by_offering.setdefault(offering_id, {})[int(row["id"])] = dict(row)
    return {
        offering_id: sorted(nodes.values(), key=lambda item: (item["root_id"] or 0, len(item["material_path"]), item["material_path"]))
        for offering_id, nodes in by_offering.items()
    }, restricted_paths


def mark_legacy_binding_publications(conn, class_offering_id=None) -> None:
    """Keep a legacy lesson-derived association from becoming a grant after unlinking."""
    if not _has_publication_mode(conn):
        return
    sources = _binding_sources(conn)
    if not sources:
        return
    offering_filter = "AND course_material_assignments.class_offering_id = ?" if class_offering_id is not None else ""
    conn.execute(
        f"""
        WITH bindings AS ({sources})
        UPDATE course_material_assignments
        SET publication_mode = 'binding'
        WHERE publication_mode IS NULL {offering_filter}
          AND EXISTS (
              SELECT 1 FROM bindings b
              JOIN course_materials m ON m.id = b.material_id
              JOIN course_materials anchor ON anchor.id = course_material_assignments.material_id
              WHERE b.class_offering_id = course_material_assignments.class_offering_id
                AND m.root_id = anchor.root_id
                AND (m.material_path = anchor.material_path
                     OR SUBSTR(m.material_path, 1, LENGTH(anchor.material_path) + 1) = anchor.material_path || '/')
          )
        """,
        () if class_offering_id is None else (class_offering_id,),
    )


def record_classroom_material_publication(conn, material_id, class_offering_id, teacher_id, *, mode="explicit", now=None) -> None:
    if mode not in {"explicit", "binding", "archive"}:
        raise ValueError("Invalid material publication mode")
    if mode == "explicit":
        material = conn.execute("SELECT * FROM course_materials WHERE id = ?", (material_id,)).fetchone()
        if material is None or is_git_internal_material_path(material["material_path"]):
            raise HTTPException(404, "材料不存在")
        if is_restricted_material(material, get_restricted_material_paths(conn, [material["root_id"]])):
            raise HTTPException(403, "过程材料仅供教师归档")
    stamp = now or datetime.now().isoformat()
    if not _has_publication_mode(conn):
        conn.execute(
            """
            INSERT INTO course_material_assignments
                (material_id, class_offering_id, assigned_by_teacher_id, created_at)
            SELECT ?, ?, ?, ?
            WHERE NOT EXISTS (SELECT 1 FROM course_material_assignments WHERE material_id = ? AND class_offering_id = ?)
            """,
            (material_id, class_offering_id, teacher_id, stamp, material_id, class_offering_id),
        )
        return
    conn.execute(
        """
        INSERT INTO course_material_assignments
            (material_id, class_offering_id, assigned_by_teacher_id, created_at, publication_mode)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (material_id, class_offering_id) DO UPDATE SET
            publication_mode = CASE
                WHEN course_material_assignments.publication_mode = 'explicit_archive' THEN 'explicit_archive'
                WHEN (excluded.publication_mode = 'explicit' AND course_material_assignments.publication_mode = 'archive')
                  OR (excluded.publication_mode = 'archive' AND course_material_assignments.publication_mode = 'explicit')
                    THEN 'explicit_archive'
                WHEN excluded.publication_mode = 'binding' THEN course_material_assignments.publication_mode
                ELSE excluded.publication_mode
            END,
            assigned_by_teacher_id = CASE WHEN excluded.publication_mode = 'explicit'
                THEN excluded.assigned_by_teacher_id ELSE course_material_assignments.assigned_by_teacher_id END
        WHERE excluded.publication_mode != 'binding'
          AND (course_material_assignments.publication_mode IS NULL
               OR course_material_assignments.publication_mode NOT IN (excluded.publication_mode, 'explicit_archive'))
        """,
        (material_id, class_offering_id, teacher_id, stamp, mode),
    )


def remove_classroom_material_publication(conn, material_id, class_offering_id) -> None:
    params = (material_id, class_offering_id)
    conn.execute(
        """
        DELETE FROM course_material_assignments
        WHERE material_id = ? AND class_offering_id = ?
          AND (publication_mode IS NULL OR publication_mode = 'explicit')
        """,
        params,
    )
    conn.execute(
        """
        UPDATE course_material_assignments SET publication_mode = 'archive'
        WHERE material_id = ? AND class_offering_id = ? AND publication_mode = 'explicit_archive'
        """,
        params,
    )
