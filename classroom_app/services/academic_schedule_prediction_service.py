"""Complete, scoped JWXT snapshots with stable classroom-session identities.

No network, template generation, schema writes, or commits happen here. Callers
commit lease claims before fetching remotely, then commit publication (or roll
back on error). Approved changes update schedule fields only; a date sort never
chooses a lesson or overwrites teaching material.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .semester_identity_service import parse_semester_identity


class ScheduleSnapshotError(ValueError):
    """The supplied snapshot cannot be published safely."""


class ScheduleSyncLeaseError(ScheduleSnapshotError):
    """Another worker owns the lease, or this worker's lease expired."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clock(now=None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _stamp(now=None) -> str:
    return _clock(now).isoformat(timespec="microseconds")


def _sqlite(conn) -> bool:
    return isinstance(conn, sqlite3.Connection)


def _table_exists(conn, name: str) -> bool:
    if _sqlite(conn):
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    return bool(conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=?", (name,),
    ).fetchone())


def claim_schedule_sync(conn, teacher_id: int, *, lease_seconds: int = 600, now=None) -> dict:
    """Acquire a teacher-wide CAS lease. Caller MUST commit before remote I/O."""
    teacher_id = int(teacher_id)
    if not conn.execute("SELECT id FROM teachers WHERE id=?", (teacher_id,)).fetchone():
        raise ScheduleSnapshotError("教师不存在")
    duration = max(30, min(1800, int(lease_seconds)))
    clock = _clock(now)
    stamp, expires = _stamp(clock), _stamp(clock + timedelta(seconds=duration))
    token = uuid.uuid4().hex
    conn.execute("INSERT INTO teacher_academic_schedule_sync_state(teacher_id) VALUES(?) ON CONFLICT(teacher_id) DO NOTHING", (teacher_id,))
    cursor = conn.execute(
        """UPDATE teacher_academic_schedule_sync_state
           SET lease_token=?,lease_expires_at=?,status='syncing',last_attempt_at=?,error=''
           WHERE teacher_id=? AND (lease_token='' OR lease_expires_at<=?)""",
        (token, expires, stamp, teacher_id, stamp),
    )
    if cursor.rowcount != 1:
        row = conn.execute("SELECT lease_expires_at FROM teacher_academic_schedule_sync_state WHERE teacher_id=?", (teacher_id,)).fetchone()
        return {"status": "busy", "token": None, "expires_at": row["lease_expires_at"]}
    return {"status": "claimed", "token": token, "expires_at": expires}


def release_schedule_sync(conn, teacher_id: int, lease_token: str, *, error: str = "", now=None) -> bool:
    """Release only the caller's token, preserving the last successful snapshot."""
    cursor = conn.execute(
        """UPDATE teacher_academic_schedule_sync_state SET lease_token='',lease_expires_at='',
           status=CASE WHEN ?<>'' THEN 'failed' WHEN last_success_at<>'' THEN 'ready' ELSE 'idle' END,error=?
           WHERE teacher_id=? AND lease_token=?""",
        (str(error)[:500], str(error)[:500], int(teacher_id), str(lease_token)),
    )
    return cursor.rowcount == 1


def fail_schedule_sync(conn, teacher_id: int, lease_token: str, error: str = "同步未完成，请重试", *, now=None) -> bool:
    return release_schedule_sync(conn, teacher_id, lease_token, error=error, now=now)


def _identity(row: dict) -> dict:
    return {key: str(row.get(key) or "").strip() for key in (
        "teaching_class_id", "course_code", "course_name", "teaching_class_name", "class_label",
    )}


def _identity_key(row: dict) -> str:
    value = _identity(row)
    return _json([value["teaching_class_id"] or value["teaching_class_name"], value["course_code"] or value["course_name"]])


def _same_identity(left: dict, right: dict) -> bool:
    a, b = _identity(left), _identity(right)
    if a["course_code"] and b["course_code"] and a["course_code"] != b["course_code"]:
        return False
    if a["teaching_class_id"] and b["teaching_class_id"]:
        return a["teaching_class_id"] == b["teaching_class_id"]
    return bool(a["teaching_class_name"] and a["teaching_class_name"] == b["teaching_class_name"]
                and (a["course_code"] == b["course_code"] if a["course_code"] and b["course_code"] else a["course_name"] == b["course_name"]))


def _sections(text: Any) -> list[int]:
    if isinstance(text, list):
        return sorted(set(int(value) for value in text))
    text = str(text or "").strip()
    match = re.fullmatch(r"(?:第)?(\d+)\s*[-—－~～]\s*(\d+)(?:节)?", text)
    if match:
        return list(range(int(match[1]), int(match[2]) + 1))
    if re.fullmatch(r"\d+", text):
        return [int(text)]
    return []


