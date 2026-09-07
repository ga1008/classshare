"""Teacher-authorized material source completeness; never changes source grades."""
from __future__ import annotations
import json
from typing import Any
from .score_projection_service import load_submission_score_facts
from .ai_model_policy import AI_EXECUTION_POLICY_VERSION


def build_grade_source_preflight(conn, *, assignment_ids: list[int], student_ids: list[int]) -> dict[str, Any]:
    facts = load_submission_score_facts(conn, assignment_ids=assignment_ids, include_content=False)
    roster_ids = set(student_ids)
    scoped = {(int(f["assignment_id"]), int(f["student_pk_id"])): f for f in facts if int(f["student_pk_id"] or 0) in roster_ids}
    issues = []
    counts = {"missing_score": 0, "regrading": 0, "review_required": 0, "group_unreleased": 0,
              "legacy_execution": 0, "classification_changed": 0, "quality_floor_mismatch": 0, "policy_changed": 0}
    classifications = {int(r["id"]): dict(r) for r in conn.execute(
        f"SELECT id, assessment_kind, assessment_kind_version FROM assignments WHERE id IN ({','.join('?' for _ in assignment_ids)})",
        tuple(assignment_ids),
    ).fetchall()} if assignment_ids else {}
    snapshots = []
    for aid in assignment_ids:
        for student_id in student_ids:
            fact = scoped.get((aid, student_id))
            current = classifications.get(aid, {})
            audit = {}
            review_codes = []
            reasons = []
            if not fact or not fact["has_effective_score"]:
                reasons.append("missing_score")
            if fact:
                if fact["is_regrading"]:
                    reasons.append("regrading")
                if fact.get("status") == "grading_review":
                    reasons.append("review_required")
                if fact["grade_display_state"] == "group_pending":
                    reasons.append("group_unreleased")
                try:
                    audit = json.loads(fact.get("grade_quality_audit_json") or "{}")
                    provenance = json.loads(fact.get("grade_provenance_json") or "{}")
                except (TypeError, ValueError):
                    audit, provenance = {}, {}
                audit = audit if isinstance(audit, dict) else {}
                provenance = provenance if isinstance(provenance, dict) else {}
                review_codes = sorted({str(code) for code in [
                    *(provenance.get("review_reason_codes") or []), *(audit.get("review_reason_codes") or [])
                ] if code})
                if (review_codes or audit.get("review_required")) and "review_required" not in reasons:
                    reasons.append("review_required")
                manual = (provenance.get("source") in {"manual", "teacher_absence"}
                          or audit.get("group_work_source") in {"manual", "teacher_absence"} or fact.get("is_absence_score"))
                if fact["has_effective_score"] and not manual and not audit.get("execution_metadata"):
                    reasons.append("legacy_execution")
                context = audit.get("business_context") or {}
                execution = audit.get("execution_metadata") or {}
                plan = audit.get("execution_plan") or {}
                if not manual and plan.get("capability") == "vision" and current.get("assessment_kind") in {"midterm", "final"}:
                    if execution and (execution.get("profile_id") != "vision_assessment_high" or execution.get("reasoning_effort", execution.get("effort")) != "high"):
                        reasons.append("quality_floor_mismatch")
                if not manual and audit.get("ai_policy_version") and audit["ai_policy_version"] != AI_EXECUTION_POLICY_VERSION:
                    reasons.append("policy_changed")
                if context and (context.get("assessment_kind") != current.get("assessment_kind")
                                or int(context.get("assessment_kind_version") or 0) != int(current.get("assessment_kind_version") or 0)):
                    reasons.append("classification_changed")
            snapshots.append({"assignment_id": aid, "student_id": student_id,
                "submission_id": fact["id"] if fact else None, "grade_revision_id": fact.get("effective_revision_id") if fact else None,
                "effective_score": fact["effective_score"] if fact else None, "status": fact.get("status") if fact else "unsubmitted",
                "grade_display_state": fact.get("grade_display_state") if fact else "pending",
                "has_effective_score": bool(fact and fact["has_effective_score"]),
                "assessment_kind": current.get("assessment_kind"), "assessment_kind_version": current.get("assessment_kind_version") or 0,
                "policy_version": audit.get("ai_policy_version"), "profile_id": (audit.get("execution_metadata") or {}).get("profile_id"),
                "review_required": "review_required" in reasons, "review_reason_codes": review_codes})
            if reasons:
                for reason in reasons:
                    counts[reason] += 1
                issues.append({"assignment_id": aid, "student_id": student_id, "reason_codes": reasons})
    labels = {"missing_score": "来源缺少有效分", "regrading": "来源正在重批（保留原有效分）", "review_required": "来源待复核",
              "group_unreleased": "小组尚未结算揭晓", "legacy_execution": "历史成绩缺少实际模型档位记录", "classification_changed": "评分后任务分类已变更，需核验档位",
              "quality_floor_mismatch": "视觉评分实际档位不满足测验要求", "policy_changed": "评分执行策略版本已变化，需核验"}
    warnings = [f"{labels[key]}：{count} 条。" for key, count in counts.items() if count]
    return {"version": "grade-source-preflight-v1", "counts": counts, "issues": issues, "source_snapshots": snapshots,
            "warnings": warnings, "ready_for_publication": not issues,
            "missing_score_policy": "ordinary_material_zero_fill_with_warning; exam_material_blank",
            "count_semantics": "counts are overlapping assignment/student facts, not additive headcounts"}
