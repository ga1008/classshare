"""One teacher action reconciles a bounded set of students in an exact school lesson."""
from collections import Counter

from fastapi import HTTPException

from ..database import get_db_connection
from . import login_attendance_school_register_service as register
from . import login_attendance_service as local
from . import smart_classroom_alignment as alignment
from . import smart_classroom_auto_sync_service as auto
from . import smart_classroom_calendar_sync_service as calendar
from . import smart_classroom_miniapp_adapter as mini
from . import smart_classroom_workflow_service as flow
from . import smart_classroom_write_service as ledger
from .login_attendance_settlement_service import has_unpublished_settlement
from .smart_classroom_student_push_service import assert_no_active_push


def _result(state, message, rows=(), **kwargs):
    return {"state": state, "message": message, "rows": list(rows),
            "counts": dict(Counter(r["state"] for r in rows)), "local_changed": False, "receipt": False, **kwargs}


def _receipt(conn, user, offering_id, session_id, key, scope_hash):
    row = conn.execute("SELECT class_offering_id,session_id,scope_hash FROM smart_attendance_calendar_receipts "
                       "WHERE owner_teacher_id=? AND request_key=?", (user["id"], key)).fetchone()
    if row:
        if (row["class_offering_id"], row["session_id"], row["scope_hash"]) != (offering_id, session_id, scope_hash):
            raise HTTPException(409, "该请求已用于其他课次或学生范围，请重新同步。")
        return calendar.status(conn, user, offering_id, session_id, key)


def _check_backfill_version(conn, user, offering_id, session_id, key, student_ids):
    if not key.startswith("backfill.v1."):
        return
    parts = key.split(".", 3)
    if len(parts) != 4 or not parts[2].isdigit() or len(parts[2]) > 18 or student_ids is not None:
        raise HTTPException(409, "补录回执与本课全班范围不符。")
    row = conn.execute("""SELECT r.revision,a.after_json FROM login_attendance_rounds r
        JOIN login_attendance_audit a ON a.round_id=r.id WHERE r.id=? AND r.class_offering_id=?
        AND r.session_id=? AND r.is_current=1 AND r.state='published' AND a.actor_id=?
        AND a.action='backfill_saved' AND a.reason=?""", (int(parts[2]), offering_id, session_id, user["id"], parts[3])).fetchone()
    receipt = local._load(row["after_json"]) if row else {}
    from .login_attendance_calendar_service import _slot_revision
    if (not receipt.get("sync_school") or receipt["result"]["round_revision"] != row["revision"]
            or receipt["slot_revision"] != _slot_revision(local._session(conn, offering_id, session_id))):
        raise HTTPException(409, "本课补录版本已变化，请核对当前名单后使用本课同步。")


async def reconcile(user, offering_id, session_id, student_ids, key, location=None):
    if student_ids is not None and len(set(student_ids)) != len(student_ids):
        raise HTTPException(422, "选择中含有重复学生。")
    scope_hash = ledger._hash(sorted(student_ids) if student_ids is not None else None)
    with get_db_connection() as conn:
        calendar._session(conn, user, offering_id, session_id)
        existing = _receipt(conn, user, offering_id, session_id, key, scope_hash)
        if existing:
            return existing

        _check_backfill_version(conn, user, offering_id, session_id, key, student_ids)

    def consume(revision, source, slot, access, snapshot):
        with get_db_connection() as conn:
            calendar._session(conn, user, offering_id, session_id, lock=True)
            existing = _receipt(conn, user, offering_id, session_id, key, scope_hash)
            if existing:
                return existing
            _check_backfill_version(conn, user, offering_id, session_id, key, student_ids)
            flow.assert_current_alignment(conn, user, offering_id, session_id, source, slot, revision)
            ledger._account_access(conn, user["id"], source["external_account_key"], access["credential_generation"])
            result = _apply(conn, user, offering_id, session_id, student_ids, key, location,
                            revision, source, access, snapshot)
            # A browser location is a continuation of this same request. A fresh
            # snapshot is read when it arrives, before any job is authorized.
            if result["state"] in {"location_required", "alignment_required"}:
                return result
            result["has_receipt"] = True
            inserted = conn.execute("INSERT INTO smart_attendance_calendar_receipts "
                "(owner_teacher_id,request_key,class_offering_id,session_id,scope_hash,result_json,created_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(owner_teacher_id,request_key) DO NOTHING RETURNING request_key",
                (user["id"], key, offering_id, session_id, scope_hash, ledger._json(result), ledger._now())).fetchone()
            if not inserted:
                raise HTTPException(409, "此请求已由另一课次接收，请重新核对。")
            return result

    result = await calendar.read_lesson_snapshot(user, offering_id, session_id, consume)
    if result["state"] == "alignment_required":
        aligned = await flow.match_lesson(user, offering_id, session_id)
        if aligned.get("binding", {}).get("state") == "matched":
            result = await calendar.read_lesson_snapshot(user, offering_id, session_id, consume)
        else:
            result = _result("alignment_required", aligned.get("binding", {}).get("message") or "课次对应尚不能确定。")
    return {"local_changed": False, "receipt": False, **result}


