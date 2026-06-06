import unittest
from urllib.parse import parse_qs
import httpx
from unittest.mock import patch

from tools import high_concurrency_smoke


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConnection:
    def __init__(self):
        self.calls = []
        self._next_id = 100
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        self.calls.append((str(sql), tuple(params)))
        self._next_id += 1
        return _FakeCursor({"id": self._next_id})

    def commit(self):
        self.committed = True


class HighConcurrencySmokePostgresTests(unittest.TestCase):
    def test_seed_data_uses_insert_returning_helper_for_postgres(self):
        fake_conn = _FakeConnection()

        with (
            patch("classroom_app.database.get_db_connection", return_value=fake_conn),
            patch("classroom_app.db.connection.get_configured_db_engine", return_value="postgres"),
            patch("classroom_app.dependencies.get_password_hash", side_effect=lambda value: f"hashed:{value}"),
        ):
            seed = high_concurrency_smoke._seed_test_data(2, run_id="repeatable-run-001")

        self.assertTrue(fake_conn.committed)
        self.assertEqual(101, seed["teacher_id"])
        self.assertEqual("loadtest.teacher.atablerun001@example.com", seed["teacher_email"])
        self.assertEqual("atablerun001", seed["run_id"])
        self.assertEqual(102, seed["class_id"])
        self.assertEqual(103, seed["course_id"])
        self.assertEqual(104, seed["class_offering_id"])
        self.assertEqual([105, 106], [student.student_pk for student in seed["students"]])
        self.assertEqual(["LTatablerun001001", "LTatablerun001002"], [student.student_id_number for student in seed["students"]])
        self.assertEqual(6, len(fake_conn.calls))
        for sql, _params in fake_conn.calls:
            self.assertIn("RETURNING id", sql)
        flattened_params = [param for _sql, params in fake_conn.calls for param in params]
        self.assertIn("Load Test Class atablerun001", flattened_params)
        self.assertIn("Load Test Course atablerun001", flattened_params)

    def test_teacher_login_uses_seeded_email(self):
        captured = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            form = parse_qs(request.content.decode())
            captured.update({key: values[0] for key, values in form.items()})
            return httpx.Response(
                302,
                headers={"location": "/dashboard", "set-cookie": "access_token=teacher-token; Path=/"},
            )

        async def run_case():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await high_concurrency_smoke._login_teacher(
                    client,
                    email="loadtest.teacher.unique@example.com",
                    password="pw",
                )

        import asyncio

        token = asyncio.run(run_case())

        self.assertEqual("teacher-token", token)
        self.assertEqual("loadtest.teacher.unique@example.com", captured["email"])
        self.assertEqual("pw", captured["password"])


if __name__ == "__main__":
    unittest.main()
