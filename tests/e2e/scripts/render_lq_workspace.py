"""Pure real Jinja fixture: imports no application or database state."""
import importlib.util
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]
package = types.ModuleType("lq_workspace_fixture")
package.__path__ = [str(ROOT / "classroom_app")]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(package.__name__ + ".lq_workspace", ROOT / "classroom_app/lq_workspace.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
env.globals["lq_props"] = helper.lq_workspace_props
macros = env.get_template("macros/lq/workspace.html").module
base = {"id": "demo", "label": "阅读工作区", "sideLabel": "目录", "mainLabel": "正文"}
cases = [("split", base), ("split", {**base, "width": 320, "selected": "side"}), ("split", {**base, "label": '<script>"&', "attrs": {"data-custom": "值", "aria-label": "替代名称"}}), ("viewer", {"id": "document", "title": "知识说明"}), ("viewer", {"id": "frame", "title": "课件 < &", "kind": "iframe"})]
invalid = [("split", {**base, **change}) for change in [{"min": 1000}, {"width": 100}, {"max": 200}, {"width": True}, {"width": 280.5}, {"selected": "unknown"}, {"selected": None}, {"sideLabel": ""}, {"attrs": {"onclick": "bad()"}}, {"id": "demo--lq-test"}, {"rawHTML": "unsafe"}]] + [("viewer", {"id": "view", "title": "内容", "kind": "unknown"}), ("viewer", {"id": "view", "title": "内容", "kind": None}), ("viewer", {"id": "view", "title": {}})]
payload = {"cases": [], "invalid": []}
for key, group in [("cases", cases), ("invalid", invalid)]:
    for kind, props in group:
        row = {"kind": kind, "props": props}
        try:
            row["tree"] = helper.lq_workspace_props(kind, **props)
            row["html"] = str(getattr(macros, "lq_" + kind)(**props))
        except (ValueError, TypeError) as error:
            row["error"] = type(error).__name__
        payload[key].append(row)
assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
