"""Run with the project Python; isolated API fixtures, real shared ES modules."""
import json
import re
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    from classroom_app.core import templates
    from classroom_app.services.manage_nav_service import build_manage_nav
    from starlette.requests import Request

    actor = {'id': 1, 'name': '测试教师', 'email': 'qa@example.invalid', 'role': 'teacher', 'school_code': 'qa', 'school_name': '测试学校', 'is_super_admin': False}

    def management_page(name, title):
        rendered = templates.get_template(f'manage/{name}.html').render(
            request=Request({'type': 'http', 'path': '/qa', 'headers': [], 'query_string': b''}),
            user_info=actor, page_title=title, active_page=name, embedded_mode=False,
            current_teacher_is_super_admin=False,
            manage_nav=build_manage_nav(actor, name, is_super_admin=False),
            signature_actor=actor,
            signature_stats={'visible_total': 2, 'mine': 1, 'college': 0, 'usage_total': 0},
        )
        # Keep the complete production layout/styles, with only the page module
        # running against fixtures; unrelated account widgets have no test API.
        rendered = re.sub(r'<script\b[^>]*>.*?</script>', '', rendered, flags=re.S)
        module = 'manage_signatures' if name == 'signatures' else name
        return rendered.replace('</body>', f'<script type="module" src="/static/js/{module}.js"></script></body>')

    inbox_html = management_page('signature_workflows', '签名审批与使用')
    library_html = management_page('signatures', '电子签名')
    head = '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/material_workflows.css"><link rel="stylesheet" href="/static/css/signature_point_workflow.css"><style>*{box-sizing:border-box}body{font:14px system-ui;margin:24px;background:#f6f8fb}button,input,select,textarea{font:inherit}#grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:20px}.card{border:1px solid #dce2ed;border-radius:16px;background:white;padding:25px;min-height:150px}</style>'
    html = head + '<main id="grid"><article class="card" data-material-key="assessment_plan:1"><h3>网络工程 · 软工2401班</h3><button id="old-action">原有预览</button></article><article class="card" data-material-key="assessment_plan:2"><h3>网络工程 · 软工2402班</h3></article></main><script type="module">import {MaterialSelectionPanel} from "/static/js/material_selection_panel.js";window.panel=new MaterialSelectionPanel({grid:document.querySelector("#grid")});panel.update([1,2].map(n=>({material_type:"assessment_plan",material_id:String(n),title:"网络工程 · 软工240"+n+"班"})));</script>'

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(repo), **kwargs)

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path in {'/qa', '/qa-inbox', '/qa-library'}:
                body = {'/qa': html, '/qa-inbox': inbox_html, '/qa-library': library_html}[self.path]
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(body.encode())
                return
            super().do_GET()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    calls = []
    can_apply = True
    email_ready = True
    items = [{'id': 1, 'name': '张老师', 'subject_name': '张老师', 'identity_match': True, 'can_use': False, 'signature_kind': 'personal', 'scope_label': '学校可见', 'identity_label': '教师'}, {'id': 2, 'name': '已核', 'subject_name': '已核', 'identity_match': True, 'can_use': True, 'signature_kind': 'stamp'}] + [{'id': i, 'name': f'教师{i}', 'subject_name': f'教师{i}', 'identity_match': True, 'can_use': False, 'signature_kind': 'personal'} for i in range(3, 70)]
    request = {'id': 1, 'requester_name': '申请教师', 'signature_subject_name': '张老师', 'context_label': '试卷分析表 · 网络工程 · 软工2401班', 'status': 'pending', 'can_review': True, 'requested_at': '2026-09-08 12:00', 'request_note': '请审核本学期课程材料', 'snapshot_id': 'snapshot1', 'items': [{'function_point_label': '系（教研室）审核'}], 'reviewers': [], 'requester_department': '网络工程系'}

    def api(route):
        path = route.request.url.split('/api/signatures', 1)[1].split('?', 1)[0]
        body = route.request.post_data_json if route.request.post_data else {}
        calls.append((path, body))
        if path == '/materials/selection':
            docs = [{**doc, 'title': '课程' + doc['material_id'], 'complete': False} for doc in body['documents']]
            data = {'documents': docs, 'common_properties': {'文档类型': '考核计划表', '课程': '网络工程', '班级': '多种'}, 'can_apply': can_apply, 'apply_reason': '请选择同类文档', 'points': [{'key': 'assessment_plan.reviewer_signature', 'label': '系（教研室）主任审核', 'opinion_key': 'department_review_opinion'}]}
        elif path == '/notification-readiness':
            data = {'email_available': email_ready, 'email_reason': '尚未配置邮件提醒'}
        elif path == '':
            data = {'actor': actor, 'items': [{**item, 'image_url': 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="240" height="100"%3E%3Ctext x="40" y="65" font-size="40"%3EQA%3C/text%3E%3C/svg%3E', 'can_use': True, 'can_edit': True, 'can_delete': True} for item in items[:2]], 'stats': {'visible_total': 2}}
        elif path.startswith('/points/'):
            data = {'signatures': items, 'point': {'required_identity_labels': ['系主任（含副职）']}}
        elif path == '/materials/bundles/preflight':
            data = {'id': 'bundle-test', 'incomplete': ['网络工程 · 软工2401班', '网络工程 · 软工2402班'], 'count': 2}
        elif path.endswith('/submit'):
            data = {'status': 'queued'}
        elif path == '/materials/bundles/bundle-test':
            data = {'status': 'failed', 'results': [], 'error_message': '测试完成'}
        elif path == '/materials/applications':
            data = {'id': 'batch-test', 'status': 'preparing', 'batches': [], 'flows': []}
        elif path == '/materials/applications/batch-test':
            data = {'status': 'completed', 'results': [{'status': 'submitted'}]}
        elif path == '/requests':
            data = {'items': [request], 'total': 1}
        elif path == '/requests/1':
            data = {'request': request, 'preview_url': '/api/signatures/requests/1/preview'}
        elif path == '/requests/1/preview':
            route.fulfill(status=200, content_type='text/html', body='<h2>申请时文档</h2><p>课程：网络工程；分析成绩：80。</p>')
            return
        elif path.endswith('/approve'):
            data = {'status': 'success', 'request': request}
        else:
            data = {}
        route.fulfill(status=200, content_type='application/json', body=json.dumps(data, ensure_ascii=False))

    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True, channel='chrome')
            page = browser.new_page(viewport={'width': 1360, 'height': 960})
            page.emulate_media(reduced_motion='reduce')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/signatures**', api)
            page.goto(f'http://127.0.0.1:{server.server_port}/qa')
            page.locator('[data-select-visible]').wait_for()
            expect(page.locator('[data-request-signatures]')).to_be_hidden()
            expect(page.locator('[data-clear-selection]')).to_be_hidden()
            page.locator('#old-action').click()
            assert page.locator('.msw-selected-card').count() == 0
            page.locator('[data-select-visible]').click()
            page.locator('[data-request-signatures]').wait_for(state='visible')
            assert page.locator('.msw-selected-card').count() == 2
            page.locator('[data-clear-selection]').click()
            expect(page.locator('[data-request-signatures]')).to_be_hidden()
            expect(page.locator('[data-clear-selection]')).to_be_hidden()
            can_apply = False
            page.locator('[data-select-visible]').click()
            expect(page.locator('[data-selection-help]')).to_have_text('请选择同类文档')
            expect(page.locator('[data-request-signatures]')).to_be_hidden()
            can_apply = True
            page.locator('[data-clear-selection]').click()
            page.locator('[data-select-visible]').click()
            page.locator('[data-request-signatures]').wait_for(state='visible')
            page.locator('[data-request-signatures]').click()
            page.locator('[data-spm-open]').click()
            page.locator('[data-spm-option="1"]').click()
            page.locator('[data-spm-option="2"]').click()
            search = page.get_by_role('combobox', name='搜索签名姓名、职务')
            search.fill('张')
            page.wait_for_timeout(350)
            assert search.input_value() == '张'
            search.fill('')
            page.wait_for_timeout(350)
            page.locator('[data-spm-tab="selected"]').click()
            page.locator('[data-spm-id="2"] [data-spm-move="-1"]').click()
            assert page.locator('[data-spm-selected] li').first.get_attribute('data-spm-id') == '2'
            bounds = page.locator('.spm-popover').bounding_box()
            assert bounds['width'] <= 480 and bounds['height'] <= 440, bounds
            page.screenshot(path=str(Path(tempfile.gettempdir()) / 'lanshare-material-picker-qa.png'), full_page=True)
            page.locator('[data-spm-done]').click()
            page.locator('[data-submit]').click()
            page.wait_for_timeout(400)
            posted = next(body for path, body in calls if path == '/materials/applications')
            assert posted['points'][0]['signature_ids'] == [2, 1]
            page.locator('[data-download-selected]').click()
            page.get_by_text('以下文档内容待补充', exact=True).wait_for()
            page.locator('[data-cancel]').click()
            assert not any(path.endswith('/submit') for path, body in calls)
            page.locator('[data-download-selected]').click()
            page.locator('[data-continue]').click()
            page.wait_for_timeout(250)
            assert any(path.endswith('/submit') and body['allow_incomplete'] for path, body in calls)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('[data-request-signatures]').click()
            page.locator('[data-spm-open]').click()
            mobile_bounds = page.locator('.spm-popover').bounding_box()
            assert mobile_bounds['width'] <= 366 and mobile_bounds['y'] + mobile_bounds['height'] <= 844, mobile_bounds
            page.screenshot(path=str(Path(tempfile.gettempdir()) / 'lanshare-material-picker-mobile-qa.png'), full_page=True)
            page.locator('[data-spm-done]').click()
            page.locator('[data-close]').click()
            page.set_viewport_size({'width': 1360, 'height': 960})
            page.goto(f'http://127.0.0.1:{server.server_port}/qa-inbox')
            expect(page.locator('[data-notification-readiness]')).to_be_hidden()
            page.locator('[data-request="1"]').click()
            page.frame_locator('iframe').get_by_text('申请时文档', exact=True).wait_for()
            page.screenshot(path=str(Path(tempfile.gettempdir()) / 'lanshare-signature-inbox-qa.png'), full_page=True)
            page.locator('[data-review="approve"]').click()
            page.wait_for_timeout(250)
            assert any(path.endswith('/approve') and body['expected_snapshot_id'] == 'snapshot1' for path, body in calls)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(Path(tempfile.gettempdir()) / 'lanshare-signature-mobile-qa.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.locator('[data-view="outgoing"]').click()
            expect(page.locator('[data-batch="approve"]')).to_be_hidden()
            expect(page.locator('[data-history]')).to_be_visible()
            page.locator('[data-view="usage"]').click()
            expect(page.locator('[data-filters]')).to_be_hidden()
            expect(page.locator('[data-preview]')).to_be_hidden()
            expect(page.locator('[data-history]')).to_be_hidden()
            page.locator('[data-view="incoming"]').click()
            expect(page.locator('[data-filters]')).to_be_visible()
            expect(page.locator('[data-batch="approve"]')).to_be_visible()
            email_ready = False
            page.reload()
            expect(page.locator('[data-notification-readiness]')).to_have_text('尚未配置邮件提醒')
            expect(page.locator('[data-notification-readiness]')).to_be_visible()
            page.set_viewport_size({'width': 1360, 'height': 960})
            calls.clear()
            page.goto(f'http://127.0.0.1:{server.server_port}/qa-library')
            expect(page.locator('.signature-card')).to_have_count(2)
            expect(page.locator('#signature-requests')).to_have_count(0)
            assert not any(path == '/requests' for path, body in calls)
            expect(page.locator('#signature-edit-btn')).to_be_visible()
            expect(page.locator('#signature-request-btn')).to_be_hidden()
            expect(page.locator('#signature-claim-btn')).to_be_hidden()
            for width in [1100, 1360, 1920]:
                page.set_viewport_size({'width': width, 'height': 960})
                assert page.locator('.signature-library-panel').evaluate('(panel) => panel.scrollWidth <= panel.clientWidth+1'), width
                for card in page.locator('.signature-card').all():
                    assert card.evaluate('(card) => card.scrollWidth <= card.clientWidth+1'), (width, card.evaluate('(card) => [card, ...card.querySelectorAll("*")].map(el=>({tag:el.tagName,cls:el.className,width:el.clientWidth,scroll:el.scrollWidth,css:getComputedStyle(el).gridTemplateColumns,min:getComputedStyle(el).minWidth}))'))
            page.set_viewport_size({'width': 1360, 'height': 960})
            page.locator('#signature-edit-btn').click()
            expect(page.locator('#signature-edit-form')).to_be_visible()
            overlay = page.locator('#signature-edit-modal').bounding_box()
            assert overlay['x'] == 0 and overlay['y'] == 0 and overlay['width'] == 1360, overlay
            page.screenshot(path=str(Path(tempfile.gettempdir()) / 'lanshare-signature-library-edit-qa.png'), full_page=True)
            page.goto(f'http://127.0.0.1:{server.server_port}/qa-library')
            expect(page.locator('.signature-card')).to_have_count(2)
            page.screenshot(path=str(Path(tempfile.gettempdir()) / 'lanshare-signature-library-qa.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(Path(tempfile.gettempdir()) / 'lanshare-signature-library-mobile-qa.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
    print('BROWSER_SELECTION_SORT_SEARCH_APPROVAL_AND_INCOMPLETE_DOWNLOAD_OK')


if __name__ == '__main__':
    main()
