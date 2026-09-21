"""Typed upload presentation; no controller, requests, or application imports."""
import re
from .lq_components import _attrs, lq_props

UPLOAD_KINDS = ("upload", "dropzone", "file_chip")
FILE_STATES = ("selected", "validating", "rejected", "uploading", "uploaded", "failed", "removing")
LABELS = dict(zip(FILE_STATES, ("已选择，尚未上传", "正在检查", "未接受", "正在上传", "已保存到服务器", "上传失败", "正在移除")))


def _mapping(value, keys):
    if not isinstance(value, dict) or value.keys() - set(keys):
        raise ValueError("Invalid upload props")


def _text(value, required=False):
    if not isinstance(value, str) or required and not value.strip():
        raise ValueError("Upload requires text")
    return str(value)


def _integer(value):
    if type(value) is not int or not 0 <= value <= 9007199254740991:
        raise ValueError("Invalid upload generation")
    return value


def _flag(value):
    if type(value) is not bool:
        raise ValueError("Invalid upload flag")
    return value


def _node(tag, attrs=None, children=None):
    return {"tag": tag, "attrs": attrs or {}, "children": children or []}


def upload_item(value):
    _mapping(value, ("id", "generation", "name", "sizeLabel", "state", "progress", "reason", "questionLabel", "rejectionCode", "duplicateOfQuestion", "confirmation", "retryable", "removable"))
    item = {key: _text(value.get(key, ""), key in ("id", "name")) for key in ("id", "name", "sizeLabel", "state", "reason", "questionLabel", "rejectionCode", "duplicateOfQuestion")}
    item.update(generation=_integer(value.get("generation")), retryable=_flag(value.get("retryable", True)), removable=_flag(value.get("removable", True)))
    if item["state"] not in FILE_STATES or item["state"] in ("rejected", "failed") and not item["reason"].strip():
        raise ValueError("Invalid file state/reason")
    if item["rejectionCode"] and (item["rejectionCode"] != "duplicate-image" or item["state"] != "rejected" or not item["duplicateOfQuestion"].strip()):
        raise ValueError("Duplicate image needs owning question")
    if "progress" in value:
        progress = value["progress"]
        if item["state"] != "uploading" or type(progress) not in (int, float) or not 0 <= progress <= 100:
            raise ValueError("Invalid progress")
        item["progress"] = progress
    if "confirmation" in value:
        _mapping(value["confirmation"], ("id",))
        identity = value["confirmation"].get("id")
        if not (isinstance(identity, str) and identity.strip()) and not (type(identity) is int and 0 <= identity <= 9007199254740991):
            raise ValueError("Invalid confirmation")
        item["confirmation"] = {"id": str(identity)}
    if item["state"] == "uploaded" and "confirmation" not in item:
        raise ValueError("Uploaded requires server confirmation")
    return item


def upload_snapshot(value):
    _mapping(value, ("generation", "disabled", "items"))
    generation, disabled = _integer(value.get("generation")), _flag(value.get("disabled", False))
    if not isinstance(value.get("items"), list):
        raise ValueError("Items must be a list")
    items = [upload_item(item) for item in value["items"]]
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("Duplicate file identity")
    state = "busy" if any(item["state"] in ("validating", "uploading", "removing") for item in items) else "partial-failed" if any(item["state"] in ("rejected", "failed") for item in items) else "idle"
    return {"generation": generation, "disabled": disabled, "items": items, "state": state}


def _action(identity, disabled):
    props = {"label": "重试" if identity == "retry" else "移除", "variant": "ghost", "size": "sm", "disabled": disabled, "attrs": {"data-lq-upload-action": identity}}
    lq_props("button", **props)
    return {"component": "button", "props": props}


