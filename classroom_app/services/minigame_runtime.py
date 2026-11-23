"""Persistent game runtime. Each public command executes inside one database transaction."""

import asyncio
import hashlib
import json
import threading

from fastapi import HTTPException

from . import minigame_platform as platform
from . import minigame_runtime_rooms as rooms
from . import minigame_runtime_views as views
from .minigame_runtime_rules import apply_intent, compact, fail
from .minigame_runtime_store import (
    active_session_keys,
    advance_progress,
    cipher,
    ensure_runtime_schema as ensure_runtime_schema,
    event,
    get,
    issue_code,
    keyed,
    lock,
    prefs,
    progress,
    rows,
    save_private,
    uid,
    update_prefs,
)
from .minigame_runtime_views import runtime_sidebar as runtime_sidebar


def has_active_version(conn, actor, game_id, version_id):
    return bool(
        get(
            conn,
            """SELECT a.id FROM mg_rt_activity a JOIN mg_rt_seats s ON s.activity=a.id
        JOIN mg_rt_claims c ON c.target=a.id AND c.actor=s.actor WHERE a.game=? AND a.version=? AND s.actor=?
        AND a.run IS NOT NULL AND a.phase IN ('running','reconnecting') AND c.expires>? LIMIT 1""",
            (str(game_id), str(version_id), actor, platform.now_ms() - rooms.GRACE_MS),
        )
    )


def impact_counts(conn, game_id):
    result = {}
    for name, table, predicate in (
        (
            "activeRooms",
            "activity",
            "mode<>'solo' AND phase IN ('waiting','countdown','running','reconnecting')",
        ),
        ("activeRuns", "activity", "run IS NOT NULL AND phase IN ('running','reconnecting')"),
        ("waitingTickets", "tickets", "phase='waiting'"),
    ):
        result[name] = conn.execute(
            f"SELECT COUNT(*) AS n FROM mg_rt_{table} WHERE game=? AND {predicate}", (game_id,)
        ).fetchone()["n"]
    result["playersWithProgress"] = conn.execute(
        "SELECT COUNT(DISTINCT actor) AS n FROM mg_rt_progress WHERE game=?", (game_id,)
    ).fetchone()["n"]
    for name, table in (("historyRows", "seats"), ("messages", "messages")):
        condition = " AND a.run IS NOT NULL" if name == "historyRows" else ""
        result[name] = conn.execute(
            f"SELECT COUNT(*) AS n FROM mg_rt_{table} m JOIN mg_rt_activity a ON m.activity=a.id WHERE a.game=?"
            + condition,
            (game_id,),
        ).fetchone()["n"]
    return result


def cancel_waiting(conn, game_id):
    now = platform.now_ms()
    for room in rows(
        conn, "SELECT id FROM mg_rt_activity WHERE game=? AND phase IN ('waiting','countdown')", (game_id,)
    ):
        rooms.close(conn, room["id"], "closed", "version_changed", now)
    for t in rows(conn, "SELECT id FROM mg_rt_tickets WHERE game=? AND phase='waiting'", (game_id,)):
        rooms.release_ticket(conn, t["id"], "canceled")


def on_publish(conn, game_id, version_id=None):
    cancel_waiting(conn, game_id)


def on_policy_change(conn, game_id):
    """Called after policy fields are updated, before the catalog transaction commits."""
    now = platform.now_ms()
    for r in rows(
        conn,
        "SELECT id FROM mg_rt_activity WHERE game=? AND phase IN ('waiting','countdown','running','reconnecting','paused')",
        (game_id,),
    ):
        for member in rooms.members(conn, r["id"]):
            if rooms.valid_member(conn, member["actor"], game_id):
                continue
            room = rooms.activity(conn, r["id"])
            if room["phase"] == "paused":
                rooms.close(conn, r["id"], "interrupted", "eligibility_changed", now)
            else:
                rooms.leave(conn, room, member["actor"], now)
    for ticket in rows(
        conn, "SELECT id,actor FROM mg_rt_tickets WHERE game=? AND phase='waiting'", (game_id,)
    ):
        if not rooms.valid_member(conn, ticket["actor"], game_id):
            rooms.release_ticket(conn, ticket["id"], "expired")


