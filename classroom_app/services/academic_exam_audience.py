"""Resolve a full course-exam snapshot to administrative classes and possible rooms."""
from __future__ import annotations

import unicodedata
from collections import defaultdict
from typing import Any

from .academic_class_mapping_service import _split_admin_class_names
from .academic_location_service import compose_exam_location


def _class_key(name: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(name or "")).casefold()
    return "".join(normalized.split()).removesuffix("班")


def _offering_classes(conn, offering_ids: list[int]) -> dict[int, dict[int, set[str]]]:
    classes = defaultdict(dict)
    for offset in range(0, len(offering_ids), 250):
        ids = offering_ids[offset:offset + 250]
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""SELECT linked.offering_id,c.id,c.name,c.academic_class_name,c.academic_class_code
                FROM (
                    SELECT o.id AS offering_id,o.class_id FROM class_offerings o WHERE o.id IN ({marks})
                    UNION
                    SELECT l.offering_id,l.class_id FROM class_offering_class_links l WHERE l.offering_id IN ({marks})
                ) linked JOIN classes c ON c.id=linked.class_id""",
            (*ids, *ids),
        ).fetchall()
        for row in rows:
            aliases = {_class_key(row[key]) for key in ("name", "academic_class_name", "academic_class_code")}
            classes[int(row["offering_id"])][int(row["id"])] = aliases - {""}
    return classes


def student_room_group(row: dict) -> tuple[int, str]:
    # One offering identifies the course and teacher. For split rooms the
    # provider can return different paper IDs and finish times for one sitting.
    slot = str(row.get("starts_at") or "")
    if not slot and row.get("exam_date"):
        slot = f"{row['exam_date']}|{row.get('exam_time_text') or ''}"
    return int(row.get("class_offering_id") or 0), slot


def student_allowed(rule: dict, class_id: int) -> bool:
    return "student_class_ids" not in rule or class_id in rule["student_class_ids"]


def room_options_for_student(rule: dict, class_id: int) -> list[str]:
    return (rule.get("student_rooms_by_class") or {}).get(str(class_id), [])


def active_student_exam_class(conn, student_id: int) -> int | None:
    row = conn.execute(
        "SELECT class_id FROM students WHERE id=? AND COALESCE(enrollment_status,'active')='active'",
        (int(student_id),),
    ).fetchone()
    return int(row["class_id"]) if row and row["class_id"] else None


def student_exam_rows(conn, rows, class_id: int | None) -> list[dict]:
    if not class_id:
        return []
    records = [dict(row) for row in rows]
    result = []
    split_groups = defaultdict(list)
    for row, rule in zip(records, course_exam_student_rules(conn, records)):
        if not student_allowed(rule, class_id):
            continue
        options = room_options_for_student(rule, class_id)
        group = student_room_group(row)
        if options:
            split_groups[group].append(row)
            if len(split_groups[group]) > 1:
                continue
        row["student_room_options"] = options
        result.append(row)
    for members in split_groups.values():
        end_times = {str(row.get("ends_at") or "") for row in members}
        if len(end_times) < 2:
            continue
        schedules = []
        for row in members:
            room = compose_exam_location(row.get("campus"), row.get("building"), row.get("location"))
            start = str(row.get("starts_at") or "").replace("T", " ")
            end = str(row.get("ends_at") or "").replace("T", " ")
            time_text = row.get("exam_time_text") or f"{start} 至 {end or '待定'}"
            schedules.append(f"{room}（{time_text}）")
        members[0]["student_room_times"] = "；".join(sorted(set(schedules)))
        members[0]["ends_at"] = max(end_times) if "" not in end_times else ""
    return result


def student_exam_location(row: dict) -> str:
    options = row.get("student_room_options")
    if options:
        return f"候选考场：{'、'.join(options)}（个人考场请核对教务座位名单）"
    return str(row.get("location") or "")


def course_exam_student_rules(conn, items: list) -> list[dict]:
    """Return one rule per row, in input order, for both publishing and display.

    An absent class filter means a whole teaching-class sitting. An explicit
    empty filter means the announced administrative class has no local match.
    """
    rows = [item if isinstance(item, dict) else vars(item) for item in items]
    groups = [student_room_group(row) for row in rows]
    announced_classes = [
        _split_admin_class_names(row.get("class_composition"), row.get("teaching_class_name")) for row in rows
    ]
    locations = [compose_exam_location(row.get("campus"), row.get("building"), row.get("location")) for row in rows]
    group_rooms = defaultdict(set)
    for group, location in zip(groups, locations):
        if group[1] and location:
            group_rooms[group].add(location)
    # An ordinary whole-class exam in one room needs no class roster lookup.
    scoped_ids = {group[0] for group, names in zip(groups, announced_classes)
                  if group[0] and (names or len(group_rooms[group]) > 1)}
    if not scoped_ids:
        return [{} for _ in rows]
    classes = _offering_classes(conn, sorted(scoped_ids))
    rooms = defaultdict(set)
    rules = []
    class_sets = []
    for group, named_classes, room in zip(groups, announced_classes, locations):
        classes_by_id = classes.get(group[0], {})
        rule = {}
        if named_classes:
            names = {_class_key(name) for name in named_classes}
            class_ids = [pk for pk, aliases in classes_by_id.items() if aliases & names]
            rule["student_class_ids"] = sorted(class_ids)
        else:
            class_ids = list(classes_by_id)
        rules.append(rule)
        class_sets.append(class_ids)
        if group[1] and room:
            for class_id in class_ids:
                rooms[group, class_id].add(room)
    for rule, group, class_ids in zip(rules, groups, class_sets):
        split_rooms = {
            str(class_id): sorted(rooms[group, class_id])
            for class_id in class_ids if len(rooms.get((group, class_id), ())) > 1
        }
        if split_rooms:
            rule["student_rooms_by_class"] = split_rooms
    return rules
