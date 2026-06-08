"""Sync the teacher's 校园公文通 inbox documents into ``gongwen_documents``.

Pulls ``/user/doc/page`` page-by-page with the authenticated client, splits each
document into structured fields, upserts it, and best-effort downloads the primary
attachment + extra attachment to ``data/gongwen_attachments`` (deploy-excluded, so
downloads survive redeploys).

Also exposes content-retrieval and search interfaces (``get_gongwen_document_content``,
``search_gongwen_documents``) that the future 公文内容提醒 feature will build on.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..config import DATA_DIR
from ..database import get_db_connection
from ..db.schema_gongwen import ensure_gongwen_schema
from .academic_integration_service import STATUS_VERIFIED
from .gongwen_integration_service import (
    get_gongwen_system_profile,
    load_teacher_gongwen_access_method,
    open_authenticated_gongwen_client,
)


GONGWEN_ATTACHMENT_DIR = Path(DATA_DIR) / "gongwen_attachments"
PAGE_SIZE = 50
MAX_PAGES = 40
# Be a courteous client: pace page requests and walk the inbox newest-first,
# stopping once a whole page is already in our DB (incremental). The first sync
# of an empty DB naturally walks to the end (full), but still paced.
PAGE_DELAY_SECONDS = 0.4
# Recurring auto-sync: low frequency, staggered per teacher so syncs don't all
# hit the upstream at once. Manual 立即同步 is the on-demand top-up.
GONGWEN_SYNC_TASK_KIND = "gongwen_incremental_sync"
GONGWEN_SYNC_INTERVAL_SECONDS = 6 * 3600
GONGWEN_SYNC_STAGGER_MINUTES = 180
MAX_DOWNLOAD_BYTES = 60 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60.0
# Only these hosts may be cached/redirected to — the school document CDN.
ALLOWED_FILE_HOSTS = {"doc.gxufl.com", "doc_api.gxufl.com"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_allowed_file_url(url: Any) -> bool:
    """Guard against open-redirect / SSRF: only allow the document CDN hosts."""
    raw = str(url or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        from urllib.parse import urlparse

        host = (urlparse(raw).hostname or "").lower()
    except ValueError:
        return False
    return host in ALLOWED_FILE_HOSTS


def _strip_html(raw_html: Any) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", str(raw_html or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_text(value: Any, *, limit: int | None = None) -> str:
    text = str(value if value is not None else "").strip()
    if limit and len(text) > limit:
        return text[:limit]
    return text


def _normalize_remote_url(url: Any, base_url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("/"):
        return base_url.rstrip("/") + raw
    return raw


def _safe_filename(url: str, fallback: str) -> str:
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    name = re.sub(r"[^0-9A-Za-z._-]", "_", name)
    return name or fallback


def _extract_document_fields(item: dict[str, Any], base_url: str) -> dict[str, Any]:
    doc_cate = item.get("docCate") if isinstance(item.get("docCate"), dict) else {}
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    content_html = _clean_text(item.get("content"))
    return {
        "remote_id": _clean_text(item.get("id")),
        "sn": _clean_text(item.get("sn"), limit=200),
        "title": _clean_text(item.get("title"), limit=500),
        "subhead": _clean_text(item.get("subhead"), limit=500),
        "author": _clean_text(item.get("author"), limit=200),
        "sender_name": _clean_text(user.get("realName") or user.get("name"), limit=120),
        "category_id": _clean_text(item.get("docCateId") or doc_cate.get("id"), limit=64),
        "category_name": _clean_text(doc_cate.get("name"), limit=200),
        "summary": _clean_text(item.get("description"), limit=2000),
        "content_html": content_html,
        "content_text": _strip_html(content_html),
        "keywords": _clean_text(item.get("keywords"), limit=500),
        "tags": _clean_text(item.get("tags"), limit=500),
        "source": _clean_text(item.get("source"), limit=200),
        "link": _normalize_remote_url(item.get("link"), base_url),
        "source_link": _normalize_remote_url(item.get("sourceLink"), base_url),
        "cover_url": _normalize_remote_url(item.get("cover"), base_url),
        "file_url": _normalize_remote_url(item.get("filePath"), base_url),
        "attachment_url": _normalize_remote_url(item.get("attachment"), base_url),
        "is_read": 1 if item.get("isRead") else 0,
        "is_fav": 1 if item.get("isFav") else 0,
        "is_need_feedback": 1 if item.get("isNeedFeedback") else 0,
        "publish_time": _clean_text(item.get("publishTime") or item.get("createAt"), limit=40),
        "remote_created_at": _clean_text(item.get("createAt"), limit=40),
        "remote_updated_at": _clean_text(item.get("updateAt"), limit=40),
    }


def _upsert_document(conn, teacher_id: int, credential_id: int | None, system_code: str, fields: dict[str, Any], raw_item: dict[str, Any]) -> int | None:
    if not fields["remote_id"]:
        return None
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO gongwen_documents (
            teacher_id, credential_id, system_code, remote_id, sn, title, subhead,
            author, sender_name, category_id, category_name, summary, content_html,
            content_text, keywords, tags, source, link, source_link, cover_url,
            file_url, attachment_url, is_read, is_fav, is_need_feedback,
            publish_time, remote_created_at, remote_updated_at, raw_json,
            synced_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(teacher_id, system_code, remote_id) DO UPDATE SET
            credential_id = excluded.credential_id,
            sn = excluded.sn,
            title = excluded.title,
            subhead = excluded.subhead,
            author = excluded.author,
            sender_name = excluded.sender_name,
            category_id = excluded.category_id,
            category_name = excluded.category_name,
            summary = excluded.summary,
            content_html = excluded.content_html,
            content_text = excluded.content_text,
            keywords = excluded.keywords,
            tags = excluded.tags,
            source = excluded.source,
            link = excluded.link,
            source_link = excluded.source_link,
            cover_url = excluded.cover_url,
            file_url = excluded.file_url,
            attachment_url = excluded.attachment_url,
            is_read = excluded.is_read,
            is_fav = excluded.is_fav,
            is_need_feedback = excluded.is_need_feedback,
            publish_time = excluded.publish_time,
            remote_created_at = excluded.remote_created_at,
            remote_updated_at = excluded.remote_updated_at,
            raw_json = excluded.raw_json,
            synced_at = excluded.synced_at,
            updated_at = excluded.updated_at
        """,
        (
            teacher_id, credential_id, system_code, fields["remote_id"], fields["sn"], fields["title"],
            fields["subhead"], fields["author"], fields["sender_name"], fields["category_id"],
            fields["category_name"], fields["summary"], fields["content_html"], fields["content_text"],
            fields["keywords"], fields["tags"], fields["source"], fields["link"], fields["source_link"],
            fields["cover_url"], fields["file_url"], fields["attachment_url"], fields["is_read"],
            fields["is_fav"], fields["is_need_feedback"], fields["publish_time"], fields["remote_created_at"],
            fields["remote_updated_at"], json.dumps(raw_item, ensure_ascii=False)[:200000], now, now, now,
        ),
    )
    row = conn.execute(
        "SELECT id, local_file_path, local_attachment_path FROM gongwen_documents "
        "WHERE teacher_id = ? AND system_code = ? AND remote_id = ? LIMIT 1",
        (teacher_id, system_code, fields["remote_id"]),
    ).fetchone()
    return dict(row) if row else None


