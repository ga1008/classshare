"""Deterministic, transactional projection of repository entries onto lesson ordinals.

No network or AI work is performed under database locks. The whole published tree
is considered, including unchanged entries, and explicit unbinds are remembered.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from ..db.connection import get_configured_db_engine
from ..db.schema_git_learning_bindings import ensure_git_learning_bindings_schema
from ..db.schema_session_learning_materials import ensure_session_learning_materials_schema
from .materials_service import is_git_internal_material_path, sync_classroom_learning_material_assignments


def table_exists(conn, name):
    if get_configured_db_engine() == "postgres":
        row = conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=?", (name,)).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def _ensure_projection_tables(conn):
    """Compatibility bootstrap only, after course/offering locks are held.

    Repeating CREATE INDEX IF NOT EXISTS on PostgreSQL still acquires a table
    lock. Normal publications must perform no DDL while competing with another
    publication. Serialize the exceptional first bootstrap and recheck inside
    that lock so concurrent first users do not race catalog creation either.
    """
    from . import session_learning_materials_service as materials
    if table_exists(conn, "class_offering_git_learning_bindings") and materials.has_material_bindings_table(conn):
        return
    if get_configured_db_engine() == "postgres":
        conn.execute("SELECT pg_advisory_xact_lock(742839012)")
    if not table_exists(conn, "class_offering_git_learning_bindings"):
        ensure_git_learning_bindings_schema(conn)
    if not materials.has_material_bindings_table(conn):
        ensure_session_learning_materials_schema(conn)


def _number(name):
    patterns = (r"(?:lesson|l)[ _-]*0*(\d{1,3})", r"第\s*0*(\d{1,3})\s*(?:次课|课|讲)", r"0*(\d{1,3})")
    for pattern in patterns:
        match = re.fullmatch(pattern, name, re.IGNORECASE)
        if match:
            return int(match[1])
    return 0


def is_repository_learning_entry(name):
    """Shared filename contract for deterministic and explicitly requested AI paths."""
    name = str(name or "").strip().lower()
    if name in {"readme.md", "index.md", "main.html", "main.htm", "index.html", "index.htm"}:
        return True
    return bool(name.endswith((".html", ".htm", ".md")) and _number(PurePosixPath(name).stem))


def repository_lesson_entries(root, rows):
    """Only unambiguous conventions; conflicting lesson folders are surfaced."""
    prefix = str(root["material_path"]).replace("\\", "/").strip("/") + "/"
    grouped = {}
    unresolved = []
    for raw in rows:
        row = dict(raw)
        path = str(row.get("material_path") or "").replace("\\", "/").strip("/")
        if row.get("node_type") != "file" or is_git_internal_material_path(path) or not path.startswith(prefix):
            continue
        parts = PurePosixPath(path[len(prefix):]).parts
        name = parts[-1].lower()
        stem = PurePosixPath(name).stem
        explicit = _number(stem) if name.endswith((".html", ".htm", ".md")) else 0
        if not is_repository_learning_entry(name):
            continue
        numbered_dirs = [(index, _number(part)) for index, part in enumerate(parts[:-1]) if _number(part)]
        if len({number for _, number in numbered_dirs} | ({explicit} if explicit else set())) > 1:
            unresolved.append({"material_id": row["id"], "reason": "目录和入口序号不一致"})
            continue
        number = numbered_dirs[-1][1] if numbered_dirs else explicit
        if not number and len(parts) != 1:
            unresolved.append({"material_id": row["id"], "reason": "目录没有明确课次序号"})
            continue
        folder = "/".join(parts[:numbered_dirs[-1][0] + 1]) if numbered_dirs else ""
        priority = (0 if explicit and name.endswith((".html", ".htm")) else
                    1 if name in {"main.html", "main.htm"} else
                    2 if name in {"index.html", "index.htm"} else 3)
        grouped.setdefault(number, []).append((priority, folder, path, row))
    entries = {}
    for number, choices in grouped.items():
        choices.sort(key=lambda item: (item[0], item[2]))
        if len({choice[1] for choice in choices}) > 1 or (len(choices) > 1 and choices[0][0] == choices[1][0]):
            unresolved.extend({"material_id": choice[3]["id"], "reason": "同一课次存在多个入口，需手工选择"} for choice in choices)
        else:
            entries[number] = choices[0][3]
    return entries, unresolved


def suppress_git_binding(conn, offering_id, session_id, material_id):
    if table_exists(conn, "class_offering_git_learning_bindings"):
        changed = conn.execute("UPDATE class_offering_git_learning_bindings SET suppressed=1, owns_binding=0 "
                               "WHERE class_offering_id=? AND session_id=? AND material_id=?",
                               (offering_id, session_id, material_id))
        if changed.rowcount:
            return
    # A teacher can explicitly remove an old binding before the first ordinal
    # projection creates provenance. Record that decision only when persisted
    # repository metadata and the whole tree prove this exact canonical entry.
    material = conn.execute("SELECT * FROM course_materials WHERE id=?", (int(material_id),)).fetchone()
    if not material:
        return
    material = dict(material)
    root = conn.execute("SELECT * FROM course_materials WHERE id=?", (material.get("root_id"),)).fetchone()
    if not root:
        return
    root = dict(root)
    if root.get("git_repo_status") != "repository" or root.get("id") != root.get("root_id"):
        return
    owner = conn.execute("SELECT teacher_id FROM class_offerings WHERE id=?", (int(offering_id),)).fetchone()
    if not owner or int(owner["teacher_id"]) != int(root["teacher_id"]):
        return
    if session_id:
        from .offering_session_resequence_service import is_numbered_session
        session = conn.execute("SELECT * FROM class_offering_sessions WHERE id=? AND class_offering_id=?",
                               (int(session_id), int(offering_id))).fetchone()
        if not session or not is_numbered_session(dict(session)):
            return
        number = int(session["order_index"])
    else:
        number = 0
    rows = conn.execute("SELECT id,material_path,node_type FROM course_materials WHERE root_id=?", (root["id"],)).fetchall()
    entries, _ = repository_lesson_entries(root, rows)
    if number not in entries or int(entries[number]["id"]) != int(material_id):
        return
    _ensure_projection_tables(conn)
    conn.execute("""INSERT INTO class_offering_git_learning_bindings
        (class_offering_id,repository_id,lesson_order,material_id,session_id,owns_binding,suppressed)
        VALUES (?,?,?,?,?,0,1) ON CONFLICT(class_offering_id,repository_id,lesson_order) DO NOTHING""",
        (int(offering_id), int(root["id"]), number, int(material_id), int(session_id)))


def sync_repository_learning_bindings(conn, root, rows, teacher_id):
    """Called inside Git publication transaction, with the latest tree locked."""
    from . import session_learning_materials_service as materials
    entries, unresolved = repository_lesson_entries(root, rows)
    # An older primary may be README.md beside today's preferred lesson_N.html.
    # Its explicit directory still proves its ordinal, even when it is no longer
    # the preferred entry. Do not infer an ordinal from arbitrary document names.
    canonical_numbers = {}
    for row in rows:
        individual, _ = repository_lesson_entries(root, [row])
        canonical_numbers.update({int(entry["id"]): number for number, entry in individual.items()})
    root_id = int(root.get("root_id") or root["id"])
    root_path = str(root["material_path"]).strip("/")
    assignments = conn.execute("""
        SELECT DISTINCT a.class_offering_id, m.material_path
        FROM course_material_assignments a JOIN course_materials m ON m.id=a.material_id
        JOIN class_offerings o ON o.id=a.class_offering_id
        WHERE m.root_id=? AND o.teacher_id=?
    """, (root_id, int(teacher_id))).fetchall()
    offering_ids = sorted({int(row["class_offering_id"]) for row in assignments
                           if not is_git_internal_material_path(row["material_path"])
                           and (str(row["material_path"]).strip("/") == root_path
                                or str(row["material_path"]).startswith(root_path + "/")
                                or root_path.startswith(str(row["material_path"]).strip("/") + "/"))})
    result = {"status": "success", "binding_mode": "repository_ordinal", "target_classroom_count": len(offering_ids),
              "total_assignments": 0, "total_home_assignments": 0, "removed_count": 0,
              "assignments": [], "unresolved": unresolved, "warnings": [], "suppressed_count": 0,
              "corrected_legacy_count": 0}
    if not offering_ids:
        result["message"] = "仓库尚未分配课堂，未建立课次绑定。"
        return result
    # Acquire every course before any offering, matching editor/sync lock order
    # even when a repository is assigned to several classrooms.
    from .offering_plan_edit_service import lock_plan_row
    marks = ",".join("?" for _ in offering_ids)
    scoped = conn.execute(f"SELECT id,course_id FROM class_offerings WHERE id IN ({marks})", offering_ids).fetchall()
    for course_id in sorted({int(row["course_id"]) for row in scoped if row["course_id"]}):
        lock_plan_row(conn, "courses", course_id)
    for offering_id in offering_ids:
        materials._ensure_offering_owner(conn, offering_id, teacher_id)
    _ensure_projection_tables(conn)
    for offering_id in offering_ids:
        # Git may finish after a timetable update. Resolve the latest ordering
        # while holding the same offering lock used by timetable publication.
        from .offering_session_resequence_service import plan_offering_resequence, apply_offering_resequence, is_numbered_session
        plan = plan_offering_resequence(conn, offering_id)
        apply_offering_resequence(conn, plan, note="Git 教材同步校准课次序号")
        sessions = conn.execute("SELECT id,order_index,title,schedule_status,session_date FROM class_offering_sessions "
                                "WHERE class_offering_id=? ORDER BY order_index,id", (offering_id,)).fetchall()
        sessions_by_id = {int(row["id"]): dict(row) for row in sessions}
        targets = {int(row["order_index"]): dict(row) for row in sessions if is_numbered_session(dict(row))}
        targets[0] = {"id": 0, "title": "课程首页"}
        previous = {int(row["lesson_order"]): dict(row) for row in conn.execute(
            "SELECT * FROM class_offering_git_learning_bindings WHERE class_offering_id=? AND repository_id=?",
            (offering_id, root["id"])).fetchall()}
        ambiguous_ids = {int(item["material_id"]) for item in unresolved}
        actions = []
        for number in sorted(set(previous) | set(entries)):
            rule = previous.get(number)
            entry, target = entries.get(number), targets.get(number)
            # An ambiguous rename must not destructively choose a replacement.
            if not entry and rule and int(rule["material_id"]) in ambiguous_ids:
                continue
            if rule and int(rule["suppressed"]):
                result["suppressed_count"] += 1
                continue
            desired_id = int(entry["id"]) if entry and target else 0
            desired_session = int(target["id"]) if target else 0
            if entry and not target:
                result["warnings"].append({"code": "missing_active_lesson", "class_offering_id": offering_id,
                    "lesson_order": number, "material_id": int(entry["id"]), "reason": "没有对应的有效课次，教材未自动绑定。"})
            actions.append({"number": number, "rule": rule, "entry": entry, "target": target,
                            "desired_id": desired_id, "desired_session": desired_session})
        # Remove all obsolete active projections before installing any target.
        # Per-number interleaving can otherwise remove a newly installed primary
        # during cancellation/ordinal compression or overlapping old rules.
        cleared_primaries = set()
        for action in actions:
            rule = action["rule"]
            desired_id, desired_session = action["desired_id"], action["desired_session"]
            if rule and (int(rule["material_id"]) != desired_id or int(rule["session_id"]) != desired_session):
                if int(rule["owns_binding"]):
                    sid, mid = int(rule["session_id"]), int(rule["material_id"])
                    old_session = sessions_by_id.get(sid)
                    if sid and old_session and not is_numbered_session(old_session):
                        result["warnings"].append({"code": "inactive_binding_retained", "class_offering_id": offering_id,
                            "session_id": sid, "material_id": mid, "reason": "已取消或未排期课次保留原教材，旧自动规则已解除。"})
                    else:
                        conn.execute("DELETE FROM class_offering_learning_materials WHERE class_offering_id=? AND session_id=? AND material_id=?",
                                     (offering_id, sid, mid))
                        if materials._primary_material_id(conn, offering_id, sid) == mid:
                            cleared_primaries.add(sid)
                        result["removed_count"] += 1
                conn.execute("DELETE FROM class_offering_git_learning_bindings WHERE class_offering_id=? AND repository_id=? AND lesson_order=?",
                             (offering_id, root["id"], action["number"]))
                action["rule"] = None
        for sid in sorted(cleared_primaries):
            remaining = materials._fetch_rows(conn, offering_id, sid)
            materials._set_primary_material_id(conn, offering_id, sid, int(remaining[0]["material_id"]) if remaining else None)
        granted = []
        for action in actions:
            number, rule, entry, target = (action[key] for key in ("number", "rule", "entry", "target"))
            desired_id, desired_session = action["desired_id"], action["desired_session"]
            if not desired_id:
                continue
            materials._backfill_primary(conn, offering_id, desired_session, teacher_id)
            primary_id = materials._primary_material_id(conn, offering_id, desired_session)
            correct_legacy = primary_id in canonical_numbers and canonical_numbers[primary_id] != number
            if correct_legacy:
                # Only a proven entry from this same numbered repository is a
                # wrong legacy primary. Unrelated hand-picked documents and all
                # other supplemental bindings are preserved.
                conn.execute("DELETE FROM class_offering_learning_materials WHERE class_offering_id=? AND session_id=? AND material_id=?",
                             (offering_id, desired_session, primary_id))
                result["corrected_legacy_count"] += 1
            existing = materials._fetch_rows(conn, offering_id, desired_session)
            present = any(int(row["material_id"]) == desired_id for row in existing)
            owns = int(rule["owns_binding"]) if rule else int(not present)
            if not present:
                materials._insert_row(conn, offering_id, desired_session, desired_id, teacher_id,
                                      sort_order=(min((int(row["sort_order"]) for row in existing), default=0) - 1) if correct_legacy
                                      else max((int(row["sort_order"]) for row in existing), default=-1) + 1)
            elif correct_legacy:
                conn.execute("UPDATE class_offering_learning_materials SET sort_order=? WHERE class_offering_id=? AND session_id=? AND material_id=?",
                             (min((int(row["sort_order"]) for row in existing), default=0) - 1, offering_id, desired_session, desired_id))
            if correct_legacy or materials._primary_material_id(conn, offering_id, desired_session) <= 0:
                materials._set_primary_material_id(conn, offering_id, desired_session, desired_id)
            conn.execute("""INSERT INTO class_offering_git_learning_bindings
                (class_offering_id,repository_id,lesson_order,material_id,session_id,owns_binding,suppressed,updated_at)
                VALUES (?,?,?,?,?,?,0,?) ON CONFLICT(class_offering_id,repository_id,lesson_order)
                DO UPDATE SET material_id=excluded.material_id,session_id=excluded.session_id,
                              owns_binding=excluded.owns_binding,updated_at=excluded.updated_at""",
                (offering_id, root["id"], number, desired_id, desired_session, owns, materials._now()))
            granted.append(desired_id)
            result["total_home_assignments" if number == 0 else "total_assignments"] += 1
            result["assignments"].append({"class_offering_id": offering_id, "session_id": desired_session,
                "target_type": "home" if number == 0 else "lesson", "confidence": "high",
                "order_index": number, "material_id": desired_id, "session_title": target["title"],
                "material_path": entry["material_path"], "source": "repository_ordinal"})
        if granted:
            sync_classroom_learning_material_assignments(conn, class_offering_id=offering_id,
                                                         teacher_id=teacher_id, material_ids=granted)
    result["message"] = f"已同步 {result['total_assignments']} 个课次教材、{result['total_home_assignments']} 个课程首页。"
    if result["warnings"]:
        result["message"] += f"另有 {len(result['warnings'])} 项保留或未匹配记录，请核对。"
    return result
