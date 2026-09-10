import copy
import json
import unittest
from unittest.mock import patch

from tools import agent_authority_migration_rehearsal as proof
from tools.assessment_postgres_rehearsal import snapshot


def column(ordinal, kind="text", *, not_null=False, default=None):
    return {"ordinal": ordinal, "type": kind, "not_null": not_null, "default": default,
            "identity": "", "generated": "", "collation": '"default"' if kind == "text" else "-"}


def index(names, *, primary=False, predicate=None):
    return {"columns": names, "primary": primary, "unique": True, "valid": True,
            "predicate": predicate, "definition_sha256": proof._digest([names, primary, predicate])}


def table(name, values):
    columns = list(values[0])
    metadata = {key: column(i + 1, "bigint" if key in {"id", "teacher_id"} else "text") for i, key in enumerate(columns)}
    return {"columns": columns, "column_definitions": metadata,
            "rows": [proof._row_evidence(name, row) for row in values], "constraints": {}, "indexes": {}}


def update_row(table_name, row, changes):
    # Preserve hidden old hashes while changing only the synthetic fields used
    # by a scenario. No database/user content is part of this fixture.
    row = copy.deepcopy(row)
    for name, value in changes.items():
        if name in proof.SAFE_FIELDS:
            row["values"][name] = value
        row["column_sha256"][name] = proof._digest(value)
        row["null_columns"] = sorted(set(row["null_columns"]) - {name} | ({name} if value is None else set()))
    row["key"] = proof._identity(table_name, row["values"])
    return row


def migration_fixture():
    before = {"tables": {}, "authority_tables": {}}
    before["tables"]["agent_tasks"] = table("agent_tasks", [
        {"id": "10", "teacher_id": "7", "status": "completed", "private_instruction": "DO_NOT_REPORT_TASK", "updated_at": "2026-09-01"},
        {"id": "11", "teacher_id": "8", "status": "failed", "private_instruction": "DO_NOT_REPORT_OTHER", "updated_at": "2026-09-02"},
    ])
    before["tables"]["agent_task_composers"] = table("agent_task_composers", [
        {"teacher_id": "7", "page_label": "DO_NOT_REPORT_PAGE", "updated_at": "2026-09-01"},
    ])
    before["tables"]["agent_runtime_api_keys"] = table("agent_runtime_api_keys", [
        {"id": "1", "provider": "deepseek", "enabled": "1", "is_active": "1", "key_encrypted": "DO_NOT_REPORT_CIPHER", "updated_at": "2026-09-01"},
        {"id": "2", "provider": "deepseek", "enabled": "1", "is_active": "1", "key_encrypted": "DO_NOT_REPORT_CIPHER_2", "updated_at": "2026-09-02"},
    ])
    for name in ("agent_tasks", "agent_task_composers"):
        item = before["tables"][name]
        item["column_definitions"]["teacher_id"]["not_null"] = True
        item["constraints"][name + "_teacher_id_fkey"] = {"kind": "f", "definition": "FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE",
                                                         "validated": True, "deferrable": False, "deferred": False}
    composer = before["tables"]["agent_task_composers"]
    composer["constraints"]["agent_task_composers_pkey"] = {"kind": "p", "definition": "PRIMARY KEY (teacher_id)", "validated": True, "deferrable": False, "deferred": False}
    composer["indexes"]["agent_task_composers_pkey"] = index(["teacher_id"], primary=True)
    after = copy.deepcopy(before)
    for name in ("agent_tasks", "agent_task_composers"):
        item = after["tables"][name]
        item["column_definitions"]["teacher_id"]["not_null"] = False
        additions = {"actor_role": "teacher", "actor_id": None}
        if name == "agent_tasks":
            additions.update({field: None for field in sorted(proof.SOURCE_FIELDS)})
        for field in additions:
            item["columns"].append(field)
            item["column_definitions"][field] = column(len(item["columns"]), "bigint" if field == "actor_id" else "text",
                not_null=field == "actor_role", default="'teacher'::text" if field == "actor_role" else None)
        item["rows"] = [update_row(name, row, {**additions, "actor_id": row["values"]["teacher_id"]}) for row in item["rows"]]
    composer = after["tables"]["agent_task_composers"]
    composer["constraints"].pop("agent_task_composers_pkey")
    composer["indexes"].pop("agent_task_composers_pkey")
    composer["indexes"]["idx_agent_composers_actor"] = index(["actor_role", "actor_id"])
    keys = after["tables"]["agent_runtime_api_keys"]
    keys["columns"].append("deleted_at")
    keys["column_definitions"]["deleted_at"] = column(len(keys["columns"]))
    keys["rows"] = [update_row("agent_runtime_api_keys", row, {"deleted_at": None, "is_active": "0" if row["key"] == "1" else "1"}) for row in keys["rows"]]
    keys["indexes"]["idx_agent_keys_one_selected_provider"] = index(["provider"], predicate="(is_active = 1)")
    for name in proof.AUTHORITY_TABLES:
        after["authority_tables"][name] = {"columns": ["id"], "row_count": 0, "rows_sha256": proof._digest([])}
    after["authority_tables"]["agent_model_configuration_lock"] = {
        "columns": ["id", "revision"], "row_count": 1,
        "rows_sha256": proof._digest([proof._digest(["1", "0"])]), "lock_rows": [[1, 0]]}
    return before, after


