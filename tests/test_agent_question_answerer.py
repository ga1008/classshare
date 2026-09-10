"""Execute the actual image plugin in Node; no model or platform keys."""
from pathlib import Path
import shutil
import subprocess
import unittest


class QuestionAnswererTests(unittest.TestCase):
    def test_native_image_plugin_wait_and_cleanup(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime unavailable')
        result = subprocess.run([node, str(Path(__file__).parent / 'fixtures/dsh_question_answerer.mjs')],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('startup passed', result.stdout)
