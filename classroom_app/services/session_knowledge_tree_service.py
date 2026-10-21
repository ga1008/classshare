"""课次知识树：课次独立生命周期的数据与规则真源。

一个课次（``class_offering_sessions`` 行）至多一棵知识树。树由 AI 依据课程简介、
教材目录与本课次绑定的学习文档生成，服务端在这里统一校验和归一化：

* 1–4 个大知识点（顶层），整棵树 2–3 层；超过三层的部分并入第三层，顶层必须有下级；
* 末梢（无下级的节点，层级 ≥2）携带比重，归一化为整数且总和恰为 100；
* 课次进度 = 已完成末梢的比重之和；生命周期由进度派生：
  ``none``（无树）→ ``not_started``（0）→ ``in_progress`` → ``completed``（100）。

节点 id 在生成时分配并保持稳定，拖动排序只改变同级顺序，不影响完成记录。重新生成会
替换整棵树并清空完成记录（文档变化后旧知识点不再可信）。完成记录的修改用 version
做乐观并发，适配多标签/多设备同时操作。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Iterable

from fastapi import HTTPException

MAX_TOP_NODES = 4
MAX_DEPTH = 3
MAX_CHILDREN = 8
MAX_LEAVES = 40
LABEL_LIMIT = 40
DETAIL_FIELDS = {
    "detail": 400,
    "example": 300,
    "question": 200,
    "prerequisite": 160,
    "extension": 160,
}
GENERATION_STALE_AFTER = timedelta(minutes=20)

STATUS_EMPTY = "empty"
STATUS_GENERATING = "generating"
STATUS_READY = "ready"
STATUS_FAILED = "failed"


class KnowledgeTreeError(ValueError):
    """AI output cannot be shaped into a valid knowledge tree."""


def now_text() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip()


def _new_node_id(used: set[str]) -> str:
    while True:
        node_id = uuid.uuid4().hex[:8]
        if node_id not in used:
            used.add(node_id)
            return node_id


# ---------------------------------------------------------------------------
# Normalization (pure)
# ---------------------------------------------------------------------------
def _raw_children(raw: dict[str, Any]) -> list[dict[str, Any]]:
    children = raw.get("children")
    if not isinstance(children, list):
        children = raw.get("nodes") if isinstance(raw.get("nodes"), list) else []
    return [item for item in children if isinstance(item, dict) and _clean_text(item.get("label") or item.get("title"), LABEL_LIMIT)]


def _flatten_text(raw: dict[str, Any], depth: int = 0) -> list[str]:
    """Labels of nodes nested deeper than the tree allows, kept as leaf detail."""
    parts: list[str] = []
    for child in _raw_children(raw):
        parts.append(_clean_text(child.get("label") or child.get("title"), LABEL_LIMIT))
        if depth < 2:
            parts.extend(_flatten_text(child, depth + 1))
    return parts


def _normalize_node(raw: dict[str, Any], level: int, used: set[str], leaf_budget: list[int]) -> dict[str, Any] | None:
    label = _clean_text(raw.get("label") or raw.get("title"), LABEL_LIMIT)
    if not label:
        return None
    node: dict[str, Any] = {"id": _new_node_id(used), "label": label}
    for field, limit in DETAIL_FIELDS.items():
        value = _clean_text(raw.get(field), limit)
        if value:
            node[field] = value
    children_raw = _raw_children(raw)[:MAX_CHILDREN]
    if level >= MAX_DEPTH and children_raw:
        extra = "、".join(_flatten_text(raw))[:200]
        if extra:
            node["detail"] = _clean_text(f"{node.get('detail', '')} 包含：{extra}", DETAIL_FIELDS["detail"] + 200)
        children_raw = []
    children: list[dict[str, Any]] = []
    for child_raw in children_raw:
        if leaf_budget[0] <= 0:
            break
        child = _normalize_node(child_raw, level + 1, used, leaf_budget)
        if child:
            children.append(child)
    if children:
        node["children"] = children
    else:
        leaf_budget[0] -= 1
        try:
            node["weight"] = max(0.0, float(raw.get("weight") or 0))
        except (TypeError, ValueError):
            node["weight"] = 0.0
    return node


def iter_leaves(nodes: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for node in nodes:
        children = node.get("children") or []
        if children:
            yield from iter_leaves(children)
        else:
            yield node


def iter_nodes(nodes: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for node in nodes:
        yield node
        yield from iter_nodes(node.get("children") or [])


def tree_depth(nodes: list[dict[str, Any]]) -> int:
    if not nodes:
        return 0
    return 1 + max(tree_depth(node.get("children") or []) for node in nodes)


def distribute_weights(raw_weights: list[float]) -> list[int]:
    """Integer weights proportional to ``raw_weights`` summing to exactly 100.

    Missing/zero weights take the smallest given weight (or an even split when
    none is given); every leaf keeps at least 1.
    """
    count = len(raw_weights)
    if count == 0:
        return []
    if count > 100:
        raise KnowledgeTreeError("知识点过多，无法分配比重")
    weights = [w if w > 0 else 0.0 for w in raw_weights]
    if sum(weights) <= 0:
        weights = [1.0] * count
    else:
        floor = min(w for w in weights if w > 0)
        weights = [w if w > 0 else floor for w in weights]
    # Proportional largest-remainder rounding keeps weights that already sum
    # to 100 unchanged; any leaf rounded to 0 then borrows from the largest.
    total = sum(weights)
    exact = [w / total * 100 for w in weights]
    result = [int(value) for value in exact]
    order = sorted(range(count), key=lambda i: (exact[i] - int(exact[i]), weights[i]), reverse=True)
    for index in order[:100 - sum(result)]:
        result[index] += 1
    for index in range(count):
        if result[index] < 1:
            donor = max(range(count), key=lambda i: result[i])
            result[donor] -= 1
            result[index] = 1
    return result


def normalize_tree(raw: Any) -> dict[str, Any]:
    """Validate and normalize an AI knowledge tree payload."""
    if not isinstance(raw, dict):
        raise KnowledgeTreeError("AI 未返回知识树结构")
    top_raw = _raw_children(raw)
    if not top_raw:
        raise KnowledgeTreeError("AI 返回的知识树没有知识点")
    used: set[str] = set()
    leaf_budget = [MAX_LEAVES]
    nodes = [node for node in (_normalize_node(item, 1, used, leaf_budget) for item in top_raw[:MAX_TOP_NODES]) if node]
    if not any(node.get("children") for node in nodes):
        raise KnowledgeTreeError("知识树至少需要两层（大知识点下需有细分知识）")
    for node in nodes:
        if not node.get("children"):
            # A childless major point would be a level-1 leaf; keep the 2-3 layer
            # contract by giving it a single basic unit carrying its content.
            leaf = {"id": _new_node_id(used), "label": node["label"], "weight": node.pop("weight", 0.0)}
            for field in DETAIL_FIELDS:
                if node.get(field):
                    leaf[field] = node[field]
            node["children"] = [leaf]
    leaves = list(iter_leaves(nodes))
    for leaf, weight in zip(leaves, distribute_weights([float(leaf.get("weight") or 0) for leaf in leaves])):
        leaf["weight"] = weight
    return {
        "title": _clean_text(raw.get("title"), 60),
        "summary": _clean_text(raw.get("summary"), 200),
        "depth": tree_depth(nodes),
        "nodes": nodes,
    }


# ---------------------------------------------------------------------------
# Progress (pure)
# ---------------------------------------------------------------------------
def lifecycle_state(has_tree: bool, percent: int, completed_count: int = 0) -> str:
    if not has_tree:
        return "none"
    if percent >= 100:
        return "completed"
    if percent > 0 or completed_count > 0:
        return "in_progress"
    return "not_started"


def leaf_weight(leaf: dict[str, Any]) -> int:
    try:
        return max(0, int(leaf.get("weight") or 0))
    except (TypeError, ValueError):
        return 0


def compute_progress(tree: dict[str, Any], completed_ids: Iterable[str]) -> dict[str, Any]:
    """Progress = completed weight share of the total weight.

    Generated trees always total 100; after manual edits the total may differ
    (the editor warns until it is 100 again), so the share is taken against the
    actual total. An all-zero tree falls back to counting units.
    """
    leaves = list(iter_leaves(tree.get("nodes") or []))
    leaf_ids = {leaf["id"] for leaf in leaves}
    done = [node_id for node_id in dict.fromkeys(str(i) for i in completed_ids) if node_id in leaf_ids]
    done_set = set(done)
    total = sum(leaf_weight(leaf) for leaf in leaves)
    done_weight = sum(leaf_weight(leaf) for leaf in leaves if leaf["id"] in done_set)
    if total > 0:
        percent = done_weight if total == 100 else int(done_weight * 100 / total + 0.5)
    else:
        percent = int(len(done_set) * 100 / len(leaves) + 0.5) if leaves else 0
    percent = max(0, min(100, percent))
    if leaves and len(done_set) == len(leaves):
        percent = 100
    elif percent >= 100:
        percent = 99  # never report completion while a unit is still open
    return {
        "completed_ids": done,
        "progress": percent,
        "leaf_count": len(leaves),
        "completed_count": len(done),
        "weight_total": total,
        "lifecycle": lifecycle_state(bool(leaves), percent, len(done)),
    }


def reorder_children(tree: dict[str, Any], parent_id: str, order: list[str]) -> dict[str, Any]:
    """Return a copy of ``tree`` with ``parent_id``'s children in ``order``."""
    tree = json.loads(json.dumps(tree))
    for index, node in enumerate(tree.get("nodes") or []):
        if not isinstance(node.get("tone"), int):
            node["tone"] = index % 8  # keep each major point's colour across reordering
    if parent_id:
        parent = next((node for node in iter_nodes(tree.get("nodes") or []) if node["id"] == parent_id), None)
        if not parent or not parent.get("children"):
            raise HTTPException(404, "要排序的知识点不存在")
        siblings = parent["children"]
    else:
        siblings = tree.get("nodes") or []
    current = [node["id"] for node in siblings]
    order = [str(item) for item in order]
    if sorted(current) != sorted(order) or len(set(order)) != len(order):
        raise HTTPException(409, "知识树已更新，请刷新后再调整顺序")
    by_id = {node["id"]: node for node in siblings}
    reordered = [by_id[node_id] for node_id in order]
    if parent_id:
        parent["children"] = reordered
    else:
        tree["nodes"] = reordered
    return tree


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def load_row(conn, session_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM session_knowledge_trees WHERE session_id = ? LIMIT 1",
        (int(session_id),),
    ).fetchone()
    return dict(row) if row else None