def on_unlist(conn, game_id):
    cancel_waiting(conn, game_id)
    now = platform.now_ms()
    for room in rows(
        conn,
        "SELECT id FROM mg_rt_activity WHERE game=? AND phase IN ('running','reconnecting','paused')",
        (game_id,),
    ):
        rooms.close(conn, room["id"], "interrupted", "unlisted", now)


def purge_game(conn, game_id):
    for table in (
        "receipts",
        "completed_scopes",
        "changes",
        "progress",
        "templates",
        "codes",
        "tickets",
        "bucket",
        "claims",
        "activity",
    ):
        conn.execute(f"DELETE FROM mg_rt_{table} WHERE game=?", (game_id,))


def resolve(conn, kind, target, user=None):
    if kind == "game":
        return str(target), None
    if kind == "account":
        return "", None
    if kind == "template":
        row = get(conn, "SELECT * FROM mg_rt_templates WHERE id=? AND actor=?", (target, user["actor"]))
    elif kind == "ticket":
        row = get(conn, "SELECT * FROM mg_rt_tickets WHERE id=? AND actor=?", (target, user["actor"]))
    elif kind in ("room", "run"):
        column = "run" if kind == "run" else "id"
        row = get(
            conn,
            f"SELECT a.* FROM mg_rt_activity a JOIN mg_rt_seats s ON s.activity=a.id WHERE a.{column}=? AND s.actor=?",
            (target, user["actor"]),
        )
    else:
        fail("INVALID_CONTEXT")
    if not row:
        fail("NOT_FOUND", 404)
    return str(row["game"]), row


def game_for(conn, user, game_id, row, kind):
    if not game_id:
        return None
    if kind in ("room", "run") and row:
        return views.bound_game(conn, user, row, membership_checked=True)
    return platform.require_play(conn, user, game_id)


def bind_sync(body):
    values = [(k, body.get(k + "Id")) for k in ("room", "run", "ticket") if body.get(k + "Id")]
    if len(values) != 1 or not isinstance(values[0][1], str) or len(values[0][1]) > 128:
        fail("INVALID_CONTEXT")
    return values[0]


def messages(conn, rid, user):
    actor = user["actor"]
    recent = rows(
        conn,
        f"""SELECT m.id,m.actor,m.body,m.at_ms,s.alias,s.name FROM mg_rt_messages m JOIN mg_rt_seats s
        ON s.activity=m.activity AND s.actor=m.actor WHERE m.activity=? AND m.at_ms>? AND {views.VISIBLE_SEAT}
        ORDER BY m.at_ms DESC,m.id DESC LIMIT 80""",
        (rid, platform.now_ms() - 7 * 86400000, actor, actor, actor, actor),
    )
    return [
        {"id": m["id"], "playerId": m["alias"], "displayName": m["name"], "text": m["body"], "at": m["at_ms"]}
        for m in reversed(recent)
    ]


def sync(conn, user, kind, target, body, now, *, row=None, claims=None):
    if kind == "ticket":
        snap = views.ticket_view(conn, user, target, body, now)
    else:
        row = row or resolve(conn, kind, target, user)[1]
        snap = views.project(conn, user, row["id"], body, now, room=row, claims=claims)
    rid = snap.get("roomId") or snap.get("runId")
    stream = rid or target
    try:
        after = int(body.get("after", 0) or 0)
    except (TypeError, ValueError):
        fail("INVALID_CURSOR")
    events = []
    if rid and after < snap["seq"]:
        for row in rows(
            conn, "SELECT * FROM mg_rt_events WHERE activity=? AND seq>? ORDER BY seq LIMIT 100", (rid, after)
        ):
            events.append(
                {
                    "streamId": stream,
                    "seq": row["seq"],
                    "type": row["kind"],
                    "revision": row["revision"],
                    "generation": row["generation"],
                    "serverTime": row["at_ms"],
                }
            )
        if events and (events[0]["seq"] != after + 1 or events[-1]["seq"] != snap["seq"]):
            # The complete snap supersedes any trimmed or oversized increment.
            events = []
    result = {
        "snapshot": snap,
        "streamId": stream,
        "throughSeq": snap["seq"],
        "serverNow": now,
        "events": events,
    }
    if rid and snap.get("roomId") and snap["phase"] in rooms.ACTIVE and snap.get("roomCanChat"):
        result["messages"] = messages(conn, rid, user)
    return result


