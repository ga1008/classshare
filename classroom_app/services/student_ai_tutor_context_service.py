"""AI 学伴课程上下文：材料片段检索（带出处）+ 进行中任务的苏格拉底守卫。

给研讨室 AI"助教"补两块按提问动态生成的系统提示：

1. **材料参考**：按提问关键词检索当前课堂绑定的文本类材料，取最相关片段，
   并要求回答时标注出处（见《材料名》），让 AI 的答案可溯源到本课材料。
2. **苏格拉底守卫**：提问命中进行中的作业/考试（标题或试卷题干）时，
   注入"只给提示链、不给最终答案"的守卫指令——教师可放心开放 AI 的前提。

设计约束：
- 单一入口 ``build_tutor_context_block``，流式/非流式两条回复路径共用，防止漂移；
- 纯只读、best-effort：任何异常返回空串，绝不拖垮 AI 回复主链路；
- 检索是可替换的策略层（当前为关键词计分，后续可换 tsvector/向量检索而不动调用方）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .file_service import resolve_global_file_path

# 检索与拼装的规模上限：控制 token 成本与请求路径上的文件 IO。
MAX_CANDIDATE_MATERIALS = 40
MAX_FILE_READS = 8
MAX_FILE_BYTES = 300 * 1024
MAX_SNIPPETS = 3
SNIPPET_WINDOW = 260
MAX_QUERY_TOKENS = 24

_TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030")
_TEXT_FILE_EXTS = {"md", "markdown", "txt"}
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")
_STOP_TOKENS = {
    "什么", "怎么", "如何", "为何", "为什么", "请问", "老师", "助教", "一下",
    "这个", "那个", "可以", "是否", "问题", "帮我", "谢谢", "你好",
}


def _normalize_query(raw: Any) -> str:
    text = str(raw or "")
    # 剥掉 @助教 召唤词与多余空白，只留真正的问题。
    text = re.sub(r"@\S+", " ", text)
    return " ".join(text.split()).strip()


def _tokenize(text: str) -> list[str]:
    """CJK 连续二字词 + 拉丁/数字词，去停用词，保序去重。"""
    tokens: list[str] = []
    for match in _LATIN_TOKEN_RE.finditer(text):
        tokens.append(match.group(0).lower())
    cjk_runs = re.findall(r"[一-鿿]{2,}", text)
    for run in cjk_runs:
        for idx in range(len(run) - 1):
            tokens.append(run[idx : idx + 2])
    seen: set[str] = set()
    result = []
    for token in tokens:
        if token in _STOP_TOKENS or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result[:MAX_QUERY_TOKENS]


def _decode_text_bytes(raw: bytes) -> str:
    if b"\x00" in raw:
        return ""
    for encoding in _TEXT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _load_bound_text_materials(conn: Any, class_offering_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT m.id, m.name, m.file_ext, m.file_hash, m.file_size,
               m.preview_type, m.ai_optimized_markdown
        FROM course_material_assignments a
        JOIN course_materials m ON m.id = a.material_id
        WHERE a.class_offering_id = ?
          AND m.node_type = 'file'
        ORDER BY a.created_at DESC, m.updated_at DESC, m.id DESC
        LIMIT ?
        """,
        (int(class_offering_id), MAX_CANDIDATE_MATERIALS),
    ).fetchall()
    materials = []
    for row in rows:
        item = dict(row)
        ext = str(item.get("file_ext") or "").lower().lstrip(".")
        is_text = (
            bool(str(item.get("ai_optimized_markdown") or "").strip())
            or ext in _TEXT_FILE_EXTS
            or str(item.get("preview_type") or "") in {"markdown", "text"}
        )
        if is_text:
            materials.append(item)
    return materials


