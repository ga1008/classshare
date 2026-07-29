from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any, Callable

from ..db.connection import get_configured_db_engine


MATERIAL_DELETE_ITEM_LIMIT = 12

_REFERENCE_TABLES = {
    "class_offering_learning_materials",
    "class_offering_sessions",
    "class_offerings",
    "course_lessons",
    "course_material_assignments",
    "learning_material_progress",
    "material_ai_import_records",
    "session_material_generation_tasks",
}

_SUBTREE_ID_SQL = """
    SELECT id
    FROM course_materials
    WHERE root_id = ?
      AND (material_path = ? OR material_path LIKE ?)
"""


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _available_reference_tables(conn) -> set[str]:
    engine = get_configured_db_engine()
    names = sorted(_REFERENCE_TABLES)
    placeholders = ",".join("?" for _ in names)
    if engine == "postgres":
        rows = conn.execute(
            f"""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = ? AND table_name IN ({placeholders})
            """,
            ("public", *names),
        ).fetchall()
        return {str(_row_dict(row).get("table_name") or "") for row in rows}
    rows = conn.execute(
        f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})",
        tuple(names),
    ).fetchall()
    return {str(_row_dict(row).get("name") or "") for row in rows}


def _subtree_params(material_row: Any) -> tuple[Any, ...]:
    material = _row_dict(material_row)
    path = str(material.get("material_path") or "")
    return (int(material["root_id"]), path, f"{path}/%")


def _query_count(conn, sql: str, params: tuple[Any, ...]) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        # Isolated unit-test schemas and old SQLite snapshots may not yet have
        # every optional integration column. Production migrations are strict.
        return 0
    if row is None:
        return 0
    try:
        return int(row[0] or 0)
    except (KeyError, TypeError):
        data = _row_dict(row)
        return int(next(iter(data.values()), 0) or 0)


