"""Generate a 《教师评学表》 from a class offering via the fast model.

Method two of the 评学表 feature: the teacher picks a taught class and the fast
model turns the class's whole-semester performance (作业/考试成绩归集、课堂互动归集、
修炼等级归集 + 课堂/教材上下文) into a fair 1–10 score for each of the 10 fixed 评价指标
plus a plain-text 学习情况分析与教学改革建议 (≤300 字). Runs as a background asyncio
task so the list page can show a placeholder card that polls progress; on AI failure
it falls back to a locally-derived score set so the closed loop never leaves the
teacher empty-handed.

The total is always coerced into the 60–95 band (per the official 评学 convention),
and 综合评价 (优秀/良好/一般/较差) is computed from the total by
:mod:`teacher_evaluation_service` — never picked by the AI.
"""

from __future__ import annotations

import json
import re
import traceback
from typing import Any

import httpx

from ..core import ai_client
from ..db.connection import get_db_connection
from . import teacher_evaluation_service as te

_AI_TIMEOUT = 150.0
_AI_RETRY_TIMEOUT = 90.0

# Target band for the whole-sheet total, per the official 评学 convention.
_MIN_TOTAL = 60
_MAX_TOTAL = 95


# ---------------------------------------------------------------------------
# Class performance aggregation (the data handed to the AI for a fair judgement)
# ---------------------------------------------------------------------------
def _one(conn: Any, sql: str, params: tuple) -> dict[str, Any]:
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _rows(conn: Any, sql: str, params: tuple) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


def build_class_performance_summary(conn: Any, class_offering_id: int) -> dict[str, Any]:
    """Aggregate every class-linked performance signal for a fair evaluation.

    Each sub-query is defensive (returns empty on any schema mismatch) so a partial
    summary still flows to the AI — the fields cover 作业/考试成绩、提交完成率、课堂互动、
    修炼等级分布 and the roster size.
    """
    oid = int(class_offering_id)
    summary: dict[str, Any] = {}
    lines: list[str] = []

    # Roster size.
    roster = _one(
        conn,
        """
        SELECT COUNT(*) AS n
        FROM students st
        JOIN class_offerings o ON o.class_id = st.class_id
        WHERE o.id = ? AND COALESCE(st.enrollment_status, 'active') = 'active'
        """,
        (oid,),
    )
    student_count = int(roster.get("n") or 0)
    summary["student_count"] = student_count
    if student_count:
        lines.append(f"班级在读人数：{student_count} 人")

    # Homework vs exam (assignments split by exam_paper_id) — averages + submission rate.
    assignment_stats = _rows(
        conn,
        """
        SELECT CASE WHEN a.exam_paper_id IS NOT NULL AND TRIM(a.exam_paper_id) != '' THEN 'exam' ELSE 'homework' END AS kind,
               COUNT(DISTINCT a.id) AS assignment_count,
               COUNT(s.id) AS submission_count,
               AVG(CASE WHEN s.score IS NOT NULL THEN s.score END) AS avg_score,
               SUM(CASE WHEN s.status = 'graded' OR s.score IS NOT NULL THEN 1 ELSE 0 END) AS graded_count
        FROM assignments a
        LEFT JOIN submissions s ON CAST(s.assignment_id AS TEXT) = CAST(a.id AS TEXT)
        WHERE a.class_offering_id = ?
        GROUP BY kind
        """,
        (oid,),
    )
    homework: dict[str, Any] = {}
    exam: dict[str, Any] = {}
    for row in assignment_stats:
        bucket = {
            "assignment_count": int(row.get("assignment_count") or 0),
            "submission_count": int(row.get("submission_count") or 0),
            "graded_count": int(row.get("graded_count") or 0),
            "avg_score": round(float(row.get("avg_score")), 1) if row.get("avg_score") is not None else None,
        }
        if row.get("kind") == "exam":
            exam = bucket
        else:
            homework = bucket
    summary["homework"] = homework
    summary["exam"] = exam
    if homework.get("assignment_count"):
        expected = max(1, homework["assignment_count"] * student_count)
        rate = round(100 * homework["submission_count"] / expected) if student_count else 0
        avg = homework.get("avg_score")
        lines.append(
            f"书面/编程作业：共 {homework['assignment_count']} 次，"
            f"提交完成率约 {rate}%，平均分 {avg if avg is not None else '暂无批改'}。"
        )
    if exam.get("assignment_count"):
        avg = exam.get("avg_score")
        lines.append(
            f"考试/测验：共 {exam['assignment_count']} 次，平均分 {avg if avg is not None else '暂无批改'}。"
        )

    # Classroom interaction (live activities + responses).
    interaction = _one(
        conn,
        """
        SELECT
          (SELECT COUNT(*) FROM classroom_live_activities WHERE class_offering_id = ?) AS activity_count,
          (SELECT COUNT(*) FROM classroom_live_responses r
             JOIN classroom_live_activities act ON act.id = r.activity_id
            WHERE act.class_offering_id = ?) AS response_count,
          (SELECT COUNT(*) FROM classroom_live_questions q
             JOIN classroom_live_activities act ON act.id = q.activity_id
            WHERE act.class_offering_id = ?) AS question_count
        """,
        (oid, oid, oid),
    )
    summary["interaction"] = {
        "activity_count": int(interaction.get("activity_count") or 0),
        "response_count": int(interaction.get("response_count") or 0),
        "question_count": int(interaction.get("question_count") or 0),
    }
    if summary["interaction"]["activity_count"] or summary["interaction"]["response_count"]:
        lines.append(
            f"课堂互动：发起活动 {summary['interaction']['activity_count']} 次，"
            f"学生应答 {summary['interaction']['response_count']} 人次，"
            f"课堂提问 {summary['interaction']['question_count']} 条。"
        )

    # Cultivation (修炼) level + progress distribution.
    cultivation = _rows(
        conn,
        """
        SELECT level_key, COUNT(*) AS n,
               AVG(score) AS avg_score, AVG(progress_percent) AS avg_progress
        FROM learning_progress_snapshots
        WHERE class_offering_id = ?
        GROUP BY level_key
        """,
        (oid,),
    )
    if cultivation:
        try:
            from .learning_progress_service import get_learning_level

            dist = []
            avg_progress_total = 0.0
            counted = 0
            for row in cultivation:
                level = get_learning_level(row.get("level_key")) or {}
                name = level.get("name") or level.get("short_name") or str(row.get("level_key") or "")
                count = int(row.get("n") or 0)
                dist.append({"level": name, "count": count})
                if row.get("avg_progress") is not None:
                    avg_progress_total += float(row.get("avg_progress")) * count
                    counted += count
            summary["cultivation"] = {"distribution": dist}
            if counted:
                avg_prog = round(avg_progress_total / counted)
                summary["cultivation"]["avg_progress"] = avg_prog
                spread = "、".join(f"{d['level']} {d['count']}人" for d in dist if d["count"])
                lines.append(f"修炼等级分布：{spread}；班级平均学习进度约 {avg_prog}%。")
        except Exception:
            pass

    summary["performance_summary"] = "\n".join(lines) if lines else "暂无可归集的量化表现数据，请结合日常观察评分。"
    return summary


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
def _indicator_lines() -> str:
    return "\n".join(
        f"{i + 1}. {text[text.find('.') + 1:].strip()}" for i, (_, text) in enumerate(te.EVALUATION_INDICATORS)
    )