class AgentAuthorityMigrationProofTests(unittest.TestCase):
    def setUp(self):
        self.before, self.after = migration_fixture()

    def test_exact_actor_nullable_pk_and_selected_key_changes_have_narrow_proof(self):
        result = proof.prove(self.before, self.after)
        self.assertEqual("ok", result["status"], result["blockers"])
        self.assertTrue(proof.valid_report_proof(result))
        self.assertEqual([
            "table:agent_runtime_api_keys",
            "schema:column:agent_task_composers.teacher_id", "schema:column:agent_tasks.teacher_id",
            "schema:constraint:agent_task_composers.agent_task_composers_pkey", "schema:index:agent_task_composers_pkey",
        ], result["allowed_differences"])
        self.assertEqual([], proof.prove(self.after, self.after)["allowed_differences"])
        self.assertNotIn("DO_NOT_REPORT", json.dumps(result))

    def test_source_session_or_persistent_authority_cannot_be_invented(self):
        for field in proof.SOURCE_FIELDS:
            after = copy.deepcopy(self.after)
            item = after["tables"]["agent_tasks"]
            item["rows"][0] = update_row("agent_tasks", item["rows"][0], {field: "invented-authority"})
            result = proof.prove(self.before, after)
            self.assertEqual("failed", result["status"])
            self.assertTrue(any("forged" in item for item in result["blockers"]))

    def test_only_proven_owned_composer_identity_sequence_may_be_retired(self):
        before = copy.deepcopy(self.before)
        composer = before['tables']['agent_task_composers']
        composer['column_definitions']['teacher_id']['identity'] = 'd'
        self.assertEqual('failed', proof.prove(before, self.after)['status'])
        composer['identity_sequence'] = {'schema':'public','name':'agent_task_composers_teacher_id_seq','dependency':'i'}
        result = proof.prove(before, self.after)
        self.assertEqual('ok', result['status'], result['blockers'])
        self.assertIn('schema:sequence:agent_task_composers_teacher_id_seq', result['allowed_differences'])
        self.assertIn('sequence:agent_task_composers_teacher_id_seq', result['allowed_differences'])
        after = copy.deepcopy(self.after)
        after['tables']['agent_task_composers']['column_definitions']['teacher_id']['default'] = "nextval('wrong_seq')"
        self.assertEqual('failed', proof.prove(before, after)['status'])
        before['tables']['agent_tasks']['column_definitions']['teacher_id']['identity'] = 'd'
        self.assertEqual('failed', proof.prove(before, self.after)['status'])

    def test_wrong_actor_missing_row_or_old_business_field_changes_are_blocked(self):
        for name, field, value in (("agent_tasks", "actor_id", "8"), ("agent_tasks", "private_instruction", "rewritten"),
                                   ("agent_task_composers", "page_label", "changed"), ("agent_runtime_api_keys", "updated_at", "2026-10-01"),
                                   ("agent_runtime_api_keys", "key_encrypted", "re-encrypted")):
            after = copy.deepcopy(self.after)
            item = after["tables"][name]
            item["rows"][0] = update_row(name, item["rows"][0], {field: value})
            self.assertEqual("failed", proof.prove(self.before, after)["status"])
        self.after["tables"]["agent_tasks"]["rows"].pop()
        self.assertEqual("failed", proof.prove(self.before, self.after)["status"])

    def test_foreign_keys_column_type_and_actor_unique_index_cannot_be_relaxed(self):
        after = copy.deepcopy(self.after)
        after["tables"]["agent_tasks"]["constraints"].clear()
        self.assertEqual("failed", proof.prove(self.before, after)["status"])
        after = copy.deepcopy(self.after)
        after["tables"]["agent_tasks"]["column_definitions"]["teacher_id"]["type"] = "text"
        self.assertEqual("failed", proof.prove(self.before, after)["status"])
        after = copy.deepcopy(self.after)
        after["tables"]["agent_task_composers"]["indexes"]["idx_agent_composers_actor"]["columns"] = ["actor_id"]
        self.assertEqual("failed", proof.prove(self.before, after)["status"])

    def test_key_repair_selects_exact_expected_winner_and_enforces_partial_index(self):
        after = copy.deepcopy(self.after)
        item = after["tables"]["agent_runtime_api_keys"]
        item["rows"] = [update_row("agent_runtime_api_keys", row, {"is_active": "1" if row["key"] == "1" else "0"}) for row in item["rows"]]
        self.assertEqual("failed", proof.prove(self.before, after)["status"])
        after = copy.deepcopy(self.after)
        after["tables"]["agent_runtime_api_keys"]["indexes"]["idx_agent_keys_one_selected_provider"]["predicate"] = "is_active=0"
        self.assertEqual("failed", proof.prove(self.before, after)["status"])

    def test_new_ledger_entries_and_rewritten_old_authority_are_rejected(self):
        after = copy.deepcopy(self.after)
        after["authority_tables"]["agent_task_delegations"].update(row_count=1, rows_sha256="a" * 64)
        self.assertEqual("failed", proof.prove(self.before, after)["status"])
        before = copy.deepcopy(self.after)
        after = copy.deepcopy(self.after)
        after["authority_tables"]["agent_model_requests"].update(row_count=1, rows_sha256="a" * 64)
        self.assertEqual("failed", proof.prove(before, after)["status"])

    def test_questions_are_present_and_empty_on_first_migration_and_preserved_on_rerun(self):
        self.assertIn("agent_task_questions", self.after["authority_tables"])
        missing = copy.deepcopy(self.after)
        missing["authority_tables"].pop("agent_task_questions")
        self.assertEqual("failed", proof.prove(self.before, missing)["status"])
        forged = copy.deepcopy(self.after)
        forged["authority_tables"]["agent_task_questions"].update(row_count=1, rows_sha256="b" * 64)
        self.assertEqual("failed", proof.prove(self.before, forged)["status"])
        self.assertEqual("ok", proof.prove(forged, forged)["status"])
        self.assertEqual("failed", proof.prove(forged, self.after)["status"])

    def test_http_observations_are_empty_on_migration_and_cannot_disappear_on_rerun(self):
        self.assertIn("agent_platform_requests", self.after["authority_tables"])
        missing = copy.deepcopy(self.after)
        missing["authority_tables"].pop("agent_platform_requests")
        self.assertEqual("failed", proof.prove(self.before, missing)["status"])
        observation = copy.deepcopy(self.after)
        observation["authority_tables"]["agent_platform_requests"].update(row_count=1, rows_sha256="c" * 64)
        self.assertEqual("failed", proof.prove(self.before, observation)["status"])
        self.assertEqual("ok", proof.prove(observation, observation)["status"])
        self.assertEqual("failed", proof.prove(observation, self.after)["status"])

    def test_child_admissions_are_empty_on_migration_and_monotone_across_reruns(self):
        self.assertIn('agent_task_children', self.after['authority_tables'])
        missing = copy.deepcopy(self.after)
        missing['authority_tables'].pop('agent_task_children')
        self.assertEqual('failed', proof.prove(self.before, missing)['status'])
        admitted = copy.deepcopy(self.after)
        admitted['authority_tables']['agent_task_children'].update(row_count=1, rows_sha256='d' * 64)
        self.assertEqual('failed', proof.prove(self.before, admitted)['status'])
        self.assertEqual('ok', proof.prove(admitted, admitted)['status'])
        self.assertEqual('failed', proof.prove(admitted, self.after)['status'])
        rewritten = copy.deepcopy(admitted)
        rewritten['authority_tables']['agent_task_children']['rows_sha256'] = 'e' * 64
        self.assertEqual('failed', proof.prove(admitted, rewritten)['status'])

    def test_report_validator_recalculates_rules_and_cannot_add_a_whitelist(self):
        original = proof.prove(self.before, self.after)
        for key, value in (("allowed_differences", ["table:agent_tasks"]), ("expected_changes", []), ("blockers", ["ignored"])):
            changed = copy.deepcopy(original)
            changed[key] = value
            self.assertFalse(proof.valid_report_proof(changed))
        changed = copy.deepcopy(original)
        changed["after"]["tables"]["agent_tasks"]["rows"][0]["values"]["actor_id"] = "999"
        self.assertFalse(proof.valid_report_proof(changed))
        self.assertFalse(proof.valid_report_proof({"contract": proof.PROOF_CONTRACT, "status": "ok"}))


