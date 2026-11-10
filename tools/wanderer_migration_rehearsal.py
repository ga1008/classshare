"""Exact, read-only evidence for AI account takeover and relationship upgrades.

Only the caller's isolated rehearsal connection is used. Credentials, message
contents and session identifiers are represented by hashes, never by plaintext.
The proof permits specific cells/deleted AI sessions, not whole-table rewrites.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct

PROOF_CONTRACT = "wanderer-migration-v1"
REVOCATIONS = {
    "remember_login_tokens": ("role", "user_id", "revoked_at", "", {"revoke_reason": "ai_account_control"}),
    "mp_sessions": ("user_role", "user_pk", "revoked", "0", {"revoked": "1"}),
    "wechat_bindings": ("user_role", "user_pk", "status", "active", {"status": "revoked"}),
    "agent_task_delegations": ("actor_role", "actor_id", "status", "active", {"status": "revoked", "revoke_reason": "ai_account_control"}),
    "agent_persistent_authorizations": ("actor_role", "actor_id", "status", "active", {"status": "revoked", "revoke_reason": "ai_account_control"}),
}
TABLES = ("students", "wanderer_agents", "user_sessions", *REVOCATIONS,
          "wanderer_relationships", "wanderer_relationship_versions", "account_control_migrations", "agent_platform_requests")
SAFE = {
    "students": {"id", "control_mode", "enrollment_status", "password_reset_required"},
    "wanderer_agents": {"id", "student_id"},
    "user_sessions": {"role", "user_id"},
    "wanderer_relationships": {"wanderer_id", "peer_identity", "affinity"},
    "wanderer_relationship_versions": {"wanderer_id", "peer_identity", "revision", "origin", "legacy_affinity", "source_turn_id"},
    "account_control_migrations": {"student_id", "migrated_at", "evidence_json"},
    "agent_platform_requests": {"principal_kind", "wanderer_id", "turn_id", "life_epoch", "authority_version"},
    **{name: {role, owner, state, *changes, "revoked_at", "updated_at"}
       for name, (role, owner, state, _, changes) in REVOCATIONS.items()},
}
PLATFORM_ADDITIONS = {"principal_kind": "human_delegation", "wanderer_id": None, "turn_id": None,
                      "life_epoch": None, "authority_version": None}
PLATFORM_NULLABLE = ("task_id", "attempt_id", "delegation_id", "source_session_hash")
PLATFORM_AUTHORITY_CHECK = """CHECK (principal_kind = 'human_delegation'::text AND task_id IS NOT NULL
    AND attempt_id IS NOT NULL AND delegation_id IS NOT NULL AND source_session_hash IS NOT NULL
    AND wanderer_id IS NULL AND turn_id IS NULL AND life_epoch IS NULL AND authority_version IS NULL
    OR principal_kind = 'wanderer_service'::text AND task_id IS NULL AND attempt_id IS NULL
    AND delegation_id IS NULL AND source_session_hash IS NULL AND actor_role = 'student'::text
    AND wanderer_id IS NOT NULL AND turn_id IS NOT NULL AND life_epoch IS NOT NULL
    AND authority_version IS NOT NULL AND life_epoch > 0 AND authority_version > 0)"""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def capture(conn, table_names):
    result = {"tables": {}, "platform_columns": {}, "platform_constraint": None}
    for table in TABLES:
        if table not in table_names:
            continue
        metadata = conn.execute("""SELECT a.attname,a.attnum,format_type(a.atttypid,a.atttypmod),a.attnotnull,
            pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated,a.attcollation::regcollation::text
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE n.nspname='public' AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (table,)).fetchall()
        columns = [row[0] for row in metadata]
        keys = [row[0] for row in conn.execute("""SELECT a.attname FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY x(attnum,ord)
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.attnum
            WHERE n.nspname='public' AND c.relname=%s AND k.contype='p' ORDER BY x.ord""", (table,))]
        selected = ",".join('"' + name.replace('"', '""') + '"::text' for name in columns)
        rows = []
        for raw in conn.execute(f'SELECT {selected} FROM public."{table}"'):
            values = dict(zip(columns, raw))
            rows.append({"key": digest([values[name] for name in keys] if keys else list(raw)),
                         "values": {name: value for name, value in values.items() if name in SAFE[table]},
                         "column_sha256": {name: digest(value) for name, value in values.items()},
                         "row_sha256": digest(list(raw))})
        result["tables"][table] = {"columns": columns, "column_types": {row[0]: row[2] for row in metadata},
                                    "rows": sorted(rows, key=lambda row: row["key"])}
        if table == "agent_platform_requests":
            result["platform_columns"] = {row[0]: list(row[1:]) for row in metadata}
            constraint = conn.execute("""SELECT pg_get_constraintdef(k.oid,true),k.convalidated FROM pg_constraint k
                WHERE k.conrelid='public.agent_platform_requests'::regclass
                AND k.conname='agent_platform_request_authority_source' AND k.contype='c'""").fetchone()
            result["platform_constraint"] = list(constraint) if constraint else None
    return result


