"""Shared authentication, audience and database boundary for the HTML game platform."""

import asyncio
import contextvars
import functools
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from .. import dependencies
from ..db.connection import get_configured_db_engine, get_db_connection
from ..db.errors import (
    DatabaseBusyError,
    DatabaseConnectionError,
    DatabaseRetryableError,
    is_retryable_conflict,
)
from ..db.sessions import get_user_session
from ..storage_paths import read_data_root
from .account_control_service import student_is_ai_controlled
from .minigame_file_lock import asset_usage as asset_usage
from .offering_membership_service import student_offering_where
from .organization_scope_service import load_teacher_org_memberships, org_scope_from_row
from .resource_access_service import (
    is_super_admin_teacher,
)
from .social_common import env_int, serialize_on
from .social_scope_service import student_global_tier


def now_ms():
    return time.time_ns() // 1_000_000


def game_root():
    return read_data_root() / "games"


# Each application instance must be assigned its share of the deployment-wide budget.
_WORKERS = env_int("GAMES_DB_WORKERS", 4, low=1, high=4)
_executor = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="games-db")
_admitted = threading.BoundedSemaphore(96)
_connections = threading.BoundedSemaphore(_WORKERS)
_actor_cache = contextvars.ContextVar("minigame_transaction_actors", default=None)
_jobs = set()
_jobs_lock = threading.Lock()


def _job_finished(future):
    with _jobs_lock:
        _jobs.discard(future)


async def run_db(fn, *args):
    if not _admitted.acquire(blocking=False):
        raise HTTPException(
            503, "游戏服务繁忙，请稍后重试", headers={"Retry-After": "2"}
        )
    call = functools.partial(contextvars.copy_context().run, fn, *args)
    deadline = time.monotonic() + 1.5

    def invoke():
        try:
            if time.monotonic() > deadline:
                raise HTTPException(503, "游戏服务暂忙", headers={"Retry-After": "2"})
            return call()
        finally:
            _admitted.release()

    try:
        worker = _executor.submit(invoke)
    except RuntimeError:
        _admitted.release()
        raise
    with _jobs_lock:
        _jobs.add(worker)
    worker.add_done_callback(_job_finished)
    future = asyncio.wrap_future(worker)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # The database operation may still be committing. Shutdown waits for its worker.
        future.add_done_callback(lambda f: None if f.cancelled() else f.exception())
        raise


@contextmanager
def game_db(*, snapshot=False):
    if not _connections.acquire(timeout=1.5):
        raise HTTPException(503, "游戏连接暂忙", headers={"Retry-After": "2"})
    try:
        with get_db_connection() as conn:
            if get_configured_db_engine() == "postgres":
                if snapshot:
                    # The adapter may have used a transaction to set connection metadata.
                    conn.commit()
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                conn.execute(
                    "SELECT set_config('lock_timeout','500ms',true), set_config('statement_timeout','2s',true)"
                )
            else:
                conn.execute("PRAGMA busy_timeout=500")
                if snapshot:
                    conn.execute("BEGIN")
            token = _actor_cache.set((conn, {}))
            try:
                yield conn
            finally:
                _actor_cache.reset(token)
    except Exception as exc:
        sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
        if (
            isinstance(
                exc,
                (DatabaseBusyError, DatabaseConnectionError, DatabaseRetryableError),
            )
            or is_retryable_conflict(exc)
            or sqlstate == "57014"
            or (getattr(exc, "sqlite_errorcode", 0) or 0) & 255 in (5, 6)
        ):
            raise HTTPException(
                503, "游戏数据库暂忙，请重试原操作", headers={"Retry-After": "2"}
            ) from exc
        raise
    finally:
        _connections.release()


def db_start(conn, game_id="", *, shared=False):
    if get_configured_db_engine() == "sqlite":
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
    elif game_id:
        serialize_on(conn, "mg-game", [str(game_id)], shared=shared)


