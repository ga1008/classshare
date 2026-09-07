from __future__ import annotations

import asyncio
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from PIL import Image, ImageDraw

from classroom_app.services import signature_service as service, signature_scope_service as scopes


class SignatureVisibilityHierarchyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT, email TEXT DEFAULT '',
                school_code TEXT, school_name TEXT DEFAULT '', college TEXT, department TEXT,
                is_active INTEGER DEFAULT 1, is_super_admin INTEGER DEFAULT 0, identity_category TEXT DEFAULT '');
            CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, school_code TEXT,
                school_name TEXT DEFAULT '', college TEXT, department TEXT, class_id INTEGER, identity_category TEXT DEFAULT '');
            CREATE TABLE classes (id INTEGER PRIMARY KEY, school_code TEXT, school_name TEXT DEFAULT '', college TEXT, department TEXT);
            CREATE TABLE teacher_organization_memberships (id INTEGER PRIMARY KEY, teacher_id INTEGER,
                school_code TEXT, school_name TEXT DEFAULT '', college TEXT, department TEXT,
                is_active INTEGER DEFAULT 1, is_primary INTEGER DEFAULT 0, updated_at TEXT);
            CREATE TABLE organization_schools (school_code TEXT, school_name TEXT, is_active INTEGER DEFAULT 1);
            CREATE TABLE organization_colleges (school_code TEXT, college_name TEXT, is_active INTEGER DEFAULT 1);
            CREATE TABLE organization_departments (school_code TEXT, college_name TEXT, department_name TEXT, is_active INTEGER DEFAULT 1);
            CREATE TABLE signature_usage_logs (id INTEGER PRIMARY KEY, signature_id INTEGER, created_at TEXT);
            CREATE TABLE signature_access_requests (id INTEGER PRIMARY KEY, signature_id INTEGER,
                requester_role TEXT, requester_id INTEGER, status TEXT, requested_at TEXT, reviewed_at TEXT, review_note TEXT);
            INSERT INTO organization_schools VALUES ('a', 'A校', 1), ('b', 'B校', 1);
            INSERT INTO organization_colleges VALUES ('a', '甲学院', 1), ('a', '乙学院', 1), ('b', '甲学院', 1);
            INSERT INTO organization_departments VALUES ('a', '甲学院', '网络工程系', 1), ('a', '乙学院', '网络工程系', 1);
            INSERT INTO teachers (id,name,school_code,college,department) VALUES
                (1,'教师一','a','甲学院','网络工程系'), (2,'归属人','a','甲学院','网络工程系'),
                (3,'同院','a','甲学院','软件工程系'), (4,'同名跨院','a','乙学院','网络工程系'),
                (5,'跨校','b','甲学院','网络工程系'), (6,'已停用归属','a','甲学院','网络工程系'),
                (7,'无组织','','',''), (9,'超管','b','乙学院','其他系');
            UPDATE teachers SET is_super_admin = 1 WHERE id = 9;
            INSERT INTO teacher_organization_memberships (id,teacher_id,school_code,college,department,is_active,is_primary)
                VALUES (1,1,'a','甲学院','网络工程系',1,1), (2,1,'b','甲学院','软件工程系',1,0), (3,6,'a','甲学院','网络工程系',0,1);
            INSERT INTO classes VALUES (1,'a','','甲学院','网络工程系'), (2,'a','','甲学院','软件工程系'), (3,'b','','甲学院','网络工程系');
            INSERT INTO students (id,name,school_code,college,department,class_id) VALUES
                (1,'同系学生','a','甲学院','网络工程系',1), (2,'归属学生','b','甲学院','网络工程系',3),
                (3,'旧占位','a','甲学院','甲学院系',1), (4,'跨院冲突','a','乙学院','甲学院系',1),
                (5,'缺学校','','甲学院','',1), (6,'空系','a','甲学院','',2);
        """)
        texts = "name subject_name subject_role owner_role owner_name_snapshot uploaded_by_role uploaded_by_name_snapshot scope_level school_code school_name college department file_hash file_ext mime_type stored_path description legacy_source metadata_json ownership_updated_at created_at updated_at".split()
        self.conn.execute("CREATE TABLE electronic_signatures (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, subject_id INTEGER, uploaded_by_id INTEGER, identity_verified INTEGER DEFAULT 0, identity_category TEXT DEFAULT '', signature_kind TEXT DEFAULT 'personal', file_size INTEGER DEFAULT 0, status TEXT DEFAULT 'active', deleted_at TEXT, ownership_updated_by_teacher_id INTEGER, " + ",".join(f"{key} TEXT DEFAULT ''" for key in texts) + ")")
        self.patches = [
            patch("classroom_app.services.organization_scope_service.get_configured_db_engine", return_value="sqlite"),
            patch.object(service, "is_super_admin_teacher", side_effect=lambda conn, tid: tid == 9),
            patch.object(service, "_signature_school_options", return_value=[]),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self.conn.close)
        for item in self.patches:
            self.addCleanup(item.stop)

    def actor(self, role="teacher", user_id=1):
        return service.build_signature_actor(self.conn, {"role": role, "id": user_id})

    def signature(self, level, **values):
        data = dict(name="签名", subject_name="归属人", subject_role="teacher", subject_id=2,
                    owner_role="teacher", owner_id=2, scope_level=level,
                    school_code="a", college="甲学院", department="网络工程系")
        data.update(values)
        keys = list(data)
        cur = self.conn.execute(f"INSERT INTO electronic_signatures ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", [data[key] for key in keys])
        return self.conn.execute("SELECT * FROM electronic_signatures WHERE id=?", (cur.lastrowid,)).fetchone()

    def test_scope_matrix_and_list_sql_are_identical(self):
        expected = {
            ("teacher", 1): {"platform", "school", "college", "department"},
            ("teacher", 2): set(scopes.SCOPE_LABELS),
            ("teacher", 3): {"platform", "school", "college"},
            ("teacher", 4): {"platform", "school"},
            ("teacher", 5): {"platform"},
            ("teacher", 6): {"platform"},
            ("teacher", 7): {"platform"},
            ("teacher", 9): set(scopes.SCOPE_LABELS),
            ("student", 1): {"platform", "school", "college", "department"},
            ("student", 2): {"platform"},
        }
        rows = [self.signature(level) for level in scopes.SCOPE_LABELS]
        for (role, uid), visible_levels in expected.items():
            with self.subTest(role=role, uid=uid):
                actor = self.actor(role, uid)
                self.assertEqual(visible_levels, {row["scope_level"] for row in rows if service.can_view_signature(actor, row)})
                listed = service.list_signatures(self.conn, {"role": role, "id": uid})
                self.assertEqual(visible_levels, {row["scope_level"] for row in listed["items"]})
                self.assertEqual(len(visible_levels), listed["total"])
                self.assertTrue(all(row["can_view"] for row in listed["items"]))

    def test_secondary_school_and_explicit_filter(self):
        secondary = self.signature("department", school_code="b", department="软件工程系")
        first = self.signature("school")
        foreign = self.signature("platform", school_code="b")
        self.assertTrue(service.can_view_signature(self.actor(), secondary))
        for uid in (1, 9):
            all_rows = service.list_signatures(self.conn, {"role": "teacher", "id": uid})
            self.assertEqual(3, all_rows["total"])
            selected = service.list_signatures(self.conn, {"role": "teacher", "id": uid}, school_code="b")
            self.assertEqual({secondary["id"], foreign["id"]}, {row["id"] for row in selected["items"]})
            self.assertNotIn(first["id"], {row["id"] for row in selected["items"]})
        self.assertEqual(1, service.list_signatures(self.conn, {"role": "teacher", "id": 5}, scope="platform")["total"])

    def test_missing_anchor_unknown_scope_and_system_owner_do_not_broaden(self):
        for level, fields in scopes.SCOPE_FIELDS.items():
            for field in fields:
                row = self.signature(level, owner_role="system", subject_id=None, **{field: ""})
                self.assertFalse(service.can_view_signature(self.actor(), row))
        row = self.signature("invalid", owner_role="system", subject_id=None)
        self.assertFalse(service.can_view_signature(self.actor(), row))
        self.assertEqual(0, service.list_signatures(self.conn, {"role": "teacher", "id": 1})["total"])

    def test_students_only_inherit_compatible_class(self):
        self.assertEqual("网络工程系", self.actor("student", 3)["scope"]["department"])
        self.assertEqual("", self.actor("student", 4)["scope"]["department"])
        self.assertEqual([], self.actor("student", 5)["memberships"])
        self.assertEqual("软件工程系", self.actor("student", 6)["scope"]["department"])
        self.assertEqual([], self.actor(user_id=6)["memberships"])
        self.conn.execute("UPDATE teachers SET is_active=0 WHERE id=5")
        with self.assertRaises(service.SignatureServiceError):
            self.actor(user_id=5)

    def test_ownership_subject_and_admin_are_role_aware(self):
        row = self.signature("personal", owner_id=1, subject_id=2, subject_role="student")
        self.assertTrue(service.can_use_signature(self.actor(), row))
        self.assertTrue(service.can_use_signature(self.actor("student", 2), row))
        self.assertFalse(service.can_view_signature(self.actor("student", 1), row))
        self.assertFalse(service.can_use_signature(self.actor(user_id=9), row))
        mine = service.list_signatures(self.conn, {"role": "student", "id": 2}, scope="mine")
        self.assertEqual(1, mine["total"])
        self.assertTrue(mine["items"][0]["is_subject"])

    def test_stamps_still_require_visibility_and_claims_are_visible(self):
        row = self.signature("department", signature_kind="stamp", subject_role="other", subject_id=None)
        self.assertTrue(service.can_use_signature(self.actor(), row))
        self.assertFalse(service.can_use_signature(self.actor(user_id=4), row))
        row = self.signature("platform", subject_name="教师一", subject_id=None)
        self.assertTrue(service.can_claim_signature(self.actor(), row))
        self.conn.execute("UPDATE electronic_signatures SET scope_level='personal' WHERE id=?", (row["id"],))
        row = self.conn.execute("SELECT * FROM electronic_signatures WHERE id=?", (row["id"],)).fetchone()
        self.assertFalse(service.can_claim_signature(self.actor(), row))

    def test_owner_can_save_all_five_scopes_and_title_keeps_department(self):
        row = self.signature("department", owner_id=1, subject_id=1, subject_name="教师一")
        for level in scopes.SCOPE_LABELS:
            result = service.update_signature_metadata(self.conn, {"role": "teacher", "id": 1}, row["id"], {"scope_level": level, "identity_category": "dean"})
            self.assertEqual(level, result["scope_level"])
            self.assertEqual("网络工程系", result["department"])
        for payload in ({"scope_level": "class"}, {"scope_level": "college", "college": "乙学院"}, {"scope_level": "school", "school_code": "alien"}):
            with self.assertRaises(service.SignatureServiceError):
                service.update_signature_metadata(self.conn, {"role": "teacher", "id": 1}, row["id"], payload)
        secondary = service.update_signature_metadata(self.conn, {"role": "teacher", "id": 1}, row["id"], {"scope_level": "department", "school_code": "b", "college": "甲学院", "department": "软件工程系"})
        self.assertEqual("b", secondary["school_code"])

    def test_admin_validates_full_organization_anchor(self):
        actor = self.actor(user_id=9)
        self.assertEqual("甲学院", service._resolve_visibility_organization(self.conn, actor, "college", {"school_code": "a", "college": "甲学院"})["college"])
        for level, payload in (("school", {"school_code": "unknown"}), ("college", {"school_code": "a", "college": ""}), ("department", {"school_code": "a", "college": "不存在", "department": "网络工程系"})):
            with self.assertRaises(service.SignatureServiceError):
                service._resolve_visibility_organization(self.conn, actor, level, payload)

    def test_upload_and_import_honor_explicit_scope(self):
        image = Image.new("RGB", (160, 60), "white")
        ImageDraw.Draw(image).line([(10, 45), (90, 10), (145, 40)], fill="black", width=5)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        with tempfile.TemporaryDirectory() as directory, patch.object(service, "SIGNATURES_DIR", Path(directory)):
            for level in scopes.SCOPE_LABELS:
                result = asyncio.run(service.create_signature_from_upload(self.conn, {"role": "student", "id": 1}, UploadFile(io.BytesIO(data), filename="signature.png"), scope_level=level))
                self.assertEqual(level, result["scope_level"])
                self.assertEqual(1, result["subject_id"])
            result = asyncio.run(service.create_signature_from_bytes(self.conn, {"role": "teacher", "id": 1}, data, scope_level="college", subject_name="教师一"))
            self.assertEqual("college", result["scope_level"])

    def test_teacher_options_and_subject_resolution_include_active_secondary_school(self):
        result = service.list_signature_teacher_options(self.conn, {"role": "teacher", "id": 9}, school_code="b")
        self.assertIn(1, {item["id"] for item in result["items"]})
        for explicit in (1, None):
            self.assertEqual(1, service._resolve_subject_id(self.conn, subject_role="teacher", subject_name="教师一", explicit_id=explicit, school_code="b"))
        result = service.list_signature_teacher_options(self.conn, {"role": "teacher", "id": 9}, school_code="a")
        self.assertNotIn(6, {item["id"] for item in result["items"]})

    def test_transfer_to_teacher_in_secondary_school(self):
        row = self.signature("department", school_code="b", owner_id=5, subject_id=5)
        result = service.update_signature_metadata(self.conn, {"role": "teacher", "id": 5}, row["id"], {"owner_teacher_id": 1})
        self.assertEqual(1, result["owner_id"])
        self.assertEqual("b", result["school_code"])
        with self.assertRaises(service.SignatureServiceError):
            service.update_signature_metadata(self.conn, {"role": "teacher", "id": 1}, row["id"], {"owner_teacher_id": 6})

    def test_legacy_whitespace_uses_same_sql_and_detail_normalization(self):
        row = self.signature("department", school_code=" a\t", college="\r甲学院\n", department="网络工程系\u3000")
        self.assertTrue(service.can_view_signature(self.actor(), row))
        self.assertEqual(1, service.list_signatures(self.conn, {"role": "teacher", "id": 1})["total"])


if __name__ == "__main__":
    unittest.main()
