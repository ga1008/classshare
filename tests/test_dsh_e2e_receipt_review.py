"""Post-run evaluation of preserved c3 receipt; never invokes a model or DB."""
import copy
import json
from pathlib import Path
import unittest

from tools.dsh_isolated_e2e_app import platform_read_verified


class DshE2eReceiptReviewTests(unittest.TestCase):
    def test_preserved_teacher_scoped_query_and_fail_closed_variants(self):
        path = Path(__file__).resolve().parents[1] / 'docs/agent-dsh-c3-teacher-query-receipt-2026-09-10.json'
        receipt = json.loads(path.read_text(encoding='utf-8'))['tool_receipt']
        self.assertTrue(platform_read_verified([receipt], 'teacher'))
        self.assertFalse(platform_read_verified([receipt], 'student'))
        for changes in ({'status': 'failed'}, {'rawInput': {'query': 'other_query'}}, {'content': []}):
            altered = {**copy.deepcopy(receipt), **changes}
            self.assertFalse(platform_read_verified([altered], 'teacher'))
        for body in ('{}', 'not json', '[]', '{"status":"error"}'):
            altered = copy.deepcopy(receipt)
            altered['content'][0]['content']['text'] = body
            self.assertFalse(platform_read_verified([altered], 'teacher'))


if __name__ == '__main__': unittest.main()