def _material_text(material: dict[str, Any], *, file_read_budget: list[int]) -> str:
    optimized = str(material.get("ai_optimized_markdown") or "").strip()
    if optimized:
        return optimized
    if file_read_budget[0] <= 0:
        return ""
    file_hash = str(material.get("file_hash") or "").strip()
    if not file_hash:
        return ""
    try:
        size = int(material.get("file_size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size > MAX_FILE_BYTES:
        return ""
    path = resolve_global_file_path(file_hash)
    if not path:
        return ""
    try:
        file_read_budget[0] -= 1
        return _decode_text_bytes(path.read_bytes())
    except OSError:
        return ""


def _score_and_snippet(text: str, title: str, tokens: list[str]) -> tuple[int, str]:
    if not tokens:
        return 0, ""
    lowered = text.lower()
    lowered_title = str(title or "").lower()
    score = 0
    first_hit = -1
    for token in tokens:
        if token in lowered_title:
            score += 4
        count = lowered.count(token)
        if count:
            score += min(count, 3)
            pos = lowered.find(token)
            if first_hit < 0 or pos < first_hit:
                first_hit = pos
    if score <= 0:
        return 0, ""
    if first_hit < 0:
        first_hit = 0
    start = max(0, first_hit - SNIPPET_WINDOW // 2)
    snippet = text[start : start + SNIPPET_WINDOW]
    snippet = " ".join(snippet.split())
    return score, snippet


def retrieve_material_snippets(
    conn: Any,
    *,
    class_offering_id: int,
    query: str,
    limit: int = MAX_SNIPPETS,
) -> list[dict[str, Any]]:
    """按提问检索本课堂绑定材料，返回 [{material_id, title, snippet, score}]。"""
    tokens = _tokenize(_normalize_query(query))
    if not tokens:
        return []
    file_read_budget = [MAX_FILE_READS]
    scored: list[dict[str, Any]] = []
    for material in _load_bound_text_materials(conn, class_offering_id):
        text = _material_text(material, file_read_budget=file_read_budget)
        if not text:
            continue
        score, snippet = _score_and_snippet(text, str(material.get("name") or ""), tokens)
        if score <= 0 or not snippet:
            continue
        scored.append(
            {
                "material_id": material.get("id"),
                "title": str(material.get("name") or "课程材料"),
                "snippet": snippet,
                "score": score,
            }
        )
    scored.sort(key=lambda item: -item["score"])
    return scored[: max(1, limit)]


def _load_open_tasks(conn: Any, class_offering_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.id, a.title, a.exam_paper_id, a.due_at,
               ep.questions_json
        FROM assignments a
        LEFT JOIN exam_papers ep ON ep.id = a.exam_paper_id
        WHERE a.class_offering_id = ?
          AND a.status = 'published'
          AND a.closed_at IS NULL
        ORDER BY a.created_at DESC
        LIMIT 20
        """,
        (int(class_offering_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _question_texts(questions_json: Any, *, limit: int = 60) -> list[str]:
    try:
        payload = json.loads(str(questions_json or "{}"))
    except (TypeError, ValueError):
        return []
    texts: list[str] = []
    pages = payload.get("pages") if isinstance(payload, dict) else []
    for page in pages if isinstance(pages, list) else []:
        if not isinstance(page, dict):
            continue
        for question in page.get("questions") or []:
            if isinstance(question, dict):
                text = str(question.get("text") or question.get("question") or "").strip()
                if len(text) >= 8:
                    texts.append(text)
            if len(texts) >= limit:
                return texts
    return texts


def _bigrams(text: str) -> set[str]:
    condensed = re.sub(r"\s+", "", str(text or "").lower())
    return {condensed[i : i + 2] for i in range(len(condensed) - 1)} if len(condensed) > 1 else set()


def detect_open_task_hit(conn: Any, *, class_offering_id: int, query: str) -> dict[str, Any] | None:
    """提问是否明显指向某个进行中的作业/考试题目。"""
    normalized = _normalize_query(query)
    if len(normalized) < 4:
        return None
    query_bigrams = _bigrams(normalized)
    if not query_bigrams:
        return None
    for task in _load_open_tasks(conn, class_offering_id):
        title = str(task.get("title") or "").strip()
        # 标题重叠：标题的有效二字组一半以上出现在提问里。
        title_bigrams = _bigrams(title)
        if title_bigrams and len(title_bigrams & query_bigrams) >= max(2, len(title_bigrams) // 2):
            return {"title": title, "kind": "考试" if task.get("exam_paper_id") else "作业"}
        # 题干重叠：提问与某道题的二字组交集覆盖提问的 60% 以上（学生把题目粘贴进来问答案）。
        for question_text in _question_texts(task.get("questions_json")):
            question_bigrams = _bigrams(question_text)
            if not question_bigrams:
                continue
            overlap = len(query_bigrams & question_bigrams)
            if overlap >= max(6, int(len(query_bigrams) * 0.6)):
                return {"title": title, "kind": "考试" if task.get("exam_paper_id") else "作业"}
    return None


def build_tutor_context_block(
    conn: Any,
    *,
    class_offering_id: int,
    query: str,
    user_role: str,
) -> str:
    """组装注入系统提示的"学伴上下文"文本块；无内容/出错时返回空串。"""
    try:
        sections: list[str] = []

        if str(user_role or "").strip().lower() == "student":
            hit = detect_open_task_hit(conn, class_offering_id=class_offering_id, query=query)
            if hit:
                sections.append(
                    "--- 学业诚信守卫（必须遵守，优先级最高） ---\n"
                    f"当前提问疑似指向进行中的{hit['kind']}《{hit['title']}》。\n"
                    "对这类问题绝不直接给出最终答案、完整解题过程或可直接提交的代码/文段。\n"
                    "改用苏格拉底式引导，按顺序：1) 用一句话帮学生复述题目在考察什么；"
                    "2) 点出涉及的关键概念并提示去哪份课程材料复习；"
                    "3) 给出第一步的思考方向，然后邀请学生先自己尝试再来讨论。\n"
                    "如果学生坚持索要答案，温和说明截止后可以一起完整复盘。"
                )

        snippets = retrieve_material_snippets(
            conn, class_offering_id=class_offering_id, query=query
        )
        if snippets:
            lines = ["--- 课程材料参考（回答时引用出处） ---"]
            for index, item in enumerate(snippets, start=1):
                lines.append(f"[材料{index}]《{item['title']}》：{item['snippet']}")
            lines.append(
                "使用规则：优先依据以上材料回答，并在对应结论后标注出处，格式如（见《材料名》）；"
                "材料未覆盖的部分可以用通识知识补充，但要说明\"以下内容超出本课材料\"。"
            )
            sections.append("\n".join(lines))

        return "\n\n".join(sections)
    except Exception:
        # 学伴上下文是增强项：任何失败都不允许影响 AI 回复本身。
        return ""
