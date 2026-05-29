import re
from copy import deepcopy
from datetime import datetime
from typing import Any


FINAL_MATERIAL_TYPES = {"assessment_plan", "grading_rubric", "exam_paper"}


FINAL_MATERIAL_LABELS = {
    "assessment_plan": "课程考核计划表",
    "grading_rubric": "课程考核评分细则",
    "exam_paper": "课程考核试卷",
}


FINAL_MATERIAL_LAYOUTS: dict[str, dict[str, Any]] = {
    "assessment_plan": {
        "page": "A4 portrait",
        "margins_cm": {"top": 1.15, "bottom": 1.0, "left": 1.6, "right": 1.45, "footer": 0.55},
        "title_font": {"name": "宋体", "size_pt": 18, "bold": True},
        "period_font": {"name": "宋体", "size_pt": 14, "bold": True},
        "body_font": {"name": "宋体", "size_pt": 10.5},
        "metadata_table_widths_cm": [4.45, 4.15, 4.05, 4.85],
        "assessment_table_widths_cm": [4.45, 9.65, 3.35],
        "metadata_row_height_cm": 1.12,
        "assessment_header_height_cm": 1.0,
        "assessment_body_min_height_cm": 1.75,
    },
    "grading_rubric": {
        "page": "A4 portrait",
        "margins_cm": {"top": 1.5, "bottom": 1.5, "left": 1.5, "right": 1.5, "footer": 1.75},
        "title_font": {"name": "宋体", "size_pt": 18, "bold": True},
        "period_font": {"name": "宋体", "size_pt": 14, "bold": True},
        "body_font": {"name": "宋体", "size_pt": 10.5},
        "metadata_table_widths_cm": [3.2, 4.0, 3.0, 4.8],
    },
    "exam_paper": {
        "page": "A4 portrait",
        "margins_cm": {"top": 1.5, "bottom": 1.5, "left": 3.0, "right": 2.0, "footer": 1.5},
        "title_font": {"name": "宋体", "size_pt": 18, "bold": True},
        "period_font": {"name": "宋体", "size_pt": 14, "bold": True},
        "body_font": {"name": "宋体", "size_pt": 10.5},
        "metadata_table_widths_cm": [1.95, 2.75, 1.65, 2.85, 1.6, 2.85, 1.5, 2.2],
    },
}


ASSESSMENT_PLAN_SCHEMA_VERSION = "gxufl-assessment-plan-v2"

ASSESSMENT_PLAN_NOTES = [
    "注：",
    "1．课程名称必须与教学计划上的名称一致。",
    "2．考核类型：考查、考试（按教学计划填写）。",
    "3．命题教师：务必输入命题教师名字，打印纸质版后再手写签名；系（教研室）主任审核签字：须手写签名。",
    "4．各专业根据教学大纲自行拟定考核形式、考核技能/内容、分值。",
    "5. 该表文字部分均用五号宋体，使用A4纸双面打印。",
    "6. 命题完成后将该表与评分细则（电子版及纸质版）交到二级学院（部），并装入试卷袋存档。",
]

