"""全局搜索：一个搜索框覆盖 课堂 / 材料 / 作业考试 / 博客。

按角色严格圈定可见范围（学生=本班课堂及其绑定资源；教师=自己执教的课堂），
博客只搜已发布的公开文章。LIKE 参数化并转义通配符，天然防注入。

检索是策略层：当前为 LOWER LIKE，后续可平滑替换为 PostgreSQL tsvector/pg_trgm
物化索引而不改路由与前端。
（区别于 gongwen_ai_search_service：那是公文库的 AI 语义检索。）
"""

from __future__ import annotations

from typing import Any

MAX_PER_KIND = 6
MIN_QUERY_LENGTH = 2

KIND_LABELS = {
    "classroom": "课堂",
    "material": "材料",
    "assignment": "作业/考试",
    "blog": "博客",
}


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped.lower()}%"


def _normalize_query(raw: Any) -> str:
    return " ".join(str(raw or "").split()).strip()


def _load_my_offering_ids(conn: Any, *, role: str, user_pk: int) -> list[int]:
    if role == "student":
        rows = conn.execute(
            """
            SELECT o.id FROM class_offerings o
            JOIN students s ON s.class_id = o.class_id
            WHERE s.id = ? AND COALESCE(s.enrollment_status, 'active') = 'active'
            """,
            (int(user_pk),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM class_offerings WHERE teacher_id = ?",
            (int(user_pk),),
        ).fetchall()
    return [int(row["id"]) for row in rows]


def _search_classrooms(conn: Any, *, role: str, user_pk: int, pattern: str) -> list[dict[str, Any]]:
    if role == "student":
        rows = conn.execute(
            """
            SELECT o.id, c.name AS course_name, cl.name AS class_name
            FROM class_offerings o
            JOIN students s ON s.class_id = o.class_id
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            WHERE s.id = ?
              AND (LOWER(c.name) LIKE ? ESCAPE '\\' OR LOWER(cl.name) LIKE ? ESCAPE '\\')
            LIMIT ?
            """,
            (int(user_pk), pattern, pattern, MAX_PER_KIND),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT o.id, c.name AS course_name, cl.name AS class_name
            FROM class_offerings o
            JOIN courses c ON c.id = o.course_id
            JOIN classes cl ON cl.id = o.class_id
            WHERE o.teacher_id = ?
              AND (LOWER(c.name) LIKE ? ESCAPE '\\' OR LOWER(cl.name) LIKE ? ESCAPE '\\')
            LIMIT ?
            """,
            (int(user_pk), pattern, pattern, MAX_PER_KIND),
        ).fetchall()
    return [
        {
            "kind": "classroom",
            "title": str(row["course_name"] or "课堂"),
            "subtitle": str(row["class_name"] or ""),
            "link_url": f"/classroom/{int(row['id'])}",
        }
        for row in rows
    ]


def _search_materials(conn: Any, offering_ids: list[int], pattern: str) -> list[dict[str, Any]]:
    if not offering_ids:
        return []
    placeholders = ",".join("?" for _ in offering_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT m.id, m.name, a.class_offering_id, c.name AS course_name
        FROM course_material_assignments a
        JOIN course_materials m ON m.id = a.material_id
        JOIN class_offerings o ON o.id = a.class_offering_id
        JOIN courses c ON c.id = o.course_id
        WHERE a.class_offering_id IN ({placeholders})
          AND m.node_type = 'file'
          AND LOWER(m.name) LIKE ? ESCAPE '\\'
        LIMIT ?
        """,
        (*offering_ids, pattern, MAX_PER_KIND),
    ).fetchall()
    return [
        {
            "kind": "material",
            "title": str(row["name"] or "课程材料"),
            "subtitle": f"{row['course_name'] or '课程'} · 课堂材料区",
            "link_url": f"/classroom/{int(row['class_offering_id'])}",
        }
        for row in rows
    ]


def _search_assignments(conn: Any, offering_ids: list[int], pattern: str, *, role: str) -> list[dict[str, Any]]:
    if not offering_ids:
        return []
    placeholders = ",".join("?" for _ in offering_ids)
    status_clause = "" if role == "teacher" else "AND a.status != 'new'"
    rows = conn.execute(
        f"""
        SELECT a.id, a.title, a.exam_paper_id, a.status, c.name AS course_name
        FROM assignments a
        JOIN courses c ON c.id = a.course_id
        WHERE a.class_offering_id IN ({placeholders})
          {status_clause}
          AND LOWER(a.title) LIKE ? ESCAPE '\\'
        ORDER BY a.created_at DESC
        LIMIT ?
        """,
        (*offering_ids, pattern, MAX_PER_KIND),
    ).fetchall()
    return [
        {
            "kind": "assignment",
            "title": str(row["title"] or "任务"),
            "subtitle": f"{row['course_name'] or '课程'} · {'考试' if row['exam_paper_id'] else '作业'}",
            "link_url": f"/assignment/{row['id']}",
        }
        for row in rows
    ]


def _search_blog(conn: Any, pattern: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, title, author_display_name
        FROM blog_posts
        WHERE status = 'published'
          AND visibility = 'public'
          AND (LOWER(title) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(summary, '')) LIKE ? ESCAPE '\\')
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (pattern, pattern, MAX_PER_KIND),
    ).fetchall()
    return [
        {
            "kind": "blog",
            "title": str(row["title"] or "文章"),
            "subtitle": f"博客 · {row['author_display_name'] or '作者'}",
            "link_url": f"/blog?post={int(row['id'])}",
        }
        for row in rows
    ]


def search_everything(conn: Any, user: dict[str, Any], raw_query: str) -> dict[str, Any]:
    """跨域搜索入口；查询过短时返回空组，前端据此提示继续输入。"""
    query = _normalize_query(raw_query)
    role = str(user.get("role") or "").strip().lower()
    user_pk = int(user.get("id") or 0)
    if len(query) < MIN_QUERY_LENGTH or role not in {"student", "teacher"} or not user_pk:
        return {"query": query, "groups": [], "total": 0}

    pattern = _like_pattern(query)
    offering_ids = _load_my_offering_ids(conn, role=role, user_pk=user_pk)

    groups = []
    for kind, results in (
        ("classroom", _search_classrooms(conn, role=role, user_pk=user_pk, pattern=pattern)),
        ("material", _search_materials(conn, offering_ids, pattern)),
        ("assignment", _search_assignments(conn, offering_ids, pattern, role=role)),
        ("blog", _search_blog(conn, pattern)),
    ):
        if results:
            groups.append({"kind": kind, "kind_label": KIND_LABELS[kind], "results": results})

    return {
        "query": query,
        "groups": groups,
        "total": sum(len(group["results"]) for group in groups),
    }
