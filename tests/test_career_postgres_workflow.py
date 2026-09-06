"""Opt-in real PostgreSQL contract probe; normal unit runs stay isolated/fast."""
import os
import unittest


class CareerPostgresWorkflowTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RUN_LOCAL_PG_CAREER_PROBE")=="1","opt-in local PostgreSQL integration probe")
    def test_isolated_real_http_contract(self):
        from tools.career_postgres_workflow_probe import run
        report=run(students=10,polls=30,threads=5)
        self.assertTrue(report["ok"])
        self.assertTrue(report["isolated_schema_removed"])


if __name__=="__main__":unittest.main()
