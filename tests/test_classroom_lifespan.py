"""Boot the classroom entry point in a fresh process with an empty, owned SQLite database."""

import os
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from urllib.parse import urlsplit


class ClassroomLifespanTests(unittest.TestCase):
    def test_serves_classroom_and_games_then_closes_resources(self):
        root = Path(self.enterContext(TemporaryDirectory())).resolve()
        system_keys = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LD_LIBRARY_PATH", "LANG"}
        env = {k: v for k, v in os.environ.items() if k.upper() in system_keys}
        env.update(TEMP=str(root), TMP=str(root), TMPDIR=str(root), PYTHONUTF8="1",
                   PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                   MINIGAMES_EXTERNAL_API="1", AI_ASSISTANT_URL="http://127.0.0.1:1",
                   SECRET_KEY=secrets.token_urlsafe(48))
        result = subprocess.run(
            [sys.executable, "-m", "tests.test_classroom_lifespan", str(root)],
            cwd=Path(__file__).resolve().parents[1], env=env,
            capture_output=True, encoding="utf-8", errors="replace", timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


def exercise_lifespan(root):
    from tools.isolated_environment import guard_dotenv_loading, guard_postgres_connections, isolate_sqlite_environment

    isolate_sqlite_environment(root)
    guard_dotenv_loading()
    guard_postgres_connections()
    from fastapi.testclient import TestClient
    from classroom_app.app import app, ai_client
    from classroom_app.config import DB_PATH

    check = unittest.TestCase()
    check.assertEqual(DB_PATH.resolve(), root / "db" / "classroom.db")
    with TestClient(app, follow_redirects=False) as client:
        live = client.get("/api/internal/live")
        check.assertEqual(live.status_code, 200)
        check.assertEqual(live.json()["service"], "main")
        deadline = time.monotonic() + 10
        ready = client.get("/api/internal/ready")
        while ready.status_code == 503 and time.monotonic() < deadline:
            time.sleep(0.05)
            ready = client.get("/api/internal/ready")
        check.assertEqual(ready.status_code, 200, ready.text)
        check.assertTrue(app.state.minigames_ready)
        check.assertFalse(app.state.minigames_owns_workers)
        hall = client.get("/games")
        check.assertTrue(hall.is_redirect, hall.text)
        check.assertTrue(urlsplit(hall.headers["location"]).path.endswith("/login"))
    check.assertFalse(app.state.minigames_ready)
    check.assertTrue(ai_client.is_closed)


if __name__ == "__main__":
    exercise_lifespan(Path(sys.argv[1]))
