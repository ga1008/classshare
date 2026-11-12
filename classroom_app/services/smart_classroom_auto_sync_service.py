"""Explicit per-round consent, durable bounded writes, and exact school readback.

This is an outbox over the existing binding/run/operation ledger. A network
request never holds a database transaction. Every potentially sent mutation is
durably marked unknown first; restart always reads before deciding what to do.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import math
import time

from fastapi import HTTPException

from ..database import get_db_connection
from . import smart_classroom_write_service as ledger
from . import smart_classroom_miniapp_adapter as mini
from .attendance_report_service import _decode_option, sign_attendance_source_option
from .smart_classroom_integration_service import open_authenticated_smart_classroom_client

TASK_KIND = "smart_attendance_auto_sync"
BATCH_SIZE = 20
_now, _json, _id = ledger._now, ledger._json, ledger._id
HOLD_STATES = {"paused", "void", "outcome_unknown", "needs_teacher_reauth", "captcha_required", "credential_invalid", "awaiting_push", "conflict", "failed"}


def _fingerprint(ctx, plan):
    return ledger.result_fingerprint(ctx) if json.loads(plan["intent_json"]).get("manual_dispatch") else ledger.context_fingerprint(ctx)


def _lifecycle_unknown(plan):
    intent = json.loads(plan["intent_json"])
    return intent.get("operation") in {"start", "stop"} and not intent.get("verified")


def _plan(conn, user, round_id, *, lock=False):
    ctx = ledger.load_read_context(conn, user, round_id, lock=lock)
    suffix = " FOR UPDATE" if lock and ledger.get_configured_db_engine() == "postgres" else ""
    row = conn.execute("SELECT * FROM smart_attendance_auto_sync WHERE local_round_id=? AND owner_teacher_id=?" + suffix,
                       (str(round_id), int(user["id"]))).fetchone()
    return ctx, dict(row) if row else None


def _target(source, slot):
    return {**{k: source.get(k, "") for k in ("course_name", "course_code", "teaching_class_name", "academic_year", "academic_term")},
            **{k: slot.get(k) for k in ("session_date", "week", "day_of_week", "section")},
            "section_text": "、".join(str(n) for n in slot.get("sections", [])) + "节"}


def get_auto_sync(conn, user, round_id):
    _, plan = _plan(conn, user, round_id)
    public = None
    if plan:
        public = {k: plan[k] for k in ("revision", "state", "message", "remote_checkin_id", "binding_id", "updated_at")}
        public.update(enabled=bool(plan["enabled"]), target=_target(json.loads(plan["source_json"]), json.loads(plan["slot_json"])), last_run=None)
        intent = json.loads(plan["intent_json"])
        public["comparison"] = intent.get("comparison")
        if plan["last_run_id"]:
            run = conn.execute("SELECT id,binding_id,state,revision,message,verified_at,created_at,updated_at FROM smart_attendance_sync_runs WHERE id=?", (plan["last_run_id"],)).fetchone()
            if run:
                public["last_run"] = dict(run)
                public["last_run"]["operations"] = [dict(r) for r in conn.execute("SELECT student_id,student_number,baseline_status,target_status,remote_status,state,reason FROM smart_attendance_sync_operations WHERE run_id=? ORDER BY student_id", (run["id"],)).fetchall()]
                public["last_run"].update(confirmation_scope="matched_local_students_only", full_remote_round_confirmed=False)
        operations = (public.get("last_run") or {}).get("operations", [])
        completed = sum(o["state"] not in {"pending", "outcome_unknown"} for o in operations)
        terminal = plan["state"] in {"confirmed", "partial", "conflict"}
        percent = 100 if terminal else min(95, 30 + int(60 * completed / max(1, len(operations)))) if operations else {"reading": 10, "comparing": 25, "running": 30}.get(plan["state"], 0)
        public["progress"] = {"percent": percent, "completed": completed, "total": len(operations), "stage": plan["state"]}
    return {"plan": public, "capabilities": ledger.capabilities()}


def _validate_context_source(ctx, source):
    if (str(ctx.get("year")), str(ctx.get("term"))) != (str(source.get("academic_year")), str(source.get("academic_term"))):
        raise HTTPException(409, "所选授课班学期与本地课次不一致。")
    alias_verified = source.get("course_match_evidence") in {"name_and_roster", "verified_course_alignment"} and source.get("local_course_code") == ctx.get("course_code")
    if source.get("course_match_evidence") == "verified_course_alignment":
        codes = source.get("local_course_codes") or [source.get("local_course_code")]
        alias_verified = ctx.get("course_code") in codes and source.get("local_course_name") == ctx.get("course_name")
    if (not ctx.get("course_code") and not alias_verified) or (str(ctx.get("course_code", "")).upper() != str(source.get("course_code", "")).upper() and not alias_verified):
        raise HTTPException(409, "所选课程代码与本地课次不一致。")
    if not ctx.get("week_index") or not mini.sections(ctx.get("section_text")):
        raise HTTPException(409, "本地课次缺少可靠的教学周或节次，请先维护课表。")
    try:
        day = date.fromisoformat(str(ctx["session_date"]))
    except (ValueError, KeyError):
        raise HTTPException(409, "本地课次缺少准确上课日期。") from None
    if ctx.get("weekday") is not None and int(ctx["weekday"]) != day.weekday():
        raise HTTPException(409, "本地课次日期和星期不一致，请先核对课表。")


def _validate_slot_context(ctx, source, slot):
    _validate_context_source(ctx, source)
    # A teacher may explicitly bind a rescheduled school lesson. Its remote
    # slot stays intact; the sealed local identity must still match this round.
    # _assert_alignment separately verifies the live server-owned binding.
    identity = slot
    if source.get("course_match_evidence") == "verified_course_alignment":
        identity = slot.get("local_session_identity") or {}
        if slot.get("alignment_method") not in {"exact_slot", "manual"}:
            raise HTTPException(409, "课次对齐依据无效，请重新对齐。")
    if (str(slot.get("local_round_id")), str(slot.get("source_id")), str(identity.get("session_date")), int(identity.get("week", 0)),
            int(identity.get("day_of_week", 0)), tuple(identity.get("sections", []))) != (
            str(ctx["round_id"]), str(source["remote_schedule_id"]), str(ctx["session_date"]), int(ctx["week_index"]),
            date.fromisoformat(ctx["session_date"]).isoweekday(), mini.sections(ctx["section_text"])):
        raise HTTPException(409, "真实排课选项已过期或不属于本地课次。")
    if slot.get("external_account_key") != source["external_account_key"]:
        raise HTTPException(409, "真实排课选项属于其他智慧课堂账号。")


def _assert_alignment(conn, user, plan):
    source, slot = json.loads(plan["source_json"]), json.loads(plan["slot_json"])
    if source.get("course_match_evidence") != "verified_course_alignment":
        return
    from .smart_classroom_workflow_service import assert_current_alignment
    try:
        assert_current_alignment(conn, user, plan["class_offering_id"], plan["session_id"], source, slot,
                                 json.loads(plan["intent_json"]).get("binding_revision"))
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        message = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
        raise HTTPException(409, {"code": "alignment_changed", "message": message}) from exc


def _guard_remote_roster(user, plan, ctx, remote):
    """School membership controls which local records may be sent, never vice versa."""
    source = json.loads(plan["source_json"])
    if source.get("course_match_evidence") != "verified_course_alignment":
        return
    from .smart_classroom_alignment import roster_evidence
    evidence = roster_evidence((s["student_number"] for s in ctx["students"]), remote)
    manual_roster = (source.get("course_alignment_method") == "manual" and evidence["majority"] and
                     source.get("remote_roster_fingerprint") == ledger._hash(sorted(remote)))
    if evidence["accepted"] or manual_roster:
        return
    message = "学校名单已变化，双方共有学生或人数差异不再符合已确认范围。请重新对齐课次；本地记录完整保留。"
    with get_db_connection() as conn:
        _, fresh = _plan(conn, user, plan["local_round_id"], lock=True)
        if fresh and fresh["revision"] == plan["revision"]:
            changed = conn.execute("UPDATE smart_attendance_course_links SET state='stale',revision=revision+1,message=?,updated_at=? "
                "WHERE class_offering_id=? AND owner_teacher_id=? AND revision=? AND state='matched'",
                (message, _now(), plan["class_offering_id"], user["id"], source.get("course_link_revision")))
            if changed.rowcount:
                conn.execute("UPDATE smart_attendance_session_links SET state='stale',revision=revision+1,message=?,updated_at=? "
                    "WHERE class_offering_id=? AND owner_teacher_id=? AND course_link_revision=?",
                    (message, _now(), plan["class_offering_id"], user["id"], source.get("course_link_revision")))
        conn.commit()
    raise HTTPException(409, {"code": "alignment_changed", "message": message})


def _schedule_rows(payload):
    """Only objects returned by the official schedule list can be selected."""
    result, seen = [], set()
    def visit(value, depth=0):
        if depth > 6:
            return
        if isinstance(value, list):
            if len(value) > 1000:
                raise HTTPException(409, "远端课表超出完整核对范围。")
            for item in value:
                visit(item, depth + 1)
        elif isinstance(value, dict):
            if value.get("id") and value.get("courseCode") and value.get("sections"):
                key = str(value["id"])
                if key not in seen:
                    result.append(value); seen.add(key)
            else:
                for item in value.values():
                    if isinstance(item, (dict, list)):
                        visit(item, depth + 1)
    visit(payload)
    return result


async def list_slot_options(user, round_id, source_token):
    source = _decode_option(source_token, int(user["id"]))
    with get_db_connection() as conn:
        ctx = ledger.load_local_context(conn, user, round_id)
        _validate_context_source(ctx, source)
        access = ledger._account_access(conn, user["id"], source["external_account_key"])
    async with ledger._account_lease(source["external_account_key"]):
        # The representative group ID cannot be used as a timetable row ID.
        async with open_authenticated_smart_classroom_client(access) as (pc, _, _login):
            groups = await ledger.source_adapter.fetch_source_schedules(pc, source["academic_year"], source["academic_term"])
        groups = [g for g in groups if str(g["id"]) == str(source["remote_schedule_id"])
                  and str(g["courseId"]) == str(source["course_code"]) and str(g["claId"]) == str(source["remote_class_id"])]
        if len(groups) != 1:
            raise HTTPException(409, "原授课班已变化，请重新读取来源。")
        group = groups[0]
        async with open_authenticated_smart_classroom_client(access, api="mini") as (client, _, _login):
            payload = await mini.list_schedules(client)
            if not isinstance(payload, dict) or (str(payload.get("year")), str(payload.get("semester"))) != (str(source["academic_year"]), str(source["academic_term"])):
                raise HTTPException(409, "学校小程序当前学期与本地课次不一致。")
            options = []
            for row in _schedule_rows(payload):
                if str(row.get("courseCode")) != str(source["course_code"]):
                    continue
                if mini.sections(row.get("sections")) != mini.sections(ctx["section_text"]):
                    continue
                if str(row.get("xqj")) != str(date.fromisoformat(ctx["session_date"]).isoweekday()):
                    continue
                if not isinstance(row.get("weeks"), list) or int(ctx["week_index"]) not in [int(w) for w in row["weeks"]]:
                    continue
                slot = {"kind": "smart_attendance_slot", "local_round_id": str(round_id), "source_id": source["remote_schedule_id"],
                        "external_account_key": source["external_account_key"], "schedule_id": str(row["id"]),
                        "session_date": ctx["session_date"], "week": int(ctx["week_index"]),
                        "day_of_week": date.fromisoformat(ctx["session_date"]).isoweekday(),
                        "sections": list(mini.sections(ctx["section_text"])), "section": min(mini.sections(ctx["section_text"]))}
                snap = await mini.read_slot(client, slot, source)
                if not snap["record"] and str(row["id"]) != str(source["remote_schedule_id"]):
                    # Group source IDs may identify a representative timetable
                    # row. A different empty row needs a verified group mapping.
                    history = await mini.read_history(client, str(row["id"]))
                    if not any(str(r.get("teacherScheduleId")) == str(row["id"]) and
                               str(r.get("claId")) == str(source["remote_class_id"]) and
                               str(r.get("courseId")) == str(source["course_code"]) for r in history):
                        continue
                slot["remote_checkin_id"] = str(snap["record"]["id"]) if snap["record"] else None
                target = _target(source, slot)
                options.append({"slot_token": sign_attendance_source_option(int(user["id"]), slot),
                    "label": f"{target['session_date']} · 第 {slot['week']} 周 · {ctx['section_text']} 节 · {source.get('teaching_class_name', '')}",
                    "target": target, "remote_checkin_id": slot["remote_checkin_id"],
                    "is_stopped": bool(snap["record"] and snap["record"].get("isStop")),
                    "can_create": bool(not snap["record"] and snap["can_checkin"] and mini.LIFECYCLE_VERIFIED and not ctx.get("closed"))})
    return {"options": options}


def _location(value):
    try:
        result = {k: float(value[k]) for k in ("latitude", "longitude", "captured_at_ms")}
        valid = all(math.isfinite(v) for v in result.values()) and -90 <= result["latitude"] <= 90 and -180 <= result["longitude"] <= 180
        age = time.time() * 1000 - result["captured_at_ms"]
        if not valid or not -10000 <= age <= 300000:
            raise ValueError()
        return result
    except (TypeError, KeyError, ValueError, OverflowError):
        raise HTTPException(422, "新建学校点名需要教师本次点击时获取的位置，位置须在五分钟内。") from None


def _grant(conn, access, round_id):
    from .smart_classroom_session_service import grant_background_login
    grant_background_login(conn, access, "attendance:" + str(round_id), expires_at_ms=int(time.time() * 1000) + 12 * 3600 * 1000)


async def configure_auto_sync(user, round_id, payload):
    if payload.get("enabled") is not True:
        raise HTTPException(422, "停用自动同步请使用暂停操作，避免覆盖原绑定记录。")
    if payload.get("acknowledge_session_takeover") is not True:
        raise HTTPException(422, "请确认由服务器保持本轮智慧课堂会话，学校小程序可能需要重新登录。")
    source = _decode_option(payload.get("source_token", ""), int(user["id"]))
    slot = _decode_option(payload.get("slot_token", ""), int(user["id"]))
    key = str(payload.get("idempotency_key") or "").strip()
    if not 1 <= len(key) <= 160 or slot.get("kind") != "smart_attendance_slot":
        raise HTTPException(422, "同步配置请求无效。")
    with get_db_connection() as conn:
        ctx, prior = _plan(conn, user, round_id, lock=True)
        if prior and prior["request_key"] == key:
            if prior["source_json"] != _json(source) or prior["slot_json"] != _json(slot):
                raise HTTPException(409, "同一请求标识不能用于不同课程或排课。")
            return get_auto_sync(conn, user, round_id)
        if prior and prior["revision"] != payload.get("expected_revision"):
            raise HTTPException(409, "同步配置已变化，请刷新。")
        if not ctx.get("is_current", True) or ctx["state"] == "void":
            raise HTTPException(409, "已作废或历史轮次不能开启自动同步。")
        _validate_slot_context(ctx, source, slot)
        access = ledger._account_access(conn, user["id"], source["external_account_key"])
        creating = not slot.get("remote_checkin_id")
        if creating and (not payload.get("allow_create") or ctx.get("closed") or not mini.LIFECYCLE_VERIFIED):
            raise HTTPException(409, "当前只能选择学校官方端已存在的正确点名。")
        location = _location(payload.get("location")) if creating else None
        if prior and (prior["remote_checkin_id"] or json.loads(prior["intent_json"]).get("operation")):
            if prior["source_json"] != _json(source) or json.loads(prior["slot_json"])["schedule_id"] != slot["schedule_id"]:
                raise HTTPException(409, "已执行的学校同步不能改绑其他课程；请保留原核对记录。")
            if prior["remote_checkin_id"] and str(prior["remote_checkin_id"]) != str(slot.get("remote_checkin_id")):
                raise HTTPException(409, "不能将已有同步记录改绑新的学校点名。")
            if prior["last_run_id"] and ledger._unknown_submission(conn, dict(conn.execute("SELECT * FROM smart_attendance_sync_runs WHERE id=?", (prior["last_run_id"],)).fetchone())):
                raise HTTPException(409, "旧请求结果未知，请先回读核对，不能覆盖同步配置。")
        now = _now()
        conn.execute("""INSERT INTO smart_attendance_auto_sync(local_round_id,owner_teacher_id,class_offering_id,session_id,
            external_account_key,credential_generation,source_json,slot_json,remote_checkin_id,enabled,state,request_key,intent_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,1,'pending',?,?,?,?) ON CONFLICT(local_round_id) DO UPDATE SET
            credential_generation=excluded.credential_generation,source_json=excluded.source_json,slot_json=excluded.slot_json,
            remote_checkin_id=excluded.remote_checkin_id,enabled=1,state='pending',request_key=excluded.request_key,
            intent_json=excluded.intent_json,revision=smart_attendance_auto_sync.revision+1,message='',updated_at=excluded.updated_at""",
            (str(round_id), int(user["id"]), ctx["class_offering_id"], ctx["session_id"], source["external_account_key"],
             access["credential_generation"], _json(source), _json(slot), slot.get("remote_checkin_id"), key,
             _json({"allow_create": creating, "location": location}), now, now))
        _grant(conn, access, round_id)
        schedule_due_auto_sync(conn, round_id, force=True)
        conn.commit()
        return get_auto_sync(conn, user, round_id)


def _change_plan(conn, user, round_id, revision, *, reauthorize=False, pause=False):
    ctx, plan = _plan(conn, user, round_id, lock=True)
    if not plan or plan["revision"] != revision:
        raise HTTPException(409, "同步配置已变化，请刷新。")
    if pause:
        unknown = _lifecycle_unknown(plan) or bool(plan["last_run_id"] and conn.execute("SELECT 1 FROM smart_attendance_sync_operations WHERE run_id=? AND state='outcome_unknown' LIMIT 1", (plan["last_run_id"],)).fetchone())
        conn.execute("UPDATE smart_attendance_auto_sync SET enabled=0,state=?,revision=revision+1,message=?,updated_at=? WHERE local_round_id=?",
                     ("outcome_unknown" if unknown else "paused", "已停止发送；已提交但未知的请求仍保留核对记录。", _now(), str(round_id)))
        if unknown:
            schedule_due_auto_sync(conn, round_id, force=True)
    else:
        if not ctx.get("is_current", True) or ctx["state"] == "void":
            raise HTTPException(409, "历史或作废轮次只能查看原核对记录。")
        access = ledger._account_access(conn, user["id"], plan["external_account_key"], None if reauthorize else plan["credential_generation"])
        if reauthorize:
            _grant(conn, access, round_id)
            if plan["binding_id"]:
                binding = conn.execute("SELECT * FROM smart_attendance_write_bindings WHERE id=?", (plan["binding_id"],)).fetchone()
                ledger.reauthorize_binding(conn, user, round_id, plan["binding_id"], binding["revision"], auto_sync=True)
        conn.execute("UPDATE smart_attendance_auto_sync SET enabled=1,state='pending',credential_generation=?,revision=revision+1,message='',updated_at=? WHERE local_round_id=?",
                     (access["credential_generation"], _now(), str(round_id)))
        schedule_due_auto_sync(conn, round_id, force=True)
    return get_auto_sync(conn, user, round_id)


def pause_auto_sync(conn, user, round_id, expected_revision):
    return _change_plan(conn, user, round_id, expected_revision, pause=True)


def retry_auto_sync(conn, user, round_id, expected_revision):
    # Unknown writes are read-only on retry; never turn them back into unsent.
    _, plan = _plan(conn, user, round_id, lock=True)
    if plan and plan["revision"] == expected_revision:
        intent = json.loads(plan["intent_json"])
        intent["busy_retries"] = 0
        conn.execute("UPDATE smart_attendance_auto_sync SET intent_json=? WHERE local_round_id=?", (_json(intent), str(round_id)))
    return _change_plan(conn, user, round_id, expected_revision)


async def reauthorize_auto_sync(user, round_id, expected_revision):
    """An explicit teacher action may replace a school single-session token.

    Validate ownership/revision before login, serialize with all account work,
    then fence the revision and credentials again before publishing consent.
    No database transaction spans the school request.
    """
    with get_db_connection() as conn:
        ctx, plan = _plan(conn, user, round_id)
        if not plan or plan["revision"] != expected_revision:
            raise HTTPException(409, "同步配置已变化，请刷新。")
        if not ctx.get("is_current", True) or ctx["state"] == "void":
            raise HTTPException(409, "历史或作废轮次只能查看原核对记录。")
        access = ledger._account_access(conn, user["id"], plan["external_account_key"])
    async with ledger._account_lease(plan["external_account_key"]):
        with get_db_connection() as conn:
            current_ctx, current = _plan(conn, user, round_id)
            if (not current or current["revision"] != expected_revision
                    or not current_ctx.get("is_current", True) or current_ctx["state"] == "void"):
                raise HTTPException(409, "同步配置已变化，请刷新。")
        async with open_authenticated_smart_classroom_client(access, api="mini", force_reauth=True):
            pass
        with get_db_connection() as conn:
            ledger._account_access(conn, user["id"], plan["external_account_key"], access["credential_generation"])
            result = _change_plan(conn, user, round_id, expected_revision, reauthorize=True)
            conn.commit()
            return result


def schedule_due_auto_sync(conn, round_id, *, force=False, delay=0):
    """Cheap hook: same transaction as publish/correction/void, no network IO."""
    from .scheduled_task_service import schedule_task
    plan = conn.execute("SELECT * FROM smart_attendance_auto_sync WHERE local_round_id=?", (str(round_id),)).fetchone()
    if not plan:
        return
    local = conn.execute("SELECT state,is_current FROM login_attendance_rounds WHERE id=?", (str(round_id),)).fetchone()
    invalid = not local or not local["is_current"] or local["state"] == "void"
    unknown = _lifecycle_unknown(plan) or plan["state"] == "outcome_unknown" or bool(plan["last_run_id"] and conn.execute("SELECT 1 FROM smart_attendance_sync_operations WHERE run_id=? AND state='outcome_unknown' LIMIT 1", (plan["last_run_id"],)).fetchone())
    if invalid:
        conn.execute("UPDATE smart_attendance_auto_sync SET enabled=0,state=?,message=?,updated_at=? WHERE local_round_id=?",
                     ("outcome_unknown" if unknown else "void", "本地点名已作废或归档，停止发送；学校原记录保留。", _now(), str(round_id)))
        if not unknown:
            return
    if unknown and not force and not invalid:
        # The worker queues one recovery read; ordinary published-round ticks
        # must not keep polling a permanently ambiguous school operation.
        return
    changed_result = False
    intent = json.loads(plan["intent_json"])
    if intent.get("manual_dispatch") and not unknown and local and local["state"] == "published":
        ctx = ledger.load_local_context(conn, {"role": "teacher", "id": plan["owner_teacher_id"]}, str(round_id))
        if _fingerprint(ctx, plan) != intent.get("authorized_fingerprint"):
            conn.execute("UPDATE smart_attendance_auto_sync SET enabled=0,state='awaiting_push',message=?,updated_at=? WHERE local_round_id=?", ("本地结果已更新，请点击同步与推送以提交新版本。", _now(), str(round_id)))
            return
    if not force and not invalid and local["state"] == "published" and plan["last_context_fingerprint"]:
        ctx = ledger.load_local_context(conn, {"role": "teacher", "id": plan["owner_teacher_id"]}, str(round_id))
        changed_result = _fingerprint(ctx, plan) != plan["last_context_fingerprint"]
        if not changed_result and plan["state"] in {"confirmed", "partial", "conflict"}:
            return
    if not force and not invalid and ((plan["state"] == "waiting_local_end" and local["state"] in {"scheduled", "running"}) or
                                      (plan["state"] == "waiting_settlement" and local["state"] == "settling")):
        return
    held = plan["state"] in HOLD_STATES and not (plan["state"] == "conflict" and changed_result)
    if not unknown and (not plan["enabled"] or (held and not force)):
        return
    if not force and conn.execute("SELECT 1 FROM scheduled_tasks WHERE task_kind=? AND dedupe_key LIKE ? AND status IN ('pending','running') LIMIT 1",
                                  (TASK_KIND, f"smart-auto:{round_id}:%")).fetchone():
        return
    seq = int(plan["dispatch_seq"]) + 1
    conn.execute("UPDATE smart_attendance_auto_sync SET dispatch_seq=?,updated_at=? WHERE local_round_id=?", (seq, _now(), str(round_id)))
    schedule_task(conn, task_kind=TASK_KIND, run_at=datetime.now() + timedelta(seconds=delay),
                  payload={"round_id": str(round_id), "teacher_id": int(plan["owner_teacher_id"])},
                  dedupe_key=f"smart-auto:{round_id}:{seq}", replace=False, max_attempts=2,
                  owner_role="teacher", owner_user_pk=int(plan["owner_teacher_id"]), title="课堂点名同步智慧课堂")


sync_schedule_for_round = schedule_due_auto_sync


def _state(round_id, state, message="", *, expected_revision, **fields):
    allowed = {"remote_checkin_id", "binding_id", "last_run_id", "last_context_fingerprint", "intent_json"}
    if set(fields) - allowed:
        raise ValueError("Unexpected plan mutation")
    with get_db_connection() as conn:
        assignments = ",".join(k + "=?" for k in fields)
        changed = conn.execute("UPDATE smart_attendance_auto_sync SET state=?,message=?,updated_at=?" + ("," + assignments if assignments else "") + " WHERE local_round_id=? AND revision=? AND enabled=1",
                     (state, message, _now(), *fields.values(), str(round_id), expected_revision))
        if changed.rowcount != 1:
            raise HTTPException(409, "同步授权已变化，未继续发送学校请求。")
        conn.commit()


def _plan_state(plan, state, message="", **fields):
    _state(plan["local_round_id"], state, message, expected_revision=plan["revision"], **fields)
    plan.update(state=state, message=message, **fields)


class _LeasedClient:
    """Renew the durable account fence before every request, including reads."""
    def __init__(self, client, key, token):
        self.client, self.key, self.token = client, key, token

    async def post(self, *args, **kwargs):
        with get_db_connection() as conn:
            changed = conn.execute("UPDATE smart_attendance_account_leases SET lease_until=? WHERE external_account_key=? AND lease_token=? AND lease_until>?",
                ((datetime.now() + timedelta(seconds=120)).isoformat(timespec="microseconds"), self.key, self.token, _now()))
            conn.commit()
            if changed.rowcount != 1:
                raise HTTPException(409, "账号同步租约已过期，停止请求并保留回读记录。")
        return await self.client.post(*args, **kwargs)


def _fence(user, plan, *, sending=False, expected_fingerprint=None):
    with get_db_connection() as conn:
        ctx, fresh = _plan(conn, user, plan["local_round_id"], lock=True)
        ledger._account_access(conn, user["id"], plan["external_account_key"], plan["credential_generation"])
        if not fresh or fresh["revision"] != plan["revision"] or fresh["credential_generation"] != plan["credential_generation"]:
            raise HTTPException(409, "同步授权已变化，已停止新的发送。")
        if sending and (not fresh["enabled"] or not ctx.get("is_current", True) or ctx["state"] == "void"):
            raise HTTPException(409, "本轮同步已停用，已停止新的发送。")
        intent = json.loads(fresh["intent_json"])
        if sending and intent.get("manual_dispatch") and _fingerprint(ctx, fresh) != intent.get("authorized_fingerprint"):
            raise HTTPException(409, "本地结果已更新，请点击同步与推送以提交新版本。")
        if sending:
            _assert_alignment(conn, user, fresh)
        if expected_fingerprint and (ctx["state"] != "published" or _fingerprint(ctx, fresh) != expected_fingerprint):
            raise HTTPException(409, "本地点名结果已变化，旧版本停止发送。")
        return ctx, fresh


def _bind_plan(user, plan, snapshot):
    with get_db_connection() as conn:
        ctx, fresh = _plan(conn, user, plan["local_round_id"], lock=True)
        if fresh["revision"] != plan["revision"] or not fresh["enabled"]:
            raise HTTPException(409, "同步授权已变化。")
        existing = conn.execute("SELECT * FROM smart_attendance_write_bindings WHERE external_account_key=? AND remote_checkin_id=? AND state<>'released'",
                                (plan["external_account_key"], str(snapshot["record"]["id"]))).fetchone()
        if existing and (int(existing["owner_teacher_id"]) != int(user["id"]) or str(existing["local_round_id"]) != plan["local_round_id"]):
            raise HTTPException(409, "该学校点名已由其他本地轮次同步，不能重复接管。")
        if existing:
            binding_id = existing["id"]
            if existing["state"] != "active":
                raise HTTPException(409, "原远端绑定已暂停，请先核实。")
        else:
            access = ledger._account_access(conn, user["id"], plan["external_account_key"], plan["credential_generation"])
            binding_id, now = _id(), _now()
            conn.execute("""INSERT INTO smart_attendance_write_bindings(id,owner_teacher_id,class_offering_id,session_id,
                local_round_id,external_account_key,credential_id,credential_generation,source_json,remote_checkin_id,
                remote_snapshot_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (binding_id, user["id"], ctx["class_offering_id"], ctx["session_id"], plan["local_round_id"], plan["external_account_key"],
                 access["credential_id"], access["credential_generation"], plan["source_json"], str(snapshot["record"]["id"]), _json(snapshot), now, now))
        conn.execute("UPDATE smart_attendance_auto_sync SET binding_id=?,remote_checkin_id=?,updated_at=? WHERE local_round_id=?",
                     (binding_id, str(snapshot["record"]["id"]), _now(), plan["local_round_id"]))
        conn.commit()
        return binding_id


