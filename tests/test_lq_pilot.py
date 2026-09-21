from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from starlette.requests import Request

from classroom_app.lq_pilot import LQ_PILOT_PATHS, is_lq_pilot_enabled


class LqPilotFlagTests(unittest.TestCase):
    def test_missing_and_invalid_values_keep_every_pilot_off(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(any(map(is_lq_pilot_enabled, LQ_PILOT_PATHS)))
        for value in ("", "false", "0", "off", "no", "enabled", "2"):
            with self.subTest(value=value), patch.dict(os.environ, {"LANSHARE_LQ_PILOT": value}):
                self.assertFalse(any(map(is_lq_pilot_enabled, LQ_PILOT_PATHS)))

    def test_only_nine_explicit_canonical_paths_can_be_enabled(self):
        self.assertEqual(len(LQ_PILOT_PATHS), 9)
        for value in ("1", "true", "YES", " On "):
            with self.subTest(value=value), patch.dict(os.environ, {"LANSHARE_LQ_PILOT": value}):
                self.assertTrue(all(map(is_lq_pilot_enabled, LQ_PILOT_PATHS)))
                for path in ("/", "/achievements", "/manage/courses", "/manage/library/courses/",
                             "/manage/library/courses/123", "/report-card-other", "/api/report-card",
                             "/manage/library/courses?lq_pilot=true"):
                    self.assertFalse(is_lq_pilot_enabled(path), path)

    def test_browser_query_and_header_do_not_enable_presentation(self):
        request = Request({"type": "http", "path": "/report-card", "scheme": "http",
                           "server": ("localhost", 80), "query_string": b"lq_pilot=true",
                           "headers": [(b"x-lq-pilot", b"true")]})
        with patch.dict(os.environ, {"LANSHARE_LQ_PILOT": "false"}):
            self.assertFalse(is_lq_pilot_enabled(request))
        with patch.dict(os.environ, {"LANSHARE_LQ_PILOT": "true"}):
            self.assertTrue(is_lq_pilot_enabled(request))

    def test_server_switch_can_return_the_same_route_to_legacy(self):
        with patch.dict(os.environ, {"LANSHARE_LQ_PILOT": "true"}):
            self.assertTrue(is_lq_pilot_enabled("/report-card"))
            os.environ["LANSHARE_LQ_PILOT"] = "false"
            self.assertFalse(is_lq_pilot_enabled("/report-card"))
