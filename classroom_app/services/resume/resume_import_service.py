"""Resume import parsing and merge workflow.

The import path mirrors the mature material-import pipeline: extract local text
and page images first, ask the deep model for strict JSON, repair malformed JSON
only when there is useful output, then merge the normalized payload into the
student-owned resume profile without overwriting existing data.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ...core import ai_client
from ...database import get_db_connection
from ...db.connection import execute_insert_returning_id
from ..file_service import resolve_global_file_path
from ..material_ai_import_service import MAX_VISION_IMAGES, extract_material_content
from . import resume_ai_service as ai
from . import resume_document_service as docs
from . import resume_profile_service as profile
from . import resume_render_service as render

ALLOWED_EXTENSIONS = {".doc", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MAX_IMPORT_BYTES = 20 * 1024 * 1024
TEXT_BUDGET = 90_000
AI_TIMEOUT = 240.0


class ResumeImportResourceLimit(HTTPException):
    def __init__(self):
        super().__init__(413, "简历页数、图片尺寸或解压后内容过大，请精简后重新上传")


class ResumeImportInvalidDocument(HTTPException):
    def __init__(self):
        super().__init__(422, "文件内容无效或受密码保护，请上传可正常打开的原件")

_MONTH_RE = re.compile(r"(?P<year>19\d{2}|20\d{2})\D{0,3}(?P<month>0?[1-9]|1[0-2])?")
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\s,，。；;：:、·.\-_/\\|（）()【】\[\]{}<>《》\"']")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def validate_import_file(filename: str, mime_type: str = "", file_size: int = 0) -> dict[str, str]:
    safe_name = Path(str(filename or "resume")).name
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "仅支持 Word、PDF、PNG/JPG/WebP 等图片格式的简历导入")
    if file_size and int(file_size) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "简历文件不能超过 20MB")
    guessed = mimetypes.guess_type(safe_name)[0] or ""
    return {"filename": safe_name, "extension": ext, "mime_type": (mime_type or guessed or "").split(";", 1)[0]}


async def validate_upload_stream(file, *, max_bytes: int = MAX_IMPORT_BYTES) -> int:
    """Bound reads before hashing/publishing to the shared file store."""
    total = 0
    signature = b""
    try:
        while True:
            chunk = await file.read(min(1024 * 1024, max_bytes - total + 1))
            if not chunk:
                break
            if not signature:
                signature = chunk[:16]
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(413, f"文件不能超过 {max_bytes // (1024 * 1024)}MB")
        if not total:
            raise HTTPException(400, "上传文件为空")
        ext = Path(str(file.filename or "")).suffix.lower()
        valid = {
            ".pdf": signature.startswith(b"%PDF"),
            ".docx": signature.startswith(b"PK"),
            ".doc": signature.startswith(bytes.fromhex("d0cf11e0a1b11ae1")),
            ".png": signature.startswith(b"\x89PNG\r\n\x1a\n"),
            ".jpg": signature.startswith(b"\xff\xd8\xff"),
            ".jpeg": signature.startswith(b"\xff\xd8\xff"),
            ".gif": signature.startswith((b"GIF87a", b"GIF89a")),
            ".webp": signature.startswith(b"RIFF") and signature[8:12] == b"WEBP",
            ".bmp": signature.startswith(b"BM"),
        }.get(ext, False)
        if not valid:
            raise HTTPException(415, "文件内容与扩展名不一致，请上传有效的文档或图片")
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            await file.seek(0)
            await asyncio.to_thread(_check_image_input, file.file)
        return total
    finally:
        await file.seek(0)


async def execute_import_candidate(job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Extract and parse only. Student material is untouched until acceptance."""
    def read():
        from .resume_generation_service import _current_resume
        from ..student_career_job_service import SupersededCareerJob
        with get_db_connection() as conn:
            resume = _current_resume(conn, job, payload)
            if not resume:
                raise SupersededCareerJob()
            return resume
    resume = await asyncio.to_thread(read)
    source_path = resolve_global_file_path(str(resume.get("source_file_hash") or ""))
    if not source_path:
        raise ValueError("导入原件不存在")
    extracted = await asyncio.to_thread(_extract_resume_file, source_path, resume["source_filename"])
    if not extracted["file_texts"] and not extracted["images"]:
        raise ValueError("未提取到可识别内容，请上传更清晰的文件")
    parsed = normalize_resume_import_payload(await _parse_with_ai(extracted, resume["source_filename"]))
    if _payload_is_empty(parsed):
        raise ValueError("未识别到可用简历内容")
    parsed["warnings"] = _merge_text_list(parsed.get("warnings"), extracted.get("warnings"))
    return {"parsed": parsed, "source_filename": resume["source_filename"], "message": "解析完成，请检查识别内容后导入资料库。"}