def _load_existing_remote_ids(conn, teacher_id: int, system_code: str) -> set[str]:
    rows = conn.execute(
        "SELECT remote_id FROM gongwen_documents WHERE teacher_id = ? AND system_code = ?",
        (int(teacher_id), system_code),
    ).fetchall()
    return {str(dict(r)["remote_id"]) for r in rows}


async def sync_current_teacher_gongwen_documents(teacher_id: int, *, full: bool = False) -> dict[str, Any]:
    """Incrementally sync the teacher's inbox document metadata.

    Strategy (courteous data replication, not scraping):
    - Walk the inbox newest-first, paced by ``PAGE_DELAY_SECONDS``.
    - Match by stable ``remote_id`` (the snowflake id), never by date.
    - Stop as soon as a whole page contains nothing new (we've reached
      already-synced territory) unless ``full`` forces a complete sweep.
    - The first sync of an empty DB naturally walks to ``lastPage`` (full),
      so no extra "first full" code path is needed.

    Attachments are not bulk-downloaded here; they are public on the CDN and
    cached lazily on first download via ``ensure_local_attachment``.
    """
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        access = load_teacher_gongwen_access_method(conn, teacher_id)
    if not access:
        return {
            "status": "missing_credential",
            "message": "尚未保存并验证校园公文通账号，无法同步公文。",
            "counts": {},
            "warnings": [],
        }

    profile = get_gongwen_system_profile(access.get("system_code") or "gxufl")
    credential_id = access.get("credential_id")
    with get_db_connection() as conn:
        known_ids = _load_existing_remote_ids(conn, teacher_id, profile.system_code)
    counts = {"fetched": 0, "stored": 0, "new": 0, "pages": 0}
    warnings: list[str] = []
    mode = "全量" if (full or not known_ids) else "增量"

    try:
        async with open_authenticated_gongwen_client(access) as (client, profile, _token):
            page_no = 1
            while page_no <= MAX_PAGES:
                resp = await client.get(
                    f"{profile.api_base_url}/user/doc/page",
                    params={"pageNo": page_no, "pageSize": PAGE_SIZE},
                    headers={"Origin": profile.base_url, "Referer": f"{profile.base_url}/"},
                )
                resp.raise_for_status()
                body = resp.json()
                result = body.get("result") if isinstance(body, dict) else None
                if not isinstance(result, dict):
                    break
                items = result.get("list") or []
                if not items:
                    break
                counts["pages"] += 1

                new_in_page = 0
                with get_db_connection() as conn:
                    ensure_gongwen_schema(conn)
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        counts["fetched"] += 1
                        fields = _extract_document_fields(item, profile.base_url)
                        if fields["remote_id"] and fields["remote_id"] not in known_ids:
                            new_in_page += 1
                            counts["new"] += 1
                        row = _upsert_document(conn, teacher_id, credential_id, profile.system_code, fields, item)
                        if row:
                            counts["stored"] += 1
                    conn.commit()

                if result.get("lastPage"):
                    break
                # Incremental stop: this whole page was already known -> older
                # pages are too (feed is newest-first), so we're caught up.
                if not full and known_ids and new_in_page == 0:
                    break
                page_no += 1
                await asyncio.sleep(PAGE_DELAY_SECONDS)
            if page_no > MAX_PAGES:
                warnings.append(f"公文数量较多，本次最多读取 {MAX_PAGES * PAGE_SIZE} 条，可再次同步继续。")
    except ValueError as exc:
        return {"status": "failed", "message": str(exc), "counts": counts, "warnings": warnings}
    except httpx.HTTPError as exc:
        return {
            "status": "failed",
            "message": f"同步公文时连接异常：{str(exc)[:160]}",
            "counts": counts,
            "warnings": warnings,
        }

    status = "success" if counts["stored"] else "empty"
    if counts["stored"]:
        message = f"{mode}同步完成：新增 {counts['new']} 条，更新 {counts['stored']} 条。附件首次下载时按需缓存。"
    else:
        message = "未发现可同步的公文。"
    return {"status": status, "message": message, "counts": counts, "warnings": warnings}


