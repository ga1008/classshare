"""Sync the campus 校园公文通 inbox into ``gongwen_documents`` (campus-scoped).

公文 are shared per campus: deduped by ``(attr_school_code, system_code, remote_id)``
so a document is stored once no matter how many teachers in that school sync it.
Visibility within the campus uses the unified 归属 + 开放范围 model
(``material_scope_service``): documents default to school-attributed / 本校可见,
and a teacher can re-attribute a document to a 学院 / 系部 and narrow its openness.

Sync is metadata-only + incremental (see methods); attachments are public on the
CDN and cached lazily on first download.
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
from . import material_scope_service as ms
from .gongwen_integration_service import (
    get_gongwen_system_profile,
    load_teacher_gongwen_access_method,
    open_authenticated_gongwen_client,
)
from .organization_scope_service import load_teacher_org_scope, normalize_school_code


GONGWEN_ATTACHMENT_DIR = Path(DATA_DIR) / "gongwen_attachments"
PAGE_SIZE = 50
MAX_PAGES = 40
MAX_DOWNLOAD_BYTES = 60 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60.0
PAGE_DELAY_SECONDS = 0.4
ALLOWED_FILE_HOSTS = {"doc.gxufl.com", "doc_api.gxufl.com"}

GONGWEN_SYNC_TASK_KIND = "gongwen_incremental_sync"
GONGWEN_SYNC_INTERVAL_SECONDS = 6 * 3600
GONGWEN_SYNC_STAGGER_MINUTES = 180


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_allowed_file_url(url: Any) -> bool:
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


def _upsert_document(
    conn,
    *,
    school_code: str,
    school_name: str,
    system_code: str,
    synced_by_teacher_id: int | None,
    synced_by_credential_id: int | None,
    fields: dict[str, Any],
    raw_item: dict[str, Any],
) -> dict[str, Any] | None:
    """Campus-scoped upsert. New docs default to school-attributed / 本校可见;
    on conflict the content/state is refreshed but the (possibly overridden)
    attribution + openness are preserved."""
    if not fields["remote_id"]:
        return None
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO gongwen_documents (
            system_code, remote_id, attr_school_code, attr_school_name, attr_college,
            attr_department, attr_level, openness, synced_by_teacher_id, synced_by_credential_id,
            sn, title, subhead, author, sender_name, category_id, category_name, summary,
            content_html, content_text, keywords, tags, source, link, source_link, cover_url,
            file_url, attachment_url, is_read, is_fav, is_need_feedback, publish_time,
            remote_created_at, remote_updated_at, raw_json, synced_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, '', '', 'school', 'school', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(attr_school_code, system_code, remote_id) DO UPDATE SET
            attr_school_name = excluded.attr_school_name,
            synced_by_teacher_id = excluded.synced_by_teacher_id,
            synced_by_credential_id = excluded.synced_by_credential_id,
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
            system_code, fields["remote_id"], school_code, school_name,
            synced_by_teacher_id, synced_by_credential_id,
            fields["sn"], fields["title"], fields["subhead"], fields["author"], fields["sender_name"],
            fields["category_id"], fields["category_name"], fields["summary"], fields["content_html"],
            fields["content_text"], fields["keywords"], fields["tags"], fields["source"], fields["link"],
            fields["source_link"], fields["cover_url"], fields["file_url"], fields["attachment_url"],
            fields["is_read"], fields["is_fav"], fields["is_need_feedback"], fields["publish_time"],
            fields["remote_created_at"], fields["remote_updated_at"],
            json.dumps(raw_item, ensure_ascii=False)[:200000], now, now, now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM gongwen_documents WHERE attr_school_code = ? AND system_code = ? AND remote_id = ? LIMIT 1",
        (school_code, system_code, fields["remote_id"]),
    ).fetchone()
    return dict(row) if row else None


def _load_existing_remote_ids(conn, school_code: str, system_code: str) -> set[str]:
    rows = conn.execute(
        "SELECT remote_id FROM gongwen_documents WHERE attr_school_code = ? AND system_code = ?",
        (school_code, system_code),
    ).fetchall()
    return {str(dict(r)["remote_id"]) for r in rows}


async def sync_current_teacher_gongwen_documents(teacher_id: int, *, full: bool = False) -> dict[str, Any]:
    """Incrementally sync the teacher's inbox into the *campus* document pool.

    Matched by stable ``remote_id``; stops once a whole page is already known
    (incremental); paced by ``PAGE_DELAY_SECONDS``. Empty pool ⇒ full sweep.
    """
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        access = load_teacher_gongwen_access_method(conn, teacher_id)
        org = load_teacher_org_scope(conn, teacher_id)
    if not access:
        return {"status": "missing_credential", "message": "尚未保存并验证校园公文通账号，无法同步公文。", "counts": {}, "warnings": []}

    profile = get_gongwen_system_profile(access.get("system_code") or "gxufl")
    school_code = normalize_school_code(org.get("school_code"))
    school_name = str(org.get("school_name") or "")
    credential_id = access.get("credential_id")
    with get_db_connection() as conn:
        known_ids = _load_existing_remote_ids(conn, school_code, profile.system_code)
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
                        row = _upsert_document(
                            conn,
                            school_code=school_code,
                            school_name=school_name,
                            system_code=profile.system_code,
                            synced_by_teacher_id=int(teacher_id),
                            synced_by_credential_id=credential_id,
                            fields=fields,
                            raw_item=item,
                        )
                        if row:
                            counts["stored"] += 1
                    conn.commit()

                if result.get("lastPage"):
                    break
                if not full and known_ids and new_in_page == 0:
                    break
                page_no += 1
                await asyncio.sleep(PAGE_DELAY_SECONDS)
            if page_no > MAX_PAGES:
                warnings.append(f"公文数量较多，本次最多读取 {MAX_PAGES * PAGE_SIZE} 条，可再次同步继续。")
    except ValueError as exc:
        return {"status": "failed", "message": str(exc), "counts": counts, "warnings": warnings}
    except httpx.HTTPError as exc:
        return {"status": "failed", "message": f"同步公文时连接异常：{str(exc)[:160]}", "counts": counts, "warnings": warnings}

    status = "success" if counts["stored"] else "empty"
    message = (
        f"{mode}同步完成（{school_name or school_code}）：新增 {counts['new']} 条，更新 {counts['stored']} 条。附件首次下载时按需缓存。"
        if counts["stored"]
        else "未发现可同步的公文。"
    )
    return {"status": status, "message": message, "counts": counts, "warnings": warnings}


async def ensure_local_attachment(
    teacher_scope: dict[str, str],
    document_id: int,
    which: str = "primary",
    *,
    is_super_admin: bool = False,
) -> dict[str, Any]:
    """Lazily cache a visible document's public attachment; never raises."""
    column = "local_file_path" if which != "attachment" else "local_attachment_path"
    url_column = "file_url" if which != "attachment" else "attachment_url"
    with get_db_connection() as conn:
        ensure_gongwen_schema(conn)
        row = conn.execute(
            "SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1",
            (int(document_id),),
        ).fetchone()
    if row is None:
        return {"status": "not_found"}
    data = dict(row)
    if not ms.can_view(data, teacher_scope, is_super_admin=is_super_admin):
        return {"status": "not_found"}
    local_path = str(data.get(column) or "")
    remote_url = str(data.get(url_column) or "")
    if local_path and Path(local_path).exists():
        return {"status": "local", "local_path": local_path}
    if not remote_url or not is_allowed_file_url(remote_url):
        return {"status": "no_file"}

    dest = GONGWEN_ATTACHMENT_DIR / normalize_school_code(data.get("attr_school_code")) / _safe_filename(
        remote_url, f"{data.get('remote_id')}_{which}"
    )
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
                "file_download_error = '', updated_at = ? WHERE id = ?",
                (str(dest), _now_iso(), int(document_id)),
            )
            conn.commit()
        return {"status": "local", "local_path": str(dest)}
    except (httpx.HTTPError, ValueError, OSError) as exc:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE gongwen_documents SET file_download_status = 'failed', file_download_error = ?, "
                "updated_at = ? WHERE id = ?",
                (str(exc)[:300], _now_iso(), int(document_id)),
            )
            conn.commit()
        return {"status": "redirect", "remote_url": remote_url, "message": str(exc)[:160]}


