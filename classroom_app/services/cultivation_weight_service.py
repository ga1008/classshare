from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any


CULTIVATION_WEIGHT_KEYS: tuple[str, ...] = ("material", "task", "interaction", "consistency")
CULTIVATION_WEIGHT_VERSION_DEFAULT = "default-v1"
CULTIVATION_WEIGHT_COOLDOWN_DAYS = 7

DEFAULT_CULTIVATION_WEIGHTS: dict[str, int] = {
    "material": 45,
    "task": 35,
    "interaction": 15,
    "consistency": 5,
}

CULTIVATION_WEIGHT_LABELS: dict[str, str] = {
    "material": "学习材料",
    "task": "作业考试",
    "interaction": "互动求助",
    "consistency": "稳定投入",
}

CULTIVATION_WEIGHT_PRESETS: dict[str, dict[str, Any]] = {
    "lecture": {
        "label": "讲授型",
        "weights": {"material": 50, "task": 35, "interaction": 10, "consistency": 5},
    },
    "lab": {
        "label": "实验型",
        "weights": {"material": 30, "task": 55, "interaction": 10, "consistency": 5},
    },
    "seminar": {
        "label": "研讨型",
        "weights": {"material": 30, "task": 25, "interaction": 40, "consistency": 5},
    },
}


class CultivationWeightValidationError(ValueError):
    """Raised when a teacher-submitted cultivation weight payload is invalid."""


def _json_loads(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_weight_int(value: Any, key: str) -> int:
    if isinstance(value, bool):
        raise CultivationWeightValidationError(f"{CULTIVATION_WEIGHT_LABELS[key]}权重必须是数字")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CultivationWeightValidationError(f"{CULTIVATION_WEIGHT_LABELS[key]}权重必须是数字") from None
    if not number.is_integer():
        raise CultivationWeightValidationError(f"{CULTIVATION_WEIGHT_LABELS[key]}权重必须是整数")
    integer = int(number)
    if integer < 0 or integer > 100:
        raise CultivationWeightValidationError(f"{CULTIVATION_WEIGHT_LABELS[key]}权重必须在 0 到 100 之间")
    return integer


def normalize_cultivation_weights(payload: Any) -> dict[str, int]:
    data = _json_loads(payload, {}) if not isinstance(payload, dict) else payload
    if not isinstance(data, dict):
        raise CultivationWeightValidationError("修为权重格式不正确")
    weights = data.get("weights") if isinstance(data.get("weights"), dict) else data
    normalized = {
        key: _as_weight_int(weights.get(key), key)
        for key in CULTIVATION_WEIGHT_KEYS
    }
    total = sum(normalized.values())
    if total != 100:
        raise CultivationWeightValidationError("四项修为权重合计必须等于 100")
    return normalized


def serialize_cultivation_weights(weights: dict[str, int]) -> str:
    return json.dumps({key: int(weights[key]) for key in CULTIVATION_WEIGHT_KEYS}, ensure_ascii=False, sort_keys=True)


def weight_rules_from_weights(weights: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"key": key, "label": CULTIVATION_WEIGHT_LABELS[key], "weight": int(weights[key])}
        for key in CULTIVATION_WEIGHT_KEYS
    ]


def public_cultivation_weight_presets() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": str(item["label"]),
            "weights": dict(item["weights"]),
        }
        for key, item in CULTIVATION_WEIGHT_PRESETS.items()
    ]


def _default_config() -> dict[str, Any]:
    config = {
        "weights": dict(DEFAULT_CULTIVATION_WEIGHTS),
        "version": CULTIVATION_WEIGHT_VERSION_DEFAULT,
        "source": "default",
        "updated_at": None,
        "updated_by_teacher_id": None,
    }
    config["rules"] = weight_rules_from_weights(DEFAULT_CULTIVATION_WEIGHTS)
    return config


