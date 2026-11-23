"""Version-bound student publication, super-admin review workspace, and optional AI risk check."""

import asyncio
import json
import logging
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import HTTPException

from . import minigame_content_assessment as assessment
from . import minigame_package_validation as validation
from . import minigame_packages as packages
from . import minigame_platform as platform

STATES = {"queued", "running", "manual", "approved", "rejected", "cancelled"}
FILTER_STATES = STATES | {"ai_pending", "all"}
LEASE_MS = 180_000
MAX_PREVIEW_TEXT_BYTES = 1024 * 1024
SETTING_AUTO_AI_REVIEW = "minigames_auto_ai_review"
logger = logging.getLogger(__name__)


def ensure_schema(conn):
    for sql in (
        """CREATE TABLE IF NOT EXISTS mg_content_reviews(
            version_id TEXT PRIMARY KEY REFERENCES mg_versions(id) ON DELETE CASCADE,
            game_id TEXT NOT NULL REFERENCES mg_games(id) ON DELETE CASCADE, digest TEXT NOT NULL,
            status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', ai_reason TEXT NOT NULL DEFAULT '',
            categories_json TEXT NOT NULL DEFAULT '[]', evidence_json TEXT NOT NULL DEFAULT '[]',
            claim_token TEXT NOT NULL DEFAULT '', lease_until BIGINT NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0, decider TEXT NOT NULL DEFAULT '',
            notify_due BIGINT NOT NULL DEFAULT 0, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS mg_content_reviews_queue ON mg_content_reviews(status,created_at)",
        """CREATE TABLE IF NOT EXISTS mg_review_notices(
            version_id TEXT NOT NULL REFERENCES mg_content_reviews(version_id) ON DELETE CASCADE,
            teacher_id INTEGER NOT NULL, inbox_id BIGINT NOT NULL DEFAULT 0,
            email_queued INTEGER NOT NULL DEFAULT 0, due BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY(version_id,teacher_id))""",
        "CREATE INDEX IF NOT EXISTS mg_review_notices_due ON mg_review_notices(email_queued,due)",
    ):
        conn.execute(sql)


def parse_evidence_blob(raw):
    if not raw:
        return [], None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return [], None
    if isinstance(parsed, list):
        return parsed, None
    if isinstance(parsed, dict):
        findings = parsed.get("findings")
        report = parsed.get("report")
        return (
            findings if isinstance(findings, list) else [],
            report if isinstance(report, dict) else None,
        )
    return [], None


def dump_evidence_blob(findings, report=None):
    clean_findings = list(findings or [])
    if isinstance(report, dict) and report:
        return json.dumps({"findings": clean_findings, "report": report}, ensure_ascii=False)
    return json.dumps(clean_findings, ensure_ascii=False)


def get_settings(conn):
    auto_ai = False
    try:
        conn.execute("SAVEPOINT mg_review_settings_read")
        try:
            row = conn.execute(
                "SELECT value FROM system_settings WHERE key=?",
                (SETTING_AUTO_AI_REVIEW,),
            ).fetchone()
            auto_ai = bool(row and str(row["value"]).strip().lower() in ("1", "true", "yes", "on"))
        finally:
            conn.execute("RELEASE SAVEPOINT mg_review_settings_read")
    except Exception:
        try:
            conn.execute("ROLLBACK TO SAVEPOINT mg_review_settings_read")
            conn.execute("RELEASE SAVEPOINT mg_review_settings_read")
        except Exception:
            pass
    return {"autoAiReview": auto_ai}


def read_settings(conn, user):
    require_admin(conn, user)
    return get_settings(conn)


def set_settings(conn, user, body):
    require_admin(conn, user)
    if not isinstance(body, dict) or type(body.get("autoAiReview")) is not bool:
        raise HTTPException(422, "请明确设置是否开启自动 AI 审核。")
    auto_ai = body["autoAiReview"]
    conn.execute(
        """INSERT INTO system_settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
        (SETTING_AUTO_AI_REVIEW, "1" if auto_ai else "0"),
    )
    conn.commit()
    return {"autoAiReview": auto_ai}