def touch(conn, user, kind, row, body, now, control=None):
    target = row["activity"] if kind == "ticket" and row.get("phase") == "assigned" else row["id"]
    c = control or rooms.control(conn, user, target, body)
    if c["expires"] + rooms.GRACE_MS <= now:
        fail("ACTIVITY_ENDED", 410)
    if c["expires"] <= now:
        with_target = rooms.activity(conn, target)
        if not with_target or with_target["phase"] not in ("running", "reconnecting"):
            fail("ACTIVITY_ENDED", 410)
    identity = (c["actor"], c["target"], c["epoch"], c["host"], c["session_key"], c["game"])
    with _mail_lock:
        if len(_mail) < 4096 or identity in _mail:
            _mail[identity] = max(now, _mail.get(identity, 0))
    # Persist recovery immediately, bulk-coalesce ordinary liveness.
    if c["expires"] <= now:
        flush_one(conn, identity, now)


def flush_one(conn, identity, observed):
    actor, target, epoch, host, session, game = identity
    conn.execute(
        """UPDATE mg_rt_claims SET expires=CASE WHEN expires<? THEN ? ELSE expires END,
        touched=CASE WHEN touched<? THEN ? ELSE touched END WHERE actor=? AND target=? AND epoch=? AND host=?
        AND session_key=? AND game=? AND expires>?""",
        (
            observed + rooms.LEASE_MS,
            observed + rooms.LEASE_MS,
            observed,
            observed,
            actor,
            target,
            epoch,
            host,
            session,
            game,
            observed - rooms.GRACE_MS,
        ),
    )


def flush_batch(conn, batch, now):
    entries = sorted(batch.items())
    for index in range(0, len(entries), 64):
        chunk = entries[index : index + 64]
        actors = sorted({key[0] for key, _ in chunk})
        sessions = active_session_keys(conn, actors, now)
        updates = [
            (
                actor,
                target,
                epoch,
                host,
                sid,
                game,
                observed,
                observed + rooms.LEASE_MS,
                observed - rooms.GRACE_MS,
            )
            for (actor, target, epoch, host, sid, game), observed in chunk
            if sessions.get(actor) == sid
        ]
        if not updates:
            continue
        # Explicit casts keep the VALUES column types identical on SQLite and PostgreSQL.
        one = "(?,?,CAST(? AS BIGINT),?,?,?,CAST(? AS BIGINT),CAST(? AS BIGINT),CAST(? AS BIGINT))"
        values = ",".join(one for _ in updates)
        conn.execute(
            f"""WITH fresh(actor,target,epoch,host,sid,game,t,until_ms,allow_since) AS (VALUES {values})
            UPDATE mg_rt_claims SET expires=CASE WHEN expires<f.until_ms THEN f.until_ms ELSE expires END,
                touched=CASE WHEN touched<f.t THEN f.t ELSE touched END FROM fresh f
            WHERE mg_rt_claims.actor=f.actor AND mg_rt_claims.target=f.target AND mg_rt_claims.epoch=f.epoch
              AND mg_rt_claims.host=f.host AND mg_rt_claims.session_key=f.sid AND mg_rt_claims.game=f.game
              AND mg_rt_claims.expires>f.allow_since""",
            tuple(item for update in updates for item in update),
        )


def private_write(conn, user, game, body, now):
    actor = user["actor"]
    target = body.get("runId")
    _, room = resolve(conn, "run", target, user)
    if room["game"] != str(game["id"]) or room["phase"] != "running":
        fail("ACTIVITY_ENDED", 409)
    game = views.bound_game(conn, user, room)
    rooms.control(conn, user, room["id"], body, active_at=now)
    return save_private(conn, game, actor, body.get("privateSave"), body.get("expectedRevision"))