async def _prepare_remote(client, user, plan, ctx):
    source, slot = json.loads(plan["source_json"]), json.loads(plan["slot_json"])
    _validate_slot_context(ctx, source, slot)
    snap = await mini.read_slot(client, slot, source, plan["remote_checkin_id"])
    _guard_remote_roster(user, plan, ctx, snap["students"])
    original_record = dict(snap["record"] or {})
    dispatch_intent = json.loads(plan["intent_json"])
    if dispatch_intent.get("manual_dispatch"):
        if "comparison_record" not in dispatch_intent:
            dispatch_intent.update(comparison_record=original_record, new_remote_round=not bool(snap["record"]))
            _plan_state(plan, "reading", "正在读取学校本次课记录。", intent_json=_json(dispatch_intent))
        original_record = dispatch_intent["comparison_record"]
    if dispatch_intent.get("manual_dispatch") and snap["record"]:
        from .smart_classroom_workflow_service import persist_remote_snapshot
        _plan_state(plan, "reading", "正在同步学校本次课点名记录到本地。")
        with get_db_connection() as conn:
            persist_remote_snapshot(conn, user, ctx, source, snap)
            conn.commit()
    if not snap["record"]:
        intent = json.loads(plan["intent_json"])
        if intent.get("operation") == "start" and intent.get("auth_rejected_lifecycle") == "start":
            history = await mini.read_history(client, slot["schedule_id"])
            if sorted(str(r["id"]) for r in history) == sorted(intent.get("before_ids", [])):
                intent.pop("operation", None)
                intent.pop("auth_rejected_lifecycle", None)
                _plan_state(plan, "pending", "登录已恢复，回读确认尚未创建学校点名。", intent_json=_json(intent))
        if intent.get("operation") == "start":
            _plan_state(plan, "outcome_unknown", "学校开始点名的结果未知，请在官方端核实并选择确切轮次；不会重复创建。")
            return None
        if not plan["enabled"] or ctx.get("closed") or ctx["state"] == "void" or not intent.get("allow_create") or not snap["can_checkin"]:
            raise HTTPException(409, "当前课次不能新建学校点名。")
        location = _location(intent.get("location"))
        before = await mini.read_history(client, slot["schedule_id"])
        _fence(user, plan, sending=True)
        intent.update(operation="start", acknowledged=False, before_ids=[str(r["id"]) for r in before], submitted_at=_now())
        _plan_state(plan, "outcome_unknown", "正在创建学校点名并等待回读确认。", intent_json=_json(intent))
        created = await mini.start_checkin(client, slot, location)
        intent["acknowledged"] = True
        intent["created_id"] = str(created["id"])
        _plan_state(plan, "outcome_unknown", "学校已接受开始请求，等待唯一轮次确认。", intent_json=_json(intent))
        after = await mini.read_history(client, slot["schedule_id"])
        added = [r for r in after if str(r["id"]) not in intent["before_ids"]]
        if len(added) != 1 or str(added[0]["id"]) != intent["created_id"]:
            raise HTTPException(409, "学校出现多个或尚未显示新轮次，未猜测绑定。")
        snap = await mini.read_slot(client, slot, source, str(added[0]["id"]))
    # An unacknowledged start may have succeeded; never silently take ownership
    # of a newly appeared round on recovery. A teacher must explicitly select it.
    intent = json.loads(plan["intent_json"])
    if intent.get("operation") == "start" and not plan["remote_checkin_id"] and not intent.get("acknowledged"):
        _plan_state(plan, "outcome_unknown", "开始请求可能已执行，请选择官方端确切点名后确认绑定。")
        return None
    if intent.get("operation") == "start" and intent.get("created_id") and str(snap["record"]["id"]) != str(intent["created_id"]):
        raise HTTPException(409, "官方端已出现其他点名，未把它当作本次创建结果。")
    if not plan["binding_id"]:
        plan["binding_id"] = _bind_plan(user, plan, snap)
    plan["remote_checkin_id"] = str(snap["record"]["id"])
    if intent.get("operation") == "start":
        intent["verified"] = True
        _plan_state(plan, "pending", intent_json=_json(intent))
        plan["intent_json"] = _json(intent)
    if ctx["state"] not in {"settling", "published"}:
        _plan_state(plan, "waiting_local_end", "已绑定正确的学校点名，等待本地点名结束。")
        return None
    if snap["record"].get("isStop") is not True:
        _guard_remote_roster(user, plan, ctx, snap["students"])
        _fence(user, plan, sending=True)
        intent = json.loads(plan["intent_json"])
        if intent.get("operation") == "stop" and intent.get("auth_rejected_lifecycle") == "stop":
            intent.pop("operation", None)
            intent.pop("auth_rejected_lifecycle", None)
            _plan_state(plan, "pending", "登录已恢复，回读确认学校点名仍在进行。", intent_json=_json(intent))
        if intent.get("operation") == "stop":
            _plan_state(plan, "outcome_unknown", "学校结束点名尚未确认，请在官方端结束后回读；不会重复发送。")
            return None
        intent.update(operation="stop", submitted_at=_now(), verified=False)
        _plan_state(plan, "outcome_unknown", "正在结束学校点名并回读确认。", intent_json=_json(intent))
        await mini.stop_checkin(client, plan["remote_checkin_id"])
        snap = await mini.read_slot(client, slot, source, plan["remote_checkin_id"])
        if snap["record"].get("isStop") is not True:
            raise HTTPException(409, "学校尚未确认点名结束，未发送学生改签。")
    intent = json.loads(plan["intent_json"])
    if intent.get("operation") == "stop" or snap["record"].get("isStop") is True:
        intent.pop("auth_rejected_lifecycle", None)
        intent.update(operation="stop", verified=True)
        _plan_state(plan, "pending", intent_json=_json(intent))
    if dispatch_intent.get("manual_dispatch") and not original_record:
        # A newly created school round did not exist during the first import.
        # Persist its verified roster before AI comparison or any student write.
        from .smart_classroom_workflow_service import persist_remote_snapshot
        with get_db_connection() as conn:
            persist_remote_snapshot(conn, user, ctx, source, snap)
            conn.commit()
    if ctx["state"] != "published":
        _plan_state(plan, "waiting_settlement", "学校点名已结束，等待本地迟到事件归并和最终结果发布。")
        return None
    snap["comparison_record"] = original_record
    snap["new_remote_round"] = dispatch_intent.get("new_remote_round", False)
    return snap


