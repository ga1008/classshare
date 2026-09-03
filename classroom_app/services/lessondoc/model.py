"""Bounded model traversal shared by normalization, editor and reuse services."""

import copy
import json
import re

from . import spec


def check_budget(payload):
    stack = [(payload, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > 24000 or depth > 32:
            raise ValueError("文档结构过大或嵌套超过 32 层")
        if isinstance(node, dict):
            stack.extend((value, depth + 1) for value in node.values())
        elif isinstance(node, list):
            stack.extend((value, depth + 1) for value in node)
    try:
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("文档必须为有限值 JSON") from exc
    if len(raw.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("文档不能超过 2 MiB")
    if isinstance(payload, dict):
        def blocks(root):
            return sum(1 for _, node in walk_model(root) if node.get("type") in spec.BLOCK_TYPES)
        if blocks(payload) > 2000:
            raise ValueError("文档元素总数不能超过 2000")
        for slide in payload.get("slides") if isinstance(payload.get("slides"), list) else []:
            if blocks(slide) > 160:
                raise ValueError("每页元素总数不能超过 160（含嵌套元素）")


def walk_model(value, path=""):
    """Yield every model object with its unambiguous JSON path."""
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from walk_model(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_model(item, f"{path}[{index}]")


def ensure_editor_ids(payload):
    """Assign missing IDs only on editor load; preserve old read/generation output."""
    out = copy.deepcopy(payload)
    known = {n["id"] for _, n in walk_model(out) if isinstance(n.get("id"), str)}
    counter = 0
    for path, node in walk_model(out):
        is_slide = path.startswith("slides[") and path.count("[") == 1 and "." not in path
        is_block = node.get("type") in spec.BLOCK_TYPES or (isinstance(node.get("type"), str) and bool(re.search(r"(?:blocks|left|right|objects|overlays|globals|children)\[\d+\]$|\.stage$", path)))
        if not (is_slide or is_block) or node.get("id"):
            continue
        while True:
            counter += 1
            candidate = ("s" if is_slide else "b") + str(counter)
            if candidate not in known:
                break
        known.add(candidate)
        node["id"] = candidate
    return out


def normalization_diagnostics(original, clean, warnings):
    """Detect content loss within the submitted edit, never against the old revision.

    IDs are assigned before editor validation, so deletion/reordering by the author
    is accepted while validator-dropped/replaced objects can block a save.
    """
    clean_ids = {n.get("id"): n for _, n in walk_model(clean) if n.get("id") and (n.get("type") in spec.BLOCK_TYPES or "layout" in n)}
    diagnostics = []
    for path, node in walk_model(original):
        if not node.get("id") or not (node.get("type") or "layout" in node):
            continue
        result = clean_ids.get(node["id"])
        if result is None or result.get("type") != node.get("type"):
            diagnostics.append(dict(code="CONTENT_DROPPED", path=path, object_id=node["id"], severity="error", destructive=True, message="校验将删除或替换此内容，请修正后保存"))
        elif node.get("type") in {"table", "codewalk", "quiz", "stepper"}:
            for field in ("rows", "lines", "options", "steps"):
                if isinstance(node.get(field), list) and len(node[field]) > len(result.get(field) or []):
                    diagnostics.append(dict(code="ITEMS_DROPPED", path=f"{path}.{field}", object_id=node["id"], severity="error", destructive=True, message="校验将截断或删除内容条目，请修正后保存"))
            if node.get("type") == "quiz":
                keys = [str(option.get("k", option.get("key", ""))) for option in node.get("options") or [] if isinstance(option, dict)]
                if not keys or len(set(keys)) != len(keys) or any(not key for key in keys) or str(node.get("answer") or "") not in keys:
                    diagnostics.append(dict(code="QUIZ_ANSWER_INVALID", path=f"{path}.answer", object_id=node["id"], severity="error", destructive=True, message="测验选项标记须唯一，且必须明确选择正确答案"))
            if node.get("type") == "codewalk" and len(node.get("lines") or []) == len(result.get("lines") or []):
                for i, (line, after) in enumerate(zip(node.get("lines") or [], result.get("lines") or [])):
                    line = {"code": line} if isinstance(line, str) else line
                    if isinstance(line, dict) and any(isinstance(line.get(key), str) and len(line[key]) > len(str(after.get(key) or "")) for key in ("code", "out", "note")):
                        diagnostics.append(dict(code="CODEWALK_TRUNCATED", path=f"{path}.lines[{i}]", object_id=node["id"], severity="error", destructive=True, message="代码或解释超出长度限制，请拆分后保存"))
    diagnostics.extend(dict(code="NORMALIZED", path="", object_id=None, severity="warning", destructive=False, message=w) for w in warnings)
    return diagnostics
