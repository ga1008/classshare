"""Exercise the browser probe's lifecycle without starting any real processes."""
from __future__ import annotations

from contextlib import ExitStack
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.ui import probe_assessment_editor as probe


class AssessmentProbeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='assessment-lifecycle-fixture-')))
        self.evidence = self.root / 'evidence'
        self.directories = []
        self.clock = 0.0
        self.sleeps = []
        self.processes = []

    def _simulate(self, mode):
        original_new_directory = probe._new_directory

        def new_directory(scratch):
            owned = original_new_directory(scratch)
            original_cleanup = owned.cleanup
            # This fixture has no actual children. Restore the real method
            # before fixture cleanup even if a simulated failure/assertion fires.
            self.addCleanup(original_cleanup)
            self.directories.append(owned)

            def cleanup():
                if mode == 'transient' and owned.cleanup.call_count <= 2:
                    # A real rmtree can remove the marker before a locked DB.
                    (Path(owned.name) / probe.OWNER_FILE).unlink(missing_ok=True)
                    raise PermissionError('simulated transient file lock')
                if mode == 'persistent':
                    raise PermissionError('simulated persistent file lock')
                if mode == 'unconfirmed':
                    raise AssertionError('Must not clean an unconfirmed process runtime')
                original_cleanup()

            owned.cleanup = Mock(side_effect=cleanup)
            self.addCleanup(setattr, owned, 'cleanup', original_cleanup)
            return owned

        def spawn(command, **kwargs):
            is_server = 'tests/e2e/scripts/serve_ui_v3.py' in command
            runtime = Path(kwargs['env']['P03_RUNTIME_ROOT'])
            if 'tests/e2e/scripts/prepare_ui_v3_runtime.py' in command:
                (runtime / 'db').mkdir(parents=True)
                (runtime / 'db/classroom.db').write_text('synthetic placeholder', encoding='utf-8')
                (runtime / 'fixture.json').write_text(json.dumps({
                    'runtimeRoot': str(runtime),
                    's3': {'draftAssignmentId': 'draft', 'examDraftAssignmentId': 'exam'},
                }), encoding='utf-8')
            process = Mock(pid=900000 + len(self.processes))
            process.returncode = None if is_server else 0
            process.poll.side_effect = lambda: process.returncode

            def terminate():
                if mode == 'unconfirmed':
                    raise OSError('simulated inability to stop the owned server')
                process.returncode = 1

            process.terminate.side_effect = terminate
            process.wait.side_effect = lambda **kwargs: process.returncode
            self.processes.append(process)
            return process

        def health(*args, **kwargs):
            runtime = Path(self.directories[0].name) / 'runtime'
            return io.StringIO(json.dumps({'database_path': str(runtime / 'db/classroom.db')}))

        def sleep(seconds):
            self.sleeps.append(seconds)
            self.clock += seconds

        with ExitStack() as mocks:
            mocks.enter_context(patch.object(probe, 'ROOT', self.root))
            mocks.enter_context(patch.object(probe, '_new_directory', new_directory))
            mocks.enter_context(patch.object(probe.subprocess, 'Popen', spawn))
            mocks.enter_context(patch.object(probe.urllib.request, 'urlopen', health))
            mocks.enter_context(patch.object(probe.time, 'monotonic', lambda: self.clock))
            mocks.enter_context(patch.object(probe.time, 'sleep', sleep))
            mocks.enter_context(patch.object(probe.sys, 'argv', ['probe', '--evidence', str(self.evidence)]))
            mocks.enter_context(patch('sys.stdout', io.StringIO()))
            result = probe.main()
        report = json.loads((self.evidence / 'runtime-lifecycle.json').read_text(encoding='utf-8'))
        owner = json.loads((self.evidence / 'runtime-owner.json').read_text(encoding='utf-8'))
        fixture = json.loads((self.evidence / 'fixture.json').read_text(encoding='utf-8'))
        self.assertEqual(owner['run_id'], report['run_id'])
        self.assertEqual(owner['owned_directory'], report['owned_directory'])
        self.assertEqual(fixture['runtimeRoot'], report['runtime'])
        self.assertEqual(report['test_exit_code'], 0)
        self.assertEqual(report['exit_code'], result)
        self.assertEqual(report['server_pid'], self.processes[2].pid)
        self.assertEqual(report['layout_exit_codes'], {'teacher': 0, 'student': 0})
        return result, report

    def test_transient_permission_error_retries_and_removes_owned_runtime(self):
        result, report = self._simulate('transient')
        self.assertEqual(result, 0)
        self.assertEqual(report['cleanup']['status'], 'removed')
        self.assertEqual(report['cleanup']['attempts'], 3)
        self.assertEqual(report['server_returncode'], 1)
        self.assertTrue(report['server_exit_confirmed'])
        self.assertTrue(report['runtime_removed'])
        self.assertTrue(report['owned_directory_removed'])
        self.assertEqual(self.sleeps, [.1, .2])
        self.assertEqual(report['errors'], [])

    def test_persistent_permission_error_returns_failure_and_retains_report(self):
        result, report = self._simulate('persistent')
        self.assertNotEqual(result, 0)
        self.assertEqual(report['cleanup']['status'], 'failed')
        self.assertIn('PermissionError', report['cleanup']['error'])
        self.assertEqual(sum(self.sleeps), 10.0)
        self.assertEqual(report['cleanup']['elapsed_seconds'], 10.0)
        self.assertTrue(report['server_exit_confirmed'])
        self.assertFalse(report['runtime_removed'])
        self.assertFalse(report['owned_directory_removed'])
        self.assertTrue(Path(report['owned_directory']).is_dir())

    def test_unconfirmed_server_exit_preserves_directory_without_cleanup(self):
        result, report = self._simulate('unconfirmed')
        self.assertNotEqual(result, 0)
        self.assertIsNone(report['server_returncode'])
        self.assertFalse(report['server_exit_confirmed'])
        self.assertFalse(report['processes_exited'])
        self.assertEqual(report['cleanup']['status'], 'preserved')
        self.assertEqual(report['cleanup']['attempts'], 0)
        self.directories[0].cleanup.assert_not_called()
        self.assertTrue(Path(report['owned_directory']).is_dir())
        self.assertTrue(report['errors'])


if __name__ == '__main__':
    unittest.main()