def apply_command(conn, user, game, op, kind, target, row, body, now):
    actor = user["actor"]
    if op == "prefs":
        return update_prefs(conn, actor, body)
    if op == "delete_template":
        conn.execute("DELETE FROM mg_rt_templates WHERE actor=? AND id=?", (actor, target))
        return {"deleted": True}
    if op == "private":
        return private_write(conn, user, game, body, now)
    if op == "takeover":
        specified = body.get("ticketId") or body.get("roomId") or body.get("runId")
        clauses = rows(conn, "SELECT * FROM mg_rt_claims WHERE actor=? AND game=?", (actor, game["id"]))
        c = (
            next(
                (
                    item
                    for item in clauses
                    if specified == item["target"]
                    or (rooms.activity(conn, item["target"]) or {}).get("run") == specified
                ),
                None,
            )
            if specified
            else (clauses[0] if len(clauses) == 1 else None)
        )
        if not c or c["epoch"] != body.get("controlEpoch") or c["expires"] + rooms.GRACE_MS <= now:
            fail("CONTROL_MOVED", 409)
        conn.execute(
            """UPDATE mg_rt_claims SET host=?,epoch=epoch+1,session_key=?,expires=?,touched=?
            WHERE actor=? AND target=? AND epoch=?""",
            (
                rooms.host_id(body),
                keyed(user["session_id"]),
                now + rooms.LEASE_MS,
                now,
                actor,
                c["target"],
                c["epoch"],
            ),
        )
        if rooms.activity(conn, c["target"]):
            event(conn, c["target"], "control.changed", now)
            return views.project(conn, user, c["target"], body, now)
        conn.execute("UPDATE mg_rt_tickets SET seq=seq+1 WHERE id=?", (c["target"],))
        return views.ticket_view(conn, user, c["target"], body, now)
    if op in ("solo", "create", "join", "queue"):
        if op == "queue":
            return views.ticket_view(conn, user, rooms.enqueue(conn, user, game, body, now), body, now)
        fn = {"solo": rooms.open_solo, "create": rooms.create_private, "join": rooms.join_private}[op]
        return views.project(conn, user, fn(conn, user, game, body, now), body, now)
    if op in ("pulse", "sync"):
        touch(conn, user, kind, row, body, now)
        rid = row.get("activity") if kind == "ticket" else row["id"]
        if rid:
            current = rooms.activity(conn, rid)
            if current:
                rooms.advance_room(conn, current, now)
        return sync(conn, user, kind, target, body, now)
    if op == "cancel":
        if row["phase"] == "assigned":
            rooms.control(conn, user, row["activity"], body)
            rooms.leave(conn, rooms.activity(conn, row["activity"]), actor, now)
        elif row["phase"] == "waiting":
            rooms.control(conn, user, target, body)
            rooms.release_ticket(conn, target, "canceled")
        return {"phase": "canceled", "ticketId": target}
    rid = row["id"]
    control = rooms.control(conn, user, rid, body)
    if control["expires"] <= now:
        fail("CONTROL_EXPIRED", 409)
    if op == "rotate":
        if row["owner"] != actor or row["mode"] != "private" or row["phase"] != "waiting":
            fail("NOT_ALLOWED", 403)
        issue_code(conn, rid, game["id"], now)
        event(conn, rid, "room.code_changed", now)
    elif op == "chat":
        if (
            row["mode"] == "solo"
            or row["phase"] not in rooms.ACTIVE
            or "room.chat" not in game["config"].get("capabilities", [])
        ):
            fail("NOT_ALLOWED", 403)
        text = body.get("text", body.get("body"))
        if not isinstance(text, str) or not text.strip() or len(text) > 300:
            fail("INVALID_MESSAGE")
        recent = conn.execute(
            "SELECT COUNT(*) AS n FROM mg_rt_messages WHERE activity=? AND actor=? AND at_ms>?",
            (rid, actor, now - 5000),
        ).fetchone()["n"]
        if recent >= 3:
            fail("RATE_LIMITED", 429)
        conn.execute("INSERT INTO mg_rt_messages VALUES(?,?,?,?,?)", (uid(), rid, actor, text.strip(), now))
        event(conn, rid, "chat.posted", now)
    elif op == "leave":
        if "privateSave" in body:
            save_private(conn, game, actor, body["privateSave"], body.get("expectedPrivateRevision"))
        rooms.leave(conn, row, actor, now)
        return {"phase": "left", "roomId": None if row["mode"] == "solo" else rid, "runId": row["run"]}
    elif op == "finish":
        if row["mode"] != "solo" or game["rules"] or row["phase"] != "running":
            fail("NOT_APPLICABLE")
        rooms.close(conn, rid, "finished", "selfCompleted", now)
    elif op == "intent":
        if row["phase"] != "running" or body.get("expectedRevision") != row["revision"]:
            fail("STALE_REVISION", 409)
        multiplayer = row["mode"] != "solo"
        participants = rooms.members(conn, rid, with_session=multiplayer)
        if multiplayer:
            actors = [p["actor"] for p in participants]
            if (rooms.revoked_room_member(conn, participants, now)
                    or not rooms.roster_is_eligible(conn, game, actors) or rooms.forbidden_pairs(conn, actors)):
                rooms.close(conn, rid, "interrupted", "eligibility_changed", now)
                return views.project(conn, user, rid, body, now, game)
            if get(conn, "SELECT actor FROM mg_rt_claims WHERE target=? AND expires<=?", (rid, now)):
                fail("RECONNECTING", 409)
        seat = next(p["seat"] for p in participants if p["actor"] == actor)
        state = json.loads(row["state_json"])
        original_rank = state["rank"]
        outcome = apply_intent(game, state, seat, row["capacity"], body.get("intent"))
        conn.execute(
            "UPDATE mg_rt_activity SET state_json=?,revision=revision+1 WHERE id=?", (compact(state), rid)
        )
        if state["rank"] > original_rank:
            for p in sorted(participants, key=lambda person: person["actor"]):
                advance_progress(conn, game, p["actor"], state["rank"], row["run"], now)
        event(conn, rid, "run.changed", now)
        if outcome:
            rooms.close(conn, rid, "finished", outcome, now)
    else:
        fail("NOT_FOUND", 404)
    return views.project(conn, user, rid, body, now, game)


