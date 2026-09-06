"""Serve real UI/routes on a synthetic fixture, without production workers."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TEMP = (REPO / '.codex-temp').resolve()
parser = argparse.ArgumentParser()
parser.add_argument('--runtime-root', type=Path, required=True)
parser.add_argument('--source', type=Path, default=REPO)
parser.add_argument('--port', type=int, required=True)
args = parser.parse_args()
runtime, source = args.runtime_root.resolve(), args.source.resolve()
assert runtime != TEMP and runtime.is_relative_to(TEMP)
assert source == REPO or source.is_relative_to(TEMP)
fixture = json.loads((runtime / 'fixture.json').read_text(encoding='utf-8'))
assert fixture.get('uiV3Synthetic') is True
assert Path(fixture['databasePath']).resolve() == runtime / 'db/classroom.db'
os.environ.update({
    'PYTHON_DOTENV_DISABLED': '1', 'DB_ENGINE': 'sqlite', 'POSTGRES_BACKEND_READY': 'false',
    'LANSHARE_DATA_ROOT': str(runtime), 'MAIN_DATA_DIR': str(runtime),
    'MAIN_DB_PATH': str(runtime / 'db/classroom.db'), 'PYTHONIOENCODING': 'utf-8',
    'AI_HOST': '127.0.0.1', 'AI_PORT': '1', 'AI_ASSISTANT_URL': 'http://127.0.0.1:1',
    'MAIN_APP_CALLBACK_URL': f'http://127.0.0.1:{args.port}/api/internal/grading-complete',
    'UI_COPY_GENERATION_ENABLED': 'false',
})

# Only loopback communication belongs in this synthetic HTTP/UI harness.
original_connect = socket.socket.connect
original_dns = socket.getaddrinfo


def local_connect(sock, address):
    if str(address[0]) not in {'127.0.0.1', '::1', 'localhost'}:
        raise RuntimeError('UI v3 test runtime forbids external sockets')
    return original_connect(sock, address)


def local_dns(host, *values, **kwargs):
    if host is not None and str(host) not in {'127.0.0.1', '::1', 'localhost'}:
        raise RuntimeError('UI v3 test runtime forbids external DNS')
    return original_dns(host, *values, **kwargs)


socket.socket.connect = local_connect
socket.getaddrinfo = local_dns
sys.path.insert(0, str(source))
from classroom_app.app import app
from classroom_app.core import ai_client
from classroom_app.database import init_database
from classroom_app.config import DB_PATH, ensure_runtime_directories

assert Path(DB_PATH).resolve() == runtime / 'db/classroom.db'


@asynccontextmanager
async def ui_lifespan(_app):
    ensure_runtime_directories()
    init_database()
    await ai_client.__aenter__()
    print('UI_V3_SYNTHETIC_HTTP_READY', flush=True)
    yield
    await ai_client.__aexit__(None, None, None)


app.router.lifespan_context = ui_lifespan
import uvicorn
uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
