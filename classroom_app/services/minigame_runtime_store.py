"""Portable persistent state for games. Schema setup belongs to the explicit migration."""

from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import secrets
import uuid

from cryptography.fernet import Fernet

from .. import config
from ..db.connection import get_configured_db_engine
from .minigame_runtime_rules import compact, fail, validate_private


def ensure_runtime_schema(conn):
    for definition in TABLES:
        conn.execute("CREATE TABLE IF NOT EXISTS mg_rt_" + definition)
    from ..db.schema_social import _add_column_if_missing

    engine = get_configured_db_engine()
    _add_column_if_missing(conn, engine, "mg_rt_claims", "interacted", "BIGINT NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, engine, "mg_rt_activity", "chat_enabled", "INTEGER")
    _add_column_if_missing(conn, engine, "mg_rt_receipts", "scope_ref", "TEXT NOT NULL DEFAULT ''")
    for suffix, columns in INDEXES:
        name = suffix + "_" + columns.replace(",", "_").replace(" ", "")
        conn.execute(f"CREATE INDEX IF NOT EXISTS mg_ri_{name} ON mg_rt_{suffix} ({columns})")


TABLES = (
    """activity (id TEXT PRIMARY KEY,game TEXT NOT NULL,version TEXT NOT NULL,mode TEXT NOT NULL,
        phase TEXT NOT NULL,owner TEXT NOT NULL,capacity INTEGER NOT NULL,requested INTEGER,planned INTEGER,
        gen INTEGER NOT NULL DEFAULT 0, starts BIGINT,run TEXT UNIQUE,revision INTEGER NOT NULL DEFAULT 0,
        seq INTEGER NOT NULL DEFAULT 0,state_json TEXT NOT NULL DEFAULT '{}',outcome TEXT,created BIGINT NOT NULL,
        started BIGINT,ended BIGINT,code_lookup TEXT,code_cipher TEXT,code_until BIGINT,team_name TEXT NOT NULL DEFAULT '')""",
    """seats (activity TEXT NOT NULL REFERENCES mg_rt_activity(id) ON DELETE CASCADE,actor TEXT NOT NULL,
        seat INTEGER NOT NULL,alias TEXT NOT NULL,name TEXT NOT NULL,joined BIGINT NOT NULL,
        PRIMARY KEY(activity,actor),UNIQUE(activity,seat))""",
    """claims (actor TEXT NOT NULL,slot TEXT NOT NULL,game TEXT NOT NULL,target TEXT NOT NULL,host TEXT NOT NULL,
        epoch BIGINT NOT NULL,session_key TEXT NOT NULL,expires BIGINT NOT NULL,touched BIGINT NOT NULL,
        interacted BIGINT NOT NULL,PRIMARY KEY(actor,slot))""",
    """tickets (id TEXT PRIMARY KEY,game TEXT NOT NULL,version TEXT NOT NULL,capacity INTEGER NOT NULL,
        actor TEXT NOT NULL,phase TEXT NOT NULL,queued BIGINT NOT NULL,activity TEXT,seq INTEGER NOT NULL DEFAULT 1)""",
    """bucket (game TEXT NOT NULL,version TEXT NOT NULL,capacity INTEGER NOT NULL,anchor TEXT NOT NULL,
        PRIMARY KEY(game,version,capacity))""",
    """codes (digest TEXT PRIMARY KEY,game TEXT NOT NULL,activity TEXT,expires BIGINT NOT NULL)""",
    """progress (game TEXT NOT NULL,series TEXT NOT NULL,save_ver INTEGER NOT NULL,actor TEXT NOT NULL,
        rank INTEGER NOT NULL DEFAULT 0, shared_rev INTEGER NOT NULL DEFAULT 0,private_rev INTEGER NOT NULL DEFAULT 0,
        priv TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(game,series,save_ver,actor))""",
    """changes (id TEXT PRIMARY KEY,game TEXT NOT NULL,run TEXT NOT NULL,actor TEXT NOT NULL,
        before_rank INTEGER NOT NULL,after_rank INTEGER NOT NULL,revision INTEGER NOT NULL,at_ms BIGINT NOT NULL,
        UNIQUE(run,actor,revision))""",
    """events (activity TEXT NOT NULL REFERENCES mg_rt_activity(id) ON DELETE CASCADE,seq INTEGER NOT NULL,
        kind TEXT NOT NULL,revision INTEGER NOT NULL,generation INTEGER NOT NULL,at_ms BIGINT NOT NULL,PRIMARY KEY(activity,seq))""",
    """messages (id TEXT PRIMARY KEY,activity TEXT NOT NULL REFERENCES mg_rt_activity(id) ON DELETE CASCADE,
        actor TEXT NOT NULL,body TEXT NOT NULL,at_ms BIGINT NOT NULL)""",
    """templates (id TEXT PRIMARY KEY,actor TEXT NOT NULL,game TEXT NOT NULL,fingerprint TEXT NOT NULL,
        activity TEXT NOT NULL,last_used BIGINT NOT NULL,UNIQUE(actor,game,fingerprint))""",
    """prefs (actor TEXT PRIMARY KEY,display_alias TEXT NOT NULL DEFAULT '',sharing INTEGER NOT NULL DEFAULT 0,
        muted INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 0)""",
    """receipts (actor TEXT NOT NULL,game TEXT NOT NULL,op TEXT NOT NULL,k TEXT NOT NULL,digest TEXT NOT NULL,
        result TEXT NOT NULL,at_ms BIGINT NOT NULL,scope_ref TEXT NOT NULL DEFAULT '',PRIMARY KEY(actor,game,op,k))""",
    """completed_scopes (ref TEXT PRIMARY KEY,game TEXT NOT NULL,at_ms BIGINT NOT NULL)""",
    """attempts (k TEXT PRIMARY KEY,window_ms BIGINT NOT NULL,n INTEGER NOT NULL)""",
)
INDEXES = (
    ("activity", "game,phase"),
    ("activity", "phase,starts"),
    ("activity", "phase,code_until"),
    ("activity", "game,started"),
    ("seats", "actor,activity"),
    ("claims", "target"),
    ("claims", "expires"),
    ("tickets", "game,version,capacity,phase,queued,id"),
    ("tickets", "activity,actor"),
    ("codes", "expires"),
    ("messages", "activity,at_ms"),
    ("templates", "actor,last_used"),
    ("receipts", "at_ms"),
    ("receipts", "scope_ref"),
    ("completed_scopes", "at_ms"),
    ("messages", "at_ms"),
)


def uid():
    return uuid.uuid4().hex


def lock(conn, key, *, shared=False):
    if get_configured_db_engine() == "postgres":
        fn = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
        conn.execute(
            f"SELECT {fn}(?)",
            (
                int.from_bytes(
                    hashlib.sha256(("mg-runtime:" + key).encode()).digest()[:8], "big", signed=True
                ),
            ),
        )


def get(conn, sql, args=()):
    row = conn.execute(sql, tuple(args)).fetchone()
    return dict(row) if row is not None else None


def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def keyed(text):
    return hmac.new(
        str(config.SECRET_KEY).encode(), ("minigame-v1:" + text).encode(), hashlib.sha256
    ).hexdigest()


def active_session_keys(conn, actors, now, *, protect=False):
    # Lock the verified sessions through shared-progress commit. A concurrent
    # logout or replacement then has a consistent order relative to the move.
    cutoff = datetime.fromtimestamp(now / 1000, timezone.utc).isoformat()
    marks = ",".join("?" for _ in actors)
    lock_rows = " FOR SHARE" if protect and get_configured_db_engine() == "postgres" else ""
    return {
        row["session_user_key"]: keyed(row["session_id"])
        for row in rows(conn, f"""SELECT session_user_key,session_id FROM user_sessions
            WHERE session_user_key IN ({marks}) AND (expires_at='' OR expires_at>?)
            ORDER BY session_user_key{lock_rows}""", (*actors, cutoff))
        if row["session_id"]
    }


def cipher():
    return Fernet(base64.urlsafe_b64encode(bytes.fromhex(keyed("room-code-encryption"))))


def prefs(conn, actor):
    row = get(conn, "SELECT * FROM mg_rt_prefs WHERE actor=?", (actor,)) or {}
    return {
        "displayAlias": row.get("display_alias", ""),
        "sharePlaying": bool(row.get("sharing")),
        "audioMuted": bool(row.get("muted")),
        "revision": row.get("revision", 0),
    }


def update_prefs(conn, actor, b):
    prev = prefs(conn, actor)
    if b.get("expectedRevision") != prev["revision"]:
        fail("STALE_REVISION", 409)
    alias = b.get("displayAlias", prev["displayAlias"])
    share, mute = b.get("sharePlaying", prev["sharePlaying"]), b.get("audioMuted", prev["audioMuted"])
    if (
        not isinstance(alias, str)
        or len(alias) > 32
        or not isinstance(share, bool)
        or not isinstance(mute, bool)
    ):
        fail("INVALID_PREFERENCES")
    conn.execute(
        """INSERT INTO mg_rt_prefs(actor,display_alias,sharing,muted,revision) VALUES(?,?,?,?,?)
        ON CONFLICT(actor) DO UPDATE SET display_alias=excluded.display_alias,sharing=excluded.sharing,
        muted=excluded.muted,revision=excluded.revision""",
        (actor, alias.strip(), int(share), int(mute), prev["revision"] + 1),
    )
    return prefs(conn, actor)


def pkey(game, actor):
    w = game["writeback"]
    return (str(game["id"]), w["series"], w["saveVersion"], actor)


def progress(conn, game, actor):
    w = game["writeback"]
    row = (
        get(
            conn,
            "SELECT * FROM mg_rt_progress WHERE game=? AND series=? AND save_ver=? AND actor=?",
            pkey(game, actor),
        )
        or {}
    )
    raw = json.loads(row.get("priv", "{}"))
    rank = row.get("rank", 0)
    return {
        "series": w["series"],
        "saveVersion": w["saveVersion"],
        "rank": rank,
        "milestoneId": w["milestones"][rank]["id"] if rank < len(w["milestones"]) else None,
        "privateSave": {k: raw.get(k, sp["default"]) for k, sp in w["privateFields"].items()},
        "privateRevision": row.get("private_rev", 0),
        "sharedRevision": row.get("shared_rev", 0),
    }


def save_private(conn, game, actor, values, expected):
    if "save.private" not in game["config"].get("capabilities", []):
        fail("CAPABILITY_DENIED", 403)
    validate_private(values, game["writeback"]["privateFields"])
    ids = pkey(game, actor)
    conn.execute(
        "INSERT INTO mg_rt_progress(game,series,save_ver,actor) VALUES(?,?,?,?) ON CONFLICT DO NOTHING", ids
    )
    row = get(
        conn,
        "SELECT priv,private_rev FROM mg_rt_progress WHERE game=? AND series=? AND save_ver=? AND actor=?",
        ids,
    )
    if type(expected) is not int or row["private_rev"] != expected:
        fail("STALE_REVISION", 409)
    stored = json.loads(row["priv"])
    stored.update(values)
    if len(compact(stored).encode()) > 32768:
        fail("SAVE_TOO_LARGE", 413)
    if (
        conn.execute(
            """UPDATE mg_rt_progress SET priv=?,private_rev=private_rev+1 WHERE game=? AND series=?
        AND save_ver=? AND actor=? AND private_rev=?""",
            (compact(stored), *ids, expected),
        ).rowcount
        != 1
    ):
        fail("STALE_REVISION", 409)
    return progress(conn, game, actor)


def advance_progress(conn, game, actor, rank, run, now):
    ids = pkey(game, actor)
    conn.execute(
        "INSERT INTO mg_rt_progress(game,series,save_ver,actor) VALUES(?,?,?,?) ON CONFLICT DO NOTHING", ids
    )
    # Decide from the latest locked row and retain that lock through its audit append.
    # SQLite already serializes writers at the preceding INSERT.
    lock_row = " FOR UPDATE" if get_configured_db_engine() == "postgres" else ""
    row = get(
        conn,
        "SELECT rank,shared_rev FROM mg_rt_progress WHERE game=? AND series=? AND save_ver=? AND actor=?"
        + lock_row,
        ids,
    )
    if row["rank"] >= rank:
        return
    conn.execute(
        """UPDATE mg_rt_progress SET rank=?,shared_rev=shared_rev+1
        WHERE game=? AND series=? AND save_ver=? AND actor=?""",
        (rank, *ids),
    )
    conn.execute(
        "INSERT INTO mg_rt_changes VALUES(?,?,?,?,?,?,?,?)",
        (uid(), ids[0], run, actor, row["rank"], rank, row["shared_rev"] + 1, now),
    )


def issue_code(conn, activity, game, now):
    letters = "ABCDEFGHJKMNPQRSTUVWXYZ"
    for _ in range(40):
        code = "".join(secrets.choice(letters + "23456789") for _ in range(4))
        if code.isalpha() or code.isdigit():
            continue
        digest = keyed("code:" + code)
        until = now + 900_000
        conn.execute("DELETE FROM mg_rt_codes WHERE digest=? AND expires<=?", (digest, now))
        if not conn.execute(
            "INSERT INTO mg_rt_codes VALUES(?,?,?,?) ON CONFLICT DO NOTHING", (digest, game, activity, until)
        ).rowcount:
            continue
        conn.execute(
            "UPDATE mg_rt_codes SET activity=NULL WHERE activity=? AND digest<>?", (activity, digest)
        )
        conn.execute(
            "UPDATE mg_rt_activity SET code_lookup=?,code_cipher=?,code_until=? WHERE id=?",
            (digest, cipher().encrypt(code.encode()).decode(), until, activity),
        )
        return code
    fail("CODE_UNAVAILABLE", 503)


def event(conn, rid, kind, now):
    row = get(conn, "UPDATE mg_rt_activity SET seq=seq+1 WHERE id=? RETURNING seq,revision,gen", (rid,))
    conn.execute(
        "INSERT INTO mg_rt_events(activity,seq,kind,revision,generation,at_ms) VALUES(?,?,?,?,?,?)",
        (rid, row["seq"], kind, row["revision"], row["gen"], now),
    )
    if row["seq"] % 128 == 0:
        conn.execute("DELETE FROM mg_rt_events WHERE activity=? AND seq<=?", (rid, row["seq"] - 256))
