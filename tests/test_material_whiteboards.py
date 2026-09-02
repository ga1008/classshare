"""材料白板（material_whiteboards）后端测试：schema 幂等、读写回读、隔离、乐观锁、校验、角色门禁、软删。

用内存 sqlite + 最小 course_materials/teachers 表，直接打 FastAPI 路由（patch 路由模块的
get_db_connection，override get_current_user）。
"""

import math
import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_ENGINE", "sqlite")

from fastapi.testclient import TestClient

import classroom_app.db.schema_material_whiteboards as schema_wb
from classroom_app.app import app
from classroom_app.dependencies import get_current_user
from classroom_app.routers import material_whiteboards as router_mod
from classroom_app.services import material_whiteboard_service as svc

SCHEMA = """
CREATE TABLE teachers (
    id INTEGER PRIMARY KEY,
    name TEXT,
    is_super_admin INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1
);
CREATE TABLE course_materials (
    id INTEGER PRIMARY KEY,
    teacher_id INTEGER NOT NULL,
    root_id INTEGER,
    material_path TEXT NOT NULL,
    name TEXT NOT NULL,
    node_type TEXT NOT NULL DEFAULT 'file',
    scope_level TEXT NOT NULL DEFAULT 'private'
);
"""

TEACHER_OWNER = {"id": 11, "role": "teacher", "name": "李老师"}
TEACHER_OTHER = {"id": 12, "role": "teacher", "name": "王老师"}
STUDENT = {"id": 901, "role": "student", "name": "小明"}
MATERIAL_PUBLIC = 501
MATERIAL_PRIVATE = 502
BASE = f"/api/materials/{MATERIAL_PUBLIC}/whiteboards"


def _payload(elements=None, name="板一", base_version=None, viewport=None):
    return {
        "name": name,
        "viewport": {"scale": 1.2, "x": 10, "y": -5} if viewport is None else viewport,
        "elements": [{"type": "stroke", "points": [[1, 2], [3, 4]]}] if elements is None else elements,
        "schema_version": 2,
        "base_version": base_version,
    }


