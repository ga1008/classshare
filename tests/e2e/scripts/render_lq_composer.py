"""Pure Jinja/typed fixture: no application import, database or HTTP."""
from importlib import import_module
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

ROOT = Path(__file__).resolve().parents[3]
package = types.ModuleType("lq_composer_fixture")
package.__path__ = [str(ROOT / "classroom_app")]
sys.modules[package.__name__] = package
api = import_module("lq_composer_fixture.lq_composer")
base = import_module("lq_composer_fixture.lq").lq_props
env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True, undefined=StrictUndefined)
# Use the actual dispatcher as soon as the independent integration owner registers this package.
try:
    base("composer")
    dispatcher = base
    integrated = True
except ValueError:
    dispatcher = lambda kind, **props: api.lq_composer_props(kind, **props) if kind == "composer" else base(kind, **props)
    integrated = False
env.globals["lq_props"] = dispatcher
macro = env.get_template("macros/lq/composer.html").module.lq_composer
cases = [{"props": p} for p in ({}, {"value": "  草稿\n第二行  "}, {"value": "\n\n空白"}, {"busy": True, "value": "忙碌保留"}, {"disabled": True},
    {"enter": "send", "required": True, "maxlength": 100}, {"hasContent": True}, {"attachment": None, "emoji": None},
    {"form": "external", "name": "message", "submit_name": "action", "submit_value": "send"},
    {"value": Markup('</textarea><img src=x onerror="alert(1)">'), "label": Markup('<b>内容</b>'), "submit_name": 'x" onfocus="x', "submit_value": '<img src=x>'},
    {"id": "test", "attrs": {"data-resource": "r", "aria-describedby": "hint"}, "attachment": "图片或附件", "emoji": "插入表情", "sendLabel": "发送消息"})]
invalid = [{"props": p} for p in ({"value": None}, {"busy": 1}, {"disabled": "false"}, {"hasContent": []}, {"enter": "auto"}, {"name": ""},
    {"form": ""}, {"required": 1}, {"maxlength": True}, {"maxlength": 0}, {"submit_value": "x"}, {"label": ""}, {"attachment": ""},
    {"emoji": 3}, {"sendLabel": None}, {"attrs": {"style": "color:red"}}, {"__proto__": {}}, {"html": "<b>raw</b>"})]
for case in cases + invalid:
    try:
        case["normalized"] = dispatcher("composer", **case["props"])
        case["html"] = str(macro(**case["props"]))
    except (ValueError, TypeError) as error:
        case["error"] = type(error).__name__
composition = env.from_string("""{% from 'macros/lq/composer.html' import lq_composer %}<form id="native">{% call lq_composer(value='草稿',required=true,submit_name='operation',submit_value='send') %}<p id="warning">发送失败，草稿已保留</p><label for="context">业务上下文</label><input id="context" name="context" value="原值">{% endcall %}</form>""").render()
assert not any(name == "classroom_app" or name.startswith("classroom_app.") for name in sys.modules)
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps({"cases": cases, "invalid": invalid, "composition": composition, "isolated": True, "integrated": integrated}, ensure_ascii=False))
