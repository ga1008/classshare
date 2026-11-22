"""Read structured interaction opportunities for an internal, per-offering shadow projection.

One published activity or poll is one opportunity. Free group chat is a channel
and has no required response. Only finished windows enter the participation
ratio. Optional sources and uncertain historical eligibility stay distinguishable
from a known zero. No question, answer, vote choice or attendance result is read.
"""
from __future__ import annotations

from collections import defaultdict
import sqlite3

from ..db.connection import get_configured_db_engine
from ..time_utils import APP_TIMEZONE, local_now, to_local_datetime
from .offering_membership_service import offering_student_where


def _empty():
    return {
        "offered": 0, "participated": 0, "open": 0, "open_participated": 0, "expression_offered": 0,
        "uncertain": 0, "group_channels": 0, "missing_sources": [],
        "coverage_status": "complete", "coverage_ratio": None,
        "by_kind": {kind: {"offered": 0, "participated": 0, "open": 0, "open_participated": 0}
                    for kind in ("quiz", "qna", "poll")},
    }


def _record(item, kind, *, closed, participated):
    counts = item["by_kind"][kind]
    if closed:
        counts["offered"] += 1
        item["offered"] += 1
        if participated:
            counts["participated"] += 1
            item["participated"] += 1
    else:
        counts["open"] += 1
        item["open"] += 1
        if participated:
            counts["open_participated"] += 1
            item["open_participated"] += 1
    if kind == "qna":
        item["expression_offered"] += 1


def _historical_roster(conn, offering, records, available):
    # Round rosters record who belonged to the class during a concrete window.
    # A PRESENT or ABSENT result has no bearing on this calculation.
    windows = defaultdict(list)
    if not {"login_attendance_rounds", "login_attendance_records"} <= available:
        return windows
    ids = ",".join("?" for _ in records)
    for row in conn.execute(
        f"""SELECT m.student_id, r.starts_at_ms, r.ends_at_ms FROM login_attendance_rounds r
           JOIN login_attendance_records m ON m.round_id=r.id
           WHERE r.class_offering_id=? AND r.is_current=1
             AND r.state IN ('running','settling','published') AND m.expected=1
             AND m.student_id IN ({ids})""", (offering, *records)
    ).fetchall():
        windows[int(row["student_id"])].append((row["starts_at_ms"], row["ends_at_ms"]))
    return windows


def _window_known(windows, starts, ends):
    # Stored attendance bounds are absolute milliseconds; activity writers use
    # local ISO text. Convert the two opportunity bounds once per activity.
    return any(left < ends and right > starts for left, right in windows)


def _millis(value):
    return int(value.replace(tzinfo=APP_TIMEZONE).timestamp() * 1000)


def _live(conn, offering, now, records, windows, available, include_anonymous):
    ids = ",".join("?" for _ in records)
    completed = defaultdict(dict)
    for kind, table in (("quiz", "classroom_live_responses"), ("qna", "classroom_live_questions")):
        if table not in available:
            continue
        condition = "AND f.class_offering_id=a.class_offering_id AND f.status IN ('open','addressed')" if kind == "qna" else ""
        visibility = "" if include_anonymous else "AND f.is_anonymous=0"
        rows = conn.execute(
            f"""SELECT f.activity_id, f.student_id, MIN(f.created_at) AS observed
                FROM classroom_live_activities a JOIN {table} f ON f.activity_id=a.id
                WHERE a.class_offering_id=? AND a.kind=? AND f.student_id IN ({ids}) {condition} {visibility}
                GROUP BY f.activity_id,f.student_id""", (offering, kind, *records)
        ).fetchall()
        for row in rows:
            completed[int(row["activity_id"])][int(row["student_id"])] = to_local_datetime(row["observed"])
    for row in conn.execute(
        """SELECT id,kind,status,started_at,closed_at FROM classroom_live_activities
           WHERE class_offering_id=? AND kind IN ('qna','quiz')
             AND status IN ('active','closed','archived')""", (offering,)
    ).fetchall():
        start = to_local_datetime(row["started_at"])
        end = to_local_datetime(row["closed_at"])
        if start and start > now:
            continue
        if not start or (row["status"] != "active" and (not end or end < start)):
            for item in records.values():
                item["uncertain"] += 1
            continue
        closed = bool(end and end <= now)
        bounds = (_millis(start), _millis(end)) if closed else None
        for student, item in records.items():
            observed = completed[int(row["id"])].get(student)
            participated = bool(observed and start <= observed <= min(end or now, now))
            if closed and not participated and not _window_known(windows[student], *bounds):
                item["uncertain"] += 1
                continue
            _record(item, row["kind"], closed=closed, participated=participated)


