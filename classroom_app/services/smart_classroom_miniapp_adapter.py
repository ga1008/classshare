"""Fixed GXUFL mini-program contract. No caller-selected URL or credentials.

Student writes and start/stop were independently read back on 2026-11-09,
with temporary records removed and original attendance verified unchanged.
"""
from __future__ import annotations

import asyncio
import json
import re

from fastapi import HTTPException

from .smart_classroom_attendance_adapter import AttendanceSourceError, _check_response, REQUEST_INTERVAL

PREFIX = "/teaching/teacherSchedule"
LIFECYCLE_VERIFIED = True  # Controlled server start/view/stop/view, 2026-11-09.
STUDENT_WRITE_VERIFIED = True
STATUSES = frozenset({"CHECKED", "UNCHECKED", "SICK_LEAVE", "PERSONAL_LEAVE", "LATE_OR_EARLY", "STUDY_TOUR", "PRACTICE", "VOLUNTEER"})


async def _post(client, operation, data):
    if operation not in {"list", "view", "viewCheckinCoursesRecords", "viewCheckinCoursesCount", "changeStuStatus", "startCheckin", "stopCheckin"}:
        raise ValueError("Unsupported school operation")
    await asyncio.sleep(REQUEST_INTERVAL)
    response = await client.post(PREFIX + "/" + operation, data=data)
    _check_response(response)
    try:
        result = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise AttendanceSourceError("invalid_response", "智慧课堂返回格式异常，需要回读核对。") from exc
    if isinstance(result, dict) and (result.get("state") == "fail" or result.get("errNo") not in (None, 0, "0")):
        # Deliberately do not expose upstream messages containing account data.
        raise AttendanceSourceError("remote_rejected", "智慧课堂未接受该操作，原有状态保留待核对。")
    if operation in {"changeStuStatus", "stopCheckin"} and (not isinstance(result, dict) or result.get("state") != "ok"):
        raise AttendanceSourceError("unconfirmed_response", "智慧课堂未返回可确认的写入结果，需要回读核对。")
    if operation == "startCheckin" and (not isinstance(result, dict) or not result.get("id") or any(
            str(result.get(k)) != str(data[k]) for k in ("teacherScheduleId", "week", "dayOfWeek", "section"))):
        raise AttendanceSourceError("unconfirmed_response", "学校开始请求未返回确切课次，必须回读核对。")
    return result


def student_map(rows):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise AttendanceSourceError("invalid_roster", "远端名单格式异常或超过核对范围。")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise AttendanceSourceError("invalid_roster", "远端名单格式异常。")
        number = str(row.get("no") or "").strip()
        if not number or number in result:
            raise AttendanceSourceError("duplicate_student", "远端学号缺失或重复，已停止写入。")
        result[number] = str(row.get("status") or "")
    return result


def sections(value):
    """Normalize the verified numeric section display, without guessing IDs."""
    if isinstance(value, list):
        return tuple(sorted(set(int(n) for n in value)))
    text = str(value or "").strip()
    if re.fullmatch(r"\d+\s*[-~～至]\s*\d+", text):
        first, last = [int(n) for n in re.findall(r"\d+", text)]
        return tuple(range(first, last + 1)) if 1 <= first <= last <= 20 else ()
    if re.fullmatch(r"[\d\s,，、节]+", text):
        return tuple(sorted(set(int(n) for n in re.findall(r"\d+", text))))
    return ()


def validate_view(payload, slot, source, expected_checkin_id=None):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise AttendanceSourceError("invalid_slot", "智慧课堂课次信息不完整。")
    schedule = payload["data"]
    checks = ((schedule.get("id"), slot["schedule_id"]),
              (schedule.get("courseCode"), source["course_code"]),
              (payload.get("year", schedule.get("year")), source["academic_year"]),
              (payload.get("semester", schedule.get("semester")), source["academic_term"]))
    if any(str(actual) != str(expected) for actual, expected in checks):
        raise AttendanceSourceError("slot_identity_changed", "远端学期、课程或真实排课标识已变化，已停止同步。")
    if sections(schedule.get("sections")) != tuple(slot["sections"]):
        raise AttendanceSourceError("slot_section_changed", "远端节次与本地课次不一致。")
    record = payload.get("checkinCourse")
    if record:
        for field, expected in (("teacherScheduleId", slot["schedule_id"]), ("week", slot["week"]),
                                ("dayOfWeek", slot["day_of_week"]), ("section", slot["section"]),
                                ("year", source["academic_year"]), ("semester", source["academic_term"]),
                                ("courseId", source["course_code"]), ("claId", source["remote_class_id"])):
            if str(record.get(field)) != str(expected):
                raise AttendanceSourceError("round_identity_changed", "远端点名范围已变化，未用最新点名替代原轮次。")
        if not str(record.get("id") or ""):
            raise AttendanceSourceError("missing_round_id", "远端点名缺少标识。")
    if expected_checkin_id and (not record or str(record["id"]) != str(expected_checkin_id)):
        raise AttendanceSourceError("round_replaced", "学校官方端已删除或重新发起点名，原绑定已暂停。")
    students = student_map(payload.get("stuList"))
    return {"record": record, "students": students,
            "student_names": {str(r["no"]).strip(): str(r.get("name") or "") for r in payload["stuList"]},
            "can_checkin": payload.get("isCanCheckin") is True, "schedule": schedule}