def enqueue(conn, game_id, version_id, digest):
    now = platform.now_ms()
    auto_ai = get_settings(conn)["autoAiReview"]
    status = "queued" if auto_ai else "manual"
    reason = (
        "已通过第一轮基础校验，等待 AI 风险查验。"
        if auto_ai
        else "已通过第一轮基础校验，等待超管审核。"
    )
    changed = conn.execute(
        """INSERT INTO mg_content_reviews(version_id,game_id,digest,status,reason,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?) ON CONFLICT(version_id) DO NOTHING""",
        (version_id, game_id, digest, status, reason, now, now),
    ).rowcount
    if changed == 1 and status == "manual":
        register_recipients(conn, version_id)
    return status


def resubmit(conn, game_id, version_id, digest):
    now = platform.now_ms()
    auto_ai = get_settings(conn)["autoAiReview"]
    status = "queued" if auto_ai else "manual"
    reason = (
        "已重新提交审核，等待 AI 风险查验。"
        if auto_ai
        else "已重新提交审核，等待超管审核。"
    )
    conn.execute("DELETE FROM mg_review_notices WHERE version_id=?", (version_id,))
    conn.execute(
        """INSERT INTO mg_content_reviews(
               version_id,game_id,digest,status,reason,ai_reason,categories_json,evidence_json,
               claim_token,lease_until,attempts,decider,notify_due,created_at,updated_at
           ) VALUES(?,?,?,?,?,'','[]','[]','',0,0,'',0,?,?)
           ON CONFLICT(version_id) DO UPDATE SET
               digest=excluded.digest, status=excluded.status, reason=excluded.reason, ai_reason='',
               categories_json='[]', evidence_json='[]', claim_token='', lease_until=0,
               attempts=0, decider='', notify_due=0, updated_at=excluded.updated_at""",
        (version_id, game_id, digest, status, reason, now, now),
    )
    if status == "manual":
        register_recipients(conn, version_id)
    return status


def cancel(conn, game_id, version_id=None):
    sql = """UPDATE mg_content_reviews SET status='cancelled',claim_token='',lease_until=0,updated_at=?
             WHERE game_id=? AND status IN ('queued','running','manual')"""
    args = (platform.now_ms(), game_id)
    if version_id is not None:
        sql += " AND version_id=?"
        args += (version_id,)
    conn.execute(sql, args)


def get_summary(conn, vid):
    return get_summaries(conn, [vid]).get(vid)


def get_summaries(conn, version_ids):
    if not version_ids:
        return {}
    params = ",".join("?" for _ in version_ids)
    rows = conn.execute(
        f"""SELECT version_id,status,reason,categories_json,updated_at FROM mg_content_reviews
            WHERE version_id IN ({params})""",
        tuple(version_ids),
    ).fetchall()
    return {
        row["version_id"]: {
            "status": row["status"],
            "reason": row["reason"],
            "categories": json.loads(row["categories_json"]),
            "updatedAt": row["updated_at"],
        }
        for row in rows
    }


def claim():
    now = platform.now_ms()
    sql = """SELECT r.*,v.files_json,g.owner_teacher_id,a.author,a.source_author,a.tags_json,a.source_tags_json
        FROM mg_content_reviews r JOIN mg_games g ON g.id=r.game_id JOIN mg_versions v ON v.id=r.version_id
        JOIN mg_version_authorship a ON a.version_id=v.id
        WHERE (r.status='queued' OR (r.status='running' AND r.lease_until<=?))
        AND v.status='reviewing' AND r.digest=v.digest AND g.owner_teacher_id<0
        AND g.status NOT IN ('deleting','deleted') AND NOT EXISTS(
        SELECT 1 FROM mg_content_reviews busy WHERE busy.status='running' AND busy.lease_until>?)
        ORDER BY r.created_at,r.version_id LIMIT 1"""
    with platform.game_db() as conn:
        if not conn.execute(sql, (now, now)).fetchone():
            return None
        platform.db_start(conn)
        packages.serial(conn)
        row = conn.execute(sql, (now, now)).fetchone()
        if not row:
            return None
        job = dict(row)
        job.update(claim_token=packages.uid(), attempts=row["attempts"] + 1, student_id=-row["owner_teacher_id"])
        job["metadata"] = {
            "author": row["author"],
            "sourceAuthor": row["source_author"],
            "tags": json.loads(row["tags_json"]),
            "sourceTags": json.loads(row["source_tags_json"]),
        }
        changed = conn.execute(
            """UPDATE mg_content_reviews SET status='running',claim_token=?,lease_until=?,attempts=?,updated_at=?
               WHERE version_id=? AND (status='queued' OR (status='running' AND lease_until<=?))""",
            (job["claim_token"], now + LEASE_MS, job["attempts"], now, job["version_id"], now),
        ).rowcount
        if changed != 1:
            return None
        conn.commit()
        return job


