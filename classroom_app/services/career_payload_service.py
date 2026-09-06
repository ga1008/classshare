"""Strict publication contract for untrusted career AI output."""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from .career_recommendation_service import payload_hash
from .psych_profile_service import sanitize_hidden_profile_leaks

SCHEMA_VERSION = "career-network-v3"
ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
GENERATION_CONTRACT = "career-directions-compact-v2"


def _network_parts(payload: Any):
    """Bound collections before traversing or expanding untrusted candidates."""
    if not isinstance(payload, dict):
        raise ValueError("职业网络必须是对象")
    cats, nodes, links = (payload.get(key) for key in ("cats", "nodes", "links"))
    if not isinstance(cats, list) or not 1 <= len(cats) <= 12 or not isinstance(nodes, list) or not 6 <= len(nodes) <= 60:
        raise ValueError("职业网络需要1至12类、6至60个有效方向")
    if not isinstance(links, list) or len(links) > 240:
        raise ValueError("职业关系必须为不超过240条的列表")
    return cats, nodes, links


def _direction_name_key(name: str) -> str:
    return "".join(unicodedata.normalize("NFKC", name).split()).casefold()


def assign_network_direction_ids(payload: Any, major_name: str, previous_directions=None):
    """Resolve identity from exact normalized names, never a model's opaque IDs.

    A changed meaning/name gets a new identity. Ambiguous or malformed legacy
    identity entries are not a license to attach somebody's saved feedback to
    another direction. Full historical payload validation remains strict.
    """
    from .career_seed_data import normalize_major_key
    cats, nodes, links = _network_parts(payload)
    by_name, id_uses = {}, {}
    if isinstance(previous_directions, list) and len(previous_directions) <= 60:
        for old in previous_directions:
            if not isinstance(old, dict):
                continue
            old_id, old_name = old.get("direction_id"), old.get("name")
            if not isinstance(old_id, str) or not ID.fullmatch(old_id) or not isinstance(old_name, str) or not old_name.strip() or len(old_name) > 80:
                continue
            key = _direction_name_key(old_name)
            by_name.setdefault(key, []).append(old_id)
            id_uses[old_id] = id_uses.get(old_id, 0) + 1
    resolved = []
    for item in nodes:
        if not isinstance(item, dict):
            raise ValueError("职业方向必须是对象")
        name = text(item.get("name", ""), 80)
        key = _direction_name_key(name)
        matches = by_name.get(key, [])
        stable_id = matches[0] if len(matches) == 1 and id_uses[matches[0]] == 1 else (
            "direction-" + payload_hash({"major": normalize_major_key(major_name), "name": key})[:24]
        )
        resolved.append({**item, "direction_id": stable_id})
    return {**payload, "cats": cats, "nodes": resolved, "links": links}


def expand_network_candidate(payload: Any, major_name: str, *, previous_directions=None) -> dict[str, Any]:
    """Add server-owned presentation fields before strict graph validation.

    Models only propose topology, interests and preparation items. Repeating
    shared stage/market paragraphs per node wastes generation and is not an
    independent source of evidence. Model-local tags and links are validated;
    persistent IDs are server-owned and excluded from the generation contract.
    """
    from .career_public_view_service import STAGES, EXPLORATION_REASON, MARKET_NOTE
    raw_cats, raw_nodes, links = _network_parts(payload)
    palette = (("#6ee7ff", "#3b82f6"), ("#a78bfa", "#7c3aed"),
               ("#6ee7b7", "#059669"), ("#fda4af", "#e11d48"))
    cats = []
    for index, item in enumerate(raw_cats):
        if not isinstance(item, dict):
            raise ValueError("职业类别必须是对象")
        first, second = palette[index % len(palette)]
        cats.append({"id": item.get("id"), "name": item.get("name"), "c1": first, "c2": second})
    nodes = []
    for item in raw_nodes:
        if not isinstance(item, dict):
            raise ValueError("职业方向必须是对象")
        node = {key: item[key] for key in ("tag", "cat", "name", "riasec", "lang", "pre", "know") if key in item}
        node.update(rec=3, tl=[list(stage) for stage in STAGES], reason=EXPLORATION_REASON, trend=MARKET_NOTE)
        nodes.append(node)
    assigned = assign_network_direction_ids({"cats": cats, "nodes": nodes, "links": links}, major_name, previous_directions)
    return validate_network_payload(assigned, major_name)


