"""聊天 AI 实时查库（G9）—— 轻问题不动用 Agent 队列。

教师在聊天里问「三班几个人没交作业」这类轻量数据问题时：
1. 本地正则粗筛（高召回、零成本）；
2. 快速 AI 从**白名单视图**里规划最多 2 轮 platform_query 工具调用（少而准，不让模型写 SQL）；
3. 以当前教师身份执行视图查询（内部函数，不经 HTTP 桥接，范围窄于 Agent）；
4. 把结果表格拼进 system_prompt，流式回答引用真实数字。

任何环节失败都降级为普通对话，并提示可转 Agent 任务。学生角色不触发。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

INTENT_MESSAGE_LIMIT = 400
MAX_RESULT_ROWS = 100
RESULT_BLOCK_LIMIT = 6000
MAX_PLATFORM_TOOL_CALLS = 2
MAX_PLANNED_PLATFORM_TOOL_CALLS = MAX_PLATFORM_TOOL_CALLS

# 粗筛：数据型轻问题的高召回信号。
_DATA_QUESTION_PATTERN = re.compile(
    r"(没交|未交|交了|提交率|提交情况|多少(人|个)|几个(人|学生)|人数|名单|花名册"
    r"|成绩|分数|低分|平均分|及格|不及格|低于\s*\d{1,3}(?:\.\d{1,2})?\s*分"
    r"|多少学生|几个学生"
    r"|日程|课表|安排.{0,4}(考试|监考)|监考|我的课堂|哪些课堂|哪些班)"
)

# 白名单视图：key -> 描述（给意图 AI 选择用）+ 执行函数。
VIEW_DESCRIPTIONS: dict[str, str] = {
    "my_classrooms": "我的课堂列表（课程、班级、学生数）。参数：无",
    "class_roster": "班级学生名册。参数 class_keyword（班级或课程名关键词，可空=全部）",
    "assignment_submission_status": (
        "某次作业的提交情况：提交数/未交名单。参数 assignment_keyword（作业名关键词，可空=最近一次作业）、"
        "class_keyword（课堂关键词，可空）"
    ),
    "low_scores": "低分学生名单（最近批改的作业/考试）。参数 threshold（分数线，默认 60）、class_keyword（可空）",
    "my_schedule": "我未来 7 天的日程（考试/监考/待办）。参数：无",
}


def platform_query_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "platform_query",
            "description": (
                "查询 LanShare 教学平台的轻量只读白名单视图。只用于教师当前身份可见的课堂、"
                "作业提交、成绩、日程等即时问题；不要写 SQL。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "view": {
                        "type": "string",
                        "enum": list(VIEW_DESCRIPTIONS.keys()),
                        "description": "要查询的白名单视图。",
                    },
                    "params": {
                        "type": "object",
                        "description": "视图参数，例如 class_keyword、assignment_keyword、threshold。",
                        "additionalProperties": True,
                    },
                },
                "required": ["view"],
                "additionalProperties": False,
            },
        },
    }


def message_may_need_platform_data(message: str) -> bool:
    return bool(_DATA_QUESTION_PATTERN.search(str(message or "")))


def _extract_class_keyword(message: str) -> str:
    match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{1,16}班)", message)
    return match.group(1)[:40] if match else ""


def _extract_score_threshold(message: str) -> float | None:
    match = re.search(r"(\d{1,3}(?:\.\d{1,2})?)\s*分", message)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if 0 <= value <= 150 else None


def infer_platform_query_intent(message: str) -> dict[str, Any] | None:
    """本地高置信兜底：意图 AI 不可用时仍能覆盖最高频轻量查询。"""
    plan = infer_platform_query_tool_calls(message)
    calls = plan.get("tool_calls") if isinstance(plan, dict) else None
    return calls[0] if calls else None


def _append_tool_call(calls: list[dict[str, Any]], view: str, params: dict[str, Any] | None = None) -> None:
    if view not in VIEW_DESCRIPTIONS:
        return
    normalized = {"view": view, "params": params or {}}
    fingerprint = jsonish_fingerprint(normalized)
    if any(jsonish_fingerprint(item) == fingerprint for item in calls):
        return
    calls.append(normalized)


def jsonish_fingerprint(value: Any) -> tuple:
    if isinstance(value, dict):
        return tuple(sorted((str(key), jsonish_fingerprint(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(jsonish_fingerprint(item) for item in value)
    return (type(value).__name__, str(value))


def infer_platform_query_tool_calls(message: str) -> dict[str, Any]:
    """本地高置信兜底：为常见组合轻查询规划 0~N 个 platform_query 调用。"""
    text = str(message or "").strip()
    if not text or not message_may_need_platform_data(text):
        return {"related": False, "tool_calls": [], "needs_agent": False, "planner_source": "local_fallback"}
    class_keyword = _extract_class_keyword(text)
    base_params: dict[str, Any] = {}
    if class_keyword:
        base_params["class_keyword"] = class_keyword

    calls: list[dict[str, Any]] = []
    if re.search(r"(没交|未交|提交率|提交情况|交了)", text):
        _append_tool_call(calls, "assignment_submission_status", dict(base_params))
    if re.search(r"(低分|不及格|及格|分数|成绩|低于|小于|\d{1,3}(?:\.\d{1,2})?\s*分)", text):
        threshold = _extract_score_threshold(text)
        params = dict(base_params)
        if threshold is not None:
            params["threshold"] = threshold
        _append_tool_call(calls, "low_scores", params)
    if re.search(r"(花名册|名册|学生名单|名单|人数|多少(人|个|学生)|几个(人|学生))", text):
        _append_tool_call(calls, "class_roster", dict(base_params))
    if re.search(r"(日程|课表|监考|考试|安排)", text):
        _append_tool_call(calls, "my_schedule", {})
    if re.search(r"(我的课堂|哪些课堂|哪些班)", text):
        _append_tool_call(calls, "my_classrooms", {})
    raw_call_count = len(calls)
    needs_agent = bool(re.search(r"(深入|分析|生成|方案|报告|总结|跨.{0,4}对比|长期|趋势)", text))
    agent_reason = ""
    if raw_call_count > MAX_PLATFORM_TOOL_CALLS:
        agent_reason = f"轻量查询最多执行 {MAX_PLATFORM_TOOL_CALLS} 次，当前问题需要 {raw_call_count} 个数据视图。"
    return {
        "related": bool(calls),
        "tool_calls": calls[:MAX_PLANNED_PLATFORM_TOOL_CALLS],
        "needs_agent": needs_agent or raw_call_count > MAX_PLATFORM_TOOL_CALLS,
        "agent_reason": agent_reason,
        "planner_source": "local_fallback",
    }


def _sanitize_tool_call(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    view = str(raw.get("view") or "").strip()
    if view not in VIEW_DESCRIPTIONS:
        return None
    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    safe_params: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or isinstance(value, (str, int, float, bool)):
            safe_params[str(key)[:40]] = value
    return {"view": view, "params": safe_params}


def _normalize_tool_call_plan(parsed: Any) -> dict[str, Any] | None:
    if not isinstance(parsed, dict):
        return None
    if "tool_calls" in parsed:
        raw_calls = parsed.get("tool_calls") if isinstance(parsed.get("tool_calls"), list) else []
    elif parsed.get("related") and parsed.get("view"):
        raw_calls = [{"view": parsed.get("view"), "params": parsed.get("params") or {}}]
    else:
        raw_calls = []
    calls: list[dict[str, Any]] = []
    for raw in raw_calls:
        call = _sanitize_tool_call(raw)
        if call:
            _append_tool_call(calls, call["view"], call["params"])
    if not calls and not parsed.get("related"):
        return {"related": False, "tool_calls": [], "needs_agent": False, "planner_source": "json_plan"}
    return {
        "related": bool(calls),
        "tool_calls": calls[:MAX_PLANNED_PLATFORM_TOOL_CALLS],
        "needs_agent": bool(parsed.get("needs_agent")) or len(calls) > MAX_PLATFORM_TOOL_CALLS,
        "agent_reason": str(parsed.get("agent_reason") or "").strip()[:120],
        "planner_source": "json_plan",
    }


def _merge_provider_calls_with_local_guardrails(
    message: str, provider_calls: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], bool, bool, str]:
    """Keep provider-native tool use, but normalize high-confidence view choice/order."""
    if not provider_calls:
        return [], False, False, ""
    local_plan = infer_platform_query_tool_calls(message)
    raw_local_calls = local_plan.get("tool_calls") if isinstance(local_plan, dict) else []
    local_budget_reason = str(local_plan.get("agent_reason") or "")
    if not isinstance(raw_local_calls, list) or not raw_local_calls:
        budget_exceeded = len(provider_calls) > MAX_PLATFORM_TOOL_CALLS
        budget_reason = (
            f"轻量查询最多执行 {MAX_PLATFORM_TOOL_CALLS} 次，当前问题规划了 {len(provider_calls)} 次查询。"
            if budget_exceeded
            else ""
        )
        return provider_calls[:MAX_PLANNED_PLATFORM_TOOL_CALLS], False, budget_exceeded, budget_reason

    provider_params_by_view: dict[str, dict[str, Any]] = {}
    for call in provider_calls:
        if not isinstance(call, dict):
            continue
        view = str(call.get("view") or "")
        params = call.get("params") if isinstance(call.get("params"), dict) else {}
        if view and view not in provider_params_by_view:
            provider_params_by_view[view] = dict(params)

    local_view_order = [
        str(call.get("view") or "")
        for call in raw_local_calls
        if isinstance(call, dict) and str(call.get("view") or "") in VIEW_DESCRIPTIONS
    ]
    provider_specific_views = [
        str(call.get("view") or "")
        for call in provider_calls
        if isinstance(call, dict)
        and str(call.get("view") or "") in VIEW_DESCRIPTIONS
        and str(call.get("view") or "") != "my_classrooms"
    ]
    if provider_specific_views and all(view in local_view_order for view in provider_specific_views):
        selected_views = set(provider_specific_views)
        selected_local_calls = [
            call
            for call in raw_local_calls
            if isinstance(call, dict) and str(call.get("view") or "") in selected_views
        ]
    else:
        selected_local_calls = raw_local_calls

    guarded_calls: list[dict[str, Any]] = []
    for raw_call in selected_local_calls:
        call = _sanitize_tool_call(raw_call)
        if not call:
            continue
        params = dict(provider_params_by_view.get(call["view"], {}))
        for key, value in call["params"].items():
            if value not in ("", None):
                params[key] = value
        _append_tool_call(guarded_calls, call["view"], params)

    if not guarded_calls:
        budget_exceeded = len(provider_calls) > MAX_PLATFORM_TOOL_CALLS
        budget_reason = (
            f"轻量查询最多执行 {MAX_PLATFORM_TOOL_CALLS} 次，当前问题规划了 {len(provider_calls)} 次查询。"
            if budget_exceeded
            else ""
        )
        return provider_calls[:MAX_PLANNED_PLATFORM_TOOL_CALLS], False, budget_exceeded, budget_reason
    limited = guarded_calls[:MAX_PLANNED_PLATFORM_TOOL_CALLS]
    changed = jsonish_fingerprint(limited) != jsonish_fingerprint(provider_calls[:MAX_PLANNED_PLATFORM_TOOL_CALLS])
    budget_exceeded = (
        len(guarded_calls) > MAX_PLATFORM_TOOL_CALLS
        or len(provider_calls) > MAX_PLATFORM_TOOL_CALLS
        or bool(local_budget_reason)
    )
    budget_reason = local_budget_reason
    if budget_exceeded and not budget_reason:
        budget_reason = (
            f"轻量查询最多执行 {MAX_PLATFORM_TOOL_CALLS} 次，当前问题规划了 "
            f"{max(len(guarded_calls), len(provider_calls))} 次查询。"
        )
    return limited, changed, budget_exceeded, budget_reason


async def detect_platform_query_tool_calls(message: str) -> dict[str, Any]:
    """快速 AI 规划 platform_query 调用。失败时用本地高置信规则兜底。"""
    cleaned = str(message or "").strip()
    if not cleaned:
        return {"related": False, "tool_calls": [], "needs_agent": False}
    views_text = "\n".join(f"- {key}: {desc}" for key, desc in VIEW_DESCRIPTIONS.items())
    system_prompt = (
        "你是教学平台聊天工具调用规划器。教师的问题如果能通过平台实时数据轻查询解决，"
        "请从白名单视图中规划 platform_query 工具调用，最多规划 2 个，按回答需要的顺序排列；"
        "超过 2 个、需要长时间分析、生成完整报告或跨多类数据综合推理时，"
        "needs_agent=true，并简短给 agent_reason。不要写 SQL，不要发散到白名单外。"
        f"\n可用视图：\n{views_text}\n"
    )
    common_payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": cleaned[:INTENT_MESSAGE_LIMIT],
        "base64_urls": [],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "task_priority": "interactive",
    }
    try:
        from ..core import ai_client

        tool_payload = {
            **common_payload,
            "tools": [platform_query_tool_schema()],
            "tool_choice": "auto",
            "task_label": "platform_query_tool_call_plan",
        }
        resp = await ai_client.post("/api/ai/chat", json=tool_payload, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — 规划 AI 故障 → 本地高置信兜底
        data = {}
    if isinstance(data, dict):
        tool_calls = []
        for raw in data.get("tool_calls") or []:
            if not isinstance(raw, dict) or raw.get("name") != "platform_query":
                continue
            args = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {}
            call = _sanitize_tool_call({"view": args.get("view"), "params": args.get("params") or {}})
            if call:
                _append_tool_call(tool_calls, call["view"], call["params"])
        if tool_calls:
            (
                guarded_tool_calls,
                guardrail_applied,
                budget_exceeded,
                budget_reason,
            ) = _merge_provider_calls_with_local_guardrails(
                cleaned, tool_calls
            )
            return {
                "related": True,
                "tool_calls": guarded_tool_calls,
                "needs_agent": budget_exceeded or bool(
                    re.search(r"(深入|分析|生成|方案|报告|总结|跨.{0,4}对比|长期|趋势)", cleaned)
                ),
                "agent_reason": budget_reason if budget_exceeded else "",
                "planner_source": "provider_tool_call",
                "guardrail_applied": guardrail_applied,
            }

    json_payload = {
        **common_payload,
        "system_prompt": (
            system_prompt
            + '只输出 JSON：{"related": true/false, "tool_calls": [{"view": "视图名", '
            '"params": {"参数名": "值"}}], "needs_agent": true/false, "agent_reason": "可空"}。'
        ),
        "response_format": "json",
        "task_label": "platform_query_tool_plan",
    }
    try:
        from ..core import ai_client

        resp = await ai_client.post("/api/ai/chat", json=json_payload, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — JSON 规划 AI 故障 → 本地高置信兜底
        return infer_platform_query_tool_calls(cleaned)
    parsed = data.get("response_json") if isinstance(data, dict) else None
    plan = _normalize_tool_call_plan(parsed)
    if plan is None:
        return infer_platform_query_tool_calls(cleaned)
    return plan


async def detect_platform_query_intent(message: str) -> dict[str, Any] | None:
    """兼容旧调用：返回规划结果中的第一个 platform_query。"""
    plan = await detect_platform_query_tool_calls(message)
    calls = plan.get("tool_calls") if isinstance(plan, dict) else None
    return calls[0] if calls else None


def _kw(params: dict[str, Any], key: str) -> str:
    return str(params.get(key) or "").strip()[:40]


def _view_my_classrooms(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT c.name AS 课程, cl.name AS 班级,
               (SELECT COUNT(*) FROM students st WHERE st.class_id = cl.id) AS 学生数
        FROM class_offerings co
        JOIN courses c ON c.id = co.course_id
        JOIN classes cl ON cl.id = co.class_id
        WHERE co.teacher_id = ?
        ORDER BY co.id DESC
        LIMIT 30
        """,
        (teacher_id,),
    ).fetchall()
    return {"title": "我的课堂", "rows": [dict(row) for row in rows]}


