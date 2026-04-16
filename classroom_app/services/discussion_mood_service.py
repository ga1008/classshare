from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from ..core import ai_client
from ..database import get_db_connection

DISCUSSION_MOOD_SCHEMA_VERSION = "v1"
DISCUSSION_MOOD_HISTORY_LIMIT = 20
DISCUSSION_MOOD_TEACHER_HISTORY_LIMIT = 6
DISCUSSION_MOOD_HEADLINE_MAX_LENGTH = 36
DISCUSSION_MOOD_DETAIL_MAX_LENGTH = 36
DISCUSSION_MOOD_MIN_REFRESH_INTERVAL_SECONDS = 90
DISCUSSION_MOOD_STALE_SECONDS = 20 * 60
DISCUSSION_MOOD_TRIGGER_MESSAGE_GAP = 4
DISCUSSION_MOOD_AI_TIMEOUT_SECONDS = 45.0

DISCUSSION_MOOD_FALLBACKS: tuple[tuple[str, str, str], ...] = (
    ("soft", "风先把聊天窗吹热了。", "想到什么就丢进来吧，这里本来就该松松软软地说话。"),
    ("singing", "今天的课堂心情，有点想唱副歌。", "你一句我一句，气氛就会自己长出旋律。"),
    ("floating", "老师的情绪像云朵乱跑。", "高兴也好，发呆也好，这里都能接住一小截心事。"),
    ("playful", "先别急着端着，麦克风借你。", "问题、灵感、碎碎念，都可以先在这儿落个脚。"),
    ("warm", "有一点点燃，也有一点点软。", "聊开了就行，认真留给文档、练习和考试。"),
    ("night-breeze", "今天这间聊天室，像晚风开着窗。", "问一句、笑一句、偶尔哼两句，都算课堂活过来的声音。"),
)

FORBIDDEN_MOOD_MARKERS = (
    "系统",
    "AI",
    "提示词",
    "内部",
    "分析",
    "模块",
    "按钮",
    "点击",
)

_refresh_tasks: dict[int, asyncio.Task[None]] = {}
_refresh_lock = asyncio.Lock()


def get_discussion_mood_payload(conn, class_offering_id: int) -> dict[str, Any]:
    row = _load_snapshot_row(conn, class_offering_id)
    payload = _normalize_snapshot_row(row) if row else {}
    if payload:
        return payload
    return _build_fallback_payload(class_offering_id)


def _load_refresh_snapshot_inputs(
    class_offering_id: int,
    latest_message_id: int | None,
) -> tuple[Any, int]:
    with get_db_connection() as conn:
        snapshot_row = _load_snapshot_row(conn, class_offering_id)
        resolved_latest_message_id = int(
            latest_message_id
            if latest_message_id is not None
            else _load_latest_chat_message_id(conn, class_offering_id)
        )
    return snapshot_row, resolved_latest_message_id


async def maybe_schedule_discussion_mood_refresh(
    class_offering_id: int,
    *,
    reason: str,
    latest_message_id: int | None = None,
    force: bool = False,
) -> bool:
    normalized_room_id = int(class_offering_id)

    async with _refresh_lock:
        existing_task = _refresh_tasks.get(normalized_room_id)
        if existing_task and not existing_task.done():
            return False

        snapshot_row, resolved_latest_message_id = await asyncio.to_thread(
            _load_refresh_snapshot_inputs,
            normalized_room_id,
            latest_message_id,
        )

        if not _should_refresh_snapshot(
            snapshot_row,
            latest_message_id=resolved_latest_message_id,
            force=force,
        ):
            return False

        task = asyncio.create_task(
            _refresh_discussion_mood_task(
                normalized_room_id,
                latest_message_id=resolved_latest_message_id,
                reason=reason,
            )
        )
        _refresh_tasks[normalized_room_id] = task
        return True


