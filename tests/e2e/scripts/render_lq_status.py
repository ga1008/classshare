"""Pure Jinja fixture, deliberately never imports classroom_app/__init__.py."""
import importlib.util
from importlib import import_module
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

ROOT = Path(__file__).resolve().parents[3]


def fixture():
    package = types.ModuleType("lq_status_fixture")
    package.__path__ = [str(ROOT / "classroom_app")]
    sys.modules[package.__name__] = package
    name = "lq_status_fixture.lq_status"
    spec = importlib.util.spec_from_file_location(name, ROOT / "classroom_app/lq_status.py")
    status = importlib.util.module_from_spec(spec)
    sys.modules[name] = status
    spec.loader.exec_module(status)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    env.globals["lq_props"] = import_module('lq_status_fixture.lq').lq_props
    macros = env.get_template("macros/lq/status.html").module
    cases = [{"kind": "save_status", "props": {"state": state, **({"action": {"label": "重新核对" if state == "conflict" else "重试"}} if state in ("error", "conflict") else {})}}
             for state in status.SAVE_LABELS]
    cases += [{"kind": "save_status", "props": {"state": "constructor", "label": "已保存"}},
              {"kind": "save_status", "props": {"state": "synced", "time": "20:45", "datetime": "2026-09-20T20:45:00+08:00", "attrs": {"aria-live": "assertive"}}},
              {"kind": "status", "props": {"family": "score", "state": "excellent", "label": "优秀"}},
              {"kind": "status", "props": {"family": "__proto__", "state": "constructor", "label": "待核对"}},
              {"kind": "status", "props": {"family": "save", "state": "local_saved", "label": "本地草稿"}}]
    cases += [{"kind": "alert", "props": {"tone": tone, "title": "保存说明", "body": Markup('<img src=x onerror=alert(1)>正文'), "announce": announce,
               "action": {"label": "查看说明", "href": "/help", "attrs": {"target": "_blank"}}}}
              for tone in ("info", "warning", "danger") for announce in ("off", "polite", "assertive")]
    cases += [{"kind": "conflict", "props": {"action": {"label": "重新核对", "id": "recheck"}, "local": "本地草稿", "server": "最新服务器内容"}}]
    invalid = [
        {"kind": "status", "props": {"family": "save", "state": "synced", "label": ""}},
        {"kind": "status", "props": {"family": [], "state": "synced", "label": "已保存"}},
        *({"kind": "save_status", "props": props} for props in ({"state": None}, {"state": "error"}, {"state": "conflict"},
          {"state": "synced", "label": None}, {"state": "dirty", "time": 13}, {"state": "synced", "datetime": "2026-09-20"},
          {"state": "error", "action": {"label": ""}}, {"state": "error", "action": {"label": "重试", "href": "javascript:alert(1)"}},
          {"state": "error", "action": {"label": "重试", "onClick": "fetch('/')"}}, {"state": "error", "action": {"label": "重试", "disabled": True}},
          {"state": "error", "action": {"label": "重试", "variant": None}}, {"state": "dirty", "attrs": {"onclick": "alert(1)"}}, {"state": "dirty", "__proto__": {}},
          {"state": "dirty", "html": "<b>raw</b>"})),
        *({"kind": "alert", "props": props} for props in ({}, {"body": "text", "tone": "success"}, {"body": "text", "announce": True},
          {"body": None}, {"body": "text", "tone": None}, {"body": "text", "announce": None})),
        {"kind": "conflict", "props": {}}, {"kind": "conflict", "props": {"action": {"label": "重新核对"}, "local": {"html": "raw"}}},
    ]
    for case in cases + invalid:
        try:
            case["normalized"] = status.lq_status_props(case["kind"], **case["props"])
            case["html"] = str(getattr(macros, "lq_" + case["kind"])(**case["props"]))
        except (ValueError, TypeError) as error:
            case["error"] = type(error).__name__
    composition = env.from_string("""{% from 'macros/lq/status.html' import lq_conflict %}{% call(key) lq_conflict({'label':'重新核对'}) %}{% if key == 'local' %}<label for="local-draft">本地评分草稿</label><textarea id="local-draft">保留的评语</textarea>{% elif key == 'server' %}<p>服务器原始分：70</p>{% else %}内容已变化，本地输入保留。{% endif %}{% endcall %}""").render()
    assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
    return {"cases": cases, "invalid": invalid, "composition": composition, "isolated": True}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fixture(), ensure_ascii=False))
