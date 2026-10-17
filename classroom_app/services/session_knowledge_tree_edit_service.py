"""课次知识树的手动编辑：新增、修改、删除、移动节点与比重归一化。

规则真源（前端 static/js/knowledge_tree/model.js 只做同规则的乐观预演）：

* 根节点（课次主题）不可删除、不可移动；整棵树最多三层，至少保留一个大知识点。
* 新增节点总是新的末梢。父节点原本是末梢时，新节点**继承**它的比重与完成状态
  （父节点被细分，内容不变，总比重不变）；父节点是中间节点时，新末梢先补足合计
  与 100 的差额，已满 100 时取现有末梢的平均比重——此时合计会超过 100，编辑器提示
  并提供「按比例调整为 100%」。
* 删除中间节点会连同下级一起删除；删除后父节点若没有下级，就重新成为末梢，并承接
  被删末梢的比重之和与完成状态（全部完成才算完成），保持总比重不变。
* 移动保持层级不变（拖动只换父节点或顺序），目标父节点须为中间节点；原父节点
  因此没有下级时成为比重 0 的末梢。
* 大知识点的配色 ``tone`` 在第一次编辑前按当前位置固定，之后排序不会换色。
* 「按比例调整为 100%」只缩放大于 0 的比重（教师设为 0 的单元保持 0）；全树最多 100 个基础单元。
* 所有写入都基于最新行重放（节点 id 稳定），用 version 乐观并发并重试，完成记录
  只保留仍然存在的末梢。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import HTTPException

from . import session_knowledge_tree_service as kt

MANUAL_MAX_TOP = 8
MANUAL_MAX_CHILDREN = 12
MANUAL_MAX_NODES = 160
MANUAL_MAX_LEAVES = 100  # every unit keeps ≥1 when weights are normalized to 100
WEIGHT_MAX = 100
DETAIL_EDIT_LIMITS = {**kt.DETAIL_FIELDS, "detail": 600}  # teachers may write a longer introduction
DEFAULT_LABEL = "新知识点"
ROOT_ID = "__root"  # client id of the session theme (tree title/summary)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def _locate(tree: dict[str, Any], node_id: str) -> dict[str, Any]:
    """{node, parent (None = root), siblings, level} for ``node_id``."""
    def walk(nodes, parent, level):
        for node in nodes:
            if node.get("id") == node_id:
                return {"node": node, "parent": parent, "siblings": nodes, "level": level}
            found = walk(node.get("children") or [], node, level + 1)
            if found:
                return found
        return None

    found = walk(tree.get("nodes") or [], None, 1)
    if not found:
        raise HTTPException(404, "知识点不存在，可能已被删除，请刷新后重试。")
    return found


def _children_of(tree: dict[str, Any], parent_id: str) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    """(children list, level of those children, parent node or None for root)."""
    if not parent_id:
        return tree.setdefault("nodes", []), 1, None
    found = _locate(tree, parent_id)
    return found["node"].setdefault("children", []), found["level"] + 1, found["node"]


BRANCH_TONES = 8


def _copy(tree: dict[str, Any]) -> dict[str, Any]:
    """Deep copy with every major point's colour pinned before positions change."""
    tree = json.loads(json.dumps(tree))
    for index, node in enumerate(tree.get("nodes") or []):
        if not isinstance(node.get("tone"), int):
            node["tone"] = index % BRANCH_TONES
    return tree


def _all_ids(tree: dict[str, Any]) -> set[str]:
    return {node["id"] for node in kt.iter_nodes(tree.get("nodes") or [])}


def _check_capacity(siblings: list[dict[str, Any]], level: int) -> None:
    limit = MANUAL_MAX_TOP if level == 1 else MANUAL_MAX_CHILDREN
    if len(siblings) >= limit:
        raise HTTPException(409, f"同一层最多 {limit} 个知识点。")


def _clean_label(value: Any) -> str:
    label = kt._clean_text(value, kt.LABEL_LIMIT)
    if not label:
        raise HTTPException(400, "知识点名称不能为空。")
    return label


def _clean_weight(value: Any) -> int:
    try:
        weight = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, "比重必须是 0–100 的整数。") from None
    if weight < 0 or weight > WEIGHT_MAX:
        raise HTTPException(400, "比重必须是 0–100 的整数。")
    return weight


def _make_leaf(node: dict[str, Any], weight: int) -> None:
    node.pop("children", None)
    node["weight"] = weight


def _drop_empty_children(nodes: list[dict[str, Any]]) -> None:
    for node in nodes:
        if "children" in node and not node["children"]:
            node.pop("children")
        _drop_empty_children(node.get("children") or [])