# --------------------------------------------------------------------------- #
# Scheduled auto-sync (recurring, staggered, incremental)
# --------------------------------------------------------------------------- #


def schedule_gongwen_auto_sync(conn, teacher_id: int) -> int:
    from .scheduled_task_service import schedule_task

    teacher_id = int(teacher_id)
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


# --------------------------------------------------------------------------- #
# Visibility-filtered listing / retrieval (uses material_scope_service)
# --------------------------------------------------------------------------- #


def serialize_gongwen_document(row: Any, *, include_content: bool = False) -> dict[str, Any]:
    item = dict(row)
    summary = ms.scope_summary(item)
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
        "attr_school_code": str(item.get("attr_school_code") or ""),
        "attr_college": str(item.get("attr_college") or ""),
        "attr_department": str(item.get("attr_department") or ""),
        "attr_level": summary["attr_level"],
        "attr_level_label": summary["attr_level_label"],
        "attribution_label": summary["attribution_label"],
        "openness": summary["openness"],
        "openness_label": summary["openness_label"],
        "scope_overridden": bool(item.get("scope_overridden")),
    }
    if include_content:
        payload["content_html"] = str(item.get("content_html") or "")
        payload["content_text"] = str(item.get("content_text") or "")
    return payload


def list_visible_gongwen_documents(
    conn,
    teacher_scope: dict[str, str],
    *,
    is_super_admin: bool = False,
    keyword: str = "",
    category: str = "",
    unread_only: bool = False,
    favorite_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    ensure_gongwen_schema(conn)
    visibility_sql, params = ms.build_visibility_filter(teacher_scope, is_super_admin=is_super_admin)
    where = [visibility_sql]
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
    total = int((conn.execute(f"SELECT COUNT(*) AS c FROM gongwen_documents WHERE {where_sql}", params).fetchone() or {"c": 0})["c"])
    rows = conn.execute(
        f"SELECT * FROM gongwen_documents WHERE {where_sql} ORDER BY publish_time DESC, id DESC LIMIT ? OFFSET ?",
        [*params, int(limit), int(offset)],
    ).fetchall()
    return {"total": total, "documents": [serialize_gongwen_document(row) for row in rows]}


def count_visible_gongwen_documents(conn, teacher_scope: dict[str, str], *, is_super_admin: bool = False) -> dict[str, Any]:
    ensure_gongwen_schema(conn)
    visibility_sql, params = ms.build_visibility_filter(teacher_scope, is_super_admin=is_super_admin)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) AS unread,
               SUM(CASE WHEN is_fav = 1 THEN 1 ELSE 0 END) AS favorite,
               SUM(CASE WHEN file_url <> '' OR attachment_url <> '' THEN 1 ELSE 0 END) AS with_file,
               MAX(synced_at) AS last_synced_at
        FROM gongwen_documents WHERE {visibility_sql}
        """,
        params,
    ).fetchone()
    data = dict(row) if row else {}
    return {
        "total": int(data.get("total") or 0),
        "unread": int(data.get("unread") or 0),
        "favorite": int(data.get("favorite") or 0),
        "with_file": int(data.get("with_file") or 0),
        "last_synced_at": str(data.get("last_synced_at") or ""),
    }


def list_visible_gongwen_categories(conn, teacher_scope: dict[str, str], *, is_super_admin: bool = False) -> list[dict[str, Any]]:
    ensure_gongwen_schema(conn)
    visibility_sql, params = ms.build_visibility_filter(teacher_scope, is_super_admin=is_super_admin)
    rows = conn.execute(
        f"""
        SELECT category_name AS name, COUNT(*) AS total
        FROM gongwen_documents
        WHERE {visibility_sql} AND category_name <> ''
        GROUP BY category_name ORDER BY total DESC, category_name ASC
        """,
        params,
    ).fetchall()
    return [{"name": str(dict(r)["name"]), "total": int(dict(r)["total"])} for r in rows]


def build_gongwen_sync_capabilities(conn, teacher_scope: dict[str, str], *, is_super_admin: bool = False) -> list[dict[str, Any]]:
    summary = count_visible_gongwen_documents(conn, teacher_scope, is_super_admin=is_super_admin)
    total = summary["total"]
    return [
        {
            "key": "documents",
            "label": "公文收件箱",
            "description": "按校区增量同步公文（按公文编号匹配、分页限速、每校区只存一份），含文号、发文单位、分类与附件。每 6 小时按账号错峰自动同步。",
            "endpoint": "GET /user/doc/page",
            "method": "GET",
            "scope": "本校区可见的公文",
            "has_synced": total > 0,
            "last_synced_at": summary["last_synced_at"],
            "status_text": "已同步" if total > 0 else "未同步",
            "stats": [
                {"label": "可见公文", "value": total},
                {"label": "未读", "value": summary["unread"]},
                {"label": "含附件", "value": summary["with_file"]},
            ],
            "safe_note": "增量、限速、错峰、校区去重，仅读取列表与公开附件，不回写/标记已读/反馈。",
        }
    ]


def get_visible_gongwen_document(conn, teacher_scope: dict[str, str], document_id: int, *, is_super_admin: bool = False) -> dict[str, Any] | None:
    """Retrieval interface — returns a visible document with full content."""
    ensure_gongwen_schema(conn)
    row = conn.execute("SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)).fetchone()
    if row is None:
        return None
    data = dict(row)
    if not ms.can_view(data, teacher_scope, is_super_admin=is_super_admin):
        return None
    return serialize_gongwen_document(row, include_content=True)


def search_visible_gongwen_documents(
    conn,
    teacher_scope: dict[str, str],
    query: str = "",
    *,
    is_super_admin: bool = False,
    category: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Keyword search interface (reminders/retrieval) within visible scope."""
    return list_visible_gongwen_documents(
        conn, teacher_scope, is_super_admin=is_super_admin, keyword=query, category=category, limit=limit
    )["documents"]


