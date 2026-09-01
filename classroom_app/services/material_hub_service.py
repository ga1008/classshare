"""材料中心（/manage/library）统一检索服务。

「材料」域没有传统菜单：左栏是分类多选按钮（见
``manage_nav_service.MATERIAL_HUB_CATEGORIES``），内容区是一张搜索/筛选卡。
本服务把全平台的材料来源统一成一个检索接口：

- 学习文档 / 课后材料  → ``course_materials``（按导入包 / 课堂生成任务推导分类）
- 教案 / 考核计划表 / 教师评学表 → 各自的结构化表
- 评分细则 / 平时成绩表 / 考核登分表 / 期末成绩单 / 成绩登记表 / 试卷分析表
  → ``material_ai_import_records``（含解析正文 ``content_markdown``）
- 试卷 → ``exam_papers``；教材 → ``textbooks``
- 公文 → ``gongwen_documents``（复用公文可见范围过滤）

每个分类一个检索器，单个检索器异常只影响该分类（绝不拖垮整次搜索）。
AI 搜索：快速 AI 把自然语言需求解析成 关键词/分类/范围，再走同一条检索链，
AI 不可用时自动降级为普通模糊搜索。
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .manage_nav_service import MATERIAL_HUB_CATEGORIES
from .organization_scope_service import load_teacher_org_scope
from .resource_access_service import is_super_admin_teacher

MAX_TERMS = 4
PER_CATEGORY_LIMIT = 30
SNIPPET_RADIUS = 60

CATEGORY_KEYS: tuple[str, ...] = tuple(category["key"] for category in MATERIAL_HUB_CATEGORIES)
CATEGORY_LABELS: dict[str, str] = {category["key"]: category["label"] for category in MATERIAL_HUB_CATEGORIES}

SCOPE_LABELS = {
    "private": "本人",
    "department": "本系部",
    "college": "本院级",
    "school": "本校",
    "public": "完全公开",
}

# 导入解析记录类分类 → material_ai_import_records.document_type + 落地页
_IMPORT_RECORD_CATEGORIES: dict[str, tuple[str, str]] = {
    "grading_rubrics": ("grading_rubric", "/manage/teaching/grading-rubrics"),
    "ordinary_grade_records": ("ordinary_grade_record", "/manage/teaching/ordinary-grade-records"),
    "exam_grade_records": ("exam_grade_record", "/manage/teaching/exam-grade-records"),
    "final_grade_transcripts": ("final_grade_transcript", "/manage/teaching/final-grade-transcripts"),
    "academic_grade_registers": ("academic_grade_register", "/manage/teaching/academic-grade-registers"),
    "academic_exam_analyses": ("academic_exam_analysis", "/manage/teaching/academic-exam-analyses"),
}


def normalize_hub_categories(raw: Any) -> list[str]:
    """归一化分类参数：空/非法 → 全部分类。"""
    if isinstance(raw, str):
        raw = [part for part in raw.split(",")]
    picked: list[str] = []
    for item in raw or []:
        key = str(item or "").strip()
        if key in CATEGORY_KEYS and key not in picked:
            picked.append(key)
    return picked or list(CATEGORY_KEYS)


def split_search_terms(query: str) -> list[str]:
    terms: list[str] = []
    for chunk in re.split(r"[\s,，;；/、]+", str(query or "").strip()):
        chunk = chunk.strip()
        if chunk and chunk not in terms:
            terms.append(chunk)
        if len(terms) >= MAX_TERMS:
            break
    return terms


def _like_condition(fields: list[str], terms: list[str]) -> tuple[str, list[str]]:
    """每个词都要命中（AND），任一字段命中即可（OR）。无词 → 恒真。"""
    if not terms:
        return "1 = 1", []
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        pattern = f"%{term}%"
        clauses.append("(" + " OR ".join(f"{field} LIKE ?" for field in fields) + ")")
        params.extend([pattern] * len(fields))
    return " AND ".join(clauses), params


def _scope_visibility_condition(alias: str, scope: dict[str, str], teacher_id: int, *, is_super_admin: bool) -> tuple[str, list[Any]]:
    """按 scope_level + 组织列的通用可见性（course_materials / 结构化表共用语义）。"""
    if is_super_admin:
        return "1 = 1", []
    prefix = f"{alias}." if alias else ""
    school = str(scope.get("school_code") or "").strip().lower()
    college = str(scope.get("college") or "").strip().lower()
    department = str(scope.get("department") or "").strip().lower()
    sql = (
        f"({prefix}teacher_id = ?"
        f" OR LOWER(COALESCE({prefix}scope_level, '')) = 'public'"
        f" OR (LOWER(COALESCE({prefix}scope_level, '')) = 'school' AND LOWER(TRIM(COALESCE({prefix}school_code, ''))) = ?)"
        f" OR (LOWER(COALESCE({prefix}scope_level, '')) = 'college' AND LOWER(TRIM(COALESCE({prefix}school_code, ''))) = ?"
        f"     AND LOWER(TRIM(COALESCE({prefix}college, ''))) = ?)"
        f" OR (LOWER(COALESCE({prefix}scope_level, '')) = 'department' AND LOWER(TRIM(COALESCE({prefix}school_code, ''))) = ?"
        f"     AND LOWER(TRIM(COALESCE({prefix}department, ''))) = ?))"
    )
    return sql, [int(teacher_id), school, school, college, school, department]


# 课后材料 = 导入解析包（含其 source/readme）或课堂生成任务落库的材料。
_POSTCLASS_MEMBERSHIP_SQL = """
    (EXISTS (
        SELECT 1 FROM material_ai_import_records r
        WHERE COALESCE(r.parse_status, '') = 'completed'
          AND (r.package_material_id = m.id OR r.source_material_id = m.id OR r.parsed_material_id = m.id)
    )
    OR EXISTS (
        SELECT 1 FROM session_material_generation_tasks t
        WHERE t.generated_material_id = m.id
    ))
