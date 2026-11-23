"""Room, queue, seat, and run transitions; callers own the write transaction."""

import json
import secrets

from fastapi import HTTPException

from . import minigame_platform as platform
from .minigame_runtime_rules import compact, fail, initial_state, integer
from .minigame_runtime_store import (
    active_session_keys,
    advance_progress,
    event,
    get,
    issue_code,
    keyed,
    prefs,
    progress,
    rows,
    save_private,
    uid,
)

LEASE_MS = 45000
GRACE_MS = 60000
ACTIVE = ("waiting", "countdown", "running", "reconnecting")


def activity(conn, rid):
    return get(conn, "SELECT * FROM mg_rt_activity WHERE id=?", (rid,))


def members(conn, rid, *, with_session=False):
    if with_session:
        return rows(conn, """SELECT s.*,c.session_key FROM mg_rt_seats s
            LEFT JOIN mg_rt_claims c ON c.actor=s.actor AND c.slot='multi' AND c.target=s.activity
            WHERE s.activity=? ORDER BY s.seat""", (rid,))
    return rows(conn, "SELECT * FROM mg_rt_seats WHERE activity=? ORDER BY seat", (rid,))


def revoked_room_member(conn, people, now):
    sessions = active_session_keys(conn, [p["actor"] for p in people], now, protect=True)
    return next((p["actor"] for p in people
                 if not p["session_key"] or sessions.get(p["actor"]) != p["session_key"]), None)


def claim(conn, actor, slot):
    return get(conn, "SELECT * FROM mg_rt_claims WHERE actor=? AND slot=?", (actor, slot))


def host_id(body):
    host = body.get("hostInstance")
    if not isinstance(host, str) or not 1 <= len(host) <= 128:
        fail("INVALID_HOST")
    return host


def control(conn, user, target, body, *, active_at=None):
    c = get(conn, "SELECT * FROM mg_rt_claims WHERE actor=? AND target=?", (user["actor"], target))
    check_control(c, user, body)
    if active_at is not None and c["expires"] <= active_at:
        fail("CONTROL_EXPIRED", 409)
    return c


def check_control(c, user, body):
    if (
        not c
        or c["host"] != host_id(body)
        or body.get("controlEpoch") != c["epoch"]
        or c["session_key"] != keyed(user["session_id"])
    ):
        fail("CONTROL_MOVED", 409)
    return c


