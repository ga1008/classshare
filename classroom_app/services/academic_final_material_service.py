"""GXUFL academic final-material synchronization, parsing and validation.

The academic system exposes the grade register and exam analysis through the
same submitted-grade page.  This module deliberately treats them as one
paired, idempotent synchronization operation:

* one login and one course-list request;
* one selected academic teaching class;
* two FineReport Word (RTF-in-``.doc``) exports;
* deterministic parsing and cross-document score validation;
* optional AI review remains an orchestration concern in the router.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import math
import re
import statistics
import time
import unicodedata
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx

from ..config import STATIC_DIR
from ..database import get_db_connection
from ..db.connection import execute_insert_returning_id
from ..db.schema_academic_final_materials import ensure_academic_final_material_schema
from .academic_exam_roster_sync_service import (
    SCHOOL_CODE,
    ZF_EXAM_COURSE_INDEX_PATH,
    _exam_student_from_row,
    _fetch_exam_courses,
    _fetch_exam_students,
    _field,
    _load_offering_context,
    _load_semester_for_offering,
    _select_exam_course,
)
from .academic_integration_service import (
    load_teacher_academic_access_method,
    open_authenticated_academic_client,
)
from .academic_service import china_now
from .signature_service import resolve_signature_file_path


ACADEMIC_GRADE_REGISTER_TYPE = "academic_grade_register"
ACADEMIC_EXAM_ANALYSIS_TYPE = "academic_exam_analysis"
ACADEMIC_GRADE_REGISTER_LABEL = "期末成绩登记表"
ACADEMIC_EXAM_ANALYSIS_LABEL = "试卷分析表"
ACADEMIC_FINAL_MATERIAL_TYPES = {
    ACADEMIC_GRADE_REGISTER_TYPE,
    ACADEMIC_EXAM_ANALYSIS_TYPE,
}

ACADEMIC_FINAL_MATERIAL_CACHE_SECONDS = 30 * 60
ACADEMIC_FINAL_MATERIAL_STALE_SECONDS = 12 * 60
ACADEMIC_FINAL_MATERIAL_ACTIVE_STATUSES = {"queued", "running", "processing"}
ZF_REPORT_INIT_PATH = "/report/report_cxFineReportViewIndex.html"
ZF_GRADE_ENTRY_FUNCTION_CODE = "N302505"
ZF_GRADE_REGISTER_REPORT_ID = "cjddy_bj.cpt"
ZF_EXAM_ANALYSIS_REPORT_ID = "sjfxabjdy.cpt"
FINE_REPORT_ALLOWED_HOSTS = {"jwcjcx.gxufl.com", "jwxt.gxufl.com"}

_sync_locks: dict[tuple[int, int], asyncio.Lock] = {}


def _now_iso() -> str:
    return china_now().replace(tzinfo=None).isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _normalize_space(value: Any) -> str:
    return re.sub(r"[ \u3000]+", " ", str(value or "")).strip()


def _normalize_match_text(value: Any) -> str:
    """Normalize human-entered labels without erasing meaningful symbols.

    Course names are entered independently in LanShare and JWXT.  Case,
    full-width Latin characters, spaces and decorative separators are not
    identity-bearing, while symbols such as ``+`` and ``#`` can be part of a
    real course name and must be preserved.
    """

    text = unicodedata.normalize("NFKC", _normalize_space(value)).casefold()
    return re.sub(r"[\s·•・‐‑‒–—―_-]+", "", text)


def _labels_equivalent(left: Any, right: Any) -> bool:
    left_key = _normalize_match_text(left)
    right_key = _normalize_match_text(right)
    return bool(left_key and right_key and (left_key == right_key or left_key in right_key or right_key in left_key))


def _float(value: Any) -> float | None:
    text = str(value or "").strip().replace("%", "")
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _int(value: Any) -> int:
    parsed = _float(value)
    return int(parsed) if parsed is not None else 0


def _is_rtf_doc(content: bytes) -> bool:
    return content.lstrip().startswith(b"{\\rtf")


_RTF_DESTINATIONS = {
    "fonttbl",
    "colortbl",
    "stylesheet",
    "info",
    "pict",
    "shppict",
    "nonshppict",
    "listtable",
    "listoverridetable",
    "generator",
    "xmlnstbl",
    "datastore",
    "themedata",
    "colorschememapping",
}


def extract_fine_report_rtf_text(content: bytes) -> str:
    """Extract Unicode/cell structure from FineReport RTF without Word/LO."""
    if not _is_rtf_doc(content):
        raise ValueError("教务系统下载结果不是可识别的 Word/RTF 报表。")
    source = content.decode("latin-1", errors="ignore")
    output: list[str] = []
    stack: list[tuple[bool, int]] = []
    skip_destination = False
    unicode_skip = 1
    index = 0
    length = len(source)

    while index < length:
        char = source[index]
        if char == "{":
            stack.append((skip_destination, unicode_skip))
            index += 1
            continue
        if char == "}":
            if stack:
                skip_destination, unicode_skip = stack.pop()
            index += 1
            continue
        if char != "\\":
            if not skip_destination and char not in "\r\n":
                output.append(char)
            index += 1
            continue

        index += 1
        if index >= length:
            break
        marker = source[index]
        if marker in "\\{}":
            if not skip_destination:
                output.append(marker)
            index += 1
            continue
        if marker == "'":
            if index + 2 < length and not skip_destination:
                try:
                    output.append(bytes.fromhex(source[index + 1 : index + 3]).decode("cp1252"))
                except (ValueError, UnicodeDecodeError):
                    pass
            index += 3
            continue
        if marker == "*":
            skip_destination = True
            index += 1
            continue
        if not marker.isalpha():
            if marker == "~" and not skip_destination:
                output.append(" ")
            elif marker == "_" and not skip_destination:
                output.append("-")
            index += 1
            continue

        word_start = index
        while index < length and source[index].isalpha():
            index += 1
        word = source[word_start:index]
        sign = 1
        if index < length and source[index] == "-":
            sign = -1
            index += 1
        number_start = index
        while index < length and source[index].isdigit():
            index += 1
        parameter = None
        if index > number_start:
            parameter = sign * int(source[number_start:index])
        if index < length and source[index] == " ":
            index += 1

        if word in _RTF_DESTINATIONS:
            skip_destination = True
            continue
        if word == "uc" and parameter is not None:
            unicode_skip = max(0, parameter)
            continue
        if word == "u" and parameter is not None:
            if not skip_destination:
                output.append(chr(parameter if parameter >= 0 else parameter + 65536))
            fallback = unicode_skip
            while fallback > 0 and index < length:
                if source[index] == "\\" and index + 1 < length and source[index + 1] == "'":
                    index += 4
                else:
                    index += 1
                fallback -= 1
            continue
        if word == "bin" and parameter is not None:
            index += max(0, parameter)
            continue
        if skip_destination:
            continue
        if word in {"cell", "nestcell"}:
            output.append("\t")
        elif word in {"row", "nestrow", "par", "line"}:
            output.append("\n")
        elif word == "tab":
            output.append("\t")

    text = "".join(output).replace("\x00", "")
    text = re.sub(r"\t+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _extract_labeled_text(text: str, label: str, stop_labels: list[str]) -> str:
    stops = "|".join(re.escape(item) for item in stop_labels)
    match = re.search(
        rf"{re.escape(label)}\s*[：:]?\s*([^\t\n]+?)(?=\s*(?:{stops})\s*[：:]|\t|\n|$)",
        text,
    )
    return _normalize_space(match.group(1)) if match else ""


def _student_from_cells(cells: list[str]) -> dict[str, Any] | None:
    padded = [_normalize_space(value) for value in cells[:8]]
    padded += [""] * (8 - len(padded))
    student_number, student_name = padded[:2]
    if not re.fullmatch(r"\d{8,16}", student_number) or not student_name:
        return None
    return {
        "student_number": student_number,
        "student_name": student_name,
        "ordinary_score": _float(padded[2]),
        "midterm_score": _float(padded[3]),
        "experiment_online_score": _float(padded[4]),
        "final_exam_score": _float(padded[5]),
        "final_score": _float(padded[6]),
        "remark": padded[7],
    }


def parse_grade_register_rtf(content: bytes) -> dict[str, Any]:
    text = extract_fine_report_rtf_text(content)
    if "期末成绩登记表" not in text:
        raise ValueError("下载文件中未识别到“期末成绩登记表”标题。")
    lines = text.splitlines()
    students: list[dict[str, Any]] = []
    seen: set[str] = set()
    header_seen = False
    for line in lines:
        cells = line.split("\t")
        if not header_seen:
            header_seen = len(cells) >= 8 and cells[0].strip() == "学号" and cells[1].strip() == "姓名"
            continue
        if "教师：" in line:
            break
        for offset in (0, 8):
            student = _student_from_cells(cells[offset : offset + 8])
            if student and student["student_number"] not in seen:
                seen.add(student["student_number"])
                students.append(student)

    period_match = re.search(r"(20\d{2}\s*-\s*20\d{2})\s*学年第?\s*([一二12])\s*学期", text)
    fields = {
        "title": "广西外国语学院期末成绩登记表",
        "academic_year": period_match.group(1).replace(" ", "") if period_match else "",
        "semester": f"第{period_match.group(2)}学期" if period_match else "",
        "department": _extract_labeled_text(text, "开课部门", ["班级", "任课教师", "学分"]),
        "class_name": _extract_labeled_text(text, "班级", ["任课教师", "学分", "课程名称"]),
        "teacher_name": _extract_labeled_text(text, "任课教师", ["学分", "课程名称"]),
        "credits": _extract_labeled_text(text, "学分", ["课程名称"]),
        "course_name": _extract_labeled_text(text, "课程名称", ["课程性质", "考核方式", "填表日期"]),
        "course_nature": _extract_labeled_text(text, "课程性质", ["考核方式", "填表日期"]),
        "assessment_method": _extract_labeled_text(text, "考核方式", ["填表日期"]),
        "date": _extract_labeled_text(text, "填表日期", ["学号"]),
    }
    formula_match = re.search(r"总评成绩\s*=\s*([^\n]+)", text)
    formula = _normalize_space(formula_match.group(1)) if formula_match else ""
    return {
        "text": text,
        "fields": fields,
        "students": students,
        "formula": formula,
        "source_format": "fine_report_rtf_doc",
    }


def _cells_after_label(lines: list[str], label: str) -> list[str]:
    for line in lines:
        cells = [_normalize_space(value) for value in line.split("\t")]
        if label in cells:
            return cells[cells.index(label) + 1 :]
    return []


def parse_exam_analysis_rtf(content: bytes) -> dict[str, Any]:
    text = extract_fine_report_rtf_text(content)
    if "课程试卷分析表" not in text and "试卷分析表" not in text:
        raise ValueError("下载文件中未识别到“课程试卷分析表”标题。")
    lines = text.splitlines()
    period_match = re.search(r"(20\d{2}\s*-\s*20\d{2})\s*学年第?\s*([一二12])\s*学期", text)
    course_cells = _cells_after_label(lines, "课程名称")
    teacher_cells = _cells_after_label(lines, "教师姓名")
    class_cells = _cells_after_label(lines, "学生班级")
    count_cells = _cells_after_label(lines, "人数")
    ratio_cells = _cells_after_label(lines, "比例")
    average_cells = _cells_after_label(lines, "平均分")
    maximum_cells = _cells_after_label(lines, "最高分")
    fields = {
        "title": "广西外国语学院课程试卷分析表",
        "academic_year": period_match.group(1).replace(" ", "") if period_match else "",
        "semester": f"第{period_match.group(2)}学期" if period_match else "",
        "course_name": course_cells[0] if course_cells else "",
        "course_hours": course_cells[2] if len(course_cells) > 2 else "",
        "department": course_cells[4] if len(course_cells) > 4 else "",
        "teacher_name": teacher_cells[0] if teacher_cells else "",
        "course_nature": "",
        "class_name": class_cells[0] if class_cells else "",
        "proposition_form": "",
        "exam_form": "",
        "separate_teaching_exam": "",
        "marking_form": "",
    }
    segments = ["<60", "60-69", "70-79", "80-89", "90-100"]
    distribution = []
    for index, segment in enumerate(segments):
        count = _int(count_cells[index] if index < len(count_cells) else "")
        ratio = _float(ratio_cells[index] if index < len(ratio_cells) else "")
        distribution.append({"segment": segment, "count": count, "ratio": ratio or 0.0})
    statistics_payload = {
        "average": _float(average_cells[0] if average_cells else ""),
        "standard_deviation": _float(average_cells[2] if len(average_cells) > 2 else ""),
        "maximum": _float(maximum_cells[0] if maximum_cells else ""),
        "minimum": _float(maximum_cells[2] if len(maximum_cells) > 2 else ""),
        "pass_rate": _float(maximum_cells[4] if len(maximum_cells) > 4 else ""),
    }
    return {
        "text": text,
        "fields": fields,
        "distribution": distribution,
        "statistics": statistics_payload,
        "source_format": "fine_report_rtf_doc",
    }


def _rounded_equal(left: float | None, right: float | None, tolerance: float = 0.02) -> bool:
    return left is not None and right is not None and abs(float(left) - float(right)) <= tolerance


def validate_paired_reports(
    grade: dict[str, Any],
    analysis: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
    remote_student_count: int = 0,
    accepted_metadata_keys: set[str] | None = None,
) -> dict[str, Any]:
    context = context or {}
    accepted_metadata_keys = set(accepted_metadata_keys or ())
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []

    def check(key: str, ok: bool, message: str, *, severity: str = "error") -> None:
        checks.append({"key": key, "ok": bool(ok), "message": message, "severity": severity})
        if not ok:
            (errors if severity == "error" else warnings).append(message)

    grade_fields = grade.get("fields") or {}
    analysis_fields = analysis.get("fields") or {}
    students = grade.get("students") or []
    student_numbers = [str(item.get("student_number") or "") for item in students]
    check("students_present", bool(students), "成绩登记表没有解析到学生成绩。")
    check(
        "student_numbers_unique",
        len(student_numbers) == len(set(student_numbers)),
        "成绩登记表存在重复学号。",
    )
    if remote_student_count:
        check(
            "remote_student_count",
            len(students) == int(remote_student_count),
            f"成绩登记表人数 {len(students)} 与教务考试名单 {remote_student_count} 不一致。",
        )
    for field_key, label in (
        ("course_name", "课程名称"),
        ("teacher_name", "教师姓名"),
        ("class_name", "班级"),
        ("academic_year", "学年"),
        ("semester", "学期"),
    ):
        left = _normalize_space(grade_fields.get(field_key))
        right = _normalize_space(analysis_fields.get(field_key))
        check_key = f"paired_{field_key}"
        check(
            check_key,
            _labels_equivalent(left, right) or check_key in accepted_metadata_keys,
            f"两份报表的{label}不一致（{left or '空'} / {right or '空'}）。",
        )
    for field_key, context_key, label in (
        ("course_name", "course_name", "课程"),
        ("teacher_name", "teacher_name", "教师"),
    ):
        expected = _normalize_space(context.get(context_key))
        actual = _normalize_space(grade_fields.get(field_key))
        if expected:
            check_key = f"context_{field_key}"
            check(
                check_key,
                _labels_equivalent(actual, expected) or check_key in accepted_metadata_keys,
                f"报表{label}“{actual}”与所选课堂“{expected}”不一致。",
            )

    final_scores: list[float] = []
    exam_scores: list[float] = []
    formula_checked = 0
    formula_failed = 0
    for student in students:
        score = _float(student.get("final_score"))
        if score is None or not (0 <= score <= 100):
            errors.append(f"学生 {student.get('student_number')} 的总评成绩无效。")
            continue
        final_scores.append(score)
        ordinary = _float(student.get("ordinary_score"))
        final_exam = _float(student.get("final_exam_score"))
        if final_exam is not None and 0 <= final_exam <= 100:
            exam_scores.append(final_exam)
        if ordinary is not None and final_exam is not None:
            formula_checked += 1
            if not _rounded_equal(score, ordinary * 0.4 + final_exam * 0.6, 0.03):
                formula_failed += 1
    check(
        "score_formula",
        formula_checked == 0 or formula_failed == 0,
        f"有 {formula_failed} 名学生的总评不符合“平时×40% + 期末×60%”。",
    )

    if exam_scores:
        computed_counts = [
            sum(1 for score in exam_scores if score < 60),
            sum(1 for score in exam_scores if 60 <= score < 70),
            sum(1 for score in exam_scores if 70 <= score < 80),
            sum(1 for score in exam_scores if 80 <= score < 90),
            sum(1 for score in exam_scores if score >= 90),
        ]
        analysis_counts = [int(item.get("count") or 0) for item in analysis.get("distribution") or []]
        check(
            "distribution_counts",
            computed_counts == analysis_counts,
            f"试卷分析表分段人数 {analysis_counts} 与成绩登记表重算结果 {computed_counts} 不一致。",
        )
        stats = analysis.get("statistics") or {}
        computed_average = round(statistics.fmean(exam_scores), 2)
        computed_std = round(statistics.stdev(exam_scores), 2) if len(exam_scores) > 1 else 0.0
        computed_max = max(exam_scores)
        computed_min = min(exam_scores)
        computed_pass = round(sum(1 for score in exam_scores if score >= 60) * 100 / len(exam_scores), 2)
        for key, label, computed in (
            ("average", "平均分", computed_average),
            ("standard_deviation", "标准差", computed_std),
            ("maximum", "最高分", computed_max),
            ("minimum", "最低分", computed_min),
            ("pass_rate", "及格率", computed_pass),
        ):
            check(
                f"statistics_{key}",
                _rounded_equal(_float(stats.get(key)), computed, 0.03),
                f"试卷分析表{label} {_float(stats.get(key))} 与重算结果 {computed} 不一致。",
            )
    else:
        computed_counts = [0, 0, 0, 0, 0]
        computed_average = computed_std = computed_max = computed_min = computed_pass = 0

    return {
        "status": "passed" if not errors else "failed",
        "passed": not errors,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "computed": {
            "student_count": len(exam_scores),
            "distribution_counts": computed_counts,
            "average": computed_average,
            "standard_deviation": computed_std,
            "maximum": computed_max,
            "minimum": computed_min,
            "pass_rate": computed_pass,
        },
    }


def build_grade_register_export_payload(
    parsed: dict[str, Any],
    validation: dict[str, Any],
    *,
    teacher_signature_id: int | None = None,
    teacher_signature_path: str = "",
) -> dict[str, Any]:
    fields = dict(parsed.get("fields") or {})
    fields.update(
        {
            "teacher_signature_id": teacher_signature_id,
            "teacher_signature_image_path": teacher_signature_path,
        }
    )
    distribution_counts = (validation.get("computed") or {}).get("distribution_counts") or [0, 0, 0, 0, 0]
    total = max(1, len(parsed.get("students") or []))
    distribution = [
        {
            "segment": segment,
            "count": int(distribution_counts[index] or 0),
            "ratio": round(int(distribution_counts[index] or 0) * 100 / total, 2),
        }
        for index, segment in enumerate(["<60", "60-69", "70-79", "80-89", "90-100"])
    ]
    return {
        "schema_version": "gxufl-academic-grade-register-v1",
        "template_key": ACADEMIC_GRADE_REGISTER_TYPE,
        "document_group": "final_material",
        "document_type": ACADEMIC_GRADE_REGISTER_TYPE,
        "document_type_label": ACADEMIC_GRADE_REGISTER_LABEL,
        "fields": fields,
        "structured": {
            "students": parsed.get("students") or [],
            "score_distribution": distribution,
            "statistics": validation.get("computed") or {},
            "formula": parsed.get("formula") or "平时*40% + 期末*60%",
            "validation": validation,
        },
        "layout_profile": {
            "page": "A4 portrait",
            "margins_cm": {"top": 0.4, "bottom": 0.4, "left": 0.5, "right": 0.5},
            "signature_mode": "inline_locked",
        },
    }


def build_exam_analysis_export_payload(
    parsed: dict[str, Any],
    validation: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = dict(parsed.get("fields") or {})
    fields.update(defaults or {})
    computed = validation.get("computed") or {}
    counts = computed.get("distribution_counts") or [0, 0, 0, 0, 0]
    total = max(1, int(computed.get("student_count") or 0))
    distribution = [
        {
            "segment": segment,
            "count": int(counts[index] or 0),
            "ratio": round(int(counts[index] or 0) * 100 / total, 2),
        }
        for index, segment in enumerate(["<60", "60-69", "70-79", "80-89", "90-100"])
    ]
    return {
        "schema_version": "gxufl-academic-exam-analysis-v1",
        "template_key": ACADEMIC_EXAM_ANALYSIS_TYPE,
        "document_group": "final_material",
        "document_type": ACADEMIC_EXAM_ANALYSIS_TYPE,
        "document_type_label": ACADEMIC_EXAM_ANALYSIS_LABEL,
        "fields": fields,
        "structured": {
            "score_distribution": distribution,
            "statistics": computed,
            "analysis_text": str((defaults or {}).get("analysis_text") or ""),
            "validation": validation,
        },
        "layout_profile": {
            "page": "A4 portrait",
            "margins_cm": {"top": 1.0, "bottom": 0.8, "left": 1.1, "right": 1.1},
            "signature_mode": "inline_locked",
        },
    }


def normalize_academic_final_material_payload(
    *,
    document_type: str,
    metadata: dict[str, Any] | None,
    export_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(export_payload or {})
    fields = dict(payload.get("fields") or {})
    for key, value in (metadata or {}).items():
        if value not in (None, "") and fields.get(key) in (None, ""):
            fields[key] = value
    payload.update(
        {
            "template_key": document_type,
            "document_group": "final_material",
            "document_type": document_type,
            "document_type_label": (
                ACADEMIC_GRADE_REGISTER_LABEL
                if document_type == ACADEMIC_GRADE_REGISTER_TYPE
                else ACADEMIC_EXAM_ANALYSIS_LABEL
            ),
            "fields": fields,
            "structured": dict(payload.get("structured") or {}),
        }
    )
    return payload


def build_content_markdown(export_payload: dict[str, Any]) -> str:
    fields = export_payload.get("fields") or {}
    structured = export_payload.get("structured") or {}
    lines = [
        f"# {export_payload.get('document_type_label') or '期末材料'}",
        "",
        f"- 课程名称：{fields.get('course_name') or ''}",
        f"- 班级：{fields.get('class_name') or ''}",
        f"- 任课教师：{fields.get('teacher_name') or ''}",
        f"- 学年学期：{fields.get('academic_year') or ''} {fields.get('semester') or ''}",
    ]
    if export_payload.get("document_type") == ACADEMIC_GRADE_REGISTER_TYPE:
        lines.extend(["", f"- 学生人数：{len(structured.get('students') or [])}", f"- 总评规则：{structured.get('formula') or ''}"])
    else:
        lines.extend(["", "## 试卷与成绩分析", "", str(structured.get("analysis_text") or "待生成")])
    return "\n".join(lines)


def build_parse_result_dict(
    export_payload: dict[str, Any],
    *,
    extraction_method: str,
    warnings: list[str] | None = None,
    ai_used: bool = False,
) -> dict[str, Any]:
    content_markdown = build_content_markdown(export_payload)
    return {
        "metadata": export_payload.get("fields") or {},
        "content_markdown": content_markdown,
        "tables": [],
        "warnings": warnings or [],
        "export_payload": export_payload,
        "document_group": "final_material",
        "document_type": export_payload.get("document_type"),
        "document_type_label": export_payload.get("document_type_label"),
        "extraction_method": extraction_method,
        "ai_used": ai_used,
    }


def is_grade_entry_submitted(status: Any, raw_row: dict[str, Any] | None = None) -> bool:
    normalized = re.sub(r"\s+", "", str(status or ""))
    if any(marker in normalized for marker in ("未提交", "未录入", "未完成", "待提交", "未锁定")):
        return False
    if any(marker in normalized for marker in ("已提交", "提交", "已录入", "已完成", "已锁定", "审核通过")):
        return True
    raw = raw_row or {}
    flags = [
        _field(raw, "cjsftj", "CJSFTJ"),
        _field(raw, "sftj", "SFTJ"),
        _field(raw, "tjzt", "TJZT"),
    ]
    if any(str(flag).strip().lower() in {"1", "true", "yes", "submitted"} for flag in flags):
        return True
    raw_status = re.sub(
        r"\s+",
        "",
        " ".join(
            str(value or "")
            for value in (
                _field(raw, "lrztmc", "LRZTMC"),
                _field(raw, "cjlrshzt", "CJLRSHZT"),
            )
        ),
    )
    if any(marker in raw_status for marker in ("未提交", "退回", "不通过")):
        return False
    return any(marker in raw_status for marker in ("提交", "审核通过"))


def _safe_report_url(value: str, *, base_url: str) -> str:
    candidate = html.unescape(str(value or "")).replace("\\/", "/").strip("'\" ")
    if candidate.startswith("/"):
        candidate = urljoin(base_url.rstrip("/") + "/", candidate.lstrip("/"))
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or parsed.hostname not in FINE_REPORT_ALLOWED_HOSTS:
        return ""
    if "ReportServer" not in parsed.path:
        return ""
    return candidate


def _html_attribute(tag: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*(['\"])(.*?)\1",
        str(tag or ""),
        re.IGNORECASE | re.DOTALL,
    )
    return html.unescape(match.group(2)).strip() if match else ""


def _extract_report_form(response: httpx.Response, *, base_url: str) -> tuple[str, dict[str, str]]:
    for match in re.finditer(
        r"<form\b(?P<attributes>[^>]*)>(?P<body>.*?)</form>",
        response.text,
        re.IGNORECASE | re.DOTALL,
    ):
        attributes = match.group("attributes")
        if _html_attribute(attributes, "id") != "reportSearchForm":
            continue
        action = _safe_report_url(_html_attribute(attributes, "action"), base_url=base_url)
        if not action:
            return "", {}
        payload: dict[str, str] = {}
        for input_match in list(re.finditer(r"<input\b[^>]*>", match.group("body"), re.IGNORECASE))[:32]:
            tag = input_match.group(0)
            name = _html_attribute(tag, "name")
            if name:
                payload[name] = _html_attribute(tag, "value")
        return action, payload
    return "", {}


def _extract_report_url(response: httpx.Response, report_id: str, *, base_url: str) -> str:
    candidates = [str(response.headers.get("location") or ""), str(response.url)]
    body = response.text
    candidates.extend(
        match.group(0)
        for match in re.finditer(
            r"https://(?:jwcjcx|jwxt)\.gxufl\.com(?::\d+)?/[^\s\"'<>]*ReportServer[^\s\"'<>]*",
            body,
            re.IGNORECASE,
        )
    )
    for candidate in candidates:
        safe = _safe_report_url(candidate, base_url=base_url)
        if safe:
            return safe
    return ""


def _find_fine_report_session_id(response: httpx.Response) -> str:
    candidates = [
        parse_qs(urlparse(str(response.url)).query).get("sessionID", [""])[0],
        response.cookies.get("sessionID", ""),
    ]
    body = response.text
    for pattern in (
        r"sessionID\s*[=:]\s*['\"]?(\d+)",
        r"sessionID(?:%3D|=)(\d+)",
        r"FR\.SessionMgr\.[A-Za-z]+\(\s*['\"]?(\d+)",
        r"sessionid\s*[=:]\s*['\"]?(\d+)",
    ):
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            candidates.append(match.group(1))
    for candidate in candidates:
        normalized = re.sub(r"\D", "", str(candidate or ""))
        if normalized:
            return normalized
    return ""


async def _download_fine_report_word(
    client: httpx.AsyncClient,
    *,
    report_id: str,
    teaching_class_id: str,
    teacher_org_id: str,
    source_summary: list[dict[str, Any]],
) -> bytes:
    init_response = await client.post(
        ZF_REPORT_INIT_PATH,
        params={
            "reportID": report_id,
            "gnmkdmKey": ZF_GRADE_ENTRY_FUNCTION_CODE,
            "_t": int(time.time() * 1000),
        },
        data={
            "mapRow.row.jxb_id": teaching_class_id,
            "mapRow.row.jgh_id": teacher_org_id,
        },
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": str(client.base_url).rstrip("/") + ZF_EXAM_COURSE_INDEX_PATH,
        },
    )
    if init_response.status_code >= 400:
        raise ValueError(f"教务系统打开{report_id}失败（HTTP {init_response.status_code}）。")
    if "无功能权限" in init_response.text:
        raise ValueError(f"教务系统拒绝打开{report_id}，当前账号缺少报表权限。")
    source_summary.append(
        {
            "path": ZF_REPORT_INIT_PATH,
            "method": "POST",
            "report_id": report_id,
            "status_code": init_response.status_code,
        }
    )
    report_url, report_form = _extract_report_form(init_response, base_url=str(client.base_url))
    if not report_url:
        report_url = _extract_report_url(init_response, report_id, base_url=str(client.base_url))
    if not report_url:
        raise ValueError(f"教务系统打开{report_id}后未返回有效报表地址。")
    if report_form:
        report_response = await client.post(report_url, data=report_form, follow_redirects=True)
        report_method = "POST"
    else:
        report_response = await client.get(report_url, follow_redirects=True)
        report_method = "GET"
    source_summary.append(
        {
            "path": urlparse(report_url).path,
            "method": report_method,
            "report_id": report_id,
            "status_code": report_response.status_code,
            "host": urlparse(str(report_response.url)).hostname or "",
        }
    )
    if _is_rtf_doc(report_response.content):
        return report_response.content

    session_id = _find_fine_report_session_id(report_response)
    parsed = urlparse(str(report_response.url))
    report_base = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    candidates: list[str] = []
    if session_id:
        candidates.append(f"{report_base}?{urlencode({'op': 'export', 'sessionID': session_id, 'format': 'word'})}")
    candidates.extend(
        [
            f"{report_base}?{urlencode({'reportlet': report_id, 'op': 'export', 'format': 'word'})}",
            report_url + ("&" if "?" in report_url else "?") + "op=export&format=word",
        ]
    )
    seen_urls: set[str] = set()
    for export_url in candidates:
        if export_url in seen_urls:
            continue
        seen_urls.add(export_url)
        response = await client.get(export_url, follow_redirects=True)
        source_summary.append(
            {
                "path": urlparse(export_url).path,
                "method": "GET",
                "report_id": report_id,
                "operation": "export_word",
                "status_code": response.status_code,
                "size": len(response.content),
            }
        )
        if response.status_code == 200 and _is_rtf_doc(response.content):
            return response.content
    raise ValueError(f"教务系统已打开{report_id}，但未能取得 Word 导出文件。")


def _load_batch(conn: Any, *, teacher_id: int, class_offering_id: int):
    ensure_academic_final_material_schema(conn)
    return conn.execute(
        """
        SELECT *
        FROM academic_final_material_batches
        WHERE teacher_id = ? AND class_offering_id = ?
        LIMIT 1
        """,
        (int(teacher_id), int(class_offering_id)),
    ).fetchone()


def load_fresh_cached_batch(teacher_id: int, class_offering_id: int) -> dict[str, Any] | None:
    with get_db_connection() as conn:
        row = _load_batch(conn, teacher_id=teacher_id, class_offering_id=class_offering_id)
        if not row or str(row["sync_status"] or "") != "completed":
            return None
        synced_at = str(row["synced_at"] or "")
        try:
            parsed = datetime.fromisoformat(synced_at)
        except ValueError:
            return None
        age = (datetime.now(parsed.tzinfo) - parsed).total_seconds() if parsed.tzinfo else (datetime.now() - parsed).total_seconds()
        if age >= ACADEMIC_FINAL_MATERIAL_CACHE_SECONDS:
            return None
        return serialize_batch(row)


def reclaim_stale_academic_final_material_batches(conn: Any, teacher_id: int) -> int:
    """Turn orphaned background jobs into retryable failures.

    A worker restart can interrupt an in-process synchronization after its state
    has already been persisted.  List requests call this cheap bounded update so
    the UI never polls an abandoned ``queued``/``running`` row forever.
    """
    ensure_academic_final_material_schema(conn)
    cutoff = (china_now().replace(tzinfo=None) - timedelta(seconds=ACADEMIC_FINAL_MATERIAL_STALE_SECONDS)).isoformat(
        timespec="seconds"
    )
    now = _now_iso()
    placeholders = ", ".join("?" for _ in ACADEMIC_FINAL_MATERIAL_ACTIVE_STATUSES)
    cursor = conn.execute(
        f"""
        UPDATE academic_final_material_batches
        SET sync_status = 'failed',
            last_error = '后台同步已中断，请重新同步。',
            updated_at = ?
        WHERE teacher_id = ?
          AND sync_status IN ({placeholders})
          AND updated_at < ?
        """,
        (
            now,
            int(teacher_id),
            *sorted(ACADEMIC_FINAL_MATERIAL_ACTIVE_STATUSES),
            cutoff,
        ),
    )
    return max(0, int(cursor.rowcount or 0))


def serialize_batch(row: Any) -> dict[str, Any]:
    item = dict(row)
    return {
        **item,
        "validation": _json_loads(item.get("validation_json"), {}),
        "edit_state": _json_loads(item.get("edit_state_json"), {}),
        "sync_options": _json_loads(item.get("sync_options_json"), {}),
        "source_summary": _json_loads(item.get("source_summary_json"), []),
        "grade_record_id": int(item.get("grade_record_id") or 0) or None,
        "analysis_record_id": int(item.get("analysis_record_id") or 0) or None,
        "class_offering_id": int(item.get("class_offering_id") or 0),
        "teacher_id": int(item.get("teacher_id") or 0),
    }


def upsert_batch_state(
    conn: Any,
    *,
    teacher_id: int,
    class_offering_id: int,
    values: dict[str, Any],
) -> dict[str, Any]:
    ensure_academic_final_material_schema(conn)
    now = _now_iso()
    allowed = {
        "exam_roster_item_id",
        "academic_year",
        "academic_term",
        "exam_course_key",
        "course_code",
        "course_name",
        "teaching_class_id",
        "teaching_class_name",
        "grade_entry_status",
        "sync_status",
        "grade_record_id",
        "analysis_record_id",
        "grade_source_hash",
        "analysis_source_hash",
        "grade_source_size",
        "analysis_source_size",
        "validation_status",
        "validation_json",
        "edit_state_json",
        "sync_options_json",
        "last_error",
        "source_summary_json",
        "synced_at",
    }
    updates = {key: value for key, value in values.items() if key in allowed}
    payload = {
        "id": str(uuid.uuid4()),
        "teacher_id": int(teacher_id),
        "class_offering_id": int(class_offering_id),
        "school_code": SCHOOL_CODE,
        **updates,
        "created_at": now,
        "updated_at": now,
    }
    columns = list(payload)
    placeholders = ", ".join("?" for _ in columns)
    conflict_assignments = ", ".join(
        [*(f"{key} = excluded.{key}" for key in updates), "updated_at = excluded.updated_at"]
    )
    conn.execute(
        f"""
        INSERT INTO academic_final_material_batches ({', '.join(columns)})
        VALUES ({placeholders})
        ON CONFLICT (teacher_id, class_offering_id)
        DO UPDATE SET {conflict_assignments}
        """,
        tuple(payload[column] for column in columns),
    )
    refreshed = _load_batch(conn, teacher_id=teacher_id, class_offering_id=class_offering_id)
    return serialize_batch(refreshed)


def _finish_sync_without_download(
    teacher_id: int,
    class_offering_id: int,
    *,
    status: str,
    message: str,
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with get_db_connection() as conn:
        batch = upsert_batch_state(
            conn,
            teacher_id=teacher_id,
            class_offering_id=class_offering_id,
            values={
                "sync_status": status,
                "last_error": message,
                **(values or {}),
            },
        )
        conn.commit()
    return batch


def list_teacher_final_material_candidates(conn: Any, teacher_id: int) -> list[dict[str, Any]]:
    ensure_academic_final_material_schema(conn)
    rows = conn.execute(
        """
        SELECT o.id AS class_offering_id,
               o.semester,
               o.semester_id,
               o.academic_teaching_class_name,
               c.name AS course_name,
               c.academic_course_code AS course_code,
               c.credits,
               cl.name AS class_name,
               cl.academic_class_name,
               b.id AS batch_id,
               b.sync_status,
               b.validation_status,
               b.grade_entry_status,
               b.synced_at,
               b.last_error
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        LEFT JOIN academic_final_material_batches b
          ON b.teacher_id = o.teacher_id AND b.class_offering_id = o.id
        WHERE o.teacher_id = ?
        ORDER BY (b.synced_at IS NULL) ASC,
                 b.synced_at DESC,
                 o.created_at DESC,
                 o.id DESC
        """,
        (int(teacher_id),),
    ).fetchall()
    return [
        {
            "class_offering_id": int(row["class_offering_id"]),
            "course_name": row["course_name"] or "",
            "course_code": row["course_code"] or "",
            "class_name": row["academic_class_name"] or row["class_name"] or "",
            "teaching_class_name": row["academic_teaching_class_name"] or "",
            "semester": row["semester"] or "",
            "credits": row["credits"],
            "batch_id": row["batch_id"] or "",
            "sync_status": row["sync_status"] or "not_synced",
            "validation_status": row["validation_status"] or "unchecked",
            "grade_entry_status": row["grade_entry_status"] or "",
            "synced_at": row["synced_at"] or "",
            "last_error": row["last_error"] or "",
        }
        for row in rows
    ]


def list_teacher_final_material_batches(
    conn: Any,
    teacher_id: int,
    *,
    document_type: str,
) -> list[dict[str, Any]]:
    ensure_academic_final_material_schema(conn)
    record_column = "grade_record_id" if document_type == ACADEMIC_GRADE_REGISTER_TYPE else "analysis_record_id"
    rows = conn.execute(
        f"""
        SELECT b.*,
               r.document_type_label,
               r.updated_at AS record_updated_at,
               r.content_quality_status
        FROM academic_final_material_batches b
        LEFT JOIN material_ai_import_records r ON r.id = b.{record_column}
        WHERE b.teacher_id = ?
        ORDER BY b.updated_at DESC, b.id DESC
        """,
        (int(teacher_id),),
    ).fetchall()
    items = []
    for row in rows:
        item = serialize_batch(row)
        record_id = item.get("grade_record_id") if document_type == ACADEMIC_GRADE_REGISTER_TYPE else item.get("analysis_record_id")
        item.update(
            {
                "record_id": record_id,
                "document_type": document_type,
                "document_type_label": (
                    ACADEMIC_GRADE_REGISTER_LABEL
                    if document_type == ACADEMIC_GRADE_REGISTER_TYPE
                    else ACADEMIC_EXAM_ANALYSIS_LABEL
                ),
                "export_url": f"/api/materials/ai-import-records/{record_id}/export?format=docx" if record_id else "",
                "preview_url": f"/api/materials/ai-import-records/{record_id}/render-preview?format=docx" if record_id else "",
            }
        )
        items.append(item)
    return items


async def sync_paired_reports_from_academic_system(
    teacher_id: int,
    class_offering_id: int,
    *,
    exam_course_key: str = "",
    force: bool = False,
) -> dict[str, Any]:
    teacher_id = int(teacher_id)
    class_offering_id = int(class_offering_id)
    lock = _sync_locks.setdefault((teacher_id, class_offering_id), asyncio.Lock())
    async with lock:
        if not force:
            cached = load_fresh_cached_batch(teacher_id, class_offering_id)
            if cached and cached.get("grade_record_id") and cached.get("analysis_record_id"):
                return {
                    "status": "cached",
                    "message": "已使用 30 分钟内的双表同步结果，本次未重复访问教务系统。",
                    "batch": cached,
                }

        with get_db_connection() as conn:
            access = load_teacher_academic_access_method(conn, teacher_id, school_code=SCHOOL_CODE)
            context = _load_offering_context(conn, teacher_id, class_offering_id)
            semester = _load_semester_for_offering(conn, teacher_id, context) if context else None
            upsert_batch_state(
                conn,
                teacher_id=teacher_id,
                class_offering_id=class_offering_id,
                values={"sync_status": "running", "sync_options_json": "{}", "last_error": ""},
            )
            conn.commit()
        if not context:
            message = "课堂不存在或无权访问。"
            _finish_sync_without_download(
                teacher_id,
                class_offering_id,
                status="failed",
                message=message,
            )
            return {"status": "not_found", "message": message}
        if not access:
            message = "请先在系统设置中配置并验证教务系统账号。"
            batch = _finish_sync_without_download(
                teacher_id,
                class_offering_id,
                status="needs_attention",
                message=message,
            )
            return {"status": "missing_credential", "message": message, "batch": batch}
        if not semester:
            message = "所选课堂没有可对齐的学期，请先完善课堂学期。"
            batch = _finish_sync_without_download(
                teacher_id,
                class_offering_id,
                status="needs_attention",
                message=message,
            )
            return {"status": "no_semester", "message": message, "batch": batch}

        sources: list[dict[str, Any]] = []
        try:
            async with open_authenticated_academic_client(access) as (client, _profile, _login):
                courses, course_sources, term_params = await _fetch_exam_courses(client, semester)
                sources.extend(course_sources)
                selected, candidates, needs_confirmation = _select_exam_course(
                    courses,
                    context,
                    requested_exam_course_key=_normalize_space(exam_course_key),
                )
                if needs_confirmation or selected is None:
                    message = "未能唯一确认课堂对应的教务成绩课程，请选择匹配项后继续。"
                    batch = _finish_sync_without_download(
                        teacher_id,
                        class_offering_id,
                        status="needs_confirmation",
                        message=message,
                        values={
                            "source_summary_json": _json_dumps(sources),
                            "sync_options_json": _json_dumps({"candidates": candidates}),
                        },
                    )
                    return {
                        "status": "needs_confirmation",
                        "message": message,
                        "candidates": candidates,
                        "batch": batch,
                    }
                if not is_grade_entry_submitted(selected.grade_entry_status, selected.raw_json):
                    message = (
                        f"《{selected.course_name}》当前成绩状态为"
                        f"“{selected.grade_entry_status or '未录入/未提交'}”，请先在教务系统完成并提交成绩。"
                    )
                    batch = _finish_sync_without_download(
                        teacher_id,
                        class_offering_id,
                        status="grades_missing",
                        message=message,
                        values={
                            "course_name": selected.course_name,
                            "course_code": selected.course_code,
                            "teaching_class_id": selected.teaching_class_id,
                            "teaching_class_name": selected.teaching_class_name,
                            "grade_entry_status": selected.grade_entry_status or "",
                            "source_summary_json": _json_dumps(sources),
                        },
                    )
                    return {
                        "status": "grades_missing",
                        "message": message,
                        "grade_entry_status": selected.grade_entry_status or "",
                        "batch": batch,
                    }
                selected.academic_year = selected.academic_year or (term_params or {}).get("xnm", "")
                selected.academic_term = selected.academic_term or (term_params or {}).get("xqm", "")
                remote_students = await _fetch_exam_students(client, selected, sources)
                teacher_org_id = _field(selected.raw_json, "jgh_id", "jgh", "JGH_ID", "JGH")
                grade_bytes = await _download_fine_report_word(
                    client,
                    report_id=ZF_GRADE_REGISTER_REPORT_ID,
                    teaching_class_id=selected.teaching_class_id,
                    teacher_org_id=teacher_org_id,
                    source_summary=sources,
                )
                analysis_bytes = await _download_fine_report_word(
                    client,
                    report_id=ZF_EXAM_ANALYSIS_REPORT_ID,
                    teaching_class_id=selected.teaching_class_id,
                    teacher_org_id=teacher_org_id,
                    source_summary=sources,
                )
        except (ValueError, httpx.HTTPError, json.JSONDecodeError) as exc:
            message = f"教务系统双表同步失败：{str(exc)[:240]}"
            with get_db_connection() as conn:
                upsert_batch_state(
                    conn,
                    teacher_id=teacher_id,
                    class_offering_id=class_offering_id,
                    values={"sync_status": "failed", "last_error": message, "source_summary_json": _json_dumps(sources)},
                )
                conn.commit()
            return {"status": "failed", "message": message}

        try:
            grade = parse_grade_register_rtf(grade_bytes)
            analysis = parse_exam_analysis_rtf(analysis_bytes)
            validation = validate_paired_reports(
                grade,
                analysis,
                context=context,
                remote_student_count=len(remote_students),
            )
        except (TypeError, ValueError) as exc:
            message = f"双表已下载，但结构化解析或交叉校验失败：{str(exc)[:240]}"
            with get_db_connection() as conn:
                upsert_batch_state(
                    conn,
                    teacher_id=teacher_id,
                    class_offering_id=class_offering_id,
                    values={
                        "sync_status": "failed",
                        "last_error": message,
                        "source_summary_json": _json_dumps(sources),
                    },
                )
                conn.commit()
            return {"status": "failed", "message": message}
        return {
            "status": "downloaded",
            "message": "已在同一教务会话中下载并解析两份报表。",
            "context": context,
            "course": asdict(selected),
            "remote_students": [asdict(item) for item in remote_students],
            "grade_bytes": grade_bytes,
            "analysis_bytes": analysis_bytes,
            "grade": grade,
            "analysis": analysis,
            "validation": validation,
            "source_summary": sources,
            "grade_hash": hashlib.sha256(grade_bytes).hexdigest(),
            "analysis_hash": hashlib.sha256(analysis_bytes).hexdigest(),
        }


def resolve_signature_path(conn: Any, signature_id: int | None) -> str:
    if not signature_id:
        return ""
    row = conn.execute(
        "SELECT * FROM electronic_signatures WHERE id = ? AND status = 'active' AND deleted_at IS NULL",
        (int(signature_id),),
    ).fetchone()
    if not row:
        return ""
    path = resolve_signature_file_path(row)
    return str(path) if path else ""


SYSTEM_CONSENT_ASSETS = (
    {
        "legacy_id": "final-material-consent-department-v1",
        "name": "系部审核意见·同意",
        "subject_name": "同意（行草）",
        "filename": "consent-department.png",
        "description": "期末材料系（教研室）审核意见默认手写“同意”",
    },
    {
        "legacy_id": "final-material-consent-dean-v1",
        "name": "教学院长审核意见·同意",
        "subject_name": "同意（楷行）",
        "filename": "consent-dean.png",
        "description": "期末材料教学院长审核意见默认手写“同意”",
    },
)


def ensure_system_consent_signatures(conn: Any) -> dict[str, dict[str, Any]]:
    """Seed two immutable, platform-usable opinion assets into the signature library."""
    from ..config import SIGNATURES_DIR
    from .signature_service import signature_relative_path

    result: dict[str, dict[str, Any]] = {}
    for index, config in enumerate(SYSTEM_CONSENT_ASSETS):
        row = conn.execute(
            """
            SELECT *
            FROM electronic_signatures
            WHERE legacy_source = 'system_final_material_opinion'
              AND legacy_id = ?
              AND status = 'active'
            ORDER BY id ASC
            LIMIT 1
            """,
            (config["legacy_id"],),
        ).fetchone()
        if row:
            path = resolve_signature_file_path(row)
            result["department" if index == 0 else "dean"] = {"id": int(row["id"]), "path": str(path or "")}
            continue

        asset_path = STATIC_DIR / "images" / "system_signatures" / config["filename"]
        data = asset_path.read_bytes()
        file_hash = hashlib.sha256(data).hexdigest()
        relative = signature_relative_path(file_hash, ".png")
        target = SIGNATURES_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
        signature_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO electronic_signatures (
                name, subject_name, subject_role, scope_level,
                owner_role, owner_id, owner_name_snapshot,
                uploaded_by_role, uploaded_by_id, uploaded_by_name_snapshot,
                school_code, school_name, college, department,
                file_hash, file_ext, mime_type, stored_path, file_size,
                description, legacy_source, legacy_id, metadata_json
            )
            VALUES (?, ?, 'system', 'platform', 'system', NULL, 'LanShare',
                    'system', NULL, 'LanShare', 'gxufl', '广西外国语学院', '', '',
                    ?, '.png', 'image/png', ?, ?, ?,
                    'system_final_material_opinion', ?, ?)
            """,
            (
                config["name"],
                config["subject_name"],
                file_hash,
                str(relative).replace("\\", "/"),
                len(data),
                config["description"],
                config["legacy_id"],
                _json_dumps({"locked": True, "font_variant": "department" if index == 0 else "dean"}),
            ),
        )
        result["department" if index == 0 else "dean"] = {"id": signature_id, "path": str(target)}
    return result