def _chip(item, disabled):
    blocked = disabled or item["state"] in ("validating", "uploading", "removing")
    content = [_node("strong", {"class": "lq-file-chip__name"}, [item["name"]]),
               _node("span", {"class": "lq-file-chip__meta"}, [" · ".join(v for v in (item["questionLabel"], item["sizeLabel"]) if v)]),
               _node("span", {"class": "lq-file-chip__state"}, ["上传完成，等待服务器确认" if item["state"] == "uploading" and item.get("progress") == 100 else LABELS[item["state"]]])]
    if item["state"] == "uploading":
        content.append(_node("progress", {"max": "100", **({"value": format(item["progress"], "g")} if "progress" in item else {}), "aria-label": item["name"] + "上传进度"}))
    if item["reason"]:
        content.append(_node("p", {"class": "lq-file-chip__reason"}, [item["reason"]]))
    if item["duplicateOfQuestion"]:
        content.append(_node("p", {"class": "lq-file-chip__owner"}, ["截图归属：" + item["duplicateOfQuestion"]]))
    actions = []
    if item["state"] == "failed" and item["retryable"]:
        actions.append(_action("retry", blocked))
    if item["removable"]:
        actions.append(_action("remove", blocked))
    return _node("div", {"class": "lq-file-chip", "data-lq-upload": "file_chip", "data-file-id": item["id"], "data-file-generation": str(item["generation"]), "data-file-state": item["state"], "role": "group", "aria-label": item["name"], "tabindex": "-1"}, [
        _node("div", {"class": "lq-file-chip__content"}, content), _node("div", {"class": "lq-file-chip__actions"}, actions)])


def lq_upload_props(kind, **props):
    if kind not in UPLOAD_KINDS:
        raise ValueError("Unknown upload kind")
    _mapping(props, ("item", "disabled") if kind == "file_chip" else ("id", "label", "policy", "accept", "multiple", "name", "form", "snapshot", "attrs"))
    if kind == "file_chip":
        return _chip(upload_item(props.get("item")), _flag(props.get("disabled", False)))
    identity, label, policy = (_text(props.get(key), True) for key in ("id", "label", "policy"))
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", identity):
        raise ValueError("Invalid upload id")
    snapshot = upload_snapshot(props.get("snapshot", {"generation": 0, "items": []}))
    attrs = {key: value for key, value in _attrs(props.get("attrs")).items() if not key.startswith(("data-lq-", "aria-")) and key not in ("id", "name", "form", "target", "rel")}
    input_attrs = {"id": identity + "-input", "type": "file", "class": "lq-dropzone__input", "aria-describedby": identity + "-policy", "accept": _text(props.get("accept", ""))}
    if _flag(props.get("multiple", True)):
        input_attrs["multiple"] = ""
    if snapshot["disabled"]:
        input_attrs["disabled"] = ""
    for key in ("name", "form"):
        if key in props:
            input_attrs[key] = _text(props[key])
    summary = {"idle": "当前无传输任务", "busy": "仍有文件处理中", "partial-failed": "部分文件未完成，请检查原因"}[snapshot["state"]]
    return _node("div", {**attrs, "id": identity, "class": "lq-upload", "data-lq-upload": kind, "data-upload-state": snapshot["state"], "data-upload-generation": str(snapshot["generation"]), "tabindex": "-1"}, [
        _node("div", {"class": "lq-dropzone", "data-lq-upload-dropzone": ""}, [_node("label", {"class": "lq-dropzone__label", "for": input_attrs["id"]}, [label]), _node("p", {"class": "lq-dropzone__hint"}, ["选择文件，或在此拖放、粘贴文件"]), _node("input", input_attrs)]),
        _node("p", {"id": identity + "-policy", "class": "lq-upload__policy"}, [policy]),
        _node("p", {"class": "lq-upload__summary", "data-lq-upload-summary": ""}, [f'{len(snapshot["items"])} 个文件 · {summary}']),
        _node("div", {"class": "lq-upload__list", "data-lq-upload-list": ""}, [_chip(item, snapshot["disabled"]) for item in snapshot["items"]]),
        _node("p", {"class": "lq-upload__notice", "data-lq-upload-notice": "", "role": "status", "aria-live": "polite", "aria-atomic": "true"})])
