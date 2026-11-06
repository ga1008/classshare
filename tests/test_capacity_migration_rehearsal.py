"""Migration digests may exclude only the reviewed new columns."""
import unittest

from tools.platform_capacity_migration_rehearsal import projection_extras


class LegacyBusinessProjectionTests(unittest.TestCase):
    def test_only_explicit_release_additions_are_projected_out(self):
        old = {"behavior_write_inbox": ["batch_key", "payload_hash", "events_json", "state"],
               "learning_progress_snapshots": ["score", "metrics_json"]}
        actual = {"behavior_write_inbox": [*old["behavior_write_inbox"], "resolved_at"],
                  "learning_progress_snapshots": [*old["learning_progress_snapshots"], "dirty_version", "calculated_version"]}
        self.assertEqual({"behavior_write_inbox": ["resolved_at"],
                          "learning_progress_snapshots": ["calculated_version", "dirty_version"]}, projection_extras(actual, old))

    def test_missing_original_payload_or_hash_is_a_failure(self):
        old = {"behavior_write_inbox": ["batch_key", "payload_hash", "events_json"]}
        for missing in ("payload_hash", "events_json"):
            with self.subTest(missing=missing), self.assertRaises(AssertionError):
                projection_extras({"behavior_write_inbox": [column for column in old["behavior_write_inbox"] if column != missing]}, old)

    def test_unreviewed_column_or_table_cannot_be_silently_ignored(self):
        old = {"behavior_write_inbox": ["batch_key", "payload_hash"]}
        for actual in ({"behavior_write_inbox": ["batch_key", "payload_hash", "unexpected_field"]}, {}):
            with self.subTest(actual=actual), self.assertRaises(AssertionError):
                projection_extras(actual, old)