"""


def _scope_key(row: Any) -> str:
    data = dict(row) if not isinstance(row, dict) else row
    level = str(data.get("scope_level") or "").strip().lower()
    if level in SCOPE_LABELS and level != "private":
        return level
    return "private"


def _snippet(text: str, terms: list[str]) -> str:
    body = re.sub(r"\s+", " ", str(text or "")).strip()
    if not body:
        return ""
    lowered = body.lower()
    for term in terms:
        index = lowered.find(term.lower())
        if index >= 0:
            start = max(0, index - SNIPPET_RADIUS)
            end = min(len(body), index + len(term) + SNIPPET_RADIUS)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(body) else ""
            return f"{prefix}{body[start:end]}{suffix}"
    return body[: SNIPPET_RADIUS * 2] + ("…" if len(body) > SNIPPET_RADIUS * 2 else "")


def _item(
    *,
    category: str,
    item_id: Any,
    title: str,
    owner: str,
    scope_key: str,
    updated_at: str,
    url: str,
    snippet: str = "",
    meta: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "category_label": CATEGORY_LABELS.get(category, category),
        "id": str(item_id),
        "title": title or "(未命名)",
        "owner": owner or "",
        "scope_key": scope_key,
        "scope_label": SCOPE_LABELS.get(scope_key, scope_key or ""),
        "updated_at": str(updated_at or "")[:16],
        "url": url,
        "snippet": snippet,
        "meta": [chip for chip in (meta or []) if chip],
    }


def _lessondoc_pack_root_ids(conn, material_ids: list[int]) -> set[int]:
    """这批材料里哪些是 LessonDoc 学习文档包的包根（用于结果卡徽标）。

    单条 SQL；任何异常降级为空集，检索结果绝不因徽标失败而缺失。
    """
    if not material_ids:
        return set()
    try:
        from ..db.schema_course_doc_packs import ensure_course_doc_pack_schema

        ensure_course_doc_pack_schema(conn)
        placeholders = ",".join("?" for _ in material_ids)
        rows = conn.execute(
            f"""
            SELECT root_material_id FROM course_doc_packs
            WHERE status = 'active' AND root_material_id IN ({placeholders})
            """,
            material_ids,
        ).fetchall()
        return {int(row["root_material_id"]) for row in rows}
    except Exception:
        return set()


def _search_course_materials(conn, ctx: dict[str, Any], terms: list[str], *, postclass: bool) -> list[dict[str, Any]]:
    visibility_sql, visibility_params = _scope_visibility_condition(
        "m", ctx["scope"], ctx["teacher_id"], is_super_admin=ctx["is_super_admin"]
    )
    like_sql, like_params = _like_condition(["m.name", "m.material_path", "tt.name"], terms)
    membership_sql = _POSTCLASS_MEMBERSHIP_SQL if postclass else f"NOT {_POSTCLASS_MEMBERSHIP_SQL}"
    # 无关键词时只看根节点，避免整棵树刷屏；有关键词时全层级检索。
    depth_sql = "1 = 1" if terms else "m.parent_id IS NULL"
    rows = conn.execute(
        f"""
        SELECT m.id, m.parent_id, m.name, m.material_path, m.node_type, m.preview_type,
               m.scope_level, m.teacher_id, m.updated_at, tt.name AS owner_name
        FROM course_materials m
        LEFT JOIN teachers tt ON tt.id = m.teacher_id
        WHERE m.name != '.git'
          AND m.material_path NOT LIKE '%/.git/%'
          AND {depth_sql}
          AND {membership_sql}
          AND {visibility_sql}
          AND {like_sql}
        ORDER BY m.updated_at DESC, m.id DESC
        LIMIT ?
        """,
        [*visibility_params, *like_params, PER_CATEGORY_LIMIT],
    ).fetchall()
    category = "postclass" if postclass else "learning_docs"
    base_page = "/manage/teaching/postclass-materials" if postclass else "/manage/teaching/materials"
    pack_root_ids = _lessondoc_pack_root_ids(conn, [int(row["id"]) for row in rows])
    items = []
    for row in rows:
        data = dict(row)
        is_folder = str(data.get("node_type") or "") == "folder"
        anchor_id = data["id"] if is_folder else (data.get("parent_id") or data["id"])
        items.append(
            _item(
                category=category,
                item_id=data["id"],
                title=data.get("name") or "",
                owner=data.get("owner_name") or "",
                scope_key=_scope_key(data),
                updated_at=data.get("updated_at") or "",
                url=f"{base_page}?parent_id={anchor_id}",
                snippet=_snippet(data.get("material_path") or "", terms),
                meta=[
                    "文件夹" if is_folder else str(data.get("preview_type") or "文件"),
                    "学习文档包" if int(data["id"]) in pack_root_ids else "",
                ],
            )
        )
    return items


_STRUCTURED_TABLES = {
    "lesson_plans": ("lesson_plans", "/manage/teaching/lesson-plans", ["title", "tags_json", "cover_json"]),
    "assessment_plans": ("assessment_plans", "/manage/teaching/assessment-plans", ["title", "tags_json", "fields_json"]),
    "teacher_evaluations": ("teacher_evaluations", "/manage/teaching/teacher-evaluations", ["title", "tags_json", "analysis"]),
}


def _search_structured_table(conn, ctx: dict[str, Any], terms: list[str], category: str) -> list[dict[str, Any]]:
    table, page_url, text_fields = _STRUCTURED_TABLES[category]
    visibility_sql, visibility_params = _scope_visibility_condition(
        "s", ctx["scope"], ctx["teacher_id"], is_super_admin=ctx["is_super_admin"]
    )
    like_sql, like_params = _like_condition([f"s.{field}" for field in text_fields] + ["tt.name"], terms)
    rows = conn.execute(
        f"""
        SELECT s.id, s.title, s.scope_level, s.teacher_id, s.updated_at, s.status,
               tt.name AS owner_name
        FROM {table} s
        LEFT JOIN teachers tt ON tt.id = s.teacher_id
        WHERE {visibility_sql}
          AND {like_sql}
        ORDER BY s.updated_at DESC
        LIMIT ?
        """,
        [*visibility_params, *like_params, PER_CATEGORY_LIMIT],
    ).fetchall()
    items = []
    for row in rows:
        data = dict(row)
        items.append(
            _item(
                category=category,
                item_id=data["id"],
                title=data.get("title") or "",
                owner=data.get("owner_name") or "",
                scope_key=_scope_key(data),
                updated_at=data.get("updated_at") or "",
                url=f"{page_url}?locate={data['id']}",
                meta=[str(data.get("status") or "")],
            )
        )
    return items


def _search_import_records(conn, ctx: dict[str, Any], terms: list[str], category: str) -> list[dict[str, Any]]:
    document_type, page_url = _IMPORT_RECORD_CATEGORIES[category]
    like_sql, like_params = _like_condition(
        ["r.source_file_name", "r.document_type_label", "r.content_markdown", "m.name", "tt.name"], terms
    )
    visibility_sql, visibility_params = _scope_visibility_condition(
        "m", ctx["scope"], ctx["teacher_id"], is_super_admin=ctx["is_super_admin"]
    )
    rows = conn.execute(
        f"""
        SELECT r.id AS record_id, r.source_file_name, r.document_type_label, r.content_markdown,
               r.updated_at, m.id AS package_id, m.name AS package_name, m.scope_level,
               m.teacher_id, tt.name AS owner_name
        FROM material_ai_import_records r
        JOIN course_materials m ON m.id = COALESCE(r.package_material_id, r.parsed_material_id, r.source_material_id)
        LEFT JOIN teachers tt ON tt.id = m.teacher_id
        WHERE r.document_type = ?
          AND COALESCE(r.parse_status, '') = 'completed'
          AND {visibility_sql}
          AND {like_sql}
        ORDER BY r.updated_at DESC
        LIMIT ?
        """,
        [document_type, *visibility_params, *like_params, PER_CATEGORY_LIMIT],
    ).fetchall()
    items = []
    for row in rows:
        data = dict(row)
        items.append(
            _item(
                category=category,
                item_id=data["record_id"],
                title=data.get("package_name") or data.get("source_file_name") or "",
                owner=data.get("owner_name") or "",
                scope_key=_scope_key(data),
                updated_at=data.get("updated_at") or "",
                url=f"{page_url}?parent_id={data.get('package_id')}",
                snippet=_snippet(data.get("content_markdown") or "", terms),
                meta=[str(data.get("document_type_label") or "")],
            )
        )
    return items


def _search_exam_papers(conn, ctx: dict[str, Any], terms: list[str]) -> list[dict[str, Any]]:
    like_sql, like_params = _like_condition(["title", "description", "tags_json"], terms)
    rows = conn.execute(
        f"""
        SELECT id, title, status, updated_at
        FROM exam_papers
        WHERE teacher_id = ?
          AND {like_sql}
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        [ctx["teacher_id"], *like_params, PER_CATEGORY_LIMIT],
    ).fetchall()
    return [
        _item(
            category="exam_papers",
            item_id=row["id"],
            title=row["title"],
            owner=ctx["teacher_name"],
            scope_key="private",
            updated_at=row["updated_at"] or "",
            url="/manage/teaching/exams",
            meta=[str(row["status"] or "")],
        )
        for row in rows
    ]