def _valid_capture(value):
    if not isinstance(value, dict) or set(value) not in ({"tables", "platform_columns"}, {"tables", "platform_columns", "platform_constraint"}):
        return False
    if not isinstance(value["tables"], dict) or set(value["tables"]) - set(TABLES):
        return False
    for table, item in value["tables"].items():
        if (set(item) != {"columns", "column_types", "rows"} or len(item["columns"]) != len(set(item["columns"]))
                or set(item["column_types"]) != set(item["columns"])):
            return False
        keys = set()
        for row in item["rows"]:
            if (set(row) != {"key", "values", "column_sha256", "row_sha256"} or row["key"] in keys
                    or set(row["column_sha256"]) != set(item["columns"])
                    or set(row["values"]) != set(item["columns"]) & SAFE[table]
                    or any(not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{64}", h)
                           for h in (row["key"], row["row_sha256"], *row["column_sha256"].values()))
                    or any(h != digest(row["values"][name]) for name, h in row["column_sha256"].items() if name in row["values"])):
                return False
            keys.add(row["key"])
    return isinstance(value["platform_columns"], dict)


def authority_capture_matches(proof, table, before, after):
    """Bind the Agent aggregate witness to the independently verified cells."""
    if table not in {"agent_task_delegations", "agent_persistent_authorizations", "agent_platform_requests"} or not valid_report_proof(proof):
        return False
    for side, aggregate in (("before", before), ("after", after)):
        item = proof[side]["tables"].get(table)
        if not item or aggregate != {"columns": item["columns"], "row_count": len(item["rows"]),
                                    "rows_sha256": digest(sorted(row["row_sha256"] for row in item["rows"]))}:
            return False
    return True