def authenticated_user(conn, user):
    platform.assert_session(conn, user)
    return {**user, **platform.load_actor(conn, user["actor"]), "session_id": user["session_id"]}


def write(user, op, kind, target, body, key, source=""):
    now = platform.now_ms()
    if op not in ("pulse", "sync") and (not isinstance(key, str) or not 1 <= len(key) <= 180):
        fail("IDEMPOTENCY_REQUIRED")
    if op in ("pulse", "sync"):
        kind, target = bind_sync(body)
    actor = user["actor"]
    # Peek to identify the game fencing key. Membership and authorization are checked again after acquiring it.
    with platform.game_db() as conn:
        if kind == "account":
            game_id = ""
        elif kind == "game":
            game_id = str(target)
        else:
            table, col = {
                "room": ("activity", "id"),
                "run": ("activity", "run"),
                "ticket": ("tickets", "id"),
                "template": ("templates", "id"),
            }[kind]
            peek = get(conn, f"SELECT game FROM mg_rt_{table} WHERE {col}=?", (target,))
            if not peek and op not in ("pulse", "sync"):
                peek = get(
                    conn,
                    "SELECT game FROM mg_rt_receipts WHERE actor=? AND op=? AND k=?",
                    (actor, op + ":" + str(target), key),
                )
            if not peek:
                fail("NOT_FOUND", 404)
            game_id = peek["game"]
        steady = op in ("intent", "chat", "private", "finish")
        platform.db_start(conn, game_id, shared=steady)
        # Domain-level coordination is kept separate from the platform's publication fence.
        if game_id:
            lock(conn, "game:" + game_id, shared=steady)
        if steady:
            if kind == "room":
                rid = target
            else:
                run = body.get("runId") if kind == "game" else target
                found = get(conn, "SELECT id FROM mg_rt_activity WHERE run=?", (run,))
                rid = found["id"] if found else str(run)
            lock(conn, "room:" + rid)
        lock(conn, "actor:" + actor)
        user = authenticated_user(conn, user)
        request_digest = hashlib.sha256(compact(body).encode()).hexdigest()
        opkey = op + ":" + str(target)
        receipt = (
            get(
                conn,
                "SELECT * FROM mg_rt_receipts WHERE game=? AND actor=? AND op=? AND k=?",
                (game_id, actor, opkey, key),
            )
            if key and op not in ("pulse", "sync")
            else None
        )
        if receipt:
            if game_id:
                platform.require_play(conn, user, game_id, definition=False)
            if receipt["digest"] != request_digest:
                fail("IDEMPOTENCY_MISMATCH", 409)
            response = json.loads(cipher().decrypt(receipt["result"].encode()).decode())
            if "members" in response:
                rid = response.get("roomId") or response.get("runId")
                if rid:
                    fresh = views.project(conn, user, rid, body, now)
                    allowed = {m["playerId"] for m in fresh["members"]}
                    response["members"] = [m for m in response["members"] if m["playerId"] in allowed]
                    response["readOnly"] = fresh["readOnly"]
                    response["controlEpoch"] = fresh["controlEpoch"]
            if isinstance(response, dict) and response.get("code"):
                current = rooms.activity(conn, response.get("roomId"))
                if (
                    not current
                    or current["phase"] != "waiting"
                    or current["code_until"] <= now
                    or not current["code_cipher"]
                    or not get(
                        conn,
                        "SELECT actor FROM mg_rt_claims WHERE target=? AND actor=?",
                        (current["id"], actor),
                    )
                ):
                    response.pop("code", None)
                else:
                    response["code"] = cipher().decrypt(current["code_cipher"].encode()).decode()
            conn.commit()
            return response
        _, row = resolve(conn, kind, target, user)
        game = game_for(conn, user, game_id, row, kind)
        if op == "join":
            check_attempts(conn, actor, source, now)
        try:
            response = apply_command(conn, user, game, op, kind, target, row, body, now)
        except HTTPException as exc:
            if op == "join" and exc.detail == {"code": "ROOM_UNAVAILABLE"}:
                conn.rollback()
                platform.db_start(conn, game_id)
                record_failure(conn, actor, source, now)
                conn.commit()
            raise
        if key and op not in ("pulse", "sync"):
            if game_id and op in ("private", "intent", "chat", "takeover", "rotate"):
                conn.execute(
                    "UPDATE mg_rt_claims SET interacted=? WHERE actor=? AND game=?", (now, actor, game_id)
                )
            ref = response.get("roomId") or response.get("ticketId") or response.get("runId") or ""
            if row and kind in ("room", "run", "ticket"):
                ref = row["id"]
            if op == "private":
                ref = rid
            conn.execute(
                "INSERT INTO mg_rt_receipts(actor,game,op,k,digest,result,at_ms,scope_ref) VALUES(?,?,?,?,?,?,?,?)",
                (
                    actor,
                    game_id,
                    opkey,
                    key,
                    request_digest,
                    cipher().encrypt(compact(response).encode()).decode(),
                    now,
                    ref,
                ),
            )
        conn.commit()
        if op == "create" or (op in ("join", "queue") and response.get("phase") == "countdown"):
            wake_scheduler()
        return response


