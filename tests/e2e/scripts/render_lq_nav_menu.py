"""Actual Jinja nav-menu fixtures, loading only pure props modules by file path.

No application package, .env, database or network import is allowed here: the
component contract has to be provable on its own.
"""
import importlib.util
import json
from pathlib import Path
import sys
import types

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = types.ModuleType("lq_nav_menu_fixture")
PACKAGE.__path__ = [str(ROOT / "classroom_app")]
sys.modules[PACKAGE.__name__] = PACKAGE


def load(name):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE.__name__}.{name}", ROOT / f"classroom_app/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


components = load("lq_components")
menu_tooltip = load("lq_menu_tooltip")
nav_menu = load("lq_nav_menu")


def dispatch(component, **props):
    """The three groups the nav-menu macro tree actually reaches."""
    if component in nav_menu.NAV_MENU_KINDS:
        return nav_menu.lq_nav_menu_kind_props(component, **props)
    if component in menu_tooltip.MENU_TOOLTIP_KINDS:
        return menu_tooltip.lq_menu_tooltip_props(component, **props)
    return components.lq_props(component, **props)


def fixture():
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    env.globals["lq_props"] = dispatch
    macro = env.get_template("macros/lq/nav-menu.html").module.lq_nav_menu
    payload = json.loads((ROOT / "tests/e2e/components/fixtures/lq-nav-menu.json").read_text(encoding="utf-8"))
    for case in [*payload["cases"], *payload["invalid"]]:
        props = dict(case)
        case.clear()
        case["props"] = props
        try:
            case["normalized"] = nav_menu.lq_nav_menu_props(**props)
            case["html"] = str(macro(**props))
        except (TypeError, ValueError) as error:
            case.pop("normalized", None)
            case["error"] = type(error).__name__
    assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
    assert not any(name in sys.modules for name in ("sqlite3", "psycopg", "dotenv"))
    payload["isolated"] = True
    return payload


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fixture(), ensure_ascii=False))
