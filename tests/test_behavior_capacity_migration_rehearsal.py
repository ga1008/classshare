"""Only reviewed additive recorder indexes can pass the migration proof."""
import copy
import unittest

from tools.behavior_capacity_migration_rehearsal import EXPECTED_INDEXES, prove, valid_report_proof


def baseline():
    return {"present": True, "table": {"columns": ["batch_key", "state"], "data_sha256": "a" * 64},
            "non_index_schema": {"constraint:behavior_write_inbox.pk": "b" * 64},
            "indexes": {"behavior_write_inbox_pkey": {"definition": "unchanged existing primary key"}},
            "sequences": {"existing_business_id_seq": "c" * 64}}


def migrated(before):
    after = copy.deepcopy(before)
    for name, expected in EXPECTED_INDEXES.items():
        after["indexes"][name] = {"method": "btree", "unique": False, "primary": False,
            "valid": True, "ready": True, "key_count": len(expected["keys"]),
            "attribute_count": len(expected["keys"]), "keys": expected["keys"],
            "predicate": expected["predicate"], "options": None, "definition": "catalog definition"}
    return after


class RecorderCapacityIndexProofTests(unittest.TestCase):
    def test_exact_index_additions_and_repeated_initialization_pass(self):
        before = baseline()
        after = migrated(before)
        proof = prove(before, after)
        self.assertTrue(valid_report_proof(proof))
        self.assertEqual(sorted(EXPECTED_INDEXES), proof["added_indexes"])
        self.assertEqual([], proof["allowed_differences"])
        repeated = prove(after, copy.deepcopy(after))
        self.assertTrue(valid_report_proof(repeated))
        self.assertEqual([], repeated["added_indexes"])

    def test_data_columns_constraints_existing_indexes_and_sequences_are_protected(self):
        before = baseline()
        for kind in ("data", "columns", "constraint", "old_index", "sequence"):
            after = migrated(before)
            if kind == "data":
                after["table"]["data_sha256"] = "d" * 64
            elif kind == "columns":
                after["table"]["columns"].append("unreviewed")
            elif kind == "constraint":
                after["non_index_schema"].clear()
            elif kind == "old_index":
                after["indexes"]["behavior_write_inbox_pkey"]["definition"] = "different"
            else:
                after["sequences"]["existing_business_id_seq"] = "e" * 64
            with self.subTest(kind=kind):
                self.assertFalse(valid_report_proof(prove(before, after)))

    def test_wrong_predicate_key_include_method_or_options_cannot_reuse_an_allowed_name(self):
        before = baseline()
        name = "idx_behavior_inbox_open_capacity"
        for field, value in (("predicate", "state IN ('pending','quarantined') OR state='discarded'"),
                             ("keys", ["batch_key"]), ("attribute_count", 2), ("method", "hash"),
                             ("unique", True), ("options", ["fillfactor=10"]), ("valid", False)):
            after = migrated(before)
            after["indexes"][name][field] = value
            with self.subTest(field=field):
                self.assertFalse(valid_report_proof(prove(before, after)))

    def test_missing_or_extra_index_fails_and_a_forged_success_is_recomputed(self):
        before = baseline()
        after = migrated(before)
        del after["indexes"][next(iter(EXPECTED_INDEXES))]
        proof = prove(before, after)
        self.assertFalse(valid_report_proof(proof))
        proof.update(status="ok", blockers=[])
        self.assertFalse(valid_report_proof(proof))
        after = migrated(before)
        after["indexes"]["unreviewed_other_index"] = {"definition": "extra"}
        self.assertFalse(valid_report_proof(prove(before, after)))

    def test_postgres_any_array_deparser_matches_only_the_reviewed_predicate(self):
        before = baseline()
        after = migrated(before)
        after["indexes"]["idx_behavior_inbox_open_capacity"]["predicate"] = (
            "(state = ANY (ARRAY['pending'::text, 'quarantined'::text]))")
        after["indexes"]["idx_behavior_inbox_retired_cleanup"]["predicate"] = (
            "((state = ANY (ARRAY['applied'::text, 'discarded'::text])) AND (substr(batch_id, 1, 3) = 'b1_'::text))")
        self.assertTrue(valid_report_proof(prove(before, after)))

    def test_noop_without_a_recorder_table_and_unexpected_table_creation(self):
        absent = {"present": False, "table": None, "non_index_schema": {}, "indexes": {}, "sequences": {}}
        self.assertTrue(valid_report_proof(prove(absent, copy.deepcopy(absent))))
        self.assertFalse(valid_report_proof(prove(absent, migrated(baseline()))))


if __name__ == "__main__":
    unittest.main()
