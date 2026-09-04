"""Isolated browser fixture: real hero template, QR API, SQLite and hash storage.

Started by validate_classroom_group_qr.cjs; no production app or database loads.
"""
import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import qrcode
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from test_classroom_group_qr import seed_fixture, connect_fixture
from classroom_app.routers import classroom_group_qr as routes
from classroom_app.services import file_service
from classroom_app.services.classroom_group_qr_service import load_group_qr_offering, serialize_group_qr


def build_app(root):
    db_path = root / 'fixture.db'
    seed_fixture(db_path)
    routes.get_db_connection = lambda: connect_fixture(db_path)
    file_service.GLOBAL_FILES_DIR = root / 'files'
    file_service.GLOBAL_FILES_LEGACY_DIRS = ()
    app = FastAPI()
    app.include_router(routes.router)
    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

    def current_user(request: Request):
        return {'role': request.cookies.get('fixture_role', 'teacher'), 'id': 1}
    app.dependency_overrides[routes.get_current_user] = current_user

    env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=select_autoescape())
    source = (ROOT / 'templates/classroom_main_v4.html').read_text(encoding='utf-8')
    # Parse the full production template, then render its exact hero fragment.
    env.parse(source)
    hero = source.split('<div class="workspace-hero-main', 1)[1].split('<div class="workspace-hero-bottom">', 1)[0]
    template = env.from_string('<div class="workspace-hero-main' + hero)

    @app.get('/', response_class=HTMLResponse)
    def index(role: str = 'teacher'):
        user = {'id': 1, 'role': 'student' if role == 'student' else 'teacher'}
        with connect_fixture(db_path) as conn:
            group_qr = serialize_group_qr(load_group_qr_offering(conn, 11, user))
        classroom = {'id': 11, 'course_name': '计算机网络原理', 'class_name': '计算机科学2606班（专升本）'}
        content = template.render(classroom=classroom, user_info=user,
            hero={'primary_meta': [{'label': '班级', 'value': classroom['class_name']}],
                  'lead': '张老师，任务、材料、资源和讨论都在这里。'},
            classroom_page={'group_qr': group_qr, 'learning_progress': True, 'learning_overview': True})
        page = f'''<!doctype html><html lang="zh-CN" data-theme="lanshare"><head><meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <link rel="stylesheet" href="/static/css/tailwind-app.css">
            <link rel="stylesheet" href="/static/css/classroom_group_qr.css"></head>
            <body class="page-wrapper classroom-page role-{user['role']}">
            <main class="classroom-workspace" style="align-content:start"><section class="workspace-hero">{content}</section></main>
            <script type="module" src="/static/js/classroom_group_qr.js"></script></body></html>'''
        response = HTMLResponse(page)
        response.set_cookie('fixture_role', user['role'])
        return response

    @app.get('/fixture/qr')
    def qr(variant: str = 'one'):
        image = qrcode.make(f'https://example.com/group-qr-test/{variant}')
        output = io.BytesIO()
        image.save(output, format='PNG')
        return Response(output.getvalue(), media_type='image/png')

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(build_app(args.root), host='127.0.0.1', port=args.port, log_level='error')
