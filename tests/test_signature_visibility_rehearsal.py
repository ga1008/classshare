import copy
import unittest

from tools.signature_visibility_rehearsal import VERSION, prove, valid_report_proof


class SignatureVisibilityRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.before = {"signatures": {"7": {"id": "7", "scope_level": "department",
            "school_code": "gxufl", "college": "学院", "department": "", "other_columns_sha256": "unchanged"}},
            "markers": {"older|postgres": {"checksum": "old unchanged marker"}}}
        self.after = copy.deepcopy(self.before)
        self.after["signatures"]["7"]["scope_level"] = "college"
        self.after["markers"][VERSION + "|postgres"] = {"version": VERSION, "db_engine": "postgres",
            "name": "Explicit signature visibility levels", "checksum": "a" * 64,
            "applied_at": "2026-09-08 12:00:00+00", "duration_ms": "0", "success": "true", "error": None}

    def test_proof_records_exact_ids_and_single_changed_field(self):
        proof = prove(self.before, self.after)
        self.assertEqual("ok", proof["status"])
        self.assertTrue(valid_report_proof(proof))
        self.assertEqual(["table:electronic_signatures", "table:schema_migrations"], proof["allowed_differences"])
        self.assertEqual(7, proof["actual_changes"][0]["id"])
        self.assertEqual("scope_level", proof["actual_changes"][0]["field"])
        self.assertEqual([], prove(self.after, self.after)["allowed_differences"])

    def test_non_scope_data_wrong_transition_added_rows_and_marker_rewrites_fail(self):
        for bucket, key, field, value in (
            ("signatures", "7", "other_columns_sha256", "date was changed"),
            ("signatures", "7", "scope_level", "platform"),
            ("markers", "older|postgres", "checksum", "rewritten"),
            ("markers", VERSION + "|postgres", "duration_ms", "1"),
        ):
            changed = copy.deepcopy(self.after)
            changed[bucket][key][field] = value
            self.assertEqual("failed", prove(self.before, changed)["status"])
        changed = copy.deepcopy(self.after)
        changed["signatures"]["8"] = changed["signatures"]["7"].copy()
        self.assertEqual("failed", prove(self.before, changed)["status"])

    def test_report_cannot_allow_additional_field_or_invalid_scope_rule(self):
        original = prove(self.before, self.after)
        for key, value in (("field", "updated_at"), ("after", "platform"), ("department", "real department")):
            proof = copy.deepcopy(original)
            for name in ("expected_changes", "actual_changes"):
                proof[name][0][key] = value
            self.assertFalse(valid_report_proof(proof))
        proof = copy.deepcopy(original)
        proof["changed_fields"].append("updated_at")
        self.assertFalse(valid_report_proof(proof))


if __name__ == "__main__":
    unittest.main()