def _system_prompt() -> str:
    return (
        "你是一名负责给学生班级填写《教师评学表》的任课教师。"
        "你要根据某个班级本学期在这门课程上的真实表现，为固定的 10 项评价指标各打一个 1 到 10 的整数分，"
        "并写一段对学生学习情况的分析和今后教学改革建议。"
        "必须严格返回 JSON 对象，不要 Markdown 代码块，也不要多余解释。"
        "JSON 必须包含两个键：scores 和 analysis。"
        "scores 是长度为 10 的整数数组，依次对应第 1 到第 10 项指标，每个分值为 1 到 10 的整数；"
        "10 项分值之和（总分）必须落在 60 到 95 之间，表现越好总分越高。"
        "analysis 是纯文本字符串，不超过 300 字，评价这个班级本学期在这门课程上的各项学习表现，"
        "要说人话、具体、客观、公平，可以分 1、2、3 点，但不要出现任何 Markdown 记号（如 # * - 等），"
        "不要出现“教学辅助系统”“AI”“系统”等字样，就当作是任课教师本人写的评语。"
    )


def _user_prompt(
    fields: dict[str, Any], performance: dict[str, Any], classroom_context: dict[str, Any], prompt: str
) -> str:
    identity = {k: fields.get(k) for k in ("course_name", "class_name", "college", "teacher_name", "academic_year", "semester")}
    structured = {k: v for k, v in performance.items() if k != "performance_summary"}
    return "\n\n".join(
        [
            "请根据以下某个教学班级本学期在这门课程上的真实表现，为《教师评学表》的 10 项指标打分并撰写评语。",
            "10 项评价指标（学习态度 1-2、学习过程 3-7、学习效果 8-10）依次是：\n" + _indicator_lines(),
            "评分要求：每项 1-10 的整数，越符合该指标描述分越高；10 项之和须在 60-95 之间；请结合下面的量化表现数据公平打分。",
            f"课程 / 班级基本信息：\n{json.dumps(identity, ensure_ascii=False, indent=2)}",
            f"班级本学期表现归集：\n{performance.get('performance_summary') or '暂无'}",
            f"结构化表现数据：\n{json.dumps(structured, ensure_ascii=False)}",
            f"课堂与教材上下文：\n{(classroom_context.get('classroom_summary') or '')[:1500]}",
            f"任课教师补充说明：\n{prompt.strip() or '无'}",
        ]
    )


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------
def _loads_ai_json(text: Any) -> dict[str, Any] | None:
    if isinstance(text, dict):
        return text
    if text in (None, ""):
        return None
    raw = str(text).strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, json.JSONDecodeError):
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\{]", raw):
        try:
            parsed, _ = decoder.raw_decode(raw[match.start():])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_from_payload(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    for key in ("response_json", "json", "data"):
        parsed = _loads_ai_json(data.get(key))
        if parsed:
            return parsed
    return _loads_ai_json(data.get("response_text"))


async def _chat_json(system_prompt: str, user_message: str) -> dict[str, Any] | None:
    payload = {
        "system_prompt": system_prompt,
        "messages": [],
        "new_message": user_message,
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": "teacher-evaluation:generate",
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=_AI_TIMEOUT)
        response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError):
        retry = {**payload, "task_label": "teacher-evaluation:generate:retry"}
        response = await ai_client.post("/api/ai/chat", json=retry, timeout=_AI_RETRY_TIMEOUT)
        response.raise_for_status()
    return _json_from_payload(response.json())


