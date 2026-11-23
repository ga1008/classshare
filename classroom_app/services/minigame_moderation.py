"""Version-bound student publication, including leased AI work and administrator notices."""

import asyncio
import json
import logging

from fastapi import HTTPException

from . import minigame_content_assessment as assessment
from . import minigame_packages as packages
from . import minigame_platform as platform

STATES = {"queued", "running", "manual", "approved", "rejected", "cancelled"}
LEASE_MS = 180_000
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


def enqueue(conn, game_id, version_id, digest):
    now = platform.now_ms()
    conn.execute("""INSERT INTO mg_content_reviews(version_id,game_id,digest,status,created_at,updated_at)
        VALUES(?,?,?,'queued',?,?) ON CONFLICT(version_id) DO NOTHING""", (version_id, game_id, digest, now, now))


def resubmit(conn, game_id, version_id, digest):
    now = platform.now_ms()
    conn.execute("DELETE FROM mg_review_notices WHERE version_id=?", (version_id,))
    conn.execute(
        """INSERT INTO mg_content_reviews(
               version_id,game_id,digest,status,reason,ai_reason,categories_json,evidence_json,
               claim_token,lease_until,attempts,decider,notify_due,created_at,updated_at
           ) VALUES(?,?,?,'queued','','','[]','[]','',0,0,'',0,?,?)
           ON CONFLICT(version_id) DO UPDATE SET
               digest=excluded.digest, status='queued', reason='', ai_reason='',
               categories_json='[]', evidence_json='[]', claim_token='', lease_until=0,
               attempts=0, decider='', notify_due=0, updated_at=excluded.updated_at""",
        (version_id, game_id, digest, now, now),
    )


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
            WHERE version_id IN ({params})""", tuple(version_ids)
    ).fetchall()
    return {
        row["version_id"]: {
            "status": row["status"], "reason": row["reason"],
            "categories": json.loads(row["categories_json"]), "updatedAt": row["updated_at"]
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
        job["metadata"] = {"author": row["author"], "sourceAuthor": row["source_author"],
                           "tags": json.loads(row["tags_json"]), "sourceTags": json.loads(row["source_tags_json"])}
        changed = conn.execute("""UPDATE mg_content_reviews SET status='running',claim_token=?,lease_until=?,attempts=?,updated_at=?
            WHERE version_id=? AND (status='queued' OR (status='running' AND lease_until<=?))""",
            (job["claim_token"], now + LEASE_MS, job["attempts"], now, job["version_id"], now)).rowcount
        if changed != 1:
            return None
        conn.commit()
        return job


def require_admin(conn, user):
    platform.assert_session(conn, user)
    if user["role"] != "teacher" or not platform.is_super_admin_teacher(conn, user["id"]):
        raise HTTPException(403, "仅超管可以查看和决定内容复核。")


_REVIEW_SELECT = """SELECT r.*,g.title,g.status AS game_status,v.status AS version_status,v.digest AS version_digest,
    v.release_version FROM mg_content_reviews r JOIN mg_versions v ON v.id=r.version_id
    JOIN mg_games g ON g.id=r.game_id"""


def serialize(row):
    return {"versionId": row["version_id"], "gameId": row["game_id"], "title": row["title"],
            "version": row["release_version"], "digest": row["digest"], "status": row["status"],
            "reason": row["reason"], "categories": json.loads(row["categories_json"]),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def list_reviews(conn, user, status, offset):
    require_admin(conn, user)
    if status not in STATES:
        raise HTTPException(422, "无效审核状态。")
    rows = conn.execute(_REVIEW_SELECT + " WHERE r.status=? ORDER BY r.updated_at DESC,r.version_id LIMIT 26 OFFSET ?",
                        (status, offset)).fetchall()
    return {"items": [serialize(r) for r in rows[:25]], "hasMore": len(rows) > 25}


def review_row(conn, vid):
    row = conn.execute(_REVIEW_SELECT + " WHERE r.version_id=?", (vid,)).fetchone()
    if not row:
        raise HTTPException(404, "找不到此审核版本。")
    return row


def detail(conn, user, vid):
    require_admin(conn, user)
    row = review_row(conn, vid)
    files = conn.execute("SELECT path,byte_size,mime FROM mg_version_files WHERE version_id=? ORDER BY path", (vid,))
    return {**serialize(row), "evidence": json.loads(row["evidence_json"]), "aiReason": row["ai_reason"],
            "decider": row["decider"], "files": [{"path": f["path"], "size": f["byte_size"], "mime": f["mime"]} for f in files],
            "packageUrl": f"/api/games/v1/moderation/reviews/{vid}/package"}


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
    conn.execute("""UPDATE mg_content_reviews SET status=?,reason=?,ai_reason=?,categories_json=?,evidence_json=?,
        decider=?,claim_token='',lease_until=0,notify_due=0,updated_at=? WHERE version_id=?""",
        (status, result["reason"], result["reason"] if decider == "ai" else row["ai_reason"],
         json.dumps(result["categories"]), json.dumps(result["evidence"], ensure_ascii=False), decider,
         platform.now_ms(), row["version_id"]))


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
            store_result(conn, row, assessment.manual("发布条件发生变化，请复核游戏归属及存档兼容性。"), "ai")
        conn.execute("RELEASE SAVEPOINT minigame_publication")
        conn.commit()


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
    if row["status"] == "manual" and row["version_status"] == "reviewing":
        store_result(conn, row, {"status": status, "reason": reason.strip(), "categories": json.loads(row["categories_json"]),
                                 "evidence": json.loads(row["evidence_json"])}, user["actor"])
        conn.commit()
    elif not (row["status"] == status and row["decider"] == user["actor"] and row["reason"] == reason.strip()):
        raise HTTPException(409, "该版本的复核状态已变更，请刷新。")
    return detail(conn, user, vid)


def register_recipients(conn, vid):
    from .message_center_service import list_super_admin_teachers

    for teacher in list_super_admin_teachers(conn):
        conn.execute("INSERT INTO mg_review_notices(version_id,teacher_id) VALUES(?,?) ON CONFLICT DO NOTHING",
                     (vid, teacher["id"]))


def dispatch_notice():
    """Separate durable inbox and mail steps. Retry when a sender/address is configured later."""
    from .email_notification_service import queue_notification_email_if_applicable
    from .message_center_service import _build_notification_payload, _insert_notification

    now = platform.now_ms()
    schedule = "SELECT version_id FROM mg_content_reviews WHERE status='manual' AND notify_due<=? ORDER BY created_at LIMIT 1"
    due = """SELECT n.*,r.game_id,r.status,g.title FROM mg_review_notices n JOIN mg_content_reviews r ON n.version_id=r.version_id
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
            payload = _build_notification_payload(recipient_role="teacher", recipient_user_pk=admin,
                category="approval_workflow", severity="important", title="学生小游戏待内容复核" if pending else "学生小游戏复核已处理",
                body_preview=f"《{notice['title']}》的学生版本已转交超管，请查看复核记录。",
                link_url="/manage/library/games/reviews?version=" + vid, ref_type="minigame_review", ref_id=vid)
            if not notice["inbox_id"]:
                inbox_id = _insert_notification(conn, {**payload, "email_notification_allowed": False})
                conn.execute("UPDATE mg_review_notices SET inbox_id=? WHERE version_id=? AND teacher_id=?", (inbox_id, vid, admin))
            else:
                queued = queue_notification_email_if_applicable(conn, notification_id=notice["inbox_id"], payload=payload)
                conn.execute("UPDATE mg_review_notices SET email_queued=?,due=? WHERE version_id=? AND teacher_id=?",
                             (int(queued), now + 900_000, vid, admin))
        conn.commit()
        return bool(new or notice)


async def process_one():
    job = await platform.run_db(claim)
    if job:
        packet = None
        try:
            if job["attempts"] > 2:
                result = assessment.manual("审核任务多次中断，已转人工复核。")
            else:
                packet = await asyncio.to_thread(assessment.evidence_for, job)
                result = await asyncio.wait_for(assessment.assess(job, packet), timeout=140)
        except Exception:
            logger.warning("Student game review deferred to administrators, version=%s", job["version_id"])
            result = assessment.manual("AI 审核服务不可用、素材异常或返回结果不完整，请复核原包。",
                                       packet["unread"][:48] if packet else None)
        await platform.run_db(finish, job, result)
    notice = await platform.run_db(dispatch_notice)
    return bool(job or notice)