def accept_import_candidate(conn, student_id: int, resume_id: int, candidate_id: int, expected_revision: Any, *, selections: Any = None) -> int:
    resume = docs.get_resume(conn, student_id, resume_id)
    revision = docs.require_revision(resume, expected_revision)
    candidate = docs.get_candidate(conn, student_id, resume_id, candidate_id)
    if candidate["kind"] != "import" or candidate["status"] != "pending" or int(candidate["base_revision"]) != revision:
        raise docs.ResumeConflict("导入候选已处理或简历已变化，请重新载入。")
    # Lock/CAS before any profile writes, so duplicate accept requests cannot
    # both add materials. All subsequent changes share this transaction.
    result = conn.execute("UPDATE resumes SET status = 'import_applying' WHERE id = ? AND student_id = ? AND revision = ? AND archived = 0",
                          (int(resume_id), int(student_id), revision))
    if result.rowcount != 1:
        raise docs.ResumeConflict("简历已变化，请重新载入。")
    claimed = conn.execute("UPDATE resume_candidates SET status = 'accepting' WHERE id = ? AND student_id = ? AND status = 'pending'", (int(candidate_id), int(student_id)))
    if claimed.rowcount != 1:
        raise docs.ResumeConflict("该导入已处理，请刷新查看。")
    parsed = dict(candidate["payload"]["parsed"])
    choices = selections if isinstance(selections, dict) else {}
    sections = choices.get("selected_sections")
    selected_items = choices.get("selected_items") if isinstance(choices.get("selected_items"), dict) else {}
    for section in ("personal", "self_intro", "education", "experience", "skill", "certificate"):
        if isinstance(sections, list) and section not in sections:
            parsed[section] = {} if section == "personal" else []
        elif section in selected_items and isinstance(selected_items[section], list):
            indices = {int(x) for x in selected_items[section] if str(x).isdigit()}
            parsed[section] = [item for index, item in enumerate(parsed.get(section) or []) if index in indices]
    if isinstance(choices.get("selected_personal_fields"), list):
        parsed["personal"] = {key: value for key, value in (parsed.get("personal") or {}).items() if key in choices["selected_personal_fields"]}
    if _payload_is_empty(parsed):
        raise ValueError("请至少选择一项要导入的内容")
    summary = merge_resume_import_payload(conn, student_id, parsed, source_filename=resume.get("source_filename", ""))
    bundle = profile.collect_profile_bundle(conn, student_id)
    target = _target_position(parsed, bundle)
    document = _build_resume_doc(conn, student_id, resume, parsed, summary, target, [])
    docs.update_resume(conn, student_id, resume_id, title=document["title"], template_key=document["template_key"],
                       layout=document["layout"], target_position=target, expected_revision=revision, refresh_materials=True, optimized_summary_md="", tech_stack=[])
    docs.save_import_summary(conn, resume_id, document["import_summary"])
    conn.execute("UPDATE resume_candidates SET status = 'accepted', updated_at = ? WHERE id = ? AND student_id = ?", (_now(), int(candidate_id), int(student_id)))
    return revision + 1




def _student_context(conn, student_id: int) -> dict[str, Any]:
    try:
        from ..career_path_service import resolve_student_context

        return resolve_student_context(conn, int(student_id)) or {}
    except Exception:
        return {}


def _extract_resume_file(file_path: Path, filename: str) -> dict[str, Any]:
    _check_input_resource_budget(file_path, filename)
    extraction = extract_material_content(file_path, filename)
    text = str(extraction.text or "").strip()
    file_texts = [{"name": filename, "content": text[:TEXT_BUDGET]}] if text else []
    images: list[str] = []
    for item in extraction.images or []:
        if isinstance(item, dict):
            data_url = item.get("data_url")
        else:
            data_url = str(item or "")
        if data_url and len(images) < MAX_VISION_IMAGES:
            images.append(data_url)
    warnings = list(extraction.warnings or [])
    if getattr(extraction, "truncated", False):
        warnings.append("文件内容较长，已截断后交给 AI 解析。")
    return {
        "file_texts": file_texts,
        "images": images,
        "warnings": warnings,
        "method": extraction.method,
        "source_kind": extraction.source_kind,
    }