# --------------------------------------------------------------------------- #
# Scheduled auto-sync (recurring, staggered) — keeps the local copy fresh for
# secondary stats & reminders without manual clicks, and without hammering the
# upstream (low frequency + per-teacher jitter + incremental).
# --------------------------------------------------------------------------- #


def schedule_gongwen_auto_sync(conn, teacher_id: int) -> int:
    """Arm/refresh the recurring incremental sync for a teacher. Idempotent."""
    from .scheduled_task_service import schedule_task

    teacher_id = int(teacher_id)
    # Stagger the first fire so simultaneously-onboarded teachers don't sync in
    # lockstep; subsequent fires recur every GONGWEN_SYNC_INTERVAL_SECONDS.
    offset_minutes = 30 + (teacher_id % GONGWEN_SYNC_STAGGER_MINUTES)
    run_at = datetime.now() + timedelta(minutes=offset_minutes)
    return schedule_task(
        conn,
        task_kind=GONGWEN_SYNC_TASK_KIND,
        run_at=run_at,
        payload={"teacher_id": teacher_id},
        dedupe_key=f"gongwen-sync:{teacher_id}",
        recurrence_seconds=GONGWEN_SYNC_INTERVAL_SECONDS,
        owner_role="teacher",
        owner_user_pk=teacher_id,
        title="公文增量同步",
        replace=True,
    )