def _room(value: Any) -> str:
    return re.sub(r"[\s()（）]", "", str(value or ""))


def _slot_key(slot: dict) -> tuple:
    return str(slot.get("date") or ""), tuple(slot.get("sections") or [])


def _slot(raw: dict, semester: dict) -> dict:
    if not isinstance(raw, dict):
        raise ScheduleSnapshotError("课次日期信息不完整")
    try:
        day = date.fromisoformat(str(raw.get("date") or raw.get("actual_date") or ""))
        start, end = date.fromisoformat(semester["start_date"]), date.fromisoformat(semester["end_date"])
        monday = start - timedelta(days=start.weekday())
        week = (day - monday).days // 7 + 1
        sections = _sections(raw.get("sections"))
        if not start <= day <= end or not sections or sections != list(range(min(sections), max(sections) + 1)) or min(sections) < 1 or max(sections) > 30:
            raise ValueError()
        if raw.get("weekday") is not None and int(raw["weekday"]) != day.isoweekday():
            raise ValueError()
        if raw.get("week") is not None and int(raw["week"]) != week:
            raise ValueError()
    except (ValueError, TypeError, KeyError) as exc:
        raise ScheduleSnapshotError("课次日期、周次、星期或节次与所选学期不一致") from exc
    return {"date": day.isoformat(), "week": week, "weekday": day.isoweekday(), "sections": sections,
            **{key: str(raw.get(key) or "").strip() for key in ("room", "room_id", "teacher_code")}}


def _load_semester(conn, teacher_id: int, semester: dict | int) -> dict:
    sid = int(semester["id"] if isinstance(semester, dict) else semester)
    row = conn.execute("SELECT * FROM academic_semesters WHERE id=?", (sid,)).fetchone()
    teacher = conn.execute("SELECT * FROM teachers WHERE id=?", (int(teacher_id),)).fetchone()
    if not row or not teacher:
        raise ScheduleSnapshotError("教师或所选学期不存在")
    value = dict(row)
    if int(value["teacher_id"]) != int(teacher_id):
        # Semesters are shared within the institution, as in the existing sync.
        from .academic_course_sync_service import _load_semester_by_id
        if not _load_semester_by_id(conn, int(teacher_id), sid):
            raise ScheduleSnapshotError("无权同步该学期")
    if isinstance(semester, dict) and any(semester.get(key) and str(semester[key]) != str(value.get(key)) for key in ("start_date", "end_date", "name")):
        raise ScheduleSnapshotError("学期已发生变化，请重新选择学期")
    return value


def _normalize_snapshot(snapshot: dict, teacher_id: int, semester: dict) -> dict:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("official"), list) or not isinstance(snapshot.get("requests"), list):
        raise ScheduleSnapshotError("必须提供完整的正式课表与申请列表")
    if snapshot.get("complete") is False:
        raise ScheduleSnapshotError("不完整快照不能替换已有课表")
    for key, expected in (("teacher_id", teacher_id), ("semester_id", semester["id"])):
        if snapshot.get(key) is not None and int(snapshot[key]) != int(expected):
            raise ScheduleSnapshotError("快照教师或学期不一致")
    official, requests, seen = [], [], set()
    for raw in snapshot["official"]:
        item = {**_identity(raw), **_slot(raw, semester)}
        if not (item["teaching_class_id"] or item["teaching_class_name"]):
            raise ScheduleSnapshotError("正式课表缺少教学班身份")
        key = (_identity_key(item), _slot_key(item), _room(item["room"]))
        if key not in seen:
            official.append(item)
            seen.add(key)
    seen_requests = {}
    for raw in snapshot["requests"]:
        request = {**_identity(raw), **{key: str(raw.get(key) or "").strip() for key in (
            "request_id", "serial", "status", "raw_status", "kind", "reason", "applied_at",
        )}, "details": []}
        if not request["request_id"] or not isinstance(raw.get("details"), list) or not raw["details"]:
            raise ScheduleSnapshotError("申请标识或申请详情缺失")
        detail_ids = set()
        for detail in raw["details"]:
            detail_id = str(detail.get("detail_id") or "").strip()
            if not detail_id or detail_id in detail_ids:
                raise ScheduleSnapshotError("申请详情标识缺失或重复")
            detail_ids.add(detail_id)
            request["details"].append({"detail_id": detail_id, "original": _slot(detail.get("original"), semester),
                                       "proposed": _slot(detail["proposed"], semester) if detail.get("proposed") else None})
        if request["request_id"] in seen_requests:
            if request != seen_requests[request["request_id"]]:
                raise ScheduleSnapshotError("同一申请存在不一致详情")
            continue
        seen_requests[request["request_id"]] = request
        requests.append(request)
    return {"official": official, "requests": requests, "source_summary": copy.deepcopy(snapshot.get("source_summary") or []),
            "teacher_id": teacher_id, "semester_id": int(semester["id"])}