def ensure_row(conn, *, class_offering_id: int, session_id: int, teacher_id: int) -> dict[str, Any]:
    stamp = now_text()
    conn.execute(
        """
        INSERT INTO session_knowledge_trees (id, class_offering_id, session_id, teacher_id, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (session_id) DO NOTHING
        """,
        (uuid.uuid4().hex, int(class_offering_id), int(session_id), int(teacher_id), STATUS_EMPTY, stamp, stamp),
    )
    row = load_row(conn, session_id)
    if not row:
        raise HTTPException(500, "知识树记录创建失败")
    return row


def is_generation_stale(row: dict[str, Any], *, now: datetime | None = None) -> bool:
    if str(row.get("status") or "") != STATUS_GENERATING:
        return False
    started = str(row.get("ai_gen_started_at") or row.get("updated_at") or "")
    try:
        started_at = datetime.fromisoformat(started.replace("Z", "")[:19])
    except ValueError:
        return True
    return (now or datetime.now()) - started_at > GENERATION_STALE_AFTER


def expire_stale_generation(conn, row: dict[str, Any]) -> dict[str, Any]:
    if not is_generation_stale(row):
        return row
    has_tree = bool(_loads(row.get("tree_json"), {}).get("nodes"))
    status = STATUS_READY if has_tree else STATUS_FAILED
    error = "生成超时，请重新生成。"
    conn.execute(
        # Clearing the token drops a result that arrives after the expiry, so a
        # late AI reply can no longer replace a tree the teacher kept using.
        "UPDATE session_knowledge_trees SET status = ?, ai_gen_status = 'failed', ai_gen_error = ?, "
        "ai_gen_task_id = NULL, updated_at = ? WHERE id = ? AND status = ?",
        (status, error, now_text(), row["id"], STATUS_GENERATING),
    )
    return {**row, "status": status, "ai_gen_status": "failed", "ai_gen_error": error, "ai_gen_task_id": None}