async def stop_discussion_mood_refresh_tasks() -> None:
    async with _refresh_lock:
        tasks = list(_refresh_tasks.values())
        _refresh_tasks.clear()

    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _refresh_discussion_mood_task(
    class_offering_id: int,
    *,
    latest_message_id: int,
    reason: str,
) -> None:
    try:
        snapshot = await _generate_discussion_mood_snapshot(
            class_offering_id=class_offering_id,
            latest_message_id=latest_message_id,
        )
        await asyncio.to_thread(
            _store_discussion_mood_snapshot,
            class_offering_id,
            snapshot,
        )
        print(
            f"[DISCUSSION_MOOD] 已刷新课堂 {class_offering_id} 的情绪语录，"
            f"source={snapshot['source']}, reason={reason}, latest_message_id={latest_message_id}"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[DISCUSSION_MOOD] 刷新失败 (class={class_offering_id}): {exc}")
    finally:
        async with _refresh_lock:
            current_task = _refresh_tasks.get(class_offering_id)
            if current_task is asyncio.current_task():
                _refresh_tasks.pop(class_offering_id, None)


async def _generate_discussion_mood_snapshot(
    *,
    class_offering_id: int,
    latest_message_id: int,
) -> dict[str, Any]:
    room, rows = await asyncio.to_thread(
        _load_discussion_mood_generation_context,
        class_offering_id,
    )

    if room is None or not rows:
        return _build_fallback_payload(
            class_offering_id,
            latest_message_id=latest_message_id,
            source="fallback",
        )

    teacher_lines: list[str] = []
    transcript_lines: list[str] = []
    for row in reversed(rows):
        line = _format_chat_line(row)
        if not line:
            continue
        transcript_lines.append(line)
        if (
            str(row["user_role"] or "").strip().lower() == "teacher"
            and len(teacher_lines) < DISCUSSION_MOOD_TEACHER_HISTORY_LIMIT
        ):
            teacher_lines.append(line)

    if not transcript_lines:
        return _build_fallback_payload(
            class_offering_id,
            latest_message_id=latest_message_id,
            source="fallback",
        )

    request_prompt = _build_generation_prompt(
        room=room,
        teacher_lines=teacher_lines,
        transcript_lines=transcript_lines,
    )

    try:
        response = await ai_client.post(
            "/api/ai/chat",
            json={
                "system_prompt": (
                    "你是课堂即时聊天室顶部的气氛报幕员。"
                    "你只负责写两句短短的情绪语录，不负责讲知识点，也不负责操作引导。"
                    "在邓紫棋、周杰伦、孙燕姿、陈奕迅、五月天等90后熟知的歌手中挑两句歌词表达心情，可以是撒欢、感伤、热血、发呆、轻微中二，任何你觉得合适的情感。"
                ),
                "messages": [],
                "new_message": request_prompt,
                "model_capability": "standard",
                "response_format": "json",
                "task_priority": "background",
                "task_label": "discussion_mood",
                "web_search_enabled": False,
            },
            timeout=DISCUSSION_MOOD_AI_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError(f"AI 返回失败: {data}")
        response_json = data.get("response_json")
        if not isinstance(response_json, dict):
            raise RuntimeError("AI 未返回合法 JSON")
        return _sanitize_generated_payload(
            response_json,
            class_offering_id=class_offering_id,
            latest_message_id=latest_message_id,
        )
    except Exception as exc:
        print(f"[DISCUSSION_MOOD] AI 生成失败，改用兜底语录: {exc}")
        return _build_fallback_payload(
            class_offering_id,
            latest_message_id=latest_message_id,
            source="fallback",
        )


def _store_discussion_mood_snapshot(class_offering_id: int, payload: dict[str, Any]) -> None:
    with get_db_connection() as conn:
        _upsert_snapshot(
            conn,
            class_offering_id=class_offering_id,
            payload=payload,
        )
        conn.commit()


def _load_discussion_mood_generation_context(class_offering_id: int):
    with get_db_connection() as conn:
        room = conn.execute(
            """
            SELECT o.id,
                   c.name AS course_name,
                   cl.name AS class_name,
                   t.name AS teacher_name
            FROM class_offerings o
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            JOIN teachers t ON t.id = o.teacher_id
            WHERE o.id = ?
            LIMIT 1
            """,
            (class_offering_id,),
        ).fetchone()
        rows = conn.execute(
            """
            SELECT id, user_name, user_role, message, logged_at, emoji_payload_json, attachments_json
            FROM chat_logs
            WHERE class_offering_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (class_offering_id, DISCUSSION_MOOD_HISTORY_LIMIT),
        ).fetchall()
    return room, rows


def _build_generation_prompt(
    *,
    room,
    teacher_lines: list[str],
    transcript_lines: list[str],
) -> str:
    teacher_section = "\n".join(teacher_lines) if teacher_lines else "（老师最近还没在聊天室发言，可自由发挥）"
    transcript = "\n".join(transcript_lines[-DISCUSSION_MOOD_HISTORY_LIMIT:])
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        "请根据下面的课堂即时聊天室内容，写两句适合贴在聊天室顶部的情绪语录，并返回 JSON。\n"
        "严格要求：\n"
        '1. 只返回合法 JSON，格式固定为 {"mood_label":"...", "headline":"...", "detail":"..."}。\n'
        "2. headline 和 detail 都必须是单行短句，在邓紫棋、周杰伦、孙燕姿、陈奕迅、五月天等90后熟知的歌手中挑两句歌词表达心情，任何你觉得合适的情感。\n"
        "3. 重点参考老师最近发言的情绪，但不必太准确，可以夸张一点、戏剧化一点、可爱一点。\n"
        "4. 可以是撒欢、发呆、悲伤、痛苦、热血、松弛，主打情绪输出。\n"
        "5. 不要写成功能说明，不要写操作步骤，不要提 AI、系统、提示词、内部分析。\n"
        "6. 可以轻微改写歌词，不要太长就行。\n"
        "7. 每句尽量控制在 8-26 个汉字内。\n\n"
        f"当前时间：{now_text}\n"
        f"课程：{room['course_name']}\n"
        f"班级：{room['class_name']}\n"
        f"老师：{room['teacher_name']}\n\n"
        "【老师最近发言】\n"
        f"{teacher_section}\n\n"
        "【最近聊天室】\n"
        f"{transcript}"
    )


def _should_refresh_snapshot(
    snapshot_row,
    *,
    latest_message_id: int,
    force: bool,
) -> bool:
    if snapshot_row is None:
        return True

    updated_at = _parse_datetime(snapshot_row["updated_at"] or snapshot_row["created_at"])
    if updated_at is None:
        return True

    seconds_since_update = (datetime.now() - updated_at).total_seconds()
    snapshot_message_id = int(snapshot_row["latest_message_id"] or 0)
    message_gap = max(int(latest_message_id or 0) - snapshot_message_id, 0)

    if seconds_since_update >= DISCUSSION_MOOD_STALE_SECONDS:
        return True
    if message_gap >= DISCUSSION_MOOD_TRIGGER_MESSAGE_GAP and seconds_since_update >= DISCUSSION_MOOD_MIN_REFRESH_INTERVAL_SECONDS:
        return True
    if force and seconds_since_update >= DISCUSSION_MOOD_MIN_REFRESH_INTERVAL_SECONDS:
        return True
    return False


def _load_snapshot_row(conn, class_offering_id: int):
    return conn.execute(
        """
        SELECT *
        FROM discussion_mood_snapshots
        WHERE class_offering_id = ?
        LIMIT 1
        """,
        (class_offering_id,),
    ).fetchone()


def _load_latest_chat_message_id(conn, class_offering_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM chat_logs WHERE class_offering_id = ?",
        (class_offering_id,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _upsert_snapshot(
    conn,
    *,
    class_offering_id: int,
    payload: dict[str, Any],
) -> None:
    now_text = datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO discussion_mood_snapshots (
            class_offering_id,
            schema_version,
            source,
            mood_label,
            headline,
            detail,
            latest_message_id,
            raw_payload_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(class_offering_id)
        DO UPDATE SET
            schema_version = excluded.schema_version,
            source = excluded.source,
            mood_label = excluded.mood_label,
            headline = excluded.headline,
            detail = excluded.detail,
            latest_message_id = excluded.latest_message_id,
            raw_payload_json = excluded.raw_payload_json,
            updated_at = excluded.updated_at
        """,
        (
            class_offering_id,
            DISCUSSION_MOOD_SCHEMA_VERSION,
            str(payload.get("source") or "fallback"),
            str(payload.get("mood_label") or "warm"),
            str(payload.get("headline") or ""),
            str(payload.get("detail") or ""),
            int(payload.get("latest_message_id") or 0),
            json.dumps(payload, ensure_ascii=False),
            now_text,
            now_text,
        ),
    )


def _normalize_snapshot_row(row) -> dict[str, Any]:
    if row is None:
        return {}

    headline = _normalize_line(row["headline"], limit=DISCUSSION_MOOD_HEADLINE_MAX_LENGTH)
    detail = _normalize_line(row["detail"], limit=DISCUSSION_MOOD_DETAIL_MAX_LENGTH)
    if not headline or not detail:
        return {}

    updated_at = str(row["updated_at"] or row["created_at"] or "")
    latest_message_id = int(row["latest_message_id"] or 0)
    source = str(row["source"] or "fallback")
    mood_label = str(row["mood_label"] or "warm")
    return {
        "source": source,
        "mood_label": mood_label,
        "headline": headline,
        "detail": detail,
        "latest_message_id": latest_message_id,
        "updated_at": updated_at,
        "version": f"{source}:{latest_message_id}:{updated_at}",
    }


def _sanitize_generated_payload(
    payload: dict[str, Any],
    *,
    class_offering_id: int,
    latest_message_id: int,
) -> dict[str, Any]:
    headline = _normalize_line(payload.get("headline"), limit=DISCUSSION_MOOD_HEADLINE_MAX_LENGTH)
    detail = _normalize_line(payload.get("detail"), limit=DISCUSSION_MOOD_DETAIL_MAX_LENGTH)
    mood_label = _normalize_line(payload.get("mood_label"), limit=20) or "warm"

    if not headline or not detail:
        return _build_fallback_payload(
            class_offering_id,
            latest_message_id=latest_message_id,
            source="fallback",
        )
    if any(marker in headline or marker in detail for marker in FORBIDDEN_MOOD_MARKERS):
        return _build_fallback_payload(
            class_offering_id,
            latest_message_id=latest_message_id,
            source="fallback",
        )

    updated_at = datetime.now().isoformat()
    return {
        "source": "ai",
        "mood_label": mood_label,
        "headline": headline,
        "detail": detail,
        "latest_message_id": int(latest_message_id or 0),
        "updated_at": updated_at,
        "version": f"ai:{int(latest_message_id or 0)}:{updated_at}",
    }


def _build_fallback_payload(
    class_offering_id: int,
    *,
    latest_message_id: int = 0,
    source: str = "fallback",
) -> dict[str, Any]:
    fallback = DISCUSSION_MOOD_FALLBACKS[abs(int(class_offering_id or 0)) % len(DISCUSSION_MOOD_FALLBACKS)]
    mood_label, headline, detail = fallback
    updated_at = datetime.now().isoformat()
    return {
        "source": source,
        "mood_label": mood_label,
        "headline": headline,
        "detail": detail,
        "latest_message_id": int(latest_message_id or 0),
        "updated_at": updated_at,
        "version": f"{source}:{int(latest_message_id or 0)}:{updated_at}",
    }


def _format_chat_line(row) -> str:
    role = str(row["user_role"] or "").strip().lower()
    sender = str(row["user_name"] or "课堂成员").strip() or "课堂成员"
    if role == "teacher":
        role_label = "老师"
    elif role == "assistant":
        role_label = "助教"
    else:
        role_label = "同学"

    message_text = _normalize_line(row["message"], limit=84)
    emoji_payload = _safe_json_loads(row["emoji_payload_json"]) or []
    attachment_payload = _safe_json_loads(row["attachments_json"]) or []
    extra_hints: list[str] = []
    if emoji_payload:
        extra_hints.append(f"表情 {len(emoji_payload)} 个")
    if attachment_payload:
        extra_hints.append(f"图片 {len(attachment_payload)} 张")

    if not message_text and not extra_hints:
        return ""
    if not message_text:
        message_text = "发来了一条没有文字的小情绪"
    if extra_hints:
        message_text = f"{message_text}（{'，'.join(extra_hints)}）"

    timestamp = _format_short_time(row["logged_at"])
    return f"[{timestamp}] {role_label} {sender}: {message_text}"


def _format_short_time(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "--:--"
    return parsed.strftime("%H:%M")


def _parse_datetime(value: Any) -> datetime | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    candidates = [raw_value]
    if raw_value.endswith("Z"):
        candidates.append(raw_value[:-1] + "+00:00")

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    return None


def _safe_json_loads(raw_value: Any) -> Any:
    if not raw_value:
        return None
    try:
        return json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return None


def _normalize_line(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""

    normalized = " ".join(str(value).replace("\r", "\n").split())
    if not normalized:
        return ""
    if len(normalized) > limit:
        normalized = normalized[:limit].rstrip("，,、。；;：:!?！？ ")
    return normalized