def text(value: Any, limit: int = 1200) -> str:
    if not isinstance(value, str):
        raise ValueError("职业内容文本类型不正确")
    if len(value)>limit or any(ord(x)<32 and x not in "\n\t\r" for x in value):
        raise ValueError("职业内容超过长度限制或含控制字符")
    return sanitize_hidden_profile_leaks(value.strip())


def text_list(value: Any, *, maximum=12):
    if not isinstance(value,list) or len(value)>maximum or any(not isinstance(v,str) for v in value):
        raise ValueError("职业条目必须是有界列表")
    return [text(v,500) for v in value if v.strip()]


def validate_network_payload(payload: dict[str,Any], major_name: str):
    cats,nodes,links = _network_parts(payload)
    clean_cats=[]
    cat_ids=set()
    for cat in cats:
        if not isinstance(cat,dict) or not isinstance(cat.get("id"),str) or not ID.fullmatch(cat["id"]) or cat["id"] in cat_ids:
            raise ValueError("职业类别标识无效或重复")
        cat_ids.add(cat["id"])
        name=text(cat.get("name",""),80)
        if not name:
            raise ValueError("职业类别缺少名称")
        clean={"id":cat["id"],"name":name,"desc":text(cat.get("desc",""),500),"icon":text(cat.get("icon","✨"),16)}
        for key,default in (("c1","#6ee7ff"),("c2","#3b82f6")):
            color=cat.get(key,default)
            if not isinstance(color,str) or not COLOR.fullmatch(color):
                raise ValueError("职业类别颜色格式不正确")
            clean[key]=color
        clean_cats.append(clean)
    clean_nodes=[]
    tags=set()
    direction_ids=set()
    direction_names=set()
    for node in nodes:
        if not isinstance(node,dict) or not isinstance(node.get("tag"),str) or not ID.fullmatch(node["tag"]):
            raise ValueError("职业方向标识格式不正确")
        tag=node["tag"]
        if tag in tags or not isinstance(node.get("cat"),str) or node["cat"] not in cat_ids:
            raise ValueError("职业方向重复或分类不存在")
        tags.add(tag)
        name=text(node.get("name",""),80)
        if not name:
            raise ValueError("职业方向缺少名称")
        name_key = _direction_name_key(name)
        if name_key in direction_names:
            raise ValueError("职业方向名称重复")
        direction_names.add(name_key)
        # Existing stored IDs remain readable. AI candidates receive their IDs
        # from the server-owned name resolver before publication.
        from .career_seed_data import normalize_major_key
        direction_id = node["direction_id"] if "direction_id" in node else "direction-"+payload_hash({"major":normalize_major_key(major_name),"name":name})[:24]
        if not isinstance(direction_id,str) or not ID.fullmatch(direction_id):
            raise ValueError("职业方向稳定标识格式不正确")
        if direction_id in direction_ids:
            raise ValueError("职业方向稳定标识重复")
        direction_ids.add(direction_id)
        try:
            value=float(node.get("rec",3))
        except (TypeError,ValueError):
            raise ValueError("推荐度必须为数字") from None
        if not math.isfinite(value) or not 1<=value<=5:
            raise ValueError("推荐度超出范围")
        stages=node.get("tl")
        if not isinstance(stages,list) or len(stages)!=4:
            raise ValueError("每个职业方向需要4个完整阶段")
        normalized=[]
        for stage in stages:
            if not isinstance(stage,(list,tuple)) or len(stage)!=3:
                raise ValueError("阶段格式不正确")
            normalized.append([text(part,500) for part in stage])
            if any(not part for part in normalized[-1]):
                raise ValueError("职业阶段不可为空")
        interests=node.get("riasec",[])
        if not isinstance(interests,list) or len(interests)>6 or any(x not in "RIASEC" or len(x)!=1 for x in interests if isinstance(x,str)):
            raise ValueError("兴趣维度格式不正确")
        if any(not isinstance(x,str) for x in interests):
            raise ValueError("兴趣维度格式不正确")
        if "lang" in node and not isinstance(node["lang"],bool):
            raise ValueError("职业方向外语标记必须为布尔值")
        pre=text_list(node.get("pre",[])); know=text_list(node.get("know",[]))
        if not pre or not know:
            raise ValueError("职业方向缺少准备要求")
        clean_nodes.append({"tag":tag,"direction_id":direction_id,"cat":node["cat"],"name":name,
            "rec":round(value),"lang":node.get("lang") is True,"riasec":list(dict.fromkeys(interests)),
            "desc":text(node.get("desc","")),"reason":text(node.get("reason","")),
            "pre":pre,"know":know,"tl":normalized,"branch":text(node.get("branch","")),
            "trend":text(node.get("trend",""))})
    clean_links=[]
    seen=set()
    for link in links:
        if (not isinstance(link,(list,tuple)) or len(link)!=4
                or not isinstance(link[0],str) or not isinstance(link[2],str)
                or link[0] not in tags or link[2] not in tags):
            raise ValueError("职业关系引用不存在的方向")
        if any(isinstance(link[i],bool) or not isinstance(link[i],int) or not 0<=link[i]<=3 for i in (1,3)):
            raise ValueError("职业关系阶段超出范围")
        key=tuple(link)
        if key not in seen:
            seen.add(key);clean_links.append(list(link))
    return {"major_name":text(major_name,160),"graduate_label":text(payload.get("graduate_label",major_name+"职业探索"),180),
            "intro":text(payload.get("intro","")),"cats":clean_cats,"nodes":clean_nodes,"links":clean_links,
            "schema_version":SCHEMA_VERSION,"market_data_verified":False}


