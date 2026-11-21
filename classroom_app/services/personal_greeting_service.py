# -*- coding: utf-8 -*-
"""个性化首页欢迎语：AI 每日一句，结合侧写与个人数据.

流程（"每日初次登录只生成一次"）：

1. 前端首页加载后请求 ``GET /api/learning/personal-greeting``；
2. 当天无记录 → 落一行 ``personal_greetings(status='pending')`` 并向统一
   调度器投递 ``personal_greeting_generate`` 任务（dedupe 按 人+日期），
   本次返回 pending，前端保持默认文案、稍后重试一次；
3. scheduler worker 里 handler 汇集轻量个人上下文（姓名/系部/修为、
   最近一次课堂侧写摘要、日期时段），随机抽一个"人设"文风调快速文本
   模型生成 12–60 字单句；输出经 ``sanitize_hidden_profile_leaks`` 净化
   （绝不把侧写术语漏给学生）后写回 ``status='ready'``；
4. 前端拿到 ready 文案，滚动动画替换默认欢迎语。

约束：登录/首页路径零 AI 调用；生成失败标 failed，首页永远有默认文案
兜底；每天一人最多一次 AI 调用（fast 模型、background 优先级）。
"""

from __future__ import annotations

import random
import re
from datetime import timedelta
from typing import Any

from ..database import get_db_connection
from ..db.connection import get_configured_db_engine

PERSONAL_GREETING_TASK_KIND = "personal_greeting_generate"
GREETING_MIN_CHARS = 8
GREETING_MAX_CHARS = 64
GREETING_CONTEXT_VERSION = "facts-v1:"

# 学生端每日文风池：温和、清醒、有画面感，不油腻、不强行卖萌。
PERSONAS: tuple[tuple[str, str], ...] = (
    ("晨光散记", "克制的散文感，从今天的时节、天色或节奏切入，轻落在学习与生活上"),
    ("同路学伴", "像并肩自习的朋友，自然、诚恳，给一句不浮夸的具体鼓励"),
    ("书院清话", "带一点淡淡的书卷气与松弛感，提醒稳住步子，不端架子"),
    ("干脆利落", "简短直接、带一点清醒的幽默，帮人迅速进入今天的节奏"),
    ("微风观察", "留意日常里的小细节，温和接住当下的心情，留一点余味"),
)

# 教师端每日文风池：专业、体面、有分寸的同行问候。
TEACHER_PERSONAS: tuple[tuple[str, str], ...] = (
    ("从容同行", "像体贴靠谱的同行问候，简短克制，顾及备课、授课或批改的节奏"),
    ("时节清言", "从今日时段或学期节点轻起一笔，祝今天课堂顺手、留点歇息空档"),
    ("朴素致意", "不客套、不拔高，平实温暖地开启一天的工作"),
)