class MaterialWhiteboardApiTests(unittest.TestCase):
    def setUp(self):
        # 每个用例全新内存库：重置模块级 _SCHEMA_READY，确保建表真的执行。
        schema_wb._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO teachers (id, name) VALUES (?, ?)", (TEACHER_OWNER["id"], "李老师"))
        self.conn.execute("INSERT INTO teachers (id, name) VALUES (?, ?)", (TEACHER_OTHER["id"], "王老师"))
        self.conn.execute(
            "INSERT INTO course_materials (id, teacher_id, root_id, material_path, name, scope_level) "
            "VALUES (?, ?, ?, ?, ?, 'public')",
            (MATERIAL_PUBLIC, TEACHER_OWNER["id"], MATERIAL_PUBLIC, "wb-test/公开.md", "公开材料"),
        )
        self.conn.execute(
            "INSERT INTO course_materials (id, teacher_id, root_id, material_path, name, scope_level) "
            "VALUES (?, ?, ?, ?, ?, 'private')",
            (MATERIAL_PRIVATE, TEACHER_OWNER["id"], MATERIAL_PRIVATE, "wb-test/私有.md", "私有材料"),
        )
        self.conn.commit()

        self.client = TestClient(app)
        self.previous_override = app.dependency_overrides.get(get_current_user)
        self._db_patch = patch.object(router_mod, "get_db_connection", return_value=self.conn)
        self._db_patch.start()
        self._login(TEACHER_OWNER)

    def tearDown(self):
        self._db_patch.stop()
        if self.previous_override is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = self.previous_override
        self.conn.close()
        schema_wb._SCHEMA_READY = False

    def _login(self, user):
        app.dependency_overrides[get_current_user] = lambda: dict(user)

    # ------------------------------------------------------------------ schema
    def test_ensure_schema_is_idempotent(self):
        schema_wb.ensure_material_whiteboard_schema(self.conn)
        schema_wb._SCHEMA_READY = False
        schema_wb.ensure_material_whiteboard_schema(self.conn)
        tables = {
            row["name"]
            for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        self.assertIn("material_whiteboards", tables)
        indexes = {
            row["name"]
            for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        self.assertIn("idx_material_whiteboards_owner", indexes)
        self.assertTrue(schema_wb._SCHEMA_READY)

    # --------------------------------------------------------------- roundtrip
    def test_upsert_list_get_roundtrip(self):
        elements = [
            {"type": "stroke", "points": [[0, 0], [5, 5]], "width": 2.5},
            {"type": "shape", "kind": "rect", "x": 1, "y": 2, "w": 30, "h": 40},
            {"type": "text", "x": 3, "y": 4, "text": "你好"},
            {"type": "eraser", "points": [[1, 1]]},
        ]
        resp = self.client.put(f"{BASE}/b-1", json=_payload(elements=elements))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        board = body["board"]
        self.assertEqual(board["board_key"], "b-1")
        self.assertEqual(board["name"], "板一")
        self.assertEqual(board["version"], 1)
        # element_count 只统计墨迹元素（橡皮不计）
        self.assertEqual(board["element_count"], 3)
        self.assertEqual(board["visibility"], "private")
        self.assertEqual(board["viewport"]["scale"], 1.2)
        self.assertEqual(board["elements"], elements)
        self.assertTrue(board["created_at"] and board["updated_at"])

        listed = self.client.get(BASE).json()
        self.assertEqual(listed["status"], "ok")
        self.assertEqual(len(listed["boards"]), 1)
        meta = listed["boards"][0]
        self.assertNotIn("elements", meta)
        self.assertEqual(meta["board_key"], "b-1")
        self.assertEqual(meta["element_count"], 3)
        self.assertEqual(meta["version"], 1)

        fetched = self.client.get(f"{BASE}/b-1").json()["board"]
        self.assertEqual(fetched["elements"], elements)
        self.assertEqual(fetched["element_count"], 3)

        # 二次保存：base_version 匹配 -> version 递增，element_count 跟随变化
        again = self.client.put(f"{BASE}/b-1", json=_payload(elements=elements[:2], base_version=1))
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["board"]["version"], 2)
        self.assertEqual(again.json()["board"]["element_count"], 2)

    def test_viewport_scale_is_clamped(self):
        resp = self.client.put(f"{BASE}/b-scale", json=_payload(viewport={"scale": 9.0}))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["board"]["viewport"]["scale"], svc.VIEWPORT_SCALE_MAX)
        low = self.client.put(f"{BASE}/b-scale", json=_payload(viewport={"scale": 0.01}, base_version=1))
        self.assertEqual(low.json()["board"]["viewport"]["scale"], svc.VIEWPORT_SCALE_MIN)

    def test_rename_and_missing_board(self):
        self.client.put(f"{BASE}/b-rn", json=_payload())
        resp = self.client.patch(f"{BASE}/b-rn", json={"name": "新名字"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["board"]["name"], "新名字")
        self.assertEqual(self.client.get(f"{BASE}/b-rn").json()["board"]["name"], "新名字")

        self.assertEqual(self.client.get(f"{BASE}/nope").status_code, 404)
        self.assertEqual(self.client.patch(f"{BASE}/nope", json={"name": "x"}).status_code, 404)
        self.assertEqual(self.client.delete(f"{BASE}/nope").status_code, 404)
        too_long = self.client.patch(f"{BASE}/b-rn", json={"name": "长" * 61})
        self.assertEqual(too_long.status_code, 400)

    # --------------------------------------------------------------- isolation
    def test_other_teacher_sees_empty_list(self):
        self.client.put(f"{BASE}/b-1", json=_payload())
        self._login(TEACHER_OTHER)
        listed = self.client.get(BASE)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["boards"], [])
        self.assertEqual(self.client.get(f"{BASE}/b-1").status_code, 404)

    def test_other_teacher_cannot_touch_private_material(self):
        self._login(TEACHER_OTHER)
        resp = self.client.get(f"/api/materials/{MATERIAL_PRIVATE}/whiteboards")
        # 服务抛 403；app.py 的 403 处理器对无 cookie 的 API 请求重写为 401。
        self.assertEqual(resp.status_code, 401, resp.text)

    # ---------------------------------------------------------- optimistic lock
    def test_stale_base_version_returns_conflict_with_server_copy(self):
        self.client.put(f"{BASE}/b-1", json=_payload())
        ok = self.client.put(f"{BASE}/b-1", json=_payload(name="第二版", base_version=1))
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["board"]["version"], 2)

        stale = self.client.put(
            f"{BASE}/b-1",
            json=_payload(name="过期写入", elements=[{"type": "text", "text": "x"}], base_version=1),
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        body = stale.json()
        self.assertEqual(body["status"], "conflict")
        self.assertEqual(body["board"]["version"], 2)
        self.assertEqual(body["board"]["name"], "第二版")
        self.assertEqual(body["board"]["elements"], _payload()["elements"])
        # 服务端内容未被过期写入覆盖
        self.assertEqual(self.client.get(f"{BASE}/b-1").json()["board"]["name"], "第二版")

    def test_missing_base_version_on_existing_board_conflicts(self):
        self.client.put(f"{BASE}/b-1", json=_payload())
        resp = self.client.put(f"{BASE}/b-1", json=_payload(base_version=None))
        self.assertEqual(resp.status_code, 409)

    # -------------------------------------------------------------- validation
    def test_bad_element_type_rejected(self):
        resp = self.client.put(f"{BASE}/b-bad", json=_payload(elements=[{"type": "image"}]))
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_non_list_elements_rejected(self):
        resp = self.client.put(f"{BASE}/b-bad", json=_payload(elements={"type": "stroke"}))
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_too_many_elements_rejected(self):
        elements = [{"type": "stroke", "points": [[0, 0]]}] * (svc.MAX_ELEMENTS + 1)
        resp = self.client.put(f"{BASE}/b-many", json=_payload(elements=elements))
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_nan_rejected_by_service(self):
        # JSON 本身传不了 NaN；直接打服务层验证 math.isfinite 守门。
        with self.assertRaises(svc.WhiteboardValidationError):
            svc.upsert_board(
                self.conn, TEACHER_OWNER, MATERIAL_PUBLIC, "b-nan",
                _payload(elements=[{"type": "stroke", "points": [[math.nan, 1]]}]), None,
            )
        with self.assertRaises(svc.WhiteboardValidationError):
            svc.upsert_board(
                self.conn, TEACHER_OWNER, MATERIAL_PUBLIC, "b-inf",
                _payload(viewport={"scale": 1.0, "x": math.inf}), None,
            )

    def test_name_too_long_rejected(self):
        resp = self.client.put(f"{BASE}/b-name", json=_payload(name="名" * 61))
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_oversized_payload_returns_413(self):
        elements = [{"type": "text", "text": "a" * (svc.MAX_ELEMENTS_BYTES + 1024)}]
        resp = self.client.put(f"{BASE}/b-big", json=_payload(elements=elements))
        self.assertEqual(resp.status_code, 413, resp.text[:200])

    # ---------------------------------------------------------------- role gate
    def test_student_role_is_rejected(self):
        self._login(STUDENT)
        resp = self.client.get(BASE)
        # 服务抛 HTTPException(403)；app.py 对无 cookie 的 API 请求把 403 重写为 401。
        self.assertEqual(resp.status_code, 401, resp.text)
        put = self.client.put(f"{BASE}/b-1", json=_payload())
        self.assertEqual(put.status_code, 401)

    # ---------------------------------------------------------------- soft delete
    def test_soft_deleted_board_absent_from_list(self):
        self.client.put(f"{BASE}/b-1", json=_payload())
        self.client.put(f"{BASE}/b-2", json=_payload(name="板二"))
        resp = self.client.delete(f"{BASE}/b-1")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "ok")

        keys = [b["board_key"] for b in self.client.get(BASE).json()["boards"]]
        self.assertEqual(keys, ["b-2"])
        self.assertEqual(self.client.get(f"{BASE}/b-1").status_code, 404)
        # 行仍在库中（软删）
        row = self.conn.execute(
            "SELECT deleted_at FROM material_whiteboards WHERE board_key = 'b-1'"
        ).fetchone()
        self.assertIsNotNone(row["deleted_at"])

        # 同 key 再次保存可复活为全新白板
        revived = self.client.put(f"{BASE}/b-1", json=_payload(name="复活"))
        self.assertEqual(revived.status_code, 200, revived.text)
        self.assertEqual(revived.json()["board"]["version"], 1)
        self.assertEqual(revived.json()["board"]["name"], "复活")


if __name__ == "__main__":
    unittest.main()
