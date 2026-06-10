"""公文同步分页 — 上游只认 ``page`` 参数（pageNo 被忽略时曾把首页重复入库 16 次）。"""

import contextlib
import os
import sqlite3
import unittest
from unittest.mock import patch

# 引擎必须在导入 schema 模块前固定（宿主机 .env 是 DB_ENGINE=postgres）。
os.environ["DB_ENGINE"] = "sqlite"

import classroom_app.db.schema_gongwen as schema_gongwen
import classroom_app.services.gongwen_document_sync_service as sync_mod
from classroom_app.db.schema_gongwen import ensure_gongwen_schema

PAGE_SIZE_FOR_TEST = 2


def _doc_item(doc_id: int, publish: str = "2026-06-01 09:00:00") -> dict:
    return {
        "id": str(doc_id),
        "sn": f"测发〔2026〕{doc_id}号",
        "title": f"测试公文 {doc_id}",
        "publishTime": publish,
        "createAt": publish,
        "docCate": {"id": "1", "name": "测试分类"},
        "user": {"realName": "测试员"},
    }


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """分页表驱动的假上游：``honors_page=False`` 模拟忽略 page 参数的旧行为。"""

    def __init__(self, pages: list[list[dict]], *, honors_page: bool = True):
        self.pages = pages
        self.honors_page = honors_page
        self.requested_params: list[dict] = []

    async def get(self, url, params=None, headers=None):
        self.requested_params.append(dict(params or {}))
        requested = int((params or {}).get("page") or 1)
        effective = requested if self.honors_page else 1
        index = min(max(effective, 1), len(self.pages)) - 1
        total_rows = sum(len(p) for p in self.pages)
        return _FakeResponse({
            "code": 0,
            "result": {
                "list": list(self.pages[index]),
                "pageNumber": effective,
                "pageSize": PAGE_SIZE_FOR_TEST,
                "totalPage": len(self.pages),
                "totalRow": total_rows,
            },
        })


class GongwenSyncPaginationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        schema_gongwen._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_gongwen_schema(self.conn)
        self.addCleanup(self.conn.close)

        @contextlib.contextmanager
        def _shared_conn():
            yield self.conn

        for target, value in (
            ("get_db_connection", _shared_conn),
            ("PAGE_SIZE", PAGE_SIZE_FOR_TEST),
            ("PAGE_DELAY_SECONDS", 0),
            ("load_teacher_org_scope", lambda conn, tid: {"school_code": "sch", "school_name": "测试校区"}),
            ("load_teacher_gongwen_access_method", lambda conn, tid: {"system_code": "gxufl", "credential_id": 7}),
        ):
            patcher = patch.object(sync_mod, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _patch_client(self, client: _FakeClient) -> None:
        profile = sync_mod.get_gongwen_system_profile("gxufl")

        @contextlib.asynccontextmanager
        async def _fake_open(access):
            yield client, profile, "token"

        patcher = patch.object(sync_mod, "open_authenticated_gongwen_client", _fake_open)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stored_remote_ids(self) -> set[str]:
        rows = self.conn.execute("SELECT remote_id FROM gongwen_documents").fetchall()
        return {row["remote_id"] for row in rows}

    async def test_sweep_walks_all_pages_with_page_param(self):
        # Arrange: 3 pages × 2 docs, server honors ``page``.
        pages = [[_doc_item(1), _doc_item(2)], [_doc_item(3), _doc_item(4)], [_doc_item(5), _doc_item(6)]]
        client = _FakeClient(pages, honors_page=True)
        self._patch_client(client)

        # Act
        result = await sync_mod.sync_current_teacher_gongwen_documents(11)

        # Assert: all 6 distinct docs stored exactly once, requests used ``page``.
        self.assertEqual(result["status"], "success")
        self.assertEqual(self._stored_remote_ids(), {"1", "2", "3", "4", "5", "6"})
        self.assertEqual(result["counts"]["new"], 6)
        self.assertEqual(result["counts"]["stored"], 6)
        self.assertEqual([p.get("page") for p in client.requested_params], [1, 2, 3])
        self.assertTrue(all("pageNo" not in p for p in client.requested_params))

    async def test_server_ignoring_page_param_stops_with_warning(self):
        # Arrange: server always returns page 1 regardless of the page param —
        # the regression that once stored the same newest page 16 times.
        pages = [[_doc_item(1), _doc_item(2)], [_doc_item(3), _doc_item(4)], [_doc_item(5), _doc_item(6)]]
        client = _FakeClient(pages, honors_page=False)
        self._patch_client(client)

        # Act
        result = await sync_mod.sync_current_teacher_gongwen_documents(11)

        # Assert: only page 1 stored, counts not inflated, mismatch warned.
        self.assertEqual(self._stored_remote_ids(), {"1", "2"})
        self.assertEqual(result["counts"]["new"], 2)
        self.assertEqual(result["counts"]["stored"], 2)
        self.assertTrue(any("分页异常" in w for w in result["warnings"]))

    async def test_overlapping_item_across_pages_counted_once(self):
        # Arrange: doc 3 drifts onto both page 1 and page 2 between requests.
        pages = [[_doc_item(1), _doc_item(3)], [_doc_item(3), _doc_item(4)]]
        client = _FakeClient(pages, honors_page=True)
        self._patch_client(client)

        # Act
        result = await sync_mod.sync_current_teacher_gongwen_documents(11)

        # Assert
        self.assertEqual(self._stored_remote_ids(), {"1", "3", "4"})
        self.assertEqual(result["counts"]["new"], 3)
        self.assertEqual(result["counts"]["stored"], 3)

    async def test_new_docs_ingest_as_pending_for_parser(self):
        # Arrange
        pages = [[_doc_item(1), _doc_item(2)]]
        client = _FakeClient(pages, honors_page=True)
        self._patch_client(client)

        # Act
        await sync_mod.sync_current_teacher_gongwen_documents(11)

        # Assert: ingested docs are claimable by the parse worker ('pending',
        # never the legacy 'idle' that the worker skips).
        rows = self.conn.execute("SELECT parsed_status FROM gongwen_documents").fetchall()
        self.assertEqual({row["parsed_status"] for row in rows}, {"pending"})


if __name__ == "__main__":
    unittest.main()
