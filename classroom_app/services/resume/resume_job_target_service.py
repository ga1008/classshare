"""Explainable, student-owned job-description analysis for the resume console.

The first pass is deterministic and fast: it extracts explicit requirements,
maps common skills/capabilities, checks them against the student's existing
profile, and explains every gap.  It never invents experience or claims a
student has a skill merely because the job asks for it.
"""

from __future__ import annotations

import json
import hashlib
from functools import lru_cache
from copy import deepcopy
import re
from datetime import datetime, timezone
from typing import Any

from ...db.connection import execute_insert_returning_id
from ...db.schema_resume import ensure_resume_schema
from . import resume_profile_service as profile
from .resume_requirement_service import evaluate_hard_requirements

MAX_DESCRIPTION_LENGTH = 15_000
MAX_TARGETS_PER_STUDENT = 30

_REQUIREMENT_SIGNAL = re.compile(
    r"要求|负责|职责|熟悉|掌握|具备|能够|能力|经验|优先|加分|本科|专业|语言|证书|"
    r"requirements?|responsibilit|proficien|experience|preferred|qualification",
    re.I,
)
_NICE_SIGNAL = re.compile(r"优先|加分|更佳|preferred|plus|nice\s+to\s+have", re.I)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|个|项|人|次|万|千|天|周|月|年|元|篇|家|套)?")

# Canonical label -> aliases likely to occur in job descriptions/profile data.
_CAPABILITY_ALIASES: dict[str, tuple[str, ...]] = {
    "Python": ("python",),
    "Java": ("java",),
    "JavaScript": ("javascript", "js"),
    "TypeScript": ("typescript", "ts"),
    "SQL": ("sql",),
    "Excel": ("excel",),
    "Power BI": ("power bi", "powerbi"),
    "Tableau": ("tableau",),
    "Linux": ("linux",),
    "Git": ("git",),
    "Docker": ("docker",),
    "React": ("react",),
    "Vue": ("vue", "vue.js", "vuejs"),
    "FastAPI": ("fastapi",),
    "Django": ("django",),
    "Flask": ("flask",),
    "Spring": ("spring", "spring boot", "springboot"),
    "数据分析": ("数据分析", "data analysis", "数据处理"),
    "数据可视化": ("数据可视化", "visualization", "可视化"),
    "用户研究": ("用户研究", "用户调研", "user research"),
    "市场调研": ("市场调研", "市场研究", "market research"),
    "项目管理": ("项目管理", "项目推进", "project management"),
    "产品设计": ("产品设计", "产品策划", "需求分析", "原型设计"),
    "内容运营": ("内容运营", "新媒体运营", "社媒运营", "content operation"),
    "文案写作": ("文案", "写作", "copywriting"),
    "商务沟通": ("商务沟通", "商务谈判", "客户沟通", "business communication"),
    "团队协作": ("团队协作", "跨团队", "协作能力", "teamwork"),
    "英语": ("英语", "english", "cet-4", "cet4", "cet-6", "cet6", "雅思", "托福"),
    "日语": ("日语", "japanese", "jlpt"),
    "泰语": ("泰语", "thai"),
    "教师资格": ("教师资格", "教资"),
    "翻译": ("翻译", "translation", "interpreting", "口译", "笔译"),
    "教学设计": ("教学设计", "教案", "lesson planning"),
    "课堂组织": ("课堂管理", "课堂组织", "classroom management"),
    "跨境电商": ("跨境电商", "亚马逊运营", "amazon seller"),
    "客户服务": ("客户服务", "客服", "customer service"),
    "会计核算": ("会计核算", "财务报表", "accounting"),
    "审计": ("审计", "auditing"),
    "视觉设计": ("视觉设计", "平面设计", "graphic design"),
    "Photoshop": ("photoshop",),
    "Illustrator": ("illustrator",),
    "视频剪辑": ("视频剪辑", "premiere", "final cut"),
    "旅游服务": ("旅游服务", "导游", "tour guide"),
    "酒店运营": ("酒店运营", "酒店管理", "hospitality"),
    "物流管理": ("物流管理", "供应链", "supply chain"),
    "人力资源": ("人力资源", "招聘管理", "human resources"),
    "调查研究": ("问卷设计", "访谈", "调查研究", "survey research"),
}

