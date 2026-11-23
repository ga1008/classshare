"""Short-lived, session-bound grants for immutable game content."""

import hashlib
import base64
import hmac
import json
import re
import secrets

from fastapi import HTTPException
from ..config import SECRET_KEY
from ..db.sessions import get_user_session
from . import minigame_platform as p

LIFETIME = 90_000
INSTANCE = re.compile(r"[a-zA-Z0-9_-]{8,128}\Z")
SIGN_FIELDS = (
    "handle_hash",
    "lease_id",
    "actor",
    "sid_hash",
    "game_id",
    "version_id",
    "purpose",
    "policy_revision",
    "host_instance",
    "expires_at",
    "run_id",
)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def sign(row):
    material = json.dumps(
        [row[k] for k in SIGN_FIELDS], separators=(",", ":"), ensure_ascii=True
    )
    return hmac.new(
        str(SECRET_KEY).encode(),
        ("minigames.content.v1:" + material).encode(),
        "sha256",
    ).hexdigest()


def version_for(conn, user, game_id, vid, purpose, *, definition=True):
    if purpose == "play":
        return p.require_play(conn, user, game_id, vid, definition=definition)
    if purpose != "preview":
        raise HTTPException(422, "未知启动用途")
    game = p.require_manager(conn, user, game_id)
    fields = "config_json,rules_json,writeback_json" if definition else "id"
    ver = conn.execute(
        f"SELECT {fields} FROM mg_versions WHERE game_id=? AND id=? AND status='ready'",
        (game_id, vid),
    ).fetchone()
    if not ver or game["status"] in ("deleting", "deleted"):
        raise HTTPException(404, "当前版本无法预览")
    return {
        **game,
        "version_id": vid,
        **(p.version_definition(ver) if definition else {}),
    }


def handle_for(lease_id):
    opaque = hmac.new(
        str(SECRET_KEY).encode(), ("minigames.handle.v1:" + lease_id).encode(), "sha256"
    ).digest()
    return base64.urlsafe_b64encode(opaque).decode().rstrip("=")


def launch_response(row, game, user, asset):
    return {
        "leaseId": row["lease_id"],
        "versionId": row["version_id"],
        "csrfToken": p.csrf_token(user),
        "expiresAt": row["expires_at"],
        "contentEntryUrl": f"{asset}/l/{handle_for(row['lease_id'])}/main.html",
        "definition": {k: game[k] for k in ("config", "writeback", "rules")},
    }


def check_run(conn, actor, game, version, run_id):
    if (
        run_id
        and not conn.execute(
            """SELECT 1 FROM mg_rt_activity a JOIN mg_rt_seats s ON s.activity=a.id
            JOIN mg_rt_claims c ON c.target=a.id AND c.actor=s.actor WHERE s.actor=? AND a.game=?
            AND a.version=? AND a.run=? AND a.phase IN ('running','reconnecting') AND c.expires>? LIMIT 1""",
            (actor, game, version, run_id, p.now_ms()),
        ).fetchone()
    ):
        raise HTTPException(404, "本局资源已失效")


def launch(user, game_id, body, key):
    _, asset = p.content_origins()
    instance = body.get("hostInstance")
    if not isinstance(instance, str) or not INSTANCE.fullmatch(instance):
        raise HTTPException(422, "游戏窗口标识无效")
    purpose = body.get("purpose", "play")
    run_id = body.get("runId")
    if (
        set(body) - {"hostInstance", "purpose", "versionId", "runId"}
        or purpose not in ("play", "preview")
        or any(
            v is not None and (not isinstance(v, str) or not 1 <= len(v) <= 128)
            for v in (run_id, body.get("versionId"))
        )
        or (purpose == "preview" and run_id is not None)
    ):
        raise HTTPException(422, "启动参数无效")
    if not key or len(key) > 128 or not key.isascii():
        raise HTTPException(422, "需要有效的操作编号")
    command_key = digest(user["session_id"] + "|" + key)
    command_digest = digest(
        json.dumps(
            [game_id, instance, purpose, body.get("versionId"), run_id],
            separators=(",", ":"),
        )
    )
    with p.game_db() as conn:
        p.db_start(conn, game_id, shared=True)
        p.assert_session(conn, user)
        game = version_for(conn, user, game_id, body.get("versionId"), purpose)
        check_run(conn, user["actor"], game_id, game["version_id"], run_id)
        now = p.now_ms()
        from .social_common import serialize_on

        serialize_on(conn, "mg-launch-actor", [user["actor"]])
        conn.execute("DELETE FROM mg_launch_leases WHERE expires_at<=?", (now,))
        previous = conn.execute(
            "SELECT * FROM mg_launch_leases WHERE actor=? AND command_key=?",
            (user["actor"], command_key),
        ).fetchone()
        if previous:
            if (
                previous["command_digest"] != command_digest
                or previous["version_id"] != game["version_id"]
                or previous["revoked"]
            ):
                raise HTTPException(409, "启动操作已变更，请重新打开游戏")
            check_grant(conn, previous)
            return launch_response(previous, game, user, asset)
        # A new page generation invalidates any older handle for that exact same instance.
        conn.execute(
            "UPDATE mg_launch_leases SET revoked=1 WHERE actor=? AND host_instance=? AND game_id=?",
            (user["actor"], instance, game_id),
        )
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM mg_launch_leases WHERE actor=? AND revoked=0",
            (user["actor"],),
        ).fetchone()["n"]
        if count >= 24:
            raise HTTPException(429, "已打开太多游戏窗口，请稍后重试")
        lease_id = secrets.token_hex(24)
        row = dict(
            handle_hash=digest(handle_for(lease_id)),
            lease_id=lease_id,
            actor=user["actor"],
            sid_hash=digest(user["session_id"]),
            game_id=game_id,
            version_id=game["version_id"],
            purpose=purpose,
            policy_revision=game["policy_revision"],
            host_instance=instance,
            expires_at=now + LIFETIME,
            run_id=run_id,
        )
        fields = (*SIGN_FIELDS, "signature", "command_key", "command_digest")
        conn.execute(
            f"INSERT INTO mg_launch_leases({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
            (*[row[k] for k in SIGN_FIELDS], sign(row), command_key, command_digest),
        )
        return launch_response(row, game, user, asset)


