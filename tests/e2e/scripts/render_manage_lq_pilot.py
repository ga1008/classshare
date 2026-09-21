"""Pure SSR pilot shell fixture; never import the application, core or database."""
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import types
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[3]
package = types.ModuleType('manage_lq_fixture')
package.__path__ = [str(ROOT / 'classroom_app')]
sys.modules[package.__name__] = package

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

helper = load('manage_lq_fixture.lq', ROOT / 'classroom_app/lq.py')
nav = load('manage_lq_fixture.nav', ROOT / 'classroom_app/services/manage_nav_service.py')
partials = {f'partials/{name}.html': '' for name in ('lq_editor_head', 'ai_workspace_widget', 'vite_islands', 'feedback_modal', 'teacher_onboarding_modal', 'markdown_assets')}
partials['fixture.html'] = '''{% extends 'manage/layout.html' %}
{% block header_actions %}<form id="action-form" aria-label="原位操作表单"><label for="retained-input">原位草稿</label><input id="retained-input" name="draft" value="原值"><fieldset disabled><legend>服务端禁用</legend><input name="locked" value="locked" aria-label="锁定字段"><button id="locked-action" type="submit">禁止保存</button></fieldset><button type="button" id="business-open">打开原业务弹窗</button><button type="submit" id="business-submit" name="intent" value="save">提交原表单</button></form>
<div class="materials-upload-menu"><button type="button" id="materials-create-menu-btn" aria-haspopup="true" aria-expanded="false">新建</button><div id="materials-create-dropdown" class="materials-upload-dropdown" hidden><button type="button" id="materials-create-file-btn">新建文档</button></div></div><iframe id="retained-frame" title="原位预览" src="/frame"></iframe>{% endblock %}
{% block content %}<h2>原有页面内容</h2><label for="outside-draft">页面草稿</label><input id="outside-draft" value="不重建"><dialog id="business-modal" aria-label="原业务弹窗"><label for="business-focus">原业务字段</label><input id="business-focus"><button type="button" id="business-close">关闭业务弹窗</button></dialog>{% endblock %}'''
env = Environment(loader=ChoiceLoader([DictLoader(partials), FileSystemLoader(ROOT / 'templates')]), autoescape=True)
env.globals.update(lq_props=helper.lq_props, asset_url=lambda value: '/static/' + value)
family = os.environ.get('LQ_MANAGE_FAMILY') == '1'
env.globals['lq_family_enabled'] = lambda name: family and name == 'manage-shell'
user = {'id': 73, 'role': 'teacher', 'name': '测试教师', 'email': 'fixture@example.invalid'}
preferences = {'enabled': True, 'palette_key': 'indigo', 'appearance': 'light', 'glass': 'off', 'presets': [{'key': 'indigo', 'name': '靛蓝'}]}
env.globals['resolve_user_ui_preferences'] = lambda request, user: preferences
html = env.get_template('fixture.html').render(lq_pilot_enabled=not family, embedded_mode=False, active_page='courses', page_title='课程', user_info=user, ui_palette=preferences, manage_nav=nav.build_manage_nav(user, 'courses'))
html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S | re.I)
headers = {}
for name in ('courses', 'classes', 'offering_hub', 'semesters', 'textbooks', 'lesson_plans', 'materials', 'system/users'):
    source = (ROOT / 'templates/manage' / (name + '.html')).read_text(encoding='utf-8')
    header = re.search(r'{% block header_actions %}(.*?){% endblock %}', source, re.S)
    # Real list pages are taller than a viewport. A tiny placeholder allowed the
    # legacy min-height grid to distribute free row space, keeping the sticky
    # topbar hundreds of pixels below its actual overlap with the fixed trigger.
    page_source = "{% extends 'manage/layout.html' %}{% block header_actions %}" + (header.group(1) if header else '') + "{% endblock %}{% block content %}<h2>真实操作区纯呈现</h2>{% for _ in range(40) %}<p>列表占位内容：保留真实列表页的纵向滚动条件。</p>{% endfor %}{% endblock %}"
    rendered = env.from_string(page_source).render(lq_pilot_enabled=not family, embedded_mode=False, active_page='courses', page_title='课程管理', initial_ai_generate=None, user_info=user, ui_palette=preferences, manage_nav=nav.build_manage_nav(user, 'courses'))
    headers[name] = re.sub(r'<script\b[^>]*>.*?</script>', '', rendered, flags=re.S | re.I)
sys.stdout.reconfigure(encoding='utf-8')
print(json.dumps({'html': html, 'headers': headers, 'isolated': not any(name == 'classroom_app' or name.startswith('classroom_app.') or name in ('app', 'core') for name in sys.modules)}, ensure_ascii=False))
