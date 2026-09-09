"""Add inherited library filter labels to rows already authorized for display.

This service only decorates responses; material access and ownership remain
with the existing authorization services.
"""
from typing import Any

from .materials_service import is_descendant_path, is_git_internal_material_path


def attach_material_assignment_facets(conn, rows) -> list[dict[str, Any]]:
    """Decorate already-visible rows with the package's effective filter labels.

    Scope is managed at the outermost folder, while classroom assignments grant
    access to their descendants. New Git children must follow both contracts in
    library filters without creating duplicate assignments or changing access.
    """
    material_rows = [dict(row) for row in rows if not is_git_internal_material_path(row["material_path"])]
    material_ids = [int(row["id"]) for row in material_rows if row.get("id")]
    if not material_ids:
        return material_rows

    root_ids = sorted({int(row.get("root_id") or row["id"]) for row in material_rows})
    root_placeholders = ", ".join("?" for _ in root_ids)
    root_rows = conn.execute(
        f"""
        SELECT id, teacher_id, material_path, scope_level,
               school_code, school_name, college, department
        FROM course_materials WHERE id IN ({root_placeholders})
        """,
        root_ids,
    ).fetchall()
    roots = {int(row["id"]): dict(row) for row in root_rows}
    # This is response decoration after _material_visibility_condition. Keep
    # ownership and the database's authorization metadata untouched.
    for item in material_rows:
        root = roots.get(int(item.get("root_id") or item["id"]))
        if (
            root
            and int(root["id"]) != int(item["id"])
            and int(root["teacher_id"]) == int(item["teacher_id"])
            and is_descendant_path(str(item["material_path"]), str(root["material_path"]))
        ):
            for field in ("scope_level", "school_code", "school_name", "college", "department"):
                if field in root:
                    item[field] = root[field]

    assignment_rows = conn.execute(
        f"""
        SELECT a.material_id, assigned.root_id, assigned.material_path,
               c.name AS class_name,
               co.name AS course_name,
               COALESCE(NULLIF(sem.name, ''), NULLIF(o.semester, ''), '') AS semester_label
        FROM course_material_assignments a
        JOIN course_materials assigned ON assigned.id = a.material_id
        JOIN class_offerings o ON o.id = a.class_offering_id
        JOIN classes c ON c.id = o.class_id
        JOIN courses co ON co.id = o.course_id
        LEFT JOIN academic_semesters sem ON sem.id = o.semester_id
        WHERE assigned.root_id IN ({root_placeholders})
        ORDER BY co.name, c.name
        """,
        root_ids,
    ).fetchall()
    by_path: dict[tuple[int, str], dict[str, set[str]]] = {}
    for row in assignment_rows:
        if is_git_internal_material_path(row["material_path"]):
            continue
        course_name = str(row["course_name"] or "").strip()
        class_name = str(row["class_name"] or "").strip()
        semester_label = str(row["semester_label"] or "").strip()
        bucket = by_path.setdefault(
            (int(row["root_id"]), str(row["material_path"])),
            {"courses": set(), "classes": set(), "offerings": set()},
        )
        if course_name:
            bucket["courses"].add(course_name)
        if class_name:
            bucket["classes"].add(class_name)
        label_parts = [part for part in (course_name, class_name, semester_label) if part]
        if label_parts:
            bucket["offerings"].add(" / ".join(label_parts))

    for item in material_rows:
        labels = {"courses": set(), "classes": set(), "offerings": set()}
        root_id = int(item.get("root_id") or item["id"])
        parts = str(item["material_path"]).split("/")
        # Prefix lookups are O(path depth), not one query or a scan of every
        # classroom assignment per file. Segment boundaries exclude siblings.
        for length in range(1, len(parts) + 1):
            inherited = by_path.get((root_id, "/".join(parts[:length])))
            if inherited:
                for key in labels:
                    labels[key].update(inherited[key])
        item["assigned_course_names"] = sorted(labels["courses"], key=lambda value: value.lower())
        item["assigned_class_names"] = sorted(labels["classes"], key=lambda value: value.lower())
        item["assigned_offering_labels"] = sorted(labels["offerings"], key=lambda value: value.lower())
    return material_rows