class OldProjectionPrimaryKeyTests(unittest.TestCase):
    def test_new_actor_primary_key_does_not_index_nonexistent_old_columns(self):
        class Rows(list):
            def fetchall(self):
                return self

        class Cursor:
            def __init__(self, conn): self.conn = conn
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, sql):
                self.selected = [part.split('"')[1] for part in sql.split("SELECT ", 1)[1].split(" FROM ", 1)[0].split(",")]
            def __iter__(self): return iter([tuple(self.conn.values[key] for key in self.selected)])

        class Connection:
            columns = ["teacher_id", "page_label"]
            keys = ["teacher_id"]
            values = {"teacher_id": "7", "page_label": "unchanged", "actor_role": "teacher", "actor_id": "7"}
            def cursor(self, **kwargs): return Cursor(self)
            def execute(self, sql, args=()):
                if "c.relkind IN ('r','p')" in sql: return Rows([("agent_task_composers",)])
                if "SELECT a.attname FROM pg_attribute" in sql: return Rows([(key,) for key in self.columns])
                if "k.contype='p'" in sql: return Rows([(key,) for key in self.keys])
                return Rows()

        conn = Connection()
        with patch("tools.assessment_postgres_rehearsal.schema_objects", return_value={}):
            before = snapshot(conn)
            conn.columns = [*conn.columns, "actor_role", "actor_id"]
            conn.keys = ["actor_role", "actor_id"]
            projected = snapshot(conn, baseline=before)
            current = snapshot(conn)
        self.assertEqual(before, projected)
        self.assertEqual(["actor_role", "actor_id"], current["tables"]["agent_task_composers"]["primary_key_columns"])


if __name__ == "__main__":
    unittest.main()
