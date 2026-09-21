"""Pure ChipRow presentation; no persistence, selection state, or Node slots."""
import re
from .lq_components import lq_props as presentation_props

CHIP_ROW_KINDS = ("chip_row",)
_ITEM_KEYS = {"label", "kind", "tone", "size", "pressed", "disabled", "removable", "removeLabel", "id", "attrs",
              "href"}


def lq_chip_row_props(kind, **props):
    if kind not in CHIP_ROW_KINDS or set(props) - {"id", "label", "items"}:
        raise ValueError("Invalid LQ chip row props")
    identifier, label, items = props.get("id"), props.get("label"), props.get("items")
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]*", identifier):
        raise ValueError("Chip row requires an id")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("Chip row requires a label")
    if not isinstance(items, list):
        raise ValueError("Chip row items must be an array")
    normalized = []
    for item in items:
        if not isinstance(item, dict) or set(item) - _ITEM_KEYS or not isinstance(item.get("label"), str) or not item["label"].strip():
            raise ValueError("Invalid chip row item")
        validation = dict(item)
        if "removeLabel" in validation:
            validation["remove_label"] = validation.pop("removeLabel")
        presentation_props("chip", **validation)
        normalized.append(dict(item))
    return {"attrs": {"id": identifier, "role": "group", "aria-label": label.strip()}, "track_id": f"{identifier}--lq-track", "items": normalized}
