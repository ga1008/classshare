"""Audit/repair chronological ordinals and Git bindings from stored data only.

Default dry-run rolls all changes back. --apply is required for persistence.
No fixture generation, JWXT/Git network call, content export or file write occurs.
Run after startup migrations, preferably in the deployment maintenance window.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_repair(conn, *, teacher_id=None, include_repositories=True, now=None):
    from classroom_app.services.academic_schedule_prediction_service import _table_exists
    from classroom_app.services.schedule_learning_repair_service import repair_stored_schedule_snapshot

    if isinstance(conn, sqlite3.Connection) and not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    clauses, args = (" WHERE teacher_id=?", (int(teacher_id),)) if teacher_id else ("", ())
    snapshots = ([dict(row) for row in conn.execute(
        "SELECT teacher_id,semester_id FROM teacher_academic_schedule_snapshots" + clauses + " ORDER BY teacher_id,semester_id", args).fetchall()]
        if _table_exists(conn, "teacher_academic_schedule_snapshots") else [])
    repositories = []
    if include_repositories and all(_table_exists(conn, table) for table in ("course_materials", "course_material_assignments")):
        repositories = [dict(row) for row in conn.execute(
            """SELECT r.* FROM course_materials r WHERE r.id=r.root_id AND r.node_type='folder'
               AND r.git_repo_status='repository' AND EXISTS (
                 SELECT 1 FROM course_material_assignments a JOIN course_materials m ON m.id=a.material_id
                 JOIN class_offerings o ON o.id=a.class_offering_id
                 WHERE m.root_id=r.id AND o.teacher_id=r.teacher_id)"""
            + (" AND r.teacher_id=?" if teacher_id else "") + " ORDER BY r.id", args).fetchall()]
    # Git publication locks repository roots before course/offering rows. Take
    # every repository root first so this combined repair follows the same order.
    for root in repositories:
        if isinstance(conn, sqlite3.Connection):
            conn.execute("UPDATE course_materials SET id=id WHERE id=?", (root["id"],))
        else:
            conn.execute("SELECT id FROM course_materials WHERE id=? FOR UPDATE", (root["id"],)).fetchone()
    session_sql = """SELECT s.id,s.class_offering_id,s.order_index,s.session_date,s.academic_section_text,s.learning_material_id
        FROM class_offering_sessions s JOIN class_offerings o ON o.id=s.class_offering_id"""
    session_sql += (" WHERE o.teacher_id=?" if teacher_id else "") + " ORDER BY s.id"
    before = {int(row["id"]): dict(row) for row in conn.execute(session_sql, args).fetchall()}
    schedule_reports = [repair_stored_schedule_snapshot(conn, row["teacher_id"], row["semester_id"], now=now) for row in snapshots]
    repository_reports = []
    if repositories:
        from classroom_app.services.materials_git_service import _fetch_subtree_rows
        from classroom_app.services.git_learning_bindings_service import sync_repository_learning_bindings
        for root in repositories:
            # Reload after the root lock; never use metadata from a racing Git
            # transaction or call attach_git_repository_metadata (it can commit).
            current = dict(conn.execute("SELECT * FROM course_materials WHERE id=?", (root["id"],)).fetchone())
            report = sync_repository_learning_bindings(conn, current, _fetch_subtree_rows(conn, current), current["teacher_id"])
            repository_reports.append({"teacher_id": int(current["teacher_id"]), "repository_id": int(current["id"]),
                **{key: report.get(key, 0) for key in ("target_classroom_count", "total_assignments", "total_home_assignments", "removed_count", "suppressed_count", "corrected_legacy_count")},
                "warnings": [*report.get("unresolved", []), *report.get("warnings", [])]})
    after = {int(row["id"]): dict(row) for row in conn.execute(session_sql, args).fetchall()}
    changed = [{"session_id": sid, "class_offering_id": row["class_offering_id"],
                "old_order_index": before[sid]["order_index"], "order_index": row["order_index"],
                "physical_slot_repaired": (row["session_date"], row["academic_section_text"]) != (before[sid]["session_date"], before[sid]["academic_section_text"]),
                "primary_material_changed": row["learning_material_id"] != before[sid]["learning_material_id"]}
               for sid, row in after.items() if sid in before and row != before[sid]]
    return {"teacher_count": len({row["teacher_id"] for row in snapshots} | {row["teacher_id"] for row in repositories}),
            "semester_count": len(snapshots), "repository_count": len(repositories), "session_count": len(after),
            "changed_session_count": len(changed), "sessions": changed,
            "schedules": schedule_reports, "repositories": repository_reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="计算全部变更后回滚（默认）")
    mode.add_argument("--apply", action="store_true", help="提交已审阅的修复")
    parser.add_argument("--teacher-id", type=int, help="只修复指定教师")
    parser.add_argument("--skip-repositories", action="store_true", help="仅修复课次，不刷新已分配Git教材")
    options = parser.parse_args()
    from classroom_app.db import get_db_connection
    try:
        with get_db_connection() as conn:
            try:
                report = run_repair(conn, teacher_id=options.teacher_id, include_repositories=not options.skip_repositories)
                if options.apply:
                    conn.commit()
                else:
                    conn.rollback()
            except Exception:
                conn.rollback()
                raise
        print(json.dumps({"mode": "apply" if options.apply else "dry-run", "committed": bool(options.apply), **report}, ensure_ascii=False, indent=2))
    except Exception as exc:
        # No connection strings, material content or credential-bearing exception
        # text may leak through a production maintenance report.
        print(json.dumps({"status": "failed", "committed": False, "error_type": type(exc).__name__,
                          "message": "修复失败，全部变更已回滚；请在受控日志中排查。"}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
