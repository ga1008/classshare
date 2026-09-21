"""Render actual macros with pure modules, never importing the application."""
import importlib.util
import json
from pathlib import Path
import sys
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"classroom_app/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    nav, presentation = load("lq_navigation"), load("lq_components")
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    env.globals["lq_props"] = lambda kind, **p: nav.lq_navigation_props(kind, **p) if kind in nav.NAVIGATION_KINDS else presentation.lq_props(kind, **p)
    macros = env.get_template("macros/lq/navigation.html").module
    items = [{"key": "first", "label": "课程概览", "panel": "第一块说明", "badge": 2},
             {"key": "disabled", "label": "不可用", "disabled": True},
             {"key": "second", "label": "草稿", "panel": "<img src=x onerror=alert(1)>"},
             {"key": "third", "label": "非常长的视图标题" * 8, "panel": "最后一块内容", "badge": 0}]
    base = {"id": "navigation", "label": "学习视图", "items": items}
    cases = []
    for kind, variant, orientation in (("tabs", "line", "horizontal"), ("tabs", "pill", "horizontal"), ("tabs", "line", "vertical"), ("segment", "line", "horizontal")):
        for activation in ("auto", "manual"):
            for size in ("sm", "md"):
                props = {**base, "variant": variant, "orientation": orientation, "activation": activation, "size": size}
                cases.append({"kind": kind, "props": props})
    bad_props = [{"id": "bad id"}, {"id": "reserved--lq-thing"}, {"label": ""}, {"label": None}, {"items": []},
                 {"items": [items[0], items[0]]}, {"items": [items[1], {**items[1], "key": "other"}]},
                 {"selected": "disabled"}, {"selected": "absent"}, {"selected": None}, {"activation": "invalid"},
                 {"orientation": "diagonal"}, {"variant": None}, {"size": "lg"},
                 *({"items": [items[0], {**items[2], "disabled": value}]} for value in ("false", None, 0)),
                 *({"items": [items[0], {**items[2], "badge": value}]} for value in (-1, 1.5, True, "3", 9007199254740992)),
                 {"items": [items[0], {**items[2], "panel": {"html": "x"}}]}]
    invalid = [{"kind": "tabs", "props": {**base, **props}} for props in bad_props]
    invalid += [{"kind": "segment", "props": {**base, **props}} for props in ({"orientation": "vertical"}, {"variant": "pill"}, {"items": [{"key": f"key{i}", "label": "label"} for i in range(6)]})]
    for case in cases + invalid:
        try:
            case["normalized"] = nav.lq_navigation_props(case["kind"], **case["props"])
            case["html"] = str(getattr(macros, "lq_" + case["kind"])(**case["props"]))
        except (ValueError, TypeError) as error:
            case["error"] = type(error).__name__
    composition = env.from_string("""{% from 'macros/lq/navigation.html' import lq_tabs %}{% call(key) lq_tabs('composed','组合视图',[{'key':'first','label':'概览'}, {'key':'second','label':'草稿'}]) %}<label for="{{ key }}-draft">{{ key }}</label><input id="{{ key }}-draft" value="保留草稿">{% endcall %}""").render()
    assert not any(name == 'classroom_app' or name.startswith('classroom_app.') for name in sys.modules)
    return {"cases": cases, "invalid": invalid, "composition": composition, "isolated": True}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fixture(), ensure_ascii=False))
