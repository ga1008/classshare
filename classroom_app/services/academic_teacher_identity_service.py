"""Resolve timetable identities from current school-scoped employee numbers.

Links are a read-time projection, never a second identity registry. Changing an
employee number, disabling an account, or revoking a school membership therefore
invalidates its old links immediately, including previously cached timetables.
"""
from __future__ import annotations

from .teacher_identity_service import normalize_employee_number


def resolve_teacher_account_links(conn, school_code, teacher_nos):
    """Return exact, unambiguous platform-account links for one school.

    A populated membership employee number takes precedence over a previously
    verified academic login. Legacy credentials remain useful only until that
    school identity has been supplied. Neither a name nor a different school's
    employee number is evidence of a match. This does not grant permissions.
    """
    result = {
        no: {"linked_teacher_id": None, "account_status": "unlinked", "account_link_source": ""}
        for no in teacher_nos
    }
    canonical = {}
    for no in result:
        try:
            value = normalize_employee_number(no)
        except ValueError:
            continue
        if value:
            canonical.setdefault(value, []).append(no)
    if not canonical:
        return result

    school = str(school_code or "").strip().casefold()
    numbers = tuple(canonical)
    marks = ",".join("?" for _ in numbers)
    # Three bounded queries replace per-account membership reads. Existing
    # membership records are authoritative even if every record is inactive.
    membership_rows = conn.execute(
        f"""SELECT m.employee_number,m.teacher_id
            FROM teacher_organization_memberships m
            JOIN teachers t ON t.id=m.teacher_id
            WHERE m.school_code=? AND COALESCE(m.is_active,1)=1
              AND COALESCE(t.is_active,1)=1 AND m.employee_number IN ({marks})""",
        (school, *numbers),
    ).fetchall()
    legacy_rows = conn.execute(
        f"""SELECT t.employee_number,t.id AS teacher_id
            FROM teachers t WHERE t.school_code=? AND COALESCE(t.is_active,1)=1
              AND t.employee_number IN ({marks})
              AND NOT EXISTS (
                  SELECT 1 FROM teacher_organization_memberships any_membership
                  WHERE any_membership.teacher_id=t.id
              )""",
        (school, *numbers),
    ).fetchall()
    credential_rows = conn.execute(
        f"""SELECT UPPER(TRIM(c.username)) AS employee_number,c.teacher_id
            FROM teacher_academic_system_credentials c
            JOIN teachers t ON t.id=c.teacher_id
            LEFT JOIN teacher_organization_memberships m
              ON m.teacher_id=t.id AND m.school_code=c.school_code
              AND COALESCE(m.is_active,1)=1
            WHERE c.school_code=? AND c.enabled=1 AND c.last_status='verified'
              AND COALESCE(c.last_verified_at,'')<>'' AND COALESCE(t.is_active,1)=1
              AND UPPER(TRIM(c.username)) IN ({marks})
              AND (
                (m.id IS NOT NULL AND COALESCE(m.employee_number,'')='')
                OR (
                    t.school_code=c.school_code AND COALESCE(t.employee_number,'')=''
                    AND NOT EXISTS (
                        SELECT 1 FROM teacher_organization_memberships any_membership
                        WHERE any_membership.teacher_id=t.id
                    )
                )
              )""",
        (school, *numbers),
    ).fetchall()

    matches = {}
    for rows, source in (
        (membership_rows, "employee_number"),
        (legacy_rows, "employee_number"),
        (credential_rows, "verified_academic_account"),
    ):
        for row in rows:
            candidates = matches.setdefault(row["employee_number"], {})
            candidates[int(row["teacher_id"])] = source
    for number, candidates in matches.items():
        if number not in canonical:
            continue
        unique = len(candidates) == 1
        teacher_id = next(iter(candidates)) if unique else None
        link = {
            "linked_teacher_id": teacher_id,
            "account_status": "linked" if unique else "ambiguous",
            "account_link_source": candidates[teacher_id] if unique else "",
        }
        for no in canonical[number]:
            result[no] = dict(link)
    return result
