"""Career-development network service.

Resolves a student's major + graduation timeline, loads (or has the
deep-thinking AI generate) the major's career network, runs the personality
test, and — crucially — asks the deep-thinking AI to *re-weight* recommendations
and author pre-graduation knowledge cards for that specific student using their
explicit profile + a HIDDEN behavioural profile that must never be surfaced.

The two heavy AI steps run on the unified scheduler (handlers at the bottom):
* ``career_major_network_generate`` — once per non-seed major, cached for all.
* ``career_personalize_generate``   — once per student, after they finish the test.

See [[scheduler-and-reminders]], [[agent-bridge-and-knowledge]] and the seed in
``career_seed_data.py``.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from typing import Any, Optional

from ..core import ai_client
from ..database import get_db_connection
from ..db.schema_career_path import ensure_career_path_schema
from .career_seed_data import (
    CAREER_PERSONALITY_QUESTIONS,
    RIASEC_LABELS,
    SEED_MAJOR_KEYS,
    SOFTWARE_ENGINEERING_NETWORK,
    normalize_major_key,
    score_personality_answers,
)
from .prompt_utils import build_time_context_text, polite_address
from .psych_profile_service import (
    build_explicit_user_profile_prompt,
    load_explicit_user_profile,
    sanitize_hidden_profile_leaks,
)
from .scheduled_task_service import register_task_handler, schedule_task

NETWORK_GENERATE_TASK_KIND = "career_major_network_generate"
PERSONALIZE_TASK_KIND = "career_personalize_generate"

DEFAULT_PROGRAM_YEARS = 4
PREP_LEVELS = ("非常重要", "一般重要", "需了解")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _json_loads(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        value = json.loads(raw) if raw else fallback
        return value if value is not None else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback


def _extract_json_object(value: Any) -> Optional[dict[str, Any]]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# graduation timeline derivation
# ---------------------------------------------------------------------------
def _parse_enrollment_year(*candidates: Any) -> Optional[int]:
    """Best-effort enrollment year from explicit fields or a class name.

    Handles '软工2401' (24→2024), '网络工程2023', '2024级', plain 2024/24.
    """
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        m = re.search(r"(20\d{2})", text)
        if m:
            return int(m.group(1))
        m = re.search(r"(19\d{2})", text)
        if m:
            return int(m.group(1))
        # trailing 4-digit class code like 2401 -> first two digits are the year
        m = re.search(r"(\d{2})\d{2}\b", text)
        if m:
            yy = int(m.group(1))
            return 2000 + yy if yy < 80 else 1900 + yy
        m = re.search(r"\b(\d{2})\b", text)
        if m:
            yy = int(m.group(1))
            if 0 <= yy <= 60:
                return 2000 + yy
    return None


def derive_timeline(class_row: dict[str, Any]) -> dict[str, Any]:
    """Compute enrollment/graduation years + time-to-graduation for a class."""
    program_years = class_row.get("program_duration_years")
    try:
        program_years = int(program_years) if program_years else DEFAULT_PROGRAM_YEARS
    except (TypeError, ValueError):
        program_years = DEFAULT_PROGRAM_YEARS
    if program_years < 2 or program_years > 8:
        program_years = DEFAULT_PROGRAM_YEARS

    enrollment_year = None
    raw_enroll = class_row.get("enrollment_year")
    try:
        enrollment_year = int(raw_enroll) if raw_enroll else None
    except (TypeError, ValueError):
        enrollment_year = None
    if not enrollment_year:
        enrollment_year = _parse_enrollment_year(
            class_row.get("academic_grade"),
            class_row.get("academic_class_code"),
            class_row.get("name"),
            class_row.get("academic_class_name"),
        )

    graduation_year = None
    raw_grad = class_row.get("expected_graduation_year")
    try:
        graduation_year = int(raw_grad) if raw_grad else None
    except (TypeError, ValueError):
        graduation_year = None
    if not graduation_year and enrollment_year:
        graduation_year = enrollment_year + program_years

    now = _now()
    years_to_grad: Optional[float] = None
    months_to_grad: Optional[int] = None
    graduation_date_label = ""
    if graduation_year:
        # 国内本科多为 6 月毕业
        grad_date = datetime(graduation_year, 7, 1)
        graduation_date_label = f"{graduation_year} 年 6 月"
        delta_days = (grad_date - now).days
        months_to_grad = max(0, round(delta_days / 30.4))
        years_to_grad = round(delta_days / 365.25, 1)

    return {
        "enrollment_year": enrollment_year,
        "graduation_year": graduation_year,
        "program_duration_years": program_years,
        "graduation_date_label": graduation_date_label,
        "years_to_graduation": years_to_grad,
        "months_to_graduation": months_to_grad,
        "already_graduated": bool(graduation_year and years_to_grad is not None and years_to_grad <= 0),
    }


# ---------------------------------------------------------------------------
# student context (major, class, profile, hidden profile)
# ---------------------------------------------------------------------------
def resolve_student_context(conn, student_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT s.id, s.name, s.gender, s.class_id, s.school_code,
               s.academic_major AS student_major, s.academic_grade AS student_grade,
               s.description, s.nickname, s.today_mood,
               c.name AS class_name, c.major AS class_major, c.academic_major,
               c.academic_class_code, c.academic_class_name, c.academic_grade,
               c.enrollment_year, c.expected_graduation_year, c.program_duration_years,
               c.college, c.department
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.id = ?
          AND COALESCE(s.enrollment_status, 'active') = 'active'
        LIMIT 1
        """,
        (student_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    major_name = (
        str(item.get("class_major") or "").strip()
        or str(item.get("academic_major") or "").strip()
        or str(item.get("student_major") or "").strip()
        or "软件工程"
    )
    timeline = derive_timeline(item)
    return {
        "student_id": int(item["id"]),
        "name": str(item.get("name") or ""),
        "gender": str(item.get("gender") or ""),
        "school_code": str(item.get("school_code") or "gxufl"),
        "class_name": str(item.get("class_name") or ""),
        "college": str(item.get("college") or ""),
        "department": str(item.get("department") or ""),
        "major_name": major_name,
        "major_key": normalize_major_key(major_name),
        "description": str(item.get("description") or ""),
        "nickname": str(item.get("nickname") or ""),
        "timeline": timeline,
    }


def _load_hidden_profile_for_student(conn, student_id: int) -> dict[str, Any]:
    """Most-recent behavioural profile across all the student's offerings.

    Reused only inside AI prompts; NEVER returned to the page. Mirrors
    ``psych_profile_service.load_latest_hidden_profile`` but spans offerings.
    """
    try:
        row = conn.execute(
            """
            SELECT profile_summary, mental_state_summary, support_strategy,
                   personality_traits, preference_summary, language_habit_summary,
                   preferred_ai_style, interest_hypothesis, evidence_summary, confidence
            FROM classroom_behavior_profiles
            WHERE user_pk = ? AND user_role = 'student'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (student_id,),
        ).fetchone()
    except Exception:
        row = None
    return dict(row) if row else {}


def _build_hidden_profile_block(conn, student_id: int) -> str:
    profile = _load_hidden_profile_for_student(conn, student_id)
    if not profile:
        return "（暂无后台学习支持参考，请仅依据显式资料与测试结果判断。）"
    parts = [
        f"长期支持摘要：{profile.get('profile_summary') or '（无）'}",
        f"性格特征推测：{profile.get('personality_traits') or '（无稳定判断）'}",
        f"偏好与兴趣：{profile.get('preference_summary') or profile.get('interest_hypothesis') or '（无）'}",
        f"表达与用语习惯：{profile.get('language_habit_summary') or '（无）'}",
        f"当前状态：{profile.get('mental_state_summary') or '（中性）'}",
        f"建议支持策略：{profile.get('support_strategy') or '（保持耐心、鼓励）'}",
        f"置信度：{profile.get('confidence') or 'medium'}",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# network: seed / cache / generation
# ---------------------------------------------------------------------------
def _is_seed_major(major_key: str) -> bool:
    if major_key in {normalize_major_key(k) for k in SEED_MAJOR_KEYS}:
        return True
    return major_key in SEED_MAJOR_KEYS


def _seed_network_for(major_key: str) -> Optional[dict[str, Any]]:
    if _is_seed_major(major_key):
        return copy.deepcopy(SOFTWARE_ENGINEERING_NETWORK)
    return None


def load_major_network_row(conn, school_code: str, major_key: str) -> Optional[dict[str, Any]]:
    ensure_career_path_schema(conn)
    row = conn.execute(
        """
        SELECT id, school_code, major_key, major_name, status, source,
               network_json, knowledge_json, doc_markdown, model_label,
               error_message, generated_at, updated_at
        FROM career_major_networks
        WHERE school_code = ? AND major_key = ?
        LIMIT 1
        """,
        (school_code, major_key),
    ).fetchone()
    return dict(row) if row else None


def _upsert_major_network(
    conn,
    *,
    school_code: str,
    major_key: str,
    major_name: str,
    status: str,
    source: str,
    network: Optional[dict[str, Any]] = None,
    doc_markdown: str = "",
    model_label: str = "",
    error_message: str = "",
) -> None:
    ensure_career_path_schema(conn)
    now = _now_iso()
    existing = conn.execute(
        "SELECT id FROM career_major_networks WHERE school_code = ? AND major_key = ? LIMIT 1",
        (school_code, major_key),
    ).fetchone()
    network_json = json.dumps(network, ensure_ascii=False) if network is not None else None
    generated_at = now if status == "ready" else None
    if existing:
        sets = ["status = ?", "source = ?", "major_name = ?", "updated_at = ?", "error_message = ?"]
        params: list[Any] = [status, source, major_name, now, error_message]
        if network_json is not None:
            sets.append("network_json = ?")
            params.append(network_json)
        if doc_markdown:
            sets.append("doc_markdown = ?")
            params.append(doc_markdown)
        if model_label:
            sets.append("model_label = ?")
            params.append(model_label)
        if generated_at:
            sets.append("generated_at = ?")
            params.append(generated_at)
        params.extend([school_code, major_key])
        conn.execute(
            f"UPDATE career_major_networks SET {', '.join(sets)} WHERE school_code = ? AND major_key = ?",
            params,
        )
    else:
        conn.execute(
            """
            INSERT INTO career_major_networks
                (school_code, major_key, major_name, status, source, network_json,
                 doc_markdown, model_label, error_message, generated_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                school_code, major_key, major_name, status, source,
                network_json or "{}", doc_markdown, model_label, error_message,
                generated_at, now, now,
            ),
        )


def get_or_prepare_network(conn, ctx: dict[str, Any]) -> dict[str, Any]:
    """Return the network + a status. Seeds SE instantly; schedules AI for others."""
    school_code = ctx["school_code"]
    major_key = ctx["major_key"]
    major_name = ctx["major_name"]

    seed = _seed_network_for(major_key)
    if seed is not None:
        return {"status": "ready", "source": "seed", "network": seed}

    row = load_major_network_row(conn, school_code, major_key)
    if row and row.get("status") == "ready":
        network = _json_loads(row.get("network_json"), {})
        if network.get("nodes"):
            return {"status": "ready", "source": row.get("source") or "ai", "network": network}
    if row and row.get("status") == "generating":
        return {"status": "generating", "source": "ai", "network": None}

    # Need to (re)generate. Mark generating + schedule the task.
    _upsert_major_network(
        conn, school_code=school_code, major_key=major_key, major_name=major_name,
        status="generating", source="ai",
    )
    schedule_task(
        conn,
        task_kind=NETWORK_GENERATE_TASK_KIND,
        run_at=_now_iso(),
        payload={"school_code": school_code, "major_key": major_key, "major_name": major_name},
        dedupe_key=f"career-network:{school_code}:{major_key}",
        title=f"生成 {major_name} 职业网络",
        max_attempts=3,
    )
    return {"status": "generating", "source": "ai", "network": None}


# ---------------------------------------------------------------------------
# per-student session
# ---------------------------------------------------------------------------
def _load_session_row(conn, student_id: int) -> Optional[dict[str, Any]]:
    ensure_career_path_schema(conn)
    row = conn.execute(
        """
        SELECT id, student_id, school_code, major_key, major_name, status,
               enrollment_year, graduation_year, program_duration_years,
               test_answers_json, test_result_json, personalized_json,
               model_label, error_message, submitted_at, generated_at,
               created_at, updated_at
        FROM career_student_sessions
        WHERE student_id = ?
        LIMIT 1
        """,
        (student_id,),
    ).fetchone()
    return dict(row) if row else None


def ensure_session(conn, ctx: dict[str, Any]) -> dict[str, Any]:
    """Create the per-student session row if missing; keep major/timeline fresh."""
    ensure_career_path_schema(conn)
    student_id = ctx["student_id"]
    tl = ctx["timeline"]
    row = _load_session_row(conn, student_id)
    now = _now_iso()
    if row:
        # keep major/graduation aligned if the academic data changed
        conn.execute(
            """
            UPDATE career_student_sessions
            SET major_key = ?, major_name = ?, enrollment_year = ?, graduation_year = ?,
                program_duration_years = ?, updated_at = ?
            WHERE student_id = ?
            """,
            (
                ctx["major_key"], ctx["major_name"], tl.get("enrollment_year"),
                tl.get("graduation_year"), tl.get("program_duration_years"), now, student_id,
            ),
        )
        row.update({
            "major_key": ctx["major_key"], "major_name": ctx["major_name"],
            "enrollment_year": tl.get("enrollment_year"),
            "graduation_year": tl.get("graduation_year"),
            "program_duration_years": tl.get("program_duration_years"),
        })
        return row
    conn.execute(
        """
        INSERT INTO career_student_sessions
            (student_id, school_code, major_key, major_name, status,
             enrollment_year, graduation_year, program_duration_years,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, 'intro', ?, ?, ?, ?, ?)
        """,
        (
            student_id, ctx["school_code"], ctx["major_key"], ctx["major_name"],
            tl.get("enrollment_year"), tl.get("graduation_year"),
            tl.get("program_duration_years"), now, now,
        ),
    )
    return _load_session_row(conn, student_id) or {}


def save_test_progress(conn, ctx: dict[str, Any], answers: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist partial quiz answers so the student can resume after leaving.

    Only writes while the test is unfinished (status intro/testing); once a
    student has submitted/generated/ready we never clobber their result here.
    """
    ensure_session(conn, ctx)
    student_id = ctx["student_id"]
    row = _load_session_row(conn, student_id) or {}
    status = str(row.get("status") or "intro")
    if status not in ("intro", "testing"):
        return {"status": status, "saved": False}
    conn.execute(
        """
        UPDATE career_student_sessions
        SET status = 'testing', test_answers_json = ?, updated_at = ?
        WHERE student_id = ?
        """,
        (json.dumps(answers, ensure_ascii=False), _now_iso(), student_id),
    )
    return {"status": "testing", "saved": True, "answered": len(answers)}


def save_test_and_generate(conn, ctx: dict[str, Any], answers: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist answers, score them, flip to 'generating' and schedule the AI."""
    ensure_session(conn, ctx)
    student_id = ctx["student_id"]
    result = score_personality_answers(answers)
    now = _now_iso()
    conn.execute(
        """
        UPDATE career_student_sessions
        SET status = 'generating', test_answers_json = ?, test_result_json = ?,
            error_message = '', submitted_at = ?, updated_at = ?
        WHERE student_id = ?
        """,
        (
            json.dumps(answers, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            now, now, student_id,
        ),
    )
    schedule_task(
        conn,
        task_kind=PERSONALIZE_TASK_KIND,
        run_at=_now_iso(),
        payload={"student_id": student_id},
        dedupe_key=f"career-personalize:{student_id}",
        title=f"为学生 {student_id} 定制职业网络",
        owner_role="student",
        owner_user_pk=student_id,
        max_attempts=3,
    )
    return {"status": "generating", "test_result": result}


def reset_session(conn, student_id: int) -> None:
    ensure_career_path_schema(conn)
    conn.execute(
        """
        UPDATE career_student_sessions
        SET status = 'intro', test_answers_json = '[]', test_result_json = '{}',
            personalized_json = '{}', error_message = '', submitted_at = NULL,
            generated_at = NULL, updated_at = ?
        WHERE student_id = ?
        """,
        (_now_iso(), student_id),
    )


# ---------------------------------------------------------------------------
# pre-graduation knowledge cards (baseline derivation + AI merge)
# ---------------------------------------------------------------------------
def derive_prep_cards_from_node(node: dict[str, Any]) -> dict[str, Any]:
    """Baseline knowledge stacks for a node when the AI hasn't enriched it."""
    pre = [str(x).strip() for x in (node.get("pre") or []) if str(x).strip()]
    know = [str(x).strip() for x in (node.get("know") or []) if str(x).strip()]
    half = (len(know) + 1) // 2
    stacks = [
        {"level": "非常重要", "items": pre or know[:2]},
        {"level": "一般重要", "items": know[:half]},
        {"level": "需了解", "items": know[half:]},
    ]
    stacks = [s for s in stacks if s["items"]]
    return {
        "summary": node.get("reason") or node.get("desc") or "",
        "stacks": stacks,
    }


def build_prep_cards(network: dict[str, Any], personalized: dict[str, Any]) -> dict[str, Any]:
    """Per-node prep cards: AI override if present, else derived from the node."""
    ai_cards = personalized.get("prep_cards") or {}
    cards: dict[str, Any] = {}
    for node in network.get("nodes", []):
        tag = node.get("tag")
        if not tag:
            continue
        ai_card = ai_cards.get(tag)
        if isinstance(ai_card, dict) and ai_card.get("stacks"):
            cards[tag] = ai_card
        else:
            cards[tag] = derive_prep_cards_from_node(node)
    return cards


def apply_personalization(network: dict[str, Any], personalized: dict[str, Any]) -> dict[str, Any]:
    """Overlay AI rec-overrides + glow onto a copy of the network for the page."""
    net = copy.deepcopy(network)
    overrides = personalized.get("rec_overrides") or {}
    glow = personalized.get("dim_glow") or {}
    tips = personalized.get("node_tips") or {}
    highlights = set(personalized.get("highlights") or [])
    for node in net.get("nodes", []):
        tag = node.get("tag")
        if tag in overrides:
            try:
                node["rec"] = max(1, min(5, int(round(float(overrides[tag])))))
            except (TypeError, ValueError):
                pass
        node["base_rec"] = node.get("rec")
        if tag in glow:
            try:
                node["glow"] = max(0.0, min(1.0, float(glow[tag])))
            except (TypeError, ValueError):
                node["glow"] = None
        if tag in tips:
            node["tip"] = sanitize_hidden_profile_leaks(tips[tag])
        node["highlighted"] = tag in highlights
    return net


# ---------------------------------------------------------------------------
# state assembly for the page / API
# ---------------------------------------------------------------------------
def build_state(conn, student_id: int) -> dict[str, Any]:
    ctx = resolve_student_context(conn, student_id)
    if not ctx:
        return {"ok": False, "error": "student_not_found"}
    session = ensure_session(conn, ctx)
    net_state = get_or_prepare_network(conn, ctx)

    status = str(session.get("status") or "intro")
    personalized = _json_loads(session.get("personalized_json"), {})
    test_result = _json_loads(session.get("test_result_json"), {})

    # Resolve the network to display: if personalised + ready, overlay.
    network = net_state.get("network")
    display_network = None
    prep_cards: dict[str, Any] = {}
    if network:
        if status == "ready" and personalized:
            display_network = apply_personalization(network, personalized)
        else:
            display_network = copy.deepcopy(network)
        prep_cards = build_prep_cards(network, personalized if status == "ready" else {})

    tl = ctx["timeline"]
    address = polite_address(ctx["name"], "student")

    # 未完成测试时回传已作答的草稿，前端据此从断点续做（而非从头开始）。
    draft_answers = _json_loads(session.get("test_answers_json"), [])
    draft = draft_answers if (status in ("intro", "testing") and isinstance(draft_answers, list)) else []

    # Combined lifecycle status the frontend switches on.
    if net_state.get("status") == "generating" and not network:
        page_phase = "network_generating"
    elif status in ("intro", "testing"):
        page_phase = "intro"
    elif status in ("generating", "submitted"):
        page_phase = "personalizing"
    elif status == "failed":
        page_phase = "ready"  # show base network, surface a soft note
    else:
        page_phase = "ready"

    return {
        "ok": True,
        "phase": page_phase,
        "session_status": status,
        "student": {
            "name": ctx["name"],
            "address": address,
            "class_name": ctx["class_name"],
            "college": ctx["college"],
        },
        "major": {"name": ctx["major_name"], "key": ctx["major_key"]},
        "timeline": tl,
        "network": display_network,
        "network_status": net_state.get("status"),
        "network_source": net_state.get("source"),
        "prep_cards": prep_cards,
        "personalized": _public_personalized(personalized) if status == "ready" else {},
        "test_result": {"holland_code": test_result.get("holland_code"), "top_dims": test_result.get("top_dims")},
        "draft": draft,
        "error_message": sanitize_hidden_profile_leaks(session.get("error_message") or ""),
    }


def _public_personalized(personalized: dict[str, Any]) -> dict[str, Any]:
    """Strip anything internal; sanitize free text against profile leaks."""
    if not personalized:
        return {}
    return {
        "greeting": sanitize_hidden_profile_leaks(personalized.get("greeting") or ""),
        "summary": sanitize_hidden_profile_leaks(personalized.get("summary") or ""),
        "timeline_advice": sanitize_hidden_profile_leaks(personalized.get("timeline_advice") or ""),
        "top_paths": [
            {
                "tag": str(p.get("tag") or ""),
                "name": sanitize_hidden_profile_leaks(p.get("name") or ""),
                "why": sanitize_hidden_profile_leaks(p.get("why") or ""),
            }
            for p in (personalized.get("top_paths") or [])
            if isinstance(p, dict)
        ][:4],
        "holland_code": str(personalized.get("holland_code") or ""),
    }


def get_questions() -> list[dict[str, Any]]:
    """Public question bank (without the scoring weights)."""
    public: list[dict[str, Any]] = []
    for q in CAREER_PERSONALITY_QUESTIONS:
        item = {k: v for k, v in q.items() if k not in ("low_weights", "high_weights")}
        if "options" in item:
            item["options"] = [{"value": o["value"], "label": o["label"]} for o in q["options"]]
        public.append(item)
    return public


# ---------------------------------------------------------------------------
# AI prompts
# ---------------------------------------------------------------------------
def _network_seed_example() -> str:
    """A trimmed structural example handed to the AI for non-seed majors."""
    sample = {
        "major_name": "软件工程",
        "cats": SOFTWARE_ENGINEERING_NETWORK["cats"][:2],
        "nodes": [
            {k: SOFTWARE_ENGINEERING_NETWORK["nodes"][0][k]
             for k in ("cat", "tag", "name", "rec", "lang", "riasec", "desc", "reason", "pre", "know", "tl", "branch", "trend")}
        ],
        "links": SOFTWARE_ENGINEERING_NETWORK["links"][:2],
    }
    return json.dumps(sample, ensure_ascii=False, indent=1)


def build_network_generation_prompt(major_name: str) -> tuple[str, str]:
    system = (
        "你是资深的高校生涯规划与行业研究专家。你要为某个本科专业，编制一张"
        "结构化的『职业发展路线网络』数据，供前端渲染成可交互的职业网络图。"
        "必须严格输出 JSON，不要任何解释文字、不要 markdown 代码块。"
        + build_time_context_text()
    )
    user = "\n\n".join([
        f"目标专业：{major_name}（中国普通本科，结合 2025–2026 就业现实与 AI 对行业的冲击）。",
        "请产出该专业毕业生的完整就业去向网络，包含 4–6 个就业大类(cats)、每个大类下若干"
        "细分方向(nodes，总计 18–26 个)、以及跨方向可转岗的分叉(links)。",
        "字段与结构必须与下方示例完全一致：",
        "cats[].id 用大写字母 A/B/C…；nodes[].tag 形如 A1/A2；nodes[].cat 对应大类 id；"
        "rec 为 1–5 的推荐度整数；lang 为是否属于『外语+技术/国际化』特色方向(布尔)；"
        "riasec 为该方向最相关的霍兰德代码数组(取 R/I/A/S/E/C 中 1–3 个)；"
        "pre/know 为字符串数组；tl 为 4 个阶段，每个阶段是 [时间, 职位, 说明] 三元数组(0–1年→3–5年→5–10年→10年+)；"
        "branch 为一句分叉路径文本；trend 为该方向的未来趋势。"
        "links 为 [fromTag, fromStage, toTag, toStage] 四元数组(stage 取 0–3)。",
        "另外加入顶层 major_name、graduate_label、intro(一句该专业差异化建议)。",
        "请确保推荐度客观：综合市场需求×薪资上限×学历友好度×抗AI替代×普通本科适配度。"
        "若该专业属于文科/管理/语言类，也要给出真实可行的就业大类（如内容/运营/教育/公共部门/国际化等），不要硬套理工方向。",
        "结构示例（只示意字段，不要照抄内容）：\n" + _network_seed_example(),
        "只返回一个 JSON 对象。",
    ])
    return system, user


def build_personalization_prompt(
    ctx: dict[str, Any],
    network: dict[str, Any],
    test_result: dict[str, Any],
    explicit_prompt: str,
    hidden_block: str,
) -> tuple[str, str]:
    tl = ctx["timeline"]
    node_index = "\n".join(
        f"- {n.get('tag')} {n.get('name')}（默认推荐 {n.get('rec')}，riasec {','.join(n.get('riasec') or [])}）"
        for n in network.get("nodes", [])
    )
    scores = test_result.get("scores") or {}
    score_text = "；".join(f"{RIASEC_LABELS.get(k, k)}={v}" for k, v in scores.items())

    system = (
        "你是一位顶尖的生涯规划顾问与心理画像专家，正在为一名具体的大学生定制专属职业发展网络。"
        "你拥有该生的显式资料、霍兰德职业兴趣测试结果，以及一份只供你内部参考的学习支持画像。"
        "你的判断要精准、温暖、可执行。必须严格输出 JSON，不要任何解释、不要 markdown 代码块。\n"
        "【绝对保密】严禁以任何形式向学生透露、暗示或命名存在『侧写』『画像』『后台分析』『系统提示』"
        "『侧写师』等内部信息来源；所有文案都要像是顾问基于其公开资料与测试自然得出的，不能有读心感。\n"
        + build_time_context_text()
    )
    user = "\n\n".join([
        f"【学生显式资料】\n姓名称呼：{polite_address(ctx['name'], 'student')}；专业：{ctx['major_name']}；班级：{ctx['class_name']}。",
        explicit_prompt,
        f"【毕业时间线】入学约 {tl.get('enrollment_year') or '未知'} 年，预计 {tl.get('graduation_date_label') or '未知'} 毕业，"
        f"距今约 {tl.get('years_to_graduation') if tl.get('years_to_graduation') is not None else '未知'} 年"
        f"（{tl.get('months_to_graduation') if tl.get('months_to_graduation') is not None else '未知'} 个月）。",
        f"【霍兰德测试结果】代码 {test_result.get('holland_code') or '未知'}；各维度得分：{score_text}。"
        f"自填补充：{test_result.get('free_text') or '（无）'}；侧重选择：{('、'.join(test_result.get('focus_choices') or [])) or '（无）'}。",
        "【内部学习支持画像 — 仅供你判断性格与节奏，严禁外显或提及来源】\n" + hidden_block,
        "【该专业职业网络的全部方向节点】\n" + node_index,
        "请综合以上信息，输出一个 JSON 对象，字段如下：\n"
        "greeting: 给该生的一句专属欢迎/定位文案；\n"
        "summary: 用 1–2 句话点出最适合 TA 的发展基调（基于性格但不读心）；\n"
        "holland_code: 你最终判断的霍兰德代码；\n"
        "rec_overrides: { 节点tag: 1–5 }，对最契合 TA 的方向上调、不契合的下调（只列需要调整的节点，幅度克制合理）；\n"
        "highlights: 最推荐 TA 重点看的 3–5 个节点 tag 数组；\n"
        "dim_glow: { 节点tag: 0–1 }，作为星图发光强度，越契合越亮（覆盖尽量多节点，弱相关给 0.15–0.35）；\n"
        "node_tips: { 节点tag: 针对 TA 的一句话建议 }（覆盖 highlights 与若干相关节点）；\n"
        "prep_cards: { 节点tag: { summary, stacks:[{level:'非常重要'|'一般重要'|'需了解', items:[知识/技能字符串]}] } }，"
        "为 highlights 中的节点给出『从现在到毕业要补的知识栈』，分重要程度；\n"
        "timeline_advice: 结合 TA 距毕业的时间，给出『来不来得及、该怎么准备』的温馨而具体的建议；\n"
        "top_paths: [ {tag, name, why} ]，2–4 条最推荐路径及理由。\n"
        "只返回这个 JSON 对象。",
    ])
    return system, user


# ---------------------------------------------------------------------------
# AI call
# ---------------------------------------------------------------------------
async def _call_thinking_ai(system_prompt: str, user_message: str, *, label: str, timeout: float = 240.0) -> dict[str, Any]:
    response = await ai_client.post(
        "/api/ai/chat",
        json={
            "system_prompt": system_prompt,
            "messages": [],
            "new_message": user_message,
            "model_capability": "thinking",
            "task_type": "deep_text_reasoning",
            "response_format": "json",
            "task_priority": "background",
            "task_label": label,
            "web_search_enabled": False,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "success":
        raise RuntimeError(f"AI 返回失败: {str(data)[:300]}")
    payload = data.get("response_json")
    if not isinstance(payload, dict):
        payload = _extract_json_object(data.get("response_text"))
    if not isinstance(payload, dict):
        raise RuntimeError("AI 未返回 JSON 对象")
    return payload


# ---------------------------------------------------------------------------
# response validation
# ---------------------------------------------------------------------------
def _validate_network_payload(payload: dict[str, Any], major_name: str) -> dict[str, Any]:
    cats = payload.get("cats") or []
    nodes = payload.get("nodes") or []
    links = payload.get("links") or []
    if not isinstance(cats, list) or not isinstance(nodes, list) or len(nodes) < 6:
        raise RuntimeError("AI 网络结构不完整")

    clean_cats = []
    for c in cats:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        clean_cats.append({
            "id": str(c.get("id")), "name": str(c.get("name") or ""),
            "desc": str(c.get("desc") or ""), "icon": str(c.get("icon") or "✨"),
            "c1": str(c.get("c1") or "#6ee7ff"), "c2": str(c.get("c2") or "#3b82f6"),
        })
    clean_nodes = []
    valid_tags = set()
    for n in nodes:
        if not isinstance(n, dict) or not n.get("tag") or not n.get("cat"):
            continue
        tl = n.get("tl") or []
        norm_tl = []
        for stage in tl[:4]:
            if isinstance(stage, (list, tuple)) and len(stage) >= 2:
                norm_tl.append([str(stage[0]), str(stage[1]), str(stage[2]) if len(stage) > 2 else "—"])
        try:
            rec = max(1, min(5, int(round(float(n.get("rec", 3))))))
        except (TypeError, ValueError):
            rec = 3
        riasec = [str(x).upper() for x in (n.get("riasec") or []) if str(x).upper() in RIASEC_LABELS]
        tag = str(n.get("tag"))
        valid_tags.add(tag)
        clean_nodes.append({
            "cat": str(n.get("cat")), "tag": tag, "name": str(n.get("name") or tag),
            "rec": rec, "lang": bool(n.get("lang")), "riasec": riasec,
            "desc": str(n.get("desc") or ""), "reason": str(n.get("reason") or ""),
            "pre": [str(x) for x in (n.get("pre") or [])],
            "know": [str(x) for x in (n.get("know") or [])],
            "tl": norm_tl or [["0–1 年", str(n.get("name") or tag), "—"]],
            "branch": str(n.get("branch") or ""), "trend": str(n.get("trend") or ""),
        })
    clean_links = []
    for l in links:
        if isinstance(l, (list, tuple)) and len(l) == 4 and str(l[0]) in valid_tags and str(l[2]) in valid_tags:
            try:
                clean_links.append([str(l[0]), int(l[1]), str(l[2]), int(l[3])])
            except (TypeError, ValueError):
                continue
    return {
        "major_name": str(payload.get("major_name") or major_name),
        "graduate_label": str(payload.get("graduate_label") or f"{major_name}毕业生"),
        "intro": str(payload.get("intro") or ""),
        "cats": clean_cats,
        "nodes": clean_nodes,
        "links": clean_links,
    }


def _validate_personalization_payload(payload: dict[str, Any], network: dict[str, Any]) -> dict[str, Any]:
    valid_tags = {n.get("tag") for n in network.get("nodes", [])}

    def _clean_map(raw, caster):
        out = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if k in valid_tags:
                    casted = caster(v)
                    if casted is not None:
                        out[k] = casted
        return out

    def _as_rec(v):
        try:
            return max(1, min(5, int(round(float(v)))))
        except (TypeError, ValueError):
            return None

    def _as_glow(v):
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return None

    prep_cards = {}
    raw_cards = payload.get("prep_cards")
    if isinstance(raw_cards, dict):
        for tag, card in raw_cards.items():
            if tag not in valid_tags or not isinstance(card, dict):
                continue
            stacks = []
            for s in (card.get("stacks") or []):
                if not isinstance(s, dict):
                    continue
                level = str(s.get("level") or "").strip()
                if level not in PREP_LEVELS:
                    level = "一般重要"
                items = [sanitize_hidden_profile_leaks(str(x)) for x in (s.get("items") or []) if str(x).strip()]
                if items:
                    stacks.append({"level": level, "items": items})
            if stacks:
                prep_cards[tag] = {"summary": sanitize_hidden_profile_leaks(card.get("summary") or ""), "stacks": stacks}

    return {
        "greeting": sanitize_hidden_profile_leaks(payload.get("greeting") or ""),
        "summary": sanitize_hidden_profile_leaks(payload.get("summary") or ""),
        "holland_code": str(payload.get("holland_code") or ""),
        "rec_overrides": _clean_map(payload.get("rec_overrides"), _as_rec),
        "highlights": [t for t in (payload.get("highlights") or []) if t in valid_tags][:6],
        "dim_glow": _clean_map(payload.get("dim_glow"), _as_glow),
        "node_tips": {
            k: sanitize_hidden_profile_leaks(v)
            for k, v in (payload.get("node_tips") or {}).items()
            if k in valid_tags and str(v or "").strip()
        },
        "prep_cards": prep_cards,
        "timeline_advice": sanitize_hidden_profile_leaks(payload.get("timeline_advice") or ""),
        "top_paths": [
            {"tag": str(p.get("tag") or ""), "name": str(p.get("name") or ""), "why": sanitize_hidden_profile_leaks(p.get("why") or "")}
            for p in (payload.get("top_paths") or [])
            if isinstance(p, dict)
        ][:4],
    }


# ---------------------------------------------------------------------------
# scheduler handlers
# ---------------------------------------------------------------------------
async def handle_network_generation(task: dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    school_code = str(payload.get("school_code") or "gxufl")
    major_key = str(payload.get("major_key") or "")
    major_name = str(payload.get("major_name") or major_key)
    if not major_key:
        return "skipped: missing major_key"
    try:
        system, user = build_network_generation_prompt(major_name)
        raw = await _call_thinking_ai(system, user, label=f"career_network:{major_key}")
        network = _validate_network_payload(raw, major_name)
    except Exception as exc:  # noqa: BLE001
        with get_db_connection() as conn:
            _upsert_major_network(
                conn, school_code=school_code, major_key=major_key, major_name=major_name,
                status="failed", source="ai", error_message=str(exc)[:400],
            )
            conn.commit()
        return f"failed: {str(exc)[:160]}"
    with get_db_connection() as conn:
        _upsert_major_network(
            conn, school_code=school_code, major_key=major_key, major_name=major_name,
            status="ready", source="ai", network=network,
            doc_markdown=str(network.get("intro") or ""), model_label="thinking",
        )
        conn.commit()
    return f"generated network for {major_name} ({len(network.get('nodes', []))} nodes)"


async def handle_personalization(task: dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    student_id = int(payload.get("student_id") or 0)
    if not student_id:
        return "skipped: missing student_id"

    # Gather context + prompt inputs (sync DB read).
    with get_db_connection() as conn:
        ctx = resolve_student_context(conn, student_id)
        if not ctx:
            return "skipped: student not found"
        net_state = get_or_prepare_network(conn, ctx)
        session = _load_session_row(conn, student_id) or {}
        test_result = _json_loads(session.get("test_result_json"), {})
        explicit_profile = load_explicit_user_profile(conn, student_id, "student")
        explicit_prompt = build_explicit_user_profile_prompt(explicit_profile)
        hidden_block = _build_hidden_profile_block(conn, student_id)
        conn.commit()

    network = net_state.get("network")
    if not network:
        # Major network still generating — retry shortly by raising.
        raise RuntimeError("major network not ready yet")

    try:
        system, user = build_personalization_prompt(ctx, network, test_result, explicit_prompt, hidden_block)
        raw = await _call_thinking_ai(system, user, label=f"career_personalize:{student_id}")
        personalized = _validate_personalization_payload(raw, network)
        personalized["holland_code"] = personalized.get("holland_code") or test_result.get("holland_code") or ""
    except Exception as exc:  # noqa: BLE001
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE career_student_sessions SET status = 'failed', error_message = ?, updated_at = ? WHERE student_id = ?",
                (str(exc)[:400], _now_iso(), student_id),
            )
            conn.commit()
        return f"failed: {str(exc)[:160]}"

    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE career_student_sessions
            SET status = 'ready', personalized_json = ?, model_label = 'thinking',
                error_message = '', generated_at = ?, updated_at = ?
            WHERE student_id = ?
            """,
            (json.dumps(personalized, ensure_ascii=False), _now_iso(), _now_iso(), student_id),
        )
        conn.commit()
    return f"personalized career network for student {student_id}"


register_task_handler(NETWORK_GENERATE_TASK_KIND, handle_network_generation)
register_task_handler(PERSONALIZE_TASK_KIND, handle_personalization)
