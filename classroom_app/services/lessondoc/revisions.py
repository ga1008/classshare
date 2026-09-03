"""Snapshot primitives; callers own pack authorization and the transaction."""

import json

from ...db.connection import execute_insert_returning_id

HISTORY_LIMIT = 20


def record_snapshot(conn, *, pack_id, lesson_no, revision, document, source, author_id, now):
    prior = conn.execute("SELECT file_hash FROM lessondoc_doc_revisions WHERE pack_id=? AND lesson_no=? ORDER BY id DESC LIMIT 1", (pack_id, lesson_no)).fetchone()
    if prior and prior["file_hash"] == revision:
        return
    execute_insert_returning_id(conn, "INSERT INTO lessondoc_doc_revisions(pack_id,lesson_no,file_hash,model_json,source,author_id,created_at) VALUES(?,?,?,?,?,?,?)",
                                (pack_id, lesson_no, revision, json.dumps(document, ensure_ascii=False), source, author_id, now))
    conn.execute("DELETE FROM lessondoc_doc_revisions WHERE pack_id=? AND lesson_no=? AND id NOT IN (SELECT id FROM lessondoc_doc_revisions WHERE pack_id=? AND lesson_no=? ORDER BY id DESC LIMIT ?)",
                 (pack_id, lesson_no, pack_id, lesson_no, HISTORY_LIMIT))


def list_snapshots(conn, *, pack_id, lesson_no):
    return [dict(row) for row in conn.execute("SELECT id,file_hash AS revision,source,author_id,created_at FROM lessondoc_doc_revisions WHERE pack_id=? AND lesson_no=? ORDER BY id DESC LIMIT ?",
                                             (pack_id, lesson_no, HISTORY_LIMIT)).fetchall()]


def get_snapshot(conn, *, pack_id, lesson_no, revision_id):
    row = conn.execute("SELECT * FROM lessondoc_doc_revisions WHERE pack_id=? AND lesson_no=? AND id=?", (pack_id, lesson_no, revision_id)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["document"] = json.loads(result.pop("model_json"))
    return result