ASSESSMENT_PLAN_DEFAULT_ITEMS = [
    {
        "assessment_form": "机试",
        "content": "Linux 用户与目录管理（创建用户组 / 用户、设置密码、创建多级目录、修改归属与权限，执行 id/grep/ls 命令查询信息）",
        "score": "24",
    },
    {
        "assessment_form": "机试",
        "content": "Shell 脚本编写（创建系统巡检、自动化备份脚本，编写指定命令与输出，添加可执行权限并执行）",
        "score": "14",
    },
    {
        "assessment_form": "机试",
        "content": "Web 服务部署与配置（安装 httpd、启停服务并设开机自启，配置 SELinux 权限与用户家目录网页访问，访问测试页面）",
        "score": "25",
    },
    {
        "assessment_form": "机试",
        "content": "数据库服务管理（安装 mariadb-server、启停服务并设开机自启，初始化数据库，创建数据库 / 用户及授权，查询用户权限）",
        "score": "27",
    },
    {
        "assessment_form": "机试",
        "content": "服务与系统状态查询（执行 systemctl/getsebool 命令查看 httpd 服务、SELinux 状态，执行数据库权限查询命令）",
        "score": "10",
    },
]


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("标题", "材料标题", "title"),
    "school": ("学校", "school"),
    "college": ("学院", "二级学院", "二级学院（部）", "college"),
    "department": ("系部", "系（教研室）", "department"),
    "course_name": ("课程名称", "考试科目", "科目", "course_name"),
    "course_code": ("课程代码", "课程编号", "course_code"),
    "class_name": ("专业年级班级", "授课班级", "班级", "专业班级", "class_name"),
    "examiner_name": ("命题教师", "出题人", "命题人", "examiner_name"),
    "reviewer_name": ("系（教研室）主任审核签字", "系（教研室）主任", "系主任", "审核人", "reviewer_name"),
    "teacher_name": ("授课教师", "任课教师", "教师", "teacher_name"),
    "leader_name": ("二级学院（部）主管教学领导", "主管教学领导", "审批人", "leader_name"),
    "academic_year": ("学年", "学年度", "academic_year"),
    "semester": ("学期", "semester"),
    "date": ("日期", "命题日期", "date"),
    "assessment_type": ("考核类型", "考查考试", "assessment_type"),
    "assessment_method": ("考核形式", "考核方式", "考试形式", "assessment_method"),
    "assessment_mode": ("笔试考核", "非笔试考核", "考核方式类型", "考核形态", "assessment_mode", "exam_work_mode"),
    "assessment_mode_label": ("考核方式标注", "考核计划类型", "assessment_mode_label"),
    "examiner_signature": ("命题教师签名", "命题教师签字", "examiner_signature"),
    "reviewer_signature": ("系主任签名", "审核签名", "系（教研室）主任签名", "reviewer_signature"),
    "academic_exam_method": ("教务考核方式", "教务考核类型", "academic_exam_method"),
    "academic_exam_mode": ("教务考试方式", "教务考试形式", "academic_exam_mode"),
    "paper_type": ("试卷类型", "开闭卷", "paper_type"),
    "education_level": ("学历层次", "education_level"),
    "exam_duration": ("考试时间", "考试时长", "exam_duration"),
    "total_score": ("总分", "满分", "total_score"),
}


def is_final_material_type(document_type: str | None) -> bool:
    return str(document_type or "").strip() in FINAL_MATERIAL_TYPES


def final_material_label(document_type: str | None) -> str:
    key = str(document_type or "").strip()
    return FINAL_MATERIAL_LABELS.get(key, "期末材料")


