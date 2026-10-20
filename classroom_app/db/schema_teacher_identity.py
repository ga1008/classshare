"""Additive employee-identity migration. No guessed IDs or credential copying."""
from __future__ import annotations


def ensure_teacher_identity_schema(conn, *, engine: str = "sqlite") -> None:
    for table in ("teachers", "teacher_organization_memberships"):
        if engine == "postgres":
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS employee_number TEXT NOT NULL DEFAULT ''")
        else:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "employee_number" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN employee_number TEXT NOT NULL DEFAULT ''")
    # Empty legacy accounts stay usable by email. Occupied identities remain
    # reserved even when a membership is inactive, protecting historic links.
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_teacher_membership_school_employee
           ON teacher_organization_memberships (school_code, employee_number)
           WHERE employee_number <> ''"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_teacher_membership_employee_login
           ON teacher_organization_memberships (employee_number, is_active, teacher_id)
           WHERE employee_number <> ''"""
    )
