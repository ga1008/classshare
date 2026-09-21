"""Actual centered documents, pure Jinja and presentation helper only."""
import importlib.util
import json
from pathlib import Path
import sys
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('lq_centered_pure_components', ROOT / 'classroom_app/lq_components.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
PAGES = ('student_login_v4', 'teacher_login_v4', 'teacher_register_v4', 'session_expired', 'permission_denied', 'status', 'error')

def render_page(name, enabled=True, *, legacy_root=None, **extra):
    roots = ([str(Path(legacy_root) / 'templates')] if legacy_root else []) + [str(ROOT / 'templates')]
    env = Environment(loader=FileSystemLoader(roots), autoescape=True)
    env.globals.update(lq_props=helper.lq_props,
        asset_url=lambda name: '/static/css/tailwind-app.css' if name == 'tailwind_app' else '/static/' + name,
        static_asset_revision=lambda: 'centered-pure-fixture',
        lq_family_enabled=lambda family: bool(enabled and family == 'centered'))
    data = dict(request=None, user_info=None, next_url='/materials/42?source=login', teacher_entry_url='/teacher/login?next=%2Fmaterials%2F42', student_entry_url='/student/login?next=%2Fmaterials%2F42',
        password_policy_hint='至少 6 位字符', login_error='', login_identifier='', login_email='', success=False,
        message='操作失败，请核对后重试。', back_url='/dashboard?source=status', error_code=404, error_title='页面不存在', error_message='请检查链接或返回首页。',
        current_user={'name': '合成学生'}, current_role_label='学生', required_role='teacher', required_role_label='教师',
        teacher_login_url='/teacher/login?next=%2Fmanage%2Fcourses', student_login_url='/student/login?next=%2Fmaterials%2F42',
        session_login_url='/student/login?next=%2Fmaterials%2F42', dashboard_url='/dashboard', show_teacher_login=True,
        permission_message='当前账号已登录，但没有访问该页面或资源的权限。',
        site_record={'icp_number': '测试备案号', 'owner_statement': '隔离组件测试', 'lookup_url': 'https://beian.miit.gov.cn', 'approved_at': '', 'notice_source': ''})
    data.update(extra)
    return env.get_template(name + '.html').render(**data)

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    pages = {name: render_page(name) for name in PAGES}
    pages['student_error'] = render_page('student_login_v4', login_error='登录失败：账号或密码错误。', login_identifier='<合成学生>')
    pages['teacher_error'] = render_page('teacher_login_v4', login_error='登录失败：邮箱或密码错误。', login_email='qa@example.test')
    pages['status_success'] = render_page('status', success=True, message='操作已完成。')
    print(json.dumps({'pages': pages, 'legacy': {name: render_page(name, False) for name in PAGES},
        'isolated': not any(n in ('app', 'core', 'classroom_app') or n.startswith('classroom_app.') for n in sys.modules)}, ensure_ascii=False))
