"""Safety-boundary tests; destructive-to-owned-process PG scenarios are opt-in."""
import os
import unittest
from unittest.mock import patch

from tools import career_postgres_process_kill_probe as probe


class ProcessKillProbeBoundaryTests(unittest.TestCase):
    def test_only_localhost_and_exact_generated_schema_are_accepted(self):
        schema = "career_kill_probe_" + "a" * 32
        for url in ("postgresql://127.0.0.1/test", "postgresql://localhost/test", "postgresql://[::1]/test"):
            probe.validate_boundary(url, schema)
        for url in ("postgresql://production.example/test", "postgresql://localhost.example/test", "postgresql:///test"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                probe.validate_boundary(url, schema)
        for unsafe in ("public", "career_kill_probe_", "career_kill_probe_" + "g" * 32,
                       schema + ";DROP SCHEMA public", schema + ",public", schema + "\n"):
            with self.subTest(schema=unsafe), self.assertRaises(ValueError):
                probe.validate_boundary("postgresql://localhost/test", unsafe)

    def test_connection_boundary_fails_before_opening_any_database(self):
        with patch.object(probe.config, "DATABASE_URL", "postgresql://production.example/test"), \
                patch.object(probe.psycopg, "connect") as connect, self.assertRaises(ValueError):
            probe._connect_factory("career_kill_probe_" + "0" * 32)
        connect.assert_not_called()

    @unittest.skipUnless(os.environ.get("RUN_LOCAL_PG_CAREER_KILL_PROBE") == "1", "opt-in real process-kill PostgreSQL probe")
    def test_real_process_failures_recover_and_cleanup(self):
        report = probe.run()
        self.assertTrue(report["ok"])
        self.assertEqual(report["forced_own_processes"], 6)
        self.assertEqual(report["admitted_jobs"], report["retained_jobs"])
        self.assertEqual(report["duplicate_publications_per_revision"], 0)
        self.assertTrue(report["schema_removed"])
        self.assertEqual(report["owned_sessions_remaining"], 0)

    @unittest.skipUnless(os.environ.get("RUN_LOCAL_PG_CAREER_KILL_PROBE") == "1", "opt-in real PostgreSQL finally-cleanup probe")
    def test_schema_is_removed_even_when_acceptance_body_fails(self):
        fixture = None
        with self.assertRaisesRegex(RuntimeError, "intentional acceptance failure"):
            with probe.isolated_process_kill_postgres() as fixture:
                raise RuntimeError("intentional acceptance failure")
        self.assertTrue(fixture["schema_removed"])
        self.assertEqual(fixture["owned_sessions_remaining"], 0)


if __name__ == "__main__":
    unittest.main()