def _prove(before, after):
    blockers, allowed, changes = [], set(), []
    if not _valid_capture(before) or not _valid_capture(after):
        return {"status": "failed", "blockers": ["invalid_capture"], "allowed_differences": [], "exact_changes": []}
    old, new = before["tables"], after["tables"]
    # Identity is established from existing bindings, never from model output.
    bound = {row["values"].get("student_id") for row in old.get("wanderer_agents", {}).get("rows", [])}
    subjects = {row["values"]["id"]: row for row in old.get("students", {}).get("rows", [])
                if row["values"].get("control_mode") == "ai" or row["values"].get("enrollment_status") == "wanderer" or row["values"]["id"] in bound}
    audits = {row["values"].get("student_id"): row for row in new.get("account_control_migrations", {}).get("rows", [])}
    old_audits = {row["values"].get("student_id"): row for row in old.get("account_control_migrations", {}).get("rows", [])}
    control_ran = "account_control_migrations" in new
    expected_audit_counts = {sid: {"had_human_password": row["column_sha256"].get("hashed_password") not in {digest(None), digest("")}}
                             for sid, row in subjects.items()}
    old_versions = {(r["values"]["wanderer_id"], r["values"]["peer_identity"]): r
                    for r in old.get("wanderer_relationship_versions", {}).get("rows", [])}
    versions = {(r["values"]["wanderer_id"], r["values"]["peer_identity"]): r
                for r in new.get("wanderer_relationship_versions", {}).get("rows", [])}
    migrated_pairs = set()

    def expect_cell(table, row, current, column, value):
        wanted = digest(value)
        if current["column_sha256"].get(column) != wanted:
            blockers.append(f"unexpected_cell:{table}:{row['key']}:{column}")
        if column in row["column_sha256"] and row["column_sha256"][column] != wanted:
            changes.append({"table": table, "key": row["key"], "column": column,
                            "before_sha256": row["column_sha256"][column], "after_sha256": wanted})
            allowed.add("table:" + table)

    for table, item in old.items():
        current = new.get(table)
        if current is None or not set(item["columns"]).issubset(current["columns"]):
            blockers.append("old_table_or_columns_removed:" + table)
            continue
        current_rows = {row["key"]: row for row in current["rows"]}
        original_keys = {row["key"] for row in item["rows"]}
        for row in item["rows"]:
            values, actual = row["values"], current_rows.get(row["key"])
            removed_session = control_ran and table == "user_sessions" and values.get("role") == "student" and values.get("user_id") in subjects
            if removed_session:
                if actual is not None:
                    blockers.append("ai_session_not_removed:" + row["key"])
                else:
                    allowed.add("table:" + table)
                    changes.append({"table": table, "key": row["key"], "column": "__deleted_ai_session__"})
                continue
            if actual is None:
                blockers.append("unexpected_row_deleted:" + table + ":" + row["key"])
                continue
            expected = {}
            if control_ran and table == "students":
                sid = values["id"]
                expected["control_mode"] = "ai" if sid in subjects else values.get("control_mode", "human")
                if sid in subjects:
                    expected.update(hashed_password=None, password_reset_required="0")
            if control_ran and table in REVOCATIONS:
                role, owner, field, active, patch = REVOCATIONS[table]
                sid = values.get(owner)
                matches = values.get(role) == "student" and sid in subjects and values.get(field) == active
                if matches:
                    expected.update(patch)
                    stamp = audits.get(sid, {}).get("values", {}).get("migrated_at")
                    if not stamp or not str(stamp).isdigit():
                        blockers.append("missing_account_migration_timestamp:" + str(sid))
                    elif sid not in old_audits:
                        if table == "wechat_bindings": expected["updated_at"] = str(stamp)
                        elif table != "mp_sessions": expected["revoked_at"] = str(stamp)
                    else:
                        # Reopened credentials are forbidden in a quiescent,
                        # already migrated input: investigate instead of widening
                        # the timestamp rule for an unreviewed repair.
                        blockers.append("active_credential_after_prior_takeover:" + table + ":" + str(sid))
            if table == "wanderer_relationships" and "wanderer_relationship_versions" in new:
                pair = (values["wanderer_id"], values["peer_identity"])
                if pair not in old_versions:
                    migrated_pairs.add(pair)
                    def stored_float(value, kind):
                        number = float(value or 0)
                        return struct.unpack("!f", struct.pack("!f", number))[0] if kind == "real" else number
                    # The production adapter uses psycopg text decoding: its
                    # input is PostgreSQL's shortest text representation, then
                    # the arithmetic result is rounded by the target SQL type.
                    affinity = float(values.get("affinity") or 0)
                    mapped = max(0.0, min(100.0, (affinity + 100.0) / 2.0))
                    # PostgreSQL REAL/DOUBLE text may spell integral values as
                    # 0 or 0.0; compare the exposed numeric value and bind hash.
                    actual_value = actual["values"].get("affinity")
                    if (actual_value is None or stored_float(actual_value, current["column_types"]["affinity"])
                            != stored_float(mapped, current["column_types"]["affinity"])):
                        blockers.append("relationship_mapping_invalid:" + row["key"])
                    expected["affinity"] = actual_value
                    version = versions.get(pair)
                    if (not version or version["values"].get("origin") != "legacy" or version["values"].get("revision") != "0"
                            or stored_float(version["values"].get("legacy_affinity"), new["wanderer_relationship_versions"]["column_types"]["legacy_affinity"])
                               != stored_float(affinity, new["wanderer_relationship_versions"]["column_types"]["legacy_affinity"])
                            or version["values"].get("source_turn_id", "") != ""):
                        blockers.append("relationship_origin_invalid:" + row["key"])
            if table == "agent_platform_requests":
                expected.update({name: value for name, value in PLATFORM_ADDITIONS.items() if name not in item["columns"]})
            for column in item["columns"]:
                if column not in expected and row["column_sha256"][column] != actual["column_sha256"].get(column):
                    blockers.append(f"unrelated_cell_changed:{table}:{row['key']}:{column}")
            for column, value in expected.items():
                expect_cell(table, row, actual, column, value)
        extras = [row for row in current["rows"] if row["key"] not in original_keys]
        if extras and table not in {"account_control_migrations", "wanderer_relationship_versions"}:
            blockers.append("unexpected_rows_added:" + table)

    if control_ran:
        if set(audits) != set(old_audits) | set(subjects):
            blockers.append("account_audit_subjects_mismatch")
        for sid in subjects:
            if sid in old_audits:
                continue
            facts = expected_audit_counts[sid]
            for table in ("user_sessions", *REVOCATIONS):
                if table not in old:
                    continue
                if table == "user_sessions": role, owner, field, active = "role", "user_id", None, None
                else: role, owner, field, active, _ = REVOCATIONS[table]
                facts[table] = sum(r["values"].get(role) == "student" and r["values"].get(owner) == sid
                                   and (field is None or r["values"].get(field) == active) for r in old[table]["rows"])
            audit = audits.get(sid, {}).get("values", {})
            try:
                if json.loads(audit.get("evidence_json", "null")) != facts or not str(audit.get("migrated_at", "")).isdigit():
                    blockers.append("account_audit_evidence_invalid:" + sid)
            except (ValueError, TypeError): blockers.append("account_audit_evidence_invalid:" + sid)
        if "account_control_migrations" in old and set(audits) != set(old_audits):
            allowed.add("table:account_control_migrations")
    if "wanderer_relationship_versions" in new:
        if set(versions) != set(old_versions) | migrated_pairs:
            blockers.append("relationship_origin_subjects_mismatch")
        if "wanderer_relationship_versions" in old and set(versions) != set(old_versions):
            allowed.add("table:wanderer_relationship_versions")

    for name, metadata in before["platform_columns"].items():
        current = after["platform_columns"].get(name)
        expected = list(metadata)
        if name in PLATFORM_NULLABLE:
            expected[2] = False
        if current != expected:
            blockers.append("platform_column_definition_changed:" + name)
        elif current != metadata:
            allowed.add("schema:column:agent_platform_requests." + name)
    if "agent_platform_requests" in new:
        constraint = after.get("platform_constraint")
        normalized = lambda text: re.sub(r"\s+", "", text).replace("::text", "")
        if (not isinstance(constraint, list) or len(constraint) != 2 or constraint[1] is not True
                or normalized(constraint[0]) != normalized(PLATFORM_AUTHORITY_CHECK)):
            blockers.append("platform_authority_constraint_missing_or_changed")
    return {"status": "failed" if blockers else "ok", "blockers": sorted(set(blockers)),
            "allowed_differences": sorted(allowed, key=lambda s: ({"table": 0, "schema": 1}.get(s.split(":", 1)[0], 2), s)) if not blockers else [],
            "exact_changes": sorted(changes, key=lambda r: (r["table"], r["key"], r["column"]))}


