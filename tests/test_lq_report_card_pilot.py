"""S3 B presentation contracts. No HTTP client or real database connection."""
import asyncio
from contextlib import contextmanager
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from fastapi import HTTPException
from classroom_app.lq import lq_props
from classroom_app.routers import report_card as route

ROOT = Path(__file__).resolve().parents[1]


def sample_card():
    records = []
    for index, (score, state) in enumerate(((80, 'graded'), (0, 'absence'), (None, 'pending'), (None, 'returned'), (None, 'group_pending'), (70, 'graded'))):
        records.append(dict(title=f'任务{index}', date_label=f'2026-09-{index + 1:02}', link_url=f'/assignment/{index + 1}', kind_label='平时作业', is_late=False,
                            is_absence_score=state == 'absence', is_regrading=index == 5, my_score=score, class_avg=score, band_label='', band_tone='', grade_display_state=state))
    chart = {'labels': [r['date_label'] for r in records], 'mine': [r['my_score'] for r in records], 'class_avg': [r['class_avg'] for r in records]}
    category = dict(label='平时作业', graded_count=3, avg_score=50, trend_label='样本还少，继续积累', chart_index=0, records=records)
    return dict(summary=dict(record_total=3, overall_avg=50, top_band_count=0, pending_total=3),
                published_grades=[dict(course_name='课程', semester_name='秋季', version=1, published_at='2026-09-20', ordinary_score=0, final_exam_score=0, overall_score=0, formula={'text': '冻结课程总评'})],
                selected_assessment_kind='', selected_class_offering_id=40, personal_records=[], charts=[chart],
                courses=[dict(course_name='课程', semester_name='秋季', record_count=6, avg_score=50, trend_label='样本还少，继续积累', categories=[category])])


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.tags = []; self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def with_attr(self, key):
        return [(tag, attrs) for tag, attrs in self.tags if key in attrs]


class ReportCardPilotTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=True, undefined=StrictUndefined)
        preferences = dict(enabled=True, palette_key='indigo', appearance='dark', glass='off', version=0, context_token='fixture', available=True,
                           presets=[{'key': key, 'name': key} for key in ('teal', 'indigo', 'sky', 'mint', 'violet', 'rose')])
        cls.env.globals.update(lq_props=lq_props, asset_url=lambda value: '/static/' + value, static_asset_revision=lambda: 'fixture',
                               vite_entry_tags=lambda _: '', resolve_user_ui_preferences=lambda *_: preferences)

    def render(self, enabled, card=None, path='/report-card'):
        return self.env.get_template('report_card.html').render(request=SimpleNamespace(url=SimpleNamespace(path=path)),
            user_info={'role': 'student', 'id': 7, 'name': '<同学>', 'nickname': '昵称'}, student_security_summary=None,
            report_card=card or sample_card(), lq_pilot_enabled=enabled)

    def test_switch_retains_all_data_and_filter_urls_with_single_owners(self):
        old, new = self.render(False), self.render(True)
        for html in (old, new):
            self.assertIn('总评 0 分', html); self.assertIn('未提交，教师记 0', html)
            self.assertIn('等待小组揭晓', html); self.assertIn('已退回待重交', html); self.assertIn('重批中 · 原有效分', html)
            self.assertNotIn('<同学>', html); self.assertIn('&lt;同学&gt;', html)
            tags = Tags(html)
            for identity in ('student-security-modal', 'feedback-modal'):
                self.assertEqual(sum(attrs.get('id') == identity for _, attrs in tags.tags), 1)
            self.assertEqual(len(tags.with_attr('data-message-center-bell')), 1)
            self.assertEqual(len(tags.with_attr('data-app-bottomnav')), 1)
            self.assertEqual(len(tags.with_attr('data-report-chart-data')), 1)
        pattern = r'<script type="application/json" data-report-chart-data>(.*?)</script>'
        self.assertEqual(json.loads(re.search(pattern, old, re.S)[1]), json.loads(re.search(pattern, new, re.S)[1]))
        links = lambda html: [a['href'] for t, a in Tags(html).tags if t == 'a' and a.get('href', '').startswith('/report-card?')]
        self.assertEqual(links(old), links(new))
        self.assertTrue(all('class_offering_id=40' in link for link in links(new)))
        self.assertIn("const topbarMenus =", old); self.assertNotIn("const topbarMenus =", new)
        self.assertNotIn('src="/static/js/report_card.js"', old); self.assertIn('src="/static/js/report_card.js"', new)
        self.assertIn('const chart = echarts.init(el)', old); self.assertNotIn('const chart = echarts.init(el)', new)
        self.assertEqual(len(Tags(new).with_attr('data-lq-report-card-topbar')), 1)
        self.assertNotIn('app-topbar-menu--personal', new)

    def test_compatibility_hooks_and_nonpilot_base_branch_are_explicit(self):
        enabled = self.render(True)
        roots = [(t, a) for t, a in Tags(enabled).tags if 'data-lq-report-card-topbar' in a]
        self.assertIn('app-topbar', roots[0][1]['class'].split())
        self.assertEqual(sum('app-topbar-brand' in a.get('class', '').split() for _, a in Tags(enabled).tags), 1)
        self.assertEqual(len(Tags(enabled).with_attr('data-ui-preferences-details')), 1)
        # Guard shared base_navbar independently of the report-card-only body.
        wrong_path = self.render(True, path='/resume')
        self.assertNotIn('data-lq-report-card-topbar', wrong_path)
        self.assertIn('const topbarMenus =', wrong_path)

    def test_empty_and_single_scores_do_not_create_false_zero_or_empty_chart(self):
        card = sample_card(); card['summary'].update(overall_avg=None, record_total=0, pending_total=0); card['courses'] = []; card['charts'] = []
        html = self.render(True, card)
        self.assertIn('当前范围还没有成绩记录', html); self.assertIn('总评 0 分', html)
        self.assertIn('>—</strong>', html); self.assertEqual(len(Tags(html).with_attr('data-report-chart')), 0)
        card = sample_card(); card['courses'][0]['categories'][0]['graded_count'] = 1
        self.assertEqual(len(Tags(self.render(True, card)).with_attr('data-report-chart')), 0)


