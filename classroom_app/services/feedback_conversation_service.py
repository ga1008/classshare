"""Private feedback conversations shared by their author and active super admins."""
from datetime import datetime
import re

from fastapi import HTTPException

from ..db.connection import begin_immediate_transaction, execute_insert_returning_id, get_configured_db_engine
from .message_center_service import create_feedback_conversation_notifications, is_super_admin_teacher

CLOSED_STATUSES = {"closed", "resolved"}
MAX_MESSAGE_LENGTH = 5000


def is_feedback_admin(conn, user):
    return user.get("role") == "teacher" and is_super_admin_teacher(conn, user.get("id"))


def is_feedback_owner(feedback, user):
    return str(feedback["user_id"]) == str(user["id"]) and feedback["user_role"] == user.get("role")


def load_feedback(conn, feedback_id, user, *, lock=False, owner_only=False):
    if lock:
        begin_immediate_transaction(conn)
    suffix = " FOR UPDATE" if lock and get_configured_db_engine() == "postgres" else ""
    row = conn.execute("SELECT * FROM app_feedback WHERE id = ?" + suffix, (feedback_id,)).fetchone()
    if not row:
        raise HTTPException(404, "反馈记录不存在。")
    admin = is_feedback_admin(conn, user)
    if not is_feedback_owner(row, user) and (owner_only or not admin):
        raise HTTPException(403, "无权访问此反馈。")
    return dict(row), admin


def _summary_select(user):
    sql = """SELECT f.*,
        (SELECT COUNT(*) FROM app_feedback_attachments a WHERE a.feedback_id=f.id) AS attachment_count,
        (SELECT COUNT(*) FROM app_feedback_messages m WHERE m.feedback_id=f.id) AS message_count,
        (SELECT COUNT(*) FROM app_feedback_messages m WHERE m.feedback_id=f.id AND m.event_type='reply') AS reply_count,
        COALESCE((SELECT MAX(m.id) FROM app_feedback_messages m WHERE m.feedback_id=f.id),0) AS last_message_id,
        (SELECT COUNT(*) FROM app_feedback_messages m WHERE m.feedback_id=f.id
          AND NOT(m.sender_role=? AND m.sender_id=?)
          AND m.id > COALESCE((SELECT r.last_read_message_id FROM app_feedback_reads r
            WHERE r.feedback_id=f.id AND r.user_role=? AND r.user_id=?),0)) AS unread_count
        FROM app_feedback f """
    return sql, [user["role"], str(user["id"]), user["role"], str(user["id"])]


def _decorate(row, user, admin):
    item = dict(row)
    closed = item["status"] in CLOSED_STATUSES
    item.update(
        status_label="已关闭" if closed else ("待处理" if item["status"] == "pending" else "沟通中"),
        can_reply=not closed, can_manage=bool(admin),
        can_withdraw=is_feedback_owner(item, user) and not closed and not item["message_count"],
    )
    return item


def list_feedback(conn, user, *, admin=False, status="all", before_id=None, limit=40):
    manager = is_feedback_admin(conn, user)
    if admin and not manager:
        raise HTTPException(403, "仅超管可以查看所有反馈。")
    sql, params = _summary_select(user)
    clauses = ["1=1"]
    if not admin:
        clauses.append("f.user_role=? AND f.user_id=?")
        params.extend((user["role"], str(user["id"])))
    if status not in {"all", "open", "closed"}:
        raise HTTPException(400, "反馈状态筛选无效。")
    if status != "all":
        clauses.append("f.status " + ("IN" if status == "closed" else "NOT IN") + " ('closed','resolved')")
    if before_id:
        clauses.append("f.id < ?")
        params.append(before_id)
    limit = max(1, min(int(limit), 100))
    rows = conn.execute(sql + " WHERE " + " AND ".join(clauses) + " ORDER BY f.id DESC LIMIT ?", (*params, limit + 1)).fetchall()
    items = [_decorate(row, user, manager) for row in rows[:limit]]
    return {"items": items, "has_more": len(rows) > limit,
            "next_before_id": items[-1]["id"] if len(rows) > limit else None}


