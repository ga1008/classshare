"""Controlled business presentation. No task/clock owner, network or permissions."""
from .lq_components import _attrs, lq_props
from .lq_tones import lq_tone

BUSINESS_KINDS = ("deadline_clock", "job_status", "question_navigator")


def _text(value, required=False):
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError("LQ business text must be a string")
    return str(value)


def _integer(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= 9007199254740991 or int(value) != value:
        raise ValueError("LQ business number must be a safe integer")
    return int(value)


def _flag(value):
    if not isinstance(value, bool):
        raise ValueError("LQ business flags must be boolean")
    return value


def _mapping(value, keys):
    if not isinstance(value, dict) or value.keys() - set(keys):
        raise ValueError("Invalid LQ business props")


def _array(value):
    if not isinstance(value, list):
        raise ValueError("LQ business items must be an array")
    return value


def _node(tag, attrs=None, children=None):
    return {"tag": tag, "attrs": attrs or {}, "children": children or []}


def _root(props, owned):
    attrs = {key: value for key, value in _attrs(props.get("attrs")).items() if not key.startswith(("data-lq-", "data-assignment-"))}
    if "id" in props:
        attrs["id"] = _text(props["id"], True)
    return {**attrs, **owned}


def _action(value):
    _mapping(value, ("key", "label", "href", "icon", "variant", "disabled", "attrs"))
    key, label = _text(value.get("key"), True), _text(value.get("label"), True)
    extra = {key: value for key, value in _attrs(value.get("attrs")).items() if not key.startswith("data-lq-")}
    props = {"label": label, "href": value.get("href"), "icon": value.get("icon"), "variant": value.get("variant", "soft"),
             "disabled": _flag(value.get("disabled", False)), "type": "button", "size": "sm", "attrs": {**extra, "data-lq-job-action": key}}
    lq_props("button", **props)
    return {"key": key, "props": props}


def lq_business_props(kind, **props):
    if kind not in BUSINESS_KINDS:
        raise ValueError("Unknown LQ business component")
    common = ["id", "attrs"]
    if kind == "deadline_clock":
        fields = ["assignment_id", "server_now", "countdown_at", "starts_at", "resubmission_due_at", "late_until", "deadline_phase", "late_policy_label"]
        flags = ["personal_resubmission", "can_resubmit", "accepting", "late_open"]
        _mapping(props, common + fields + flags + ["compact", "label", "value", "detail", "absolute"])
        data = {}
        for key in fields:
            value = props.get(key, "none" if key == "deadline_phase" else "")
            if key == "assignment_id" and isinstance(value, (int, float)) and not isinstance(value, bool):
                value = str(_integer(value, 1))
            data["data-" + key.replace("_", "-")] = _text(value)
        for key in flags:
            data["data-" + key.replace("_", "-")] = "1" if _flag(props.get(key, False)) else "0"
        compact = _flag(props.get("compact", False))
        deadline = data["data-resubmission-due-at"] if data["data-personal-resubmission"] == "1" else data["data-countdown-at"]
        resolved = lq_tone("deadline", data["data-deadline-phase"])
        return _node("div", _root(props, {"class": "lq-clock" + (" lq-clock--compact" if compact else ""), "data-lq-business": kind,
            "data-assignment-clock": "", "data-tone": resolved["name"], "data-lq-tone-level": resolved["level"], **data}), [
                _node("span", {"class": "lq-clock__dot", "aria-hidden": "true"}),
                _node("span", {"class": "lq-clock__label", "data-assignment-clock-label": ""}, [_text(props.get("label", "剩余时间"))]),
                _node("strong", {"class": "lq-clock__value", "data-assignment-clock-value": "", "role": "timer", "aria-live": "off", **({"hidden": ""} if compact else {})}, [_text(props.get("value", "--:--:--"))]),
                _node("strong", {"class": "lq-clock__compact", "data-lq-clock-compact": "", "role": "timer", "aria-live": "off", **({} if compact else {"hidden": ""})}, ["--:--"]),
                _node("small", {"class": "lq-clock__detail", "data-assignment-clock-detail": ""}, [_text(props.get("detail", ""))]),
                _node("span", {"class": "lq-clock__absolute"}, ["绝对截止：", _node("time", {"data-lq-clock-absolute": "", **({"datetime": deadline} if deadline else {})}, [_text(props.get("absolute", deadline or "未设置截止时间"))])])])
    if kind == "job_status":
        _mapping(props, common + ["identity", "generation", "family", "state", "label", "message", "elapsed", "progress", "actions"])
        identity, generation = _text(props.get("identity"), True), _integer(props.get("generation"))
        family = _text(props.get("family", "job"))
        if family not in ("job", "agent"):
            raise ValueError("LQ jobs require a job or agent family")
        state = _text(props.get("state"))
        resolved = lq_tone(family, state)
        label, message, elapsed = _text(props.get("label"), True), _text(props.get("message", "")), _text(props.get("elapsed", ""))
        actions = [_action(value) for value in _array(props.get("actions", []))]
        if len({action["key"] for action in actions}) != len(actions):
            raise ValueError("Duplicate LQ job action key")
        progress = None
        if props.get("progress") is not None:
            value = props["progress"]
            _mapping(value, ("value", "max", "label"))
            progress = {"label": _text(value.get("label"), True), "max": value.get("max", 100), "value": value.get("value")}
            lq_props("progress", **progress)
        return _node("section", _root(props, {"class": "lq-job", "tabindex": "-1", "data-lq-business": kind, "data-lq-job-identity": identity,
            "data-lq-job-generation": str(generation), "data-lq-job-state": state if resolved["known"] else "unknown"}), [
                _node("div", {"class": "lq-job__head"}, [{"status": {"family": family, "state": state, "label": label if resolved["known"] else "任务状态未知"}},
                    _node("span", {"class": "lq-job__elapsed", "data-lq-job-elapsed": ""}, [elapsed])]),
                _node("div", {"class": "lq-job__progress"}, [{"component": "progress", "props": progress}] if progress else []),
                _node("p", {"class": "lq-job__message"}, [message]),
                _node("p", {"class": "lq-job__superseded", **({} if state == "superseded" else {"hidden": ""})}, ["此任务已被更新的任务替代，请核对最新结果。"]),
                _node("div", {"class": "lq-job__actions"}, [{"component": "button", "props": action["props"]} for action in actions])])
    _mapping(props, common + ["label", "groups"])
    identities, numbers, group_ids = set(), set(), set()
    current_count = count = 0
    groups = []
    for group in _array(props.get("groups")):
        _mapping(group, ("id", "label", "items"))
        group_id, label = _text(group.get("id"), True), _text(group.get("label"), True)
        if group_id in group_ids:
            raise ValueError("Duplicate LQ question group")
        group_ids.add(group_id)
        items = []
        for item in _array(group.get("items")):
            _mapping(item, ("id", "index", "label", "answered", "current", "flagged", "error", "pendingUpload", "disabled"))
            identity, index, title = _text(item.get("id"), True), _integer(item.get("index"), 1), _text(item.get("label", ""))
            if identity in identities or index in numbers:
                raise ValueError("Duplicate LQ question identity/index")
            identities.add(identity)
            numbers.add(index)
            count += 1
            flags = {key: _flag(item.get(key, False)) for key in ("answered", "current", "flagged", "error", "pendingUpload", "disabled")}
            current_count += int(flags["current"])
            names = ["已作答" if flags["answered"] else "未作答"] + [label for key, label in (("current", "当前题"), ("flagged", "已标记"), ("error", "有错误"), ("pendingUpload", "附件上传中")) if flags[key]]
            classes = ["lq-nav-grid__item"] + ["is-" + key for key in ("answered", "current", "flagged", "error") if flags[key]] + (["is-pending-upload"] if flags["pendingUpload"] else [])
            items.append(_node("button", {"type": "button", "class": " ".join(classes), "data-lq-question": identity, "data-lq-question-index": str(index),
                "aria-label": "，".join([f"第{index}题"] + ([title] if title else []) + names), **({"aria-current": "step"} if flags["current"] else {}),
                **({"disabled": "", "aria-disabled": "true"} if flags["disabled"] else {})}, [
                    _node("span", {"class": "lq-nav-grid__number"}, [str(index)]),
                    _node("span", {"class": "lq-nav-grid__marks", "aria-hidden": "true"}, ["".join(["✓" if flags["answered"] else "", "⚑" if flags["flagged"] else "", "!" if flags["error"] else "", "•" if flags["pendingUpload"] else ""])])]))
        groups.append(_node("section", {"class": "lq-nav-grid__group", "data-lq-question-group": group_id, "role": "group", "aria-label": label}, [
            _node("p", {"class": "lq-nav-grid__title"}, [label]), _node("div", {"class": "lq-nav-grid__items"}, items)]))
    if current_count > 1:
        raise ValueError("Only one current question is allowed")
    return _node("nav", _root(props, {"class": "lq-nav-grid", "tabindex": "-1", "data-lq-business": kind,
        "aria-label": _text(props.get("label", "答题卡"), True)}), groups if count else [_node("p", {"class": "lq-nav-grid__empty"}, ["暂无题目"])])