def load_cultivation_weight_config(conn, class_offering_id: int) -> dict[str, Any]:
    try:
        row = conn.execute(
            """
            SELECT cultivation_weights_json,
                   cultivation_weights_version,
                   cultivation_weights_updated_at,
                   cultivation_weights_updated_by_teacher_id
            FROM class_offerings
            WHERE id = ?
            LIMIT 1
            """,
            (int(class_offering_id),),
        ).fetchone()
    except Exception as exc:
        message = str(exc).lower()
        if "cultivation_weights" not in message and "no such column" not in message and "undefinedcolumn" not in message:
            raise
        return _default_config()
    if not row:
        return _default_config()
    item = dict(row)
    source = "custom"
    try:
        weights = normalize_cultivation_weights(item.get("cultivation_weights_json"))
    except CultivationWeightValidationError:
        weights = dict(DEFAULT_CULTIVATION_WEIGHTS)
        source = "default"
    version = str(item.get("cultivation_weights_version") or "").strip()
    if source == "default":
        version = CULTIVATION_WEIGHT_VERSION_DEFAULT
    elif not version:
        version = "custom-legacy"
    config = {
        "weights": weights,
        "version": version,
        "source": source,
        "updated_at": item.get("cultivation_weights_updated_at"),
        "updated_by_teacher_id": item.get("cultivation_weights_updated_by_teacher_id"),
    }
    config["rules"] = weight_rules_from_weights(weights)
    return config


def build_weight_settings_payload(config: dict[str, Any], *, now: str | None = None) -> dict[str, Any]:
    current_time = _parse_iso(now) or datetime.now()
    updated_at = _parse_iso(config.get("updated_at"))
    next_available_at = None
    cooldown_remaining_days = 0
    can_update = True
    if updated_at:
        next_update = updated_at + timedelta(days=CULTIVATION_WEIGHT_COOLDOWN_DAYS)
        if current_time < next_update:
            can_update = False
            next_available_at = next_update.isoformat(timespec="seconds")
            cooldown_remaining_days = max(1, (next_update.date() - current_time.date()).days)
    return {
        "weights": dict(config.get("weights") or DEFAULT_CULTIVATION_WEIGHTS),
        "rules": list(config.get("rules") or weight_rules_from_weights(DEFAULT_CULTIVATION_WEIGHTS)),
        "version": str(config.get("version") or CULTIVATION_WEIGHT_VERSION_DEFAULT),
        "source": str(config.get("source") or "default"),
        "updated_at": config.get("updated_at"),
        "can_update": can_update,
        "cooldown_days": CULTIVATION_WEIGHT_COOLDOWN_DAYS,
        "cooldown_remaining_days": cooldown_remaining_days,
        "next_available_at": next_available_at,
        "presets": public_cultivation_weight_presets(),
    }


def generate_cultivation_weight_version(timestamp: str | None = None) -> str:
    parsed = _parse_iso(timestamp)
    if parsed:
        stamp = parsed.strftime("%Y%m%d%H%M%S")
    else:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"weights-{stamp}"


def save_cultivation_weight_config(
    conn,
    class_offering_id: int,
    *,
    teacher_id: int,
    weights: dict[str, int],
    previous_config: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    timestamp = timestamp or _now_iso()
    version = generate_cultivation_weight_version(timestamp)
    conn.execute(
        """
        UPDATE class_offerings
        SET cultivation_weights_json = ?,
            cultivation_weights_version = ?,
            cultivation_weights_updated_at = ?,
            cultivation_weights_updated_by_teacher_id = ?
        WHERE id = ?
        """,
        (
            serialize_cultivation_weights(weights),
            version,
            timestamp,
            int(teacher_id),
            int(class_offering_id),
        ),
    )
    config = {
        "weights": dict(weights),
        "version": version,
        "source": "custom",
        "updated_at": timestamp,
        "updated_by_teacher_id": int(teacher_id),
        "previous_version": (previous_config or {}).get("version"),
        "previous_weights": dict((previous_config or {}).get("weights") or DEFAULT_CULTIVATION_WEIGHTS),
    }
    config["rules"] = weight_rules_from_weights(weights)
    return config
