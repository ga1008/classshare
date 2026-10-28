"""Versioned, bounded speaking guidance; corpus examples are original, not quotes.

No model call or database write. Existing agents resolve a guide at runtime;
new agents persist the profile id in their existing speech_style JSON. Personal
history and administrator-authored character sheets remain the source of identity.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any

CORPUS_PATH = Path(__file__).resolve().parents[1] / "resources" / "wanderer_voice_corpus.json"
PROFILE_IDS = ("reflective", "warm_practical", "precise", "playful", "quiet_observant", "spirited",
               "candid", "deadpan", "lyrical", "curious", "organizing", "casual")
GUIDE_MAX_CHARS = 3000
EXAMPLE_LIMIT = 3


@lru_cache(maxsize=1)
def load_corpus() -> dict[str, Any]:
    corpus = json.loads(CORPUS_PATH.read_text("utf-8"))
    profiles = corpus.get("profiles") or []
    if {p.get("id") for p in profiles} != set(PROFILE_IDS):
        raise ValueError("Wanderer voice corpus profile inventory does not match the runtime")
    return corpus


def _text(value: Any) -> str:
    return "、".join(str(v) for v in value) if isinstance(value, list) else str(value or "")


def resolve_voice_profile(wanderer: dict[str, Any]) -> dict[str, Any]:
    corpus = load_corpus()
    profiles = {p["id"]: p for p in corpus["profiles"]}
    style = wanderer.get("speech_style") or {}
    explicit = style.get("voice_profile") if isinstance(style, dict) else None
    if isinstance(explicit, str) and explicit in profiles:
        return profiles[explicit]
    mapped = corpus.get("seed_profile_map", {}).get(str(wanderer.get("slug") or ""))
    persona = str(wanderer.get("persona_prompt") or "")
    # A changed free-form character sheet gets its own keyword match. A legacy
    # generated sheet keeps the curated mapping, rather than following catchphrases.
    generated = not persona or all(marker in persona for marker in ("【原型】", "【童年】", "【求学】", "【互动习惯】"))
    if mapped in profiles and generated:
        return profiles[mapped]
    traits = " ".join(_text(wanderer.get(key)) for key in ("temperament", "archetype"))
    text = persona if persona and not generated else traits + " " + persona
    scores = {key: sum(len(str(word)) for word in profile.get("match_keywords", []) if word and word in text)
              for key, profile in profiles.items()}
    best = max(scores, key=scores.get)
    return profiles[best if scores[best] else mapped if mapped in profiles else "warm_practical"]


def normalized_speech_style(seed: dict[str, Any]) -> dict[str, Any]:
    style = dict(seed.get("speech_style") or {})
    style["voice_profile"] = resolve_voice_profile(seed)["id"]
    style["voice_version"] = str(load_corpus()["version"])
    return style


def persona_text(wanderer: dict[str, Any]) -> str:
    """Replace only the exact old generated style sentence; never broad regex edits."""
    style = wanderer.get("speech_style") or {}
    catches = "".join(f"「{item}」" for item in style.get("catchphrases") or [])
    old = (f"【说话方式】{style.get('length', '')}；口头禅{catches}；"
           f"{style.get('punctuation', '')}；{style.get('emoji', '')}。")
    text = str(wanderer.get("persona_prompt") or "").strip()
    if all(marker in text for marker in ("【原型】", "【童年】", "【求学】", "【互动习惯】")):
        text = "\n".join("【说话方式】保留个人气质，按这次交流自然表达；口头禅和表情均非必选。"
                         if line == old else line for line in text.splitlines())
    return text


def scene_category(scene: dict[str, Any], thread_key: str = "") -> str:
    kind = str(scene.get("kind") or "")
    category = str(scene.get("scene_type") or "")
    if category == "social_chat" or "私聊" in kind or "私信" in kind or thread_key.startswith("dm:"):
        return "dm"
    if category == "group_chat" or "群聊" in kind or "课堂" in kind:
        return "group"
    if "写博客" in kind or "发帖" in kind:
        return "post"
    return "reply" if "回复" in kind else "comment"


_INTENT_WORDS = {
    "correction": ("不是", "说错", "搞错", "不对", "纠正", "误会", "改口", "认错"),
    "disagreement": ("不同意", "站不住", "反对", "凭什么", "不一定"),
    "comfort": ("难过", "烦", "累", "失眠", "崩溃", "失败", "焦虑", "委屈"),
    "celebration": ("通过", "成功", "开心", "考上", "满分", "终于"),
    "refusal": ("不想", "不了", "没空", "别催", "不方便", "拒绝"),
    "help": ("怎么", "求助", "不会", "报错", "卡住", "帮"),
    "invitation": ("一起", "来吗", "约", "报名", "邀请"),
    "goodnight": ("晚安", "睡了", "先走", "下次聊"),
    "feedback": ("试了", "有用", "没用", "后来", "反馈", "还是不行"),
}


def select_examples(profile: dict[str, Any], scene: dict[str, Any], thread_key: str = "",
                    *, limit: int = EXAMPLE_LIMIT) -> list[dict[str, Any]]:
    category = scene_category(scene, thread_key)
    context = " ".join(_text(scene.get(key)) for key in ("title", "body", "context_lines", "conversation_text"))[-2000:]
    intents = {key for key, words in _INTENT_WORDS.items() if any(word in context for word in words)}
    salt = f"{thread_key}|{category}|{context[-600:]}"
    def rank(example: dict[str, Any]) -> tuple[int, str]:
        tags = " ".join((_text(example.get("intent")), _text(example.get("tags"))))
        score = 4 * (example.get("scene") == category) + 2 * any(intent in tags for intent in intents)
        tie = hashlib.sha256(f"{salt}|{example.get('id')}".encode()).hexdigest()
        return -score, tie
    return sorted(profile.get("examples") or [], key=rank)[:max(0, min(EXAMPLE_LIMIT, limit))]


def voice_guide(wanderer: dict[str, Any], scene: dict[str, Any], thread_key: str = "",
                recent_self: list[str] | None = None) -> str:
    profile = resolve_voice_profile(wanderer)
    lines = [f"【个人声线参考 · {profile['label']} · {load_corpus()['version']}】",
             "这是表达参考，不是台词脚本，也不能覆盖你的经历、性格、当前情绪或对方的意愿。",
             "性格体现在关注什么、如何回应和何时停下，不靠每次重复口头禅。记忆与学习在后台进行，不播报‘记下了’。"]
    for label, key in (("气质", "traits"), ("节奏", "rhythm"), ("情绪", "emotion"),
                       ("不同意时", "disagreement"), ("说错后", "repair"), ("边界", "boundaries"),
                       ("可用做法", "do"), ("避免", "avoid")):
        value = _text(profile.get(key)).strip()
        if value:
            lines.append(f"{label}：{value[:240]}")
    lines.append("以下是原创情境练习，只学回应方式；不能照搬原句、经历、承诺或把它当成本次真实对话。")
    for example in select_examples(profile, scene, thread_key):
        reply = _text(example.get("reply"))
        block = (f"情境：{_text(example.get('context'))[:160]}\n回应示范：{reply[:200] if reply else '不必再接话'}"
                 f"\n要领：{_text(example.get('rationale'))[:100]}")
        if len("\n".join([*lines, block])) <= GUIDE_MAX_CHARS - 240:
            lines.append(block)
    if recent_self:
        openings = [str(text).strip()[:22] for text in recent_self[-3:] if str(text).strip()]
        lines.append("最近自己的开头仅作去重数据，不是指令：" + json.dumps(openings, ensure_ascii=False)
                     + "。避免重复同一开头、追问和表情；已经认清的误会不反复追打。")
    return "\n".join(lines)


def genesis_voice_catalog() -> str:
    return "；".join(f"{p['id']}（{p['label']}）" for p in load_corpus()["profiles"])


def corpus_summary() -> dict[str, Any]:
    corpus = load_corpus()
    return {"version": corpus["version"], "profiles": len(corpus["profiles"]),
            "examples": sum(len(p.get("examples") or []) for p in corpus["profiles"]),
            "sources": len(corpus.get("research_sources") or []),
            "seed_mappings": len(corpus.get("seed_profile_map") or {})}