def _load_scope(conn, teacher_id: int, semester_id: int) -> tuple[list[dict], list[dict]]:
    offerings = [dict(row) for row in conn.execute(
        """SELECT o.id,o.teacher_id,o.semester_id,o.course_id,o.academic_teaching_class_id AS teaching_class_id,
           o.academic_teaching_class_name AS teaching_class_name,c.academic_course_code AS course_code,c.name AS course_name
           FROM class_offerings o JOIN courses c ON c.id=o.course_id WHERE o.teacher_id=? AND o.semester_id=? ORDER BY o.id""",
        (teacher_id, semester_id),
    ).fetchall()]
    if not offerings:
        return offerings, []
    ids = [row["id"] for row in offerings]
    # Same lock order as manual plan edits: course -> offering -> session.
    if not _sqlite(conn):
        for table, keys in (("courses", sorted({row["course_id"] for row in offerings})), ("class_offerings", ids)):
            marks = ",".join("?" for _ in keys)
            conn.execute(f"SELECT id FROM {table} WHERE id IN ({marks}) ORDER BY id FOR UPDATE", tuple(keys)).fetchall()
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM class_offering_sessions WHERE class_offering_id IN ({marks}) ORDER BY id" + ("" if _sqlite(conn) else " FOR UPDATE"), tuple(ids),
    ).fetchall()
    sessions = [dict(row) for row in rows]
    for row in sessions:
        try:
            metadata = json.loads(row.get("schedule_metadata_json") or "{}")
        except (ValueError, TypeError):
            metadata = {}
        metadata = metadata if isinstance(metadata, dict) else {}
        row["slot"] = {"date": row["session_date"], "sections": _sections(row.get("academic_section_text") or metadata.get("section_text")), "room": row.get("academic_location") or ""}
    return offerings, sessions


def _offering_for(identity: dict, offerings: list[dict]) -> dict | None:
    candidates = [row for row in offerings if _same_identity(identity, row)]
    return candidates[0] if len(candidates) == 1 else None


def _covers(official: list[dict], identity: dict, slot: dict, *, room: bool = False) -> bool:
    covered = set()
    for item in official:
        if _same_identity(identity, item) and item["date"] == slot["date"] and (not room or _room(item["room"]) == _room(slot["room"])):
            covered.update(item["sections"])
    return set(slot["sections"]).issubset(covered)


def _bind(conn, teacher_id, semester_id, session: dict, identity: dict, slot: dict, stamp: str, evidence: str) -> str:
    event_key = f"academic:{teacher_id}:{semester_id}:session:{session['id']}"
    current = {**slot, "schedule_status": session.get("schedule_status") or "scheduled"}
    conn.execute(
        """INSERT INTO academic_schedule_session_bindings
           (teacher_id,semester_id,session_id,class_offering_id,event_key,identity_json,original_json,current_json,evidence,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(teacher_id,semester_id,session_id) DO UPDATE SET
           current_json=excluded.current_json,identity_json=excluded.identity_json,evidence=excluded.evidence,updated_at=excluded.updated_at""",
        (teacher_id, semester_id, session["id"], session["class_offering_id"], event_key, _json(_identity(identity)), _json(session["slot"]), _json(current), evidence, stamp),
    )
    return event_key


def _update_session(conn, session: dict, slot: dict, stamp: str, *, cancelled: bool = False) -> None:
    # Do not touch course_lesson_id, order_index, title, content, material, or references.
    sections = slot["sections"]
    period = str(sections[0]) if len(sections) == 1 else f"{sections[0]}-{sections[-1]}"
    conn.execute(
        """UPDATE class_offering_sessions SET session_date=?,weekday=?,week_index=?,academic_section_text=?,
           academic_location=?,slot_section_count=?,schedule_status=?,updated_at=? WHERE id=? AND class_offering_id=?""",
        (slot["date"], int(slot["weekday"]) - 1, slot["week"], period, slot["room"], len(sections),
         "cancelled" if cancelled else "scheduled", stamp, session["id"], session["class_offering_id"]),
    )
    session["slot"] = copy.deepcopy(slot)
    session["schedule_status"] = "cancelled" if cancelled else "scheduled"