# ---------------------------------------------------------------------------
# Score coercion — force the 10 scores into 1..10 and the total into [60, 95]
# ---------------------------------------------------------------------------
def _coerce_scores(raw_scores: Any) -> list[int]:
    scores: list[int] = []
    for value in (raw_scores or [])[:10]:
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError):
            number = 8
        scores.append(max(1, min(te.MAX_INDICATOR_SCORE, number)))
    while len(scores) < 10:
        scores.append(8)
    return _fit_total_band(scores)


def _fit_total_band(scores: list[int]) -> list[int]:
    """Nudge whole-set scores until the total sits within [60, 95] (all 1..10)."""
    scores = [max(1, min(10, int(s))) for s in scores]
    guard = 0
    while sum(scores) < _MIN_TOTAL and guard < 200:
        candidates = [i for i, s in enumerate(scores) if s < 10]
        if not candidates:
            break
        target = min(candidates, key=lambda i: scores[i])
        scores[target] += 1
        guard += 1
    guard = 0
    while sum(scores) > _MAX_TOTAL and guard < 200:
        candidates = [i for i, s in enumerate(scores) if s > 1]
        if not candidates:
            break
        target = max(candidates, key=lambda i: scores[i])
        scores[target] -= 1
        guard += 1
    return scores


def _fallback_scores(performance: dict[str, Any]) -> list[int]:
    """Derive a defensible score set from the quantitative signals (no AI)."""
    base = 8
    homework_avg = (performance.get("homework") or {}).get("avg_score")
    exam_avg = (performance.get("exam") or {}).get("avg_score")
    grade_signal = next((v for v in (homework_avg, exam_avg) if v is not None), None)
    if grade_signal is not None:
        base = max(6, min(9, round(grade_signal / 100 * 10)))
    interaction = performance.get("interaction") or {}
    lively = 1 if (interaction.get("response_count") or 0) > 0 else 0
    # A gentle, plausible spread around the base with a small lift for lively classes.
    spread = [0, 0, 0, 0, lively, 0, -1, 1, 0, -1]
    scores = [base + delta for delta in spread]
    return _fit_total_band(scores)


