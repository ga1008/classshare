"""Actual Jinja upload structures, with pure helper imports only."""
import importlib.util
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]
package = types.ModuleType("lq_upload_fixture")
package.__path__ = [str(ROOT / "classroom_app")]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location("lq_upload_fixture.lq_upload", ROOT / "classroom_app/lq_upload.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
env.globals["lq_props"] = lambda kind, **props: module.lq_upload_props(kind, **props) if kind in module.UPLOAD_KINDS else module.lq_props(kind, **props)
macros = env.get_template("macros/lq/upload.html").module
payload = json.loads((ROOT / "tests/e2e/components/fixtures/lq-upload.json").read_text(encoding="utf-8"))
for case in payload["cases"] + payload["invalid"]:
    try:
        module.lq_upload_props(case["kind"], **case["props"])
        case["html"] = str(getattr(macros, "lq_" + case["kind"])(**case["props"]))
    except (ValueError, TypeError) as error:
        case["error"] = type(error).__name__
assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
assert not any(name in sys.modules for name in ("sqlite3", "psycopg", "dotenv"))
payload["isolated"] = True
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