def _resequence_after_updates(conn, teacher_id: int, semester_id: int, sessions: list[dict], touched: set, stamp: str) -> list[dict]:
    """Re-order the remaining lessons of every offering whose session dates just changed."""
    from .offering_session_resequence_service import resequence_offerings_after_publish

    offering_ids = {int(row["class_offering_id"]) for row in sessions if int(row["id"]) in touched}
    if not offering_ids:
        return []
    semester = conn.execute("SELECT start_date FROM academic_semesters WHERE id=?", (int(semester_id),)).fetchone()
    start = date.fromisoformat(str(semester["start_date"])) if semester and semester["start_date"] else None
    monday = start - timedelta(days=start.weekday()) if start else None
    today = _clock(stamp).date()
    return resequence_offerings_after_publish(conn, teacher_id, semester_id, sessions, offering_ids,
                                              week1_monday=monday, today=today, stamp=stamp)


def _split_official(official: list[dict], requests: list[dict], offerings: list[dict], sessions: list[dict]) -> list[dict]:
    """Split merged remote coverage only when known session/request boundaries prove it."""
    result = []
    for item in official:
        boundaries = []
        offering = _offering_for(item, offerings)
        if offering:
            boundaries.extend(row["slot"] for row in sessions if row["class_offering_id"] == offering["id"])
        for request in requests:
            if _same_identity(item, request):
                for detail in request["details"]:
                    boundaries.extend(slot for slot in (detail["original"], detail["proposed"]) if slot)
        pieces = {tuple(slot["sections"]) for slot in boundaries if slot["date"] == item["date"] and slot["sections"]
                  and set(slot["sections"]).issubset(item["sections"])}
        # A merged session must not veto a finer, fully evidenced partition.
        minimal = sorted(piece for piece in pieces if not any(set(other) < set(piece) for other in pieces))
        flattened = [number for piece in minimal for number in piece]
        if minimal and sorted(flattened) == item["sections"] and len(flattened) == len(set(flattened)):
            result.extend({**item, "sections": list(piece)} for piece in minimal)
        else:
            result.append(item)
    return result


def _warning(warnings: list, code: str, *, request_id: str = "", message: str = "") -> None:
    item = {"code": code, "request_id": request_id, "message": message}
    if item not in warnings:
        warnings.append(item)


def _lesson(item: dict, teacher_id: int, semester_id: int, offering: dict | None, session: dict | None, session_total: int = 0) -> dict:
    suffix = hashlib.sha256(_json([_identity(item), _slot_key(item), item["room"]]).encode()).hexdigest()[:20]
    sid = int(session["id"]) if session else None
    oid = int(offering["id"]) if offering else None
    return {"event_key": f"academic:{teacher_id}:{semester_id}:session:{sid}" if sid else f"academic:{teacher_id}:{semester_id}:official:{suffix}",
            "session_id": sid, "class_offering_id": oid,
            "session_no": int(session["order_index"]) if session else None,
            "session_total": session_total if session else None,
            "binding_status": "bound" if sid else "unresolved",
            "classroom_url": f"/classroom/{oid}?session_id={sid}" if sid and oid else "",
            "counts_towards_total": True, "actual_date": item["date"], "weekday": item["weekday"],
            "sections": item["sections"], "week_index": item["week"], **_identity(item), "classroom": item["room"]}


def _public_slot(slot: dict | None) -> dict | None:
    return {key: slot[key] for key in ("date", "sections", "room")} if slot else None


def reconcile_and_publish_snapshot(conn, teacher_id: int, semester: dict | int, snapshot: dict, lease_token: str, *, now=None) -> dict:
    """Publish atomically without committing. Failed validation leaves old data intact."""
    teacher_id, stamp = int(teacher_id), _stamp(now)
    semester = _load_semester(conn, teacher_id, semester)
    semester_id = int(semester["id"])
    snapshot = _normalize_snapshot(snapshot, teacher_id, semester)
    if _sqlite(conn) and not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute("SAVEPOINT academic_schedule_publication")
    try:
        lease = conn.execute(
            "SELECT * FROM teacher_academic_schedule_sync_state WHERE teacher_id=?" + ("" if _sqlite(conn) else " FOR UPDATE"), (teacher_id,),
        ).fetchone()
        if not lease or lease["lease_token"] != lease_token or not lease_token or lease["lease_expires_at"] <= stamp or lease["status"] != "syncing":
            raise ScheduleSyncLeaseError("同步锁已过期或已由其他任务接管，请重新同步")
        summary = _publish(conn, teacher_id, semester_id, snapshot, lease_token, stamp)
        # Check wall time again at publication: a very slow reconciliation cannot publish an expired lease.
        final_stamp = _stamp(now)
        updated = conn.execute(
            """UPDATE teacher_academic_schedule_sync_state SET status='ready',last_success_at=?,error=''
               WHERE teacher_id=? AND lease_token=? AND lease_expires_at>? AND status='syncing'""",
            (final_stamp, teacher_id, lease_token, final_stamp),
        )
        if updated.rowcount != 1:
            raise ScheduleSyncLeaseError("同步锁已过期，结果未发布")
        conn.execute("RELEASE SAVEPOINT academic_schedule_publication")
        return summary
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT academic_schedule_publication")
        conn.execute("RELEASE SAVEPOINT academic_schedule_publication")
        raise


