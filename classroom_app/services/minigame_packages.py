"""Teacher catalog commands and persistent package-validation/deletion jobs."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import uuid

from fastapi import HTTPException

from . import minigame_platform as platform
from . import minigame_package_validation as validation
from . import minigame_repository as repository
from . import minigame_package_suggestions as suggestions
from .resource_access_service import is_super_admin_teacher

log = logging.getLogger(__name__)
WORKER_TTL = 5 * 60_000
PROBE_BACKOFF_MS = 120_000
PROBE_OFFLINE_STAGE = "静态检查已通过，执行器恢复后自动继续"
KEY = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
GAME_SCOPES = {"private", "classroom", "class", "department", "college", "school", "public"}
VERSION_WITH_AUTHOR = """SELECT v.id,v.game_id,v.created_at,v.digest,v.release_version,v.status,v.config_json,
    a.author,a.allow_remix,a.tags_json,a.source_version_id,a.source_author,
    a.source_tags_json FROM mg_versions v LEFT JOIN mg_version_authorship a ON a.version_id=v.id"""
FOLDER_SELECT = """SELECT g.*,v.config_json,a.author,a.allow_remix,a.source_version_id,
    (SELECT COUNT(*) FROM mg_versions vc WHERE vc.game_id=g.id AND vc.status<>'deleted') AS version_count
    FROM mg_games g LEFT JOIN mg_versions v ON v.id=COALESCE(g.current_version_id,
    (SELECT vx.id FROM mg_versions vx WHERE vx.game_id=g.id AND vx.status<>'deleted'
     ORDER BY vx.created_at DESC,vx.id DESC LIMIT 1))
    LEFT JOIN mg_version_authorship a ON a.version_id=v.id"""
_executor = _runner = _ai_runner = None
_stop = None


def ensure_package_schema(conn):
    statements = [
        """CREATE TABLE IF NOT EXISTS mg_version_authorship(
        version_id TEXT PRIMARY KEY REFERENCES mg_versions(id) ON DELETE CASCADE,author TEXT NOT NULL,
        allow_remix INTEGER NOT NULL DEFAULT 0,source_version_id TEXT NOT NULL DEFAULT '',
        source_author TEXT NOT NULL DEFAULT '',source_tags_json TEXT NOT NULL DEFAULT '[]',
        source_json TEXT NOT NULL DEFAULT '{}',tags_json TEXT NOT NULL DEFAULT '[]',package_sha256 TEXT NOT NULL DEFAULT '',
        release_rank BIGINT NOT NULL DEFAULT 0)""",
        "CREATE INDEX IF NOT EXISTS idx_mg_authorship_digest ON mg_version_authorship(package_sha256)",
        """CREATE TABLE IF NOT EXISTS mg_admin_jobs (
           id TEXT PRIMARY KEY, game_id TEXT NOT NULL, version_id TEXT NOT NULL, actor INTEGER NOT NULL,
           kind TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, token TEXT NOT NULL DEFAULT '',
           expires BIGINT NOT NULL DEFAULT 0, due BIGINT NOT NULL DEFAULT 0, reserved BIGINT NOT NULL DEFAULT 0,
           error_json TEXT NOT NULL DEFAULT '{}', result_json TEXT NOT NULL DEFAULT '{}', created BIGINT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_mg_jobs_due ON mg_admin_jobs(status,due,expires,created)",
        "CREATE INDEX IF NOT EXISTS idx_mg_jobs_game ON mg_admin_jobs(game_id,created)",
        """CREATE TABLE IF NOT EXISTS mg_admin_receipts (
           actor INTEGER NOT NULL, op TEXT NOT NULL, key_value TEXT NOT NULL, game_id TEXT NOT NULL,
           digest TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(actor,op,key_value))""",
        """CREATE TABLE IF NOT EXISTS mg_admin_plans (token TEXT PRIMARY KEY,actor INTEGER NOT NULL,
           game_id TEXT NOT NULL,revision INTEGER NOT NULL,expires BIGINT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS mg_admin_assets (game_id TEXT NOT NULL,kind TEXT NOT NULL,
           asset_id TEXT NOT NULL, bytes_used BIGINT NOT NULL DEFAULT 0,PRIMARY KEY(kind,asset_id))""",
        """CREATE TABLE IF NOT EXISTS mg_admin_playtests(version_id TEXT NOT NULL,game_id TEXT NOT NULL,
           actor INTEGER NOT NULL,digest TEXT NOT NULL,passed INTEGER NOT NULL,created BIGINT NOT NULL,
           id TEXT PRIMARY KEY)""",
        """CREATE TABLE IF NOT EXISTS mg_admin_preview_receipts(lease_id TEXT PRIMARY KEY,game_id TEXT NOT NULL,
           version_id TEXT NOT NULL,actor INTEGER NOT NULL,digest TEXT NOT NULL,loaded_at BIGINT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_mg_preview_receipt ON mg_admin_preview_receipts(game_id,version_id,actor,loaded_at)",
        "CREATE INDEX IF NOT EXISTS idx_mg_preview_age ON mg_admin_preview_receipts(loaded_at)",
        """CREATE TABLE IF NOT EXISTS mg_admin_releases(game_id TEXT NOT NULL,version_id TEXT NOT NULL,
           series TEXT NOT NULL,save_version INTEGER NOT NULL,PRIMARY KEY(game_id,version_id))""",
        "CREATE INDEX IF NOT EXISTS idx_mg_releases_series ON mg_admin_releases(game_id,series,save_version)",
        """CREATE TABLE IF NOT EXISTS mg_admin_serial(id INTEGER PRIMARY KEY)""",
        """INSERT INTO mg_admin_serial(id) VALUES (1) ON CONFLICT(id) DO NOTHING""",
    ]
    for statement in statements:
        conn.execute(statement)
    # Register historical releases in the new index without granting source distribution.
    legacy = conn.execute(
        """SELECT v.id,v.release_version,v.config_json,g.tags_json FROM mg_versions v
           JOIN mg_games g ON g.id=v.game_id LEFT JOIN mg_version_authorship a ON a.version_id=v.id
           WHERE a.version_id IS NULL AND v.status IN ('ready','retired') AND v.release_version IS NOT NULL"""
    ).fetchall()
    for version in legacy:
        number = version["release_version"]
        if re.fullmatch(r"(?:0|[1-9][0-9]{0,4})(?:\.(?:0|[1-9][0-9]{0,4})){2}", number):
            cfg = json.loads(version["config_json"] or "{}")
            conn.execute(
                """INSERT INTO mg_version_authorship(version_id,author,tags_json,release_rank)
                   VALUES(?,?,?,?) ON CONFLICT(version_id) DO NOTHING""",
                (version["id"], cfg.get("author", ""), version["tags_json"], repository.release_rank(number)),
            )


def error(status, code, message):
    raise HTTPException(status, {"code": code, "message": message})


def uid():
    return uuid.uuid4().hex


def object_game(row):
    from .minigame_discovery import genre_labels

    row = dict(row)
    genre = json.loads(row.get("config_json") or "{}").get("genre", "other")
    return {
        "id": row["id"],
        "slug": row["slug"],
        "gameKey": row["slug"],
        "title": row["title"],
        "summary": row["summary"],
        "tags": json.loads(row["tags_json"]),
        "genre": genre,
        "genreLabel": genre_labels().get(genre, "其他"),
        "scope": row["scope"],
        "targets": json.loads(row["targets_json"]),
        "minTier": row["min_tier"],
        "maxTier": row["max_tier"],
        "featured": bool(row["featured"]),
        "status": row["status"],
        "currentVersionId": row["current_version_id"],
        "revision": row["revision"],
        "policyRevision": row["policy_revision"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "versionCount": row.get("version_count", 0),
        "author": row.get("author") or "",
        "allowRemix": bool(row.get("allow_remix")),
        "sourceVersionId": row.get("source_version_id") or "",
        "ownerRole": "student" if row["owner_teacher_id"] < 0 else "teacher",
    }


def version_info(row):
    row = dict(row)
    cfg = json.loads(row["config_json"] or "{}")
    return {
        "id": row["id"],
        "versionId": row["id"],
        "gameId": row["game_id"],
        "releaseVersion": row["release_version"],
        "title": cfg.get("title", ""),
        "config": cfg,
        "digest": row["digest"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "author": row.get("author") or cfg.get("author") or "",
        "authorName": row.get("author") or cfg.get("author") or "",
        "allowRemix": bool(row.get("allow_remix")),
        "tags": json.loads(row.get("tags_json") or "[]"),
        "sourceVersionId": row.get("source_version_id") or "",
        "sourceAuthor": row.get("source_author") or "",
        "sourceTags": json.loads(row.get("source_tags_json") or "[]"),
    }


def job_info(row):
    result = json.loads(row["result_json"])
    if row["kind"] == "suggest" and row["status"] != "succeeded":
        result = {}
    return {
        "id": row["id"],
        "jobId": row["id"],
        "gameId": row["game_id"],
        "versionId": row["version_id"],
        "status": row["status"],
        "stage": row["stage"],
        "kind": row["kind"],
        "error": json.loads(row["error_json"]),
        "result": result,
    }


def one(conn, table, item):
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (item,)).fetchone()
    if row is None:
        error(404, "NOT_FOUND", "记录不存在。")
    return row


def require_teacher(user):
    if user["role"] != "teacher":
        error(403, "TEACHER_REQUIRED", "该操作需要教师身份。")


def recheck_contributor(conn, user):
    platform.assert_session(conn, user)
    platform.repository_actor_id(platform.load_actor(conn, user["actor"]))


def related_games(conn, user, game):
    actor_id = platform.repository_actor_id(user)
    is_owner = game["owner_teacher_id"] == actor_id
    owner_clause = " AND g.owner_teacher_id=?" if is_owner else ""
    owner_args = (actor_id,) if is_owner else ()
    base_sql = """SELECT g.id,g.slug,g.title,g.status,g.owner_teacher_id,g.updated_at,
        (SELECT COUNT(*) FROM mg_versions vc WHERE vc.game_id=g.id AND vc.status<>'deleted') AS version_count
        FROM mg_games g WHERE g.status<>'deleted' AND g.id<>?""" + owner_clause
    found = {}

    def collect(rows, relation):
        for r in rows:
            gid = r["id"]
            if gid in found or len(found) >= 12:
                continue
            found[gid] = {
                "id": gid,
                "slug": r["slug"] or "",
                "gameKey": r["slug"] or "",
                "title": r["title"],
                "status": r["status"],
                "versionCount": int(r["version_count"] or 0),
                "ownerRole": "student" if r["owner_teacher_id"] < 0 else "teacher",
                "relation": relation,
            }

    source_rows = conn.execute(
        base_sql + """ AND EXISTS (
            SELECT 1 FROM mg_versions sv JOIN mg_version_authorship a ON a.source_version_id=sv.id
            JOIN mg_versions cv ON cv.id=a.version_id
            WHERE sv.game_id=g.id AND cv.game_id=? AND a.source_version_id<>''
        ) ORDER BY g.updated_at DESC,g.id DESC LIMIT 6""",
        (game["id"], *owner_args, game["id"]),
    ).fetchall()
    collect(source_rows, "source")

    remix_rows = conn.execute(
        base_sql + """ AND EXISTS (
            SELECT 1 FROM mg_versions rv JOIN mg_version_authorship a ON a.version_id=rv.id
            JOIN mg_versions pv ON pv.id=a.source_version_id
            WHERE rv.game_id=g.id AND pv.game_id=? AND a.source_version_id<>''
        ) ORDER BY g.updated_at DESC,g.id DESC LIMIT 8""",
        (game["id"], *owner_args, game["id"]),
    ).fetchall()
    collect(remix_rows, "remix")

    title = (game.get("title") or "").strip()
    if title:
        same_title_rows = conn.execute(
            base_sql + " AND LOWER(TRIM(g.title))=LOWER(TRIM(?)) ORDER BY g.updated_at DESC,g.id DESC LIMIT 8",
            (game["id"], *owner_args, title),
        ).fetchall()
        collect(same_title_rows, "same_title")

    return list(found.values())


def detail(conn, user, game_id, version_offset=0):
    from . import minigame_moderation as moderation
    game = platform.require_manager(conn, user, game_id)
    version_rows = conn.execute(
        VERSION_WITH_AUTHOR + """ WHERE v.game_id=? AND v.status<>'deleted'
        ORDER BY COALESCE(a.release_rank,0) DESC,v.created_at DESC,v.id DESC LIMIT 101 OFFSET ?""",
        (game_id,version_offset),
    ).fetchall()
    versions = [version_info(v) for v in version_rows[:100]]
    version_ids = [v["id"] for v in versions]
    reviews = moderation.get_summaries(conn, version_ids)
    playtested = {}
    previewed = set()
    if version_ids:
        placeholders = ",".join("?" for _ in version_ids)
        for pt in conn.execute(
            f"""SELECT version_id,digest,passed FROM mg_admin_playtests
                WHERE game_id=? AND version_id IN ({placeholders})
                ORDER BY created DESC,id DESC""",
            (game_id, *version_ids),
        ).fetchall():
            playtested.setdefault((pt["version_id"], pt["digest"]), bool(pt["passed"]))
        previewed = {
            (pr["version_id"], pr["digest"])
            for pr in conn.execute(
                f"""SELECT version_id,digest FROM mg_admin_preview_receipts
                    WHERE actor=? AND game_id=? AND version_id IN ({placeholders}) AND loaded_at>?""",
                (platform.repository_actor_id(user), game_id, *version_ids, platform.now_ms() - 3600000),
            ).fetchall()
        }
    for version in versions:
        version["review"] = reviews.get(version["id"])
        key = (version["id"], version["digest"])
        version["playtested"] = bool(version["digest"] and playtested.get(key, False))
        version["previewed"] = bool(version["digest"] and key in previewed)
    info = object_game(conn.execute(FOLDER_SELECT + " WHERE g.id=?", (game_id,)).fetchone())
    return {
        "game": info,
        "versions": versions,
        "relatedGames": related_games(conn, user, game) if version_offset == 0 else [],
        "nextVersionOffset": version_offset + 100 if len(version_rows) > 100 else None,
        "jobs": [
            job_info(j)
            for j in conn.execute(
                "SELECT * FROM mg_admin_jobs WHERE game_id=? ORDER BY created DESC,id DESC LIMIT 30",
                (game_id,),
            ).fetchall()
        ],
    }


def list_games(conn, user, params):
    clauses, args = ["g.status<>'deleted'"], []
    if user["role"] != "teacher" or not is_super_admin_teacher(conn, user["id"]):
        clauses.append("g.owner_teacher_id=?")
        args.append(platform.repository_actor_id(user))
    for field in ("status", "scope"):
        if value := params.get(field):
            clauses.append(f"g.{field}=?")
            args.append(value)
    if query := params.get("q", "").strip()[:120]:
        esc = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        clauses.append(
            "(LOWER(g.title) LIKE LOWER(?) ESCAPE '!' OR LOWER(g.slug) LIKE LOWER(?) ESCAPE '!' OR LOWER(g.tags_json) LIKE LOWER(?) ESCAPE '!')"
        )
        args.extend([f"%{esc}%"] * 3)
    if tag := params.get("tag"):
        clauses.append("g.tags_json LIKE ? ESCAPE '!'")
        escaped = validation.canonical(tag).replace("!", "!!").replace("%", "!%").replace("_", "!_")
        args.append("%" + escaped + "%")
    if (mode := params.get("mode")) in {"solo", "coop", "versus"}:
        marker = '"solo":true' if mode == "solo" else '"kind":"' + mode + '"'
        clauses.append("v.config_json LIKE ?")
        args.append("%" + marker + "%")
    if genre := params.get("genre"):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", genre):
            error(422, "INVALID_GENRE", "请选择有效的内容类型。")
        clause = "v.config_json LIKE ?"
        if genre == "other":
            clause = "(" + clause + """ OR COALESCE(v.config_json,'{}') NOT LIKE '%"genre":%')"""
        clauses.append(clause)
        args.append('%"genre":"' + genre + '"%')
    if cursor := params.get("cursor"):
        pair = cursor.split(".", 1)
        if (
            len(pair) != 2
            or not pair[0].isdigit()
            or len(pair[0]) > 17
            or not re.fullmatch("[a-f0-9]{32}", pair[1])
        ):
            error(422, "INVALID_CURSOR", "分页游标无效。")
        clauses.append("(g.updated_at < ? OR (g.updated_at=? AND g.id<?))")
        args.extend((int(pair[0]), int(pair[0]), pair[1]))
    rows = conn.execute(
        FOLDER_SELECT + " WHERE "
        + " AND ".join(clauses)
        + " ORDER BY g.updated_at DESC,g.id DESC LIMIT 25",
        tuple(args),
    ).fetchall()
    return {
        "items": [object_game(r) for r in rows[:24]],
        "nextCursor": f"{rows[23]['updated_at']}.{rows[23]['id']}" if len(rows) == 25 else None,
    }


def serial(conn):
    conn.execute("UPDATE mg_admin_serial SET id=id WHERE id=1")


def receipt(conn, user, op, key, digest):
    if not KEY.fullmatch(key or ""):
        error(422, "REQUEST_KEY", "缺少有效的 Idempotency-Key。")
    row = conn.execute(
        "SELECT * FROM mg_admin_receipts WHERE actor=? AND op=? AND key_value=?", (platform.repository_actor_id(user), op, key)
    ).fetchone()
    if row and row["digest"] != digest:
        error(409, "REQUEST_KEY_CONFLICT", "该请求键已用于不同内容。")
    return json.loads(row["result"]) if row else None


def store_receipt(conn, user, op, key, game_id, digest, result):
    conn.execute(
        "INSERT INTO mg_admin_receipts(actor,op,key_value,game_id,digest,result) VALUES(?,?,?,?,?,?)",
        (platform.repository_actor_id(user), op, key, game_id, digest, validation.canonical(result)),
    )


def check_revision(row, body):
    rev = body.get("expectedRevision")
    if type(rev) is not int or rev != row["revision"]:
        error(409, "STALE_REVISION", "游戏已更新，请核对最新版本后重试。")
    if row["status"] in {"deleting", "deleted"}:
        error(409, "GAME_REMOVED", "此游戏已进入删除流程。")


def set_attributes(conn, user, row, body):
    allowed = {
        "title",
        "summary",
        "tags",
        "scope",
        "targets",
        "minTier",
        "maxTier",
        "featured",
        "expectedRevision",
    }
    if not body.keys() <= allowed:
        error(422, "INVALID_PROPERTIES", "请求含未知属性。")
    old = object_game(row)
    if row["slug"] and "title" in body and body["title"] != old["title"]:
        error(422, "PACKAGE_TITLE", "名称由游戏包 config.json 的 title 决定。")
    administrator = user["role"] == "teacher" and is_super_admin_teacher(conn, user["id"])
    if not administrator:
        if row["slug"] and any(k in body and body[k] != old[k] for k in ("title", "summary", "tags")):
            error(403, "REVIEWED_METADATA", "公开文案与标签随新版本提交审核。")
        if "featured" in body and body["featured"] != old["featured"]:
            error(403, "ADMIN_REQUIRED", "精选标记由超管管理。")
        if user["role"] == "student" and body.get("scope", old["scope"]) not in {"public", "private"}:
            error(403, "SCOPE_FORBIDDEN", "请选择公开或私有范围。")
    new = {**old, **body}
    if (
        not isinstance(new["title"], str)
        or not new["title"].strip()
        or len(new["title"]) > 48
        or not isinstance(new["summary"], str)
        or len(new["summary"]) > 240
        or not isinstance(new["tags"], list)
        or len(new["tags"]) > 12
        or not all(isinstance(t, str) and 0 < len(t.strip()) <= 32 for t in new["tags"])
        or type(new["featured"]) is not bool
        or type(new["minTier"]) is not int
        or type(new["maxTier"]) is not int
        or not 0 <= new["minTier"] <= new["maxTier"] <= 10
        or not isinstance(new["scope"], str)
        or new["scope"] not in GAME_SCOPES
    ):
        error(422, "INVALID_PROPERTIES", "检查名称、标签、范围与修为等级。")
    target = new["targets"]
    if not isinstance(target, list) or len(target) > 200 or not all(type(i) is int and i > 0 for i in target):
        error(422, "INVALID_TARGETS", "只接受有效课堂或班级编号。")
    scope_changed = any(k in body for k in ("scope", "targets"))
    if new["scope"] in ("classroom", "class"):
        if not target:
            error(422, "MISSING_TARGETS", "请至少选择一个可管理的目标。")
        if scope_changed:
            available = {int(i["id"]) for i in platform.scope_options(conn, user)[new["scope"]]}
            if not set(target) <= available:
                error(403, "TARGET_FORBIDDEN", "所选目标超出可授予范围。")
    elif target:
        error(422, "INVALID_TARGETS", "当前范围不能指定课堂或班级。")
    org = json.loads(row["org_json"])
    if "scope" in body and (old["scope"] != new["scope"] or not org):
        org = (
            platform.scope_snapshot(conn, row["owner_teacher_id"] if row["owner_teacher_id"] > 0 else user["id"], new["scope"])
            if new["scope"] in {"school", "college", "department"}
            else {}
        )
    new["targets"] = sorted(set(target))
    policy = any(new[k] != old[k] for k in ("scope", "targets", "minTier", "maxTier"))
    if "tags" in body and new["tags"] != old["tags"]:
        target_version = conn.execute(
            """SELECT a.version_id,a.source_tags_json FROM mg_version_authorship a
               JOIN mg_versions v ON v.id=a.version_id WHERE v.game_id=? AND v.status NOT IN ('deleting','deleted')
               ORDER BY CASE WHEN v.id=? THEN 0 ELSE 1 END,a.release_rank DESC,v.created_at DESC LIMIT 1""",
            (row["id"], row["current_version_id"]),
        ).fetchone()
        if target_version:
            required = json.loads(target_version["source_tags_json"])
            new["tags"] = repository.check_tags(list(dict.fromkeys([*required, *new["tags"]])))
            conn.execute("UPDATE mg_version_authorship SET tags_json=? WHERE version_id=?",
                         (validation.canonical(new["tags"]),target_version["version_id"]))
    conn.execute(
        """UPDATE mg_games SET title=?,summary=?,tags_json=?,scope=?,targets_json=?,org_json=?,
        min_tier=?,max_tier=?,featured=?,revision=revision+1,policy_revision=policy_revision+?,updated_at=? WHERE id=?""",
        (
            new["title"].strip(),
            new["summary"].strip(),
            validation.canonical(list(dict.fromkeys(t.strip() for t in new["tags"]))),
            new["scope"],
            validation.canonical(sorted(set(target))),
            validation.canonical(org),
            new["minTier"],
            new["maxTier"],
            int(new["featured"]),
            int(policy),
            platform.now_ms(),
            row["id"],
        ),
    )
    if policy and row["status"] == "listed":
        from .minigame_runtime import on_policy_change

        on_policy_change(conn, row["id"])


def checked_dir(root, child):
    """Reject symlinks/junctions in every owned component before following the path."""
    if not child.is_relative_to(root) or child == root:
        raise RuntimeError("owned game path invalid")
    for p in (root, *[a for a in reversed(child.parents) if a.is_relative_to(root) and a != root], child):
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise RuntimeError("owned game path changed")
    if child.exists() and not child.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("owned game path escapes root")
    return child


def asset_path(kind, game_id, item):
    if not re.fullmatch(r"[0-9a-f]{32}", item) or not re.fullmatch(r"[0-9a-f]{32}", game_id):
        raise RuntimeError("invalid internal game asset id")
    root = platform.game_root()
    if kind == "stage":
        return checked_dir(root, root / "staging" / item)
    if kind == "version":
        return checked_dir(root, root / "versions" / game_id / item)
    raise RuntimeError("unknown asset kind")


def marker(path, game, item, create=False):
    owner = path / ".minigame-owner"
    expected = game + "/" + item
    if create:
        path.mkdir(parents=True, exist_ok=False)
        with owner.open("x", encoding="ascii") as handle:
            handle.write(expected)
    elif owner.is_symlink() or not owner.is_file() or owner.read_text(encoding="ascii") != expected:
        raise RuntimeError("cannot verify game asset ownership")


def remove_asset(kind, game, item):
    path = asset_path(kind, game, item)
    if not path.exists():
        return
    if path.is_mount():
        raise RuntimeError("unexpected mounted package directory")
    if not any(path.iterdir()):
        path.rmdir()  # Recovery after the final owner marker was removed.
        return
    marker(path, game, item)
    allowed = None
    if kind == "version":
        index = checked_dir(path, path / "validated.json")
        files = json.loads(index.read_text(encoding="utf-8"))["files"] if index.is_file() else {}
        if not isinstance(files, dict):
            raise RuntimeError("missing canonical version manifest")
        allowed = {".minigame-owner"}
        if index.is_file():
            allowed.update(("validated.json", "thumbnail.png", "upload.zip", "runtime"))
        for name in files:
            member = Path("runtime") / validation.clean_member(name)
            allowed.add(member.as_posix())
            allowed.update(p.as_posix() for p in member.parents if p != Path("."))
    for base, folders, files in os.walk(path, followlinks=False):
        for name in (*folders, *files):
            member = checked_dir(platform.game_root(), Path(base) / name)
            if member.is_mount() or (
                allowed is not None and member.relative_to(path).as_posix() not in allowed
            ):
                raise RuntimeError("unexpected entry in package directory")
            if (
                kind == "stage"
                and Path(base) == path
                and name not in {".minigame-owner", "upload.zip", "prepared"}
            ):
                raise RuntimeError("unexpected upload stage contents")
    # Keep the ownership proof and manifest until every payload file has gone, so interrupted
    # deletions can revalidate and retry on Windows where an in-use file can block unlink.
    retained = {path / ".minigame-owner"}
    if kind == "version":
        retained.add(path / "validated.json")
    for base, folders, files in os.walk(path, topdown=False, followlinks=False):
        for name in files:
            filename = Path(base) / name
            if filename not in retained:
                filename.unlink()
        for name in folders:
            (Path(base) / name).rmdir()
    if kind == "version":
        (path / "validated.json").unlink(missing_ok=True)
    (path / ".minigame-owner").unlink()
    path.rmdir()


def job_new(conn, job, game, version, user, kind, status, stage):
    conn.execute(
        """INSERT INTO mg_admin_jobs(id,game_id,version_id,actor,kind,status,stage,created)
                    VALUES (?,?,?,?,?,?,?,?)""",
        (job, game, version, platform.repository_actor_id(user), kind, status, stage, platform.now_ms()),
    )


def mark_preview_loaded(conn, game_id, version_id, actor, lease_id):
    """Called by the gateway only for a successful full preview main.html resource."""
    role, _, identity = str(actor).partition(":")
    if role not in ("student", "teacher") or not identity.isascii() or not identity.isdigit():
        return
    now = platform.now_ms()
    conn.execute("DELETE FROM mg_admin_preview_receipts WHERE loaded_at<=?", (now - 3600000,))
    conn.execute(
        """INSERT INTO mg_admin_preview_receipts(lease_id,game_id,version_id,actor,digest,loaded_at)
        SELECT l.lease_id,l.game_id,l.version_id,?,v.digest,? FROM mg_launch_leases l
        JOIN mg_versions v ON v.id=l.version_id JOIN mg_games g ON g.id=l.game_id
        WHERE l.game_id=? AND l.version_id=? AND l.lease_id=? AND l.actor=? AND l.purpose='preview'
          AND l.revoked=0 AND l.expires_at>? AND v.status IN ('ready','reviewing') AND g.status NOT IN ('deleting','deleted')
        ON CONFLICT(lease_id) DO NOTHING""",
        (platform.repository_actor_id({"role": role, "id": identity}), now, game_id, version_id, lease_id, actor, now),
    )


def impact(conn, game):
    from .minigame_runtime import impact_counts

    data = impact_counts(conn, game)
    return {
        **data,
        "versions": conn.execute("SELECT COUNT(*) FROM mg_versions WHERE game_id=?", (game,)).fetchone()[0],
        "bytes": int(
            conn.execute(
                "SELECT COALESCE(SUM(bytes_used),0) FROM mg_admin_assets WHERE game_id=?", (game,)
            ).fetchone()[0]
        ),
        "saves": data["playersWithProgress"],
        "runs": data["historyRows"],
    }


def mutate(user, game, action, body, key, vid=""):
    platform.repository_actor_id(user)
    if not isinstance(body, dict):
        error(422, "INVALID_BODY", "需要 JSON 对象。")
    op = "/".join((action, game, vid))
    digest = hashlib.sha256(validation.canonical(body).encode()).hexdigest()
    with platform.game_db() as conn:
        platform.db_start(conn, game)
        serial(conn)
        recheck_contributor(conn, user)
        row = platform.require_manager(conn, user, game) if game else None
        if action == "publish" and (user["role"] != "teacher" or not is_super_admin_teacher(conn, user["id"])):
            error(403, "MODERATION_REQUIRED", "请将版本提交给超管审核。")
        prev = receipt(conn, user, op, key, digest)
        if prev is not None:
            return prev
        if row:
            check_revision(row, body)
        now = platform.now_ms()
        if action == "create":
            game = uid()
            conn.execute(
                """INSERT INTO mg_games(id,owner_teacher_id,title,summary,status,created_at,updated_at)
                VALUES (?,?,?,'','draft',?,?)""",
                (game, platform.repository_actor_id(user), "新游戏", now, now),
            )
            if body:
                set_attributes(conn, user, one(conn, "mg_games", game), body)
        elif action == "patch":
            set_attributes(conn, user, row, body)
        elif action == "license":
            version = one(conn,"mg_versions",vid)
            if version["game_id"] != game or version["status"] in {"deleted","deleting"}:
                error(404,"NOT_FOUND","版本不存在。")
            if type(body.get("allowRemix")) is not bool:
                error(422,"INVALID_LICENSE","请明确选择是否允许二次开发。")
            authorship = conn.execute("SELECT author FROM mg_version_authorship WHERE version_id=?", (vid,)).fetchone()
            if not authorship or not authorship["author"]:
                cfg = json.loads(version["config_json"] or "{}")
                if not cfg.get("version"):
                    error(409,"VERSION_NOT_READY","等待版本清单识别后再修改授权。")
                owner_role = "teacher" if row["owner_teacher_id"] > 0 else "student"
                owner = platform.load_actor(conn, f"{owner_role}:{abs(row['owner_teacher_id'])}")
                author = cfg.get("author") or owner.get("display_name") or "作者"
                if authorship:
                    conn.execute("UPDATE mg_version_authorship SET author=? WHERE version_id=?", (author,vid))
                else:
                    repository.store_authorship(conn,vid,cfg,author,body,("",{}), json.loads(row["tags_json"]), "")
            conn.execute("UPDATE mg_version_authorship SET allow_remix=? WHERE version_id=?",
                         (int(body["allowRemix"]),vid))
            conn.execute("UPDATE mg_games SET revision=revision+1,updated_at=? WHERE id=?", (now,game))
        elif action == "submit-review":
            from . import minigame_moderation as moderation
            version = one(conn, "mg_versions", vid)
            if version["game_id"] != game or version["status"] in {"deleted", "deleting"}:
                error(404, "NOT_FOUND", "版本不存在或正在删除。")
            if version["status"] == "validating":
                error(409, "VERSION_NOT_READY", "版本仍在第一轮基础校验中，通过后将自动进入审核队列。")
            if version["status"] == "reviewing":
                error(409, "ALREADY_REVIEWING", "此版本已在审核队列中。")
            if version["status"] == "ready" and row["status"] == "listed" and row["current_version_id"] == vid:
                error(409, "ALREADY_LISTED", "此版本当前已上架。")
            prior = moderation.get_summary(conn, vid)
            if version["status"] == "rejected":
                if not prior:
                    error(409, "VERSION_NOT_READY", "该版本未通过技术校验，请修复并提升版本号后重新上传。")
                if prior["status"] == "rejected":
                    error(409, "REVIEW_REJECTED", "该版本内容审核未通过，请修改并提升版本号后重新上传。")
                if prior["status"] != "cancelled":
                    error(409, "VERSION_NOT_READY", "当前版本状态无法重新提交审核。")
            elif version["status"] not in {"ready", "retired"}:
                error(409, "VERSION_NOT_READY", "当前版本状态无法提交审核。")
            if version["status"] == "ready" and version_in_use(conn, vid):
                error(409, "VERSION_IN_USE", "此版本仍有进行中的对局，结束后再提交审核。")
            if not version["digest"] or not version["files_json"] or version["files_json"] == "{}":
                error(409, "VERSION_NOT_READY", "版本文件不完整，请重新上传。")
            folder = asset_path("version", game, vid)
            if not folder.is_dir() or not checked_dir(folder, folder / "validated.json").is_file():
                error(409, "VERSION_NOT_READY", "版本文件不可用，请重新上传。")
            marker(folder, game, vid)
            conn.execute("UPDATE mg_versions SET status='reviewing' WHERE id=? AND game_id=?", (vid, game))
            moderation.resubmit(conn, game, vid, version["digest"])
            conn.execute("UPDATE mg_games SET revision=revision+1,updated_at=? WHERE id=?", (now, game))
        elif action == "delete-version":
            from . import minigame_moderation as moderation
            version = one(conn,"mg_versions",vid)
            if version["game_id"] != game or version["status"] in {"deleted","deleting"}:
                error(404,"NOT_FOUND","版本不存在或正在删除。")
            withdraw_version(conn,row,vid,now)
            moderation.cancel(conn,game,vid)
            conn.execute("UPDATE mg_versions SET status='deleting' WHERE id=?", (vid,))
            conn.execute("""UPDATE mg_admin_jobs SET status='cancelled',token='',expires=0
                WHERE version_id=? AND kind='validate' AND status IN ('queued','running','retry','waiting_probe')""",(vid,))
            job = uid()
            job_new(conn,job,game,vid,user,"delete-version","queued","等待清理版本")
            result = {"gameId":game,"versionId":vid,"jobId":job,"status":"deleting"}
        elif action in {"publish", "retire", "preview", "playtest"}:
            version = one(conn, "mg_versions", vid or body.get("versionId", ""))
            if version["game_id"] != game:
                error(404, "NOT_FOUND", "版本不存在。")
            if action == "preview":
                archive_admin = (version["status"] in ("retired", "rejected") and user["role"] == "teacher"
                                 and is_super_admin_teacher(conn, user["id"]))
                available = platform.can_preview_version(version["status"], version["digest"], archive_admin)
            else:
                available = version["status"] == "ready" or (action == "playtest" and version["status"] == "reviewing")
            if not available:
                error(409, "VERSION_NOT_READY", "版本尚未完成隔离验收。")
            vid = version["id"]
            if action == "preview":
                result = {"url": f"/games/{row['slug'] or game}?previewVersion={vid}", "versionId": vid}
            elif action == "playtest":
                if type(body.get("passed")) is not bool:
                    error(422, "PLAYTEST_RESULT", "请选择试玩结果。")
                if not conn.execute(
                    """SELECT 1 FROM mg_admin_preview_receipts WHERE actor=? AND game_id=? AND
                    version_id=? AND digest=? AND loaded_at>? LIMIT 1""",
                    (platform.repository_actor_id(user), game, vid, version["digest"], now - 3600000),
                ).fetchone():
                    error(409, "PREVIEW_REQUIRED", "请先在预览页实际打开本版本，再登记试玩结果。")
                conn.execute(
                    "INSERT INTO mg_admin_playtests VALUES(?,?,?,?,?,?,?)",
                    (vid, game, platform.repository_actor_id(user), version["digest"], int(body["passed"]), now, uid()),
                )
                result = {"versionId": vid, "passed": body["passed"]}
            elif action == "publish":
                approved = conn.execute(
                    """SELECT 1 FROM mg_content_reviews WHERE version_id=? AND digest=? AND status='approved'
                       AND decider LIKE 'teacher:%'""", (vid, version["digest"]),
                ).fetchone()
                if not approved:
                    error(409, "MODERATION_REQUIRED", "请先在审核中心确认此版本。")
                platform.content_origins()
                publish_version(conn, row, version)
            else:
                withdraw_version(conn,row,vid,now)
                conn.execute("UPDATE mg_versions SET status='retired' WHERE id=?", (vid,))
        elif action in {"unlist", "delete"}:
            from .minigame_runtime import on_unlist
            from . import minigame_moderation as moderation

            if action == "unlist" and row["status"] != "listed":
                error(409, "NOT_LISTED", "只有已发布游戏可以下架。")
            if action == "delete":
                token = str(body.get("planToken", ""))
                plan = conn.execute(
                    """SELECT token FROM mg_admin_plans WHERE token=? AND actor=? AND game_id=?
                    AND revision=? AND expires>?""",
                    (hashlib.sha256(token.encode()).hexdigest(), platform.repository_actor_id(user), game, row["revision"], now),
                ).fetchone()
                if not plan or body.get("confirmationText") != (row["slug"] or "draft-" + game):
                    error(409, "DELETE_PLAN", "请重新预览删除影响并输入显示的游戏标识。")
                conn.execute("DELETE FROM mg_admin_plans WHERE game_id=?", (game,))
            on_unlist(conn, game)
            moderation.cancel(conn,game)
            conn.execute("""UPDATE mg_versions SET status='rejected' WHERE game_id=?
                         AND status IN ('reviewing','validating')""", (game,))
            conn.execute("""UPDATE mg_admin_jobs SET status='rejected',token='',expires=0,stage='已撤回'
                         WHERE game_id=? AND kind='validate' AND status IN
                         ('receiving','queued','running','retry','waiting_probe')""",(game,))
            conn.execute("UPDATE mg_launch_leases SET revoked=1 WHERE game_id=? AND revoked=0", (game,))
            status = "deleting" if action == "delete" else "unlisted"
            conn.execute(
                "UPDATE mg_games SET status=?,revision=revision+1,policy_revision=policy_revision+1,updated_at=? WHERE id=?",
                (status, now, game),
            )
            if action == "delete":
                job = uid()
                job_new(conn, job, game, "", user, "delete", "queued", "撤销访问")
                result = {"jobId": job, "status": "queued"}
        elif action == "suggest":
            result = suggestions.prepare(conn, row, user, body)
        elif action == "plan":
            token = secrets.token_urlsafe(32)
            expires = now + 5 * 60_000
            conn.execute("DELETE FROM mg_admin_plans WHERE expires<=?", (now,))
            conn.execute(
                "INSERT INTO mg_admin_plans VALUES(?,?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), platform.repository_actor_id(user), game, row["revision"], expires),
            )
            result = {
                "planToken": token,
                "expiresAt": expires,
                "confirmationText": row["slug"] or "draft-" + game,
                "impact": impact(conn, game),
            }
        else:
            raise ValueError(action)
        if action not in {"plan", "preview", "playtest", "delete", "delete-version", "suggest"}:
            result = {"game": object_game(one(conn, "mg_games", game))}
        store_receipt(conn, user, op, key, game, digest, result)
        conn.commit()
    return result


def withdraw_version(conn, row, vid, now):
    if row["current_version_id"] == vid:
        from .minigame_runtime import on_unlist
        on_unlist(conn,row["id"])
        conn.execute("UPDATE mg_launch_leases SET revoked=1 WHERE game_id=?", (row["id"],))
        conn.execute("""UPDATE mg_games SET status='unlisted',current_version_id=NULL,
                     policy_revision=policy_revision+1 WHERE id=?""",(row["id"],))
    elif version_in_use(conn, vid):
        error(409,"VERSION_IN_USE","此旧版仍有对局或预览在使用，结束后可下架或删除。")
    conn.execute("UPDATE mg_games SET revision=revision+1,updated_at=? WHERE id=?", (now,row["id"]))


def publish_version(conn, game, version):
    from .minigame_runtime import cancel_waiting
    gid, vid = game["id"], version["id"]
    if game["current_version_id"] != vid:
        verify_compatibility(conn, gid, game["current_version_id"], version)
        cancel_waiting(conn,gid)
    writeback = json.loads(version["writeback_json"])
    cfg = json.loads(version["config_json"])
    authorship = conn.execute("SELECT tags_json FROM mg_version_authorship WHERE version_id=?", (vid,)).fetchone()
    conn.execute("INSERT INTO mg_admin_releases VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                 (gid,vid,writeback["series"],writeback["saveVersion"]))
    conn.execute("""UPDATE mg_games SET current_version_id=?,status='listed',title=?,summary=?,tags_json=?,
                 revision=revision+1,policy_revision=policy_revision+?,updated_at=? WHERE id=?""",
                 (vid,cfg["title"],cfg["summary"],authorship["tags_json"] if authorship else game["tags_json"],
                  int(game["status"] != "listed"),platform.now_ms(),gid))


def publish_reviewed(conn, game_id, version_id):
    game = one(conn,"mg_games",game_id)
    version = one(conn,"mg_versions",version_id)
    if (game["status"] in {"deleted","deleting"} or
            version["game_id"] != game_id or version["status"] != "reviewing"):
        error(409,"REVIEW_STALE","此版本的审查已被取消。")
    if game["current_version_id"] and game["status"] == "listed":
        current = one(conn,"mg_versions",game["current_version_id"])
        if repository.release_rank(current["release_version"]) >= repository.release_rank(version["release_version"]):
            conn.execute("UPDATE mg_versions SET status='ready' WHERE id=?",(version_id,))
            return
    publish_version(conn,game,version)
    conn.execute("UPDATE mg_versions SET status='ready' WHERE id=?", (version_id,))


def version_in_use(conn, vid):
    return bool(
        conn.execute(
            """SELECT 1 FROM mg_rt_activity WHERE version=? AND phase IN
        ('waiting','countdown','running','reconnecting','paused') UNION ALL
        SELECT 1 FROM mg_launch_leases WHERE version_id=? AND revoked=0 AND expires_at>? LIMIT 1""",
            (vid, vid, platform.now_ms()),
        ).fetchone()
    )


def download_source(user, game_id, version_id=None):
    with platform.game_db() as conn:
        platform.assert_session(conn, user)
        item = repository.source_info(conn, user, game_id, version_id)
        vid = item["version_id"]
    return package_download(game_id, vid, item["slug"], item["release_version"])


def download_managed_version(user, game_id, vid):
    with platform.game_db() as conn:
        platform.assert_session(conn, user)
        game = platform.require_manager(conn, user, game_id)
        version = conn.execute("""SELECT release_version FROM mg_versions WHERE game_id=? AND id=?
            AND status IN ('ready','reviewing','retired','rejected') AND digest<>''""", (game_id, vid)).fetchone()
        if not version or game["status"] in ("deleted", "deleting"):
            error(404, "SOURCE_UNAVAILABLE", "当前版本的原包不可用。")
        return package_download(game_id, vid, game["slug"], version["release_version"])


def package_download(game_id, vid, slug, release_version):
    from ..minigame_content import GameFileResponse

    directory = asset_path("version",game_id,vid)
    if not directory.is_dir():
        error(404, "SOURCE_UNAVAILABLE", "此版本的游戏包不可用。")
    marker(directory,game_id,vid)
    path = checked_dir(directory,directory/"upload.zip")
    if not path.is_file():
        error(404,"SOURCE_UNAVAILABLE","此版本的游戏包不可用。")
    return GameFileResponse(game_id,path,media_type="application/zip",
                            filename=f"{slug}-{release_version}.zip",
                            headers={"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff",
                                     "Referrer-Policy":"no-referrer"})


def verify_compatibility(conn, game, current_id, new):
    after = json.loads(new["writeback_json"])
    series = (game, after["series"], after["saveVersion"])
    releases = conn.execute(
        """SELECT v.id,v.writeback_json FROM mg_versions v
        WHERE v.game_id=? AND (v.id=? OR EXISTS (SELECT 1 FROM mg_admin_releases r
            WHERE r.game_id=? AND r.series=? AND r.save_version=? AND r.version_id=v.id))""",
        (game, current_id, *series),
    ).fetchall()
    known = conn.execute(
        "SELECT 1 FROM mg_admin_releases WHERE game_id=? AND version_id=?", (game, new["id"])
    ).fetchone()
    marks = [(p["id"], p["resumeState"]) for p in after["milestones"]]
    fields = after["privateFields"]
    for release in releases:
        before = json.loads(release["writeback_json"])
        if release["id"] == new["id"] or (game, before["series"], before["saveVersion"]) != series:
            continue
        previous = [(p["id"], p["resumeState"]) for p in before["milestones"]]
        common = min(len(marks), len(previous))
        removed = sorted(before["privateFields"].keys() - fields.keys())
        if marks[:common] != previous[:common] or any(
            fields[k] != before["privateFields"][k] for k in fields.keys() & before["privateFields"].keys()
        ):
            error(409, "SAVE_INCOMPATIBLE", "已有里程碑或私档字段定义不同，请另启存档谱系。")
        if not removed and len(marks) >= len(previous):
            continue
        if not known:
            error(409, "SAVE_INCOMPATIBLE", "同谱系新版本须保留已发布的字段与里程碑。")
        if conn.execute(
            """SELECT 1 FROM mg_rt_activity WHERE game=? AND version=?
            AND phase IN ('running','reconnecting') LIMIT 1""",
            (game, release["id"]),
        ).fetchone():
            error(409, "DRAIN_OLD_RUNS", "请等待使用较新协议的对局结束后再回滚。")
        rank = conn.execute(
            "SELECT MAX(rank) FROM mg_rt_progress WHERE game=? AND series=? AND save_ver=?", series
        ).fetchone()[0]
        if rank is not None and rank >= len(marks):
            error(409, "SAVE_INCOMPATIBLE", f"已保存的最高进度为 {rank}，目标版本仅支持至 {len(marks) - 1}。")
        if removed:
            keys = (
                "jsonb_object_keys(CAST(p.priv AS jsonb)) AS f(key)"
                if platform.get_configured_db_engine() == "postgres"
                else "json_each(p.priv) AS f"
            )
            placeholders = ",".join("?" for _ in removed)
            if conn.execute(
                f"""SELECT 1 FROM mg_rt_progress p WHERE game=? AND series=? AND save_ver=?
                AND EXISTS (SELECT 1 FROM {keys} WHERE f.key IN ({placeholders})) LIMIT 1""",
                (*series, *removed),
            ).fetchone():
                error(409, "SAVE_INCOMPATIBLE", "已有玩家保存了目标版本缺少的字段：" + "、".join(removed))


@contextmanager
def disk_lock(key):
    """OS-owned locks expire when the process exits; fixed stripes never name customer assets."""
    lockpath = checked_dir(platform.game_root(), platform.game_root() / "locks" / key)
    lockpath.parent.mkdir(parents=True, exist_ok=True)
    with lockpath.open("a+b") as handle:
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            error(503, "ASSET_BUSY", "文件任务仍在执行，请稍后重试。")
        yield


def game_lock(game):
    return disk_lock("game-" + hashlib.sha256(game.encode()).hexdigest()[:2])


def receive_upload(user, game, rev, key, stream):
    platform.repository_actor_id(user)
    if not KEY.fullmatch(key or ""):
        error(422, "REQUEST_KEY", "缺少有效的上传请求键。")
    with game_lock(game):
        return _receive_locked(user, game, rev, key, stream)


def import_package(user, key, stream, options):
    if not KEY.fullmatch(key or ""):
        error(422, "REQUEST_KEY", "缺少有效的上传请求键。")
    config, package_sha = repository.read_manifest(stream)
    slug = config["gameKey"]
    lock_key = "import-" + hashlib.sha256(slug.encode("ascii")).hexdigest()[:2]
    with disk_lock(lock_key):
        with platform.game_db() as conn:
            row = conn.execute("SELECT id,owner_teacher_id,revision FROM mg_games WHERE slug=?", (slug,)).fetchone()
            if row and row["owner_teacher_id"] != platform.repository_actor_id(user) and not options.get("targetGameId"):
                error(409, "GAME_KEY_TAKEN", "此 gameKey 已登记；改编作品请使用新的 gameKey。")
            if target := options.get("targetGameId"):
                folder = platform.require_manager(conn, user, target)
                if row and row["id"] != target or folder["slug"] not in (None, slug):
                    error(409, "GAME_KEY", "文件夹与 config.json 的 gameKey 不一致。")
                row = folder
            game = row["id"] if row else uid()
            revision = options.get("expectedRevision")
        with game_lock(game):
            return _receive_locked(user, game, revision, key, stream, imported=(config, package_sha, options))


def _receive_locked(user, game, rev, key, stream, *, imported=None):
    stage, vid = uid(), uid()
    op = "repository/import" if imported else "upload/" + game
    created_game = False
    if imported:
        config, package_sha, options = imported
        checksum = hashlib.sha256(validation.canonical([package_sha, options]).encode()).hexdigest()
    with platform.game_db() as conn:
        platform.db_start(conn, game)
        serial(conn)
        recheck_contributor(conn, user)
        if imported:
            if prev := receipt(conn, user, op, key, checksum):
                accepted = conn.execute("SELECT status FROM mg_versions WHERE id=?", (prev["versionId"],)).fetchone()
                if not accepted or accepted["status"] in {"deleting", "deleted"}:
                    error(410, "VERSION_REMOVED", "此次提交的版本已删除。")
                return prev
            created_game = not conn.execute("SELECT 1 FROM mg_games WHERE id=?", (game,)).fetchone()
            source = repository.import_attribution(conn, user, None if created_game else game, config,
                                                   options.get("sourceVersionId", ""))
            if created_game:
                if conn.execute("SELECT 1 FROM mg_games WHERE slug=?", (config["gameKey"],)).fetchone():
                    error(409, "GAME_KEY_TAKEN", "作品标识刚刚被登记，请刷新后重试。")
                now = platform.now_ms()
                conn.execute(
                    """INSERT INTO mg_games(id,slug,owner_teacher_id,title,summary,status,scope,created_at,updated_at)
                       VALUES (?,?,?,?,?,'draft',?,?,?)""",
                    (game,config["gameKey"],platform.repository_actor_id(user),config["title"],config["summary"],
                     "public" if user["role"] == "student" else "private",now,now),
                )
        row = platform.require_manager(conn, user, game)
        old = conn.execute(
            "SELECT 1 FROM mg_admin_receipts WHERE actor=? AND op=? AND key_value=?",
            (platform.repository_actor_id(user), op, key),
        ).fetchone()
        if row["status"] in {"deleting", "deleted"}:
            error(409, "GAME_REMOVED", "游戏已删除或正在清理。")
        if imported:
            supplied_tags = options.get("tags")
            tags = repository.check_tags(supplied_tags if supplied_tags is not None else json.loads(row["tags_json"]))
            tags = repository.check_tags(list(dict.fromkeys([*source[1].get("tags", []), *tags])))
            duplicate = conn.execute(
                """SELECT v.id,v.status,a.package_sha256,a.tags_json,a.source_version_id,a.allow_remix,
                   j.id AS job_id FROM mg_versions v LEFT JOIN mg_version_authorship a ON a.version_id=v.id
                   LEFT JOIN mg_admin_jobs j ON j.version_id=v.id AND j.kind='validate'
                   WHERE v.game_id=? AND v.release_version=? AND v.status<>'rejected'
                   ORDER BY v.created_at DESC,j.created ASC LIMIT 1""", (game,config["version"])
            ).fetchone()
            if duplicate:
                if (duplicate["status"] in {"deleted","deleting","retired"} or
                        duplicate["package_sha256"] != package_sha or duplicate["source_version_id"] != source[0] or
                        json.loads(duplicate["tags_json"] or "[]") != tags or
                        bool(duplicate["allow_remix"]) != options["allowRemix"]):
                    error(409, "VERSION_CONFLICT", "同一作品的已登记版本不可覆盖，请提高 config.json 的 version。")
                result = {"gameId":game, "versionId":duplicate["id"], "jobId":duplicate["job_id"],
                          "status":duplicate["status"]}
                store_receipt(conn,user,op,key,game,checksum,result)
                conn.commit()
                return result
            if rev is None:
                rev = row["revision"]
        if not old:
            check_revision(row, {"expectedRevision": rev})
            usage = conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN g.owner_teacher_id=? THEN a.bytes_used ELSE 0 END),0) AS owned,
                COALESCE(SUM(a.bytes_used),0) AS total FROM mg_admin_assets a JOIN mg_games g ON g.id=a.game_id""",
                (row["owner_teacher_id"],),
            ).fetchone()
            # The original and the immutable copy overlap until directory promotion completes.
            reserve = 2 * validation.MAX_ZIP + validation.MAX_TOTAL + 16 * validation.MIB
            quota = int(os.environ.get("MINIGAMES_TEACHER_QUOTA_MIB", "2048")) * validation.MIB
            if int(usage["owned"]) + reserve > quota:
                error(413, "STORAGE_QUOTA", "此账号的游戏包存储额度不足。")
            budget = int(os.environ.get("MINIGAMES_STORAGE_BUDGET_MIB", "768")) * validation.MIB
            if int(usage["total"]) + reserve > budget:
                error(507, "PLATFORM_STORAGE_QUOTA", "小游戏平台的存储额度暂时不足，请稍后重试或清理旧游戏。")
            conn.execute("INSERT INTO mg_admin_assets VALUES(?,?,?,?)", (game, "stage", stage, reserve))
            job_new(conn, stage, game, vid, user, "validate", "receiving", "接收文件")
            conn.execute(
                "UPDATE mg_admin_jobs SET expires=? WHERE id=?", (platform.now_ms() + WORKER_TTL, stage)
            )
        conn.commit()
    path = asset_path("stage", game, stage)
    queued = False
    try:
        if not old:
            marker(path, game, stage, create=True)
            if shutil.disk_usage(path).free < reserve + 32 * validation.MIB:
                error(503, "DISK_FULL", "当前可用磁盘不足，请稍后重试。")
        digest, size = hashlib.sha256(), 0
        with nullcontext(None) if old else (path / "upload.zip").open("xb") as out:
            while chunk := stream.read(256 * 1024):
                size += len(chunk)
                if size > validation.MAX_ZIP:
                    error(413, "ZIP_QUOTA", "ZIP 超过 64 MiB。")
                digest.update(chunk)
                if out is not None:
                    out.write(chunk)
        if imported:
            if digest.hexdigest() != package_sha:
                error(409, "PACKAGE_CHANGED", "接收的游戏包与清单读取时不一致，请重传。")
        else:
            checksum = hashlib.sha256(f"{rev}:{digest.hexdigest()}".encode()).hexdigest()
        with platform.game_db() as conn:
            platform.db_start(conn, game)
            serial(conn)
            recheck_contributor(conn, user)
            row = platform.require_manager(conn, user, game)
            if prev := receipt(conn, user, op, key, checksum):
                return prev
            if old:
                error(409, "RECEIPT_EXPIRED", "上传状态已变化，请刷新后重试。")
            check_revision(row, {"expectedRevision": rev})
            if imported and repository.import_attribution(conn, user, game, config, options.get("sourceVersionId", "")) != source:
                error(409, "SOURCE_CHANGED", "原作授权信息已变化，请核对后重试。")
            conn.execute(
                "INSERT INTO mg_versions(id,game_id,status,created_at) VALUES(?,?,'validating',?)",
                (vid, game, platform.now_ms()),
            )
            if imported:
                conn.execute("UPDATE mg_versions SET release_version=?,config_json=? WHERE id=?",
                             (config["version"],validation.canonical(config),vid))
                conn.execute("UPDATE mg_games SET slug=? WHERE id=? AND slug IS NULL", (config["gameKey"],game))
                repository.store_authorship(conn,vid,config,user.get("display_name") or user.get("name") or "作者",
                                            options,source,tags,package_sha)
                if not row["current_version_id"]:
                    conn.execute("UPDATE mg_games SET title=?,summary=?,tags_json=?,updated_at=? WHERE id=?",
                                 (config["title"],config["summary"],validation.canonical(tags),platform.now_ms(),game))
                conn.execute("UPDATE mg_games SET revision=revision+1,updated_at=? WHERE id=?", (platform.now_ms(),game))
            conn.execute(
                "UPDATE mg_admin_jobs SET status='queued',stage='待校验',expires=0 WHERE id=?", (stage,)
            )
            result = {"gameId": game, "versionId": vid, "jobId": stage, "status": "queued"}
            store_receipt(conn, user, op, key, game, checksum, result)
            conn.commit()
        queued = True
        return result
    finally:
        if not old and not queued:
            remove_asset("stage", game, stage)
            with platform.game_db() as conn:
                platform.db_start(conn, game)
                conn.execute("DELETE FROM mg_admin_assets WHERE kind='stage' AND asset_id=?", (stage,))
                conn.execute("DELETE FROM mg_admin_jobs WHERE id=? AND status='receiving'", (stage,))
                if created_game:
                    conn.execute("""DELETE FROM mg_games WHERE id=? AND status='draft'
                                 AND NOT EXISTS(SELECT 1 FROM mg_versions WHERE game_id=?)""",(game,game))
                conn.commit()