def _fallback_analysis(fields: dict[str, Any], performance: dict[str, Any]) -> str:
    course = fields.get("course_name") or "本课程"
    parts = [
        f"本学期该班级在《{course}》课程的整体学习态度端正，课堂秩序良好，多数学生能按要求完成学习任务。",
        "1.学习过程方面，课前预习与课后复习基本落实，课堂参与和作业提交总体稳定，部分学生的自主学习和文献阅读习惯仍有提升空间。",
        "2.学习效果方面，学生对课程内容有一定兴趣，基本知识和基本技能掌握较好，但灵活运用知识解决实际问题的能力还需加强。",
        "3.今后教学中建议增加实操与案例研讨环节，加强对学习薄弱学生的个别辅导，进一步激发学习主动性。",
    ]
    return "".join(parts)[:300]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _set_status(evaluation_id: str, **kwargs: Any) -> None:
    with get_db_connection() as conn:
        te.set_generation_status(conn, evaluation_id, **kwargs)
        conn.commit()


def _classroom_context(conn: Any, class_offering_id: int) -> dict[str, Any]:
    try:
        from .academic_service import build_classroom_ai_context

        return build_classroom_ai_context(conn, int(class_offering_id)) or {}
    except Exception:
        return {}


def _merge_fields(offering_fields: dict[str, Any], ai_fields: dict[str, Any]) -> dict[str, Any]:
    merged = dict(ai_fields or {})
    for key, value in (offering_fields or {}).items():
        if str(value or "").strip():
            merged[key] = value
    return merged


def _clean_analysis(text: Any) -> str:
    raw = str(text or "").strip()
    # Strip markdown noise and forbidden system references.
    raw = re.sub(r"[#*`>]+", "", raw)
    raw = re.sub(r"教学辅助系统|智能助教|AI\s*助教|本系统", "", raw)
    raw = re.sub(r"[ \t]+", "", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()[:300]


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------
async def run_generation_job(
    evaluation_id: str, class_offering_id: int, teacher_id: int, prompt: str = ""
) -> None:
    try:
        _set_status(
            evaluation_id,
            status="generating",
            ai_gen_status="running",
            ai_gen_error="",
            progress={"done": 0, "total": 1, "current_label": "正在归集班级表现…"},
        )
        with get_db_connection() as conn:
            teacher_row = conn.execute(
                "SELECT id, name, email AS username FROM teachers WHERE id = ? LIMIT 1",
                (int(teacher_id),),
            ).fetchone()
            teacher = dict(teacher_row) if teacher_row else {"id": teacher_id, "name": "", "username": ""}
            offering_fields = te.build_fields_from_offering(conn, int(class_offering_id), teacher=teacher)
            performance = build_class_performance_summary(conn, int(class_offering_id))
            classroom_context = _classroom_context(conn, int(class_offering_id))

        warnings: list[str] = []
        fields = dict(offering_fields)
        try:
            _set_status(evaluation_id, progress={"done": 0, "total": 1, "current_label": "AI 正在评分与撰写评语…"})
            raw = await _chat_json(
                _system_prompt(), _user_prompt(offering_fields, performance, classroom_context, prompt)
            )
            if not raw:
                raise ValueError("AI 未返回有效 JSON")
            ai_fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
            fields = _merge_fields(offering_fields, ai_fields)
            scores = _coerce_scores(raw.get("scores"))
            analysis = _clean_analysis(raw.get("analysis"))
            if not analysis:
                analysis = _fallback_analysis(fields, performance)
                warnings.append("AI 未返回评语，已使用本地草稿评语，请复核。")
        except Exception as exc:  # noqa: BLE001 — fall back to a local complete draft.
            scores = _fallback_scores(performance)
            analysis = _fallback_analysis(fields, performance)
            warnings.append(
                f"AI 生成不可用，已使用本地评分与评语草稿（{type(exc).__name__}: {str(exc)[:160]}）。请复核后再导出。"
            )

        items = [{"score": score} for score in scores]
        normalized = te.normalize_evaluation_payload(fields, items, analysis)
        total = normalized["score_total"]
        rating = normalized["rating"]

        with get_db_connection() as conn:
            course_name = normalized["fields"].get("course_name") or "教师评学表"
            te.apply_generated_payload(
                conn,
                evaluation_id,
                fields=normalized["fields"],
                items=normalized["items"],
                analysis=normalized["analysis"],
                title=f"{course_name}（按班级生成）",
                ai_gen_status="completed" if not warnings else "completed_with_fallback",
                ai_gen_error="；".join(warnings)[:800],
                import_preview={
                    "performance_summary": performance.get("performance_summary") or "",
                    "score_total": total,
                    "rating": rating,
                    "warnings": warnings[:6],
                },
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _set_status(
            evaluation_id,
            status="failed",
            ai_gen_status="failed",
            ai_gen_error=f"生成失败：{type(exc).__name__}: {str(exc)[:400]}",
        )
