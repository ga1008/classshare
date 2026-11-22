"""Private, descriptive evidence for the radar. Published cultivation rows are never written."""
from __future__ import annotations

from collections import Counter
import json

from ..db.schema_interaction_expression import EXPRESSION_MODEL_VERSION


def load_private_radar_rows(conn, offering_ids, student_id, *, audience):
    """One indexed read for both the student's scores and the permitted evidence."""
    if audience not in {"self", "teacher"} or not offering_ids:
        return []
    column = "self_json" if audience == "self" else "teacher_json"
    placeholders = ",".join("?" for _ in offering_ids)
    return conn.execute(
        f"""SELECT o.id AS offering, p.student_id AS scored_student, p.metrics_json, p.score,
                   x.{column} AS evidence_json, COALESCE(NULLIF(r.calculated_at,''),x.calculated_at) AS calculated_at
            FROM class_offerings o LEFT JOIN learning_progress_snapshots p
              ON p.class_offering_id=o.id AND p.student_id=?
            LEFT JOIN interaction_expression_snapshots x
              ON x.class_offering_id=o.id AND x.student_id=? AND x.model_version=?
            LEFT JOIN interaction_expression_refresh_state r
              ON r.class_offering_id=x.class_offering_id AND r.model_version=x.model_version
            WHERE o.id IN ({placeholders})""",
        (student_id, student_id, EXPRESSION_MODEL_VERSION, *offering_ids),
    ).fetchall()


def build_interaction_detail(rows, *, audience):
    """Aggregate only published course records. Pending history never means zero."""
    if audience not in {"self", "teacher"} or not rows:
        return None
    own = audience == "self"
    pending = 0
    timestamps = []
    expression_sources = Counter()
    totals = Counter()
    limited_courses = 0
    expression_courses = 0
    ai_complete = True
    for row in rows:
        try:
            record = json.loads(row["evidence_json"] or "{}")
            facts, opportunity = record["facts"], record["opportunities"]
            if not isinstance(facts, dict) or not isinstance(opportunity, dict):
                raise ValueError("invalid expression snapshot")
        except (KeyError, TypeError, ValueError):
            pending += 1
            continue
        if opportunity.get("coverage_status") == "ineligible":
            continue
        if row["calculated_at"]:
            timestamps.append(str(row["calculated_at"]))
        missing = set(facts.get("unavailable_sources", ()))
        if not facts or missing.intersection({"legacy_ai", "workspace_ai"}):
            ai_complete = False
        limited_courses += int(not facts or bool(missing) or opportunity.get("coverage_status") != "complete")
        totals["courses"] += 1
        expression_courses += int(bool(facts.get("expression_count")))
        expression_sources.update(facts.get("expression_source_counts", {}))
        for key in ("expression_count", "participation_count", "ai_message_count",
                    "legacy_ai_message_count", "workspace_ai_message_count"):
            totals[key] += facts.get(key, 0)
        for key in ("offered", "participated", "open", "open_participated", "expression_offered"):
            totals[key] += opportunity.get(key, 0)
    if not totals["courses"] and pending:
        return {"version": EXPRESSION_MODEL_VERSION, "status_label": "课堂记录正在整理",
                "participation_label": "待汇总", "expression_label": "待汇总",
                "coverage_label": f"{pending} 门课堂等待首次整理。", "ai_message_count": None}
    if not totals["courses"]:
        return None

    if totals["expression_count"]:
        status = "已记录互动表达"
    elif totals["expression_offered"]:
        status = "已有表达机会，尚未观察到表达"
    elif limited_courses or pending:
        status = "历史覆盖有限，暂无法判断表达机会"
    elif totals["participation_count"] or totals["participated"] or totals["open_participated"]:
        status = "已记录课堂参与，暂无可评表达样例"
    else:
        status = "暂无可评机会"

    participation = []
    if totals["offered"]:
        participation.append(f"已核实的结束活动 {totals['participated']}/{totals['offered']} 次")
    if totals["open"]:
        participation.append(f"进行中活动已参与 {totals['open_participated']}/{totals['open']} 次")
    if totals["participation_count"]:
        participation.append(f"发言、提问等参与记录 {totals['participation_count']} 件")
    if totals["ai_message_count"]:
        participation.append(f"课堂 AI 学生消息 {totals['ai_message_count']} 条")
    expression = f"{totals['expression_count']} 件可核实表达" if totals["expression_count"] else "尚无可核实的表达记录"
    examples = []
    ai_works = expression_sources["legacy_ai"] + expression_sources["workspace_ai"]
    if ai_works:
        examples.append(f"AI 提问 {ai_works} 件，同一会话同日合并")
    group_days = expression_sources["group_exchange"] + expression_sources["group_task_discussion"]
    if group_days:
        examples.append(f"小组讨论 {group_days} 件，同一小组同日合并")
    if expression_sources["discussion_reply"]:
        examples.append(f"研讨中回应 {expression_sources['discussion_reply']} 条原发言")
    for kind, label in (("qna", "问答墙"), ("quiz_response", "随堂测说明"), ("peer_review", "文字互评")):
        if expression_sources[kind]:
            examples.append(f"{label} {expression_sources[kind]} 件")
    if examples:
        expression += "（" + "，".join(examples) + "）"
    scope = f"{totals['courses']} 门课堂，按可确认归属的历史记录去重。"
    if limited_courses:
        scope += f"其中 {limited_courses} 门的历史资料或活动资格不完整。"
    if pending:
        scope += f"另有 {pending} 门课堂等待整理。"
    if timestamps:
        scope += "记录截至 " + min(timestamps)[:16].replace("T", " ") + "。"
    if not own:
        scope += "仅含任课教师可见的记录。"
    return {
        "version": EXPRESSION_MODEL_VERSION, "status_label": status,
        "participation_label": "；".join(participation) or "尚无可核实的课堂参与记录",
        "expression_label": expression, "coverage_label": scope,
        "ai_message_count": totals["ai_message_count"] if ai_complete and not pending else None,
        "expression_count": totals["expression_count"], "expression_courses": expression_courses,
        "offered": totals["offered"], "participated": totals["participated"],
    }