def _normalise_greeting(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    cleaned = cleaned.strip('"“”\'')
    return cleaned


def is_valid_greeting(text: str) -> bool:
    cleaned = _normalise_greeting(text)
    return GREETING_MIN_CHARS <= len(cleaned) <= GREETING_MAX_CHARS


def get_or_request_personal_greeting(
    conn: Any,
    *,
    user_role: str,
    user_pk: int,
    display_name: str = "",
) -> dict[str, Any]:
    """查当天欢迎语；没有则登记 pending 并排队生成。"""
    from .academic_service import china_now, china_today
    from .life_tip_service import ensure_life_tip_runtime
    from .scheduled_task_service import schedule_task

    ensure_life_tip_runtime(conn)
    today = china_today().isoformat()
    role = "teacher" if user_role == "teacher" else "student"

    row = conn.execute(
        """
        SELECT status, greeting_text, persona
        FROM personal_greetings
        WHERE user_role = ? AND user_pk = ? AND greet_date = ?
        LIMIT 1
        """,
        (role, int(user_pk), today),
    ).fetchone()
    if row:
        current = role != "student" or str(row["persona"] or "").startswith(GREETING_CONTEXT_VERSION)
        if row["status"] == "ready" and row["greeting_text"] and current:
            from .psych_profile_service import sanitize_hidden_profile_leaks
            persona = str(row["persona"] or "")
            return {"status": "ready", "text": sanitize_hidden_profile_leaks(row["greeting_text"]),
                    "persona": persona.removeprefix(GREETING_CONTEXT_VERSION)}
        if current:
            return {"status": str(row["status"] or "pending")}
        # An old ready message may have been generated from private inputs.
        # Replace only this day's derived cache; preserve source records.
        conn.execute("UPDATE personal_greetings SET status='pending', greeting_text='', persona=? "
                     "WHERE user_role=? AND user_pk=? AND greet_date=?",
                     (GREETING_CONTEXT_VERSION, role, int(user_pk), today))

    sql = (
        "INSERT INTO personal_greetings (user_role, user_pk, greet_date, status) "
        "VALUES (?, ?, ?, 'pending')"
    )
    if get_configured_db_engine() == "postgres":
        sql += " ON CONFLICT (user_role, user_pk, greet_date) DO NOTHING"
    else:
        sql = sql.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
    conn.execute(sql, (role, int(user_pk), today))

    schedule_task(
        conn,
        task_kind=PERSONAL_GREETING_TASK_KIND,
        run_at=china_now().replace(tzinfo=None) + timedelta(seconds=5),
        payload={
            "user_role": role,
            "user_pk": int(user_pk),
            "greet_date": today,
            "display_name": str(display_name or "")[:40],
        },
        dedupe_key=f"personal-greeting:{role}:{int(user_pk)}:{today}:{GREETING_CONTEXT_VERSION}",
        recurrence_seconds=None,
        owner_role="system",
        title="个性化欢迎语生成",
        max_attempts=2,
        replace=False,
    )
    return {"status": "pending"}


def _load_student_context(conn: Any, user_pk: int) -> dict[str, str]:
    context: dict[str, str] = {}
    row = conn.execute(
        "SELECT name, department, college FROM students WHERE id = ?",
        (int(user_pk),),
    ).fetchone()
    if row:
        context["name"] = row["name"] or ""
        context["department"] = row["department"] or row["college"] or ""
    try:
        from .learning_progress_service import build_student_global_cultivation_profile

        profile = build_student_global_cultivation_profile(conn, int(user_pk)) or {}
        level = (profile.get("highest_level") or {}).get("level_name") or ""
        if level:
            context["cultivation"] = f"修为境界「{level}」，全局修为 {profile.get('score', 0)}/100"
            if profile.get("breakthrough_ready"):
                context["cultivation"] += "，已可挑战破境试炼"
    except Exception:
        pass
    return context


def _load_teacher_context(conn: Any, user_pk: int) -> dict[str, str]:
    row = conn.execute(
        "SELECT name, department, college FROM teachers WHERE id = ?",
        (int(user_pk),),
    ).fetchone()
    if not row:
        return {}
    return {
        "name": row["name"] or "",
        "department": row["department"] or row["college"] or "",
    }


def _load_profiler_hint(conn: Any, user_role: str, user_pk: int) -> str:
    """取最近一次课堂侧写的支持要点（仅用于喂给 AI，绝不外显）。"""
    if user_role == "student":
        return ""
    try:
        row = conn.execute(
            """
            SELECT profile_summary, support_strategy, interest_hypothesis, preferred_ai_style
            FROM classroom_behavior_profiles
            WHERE user_pk = ? AND user_role = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (int(user_pk), user_role),
        ).fetchone()
    except Exception:
        return ""
    if not row:
        return ""
    parts = [
        str(row["profile_summary"] or "")[:200],
        str(row["support_strategy"] or "")[:150],
        str(row["interest_hypothesis"] or "")[:100],
        str(row["preferred_ai_style"] or "")[:60],
    ]
    return "；".join(part for part in parts if part.strip())


def _build_greeting_prompt(
    *,
    user_role: str,
    display_name: str,
    context: dict[str, str],
    profiler_hint: str,
    persona_name: str,
    persona_style: str,
    time_context: str,
) -> str:
    from .prompt_utils import polite_address

    role_label = "高校教师" if user_role == "teacher" else "大学生"
    raw_name = str(display_name or context.get("name") or "").strip()
    address = polite_address(raw_name, "teacher" if user_role == "teacher" else "student")
    lines = [
        f"请为一位{role_label}写一句今天打开平台首页时看到的每日问候。",
        time_context,
        f"称呼对象：{address}。",
    ]
    if context.get("department"):
        lines.append(f"所在院系：{context['department']}。")
    if context.get("cultivation"):
        lines.append(f"近期学习进度：{context['cultivation']}。")
    if profiler_hint:
        lines.append(
            "以下是用于拿捏语气分寸的补充参考（严禁在输出中提及或复述来源）："
            f"{profiler_hint}"
        )
    lines.extend([
        f"今日语调：{persona_name} —— {persona_style}。",
        "要求：",
        "1. 只输出一句完整的中文短句（12–48 个汉字），不带引号、不带前后说明、不用表情符号。",
        "2. 贴合当前时段与对方身份；如需称呼，直接使用上面的礼貌称呼，不直呼全名。",
        "3. 有温度、有分寸，避免空洞鸡汤、口号感、网络油腻梗和“赋能/加油冲”等套话，不提平台名称。",
    ])
    return "\n".join(lines)


async def handle_personal_greeting_task(task: dict[str, Any]) -> str:
    from ..core import ai_client
    from .academic_service import china_now
    from .ai_gateway_service import ai_gateway_post
    from .psych_profile_service import sanitize_hidden_profile_leaks

    payload = task.get("payload") or {}
    user_role = "teacher" if payload.get("user_role") == "teacher" else "student"
    user_pk = int(payload.get("user_pk") or 0)
    greet_date = str(payload.get("greet_date") or "")
    display_name = str(payload.get("display_name") or "")
    if not user_pk or not greet_date:
        return "skipped: bad payload"

    with get_db_connection() as conn:
        context = (
            _load_teacher_context(conn, user_pk)
            if user_role == "teacher"
            else _load_student_context(conn, user_pk)
        )
        profiler_hint = _load_profiler_hint(conn, user_role, user_pk)
        conn.commit()

    from .prompt_utils import build_time_context_text

    persona_pool = TEACHER_PERSONAS if user_role == "teacher" else PERSONAS
    persona_name, persona_style = random.choice(persona_pool)
    time_context = build_time_context_text(china_now().replace(tzinfo=None))
    prompt = _build_greeting_prompt(
        user_role=user_role,
        display_name=display_name,
        context=context,
        profiler_hint=profiler_hint,
        persona_name=persona_name,
        persona_style=persona_style,
        time_context=time_context,
    )

    greeting = ""
    try:
        response = await ai_gateway_post(
            ai_client,
            "/api/ai/chat",
            json_payload={
                "system_prompt": (
                    "你负责为高校师生撰写简短、克制、有温度的每日一句问候。"
                    "只输出最终那一句话，不要解释、不要引号、不要鸡汤套话。"
                ),
                "messages": [],
                "new_message": prompt,
                "model_capability": "standard",
                "task_type": "fast_text_response",
                "task_priority": "background",
                "task_label": "personal_greeting",
                "web_search_enabled": False,
            },
            timeout=60.0,
            task_type="personal_greeting",
            priority="P1",
            student_id=user_pk if user_role == "student" else None,
            teacher_id=user_pk if user_role == "teacher" else None,
            source_ref=f"personal-greeting:{user_role}:{user_pk}:{greet_date}",
        )
        response.raise_for_status()
        data = response.json()
        # /api/ai/chat 文本模式的正文字段是 response_text（json 模式才是
        # response_json）；兼容取值防上游字段演化。
        raw = str(data.get("response_text") or data.get("response") or "")
        greeting = _normalise_greeting(sanitize_hidden_profile_leaks(raw))
    except Exception as exc:
        print(f"[GREETING] AI 生成失败 {user_role}:{user_pk}: {exc}")

    with get_db_connection() as conn:
        if greeting and is_valid_greeting(greeting):
            conn.execute(
                """
                UPDATE personal_greetings
                SET status = 'ready', greeting_text = ?, persona = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_role = ? AND user_pk = ? AND greet_date = ?
                """,
                (greeting, (GREETING_CONTEXT_VERSION + persona_name) if user_role == "student" else persona_name,
                 user_role, user_pk, greet_date),
            )
            result = f"ready ({persona_name}): {greeting[:24]}"
        else:
            conn.execute(
                """
                UPDATE personal_greetings
                SET status = 'failed', updated_at = CURRENT_TIMESTAMP
                WHERE user_role = ? AND user_pk = ? AND greet_date = ?
                """,
                (user_role, user_pk, greet_date),
            )
            result = "failed: invalid or empty greeting"
        conn.commit()
    return result
