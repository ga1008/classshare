"""Attendance archive commands and queries; transactions belong to the caller.

Remote clients and AI never run inside this module. Worker writes are fenced by
the durable job lease, and reviewed interpretations never mutate their source.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import unicodedata
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..config import SECRET_KEY
from .. import config
from ..db.connection import get_configured_db_engine
from ..time_utils import local_iso
from .ai_durable_job_service import create_ai_job, cancel_ai_job_by_id
from .file_service import bind_global_file_references, resolve_global_file_path
from .semester_identity_service import identity_from_year_term

VALID_STATUSES = {"CHECKED", "UNCHECKED", "SICK_LEAVE", "PERSONAL_LEAVE", "LATE_OR_EARLY"}
ALL_STATUSES = VALID_STATUSES | {"UNKNOWN", "NOT_APPLICABLE"}
ACTIVE_JOBS = ("queued", "running", "retry_wait", "result_ready")


def _now() -> str:
    return local_iso(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _read(value: Any, fallback=None):
    if value in (None, ""):
        return {} if fallback is None else fallback
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return {} if fallback is None else fallback


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _int(value: Any, default=0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _actor(user: dict) -> int:
    if user.get("role") != "teacher" or not _int(user.get("id")):
        raise HTTPException(403, "仅教师可访问签到归档。")
    return int(user["id"])


def _lock(conn, table: str, row_id: int):
    if get_configured_db_engine() == "postgres":
        row = conn.execute(f"SELECT * FROM {table} WHERE id=? FOR UPDATE", (row_id,)).fetchone()
    else:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    return dict(row) if row else None


def _insert(conn, table: str, values: dict) -> int:
    columns = ",".join(values)
    marks = ",".join("?" for _ in values)
    return int(conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks}) RETURNING id", tuple(values.values())).fetchone()[0])


def _check_revision(actual: Any, expected: Any):
    if expected is None or _int(actual) != _int(expected, -1):
        raise HTTPException(409, {"code": "revision_conflict", "message": "数据已变化，请刷新后重试。", "revision": actual})


def _binding(conn, binding_id: int, user: dict, *, lock=False):
    row = _lock(conn, "smart_attendance_source_bindings", binding_id) if lock else conn.execute(
        "SELECT * FROM smart_attendance_source_bindings WHERE id=?", (binding_id,)).fetchone()
    if not row or int(row["owner_teacher_id"]) != _actor(user):
        raise HTTPException(404, "签到来源不存在或无访问权限。")
    return dict(row)


def get_attendance_report(conn, report_id: int, user: dict, *, lock=False, allow_deleted=False):
    row = _lock(conn, "attendance_reports", report_id) if lock else conn.execute(
        "SELECT * FROM attendance_reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        raise HTTPException(404, "签到档案不存在或无访问权限。")
    _binding(conn, int(row["binding_id"]), user)
    if row["deleted_at"] and not allow_deleted:
        raise HTTPException(404, "签到档案已移入回收站。")
    return dict(row)


def _run(conn, report_id: int, run_id: int, user: dict, *, lock=False):
    get_attendance_report(conn, report_id, user, allow_deleted=not lock)
    row = _lock(conn, "attendance_parse_runs", run_id) if lock else conn.execute(
        "SELECT * FROM attendance_parse_runs WHERE id=?", (run_id,)).fetchone()
    if not row or not conn.execute("SELECT 1 FROM attendance_report_versions WHERE id=? AND report_id=?",
                                  (row["source_version_id"], report_id)).fetchone():
        raise HTTPException(404, "解析版本不存在。")
    return dict(row)


def _offering(conn, offering_id: int | None, user: dict):
    if not offering_id:
        return None
    row = conn.execute("SELECT id,teacher_id,semester,semester_id FROM class_offerings WHERE id=?", (offering_id,)).fetchone()
    if not row or int(row["teacher_id"]) != _actor(user):
        raise HTTPException(403, "只能关联本人任课课堂。")
    return dict(row)


def normalize_attendance_term(year: Any, term: Any) -> tuple[str, int]:
    raw = str(year or "").strip()
    match = re.fullmatch(r"(20\d{2})-(20\d{2})", raw)
    if not match or int(match[2]) != int(match[1]) + 1 or _int(term) not in (1, 2):
        raise HTTPException(422, "请选择有效学年和第一/第二学期。")
    identity = identity_from_year_term(raw, term)
    if not identity:
        raise HTTPException(422, "学年学期无效。")
    y, t = identity.as_year_term()
    return y, int(t)


def sign_attendance_source_option(teacher_id: int, source: dict) -> str:
    raw = base64.urlsafe_b64encode(_json({"owner": teacher_id, "exp": int(time.time()) + 1800, "source": source}).encode()).decode().rstrip("=")
    digest = hmac.new(str(SECRET_KEY).encode(), ("attendance-source-v1:" + raw).encode(), hashlib.sha256).hexdigest()
    return raw + "." + digest


def _decode_option(token: str, teacher_id: int) -> dict:
    try:
        raw, supplied = token.rsplit(".", 1)
        digest = hmac.new(str(SECRET_KEY).encode(), ("attendance-source-v1:" + raw).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, supplied):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if payload["owner"] != teacher_id or payload["exp"] < time.time():
            raise ValueError()
        return payload["source"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, "来源选项已失效，请重新读取智慧课堂课程。") from None


def serialize_binding(conn, binding: dict) -> dict:
    result = {k: binding.get(k) for k in ("id", "owner_teacher_id", "school_code", "platform_code", "external_account_key", "academic_year", "academic_term", "revision", "binding_state")}
    result.update(course_name=binding["remote_course_name"], course_code=binding["remote_course_id"],
                  teaching_class_name=binding["remote_class_name"])
    link = conn.execute("SELECT class_offering_id,revision,is_grade_source FROM smart_attendance_source_offerings WHERE binding_id=? AND link_state='active'", (binding["id"],)).fetchone()
    result["class_offering_id"] = link["class_offering_id"] if link else None
    result["link_revision"] = link["revision"] if link else None
    result["is_grade_source"] = bool(link["is_grade_source"]) if link else False
    return result


def save_attendance_binding(conn, user: dict, *, source_token: str = "", binding_id: int | None = None,
                            class_offering_id: int | None = None, expected_revision=None) -> dict:
    if not config.ATTENDANCE_ARCHIVE_ENABLED:
        raise HTTPException(503, "新签到归档暂未启用，历史原件仍可查看。")
    creating = binding_id is None
    teacher_id = _actor(user)
    offering = _offering(conn, class_offering_id, user)
    now = _now()
    if binding_id:
        binding = _binding(conn, binding_id, user, lock=True)
        _check_revision(binding["revision"], expected_revision)
    else:
        source = _decode_option(source_token, teacher_id)
        year, term = normalize_attendance_term(source.get("academic_year"), source.get("academic_term"))
        identity = {"owner_teacher_id": teacher_id, "school_code": str(source.get("school_code") or "gxufl"),
                    "platform_code": str(source.get("platform_code") or "gxufl_smart_classroom"),
                    "external_account_key": str(source.get("external_account_key") or ""),
                    "remote_schedule_id": str(source.get("remote_schedule_id") or ""), "academic_year": year, "academic_term": term}
        if not identity["external_account_key"] or not identity["remote_schedule_id"]:
            raise HTTPException(422, "来源账号或授课班标识缺失。")
        columns = list(identity)
        conn.execute(f"INSERT INTO smart_attendance_source_bindings ({','.join(columns)},remote_course_id,remote_course_name,remote_class_id,remote_class_name,credential_id,created_at,updated_at,confirmed_by,confirmed_at) "
                     f"VALUES ({','.join('?' for _ in range(len(columns)+9))}) ON CONFLICT(owner_teacher_id,school_code,platform_code,external_account_key,academic_year,academic_term,remote_schedule_id) DO NOTHING",
                     (*identity.values(), str(source.get("course_code") or ""), str(source.get("course_name") or ""), str(source.get("remote_class_id") or ""), str(source.get("teaching_class_name") or ""), source.get("credential_id"), now, now, teacher_id, now))
        row = conn.execute("SELECT * FROM smart_attendance_source_bindings WHERE " + " AND ".join(k+"=?" for k in columns), tuple(identity.values())).fetchone()
        binding = _binding(conn, int(row["id"]), user, lock=True)
        binding_id = binding["id"]
    if offering:
        semester = conn.execute("SELECT name FROM academic_semesters WHERE id=?", (offering.get("semester_id"),)).fetchone() if offering.get("semester_id") else None
        from .semester_identity_service import parse_semester_identity
        ident = parse_semester_identity(semester["name"] if semester else offering.get("semester"))
        if not ident or ident.as_year_term() != (binding["academic_year"], str(binding["academic_term"])):
            raise HTTPException(409, "课堂学期与智慧课堂来源不一致，不能绑定。")
    active = conn.execute("SELECT * FROM smart_attendance_source_offerings WHERE binding_id=? AND link_state='active'", (binding_id,)).fetchone()
    if creating and active and _int(active["class_offering_id"]) != _int(class_offering_id):
        raise HTTPException(409, "该来源已有课堂关联，请使用已有绑定版本明确修改。")
    if active and _int(active["class_offering_id"]) != _int(class_offering_id):
        conn.execute("UPDATE smart_attendance_source_offerings SET link_state='ended',is_grade_source=0,ended_at=?,revision=revision+1 WHERE id=?", (now, active["id"]))
    if offering and (not active or _int(active["class_offering_id"]) != class_offering_id):
        conn.execute("INSERT INTO smart_attendance_source_offerings (binding_id,class_offering_id,confirmed_by,confirmed_at) VALUES (?,?,?,?) ON CONFLICT(binding_id,class_offering_id) DO UPDATE SET link_state='active',is_grade_source=0,ended_at=NULL,revision=smart_attendance_source_offerings.revision+1,confirmed_by=excluded.confirmed_by,confirmed_at=excluded.confirmed_at",
                     (binding_id, class_offering_id, teacher_id, now))
    conn.execute("UPDATE smart_attendance_source_bindings SET revision=revision+1,updated_at=? WHERE id=?", (now, binding_id))
    return {"binding": serialize_binding(conn, _binding(conn, binding_id, user))}


def list_attendance_bindings(conn, user: dict, *, class_offering_id=None, year=None, term=None) -> dict:
    where, params = ["b.owner_teacher_id=?"], [_actor(user)]
    if class_offering_id:
        _offering(conn, class_offering_id, user)
        where.append("EXISTS(SELECT 1 FROM smart_attendance_source_offerings l WHERE l.binding_id=b.id AND l.class_offering_id=? AND l.link_state='active')")
        params.append(class_offering_id)
    if year:
        where.append("b.academic_year=?"); params.append(year)
    if term:
        where.append("b.academic_term=?"); params.append(term)
    rows = conn.execute("SELECT b.* FROM smart_attendance_source_bindings b WHERE " + " AND ".join(where) + " ORDER BY b.academic_year DESC,b.academic_term DESC,b.id DESC", params).fetchall()
    credential = conn.execute("SELECT id FROM teacher_smart_classroom_credentials WHERE teacher_id=? AND enabled=1 AND last_status='verified' LIMIT 1", (_actor(user),)).fetchone()
    return {"bindings": [serialize_binding(conn, dict(r)) for r in rows], "credential_available": bool(credential), "year": year, "term": term,
            "archive_enabled": config.ATTENDANCE_ARCHIVE_ENABLED, "parse_enabled": config.ATTENDANCE_PARSE_ENABLED}


def _public_job(row) -> dict:
    item = dict(row)
    return {"id": item["id"], "task_type": item["task_type"], "status": item["status"],
            "error": item.get("last_error") or "", "error_code": item.get("last_error_code") or "",
            "created_at": item.get("created_at"), "updated_at": item.get("updated_at"),
            "status_url": f"/api/attendance-reports/jobs/{item['id']}"}


def get_attendance_job(conn, job_id: int, user: dict) -> dict:
    row = conn.execute("SELECT * FROM ai_jobs WHERE id=? AND owner_role='teacher' AND owner_user_pk=? AND task_type IN ('attendance_export','attendance_parse')", (job_id, _actor(user))).fetchone()
    if not row:
        raise HTTPException(404, "签到任务不存在。")
    return {"job": _public_job(row)}


def _job_reply(conn, report: dict, version: dict, job_id: int, run_id=None) -> dict:
    return {"report_id": report["id"], "source_version_id": version["id"], "parse_run_id": run_id,
            "job_id": job_id, "status_url": f"/api/attendance-reports/jobs/{job_id}", "report_revision": report["revision"]}


def _request_key(key: Any) -> str:
    text = str(key or "").strip()
    if not text or len(text) > 160:
        raise HTTPException(422, "请求必须带有效幂等标识。")
    return text


def enqueue_attendance_export(conn, user: dict, *, binding_id: int, expected_binding_revision: int, idempotency_key: str):
    if not config.ATTENDANCE_ARCHIVE_ENABLED:
        raise HTTPException(503, "新签到归档暂未启用，历史原件仍可查看。")
    binding = _binding(conn, binding_id, user, lock=True)
    _check_revision(binding["revision"], expected_binding_revision)
    key, now = _request_key(idempotency_key), _now()
    conn.execute("INSERT INTO attendance_reports(binding_id,created_at,updated_at) VALUES(?,?,?) ON CONFLICT(binding_id,scope_kind) DO NOTHING", (binding_id, now, now))
    report = dict(conn.execute("SELECT * FROM attendance_reports WHERE binding_id=?", (binding_id,)).fetchone())
    if report["deleted_at"]:
        raise HTTPException(409, "请先恢复回收站中的签到档案。")
    prior = conn.execute("SELECT * FROM attendance_report_versions WHERE report_id=? AND request_key=?", (report["id"], key)).fetchone()
    if prior:
        return _job_reply(conn, report, dict(prior), prior["job_id"])
    active = conn.execute("SELECT v.* FROM attendance_report_versions v JOIN ai_jobs j ON j.id=v.job_id WHERE v.report_id=? AND j.status IN ('queued','running','retry_wait','result_ready') ORDER BY v.id DESC LIMIT 1", (report["id"],)).fetchone()
    if active:
        return _job_reply(conn, report, dict(active), active["job_id"])
    version_no = conn.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM attendance_report_versions WHERE report_id=?", (report["id"],)).fetchone()[0]
    version_id = _insert(conn, "attendance_report_versions", {"report_id": report["id"], "version_no": version_no, "request_key": key, "created_by": _actor(user), "created_at": now})
    payload = {"teacher_id": _actor(user), "binding_id": binding_id, "binding_revision": binding["revision"], "report_id": report["id"], "source_version_id": version_id, "auto_parse": True}
    job, _ = create_ai_job(conn, task_type="attendance_export", dedupe_key=f"attendance-export:{report['id']}:{key}", payload=payload,
                           owner_role="teacher", owner_user_pk=_actor(user), scope_type="attendance_report", scope_id=str(report["id"]), source_ref=f"attendance_report:{report['id']}")
    conn.execute("UPDATE attendance_report_versions SET job_id=? WHERE id=?", (job["id"], version_id))
    conn.execute("UPDATE attendance_reports SET updated_at=? WHERE id=?", (now, report["id"]))
    return _job_reply(conn, report, {"id": version_id}, job["id"])


def enqueue_attendance_parse(conn, user: dict, *, report_id: int, source_version_id: int, idempotency_key: str,
                              base_confirmed_run_id=None):
    if not config.ATTENDANCE_PARSE_ENABLED:
        raise HTTPException(503, "AI 签到解析暂未启用，缓存原件仍可查看。")
    report = get_attendance_report(conn, report_id, user, lock=True)
    version = conn.execute("SELECT * FROM attendance_report_versions WHERE id=? AND report_id=?", (source_version_id, report_id)).fetchone()
    if not version or version["source_state"] != "cached" or not resolve_global_file_path(version["source_file_hash"]):
        raise HTTPException(409, "有效原件尚未缓存。")
    key = _request_key(idempotency_key)
    previous = conn.execute("SELECT * FROM attendance_parse_runs WHERE source_version_id=? AND request_key=?", (source_version_id, key)).fetchone()
    if previous:
        return _job_reply(conn, report, dict(version), previous["job_id"], previous["id"])
    active = conn.execute("SELECT p.* FROM attendance_parse_runs p JOIN ai_jobs j ON j.id=p.job_id WHERE p.source_version_id=? AND j.status IN ('queued','running','retry_wait','result_ready') ORDER BY p.id DESC LIMIT 1", (source_version_id,)).fetchone()
    if active:
        return _job_reply(conn, report, dict(version), active["job_id"], active["id"])
    number = conn.execute("SELECT COALESCE(MAX(run_no),0)+1 FROM attendance_parse_runs WHERE source_version_id=?", (source_version_id,)).fetchone()[0]
    link = conn.execute("SELECT * FROM smart_attendance_source_offerings WHERE binding_id=? AND link_state='active'", (report["binding_id"],)).fetchone()
    if base_confirmed_run_id:
        base = _run(conn, report_id, base_confirmed_run_id, user)
        if base["state"] != "confirmed":
            raise HTTPException(409, "只能从已确认版本派生更正。")
    run_id = _insert(conn, "attendance_parse_runs", {"source_version_id": source_version_id, "run_no": number, "request_key": key,
                     "base_confirmed_run_id": base_confirmed_run_id or report["confirmed_parse_run_id"],
                     "mapped_offering_id": link["class_offering_id"] if link else None, "binding_link_revision": link["revision"] if link else None})
    job, _ = create_ai_job(conn, task_type="attendance_parse", dedupe_key=f"attendance-parse:{source_version_id}:{key}",
                           payload={"teacher_id": _actor(user), "report_id": report_id, "source_version_id": source_version_id, "parse_run_id": run_id},
                           owner_role="teacher", owner_user_pk=_actor(user), scope_type="attendance_report", scope_id=str(report_id), source_ref=f"attendance_report:{report_id}")
    conn.execute("UPDATE attendance_parse_runs SET job_id=? WHERE id=?", (job["id"], run_id))
    return _job_reply(conn, report, dict(version), job["id"], run_id)


def load_attendance_job_context(conn, job_id: int, lease_token: str) -> dict:
    preliminary = conn.execute("SELECT payload_json FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
    if not preliminary:
        raise HTTPException(409, "签到任务不存在。")
    initial_payload = _read(preliminary["payload_json"])
    # Shared lock order is report -> job -> run, including soft delete/cancel.
    get_attendance_report(conn, initial_payload.get("report_id", 0), {"role": "teacher", "id": initial_payload.get("teacher_id")}, lock=True)
    job = _lock(conn, "ai_jobs", job_id)
    if not job or job["task_type"] not in {"attendance_export", "attendance_parse"} or job["status"] != "running" or not lease_token or job["lease_token"] != lease_token:
        raise HTTPException(409, "签到任务租约已失效或已取消。")
    # Durable jobs currently use the host's naive datetime.now() clock; archive
    # labels use the configured teaching timezone. Never compare the two clocks.
    if not job.get("lease_expires_at") or job["lease_expires_at"] <= datetime.now().isoformat(timespec="seconds"):
        raise HTTPException(409, "签到任务租约已到期。")
    payload = _read(job["payload_json"])
    user = {"role": "teacher", "id": payload["teacher_id"]}
    report = get_attendance_report(conn, payload["report_id"], user, lock=True)
    binding = _binding(conn, report["binding_id"], user)
    version = conn.execute("SELECT * FROM attendance_report_versions WHERE id=? AND report_id=?", (payload["source_version_id"], report["id"])).fetchone()
    if not version:
        raise HTTPException(409, "任务原件版本已失效。")
    run = None
    if job["task_type"] == "attendance_export":
        _check_revision(binding["revision"], payload["binding_revision"])
        if version["source_state"] != "cached":
            conn.execute("UPDATE attendance_report_versions SET source_state='exporting' WHERE id=?", (version["id"],))
    else:
        run = _run(conn, report["id"], payload["parse_run_id"], user, lock=True)
        if run["source_version_id"] != version["id"] or version["source_state"] != "cached":
            raise HTTPException(409, "解析原件不匹配。")
        if run["state"] not in {"queued", "parsing", "failed"}:
            raise HTTPException(409, "解析候选已完成或已被复核。")
        conn.execute("UPDATE attendance_parse_runs SET state='parsing',started_at=COALESCE(started_at,?) WHERE id=?", (_now(), run["id"]))
        run["state"] = "parsing"
    return {"job": job, "payload": payload, "binding": binding, "report": report, "source_version": dict(version), "parse_run": run}


def cache_attendance_source(conn, job_id: int, lease_token: str, source: dict) -> dict:
    ctx = load_attendance_job_context(conn, job_id, lease_token)
    version, report = ctx["source_version"], ctx["report"]
    if ctx["job"]["task_type"] != "attendance_export":
        raise HTTPException(409, "任务类型不匹配。")
    if version["source_state"] != "cached":
        credential = _lock(conn, "teacher_smart_classroom_credentials", _int(ctx["binding"].get("credential_id")))
        from .smart_classroom_attendance_adapter import external_account_key
        if (not credential or credential["teacher_id"] != ctx["binding"]["owner_teacher_id"]
                or not credential.get("enabled") or credential.get("last_status") != "verified"
                or external_account_key(credential) != ctx["binding"]["external_account_key"]):
            raise HTTPException(409, "智慧课堂账号已改变，请重新选择来源；原有归档保留。")
    file_hash = str(source.get("file_hash") or "")
    if version["source_state"] == "cached":
        if version["source_file_hash"] != file_hash:
            raise HTTPException(409, "已缓存原件不可覆盖。")
    else:
        bind_global_file_references(conn, [file_hash])
        manifest = source.get("checkin_manifest") or {}
        conn.execute("UPDATE attendance_report_versions SET source_file_hash=?,source_byte_size=?,source_page_count=?,source_filename=?,request_manifest_json=?,checkin_manifest_json=?,manifest_fingerprint=?,fetched_at=?,source_exported_at=?,source_state='cached',error_code='',safe_message='' WHERE id=?",
                     (file_hash, source.get("byte_size"), source.get("page_count"), str(source.get("filename") or "点名记录.pdf"),
                      _json(source.get("request_manifest") or {}), _json(manifest), _hash(manifest), _now(), source.get("source_exported_at"), version["id"]))
        conn.execute("UPDATE attendance_reports SET latest_source_version_id=?,updated_at=?,revision=revision+1 WHERE id=?", (version["id"], _now(), report["id"]))
    if not config.ATTENDANCE_PARSE_ENABLED:
        return {"completed": True, "report_id": report["id"], "source_version_id": version["id"], "parse_run_id": None, "parse_state": "disabled"}
    return enqueue_attendance_parse(conn, {"id": ctx["payload"]["teacher_id"], "role": "teacher"}, report_id=report["id"], source_version_id=version["id"], idempotency_key=f"auto:{version['id']}")


def _local_maps(conn, run: dict) -> tuple[dict, list]:
    offering_id = run.get("mapped_offering_id")
    if not offering_id:
        return {}, []
    rows = conn.execute("SELECT s.id,s.student_id_number FROM students s JOIN class_offerings o ON "
                        "(s.class_id=o.class_id OR EXISTS(SELECT 1 FROM class_offering_class_links l WHERE l.offering_id=o.id AND l.class_id=s.class_id)) WHERE o.id=?", (offering_id,)).fetchall()
    numbers = {}
    for row in rows:
        numbers.setdefault(_norm(row["student_id_number"]), []).append(int(row["id"]))
    sessions = [dict(r) for r in conn.execute("SELECT * FROM class_offering_sessions WHERE class_offering_id=?", (offering_id,)).fetchall()]
    return numbers, sessions


def _match_local_session(item: dict, sessions: list) -> int | None:
    date = str(item.get("source_datetime") or "")[:10]
    if not date:
        return None
    matches = [s for s in sessions if str(s.get("session_date") or "") == date]
    if item.get("section"):
        section = _int(item["section"])
        narrowed = []
        for row in matches:
            numbers = [int(n) for n in re.findall(r"\d+", str(row.get("academic_section_text") or ""))]
            if numbers and (section in numbers or len(numbers) == 2 and min(numbers) <= section <= max(numbers)):
                narrowed.append(row)
        matches = narrowed
    return int(matches[0]["id"]) if len(matches) == 1 else None


def validate_attendance_run(conn, run_id: int) -> dict:
    run = dict(conn.execute("SELECT * FROM attendance_parse_runs WHERE id=?", (run_id,)).fetchone())
    version = conn.execute("SELECT * FROM attendance_report_versions WHERE id=?", (run["source_version_id"],)).fetchone()
    students = [dict(r) for r in conn.execute("SELECT * FROM attendance_report_students WHERE parse_run_id=?", (run_id,)).fetchall()]
    sessions = [dict(r) for r in conn.execute("SELECT * FROM attendance_report_sessions WHERE parse_run_id=?", (run_id,)).fetchall()]
    counts = {r["normalized_status"]: int(r["n"]) for r in conn.execute("SELECT normalized_status,COUNT(*) AS n FROM attendance_report_cells WHERE parse_run_id=? GROUP BY normalized_status", (run_id,)).fetchall()}
    cell_count = sum(counts.values())
    unknown = sum(n for status, n in counts.items() if status not in VALID_STATUSES | {"NOT_APPLICABLE"})
    conflict = conn.execute("SELECT COUNT(*) FROM attendance_report_cells WHERE parse_run_id=? AND quality_state IN ('unknown','conflict')", (run_id,)).fetchone()[0]
    prior = _read(run["validation_json"])
    blockers = list(prior.get("parser_blockers") or [])
    warnings = list(prior.get("parser_warnings") or [])
    def block(code, message):
        blockers.append({"code": code, "message": message})
    if not students or not sessions or cell_count != len(students) * len(sessions):
        block("matrix_incomplete", "学生、点名与单元格覆盖不完整。")
    numbers = [_norm(s["student_number"]) for s in students]
    if any(not n for n in numbers) or len(set(numbers)) != len(numbers):
        block("student_identity", "存在缺失或重复学号，请逐行核实。")
    local_ids = [s["local_student_id"] for s in students if s["local_student_id"]]
    if len(set(local_ids)) != len(local_ids):
        block("duplicate_local_student", "多行映射到同一本地学生，请先复核身份映射。")
    remote_ids = [str(s["remote_checkin_id"]) for s in sessions if s["remote_checkin_id"]]
    if len(remote_ids) != len(set(remote_ids)):
        block("duplicate_checkin", "多个列指向同一次远端点名。")
    if any(not s["source_header"] and not s["source_datetime"] for s in sessions):
        block("missing_session_header", "存在无法识别的点名列。")
    if unknown:
        block("unknown_status", f"还有 {unknown} 个单元格状态未确定。")
    if conflict:
        block("unresolved_evidence", f"还有 {conflict} 个单元格证据待核对。")
    if not run["ai_used"]:
        block("ai_unavailable", "尚未完成 AI 解析核验。")
    coverage, ai_coverage = _read(run["coverage_json"]), _read(run["ai_coverage_json"])
    expected_pages = int(version["source_page_count"] or 0)
    processed = set(coverage.get("processed_pages") or [])
    ai_processed = set(ai_coverage.get("processed_pages") or [])
    if expected_pages <= 0 or processed != set(range(1, expected_pages + 1)):
        block("page_coverage", "未证明全部原件页面已处理。")
    if run["ai_used"] and ai_processed != set(range(1, expected_pages + 1)):
        block("ai_page_coverage", "AI 核验尚未覆盖全部页面。")
    unmapped_students = sum(not s["local_student_id"] or s["identity_state"] != "matched" for s in students)
    unmapped_sessions = sum(not s["local_session_id"] or s["mapping_state"] != "matched" for s in sessions)
    if unmapped_students or unmapped_sessions:
        warnings.append({"code": "local_mapping", "message": f"{unmapped_students} 名学生、{unmapped_sessions} 次点名未映射到本地；相应成绩消费不可用。"})
    return {"blockers": blockers, "warnings": warnings, "parser_blockers": prior.get("parser_blockers") or [],
            "parser_warnings": prior.get("parser_warnings") or [], "student_count": len(students), "session_count": len(sessions),
            "cell_count": cell_count, "unknown_count": unknown, "conflict_count": int(conflict),
            "unmapped_student_count": unmapped_students, "unmapped_session_count": unmapped_sessions,
            "status_counts": counts, "can_confirm": not blockers}


def save_attendance_parse_result(conn, job_id: int, lease_token: str, result: dict) -> dict:
    ctx = load_attendance_job_context(conn, job_id, lease_token)
    run = ctx["parse_run"]
    if run is None:
        raise HTTPException(409, "任务类型不匹配。")
    run_id = run["id"]
    students, sessions, cells = result.get("students") or [], result.get("sessions") or [], result.get("cells") or []
    if len(students) > 10000 or len(sessions) > 500 or len(cells) > 500000:
        raise HTTPException(422, "签到表超出安全处理规模。")
    # The entire candidate replacement is one atomic publication. No reader
    # can observe a partial matrix; an expired worker cannot alter a reviewed run.
    conn.execute("DELETE FROM attendance_report_cells WHERE parse_run_id=?", (run_id,))
    conn.execute("DELETE FROM attendance_report_students WHERE parse_run_id=?", (run_id,))
    conn.execute("DELETE FROM attendance_report_sessions WHERE parse_run_id=?", (run_id,))
    local_numbers, local_sessions = _local_maps(conn, run)
    row_ids, column_ids = {}, {}
    for item in students:
        index = _int(item.get("row_index"), -1)
        if index < 0 or index in row_ids:
            raise HTTPException(422, "解析产生重复或无效学生行。")
        number = str(item.get("student_number") or "").strip()
        matches = local_numbers.get(_norm(number), [])
        local_id = matches[0] if len(matches) == 1 else None
        row_ids[index] = _insert(conn, "attendance_report_students", {"parse_run_id": run_id, "row_index": index,
            "student_number": number, "source_name": str(item.get("source_name") or ""), "source_class_name": str(item.get("source_class_name") or ""),
            "local_student_id": local_id, "identity_state": "matched" if local_id else "unmapped",
            "source_page": item.get("source_page"), "bbox_json": _json(item.get("bbox") or []),
            "raw_identity_json": _json(item.get("raw_identity") or {})})
    for item in sessions:
        index = _int(item.get("column_index"), -1)
        if index < 0 or index in column_ids:
            raise HTTPException(422, "解析产生重复或无效点名列。")
        local_id = _match_local_session(item, local_sessions)
        column_ids[index] = _insert(conn, "attendance_report_sessions", {"parse_run_id": run_id, "column_index": index,
            "source_header": str(item.get("source_header") or ""), "source_datetime": item.get("source_datetime"),
            "time_precision": str(item.get("time_precision") or "minute"), "remote_checkin_id": item.get("remote_checkin_id"),
            "local_session_id": local_id, "mapping_state": "matched" if local_id else "unmapped",
            "week_index": item.get("week_index"), "weekday": item.get("weekday"), "section": item.get("section"),
            "evidence_json": _json(item.get("evidence") or {})})
    seen = set()
    values = []
    for item in cells:
        key = (_int(item.get("row_index"), -1), _int(item.get("column_index"), -1))
        if key[0] not in row_ids or key[1] not in column_ids or key in seen:
            raise HTTPException(422, "解析产生无归属或重复单元格。")
        seen.add(key)
        status = str(item.get("normalized_status") or "UNKNOWN").upper()
        quality = str(item.get("quality_state") or ("verified" if status in VALID_STATUSES else "unknown"))
        # AI cannot waive attendance applicability; N/A requires explicit review.
        if status not in VALID_STATUSES:
            status, quality = "UNKNOWN", "unknown"
        if quality not in {"verified", "unknown", "conflict"}:
            quality = "unknown"
        values.append((run_id, row_ids[key[0]], column_ids[key[1]], str(item.get("raw_text") or ""), str(item.get("raw_status") or ""), status, quality,
                       str(item.get("interpretation_method") or ""), item.get("model_confidence"), item.get("evidence_page"), _json(item.get("bbox") or []), item.get("api_status"),
                       str(item.get("evidence_fingerprint") or _hash({"source": ctx["source_version"]["source_file_hash"], "row": key[0], "column": key[1], "raw": item.get("raw_text")})), item.get("ai_status")))
    conn.executemany("INSERT INTO attendance_report_cells(parse_run_id,student_row_id,session_column_id,raw_text,raw_status,normalized_status,quality_state,interpretation_method,model_confidence,evidence_page,bbox_json,api_status,evidence_fingerprint,ai_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
    parser_validation = result.get("validation") or {}
    conn.execute("UPDATE attendance_parse_runs SET parser_version=?,prompt_version=?,model_id=?,schema_version=?,ai_used=?,ai_coverage_json=?,coverage_json=?,validation_json=?,semantic_fingerprint=?,revision=revision+1,finished_at=? WHERE id=?",
                 (str(result.get("parser_version") or "v1"), str(result.get("prompt_version") or "v1"), str(result.get("model_id") or ""), str(result.get("schema_version") or "v1"),
                  int(bool(result.get("ai_used"))), _json(result.get("ai_coverage") or {}), _json(result.get("coverage") or {}),
                  _json({"parser_blockers": parser_validation.get("blockers") or [], "parser_warnings": parser_validation.get("warnings") or []}),
                  str(result.get("semantic_fingerprint") or _hash({"students": students, "sessions": sessions, "cells": cells})), _now(), run_id))
    validation = validate_attendance_run(conn, run_id)
    state = "validated" if validation["can_confirm"] else "needs_review"
    conn.execute("UPDATE attendance_parse_runs SET validation_json=?,state=? WHERE id=?", (_json(validation), state, run_id))
    conn.execute("UPDATE attendance_reports SET updated_at=? WHERE id=?", (_now(), ctx["report"]["id"]))
    return {"parse_run_id": run_id, "state": state, "validation": validation}


def mark_attendance_job_failure(conn, job_id: int, lease_token: str, message: str, *, error_code="processing_failed") -> None:
    job = _lock(conn, "ai_jobs", job_id)
    if not job or job.get("lease_token") != lease_token or job["status"] not in {"running", "retry_wait", "dead_letter", "review_required"}:
        return
    payload = _read(job["payload_json"])
    # Never write remote response bodies, credential-bearing URLs, or tokens.
    safe_message = "签到处理未完成，请查看任务状态并重试。"
    if job["task_type"] == "attendance_export":
        conn.execute("UPDATE attendance_report_versions SET source_state='failed',error_code=?,safe_message=? WHERE id=? AND source_state IN ('queued','exporting','failed')", (error_code, safe_message, payload["source_version_id"]))
    elif job["task_type"] == "attendance_parse":
        conn.execute("UPDATE attendance_parse_runs SET state='failed',error_code=?,safe_message=?,finished_at=? WHERE id=? AND state IN ('queued','parsing','failed')", (error_code, safe_message, _now(), payload["parse_run_id"]))


def _serialize_run(row) -> dict:
    item = dict(row)
    for column in ("validation", "coverage", "ai_coverage"):
        item[column] = _read(item.pop(column + "_json"))
    item["ai_used"] = bool(item["ai_used"])
    return item


def review_attendance_run(conn, user: dict, *, report_id: int, run_id: int, target_type: str, target_id: int,
                          changes: dict, reason: str, expected_revision: int):
    report = get_attendance_report(conn, report_id, user, lock=True)
    run = _run(conn, report_id, run_id, user, lock=True)
    _check_revision(run["revision"], expected_revision)
    if run["state"] not in {"needs_review", "validated"}:
        raise HTTPException(409, "只可修订已完成解析的候选；已确认版本请重新解析后更正。")
    reason = str(reason or "").strip()
    if not reason or len(reason) > 2000:
        raise HTTPException(422, "请填写明确的复核理由。")
    allowed = {"cell": ("attendance_report_cells", {"normalized_status", "quality_state"}),
               "student": ("attendance_report_students", {"student_number", "source_name", "source_class_name", "local_student_id"}),
               "session": ("attendance_report_sessions", {"source_datetime", "source_header", "remote_checkin_id", "local_session_id"})}
    if target_type not in allowed or not isinstance(changes, dict):
        raise HTTPException(422, "复核目标无效。")
    table, fields = allowed[target_type]
    if not changes or set(changes) - (fields | {"applicability_evidence"}):
        raise HTTPException(422, "存在不允许修改的字段。")
    row = conn.execute(f"SELECT * FROM {table} WHERE id=? AND parse_run_id=?", (target_id, run_id)).fetchone()
    if not row:
        raise HTTPException(404, "复核目标不存在。")
    before = dict(row)
    updates = {k: v for k, v in changes.items() if k in fields}
    if target_type == "cell":
        status = str(updates.get("normalized_status", before["normalized_status"]))
        quality = str(updates.get("quality_state") or "verified")
        if status not in ALL_STATUSES or quality not in {"verified", "unknown", "conflict", "resolved_historical_difference"}:
            raise HTTPException(422, "签到状态或证据状态无效。")
        if status == "NOT_APPLICABLE" and not str(changes.get("applicability_evidence") or "").strip():
            raise HTTPException(422, "不适用必须提供名册或适用范围证据。")
        updates.update(normalized_status=status, quality_state=quality, interpretation_method="human_review", revision=before["revision"] + 1)
    elif target_type == "student":
        local_id = updates.get("local_student_id", before["local_student_id"])
        number = _norm(updates.get("student_number", before["student_number"]))
        local_map, _ = _local_maps(conn, run)
        if local_id and _int(local_id) not in local_map.get(number, []):
            raise HTTPException(422, "学生不在目标课堂或学号不一致。")
        updates["identity_state"] = "matched" if local_id else "unmapped"
    else:
        local_id = updates.get("local_session_id", before["local_session_id"])
        if local_id and not conn.execute("SELECT 1 FROM class_offering_sessions WHERE id=? AND class_offering_id=?", (local_id, run["mapped_offering_id"])).fetchone():
            raise HTTPException(422, "课次不属于该解析版本的课堂。")
        updates["mapping_state"] = "matched" if local_id else "unmapped"
    conn.execute(f"UPDATE {table} SET " + ",".join(k+"=?" for k in updates) + " WHERE id=? AND parse_run_id=?", (*updates.values(), target_id, run_id))
    conn.execute("UPDATE attendance_parse_runs SET revision=revision+1,state='needs_review' WHERE id=? AND revision=?", (run_id, expected_revision))
    _insert(conn, "attendance_report_reviews", {"parse_run_id": run_id, "target_type": target_type, "target_id": target_id,
          "before_json": _json(before), "after_json": _json({**before, **updates, "applicability_evidence": changes.get("applicability_evidence")}), "reason": reason,
          "actor_id": _actor(user), "expected_revision": expected_revision, "created_at": _now(), "event_type": "review"})
    validation = validate_attendance_run(conn, run_id)
    conn.execute("UPDATE attendance_parse_runs SET validation_json=?,state=? WHERE id=?", (_json(validation), "validated" if validation["can_confirm"] else "needs_review", run_id))
    return {"run": _serialize_run(conn.execute("SELECT * FROM attendance_parse_runs WHERE id=?", (run_id,)).fetchone()), "validation": validation, "report_revision": report["revision"]}


def confirm_attendance_run(conn, user: dict, *, report_id: int, run_id: int, expected_run_revision: int, expected_report_revision: int):
    report = get_attendance_report(conn, report_id, user, lock=True)
    run = _run(conn, report_id, run_id, user, lock=True)
    _check_revision(report["revision"], expected_report_revision)
    _check_revision(run["revision"], expected_run_revision)
    if run["state"] != "validated":
        raise HTTPException(409, "当前解析版本尚未通过核验。")
    validation = validate_attendance_run(conn, run_id)
    if not validation["can_confirm"]:
        raise HTTPException(409, {"message": "仍有阻断项需要处理。", "validation": validation})
    now = _now()
    conn.execute("UPDATE attendance_parse_runs SET state='confirmed',revision=revision+1,confirmed_by=?,confirmed_at=?,validation_json=? WHERE id=? AND revision=?", (_actor(user), now, _json(validation), run_id, expected_run_revision))
    conn.execute("UPDATE attendance_reports SET confirmed_parse_run_id=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?", (run_id, now, report_id, expected_report_revision))
    _insert(conn, "attendance_report_reviews", {"parse_run_id": run_id, "target_type": "run", "target_id": run_id, "before_json": _json({"confirmed_parse_run_id": report["confirmed_parse_run_id"]}),
             "after_json": _json({"confirmed_parse_run_id": run_id}), "reason": "确认当前来源解析版本", "actor_id": _actor(user), "expected_revision": expected_run_revision, "created_at": now, "event_type": "confirm"})
    return {"report_id": report_id, "confirmed_parse_run_id": run_id, "report_revision": expected_report_revision + 1,
            "run": _serialize_run(conn.execute("SELECT * FROM attendance_parse_runs WHERE id=?", (run_id,)).fetchone())}


_REPORT_SELECT = """SELECT r.*,b.owner_teacher_id,b.academic_year,b.academic_term,
    b.remote_course_name AS course_name,b.remote_course_id AS course_code,b.remote_class_name AS teaching_class_name,
    v.id AS source_version_id,v.source_state,p.state AS parse_state,p.id AS latest_parse_run_id,
    (SELECT COUNT(*) FROM attendance_report_students st WHERE st.parse_run_id=p.id) AS student_count,
    (SELECT COUNT(*) FROM attendance_report_sessions se WHERE se.parse_run_id=p.id) AS session_count
    FROM attendance_reports r JOIN smart_attendance_source_bindings b ON b.id=r.binding_id
    LEFT JOIN attendance_report_versions v ON v.id=(SELECT MAX(v1.id) FROM attendance_report_versions v1 WHERE v1.report_id=r.id)
    LEFT JOIN attendance_parse_runs p ON p.id=(SELECT MAX(p1.id) FROM attendance_parse_runs p1 JOIN attendance_report_versions v2 ON v2.id=p1.source_version_id WHERE v2.report_id=r.id)"""


def _list_where(user: dict, filters: dict) -> tuple[list, list]:
    clauses, params = ["b.owner_teacher_id=?", "r.deleted_at IS " + ("NOT NULL" if str(filters.get("deleted") or "0") == "1" else "NULL")], [_actor(user)]
    for key, column in (("year", "b.academic_year"), ("term", "b.academic_term"), ("course", "b.remote_course_id"), ("teaching_class", "b.id")):
        if filters.get(key) not in (None, ""):
            clauses.append(column + "=?"); params.append(filters[key])
    if filters.get("offering") or filters.get("class_offering_id"):
        clauses.append("EXISTS(SELECT 1 FROM smart_attendance_source_offerings l WHERE l.binding_id=b.id AND l.class_offering_id=? AND l.link_state='active')")
        params.append(filters.get("offering") or filters["class_offering_id"])
    q = str(filters.get("q") or "").strip()[:120]
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(b.remote_course_name LIKE ? ESCAPE '\\' OR b.remote_course_id LIKE ? ESCAPE '\\' OR b.remote_class_name LIKE ? ESCAPE '\\')")
        params.extend([pattern] * 3)
    state = str(filters.get("status") or "")
    if state == "processing":
        clauses.append("(v.source_state IN ('queued','exporting') OR p.state IN ('queued','parsing'))")
    elif state == "cached":
        clauses.append("v.source_state='cached'")
    elif state == "failed":
        clauses.append("(v.source_state='failed' OR p.state='failed')")
    elif state in {"needs_review", "validated", "confirmed"}:
        clauses.append("p.state=?"); params.append(state)
    return clauses, params


def list_attendance_reports(conn, user: dict, **filters):
    clauses, params = _list_where(user, filters)
    query = _REPORT_SELECT + " WHERE " + " AND ".join(clauses)
    total = int(conn.execute("SELECT COUNT(*) FROM (" + query + ") filtered", params).fetchone()[0])
    page, size = max(1, _int(filters.get("page"), 1)), max(1, min(100, _int(filters.get("page_size"), 25)))
    sort = {"updated_desc": "r.updated_at DESC,r.id DESC", "updated_asc": "r.updated_at,r.id", "course_asc": "b.remote_course_name,b.remote_class_name,r.id"}.get(filters.get("sort"), "r.updated_at DESC,r.id DESC")
    rows = conn.execute(query + " ORDER BY " + sort + " LIMIT ? OFFSET ?", (*params, size, (page-1)*size)).fetchall()
    return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": size, "applied_filters": filters}


def attendance_report_options(conn, user: dict, **filters):
    # Facets only reflect records this owner can access, including empty states.
    clauses, params = _list_where(user, {k: v for k, v in filters.items() if k in {"year", "term", "offering", "deleted"}})
    rows = conn.execute("SELECT DISTINCT b.academic_year,b.academic_term,b.remote_course_id,b.remote_course_name,b.id,b.remote_class_name FROM attendance_reports r JOIN smart_attendance_source_bindings b ON b.id=r.binding_id WHERE " + " AND ".join(clauses), params).fetchall()
    all_rows = conn.execute("SELECT DISTINCT b.academic_year,b.academic_term FROM attendance_reports r JOIN smart_attendance_source_bindings b ON b.id=r.binding_id WHERE b.owner_teacher_id=? AND r.deleted_at IS " + ("NOT NULL" if str(filters.get("deleted") or "0") == "1" else "NULL"), (_actor(user),)).fetchall()
    teaching_rows = [r for r in rows if not filters.get("course") or str(r["remote_course_id"]) == str(filters["course"])]
    return {"years": sorted({r["academic_year"] for r in all_rows}, reverse=True), "terms": sorted({int(r["academic_term"]) for r in all_rows if not filters.get("year") or r["academic_year"] == filters["year"]}),
            "courses": [{"value": k, "label": v} for k, v in sorted({r["remote_course_id"]: r["remote_course_name"] for r in rows}.items())],
            "teaching_classes": [{"value": r["id"], "label": r["remote_class_name"]} for r in teaching_rows]}


def attendance_report_detail(conn, user: dict, report_id: int):
    report = get_attendance_report(conn, report_id, user, allow_deleted=True)
    display = dict(conn.execute(_REPORT_SELECT + " WHERE r.id=?", (report_id,)).fetchone())
    versions = []
    for raw in conn.execute("SELECT * FROM attendance_report_versions WHERE report_id=? ORDER BY version_no DESC", (report_id,)).fetchall():
        item = dict(raw)
        # Raw detail manifests contain whole rosters and remain worker-only.
        item.pop("checkin_manifest_json", None); item.pop("request_manifest_json", None)
        item["source_url"] = f"/api/attendance-reports/{report_id}/versions/{item['id']}/source.pdf" if item["source_state"] == "cached" else None
        versions.append(item)
    runs = [_serialize_run(row) for row in conn.execute("SELECT p.* FROM attendance_parse_runs p JOIN attendance_report_versions v ON v.id=p.source_version_id WHERE v.report_id=? ORDER BY p.id DESC", (report_id,)).fetchall()]
    jobs = [_public_job(row) for row in conn.execute("SELECT * FROM ai_jobs WHERE source_ref=? AND owner_role='teacher' AND owner_user_pk=? AND task_type IN ('attendance_export','attendance_parse') ORDER BY id DESC LIMIT 30", (f"attendance_report:{report_id}", _actor(user))).fetchall()]
    return {"report": display, "binding": serialize_binding(conn, _binding(conn, report["binding_id"], user)), "versions": versions, "runs": runs, "jobs": jobs,
            "active_run_id": runs[0]["id"] if runs else None}


def attendance_run_students(conn, user: dict, report_id: int, run_id: int, *, q="", page=1, page_size=50, status="", quality_state="", **unused):
    run = _run(conn, report_id, run_id, user)
    where, params = ["s.parse_run_id=?"], [run_id]
    if q:
        pattern = "%" + str(q)[:120].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(s.student_number LIKE ? ESCAPE '\\' OR s.source_name LIKE ? ESCAPE '\\')"); params.extend([pattern, pattern])
    if status == "incomplete":
        where.append("EXISTS(SELECT 1 FROM attendance_report_cells c WHERE c.student_row_id=s.id AND (c.normalized_status='UNKNOWN' OR c.quality_state IN ('unknown','conflict')))")
    elif status in ALL_STATUSES:
        where.append("EXISTS(SELECT 1 FROM attendance_report_cells c WHERE c.student_row_id=s.id AND c.normalized_status=?)"); params.append(status)
    if quality_state in {"unknown", "conflict", "verified", "resolved_historical_difference"}:
        where.append("EXISTS(SELECT 1 FROM attendance_report_cells c WHERE c.student_row_id=s.id AND c.quality_state=?)"); params.append(quality_state)
    sql = " FROM attendance_report_students s WHERE " + " AND ".join(where)
    total = conn.execute("SELECT COUNT(*)" + sql, params).fetchone()[0]
    page, size = max(1, _int(page, 1)), max(1, min(100, _int(page_size, 50)))
    rows = [dict(r) for r in conn.execute("SELECT s.*" + sql + " ORDER BY s.row_index LIMIT ? OFFSET ?", (*params, size, (page-1)*size)).fetchall()]
    from .attendance_fact_service import student_run_summaries
    summaries = student_run_summaries(conn, run_id, [r["id"] for r in rows])
    numbers, _ = _local_maps(conn, run)
    candidate_ids = sorted({i for row in rows for i in numbers.get(_norm(row["student_number"]), [])})
    candidates = {r["id"]: dict(r) for r in conn.execute("SELECT id,student_id_number,name FROM students WHERE id IN (" + ",".join("?" for _ in candidate_ids) + ")", candidate_ids).fetchall()} if candidate_ids else {}
    for row in rows:
        row["summary"] = summaries.get(row["id"], {})
        row["bbox"] = _read(row.pop("bbox_json"), [])
        row["mapping_candidates"] = [{"id": i, "label": f"{candidates[i]['student_id_number']} · {candidates[i]['name']}（#{i}）"} for i in numbers.get(_norm(row["student_number"]), []) if i in candidates]
    return {"items": rows, "total": total, "page": page, "page_size": size}


def attendance_run_sessions(conn, user: dict, report_id: int, run_id: int, *, page=1, page_size=20):
    run = _run(conn, report_id, run_id, user)
    page, size = max(1, _int(page, 1)), max(1, min(100, _int(page_size, 20)))
    rows = [dict(r) for r in conn.execute("SELECT * FROM attendance_report_sessions WHERE parse_run_id=? ORDER BY column_index LIMIT ? OFFSET ?", (run_id, size, (page-1)*size)).fetchall()]
    _, local = _local_maps(conn, run)
    for row in rows:
        row["evidence"] = _read(row.pop("evidence_json"))
        row["mapping_candidates"] = [{"id": s["id"], "label": f"{s.get('session_date') or ''} {s.get('academic_section_text') or ''} {s.get('title') or s.get('name') or ''}"} for s in local if str(s.get("session_date") or "") == str(row["source_datetime"] or "")[:10]]
    return {"items": rows, "total": conn.execute("SELECT COUNT(*) FROM attendance_report_sessions WHERE parse_run_id=?", (run_id,)).fetchone()[0], "page": page, "page_size": size}


def attendance_run_cells(conn, user: dict, report_id: int, run_id: int, **filters):
    _run(conn, report_id, run_id, user)
    where, params = ["c.parse_run_id=?"], [run_id]
    for key, column in (("student_ids", "c.student_row_id"), ("session_ids", "c.session_column_id")):
        if filters.get(key):
            ids = [_int(i) for i in str(filters[key]).split(",")]
            if not ids or len(ids) > 100 or any(i <= 0 for i in ids):
                raise HTTPException(422, "矩阵窗口标识无效。")
            where.append(column + " IN (" + ",".join("?" for _ in ids) + ")"); params.extend(ids)
    for key, expression in (("row_start", "s.row_index>=?"), ("row_end", "s.row_index<=?"), ("column_start", "se.column_index>=?"), ("column_end", "se.column_index<=?")):
        if filters.get(key) not in (None, ""):
            where.append(expression); params.append(_int(filters[key]))
    rows = conn.execute("SELECT c.*,s.row_index,se.column_index FROM attendance_report_cells c JOIN attendance_report_students s ON s.id=c.student_row_id AND s.parse_run_id=c.parse_run_id JOIN attendance_report_sessions se ON se.id=c.session_column_id AND se.parse_run_id=c.parse_run_id WHERE " + " AND ".join(where) + " ORDER BY s.row_index,se.column_index LIMIT 10001", params).fetchall()
    if len(rows) > 10000:
        raise HTTPException(422, "请缩小矩阵行列窗口。")
    items = []
    for row in rows:
        item = dict(row); item["bbox"] = _read(item.pop("bbox_json"), []); items.append(item)
    return {"items": items, "total": len(items), "filters": filters}


def attendance_run_reviews(conn, user: dict, report_id: int, run_id: int, *, page=1, page_size=50):
    _run(conn, report_id, run_id, user)
    page, size = max(1, _int(page, 1)), max(1, min(100, _int(page_size, 50)))
    items = []
    for row in conn.execute("SELECT * FROM attendance_report_reviews WHERE parse_run_id=? ORDER BY id DESC LIMIT ? OFFSET ?", (run_id, size, (page-1)*size)).fetchall():
        item = dict(row); item["before"] = _read(item.pop("before_json")); item["after"] = _read(item.pop("after_json")); items.append(item)
    return {"items": items, "total": conn.execute("SELECT COUNT(*) FROM attendance_report_reviews WHERE parse_run_id=?", (run_id,)).fetchone()[0], "page": page, "page_size": size}


def cancel_attendance_job(conn, user: dict, job_id: int):
    get_attendance_job(conn, job_id, user)
    row = conn.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
    payload = _read(row["payload_json"])
    get_attendance_report(conn, payload["report_id"], user, lock=True, allow_deleted=True)
    row = _lock(conn, "ai_jobs", job_id)
    if row["status"] in ACTIVE_JOBS:
        cancel_ai_job_by_id(conn, job_id, reason="attendance_cancelled_by_owner")
        if row["task_type"] == "attendance_export":
            conn.execute("UPDATE attendance_report_versions SET source_state='cancelled' WHERE id=? AND source_state IN ('queued','exporting','failed')", (payload["source_version_id"],))
        else:
            conn.execute("UPDATE attendance_parse_runs SET state='cancelled',revision=revision+1 WHERE id=? AND state IN ('queued','parsing','failed')", (payload["parse_run_id"],))
    return get_attendance_job(conn, job_id, user)


def set_attendance_report_deleted(conn, user: dict, report_id: int, *, expected_revision: int, deleted: bool):
    report = get_attendance_report(conn, report_id, user, lock=True, allow_deleted=True)
    _check_revision(report["revision"], expected_revision)
    if deleted:
        # Cancel uses the same transaction: a worker cannot resurrect this row.
        jobs = conn.execute("SELECT id FROM ai_jobs WHERE source_ref=? AND task_type IN ('attendance_export','attendance_parse') AND owner_user_pk=? AND status IN ('queued','running','retry_wait','result_ready')", (f"attendance_report:{report_id}", _actor(user))).fetchall()
        for job in jobs:
            cancel_attendance_job(conn, user, job["id"])
    conn.execute("UPDATE attendance_reports SET deleted_at=?,deleted_by=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?", (_now() if deleted else None, _actor(user) if deleted else None, _now(), report_id, expected_revision))
    return {"report_id": report_id, "deleted": deleted, "revision": expected_revision + 1}


def attendance_source_file(conn, user: dict, report_id: int, version_id: int):
    get_attendance_report(conn, report_id, user, allow_deleted=True)
    row = conn.execute("SELECT * FROM attendance_report_versions WHERE id=? AND report_id=? AND source_state='cached'", (version_id, report_id)).fetchone()
    if not row:
        raise HTTPException(404, "原件尚未缓存或版本不存在。")
    path = resolve_global_file_path(row["source_file_hash"])
    if not path:
        raise HTTPException(404, "原件暂不可用，请联系管理员核查存储。")
    return path, dict(row)


def completed_attendance_job_result(conn, job_id: int, lease_token: str) -> dict | None:
    """Recover an already-published business result before repeating I/O."""
    raw = conn.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
    if not raw:
        raise HTTPException(409, "任务已失效。")
    payload = _read(raw["payload_json"])
    report = get_attendance_report(conn, payload["report_id"], {"id": payload["teacher_id"], "role": "teacher"}, lock=True)
    job = _lock(conn, "ai_jobs", job_id)
    if job["status"] != "running" or not lease_token or job["lease_token"] != lease_token or not job["lease_expires_at"] or job["lease_expires_at"] <= datetime.now().isoformat(timespec="seconds"):
        raise HTTPException(409, "任务租约已失效。")
    version = conn.execute("SELECT * FROM attendance_report_versions WHERE id=? AND report_id=?", (payload["source_version_id"], report["id"])).fetchone()
    if not version:
        raise HTTPException(409, "任务原件版本失效。")
    if job["task_type"] == "attendance_export" and version["source_state"] == "cached":
        run = conn.execute("SELECT id,job_id FROM attendance_parse_runs WHERE source_version_id=? ORDER BY id LIMIT 1", (version["id"],)).fetchone()
        if run:
            return {"completed": True, "report_id": report["id"], "source_version_id": version["id"], "parse_run_id": run["id"], "parse_job_id": run["job_id"]}
        return {"completed": True, "report_id": report["id"], "source_version_id": version["id"], "parse_run_id": None, "parse_state": "not_requested"}
    if job["task_type"] == "attendance_parse":
        run = conn.execute("SELECT * FROM attendance_parse_runs WHERE id=? AND source_version_id=?", (payload["parse_run_id"], version["id"])).fetchone()
        if run and run["state"] in {"needs_review", "validated", "confirmed"}:
            return {"completed": True, "report_id": report["id"], "parse_run_id": run["id"], "state": run["state"], "validation": _read(run["validation_json"])}
    return None


def select_attendance_grade_source(conn, user: dict, binding_id: int, *, expected_revision: int):
    binding = _binding(conn, binding_id, user)
    link = conn.execute("SELECT * FROM smart_attendance_source_offerings WHERE binding_id=? AND link_state='active'", (binding_id,)).fetchone()
    if not link or not link["class_offering_id"]:
        raise HTTPException(409, "该来源尚未关联课堂。")
    # One classroom lock serializes selection across different source bindings.
    _lock(conn, "class_offerings", int(link["class_offering_id"]))
    binding = _binding(conn, binding_id, user, lock=True)
    _check_revision(binding["revision"], expected_revision)
    current_link = conn.execute("SELECT * FROM smart_attendance_source_offerings WHERE binding_id=? AND link_state='active'", (binding_id,)).fetchone()
    if not current_link or current_link["id"] != link["id"] or current_link["class_offering_id"] != link["class_offering_id"]:
        raise HTTPException(409, "来源课堂关联已改变，请刷新后重试。")
    report = conn.execute("SELECT id FROM attendance_reports WHERE binding_id=? AND deleted_at IS NULL", (binding_id,)).fetchone()
    if not report:
        raise HTTPException(409, "该来源尚无可用归档。")
    from .attendance_fact_service import load_confirmed_attendance_facts
    facts = load_confirmed_attendance_facts(conn, class_offering_id=link["class_offering_id"], teacher_id=_actor(user), report_id=report["id"], require_feature=False)
    if not facts or not facts["available"]:
        raise HTTPException(409, facts["message"] if facts else "请先确认来源解析版本及课堂映射。")
    conn.execute("UPDATE smart_attendance_source_offerings SET is_grade_source=0 WHERE class_offering_id=? AND link_state='active'", (link["class_offering_id"],))
    conn.execute("UPDATE smart_attendance_source_offerings SET is_grade_source=1 WHERE id=? AND link_state='active'", (link["id"],))
    conn.execute("UPDATE smart_attendance_source_bindings SET revision=revision+1,updated_at=? WHERE id=?", (_now(), binding_id))
    return {"binding": serialize_binding(conn, _binding(conn, binding_id, user))}
