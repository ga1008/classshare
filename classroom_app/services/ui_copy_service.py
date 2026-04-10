from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime
from typing import Any

from ..config import UI_COPY_GENERATION_ENABLED, UI_COPY_REFRESH_POLL_SECONDS
from ..core import ai_client
from ..database import get_db_connection

UI_COPY_SCHEMA_VERSION = "v1"
UI_COPY_AI_TIMEOUT_SECONDS = 90.0
UI_COPY_MAX_TEXT_LENGTH = 120

DEFAULT_UI_COPY_SNAPSHOT: dict[str, dict[str, dict[str, str]]] = {
    "dashboard": {
        "teacher": {
            "hero_eyebrow": "Teaching Command Center",
            "hero_title": "教学控制台",
            "hero_subtitle": "{{name}}，把课堂推进、学生反馈和系统提醒放在同一个面板里，处理事情更顺手。",
            "spotlight_pending_label": "待办焦点",
            "spotlight_pending_note": "先把学生提交处理掉，课堂节奏会顺很多。",
            "spotlight_reset_label": "系统审核",
            "spotlight_reset_note": "有学生在等你审核找回密码申请。",
            "spotlight_unread_label": "未读提醒",
            "spotlight_unread_note": "消息中心有新互动，记得回看。",
            "spotlight_login_label": "今日登录",
            "spotlight_login_note": "今天的学生登录情况会汇总在这里。",
            "quick_actions_title": "快捷入口",
            "quick_actions_subtitle": "常用管理动作放近一点，备课和推进都更省心。",
            "focus_title": "优先处理",
            "focus_subtitle": "先处理最影响课堂节奏的事，后面的推进会轻不少。",
            "focus_empty_title": "当前节奏稳定",
            "focus_empty_description": "眼下没有紧急待办，适合补资料、磨试卷，或者顺手看看课堂反馈。",
            "activity_title": "近期动态",
            "activity_subtitle": "最近的课堂互动和提醒，随时在这儿接上。",
            "action_offering_label": "开设课堂",
            "action_offering_description": "关联班级和课程，快速搭好新的教学空间。",
            "action_materials_label": "课程材料",
            "action_materials_description": "把课程文档整理好，后面分发和复用都更轻松。",
            "action_exams_label": "考试题库",
            "action_exams_description": "维护试卷，发布考试，少走来回路。",
            "action_system_label": "系统审核",
            "action_system_description": "查看找回密码申请和安全审计记录。",
            "empty_title": "先开出第一间课堂",
            "empty_description": "现在还没有开设中的课堂，先把班级和课程关联起来，后面布置任务会顺很多。",
            "empty_action_label": "开设新课堂",
        },
        "student": {
            "hero_eyebrow": "Learning Overview",
            "hero_title": "学习总览",
            "hero_subtitle": "{{name}}，课程、待办和提醒都在这里，今天先做什么一眼就能找到。",
            "spotlight_pending_label": "待完成任务",
            "spotlight_pending_note": "先从最近要交的作业或考试下手，状态会轻松很多。",
            "spotlight_unread_label": "未读提醒",
            "spotlight_unread_note": "消息中心有新反馈，别让重要提醒悄悄溜走。",
            "spotlight_login_label": "累计登录",
            "spotlight_login_note": "常来看看，进度、提醒和安全记录都会接得更稳。",
            "quick_actions_title": "快捷入口",
            "quick_actions_subtitle": "常用入口放在手边，少点几下，省点脑力。",
            "focus_title": "学习提醒",
            "focus_subtitle": "先把最要紧的几件事解决，后面会轻松很多。",
            "activity_title": "近期动态",
            "activity_subtitle": "最新的课堂提醒在这儿，路过顺手看一眼就好。",
            "priority_unread_title": "消息中心有新提醒",
            "priority_unread_description": "先去看看，可能有老师反馈、批改结果或同学互动。",
            "priority_empty_title": "当前节奏不错",
            "priority_empty_description": "眼下没有急事，去课堂里看看资料、讨论或复盘一下也很值。",
            "action_priority_label": "优先任务",
            "action_priority_description": "直接跳到眼下最该先处理的内容。",
            "action_message_label": "消息中心",
            "action_message_description": "私信、提醒和批改反馈都在这里。",
            "action_security_label": "修改密码",
            "action_security_description": "顺手把账号安全也照顾好。",
            "empty_title": "这会儿还没有可进入的课堂",
            "empty_description": "等老师为你的班级开课后，入口就会出现在这里。",
            "empty_action_label": "先去消息中心",
        },
    },
    "classroom": {
        "teacher": {
            "hero_eyebrow": "Teaching Studio",
            "hero_lead": "{{name}}，这间课堂的任务、材料、资源和讨论都收在这里，推进节奏会更顺。",
            "assignment_title": "作业与考试",
            "assignment_subtitle": "从这里发布、调整并跟进本课堂的作业和考试安排。",
            "assignment_empty_title": "这门课还没有发布任务",
            "assignment_empty_description": "可以先新建作业，或者从试卷库挑一份考试发到课堂里。",
            "materials_title": "课程材料",
            "materials_subtitle": "给学生看的课程文档都在这里，整理好后复用和分发都会更省心。",
            "resources_title": "软件分享与课堂资源",
            "resources_subtitle": "课件、工具和示例资料统一放这里，发给学生更直接。",
            "discussion_title": "即时讨论",
            "discussion_subtitle": "课堂提问、提醒和反馈都能在这儿快速对上节奏。",
            "discussion_detail_template": "{{name}}，一条提醒、一个追问，都可能把课堂互动带起来。",
            "spotlight_draft_label": "待发布任务",
            "spotlight_draft_note": "还有任务停在草稿里，补完就能发给学生。",
            "spotlight_active_label": "已运行任务",
            "spotlight_active_note": "课堂任务已经跑起来了，现在适合继续补资料和打磨体验。",
        },
        "student": {
            "hero_eyebrow": "Learning Space",
            "hero_lead": "{{name}}，欢迎回来。任务、材料、资源和讨论都在这间课堂里，学到哪就从哪继续。",
            "assignment_title": "我的作业与考试",
            "assignment_subtitle": "作业、考试和提交入口都在这儿，先看要求，再稳稳交上去。",
            "assignment_empty_title": "这门课暂时还没有新任务",
            "assignment_empty_description": "老师一发布作业或考试，这里就会第一时间出现入口。",
            "materials_title": "课程材料",
            "materials_subtitle": "老师分配的课程文档都在这里，查资料、读 README、下载复习都方便。",
            "resources_title": "软件分享与课堂资源",
            "resources_subtitle": "课件、工具和实验资料放在一起，需要什么就直接拿。",
            "discussion_title": "即时讨论",
            "discussion_subtitle": "卡住了就问，想到就聊，作业难点和实验进度都能在这儿接上。",
            "discussion_detail_template": "{{alias_or_name}}，先发一句也行，讨论往往就是这样热起来的。",
            "spotlight_pending_label": "待完成任务",
            "spotlight_pending_note": "先收掉还没提交的任务，后面的学习节奏会轻松很多。",
            "spotlight_submitted_label": "已提交任务",
            "spotlight_submitted_note": "你已经有内容在提交流程里了，记得回来看老师的批改和反馈。",
            "spotlight_empty_label": "当前任务",
            "spotlight_empty_note": "老师一发新任务，这里就会第一时间提醒你。",
        },
    },
}

