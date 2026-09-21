"""Isolated Jinja fixture; does not import the application, core or database."""
import importlib.util
import json
from pathlib import Path
import sys
import types
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]
package = types.ModuleType('lq_shell_fixture')
package.__path__ = [str(ROOT / 'classroom_app')]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location('lq_shell_fixture.lq_shells', ROOT / 'classroom_app/lq_shells.py')
helper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helper
spec.loader.exec_module(helper)
env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=True, undefined=StrictUndefined)
env.globals['lq_props'] = lambda component, **p: helper.lq_shell_props(component, **p) if component in helper.SHELL_KINDS else helper.presentation_props(component, **p)
aliases = {'lockNav': 'lock_nav', 'railLabel': 'rail_label', 'asideLabel': 'aside_label', 'stackIndex': 'stack_index', 'viewTransition': 'view_transition'}
fixture = json.loads((ROOT / 'tests/e2e/components/fixtures/lq-shells.json').read_text(encoding='utf-8'))
template = env.from_string("{% from 'macros/lq/shells.html' import lq_shell %}{{ lq_shell(component, **props) }}")
for section in ('cases', 'invalid'):
    for item in fixture[section]:
        props = {aliases.get(key, key): value for key, value in item['props'].items()}
        try:
            item['normalized'] = helper.lq_shell_props(item['kind'], **props)
            item['html'] = template.render(component=item['kind'], props=props)
        except ValueError:
            item['error'] = 'ValueError'
fixture['composition'] = env.from_string("""{% from 'macros/lq/shells.html' import lq_editor %}{% call(slot) lq_editor('live-editor','保留原位的编辑器',primary={'key':'save','label':'保存'}) %}{% if slot=='main' %}<label for="main-draft">正文</label><textarea id="main-draft" name="mainDraft">正文草稿</textarea>{% elif slot=='rail' %}<label for="rail-draft">标题</label><input id="rail-draft" name="title" value="未保存标题"><fieldset disabled><legend>服务端禁用</legend><input id="disabled-draft" name="locked" value="锁定值"></fieldset><input id="file-draft" type="file" name="file" aria-label="附件">{% elif slot=='aside' %}<iframe id="preview-frame" title="独立预览" src="/frame"></iframe>{% endif %}{% endcall %}""").render()
fixture['composition'] = fixture['composition'].replace('id="disabled-draft"', 'id="disabled-draft" aria-label="已锁定的值"')
fixture['topbarComposition'] = env.from_string("""{% from 'macros/lq/shells.html' import lq_topbar %}{% call(slot) lq_topbar('live-topbar','保留真实操作的顶栏',view_transition=true,actions=[{'key':'help','label':'帮助'}],primary={'key':'primary','label':'主要操作'}) %}{% if slot=='more' %}<label for="topbar-draft">未保存名称</label><input id="topbar-draft" name="draft" value="未保存"><button id="topbar-submit" type="submit" name="intent" value="save">提交原表单</button><fieldset disabled><legend>禁用范围</legend><input id="topbar-locked" name="locked" value="锁定" aria-label="锁定字段"><button type="submit">禁止提交</button></fieldset><iframe id="topbar-frame" title="原位小型预览" src="/frame"></iframe>{% endif %}{% endcall %}""").render()
fixture['isolated'] = not any(name == 'classroom_app' or name.startswith('classroom_app.') or name == 'app' or name == 'core' for name in sys.modules)
sys.stdout.reconfigure(encoding='utf-8')
print(json.dumps(fixture, ensure_ascii=False))
