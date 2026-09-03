"""LessonDoc 课次 AI 生成执行.

**任务态就是 `course_doc_pack_lessons.gen_status`**(pending/queued/running/
ready/failed),不复用 `session_material_generation_tasks`——后者的
`class_offering_id` 外键要求任务必须挂在具体课堂上,而 lessondoc 任务属于
「课程 × 包」,与课堂无关。这样旧 HTML 包/Markdown 生成通道零改动、零风险,
本模块也无需污染旧表。前端轮询 `GET /api/lessondoc/packs/{id}` 取状态。

上下文包(设计文档 §7.4):编写规范 AI 摘要 → course.json 全量 → 本课次
(课程模板 title/content + 教师 hint + 重写时现有 deck)→ 前一课完整 deck
→ 教材(introduction + catalog 相关截取)。预算 ~30k 字符,超限逐级截断。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

from ...core import ai_client
from . import editor_service, pack_service, render, validate
from .validate import LessonDocValidationError

MODE_GENERATE = "generate"
MODE_REWRITE = "rewrite"

_GUIDE_PATH = Path(__file__).resolve().parents[3] / "docs" / "lessondoc-authoring-guide.md"
_GUIDE_BEGIN = "<!-- AI-SUMMARY-BEGIN -->"
_GUIDE_END = "<!-- AI-SUMMARY-END -->"
_guide_cache: str | None = None

# 上下文预算(字符)
_BUDGET_MANIFEST = 9000
_BUDGET_PREV_DECK = 10000
_BUDGET_EXISTING_DECK = 10000
_BUDGET_TEXTBOOK = 4000


def _now_iso() -> str:
    return datetime.now().isoformat()


def _safe_text(value: Any, max_length: int = 280) -> str:
    text = str(value or "").strip()
    return text[:max_length]


def load_guide_summary() -> str:
    """docs/lessondoc-authoring-guide.md 的 AI 摘要节(缓存;缺失时给最小兜底)."""
    global _guide_cache
    if _guide_cache is not None:
        return _guide_cache
    try:
        raw = _GUIDE_PATH.read_text(encoding="utf-8")
        start = raw.index(_GUIDE_BEGIN) + len(_GUIDE_BEGIN)
        end = raw.index(_GUIDE_END)
        _guide_cache = raw[start:end].strip()
    except (OSError, ValueError):
        _guide_cache = (
            "你在编写 LessonDoc 2.0 课次 deck JSON。只输出一个 JSON 对象。"
            '顶层 {"spec":"lessondoc/2.0","kind":"lesson","lesson":N,"slides":[...]},'
            "版式 title/section/content/two-col/center/grid/end,"
            "块 text/cards/timeline/table/callout/code/media/svg/diagram/quiz/tasklist/reveal/stepper。"
        )
    return _guide_cache


# ---------------------------------------------------------------- 任务创建

def create_lessondoc_task(
    conn,
    *,
    pack: dict[str, Any],
    lesson_no: int,
    mode: str = MODE_GENERATE,
    user_hint: str = "",
    class_offering_id: int = 0,
    session_id: int = 0,
) -> dict[str, Any]:
    """把课次置为 queued(即"任务已登记")。

    去重:该课次已是 queued/running 时直接返回 already_running,不重复派发。
    `class_offering_id`/`session_id` 仅作调用来源记录,当前不落库(任务态在
    pack lessons 上,与课堂无关)。
    """
    mode = mode if mode in {MODE_GENERATE, MODE_REWRITE} else MODE_GENERATE
    pack_service.reclaim_stale_lessons(conn, int(pack["id"]))
    # Stale-task reclamation may commit; take the short pack lock afterwards.
    try:
        pack = editor_service._lock_pack(conn, int(pack["id"]), int(pack["teacher_id"]))
        state = editor_service._lesson_state(conn, pack, int(lesson_no))
    except editor_service.EditorError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    if state["gen_status"] == "excluded":
        raise HTTPException(409, "该课次已被排除，请先恢复")
    lessons = {l["lesson_no"]: l for l in pack_service.list_pack_lessons(conn, int(pack["id"]))}
    state = lessons.get(int(lesson_no))
    if state is not None and state.get("gen_status") in {"queued", "running"}:
        return {
            "pack_id": int(pack["id"]),
            "lesson_no": int(lesson_no),
            "status": state["gen_status"],
            "already_running": True,
            "mode": mode,
        }
    pack_service.update_lesson_state(
        conn,
        pack_id=int(pack["id"]),
        lesson_no=int(lesson_no),
        gen_status="queued",
        user_hint=user_hint if user_hint else None,
    )
    return {
        "pack_id": int(pack["id"]),
        "lesson_no": int(lesson_no),
        "status": "queued",
        "already_running": False,
        "mode": mode,
    }


# ---------------------------------------------------------------- 上下文构建

def _find_lesson_entry_row(conn, pack: dict[str, Any], lesson_no: int):
    return pack_service.find_lesson_entry(conn, pack, lesson_no)


def _load_lesson_deck(conn, pack: dict[str, Any], lesson_no: int) -> dict[str, Any] | None:
    entry = _find_lesson_entry_row(conn, pack, lesson_no)
    if entry is None:
        return None
    text = pack_service._load_file_text(conn, entry)
    if not text:
        return None
    return render.extract_embedded_json(text)


def _load_textbook_context(conn, *, course_id: int, teacher_id: int, topics: list[str]) -> str:
    row = conn.execute(
        """
        SELECT tb.title, tb.publisher, tb.introduction, tb.catalog_text
        FROM textbooks tb
        JOIN class_offerings o ON o.textbook_id = tb.id
        WHERE o.course_id = ? AND o.teacher_id = ?
        ORDER BY o.id DESC
        LIMIT 1
        """,
        (int(course_id), int(teacher_id)),
    ).fetchone()
    if row is None:
        return ""
    parts = [f"教材:《{row['title']}》 {row['publisher'] or ''}".strip()]
    intro = _safe_text(row["introduction"], 800)
    if intro:
        parts.append(f"教材简介:{intro}")
    catalog = str(row["catalog_text"] or "")
    if catalog:
        # 按课次主题词截取目录相关行,失配则给开头
        lines = catalog.splitlines()
        hits: list[str] = []
        for topic in topics[:4]:
            key = _safe_text(topic, 24)
            if not key:
                continue
            for i, line in enumerate(lines):
                if key[:6] and key[:6] in line:
                    hits.extend(lines[max(0, i - 2): i + 6])
        excerpt = "\n".join(dict.fromkeys(hits)) if hits else "\n".join(lines[:60])
        parts.append("教材目录(相关节选):\n" + excerpt[:_BUDGET_TEXTBOOK])
    return "\n".join(parts)


def build_generation_context(
    conn,
    *,
    pack: dict[str, Any],
    lesson_no: int,
    mode: str,
    user_hint: str,
) -> tuple[str, str]:
    """返回 (system_prompt, user_message)."""
    manifest = pack_service.read_manifest(conn, pack)
    lessons = {int(l.get("n") or 0): l for l in manifest.get("lessons") or [] if isinstance(l, dict)}
    target = lessons.get(int(lesson_no), {})
    topics = [str(t) for t in target.get("topics") or []]

    system_prompt = load_guide_summary()

    sections: list[str] = []
    manifest_text = json.dumps(manifest, ensure_ascii=False)
    sections.append("【课程清单 course.json(全课程知识包)】\n" + manifest_text[:_BUDGET_MANIFEST])

    lesson_row = conn.execute(
        "SELECT title, content FROM course_lessons WHERE course_id = ? AND order_index = ? LIMIT 1",
        (int(pack["course_id"]), int(lesson_no)),
    ).fetchone()
    target_lines = [
        f"目标课次:第 {lesson_no} 课「{target.get('title') or (lesson_row and lesson_row['title']) or ''}」"
    ]
    if topics:
        target_lines.append("本课主题要点:" + " / ".join(topics[:8]))
    if lesson_row and _safe_text(lesson_row["content"], 10):
        target_lines.append("课程模板课次说明:" + _safe_text(lesson_row["content"], 1500))
    if user_hint:
        target_lines.append("教师对本课的生成提示(必须遵守):" + _safe_text(user_hint, 2000))
    sections.append("【目标课次】\n" + "\n".join(target_lines))

    if mode == MODE_REWRITE:
        existing = _load_lesson_deck(conn, pack, lesson_no)
        if existing:
            sections.append(
                "【现有课次 deck(重写基础:保留讲对了的内容,按教师提示改进)】\n"
                + json.dumps(existing, ensure_ascii=False)[:_BUDGET_EXISTING_DECK]
            )

    prev_no = 0
    for n in sorted(lessons):
        if n < int(lesson_no) and lessons[n].get("status") == "ready":
            prev_no = n
    if prev_no:
        prev_deck = _load_lesson_deck(conn, pack, prev_no)
        if prev_deck:
            sections.append(
                f"【前一课(第 {prev_no} 课)完整 deck,作为风格与衔接参照】\n"
                + json.dumps(prev_deck, ensure_ascii=False)[:_BUDGET_PREV_DECK]
            )

    textbook = _load_textbook_context(
        conn, course_id=int(pack["course_id"]), teacher_id=int(pack["teacher_id"]), topics=topics
    )
    if textbook:
        sections.append("【教材依据】\n" + textbook)

    sections.append(
        f"请编写第 {lesson_no} 课的完整 deck JSON。要求:与前后课次衔接自然、不重复已讲内容;"
        f"lesson 字段必须为 {lesson_no};badge 沿用课程惯例;不要在 JSON 里硬编码颜色。"
        "只输出 JSON 对象。"
    )
    return system_prompt, "\n\n".join(sections)


# ---------------------------------------------------------------- AI 调用

async def _call_lessondoc_ai(
    *,
    system_prompt: str,
    user_message: str,
    task_priority: str = "background",
    task_label: str = "lessondoc_generate",
    timeout: float = 600.0,
) -> dict[str, Any]:
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": user_message,
        "base64_urls": [],
        "file_texts": [],
        "model_capability": "thinking",
        "task_type": "deep_text_reasoning",
        "response_format": "json",
        "task_priority": task_priority,
        "task_label": task_label,
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        parsed = data.get("response_json")
        if not isinstance(parsed, dict):
            raise HTTPException(500, "AI 未返回有效的 JSON 结果。")
        return parsed
    except httpx.ConnectError:
        raise HTTPException(503, "AI 助教服务未运行,请稍后重试。")
    except httpx.TimeoutException:
        raise HTTPException(504, "AI 生成超时,请稍后重试。")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"AI 服务错误: {exc.response.text[:300]}")


# ---------------------------------------------------------------- 任务执行

def _claim_lesson(conn, *, pack_id: int, lesson_no: int) -> bool:
    """原子领取:queued → running。已被别的 worker 领走时返回 False."""
    now = _now_iso()
    cursor = conn.execute(
        """
        UPDATE course_doc_pack_lessons
        SET gen_status = 'running', updated_at = ?
        WHERE pack_id = ? AND lesson_no = ? AND gen_status = 'queued'
        """,
        (now, int(pack_id), int(lesson_no)),
    )
    claimed = int(getattr(cursor, "rowcount", 0) or 0) > 0
    conn.commit()
    return claimed


async def run_lessondoc_task(
    pack_id: int,
    lesson_no: int,
    *,
    mode: str = MODE_GENERATE,
    user_hint: str = "",
) -> None:
    """执行一个 lessondoc 课次生成(独立于旧 run_generation_task)."""
    from ...database import get_db_connection

    pack_id = int(pack_id)
    lesson_no = int(lesson_no)
    claim_stamp = None
    try:
        with get_db_connection() as conn:
            if lesson_no <= 0:
                raise HTTPException(400, "课次编号无效。")
            if not _claim_lesson(conn, pack_id=pack_id, lesson_no=lesson_no):
                return   # 已被领走或状态不是 queued
            pack = pack_service.get_pack(conn, pack_id)
            if not pack or pack.get("status") != "active":
                raise HTTPException(410, "学习文档包不存在或已归档。")
            claim_stamp = editor_service._lesson_state(conn, pack, lesson_no)["updated_at"]
            base_revision = editor_service.lesson_revision(conn, pack, lesson_no)
            if not user_hint:
                states = {l["lesson_no"]: l for l in pack_service.list_pack_lessons(conn, pack_id)}
                user_hint = str((states.get(lesson_no) or {}).get("user_hint") or "")
            system_prompt, user_message = build_generation_context(
                conn, pack=pack, lesson_no=lesson_no, mode=mode, user_hint=user_hint
            )

        ai_result = await _call_lessondoc_ai(system_prompt=system_prompt, user_message=user_message)

        with get_db_connection() as conn:
            pack = pack_service.get_pack(conn, pack_id)
            if not pack or pack.get("status") != "active":
                raise HTTPException(410, "学习文档包已被删除,生成结果无处落地。")
            try:
                editor_service.save_document(conn, pack_id=pack_id, teacher_id=int(pack["teacher_id"]), lesson_no=lesson_no,
                                                     document=ai_result, expected_revision=base_revision, operation_id="generate_" + uuid.uuid4().hex,
                                                     source="ai_generate", allow_loss=True, generation_claim=claim_stamp)
            except (LessonDocValidationError, editor_service.EditorError) as exc:
                if isinstance(exc, editor_service.EditorError) and exc.status == 409:
                    raise
                raise HTTPException(500, f"AI 输出不符合 LessonDoc 规范:{exc}")
            conn.commit()
    except Exception as exc:
        error_message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        try:
            from ...database import get_db_connection as _get_conn

            with _get_conn() as conn:
                if claim_stamp is not None:
                    conn.execute("UPDATE course_doc_pack_lessons SET gen_status='failed',warnings_json=?,updated_at=? WHERE pack_id=? AND lesson_no=? AND gen_status='running' AND updated_at=?",
                                 (json.dumps([_safe_text(error_message, 200)], ensure_ascii=False), _now_iso(), pack_id, lesson_no, claim_stamp))
                conn.commit()
        except Exception as inner_exc:  # pragma: no cover
            print(f"[LESSONDOC] failed to persist error state: {inner_exc}")
        print(f"[LESSONDOC] generation failed (pack {pack_id} lesson {lesson_no}): {error_message}")


def _lesson_status(conn, pack_id: int, lesson_no: int) -> str:
    row = conn.execute(
        "SELECT gen_status FROM course_doc_pack_lessons WHERE pack_id = ? AND lesson_no = ? LIMIT 1",
        (int(pack_id), int(lesson_no)),
    ).fetchone()
    return str(row["gen_status"]) if row else "pending"


async def run_lessondoc_batch(*, pack_id: int, lesson_nos: list[int], teacher_id: int) -> None:
    """顺序补齐多课次:逐课创建任务并等待完成(前课 summary 进入后课上下文)。

    韧性口径(R4):单课失败不阻断队列;失败课次**自动重试一次**(AI 偶发
    输出不合规是常态,一次重试能消化大半);重试仍失败则留 failed 状态,
    教师再点「补齐待生成课次」即断点续跑(候选=pending/failed,天然跳过
    已完成课次)。"""
    from ...database import get_db_connection

    for lesson_no in lesson_nos:
        for attempt in range(2):   # 首跑 + 失败自动重试一次
            try:
                with get_db_connection() as conn:
                    pack = pack_service.get_pack(conn, int(pack_id))
                    if not pack or pack.get("status") != "active" or int(pack["teacher_id"]) != int(teacher_id):
                        return
                    status = _lesson_status(conn, int(pack_id), int(lesson_no))
                    if status in {"ready", "excluded", "queued", "running"}:
                        break
                    task = create_lessondoc_task(conn, pack=pack, lesson_no=int(lesson_no))
                    conn.commit()
                if not task.get("already_running"):
                    await run_lessondoc_task(int(pack_id), int(lesson_no))
                with get_db_connection() as conn:
                    if _lesson_status(conn, int(pack_id), int(lesson_no)) != "failed":
                        break   # ready(或被排除等) → 下一课
                if attempt == 0:
                    print(f"[LESSONDOC] batch lesson_{lesson_no} failed, retrying once")
            except Exception as exc:  # pragma: no cover — 单课异常继续
                print(f"[LESSONDOC] batch item lesson_{lesson_no} failed: {exc}")
                await asyncio.sleep(0)


# ---------------------------------------------------------------- 单页重写(R2)

def _looks_like_slide(value: Any) -> bool:
    return isinstance(value, dict) and (
        "layout" in value or "blocks" in value or "left" in value or "areas" in value
    )


def _unwrap_slide_payload(raw: Any, *, slide_index: int) -> dict[str, Any] | None:
    """从 AI 返回中提取单个 Slide。AI 有随机性,常见形态都要接得住:

    1. 裸 Slide 对象(理想);
    2. ``{"slides": [...]}`` 整 deck——若长度>目标下标取对应页,单元素取 [0];
    3. 任意单键包装(``{"slide": {...}}`` / ``{"result": {...}}`` …)。
    识别不了返回 None,由调用方带纠错提示重试。
    """
    if _looks_like_slide(raw):
        return raw
    if not isinstance(raw, dict):
        return None
    slides = raw.get("slides")
    if isinstance(slides, list) and slides:
        candidate = None
        if len(slides) == 1:
            candidate = slides[0]
        elif 0 <= slide_index < len(slides):
            candidate = slides[slide_index]
        if _looks_like_slide(candidate):
            return candidate
    for value in raw.values():   # 单键/多键包装:取第一个像 Slide 的值
        if _looks_like_slide(value):
            return value
    return None

def build_slide_rewrite_context(
    conn,
    *,
    pack: dict[str, Any],
    lesson_no: int,
    slide_no: int,
    user_hint: str,
) -> tuple[str, str, dict[str, Any]]:
    """单页重写的提示词。返回 (system_prompt, user_message, 现有 deck)。

    与整课生成的差别:AI 只输出**一个 Slide 对象**;上下文给足当前页 + 前后
    相邻页(衔接)+ 课程要点,预算远小于整课(交互式场景要快)。
    """
    deck = _load_lesson_deck(conn, pack, lesson_no)
    if not deck or not isinstance(deck.get("slides"), list):
        raise HTTPException(404, "该课次还没有可编辑的学习文档")
    slides = deck["slides"]
    index = int(slide_no) - 1
    if index < 0 or index >= len(slides):
        raise HTTPException(404, f"第 {slide_no} 页不存在(本课共 {len(slides)} 页)")

    system_prompt = (
        load_guide_summary()
        + "\n\n本次任务是【单页重写】:只输出**一个 Slide 对象**的 JSON"
        "(即 slides 数组中的一个元素,含 layout 等字段),不要输出整个 deck、"
        "不要输出数组、不要输出 spec/lesson 等顶层字段。"
    )
    sections = [
        f"课程:{deck.get('course') or ''}|课次:第 {lesson_no} 课「{deck.get('title') or ''}」"
        f"|目标页:第 {slide_no} 页(共 {len(slides)} 页)",
        "【当前页(重写对象,保留讲对的内容,按要求改进)】\n"
        + json.dumps(slides[index], ensure_ascii=False),
    ]
    if index > 0:
        sections.append("【前一页(衔接参照,不要重复其内容)】\n"
                        + json.dumps(slides[index - 1], ensure_ascii=False)[:3000])
    if index + 1 < len(slides):
        sections.append("【后一页(衔接参照)】\n"
                        + json.dumps(slides[index + 1], ensure_ascii=False)[:3000])
    if user_hint.strip():
        sections.append("【教师改进要求(必须遵守)】\n" + _safe_text(user_hint, 2000))
    else:
        sections.append("【教师未给具体要求】请在保持知识点不变的前提下优化"
                        "表达与版式(多用图示/分步,精简文字)。")
    sections.append(f"输出第 {slide_no} 页重写后的单个 Slide JSON 对象。")
    return system_prompt, "\n\n".join(sections), deck


async def rewrite_slide_with_ai(
    *,
    pack_id: int,
    lesson_no: int,
    slide_no: int,
    user_hint: str = "",
) -> dict[str, Any]:
    """同步单页重写:AI 产单页 → 替换 → 全量校验 → 落盘。返回 {warnings, slide}。

    刻意不走任务队列:单页调用轻量(交互式优先级,240s 上限),教师期待
    改完立即看到;失败直接抛 HTTPException,不留任何状态残留。
    """
    from ...database import get_db_connection

    with get_db_connection() as conn:
        pack = pack_service.get_pack(conn, int(pack_id))
        if not pack or pack.get("status") != "active":
            raise HTTPException(404, "学习文档包不存在或已归档")
        base_revision = editor_service.lesson_revision(conn, pack, int(lesson_no))
        system_prompt, user_message, deck = build_slide_rewrite_context(
            conn, pack=pack, lesson_no=int(lesson_no), slide_no=int(slide_no),
            user_hint=user_hint,
        )

    new_slide: dict[str, Any] | None = None
    last_shape = ""
    for attempt in range(2):
        message = user_message
        if attempt:  # 第二次:附上纠错指令(AI 有随机性,偶发输出包装/整 deck)
            message += (
                f"\n\n【格式纠正】你上一次输出的顶层结构是 {last_shape},不符合要求。"
                "必须只输出单个 Slide 对象(顶层含 layout 字段),不要任何包装。"
            )
        raw = await _call_lessondoc_ai(
            system_prompt=system_prompt,
            user_message=message,
            task_priority="interactive",
            task_label="lessondoc_slide_rewrite",
            timeout=240.0,
        )
        new_slide = _unwrap_slide_payload(raw, slide_index=int(slide_no) - 1)
        if new_slide is not None:
            break
        last_shape = "/".join(sorted(raw.keys())[:6]) if isinstance(raw, dict) else type(raw).__name__
    if new_slide is None:
        raise HTTPException(
            500, f"AI 两次都未返回可用的单页内容(实际输出键:{last_shape}),请换个说法重试"
        )

    index = int(slide_no) - 1
    original_count = len(deck["slides"])
    if deck["slides"][index].get("id"):
        new_slide["id"] = deck["slides"][index]["id"]
    deck["slides"][index] = new_slide

    # 落盘前先在内存全量校验:若新页被降级丢弃(总页数变少),直接拒绝,
    # 原文件纹丝不动——绝不能悄悄少一页。
    try:
        clean_preview, _ = validate.validate_deck(deck, expected_lesson=int(lesson_no))
    except Exception as exc:
        raise HTTPException(500, f"重写结果不符合 LessonDoc 规范:{exc}(原页面未改动)")
    if len(clean_preview.get("slides") or []) != original_count:
        raise HTTPException(
            500, "重写后的页面未通过校验(整页被降级丢弃),原页面未改动,请换个说法重试"
        )

    with get_db_connection() as conn:
        pack = pack_service.get_pack(conn, int(pack_id))
        if not pack or pack.get("status") != "active":
            raise HTTPException(410, "学习文档包已被删除")
        try:
            saved = editor_service.save_document(conn, pack_id=int(pack_id), teacher_id=int(pack["teacher_id"]), lesson_no=int(lesson_no),
                                                 document=deck, expected_revision=base_revision, operation_id="rewrite_" + uuid.uuid4().hex,
                                                 source="ai_rewrite", allow_loss=True)
        except editor_service.EditorError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        conn.commit()
    return {"warnings": saved["warnings"], "slide_no": int(slide_no), "revision": saved["revision"]}
