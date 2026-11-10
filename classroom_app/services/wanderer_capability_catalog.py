"""Search and bounded, lossless discovery over an already-authorized catalog.

This module never resolves or grants capabilities. Aliases describe vocabulary,
not alternate business operations; execution retains the original route policy.
"""
from __future__ import annotations

import json
import unicodedata

from .agent_capability_catalog_service import search_catalog_items

_PAGE_BUDGET = 3000  # Runtime's durable receipt envelope has a 4000-char prompt limit.


def _packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _normal(value):
    return unicodedata.normalize("NFKC", str(value)).casefold().strip()


def search(items, query):
    """Prefer exact ids, then all search concepts, then an explicit partial match."""
    query = _normal(query)[:256]
    if not query:
        return list(items), "browse"
    exact = [item for item in items if query in {
        _normal(item.get("key", "")), _normal(item.get("capability", "")),
        *(_normal(alias) for alias in item.get("aliases", [])),
    }]
    if exact:
        return exact, "exact"
    return search_catalog_items(items, query, limit=None, prefer_complete=True)


def page(items, *, query="", offset=0, capability=""):
    """Keep whole entries and continuation metadata inside one callback receipt.

    Oversized entries have an exact detail link. Detail JSON is losslessly sliced
    with its own explicit continuation so even arbitrarily large schemas work.
    """
    offset = max(0, int(offset))
    query, capability = str(query)[:256], str(capability)[:256]
    if capability:
        matches, mode = search(items, capability)
        if mode != "exact":
            return {"items": [], "total": 0, "next_offset": None,
                    "notice": "能力编号不存在或当前账号不可发现；请用query检索。"}
        item = matches[0]
        encoded = _packed(item)
        if len(encoded) < 2600:
            return {"item": item, "next_offset": None}
        fragment = encoded[offset:offset + 1200]
        next_offset = offset + len(fragment) if offset + len(fragment) < len(encoded) else None
        return {"capability": item["key"], "format": "json_fragment", "detail_json": fragment,
                "offset": offset, "total_characters": len(encoded), "next_offset": next_offset,
                "next": {"capability": "platform.capabilities", "args": {
                    "capability": item["key"], "offset": next_offset}} if next_offset is not None else None,
                "notice": "按next继续读取并拼接detail_json，即完整能力元数据；offset在详情模式按字符计。"}
    matches, mode = search(items, query)
    response = {"items": [], "total": len(matches), "next_offset": None, "search_mode": mode,
                "request_format": "HTTP能力的args使用path_params、query_params、body；参数in=path/query对应前两者，body_schema对应body。"}
    if mode in {"empty", "partial"}:
        response["notice"] = ("未找到全部关键词，以下为部分匹配。" if matches else "没有匹配能力。") + "可减少关键词、使用准确key/aliases，或query为空浏览；空结果不代表无权限。"
    for item in matches[offset:offset + 40]:
        candidate = item
        if len(_packed(item)) > 2200:
            candidate = {name: item[name] for name in ("key", "aliases", "label", "method", "path", "mutates") if name in item}
            candidate["detail"] = {"capability": "platform.capabilities", "args": {"capability": item["key"]}}
        # Reserve space for the continuation, including the original query.
        if response["items"] and len(_packed(response)) + len(_packed(candidate)) > _PAGE_BUDGET - 450:
            break
        response["items"].append(candidate)
    next_offset = offset + len(response["items"])
    if next_offset < len(matches):
        response["next_offset"] = next_offset
        response["next"] = {"capability": "platform.capabilities", "args": {"query": query, "offset": next_offset}}
    return response
