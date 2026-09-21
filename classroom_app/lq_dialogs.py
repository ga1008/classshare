"""Pure, text-only dialog presentation props; no application or database imports."""
import re
from .lq_components import _attrs


def lq_dialog_props(**props):
    allowed = {"id", "type", "size", "side", "title", "body", "footer", "closeLabel", "closeButton", "attrs"}
    if props.keys() - allowed:
        raise ValueError("Unknown LQ dialog prop")
    def text(name, default=""):
        value = props.get(name, default)
        if not isinstance(value, str):
            raise ValueError("LQ dialog text must be a string")
        return str(value)
    kind = props.get("type", "modal")
    sizes = {"modal": ("sm", "md", "lg", "xl", "full"), "sheet": ("md",), "drawer": ("md", "wide"), "popover": ("md",)}
    if kind not in sizes:
        raise ValueError("Invalid LQ dialog type")
    size = props.get("size", "md")
    if size not in sizes[kind]:
        raise ValueError("Invalid LQ dialog size")
    side = props.get("side", "bottom" if kind == "sheet" else "right")
    if side not in ("bottom", "right") or (kind != "sheet" and "side" in props):
        raise ValueError("Invalid LQ dialog side")
    identity, title, close_label = text("id"), text("title").strip(), text("closeLabel", "关闭").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", identity) or "--lq-" in identity:
        raise ValueError("LQ dialog needs a safe unique id")
    if not title or not close_label:
        raise ValueError("LQ dialog and close button need names")
    close_button = props.get("closeButton", True)
    if not isinstance(close_button, bool):
        raise ValueError("LQ dialog flags must be boolean")
    body, footer = text("body"), text("footer")
    attrs = _attrs(props.get("attrs"))
    attrs = {key: value for key, value in attrs.items() if not key.startswith("data-lq-") and key not in ("id", "aria-label", "aria-labelledby", "aria-describedby")}
    root_attrs = {**attrs, "id": identity, "class": "lq-dialog-root", "data-lq-dialog": kind, "hidden": ""}
    surface_attrs = {"class": f"lq-dialog__surface lq-{kind} lq-{kind}--{size}" + (f" lq-sheet--{side}" if kind == "sheet" else "") + " lq-glass" + ("" if kind == "popover" else " lq-glass--thick"),
                     "role": "dialog", "tabindex": "-1", "aria-labelledby": f"{identity}--lq-title"}
    if body:
        surface_attrs["aria-describedby"] = f"{identity}--lq-body"
    surface_attrs["data-ui-overlay-surface"] = ""
    return {"id": identity, "type": kind, "title": title, "body": body, "footer": footer,
            "close_label": close_label, "close_button": close_button, "root_attrs": root_attrs, "surface_attrs": surface_attrs}
