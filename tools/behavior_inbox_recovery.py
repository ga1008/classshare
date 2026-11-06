"""Server-admin receipt inspection; writes require an explicit scope/hash/reason.

Uses the server's configured connection only when explicitly invoked. Never run
this tool against a business database as part of tests or capacity rehearsals.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="list", choices=("list", "inspect", "retry", "discard"))
    parser.add_argument("--batch-key")
    parser.add_argument("--payload-hash")
    parser.add_argument("--class-offering-id", type=int)
    parser.add_argument("--user-role", choices=("student", "teacher"))
    parser.add_argument("--user-pk", type=int)
    parser.add_argument("--operator")
    parser.add_argument("--reason")
    parser.add_argument("--acknowledge-write", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "inspect" and not args.batch_key:
        parser.error("inspect requires --batch-key")
    if args.action in {"retry", "discard"} and not all((args.acknowledge_write, args.batch_key, args.payload_hash,
            args.class_offering_id, args.user_role, args.user_pk, args.operator, args.reason)):
        parser.error("writes require --acknowledge-write, batch key, hash, classroom, role, user ID, operator and reason")
    return args


def run(args, connect):
    with connect() as conn:
        if args.action == "list":
            conditions, values = ["state='quarantined'"], []
            for column, value in (("batch_key", args.batch_key), ("class_offering_id", args.class_offering_id),
                                  ("user_role", args.user_role), ("user_pk", args.user_pk)):
                if value is not None:
                    conditions.append(f"{column}=?")
                    values.append(value)
            return [dict(row) for row in conn.execute("""SELECT batch_key,payload_hash,class_offering_id,user_role,user_pk,
                state,attempts,received_at,last_error FROM behavior_write_inbox WHERE """ + " AND ".join(conditions) +
                " ORDER BY received_at,batch_key LIMIT 100", tuple(values)).fetchall()]
        if args.action == "inspect":
            row = conn.execute("SELECT * FROM behavior_write_inbox WHERE batch_key=?", (args.batch_key,)).fetchone()
            return dict(row) if row is not None else None
        from classroom_app.services.behavior_inbox_recovery_service import recover_behavior_receipt
        result = recover_behavior_receipt(conn, action=args.action, batch_key=args.batch_key,
            payload_hash=args.payload_hash, class_offering_id=args.class_offering_id,
            user_role=args.user_role, user_pk=args.user_pk, operator=args.operator, reason=args.reason)
        conn.commit()
        return result


def main(argv=None):
    args = arguments(argv)
    from classroom_app.database import get_db_connection
    print(json.dumps(run(args, get_db_connection), ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