def _check_input_resource_budget(file_path: Path, filename: str) -> None:
    """Bound expanded input before the shared parser allocates document data."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(file_path) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(item.file_size for item in entries) > 80 * 1024 * 1024 or any(item.file_size > 20 * 1024 * 1024 for item in entries):
                    raise ResumeImportResourceLimit()
                if "word/document.xml" not in archive.namelist() or any(item.flag_bits & 1 for item in entries):
                    raise ResumeImportInvalidDocument()
        except zipfile.BadZipFile as exc:
            raise ResumeImportInvalidDocument() from exc
    elif suffix == ".pdf":
        import fitz
        try:
            with fitz.open(file_path) as document:
                if document.needs_pass or document.page_count <= 0:
                    raise ResumeImportInvalidDocument()
                if document.page_count > 40:
                    raise ResumeImportResourceLimit()
        except HTTPException:
            raise
        except Exception as exc:
            raise ResumeImportInvalidDocument() from exc
    elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
        _check_image_input(file_path)


def _check_image_input(source: Any) -> None:
    from PIL import Image
    try:
        with Image.open(source) as picture:
            if picture.width * picture.height > 25_000_000:
                raise ResumeImportResourceLimit()
    except HTTPException:
        raise
    except Image.DecompressionBombError as exc:
        raise ResumeImportResourceLimit() from exc
    except Exception as exc:
        raise ResumeImportInvalidDocument() from exc


async def _parse_with_ai(extracted: dict[str, Any], filename: str) -> dict[str, Any] | None:
    images = extracted.get("images") or []
    file_texts = extracted.get("file_texts") or []
    text_chars = sum(len(str(item.get("content") or "")) for item in file_texts if isinstance(item, dict))
    use_vision = bool(images) and text_chars < 12000
    payload = {
        "system_prompt": _build_import_system_prompt(),
        "messages": [],
        "new_message": (
            f"请解析上传的简历文件《{filename}》。"
            "只做忠实识别和结构化，不要补写不存在的经历、技能、证书或时间。"
        ),
        "file_texts": file_texts,
        "base64_urls": images if use_vision else [],
        "model_capability": "vision" if use_vision else "thinking",
        "task_type": "document_multimodal_understanding" if use_vision else "deep_text_reasoning",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": "resume:import-parse",
    }
    response = await ai_client.post("/api/ai/chat", json=payload, timeout=AI_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    parsed = _json_from_ai_payload(data)
    if parsed:
        return parsed
    return await _repair_json_text(data.get("response_text"))


def _build_import_system_prompt() -> str:
    return (
        "你是严谨的简历解析器。请从学生上传的中文或中英混合简历中抽取事实信息，返回一个 JSON 对象。"
        "必须使用这些字段："
        "personal{name,gender,birthday,phone,email,qq,wechat,address,hometown,id_card,expected_position,expected_industry,expected_salary},"
        "self_intro[{title,content_md}],"
        "education[{kind,school,college,major,degree,start_date,end_date,content}],"
        "experience[{kind,title,start_date,end_date,role,content,contribution,achievement}],"
        "skill[{name,level,acquired_date,expiry_date,description}],"
        "certificate[{name,acquired_date,expiry_date,description}],"
        "tech_stack[{group,items}],target_position,warnings。"
        f"kind 只能用 education: high_school/university/training，experience: {'/'.join(profile.EXPERIENCE_KINDS)}。"
        "degree只记录明确写出的高中/中专/大专/本科/硕士/博士，不根据大学名称推断学历。"
        "日期尽量输出 YYYY-MM；只有年份就 YYYY；正在进行可写“至今”。"
        "个人介绍必须是简历中的职业摘要/自我评价原文或忠实压缩，不要扩写。"
        "技能和证书不要混淆：技术能力放 skill，真实证书/等级考试/资格证放 certificate。"
        "不要编造、不美化、不推断敏感信息。缺失字段留空字符串，缺失数组用 []。只返回 JSON。"
    )


def _loads_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.DOTALL | re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", raw):
        try:
            parsed, _ = decoder.raw_decode(raw[match.start():])
            return parsed
        except (TypeError, json.JSONDecodeError):
            continue
    return None


def _json_from_ai_payload(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    for key in ("response_json", "json", "data"):
        parsed = _loads_json(data.get(key))
        if isinstance(parsed, dict):
            return parsed
    parsed = _loads_json(data.get("response_text"))
    return parsed if isinstance(parsed, dict) else None


async def _repair_json_text(raw_text: Any) -> dict[str, Any] | None:
    raw = str(raw_text or "").strip()
    if not raw:
        return None
    payload = {
        "system_prompt": (
            "You repair malformed JSON from a resume parser. Extract only useful data from the user's text "
            "and return valid JSON with the same resume import schema. Do not invent fields."
        ),
        "messages": [],
        "new_message": raw[:24000],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "web_search_enabled": False,
        "task_priority": "background",
        "task_label": "resume:import-parse:json-repair",
    }
    try:
        response = await ai_client.post("/api/ai/chat", json=payload, timeout=90.0)
        response.raise_for_status()
        return _json_from_ai_payload(response.json())
    except Exception:
        return None


def normalize_resume_import_payload(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    personal = _coerce_personal(data.get("personal") or data.get("个人信息") or {})
    result: dict[str, Any] = {
        "personal": personal,
        "target_position": _clean(data.get("target_position") or personal.get("expected_position"), 120),
        "self_intro": [_coerce_self_intro(x) for x in _as_list(data.get("self_intro") or data.get("self_intros") or data.get("个人介绍"))],
        "education": [_coerce_education(x) for x in _as_list(data.get("education") or data.get("educations") or data.get("学习经历") or data.get("教育经历"))],
        "experience": [_coerce_experience(x) for x in _as_list(data.get("experience") or data.get("experiences") or data.get("项目经历") or data.get("项目比赛"))],
        "skill": [_coerce_skill(x) for x in _as_list(data.get("skill") or data.get("skills") or data.get("技能"))],
        "certificate": [_coerce_certificate(x) for x in _as_list(data.get("certificate") or data.get("certificates") or data.get("证书"))],
        "tech_stack": _coerce_tech_stack(data.get("tech_stack") or data.get("技术栈")),
        "warnings": [_clean(x, 180) for x in _as_list(data.get("warnings")) if _clean(x, 180)],
    }
    for key in ("self_intro", "education", "experience", "skill", "certificate"):
        result[key] = [item for item in result[key] if _section_has_identity(key, item)]
    return result


def _payload_is_empty(payload: dict[str, Any]) -> bool:
    personal = payload.get("personal") if isinstance(payload.get("personal"), dict) else {}
    if any(str(personal.get(k) or "").strip() for k in ("name", "email", "phone", "expected_position")):
        return False
    return not any(payload.get(k) for k in ("self_intro", "education", "experience", "skill", "certificate"))


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, "", {}):
        return []
    return [value]


def _clean(value: Any, limit: int = 2000) -> str:
    text = _SPACE_RE.sub(" ", str(value if value is not None else "").strip())
    return text[:limit]


def _pick(data: dict[str, Any], *keys: str, limit: int = 2000) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return _clean(value, limit)
    return ""


def _norm_month(value: Any) -> str:
    raw = _clean(value, 40)
    if not raw:
        return ""
    if any(word in raw for word in ("至今", "现在", "当前", "present", "Present")):
        return "至今"
    match = _MONTH_RE.search(raw)
    if not match:
        return raw[:20]
    year = match.group("year")
    month = match.group("month")
    if not month:
        return year
    return f"{year}-{int(month):02d}"


def _coerce_personal(value: Any) -> dict[str, str]:
    data = value if isinstance(value, dict) else {}
    return {
        "name": _pick(data, "name", "姓名", limit=80),
        "gender": _pick(data, "gender", "性别", limit=20),
        "birthday": _norm_month(data.get("birthday") or data.get("出生年月") or data.get("生日")),
        "phone": _pick(data, "phone", "mobile", "手机号", "电话", limit=40),
        "email": _pick(data, "email", "邮箱", limit=120),
        "qq": _pick(data, "qq", "QQ", limit=40),
        "wechat": _pick(data, "wechat", "微信", limit=80),
        "address": _pick(data, "address", "住址", "现居地址", limit=200),
        "hometown": _pick(data, "hometown", "籍贯", limit=120),
        "id_card": _pick(data, "id_card", "身份证号", limit=60),
        "expected_position": _pick(data, "expected_position", "target_position", "期望岗位", "求职意向", limit=120),
        "expected_industry": _pick(data, "expected_industry", "期望行业", limit=120),
        "expected_salary": _pick(data, "expected_salary", "期望薪资", limit=120),
    }


def _coerce_self_intro(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        return {"title": "导入自我介绍", "content_md": _clean(value, 4000), "source": "import"}
    data = value if isinstance(value, dict) else {}
    return {
        "title": _pick(data, "title", "标题", limit=120) or "导入自我介绍",
        "content_md": _pick(data, "content_md", "content", "text", "内容", limit=4000),
        "source": "import",
    }


def _coerce_education(value: Any) -> dict[str, str]:
    data = value if isinstance(value, dict) else {}
    return {
        "kind": _normalize_education_kind(_pick(data, "kind", "type", "类型", limit=40)),
        "school": _pick(data, "school", "学校", "学校名称", "机构", limit=160),
        "college": _pick(data, "college", "学院", "院系", limit=160),
        "major": _pick(data, "major", "专业", limit=160),
        "degree": _pick(data, "degree", "学历", "学位", limit=40),
        "start_date": _norm_month(data.get("start_date") or data.get("开始时间") or data.get("start")),
        "end_date": _norm_month(data.get("end_date") or data.get("结束时间") or data.get("end")),
        "content": _pick(data, "content", "description", "学习内容", "主修课程", limit=2000),
        "source": "import",
    }


def _coerce_experience(value: Any) -> dict[str, str]:
    data = value if isinstance(value, dict) else {}
    return {
        "kind": _normalize_experience_kind(_pick(data, "kind", "type", "类型", limit=40)),
        "title": _pick(data, "title", "name", "项目名称", "比赛名称", "名称", limit=160),
        "start_date": _norm_month(data.get("start_date") or data.get("开始时间") or data.get("start")),
        "end_date": _norm_month(data.get("end_date") or data.get("结束时间") or data.get("end")),
        "role": _pick(data, "role", "个人角色", "角色", limit=400),
        "content": _pick(data, "content", "description", "项目内容", "比赛内容", "内容", limit=2500),
        "contribution": _pick(data, "contribution", "个人贡献", "贡献", limit=2500),
        "achievement": _pick(data, "achievement", "result", "成果", "获得成绩", limit=1200),
    }


def _coerce_skill(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        return {"name": _clean(value, 120), "level": "", "acquired_date": "", "expiry_date": "", "description": ""}
    data = value if isinstance(value, dict) else {}
    return {
        "name": _pick(data, "name", "技能名称", "skill", limit=120),
        "level": _pick(data, "level", "熟练度", "等级", limit=80),
        "acquired_date": _norm_month(data.get("acquired_date") or data.get("获得时间") or data.get("time")),
        "expiry_date": _norm_month(data.get("expiry_date") or data.get("有效期")),
        "description": _pick(data, "description", "说明", "描述", limit=1200),
    }


def _coerce_certificate(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        return {"name": _clean(value, 160), "acquired_date": "", "expiry_date": "", "description": ""}
    data = value if isinstance(value, dict) else {}
    return {
        "name": _pick(data, "name", "证书名称", "certificate", limit=160),
        "acquired_date": _norm_month(data.get("acquired_date") or data.get("获得时间") or data.get("time")),
        "expiry_date": _norm_month(data.get("expiry_date") or data.get("有效期")),
        "description": _pick(data, "description", "说明", "描述", limit=1200),
    }


def _coerce_tech_stack(value: Any) -> list[dict[str, Any]]:
    groups = ai._coerce_tech_groups(value)
    return groups


def _merge_text_list(*groups: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in _as_list(group):
            text = _clean(item, 180)
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
            if len(merged) >= 20:
                return merged
    return merged


def _normalize_education_kind(value: str) -> str:
    raw = value.lower()
    if "高中" in value or "high" in raw:
        return "high_school"
    if "培训" in value or "training" in raw:
        return "training"
    return "university"


def _normalize_experience_kind(value: str) -> str:
    raw = value.lower()
    if raw in profile.EXPERIENCE_KINDS:
        return raw
    aliases = {"internship": ("实习",), "course": ("课程",), "campus": ("社团", "学生工作"),
               "volunteer": ("志愿", "义工"), "part_time": ("兼职",), "research": ("调研", "科研"), "employment": ("全职", "正式工作", "employment", "full_time")}
    for kind, labels in aliases.items():
        if raw == kind or any(label in value for label in labels):
            return kind
    if "比赛" in value or "竞赛" in value or "competition" in raw or "contest" in raw:
        return "competition"
    return "project"


def _section_has_identity(section: str, item: dict[str, Any]) -> bool:
    if section == "self_intro":
        return bool(item.get("content_md"))
    if section in {"skill", "certificate"}:
        return bool(item.get("name"))
    if section == "education":
        return bool(item.get("school") or item.get("major") or item.get("content"))
    if section == "experience":
        return bool(item.get("title") or item.get("content") or item.get("achievement"))
    return False


def merge_resume_import_payload(
    conn,
    student_id: int,
    payload: dict[str, Any],
    *,
    source_filename: str = "",
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "source": "import",
        "source_filename": source_filename,
        "added": {},
        "updated": {},
        "matched": {},
        "conflicts": [],
        "warnings": list(payload.get("warnings") or []),
        "selected": {},
    }
    personal_result = profile.merge_personal_info_partial(conn, student_id, payload.get("personal") or {})
    if personal_result["updated_fields"]:
        summary["updated"]["personal"] = personal_result["updated_fields"]
    for conflict in personal_result["conflicts"]:
        summary["conflicts"].append({"section": "personal", **conflict})
    for field in personal_result["skipped_fields"]:
        summary["warnings"].append(f"个人信息字段 {field} 格式不完整，已跳过。")

    for section in ("self_intro", "education", "experience", "skill", "certificate"):
        added_ids: list[int] = []
        updated_ids: list[int] = []
        matched_ids: list[int] = []
        existing = profile.list_section(conn, student_id, section)
        for item in payload.get(section) or []:
            match = _find_similar_item(section, item, existing)
            if match:
                update_info = _fill_blank_section_fields(conn, student_id, section, match, item)
                if update_info["updated_fields"]:
                    updated_ids.append(int(match["id"]))
                    match = profile.get_section_item(conn, student_id, section, int(match["id"]))
                    _replace_existing(existing, match)
                else:
                    matched_ids.append(int(match["id"]))
                for conflict in update_info["conflicts"]:
                    summary["conflicts"].append({"section": section, "existing_id": int(match["id"]), **conflict})
                continue
            new_id = _insert_import_section(conn, student_id, section, item)
            if new_id:
                added_ids.append(new_id)
                existing.append(profile.get_section_item(conn, student_id, section, new_id))
        if added_ids:
            summary["added"][section] = added_ids
        if updated_ids:
            summary["updated"][section] = updated_ids
        if matched_ids:
            summary["matched"][section] = matched_ids
        summary["selected"][section] = added_ids + updated_ids + matched_ids
    profile._notify_profile_change(conn, student_id)
    return summary


def accept_import_conflict(conn, student_id: int, resume_id: int, conflict_index: int) -> dict[str, Any]:
    """Apply one import conflict's incoming value and refresh the rendered resume."""
    resume = docs.get_resume(conn, student_id, resume_id)
    locked = conn.execute("UPDATE resumes SET revision = revision WHERE id = ? AND student_id = ? AND revision = ? AND archived = 0", (int(resume_id), int(student_id), int(resume["revision"])))
    if locked.rowcount != 1:
        raise docs.ResumeConflict("简历已更新，请重新核对导入冲突。")
    summary = resume.get("import_summary") if isinstance(resume.get("import_summary"), dict) else {}
    conflicts = summary.get("conflicts") if isinstance(summary.get("conflicts"), list) else []
    index = int(conflict_index)
    if index < 0 or index >= len(conflicts) or not isinstance(conflicts[index], dict):
        raise ValueError("未找到该导入冲突")
    conflict = dict(conflicts[index])
    if conflict.get("accepted"):
        return {"summary": summary, "conflict": conflict, "changed": False}

    section = str(conflict.get("section") or "").strip()
    field = str(conflict.get("field") or "").strip()
    incoming = _clean(conflict.get("incoming"), 8000 if field in {"content_md", "content", "contribution"} else 2000)
    if not section or not field or not incoming:
        raise ValueError("该冲突缺少可应用的导入值")

    if section == "personal":
        _apply_personal_conflict(conn, student_id, field, incoming, expected=str(conflict.get("existing") or ""))
    else:
        existing_id = int(conflict.get("existing_id") or 0)
        _apply_section_conflict(conn, student_id, section, existing_id, field, incoming, expected=str(conflict.get("existing") or ""))

    profile._notify_profile_change(conn, student_id)

    conflict["accepted"] = True
    conflict["resolved_at"] = _now()
    conflicts[index] = conflict
    summary["conflicts"] = conflicts
    summary["message"] = _summary_message(summary)
    docs.save_import_summary(conn, resume_id, summary)

    revision = docs.update_resume(conn, student_id, resume_id, title=resume["title"], template_key=resume["template_key"],
                                  layout=resume.get("layout"), target_position=resume["target_position"], expected_revision=resume["revision"],
                                  content_overrides=[{"section": section, "id": conflict.get("existing_id", 0), "fields": {field: incoming}}])
    version = docs.get_version(conn, student_id, resume_id, revision)
    html = render.assemble_resume_html(None, student_id, docs.snapshot_resume(version))
    docs.save_version_render(conn, student_id, resume_id, revision, html)
    return {"summary": summary, "conflict": conflict, "changed": True, "revision": revision}


