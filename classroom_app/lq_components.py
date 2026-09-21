"""Pure presentation props for LQ Jinja macros. No application or database imports.

Register ``lq_props`` as a Jinja global. All returned text is an ordinary str,
including Markup inputs; the macros force-escape text and attribute values.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
import re
from urllib.parse import urlsplit


TONES = ("primary", "success", "warning", "danger", "info", "neutral")
BUTTON_VARIANTS = ("prominent", "glass", "soft", "ghost", "destructive", "link")
SIZES = ("sm", "md", "lg")
_ATTRIBUTE = re.compile(r"(?:aria|data)-[a-z][a-z0-9_.:-]*\Z")
_EXTRA_ATTRIBUTES = {"id", "name", "title", "form", "target", "rel"}
_ICON = re.compile(r"[a-z][a-z0-9-]*\Z")
_CONTROL = re.compile(r"[\x00-\x20\x7f]")


def _text(value) -> str:
    # Calling escape(value) directly would trust Markup.__html__().
    if isinstance(value, bool):
        return str(value).lower()
    return "" if value is None else str(value)


def _choice(value, choices, name):
    value = _text(value)
    if value not in choices:
        raise ValueError(f"Invalid LQ {name}")
    return value


def _flag(value, name):
    if not isinstance(value, bool):
        raise ValueError(f"LQ {name} must be a boolean")
    return value


def _attrs(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("LQ attrs must be a mapping")
    result = {}
    for key, item in value.items():
        key = _text(key)
        if key not in _EXTRA_ATTRIBUTES and not _ATTRIBUTE.fullmatch(key):
            raise ValueError("Unsupported LQ attribute")
        if item is None:
            continue
        if not isinstance(item, (str, int, float, bool)):
            raise ValueError("LQ attribute values must be scalar")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("LQ numeric attribute values must be finite")
        result[key] = str(item).lower() if isinstance(item, bool) else _text(item)
    if result.get("target") == "_blank":
        rel = set(result.get("rel", "").lower().split()) - {"opener"}
        result["rel"] = " ".join(sorted(rel | {"noopener", "noreferrer"}))
    # These six primitives are not live regions and must retain their semantics.
    result.pop("aria-hidden", None)
    result.pop("aria-live", None)
    return result


def _url(value, *, image=False):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("LQ URL must be a string")
    value = _text(value)
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("Invalid LQ URL")
    value = value.strip()
    if not value or _CONTROL.search(value) or "\\" in value or value.startswith("//"):
        raise ValueError("Invalid LQ URL")
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError as error:
        raise ValueError("Invalid LQ URL") from error
    allowed = {"", "http", "https"} if image else {"", "http", "https", "mailto", "tel"}
    if scheme not in allowed:
        raise ValueError("Unsupported LQ URL scheme")
    return value


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"LQ {name} must be a finite number")
    return int(value) if int(value) == value else value


def _name(label, attrs):
    name = _text(label).strip() or attrs.get("aria-label", "").strip()
    if not name:
        raise ValueError("LQ component requires an accessible name")
    return name


def _badge_value(value):
    if isinstance(value, bool):
        raise ValueError("LQ badge value cannot be a boolean")
    if value is None or value == "" or value == 0 or value == "0":
        return None
    if isinstance(value, (int, float)) and _number(value, "badge value") < 0:
        raise ValueError("LQ badge value cannot be negative")
    return _text(value)


def _button(props):
    label = _text(props.get("label", ""))
    attrs = _attrs(props.get("attrs"))
    name = _name(label, attrs)
    variant = _choice(props.get("variant", "soft"), BUTTON_VARIANTS, "button variant")
    size = _choice(props.get("size", "md"), SIZES, "size")
    icon = props.get("icon")
    if icon is not None:
        if not isinstance(icon, str) or not _ICON.fullmatch(_text(icon)):
            raise ValueError("LQ icon must be a registered icon name")
        icon = _text(icon)
    if not label.strip() and not icon:
        raise ValueError("An icon-only LQ button requires an icon")
    href = _url(props.get("href"))
    button_type = _choice(props.get("type", "button"), ("button", "submit", "reset"), "button type")
    disabled = _flag(props.get("disabled", False), "disabled")
    aria_disabled = _flag(props.get("aria_disabled", False), "aria_disabled")
    loading = _flag(props.get("loading", False), "loading")
    blocked = disabled or aria_disabled or loading
    classes = f"lq-btn lq-btn--{variant} lq-btn--{size}"
    if not label.strip():
        classes += " lq-btn--icon"
    if loading:
        classes += " is-loading"
    if disabled or aria_disabled:
        classes += " is-disabled"
    # Core state and naming win over passthrough attrs, including malicious ones.
    attrs.pop("aria-busy", None)
    attrs.pop("aria-disabled", None)
    attrs.pop("data-lq-disabled", None)
    attrs.pop("aria-labelledby", None)
    attrs["aria-label"] = name
    if props.get("id") is not None:
        attrs["id"] = _text(props["id"])
    if href is None:
        attrs.pop("target", None)
        attrs.pop("rel", None)
        attrs["type"] = button_type
        if disabled:
            attrs["disabled"] = ""
    else:
        attrs["href"] = href
    if blocked:
        attrs["aria-disabled"] = "true"
        attrs["data-lq-disabled"] = "true"
    if loading:
        attrs["aria-busy"] = "true"
    return {"tag": "a" if href is not None else "button", "classes": classes, "attrs": attrs,
            "label": label, "icon": icon, "badge": _badge_value(props.get("badge")),
            "loading": loading, "icon_only": not bool(label.strip())}


def _chip(props):
    label = _text(props.get("label", ""))
    attrs = _attrs(props.get("attrs"))
    name = _name(label, attrs)
    kind = _choice(props.get("kind", "status"), ("filter", "status", "tag"), "chip kind")
    size = _choice(props.get("size", "md"), ("sm", "md"), "chip size")
    tone = _choice(props.get("tone", "neutral"), TONES, "tone")
    pressed = _flag(props.get("pressed", False), "pressed")
    disabled = _flag(props.get("disabled", False), "disabled")
    removable = _flag(props.get("removable", False), "removable")
    if removable and kind != "tag":
        raise ValueError("Only a tag chip can be removable")
    for key in ("aria-pressed", "aria-live", "aria-disabled", "data-lq-disabled", "aria-labelledby"):
        attrs.pop(key, None)
    attrs["data-tone"] = tone
    if props.get("id") is not None:
        attrs["id"] = _text(props["id"])
    classes = f"lq-chip lq-chip--{kind} lq-chip--{size}"
    if kind == "filter":
        attrs.update({"type": "button", "aria-pressed": str(pressed).lower(), "aria-label": name})
        if pressed:
            classes += " is-selected"
        if disabled:
            attrs.update({"disabled": "", "aria-disabled": "true", "data-lq-disabled": "true"})
    else:
        attrs.pop("aria-label", None)
    if disabled:
        classes += " is-disabled"
    remove_label = _text(props.get("remove_label")).strip() or f"移除{name}"
    return {"tag": "button" if kind == "filter" else "span", "classes": classes, "attrs": attrs,
            "kind": kind, "label": label, "removable": removable, "remove_label": remove_label,
            "disabled": disabled}


def _badge(props):
    attrs = _attrs(props.get("attrs"))
    dot = _flag(props.get("dot", False), "dot")
    value = _badge_value(props.get("value"))
    visible = value is not None or (dot and props.get("value") is None)
    attrs.pop("aria-labelledby", None)
    attrs["data-tone"] = _choice(props.get("tone", "neutral"), TONES, "tone")
    if dot and visible:
        attrs.update({"role": "img", "aria-label": _name(props.get("label"), attrs)})
    elif props.get("label") is not None:
        attrs["aria-label"] = _text(props["label"])
    return {"classes": "lq-badge" + (" lq-badge--dot" if dot else ""), "attrs": attrs,
            "value": value, "dot": dot, "visible": visible}


def _avatar(props):
    attrs = _attrs(props.get("attrs"))
    name = _name(props.get("name"), {})
    size = props.get("size", 40)
    if type(size) is not int or size not in (24, 32, 40, 56):
        raise ValueError("Invalid LQ avatar size")
    hashed = 0
    for char in name:
        hashed = (hashed * 31 + ord(char)) & 0xFFFFFFFF
    bucket = hashed % len(TONES)
    attrs.pop("aria-labelledby", None)
    attrs.update({"role": "img", "aria-label": name, "data-tone": TONES[bucket], "data-avatar-bucket": str(bucket)})
    return {"classes": f"lq-avatar lq-avatar--{size}", "attrs": attrs,
            "name": name, "initial": name[0].upper(), "src": _url(props.get("src"), image=True)}


def _spinner(props):
    attrs = _attrs(props.get("attrs"))
    attrs.pop("aria-label", None)
    attrs.pop("aria-labelledby", None)
    attrs.pop("aria-live", None)
    attrs["aria-hidden"] = "true"
    size = _choice(props.get("size", "md"), SIZES, "spinner size")
    return {"classes": f"lq-spinner lq-spinner--{size}", "attrs": attrs}


def _progress(props):
    attrs = _attrs(props.get("attrs"))
    name = _name(props.get("label"), {})
    variant = _choice(props.get("variant", "bar"), ("bar", "ring"), "progress variant")
    maximum = _number(props.get("max", 100), "progress max")
    if maximum <= 0:
        raise ValueError("LQ progress max must be positive")
    value = props.get("value")
    if value is not None:
        value = _number(value, "progress value")
        if not 0 <= value <= maximum:
            raise ValueError("LQ progress value is out of range")
    for key in ("aria-valuemin", "aria-valuemax", "aria-valuenow", "aria-valuetext", "aria-labelledby"):
        attrs.pop(key, None)
    attrs.update({"aria-label": name, "data-tone": "primary"})
    if variant == "ring":
        attrs.update({"role": "progressbar", "aria-valuemin": "0", "aria-valuemax": str(maximum)})
        if value is not None:
            attrs["aria-valuenow"] = str(value)
    else:
        attrs["max"] = str(maximum)
        if value is not None:
            attrs["value"] = str(value)
    return {"tag": "span" if variant == "ring" else "progress",
            "classes": "lq-progress" + (" lq-progress--ring" if variant == "ring" else ""),
            "attrs": attrs, "variant": variant,
            "percent": None if value is None else math.floor(value / maximum * 100 + .5)}


def _skeleton(props):
    attrs = _attrs(props.get("attrs"))
    for key in ("aria-label", "aria-labelledby", "aria-describedby", "aria-busy"):
        attrs.pop(key, None)
    attrs["aria-hidden"] = "true"
    shape = _choice(props.get("shape", "text"), ("text", "avatar", "block"), "skeleton shape")
    lines = props.get("lines", 1)
    if type(lines) is not int or not 1 <= lines <= 8 or (shape != "text" and lines != 1):
        raise ValueError("Invalid LQ skeleton lines")
    return {"classes": f"lq-skeleton lq-skeleton--{shape}", "attrs": attrs, "lines": lines}


_NORMALIZERS = {"button": _button, "chip": _chip, "badge": _badge, "avatar": _avatar,
                "spinner": _spinner, "progress": _progress, "skeleton": _skeleton}


def lq_props(component, **props):
    """Normalize one component; invalid input fails before any HTML is emitted."""
    try:
        normalize = _NORMALIZERS[component]
    except (KeyError, TypeError) as error:
        raise ValueError("Unknown LQ component") from error
    return normalize(props)
