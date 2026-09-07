"""Public career exploration projection; raw historical graphs remain immutable.

Research digests and model-provided `verified` flags are not field-level source
verification. Until a reviewed claim/source contract exists, market forecasts,
pay figures and promotion promises are not published as career facts.
"""
from __future__ import annotations

import copy
import re
from functools import lru_cache
from typing import Any

from .career_stage_service import build_career_stages, build_career_time_axis

PUBLIC_VIEW_VERSION = "career-public-view-v3"
MARKET_NOTE = "这是职业探索方向。当前薪酬、招聘需求和录用条件须以有来源、仍有效的具体岗位公告为准。"
EXPLORATION_REASON = "结合明确的职业兴趣与已有实践证据，选择下一项可验证的学习或体验任务。"
# This detector is only for discrete preparation/advice entries. Public market,
# description and timeline fields use controlled text regardless of detection.
# Never remove digits generally: CET4/Java17, experience and certificate validity
# are distinct from pay/market claims and remain intact as items to verify.
_PAY_FIGURE = re.compile(r"(?:月薪|年薪|薪资|薪酬|工资|待遇|收入)[^。！？\n]{0,24}(?:\d|[一二三四五六七八九十百千万两]).{0,8}(?:[kKwW万千元]|倍|%|％)")
_MARKET_CLAIM = re.compile(r"(?:招聘|就业|岗位需求|人才需求|市场需求|职位需求|薪资|薪酬|年薪|月薪)[^。！？\n]{0,28}(?:翻倍|增长|上涨|供不应求|缺口|最热门|最高|稳拿|保证|必达|必然|必定)")
_GUARANTEE = re.compile(r"(?:保证|必然|必定|稳拿|必达|包)[^。！？\n]{0,18}(?:晋升|升职|经理|总监|录用|就业|offer|年薪|月薪)|(?:\d|[一二三四五六七八九十])[0-9一二三四五六七八九十至到–—-]*\s*年[^。！？\n]{0,10}(?:必达|必升|升至|升为|晋升|成为|做到)|(?:不会|永不|不被|无法)[^。！？\n]{0,12}(?:AI替代|AI取代|人工智能替代)", re.I)


def contains_market_claim(value: Any) -> bool:
    text = str(value or "")
    return any(pattern.search(text) for pattern in (_PAY_FIGURE, _MARKET_CLAIM, _GUARANTEE))


def project_advice(value: Any, fallback: str = EXPLORATION_REASON) -> str:
    text = str(value or "")
    return fallback if contains_market_claim(text) else text


@lru_cache(maxsize=1)
def _maintained_descriptions() -> dict[str, str]:
    from .career_seed_data import SE_NODES
    return {node["name"]: node["desc"] for node in SE_NODES if not contains_market_claim(node.get("desc"))}


def project_network_for_public(network: dict[str, Any]) -> dict[str, Any]:
    """Project content without changing topology, stable IDs or the input object."""
    public = copy.deepcopy(network)
    public.update(public_view_version=PUBLIC_VIEW_VERSION, market_data_verified=False,
                  content_kind="career_exploration",
                  time_axis=build_career_time_axis(),
                  intro="横轴以毕业为起点，显示各列的参考年限；沿路径查看相应职位与职责，规划准备重点。年限为规划参考，不承诺按时晋升；进修、转行及执业资格路径用时可能不同，具体条件须核对实际岗位公告。")
    public["graduate_label"] = str(public.get("major_name") or "专业") + "职业探索"
    for category in public.get("cats", []):
        category["desc"] = "结合实际职责、准备要求和个人实践了解这一组方向。"
        if contains_market_claim(category.get("name")):
            category["name"] = "职业探索方向"
    for index, node in enumerate(public.get("nodes", []), 1):
        if contains_market_claim(node.get("name")):
            node["name"] = f"职业探索方向 {index}"
        name = str(node.get("name") or "该方向")
        # Legacy shared graph scores were based on unsourced market/pay claims.
        # Actual student-specific evidence scores are applied after projection.
        node["rec"] = 3
        node["desc"] = _maintained_descriptions().get(name) or f"可通过岗位说明、课程实践或从业者访谈，了解“{name}”的具体职责和工作环境。"
        node["reason"] = EXPLORATION_REASON
        node["trend"] = MARKET_NOTE
        node["tl"] = [[stage[0], "" if contains_market_claim(stage[1]) else stage[1], stage[2]]
                      for stage in node.get("tl", [])]
        node["tl"] = build_career_stages(node, major_name=str(public.get("major_name") or ""))
        for stage in node["tl"]:
            stage[2] = project_advice(stage[2])
        node["branch"] = "结合已有能力和个人选择，了解相邻方向的职责差异与转向要求。"
        for field in ("pre", "know"):
            node[field] = [item for item in node.get(field, []) if not contains_market_claim(item)]
            if not node[field]:
                node[field] = ["核对实际岗位的职责、学历、经验和资格要求，再选择准备任务。"]
        if "tip" in node:
            node["tip"] = project_advice(node["tip"])
        for field in ("salary", "salary_range", "market_salary", "promotion_years"):
            node.pop(field, None)
    return public


def project_personalized_advice(personalized: dict[str, Any]) -> dict[str, Any]:
    """Keep explicit evidence explanations; neutralize unsupported guarantees."""
    if not personalized:
        return {}
    public = copy.deepcopy(personalized)
    for field in ("greeting", "summary", "region_note", "timeline_advice"):
        if field in public:
            public[field] = project_advice(public[field], MARKET_NOTE if field == "region_note" else EXPLORATION_REASON)
    public["node_tips"] = {tag: project_advice(value) for tag, value in (public.get("node_tips") or {}).items()}
    for item in public.get("top_paths", []):
        if isinstance(item, dict):
            item["why"] = project_advice(item.get("why"))
            if contains_market_claim(item.get("name")):
                item["name"] = "职业探索方向"
    return public
