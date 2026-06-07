"""Campus-aware exam/invigilation location helpers.

A school often has more than one campus, so a bare room name like ``301`` is
ambiguous on its own. These helpers compose a readable, campus-qualified
location string and can backfill a missing campus/building from the teacher's
already-synced teaching places when the academic feed omits it.

Shared by the invigilation and course-exam sync services and the dashboard /
classroom renderers so the displayed location stays consistent everywhere.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("　", " ")).strip()


def _norm_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").replace("　", "")).strip().lower()


def compose_exam_location(
    campus: Any = "",
    building: Any = "",
    location: Any = "",
    *,
    separator: str = " ",
) -> str:
    """Compose a campus-qualified location string.

    Prepends the campus (and the building, when the room name does not already
    imply it) so that a room reads unambiguously across multiple campuses, e.g.
    ``西校区 实验楼301``. Returns an empty string when every part is blank.
    """
    campus_text = _norm(campus)
    building_text = _norm(building)
    room_text = _norm(location)

    parts: list[str] = []
    if campus_text:
        parts.append(campus_text)
    # Only add the building when the room text does not already contain it
    # (ZF room names like "实验楼301" already embed the building).
    if building_text and building_text not in room_text and room_text not in building_text:
        parts.append(building_text)
    if room_text:
        parts.append(room_text)

    deduped: list[str] = []
    for part in parts:
        if part and part not in deduped:
            deduped.append(part)
    return separator.join(deduped)


def load_teaching_place_location_index(
    conn: sqlite3.Connection,
    teacher_id: int,
) -> dict[str, tuple[str, str]]:
    """Index the teacher's synced teaching places by room name.

    Returns ``{normalized_room_key: (campus_name, building_name)}`` so a sync
    can backfill the campus/building that the exam feed left blank.
    """
    index: dict[str, tuple[str, str]] = {}
    try:
        rows = conn.execute(
            """
            SELECT room_name, room_full_name, campus_name, building_name
            FROM teacher_academic_teaching_places
            WHERE teacher_id = ?
              AND sync_status = 'active'
            """,
            (int(teacher_id),),
        ).fetchall()
    except sqlite3.Error:
        return index

    for row in rows:
        record = dict(row)
        campus = _norm(record.get("campus_name"))
        building = _norm(record.get("building_name"))
        if not campus and not building:
            continue
        for field_name in ("room_name", "room_full_name"):
            key = _norm_key(record.get(field_name))
            if key and key not in index:
                index[key] = (campus, building)
    return index


def enrich_campus_building(
    index: dict[str, tuple[str, str]],
    *,
    campus: Any = "",
    building: Any = "",
    location: Any = "",
    location_short_name: Any = "",
) -> tuple[str, str]:
    """Fill a missing campus/building from the teaching-place index.

    Keeps any value the academic feed already provided; only blank parts are
    backfilled by matching the room name against the synced teaching places.
    """
    campus_text = _norm(campus)
    building_text = _norm(building)
    if campus_text and building_text:
        return campus_text, building_text
    if not index:
        return campus_text, building_text

    for candidate in (location, location_short_name):
        key = _norm_key(candidate)
        if not key or key not in index:
            continue
        idx_campus, idx_building = index[key]
        campus_text = campus_text or idx_campus
        building_text = building_text or idx_building
        if campus_text and building_text:
            break
    return campus_text, building_text
