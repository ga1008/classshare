"""Bounded AI-only rollout admission; existing manual workflows stay available.

Configuration is process-local deployment configuration, not a hot feature-flag
store. Operators must deploy the same settings to application and worker hosts.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .. import config

MESSAGE = "AI 增强正在分批开放。基础职业探索、测评、手工简历编辑发布和已有版本仍可使用。"
MAX_CONFIG_BYTES = 32768
MAX_STUDENTS = 500
MAX_MAJORS = 100


class CareerRolloutLimited(ValueError):
    def __init__(self):
        super().__init__(MESSAGE)
        self.detail = {"code": "rollout_limited", "message": MESSAGE, "retryable": False}


@dataclass(frozen=True)
class RolloutPolicy:
    mode: str
    valid: bool
    student_ids: frozenset[int]
    major_scopes: frozenset[tuple[str, str]]
    revision: str

    def allows(self, context: dict[str, Any] | None, *, system: bool = False) -> bool:
        if not self.valid:
            return False
        if self.mode == "all":
            return True
        if not context:
            return False
        scope = (str(context.get("school_code") or ""), str(context.get("major_key") or ""))
        student_id = context.get("student_id")
        return scope in self.major_scopes or (not system and type(student_id) is int and student_id in self.student_ids)


@lru_cache(maxsize=8)
def parse_policy(mode: str, students: str, majors: str) -> RolloutPolicy:
    inputs = (mode, students, majors)
    bounded = all(isinstance(value, str) and len(value.encode("utf-8")) <= MAX_CONFIG_BYTES for value in inputs)
    digest = hashlib.sha256("\x00".join(value[:MAX_CONFIG_BYTES] if isinstance(value, str) else "invalid" for value in inputs).encode()).hexdigest()[:16]
    empty = RolloutPolicy("allowlist", False, frozenset(), frozenset(), digest)
    if not bounded:
        return empty
    mode = mode.strip().lower()
    if mode == "all":
        return RolloutPolicy(mode, True, frozenset(), frozenset(), digest)
    if mode != "allowlist":
        return empty
    try:
        parts = students.split(",") if students.strip() else []
        if len(parts) > MAX_STUDENTS or any(not part.strip().isascii() or not part.strip().isdigit() or len(part.strip()) > 19 for part in parts):
            return empty
        student_ids = frozenset(int(part.strip()) for part in parts)
        if any(not 0 < sid <= 2**63-1 for sid in student_ids):
            return empty
        def strict_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate rollout configuration key")
                result[key] = value
            return result
        rows = json.loads(majors or "[]", object_pairs_hook=strict_object)
        if not isinstance(rows, list) or len(rows) > MAX_MAJORS:
            return empty
        scopes = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"school_code", "major_key"}:
                return empty
            school, major = row["school_code"], row["major_key"]
            if (not isinstance(school, str) or not isinstance(major, str) or
                    not 0 < len(school.strip()) <= 80 or not 0 < len(major.strip()) <= 160 or major.strip() == "unknown"):
                return empty
            # Keys are the existing school-scoped canonical mapping output.
            # We do not guess aliases or match substrings from operator input.
            scopes.add((school.strip(), major.strip()))
        return RolloutPolicy(mode, True, student_ids, frozenset(scopes), digest)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return empty


def current_policy() -> RolloutPolicy:
    return parse_policy(config.CAREER_AI_ROLLOUT_MODE, config.CAREER_AI_ROLLOUT_STUDENT_IDS, config.CAREER_AI_ROLLOUT_MAJORS)


def _student_context(conn, student_id):
    if type(student_id) is not int or student_id <= 0:
        return None
    from .career_path_service import resolve_student_context
    return resolve_student_context(conn, student_id)


def ai_availability(conn=None, student_id=None, *, context=None, system=False):
    policy = current_policy()
    if context is None and policy.valid and policy.mode != "all" and (policy.student_ids or policy.major_scopes) and conn is not None and student_id is not None:
        context = _student_context(conn, student_id)
    allowed = policy.allows(context, system=system)
    return {"allowed": allowed, "code": "" if allowed else "rollout_limited",
            "message": "" if allowed else MESSAGE, "retryable": False, "policy_revision": policy.revision}


def require_student_ai(conn, student_id):
    if not ai_availability(conn, student_id)["allowed"]:
        raise CareerRolloutLimited()


def require_ai_job_admission(conn, *, task_type, lane, student_id, payload, requester_student_id=None):
    if lane != "ai":
        return
    if task_type != "career_major_network_generate":
        require_student_ai(conn, student_id)
        return
    if student_id is not None:
        raise CareerRolloutLimited()
    # Shared ownership is never an admission exemption. Resolve the immutable
    # row scope, not a caller-supplied school/major or an owner_role string.
    network_id = payload.get("network_id")
    if type(network_id) is not int or network_id <= 0:
        raise CareerRolloutLimited()
    row = conn.execute("SELECT school_code,major_key FROM career_major_networks WHERE id=?", (network_id,)).fetchone()
    if not row or (row["school_code"], row["major_key"]) != (payload.get("school_code"), payload.get("major_key")):
        raise CareerRolloutLimited()
    scope = {"school_code": row["school_code"], "major_key": row["major_key"]}
    if requester_student_id is None:
        allowed = current_policy().allows(scope, system=True)
    else:
        context = _student_context(conn, requester_student_id)
        allowed = bool(context and (context["school_code"], context["major_key"]) == (scope["school_code"], scope["major_key"]) and current_policy().allows(context))
    if not allowed:
        raise CareerRolloutLimited()


def apply_state_availability(context, state):
    availability = ai_availability(context=context)
    state["ai_availability"] = availability
    if not availability["allowed"]:
        for task in state.get("tasks", {}).values():
            task["can_retry"] = False
            task["admission_code"] = "rollout_limited"
            # Previously accepted work may complete; its real status and the
            # student's cancellation capability remain intact.
            if not task.get("task_type") and task.get("status") not in {"ready", "succeeded"}:
                task.update(status="rollout_limited", phase_label="AI 增强分批开放", error_code="rollout_limited", message=MESSAGE)
        state["network_status"] = state.get("tasks", {}).get("network", {}).get("status", state.get("network_status"))
        state["poll_after_ms"] = 8000 if any(task.get("status") in {"queued", "running", "retry_wait", "result_ready"} for task in state.get("tasks", {}).values()) else 0
    return state
