"""Synthetic end-to-end domain tests using the real package and classroom services."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from classroom_app.database import get_db_connection
from classroom_app.db.schema_project_topic_dispatch import ensure_project_topic_dispatch_schema
from classroom_app.db.schema_project_topic_packages import ensure_project_topic_packages_schema
from classroom_app.services import collaboration_service as groups
from classroom_app.services import file_service
from classroom_app.services import project_topic_dispatch_service as service
from classroom_app.services import project_topic_package_service as packages
from tests.sqlite_database_fixture import isolated_sqlite_database


class ProjectTopicDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        private = Path(cls.enterClassContext(TemporaryDirectory(prefix="project-topic-files-")))
        cls.enterClassContext(patch.object(file_service, "GLOBAL_FILES_DIR", private))
        cls.enterClassContext(patch.object(file_service, "GLOBAL_FILES_LEGACY_DIRS", ()))

    def setUp(self):
        self.conn = get_db_connection()
        self.addCleanup(self.conn.close)
        ensure_project_topic_packages_schema(self.conn)
        ensure_project_topic_dispatch_schema(self.conn)
        self.teacher = {"role": "teacher", "id": 731}
        self.conn.execute("INSERT INTO teachers (id,email,name,hashed_password,is_active) VALUES (731,'qa@example.invalid','QA','x',1)")
        self.conn.execute("INSERT INTO courses (id,name,created_by_teacher_id) VALUES (731,'Python',731)")
        self.conn.execute("INSERT INTO classes (id,name,created_by_teacher_id) VALUES (731,'A',731),(732,'B',731),(733,'Other',731)")
        self.conn.execute("INSERT INTO class_offerings (id,course_id,class_id,teacher_id) VALUES (731,731,731,731),(732,731,733,731)")
        self.conn.execute("INSERT INTO class_offering_class_links (offering_id,class_id,teacher_id) VALUES (731,732,731)")
        self.conn.executemany("INSERT INTO students (id,name,student_id_number,class_id) VALUES (?,?,?,?)",
                              [(i, f"QA {i}", f"00{i}", 731 if i % 2 else 732) for i in range(1, 65)])
        self.conn.execute("INSERT INTO classroom_retake_students (class_offering_id,student_id,status) VALUES (731,64,'confirmed')")
        self.conn.execute("INSERT INTO students (id,name,student_id_number,class_id) VALUES (65,'Other','0065',733)")
        self.package = self.make_package()
        self.config = {"title": "开题", "package_version_id": self.package["id"],
                       "new_scheme": {"name": "随机组", "allocation_mode": "all", "group_count": 13,
                                      "min_members": 4, "max_members": 5}}

    def make_package(self, version="1", key="python", teacher=731):
        topics = []
        files = [("公共说明.txt", b"public")]
        for i in range(1, 16):
            item = {"key": f"topic-{i}", "number": f"{i:02}", "title": f"课程题例 {i}",
                    "pool": "reserve" if i == 14 else "regular", "brief": f"测试题 {i} 的任务方向"}
            if i != 15:
                item["entry"] = f"题{i}/说明.txt"
                files.append((item["entry"], f"{version}: topic {i}".encode()))
            topics.append(item)
        manifest = {"formatVersion": 1, "packageKey": key, "version": version, "title": "题包",
                    "common": ["公共说明.txt"], "topics": topics}
        files.append(("project-topics.json", json.dumps(manifest).encode()))
        return packages.import_package(self.conn, teacher, files)

    @staticmethod
    def student(sid):
        return {"role": "student", "id": sid}

    def preview(self, config):
        return service.preview_dispatch(self.conn, 731, self.teacher, config)

    def publish(self, config=None, request_id="first"):
        config = self.config if config is None else config
        pre = self.preview(config)
        return service.publish_dispatch(self.conn, 731, self.teacher, {
            **config, "request_id": request_id, "preview_token": pre["preview_token"],
        })

    def change(self, activity, action, **settings):
        return service.update_dispatch(self.conn, activity["id"], self.teacher, {
            "revision": activity["revision"], "action": action, "reason": "课堂核对", **settings,
        })

    def test_full_class_balanced_unique_draw_replay_and_private_resources(self):
        pre = self.preview(self.config)
        self.assertTrue(pre["ready"])
        self.assertEqual((pre["student_count"], pre["group_count"], pre["eligible_topic_count"]), (63,13,13))
        activity = self.publish()
        self.assertEqual(sorted(g["member_count"] for g in activity["groups"]), [4]*2 + [5]*11)
        self.assertEqual({g["topic_key"] for g in activity["groups"]}, {f"topic-{i}" for i in range(1,14)})
        replay = service.publish_dispatch(self.conn, 731, self.teacher, {
            **self.config, "request_id": "first", "preview_token": pre["preview_token"],
        })
        self.assertEqual(replay, activity)
        with self.assertRaises(HTTPException) as conflict:
            service.publish_dispatch(self.conn, 731, self.teacher, {
                **self.config, "request_id": "first", "title": "不同项目", "preview_token": pre["preview_token"],
            })
        self.assertEqual(conflict.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            service.publish_dispatch(self.conn, 731, self.teacher, {
                **self.config, "request_id": "separate-tab", "preview_token": pre["preview_token"],
            })
        rows = service.export_dispatch(self.conn, activity["id"], self.teacher)
        self.assertEqual({r["student_number"] for r in rows}, {f"00{i}" for i in range(1,64)})
        student = self.student(1)
        view = service.get_dispatch(self.conn, activity["id"], student)
        own = view["my_topic"]
        self.assertNotIn("groups", view)
        self.assertNotIn("history", view)
        self.assertEqual(service.resolve_dispatch_file(self.conn, activity["id"], student, "公共说明.txt")[0].read_bytes(), b"public")
        personal = service.resolve_dispatch_file(self.conn, activity["id"], student, own["entry"], topic_key=own["key"])
        self.assertTrue(personal[0].read_bytes().startswith(b"1: topic "))
        other = next(g["topic"] for g in activity["groups"] if g["topic_key"] != own["key"])
        for path, scope in [(other["entry"], other["key"]), (other["entry"], own["key"]), (own["entry"], None)]:
            with self.subTest(scope=scope), self.assertRaises(HTTPException):
                service.resolve_dispatch_file(self.conn, activity["id"], student, path, topic_key=scope)
        for user in (self.student(65), {"id": 56, "role": "teacher"}):
            with self.assertRaises(HTTPException):
                service.get_dispatch(self.conn, activity["id"], user)
        with self.assertRaises(HTTPException):
            service.export_dispatch(self.conn, activity["id"], student)

    def test_constraints_insufficient_draft_manual_reuse_and_atomic_rollback(self):
        short = {**self.config, "topic_keys": [f"topic-{i}" for i in range(1,13)]}
        self.assertIn("还差 1 题", self.preview(short)["issues"][0])
        with self.assertRaises(HTTPException):
            self.publish(short)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM group_schemes").fetchone()[0], 0)
        cfg = {**self.config, "mode":"manual", "bindings":[{"group_index":i, "topic_key":f"topic-{14-i}"} for i in range(1,14)]}
        self.conn.execute("SAVEPOINT before_forced_failure")
        with patch.object(service, "_insert_bindings", side_effect=RuntimeError("synthetic insertion fault")):
            with self.assertRaises(RuntimeError):
                self.publish(cfg)
        self.conn.execute("ROLLBACK TO SAVEPOINT before_forced_failure")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM group_schemes").fetchone()[0], 0)
        activity = self.publish(cfg)
        self.assertEqual(activity["groups"][0]["topic_key"], "topic-13")
        existing = {"title":"第二个项目", "scheme_id":activity["scheme_id"], "package_version_id":self.package["id"]}
        second = self.publish(existing, request_id="second")
        self.assertNotEqual(second["id"], activity["id"])
        self.assertEqual(second["scheme_id"], activity["scheme_id"])
        with self.assertRaises(HTTPException):
            service.preview_dispatch(self.conn, 732, self.teacher, existing)
        self.assertFalse(self.preview({**cfg, "bindings":cfg["bindings"][:-1]})["ready"])
        with self.assertRaises(HTTPException):
            self.preview({**cfg, "package_version_id":self.make_package(teacher=732)["id"]})
        keys = [f"topic-{i}" for i in range(1,13)] + ["topic-15"]
        self.assertFalse(self.preview({**self.config,"topic_keys":keys})["ready"])
        self.assertTrue(self.preview({**self.config,"topic_keys":keys,"allow_brief":True})["ready"])

    def test_student_arrival_move_archive_withdraw_restore_and_stale_preview(self):
        cfg = {**self.config, "new_scheme":{**self.config["new_scheme"],"allocation_mode":"self"}}
        activity = self.publish(cfg)
        did = activity["id"]
        student = self.student(1)
        self.assertIsNone(service.get_dispatch(self.conn,did,student)["my_topic"])
        first, second = activity["groups"][:2]
        settings = {"title":"下一批", "scheme_id":activity["scheme_id"], "package_version_id":self.package["id"]}
        pre = self.preview(settings)
        groups.teacher_assign_to_scheme_group(self.conn,first["id"],self.teacher,1)
        self.assertEqual(service.get_dispatch(self.conn,did,student)["my_topic"]["key"],first["topic_key"])
        with self.assertRaises(HTTPException):
            service.publish_dispatch(self.conn,731,self.teacher,{**settings,"request_id":"changed","preview_token":pre["preview_token"]})
        groups.remove_group_member(self.conn,first["id"],self.teacher,1)
        groups.teacher_assign_to_scheme_group(self.conn,second["id"],self.teacher,1)
        with self.assertRaises(HTTPException):
            service.resolve_dispatch_file(self.conn,did,student,first["topic"]["entry"],topic_key=first["topic_key"])
        self.assertEqual(service.get_dispatch(self.conn,did,student)["my_topic"]["key"],second["topic_key"])
        archived = self.change(activity,"archive")
        groups.remove_group_member(self.conn,second["id"],self.teacher,1)
        groups.teacher_assign_to_scheme_group(self.conn,first["id"],self.teacher,2)
        self.assertIsNone(service.get_dispatch(self.conn,did,self.student(2))["my_topic"])
        self.assertEqual(service.get_dispatch(self.conn,did,student)["my_topic"]["key"],second["topic_key"])
        rows = service.export_dispatch(self.conn,did,self.teacher)
        self.assertEqual(sum(r["membership"] == "未分组" for r in rows),62)
        withdrawn = self.change(archived,"withdraw")
        self.assertEqual(service.list_dispatches(self.conn,731,student),[])
        with self.assertRaises(HTTPException):
            service.resolve_dispatch_file(self.conn,did,student,"公共说明.txt")
        with self.assertRaises(HTTPException):
            self.change(archived,"restore")
        restored = self.change(withdrawn,"restore")
        self.assertEqual(restored["state"], "archived")
        self.assertEqual(service.get_dispatch(self.conn,did,student)["my_topic"]["key"],second["topic_key"])

    def test_teacher_swap_version_upgrade_and_protected_work(self):
        activity = self.publish()
        first, second = activity["groups"][:2]
        swap = [{"group_id":first["id"],"topic_key":second["topic_key"]},{"group_id":second["id"],"topic_key":first["topic_key"]}]
        with self.assertRaises(HTTPException):
            self.change(activity,"rebind",bindings=swap[:1])
        self.conn.execute("INSERT INTO group_submissions (group_id,submitted_by_role,submitted_by_user_pk) VALUES (?,'student',1)",(first["id"],))
        with self.assertRaises(HTTPException):
            self.change(activity,"rebind",bindings=swap)
        fixed = self.change(activity,"rebind",bindings=swap,acknowledge_work=True)
        self.assertEqual(fixed["groups"][0]["topic_key"],second["topic_key"])
        other = self.make_package(key="other")
        with self.assertRaises(HTTPException):
            self.change(fixed,"upgrade",package_version_id=other["id"])
        new = self.make_package(version="2")
        revised = self.change(fixed,"upgrade",package_version_id=new["id"])
        self.assertEqual(revised["package_version"], "2")
        other_activity = self.publish({"title":"旧版", "scheme_id":fixed["scheme_id"], "package_version_id":self.package["id"]}, "keep-old")
        self.assertEqual(other_activity["package_version"], "1")
        self.assertEqual([h["action"] for h in revised["history"]], ["upgrade","rebind","publish"])

    def test_incomplete_private_draft_cas_and_publish_keeps_later_edits(self):
        version = self.package["id"]
        def save(revision, payload):
            return service.save_dispatch_draft(self.conn, 731, self.teacher, version,
                                               {"revision": revision, "payload": payload})

        initial = service.get_dispatch_draft(self.conn, 731, self.teacher, version)
        self.assertEqual((initial["revision"], initial["payload"]), (0, None))
        partial = {"title": "待补一题", "topic_keys": ["topic-1"], "bindings": []}
        draft = save(0, partial)
        self.assertEqual(draft["payload"]["title"], partial["title"])
        self.assertEqual(save(0, partial), draft)
        self.assertEqual(service.list_dispatches(self.conn, 731, self.student(1)), [])
        for operation in (service.get_dispatch_draft, service.save_dispatch_draft):
            args = () if operation == service.get_dispatch_draft else ({"revision": 1, "payload": partial},)
            with self.assertRaises(HTTPException):
                operation(self.conn, 731, self.student(1), version, *args)
        with self.assertRaises(HTTPException):
            save(0, self.config)
        ready = save(1, self.config)
        pre = self.preview(self.config)
        late = save(ready["revision"], {**self.config, "title": "另存进度"})
        service.publish_dispatch(self.conn, 731, self.teacher, {
            **self.config, "preview_token": pre["preview_token"], "request_id": "tab-a", "draft_revision": ready["revision"],
        })
        self.assertEqual(service.get_dispatch_draft(self.conn, 731, self.teacher, version), late)
        self.publish({**late["payload"], "draft_revision": late["revision"]}, "tab-b")
        cleared = service.get_dispatch_draft(self.conn, 731, self.teacher, version)
        self.assertEqual((cleared["revision"], cleared["payload"]), (late["revision"] + 1, None))
        with self.assertRaises(HTTPException):
            save(late["revision"], partial)
        self.assertEqual(service.get_dispatch_draft(self.conn, 732, self.teacher, version)["revision"], 0)

    def test_published_group_prevents_self_redraw_but_teacher_can_adjust_and_unbound_groups_can_leave(self):
        wait = {**self.config["new_scheme"], "allocation_mode": "self"}
        scheme = groups.create_group_scheme(self.conn, 731, self.teacher, wait)
        first, second = scheme["groups"][:2]
        groups.teacher_assign_to_scheme_group(self.conn, first["id"], self.teacher, 1)
        groups.teacher_assign_to_scheme_group(self.conn, second["id"], self.teacher, 2)
        activity = self.publish({"title": "部分组派题", "package_version_id": self.package["id"],
                                 "scheme_id": scheme["id"], "group_ids": [first["id"]]})
        with self.assertRaises(HTTPException) as denied:
            groups.leave_group(self.conn, first["id"], self.student(1))
        self.assertEqual(denied.exception.status_code, 409)
        self.assertEqual(groups.random_join_scheme(self.conn, scheme["id"], self.student(1))["group_id"], first["id"])
        groups.leave_group(self.conn, second["id"], self.student(2))
        self.assertIsNone(groups._student_scheme_group(self.conn, scheme["id"], 2))
        groups.remove_group_member(self.conn, first["id"], self.teacher, 1)
        self.assertIsNone(service.get_dispatch(self.conn, activity["id"], self.student(1))["my_topic"])
        groups.teacher_assign_to_scheme_group(self.conn, first["id"], self.teacher, 2)
        self.change(activity, "withdraw")
        groups.leave_group(self.conn, first["id"], self.student(2))
        self.assertIsNone(groups._student_scheme_group(self.conn, scheme["id"], 2))
