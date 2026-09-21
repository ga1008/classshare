"""Server-only rollout isolation; execute with tools/test_backend.py."""
import os
import unittest
from unittest.mock import patch

from classroom_app.lq_migration import LQ_MIGRATION_FAMILIES, lq_family_enabled
from classroom_app.lq_pilot import is_lq_pilot_enabled


class LqMigrationFlagTests(unittest.TestCase):
    def test_default_and_unrecognized_switches_leave_families_off(self):
        for value in ("", "true", "1", "all", "*", "PROFILE", "profile-extra", "profile messages"):
            with self.subTest(value=value), patch.dict(os.environ, {"LANSHARE_LQ_FAMILIES": value}):
                self.assertFalse(any(map(lq_family_enabled, LQ_MIGRATION_FAMILIES)))
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(any(map(lq_family_enabled, LQ_MIGRATION_FAMILIES)))

    def test_families_can_be_enabled_and_rolled_back_independently(self):
        with patch.dict(os.environ, {"LANSHARE_LQ_FAMILIES": " profile, centered,profile,unknown,, "}):
            self.assertEqual({key for key in LQ_MIGRATION_FAMILIES if lq_family_enabled(key)}, {"profile", "centered"})
            self.assertFalse(lq_family_enabled("unknown"))
            self.assertFalse(lq_family_enabled("profile?enabled=true"))
            os.environ["LANSHARE_LQ_FAMILIES"] = "centered"
            self.assertFalse(lq_family_enabled("profile"))
            self.assertTrue(lq_family_enabled("centered"))

    def test_family_and_exact_route_pilot_switches_are_independent(self):
        with patch.dict(os.environ, {"LANSHARE_LQ_FAMILIES": "manage-shell", "LANSHARE_LQ_PILOT": "false"}):
            self.assertTrue(lq_family_enabled("manage-shell"))
            self.assertFalse(is_lq_pilot_enabled("/manage/library/courses"))
        with patch.dict(os.environ, {"LANSHARE_LQ_FAMILIES": "", "LANSHARE_LQ_PILOT": "true"}):
            self.assertFalse(lq_family_enabled("manage-shell"))
            self.assertTrue(is_lq_pilot_enabled("/manage/library/courses"))
            self.assertFalse(is_lq_pilot_enabled("/manage/me"))