_scheduler_task: asyncio.Task | None = None
_scheduler_stop_event: asyncio.Event | None = None


def get_ui_copy_block(
    conn,
    *,
    scene: str,
    role: str,
) -> dict[str, Any]:
    normalized_scene = str(scene or "").strip().lower()
    normalized_role = "teacher" if str(role or "").strip().lower() == "teacher" else "student"

    default_scene = DEFAULT_UI_COPY_SNAPSHOT.get(normalized_scene, {})
    default_block = default_scene.get(normalized_role) or {}
    if not default_block:
        return {}

    override_snapshot = _load_latest_snapshot_payload(conn)
    override_block = {}
    if override_snapshot:
        override_block = (
            override_snapshot.get(normalized_scene, {}).get(normalized_role)
            if isinstance(override_snapshot.get(normalized_scene), dict)
            else {}
        ) or {}

    return _deep_merge(copy.deepcopy(default_block), override_block)


def render_ui_copy_block(block: dict[str, Any], tokens: dict[str, Any] | None = None) -> dict[str, Any]:
    return _render_copy_tokens(copy.deepcopy(block), tokens or {})


async def ensure_ui_copy_snapshot(*, reason: str = "startup", force: bool = False) -> str:
    today_text = _today_text()

    with get_db_connection() as conn:
        existing_row = _load_snapshot_row(conn, snapshot_date=today_text)
        existing_source = str(existing_row["source"] or "") if existing_row else ""
        if existing_row and existing_source == "ai" and not force:
            return existing_source

    payload = copy.deepcopy(DEFAULT_UI_COPY_SNAPSHOT)
    source = "fallback"

    if UI_COPY_GENERATION_ENABLED:
        try:
            generated_snapshot = await _generate_snapshot_with_ai()
        except Exception as exc:
            print(f"[UI_COPY] AI 文案生成失败，将回退到默认文案: {exc}")
        else:
            payload = _deep_merge(copy.deepcopy(DEFAULT_UI_COPY_SNAPSHOT), generated_snapshot)
            source = "ai"

    with get_db_connection() as conn:
        current_row = _load_snapshot_row(conn, snapshot_date=today_text)
        current_source = str(current_row["source"] or "") if current_row else ""
        if current_row and current_source == "ai" and source != "ai":
            return current_source

        _upsert_snapshot(
            conn,
            snapshot_date=today_text,
            payload=payload,
            source=source,
            reason=reason,
        )
        conn.commit()

    print(f"[UI_COPY] 已刷新 {today_text} 的界面文案快照，source={source}, reason={reason}")
    return source


