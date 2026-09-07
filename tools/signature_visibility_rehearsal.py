"""Narrow, independently calculated proof for the one-time signature scope repair."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

VERSION = "20260908_signature_visibility_levels_v1"
PROOF_CONTRACT = "signature-visibility-migration-v1"
ORG_FIELDS = ("school_code", "college", "department")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def capture(conn, table_names):
    result = {"signatures": {}, "markers": {}}
    for table, bucket, keys in (("electronic_signatures", "signatures", ("id",)),
                                ("schema_migrations", "markers", ("version", "db_engine"))):
        if table not in table_names:
            continue
        columns = [row[0] for row in conn.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""", (table,))]
        if not set(keys).issubset(columns):
            continue
        selected = ",".join('"' + name.replace('"', '""') + '"::text' for name in columns)
        for row in conn.execute(f'SELECT {selected} FROM public."{table}"'):
            values = dict(zip(columns, row))
            key = "|".join(str(values[name]) for name in keys)
            if bucket == "signatures":
                result[bucket][key] = {name: values.get(name) for name in ("id", "scope_level", *ORG_FIELDS)}
                result[bucket][key]["other_columns_sha256"] = _digest(
                    {name: value for name, value in values.items() if name != "scope_level"})
            else:
                result[bucket][key] = values
    return result


def expected_level(row):
    level = row.get("scope_level")
    if level not in {"college", "department"}:
        return level
    if not str(row.get("department") or "").strip() and str(row.get("school_code") or "").strip():
        return "college" if str(row.get("college") or "").strip() else "school"
    return "department"


def prove(before, after):
    marker_key = VERSION + "|postgres"
    initial_marker = before["markers"].get(marker_key)
    pending = not initial_marker or initial_marker.get("success") not in {"true", "t", "1"}
    expected, actual, blockers = [], [], []
    originals, current = before["signatures"], after["signatures"]
    if set(originals) != set(current):
        blockers.append("signature_ids_changed")
    for key, row in originals.items():
        target = expected_level(row) if pending else row["scope_level"]
        evidence = {"id": int(key), "field": "scope_level", "before": row["scope_level"],
                    "after": target, **{name: row.get(name) for name in ORG_FIELDS}}
        if target != row["scope_level"]:
            expected.append(evidence)
        found = current.get(key)
        if not found:
            continue
        if found["other_columns_sha256"] != row["other_columns_sha256"]:
            blockers.append("signature_other_columns_changed:" + key)
        if found["scope_level"] != row["scope_level"]:
            actual.append({**evidence, "after": found["scope_level"]})
        if found["scope_level"] != target:
            blockers.append("signature_scope_transition_mismatch:" + key)
    expected.sort(key=lambda row: row["id"])
    actual.sort(key=lambda row: row["id"])
    old_markers, new_markers = dict(before["markers"]), dict(after["markers"])
    added_marker = None
    if pending and initial_marker is None and marker_key in new_markers:
        added_marker = new_markers.pop(marker_key)
        valid = (set(added_marker) == {"version", "name", "checksum", "applied_at", "duration_ms", "db_engine", "success", "error"}
                 and added_marker["version"] == VERSION and added_marker["db_engine"] == "postgres"
                 and added_marker["name"] == "Explicit signature visibility levels"
                 and bool(re.fullmatch(r"[a-f0-9]{64}", added_marker["checksum"] or ""))
                 and bool(added_marker["applied_at"]) and added_marker["duration_ms"] == "0"
                 and added_marker["success"] in {"true", "t", "1"} and added_marker["error"] is None)
        if not valid:
            blockers.append("migration_marker_invalid")
    elif pending and originals:
        blockers.append("migration_marker_missing_or_rewritten")
    if old_markers != new_markers:
        blockers.append("old_migration_records_changed")
    allowed = []
    if not blockers:
        if actual:
            allowed.append("table:electronic_signatures")
        if added_marker:
            allowed.append("table:schema_migrations")
    return {"contract": PROOF_CONTRACT, "status": "failed" if blockers else "ok",
            "expected_changes": expected, "actual_changes": actual,
            "changed_fields": ["scope_level"] if actual else [],
            "all_other_signature_columns_unchanged": not any("other_columns" in item for item in blockers),
            "old_migration_records_unchanged": old_markers == new_markers,
            "added_migration_marker": added_marker, "allowed_differences": allowed,
            "signature_row_count": len(originals), "blockers": blockers}


def valid_report_proof(proof):
    if not isinstance(proof, dict) or proof.get("contract") != PROOF_CONTRACT or proof.get("status") != "ok":
        return False
    changes = proof.get("expected_changes")
    if (not isinstance(changes, list) or changes != proof.get("actual_changes")
            or proof.get("changed_fields") != (["scope_level"] if changes else [])
            or proof.get("all_other_signature_columns_unchanged") is not True
            or proof.get("old_migration_records_unchanged") is not True or proof.get("blockers") != []):
        return False
    ids = set()
    for row in changes:
        if (not isinstance(row, dict) or set(row) != {"id", "field", "before", "after", *ORG_FIELDS}
                or not isinstance(row["id"], int) or row["id"] <= 0 or row["id"] in ids
                or row["field"] != "scope_level" or row["before"] == row["after"]
                or expected_level({**row, "scope_level": row["before"]}) != row["after"]):
            return False
        ids.add(row["id"])
    marker = proof.get("added_migration_marker")
    if changes and marker is None:
        return False
    if marker is not None:
        if not isinstance(marker, dict):
            return False
        # Reuse the strict marker check, with no signature rows to repair.
        check = prove({"signatures": {}, "markers": {}},
                      {"signatures": {}, "markers": {VERSION + "|postgres": marker}})
        if check["status"] != "ok":
            return False
        try:
            if datetime.fromisoformat(marker["applied_at"]).tzinfo is None:
                return False
        except (TypeError, ValueError):
            return False
    allowed = (["table:electronic_signatures"] if changes else []) + (["table:schema_migrations"] if marker else [])
    return proof.get("allowed_differences") == allowed