def _search_textbooks(conn, ctx: dict[str, Any], terms: list[str]) -> list[dict[str, Any]]:
    like_sql, like_params = _like_condition(["title", "publisher"], terms)
    rows = conn.execute(
        f"""
        SELECT id, title, publisher, updated_at
        FROM textbooks
        WHERE teacher_id = ?
          AND {like_sql}
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        [ctx["teacher_id"], *like_params, PER_CATEGORY_LIMIT],
    ).fetchall()
    return [
        _item(
            category="textbooks",
            item_id=row["id"],
            title=row["title"],
            owner=ctx["teacher_name"],
            scope_key="private",
            updated_at=row["updated_at"] or "",
            url="/manage/teaching/textbooks",
            meta=[str(row["publisher"] or "")],
        )
        for row in rows
    ]


_GONGWEN_OPENNESS_TO_SCOPE = {"public": "public", "school": "school", "college": "college", "department": "department"}


def _search_gongwen(conn, ctx: dict[str, Any], terms: list[str], raw_query: str) -> list[dict[str, Any]]:
    from .gongwen_document_sync_service import list_visible_gongwen_documents

    result = list_visible_gongwen_documents(
        conn,
        ctx["scope"],
        is_super_admin=ctx["is_super_admin"],
        keyword=str(raw_query or "").strip() or (terms[0] if terms else ""),
        limit=PER_CATEGORY_LIMIT,
    )
    items = []
    for doc in result.get("documents", []):
        openness = str(doc.get("openness") or "school").strip().lower()
        items.append(
            _item(
                category="gongwen",
                item_id=doc.get("id"),
                title=doc.get("title") or "",
                owner=doc.get("author") or doc.get("sender_name") or "",
                scope_key=_GONGWEN_OPENNESS_TO_SCOPE.get(openness, "school"),
                updated_at=str(doc.get("publish_time") or doc.get("created_at") or "")[:16],
                url=f"/manage/academic/gongwen?doc={doc.get('id')}",
                snippet=_snippet(doc.get("parsed_summary") or doc.get("summary") or "", terms),
                meta=[str(doc.get("sn") or ""), str(doc.get("category_name") or "")],
            )
        )
    return items


def build_hub_context(conn, user: dict[str, Any]) -> dict[str, Any]:
    teacher_id = int(user["id"])
    return {
        "teacher_id": teacher_id,
        "teacher_name": str(user.get("name") or ""),
        "scope": load_teacher_org_scope(conn, teacher_id),
        "is_super_admin": is_super_admin_teacher(conn, teacher_id),
    }


def search_material_hub(
    conn,
    user: dict[str, Any],
    *,
    query: str = "",
    categories: Any = None,
    scope_filter: str = "all",
) -> dict[str, Any]:
    """统一检索：返回 {groups, counts, total, terms}；单分类失败只记录不抛。"""
    ctx = build_hub_context(conn, user)
    picked = normalize_hub_categories(categories)
    terms = split_search_terms(query)
    normalized_scope = str(scope_filter or "all").strip().lower()

    searchers: dict[str, Callable[[], list[dict[str, Any]]]] = {
        "learning_docs": lambda: _search_course_materials(conn, ctx, terms, postclass=False),
        "postclass": lambda: _search_course_materials(conn, ctx, terms, postclass=True),
        "lesson_plans": lambda: _search_structured_table(conn, ctx, terms, "lesson_plans"),
        "assessment_plans": lambda: _search_structured_table(conn, ctx, terms, "assessment_plans"),
        "teacher_evaluations": lambda: _search_structured_table(conn, ctx, terms, "teacher_evaluations"),
        "exam_papers": lambda: _search_exam_papers(conn, ctx, terms),
        "textbooks": lambda: _search_textbooks(conn, ctx, terms),
        "gongwen": lambda: _search_gongwen(conn, ctx, terms, query),
    }
    for category_key in _IMPORT_RECORD_CATEGORIES:
        searchers[category_key] = lambda key=category_key: _search_import_records(conn, ctx, terms, key)

    groups: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    failed: list[str] = []
    for category_key in picked:
        searcher = searchers.get(category_key)
        if not searcher:
            continue
        try:
            items = searcher()
        except Exception as exc:  # noqa: BLE001 — 单分类故障不拖垮整次搜索
            print(f"[MATERIAL_HUB] category {category_key} search failed: {exc}")
            failed.append(category_key)
            items = []
        if normalized_scope != "all":
            items = [item for item in items if item["scope_key"] == normalized_scope]
        counts[category_key] = len(items)
        if items:
            groups.append(
                {
                    "key": category_key,
                    "label": CATEGORY_LABELS.get(category_key, category_key),
                    "items": items,
                }
            )

    return {
        "groups": groups,
        "counts": counts,
        "total": sum(counts.values()),
        "terms": terms,
        "failed_categories": failed,
    }


async def ai_understand_hub_query(query: str) -> dict[str, Any] | None:
    """快速 AI 把自然语言检索需求解析为 关键词/分类/范围；失败返回 None（降级普通搜索）。"""
    cleaned = str(query or "").strip()
    if not cleaned:
        return None
    category_menu = "、".join(f"{item['key']}({item['label']})" for item in MATERIAL_HUB_CATEGORIES)
    payload = {
        "system_prompt": (
            "你是教学材料检索意图解析器。教师在材料中心输入了一句自然语言需求，"
            "请解析出检索关键词与目标分类。可用分类 key："
            f"{category_menu}。只输出 JSON："
            '{"keywords": ["1-4个检索关键词"], "categories": ["相关分类key，不确定就给[]"], '
            '"scope": "all|private|department|college|school|public", "explanation": "20字内的理解说明"}。'
            "keywords 必须是适合 LIKE 模糊匹配的名词短语（课程名/材料名/人名/学期等），"
            "不要包含「帮我找」「材料」这类虚词。"
        ),
        "messages": [],
        "new_message": cleaned[:400],
        "base64_urls": [],
        "file_texts": [],
        "model_capability": "standard",
        "task_type": "fast_text_response",
        "response_format": "json",
        "task_priority": "interactive",
        "task_label": "material_hub_ai_search",
    }
    try:
        from ..core import ai_client

        resp = await ai_client.post("/api/ai/chat", json=payload, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — AI 故障 → 普通搜索兜底
        print(f"[MATERIAL_HUB] ai search intent failed: {exc}")
        return None

    parsed = data.get("response_json") if isinstance(data, dict) else None
    if not isinstance(parsed, dict):
        return None
    keywords = [str(item).strip() for item in (parsed.get("keywords") or []) if str(item or "").strip()][:MAX_TERMS]
    categories = [str(item).strip() for item in (parsed.get("categories") or []) if str(item or "").strip() in CATEGORY_KEYS]
    scope = str(parsed.get("scope") or "all").strip().lower()
    if scope not in {"all", *SCOPE_LABELS}:
        scope = "all"
    return {
        "keywords": keywords,
        "categories": categories,
        "scope": scope,
        "explanation": str(parsed.get("explanation") or "").strip()[:60],
    }


__all__ = [
    "CATEGORY_KEYS",
    "CATEGORY_LABELS",
    "ai_understand_hub_query",
    "build_hub_context",
    "normalize_hub_categories",
    "search_material_hub",
    "split_search_terms",
]