def cancel_gongwen_auto_sync(conn, teacher_id: int) -> int:
    from .scheduled_task_service import cancel_tasks_by_dedupe

    return cancel_tasks_by_dedupe(conn, f"gongwen-sync:{int(teacher_id)}")


async def handle_gongwen_sync_task(task: dict[str, Any]) -> str:
    """Scheduler handler: run one incremental sync; self-cancel if the
    credential was removed so we stop logging into the upstream."""
    payload = task.get("payload") or {}
    teacher_id = int(payload.get("teacher_id") or 0)
    if not teacher_id:
        return "skipped: missing teacher"
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        access = load_teacher_gongwen_access_method(conn, teacher_id)
    if not access:
        with get_db_connection() as conn:
            cancel_gongwen_auto_sync(conn, teacher_id)
            conn.commit()
        return "cancelled: credential removed"
    result = await sync_current_teacher_gongwen_documents(teacher_id)
    counts = result.get("counts") or {}
    return f"{result.get('status')}: new={counts.get('new', 0)} stored={counts.get('stored', 0)}"


async def ensure_local_attachment(teacher_id: int, document_id: int, which: str = "primary") -> dict[str, Any]:
    """Lazily cache a document's public attachment to local disk on first access.

    Returns ``{"status", "local_path"?, "remote_url"?, "message"?}``. The caller
    serves the local file when present, otherwise redirects to ``remote_url``.
    Never raises — failures degrade to a redirect.
    """
    column = "local_file_path" if which != "attachment" else "local_attachment_path"
    url_column = "file_url" if which != "attachment" else "attachment_url"
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        row = conn.execute(
            f"SELECT id, {column} AS local_path, {url_column} AS remote_url, remote_id "
            "FROM gongwen_documents WHERE id = ? AND teacher_id = ? LIMIT 1",
            (int(document_id), int(teacher_id)),
        ).fetchone()
    if row is None:
        return {"status": "not_found"}
    row = dict(row)
    local_path = str(row.get("local_path") or "")
    remote_url = str(row.get("remote_url") or "")
    if local_path and Path(local_path).exists():
        return {"status": "local", "local_path": local_path}
    if not remote_url:
        return {"status": "no_file"}
    if not is_allowed_file_url(remote_url):
        # Refuse to touch/redirect to an unexpected host.
        return {"status": "no_file"}

    dest = GONGWEN_ATTACHMENT_DIR / str(teacher_id) / _safe_filename(remote_url, f"{row.get('remote_id')}_{which}")
    try:
        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
            response = await client.get(remote_url, headers={"User-Agent": "LanShare-Gongwen/1.0"})
            response.raise_for_status()
            content = response.content
            if not content or len(content) > MAX_DOWNLOAD_BYTES:
                raise ValueError("文件为空或超过下载上限。")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        with get_db_connection() as conn:
            conn.execute(
                f"UPDATE gongwen_documents SET {column} = ?, file_download_status = 'done', "
                "file_download_error = '', updated_at = ? WHERE id = ? AND teacher_id = ?",
                (str(dest), _now_iso(), int(document_id), int(teacher_id)),
            )
            conn.commit()
        return {"status": "local", "local_path": str(dest)}
    except (httpx.HTTPError, ValueError, OSError) as exc:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE gongwen_documents SET file_download_status = 'failed', file_download_error = ?, "
                "updated_at = ? WHERE id = ? AND teacher_id = ?",
                (str(exc)[:300], _now_iso(), int(document_id), int(teacher_id)),
            )
            conn.commit()
        return {"status": "redirect", "remote_url": remote_url, "message": str(exc)[:160]}


