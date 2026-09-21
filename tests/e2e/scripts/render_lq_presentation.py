"""Render real LQ macros for browser parity without importing app/core or DB."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "tests/e2e/components/fixtures/lq-presentation.json"
MACROS = {"button": ("button", "lq_btn"), "chip": ("chip", "lq_chip"),
          "skeleton": ("skeleton", "lq_skeleton"),
          **{name: ("indicators", f"lq_{name}") for name in ("badge", "avatar", "spinner", "progress")}}


def render_fixture():
    spec = importlib.util.spec_from_file_location("lq_presentation_pure", ROOT / "classroom_app/lq_components.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    env.globals["lq_props"] = helper.lq_props
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    cases = []
    for size in source["buttonSizes"]:
        for variant in source["buttonVariants"]:
            for loading in (False, True):
                cases.append({"id": f"button-{variant}-{size}" + ("-busy" if loading else ""),
                              "kind": "button", "props": {"label": "保存", "variant": variant,
                                                           "size": size, "loading": loading, "icon": "save"}})
    for tone in helper.TONES:
        cases.append({"id": f"status-{tone}", "kind": "chip", "props": {"label": f"状态 {tone}", "tone": tone}})
        cases.append({"id": f"badge-{tone}", "kind": "badge", "props": {"value": 12, "tone": tone}})
    cases.extend(source["cases"])
    for case in [*cases, *source["invalid"]]:
        props = {({"ariaDisabled": "aria_disabled", "removeLabel": "remove_label"}.get(k, k)): v
                 for k, v in case["props"].items()}
        try:
            case["normalized"] = helper.lq_props(case["kind"], **props)
            file, name = MACROS[case["kind"]]
            case["html"] = str(getattr(env.get_template(f"macros/lq/{file}.html").module, name)(**props))
        except (ValueError, TypeError) as error:
            case["error"] = type(error).__name__
    assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
    return {"cases": cases, "invalid": source["invalid"], "isolated": True}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(render_fixture(), ensure_ascii=False))
