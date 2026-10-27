"""聊天媒体：预检去重、分块上传、配额、令牌、下载鉴权、局域网补传 (social doc §6).

Flow: the client hashes the file (SHA-256) and calls precheck. A hash alone
proves nothing, so precheck only answers ``exists`` (with a single-use
``media_token``, no quota cost) when the requester can already read those
bytes: a live social-store attachment in a conversation they currently belong
to, or their own completed upload of the same bytes. The global store is never
reachable through precheck. Otherwise the client uploads 1 MB chunks into one
session (≤ 1 active per person); ``complete`` re-hashes the whole file, rejects
a mismatch and publishes atomically — reusing bytes already on disk (social or
global store) instead of writing a second copy, and charging the daily quota
only for bytes newly written. Tokens are redeemed when a message is sent
(``attach_media``). Token issue, attach, publish and the retention purge take
the same per-blob lock (``social_media_store.lock_blobs``).

Uploads: chunks are validated (session + owner) before any limiter; each
person may have one chunk in flight (concurrent → 429); a token bucket
(≈ 2 MB/s, burst 2 chunks) rejects excess with 429 + Retry-After instead of
sleeping; the global upload slot is acquired with a short timeout (503).

Limits come from ``SOCIAL_*`` environment variables (docker.env.example).
Single-worker assumption: the upload throttle and concurrency slots are
in-process, which is exact for the single production app worker.
"""
from __future__ import annotations

import asyncio
import math
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from . import social_media_store as store
from .file_service import lock_global_file_references, resolve_global_file_path
from .social_common import (
    SocialError,
    add_event,
    as_int,
    env_int,
    ensure_schema,
    iso_after,
    iso_before,
    now_iso,
    parse_iso,
    row_dict,
    utc_date,
    utc_now,
    user_identity,
)

CHUNK_SIZE = 1024 * 1024
MAX_ATTACHMENTS = 9
TOKEN_TTL_HOURS = 24
SESSION_STALE_SECONDS = 3600
FILENAME_MAX = 120
SLOT_WAIT_SECONDS = 30.0
UPLOAD_SLOT_WAIT_SECONDS = 2.0
UPLOAD_BURST_CHUNKS = 2
PRECHECK_SCAN_LIMIT = 50
_MIME_PATTERN = re.compile("^[a-z0-9.+-]+/[a-z0-9.+-]+$")
LAN_STATUSES = ("p2p_done", "p2p_failed")
_MIB = 1024 * 1024


def file_limit_bytes() -> int:
    return env_int("SOCIAL_MEDIA_FILE_LIMIT_MB", 100, low=1, high=2048) * _MIB


def daily_limit_bytes() -> int:
    return env_int("SOCIAL_MEDIA_DAILY_LIMIT_MB", 200, low=1, high=100_000) * _MIB


def retention_days() -> int:
    return env_int("SOCIAL_MEDIA_RETENTION_DAYS", 365, low=1, high=3650)


def upload_rate_bytes() -> int:
    return env_int("SOCIAL_UPLOAD_KBPS_PER_USER", 2048, low=64, high=1_000_000) * 1024


def download_rate_bytes() -> int:
    return env_int("SOCIAL_DOWNLOAD_KBPS_PER_CONNECTION", 4096, low=64, high=1_000_000) * 1024


def media_kind(mime: str) -> str:
    major = str(mime or "").split("/", 1)[0].lower()
    return major if major in {"image", "video", "audio"} else "file"