def _apply_personal_conflict(conn, student_id: int, field: str, incoming: str, *, expected: str = "") -> None:
    if field not in profile.PERSONAL_FIELDS:
        raise ValueError("该个人信息字段不能自动更新")
    profile.get_personal_info(conn, student_id)
    result = conn.execute(
        f"UPDATE resume_personal_info SET {field} = ?, revision = revision + 1, updated_at = ? WHERE student_id = ? AND {field} = ?",
        (incoming[:200], _now(), int(student_id), expected),
    )
    if result.rowcount != 1:
        raise docs.ResumeConflict("该字段在解析后已修改，请重新核对，系统未覆盖你的新内容。")


def _apply_section_conflict(conn, student_id: int, section: str, item_id: int, field: str, incoming: str, *, expected: str = "") -> None:
    if section not in profile.LIST_SECTIONS:
        raise ValueError("该资料分区不能自动更新")
    spec = profile.LIST_SECTIONS[section]
    if field not in spec["fields"]:
        raise ValueError("该资料字段不能自动更新")
    profile.get_section_item(conn, student_id, section, item_id)
    limit = 8000 if field in {"content_md", "content", "contribution"} else 2000
    result = conn.execute(
        f"UPDATE {spec['table']} SET {field} = ?, revision = revision + 1, updated_at = ? WHERE id = ? AND student_id = ? AND {field} = ?",
        (incoming[:limit], _now(), int(item_id), int(student_id), expected),
    )
    if result.rowcount != 1:
        raise docs.ResumeConflict("该字段在解析后已修改，请重新核对，系统未覆盖你的新内容。")