def feedback_summary(conn, feedback_id, user, admin):
    sql, params = _summary_select(user)
    row = conn.execute(sql + " WHERE f.id=?", (*params, feedback_id)).fetchone()
    return _decorate(row, user, admin)


def list_messages(conn, feedback_id, *, before_id=None, limit=50):
    limit = max(1, min(int(limit), 100))
    clauses, params = ["feedback_id=?"], [feedback_id]
    if before_id:
        clauses.append("id < ?")
        params.append(before_id)
    rows = conn.execute("SELECT * FROM app_feedback_messages WHERE " + " AND ".join(clauses)
                        + " ORDER BY id DESC LIMIT ?", (*params, limit + 1)).fetchall()
    items = [dict(row) for row in reversed(rows[:limit])]
    for item in items:
        item["sender_is_super_admin"] = bool(item["sender_is_super_admin"])
    return {"items": items, "messages": items, "has_more": len(rows) > limit,
            "next_before_id": items[0]["id"] if len(rows) > limit else None}


def get_feedback_detail(conn, feedback_id, user):
    _, admin = load_feedback(conn, feedback_id, user)
    feedback = feedback_summary(conn, feedback_id, user, admin)
    attachments = [dict(row) for row in conn.execute(
        "SELECT id,file_hash,original_filename,file_size,mime_type,created_at FROM app_feedback_attachments WHERE feedback_id=? ORDER BY id",
        (feedback_id,)).fetchall()]
    messages = list_messages(conn, feedback_id)
    return {"feedback": feedback, "attachments": attachments,
            **messages, **{key: feedback[key] for key in ("can_reply", "can_manage", "can_withdraw")}}


