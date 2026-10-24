"""Classroom AI actions with the teacher's authority — proposed in chat, executed on confirm.

Flow (teacher only, classroom context required):

1. ``message_may_request_action`` — cheap regex pre-filter (发起投票 / 随机分组 / 结束投票 …).
2. ``plan_actions`` — a fast AI tool call (``propose_classroom_action``) turns the
   request into structured arguments; ``infer_actions_locally`` is the no-AI
   fallback (parses 选项、组数、每组人数 from the text). Everything the model says
   is *untrusted input*: ``ACTIONS[name]["plan"]`` re-validates it against the
   real classroom (roster size, option count, existing polls) and produces a preview.
3. ``create_proposals`` stores a pending row with a random token and expiry; the
   chat streams an ``action_proposal`` event and the model is told to *describe*
   the proposal, never to claim it ran.
4. ``execute_proposal`` — only the proposing teacher, only while pending and
   unexpired, re-checks classroom ownership, then calls the same service
   functions the UI uses (``poll_service.create_poll`` / ``set_poll_status``,
   ``collaboration_service.create_group_scheme`` + member placement +
   ``auto_assign_scheme_leaders``). One transaction; the row records the result.

This is the "本人确认" pattern of the AI/Agent standard §5 applied to chat: the
model only proposes, the human in the loop executes with their own session.
"""
from __future__ import annotations

import json
import math
import random
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException

from .chat_action_schema import ensure_chat_action_schema
from .resource_access_service import ensure_classroom_access

PROPOSAL_TTL_MINUTES = 30
MAX_PROPOSALS_PER_MESSAGE = 2
INTENT_MESSAGE_LIMIT = 400

STATUS_PENDING = "pending"
STATUS_EXECUTED = "executed"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"

_STATUS_LABELS = {"executed": "执行", "executing": "在执行中", "cancelled": "取消", "failed": "失败", "expired": "过期"}
_VISIBILITY_LABELS = {"always": "随时", "after_vote": "投票后", "after_close": "结束后"}

# Imperative forms only: "发起/创建/来一个 … 投票|分组", "随机分组", "分成 N 组", "结束投票".
# Read-only questions about polls/groups ("看看投票结果", "分析小组作业") must NOT trigger.
_ACTION_PATTERN = re.compile(
    r"((发起|创建|新建|开始|开启|启动|来一个|来个|做一个|搞一个|弄一个|建一个|组织一次|进行一次|发布一个|帮我(发起|创建|建|开|做|搞|弄))"
    r"[^。！？\n]{0,8}(投票|表决|分组|分个组)"
    r"|(随机|重新|自动)分组|分成\s*\d+\s*(个)?组|每组\s*\d+\s*(人|个|名)"
    r"|(结束|关闭|停止|关掉)[^。！？\n]{0,8}投票|投票[^。！？\n]{0,6}(结束|关闭|停止|关掉))"
)
_READONLY_PATTERN = re.compile(r"看看|查看|查一下|看一下|看下|分析|结果|情况|统计|怎么样|如何|哪些|哪个组|谁|多少|名单|票数|成员")
_CLOSE_POLL_PATTERN = re.compile(r"(结束|关闭|停止|关掉)[^。！？\n]{0,8}投票|投票[^。！？\n]{0,6}(结束|关闭|停止|关掉)")
_GROUP_PATTERN = re.compile(r"分组|分成\s*\d+\s*组|每组\s*\d+\s*人|分个组|小组")
_POLL_PATTERN = re.compile(r"投票|表决")
_GROUP_COUNT_RE = re.compile(r"分成?\s*(\d{1,2})\s*(?:个)?组|(\d{1,2})\s*(?:个)?组")
_GROUP_SIZE_RE = re.compile(r"每组\s*(\d{1,2})\s*(?:人|个|名)|(\d{1,2})\s*(?:人|名)\s*一组")
_OPTIONS_RE = re.compile(r"(?:选项|候选|备选)[:：]?\s*(.+?)(?:[，,。；;]\s*(?:\d+\s*分钟|截止|多选|单选|立即|草稿|不限)[^，,]*)*$")
_TITLE_RE = re.compile(r"(?:投票|表决)[:：「“\"]?\s*([^「」“”\"：:，,。！？\n]{2,60})")
_SPLIT_RE = re.compile(r"\s*[、，,；;/｜|]\s*|\s+(?:和|或|还是)\s+")


