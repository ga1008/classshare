"""Render real dialog macros via pure modules; never import application startup."""
import importlib.util
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]


def fixture():
    package = types.ModuleType("lq_dialog_fixture")
    package.__path__ = [str(ROOT / "classroom_app")]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location("lq_dialog_fixture.lq_dialogs", ROOT / "classroom_app/lq_dialogs.py")
    dialogs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dialogs)
    components = sys.modules["lq_dialog_fixture.lq_components"]
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    env.globals.update(lq_props=components.lq_props, lq_dialog_props=dialogs.lq_dialog_props)
    macro = env.get_template("macros/lq/dialogs.html").module.lq_dialog
    payload = json.loads((ROOT / "tests/e2e/components/fixtures/lq-dialogs.json").read_text(encoding="utf-8"))
    for group in ("cases", "invalid"):
        payload[group] = [{"props": p} for p in payload[group]]
        for case in payload[group]:
            try:
                case["html"] = str(macro(**case["props"]))
            except (ValueError, TypeError) as error:
                case["error"] = type(error).__name__
    assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
    assert not any(name in sys.modules for name in ("sqlite3", "psycopg", "dotenv"))
    payload["isolated"] = True
    return payload


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fixture(), ensure_ascii=False))
