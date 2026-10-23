"""Render the actual Profile message partial without importing the application."""
import json
import sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
from classroom_app.lq import lq_props  # pure presentation props; no application state

env = Environment(loader=FileSystemLoader(root / 'templates'), autoescape=select_autoescape())
env.globals['lq_props'] = lq_props
template = env.get_template('partials/profile/messages.html')
print(json.dumps({mode: template.render(active_section=mode, initial_tab='private_message' if mode == 'private' else 'all',
                                       initial_contact='', initial_scope=None)
                  for mode in ('private', 'notifications')}, ensure_ascii=True))
