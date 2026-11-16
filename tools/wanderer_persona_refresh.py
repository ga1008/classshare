"""Safely refresh untouched seed personas to the current natural-voice version.

The strict hash whitelist in ``upgrade_seed_personas`` silently skips older
legacy renderings, so wanderers keep outdated catchphrases (e.g. a fixed
"家人们！" opening). This tool refreshes those rows without touching anything
that was human-edited.

Modes:
  * default (no flag): read-only dry run, prints the planned changes.
  * --apply: writes a full backup (including every old prompt) first, then applies.
  * --restore <backup>: restore full persona metadata from a v2 backup.

Only rows with ``origin = 'seed'`` and an empty ``persona_updated_at`` are ever
written. Every write compares the complete planned state atomically; later
edits are conflicts. Backups are bound to their database target and row identity.
Other worker processes retain their existing, bounded 60-second identity cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

BACKUP_CONTRACT = "wanderer-persona-refresh-backup-v2"
DEFAULT_BACKUP_DIR = REPO / "data" / "backups" / "persona_refresh"


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _plan(conn) -> list[dict]:
    from classroom_app.services.wanderer_persona_seeds import plan_seed_persona_refresh
    return plan_seed_persona_refresh(conn)


def _database_identity(conn, engine: str) -> dict:
    """Fingerprint the connected target without storing credentials or URLs."""
    if engine == "sqlite":
        rows = conn.execute("PRAGMA database_list").fetchall()
        filename = next((row[2] for row in rows if row[1] == "main"), "")
        if not filename:
            raise ValueError("Persona backups require a persistent SQLite database target")
        target = [str(Path(filename).resolve())]
    else:
        row = conn.execute(
            "SELECT current_database() AS database_name, current_schema() AS schema_name, "
            "'wanderer_agents'::regclass::oid AS relation_oid"
        ).fetchone()
        info = conn.raw_connection.info
        target = [str(info.host), str(info.port), row["database_name"], row["schema_name"], row["relation_oid"]]
    encoded = json.dumps(target, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"engine": engine, "fingerprint": hashlib.sha256(encoded).hexdigest()}


def _write_backup(plan: list[dict], target: dict, backup_dir: Path) -> Path:
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = backup_dir / f"wanderer_persona_refresh_{stamp}.json"
    document = {
        "contract": BACKUP_CONTRACT,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "engine": target["engine"],
        "target": target,
        "count": len(plan),
        "items": plan,
    }
    # Exclusive creation never overwrites a prior recovery point. fsync the full
    # snapshot before any database write; a backup failure leaves the DB alone.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _run_refresh(apply: bool, backup_dir: Path) -> int:
    from classroom_app.database import get_db_connection
    from classroom_app.db.connection import get_configured_db_engine
    from classroom_app.services.wanderer_persona_seeds import apply_seed_persona_refresh

    engine = get_configured_db_engine()
    with get_db_connection() as conn:
        if not apply and engine == "postgres":
            conn.execute("SET TRANSACTION READ ONLY")
        plan = _plan(conn)
        summary = [
            {"slug": item["slug"], "old_hash": item["old_hash"],
             "new_hash": item["persona_source_hash"]}
            for item in plan
        ]
        if not apply:
            conn.rollback()
            _print({"mode": "dry-run", "engine": engine, "changes": len(plan), "plan": summary})
            print("No changes written. Re-run with --apply to back up and refresh.")
            return 0
        if not plan:
            conn.rollback()
            _print({"mode": "apply", "engine": engine, "changes": 0,
                    "message": "All untouched seed personas are already current."})
            return 0
        backup_path = _write_backup(plan, _database_identity(conn, engine), backup_dir)
        refreshed = apply_seed_persona_refresh(conn, plan)
        conn.commit()
        conflicts = [item["slug"] for item in plan if item["slug"] not in refreshed]
        _print({"mode": "apply", "engine": engine, "refreshed": refreshed,
                "conflicts": conflicts,
                "backup": str(backup_path),
                "rollback": f"python tools/wanderer_persona_refresh.py --restore \"{backup_path}\" --apply"})
        return 2 if conflicts else 0


def _run_restore(backup: Path, apply: bool) -> int:
    from classroom_app.database import get_db_connection
    from classroom_app.db.connection import get_configured_db_engine
    from classroom_app.services.wanderer_persona_seeds import restore_seed_persona_refresh

    document = json.loads(backup.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("contract") != BACKUP_CONTRACT:
        raise ValueError(f"{backup} is not a {BACKUP_CONTRACT} backup; legacy text-only backups require manual review")
    items = document.get("items")
    if not isinstance(items, list) or document.get("count") != len(items):
        raise ValueError("Persona backup item count is invalid")
    if len({item.get("id") for item in items}) != len(items):
        raise ValueError("Persona backup contains duplicate row identities")
    engine = get_configured_db_engine()
    with get_db_connection() as conn:
        if not apply and engine == "postgres":
            conn.execute("SET TRANSACTION READ ONLY")
        if document.get("engine") != engine or document.get("target") != _database_identity(conn, engine):
            raise ValueError("Persona backup belongs to a different database target")
        report = restore_seed_persona_refresh(conn, items, apply=apply)
        if apply:
            conn.commit()
        else:
            conn.rollback()
        _print({"mode": "restore-apply" if apply else "restore-dry-run", "engine": engine,
                **{("would_restore" if not apply and key == "restored" else key): value
                   for key, value in report.items()},
                "note": "Full persona metadata; later edits remain untouched. Other workers' caches expire within 60s."})
        return 2 if report["conflicts"] or report["missing_rows"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually write; default is a read-only dry run.")
    parser.add_argument("--restore", type=Path, help="Restore complete persona metadata from a matching v2 backup.")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR,
                        help="Directory for backup files (refresh mode only).")
    args = parser.parse_args()
    if args.restore:
        return _run_restore(args.restore, args.apply)
    return _run_refresh(args.apply, args.backup_dir)


if __name__ == "__main__":
    raise SystemExit(main())