def _new_run(user, plan, ctx, snapshot):
    """A new local revision inherits the last confirmed remote baseline."""
    with get_db_connection() as conn:
        fresh_ctx, fresh = _plan(conn, user, plan["local_round_id"], lock=True)
        fingerprint = _fingerprint(ctx, plan)
        if not fresh["enabled"] or fresh["revision"] != plan["revision"] or fresh_ctx["state"] != "published" or _fingerprint(fresh_ctx, fresh) != fingerprint:
            raise HTTPException(409, "本地最终结果或同步授权已变化。")
        binding = dict(conn.execute("SELECT * FROM smart_attendance_write_bindings WHERE id=?", (plan["binding_id"],)).fetchone())
        if binding["state"] != "active" or binding["credential_generation"] != plan["credential_generation"]:
            raise HTTPException(409, "远端绑定或凭据已变化。")
        _assert_alignment(conn, user, fresh)
        last = conn.execute("SELECT * FROM smart_attendance_sync_runs WHERE id=?", (fresh["last_run_id"],)).fetchone() if fresh["last_run_id"] else None
        if last and ledger._unknown_submission(conn, dict(last)):
            return dict(last)
        manual = json.loads(plan["intent_json"]).get("manual_dispatch")
        if last and last["context_fingerprint"] == fingerprint and (not manual or last["request_key"].endswith(":" + str(plan["revision"]))):
            return dict(last)
        baseline = json.loads(binding["remote_snapshot_json"])["students"]
        if last:
            for op in conn.execute("SELECT student_number,remote_status,state FROM smart_attendance_sync_operations WHERE run_id=?", (last["id"],)).fetchall():
                if op["state"] == "confirmed":
                    baseline[op["student_number"]] = op["remote_status"]
            conn.execute("UPDATE smart_attendance_sync_runs SET state='superseded',revision=revision+1,updated_at=? WHERE id=? AND state NOT IN ('confirmed','outcome_unknown')", (_now(), last["id"]))
        targets = ledger._targets(ctx)
        intent = json.loads(plan["intent_json"])
        if intent.get("manual_dispatch"):
            from .smart_classroom_workflow_service import comparison_targets
            targets, _ = comparison_targets(ctx, {**snapshot, "record": snapshot.get("comparison_record", snapshot["record"])})
            # This explicit dispatch authorizes a fresh remote snapshot; later
            # changes are still protected by the per-operation baseline fence.
            baseline = dict(snapshot["students"])
        school_authoritative = json.loads(plan["source_json"]).get("course_match_evidence") == "verified_course_alignment"
        comparisons = ledger.compare_students(targets, baseline, snapshot["students"], school_authoritative=school_authoritative)
        run_id, now = _id(), _now()
        conn.execute("""INSERT INTO smart_attendance_sync_runs(id,binding_id,owner_teacher_id,local_round_id,
            local_revision,context_fingerprint,credential_generation,binding_revision,target_json,baseline_json,state,request_key,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)""", (run_id, binding["id"], user["id"], plan["local_round_id"],
            ctx["revision"], fingerprint, plan["credential_generation"], binding["revision"], _json(targets),
            _json({"record": snapshot["record"], "students": baseline}), "auto:" + plan["local_round_id"] + ":" + fingerprint + (":" + str(plan["revision"]) if intent.get("manual_dispatch") else ""), now, now))
        for item in comparisons:
            state = "pending" if item["state"] == "needs_teacher_action" else item["state"]
            conn.execute("""INSERT INTO smart_attendance_sync_operations(id,run_id,student_id,student_number,target_revision,
                baseline_status,target_status,remote_status,state,reason,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (_id(), run_id, item["student_id"], item["student_number"], item["revision"], item["baseline_status"],
                 item["target_status"], item["remote_status"], state, item["reason"], now))
        ledger._event(conn, run_id, "automatic_sync_version", user["id"], {"local_fingerprint": fingerprint,
            "local_count": len(targets), "remote_count": len(snapshot["students"]),
            "remote_unmatched_count": len(set(snapshot["students"]) - {t["student_number"] for t in targets})})
        conn.execute("UPDATE smart_attendance_auto_sync SET last_run_id=?,last_context_fingerprint=?,updated_at=? WHERE local_round_id=?",
                     (run_id, fingerprint, now, plan["local_round_id"]))
        conn.commit()
        return dict(conn.execute("SELECT * FROM smart_attendance_sync_runs WHERE id=?", (run_id,)).fetchone())


def _op_state(run_id, number, state, remote, reason):
    with get_db_connection() as conn:
        conn.execute("UPDATE smart_attendance_sync_operations SET state=?,remote_status=?,reason=?,updated_at=? WHERE run_id=? AND student_number=?",
                     (state, remote, reason, _now(), run_id, number))
        conn.commit()


def _run_finish(plan, run, *, waiting=False):
    with get_db_connection() as conn:
        rows = conn.execute("SELECT state FROM smart_attendance_sync_operations WHERE run_id=?", (run["id"],)).fetchall()
        states = {r["state"] for r in rows}
        if "outcome_unknown" in states:
            run_state, state, message = "outcome_unknown", "outcome_unknown", "部分请求结果未知；先回读核对，未重复提交。"
        elif "pending" in states:
            run_state, state, message = "pending", "running", "正在分批提交并回读确认本地已发布结果。"
        elif states and states <= {"confirmed", "not_enrolled"}:
            skipped = sum(r["state"] == "not_enrolled" for r in rows)
            run_state, state, message = "confirmed", "confirmed", "双方共有学生的目标状态均已逐人回读一致；学校独有学生保持原状。"
            if skipped:
                message += f" {skipped} 名仅在本地的学生保留本地记录，未向学校新增学生。"
        elif "conflict" in states:
            run_state, state, message = "conflict", "conflict", "远端存在独立修改，已保留学校原状态，请核对差异。"
        else:
            run_state, state, message = "needs_teacher_action", "partial", "匹配学生已处理；待确认、特殊状态或未匹配学生保留原状。"
        conn.execute("UPDATE smart_attendance_sync_runs SET state=?,message=?,revision=revision+1,verified_at=?,updated_at=? WHERE id=?",
                     (run_state, message, _now(), _now(), run["id"]))
        fresh = conn.execute("SELECT enabled,state,revision FROM smart_attendance_auto_sync WHERE local_round_id=?", (plan["local_round_id"],)).fetchone()
        if fresh and fresh["enabled"] and fresh["revision"] == plan["revision"]:
            changed = conn.execute("UPDATE smart_attendance_auto_sync SET state=?,message=?,updated_at=? WHERE local_round_id=? AND enabled=1 AND revision=?", (state, message, _now(), plan["local_round_id"], plan["revision"]))
            if changed.rowcount == 1 and state == "running" and not waiting:
                schedule_due_auto_sync(conn, plan["local_round_id"], force=True, delay=2)
        elif fresh and not fresh["enabled"] and "outcome_unknown" not in states:
            local = conn.execute("SELECT state,is_current FROM login_attendance_rounds WHERE id=?", (plan["local_round_id"],)).fetchone()
            state = "void" if not local or local["state"] == "void" or not local["is_current"] else "paused"
            message = "停止前已提交的请求已回读核对；剩余学生未发送，原始记录保留。"
            conn.execute("UPDATE smart_attendance_auto_sync SET state=?,message=?,updated_at=? WHERE local_round_id=? AND enabled=0 AND revision=?",
                         (state, message, _now(), plan["local_round_id"], fresh["revision"]))
            if "pending" in states:
                conn.execute("UPDATE smart_attendance_sync_runs SET state='paused',message=?,updated_at=? WHERE id=?", (message, _now(), run["id"]))
        conn.commit()
        return {"round_id": plan["local_round_id"], "state": state, "run_id": run["id"]}


async def _reconcile_unknown(client, user, plan, run):
    source, slot = json.loads(plan["source_json"]), json.loads(plan["slot_json"])
    # Preserve identity verification even when the local round has been voided.
    await mini.read_slot(client, slot, source, plan["remote_checkin_id"])
    remote = await mini.read_records(client, plan["remote_checkin_id"])
    with get_db_connection() as conn:
        operations = [dict(r) for r in conn.execute("SELECT * FROM smart_attendance_sync_operations WHERE run_id=? AND state='outcome_unknown'", (run["id"],)).fetchall()]
    intent = json.loads(plan["intent_json"])
    for op in operations:
        actual = remote.get(op["student_number"])
        if actual == op["target_status"]:
            _op_state(run["id"], op["student_number"], "confirmed", actual, "恢复后回读确认先前请求已生效。")
        elif intent.get("auth_rejected_operation_id") == op["id"] and actual == op["baseline_status"]:
            _op_state(run["id"], op["student_number"], "pending", actual, "过期会话已自动恢复，回读确认原状态未变，将继续提交。")
        else:
            _op_state(run["id"], op["student_number"], "outcome_unknown", actual,
                      "先前请求可能已提交，当前结果尚不能确认；请在官方端核实，系统不会盲目重发。")
    if intent.pop("auth_rejected_operation_id", None):
        with get_db_connection() as conn:
            conn.execute("UPDATE smart_attendance_auto_sync SET intent_json=? WHERE local_round_id=? AND revision=?", (_json(intent), plan["local_round_id"], plan["revision"]))
            conn.commit()
        plan["intent_json"] = _json(intent)
    return _run_finish(plan, run, waiting=True)


async def _reconcile_lifecycle_only(client, plan):
    """A pause/void can never turn reconciliation into a new school mutation."""
    intent = json.loads(plan["intent_json"])
    expected = plan["remote_checkin_id"] or intent.get("created_id")
    if not expected:
        return {"state": "outcome_unknown"}
    snap = await mini.read_slot(client, json.loads(plan["slot_json"]), json.loads(plan["source_json"]), expected)
    resolved = intent.get("operation") == "start" or snap["record"].get("isStop") is True
    if resolved:
        intent["verified"] = True
        with get_db_connection() as conn:
            local = conn.execute("SELECT state,is_current FROM login_attendance_rounds WHERE id=?", (plan["local_round_id"],)).fetchone()
            state = "void" if not local or local["state"] == "void" or not local["is_current"] else "paused"
            conn.execute("UPDATE smart_attendance_auto_sync SET intent_json=?,remote_checkin_id=?,state=?,updated_at=? WHERE local_round_id=? AND revision=? AND enabled=0",
                (_json(intent), str(expected), state, _now(), plan["local_round_id"], plan["revision"]))
            conn.commit()
        return {"state": state}
    return {"state": "outcome_unknown"}


def _submission_intent(user, plan, run, op):
    """Final local fence and intent are in one transaction before external IO."""
    with get_db_connection() as conn:
        ctx, fresh = _plan(conn, user, plan["local_round_id"], lock=True)
        ledger._account_access(conn, user["id"], plan["external_account_key"], plan["credential_generation"])
        binding = conn.execute("SELECT state,revision,credential_generation FROM smart_attendance_write_bindings WHERE id=?", (run["binding_id"],)).fetchone()
        if not fresh or not fresh["enabled"] or fresh["revision"] != plan["revision"] or ctx["state"] != "published" or _fingerprint(ctx, fresh) != run["context_fingerprint"]:
            raise HTTPException(409, "本地结果、轮次或授权已变化，旧版本停止发送。")
        if not binding or binding["state"] != "active" or binding["revision"] != run["binding_revision"] or binding["credential_generation"] != run["credential_generation"]:
            raise HTTPException(409, "绑定已变化，旧版本停止发送。")
        _assert_alignment(conn, user, fresh)
        changed = conn.execute("UPDATE smart_attendance_sync_operations SET state='outcome_unknown',reason=?,updated_at=? WHERE id=? AND state='pending'",
                               ("提交意图已保存，等待学校回读。", _now(), op["id"]))
        if changed.rowcount != 1:
            raise HTTPException(409, "该学生的同步操作已由其他任务处理。")
        conn.execute("UPDATE smart_attendance_sync_runs SET state='outcome_unknown',updated_at=? WHERE id=?", (_now(), run["id"]))
        conn.execute("UPDATE smart_attendance_auto_sync SET state='outcome_unknown',updated_at=? WHERE local_round_id=?", (_now(), plan["local_round_id"]))
        ledger._event(conn, run["id"], "student_submit_intent", user["id"], {"student_id": op["student_id"], "target_status": op["target_status"]})
        conn.commit()


async def _write_batch(client, user, plan, run, snapshot):
    source, slot = json.loads(plan["source_json"]), json.loads(plan["slot_json"])
    leave_corrections = {t["student_number"] for t in json.loads(run["target_json"]) if t.get("allow_protected_override")}
    # AI comparison and queue handoff may take seconds. Read again before the
    # first mutation so independent school edits become conflicts, not writes.
    remote = await mini.read_records(client, plan["remote_checkin_id"])
    ctx, _ = _fence(user, plan, sending=True, expected_fingerprint=run["context_fingerprint"])
    _guard_remote_roster(user, plan, ctx, remote)
    with get_db_connection() as conn:
        operations = [dict(r) for r in conn.execute("SELECT * FROM smart_attendance_sync_operations WHERE run_id=? ORDER BY student_id", (run["id"],)).fetchall()]
    submitted = 0
    for op in operations:
        if op["state"] == "confirmed" and remote.get(op["student_number"]) != op["target_status"]:
            _op_state(run["id"], op["student_number"], "conflict", remote.get(op["student_number"]), "学校状态在确认后发生独立修改，保留学校现值。")
        if op["state"] != "pending":
            continue
        if submitted >= BATCH_SIZE:
            break
        actual, desired = remote.get(op["student_number"]), op["target_status"]
        if actual is None and source.get("course_match_evidence") == "verified_course_alignment":
            _op_state(run["id"], op["student_number"], "not_enrolled", None,
                      "学校名单已移除此学生，尚未向其发送；保留本地记录。")
            continue
        if actual == desired:
            _op_state(run["id"], op["student_number"], "confirmed", actual, "学校回读状态已一致，无需重复提交。")
            continue
        if actual is None or actual not in mini.STATUSES or desired is None:
            _op_state(run["id"], op["student_number"], "blocked", actual, "身份或结果无法可靠确认，保留学校原状态。")
            continue
        if actual in ledger.PROTECTED_STATUSES and not (op["student_number"] in leave_corrections and actual in {"SICK_LEAVE", "PERSONAL_LEAVE"}):
            _op_state(run["id"], op["student_number"], "protected", actual, "学校已设置请假或特殊状态，保留原值。")
            continue
        if actual != op["baseline_status"]:
            _op_state(run["id"], op["student_number"], "conflict", actual, "学校状态在同步期间已发生独立变化，保留原值。")
            continue
        _submission_intent(user, plan, run, op)
        await mini.change_student(client, plan["remote_checkin_id"], op["student_number"], desired)
        submitted += 1
        remote = await mini.read_records(client, plan["remote_checkin_id"])
        actual = remote.get(op["student_number"])
        if actual != desired:
            _op_state(run["id"], op["student_number"], "outcome_unknown", actual, "学校已响应但回读未确认目标状态，停止后续提交。")
            break
        _op_state(run["id"], op["student_number"], "confirmed", actual, "提交后完整名单回读已确认该学生状态。")
        _guard_remote_roster(user, plan, ctx, remote)
    # Confirm that a concurrent official restart did not silently replace the
    # scoped round; never report another round as the success of this one.
    await mini.read_slot(client, slot, source, plan["remote_checkin_id"])
    return _run_finish(plan, run)


async def process_auto_sync(teacher_id, round_id):
    user = {"role": "teacher", "id": int(teacher_id)}
    with get_db_connection() as conn:
        ctx, plan = _plan(conn, user, round_id)
        if not plan:
            return {"state": "not_configured"}
        last = conn.execute("SELECT * FROM smart_attendance_sync_runs WHERE id=?", (plan["last_run_id"],)).fetchone() if plan["last_run_id"] else None
        unknown = bool(last and ledger._unknown_submission(conn, dict(last)))
        unknown_lifecycle = _lifecycle_unknown(plan)
        if not unknown and not unknown_lifecycle and (not plan["enabled"] or ctx["state"] == "void" or not ctx.get("is_current", True)):
            return {"state": plan["state"]}
    try:
        with get_db_connection() as conn:
            access = ledger._account_access(conn, user["id"], plan["external_account_key"], plan["credential_generation"])
        async with ledger._account_lease(plan["external_account_key"]) as lease_token:
            async with open_authenticated_smart_classroom_client(access, api="mini", login_scope="attendance:" + str(round_id), allow_login=True) as (client, _, _login):
                client = _LeasedClient(client, plan["external_account_key"], lease_token)
                if unknown_lifecycle and (not plan["enabled"] or ctx["state"] == "void" or not ctx.get("is_current", True)):
                    return await _reconcile_lifecycle_only(client, plan)
                if unknown:
                    result = await _reconcile_unknown(client, user, plan, dict(last))
                    if result["state"] == "outcome_unknown" or not plan["enabled"] or ctx["state"] != "published":
                        return result
                    # Previously submitted old versions are resolved before a
                    # corrected local version is ever allowed to start.
                ctx, plan = _fence(user, plan)
                if not plan["enabled"] or ctx["state"] == "void":
                    return {"state": "paused"}
                snapshot = await _prepare_remote(client, user, plan, ctx)
                if snapshot is None:
                    with get_db_connection() as conn:
                        return {"state": _plan(conn, user, round_id)[1]["state"]}
                ctx, plan = _fence(user, plan, sending=True)
                if ctx["state"] != "published":
                    return {"state": "waiting_settlement"}
                intent = json.loads(plan["intent_json"])
                if intent.get("manual_dispatch") and not (last and last["context_fingerprint"] == _fingerprint(ctx, plan) and str(last["request_key"]).endswith(":" + str(plan["revision"]))):
                    from .smart_classroom_workflow_service import comparison_targets, _ai_assist
                    _, comparison = comparison_targets(ctx, {**snapshot, "record": snapshot.get("comparison_record", snapshot["record"])})
                    _plan_state(plan, "comparing", "学校记录已同步，正在按修改时间和出勤证据比对。")
                    allowed = ["local_newer", "remote_newer", "mixed", "unknown"]
                    comparison["ai"] = await _ai_assist("依据给出的逐人可靠时间比较计数判定版本方向。local_newer/remote_newer只有对应计数且无另一方向与unknown时成立；多种并存选mixed；全unknown选unknown。远端较新或不明时只补出勤。", {"counts": comparison["counts"], "remote_updated_at_ms": comparison["remote_updated_at_ms"], "allowed_decisions": allowed})
                    present_modes = {k for k in ("local_newer", "remote_newer", "unknown") if comparison["counts"].get(k)}
                    expected_decision = next(iter(present_modes)) if len(present_modes) == 1 else "mixed"
                    if comparison["ai"]["status"] == "assisted" and comparison["ai"]["decision"] != expected_decision:
                        comparison["ai"] = {"status": "evidence_fallback", "decision": expected_decision}
                    intent["comparison"] = comparison
                    _plan_state(plan, "running", "已完成比对，正在逐人推送并回读。", intent_json=_json(intent))
                    ctx, plan = _fence(user, plan, sending=True)
                run = _new_run(user, plan, ctx, snapshot)
                result = await _write_batch(client, user, plan, run, snapshot)
                if intent.get("manual_dispatch") and result["state"] in {"confirmed", "partial", "conflict"}:
                    from .smart_classroom_workflow_service import persist_remote_snapshot
                    final_snapshot = await mini.read_slot(client, json.loads(plan["slot_json"]), json.loads(plan["source_json"]), plan["remote_checkin_id"])
                    with get_db_connection() as conn:
                        persist_remote_snapshot(conn, user, ctx, json.loads(plan["source_json"]), final_snapshot)
                        conn.commit()
                return result
    except Exception as exc:
        # Safe, bounded recovery: scheduler may retry a read, never an unlogged
        # mutation. Do not expose upstream secrets or personal error payloads.
        code = getattr(exc, "code", None)
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict):
            code = detail.get("code", code)
        with get_db_connection() as conn:
            fresh_ctx, fresh = _plan(conn, user, round_id)
            run = conn.execute("SELECT * FROM smart_attendance_sync_runs WHERE id=?", (fresh["last_run_id"],)).fetchone() if fresh and fresh["last_run_id"] else None
            pending_unknown = bool(run and ledger._unknown_submission(conn, dict(run)))
            intent = json.loads(fresh["intent_json"]) if fresh else {}
            lifecycle_unknown = bool(fresh and fresh["state"] == "outcome_unknown" and intent.get("operation") in {"start", "stop"})
            if fresh and fresh["enabled"] and intent.get("manual_dispatch") and fresh_ctx["state"] == "published" and not pending_unknown and _fingerprint(fresh_ctx, fresh) != intent.get("authorized_fingerprint"):
                conn.execute("UPDATE smart_attendance_auto_sync SET enabled=0,state='awaiting_push',message=?,updated_at=? WHERE local_round_id=?", ("本地结果已更新，请点击同步与推送以提交新版本。", _now(), str(round_id)))
                conn.commit()
                return {"state": "awaiting_push"}
            if fresh and fresh["enabled"] and fresh_ctx["state"] == "published" and run and not pending_unknown and _fingerprint(fresh_ctx, fresh) != run["context_fingerprint"]:
                conn.execute("UPDATE smart_attendance_auto_sync SET state='pending',message=?,updated_at=? WHERE local_round_id=?", ("本地结果已更新，将按最新发布版本继续。", _now(), str(round_id)))
                schedule_due_auto_sync(conn, round_id, force=True, delay=2)
                conn.commit()
                return {"state": "pending"}
        if code == "session_refreshed" and fresh and fresh["enabled"]:
            with get_db_connection() as conn:
                if pending_unknown and run:
                    rejected = conn.execute("SELECT id FROM smart_attendance_sync_operations WHERE run_id=? AND state='outcome_unknown' ORDER BY updated_at DESC LIMIT 1", (run["id"],)).fetchone()
                    if rejected:
                        intent["auth_rejected_operation_id"] = rejected["id"]
                elif lifecycle_unknown:
                    intent["auth_rejected_lifecycle"] = intent["operation"]
                conn.execute("UPDATE smart_attendance_auto_sync SET intent_json=?,message=?,updated_at=? WHERE local_round_id=? AND revision=?", (_json(intent), "会话已自动更新，正在回读并继续同步。", _now(), str(round_id), plan["revision"]))
                schedule_due_auto_sync(conn, round_id, force=True, delay=2)
                conn.commit()
            return {"state": "outcome_unknown"}
        if code in {"captcha_required", "credential_invalid"}:
            state, message = code, "学校要求验证码验证，请完成学校登录验证后重试；本地点名和同步进度已保留。" if code == "captcha_required" else "学校账号密码验证失败，请更新账号密码；本地点名和同步进度已保留。"
        elif code in {"credential_changed", "authentication_required"}:
            state, message = "needs_teacher_reauth", "学校会话或凭据已变化，请教师重新授权；已提交请求先回读，不会自动重放。"
        elif getattr(exc, "status_code", None) == 429 or code in {"session_busy", "needs_teacher_reauth"}:
            with get_db_connection() as conn:
                _, retry_plan = _plan(conn, user, round_id, lock=True)
                retry_intent = json.loads(retry_plan["intent_json"])
                attempts = int(retry_intent.get("busy_retries", 0))
                if retry_plan["enabled"] and retry_plan["revision"] == plan["revision"] and attempts < 2:
                    retry_intent["busy_retries"] = attempts + 1
                    conn.execute("UPDATE smart_attendance_auto_sync SET intent_json=? WHERE local_round_id=?", (_json(retry_intent), str(round_id)))
                    schedule_due_auto_sync(conn, round_id, force=True, delay=65 * (attempts + 1)); conn.commit()
                    return {"state": "pending"}
            state, message = "failed", "该学校账号持续繁忙，已停止自动重试；请稍后由教师重试。"
        elif pending_unknown or lifecycle_unknown:
            state, message = "outcome_unknown", "请求可能已提交，已保留意图日志；请回读核对，未重复发送。"
        elif code == "alignment_changed":
            state, message = "conflict", detail.get("message") or "课程或课次对齐已变化，请重新对齐后推送；本地记录保留。"
        else:
            state, message = "failed", "同步暂未完成，原有考勤保留；请检查课次绑定或学校服务后重试。"
        if fresh and fresh["enabled"]:
            _state(round_id, state, message, expected_revision=plan["revision"])
            if state == "outcome_unknown":
                with get_db_connection() as conn:
                    _, recovery = _plan(conn, user, round_id, lock=True)
                    recovery_intent = json.loads(recovery["intent_json"])
                    if recovery["enabled"] and recovery["revision"] == plan["revision"] and int(recovery_intent.get("unknown_reads", 0)) < 1:
                        recovery_intent["unknown_reads"] = 1
                        conn.execute("UPDATE smart_attendance_auto_sync SET intent_json=? WHERE local_round_id=?", (_json(recovery_intent), str(round_id)))
                        schedule_due_auto_sync(conn, round_id, force=True, delay=5)
                        conn.commit()
        return {"state": state}
