"""ChipRow item validation. The row forwards every item to the chip contract,
so anything the chip learns has to be reachable through the row as well."""
from __future__ import annotations

import unittest

from classroom_app.lq_chip_row import lq_chip_row_props


class LqChipRowTests(unittest.TestCase):
    def row(self, items):
        return lq_chip_row_props("chip_row", id="inboxSource", label="按来源筛选", items=items)

    def test_a_filter_row_can_carry_link_chips(self):
        """URL-driven filters reach the row, not just a standalone chip."""
        result = self.row([
            {"label": "全部", "kind": "filter", "href": "/manage/me/inbox", "pressed": True},
            {"label": "作业", "kind": "filter", "href": "/manage/me/inbox?source=homework"},
        ])
        self.assertEqual(["/manage/me/inbox", "/manage/me/inbox?source=homework"],
                         [item["href"] for item in result["items"]])
        self.assertEqual("按来源筛选", result["attrs"]["aria-label"])

    def test_the_row_enforces_the_same_link_rules_as_a_single_chip(self):
        for item in ({"label": "z", "kind": "status", "href": "/a"},
                     {"label": "z", "kind": "tag", "href": "/a"},
                     {"label": "z", "kind": "filter", "href": "javascript:alert(1)"},
                     {"label": "z", "kind": "filter", "href": "//evil.test/x"}):
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.row([item])

    def test_unknown_item_keys_are_still_rejected(self):
        with self.assertRaises(ValueError):
            self.row([{"label": "z", "kind": "filter", "hrefs": "/a"}])


if __name__ == "__main__":
    unittest.main()