def validate_personalization_payload(payload,network):
    if not isinstance(payload,dict):
        raise ValueError("职业建议必须是对象")
    valid={n["tag"] for n in network.get("nodes",[])}
    result={key:text(payload.get(key,"")) for key in ("greeting","summary","region_note","timeline_advice")}
    result["holland_code"]=text(payload.get("holland_code",""),6)
    for key in ("rec_overrides","dim_glow","node_tips"):
        raw=payload.get(key,{})
        if not isinstance(raw,dict) or len(raw)>60:
            raise ValueError("职业建议字段格式不正确")
        result[key]={}
        for tag,value in raw.items():
            if tag not in valid:
                raise ValueError("职业建议引用不存在的方向")
            if key=="node_tips":
                result[key][tag]=text(value,1200)
            else:
                try:
                    number=float(value)
                except (ValueError,TypeError):
                    raise ValueError("职业建议分值格式不正确") from None
                low,high=(1,5) if key=="rec_overrides" else (0,1)
                if not math.isfinite(number) or not low<=number<=high:
                    raise ValueError("职业建议分值超出范围")
                result[key][tag]=round(number) if key=="rec_overrides" else number
    highlights=payload.get("highlights",[])
    if not isinstance(highlights,list) or len(highlights)>6 or any(not isinstance(t,str) or t not in valid for t in highlights):
        raise ValueError("推荐方向无效")
    result["highlights"]=list(dict.fromkeys(highlights))
    paths=payload.get("top_paths",[])
    if not isinstance(paths,list) or len(paths)>4:
        raise ValueError("推荐路径格式不正确")
    result["top_paths"]=[]
    for path in paths:
        if not isinstance(path,dict) or not isinstance(path.get("tag"),str) or path["tag"] not in valid:
            raise ValueError("推荐路径引用不存在的方向")
        result["top_paths"].append({"tag":path["tag"],"name":text(path.get("name",""),80),"why":text(path.get("why",""))})
    # Derived baseline cards are authoritative. AI explanation need not regenerate them.
    result["prep_cards"]={};result["job_keywords"]={}
    return result
