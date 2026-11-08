"""Explicit public learning projections; internal dictionaries never cross this boundary.

This module does not calculate scores or change persisted snapshots. Historical
scores remain historical facts, while obsolete/private metadata is not exposed.
The same projection serves HTTP, SSR and delegated Agent reads.
"""
from __future__ import annotations

import math
from typing import Any


def fields(names: str) -> dict[str, Any]:
    return dict.fromkeys(names.split(), True)


COMPONENTS = fields("material task interaction consistency")
LEVEL = fields("id key level_key level_name name short_name tier unlock_score theme title certificate_title address_title aura_label description summary color icon")
CERTIFICATE = {**LEVEL, **fields("class_offering_id student_id stage_key certificate_code issued_at revealed_at needs_reveal score course_name student_name class_name teacher_name address_name certificate_url award_url")}
ATTEMPT = fields("id stage_key status assignment_id score passed created_at generated_at submitted_at graded_at completed_at attempt_number error_message")
STAGE = {**LEVEL, **fields("status progress_score progress_percent pass_score last_exam_assignment_id"),
         "certificate": CERTIFICATE, "latest_attempt": ATTEMPT}
MATERIAL_PROGRESS = fields("id material_id session_id accumulated_seconds active_seconds max_scroll_ratio completed mastered mastery_attempts mastered_at mastery_source progress_rule_version first_viewed_at last_viewed_at updated_at")
QUESTION = {**fields("id question_id question text type prompt"), "options": [True]}
MASTERY = {**fields("status available completed mastered attempts source title description pass_score question_count"), "questions": [QUESTION]}
MATERIAL = {**fields("id name title material_id material_path open_url preview_type unit_ratio percent completed mastered mastery_source needs_mastery_check"),
            "progress": MATERIAL_PROGRESS, "mastery_check": MASTERY}
ASSIGNMENT_ITEM = fields("id title kind submitted graded score on_time")
ASSIGNMENTS = {**fields("assignment_count submitted_count graded_count exam_count group_count on_time_count late_count completion_ratio score_ratio punctuality_ratio breadth_ratio task_ratio"),
               "kinds_available": [True], "kinds_attempted": [True], "items": [ASSIGNMENT_ITEM]}
INTERACTIONS = fields("chat_message_count assistant_mention_count ai_question_count peer_help_count peer_help_units peer_help_multiplier private_teacher_count live_participation_count live_available live_response_count poll_ballot_count group_message_count streak_days active_days online_seconds focus_seconds activity_count base_interaction_ratio interaction_ratio consistency_ratio")
METRICS = {**fields("score raw_score growth_exponent weight_version weight_source scoring_policy_version"),
           "weights": COMPONENTS, "components": COMPONENTS,
           "material": {**fields("required_count completed_count mastered_count ratio reading_ratio mastery_ratio depth_ratio"), "items": [MATERIAL]},
           "assignments": ASSIGNMENTS, "interactions": INTERACTIONS}
SIGNAL = fields("key label hint share ratio percent points max_points value_label")
BREAKDOWN = {**fields("key label description weight points ratio percent remaining"), "signals": [SIGNAL]}
GROWTH = {**fields("model_version exponent raw_score score bonus marginal_value phase phase_label note"),
          "next_stage": fields("key unlock_score raw_required raw_gap score_gap")}
EVENT = {**fields("id event_type delta delta_label component component_label created_at"),
         "metadata": {**fields("previous current weight_version"), "weights": COMPONENTS}}
RANK_PERSON = {**fields("name rank score top_percent surpass_percent x is_self is_top is_tianjiao anonymous"), "level": LEVEL}
POSITION = {**fields("total hidden_count self_hidden"), "current": RANK_PERSON, "leader": RANK_PERSON,
            "ranking": [RANK_PERSON], "current_level": LEVEL,
            "mountain": {**fields("min_score max_score"), "points": [fields("x score rank is_self is_top")]}}