def check_attempts(conn, actor, source, now):
    for key, cap in ((keyed("actor:" + actor), 6), (keyed("ip:" + source), 600)):
        r = get(conn, "SELECT * FROM mg_rt_attempts WHERE k=?", (key,))
        if r and r["window_ms"] + 60000 > now and r["n"] >= cap:
            fail("RATE_LIMITED", 429)


def record_failure(conn, actor, source, now):
    for k in (keyed("actor:" + actor), keyed("ip:" + source)):
        conn.execute(
            """INSERT INTO mg_rt_attempts(k,window_ms,n) VALUES(?,?,1) ON CONFLICT(k) DO UPDATE SET
            n=CASE WHEN mg_rt_attempts.window_ms<=? THEN 1 ELSE mg_rt_attempts.n+1 END,
            window_ms=CASE WHEN mg_rt_attempts.window_ms<=? THEN excluded.window_ms ELSE mg_rt_attempts.window_ms END""",
            (k, now, now - 60000, now - 60000),
        )


def read(user, op, target, body):
    with platform.game_db() as conn:
        user = authenticated_user(conn, user)
        now = platform.now_ms()
        if op == "sync":
            kind, tid = bind_sync(body)
            return sync(conn, user, kind, tid, body, now)
        if op == "prefs":
            return prefs(conn, user["actor"])
        if op == "history":
            return views.history(conn, user, body)
        if op == "teams":
            return views.teams(conn, user, body)
        if op == "activity":
            return views.read_activity(conn, user, body, now)
        if op == "progress":
            if body.get("runId"):
                _, room = resolve(conn, "run", body["runId"], user)
                if str(target) != room["game"]:
                    fail("NOT_FOUND", 404)
                game = views.bound_game(conn, user, room, membership_checked=True)
            else:
                game = platform.require_play(conn, user, target)
            return progress(conn, game, user["actor"])
        fail("NOT_FOUND", 404)


