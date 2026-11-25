"""Actor-scoped read models for the trusted host; SDK receives a smaller projection."""

import json

from . import minigame_platform as platform
from .minigame_runtime_rooms import activity, claim, members, valid_member
from .minigame_runtime_rules import fail
from .minigame_runtime_store import cipher, get, keyed, progress, rows
from .social_common import blocked_by, blockers_of
from .social_relations import friend_identities


VISIBLE_SEAT = """NOT EXISTS (SELECT 1 FROM social_blocks b WHERE (b.owner_identity=s.actor AND b.blocked_identity=?)
            OR (b.blocked_identity=s.actor AND b.owner_identity=?))
        AND NOT EXISTS (SELECT 1 FROM private_message_blocks b WHERE (b.owner_identity=s.actor AND b.blocked_identity=?)
            OR (b.blocked_identity=s.actor AND b.owner_identity=?))"""


def bound_game(conn, user, room, *, membership_checked=False, definition=True):
    if not membership_checked and not get(
        conn, "SELECT seat FROM mg_rt_seats WHERE activity=? AND actor=?", (room["id"], user["actor"])
    ):
        fail("NOT_FOUND", 404)
    pinned = room["version"] if room["run"] and room["phase"] in ("running", "reconnecting") else None
    return platform.require_play(conn, user, room["game"], pinned, definition=definition)


def project(conn, user, rid, body, now, game=None, *, room=None, claims=None):
    room = room or activity(conn, rid)
    if not room:
        fail("NOT_FOUND", 404)
    actor = user["actor"]
    people = rows(
        conn,
        f"""SELECT s.actor,s.alias,s.name,s.seat,c.host,c.epoch,c.expires,c.session_key,c.target,c.game
            FROM mg_rt_seats s LEFT JOIN mg_rt_claims c ON c.target=s.activity AND c.actor=s.actor
            WHERE s.activity=? AND {VISIBLE_SEAT} ORDER BY s.seat""",
        (rid, actor, actor, actor, actor),
    )
    if user["actor"] not in {seat["actor"] for seat in people}:
        fail("NOT_FOUND", 404)
    if game is None:
        game = bound_game(
            conn,
            user,
            room,
            membership_checked=True,
            definition=room["phase"] == "countdown" or room["chat_enabled"] is None,
        )
    claim_rows = {seat["actor"]:seat for seat in people if seat["target"] is not None}
    if claims is not None:
        claims.update(claim_rows)
    c = claim_rows.get(user["actor"])
    host_matches = bool(
        c and c["host"] == body.get("hostInstance") and c["session_key"] == keyed(user["session_id"])
    )
    roster = []
    me = None
    for seat in people:
        lease = claim_rows.get(seat["actor"])
        view = {
            "playerId": seat["alias"],
            "displayName": seat["name"],
            "seat": seat["seat"],
            "status": "active" if lease and lease["expires"] > now else "reconnecting",
        }
        if room["ended"]:
            view["status"] = "ended"
        if seat["actor"] == user["actor"]:
            me = view
        roster.append(view)
    if me is None:
        fail("NOT_FOUND", 404)
    state = json.loads(room["state_json"])
    data = {
        "gameId": room["game"],
        "versionId": room["version"],
        "roomId": None if room["mode"] == "solo" else rid,
        "runId": room["run"],
        "mode": room["mode"],
        "phase": room["phase"],
        "generation": room["gen"],
        "startsAt": room["starts"],
        "startedAt": room["started"],
        "seq": room["seq"],
        "revision": room["revision"],
        "serverNow": now,
        "members": roster,
        "me": me,
        "controlEpoch": c["epoch"] if c else None,
        "readOnly": not host_matches,
        "hostInstance": body.get("hostInstance"),
        "startRank": room["planned"],
        "targetSize": room["capacity"],
        "isOwner": room["owner"] == user["actor"],
        "outcome": room["outcome"],
        "publicState": state,
        "state": state,
        "turnSeat": state.get("turnSeat"),
        "teamName": room["team_name"],
        "roomCanChat": bool(room["chat_enabled"])
        if room["chat_enabled"] is not None
        else "room.chat" in game["config"].get("capabilities", []),
    }
    if room["phase"] in ("waiting", "countdown") and room["mode"] == "private" and room["code_cipher"]:
        data["code"] = cipher().decrypt(room["code_cipher"].encode()).decode()
        data["codeExpiresAt"] = room["code_until"]
    if room["phase"] == "countdown":
        data["ownRank"] = progress(conn, game, user["actor"])["rank"]
    if room["phase"] == "reconnecting":
        due = [row["expires"] + 60000 for row in claim_rows.values() if row["expires"] <= now]
        data["reconnectUntil"] = min(due, default=now + 60000)
    return data