def _publish(conn, teacher_id: int, semester_id: int, snapshot: dict, token: str, stamp: str) -> dict:
    old = conn.execute("SELECT * FROM teacher_academic_schedule_snapshots WHERE teacher_id=? AND semester_id=?", (teacher_id, semester_id)).fetchone()
    revision = int(old["revision"]) + 1 if old else 1
    offerings, sessions = _load_scope(conn, teacher_id, semester_id)
    session_by_id = {row["id"]: row for row in sessions}
    bindings = [dict(row) for row in conn.execute("SELECT * FROM academic_schedule_session_bindings WHERE teacher_id=? AND semester_id=?", (teacher_id, semester_id)).fetchall()]
    bindings_by_session = {row["session_id"]: row for row in bindings}
    local_conflicts = set()
    for session_id, binding in bindings_by_session.items():
        session = session_by_id.get(session_id)
        if not session:
            continue
        expected, actual = json.loads(binding["current_json"]), session["slot"]
        if (_slot_key(expected) != _slot_key(actual) or _room(expected.get("room")) != _room(actual.get("room"))
                or expected.get("schedule_status", session.get("schedule_status")) != session.get("schedule_status")):
            local_conflicts.add(session_id)
    links = {(row["request_id"], row["detail_id"]): dict(row) for row in conn.execute("SELECT * FROM academic_schedule_change_session_links WHERE teacher_id=? AND semester_id=?", (teacher_id, semester_id)).fetchall()}
    warnings, resolved, request_session_ids = [], {}, {}
    official, requests = snapshot["official"], snapshot["requests"]
    changed_sessions, cancelled_sessions = set(), set()
    approved_updates = {}
    if local_conflicts:
        _warning(warnings, "local_schedule_conflict", message="课堂日程在上次同步后已被修改，保留人工安排及原核对基线，等待核对。")
    # Resolve identities before applying any date change. Never index by sorted date/order.
    for request in requests:
        if request["status"] not in {"draft", "pending", "approved", "returned", "rejected"}:
            _warning(warnings, "unknown_request_status", request_id=request["request_id"], message="申请状态暂无法识别，保留正式课表且不生成该申请的预测，请核对教务状态。")
        offering = _offering_for(request, offerings)
        for detail in request["details"]:
            key = (request["request_id"], detail["detail_id"])
            session = None
            if offering:
                linked = links.get(key)
                if linked and linked["class_offering_id"] == offering["id"]:
                    session = session_by_id.get(linked["session_id"])
                    if session and session["class_offering_id"] != offering["id"]:
                        session = None
                if not session:
                    candidates = {row["id"] for row in sessions if row["class_offering_id"] == offering["id"] and _slot_key(row["slot"]) == _slot_key(detail["original"])}
                    for binding in bindings:
                        if binding["class_offering_id"] == offering["id"] and _same_identity(json.loads(binding["identity_json"]), request):
                            if any(_slot_key(json.loads(binding[field])) == _slot_key(detail["original"]) for field in ("original_json", "current_json")):
                                candidates.add(binding["session_id"])
                    if len(candidates) == 1:
                        session = session_by_id.get(next(iter(candidates)))
                    elif not candidates and request["status"] == "approved" and detail["proposed"]:
                        # First snapshot may already reflect an approved adjustment.
                        candidates = [row for row in sessions if row["class_offering_id"] == offering["id"] and _slot_key(row["slot"]) == _slot_key(detail["proposed"])]
                        if len(candidates) == 1 and not _covers(official, request, detail["original"]):
                            session = candidates[0]
            if session:
                request_session_ids[key] = session["id"]
                if session["id"] in local_conflicts:
                    _warning(warnings, "local_schedule_conflict", request_id=request["request_id"], message="课堂日程在上次同步后已被修改，保留人工安排，等待核对。")
                    session = None
            if session:
                resolved[key] = session
                _bind(conn, teacher_id, semester_id, session, request, session["slot"], stamp, "request_original_exact")
                conn.execute(
                    """INSERT INTO academic_schedule_change_session_links(teacher_id,semester_id,request_id,detail_id,class_offering_id,session_id,status,updated_at)
                       VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(teacher_id,semester_id,request_id,detail_id,class_offering_id)
                       DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at""",
                    (teacher_id, semester_id, *key, session["class_offering_id"], session["id"], request["status"], stamp),
                )
            elif request["status"] in ("pending", "approved"):
                _warning(warnings, "unresolved_session", request_id=request["request_id"], message="申请课次尚未精确关联课堂；不会按日期重新编号。")
            if request["status"] != "approved":
                continue
            original, proposed = detail["original"], detail["proposed"]
            room_only = proposed and _slot_key(original) == _slot_key(proposed)
            effective = (request["kind"] == "cancel" and not _covers(official, request, original)) or (
                request["kind"] == "move" and proposed and _covers(official, request, proposed, room=bool(proposed["room"]))
                and (room_only or not _covers(official, request, original)))
            if effective and session:
                target = original if request["kind"] == "cancel" else proposed
                approved_updates.setdefault(session["id"], []).append((request, target, session))
            elif not effective:
                _warning(warnings, "approved_not_reflected", request_id=request["request_id"], message="申请已通过，正式课表尚未完整体现；当前仅显示正式课表。")
    # Resolve all approved claims before writing any session. List order is never
    # an approval priority; contradictory effective targets preserve local state.
    for session_id, updates in approved_updates.items():
        targets = {(_slot_key(target), _room(target["room"]), request["kind"] == "cancel") for request, target, _ in updates}
        if len(targets) != 1:
            for request, _, _ in updates:
                _warning(warnings, "approved_conflict", request_id=request["request_id"], message="同一课堂课次存在互相冲突的已通过申请，保留课堂日程并等待核对。")
            continue
        request, target, session = updates[0]
        _update_session(conn, session, target, stamp, cancelled=request["kind"] == "cancel")
        _bind(conn, teacher_id, semester_id, session, request, target, stamp, "approved_official_confirmed")
        (cancelled_sessions if request["kind"] == "cancel" else changed_sessions).add(session_id)
    # 课程一旦调整（审批通过并已体现在正式课表），剩余课次按新日期重排：已发生课次不变，
    # 材料跟随课次序号自动重绑。重排失败只降级为警告，绝不让发布失败。
    resequenced = []
    if changed_sessions or cancelled_sessions:
        try:
            resequenced = _resequence_after_updates(conn, teacher_id, semester_id, sessions, changed_sessions | cancelled_sessions, stamp)
        except Exception as exc:  # pragma: no cover - defensive
            _warning(warnings, "resequence_failed", message=f"课次重排未完成：{str(exc)[:120]}")
    for report in resequenced:
        _warning(warnings, "sessions_resequenced", message=f"调课已生效，{report['applied_count']} 个课次已按新日期重排（课次材料随序号保持不变）。")
    active_ids = {request["request_id"] for request in requests}
    for link in links.values():
        if link["request_id"] not in active_ids:
            conn.execute("UPDATE academic_schedule_change_session_links SET status='missing_unconfirmed',updated_at=? WHERE teacher_id=? AND semester_id=? AND request_id=?", (stamp, teacher_id, semester_id, link["request_id"]))
    split = _split_official(official, requests, offerings, sessions)
    lessons, lesson_slots, used_sessions = [], {}, set()
    for item in split:
        offering = _offering_for(item, offerings)
        candidates = [row for row in sessions if offering and row["class_offering_id"] == offering["id"] and row.get("schedule_status") != "cancelled" and _slot_key(row["slot"]) == _slot_key(item)]
        session = candidates[0] if len(candidates) == 1 and candidates[0]["id"] not in used_sessions else None
        if session:
            used_sessions.add(session["id"])
            # Reading an official slot is not an acknowledgement of a manual
            # edit. Preserve the old comparison baseline across every sync,
            # including complete snapshots where the request is absent.
            if session["id"] not in local_conflicts:
                _bind(conn, teacher_id, semester_id, session, item, session["slot"], stamp, "official_exact")
        elif offering:
            _warning(warnings, "unresolved_official", message="部分正式课次尚未精确关联课堂，保留正式安排且不猜测课堂课次。")
        total = sum(row["class_offering_id"] == offering["id"] for row in sessions) if offering else 0
        lesson = _lesson(item, teacher_id, semester_id, offering, session, total)
        lessons.append(lesson)
        lesson_slots.setdefault((_identity_key(item), _slot_key(item)), []).append(lesson)
    pending_groups = {}
    for request in requests:
        if request["status"] == "pending":
            for detail in request["details"]:
                pending_groups.setdefault((_identity_key(request), _slot_key(detail["original"])), []).append((request, detail))
    for group_key, group in pending_groups.items():
        # Even two identical pending requests remain distinct review decisions.
        if len(group) != 1:
            for request, _ in group:
                _warning(warnings, "pending_conflict", request_id=request["request_id"], message="同一原课次存在多条待审申请，暂不推断唯一调整目标。")
            continue
        request, detail = group[0]
        original, proposed = detail["original"], detail["proposed"]
        sources = lesson_slots.get(group_key, [])
        if len(sources) != 1 or request["kind"] not in ("move", "cancel") or (request["kind"] == "move" and not proposed):
            _warning(warnings, "pending_source_unresolved", request_id=request["request_id"], message="待审申请与正式课表无法唯一对齐，暂不绘制预测位置。")
            continue
        source = sources[0]
        session = resolved.get((request["request_id"], detail["detail_id"]))
        known_session_id = request_session_ids.get((request["request_id"], detail["detail_id"]))
        if proposed and known_session_id and any(
            row["counts_towards_total"] and row.get("session_id") == known_session_id
            and row["actual_date"] == proposed["date"] and row["sections"] == proposed["sections"]
            and (not proposed["room"] or _room(row["classroom"]) == _room(proposed["room"])) for row in lessons
        ):
            _warning(warnings, "pending_target_already_official", request_id=request["request_id"], message="待审申请的拟位置已是该课次的正式安排，未重复生成预测，请核对申请状态。")
            continue
        if session and source["session_id"] != session["id"]:
            _warning(warnings, "pending_identity_conflict", request_id=request["request_id"], message="待审申请与正式课次身份不一致，未建立错误跳转。")
            continue
        kind = "cancel" if request["kind"] == "cancel" else "room" if _slot_key(original) == _slot_key(proposed) else "move"
        proposed_key = f"{source['event_key']}:pending:{request['request_id']}:{detail['detail_id']}" if kind == "move" else None
        source["adjustment"] = {"request_id": request["request_id"], "kind": kind, "phase": "pending", "endpoint": "original",
                                "counterpart_event_key": proposed_key, "counterpart_week_index": proposed["week"] if proposed_key else None,
                                "original": _public_slot(original), "proposed": _public_slot(proposed)}
        if kind == "move":
            if any(row is not source and row["counts_towards_total"] and _same_identity(row, request)
                   and row["actual_date"] == proposed["date"] and set(row["sections"]) & set(proposed["sections"]) for row in lessons):
                _warning(warnings, "pending_target_conflict", request_id=request["request_id"], message="待审申请的拟位置与同教学班另一正式课次重叠；预测保留供核对，不计入正式课时。")
            prediction = {**copy.deepcopy(source), "event_key": proposed_key, "counts_towards_total": False,
                          "actual_date": proposed["date"], "weekday": proposed["weekday"], "week_index": proposed["week"],
                          "sections": proposed["sections"], "classroom": proposed["room"]}
            prediction["adjustment"].update(endpoint="proposed", counterpart_event_key=source["event_key"], counterpart_week_index=original["week"])
            lessons.append(prediction)
    # Explicit remote scope includes cancelled-only classes as well as visible occurrences.
    identities = official + requests
    covered = sorted({offering["id"] for identity in identities if (offering := _offering_for(identity, offerings))})
    history = {item["request_id"]: item for item in json.loads(old["request_history_json"])} if old else {}
    for item in history.values():
        if item["request_id"] not in active_ids:
            item["presence"] = "missing_unconfirmed"
    for request in requests:
        history[request["request_id"]] = {**request, "presence": "present", "last_seen_at": stamp}
    # Keep a previously covered empty classroom covered by a complete teacher snapshot.
    if old:
        allowed = {row["id"] for row in offerings}
        covered = sorted(set(covered) | (set(json.loads(old["covered_offering_ids_json"])) & allowed))
    lessons.sort(key=lambda row: (row["actual_date"], row["sections"], row["event_key"]))
    conn.execute(
        """INSERT INTO teacher_academic_schedule_snapshots(teacher_id,semester_id,revision,published_at,publication_token,
           snapshot_json,request_history_json,lessons_json,warnings_json,covered_offering_ids_json) VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(teacher_id,semester_id) DO UPDATE SET revision=excluded.revision,published_at=excluded.published_at,
           publication_token=excluded.publication_token,snapshot_json=excluded.snapshot_json,request_history_json=excluded.request_history_json,
           lessons_json=excluded.lessons_json,warnings_json=excluded.warnings_json,covered_offering_ids_json=excluded.covered_offering_ids_json""",
        (teacher_id, semester_id, revision, stamp, token, _json(snapshot), _json(list(history.values())), _json(lessons), _json(warnings), _json(covered)),
    )
    return {"semester_id": semester_id, "revision": revision, "official_count": sum(row["counts_towards_total"] for row in lessons),
            "predicted_count": sum(not row["counts_towards_total"] for row in lessons), "request_count": len(requests),
            "updated_session_ids": sorted(changed_sessions), "cancelled_session_ids": sorted(cancelled_sessions), "warnings": warnings}


