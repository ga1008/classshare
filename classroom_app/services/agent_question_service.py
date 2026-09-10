"""A question answer resumes its exact waiting tool; it grants no business rights."""
import hashlib
import json
import re
import time
import uuid

from fastapi import HTTPException

from .agent_actor_service import resolve_agent_actor
from .agent_delegation_service import verify_task_delegation


def _text(value, maximum, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise HTTPException(400, "问题或回答的文字长度无效。")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(400, "问题或回答包含无效字符。") from None
    return value.strip()


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def normalize_questions(questions):
    if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
        raise HTTPException(400, "每次可以询问 1 至 3 个问题。")
    clean, identifiers = [], set()
    for question in questions:
        if not isinstance(question, dict) or set(question) - {"id", "question", "detail", "header", "options", "multiSelect", "intent"}:
            raise HTTPException(400, "问题格式无效。")
        identifier = _text(question.get("id"), 80)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier) or identifier in identifiers:
            raise HTTPException(400, "问题编号必须唯一且只包含字母、数字、下划线或短横线。")
        identifiers.add(identifier)
        item = {"id": identifier, "question": _text(question.get("question"), 1000)}
        for key, maximum in (("detail", 2000), ("header", 40)):
            if question.get(key):
                item[key] = _text(question[key], maximum)
        if type(question.get("multiSelect", False)) is not bool:
            raise HTTPException(400, "问题选项类型无效。")
        item["multiSelect"] = question.get("multiSelect", False)
        options = question.get("options", [])
        if not isinstance(options, list) or len(options) > 6:
            raise HTTPException(400, "每题最多 6 个选项。")
        item["options"] = []
        labels = set()
        for option in options:
            if not isinstance(option, dict) or set(option) - {"label", "description"}:
                raise HTTPException(400, "选项格式无效。")
            label = _text(option.get("label"), 120)
            if label in labels:
                raise HTTPException(400, "选项不能重复。")
            labels.add(label)
            item["options"].append({"label": label, "description": _text(option.get("description", ""), 500, empty=True)})
        intent = question.get("intent")
        if intent is not None:
            if (not isinstance(intent, dict) or set(intent) != {"kind", "approve"} or intent["kind"] != "plan-review"
                    or not isinstance(intent["approve"], str) or intent["approve"] not in labels):
                raise HTTPException(400, "计划问题的选项无效。")
            item["intent"] = dict(intent)
        clean.append(item)
    if len(_json(clean).encode()) > 16000:
        raise HTTPException(400, "问题内容过长。")
    return clean


def _public(row, *, include_questions=False):
    status = row["status"]
    if status == "pending" and int(row["expires_at"]) <= int(time.time()):
        status = "expired"
    result = {"id": row["id"], "status": status, "expires_at": int(row["expires_at"])}
    if status == "answered":
        result["answers"] = json.loads(row["answers_json"])
    if include_questions:
        result["questions"] = json.loads(row["questions_json"])
    return result


def _event(conn, task_id, kind, message, detail=None):
    from .agent_task_service import append_task_event, utcnow_iso
    conn.execute("UPDATE agent_tasks SET runtime_status=?,updated_at=? WHERE id=? AND status='running'",
                 ("waiting_input" if kind == "question_requested" else "running", utcnow_iso(), task_id))
    append_task_event(conn, task_id, kind, message, detail or {}, commit=False)