def start_generation(conn, row: dict[str, Any]) -> str:
    """Mark the row as generating; returns the task token guarding the result."""
    row = expire_stale_generation(conn, row)
    if str(row.get("status") or "") == STATUS_GENERATING:
        raise HTTPException(409, "知识树正在生成，请稍候。")
    token = uuid.uuid4().hex
    stamp = now_text()
    cursor = conn.execute(
        """
        UPDATE session_knowledge_trees
        SET status = ?, ai_gen_status = 'pending', ai_gen_error = NULL, ai_gen_task_id = ?,
            ai_gen_started_at = ?, updated_at = ?
        WHERE id = ? AND status <> ?
        """,
        (STATUS_GENERATING, token, stamp, stamp, row["id"], STATUS_GENERATING),
    )
    if getattr(cursor, "rowcount", 1) == 0:
        raise HTTPException(409, "知识树正在生成，请稍候。")
    return token


def store_generated_tree(conn, row_id: str, token: str, tree: dict[str, Any], *, fingerprint: str, source_label: str) -> bool:
    """Replace the tree and reset progress, only if ``token`` is still current."""
    leaf_count = sum(1 for _ in iter_leaves(tree.get("nodes") or []))
    stamp = now_text()
    cursor = conn.execute(
        """
        UPDATE session_knowledge_trees
        SET status = ?, ai_gen_status = 'completed', ai_gen_error = NULL, tree_json = ?, completed_json = '[]',
            progress_percent = 0, leaf_count = ?, completed_count = 0, source_fingerprint = ?, source_label = ?,
            generated_at = ?, completed_at = NULL, version = version + 1, updated_at = ?
        WHERE id = ? AND ai_gen_task_id = ?
        """,
        (STATUS_READY, json.dumps(tree, ensure_ascii=False), leaf_count, fingerprint, source_label[:300],
         stamp, stamp, row_id, token),
    )
    return getattr(cursor, "rowcount", 1) != 0


