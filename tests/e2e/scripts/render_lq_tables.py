"""Real table macros rendered from pure helpers without importing the application."""
import importlib.util
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]
ALIASES = {"selectionName": "selection_name", "selectedCount": "selected_count", "totalPages": "total_pages"}


def environment():
    package = types.ModuleType("lq_tables_fixture")
    package.__path__ = [str(ROOT / "classroom_app")]
    sys.modules[package.__name__] = package
    def load(name):
        spec = importlib.util.spec_from_file_location(package.__name__ + "." + name, ROOT / ("classroom_app/" + name + ".py"))
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        return helper
    tables, content = load("lq_tables"), load("lq_content")
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    env.globals["lq_props"] = lambda kind, **p: tables.lq_table_props(kind, **p) if kind in tables.TABLE_KINDS else content.lq_content_props(kind, **p) if kind in content.CONTENT_KINDS else tables.presentation_props(kind, **p)
    return tables, env


def fixture():
    tables, env = environment()
    macros = env.get_template("macros/lq/tables.html").module
    payload = json.loads((ROOT / "tests/e2e/components/fixtures/lq-tables.json").read_text(encoding="utf-8"))
    for case in [*payload["cases"], *payload["invalid"]]:
        props = {ALIASES.get(key, key): value for key, value in case["props"].items()}
        try:
            case["normalized"] = tables.lq_table_props(case["kind"], **props)
            case["html"] = str(getattr(macros, "lq_" + case["kind"])(**props))
        except (ValueError, TypeError) as error:
            case["error"] = type(error).__name__
    payload["composition"] = env.from_string("""{% from 'macros/lq/tables.html' import lq_table %}{% from 'macros/lq/content.html' import lq_empty %}
{% call(slot) lq_table('composed','真实输入槽',[{'key':'student','label':'学生','rowHeader':true},{'key':'score','label':'评分'}],[{'key':'a','cells':{'student':'学生甲','score':''}}],mode='matrix') %}{% if slot == 'cell:a:score' %}<label for="table-draft">评分草稿</label><input id="table-draft" value="88.5" type="text">{% endif %}{% endcall %}
{% for reason in ['empty','error','offline'] %}{% call(slot) lq_table('state-' ~ reason,reason ~ '状态表',[{'key':'name','label':'名称'}],[]) %}{% if slot == 'empty' %}{{ lq_empty(reason=reason,description='状态由业务控制器明确提供',variant='inline') }}{% endif %}{% endcall %}{% endfor %}""").render()
    assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
    payload["isolated"] = True
    return payload


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(fixture(), ensure_ascii=False))