def _insert_import_section(conn, student_id: int, section: str, item: dict[str, Any]) -> int | None:
    if not _section_has_identity(section, item):
        return None
    spec = profile.LIST_SECTIONS[section]
    now = _now()
    cleaned = {field: _clean(item.get(field), 8000 if field in {"content_md", "content", "contribution"} else 2000)
               for field in spec["fields"]}
    columns = list(cleaned.keys()) + ["student_id", "created_at", "updated_at"]
    values = list(cleaned.values()) + [int(student_id), now, now]
    placeholders = ", ".join("?" for _ in columns)
    return int(
        execute_insert_returning_id(
            conn,
            f"INSERT INTO {spec['table']} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
    )


def _fill_blank_section_fields(
    conn,
    student_id: int,
    section: str,
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    spec = profile.LIST_SECTIONS[section]
    updates: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    for field in spec["fields"]:
        value = _clean(incoming.get(field), 8000 if field in {"content_md", "content", "contribution"} else 2000)
        if not value:
            continue
        current = _clean(existing.get(field), 8000 if field in {"content_md", "content", "contribution"} else 2000)
        if not current:
            updates[field] = value
        elif current != value and field not in _identity_fields(section) and field not in {"source"}:
            conflicts.append({"field": field, "existing": current, "incoming": value})
    if updates:
        table = spec["table"]
        assignments = ", ".join(f"{field} = ?" for field in updates)
        params = list(updates.values()) + [_now(), int(existing["id"]), int(student_id), int(existing.get("revision") or 1)]
        result = conn.execute(
            f"UPDATE {table} SET {assignments}, revision = revision + 1, updated_at = ? WHERE id = ? AND student_id = ? AND revision = ?",
            params,
        )
        if result.rowcount != 1:
            raise docs.ResumeConflict("素材在导入期间发生变化，请检查后重新确认。")
    return {"updated_fields": list(updates.keys()), "conflicts": conflicts}


def _find_similar_item(section: str, item: dict[str, Any], existing: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in existing:
        if section in {"skill", "certificate"} and _norm_key(row.get("name")) == _norm_key(item.get("name")):
            return row
        if section == "education":
            school_match = _norm_key(row.get("school")) and _norm_key(row.get("school")) == _norm_key(item.get("school"))
            major_match = _norm_key(row.get("major")) and _norm_key(row.get("major")) == _norm_key(item.get("major"))
            if school_match and (major_match or _date_near(row, item)):
                return row
        if section == "experience":
            title_a = _norm_key(row.get("title"))
            title_b = _norm_key(item.get("title"))
            if title_a and title_b and (title_a == title_b or title_a in title_b or title_b in title_a):
                if _date_near(row, item) or len(min(title_a, title_b, key=len)) >= 6:
                    return row
        if section == "self_intro":
            body_a = _norm_key(row.get("content_md"))
            body_b = _norm_key(item.get("content_md"))
            if body_a and body_b and (body_a[:80] == body_b[:80] or body_a in body_b or body_b in body_a):
                return row
    return None


def _identity_fields(section: str) -> set[str]:
    return {
        "skill": {"name"},
        "certificate": {"name"},
        "education": {"school", "major"},
        "experience": {"title"},
        "self_intro": {"content_md"},
    }.get(section, set())


def _replace_existing(items: list[dict[str, Any]], new_item: dict[str, Any]) -> None:
    for index, item in enumerate(items):
        if int(item.get("id") or 0) == int(new_item.get("id") or 0):
            items[index] = new_item
            return
    items.append(new_item)


def _norm_key(value: Any) -> str:
    return _PUNCT_RE.sub("", str(value or "").strip().lower())


def _date_near(a: dict[str, Any], b: dict[str, Any]) -> bool:
    a_start, a_end = str(a.get("start_date") or ""), str(a.get("end_date") or "")
    b_start, b_end = str(b.get("start_date") or ""), str(b.get("end_date") or "")
    if not (a_start or a_end or b_start or b_end):
        return False
    if a_start and b_start and a_start[:4] == b_start[:4]:
        return True
    if a_end and b_end and a_end[:4] == b_end[:4]:
        return True
    return bool(a_start and b_start and a_start == b_start) or bool(a_end and b_end and a_end == b_end)


def _target_position(payload: dict[str, Any], bundle: dict[str, Any]) -> str:
    personal = bundle.get("personal") or {}
    return _clean(
        payload.get("target_position")
        or (payload.get("personal") or {}).get("expected_position")
        or personal.get("expected_position")
        or "",
        120,
    )


def _build_resume_doc(
    conn,
    student_id: int,
    previous: dict[str, Any],
    payload: dict[str, Any],
    merge_result: dict[str, Any],
    target_position: str,
    tech_stack: list[Any],
) -> dict[str, Any]:
    selected = merge_result.get("selected") if isinstance(merge_result.get("selected"), dict) else {}
    title_name = (payload.get("personal") or {}).get("name") or (profile.get_personal_info(conn, student_id) or {}).get("name")
    title = f"{title_name}的简历" if title_name else Path(str(previous.get("source_filename") or "导入简历")).stem[:90]
    blocks: list[dict[str, Any]] = []
    if selected.get("self_intro"):
        blocks.append({"type": "self_intro", "ids": selected.get("self_intro")})
    if selected.get("education"):
        blocks.append({"type": "education", "ids": selected.get("education")})
    if selected.get("experience"):
        blocks.append({"type": "experience", "ids": selected.get("experience")})
    skill_ids = selected.get("skill") or []
    cert_ids = selected.get("certificate") or []
    if skill_ids or cert_ids:
        blocks.append({"type": "skill_cert", "skill_ids": skill_ids, "cert_ids": cert_ids})
    if tech_stack:
        blocks.append({"type": "tech_stack"})
    if not blocks:
        blocks.append({"type": "tech_stack"})

    summary = dict(merge_result)
    summary["message"] = _summary_message(summary)
    summary["parsed_at"] = _now()
    summary["counts"] = {
        section: len(payload.get(section) or [])
        for section in ("self_intro", "education", "experience", "skill", "certificate")
    }
    return {
        "id": previous.get("id"),
        "title": title or "导入简历",
        "target_position": target_position,
        "template_key": previous.get("template_key") or "classic",
        "layout": {
            "personal_fields": ["gender", "birthday", "phone", "email", "expected_position"],
            "blocks": blocks,
        },
        "tech_stack": tech_stack,
        "optimized_summary_md": "",
        "import_summary": summary,
    }


def _summary_message(summary: dict[str, Any]) -> str:
    added_count = sum(len(v) for v in (summary.get("added") or {}).values() if isinstance(v, list))
    updated_count = sum(len(v) for v in (summary.get("updated") or {}).values() if isinstance(v, list))
    conflict_count = sum(
        1 for item in (summary.get("conflicts") or [])
        if isinstance(item, dict) and not item.get("accepted")
    )
    parts = []
    if added_count:
        parts.append(f"新增 {added_count} 项资料")
    if updated_count:
        parts.append(f"补全 {updated_count} 项资料")
    if conflict_count:
        parts.append(f"{conflict_count} 处相似内容待确认")
    return "，".join(parts) or "解析完成，已生成可预览简历"