def mark_generation_failed(conn, row_id: str, token: str, error: str) -> None:
    row = conn.execute("SELECT tree_json FROM session_knowledge_trees WHERE id = ?", (row_id,)).fetchone()
    has_tree = bool(row and _loads(row["tree_json"], {}).get("nodes"))
    conn.execute(
        "UPDATE session_knowledge_trees SET status = ?, ai_gen_status = 'failed', ai_gen_error = ?, updated_at = ? "
        "WHERE id = ? AND ai_gen_task_id = ?",
        (STATUS_READY if has_tree else STATUS_FAILED, str(error or "生成失败")[:500], now_text(), row_id, token),
    )


def _write_completion(conn, row: dict[str, Any], completed_ids: list[str]) -> dict[str, Any] | None:
    tree = _loads(row.get("tree_json"), {})
    state = compute_progress(tree, completed_ids)
    completed_at = row.get("completed_at") if state["progress"] >= 100 else None
    if state["progress"] >= 100 and not completed_at:
        completed_at = now_text()
    cursor = conn.execute(
        """
        UPDATE session_knowledge_trees
        SET completed_json = ?, progress_percent = ?, completed_count = ?, completed_at = ?,
            version = version + 1, updated_at = ?
        WHERE id = ? AND version = ? AND status <> 'generating'
        """,
        (json.dumps(state["completed_ids"]), state["progress"], state["completed_count"], completed_at,
         now_text(), row["id"], int(row["version"])),
    )
    if getattr(cursor, "rowcount", 1) == 0:
        return None
    return load_row(conn, int(row["session_id"]))