def _build_auto_sync_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status") or "unknown",
        "message": result.get("message") or "",
        "stages": [
            {
                "key": "documents",
                "label": "公文收件箱",
                "status": result.get("status") or "unknown",
                "message": result.get("message") or "",
                "counts": result.get("counts") or {},
                "warnings": result.get("warnings") or [],
            }
        ],
    }


async def sync_teacher_gongwen_data_after_credential_verified(teacher_id: int) -> dict[str, Any]:
    """Run the document sync right after a credential is verified/saved."""
    result = await sync_current_teacher_gongwen_documents(teacher_id)
    payload = _build_auto_sync_payload(result)
    payload["counts"] = result.get("counts") or {}
    payload["warnings"] = result.get("warnings") or []
    return payload


# --------------------------------------------------------------------------- #
# Capabilities + serialization
# --------------------------------------------------------------------------- #


def build_gongwen_sync_capabilities(conn, teacher_id: int) -> list[dict[str, Any]]:
    ensure_gongwen_schema(conn)
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS unread,
               SUM(CASE WHEN file_url <> '' OR attachment_url <> '' THEN 1 ELSE 0 END) AS with_file,
               MAX(synced_at) AS last_synced_at
        FROM gongwen_documents
        WHERE teacher_id = ?
        """,
        (int(teacher_id),),
    ).fetchone()
    data = dict(row) if row else {}
    total = int(data.get("total") or 0)
    last_synced_at = str(data.get("last_synced_at") or "")
    return [
        {
            "key": "documents",
            "label": "公文收件箱",
            "description": "增量同步统一认证账号收到的公文（按公文编号匹配、分页限速），含文号、发文单位、分类与附件。每 6 小时按账号错峰自动同步一次。",
            "endpoint": "GET /user/doc/page",
            "method": "GET",
            "scope": "当前账号收件箱内的公文（增量）",
            "has_synced": total > 0,
            "last_synced_at": last_synced_at,
            "status_text": "已同步" if total > 0 else "未同步",
            "stats": [
                {"label": "公文", "value": total},
                {"label": "未读", "value": int(data.get("unread") or 0)},
                {"label": "含附件", "value": int(data.get("with_file") or 0)},
            ],
            "safe_note": "增量、限速、错峰，仅读取列表与公开附件，不回写/标记已读/反馈。",
        }
    ]


def serialize_gongwen_document(row: Any, *, include_content: bool = False) -> dict[str, Any]:
    item = dict(row)
    payload = {
        "id": int(item["id"]),
        "remote_id": str(item.get("remote_id") or ""),
        "sn": str(item.get("sn") or ""),
        "title": str(item.get("title") or ""),
        "subhead": str(item.get("subhead") or ""),
        "author": str(item.get("author") or ""),
        "sender_name": str(item.get("sender_name") or ""),
        "category_name": str(item.get("category_name") or ""),
        "summary": str(item.get("summary") or ""),
        "keywords": str(item.get("keywords") or ""),
        "file_url": str(item.get("file_url") or ""),
        "attachment_url": str(item.get("attachment_url") or ""),
        "has_local_file": bool(str(item.get("local_file_path") or "")),
        "has_local_attachment": bool(str(item.get("local_attachment_path") or "")),
        "file_download_status": str(item.get("file_download_status") or "idle"),
        "is_read": bool(item.get("is_read")),
        "is_fav": bool(item.get("is_fav")),
        "is_need_feedback": bool(item.get("is_need_feedback")),
        "publish_time": str(item.get("publish_time") or ""),
        "synced_at": str(item.get("synced_at") or ""),
    }
    if include_content:
        payload["content_html"] = str(item.get("content_html") or "")
        payload["content_text"] = str(item.get("content_text") or "")
    return payload


def list_teacher_gongwen_documents(
    conn,
    teacher_id: int,
    *,
    keyword: str = "",
    category: str = "",
    unread_only: bool = False,
    favorite_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    ensure_gongwen_schema(conn)
    where = ["teacher_id = ?"]
    params: list[Any] = [int(teacher_id)]
    keyword = str(keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        where.append("(title LIKE ? OR sn LIKE ? OR author LIKE ? OR content_text LIKE ? OR keywords LIKE ?)")
        params.extend([like, like, like, like, like])
    if category:
        where.append("category_name = ?")
        params.append(str(category))
    if unread_only:
        where.append("is_read = 0")
    if favorite_only:
        where.append("is_fav = 1")
    where_sql = " AND ".join(where)
    total = int(
        (conn.execute(f"SELECT COUNT(*) AS c FROM gongwen_documents WHERE {where_sql}", params).fetchone() or {"c": 0})["c"]
    )
    rows = conn.execute(
        f"""
        SELECT * FROM gongwen_documents
        WHERE {where_sql}
        ORDER BY publish_time DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, int(limit), int(offset)],
    ).fetchall()
    return {
        "total": total,
        "documents": [serialize_gongwen_document(row) for row in rows],
    }


