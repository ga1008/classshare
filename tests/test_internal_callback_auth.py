import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.routers import ai as ai_router
from classroom_app.services.internal_callback_auth import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    signed_callback_request,
    verify_signed_callback,
)


class _FakeRequest:
    def __init__(self, body: bytes, headers: dict[str, str]):
        self._body = body
        self.headers = {key.lower(): value for key, value in headers.items()}

    async def body(self):
        return self._body

    async def json(self):
        return json.loads(self._body)


class CallbackSignatureTests(unittest.TestCase):
    def test_signed_request_verifies(self):
        body, headers = signed_callback_request({"submission_id": 5, "score": 90}, now=1_000_000)
        verify_signed_callback(body, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], now=1_000_010)

    def test_tampered_body_is_rejected(self):
        body, headers = signed_callback_request({"submission_id": 5, "score": 10}, now=1_000_000)
        forged = body.replace(b"10", b"99")
        with self.assertRaises(HTTPException) as ctx:
            verify_signed_callback(forged, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], now=1_000_000)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_missing_or_stale_signature_is_rejected(self):
        body, headers = signed_callback_request({"submission_id": 5}, now=1_000_000)
        for ts, sig, now in (("", "", 1_000_000), (headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], 1_000_000 + 3600)):
            with self.assertRaises(HTTPException):
                verify_signed_callback(body, ts, sig, now=now)


class CallbackRouteTests(unittest.TestCase):
    def test_unsigned_callback_is_rejected_before_touching_the_database(self):
        request = _FakeRequest(json.dumps({"submission_id": 1, "score": 100}).encode(), {})
        with patch.object(ai_router, "get_db_connection") as db:
            response = asyncio.run(ai_router.handle_ai_grading_callback(request))
        self.assertEqual(response.status_code, 401)
        db.assert_not_called()


if __name__ == "__main__":
    unittest.main()
