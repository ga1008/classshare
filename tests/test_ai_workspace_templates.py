"""Assistant document mounting and dependency order, with the server access policy."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from starlette.requests import Request

from classroom_app.services.ai_workspace_policy import ai_workspace_policy
from classroom_app.services.manage_nav_service import build_manage_nav


ROOT = Path(__file__).resolve().parents[1]


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class AiWorkspaceTemplateTests(unittest.TestCase):
    def setUp(self):
        fixtures = {
            'partials/vite_islands.html': '',
            'partials/lq_page_backdrop.html': '',
            'fixture-navbar.html': '{% extends "base_navbar.html" %}{% block main_content %}<main id="page-content">正文</main>{% endblock %}',
            'fixture-markdown.html': '''{% extends "base.html" %}
{% block head %}{% with defer_markdown_assets=true %}{% include 'partials/markdown_assets.html' %}{% endwith %}{% endblock %}
{% block scripts %}{% with include_mermaid=true %}{% include 'partials/markdown_assets.html' %}{% endwith %}{% endblock %}''',
        }
        self.env = Environment(loader=ChoiceLoader([DictLoader(fixtures), FileSystemLoader(ROOT / 'templates')]), autoescape=True)
        self.env.globals.update(
            ai_workspace_policy=ai_workspace_policy,
            asset_url=lambda name: '/static/' + name,
            static_asset_revision=lambda: 'fixture',
            vite_entry_tags=lambda entry: '',
        )

    def render(self, template='base.html', role='student', path='/dashboard', query='', **context):
        user = {'id': 17, 'role': role, 'name': '测试用户'} if role else None
        request = Request({'type': 'http', 'scheme': 'http', 'path': path,
                           'query_string': query.encode(), 'headers': [], 'server': ('fixture.test', 80)})
        return self.env.get_template(template).render(user_info=user, request=request, **context)

    def assert_single_workspace(self, html, role='student', path='/dashboard'):
        tags = Tags(html).tags
        self.assertEqual(sum(attrs.get('id') == 'ai-chat-fab' for _, attrs in tags), 1)
        self.assertEqual(sum(attrs.get('id') == 'ai-chat-modal' for _, attrs in tags), 1)
        self.assertEqual(sum(attrs.get('href') == '/static/css/ai_workspace.css' for _, attrs in tags), 1)
        scripts = [attrs for tag, attrs in tags if tag == 'script' and 'src' in attrs]
        dependencies = ['es2022_polyfills', 'marked', 'markdown_runtime', 'js/ai_chat_component.js', 'js/ai_workspace_widget.js']
        srcs = [attrs['src'] for attrs in scripts]
        positions = []
        for name in dependencies:
            self.assertEqual(srcs.count('/static/' + name), 1, name)
            positions.append(srcs.index('/static/' + name))
        self.assertEqual(positions, sorted(positions))
        component = scripts[positions[-2]]
        self.assertNotIn('type', component)
        self.assertIn('defer', component)
        self.assertIn(f'userKey: "{role}:17"', html)
        self.assertIn(f'pagePath: "{path}"', html)
        container = next(attrs for _, attrs in tags if 'ai-workspace-container' in attrs.get('class', '').split())
        self.assertEqual(container.get('aria-modal'), 'false')
        capture = next(attrs for _, attrs in tags if attrs.get('id') == 'ai-chat-btn-capture')
        self.assertEqual(capture.get('aria-label'), '截取并标注当前页面')
        self.assertEqual(sum('resizer' in attrs.get('class', '').split() for _, attrs in tags), 8)

    def test_student_and_teacher_share_one_authenticated_entry_on_navbar_and_base(self):
        for role in ('student', 'teacher'):
            for template in ('base.html', 'fixture-navbar.html'):
                with self.subTest(role=role, template=template):
                    self.assert_single_workspace(self.render(template, role=role), role)

    def test_students_never_mount_workspace_on_assessment_paths_even_when_legacy_ai_flag_is_on(self):
        for path in ('/assignment/12', '/assignments', '/exam/take/12', '/exams', '/exam-papers/3', '/mp/assignment/2', '/submission/4', '/submissions/4'):
            with self.subTest(path=path):
                html = self.render(path=path, exam_ai_allowed=True)
                self.assertNotIn('id="ai-chat-fab"', html)
                self.assertNotIn('ai_workspace.css', html)
                self.assertNotIn('ai_chat_component.js', html)
        self.assert_single_workspace(self.render(role='teacher', path='/assignment/12'), 'teacher', '/assignment/12')

    def test_anonymous_and_embedded_documents_have_no_workspace_assets_or_entry(self):
        for context in ({'role': None}, {'embedded_mode': True}, {'query': 'embed=1'}, {'query': 'embed=TRUE'}):
            with self.subTest(context=context):
                html = self.render(**context)
                self.assertNotIn('id="ai-chat-fab"', html)
                self.assertNotIn('ai_workspace.css', html)
                self.assertNotIn('ai_chat_component.js', html)

    def test_manage_and_resume_independent_roots_reuse_workspace_and_respect_embed(self):
        user = {'id': 17, 'role': 'teacher', 'name': '测试用户'}
        manage = dict(page_title='课程', active_page='courses', embedded_mode=False,
                      manage_nav=build_manage_nav(user, 'courses', is_super_admin=False))
        self.assert_single_workspace(self.render('manage/layout.html', role='teacher', path='/manage/courses', **manage), 'teacher', '/manage/courses')
        manage['embedded_mode'] = True
        self.assertNotIn('id="ai-chat-fab"', self.render('manage/layout.html', role='teacher', path='/manage/courses', **manage))
        self.assert_single_workspace(self.render('resume/layout.html', path='/resume', page_title='简历', resume_nav={'groups': []}), path='/resume')

    def test_material_shell_preserves_context_and_dependency_order_for_both_roles(self):
        shell = dict(entry_name='lesson.html', material_name='课程', entry_material_id=9, material_path='lesson.html',
                     is_html_package=True, node_id=1, package_root_id=1, iframe_src='/fixture-lesson', lesson_number=5)
        for role in ('teacher', 'student'):
            html = self.render('material_render_shell.html', role=role, path='/materials/9/render', shell=shell,
                               learning_context={'class_offering_id': 42, 'session_id': 5}, reader_return=None)
            self.assert_single_workspace(html, role, '/materials/9/render')
            self.assertIn('classOfferingId: 42', html)
            self.assertIn('id="render-shell-frame"', html)

    def test_page_markdown_and_late_mermaid_are_not_reexecuted_by_global_workspace(self):
        html = self.render('fixture-markdown.html')
        self.assert_single_workspace(html)
        self.assertEqual(html.count('src="/static/mermaid"'), 1)
        scripts = [attrs for tag, attrs in Tags(html).tags if tag == 'script' and 'src' in attrs]
        for name in ('es2022_polyfills', 'marked', 'markdown_runtime'):
            self.assertIn('defer', next(attrs for attrs in scripts if attrs['src'] == '/static/' + name))

    def test_every_standalone_document_owns_one_mount_and_exam_business_remains(self):
        roots = []
        for path in (ROOT / 'templates').rglob('*.html'):
            source = path.read_text(encoding='utf-8')
            self.env.parse(source)
            if re.search(r'<html\b', source, re.I) and not re.search(r'{%\s*extends\b', source):
                roots.append(path)
                self.assertEqual(source.count('{% include "partials/ai_workspace_mount.html" %}'), 1, str(path))
                self.assertIn('set markdown_assets = namespace(', source, str(path))
        self.assertEqual(len(roots), 9)
        exam = (ROOT / 'templates/exam_take.html').read_text(encoding='utf-8')
        for legacy in ('EXAM_AI_ALLOWED', 'EXAM_AI_CONTEXT', 'initExamAiChat', 'exam_ai_allowed', 'new window.AIChatComponent'):
            self.assertNotIn(legacy, exam)
        for business in ('saveToLocal()', 'DOMContentLoaded', 'group_peer_eval.js', 'id="topbarSubmitBtn"', 'id="pageContent"'):
            self.assertIn(business, exam)


if __name__ == '__main__':
    unittest.main()
