"""Regression tests: classroom free-room queries must not 500 on login failure.

`open_authenticated_academic_client` raises a bare ``ValueError`` when the
academic-system login can no longer be verified (wrong password, captcha
challenge, password-change required, unsupported adapter). The classroom
service used to catch only ``AcademicSessionRedirectError`` and
``httpx.HTTPError``, so that ``ValueError`` propagated to the route handler and
surfaced to the teacher as "失败，服务器错误" (HTTP 500). These tests lock in the
graceful, non-raising behaviour shared with the other academic sync services.
"""

import asyncio
import contextlib
import unittest
from unittest.mock import patch

from classroom_app.services import academic_classroom_sync_service as svc


class _DummyConn:
    """Minimal context-manager connection; never actually queried in these tests."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("DB should not be queried after login failure")


@contextlib.contextmanager
def _dummy_db_connection():
    yield _DummyConn()


def _raising_client(_access_payload):
    @contextlib.asynccontextmanager
    async def _cm():
        raise ValueError("账号密码可能正确，但教务系统要求完成二次验证或改密后才能对接。")
        yield  # pragma: no cover - unreachable, marks this an async generator

    return _cm()


class ClassroomLoginFailureTests(unittest.TestCase):
    def setUp(self):
        self._patchers = [
            patch.object(svc, "get_db_connection", _dummy_db_connection),
            patch.object(
                svc,
                "load_teacher_academic_access_method",
                lambda *a, **k: {"school_code": "gxufl", "username": "t", "password": "p"},
            ),
            patch.object(svc, "open_authenticated_academic_client", _raising_client),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patchers])

    def test_free_query_returns_login_failed_instead_of_raising(self):
        async def run():
            with patch.object(
                svc, "_resolve_term_params", return_value=({"xnm": "2024", "xqm": "3"}, None)
            ), patch.object(svc, "_find_unique_local_place_for_free_query", return_value=None):
                return await svc.query_free_classrooms_from_academic_system(
                    1, {"weeks": "1", "xqj": "1", "sections": "1"}
                )

        result = asyncio.run(run())
        self.assertEqual(result["status"], "academic_login_failed")
        self.assertNotEqual(result["status"], "success")
        self.assertIn("教务系统", result["message"])

    def test_free_options_returns_graceful_status_instead_of_raising(self):
        async def run():
            with patch.object(
                svc, "_resolve_term_params", return_value=({"xnm": "2024", "xqm": "3"}, None)
            ), patch.object(svc, "_query_options_from_places", return_value={}):
                return await svc.load_free_classroom_options_from_academic_system(1)

        result = asyncio.run(run())
        self.assertEqual(result["status"], "academic_unavailable")
        self.assertIn("options", result)

    def test_teaching_place_sync_returns_graceful_status_instead_of_raising(self):
        result = asyncio.run(svc.sync_teaching_places_from_academic_system(1))
        self.assertEqual(result["status"], "academic_unavailable")
        self.assertNotEqual(result["status"], "success")


if __name__ == "__main__":
    unittest.main()
