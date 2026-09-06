"""Bounded, explainable career exploration from explicit student evidence.

This is an exploration score, never a hiring probability or eligibility ruling.
No names, gender, contact details or psychological support records are inputs.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

SCORER_VERSION = "career-evidence-v3"
CATALOG_VERSION = "career-catalog-v3"

# Curated exploration directions, not claims that a vacancy currently exists.
# Each tuple names a direction, interest dimensions, and concrete evidence to prepare.
FAMILY_DIRECTIONS = {
    "language": [
        ("翻译与本地化", "AIC", ["翻译", "校对", "术语管理"]),
        ("国际商务与跨境服务", "ESC", ["外语", "商务沟通", "跨文化沟通"]),
        ("语言教学与培训", "SAC", ["教学设计", "语言表达", "教师资格"]),
        ("国际内容与传播", "AES", ["内容写作", "外语", "新媒体"]),
        ("外事与会展服务", "SEC", ["活动组织", "外语", "协调沟通"]),
        ("语言研究与继续深造", "IAC", ["文献检索", "研究方法", "学术写作"]),
    ],
    "business": [
        ("业务运营", "ECI", ["运营分析", "Excel", "流程管理"]),
        ("市场与品牌", "EAI", ["市场调研", "文案写作", "数据分析"]),
        ("人力资源服务", "SEC", ["沟通", "招聘流程", "劳动关系"]),
        ("财务与会计支持", "CIR", ["会计", "Excel", "财务分析"]),
        ("供应链与采购", "CEI", ["采购", "库存管理", "数据分析"]),
        ("客户成功与商务", "ESI", ["商务沟通", "需求分析", "客户服务"]),
    ],
    "education": [
        ("学科教学", "SAC", ["教学设计", "学科知识", "教师资格"]),
        ("课程与学习资源设计", "SAI", ["课程设计", "内容写作", "学习评价"]),
        ("教育项目运营", "SEC", ["项目管理", "沟通", "活动组织"]),
        ("学习支持与学生服务", "SCI", ["学习支持", "沟通", "记录整理"]),
        ("教育内容编辑", "ACI", ["编辑", "校对", "课程设计"]),
        ("教育研究与深造", "ISC", ["研究方法", "数据分析", "学术写作"]),
    ],
    "design": [
        ("视觉与品牌设计", "AEI", ["视觉设计", "排版", "作品集"]),
        ("交互与用户体验", "AIS", ["交互设计", "用户研究", "原型设计"]),
        ("数字内容制作", "ARI", ["视频制作", "剪辑", "作品集"]),
        ("文化活动与策展", "AES", ["策划", "活动组织", "文化研究"]),
        ("创意内容与传播", "AES", ["内容写作", "新媒体", "视觉表达"]),
        ("艺术教育与服务", "SAC", ["教学设计", "艺术实践", "沟通"]),
    ],
    "health": [
        ("专业临床与护理路径", "SRI", ["专业资格", "临床实践", "记录规范"]),
        ("健康管理服务", "SCI", ["健康教育", "沟通", "数据记录"]),
        ("康复与社区支持", "SRI", ["专业资格", "服务实践", "沟通"]),
        ("医药与健康产品支持", "ESI", ["产品知识", "商务沟通", "合规意识"]),
        ("健康内容与科普", "SAI", ["文献检索", "内容写作", "科学传播"]),
        ("专业研究与深造", "ISC", ["研究方法", "文献检索", "数据分析"]),
    ],
    "technology": [
        ("软件开发", "IRC", ["编程", "数据库", "项目实践"]),
        ("数据分析", "ICR", ["Python", "SQL", "数据分析"]),
        ("网络与信息安全", "RIC", ["网络", "Linux", "安全实践"]),
        ("系统实施与技术支持", "RSC", ["系统部署", "问题排查", "沟通"]),
        ("产品与技术运营", "EIS", ["需求分析", "产品设计", "数据分析"]),
        ("技术研究与深造", "IRC", ["数学", "文献检索", "研究方法"]),
    ],
    "general": [
        ("专业实践与行业服务", "RSI", ["专业知识", "实践成果", "服务意识"]),
        ("内容与信息整理", "ACI", ["内容写作", "信息整理", "校对"]),
        ("组织与项目支持", "ECS", ["项目管理", "活动组织", "协调沟通"]),
        ("研究与数据支持", "ICR", ["文献检索", "Excel", "数据分析"]),
        ("公共与客户服务", "SEC", ["沟通", "客户服务", "记录规范"]),
        ("专业进修与继续深造", "ICA", ["学习规划", "研究方法", "专业知识"]),
    ],
}


def payload_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def major_family(major_name: str) -> str:
    text = str(major_name or "")
    families = (
        ("language", ("英语", "日语", "泰语", "越南语", "外语", "翻译", "法语", "德语", "西班牙语")),
        ("education", ("教育", "师范", "学前")),
        ("design", ("设计", "艺术", "美术", "音乐", "舞蹈", "传媒", "广播")),
        ("health", ("护理", "医学", "药学", "康复", "卫生")),
        ("business", ("管理", "经济", "金融", "会计", "商务", "营销", "物流", "财务")),
        ("technology", ("软件", "计算机", "网络", "人工智能", "数据科学", "信息安全", "电子", "自动化")),
    )
    return next((family for family, tokens in families if any(x in text for x in tokens)), "general")


def baseline_network(major_name: str) -> dict[str, Any]:
    family = major_family(major_name)
    nodes = []
    for index, (name, interests, skills) in enumerate(FAMILY_DIRECTIONS[family], 1):
        tag = f"B{index}"
        nodes.append({
            "tag": tag, "direction_id": f"{family}-{index}", "cat": "B", "name": name,
            "rec": 3, "lang": family == "language", "riasec": list(interests),
            "desc": f"可结合{major_name or '所学专业'}和个人实践进一步了解的职业方向。",
            "reason": "先了解实际工作内容，再用项目、实习或作品验证兴趣与能力。",
            "pre": skills, "know": ["了解目标岗位的真实职责", "准备可展示的实践证据", "核对招聘条件与专业资格"],
            "tl": [["准备阶段", "了解与体验", "访谈、课程和小实践"],
                   ["入门阶段", "实习与初级工作", "根据真实岗位条件补足证据"],
                   ["发展阶段", "独立承担职责", "通过持续实践提升专业能力"],
                   ["进阶阶段", "专长与协作", "结合个人选择探索专业深度或团队协作"]],
            "branch": "根据已积累的能力探索相邻方向，核对转向要求。",
            "trend": "此为通用探索框架，具体招聘需求和资格请核对当前真实岗位。",
        })
    return {"major_name": major_name or "专业待确认", "graduate_label": "职业探索",
            "intro": "基础职业探索已可使用；专业网络可在后台进一步完善。这里的方向不代表正在招聘的职位。",
            "cats": [{"id": "B", "name": "可探索的方向", "desc": "从事实与实践开始", "icon": "🧭",
                      "c1": "#6ee7ff", "c2": "#3b82f6"}], "nodes": nodes, "links": [],
            "schema_version": CATALOG_VERSION, "market_data_verified": False}


def load_evidence_snapshot(conn: Any, student_id: int) -> dict[str, Any]:
    """Only command paths read material rows; polling uses the saved snapshot."""
    sections = {
        "skill": ("resume_skills", ("name", "level", "description")),
        "certificate": ("resume_certificates", ("name", "description")),
        "experience": ("resume_experiences", ("title", "role", "content", "contribution", "achievement")),
        "education": ("resume_educations", ("major", "content")),
    }
    evidence = []
    for section, (table, fields) in sections.items():
        validity = ", expiry_date" if section == "certificate" else ""
        rows = conn.execute(f"SELECT id, {', '.join(fields)}, updated_at{validity} FROM {table} "
                            "WHERE student_id = ? ORDER BY id DESC LIMIT 40", (student_id,)).fetchall()
        for row in rows:
            item = dict(row)
            text = "；".join(str(item.get(field) or "")[:1200] for field in fields).strip("；")
            if text:
                entry={"section": section, "id": item["id"], "text": text[:2400],
                       "source": "学生维护的材料", "updated_at": str(item.get("updated_at") or "")}
                if section=="certificate":entry["expiry_date"]=str(item.get("expiry_date") or "")[:10]
                evidence.append(entry)
    # A target position is a preference, never evidence that a skill is possessed.
    row = conn.execute("SELECT expected_position, expected_industry FROM resume_personal_info "
                       "WHERE student_id = ? LIMIT 1", (student_id,)).fetchone()
    return {"evidence": evidence, "intent": dict(row) if row else {}}


def validate_preferences(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("职业偏好必须是对象")
    allowed = {"city", "cities", "target_positions", "notes", "work_mode", "goal", "location_pref"}
    if set(raw) - allowed:
        raise ValueError("包含不支持的职业偏好字段")
    result = {}
    for key, value in raw.items():
        if key in ("cities", "target_positions"):
            if not isinstance(value, list) or len(value) > 8 or any(not isinstance(x, str) or len(x) > 80 for x in value):
                raise ValueError("城市或岗位偏好格式不正确")
            result[key] = list(dict.fromkeys(x.strip() for x in value if x.strip()))
            continue
        if key == "notes":
            if not isinstance(value, str) or len(value) > 500:
                raise ValueError("偏好补充请控制在500字以内")
            result[key] = value.strip()
            continue
        if not isinstance(value, str) or len(value) > 80:
            raise ValueError("职业偏好格式或长度不正确")
        result[key] = value.strip()
    if result.get("cities") and not result.get("city"):
        result["city"] = result["cities"][0]
    if result.get("work_mode", "") not in ("", "onsite", "remote", "hybrid", "flexible"):
        raise ValueError("未知的工作方式")
    if result.get("goal", "") not in ("", "internship", "employment", "further_study", "explore"):
        raise ValueError("未知的职业目标")
    return result


def _positive_match(text: str, term: str) -> bool:
    pattern = re.escape(term)
    if re.fullmatch(r"[A-Za-z0-9+#. -]+", term):
        pattern = rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])"
    for match in re.finditer(pattern, text, re.I):
        prefix = text[max(0, match.start() - 14):match.start()]
        if not re.search(r"不会|不熟悉|未掌握|不掌握|没有|缺乏|尚未|待学习|想学习|希望学习|not\s+|no\s+", prefix, re.I):
            return True
    return False


def recommend(network: dict[str, Any], *, test_result: dict[str, Any],
              evidence: dict[str, Any], preferences: dict[str, Any],
              feedback: dict[str, Any], timeline: dict[str, Any], evaluation_month: str | None = None) -> dict[str, Any]:
    scores = test_result.get("scores") or {}
    month=evaluation_month or datetime.now().strftime("%Y-%m")
    records = [item for item in (evidence.get("evidence") or [])
               if not (item.get("section")=="certificate" and item.get("expiry_date") and str(item["expiry_date"])[:7]<month)]
    ranked = []
    for node in network.get("nodes", []):
        tag = node["tag"]
        dims = node.get("riasec") or []
        interest = sum(float(scores.get(dim) or 0) for dim in dims) / max(1, len(dims))
        requirements = [str(value) for value in node.get("pre", [])][:8]
        hits = []
        gaps = []
        for requirement in requirements:
            # Exact named requirement or distinctive words; a mention stays self-reported evidence.
            terms = [x for x in re.split(r"[/、,，()（）:：\s]+", requirement) if len(x) >= 2]
            matched = next((r for r in records if any(_positive_match(r["text"], x) for x in terms)), None)
            if matched:
                hits.append({"requirement": requirement, "section": matched["section"],
                             "evidence_id": matched["id"], "status": "self_reported"})
            else:
                gaps.append(requirement)
        coverage = len(hits) / max(1, len(requirements))
        interest_weight = .60 if any(scores.values()) else 0
        value = 30 + interest_weight * interest + 25 * coverage
        signals = []
        action = feedback.get(str(node.get("direction_id") or tag)) or feedback.get(tag)
        if action == "saved":
            value += 10
            signals.append("你收藏了这个方向")
        elif action == "dismissed":
            value -= 35
            signals.append("已降低你标记不感兴趣的方向")
        if preferences.get("goal") == "further_study" and re.search(r"深造|研究|进修", node["name"]):
            value += 15
            signals.append("与你的继续深造目标相关")
        if any(_positive_match(node["name"], target) or _positive_match(target, node["name"])
               for target in preferences.get("target_positions", []) if len(target) >= 2):
            value += 10
            signals.append("与你填写的目标岗位相关，意向不计入能力证据")
        city = preferences.get("city") or ""
        # Only use explicit, source-backed region tags. Never infer a market from a city name.
        verified_regions = node.get("verified_regions") or []
        if city and city in verified_regions:
            value += 8
            signals.append("已有资料覆盖你选择的城市")
        confidence = "partial" if hits else "insufficient"
        months = timeline.get("months_to_graduation")
        qualification_gap = any(re.search(r"资格|执照|执业", requirement) for requirement in gaps)
        preparation_cost = len(gaps) + (2 if qualification_gap else 0)
        horizon = "validate_now" if coverage >= .5 and not qualification_gap else "prepare"
        if qualification_gap or re.search(r"深造|研究与|进修", node["name"]):
            horizon = "long_term"
        # Approaching graduation changes preparation priorities, never eligibility.
        # Missing evidence remains unknown rather than a statement of inability.
        if months is not None and months <= 6 and preferences.get("goal") != "further_study":
            value -= min(18, preparation_cost * 2)
            if horizon == "long_term":
                value -= 8
            signals.append("临近毕业，优先考虑证据较充分、准备步骤较少的方向；长期方向仍保留")
        elif months is not None and months > 18 and horizon == "long_term":
            value += 4
            signals.append("准备时间较充分，可用小实践验证长期方向")
        why = f"兴趣参考 {round(interest)}；现有材料关联 {len(hits)}/{len(requirements)} 项准备要求。"
        if not any(scores.values()):
            why = f"尚无完整兴趣作答；现有材料关联 {len(hits)}/{len(requirements)} 项准备要求。"
        if signals:
            why += "；".join(signals) + "。"
        ranked.append({"tag": tag, "direction_id": node.get("direction_id", tag), "name": node["name"],
                       "score": round(max(0, min(100, value)), 2), "interest_score": round(interest),
                       "evidence_coverage": round(coverage, 3), "evidence": hits, "gaps": gaps,
                       "preparation_cost": preparation_cost, "horizon": horizon,
                       "confidence": confidence, "why": why, "market_status": "unknown"})
    ranked.sort(key=lambda x: (-x["score"], x["direction_id"]))
    top = ranked[:4]
    months = timeline.get("months_to_graduation")
    if months is None:
        advice = "先确认预计毕业时间，再选一个方向完成小实践并记录成果。"
    elif months <= 6:
        advice = "优先核对当前岗位要求、整理已有成果与简历，再补最关键的能力缺口。"
    else:
        advice = f"距预计毕业约 {months} 个月：先用课程、小项目或实习验证一个方向，再逐步积累证据。"
    city_label = preferences.get("city") or test_result.get("location_label") or ""
    return {"greeting": "从你的兴趣与实践出发", "summary": "以下是可解释的职业探索建议，材料命中不代表熟练度或招聘资格已通过。",
            "region_note": (f"已记录地域意向：{city_label}。具体机会与资格需要核对真实岗位。" if city_label else "可以补充目标城市，以便核对真实岗位。"),
            "timeline_advice": advice, "holland_code": test_result.get("holland_code") or "",
            "top_paths": [{k: item[k] for k in ("tag", "name", "why")} for item in top],
            "highlights": [x["tag"] for x in top], "rec_overrides": {x["tag"]: max(1, min(5, round(x["score"] / 20))) for x in ranked},
            "dim_glow": {x["tag"]: max(.15, x["score"] / 100) for x in ranked},
            "node_tips": {x["tag"]: x["why"] for x in ranked}, "rankings": ranked,
            "prep_cards": {}, "job_keywords": {}, "scorer_version": SCORER_VERSION, "source": "baseline"}