def _finish(tree: dict[str, Any], completed: list[str]) -> tuple[dict[str, Any], list[str]]:
    _drop_empty_children(tree.get("nodes") or [])
    tree["depth"] = kt.tree_depth(tree.get("nodes") or [])
    tree["edited_at"] = kt.now_text()
    alive = {leaf["id"] for leaf in kt.iter_leaves(tree.get("nodes") or [])}
    return tree, [item for item in dict.fromkeys(completed) if item in alive]


def default_new_weight(tree: dict[str, Any]) -> int:
    """Fill the gap to 100 first; otherwise the average unit weight."""
    leaves = list(kt.iter_leaves(tree.get("nodes") or []))
    total = sum(kt.leaf_weight(leaf) for leaf in leaves)
    if total < 100:
        return 100 - total
    return max(1, min(WEIGHT_MAX, int(total / len(leaves) + 0.5))) if leaves else 100


# ---------------------------------------------------------------------------
# Pure operations: (tree, completed) -> (tree, completed, extra)
# ---------------------------------------------------------------------------
def add_node(tree, completed, *, parent_id: str, label: str = "", index: int | None = None):
    tree = _copy(tree)
    completed = list(completed)
    if sum(1 for _ in kt.iter_nodes(tree.get("nodes") or [])) >= MANUAL_MAX_NODES:
        raise HTTPException(409, f"一棵知识树最多 {MANUAL_MAX_NODES} 个知识点。")
    parent_id = "" if parent_id == ROOT_ID else str(parent_id or "")
    siblings, level, parent = _children_of(tree, parent_id)
    if level > kt.MAX_DEPTH:
        raise HTTPException(409, "知识树最多三层，基础单元下不能再添加下级。")
    _check_capacity(siblings, level)
    subdividing = parent is not None and not siblings
    if not subdividing and sum(1 for _ in kt.iter_leaves(tree.get("nodes") or [])) >= MANUAL_MAX_LEAVES:
        raise HTTPException(409, f"一棵知识树最多 {MANUAL_MAX_LEAVES} 个基础单元。")
    node = {"id": kt._new_node_id(_all_ids(tree)), "label": _clean_label(label or DEFAULT_LABEL)}
    if parent is None:
        used = {item.get("tone") for item in siblings}
        node["tone"] = next((tone for tone in range(BRANCH_TONES) if tone not in used), len(siblings) % BRANCH_TONES)
    if subdividing:
        # The parent unit is being subdivided: the child carries its share and state.
        node["weight"] = kt.leaf_weight(parent)
        parent.pop("weight", None)
        if parent["id"] in completed:
            completed = [node["id"] if item == parent["id"] else item for item in completed]
    else:
        node["weight"] = default_new_weight(tree)
    position = len(siblings) if index is None else max(0, min(int(index), len(siblings)))
    siblings.insert(position, node)
    tree, completed = _finish(tree, completed)
    return tree, completed, {"created_id": node["id"]}


def update_node(tree, completed, *, node_id: str, changes: dict[str, Any]):
    tree = _copy(tree)
    if node_id == ROOT_ID:
        # The root is the session theme: its label is the tree title, its detail the summary.
        if changes.get("label") is not None:
            tree["title"] = kt._clean_text(changes["label"], 60) or tree.get("title", "")
        if changes.get("detail") is not None:
            tree["summary"] = kt._clean_text(changes["detail"], 200)
        if changes.get("weight") is not None:
            raise HTTPException(400, "只有基础单元（末梢）可以设置比重。")
        tree, completed = _finish(tree, list(completed))
        return tree, completed, {}
    node = _locate(tree, node_id)["node"]
    is_leaf = not node.get("children")
    for key, value in changes.items():
        if value is None:
            continue
        if key == "label":
            node["label"] = _clean_label(value)
        elif key in kt.DETAIL_FIELDS:
            text = kt._clean_text(value, DETAIL_EDIT_LIMITS[key])
            if text:
                node[key] = text
            else:
                node.pop(key, None)
        elif key == "weight":
            if not is_leaf:
                raise HTTPException(400, "只有基础单元（末梢）可以设置比重。")
            node["weight"] = _clean_weight(value)
    tree, completed = _finish(tree, list(completed))
    return tree, completed, {}