def _read_snapshot(row: dict) -> dict:
    snapshot = json.loads(row["snapshot_json"])
    lessons = json.loads(row["lessons_json"])
    return {"semester_id": row["semester_id"], "teacher_id": row["teacher_id"], "lessons": lessons,
            "official_lessons": [item for item in lessons if item["counts_towards_total"]],
            "predicted_lessons": [item for item in lessons if not item["counts_towards_total"]],
            "requests": snapshot["requests"], "request_history": json.loads(row["request_history_json"]),
            "covered_offering_ids": json.loads(row["covered_offering_ids_json"]), "warnings": json.loads(row["warnings_json"]),
            "sync_state": {"status": "ready", "semester_id": row["semester_id"], "revision": row["revision"], "last_success_at": row["published_at"]}}


def load_teacher_prediction_snapshot(conn, teacher_id: int, semester_id: int) -> dict | None:
    if not _table_exists(conn, "teacher_academic_schedule_snapshots"):
        return None
    row = conn.execute("SELECT * FROM teacher_academic_schedule_snapshots WHERE teacher_id=? AND semester_id=?", (int(teacher_id), int(semester_id))).fetchone()
    if not row:
        return None
    result = _read_snapshot(dict(row))
    state = conn.execute("SELECT status,error,last_attempt_at FROM teacher_academic_schedule_sync_state WHERE teacher_id=?", (int(teacher_id),)).fetchone()
    if state:
        result["sync_state"].update(dict(state))
    return result