def probe_configured():
    return bool(os.getenv("MINIGAMES_PROBE_COMMAND", "").strip())


def probe_package(runtime, digest):
    """Call a trusted operator-provided sandbox launcher, never execute the uploaded code in FastAPI."""
    raw = os.getenv("MINIGAMES_PROBE_COMMAND", "").strip()
    if not raw:
        error(503, "PROBE_NOT_CONFIGURED", "静态检查已通过，启用浏览器验收后自动继续。")
    try:
        argv = json.loads(raw)
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(x, str) for x in argv)
            or not Path(argv[0]).is_absolute()
        ):
            raise ValueError("invalid command")
        # The wrapper receives a private, generated directory as a separate argument and mounts it read-only.
        # Only OS process launch variables survive. No database, model or web session secret is inherited.
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "TMPDIR",
                "PATH",
                "PROGRAMFILES",
                "PROGRAMFILES(X86)",
                "LOCALAPPDATA",
                "USERPROFILE",
                "APPDATA",
                "PROGRAMDATA",
                "ALLUSERSPROFILE",
                "SYSTEMDRIVE",
                "COMSPEC",
                "HOMEDRIVE",
                "HOMEPATH",
            }
        }
        synthetic = "--synthetic-digest" in argv
        proc = subprocess.run(
            [*argv, str(runtime), digest],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if synthetic else subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        if proc.stderr and synthetic:
            log.warning("Reviewed synthetic probe failed: %s", proc.stderr.decode(errors="replace")[-2400:])
        if proc.returncode or len(proc.stdout) > 16384:
            raise ValueError("probe failed")
        report = json.loads(proc.stdout)
        if report.get("digest") != digest:
            raise ValueError("wrong digest")
        problem = report.get("resourceError")
        if problem:
            names = {
                "ASSET_LOAD": "浏览器无法从包内地址加载素材。",
                "ASSET_BYTES": "浏览器读取到的素材字节数与清单不符。",
                "IMAGE_BROWSER_DECODE": "浏览器无法解码图片。",
                "IMAGE_PIXEL_ACCESS": "图片已加载，但浏览器无法读取画布像素。",
                "FONT_DECODE": "浏览器无法解码 WOFF2 字体。",
                "AUDIO_DECODE": "浏览器无法读取音频元数据或解码音频样本。",
                "AUDIO_UNSUPPORTED": "当前目标浏览器不支持此音频容器格式。",
                "VIDEO_DECODE": "浏览器无法解码视频首帧。",
                "VIDEO_UNSUPPORTED": "当前目标浏览器不支持此视频容器格式。",
                "LEVEL_JSON_DECODE": "浏览器无法以 UTF-8 解析关卡 JSON。",
                "ASSET_DECODE": "浏览器无法解码此素材。",
                "ASSET_PROBE_BUDGET": "素材解码超过隔离执行器的资源预算，正在等待重试。",
            }
            code, name, mode = problem["code"], problem["file"], problem["field"]
            if (
                code not in names
                or mode not in ("platform", "offline")
                or not isinstance(name, str)
                or not name.startswith("static/")
                or "\\" in name
                or any(part in {".", "..", ""} for part in name.split("/"))
                or not (runtime / name).resolve().is_relative_to((runtime / "static").resolve())
                or not (runtime / name).is_file()
            ):
                raise ValueError("invalid resource evidence")
            if code == "ASSET_PROBE_BUDGET":
                raise HTTPException(
                    503, detail={"code": code, "message": names[code], "file": name, "field": mode}
                )
            validation.fail(code, names[code], name, mode)
        if report.get("status") == "invalid":
            checks = [
                s for s in report.get("checks", []) if isinstance(s, str) and re.fullmatch(r"[a-z-]{1,32}", s)
            ]
            if not report.get("platform"):
                checks.append("platform-start")
            if not report.get("offline"):
                checks.append("offline-start")
            validation.fail(
                "BROWSER_SMOKE", "请在平台预览与离线入口复查启动过程。", "main.html", ", ".join(checks[:6])
            )
        if (
            report.get("status") != "passed"
            or report.get("platform") is not True
            or report.get("offline") is not True
        ):
            raise ValueError("missing evidence")
        isolation = report.get("isolation")
        if isolation != "isolated-worker" and not synthetic:
            raise ValueError("isolated worker proof missing")
        counts = report.get("resourceCounts")
        expected = sum(1 for path in (runtime / "static").rglob("*") if path.is_file())
        if not isinstance(counts, dict) or any(
            type(counts.get(k)) is not int or counts[k] != expected for k in ("total", "platform", "offline")
        ):
            raise ValueError("incomplete resource coverage")
        return {
            "digest": digest,
            "platform": True,
            "offline": True,
            "isolation": isolation,
            "resourceCounts": counts,
        }
    except validation.InvalidPackage:
        raise
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        error(503, "PROBE_UNAVAILABLE", "验收执行器未就绪或超时，系统将保留作业重试。")


def finish_job(conn, job, status, stage, problem=None, result=None, due=0):
    changed = conn.execute(
        """UPDATE mg_admin_jobs SET status=?,stage=?,error_json=?,result_json=?,due=?,expires=0,
        token='' WHERE id=? AND token=?""",
        (
            status,
            stage,
            validation.canonical(problem or {}),
            validation.canonical(result or {}),
            due,
            job["id"],
            job["token"],
        ),
    ).rowcount
    if changed != 1:
        error(409, "STALE_CLAIM", "该作业已由其他执行器接管。")


def clear_stage(conn, job):
    conn.execute("DELETE FROM mg_admin_assets WHERE kind='stage' AND asset_id=?", (job["id"],))


@lru_cache(maxsize=1)
def static_validation_recipe():
    """Only reuse staging output produced by this validator, contracts and SDK."""
    recipe = hashlib.sha256()
    for source in (Path(validation.__file__), Path(__file__).with_name("minigame_runtime_rules.py"),
                   validation.SDK, *sorted(validation.SCHEMAS.glob("*.schema.json"))):
        recipe.update(hashlib.sha256(source.read_bytes()).digest())
    return recipe.hexdigest()


def account_prepared_stage(job, stage, path, meta, checkpoint_size):
    # The retained archive and its immutable copy overlap briefly during promotion.
    reserve = (
        2 * (stage / "upload.zip").stat().st_size + sum(f["size"] for f in meta["files"].values())
        + (path / "thumbnail.png").stat().st_size + 2 * checkpoint_size + 128 * 1024
    )
    with platform.game_db() as conn:
        conn.execute("UPDATE mg_admin_assets SET bytes_used=? WHERE kind='stage' AND asset_id=?",
                     (reserve, job["id"]))
        conn.commit()


def prepare_static(job, stage, path):
    checkpoint = path / "static-check.json"
    if path.exists():
        marker(path, job["game_id"], job["version_id"])
        if checked_dir(path, checkpoint).is_file():
            try:
                saved = json.loads(checkpoint.read_text(encoding="utf-8"))
                if saved["recipe"] == static_validation_recipe() and isinstance(saved["meta"], dict):
                    account_prepared_stage(job, stage, path, saved["meta"], checkpoint.stat().st_size)
                    return saved["meta"]
            except (KeyError, TypeError, ValueError):
                pass
        # An interrupted or superseded static check is rebuilt from the retained ZIP.
        shutil.rmtree(path)
    marker(path, job["game_id"], job["version_id"], create=True)
    meta = validation.validate_package(stage / "upload.zip", path)
    serialized = validation.canonical({"recipe": static_validation_recipe(), "meta": meta}).encode()
    partial = path / "static-check.partial"
    partial.write_bytes(serialized)
    partial.replace(checkpoint)
    account_prepared_stage(job, stage, path, meta, len(serialized))
    return meta


def validate_job(job, probe_blocked_until=0):
    game, vid = job["game_id"], job["version_id"]
    stage, target = asset_path("stage", game, job["id"]), asset_path("version", game, vid)
    marker(stage, game, job["id"])
    path = stage / "prepared"
    if target.exists():
        marker(target, game, vid)
        meta = json.loads((target / "validated.json").read_text(encoding="utf-8"))
    else:
        meta = prepare_static(job, stage, path)
        with platform.game_db() as conn:
            platform.db_start(conn, game)
            serial(conn)
            owner = one(conn, "mg_games", game)
            cfg = meta["config"]
            if owner["slug"] not in (None, cfg["gameKey"]):
                validation.fail("GAME_KEY", "上传版本的标识与作品不符。", "config.json", "gameKey")
            conn.execute("""UPDATE mg_games SET title=?,summary=? WHERE id=? AND current_version_id IS NULL""",
                         (cfg["title"],cfg["summary"],game))
            conn.execute("""UPDATE mg_games SET slug=? WHERE id=? AND slug IS NULL
                         AND NOT EXISTS (SELECT 1 FROM mg_games WHERE slug=?)""",
                         (cfg["gameKey"], game, cfg["gameKey"]))
            conn.execute("UPDATE mg_versions SET config_json=?,release_version=? WHERE id=? AND status='validating'",
                         (validation.canonical(cfg),cfg["version"],vid))
            conn.commit()
        if owner["owner_teacher_id"] < 0:
            meta["probe"] = {"staticValidation": "passed", "review": "queued"}
        elif not probe_configured():
            meta["probe"] = {"staticValidation": "passed", "browserProbe": "skipped_unconfigured"}
        else:
            if probe_blocked_until > platform.now_ms():
                error(503, "PROBE_UNAVAILABLE", PROBE_OFFLINE_STAGE)
            meta["probe"] = probe_package(path / "runtime", meta["digest"])
        shutil.copyfile(stage / "upload.zip", path / "upload.zip")
        (path / "validated.json").write_text(validation.canonical(meta), encoding="utf-8")
        (path / "static-check.json").unlink()
    config = meta["config"]
    with platform.game_db() as conn:
        platform.db_start(conn, game)
        serial(conn)
        current = one(conn, "mg_games", game)
        if current["status"] in ("deleting", "deleted"):
            validation.fail("GAME_REMOVED", "游戏已进入清理流程。")
        if current["slug"] and current["slug"] != config["gameKey"]:
            validation.fail("GAME_KEY", "新版本 gameKey 与现有游戏不同。", "config.json", "gameKey")
        if conn.execute(
            "SELECT 1 FROM mg_games WHERE slug=? AND id<>?", (config["gameKey"], game)
        ).fetchone():
            validation.fail("GAME_KEY", "此 gameKey 已绑定其他游戏。", "config.json", "gameKey")
        old = conn.execute(
            """SELECT id,digest,status FROM mg_versions WHERE game_id=? AND release_version=? AND id<>?
            AND status IN ('ready','reviewing','retired','deleting','deleted')""", (game,config["version"],vid)
        ).fetchone()
        if one(conn,"mg_versions",vid)["status"] != "validating":
            return
        if old:
            if old["digest"] != meta["digest"] or old["status"] in {"deleted","deleting"}:
                validation.fail(
                    "VERSION_CONFLICT", "已验收的发行版本号不能覆盖，请更换版本号。", "config.json", "version"
                )
            conn.execute("DELETE FROM mg_versions WHERE id=? AND status='validating'", (vid,))
            conn.execute("UPDATE mg_admin_jobs SET version_id=? WHERE id=?", (old["id"], job["id"]))
            finish_job(conn, job, "succeeded", "已验收相同内容", result={"versionId": old["id"]})
        else:
            # A generated, verified directory is moved without copying bytes inside the transaction.
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    path.rename(target)
                except PermissionError as exc:
                    if getattr(exc, "winerror", None) not in (5, 32, 33):
                        raise
                    error(503, "ASSET_BUSY", "暂存目录正被占用，验收任务将保留并自动重试。")
            physical = sum(x["size"] for x in meta["files"].values()) + (target / "upload.zip").stat().st_size
            physical += (target / "thumbnail.png").stat().st_size + (target / "validated.json").stat().st_size
            conn.execute(
                """INSERT INTO mg_admin_assets VALUES(?,?,?,?) ON CONFLICT(kind,asset_id)
                DO UPDATE SET bytes_used=excluded.bytes_used""",
                (game, "version", vid, physical),
            )
            platform.index_version_files(conn, vid, meta["files"])
            if not conn.execute("SELECT 1 FROM mg_version_authorship WHERE version_id=?", (vid,)).fetchone():
                role = "teacher" if current["owner_teacher_id"] > 0 else "student"
                owner = platform.load_actor(conn, f"{role}:{abs(current['owner_teacher_id'])}")
                repository.store_authorship(conn, vid, config, owner.get("display_name") or owner.get("name") or "作者",
                                            {"allowRemix": False}, ("", {}), json.loads(current["tags_json"]), "")
            conn.execute(
                """UPDATE mg_versions SET status=?,release_version=?,digest=?,config_json=?,
                writeback_json=?,rules_json=?,files_json=? WHERE id=? AND status='validating'""",
                (
                    "reviewing",
                    config["version"],
                    meta["digest"],
                    validation.canonical(config),
                    validation.canonical(meta["writeback"]),
                    validation.canonical(meta["rules"]),
                    validation.canonical(meta["files"]),
                    vid,
                ),
            )
            conn.execute(
                """UPDATE mg_games SET slug=?, title=CASE WHEN current_version_id IS NULL THEN ? ELSE title END,
                summary=CASE WHEN current_version_id IS NULL THEN ? ELSE summary END,revision=revision+1,updated_at=? WHERE id=?""",
                (config["gameKey"], config["title"], config["summary"], platform.now_ms(), game),
            )
            from . import minigame_moderation as moderation
            enqueued_status = moderation.enqueue(conn, game, vid, meta["digest"])
            review_stage = "基础校验通过，已提交 AI 风险查验" if enqueued_status == "queued" else "基础校验通过，等待超管审核"
            finish_job(
                conn, job, "succeeded", review_stage,
                result={"versionId": vid, "probe": meta["probe"]}
            )
        conn.commit()
    remove_asset("stage", game, job["id"])
    with platform.game_db() as conn:
        clear_stage(conn, job)
        conn.commit()


def delete_job(job):
    game = job["game_id"]
    with platform.game_db() as conn:
        assets = conn.execute("SELECT kind,asset_id FROM mg_admin_assets WHERE game_id=?", (game,)).fetchall()
        versions = conn.execute("SELECT id FROM mg_versions WHERE game_id=?", (game,)).fetchall()
    # A successful rename may have preceded an interrupted DB commit.
    owned = {(r["kind"], r["asset_id"]) for r in assets} | {("version", r["id"]) for r in versions}
    for kind, identifier in sorted(owned):
        remove_asset(kind, game, identifier)
    parent = checked_dir(platform.game_root(), platform.game_root() / "versions" / game)
    if parent.exists():
        parent.rmdir()  # Nonempty unknown contents require an operator to check ownership.
    with platform.game_db() as conn:
        platform.db_start(conn, game)
        from .minigame_runtime import purge_game

        purge_game(conn, game)
        conn.execute("DELETE FROM mg_launch_leases WHERE game_id=?", (game,))
        for table in (
            "mg_heat_players", "mg_heat_days", "mg_heat_windows", "mg_heat_games",
            "mg_admin_assets",
            "mg_admin_plans",
            "mg_admin_playtests",
            "mg_admin_preview_receipts",
            "mg_admin_releases",
            "mg_versions",
        ):
            conn.execute(f"DELETE FROM {table} WHERE game_id=?", (game,))
        conn.execute("DELETE FROM mg_admin_jobs WHERE game_id=? AND id<>?", (game, job["id"]))
        conn.execute("DELETE FROM mg_admin_receipts WHERE game_id=? AND op NOT LIKE 'delete/%'", (game,))
        conn.execute(
            """UPDATE mg_games SET slug=NULL,title='',summary='',tags_json='[]',targets_json='[]',
            scope='private',org_json='{}',featured=0,status='deleted',current_version_id=NULL,
            updated_at=? WHERE id=? AND status='deleting'""",
            (platform.now_ms(), game),
        )
        finish_job(conn, job, "succeeded", "已彻底删除", result={"gameId": game})
        conn.commit()


def delete_version_job(job):
    """Remove only the target version's owned bytes; preserve shared progress contracts."""
    game,vid = job["game_id"],job["version_id"]
    with platform.game_db() as conn:
        stages = [r[0] for r in conn.execute(
            "SELECT id FROM mg_admin_jobs WHERE game_id=? AND version_id=? AND kind='validate'",(game,vid)
        ).fetchall()]
    for stage in stages:
        remove_asset("stage",game,stage)
    remove_asset("version",game,vid)
    with platform.game_db() as conn:
        platform.db_start(conn,game)
        serial(conn)
        from . import minigame_moderation as moderation
        moderation.cancel(conn,game,vid)
        conn.execute("DELETE FROM mg_admin_assets WHERE kind='version' AND asset_id=?", (vid,))
        for stage in stages:
            conn.execute("DELETE FROM mg_admin_assets WHERE kind='stage' AND asset_id=?", (stage,))
        for table in ("mg_version_files","mg_admin_playtests","mg_admin_preview_receipts","mg_launch_leases"):
            conn.execute(f"DELETE FROM {table} WHERE version_id=?", (vid,))
        conn.execute("DELETE FROM mg_version_authorship WHERE version_id=? AND source_version_id=''", (vid,))
        # The remaining lineage snapshot belongs to the work. It survives the removal of its last edition.
        conn.execute("""UPDATE mg_version_authorship SET author='',allow_remix=0,tags_json='[]',package_sha256=''
                     WHERE version_id=?""", (vid,))
        conn.execute("DELETE FROM mg_admin_jobs WHERE game_id=? AND version_id=? AND kind='validate'", (game,vid))
        # Save-version compatibility still consults the schema of previously published versions.
        conn.execute("""UPDATE mg_versions SET status='deleted',config_json='{}',rules_json=NULL,files_json='{}',
                     digest=NULL WHERE id=? AND game_id=?""", (vid,game))
        finish_job(conn,job,"succeeded","版本文件已删除",result={"gameId":game,"versionId":vid})
        conn.commit()


def deletion_register(job):
    """Keep a content-free, write-once operator ledger separate from package directories."""
    root = Path(
        os.getenv("MINIGAMES_DELETION_REGISTER")
        or (platform.game_root().parent / "audit" / "minigame-deletions")
    )
    root.mkdir(parents=True, exist_ok=True)
    path = root / (job["id"] + ".json")
    expected = {
        "gameId": job["game_id"],
        "jobId": job["id"],
        "actor": job["actor"],
        "createdAt": job["created"],
    }
    if dict(job).get("kind") == "delete-version":
        expected["versionId"] = job["version_id"]
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != expected:
            raise RuntimeError("deletion register mismatch")
        return
    temporary = root / (job["id"] + ".partial")
    with temporary.open("w", encoding="utf-8") as out:
        out.write(validation.canonical(expected))
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)
    if os.name != "nt":
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def record_job_failure(job, status, issue, delay=0):
    if status == "rejected" and job["kind"] == "validate":
        issue = {**issue, "repairHint": suggestions.repair_hint(issue["code"])}
    with platform.game_db() as conn:
        platform.db_start(conn, job["game_id"])
        if status == "rejected":
            conn.execute(
                "UPDATE mg_versions SET status='rejected' WHERE id=? AND status='validating'",
                (job["version_id"],),
            )
        finish_job(
            conn,
            job,
            status,
            "校验未通过" if status == "rejected" else "待重试",
            issue,
            due=platform.now_ms() + delay,
        )
        conn.commit()
    if status == "rejected":
        remove_asset("stage", job["game_id"], job["id"])
        with platform.game_db() as conn:
            clear_stage(conn, job)
            conn.commit()


