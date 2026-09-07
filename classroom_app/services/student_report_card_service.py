"""Student-only effective grades grouped by offering, semester and purpose.

Task averages are descriptive percentages, never the official weighted grade.
Unreleased group results are removed before personal and peer aggregation.
"""
from __future__ import annotations
from typing import Any
from .assessment_classification_service import enrich_assessment_classifications
from .score_projection_service import load_submission_score_facts
from .grade_publication_service import student_published_grades

MIN_BAND_SAMPLE = 5
PERCENTILE_BANDS = ((0.10, "前 10%", "top"), (0.25, "前 25%", "high"),
                    (0.50, "前 50%", "mid"), (1.01, "后 50%", "low"))


def _percentile_band(rank: int, total: int) -> tuple[str, str]:
    for threshold, label, tone in PERCENTILE_BANDS:
        if rank / total <= threshold:
            return label, tone
    return PERCENTILE_BANDS[-1][1:]


def _course_trend(records: list[dict[str, Any]]) -> str:
    scored = [r["my_score"] for r in records if r["my_score"] is not None]
    if len(scored) < 4:
        return "样本还少，继续积累"
    diff = sum(scored[-3:]) / 3 - sum(scored[:-3]) / len(scored[:-3])
    return "稳步上升" if diff >= 3 else "最近有所下滑" if diff <= -3 else "保持平稳"


def _summary_for_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [r["my_score"] for r in records if r["my_score"] is not None]
    return {
        "record_count": len(records), "graded_count": len(scores),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "latest_score": scores[-1] if scores else None, "trend_label": _course_trend(records),
        "chart": {"labels": [r["date_label"] or f"#{i+1}" for i, r in enumerate(records)],
                  "mine": [r["my_score"] for r in records],
                  "class_avg": [r["class_avg"] for r in records]},
    }


def build_student_report_card(conn, *, student_id: int, assessment_kind: str | None = None,
                              class_offering_id: int | None = None) -> dict[str, Any]:
    facts = load_submission_score_facts(conn, student_id=student_id, student_view=True, include_content=False)
    metadata = conn.execute("""
        SELECT a.*, c.name AS course_name, o.semester_id, sem.name AS semester_name
        FROM assignments a JOIN courses c ON c.id = a.course_id
        LEFT JOIN class_offerings o ON o.id = a.class_offering_id
        LEFT JOIN academic_semesters sem ON sem.id = o.semester_id
        WHERE EXISTS (SELECT 1 FROM submissions s WHERE s.assignment_id = a.id AND s.student_pk_id = ?)
    """, (int(student_id),)).fetchall()
    assignments = {str(row["id"]): row for row in enrich_assessment_classifications(conn, metadata)}
    facts = [f for f in facts if str(f["assignment_id"]) in assignments]
    if class_offering_id is not None:
        facts = [f for f in facts if assignments[str(f["assignment_id"])].get("class_offering_id") == class_offering_id]
    if assessment_kind:
        facts = [f for f in facts if assignments[str(f["assignment_id"])].get("assessment_kind") == assessment_kind
                 and not f["is_personal_stage"]]
    assignment_ids = list(dict.fromkeys(f["assignment_id"] for f in facts if not f["is_personal_stage"]))
    peers_by_assignment: dict[str, list[float]] = {}
    for fact in load_submission_score_facts(conn, assignment_ids=assignment_ids, student_view=True, include_content=False):
        if fact["score_visible"] and not fact["is_personal_stage"]:
            peers_by_assignment.setdefault(str(fact["assignment_id"]), []).append(fact["effective_score"])

    course_index: dict[tuple, dict[str, Any]] = {}
    personal_records, all_scores, charts = [], [], []
    top_band_count = 0
    for fact in facts:
        assignment = assignments[str(fact["assignment_id"])]
        score = round(fact["effective_score"], 1) if fact["score_visible"] else None
        peers = peers_by_assignment.get(str(fact["assignment_id"]), []) if score is not None else []
        band_label, band_tone = "", ""
        if len(peers) >= MIN_BAND_SAMPLE:
            band_label, band_tone = _percentile_band(1 + sum(p > score for p in peers), len(peers))
        record = {
            "assignment_id": fact["assignment_id"], "title": str(assignment.get("title") or "任务"),
            "is_exam": bool(assignment.get("exam_paper_id")), "has_exam_paper": bool(assignment.get("exam_paper_id")),
            "assessment_kind": assignment.get("assessment_kind"), "kind_label": assignment["assessment_kind_label"],
            "classification_status": assignment["classification_status"],
            "submitted_at": str(fact.get("submitted_at") or ""), "date_label": str(fact.get("submitted_at") or "")[:10],
            "my_score": score, "class_avg": round(sum(peers)/len(peers), 1) if peers else None,
            "class_count": len(peers), "band_label": band_label, "band_tone": band_tone,
            "is_late": bool(fact.get("is_late_submission")), "is_absence_score": bool(fact.get("is_absence_score")),
            "grade_display_state": fact["grade_display_state"], "is_regrading": fact["is_regrading"],
            "can_export_answer": fact["can_export_answer"], "score_visible": fact["score_visible"],
            "link_url": f"/assignment/{fact['assignment_id']}", "course_name": assignment["course_name"],
        }
        if fact["is_personal_stage"]:
            personal_records.append(record)
            continue
        if score is not None:
            all_scores.append(score)
            top_band_count += int(band_tone in {"top", "high"})
        key = (assignment.get("class_offering_id"), assignment.get("semester_id"), assignment["course_id"])
        course = course_index.setdefault(key, {
            "course_id": assignment["course_id"], "class_offering_id": assignment.get("class_offering_id"),
            "semester_id": assignment.get("semester_id"), "semester_name": assignment.get("semester_name") or "未关联学期",
            "course_name": assignment["course_name"], "records": [], "categories": [],
        })
        course["records"].append(record)

    courses = list(course_index.values())
    for course in courses:
        course.update(_summary_for_records(course["records"]))
        for kind in ("homework", "midterm", "final", None):
            records = [r for r in course["records"] if r["assessment_kind"] == kind]
            if not records:
                continue
            category = {"assessment_kind": kind, "label": records[0]["kind_label"], "records": records,
                        **_summary_for_records(records), "chart_index": len(charts)}
            charts.append(category["chart"])
            course["categories"].append(category)
        if len(course["categories"]) != 1:
            course["trend_label"] = "按任务类型查看趋势"
            course["chart"] = {"labels": [], "mine": [], "class_avg": []}
    courses.sort(key=lambda c: (str(c["semester_name"]), c["record_count"]), reverse=True)
    ranked = [c for c in courses if c["avg_score"] is not None]
    best = max(ranked, key=lambda c: c["avg_score"], default=None)
    weakest = min(ranked, key=lambda c: c["avg_score"], default=None)
    return {
        "published_grades": [item for item in student_published_grades(conn, student_id=student_id)
                             if class_offering_id is None or item["class_offering_id"] == class_offering_id],
        "courses": courses, "personal_records": personal_records, "charts": charts,
        "selected_assessment_kind": assessment_kind or "", "selected_class_offering_id": class_offering_id,
        "summary": {"record_total": len(all_scores), "pending_total": sum(c["record_count"] for c in courses)-len(all_scores),
                    "course_total": len(courses), "overall_avg": round(sum(all_scores)/len(all_scores), 1) if all_scores else None,
                    "top_band_count": top_band_count, "personal_record_total": len(personal_records),
                    "best_course": best["course_name"] if best else "",
                    "weakest_course": weakest["course_name"] if weakest and weakest is not best else ""},
    }
