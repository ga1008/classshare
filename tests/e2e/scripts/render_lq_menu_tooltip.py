"""Render actual macros through pure imports; no app/.env/database initialization."""
import importlib.util
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]
package = types.ModuleType("lq_menu_fixture")
package.__path__ = [str(ROOT / "classroom_app")]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location("lq_menu_fixture.lq_menu_tooltip", ROOT / "classroom_app/lq_menu_tooltip.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
env.globals["lq_props"] = module.lq_menu_tooltip_props
payload = json.loads((ROOT / "tests/e2e/components/fixtures/lq-menu-tooltip.json").read_text(encoding="utf-8"))
for group in list(payload):
    kind = "menu" if group.lower().endswith("menus") else "tooltip"
    macro = getattr(env.get_template(f"macros/lq/{kind}s.html").module, f"lq_{kind}")
    rendered = []
    for props in payload[group]:
        try:
            rendered.append({"props": props, "html": str(macro(**props))})
        except (TypeError, ValueError) as error:
            rendered.append({"props": props, "error": type(error).__name__})
    payload[group] = rendered
assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
assert not any(name in sys.modules for name in ("sqlite3", "psycopg", "dotenv"))
payload["isolated"] = True
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
