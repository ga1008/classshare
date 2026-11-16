"""Failure-path checks for temporary workspaces owned by unittest fixtures."""

from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from classroom_app import config, database
from classroom_app.services import agent_task_service
from tests import test_agent_sdk_runtime as sdk_tests
from tests import test_agent_task_improvements as task_tests
from tests import test_gongwen_package_service as package_tests
from tests import test_life_tip_transactions as life_tip_tests


class TemporaryTestFixtureCleanupTests(unittest.TestCase):
    def run_fixture(self, case):
        result = unittest.TestResult()
        unittest.TestSuite([case]).run(result)
        return result

    def test_sdk_class_setup_failure_removes_workspace_and_restores_database(self):
        original_db_path = config.DB_PATH

        class FailedClassSetup(sdk_tests.AgentSdkRuntimeTests):
            @classmethod
            def setUpClass(cls):
                super().setUpClass()
                (cls.workspace / "partial-result.txt").write_text("synthetic", encoding="utf-8")
                raise RuntimeError("synthetic failure after class setup")

            def runTest(self):
                raise AssertionError("setUpClass must fail first")

        result = self.run_fixture(FailedClassSetup())

        self.assertEqual(len(result.errors), 1)
        self.assertIn("synthetic failure after class setup", result.errors[0][1])
        self.assertFalse(FailedClassSetup.workspace.exists())
        self.assertEqual(config.DB_PATH, original_db_path)

    def test_sdk_instance_setup_failure_restores_patches_and_removes_workspace(self):
        original_root = agent_task_service.AGENT_TASK_WORKSPACE_ROOT
        original_notify = agent_task_service._notify_task_finished

        class FailedInstanceSetup(sdk_tests.AgentSdkRuntimeTests):
            def setUp(self):
                with patch.object(database, "get_db_connection", side_effect=RuntimeError("synthetic setup failure")):
                    super().setUp()

            def runTest(self):
                raise AssertionError("setUp must fail first")

        result = self.run_fixture(FailedInstanceSetup())

        self.assertEqual(len(result.errors), 1)
        self.assertIn("synthetic setup failure", result.errors[0][1])
        self.assertIs(agent_task_service.AGENT_TASK_WORKSPACE_ROOT, original_root)
        self.assertIs(agent_task_service._notify_task_finished, original_notify)
        self.assertFalse(FailedInstanceSetup.workspace.exists())

    def test_result_read_failure_removes_generated_deliverable_and_restores_root(self):
        original_root = agent_task_service.AGENT_TASK_WORKSPACE_ROOT
        workspaces = []

        def fail_read(task_id):
            workspace = agent_task_service.AGENT_TASK_WORKSPACE_ROOT
            self.assertTrue((workspace / "tasks" / str(task_id) / "RESULT.md").is_file())
            workspaces.append(workspace)
            raise AssertionError("synthetic assertion during result read")

        case = task_tests.AgentTaskImprovementTests("test_read_task_result_deliverable_reads_workspace_result_md")
        with patch.object(agent_task_service, "read_task_result_deliverable", side_effect=fail_read):
            result = self.run_fixture(case)

        self.assertEqual(len(result.failures), 1)
        self.assertIn("synthetic assertion during result read", result.failures[0][1])
        self.assertEqual(len(workspaces), 1)
        self.assertFalse(workspaces[0].exists())
        self.assertIs(agent_task_service.AGENT_TASK_WORKSPACE_ROOT, original_root)

    def test_package_schema_setup_failure_closes_connection(self):
        case = package_tests.PackageBuildTests("test_raises_lookup_error_for_missing_document")
        with patch.object(package_tests, "ensure_gongwen_schema", side_effect=RuntimeError("synthetic schema failure")):
            result = self.run_fixture(case)

        self.assertEqual(len(result.errors), 1)
        self.assertIn("synthetic schema failure", result.errors[0][1])
        with self.assertRaises(sqlite3.ProgrammingError):
            case.conn.execute("SELECT 1")

    def test_package_assertion_failure_removes_workspace(self):
        class FailedPackageTest(package_tests.PackageBuildTestBase):
            def runTest(self):
                (self.workdir / "partial-attachment.txt").write_text("synthetic", encoding="utf-8")
                self.fail("synthetic package assertion")

        case = FailedPackageTest()
        result = self.run_fixture(case)

        self.assertEqual(len(result.failures), 1)
        self.assertIn("synthetic package assertion", result.failures[0][1])
        self.assertFalse(case.workdir.exists())

    def test_existing_life_tip_cleanup_survives_schema_setup_failure(self):
        original_db_path = config.DB_PATH
        case = life_tip_tests.LifeTipTransactionTests("test_rollback_is_reseeded_by_the_next_real_transaction")
        with patch.object(life_tip_tests.schema, "ensure_life_tip_schema", side_effect=RuntimeError("synthetic tip setup failure")):
            result = self.run_fixture(case)

        self.assertEqual(len(result.errors), 1)
        self.assertIn("synthetic tip setup failure", result.errors[0][1])
        self.assertFalse(Path(case.folder.name).exists())
        self.assertEqual(config.DB_PATH, original_db_path)
