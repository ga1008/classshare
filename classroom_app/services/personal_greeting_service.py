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

# 人设文风池：每天随机一个，让欢迎语"不经意地"变换性格。
PERSONAS: tuple[tuple[str, str], ...] = (
    ("幽默段子手", "轻松幽默，可以玩谐音梗或自嘲，但不油腻"),
    ("霸道总裁", "霸道总裁口吻，简短有力，带一点宠溺的命令感，如'今天的任务，本总裁替你排好了'"),
    ("温柔学姐", "温柔体贴的学姐口吻，观察细节，给具体的小鼓励"),
    ("武侠说书人", "武侠说书口吻，把学习比作修行与闯关，抑扬顿挫"),
    ("电影旁白", "电影预告片旁白感，庄重中带热血，仿佛主角即将登场"),
    ("毒舌教练", "毒舌但暖心的教练，先小小吐槽再给一句实在的打气"),
    ("诗意散文", "克制的诗意散文风，从今天的时节或天光切入，落到人身上"),
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
        if row["status"] == "ready" and row["greeting_text"]:
            return {"status": "ready", "text": row["greeting_text"], "persona": row["persona"]}
        return {"status": str(row["status"] or "pending")}

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
        dedupe_key=f"personal-greeting:{role}:{int(user_pk)}:{today}",
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
    weekday_label: str,
) -> str:
    role_label = "老师" if user_role == "teacher" else "大学生"
    lines = [
        f"你要为一名{role_label}写一句今天登录学习平台首页时看到的个性化欢迎语。",
        f"今天是{weekday_label}。",
        f"这个人叫 {display_name or context.get('name') or '同学'}。",
    ]
    if context.get("department"):
        lines.append(f"所在院系：{context['department']}。")
    if context.get("cultivation"):
        lines.append(f"学习平台上的成长状态：{context['cultivation']}。")
    if profiler_hint:
        lines.append(
            "以下是平台对这个人的内部观察（只许用来让语气和内容更贴心，"
            f"严禁在输出里出现'侧写/画像/分析/观察'等字眼或直接复述）：{profiler_hint}"
        )
    lines.extend([
        f"文风人设：{persona_name} —— {persona_style}。",
        "要求：只输出一句话（12-50个汉字），不带引号，不带前后缀说明，",
        "可以称呼名字，内容要具体、贴合这个人，不要空洞鸡汤，不要提平台名。",
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

    persona_name, persona_style = random.choice(PERSONAS)
    weekday_label = "周" + "一二三四五六日"[china_now().weekday()]
    prompt = _build_greeting_prompt(
        user_role=user_role,
        display_name=display_name,
        context=context,
        profiler_hint=profiler_hint,
        persona_name=persona_name,
        persona_style=persona_style,
        weekday_label=weekday_label,
    )

    greeting = ""
    try:
        response = await ai_gateway_post(
            ai_client,
            "/api/ai/chat",
            json_payload={
                "system_prompt": "你是文案高手，只输出最终那一句话，不要任何解释。",
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
        raw = str(data.get("response") or "")
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
                (greeting, persona_name, user_role, user_pk, greet_date),
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