def delete_node(tree, completed, *, node_id: str):
    tree = _copy(tree)
    completed = list(completed)
    found = _locate(tree, node_id)
    node, parent, siblings = found["node"], found["parent"], found["siblings"]
    if parent is None and len(siblings) <= 1:
        raise HTTPException(409, "至少保留一个大知识点；如需整体替换请使用“重新生成”。")
    removed_leaves = list(kt.iter_leaves([node]))
    removed_ids = {item["id"] for item in kt.iter_nodes([node])}
    siblings.remove(node)
    if parent is not None and not siblings:
        # The parent becomes a unit again and keeps the removed share and state.
        _make_leaf(parent, sum(kt.leaf_weight(leaf) for leaf in removed_leaves))
        if removed_leaves and all(leaf["id"] in completed for leaf in removed_leaves):
            completed.append(parent["id"])
    completed = [item for item in completed if item not in removed_ids]
    tree, completed = _finish(tree, completed)
    return tree, completed, {"removed": sorted(removed_ids), "parent_id": parent["id"] if parent else ""}


def move_node(tree, completed, *, node_id: str, parent_id: str, index: int):
    tree = _copy(tree)
    found = _locate(tree, node_id)
    node, old_parent, old_siblings = found["node"], found["parent"], found["siblings"]
    target_id = "" if parent_id == ROOT_ID else str(parent_id or "")
    if target_id and target_id in {item["id"] for item in kt.iter_nodes([node])}:
        raise HTTPException(400, "不能把知识点移动到它自己的下级。")
    siblings, level, target = _children_of(tree, target_id)
    if level != found["level"]:
        raise HTTPException(400, "拖动只能在同一层级之间调整位置。")
    if target is not None and not siblings:
        raise HTTPException(400, "目标是基础单元，请先为它添加下级再移入。")
    if siblings is not old_siblings:
        _check_capacity(siblings, level)
    old_siblings.remove(node)
    siblings.insert(max(0, min(int(index), len(siblings))), node)
    if old_parent is not None and not old_siblings:
        _make_leaf(old_parent, 0)
    tree, completed = _finish(tree, list(completed))
    return tree, completed, {}


def normalize_weights(tree, completed):
    """Scale positive weights to total 100; units the teacher set to 0 stay 0."""
    tree = _copy(tree)
    leaves = list(kt.iter_leaves(tree.get("nodes") or []))
    weighted = [leaf for leaf in leaves if kt.leaf_weight(leaf) > 0] or leaves
    try:
        weights = kt.distribute_weights([float(kt.leaf_weight(leaf)) for leaf in weighted])
    except kt.KnowledgeTreeError as exc:
        raise HTTPException(409, f"基础单元超过 {MANUAL_MAX_LEAVES} 个，无法按整数比重分配。") from exc
    for leaf, weight in zip(weighted, weights):
        leaf["weight"] = weight
    tree, completed = _finish(tree, list(completed))
    return tree, completed, {}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
Operation = Callable[[dict[str, Any], list[str]], tuple[dict[str, Any], list[str], dict[str, Any]]]


def edit_tree(conn, session_id: int, operation: Operation) -> tuple[dict[str, Any], dict[str, Any]]:
    """Replay ``operation`` on the latest tree with optimistic version retries."""
    for _ in range(4):
        row = kt.load_row(conn, session_id)
        tree = kt._loads((row or {}).get("tree_json"), {})
        if not row or not tree.get("nodes"):
            raise HTTPException(404, "本课次还没有知识树")
        if str(row.get("status") or "") == kt.STATUS_GENERATING:
            raise HTTPException(409, "知识树正在重新生成，完成后再编辑。")
        new_tree, completed, extra = operation(tree, list(kt._loads(row.get("completed_json"), [])))
        state = kt.compute_progress(new_tree, completed)
        completed_at = row.get("completed_at") if state["progress"] >= 100 else None
        if state["progress"] >= 100 and not completed_at:
            completed_at = kt.now_text()
        cursor = conn.execute(
            """
            UPDATE session_knowledge_trees
            SET tree_json = ?, completed_json = ?, progress_percent = ?, leaf_count = ?, completed_count = ?,
                completed_at = ?, version = version + 1, updated_at = ?
            WHERE id = ? AND version = ? AND status <> ?
            """,
            # start_generation flips status without bumping version: never write into a run.
            (json.dumps(new_tree, ensure_ascii=False), json.dumps(state["completed_ids"]), state["progress"],
             state["leaf_count"], state["completed_count"], completed_at, kt.now_text(), row["id"], int(row["version"]),
             kt.STATUS_GENERATING),
        )
        if getattr(cursor, "rowcount", 1) != 0:
            return kt.load_row(conn, session_id), extra
    raise HTTPException(409, "知识树同时被修改，请刷新后重试。")
