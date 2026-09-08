"""Idempotent built-in optional review marks; no approval is required."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..config import SIGNATURES_DIR
from .signature_service import signature_relative_path


def ensure_standard_review_signatures(conn) -> None:
    from ..db.connection import get_configured_db_engine

    if get_configured_db_engine() == "postgres":
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("standard-review-signatures",))
    else:
        conn.execute("UPDATE electronic_signatures SET id = id WHERE legacy_source = 'system_review_mark'")
    for word, filename in (("已核", "gxufl_exam_review_checked.png"), ("同意", "gxufl_exam_review_agreed.png")):
        legacy_id = f"standard-review-{filename}"
        if conn.execute("SELECT id FROM electronic_signatures WHERE legacy_source = 'system_review_mark' AND legacy_id = ?", (legacy_id,)).fetchone():
            continue
        content = (Path(__file__).parent / "assets" / filename).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        relative = signature_relative_path(digest, ".png")
        target = SIGNATURES_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
        conn.execute("""INSERT INTO electronic_signatures (
            name, subject_name, subject_role, signature_kind, scope_level,
            owner_role, owner_name_snapshot, uploaded_by_role, uploaded_by_name_snapshot,
            file_hash, file_ext, mime_type, stored_path, file_size, description, legacy_source, legacy_id)
            VALUES (?, ?, 'system', 'stamp', 'platform', 'system', 'LanShare', 'system', 'LanShare',
                    ?, '.png', 'image/png', ?, ?, '可选批语，无需申请', 'system_review_mark', ?)
            """,
            (word, word, digest, str(relative).replace('\\', '/'), len(content), legacy_id))
