"""Conservative qualification checks, separate from keyword coverage.

Unknown information is never a pass or a rejection. Facts are student-reported
unless their evidence source says otherwise; missing material is not proof that
the student lacks a qualification.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

DEGREES = {"高中": 1, "中专": 1, "大专": 2, "专科": 2, "本科": 3, "学士": 3, "硕士": 4, "博士": 5}
_CERTIFICATES = re.compile(r"教师资格(?:证)?|导游证|会计(?:专业技术)?资格(?:证)?|护士执业(?:资格)?证|法律职业资格(?:证)?|"
                           r"CET[- ]?[46]|英语[四六]级|JLPT[- ]?N[1-5]|雅思|托福|IELTS|TOEFL(?:\s*iBT)?|TOEIC", re.I)
_NICE = re.compile(r"优先|加分|preferred|nice.to.have", re.I)
_ALTERNATIVE = re.compile(r"或者|或|任选|任一|之一|one of|\bor\b", re.I)
_SCORE = re.compile(r"\d+(?:\.\d+)?\s*(?:分|points?|score)|"
                    r"(?:雅思|托福|IELTS|TOEFL(?:\s*iBT)?|TOEIC|CET[- ]?[46]|英语[四六]级)"
                    r"(?:\s|成绩|总分|分数|达到|至少|不低于|不少于|最低|≥|>=|[（(:：]){0,8}\d", re.I)


def _reference(item: dict[str, Any], section: str) -> dict[str, Any]:
    return {"section": section, "item_id": item.get("id"), "revision": item.get("revision", 1), "evidence_level": "self_reported"}


def _key(text: Any) -> str:
    return re.sub(r"[\s\-证]+", "", str(text or "")).casefold()


def _degree_level(value: Any) -> int | None:
    levels = [level for label, level in DEGREES.items() if label in str(value or "")]
    return max(levels) if levels else None


def _graduation_year(item: dict[str, Any]) -> int | None:
    value = str(item.get("end_date") or "")
    try:
        return date.fromisoformat(value+"-01" if len(value)==7 else value).year
    except ValueError:
        return None


def _work_months(items: list[dict[str, Any]], *, internships: bool, today: date) -> tuple[int, list[dict[str, Any]]]:
    occupied: set[int] = set()
    evidence = []
    for item in items:
        if item.get("kind") not in ({"internship", "work", "employment"} if internships else {"work", "employment"}):
            continue
        start, end = str(item.get("start_date") or ""), str(item.get("end_date") or "")
        if end.casefold() in {"至今", "present", "current"}:
            end = today.strftime("%Y-%m")
        if not re.fullmatch(r"\d{4}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}", end):
            continue
        sy, sm = map(int, start.split("-")); ey, em = map(int, end.split("-"))
        if not 1 <= sm <= 12 or not 1 <= em <= 12:
            continue
        begin, finish = sy * 12 + sm, min(ey * 12 + em, today.year * 12 + today.month)
        if not 0 < finish - begin < 12 * 60:
            continue
        occupied.update(range(begin, finish))
        evidence.append(_reference(item, "experience"))
    return len(occupied), evidence


def evaluate_hard_requirements(bundle: dict[str, Any], description: str, *, today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    checks: list[dict[str, Any]] = []
    clauses = []
    for part in [part.strip() for part in re.split(r"[。；;\n]+", description) if part.strip()]:
        # Preserve a certificate alternative continued after a list separator.
        if clauses and re.match(r"(?:或者|或|任选|任一|or\b)",part,re.I) and _CERTIFICATES.search(clauses[-1]+part):
            clauses[-1] += "，"+part
        else:
            clauses.append(part)
    for clause in clauses[:40]:
        importance = "preferred" if _NICE.search(clause) else "required"
        def add(kind, state="unknown", reason="现有材料不足，需要本人核实。", evidence=None, **extra):
            checks.append({"type": kind, "text": clause[:300], "importance": importance, "state": state,
                           "reason": reason, "evidence": evidence or [], **extra})

        # These eligibility conditions are independent of having a degree or a
        # certificate name. Unsupported conditions remain visible to callers,
        # preventing all(extracted checks == met) from becoming a false pass.
        cohorts = set(re.findall(r"(20\d{2})\s*届", clause))
        if cohorts:
            education = bundle.get("education") or []
            dated = [item for item in education if _graduation_year(item) is not None]
            single = len(cohorts)==1 and len(set(re.findall(r"20\d{2}",clause)))==1 and not _ALTERNATIVE.search(clause)
            mismatch = bool(single and dated and len(dated)==len(education)
                            and all(str(item["end_date"])[:4] not in cohorts for item in dated))
            add("eligibility", "failed" if mismatch else "unknown",
                "已填写的教育毕业年份均不在这一明确届次内；若有遗漏教育经历请补充。" if mismatch else "毕业届次与应届身份需要结合招聘政策和实际材料确认。",
                [_reference(item,"education") for item in dated], condition_key="graduation_cohort", extraction_complete=bool(mismatch))
        if "毕业" in clause and len(re.findall(r"20\d{2}(?:年|[-/])", clause))>=2:
            add("eligibility", reason="具体毕业日期区间及境内外适用分支尚未完整判断，请逐项核对。",
                condition_key="graduation_window", extraction_complete=False)
        if re.search(r"首次就业|初次就业|应届(?:毕业生)?|无[^。；;\n]{0,12}(?:全职|工作经历)|未[^。；;\n]{0,8}就业",clause):
            add("eligibility", reason="应届或首次就业身份需要本人确认；没有已填工作经历不能证明从未就业。",
                condition_key="first_employment", extraction_complete=False)
        if re.search(r"留服|留学服务|留学[^。；;\n]{0,12}回国|境外[^。；;\n]{0,18}(?:学历|认证)|归国|海归",clause):
            add("eligibility", reason="境外学历、回国或认证条件尚无完整证据，请核对具体要求及认证材料。",
                condition_key="overseas_credential", extraction_complete=False)

        degree_clause = re.sub(r"(?:高中|初中|小学|幼儿园)[^，,。；;]{0,8}教师资格(?:证)?", "", clause)
        minimums = [level for label, level in DEGREES.items() if label in degree_clause]
        if minimums and re.search(r"学历|学位|毕业|以上|及以上|要求|本科|硕士|博士|大专", clause):
            minimum = min(minimums)
            education = bundle.get("education") or []
            known = [(item, _degree_level(item.get("degree"))) for item in education]
            declared = [(item, level) for item, level in known if level is not None]
            qualified = [(item, level) for item, level in declared if level >= minimum]
            if qualified:
                item = max(qualified, key=lambda pair: pair[1])[0]
                end_date = str(item.get("end_date") or "")
                in_progress = not end_date or end_date[:7] > today.strftime("%Y-%m")
                if in_progress:
                    add("education", reason="已填写学历层次，但毕业状态或预计毕业时间仍需结合岗位确认。", evidence=[_reference(item, "education")], minimum_level=minimum)
                else:
                    add("education", "met", "已填写的学历层次达到该条门槛，仍需招聘方核验。", [_reference(item, "education")], minimum_level=minimum)
            elif declared and len(declared) == len(known):
                add("education", "failed", "当前明确填写的学历层次低于该条门槛；可补充遗漏学历。", [_reference(item, "education") for item, _ in declared], minimum_level=minimum)
            else:
                add("education", minimum_level=minimum)

        years = re.search(r"(\d+(?:\.\d+)?)\s*年(?:以上|及以上)?[^。；;\n]{0,18}(?:工作|实习|经验)|(?:工作|实习|经验)[^。；;\n]{0,12}(\d+(?:\.\d+)?)\s*年", clause)
        if years and float(years.group(1) or years.group(2))<=60:
            required_years = float(years.group(1) or years.group(2))
            months, evidence = _work_months(bundle.get("experience") or [], internships="实习" in clause, today=today)
            if months >= required_years * 12:
                add("experience", "met", "已填写相应工作类型的时长覆盖门槛，重叠月份不重复累计。", evidence, required_years=required_years)
            else:
                add("experience", reason="已填写经历不足以确认年限；课程、项目和普通实习不自动算作全职工作经验。", evidence=evidence, required_years=required_years)

        certificates = _CERTIFICATES.findall(clause)
        alternatives = bool(certificates and _ALTERNATIVE.search(clause))
        score_required = bool(certificates and _SCORE.search(clause))
        if alternatives:
            # An expired option is not a failure of the whole OR clause. Do not
            # pretend alternatives and score equivalences are an AND checklist.
            add("qualification", reason="这条包含备选证书或等效证明，组合及分数要求尚待确认，不能按每项都必须具备来判断。",
                qualification=" / ".join(dict.fromkeys(certificates)), condition_key="certificate_alternatives", extraction_complete=False)
        for requirement in (() if alternatives else dict.fromkeys(certificates)):
            matched = [item for item in bundle.get("certificate") or [] if _key(requirement) in _key(item.get("name"))]
            issued = [item for item in matched if str(item.get("acquired_date") or "")[:7] <= today.strftime("%Y-%m")]
            valid = [item for item in issued if not str(item.get("expiry_date") or "").strip() or str(item["expiry_date"])[:7] >= today.strftime("%Y-%m")]
            if score_required:
                add("qualification", reason="证书名称不证明达到指定成绩，考试分数及对应凭证尚待核验。",
                    evidence=[_reference(item,"certificate") for item in valid], qualification=requirement,
                    condition_key="certificate_score", extraction_complete=False)
                continue
            if valid:
                # An unspecified certificate level cannot satisfy a stated
                # subject/stage. Do not collapse all teaching credentials.
                levels = [word for word in ("幼儿园", "小学", "初中", "高中", "中级", "高级") if word in clause]
                supported = [item for item in valid if not levels or any(level in str(item.get("name")) for level in levels)]
                if supported:
                    add("qualification", "met", "资料中有对应证书记录，请核实适用等级和有效性。", [_reference(item, "certificate") for item in supported], qualification=requirement)
                else:
                    add("qualification", reason="证书类型有记录，但要求的等级或学段尚不能确认。", evidence=[_reference(item, "certificate") for item in valid], qualification=requirement)
            elif issued:
                add("qualification", "failed", "对应证书已过填写的有效期，需要更新有效凭证。", [_reference(item, "certificate") for item in matched], qualification=requirement)
            else:
                add("qualification", qualification=requirement)
        if not certificates and re.search(r"资格证|执业证|职业资格|证书", clause):
            add("qualification")

        if re.search(r"专业(?:要求|限制)|(?:相关|指定|限)[^。；;\n]{0,15}专业|专业毕业", clause):
            majors = [(item, str(item.get("major") or "").strip()) for item in bundle.get("education") or []]
            exact = [item for item, major in majors if major and major in clause]
            add("major", "met" if exact else "unknown", "已填写专业在要求中明确出现。" if exact else "专业范围或等效专业需要进一步核实。", [_reference(item, "education") for item in exact])
        if re.search(r"工作地点|办公地点|驻地|到岗地点|需出差|经常出差", clause):
            add("location", reason="请按本人明确的城市与出差意愿确认，当前住址不等同于求职地域偏好。")
    if len(clauses)>40 or len(checks)>40:
        # Preserve the incompleteness guard even when a long JD hits the bound.
        return [{"type":"eligibility","text":"岗位条件较多，部分条款尚未纳入自动判断。","importance":"required",
                 "state":"unknown","reason":"请核对完整公告，不能仅凭当前已提取条款确认符合。","evidence":[],
                 "condition_key":"extraction_limit","extraction_complete":False},*checks[:39]]
    return checks
