"""Inspect or replay a sealed register against an explicitly named, offline restore copy."""

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="action", required=True)
    seal = subs.add_parser("seal")
    restore = subs.add_parser("reconcile")
    for p in (seal, restore):
        p.add_argument("--register", required=True, type=Path)
    restore.add_argument("--expected-digest", required=True)
    restore.add_argument("--restore-data-root", required=True, type=Path)
    engines = restore.add_mutually_exclusive_group(required=True)
    engines.add_argument("--sqlite-copy", type=Path)
    engines.add_argument(
        "--postgres-copy",
        metavar="DATABASE_NAME",
        help="Connection URL comes only from MINIGAMES_RESTORE_DATABASE_URL",
    )
    restore.add_argument(
        "--apply", action="store_true", help="Erase replayed games in this quiesced restore copy"
    )
    restore.add_argument(
        "--confirm-offline", action="store_true", help="The restore copy has no application or asset traffic"
    )
    args = parser.parse_args()
    # The standalone tool must never silently use a live checkout's .env.
    import dotenv

    dotenv.load_dotenv = lambda *a, **k: False
    if args.action == "seal":
        from classroom_app.services.minigame_package_recovery import register_snapshot

        digest, rows = register_snapshot(args.register)
        print(json.dumps({"count": len(rows), "digest": digest}))
        return
    target = args.restore_data_root.resolve(strict=True)
    forbidden = {ROOT / "data", Path("/lanshare/data").resolve(), Path("/app/data").resolve()}
    for var in ("LANSHARE_DATA_ROOT", "MAIN_DATA_DIR"):
        if os.getenv(var):
            forbidden.add(Path(os.environ[var]).resolve())
    if any(target.is_relative_to(directory) or directory.is_relative_to(target) for directory in forbidden):
        parser.error("Choose a dedicated offline copy outside the active data directory.")
    if args.apply and not args.confirm_offline:
        parser.error("Applying requires a quiesced restore copy and --confirm-offline.")
    if not (target / "games").is_dir():
        parser.error("Restore the matching games directory before reconciliation.")
    os.environ["LANSHARE_DATA_ROOT"] = str(target)
    if args.sqlite_copy:
        database = args.sqlite_copy.resolve(strict=True)
        if not database.is_file() or not database.is_relative_to(target):
            parser.error("The existing SQLite copy must be inside the restore data root.")
        os.environ.update(MAIN_DB_PATH=str(database), DB_ENGINE="sqlite", DATABASE_URL="")
    else:
        uri = os.environ.get("MINIGAMES_RESTORE_DATABASE_URL", "")
        parsed = urlsplit(uri)
        if (
            parsed.scheme not in ("postgres", "postgresql")
            or unquote(parsed.path.lstrip("/")) != args.postgres_copy
            or not parsed.hostname
            or os.environ.get("DATABASE_URL") == uri
        ):
            parser.error("Provide the distinct restore DSN and its matching database name.")
        os.environ.update(DB_ENGINE="postgres", DATABASE_URL=uri, POSTGRES_BACKEND_READY="true")
    from classroom_app.services import minigame_package_recovery as recovery

    if args.postgres_copy:
        with recovery.platform.game_db() as conn:
            name = conn.execute("SELECT current_database()").fetchone()[0]
            busy = conn.execute("""SELECT COUNT(*) FROM pg_stat_activity WHERE datname=current_database()
                AND pid<>pg_backend_pid() AND backend_type='client backend'""").fetchone()[0]
            if name != args.postgres_copy or busy:
                parser.error(
                    "Verify the restore database is offline and has no other application connections."
                )
    result = recovery.reconcile(args.register, args.expected_digest, apply=args.apply)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