def _view_class_roster(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    keyword = _kw(params, "class_keyword")
    pattern = f"%{keyword.lower()}%"
    rows = conn.execute(
        """
        SELECT cl.name AS 班级, st.name AS 姓名, st.student_id_number AS 学号
        FROM class_offerings co
        JOIN classes cl ON cl.id = co.class_id
        JOIN courses c ON c.id = co.course_id
        JOIN students st ON st.class_id = cl.id
        WHERE co.teacher_id = ?
          AND (? = '' OR lower(cl.name) LIKE ? OR lower(c.name) LIKE ?)
        ORDER BY cl.name, st.name
        LIMIT ?
        """,
        (teacher_id, keyword.lower(), pattern, pattern, MAX_RESULT_ROWS),
    ).fetchall()
    return {"title": f"班级名册{('（' + keyword + '）') if keyword else ''}", "rows": [dict(row) for row in rows]}


def _view_assignment_submission_status(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    assignment_kw = _kw(params, "assignment_keyword")
    class_kw = _kw(params, "class_keyword")
    assignment = conn.execute(
        """
        SELECT a.id, a.title, a.class_offering_id, c.name AS course_name, cl.name AS class_name
        FROM assignments a
        JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings co ON co.id = a.class_offering_id
        LEFT JOIN classes cl ON cl.id = co.class_id
        WHERE (co.teacher_id = ? OR c.created_by_teacher_id = ?)
          AND (? = '' OR lower(a.title) LIKE ?)
          AND (? = '' OR lower(COALESCE(cl.name, '')) LIKE ? OR lower(c.name) LIKE ?)
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 1
        """,
        (
            teacher_id,
            teacher_id,
            assignment_kw.lower(),
            f"%{assignment_kw.lower()}%",
            class_kw.lower(),
            f"%{class_kw.lower()}%",
            f"%{class_kw.lower()}%",
        ),
    ).fetchone()
    if not assignment:
        return {"title": "作业提交情况", "rows": [], "note": "没有找到匹配的作业。"}
    submitted = conn.execute(
        "SELECT COUNT(*) FROM submissions WHERE assignment_id = ?",
        (assignment["id"],),
    ).fetchone()
    missing_rows = conn.execute(
        """
        SELECT st.name AS 未交学生
        FROM class_offerings co
        JOIN students st ON st.class_id = co.class_id
        WHERE co.id = ?
          AND st.id NOT IN (SELECT s.student_pk_id FROM submissions s WHERE s.assignment_id = ?)
        ORDER BY st.name
        LIMIT 80
        """,
        (assignment["class_offering_id"] or 0, assignment["id"]),
    ).fetchall()
    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM class_offerings co
        JOIN students st ON st.class_id = co.class_id
        WHERE co.id = ?
        """,
        (assignment["class_offering_id"] or 0,),
    ).fetchone()
    return {
        "title": f"作业「{assignment['title']}」提交情况（{assignment['course_name'] or ''} {assignment['class_name'] or ''}）",
        "rows": [dict(row) for row in missing_rows],
        "note": f"班级总人数 {int(total[0] or 0)}，已提交 {int(submitted[0] or 0)}，未交 {len(missing_rows)} 人。",
    }


def _view_low_scores(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    try:
        threshold = float(params.get("threshold") or 60)
    except (TypeError, ValueError):
        threshold = 60.0
    class_kw = _kw(params, "class_keyword")
    rows = conn.execute(
        """
        SELECT a.title AS 作业, s.student_name AS 学生, s.score AS 分数, cl.name AS 班级
        FROM submissions s
        JOIN assignments a ON CAST(a.id AS TEXT) = CAST(s.assignment_id AS TEXT)
        LEFT JOIN class_offerings co ON co.id = a.class_offering_id
        LEFT JOIN classes cl ON cl.id = co.class_id
        JOIN courses c ON c.id = a.course_id
        WHERE (co.teacher_id = ? OR c.created_by_teacher_id = ?)
          AND s.score IS NOT NULL AND s.score < ?
          AND (? = '' OR lower(COALESCE(cl.name, '')) LIKE ? OR lower(c.name) LIKE ?)
        ORDER BY a.created_at DESC, s.score ASC
        LIMIT ?
        """,
        (
            teacher_id,
            teacher_id,
            threshold,
            class_kw.lower(),
            f"%{class_kw.lower()}%",
            f"%{class_kw.lower()}%",
            MAX_RESULT_ROWS,
        ),
    ).fetchall()
    return {"title": f"低于 {threshold:g} 分的提交", "rows": [dict(row) for row in rows]}


def _view_my_schedule(conn, teacher_id: int, params: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now()
    start_at = now.isoformat(timespec="seconds")
    end_at = (now + timedelta(days=7)).isoformat(timespec="seconds")
    start_date = now.date().isoformat()
    end_date = (now + timedelta(days=7)).date().isoformat()
    rows: list[dict[str, Any]] = []

    calendar_rows = conn.execute(
        """
        SELECT starts_at AS 时间, title AS 事项, location AS 地点, source_type AS 类型
        FROM teacher_calendar_events
        WHERE teacher_id = ?
          AND status = 'active' AND deleted_at IS NULL
          AND starts_at >= ? AND starts_at <= ?
        ORDER BY starts_at ASC
        LIMIT 40
        """,
        (teacher_id, start_at, end_at),
    ).fetchall()
    rows.extend(dict(row) for row in calendar_rows)

    session_rows = conn.execute(
        """
        SELECT s.session_date AS 时间,
               s.title AS session_title,
               s.order_index AS order_index,
               c.name AS course_name,
               cl.name AS class_name
        FROM class_offering_sessions s
        JOIN class_offerings co ON co.id = s.class_offering_id
        JOIN courses c ON c.id = co.course_id
        JOIN classes cl ON cl.id = co.class_id
        WHERE co.teacher_id = ?
          AND s.session_date >= ?
          AND s.session_date <= ?
        ORDER BY s.session_date ASC, s.order_index ASC, s.id ASC
        LIMIT 40
        """,
        (teacher_id, start_date, end_date),
    ).fetchall()
    for row in session_rows:
        order_label = f"第 {int(row['order_index'])} 次课" if row["order_index"] not in (None, "") else "课堂"
        rows.append(
            {
                "时间": row["时间"],
                "事项": " ".join(
                    part
                    for part in (
                        row["course_name"],
                        row["class_name"],
                        order_label,
                        row["session_title"],
                    )
                    if part
                ),
                "地点": "",
                "类型": "class_session",
            }
        )

    rows.sort(key=lambda item: str(item.get("时间") or ""))
    return {"title": "未来 7 天日程", "rows": rows[:40]}


_VIEW_EXECUTORS = {
    "my_classrooms": _view_my_classrooms,
    "class_roster": _view_class_roster,
    "assignment_submission_status": _view_assignment_submission_status,
    "low_scores": _view_low_scores,
    "my_schedule": _view_my_schedule,
}


def run_platform_view(conn, *, teacher_id: int, view: str, params: dict[str, Any]) -> dict[str, Any]:
    executor = _VIEW_EXECUTORS.get(str(view or ""))
    if not executor:
        raise ValueError(f"未知视图：{view}")
    return executor(conn, int(teacher_id), params or {})


def build_query_result_block(view: str, result: dict[str, Any]) -> str:
    """查询结果 → 注入 system_prompt 的事实块（平台生成，绝对准确）。"""
    lines = [
        "--- 平台实时数据（刚刚以教师身份查询，数字准确，回答必须以此为准） ---",
        f"查询：{result.get('title') or view}",
    ]
    note = str(result.get("note") or "")
    if note:
        lines.append(note)
    rows = result.get("rows") or []
    if rows:
        columns = list(rows[0].keys())
        lines.append(" | ".join(str(col) for col in columns))
        for row in rows[:MAX_RESULT_ROWS]:
            lines.append(" | ".join(str(row.get(col, "")) for col in columns))
        if len(rows) >= MAX_RESULT_ROWS:
            lines.append(f"（已截断到 {MAX_RESULT_ROWS} 行）")
    elif not note:
        lines.append("（查询结果为空）")
    lines.append(
        "回答时直接引用上面的数字和名单；如果数据没有覆盖问题，明确说明并建议教师"
        "把问题交给 Agent 任务深入处理。"
    )
    block = "\n".join(lines)
    return block[:RESULT_BLOCK_LIMIT]


def build_agent_handoff_instruction(message: str, reason: str = "") -> str:
    reason_text = str(reason or "").strip()
    lines = [
        "--- Agent 任务转化建议 ---",
        "本轮问题可能需要超过聊天轻查询的 2 次工具调用，或需要更深入的跨数据分析。",
        "回答末尾请自然附上一句：这个问题适合交给 Agent 任务深入处理，可以点击下方「转为 Agent 任务」继续。",
        f"建议带入 Agent 的任务：{str(message or '').strip()[:500]}",
    ]
    if reason_text:
        lines.append(f"原因：{reason_text[:120]}")
    return "\n".join(lines)
