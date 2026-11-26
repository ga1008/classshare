"""Reconstruct one finished lesson from server-timed evidence and explicit teacher decisions."""
from collections import defaultdict
from datetime import datetime
import hashlib

from fastapi import HTTPException

from . import login_attendance_calendar_service as calendar
from . import login_attendance_service as local
from .classroom_activity_evidence_service import _window_clause
from .login_attendance_settlement_service import assert_no_unpublished_settlement
from .smart_classroom_student_push_service import assert_no_active_push, revoke_for_local_changes


def _digest(value):
    return hashlib.sha256(local._json(value).encode()).hexdigest()


def _evidence(conn, offering_id, ids, start, end, policy):
    facts = defaultdict(dict)
    if not ids:
        return facts
    marks = ",".join("?" for _ in ids)
    # The grace bands are context for the teacher. Only the scheduled window votes.
    before = start - int(policy["before_minutes"]) * 60_000
    after = end + int(policy["after_minutes"]) * 60_000
    rows = conn.execute(f"""SELECT student_id,ip_address,
        CASE WHEN received_at_ms<? THEN 'before' WHEN received_at_ms>=? THEN 'after' ELSE 'during' END AS band,
        MIN(received_at_ms) AS first_ms, MAX(received_at_ms) AS last_ms, COUNT(*) AS n
        FROM login_attendance_access WHERE student_id IN ({marks}) AND received_at_ms>=? AND received_at_ms<?
        GROUP BY student_id,ip_address,band""", (start, end, *ids, before, after))
    for r in rows:
        fact = facts[r["student_id"]]
        if r["band"] == "before":
            fact["before_ms"] = max(fact.get("before_ms", 0), r["last_ms"])
        elif r["band"] == "after":
            fact["after_ms"] = min(fact.get("after_ms", r["first_ms"]), r["first_ms"])
        elif policy["enforce_network"] and not local.rules.matches_network(r["ip_address"], policy["networks"]):
            fact["other_network"] = True
        else:
            fact["first_ms"] = min(fact.get("first_ms", r["first_ms"]), r["first_ms"])
            fact["last_ms"] = max(fact.get("last_ms", 0), r["last_ms"])
            fact["access_count"] = fact.get("access_count", 0) + r["n"]
    if not policy["enforce_network"]:
        lo, hi = (datetime.fromtimestamp(ms / 1000, local.rules.SHANGHAI) for ms in (start, end))
        clause, args = _window_clause("received_at", lo, hi)
        for r in conn.execute(f"""SELECT user_pk, MAX(REPLACE(received_at,' ','T')) AS stamp, COUNT(*) AS n
                FROM behavior_write_inbox WHERE class_offering_id=? AND user_role='student' AND state='applied'
                AND user_pk IN ({marks}) AND {clause} GROUP BY user_pk""", (offering_id, *ids, *args)):
            facts[r["user_pk"]].update(receipt_ms=local.rules.to_ms(r["stamp"]), receipt_count=r["n"])
    return facts


def preview(conn, offering_id, session_id, user, *, lock=False):
    offering = local.require_teacher(conn, offering_id, user, lock=lock)
    _, lessons = calendar.semester_sessions(conn, offering, session_id, lock=lock)
    lesson = lessons[0]
    policy = local.load_policy(conn, offering_id)
    if policy["closed_at_ms"] or lesson["schedule_status"] in {"cancelled", "canceled", "suspended"}:
        raise HTTPException(409, "课程已结课或本课已停课，不能补录点名。")
    try:
        window = local.rules.resolve_session_window(lesson)
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, "请先在课表确认本课起止时间。") from exc
    if window["ends_at_ms"] > local.now_ms():
        raise HTTPException(409, "本课尚未结束，可在下课后按操作记录补录。")
    sessions, _, records = calendar._projection(conn, offering, lessons, None, detail=True)
    session = sessions[0]
    if session["round_id"]:
        current = local.load_round(conn, session["round_id"], lock=lock)
        if current["state"] != "published" or local._load(current["context_json"]).get("time_source") not in {"manual_register", "school_register"}:
            raise HTTPException(409, "本课已有正式点名，请在原轮次核对或更正。")
        assert_no_unpublished_settlement(conn, current["id"])
    if not 1 <= len(records) <= 500:
        raise HTTPException(409, "可补录的课次名单须为 1 至 500 人。")
    eligible = {r["student_id"] for r in records if r["editable"] and r["expected"] and not r["manual_confirmed"]
                and r["status"] == "PENDING" and r["effective_status"] in (None, "PENDING")}
    facts = _evidence(conn, offering_id, sorted(eligible), window["starts_at_ms"], window["ends_at_ms"], policy)
    rows = []
    for r in records:
        sid = r["student_id"]
        fact = facts.get(sid, {})
        rows.append({**r, "eligible": sid in eligible, "evidence": fact,
                     "suggested": "PRESENT" if fact.get("access_count") or fact.get("receipt_count") else None})
    revision = _digest([offering_id, session_id, session["round_id"], session["slot_revision"],
                        policy["revision"], policy["networks"], policy["enforce_network"], window, rows])
    return {"session": session, "records": rows, "window": window, "network_required": policy["enforce_network"],
            "revision": revision}


