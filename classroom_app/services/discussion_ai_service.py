from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, Optional

import httpx

from ..core import ai_client
from ..database import get_db_connection
from ..routers.ai import format_system_prompt
from .behavior_tracking_service import record_behavior_event
from .psych_profile_service import (
    compose_classroom_chat_system_prompt as build_classroom_chat_prompt,
    format_classroom_summary as build_classroom_summary,
    load_ai_class_config as fetch_ai_class_config,
    load_classroom_snapshot as fetch_classroom_snapshot,
    load_latest_hidden_profile as load_hidden_profile_snapshot,
    normalize_psych_profile_payload as normalize_profile_payload,
)

DISCUSSION_AI_ASSISTANT_NAME = "助教"
DISCUSSION_AI_USER_ID = "discussion_ai_assistant"
DISCUSSION_ACTIVITY_TRIGGER_THRESHOLD = 4
DISCUSSION_ACTIVITY_HISTORY_LIMIT = 24
DISCUSSION_CHAT_HISTORY_LIMIT = 100
DISCUSSION_REPLY_FALLBACK = "我在，先把刚刚这段讨论的球接住。你再把最想追问的点抛给我一句，我马上接着讲。"

_MENTION_PATTERN = re.compile(r"@助教")


def contains_discussion_ai_mention(text: str) -> bool:
    return bool(_MENTION_PATTERN.search(text or ""))


def strip_discussion_ai_mention(text: str) -> str:
    cleaned = _MENTION_PATTERN.sub("", text or "", count=1)
    cleaned = re.sub(r"^[\s,，。.!！？:：;；]+", "", cleaned)
    return cleaned.strip()


def _truncate_text(text: str, limit: int = 120) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 1, 0)].rstrip() + "…"


def _format_timestamp(value: Optional[str]) -> str:
    if not value:
        return ""

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return parsed.strftime("%H:%M")


def _safe_json_loads(raw_value: Optional[str]) -> Any:
    if not raw_value:
        return None
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return None


def _load_latest_discussion_hidden_profile(
    conn,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT id, round_index, profile_summary, mental_state_summary, support_strategy,
               hidden_premise_prompt, confidence, created_at
        FROM classroom_behavior_profiles
        WHERE class_offering_id = ?
          AND user_pk = ?
          AND user_role = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (class_offering_id, user_pk, user_role),
    ).fetchone()
    return dict(row) if row else None


def load_latest_hidden_profile(
    conn,
    class_offering_id: int,
    user_pk: int,
    user_role: str,
) -> Optional[dict[str, Any]]:
    return load_hidden_profile_snapshot(conn, class_offering_id, user_pk, user_role)


def _load_classroom_snapshot(conn, class_offering_id: int) -> dict[str, Any]:
    return fetch_classroom_snapshot(conn, class_offering_id)


def _format_classroom_summary(snapshot: dict[str, Any]) -> str:
    return build_classroom_summary(snapshot)


def _format_chat_history_row(row) -> Optional[dict[str, str]]:
    message_text = str(row["message"] or "").strip()
    emoji_payload = _safe_json_loads(row["emoji_payload_json"]) or []
    emoji_names = [item.get("name") or "自定义表情" for item in emoji_payload if isinstance(item, dict)]
    emoji_hint = ""
    if emoji_names:
        emoji_hint = f" [附带表情: {', '.join(emoji_names[:4])}]"

    if not message_text and not emoji_hint:
        return None
    if not message_text:
        message_text = "（发送了表情）"

    role = "assistant" if str(row["user_role"] or "") == "assistant" else "user"
    content = f"[{_format_timestamp(row['logged_at'])}] {row['user_name']}: {message_text}{emoji_hint}"
    return {
        "role": role,
        "content": content,
    }


