"""Resolve timetable identities from current school-scoped employee numbers.

Links are a read-time projection, never a second identity registry. Changing an
employee number, disabling an account, or revoking a school membership therefore
invalidates its old links immediately, including previously cached timetables.
"""
from __future__ import annotations

from collections import defaultdict
from urllib.parse import quote, urlencode

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


def _chunks(values, size=300):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _account_employee_identities(conn, teacher_ids):
    """Read the same authoritative identities as the forward resolver in bulk."""
    identities = defaultdict(dict)
    for batch in _chunks(teacher_ids):
        marks = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""WITH targets AS (
                SELECT id,school_code,school_name,employee_number FROM teachers
                WHERE id IN ({marks}) AND COALESCE(is_active,1)=1
            )
            SELECT m.teacher_id,m.school_code,m.school_name,m.employee_number,
                   'employee_number' AS identity_source
            FROM teacher_organization_memberships m JOIN targets t ON t.id=m.teacher_id
            WHERE COALESCE(m.is_active,1)=1 AND COALESCE(m.employee_number,'')<>''
            UNION ALL
            SELECT t.id,t.school_code,t.school_name,t.employee_number,'employee_number'
            FROM targets t WHERE COALESCE(t.employee_number,'')<>''
              AND NOT EXISTS (SELECT 1 FROM teacher_organization_memberships a WHERE a.teacher_id=t.id)
            UNION ALL
            SELECT t.id,c.school_code,COALESCE(m.school_name,t.school_name),
                   c.username,'verified_academic_account'
            FROM teacher_academic_system_credentials c JOIN targets t ON t.id=c.teacher_id
            LEFT JOIN teacher_organization_memberships m ON m.teacher_id=t.id
              AND m.school_code=c.school_code AND COALESCE(m.is_active,1)=1
            WHERE c.enabled=1 AND c.last_status='verified' AND COALESCE(c.last_verified_at,'')<>''
              AND (
                (m.id IS NOT NULL AND COALESCE(m.employee_number,'')='')
                OR (t.school_code=c.school_code AND COALESCE(t.employee_number,'')=''
                  AND NOT EXISTS (SELECT 1 FROM teacher_organization_memberships a WHERE a.teacher_id=t.id))
              )""",
            batch,
        ).fetchall()
        for row in rows:
            try:
                number = normalize_employee_number(row["employee_number"])
            except ValueError:
                continue
            school = str(row["school_code"] or "").strip().casefold()
            if number and school:
                identities[int(row["teacher_id"])][(school, number)] = {
                    "school_code": school, "school_name": str(row["school_name"] or ""),
                    "employee_number": number, "account_link_source": row["identity_source"],
                }
    return identities


def load_teacher_timetable_link_summaries(conn, teacher_ids, *, actor_teacher_id=None):
    """Reverse-link accounts to directory/timetable caches, without remote I/O.

    Callers must first authorize access to the requested platform accounts. This
    helper does not extend that authority to another school's cached profiles:
    profile details and navigation links require the actor's active school scope.
    Every timetable URL retains the existing academic domain and school checks.
    """
    from .organization_scope_service import load_teacher_org_memberships
    from .semester_identity_service import parse_semester_identity

    ids = list(dict.fromkeys(int(value) for value in teacher_ids))
    result = {
        teacher_id: {"status": "not_found", "linked_count": 0, "ambiguous_count": 0, "items": []}
        for teacher_id in ids
    }
    if not ids:
        return result
    active_ids = []
    for batch in _chunks(ids):
        rows = conn.execute(
            f"SELECT id,is_active,cancelled_at FROM teachers WHERE id IN ({','.join('?' for _ in batch)})",
            batch,
        ).fetchall()
        for row in rows:
            teacher_id = int(row["id"])
            status = "cancelled" if row["cancelled_at"] else "inactive" if row["is_active"] == 0 else "unlinked"
            result[teacher_id]["status"] = status
            if status == "unlinked":
                active_ids.append(teacher_id)
    if not active_ids:
        return result

    readable_schools = set()
    if actor_teacher_id is not None:
        actor = conn.execute(
            "SELECT is_active,cancelled_at FROM teachers WHERE id=?", (int(actor_teacher_id),),
        ).fetchone()
        if actor and actor["is_active"] != 0 and not actor["cancelled_at"]:
            readable_schools = {
                str(m["school_code"]).casefold()
                for m in load_teacher_org_memberships(conn, int(actor_teacher_id))
            }
    identities = _account_employee_identities(conn, active_ids)
    requested = {key for by_identity in identities.values() for key in by_identity}
    if not requested:
        return result

    directory = defaultdict(list)
    # Read only compact directory columns once per represented school, never
    # profile_json or lesson snapshots. Normalizing in Python preserves NFKC
    # equality without PostgreSQL/SQLite-specific Unicode SQL or per-user reads.
    for schools in _chunks(sorted({school for school, _ in requested})):
        rows = conn.execute(
            f"""SELECT school_code,teacher_no,name,college_name,synced_at
                FROM academic_teacher_directory
                WHERE school_code IN ({','.join('?' for _ in schools)})""",
            schools,
        ).fetchall()
        for row in rows:
            try:
                number = normalize_employee_number(row["teacher_no"])
            except ValueError:
                continue
            key = (row["school_code"], number)
            if key in requested:
                directory[key].append(dict(row))

    forward, terms = {}, defaultdict(list)
    by_school = defaultdict(set)
    for (school, _), entries in directory.items():
        for entry in entries:
            by_school[school].add(entry["teacher_no"])
    for school, numbers in by_school.items():
        for batch in _chunks(sorted(numbers)):
            for number, link in resolve_teacher_account_links(conn, school, batch).items():
                forward[(school, number)] = link
            rows = conn.execute(
                f"""SELECT teacher_no,term_code,synced_at FROM academic_teacher_timetables
                    WHERE school_code=? AND teacher_no IN ({','.join('?' for _ in batch)})""",
                (school, *batch),
            ).fetchall()
            for row in rows:
                identity = parse_semester_identity(row["term_code"])
                if identity and identity.code == row["term_code"]:
                    year, term = identity.as_year_term()
                    terms[(school, row["teacher_no"])].append({
                        "year": year, "term": term, "label": identity.canonical_name,
                        "synced_at": row["synced_at"],
                    })

    for teacher_id, by_identity in identities.items():
        summary = result[teacher_id]
        for key, identity in sorted(by_identity.items()):
            school, number = key
            item = {
                **identity, "teacher_no": "", "account_status": "unlinked", "name": "",
                "college_name": "", "synced_at": "", "has_timetable": False,
                "can_view": False, "can_view_school": school in readable_schools,
                "query_supported": school == "gxufl",
                "year": "", "term": "", "detail_url": "", "query_url": "",
                "cached_term_count": 0, "terms": [],
            }
            entries = directory.get(key, [])
            if entries:
                entry = entries[0]
                link = forward[(school, entry["teacher_no"])]
                # Duplicate canonical directory identities or conflicting
                # platform accounts must never silently select a teacher.
                if len(entries) != 1 or link["account_status"] == "ambiguous":
                    item["account_status"] = "ambiguous"
                elif link["linked_teacher_id"] == teacher_id:
                    item["account_status"] = "linked"
                    item["teacher_no"] = entry["teacher_no"]
                    if school in readable_schools:
                        cached_terms = sorted(
                            terms[(school, entry["teacher_no"])],
                            key=lambda term: (term["year"], term["term"]), reverse=True,
                        )
                        item.update({
                            "name": entry["name"], "college_name": entry["college_name"],
                            "synced_at": entry["synced_at"], "terms": cached_terms,
                            "cached_term_count": len(cached_terms), "has_timetable": bool(cached_terms),
                        })
                        if cached_terms and school == "gxufl":
                            latest = cached_terms[0]
                            params = {"year": latest["year"], "term": latest["term"]}
                            item.update({
                                **params, "can_view": True,
                                "detail_url": (
                                    f"/api/manage/academic/teachers/{quote(entry['teacher_no'], safe='')}"
                                    f"/timetable?{urlencode(params)}"
                                ),
                                "query_url": "/manage/academic/teachers/schedule?" + urlencode({
                                    "teacher_no": entry["teacher_no"], **params, "local_only": "1",
                                }),
                            })
            summary["items"].append(item)
        summary["linked_count"] = sum(item["account_status"] == "linked" for item in summary["items"])
        summary["ambiguous_count"] = sum(item["account_status"] == "ambiguous" for item in summary["items"])
        summary["status"] = (
            "ambiguous" if summary["ambiguous_count"] else "linked" if summary["linked_count"] else "unlinked"
        )
    return result