def compare_for_push(ctx, snapshot, selection):
    """Re-evaluate authority and baseline after queuing, without expanding the selection."""
    targets = [t for t in ledger._targets(ctx) if str(t["student_id"]) in selection["baseline"]]
    people = {r["student_id"]: r for r in ctx["students"]}
    baseline = {t["student_number"]: selection["baseline"][str(t["student_id"])] for t in targets}
    directions = {}
    for target in targets:
        row = people[target["student_id"]]
        direction = register.decision_direction(row, ctx, snapshot)
        directions[target["student_id"]] = direction
        actual = snapshot["students"].get(target["student_number"])
        protected = actual in ledger.PROTECTED_STATUSES or actual == "NOT_APPLICABLE"
        if direction == "local_newer" and target["target_status"] and not (protected and row.get("backfill_inferred")):
            target["reconcile_newer"] = True
    rows = ledger.compare_students(targets, baseline, snapshot["students"], school_authoritative=True)
    for row in rows:
        if row["state"] != "needs_teacher_action":
            continue
        student = people[row["student_id"]]
        if student.get("backfill_inferred") and row.get("remote_status") == "NOT_APPLICABLE":
            row.update(state="protected", reason="保留学校免考勤记录。")
            continue
        if directions[row["student_id"]] != "local_newer":
            row.update(state="conflict", reason="两端状态有差异，时间未能证明本地较新。")
        elif row["student_id"] not in selection.get("writable_ids", people):
            row.update(state="conflict", reason="该学生的状态在同步期间变化，保留两端现值。")
        else:
            row["reason"] = "将按较新的本地记录更新学校并回读。"
    return targets, baseline, rows


def _apply(conn, user, offering_id, session_id, ids, key, location, revision, source, access, snapshot):
    current = calendar._round(conn, offering_id, session_id)
    if local.load_policy(conn, offering_id).get("closed_at_ms"):
        return _result("blocked", "课程已结课。")
    if current and (current["state"] != "published" or has_unpublished_settlement(conn, current["id"])):
        return _result("local_pending", "本地点名仍在进行或等待结算，发布后可同步。")
    if current:
        assert_no_active_push(conn, current["id"])
        active = conn.execute("SELECT * FROM smart_attendance_auto_sync WHERE local_round_id=?",
                              (str(current["id"]),)).fetchone()
        if active and (auto._lifecycle_unknown(active) or active["state"] == "outcome_unknown" or
                       active["enabled"] and active["state"] in {"pending", "running", "reading", "comparing"} or
                       conn.execute("SELECT 1 FROM smart_attendance_sync_operations WHERE run_id=? "
                                    "AND state='outcome_unknown' LIMIT 1", (active["last_run_id"],)).fetchone()):
            return _result("blocked", "本课已有学校任务正在执行或等待回读，请查看原回执。")
    record = snapshot.get("record")
    if not current and not record:
        return _result("empty", "未开始点名。")
    roster = local._roster(conn, offering_id)
    numbers = [r["student_id_number"] for r in roster if not r.get("confirmed_retake")]
    if not alignment.roster_authorized(alignment.roster_evidence(numbers, snapshot["students"]), source,
                                       ledger._hash(sorted(snapshot["students"]))):
        return _result("alignment_required", "学校名单有变化，请核对本课所属教学班。")
    ctx = ledger.load_read_context(conn, user, current["id"], lock=True) if current else {
        "class_offering_id": offering_id, "session_id": session_id,
        "students": [{"student_number": r["student_id_number"], "name": r["name"]} for r in roster]}
    selection = {"session_id": session_id, "offering_id": offering_id, "binding_revision": revision,
                 "remote_checkin_id": str(record["id"]) if record else None, "bidirectional": True}
    if not record:
        return _create(conn, user, ctx, selection, ids, key, location, access, snapshot)
    flow.persist_remote_snapshot(conn, user, ctx, source, snapshot)
    imported = register.reconcile_snapshot(conn, user, offering_id, session_id, snapshot, ids)
    if not imported["round_id"]:
        if ids is None:
            rows = [_unmatched_school_student(no, snapshot) for no in snapshot["students"]]
        else:
            rows = [_missing_local(s, roster) for s in ids]
        return _result("partial", "已读取学校点名；所选学生没有可确认的本地身份。", rows)
    ctx = ledger.load_read_context(conn, user, imported["round_id"])
    people = {r["student_id"]: r for r in ctx["students"]}
    chosen = sorted(people) if ids is None else ids
    selection.update(kind="attendance_calendar_v1", round_id=ctx["round_id"], local_fingerprint=ledger.result_fingerprint(ctx),
                     baseline={str(s): snapshot["students"].get(people[s]["student_number"]) for s in chosen if s in people})
    _, _, rows = compare_for_push(ctx, snapshot, selection)
    missing = [s for s in chosen if s not in people]
    for row in rows:
        row["name"] = people[row["student_id"]]["name"]
        if row["student_id"] in imported["imported_ids"]:
            row.update(state="imported", reason="已按学校结果更新本地。")
        if record.get("isStop") is not True and row["state"] == "needs_teacher_action":
            row.update(state="blocked", reason="学校点名仍在进行，结束后可更新学校。")
    rows.extend(_missing_local(s, roster) for s in missing)
    if ids is None:
        entered = {r["student_number"] for r in rows}
        rows.extend(_unmatched_school_student(no, snapshot) for no in snapshot["students"] if no not in entered)
    rows.sort(key=lambda r: {"needs_teacher_action":1, "imported":2, "confirmed":3}.get(r["state"], 0))
    changed = bool(imported["created"] or imported["imported_ids"])
    writable = [r["student_id"] for r in rows if r["state"] == "needs_teacher_action"]
    review = any(r["state"] not in {"imported", "confirmed", "needs_teacher_action"} for r in rows)
    if writable:
        selection.update(writable_ids=writable, excluded_ids=missing)
        _enqueue(conn, user, selection, key, None)
        return _result("queued", "本地已核对，学校更新已排队。", rows, receipt=True, local_changed=changed,
                       requires_review=review, imported_count=len(imported["imported_ids"]))
    if review:
        state = "partial"
        prefix = "已建立本地点名，" if imported["created"] else ""
        message = f"{prefix}已将 {len(imported['imported_ids'])} 人的学校结果同步到本地；其余差异可展开核对。"
    elif changed:
        state, message = "imported", "学校点名已登记到本地。"
    else:
        state, message = "confirmed", "两端出勤已一致。"
    return _result(state, message, rows, local_changed=changed, imported_count=len(imported["imported_ids"]))