def count_teacher_gongwen_documents(conn, teacher_id: int) -> dict[str, Any]:
    ensure_gongwen_schema(conn)
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS unread,
               SUM(CASE WHEN is_fav = 1 THEN 1 ELSE 0 END) AS favorite,
               SUM(CASE WHEN file_url <> '' OR attachment_url <> '' THEN 1 ELSE 0 END) AS with_file,
               MAX(synced_at) AS last_synced_at
        FROM gongwen_documents
        WHERE teacher_id = ?
        """,
        (int(teacher_id),),
    ).fetchone()
    data = dict(row) if row else {}
    return {
        "total": int(data.get("total") or 0),
        "unread": int(data.get("unread") or 0),
        "favorite": int(data.get("favorite") or 0),
        "with_file": int(data.get("with_file") or 0),
        "last_synced_at": str(data.get("last_synced_at") or ""),
    }


def list_teacher_gongwen_categories(conn, teacher_id: int) -> list[dict[str, Any]]:
    ensure_gongwen_schema(conn)
    rows = conn.execute(
        """
        SELECT category_name AS name, COUNT(*) AS total
        FROM gongwen_documents
        WHERE teacher_id = ? AND category_name <> ''
        GROUP BY category_name
        ORDER BY total DESC, category_name ASC
        """,
        (int(teacher_id),),
    ).fetchall()
    return [{"name": str(dict(r)["name"]), "total": int(dict(r)["total"])} for r in rows]


# --------------------------------------------------------------------------- #
# Retrieval + search interfaces (reserved for the 公文内容提醒 feature)
# --------------------------------------------------------------------------- #


def get_gongwen_document_content(conn, teacher_id: int, document_id: int) -> dict[str, Any] | None:
    """Return a single document with its full content for reminder/AI use."""
    ensure_gongwen_schema(conn)
    row = conn.execute(
        "SELECT * FROM gongwen_documents WHERE id = ? AND teacher_id = ? LIMIT 1",
        (int(document_id), int(teacher_id)),
    ).fetchone()
    if row is None:
        return None
    return serialize_gongwen_document(row, include_content=True)


def search_gongwen_documents(
    conn,
    teacher_id: int,
    query: str = "",
    *,
    category: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Keyword search across the teacher's synced documents (reminders/retrieval)."""
    result = list_teacher_gongwen_documents(
        conn,
        teacher_id,
        keyword=query,
        category=category,
        limit=limit,
    )
    return result["documents"]
