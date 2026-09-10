"""Pure preview snapshots and transactional guards for the existing plan editor."""
import sqlite3

from fastapi import HTTPException


def lock_plan_row(conn, table: str, row_id: int) -> None:
    if table not in {"courses", "class_offerings", "class_offering_sessions"}:
        raise ValueError("Unsupported plan resource")
    if isinstance(conn, sqlite3.Connection):
        conn.execute(f"UPDATE {table} SET id = id WHERE id = ?", (int(row_id),))
    else:
        # FOR UPDATE also blocks new FK references while the content guard runs.
        conn.execute(f"SELECT id FROM {table} WHERE id = ? FOR UPDATE", (int(row_id),)).fetchone()


def offering_edit_snapshot(conn, offering) -> dict:
    from .session_learning_materials_service import has_material_bindings_table
    offering = dict(offering)
    offering_id = int(offering["id"])
    sessions = [dict(row) for row in conn.execute(
        "SELECT * FROM class_offering_sessions WHERE class_offering_id = ? ORDER BY order_index, id",
        (offering_id,),
    ).fetchall()]
    links = [dict(row) for row in conn.execute(
        "SELECT * FROM class_offering_class_links WHERE offering_id = ? ORDER BY class_id, id",
        (offering_id,),
    ).fetchall()]
    bindings = [dict(row) for row in conn.execute(
        """SELECT id, session_id, material_id, sort_order FROM class_offering_learning_materials
           WHERE class_offering_id = ? ORDER BY session_id, sort_order, id""", (offering_id,),
    ).fetchall()] if has_material_bindings_table(conn) else []
    protected = {int(row["id"]) for row in sessions if row.get("learning_material_id")}
    protected.update(int(row["session_id"]) for row in bindings if row["session_id"])
    for table in ("learning_material_progress", "session_material_generation_tasks",
                  "smart_classroom_checkin_sessions", "smart_classroom_checkin_students"):
        protected.update(int(row[0]) for row in conn.execute(
            f"""SELECT DISTINCT r.session_id FROM {table} r
                JOIN class_offering_sessions s ON s.id = r.session_id
                WHERE s.class_offering_id = ?""", (offering_id,),
        ).fetchall())
    return {"offering": offering, "sessions": sessions, "class_links": links,
            "material_bindings": bindings, "protected_session_ids": sorted(protected)}


def offering_edit_impact(snapshot: dict | None, payload: dict) -> dict:
    if not snapshot:
        return {"canceled_count": 0, "protected_session_count": 0, "blockers": []}
    proposed = {int(row["order_index"]): row for row in payload["plan"]["sessions"]}
    existing = snapshot["sessions"]
    protected = set(snapshot["protected_session_ids"])
    blockers = []
    before = snapshot["offering"]
    old_classes = {int(row["class_id"]) for row in snapshot["class_links"]} or {int(before["class_id"])}
    if existing and (any(int(before.get(key) or 0) != int(payload.get(key) or 0)
                         for key in ("course_id", "semester_id"))
                     or old_classes != set(payload["class_ids"])):
        blockers.append("已有课次的课堂不能在排课编辑中改换课程、学期或班级组成，请使用对应的课堂迁移或合班流程。")
    changed_protected = []
    for row in existing:
        next_row = proposed.get(int(row["order_index"]))
        if not next_row or int(row["id"]) not in protected:
            continue
        # Date, room and timetable corrections retain the same historical ID.
        # Reusing that ID for different lesson content would mislabel its records.
        bound_materials = [item["material_id"] for item in snapshot["material_bindings"]
                           if int(item["session_id"]) == int(row["id"])]
        current_primary = bound_materials[0] if bound_materials else row.get("learning_material_id")
        changes_material = (next_row.get("learning_material_id") is not None
                            and next_row["learning_material_id"] != current_primary)
        if changes_material or any(str(row.get(key) or "") != str(next_row.get(key) or "")
                                   for key in ("title", "content", "section_count")):
            changed_protected.append(int(row["id"]))
    if changed_protected:
        blockers.append(f"{len(changed_protected)} 个课次已有材料、学习、考勤或生成记录，不能覆盖为不同课时内容或主材料；可调整时间，或通过材料管理调整绑定。")
    return {
        "canceled_count": sum(int(row["order_index"]) not in proposed and row.get("schedule_status") != "cancelled" for row in existing),
        "protected_session_count": len(protected), "blockers": blockers,
    }


def validate_offering_edit(snapshot: dict | None, payload: dict) -> dict:
    impact = offering_edit_impact(snapshot, payload)
    if impact["blockers"]:
        raise HTTPException(409, " ".join(impact["blockers"]))
    return impact