def normalize_final_material_payload(
    *,
    document_type: str,
    metadata: dict[str, Any] | None,
    content_markdown: str,
    tables: list[dict[str, Any]] | None,
    export_payload: dict[str, Any] | None = None,
    classroom_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = str(document_type or "").strip()
    if key not in FINAL_MATERIAL_TYPES:
        return deepcopy(export_payload or {})

    normalized_tables = normalize_table_payloads(tables or [])
    fields = _normalize_field_map(metadata or {})
    if export_payload:
        fields.update({k: v for k, v in _normalize_field_map(_as_dict(export_payload.get("fields"))).items() if _is_blank(fields.get(k))})
    fields.update({k: v for k, v in _fields_from_markdown_tables(normalized_tables).items() if _is_blank(fields.get(k))})
    fields.update({k: v for k, v in _fields_from_text(content_markdown).items() if _is_blank(fields.get(k))})
    if classroom_context:
        fields.update({k: v for k, v in _fields_from_classroom_context(classroom_context).items() if _is_blank(fields.get(k))})

    sections = split_markdown_sections(content_markdown)
    if key == "assessment_plan":
        fields = _normalize_assessment_plan_fields(fields)
        structured = _assessment_plan_payload(
            fields,
            normalized_tables,
            sections,
            seed_items=_assessment_items_from_export_payload(export_payload),
        )
    elif key == "grading_rubric":
        structured = _grading_rubric_payload(fields, normalized_tables, sections, content_markdown)
    else:
        structured = _exam_paper_payload(fields, normalized_tables, sections, content_markdown)

    base = deepcopy(export_payload or {})
    base.setdefault("document_group", "final_material")
    base["document_type"] = key
    base["document_type_label"] = FINAL_MATERIAL_LABELS[key]
    base["template_key"] = key
    base["fields"] = {**_as_dict(base.get("fields")), **fields}
    base["sections"] = structured.get("sections", sections)
    base["tables"] = normalized_tables
    base["layout_profile"] = deepcopy(FINAL_MATERIAL_LAYOUTS[key])
    base["structured"] = structured
    base["queryable_fields"] = _queryable_fields(base["fields"], structured)
    base["compatibility"] = {
        **_as_dict(base.get("compatibility")),
        "source_format_preserved": True,
        "requires_template_confirmation": bool(structured.get("requires_teacher_confirmation")),
        "layout_source": "guangwai_final_material_samples",
        "template_schema_version": structured.get("template_schema_version") or "",
    }
    return base


def normalize_table_payloads(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(tables or [], start=1):
        if not isinstance(item, dict):
            continue
        rows = item.get("rows")
        if not isinstance(rows, list):
            continue
        clean_rows = []
        for row in rows:
            if isinstance(row, dict):
                clean_row = [_stringify(value).strip() for value in row.values()]
            elif isinstance(row, list):
                clean_row = [_stringify(value).strip() for value in row]
            else:
                continue
            if any(clean_row):
                clean_rows.append(clean_row)
        if not clean_rows:
            continue
        max_len = max(len(row) for row in clean_rows)
        clean_rows = [row + [""] * (max_len - len(row)) for row in clean_rows]
        normalized.append(
            {
                "title": str(item.get("title") or f"表格 {index}").strip(),
                "rows": clean_rows,
                "column_count": max_len,
                "row_count": len(clean_rows),
            }
        )
    return normalized


def extract_markdown_tables(content: str) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    buffer: list[str] = []
    table_index = 1

    def flush() -> None:
        nonlocal buffer, table_index
        if len(buffer) < 2:
            buffer = []
            return
        rows: list[list[str]] = []
        for line in buffer:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                continue
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append({"title": f"表格 {table_index}", "rows": rows})
            table_index += 1
        buffer = []

    for line in str(content or "").splitlines():
        stripped = line.strip()
        if "|" in stripped and re.match(r"^\|?.+\|.+\|?$", stripped):
            buffer.append(stripped)
        else:
            flush()
    flush()
    return normalize_table_payloads(tables)


def split_markdown_sections(content: str) -> list[dict[str, Any]]:
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    sections: list[dict[str, Any]] = []
    current_title = "正文"
    current_lines: list[str] = []
    heading_pattern = re.compile(r"^\s*(?:#{1,4}\s*)?([一二三四五六七八九十]+[、.．].+|任务\s*\d+[:：].+|评分细则|注[:：]?)\s*$")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        implicit = heading_pattern.match(line) if not heading else None
        if heading or implicit:
            if current_lines:
                sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})
            current_title = (heading.group(2) if heading else implicit.group(1)).strip()
            current_lines = []
        else:
            current_lines.append(raw_line)
    if current_lines:
        sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})
    return [section for section in sections if section.get("content")]


def build_final_material_generation_seed(
    *,
    document_type: str,
    classroom_context: dict[str, Any],
    prompt: str = "",
) -> dict[str, Any]:
    key = str(document_type or "").strip()
    fields = _fields_from_classroom_context(classroom_context)
    now = datetime.now()
    fields.setdefault("date", now.strftime("%Y年%m月%d日"))
    fields.setdefault("title", final_material_label(key))
    fields.setdefault("assessment_type", "考试")
    fields.setdefault("assessment_method", "机试")
    fields.setdefault("assessment_mode", "non_written")
    fields.setdefault("assessment_mode_label", "非笔试考核")
    fields.setdefault("exam_duration", "90")
    fields.setdefault("total_score", "100")
    sections: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    if key == "assessment_plan":
        tables = [
            {
                "title": "考核技能/内容",
                "rows": [
                    ["考核形式", "考核技能/内容", "分值"],
                    *[
                        [item["assessment_form"], item["content"], item["score"]]
                        for item in ASSESSMENT_PLAN_DEFAULT_ITEMS
                    ],
                ],
            }
        ]
        sections = [{"title": "注", "content": "\n".join(ASSESSMENT_PLAN_NOTES)}]
    elif key == "grading_rubric":
        tables = []
        sections = [
            {"title": "评分细则", "content": _default_rubric_content(fields, prompt)},
        ]
    else:
        tables = []
        sections = [
            {"title": "一、基础环境配置（共30分）", "content": _default_exam_section_one(prompt)},
            {"title": "二、综合服务部署（共70分）", "content": _default_exam_section_two(prompt)},
        ]
    content = "\n\n".join(f"## {item['title']}\n{item['content']}" for item in sections)
    export_payload = normalize_final_material_payload(
        document_type=key,
        metadata=fields,
        content_markdown=content,
        tables=tables,
        export_payload={"sections": sections, "tables": tables},
        classroom_context=classroom_context,
    )
    return {
        "metadata": export_payload["fields"],
        "content_markdown": content,
        "tables": tables,
        "warnings": ["AI 未返回可用内容时生成的本地完整草稿，请教师复核后导出。"],
        "export_payload": export_payload,
    }


