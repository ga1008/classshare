"""Report or apply the blog closed-loop backfill on the configured database.

Default is a dry run: counts what ``backfill_blog_closed_loop_data`` would
change and writes nothing. ``--apply`` ensures the tables and applies it (the
application also does this idempotently at startup).

Use only against an isolated or rehearsal database you are allowed to modify:

    DB_ENGINE=sqlite python tools/db/migrate_blog_closed_loop.py
    python tools/db/migrate_blog_closed_loop.py --apply --json-output report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from classroom_app.database import get_db_connection  # noqa: E402
from classroom_app.db.connection import get_configured_db_engine  # noqa: E402
from classroom_app.db.schema_blog_closed_loop import (  # noqa: E402
    backfill_blog_closed_loop_data,
    ensure_blog_closed_loop_schema,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the backfill (default: dry run)")
    parser.add_argument("--json-output", type=Path, help="also write the report to this new file")
    args = parser.parse_args()
    if args.json_output and args.json_output.exists():
        parser.error("--json-output must be a new path")

    remaining = None
    conn = get_db_connection()
    try:
        if args.apply:
            ensure_blog_closed_loop_schema(conn)
        report = backfill_blog_closed_loop_data(conn, apply=args.apply, now=datetime.now().isoformat())
        if args.apply:
            conn.commit()
            # A second pass must find nothing left: proves the apply converged.
            remaining = backfill_blog_closed_loop_data(conn, apply=False)
        conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    result = {
        "engine": get_configured_db_engine(),
        "mode": "apply" if args.apply else "dry-run",
        "changes": report,
        "remaining_after_apply": remaining,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.json_output:
        args.json_output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