def start_ui_copy_refresh_scheduler() -> None:
    global _scheduler_task, _scheduler_stop_event
    if _scheduler_task and not _scheduler_task.done():
        return

    _scheduler_stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(_ui_copy_scheduler_loop(_scheduler_stop_event))
    print("[UI_COPY] 每日界面文案调度器已启动")


async def stop_ui_copy_refresh_scheduler() -> None:
    global _scheduler_task, _scheduler_stop_event

    if _scheduler_stop_event:
        _scheduler_stop_event.set()
    if _scheduler_task:
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass

    _scheduler_task = None
    _scheduler_stop_event = None
    print("[UI_COPY] 每日界面文案调度器已停止")


def _load_snapshot_row(conn, *, snapshot_date: str | None = None):
    if snapshot_date:
        return conn.execute(
            """
            SELECT *
            FROM ui_copy_snapshots
            WHERE snapshot_date = ?
            LIMIT 1
            """,
            (snapshot_date,),
        ).fetchone()

    return conn.execute(
        """
        SELECT *
        FROM ui_copy_snapshots
        ORDER BY snapshot_date DESC, id DESC
        LIMIT 1
        """
    ).fetchone()


def _load_latest_snapshot_payload(conn) -> dict[str, Any]:
    row = _load_snapshot_row(conn)
    if not row:
        return {}

    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}

    return payload if isinstance(payload, dict) else {}


