"""Render real content macros through an isolated namespace of pure helpers."""
import importlib.util
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]
ALIASES = {"titleId": "title_id", "explainLabel": "explain_label", "searchId": "search_id", "searchLabel": "search_label", "searchName": "search_name", "searchValue": "search_value", "searchPlaceholder": "search_placeholder", "searchAttrs": "search_attrs"}


def environment():
    package = types.ModuleType("lq_content_fixture")
    package.__path__ = [str(ROOT / "classroom_app")]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location(package.__name__ + ".lq_content", ROOT / "classroom_app/lq_content.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
    env.globals["lq_props"] = lambda kind, **p: helper.lq_content_props(kind, **p) if kind in helper.CONTENT_KINDS else helper.presentation_props(kind, **p)
    return helper, env


def fixture():
    helper, env = environment()
    macros = env.get_template("macros/lq/content.html").module
    payload = json.loads((ROOT / "tests/e2e/components/fixtures/lq-content.json").read_text(encoding="utf-8"))
    for case in [*payload["cases"], *payload["invalid"]]:
        props = {ALIASES.get(key, key): value for key, value in case["props"].items()}
        try:
            case["normalized"] = helper.lq_content_props(case["kind"], **props)
            case["html"] = str(getattr(macros, "lq_" + case["kind"])(**props))
        except (ValueError, TypeError) as error:
            case["error"] = type(error).__name__
    payload["composition"] = env.from_string("""{% from 'macros/lq/content.html' import lq_card,lq_list,lq_row,lq_page_head,lq_filter_bar,lq_prose %}
{% call(slot) lq_card('保留草稿',id='composed-card') %}{% if slot == 'body' %}<label for="draft">草稿</label><textarea id="draft">未保存的内容</textarea>{% elif slot == 'foot' %}<button type="button" id="draft-save">保存草稿</button>{% endif %}{% endcall %}
{% call(slot) lq_list('真实宏列表',id='composed-list') %}{% if slot == 'items' %}{{ lq_row('现有附件',primary={'id':'composed-row-open'},actions=[{'label':'删除','id':'composed-row-delete'}]) }}{% endif %}{% endcall %}
{% call lq_page_head('旧签名的新实现',description='aside 保留',actions=[{'label':'创建','variant':'primary'}],eyebrow='不填充',title_id='compat-title',id='compat-head') %}<span id="compat-aside">调用者摘要</span>{% endcall %}
{% call lq_filter_bar('compat-search',search_attrs={'data-search':''},id='compat-filter',label='兼容筛选') %}<label for="compat-select">范围</label><select id="compat-select"><option>全部</option></select>{% endcall %}
{% call(slot) lq_prose(id='composed-prose') %}{% if slot == 'content' %}<p>已由作者安全输出的 <strong>Markdown DOM</strong>。</p><pre><code>const longLine = '{{ 'x' * 180 }}';</code></pre><table><caption>示例</caption><thead><tr><th scope="col">课程</th><th scope="col">说明</th></tr></thead><tbody><tr><td>课程一</td><td>只提供排版</td></tr></tbody></table>{% endif %}{% endcall %}""").render()
    assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
    payload["isolated"] = True
    return payload


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fixture(), ensure_ascii=False))
