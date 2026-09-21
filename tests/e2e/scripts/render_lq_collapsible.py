"""Pure real-macro fixture; no app/core or DB imports."""
import importlib.util
import json
from pathlib import Path
import sys
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]


def fixture():
    spec = importlib.util.spec_from_file_location('pure_collapsible', ROOT / 'classroom_app/lq_collapsible.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=True, undefined=StrictUndefined)
    env.globals['lq_props'] = helper.lq_collapsible_props
    macro = env.get_template('macros/lq/collapsible.html').module.lq_collapsible
    source = json.loads((ROOT / 'tests/e2e/components/fixtures/lq-collapsible.json').read_text(encoding='utf-8'))
    result = {'cases': [], 'invalid': [], 'isolated': True}
    for group in ('cases', 'invalid'):
        for props in source[group]:
            py = { {'keepOpen': 'keep_open', 'hasError': 'has_error'}.get(k,k):v for k,v in props.items() }
            item = {'props': props}
            try:
                item['normalized'] = helper.lq_collapsible_props('collapsible', **py)
                item['html'] = str(macro(**py))
            except (ValueError, TypeError) as error:
                item['error'] = type(error).__name__
            result[group].append(item)
    result['composed'] = env.from_string("{% from 'macros/lq/collapsible.html' import lq_collapsible %}{% call lq_collapsible('composed','草稿',mode='always') %}<label for='draft'>草稿</label><textarea id='draft'>未保存</textarea>{% endcall %}").render()
    assert not any(key == 'classroom_app' or key.startswith('classroom_app.') for key in sys.modules)
    return result


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(fixture(), ensure_ascii=False))
