"""Career-development network service.

Resolves academic context, provides a versioned interest questionnaire and
bounded presentation helpers. Lifecycle commands and durable AI adapters live
in career_lifecycle_service; deterministic evidence-based recommendations live
in career_recommendation_service. State reads never mutate business data.

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
from . import ai_web_research
from .career_seed_data import (
    CAREER_GENERAL_FOCUS_QUESTION,
    CAREER_PERSONALITY_QUESTIONS,
    SEED_MAJOR_KEYS,
    SOFTWARE_ENGINEERING_NETWORK,
    normalize_major_key,
    score_personality_answers,
)
from .prompt_utils import build_time_context_text, polite_address
from .psych_profile_service import (
    sanitize_hidden_profile_leaks,
)
from .scheduled_task_service import register_task_handler
from .career_recommendation_service import baseline_network

NETWORK_GENERATE_TASK_KIND = "career_major_network_generate"
PERSONALIZE_TASK_KIND = "career_personalize_generate"

PREP_LEVELS = ("非常重要", "一般重要", "需了解")
QUIZ_VERSION = "career-quiz-v2"
NETWORK_SCHEMA_VERSION = "career-network-v3"
SAFE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class CareerConflict(ValueError):
    def __init__(self, row: dict[str, Any]):
        super().__init__("资料已在另一个页面更新，请保留当前作答并刷新后重试")
        self.detail = {"code": "revision_conflict", "message": str(self),
                       "current_revision": int(row.get("revision") or 0),
                       "draft": _json_loads(row.get("test_answers_json"), []),
                       "quiz_mode": row.get("quiz_mode") or "quick", "quiz_version": QUIZ_VERSION}


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
    explicit_duration = bool(program_years)
    major_label = " ".join(str(class_row.get(key) or "") for key in ("student_major","class_major","academic_major"))
    pathway = "top_up" if "专升本" in major_label else "unknown"
    try:
        program_years = int(program_years) if program_years else None
    except (TypeError, ValueError):
        program_years = None
    if program_years is not None and (program_years < 1 or program_years > 8):
        program_years = None

    enrollment_year = None
    raw_enroll = class_row.get("enrollment_year")
    try:
        enrollment_year = int(raw_enroll) if raw_enroll else None
    except (TypeError, ValueError):
        enrollment_year = None
    if not enrollment_year:
        enrollment_year = _parse_enrollment_year(
            class_row.get("student_grade"),
            class_row.get("academic_grade"),
            class_row.get("academic_class_code"),
            class_row.get("class_name"),
            class_row.get("academic_class_name"),
        )

    graduation_year = None
    raw_grad = class_row.get("expected_graduation_year")
    try:
        graduation_year = int(raw_grad) if raw_grad else None
    except (TypeError, ValueError):
        graduation_year = None
    if not graduation_year and enrollment_year and program_years:
        graduation_year = enrollment_year + program_years
    if enrollment_year and not 1980 <= enrollment_year <= _now().year + 2:
        enrollment_year = None
    if graduation_year and not 1980 <= graduation_year <= _now().year + 10:
        graduation_year = None

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
        "program_pathway": pathway,
        "duration_source": "academic_record" if explicit_duration and program_years else "unknown",
        "enrollment_source": "academic_record" if raw_enroll and enrollment_year else ("academic_grade_or_class" if enrollment_year else "unknown"),
        "graduation_source": "academic_record" if raw_grad and graduation_year else ("enrollment_and_duration" if graduation_year else "unknown"),
        "graduation_precision": "year" if graduation_year else "unknown",
        "graduation_date_label": graduation_date_label,
        "years_to_graduation": years_to_grad,
        "months_to_graduation": months_to_grad,
        "already_graduated": bool(graduation_year and years_to_grad is not None and years_to_grad <= 0),
    }


# ---------------------------------------------------------------------------
# Student academic context
# ---------------------------------------------------------------------------
def resolve_student_context(conn, student_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT s.id, s.name, s.gender, s.class_id, s.school_code, s.school_name,
               s.academic_major AS student_major, s.academic_grade AS student_grade,
               s.description, s.nickname, s.today_mood,
               c.name AS class_name, c.major AS class_major, c.academic_major,
               c.academic_class_code, c.academic_class_name, c.academic_grade,
               c.enrollment_year, c.expected_graduation_year, c.program_duration_years,
               c.college, c.department
        FROM students s
        LEFT JOIN classes c ON c.id = s.class_id
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
        str(item.get("student_major") or "").strip()
        or str(item.get("class_major") or "").strip()
        or str(item.get("academic_major") or "").strip()
    )
    timeline = derive_timeline(item)
    from .career_major_mapping_service import resolve_career_major
    school_code = str(item.get("school_code") or "gxufl")
    major = resolve_career_major(conn, school_code, major_name)
    return {
        "student_id": int(item["id"]),
        "name": str(item.get("name") or ""),
        "gender": str(item.get("gender") or ""),
        "school_code": school_code,
        "school_name": str(item.get("school_name") or "").strip(),
        "class_name": str(item.get("class_name") or ""),
        "college": str(item.get("college") or ""),
        "department": str(item.get("department") or ""),
        **major,
        "major_confirmed": bool(major_name),
        "description": str(item.get("description") or ""),
        "nickname": str(item.get("nickname") or ""),
        "timeline": timeline,
    }


# ---------------------------------------------------------------------------
# network: seed / cache / generation
# ---------------------------------------------------------------------------
def _is_seed_major(major_key: str) -> bool:
    return major_key == "软件工程"


def _seed_network_for(major_key: str) -> Optional[dict[str, Any]]:
    if _is_seed_major(major_key):
        return _validate_network_payload(copy.deepcopy(SOFTWARE_ENGINEERING_NETWORK),"软件工程")
    return None


# ---------------------------------------------------------------------------
# per-student session
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# job-search keywords (baseline derivation + AI merge + on-demand fast AI)
# ---------------------------------------------------------------------------
MAX_JOB_KEYWORDS = 6
_JOB_TAIL_RE = re.compile(r"(开发工程师|研发工程师|工程师|开发|研发|师)$")
# 招聘市场上常见、好搜的语言/技术词，用于把方向名扩展成更贴合岗位命名的关键字。
_KEYWORD_TECH_HINTS = (
    "Java", "Python", "Go", "Golang", "C++", "C#", "PHP", "Node", "JavaScript",
    "Vue", "React", "Android", "iOS", "Flutter", "Unity", "Unreal", "Kubernetes",
    "Spring", "SQL", "BI", "RAG", "Agent", "鸿蒙", "ArkTS",
)


def derive_job_keywords_from_node(node: dict[str, Any]) -> list[str]:
    """Deterministic fallback keywords from a node when the AI hasn't supplied any.

    Expands the direction name into a few market-style search terms and folds in
    any well-known tech tokens mentioned in its prerequisites/knowledge.
    """
    name = re.sub(r"[（(].*?[）)]", "", str(node.get("name") or "")).strip()
    if not name:
        return []
    core = _JOB_TAIL_RE.sub("", name).strip() or name

    out: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in out and len(out) < MAX_JOB_KEYWORDS:
            out.append(value)

    add(name)
    if core and core != name:
        if "工程师" in name or "开发" in name:
            add(core + "工程师")
            add(core + "开发")
        add(core)

    haystack = " ".join([name] + [str(x) for x in (node.get("pre") or [])] + [str(x) for x in (node.get("know") or [])])
    for tech in _KEYWORD_TECH_HINTS:
        if len(out) >= MAX_JOB_KEYWORDS:
            break
        if tech.lower() in haystack.lower():
            add(tech + core if core and core != name else tech + name)
    return out[:MAX_JOB_KEYWORDS]


def _sanitize_keyword_list(raw: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        kw = sanitize_hidden_profile_leaks(str(item)).strip()
        if kw and kw not in out:
            out.append(kw[:24])
        if len(out) >= MAX_JOB_KEYWORDS:
            break
    return out


def build_job_keywords(network: dict[str, Any], personalized: dict[str, Any]) -> dict[str, list[str]]:
    """Per-node search keywords: AI-tailored if present, else derived from node."""
    ai_keywords = personalized.get("job_keywords") or {}
    out: dict[str, list[str]] = {}
    for node in network.get("nodes", []):
        tag = node.get("tag")
        if not tag:
            continue
        kws = _sanitize_keyword_list(ai_keywords.get(tag)) or derive_job_keywords_from_node(node)
        if kws:
            out[tag] = kws
    return out


def apply_personalization(network: dict[str, Any], personalized: dict[str, Any]) -> dict[str, Any]:
    """Overlay AI rec-overrides + glow onto a copy of the network for the page."""
    net = copy.deepcopy(network)
    overrides = personalized.get("rec_overrides") or {}
    glow = personalized.get("dim_glow") or {}
    tips = personalized.get("node_tips") or {}
    highlights = set(personalized.get("highlights") or [])
    for node in net.get("nodes", []):
        tag = node.get("tag")
        node["base_rec"] = node.get("rec")
        if tag in overrides:
            try:
                node["rec"] = max(1, min(5, int(round(float(overrides[tag])))))
            except (TypeError, ValueError):
                pass
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


def _public_personalized(personalized: dict[str, Any]) -> dict[str, Any]:
    """Strip anything internal; sanitize free text against profile leaks."""
    if not personalized:
        return {}
    return {
        "greeting": sanitize_hidden_profile_leaks(personalized.get("greeting") or ""),
        "summary": sanitize_hidden_profile_leaks(personalized.get("summary") or ""),
        "region_note": sanitize_hidden_profile_leaks(personalized.get("region_note") or ""),
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


def _is_technology_major(major_key: str) -> bool:
    text = str(major_key or "").strip()
    if text in SEED_MAJOR_KEYS:
        return True
    return any(token in text for token in (
        "软件", "计算机", "网络", "人工智能", "数据科学", "信息安全", "电子信息", "自动化",
    ))


def get_questions(*, mode: str = "quick", major_key: str = "") -> list[dict[str, Any]]:
    """Public, major-aware question bank without private scoring weights.

    Quick mode is the student default (seven questions, about one minute).
    Full mode preserves the original deeper exploration flow for students who
    want it.  Existing saved answers remain score-compatible by question id.
    """
    selected_mode = "full" if str(mode or "").strip().lower() == "full" else "quick"
    focus_id = "q8" if _is_technology_major(major_key) else "q_focus"
    focus_question = (
        next((q for q in CAREER_PERSONALITY_QUESTIONS if q.get("id") == "q8"), CAREER_GENERAL_FOCUS_QUESTION)
        if focus_id == "q8"
        else CAREER_GENERAL_FOCUS_QUESTION
    )
    if selected_mode == "full":
        questions = [q for q in CAREER_PERSONALITY_QUESTIONS if q.get("id") != "q8"]
        insert_at = next((index for index, q in enumerate(questions) if q.get("id") == "q9"), len(questions))
        questions.insert(insert_at, focus_question)
    else:
        quick_ids = {"q1", "q2", "q3", "q5", "q6", "q_loc"}
        questions = [q for q in CAREER_PERSONALITY_QUESTIONS if q.get("id") in quick_ids]
        questions.append(focus_question)

    public: list[dict[str, Any]] = []
    for q in questions:
        item = {k: v for k, v in q.items() if k not in ("low_weights", "high_weights")}
        if "options" in item:
            item["options"] = [{"value": o["value"], "label": o["label"]} for o in q["options"]]
        public.append(item)
    if not _is_technology_major(major_key):
        replacement = {
            ("q1", "build"): "动手把一个想法变成具体成果，并不断改进",
            ("q2", "coder"): "承担具体专业工作，把关键任务落实完成",
            ("q3", "solve"): "查明一个复杂问题，并提出有效解决办法",
            ("q6", "expert"): "在某个专业领域里成为靠谱、被信任的人",
        }
        for item in public:
            for option in item.get("options", []):
                option["label"] = replacement.get((item["id"], option["value"]), option["label"])
            if item["id"] == "q5":
                item["title"] = "比起频繁对外沟通，我更喜欢独立研究问题、资料或作品。"
            if item["id"] == "q10":
                item["placeholder"] = "例如：想把语言、设计或组织能力用在真实工作中；希望找到适合的实践机会。"
    return public


# ---------------------------------------------------------------------------
# AI prompts
# ---------------------------------------------------------------------------
def build_network_generation_prompt(major_name: str, research_digest: str = "") -> tuple[str, str]:
    system = (
        "你是高校职业探索内容编辑。只返回严格JSON，不写薪资、市场预测、录用或晋升保证，"
        "不替学生作个性化能力判断。平台负责统一阶段和展示文案，你只补充差异化方向与准备线索。"
        + build_time_context_text()
    )
    research_block = (
        "【联网检索参考：核对职业职责、学习路径与资格要求；不输出薪资、招聘数量或缺乏来源的地域市场结论】\n" + research_digest + "\n"
        if research_digest else ""
    )
    user = "\n\n".join([
        f"目标专业：{major_name}。提供适用于该专业的职业探索，培养阶段以学生真实学籍为准。",
        *( [research_block] if research_block else [] ),
        "提供3至4个类别和12个不重复的职业方向。类别id、方向tag、方向名称各自全局唯一；"
        "同一职责不要在多个类别重复。只覆盖有依据的探索方向，不声称穷尽所有就业出路。",
        '输出结构：{"cats":[{"id":"A","name":"类别名"}],"nodes":[{"tag":"A1","cat":"A",'
        '"name":"方向名","riasec":["S","A"],"lang":false,"pre":["准备线索"],"know":["实践任务"]}],'
        '"links":[["A1",1,"B1",1]]}。示例只示意字段，links只能引用实际存在的方向。',
        "每个方向pre恰好3项、know恰好3项，每项不超过25个汉字，写具体可验证的学习或实践任务。"
        "必要职业资格只是待核对线索，不假定学生已获得。职业方向不等于正在招聘的职位。",
        "riasec从R/I/A/S/E/C中选1至3项。lang为是否涉及外语或国际沟通。links最多8条，"
        "只表达有依据的相邻方向探索，阶段取0至3。",
        "不输出direction_id/desc/reason/trend/tl/branch/rec；这些字段由平台统一生成，平台 rec 固定为3，"
        "个人排序另按学生兴趣和能力证据计算。不要生成会被平台替换的阶段说明。",
        "尊重专业和学制差异；没有学生明确学历与资格证据时，不假定普通本科身份或满足招录条件。"
        "若该专业属于文科/管理/语言类，也要给出真实可行的就业大类（如内容/运营/教育/公共部门/国际化等），不要硬套理工方向。",
        "只返回一个 JSON 对象。",
    ])
    return system, user


# ---------------------------------------------------------------------------
# AI call
# ---------------------------------------------------------------------------
async def _call_career_ai(system_prompt: str, user_message: str, *, label: str, timeout: float = 240.0,
                          capability: str = "thinking") -> dict[str, Any]:
    if capability not in {"standard", "thinking"}:
        raise ValueError("Unsupported career model capability")
    response = await ai_client.post(
        "/api/ai/chat",
        json={
            "system_prompt": system_prompt,
            "messages": [],
            "new_message": user_message,
            "model_capability": capability,
            "task_type": "fast_text_response" if capability == "standard" else "deep_text_reasoning",
            "response_format": "json",
            "task_priority": "background",
            "task_label": label,
            "web_search_enabled": False,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        data = response.json()
    except (ValueError, TypeError):
        raise ValueError("AI 响应不是有效 JSON") from None
    if not isinstance(data, dict):
        raise ValueError("AI 响应必须是 JSON 对象")
    if data.get("status") != "success":
        raise RuntimeError(f"AI 返回失败: {str(data)[:300]}")
    payload = data.get("response_json")
    if not isinstance(payload, dict):
        payload = _extract_json_object(data.get("response_text"))
    if not isinstance(payload, dict):
        raise ValueError("AI 未返回 JSON 对象")
    return payload


# ---------------------------------------------------------------------------
# Compatibility keyword lookup
# ---------------------------------------------------------------------------


async def generate_keywords_on_demand(student_id: int, tag: str) -> dict[str, Any]:
    """Read existing safe keywords; a direction click never starts a model call."""
    with get_db_connection() as conn:
        ctx = resolve_student_context(conn, student_id)
        if not ctx:
            return {"ok": False, "error": "student_not_found"}
        net_state = get_or_prepare_network(conn, ctx)

    network = net_state.get("network") or {}
    node = next((n for n in network.get("nodes", []) if n.get("tag") == tag), None)
    if not node:
        return {"ok": False, "error": "node_not_found"}

    return {"ok": True, "tag": tag, "keywords": derive_job_keywords_from_node(node), "source": "baseline"}


# ---------------------------------------------------------------------------
# Retired scheduler handlers and public facade
# ---------------------------------------------------------------------------


# Public compatibility facade; lifecycle mutations live beside their durable adapters.
from .career_lifecycle_service import (
    _load_session_row, build_state, career_job_command, ensure_session,
    get_or_prepare_network, initialize_career, load_major_network_row,
    record_career_feedback, recover_career_jobs, reset_session,
    save_test_and_generate, save_test_progress, update_career_preferences,
)

async def handle_network_generation(task):
    return "superseded: career work is handled by durable AI jobs"

async def handle_personalization(task):
    return "superseded: career work is handled by durable AI jobs"

register_task_handler(NETWORK_GENERATE_TASK_KIND, handle_network_generation)
register_task_handler(PERSONALIZE_TASK_KIND, handle_personalization)

from .career_payload_service import (validate_network_payload as _validate_network_payload, validate_personalization_payload as _validate_personalization_payload)