class ReportCardPilotRouteTests(unittest.TestCase):
    def test_context_flag_never_changes_actor_or_projection_and_teacher_is_rejected(self):
        sentinel = object()
        @contextmanager
        def connection():
            yield sentinel
        request = SimpleNamespace(url=SimpleNamespace(path='/report-card'))
        with patch.object(route, 'get_db_connection', connection), patch.object(route, 'build_student_report_card', return_value=sample_card()) as build, \
                patch.object(route, 'is_lq_pilot_enabled', return_value=True) as flag, patch.object(route.templates, 'TemplateResponse', side_effect=lambda req, name, context: context):
            context = asyncio.run(route.report_card_page(request, user={'role': 'student', 'id': 7}, assessment_kind='homework', class_offering_id=40))
            build.assert_called_once_with(sentinel, student_id=7, assessment_kind='homework', class_offering_id=40)
            flag.assert_called_once_with(request); self.assertTrue(context['lq_pilot_enabled'])
            self.assertEqual(context['user_info']['id'], 7)
            with self.assertRaises(HTTPException) as error:
                asyncio.run(route.report_card_page(request, user={'role': 'teacher', 'id': 7}))
            self.assertEqual(error.exception.status_code, 403); self.assertEqual(build.call_count, 1)

    def test_json_projection_does_not_consult_presentation_flag(self):
        @contextmanager
        def connection():
            yield None
        card = sample_card()
        with patch.object(route, 'get_db_connection', connection), patch.object(route, 'build_student_report_card', return_value=card) as build, patch.object(route, 'is_lq_pilot_enabled') as flag:
            self.assertEqual(route.api_report_card(user={'role': 'student', 'id': 7}, class_offering_id=40), {'status': 'success', 'report_card': card})
            build.assert_called_once_with(None, student_id=7, assessment_kind=None, class_offering_id=40)
            flag.assert_not_called()
