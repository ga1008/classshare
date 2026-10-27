"""聊天媒体留存与空间守护 (social doc §6) — daily scheduler task ``social_media_retention``.

1. Media whose ``expires_at`` (created + 365 days) passed get ``expired_at``
   (clients show 「文件已过期」) and release their blob references.
2. Social-store blobs with ``ref_count = 0`` (after a 24 h grace, and with no
   unused media token pointing at them) are physically deleted. Global-store
   references are only released; global bytes are never touched.
3. Disk guard: above ``SOCIAL_MEDIA_DISK_HIGH_WATERMARK`` (85 %) images > 2 MB
   are re-encoded as WebP (quality 70, long side 2048), oldest first; if that
   is not enough the oldest videos/files expire early and are deleted, until
   usage is below ``SOCIAL_MEDIA_DISK_LOW_WATERMARK`` (80 %) or 500 actions.

Each unit of work commits on its own under the per-blob lock (no long
transaction). ``dry_run`` reports what would happen without touching rows or
files. The
last summary is written to ``DATA_ROOT/runtime/social_media_retention_last.json``
for ``GET /api/social/admin/storage``.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from ..storage_paths import DATA_ROOT
from . import social_media_store as store
from .social_common import as_int, env_float, ensure_schema, iso_before, now_iso, row_dict

TASK_KIND_RETENTION = "social_media_retention"
TASK_KIND_UPLOAD_CLEANUP = "social_upload_cleanup"
MAX_ACTIONS = 500
COMPRESS_MIN_BYTES = 2 * 1024 * 1024
ORPHAN_GRACE_HOURS = 24
EXPIRE_BATCH = 5000
COMMIT_BATCH = 200
DRY_RUN_WEBP_RATIO = 0.3
SUMMARY_PATH = DATA_ROOT / "runtime" / "social_media_retention_last.json"


def high_watermark() -> float:
    return env_float("SOCIAL_MEDIA_DISK_HIGH_WATERMARK", 85.0, low=1.0, high=100.0)


def low_watermark() -> float:
    return min(high_watermark(), env_float("SOCIAL_MEDIA_DISK_LOW_WATERMARK", 80.0, low=1.0, high=100.0))


def disk_usage(path: Path = DATA_ROOT) -> tuple[int, int]:
    """``(total_bytes, used_bytes)`` of the data disk (patched in tests)."""
    target = Path(path)
    while not target.exists() and target.parent != target:
        target = target.parent
    usage = shutil.disk_usage(target)
    return int(usage.total), int(usage.used)


class _Disk:
    """Projected usage: the real disk at the start of the run, minus bytes this run freed."""

    def __init__(self) -> None:
        self.total, self.used = disk_usage()

    def percent(self) -> float:
        return round(100.0 * self.used / self.total, 2) if self.total else 0.0

    def free(self, size: int) -> None:
        self.used = max(0, self.used - max(0, int(size)))


# ---------------------------------------------------------------------------
# Expiry and reference release
# ---------------------------------------------------------------------------
#
# Every unit of work (one batch of expiries, one blob purge, one compression,
# one early expiry) runs under the per-blob lock (``store.lock_blobs``) and is
# committed on its own, so no long transaction holds locks or WAL, and the
# precheck / attach / publish paths serialise against it per blob. Reference
# counts are re-read under the lock and never forced to 0.

def _commit(conn: Any, dry_run: bool) -> None:
    """End one unit of work: commit it, or (dry run, nothing written) just release its locks."""
    if dry_run:
        conn.rollback()
    else:
        conn.commit()


def _released_hashes(media: dict[str, Any]) -> list[str]:
    hashes = []
    if media["store"] == "social" and as_int(media["server_ready"], 1):
        hashes.append(media["blob_hash"])
    if media.get("thumbnail_hash"):
        hashes.append(media["thumbnail_hash"])
    return hashes


def _expire_media(conn: Any, media: dict[str, Any], dry_run: bool) -> list[str]:
    """Mark one media expired and release its blob refs (caller holds the blob locks). Returns the hashes."""
    hashes = _released_hashes(media)
    if dry_run:
        return hashes
    # A global-store reference is released (bytes untouched); social blobs lose one ref each.
    updated = conn.execute("UPDATE social_message_media SET expired_at = ?, global_file_hash = '' "
                           "WHERE id = ? AND expired_at IS NULL", (now_iso(), int(media["id"])))
    if as_int(getattr(updated, "rowcount", 1), 1) == 0:
        return []  # someone else expired it meanwhile; its refs are already released
    for file_hash in hashes:
        store.add_blob_ref(conn, file_hash, -1)
    return hashes


def expire_due_media(conn: Any, released: set[str], *, dry_run: bool) -> int:
    rows = [row_dict(row) for row in conn.execute(
        "SELECT * FROM social_message_media WHERE expired_at IS NULL AND expires_at IS NOT NULL AND expires_at < ? "
        "ORDER BY id LIMIT ?", (now_iso(), EXPIRE_BATCH),
    ).fetchall()]
    for start in range(0, len(rows), COMMIT_BATCH):
        batch = rows[start:start + COMMIT_BATCH]
        store.lock_blobs(conn, [item for media in batch for item in _released_hashes(media)])
        for media in batch:
            released.update(_expire_media(conn, media, dry_run))
        _commit(conn, dry_run)
    return len(rows)


def _delete_blob_locked(conn: Any, file_hash: str, *, dry_run: bool, grace_cutoff: Optional[str]) -> int:
    """Delete one social blob if, under its lock, nothing references it. Returns bytes freed.

    The file is unlinked while the lock is held (a concurrent publish waits and then
    re-publishes onto a row marked deleted); the row change is committed right after.
    """
    store.lock_blobs(conn, [file_hash])
    blob = store.load_blob(conn, file_hash)
    if not blob or blob["storage_state"] == store.STATE_DELETED or as_int(blob["ref_count"]) > 0:
        return 0
    touched = str(blob.get("last_referenced_at") or blob.get("first_seen_at") or "")
    if grace_cutoff is not None and touched >= grace_cutoff:
        return 0  # fresh upload: give the sender time to redeem the token
    if store.live_token_exists(conn, file_hash):
        return 0
    path = store.blob_path(file_hash)
    size = path.stat().st_size if path.is_file() else 0
    if dry_run:
        return size
    if not store.delete_blob_file(file_hash):
        return 0
    conn.execute("UPDATE social_media_blobs SET storage_state = ?, deleted_at = ? WHERE blob_hash = ? "
                 "AND ref_count <= 0", (store.STATE_DELETED, now_iso(), file_hash))
    return size


def purge_unreferenced(conn: Any, released: set[str], *, dry_run: bool) -> tuple[int, int]:
    """Delete social-store blobs nobody references (one committed unit per blob). ``(count, bytes)``."""
    cutoff = iso_before(hours=ORPHAN_GRACE_HOURS)
    candidates = [str(row["blob_hash"]) for row in conn.execute(
        "SELECT blob_hash FROM social_media_blobs WHERE storage_state <> ? AND ref_count <= 0 "
        "ORDER BY first_seen_at", (store.STATE_DELETED,),
    ).fetchall()]
    count = freed = 0
    for file_hash in candidates:
        size = _delete_blob_locked(conn, file_hash, dry_run=dry_run,
                                   grace_cutoff=None if file_hash in released else cutoff)
        _commit(conn, dry_run)
        if size:
            count += 1
            freed += size
    return count, freed


# ---------------------------------------------------------------------------
# Disk guard
# ---------------------------------------------------------------------------

def _compress_candidates(conn: Any) -> list[dict[str, Any]]:
    return [row_dict(row) for row in conn.execute(
        "SELECT b.blob_hash, b.file_size, MIN(m.created_at) AS first_used FROM social_media_blobs b "
        "JOIN social_message_media m ON m.blob_hash = b.blob_hash AND m.store = 'social' "
        "WHERE b.storage_state = ? AND b.file_size > ? AND m.media_kind = 'image' AND m.expired_at IS NULL "
        "AND m.server_ready = 1 GROUP BY b.blob_hash, b.file_size ORDER BY first_used ASC LIMIT ?",
        (store.STATE_READY, COMPRESS_MIN_BYTES, MAX_ACTIONS)).fetchall()]


def _compress_one(conn: Any, blob: dict[str, Any], dry_run: bool) -> int:
    """Re-encode one image as WebP under its own hash, repoint its media, drop the original. Bytes freed."""
    old_hash, old_size = str(blob["blob_hash"]), as_int(blob["file_size"])
    if dry_run:
        return int(old_size * (1 - DRY_RUN_WEBP_RATIO))
    data = store.webp_bytes(store.blob_path(old_hash))
    if len(data) >= old_size:
        return 0  # not worth it
    new_hash, _path = store.publish_bytes(data)
    store.lock_blobs(conn, sorted({old_hash, new_hash}))
    current = store.load_blob(conn, old_hash)
    if not current or current["storage_state"] != store.STATE_READY:
        conn.rollback()
        return 0
    store.upsert_blob(conn, new_hash, len(data), "image/webp", store.STATE_COMPRESSED)
    moved = conn.execute(
        "UPDATE social_message_media SET blob_hash = ?, mime_type = 'image/webp', file_size = ? "
        "WHERE blob_hash = ? AND store = 'social' AND server_ready = 1 AND expired_at IS NULL",
        (new_hash, len(data), old_hash),
    )
    count = as_int(getattr(moved, "rowcount", 0))
    store.add_blob_ref(conn, new_hash, count)
    store.add_blob_ref(conn, old_hash, -count)
    conn.commit()
    freed = _delete_blob_locked(conn, old_hash, dry_run=False, grace_cutoff=None)
    conn.commit()
    return freed


def _early_expiry_candidates(conn: Any) -> list[dict[str, Any]]:
    return [row_dict(row) for row in conn.execute(
        "SELECT * FROM social_message_media WHERE store = 'social' AND expired_at IS NULL "
        "AND media_kind IN ('video', 'file', 'audio') ORDER BY created_at ASC, id ASC LIMIT ?",
        (MAX_ACTIONS,)).fetchall()]


def _compress_phase(conn: Any, disk: _Disk, summary: dict[str, Any], dry_run: bool) -> int:
    actions = 0
    for blob in _compress_candidates(conn):
        if disk.percent() < low_watermark() or actions >= MAX_ACTIONS:
            break
        try:
            disk.free(_compress_one(conn, blob, dry_run))
            summary["compressed_images"] += 1
        except Exception as exc:  # noqa: BLE001 - one broken image must not stop the guard
            conn.rollback()
            summary["errors"].append(f"compress {blob['blob_hash'][:12]}: {exc}")
        actions += 1
    return actions


def _expiry_phase(conn: Any, disk: _Disk, released: set[str], summary: dict[str, Any], dry_run: bool,
                  actions: int) -> int:
    for media in _early_expiry_candidates(conn):
        if disk.percent() < low_watermark() or actions >= MAX_ACTIONS:
            break
        hashes = _released_hashes(media)
        store.lock_blobs(conn, hashes)
        if any(store.live_token_exists(conn, file_hash) for file_hash in hashes):
            conn.rollback()  # a sender is about to attach these bytes again; leave them
            actions += 1
            continue
        released.update(_expire_media(conn, media, dry_run))
        _commit(conn, dry_run)
        summary["early_expired"] += 1
        size = _delete_blob_locked(conn, media["blob_hash"], dry_run=dry_run, grace_cutoff=None)
        _commit(conn, dry_run)
        if size:
            disk.free(size)
            summary["deleted_blobs"] += 1
        actions += 1
    return actions


def guard_disk(conn: Any, released: set[str], summary: dict[str, Any], *, dry_run: bool) -> None:
    disk = _Disk()
    summary["disk_before"] = disk.percent()
    actions = 0
    if disk.percent() > high_watermark():
        actions = _compress_phase(conn, disk, summary, dry_run)
        actions = _expiry_phase(conn, disk, released, summary, dry_run, actions)
    summary["disk_after"] = disk.percent()
    summary["actions"] = actions


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _write_summary(summary: dict[str, Any]) -> None:
    path = Path(SUMMARY_PATH)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".retention-", delete=False,
                                         encoding="utf-8") as output:
            temporary = output.name
            json.dump(summary, output, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except OSError as exc:
        print(f"[SOCIAL_MEDIA] retention summary write failed: {exc!r}")
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def last_summary() -> Optional[dict[str, Any]]:
    try:
        return json.loads(Path(SUMMARY_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_retention(conn: Any, *, dry_run: bool = False) -> dict[str, Any]:
    ensure_schema(conn)
    summary: dict[str, Any] = {
        "ran_at": now_iso(), "dry_run": bool(dry_run), "expired_media": 0, "deleted_blobs": 0, "freed_bytes": 0,
        "compressed_images": 0, "early_expired": 0, "disk_before": None, "disk_after": None,
        "high_watermark": high_watermark(), "low_watermark": low_watermark(), "actions": 0, "errors": [],
    }
    released: set[str] = set()
    summary["expired_media"] = expire_due_media(conn, released, dry_run=dry_run)
    summary["deleted_blobs"], summary["freed_bytes"] = purge_unreferenced(conn, released, dry_run=dry_run)
    guard_disk(conn, released, summary, dry_run=dry_run)
    print(f"[SOCIAL_MEDIA] retention {'dry-run ' if dry_run else ''}summary: "
          f"expired={summary['expired_media']} deleted={summary['deleted_blobs']} "
          f"compressed={summary['compressed_images']} early={summary['early_expired']} "
          f"disk={summary['disk_before']}%->{summary['disk_after']}%")
    _write_summary(summary)
    return summary


def handle_social_media_retention(task: dict[str, Any]) -> str:
    from ..database import get_db_connection

    dry_run = bool((task.get("payload") or {}).get("dry_run"))
    with get_db_connection() as conn:
        summary = run_retention(conn, dry_run=dry_run)
        conn.commit()
    return (f"social media retention: expired={summary['expired_media']} deleted={summary['deleted_blobs']} "
            f"compressed={summary['compressed_images']} early={summary['early_expired']}")


def handle_social_upload_cleanup(task: dict[str, Any]) -> str:
    from ..database import get_db_connection
    from .social_media_service import cleanup_stale_sessions

    with get_db_connection() as conn:
        expired = cleanup_stale_sessions(conn)
        conn.commit()
    return f"social upload cleanup: expired sessions={expired}"


def ensure_social_media_tasks(conn: Any) -> None:
    """Arm the daily retention (04:20) and the hourly upload-session cleanup (idempotent)."""
    from .scheduled_task_service import schedule_task

    now = datetime.now()
    first = now.replace(hour=4, minute=20, second=0, microsecond=0)
    if first <= now:
        first += timedelta(days=1)
    schedule_task(conn, task_kind=TASK_KIND_RETENTION, run_at=first, payload={"dry_run": False},
                  dedupe_key="social:media-retention", recurrence_seconds=86400,
                  title="聊天媒体留存与空间守护", priority=90, max_attempts=3, replace=False)
    schedule_task(conn, task_kind=TASK_KIND_UPLOAD_CLEANUP, run_at=now + timedelta(minutes=15), payload={},
                  dedupe_key="social:upload-cleanup", recurrence_seconds=3600,
                  title="聊天分块上传会话清理", priority=95, max_attempts=3, replace=False)


def storage_summary(conn: Any) -> dict[str, Any]:
    """Super-admin view: store totals, active uploads, disk usage, watermarks, last run, presence."""
    from .presence_service import snapshot

    ensure_schema(conn)
    blobs = {str(row["storage_state"]): {"count": as_int(row["n"]), "bytes": as_int(row["total"])}
             for row in conn.execute("SELECT storage_state, COUNT(*) AS n, COALESCE(SUM(file_size), 0) AS total "
                                     "FROM social_media_blobs GROUP BY storage_state").fetchall()}
    media = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN expired_at IS NULL THEN 0 ELSE 1 END), 0) "
                         "AS expired FROM social_message_media").fetchone()
    uploads = conn.execute("SELECT COUNT(*) AS n FROM social_upload_sessions WHERE status = 'uploading'").fetchone()
    disk = _Disk()
    return {
        "blobs": blobs,
        "media": {"total": as_int(media["n"]), "expired": as_int(media["expired"])},
        "active_uploads": as_int(uploads["n"]),
        "disk": {"percent": disk.percent(), "total_bytes": disk.total, "used_bytes": disk.used},
        "watermarks": {"high": high_watermark(), "low": low_watermark()},
        "last_run": last_summary(),
        "presence": snapshot(),
    }
