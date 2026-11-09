"""Local lesson attendance: immutable inputs, positive-only inference, teacher decisions."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import re
import time

from fastapi import HTTPException

from ..db.connection import begin_immediate_transaction, execute_insert_returning_id, get_configured_db_engine
from . import login_attendance_rules as rules
from .classroom_activity_evidence_service import build_classroom_activity_evidence, _roster, _window_clause
from .login_attendance_access_service import collection_health, enabled

ALGORITHM_VERSION = rules.VERSION
REASON_LABELS = {
    "cohort_matched": "课堂网络接入与多数同学相符", "teacher_reference_matched": "符合教师确认的时间参考",
    "baseline_insufficient": "班级时间基准尚不足", "network_unconfirmed": "有接入记录，网络需核对",
    "no_access_evidence": "暂未收到可信接入记录", "outside_cohort_tolerance": "时间偏离需教师核对",
    "boundary_sensitive": "群体边界需教师核对", "collection_pending": "采集记录仍在处理",
    "later_access_cohort_matched": "后续课堂网络接入与全班活动时间相符",
}


def now_ms():
    return int(time.time() * 1000)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load(value, fallback=None):
    try:
        return json.loads(value) if value else ({} if fallback is None else fallback)
    except (ValueError, TypeError):
        return {} if fallback is None else fallback


def _begin(conn):
    if get_configured_db_engine() == "sqlite" and not getattr(conn, "in_transaction", False):
        begin_immediate_transaction(conn)


def require_teacher(conn, offering_id, user, *, lock=False):
    if user.get("role") != "teacher":
        raise HTTPException(403, "只有任课教师可以管理点名。")
    if lock:
        _begin(conn)
    # Serialize attendance decisions and ownership changes without blocking
    # recorder/FK FOR KEY SHARE readers of the same classroom.
    suffix = " FOR NO KEY UPDATE" if lock and get_configured_db_engine() == "postgres" else ""
    row = conn.execute("SELECT * FROM class_offerings WHERE id=?" + suffix, (int(offering_id),)).fetchone()
    if not row or int(row["teacher_id"]) != int(user["id"]):
        raise HTTPException(403, "无权管理此课堂的点名。")
    return dict(row)


def _session(conn, offering_id, session_id):
    row = conn.execute("SELECT * FROM class_offering_sessions WHERE id=? AND class_offering_id=?",
                       (int(session_id), int(offering_id))).fetchone()
    if not row:
        raise HTTPException(404, "课次不存在。")
    return dict(row)


def load_policy(conn, offering_id):
    row = conn.execute("SELECT * FROM login_attendance_policies WHERE class_offering_id=?", (offering_id,)).fetchone()
    result = dict(row) if row else {"class_offering_id": offering_id, "revision": 0, "closed_at_ms": None,
                                  "before_minutes": 10, "after_minutes": 10, "appeal_days": 7,
                                  "teacher_reference_ms": None, "networks_json": "[]"}
    result["networks"] = _load(result.pop("networks_json"), [])
    return result


def save_policy(conn, offering_id, user, payload):
    require_teacher(conn, offering_id, user, lock=True)
    old = load_policy(conn, offering_id)
    if int(payload.get("revision", -1)) != old["revision"]:
        raise HTTPException(409, "设置已变化，请刷新后重试。")
    try:
        networks = rules.normalize_networks(payload.get("networks", old["networks"]))
        before, after = int(payload.get("before_minutes", old["before_minutes"])), int(payload.get("after_minutes", old["after_minutes"]))
        appeal_days = int(payload.get("appeal_days", old["appeal_days"]))
        if not 0 <= before <= 20 or not 0 <= after <= 20 or not 1 <= appeal_days <= 30:
            raise ValueError("宽容时间须在 0—20 分钟，申诉期限须在 1—30 天。")
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    conn.execute("INSERT INTO login_attendance_policies(class_offering_id,revision,networks_json,before_minutes,after_minutes,"
                 "appeal_days,updated_by,updated_at_ms) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(class_offering_id) DO UPDATE SET "
                 "revision=excluded.revision,networks_json=excluded.networks_json,before_minutes=excluded.before_minutes,"
                 "after_minutes=excluded.after_minutes,appeal_days=excluded.appeal_days,updated_by=excluded.updated_by,updated_at_ms=excluded.updated_at_ms",
                 (offering_id, old["revision"] + 1, _json(networks), before, after, appeal_days, user["id"], now_ms()))
    return load_policy(conn, offering_id)


def _context(conn, offering, session):
    course = conn.execute("SELECT name FROM courses WHERE id=?", (offering["course_id"],)).fetchone()
    semester = str(offering.get("semester") or "")
    if offering.get("semester_id"):
        sem = conn.execute("SELECT name FROM academic_semesters WHERE id=?", (offering["semester_id"],)).fetchone()
        if sem:
            semester = str(sem["name"])
    year = re.search(r"(20\d{2})\s*[-—]\s*(20\d{2})", semester)
    term = re.search(r"(?:第|[-_])\s*([12一二])(?:学期|$)", semester)
    return {"class_offering_id": offering["id"], "session_id": session["id"],
            "course_name": course["name"] if course else "", "session_title": session["title"],
            "session_date": session["session_date"], "weekday": session["weekday"], "week_index": session.get("week_index"),
            "section_text": session.get("academic_section_text") or "", "course_code": session.get("academic_course_code") or "",
            "semester": semester, "year": f"{year[1]}-{year[2]}" if year else "",
            "term": {"一":1,"二":2,"1":1,"2":2}.get(term[1]) if term else None}


def _audit(conn, round_id, action, user=None, *, record_id=None, before=None, after=None, reason=""):
    conn.execute("INSERT INTO login_attendance_audit(round_id,record_id,actor_id,action,before_json,after_json,reason,created_at_ms) "
                 "VALUES(?,?,?,?,?,?,?,?)", (round_id, record_id, user.get("id") if user else None, action,
                                             _json(before or {}), _json(after or {}), str(reason)[:1000], now_ms()))


def load_round(conn, round_id, user=None, *, lock=False):
    row = conn.execute("SELECT * FROM login_attendance_rounds WHERE id=?", (int(round_id),)).fetchone()
    if not row:
        raise HTTPException(404, "点名轮次不存在。")
    result = dict(row)
    if user is not None:
        if not result["class_offering_id"]:
            if user.get("role") != "teacher" or int(user["id"]) != result["teacher_id"] or lock:
                raise HTTPException(403, "历史课堂已归档，不能执行此操作。")
        else:
            require_teacher(conn, result["class_offering_id"], user, lock=lock)
    if lock and get_configured_db_engine() == "postgres":
        result = dict(conn.execute("SELECT * FROM login_attendance_rounds WHERE id=? FOR UPDATE", (int(round_id),)).fetchone())
    return result


def _revision(round_row, payload):
    try:
        matches = int(payload.get("revision", -1)) == round_row["revision"]
    except (ValueError, TypeError):
        matches = False
    if not matches:
        raise HTTPException(409, "点名已被更新，请刷新后重试；当前选择会保留。")


def create_round(conn, offering_id, session_id, user, payload, *, timestamp=None):
    if not enabled():
        raise HTTPException(503, "自动点名暂未启用。")
    now = now_ms() if timestamp is None else timestamp
    offering = require_teacher(conn, offering_id, user, lock=True)
    request_key = str(payload.get("request_key") or "").strip()
    if not 8 <= len(request_key) <= 100:
        raise HTTPException(422, "请提供有效的操作幂等标识。")
    old_request = conn.execute("SELECT id,session_id FROM login_attendance_rounds WHERE class_offering_id=? AND request_key=?",
                               (offering_id, request_key)).fetchone()
    if old_request:
        if old_request["session_id"] != session_id:
            raise HTTPException(409,"操作标识已用于其他课次，请重新打开本课次。")
        return round_view(conn, old_request["id"], user)
    session = _session(conn, offering_id, session_id)
    if session.get("schedule_status") in {"cancelled", "canceled", "suspended"}:
        raise HTTPException(409, "此课次已停课，不能开启点名。")
    policy = load_policy(conn, offering_id)
    if policy.get("closed_at_ms"):
        raise HTTPException(409, "课程已结课，仅可更正历史考勤。")
    try:
        period = rules.resolve_session_window(session, explicit_start=payload.get("starts_at"), explicit_end=payload.get("ends_at"))
        networks = rules.normalize_networks(payload.get("networks") or policy["networks"])
        reference = rules.to_ms(payload["teacher_reference_at"]) if payload.get("teacher_reference_at") else None
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if now >= period["ends_at_ms"]:
        raise HTTPException(409, "本次课已结束，请使用历史补核，不能重开实时点名。")
    if period["starts_at_ms"] - now > 24 * 3600_000:
        raise HTTPException(422, "只能预约未来 24 小时内的课次。")
    window_start = period["starts_at_ms"] - policy["before_minutes"] * 60_000
    window_end = period["ends_at_ms"] + policy["after_minutes"] * 60_000
    if reference is not None and not window_start <= reference < window_end:
        raise HTTPException(422, "教师时间参考必须在本次课窗口内。")
    previous = conn.execute("SELECT * FROM login_attendance_rounds WHERE session_id=? AND is_current=1 AND state<>'void'",
                            (session_id,)).fetchone()
    if previous:
        if previous["state"] in {"scheduled", "running"}:
            raise HTTPException(409, "此课次已有进行中的点名。")
        if int(payload.get("supersedes_id") or 0) != previous["id"]:
            raise HTTPException(409, "此课次已有点名，请明确选择重新开启并保留历史。")
        conn.execute("UPDATE login_attendance_rounds SET is_current=0,revision=revision+1 WHERE id=?", (previous["id"],))
        _cancel_appeals(conn, previous["id"], None, user, "本轮点名已被重新开启的轮次替代，历史申请与处理记录保留。")
    roster = _roster(conn, offering_id)
    if not roster:
        raise HTTPException(409, "课堂暂无有效学生名单。")
    policy.update({"networks": networks, "teacher_reference_ms": reference, "algorithm_version": ALGORITHM_VERSION})
    context = {**_context(conn, offering, session), **period}
    round_id = execute_insert_returning_id(conn,
        "INSERT INTO login_attendance_rounds(class_offering_id,session_id,teacher_id,request_key,state,starts_at_ms,ends_at_ms,"
        "window_start_ms,window_end_ms,settle_at_ms,created_at_ms,next_evaluation_ms,policy_json,context_json,supersedes_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (offering_id, session_id, user["id"], request_key, "scheduled" if now < window_start else "running",
         period["starts_at_ms"], period["ends_at_ms"], window_start, window_end, window_end + 120_000,
         now, now, _json(policy), _json(context), previous["id"] if previous else None))
    previous_manual = {}
    if previous:
        previous_manual = {r["student_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM login_attendance_records WHERE round_id=? AND manual_confirmed=1", (previous["id"],)).fetchall()}
    for row in roster:
        inherited = previous_manual.get(row["id"], {})
        conn.execute("INSERT INTO login_attendance_records(round_id,student_id,student_number,name,class_name,confirmed_retake,expected,"
                     "status,manual_confirmed,reason,decided_by,decided_at_ms,updated_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (round_id, row["id"], row["student_id_number"] or "", row["name"], row["class_name"], int(row["confirmed_retake"]),
                      0 if row["confirmed_retake"] else 1,inherited.get("status", "PENDING"), int(bool(inherited)), inherited.get("reason", ""),
                      inherited.get("decided_by"), inherited.get("decided_at_ms"), now))
    _audit(conn, round_id, "created", user, after={"context": context, "policy": policy, "students": len(roster)})
    evaluate_round(conn, round_id, timestamp=now)
    return round_view(conn, round_id, user)


def _records(conn, round_id):
    return [dict(r) for r in conn.execute("SELECT * FROM login_attendance_records WHERE round_id=? ORDER BY student_number,student_id", (round_id,)).fetchall()]


def evaluate_round(conn, round_id, *, timestamp=None):
    now = now_ms() if timestamp is None else timestamp
    row = load_round(conn, round_id)
    if row["state"] == "void" or not row["is_current"] or not row["class_offering_id"]:
        return
    # All attendance writers use parent -> round -> record order.
    owner = conn.execute("SELECT teacher_id FROM class_offerings WHERE id=?",(row["class_offering_id"],)).fetchone()
    if not owner:
        return
    require_teacher(conn, row["class_offering_id"], {"role":"teacher","id":owner["teacher_id"]}, lock=True)
    row = load_round(conn, round_id, lock=True)
    if row["state"] == "void" or not row["is_current"]:
        return
    if row["state"] == "published" and now > row["window_end_ms"] + 7 * 86400_000:
        # Browser replay evidence has a bounded retention horizon. Historical
        # decisions remain editable, but expired raw coverage cannot erase a
        # previously sealed lesson's evidence.
        return
    policy = _load(row["policy_json"])
    records = _records(conn, round_id)
    if not records:
        return
    end_ms = min(row["window_end_ms"], now + 1)
    if end_ms <= row["window_start_ms"]:
        conn.execute("UPDATE login_attendance_rounds SET next_evaluation_ms=? WHERE id=?", (row["window_start_ms"], round_id))
        return
    ids = [r["student_id"] for r in records]
    placeholders = ",".join("?" for _ in ids)
    observations = [dict(x) for x in conn.execute(
        f"SELECT * FROM login_attendance_access WHERE student_id IN ({placeholders}) AND observed_at_ms>=? AND observed_at_ms<? ORDER BY observed_at_ms,event_key",
        (*ids, row["window_start_ms"], end_ms)).fetchall()]
    calculation = rules.evaluate_roster(records, observations, start_ms=row["window_start_ms"], end_ms=end_ms,
                                        networks=policy["networks"], teacher_reference_ms=policy.get("teacher_reference_ms"))
    network_evidence = {}
    for event in observations:
        sid=event["student_id"]
        item=network_evidence.setdefault(sid,{"first_ip":event["ip_address"],"last_ip":event["ip_address"],
            "distinct_ips":[],"source_types":[],"observation_count":0})
        item["last_ip"]=event["ip_address"]
        item["observation_count"]+=1
        if event["ip_address"] not in item["distinct_ips"] and len(item["distinct_ips"])<16:
            item["distinct_ips"].append(event["ip_address"])
        if event["source"] not in item["source_types"]:
            item["source_types"].append(event["source"])
    roster = [{"id":r["student_id"],"name":r["name"],"student_id_number":r["student_number"],"class_name":r["class_name"],
               "confirmed_retake":r["confirmed_retake"]} for r in records]
    # Observe receipts before reading derived facts. A batch applied midway
    # through this transaction stays conservatively pending in this snapshot.
    # Include the settlement tail; a delayed ACK after class is not true zero.
    receipt_clause, receipt_args = _window_clause("received_at",
        datetime.fromtimestamp(row["window_start_ms"]/1000,rules.SHANGHAI),
        datetime.fromtimestamp(min(now+1,row["settle_at_ms"]+1)/1000,rules.SHANGHAI))
    receipt_rows = conn.execute(f"SELECT user_pk,state,COUNT(*) AS n,MAX(received_at) AS last_received_at "
        f"FROM behavior_write_inbox WHERE class_offering_id=? AND user_role='student' "
        f"AND user_pk IN ({placeholders}) AND {receipt_clause} GROUP BY user_pk,state",
        (row["class_offering_id"],*ids,*receipt_args)).fetchall()
    receipt_counts = {int(sid):{} for sid in ids}
    for receipt in receipt_rows:
        receipt_counts[int(receipt["user_pk"])][receipt["state"]]=int(receipt["n"])
    activity = build_classroom_activity_evidence(conn, row["class_offering_id"], rules.display_time(row["window_start_ms"]),
                                                rules.display_time(end_ms), roster_rows=roster)
    by_student = {int(x["id"]):x for x in activity["students"]}
    for sid, counts in receipt_counts.items():
        collection = by_student[sid]["collection"]
        collection["settlement_receipts"] = counts
        collection["pending_batch_count"] = max(collection["pending_batch_count"], counts.get("pending",0))
        collection["quarantined_batch_count"] = max(collection["quarantined_batch_count"], counts.get("quarantined",0))
        if collection["quarantined_batch_count"]:
            collection["status"]="error"
        elif collection["pending_batch_count"]:
            collection["status"]="pending"
    finalized = now >= row["settle_at_ms"]
    state = "published" if finalized else "settling" if now >= row["ends_at_ms"] or row["ended_at_ms"] else "running"
    pending = sum(int(x["collection"]["pending_batch_count"]) for x in by_student.values())
    isolated = sum(int(x["collection"]["quarantined_batch_count"]) for x in by_student.values())
    watermark = {"observed_through_ms": end_ms, "receipt_cutoff_ms": min(now, row["settle_at_ms"]),
                 "access_event_count":len(observations),"access_last_received_ms":max((x["received_at_ms"] for x in observations),default=None),
                 "receipt_counts":dict(Counter({state:sum(counts.get(state,0) for counts in receipt_counts.values())
                                                for state in ("pending","applied","quarantined","discarded")})),
                 "receipt_last_received_at":max((x["last_received_at"] for x in receipt_rows),default=None),
                 "pending_batch_count": pending, "quarantined_batch_count": isolated,
                 "complete": not (pending or isolated)}
    outputs, newly_present = [], set()
    for record in records:
        candidate = calculation["results"][record["student_id"]]
        facts = by_student[record["student_id"]]
        evidence = {"login":facts["login"], "classroom_access":facts["classroom_access"], "learning":facts["learning"],
                    "collection":facts["collection"], "network_source":"teacher_confirmed_network", "reason_code":candidate["reason_code"],
                    "network_access":network_evidence.get(record["student_id"],{"distinct_ips":[],"source_types":[],"observation_count":0}),
                    "matching_seen_ms":candidate["matching_seen_ms"]}
        status = record["status"]
        if not record["manual_confirmed"] and finalized:
            # Preserve published positive facts; later uncertainty creates a
            # visible suggestion, never an automatic absence or downgrade.
            status = "PRESENT" if candidate["suggestion"] == "PRESENT" or status == "PRESENT" else "PENDING"
        quality = facts["collection"]["status"]
        reason = record["reason"] if record["manual_confirmed"] else REASON_LABELS.get(candidate["reason_code"], "待教师核对")
        changed = any((record[k] != value) for k,value in {
            "status":status,"suggestion":candidate["suggestion"],"quality":quality,"first_seen_ms":candidate["first_seen_ms"],
            "last_seen_ms":candidate["last_seen_ms"],"deviation_ms":candidate["deviation_ms"],"evidence_json":_json(evidence),"reason":reason}.items())
        if changed:
            conn.execute("UPDATE login_attendance_records SET status=?,suggestion=?,quality=?,first_seen_ms=?,last_seen_ms=?,deviation_ms=?,"
                         "evidence_json=?,reason=?,revision=revision+1,updated_at_ms=? WHERE id=?",
                         (status,candidate["suggestion"],quality,candidate["first_seen_ms"],candidate["last_seen_ms"],candidate["deviation_ms"],
                          _json(evidence),reason,now,record["id"]))
        if status=="PRESENT" and record["status"]!="PRESENT":
            newly_present.add(record["student_id"])
        outputs.append({"student_id":record["student_id"],"status":status,"manual":bool(record["manual_confirmed"]),**candidate,
                        "collection":facts["collection"],"learning":facts["learning"]})
    baseline = calculation["baseline"]
    fingerprint = hashlib.sha256(_json({"results":outputs,"baseline":baseline,"state":state,"watermark":watermark}).encode()).hexdigest()
    # Live polling updates one projection, not a full roster archive every 15s.
    # Persist initial, state-transition and final/revised settlement evidence.
    if not row["evaluated_at_ms"] or row["state"]!=state or finalized:
        conn.execute("INSERT INTO login_attendance_evaluations(round_id,evaluated_at_ms,algorithm_version,input_hash,baseline_json,watermark_json,results_json) "
                     "VALUES(?,?,?,?,?,?,?) ON CONFLICT(round_id,input_hash) DO NOTHING",
                     (round_id,now,ALGORITHM_VERSION,fingerprint,_json(baseline),_json(watermark),_json(outputs)))
    conn.execute("UPDATE login_attendance_rounds SET state=?,baseline_json=?,watermark_json=?,evaluated_at_ms=?,next_evaluation_ms=?,"
                 "revision=revision+?,ended_at_ms=COALESCE(ended_at_ms,?) WHERE id=?",
                 (state,_json(baseline),_json(watermark),now,now+(120_000 if finalized else 15_000),int(state!=row["state"] or bool(newly_present)),
                  row["ends_at_ms"] if now>=row["ends_at_ms"] else None,round_id))
    if newly_present:
        _cancel_appeals(conn,round_id,newly_present,None,"新采集证据已确认出勤，申诉已自动纠正并保留处理记录。")

    _schedule_school_sync(conn, round_id)


def _schedule_school_sync(conn, round_id):
    # Scheduling shares the local transaction; no network IO or remote mutation here.
    from .smart_classroom_auto_sync_service import schedule_due_auto_sync
    schedule_due_auto_sync(conn, round_id)


def end_round(conn, round_id, user, payload, *, timestamp=None):
    row = load_round(conn, round_id, user, lock=True)
    if row["state"] not in {"running", "scheduled"}:
        return round_view(conn, round_id, user)
    _revision(row, payload)
    now = now_ms() if timestamp is None else timestamp
    if now < row["starts_at_ms"]:
        raise HTTPException(409, "尚未到上课时间，可作废预约。")
    early = now < row["ends_at_ms"]
    conn.execute("UPDATE login_attendance_rounds SET state='settling',ended_at_ms=?,window_end_ms=?,settle_at_ms=?,revision=revision+1,next_evaluation_ms=? WHERE id=?",
                 (now,now if early else row["window_end_ms"],now+120_000 if early else row["settle_at_ms"],now,round_id))
    _audit(conn, round_id, "ended", user, after={"ended_at_ms":now,"early":early})
    evaluate_round(conn, round_id, timestamp=now)
    return round_view(conn, round_id, user)


def void_round(conn, round_id, user, payload):
    row = load_round(conn, round_id, user, lock=True)
    if row["state"] == "void":
        return round_view(conn, round_id, user)
    _revision(row, payload)
    reason = str(payload.get("reason") or "").strip()
    if not reason or len(reason) > 1000:
        raise HTTPException(422, "请填写 1—1000 字作废原因。")
    conn.execute("UPDATE login_attendance_rounds SET state='void',is_current=0,void_reason=?,revision=revision+1 WHERE id=?", (reason,round_id))
    _audit(conn, round_id, "void", user, before={"state":row["state"]}, reason=reason)
    _cancel_appeals(conn, round_id, None, user, "本次点名已作废，原记录保留供查询。")
    _schedule_school_sync(conn, round_id)
    return round_view(conn, round_id, user)


def _cancel_appeals(conn, round_id, student_ids, user, reason):
    from .approval_workflow_service import auto_cancel_requests
    rows = conn.execute("SELECT id,student_id FROM login_attendance_records WHERE round_id=?", (round_id,)).fetchall()
    subjects = [str(r["id"]) for r in rows if student_ids is None or r["student_id"] in student_ids]
    if subjects:
        auto_cancel_requests(conn, request_type="login_attendance_appeal", subject_ids=subjects, actor=user, note=reason)
    clause, args = ("", ()) if student_ids is None else (" AND student_id IN ("+",".join("?" for _ in student_ids)+")",tuple(student_ids))
    if student_ids is None or student_ids:
        conn.execute("UPDATE login_attendance_attention SET resolved_at_ms=? WHERE round_id=? AND resolved_at_ms IS NULL"+clause,
                     (now_ms(),round_id,*args))


def change_records(conn, round_id, user, payload, *, resolve_appeals=True):
    row = load_round(conn, round_id, user, lock=True)
    if not _can_edit(row):
        raise HTTPException(409, "此轮已作废，请更正有效轮次。")
    changes = payload.get("records")
    reason = str(payload.get("reason") or "").strip()
    if not isinstance(changes,list) or not 1<=len(changes)<=500 or not reason or len(reason)>1000:
        raise HTTPException(422, "请选择学生并填写 1—1000 字更正原因。")
    current = {r["student_id"]:r for r in _records(conn,round_id)}
    seen = set()
    for entry in changes:
        sid = entry.get("student_id") if isinstance(entry,dict) else None
        if type(sid) is not int or sid not in current or sid in seen:
            raise HTTPException(422, "名单包含重复或不属于本次点名的学生。")
        if entry.get("status") not in rules.STATUS_LABELS or entry.get("status")=="PENDING":
            raise HTTPException(422, "请选择有效的正式考勤状态。")
        seen.add(sid)
    results, settled = [], set()
    for entry in changes:
        record = current[entry["student_id"]]
        if type(entry.get("revision")) is not int or record["revision"] != entry["revision"]:
            results.append({"student_id":entry["student_id"],"result":"conflict","revision":record["revision"]})
            continue
        conn.execute("UPDATE login_attendance_records SET status=?,manual_confirmed=1,reason=?,decided_by=?,decided_at_ms=?,"
                     "revision=revision+1,updated_at_ms=? WHERE id=? AND revision=?",
                     (entry["status"],reason,user["id"],now_ms(),now_ms(),record["id"],record["revision"]))
        _audit(conn,round_id,"manual_decision",user,record_id=record["id"],before={"status":record["status"],"revision":record["revision"]},
               after={"status":entry["status"],"revision":record["revision"]+1},reason=reason)
        settled.add(entry["student_id"])
        results.append({"student_id":entry["student_id"],"result":"updated","revision":record["revision"]+1})
    if settled:
        conn.execute("UPDATE login_attendance_rounds SET revision=revision+1 WHERE id=?",(round_id,))
        if resolve_appeals:
            _cancel_appeals(conn,round_id,settled,user,"教师已更正本次考勤，请查看当前结果。")
        _schedule_school_sync(conn, round_id)
    return {"results":results,"round":round_view(conn,round_id,user)}


def _public_record(record, *, teacher=False):
    result = {k:record[k] for k in ("id","student_id","student_number","name","class_name","status","suggestion","expected","confirmed_retake",
                                   "quality","revision","manual_confirmed","first_seen_ms","last_seen_ms","deviation_ms","updated_at_ms")}
    result["status_label"] = rules.STATUS_LABELS[result["status"]]
    if not record["expected"] and record["status"]=="PENDING":
        result["status_label"]="免考勤"
    facts = _load(record["evidence_json"])
    if teacher:
        result["reason"] = record["reason"]
        result["evidence"] = facts
    else:
        result["message"] = "教师已确认" if record["manual_confirmed"] else ("已确认到课" if record["status"]=="PRESENT" else "结束后可提交申诉，请等待教师核对")
        result["participation"] = {"measured_seconds": facts.get("learning",{}).get("measured_seconds"),
                                    "collection_status": facts.get("collection",{}).get("status","no_observation")}
        for key in ("deviation_ms","suggestion","quality"):
            result.pop(key,None)
    return result


def round_view(conn, round_id, user):
    row = load_round(conn,round_id,user)
    records = _records(conn,round_id)
    counts = Counter(r["status"] for r in records)
    result = {k:row[k] for k in ("id","class_offering_id","session_id","state","revision","is_current","starts_at_ms","ends_at_ms",
                                 "window_start_ms","window_end_ms","settle_at_ms","ended_at_ms","evaluated_at_ms","void_reason")}
    result.update({"can_edit":_can_edit(row),"history_only":bool(_load(row["context_json"]).get("history_only")),
                   "context":_load(row["context_json"]),"policy":_load(row["policy_json"]),"baseline":_load(row["baseline_json"]),
                   "watermark":_load(row["watermark_json"]),"records":[_public_record(r,teacher=True) for r in records],
                   "summary":{"total":len(records),"expected":sum(r["expected"] for r in records),"exempt":sum(not r["expected"] for r in records),
                              "counts":dict(counts),"expected_counts":dict(Counter(r["status"] for r in records if r["expected"])),
                              "provisionally_present":sum(r["suggestion"]=="PRESENT" and r["expected"] for r in records)},
                   "collection_health":collection_health()})
    return result


def _can_edit(row):
    return bool(row["class_offering_id"] and row["state"]!="void" and
                (row["is_current"] or (_load(row["context_json"]).get("merged_from") or {}).get("was_current")))


def history(conn,offering_id,user,*,before_id=None,limit=50):
    require_teacher(conn,offering_id,user)
    limit=max(1,min(int(limit),100))
    condition="class_offering_id=?";args=[offering_id]
    if before_id is not None:
        condition+=" AND id<?";args.append(int(before_id))
    rows=conn.execute("SELECT * FROM login_attendance_rounds WHERE "+condition+" ORDER BY id DESC LIMIT ?",(*args,limit+1)).fetchall()
    selected=[dict(r) for r in rows[:limit]]
    items=[{**{k:r[k] for k in ("id","session_id","state","is_current","revision","created_at_ms","starts_at_ms","ends_at_ms")},
            "context":_load(r["context_json"]),"can_edit":_can_edit(r),
            "history_only":bool(_load(r["context_json"]).get("history_only"))} for r in selected]
    return {"items":items,"next_before_id":items[-1]["id"] if len(rows)>limit else None}


def session_view(conn, offering_id, session_id, user):
    require_teacher(conn,offering_id,user)
    session = _session(conn,offering_id,session_id)
    current = conn.execute("SELECT id FROM login_attendance_rounds WHERE session_id=? ORDER BY is_current DESC,id DESC LIMIT 1",(session_id,)).fetchone()
    result = {"session":session,"policy":load_policy(conn,offering_id),"round":round_view(conn,current["id"],user) if current else None}
    return result


def student_view(conn, offering_id, session_id, user):
    if user.get("role") != "student":
        raise HTTPException(403,"仅学生本人可以读取此结果。")
    row = conn.execute("SELECT r.* FROM login_attendance_rounds r JOIN login_attendance_records s ON s.round_id=r.id "
                       "WHERE r.class_offering_id=? AND r.session_id=? AND s.student_id=? "
                       "AND r.is_current=1 AND r.state<>'void' LIMIT 1",
                       (offering_id,session_id,user["id"])).fetchone()
    if not row:
        from .materials_service import ensure_classroom_access
        ensure_classroom_access(conn,offering_id,user)
        return {"round":None,"status":"NOT_STARTED","can_appeal":False}
    record = dict(conn.execute("SELECT * FROM login_attendance_records WHERE round_id=? AND student_id=?",(row["id"],user["id"])).fetchone())
    policy = _load(row["policy_json"])
    ended = row["ended_at_ms"] or row["ends_at_ms"]
    result = {"round":{k:row[k] for k in ("id","state","ends_at_ms","ended_at_ms","revision")},**_public_record(record),
              "can_appeal":bool(row["is_current"]) and row["state"] not in {"scheduled","running","void"} and now_ms()<=ended+policy["appeal_days"]*86400_000}
    pending = conn.execute("SELECT id,status FROM approval_requests WHERE request_type='login_attendance_appeal' "
                           "AND subject_id=? AND applicant_role='student' AND applicant_user_pk=? ORDER BY id DESC LIMIT 1",
                           (str(record["id"]),user["id"])).fetchone()
    result["appeal"] = dict(pending) if pending else None
    if pending and pending["status"]=="pending":
        result["can_appeal"]=False
    return result


def student_detail(conn, round_id, student_id, user):
    row = load_round(conn,round_id,user)
    record = conn.execute("SELECT * FROM login_attendance_records WHERE round_id=? AND student_id=?",(round_id,student_id)).fetchone()
    if not record:
        raise HTTPException(404,"学生不在本次点名名单中。")
    history = conn.execute("SELECT s.*,r.context_json,r.created_at_ms FROM login_attendance_records s JOIN login_attendance_rounds r ON r.id=s.round_id "
                           "WHERE r.class_offering_id=? AND s.student_id=? AND r.starts_at_ms<? AND r.state<>'void' "
                           "AND (r.is_current=1 OR r.context_json LIKE ?) "
                           "ORDER BY r.starts_at_ms DESC LIMIT 5",(row["class_offering_id"],student_id,row["starts_at_ms"],'%"was_current":true%')).fetchall()
    appeal=conn.execute("SELECT id,status,reason,created_at FROM approval_requests WHERE request_type='login_attendance_appeal' "
                        "AND subject_id=? AND status='pending' ORDER BY id DESC LIMIT 1",(str(record["id"]),)).fetchone()
    return {"record":_public_record(dict(record),teacher=True),"pending_appeal":dict(appeal) if appeal else None,
            "history":[{**_public_record(dict(r),teacher=True),"context":_load(r["context_json"])} for r in history]}


def load_attendance_sync_context(conn, user, round_id):
    row = load_round(conn,round_id,user)
    if not row["is_current"] or row["state"]=="void":
        raise HTTPException(409,"只能同步当前有效点名轮次。")
    context = _load(row["context_json"])
    return {**context,"round_id":row["id"],"revision":row["revision"],"state":row["state"],
            "closed":bool(load_policy(conn,row["class_offering_id"]).get("closed_at_ms")),"voided":False,
            "students":[{k:r[k] for k in ("student_id","student_number","status","revision","manual_confirmed","expected")}
                        for r in _records(conn,round_id) if r["expected"] or r["manual_confirmed"]],
            "exempt_students":[r["student_id"] for r in _records(conn,round_id) if not r["expected"] and not r["manual_confirmed"]]}


def close_attendance_for_course(conn, offering_id, user):
    require_teacher(conn,offering_id,user,lock=True)
    stamp = now_ms()
    conn.execute("INSERT INTO login_attendance_policies(class_offering_id,closed_at_ms,updated_at_ms,updated_by) VALUES(?,?,?,?) "
                 "ON CONFLICT(class_offering_id) DO UPDATE SET closed_at_ms=excluded.closed_at_ms,updated_at_ms=excluded.updated_at_ms,"
                 "updated_by=excluded.updated_by,revision=login_attendance_policies.revision+1",(offering_id,stamp,stamp,user["id"]))
    rows = conn.execute("SELECT * FROM login_attendance_rounds WHERE class_offering_id=? AND state IN ('scheduled','running')",(offering_id,)).fetchall()
    for row in rows:
        if row["state"]=="scheduled" or stamp < row["starts_at_ms"]:
            void_round(conn,row["id"],user,{"revision":row["revision"],"reason":"课程结课，取消预约点名"})
        else:
            end_round(conn,row["id"],user,{"revision":row["revision"]},timestamp=stamp)
    return len(rows)
