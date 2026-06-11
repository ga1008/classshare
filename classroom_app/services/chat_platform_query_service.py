"""聊天 AI 实时查库（G9）—— 轻问题不动用 Agent 队列。

教师在聊天里问「三班几个人没交作业」这类轻量数据问题时：
1. 本地正则粗筛（高召回、零成本）；
2. 快速 AI 从**白名单视图**里选一个视图 + 参数（少而准，不让模型写 SQL）；
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

# 粗筛：数据型轻问题的高召回信号。
_DATA_QUESTION_PATTERN = re.compile(
    r"(没交|未交|交了|提交率|提交情况|多少(人|个)|几个(人|学生)|人数|名单|花名册"
    r"|成绩|分数|低分|平均分|及格|不及格"
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


def message_may_need_platform_data(message: str) -> bool:
    return bool(_DATA_QUESTION_PATTERN.search(str(message or "")))


async def detect_platform_query_intent(message: str) -> dict[str, Any] | None:
    """快速 AI 选视图 + 参数。失败/不相关返回 None（降级为普通对话）。"""
    cleaned = str(message or "").strip()
    if not cleaned:
        return None
    views_text = "\n".join(f"- {key}: {desc}" for key, desc in VIEW_DESCRIPTIONS.items())
    payload = {
        "system_prompt": (
            "你是教学平台数据查询意图识别器。教师的这句话如果是想查询平台里的实时数据"
            "（提交情况/名单/人数/成绩/日程/课堂列表），从下面的白名单视图中选择最匹配的一个并填参数。"
            f"可用视图：\n{views_text}\n"
            '只输出 JSON：{"related": true/false, "view": "视图名", "params": {"参数名": "值"}}。'
            "拿不准、或者问题需要分析/生成内容（不是查数）时输出 {\"related\": false}。"
        ),
        "messages": [],
        "new_message": cleaned[:INTENT_MESSAGE_LIMIT],
        "base64_urls": [],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "task_priority": "interactive",
        "task_label": "platform_query_intent",
    }
    try:
        from ..core import ai_client

        resp = await ai_client.post("/api/ai/chat", json=payload, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — 意图 AI 故障 → 降级普通对话
        return None
    parsed = data.get("response_json") if isinstance(data, dict) else None
    if not isinstance(parsed, dict) or not parsed.get("related"):
        return None
    view = str(parsed.get("view") or "").strip()
    if view not in VIEW_DESCRIPTIONS:
        return None
    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    return {"view": view, "params": params}


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
    rows = conn.execute(
        """
        SELECT starts_at AS 时间, title AS 事项, location AS 地点, source_type AS 类型
        FROM teacher_calendar_events
        WHERE teacher_id = ?
          AND status = 'active' AND deleted_at IS NULL
          AND starts_at >= ? AND starts_at <= ?
        ORDER BY starts_at ASC
        LIMIT 40
        """,
        (
            teacher_id,
            now.isoformat(timespec="seconds"),
            (now + timedelta(days=7)).isoformat(timespec="seconds"),
        ),
    ).fetchall()
    return {"title": "未来 7 天日程", "rows": [dict(row) for row in rows]}


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
