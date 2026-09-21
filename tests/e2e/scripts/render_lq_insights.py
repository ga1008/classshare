"""Pure real-macro fixture. Never imports classroom_app/__init__ or storage."""
from importlib import import_module
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

ROOT = Path(__file__).resolve().parents[3]


def fixture():
    package = types.ModuleType("lq_insights_fixture")
    package.__path__ = [str(ROOT / "classroom_app")]
    sys.modules[package.__name__] = package
    api = import_module("lq_insights_fixture.lq_insights")
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    dispatcher = import_module("lq_insights_fixture.lq").lq_props
    env.globals["lq_props"] = dispatcher
    macros = env.get_template("macros/lq/insights.html").module
    ring = {"title": "文件占比", "value": 3, "total": 7, "caption": "实际文件在材料库中的占比。", "tone": "teal", "value_label": "3 / 7 项", "data_key": "materials-file-ratio"}
    meter = {"title": "材料总数", "value": 5, "percent": 100, "caption": "文件夹 2 · 文件 3", "tone": "amber", "unit": "项", "delta": "已有统计", "data_key": "materials-total"}
    bars = {"title": "题型结构", "items": [{"label": "客观题", "value": 3, "tone": "teal"}, {"label": "主观题", "value": 1, "tone": "violet"}, {"label": "混合", "value": 0, "tone": "indigo"}], "caption": "由调用方统计", "tone": "violet", "limit": 6}
    cases = [{"kind": "avatar_stack", "props": {"items": [{"name": name, "detail": f"组员{index}", **({"src": "/avatar.png"} if index == 0 else {})} for index, name in enumerate(["😀张三", "李四", "王五", "赵六", "孙七", "周八"][:count])], "size": size}}
             for count, size in ((0, 24), (1, 32), (4, 40), (6, 56))]
    cases += [{"kind": "insight_ring", "props": {**ring, **p}} for p in ({}, {"value": 0}, {"value": 0, "zero": "value", "value_label": "0 / 7 项"},
              {"value": None}, {"total": None}, {"value": 0, "total": 0}, {"value": .5, "total": 2.5, "value_label": ""}, {"title": Markup('<img src=x onerror=alert(1)>')})]
    cases += [{"kind": "insight_meter", "props": {**meter, **p}} for p in ({}, {"value": 0}, {"value": 0, "zero": "value", "unit": "分"},
              {"value": None}, {"percent": None}, {"value": .0000001, "percent": 0}, {"value": .000001, "percent": 0}, {"value": 1234.5, "percent": 12.5})]
    cases += [{"kind": "insight_bars", "props": {**bars, **p}} for p in ({}, {"items": []}, {"items": [{"label": "未知", "value": None}]},
              {"items": [{"label": "零分", "value": 0, "zero": "value"}, {"label": "未提供", "value": None}, {"label": "高分", "value": 95}]},
              {"items": [{"label": "零", "value": 0}]}, {"limit": 0}, {"limit": 1})]
    invalid = [
        *({"kind": "avatar_stack", "props": p} for p in ({"items": None}, {"items": [{"name": ""}]}, {"items": [], "size": 0}, {"items": [], "size": True},
          {"items": [{"name": "A", "src": "javascript:x"}]}, {"items": [{"name": "A", "detail": None}]}, {"items": [{"name": "A", "html": "<b>A</b>"}]},
          {"items": [{"name": "A"}] * 4 + [{"name": "B", "src": "//external/unsafe"}]})),
        *({"kind": "insight_ring", "props": {**ring, **p}} for p in ({"value": -1}, {"value": True}, {"value": "3"}, {"value": 8}, {"total": 0},
          {"tone": "url(x)"}, {"tone": "__proto__"}, {"caption": {}}, {"attrs": {"onclick": "x"}}, {"zero": True}, {"__proto__": {}}, {"value_label": None})),
        *({"kind": "insight_meter", "props": {**meter, **p}} for p in ({"percent": 101}, {"percent": -1}, {"percent": "1"}, {"value": "--"}, {"unit": None}, {"rawHTML": "x"})),
        *({"kind": "insight_bars", "props": {**bars, **p}} for p in ({"limit": -1}, {"limit": True}, {"items": None}, {"items": [{"label": "A"}]},
          {"items": [{"label": "A", "value": 1, "tone": "red;display:none"}]}, {"items": [{"label": "A", "value": 1, "tone": "#0d9488"}]}, {"items": [{"label": "A", "value": False}]})),
    ]
    for case in cases + invalid:
        try:
            case["normalized"] = dispatcher(case["kind"], **case["props"])
            macro = "lq_avatar_stack" if case["kind"] == "avatar_stack" else case["kind"]
            case["html"] = str(getattr(macros, macro)(**case["props"]))
        except (ValueError, TypeError) as error:
            case["error"] = type(error).__name__
    old = env.get_template("macros/manage_insights.html").module
    signatures = {kind: {"old": list(getattr(old, kind).arguments), "new": list(getattr(macros, kind).arguments)} for kind in ("insight_ring", "insight_bars", "insight_meter")}
    legacy = [{"kind": kind, "props": props, "old": str(getattr(old, kind)(**props)), "new": str(getattr(macros, kind)(**props))}
              for kind, props in (("insight_ring", ring), ("insight_meter", meter), ("insight_bars", bars))]
    composition = env.from_string("""{% from 'macros/lq/insights.html' import insight_meter %}{% call insight_meter('评分',0,0,unit='分',zero='value') %}<label for="draft">评分说明</label><textarea id="draft">调用方草稿</textarea>{% endcall %}""").render()
    assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
    return {"cases": cases, "invalid": invalid, "signatures": signatures, "legacy": legacy, "composition": composition, "isolated": True}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fixture(), ensure_ascii=False))
