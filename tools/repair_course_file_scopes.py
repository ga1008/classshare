"""回填被误判成 private 的课堂共享资源作用域。

历史缺陷：``chunked_uploads.is_public`` / ``is_teacher_resource`` 在 PostgreSQL 里
是 TEXT 列，完成分块上传时 ``bool('false')`` 为 True，于是教师正常分享的文件被写成
``scope_level='private'``，学生端资源区看不到。

本脚本只处理「``is_public`` 为真、``is_teacher_resource`` 为假却被标成 private」的行，
按课堂归属回填作用域；教师刻意设为私有资源的行不动。

    python tools/repair_course_file_scopes.py           # 只预览
    python tools/repair_course_file_scopes.py --apply   # 写入
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCOPE_PRIVATE = "private"
SCOPE_CLASSROOM = "classroom"
SCOPE_DEPARTMENT = "department"
SCOPE_SCHOOL = "school"


def _flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}
    return bool(value)


def _row_value(row, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _offering_for(conn, course_id, class_offering_id):
    if class_offering_id not in (None, ""):
        row = conn.execute(
            "SELECT id, class_id FROM class_offerings WHERE id = ? AND course_id = ?",
            (int(class_offering_id), int(course_id)),
        ).fetchone()
        if row:
            return row
    rows = conn.execute(
        "SELECT id, class_id FROM class_offerings WHERE course_id = ? ORDER BY id",
        (int(course_id),),
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


def plan_repairs(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, course_id, file_name, scope_level, is_public, is_teacher_resource,
               class_offering_id, class_id, department
        FROM course_files
        WHERE LOWER(COALESCE(scope_level, '')) = ?
        ORDER BY id
        """,
        (SCOPE_PRIVATE,),
    ).fetchall()

    plans: list[dict] = []
    for row in rows:
        if not _flag(_row_value(row, "is_public")) or _flag(_row_value(row, "is_teacher_resource")):
            continue
        offering = _offering_for(conn, _row_value(row, "course_id"), _row_value(row, "class_offering_id"))
        if offering is not None:
            target = {
                "scope_level": SCOPE_CLASSROOM,
                "class_offering_id": int(_row_value(offering, "id")),
                "class_id": int(_row_value(offering, "class_id")),
            }
        elif str(_row_value(row, "department") or "").strip():
            target = {
                "scope_level": SCOPE_DEPARTMENT,
                "class_offering_id": None,
                "class_id": None,
            }
        else:
            target = {
                "scope_level": SCOPE_SCHOOL,
                "class_offering_id": None,
                "class_id": None,
            }
        if (
            str(_row_value(row, "scope_level") or "") == target["scope_level"]
            and _row_value(row, "class_offering_id") == target["class_offering_id"]
            and _row_value(row, "class_id") == target["class_id"]
        ):
            continue
        plans.append(
            {
                "id": int(_row_value(row, "id")),
                "file_name": str(_row_value(row, "file_name") or ""),
                "course_id": int(_row_value(row, "course_id")),
                "from": {
                    "scope_level": _row_value(row, "scope_level"),
                    "class_offering_id": _row_value(row, "class_offering_id"),
                    "class_id": _row_value(row, "class_id"),
                },
                **target,
            }
        )
    return plans


def apply_repairs(conn, plans: list[dict]) -> int:
    for plan in plans:
        conn.execute(
            "UPDATE course_files SET scope_level = ?, class_offering_id = ?, class_id = ? WHERE id = ?",
            (plan["scope_level"], plan["class_offering_id"], plan["class_id"], plan["id"]),
        )
    conn.commit()
    return len(plans)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="写入修复结果（默认只预览）")
    args = parser.parse_args(argv)

    from classroom_app.database import get_db_connection

    with get_db_connection() as conn:
        plans = plan_repairs(conn)
        if not plans:
            print("没有需要修复的课堂资源。")
            return 0
        for plan in plans:
            print(
                f"#{plan['id']} {plan['file_name']} (course {plan['course_id']}): "
                f"{plan['from']['scope_level']}/offering={plan['from']['class_offering_id']} -> "
                f"{plan['scope_level']}/offering={plan['class_offering_id']}/class={plan['class_id']}"
            )
        if not args.apply:
            print(f"预览 {len(plans)} 项；加 --apply 写入。")
            return 0
        print(f"已修复 {apply_repairs(conn, plans)} 项。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