TREND = {**fields("weeks has_enough_data total_delta total_delta_label weekly_delta weekly_delta_label"),
         "points": [{**fields("week_start week_end score progress_percent level_key"), "components": COMPONENTS}],
         "sparkline": fields("points area_points view_box min max")}
STATE = {**fields("score progress_percent snapshot_status calculated_at"),
         "current_level": CERTIFICATE, "next_stage": STAGE, "eligible_stage": STAGE,
         "stages": [STAGE], "certificates": [CERTIFICATE], "latest_certificate": CERTIFICATE,
         "latest_unrevealed_certificate": CERTIFICATE, "metrics": METRICS,
         "rules": {**fields("weight_version weight_source pass_score fairness_note"), "score_weights": [fields("key label weight")]},
         "breakdown": [BREAKDOWN], "growth": GROWTH, "stage_effort": [fields("key tier unlock_score raw_required raw_gap")],
         "score_opportunities": [fields("type component title summary estimated_delta action_label action_url")],
         "recent_score_events": [EVENT], "growth_trend": TREND, "class_position": POSITION,
         "display": fields("hidden")}
ATTENDANCE_ADVICE = {
    "course": fields("course_name class_name semester_name synced_session_count latest_synced_at"),
    "student_attendance": fields("attendance_rate checked total absent late_or_early leave_count abnormal risk_level latest_status_label latest_checkin_time"),
    "recent_sessions": [fields("label checkin_time status_label")],
    "course_comparisons": [fields("course_name class_name rate total is_current delta_from_current")],
    "fallback_cards": [fields("key title fallback_text tone")],
}


def _project(value: Any, schema: Any) -> Any:
    if value is None:
        return None
    if schema is True:
        if isinstance(value, str):
            from .psych_profile_service import sanitize_hidden_profile_leaks
            return sanitize_hidden_profile_leaks(value)
        if isinstance(value, (bool, int)):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
        return None
    if isinstance(schema, list):
        return [_project(item, schema[0]) for item in value[:256]] if isinstance(value, list) else []
    if isinstance(schema, dict):
        return {key: _project(value[key], child) for key, child in schema.items() if key in value} if isinstance(value, dict) else {}
    return None


def public_student_learning_state(state: dict[str, Any]) -> dict[str, Any]:
    """Keep public facts/legacy scores, reject unknown keys at every nesting level."""
    return _project(state, STATE)


def public_cultivation_score_event(event: dict[str, Any]) -> dict[str, Any]:
    return _project(event, EVENT)


def public_student_learning_stage(stage: dict[str, Any] | None) -> dict[str, Any] | None:
    return _project(stage, STAGE)


def public_student_certificate(certificate: dict[str, Any] | None) -> dict[str, Any] | None:
    return _project(certificate, CERTIFICATE)


def public_student_attendance_advice_context(context: dict[str, Any]) -> dict[str, Any]:
    return _project(context, ATTENDANCE_ADVICE)


def public_student_legacy_tool_result(value: Any) -> Any:
    """Read-time defense for old, owner-authorized receipts, not a new DTO.

    New domain responses still require their explicit allowlist above. This
    protects persisted pre-boundary tool observations without deleting audits.
    """
    private_names = ("interaction_quality", "profile_summary", "mental_state", "personality_traits",
                     "hidden_premise", "psych_profile", "behavior_profile", "interest_hypothesis",
                     "hidden_profile", "raw_payload", "context_prompt", "system_prompt")
    if isinstance(value, dict):
        return {key: public_student_legacy_tool_result(item) for key, item in value.items()
                if isinstance(key, str) and not any(name in key.lower() for name in private_names)}
    if isinstance(value, list):
        return [public_student_legacy_tool_result(item) for item in value]
    if isinstance(value, str):
        if any(name in value.lower() for name in private_names) or any(
                marker in value for marker in ("侧写", "心理画像", "隐藏画像", "后台画像", "画像摘要", "全量画像")):
            return "部分历史内容已按当前可见范围省略。"
    return value