def require_admin(conn, user):
    platform.assert_session(conn, user)
    if user["role"] != "teacher" or not platform.is_super_admin_teacher(conn, user["id"]):
        raise HTTPException(403, "仅超管可以查看和决定小游戏审核。")


_REVIEW_SELECT = """SELECT r.*,g.title,g.slug,g.summary AS game_summary,g.status AS game_status,
    g.owner_teacher_id,g.tags_json AS game_tags_json,
    v.status AS version_status,v.digest AS version_digest,v.release_version,v.config_json,
    COALESCE(a.author,'') AS author_name,COALESCE(a.source_author,'') AS source_author,
    COALESCE(a.tags_json,'[]') AS version_tags_json,COALESCE(a.source_tags_json,'[]') AS source_tags_json,
    COALESCE(a.allow_remix,0) AS allow_remix,
    (SELECT COUNT(*) FROM mg_version_files vf WHERE vf.version_id=r.version_id) AS file_count,
    (SELECT COALESCE(SUM(vf.byte_size),0) FROM mg_version_files vf WHERE vf.version_id=r.version_id) AS total_bytes
    FROM mg_content_reviews r JOIN mg_versions v ON v.id=r.version_id
    JOIN mg_games g ON g.id=r.game_id LEFT JOIN mg_version_authorship a ON a.version_id=v.id"""