async def read_slot(client, slot, source, expected_checkin_id=None):
    payload = await _post(client, "view", {"id": slot["schedule_id"], "week": slot["week"],
                        "dayOfWeek": slot["day_of_week"], "section": slot["section"]})
    try:
        return validate_view(payload, slot, source, expected_checkin_id)
    except AttendanceSourceError as exc:
        if exc.code != 'round_identity_changed':
            raise
        # Some official timetable rows return an older internal schedule id on
        # their checkin. Only that id mismatch is eligible for proof by the
        # requested row's own history; every other scope dimension stays exact.
        snapshot = validate_view({**payload, 'checkinCourse': None}, slot, source)
        record = payload.get('checkinCourse') or {}
        scope = {'week': slot['week'], 'dayOfWeek': slot['day_of_week'], 'section': slot['section'],
                 'year': source['academic_year'], 'semester': source['academic_term'],
                 'courseId': source['course_code'], 'claId': source['remote_class_id']}
        if not record.get('id') or str(record.get('teacherScheduleId')) == str(slot['schedule_id']) or any(str(record.get(k)) != str(v) for k,v in scope.items()):
            raise exc
        if expected_checkin_id is not None and str(record['id']) != str(expected_checkin_id):
            raise AttendanceSourceError('round_replaced', '学校官方端已删除或重新发起点名，原绑定已暂停。') from exc
        history = await read_history(client, slot['schedule_id'])
        candidates = [r for r in history if all(str(r.get(k)) == str(v) for k,v in scope.items())]
        if len(candidates) != 1 or str(candidates[0].get('id')) != str(record['id']) or str(candidates[0].get('teacherScheduleId')) != str(record.get('teacherScheduleId')):
            raise AttendanceSourceError('round_identity_changed', '学校排课历史未唯一证明本轮点名归属，已停止同步。') from exc
        snapshot.update(record=record, students=await read_records(client, record['id']), can_checkin=False,
                        record_scope_evidence={'kind':'requested_schedule_history','schedule_id':str(slot['schedule_id']),
                                               'remote_checkin_id':str(record['id']), 'record_schedule_id':str(record['teacherScheduleId'])})
        return snapshot


def validate_alignment_view(payload, slot, source):
    """Read-only course roster discovery, separate from exact-round authority.

    Validate the queried timetable and teaching group. A view's different
    rollcall is discarded, so it cannot authorize a create/stop/student write.
    """
    if not isinstance(payload, dict):
        raise AttendanceSourceError('invalid_slot', '智慧课堂课次信息不完整。')
    snapshot = validate_view({**payload, 'checkinCourse': None}, slot, source)
    record = payload.get('checkinCourse')
    if not record:
        return snapshot
    group = {'year':source['academic_year'], 'semester':source['academic_term'],
             'courseId':source['course_code'], 'claId':source['remote_class_id']}
    if not isinstance(record,dict) or any(str(record.get(k)) != str(v) for k,v in group.items()):
        raise AttendanceSourceError('round_identity_changed', '学校返回的名单不属于所选学期课程或教学班，未用于对齐。')
    try:
        return validate_view(payload, slot, source)
    except AttendanceSourceError as exc:
        if exc.code != 'round_identity_changed':
            raise
        snapshot.update(record=None, can_checkin=False,
                        alignment_note='学校名单已核对；返回点名的排课标识或课次不同，已排除该点名，推送时须重新精确核实。')
        return snapshot


async def read_alignment_slot(client, slot, source):
    payload = await _post(client, 'view', {'id':slot['schedule_id'], 'week':slot['week'],
                                         'dayOfWeek':slot['day_of_week'], 'section':slot['section']})
    return validate_alignment_view(payload, slot, source)


async def read_records(client, checkin_id):
    return student_map(await _post(client, "viewCheckinCoursesRecords", {"checkinCourseId": str(checkin_id)}))


async def list_schedules(client):
    return await _post(client, "list", {})


async def read_history(client, schedule_id):
    rows = await _post(client, "viewCheckinCoursesCount", {"teacherScheduleId": str(schedule_id)})
    if not isinstance(rows, list) or len(rows) > 1000 or any(not isinstance(r, dict) or not r.get("id") for r in rows):
        raise AttendanceSourceError("invalid_history", "远端点名历史不完整，未开始新的点名。")
    if len({str(r["id"]) for r in rows}) != len(rows):
        raise AttendanceSourceError("duplicate_history", "远端点名历史出现重复标识。")
    return rows


async def change_student(client, checkin_id, number, status):
    if not STUDENT_WRITE_VERIFIED or status not in STATUSES:
        raise HTTPException(409, "逐人改签协议尚不可用。")
    return await _post(client, "changeStuStatus", {"checkinCourseId": str(checkin_id), "username": str(number), "status": status})


async def start_checkin(client, slot, location):
    if not LIFECYCLE_VERIFIED:
        raise HTTPException(409, "开始点名协议尚未完成真实核实，请先在学校官方端开始点名。")
    return await _post(client, "startCheckin", {"teacherScheduleId": slot["schedule_id"], "week": slot["week"],
        "dayOfWeek": slot["day_of_week"], "section": slot["section"], "latitude": location["latitude"],
        "longitude": location["longitude"], "method": "RANDOM_NO", "duration": 3, "distance": 300})


async def stop_checkin(client, checkin_id):
    if not LIFECYCLE_VERIFIED:
        raise HTTPException(409, "结束点名协议尚未完成真实核实，请先在学校官方端结束点名。")
    return await _post(client, "stopCheckin", {"checkinCourseId": str(checkin_id)})