def update_completion(conn, session_id: int, mutate) -> dict[str, Any]:
    """Apply ``mutate(tree, completed) -> completed list`` with optimistic retries."""
    for _ in range(4):
        row = load_row(conn, session_id)
        if not row or not _loads(row.get("tree_json"), {}).get("nodes"):
            raise HTTPException(404, "本课次还没有知识树")
        if str(row.get("status") or "") == STATUS_GENERATING:
            raise HTTPException(409, "知识树正在重新生成，完成后再标记进度。")
        tree = _loads(row.get("tree_json"), {})
        completed = list(_loads(row.get("completed_json"), []))
        updated = _write_completion(conn, row, mutate(tree, completed))
        if updated:
            return updated
    raise HTTPException(409, "进度同时被修改，请刷新后重试。")


def set_leaf_done(conn, session_id: int, leaf_id: str, done: bool) -> dict[str, Any]:
    def mutate(tree, completed):
        leaf_ids = {leaf["id"] for leaf in iter_leaves(tree.get("nodes") or [])}
        if leaf_id not in leaf_ids:
            raise HTTPException(404, "只能标记知识树末梢的基础知识单元")
        rest = [item for item in completed if item != leaf_id]
        return rest + [leaf_id] if done else rest

    return update_completion(conn, session_id, mutate)


def set_all_done(conn, session_id: int, done: bool) -> dict[str, Any]:
    return update_completion(
        conn,
        session_id,
        lambda tree, _completed: [leaf["id"] for leaf in iter_leaves(tree.get("nodes") or [])] if done else [],
    )


def save_order(conn, session_id: int, parent_id: str, order: list[str], expected_version: int | None) -> dict[str, Any]:
    row = load_row(conn, session_id)
    if not row or not _loads(row.get("tree_json"), {}).get("nodes"):
        raise HTTPException(404, "本课次还没有知识树")
    if str(row.get("status") or "") == STATUS_GENERATING:
        raise HTTPException(409, "知识树正在重新生成，完成后再调整顺序。")
    if expected_version is not None and int(row["version"]) != int(expected_version):
        raise HTTPException(409, "知识树已在别处更新，请刷新后再调整顺序")
    tree = reorder_children(_loads(row.get("tree_json"), {}), str(parent_id or ""), order)
    cursor = conn.execute(
        "UPDATE session_knowledge_trees SET tree_json = ?, version = version + 1, updated_at = ? WHERE id = ? AND version = ? AND status <> 'generating'",
        (json.dumps(tree, ensure_ascii=False), now_text(), row["id"], int(row["version"])),
    )
    if getattr(cursor, "rowcount", 1) == 0:
        raise HTTPException(409, "知识树已在别处更新，请刷新后再调整顺序")
    return load_row(conn, session_id)


# ---------------------------------------------------------------------------
# Source materials and staleness
# ---------------------------------------------------------------------------
def session_material_ids(conn, class_offering_id: int, session: dict[str, Any], teacher_id: int) -> list[int]:
    """Material ids bound to the session (primary binding first)."""
    from .session_learning_materials_service import build_material_entries

    ids: list[int] = []
    primary = int(session.get("learning_material_id") or 0)
    if primary > 0:
        ids.append(primary)
    try:
        entries = build_material_entries(
            conn, int(class_offering_id), int(session["id"]), teacher_id=int(teacher_id), persist_legacy=False,
        )
    except HTTPException:
        raise
    except Exception as exc:  # binding table may be absent on legacy fixtures
        print(f"[KNOWLEDGE_TREE] material entries unavailable: {exc!r}")
        entries = []
    ids.extend(int(entry.get("material_id") or 0) for entry in entries)
    return [material_id for material_id in dict.fromkeys(ids) if material_id > 0]


