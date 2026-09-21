"""Typed presentation only: no persistence, requests, timers or application imports."""
from .lq_components import _attrs, lq_props
from .lq_tones import lq_tone

STATUS_KINDS = ("status", "save_status", "alert", "conflict")
SAVE_LABELS = {"dirty": "尚未保存", "local_saved": "已保存到本机", "syncing": "正在同步", "synced": "已同步到服务器",
               "offline": "离线，尚未同步", "error": "保存失败", "conflict": "内容有冲突，请重新核对", "submitting": "正在提交", "submitted": "已提交"}


def _string(value, required=False):
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError("LQ status requires text")
    return str(value)


def _mapping(value, allowed):
    if not isinstance(value, dict) or value.keys() - set(allowed):
        raise ValueError("Invalid LQ status props")


def _node(tag, attrs=None, children=None):
    return {"tag": tag, "attrs": attrs or {}, "children": children or []}


def _span(classes, children=None, attrs=None):
    return _node("span", {"class": classes, **(attrs or {})}, children)


def _action(value, required):
    if value is None:
        if required:
            raise ValueError("LQ error/conflict requires an explicit action")
        return None
    _mapping(value, ("label", "href", "icon", "variant", "attrs", "id"))
    attrs = {key: item for key, item in _attrs(value.get("attrs")).items() if not key.startswith("data-lq-")}
    result = {**value, "label": _string(value.get("label"), True), "variant": value.get("variant", "link"), "size": "sm", "type": "button",
              "attrs": {**attrs, "data-lq-status-action": ""}}
    lq_props("button", **result)
    return result


def _root(props, owned):
    attrs = {key: item for key, item in _attrs(props.get("attrs")).items() if not key.startswith("data-lq-")}
    if "id" in props:
        attrs["id"] = _string(props["id"], True)
    return {**attrs, **owned}


def _actions(action):
    return [{"component": "button", "props": action}] if action else []


def lq_status_props(kind, **props):
    if kind not in STATUS_KINDS:
        raise ValueError("Unknown LQ status kind")
    allowed = ["id", "attrs"] + (["family", "state", "label"] if kind == "status" else
        ["state", "label", "time", "datetime", "action"] if kind == "save_status" else
        ["title", "body", "action", "announce"] + (["tone"] if kind == "alert" else ["local", "server"]))
    _mapping(props, allowed)
    if kind == "status":
        resolved = lq_tone(_string(props.get("family")), _string(props.get("state")))
        return _node("span", _root(props, {"class": "lq-status", "data-lq-status": kind, "data-tone": resolved["name"], "data-lq-tone-level": resolved["level"]}), [
            _span("lq-status__dot", attrs={"aria-hidden": "true"}), _span("lq-status__label", [_string(props.get("label"), True)])])
    if kind == "save_status":
        requested = _string(props.get("state", "dirty"))
        state = requested if requested in SAVE_LABELS else "unknown"
        if "label" in props:
            _string(props["label"], True)
        label = props.get("label", SAVE_LABELS.get(state)) if state != "unknown" else "保存状态未知"
        time = _string(props.get("time", ""))
        datetime = _string(props["datetime"], True) if "datetime" in props else ""
        if datetime and not time.strip():
            raise ValueError("LQ datetime requires visible time")
        action = _action(props.get("action"), state in ("error", "conflict"))
        indicator = [{"component": "spinner", "props": {"size": "sm"}}] if state in ("syncing", "submitting") else [_span("lq-status__dot")]
        return _node("span", _root(props, {"class": "lq-save-status", "tabindex": "-1", "data-lq-status": kind, "data-lq-save-state": state,
            "data-tone": lq_tone("save", state)["name"], "data-lq-tone-level": lq_tone("save", state)["level"]}), [
                _span("lq-status", [_span("lq-status__indicator", indicator, {"aria-hidden": "true", "data-lq-save-indicator": ""}),
                    _span("lq-status__label", [label], {"data-lq-save-label": ""}),
                    _node("time", {"class": "lq-status__time", "data-lq-save-time": "", **({"datetime": datetime} if datetime else {}),
                        **({"hidden": ""} if not time else {})}, [time])]),
                _span("lq-status__actions", _actions(action), {"data-lq-save-action": ""}),
                _span("lq-status__live", attrs={"data-lq-save-live": "", "role": "status", "aria-live": "polite", "aria-atomic": "true"})])
    conflict = kind == "conflict"
    severity = "danger" if conflict else props.get("tone", "info")
    if severity not in ("info", "warning", "danger"):
        raise ValueError("Invalid LQ alert tone")
    announce = props.get("announce", "off")
    if announce not in ("off", "polite", "assertive"):
        raise ValueError("Invalid LQ announcement policy")
    title = _string(props.get("title", "内容已变化，请重新核对" if conflict else ""))
    body = _string(props.get("body", "本地修改已保留，尚未覆盖服务器内容。" if conflict else ""))
    if not title.strip() and not body.strip():
        raise ValueError("LQ alert requires visible text")
    action = _action(props.get("action"), conflict)
    content = [_node("strong", {"class": "lq-alert__title", **({"hidden": ""} if not title else {})}, [title]),
               _node("div", {"class": "lq-alert__body", "data-lq-status-slot": "body"}, [{"slot": "body", "text": body}])]
    if conflict:
        for key, label in (("local", "本地内容"), ("server", "服务器内容")):
            content.append(_node("section", {"class": "lq-conflict__version", "data-lq-status-version": key}, [
                _node("strong", children=[label]), _node("div", {"data-lq-status-slot": key}, [{"slot": key, "text": _string(props.get(key, ""))}])]))
    return _node("div", _root(props, {"class": "lq-alert" + (" lq-conflict" if conflict else ""), "data-lq-status": kind,
        "data-tone": lq_tone("save", "conflict")["name"] if conflict else severity, "data-lq-tone-level": severity}), [
            _node("div", {"class": "lq-alert__content", **({} if announce == "off" else {
                "role": "alert" if announce == "assertive" else "status", "aria-live": announce, "aria-atomic": "true"})}, content),
            _node("div", {"class": "lq-alert__actions"}, _actions(action))])