def poll(user, body, controlled):
    kind, target = bind_sync(body)
    now = platform.now_ms()
    needs_write = False
    with platform.game_db(snapshot=True) as conn:
        user = authenticated_user(conn, user)
        _, row = resolve(conn, kind, target, user)
        claim_rows = {}
        response = sync(conn, user, kind, target, body, now, row=row, claims=claim_rows)
        if controlled and response["snapshot"]["phase"] in rooms.ACTIVE:
            rid = row["activity"] if kind == "ticket" and row.get("phase") == "assigned" else row["id"]
            c = claim_rows.get(user["actor"])
            if c:
                rooms.check_control(c, user, body)
            else:
                c = rooms.control(conn, user, rid, body)
            needs_write = (
                c["expires"] <= now
                or (response["snapshot"]["phase"] == "countdown" and response["snapshot"]["startsAt"] <= now)
                or response["snapshot"]["phase"] == "reconnecting"
            )
            if not needs_write:
                touch(conn, user, kind, row, body, now, c)
    if needs_write:
        return write(user, "sync", kind, target, body, None)
    return response


_mail = {}
_mail_lock = threading.Lock()
_task = None
_wake = None
_loop = None


def wake_scheduler():
    loop, signal = _loop, _wake
    if loop is not None and signal is not None and loop.is_running():
        try:
            loop.call_soon_threadsafe(signal.set)
        except RuntimeError:
            # A worker may be shutting down between its commit and this optional hint.
            pass


def stream_watermarks(streams):
    """Internal wakeup hints only. Never return these values directly to a caller."""
    watermarks = {}
    with platform.game_db() as conn:
        for kind, table in (("activity", "mg_rt_activity"), ("ticket", "mg_rt_tickets")):
            ids = [target for source, target in streams if source == kind]
            for index in range(0, len(ids), 128):
                chunk = ids[index : index + 128]
                marks = ",".join("?" for _ in chunk)
                for r in rows(conn, f"SELECT id,seq FROM {table} WHERE id IN ({marks})", chunk):
                    watermarks[(kind, r["id"])] = r["seq"]
    return watermarks


def prune_expired(conn, now):
    day = 86400000
    conn.execute(
        """DELETE FROM mg_rt_receipts WHERE (actor,game,op,k) IN
        (SELECT r.actor,r.game,r.op,r.k FROM mg_rt_receipts r LEFT JOIN mg_rt_completed_scopes s
         ON s.ref=r.scope_ref WHERE r.at_ms<=? AND (r.scope_ref='' OR s.at_ms<=?) LIMIT 512)""",
        (now - day, now - day),
    )
    conn.execute(
        """DELETE FROM mg_rt_completed_scopes WHERE ref IN (SELECT s.ref FROM mg_rt_completed_scopes s
        WHERE s.at_ms<=? AND NOT EXISTS(SELECT 1 FROM mg_rt_receipts r WHERE r.scope_ref=s.ref) LIMIT 256)""",
        (now - day,),
    )
    conn.execute(
        "DELETE FROM mg_rt_messages WHERE id IN (SELECT id FROM mg_rt_messages WHERE at_ms<=? LIMIT 512)",
        (now - 7 * day,),
    )
    conn.execute(
        "DELETE FROM mg_rt_attempts WHERE k IN (SELECT k FROM mg_rt_attempts WHERE window_ms<=? LIMIT 256)",
        (now - 60000,),
    )
    conn.execute(
        "DELETE FROM mg_rt_codes WHERE digest IN (SELECT digest FROM mg_rt_codes WHERE expires<=? LIMIT 256)",
        (now,),
    )