def _source_rows(conn, material_ids: list[int]) -> list[tuple[Any, Any]]:
    """(bound material row, text-bearing file row) pairs."""
    from .material_render_service import resolve_render_target

    pairs = []
    for material_id in material_ids:
        row = conn.execute("SELECT * FROM course_materials WHERE id = ? LIMIT 1", (material_id,)).fetchone()
        if not row:
            continue
        file_row = row
        try:
            target = resolve_render_target(conn, row)
        except Exception:
            target = None
        entry_id = int((target or {}).get("entry_id") or 0)
        if entry_id and entry_id != int(row["id"]):
            entry = conn.execute("SELECT * FROM course_materials WHERE id = ? LIMIT 1", (entry_id,)).fetchone()
            file_row = entry or row
        pairs.append((row, file_row))
    return pairs


def source_fingerprints(conn, material_ids: list[int]) -> tuple[str, str]:
    """Content revision plus its legacy identity-based equivalent, without I/O.

    A Git rename, equivalent entry replacement, duplicate binding or binding
    reorder does not change the document content used to build a tree. Missing
    hashes remain identity-specific: unknown files must never compare equal.
    The legacy value only proves an existing tree is still current; it must not
    be used to mark an already-stale tree as current during an upgrade.
    """
    pairs = _source_rows(conn, material_ids)
    legacy_parts = [
        f"{int(row['id'])}:{int(file_row['id'])}:{file_row['file_hash'] or ''}"
        for row, file_row in pairs
    ]
    if not legacy_parts:
        return "", ""
    content_parts = sorted({
        str(file_row['file_hash']) if file_row['file_hash'] else f"missing:{int(file_row['id'])}"
        for _row, file_row in pairs
    })
    content = hashlib.sha256("|".join(content_parts).encode("utf-8")).hexdigest()
    legacy = hashlib.sha256("|".join(legacy_parts).encode("utf-8")).hexdigest()
    return f"content-v1:{content}", legacy


def source_fingerprint(conn, material_ids: list[int]) -> str:
    return source_fingerprints(conn, material_ids)[0]


def upgrade_repository_source_fingerprints(conn, root_id: int, teacher_id: int) -> int:
    """Preserve proven-current legacy trees before Git changes files/bindings.

    Runs within the publication transaction, before any material mutation. Only
    matching legacy revisions are upgraded; stale trees and in-flight generation
    are untouched. CAS protects an intervening tree edit/generation, and no tree
    content, progress, version or user-visible timestamps are changed.
    """
    from .git_learning_bindings_service import table_exists
    if not table_exists(conn, "session_knowledge_trees"):
        return 0
    binding_clause = ""
    params = [int(teacher_id), int(root_id)]
    if table_exists(conn, "class_offering_learning_materials"):
        binding_clause = """ OR EXISTS (
            SELECT 1 FROM class_offering_learning_materials b
            JOIN course_materials m ON m.id=b.material_id
            WHERE b.class_offering_id=s.class_offering_id AND b.session_id=s.id AND m.root_id=?)"""
        params.append(int(root_id))
    rows = conn.execute(f"""
        SELECT kt.*, s.learning_material_id
        FROM session_knowledge_trees kt
        JOIN class_offering_sessions s ON s.id=kt.session_id AND s.class_offering_id=kt.class_offering_id
        JOIN class_offerings o ON o.id=s.class_offering_id
        WHERE o.teacher_id=? AND kt.status <> 'generating'
          AND kt.source_fingerprint <> '' AND kt.source_fingerprint NOT LIKE 'content-v1:%'
          AND (EXISTS (SELECT 1 FROM course_materials m WHERE m.id=s.learning_material_id AND m.root_id=?)
               {binding_clause})
    """, params).fetchall()
    upgraded = 0
    for raw in rows:
        row = dict(raw)
        session = {"id": row["session_id"], "learning_material_id": row["learning_material_id"]}
        ids = session_material_ids(conn, int(row["class_offering_id"]), session, teacher_id)
        content, legacy = source_fingerprints(conn, ids)
        if legacy and str(row["source_fingerprint"]) == legacy:
            result = conn.execute("""UPDATE session_knowledge_trees SET source_fingerprint=?
                WHERE id=? AND version=? AND source_fingerprint=? AND status <> 'generating'""",
                (content, row["id"], int(row["version"]), legacy))
            upgraded += max(0, result.rowcount)
    return upgraded


