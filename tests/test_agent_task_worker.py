import unittest

from agent_task_worker import _worker_ids


class AgentTaskWorkerTests(unittest.TestCase):
    def test_worker_ids_keep_single_worker_id_unchanged(self):
        self.assertEqual(["agent-worker-compose"], _worker_ids("agent-worker-compose", 1))

    def test_worker_ids_suffix_parallel_worker_ids(self):
        self.assertEqual(
            ["agent-worker-compose-1", "agent-worker-compose-2"],
            _worker_ids("agent-worker-compose", 2),
        )

    def test_worker_ids_cap_parallelism_to_safe_upper_bound(self):
        self.assertEqual(
            ["agent-worker-compose-1", "agent-worker-compose-2", "agent-worker-compose-3", "agent-worker-compose-4"],
            _worker_ids("agent-worker-compose", 20),
        )


if __name__ == "__main__":
    unittest.main()
