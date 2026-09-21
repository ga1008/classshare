"""Add growth-page-family (S4 package E) scenarios to an already asserted
synthetic fixture, after `tests/e2e/scripts/prepare_p03_runtime.py --synthetic`
and `tools/ui/prepare_lq_s3.py` have run. Per the S4 runbook (`.codex-temp/
claude-s4-runbook.md` §4), this script must not modify either of those two
files; it only adds rows to tables owned by the growth pages this package is
responsible for (`student_point_ledger`, `student_achievements`).

Usage: python tools/ui/prepare_lq_growth.py <runtime-root>

The synthetic student otherwise already exercises the zero/empty states this
package's iron-rule-6 exception cares about (fresh fixture => zero points
balance, no achievements earned, empty wrong book, empty feedback review) —
so this script's job is the *opposite* one: giving the same student a
non-zero balance, one earned + one locked achievement, and a couple of
point-ledger rows, so screenshots and assertions can show both states without
a second fixture. It does not touch wrong_book or feedback_review rows
because `tools/ui/prepare_lq_s3.py` already seeds a wrong submission
(`s3.wrongAssignmentId`/`s3.wrongSubmissionId`) that exercises those pages.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def insert(conn: sqlite3.Connection, table: str, values: dict) -> int:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if not set(values).issubset(columns):
        raise ValueError(f"Unexpected {table} seed columns: {set(values) - columns}")
    names = ",".join(values)
    placeholders = ",".join("?" for _ in values)
    cursor = conn.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", tuple(values.values()))
    return cursor.lastrowid


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS student_point_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            reason_kind TEXT NOT NULL,
            reason_ref TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS student_achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            badge_key TEXT NOT NULL,
            earned_at TEXT NOT NULL,
            UNIQUE (student_id, badge_key)
        )
        """
    )


def prepare(runtime: Path) -> None:
    runtime = runtime.resolve()
    temporary = (ROOT / ".codex-temp").resolve()
    if runtime == temporary or not runtime.is_relative_to(temporary):
        raise ValueError("Growth seed must stay in an owned child of .codex-temp")
    fixture_path = runtime / "fixture.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    database = runtime / "db/classroom.db"
    if fixture.get("uiV3Synthetic") is not True or Path(fixture["databasePath"]).resolve() != database:
        raise ValueError("Growth seed requires the exact synthetic SQLite identity")
    if fixture.get("lqGrowthSynthetic"):
        raise ValueError("Growth scenarios already exist; use a fresh fixture rather than resetting results")
    student = fixture["student"]["id"]
    now = datetime.now().replace(microsecond=0).isoformat()

    with sqlite3.connect(database) as conn:
        ensure_schema(conn)
        # Non-zero points balance with a mixed earn/spend ledger, so the
        # points shop screenshot shows a real balance and history rather
        # than the fixture's default zero state. The net balance (80 + 60 -
        # 10 = 130) is kept >= the single shop item's cost (100, see
        # classroom_app/services/student_points_service.py SHOP_ITEMS) so
        # e2e can exercise a real, affordable redeem click rather than only
        # the disabled/insufficient-balance state.
        insert(conn, "student_point_ledger", {
            "student_id": student, "delta": 80, "reason_kind": "daily_login",
            "reason_ref": "seed:lq-growth", "note": "S4 growth 种子：登录", "created_at": now,
        })
        insert(conn, "student_point_ledger", {
            "student_id": student, "delta": 60, "reason_kind": "badge_unlock",
            "reason_ref": "seed:lq-growth", "note": "S4 growth 种子：解锁徽章", "created_at": now,
        })
        insert(conn, "student_point_ledger", {
            "student_id": student, "delta": -10, "reason_kind": "shop_redeem",
            "reason_ref": "seed:lq-growth", "note": "S4 growth 种子：兑换", "created_at": now,
        })
        # One earned achievement so the achievements page shows both a lit
        # badge (with the one-shot reveal path exercised via `newly_awarded`
        # on first load after this seed) and the remaining locked badges.
        conn.execute(
            "INSERT OR IGNORE INTO student_achievements (student_id, badge_key, earned_at) VALUES (?, ?, ?)",
            (student, "first_submission", now),
        )
        conn.commit()

    fixture["lqGrowthSynthetic"] = True
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=True, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime_root", type=Path)
    args = parser.parse_args()
    prepare(args.runtime_root)
    print(f"Growth scenarios added to {args.runtime_root}")


if __name__ == "__main__":
    main()
