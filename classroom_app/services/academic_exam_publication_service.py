"""Publish term exam snapshots and their audience's inbox/outbox in one transaction."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter

from ..db.connection import execute_insert_returning_id
from .academic_exam_audience import room_options_for_student, student_room_group
from .academic_service import china_now
from .email_notification_service import queue_notification_email_if_applicable
from .message_center_service import create_academic_exam_notification
from .offering_membership_service import offering_student_where

CALENDAR_LINK = "/dashboard#dashboard-exam-calendar"


def lock_exam_snapshot(conn, teacher_id: int, source_type: str) -> None:
    """Also serialize writes from the pre-existing classroom/settings sync endpoints."""
    if isinstance(conn, sqlite3.Connection):
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))",
                     (f"academic-calendar:{teacher_id}:{source_type}",))


def _metadata(event: dict) -> dict:
    try:
        metadata = json.loads(event.get("metadata_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _recipient_students(conn, metadata: dict, teacher_id: int) -> dict[int, int]:
    offering_id = metadata.get("class_offering_id")
    class_ids = metadata.get("student_class_ids")
    if not offering_id or class_ids == []:
        return {}
    classes_clause = ""
    params = [offering_id, teacher_id]
    if class_ids is not None:
        classes_clause = f" AND s.class_id IN ({','.join('?' for _ in class_ids)})"
        params.extend(class_ids)
    rows = conn.execute(
        f"""SELECT s.id, s.class_id FROM class_offerings o JOIN students s ON {offering_student_where()}
            WHERE o.id = ? AND o.teacher_id = ? AND COALESCE(s.enrollment_status, 'active') = 'active'
            {classes_clause}""", params,
    ).fetchall()
    return {int(row["id"]): int(row["class_id"]) for row in rows}


def _ended(event: dict) -> bool:
    end = str(event.get("ends_at") or event.get("starts_at") or event.get("due_at") or "")
    if not end:
        return False
    now = china_now().replace(tzinfo=None)
    return end < (now.date().isoformat() if len(end) == 10 else now.isoformat(timespec="seconds"))


def _deliver(conn, event, metadata, *, students, teacher_id, change, ref_id, legacy_ref="", allow_new=True, snapshot=""):
    recipients = {("student", uid) for uid in students}
    if teacher_id is not None:
        recipients.add(("teacher", teacher_id))
    refs = (ref_id, legacy_ref) if legacy_ref else (ref_id,)
    existing = conn.execute(
        f"""SELECT n.id, n.recipient_role, n.recipient_user_pk, n.email_status, n.ref_id, n.metadata_json
            FROM message_center_notifications n
            JOIN (SELECT MAX(id) AS id FROM message_center_notifications WHERE ref_type = 'academic_exam'
                  AND ref_id IN ({','.join('?' for _ in refs)})
                  GROUP BY recipient_role, recipient_user_pk) latest ON n.id=latest.id""", refs,
    ).fetchall()
    known = {(row["recipient_role"], int(row["recipient_user_pk"])): row for row in existing}
    kind = "监考" if event["source_type"] == "academic_invigilation" else "考试"
    subject = metadata.get("course_name") or str(event["title"]).split("：", 1)[-1]
    details = [event["notes"] or metadata.get("exam_time_text") or "时间和地点待教务系统公布。"]
    if kind == "监考" and metadata.get("invigilators"):
        details.append(metadata["invigilators"])
    if metadata.get("note"):
        details.append(metadata["note"])
    summary = " | ".join(details)
    if change == "撤回":
        summary = f"原安排已从教务系统同步清单中移除。原安排：{summary}"
    counts = Counter()
    for role, user_id in sorted(recipients):
        row = known.get((role, user_id))
        label = change
        previous = _metadata(dict(row)) if row and snapshot else {}
        if row and snapshot and row["ref_id"] == legacy_ref:
            # Keep the original card and its read/email state when the old student
            # notification already describes this same single-room exam.
            previous.update(notice_snapshot="legacy" if change == "撤回" else snapshot, change="新增")
            conn.execute("UPDATE message_center_notifications SET ref_id=?, link_url=?, metadata_json=? WHERE id=?",
                         (ref_id, CALENDAR_LINK, json.dumps(previous, ensure_ascii=False), row["id"]))
        if row and (not snapshot or previous.get("notice_snapshot") == snapshot):
            # No inbox duplicate when a missing address or SMTP config is supplied later.
            if row["email_status"] in ("skipped", "not_configured", "not_required"):
                payload = conn.execute("SELECT * FROM message_center_notifications WHERE id = ?",
                                       (row["id"],)).fetchone()
                queue_notification_email_if_applicable(conn, notification_id=int(row["id"]), payload=dict(payload))
            continue
        if row and change != "撤回":
            label = "恢复" if previous.get("change") == "撤回" else "更新"
        if allow_new or (snapshot and row):
            rooms = room_options_for_student(metadata, students[user_id]) if role == "student" else []
            notice_kind = f"{kind}候选考场" if rooms else kind
            preview = (f"本场为候选考场，请以个人座位名单为准。教学班考场：{'、'.join(rooms)}。{summary}"
                       if rooms else summary)
            counts[role] += create_academic_exam_notification(
                conn, recipient_role=role, recipient_user_pk=user_id,
                title=f"教务系统{label}{notice_kind}：{subject}", body_preview=preview,
                link_url=CALENDAR_LINK, class_offering_id=metadata.get("class_offering_id"),
                ref_id=ref_id, actor_display_name="教务系统", allow_duplicates=True,
                metadata={"source": "gxufl_jwxt", "source_key": event["source_key"],
                          "calendar_event_id": event["id"], "change": label, "notice_snapshot": snapshot,
                          "course_name": subject, "starts_at": event["starts_at"],
                          "ends_at": event["ends_at"], "campus": metadata.get("campus", ""),
                          "building": metadata.get("building", ""), "location": event["location"],
                          "room_schedules": metadata.get("room_schedules", [])},
            )
    return counts


def save_exam_event(conn, *, teacher_id, semester_id, source_type, source_id, source_key, title,
                    subtitle, notes, starts_at, ends_at, due_at, location, tone, link_url,
                    metadata, synced_at) -> tuple[bool, bool, Counter]:
    row = conn.execute(
        """SELECT * FROM teacher_calendar_events WHERE teacher_id = ? AND source_type = ? AND source_key = ?""",
        (teacher_id, source_type, source_key),
    ).fetchone()
    previous = dict(row) if row else {}
    previous_metadata = _metadata(previous)
    event = dict(teacher_id=teacher_id, semester_id=semester_id, source_type=source_type,
                 source_id=source_id, source_key=source_key, title=title, subtitle=subtitle,
                 notes=notes, starts_at=starts_at, ends_at=ends_at, due_at=due_at, location=location,
                 tone=tone, status="active", link_url=link_url)
    changed = bool(previous) and (
        any((previous.get(k) or "") != (event[k] or "") for k in
            ("title", "subtitle", "notes", "starts_at", "ends_at", "due_at", "location", "status"))
        or any((previous_metadata.get(k) or "") != (value or "") for k, value in metadata.items()
               if k not in ("academic_source", "course_exam_item_id", "invigilation_item_id", "signature"))
    )
    revision = int(previous_metadata.get("notice_revision") or 1) + int(changed)
    metadata["notice_revision"] = revision
    event["metadata_json"] = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    event["synced_at"] = event["updated_at"] = synced_at
    if row:
        event_id = int(row["id"])
        columns = [key for key in event if key not in ("teacher_id", "source_type", "source_key")]
        conn.execute(
            f"UPDATE teacher_calendar_events SET {', '.join(key + ' = ?' for key in columns)}, "
            "deleted_at = NULL WHERE id = ?", (*[event[key] for key in columns], event_id),
        )
    else:
        event["created_at"] = synced_at
        event_id = execute_insert_returning_id(
            conn, f"INSERT INTO teacher_calendar_events ({', '.join(event)}) VALUES "
                  f"({', '.join('?' for _ in event)})", tuple(event.values()),
        )
    event["id"] = event_id
    counts = Counter()
    if _ended(event) and (not previous or _ended(previous)):
        return not row, changed, counts
    ref = f"academic-calendar:{event_id}:{revision}"
    if source_type == "academic_invigilation" and revision == 1 and not previous_metadata.get("notice_revision"):
        # Original invigilation notices were inbox-only todos. Upgrade the same card
        # so its read state, id, and email worker's re-check remain coherent.
        conn.execute(
            """UPDATE message_center_notifications SET category = 'academic_exam', severity = 'important',
               ref_type = 'academic_exam', ref_id = ?, link_url = ?
               WHERE recipient_role = 'teacher' AND recipient_user_pk = ? AND category = 'todo'
                 AND ref_type = 'todo' AND ref_id = ?""",
            (ref, CALENDAR_LINK, teacher_id, f"academic-invigilation:{source_key}:{starts_at}"),
        )
    change = "恢复" if previous.get("status") == "stale" else ("更新" if revision > 1 else "新增")
    counts.update(_deliver(conn, event, metadata, students={}, teacher_id=teacher_id, change=change,
                           ref_id=ref))
    return not row, changed, counts


def withdraw_exam_events(conn, *, teacher_id: int, semester_id: int, source_type: str,
                         source_keys: list[str], synced_at: str) -> Counter:
    counts = Counter()
    for offset in range(0, len(source_keys), 250):
        keys = source_keys[offset:offset + 250]
        rows = conn.execute(
            f"""SELECT * FROM teacher_calendar_events WHERE teacher_id = ? AND semester_id = ?
                AND source_type = ? AND source_key IN ({','.join('?' for _ in keys)})""",
            (teacher_id, semester_id, source_type, *keys),
        ).fetchall()
        for row in rows:
            event = dict(row)
            metadata = _metadata(event)
            newly_withdrawn = event["status"] == "active"
            if newly_withdrawn:
                metadata["notice_revision"] = int(metadata.get("notice_revision") or 1) + 1
                conn.execute(
                    """UPDATE teacher_calendar_events SET status = 'stale', metadata_json = ?,
                       synced_at = ?, updated_at = ? WHERE id = ?""",
                    (json.dumps(metadata, ensure_ascii=False), synced_at, synced_at, event["id"]),
                )
            if _ended(event) or not metadata.get("notice_revision"):
                continue
            counts.update(_deliver(conn, event, metadata, students={}, teacher_id=teacher_id, change="撤回",
                                   ref_id=f"academic-calendar:{event['id']}:{metadata['notice_revision']}",
                                   allow_new=newly_withdrawn))
    return counts


def student_exam_groups(conn, teacher_id, semester_id):
    """Build one notice per sitting and administrative class from the complete local snapshot."""
    rows = conn.execute(
        """SELECT * FROM teacher_calendar_events WHERE teacher_id=? AND semester_id=?
           AND source_type='academic_course_exam' AND deleted_at IS NULL ORDER BY source_key,id""",
        (teacher_id, semester_id),
    ).fetchall()
    groups = {}
    roster_cache = {}
    today = china_now().date().isoformat()
    for row in rows:
        event = dict(row)
        last_day = str(event.get("ends_at") or event.get("starts_at") or event.get("due_at") or "")[:10]
        if last_day and last_day < today:
            continue
        metadata = _metadata(event)
        offering = metadata.get("class_offering_id")
        if not offering:
            continue
        if offering not in roster_cache:
            roster_cache[offering] = _recipient_students(conn, {"class_offering_id": offering}, teacher_id)
        scope = metadata.get("student_class_ids")
        by_class = {}
        for uid, cid in roster_cache[offering].items():
            if scope is None or cid in scope:
                by_class.setdefault(cid, {})[uid] = cid
        _, slot = student_room_group({**metadata, "starts_at": event["starts_at"], "exam_date": event["due_at"]})
        sitting = slot or event["source_key"]
        for cid, students in by_class.items():
            group = groups.setdefault((offering, sitting, cid),
                                      {"active": [], "stale": [], "students": students})
            group["active" if event["status"] == "active" else "stale"].append((event, metadata))
    return groups


def publish_student_exam_groups(conn, teacher_id, semester_id, previous):
    current = student_exam_groups(conn, teacher_id, semester_id)
    counts = Counter()
    # A rescheduled source row announces its new time. An additional withdrawal
    # for the superseded time would falsely imply the whole exam was cancelled.
    active_ids = {(key[2], event["id"]) for key, group in current.items()
                  for event, _ in group["active"]}
    previous_active_ids = {(key[2], event["id"]) for key, group in previous.items()
                           for event, _ in group["active"]}
    for key in sorted(current.keys() | previous.keys()):
        group = current.get(key) or {}
        active = group.get("active", [])
        old = previous.get(key) or {}
        old_active = old.get("active", [])
        records = active or old_active or group.get("stale", [])
        if not records or all(_ended(e) for e, _ in records):
            continue
        event, metadata = records[0]
        offering, slot, class_id = key
        ref = f"academic-sitting:{teacher_id}:{semester_id}:{offering}:{class_id}:{_token(slot)}"
        students = group.get("students") or old["students"]
        rooms = room_options_for_student(metadata, class_id)
        if rooms:
            # A representative calendar row must not imply a personal room or
            # end time. Different exams in this sitting may end at different times.
            schedules = sorted({f"{e['location']}（{m.get('exam_time_text') or e['starts_at'] or '时间待定'}）"
                                for e, m in records})
            end_times = [e["ends_at"] or "" for e, _ in records]
            event = {**event, "location": "、".join(rooms),
                     "ends_at": max(end_times) if all(end_times) else "",
                     "notes": "；".join(schedules)}
            metadata = {**metadata, "room_schedules": schedules}
        if not active and any((class_id, entry["id"]) in active_ids for entry, _ in records):
            continue
        legacy_ref = ""
        if len(records) == 1 and metadata.get("signature") and (not active or metadata.get("notice_revision", 1) == 1):
            legacy_ref = f"academic-course-exam:{event['source_key']}:{metadata['signature']}"
        if active:
            # A representative row can change independently of the other rooms.
            # Compare what this class sees, including every possible room.
            fingerprint = [_student_visible_fields(e, m, class_id) for e, m in active]
            snapshot = _token(json.dumps(sorted(fingerprint), ensure_ascii=False, separators=(",", ":")))
        else:
            snapshot = "withdrawn"
        # A withdrawal only reaches students who have already received that sitting.
        change = "新增" if active else "撤回"
        if active and not old_active and any((class_id, e["id"]) in previous_active_ids for e, _ in active):
            change = "更新"
        counts.update(_deliver(conn, event, metadata, students=students, teacher_id=None,
                               change=change, ref_id=ref,
                               legacy_ref=legacy_ref, snapshot=snapshot, allow_new=bool(active)))
    return counts


def _student_visible_fields(event, metadata, class_id):
    rooms = room_options_for_student(metadata, class_id)
    return (event["title"], event["subtitle"], event["starts_at"] or "", event["ends_at"] or "",
            event["location"], event["notes"], metadata.get("note") or "", tuple(rooms),
            int(metadata.get("notice_revision") or 1))


def _token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
