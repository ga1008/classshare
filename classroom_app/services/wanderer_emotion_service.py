"""散修 live mood and next-day self-tuning (humanity v2).

Mood is a temporary state, never a permanent personality label. It decays toward
the persona baseline and is written by a turn's optional ``emotion`` object or by
the daily reflection. ``self_tuning`` is the reflection's bounded adjustment for
the next day only; it cannot touch talents, tier, lifespan or the action budget.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from ..time_utils import local_iso, local_now, parse_datetime

MOOD_LABEL_MAX = 20
MOOD_CAUSE_MAX = 160
MOOD_HALF_LIFE_HOURS = 18.0
DEFAULT_VALENCE = 0.0
DEFAULT_AROUSAL = 0.35
TUNING_MAX = 1.0


def _clamp(value: Any, low: float, high: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def mood_from_wanderer(wanderer: dict[str, Any]) -> dict[str, Any]:
    raw = _as_dict(wanderer.get("mood"))
    return {
        "emotion": str(raw.get("emotion") or "").strip()[:MOOD_LABEL_MAX],
        "valence": _clamp(raw.get("valence"), -1.0, 1.0, DEFAULT_VALENCE),
        "arousal": _clamp(raw.get("arousal"), 0.0, 1.0, DEFAULT_AROUSAL),
        "cause": str(raw.get("cause") or "").strip()[:MOOD_CAUSE_MAX],
        "updated_at": str(raw.get("updated_at") or ""),
    }


def effective_mood(wanderer: dict[str, Any], *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Decay the stored mood toward baseline; old moods stop colouring today's voice."""
    mood = mood_from_wanderer(wanderer)
    if not mood["updated_at"]:
        mood.update(emotion="", valence=DEFAULT_VALENCE, arousal=DEFAULT_AROUSAL, age_hours=0.0)
        return mood
    try:
        updated = parse_datetime(mood["updated_at"])
    except (TypeError, ValueError):
        updated = None
    current = now or local_now()
    age_hours = 0.0 if updated is None else max(0.0, (current - updated).total_seconds() / 3600.0)
    factor = 0.5 ** (age_hours / MOOD_HALF_LIFE_HOURS)
    mood["valence"] = round(DEFAULT_VALENCE + (mood["valence"] - DEFAULT_VALENCE) * factor, 3)
    mood["arousal"] = round(DEFAULT_AROUSAL + (mood["arousal"] - DEFAULT_AROUSAL) * factor, 3)
    mood["age_hours"] = round(age_hours, 1)
    if not mood["emotion"] or abs(mood["valence"]) < 0.12:
        mood["emotion"] = "平静" if mood["emotion"] in {"", "平"} else mood["emotion"]
    return mood


def mood_from_turn(turn: dict[str, Any]) -> dict[str, Any]:
    """Normalise a turn's optional emotion object, or fall back to its mood word."""
    emotion = turn.get("emotion")
    if isinstance(emotion, dict):
        label = str(emotion.get("name") or emotion.get("label") or "").strip()[:MOOD_LABEL_MAX]
        return {
            "emotion": label or "有些说不上来",
            "valence": _clamp(emotion.get("valence"), -1.0, 1.0, DEFAULT_VALENCE),
            "arousal": _clamp(emotion.get("arousal"), 0.0, 1.0, DEFAULT_AROUSAL),
            "cause": str(emotion.get("reason") or "").strip()[:MOOD_CAUSE_MAX],
        }
    label = str(turn.get("mood") or "").strip()[:MOOD_LABEL_MAX]
    if not label:
        return {}
    return {"emotion": label, "valence": DEFAULT_VALENCE, "arousal": DEFAULT_AROUSAL, "cause": ""}


def save_mood(conn: Any, wanderer: dict[str, Any], mood: dict[str, Any]) -> dict[str, Any]:
    """Persist a normalised mood and refresh the in-memory snapshot."""
    value = {
        "emotion": str(mood.get("emotion") or "")[:MOOD_LABEL_MAX],
        "valence": _clamp(mood.get("valence"), -1.0, 1.0, DEFAULT_VALENCE),
        "arousal": _clamp(mood.get("arousal"), 0.0, 1.0, DEFAULT_AROUSAL),
        "cause": str(mood.get("cause") or "")[:MOOD_CAUSE_MAX],
        "updated_at": local_iso(),
    }
    conn.execute("UPDATE wanderer_agents SET mood_json = ?, updated_at = ? WHERE id = ?",
                 (json.dumps(value, ensure_ascii=False, sort_keys=True), value["updated_at"], int(wanderer["id"])))
    wanderer["mood"] = value
    from .wanderer_identity_service import invalidate_wanderer_cache

    invalidate_wanderer_cache()
    return value


