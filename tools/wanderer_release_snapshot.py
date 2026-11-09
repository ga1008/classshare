"""Read-only production aggregates for a wanderer release; no application import.

Uses only DATABASE_URL already supplied to the running container. Credentials,
identities, message bodies, prompts, memories and provider output are never
selected. No migrations, scheduler, model request or business action is invoked.
Missing schema fails closed. No messages since release means not observed, not a
failed communication chain. Prove that chain with wanderer_release_verify.py.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import sys
from zoneinfo import ZoneInfo


REQUIRED_TABLES = frozenset({
    "students", "wanderer_agents", "account_control_migrations", "user_sessions",
    "social_groups", "social_group_members", "social_messages", "private_messages",
    "wanderer_private_message_operations", "wanderer_group_observations",
    "wanderer_social_inbox", "wanderer_wake_events", "wanderer_turns",
    "wanderer_commands", "wanderer_resource_reservations", "wanderer_resource_usage",
    "wanderer_world_schema", "wanderer_runtime_settings",
})
OPTIONAL_AUTH_TABLES = frozenset({"remember_login_tokens", "mp_sessions", "wechat_bindings",
                                 "agent_task_delegations", "agent_persistent_authorizations"})

AI = "SELECT 'student:' || CAST(student_id AS TEXT) FROM wanderer_agents"


def collect_snapshot(conn, *, since_local: str, present_tables: set[str]) -> dict:
    """Only SELECTs, usable with a DB-API connection; returns no row identities."""
    def count(sql, params=(), *, optional_table=None):
        if optional_table and optional_table not in present_tables:
            return 0
        return int(conn.execute(sql, params).fetchone()[0] or 0)

    def states(table, allowed):
        # Table names and possible state values are compile-time allowlists.
        values = ",".join("'" + state + "'" for state in allowed)
        rows = conn.execute(
            f"SELECT CASE WHEN state IN ({values}) THEN state ELSE 'other' END AS state_key, COUNT(*) "
            f"FROM {table} GROUP BY 1 ORDER BY 1"
        ).fetchall()
        return {row[0]: int(row[1]) for row in rows}

    checks = {
        "wanderers_missing_student": count("SELECT COUNT(*) FROM wanderer_agents w LEFT JOIN students s ON s.id=w.student_id WHERE s.id IS NULL"),
        "wanderer_account_not_ai": count("SELECT COUNT(*) FROM wanderer_agents w JOIN students s ON s.id=w.student_id WHERE s.control_mode<>'ai'"),
        "ai_accounts_with_human_password": count("SELECT COUNT(*) FROM students WHERE control_mode='ai' AND COALESCE(hashed_password,'')<>''"),
        "ai_human_sessions": count("SELECT COUNT(*) FROM user_sessions x JOIN students s ON x.user_id=CAST(s.id AS TEXT) WHERE x.role='student' AND s.control_mode='ai'"),
        "ai_active_remember_tokens": count("SELECT COUNT(*) FROM remember_login_tokens x JOIN students s ON x.user_id=CAST(s.id AS TEXT) WHERE x.role='student' AND s.control_mode='ai' AND x.revoked_at=''", optional_table="remember_login_tokens"),
        "ai_active_mp_sessions": count("SELECT COUNT(*) FROM mp_sessions x JOIN students s ON x.user_pk=s.id WHERE x.user_role='student' AND s.control_mode='ai' AND x.revoked=0", optional_table="mp_sessions"),
        "ai_active_wechat_bindings": count("SELECT COUNT(*) FROM wechat_bindings x JOIN students s ON x.user_pk=s.id WHERE x.user_role='student' AND s.control_mode='ai' AND x.status='active'", optional_table="wechat_bindings"),
        "ai_active_delegations": count("SELECT COUNT(*) FROM agent_task_delegations x JOIN students s ON x.actor_id=s.id WHERE x.actor_role='student' AND s.control_mode='ai' AND x.status='active'", optional_table="agent_task_delegations"),
        "ai_active_persistent_authorizations": count("SELECT COUNT(*) FROM agent_persistent_authorizations x JOIN students s ON x.actor_id=s.id WHERE x.actor_role='student' AND s.control_mode='ai' AND x.status='active'", optional_table="agent_persistent_authorizations"),
        "commands_missing_turn": count("SELECT COUNT(*) FROM wanderer_commands c LEFT JOIN wanderer_turns t ON t.turn_id=c.turn_id WHERE t.turn_id IS NULL"),
        "turns_missing_event": count("SELECT COUNT(*) FROM wanderer_turns t LEFT JOIN wanderer_wake_events e ON e.event_id=t.event_id WHERE e.event_id IS NULL"),
        "negative_resource_usage": count("SELECT COUNT(*) FROM wanderer_resource_usage WHERE used_units<0 OR reserved_units<0"),
    }
    totals = {
        "wanderers": count("SELECT COUNT(*) FROM wanderer_agents"),
        "ai_accounts": count("SELECT COUNT(*) FROM students WHERE control_mode='ai'"),
        "human_accounts": count("SELECT COUNT(*) FROM students WHERE control_mode='human'"),
        "migrated_ai_accounts": count("SELECT COUNT(*) FROM account_control_migrations"),
        "active_groups_owned_by_ai": count(f"SELECT COUNT(*) FROM social_groups WHERE owner_identity IN ({AI}) AND dissolved_at IS NULL"),
        "active_ai_group_memberships": count(f"SELECT COUNT(*) FROM social_group_members WHERE member_identity IN ({AI}) AND left_at IS NULL"),
        "active_ai_group_memberships_without_observation": count("SELECT COUNT(*) FROM social_group_members m JOIN wanderer_agents w ON m.member_identity='student:' || CAST(w.student_id AS TEXT) JOIN social_groups g ON g.id=m.group_id LEFT JOIN wanderer_group_observations o ON o.wanderer_id=w.id AND o.group_id=m.group_id WHERE m.left_at IS NULL AND g.dissolved_at IS NULL AND o.wanderer_id IS NULL"),
        "active_ai_owned_groups_without_text": count(f"SELECT COUNT(*) FROM social_groups g WHERE g.owner_identity IN ({AI}) AND g.dissolved_at IS NULL AND NOT EXISTS (SELECT 1 FROM social_messages m WHERE m.conversation_key='group:' || CAST(g.id AS TEXT) AND m.message_type='text')"),
        "group_observation_rows": count("SELECT COUNT(*) FROM wanderer_group_observations"),
        "legacy_delivery_receipts": count("SELECT COUNT(*) FROM wanderer_private_message_operations"),
        "legacy_delivery_receipts_with_deleted_message": count("SELECT COUNT(*) FROM wanderer_private_message_operations o LEFT JOIN private_messages m ON m.id=o.message_id WHERE m.id IS NULL"),
    }
    recent = {}
    for label, table, kind in (("native_dm", "social_messages", "dm"), ("group", "social_messages", "group"), ("legacy_dm", "private_messages", None)):
        kind_clause = " AND m.conversation_type=%s AND m.message_type='text'" if kind else ""
        parameters = (since_local, kind) if kind else (since_local,)
        where = f"m.sender_identity IN ({AI}) AND REPLACE(m.created_at,'T',' ') >= %s" + kind_clause
        rows = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(n),0), "
            "COALESCE(SUM(CASE WHEN n>=3 AND senders>=2 THEN 1 ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN n>=6 AND senders>=2 THEN 1 ELSE 0 END),0) "
            "FROM (SELECT m.conversation_key, COUNT(*) n, COUNT(DISTINCT m.sender_identity) senders "
            f"FROM {table} m WHERE {where} GROUP BY m.conversation_key) q", parameters,
        ).fetchone()
        recent[label] = dict(zip(("conversations", "ai_messages", "conversations_with_3_ai_messages_and_2_ai_senders", "conversations_with_6_ai_messages_and_2_ai_senders"), map(int, rows)))
    recent["model_dispatches"] = count("SELECT COUNT(*) FROM wanderer_turns WHERE dispatched_at IS NOT NULL AND REPLACE(dispatched_at,'T',' ') >= %s", (since_local,))
    recent["succeeded_commands"] = count("SELECT COUNT(*) FROM wanderer_commands WHERE state='succeeded' AND REPLACE(created_at,'T',' ') >= %s", (since_local,))
    settings = {}
    for key, value in conn.execute("SELECT key,value FROM wanderer_runtime_settings WHERE key IN ('enabled','autonomous_v2_enabled')").fetchall():
        settings[key] = value if value in ("0", "1", "true", "false") else "other"
    schema_rows = conn.execute("SELECT value FROM wanderer_world_schema WHERE key='version'").fetchall()
    versions = [str(row[0]) if str(row[0]).isdigit() else "other" for row in schema_rows]
    checks["world_schema_not_v5"] = int(versions != ["5"])
    return {
        "ok": not any(checks.values()), "invariants": checks, "totals": totals,
        "since_local": since_local, "natural_activity_since": recent,
        "activity_interpretation": "counts_only_no_claim_of_alternating_rounds_or_provider_quality",
        "settings": settings, "world_schema_versions": versions,
        "absent_optional_auth_tables": sorted(OPTIONAL_AUTH_TABLES - present_tables),
        "states": {
            "events": states("wanderer_wake_events", ("pending", "processing", "deferred", "completed", "failed")),
            "turns": states("wanderer_turns", ("running", "executing", "waiting", "completed", "failed", "capped", "uncertain", "abandoned", "interrupted")),
            "commands": states("wanderer_commands", ("pending", "executing", "waiting", "succeeded", "failed", "uncertain", "resolved")),
            "legacy_inbox": states("wanderer_social_inbox", ("pending", "running", "retry", "done", "failed", "skipped")),
            "reservations": states("wanderer_resource_reservations", ("reserved", "consumed", "released")),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="Release timestamp with explicit UTC offset, for example 2026-11-10T09:00:00+08:00")
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since)
    if since.tzinfo is None:
        parser.error("--since requires an explicit UTC offset")
    local = since.astimezone(ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai")).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn.startswith(("postgresql://", "postgres://")):
        parser.error("DATABASE_URL must already be configured for PostgreSQL; no dotenv or fallback is used")
    import psycopg
    try:
        # Read-only is forced at connection startup, before the first query.
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10,
                             options="-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=1000") as conn:
            conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
                    raise RuntimeError("Read-only transaction was not established")
                present = {row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchall()}
                missing = sorted(REQUIRED_TABLES - present)
                if missing:
                    report = {"ok": False, "missing_tables": missing}
                else:
                    report = collect_snapshot(conn, since_local=local, present_tables=present)
                report.update({"read_only": True, "generated_at": datetime.now(timezone.utc).isoformat(), "body_or_secret_fields_selected": False})
            finally:
                conn.execute("ROLLBACK")
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0 if report["ok"] else 1
    except Exception as error:
        # Driver messages may contain connection details: print only type/code.
        print(json.dumps({"ok": False, "read_only": True, "error_type": type(error).__name__, "sqlstate": getattr(error, "sqlstate", None)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