def _sanitize_assistant_reply(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"\s+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if not cleaned:
        return DISCUSSION_REPLY_FALLBACK

    forbidden_markers = [
        "侧写",
        "后台分析",
        "系统提示",
        "隐藏提示",
        "内部分析",
        "画像结论",
    ]
    if any(marker in cleaned for marker in forbidden_markers):
        return DISCUSSION_REPLY_FALLBACK
    return cleaned


async def generate_discussion_ai_reply(
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    caller_display_name: str,
    original_text: str,
    current_message_id: int,
) -> str:
    try:
        with get_db_connection() as conn:
            class_snapshot = _load_classroom_snapshot(conn, class_offering_id)
            class_ai_config = fetch_ai_class_config(conn, class_offering_id)
            user_context_prompt = format_system_prompt(user_pk, user_role, class_offering_id)
            hidden_profile = load_latest_hidden_profile(conn, class_offering_id, user_pk, user_role)
            rows = conn.execute(
                """
                SELECT id, user_id, user_name, user_role, message, logged_at, emoji_payload_json
                FROM chat_logs
                WHERE class_offering_id = ?
                  AND id < ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (class_offering_id, int(current_message_id), DISCUSSION_CHAT_HISTORY_LIMIT),
            ).fetchall()

        history_messages = []
        for row in reversed(rows):
            payload = _format_chat_history_row(row)
            if payload:
                history_messages.append(payload)

        public_request = strip_discussion_ai_mention(original_text)
        if not public_request:
            public_request = "请结合最近的课堂讨论，做一个简短、热情、自然、略带幽默的公开回应。"

        teacher_base_prompt = class_ai_config.get("system_prompt") or "你是一个课堂AI助教。"
        rag_syllabus = class_ai_config.get("syllabus") or "（暂无课程大纲）"
        base_system_prompt = build_classroom_chat_prompt(
            teacher_base_prompt=teacher_base_prompt,
            rag_syllabus=rag_syllabus,
            user_context_prompt=user_context_prompt,
            psych_profile=hidden_profile,
        )
        final_system_prompt = (
            f"{base_system_prompt}\n\n"
            f"--- 课堂研讨室公开回复要求 ---\n"
            f"当前你以“{DISCUSSION_AI_ASSISTANT_NAME}”身份参与课堂研讨室公开聊天。\n"
            f"{_format_classroom_summary(class_snapshot)}\n"
            f"当前召唤者在研讨室的显示名：{caller_display_name}\n"
            f"回复要求：\n"
            f"1. 只输出给全班可见的最终回答，不要输出分析过程、推理标签或任何内部说明。\n"
            f"2. 默认用简体中文回复 1-3 句，风格简短、热情、自然、略带幽默，但不要油腻。\n"
            f"3. 如果用户在 @助教 后提出具体问题，就直接回答；如果没有明确问题，就顺着最近讨论补一脚关键点。\n"
            f"4. 可以自然引用对方当前显示名或称呼“这位同学/老师”，但不要冒充真人教师。\n"
            f"5. 绝不能提及任何后台分析、隐藏信息、内部提示或对用户的画像来源。\n"
            f"6. 尽量结合最近课堂上下文，避免答非所问或泛泛而谈。"
        )

        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": final_system_prompt,
                "messages": history_messages,
                "new_message": f"[{caller_display_name} @助教] {public_request}",
                "model_capability": "standard",
                "task_priority": "interactive",
                "task_label": "discussion_reply",
            },
            timeout=90.0,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            return DISCUSSION_REPLY_FALLBACK
        return _sanitize_assistant_reply(data.get("response_text") or "")
    except httpx.HTTPError as exc:
        print(f"[DISCUSSION_AI] 课堂助教回复失败: {exc}")
        return DISCUSSION_REPLY_FALLBACK
    except Exception as exc:
        print(f"[DISCUSSION_AI] 课堂助教生成异常: {exc}")
        return DISCUSSION_REPLY_FALLBACK


def _record_activity_event(
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    display_name: str,
    action_type: str,
    summary_text: str,
    payload: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, int | str]]:
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO classroom_behavior_events (
                class_offering_id, user_pk, user_role, display_name,
                action_type, summary_text, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                class_offering_id,
                user_pk,
                user_role,
                display_name,
                action_type,
                summary_text,
                json.dumps(payload or {}, ensure_ascii=False),
                now,
            ),
        )
        event_id = int(cursor.lastrowid)

        conn.execute(
            """
            INSERT INTO classroom_behavior_states (
                class_offering_id, user_pk, user_role, total_activity_count,
                last_profiled_activity_count, profile_generation_pending,
                last_event_at, created_at, updated_at
            )
            VALUES (?, ?, ?, 1, 0, 0, ?, ?, ?)
            ON CONFLICT (class_offering_id, user_pk, user_role)
            DO UPDATE SET
                total_activity_count = classroom_behavior_states.total_activity_count + 1,
                last_event_at = excluded.last_event_at,
                updated_at = excluded.updated_at
            """,
            (class_offering_id, user_pk, user_role, now, now, now),
        )

        state = conn.execute(
            """
            SELECT total_activity_count, last_profiled_activity_count, profile_generation_pending
            FROM classroom_behavior_states
            WHERE class_offering_id = ?
              AND user_pk = ?
              AND user_role = ?
            LIMIT 1
            """,
            (class_offering_id, user_pk, user_role),
        ).fetchone()

        trigger = None
        if state:
            total_activity_count = int(state["total_activity_count"] or 0)
            last_profiled_activity_count = int(state["last_profiled_activity_count"] or 0)
            profile_generation_pending = int(state["profile_generation_pending"] or 0)
            should_trigger = (
                total_activity_count - last_profiled_activity_count >= DISCUSSION_ACTIVITY_TRIGGER_THRESHOLD
                and profile_generation_pending == 0
            )
            if should_trigger:
                conn.execute(
                    """
                    UPDATE classroom_behavior_states
                    SET profile_generation_pending = 1,
                        updated_at = ?
                    WHERE class_offering_id = ?
                      AND user_pk = ?
                      AND user_role = ?
                    """,
                    (now, class_offering_id, user_pk, user_role),
                )
                trigger = {
                    "class_offering_id": class_offering_id,
                    "user_pk": user_pk,
                    "user_role": user_role,
                    "trigger_event_id": event_id,
                    "activity_count_snapshot": total_activity_count,
                    "round_index": max(1, total_activity_count // DISCUSSION_ACTIVITY_TRIGGER_THRESHOLD),
                }

        conn.commit()
        return trigger


def record_message_activity(
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    display_name: str,
    message_text: str,
    unicode_emojis: list[str] | None = None,
    custom_emoji_labels: list[str] | None = None,
    mentioned_assistant: bool = False,
) -> Optional[dict[str, int | str]]:
    normalized_text = str(message_text or "").strip()
    emoji_labels = [label for label in (custom_emoji_labels or []) if label]
    unicode_labels = [emoji for emoji in (unicode_emojis or []) if emoji]
    all_emoji_labels = emoji_labels + unicode_labels

    if normalized_text:
        summary = f"{display_name} 发言：“{_truncate_text(normalized_text)}”"
    else:
        summary = f"{display_name} 发送了纯表情消息"

    if all_emoji_labels:
        summary += f"，使用表情：{', '.join(all_emoji_labels[:6])}"
    if mentioned_assistant:
        summary += "，并主动 @助教"

    return record_behavior_event(
        class_offering_id=class_offering_id,
        user_pk=user_pk,
        user_role=user_role,
        display_name=display_name,
        action_type="message",
        summary_text=summary,
        payload={
            "message_text": normalized_text,
            "unicode_emojis": unicode_labels,
            "custom_emoji_labels": emoji_labels,
            "mentioned_assistant": bool(mentioned_assistant),
        },
        page_key="classroom_discussion",
    )


def record_alias_switch_activity(
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    display_name: str,
    success: bool,
    previous_name: Optional[str],
    new_name: Optional[str],
    reason: Optional[str],
) -> Optional[dict[str, int | str]]:
    if success:
        summary = f"{previous_name or display_name} 切换代号为 {new_name or display_name}"
    else:
        reason_map = {
            "cooldown": "冷却中",
            "limit_reached": "本次次数已用完",
            "no_alias_available": "暂无可用代号",
            "forbidden": "无权限切换",
        }
        summary = f"{display_name} 尝试切换代号，结果未成功（{reason_map.get(reason, '未完成')}）"

    return record_behavior_event(
        class_offering_id=class_offering_id,
        user_pk=user_pk,
        user_role=user_role,
        display_name=display_name,
        action_type="alias_switch",
        summary_text=summary,
        payload={
            "success": bool(success),
            "previous_name": previous_name,
            "new_name": new_name,
            "reason": reason,
        },
        page_key="classroom_discussion",
    )


def schedule_discussion_profile_refresh(trigger: Optional[dict[str, int | str]]) -> None:
    return None


def _build_recent_activity_transcript(rows: list[Any]) -> str:
    lines: list[str] = []
    for row in reversed(rows):
        timestamp_text = _format_timestamp(row["created_at"])
        lines.append(f"{timestamp_text} {row['summary_text']}".strip())
    return "\n".join(lines)


def _load_user_profile_seed(conn, user_pk: int, user_role: str) -> tuple[str, str]:
    if user_role == "teacher":
        row = conn.execute(
            "SELECT name, description FROM teachers WHERE id = ? LIMIT 1",
            (user_pk,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT s.name, s.description
            FROM students s
            WHERE s.id = ?
            LIMIT 1
            """,
            (user_pk,),
        ).fetchone()

    if not row:
        return "", ""
    return str(row["name"] or ""), str(row["description"] or "")


def _refresh_cached_ai_session_contexts(
    class_offering_id: int,
    user_pk: int,
    user_role: str,
) -> None:
    try:
        refreshed_context_prompt = format_system_prompt(user_pk, user_role, class_offering_id)
    except Exception as exc:
        print(f"[DISCUSSION_PROFILE] 刷新课堂 AI 会话缓存失败: {exc}")
        return

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE ai_chat_sessions
            SET context_prompt = ?
            WHERE class_offering_id = ?
              AND user_pk = ?
              AND user_role = ?
            """,
            (refreshed_context_prompt, class_offering_id, user_pk, user_role),
        )
        conn.commit()


def _finalize_profile_generation_state(
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    activity_count_snapshot: int,
    success: bool,
) -> Optional[dict[str, int | str]]:
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        if success:
            conn.execute(
                """
                UPDATE classroom_behavior_states
                SET last_profiled_activity_count = CASE
                        WHEN last_profiled_activity_count > ? THEN last_profiled_activity_count
                        ELSE ?
                    END,
                    last_profiled_at = ?,
                    profile_generation_pending = 0,
                    updated_at = ?
                WHERE class_offering_id = ?
                  AND user_pk = ?
                  AND user_role = ?
                """,
                (
                    activity_count_snapshot,
                    activity_count_snapshot,
                    now,
                    now,
                    class_offering_id,
                    user_pk,
                    user_role,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE classroom_behavior_states
                SET profile_generation_pending = 0,
                    updated_at = ?
                WHERE class_offering_id = ?
                  AND user_pk = ?
                  AND user_role = ?
                """,
                (now, class_offering_id, user_pk, user_role),
            )

        state = conn.execute(
            """
            SELECT total_activity_count, last_profiled_activity_count, profile_generation_pending
            FROM classroom_behavior_states
            WHERE class_offering_id = ?
              AND user_pk = ?
              AND user_role = ?
            LIMIT 1
            """,
            (class_offering_id, user_pk, user_role),
        ).fetchone()

        trigger = None
        if state:
            total_activity_count = int(state["total_activity_count"] or 0)
            last_profiled_activity_count = int(state["last_profiled_activity_count"] or 0)
            profile_generation_pending = int(state["profile_generation_pending"] or 0)
            should_trigger = (
                total_activity_count - last_profiled_activity_count >= DISCUSSION_ACTIVITY_TRIGGER_THRESHOLD
                and profile_generation_pending == 0
            )
            if should_trigger:
                latest_event = conn.execute(
                    """
                    SELECT id
                    FROM classroom_behavior_events
                    WHERE class_offering_id = ?
                      AND user_pk = ?
                      AND user_role = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (class_offering_id, user_pk, user_role),
                ).fetchone()
                if latest_event:
                    conn.execute(
                        """
                        UPDATE classroom_behavior_states
                        SET profile_generation_pending = 1,
                            updated_at = ?
                        WHERE class_offering_id = ?
                          AND user_pk = ?
                          AND user_role = ?
                        """,
                        (now, class_offering_id, user_pk, user_role),
                    )
                    trigger = {
                        "class_offering_id": class_offering_id,
                        "user_pk": user_pk,
                        "user_role": user_role,
                        "trigger_event_id": int(latest_event["id"]),
                        "activity_count_snapshot": total_activity_count,
                        "round_index": max(1, total_activity_count // DISCUSSION_ACTIVITY_TRIGGER_THRESHOLD),
                    }

        conn.commit()
        return trigger


async def refresh_discussion_profile_from_activity(
    class_offering_id: int,
    user_pk: int,
    user_role: str,
    trigger_event_id: int,
    activity_count_snapshot: int,
    round_index: int,
) -> None:
    success = False
    try:
        with get_db_connection() as conn:
            class_snapshot = _load_classroom_snapshot(conn, class_offering_id)
            class_ai_config = fetch_ai_class_config(conn, class_offering_id)
            latest_hidden_profile = load_latest_hidden_profile(conn, class_offering_id, user_pk, user_role)
            user_name, current_desc = _load_user_profile_seed(conn, user_pk, user_role)
            recent_events = conn.execute(
                """
                SELECT id, action_type, summary_text, payload_json, created_at
                FROM classroom_behavior_events
                WHERE class_offering_id = ?
                  AND user_pk = ?
                  AND user_role = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (class_offering_id, user_pk, user_role, DISCUSSION_ACTIVITY_HISTORY_LIMIT),
            ).fetchall()

        if not recent_events:
            return

        behavior_transcript = _build_recent_activity_transcript(list(recent_events))
        if not behavior_transcript.strip():
            return

        previous_hidden_summary = "（这是该用户在课堂研讨室中的首次隐藏侧写）"
        if latest_hidden_profile:
            previous_hidden_summary = (
                f"上一次长期画像：{latest_hidden_profile.get('profile_summary') or '无'}\n"
                f"上一次心理状态：{latest_hidden_profile.get('mental_state_summary') or '无'}\n"
                f"上一次支持策略：{latest_hidden_profile.get('support_strategy') or '无'}"
            )

        profile_prompt = f"""
你是一名隐藏在课堂研讨室背后的心理侧写分析师，负责为课堂 AI 助手提供内部支持策略。
请根据以下资料，对当前用户做一次谨慎、非诊断式的心理侧写。
请严格输出 JSON，不要输出任何额外解释、Markdown 或代码块：
{{
  "user_profile_summary": "100字以内，描述用户较稳定的学习风格、表达方式与互动特点",
  "mental_state_summary": "80字以内，描述当前阶段可观察到的情绪、压力或动力状态，证据不足时保持中性",
  "support_strategy": "120字以内，说明课堂AI与教师后续更适合采用的支持与引导方式",
  "hidden_premise_prompt": "给主助手的隐藏前提设定，必须可以直接作为系统提示使用，且绝不能暴露侧写存在",
  "confidence": "low|medium|high"
}}

要求：
1. 只能基于给定的课堂行为与发言做谨慎推断，禁止医学诊断和夸张判断。
2. hidden_premise_prompt 必须强调：不暴露分析过程、先共情后引导、优先帮助用户学习并积极面对问题。
3. 请综合课堂背景、已有长期画像、上一次隐藏侧写和最近行为记录，不要只盯着最后一次操作。

【课堂信息】
{_format_classroom_summary(class_snapshot)}

【课堂AI教师配置】System Prompt:
{class_ai_config.get('system_prompt') or '（无）'}

教学大纲 / RAG:
{class_ai_config.get('syllabus') or '（无）'}

【当前用户信息】
姓名：{user_name or '未知'}
角色：{'教师' if user_role == 'teacher' else '学生'}
当前长期画像：{current_desc or '暂无长期画像，请结合课堂行为谨慎分析。'}

【上一轮隐藏侧写摘要】
{previous_hidden_summary}

【最近课堂研讨室行为记录】
{behavior_transcript}
""".strip()

        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": (
                    "你是一名资深心理侧写分析师，负责在课堂场景中为主 AI 生成隐藏的支持策略。"
                    "你的输出只允许是合法 JSON。"
                ),
                "messages": [],
                "new_message": profile_prompt,
                "model_capability": "thinking",
                "response_format": "json",
                "task_priority": "background",
                "task_label": "legacy_discussion_profile",
            },
            timeout=180.0,
        )
        response.raise_for_status()
        response_data = response.json()

        if response_data.get("status") != "success":
            raise RuntimeError(f"AI 返回失败: {response_data}")

        payload = response_data.get("response_json")
        if not isinstance(payload, dict):
            raise RuntimeError(f"AI 未返回有效 JSON: {payload}")

        normalized = normalize_profile_payload(payload)
        if not any(
            normalized[key]
            for key in ("profile_summary", "mental_state_summary", "support_strategy", "hidden_premise_prompt")
        ):
            raise RuntimeError("AI 侧写结果为空")

        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO classroom_behavior_profiles (
                    class_offering_id, user_pk, user_role, trigger_event_id, round_index,
                    activity_count_snapshot, profile_summary, mental_state_summary,
                    support_strategy, hidden_premise_prompt, confidence, raw_payload, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    class_offering_id,
                    user_pk,
                    user_role,
                    trigger_event_id,
                    round_index,
                    activity_count_snapshot,
                    normalized["profile_summary"],
                    normalized["mental_state_summary"],
                    normalized["support_strategy"],
                    normalized["hidden_premise_prompt"],
                    normalized["confidence"],
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )

            if normalized["profile_summary"]:
                table_name = "teachers" if user_role == "teacher" else "students"
                conn.execute(
                    f"UPDATE {table_name} SET description = ? WHERE id = ?",
                    (normalized["profile_summary"], user_pk),
                )

            conn.commit()

        _refresh_cached_ai_session_contexts(class_offering_id, user_pk, user_role)
        success = True
        print(
            f"[DISCUSSION_PROFILE] 侧写更新完成: class={class_offering_id}, "
            f"user={user_role}:{user_pk}, round={round_index}, snapshot={activity_count_snapshot}"
        )
    except Exception as exc:
        print(f"[DISCUSSION_PROFILE] 侧写更新失败: {exc}")
    finally:
        followup_trigger = _finalize_profile_generation_state(
            class_offering_id=class_offering_id,
            user_pk=user_pk,
            user_role=user_role,
            activity_count_snapshot=activity_count_snapshot,
            success=success,
        )
        if followup_trigger:
            schedule_discussion_profile_refresh(followup_trigger)
