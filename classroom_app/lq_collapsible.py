"""Pure native Collapsible props; persistence identity is always caller-owned."""
from collections.abc import Mapping
import json
import re

COLLAPSIBLE_KINDS = ("collapsible",)
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
_ATTR = re.compile(r"(?:aria|data)-[a-z][a-z0-9_.:-]*\Z")


def lq_collapsible_props(component, **p):
    if component != "collapsible":
        raise ValueError("Unknown Collapsible component")
    identity, title = p.get("id"), p.get("title")
    if not isinstance(identity, str) or not _ID.fullmatch(identity) or "--lq-" in identity:
        raise ValueError("Collapsible requires a unique non-reserved id")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Collapsible requires a title")
    description = p.get("description", "")
    if not isinstance(description, str):
        raise ValueError("Collapsible description must be text")
    mode = p.get("mode", "responsive")
    if mode not in ("responsive", "always"):
        raise ValueError("Invalid Collapsible mode")
    states = {}
    for key in ("open", "keep_open", "has_error", "current", "dirty"):
        value = p.get(key, key == "open")
        if not isinstance(value, bool):
            raise ValueError("Collapsible states must be boolean")
        states[key] = value
    raw_attrs = {} if p.get("attrs") is None else p["attrs"]
    if not isinstance(raw_attrs, Mapping):
        raise ValueError("Collapsible attrs must be a mapping")
    attrs = {}
    for key, value in raw_attrs.items():
        if not isinstance(key, str) or not (_ATTR.fullmatch(key) or key == "title"):
            raise ValueError("Unsupported Collapsible attribute")
        if value is None:
            continue
        if not isinstance(value, (str, bool)):
            raise ValueError("Collapsible attribute values must be strings or booleans")
        if key.startswith("data-lq-") or key in ("aria-hidden", "aria-label", "aria-labelledby", "aria-expanded", "aria-controls", "aria-disabled", "aria-live"):
            continue
        attrs[key] = str(value).lower() if isinstance(value, bool) else str(value)
    attrs.update({"id": str(identity), "class": "lq-collapsible lq-surface", "data-lq-collapsible": "", "data-lq-mode": mode})
    for key, value in states.items():
        attrs["data-lq-" + ("default-open" if key == "open" else key.replace("_", "-"))] = str(value).lower()
    if any(states.values()):
        attrs["open"] = ""
    persist = p.get("persist")
    if persist is not None:
        if not isinstance(persist, Mapping) or set(persist) != {"identity", "resource", "key"}:
            raise ValueError("Persistence requires identity, resource and key")
        parts = []
        for key in ("identity", "resource", "key"):
            value = persist[key]
            if not isinstance(value, str) or not value.strip() or len(value) > 256 or re.search(r"[\x00-\x1f\x7f]", value):
                raise ValueError("Invalid scoped persistence key")
            parts.append(str(value))
        attrs["data-lq-persist"] = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return {"attrs": attrs, "title": str(title), "description": str(description),
            "summary_id": identity + "--lq-summary", "content_id": identity + "--lq-content"}
