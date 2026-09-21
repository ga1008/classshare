"""Render the actual Profile message partial without importing the application."""
import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

root = Path(__file__).resolve().parents[3]
env = Environment(loader=FileSystemLoader(root / 'templates'), autoescape=select_autoescape())
template = env.get_template('partials/profile/messages.html')
print(json.dumps({mode: template.render(active_section=mode, initial_tab='private_message' if mode == 'private' else 'all',
                                       initial_contact='', initial_scope=None)
                  for mode in ('private', 'notifications')}, ensure_ascii=True))