def ticket_view(conn, user, tid, body, now):
    ticket = get(conn, "SELECT * FROM mg_rt_tickets WHERE actor=? AND id=?", (user["actor"], tid))
    if not ticket:
        fail("NOT_FOUND", 404)
    if ticket["phase"] == "assigned" and ticket["activity"]:
        data = project(conn, user, ticket["activity"], body, now)
        data["ticketId"] = tid
        return data
    platform.require_play(conn, user, ticket["game"], definition=False)
    c = claim(conn, user["actor"], "multi")
    active = c and c["target"] == tid
    return {
        "gameId": ticket["game"],
        "versionId": ticket["version"],
        "ticketId": tid,
        "roomId": None,
        "runId": None,
        "mode": "random",
        "phase": ticket["phase"],
        "seq": ticket["seq"],
        "serverNow": now,
        "startsAt": None,
        "targetSize": ticket["capacity"],
        "queuedAt": ticket["queued"],
        "hostInstance": body.get("hostInstance"),
        "controlEpoch": c["epoch"] if active else None,
        "readOnly": not (
            active and c["host"] == body.get("hostInstance") and c["session_key"] == keyed(user["session_id"])
        ),
    }


def read_activity(conn, user, body, now):
    wanted = body.get("gameId")
    result = {"solo": None, "multiplayer": None}
    for c in rows(conn, "SELECT * FROM mg_rt_claims WHERE actor=?", (user["actor"],)):
        if c["slot"] != "multi" and c["game"] != wanted:
            continue
        if not valid_member(conn, user["actor"], c["game"]):
            continue
        if activity(conn, c["target"]):
            data = project(conn, user, c["target"], body, now)
        else:
            data = ticket_view(conn, user, c["target"], body, now)
        result["multiplayer" if c["slot"] == "multi" else "solo"] = data
    return result


def history(conn, user, body):
    game_id = body.get("gameId")
    if game_id:
        platform.require_play(conn, user, game_id)
    try:
        limit = max(1, min(20, int(body.get("limit", 10))))
    except (TypeError, ValueError):
        fail("INVALID_LIMIT")
    args = [user["actor"]]
    conditions = ["s.actor=?", "a.run IS NOT NULL"]
    if game_id:
        conditions.append("a.game=?")
        args.append(game_id)
    cursor = body.get("cursor")
    if cursor:
        try:
            started, last = str(cursor).split(".", 1)
            args.extend((int(started), int(started), last))
        except ValueError:
            fail("INVALID_CURSOR")
        conditions.append("(a.started<? OR (a.started=? AND a.id<?))")
    sql = """SELECT a.* FROM mg_rt_activity a JOIN mg_rt_seats s ON s.activity=a.id WHERE """ + " AND ".join(
        conditions
    )
    records = rows(conn, sql + " ORDER BY a.started DESC,a.id DESC LIMIT ?", (*args, limit + 1))
    checked = {}
    items = []
    for row in records[:limit]:
        if row["game"] not in checked:
            checked[row["game"]] = bool(valid_member(conn, user["actor"], row["game"]))
        if checked[row["game"]]:
            items.append(
                {
                    "gameId": row["game"],
                    "runId": row["run"],
                    "startedAt": row["started"],
                    "endedAt": row["ended"],
                    "mode": row["mode"],
                    "outcome": row["outcome"],
                    "phase": row["phase"],
                    "rank": json.loads(row["state_json"]).get("rank", 0),
                }
            )
    end = records[limit - 1] if len(records) > limit else None
    return {"items": items, "nextCursor": f"{end['started']}.{end['id']}" if end else None}


def teams(conn, user, body):
    actor = user["actor"]
    visible = friend_identities(conn, actor) - blocked_by(conn, actor) - blockers_of(conn, actor)
    items = []
    for row in rows(
        conn,
        """SELECT t.*,a.capacity,a.team_name FROM mg_rt_templates t JOIN mg_rt_activity a ON a.id=t.activity
        WHERE t.actor=? ORDER BY t.last_used DESC,t.id LIMIT 20""",
        (actor,),
    ):
        if (body.get("gameId") and row["game"] != body["gameId"]) or not valid_member(
            conn, actor, row["game"]
        ):
            continue
        names = [p["name"] for p in members(conn, row["activity"]) if p["actor"] in visible]
        items.append(
            {
                "templateId": row["id"],
                "gameId": row["game"],
                "targetSize": row["capacity"],
                "teamName": row["team_name"],
                "lastUsedAt": row["last_used"],
                "knownMembers": names,
            }
        )
    return {"items": items, "nextCursor": None}


def _active_friends(conn, actors):
    """Resolve human accounts for one bounded page of claims, including legacy AI bindings."""
    from .account_control_service import table_exists

    numbers = {"student": [], "teacher": []}
    for actor in actors:
        role, _, key = actor.partition(":")
        if role in numbers and key.isascii() and key.isdigit() and len(key) <= 19:
            numeric_id = int(key)
            if numeric_id <= 2**63 - 1:
                numbers[role].append(numeric_id)
    active = {}
    for role, ids in numbers.items():
        if not ids:
            continue
        marks = ",".join("?" for _ in ids)
        if role == "teacher":
            sql = f"SELECT id,is_super_admin FROM teachers WHERE id IN ({marks}) AND COALESCE(is_active,1)=1"
        else:
            sql = (
                f"SELECT id,class_id FROM students WHERE id IN ({marks}) AND COALESCE(NULLIF(enrollment_status,''),'active')='active' "
                "AND LOWER(COALESCE(control_mode,'human'))<>'ai'"
            )
            if table_exists(conn, "wanderer_agents"):
                sql += " AND NOT EXISTS (SELECT 1 FROM wanderer_agents w WHERE w.student_id=students.id)"
        for row in conn.execute(sql, ids):
            identity = role + ":" + str(row["id"])
            active[identity] = {"actor": identity, "role": role, **dict(row)}
    return active


