"""Publish an immutable package to classroom groups; all writes use the classroom roster lock.

The caller owns the transaction. Visiting an activity never creates or draws a binding.
"""

import hashlib
import json
import secrets
from collections import Counter
from datetime import datetime

from fastapi import HTTPException

from ..db.connection import execute_insert_returning_id
from . import collaboration_service as groups
from . import project_topic_package_service as packages
from .resource_access_service import student_can_read_assignment


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _positive(value, field):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit() or len(str(value)) > 18:
        raise HTTPException(400, f"{field}无效")
    parsed = int(value)
    if parsed <= 0:
        raise HTTPException(400, f"{field}无效")
    return parsed


def _text(value, limit, field):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise HTTPException(400, f"请填写{field}（最多 {limit} 字）")
    return value.strip()


def _is_teacher(user):
    return groups._is_teacher(user)


def _teacher(user):
    if not _is_teacher(user):
        raise HTTPException(403, "仅课堂教师可以管理派题活动")
    return groups._user_pk(user)


def _dispatch_row(conn, dispatch_id):
    row = conn.execute("SELECT * FROM project_topic_dispatches WHERE id=?", (dispatch_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "派题活动不存在")
    return dict(row)


def _summary(row):
    return {key: row[key] for key in (
        "id", "class_offering_id", "scheme_id", "package_version_id", "title",
        "state", "mode", "revision", "created_at", "updated_at",
    )}


def list_dispatches(conn, offering_id, user):
    groups.ensure_classroom_access(conn, offering_id, user)
    predicate = "" if _is_teacher(user) else " AND state IN ('published','archived')"
    rows = conn.execute(
        "SELECT id,class_offering_id,scheme_id,package_version_id,title,state,mode,revision,created_at,updated_at "
        f"FROM project_topic_dispatches WHERE class_offering_id=?{predicate} ORDER BY id DESC", (offering_id,),
    ).fetchall()
    return [_summary(row) for row in rows]


def _live_roster(conn, scheme, group_rows):
    """Use the grouping service's eligibility and historical roster rules."""
    members = conn.execute("""
        SELECT m.student_id,m.group_id,s.name,s.student_id_number FROM study_group_members m
        JOIN study_groups g ON g.id=m.group_id JOIN students s ON s.id=m.student_id
        WHERE g.scheme_id=? AND m.status='active' ORDER BY m.student_id,m.group_id
    """, (scheme["id"],)).fetchall()
    assignments = {r["student_id"]: r["group_id"] for r in members}
    if len(assignments) != len(members):
        raise HTTPException(409, "方案内有学生同时属于多个组，请核对原分组")
    closed = scheme["status"] != groups.SCHEME_STATUS_ACTIVE or groups._scheme_is_expired(scheme.get("expires_at"))
    roster = {} if closed else {
        r["id"]: {"student_id": r["id"], "student_name": r["name"],
                  "student_number": r["student_id_number"], "group_id": None}
        for r in groups._scheme_roster(conn, scheme, grouped_ids=set(assignments))
    }
    selected = {r["id"] for r in group_rows}
    for row in members:
        if row["group_id"] in selected:
            out_of_class = not closed and row["student_id"] not in roster
            roster[row["student_id"]] = {
                "student_id": row["student_id"], "student_name": row["name"],
                "student_number": row["student_id_number"], "group_id": row["group_id"],
                "out_of_class": out_of_class,
            }
        else:
            roster.pop(row["student_id"], None)
    return sorted(roster.values(), key=lambda r: (r["student_number"], r["student_id"]))


def _group_plan(conn, offering_id, payload):
    scheme_id, new = payload.get("scheme_id"), payload.get("new_scheme")
    if bool(scheme_id) == (new is not None):
        raise HTTPException(400, "请选择一个已有方案或新建分组")
    if scheme_id:
        scheme = groups._load_scheme(conn, _positive(scheme_id, "分组方案"))
        if scheme["class_offering_id"] != offering_id:
            raise HTTPException(400, "分组方案不属于当前课堂")
        rows = groups._scheme_group_rows(conn, scheme["id"])
        if any(g["class_offering_id"] != offering_id for g in rows):
            raise HTTPException(409, "方案里含有课堂不匹配的小组，请先核对原分组")
        if payload.get("group_ids") is not None:
            raw = payload["group_ids"]
            if not isinstance(raw, list) or not raw:
                raise HTTPException(400, "请选择参与本次派题的小组")
            selected = {_positive(g, "小组") for g in raw}
            rows = [g for g in rows if g["id"] in selected]
            if len(rows) != len(raw):
                raise HTTPException(400, "组名单重复或含有其他方案的小组")
        if not rows:
            raise HTTPException(400, "分组方案中没有可参与的小组")
        roster = _live_roster(conn, scheme, rows)
        counts = Counter(r["group_id"] for r in roster)
        outline = [{"id": g["id"], "name": g["name"], "group_index": g["group_index"],
                    "member_count": counts[g["id"]]} for g in rows]
        members = sorted((r["student_id"], r["group_id"]) for r in roster)
        unavailable = sum(bool(r.get("out_of_class")) for r in roster)
        return outline, members, counts[None], unavailable

    if not isinstance(new, dict):
        raise HTTPException(400, "新建分组设置无效")
    if payload.get("group_ids") is not None:
        raise HTTPException(400, "新建方案将为全部小组派题")
    include = new.get("include_retake", False)
    mode = new.get("allocation_mode", "all")
    if not isinstance(include, bool) or mode not in ("all", "self"):
        raise HTTPException(400, "新建分组方式或重修学生选项无效")
    students = [s["id"] for s in groups._scheme_class_students(conn, offering_id) if include or not s["exempt"]]
    minimum = groups._normalize_member_bound(new.get("min_members"), field_name="每组最小人数", default=2)
    maximum = groups._normalize_member_bound(new.get("max_members"), field_name="每组最大人数",
                                              default=groups.DEFAULT_GROUP_MAX_MEMBERS)
    requested = new.get("group_count")
    count = groups._resolve_group_count(len(students), minimum, maximum,
                                        _positive(requested, "组数") if requested not in (None, "") else None)
    average, extra = divmod(len(students), count)
    outline = [{"id": None, "group_index": i, "name": f"第 {i} 组",
                "member_count": (average + int(i <= extra)) if mode == "all" else 0}
               for i in range(1, count + 1)]
    return outline, sorted((sid, None) for sid in students), len(students) if mode == "self" else 0, 0


def _eligible(topic, allow_brief):
    return topic["readiness"] == "complete" or (allow_brief and topic["readiness"] == "brief")


def _pool(package, requested, allow_brief):
    if not isinstance(allow_brief, bool):
        raise HTTPException(400, "简介派发选项无效")
    topics = {t["key"]: t for t in package["topics"]}
    if requested is None:
        return [k for k, t in topics.items() if t["pool"] != "reserve" and _eligible(t, allow_brief)], []
    if not isinstance(requested, list) or not all(isinstance(k, str) for k in requested):
        raise HTTPException(400, "请选择有效的题目池")
    if len(requested) != len(set(requested)) or any(k not in topics for k in requested):
        raise HTTPException(400, "题目池含重复或其他版本的题目")
    issues = [f"「{topics[k]['title']}」未达到本次派发要求" for k in requested if not _eligible(topics[k], allow_brief)]
    return [k for k in requested if _eligible(topics[k], allow_brief)], issues


def _binding_map(raw, group_keys, pool, *, field, partial=False):
    if not isinstance(raw, list):
        raise HTTPException(400, "请逐组指定题目")
    found = {}
    for item in raw:
        if not isinstance(item, dict):
            raise HTTPException(400, "组题记录格式无效")
        gid = _positive(item.get(field), "小组")
        key = item.get("topic_key")
        if gid not in group_keys or gid in found or not isinstance(key, str) or key not in pool:
            raise HTTPException(400, "组题记录包含重复小组、无效小组或未选题目")
        found[gid] = key
    if len(set(found.values())) != len(found):
        raise HTTPException(400, "每道题在本活动中只能分配给一个组")
    if not partial and found.keys() != group_keys:
        raise HTTPException(400, "请为每个参与小组指定题目")
    return found


def _settings(payload):
    return {k: v for k, v in payload.items() if k not in ("preview_token", "request_id", "draft_revision")}


def _read_draft(conn, offering_id, teacher_id, version):
    row = conn.execute("""
        SELECT revision,payload_json,updated_at FROM project_topic_dispatch_drafts
        WHERE class_offering_id=? AND teacher_id=? AND package_version_id=?
    """, (offering_id, teacher_id, version)).fetchone()
    return {"package_version_id": version, "revision": row["revision"] if row else 0,
            "payload": json.loads(row["payload_json"]) if row and row["payload_json"] else None,
            "updated_at": row["updated_at"] if row else None}


def get_dispatch_draft(conn, offering_id, user, package_version_id):
    teacher_id = _teacher(user)
    groups.ensure_classroom_access(conn, offering_id, user)
    packages.ensure_teacher_package_owner(conn, package_version_id, teacher_id)
    return _read_draft(conn, offering_id, teacher_id, package_version_id)


def save_dispatch_draft(conn, offering_id, user, package_version_id, value):
    teacher_id = _teacher(user)
    groups._lock_classroom_memberships(conn, offering_id, user)
    packages.ensure_teacher_package_owner(conn, package_version_id, teacher_id)
    old = _read_draft(conn, offering_id, teacher_id, package_version_id)
    revision, payload = value.get("revision"), value.get("payload")
    if type(revision) is not int or revision < 0:
        raise HTTPException(400, "草稿修订号无效")
    fields = {"package_version_id", "title", "mode", "scheme_id", "new_scheme", "group_ids", "topic_keys",
              "allow_brief", "bindings", "assignment_id"}
    if not isinstance(payload, dict) or not payload.keys() <= fields:
        raise HTTPException(400, "派题草稿配置格式无效")
    if "package_version_id" in payload and _positive(payload["package_version_id"], "题包版本") != package_version_id:
        raise HTTPException(400, "草稿与所选题包版本不一致")
    payload = dict(payload, package_version_id=package_version_id)
    serialized = _json(payload)
    if len(serialized.encode()) > 128 * 1024:
        raise HTTPException(400, "派题草稿过大")
    if revision != old["revision"]:
        # The previous save may have committed before the HTTP connection failed.
        if revision + 1 == old["revision"] and old["payload"] == payload:
            return old
        raise HTTPException(409, "派题草稿已由其他窗口保存，请刷新并核对")
    now = _now()
    conn.execute("""
        INSERT INTO project_topic_dispatch_drafts
            (class_offering_id,teacher_id,package_version_id,revision,payload_json,updated_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT (class_offering_id,teacher_id,package_version_id) DO UPDATE SET
            revision=excluded.revision,payload_json=excluded.payload_json,updated_at=excluded.updated_at
    """, (offering_id, teacher_id, package_version_id, revision + 1, serialized, now))
    return {"package_version_id": package_version_id, "revision": revision + 1, "payload": payload, "updated_at": now}


def _clear_published_draft(conn, offering_id, teacher_id, version, payload, now):
    revision = payload.get("draft_revision")
    if type(revision) is int and revision > 0:
        # Retain a tombstone revision so an old tab cannot overwrite a newly
        # started draft with the same teacher/class/package key after publish.
        conn.execute("""
            UPDATE project_topic_dispatch_drafts SET payload_json=NULL,revision=revision+1,updated_at=?
            WHERE class_offering_id=? AND teacher_id=? AND package_version_id=? AND revision=? AND payload_json=?
        """, (now, offering_id, teacher_id, version, revision, _json(_settings(payload))))


def _preview(conn, offering_id, teacher_id, payload):
    version_id = _positive(payload.get("package_version_id"), "题包版本")
    package = packages.get_teacher_package(conn, version_id, teacher_id)
    title = _text(payload.get("title"), 100, "活动名称")
    mode = payload.get("mode", "random")
    if mode not in ("random", "manual"):
        raise HTTPException(400, "请选择随机派题或手动指定")
    if mode == "random" and payload.get("bindings"):
        raise HTTPException(400, "随机派题模式不能同时指定题目")
    if payload.get("assignment_id"):
        groups._load_assignment(conn, offering_id, payload["assignment_id"])
    outline, members, unassigned, unavailable = _group_plan(conn, offering_id, payload)
    pool, issues = _pool(package, payload.get("topic_keys"), payload.get("allow_brief", False))
    if not package["mapping_confirmed"]:
        issues.append("请先确认题包的文件与题目映射")
    issues.extend(package.get("common_issues") or [])
    if unavailable:
        issues.append(f"现有小组中有 {unavailable} 名成员已离开当前课堂，请核对并调整原分组")
    if len(outline) > len(pool):
        issues.append(f"共 {len(outline)} 组、可派 {len(pool)} 题，还差 {len(outline)-len(pool)} 题")
    field = "id" if payload.get("scheme_id") else "group_index"
    manual = {}
    if mode == "manual":
        try:
            manual = _binding_map(payload.get("bindings", []), {g[field] for g in outline}, set(pool),
                                  field="group_id" if field == "id" else field)
        except HTTPException as exc:
            issues.append(exc.detail)
    # An independent confirmation from another tab of the same class must not
    # create a second copy of this draw after the first tab committed.
    latest = conn.execute("SELECT MAX(id) FROM project_topic_dispatches WHERE class_offering_id=?",
                          (offering_id,)).fetchone()[0]
    copies = packages.get_existing_classroom_copies(conn, version_id, offering_id)
    token = _digest([offering_id, latest, _settings(payload), outline, members, pool, issues, copies])
    return {
        "title": title, "mode": mode, "ready": not issues, "issues": issues, "preview_token": token,
        "group_count": len(outline), "student_count": len(members), "groups": outline,
        "eligible_topic_count": len(pool), "unassigned_count": unassigned, "public_copies": copies,
    }, pool, manual


def preview_dispatch(conn, offering_id, user, payload):
    teacher_id = _teacher(user)
    groups.ensure_classroom_access(conn, offering_id, user)
    return _preview(conn, offering_id, teacher_id, payload)[0]


def _record_history(conn, did, revision, teacher, action, reason, changes, now):
    conn.execute("""
        INSERT INTO project_topic_dispatch_history
            (dispatch_id,revision,teacher_id,action,reason,changes_json,created_at) VALUES (?,?,?,?,?,?,?)
    """, (did, revision, teacher, action, reason, _json(changes), now))


def _insert_bindings(conn, dispatch_id, version, rows):
    conn.executemany("""
        INSERT INTO project_topic_bindings (dispatch_id,package_id,group_id,group_name,group_index,topic_key)
        VALUES (?,?,?,?,?,?)
    """, [(dispatch_id, version, g["id"], g["name"], g["group_index"], key) for g, key in rows])


def publish_dispatch(conn, offering_id, user, payload):
    teacher_id = _teacher(user)
    request_id = _text(payload.get("request_id"), 100, "发布请求标识")
    groups._lock_classroom_memberships(conn, offering_id, user)
    signature = _digest(_settings(payload))
    existing = conn.execute("""
        SELECT id,request_signature FROM project_topic_dispatches
        WHERE class_offering_id=? AND teacher_id=? AND request_id=?
    """, (offering_id, teacher_id, request_id)).fetchone()
    if existing:
        if existing["request_signature"] != signature:
            raise HTTPException(409, "此次请求已用于其他派题设置，请刷新核对")
        return get_dispatch(conn, existing["id"], user)
    preview, pool, manual = _preview(conn, offering_id, teacher_id, payload)
    if payload.get("preview_token") != preview["preview_token"]:
        raise HTTPException(409, "派题配置、课堂名单或活动已变化，请重新预检")
    if not preview["ready"]:
        raise HTTPException(409, "；".join(preview["issues"]))
    mode = preview["mode"]
    if payload.get("scheme_id"):
        scheme_id, outline = int(payload["scheme_id"]), preview["groups"]
        lookup = "id"
    else:
        # The group service participates in this same outer transaction, including
        # the full-class shuffle; a failed topic insert also rolls back the groups.
        config = {"name": preview["title"], "allocation_mode": "all", **payload["new_scheme"]}
        config.pop("request_id", None)
        scheme = groups.create_group_scheme(conn, offering_id, user, config)
        scheme_id, outline, lookup = scheme["id"], scheme["groups"], "group_index"
    keys = secrets.SystemRandom().sample(pool, len(outline)) if mode == "random" else [manual[g[lookup]] for g in outline]
    now = _now()
    version = int(payload["package_version_id"])
    did = execute_insert_returning_id(conn, """
        INSERT INTO project_topic_dispatches (class_offering_id,scheme_id,package_version_id,title,mode,state,
            teacher_id,request_id,request_signature,allow_brief,pool_json,assignment_id,created_at,updated_at)
        VALUES (?,?,?,?,?,'published',?,?,?,?,?,?,?,?)
    """, (offering_id, scheme_id, version, preview["title"], mode, teacher_id, request_id, signature,
          int(payload.get("allow_brief", False)), _json(pool), payload.get("assignment_id") or None, now, now))
    _insert_bindings(conn, did, version, zip(outline, keys))
    _record_history(conn, did, 1, teacher_id, "publish", "",
                    {"version": version, "bindings": {g["id"]: k for g, k in zip(outline, keys)}}, now)
    _clear_published_draft(conn, offering_id, teacher_id, version, payload, now)
    return get_dispatch(conn, did, user)


def _bound_groups(conn, dispatch):
    rows = conn.execute("""
        SELECT b.group_id AS id, b.topic_key, b.group_name AS frozen_name, b.group_index AS frozen_index,
               g.name, g.group_index FROM project_topic_bindings b JOIN study_groups g ON g.id=b.group_id
        WHERE b.dispatch_id=? ORDER BY b.group_index,b.group_id
    """, (dispatch["id"],)).fetchall()
    return [{"id": r["id"], "name": r["frozen_name"] if dispatch["archived_at"] else r["name"],
             "group_index": r["frozen_index"] if dispatch["archived_at"] else r["group_index"],
             "topic_key": r["topic_key"]} for r in rows]


def _roster(conn, dispatch, outline):
    if dispatch["archived_at"]:
        return [dict(r) for r in conn.execute("""
            SELECT student_id,student_name,student_number,group_id FROM project_topic_archived_roster
            WHERE dispatch_id=? ORDER BY student_number,student_id
        """, (dispatch["id"],)).fetchall()]
    scheme = groups._load_scheme(conn, dispatch["scheme_id"])
    return _live_roster(conn, scheme, outline)


def _check_read_revision(conn, dispatch):
    current = conn.execute("SELECT revision FROM project_topic_dispatches WHERE id=?", (dispatch["id"],)).fetchone()
    if current is None or current["revision"] != dispatch["revision"]:
        raise HTTPException(409, "活动在读取期间已修订，请刷新查看最新结果")


def _student_scope(conn, dispatch_id, sid):
    # One statement selects the activity's current state, package version, and
    # either live or frozen entitlement. In READ COMMITTED a prior activity
    # read and a later live membership read could straddle an archive + a join.
    row = conn.execute("""
        SELECT d.*, b.group_id AS own_group_id,b.topic_key AS own_key,
               CASE WHEN d.archived_at IS NULL THEN g.name ELSE b.group_name END AS own_name,
               CASE WHEN d.archived_at IS NULL THEN g.group_index ELSE b.group_index END AS own_index
        FROM project_topic_dispatches d
        LEFT JOIN project_topic_archived_roster ar
            ON d.archived_at IS NOT NULL AND ar.dispatch_id=d.id AND ar.student_id=?
        LEFT JOIN project_topic_bindings b ON b.dispatch_id=d.id AND (
            (d.archived_at IS NOT NULL AND b.group_id=ar.group_id) OR
            (d.archived_at IS NULL AND EXISTS (
                SELECT 1 FROM study_group_members m WHERE m.group_id=b.group_id AND m.student_id=? AND m.status='active'
            ))
        )
        LEFT JOIN study_groups g ON g.id=b.group_id
        WHERE d.id=? AND d.state IN ('published','archived')
    """, (sid, sid, dispatch_id)).fetchone()
    if row is None:
        raise HTTPException(404, "派题活动不可查看")
    own = ({"id": row["own_group_id"], "name": row["own_name"], "group_index": row["own_index"]}
           if row["own_group_id"] is not None else None)
    return dict(row), own, row["own_key"]


def _assignment_link(conn, dispatch, user, visible):
    if not visible or not dispatch["assignment_id"]:
        return {"assignment_id": None, "assignment_title": None}
    row = conn.execute("SELECT * FROM assignments WHERE class_offering_id=? AND id=?",
                       (dispatch["class_offering_id"], dispatch["assignment_id"])).fetchone()
    if row is None or (not _is_teacher(user) and not student_can_read_assignment(conn, row, user["id"])):
        return {"assignment_id": None, "assignment_title": None}
    return {"assignment_id": str(row["id"]), "assignment_title": row["title"]}


def get_dispatch(conn, dispatch_id, user):
    dispatch = _dispatch_row(conn, dispatch_id)
    groups.ensure_classroom_access(conn, dispatch["class_offering_id"], user)
    if not _is_teacher(user):
        dispatch, own, key = _student_scope(conn, dispatch_id, groups._user_pk(user))
        scoped = packages.get_scoped_topic(conn, dispatch["package_version_id"], key)
        result = dict(_summary(dispatch), my_group=own, common=scoped["common"], my_topic=scoped["topic"])
        result.update(_assignment_link(conn, dispatch, user, bool(own)))
        return result

    result = dict(_summary(dispatch), my_group=None, my_topic=None)
    package = packages.get_package_version(conn, dispatch["package_version_id"])
    topic_by_key = {t["key"]: t for t in package["topics"]}
    outline = _bound_groups(conn, dispatch)
    roster = _roster(conn, dispatch, outline)
    counts = Counter(r["group_id"] for r in roster)
    history = conn.execute("""
        SELECT revision,teacher_id,action,reason,changes_json,created_at FROM project_topic_dispatch_history
        WHERE dispatch_id=? ORDER BY revision DESC
    """, (dispatch_id,)).fetchall()
    _check_read_revision(conn, dispatch)
    for group in outline:
        group["topic"] = topic_by_key[group["topic_key"]]
        group["member_count"] = counts[group["id"]]
    result.update(groups=outline, common=package["common"], group_count=len(outline), student_count=len(roster),
                  unassigned_count=counts[None], package_version=package["version"], package_title=package["title"],
                  pool_topic_keys=json.loads(dispatch["pool_json"]), allow_brief=bool(dispatch["allow_brief"]),
                  history=[{"revision": h["revision"], "teacher_id": h["teacher_id"], "action": h["action"],
                            "reason": h["reason"], "changes": json.loads(h["changes_json"]),
                            "created_at": h["created_at"]} for h in history])
    result.update(_assignment_link(conn, dispatch, user, True))
    return result


def resolve_dispatch_file(conn, dispatch_id, user, path, *, topic_key=None):
    dispatch = _dispatch_row(conn, dispatch_id)
    groups.ensure_classroom_access(conn, dispatch["class_offering_id"], user)
    if _is_teacher(user):
        if topic_key is not None and not conn.execute(
            "SELECT 1 FROM project_topic_bindings WHERE dispatch_id=? AND topic_key=?", (dispatch_id, topic_key),
        ).fetchone():
            raise HTTPException(404, "题目不属于本次派发")
    else:
        dispatch, _, key = _student_scope(conn, dispatch_id, groups._user_pk(user))
        if topic_key is not None and key != topic_key:
            raise HTTPException(404, "文件不可查看")
    return packages.get_scoped_resource(conn, dispatch["package_version_id"], path, topic_key=topic_key)


def _freeze_roster(conn, dispatch):
    outline = _bound_groups(conn, dispatch)
    roster = _roster(conn, dispatch, outline)
    conn.executemany("""
        INSERT INTO project_topic_archived_roster (dispatch_id,student_id,student_name,student_number,group_id)
        VALUES (?,?,?,?,?)
    """, [(dispatch["id"], r["student_id"], r["student_name"], r["student_number"], r["group_id"]) for r in roster])
    conn.executemany("""
        UPDATE project_topic_bindings SET group_name=?,group_index=? WHERE dispatch_id=? AND group_id=?
    """, [(g["name"], g["group_index"], dispatch["id"], g["id"]) for g in outline])


def _existing_work(conn, dispatch, affected):
    from .group_assignment_service import scheme_assignment_activity_groups

    locked = scheme_assignment_activity_groups(conn, dispatch["scheme_id"], lock=True)
    if locked.intersection(affected):
        return True
    marks = ",".join("?" for _ in affected)
    if conn.execute(f"SELECT 1 FROM group_submissions WHERE group_id IN ({marks}) LIMIT 1", affected).fetchone():
        return True
    if dispatch["assignment_id"]:
        return bool(conn.execute(f"""
            SELECT 1 FROM submissions s JOIN study_group_members m ON m.student_id=s.student_pk_id
            WHERE s.assignment_id=? AND m.status='active' AND m.group_id IN ({marks}) LIMIT 1
        """, (dispatch["assignment_id"], *affected)).fetchone())
    return False


def _rebind(conn, dispatch, payload):
    outline = _bound_groups(conn, dispatch)
    previous = {g["id"]: g["topic_key"] for g in outline}
    changes = _binding_map(payload.get("bindings"), set(previous), set(json.loads(dispatch["pool_json"])),
                           field="group_id", partial=True)
    changes = {gid: key for gid, key in changes.items() if key != previous[gid]}
    if not changes:
        raise HTTPException(400, "尚未选择需要更正的组题")
    if len(set((previous | changes).values())) != len(outline):
        raise HTTPException(409, "所选题已被其他小组使用。如需交换，请一并指定两组的新题目")
    if _existing_work(conn, dispatch, tuple(changes)) and payload.get("acknowledge_work") is not True:
        raise HTTPException(409, "涉及的小组已有学生作品或提交，请核对后明确确认换题")
    conn.executemany("DELETE FROM project_topic_bindings WHERE dispatch_id=? AND group_id=?",
                     [(dispatch["id"], gid) for gid in changes])
    _insert_bindings(conn, dispatch["id"], dispatch["package_version_id"],
                     [(g, changes[g["id"]]) for g in outline if g["id"] in changes])
    return {"groups": [{"group_id": gid, "from": previous[gid], "to": key} for gid, key in changes.items()]}


def _upgrade(conn, dispatch, teacher_id, payload):
    version = _positive(payload.get("package_version_id"), "新题包版本")
    if version == dispatch["package_version_id"]:
        raise HTTPException(400, "活动已在使用这个题包版本")
    new = packages.get_teacher_package(conn, version, teacher_id)
    old = packages.get_teacher_package(conn, dispatch["package_version_id"], teacher_id)
    if new["package_key"] != old["package_key"]:
        raise HTTPException(409, "请选择同一题包的新版本，不能按题号跨包覆盖")
    if not new["mapping_confirmed"] or new.get("common_issues"):
        raise HTTPException(409, "新版本的题目映射或公共文件仍需确认")
    allow_brief = payload.get("allow_brief", bool(dispatch["allow_brief"]))
    # Preserve the teacher's selected pool, especially the reserve opt-in. A
    # removed, unassigned topic can be explicitly deselected for this revision.
    pool, issues = _pool(new, payload.get("topic_keys", json.loads(dispatch["pool_json"])), allow_brief)
    outline = _bound_groups(conn, dispatch)
    if issues or any(g["topic_key"] not in pool for g in outline):
        raise HTTPException(409, "新题包需保留本次已派题目的稳定标识，且所有选中题目须达到派发要求")
    conn.execute("DELETE FROM project_topic_bindings WHERE dispatch_id=?", (dispatch["id"],))
    conn.execute("UPDATE project_topic_dispatches SET package_version_id=?,pool_json=?,allow_brief=? WHERE id=?",
                 (version, _json(pool), int(allow_brief), dispatch["id"]))
    _insert_bindings(conn, dispatch["id"], version, [(g, g["topic_key"]) for g in outline])
    return {"from_version": dispatch["package_version_id"], "to_version": version, "pool": pool}


def update_dispatch(conn, dispatch_id, user, payload):
    teacher_id = _teacher(user)
    first = _dispatch_row(conn, dispatch_id)
    groups._lock_classroom_memberships(conn, first["class_offering_id"], user)
    row = _dispatch_row(conn, dispatch_id)
    revision = _positive(payload.get("revision"), "活动修订号")
    if row["revision"] != revision:
        raise HTTPException(409, "活动已更新，请刷新后核对本次变更")
    action = payload.get("action")
    if action not in ("withdraw", "restore", "archive", "rebind", "upgrade"):
        raise HTTPException(400, "派题操作无效")
    reason = "" if action == "archive" and not payload.get("reason") else _text(payload.get("reason"), 600, "变更原因")
    state, archived_at, now = row["state"], row["archived_at"], _now()
    changes = {}
    if action == "withdraw" and state in ("published", "archived"):
        state = "withdrawn"
    elif action == "restore" and state == "withdrawn":
        state = "archived" if archived_at else "published"
    elif action == "archive" and state == "published":
        _freeze_roster(conn, row)
        state, archived_at = "archived", now
    elif action in ("rebind", "upgrade") and not archived_at and state in ("published", "withdrawn"):
        changes = _rebind(conn, row, payload) if action == "rebind" else _upgrade(conn, row, teacher_id, payload)
    else:
        raise HTTPException(409, "活动当前状态不支持此操作")
    conn.execute("""
        UPDATE project_topic_dispatches SET state=?,revision=?,updated_at=?,archived_at=? WHERE id=?
    """, (state, revision + 1, now, archived_at, dispatch_id))
    _record_history(conn, dispatch_id, revision + 1, teacher_id, action, reason, changes, now)
    return get_dispatch(conn, dispatch_id, user)


def export_dispatch(conn, dispatch_id, user):
    from .offering_membership_service import offering_display_class_name

    _teacher(user)
    dispatch = _dispatch_row(conn, dispatch_id)
    offering = groups.ensure_classroom_access(conn, dispatch["class_offering_id"], user)
    class_name = offering_display_class_name(conn, offering)
    package = packages.get_package_version(conn, dispatch["package_version_id"])
    topics = {t["key"]: t for t in package["topics"]}
    outline = _bound_groups(conn, dispatch)
    indexed = {g["id"]: g for g in outline}
    rows = []
    for member in _roster(conn, dispatch, outline):
        group = indexed.get(member["group_id"], {})
        topic = topics.get(group.get("topic_key"), {})
        rows.append({
            "class_name": class_name, "course_name": offering["course_name"],
            "dispatch_title": dispatch["title"], "package_version": package["version"],
            "group_index": group.get("group_index"), "group_name": group.get("name", ""),
            "topic_number": topic.get("number", ""), "topic_title": topic.get("title", ""),
            "student_number": member["student_number"], "student_name": member["student_name"],
            "membership": "已分组" if group else "未分组",
        })
    _check_read_revision(conn, dispatch)
    return sorted(rows, key=lambda r: (r["group_index"] is None, r["group_index"], r["student_number"]))
