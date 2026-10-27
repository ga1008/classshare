"""Render the real materials template against a minimal shell; no app or DB."""
import importlib.util
import json
from pathlib import Path
import sys
import types

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[3]
package = types.ModuleType('materials_lq_fixture')
package.__path__ = [str(ROOT / 'classroom_app')]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location('materials_lq_fixture.lq', ROOT / 'classroom_app/lq.py')
helper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helper
spec.loader.exec_module(helper)
manifest = json.loads((ROOT / 'static/assets/manifest.json').read_text(encoding='utf-8'))
shell = '''<header class="lq-surface fixture-header" data-lq-component="surface">{% block header_actions %}{% endblock %}</header>{% block extra_head %}{% endblock %}<main class="fixture-main">{% block content %}{% endblock %}</main>{% block scripts %}{% endblock %}'''
env = Environment(loader=ChoiceLoader([DictLoader({'manage/layout.html': shell}), FileSystemLoader(ROOT / 'templates')]), autoescape=True)
env.globals.update(lq_props=helper.lq_props, lq_family_enabled=lambda name: True,
                   asset_url=lambda name: '/static/' + manifest['entries'][name], vite_entry_tags=lambda name: '')
html = env.get_template('manage/materials.html').render(
    user_info={'id': 1, 'name': '合成教师', 'email': 'fixture@example.invalid'}, offerings=[],
    material_stats={key: 0 for key in ['total_count', 'file_count', 'folder_count', 'assigned_material_count', 'root_count', 'classroom_count', 'total_size']},
    initial_ai_generate={}, initial_ai_import={}, initial_library_filter={'library_view': 'learning'}, material_ai_import_registry=[],
)
sys.stdout.reconfigure(encoding='utf-8')
print(html)