def _missing_local(student_id, roster):
    person = next((p for p in roster if p["id"] == student_id), {})
    return {"student_id": student_id, "name": person.get("name", ""), "student_number": person.get("student_id_number", ""),
            "state": "no_local_record", "local_status": None, "target_status": None, "remote_status": None,
            "reason": "该学生不在本课有效点名名单。"}


def _unmatched_school_student(number, snapshot):
    return {"student_id": None, "name": snapshot.get("student_names", {}).get(number, ""), "student_number": number,
            "state": "unmapped", "local_status": None, "target_status": None, "remote_status": snapshot["students"][number],
            "reason": "学校名单中有此人，本地没有可同步的对应记录。"}


def _enqueue(conn, user, selection, key, location):
    flow.enqueue_sync(conn, user, selection["offering_id"], selection["round_id"],
                      {"expected_binding_revision": selection["binding_revision"],
                       "idempotency_key": calendar._key(key), "location": location}, calendar=selection)


def _create(conn, user, ctx, selection, ids, key, location, access, snapshot):
    available = bool(snapshot.get("can_checkin") and mini.LIFECYCLE_VERIFIED)
    if ids is not None or not available:
        return _result("school_missing", "新建学校点名将涉及全班。" if ids is not None and available else "学校暂未开放该课次点名。",
                       requires_full_roster=bool(ids is not None and available), can_create=available)
    unresolved = auto.unresolved_creation_students(ctx, snapshot["students"])
    if unresolved or not snapshot["students"]:
        people = {r["student_number"]: r["student_id"] for r in ctx["students"]}
        rows = [{**_unmatched_school_student(n, snapshot), "student_id": people.get(n),
                 "state": "unconfirmed", "remote_status": None, "reason": "请先核对本课考勤。"} for n in unresolved]
        return _result("partial", f"学校新建点名会默认缺课；学校名单有 {len(rows)} 人尚无可推送的本地状态，请先核对。", rows)
    if location is None:
        return _result("location_required", "新建本课学校点名需要本次浏览器定位。")
    location = auto._location(location)
    selection.update(kind="attendance_calendar_create_v1", round_id=ctx["round_id"],
                     local_fingerprint=ledger.result_fingerprint(ctx), roster_hash=ledger._hash(sorted(snapshot["students"])),
                     credential_generation=access["credential_generation"])
    _enqueue(conn, user, selection, key, location)
    return _result("queued", "已安排新建本课学校点名并逐人回读。", receipt=True)