def _friends_playing(conn, user, query, now, limit):
    from .minigame_classroom_access import class_timetable

    actor = user["actor"]
    accepted = """SELECT identity_b AS friend FROM social_friendships WHERE identity_a=? AND status='accepted'
        UNION ALL SELECT identity_a AS friend FROM social_friendships WHERE identity_b=? AND status='accepted'"""
    # Sorting first by mode makes a friend's eligible multiplayer run take priority over all their solo runs.
    order = "CASE WHEN a.mode='solo' THEN 1 ELSE 0 END,-c.interacted,c.actor,g.id"
    source = f"""FROM ({accepted}) f JOIN mg_rt_claims c ON c.actor=f.friend
        JOIN mg_rt_prefs pref ON pref.actor=c.actor AND pref.sharing=1
        JOIN mg_rt_activity a ON a.id=c.target AND a.phase='running'
        JOIN mg_rt_seats s ON s.activity=a.id AND s.actor=c.actor
        JOIN mg_games g ON g.id=a.game JOIN mg_versions v ON v.id=g.current_version_id AND v.game_id=g.id
        LEFT JOIN mg_version_authorship va ON va.version_id=v.id
        JOIN mg_versions pinned ON pinned.id=a.version AND pinned.game_id=g.id AND pinned.status='ready'
        WHERE c.expires>? AND c.interacted>0 AND a.run IS NOT NULL AND a.started IS NOT NULL
        AND {VISIBLE_SEAT} AND {query.visible}"""
    selected = {}
    schedules = {}
    anchor = None
    batch_size = 24
    while len(selected) < limit:
        args = [actor, actor, now, actor, actor, actor, actor, *query.args]
        after = ""
        if anchor:
            after = f" AND ({order})>(?,?,?,?)"
            args.extend(anchor)
        candidates = rows(
            conn,
            f"SELECT {query.card_columns},c.actor,s.name,c.interacted,a.mode {source}{after} ORDER BY {order} LIMIT ?",
            (*args, batch_size),
        )
        if not candidates:
            break
        active = _active_friends(
            conn, {r["actor"] for r in candidates} - selected.keys()
        )
        audiences = {}
        for row in candidates:
            identity = row["actor"]
            if identity in selected or identity not in active:
                continue
            friend = active[identity]
            needs_tier = friend["role"] == "student" and (
                row["min_tier"],
                row["max_tier"],
            ) != (0, 10)
            if friend.get("is_super_admin"):
                audience = {"admin": True, "tier": None}
            elif (
                row["scope"] == "class"
                and friend["role"] == "student"
                and not needs_tier
            ):
                # A student's class was read with the live account facts for this batch.
                audience = {"admin": False, "tier": None, "classes": {friend["class_id"]}}
            elif row["scope"] == "public" and not needs_tier:
                audience = {"admin": False, "tier": None}
            else:
                if identity not in audiences:
                    audiences[identity] = platform._audience(conn, friend)
                audience = audiences[identity]
            if not platform.allowed(row, friend, audience):
                continue
            if friend["role"] == "student":
                class_id = friend["class_id"]
                if class_id not in schedules:
                    schedules[class_id] = class_timetable(conn, class_id, now)
                if not schedules[class_id].decision(row["id"], now)["allowed"]:
                    continue
            selected[identity] = {
                **platform.public_game(row),
                "displayName": row["name"],
            }
            if len(selected) == limit:
                break
        if len(candidates) < batch_size:
            break
        last = candidates[-1]
        anchor = (
            int(last["mode"] == "solo"),
            -last["interacted"],
            last["actor"],
            last["id"],
        )
    return list(selected.values())


def runtime_sidebar(conn, user, query, limit):
    from .minigame_catalog import CATALOG

    from . import minigame_popularity

    recent = conn.execute(f"""WITH picks AS (
        SELECT g.id AS game_id,MAX(a.started) AS last_play FROM {CATALOG}
        JOIN mg_rt_activity a ON a.game=g.id JOIN mg_rt_seats s ON s.activity=a.id
        WHERE {query.visible} AND s.actor=? AND a.started IS NOT NULL
        GROUP BY g.id ORDER BY last_play DESC,game_id LIMIT ?)
        SELECT {query.card_columns} FROM {CATALOG} JOIN picks ON picks.game_id=g.id
        ORDER BY picks.last_play DESC,picks.game_id""", (*query.args, user["actor"], limit))
    now = platform.now_ms()
    return {
        "recent": [platform.public_game(row) for row in recent],
        "popular": minigame_popularity.popular(conn, query, now, limit),
        "friends": _friends_playing(conn, user, query, now, limit),
    }