def _validate_message_input(body, *, optional=False):
    content = str(body.get("content") or "").strip()
    token = str(body.get("client_message_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", token):
        raise HTTPException(400, "发送标识无效，请刷新后重试。")
    if (not optional and not content) or len(content) > MAX_MESSAGE_LENGTH:
        raise HTTPException(400, f"请填写 1 至 {MAX_MESSAGE_LENGTH} 字的回复。" if not optional else "说明不能超过 5000 字。")
    return content, token


def _existing_message(conn, feedback_id, user, token, event_type, content):
    row = conn.execute("""SELECT * FROM app_feedback_messages WHERE feedback_id=?
        AND sender_role=? AND sender_id=? AND client_message_id=?""",
        (feedback_id, user["role"], str(user["id"]), token)).fetchone()
    if row and (row["event_type"] != event_type or row["content"] != content):
        raise HTTPException(409, "此发送标识已用于其他内容，请刷新后重试。")
    return dict(row) if row else None


def _insert_event(conn, feedback, user, admin, event_type, content, token):
    now = datetime.now().isoformat()
    message_id = execute_insert_returning_id(conn, """INSERT INTO app_feedback_messages
        (feedback_id,sender_role,sender_id,sender_name,sender_is_super_admin,event_type,content,client_message_id,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)""", (feedback["id"], user["role"], str(user["id"]),
        str(user.get("name") or ""), int(admin), event_type, content, token, now))
    message = dict(conn.execute("SELECT * FROM app_feedback_messages WHERE id=?", (message_id,)).fetchone())
    create_feedback_conversation_notifications(conn, feedback, message)
    return message


def reply_to_feedback(conn, feedback_id, user, body):
    content, token = _validate_message_input(body)
    feedback, admin = load_feedback(conn, feedback_id, user, lock=True)
    existing = _existing_message(conn, feedback_id, user, token, "reply", content)
    if existing:
        return {"message": existing, "deduplicated": True}
    if feedback["status"] in CLOSED_STATUSES:
        raise HTTPException(409, "反馈已关闭，无法继续回复；超管重新开启后可继续沟通。")
    message = _insert_event(conn, feedback, user, admin, "reply", content, token)
    conn.execute("UPDATE app_feedback SET status='processing',updated_at=? WHERE id=?", (message["created_at"], feedback_id))
    return {"message": message, "deduplicated": False}


def change_feedback_status(conn, feedback_id, user, body):
    content, token = _validate_message_input(body, optional=True)
    requested = body.get("status")
    if requested not in {"closed", "open"}:
        raise HTTPException(400, "反馈状态无效。")
    feedback, admin = load_feedback(conn, feedback_id, user, lock=True)
    if not admin:
        raise HTTPException(403, "仅超管可以关闭或重新开启反馈。")
    event_type = "closed" if requested == "closed" else "reopened"
    existing = _existing_message(conn, feedback_id, user, token, event_type, content)
    if existing:
        return {"message": existing, "feedback": feedback_summary(conn, feedback_id, user, admin), "deduplicated": True}
    latest_id = conn.execute("SELECT COALESCE(MAX(id),0) AS id FROM app_feedback_messages WHERE feedback_id=?", (feedback_id,)).fetchone()["id"]
    expected = body.get("expected_last_message_id")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected != latest_id:
        raise HTTPException(409, "对话已有更新，请刷新并阅读最新消息后再操作。")
    if body.get("expected_status") is not None and body["expected_status"] != feedback["status"]:
        raise HTTPException(409, "反馈状态已变化，请刷新后重试。")
    if (feedback["status"] in CLOSED_STATUSES) == (requested == "closed"):
        raise HTTPException(409, "反馈已处于此状态，请刷新后查看。")
    message = _insert_event(conn, feedback, user, admin, event_type, content, token)
    conn.execute("UPDATE app_feedback SET status=?,updated_at=? WHERE id=?",
                 ("closed" if requested == "closed" else "processing", message["created_at"], feedback_id))
    return {"message": message, "feedback": feedback_summary(conn, feedback_id, user, admin), "deduplicated": False}


def mark_feedback_read(conn, feedback_id, user, last_message_id):
    load_feedback(conn, feedback_id, user, lock=True)
    if isinstance(last_message_id, bool) or not isinstance(last_message_id, int) or last_message_id < 0:
        raise HTTPException(400, "已读位置无效。")
    if last_message_id and not conn.execute("SELECT id FROM app_feedback_messages WHERE feedback_id=? AND id=?",
                                           (feedback_id, last_message_id)).fetchone():
        raise HTTPException(400, "已读位置不属于此反馈。")
    now = datetime.now().isoformat()
    conn.execute("""INSERT INTO app_feedback_reads(feedback_id,user_role,user_id,last_read_message_id,updated_at)
        VALUES(?,?,?,?,?) ON CONFLICT(feedback_id,user_role,user_id) DO UPDATE SET
        last_read_message_id=CASE WHEN excluded.last_read_message_id>app_feedback_reads.last_read_message_id
            THEN excluded.last_read_message_id ELSE app_feedback_reads.last_read_message_id END, updated_at=excluded.updated_at""",
        (feedback_id, user["role"], str(user["id"]), last_message_id, now))
    conn.execute("""UPDATE message_center_notifications SET read_at=?
        WHERE recipient_role=? AND recipient_user_pk=? AND category='app_feedback' AND read_at IS NULL
          AND ((ref_type='app_feedback' AND ref_id=?) OR (ref_type='app_feedback_message'
            AND ref_id IN(SELECT CAST(id AS TEXT) FROM app_feedback_messages WHERE feedback_id=? AND id<=?)))""",
        (now, user["role"], user["id"], str(feedback_id), feedback_id, last_message_id))
    return {"unread_count": feedback_summary(conn, feedback_id, user, is_feedback_admin(conn, user))["unread_count"]}


def withdraw_feedback(conn, feedback_id, user):
    feedback, admin = load_feedback(conn, feedback_id, user, lock=True, owner_only=True)
    if not feedback_summary(conn, feedback_id, user, admin)["can_withdraw"]:
        raise HTTPException(409, "已开始沟通或已关闭的反馈需保留记录，不能撤回。")
    conn.execute("DELETE FROM message_center_notifications WHERE category='app_feedback' AND ref_type='app_feedback' AND ref_id=?", (str(feedback_id),))
    conn.execute("DELETE FROM app_feedback WHERE id=?", (feedback_id,))
