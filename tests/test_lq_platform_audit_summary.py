import json
from pathlib import Path
import tempfile
import unittest

from tools.ui.summarize_lq_platform_audit import summarize


class LqPlatformAuditSummaryTests(unittest.TestCase):
    def test_failure_before_artifact_is_not_lost_when_no_route_record_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = {'stats': {'expected': 0, 'unexpected': 1}, 'suites': [{'suites': [{
                'specs': [{'title': 'student 1440 dark', 'tests': [{'status': 'unexpected',
                    'results': [{'status': 'failed', 'errors': [{'message': 'ERR_CONNECTION_REFUSED'}]}]}]}]
            }]}]}
            (root / 'playwright-results.json').write_text(json.dumps(run), encoding='utf-8')
            result = summarize(root)
            self.assertEqual(result['summary']['groups'], 0)
            self.assertEqual(result['summary']['routeGaps'], 0)
            self.assertEqual(result['summary']['executionFailures'], 1)
            self.assertEqual(result['executionFailures'][0]['title'], 'student 1440 dark')
            self.assertEqual(result['acceptance'], 'pending')


if __name__ == '__main__':
    unittest.main()