def load_teacher_prediction_terms(conn, teacher_id: int) -> list[dict]:
    if not _table_exists(conn, "teacher_academic_schedule_snapshots"):
        return []
    rows = conn.execute(
        """SELECT p.semester_id,p.revision,p.published_at,s.name,s.start_date,s.end_date,s.week_count
           FROM teacher_academic_schedule_snapshots p JOIN academic_semesters s ON s.id=p.semester_id
           WHERE p.teacher_id=? ORDER BY s.start_date DESC,s.id DESC""", (int(teacher_id),),
    ).fetchall()
    return [{**dict(row), "sync_state": {"status": "ready", "semester_id": row["semester_id"], "revision": row["revision"], "last_success_at": row["published_at"]}} for row in rows]


def load_authorized_prediction_lessons(conn, authorized_offering_ids, *, semester_id=None, academic_year=None, term=None) -> dict:
    """Caller supplies live authorized membership IDs; never accepts a student ID as scope."""
    empty = {"lessons": [], "sync_states": [], "warnings": [], "covered_offering_ids": []}
    ids = sorted({int(value) for value in authorized_offering_ids if int(value) > 0})
    if not ids or not _table_exists(conn, "teacher_academic_schedule_snapshots"):
        return empty
    marks = ",".join("?" for _ in ids)
    scope_rows = conn.execute(
        f"SELECT id,teacher_id,semester_id FROM class_offerings WHERE id IN ({marks}) AND semester_id IS NOT NULL", tuple(ids),
    ).fetchall()
    allowed_by_snapshot = {}
    for row in scope_rows:
        key = (row["teacher_id"], row["semester_id"])
        if semester_id is None or int(row["semester_id"]) == int(semester_id):
            allowed_by_snapshot.setdefault(key, set()).add(row["id"])
    if not allowed_by_snapshot:
        return empty
    where = " OR ".join("(p.teacher_id=? AND p.semester_id=?)" for _ in allowed_by_snapshot)
    params = tuple(value for key in allowed_by_snapshot for value in key)
    # Fetch each possibly large JSON snapshot once, not once per enrolled class.
    rows = conn.execute(
        f"""SELECT p.*,s.name AS semester_name FROM teacher_academic_schedule_snapshots p
            JOIN academic_semesters s ON s.id=p.semester_id WHERE {where}""", params,
    ).fetchall()
    snapshots = {}
    for row in rows:
        row = dict(row)
        if semester_id is not None and int(row["semester_id"]) != int(semester_id):
            continue
        identity = parse_semester_identity(row["semester_name"])
        if academic_year or term is not None:
            if identity is None:
                continue
            year_key, term_key = identity.as_year_term()
            if academic_year and str(academic_year) != year_key:
                continue
            if term is not None and str(term) != term_key:
                continue
        key = (row["teacher_id"], row["semester_id"])
        snapshots[key] = _read_snapshot(row)
    for key, snapshot in snapshots.items():
        allowed = allowed_by_snapshot[key]
        empty["lessons"].extend(row for row in snapshot["lessons"] if row.get("class_offering_id") in allowed)
        empty["covered_offering_ids"].extend(value for value in snapshot["covered_offering_ids"] if value in allowed)
        empty["sync_states"].append(snapshot["sync_state"])
        # Request reasons and other classes' diagnostics are not student payloads.
    empty["covered_offering_ids"] = sorted(set(empty["covered_offering_ids"]))
    return empty