def set_gongwen_document_scope(
    conn,
    teacher_scope: dict[str, str],
    document_id: int,
    *,
    college: Any = "",
    department: Any = "",
    openness: Any = "",
    is_super_admin: bool = False,
) -> dict[str, Any]:
    """Set a document's 归属 (学院/系部) and 开放范围. Only a teacher in the same
    campus (or a super admin) may edit; the school attribution is immutable."""
    ensure_gongwen_schema(conn)
    row = conn.execute("SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)).fetchone()
    if row is None:
        raise ValueError("公文不存在。")
    data = dict(row)
    doc_school = normalize_school_code(data.get("attr_school_code"))
    if not is_super_admin and doc_school != normalize_school_code(teacher_scope.get("school_code")):
        raise ValueError("只能调整本校区公文的归属与开放范围。")

    normalized = ms.normalize_scope(
        school_code=doc_school,
        college=college,
        department=department,
        openness=openness,
        default_openness=ms.LEVEL_SCHOOL,
    )
    conn.execute(
        """
        UPDATE gongwen_documents
        SET attr_college = ?, attr_department = ?, attr_level = ?, openness = ?,
            scope_overridden = 1, updated_at = ?
        WHERE id = ?
        """,
        (
            normalized["attr_college"], normalized["attr_department"], normalized["attr_level"],
            normalized["openness"], _now_iso(), int(document_id),
        ),
    )
    updated = conn.execute("SELECT * FROM gongwen_documents WHERE id = ? LIMIT 1", (int(document_id),)).fetchone()
    return serialize_gongwen_document(updated, include_content=False)


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
    result = await sync_current_teacher_gongwen_documents(teacher_id)
    payload = _build_auto_sync_payload(result)
    payload["counts"] = result.get("counts") or {}
    payload["warnings"] = result.get("warnings") or []
    return payload