def save(conn, offering_id, session_id, user, payload):
    offering = local.require_teacher(conn, offering_id, user, lock=True)
    key, fingerprint = payload["idempotency_key"], _digest(payload)
    previous = conn.execute("""SELECT a.after_json FROM login_attendance_audit a
        JOIN login_attendance_rounds r ON r.id=a.round_id WHERE r.class_offering_id=? AND r.session_id=?
        AND a.actor_id=? AND a.action='backfill_saved' AND a.reason=? ORDER BY a.id DESC LIMIT 1""",
        (offering_id, session_id, user["id"], key)).fetchone()
    if previous:
        receipt = local._load(previous["after_json"])
        if receipt["hash"] != fingerprint:
            raise HTTPException(409, "该提交标识已用于其他补录内容。")
        return receipt["result"]
    view = preview(conn, offering_id, session_id, user, lock=True)
    if view["revision"] != payload["revision"]:
        raise HTTPException(409, "课表、名单、考勤或操作记录已变化，请重新预览。")
    choices = {r["student_id"]: r["status"] for r in payload["overrides"]}
    eligible = {r["student_id"]: r for r in view["records"] if r["eligible"]}
    if len(choices) != len(payload["overrides"]) or choices.keys() - eligible.keys():
        raise HTTPException(422, "选择中包含重复学生或已有考勤决定。")
    reason = payload["reason"].strip()
    if any(v != "PENDING" for v in choices.values()) and not reason:
        raise HTTPException(422, "请填写人工判定的依据。")
    auto = [r for sid, r in eligible.items() if sid not in choices and r["suggested"]]
    manual = {sid: value for sid, value in choices.items() if value != "PENDING"}
    if not auto and not manual:
        raise HTTPException(422, "没有可保存的结论，可核对后逐人选择状态。")
    rid = view["session"]["round_id"]
    if not rid:
        _, lessons = calendar.semester_sessions(conn, offering, session_id, lock=True)
        rid = calendar._manual_round(conn, offering, lessons[0], user)
        sealed = {(r["student_id"], r["student_number"], bool(r["expected"])) for r in local._records(conn, rid)}
        reviewed = {(r["student_id"], r["student_number"], bool(r["expected"])) for r in view["records"]}
        if sealed != reviewed:
            raise HTTPException(409, "建册时学生名单已变化，请重新预览。")
    assert_no_active_push(conn, rid)
    revoke_for_local_changes(conn, rid, [r["student_id"] for r in auto] + list(manual))
    now = local.now_ms()
    updates = []
    for r in auto:
        fact = r["evidence"]
        reason_code = "server_access_observed" if fact.get("access_count") else "server_learning_received"
        evidence = {"backfill": fact, "window": view["window"], "reason_code": reason_code,
                    "network_access": {"observation_count": fact.get("access_count", 0)}}
        updates.append((fact.get("first_ms"), fact.get("last_ms"), local._json(evidence), local.REASON_LABELS[reason_code],
                        now, rid, r["student_id"], r["revision"] or 1))
    if auto:
        conn.executemany("""UPDATE login_attendance_records SET status='PRESENT',suggestion='PRESENT',
            quality='server_observed',first_seen_ms=?,last_seen_ms=?,evidence_json=?,reason=?,
            revision=revision+1,updated_at_ms=? WHERE round_id=? AND student_id=? AND revision=?
            AND status='PENDING' AND manual_confirmed=0 AND expected=1""", updates)
        # The PostgreSQL adapter closes the batch cursor. Verify the actual
        # written versions once across the class, on the same transaction.
        versions = {r["student_id"]: r["revision"] for r in conn.execute(
            """SELECT student_id,revision FROM login_attendance_records WHERE round_id=? AND
               status='PRESENT' AND quality='server_observed' AND manual_confirmed=0 AND updated_at_ms=?""", (rid, now))}
        if any(versions.get(r["student_id"]) != (r["revision"] or 1) + 1 for r in auto):
            raise HTTPException(409, "学生记录已变化，请重新预览。")
        conn.execute("UPDATE login_attendance_rounds SET revision=revision+1 WHERE id=?", (rid,))
        local._cancel_appeals(conn, rid, {r["student_id"] for r in auto}, user, "课内操作记录已确认出勤。")
    if manual:
        changes = [{"student_id": sid, "revision": eligible[sid]["revision"] or 1, "status": value}
                   for sid, value in manual.items()]
        outcome = local.change_records(conn, rid, user, {"records": changes, "reason": reason}, include_round=False)
        if any(r["result"] != "updated" for r in outcome["results"]):
            raise HTTPException(409, "学生记录已变化，请重新预览。")
    current = local.load_round(conn, rid)
    context = {**local._load(current["context_json"]), "backfill_window": view["window"]}
    conn.execute("UPDATE login_attendance_rounds SET context_json=? WHERE id=?", (local._json(context), rid))
    result = {"round_id": rid, "round_revision": current["revision"], "automatic": len(auto), "manual": len(manual),
              "pending": len(eligible) - len(auto) - len(manual)}
    local._audit(conn, rid, "backfill_saved", user, reason=key, after={"hash": fingerprint, "result": result,
                 "sync_school": payload["sync_school"],
                 "slot_revision": view["session"]["slot_revision"],
                 "window": view["window"], "network_required": view["network_required"], "preview_revision": view["revision"],
                 "evidence": {r["student_id"]: r["evidence"] for r in eligible.values()}, "overrides": choices})
    return result
