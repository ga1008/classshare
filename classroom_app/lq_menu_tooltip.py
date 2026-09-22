"""Pure Menu/Tooltip presentation contracts; no application startup imports."""
import re
from .lq_components import lq_props, _url

MENU_TOOLTIP_KINDS = ("menu", "tooltip")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("LQ menu/tooltip requires text")
    return str(value).strip()


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value):
        raise ValueError("Invalid LQ identity")
    return value


def _flag(value):
    if not isinstance(value, bool):
        raise ValueError("Invalid LQ menu flag")
    return value


def _item_attrs(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("LQ menu item attrs must be a mapping")
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"data-(?!lq-)[a-z0-9-]+", key):
            raise ValueError("LQ menu item attrs allow only non-reserved data attributes")
        if item is None:
            continue
        if not isinstance(item, (str, int, float, bool)) or isinstance(item, float) and item != item:
            raise ValueError("LQ menu item attr value must be scalar")
        result[key] = "" if item is True else ("" if item is False else str(item))
    return result


def lq_menu_props(**props):
    if props.keys() - {"id", "label", "items"}:
        raise ValueError("Invalid LQ menu props")
    identity, label = _identity(props.get("id")), _text(props.get("label"))
    values = props.get("items")
    if not isinstance(values, list) or not values:
        raise ValueError("LQ menu needs items")
    items, seen = [], set()
    for value in values:
        if not isinstance(value, dict) or value.keys() - {"id", "label", "icon", "href", "target", "disabled", "danger", "group", "attrs"}:
            raise ValueError("Invalid LQ menu item")
        item_id, item_label = _text(value.get("id")), _text(value.get("label"))
        if item_id in seen:
            raise ValueError("Duplicate menu item")
        seen.add(item_id)
        disabled, danger = _flag(value.get("disabled", False)), _flag(value.get("danger", False))
        group = _text(value["group"]) if "group" in value else ""
        href = _url(value["href"]) if "href" in value else None
        if "href" in value and href is None:
            raise ValueError("Invalid menu href")
        if "target" in value and (not href or value["target"] not in ("_blank", "_self")):
            raise ValueError("Invalid menu target")
        # Pages need their own hooks on a menu item (the account menu opens two
        # modals by data attribute). Caller keys are merged first so the ones the
        # component owns always win, and `data-lq-` stays reserved for components.
        attrs = dict(_item_attrs(value.get("attrs")))
        attrs["data-lq-menu-item"] = item_id
        if danger:
            attrs["data-danger"] = "true"
        if "target" in value:
            attrs["target"] = value["target"]
        button = lq_props("button", label=item_label, variant="ghost", icon=_text(value["icon"]) if "icon" in value else None,
                          href=href, aria_disabled=disabled, attrs=attrs)
        button["classes"] = "lq-menu__item " + button["classes"]
        button["attrs"] = {"role": "menuitem", "tabindex": "-1", **button["attrs"]}
        items.append({"id": item_id, "label": item_label, "disabled": disabled, "danger": danger, "group": group, "button": button,
                      "separator": bool(items and (items[-1]["group"] != group or items[-1]["danger"] != danger))})
    return {"items": items, "attrs": {"id": identity, "class": "lq-menu lq-glass", "role": "menu", "aria-label": label, "tabindex": "-1", "hidden": ""}}


def lq_tooltip_props(**props):
    if props.keys() - {"id", "text"}:
        raise ValueError("Invalid LQ tooltip props")
    return {"text": _text(props.get("text")), "attrs": {"id": _identity(props.get("id")), "class": "lq-tooltip lq-glass lq-glass--thin", "role": "tooltip", "hidden": ""}}


def lq_menu_tooltip_props(kind, **props):
    if kind == "menu":
        return lq_menu_props(**props)
    if kind == "tooltip":
        return lq_tooltip_props(**props)
    raise ValueError("Unknown LQ menu/tooltip kind")
