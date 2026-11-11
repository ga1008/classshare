"""Versioned public world knowledge, read explicitly rather than injected at birth."""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..db.schema_wanderer_runtime import ensure_wanderer_runtime_schema
from ..time_utils import local_iso

HANDBOOK = Path(__file__).resolve().parents[1] / "resources" / "wanderer_world.md"
CHAPTER_KEYS = ("world", "time", "identity", "resources", "memory", "relationships", "conversations", "platform",
                "cultivation", "life", "bonds", "dao", "reflection", "operations", "questions", "rules")
LEGACY_TOPICS = {"identity": "identity", "time": "time", "friend_requests": "relationships",
                 "private_messages": "conversations", "groups": "conversations", "blog": "platform",
                 "classroom": "platform", "knowledge": "cultivation", "dao": "dao", "limits": "resources",
                 "profile": "platform", "materials": "platform", "notifications": "conversations", "manual": "world"}


@lru_cache(maxsize=1)
def documents() -> tuple[dict, ...]:
    text = HANDBOOK.read_text(encoding="utf-8")
    headings = list(re.finditer(r"^## 第[^\n]+", text, re.M))
    if len(headings) != 16:
        raise RuntimeError("世界手册必须包含完整16章")
    version = "v3.0-" + hashlib.sha256(text.encode()).hexdigest()[:12]
    rows = []
    for i, heading in enumerate(headings):
        body = text[heading.start():headings[i+1].start() if i+1 < len(headings) else len(text)].strip()
        rows.append({"chapter_key": CHAPTER_KEYS[i], "version": version, "title": heading.group()[3:],
                     "content": body, "content_hash": hashlib.sha256(body.encode()).hexdigest()})
    return tuple(rows)


def ensure_world_chapters(conn: Any) -> dict:
    ensure_wanderer_runtime_schema(conn)
    rows = documents()
    version, stamp = rows[0]["version"], local_iso()
    if conn.execute("SELECT COUNT(*) AS n FROM wanderer_world_chapters WHERE version=? AND status='active'",
                    (version,)).fetchone()["n"] == 16:
        return {"version": version, "created": 0}
    for row in rows:
        conn.execute("INSERT INTO wanderer_world_chapters(chapter_key,version,title,content,content_hash,effective_at) "
                     "VALUES(?,?,?,?,?,?) ON CONFLICT(chapter_key,version) DO NOTHING",
                     (row["chapter_key"], row["version"], row["title"], row["content"], row["content_hash"], stamp))
    conn.execute("UPDATE wanderer_world_chapters SET status='archived' WHERE version<>? AND status='active'", (version,))
    return {"version": version, "created": 16}


def chapter_list(conn: Any) -> dict:
    ensure_world_chapters(conn)
    rows = conn.execute("SELECT chapter_key,version,title,content_hash,effective_at,LENGTH(content) AS chars "
                        "FROM wanderer_world_chapters WHERE status='active'").fetchall()
    items = sorted((dict(row) for row in rows), key=lambda row: CHAPTER_KEYS.index(row['chapter_key']))
    return {"items": items, "count": len(items), "version": items[0]['version'] if items else ""}


def read_chapter(conn: Any, chapter_key: str, *, offset: int = 0, limit: int = 6000,
                 version: str | None = None) -> dict:
    ensure_world_chapters(conn)
    key = LEGACY_TOPICS.get(chapter_key, chapter_key)
    clause, params = ("version=?", [version]) if version else ("status='active'", [])
    row = conn.execute(f"SELECT * FROM wanderer_world_chapters WHERE chapter_key=? AND {clause}", (key,*params)).fetchone()
    if row is None:
        raise ValueError("世界章节或版本不存在，请先读取目录")
    start, size = max(0,int(offset)), max(100,min(12000,int(limit)))
    content = row['content'][start:start+size]
    ref = f"world:{key}:{row['content_hash']}:{start}:{len(content)}"
    return {"chapter_key": key, "title": row['title'], "version": row['version'], "content": content,
            "offset": start, "next_offset": start+len(content) if start+len(content)<len(row['content']) else None,
            "total_chars": len(row['content']), "content_hash": row['content_hash'],
            "evidence": {"ref": ref, "kind": "world_chapter", "id": key, "title": row['title'],
                         "version": row['version'], "hash": row['content_hash'], "scope": "public",
                         "offset": start, "length": len(content)}}


def world_manual_markdown() -> str:
    return HANDBOOK.read_text(encoding="utf-8")