def read_source_texts(conn, material_ids: list[int], *, char_budget: int) -> tuple[str, list[str]]:
    """Plain text of the bound Markdown/HTML learning documents."""
    from .html_package_service import extract_html_text, load_material_file_text

    texts: list[str] = []
    names: list[str] = []
    remaining = char_budget
    for row, file_row in _source_rows(conn, material_ids):
        if remaining <= 200:
            break
        if str(file_row["node_type"] or "") != "file":
            continue
        name = str(file_row["name"] or "").lower()
        preview = str(file_row["preview_type"] or "").lower()
        if name.endswith((".html", ".htm")) or preview == "html":
            text = extract_html_text(load_material_file_text(conn, file_row), max_chars=remaining)
        elif preview == "markdown" or name.endswith((".md", ".markdown", ".txt")):
            text = load_material_file_text(conn, file_row)[:remaining]
        else:
            continue
        text = text.strip()
        if text:
            names.append(str(row["name"] or ""))
            texts.append(f"## 学习文档：{row['name']}\n{text}")
            remaining -= len(text)
    return "\n\n---\n\n".join(texts), names


# ---------------------------------------------------------------------------
# Serialization and page summaries
# ---------------------------------------------------------------------------
def serialize(row: dict[str, Any] | None, *, can_edit: bool, has_material: bool, current_fingerprint: str | None = None,
              legacy_fingerprint: str | None = None) -> dict[str, Any]:
    row = row or {}
    tree = _loads(row.get("tree_json"), {})
    has_tree = bool(tree.get("nodes"))
    state = compute_progress(tree, _loads(row.get("completed_json"), [])) if has_tree else {
        "completed_ids": [], "progress": 0, "leaf_count": 0, "completed_count": 0, "weight_total": 0, "lifecycle": "none",
    }
    stored_fingerprint = str(row.get("source_fingerprint") or "")
    is_stale = bool(
        can_edit and has_tree and current_fingerprint is not None
        and stored_fingerprint not in (current_fingerprint, legacy_fingerprint)
    )
    return {
        "session_id": int(row["session_id"]) if row.get("session_id") else None,
        "status": str(row.get("status") or STATUS_EMPTY),
        "ai_gen_status": str(row.get("ai_gen_status") or ""),
        "error": str(row.get("ai_gen_error") or "") if row.get("ai_gen_status") == "failed" else "",
        "has_tree": has_tree,
        "tree": tree if has_tree else None,
        "version": int(row.get("version") or 0),
        "generated_at": row.get("generated_at") or "",
        "completed_at": row.get("completed_at") or "",
        "source_label": str(row.get("source_label") or ""),
        "has_material": bool(has_material),
        "can_edit": bool(can_edit),
        "is_stale": is_stale,
        "current_source_fingerprint": str(current_fingerprint or "") if can_edit else "",
        **state,
    }


