"""Readiness and build validation helpers for the student resume console."""

from __future__ import annotations

from typing import Any

from . import resume_document_service as docs
from . import resume_profile_service as profile


PERSONAL_LABELS = {
    "name": "姓名",
    "gender": "性别",
    "birthday": "生日",
    "email": "邮箱",
    "expected_position": "期望岗位",
}

SECTION_LABELS = {
    "self_intro": "自我介绍",
    "education": "学历",
    "experience": "经验",
    "skill": "技能",
    "certificate": "证书",
}


def _status(done: bool, warn: bool = False) -> str:
    if done:
        return "done"
    return "warn" if warn else "todo"


def _score(part: int, total: int, weight: int) -> int:
    if total <= 0:
        return 0
    return round(max(0, min(part, total)) / total * weight)


def _unresolved_conflicts(resumes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for resume in resumes:
        summary = resume.get("import_summary") if isinstance(resume.get("import_summary"), dict) else {}
        conflicts = summary.get("conflicts") if isinstance(summary.get("conflicts"), list) else []
        for index, conflict in enumerate(conflicts):
            if isinstance(conflict, dict) and not conflict.get("accepted"):
                rows.append({
                    "resume_id": resume.get("id"),
                    "resume_title": resume.get("title"),
                    "index": index,
                    "section": conflict.get("section"),
                    "field": conflict.get("field"),
                })
    return rows


def build_resume_readiness(conn, student_id: int) -> dict[str, Any]:
    """Return a compact, UI-ready picture of how resume-ready the student is."""
    personal = profile.get_personal_info(conn, student_id)
    sections = {
        key: profile.list_section(conn, student_id, key)
        for key in ("self_intro", "education", "experience", "skill", "certificate")
    }
    resumes = docs.list_resumes(conn, student_id)

    has_contact = any(str(personal.get(key) or "").strip() for key in profile.PERSONAL_CONTACT_FIELDS)
    required_total = len(profile.PERSONAL_REQUIRED) + 1
    required_filled = sum(1 for key in profile.PERSONAL_REQUIRED if str(personal.get(key) or "").strip())
    required_missing = [
        {"key": key, "label": PERSONAL_LABELS.get(key, key)}
        for key in profile.PERSONAL_REQUIRED
        if not str(personal.get(key) or "").strip()
    ]
    if has_contact:
        required_filled += 1
    else:
        required_missing.append({"key": "contact", "label": "邮箱或手机号"})

    has_target = bool(str(personal.get("expected_position") or "").strip())
    has_intro = bool(sections["self_intro"])
    has_education = bool(sections["education"])
    has_experience = bool(sections["experience"])
    skill_cert_count = len(sections["skill"]) + len(sections["certificate"])
    has_skill_cert = skill_cert_count > 0
    ready_resumes = [r for r in resumes if r.get("status") == "ready"]
    processing_resumes = [
        r for r in resumes
        if r.get("status") in {"rendering", "optimizing", "parsing"}
    ]
    conflicts = _unresolved_conflicts(resumes)

    score = 0
    score += _score(required_filled, required_total, 25)
    score += 10 if has_target else 0
    score += 15 if has_intro else 0
    score += 15 if has_education else 0
    score += 15 if has_experience else 0
    score += 15 if has_skill_cert else 0
    score += 5 if ready_resumes else 0
    score = max(0, min(score, 100))

    checks = [
        {
            "key": "personal",
            "label": "个人信息",
            "status": _status(not required_missing),
            "count": f"{required_filled}/{required_total}",
            "href": "/resume/profile/personal",
            "missing": required_missing,
        },
        {
            "key": "target",
            "label": "目标岗位",
            "status": _status(has_target),
            "count": "1/1" if has_target else "0/1",
            "href": "/resume/profile/personal",
        },
        {
            "key": "self_intro",
            "label": "自我介绍",
            "status": _status(has_intro),
            "count": str(len(sections["self_intro"])),
            "href": "/resume/profile/self-intro",
        },
        {
            "key": "education",
            "label": "学历",
            "status": _status(has_education),
            "count": str(len(sections["education"])),
            "href": "/resume/profile/education",
        },
        {
            "key": "experience",
            "label": "经验",
            "status": _status(has_experience),
            "count": str(len(sections["experience"])),
            "href": "/resume/profile/experience",
        },
        {
            "key": "skill_cert",
            "label": "技能证书",
            "status": _status(has_skill_cert),
            "count": str(skill_cert_count),
            "href": "/resume/profile/skill",
        },
        {
            "key": "resume",
            "label": "可投递简历",
            "status": _status(bool(ready_resumes), bool(processing_resumes)),
            "count": str(len(ready_resumes)),
            "href": "/resume/builder",
        },
    ]

    next_actions: list[dict[str, str]] = []
    if required_missing:
        labels = "、".join(item["label"] for item in required_missing[:3])
        next_actions.append({"label": f"补全{labels}", "href": "/resume/profile/personal", "kind": "personal"})
    if not has_intro:
        next_actions.append({"label": "生成一版自我介绍", "href": "/resume/profile/self-intro", "kind": "self_intro"})
    if not has_education:
        next_actions.append({"label": "补充学历", "href": "/resume/profile/education", "kind": "education"})
    if not has_experience:
        next_actions.append({"label": "补充项目或比赛经验", "href": "/resume/profile/experience", "kind": "experience"})
    if not has_skill_cert:
        next_actions.append({"label": "补充技能或证书", "href": "/resume/profile/skill", "kind": "skill"})
    if not ready_resumes and not processing_resumes:
        next_actions.append({"label": "创建目标岗位简历", "href": "/resume/builder", "kind": "resume"})
    if conflicts:
        next_actions.insert(0, {"label": "处理导入冲突", "href": "/resume/list", "kind": "conflict"})

    if score >= 85 and ready_resumes and not conflicts:
        level = "ready"
        message = "简历资料已基本完整，可以针对岗位继续微调。"
    elif score >= 60:
        level = "building"
        message = "简历骨架已经成型，继续补充关键经历会更有说服力。"
    else:
        level = "starter"
        message = "先补齐基础资料和一段可用经历，系统会帮你减少重复填写。"

    return {
        "score": score,
        "level": level,
        "message": message,
        "checks": checks,
        "next_actions": next_actions[:4],
        "counts": {
            "resumes": len(resumes),
            "ready_resumes": len(ready_resumes),
            "processing_resumes": len(processing_resumes),
            "unresolved_conflicts": len(conflicts),
            "sections": {key: len(value) for key, value in sections.items()},
        },
    }


def validate_resume_build(conn, student_id: int, *, target_position: str, layout: Any) -> dict[str, Any]:
    """Validate a resume build request before starting a render job."""
    normalized = docs.normalize_layout(layout)
    personal = profile.get_personal_info(conn, student_id)
    missing: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    if not str(target_position or "").strip():
        missing.append({"key": "target_position", "label": "目标岗位", "href": "/resume/profile/personal"})
    for key in profile.PERSONAL_REQUIRED:
        if not str(personal.get(key) or "").strip():
            missing.append({
                "key": f"personal.{key}",
                "label": PERSONAL_LABELS.get(key, key),
                "href": "/resume/profile/personal",
            })
    if not any(str(personal.get(key) or "").strip() for key in profile.PERSONAL_CONTACT_FIELDS):
        missing.append({
            "key": "personal.contact",
            "label": "邮箱或手机号",
            "href": "/resume/profile/personal",
        })

    blocks = normalized.get("blocks") if isinstance(normalized.get("blocks"), list) else []
    evidence_blocks = [block for block in blocks if block.get("type") != "tech_stack"]
    if not evidence_blocks:
        missing.append({"key": "content", "label": "至少一个可展示内容区", "href": "/resume/builder"})

    invalid = _find_invalid_layout_refs(conn, student_id, normalized)
    if invalid:
        missing.append({"key": "selection", "label": "已选择内容已失效，请刷新后重选", "href": "/resume/builder"})

    if len(evidence_blocks) == 1:
        warnings.append({"key": "thin_content", "label": "内容偏少，建议再加入项目、学历或技能证书。"})

    return {
        "ok": not missing,
        "missing": missing,
        "warnings": warnings,
        "layout": normalized,
    }


def _find_invalid_layout_refs(conn, student_id: int, layout: dict[str, Any]) -> list[dict[str, Any]]:
    invalid: list[dict[str, Any]] = []
    table_map = {
        "self_intro": "resume_self_intros",
        "education": "resume_educations",
        "experience": "resume_experiences",
        "skill": "resume_skills",
        "certificate": "resume_certificates",
    }
    blocks = layout.get("blocks") if isinstance(layout.get("blocks"), list) else []
    for block in blocks:
        btype = block.get("type")
        if btype in {"self_intro", "education", "experience"}:
            ids = [int(x) for x in block.get("ids") or []]
            invalid.extend(_missing_ids(conn, student_id, table_map[btype], ids, btype))
        elif btype == "skill_cert":
            invalid.extend(_missing_ids(conn, student_id, table_map["skill"], block.get("skill_ids") or [], "skill"))
            invalid.extend(_missing_ids(conn, student_id, table_map["certificate"], block.get("cert_ids") or [], "certificate"))
    return invalid


def _missing_ids(conn, student_id: int, table: str, ids: list[Any], section: str) -> list[dict[str, Any]]:
    clean_ids = [int(x) for x in ids if str(x).strip().lstrip("-").isdigit()]
    if not clean_ids:
        return []
    placeholders = ", ".join("?" for _ in clean_ids)
    rows = conn.execute(
        f"SELECT id FROM {table} WHERE student_id = ? AND id IN ({placeholders})",
        [int(student_id), *clean_ids],
    ).fetchall()
    found = {int(row["id"]) for row in rows}
    return [{"section": section, "id": item_id} for item_id in clean_ids if item_id not in found]