def maintenance(now, flush=False, prune=False):
    if flush:
        with _mail_lock:
            batch = dict(_mail)
        if batch or prune:
            with platform.game_db() as conn:
                platform.db_start(conn, "")
                if batch:
                    flush_batch(conn, batch, now)
                if prune:
                    prune_expired(conn, now)
                conn.commit()
        if batch:
            with _mail_lock:
                for identity, observed in batch.items():
                    if _mail.get(identity) == observed:
                        _mail.pop(identity, None)
    with platform.game_db() as conn:
        due = rows(
            conn,
            """SELECT DISTINCT a.game FROM mg_rt_activity a LEFT JOIN mg_rt_claims c ON c.target=a.id
            WHERE (a.phase='countdown' AND a.starts<=?) OR (a.phase='waiting' AND a.code_until<=?)
            OR (a.phase IN ('waiting','countdown','running') AND c.expires<=?)
            OR (a.phase='reconnecting' AND c.expires<=?) LIMIT 24""",
            (now, now, now, now - rooms.GRACE_MS),
        )
        if flush:
            due += rows(conn, "SELECT DISTINCT game FROM mg_rt_tickets WHERE phase='waiting' LIMIT 24")
        deadline = get(
            conn,
            """SELECT MIN(at_ms) AS next_at FROM (
            SELECT MIN(starts) AS at_ms FROM mg_rt_activity WHERE phase='countdown' AND starts>?
            UNION ALL SELECT MIN(code_until) FROM mg_rt_activity WHERE phase='waiting' AND code_until>?
            UNION ALL SELECT MIN(expires) FROM mg_rt_claims WHERE expires>?
            UNION ALL SELECT MIN(expires)+? FROM mg_rt_claims WHERE expires>? AND expires<=?
            ) deadlines""",
            (now, now, now, rooms.GRACE_MS, now - rooms.GRACE_MS, now),
        )["next_at"]
    for game_id in sorted({r["game"] for r in due}):
        with platform.game_db() as conn:
            platform.db_start(conn, game_id)
            lock(conn, "game:" + game_id)
            for room in rows(
                conn,
                """SELECT DISTINCT a.* FROM mg_rt_activity a LEFT JOIN mg_rt_claims c ON c.target=a.id
                WHERE a.game=? AND ((a.phase='countdown' AND a.starts<=?) OR (a.phase='waiting' AND a.code_until<=?)
                OR (a.phase IN ('waiting','countdown','running') AND c.expires<=?)
                OR (a.phase='reconnecting' AND c.expires<=?)) LIMIT 64""",
                (game_id, now, now, now, now - rooms.GRACE_MS),
            ):
                rooms.advance_room(conn, room, now)
            if flush:
                expired = rows(
                    conn,
                    """SELECT t.id FROM mg_rt_tickets t JOIN mg_rt_claims c ON c.target=t.id
                    WHERE t.game=? AND t.phase='waiting' AND c.expires<=? LIMIT 128""",
                    (game_id, now),
                )
                for t in expired:
                    rooms.release_ticket(conn, t["id"], "expired")
                buckets = rows(
                    conn,
                    """SELECT version,capacity FROM mg_rt_tickets
                    WHERE game=? AND phase='waiting' GROUP BY version,capacity LIMIT 12""",
                    (game_id,),
                )
                for b in buckets:
                    rooms.match(conn, game_id, b["version"], b["capacity"], now)
            conn.commit()
    # New countdowns have five seconds of lead time. Every worker observes the
    # persistent clock at least once in four seconds, even if the creator dies.
    return min(now + (50 if due else 4000), deadline or now + 4000)


async def _scheduler():
    import logging

    log = logging.getLogger(__name__)
    last = 0
    last_prune = 0
    delay = 0
    while True:
        if delay > 0:
            try:
                await asyncio.wait_for(_wake.wait(), delay)
            except asyncio.TimeoutError:
                pass
        _wake.clear()
        now = platform.now_ms()
        flush = now - last >= 15000
        prune = flush and now - last_prune >= 60000
        try:
            next_at = await platform.run_db(maintenance, now, flush, prune)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("小游戏到期处理失败")
            delay = 2
        else:
            if flush:
                last = now
            if prune:
                last_prune = now
            delay = max(0.01, (min(next_at, last + 15000) - platform.now_ms()) / 1000)


def start_runtime():
    global _task, _wake, _loop
    if not _task or _task.done():
        _loop = asyncio.get_running_loop()
        _wake = asyncio.Event()
        _task = asyncio.create_task(_scheduler(), name="minigame-runtime")


async def stop_runtime():
    global _task, _wake, _loop
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    _wake = None
    _loop = None
    with _mail_lock:
        _mail.clear()
