"""Opt-in composer presentation. Submission and draft lifetime belong to callers."""
from .lq_components import _attrs, lq_props

COMPOSER_KINDS = ("composer",)


def _text(value, required=False):
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError("LQ composer requires plain text")
    return str(value)


def _flag(value):
    if not isinstance(value, bool):
        raise ValueError("LQ composer requires booleans")
    return value


def _node(tag, attrs=None, children=None):
    return {"tag": tag, "attrs": attrs or {}, "children": children or []}


def _button(label, icon, variant, kind, disabled, form, hook):
    props = {"label": "", "icon": icon, "variant": variant, "type": kind, "disabled": disabled,
             "attrs": {"aria-label": label, hook: "", **({"form": form} if form else {})}}
    lq_props("button", **props)
    return {"button": props}


def lq_composer_props(kind, **p):
    keys = {"id", "attrs", "label", "name", "form", "value", "placeholder", "required", "maxlength", "disabled", "busy", "hasContent", "enter", "attachment", "emoji", "sendLabel", "submit_name", "submit_value"}
    if kind != "composer" or p.keys() - keys:
        raise ValueError("Invalid LQ composer props")
    disabled, busy, has_content = (_flag(p.get(key, False)) for key in ("disabled", "busy", "hasContent"))
    enter = _text(p.get("enter", "newline"))
    if enter not in ("newline", "send"):
        raise ValueError("Invalid LQ composer Enter policy")
    value, label, name = _text(p.get("value", "")), _text(p.get("label", "消息内容"), True), _text(p.get("name", "content"), True)
    form = _text(p["form"], True) if "form" in p else ""
    required = _flag(p.get("required", False))
    attachment, emoji = p.get("attachment", "添加附件"), p.get("emoji", "表情")
    for item in (attachment, emoji):
        if item is not None:
            _text(item, True)
    attrs = {key: val for key, val in _attrs(p.get("attrs")).items() if not key.startswith("data-lq-") and key not in ("aria-busy", "role")}
    if "id" in p:
        attrs["id"] = _text(p["id"], True)
    attrs.update({"class": "lq-composer lq-glass", "data-lq-composer": "", "data-lq-enter": enter,
                  "data-lq-disabled": str(disabled).lower(), "data-lq-busy": str(busy).lower(), "data-lq-has-content": str(has_content).lower(), "aria-busy": str(busy).lower()})
    input_attrs = {"class": "lq-composer__input", "data-lq-composer-input": "", "aria-label": label, "name": name, "rows": "1", "placeholder": _text(p.get("placeholder", "输入消息…")),
                   **({"form": form} if form else {}), **({"required": ""} if required else {}), **({"disabled": ""} if disabled else {}), **({"readonly": ""} if busy else {})}
    if "maxlength" in p:
        maximum = p["maxlength"]
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 9007199254740991:
            raise ValueError("Invalid LQ composer maxlength")
        input_attrs["maxlength"] = str(maximum)
    send = _button(_text(p.get("sendLabel", "发送"), True), "send", "prominent", "submit", disabled or busy or (not value.strip() and not has_content), form, "data-lq-composer-send")
    if "submit_name" in p:
        send.update({"submit_name": _text(p["submit_name"], True), "submit_value": _text(p.get("submit_value", ""))})
    elif "submit_value" in p:
        raise ValueError("Composer submit_value requires submit_name")
    tools = []
    if attachment is not None:
        tools.append(_button(str(attachment), "paperclip", "ghost", "button", disabled or busy, form, "data-lq-composer-attachment"))
    if emoji is not None:
        tools.append(_button(str(emoji), "smile", "ghost", "button", disabled or busy, form, "data-lq-composer-emoji"))
    return _node("div", attrs, [_node("div", {"class": "lq-composer__content", "data-lq-composer-content": ""}, [{"slot": "content"}]),
                                _node("textarea", input_attrs, [value]), _node("div", {"class": "lq-composer__actions"}, [_node("div", {"class": "lq-composer__tools"}, tools), send])])