def _upsert_snapshot(
    conn,
    *,
    snapshot_date: str,
    payload: dict[str, Any],
    source: str,
    reason: str,
) -> None:
    now_text = datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO ui_copy_snapshots (
            snapshot_date,
            schema_version,
            source,
            generation_reason,
            payload_json,
            generated_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date)
        DO UPDATE SET
            schema_version = excluded.schema_version,
            source = excluded.source,
            generation_reason = excluded.generation_reason,
            payload_json = excluded.payload_json,
            generated_at = excluded.generated_at,
            updated_at = excluded.updated_at
        """,
        (
            snapshot_date,
            UI_COPY_SCHEMA_VERSION,
            source,
            reason,
            json.dumps(payload, ensure_ascii=False),
            now_text,
            now_text,
        ),
    )


async def _generate_snapshot_with_ai() -> dict[str, Any]:
    response = await ai_client.post(
        "/api/ai/chat",
        json={
            "system_prompt": (
                "你是高校教学平台的中文界面文案设计师。"
                "你的任务是把生硬、像开发备注的提示语，改写成适合大学生阅读的产品文案。"
                "语气要自然、友好、有一点轻松感，但不能幼稚，不能像营销广告，也不能像系统后台提示。"
            ),
            "messages": [],
            "new_message": _build_generation_prompt(),
            "model_capability": "standard",
            "response_format": "json",
            "task_priority": "background",
            "task_label": "ui_copy_daily",
        },
        timeout=UI_COPY_AI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    data = response.json()
    if data.get("status") != "success":
        raise RuntimeError(f"AI 返回失败: {data}")

    response_json = data.get("response_json")
    if not isinstance(response_json, dict):
        raise RuntimeError("AI 未返回合法 JSON 对象")

    normalized = _sanitize_snapshot_payload(response_json, DEFAULT_UI_COPY_SNAPSHOT)
    if not normalized:
        raise RuntimeError("AI 返回的文案内容为空")

    return normalized


def _build_generation_prompt() -> str:
    skeleton = json.dumps(DEFAULT_UI_COPY_SNAPSHOT, ensure_ascii=False, indent=2)
    return (
        "请基于下面这份课堂平台文案骨架，生成一版新的 JSON 文案。\n"
        "严格要求：\n"
        "1. 只允许返回合法 JSON，对象结构和键名必须与骨架完全一致。\n"
        "2. 你只能改写字符串内容，不能增加或删除任何键。\n"
        "3. 文案面向大学生，语气自然、真诚、轻一点，但不要油腻、不要官话、不要开发备注口吻。\n"
        "4. 不要虚构新功能，不要出现“模块”“面板逻辑”“入口切换”等偏开发表达。\n"
        "5. 标题尽量短；说明文案尽量控制在 18-36 个汉字内；较长引导也不要超过 60 个汉字。\n"
        "6. 允许按自然语境使用占位符 {{name}}、{{class_name}}、{{course_name}}、{{alias_or_name}}，但不要滥用。\n"
        "7. 所有文案都必须适合直接展示在页面上。\n\n"
        "文案骨架如下：\n"
        f"{skeleton}"
    )


async def _ui_copy_scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=UI_COPY_REFRESH_POLL_SECONDS)
            continue
        except asyncio.TimeoutError:
            pass

        try:
            await ensure_ui_copy_snapshot(reason="scheduled")
        except Exception as exc:
            print(f"[UI_COPY] 定时刷新失败: {exc}")


def _sanitize_snapshot_payload(payload: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    normalized: dict[str, Any] = {}
    for key, schema_value in schema.items():
        candidate = payload.get(key)
        if isinstance(schema_value, dict):
            nested = _sanitize_snapshot_payload(candidate, schema_value)
            if nested:
                normalized[key] = nested
            continue

        text_value = _normalize_copy_text(candidate)
        if text_value:
            normalized[key] = text_value

    return normalized


def _normalize_copy_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""

    normalized = " ".join(value.split())
    if not normalized:
        return ""
    if len(normalized) > UI_COPY_MAX_TEXT_LENGTH:
        normalized = normalized[:UI_COPY_MAX_TEXT_LENGTH].rstrip()
    return normalized


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _render_copy_tokens(value: Any, tokens: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render_copy_tokens(item, tokens) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_copy_tokens(item, tokens) for item in value]
    if not isinstance(value, str):
        return value

    rendered = value
    for key, token_value in tokens.items():
        normalized = "" if token_value is None else str(token_value)
        rendered = rendered.replace(f"{{{{{key}}}}}", normalized)
    return rendered


def _today_text() -> str:
    return datetime.now().date().isoformat()
