"""Pure material-library visibility SQL from already authorized organization facts.

The router retains DB/role resolution; callers reuse its unchanged facade.
"""
from __future__ import annotations


def build_material_visibility_predicate(teacher_id: int, memberships, raw_membership_rows) -> tuple[str, list[object]]:
    school_codes = sorted(
        {
            str(scope.get("school_code") or "").strip().lower()
            for scope in memberships
            if str(scope.get("school_code") or "").strip()
        }
        | {
            str(row["school_code"] or "").strip().lower()
            for row in raw_membership_rows
            if str(row["school_code"] or "").strip()
        }
    )
    department_pairs = sorted(
        {
            (
                str(scope.get("school_code") or "").strip().lower(),
                str(scope.get("department") or "").strip().lower(),
            )
            for scope in memberships
            if str(scope.get("school_code") or "").strip()
            and str(scope.get("department") or "").strip()
        }
        | {
            (
                str(row["school_code"] or "").strip().lower(),
                str(row["department"] or "").strip().lower(),
            )
            for row in raw_membership_rows
            if str(row["school_code"] or "").strip()
            and str(row["department"] or "").strip()
        }
    )
    college_pairs = sorted(
        {
            (
                str(scope.get("school_code") or "").strip().lower(),
                str(scope.get("college") or "").strip().lower(),
            )
            for scope in memberships
            if str(scope.get("school_code") or "").strip()
            and str(scope.get("college") or "").strip()
        }
        | {
            (
                str(row["school_code"] or "").strip().lower(),
                str(row["college"] or "").strip().lower(),
            )
            for row in raw_membership_rows
            if str(row["school_code"] or "").strip()
            and str(row["college"] or "").strip()
        }
    )

    conditions = ["m.teacher_id = ?", "m.scope_level = 'public'"]
    params: list[object] = [int(teacher_id)]

    if school_codes:
        placeholders = ", ".join("?" for _ in school_codes)
        conditions.append(
            f"""
            (
                m.scope_level = 'school'
                AND lower(TRIM(COALESCE(m.school_code, ''))) IN ({placeholders})
            )
            """
        )
        params.extend(school_codes)

    if department_pairs:
        pair_conditions = []
        for school_code, department in department_pairs:
            pair_conditions.append(
                """
                (
                    lower(TRIM(COALESCE(m.school_code, ''))) = ?
                    AND lower(TRIM(COALESCE(m.department, ''))) = ?
                )
                """
            )
            params.extend([school_code, department])
        conditions.append(
            """
            (
                m.scope_level = 'department'
                AND (
            """
            + " OR ".join(pair_conditions)
            + """
                )
            )
            """
        )

    if college_pairs:
        pair_conditions = []
        for school_code, college in college_pairs:
            pair_conditions.append(
                """
                (
                    lower(TRIM(COALESCE(m.school_code, ''))) = ?
                    AND lower(TRIM(COALESCE(m.college, ''))) = ?
                )
                """
            )
            params.extend([school_code, college])
        conditions.append(
            """
            (
                m.scope_level = 'college'
                AND (
            """
            + " OR ".join(pair_conditions)
            + """
                )
            )
            """
        )

    return "(" + " OR ".join(f"({condition})" for condition in conditions) + ")", params
