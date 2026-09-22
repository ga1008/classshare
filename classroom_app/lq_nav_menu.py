"""Pure navigation-menu contract: a trigger button plus an existing menu panel.

No application, storage or request imports. Item validation is delegated to
``lq_menu_props`` so a nav menu can never grow a second item vocabulary, and the
panel this component reports is literally ``lq_menu``'s product.
"""
from .lq_components import SIZES, TONES, lq_props
from .lq_menu_tooltip import lq_menu_props

NAV_MENU_KINDS = ("nav_menu",)
NAV_MENU_VARIANTS = ("glass", "soft", "ghost")
NAV_MENU_SHAPES = ("capsule", "rounded")
NAV_MENU_ALIGNMENTS = ("start", "end")
NAV_MENU_CARET = "chevron-down"
_PROPS = {"id", "label", "items", "icon", "variant", "tone", "size", "shape", "align"}


def _choice(value, choices, name):
    # Stricter than the shared presentation helper on purpose: a nav menu never
    # coerces a non-string option into a configuration value.
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"Invalid LQ nav menu {name}")
    return value


def lq_nav_menu_props(**props):
    """Normalize one navigation menu; invalid input fails before any HTML."""
    if props.keys() - _PROPS:
        raise ValueError("Invalid LQ nav menu props")
    menu = lq_menu_props(id=props.get("id"), label=props.get("label"), items=props.get("items"))
    identity, label = menu["attrs"]["id"], menu["attrs"]["aria-label"]
    variant = _choice(props.get("variant", "glass"), NAV_MENU_VARIANTS, "variant")
    tone = _choice(props.get("tone", "neutral"), TONES, "tone")
    size = _choice(props.get("size", "md"), SIZES, "size")
    shape = _choice(props.get("shape", "capsule"), NAV_MENU_SHAPES, "shape")
    align = _choice(props.get("align", "start"), NAV_MENU_ALIGNMENTS, "align")
    # The shared button contract owns icon names, escaping and accessible names.
    trigger = lq_props("button", label=label, variant=variant, size=size, icon=props.get("icon"),
                       id=f"{identity}--lq-trigger",
                       attrs={"aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": identity,
                              "data-lq-nav-trigger": "", "data-tone": tone, "data-lq-nav-shape": shape})
    trigger["classes"] = "lq-nav-menu__trigger " + trigger["classes"]
    return {"attrs": {"class": "lq-nav-menu", "data-lq-nav-menu": "", "data-lq-nav-align": align},
            "trigger": trigger, "menu": menu, "caret": NAV_MENU_CARET, "id": identity, "label": label,
            "variant": variant, "tone": tone, "size": size, "shape": shape, "align": align}


def lq_nav_menu_kind_props(kind, **props):
    if kind == "nav_menu":
        return lq_nav_menu_props(**props)
    raise ValueError("Unknown LQ nav menu kind")