def load_offering_progress(conn, class_offering_id: int) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT session_id, progress_percent, leaf_count, completed_count
        FROM session_knowledge_trees
        WHERE class_offering_id = ? AND leaf_count > 0
        """,
        (int(class_offering_id),),
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        percent = int(row["progress_percent"] or 0)
        result[int(row["session_id"])] = {
            "progress": percent,
            "lifecycle": lifecycle_state(True, percent, int(row["completed_count"] or 0)),
            "leaf_count": int(row["leaf_count"] or 0),
            "completed_count": int(row["completed_count"] or 0),
        }
    return result


def attach_session_knowledge_progress(conn, class_offering_id: int, session_items: list[dict[str, Any]]) -> None:
    """Decorate classroom timeline items with ``knowledge_progress`` (best effort)."""
    from ..db.savepoint import best_effort

    progress: dict[int, dict[str, Any]] = {}
    with best_effort(conn, "knowledge_tree_progress", log_prefix="[KNOWLEDGE_TREE]"):
        progress = load_offering_progress(conn, class_offering_id)
    for item in session_items:
        item["knowledge_progress"] = progress.get(int(item.get("id") or 0))


# ---------------------------------------------------------------------------
# Offering-wide management (文档与知识树 batch panel)
# ---------------------------------------------------------------------------
BATCH_MODES = {"missing", "stale", "all"}
BATCH_LIMIT = 80


def _session_entries(conn, class_offering_id: int, session: dict[str, Any], teacher_id: int) -> list[dict[str, Any]]:
    from .session_learning_materials_service import build_material_entries

    try:
        return build_material_entries(conn, int(class_offering_id), int(session["id"]), teacher_id=int(teacher_id), persist_legacy=False)
    except HTTPException:
        raise
    except Exception as exc:  # binding table may be absent on legacy fixtures
        print(f"[KNOWLEDGE_TREE] material entries unavailable: {exc!r}")
        return []


def list_offering_trees(conn, class_offering_id: int, teacher_id: int) -> list[dict[str, Any]]:
    """Every active session with its bound documents and knowledge-tree state."""
    sessions = conn.execute(
        """
        SELECT id, order_index, title, session_date, learning_material_id
        FROM class_offering_sessions
        WHERE class_offering_id = ? AND COALESCE(schedule_status, 'scheduled') NOT IN ('cancelled', 'canceled')
        ORDER BY session_date, order_index
        """,
        (int(class_offering_id),),
    ).fetchall()
    rows = {
        int(row["session_id"]): dict(row)
        for row in conn.execute("SELECT * FROM session_knowledge_trees WHERE class_offering_id = ?", (int(class_offering_id),)).fetchall()
    }
    result = []
    for raw in sessions:
        session = dict(raw)
        entries = _session_entries(conn, class_offering_id, session, teacher_id)
        ids = [int(session["learning_material_id"])] if session.get("learning_material_id") else []
        ids += [int(entry["material_id"]) for entry in entries]
        ids = [material_id for material_id in dict.fromkeys(ids) if material_id > 0]
        named = {int(entry["material_id"]): entry for entry in entries}
        materials = []
        for material_id in ids:
            entry = named.get(material_id)
            if entry is None:
                found = conn.execute("SELECT name FROM course_materials WHERE id = ?", (material_id,)).fetchone()
                entry = {"name": found["name"] if found else f"材料 {material_id}", "open_url": ""}
            materials.append({"id": material_id, "name": str(entry.get("name") or ""), "open_url": str(entry.get("open_url") or "")})
        row = rows.get(int(session["id"]))
        if row:
            row = expire_stale_generation(conn, row)
        fingerprint, legacy = source_fingerprints(conn, ids) if ids and row else ("", "")
        state = serialize(row, can_edit=True, has_material=bool(ids),
                          current_fingerprint=fingerprint if row else None, legacy_fingerprint=legacy or None)
        state.pop("tree", None)
        result.append({
            **state,
            "session_id": int(session["id"]),
            "order_index": int(session.get("order_index") or 0),
            "title": str(session.get("title") or ""),
            "session_date": str(session.get("session_date") or ""),
            "materials": materials,
        })
    return result


def select_batch_targets(rows: list[dict[str, Any]], mode: str) -> tuple[list[int], list[dict[str, Any]]]:
    """Session ids a batch should (re)generate, plus the skipped ones with reasons."""
    if mode not in BATCH_MODES:
        raise HTTPException(400, "未知的批量生成方式")
    targets: list[int] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        sid = int(row["session_id"])
        if row.get("status") == STATUS_GENERATING:
            skipped.append({"session_id": sid, "reason": "正在生成"})
        elif not row.get("has_material"):
            skipped.append({"session_id": sid, "reason": "未绑定学习材料"})
        elif mode == "missing" and row.get("has_tree"):
            continue
        elif mode == "stale" and not (row.get("has_tree") and row.get("is_stale")):
            continue
        else:
            targets.append(sid)
    if len(targets) > BATCH_LIMIT:
        skipped += [{"session_id": sid, "reason": "超出单次批量上限"} for sid in targets[BATCH_LIMIT:]]
        targets = targets[:BATCH_LIMIT]
    return targets, skipped
