"""Read-only JWXT teacher directory: complete cached queries, never course imports."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import date, timedelta

from ..database import get_db_connection
from .academic_integration_service import load_teacher_academic_access_method, open_authenticated_academic_client
from .academic_service import china_now
from .organization_scope_service import load_teacher_org_memberships
from .semester_identity_service import SemesterIdentity, identity_from_semester_record, identity_from_year_term

logger = logging.getLogger(__name__)
SCHOOL = "gxufl"
FILTER_KEYS = ("campus_id", "department_id", "title_id", "college_id", "external")
PROFILE_KEYS = ("teacher_no", "name", "college_id", "college_name", "title_id", "title",
                "department_id", "department_name", "campus_id", "campus_name", "external")


class TimetableQueryError(ValueError):
    def __init__(self, message, *, status="invalid", http_status=400):
        super().__init__(message)
        self.status, self.http_status = status, http_status


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _stamp():
    return china_now().isoformat(timespec="microseconds")


async def _fetch_teacher_timetables(client, **kwargs):
    from .academic_teacher_timetable_adapter import fetch_teacher_timetables
    return await fetch_teacher_timetables(client, **kwargs)


def _scope(conn, teacher_id):
    if not any(str(m.get("school_code") or "").casefold() == SCHOOL
               for m in load_teacher_org_memberships(conn, int(teacher_id))):
        raise TimetableQueryError("当前账号没有该校的教师查询权限。", status="forbidden", http_status=403)


def _terms(conn):
    rows = conn.execute(
        "SELECT id,name,start_date,end_date,week_count FROM academic_semesters WHERE school_code=? ORDER BY start_date DESC,id DESC",
        (SCHOOL,),
    ).fetchall()
    result = {}
    for row in rows:
        item = dict(row)
        identity = identity_from_semester_record(item)
        if identity and identity.code not in result:
            result[identity.code] = {**item, "identity": identity}
    return result


def _term_payload(identity):
    year, term = identity.as_year_term()
    xnm, xqm = identity.as_xnm_xqm()
    return {"year": year, "term": term, "label": identity.canonical_name, "xnm": xnm, "xqm": xqm}


def _default_identity(terms, today):
    current, past = [], []
    for entry in terms.values():
        try:
            start, end = date.fromisoformat(entry["start_date"][:10]), date.fromisoformat(entry["end_date"][:10])
        except (ValueError, TypeError):
            continue
        if start <= today <= end:
            current.append((start, entry["identity"]))
        elif end < today and (today - end).days <= 180:
            past.append((end, entry["identity"]))
    if current:
        return max(current, key=lambda p: p[0])[1]
    if past:
        latest = max(past, key=lambda p: p[0])
        future_starts = [e["start_date"][:10] for e in terms.values() if e["start_date"][:10] > today.isoformat()]
        # A known upcoming term establishes a holiday. Without it, do not let an
        # old calendar suppress the regular September/March rollover forever.
        if future_starts or (today - latest[0]).days <= 70:
            return latest[1]
    if today.month >= 9:
        return SemesterIdentity(today.year, 1)
    if today.month <= 2:
        return SemesterIdentity(today.year - 1, 1)
    return SemesterIdentity(today.year - 1, 2)


def build_teacher_timetable_options(conn, teacher_id, *, today=None):
    _scope(conn, teacher_id)
    today = today or china_now().date()
    known = _terms(conn)
    default = _default_identity(known, today)
    identities = {e["identity"].code: e["identity"] for e in known.values()}
    for year in range(today.year - 4, today.year + 1):
        for term in (1, 2):
            identity = SemesterIdentity(year, term)
            identities.setdefault(identity.code, identity)
    row = conn.execute(
        "SELECT options_json FROM academic_teacher_query_cache WHERE school_code=? ORDER BY synced_at DESC LIMIT 1",
        (SCHOOL,),
    ).fetchone()
    filters = json.loads(row["options_json"]) if row else {}
    for key in ("campuses", "departments", "titles", "colleges"):
        filters.setdefault(key, [])
    filters.setdefault("external", [{"id": "0", "name": "否"}, {"id": "1", "name": "是"}])
    credential = conn.execute(
        "SELECT id FROM teacher_academic_system_credentials WHERE teacher_id=? AND school_code=? AND enabled=1 LIMIT 1",
        (int(teacher_id), SCHOOL),
    ).fetchone()
    return {"status": "success", "school_code": SCHOOL, "default_term": _term_payload(default),
            "terms": [_term_payload(i) for i in sorted(identities.values(), key=lambda i: i.sort_key, reverse=True)],
            "filters": filters, "credential_ready": bool(credential)}


def _normalize_query(conn, teacher_id, payload):
    if not isinstance(payload, dict):
        raise TimetableQueryError("查询条件格式无效。")
    _scope(conn, teacher_id)
    year, term = str(payload.get("year") or ""), str(payload.get("term") or "")
    if year or term:
        identity = identity_from_year_term(year, term)
        if not identity:
            raise TimetableQueryError("请选择有效的学年学期。")
    else:
        identity = _default_identity(_terms(conn), china_now().date())
    q = re.sub(r"\s+", " ", str(payload.get("q") or "").strip())
    if len(q) > 80:
        raise TimetableQueryError("请输入教师姓名或工号（最多80个字符）。")
    if type(payload.get("local_only", False)) is not bool:
        raise TimetableQueryError("本地查询参数无效。")
    filters = {"q": q, **{key: str(payload.get(key) or "").strip() for key in FILTER_KEYS}}
    if any(len(filters[key]) > 120 for key in FILTER_KEYS):
        raise TimetableQueryError("筛选条件过长。")
    if filters["external"] not in ("", "0", "1"):
        raise TimetableQueryError("外聘教师筛选条件无效。")
    primary = payload.get("primary_only", False)
    if primary is not False:
        raise TimetableQueryError("教师查询展示完整课表，不支持仅主讲课程筛选。")
    filters["primary_only"] = primary
    signature = hashlib.sha256(_dump({**filters, "q": q.casefold()}).encode()).hexdigest()
    try:
        page = max(1, int(payload.get("page") or 1))
        page_size = max(1, min(60, int(payload.get("page_size") or 12)))
    except (TypeError, ValueError):
        raise TimetableQueryError("分页参数无效。") from None
    return identity, filters, signature, page, page_size


def _account_links(conn, teacher_nos):
    """Verified exact employee identity only; ambiguous accounts stay unlinked."""
    result = {no: {"linked_teacher_id": None, "account_status": "unlinked"} for no in teacher_nos}
    if not teacher_nos:
        return result
    placeholders = ",".join("?" for _ in teacher_nos)
    rows = conn.execute(
        f"""SELECT DISTINCT c.username,c.teacher_id FROM teacher_academic_system_credentials c
            JOIN teachers t ON t.id=c.teacher_id
            WHERE c.school_code=? AND c.enabled=1 AND c.last_status='verified'
              AND COALESCE(c.last_verified_at,'')<>'' AND COALESCE(t.is_active,1)=1
              AND c.username IN ({placeholders})""", (SCHOOL, *teacher_nos),
    ).fetchall()
    memberships, matches = {}, {}
    for row in rows:
        tid = int(row["teacher_id"])
        if tid not in memberships:
            memberships[tid] = any(m["school_code"] == SCHOOL for m in load_teacher_org_memberships(conn, tid))
        if memberships[tid]:
            matches.setdefault(row["username"], set()).add(tid)
    for no, ids in matches.items():
        result[no] = {"linked_teacher_id": next(iter(ids)) if len(ids) == 1 else None,
                      "account_status": "linked" if len(ids) == 1 else "ambiguous"}
    return result


def _read_cached(conn, identity, signature, page, page_size, *, source="local"):
    coverage = conn.execute(
        "SELECT * FROM academic_teacher_query_cache WHERE school_code=? AND term_code=? AND query_key=?",
        (SCHOOL, identity.code, signature),
    ).fetchone()
    if coverage is None:
        return None
    nos = json.loads(coverage["teacher_nos_json"])
    rows = []
    if nos:
        # Upstream requests are bounded at 100 teachers; no unbounded parameter lists.
        rows = conn.execute(
            f"""SELECT teacher_no,profile_json,summary_json,synced_at FROM academic_teacher_timetables
                WHERE school_code=? AND term_code=? AND teacher_no IN ({','.join('?' for _ in nos)})""",
            (SCHOOL, identity.code, *nos),
        ).fetchall()
        if len(rows) != len(nos):
            return None
    indexed = {r["teacher_no"]: r for r in rows}
    links = _account_links(conn, nos)
    cards = [{**json.loads(indexed[no]["profile_json"]), **links[no],
              "summary": json.loads(indexed[no]["summary_json"]), "synced_at": indexed[no]["synced_at"]}
             for no in nos]
    pages = max(1, (len(cards) + page_size - 1) // page_size)
    page = min(page, pages)
    return {"status": "success", "source": source, "summary_scope": "query", "synced_at": coverage["synced_at"],
            "selected_term": _term_payload(identity), "message": "已从本地缓存查询。" if source == "local" else "已同步教务系统并完成查询。",
            "items": cards[(page - 1) * page_size:page * page_size],
            "pagination": {"page": page, "page_size": page_size, "total_count": len(cards), "total_page": pages},
            "summary": {"teacher_count": len(cards), "linked_count": sum(c["account_status"] == "linked" for c in cards),
                        "course_count": sum(c["summary"]["course_count"] for c in cards),
                        "lesson_count": sum(c["summary"]["lesson_count"] for c in cards)},
            "options": json.loads(coverage["options_json"])}


def _browse_local(conn, identity, filters, page, page_size):
    """Browse complete term snapshots only; remote filter meanings are not inferred."""
    total = conn.execute(
        "SELECT COUNT(*) FROM academic_teacher_timetables WHERE school_code=? AND term_code=?",
        (SCHOOL, identity.code),
    ).fetchone()[0]
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    rows = conn.execute(
        """SELECT teacher_no,profile_json,summary_json,synced_at FROM academic_teacher_timetables
           WHERE school_code=? AND term_code=? ORDER BY synced_at DESC,teacher_no ASC LIMIT ? OFFSET ?""",
        (SCHOOL, identity.code, page_size, (page - 1) * page_size),
    ).fetchall()
    links = _account_links(conn, [r["teacher_no"] for r in rows])
    cards = [{**json.loads(r["profile_json"]), **links[r["teacher_no"]],
              "summary": json.loads(r["summary_json"]), "synced_at": r["synced_at"]} for r in rows]
    ignored = [key for key in FILTER_KEYS if filters[key]]
    return {"status": "success", "source": "local", "mode": "local_browse",
            "sort": "synced_at_desc", "summary_scope": "page", "ignored_filters": ignored,
            "synced_at": cards[0]["synced_at"] if cards else "", "selected_term": _term_payload(identity),
            "message": "已按最近同步时间显示本学期本地课表。" + (" 高级筛选未应用，输入姓名或工号后生效。" if ignored else ""),
            "items": cards, "pagination": {"page": page, "page_size": page_size, "total_count": total, "total_page": pages},
            "summary": {"teacher_count": total, "linked_count": sum(c["account_status"] == "linked" for c in cards),
                        "course_count": sum(c["summary"]["course_count"] for c in cards),
                        "lesson_count": sum(c["summary"]["lesson_count"] for c in cards)},
            "options": {}}


def _lease_key(credential):
    # Two platform accounts may hold the same upstream account. They must share
    # a lease too, otherwise logging in can evict the first request's session.
    return hashlib.sha256(f"{SCHOOL}:{credential['username']}".encode()).hexdigest()


def _claim(conn, credential_key, token, stamp):
    expires = (china_now() + timedelta(minutes=10)).isoformat(timespec="microseconds")
    conn.execute("INSERT INTO academic_teacher_query_leases(credential_key) VALUES(?) ON CONFLICT(credential_key) DO NOTHING",
                 (credential_key,))
    changed = conn.execute(
        """UPDATE academic_teacher_query_leases SET lease_token=?,expires_at=?,updated_at=?
           WHERE credential_key=? AND (lease_token='' OR expires_at<=?)""",
        (token, expires, stamp, credential_key, stamp),
    ).rowcount
    return changed == 1


def _release(conn, credential_key, token):
    conn.execute("UPDATE academic_teacher_query_leases SET lease_token='',expires_at='' WHERE credential_key=? AND lease_token=?",
                 (credential_key, token))


def _normalize_teachers(result):
    if not isinstance(result, dict) or not isinstance(result.get("teachers"), list):
        raise TimetableQueryError("教务系统未返回完整的教师课表。", http_status=502, status="failed")
    if len(result["teachers"]) > 100:
        raise TimetableQueryError("匹配教师过多，请补充姓名或筛选条件。")
    normalized, seen = [], set()
    for teacher in result["teachers"]:
        profile = {key: str(teacher.get(key) or "").strip() for key in PROFILE_KEYS}
        no = profile["teacher_no"]
        if not no or len(no) > 80 or not profile["name"] or no in seen:
            raise ValueError("教师身份缺失或重复")
        seen.add(no)
        if not isinstance(teacher.get("lessons"), list) or not isinstance(teacher.get("schedule_payload"), dict):
            raise ValueError("教师完整课表缺失")
        lessons = []
        for index, raw in enumerate(teacher["lessons"]):
            lesson = dict(raw)
            weeks, sections = lesson.get("weeks"), lesson.get("sections")
            if (not lesson.get("course_name") or not isinstance(weeks, list) or not weeks
                    or not isinstance(sections, list) or not sections
                    or any(type(v) is not int or v < 1 or v > 60 for v in weeks)
                    or any(type(v) is not int or v < 1 or v > 30 for v in sections)
                    or type(lesson.get("weekday")) is not int or not 1 <= lesson["weekday"] <= 7):
                raise ValueError("课程周次或节次数据不完整")
            lesson.update({"id": str(index + 1), "weeks": sorted(set(weeks)), "sections": sorted(set(sections))})
            lessons.append(lesson)
        unscheduled = teacher.get("unscheduled_courses") or []
        if not isinstance(unscheduled, list) or any(not isinstance(i, dict) for i in unscheduled):
            raise ValueError("未排课课程格式无效")
        summary = {"course_count": len({(i.get("course_code"), i["course_name"]) for i in [*lessons, *unscheduled] if i.get("course_name")}),
                   "class_count": len({i.get("class_label") for i in [*lessons, *unscheduled] if i.get("class_label")}),
                   "lesson_count": sum(len(i["weeks"]) for i in lessons),
                   "unscheduled_count": len(unscheduled),
                   "total_hours": sum(len(i["weeks"]) * len(i["sections"]) for i in lessons)}
        snapshot = {"payload": teacher["schedule_payload"], "unscheduled_courses": unscheduled}
        normalized.append((profile, snapshot, lessons, summary))
    return normalized


def _publish(conn, credential_key, token, identity, filters, signature, result, normalized, stamp):
    now = _stamp()
    # UPDATE takes a row lock on PostgreSQL, keeping the lease valid through commit.
    if conn.execute(
        "UPDATE academic_teacher_query_leases SET updated_at=? WHERE credential_key=? AND lease_token=? AND expires_at>?",
        (now, credential_key, token, now),
    ).rowcount != 1:
        raise ValueError("同步租约已失效")
    for profile, snapshot, lessons, summary in normalized:
        conn.execute(
            """INSERT INTO academic_teacher_directory(school_code,teacher_no,name,college_name,title,profile_json,synced_at)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(school_code,teacher_no) DO UPDATE SET
               name=excluded.name,college_name=excluded.college_name,title=excluded.title,
               profile_json=excluded.profile_json,synced_at=excluded.synced_at
               WHERE academic_teacher_directory.synced_at<=excluded.synced_at""",
            (SCHOOL, profile["teacher_no"], profile["name"], profile["college_name"], profile["title"], _dump(profile), stamp),
        )
        conn.execute(
            """INSERT INTO academic_teacher_timetables
               (school_code,teacher_no,term_code,profile_json,snapshot_json,lessons_json,summary_json,synced_at)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(school_code,teacher_no,term_code) DO UPDATE SET
               profile_json=excluded.profile_json,snapshot_json=excluded.snapshot_json,
               lessons_json=excluded.lessons_json,summary_json=excluded.summary_json,synced_at=excluded.synced_at
               WHERE academic_teacher_timetables.synced_at<=excluded.synced_at""",
            (SCHOOL, profile["teacher_no"], identity.code, _dump(profile), _dump(snapshot), _dump(lessons), _dump(summary), stamp),
        )
    # An exact-employee refresh is not evidence of a complete fuzzy-name query.
    if signature is not None:
        conn.execute(
            """INSERT INTO academic_teacher_query_cache
               (school_code,term_code,query_key,filters_json,teacher_nos_json,options_json,synced_at)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(school_code,term_code,query_key) DO UPDATE SET
               filters_json=excluded.filters_json,teacher_nos_json=excluded.teacher_nos_json,
               options_json=excluded.options_json,synced_at=excluded.synced_at
               WHERE academic_teacher_query_cache.synced_at<=excluded.synced_at""",
            (SCHOOL, identity.code, signature, _dump(filters), _dump([p["teacher_no"] for p, *_ in normalized]),
             _dump(result.get("options") or {}), stamp),
        )
    _release(conn, credential_key, token)


async def query_teacher_timetables(teacher_id, payload, *, force_sync=False):
    with get_db_connection() as conn:
        identity, filters, signature, page, page_size = _normalize_query(conn, teacher_id, payload)
        if not filters["q"]:
            if force_sync:
                raise TimetableQueryError("请输入教师姓名或工号后同步。")
            return _browse_local(conn, identity, filters, page, page_size)
        if force_sync and payload.get("local_only"):
            raise TimetableQueryError("同步操作不能同时指定仅查本地。")
        if not force_sync:
            cached = _read_cached(conn, identity, signature, page, page_size)
            if cached is not None:
                return cached
            if payload.get("local_only"):
                return {"status": "success", "source": "local", "mode": "local_only", "summary_scope": "query",
                        "selected_term": _term_payload(identity), "synced_at": "", "items": [], "options": {},
                        "message": "当前条件尚无完整本地查询记录，请点击查询或同步。",
                        "pagination": {"page": 1, "page_size": page_size, "total_count": 0, "total_page": 1},
                        "summary": {"teacher_count": 0, "linked_count": 0, "course_count": 0, "lesson_count": 0}}
    return await _sync_teacher_timetables(teacher_id, identity, filters, signature, page, page_size)


async def sync_teacher_timetable(teacher_id, teacher_no, payload):
    """Refresh one exact employee independently of any cached fuzzy query."""
    if not isinstance(payload, dict) or not payload.get("year") or not payload.get("term"):
        raise TimetableQueryError("请选择有效的学年学期。")
    if not isinstance(teacher_no, str) or not teacher_no.strip() or len(teacher_no) > 80 or teacher_no != teacher_no.strip():
        raise TimetableQueryError("教师工号无效。")
    with get_db_connection() as conn:
        identity, filters, _, page, page_size = _normalize_query(
            conn, teacher_id, {"year": payload["year"], "term": payload["term"], "q": teacher_no},
        )
    return await _sync_teacher_timetables(teacher_id, identity, filters, None, page, page_size,
                                         exact_teacher_no=teacher_no)


async def _sync_teacher_timetables(teacher_id, identity, filters, signature, page, page_size, *, exact_teacher_no=None):
    with get_db_connection() as conn:
        _scope(conn, teacher_id)
        credential = load_teacher_academic_access_method(conn, teacher_id, school_code=SCHOOL)
        if not credential:
            raise TimetableQueryError("请先在教务系统对接中保存并验证账号。", status="missing_credential")
        credential_id, token, stamp = int(credential["credential_id"]), uuid.uuid4().hex, _stamp()
        credential_key = _lease_key(credential)
        claimed = _claim(conn, credential_key, token, stamp)
        conn.commit()
        if not claimed:
            raise TimetableQueryError("当前教务账号正在同步教师课表，请稍后查询。", status="busy", http_status=409)
    try:
        xnm, xqm = identity.as_xnm_xqm()
        async with open_authenticated_academic_client(credential) as (client, profile, _login):
            if profile.school_code != SCHOOL:
                raise ValueError("教务学校不匹配")
            exact = {"exact_teacher_no": exact_teacher_no} if exact_teacher_no is not None else {}
            result = await _fetch_teacher_timetables(client, xnm=xnm, xqm=xqm, filters=filters, **exact)
        normalized = _normalize_teachers(result)
        if exact_teacher_no is not None and (len(normalized) != 1 or normalized[0][0]["teacher_no"] != exact_teacher_no):
            raise TimetableQueryError("教务系统未返回该工号的完整课表，已有缓存保持不变。",
                                      status="failed", http_status=502)
        with get_db_connection() as conn:
            # A revoked membership or replaced/deleted credential cannot publish.
            _scope(conn, teacher_id)
            still_valid = conn.execute(
                "SELECT id FROM teacher_academic_system_credentials WHERE id=? AND teacher_id=? AND school_code=? AND enabled=1 AND username=?",
                (credential_id, teacher_id, SCHOOL, credential["username"]),
            ).fetchone()
            if not still_valid:
                raise ValueError("教务账号已更改")
            try:
                _publish(conn, credential_key, token, identity, filters, signature, result, normalized, stamp)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            if exact_teacher_no is not None:
                year, term = identity.as_year_term()
                detail = load_teacher_timetable(conn, teacher_id, exact_teacher_no, year=year, term=term)
                return {**detail, "source": "academic", "message": "已同步该教师的完整学期课表。"}
            return _read_cached(conn, identity, signature, page, page_size, source="academic")
    except asyncio.CancelledError:
        # Client disconnects and worker shutdown must not strand a ten-minute
        # lease. The last complete snapshot has not been replaced.
        try:
            with get_db_connection() as conn:
                _release(conn, credential_key, token)
                conn.commit()
        except Exception:
            logger.warning("Could not release cancelled teacher timetable lease")
        raise
    except Exception as exc:
        with get_db_connection() as conn:
            _release(conn, credential_key, token)
            conn.commit()
        if isinstance(exc, TimetableQueryError):
            raise
        from .academic_teacher_timetable_adapter import TeacherTimetableSourceError
        if isinstance(exc, TeacherTimetableSourceError):
            raise TimetableQueryError(str(exc), status="failed", http_status=502) from exc
        logger.warning("Teacher timetable synchronization failed (%s)", type(exc).__name__)
        raise TimetableQueryError("教务课表同步未完成，已有本地缓存保持不变，请稍后重试。",
                                  status="failed", http_status=502) from exc


def load_teacher_timetable(conn, teacher_id, teacher_no, *, year, term):
    _scope(conn, teacher_id)
    identity = identity_from_year_term(year, term)
    if not identity:
        raise TimetableQueryError("请选择有效的学年学期。")
    row = conn.execute(
        "SELECT * FROM academic_teacher_timetables WHERE school_code=? AND teacher_no=? AND term_code=?",
        (SCHOOL, teacher_no, identity.code),
    ).fetchone()
    if row is None:
        raise TimetableQueryError("该教师本学期尚无完整缓存，请先查询或同步。", status="not_found", http_status=404)
    from .smart_classroom_schedule_sync_service import (
        _build_course_stats, _build_session_no_map, _build_week_deck, _section_label, _short_classroom, _weekday_label,
    )
    lessons = json.loads(row["lessons_json"])
    items = []
    for raw in lessons:
        item = {**raw, "id": str(raw["id"]), "course_code": raw.get("course_code", ""),
                "class_label": raw.get("class_label") or raw.get("teaching_class_name") or "未提供班级",
                "teaching_class_name": raw.get("teaching_class_name", ""),
                "class_is_fallback": False, "class_offering_id": None, "classroom_url": "", "create_url": "",
                "classroom": raw.get("classroom", ""), "student_count": int(raw.get("student_count") or 0),
                "single_or_double": "NONE", "single_or_double_label": "",
                "weekday_label": _weekday_label(raw["weekday"]), "section_label": _section_label(raw["sections"]),
                "hours_per_meeting": len(raw["sections"]), "total_hours": len(raw["sections"]) * len(raw["weeks"])}
        item["classroom_short"] = _short_classroom(item["classroom"])
        items.append(item)
    calendar_term = _terms(conn).get(identity.code)
    today, monday, live_week, status = china_now().date(), None, 0, "unknown"
    max_week = max((max(i["weeks"]) for i in items), default=20)
    if calendar_term:
        start, end = date.fromisoformat(calendar_term["start_date"][:10]), date.fromisoformat(calendar_term["end_date"][:10])
        monday = start - timedelta(days=start.weekday())
        status = "current" if start <= today <= end else "ended" if end < today else "future"
        live_week = (today - monday).days // 7 + 1 if status == "current" else 0
        max_week = max(max_week, int(calendar_term["week_count"] or 0))
    weeks = _build_week_deck(items, max_week=max_week, cur_week=live_week, week1_monday=monday,
                             session_no_map=_build_session_no_map(items))
    selected = {**_term_payload(identity), "status": status, "max_week": len(weeks),
                "week1_monday": monday.isoformat() if monday else "", "live_cur_week": live_week,
                "focus_week": max(1, min(live_week or 1, len(weeks))), "anchor_source": "platform" if monday else "unavailable"}
    stats = json.loads(row["summary_json"])
    overview = {"status": "success", "has_data": bool(items), "schedule_source": "academic_teacher_query",
                "message": "" if monday else "尚无该学期校历，按教务周次展示。",
                "terms": [selected], "selected_term": selected, "weeks": weeks, "courses": _build_course_stats(items),
                "summary": {**stats, "slot_count": len(items), "cur_week": live_week, "max_week": len(weeks),
                            "classroom_count": len({i["classroom"] for i in items if i["classroom"]}),
                            "term_status": status, "week1_monday": selected["week1_monday"],
                            "current_week_hours": next((w["total_hours"] for w in weeks if w["is_current"]), 0)},
                "filters": {"course": "", "class_label": "", "course_options": [], "class_options": []},
                "section_range": {"min": 1, "max": max(11, max((max(i["sections"]) for i in items), default=11))}}
    teacher = {**json.loads(row["profile_json"]), **_account_links(conn, [teacher_no])[teacher_no],
               "summary": stats, "synced_at": row["synced_at"]}
    return {"status": "success", "teacher": teacher, "overview": overview,
            "lessons": {i["id"]: i for i in items},
            "unscheduled_courses": json.loads(row["snapshot_json"]).get("unscheduled_courses", []),
            "synced_at": row["synced_at"]}
