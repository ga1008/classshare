"""Shared school attendance ledger and the original teacher handoff/readback.

Automatic writes use the separately authorized auto-sync service. Legacy
readback actions cannot modify its operations or erase submission intentions.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import hashlib
import json
import uuid

from fastapi import HTTPException

from ..database import get_db_connection
from ..db.connection import get_configured_db_engine
from . import smart_classroom_attendance_adapter as source_adapter
from .smart_classroom_integration_service import load_teacher_smart_classroom_access_method, open_authenticated_smart_classroom_client

TASK_KIND = "smart_attendance_readback"
REMOTE_STATUSES = frozenset({"CHECKED", "UNCHECKED", "SICK_LEAVE", "PERSONAL_LEAVE", "LATE_OR_EARLY", "STUDY_TOUR", "PRACTICE", "VOLUNTEER"})
PROTECTED_STATUSES = frozenset({"SICK_LEAVE", "PERSONAL_LEAVE", "STUDY_TOUR", "PRACTICE", "VOLUNTEER"})
LOCAL_STATUS_MAP = {"PRESENT": "CHECKED", "ABSENT": "UNCHECKED", "SICK_LEAVE": "SICK_LEAVE", "PERSONAL_LEAVE": "PERSONAL_LEAVE", "LATE": "LATE_OR_EARLY", "EARLY": "LATE_OR_EARLY"}


def _now(): return datetime.now().isoformat(timespec="microseconds")
def _id(): return uuid.uuid4().hex
def _json(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def _hash(value): return hashlib.sha256(_json(value).encode()).hexdigest()


def capabilities():
    from . import smart_classroom_miniapp_adapter as mini
    return {"contract": "gxufl-attendance-write-v2", "read_sources": True, "read_checkins": True,
            "readback": True, "start": mini.LIFECYCLE_VERIFIED, "end": mini.LIFECYCLE_VERIFIED,
            "update_student": mini.STUDENT_WRITE_VERIFIED, "auto_sync": True,
            "remote_lifecycle_verified": mini.LIFECYCLE_VERIFIED, "mode": "verified_round_sync",
            "message": "教师可授权本轮自动同步；服务器只处理精确绑定课次、已发布结果和唯一匹配学生，并逐人回读确认。"}


def require_write_capability(operation):
    if operation not in {"start", "end", "update_student"} or not capabilities().get(operation):
        raise HTTPException(409, {"code": "write_protocol_unverified", "message": "该写入操作尚未完成真实核实。"})


def load_local_context(conn, user, round_id):
    from .login_attendance_service import load_attendance_sync_context
    if user.get("role") != "teacher" or not conn.execute("SELECT 1 FROM teachers WHERE id=? AND COALESCE(is_active,1)=1",(int(user["id"]),)).fetchone():
        raise HTTPException(403,"当前教师身份无效。")
    ctx = load_attendance_sync_context(conn, user, round_id)
    return ctx


def load_read_context(conn,user,round_id,*,lock=False):
    """Retain owner access to historical ledgers after void/replacement."""
    from .login_attendance_service import load_round
    if user.get("role")!="teacher" or not conn.execute("SELECT 1 FROM teachers WHERE id=? AND COALESCE(is_active,1)=1",(int(user["id"]),)).fetchone():
        raise HTTPException(403,"当前教师身份无效。")
    row=load_round(conn,round_id,user,lock=lock)
    if row["is_current"] and row["state"]!="void":
        return {**load_local_context(conn,user,round_id),"is_current":True}
    return {**json.loads(row["context_json"]),"class_offering_id":row["class_offering_id"],"session_id":row["session_id"],
            "round_id":row["id"],"state":row["state"],"revision":row["revision"],"is_current":False}


def _targets(ctx):
    if str(ctx.get("state", "")).lower() not in {"ended", "closed", "finalized", "published", "settling", "settled", "completed"}:
        raise HTTPException(409, "本地点名结束后才能核对远端结果。")
    values = ctx.get("students") or []
    if not values or len(values) > 500:
        raise HTTPException(422, "点名名单为空或超过有界核对范围。")
    result, ids, numbers = [], set(), set()
    for row in values:
        student = int(row["student_id"])
        number = str(row.get("student_number") or "").strip()
        status = str(row.get("local_status", row.get("status", "PENDING"))).upper()
        if student in ids or not number or number in numbers:
            raise HTTPException(409, "学生学号缺失或重复，请先核对本地名单。")
        ids.add(student); numbers.add(number)
        target = LOCAL_STATUS_MAP.get(status)
        if status == "ABSENT" and row.get("manual_confirmed") not in (True, 1):
            target = None
        result.append({"student_id": student, "student_number": number, "local_status": status,
                       "target_status": target, "revision": int(row.get("revision") or 1)})
    return sorted(result, key=lambda row: row["student_id"])


def context_fingerprint(ctx):
    return _hash({"round": str(ctx["round_id"]), "revision": int(ctx["revision"]), "state": ctx["state"],
                  "class": int(ctx["class_offering_id"]), "session": int(ctx["session_id"]), "students": _targets(ctx)})


def result_fingerprint(ctx):
    """Teacher dispatch fences decisions, not later refreshes of raw evidence."""
    students = {r["student_number"]: r for r in ctx.get("students", [])}
    targets = [{**{k: v for k, v in item.items() if k != "revision"},
                "manual_confirmed": bool(students[item["student_number"]].get("manual_confirmed")),
                "decided_at_ms": students[item["student_number"]].get("decided_at_ms")}
               for item in _targets(ctx)]
    return _hash({"round": str(ctx["round_id"]), "revision": int(ctx["revision"]), "state": ctx["state"],
                  "class": int(ctx["class_offering_id"]), "session": int(ctx["session_id"]), "students": targets})


def compare_students(targets, baseline, remote, *, school_authoritative=False):
    result = []
    for target in targets:
        number, desired = target["student_number"], target["target_status"]
        old, actual = baseline.get(number), remote.get(number)
        state, reason = "needs_teacher_action", "请在学校官方端处理后回读核对。"
        if school_authoritative and number not in remote:
            state, reason = "not_enrolled", "学校本次课名单无此学生；本地记录保留，不新增或改写学校学生。"
        elif desired is None: state, reason = "blocked", "本地结果待确认，不能推成远端缺课。"
        elif actual is None: state, reason = "unmapped", "远端名单没有唯一匹配学生。"
        elif actual not in REMOTE_STATUSES: state, reason = "blocked", "远端包含尚未支持的状态，保留原值。"
        elif actual == desired: state, reason = "confirmed", "本次读取的学生状态一致。"
        elif actual in PROTECTED_STATUSES and not (target.get("allow_protected_override") and actual in {"SICK_LEAVE", "PERSONAL_LEAVE"}): state, reason = "protected", "保留远端请假或特殊状态；普通请假仅由时间更新的教师明确更正覆盖。"
        elif actual != old: state, reason = "conflict", "远端状态已变化，保留外部修改。"
        result.append({**target, "baseline_status": old, "remote_status": actual, "state": state, "reason": reason})
    return result


def _account_access(conn, teacher_id, expected_key=None, expected_generation=None):
    access = load_teacher_smart_classroom_access_method(conn, int(teacher_id))
    if not access:
        raise HTTPException(409, {"code": "missing_credential", "message": "请先配置智慧课堂账号密码，保存后会自动验证；本地点名不受影响。"})
    if expected_key and source_adapter.external_account_key(access) != expected_key:
        raise HTTPException(409, {"code": "needs_teacher_reauth", "message": "请使用原智慧课堂账号重新验证后核对；旧任务未被当作未执行。"})
    if expected_generation and access["credential_generation"] != expected_generation:
        raise HTTPException(409, {"code": "credential_changed", "message": "智慧课堂凭据已变化，请重新确认绑定。"})
    return access


def _binding(conn, user, round_id, binding_id, *, lock=False):
    ctx = load_read_context(conn, user, round_id,lock=lock)
    suffix = " FOR UPDATE" if lock and get_configured_db_engine() == "postgres" else ""
    row = conn.execute("SELECT * FROM smart_attendance_write_bindings WHERE id=? AND owner_teacher_id=? AND local_round_id=?" + suffix,
                       (str(binding_id), int(user["id"]), str(round_id))).fetchone()
    if row and (row["class_offering_id"] is None or row["session_id"] is None):
        raise HTTPException(409,"该远端绑定属于已归档或合并的历史课次，仅可查看原核对记录，不能继续同步。")
    if not row or int(row["class_offering_id"]) != int(ctx["class_offering_id"]) or int(row["session_id"]) != int(ctx["session_id"]):
        raise HTTPException(404, "远端绑定不存在或无访问权限。")
    return ctx, dict(row)


@asynccontextmanager
async def _account_lease(key):
    token, now = _id(), _now()
    until = (datetime.now() + timedelta(seconds=120)).isoformat(timespec="microseconds")
    with get_db_connection() as conn:
        row = conn.execute("""INSERT INTO smart_attendance_account_leases(external_account_key,lease_token,lease_until)
            VALUES(?,?,?) ON CONFLICT(external_account_key) DO UPDATE SET lease_token=excluded.lease_token,
            lease_until=excluded.lease_until,generation=smart_attendance_account_leases.generation+1
            WHERE smart_attendance_account_leases.lease_until<? RETURNING lease_token""", (key, token, until, now)).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(429, "该智慧课堂账号正在核对，请稍后重试。", headers={"Retry-After": "3"})
    try: yield token
    finally:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM smart_attendance_account_leases WHERE external_account_key=? AND lease_token=?", (key, token)); conn.commit()


async def read_remote_snapshot(access, source, checkin_id):
    """Only verified reads; return a complete bounded roster and exact scope."""
    async with open_authenticated_smart_classroom_client(access) as (client, _, _login):
        schedules = await source_adapter.fetch_source_schedules(client, source["academic_year"], source["academic_term"])
        matches = [row for row in schedules if str(row["id"]) == str(source["remote_schedule_id"])]
        if len(matches) != 1: raise HTTPException(409, "该账号及学期下找不到已绑定授课班。")
        schedule = matches[0]
        for actual, expected in (("courseId", "course_code"), ("claId", "remote_class_id")):
            if str(schedule.get(actual) or "") != str(source.get(expected) or ""):
                raise HTTPException(409, "远端教学班身份已变化，请重新选择来源。")
        records = await source_adapter.fetch_source_checkins(client, str(schedule["id"]), expected_schedule=schedule)
        chosen = [row for row in records if str(row["id"]) == str(checkin_id)]
        if len(chosen) != 1: raise HTTPException(409, "指定点名不属于该授课班，未选择最新点名代替。")
        detail = await source_adapter.fetch_source_detail(client, checkin_id, expected_schedule=schedule)
        if len(detail["stuList"]) > 1000: raise HTTPException(422, "远端名单过大，未截断核对。")
        return {"record": chosen[0], "students": {str(row["no"]): str(row["status"]) for row in detail["stuList"]}, "fetched_at": _now()}


async def _snapshot(access, source, checkin_id):
    async with _account_lease(source_adapter.external_account_key(access)):
        return await asyncio.wait_for(read_remote_snapshot(access, source, checkin_id), timeout=90)


def _validate_session(ctx, snapshot):
    from .smart_classroom_checkin_sync_service import _match_session
    row = {"id": ctx["session_id"], "session_date": ctx.get("session_date"), "weekday": ctx.get("weekday"),
           "week_index": ctx.get("week_index"), "academic_section_text": ctx.get("section_text")}
    matched, message = _match_session([row], snapshot["record"], {})
    if matched is None: raise HTTPException(409, "该点名不能可靠匹配本地课次：" + message)


async def bind_remote_round(user, round_id, source_token, remote_checkin_id):
    from .attendance_report_service import _decode_option
    source = _decode_option(source_token, int(user["id"]))
    with get_db_connection() as conn:
        ctx = load_local_context(conn, user, round_id)
        source_adapter.validate_source_term(source.get("academic_year"), source.get("academic_term"))
        if (str(ctx["year"]), str(ctx["term"])) != (str(source["academic_year"]), str(source["academic_term"])):
            raise HTTPException(409, "来源学期与本地点名不一致。")
        if not ctx.get("course_code") or str(ctx["course_code"]).strip().upper()!=str(source.get("course_code") or "").strip().upper():
            raise HTTPException(409,"来源课程标识与本地点名不一致，不能仅按日期绑定。")
        access = _account_access(conn, user["id"], source["external_account_key"])
        fence = context_fingerprint(ctx)
    snapshot = await _snapshot(access, source, str(remote_checkin_id))
    _validate_session(ctx, snapshot)
    with get_db_connection() as conn:
        # Parent -> local round -> remote binding is the shared mutation order.
        current = load_read_context(conn, user, round_id, lock=True)
        if context_fingerprint(current) != fence: raise HTTPException(409, "本地点名已变化，请重新核对绑定。")
        _account_access(conn, user["id"], source["external_account_key"], access["credential_generation"])
        existing = conn.execute("SELECT * FROM smart_attendance_write_bindings WHERE external_account_key=? AND remote_checkin_id=? AND state<>'released'",
                                (source["external_account_key"], str(remote_checkin_id))).fetchone()
        if existing:
            if int(existing["owner_teacher_id"]) == int(user["id"]) and existing["local_round_id"] == str(round_id):
                return {"id": existing["id"], "revision": existing["revision"], "state": existing["state"]}
            raise HTTPException(409, "该学校点名已经绑定其他本地轮次，不能由两个任务同时接管。")
        binding_id, now = _id(), _now()
        inserted = conn.execute("""INSERT INTO smart_attendance_write_bindings(id,owner_teacher_id,class_offering_id,session_id,
            local_round_id,external_account_key,credential_id,credential_generation,source_json,remote_checkin_id,
            remote_snapshot_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING RETURNING id""",
            (binding_id, int(user["id"]), ctx["class_offering_id"], ctx["session_id"], str(round_id), source["external_account_key"],
             access["credential_id"], access["credential_generation"], _json(source), str(remote_checkin_id), _json(snapshot), now, now)).fetchone()
        if not inserted:
            raise HTTPException(409, "该学校点名已被其他请求绑定，请刷新核对。")
        conn.commit()
    return {"id": binding_id, "revision": 1, "state": "active", "mode": "teacher_handoff"}


def _event(conn, run_id, kind, teacher_id, detail=None):
    conn.execute("INSERT INTO smart_attendance_sync_events(id,run_id,event_type,actor_teacher_id,detail_json,created_at) VALUES(?,?,?,?,?,?)",
                 (_id(), run_id, kind, teacher_id, _json(detail or {}), _now()))


async def create_preview(user, round_id, binding_id):
    with get_db_connection() as conn:
        ctx, binding = _binding(conn, user, round_id, binding_id)
        if binding["state"] != "active": raise HTTPException(409, "绑定已暂停。")
        access = _account_access(conn, user["id"], binding["external_account_key"], binding["credential_generation"])
        if conn.execute("SELECT 1 FROM smart_attendance_sync_runs r JOIN smart_attendance_write_bindings b ON b.id=r.binding_id WHERE b.external_account_key=? AND r.state='outcome_unknown' LIMIT 1",(binding["external_account_key"],)).fetchone():
            raise HTTPException(409,"该远端账号仍有结果未知的旧请求，请先核对，不能开始新的同步运行。")
        fingerprint, targets = context_fingerprint(ctx), _targets(ctx)
    snapshot = await _snapshot(access, json.loads(binding["source_json"]), binding["remote_checkin_id"])
    _validate_session(ctx, snapshot)
    comparisons = compare_students(targets, snapshot["students"], snapshot["students"])
    with get_db_connection() as conn:
        current, fresh = _binding(conn, user, round_id, binding_id, lock=True)
        if context_fingerprint(current) != fingerprint or fresh["revision"] != binding["revision"]:
            raise HTTPException(409, "本地结果或绑定已变化，请重新预览。")
        _account_access(conn, user["id"], binding["external_account_key"], access["credential_generation"])
        # Expired previews remain auditable, but cannot permanently fill the
        # bounded live-preview allowance.
        conn.execute("UPDATE smart_attendance_sync_runs SET state='superseded',message=?,revision=revision+1,updated_at=? WHERE binding_id=? AND state='preview' AND created_at<?",
                     ("预览超过 30 分钟有效期，请重新读取。",_now(),binding_id,(datetime.now()-timedelta(minutes=30)).isoformat(timespec="microseconds")))
        pending = conn.execute("SELECT COUNT(*) FROM smart_attendance_sync_runs WHERE binding_id=? AND state='preview'", (binding_id,)).fetchone()[0]
        if pending >= 20: raise HTTPException(429, "未处理预览过多，请先处理已有核对任务。")
        run_id, now = _id(), _now()
        conn.execute("""INSERT INTO smart_attendance_sync_runs(id,binding_id,owner_teacher_id,local_round_id,
            local_revision,context_fingerprint,credential_generation,binding_revision,target_json,baseline_json,state,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,'preview',?,?)""", (run_id,binding_id,user["id"],str(round_id),ctx["revision"],fingerprint,
                access["credential_generation"],binding["revision"],_json(targets),_json(snapshot),now,now))
        for item in comparisons:
            conn.execute("""INSERT INTO smart_attendance_sync_operations(id,run_id,student_id,student_number,target_revision,
                baseline_status,target_status,remote_status,state,reason,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (_id(),run_id,item["student_id"],item["student_number"],item["revision"],item["baseline_status"],item["target_status"],item["remote_status"],item["state"],item["reason"],now))
        _event(conn,run_id,"preview",user["id"],{"snapshot_fingerprint":_hash(snapshot)})
        conn.commit()
    return get_run(user, round_id, run_id)


def _run(conn, user, round_id, run_id):
    ctx = load_read_context(conn, user, round_id)
    row = conn.execute("SELECT * FROM smart_attendance_sync_runs WHERE id=? AND owner_teacher_id=? AND local_round_id=?",
                       (str(run_id),int(user["id"]),str(round_id))).fetchone()
    if not row: raise HTTPException(404, "同步任务不存在或无访问权限。")
    key = str(row['request_key'] or '')
    return ctx, {**dict(row),"mode":"auto_sync" if key.startswith("auto:") else "student_push" if key.startswith('student:') else "teacher_handoff"}


def _require_handoff(run):
    if run.get('mode') == 'student_push':
        raise HTTPException(409,'此任务属于单学生更正，请从该学生卡片查看或回读原推送结果。')
    if run.get("mode") == "auto_sync":
        raise HTTPException(409,"此任务属于本轮自动推送，请在自动推送区域查看、暂停或重试，保留逐人提交记录。")


def get_run(user, round_id, run_id):
    with get_db_connection() as conn:
        _, run = _run(conn,user,round_id,run_id)
        public = {key:run[key] for key in ("id","binding_id","state","revision","message","verified_at","created_at","updated_at","mode")}
        public["operations"] = [dict(row) for row in conn.execute("SELECT student_id,student_number,baseline_status,target_status,remote_status,state,reason FROM smart_attendance_sync_operations WHERE run_id=? ORDER BY student_id",(run_id,)).fetchall()]
        public.update(capabilities=capabilities(), confirmation_scope="student_status_readback_only", full_remote_round_confirmed=False)
        return public


def enqueue_readback(conn, user, round_id, preview_id, request_key):
    from .scheduled_task_service import schedule_task
    ctx, run = _run(conn,user,round_id,preview_id)
    _require_handoff(run)
    key = str(request_key or "").strip()
    if not 1 <= len(key) <= 160: raise HTTPException(422,"请提供有效幂等请求标识。")
    if key.startswith(("auto:", "student:")):raise HTTPException(422,"该请求标识前缀保留给推送任务。")
    prior = conn.execute("SELECT id FROM smart_attendance_sync_runs WHERE owner_teacher_id=? AND request_key=?",(user["id"],key)).fetchone()
    if prior:
        if str(prior["id"]) != str(preview_id): raise HTTPException(409,"该请求标识已用于另一预览。")
        return {"id":run["id"],"state":run["state"]}
    _, binding = _binding(conn,user,round_id,run["binding_id"],lock=True)
    ctx,run=_run(conn,user,round_id,preview_id)
    _account_access(conn,user["id"],binding["external_account_key"],run["credential_generation"])
    if run["state"] != "preview" or binding["state"] != "active" or binding["revision"] != run["binding_revision"] or context_fingerprint(ctx) != run["context_fingerprint"]:
        raise HTTPException(409,"预览已过期或本地结果变化，请重新预览。")
    if datetime.fromisoformat(run["created_at"]) < datetime.now()-timedelta(minutes=30): raise HTTPException(409,"预览已过期，请重新读取。")
    conn.execute("SAVEPOINT smart_readback_request")
    try:
        cursor=conn.execute("UPDATE smart_attendance_sync_runs SET request_key=?,state='pending',revision=revision+1,updated_at=? WHERE id=? AND state='preview'",(key,_now(),preview_id))
    except Exception as exc:
        conn.execute("ROLLBACK TO SAVEPOINT smart_readback_request")
        conn.execute("RELEASE SAVEPOINT smart_readback_request")
        if any(cls.__name__ in {"IntegrityError","UniqueViolation"} for cls in type(exc).__mro__):
            raise HTTPException(409,"该请求标识已由另一预览使用，请刷新。") from None
        raise
    conn.execute("RELEASE SAVEPOINT smart_readback_request")
    if cursor.rowcount!=1:raise HTTPException(409,"该预览已被其他请求使用，请刷新。")
    schedule_task(conn,task_kind=TASK_KIND,run_at=_now(),payload={"run_id":preview_id,"round_id":str(round_id),"teacher_id":int(user["id"])},dedupe_key="smart-attendance-readback:"+str(preview_id),replace=False,owner_role="teacher",owner_user_pk=int(user["id"]),title="智慧课堂点名回读核对")
    _event(conn,preview_id,"readback_requested",user["id"])
    return {"id":preview_id,"state":"pending"}


def _unknown_submission(conn, run):
    return run["state"] == "outcome_unknown" or bool(conn.execute(
        "SELECT 1 FROM smart_attendance_sync_operations WHERE run_id=? AND state='outcome_unknown' LIMIT 1",
        (run["id"],)).fetchone())


def _historical(ctx):
    return not ctx.get("is_current", True) or str(ctx.get("state", "")).lower() in {"voided", "void", "cancelled", "canceled"}


def _readback_failure(user, round_id, run_id, revision):
    """Expose exhausted retries without losing a possibly submitted action.

    Never store raw remote exceptions: they can contain URLs, headers or
    personal data. Old verified_at and per-student facts remain available.
    """
    with get_db_connection() as conn:
        _, run = _run(conn, user, round_id, run_id)
        if run["revision"] != revision or run["state"] in {"paused", "superseded"}:
            return
        state = "outcome_unknown" if _unknown_submission(conn, run) else "failed"
        conn.execute("UPDATE smart_attendance_sync_runs SET state=?,message=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                     (state,"学校回读未完成，保留已有事实；可重试核对，未发送新的改签请求。",_now(),run_id,revision))
        _event(conn,run_id,"readback_failed",user["id"],{"code":"school_read_unavailable"})
        conn.commit()


async def reconcile_run(user, round_id, run_id):
    with get_db_connection() as conn:
        ctx, run = _run(conn,user,round_id,run_id)
        _require_handoff(run)
        if run["state"] in {"paused","superseded"}: return {"id":run_id,"state":run["state"]}
        unknown = _unknown_submission(conn,run)
        historical = _historical(ctx)
        if historical and not unknown:
            conn.execute("UPDATE smart_attendance_sync_runs SET state='paused',message=?,revision=revision+1,updated_at=? WHERE id=?",("本地点名已作废；远端事实保留，未发送改签请求。",_now(),run_id));conn.commit()
            return {"id":run_id,"state":"paused"}
        _, binding = _binding(conn,user,round_id,run["binding_id"])
        if not unknown and context_fingerprint(ctx) != run["context_fingerprint"]:
            conn.execute("UPDATE smart_attendance_sync_runs SET state='superseded',revision=revision+1,updated_at=? WHERE id=?",(_now(),run_id));conn.commit()
            return {"id":run_id,"state":"superseded"}
        try: access = _account_access(conn,user["id"],binding["external_account_key"],run["credential_generation"])
        except HTTPException:
            state="outcome_unknown" if unknown else "needs_teacher_reauth"
            conn.execute("UPDATE smart_attendance_sync_runs SET state=?,message=?,revision=revision+1,updated_at=? WHERE id=?",(state,"请使用原远端账号重新验证并确认绑定后核对；旧请求结果保留。",_now(),run_id));conn.commit()
            return {"id":run_id,"state":state}
    try:
        snapshot = await _snapshot(access,json.loads(binding["source_json"]),binding["remote_checkin_id"])
        _validate_session(ctx,snapshot)
    except Exception:
        _readback_failure(user,round_id,run_id,run["revision"])
        raise
    items = compare_students(json.loads(run["target_json"]),json.loads(run["baseline_json"])["students"],snapshot["students"])
    state = "confirmed" if all(row["state"]=="confirmed" for row in items) else "outcome_unknown" if unknown else "conflict" if any(row["state"]=="conflict" for row in items) else "needs_teacher_action"
    with get_db_connection() as conn:
        # Read mutable facts only after acquiring the shared parent lock.
        current, fresh_binding = _binding(conn,user,round_id,run["binding_id"],lock=True)
        _, fresh = _run(conn,user,round_id,run_id)
        if fresh["state"]=="paused": return {"id":run_id,"state":"paused"}
        if fresh["revision"]!=run["revision"]:raise HTTPException(409,"该任务已被另一核对请求更新，请刷新。")
        _account_access(conn,user["id"],binding["external_account_key"],run["credential_generation"])
        if not unknown and (context_fingerprint(current)!=run["context_fingerprint"] or fresh_binding["revision"]!=run["binding_revision"]):
            conn.execute("UPDATE smart_attendance_sync_runs SET state='superseded',revision=revision+1,updated_at=? WHERE id=?",(_now(),run_id));conn.commit()
            return {"id":run_id,"state":"superseded"}
        now = _now()
        for item in items:
            conn.execute("UPDATE smart_attendance_sync_operations SET remote_status=?,state=?,reason=?,updated_at=? WHERE run_id=? AND student_id=?",
                         (item["remote_status"],item["state"],item["reason"],now,run_id,item["student_id"]))
        message = "学生状态回读一致；学校端开始及结束仍由教师官方端确认。" if state=="confirmed" else "请在学校官方端处理差异后重新回读；本平台未发送改签请求。"
        if unknown and state!="confirmed":message="旧请求结果仍未知；保留原目标和远端事实，只能继续回读核对，不能重发。"
        conn.execute("UPDATE smart_attendance_sync_runs SET state=?,message=?,verified_at=?,revision=revision+1,updated_at=? WHERE id=?",(state,message,now,now,run_id))
        _event(conn,run_id,"readback",user["id"],{"state":state,"snapshot_fingerprint":_hash(snapshot)})
        conn.commit()
    return get_run(user,round_id,run_id)


def pause_run(conn,user,round_id,run_id,expected_revision):
    _,run=_run(conn,user,round_id,run_id)
    _require_handoff(run)
    if _unknown_submission(conn,run):raise HTTPException(409,"旧请求结果未知，不能用暂停隐藏未核实的提交；请继续回读核对。")
    if int(run["revision"])!=int(expected_revision):raise HTTPException(409,"同步任务已变化，请刷新后重试。")
    cursor=conn.execute("UPDATE smart_attendance_sync_runs SET state='paused',revision=revision+1,updated_at=? WHERE id=? AND revision=?",(_now(),run_id,expected_revision))
    if cursor.rowcount!=1:raise HTTPException(409,"同步任务已变化，请刷新后重试。")
    _event(conn,run_id,"paused",user["id"])
    return {"id":run_id,"state":"paused"}


def reauthorize_binding(conn,user,round_id,binding_id,expected_revision,*,auto_sync=False):
    """Same external account only; never relabel old work as unsent."""
    _,binding=_binding(conn,user,round_id,binding_id,lock=True)
    from .smart_classroom_student_push_service import assert_no_active_push
    assert_no_active_push(conn, round_id)
    if not auto_sync and conn.execute("SELECT 1 FROM smart_attendance_auto_sync WHERE binding_id=? LIMIT 1",(binding_id,)).fetchone():
        raise HTTPException(409,"此绑定由自动推送管理，请使用自动推送区域的重新授权入口。")
    if binding["revision"]!=expected_revision:raise HTTPException(409,"绑定已变化，请刷新。")
    access=_account_access(conn,user["id"],binding["external_account_key"])
    cursor=conn.execute("UPDATE smart_attendance_write_bindings SET credential_id=?,credential_generation=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                        (access["credential_id"],access["credential_generation"],_now(),binding_id,expected_revision))
    if cursor.rowcount!=1:raise HTTPException(409,"绑定已变化，请刷新。")
    # New previews are required for normal tasks. An unknown submitted action
    # can only be reconciled, never restarted or silently rebound.
    conn.execute("UPDATE smart_attendance_sync_runs SET credential_generation=?,binding_revision=?,state=CASE WHEN state='outcome_unknown' THEN 'outcome_unknown' ELSE 'pending' END,revision=revision+1,updated_at=? WHERE binding_id=? AND state IN ('outcome_unknown','needs_teacher_reauth') AND (request_key IS NULL OR request_key NOT LIKE 'student:%')",
                 (access["credential_generation"],expected_revision+1,_now(),binding_id))
    return {"id":binding_id,"revision":expected_revision+1,"state":binding["state"]}


def list_runs(conn,user,round_id):
    load_read_context(conn,user,round_id)
    return [dict(row) for row in conn.execute("SELECT id,binding_id,state,revision,message,verified_at,created_at,updated_at,"
        "CASE WHEN request_key LIKE 'auto:%' THEN 'auto_sync' WHEN request_key LIKE 'student:%' THEN 'student_push' ELSE 'teacher_handoff' END AS mode "
        "FROM smart_attendance_sync_runs WHERE owner_teacher_id=? AND local_round_id=? ORDER BY created_at DESC LIMIT 50",(user["id"],str(round_id))).fetchall()]


def list_bindings(conn,user,round_id):
    """Safe reload projection; credential fences and signed options stay server-side."""
    load_read_context(conn,user,round_id)
    rows=conn.execute("SELECT id,revision,state,remote_checkin_id,source_json,created_at,updated_at FROM smart_attendance_write_bindings WHERE owner_teacher_id=? AND local_round_id=? ORDER BY created_at DESC LIMIT 50",
                      (user["id"],str(round_id))).fetchall()
    result=[]
    for row in rows:
        source=json.loads(row["source_json"])
        result.append({**{key:row[key] for key in ("id","revision","state","remote_checkin_id","created_at","updated_at")},
                       **{key:source.get(key) for key in ("course_name","course_code","teaching_class_name","academic_year","academic_term")}})
    return result
