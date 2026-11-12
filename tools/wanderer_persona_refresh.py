"""Safely refresh untouched seed personas to the current natural-voice version.

The strict hash whitelist in ``upgrade_seed_personas`` silently skips older
legacy renderings, so wanderers keep outdated catchphrases (e.g. a fixed
"家人们！" opening). This tool refreshes those rows without touching anything
that was human-edited.

Modes:
  * default (no flag): read-only dry run, prints the planned changes.
  * --apply: writes a full backup (including every old prompt) first, then applies.
  * --restore <backup>: text-level rollback from a backup written by --apply.

Only rows with ``origin = 'seed'`` and an empty ``persona_updated_at`` are ever
written. The tool works on both SQLite and PostgreSQL; the active engine is
reported so the operator always knows what they are touching.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

BACKUP_CONTRACT = "wanderer-persona-refresh-backup-v1"
DEFAULT_BACKUP_DIR = REPO / "data" / "backups" / "persona_refresh"


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _plan(conn) -> list[dict]:
    from classroom_app.services.wanderer_persona_seeds import plan_seed_persona_refresh
    return plan_seed_persona_refresh(conn)


def _write_backup(plan: list[dict], engine: str, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = backup_dir / f"wanderer_persona_refresh_{stamp}.json"
    document = {
        "contract": BACKUP_CONTRACT,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "engine": engine,
        "count": len(plan),
        "items": plan,
    }
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
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
        backup_path = _write_backup(plan, engine, backup_dir)
        refreshed = apply_seed_persona_refresh(conn, plan)
        conn.commit()
        _print({"mode": "apply", "engine": engine, "refreshed": refreshed,
                "backup": str(backup_path),
                "rollback": f"python tools/wanderer_persona_refresh.py --restore \"{backup_path}\" --apply"})
        return 0


def _run_restore(backup: Path, apply: bool) -> int:
    from classroom_app.database import get_db_connection
    from classroom_app.db.connection import get_configured_db_engine

    document = json.loads(backup.read_text(encoding="utf-8"))
    if document.get("contract") != BACKUP_CONTRACT:
        raise ValueError(f"{backup} is not a {BACKUP_CONTRACT} backup")
    engine = get_configured_db_engine()
    with get_db_connection() as conn:
        if not apply and engine == "postgres":
            conn.execute("SET TRANSACTION READ ONLY")
        restored = []
        missing = []
        for item in document.get("items", []):
            row = conn.execute("SELECT id FROM wanderer_agents WHERE id = ?", (item["id"],)).fetchone()
            if row is None:
                missing.append(item["slug"])
                continue
            if apply:
                conn.execute("UPDATE wanderer_agents SET persona_prompt = ? WHERE id = ?",
                             (item["old_prompt"], item["id"]))
            restored.append(item["slug"])
        if apply:
            conn.commit()
        else:
            conn.rollback()
        _print({"mode": "restore-apply" if apply else "restore-dry-run", "engine": engine,
                "would_restore": restored, "missing_rows": missing,
                "note": "Text-level rollback of persona_prompt; review version/hash fields afterwards."})
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually write; default is a read-only dry run.")
    parser.add_argument("--restore", type=Path, help="Roll back persona_prompt from the given backup file.")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR,
                        help="Directory for backup files (refresh mode only).")
    args = parser.parse_args()
    if args.restore:
        return _run_restore(args.restore, args.apply)
    return _run_refresh(args.apply, args.backup_dir)


if __name__ == "__main__":
    raise SystemExit(main())