def _assessment_plan_payload(
    fields: dict[str, Any],
    tables: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    *,
    seed_items: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    items = seed_items or _assessment_items_from_tables(tables)
    if not items:
        items = _default_assessment_items(fields)
    total = _sum_score(item.get("score") for item in items) or _to_number(fields.get("total_score")) or 100
    fields["total_score"] = _score_to_text(total)
    return {
        "fields": fields,
        "assessment_items": items,
        "total_score": total,
        "notes": list(ASSESSMENT_PLAN_NOTES),
        "assessment_mode": fields.get("assessment_mode") or "non_written",
        "assessment_mode_label": fields.get("assessment_mode_label") or "非笔试考核",
        "requires_teacher_confirmation": bool(fields.get("requires_teacher_confirmation")),
        "template_schema_version": ASSESSMENT_PLAN_SCHEMA_VERSION,
        "sections": sections or [{"title": "注", "content": "\n".join(ASSESSMENT_PLAN_NOTES)}],
    }


def _grading_rubric_payload(
    fields: dict[str, Any],
    tables: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    content: str,
) -> dict[str, Any]:
    rubric_items = _rubric_items_from_text(content)
    total = _sum_score(item.get("score") for item in rubric_items) or _to_number(fields.get("total_score")) or 100
    return {
        "fields": fields,
        "rubric_items": rubric_items,
        "total_score": total,
        "sections": sections or [{"title": "评分细则", "content": content.strip()}],
    }


def _exam_paper_payload(
    fields: dict[str, Any],
    tables: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    content: str,
) -> dict[str, Any]:
    paper_sections = _paper_sections_from_sections(sections, content)
    total = _sum_score(item.get("score") for item in paper_sections) or _to_number(fields.get("total_score")) or 100
    return {
        "fields": fields,
        "paper_sections": paper_sections,
        "total_score": total,
        "student_fields": ["姓名", "学号", "年级、专业、班级", "座位号"],
        "sections": sections or [{"title": "试卷正文", "content": content.strip()}],
    }


def _normalize_assessment_plan_fields(fields: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(fields)
    normalized.setdefault("school", "广西外国语学院")
    normalized["title"] = "课程考核计划表"
    assessment_type = _normalize_assessment_type(
        normalized.get("assessment_type")
        or normalized.get("academic_exam_method")
        or normalized.get("exam_method")
        or normalized.get("course_nature")
    )
    normalized["assessment_type"] = assessment_type

    mode_code, mode_label, inferred_from_teacher = _normalize_assessment_mode(
        normalized.get("assessment_mode")
        or normalized.get("assessment_mode_label")
        or normalized.get("assessment_method")
        or normalized.get("academic_exam_mode")
        or normalized.get("exam_mode"),
        assessment_type=assessment_type,
    )
    normalized["assessment_mode"] = mode_code
    normalized["assessment_mode_label"] = mode_label
    if not inferred_from_teacher and assessment_type == "考试":
        normalized["requires_teacher_confirmation"] = True
    normalized.setdefault("assessment_method", "机试" if mode_code == "non_written" else "闭卷笔试")
    normalized.setdefault("examiner_name", normalized.get("teacher_name") or "")
    normalized.setdefault("date", datetime.now().strftime("%Y年%m月%d日"))
    return normalized


def _normalize_assessment_type(value: Any) -> str:
    raw = _stringify(value)
    if "考查" in raw or "考察" in raw:
        return "考查"
    if "考试" in raw:
        return "考试"
    return "考试"


def _normalize_assessment_mode(value: Any, *, assessment_type: str) -> tuple[str, str, bool]:
    raw = _stringify(value)
    if assessment_type == "考查":
        return "non_written", "非笔试考核", bool(raw)
    if "非笔试" in raw or "非 笔试" in raw or "机试" in raw or "实操" in raw or "作品" in raw or "项目" in raw:
        return "non_written", "非笔试考核", True
    if "笔试" in raw or "闭卷" in raw or "开卷" in raw:
        return "written", "笔试考核", True
    if str(raw).strip().lower() in {"written", "paper", "笔试考核"}:
        return "written", "笔试考核", True
    if str(raw).strip().lower() in {"non_written", "non-written", "practical", "非笔试考核"}:
        return "non_written", "非笔试考核", True
    return "non_written", "非笔试考核", False


def _default_assessment_items(fields: dict[str, Any]) -> list[dict[str, str]]:
    if _stringify(fields.get("course_name")).strip() == "服务器配置与管理":
        return [dict(item) for item in ASSESSMENT_PLAN_DEFAULT_ITEMS]
    method = _stringify(fields.get("assessment_method") or "机试") or "机试"
    course = _stringify(fields.get("course_name") or "本课程")
    return [
        {"assessment_form": method, "content": f"{course}基础知识、基本概念与核心工具使用", "score": "20"},
        {"assessment_form": method, "content": f"{course}核心技能/内容的独立完成与过程规范", "score": "35"},
        {"assessment_form": method, "content": f"{course}综合任务、案例分析或实践成果质量", "score": "35"},
        {"assessment_form": method, "content": "答题/提交规范、结果可复核性与材料完整性", "score": "10"},
    ]


def _assessment_items_from_tables(tables: list[dict[str, Any]]) -> list[dict[str, str]]:
    for table in tables:
        rows = table.get("rows") or []
        if not rows:
            continue
        header = [str(cell).replace(" ", "") for cell in rows[0]]
        if any("考核技能" in cell or "考核内容" in cell for cell in header) and any("分值" in cell for cell in header):
            items = []
            for row in rows[1:]:
                if len(row) < 3:
                    continue
                items.append(
                    {
                        "assessment_form": row[0],
                        "content": row[1],
                        "score": row[2],
                    }
                )
            return items
    return []


def _assessment_items_from_export_payload(export_payload: dict[str, Any] | None) -> list[dict[str, str]]:
    payload = _as_dict(export_payload)
    structured = _as_dict(payload.get("structured"))
    raw_items = structured.get("assessment_items") if isinstance(structured.get("assessment_items"), list) else []
    items: list[dict[str, str]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        content = _stringify(item.get("content") or item.get("assessment_content") or "")
        score = _stringify(item.get("score") or "")
        if not content and not score:
            continue
        items.append(
            {
                "assessment_form": _stringify(item.get("assessment_form") or item.get("form") or "机试"),
                "content": content,
                "score": score,
            }
        )
    return items


def _rubric_items_from_text(content: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in str(content or "").splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue
        heading = re.match(r"^([一二三四五六七八九十]+[、.．].*?)(?:共\s*(\d+(?:\.\d+)?)\s*分|（共\s*(\d+(?:\.\d+)?)\s*分）|\(共\s*(\d+(?:\.\d+)?)\s*分\))?", line)
        if heading:
            current = {"title": heading.group(1).strip(), "score": _first_non_empty(heading.group(2), heading.group(3), heading.group(4)), "criteria": []}
            items.append(current)
            continue
        score_line = re.search(r"【\s*(\d+(?:\.\d+)?)\s*分\s*】(.+)", line)
        if score_line:
            if current is None:
                current = {"title": "评分细则", "score": "", "criteria": []}
                items.append(current)
            current["criteria"].append({"score": score_line.group(1), "text": score_line.group(2).strip()})
    return items


def _paper_sections_from_sections(sections: list[dict[str, Any]], content: str) -> list[dict[str, Any]]:
    candidates = sections or split_markdown_sections(content)
    result: list[dict[str, Any]] = []
    for item in candidates:
        title = str(item.get("title") or "").strip()
        body = str(item.get("content") or "").strip()
        if not title and not body:
            continue
        score = ""
        match = re.search(r"(?:共\s*|\(|（)(\d+(?:\.\d+)?)\s*分", title + "\n" + body[:200])
        if match:
            score = match.group(1)
        if re.match(r"^[一二三四五六七八九十]+[、.．]", title) or "任务" in title or score:
            result.append({"title": title or "试题", "score": score, "content": body})
    if not result and content.strip():
        result.append({"title": "试卷正文", "score": "", "content": content.strip()})
    return result


def _fields_from_markdown_tables(tables: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for table in tables[:2]:
        for row in table.get("rows") or []:
            cells = [str(cell or "").strip() for cell in row if str(cell or "").strip()]
            if len(cells) < 2:
                continue
            for index in range(0, len(cells) - 1, 2):
                key = _canonical_field_key(cells[index])
                if key:
                    fields.setdefault(key, _normalize_field_value(key, cells[index + 1]))
            if len(cells) == 2:
                key = _canonical_field_key(cells[0])
                if key:
                    fields.setdefault(key, _normalize_field_value(key, cells[1]))
    if "examiner_name" in fields and "teacher_name" not in fields:
        fields["teacher_name"] = fields["examiner_name"]
    return fields


def _fields_from_text(content: str) -> dict[str, Any]:
    text = " ".join(str(content or "").split())
    fields: dict[str, Any] = {"school": "广西外国语学院"}
    year_match = re.search(r"(20\s*\d{2})\s*[—－~-]\s*(20\s*\d{2})\s*学年度第\s*([一二三四五六七八九十\d]+)\s*学期", text)
    if year_match:
        fields["academic_year"] = f"{year_match.group(1).replace(' ', '')}-{year_match.group(2).replace(' ', '')}"
        fields["semester"] = _semester_label(year_match.group(3))
    total_match = re.search(r"总分\s*(?:[:：])?\s*(\d+(?:\.\d+)?)", text)
    if total_match:
        fields["total_score"] = total_match.group(1)
    return fields


def _fields_from_classroom_context(context: dict[str, Any]) -> dict[str, Any]:
    raw = _as_dict(context)
    fields = {
        "course_name": raw.get("course_name") or raw.get("course") or "",
        "course_code": raw.get("course_code") or "",
        "class_name": raw.get("class_name") or "",
        "teacher_name": raw.get("teacher_name") or raw.get("teacher") or "",
        "examiner_name": raw.get("teacher_name") or raw.get("teacher") or "",
        "academic_year": raw.get("academic_year") or "",
        "semester": raw.get("semester") or "",
        "college": raw.get("college") or "",
        "department": raw.get("department") or "",
        "assessment_type": raw.get("assessment_type") or raw.get("academic_exam_method") or "",
        "assessment_method": raw.get("assessment_method") or "",
        "assessment_mode": raw.get("assessment_mode") or "",
        "assessment_mode_label": raw.get("assessment_mode_label") or "",
        "academic_exam_method": raw.get("academic_exam_method") or "",
        "academic_exam_mode": raw.get("academic_exam_mode") or "",
    }
    return {key: value for key, value in fields.items() if not _is_blank(value)}


def _normalize_field_map(metadata: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in metadata.items():
        if _is_blank(value):
            continue
        canonical = _canonical_field_key(key) or str(key)
        fields[canonical] = _normalize_field_value(canonical, value)
    if "document_type" in fields:
        fields.pop("document_type", None)
    if "document_group" in fields:
        fields.pop("document_group", None)
    return fields


def _canonical_field_key(raw_key: str) -> str:
    normalized = re.sub(r"\s+", "", str(raw_key or "").replace("：", "").replace(":", ""))
    if not normalized:
        return ""
    for canonical, aliases in FIELD_ALIASES.items():
        if normalized == canonical:
            return canonical
    alias_pairs: list[tuple[str, str]] = []
    for canonical, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            alias_norm = re.sub(r"\s+", "", alias)
            if alias_norm:
                alias_pairs.append((canonical, alias_norm))
    for canonical, alias_norm in sorted(alias_pairs, key=lambda item: len(item[1]), reverse=True):
        if normalized == alias_norm:
            return canonical
    for canonical, alias_norm in sorted(alias_pairs, key=lambda item: len(item[1]), reverse=True):
        if alias_norm in normalized:
            return canonical
    return ""


def _clean_field_value(value: Any) -> Any:
    if isinstance(value, str):
        text = " ".join(value.replace("\u3000", " ").split())
        text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff\d])", "", text)
        text = re.sub(r"(?<=\d)\s+(?=[\d\u4e00-\u9fff])", "", text)
        text = re.sub(r"(?<=[、,，])\s+(?=\d)", "", text)
        return text
    return value


def _normalize_field_value(key: str, value: Any) -> Any:
    text = _clean_field_value(value)
    if not isinstance(text, str):
        return text
    if key == "exam_duration":
        match = re.search(r"\d+(?:\.\d+)?", text)
        return match.group(0) if match else text
    return text


def _queryable_fields(fields: dict[str, Any], structured: dict[str, Any]) -> dict[str, Any]:
    queryable = {
        key: value
        for key, value in fields.items()
        if key in FIELD_ALIASES or key in {"source_filename", "document_type_label"}
    }
    for key in ("assessment_items", "rubric_items", "paper_sections", "total_score"):
        if key in structured:
            queryable[key] = structured[key]
    return queryable


def _default_generation_note(prompt: str) -> str:
    extra = f"\n教师补充要求：{prompt}" if str(prompt or "").strip() else ""
    return (
        "1. 课程名称必须与教学计划一致。\n"
        "2. 考核技能/内容应覆盖课程核心目标，分值合计为100分。\n"
        "3. 命题教师、审核人、日期等字段可导出后复核签字。"
        f"{extra}"
    )


def _default_rubric_content(fields: dict[str, Any], prompt: str) -> str:
    course = fields.get("course_name") or "本课程"
    extra = f"\n\n教师补充要求：{prompt}" if str(prompt or "").strip() else ""
    return (
        f"{course}评分细则应与试卷任务一一对应，重点检查操作结果、截图命名、脚本可执行性和关键配置正确性。\n"
        "一、基础任务（共30分）\n"
        "【10分】基础账户、目录或环境配置正确。\n"
        "【10分】关键命令执行结果完整且截图清晰。\n"
        "【10分】脚本或配置文件命名规范、内容可复现。\n"
        "二、综合任务（共70分）\n"
        "【30分】服务部署、启动、自启与访问验证完整。\n"
        "【25分】数据库、权限或业务配置满足题目限制。\n"
        "【15分】自动化脚本、归档文件和最终提交包符合要求。"
        f"{extra}"
    )


def _default_exam_section_one(prompt: str) -> str:
    extra = f"\n教师补充要求：{prompt}" if str(prompt or "").strip() else ""
    return (
        "背景：请围绕课程核心环境配置任务设计操作题。\n"
        "任务：完成账户、目录、权限、服务状态检查等基础操作，并按要求保存截图或代码。\n"
        "要求：截图需清晰显示关键命令、用户名、路径、权限或服务状态。"
        f"{extra}"
    )


def _default_exam_section_two(prompt: str) -> str:
    extra = f"\n教师补充要求：{prompt}" if str(prompt or "").strip() else ""
    return (
        "背景：请设计一个贴近真实业务的综合部署场景。\n"
        "任务：完成 Web 服务、数据库服务和自动化备份脚本等综合操作。\n"
        "要求：最终提交测试目录、截图、脚本和压缩包，命名必须规范。"
        f"{extra}"
    )


def _semester_label(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw in {"1", "一"}:
        return "第一学期"
    if raw in {"2", "二"}:
        return "第二学期"
    return raw if "学期" in raw else f"第{raw}学期"


def _sum_score(values: Any) -> float:
    total = 0.0
    for value in values or []:
        number = _to_number(value)
        if number is not None:
            total += number
    return total


def _to_number(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def _score_to_text(value: Any) -> str:
    number = _to_number(value)
    if number is None:
        return _stringify(value)
    return str(int(number)) if float(number).is_integer() else str(number)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return str(value)
    return str(value)


def _is_blank(value: Any) -> bool:
    return value in (None, "", [], {})


def _clean_line(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[#>*+\-\s]+", "", text)
    text = re.sub(r"[*_`]+", "", text)
    return text.strip()
