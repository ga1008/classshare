"""Actual course schedule / submission templates; no app, DB or login."""
import importlib.util
import ast
import json
from pathlib import Path
import re
import sys
import types
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[3]
package = types.ModuleType('narrow_lq_fixture')
package.__path__ = [str(ROOT / 'classroom_app')]
sys.modules[package.__name__] = package
services = types.ModuleType('narrow_lq_fixture.services')
services.__path__ = [str(ROOT / 'classroom_app/services')]
sys.modules[services.__name__] = services


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


helper = load('narrow_lq_fixture.lq', ROOT / 'classroom_app/lq.py')
nav = load('narrow_lq_fixture.services.manage_nav_service', ROOT / 'classroom_app/services/manage_nav_service.py')
partials = {f'partials/{name}.html': '' for name in ('ai_workspace_mount', 'ai_workspace_widget', 'vite_islands', 'feedback_modal', 'teacher_onboarding_modal', 'markdown_assets')}
env = Environment(loader=ChoiceLoader([DictLoader(partials), FileSystemLoader(ROOT / 'templates')]), autoescape=True)
env.globals.update(lq_props=helper.lq_props, lq_family_enabled=lambda name: True,
    asset_url=lambda name: '/static/' + ('css/tailwind-app.css' if name == 'tailwind_app' else name),
    static_asset_revision=lambda: 'narrow-page-fixture', vite_entry_tags=lambda name: '')
user = {'id': 73, 'role': 'teacher', 'name': '合成教师', 'email': 'fixture@example.invalid'}
preferences = {'enabled': False, 'palette_key': 'indigo', 'appearance': 'auto', 'glass': 'tinted'}
env.globals['resolve_user_ui_preferences'] = lambda request, user: preferences
common = dict(request=None, user_info=user, ui_palette=preferences, embedded_mode=False, navbar_shell_enabled=False)
term = {'year': '2026-2027', 'term': '1', 'label': '2026-2027学年第一学期', 'status': 'current'}
overview = {'terms': [term], 'selected_term': term, 'weeks': [], 'courses': [], 'schedule_source': 'academic',
    'filters': {'course_options': ['计算机网络原理与应用'], 'class_options': ['2026级计算机应用技术一班']},
    'summary': {'cur_week': 4, 'max_week': 20, 'week1_monday': '2026-09-14', 'anchor_label': '平台学期设置（2026-2027第1学期）', 'term_status': 'current', 'course_count': 1, 'class_count': 1}}
schedule = env.get_template('manage/course_schedule.html').render(**common, page_title='课时统计',
    active_page='course_schedule', manage_nav=nav.build_manage_nav(user, 'course_schedule'),
    course_schedule_overview=overview, has_smart_credential=False)
# Read the real static label without importing its service or its dependencies.
attachments = ast.parse((ROOT / 'classroom_app/services/ai_grading_attachments.py').read_text(encoding='utf-8'))
types_label = next(ast.literal_eval(node.value) for node in attachments.body if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == 'AI_GRADING_SUPPORTED_TYPES_LABEL' for target in node.targets))
submission = env.get_template('submission_detail.html').render(**common,
    submission={'id': 1, 'student_name': '合成学生', 'student_id_number': '20260001', 'status': 'submitted',
                'score': None, 'answers_json': {'answers': [{'question': 'P03 fixture answer', 'answer': 'This answer belongs only to the copied P03 runtime database.'}]}, 'feedback_md': ''},
    assignment={'id': 1, 'title': '网络原理随堂作业', 'requirements_md': '', 'rubric_md': ''},
    submission_files=[], exam_questions=[], can_manage_submission_files=True, expected_review_revision='review-v1',
    expected_assignment_revision='assignment-v1', ai_grading_supported_types_label=types_label, ai_grading_upload_extensions=['.pdf'], max_total_mb=20,
    max_per_file_mb=10, max_submission_file_count=5)
# Keep real markup and CSS links. Scope JavaScript to the controller under test:
# header account widgets and grading network flows belong to their own suites.
def scripts(source, keep_schedule=False):
    def retain(match):
        text = match.group(0)
        return text if 'application/json' in text or keep_schedule and 'js/manage_course_schedule.js' in text or not keep_schedule and 'setupSubmissionGrading' in text else ''
    return re.sub(r'<script\b[^>]*>.*?</script>', retain, source, flags=re.S | re.I)

sys.stdout.reconfigure(encoding='utf-8')
print(json.dumps({'schedule': scripts(schedule, True), 'submission': scripts(submission), 'overview': overview,
    'isolated': not any(name == 'classroom_app' or name.startswith('classroom_app.') or name in ('app', 'core') for name in sys.modules)}, ensure_ascii=False))