_NEGATIVE = re.compile(r"不会|未掌握|不熟悉|不具备|没有.{0,8}经验|未接触|尚未|待学习|计划学习|希望学习|"
                       r"\b(?:never|no experience|not proficient|unfamiliar|want to learn|plan to learn)\b", re.I)


def _supports(text: str, aliases: Any) -> bool:
    # A stated intention or negation is not evidence of possession. Preserve the
    # conservative unknown state rather than adding the requested skill.
    for sentence in re.split(r"[\n。；;.!?]+", text):
        if any(_contains(sentence, alias) for alias in aliases) and not _NEGATIVE.search(sentence):
            return True
    return False


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _clean(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _contains(text: str, alias: str) -> bool:
    haystack = text.casefold()
    needle = alias.casefold()
    if re.fullmatch(r"[a-z0-9+#.\- ]+", needle):
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None
    return needle in haystack


@lru_cache(maxsize=256)
def _extract_requirements(description: str) -> tuple[list[str], list[str]]:
    chunks = re.split(r"[\r\n]+|(?<=[。；;])", description)
    candidates: list[str] = []
    for chunk in chunks:
        line = re.sub(r"^[\s\-–—•·*\d.、（）()]+", "", chunk).strip(" \t。；;")
        if 5 <= len(line) <= 180 and _REQUIREMENT_SIGNAL.search(line):
            candidates.append(line)
    if not candidates:
        candidates = [
            re.sub(r"\s+", " ", item).strip(" \t。；;")
            for item in re.split(r"[。；;]", description)
            if 8 <= len(item.strip()) <= 180
        ][:8]
    seen: set[str] = set()
    must: list[str] = []
    nice: list[str] = []
    for item in candidates:
        key = re.sub(r"\s+", "", item).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        (nice if _NICE_SIGNAL.search(item) else must).append(item)
        if len(must) + len(nice) >= 12:
            break
    return must, nice


def _profile_text_and_evidence(bundle: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    evidence: list[dict[str, str]] = []
    labels = {
        "education": "学历",
        "experience": "经历",
        "skill": "技能",
        "certificate": "证书",
    }
    allowed_fields = {
        "education": ("school", "college", "major", "content"),
        "experience": ("title", "role", "content", "contribution", "achievement"),
        "skill": ("name", "level", "description"),
        "certificate": ("name", "description"),
    }
    for section, fields in allowed_fields.items():
        for item in bundle.get(section) or []:
            if not isinstance(item, dict):
                continue
            if section == "certificate" and str(item.get("expiry_date") or "").strip() and str(item["expiry_date"])[:7] < datetime.now().strftime("%Y-%m"):
                continue
            text = " ".join(_clean(item.get(field), 2_000) for field in fields if item.get(field)).strip()
            if not text:
                continue
            label = _clean(item.get("title") or item.get("name") or item.get("school") or labels[section], 100)
            evidence.append({"source": labels[section], "section": section, "item_id": item.get("id"), "label": label, "text": text,
                             "revision": item.get("revision", 1), "evidence_level": "self_reported"})
    return "\n".join(item["text"] for item in evidence), evidence


@lru_cache(maxsize=256)
def _extract_capabilities(description: str) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    for name, aliases in _CAPABILITY_ALIASES.items():
        if any(_contains(description, alias) for alias in aliases):
            lines = [line for line in re.split(r"[\r\n。；;]+", description) if any(_contains(line, alias) for alias in aliases)]
            importance = "preferred" if lines and all(_NICE_SIGNAL.search(line) for line in lines) else "required"
            capabilities.append({"name": name, "aliases": aliases, "importance": importance})
    return capabilities


def _experience_feedback(bundle: dict[str, Any], capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    feedback: list[dict[str, Any]] = []
    for item in (bundle.get("experience") or [])[:8]:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get(key) or "") for key in ("title", "role", "content", "contribution", "achievement"))
        supported = [
            capability["name"]
            for capability in capabilities
            if _supports(text, capability.get("aliases", ()))
        ]
        suggestions: list[str] = []
        if not _clean(item.get("role"), 200):
            suggestions.append("补充你在这段经历中的具体角色和责任边界。")
        if not _clean(item.get("contribution"), 2_000):
            suggestions.append("写清你亲自采取了哪些行动，避免只描述团队做了什么。")
        if not _clean(item.get("achievement"), 2_000):
            suggestions.append("补充真实结果：交付物、效率变化、覆盖人数或反馈均可。")
        elif not _NUMBER_RE.search(str(item.get("achievement") or "")):
            suggestions.append("如有可靠数据，可用人数、次数、时长或比例让结果更具体；没有就不要编造。")
        if capabilities and not supported:
            suggestions.append("说明这段经历与目标岗位哪一项要求相关；若确实无关，可不放进定向简历。")
        feedback.append({
            "experience_id": int(item.get("id") or 0),
            "title": _clean(item.get("title") or "未命名经历", 100),
            "supported_capabilities": supported[:6],
            "suggestions": suggestions[:4],
        })
    return feedback


def analyze_job_description(bundle: dict[str, Any], description: str) -> dict[str, Any]:
    description = _clean(description, MAX_DESCRIPTION_LENGTH)
    if len(description) < 30:
        raise ValueError("岗位描述太短，请粘贴职责、要求或任职条件后再分析")
    must_have, nice_to_have = deepcopy(_extract_requirements(description))
    profile_text, evidence_items = _profile_text_and_evidence(bundle)
    capabilities = _extract_capabilities(description)
    results: list[dict[str, Any]] = []
    for capability in capabilities:
        aliases = capability.get("aliases", ())
        matched_evidence = [
            {key: item.get(key) for key in ("source", "label", "section", "item_id", "revision", "evidence_level")}
            for item in evidence_items
            if _supports(item["text"], aliases)
        ][:3]
        results.append({
            "name": capability["name"],
            "importance": capability["importance"],
            "matched": bool(matched_evidence),
            "state": "evidence_present" if matched_evidence else "unknown",
            "evidence": matched_evidence,
        })
    required = [item for item in results if item["importance"] == "required"]
    denominator = len(required) or len(results)
    matched_count = sum(1 for item in (required or results) if item["matched"])
    score = round(matched_count / denominator * 100) if denominator else 0
    gaps = [
        {
            "name": item["name"],
            "importance": item["importance"],
            "suggestion": (
                "如果你确实做过，请补充一段可验证的项目、课程或实践证据；"
                "如果没有，把它加入学习计划，不要直接写成已掌握。"
            ),
        }
        for item in results if not item["matched"]
    ]
    summary = (
        f"识别出 {len(must_have)} 项核心要求和 {len(nice_to_have)} 项加分要求；"
        f"现有资料对 {matched_count}/{denominator} 项可识别能力提供了证据。"
        if denominator else
        f"识别出 {len(must_have)} 项核心要求和 {len(nice_to_have)} 项加分要求，"
        "但描述中的能力词较少，建议结合具体岗位人工确认。"
    )
    return {
        "coverage_score": score,
        "coverage_status": "partial" if denominator else "insufficient_extraction",
        "analysis_version": "evidence-v2",
        "hard_requirements": evaluate_hard_requirements(bundle, description),
        "summary": summary,
        "must_have": must_have,
        "nice_to_have": nice_to_have,
        "capabilities": results,
        "gaps": gaps,
        "experience_feedback": _experience_feedback(bundle, capabilities),
        "profile_evidence_count": len(evidence_items),
        "disclaimer": "资料覆盖度只反映当前已填写内容，不代表录用概率；系统不会替你编造经历或能力。",
    }




def create_job_target(
    conn: Any,
    student_id: int,
    *,
    target_position: Any,
    company_name: Any = "",
    job_description: Any,
) -> dict[str, Any]:
    ensure_resume_schema(conn)
    position = _clean(target_position, 100)
    if not position:
        raise ValueError("请填写目标岗位名称")
    description = _clean(job_description, MAX_DESCRIPTION_LENGTH)
    bundle = profile.collect_profile_bundle(conn, int(student_id))
    analysis = analyze_job_description(bundle, description)
    now = _now()
    digest = hashlib.sha256(json.dumps([position, _clean(company_name, 100), description], ensure_ascii=False).encode()).hexdigest()
    target_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO resume_job_targets
            (student_id, target_position, company_name, job_description,
             analysis_json, coverage_score, status, created_at, updated_at, description_hash)
        VALUES (?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?)
        ON CONFLICT(student_id,description_hash) DO UPDATE SET
          analysis_json=excluded.analysis_json, coverage_score=excluded.coverage_score,
          updated_at=excluded.updated_at, archived=0, revision=resume_job_targets.revision+1
        """,
        (
            int(student_id), position, _clean(company_name, 100), description,
            json.dumps(analysis, ensure_ascii=False), int(analysis["coverage_score"]), now, now, digest,
        ),
    )
    # Keep storage bounded for students who paste many variants.  The newest
    # 30 remain available; old target rows are independent of generated resumes
    # because resumes retain their target name and safe source context.
    target_id = int(conn.execute("SELECT id FROM resume_job_targets WHERE student_id = ? AND description_hash = ?", (int(student_id), digest)).fetchone()["id"])
    conn.execute(
        """
        UPDATE resume_job_targets SET archived = 1
        WHERE student_id = ? AND id NOT IN (
            SELECT id FROM resume_job_targets
            WHERE student_id = ? ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, updated_at DESC, id DESC LIMIT ?
        )
        """,
        (int(student_id), int(student_id), target_id, MAX_TARGETS_PER_STUDENT),
    )
    return get_job_target(conn, student_id, target_id, include_description=True)


def _parse_row(row: Any, *, include_description: bool) -> dict[str, Any]:
    item = dict(row)
    try:
        item["analysis"] = json.loads(item.pop("analysis_json", "{}") or "{}")
    except (TypeError, ValueError):
        item["analysis"] = {}
    item["coverage_status"] = item["analysis"].get("coverage_status", "partial")
    if not include_description:
        item.pop("job_description", None)
    return item


def list_job_targets(conn: Any, student_id: int, *, limit: int = 12) -> list[dict[str, Any]]:
    ensure_resume_schema(conn)
    rows = conn.execute(
        """
        SELECT id, student_id, target_position, company_name, job_description,
               analysis_json, coverage_score, status, error_text, revision, archived, created_at, updated_at
        FROM resume_job_targets
        WHERE student_id = ? AND archived = 0
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (int(student_id), max(1, min(30, int(limit)))),
    ).fetchall()
    return [_parse_row(row, include_description=False) for row in rows]


def get_job_target(conn: Any, student_id: int, target_id: int, *, include_description: bool = True) -> dict[str, Any]:
    ensure_resume_schema(conn)
    row = conn.execute(
        """
        SELECT id, student_id, target_position, company_name, job_description,
               analysis_json, coverage_score, status, error_text, revision, archived, created_at, updated_at
        FROM resume_job_targets
        WHERE id = ? AND student_id = ? LIMIT 1
        """,
        (int(target_id), int(student_id)),
    ).fetchone()
    if row is None:
        raise LookupError("岗位分析不存在或无权访问")
    return _parse_row(row, include_description=include_description)


def delete_job_target(conn: Any, student_id: int, target_id: int) -> None:
    ensure_resume_schema(conn)
    get_job_target(conn, student_id, target_id, include_description=False)
    conn.execute(
        "UPDATE resume_job_targets SET archived = 1, revision = revision + 1 WHERE id = ? AND student_id = ?",
        (int(target_id), int(student_id)),
    )
