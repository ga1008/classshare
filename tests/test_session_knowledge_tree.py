import json
import sqlite3
import unittest
from datetime import datetime, timedelta

from fastapi import HTTPException

from classroom_app.db import schema_session_knowledge_trees as schema
from classroom_app.services import session_knowledge_tree_generation_service as gen
from classroom_app.services import session_knowledge_tree_service as kt


def _leaf(label, weight=5):
    return {"label": label, "weight": weight, "detail": f"{label} 的介绍"}


SAMPLE = {
    "title": "TCP 可靠传输",
    "summary": "确认、重传与滑动窗口",
    "nodes": [
        {"label": "可靠传输原理", "children": [
            {"label": "确认与重传", "children": [_leaf("ACK 语义", 10), _leaf("超时重传", 20)]},
            {"label": "序号机制", "children": [_leaf("字节序号", 5)]},
        ]},
        {"label": "滑动窗口", "children": [_leaf("窗口大小", 15), _leaf("窗口滑动", 0)]},
    ],
}


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema._SCHEMA_READY = False
    schema.ensure_session_knowledge_tree_schema(conn)
    return conn


def _ready_row(conn, tree=None, session_id=7):
    row = kt.ensure_row(conn, class_offering_id=3, session_id=session_id, teacher_id=1)
    token = kt.start_generation(conn, row)
    normalized = tree or kt.normalize_tree(SAMPLE)
    assert kt.store_generated_tree(conn, row["id"], token, normalized, fingerprint="fp1", source_label="doc.md")
    return kt.load_row(conn, session_id), normalized


class NormalizeTreeTests(unittest.TestCase):
    def test_weights_sum_to_100_and_follow_importance(self):
        tree = kt.normalize_tree(SAMPLE)
        leaves = list(kt.iter_leaves(tree["nodes"]))
        self.assertEqual(sum(leaf["weight"] for leaf in leaves), 100)
        by_label = {leaf["label"]: leaf["weight"] for leaf in leaves}
        self.assertGreater(by_label["超时重传"], by_label["ACK 语义"])
        self.assertTrue(all(leaf["weight"] >= 1 for leaf in leaves))
        self.assertEqual(tree["depth"], 3)

    def test_missing_weights_split_evenly(self):
        self.assertEqual(kt.distribute_weights([0, 0, 0, 0]), [25, 25, 25, 25])
        self.assertEqual(sum(kt.distribute_weights([0, 0, 0])), 100)

    def test_single_layer_is_rejected(self):
        with self.assertRaises(kt.KnowledgeTreeError):
            kt.normalize_tree({"nodes": [{"label": "只有一层"}, {"label": "仍是一层"}]})

    def test_depth_capped_at_three_and_top_level_capped_at_four(self):
        deep = {"nodes": [{"label": f"大点{i}", "children": [{"label": "细分", "children": [
            {"label": "单元", "children": [{"label": "过深的第四层"}]}]}]} for i in range(6)]}
        tree = kt.normalize_tree(deep)
        self.assertEqual(len(tree["nodes"]), 4)
        self.assertEqual(tree["depth"], 3)
        leaf = next(iter(kt.iter_leaves(tree["nodes"])))
        self.assertIn("过深的第四层", leaf["detail"])

    def test_childless_major_point_gets_a_unit(self):
        tree = kt.normalize_tree({"nodes": [{"label": "A", "children": [_leaf("a1")]}, {"label": "B", "detail": "b"}]})
        second = tree["nodes"][1]
        self.assertEqual(len(second["children"]), 1)
        self.assertNotIn("weight", second)
        self.assertEqual(second["children"][0]["detail"], "b")

    def test_node_ids_are_unique(self):
        tree = kt.normalize_tree(SAMPLE)
        ids = [node["id"] for node in kt.iter_nodes(tree["nodes"])]
        self.assertEqual(len(ids), len(set(ids)))


class ProgressTests(unittest.TestCase):
    def test_progress_and_lifecycle(self):
        tree = kt.normalize_tree(SAMPLE)
        leaves = list(kt.iter_leaves(tree["nodes"]))
        self.assertEqual(kt.compute_progress(tree, [])["lifecycle"], "not_started")
        partial = kt.compute_progress(tree, [leaves[0]["id"], "unknown"])
        self.assertEqual(partial["progress"], leaves[0]["weight"])
        self.assertEqual(partial["completed_ids"], [leaves[0]["id"]])
        self.assertEqual(partial["lifecycle"], "in_progress")
        done = kt.compute_progress(tree, [leaf["id"] for leaf in leaves])
        self.assertEqual((done["progress"], done["lifecycle"]), (100, "completed"))

    def test_reorder_keeps_ids_and_rejects_mismatch(self):
        tree = kt.normalize_tree(SAMPLE)
        top = [node["id"] for node in tree["nodes"]]
        swapped = kt.reorder_children(tree, "", list(reversed(top)))
        self.assertEqual([node["id"] for node in swapped["nodes"]], list(reversed(top)))
        self.assertEqual([node["id"] for node in tree["nodes"]], top)  # original untouched
        with self.assertRaises(HTTPException):
            kt.reorder_children(tree, "", top[:1])