def check_grant(conn, record, *, retried=False):
    if (
        not record
        or record["revoked"]
        or record["expires_at"] <= p.now_ms()
        or not hmac.compare_digest(sign(record), record["signature"])
    ):
        raise HTTPException(404, "资源不可用")
    user = p.load_actor(conn, record["actor"])
    session = get_user_session(record["actor"], conn=conn)
    if not session or not hmac.compare_digest(
        digest(session["session_id"]), record["sid_hash"]
    ):
        raise HTTPException(404, "资源不可用")
    game = version_for(
        conn,
        user,
        record["game_id"],
        record["version_id"],
        record["purpose"],
        definition=False,
    )
    check_run(
        conn, user["actor"], record["game_id"], record["version_id"], record["run_id"]
    )
    if game["policy_revision"] != record["policy_revision"]:
        # A range edit can retain access for part of a class. Re-sign the same
        # prefix only after the current audience check. Hard withdrawals revoke rows.
        refreshed = dict(record)
        refreshed["policy_revision"] = game["policy_revision"]
        refreshed["signature"] = sign(refreshed)
        changed = conn.execute(
            """UPDATE mg_launch_leases SET policy_revision=?,signature=?
               WHERE lease_id=? AND signature=? AND revoked=0 AND expires_at>?
               AND EXISTS (SELECT 1 FROM mg_games g WHERE g.id=mg_launch_leases.game_id AND g.policy_revision=?)""",
            (
                game["policy_revision"],
                refreshed["signature"],
                record["lease_id"],
                record["signature"],
                p.now_ms(),
                game["policy_revision"],
            ),
        )
        if changed.rowcount:
            return refreshed
        if retried:
            raise HTTPException(
                503, "游戏访问范围正在更新", headers={"Retry-After": "1"}
            )
        current = conn.execute(
            "SELECT * FROM mg_launch_leases WHERE lease_id=?", (record["lease_id"],)
        ).fetchone()
        return check_grant(conn, current, retried=True)
    return record


def change_grant(user, lease_id, instance, revoke):
    with p.game_db() as conn:
        p.db_start(conn)
        lock = " FOR UPDATE" if p.get_configured_db_engine() == "postgres" else ""
        row = conn.execute(
            "SELECT * FROM mg_launch_leases WHERE lease_id=? AND actor=? AND host_instance=?"
            + lock,
            (lease_id, user["actor"], instance),
        ).fetchone()
        if not row or row["sid_hash"] != digest(user["session_id"]):
            raise HTTPException(404, "窗口已失效")
        if revoke:
            conn.execute(
                "UPDATE mg_launch_leases SET revoked=1 WHERE lease_id=?", (lease_id,)
            )
            return {"revoked": True}
        grant = dict(check_grant(conn, row))
        grant["expires_at"] = p.now_ms() + LIFETIME
        changed = conn.execute(
            "UPDATE mg_launch_leases SET expires_at=?, signature=? WHERE lease_id=? AND revoked=0 AND expires_at>=? AND policy_revision=?",
            (
                grant["expires_at"],
                sign(grant),
                lease_id,
                p.now_ms(),
                grant["policy_revision"],
            ),
        )
        if not changed.rowcount:
            raise HTTPException(404, "窗口已失效")
        return {
            "leaseId": lease_id,
            "versionId": row["version_id"],
            "expiresAt": grant["expires_at"],
        }


def resolve_asset(handle, path):
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]{43}", handle)
        or len(path) > 512
        or any(segment in ("", ".", "..") for segment in path.split("/"))
        or "\\" in path
    ):
        raise HTTPException(404, "资源不可用")
    with p.game_db() as conn:
        row = conn.execute(
            "SELECT * FROM mg_launch_leases WHERE handle_hash=?", (digest(handle),)
        ).fetchone()
        check_grant(conn, row)
        meta = conn.execute(
            "SELECT mime FROM mg_version_files WHERE version_id=? AND path=?",
            (row["version_id"], path),
        ).fetchone()
        if not meta or path.startswith("contracts/") or path == "config.json":
            raise HTTPException(404, "资源不可用")
        root = (
            p.game_root() / "versions" / row["game_id"] / row["version_id"] / "runtime"
        ).resolve()
        actual = (root / path).resolve()
        if not actual.is_relative_to(root) or not actual.is_file():
            raise HTTPException(404, "资源不可用")
        preview = (
            (row["game_id"], row["version_id"], row["actor"], row["lease_id"])
            if path == "main.html" and row["purpose"] == "preview"
            else None
        )
        return actual, meta["mime"], row["game_id"], preview


def mark_preview_loaded(preview):
    from .minigame_packages import mark_preview_loaded as mark

    with p.game_db() as conn:
        p.db_start(conn, preview[0], shared=True)
        mark(conn, *preview)
