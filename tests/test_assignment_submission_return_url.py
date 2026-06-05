import unittest
from urllib.parse import urlencode

from starlette.requests import Request

from classroom_app.routers.ui_parts.assignment_pages import _submission_return_url


def _request_with_query(params: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/submission/1",
            "query_string": urlencode(params).encode("utf-8"),
            "headers": [],
        }
    )


class SubmissionReturnUrlTests(unittest.TestCase):
    def test_wrong_summary_return_url_preserves_safe_fragment(self):
        request = _request_with_query(
            {
                "return_to": "/assignment/44/wrong-summary#wrong-summary-errors-q-12",
            }
        )

        self.assertEqual(
            _submission_return_url(request, "/assignment/44"),
            "/assignment/44/wrong-summary#wrong-summary-errors-q-12",
        )

    def test_unsafe_return_url_falls_back_to_assignment(self):
        request = _request_with_query({"return_to": "https://example.com/wrong-summary#x"})

        self.assertEqual(_submission_return_url(request, "/assignment/44"), "/assignment/44")


if __name__ == "__main__":
    unittest.main()
