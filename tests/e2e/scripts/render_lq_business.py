"""Render real business macros without importing the application or storage."""
from importlib import import_module
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

ROOT = Path(__file__).resolve().parents[3]


def fixture():
    package = types.ModuleType("lq_business_fixture")
    package.__path__ = [str(ROOT / "classroom_app")]
    sys.modules[package.__name__] = package
    api = import_module("lq_business_fixture.lq_business")
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    env.globals["lq_props"] = import_module("lq_business_fixture.lq").lq_props
    macros = env.get_template("macros/lq/business.html").module
    clock = {"assignment_id": 7, "server_now": "2026-09-20 12:00:00", "countdown_at": "2026-09-20 13:00:00",
             "deadline_phase": "regular", "accepting": True}
    job = {"identity": "task:9", "generation": 1, "state": "running", "label": "运行中"}
    groups = [{"id": "p1", "label": "第一部分", "items": [{"id": "q1", "index": 1, "current": True, "answered": True,
               "flagged": True, "error": True, "pendingUpload": True}, {"id": "q2", "index": 2, "disabled": True},
               {"id": "q3", "index": 3, "label": "<题目>"}]}]
    cases = [{"kind": "deadline_clock", "props": p} for p in ({}, clock, {**clock, "compact": True},
             {**clock, "personal_resubmission": True, "can_resubmit": True, "resubmission_due_at": "2026-09-21 12:00:00"},
             {**clock, "deadline_phase": "constructor", "absolute": Markup('<img src=x onerror=alert(1)>')})]
    cases += [{"kind": "job_status", "props": {**job, "state": state, "message": "尚未自动应用结果。", "elapsed": "12 秒",
               "progress": {"label": "处理进度", **({"value": 42} if state == "running" else {})},
               "actions": [{"key": "view", "label": "查看结果", "href": "/result/9"}, {"key": "apply", "label": "应用", "disabled": True}]}}
              for state in ("queued", "running", "retry_wait", "result_ready", "failed", "canceled", "superseded", "__proto__")]
    cases += [{"kind": "job_status", "props": {**job, "family": "agent", "state": state, "message": Markup('<script>alert(1)</script>')}}
              for state in ("waiting_input", "partial", "completed", "unverified")]
    cases += [{"kind": "question_navigator", "props": {"groups": groups}},
              {"kind": "question_navigator", "props": {"groups": []}}]
    invalid = [
        *({"kind": "deadline_clock", "props": p} for p in ({"compact": "true"}, {"assignment_id": 0}, {"assignment_id": True},
          {"server_now": None}, {"accepting": 1}, {"attrs": {"onclick": "x"}}, {"html": "<b>x</b>"}, {"__proto__": {}})),
        *({"kind": "job_status", "props": {**job, **p}} for p in ({"generation": -1}, {"generation": True}, {"generation": 1.5},
          {"identity": ""}, {"family": "save"}, {"label": ""}, {"state": None}, {"elapsed": 10}, {"progress": {"value": 2}},
          {"progress": {"label": "x", "value": "2"}}, {"actions": [{"key": "x", "label": "x", "href": "javascript:alert(1)"}]},
          {"actions": [{"key": "x", "label": "x", "onClick": "x"}]}, {"actions": [{"key": "x", "label": "x"}] * 2})),
        {"kind": "question_navigator", "props": {"groups": None}},
        {"kind": "question_navigator", "props": {"groups": groups * 2}},
        *({"kind": "question_navigator", "props": {"groups": [{"id": "a", "label": "A", "items": items}]}}
          for items in ([{"id": "a", "index": 0}], [{"id": "a", "index": 1, "answered": 1}],
                        [{"id": "a", "index": 1}, {"id": "a", "index": 2}], [{"id": "a", "index": 1}, {"id": "b", "index": 1}],
                        [{"id": "a", "index": 1, "current": True}, {"id": "b", "index": 2, "current": True}])),
    ]
    for case in cases + invalid:
        try:
            case["normalized"] = api.lq_business_props(case["kind"], **case["props"])
            case["html"] = str(getattr(macros, "lq_" + case["kind"])(**case["props"]))
        except (ValueError, TypeError) as error:
            case["error"] = type(error).__name__
    assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
    return {"cases": cases, "invalid": invalid, "isolated": True}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fixture(), ensure_ascii=False))