def prove(before, after):
    return {"contract": PROOF_CONTRACT, "before": before, "after": after, **_prove(before, after)}


def valid_report_proof(proof):
    try:
        return (isinstance(proof, dict) and proof.get("contract") == PROOF_CONTRACT and proof.get("status") == "ok"
                and proof == prove(proof["before"], proof["after"]))
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return False


def startup_contract(conn):
    """Supplement the central PG inventory without changing its legacy scope."""
    from classroom_app.db.schema_wanderers import _table_statements
    from classroom_app.db.schema_wanderer_runtime import TABLES as runtime
    from classroom_app.db.schema_wanderer_reflection import TABLES as reflection
    from classroom_app.db.schema_wanderer_world import TABLES as world, AGENT_COLUMNS, VERSION
    statements = [*_table_statements("id BIGINT PRIMARY KEY"), *world]
    tables = {match.group(1) for sql in statements if (match := re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))}
    tables.update(runtime)
    tables.update(reflection)
    tables.add('wanderer_message_deliveries')
    tables.update({"account_control_migrations", "wanderer_platform_ai_jobs", "wanderer_platform_private_ai_jobs",
                   "wanderer_relationship_versions", "wanderer_relationship_changes", "wanderer_group_observations", "wanderer_private_message_operations"})
    columns = {"students": {"control_mode"}, "wanderer_agents": set(AGENT_COLUMNS),
               "agent_platform_requests": set(PLATFORM_ADDITIONS), "wanderer_cultivation_awards": {"debt_paid_units"}}
    present = {row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    actual = {}
    for table, column in conn.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public'"):
        actual.setdefault(table, set()).add(column)
    missing_columns = sorted(f"{table}.{name}" for table, names in columns.items() for name in names - actual.get(table, set()))
    version = conn.execute("SELECT value FROM wanderer_world_schema WHERE key='version'").fetchone() if "wanderer_world_schema" in present else None
    return {"required_table_count": len(tables), "present_required_table_count": len(tables & present),
            "missing_tables": sorted(tables - present), "missing_columns": missing_columns,
            "world_schema_version": version[0] if version else None, "expected_world_schema_version": VERSION,
            "status": "ok" if tables <= present and not missing_columns and version and str(version[0]) == VERSION else "failed"}
