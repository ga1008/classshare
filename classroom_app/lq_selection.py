"""Pure native fallback for opt-in Combobox/Listbox enhancement."""
import re
from .lq_components import _attrs

SELECTION_KINDS = ("combobox", "listbox")


def _text(value):
    if not isinstance(value, str):
        raise ValueError("LQ selection text must be a string")
    return str(value)


def _flag(value):
    if not isinstance(value, bool):
        raise ValueError("Invalid selection flag")
    return value


def lq_selection_props(kind, **props):
    if kind not in SELECTION_KINDS or props.keys() - {"id", "name", "form", "label", "help", "value", "options", "required", "disabled", "multiple", "attrs"}:
        raise ValueError("Invalid LQ selection props")
    identity, label = _text(props.get("id")), _text(props.get("label")).strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", identity) or "--lq-" in identity or not label:
        raise ValueError("Invalid selection identity/label")
    multiple = _flag(props.get("multiple", False))
    if kind == "combobox" and multiple:
        raise ValueError("Editable combobox is single-value")
    choices = props.get("options", [])
    if not isinstance(choices, list):
        raise ValueError("Selection options must be a list")
    options, seen = [], set()
    for item in choices:
        if not isinstance(item, dict) or item.keys() - {"value", "label", "disabled"}:
            raise ValueError("Invalid selection option")
        value, text = _text(item.get("value")), _text(item.get("label"))
        if value in seen:
            raise ValueError("Duplicate selection value")
        seen.add(value)
        options.append({"value": value, "label": text, "disabled": _flag(item.get("disabled", False))})
    value = props.get("value", [] if multiple else options[0]["value"] if options else "")
    values = value if multiple else [value]
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values) or len(set(values)) != len(values) or any(v not in seen for v in values) and not (not options and not multiple and value == ""):
        raise ValueError("Selection value must match options")
    attrs = _attrs(props.get("attrs"))
    for key in list(attrs):
        if key.startswith("data-lq-") or key in ("id", "name", "form", "target", "rel", "aria-label", "aria-labelledby", "aria-describedby", "aria-invalid", "aria-required", "aria-disabled"):
            del attrs[key]
    attrs.update({"id": identity, "class": "lq-select", "data-lq-selection": kind})
    for key in ("name", "form"):
        if key in props:
            attrs[key] = _text(props[key])
    if _flag(props.get("required", False)):
        attrs["required"] = ""
    if _flag(props.get("disabled", False)):
        attrs["disabled"] = ""
    if multiple:
        attrs["multiple"] = ""
    if kind == "listbox":
        attrs["size"] = "6"
    help_text = _text(props.get("help", ""))
    if help_text:
        attrs["aria-describedby"] = identity + "--lq-help"
    for option in options:
        option["attrs"] = {"value": option["value"]}
        if option["disabled"]:
            option["attrs"]["disabled"] = ""
        if option["value"] in values:
            option["attrs"]["selected"] = ""
    return {"id": identity, "label": label, "help": help_text, "attrs": attrs, "options": options}
