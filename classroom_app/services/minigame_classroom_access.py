"""Time-limited classroom rules layered over game publication and audience checks."""

from datetime import datetime
import json

from fastapi import HTTPException

from . import minigame_platform as platform
from .academic_course_sync_service import _parse_section_range
from .login_attendance_rules import SCHOOL_PERIODS, SHANGHAI, to_ms
from .offering_membership_service import student_offering_ids_for_class_sql
from .schedule_lesson_metadata import explicit_lesson_time
from .semester_identity_service import current_identity, parse_semester_identity


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mg_classroom_access (
        offering_id BIGINT PRIMARY KEY REFERENCES class_offerings(id) ON DELETE CASCADE,
        teacher_id BIGINT NOT NULL, updated_by BIGINT NOT NULL,
        mode TEXT NOT NULL CHECK (mode IN ('auto','allow','deny')), games_json TEXT NOT NULL,
        duration_minutes INTEGER NOT NULL, expires_at BIGINT NOT NULL, revision BIGINT NOT NULL)""")


def _windows(row):
    """A paired teaching block contains separate periods, with an open recess."""
    record = dict(row)
    day = str(record["session_date"])[:10]
    section = record["academic_section_text"]
    if not section:
        try:
            metadata = json.loads(record.get("schedule_metadata_json") or "{}")
        except (ValueError, TypeError):
            metadata = {}
        section = metadata.get("section_text") if isinstance(metadata, dict) else ""
    start, end, _ = _parse_section_range(section)
    sections = list(range(start, end + 1)) if section and start else []
    explicit = explicit_lesson_time({**record, "sections": sections})
    bells = [SCHOOL_PERIODS[i] for i in sections if i in SCHOOL_PERIODS]
    if explicit["start_time"]:
        span = explicit["start_time"], explicit["end_time"]
        if len(bells) > 1 and bells[0][0] <= span[0] < bells[0][1] and bells[-1][0] < span[1] <= bells[-1][1]:
            bells = [(max(span[0], a), min(span[1], b)) for a, b in bells]
        else:
            bells = [span]
    return [(to_ms(day + "T" + a), to_ms(day + "T" + b)) for a, b in bells]


_SESSION_COLUMNS = "s.session_date,s.academic_section_text,s.academic_time_text,s.schedule_metadata_json"
_SESSION_FILTER = "s.session_date=? AND COALESCE(s.schedule_status,'scheduled') NOT IN ('cancelled','canceled','suspended')"


def _school_day(now):
    return datetime.fromtimestamp(now / 1000, SHANGHAI).date().isoformat()


class Timetable:
    def __init__(self, rows):
        self.windows = set()
        self.overrides = {}
        for row in rows:
            if row["session_date"]:
                self.windows.update(_windows(row))
            if row["mode"] and row["offering_id"] not in self.overrides:
                self.overrides[row["offering_id"]] = (
                    row["mode"], frozenset(json.loads(row["games_json"])), row["expires_at"],
                    row["expires_at"] - row["duration_minutes"] * 60_000, row["revision"],
                )

    def decision(self, game_id, now):
        active = any(start <= now < end for start, end in self.windows)
        changes = [point for span in self.windows for point in span if point > now]
        deadlines = {"allow": [], "deny": []}
        for mode, ids, expiry, start, _ in self.overrides.values():
            if start <= now < expiry and (not ids or game_id in ids):
                deadlines[mode].append(expiry)
        if deadlines["deny"]:
            allowed, reason, source = False, "任课教师已临时暂停这款小游戏。", "classroom_deny"
            changes = [max(deadlines["deny"])]
        elif deadlines["allow"]:
            allowed, reason, source = True, "任课教师已临时开放这款小游戏。", "classroom_allow"
            changes = [max(deadlines["allow"])]
        elif active:
            allowed, reason, source = False, "课表显示现在是上课时间，课间可再来。", "timetable"
        else:
            allowed, reason, source = True, "当前为自由活动时间。", "free"
        return {"allowed": allowed, "reason": reason, "source": source, "nextChangeAt": min(changes, default=None)}


def timetable(conn, user, now, audience=None):
    if user["role"] != "student" or (audience is not None and not audience["offers"]):
        return Timetable(())
    class_id = (next(iter(audience["classes"]), None) if audience is not None
                else platform.load_actor(conn, user["actor"])["class_id"])
    return class_timetable(conn, class_id, now)


def class_timetable(conn, class_id, now):
    day = _school_day(now)
    cache = platform._actor_cache.get()
    key = ("game-classroom-timetable", class_id, day)
    if cache and cache[0] is conn and key in cache[1]:
        return cache[1][key]
    # One bounded-by-membership read serves every card, room check and content decision
    # in this transaction. The next request observes a teacher's committed changes.
    schedule = _load_timetable(conn, class_id, day, now)
    if cache and cache[0] is conn:
        cache[1][key] = schedule
    return schedule


def _load_timetable(conn, class_id, day, since):
    rows = conn.execute(f"""SELECT o.id AS offering_id,r.mode,r.games_json,r.expires_at,r.duration_minutes,
               r.revision,{_SESSION_COLUMNS}
        FROM class_offerings o
        LEFT JOIN mg_classroom_access r ON r.offering_id=o.id AND r.teacher_id=o.teacher_id
            AND r.expires_at>=?
        LEFT JOIN class_offering_sessions s ON s.class_offering_id=o.id AND {_SESSION_FILTER}
        WHERE o.id IN ({student_offering_ids_for_class_sql()}) AND (r.offering_id IS NOT NULL OR s.id IS NOT NULL)""",
        (since, day, class_id, class_id)).fetchall()
    return Timetable(rows)


def grant_end_on_restriction(conn, user, game_id, grant_start_ms, now_ms):
    """Last provably playable instant of a lease interrupted by a classroom rule."""
    class_id = platform.load_actor(conn, user["actor"])["class_id"]
    day = _school_day(now_ms)
    if day != _school_day(grant_start_ms):
        return grant_start_ms
    schedule = _load_timetable(conn, class_id, day, grant_start_ms)
    points = {grant_start_ms, now_ms}
    for a, b in schedule.windows:
        points.update((a, b))
    for _, _, end, start, revision in schedule.overrides.values():
        # Older settings are intentionally not retained. Do not bill any uncertain
        # tail if two successive teacher commands could have changed this lease.
        if revision > 1 and start > grant_start_ms:
            return grant_start_ms
        points.update((start, end))
    for point in sorted(t for t in points if grant_start_ms <= t <= now_ms):
        if not schedule.decision(game_id, point)["allowed"]:
            return point
    return grant_start_ms


class Restricted(HTTPException):
    def __init__(self, decision):
        super().__init__(403, {"code": "CLASSROOM_GAME_RESTRICTED", "message": decision["reason"],
                              "nextChangeAt": decision["nextChangeAt"]})


def require_access(conn, user, game_id):
    if user["role"] == "student":
        now = platform.now_ms()
        decision = timetable(conn, user, now).decision(game_id, now)
        if not decision["allowed"]:
            raise Restricted(decision)
        return decision["nextChangeAt"]
    return None


def annotate(conn, user, cards, audience=None):
    if user["role"] != "student" or not cards:
        return
    now = platform.now_ms()
    schedule = timetable(conn, user, now, audience)
    for card in cards:
        card["access"] = schedule.decision(card["id"], now)


def _teacher(user):
    if user["role"] != "teacher":
        raise HTTPException(403, "仅任课教师或超级管理员可设置课堂小游戏。")


def _offerings(conn, user, offering_id=None, *, write=False):
    _teacher(user)
    params = []
    clauses = []
    if not platform.is_super_admin_teacher(conn, user["id"]):
        clauses.append("o.teacher_id=?")
        params.append(user["id"])
    if offering_id is not None:
        clauses.append("o.id=?")
        params.append(offering_id)
    today = _school_day(platform.now_ms())
    # An old course taught to the same cohort cannot change their present-day access.
    clauses.append("(term.id IS NULL OR (term.start_date<=? AND term.end_date>=?))")
    params.extend((today, today))
    sql = """SELECT o.id,o.teacher_id,c.name AS course_name,cl.name AS class_name,o.combined_class_names,
                    o.semester_id,o.semester
        FROM class_offerings o JOIN courses c ON c.id=o.course_id JOIN classes cl ON cl.id=o.class_id
        LEFT JOIN academic_semesters term ON term.id=o.semester_id WHERE """ + " AND ".join(clauses)
    sql += " ORDER BY o.id DESC"
    if write and platform.get_configured_db_engine() == "postgres":
        sql += " FOR UPDATE OF o"
    rows = [dict(r) for r in conn.execute(sql, tuple(params))]
    legacy_teachers = {r["teacher_id"] for r in rows if not r["semester_id"] and r["semester"]}
    if legacy_teachers:
        from .dashboard_service import _match_semester_for_offering

        terms = {}
        marks = ",".join("?" for _ in legacy_teachers)
        for term in conn.execute(f"SELECT id,teacher_id,name,start_date,end_date FROM academic_semesters WHERE teacher_id IN ({marks})",
                                 tuple(legacy_teachers)):
            terms.setdefault(term["teacher_id"], []).append(dict(term))
        def current(row):
            if row["semester_id"] or not row["semester"]:
                return True
            match = _match_semester_for_offering(terms.get(row["teacher_id"], []), row)
            if match:
                return match["start_date"] <= today <= match["end_date"]
            identity = parse_semester_identity(row["semester"])
            return identity is None or identity == current_identity(today)
        rows = [row for row in rows if current(row)]
    if offering_id is not None and not rows:
        raise HTTPException(404, "未找到本学期可管理的课堂。")
    return rows


def classrooms(conn, user):
    return {"classrooms": [
        {"id": r["id"], "name": f"{r['course_name']} · {r['combined_class_names'] or r['class_name']}"}
        for r in _offerings(conn, user)
    ], "csrfToken": platform.csrf_token(user)}


def settings(conn, user, offering_id):
    offering = _offerings(conn, user, offering_id)[0]
    now = platform.now_ms()
    policy = conn.execute("SELECT * FROM mg_classroom_access WHERE offering_id=?", (offering_id,)).fetchone()
    active = policy and policy["teacher_id"] == offering["teacher_id"] and policy["expires_at"] > now
    ids = json.loads(policy["games_json"]) if active else []
    games = []
    if ids:
        games = [dict(r) for r in conn.execute(
            "SELECT id,title FROM mg_games WHERE id IN (" + ",".join("?" for _ in ids) + ") ORDER BY title,id",
            tuple(ids))]
    rows = conn.execute(f"SELECT {_SESSION_COLUMNS} FROM class_offering_sessions s WHERE s.class_offering_id=? AND {_SESSION_FILTER}",
                        (offering_id, _school_day(now))).fetchall()
    windows = [span for r in rows for span in _windows(r)]
    return {"offeringId": offering_id, "mode": policy["mode"] if active else "auto",
            "gameIds": ids, "games": games, "expiresAt": policy["expires_at"] if active else None,
            "durationMinutes": policy["duration_minutes"] if active else 0,
            "revision": policy["revision"] if policy else 0, "serverNow": now,
            "scheduledNow": any(a <= now < b for a, b in windows),
            "nextChangeAt": min((p for span in windows for p in span if p > now), default=None),
            "csrfToken": platform.csrf_token(user)}


def save(user, offering_id, body):
    _teacher(user)
    if set(body) - {"mode", "gameIds", "durationMinutes", "expectedRevision"}:
        raise HTTPException(422, "课堂设置含有无效选项。")
    mode, duration, ids = body.get("mode"), body.get("durationMinutes"), body.get("gameIds", [])
    expected = body.get("expectedRevision")
    if mode not in ("allow", "deny", "auto") or type(expected) is not int or not 0 <= expected < 2**53:
        raise HTTPException(422, "请选择课堂规则并提供当前版本。")
    if mode == "auto":
        duration, ids = 0, []
    if type(duration) is not int or (mode != "auto" and not 1 <= duration <= 240):
        raise HTTPException(422, "临时规则时长为 1 至 240 分钟。")
    if (not isinstance(ids, list) or len(ids) > 100
            or any(not isinstance(i, str) or not 1 <= len(i) <= 128 for i in ids)):
        raise HTTPException(422, "每次最多选择 100 款游戏。")
    ids = sorted(set(ids))
    encoded = json.dumps(ids, separators=(",", ":"))
    with platform.game_db() as conn:
        platform.db_start(conn)
        platform.assert_session(conn, user)
        offering = _offerings(conn, user, offering_id, write=True)[0]
        previous = conn.execute("SELECT * FROM mg_classroom_access WHERE offering_id=?", (offering_id,)).fetchone()
        revision = previous["revision"] if previous else 0
        if expected != revision:
            # Retrying the previous acknowledged command must not extend its deadline.
            if not (previous and expected + 1 == revision and previous["updated_by"] == user["id"]
                    and previous["teacher_id"] == offering["teacher_id"] and previous["mode"] == mode
                    and previous["duration_minutes"] == duration and previous["games_json"] == encoded):
                raise HTTPException(409, {"code": "STALE_REVISION", "message": "课堂规则已更新，请刷新后再试。"})
            return settings(conn, user, offering_id)
        if ids:
            from .minigame_catalog import CATALOG, CatalogQuery

            catalog = CatalogQuery(user, platform._audience(conn, user))
            found = conn.execute(f"SELECT g.id FROM {CATALOG} WHERE {catalog.visible} AND g.id IN ("
                                 + ",".join("?" for _ in ids) + ")", (*catalog.args, *ids)).fetchall()
            if len(found) != len(ids):
                raise HTTPException(422, "所选游戏已不可用，请重新选择。")
        expiry = platform.now_ms() + duration * 60_000
        conn.execute("""INSERT INTO mg_classroom_access
            (offering_id,teacher_id,updated_by,mode,games_json,duration_minutes,expires_at,revision)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(offering_id) DO UPDATE SET
            teacher_id=excluded.teacher_id, updated_by=excluded.updated_by, mode=excluded.mode,
            games_json=excluded.games_json,duration_minutes=excluded.duration_minutes,
            expires_at=excluded.expires_at,revision=excluded.revision""",
            (offering_id, offering["teacher_id"], user["id"], mode, encoded, duration, expiry, revision + 1))
        result = settings(conn, user, offering_id)
        conn.commit()
        return result
