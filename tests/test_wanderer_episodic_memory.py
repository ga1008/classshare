import asyncio
import unittest
from datetime import datetime, timedelta

from classroom_app.services import wanderer_episodic_memory_service as memory
from classroom_app.services.wanderer_prompt_service import build_system_prompt
from tests.wanderer_test_support import WandererDbTestCase


def _old(days=30):
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


class EpisodicMemoryTest(WandererDbTestCase):
    def test_record_is_idempotent_and_retrievable(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "atai")
            first = memory.record_memory(conn, wanderer, kind="relationship", title="和阿泰一起跑步",
                                         content="他记得我上次膝盖疼，特意放慢了速度。", importance=0.9,
                                         people=["student:2"], source_kind="test", source_ref="run:1")
            second = memory.record_memory(conn, wanderer, kind="relationship", title="和阿泰一起跑步",
                                          content="他记得我上次膝盖疼，特意放慢了速度。", importance=0.9,
                                          people=["student:2"], source_kind="test", source_ref="run:1")
            conn.commit()
            self.assertEqual(first, second)
            rows = memory.select_memories(conn, wanderer, scene={"kind": "跑步", "tags": ["跑步"]})
        self.assertEqual(len(rows), 1)
        self.assertIn("膝盖", rows[0]["content"])
        self.assertGreaterEqual(rows[0]["importance"], 0.7)

    def test_compression_folds_old_low_importance_into_chapter(self):
        async def summarize(text):
            self.assertIn("旧事", text)
            return "那一周我反复在博客和群里说话，后来学会了先听别人把话说完。"

        with self.connect() as conn:
            wanderer = self.wanderer(conn, "momo")
            for index in range(6):
                memory.record_memory(conn, wanderer, kind="conversation", title=f"旧事{index}",
                                     content=f"那天发生的一件小事 {index}", importance=0.3,
                                     source_kind="test", source_ref=f"old:{index}", occurred_at=_old(30))
            conn.commit()
            report = asyncio.run(memory.compress_memories(conn, wanderer, summarizer=summarize))
            conn.commit()
            stats = memory.memory_stats(conn, wanderer)
            chapters = memory.chapter_rows(conn, wanderer)
        self.assertEqual(report["chapters"], 1)
        self.assertEqual(report["compressed"], 6)
        self.assertEqual(stats["archived"], 6)
        self.assertEqual(len(chapters), 1)
        self.assertIn("先听别人", chapters[0]["summary"])

    def test_summarizer_failure_keeps_raw_episodes(self):
        async def broken(_text):
            raise RuntimeError("synthetic model outage")

        with self.connect() as conn:
            wanderer = self.wanderer(conn, "lele")
            for index in range(3):
                memory.record_memory(conn, wanderer, kind="conversation", title="旧事",
                                     content=f"不能被删的记忆 {index}", importance=0.2,
                                     source_kind="test", source_ref=f"keep:{index}", occurred_at=_old(40))
            conn.commit()
            asyncio.run(memory.compress_memories(conn, wanderer, summarizer=broken))
            conn.commit()
            stats = memory.memory_stats(conn, wanderer)
        self.assertEqual(stats["active"], 3)
        self.assertEqual(stats["archived"], 0)

    def test_prompt_memory_block_respects_budget(self):
        with self.connect() as conn:
            wanderer = self.wanderer(conn, "chenmo")
            for index in range(20):
                memory.record_memory(conn, wanderer, kind="conversation", title=f"记忆{index}",
                                     content="很长的记忆内容" * 40, importance=0.5,
                                     source_kind="test", source_ref=f"budget:{index}", occurred_at=_old(index))
            conn.commit()
            prompt = build_system_prompt(conn, wanderer, scene={"kind": "私聊"}, thread_key="dm:student:2",
                                         budget_chars=35_000)
        self.assertLessEqual(len(prompt), 35_000)
        self.assertIn("【自传式记忆】", prompt)


if __name__ == "__main__":
    unittest.main()