def _now() -> datetime:
    return datetime.now()


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _normalize_message(message: Any) -> str:
    return " ".join(str(message or "").split()).strip()[:INTENT_MESSAGE_LIMIT]


def message_may_request_action(message: str) -> bool:
    text = _normalize_message(message)
    match = _ACTION_PATTERN.search(text)
    if not match:
        return False
    # "看看投票结果" / "分析一下分组情况" are questions for the read views, not actions —
    # unless the message also carries an explicit imperative (发起/结束/分成 N 组…).
    if _READONLY_PATTERN.search(text) and not re.search(r"发起|创建|新建|开始|开启|结束|关闭|停止|关掉|分成\s*\d+|每组\s*\d+|随机分组|重新分组", text):
        return False
    return True


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _load_offering(conn: Any, user: dict[str, Any], class_offering_id: int) -> dict[str, Any]:
    if str(user.get("role") or "").lower() != "teacher":
        raise HTTPException(403, "只有教师可以执行课堂动作")
    return dict(ensure_classroom_access(conn, int(class_offering_id), user))


def _eligible_students(conn: Any, class_offering_id: int, *, include_retake: bool) -> list[dict[str, Any]]:
    from .offering_membership_service import load_offering_students

    students = load_offering_students(conn, int(class_offering_id), active_only=True)
    if include_retake or not students:
        return students
    try:
        from .classroom_retake_service import get_confirmed_retake_students

        retake_ids = {int(item.get("student_id") or 0) for item in get_confirmed_retake_students(conn, class_offering_id=int(class_offering_id))}
    except Exception:  # noqa: BLE001 — retake registry is optional
        retake_ids = set()
    return [s for s in students if int(s["id"]) not in retake_ids]


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "要", "需要"}