def worker_once():
    with disk_lock("validation-worker"):
        now = platform.now_ms()
        with platform.game_db() as conn:
            probe_enabled = probe_configured()
            blocked_until = 0
            if probe_enabled:
                blocked_until = conn.execute(
                    "SELECT COALESCE(MAX(due),0) FROM mg_admin_jobs WHERE status='waiting_probe' AND due>?",
                    (now,),
                ).fetchone()[0]
                pending = "'queued','retry','waiting_probe'" if not blocked_until else "'queued','retry'"
                condition = f"""kind<>'suggest' AND ((status IN ({pending}) AND due<=?) OR
                    (status IN ('running','receiving') AND expires<=?))"""
            else:
                condition = """kind<>'suggest' AND ((status IN ('queued','retry') AND due<=?) OR
                    status='waiting_probe' OR
                    (status IN ('running','receiving') AND expires<=?))"""
            if not conn.execute(
                "SELECT 1 FROM mg_admin_jobs WHERE " + condition + " LIMIT 1", (now, now)
            ).fetchone():
                orphan = conn.execute("""SELECT j.id,j.game_id FROM mg_admin_jobs j JOIN mg_admin_assets a
                    ON j.id=a.asset_id WHERE a.kind='stage' AND j.status IN ('succeeded','rejected') LIMIT 1""").fetchone()
                if not orphan:
                    return False
                job = dict(orphan)
                with game_lock(job["game_id"]):
                    remove_asset("stage", job["game_id"], job["id"])
                    clear_stage(conn, job)
                    conn.commit()
                return True
            platform.db_start(conn)
            serial(conn)
            row = conn.execute(
                "SELECT * FROM mg_admin_jobs WHERE "
                + condition
                + " ORDER BY CASE WHEN kind IN ('delete','delete-version') THEN 0 ELSE 1 END,created,id LIMIT 1",
                (now, now),
            ).fetchone()
            if not row:
                return False
            job = dict(row)
            job["token"] = uid()
            conn.execute(
                "UPDATE mg_admin_jobs SET status='running',stage=?,token=?,expires=? WHERE id=?",
                (
                    "清理中" if job["kind"] in ("delete","delete-version") else "校验中",
                    job["token"],
                    now + WORKER_TTL,
                    job["id"],
                ),
            )
            conn.commit()
        try:
            with game_lock(job["game_id"]):
                if job["kind"] in ("delete","delete-version"):
                    deletion_register(job)
                    with platform.asset_usage(job["game_id"], exclusive=True):
                        if job["kind"] == "delete-version":
                            delete_version_job(job)
                        else:
                            delete_job(job)
                elif job["status"] == "receiving":
                    record_job_failure(
                        job, "rejected", {"code": "UPLOAD_INTERRUPTED", "message": "接收中断，请重新上传。"}
                    )
                else:
                    validate_job(job, probe_blocked_until=blocked_until)
        except validation.InvalidPackage as exc:
            record_job_failure(job, "rejected", exc.issue)
        except HTTPException as exc:
            issue = (
                exc.detail if isinstance(exc.detail, dict) else {"code": "RETRY", "message": str(exc.detail)}
            )
            if job["kind"] == "validate" and issue.get("code") in {"PROBE_NOT_CONFIGURED", "PROBE_UNAVAILABLE"}:
                disabled = issue["code"] == "PROBE_NOT_CONFIGURED"
                due = 0 if disabled else blocked_until or platform.now_ms() + PROBE_BACKOFF_MS
                stage = "静态检查已通过，启用验收后自动继续" if disabled else PROBE_OFFLINE_STAGE
                with platform.game_db() as conn:
                    finish_job(conn, job, "waiting_probe", stage, due=due)
                    conn.commit()
            else:
                record_job_failure(job, "retry", issue, 30_000)
        except Exception:
            log.exception("Game package job %s requires a retry", job["id"])
            record_job_failure(
                job, "retry", {"code": "JOB_RETRY", "message": "任务暂时无法完成，已安排重试。"}, 30_000
            )
        return True


async def _worker_loop(*, ai=False):
    while not _stop.is_set():
        try:
            if ai:
                from . import minigame_moderation as moderation
                busy = await moderation.process_one() or await suggestions.process_one()
            else:
                busy = await asyncio.get_running_loop().run_in_executor(_executor, worker_once)
        except Exception as exc:
            if not isinstance(exc, HTTPException):
                log.exception("Game package worker temporarily unavailable")
            busy = False
        try:
            await asyncio.wait_for(_stop.wait(), timeout=0.05 if busy else 2)
        except asyncio.TimeoutError:
            pass


async def start_package_worker():
    global _executor, _runner, _ai_runner, _stop
    if _runner is None:
        _stop = asyncio.Event()
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="game-packages")
        _runner = asyncio.create_task(_worker_loop())
        _ai_runner = asyncio.create_task(_worker_loop(ai=True))


async def stop_package_worker():
    global _runner, _executor, _ai_runner, _stop
    if _runner:
        _stop.set()
        _ai_runner.cancel()
        await asyncio.gather(_runner, _ai_runner, return_exceptions=True)
        _executor.shutdown(wait=True)
        _runner = _executor = _ai_runner = _stop = None