def _query_items(conn, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    try:
        return [_row_dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []


def _classroom_title(row: dict[str, Any]) -> str:
    course_name = str(row.get("course_name") or "").strip() or "未命名课程"
    class_name = str(row.get("class_name") or "").strip() or "未命名班级"
    return f"{course_name} · {class_name}"


def _session_secondary(row: dict[str, Any], *, home: bool = False) -> str:
    if home:
        return "课堂首页"
    order_index = _safe_int(row.get("order_index"))
    title = str(row.get("session_title") or "").strip() or "未命名课次"
    return f"第 {order_index} 课次 · {title}" if order_index > 0 else title


def _build_item(
    *,
    primary: str,
    secondary: str = "",
    meta: str = "",
    url: str = "",
    affected_count: int = 1,
) -> dict[str, Any]:
    return {
        "primary": primary,
        "secondary": secondary,
        "meta": meta,
        "url": url,
        "affected_count": max(1, int(affected_count or 1)),
    }


def _append_group(
    groups: list[dict[str, Any]],
    *,
    key: str,
    label: str,
    blocker_label: str,
    count: int,
    effect: str,
    risk: str,
    rows: list[dict[str, Any]],
    serialize: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    if count <= 0:
        return
    items = [serialize(row) for row in rows]
    groups.append(
        {
            "key": key,
            "label": label,
            "blocker_label": blocker_label,
            "count": int(count),
            "effect": effect,
            "risk": risk,
            "items": items,
            "shown_item_count": len(items),
            "has_more": count > sum(int(item.get("affected_count") or 1) for item in items),
        }
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        loaded = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return loaded if isinstance(loaded, list) else []


def _material_subtree_ids(conn, material_row: Any) -> set[int]:
    return {
        _safe_int(_row_dict(row).get("id"))
        for row in conn.execute(
            f"SELECT id FROM course_materials WHERE id IN ({_SUBTREE_ID_SQL})",
            _subtree_params(material_row),
        ).fetchall()
        if _safe_int(_row_dict(row).get("id"))
    }


def _source_import_record_ids(conn, material_row: Any) -> set[int]:
    params = _subtree_params(material_row)
    ai_where = " OR ".join(
        f"{column} IN ({_SUBTREE_ID_SQL})"
        for column in ("package_material_id", "source_material_id", "parsed_material_id", "parent_material_id")
    )
    return {
        _safe_int(_row_dict(row).get("id"))
        for row in conn.execute(
            f"SELECT id FROM material_ai_import_records WHERE {ai_where}",
            params * 4,
        ).fetchall()
        if _safe_int(_row_dict(row).get("id"))
    }


def _final_transcript_source_dependencies(
    conn,
    *,
    source_record_ids: set[int],
    teacher_id: int,
    excluded_material_ids: set[int],
) -> list[dict[str, Any]]:
    if not source_record_ids:
        return []
    try:
        rows = conn.execute(
            """
            SELECT id AS reference_id, package_material_id, parsed_material_id, source_material_id,
                   document_type_label, export_payload_json, updated_at
            FROM material_ai_import_records
            WHERE teacher_id = ?
              AND document_type = 'final_grade_transcript'
              AND parse_status = 'completed'
            ORDER BY updated_at DESC, id DESC
            """,
            (int(teacher_id),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    dependencies: list[dict[str, Any]] = []
    source_labels = {
        "ordinary_grade_record": "平时成绩表",
        "exam_grade_record": "考核登分表",
    }
    for raw_row in rows:
        row = _row_dict(raw_row)
        material_ids = {
            _safe_int(row.get("package_material_id")),
            _safe_int(row.get("parsed_material_id")),
            _safe_int(row.get("source_material_id")),
        } - {0}
        if material_ids & excluded_material_ids:
            continue
        export_payload = _json_object(row.get("export_payload_json"))
        structured = _json_object(export_payload.get("structured"))
        lineage = _json_object(structured.get("source_lineage"))
        matched_labels: list[str] = []
        for key, label in source_labels.items():
            source = _json_object(lineage.get(key))
            if source.get("detached"):
                continue
            if _safe_int(source.get("record_id")) in source_record_ids:
                matched_labels.append(label)
        if not matched_labels:
            continue
        fields = _json_object(export_payload.get("fields"))
        dependencies.append(
            {
                **row,
                "course_name": str(fields.get("course_name") or "").strip(),
                "class_name": str(fields.get("class_name") or "").strip(),
                "source_labels": matched_labels,
            }
        )
    return dependencies


def _detach_final_transcript_sources(
    conn,
    *,
    source_record_ids: set[int],
    teacher_id: int,
    now: str,
) -> int:
    dependencies = _final_transcript_source_dependencies(
        conn,
        source_record_ids=source_record_ids,
        teacher_id=teacher_id,
        excluded_material_ids=set(),
    )
    detached_count = 0
    for row in dependencies:
        record_id = _safe_int(row.get("reference_id"))
        current = conn.execute(
            """
            SELECT export_payload_json, parsed_payload_json, warnings_json,
                   package_material_id, parsed_material_id, source_material_id
            FROM material_ai_import_records
            WHERE id = ?
            """,
            (record_id,),
        ).fetchone()
        if not current:
            continue
        current_data = _row_dict(current)
        export_payload = _json_object(current_data.get("export_payload_json"))
        structured = _json_object(export_payload.get("structured"))
        lineage = _json_object(structured.get("source_lineage"))
        changed = False
        for key in ("ordinary_grade_record", "exam_grade_record"):
            source = _json_object(lineage.get(key))
            active_record_id = _safe_int(source.get("record_id"))
            if active_record_id not in source_record_ids:
                continue
            source["historical_record_id"] = active_record_id
            source.pop("record_id", None)
            source["detached"] = True
            source["detached_at"] = now
            source["detach_reason"] = "来源材料已删除；期末成绩单保留生成时的成绩快照。"
            lineage[key] = source
            queryable = _json_object(export_payload.get("queryable_fields"))
            queryable[f"{key}_id"] = ""
            export_payload["queryable_fields"] = queryable
            changed = True
        if not changed:
            continue
        structured["source_lineage"] = lineage
        structured["warnings"] = list(dict.fromkeys([
            *(
                str(item).strip()
                for item in _json_array(structured.get("warnings"))
                if str(item or "").strip()
            ),
            "上游成绩材料已删除；本期末成绩单保留生成时快照，后续重新生成前须补齐来源。",
        ]))
        export_payload["structured"] = structured

        parsed_payload = _json_object(current_data.get("parsed_payload_json"))
        if isinstance(parsed_payload.get("export_payload"), dict):
            parsed_payload["export_payload"] = export_payload
        warnings = [
            str(item).strip()
            for item in _json_array(current_data.get("warnings_json"))
            if str(item or "").strip()
        ]
        warning = "上游成绩材料已删除；已保留生成时成绩快照和历史记录号。"
        if warning not in warnings:
            warnings.append(warning)
        export_payload_json = json.dumps(export_payload, ensure_ascii=False)
        parsed_payload_json = json.dumps(parsed_payload, ensure_ascii=False)
        conn.execute(
            """
            UPDATE material_ai_import_records
            SET export_payload_json = ?,
                parsed_payload_json = ?,
                warnings_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                export_payload_json,
                parsed_payload_json,
                json.dumps(warnings, ensure_ascii=False),
                now,
                record_id,
            ),
        )
        material_ids = {
            _safe_int(current_data.get("package_material_id")),
            _safe_int(current_data.get("parsed_material_id")),
            _safe_int(current_data.get("source_material_id")),
        } - {0}
        for material_id in material_ids:
            try:
                conn.execute(
                    """
                    UPDATE course_materials
                    SET ai_parse_result_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (parsed_payload_json, now, material_id),
                )
            except sqlite3.OperationalError:
                pass
        detached_count += 1
    return detached_count


def build_material_delete_impact(
    conn,
    material_row: Any,
    *,
    include_items: bool = True,
    item_limit: int = MATERIAL_DELETE_ITEM_LIMIT,
) -> dict[str, Any]:
    """Reverse trace every business reference affected by deleting a material subtree."""

    material = _row_dict(material_row)
    params = _subtree_params(material)
    limit = max(1, min(int(item_limit or MATERIAL_DELETE_ITEM_LIMIT), 50))
    groups: list[dict[str, Any]] = []
    available_tables = _available_reference_tables(conn)

    subtree_row = conn.execute(
        f"""
        SELECT COUNT(*) AS node_count,
               SUM(CASE WHEN node_type = 'file' THEN 1 ELSE 0 END) AS file_count,
               SUM(CASE WHEN node_type = 'folder' THEN 1 ELSE 0 END) AS folder_count
        FROM course_materials
        WHERE id IN ({_SUBTREE_ID_SQL})
        """,
        params,
    ).fetchone()
    subtree = _row_dict(subtree_row)
    subtree_summary = {
        "node_count": _safe_int(subtree.get("node_count")),
        "file_count": _safe_int(subtree.get("file_count")),
        "folder_count": _safe_int(subtree.get("folder_count")),
    }

    if "course_material_assignments" in available_tables:
        assignment_count = _query_count(
            conn,
            f"SELECT COUNT(*) FROM course_material_assignments WHERE material_id IN ({_SUBTREE_ID_SQL})",
            params,
        )
        assignment_rows = _query_items(
            conn,
            f"""
            SELECT a.id AS reference_id, a.material_id, a.class_offering_id,
                   m.name AS material_name, m.material_path,
                   o.semester, c.name AS course_name, cl.name AS class_name
            FROM course_material_assignments a
            JOIN course_materials m ON m.id = a.material_id
            JOIN class_offerings o ON o.id = a.class_offering_id
            LEFT JOIN courses c ON c.id = o.course_id
            LEFT JOIN classes cl ON cl.id = o.class_id
            WHERE a.material_id IN ({_SUBTREE_ID_SQL})
            ORDER BY a.id DESC
            LIMIT ?
            """,
            (*params, limit),
        ) if include_items and assignment_count else []
        _append_group(
            groups,
            key="classroom_assignments",
            label="课堂材料分配",
            blocker_label="课堂材料分配",
            count=assignment_count,
            effect="解除课堂访问分配",
            risk="unlink",
            rows=assignment_rows,
            serialize=lambda row: _build_item(
                primary=_classroom_title(row),
                secondary=f"分配节点：{row.get('material_name') or '未命名材料'}",
                meta=str(row.get("semester") or "").strip(),
                url=f"/classroom/{_safe_int(row.get('class_offering_id'))}",
            ),
        )

    if "course_lessons" in available_tables:
        lesson_count = _query_count(
            conn,
            f"SELECT COUNT(*) FROM course_lessons WHERE learning_material_id IN ({_SUBTREE_ID_SQL})",
            params,
        )
        lesson_rows = _query_items(
            conn,
            f"""
            SELECT l.id AS reference_id, l.course_id, l.order_index,
                   l.title AS lesson_title, c.name AS course_name,
                   m.name AS material_name
            FROM course_lessons l
            LEFT JOIN courses c ON c.id = l.course_id
            LEFT JOIN course_materials m ON m.id = l.learning_material_id
            WHERE l.learning_material_id IN ({_SUBTREE_ID_SQL})
            ORDER BY c.name, l.order_index, l.id
            LIMIT ?
            """,
            (*params, limit),
        ) if include_items and lesson_count else []
        _append_group(
            groups,
            key="course_lessons",
            label="课程课次引用",
            blocker_label="课程课次引用",
            count=lesson_count,
            effect="清空课程课次的材料指针",
            risk="unlink",
            rows=lesson_rows,
            serialize=lambda row: _build_item(
                primary=str(row.get("course_name") or "未命名课程"),
                secondary=(
                    f"第 {_safe_int(row.get('order_index'))} 课次 · "
                    f"{row.get('lesson_title') or '未命名课次'}"
                ),
                meta=f"材料：{row.get('material_name') or '未命名材料'}",
                url="/manage/teaching/courses",
            ),
        )

    has_multi_bindings = "class_offering_learning_materials" in available_tables
    if "class_offering_sessions" in available_tables:
        if has_multi_bindings:
            session_select = f"""
                SELECT lm.id AS reference_id, lm.material_id, lm.class_offering_id,
                       lm.session_id, s.order_index, s.title AS session_title,
                       o.semester, c.name AS course_name, cl.name AS class_name
                FROM class_offering_learning_materials lm
                JOIN class_offerings o ON o.id = lm.class_offering_id
                LEFT JOIN class_offering_sessions s
                       ON s.id = lm.session_id AND s.class_offering_id = lm.class_offering_id
                LEFT JOIN courses c ON c.id = o.course_id
                LEFT JOIN classes cl ON cl.id = o.class_id
                WHERE lm.session_id > 0
                  AND lm.material_id IN ({_SUBTREE_ID_SQL})
                UNION ALL
                SELECT s.id AS reference_id, s.learning_material_id AS material_id,
                       s.class_offering_id, s.id AS session_id, s.order_index,
                       s.title AS session_title, o.semester,
                       c.name AS course_name, cl.name AS class_name
                FROM class_offering_sessions s
                JOIN class_offerings o ON o.id = s.class_offering_id
                LEFT JOIN courses c ON c.id = o.course_id
                LEFT JOIN classes cl ON cl.id = o.class_id
                WHERE s.learning_material_id IN ({_SUBTREE_ID_SQL})
                  AND NOT EXISTS (
                      SELECT 1
                      FROM class_offering_learning_materials lm
                      WHERE lm.class_offering_id = s.class_offering_id
                        AND lm.session_id = s.id
                        AND lm.material_id = s.learning_material_id
                  )
            """
            session_params = params * 2
        else:
            session_select = f"""
                SELECT s.id AS reference_id, s.learning_material_id AS material_id,
                       s.class_offering_id, s.id AS session_id, s.order_index,
                       s.title AS session_title, o.semester,
                       c.name AS course_name, cl.name AS class_name
                FROM class_offering_sessions s
                JOIN class_offerings o ON o.id = s.class_offering_id
                LEFT JOIN courses c ON c.id = o.course_id
                LEFT JOIN classes cl ON cl.id = o.class_id
                WHERE s.learning_material_id IN ({_SUBTREE_ID_SQL})
            """
            session_params = params
        session_count = _query_count(conn, f"SELECT COUNT(*) FROM ({session_select}) refs", session_params)
        session_rows = _query_items(
            conn,
            f"SELECT * FROM ({session_select}) refs ORDER BY class_offering_id, order_index, reference_id LIMIT ?",
            (*session_params, limit),
        ) if include_items and session_count else []
        _append_group(
            groups,
            key="classroom_sessions",
            label="课堂课次材料",
            blocker_label="课堂课次引用",
            count=session_count,
            effect="从课次材料列表移除，并自动切换剩余主材料",
            risk="unlink",
            rows=session_rows,
            serialize=lambda row: _build_item(
                primary=_classroom_title(row),
                secondary=_session_secondary(row),
                meta=str(row.get("semester") or "").strip(),
                url=f"/classroom/{_safe_int(row.get('class_offering_id'))}",
            ),
        )

    if "class_offerings" in available_tables:
        if has_multi_bindings:
            home_select = f"""
                SELECT lm.id AS reference_id, lm.material_id, lm.class_offering_id,
                       o.semester, c.name AS course_name, cl.name AS class_name
                FROM class_offering_learning_materials lm
                JOIN class_offerings o ON o.id = lm.class_offering_id
                LEFT JOIN courses c ON c.id = o.course_id
                LEFT JOIN classes cl ON cl.id = o.class_id
                WHERE lm.session_id = 0
                  AND lm.material_id IN ({_SUBTREE_ID_SQL})
                UNION ALL
                SELECT o.id AS reference_id, o.home_learning_material_id AS material_id,
                       o.id AS class_offering_id, o.semester,
                       c.name AS course_name, cl.name AS class_name
                FROM class_offerings o
                LEFT JOIN courses c ON c.id = o.course_id
                LEFT JOIN classes cl ON cl.id = o.class_id
                WHERE o.home_learning_material_id IN ({_SUBTREE_ID_SQL})
                  AND NOT EXISTS (
                      SELECT 1
                      FROM class_offering_learning_materials lm
                      WHERE lm.class_offering_id = o.id
                        AND lm.session_id = 0
                        AND lm.material_id = o.home_learning_material_id
                  )
            """
            home_params = params * 2
        else:
            home_select = f"""
                SELECT o.id AS reference_id, o.home_learning_material_id AS material_id,
                       o.id AS class_offering_id, o.semester,
                       c.name AS course_name, cl.name AS class_name
                FROM class_offerings o
                LEFT JOIN courses c ON c.id = o.course_id
                LEFT JOIN classes cl ON cl.id = o.class_id
                WHERE o.home_learning_material_id IN ({_SUBTREE_ID_SQL})
            """
            home_params = params
        home_count = _query_count(conn, f"SELECT COUNT(*) FROM ({home_select}) refs", home_params)
        home_rows = _query_items(
            conn,
            f"SELECT * FROM ({home_select}) refs ORDER BY class_offering_id, reference_id LIMIT ?",
            (*home_params, limit),
        ) if include_items and home_count else []
        _append_group(
            groups,
            key="classroom_home",
            label="课堂首页材料",
            blocker_label="课堂首页材料",
            count=home_count,
            effect="从课堂首页材料列表移除，并自动切换剩余主材料",
            risk="unlink",
            rows=home_rows,
            serialize=lambda row: _build_item(
                primary=_classroom_title(row),
                secondary=_session_secondary(row, home=True),
                meta=str(row.get("semester") or "").strip(),
                url=f"/classroom/{_safe_int(row.get('class_offering_id'))}",
            ),
        )

    if "material_ai_import_records" in available_tables:
        ai_where = " OR ".join(
            f"{column} IN ({_SUBTREE_ID_SQL})"
            for column in ("package_material_id", "source_material_id", "parsed_material_id", "parent_material_id")
        )
        ai_params = params * 4
        ai_count = _query_count(
            conn,
            f"SELECT COUNT(*) FROM material_ai_import_records WHERE {ai_where}",
            ai_params,
        )
        ai_rows = _query_items(
            conn,
            f"""
            SELECT id AS reference_id, document_type_label, document_type,
                   parse_status, source_file_name, updated_at
            FROM material_ai_import_records
            WHERE {ai_where}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (*ai_params, limit),
        ) if include_items and ai_count else []
        _append_group(
            groups,
            key="ai_import_records",
            label="AI 导入记录",
            blocker_label="AI导入记录",
            count=ai_count,
            effect="保留导入历史，仅清空材料关联",
            risk="preserve",
            rows=ai_rows,
            serialize=lambda row: _build_item(
                primary=(
                    str(row.get("document_type_label") or "").strip()
                    or str(row.get("source_file_name") or "").strip()
                    or f"AI 导入记录 #{_safe_int(row.get('reference_id'))}"
                ),
                secondary=f"状态：{row.get('parse_status') or '未知'}",
                meta=str(row.get("source_file_name") or "").strip(),
            ),
        )
        source_record_ids = _source_import_record_ids(conn, material)
        transcript_dependencies = _final_transcript_source_dependencies(
            conn,
            source_record_ids=source_record_ids,
            teacher_id=_safe_int(material.get("teacher_id")),
            excluded_material_ids=_material_subtree_ids(conn, material),
        )
        _append_group(
            groups,
            key="final_grade_transcript_sources",
            label="期末成绩单来源引用",
            blocker_label="期末成绩单来源引用",
            count=len(transcript_dependencies),
            effect="保留已生成成绩快照，并解除对待删材料的活动来源关联",
            risk="preserve",
            rows=transcript_dependencies[:limit],
            serialize=lambda row: _build_item(
                primary=" · ".join(
                    value
                    for value in (
                        str(row.get("course_name") or "").strip(),
                        str(row.get("class_name") or "").strip(),
                    )
                    if value
                ) or "期末成绩单",
                secondary=f"引用：{'、'.join(row.get('source_labels') or [])}",
                meta=f"记录 #{_safe_int(row.get('reference_id'))} · 删除后保留成绩快照",
                url=(
                    f"/materials/view/{_safe_int(row.get('package_material_id'))}"
                    if _safe_int(row.get("package_material_id"))
                    else ""
                ),
            ),
        )

    if "session_material_generation_tasks" in available_tables:
        generation_count = _query_count(
            conn,
            f"SELECT COUNT(*) FROM session_material_generation_tasks WHERE generated_material_id IN ({_SUBTREE_ID_SQL})",
            params,
        )
        generation_rows = _query_items(
            conn,
            f"""
            SELECT t.id AS reference_id, t.status, t.document_type,
                   t.class_offering_id, t.session_id,
                   s.order_index, s.title AS session_title,
                   o.semester, c.name AS course_name, cl.name AS class_name
            FROM session_material_generation_tasks t
            JOIN class_offerings o ON o.id = t.class_offering_id
            LEFT JOIN class_offering_sessions s ON s.id = t.session_id
            LEFT JOIN courses c ON c.id = o.course_id
            LEFT JOIN classes cl ON cl.id = o.class_id
            WHERE t.generated_material_id IN ({_SUBTREE_ID_SQL})
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (*params, limit),
        ) if include_items and generation_count else []
        _append_group(
            groups,
            key="generation_tasks",
            label="材料生成任务",
            blocker_label="材料生成任务",
            count=generation_count,
            effect="保留任务历史，仅清空生成结果关联",
            risk="preserve",
            rows=generation_rows,
            serialize=lambda row: _build_item(
                primary=_classroom_title(row),
                secondary=_session_secondary(row),
                meta=f"任务状态：{row.get('status') or '未知'}",
                url=f"/classroom/{_safe_int(row.get('class_offering_id'))}",
            ),
        )

    if "learning_material_progress" in available_tables:
        progress_count = _query_count(
            conn,
            f"SELECT COUNT(*) FROM learning_material_progress WHERE material_id IN ({_SUBTREE_ID_SQL})",
            params,
        )
        progress_rows = _query_items(
            conn,
            f"""
            SELECT p.class_offering_id, COUNT(*) AS progress_count,
                   SUM(CASE WHEN COALESCE(p.completed, 0) = 1 THEN 1 ELSE 0 END) AS completed_count,
                   SUM(CASE WHEN COALESCE(p.mastered, 0) = 1 THEN 1 ELSE 0 END) AS mastered_count,
                   MAX(p.last_viewed_at) AS last_viewed_at,
                   o.semester, c.name AS course_name, cl.name AS class_name
            FROM learning_material_progress p
            JOIN class_offerings o ON o.id = p.class_offering_id
            LEFT JOIN courses c ON c.id = o.course_id
            LEFT JOIN classes cl ON cl.id = o.class_id
            WHERE p.material_id IN ({_SUBTREE_ID_SQL})
            GROUP BY p.class_offering_id, o.semester, c.name, cl.name
            ORDER BY progress_count DESC, p.class_offering_id
            LIMIT ?
            """,
            (*params, limit),
        ) if include_items and progress_count else []
        _append_group(
            groups,
            key="learning_progress",
            label="学生学习进度",
            blocker_label="学生学习进度",
            count=progress_count,
            effect="随材料删除学习进度记录（不可恢复）",
            risk="delete",
            rows=progress_rows,
            serialize=lambda row: _build_item(
                primary=_classroom_title(row),
                secondary=(
                    f"{_safe_int(row.get('progress_count'))} 条记录 · "
                    f"已完成 {_safe_int(row.get('completed_count'))} · "
                    f"已掌握 {_safe_int(row.get('mastered_count'))}"
                ),
                meta="删除后无法恢复",
                url=f"/classroom/{_safe_int(row.get('class_offering_id'))}",
                affected_count=_safe_int(row.get("progress_count")),
            ),
        )

    blockers: dict[str, int] = {}
    for group in groups:
        label = str(group["blocker_label"])
        blockers[label] = blockers.get(label, 0) + int(group["count"])

    total_count = sum(int(group["count"]) for group in groups)
    destructive_count = sum(int(group["count"]) for group in groups if group["risk"] == "delete")
    preserved_history_count = sum(int(group["count"]) for group in groups if group["risk"] == "preserve")
    token_payload = {
        "material_id": _safe_int(material.get("id")),
        "root_id": _safe_int(material.get("root_id")),
        "material_path": str(material.get("material_path") or ""),
        "updated_at": str(material.get("updated_at") or ""),
        "subtree": subtree_summary,
        "groups": [(group["key"], int(group["count"])) for group in groups],
    }
    impact_token = hashlib.sha256(
        json.dumps(token_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return {
        "material": {
            "id": _safe_int(material.get("id")),
            "name": str(material.get("name") or "未命名材料"),
            "node_type": str(material.get("node_type") or "file"),
            "material_path": str(material.get("material_path") or ""),
        },
        "subtree": subtree_summary,
        "groups": groups,
        "blockers": blockers,
        "total_reference_count": total_count,
        "destructive_reference_count": destructive_count,
        "preserved_history_count": preserved_history_count,
        "can_delete_directly": total_count == 0,
        "impact_token": impact_token,
    }


def _execute_if_table(
    conn,
    available_tables: set[str],
    table_name: str,
    sql: str,
    params: tuple[Any, ...],
) -> int:
    if table_name not in available_tables:
        return 0
    cursor = conn.execute(sql, params)
    return max(0, int(cursor.rowcount or 0))


def unlink_material_delete_references(
    conn,
    material_row: Any,
    *,
    impact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Remove only material links, preserving audit/history rows wherever possible."""

    available_tables = _available_reference_tables(conn)
    impact = impact or build_material_delete_impact(conn, material_row, include_items=False)
    params = _subtree_params(material_row)
    now = datetime.now().isoformat()
    source_record_ids = (
        _source_import_record_ids(conn, material_row)
        if "material_ai_import_records" in available_tables
        else set()
    )

    _execute_if_table(
        conn,
        available_tables,
        "course_material_assignments",
        f"DELETE FROM course_material_assignments WHERE material_id IN ({_SUBTREE_ID_SQL})",
        params,
    )
    _execute_if_table(
        conn,
        available_tables,
        "course_lessons",
        f"""
        UPDATE course_lessons
        SET learning_material_id = NULL, updated_at = ?
        WHERE learning_material_id IN ({_SUBTREE_ID_SQL})
        """,
        (now, *params),
    )

    has_multi_bindings = "class_offering_learning_materials" in available_tables
    if has_multi_bindings:
        conn.execute(
            f"DELETE FROM class_offering_learning_materials WHERE material_id IN ({_SUBTREE_ID_SQL})",
            params,
        )

    if "class_offering_sessions" in available_tables:
        if has_multi_bindings:
            conn.execute(
                f"""
                UPDATE class_offering_sessions
                SET learning_material_id = (
                        SELECT lm.material_id
                        FROM class_offering_learning_materials lm
                        JOIN course_materials remaining ON remaining.id = lm.material_id
                        WHERE lm.class_offering_id = class_offering_sessions.class_offering_id
                          AND lm.session_id = class_offering_sessions.id
                        ORDER BY lm.sort_order, lm.id
                        LIMIT 1
                    ),
                    updated_at = ?
                WHERE learning_material_id IN ({_SUBTREE_ID_SQL})
                """,
                (now, *params),
            )
        else:
            conn.execute(
                f"""
                UPDATE class_offering_sessions
                SET learning_material_id = NULL, updated_at = ?
                WHERE learning_material_id IN ({_SUBTREE_ID_SQL})
                """,
                (now, *params),
            )

    if "class_offerings" in available_tables:
        if has_multi_bindings:
            conn.execute(
                f"""
                UPDATE class_offerings
                SET home_learning_material_id = (
                    SELECT lm.material_id
                    FROM class_offering_learning_materials lm
                    JOIN course_materials remaining ON remaining.id = lm.material_id
                    WHERE lm.class_offering_id = class_offerings.id
                      AND lm.session_id = 0
                    ORDER BY lm.sort_order, lm.id
                    LIMIT 1
                )
                WHERE home_learning_material_id IN ({_SUBTREE_ID_SQL})
                """,
                params,
            )
        else:
            conn.execute(
                f"""
                UPDATE class_offerings
                SET home_learning_material_id = NULL
                WHERE home_learning_material_id IN ({_SUBTREE_ID_SQL})
                """,
                params,
            )

    if "material_ai_import_records" in available_tables:
        _detach_final_transcript_sources(
            conn,
            source_record_ids=source_record_ids,
            teacher_id=_safe_int(_row_dict(material_row).get("teacher_id")),
            now=now,
        )
        for column in ("package_material_id", "source_material_id", "parsed_material_id", "parent_material_id"):
            conn.execute(
                f"""
                UPDATE material_ai_import_records
                SET {column} = NULL, updated_at = ?
                WHERE {column} IN ({_SUBTREE_ID_SQL})
                """,
                (now, *params),
            )

    _execute_if_table(
        conn,
        available_tables,
        "session_material_generation_tasks",
        f"""
        UPDATE session_material_generation_tasks
        SET generated_material_id = NULL, updated_at = ?
        WHERE generated_material_id IN ({_SUBTREE_ID_SQL})
        """,
        (now, *params),
    )
    _execute_if_table(
        conn,
        available_tables,
        "learning_material_progress",
        f"DELETE FROM learning_material_progress WHERE material_id IN ({_SUBTREE_ID_SQL})",
        params,
    )

    return impact