def new_claim(conn, user, game, target, slot, body, now):
    epoch = secrets.randbelow(2**48) + 1
    count = conn.execute(
        """INSERT INTO mg_rt_claims(actor,slot,game,target,host,epoch,session_key,expires,touched,interacted)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
        (
            user["actor"],
            slot,
            game,
            target,
            host_id(body),
            epoch,
            keyed(user["session_id"]),
            now + LEASE_MS,
            now,
            now,
        ),
    ).rowcount
    if not count:
        fail("MULTIPLAYER_ACTIVITY_EXISTS" if slot == "multi" else "ACTIVITY_EXISTS", 409)


def new_activity(conn, game, mode, owner, cap, body, now):
    rid = uid()
    requested = body.get("startRank")
    if requested is not None:
        if (
            mode == "random"
            or (mode == "private" and game["config"]["modes"]["multiplayer"]["kind"] != "coop")
            or not integer(requested, 0, progress(conn, game, owner)["rank"])
        ):
            fail("INVALID_START_RANK")
    team = body.get("teamName", "")
    if not isinstance(team, str) or len(team) > 48:
        fail("INVALID_TEAM_NAME")
    conn.execute(
        """INSERT INTO mg_rt_activity(id,game,version,mode,phase,owner,capacity,requested,created,team_name,chat_enabled)
        VALUES(?,?,?,?,'waiting',?,?,?,?,?,?)""",
        (
            rid,
            game["id"],
            game["version_id"],
            mode,
            owner,
            cap,
            requested,
            now,
            team.strip(),
            int("room.chat" in game["config"].get("capabilities", [])),
        ),
    )
    return rid


def add_seat(conn, rid, user, seat, now):
    name = prefs(conn, user["actor"])["displayAlias"] or user.get("display_name") or f"玩家 {seat}"
    taken = {p["name"] for p in rows(conn, "SELECT name FROM mg_rt_seats WHERE activity=?", (rid,))}
    name = name[:80]
    if name in taken:
        suffix = f" ·{seat}号位"
        base = name[: 80 - len(suffix) - 2]
        name = base + suffix
        for copy in range(2, 12):
            if name not in taken:
                break
            name = f"{base}{suffix}{copy}"
    conn.execute(
        "INSERT INTO mg_rt_seats VALUES(?,?,?,?,?,?)", (rid, user["actor"], seat, uid(), name[:80], now)
    )


def forbidden_pairs(conn, actors):
    if len(actors) < 2:
        return set()
    marks = ",".join("?" for _ in actors)
    result = set()
    for table in ("private_message_blocks", "social_blocks"):
        for row in conn.execute(
            f"SELECT owner_identity,blocked_identity FROM {table} WHERE owner_identity IN ({marks}) AND blocked_identity IN ({marks})",
            (*actors, *actors),
        ):
            result.add(tuple(sorted((row["owner_identity"], row["blocked_identity"]))))
    return result


def valid_member(conn, actor, game_id, version=None):
    try:
        user = platform.load_actor(conn, actor)
        if not user:
            return None
        platform.require_play(conn, user, game_id, version, definition=False)
        return user
    except HTTPException as exc:
        if exc.status_code in (401, 403, 404, 410):
            return None
        raise


def roster_is_eligible(conn, game, actors):
    # On an unrestricted public game, eligibility reduces to current human-account
    # facts; load these once for the whole room instead of ten separate manifests.
    if game.get("scope") != "public" or game.get("min_tier") != 0 or game.get("max_tier") != 10:
        return all(valid_member(conn, a, game["id"], game["version_id"]) for a in actors)
    from .account_control_service import table_exists

    student_ids = [int(a.split(":")[1]) for a in actors if a.startswith("student:")]
    teacher_ids = [int(a.split(":")[1]) for a in actors if a.startswith("teacher:")]
    if len(student_ids) + len(teacher_ids) != len(actors):
        return False
    if student_ids:
        marks = ",".join("?" for _ in student_ids)
        students = rows(
            conn, f"SELECT id,enrollment_status,control_mode FROM students WHERE id IN ({marks})", student_ids
        )
        if len(students) != len(student_ids) or any(
            (s.get("enrollment_status") or "active") != "active"
            or str(s.get("control_mode") or "human").lower() == "ai"
            for s in students
        ):
            return False
        if table_exists(conn, "wanderer_agents") and get(
            conn, f"SELECT student_id FROM wanderer_agents WHERE student_id IN ({marks}) LIMIT 1", student_ids
        ):
            return False
    if teacher_ids:
        marks = ",".join("?" for _ in teacher_ids)
        teachers = rows(conn, f"SELECT id,is_active FROM teachers WHERE id IN ({marks})", teacher_ids)
        if len(teachers) != len(teacher_ids) or any(
            t.get("is_active") not in (None, 1, True) for t in teachers
        ):
            return False
    return True


def start_rank(conn, game, room, participants):
    if room["requested"] is not None:
        return room["requested"]
    actors = [p["actor"] for p in participants]
    if not actors:
        return 0
    w = game["writeback"]
    marks = ",".join("?" for _ in actors)
    return get(
        conn,
        f"""SELECT COALESCE(MAX(rank),0) AS r FROM mg_rt_progress WHERE game=?
        AND series=? AND save_ver=? AND actor IN ({marks})""",
        (game["id"], w["series"], w["saveVersion"], *actors),
    )["r"]


def countdown(conn, game, rid, now):
    room = activity(conn, rid)
    planned = start_rank(conn, game, room, members(conn, rid))
    conn.execute(
        "UPDATE mg_rt_activity SET phase='countdown',gen=gen+1,planned=?,starts=? WHERE id=?",
        (planned, now + 5000, rid),
    )
    event(conn, rid, "countdown.started", now)


def remember_team(conn, room, people, now):
    fingerprint = keyed(compact(sorted(p["actor"] for p in people)))
    for p in people:
        actor = p["actor"]
        conn.execute(
            """INSERT INTO mg_rt_templates VALUES(?,?,?,?,?,?) ON CONFLICT(actor,game,fingerprint)
            DO UPDATE SET activity=excluded.activity,last_used=excluded.last_used""",
            (uid(), actor, room["game"], fingerprint, room["id"], now),
        )
        conn.execute(
            """DELETE FROM mg_rt_templates WHERE actor=? AND id NOT IN
            (SELECT id FROM mg_rt_templates WHERE actor=? ORDER BY last_used DESC,id LIMIT 20)""",
            (actor, actor),
        )


def start_run(conn, game, rid, now):
    room = activity(conn, rid)
    people = members(conn, rid)
    rank = start_rank(conn, game, room, people)
    if room["mode"] != "solo" and room["planned"] != rank:
        countdown(conn, game, rid, now)
        return
    run = rid if room["mode"] == "solo" else uid()
    conn.execute(
        """UPDATE mg_rt_activity SET run=?,phase='running',planned=?,started=?,revision=1,state_json=?
        WHERE id=?""",
        (run, rank, now, compact(initial_state(game, rank)), rid),
    )
    # Acquire shared-progress rows in the same order as an in-play advancement.
    for p in sorted(people, key=lambda person: person["actor"]):
        advance_progress(conn, game, p["actor"], rank, run, now)
    if room["mode"] == "private":
        remember_team(conn, room, people, now)
    conn.execute("UPDATE mg_rt_codes SET activity=NULL WHERE activity=?", (rid,))
    event(conn, rid, "run.started", now)


def close(conn, rid, phase, outcome, now):
    if conn.execute(
        """UPDATE mg_rt_activity SET phase=?,outcome=?,ended=?,revision=revision+1,code_cipher=NULL
        WHERE id=? AND phase IN ('waiting','countdown','running','reconnecting','paused')""",
        (phase, outcome, now, rid),
    ).rowcount:
        conn.execute("DELETE FROM mg_rt_claims WHERE target=?", (rid,))
        conn.execute("UPDATE mg_rt_codes SET activity=NULL WHERE activity=?", (rid,))
        conn.execute(
            """INSERT INTO mg_rt_completed_scopes(ref,game,at_ms) SELECT id,game,? FROM mg_rt_activity
            WHERE id=? ON CONFLICT DO NOTHING""",
            (now, rid),
        )
        conn.execute(
            """INSERT INTO mg_rt_completed_scopes(ref,game,at_ms) SELECT id,game,? FROM mg_rt_tickets
            WHERE activity=? AND phase='assigned' ON CONFLICT DO NOTHING""",
            (now, rid),
        )
        event(conn, rid, "run." + phase, now)


def flush_solo(conn, user, game, target, values, revision):
    current = activity(conn, target)
    if not current or current["mode"] != "solo" or current["phase"] != "running":
        fail("ACTIVITY_ENDED", 409)
    version = (
        game
        if current["version"] == game["version_id"]
        else platform.require_play(conn, user, game["id"], current["version"])
    )
    save_private(conn, version, user["actor"], values, revision)


def solo_switch(conn, user, game, body, now):
    actor = user["actor"]
    prior = claim(conn, actor, "solo:" + str(game["id"]))
    provided = body.get("fromSolo")
    if not prior:
        if provided:
            fail("CONTROL_MOVED", 409)
        return
    if not isinstance(provided, dict) or provided.get("runId") != prior["target"]:
        fail("SOLO_FLUSH_REQUIRED", 409)
    control(conn, user, prior["target"], {**provided, "hostInstance": host_id(body)}, active_at=now)
    if "pendingPrivateSave" in provided:
        flush_solo(
            conn,
            user,
            game,
            prior["target"],
            provided["pendingPrivateSave"],
            provided.get("expectedPrivateRevision"),
        )
    conn.execute("UPDATE mg_rt_activity SET phase='paused' WHERE id=?", (prior["target"],))
    conn.execute("DELETE FROM mg_rt_claims WHERE actor=? AND slot=?", (actor, prior["slot"]))
    event(conn, prior["target"], "run.paused", now)


def open_solo(conn, user, game, body, now):
    actor = user["actor"]
    multi = claim(conn, actor, "multi")
    if multi and multi["game"] == str(game["id"]):
        fail("MULTIPLAYER_ACTIVITY_EXISTS", 409)
    slot = "solo:" + str(game["id"])
    prev = claim(conn, actor, slot)
    if prev and "startRank" not in body:
        return prev["target"]
    if not game["config"]["modes"]["solo"]:
        fail("UNSUPPORTED_MODE")
    if prev:
        control(conn, user, prev["target"], body, active_at=now)
        if "privateSave" in body:
            flush_solo(
                conn, user, game, prev["target"], body["privateSave"], body.get("expectedPrivateRevision")
            )
        close(conn, prev["target"], "ended", "replay", now)
    paused = get(
        conn,
        """SELECT a.id FROM mg_rt_activity a JOIN mg_rt_seats s ON s.activity=a.id
        WHERE a.game=? AND a.version=? AND a.phase='paused' AND a.mode='solo' AND s.actor=?
        ORDER BY a.started DESC,a.id LIMIT 1""",
        (game["id"], game["version_id"], actor),
    )
    active = rows(
        conn, "SELECT * FROM mg_rt_claims WHERE actor=? AND slot<>'multi' ORDER BY interacted,game", (actor,)
    )
    if len(active) >= 5:
        victim = active[0]
        # Another game's manager can revoke or delete this LRU victim concurrently.
        # A terminal room must never be restored by a cross-game eviction.
        paused_victim = conn.execute(
            "UPDATE mg_rt_activity SET phase='paused' WHERE id=? AND phase IN ('running','reconnecting')",
            (victim["target"],),
        ).rowcount
        conn.execute(
            "DELETE FROM mg_rt_claims WHERE actor=? AND slot=? AND target=?",
            (actor, victim["slot"], victim["target"]),
        )
        if paused_victim:
            event(conn, victim["target"], "run.paused", now)
    if paused and "startRank" not in body:
        rid = paused["id"]
        conn.execute("UPDATE mg_rt_activity SET phase='running' WHERE id=?", (rid,))
        new_claim(conn, user, game["id"], rid, slot, body, now)
        event(conn, rid, "run.resumed", now)
        return rid
    rid = new_activity(conn, game, "solo", actor, 1, body, now)
    new_claim(conn, user, game["id"], rid, slot, body, now)
    add_seat(conn, rid, user, 1, now)
    start_run(conn, game, rid, now)
    return rid


def create_private(conn, user, game, body, now):
    mode = game["config"]["modes"]["multiplayer"]
    if not mode or not mode["privateRooms"]:
        fail("UNSUPPORTED_MODE")
    if body.get("templateId"):
        template = get(
            conn,
            """SELECT a.capacity,a.team_name FROM mg_rt_templates t JOIN mg_rt_activity a
            ON t.activity=a.id WHERE t.id=? AND t.actor=? AND t.game=?""",
            (body["templateId"], user["actor"], game["id"]),
        )
        if not template:
            fail("NOT_FOUND", 404)
        body = {**{"targetSize": template["capacity"], "teamName": template["team_name"]}, **body}
    cap = body.get("targetSize")
    if not integer(cap, mode["minPlayers"], mode["maxPlayers"]):
        fail("INVALID_SIZE")
    solo_switch(conn, user, game, body, now)
    rid = new_activity(conn, game, "private", user["actor"], cap, body, now)
    new_claim(conn, user, game["id"], rid, "multi", body, now)
    add_seat(conn, rid, user, 1, now)
    issue_code(conn, rid, game["id"], now)
    event(conn, rid, "room.created", now)
    return rid


def join_private(conn, user, game, body, now):
    raw = body.get("code")
    code = (
        "".join(ch for ch in raw.strip().upper() if ch not in " -")
        if isinstance(raw, str) and len(raw) <= 20
        else ""
    )
    row = get(
        conn,
        """SELECT a.* FROM mg_rt_codes c JOIN mg_rt_activity a ON c.activity=a.id
        WHERE c.digest=? AND a.code_lookup=c.digest AND c.expires>? AND a.game=? AND a.version=? AND a.phase='waiting'""",
        (keyed("code:" + code), now, game["id"], game["version_id"]),
    )
    if not row:
        fail("ROOM_UNAVAILABLE", 404)
    rid = row["id"]
    people = members(conn, rid)
    if len(people) >= row["capacity"] or forbidden_pairs(
        conn, [user["actor"], *(p["actor"] for p in people)]
    ):
        fail("ROOM_UNAVAILABLE", 404)
    seat = next(i for i in range(1, row["capacity"] + 1) if not any(p["seat"] == i for p in people))
    solo_switch(conn, user, game, body, now)
    new_claim(conn, user, game["id"], rid, "multi", body, now)
    add_seat(conn, rid, user, seat, now)
    event(conn, rid, "room.member_joined", now)
    if len(people) + 1 == row["capacity"]:
        countdown(conn, game, rid, now)
    return rid


def enqueue(conn, user, game, body, now):
    mode = game["config"]["modes"]["multiplayer"]
    cap = body.get("targetSize")
    if not mode or not integer(cap, 2, 10) or cap not in mode["randomSizes"] or "startRank" in body:
        fail("INVALID_SIZE")
    solo_switch(conn, user, game, body, now)
    ticket = uid()
    new_claim(conn, user, game["id"], ticket, "multi", body, now)
    conn.execute(
        "INSERT INTO mg_rt_tickets(id,game,version,capacity,actor,phase,queued) VALUES(?,?,?,?,?,'waiting',?)",
        (ticket, game["id"], game["version_id"], cap, user["actor"], now),
    )
    match(conn, game["id"], game["version_id"], cap, now, game=game)
    return ticket


def match(conn, game_id, version_id, cap, now, *, game=None):
    keys = (str(game_id), str(version_id), cap)
    bucket = get(conn, "SELECT anchor FROM mg_rt_bucket WHERE game=? AND version=? AND capacity=?", keys)
    anchor = json.loads(bucket["anchor"]) if bucket else [0, ""]
    sql = """SELECT t.*,c.session_key FROM mg_rt_tickets t JOIN mg_rt_claims c ON c.target=t.id AND c.actor=t.actor
        WHERE t.game=? AND t.version=? AND t.capacity=? AND t.phase='waiting' AND c.expires>?"""
    candidates = rows(
        conn,
        sql + " AND (t.queued>? OR (t.queued=? AND t.id>?)) ORDER BY t.queued,t.id LIMIT 128",
        (*keys, now, anchor[0], anchor[0], anchor[1]),
    )
    if len(candidates) < 128 and anchor[1]:
        candidates += rows(
            conn,
            sql + " AND (t.queued<? OR (t.queued=? AND t.id<=?)) ORDER BY t.queued,t.id LIMIT ?",
            (*keys, now, anchor[0], anchor[0], anchor[1], 128 - len(candidates)),
        )
    if len(candidates) < cap:
        return
    actors = [p["actor"] for p in candidates]
    blocked = forbidden_pairs(conn, actors)
    users = {}
    sessions = active_session_keys(conn, actors, now)
    for ticket in candidates:
        if sessions.get(ticket["actor"]) != ticket["session_key"]:
            release_ticket(conn, ticket["id"], "expired")
            continue
        user = valid_member(conn, ticket["actor"], game_id)
        if user:
            users[ticket["actor"]] = user
        else:
            release_ticket(conn, ticket["id"], "expired")
    valid = [p for p in candidates if p["actor"] in users]
    for first in valid:
        others = [p for p in valid if p != first]
        secrets.SystemRandom().shuffle(others)
        group = [first]
        for p in others:
            if all(tuple(sorted((q["actor"], p["actor"]))) not in blocked for q in group):
                group.append(p)
                if len(group) == cap:
                    if game is None:
                        game = platform.require_play(conn, users[first["actor"]], game_id)
                    if str(game["version_id"]) != str(version_id):
                        return
                    rid = new_activity(conn, game, "random", "", cap, {}, now)
                    for seat, item in enumerate(group, 1):
                        add_seat(conn, rid, users[item["actor"]], seat, now)
                        conn.execute(
                            "UPDATE mg_rt_claims SET target=? WHERE actor=? AND target=?",
                            (rid, item["actor"], item["id"]),
                        )
                        conn.execute(
                            "UPDATE mg_rt_tickets SET phase='assigned',activity=?,seq=seq+1 WHERE id=?",
                            (rid, item["id"]),
                        )
                    countdown(conn, game, rid, now)
                    return
    last = candidates[-1]
    conn.execute(
        """INSERT INTO mg_rt_bucket VALUES(?,?,?,?) ON CONFLICT(game,version,capacity)
        DO UPDATE SET anchor=excluded.anchor""",
        (*keys, compact([last["queued"], last["id"]])),
    )


def release_ticket(conn, ticket, phase):
    conn.execute(
        """INSERT INTO mg_rt_completed_scopes(ref,game,at_ms) SELECT id,game,? FROM mg_rt_tickets
        WHERE id=? AND phase='waiting' ON CONFLICT DO NOTHING""",
        (platform.now_ms(), ticket),
    )
    conn.execute("UPDATE mg_rt_tickets SET phase=?,seq=seq+1 WHERE id=? AND phase='waiting'", (phase, ticket))
    conn.execute("DELETE FROM mg_rt_claims WHERE target=?", (ticket,))


def leave(conn, room, actor, now):
    rid = room["id"]
    if room["phase"] not in ACTIVE:
        return
    if room["mode"] == "solo" or room["phase"] in ("running", "reconnecting"):
        close(conn, rid, "interrupted", "left", now)
        return
    conn.execute("DELETE FROM mg_rt_claims WHERE target=? AND actor=?", (rid, actor))
    conn.execute("DELETE FROM mg_rt_seats WHERE activity=? AND actor=?", (rid, actor))
    remaining = members(conn, rid)
    if room["mode"] == "random":
        for t in rows(conn, "SELECT * FROM mg_rt_tickets WHERE activity=? AND phase='assigned'", (rid,)):
            c = claim(conn, t["actor"], "multi")
            back = c and c["target"] == rid and c["expires"] > now
            phase = "waiting" if back else "canceled"
            conn.execute(
                "UPDATE mg_rt_tickets SET phase=?,activity=NULL,seq=seq+1 WHERE id=?", (phase, t["id"])
            )
            if back:
                conn.execute(
                    "UPDATE mg_rt_claims SET target=? WHERE actor=? AND target=?", (t["id"], t["actor"], rid)
                )
            else:
                conn.execute(
                    "INSERT INTO mg_rt_completed_scopes(ref,game,at_ms) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                    (t["id"], t["game"], now),
                )
        close(conn, rid, "closed", "queue_changed", now)
    elif not remaining:
        close(conn, rid, "closed", "empty", now)
    else:
        owner = (
            min(remaining, key=lambda m: (m["joined"], m["seat"]))["actor"]
            if room["owner"] == actor
            else room["owner"]
        )
        conn.execute(
            """UPDATE mg_rt_activity SET owner=?,requested=?,starts=NULL,planned=NULL,phase='waiting',
            gen=gen+1 WHERE id=?""",
            (owner, room["requested"] if owner == room["owner"] else None, rid),
        )
        if room["phase"] == "countdown":
            issue_code(conn, rid, room["game"], now)
        event(conn, rid, "room.member_left", now)


def advance_room(conn, room, now):
    if room["phase"] not in ACTIVE:
        return
    rid = room["id"]
    people = members(conn, rid, with_session=room["phase"] == "countdown")
    if not people:
        close(conn, rid, "closed", "empty", now)
        return
    expired = rows(conn, "SELECT actor,expires FROM mg_rt_claims WHERE target=? AND expires<=?", (rid, now))
    if room["phase"] in ("waiting", "countdown"):
        if room["mode"] == "private" and room["phase"] == "waiting" and room["code_until"] <= now:
            close(conn, rid, "closed", "expired", now)
            return
        if expired:
            leave(conn, room, expired[0]["actor"], now)
            return
        if room["phase"] == "waiting" or room["starts"] > now:
            return
    elif expired:
        if min(r["expires"] for r in expired) + GRACE_MS <= now:
            close(conn, rid, "interrupted", "disconnected", now)
        elif room["phase"] != "reconnecting":
            conn.execute("UPDATE mg_rt_activity SET phase='reconnecting' WHERE id=?", (rid,))
            event(conn, rid, "run.paused", now)
        return
    elif room["phase"] == "reconnecting":
        conn.execute("UPDATE mg_rt_activity SET phase='running' WHERE id=?", (rid,))
        event(conn, rid, "run.resumed", now)
        return
    else:
        return
    revoked = revoked_room_member(conn, people, now)
    if revoked:
        leave(conn, room, revoked, now)
        return
    actors = [m["actor"] for m in people]
    first = valid_member(conn, actors[0], room["game"])
    if not first:
        leave(conn, room, actors[0], now)
        return
    game = platform.require_play(conn, first, room["game"])
    if str(game["version_id"]) != room["version"]:
        close(conn, rid, "closed", "version_changed", now)
        return
    if not roster_is_eligible(conn, game, actors):
        for actor in actors:
            if not valid_member(conn, actor, room["game"]):
                leave(conn, room, actor, now)
                return
        close(conn, rid, "closed", "eligibility_changed", now)
        return
    if forbidden_pairs(conn, actors):
        close(conn, rid, "closed", "eligibility_changed", now)
        return
    start_run(conn, game, rid, now)
