"""Inspect the current database, or explicitly apply the additive minigame schema."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from classroom_app.db.connection import get_db_connection, get_configured_db_engine
from classroom_app.services.minigame_platform import ensure_all_schema, schema_installed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create the game tables in one transaction")
    args = parser.parse_args(argv)
    if args.apply:
        with get_db_connection() as conn:
            ensure_all_schema(conn)
            conn.commit()
    missing = []
    ready = schema_installed(missing=missing)
    status = "ready" if ready else "migration required (" + ", ".join(missing) + ")"
    print(f"{get_configured_db_engine()} minigames: {status}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