def apply_turn_mood(conn: Any, wanderer: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    patch = mood_from_turn(turn)
    if not patch:
        return {}
    current = mood_from_wanderer(wanderer)
    merged = {**current, **patch}
    if patch["valence"] == DEFAULT_VALENCE and current["emotion"] == patch["emotion"]:
        return {}
    return save_mood(conn, wanderer, merged)


def baseline_note(wanderer: dict[str, Any]) -> str:
    temperament = "、".join(str(item) for item in (wanderer.get("temperament") or [])[:3])
    return f"你的底色是{temperament or '安静而认真'}；情绪会起伏，但不会把你变成另一个人。"


def mood_block(wanderer: dict[str, Any], *, now: Optional[datetime] = None) -> str:
    mood = effective_mood(wanderer, now=now)
    if not mood.get("emotion"):
        return f"【此刻的你】\n{baseline_note(wanderer)}"
    valence_word = "偏亮" if mood["valence"] >= 0.2 else "偏低" if mood["valence"] <= -0.2 else "平"
    lines = [f"【此刻的你】心情：{mood['emotion']}（愉悦{valence_word}，精力 {mood['arousal']:.1f}）"]
    if mood.get("cause"):
        lines.append(f"起因：{mood['cause']}")
    if mood.get("age_hours"):
        lines.append(f"这是约 {mood['age_hours']:.0f} 小时前的情绪，会随时间回落")
    lines.append(baseline_note(wanderer))
    return "\n".join(lines)


def tuning_for_today(wanderer: dict[str, Any], *, today: Optional[str] = None) -> dict[str, Any]:
    raw = _as_dict(wanderer.get("self_tuning"))
    day = _as_dict(raw.get("next_day"))
    if not day or str(day.get("effective_date") or "") not in {"", str(today or local_now().date().isoformat())}:
        return {"initiative": 0.0, "warmth": 0.0, "directness": 0.0, "focus": "", "intentions": [],
                "note": "", "proactive_multiplier": 1.0, "effective_date": ""}
    return {
        "initiative": _clamp(day.get("initiative"), -TUNING_MAX, TUNING_MAX, 0.0),
        "warmth": _clamp(day.get("warmth"), -TUNING_MAX, TUNING_MAX, 0.0),
        "directness": _clamp(day.get("directness"), -TUNING_MAX, TUNING_MAX, 0.0),
        "focus": str(day.get("focus") or "")[:30],
        "intentions": [str(item)[:120] for item in (day.get("intentions") or []) if str(item).strip()][:3],
        "note": str(day.get("note") or "")[:200],
        "proactive_multiplier": _clamp(day.get("proactive_multiplier"), 0.7, 1.3, 1.0),
        "effective_date": str(day.get("effective_date") or ""),
    }


def tuning_block(wanderer: dict[str, Any]) -> str:
    tuning = tuning_for_today(wanderer)
    if not any((tuning["initiative"], tuning["warmth"], tuning["directness"], tuning["focus"],
                tuning["intentions"], tuning["note"])):
        return ""
    lines = ["【今天的自我调整】"]
    if tuning["initiative"]:
        lines.append("更想主动找人说话。" if tuning["initiative"] > 0 else "今天想安静一点，少主动插话。")
    if tuning["warmth"]:
        lines.append("语气更温和一些。" if tuning["warmth"] > 0 else "今天说话更直接、少绕弯。")
    if tuning["directness"]:
        lines.append("更愿意把话说明白。" if tuning["directness"] > 0 else "保留一点含糊和余地。")
    if tuning["focus"]:
        lines.append(f"今天更想把注意力放在：{tuning['focus']}。")
    if tuning["intentions"]:
        lines.append("今天想做的事：" + "；".join(tuning["intentions"]))
    if tuning["note"]:
        lines.append(f"给自己的话：{tuning['note']}")
    lines.append("这是你昨晚给自己的提醒，不是必须执行的任务；可以按今天的情况调整。")
    return "\n".join(lines)


def proactive_multiplier(wanderer: dict[str, Any]) -> float:
    """0.7–1.3 soft factor for主动 action probability, never a budget/tier change."""
    return tuning_for_today(wanderer)["proactive_multiplier"]


def neutral_mood() -> dict[str, Any]:
    return {"emotion": "", "valence": DEFAULT_VALENCE, "arousal": DEFAULT_AROUSAL, "cause": "", "updated_at": ""}