def create_question(conn, token, *, request_id, questions, timeout_seconds=300):
    key = _text(request_id, 128)
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key) or type(timeout_seconds) is not int or not 30 <= timeout_seconds <= 600:
        raise HTTPException(400, "提问编号或等待时限无效。")
    clean = normalize_questions(questions)
    payload_hash = hashlib.sha256(_json([clean, timeout_seconds]).encode()).hexdigest()
    grant = verify_task_delegation(conn, token, purpose="tools", required_scope="platform:read", lock_task=True)
    now = int(time.time())
    prior = conn.execute("SELECT * FROM agent_task_questions WHERE attempt_id=? AND request_key=?", (grant.attempt["id"], key)).fetchone()
    if prior:
        if prior["payload_hash"] != payload_hash:
            raise HTTPException(409, "相同提问编号不能换用其他问题。")
        return _public(prior)
    conn.execute("UPDATE agent_task_questions SET status='expired' WHERE task_id=? AND status='pending' AND expires_at<=?", (grant.task["id"], now))
    conn.execute("UPDATE agent_task_questions SET status='canceled' WHERE task_id=? AND status='pending' AND attempt_id<>?", (grant.task["id"], grant.attempt["id"]))
    if conn.execute("SELECT id FROM agent_task_questions WHERE task_id=? AND status='pending'", (grant.task["id"],)).fetchone():
        raise HTTPException(409, "当前任务已有待回答的问题。")
    if conn.execute("SELECT COUNT(*) FROM agent_task_questions WHERE task_id=?", (grant.task["id"],)).fetchone()[0] >= 5:
        raise HTTPException(429, "本任务已达到提问次数上限，请整理已有信息或明确说明未完成部分。")
    identifier = str(uuid.uuid4())
    conn.execute("""INSERT INTO agent_task_questions(id,task_id,attempt_id,fencing_token,delegation_id,actor_role,actor_id,
        request_key,payload_hash,questions_json,status,created_at,expires_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
        (identifier, grant.task["id"], grant.attempt["id"], grant.attempt["fencing_token"], grant.delegation["id"],
         grant.actor.role, grant.actor.id, key, payload_hash, _json(clean), now, now + timeout_seconds))
    _event(conn, grant.task["id"], "question_requested", "Agent 需要你的补充，回答后将继续当前任务。", {"question_id": identifier})
    return _public(conn.execute("SELECT * FROM agent_task_questions WHERE id=?", (identifier,)).fetchone())


def poll_question(conn, token, question_id, *, cancel=False):
    question_id = _text(question_id, 128)
    grant = verify_task_delegation(conn, token, purpose="tools", required_scope="platform:read", lock_task=cancel)
    row = conn.execute("SELECT * FROM agent_task_questions WHERE id=?", (question_id,)).fetchone()
    if not row or row["delegation_id"] != grant.delegation["id"] or row["attempt_id"] != grant.attempt["id"]:
        raise HTTPException(404, "待答问题不存在。")
    state = "canceled" if cancel else "expired" if int(row["expires_at"]) <= int(time.time()) else "pending"
    if row["status"] == "pending" and state != "pending":
        # Answer/create/finalize lock task before question. Expiry must use the
        # same order to avoid a task/question deadlock with a concurrent answer.
        if not cancel:
            verify_task_delegation(conn, token, purpose="tools", required_scope="platform:read", lock_task=True)
        changed = conn.execute("UPDATE agent_task_questions SET status=? WHERE id=? AND status='pending'", (state, question_id))
        if changed.rowcount == 1:
            _event(conn, row["task_id"], "question_closed", "本次等待已结束。", {"question_id": question_id, "status": state})
        row = conn.execute("SELECT * FROM agent_task_questions WHERE id=?", (question_id,)).fetchone()
    return _public(row)


def list_user_questions(conn, user, task_id):
    task = conn.execute("SELECT actor_role,actor_id,teacher_id FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
    from .agent_actor_service import task_actor_identity
    if not task or task_actor_identity(dict(task)) != (user["role"], int(user["id"])):
        raise HTTPException(403, "只能查看自己任务的问题。")
    rows = conn.execute("SELECT * FROM agent_task_questions WHERE task_id=? ORDER BY created_at DESC LIMIT 5", (task_id,)).fetchall()
    return [_public(row, include_questions=True) for row in rows]


def _normalize_answers(questions, answers):
    if not isinstance(answers, list) or len(answers) != len(questions):
        raise HTTPException(400, "请回答全部问题。")
    by_id = {}
    for answer in answers:
        if not isinstance(answer, dict) or set(answer) - {"id", "selected", "custom"} or not isinstance(answer.get("id"), str) or answer["id"] in by_id:
            raise HTTPException(400, "回答格式或编号无效。")
        by_id[answer["id"]] = answer
    result = []
    for question in questions:
        answer = by_id.get(question["id"])
        if answer is None:
            raise HTTPException(400, "回答未对应当前问题。")
        selected = answer.get("selected", [])
        labels = {item["label"] for item in question["options"]}
        if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected) or len(selected) != len(set(selected)) or set(selected) - labels:
            raise HTTPException(400, "所选答案无效。")
        custom = _text(answer.get("custom", ""), 4000, empty=True)
        if not question["multiSelect"]:
            if len(selected) > 1:
                raise HTTPException(400, "此问题只允许选择一个选项。")
            if custom:
                selected = []
        if not selected and not custom:
            raise HTTPException(400, "请选择选项或填写回答。")
        result.append({"id": question["id"], "selected": selected, **({"custom": custom} if custom else {})})
    return result


def answer_question(conn, user, task_id, question_id, answers):
    from .agent_delegation_service import verify_stored_task_delegation, _assert_session
    actor = resolve_agent_actor(conn, user["role"], user["id"])
    session = _text(user.get("session_id"), 512)
    _assert_session(conn, actor, hashlib.sha256(session.encode()).hexdigest(), int(time.time()))
    conn.execute("UPDATE agent_tasks SET status=status WHERE id=?", (task_id,))
    row = conn.execute("SELECT * FROM agent_task_questions WHERE id=? AND task_id=?", (question_id, task_id)).fetchone()
    if not row or (row["actor_role"], int(row["actor_id"])) != (actor.role, actor.id):
        raise HTTPException(403, "只能回答自己任务的问题。")
    clean = _normalize_answers(json.loads(row["questions_json"]), answers)
    if row["status"] == "answered":
        if _json(clean) != row["answers_json"]:
            raise HTTPException(409, "已提交的回答不能改写。")
        return _public(row)
    if row["status"] != "pending" or int(row["expires_at"]) <= int(time.time()):
        raise HTTPException(409, "该问题已结束等待，不能再回答。")
    verify_stored_task_delegation(conn, row["delegation_id"], purpose="tools", required_scope="platform:read", lock_task=True)
    cursor = conn.execute("UPDATE agent_task_questions SET status='answered',answers_json=?,answered_at=? WHERE id=? AND status='pending' AND expires_at>?",
                          (_json(clean), int(time.time()), question_id, int(time.time())))
    if cursor.rowcount != 1:
        raise HTTPException(409, "问题状态已变化，请刷新后核对。")
    _event(conn, task_id, "question_answered", "回答已提交，Agent 正在继续当前任务。", {"question_id": question_id})
    return _public(conn.execute("SELECT * FROM agent_task_questions WHERE id=?", (question_id,)).fetchone())


def close_attempt_questions(conn, attempt_id):
    conn.execute("UPDATE agent_task_questions SET status='canceled' WHERE attempt_id=? AND status='pending'", (attempt_id,))
