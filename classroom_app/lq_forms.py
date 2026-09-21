"""Pure typed form trees. No application, database or request-global state."""
from collections.abc import Mapping
import math
import re

FORM_KINDS = ("field", "input", "textarea", "select", "checkbox", "radio", "range", "switch",
              "form_section", "form_actions", "error_summary")
CONTROLS = FORM_KINDS[1:8]
INPUT_TYPES = ("text", "search", "email", "password", "url", "tel", "number", "date", "time", "datetime-local", "month", "week")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
_ATTR = re.compile(r"(?:aria|data)-[a-z][a-z0-9_.:-]*\Z")


def _text(value):
    if value is None:
        return ""
    if not isinstance(value, (str, int, float, bool)):
        raise ValueError("LQ form text must be scalar")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("LQ form numbers must be finite")
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _id(value):
    value = _text(value)
    if not _ID.fullmatch(value) or "--lq-" in value:
        raise ValueError("LQ form id must be unique and must not use reserved --lq- suffixes")
    return value


def _flag(props, name, default=False):
    value = props.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"LQ {name} must be boolean")
    return value


def _integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or value < minimum:
        raise ValueError(f"Invalid LQ {name}")
    return int(value)


def _attrs(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("LQ attrs must be a mapping")
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not (_ATTR.fullmatch(key) or key in ("id", "name", "form", "title")):
            raise ValueError("Unsupported LQ form attribute")
        if item is not None:
            serialized = _text(item)
            if not key.startswith("data-lq-"):
                result[key] = serialized
    for key in ("id", "name", "form", "aria-hidden", "aria-live", "aria-label", "aria-labelledby",
                "aria-invalid", "aria-required", "aria-disabled", "aria-readonly", "aria-checked",
                "aria-valuemin", "aria-valuemax", "aria-valuenow", "aria-valuetext"):
        result.pop(key, None)
    return result


def _node(tag, attrs=None, children=None):
    return {"tag": tag, "attrs": attrs or {}, "children": children or []}


def _control(kind, p):
    identity = _id(p.get("id"))
    label = _text(p.get("label")).strip()
    if not label:
        raise ValueError("LQ form controls require a visible label")
    size = p.get("size", "md")
    if size not in ("sm", "md", "lg"):
        raise ValueError("Invalid LQ control size")
    required, disabled, readonly = (_flag(p, key) for key in ("required", "disabled", "readonly"))
    if readonly and kind not in ("input", "textarea"):
        raise ValueError("This native control does not support readonly")
    for key, allowed in (("clearable", ("input",)), ("count", ("textarea",)),
                         ("auto_grow", ("textarea",)), ("checked", ("checkbox", "radio", "switch"))):
        if key in p and _flag(p, key) and kind not in allowed:
            raise ValueError(f"{key} is not supported by this native control")
    attrs = _attrs(p.get("attrs"))
    attrs.update({"id": identity, "class": f"lq-{kind} lq-control--{size}"})
    for key in ("name", "form"):
        if p.get(key) is not None:
            attrs[key] = _id(p[key]) if key == "form" else _text(p[key])
    for key, enabled in (("required", required), ("disabled", disabled), ("readonly", readonly)):
        if enabled:
            attrs[key] = ""
    help_text, error = _text(p.get("help")), _text(p.get("error"))
    count = kind == "textarea" and _flag(p, "count")
    descriptions = attrs.get("aria-describedby", "").split()
    # Generated descriptions are authoritative and cannot be spoofed by attrs.
    descriptions = [item for item in descriptions if "--lq-" not in item]
    descriptions += [identity + "--lq-" + suffix for suffix, present in (("help", help_text), ("error", error), ("count", count)) if present]
    attrs.pop("aria-describedby", None)
    if descriptions:
        attrs["aria-describedby"] = " ".join(dict.fromkeys(descriptions))
    if error:
        attrs["aria-invalid"] = "true"
    label_children = [label]
    if required:
        label_children.append(_node("span", {"class": "lq-field__required", "aria-hidden": "true"}, [" *"]))
    label_node = _node("label", {"class": "lq-field__label", "for": identity, "id": identity + "--lq-label"}, label_children)
    value = _text(p.get("value", "on" if kind in ("checkbox", "radio", "switch") else ""))
    children = []
    if kind == "input":
        input_type = p.get("type", "text")
        if input_type not in INPUT_TYPES:
            raise ValueError("Unsupported native input type")
        attrs.update({"type": input_type, "value": value})
    elif kind == "textarea":
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        attrs["rows"] = str(_integer(p.get("rows", 4), "textarea rows", 1))
        if _flag(p, "auto_grow"):
            attrs["data-lq-auto-grow"] = "true"
        if count:
            attrs["data-lq-count"] = identity + "--lq-count"
        children = [value]
    elif kind == "select":
        options = p.get("options", [])
        if not isinstance(options, (list, tuple)):
            raise ValueError("LQ select options must be a list")
        values = set()
        for item in options:
            if not isinstance(item, Mapping) or set(item) - {"value", "label", "disabled"}:
                raise ValueError("Invalid LQ select option")
            option_value = _text(item.get("value"))
            if option_value in values:
                raise ValueError("Duplicate LQ select option value")
            values.add(option_value)
            option_attrs = {"value": option_value}
            if option_value == value:
                option_attrs["selected"] = ""
            if _flag(item, "disabled"):
                option_attrs["disabled"] = ""
            children.append(_node("option", option_attrs, [_text(item.get("label"))]))
        if value not in values and options:
            raise ValueError("Select value must match an option")
    elif kind in ("checkbox", "radio", "switch"):
        attrs.update({"type": "radio" if kind == "radio" else "checkbox", "value": value})
        if kind == "radio" and not attrs.get("name"):
            raise ValueError("Radio controls require a group name")
        if _flag(p, "checked"):
            attrs["checked"] = ""
        if kind == "switch":
            attrs["role"] = "switch"
    else:
        minimum, maximum, step = p.get("min", 0), p.get("max", 100), p.get("step", 1)
        current = p.get("value", minimum)
        for item in (minimum, maximum, step, current):
            if isinstance(item, bool) or not isinstance(item, (float, int)) or not math.isfinite(item):
                raise ValueError("Range values must be finite numbers")
        if minimum >= maximum or step <= 0 or not minimum <= current <= maximum:
            raise ValueError("Invalid LQ range bounds")
        attrs.update({"type": "range", "min": _text(minimum), "max": _text(maximum), "step": _text(step), "value": _text(current), "data-lq-range-output": identity + "--lq-value"})
        value = _text(current)
    if kind in ("input", "textarea"):
        for key in ("placeholder", "autocomplete", "pattern", "min", "max", "step"):
            if p.get(key) is not None:
                attrs[key] = _text(p[key])
        if p.get("inputmode") is not None:
            if p["inputmode"] not in ("none", "text", "decimal", "numeric", "tel", "search", "email", "url"):
                raise ValueError("Invalid inputmode")
            attrs["inputmode"] = p["inputmode"]
        for key in ("minlength", "maxlength"):
            if p.get(key) is not None:
                attrs[key] = str(_integer(p[key], key))
        if "minlength" in attrs and "maxlength" in attrs and int(attrs["minlength"]) > int(attrs["maxlength"]):
            raise ValueError("Invalid text length bounds")
    tag = kind if kind in ("select", "textarea") else "input"
    control = _node(tag, attrs, children)
    if kind in ("checkbox", "radio", "switch"):
        label_node["attrs"]["class"] += " lq-choice"
        label_node["children"] = [control, _node("span", {"class": "lq-choice__text"}, label_children)]
        field_children = [label_node]
    else:
        control_children = []
        for side in ("prefix",):
            if p.get(side):
                control_children.append(_node("span", {"class": f"lq-field__{side}", "aria-hidden": "true"}, [_text(p[side])]))
        control_children.append(control)
        if p.get("suffix"):
            control_children.append(_node("span", {"class": "lq-field__suffix", "aria-hidden": "true"}, [_text(p["suffix"])]))
        if _flag(p, "clearable"):
            if kind != "input" or attrs["type"] not in ("text", "search", "email", "password", "url", "tel", "number"):
                raise ValueError("Clearable is only supported for text-like inputs")
            clear_attrs = {"class": "lq-field__clear", "type": "button", "aria-label": "清除" + label, "data-lq-clear": identity}
            if disabled or readonly:
                clear_attrs["disabled"] = ""
            if not value:
                clear_attrs["hidden"] = ""
            control_children.append(_node("button", clear_attrs, [_node("span", {"aria-hidden": "true"}, ["×"])]))
        if kind == "range":
            control_children.append(_node("output", {"class": "lq-range__value", "id": identity + "--lq-value", "for": identity, "aria-hidden": "true"}, [value]))
        field_children = [label_node, _node("div", {"class": "lq-field__control"}, control_children)]
    if help_text:
        field_children.append(_node("p", {"class": "lq-field__help", "id": identity + "--lq-help"}, [help_text]))
    if error:
        field_children.append(_node("p", {"class": "lq-field__error", "id": identity + "--lq-error"}, [_node("span", {"aria-hidden": "true"}, ["! "]), error]))
    if count:
        units = len(value.encode("utf-16-le")) // 2
        counter = str(units) + (" / " + attrs["maxlength"] if "maxlength" in attrs else "") + " 字"
        field_children.append(_node("p", {"class": "lq-field__count", "id": identity + "--lq-count"}, [counter]))
    return _node("div", {"class": "lq-field" + (" has-error" if error else "") + (" is-disabled" if disabled else ""), "data-lq-field": identity}, field_children)


def lq_form_props(component, **props):
    """Return a safe JSON-compatible render tree for one opt-in form component."""
    if component not in FORM_KINDS:
        raise ValueError("Unknown LQ form component")
    if component == "field":
        kind = props.get("control", "input")
        if kind not in CONTROLS or not isinstance(props.get("control_props", {}), Mapping):
            raise ValueError("Invalid typed Field control")
        return _control(kind, {**props.get("control_props", {}), **{k: v for k, v in props.items() if k not in ("control", "control_props")}})
    if component in CONTROLS:
        return _control(component, props)
    attrs = _attrs(props.get("attrs"))
    if component == "form_actions":
        attrs["class"] = "lq-form-actions"
        children = [_node("div", {"class": "lq-form-actions__content", "data-lq-slot": "content"}, [{"slot": "content"}])]
        if props.get("hint"):
            children.append(_node("p", {"class": "lq-form-actions__hint"}, [_text(props["hint"])]))
        return _node("div", attrs, children)
    identity = _id(props.get("id"))
    title = _text(props.get("title")).strip()
    if not title:
        raise ValueError("LQ form sections and error summaries require a title")
    attrs.update({"id": identity, "class": "lq-" + component.replace("_", "-")})
    if component == "form_section":
        if _flag(props, "surface"):
            attrs["class"] += " lq-surface"
        if _flag(props, "disabled"):
            attrs["disabled"] = ""
        children = [_node("legend", {"class": "lq-form-section__title"}, [title])]
        if props.get("description"):
            attrs["aria-describedby"] = identity + "--lq-description"
            children.append(_node("p", {"class": "lq-form-section__description", "id": attrs["aria-describedby"]}, [_text(props["description"])]))
        children.append(_node("div", {"class": "lq-form-section__content", "data-lq-slot": "content"}, [{"slot": "content"}]))
        return _node("fieldset", attrs, children)
    errors = props.get("errors", [])
    if not isinstance(errors, (list, tuple)) or not errors:
        raise ValueError("ErrorSummary requires a nonempty error list")
    links = []
    for error in errors:
        if not isinstance(error, Mapping) or set(error) - {"id", "message"}:
            raise ValueError("Invalid summary error")
        target = _id(error.get("id"))
        message = _text(error.get("message")).strip()
        if not message:
            raise ValueError("Summary errors require a message")
        links.append(_node("li", {}, [_node("a", {"href": "#" + target, "data-lq-error-target": target}, [message])]))
    attrs.update({"tabindex": "-1", "aria-labelledby": identity + "--lq-title"})
    return _node("section", attrs, [_node("h2", {"class": "lq-error-summary__title", "id": identity + "--lq-title"}, [title]), _node("ul", {}, links)])
