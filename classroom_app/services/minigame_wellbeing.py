"""Measure foreground play in a bounded rolling window and settle whole hours."""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from .offering_membership_service import student_offering_where_by_student_id
from .semester_identity_service import current_identity, parse_semester_identity
from .social_common import serialize_on


LOCAL = timezone(timedelta(hours=8))
HOUR = 3600
DAY = 24 * HOUR
LEASE_MS = 90_000
PERIODS = ("early_seconds", "day_seconds", "late_seconds")


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mg_play_days (
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE, local_day TEXT NOT NULL,
        early_seconds INTEGER NOT NULL, day_seconds INTEGER NOT NULL, late_seconds INTEGER NOT NULL,
        points INTEGER NOT NULL, coverage_base BIGINT NOT NULL, coverage_bits TEXT NOT NULL,
        PRIMARY KEY (student_id,local_day))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS mg_play_totals (
        student_id INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
        reward_seconds BIGINT NOT NULL, penalty_seconds BIGINT NOT NULL)""")


def _classification(early, daytime, late):
    # 00–07 counts towards today's limit even though it can only incur penalties.
    # Recompute from the three chronological periods, including late-arriving tabs.
    reward = min(daytime, max(0, HOUR - early))
    excess = min(daytime, max(0, early + daytime - 2 * HOUR))
    return reward, early + late + excess


def _net(reward, penalty):
    return reward // HOUR - penalty // HOUR


def _local_date(seconds):
    return datetime.fromtimestamp(seconds, LOCAL).date()


def _daily_counts(conn, student_id, day):
    row = conn.execute("SELECT * FROM mg_play_days WHERE student_id=? AND local_day=?",
                       (student_id, day)).fetchone()
    return dict(row) if row else dict.fromkeys((*PERIODS, "coverage_base", "points"), 0) | {"coverage_bits": "0"}


def _totals(conn, student_id):
    row = conn.execute("SELECT reward_seconds,penalty_seconds FROM mg_play_totals WHERE student_id=?",
                       (student_id,)).fetchone()
    return (row["reward_seconds"], row["penalty_seconds"]) if row else (0, 0)


def _summary(day, counts, reward, penalty):
    total = sum(counts[key] for key in PERIODS)
    return {"date": day, "todaySeconds": total, "nightSeconds": counts["early_seconds"] + counts["late_seconds"],
            "settledHours": total // HOUR, "points": counts["points"], "totalPoints": _net(reward, penalty),
            "rewardRemainderSeconds": reward % HOUR, "penaltyRemainderSeconds": penalty % HOUR}


def summary(conn, student_id, now_ms):
    day = _local_date(now_ms // 1000).isoformat()
    row = conn.execute("""SELECT d.early_seconds,d.day_seconds,d.late_seconds,d.points,
        t.reward_seconds,t.penalty_seconds FROM mg_play_totals t
        LEFT JOIN mg_play_days d ON d.student_id=t.student_id AND d.local_day=?
        WHERE t.student_id=?""", (day, student_id)).fetchone()
    counts = {key: (row[key] or 0) if row else 0 for key in (*PERIODS, "points")}
    return _summary(day, counts, row["reward_seconds"] if row else 0, row["penalty_seconds"] if row else 0)


def course_points(conn, offering_id, student_id):
    row = conn.execute("""SELECT
        COALESCE((SELECT SUM(delta) FROM cultivation_score_events
            WHERE class_offering_id=? AND student_id=? AND component='minigame'),0)
        + COALESCE((SELECT SUM(total_delta) FROM cultivation_score_event_archives
            WHERE class_offering_id=? AND student_id=? AND component='minigame'),0) AS points""",
        (offering_id, student_id, offering_id, student_id)).fetchone()
    return int(row["points"])


def _settle_courses(conn, student_id, local_day, now_ms, delta):
    from .learning_progress_service import mark_student_learning_progress_dirty

    membership = student_offering_where_by_student_id(require_active=True)
    rows = conn.execute(f"""SELECT o.id,o.semester,term.id AS term_id FROM class_offerings o
        LEFT JOIN academic_semesters term ON term.id=o.semester_id
        WHERE {membership} AND (term.id IS NULL OR (? BETWEEN term.start_date AND term.end_date))
        ORDER BY o.id""", (student_id, local_day)).fetchall()
    now = datetime.fromtimestamp(now_ms / 1000, LOCAL).replace(tzinfo=None).isoformat(timespec="seconds")
    ref = "minigame:" + local_day
    for row in rows:
        legacy_term = parse_semester_identity(row["semester"]) if not row["term_id"] else None
        if legacy_term and legacy_term != current_identity(local_day):
            continue
        conn.execute("""INSERT INTO cultivation_score_events
            (class_offering_id,student_id,event_type,delta,component,source_ref,created_at)
            VALUES (?,?,'minigame_play',?,'minigame',?,?)""", (row["id"], student_id, delta, ref, now))
        mark_student_learning_progress_dirty(conn, row["id"], student_id, source_ref=ref)


def _sample_seconds(anchor, intervals, now_ms):
    """Each bit is a server-clock second. A lease can supply at most 90 seconds."""
    if not isinstance(intervals, list) or len(intervals) > 32:
        raise HTTPException(422, "游戏计时区间无效")
    origin = anchor - LEASE_MS
    covered = set()
    for part in intervals:
        if (not isinstance(part, list) or len(part) != 2 or any(type(n) is not int for n in part)
                or not 0 <= part[0] <= part[1] <= LEASE_MS):
            raise HTTPException(422, "游戏计时区间无效")
        start = (origin + part[0]) // 1000
        end = min(now_ms, origin + part[1]) // 1000
        covered.update(range(start, end))
    return covered


def record(conn, student_id, expires_at, sample, now_ms, *, allowed_until=None):
    """Caller owns the verified lease row. Its prior expiry is the single-use sample anchor."""
    if not sample or sample.get("usageAnchor") != expires_at or now_ms >= expires_at:
        return None
    end = now_ms if allowed_until is None else min(allowed_until, now_ms)
    seconds = _sample_seconds(expires_at, sample.get("playIntervals"), end)
    if not seconds:
        return None
    serialize_on(conn, "mg-play-time", [str(student_id)])
    # Persist only recent coverage, bounded by the longest still-valid sample.
    # No write per frame or input; multiple games and tabs share the same bitmap.
    floor = (now_ms - LEASE_MS) // 1000
    grouped = {}
    for second in sorted(seconds):
        grouped.setdefault(_local_date(second).isoformat(), []).append(second)
    reward, penalty = _totals(conn, student_id)
    today = _local_date(now_ms // 1000).isoformat()
    today_counts = None
    updated = False
    for day, values in grouped.items():
        counts = _daily_counts(conn, student_id, day)
        # A transaction waiting for this student can arrive after a newer one.
        base = max(floor, counts["coverage_base"])
        bits = int(counts["coverage_bits"], 16) >> (base - counts["coverage_base"])
        previous = _classification(*(counts[key] for key in PERIODS))
        new_seconds = 0
        for second in values:
            if second < base:
                continue
            flag = 1 << (second - base)
            if bits & flag:
                continue
            bits |= flag
            local_second = (second + 8 * HOUR) % DAY
            period = "early_seconds" if local_second < 7 * HOUR else "late_seconds" if local_second >= 22 * HOUR else "day_seconds"
            counts[period] += 1
            new_seconds += 1
        if not new_seconds:
            continue
        old_points = _net(reward, penalty)
        day_reward, day_penalty = _classification(*(counts[key] for key in PERIODS))
        reward += day_reward - previous[0]
        penalty += day_penalty - previous[1]
        delta = _net(reward, penalty) - old_points
        counts["points"] += delta
        conn.execute("""INSERT INTO mg_play_days
            (student_id,local_day,early_seconds,day_seconds,late_seconds,points,coverage_base,coverage_bits)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(student_id,local_day) DO UPDATE SET
            early_seconds=excluded.early_seconds,day_seconds=excluded.day_seconds,late_seconds=excluded.late_seconds,
            points=excluded.points,coverage_base=excluded.coverage_base,coverage_bits=excluded.coverage_bits""",
            (student_id, day, *(counts[key] for key in PERIODS), counts["points"], base, format(bits, "x")))
        if delta:
            _settle_courses(conn, student_id, day, now_ms, delta)
        updated = True
        if day == today:
            today_counts = counts
    if not updated:
        return None
    conn.execute("""INSERT INTO mg_play_totals(student_id,reward_seconds,penalty_seconds) VALUES(?,?,?)
        ON CONFLICT(student_id) DO UPDATE SET reward_seconds=excluded.reward_seconds,penalty_seconds=excluded.penalty_seconds""",
        (student_id, reward, penalty))
    return _summary(today, today_counts or _daily_counts(conn, student_id, today), reward, penalty)
