import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import errors
from classroom_app.app import app as application


class DatabasePressureErrorTests(unittest.TestCase):
    def test_native_transient_errors_settle_as_503_without_touching_login(self):
        for error in (errors.QueryCanceled, errors.LockNotAvailable, errors.DeadlockDetected,
                      errors.SerializationFailure, errors.ConnectionException):
            with self.subTest(error=error.__name__):
                app = FastAPI()
                app.add_exception_handler(error, application.exception_handlers[error])
                @app.get("/api/synthetic-pressure")
                def request():
                    raise error("synthetic private SQL detail")
                with TestClient(app) as client:
                    response = client.get("/api/synthetic-pressure", headers={"Cookie": "access_token=synthetic"})
                self.assertEqual(503, response.status_code)
                self.assertEqual("service_unavailable", response.json()["code"])
                self.assertEqual("3", response.headers["retry-after"])
                self.assertNotIn("set-cookie", response.headers)
                self.assertNotIn("redirect_to", response.json())
                self.assertNotIn("private SQL", response.text)


if __name__ == "__main__":
    unittest.main()
