"""Org-level identity matching for signature points.

系主任类签名点默认只匹配材料归属教师同系的签名，院长类限同学院；
教师/校长类只要求同校。空要求恒匹配。
"""

from __future__ import annotations

import unittest

from classroom_app.services.signature_point_service import identity_org_match


def _actor(department: str = "网络工程系", college: str = "数字科技学院") -> dict:
    return {
        "role": "teacher",
        "id": 1,
        "scope": {
            "school_code": "gxufl",
            "school_name": "示例大学",
            "college": college,
            "department": department,
        },
        "memberships": [],
    }


def _item(identity: str, department: str = "网络工程系", college: str = "数字科技学院") -> dict:
    return {
        "identity_category": identity,
        "school_code": "gxufl",
        "college": college,
        "department": department,
    }


class SignaturePointOrgFilterTests(unittest.TestCase):
    def test_empty_requirement_matches_everything(self) -> None:
        self.assertTrue(identity_org_match(_actor(), _item(""), set()))

    def test_identity_outside_requirement_fails(self) -> None:
        self.assertFalse(
            identity_org_match(_actor(), _item("teacher"), {"department_head", "vice_department_head"})
        )

    def test_department_head_requires_same_department(self) -> None:
        accepted = {"department_head", "vice_department_head"}
        self.assertTrue(identity_org_match(_actor(), _item("department_head"), accepted))
        self.assertTrue(identity_org_match(_actor(), _item("vice_department_head"), accepted))
        self.assertFalse(
            identity_org_match(_actor(), _item("department_head", department="软件工程系"), accepted)
        )

    def test_dean_requires_same_college(self) -> None:
        accepted = {"dean", "vice_dean"}
        self.assertTrue(identity_org_match(_actor(), _item("dean"), accepted))
        self.assertFalse(
            identity_org_match(_actor(), _item("dean", college="外国语学院", department=""), accepted)
        )

    def test_teacher_identity_has_no_org_restriction(self) -> None:
        self.assertTrue(
            identity_org_match(_actor(), _item("teacher", department="软件工程系"), {"teacher"})
        )


if __name__ == "__main__":
    unittest.main()
