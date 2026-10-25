import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app import config
from classroom_app.services import wanderer_memory_service as memory
from tests.wanderer_test_support import WandererDbTestCase


class WandererMemoryTest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory(prefix="lanshare-wanderer-memory-")
        self.addCleanup(folder.cleanup)
        self.data_dir = Path(folder.name)
        patcher = patch.object(config, "DATA_DIR", self.data_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fill(self, conn, wanderer, key, count):
        for index in range(count):
            memory.append_turn(conn, wanderer, key, who="student:7" if index % 2 else "self",
                               text=f"第{index}句话", counterpart_identity="student:7")

    def test_append_keeps_last_forty_and_mirrors_everything(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "lele")
            self._fill(conn, wanderer, "dm:student:7", 45)
            conn.commit()
            memory.invalidate_thread_cache()
            thread = memory.load_thread(conn, wanderer["id"], "dm:student:7")
        self.assertEqual(len(thread["history"]), memory.HISTORY_DB_LIMIT)
        self.assertEqual(thread["history"][-1]["text"], "第44句话")
        self.assertEqual(thread["history"][0]["text"], "第5句话")
        self.assertEqual(thread["total_chars"], sum(len(f"第{i}句话") for i in range(45)))
        self.assertEqual(thread["counterpart_identity"], "student:7")
        mirror = self.data_dir / "wanderers" / "lele" / "threads" / "dm_student_7.jsonl"
        self.assertTrue(mirror.is_file())
        self.assertEqual(len(mirror.read_text(encoding="utf-8").splitlines()), 45)

    def test_append_reads_the_row_not_a_stale_cache(self):
        key = "dm:student:9"
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "lele")
            memory.append_turn(conn, wanderer, key, who="self", text="第一句")
            conn.commit()
            memory.load_thread(conn, wanderer["id"], key)  # warm this process's cache
            # Another process (scheduler vs app) appends a turn behind our cache.
            other = memory._read_thread(conn, wanderer["id"], key)
            other_history = [*other["history"], {"at": "2026-10-26T10:00:00", "who": "student:9", "text": "别处写的"}]
            conn.execute("UPDATE wanderer_memory_threads SET history_json = ?, total_chars = total_chars + 4 "
                         "WHERE wanderer_id = ? AND thread_key = ?",
                         (json.dumps(other_history, ensure_ascii=False), wanderer["id"], key))
            conn.commit()
            updated = memory.append_turn(conn, wanderer, key, who="self", text="第三句")
            conn.commit()
            fresh = memory.load_thread(conn, wanderer["id"], key)
        texts = [turn["text"] for turn in updated["history"]]
        self.assertEqual(texts, ["第一句", "别处写的", "第三句"])
        self.assertEqual([turn["text"] for turn in fresh["history"]], texts)  # cache refreshed after the write

    def test_compaction_uses_injected_summarizer(self):
        seen = []

        def summarizer(text):
            seen.append(text)
            return "摘要：他们聊了很多。"

        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            self._fill(conn, wanderer, "blog_post:1", 30)
            thread = memory.load_thread(conn, wanderer["id"], "blog_post:1")
            with patch.dict(os.environ, {"WANDERER_THREAD_COMPACT_CHARS": "50"}):
                self.assertTrue(memory.needs_compaction(thread))
            self.assertFalse(memory.needs_compaction(thread))
            compacted = memory.compact_thread(conn, wanderer, "blog_post:1", summarizer)
            conn.commit()
        self.assertEqual(len(seen), 1)
        self.assertIn("第0句话", seen[0])
        self.assertEqual(compacted["summary"], "摘要：他们聊了很多。")
        self.assertEqual(len(compacted["history"]), memory.HISTORY_AFTER_COMPACTION)
        self.assertEqual(compacted["compacted_count"], 1)
        self.assertLess(compacted["total_chars"], thread["total_chars"])
        mirror = self.data_dir / "wanderers" / "atai" / "threads" / "blog_post_1.jsonl"
        self.assertEqual(len(mirror.read_text(encoding="utf-8").splitlines()), 30)
        block = memory.thread_context_block(compacted, limit_turns=2)
        self.assertIn("摘要：他们聊了很多。", block)
        self.assertIn("第29句话", block)
        self.assertNotIn("第27句话", block)

    def test_failed_summarizer_keeps_memory(self):
        def broken(_text):
            raise RuntimeError("model down")

        with self.connect() as conn:
            wanderer = self.wanderer(conn, "momo")
            self._fill(conn, wanderer, "classroom:3", 3)
            with self.assertRaises(RuntimeError):
                memory.compact_thread(conn, wanderer, "classroom:3", broken)
            with self.assertRaises(ValueError):
                memory.compact_thread(conn, wanderer, "classroom:3", lambda _text: "  ")
            thread = memory.load_thread(conn, wanderer["id"], "classroom:3")
            conn.rollback()
        self.assertEqual(len(thread["history"]), 3)
        self.assertEqual(thread["summary"], "")

    def test_loaded_threads_are_copies(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "abu")
            memory.append_turn(conn, wanderer, "dao", who="天道", text="你好")
            first = memory.load_thread(conn, wanderer["id"], "dao")
            first["history"].append({"who": "x", "text": "污染"})
            second = memory.load_thread(conn, wanderer["id"], "dao")
            conn.rollback()
        self.assertEqual(len(second["history"]), 1)

    def test_empty_thread_and_safe_keys(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "gunian")
            thread = memory.load_thread(conn, wanderer["id"], "blog_post:999")
        self.assertIsNone(thread["id"])
        self.assertIn("第一次", memory.thread_context_block(thread))
        self.assertEqual(memory.safe_thread_key("../../etc/passwd"), "etc_passwd")
        self.assertEqual(memory.safe_thread_key(""), "thread")


if __name__ == "__main__":
    unittest.main()
