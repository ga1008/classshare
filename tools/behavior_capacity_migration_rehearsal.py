"""Prove the recorder capacity migration adds only three precise indexes.

This module reads an explicitly supplied rehearsal connection. It never
discovers a DSN, initializes schema, starts workers, or mutates a database.
Full table/sequence hashes come from the owning rehearsal's existing snapshot.
"""
from __future__ import annotations

import re
from typing import Any


PROOF_CONTRACT = "behavior-capacity-indexes-v1"
TABLE = "behavior_write_inbox"
EXPECTED_INDEXES = {
    "idx_behavior_inbox_open_capacity": {
        "keys": ["state"], "predicate": "state IN ('pending', 'quarantined')",
    },
    "idx_behavior_inbox_pending_actor_order": {
        "keys": ["user_role", "user_pk", "received_at", "batch_key"], "predicate": "state = 'pending'",
    },
    "idx_behavior_inbox_retired_cleanup": {
        "keys": ["COALESCE(resolved_at, applied_at, received_at)", "batch_key"],
        "predicate": "state IN ('applied', 'discarded') AND SUBSTR(batch_id, 1, 3) = 'b1_'",
    },
}


def _normalized(value: str | None) -> str:
    # pg_get_expr renders IN as = ANY(ARRAY[...::text]). The accepted predicates
    # have no nested boolean alternatives; preserve every token except the
    # documented deparser punctuation/type decoration.
    value = re.sub(r"\s+", "", str(value or "").lower()).replace("::text", "")
    value = value.replace("(", "").replace(")", "")
    value = value.replace("in'pending','quarantined'", "=anyarray['pending','quarantined']")
    value = value.replace("in'applied','discarded'", "=anyarray['applied','discarded']")
    return value


def capture(conn, full_snapshot: dict[str, Any]) -> dict[str, Any]:
    present = TABLE in full_snapshot["tables"]
    indexes = {}
    if present:
        rows = conn.execute("""SELECT c.relname, am.amname, i.indisunique, i.indisprimary,
                i.indisvalid, i.indisready, i.indnkeyatts, i.indnatts,
                pg_get_expr(i.indpred, i.indrelid), pg_get_indexdef(i.indexrelid), c.reloptions,
                ARRAY(SELECT pg_get_indexdef(i.indexrelid, key_number, true)
                      FROM generate_series(1, i.indnatts) AS key_number ORDER BY key_number)
            FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am am ON am.oid=c.relam
            WHERE n.nspname='public' AND t.relname=%s ORDER BY c.relname""", (TABLE,))
        for row in rows:
            indexes[row[0]] = dict(zip(
                ("method", "unique", "primary", "valid", "ready", "key_count", "attribute_count",
                 "predicate", "definition", "options", "keys"), row[1:]))
    non_index = {}
    for key, value in full_snapshot["schema_objects"].items():
        kind, name = key.split(":", 1)
        if ((kind == "relation" and name == TABLE)
                or (kind in {"column", "constraint", "trigger", "policy"} and name.startswith(TABLE + "."))):
            non_index[key] = value
    # This proof owns recorder indexes and recorder-owned sequences only.
    # The enclosing full snapshot still checks every database sequence; other
    # deliberate migrations (e.g. removing an obsolete Agent identity) need
    # their own exact proof and must not be contradicted by this index proof.
    owned_sequences = {row[0] for row in conn.execute("""SELECT s.relname FROM pg_depend d
        JOIN pg_class s ON s.oid=d.objid AND s.relkind='S'
        JOIN pg_namespace sn ON sn.oid=s.relnamespace
        JOIN pg_class t ON t.oid=d.refobjid JOIN pg_namespace tn ON tn.oid=t.relnamespace
        WHERE d.classid='pg_class'::regclass AND d.refclassid='pg_class'::regclass
          AND d.deptype IN ('a','i') AND sn.nspname='public' AND tn.nspname='public' AND t.relname=%s""", (TABLE,))}
    return {"present": present, "table": full_snapshot["tables"].get(TABLE),
            "non_index_schema": non_index, "indexes": indexes,
            "sequences": {name: value for name, value in full_snapshot["sequences"].items() if name in owned_sequences}}


def _expected_index(name: str, value: dict[str, Any]) -> bool:
    expected = EXPECTED_INDEXES[name]
    return (value.get("method") == "btree" and value.get("unique") is False
            and value.get("primary") is False and value.get("valid") is True and value.get("ready") is True
            and value.get("key_count") == value.get("attribute_count") == len(expected["keys"])
            and value.get("options") is None
            and [_normalized(key) for key in value.get("keys", [])] == [_normalized(key) for key in expected["keys"]]
            and _normalized(value.get("predicate")) == _normalized(expected["predicate"]))


def prove(before, after):
    blockers = []
    if before["present"] != after["present"]:
        blockers.append("recorder_table_presence_changed")
    if before["table"] != after["table"]:
        blockers.append("recorder_rows_columns_or_relationships_changed")
    if before["non_index_schema"] != after["non_index_schema"]:
        blockers.append("recorder_non_index_schema_changed")
    if any(after["sequences"].get(name) != value for name, value in before["sequences"].items()):
        blockers.append("existing_sequence_changed")
    old, current = before["indexes"], after["indexes"]
    if any(current.get(name) != value for name, value in old.items()):
        blockers.append("existing_recorder_index_changed")
    added = sorted(set(current) - set(old))
    expected_added = sorted(set(EXPECTED_INDEXES) - set(old)) if after["present"] else []
    if added != expected_added:
        blockers.append("unexpected_new_recorder_indexes")
    if after["present"]:
        for name in EXPECTED_INDEXES:
            if name not in current or not _expected_index(name, current[name]):
                blockers.append("capacity_index_definition_invalid:" + name)
    return {"contract": PROOF_CONTRACT, "status": "failed" if blockers else "ok",
            "before": before, "after": after, "expected_added_indexes": expected_added,
            "added_indexes": added, "rows_columns_constraints_and_old_indexes_unchanged": not any(
                reason.startswith(("recorder_", "existing_recorder")) for reason in blockers),
            "existing_sequences_unchanged": "existing_sequence_changed" not in blockers,
            # Added objects are absent from the old schema projection; no old
            # field difference receives a generic schema/index whitelist.
            "allowed_differences": [], "blockers": sorted(set(blockers))}


def valid_report_proof(proof):
    try:
        return (isinstance(proof, dict) and proof.get("contract") == PROOF_CONTRACT
                and proof.get("status") == "ok" and proof == prove(proof["before"], proof["after"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