def serialize(row):
    findings, report = parse_evidence_blob(row["evidence_json"])
    cfg = json.loads(row["config_json"] or "{}")
    return {
        "versionId": row["version_id"],
        "gameId": row["game_id"],
        "slug": row["slug"] or "",
        "gameKey": row["slug"] or "",
        "title": row["title"],
        "summary": row["game_summary"] or cfg.get("summary") or "",
        "version": row["release_version"] or "",
        "digest": row["digest"],
        "status": row["status"],
        "versionStatus": row["version_status"],
        "gameStatus": row["game_status"],
        "reason": row["reason"],
        "aiReason": row["ai_reason"],
        "decider": row["decider"],
        "categories": json.loads(row["categories_json"] or "[]"),
        "aiVerdict": (report or {}).get("verdict", ""),
        "findingCount": len(findings),
        "authorName": row["author_name"] or cfg.get("author") or "",
        "ownerRole": "student" if row["owner_teacher_id"] < 0 else "teacher",
        "fileCount": int(row["file_count"] or 0),
        "totalBytes": int(row["total_bytes"] or 0),
        "iconUrl": f"/api/games/v1/moderation/reviews/{row['version_id']}/icon",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def status_counts(conn):
    counts = {"manual": 0, "aiPending": 0, "approved": 0, "rejected": 0, "cancelled": 0, "total": 0}
    for row in conn.execute(
        """SELECT r.status, COUNT(*) AS n
           FROM mg_content_reviews r JOIN mg_games g ON g.id=r.game_id
           WHERE g.status NOT IN ('deleting','deleted')
           GROUP BY r.status"""
    ).fetchall():
        st, n = row["status"], int(row["n"] or 0)
        counts["total"] += n
        if st in ("queued", "running"):
            counts["aiPending"] += n
        elif st in counts:
            counts[st] += n
    return counts


def list_reviews(conn, user, status="manual", offset=0, q=""):
    require_admin(conn, user)
    if status not in FILTER_STATES:
        raise HTTPException(422, "无效审核状态。")
    clauses = ["g.status NOT IN ('deleting','deleted')"]
    args = []
    if status == "ai_pending":
        clauses.append("r.status IN ('queued','running')")
    elif status != "all":
        clauses.append("r.status=?")
        args.append(status)
    query = str(q or "").strip()[:120]
    if query:
        esc = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        clauses.append(
            "(LOWER(g.title) LIKE LOWER(?) ESCAPE '!' OR LOWER(COALESCE(g.slug,'')) LIKE LOWER(?) ESCAPE '!'"
            " OR LOWER(COALESCE(a.author,'')) LIKE LOWER(?) ESCAPE '!'"
            " OR LOWER(COALESCE(v.release_version,'')) LIKE LOWER(?) ESCAPE '!')"
        )
        args.extend([f"%{esc}%"] * 4)
    where = " WHERE " + " AND ".join(clauses)
    rows = conn.execute(
        _REVIEW_SELECT + where + " ORDER BY r.updated_at DESC,r.version_id LIMIT 26 OFFSET ?",
        (*args, offset),
    ).fetchall()
    return {
        "items": [serialize(r) for r in rows[:25]],
        "hasMore": len(rows) > 25,
        "counts": status_counts(conn),
        "settings": get_settings(conn),
    }


def review_row(conn, vid):
    row = conn.execute(_REVIEW_SELECT + " WHERE r.version_id=?", (vid,)).fetchone()
    if not row:
        raise HTTPException(404, "找不到此审核版本。")
    return row


def detail(conn, user, vid):
    require_admin(conn, user)
    row = review_row(conn, vid)
    findings, report = parse_evidence_blob(row["evidence_json"])
    cfg = json.loads(row["config_json"] or "{}")
    files = conn.execute(
        "SELECT path,byte_size,mime,sha256 FROM mg_version_files WHERE version_id=? ORDER BY path",
        (vid,),
    ).fetchall()
    tags = list(
        dict.fromkeys(
            [
                *json.loads(row["source_tags_json"] or "[]"),
                *json.loads(row["version_tags_json"] or "[]"),
                *json.loads(row["game_tags_json"] or "[]"),
            ]
        )
    )
    active = row["game_status"] not in ("deleted", "deleting")
    return {
        **serialize(row),
        "evidence": findings,
        "aiReport": report,
        "tags": tags,
        "sourceAuthor": row["source_author"] or "",
        "sourceTags": json.loads(row["source_tags_json"] or "[]"),
        "allowRemix": bool(row["allow_remix"]),
        "config": cfg,
        "files": [
            {"path": f["path"], "size": f["byte_size"], "mime": f["mime"], "sha256": f["sha256"]}
            for f in files
        ],
        "treeUrl": f"/api/games/v1/moderation/reviews/{vid}/tree",
        "packageUrl": f"/api/games/v1/moderation/reviews/{vid}/package",
        "canPreview": bool(active and row["version_status"] in ("ready", "reviewing")),
        "manageUrl": f"/manage/library/games/{row['game_id']}?version={vid}",
    }


def _describe_package_file(path, mime):
    ext = PurePosixPath(path).suffix.lower().lstrip(".")
    mime = str(mime or "").lower()
    if ext in ("md", "markdown"):
        return "markdown", "Markdown", "#0f766e", "markdown"
    if ext in ("html", "htm"):
        return "html_page", "HTML 源码", "#d97706", "text"
    if ext in ("js", "mjs", "css", "json", "xml", "svg"):
        return "code", "源码", "#0284c7", "text"
    if mime.startswith("image/"):
        return "image", "图片", "#8b5cf6", "image"
    if mime.startswith("audio/") or mime.startswith("video/"):
        return "media", "音视频", "#db2777", "media"
    if mime.startswith("text/") or mime == "application/json":
        return "text", "文本", "#64748b", "text"
    return "file", "资源文件", "#94a3b8", "download"


def package_tree(conn, user, vid):
    require_admin(conn, user)
    row = review_row(conn, vid)
    if row["game_status"] in ("deleted", "deleting"):
        raise HTTPException(404, "该版本的游戏包已移除。")
    findings, _ = parse_evidence_blob(row["evidence_json"])
    findings_by_file = {}
    for item in findings:
        if isinstance(item, dict) and item.get("file"):
            findings_by_file.setdefault(str(item["file"]), []).append(str(item.get("reason") or ""))

    file_rows = conn.execute(
        "SELECT path,byte_size,mime,sha256 FROM mg_version_files WHERE version_id=? ORDER BY path",
        (vid,),
    ).fetchall()
    root_name = f"{row['title'] or row['slug'] or 'game'} ({row['release_version'] or '候选版'})"
    next_id = 1
    root = {
        "id": next_id,
        "parent_id": None,
        "name": root_name,
        "node_type": "folder",
        "material_path": "/",
        "rel_path": "",
        "document_kind": "html_package",
        "kind_label": "游戏包目录",
        "kind_color": "#4f46e5",
        "preview_mode": "folder",
        "file_size": 0,
        "child_count": 0,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "can_manage": False,
        "editable": False,
        "download_allowed": False,
        "ai_findings": [],
        "ai_finding_count": 0,
        "children": [],
    }
    folders = {"": root}
    folder_count = 0
    total_size = 0

    def ensure_folder(folder_path):
        nonlocal next_id, folder_count
        if folder_path in folders:
            return folders[folder_path]
        parent_path = folder_path.rsplit("/", 1)[0] if "/" in folder_path else ""
        parent = ensure_folder(parent_path)
        next_id += 1
        folder_count += 1
        node = {
            "id": next_id,
            "parent_id": parent["id"],
            "name": folder_path.rsplit("/", 1)[-1],
            "node_type": "folder",
            "material_path": "/" + folder_path,
            "rel_path": folder_path,
            "document_kind": "folder",
            "kind_label": "文件夹",
            "kind_color": "#0ea5e9",
            "preview_mode": "folder",
            "file_size": 0,
            "child_count": 0,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "can_manage": False,
            "editable": False,
            "download_allowed": False,
            "ai_findings": [],
            "ai_finding_count": 0,
            "children": [],
        }
        parent["children"].append(node)
        folders[folder_path] = node
        return node

    for f in file_rows:
        rel = str(f["path"])
        parent_path = rel.rsplit("/", 1)[0] if "/" in rel else ""
        parent = ensure_folder(parent_path)
        next_id += 1
        size = int(f["byte_size"] or 0)
        total_size += size
        doc_kind, kind_label, kind_color, preview_mode = _describe_package_file(rel, f["mime"])
        encoded_path = quote(rel, safe="")
        base_url = f"/api/games/v1/moderation/reviews/{quote(vid, safe='')}"
        file_findings = findings_by_file.get(rel, [])
        node = {
            "id": next_id,
            "parent_id": parent["id"],
            "name": rel.rsplit("/", 1)[-1],
            "node_type": "file",
            "material_path": "/" + rel,
            "rel_path": rel,
            "file_ext": PurePosixPath(rel).suffix.lower().lstrip("."),
            "mime_type": f["mime"],
            "file_size": size,
            "sha256": f["sha256"],
            "document_kind": doc_kind,
            "kind_label": kind_label,
            "kind_color": kind_color,
            "preview_mode": preview_mode,
            "preview_url": "",
            "preview_frame_url": "",
            "preview_content_url": f"{base_url}/file-content?path={encoded_path}" if preview_mode in ("text", "markdown") else "",
            "preview_image_url": f"{base_url}/file?path={encoded_path}" if preview_mode == "image" else "",
            "preview_media_url": f"{base_url}/file?path={encoded_path}" if preview_mode == "media" else "",
            "download_url": f"{base_url}/file?path={encoded_path}&download=1",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "can_manage": False,
            "editable": False,
            "download_allowed": True,
            "ai_findings": file_findings,
            "ai_finding_count": len(file_findings),
        }
        parent["children"].append(node)

    def finalize(node):
        if node["node_type"] != "folder":
            return node["file_size"], node["ai_finding_count"]
        node["children"].sort(
            key=lambda child: (0 if child["node_type"] == "folder" else 1, child["name"].casefold())
        )
        sub_size = 0
        sub_findings = 0
        for child in node["children"]:
            c_size, c_findings = finalize(child)
            sub_size += c_size
            sub_findings += c_findings
        node["file_size"] = sub_size
        node["child_count"] = len(node["children"])
        node["ai_finding_count"] = sub_findings
        return sub_size, sub_findings

    finalize(root)
    return {
        "tree": root,
        "selected_id": root["id"],
        "stats": {
            "folder_count": folder_count,
            "file_count": len(file_rows),
            "total_size": total_size,
        },
    }


def resolve_review_member(conn, user, vid, rel_path):
    require_admin(conn, user)
    row = review_row(conn, vid)
    if row["game_status"] in ("deleted", "deleting"):
        raise HTTPException(404, "该版本的游戏包已移除。")
    try:
        clean = validation.clean_member(str(rel_path or ""))
    except validation.InvalidPackage as exc:
        raise HTTPException(422, "无效的文件路径。") from exc
    meta = conn.execute(
        "SELECT path,mime,byte_size,sha256 FROM mg_version_files WHERE version_id=? AND path=?",
        (vid, clean),
    ).fetchone()
    if not meta:
        raise HTTPException(404, "包内不存在该文件。")
    game = row["game_id"]
    folder = packages.asset_path("version", game, vid)
    target = packages.checked_dir(folder, folder / "runtime" / clean)
    if not target.is_file():
        raise HTTPException(404, "包内文件已移除。")
    packages.marker(folder, game, vid)
    return game, target, dict(meta)


def read_review_file_content(conn, user, vid, rel_path):
    game, target, meta = resolve_review_member(conn, user, vid, rel_path)
    mime = str(meta["mime"] or "").lower()
    if not (mime.startswith("text/") or mime in ("application/json", "application/javascript")):
        raise HTTPException(415, "此文件不是可阅读的文本或源码文件。")
    with platform.asset_usage(game):
        if not target.is_file():
            raise HTTPException(404, "包内文件已移除。")
        raw = target.read_bytes()
    truncated = len(raw) > MAX_PREVIEW_TEXT_BYTES
    content = raw[:MAX_PREVIEW_TEXT_BYTES].decode("utf-8-sig", errors="replace")
    return {
        "content": content,
        "encoding": "utf-8",
        "truncated": truncated,
        "size": meta["byte_size"],
        "path": meta["path"],
        "mime": meta["mime"],
    }


def preview_launch_url(conn, user, vid):
    require_admin(conn, user)
    row = review_row(conn, vid)
    if row["game_status"] in ("deleted", "deleting") or row["version_status"] not in ("ready", "reviewing"):
        raise HTTPException(409, "当前版本状态不支持隔离试玩。")
    platform.content_origins()
    return {
        "url": f"/games/{row['slug'] or row['game_id']}?previewVersion={vid}",
        "versionId": vid,
    }


def store_result(conn, row, result, decider):
    """Caller holds the game transaction lock; publication and review settle together."""
    status = result["status"]
    if row["digest"] != row["version_digest"] or row["version_status"] != "reviewing" or row["game_status"] in ("deleted", "deleting"):
        cancel(conn, row["game_id"], row["version_id"])
        return
    if status == "approved":
        packages.publish_reviewed(conn, row["game_id"], row["version_id"])
    elif status == "rejected":
        conn.execute("UPDATE mg_versions SET status='rejected' WHERE id=? AND status='reviewing'", (row["version_id"],))
    elif status == "manual":
        register_recipients(conn, row["version_id"])

    _, prior_report = parse_evidence_blob(row["evidence_json"])
    report = result.get("report") if decider == "ai" else (result.get("report") or prior_report)
    evidence_blob = dump_evidence_blob(result.get("evidence", []), report)
    ai_reason = result["reason"] if decider == "ai" else row["ai_reason"]
    conn.execute(
        """UPDATE mg_content_reviews SET status=?,reason=?,ai_reason=?,categories_json=?,evidence_json=?,
           decider=?,claim_token='',lease_until=0,notify_due=0,updated_at=? WHERE version_id=?""",
        (
            status,
            result["reason"],
            ai_reason,
            json.dumps(result["categories"]),
            evidence_blob,
            decider,
            platform.now_ms(),
            row["version_id"],
        ),
    )


def finish(job, result):
    with platform.game_db() as conn:
        platform.db_start(conn, job["game_id"])
        packages.serial(conn)
        row = conn.execute(_REVIEW_SELECT + " WHERE r.version_id=?", (job["version_id"],)).fetchone()
        if not row or row["status"] != "running" or row["claim_token"] != job["claim_token"] or row["digest"] != job["digest"]:
            return
        conn.execute("SAVEPOINT minigame_publication")
        try:
            store_result(conn, row, result, "ai")
        except HTTPException as exc:
            conn.execute("ROLLBACK TO SAVEPOINT minigame_publication")
            if exc.status_code not in (403, 409, 422):
                raise
            store_result(
                conn,
                row,
                assessment.manual(
                    "发布条件发生变化，请复核游戏归属及存档兼容性。",
                    verdict="uncertain",
                ),
                "ai",
            )
        conn.execute("RELEASE SAVEPOINT minigame_publication")
        conn.commit()


def submit_ai_review(conn, user, vid, body):
    require_admin(conn, user)
    game = review_row(conn, vid)["game_id"]
    platform.db_start(conn, game)
    packages.serial(conn)
    require_admin(conn, user)
    row = review_row(conn, vid)
    if not isinstance(body, dict):
        raise HTTPException(422, "请求参数无效。")
    if body.get("expectedDigest") != row["digest"] or row["version_digest"] != row["digest"]:
        raise HTTPException(409, "审核版本已改变，请刷新。")
    if row["game_status"] in ("deleted", "deleting"):
        raise HTTPException(409, "游戏已移除，请刷新。")
    if row["version_status"] != "reviewing":
        raise HTTPException(409, "该版本当前不在待审状态，无法提交 AI 审核。")
    if row["status"] in ("queued", "running"):
        return detail(conn, user, vid)
    if row["status"] != "manual":
        raise HTTPException(409, "该版本的复核状态已变更，请刷新。")
    conn.execute("DELETE FROM mg_review_notices WHERE version_id=?", (vid,))
    conn.execute(
        """UPDATE mg_content_reviews
           SET status='queued', reason='超管已提交 AI 风险查验，正在排队处理…',
               claim_token='', lease_until=0, attempts=0, notify_due=0, updated_at=?
           WHERE version_id=?""",
        (platform.now_ms(), vid),
    )
    conn.commit()
    return detail(conn, user, vid)


def decide(conn, user, vid, body):
    require_admin(conn, user)
    game = review_row(conn, vid)["game_id"]
    platform.db_start(conn, game)
    packages.serial(conn)
    require_admin(conn, user)
    row = review_row(conn, vid)
    if not isinstance(body, dict) or body.get("decision") not in ("approve", "reject"):
        raise HTTPException(422, "请选择通过或拒绝。")
    reason = body.get("reason")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 400:
        raise HTTPException(422, "请填写 1 至 400 字复核理由。")
    if body.get("expectedDigest") != row["digest"] or row["version_digest"] != row["digest"]:
        raise HTTPException(409, "审核版本已改变，请刷新。")
    status = "approved" if body["decision"] == "approve" else "rejected"
    if row["game_status"] in ("deleted", "deleting"):
        raise HTTPException(409, "游戏已移除，请刷新。")
    if row["status"] in ("manual", "queued") and row["version_status"] == "reviewing":
        findings, report = parse_evidence_blob(row["evidence_json"])
        store_result(
            conn,
            row,
            {
                "status": status,
                "reason": reason.strip(),
                "categories": json.loads(row["categories_json"] or "[]"),
                "evidence": findings,
                "report": report,
            },
            user["actor"],
        )
        conn.commit()
    elif not (row["status"] == status and row["decider"] == user["actor"] and row["reason"] == reason.strip()):
        raise HTTPException(409, "该版本的复核状态已变更，请刷新。")
    return detail(conn, user, vid)


def register_recipients(conn, vid):
    from .message_center_service import list_super_admin_teachers

    for teacher in list_super_admin_teachers(conn):
        conn.execute(
            "INSERT INTO mg_review_notices(version_id,teacher_id) VALUES(?,?) ON CONFLICT DO NOTHING",
            (vid, teacher["id"]),
        )


def dispatch_notice():
    """Separate durable inbox and mail steps. Retry when a sender/address is configured later."""
    from .email_notification_service import queue_notification_email_if_applicable
    from .message_center_service import _build_notification_payload, _insert_notification

    now = platform.now_ms()
    schedule = "SELECT version_id FROM mg_content_reviews WHERE status='manual' AND notify_due<=? ORDER BY created_at LIMIT 1"
    due = """SELECT n.*,r.game_id,r.status,r.reason,r.evidence_json,g.title
        FROM mg_review_notices n JOIN mg_content_reviews r ON n.version_id=r.version_id
        JOIN mg_games g ON r.game_id=g.id JOIN teachers t ON n.teacher_id=t.id
        WHERE r.status IN ('manual','approved','rejected') AND g.status NOT IN ('deleted','deleting')
        AND n.email_queued=0 AND n.due<=? AND t.is_super_admin=1 AND COALESCE(t.is_active,1)=1
        ORDER BY n.due,n.version_id,n.teacher_id LIMIT 1"""
    with platform.game_db() as conn:
        if not (conn.execute(schedule, (now,)).fetchone() or conn.execute(due, (now,)).fetchone()):
            return False
        platform.db_start(conn)
        packages.serial(conn)
        new = conn.execute(schedule, (now,)).fetchone()
        if new:
            vid = new["version_id"]
            register_recipients(conn, vid)
            conn.execute("UPDATE mg_content_reviews SET notify_due=? WHERE version_id=?", (now + 300_000, vid))
        notice = conn.execute(due, (now,)).fetchone()
        if notice:
            vid, admin = notice["version_id"], notice["teacher_id"]
            pending = notice["status"] == "manual"
            _, report = parse_evidence_blob(notice["evidence_json"])
            verdict = (report or {}).get("verdict", "")
            if not pending:
                title = "学生小游戏审核已处理"
                body = f"《{notice['title']}》的审核状态已更新，请查看审核记录。"
            elif verdict == "major_risk":
                title = "小游戏 AI 查验发现重大风险，请人工审核"
                body = f"《{notice['title']}》AI 提示重大内容风险：{notice['reason']}"[:240]
            elif verdict in ("uncertain", "error"):
                title = "小游戏 AI 审核未能确认，请人工复核"
                body = f"《{notice['title']}》需要超管人工查看原包与查验报告：{notice['reason']}"[:240]
            else:
                title = "学生小游戏待超管审核"
                body = f"《{notice['title']}》已通过第一轮基础校验，等待超管审核后发布。"
            payload = _build_notification_payload(
                recipient_role="teacher",
                recipient_user_pk=admin,
                category="approval_workflow",
                severity="important",
                title=title,
                body_preview=body,
                link_url="/manage/library/games/reviews?version=" + vid,
                ref_type="minigame_review",
                ref_id=vid,
            )
            if not notice["inbox_id"]:
                inbox_id = _insert_notification(conn, {**payload, "email_notification_allowed": False})
                conn.execute(
                    "UPDATE mg_review_notices SET inbox_id=? WHERE version_id=? AND teacher_id=?",
                    (inbox_id, vid, admin),
                )
            else:
                queued = queue_notification_email_if_applicable(conn, notification_id=notice["inbox_id"], payload=payload)
                conn.execute(
                    "UPDATE mg_review_notices SET email_queued=?,due=? WHERE version_id=? AND teacher_id=?",
                    (int(queued), now + 900_000, vid, admin),
                )
        conn.commit()
        return bool(new or notice)


async def process_one():
    job = await platform.run_db(claim)
    if job:
        packet = None
        try:
            if job["attempts"] > 2:
                result = assessment.manual(
                    "AI 审核任务多次中断，已转交超管人工审核。",
                    verdict="error",
                )
            else:
                packet = await asyncio.to_thread(assessment.evidence_for, job)
                result = await asyncio.wait_for(assessment.assess(job, packet), timeout=140)
        except Exception:
            logger.warning("Student game review deferred to administrators, version=%s", job["version_id"])
            result = assessment.manual(
                "AI 审核服务暂不可用、素材异常或返回结果不完整，请超管人工核查原包。",
                packet["unread"][:48] if packet else None,
                verdict="error",
            )
        await platform.run_db(finish, job, result)
    notice = await platform.run_db(dispatch_notice)
    return bool(job or notice)
