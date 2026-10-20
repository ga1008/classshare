from __future__ import annotations

import contextlib
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.dependencies import get_current_teacher
from classroom_app.routers.manage_parts import organization_suggestions as routes
from classroom_app.services import organization_suggestion_service as service
from classroom_app.services.department_service import normalize_department


class OrganizationSuggestionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE teachers(id INTEGER PRIMARY KEY, school_code TEXT, school_name TEXT,
                college TEXT, department TEXT, is_active INTEGER DEFAULT 1);
            CREATE TABLE teacher_organization_memberships(
                id INTEGER PRIMARY KEY,teacher_id INTEGER,school_code TEXT,school_name TEXT,
                college TEXT,department TEXT,is_active INTEGER DEFAULT 1);
            CREATE TABLE organization_schools(school_code TEXT,school_name TEXT,is_active INTEGER DEFAULT 1);
            CREATE TABLE organization_colleges(school_code TEXT,college_name TEXT,is_active INTEGER DEFAULT 1);
            CREATE TABLE organization_departments(school_code TEXT,college_name TEXT,
                department_name TEXT,is_active INTEGER DEFAULT 1);
            INSERT INTO organization_schools VALUES('school-a','合成甲校',1),('school-b','合成乙校',1);
            INSERT INTO organization_colleges VALUES('school-a','信息学院',1),('school-b','外语学院',1);
            INSERT INTO organization_departments VALUES
                ('school-a','信息学院','网络工程系',1),
                ('school-a','信息学院','网络工程',1),
                ('school-a','另一学院','软件工程系',1),
                ('school-b','外语学院','英语系',1);
            INSERT INTO teachers VALUES
                (1,'school-a','合成甲校','信息学院','网工',1),
                (2,'school-a','合成甲校','信息学院','网工系',1),
                (3,'school-b','合成乙校','外语学院','英语',1);
            INSERT INTO teacher_organization_memberships VALUES
                (1,1,'school-a','合成甲校','信息学院','网工',1),
                (2,2,'school-a','合成甲校','信息学院','网工系',1);
        """)
        self.conn.commit()
        service.invalidate_organization_suggestions()

    def tearDown(self):
        service.invalidate_organization_suggestions()
        self.conn.close()

    def test_department_known_aliases_and_suffix_variants_are_consistent(self):
        for alias in ("网络工程", "网络工程系", "网工", "网工系", " 网\t工　系 "):
            self.assertEqual("网络工程系", normalize_department(alias))
        for alias in ("软件工程", "软件工程系", "软工", "软工系"):
            self.assertEqual("软件工程系", normalize_department(alias))
        self.assertEqual("网安系", normalize_department("网安"))
        self.assertNotEqual(normalize_department("网络安全"), normalize_department("网络工程"))

    def test_recommendations_deduplicate_and_never_cross_selected_parents(self):
        result = service.get_organization_suggestions(self.conn, school_name="合成甲校", college="信息学院")
        self.assertEqual("school-a", result["school_code"])
        self.assertEqual(["网络工程系"], result["departments"])
        self.assertIn("网工系", result["department_aliases"]["网络工程系"])
        self.assertNotIn("外语学院", result["colleges"])
        self.assertNotIn("英语系", result["departments"])
        self.assertEqual([], service.get_organization_suggestions(self.conn)["departments"])
        self.assertEqual([], service.get_organization_suggestions(
            self.conn, school_name="新学校", college="信息学院",
        )["departments"])
        self.assertEqual(["英语系"], service.get_organization_suggestions(
            self.conn, school_code="school-b", college="外语学院",
        )["departments"])

    def test_save_uses_school_identifier_and_rejects_stale_code_or_ambiguous_name(self):
        normalized = service.canonicalize_membership_scope(
            self.conn, school_name="合成甲校", college=" 信息学院 ", department="网工系",
        )
        self.assertEqual({
            "school_code": "school-a", "school_name": "合成甲校",
            "college": "信息学院", "department": "网络工程系",
        }, normalized)
        for code, name in (("school-a", "合成乙校"), ("school-a", "新学校"), ("new-code", "合成甲校")):
            with self.subTest(code=code, name=name), self.assertRaisesRegex(ValueError, "学校标识"):
                service.canonicalize_membership_scope(self.conn, school_code=code, school_name=name)
        self.conn.execute("INSERT INTO organization_schools VALUES('school-c','合成甲校',1)")
        with self.assertRaisesRegex(ValueError, "同名学校"):
            service.canonicalize_membership_scope(self.conn, school_name="合成甲校")
        self.assertEqual("school-c", service.canonicalize_membership_scope(
            self.conn, school_code="school-c", school_name="合成甲校",
        )["school_code"])

    def test_free_input_is_preserved_and_does_not_guess_unknown_abbreviations(self):
        normalized = service.canonicalize_membership_scope(
            self.conn, school_name="新学校", college="数科", department="未知专业",
        )
        self.assertEqual("新学校", normalized["school_code"])
        self.assertEqual("数科", normalized["college"])
        self.assertEqual("未知专业系", normalized["department"])
        self.assertEqual([], self.conn.execute(
            "SELECT school_code FROM organization_schools WHERE school_name='新学校'"
        ).fetchall())

    def test_inactive_schools_and_revoked_memberships_do_not_reappear(self):
        self.conn.executescript("""
            INSERT INTO teachers VALUES(4,'revoked','停用归属学校','撤销学院','撤销系',1);
            INSERT INTO teacher_organization_memberships VALUES(4,4,'revoked','停用归属学校','撤销学院','撤销系',0);
            INSERT INTO organization_schools VALUES('disabled','停用学校',0);
            INSERT INTO teachers VALUES(5,'disabled','停用学校','旧学院','旧系',1);
            INSERT INTO teachers VALUES(6,'disabled-teacher','停用教师学校','旧学院','旧系',0);
            INSERT INTO organization_colleges VALUES('school-a','停用学院',0);
            INSERT INTO organization_departments VALUES('school-a','停用学院','旧系',1);
            INSERT INTO teachers VALUES(7,'school-a','合成甲校','停用学院','旧系',1);
        """)
        self.conn.commit()
        result = service.get_organization_suggestions(self.conn, school_code="school-a")
        self.assertEqual({"school-a", "school-b"}, {item["school_code"] for item in result["schools"]})
        self.assertNotIn("停用学院", result["colleges"])
        self.assertNotIn("旧系", result["departments"])

    def test_cache_reuses_snapshot_expires_and_returns_unshared_values(self):
        with patch.object(service, "_load_snapshot", wraps=service._load_snapshot) as loader:
            first = service.get_organization_suggestions(self.conn, school_code="school-a")
            first["schools"][0]["school_name"] = "不能污染缓存"
            service.get_organization_suggestions(self.conn, school_code="school-b")
            self.assertEqual(1, loader.call_count)
            service.invalidate_organization_suggestions(self.conn)
            self.assertNotIn("不能污染缓存", str(service.get_organization_suggestions(self.conn)))
            self.assertEqual(2, loader.call_count)
            with patch.object(service.time, "monotonic", return_value=10**15):
                service.get_organization_suggestions(self.conn)
            self.assertEqual(3, loader.call_count)

    def test_uncommitted_rows_never_leak_to_cache_and_save_never_uses_stale_cache(self):
        service.get_organization_suggestions(self.conn)
        self.conn.execute("INSERT INTO organization_schools VALUES('pending','未提交学校',1)")
        pending = service.get_organization_suggestions(self.conn)
        self.assertIn("pending", {item["school_code"] for item in pending["schools"]})
        self.assertEqual("pending", service.canonicalize_membership_scope(
            self.conn, school_name="未提交学校",
        )["school_code"])
        self.conn.rollback()
        current = service.get_organization_suggestions(self.conn)
        self.assertNotIn("pending", {item["school_code"] for item in current["schools"]})
        self.conn.execute("UPDATE organization_schools SET school_name='新正式校名' WHERE school_code='school-a'")
        self.conn.commit()
        self.assertEqual("school-a", service.canonicalize_membership_scope(
            self.conn, school_name="新正式校名",
        )["school_code"])

    def test_api_requires_teacher_and_only_exposes_organization_names(self):
        app = FastAPI()
        app.include_router(routes.router, prefix="/api/manage")
        with patch.object(routes, "get_db_connection", return_value=contextlib.nullcontext(self.conn)):
            with TestClient(app) as client:
                self.assertEqual(401, client.get("/api/manage/organization-suggestions").status_code)
                app.dependency_overrides[get_current_teacher] = lambda: {"id": 1, "role": "teacher"}
                response = client.get("/api/manage/organization-suggestions", params={"school_name": "合成甲校"})
                self.assertEqual(200, response.status_code)
                self.assertEqual("school-a", response.json()["school_code"])
                self.assertNotIn("teacher_id", response.text)
                self.assertNotIn("email", response.text)
                self.assertEqual(422, client.get(
                    "/api/manage/organization-suggestions", params={"school_name": "x" * 161},
                ).status_code)


if __name__ == "__main__":
    unittest.main()