def _clean_filename(raw: Any) -> str:
    name = str(raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(ch for ch in name if ch >= " " and ch not in '<>:"|?*')
    return (name or "file")[:FILENAME_MAX]


def _clean_mime(raw: Any) -> str:
    text = str(raw or "").strip().lower()[:100]
    return text if _MIME_PATTERN.match(text) else "application/octet-stream"


def _validate_spec(sha256: Any, size: Any) -> tuple[str, int]:
    digest = str(sha256 or "").strip().lower()
    if not store.valid_hash(digest):
        raise SocialError("文件校验值无效。", 400)
    length = as_int(size)
    if length <= 0:
        raise SocialError("文件大小无效。", 400)
    if length > file_limit_bytes():
        raise SocialError(f"单个文件不能超过 {file_limit_bytes() // _MIB} MB。", 413)
    return digest, length


# ---------------------------------------------------------------------------
# Throttle and concurrency (in-process; single worker)
# ---------------------------------------------------------------------------

class ByteRateLimiter:
    """Per-key token bucket (bytes). ``take`` never sleeps: over the rate it raises 429 + Retry-After.

    Also tracks one in-flight chunk per key (``begin``/``end``), so a person cannot
    pile up concurrent chunk requests.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, float]] = {}
        self._in_flight: set[str] = set()

    def take(self, key: str, size: int, rate: int, *, now: Optional[float] = None) -> None:
        moment = time.monotonic() if now is None else now
        capacity = max(float(size), float(rate), float(CHUNK_SIZE * UPLOAD_BURST_CHUNKS))
        tokens, stamp = self._buckets.get(key, (capacity, moment))
        tokens = min(capacity, tokens + max(0.0, moment - stamp) * max(1, rate))
        if tokens < size:
            self._buckets[key] = (tokens, moment)
            retry = max(1, math.ceil((size - tokens) / max(1, rate)))
            raise SocialError("上传太快了，请稍后继续。", 429, retry_after=retry)
        self._buckets[key] = (tokens - size, moment)

    def begin(self, key: str) -> None:
        if key in self._in_flight:
            raise SocialError("上一个分块还在处理中，请稍后重试。", 429, retry_after=1)
        self._in_flight.add(key)

    def end(self, key: str) -> None:
        self._in_flight.discard(key)


upload_limiter = ByteRateLimiter()
_slot_registry: dict[str, asyncio.Semaphore] = {}


def _slots(kind: str) -> asyncio.Semaphore:
    if kind not in _slot_registry:
        limit = (env_int("SOCIAL_UPLOAD_CONCURRENCY", 6, low=1, high=64) if kind == "upload"
                 else env_int("SOCIAL_DOWNLOAD_CONCURRENCY", 40, low=1, high=512))
        _slot_registry[kind] = asyncio.Semaphore(limit)
    return _slot_registry[kind]


@asynccontextmanager
async def transfer_slot(kind: str) -> AsyncIterator[None]:
    """A global upload (≤ 6, 2 s wait) or download (≤ 40, 30 s wait) slot; 503 after the wait."""
    semaphore = _slots(kind)
    wait = UPLOAD_SLOT_WAIT_SECONDS if kind == "upload" else SLOT_WAIT_SECONDS
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=wait)
    except asyncio.TimeoutError as exc:
        raise SocialError("服务器繁忙，请稍后重试。", 503) from exc
    try:
        yield
    finally:
        semaphore.release()


# ---------------------------------------------------------------------------
# Quota and tokens
# ---------------------------------------------------------------------------

def used_today(conn: Any, identity: str) -> int:
    ensure_schema(conn)
    row = conn.execute("SELECT bytes_uploaded FROM social_upload_usage WHERE identity = ? AND usage_date = ?",
                       (identity, utc_date())).fetchone()
    return as_int(row["bytes_uploaded"]) if row else 0


def _charge(conn: Any, identity: str, size: int) -> None:
    conn.execute(
        "INSERT INTO social_upload_usage (identity, usage_date, bytes_uploaded) VALUES (?, ?, ?) "
        "ON CONFLICT (identity, usage_date) DO UPDATE SET "
        "bytes_uploaded = social_upload_usage.bytes_uploaded + excluded.bytes_uploaded",
        (identity, utc_date(), int(size)),
    )


def quota(conn: Any, user: dict) -> dict[str, int]:
    me = user_identity(user)
    return {"daily_limit": daily_limit_bytes(), "used_today": used_today(conn, me), "file_limit": file_limit_bytes()}


def global_hash_for(file_store: str, file_hash: str) -> str:
    """The value of the ``global_file_hash`` reference column: set only for the global store."""
    return str(file_hash) if file_store == "global" else ""


def bind_global_hashes(conn: Any, hashes: Any) -> None:
    """Global-store reference protocol: lock the complete, sorted hash set before the first reference write.

    The bytes must still exist; a collector that won the race yields a 409 so the
    client re-selects the file (docs/global-file-reference-protocol.md).
    """
    wanted = sorted({str(item) for item in hashes or () if item})
    if not wanted:
        return
    try:
        lock_global_file_references(conn, wanted)
    except ValueError as exc:
        raise SocialError(str(exc), 409) from exc


def issue_token(conn: Any, owner: str, file_hash: str, file_store: str, filename: str, mime: str, size: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO social_media_tokens (token, owner_identity, blob_hash, store, original_filename, mime_type, "
        "file_size, global_file_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (token, owner, file_hash, file_store, filename, mime, int(size), global_hash_for(file_store, file_hash),
         now_iso()),
    )
    return token


def _global_has(file_hash: str, size: int) -> bool:
    path = resolve_global_file_path(file_hash)
    return bool(path and path.is_file() and path.stat().st_size == int(size))


def _own_completed_upload(conn: Any, me: str, digest: str, length: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM social_upload_sessions WHERE owner_identity = ? AND sha256 = ? AND size = ? "
        "AND status = 'completed' LIMIT 1", (me, digest, int(length)),
    ).fetchone()
    return row is not None


def _readable_attachment(conn: Any, me: str, digest: str) -> bool:
    """A live social-store attachment with these bytes in a conversation ``me`` can read now."""
    rows = conn.execute(
        "SELECT m.id, msg.conversation_key, msg.created_at AS message_created_at, msg.recalled_at "
        "FROM social_message_media m JOIN social_messages msg ON msg.id = m.message_id "
        "WHERE m.blob_hash = ? AND m.store = 'social' AND m.server_ready = 1 AND m.expired_at IS NULL "
        "AND msg.recalled_at IS NULL ORDER BY m.id DESC LIMIT ?", (digest, PRECHECK_SCAN_LIMIT),
    ).fetchall()
    for row in rows:
        try:
            _require_media_access(conn, me, row_dict(row))
        except SocialError:
            continue
        return True
    return False


def precheck(conn: Any, user: dict, *, sha256: Any, size: Any, mime: Any, filename: Any) -> dict[str, Any]:
    """``{exists:true, media_token}`` only for bytes the requester can already read (see module doc)."""
    me = user_identity(user)
    digest, length = _validate_spec(sha256, size)
    name, kind = _clean_filename(filename), _clean_mime(mime)
    ensure_schema(conn)
    if not (_own_completed_upload(conn, me, digest, length) or _readable_attachment(conn, me, digest)):
        return {"exists": False}
    store.lock_blobs(conn, [digest])
    if not store.blob_available(conn, digest, length):
        return {"exists": False}
    return {"exists": True, "media_token": issue_token(conn, me, digest, "social", name, kind, length)}


def _token_row(conn: Any, token: str, owner: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM social_media_tokens WHERE token = ? LIMIT 1", (token,)).fetchone()
    item = row_dict(row) if row is not None else {}
    created = parse_iso(item.get("created_at"))
    expired = created is None or (utc_now() - created).total_seconds() > TOKEN_TTL_HOURS * 3600
    if not item or item["owner_identity"] != owner or item.get("used_at") or expired:
        raise SocialError("附件已失效，请重新选择文件。", 410)
    return item


def redeem_tokens(conn: Any, owner: str, tokens: Any) -> list[dict[str, Any]]:
    """Validate and consume tokens (single use, owner-bound, ≤ 24 h, ≤ 9 per message)."""
    if tokens in (None, ""):
        return []
    if not isinstance(tokens, (list, tuple)):
        raise SocialError("附件格式不正确。", 400)
    wanted = list(dict.fromkeys(str(item or "").strip() for item in tokens if str(item or "").strip()))
    if len(wanted) > MAX_ATTACHMENTS:
        raise SocialError(f"一条消息最多 {MAX_ATTACHMENTS} 个附件。", 400)
    ensure_schema(conn)
    rows = [_token_row(conn, token, owner) for token in wanted]
    for item in rows:
        # The reference moves from the token to the media row written in the same transaction.
        updated = conn.execute(
            "UPDATE social_media_tokens SET used_at = ?, global_file_hash = '' WHERE token = ? AND used_at IS NULL",
            (now_iso(), item["token"]),
        )
        if as_int(getattr(updated, "rowcount", 1), 1) == 0:
            raise SocialError("附件已失效，请重新选择文件。", 410)
    return rows


def _insert_media(conn: Any, message_id: int, *, file_hash: str, file_store: str, filename: str, mime: str,
                  size: int, delivered_via: str = "server", server_ready: int = 1) -> int:
    from ..db.connection import execute_insert_returning_id

    return execute_insert_returning_id(
        conn,
        "INSERT INTO social_message_media (message_id, blob_hash, store, original_filename, mime_type, file_size, "
        "media_kind, global_file_hash, delivered_via, server_ready, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(message_id), file_hash, file_store, filename, mime, int(size), media_kind(mime),
         global_hash_for(file_store, file_hash), delivered_via, int(server_ready), iso_after(days=retention_days()),
         now_iso()),
    )


def _require_social_blobs(conn: Any, hashes: list[str]) -> None:
    """Under the blob lock: every social-store blob must still be on disk (purge may have won)."""
    store.lock_blobs(conn, hashes)
    for file_hash in hashes:
        blob = store.load_blob(conn, file_hash)
        if not blob or blob["storage_state"] == store.STATE_DELETED or not store.blob_path(file_hash).is_file():
            raise SocialError("附件已失效，请重新选择文件。", 410)


def attach_media(conn: Any, message_id: int, token_rows: list[dict[str, Any]]) -> list[int]:
    bind_global_hashes(conn, [item["blob_hash"] for item in token_rows if item["store"] == "global"])
    _require_social_blobs(conn, sorted({item["blob_hash"] for item in token_rows if item["store"] == "social"}))
    ids = []
    for item in token_rows:
        ids.append(_insert_media(conn, message_id, file_hash=item["blob_hash"], file_store=item["store"],
                                 filename=item["original_filename"], mime=item["mime_type"], size=item["file_size"]))
        if item["store"] == "social":
            store.add_blob_ref(conn, item["blob_hash"], 1)
    return ids


def attach_lan_media(conn: Any, message_id: int, sender: str, receiver: str, specs: Any) -> list[int]:
    """Media announced for LAN direct transfer: not on the server until ``backfill``."""
    if not isinstance(specs, (list, tuple)):
        raise SocialError("局域网附件格式不正确。", 400)
    ids = []
    for spec in specs:
        spec = spec if isinstance(spec, dict) else {}
        digest, length = _validate_spec(spec.get("sha256"), spec.get("size"))
        media_id = _insert_media(conn, message_id, file_hash=digest, file_store="social",
                                 filename=_clean_filename(spec.get("filename")), mime=_clean_mime(spec.get("mime")),
                                 size=length, delivered_via="lan", server_ready=0)
        timestamp = now_iso()
        conn.execute(
            "INSERT INTO social_lan_transfers (message_id, media_id, sender_identity, receiver_identity, blob_hash, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'offered', ?, ?)",
            (int(message_id), media_id, sender, receiver, digest, timestamp, timestamp),
        )
        ids.append(media_id)
    return ids


# ---------------------------------------------------------------------------
# Chunked upload
# ---------------------------------------------------------------------------

def _load_session(conn: Any, upload_id: str, owner: str) -> dict[str, Any]:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM social_upload_sessions WHERE upload_id = ? LIMIT 1", (str(upload_id),)).fetchone()
    session = row_dict(row) if row is not None else {}
    if not session or session["owner_identity"] != owner:
        raise SocialError("上传会话不存在。", 404)
    if session["status"] != "uploading":
        raise SocialError("上传会话已结束，请重新上传。", 410)
    return session


def check_upload_session(conn: Any, user: dict, upload_id: str) -> dict[str, Any]:
    """Cheap pre-flight for a chunk: the session exists, is live and belongs to the caller."""
    return _session_payload(_load_session(conn, upload_id, user_identity(user)))


def expire_session(conn: Any, session: dict[str, Any], status: str = "expired") -> None:
    try:
        Path(session["temp_path"]).unlink(missing_ok=True)
    except OSError as exc:
        print(f"[SOCIAL_MEDIA] temp cleanup failed for {session['upload_id']}: {exc!r}")
    conn.execute("UPDATE social_upload_sessions SET status = ?, updated_at = ? WHERE upload_id = ?",
                 (status, now_iso(), session["upload_id"]))


def _session_payload(session: dict[str, Any]) -> dict[str, Any]:
    return {"upload_id": session["upload_id"], "chunk_size": as_int(session["chunk_size"], CHUNK_SIZE),
            "received_bytes": as_int(session["received_bytes"])}


def _is_stale(session: dict[str, Any]) -> bool:
    updated = parse_iso(session.get("updated_at")) or utc_now()
    return (utc_now() - updated).total_seconds() > SESSION_STALE_SECONDS


def upload_init(conn: Any, user: dict, *, sha256: Any, size: Any, mime: Any, filename: Any) -> dict[str, Any]:
    me = user_identity(user)
    digest, length = _validate_spec(sha256, size)
    ensure_schema(conn)
    for row in conn.execute("SELECT * FROM social_upload_sessions WHERE owner_identity = ? AND status = 'uploading'",
                            (me,)).fetchall():
        session = row_dict(row)
        if session["sha256"] == digest and as_int(session["size"]) == length and not _is_stale(session):
            return _session_payload(session)  # resume
        if not _is_stale(session):
            raise SocialError("已有文件正在上传，请等它完成后再发送下一个。", 409)
        expire_session(conn, session)
    if used_today(conn, me) + length > daily_limit_bytes():
        raise SocialError(f"今日上传额度（{daily_limit_bytes() // _MIB} MB）已用完，请明天再试。", 429)
    upload_id = uuid.uuid4().hex
    temp_path = store.upload_temp_path(upload_id)
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(b"")
    timestamp = now_iso()
    conn.execute(
        "INSERT INTO social_upload_sessions (upload_id, owner_identity, sha256, size, mime, filename, received_bytes, "
        "chunk_size, temp_path, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 'uploading', ?, ?)",
        (upload_id, me, digest, length, _clean_mime(mime), _clean_filename(filename), CHUNK_SIZE, str(temp_path),
         timestamp, timestamp),
    )
    return {"upload_id": upload_id, "chunk_size": CHUNK_SIZE, "received_bytes": 0}


def write_chunk(conn: Any, user: dict, upload_id: str, index: Any, data: bytes) -> dict[str, Any]:
    """Append chunk ``index`` (sequential; re-sending an already stored chunk is a no-op)."""
    me = user_identity(user)
    session = _load_session(conn, upload_id, me)
    chunk, size = as_int(session["chunk_size"], CHUNK_SIZE), as_int(session["size"])
    received = as_int(session["received_bytes"])
    position = as_int(index, -1)
    if position < 0:
        raise SocialError("分块序号无效。", 400)
    offset = position * chunk
    if offset < received:
        return _session_payload(session)
    if offset > received:
        raise SocialError(f"分块顺序错误，请从第 {received // chunk} 块继续。", 409)
    expected = min(chunk, size - offset)
    if expected <= 0 or len(data) != expected:
        raise SocialError("分块大小不正确。", 400)
    with open(session["temp_path"], "r+b") as handle:
        handle.seek(offset)
        handle.write(data)
        handle.truncate(offset + len(data))
    conn.execute("UPDATE social_upload_sessions SET received_bytes = ?, updated_at = ? WHERE upload_id = ?",
                 (offset + len(data), now_iso(), session["upload_id"]))
    return {**_session_payload(session), "received_bytes": offset + len(data)}


def upload_complete(conn: Any, user: dict, upload_id: str) -> dict[str, Any]:
    me = user_identity(user)
    session = _load_session(conn, upload_id, me)
    temp_path = Path(session["temp_path"])
    if as_int(session["received_bytes"]) != as_int(session["size"]):
        raise SocialError("文件还没有上传完整。", 409)
    digest, length = store.hash_file(temp_path)
    if digest != session["sha256"] or length != as_int(session["size"]):
        expire_session(conn, session, "failed")
        raise SocialError("文件校验失败（SHA-256 不一致），请重新上传。", 400)
    store.lock_blobs(conn, [digest])
    if not store.blob_available(conn, digest, length) and _global_has(digest, length):
        # The verified bytes already sit in the global store: reference them read-only, no second copy.
        try:
            bind_global_hashes(conn, [digest])
            expire_session(conn, session, "completed")
            return {"media_token": issue_token(conn, me, digest, "global", session["filename"], session["mime"],
                                               length)}
        except SocialError:
            pass  # collected meanwhile: publish our own copy below
    if not store.blob_available(conn, digest, length):
        blob = store.load_blob(conn, digest)
        store.publish_file(temp_path, digest, replace=bool(blob and blob["storage_state"] != store.STATE_READY))
        store.upsert_blob_ready(conn, digest, length, session["mime"])
        _charge(conn, me, length)
    expire_session(conn, session, "completed")
    return {"media_token": issue_token(conn, me, digest, "social", session["filename"], session["mime"], length)}


# ---------------------------------------------------------------------------
# Download, LAN status and backfill
# ---------------------------------------------------------------------------

def load_media(conn: Any, media_id: int) -> dict[str, Any]:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT m.*, msg.conversation_key, msg.sender_identity, msg.created_at AS message_created_at, "
        "msg.recalled_at FROM social_message_media m JOIN social_messages msg ON msg.id = m.message_id "
        "WHERE m.id = ? LIMIT 1", (int(media_id),)).fetchone()
    if row is None:
        raise SocialError("文件不存在。", 404)
    return row_dict(row)


def _require_media_access(conn: Any, me: str, media: dict[str, Any]) -> dict[str, Any]:
    from .social_conversation_service import require_access

    parsed = require_access(conn, me, media["conversation_key"])
    joined = str((parsed.get("membership") or {}).get("joined_at") or "")
    if joined and str(media["message_created_at"]) < joined:
        raise SocialError("无权访问该文件。", 403)
    if media.get("recalled_at"):
        raise SocialError("消息已撤回。", 410)
    return parsed


def _served_mime(conn: Any, media: dict[str, Any]) -> str:
    if media["store"] != "social":
        return str(media["mime_type"])
    blob = store.load_blob(conn, media["blob_hash"]) or {}
    if blob.get("storage_state") == store.STATE_COMPRESSED:
        return str(blob.get("mime_type") or "image/webp")
    return str(media["mime_type"])


def resolve_download(conn: Any, user: dict, media_id: int, variant: Any = None) -> dict[str, Any]:
    """``{path, mime, filename, inline, thumb_missing, ...}`` for an authorised reader."""
    me = user_identity(user)
    media = load_media(conn, media_id)
    _require_media_access(conn, me, media)
    if media.get("expired_at"):
        raise SocialError("文件已过期。", 410)
    if not as_int(media["server_ready"], 1):
        raise SocialError("源设备补传中，请稍后再试。", 409)
    path = (store.blob_path(media["blob_hash"]) if media["store"] == "social"
            else resolve_global_file_path(media["blob_hash"]))
    if not path or not Path(path).is_file():
        raise SocialError("文件已过期。", 410)
    result = {"path": Path(path), "mime": _served_mime(conn, media), "filename": media["original_filename"],
              "media_id": int(media["id"]), "kind": media["media_kind"],
              "inline": media["media_kind"] in {"image", "video", "audio"}, "thumb_missing": False}
    if str(variant or "") == "thumb" and media["media_kind"] == "image":
        thumb = store.blob_path(media["thumbnail_hash"]) if media.get("thumbnail_hash") else None
        if thumb and thumb.is_file():
            result.update(path=thumb, mime="image/jpeg")
        else:
            result["thumb_missing"] = True
    return result


def store_thumbnail(conn: Any, media_id: int, data: bytes) -> Path:
    """Publish a generated thumbnail into the social store and reference it from the media row."""
    file_hash, path = store.publish_bytes(data)
    row = conn.execute("SELECT thumbnail_hash FROM social_message_media WHERE id = ?", (int(media_id),)).fetchone()
    if row is not None and not str(row["thumbnail_hash"] or ""):
        store.upsert_blob_ready(conn, file_hash, len(data), "image/jpeg")
        store.add_blob_ref(conn, file_hash, 1)
        conn.execute("UPDATE social_message_media SET thumbnail_hash = ? WHERE id = ?", (file_hash, int(media_id)))
    return path


def lan_status(conn: Any, user: dict, transfer_id: int, status: Any) -> dict[str, Any]:
    me = user_identity(user)
    ensure_schema(conn)
    if str(status or "") not in LAN_STATUSES:
        raise SocialError("直传状态无效。", 400)
    row = conn.execute("SELECT * FROM social_lan_transfers WHERE id = ? LIMIT 1", (int(transfer_id),)).fetchone()
    transfer = row_dict(row) if row is not None else {}
    if not transfer or me not in (transfer["sender_identity"], transfer["receiver_identity"]):
        raise SocialError("直传记录不存在。", 404)
    if transfer["status"] != "server_backfilled":
        conn.execute("UPDATE social_lan_transfers SET status = ?, updated_at = ? WHERE id = ?",
                     (str(status), now_iso(), int(transfer_id)))
        transfer["status"] = str(status)
    return {"id": int(transfer["id"]), "status": transfer["status"]}


def backfill(conn: Any, user: dict, media_id: int, media_token: Any, events: Optional[list] = None) -> dict[str, Any]:
    """Mark a LAN-delivered media as available on the server, using a token for the same bytes."""
    from .social_conversation_service import (
        for_viewer,
        load_message_row,
        participants,
        require_access,
        serialize_messages,
    )

    me = user_identity(user)
    media = load_media(conn, media_id)
    if media["sender_identity"] != me or media["delivered_via"] != "lan":
        raise SocialError("只有发送者可以补传该文件。", 403)
    if as_int(media["server_ready"], 1):
        return {"media_id": int(media_id), "server_ready": True}
    token = _token_row(conn, str(media_token or "").strip(), me)
    if token["blob_hash"] != media["blob_hash"]:
        raise SocialError("补传的文件与原文件不一致。", 400)
    if token["store"] == "global":
        bind_global_hashes(conn, [media["blob_hash"]])
    else:
        _require_social_blobs(conn, [media["blob_hash"]])
    redeem_tokens(conn, me, [token["token"]])
    conn.execute("UPDATE social_message_media SET server_ready = 1, store = ?, global_file_hash = ? WHERE id = ?",
                 (token["store"], global_hash_for(token["store"], media["blob_hash"]), int(media_id)))
    if token["store"] == "social":
        store.add_blob_ref(conn, media["blob_hash"], 1)
    conn.execute("UPDATE social_lan_transfers SET status = 'server_backfilled', updated_at = ? WHERE media_id = ?",
                 (now_iso(), int(media_id)))
    parsed = require_access(conn, me, media["conversation_key"])
    message = serialize_messages(conn, me, [load_message_row(conn, int(media["message_id"]))])[0]
    for viewer in participants(conn, parsed):  # per recipient, so the sender's copy keeps mine:true
        add_event(events, [viewer], {"type": "message", "message": for_viewer(message, viewer)})
    return {"media_id": int(media_id), "server_ready": True}


def cleanup_stale_sessions(conn: Any) -> int:
    """Hourly: expire sessions idle ≥ 1 h (temp file removed) and purge old unused tokens."""
    ensure_schema(conn)
    rows = conn.execute("SELECT * FROM social_upload_sessions WHERE status = 'uploading' AND updated_at < ?",
                        (iso_before(seconds=SESSION_STALE_SECONDS),)).fetchall()
    for row in rows:
        expire_session(conn, row_dict(row))
    conn.execute("DELETE FROM social_media_tokens WHERE used_at IS NULL AND created_at < ?",
                 (iso_before(hours=TOKEN_TTL_HOURS * 2),))
    return len(rows)