class PersistenceTests(unittest.TestCase):
    def test_toggle_bulk_and_regeneration_resets_progress(self):
        conn = _conn()
        row, tree = _ready_row(conn)
        leaves = list(kt.iter_leaves(tree["nodes"]))
        updated = kt.set_leaf_done(conn, 7, leaves[0]["id"], True)
        self.assertEqual(updated["progress_percent"], leaves[0]["weight"])
        updated = kt.set_leaf_done(conn, 7, leaves[0]["id"], True)  # idempotent
        self.assertEqual(json.loads(updated["completed_json"]), [leaves[0]["id"]])
        with self.assertRaises(HTTPException):
            kt.set_leaf_done(conn, 7, tree["nodes"][0]["id"], True)  # not a leaf
        full = kt.set_all_done(conn, 7, True)
        self.assertEqual(full["progress_percent"], 100)
        self.assertTrue(full["completed_at"])
        self.assertEqual(kt.load_offering_progress(conn, 3)[7]["lifecycle"], "completed")

        token = kt.start_generation(conn, full)
        with self.assertRaises(HTTPException):
            kt.set_leaf_done(conn, 7, leaves[0]["id"], False)  # locked while generating
        kt.store_generated_tree(conn, row["id"], token, kt.normalize_tree(SAMPLE), fingerprint="fp2", source_label="doc.md")
        fresh = kt.load_row(conn, 7)
        self.assertEqual((fresh["progress_percent"], fresh["completed_json"], fresh["completed_at"]), (0, "[]", None))

    def test_superseded_generation_cannot_overwrite(self):
        conn = _conn()
        row, _tree = _ready_row(conn)
        old_token = kt.start_generation(conn, row)
        conn.execute("UPDATE session_knowledge_trees SET ai_gen_started_at = ? WHERE id = ?",
                     ((datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"), row["id"]))
        new_token = kt.start_generation(conn, kt.load_row(conn, 7))  # stale run expired, new run starts
        self.assertNotEqual(old_token, new_token)
        self.assertFalse(kt.store_generated_tree(conn, row["id"], old_token, kt.normalize_tree(SAMPLE), fingerprint="x", source_label=""))
        kt.mark_generation_failed(conn, row["id"], old_token, "late failure")
        self.assertEqual(kt.load_row(conn, 7)["status"], kt.STATUS_GENERATING)

    def test_result_after_expiry_is_dropped(self):
        conn = _conn()
        row, tree = _ready_row(conn)
        leaf = next(iter(kt.iter_leaves(tree["nodes"])))
        token = kt.start_generation(conn, row)
        conn.execute("UPDATE session_knowledge_trees SET ai_gen_started_at = ? WHERE id = ?",
                     ((datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"), row["id"]))
        expired = kt.expire_stale_generation(conn, kt.load_row(conn, 7))
        self.assertEqual(expired["status"], kt.STATUS_READY)  # old tree stays usable
        kt.set_leaf_done(conn, 7, leaf["id"], True)
        self.assertFalse(kt.store_generated_tree(conn, row["id"], token, kt.normalize_tree(SAMPLE), fingerprint="late", source_label=""))
        self.assertIn(leaf["id"], json.loads(kt.load_row(conn, 7)["completed_json"]))

    def test_concurrent_generation_rejected(self):
        conn = _conn()
        row = kt.ensure_row(conn, class_offering_id=3, session_id=9, teacher_id=1)
        kt.start_generation(conn, row)
        with self.assertRaises(HTTPException) as ctx:
            kt.start_generation(conn, kt.load_row(conn, 9))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_failure_keeps_previous_tree(self):
        conn = _conn()
        row, _tree = _ready_row(conn)
        token = kt.start_generation(conn, row)
        kt.mark_generation_failed(conn, row["id"], token, "AI 不可用")
        state = kt.serialize(kt.load_row(conn, 7), can_edit=True, has_material=True, current_fingerprint="fp1")
        self.assertEqual(state["status"], kt.STATUS_READY)
        self.assertTrue(state["has_tree"])
        self.assertEqual(state["error"], "AI 不可用")

    def test_stale_flag_only_for_teacher_when_source_changed(self):
        conn = _conn()
        row, _tree = _ready_row(conn)
        self.assertFalse(kt.serialize(row, can_edit=True, has_material=True, current_fingerprint="fp1")["is_stale"])
        self.assertTrue(kt.serialize(row, can_edit=True, has_material=True, current_fingerprint="fp9")["is_stale"])
        self.assertFalse(kt.serialize(row, can_edit=False, has_material=True, current_fingerprint="fp9")["is_stale"])

    def test_save_order_checks_version(self):
        conn = _conn()
        row, tree = _ready_row(conn)
        parent = tree["nodes"][0]
        order = [child["id"] for child in reversed(parent["children"])]
        saved = kt.save_order(conn, 7, parent["id"], order, int(row["version"]))
        saved_tree = json.loads(saved["tree_json"])
        self.assertEqual([c["id"] for c in saved_tree["nodes"][0]["children"]], order)
        with self.assertRaises(HTTPException):
            kt.save_order(conn, 7, parent["id"], order, int(row["version"]))

    def test_page_progress_attach_is_best_effort(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        items = [{"id": 1}]
        kt.attach_session_knowledge_progress(conn, 3, items)  # table missing: no exception
        self.assertIsNone(items[0]["knowledge_progress"])


class PromptQualityTests(unittest.TestCase):
    def test_exemplar_is_flawless_and_normalizes_to_its_own_weights(self):
        from classroom_app.services import session_knowledge_tree_prompts as prompts

        self.assertEqual(prompts.quality_issues(prompts.EXAMPLE_OUTPUT), [])
        raw = [leaf["weight"] for leaf in kt.iter_leaves(prompts.EXAMPLE_OUTPUT["nodes"])]
        self.assertEqual(sum(raw), 100)
        tree = kt.normalize_tree(prompts.EXAMPLE_OUTPUT)
        self.assertEqual([leaf["weight"] for leaf in kt.iter_leaves(tree["nodes"])], raw)
        self.assertIn("栈与队列", prompts.SYSTEM_PROMPT)
        self.assertIn("输出前自检", prompts.SYSTEM_PROMPT)

    def test_quality_issues_flag_common_ai_mistakes(self):
        from classroom_app.services import session_knowledge_tree_prompts as prompts

        flat = {"nodes": [{"label": f"点{i}"} for i in range(5)]}
        issues = " ".join(prompts.quality_issues(flat))
        self.assertIn("1–4", issues)
        self.assertIn("只有一层", issues)
        sloppy = {"nodes": [{"label": "概述", "children": [{"label": "A", "weight": 5}]},
                            {"label": "B", "children": [{"label": "A", "weight": 5}, {"label": "C", "weight": 5}]}]}
        issues = " ".join(prompts.quality_issues(sloppy))
        for expected in ("空泛标签", "重复标签", "只有一个下级", "粒度太粗"):
            self.assertIn(expected, issues)
        repair = prompts.build_repair_message("原始输入", sloppy, ["问题甲"])
        self.assertIn("原始输入", repair)
        self.assertIn("问题甲", repair)


class BatchSelectionTests(unittest.TestCase):
    ROWS = [
        {"session_id": 1, "has_material": True, "has_tree": False, "status": "empty", "is_stale": False},
        {"session_id": 2, "has_material": True, "has_tree": True, "status": "ready", "is_stale": True},
        {"session_id": 3, "has_material": True, "has_tree": True, "status": "ready", "is_stale": False},
        {"session_id": 4, "has_material": False, "has_tree": False, "status": "empty", "is_stale": False},
        {"session_id": 5, "has_material": True, "has_tree": False, "status": "generating", "is_stale": False},
    ]

    def test_modes(self):
        self.assertEqual(kt.select_batch_targets(self.ROWS, "missing")[0], [1])
        self.assertEqual(kt.select_batch_targets(self.ROWS, "stale")[0], [2])
        targets, skipped = kt.select_batch_targets(self.ROWS, "all")
        self.assertEqual(targets, [1, 2, 3])
        self.assertEqual({item["session_id"] for item in skipped}, {4, 5})
        with self.assertRaises(HTTPException):
            kt.select_batch_targets(self.ROWS, "everything")


class GenerationPromptTests(unittest.TestCase):
    def test_payload_parsing_and_prompt_contents(self):
        self.assertEqual(gen.parse_ai_payload({"response_json": SAMPLE})["title"], "TCP 可靠传输")
        text = "```json\n" + json.dumps(SAMPLE, ensure_ascii=False) + "\n```"
        self.assertEqual(len(gen.parse_ai_payload({"response_text": text})["nodes"]), 2)
        message = gen.build_user_message({
            "session": {"title": "第 9 次课", "session_date": "2026-10-15", "content": "讲 TCP"},
            "doc_text": "文档正文", "classroom_summary": "课程简介：网络", "textbook_summary": "教材目录：第1章",
            "previous_title": "第 8 次课", "next_title": "第 10 次课", "unfinished": ["拥塞控制"],
        })
        for expected in ("课程简介：网络", "教材目录：第1章", "文档正文", "拥塞控制", "第 10 次课"):
            self.assertIn(expected, message)


if __name__ == "__main__":
    unittest.main()