def _polls(conn, offering, now, records, windows, available):
    polls = conn.execute(
        """SELECT p.id,p.status,p.audience_scope,p.created_at,p.updated_at,p.closed_at,
                  p.deadline_at,pa.created_at AS assigned_at
           FROM poll_assignments pa JOIN polls p ON p.id=pa.poll_id
           WHERE pa.class_offering_id=? AND p.status IN ('active','closed')""", (offering,)
    ).fetchall()
    if not polls:
        return
    ids = ",".join("?" for _ in records)
    ballots = defaultdict(dict)
    invites = defaultdict(dict)
    for table, id_key, bucket in (("poll_ballots", "voter_id", ballots),
                                  ("poll_participants", "student_id", invites)):
        if table not in available:
            continue
        for row in conn.execute(
            f"""SELECT b.poll_id,b.{id_key} AS student_id,b.created_at
                FROM poll_assignments a JOIN {table} b ON b.poll_id=a.poll_id
                WHERE a.class_offering_id=? AND b.{id_key} IN ({ids})""", (offering, *records)
        ).fetchall():
            bucket[int(row["poll_id"])][int(row["student_id"])] = to_local_datetime(row["created_at"])
    # Publication proof is global to this poll. No other voter's identity,
    # option selection or per-voter timestamp leaves this aggregate.
    first_ballot = {}
    if "poll_ballots" in available:
        first_ballot = {int(row["poll_id"]): to_local_datetime(row["first_seen"]) for row in conn.execute(
            """SELECT a.poll_id,MIN(b.created_at) AS first_seen FROM poll_assignments a
               JOIN poll_ballots b ON b.poll_id=a.poll_id
               WHERE a.class_offering_id=? GROUP BY a.poll_id""", (offering,)
        ).fetchall()}
    # A management poll can be shared. Ballots have no originating offering.
    # For a student simultaneously eligible through another assigned offering,
    # the poll cannot be attributed to a single classroom.
    also_assigned = defaultdict(dict)
    for row in conn.execute(
        f"""SELECT a.poll_id,s.id AS student_id,MIN(other.created_at) AS other_since
            FROM poll_assignments a JOIN poll_assignments other ON other.poll_id=a.poll_id
              AND other.class_offering_id<>a.class_offering_id
            JOIN class_offerings o ON o.id=other.class_offering_id
            JOIN students s ON {offering_student_where()}
            WHERE a.class_offering_id=? AND s.id IN ({ids}) GROUP BY a.poll_id,s.id""",
        (offering, *records)
    ).fetchall():
        also_assigned[int(row["poll_id"])][int(row["student_id"])] = to_local_datetime(row["other_since"])
    exempt = defaultdict(dict)
    if "classroom_retake_students" in available:
        for row in conn.execute(
            f"""SELECT a.poll_id,r.student_id,MIN(r.confirmed_at) AS since,
                       COUNT(*) AS decisions,COUNT(r.confirmed_at) AS dated
               FROM poll_assignments a
               JOIN poll_assignments other ON other.poll_id=a.poll_id
               JOIN classroom_retake_students r ON r.class_offering_id=other.class_offering_id
               WHERE a.class_offering_id=? AND r.status='confirmed' AND r.student_id IN ({ids})
               GROUP BY a.poll_id,r.student_id""", (offering, *records)
        ).fetchall():
            since = to_local_datetime(row["since"]) if row["dated"] == row["decisions"] else None
            exempt[int(row["poll_id"])][int(row["student_id"])] = since
    for row in polls:
        poll = int(row["id"])
        created = to_local_datetime(row["created_at"])
        assigned = to_local_datetime(row["assigned_at"])
        if (created and created > now) or (assigned and assigned > now):
            continue
        closes = [v for v in (to_local_datetime(row["deadline_at"]),
                              to_local_datetime(row["closed_at"])) if v]
        end = min(closes) if closes else None
        if assigned and end and assigned >= end:
            continue
        closed = bool(end and end <= now)
        # A poll may move directly from draft to closed, with no publication
        # timestamp. Only active status or a ballot proves it was offered.
        observed_open = first_ballot.get(poll)
        cutoff = min(now, end or now)
        stamps = [observed_open] if observed_open and observed_open <= cutoff else []
        if row["status"] == "active":
            last_change = to_local_datetime(row["updated_at"])
            if last_change and last_change <= cutoff:
                stamps.append(last_change)
        start = max(min(stamps), assigned) if stamps and assigned else None
        custom = row["audience_scope"] == "custom"
        for student, item in records.items():
            if custom and student not in invites[poll]:
                continue
            stamp = ballots[poll].get(student)
            participated = bool(stamp and assigned and assigned <= stamp <= cutoff)
            invited = invites[poll].get(student) if custom else None
            if invited and invited > cutoff:
                continue
            if student in also_assigned[poll]:
                elsewhere = also_assigned[poll][student]
                if not elsewhere or elsewhere <= cutoff:
                    item["uncertain"] += 1
                    continue
            if not start or not created or (row["status"] == "closed" and not to_local_datetime(row["closed_at"])):
                item["uncertain"] += 1
                continue
            accessible_until = end
            if not custom and student in exempt[poll] and not participated:
                since = exempt[poll][student]
                if since is None and closed:
                    item["uncertain"] += 1
                    continue
                if since is None or since <= start or (not closed and since <= now):
                    continue
                if closed:
                    accessible_until = min(end, since)
            listed = custom and invited is not None
            if closed and not (listed or participated or _window_known(
                    windows[student], _millis(start), _millis(accessible_until))):
                item["uncertain"] += 1
                continue
            _record(item, "poll", closed=closed, participated=participated)