def ensure_catalog_schema(conn):
    from . import minigame_classroom_access

    minigame_classroom_access.ensure_schema(conn)
    for sql in (
        """CREATE TABLE IF NOT EXISTS mg_games (
          id TEXT PRIMARY KEY, slug TEXT UNIQUE, owner_teacher_id INTEGER NOT NULL,
          title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '[]',
          scope TEXT NOT NULL DEFAULT 'private', targets_json TEXT NOT NULL DEFAULT '[]', org_json TEXT NOT NULL DEFAULT '{}',
          min_tier INTEGER NOT NULL DEFAULT 0, max_tier INTEGER NOT NULL DEFAULT 10, featured INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'draft', current_version_id TEXT, revision INTEGER NOT NULL DEFAULT 1,
          policy_revision INTEGER NOT NULL DEFAULT 1, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
          CHECK(min_tier BETWEEN 0 AND max_tier AND max_tier<=10))""",
        """CREATE TABLE IF NOT EXISTS mg_versions (
          id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES mg_games(id), release_version TEXT,
          digest TEXT, config_json TEXT NOT NULL DEFAULT '{}', writeback_json TEXT NOT NULL DEFAULT '{}',
          rules_json TEXT, files_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL, created_at BIGINT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS mg_catalog_status ON mg_games(status,updated_at,id)",
        "CREATE INDEX IF NOT EXISTS mg_versions_game ON mg_versions(game_id,created_at)",
        """CREATE TABLE IF NOT EXISTS mg_version_files (
             version_id TEXT NOT NULL REFERENCES mg_versions(id) ON DELETE CASCADE, path TEXT NOT NULL,
             mime TEXT NOT NULL, byte_size BIGINT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(version_id,path))""",
        """CREATE UNIQUE INDEX IF NOT EXISTS mg_versions_release ON mg_versions(game_id,release_version)
           WHERE status IN ('ready','retired')""",
        """CREATE TABLE IF NOT EXISTS mg_launch_leases (
           handle_hash TEXT PRIMARY KEY, lease_id TEXT NOT NULL UNIQUE, actor TEXT NOT NULL, sid_hash TEXT NOT NULL,
           game_id TEXT NOT NULL, version_id TEXT NOT NULL, purpose TEXT NOT NULL, policy_revision INTEGER NOT NULL,
           host_instance TEXT NOT NULL, expires_at BIGINT NOT NULL, run_id TEXT, signature TEXT NOT NULL,
           command_key TEXT NOT NULL, command_digest TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
           UNIQUE(actor,command_key))""",
        "CREATE INDEX IF NOT EXISTS mg_lease_game ON mg_launch_leases(game_id,version_id)",
        "CREATE INDEX IF NOT EXISTS mg_lease_expiry ON mg_launch_leases(expires_at)",
    ):
        conn.execute(sql)
    pending = conn.execute("""SELECT v.id,v.files_json FROM mg_versions v
        WHERE v.status IN ('ready','retired') AND v.files_json<>'{}'
          AND NOT EXISTS (SELECT 1 FROM mg_version_files f WHERE f.version_id=v.id)""").fetchall()
    for row in pending:
        index_version_files(conn, row["id"], json.loads(row["files_json"]))


def index_version_files(conn, version_id, files):
    conn.executemany(
        """INSERT INTO mg_version_files(version_id,path,mime,byte_size,sha256)
        VALUES(?,?,?,?,?) ON CONFLICT(version_id,path) DO NOTHING""",
        (
            (version_id, path, meta["mime"], meta["size"], meta["sha256"])
            for path, meta in files.items()
        ),
    )


def ensure_all_schema(conn):
    from .minigame_packages import ensure_package_schema
    from .minigame_runtime import ensure_runtime_schema
    from . import minigame_discovery, minigame_moderation, minigame_wellbeing

    # SQLite otherwise auto-commits initial DDL before its first DML statement.
    # PostgreSQL's IF NOT EXISTS alone does not serialize concurrent first installs.
    serialize_on(conn, "mg-migration", ["catalog-packages-runtime"])
    ensure_catalog_schema(conn)
    ensure_package_schema(conn)
    ensure_runtime_schema(conn)
    minigame_discovery.ensure_schema(conn)
    minigame_moderation.ensure_schema(conn)
    minigame_wellbeing.ensure_schema(conn)
    missing = []
    if not schema_installed(conn, missing=missing):
        raise RuntimeError("小游戏迁移未完成，事务将回滚。缺少 " + "、".join(missing))


def load_actor(conn, actor):
    cache = _actor_cache.get()
    if cache and cache[0] is conn and actor in cache[1]:
        return dict(cache[1][actor])
    role, sep, raw_id = str(actor).partition(":")
    if (
        not sep
        or not raw_id.isascii()
        or not raw_id.isdigit()
        or role not in ("student", "teacher")
    ):
        raise HTTPException(403, "需要有效的师生账号")
    select = (
        "SELECT id,name,class_id,enrollment_status,control_mode FROM students"
        if role == "student"
        else "SELECT id,name,is_active FROM teachers"
    )
    row = conn.execute(select + " WHERE id=?", (int(raw_id),)).fetchone()
    if row is None:
        raise HTTPException(403, "账号不可用")
    row = dict(row)
    if role == "teacher":
        active = row.get("is_active") in (None, 1, True)
    else:
        active = (
            row.get("enrollment_status") or "active"
        ) == "active" and not student_is_ai_controlled(conn, row)
    if not active:
        raise HTTPException(403, "小游戏仅对在籍真人师生开放")
    user = {
        "role": role,
        "id": int(raw_id),
        "actor": f"{role}:{int(raw_id)}",
        "display_name": row["name"] or "玩家",
        "name": row["name"] or "玩家",
    }
    if role == "student":
        user["class_id"] = row["class_id"]
    if cache and cache[0] is conn:
        cache[1][actor] = dict(user)
    return user


def assert_session(conn, user):
    current = get_user_session(user["actor"], conn=conn)
    if (
        not current
        or not user.get("session_id")
        or not secrets.compare_digest(
            str(current["session_id"]), str(user["session_id"])
        )
    ):
        raise HTTPException(401, "登录状态已变化，请重新打开小游戏")


def decode_player(request: Request):
    if getattr(request.app.state, "minigames_ready", None) is False:
        raise HTTPException(503, "小游戏数据库尚未就绪")
    from .agent_platform_request_context import current_agent_platform_request_user
    from .agent_request_context import current_agent_broker_user
    from .agent_user_route_request_context import current_agent_user_route_request_user

    if any(
        probe(request)
        for probe in (
            current_agent_broker_user,
            current_agent_platform_request_user,
            current_agent_user_route_request_user,
        )
    ):
        raise HTTPException(403, "需要本人浏览器会话")
    payload = dependencies.decode_token_payload(request.cookies.get("access_token"))
    if (
        not payload
        or payload.get("auth_channel") == "mp"
        or not payload.get("session_id")
    ):
        raise HTTPException(401, "请先登录平台")
    role, uid = payload.get("role"), str(payload.get("id", ""))
    if (
        role not in ("student", "teacher")
        or not uid.isascii()
        or not uid.isdigit()
        or len(uid) > 18
    ):
        raise HTTPException(403, "需要有效的师生账号")
    return {
        "role": role,
        "id": int(uid),
        "actor": f"{role}:{int(uid)}",
        "session_id": str(payload["session_id"]),
    }


def require_player(request: Request):
    claims = decode_player(request)
    with game_db() as conn:
        user = load_actor(conn, claims["actor"])
        user["session_id"] = claims["session_id"]
        assert_session(conn, user)
    if request.url.path == "/games" or request.url.path.startswith("/games/"):
        from .login_attendance_access_service import observe_authenticated_request

        observe_authenticated_request(request, user)
    return user


def csrf_token(user):
    key = str(dependencies.SECRET_KEY).encode()
    return hmac.new(
        key, f"games-v1:{user['actor']}:{user['session_id']}".encode(), "sha256"
    ).hexdigest()


def _normalized_origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid origin")
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def content_origins():
    try:
        app = _normalized_origin(os.getenv("MINIGAMES_APP_ORIGIN", ""))
        asset = _normalized_origin(os.getenv("MINIGAMES_CONTENT_ORIGIN", ""))
        hosts = {urlsplit(origin).hostname for origin in (app, asset)}
        local = hosts <= {"localhost", "127.0.0.1", "::1"}
        if len(hosts) != 2 or (
            not all(origin.startswith("https://") for origin in (app, asset))
            and not local
        ):
            raise ValueError("isolated HTTPS hosts required")
        if not local and (
            not dependencies.SECRET_KEY
            or len(dependencies.SECRET_KEY.encode("utf-8")) < 32
            or dependencies.SECRET_KEY == "DEFAULT_WEAK_SECRET_KEY_REPLACE_ME"
        ):
            raise ValueError("configured session signing key required")
        return app, asset
    except ValueError as exc:
        raise HTTPException(503, "小游戏独立内容站尚未配置") from exc


def platform_origins(primary):
    origins = [_normalized_origin(primary)]
    aliases = os.getenv("MINIGAMES_APP_ALIASES", "").strip()
    if aliases:
        _, assets = content_origins()
        for value in aliases.split(","):
            alias = _normalized_origin(value.strip())
            parts = urlsplit(alias)
            if (
                parts.scheme != "https"
                or not re.fullmatch(r"(?i)[a-z0-9.-]+(?::[0-9]{1,5})?", parts.netloc)
                or parts.hostname == urlsplit(assets).hostname
            ):
                raise ValueError("invalid trusted classroom alias")
            origins.append(alias)
    return tuple(dict.fromkeys(origins))


def check_write(request, user):
    origin = os.getenv("MINIGAMES_APP_ORIGIN") or str(request.base_url)
    try:
        valid = _normalized_origin(
            request.headers.get("origin", "")
        ) in platform_origins(origin)
    except ValueError:
        valid = False
    supplied = request.headers.get("X-LanGame-CSRF", "")
    if (
        not valid
        or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
        or not supplied.isascii()
        or not hmac.compare_digest(supplied, csrf_token(user))
    ):
        raise HTTPException(403, "页面验证已失效，请刷新后重试")


def repository_actor_id(user):
    # Administration tables predate student publishing. Preserve all existing
    # positive teacher keys and keep student keys in a disjoint namespace.
    if user["role"] not in {"teacher", "student"} or int(user["id"]) <= 0:
        raise HTTPException(403, "需要有效的师生账号")
    return int(user["id"]) if user["role"] == "teacher" else -int(user["id"])


def require_manager(conn, user, game_id):
    row = conn.execute("SELECT * FROM mg_games WHERE id=?", (game_id,)).fetchone()
    if not row or not (row["owner_teacher_id"] == repository_actor_id(user) or
                       user["role"] == "teacher" and is_super_admin_teacher(conn, user["id"])):
        raise HTTPException(404, "未找到小游戏")
    return dict(row)


def can_preview_version(status, digest, administrator):
    return status in ("ready", "reviewing") or (
        administrator and bool(digest) and status in ("retired", "rejected")
    )


def scope_snapshot(conn, owner_teacher_id, scope):
    if scope not in ("school", "college", "department"):
        return {}
    memberships = load_teacher_org_memberships(conn, owner_teacher_id)
    selected = next(
        (m for m in memberships if m.get("is_primary") == "1"),
        memberships[0] if memberships else {},
    )
    required = {
        "mg_play_days": "student_id* local_day* early_seconds day_seconds late_seconds points coverage_base coverage_bits",
        "mg_play_totals": "student_id* reward_seconds penalty_seconds",
        "school": ("school_code",),
        "college": ("school_code", "college"),
        "department": ("school_code", "college", "department"),
    }
    if not all(selected.get(k) for k in required[scope]):
        raise HTTPException(422, "请先完善归属教师的组织信息")
    return {
        key: selected.get(key, "") for key in ("school_code", "college", "department")
    }


def scope_options(conn, user):
    if user["role"] != "teacher":
        return {"class": [], "classroom": []}
    admin = is_super_admin_teacher(conn, user["id"])
    cond, args = ("", ()) if admin else (" WHERE o.teacher_id=?", (user["id"],))
    offers = conn.execute(
        "SELECT o.id, c.name AS course_name, cl.name AS class_name FROM class_offerings o "
        "JOIN courses c ON c.id=o.course_id JOIN classes cl ON cl.id=o.class_id"
        + cond
        + " ORDER BY o.id DESC",
        args,
    ).fetchall()
    sql = "SELECT cl.id,cl.name FROM classes cl"
    if not admin:
        sql += (
            " WHERE cl.created_by_teacher_id=? OR EXISTS (SELECT 1 FROM class_offerings o WHERE o.teacher_id=? AND "
            "(o.class_id=cl.id OR EXISTS (SELECT 1 FROM class_offering_class_links l WHERE l.offering_id=o.id AND l.class_id=cl.id)))"
        )
    classes = conn.execute(
        sql + " ORDER BY cl.name,cl.id", () if admin else (user["id"], user["id"])
    ).fetchall()
    return {
        "class": [dict(row) for row in classes],
        "classroom": [
            {"id": r["id"], "name": f"{r['course_name']} · {r['class_name']}"}
            for r in offers
        ],
    }


def _audience(conn, user):
    uid = user["id"]
    if user["role"] == "student":
        row = conn.execute("SELECT * FROM students WHERE id=?", (uid,)).fetchone()
        class_id = row["class_id"]
        offers = conn.execute(
            "SELECT o.id FROM class_offerings o WHERE " + student_offering_where(),
            (class_id, class_id),
        )
        return {
            "classes": {class_id},
            "offers": {r["id"] for r in offers},
            "orgs": [org_scope_from_row(dict(row))],
            "tier": student_global_tier(conn, uid),
            "admin": False,
        }
    if is_super_admin_teacher(conn, uid):
        return {"offers": set(), "classes": set(), "orgs": [], "tier": None, "admin": True}
    offers = conn.execute(
        "SELECT id,class_id FROM class_offerings WHERE teacher_id=?", (uid,)
    ).fetchall()
    linked = conn.execute(
        "SELECT l.class_id FROM class_offering_class_links l JOIN class_offerings o ON l.offering_id=o.id WHERE o.teacher_id=?",
        (uid,),
    ).fetchall()
    classes = conn.execute(
        "SELECT id FROM classes WHERE created_by_teacher_id=?", (uid,)
    ).fetchall()
    return {
        "offers": {r["id"] for r in offers},
        "classes": {r["class_id"] for r in offers + linked}
        | {r["id"] for r in classes},
        "orgs": load_teacher_org_memberships(conn, uid),
        "tier": None,
        "admin": False,
    }


def allowed(game, user, audience):
    if audience.get("admin", False):
        return True
    tier = audience["tier"]
    if tier is not None and not game["min_tier"] <= tier <= game["max_tier"]:
        return False
    scope = game["scope"]
    if scope == "public":
        return True
    if scope == "private":
        return repository_actor_id(user) == game["owner_teacher_id"]
    if scope in ("class", "classroom"):
        return bool(
            set(json.loads(game["targets_json"] or "[]"))
            & audience["classes" if scope == "class" else "offers"]
        )
    if scope not in ("school", "college", "department"):
        return False
    target = json.loads(game["org_json"] or "{}")
    keys = {
        "school": ["school_code"],
        "college": ["school_code", "college"],
        "department": ["school_code", "college", "department"],
    }[scope]
    return all(target.get(k) for k in keys) and any(
        all(org.get(k) == target[k] for k in keys) for org in audience["orgs"]
    )


def version_definition(version):
    return {
        "config": json.loads(version["config_json"]),
        "rules": json.loads(version["rules_json"] or "null"),
        "writeback": json.loads(version["writeback_json"]),
    }


def require_play(conn, user, game_id, version_id=None, *, definition=True):
    from .minigame_classroom_access import require_access

    game = listed_version(conn, user, game_id, version_id, definition=definition)
    game["accessUntil"] = require_access(conn, user, game_id)
    return game


def listed_version(conn, user, game_id, version_id=None, *, definition=True):
    # An explicitly pinned old version is allowed only for an already-running participant.
    load_actor(conn, user["actor"])
    game = conn.execute(
        "SELECT * FROM mg_games WHERE id=? AND status='listed'", (game_id,)
    ).fetchone()
    if not game:
        raise HTTPException(404, "小游戏不可用")
    if game["scope"] == "public":
        tier_gate = (game["min_tier"], game["max_tier"]) != (0, 10)
        if (
            tier_gate
            and user["role"] == "student"
            and not game["min_tier"]
            <= student_global_tier(conn, user["id"])
            <= game["max_tier"]
        ):
            raise HTTPException(404, "小游戏不可用")
    elif not allowed(game, user, _audience(conn, user)):
        raise HTTPException(404, "小游戏不可用")
    vid = version_id or game["current_version_id"]
    if vid != game["current_version_id"]:
        from . import minigame_runtime

        if not minigame_runtime.has_active_version(conn, user["actor"], game_id, vid):
            raise HTTPException(404, "游戏版本已更新")
    fields = "v.config_json,v.rules_json,v.writeback_json" if definition else "v.id"
    ver = conn.execute(
        f"""SELECT {fields},a.author AS author_name,a.allow_remix,a.source_json FROM mg_versions v
        LEFT JOIN mg_version_authorship a ON a.version_id=v.id
        WHERE v.id=? AND v.game_id=? AND v.status='ready'""",
        (vid, game_id),
    ).fetchone()
    if not ver:
        raise HTTPException(404, "游戏版本不可用")
    return {
        **dict(game),
        "allow_remix": bool(ver["allow_remix"]),
        "author_name": ver["author_name"] or "",
        "source_json": ver["source_json"],
        "version_id": vid,
        **(version_definition(ver) if definition else {}),
    }


def public_game(row):
    from .minigame_discovery import genre_labels

    row = dict(row)
    config = (
        json.loads(row["config_json"] or "{}")
        if "config_json" in row
        else row.get("config", {})
    )
    if "modes_json" in row:
        config = {"modes": json.loads(row["modes_json"] or "{}")}
    if row.get("source_json") and (attribution := json.loads(row["source_json"])):
        config = {**config, "attribution": attribution}
    genre = row.get("genre_id") or config.get("genre", "other")
    share = bool(row.get("allow_remix")) and row["status"] == "listed"
    version_id = row.get("version_id") or row["current_version_id"]
    return {
        "id": row["id"],
        "slug": row["slug"] if row["slug"] and row["slug"] != "workshop" else row["id"],
        "title": row["title"],
        "summary": row["summary"],
        "tags": json.loads(row["tags_json"]),
        "genre": genre,
        "genreLabel": genre_labels().get(genre, "其他"),
        "authorName": row.get("author_name") or config.get("author", ""),
        "allowRemix": share,
        "sourceUrl": f"/api/games/v1/games/{row['id']}/source?version={version_id}" if share else None,
        "sourceVersionId": version_id if share else None,
        "version_id": version_id,
        "config": config,
        "status": row["status"],
        "scope": row["scope"],
        "min_tier": row["min_tier"],
        "max_tier": row["max_tier"],
        "revision": row["revision"],
        "featured": bool(row["featured"]),
        "iconUrl": f"/api/games/v1/games/{row['id']}/icon",
    }


def schema_installed(conn=None, *, missing=None):
    """Read the same configured database as the game service, without creating it.

    A caller-supplied connection inspects uncommitted migration work before commit.
    Extra compatible columns and indexes from a later release are permitted.
    """
    from .minigame_runtime_store import INDEXES

    postgres = get_configured_db_engine() == "postgres"
    if conn is None:
        if postgres:
            with game_db() as db:
                return schema_installed(db, missing=missing)
        import sqlite3
        from contextlib import closing
        from .. import config

        path = config.DB_PATH.resolve()
        if not path.is_file():
            if missing is not None:
                missing.append("SQLite 数据库文件")
            return False
        # Avoid the normal writer's mkdir, file creation and journal-mode PRAGMA.
        # Keep WAL locking/change detection enabled to see a live writer's committed state.
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        ) as db:
            db.execute("BEGIN")
            return schema_installed(db, missing=missing)

    # '*' marks primary-key columns. These tables are used by the catalog, worker and
    # runtime separately, including tables first accessed after an upload or a room fills.
    required = {
        "mg_classroom_access": "offering_id* teacher_id updated_by mode games_json duration_minutes expires_at revision",
        "mg_discovery_preferences": "actor* genres_json tags_json revision",
        "mg_content_reviews": "version_id* game_id digest status reason ai_reason categories_json evidence_json claim_token lease_until attempts decider created_at updated_at notify_due",
        "mg_review_notices": "version_id* teacher_id* inbox_id email_queued due",
        "mg_version_authorship": "version_id* author allow_remix source_version_id source_author source_tags_json source_json tags_json package_sha256 release_rank",
        "mg_games": "id* slug owner_teacher_id title summary tags_json scope targets_json org_json min_tier max_tier featured status current_version_id revision policy_revision created_at updated_at",
        "mg_versions": "id* game_id release_version digest config_json writeback_json rules_json files_json status created_at",
        "mg_version_files": "version_id* path* mime byte_size sha256",
        "mg_launch_leases": "handle_hash* lease_id actor sid_hash game_id version_id purpose policy_revision host_instance expires_at run_id signature command_key command_digest revoked",
        "mg_admin_jobs": "id* game_id version_id actor kind status stage token expires due reserved error_json result_json created",
        "mg_admin_receipts": "actor* op* key_value* game_id digest result",
        "mg_admin_plans": "token* actor game_id revision expires",
        "mg_admin_assets": "game_id kind* asset_id* bytes_used",
        "mg_admin_playtests": "version_id game_id actor digest passed created id*",
        "mg_admin_preview_receipts": "lease_id* game_id version_id actor digest loaded_at",
        "mg_admin_releases": "game_id* version_id* series save_version",
        "mg_admin_serial": "id*",
        "mg_rt_activity": "id* game version mode phase owner capacity requested planned gen starts run revision seq state_json outcome created started ended code_lookup code_cipher code_until team_name chat_enabled",
        "mg_rt_seats": "activity* actor* seat alias name joined",
        "mg_rt_claims": "actor* slot* game target host epoch session_key expires touched interacted",
        "mg_rt_tickets": "id* game version capacity actor phase queued activity seq",
        "mg_rt_bucket": "game* version* capacity* anchor",
        "mg_rt_codes": "digest* game activity expires",
        "mg_rt_progress": "game* series* save_ver* actor* rank shared_rev private_rev priv",
        "mg_rt_changes": "id* game run actor before_rank after_rank revision at_ms",
        "mg_rt_events": "activity* seq* kind revision generation at_ms",
        "mg_rt_messages": "id* activity actor body at_ms",
        "mg_rt_templates": "id* actor game fingerprint activity last_used",
        "mg_rt_prefs": "actor* display_alias sharing muted revision",
        "mg_rt_receipts": "actor* game* op* k* digest result at_ms scope_ref",
        "mg_rt_completed_scopes": "ref* game at_ms",
        "mg_rt_attempts": "k* window_ms n",
    }
    if postgres:
        columns = conn.execute("""SELECT t.relname,a.attname,a.attnum=ANY(pk.indkey)
            FROM pg_catalog.pg_class t JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
            JOIN pg_catalog.pg_attribute a ON a.attrelid=t.oid AND a.attnum>0 AND NOT a.attisdropped
            LEFT JOIN pg_catalog.pg_index pk ON pk.indrelid=t.oid AND pk.indisprimary AND pk.indisvalid
            WHERE n.nspname=current_schema() AND t.relkind IN ('r','p') AND t.relname LIKE 'mg!_%' ESCAPE '!'""")
        index_sql = """SELECT i.relname FROM pg_catalog.pg_index x
            JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid
            JOIN pg_catalog.pg_namespace n ON n.oid=i.relnamespace
            WHERE n.nspname=current_schema() AND x.indisvalid AND x.indisready
              AND (left(i.relname,3)='mg_' OR left(i.relname,7)='idx_mg_')"""
    else:
        columns = conn.execute("""SELECT t.name,c.name,c.pk FROM sqlite_schema t
            JOIN pragma_table_info(t.name) c WHERE t.type='table' AND t.name LIKE 'mg!_%' ESCAPE '!'""")
        index_sql = """SELECT name FROM sqlite_schema WHERE type='index'
            AND (name GLOB 'mg_*' OR name GLOB 'idx_mg_*')"""
    found = {}
    keys = {}
    for row in columns.fetchall():
        table, name, key = row[0], row[1], row[2]
        found.setdefault(table, set()).add(name)
        if key:
            keys.setdefault(table, set()).add(name)
    problems = []
    for table, definition in required.items():
        if table not in found:
            problems.append(table)
            continue
        expected = definition.split()
        for name in expected:
            if name.removesuffix("*") not in found[table]:
                problems.append(table + "." + name.removesuffix("*"))
        if keys.get(table, set()) != {
            name[:-1] for name in expected if name.endswith("*")
        }:
            problems.append(table + " PRIMARY KEY")
    indexes = set(
        "mg_catalog_status mg_versions_game mg_versions_release mg_lease_game mg_lease_expiry "
        "idx_mg_jobs_due idx_mg_jobs_game idx_mg_preview_receipt idx_mg_preview_age idx_mg_releases_series "
        "mg_content_reviews_queue mg_review_notices_due idx_mg_authorship_digest".split()
    )
    indexes.update(
        "mg_ri_" + table + "_" + columns.replace(",", "_").replace(" ", "")
        for table, columns in INDEXES
    )
    problems.extend(
        sorted(indexes - {row[0] for row in conn.execute(index_sql).fetchall()})
    )
    if (
        not problems
        and not conn.execute("SELECT 1 FROM mg_admin_serial WHERE id=1").fetchone()
    ):
        problems.append("mg_admin_serial[1]")
    if missing is not None:
        missing.extend(problems)
    return not problems


async def start_minigames(app, *, dedicated_api=False):
    app.state.minigames_ready = False
    app.state.minigames_owns_workers = False
    try:
        if not await run_db(schema_installed):
            return
        if dedicated_api or os.getenv("MINIGAMES_EXTERNAL_API", "").strip() != "1":
            from .minigame_runtime import start_runtime
            from .minigame_packages import start_package_worker

            # A partial start still owns cleanup. SSR-only processes never stop another owner.
            app.state.minigames_owns_workers = True
            start_runtime()
            await start_package_worker()
        app.state.minigames_ready = True
    except Exception:
        logging.getLogger(__name__).exception(
            "Minigame startup failed; game routes are unavailable"
        )
        await stop_minigames(app)


async def stop_minigames(app):
    app.state.minigames_ready = False
    if getattr(app.state, "minigames_owns_workers", False):
        from .minigame_runtime import stop_runtime
        from .minigame_packages import stop_package_worker

        try:
            await stop_runtime()
        finally:
            await stop_package_worker()
            app.state.minigames_owns_workers = False
    with _jobs_lock:
        pending = tuple(_jobs)
    if pending:
        await asyncio.gather(
            *(asyncio.wrap_future(job) for job in pending), return_exceptions=True
        )
