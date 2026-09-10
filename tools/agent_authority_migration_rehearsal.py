"""Independent, exact proof of the Agent actor/key schema migration.

Capture runs only on the explicitly restored native PostgreSQL clone. Prompt,
file, session and credential contents are represented only by per-column hashes.
This module does not call migrations, connect to a database, or authorize broad
table differences: every old cell and schema object is checked independently.
"""
from __future__ import annotations

import hashlib
import json
import re


PROOF_CONTRACT = "agent-authority-migration-v1"
TABLES = ("agent_tasks", "agent_task_composers", "agent_runtime_api_keys")
AUTHORITY_TABLES = (
    "agent_task_attempts", "agent_persistent_authorizations", "agent_task_delegations",
    "agent_action_executions", "agent_model_requests", "agent_request_buckets",
    "agent_request_budget_leases", "agent_model_configuration_lock", "agent_task_questions", "agent_platform_requests",
)
SAFE_FIELDS = {"id", "teacher_id", "actor_role", "actor_id", "provider", "enabled", "is_active", "updated_at"}
SOURCE_FIELDS = {"source_session_hash", "source_session_key", "persistent_authorization_id"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _identity(table, values):
    if table != "agent_task_composers":
        return str(values.get("id"))
    role = values.get("actor_role") or "teacher"
    actor_id = values.get("actor_id")
    if actor_id is None and role == "teacher":
        actor_id = values.get("teacher_id")
    return f"{role}:{actor_id}"


def _row_evidence(table, values):
    return {
        "key": _identity(table, values),
        "values": {name: value for name, value in values.items() if name in SAFE_FIELDS},
        "column_sha256": {name: _digest(value) for name, value in values.items()},
        "null_columns": sorted(name for name, value in values.items() if value is None),
    }


def capture(conn, table_names):
    result = {"tables": {}, "authority_tables": {}}
    for table in TABLES:
        if table not in table_names:
            continue
        metadata = conn.execute("""
            SELECT a.attname, a.attnum, format_type(a.atttypid,a.atttypmod), a.attnotnull,
                pg_get_expr(d.adbin,d.adrelid), a.attidentity, a.attgenerated, a.attcollation::regcollation::text
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE n.nspname='public' AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum
        """, (table,)).fetchall()
        columns = [row[0] for row in metadata]
        selected = ",".join('"' + name.replace('"', '""') + '"::text' for name in columns)
        rows = [_row_evidence(table, dict(zip(columns, row))) for row in conn.execute(f'SELECT {selected} FROM public."{table}"')]
        constraints = {row[0]: {"kind": row[1], "definition": row[2], "validated": row[3], "deferrable": row[4], "deferred": row[5]}
                       for row in conn.execute("""
            SELECT k.conname, k.contype, pg_get_constraintdef(k.oid,true), k.convalidated, k.condeferrable, k.condeferred
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=%s ORDER BY k.conname
        """, (table,))}
        indexes = {row[0]: {"unique": row[1], "primary": row[2], "valid": row[3], "columns": list(row[4]),
                            "predicate": row[5], "definition_sha256": _digest(row[6])}
                   for row in conn.execute("""
            SELECT ci.relname, i.indisunique, i.indisprimary, i.indisvalid,
                ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY x(attnum,ord)
                      JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=x.attnum ORDER BY x.ord),
                pg_get_expr(i.indpred,i.indrelid), pg_get_indexdef(i.indexrelid)
            FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_class ci ON ci.oid=i.indexrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=%s ORDER BY ci.relname
        """, (table,))}
        result["tables"][table] = {"columns": columns, "column_definitions": {
            row[0]: dict(zip(("ordinal", "type", "not_null", "default", "identity", "generated", "collation"), row[1:]))
            for row in metadata}, "rows": sorted(rows, key=lambda row: row["key"]), "constraints": constraints, "indexes": indexes}
        if table == "agent_task_composers":
            owned = conn.execute("""
                SELECT sn.nspname, s.relname, d.deptype FROM pg_depend d
                JOIN pg_class s ON s.oid=d.objid AND s.relkind='S'
                JOIN pg_namespace sn ON sn.oid=s.relnamespace
                JOIN pg_class t ON t.oid=d.refobjid
                JOIN pg_namespace tn ON tn.oid=t.relnamespace
                JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
                WHERE d.classid='pg_class'::regclass AND d.refclassid='pg_class'::regclass
                  AND tn.nspname='public' AND t.relname='agent_task_composers'
                  AND a.attname='teacher_id' AND a.attidentity IN ('a','d') AND d.deptype='i'
            """).fetchall()
            if len(owned) > 1: raise RuntimeError("Ambiguous composer identity sequence")
            result["tables"][table]["identity_sequence"] = (
                dict(zip(("schema", "name", "dependency"), owned[0])) if owned else None)
    for table in AUTHORITY_TABLES:
        if table not in table_names:
            continue
        columns = [row[0] for row in conn.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""", (table,))]
        selected = ",".join('"' + name.replace('"', '""') + '"::text' for name in columns)
        hashes = [_digest(list(row)) for row in conn.execute(f'SELECT {selected} FROM public."{table}"')]
        record = {"columns": columns, "row_count": len(hashes), "rows_sha256": _digest(sorted(hashes))}
        if table == "agent_model_configuration_lock":
            record["lock_rows"] = [list(row) for row in conn.execute("SELECT id,revision FROM agent_model_configuration_lock ORDER BY id")]
        result["authority_tables"][table] = record
    return result


def _valid_capture(value):
    if not isinstance(value, dict) or set(value) != {"tables", "authority_tables"}:
        return False
    if not isinstance(value["tables"], dict) or set(value["tables"]) - set(TABLES):
        return False
    if not isinstance(value["authority_tables"], dict) or set(value["authority_tables"]) - set(AUTHORITY_TABLES):
        return False
    for table, item in value["tables"].items():
        base = {"columns", "column_definitions", "rows", "constraints", "indexes"}
        if set(item) not in (base, base | {"identity_sequence"} if table == "agent_task_composers" else base):
            return False
        sequence = item.get("identity_sequence")
        if sequence is not None and (not isinstance(sequence, dict) or set(sequence) != {"schema", "name", "dependency"}
                or sequence["schema"] != "public" or sequence["dependency"] != "i"
                or not isinstance(sequence["name"], str) or not sequence["name"]):
            return False
        columns = item["columns"]
        if len(columns) != len(set(columns)) or set(item["column_definitions"]) != set(columns):
            return False
        keys = set()
        for row in item["rows"]:
            if (set(row) != {"key", "values", "column_sha256", "null_columns"}
                    or set(row["values"]) != set(columns) & SAFE_FIELDS
                    or set(row["column_sha256"]) != set(columns)
                    or row["key"] != _identity(table, row["values"]) or row["key"] in keys
                    or len(row["null_columns"]) != len(set(row["null_columns"]))):
                return False
            keys.add(row["key"])
            if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in row["column_sha256"].values()):
                return False
            if set(row["null_columns"]) != {name for name, digest in row["column_sha256"].items() if digest == _digest(None)}:
                return False
            if any(value is not None and not isinstance(value, str) for value in row["values"].values()):
                return False
            if any(row["column_sha256"][name] != _digest(value) for name, value in row["values"].items()):
                return False
    for table, item in value["authority_tables"].items():
        if (set(item) != {"columns", "row_count", "rows_sha256"} | ({"lock_rows"} if table == "agent_model_configuration_lock" else set())
                or type(item["row_count"]) is not int or item["row_count"] < 0
                or not isinstance(item["rows_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["rows_sha256"])):
            return False
        if item["row_count"] == 0 and item["rows_sha256"] != _digest([]):
            return False
    return True


def _required_index(item, name, columns, predicate=None):
    index = item["indexes"].get(name, {})
    actual_predicate = re.sub(r"[\s()]", "", index.get("predicate") or "")
    return (index.get("unique") is True and index.get("valid") is True and index.get("primary") is False
            and index.get("columns") == columns and actual_predicate == (predicate or ""))


def _positive(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[1-9][0-9]*", value))


def _expected_selected(rows):
    winners = {}
    for row in rows:
        values = row["values"]
        if values.get("is_active") != "1":
            continue
        # Matches native PostgreSQL DESC (NULLS FIRST), independently of the
        # migration's ROW_NUMBER SQL. Key columns use their original text form.
        rank = (values.get("enabled") is None, int(values.get("enabled") or 0),
                values.get("updated_at") is None, values.get("updated_at") or "", int(values["id"]))
        provider = values.get("provider")
        if provider not in winners or rank > winners[provider][0]:
            winners[provider] = (rank, row["key"])
    return {value[1] for value in winners.values()}


def _difference_order(name):
    return ({"table": 0, "schema": 1, "sequence": 2}.get(name.split(":", 1)[0], 3), name)


def _prove(before, after):
    blockers, expected_changes, actual_changes, allowed = [], [], [], set()
    if not _valid_capture(before) or not _valid_capture(after):
        return {"status": "failed", "blockers": ["invalid_agent_capture"], "expected_changes": [], "actual_changes": [], "allowed_differences": []}
    for table, initial in before["tables"].items():
        current = after["tables"].get(table)
        if current is None:
            blockers.append("agent_table_removed:" + table)
            continue
        old_rows = {row["key"]: row for row in initial["rows"]}
        new_rows = {row["key"]: row for row in current["rows"]}
        if set(old_rows) != set(new_rows):
            blockers.append("agent_row_identities_changed:" + table)
        if set(initial["columns"]) - set(current["columns"]):
            blockers.append("agent_old_columns_removed:" + table)
        selected = _expected_selected(initial["rows"]) if table == "agent_runtime_api_keys" else set()
        for key, old in old_rows.items():
            new = new_rows.get(key)
            if not new:
                continue
            expected = dict(old["values"])
            mutable = set()
            if table in {"agent_tasks", "agent_task_composers"}:
                role = expected.get("actor_role") or "teacher"
                actor_id = expected.get("actor_id")
                if actor_id is None and role == "teacher":
                    actor_id = expected.get("teacher_id")
                if role not in {"teacher", "student"} or not _positive(actor_id):
                    blockers.append(f"legacy_agent_identity_invalid:{table}:{key}")
                if role == "teacher" and expected.get("teacher_id") != actor_id:
                    blockers.append(f"legacy_teacher_identity_mismatch:{table}:{key}")
                expected.update(actor_role=role, actor_id=actor_id)
                mutable = {"actor_role", "actor_id"}
                for name in SOURCE_FIELDS - set(initial["columns"]):
                    if name in current["columns"] and name not in new["null_columns"]:
                        blockers.append(f"agent_source_authority_forged:{table}:{key}:{name}")
            else:
                if expected.get("is_active") == "1" and key not in selected:
                    expected["is_active"] = "0"
                mutable = {"is_active"}
                if "deleted_at" not in initial["columns"] and "deleted_at" not in new["null_columns"]:
                    blockers.append("agent_key_soft_deleted_during_migration:" + key)
            for name in mutable:
                old_value, expected_value, actual_value = old["values"].get(name), expected.get(name), new["values"].get(name)
                evidence = {"table": table, "key": key, "field": name, "old_column": name in initial["columns"], "before": old_value}
                if old_value != expected_value or name not in initial["columns"]:
                    expected_changes.append({**evidence, "after": expected_value})
                if old_value != actual_value or name not in initial["columns"]:
                    actual_changes.append({**evidence, "after": actual_value})
                if name not in current["columns"] or actual_value != expected_value:
                    blockers.append(f"agent_identity_or_selection_transition_mismatch:{table}:{key}:{name}")
            for name in initial["columns"]:
                expected_hash = _digest(expected[name]) if name in mutable else old["column_sha256"][name]
                if new["column_sha256"].get(name) != expected_hash:
                    blockers.append(f"agent_old_cell_changed:{table}:{key}:{name}")
                if new["column_sha256"].get(name) != old["column_sha256"][name]:
                    allowed.add("table:" + table)

        for name, definition in initial["column_definitions"].items():
            wanted = dict(definition)
            if table in {"agent_tasks", "agent_task_composers"} and name == "teacher_id":
                wanted["not_null"] = False
                if table == "agent_task_composers" and definition.get("identity") in {"a", "d"}:
                    wanted["identity"] = ""
                    owned = initial.get("identity_sequence")
                    if not owned or current.get("identity_sequence") is not None:
                        blockers.append("agent_composer_identity_sequence_unproven")
                    else:
                        allowed.add("schema:sequence:" + owned["name"])
                        allowed.add("sequence:" + owned["name"])
            actual = current["column_definitions"].get(name)
            if actual != wanted:
                blockers.append(f"agent_column_definition_changed:{table}:{name}")
            elif actual != definition:
                allowed.add(f"schema:column:{table}.{name}")
        for name, definition in initial["constraints"].items():
            actual = current["constraints"].get(name)
            old_teacher_pk = (table == "agent_task_composers" and name == "agent_task_composers_pkey"
                              and definition == {"kind": "p", "definition": "PRIMARY KEY (teacher_id)", "validated": True, "deferrable": False, "deferred": False})
            if old_teacher_pk and actual is None:
                allowed.add(f"schema:constraint:{table}.{name}")
            elif actual != definition:
                blockers.append(f"agent_constraint_changed:{table}:{name}")
        for name, definition in initial["indexes"].items():
            actual = current["indexes"].get(name)
            old_teacher_index = (table == "agent_task_composers" and name == "agent_task_composers_pkey"
                                 and definition["columns"] == ["teacher_id"] and definition["primary"] is True and definition["unique"] is True)
            if old_teacher_index and actual is None:
                allowed.add("schema:index:" + name)
            elif actual != definition:
                blockers.append(f"agent_index_changed:{table}:{name}")

    for table, item in after["tables"].items():
        if table not in before["tables"] and item["rows"]:
            blockers.append("new_agent_table_contains_rows:" + table)
        if table in {"agent_tasks", "agent_task_composers"}:
            for name in ("actor_role", "actor_id"):
                meta = item["column_definitions"].get(name, {})
                valid = (meta.get("type") == "text" and meta.get("not_null") is True and meta.get("default") in {"'teacher'::text", "'teacher'"}) if name == "actor_role" else meta.get("type") in {"integer", "bigint"}
                if not valid:
                    blockers.append(f"agent_actor_column_missing_or_invalid:{table}:{name}")
            if table == "agent_tasks":
                for name in SOURCE_FIELDS:
                    meta = item["column_definitions"].get(name, {})
                    if meta.get("type") != "text" or meta.get("not_null") is not False or meta.get("default") is not None:
                        blockers.append("agent_source_column_invalid:" + name)
            elif not _required_index(item, "idx_agent_composers_actor", ["actor_role", "actor_id"]):
                blockers.append("agent_composer_actor_uniqueness_missing")
        elif not _required_index(item, "idx_agent_keys_one_selected_provider", ["provider"], "is_active=1"):
            blockers.append("agent_key_active_uniqueness_missing")

    for table, original in before["authority_tables"].items():
        if after["authority_tables"].get(table) != original:
            blockers.append("existing_agent_authority_or_receipts_changed:" + table)
    for table, current in after["authority_tables"].items():
        if table in before["authority_tables"]:
            continue
        if table == "agent_model_configuration_lock":
            if (current.get("lock_rows") != [[1, 0]] or current["row_count"] != 1
                    or current["columns"] != ["id", "revision"]
                    or current["rows_sha256"] != _digest([_digest(["1", "0"])])):
                blockers.append("new_agent_model_lock_invalid")
        elif current["row_count"]:
            blockers.append("new_agent_authority_or_receipts_forged:" + table)
    if after["tables"] and set(AUTHORITY_TABLES) - set(after["authority_tables"]):
        blockers.append("required_agent_authority_tables_missing")
    order = lambda change: (change["table"], change["key"], change["field"])
    expected_changes.sort(key=order)
    actual_changes.sort(key=order)
    if expected_changes != actual_changes:
        blockers.append("agent_exact_changes_mismatch")
    return {"status": "failed" if blockers else "ok", "blockers": sorted(set(blockers)),
            "expected_changes": expected_changes, "actual_changes": actual_changes,
            "allowed_differences": sorted(allowed, key=_difference_order) if not blockers else []}


def prove(before, after):
    return {"contract": PROOF_CONTRACT, "before": before, "after": after, **_prove(before, after)}


def valid_report_proof(proof):
    try:
        return (isinstance(proof, dict) and proof.get("contract") == PROOF_CONTRACT and proof.get("status") == "ok"
                and proof == prove(proof["before"], proof["after"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
