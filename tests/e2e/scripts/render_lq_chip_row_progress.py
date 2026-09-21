"""Render real macros through pure modules; never import application or storage."""
from importlib import import_module
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]


def fixture():
    package = types.ModuleType("lq_chip_row_fixture")
    package.__path__ = [str(ROOT / "classroom_app")]
    sys.modules[package.__name__] = package
    api = import_module("lq_chip_row_fixture.lq_chip_row")
    presentation = import_module("lq_chip_row_fixture.lq_components")
    def dispatch(component, **props):
        return api.lq_chip_row_props(component, **props) if component == "chip_row" else presentation.lq_props(component, **props)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    env.globals["lq_props"] = dispatch
    chips = env.get_template("macros/lq/chip-row.html").module.lq_chip_row
    progress = env.get_template("macros/lq/indicators.html").module.lq_progress
    row = {"id": "filters", "label": "课程筛选", "items": [{"label": f"课程 {i + 1}", "kind": "filter", "pressed": i == 9} for i in range(12)]}
    cases = [{"kind": "chip_row", "props": {**row, **p}} for p in ({}, {"items": []}, {"items": row["items"][:8]}, {"items": [{"label": '<img src=x onerror=alert(1)>', "kind": "tag", "removable": True, "removeLabel": "移除文本"}]} )]
    cases += [{"kind": "progress", "props": {"label": "上传进度", "variant": "ring", **p}} for p in ({"value": 0}, {"value": 100}, {"value": 3, "max": 7}, {}, {"value": None}, {"value": 2.5, "max": 10}, {"value": 10, "attrs": {"aria-valuenow": "90", "aria-valuemax": "200", "aria-valuetext": "伪造"}}, {"variant": "bar", "value": 40})]
    invalid = [{"kind": "chip_row", "props": {**row, **p}} for p in ({"id": 'x" onclick="bad'}, {"id": None}, {"label": ""}, {"items": None}, {"items": [{}]}, {"items": [{"label": "x", "rawHTML": "x"}]}, {"items": [{"label": "x", "kind": "menu"}]}, {"items": [{"label": "x", "pressed": 1}]}, {"items": [{"label": "x", "attrs": {"onclick": "bad"}}]}, {"body": "bad"})]
    invalid += [{"kind": "progress", "props": {"label": "进度", "variant": "ring", **p}} for p in ({"variant": None}, {"variant": "pie"}, {"max": 0}, {"max": True}, {"value": -1}, {"value": 101}, {"value": "20"}, {"value": True})]
    for case in cases + invalid:
        try:
            case["normalized"] = dispatch(case["kind"], **case["props"])
            case["html"] = str((chips if case["kind"] == "chip_row" else progress)(**case["props"]))
        except (ValueError, TypeError) as error:
            case["error"] = type(error).__name__
    assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
    return {"cases": cases, "invalid": invalid, "isolated": True}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fixture(), ensure_ascii=False))
