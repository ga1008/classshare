"""Actual Jinja form fixtures, loading only pure props modules by file path."""
import importlib.util
import json
from pathlib import Path
import sys
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]
ALIASES = {"readOnly": "readonly", "autoGrow": "auto_grow", "inputMode": "inputmode",
           "minLength": "minlength", "maxLength": "maxlength", "controlProps": "control_props"}


def python_props(props):
    return {ALIASES.get(k, k): python_props(v) if k == "controlProps" else v for k, v in props.items()}


def fixture():
    spec = importlib.util.spec_from_file_location("lq_forms_pure", ROOT / "classroom_app/lq_forms.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    env.globals["lq_props"] = module.lq_form_props
    macros = env.get_template("macros/lq/forms.html").module
    payload = json.loads((ROOT / "tests/e2e/components/fixtures/lq-forms.json").read_text(encoding="utf-8"))
    for case in [*payload["cases"], *payload["invalid"]]:
        props = python_props(case["props"])
        try:
            case["normalized"] = module.lq_form_props(case["kind"], **props)
            case["html"] = str(getattr(macros, "lq_" + case["kind"])(**props))
        except (ValueError, TypeError) as error:
            case["error"] = type(error).__name__
    payload["composition"] = env.from_string("""{% from 'macros/lq/forms.html' import lq_form_section,lq_form_actions,lq_input %}{% call lq_form_section('composed','组合分组') %}{{ lq_input('composed-input','组合字段',value='保留') }}{% endcall %}{% call lq_form_actions(hint='常显说明') %}<button type="submit">保存</button>{% endcall %}""").render()
    assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
    payload["isolated"] = True
    return payload


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fixture(), ensure_ascii=False))