def _clean_options(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [part for part in _SPLIT_RE.split(raw) if part]
    labels: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        label = item.get("label") if isinstance(item, dict) else item
        text = " ".join(str(label or "").split()).strip("。.;；")[:160]
        if text and text not in labels:
            labels.append(text)
    return labels[:12]


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


# --------------------------------------------------------------------------
# create_poll
# --------------------------------------------------------------------------
def _plan_create_poll(conn: Any, user: dict[str, Any], offering: dict[str, Any], args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    options = _clean_options(args.get("options"))
    if len(options) < 2:
        raise ValueError("投票至少需要 2 个选项，请补充选项（例如：选项 A、B、C）")
    vote_type = "multiple" if str(args.get("vote_type") or "").lower() in {"multiple", "多选"} or _as_bool(args.get("allow_multiple"), False) else "single"
    title = _text(args.get("title"), 120) or f"课堂投票 {_now().strftime('%m-%d %H:%M')}"
    deadline_minutes = _as_int(args.get("deadline_minutes"))
    deadline_at = ""
    if deadline_minutes and 1 <= deadline_minutes <= 60 * 24 * 30:
        deadline_at = (_now() + timedelta(minutes=deadline_minutes)).strftime("%Y-%m-%dT%H:%M")
    publish = _as_bool(args.get("publish"), True)
    result_visibility = str(args.get("result_visibility") or "after_vote")
    if result_visibility not in _VISIBILITY_LABELS:
        result_visibility = "after_vote"
    params = {
        "title": title,
        "description": _text(args.get("description"), 1000),
        "options": options,
        "vote_type": vote_type,
        "deadline_at": deadline_at,
        "publish": publish,
        "result_visibility": result_visibility,
    }
    from .offering_membership_service import count_offering_active_students

    audience = count_offering_active_students(conn, int(offering["id"]))
    preview = {
        "title": f"发起投票：{title}",
        "lines": [
            f"课堂：{offering.get('course_name')} · {offering.get('class_name')}",
            f"类型：{'多选' if vote_type == 'multiple' else '单选'}；结果可见：{_VISIBILITY_LABELS[result_visibility]}",
            f"选项（{len(options)}）：{'、'.join(options)}",
            f"截止：{deadline_at.replace('T', ' ') if deadline_at else '不限'}",
            f"状态：{'立即发布，通知全班 ' + str(audience) + ' 名学生' if publish else '仅保存为草稿'}",
        ],
    }
    return params, preview


def _execute_create_poll(conn: Any, user: dict[str, Any], offering: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    from .poll_service import ORIGIN_CLASSROOM, create_poll

    status = "active" if params.get("publish", True) else "draft"
    payload = {
        "title": params["title"],
        "description": params.get("description") or "",
        "options": params["options"],
        "vote_type": params.get("vote_type") or "single",
        "deadline_at": params.get("deadline_at") or None,
        "result_visibility": params.get("result_visibility") or "after_vote",
        "status": status,
    }
    poll = create_poll(conn, user, payload, origin=ORIGIN_CLASSROOM, class_offering_id=int(offering["id"]))
    return {
        "kind": "poll",
        "poll_id": int(poll["id"]),
        "class_offering_id": int(offering["id"]),
        "status": str(poll.get("status") or status),
        "title": str(poll.get("title") or params["title"]),
        "link_url": f"/classroom/{int(offering['id'])}#classroom-activity-panel-polls",
        "summary": f"投票「{poll.get('title')}」已{'发布' if status == 'active' else '保存为草稿'}（{len(params['options'])} 个选项）。",
        "notify_poll_active": status == "active",
    }


# --------------------------------------------------------------------------
# close_poll
# --------------------------------------------------------------------------
def _plan_close_poll(conn: Any, user: dict[str, Any], offering: dict[str, Any], args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    keyword = _text(args.get("poll_keyword") or args.get("title"), 60).lower()
    row = conn.execute(
        """
        SELECT p.id, p.title, p.status FROM polls p JOIN poll_assignments pa ON pa.poll_id = p.id
        WHERE pa.class_offering_id = ? AND p.status = 'active' AND (? = '' OR lower(p.title) LIKE ? ESCAPE '\\')
        ORDER BY p.updated_at DESC, p.id DESC LIMIT 1
        """,
        (int(offering["id"]), keyword, _like(keyword)),
    ).fetchone()
    if not row:
        raise ValueError("本课堂当前没有进行中的投票可以结束" + (f"（关键词「{keyword}」）" if keyword else ""))
    voters = conn.execute("SELECT COUNT(*) AS n FROM poll_ballots WHERE poll_id = ?", (int(row["id"]),)).fetchone()
    params = {"poll_id": int(row["id"]), "title": str(row["title"])}
    preview = {
        "title": f"结束投票：{row['title']}",
        "lines": [
            f"课堂：{offering.get('course_name')} · {offering.get('class_name')}",
            f"当前已投 {int(voters['n'] or 0)} 人；结束后学生不能再投票，结果按可见规则公布。",
        ],
    }
    return params, preview


def _execute_close_poll(conn: Any, user: dict[str, Any], offering: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    from .poll_service import set_poll_status

    poll = set_poll_status(conn, int(params["poll_id"]), user, "closed")
    return {
        "kind": "poll",
        "poll_id": int(params["poll_id"]),
        "class_offering_id": int(offering["id"]),
        "status": str(poll.get("status") or "closed"),
        "title": str(poll.get("title") or params.get("title") or ""),
        "link_url": f"/classroom/{int(offering['id'])}#classroom-activity-panel-polls",
        "summary": f"投票「{poll.get('title') or params.get('title')}」已结束。",
    }


# --------------------------------------------------------------------------
# create_random_groups
# --------------------------------------------------------------------------
def _group_layout(total: int, group_count: int | None, group_size: int | None) -> tuple[int, int, int]:
    """Return (group_count, base_size, remainder) for an even split of ``total`` students."""
    if group_count is None and group_size is None:
        group_size = 5
    if group_count is None:
        group_size = max(1, min(int(group_size or 5), 12))
        group_count = math.ceil(total / group_size)
    group_count = max(1, min(int(group_count), 60, total))
    base, rem = divmod(total, group_count)
    if base + (1 if rem else 0) > 12:
        raise ValueError(f"{total} 名学生分成 {group_count} 组，每组会超过 12 人上限，请增加组数")
    return group_count, base, rem


def _plan_create_random_groups(conn: Any, user: dict[str, Any], offering: dict[str, Any], args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    include_retake = _as_bool(args.get("include_retake"), False)
    students = _eligible_students(conn, int(offering["id"]), include_retake=include_retake)
    total = len(students)
    if total < 2:
        raise ValueError("当前课堂可参与分组的学生不足 2 人")
    group_count, base, rem = _group_layout(total, _as_int(args.get("group_count")), _as_int(args.get("group_size")))
    name = _text(args.get("name"), 60) or f"随机分组 {_now().strftime('%m-%d %H:%M')}"
    auto_leaders = _as_bool(args.get("auto_leaders"), True)
    params = {
        "name": name,
        "description": _text(args.get("description"), 600),
        "group_count": group_count,
        "include_retake": include_retake,
        "auto_leaders": auto_leaders,
    }
    sizes = [base + (1 if i < rem else 0) for i in range(group_count)]
    preview = {
        "title": f"随机分组：{name}",
        "lines": [
            f"课堂：{offering.get('course_name')} · {offering.get('class_name')}",
            f"参与学生 {total} 人（{'含' if include_retake else '不含'}重修/插班），分成 {group_count} 组，每组 {min(sizes)}-{max(sizes)} 人",
            f"分配方式：按名册随机打乱后平均分配；{'自动按修为配置组长' if auto_leaders else '不配置组长'}",
            "学生可在课堂协作区查看本组成员。",
        ],
    }
    return params, preview


def _execute_create_random_groups(conn: Any, user: dict[str, Any], offering: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    from . import collaboration_service as collab

    students = _eligible_students(conn, int(offering["id"]), include_retake=bool(params.get("include_retake")))
    total = len(students)
    if total < 2:
        raise HTTPException(400, "当前课堂可参与分组的学生不足 2 人")
    try:
        group_count, base, rem = _group_layout(total, _as_int(params.get("group_count")), None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    scheme = collab.create_group_scheme(
        conn,
        int(offering["id"]),
        user,
        {
            "name": params.get("name"),
            "description": params.get("description") or "由课堂 AI 按名册随机分配",
            "min_members": max(1, base),
            "max_members": min(12, base + (1 if rem else 0)),
            "group_count": group_count,
        },
    )
    scheme_id = int(scheme["id"])
    groups = collab._scheme_group_rows(conn, scheme_id)  # noqa: SLF001 — same placement path as redistribute_scheme_groups
    pool = [int(s["id"]) for s in students]
    random.shuffle(pool)
    now = _iso(_now())
    cursor = 0
    for position, group in enumerate(groups[:group_count]):
        size = base + (1 if position < rem else 0)
        chunk = pool[cursor : cursor + size]
        cursor += size
        for student_id in chunk:
            collab._upsert_member(  # noqa: SLF001
                conn,
                group_id=int(group["id"]),
                student_id=int(student_id),
                member_role="member",
                added_by_role="teacher",
                added_by_user_pk=int(user["id"]),
            )
        conn.execute("UPDATE study_groups SET updated_at = ? WHERE id = ?", (now, int(group["id"])))
    collab._cleanup_empty_scheme_groups(conn, scheme_id)  # noqa: SLF001
    leaders_assigned = 0
    if params.get("auto_leaders", True):
        try:
            leaders_assigned = int(collab.auto_assign_scheme_leaders(conn, scheme_id, user).get("assigned") or 0)
        except HTTPException:
            leaders_assigned = 0
    detail = collab._serialize_scheme(conn, collab._load_scheme(conn, scheme_id), user)  # noqa: SLF001
    group_lines = []
    for group in detail.get("groups") or []:
        names = [f"{m.get('name')}{'(组长)' if m.get('is_leader') else ''}" for m in group.get("members") or []]
        group_lines.append(f"{group.get('name')}：{'、'.join(names)}")
    return {
        "kind": "group_scheme",
        "scheme_id": scheme_id,
        "class_offering_id": int(offering["id"]),
        "title": str(detail.get("name") or params.get("name") or ""),
        "group_count": len(detail.get("groups") or []),
        "grouped": int(detail.get("grouped_count") or total),
        "leaders_assigned": leaders_assigned,
        "groups": group_lines[:60],
        "link_url": f"/classroom/{int(offering['id'])}#collaboration-panel",
        "summary": f"分组方案「{detail.get('name')}」已创建：{len(detail.get('groups') or [])} 组，{total} 人已分配，{leaders_assigned} 个组已配置组长。",
    }


ACTIONS: dict[str, dict[str, Any]] = {
    "create_poll": {
        "label": "发起投票",
        "description": "在当前课堂发起一个投票（标题、2-12 个选项、单选/多选、可选截止分钟数、是否立即发布）。",
        "schema": {
            "title": {"type": "string", "description": "投票标题"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "2-12 个选项"},
            "vote_type": {"type": "string", "enum": ["single", "multiple"]},
            "description": {"type": "string"},
            "deadline_minutes": {"type": "integer", "description": "多少分钟后截止；不填=不限"},
            "publish": {"type": "boolean", "description": "是否立即发布，默认 true"},
        },
        "plan": _plan_create_poll,
        "execute": _execute_create_poll,
    },
    "close_poll": {
        "label": "结束投票",
        "description": "结束当前课堂一个进行中的投票（按标题关键词；不填=最近一个）。",
        "schema": {"poll_keyword": {"type": "string", "description": "投票标题关键词，可空"}},
        "plan": _plan_close_poll,
        "execute": _execute_close_poll,
    },
    "create_random_groups": {
        "label": "随机分组",
        "description": "按当前课堂名册随机分组：给组数或每组人数，可选方案名、是否自动配置组长、是否包含重修插班生。",
        "schema": {
            "name": {"type": "string", "description": "方案名称，可空"},
            "group_count": {"type": "integer", "description": "分成几组"},
            "group_size": {"type": "integer", "description": "每组几人（与 group_count 二选一）"},
            "auto_leaders": {"type": "boolean", "description": "是否自动配置组长，默认 true"},
            "include_retake": {"type": "boolean", "description": "是否包含重修/插班生，默认 false"},
        },
        "plan": _plan_create_random_groups,
        "execute": _execute_create_random_groups,
    },
}


def action_tool_schema() -> dict[str, Any]:
    descriptions = "\n".join(f"- {key}：{meta['description']}" for key, meta in ACTIONS.items())
    return {
        "type": "function",
        "function": {
            "name": "propose_classroom_action",
            "description": "把教师的课堂操作要求转成结构化动作提案（只提案，不执行；教师会在界面确认）。可用动作：\n" + descriptions,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS.keys())},
                    "args": {"type": "object", "additionalProperties": True, "description": "该动作的参数"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    }


# --------------------------------------------------------------------------
# planning (AI + local fallback)
# --------------------------------------------------------------------------
def infer_actions_locally(message: str) -> list[dict[str, Any]]:
    text = _normalize_message(message)
    if not text:
        return []
    calls: list[dict[str, Any]] = []
    if _CLOSE_POLL_PATTERN.search(text):
        calls.append({"action": "close_poll", "args": {"poll_keyword": ""}})
    elif _POLL_PATTERN.search(text):
        args: dict[str, Any] = {}
        option_match = _OPTIONS_RE.search(text)
        if option_match:
            args["options"] = _clean_options(option_match.group(1))
        title_match = _TITLE_RE.search(text)
        if title_match:
            args["title"] = title_match.group(1).strip()
        if "多选" in text:
            args["vote_type"] = "multiple"
        minutes = re.search(r"(\d{1,4})\s*分钟", text)
        if minutes:
            args["deadline_minutes"] = int(minutes.group(1))
        calls.append({"action": "create_poll", "args": args})
    if _GROUP_PATTERN.search(text):
        args = {}
        count = _GROUP_COUNT_RE.search(text)
        if count:
            args["group_count"] = int(count.group(1) or count.group(2))
        size = _GROUP_SIZE_RE.search(text)
        if size:
            args["group_size"] = int(size.group(1) or size.group(2))
        if re.search(r"不(要|用|需要)?(自动)?(配|选|设)组长", text):
            args["auto_leaders"] = False
        if re.search(r"(包含|包括|带上|算上).{0,4}(重修|插班)", text):
            args["include_retake"] = True
        calls.append({"action": "create_random_groups", "args": args})
    return calls[:MAX_PROPOSALS_PER_MESSAGE]


def _sanitize_args(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[str(key)[:40]] = value
        elif isinstance(value, list):
            clean[str(key)[:40]] = [item for item in value if isinstance(item, (str, int, float, dict))][:20]
    return clean


async def plan_actions(message: str) -> list[dict[str, Any]]:
    """Fast AI tool-call planning with the local parser as guardrail and fallback."""
    cleaned = _normalize_message(message)
    if not cleaned:
        return []
    local = infer_actions_locally(cleaned)
    try:
        from ..core import ai_client

        payload = {
            "system_prompt": (
                "你是课堂教学平台的动作提案规划器。教师提出的要求如果属于“发起投票 / 结束投票 / 随机分组”，"
                "调用 propose_classroom_action 给出结构化参数：投票要提取标题与全部选项（选项不要漏，保持原文），"
                "分组要提取组数或每组人数。只提案，不执行，不要编造教师没说的选项。与这三类无关时不要调用工具。"
            ),
            "messages": [],
            "new_message": cleaned,
            "base64_urls": [],
            "file_texts": [],
            "model_capability": "standard",
            "task_type": "fast_text_response",
            "task_priority": "interactive",
            "task_label": "classroom_action_plan",
            "tools": [action_tool_schema()],
            "tool_choice": "auto",
        }
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=20.0)
        response.raise_for_status()
        data = response.json()
    except Exception:  # noqa: BLE001 — planner outage → local parser
        return local
    calls: list[dict[str, Any]] = []
    for raw in (data.get("tool_calls") or []) if isinstance(data, dict) else []:
        if not isinstance(raw, dict) or raw.get("name") != "propose_classroom_action":
            continue
        arguments = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {}
        action = str(arguments.get("action") or "").strip()
        if action not in ACTIONS:
            continue
        args = _sanitize_args(arguments.get("args"))
        local_match = next((item for item in local if item["action"] == action), None)
        if local_match:
            # Numbers / options the teacher typed literally beat the model's paraphrase.
            for key, value in local_match["args"].items():
                if value not in (None, "", []):
                    args[key] = value
        calls.append({"action": action, "args": args})
        if len(calls) >= MAX_PROPOSALS_PER_MESSAGE:
            break
    return calls or local


# --------------------------------------------------------------------------
# proposals
# --------------------------------------------------------------------------
def create_proposals(
    conn: Any,
    user: dict[str, Any],
    class_offering_id: int,
    calls: list[dict[str, Any]],
    *,
    source_message: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate planned calls against the real classroom; store the valid ones. Does not commit."""
    ensure_chat_action_schema(conn)
    offering = _load_offering(conn, user, int(class_offering_id))
    prune_old_proposals(conn)
    proposals: list[dict[str, Any]] = []
    problems: list[str] = []
    for call in calls[:MAX_PROPOSALS_PER_MESSAGE]:
        action = str(call.get("action") or "")
        meta = ACTIONS.get(action)
        if not meta:
            continue
        try:
            params, preview = meta["plan"](conn, user, offering, call.get("args") or {})
        except ValueError as exc:
            problems.append(f"{meta['label']}：{exc}")
            continue
        except HTTPException as exc:
            problems.append(f"{meta['label']}：{exc.detail}")
            continue
        token = secrets.token_urlsafe(18)
        created = _now()
        expires = created + timedelta(minutes=PROPOSAL_TTL_MINUTES)
        conn.execute(
            """
            INSERT INTO ai_chat_action_proposals
                (token, teacher_id, class_offering_id, action, params_json, preview_json, status, source_message, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                int(user["id"]),
                int(class_offering_id),
                action,
                json.dumps(params, ensure_ascii=False),
                json.dumps(preview, ensure_ascii=False),
                STATUS_PENDING,
                _normalize_message(source_message),
                _iso(created),
                _iso(expires),
            ),
        )
        proposals.append(
            {
                "token": token,
                "action": action,
                "label": meta["label"],
                "title": preview.get("title") or meta["label"],
                "lines": list(preview.get("lines") or []),
                "expires_at": _iso(expires),
                "class_offering_id": int(class_offering_id),
            }
        )
    return proposals, problems


def build_action_proposal_block(proposals: list[dict[str, Any]], problems: list[str]) -> str:
    if not proposals and not problems:
        return ""
    lines = ["\n\n--- 课堂动作提案（系统已生成，等待教师在聊天界面点击“确认执行”）---"]
    for index, proposal in enumerate(proposals, start=1):
        lines.append(f"[提案{index}] {proposal['title']}")
        for line in proposal.get("lines") or []:
            lines.append(f"  - {line}")
    for problem in problems:
        lines.append(f"[无法生成提案] {problem}")
    lines.append(
        "回答要求：用 2-4 句话复述提案要点（选项/组数/人数等），告诉教师点击提案卡片上的“确认执行”即可生效，"
        "也可以回复修改意见后重新生成；绝对不要说“已经发起/已经分好组”。对无法生成的提案，说明原因并询问缺少的信息。"
    )
    return "\n".join(lines)


def load_proposal(conn: Any, token: str) -> dict[str, Any] | None:
    ensure_chat_action_schema(conn)
    row = conn.execute("SELECT * FROM ai_chat_action_proposals WHERE token = ? LIMIT 1", (str(token or ""),)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["params"] = json.loads(item.get("params_json") or "{}")
    item["preview"] = json.loads(item.get("preview_json") or "{}")
    item["result"] = json.loads(item.get("result_json") or "{}") if item.get("result_json") else {}
    return item


def _require_owned_pending(conn: Any, user: dict[str, Any], token: str) -> dict[str, Any]:
    proposal = load_proposal(conn, token)
    if not proposal or int(proposal["teacher_id"]) != int(user.get("id") or 0) or str(user.get("role")) != "teacher":
        raise HTTPException(404, "提案不存在或不属于你")
    if proposal["status"] != STATUS_PENDING:
        raise HTTPException(409, f"提案已{_STATUS_LABELS.get(proposal['status'], proposal['status'])}")
    if str(proposal.get("expires_at") or "") < _iso(_now()):
        conn.execute("UPDATE ai_chat_action_proposals SET status = ? WHERE token = ?", (STATUS_EXPIRED, token))
        raise HTTPException(409, "提案已过期，请重新向 AI 提出要求")
    return proposal


def prune_old_proposals(conn: Any, *, days: int = 7) -> int:
    """Drop proposals older than ``days`` (any status); cheap because of the teacher/status index."""
    cutoff = _iso(_now() - timedelta(days=days))
    cursor = conn.execute("DELETE FROM ai_chat_action_proposals WHERE created_at < ?", (cutoff,))
    return int(cursor.rowcount or 0) if cursor is not None else 0


def execute_proposal(conn: Any, user: dict[str, Any], token: str) -> dict[str, Any]:
    """Run the confirmed action with the teacher's own identity. Caller commits.

    The row is *claimed* first (``pending`` → ``executing`` with a conditional
    UPDATE), so two concurrent confirmations of the same token can never both run
    the action: the loser sees zero updated rows and gets 409.
    """
    proposal = _require_owned_pending(conn, user, token)
    meta = ACTIONS.get(str(proposal["action"]))
    if not meta:
        raise HTTPException(400, "未知动作")
    claimed = conn.execute(
        "UPDATE ai_chat_action_proposals SET status = 'executing' WHERE token = ? AND teacher_id = ? AND status = ?",
        (token, int(user["id"]), STATUS_PENDING),
    )
    if int(getattr(claimed, "rowcount", 0) or 0) != 1:
        raise HTTPException(409, "提案正在执行或已处理，请勿重复点击")
    offering = _load_offering(conn, user, int(proposal["class_offering_id"]))
    result = meta["execute"](conn, user, offering, proposal["params"])
    conn.execute(
        "UPDATE ai_chat_action_proposals SET status = ?, result_json = ?, executed_at = ? WHERE token = ?",
        (STATUS_EXECUTED, json.dumps(result, ensure_ascii=False, default=str), _iso(_now()), token),
    )
    return {"token": token, "action": proposal["action"], "label": meta["label"], **result}


def mark_proposal_failed(token: str, detail: str) -> None:
    """Record a failed execution on a fresh connection (the caller's transaction was rolled back)."""
    from ..database import get_db_connection

    try:
        with get_db_connection() as conn:
            ensure_chat_action_schema(conn)
            conn.execute(
                "UPDATE ai_chat_action_proposals SET status = ?, error_text = ?, executed_at = ? WHERE token = ? AND status IN ('pending', 'executing')",
                (STATUS_FAILED, str(detail)[:500], _iso(_now()), token),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[CHAT-ACTION] could not record failure for {token[:8]}…: {exc}")


def cancel_proposal(conn: Any, user: dict[str, Any], token: str) -> dict[str, Any]:
    proposal = _require_owned_pending(conn, user, token)
    conn.execute("UPDATE ai_chat_action_proposals SET status = ? WHERE token = ?", (STATUS_CANCELLED, token))
    return {"token": token, "action": proposal["action"], "status": STATUS_CANCELLED}