def load_interaction_expression_opportunities(conn, class_offering_id, student_ids, *, as_of=None,
                                             include_anonymous=False):
    """Read current facts as of a cutoff, keyed by requested human student ID.

    Missing optional sources leave partial counts but suppress the ratio. SQL
    errors propagate to the caller's retry boundary. Historical class-wide
    eligibility requires a contemporaneous roster or the student's own work;
    today's class membership alone cannot establish a historical absence.
    """
    offering = int(class_offering_id)
    now = local_now() if as_of is None else to_local_datetime(as_of)
    if now is None:
        raise ValueError("invalid opportunity cutoff")
    requested = sorted({int(student) for student in student_ids})
    # Keep query parameter lists bounded on older SQLite as well as PostgreSQL.
    if len(requested) > 500:
        result = {}
        for offset in range(0, len(requested), 500):
            result.update(load_interaction_expression_opportunities(
                conn, offering, requested[offset:offset + 500], as_of=now, include_anonymous=include_anonymous))
        return result
    result = {student: _empty() for student in requested}
    if not result:
        return result
    tables = {
        "classroom_live_activities", "classroom_live_responses", "classroom_live_questions",
        "polls", "poll_assignments", "poll_participants", "poll_ballots",
        "study_groups", "study_group_members", "classroom_retake_students",
        "login_attendance_rounds", "login_attendance_records", "wanderer_agents",
    }
    # Probe all optional sources in one round trip, with no lazy DDL.
    placeholders = ",".join("?" for _ in tables)
    catalog = ("SELECT name FROM sqlite_master WHERE type='table' AND name"
               if isinstance(conn, sqlite3.Connection) or get_configured_db_engine() == "sqlite"
               else "SELECT table_name AS name FROM information_schema.tables "
                    "WHERE table_schema=current_schema() AND table_name")
    available = {row["name"] for row in conn.execute(
        f"{catalog} IN ({placeholders})", tuple(tables)
    ).fetchall()}
    agents = ("AND NOT EXISTS (SELECT 1 FROM wanderer_agents w WHERE w.student_id=s.id)"
              if "wanderer_agents" in available else "")
    ids = ",".join("?" for _ in requested)
    eligible = {int(row["id"]) for row in conn.execute(
        f"""SELECT s.id FROM class_offerings o JOIN students s ON {offering_student_where()}
            WHERE o.id=? AND COALESCE(s.enrollment_status,'active')='active'
              AND LOWER(COALESCE(s.control_mode,'human'))<>'ai' AND s.id IN ({ids}) {agents}""",
        (offering, *requested)
    ).fetchall()}
    records = {student: item for student, item in result.items() if student in eligible}
    for student in result.keys() - records.keys():
        result[student]["coverage_status"] = "ineligible"
    if not records:
        return result
    optional = {"wanderer_agents", "classroom_retake_students", "login_attendance_rounds", "login_attendance_records"}
    missing = sorted(tables - available - optional)
    windows = _historical_roster(conn, offering, records, available)
    if "classroom_live_activities" in available:
        _live(conn, offering, now, records, windows, available, include_anonymous)
    if {"polls", "poll_assignments"} <= available:
        _polls(conn, offering, now, records, windows, available)
    if {"study_groups", "study_group_members"} <= available:
        ids = ",".join("?" for _ in records)
        for row in conn.execute(
            f"""SELECT m.student_id,m.joined_at,m.left_at,m.status,g.archived_at,g.status AS group_status
               FROM study_groups g JOIN study_group_members m ON m.group_id=g.id
               WHERE g.class_offering_id=? AND m.student_id IN ({ids})""", (offering, *records)
        ).fetchall():
            item = records.get(int(row["student_id"]))
            joined = to_local_datetime(row["joined_at"])
            left = to_local_datetime(row["left_at"])
            archived = to_local_datetime(row["archived_at"])
            member_active = left > now if left else row["status"] == "active"
            group_open = archived > now if archived else row["group_status"] == "active"
            if item and joined and joined <= now and member_active and group_open:
                item["group_channels"] += 1
    for item in records.values():
        item["missing_sources"] = missing.copy()
        if missing or item["uncertain"]:
            item["coverage_status"] = "limited"
        elif item["offered"]:
            item["coverage_ratio"] = item["participated"] / item["offered"]
    return result
