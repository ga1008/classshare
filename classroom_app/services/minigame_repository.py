"""Metadata read from incoming ZIPs and durable per-version distribution rights."""

import hashlib
import json
import zipfile
import zlib

from fastapi import HTTPException

from . import minigame_package_validation as validation
from . import minigame_platform as platform


def fail(code, message, status=422):
    raise HTTPException(status, {"code": code, "message": message})


def release_rank(version):
    major, minor, patch = map(int, version.split("."))
    return (major * 100000 + minor) * 100000 + patch


def check_tags(tags):
    if not isinstance(tags, list) or len(tags) > 12 or not all(
        isinstance(tag, str) and 0 < len(tag.strip()) <= 32 for tag in tags
    ):
        fail("INVALID_TAGS", "最多 12 个标签，每个标签为 1～32 个字符。")
    return list(dict.fromkeys(tag.strip() for tag in tags))


def read_manifest(stream):
    stream.seek(0, 2)
    if stream.tell() > validation.MAX_ZIP:
        fail("ZIP_QUOTA", "游戏 ZIP 超过 64 MiB。", 413)
    stream.seek(0)
    try:
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            if len(infos) > 2000:
                fail("ENTRY_QUOTA", "ZIP 条目超过 2000。")
            members = [(validation.clean_member(i.orig_filename), i) for i in infos if not i.is_dir()]
            roots = {name.split("/", 1)[0] for name, _ in members}
            wrapped = len(roots) == 1 and all("/" in name for name, _ in members)
            configs = [info for name, info in members
                       if (name.split("/", 1)[1] if wrapped else name) == "config.json"]
            if len(configs) != 1:
                fail("MISSING_CONFIG", "游戏包根目录必须有且只有一个 config.json。")
            info = configs[0]
            if info.file_size > validation.TEXT_LIMIT or info.flag_bits & 1 or info.compress_type not in (
                zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED
            ):
                fail("INVALID_CONFIG", "config.json 必须是未加密、大小合规的普通 JSON。")
            with archive.open(info) as entry:
                content = entry.read(validation.TEXT_LIMIT + 1)
            if len(content) != info.file_size:
                fail("INVALID_CONFIG", "config.json 大小不符合声明。")
            config = validation.parse_json(content.decode("utf-8-sig"), "config.json")
            validation.validate_schema(config, "config", "config.json")
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return config, digest
    except validation.InvalidPackage as exc:
        raise HTTPException(422, exc.issue) from exc
    except (zipfile.BadZipFile, UnicodeError, ValueError, RuntimeError, EOFError, zlib.error) as exc:
        raise HTTPException(422, {"code": "INVALID_ZIP", "message": "无法读取游戏包清单。"}) from exc
    finally:
        stream.seek(0)


def source_info(conn, user, game, version_id=""):
    item = platform.require_play(conn, user, game, definition=False)
    vid = version_id or item["version_id"]
    edition = conn.execute(
        """SELECT v.release_version,a.author,a.allow_remix,a.tags_json
           FROM mg_versions v JOIN mg_version_authorship a ON a.version_id=v.id
           WHERE v.game_id=? AND v.id=? AND v.status='ready' AND
           (v.id=? OR EXISTS (SELECT 1 FROM mg_admin_releases r WHERE r.game_id=v.game_id AND r.version_id=v.id))""",
        (game, vid, item["version_id"]),
    ).fetchone()
    if not edition or not edition["allow_remix"]:
        fail("SOURCE_UNAVAILABLE", "此版本未开放二次开发。", 404)
    return {**item, "version_id": vid, "release_version": edition["release_version"],
            "author_name": edition["author"], "allow_remix": True, "tags_json": edition["tags_json"]}


def import_attribution(conn, user, game, config, source_id):
    declared = config.get("attribution")
    # An existing fork can release updates with the original credited snapshot
    # even if the parent subsequently changes tags or leaves the catalogue.
    if game:
        existing = conn.execute(
            """SELECT a.* FROM mg_version_authorship a JOIN mg_versions v ON v.id=a.version_id
               WHERE v.game_id=? AND a.source_version_id<>'' ORDER BY v.created_at DESC LIMIT 1""", (game,)
        ).fetchone()
        if existing:
            saved = json.loads(existing["source_json"])
            if (source_id and source_id != existing["source_version_id"]) or (declared and declared != saved):
                fail("SOURCE_MISMATCH", "同一作品的更新必须保留首次登记的原作署名。")
            return existing["source_version_id"], saved
        if (declared or source_id) and conn.execute(
            "SELECT 1 FROM mg_versions WHERE game_id=? LIMIT 1", (game,)
        ).fetchone():
            fail("FORK_KEY_REQUIRED", "引入其他原作的改编作品请使用新的 gameKey。", 409)
    if not declared and not source_id:
        return "", {}
    if source_id:
        source = conn.execute("SELECT id,game_id,release_version FROM mg_versions WHERE id=?", (source_id,)).fetchone()
    else:
        source = conn.execute(
            """SELECT v.id,v.game_id,v.release_version FROM mg_games g JOIN mg_versions v ON v.game_id=g.id
               WHERE g.slug=? AND v.release_version=? AND v.status='ready'""", (declared["gameKey"], declared["version"])
        ).fetchone()
    if not source:
        fail("SOURCE_UNAVAILABLE", "原版不存在、未公开或已停止授权。", 404)
    item = source_info(conn, user, source["game_id"], source["id"])
    if item["slug"] == config["gameKey"]:
        fail("FORK_KEY_REQUIRED", "二次开发作品请在配置包内使用新的 gameKey。", 409)
    source_meta = {"gameKey": item["slug"], "version": source["release_version"], "author": item["author_name"],
                   "tags": json.loads(item["tags_json"])}
    if declared and declared != source_meta:
        fail("SOURCE_MISMATCH", "原作者、版本或原作标签与平台登记不一致。")
    return source["id"], source_meta


def store_authorship(conn, version, config, author, options, attribution, tags, checksum):
    source_id, source = attribution
    conn.execute(
        """INSERT INTO mg_version_authorship
           (version_id,author,allow_remix,source_version_id,source_author,source_tags_json,source_json,
           tags_json,package_sha256,release_rank) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (version, config.get("author") or author, int(options["allowRemix"]), source_id, source.get("author", ""),
         validation.canonical(source.get("tags", [])), validation.canonical(source), validation.canonical(tags),
         checksum, release_rank(config["version"])),
    )
