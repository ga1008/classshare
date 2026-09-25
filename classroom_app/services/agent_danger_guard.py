"""Agent 危险操作守卫：硬性拦截 + 破坏性/批量操作自检。

政策（2026-09-25 Agent 重构）：
- Agent 以用户本人实时权限执行平台操作，确认执行后不再逐步询问；
- **硬性拦截**（对所有人生效，包括超管）：账号/组织/学期等会造成大面积数据
  丢失的删除、一键清空/重置/抹除类操作、超管授权变更、系统修复与迁移类操作。
  这些操作只能由用户本人在平台页面手工完成，Agent 永远不能执行；
- **破坏性操作自检**：删除、撤销、清空、批量等操作必须携带 ``safety_check``，
  服务端按规则核对：
  * 单条破坏性操作：须是用户明确要求，或数据已确认无效/过期；
  * 批量/大量操作：须是用户明确要求，且数据已确认无效/过期，或用户已在本任务
    中通过选项回答确认过（``user_confirmed``，服务端核对确有已回答的疑问）；
  * 每个任务的破坏性操作次数有上限，超过即熔断。

判定只依赖路由元数据（方法/路径/处理函数名）与服务端记录，模型文本不能改变
结果（AI/Agent 规范 R2）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

# 每个任务允许的破坏性操作次数上限（熔断）。
MAX_DESTRUCTIVE_OPERATIONS_PER_TASK = 40
# 同一任务中第几次破坏性操作起按“大量修改”对待。
BULK_THRESHOLD_PER_TASK = 5
MIN_REASON_CHARS = 6

SAFETY_DATA_STATES = ("invalid", "expired", "user_confirmed", "user_specified")

# 一键清空 / 抹除 / 迁移 / 回滚 / 超管授权类：任何写方法都硬性拦截。
_HARD_BLOCK_TEXT = re.compile(
    r"(wipe|truncate|drop[-_]?(table|database|all)|factory[-_]?reset|reset[-_]?(all|database|db|platform)|"
    r"clear[-_]?all|delete[-_]?all|remove[-_]?all|purge|destroy|cutover|migrate|rollback|"
    r"restore[-_]?(db|database|backup)|super[-_]?admin|repair[-_]?submission|"
    r"password|credential|permission)",
    re.IGNORECASE,
)

# 明确的高危路由（方法, 路径正则, 原因）。
_HARD_BLOCK_ROUTES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("DELETE", re.compile(r"^/api/manage/system/"), "系统级删除（账号、组织、凭据、系统配置）"),
    ("DELETE", re.compile(r"^/api/manage/students/"), "删除学生账号"),
    ("DELETE", re.compile(r"^/api/manage/classes/"), "删除行政班及其名单"),
    ("DELETE", re.compile(r"^/api/manage/semesters/"), "删除学期"),
    ("DELETE", re.compile(r"^/api/(manage/)?(users|teachers|accounts)/"), "删除账号"),
    ("POST", re.compile(r"^/api/manage/system/teachers/[^/]+/(disable|deactivate)"), "停用教师账号"),
)

# 破坏性：删除、撤销、清空、合并、发布、导入覆盖、同步覆盖等。
DESTRUCTIVE_TEXT = re.compile(
    r"(delete|remove|reset|clear|revoke|close[-_]?out|merge|publish|unpublish|archive|disable|"
    r"deactivate|retire|force|bulk|batch|reassign|transfer|regenerate|rotate|import|sync|restore|"
    r"approve|reject|grant|promote|demote|withdraw)",
    re.IGNORECASE,
)
# 天然是“大量”的操作。
BULK_TEXT = re.compile(r"(bulk|batch|clear|reset|import|sync|close[-_]?out|merge|[-_/]all\b)", re.IGNORECASE)


@dataclass(frozen=True)
class DangerAssessment:
    hard_blocked: bool
    reason: str = ""
    destructive: bool = False
    bulk: bool = False


def assess_route(method: str, path: str, handler: str = "") -> DangerAssessment:
    """Pure metadata classification of one platform route."""
    method = str(method or "").upper()
    path = str(path or "")
    if method == "GET":
        return DangerAssessment(False)
    text = f"{path} {handler or ''}"
    for blocked_method, pattern, reason in _HARD_BLOCK_ROUTES:
        if method == blocked_method and pattern.search(path):
            return DangerAssessment(True, reason, True, True)
    if _HARD_BLOCK_TEXT.search(text):
        return DangerAssessment(True, "一键清空/重置/迁移/超管授权等高危操作", True, True)
    destructive = method == "DELETE" or bool(DESTRUCTIVE_TEXT.search(text))
    return DangerAssessment(False, "", destructive, destructive and bool(BULK_TEXT.search(text)))


def hard_block_reason(method: str, path: str, handler: str = "") -> str:
    assessment = assess_route(method, path, handler)
    return assessment.reason if assessment.hard_blocked else ""


def raise_if_hard_blocked(method: str, path: str, handler: str = "") -> None:
    reason = hard_block_reason(method, path, handler)
    if reason:
        raise HTTPException(403, {
            "code": "agent_hard_blocked",
            "message": f"该操作属于硬性拦截的高危操作（{reason}），Agent 不能执行。"
                       "请在最终结果中告诉用户需要本人到平台页面手工操作，不要尝试其它接口绕过。",
        })


# Reviewed transactional write actions (platform_write) never pass through the
# route guard, so identity/organisation-destroying ones are blocked here by name.
HARD_BLOCKED_WRITE_ACTIONS = frozenset({
    "delete_organization_school", "delete_organization_college", "delete_organization_department",
    "deactivate_teacher_account", "deactivate_teacher_membership",
    "grant_teacher_super_admin", "revoke_teacher_super_admin",
})
# Reviewed single-object write actions whose names look destructive but are
# ordinary, user-requested teaching operations (kept explicit so new ones are reviewed).
REVIEWED_DESTRUCTIVE_WRITE_ACTIONS = frozenset({
    "unbind_learning_material", "withdraw_grade_publication", "publish_assignment", "publish_blog_post",
})


def raise_if_write_action_blocked(action: str) -> None:
    if str(action or "") in HARD_BLOCKED_WRITE_ACTIONS:
        raise HTTPException(403, {
            "code": "agent_hard_blocked",
            "message": "该操作属于硬性拦截的高危操作（删除组织、停用账号或变更超管授权），Agent 不能执行。"
                       "请告诉用户需要本人到平台页面手工操作。",
        })


def normalize_safety_check(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(400, "safety_check 必须是对象。")
    allowed = {"user_requested", "data_state", "reason", "target_count", "targets", "question_id"}
    extra = set(value) - allowed
    if extra:
        raise HTTPException(400, f"safety_check 含未知字段：{', '.join(sorted(extra))}")
    user_requested = value.get("user_requested")
    if not isinstance(user_requested, bool):
        raise HTTPException(400, "safety_check.user_requested 必须是布尔值。")
    state = str(value.get("data_state") or "").strip()
    if state not in SAFETY_DATA_STATES:
        raise HTTPException(400, f"safety_check.data_state 必须是 {'/'.join(SAFETY_DATA_STATES)} 之一。")
    reason = str(value.get("reason") or "").strip()
    count = value.get("target_count", 1)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 100000:
        raise HTTPException(400, "safety_check.target_count 必须是 1 以上的整数。")
    targets = str(value.get("targets") or "").strip()[:500]
    question_id = str(value.get("question_id") or "").strip()[:64]
    return {"user_requested": user_requested, "data_state": state, "reason": reason[:500],
            "target_count": count, "targets": targets, "question_id": question_id}


def evaluate_safety_check(assessment: DangerAssessment, safety_check: dict[str, Any] | None, *,
                          prior_destructive_count: int, confirmation_valid: bool) -> dict[str, Any]:
    """Return the accepted self-check record, or raise with guidance for the model."""
    if assessment.hard_blocked:
        raise HTTPException(403, {"code": "agent_hard_blocked",
                                  "message": f"该操作属于硬性拦截的高危操作（{assessment.reason}），Agent 不能执行。"})
    if not assessment.destructive:
        return {}
    if prior_destructive_count >= MAX_DESTRUCTIVE_OPERATIONS_PER_TASK:
        raise HTTPException(429, {
            "code": "agent_destructive_budget_exhausted",
            "message": f"本任务的破坏性操作已达上限（{MAX_DESTRUCTIVE_OPERATIONS_PER_TASK} 次），已熔断。"
                       "请停止继续删除/修改，总结已完成与剩余事项交给用户。",
        })
    if safety_check is None:
        raise HTTPException(428, {
            "code": "agent_safety_check_required",
            "message": "这是破坏性操作。执行前先自问是否合理，然后在 platform_request 中附带 safety_check："
                       "{user_requested: 是否用户明确要求, data_state: invalid|expired|user_confirmed|user_specified, "
                       "reason: 为什么合理, target_count: 影响条数, targets: 目标摘要}。",
        })
    if len(safety_check["reason"]) < MIN_REASON_CHARS:
        raise HTTPException(428, {"code": "agent_safety_reason_too_short",
                                  "message": "safety_check.reason 需要写清楚为什么这次修改/删除是合理的。"})
    state = safety_check["data_state"]
    if state == "user_confirmed" and not confirmation_valid:
        raise HTTPException(409, {
            "code": "agent_confirmation_missing",
            "message": "user_confirmed 必须引用用户最近一次回答的疑问编号（safety_check.question_id）。"
                       "请先用 ask_user 列出这次修改/删除的影响范围请用户选择，再用该疑问编号执行。",
        })
    bulk = (assessment.bulk or safety_check["target_count"] > 1
            or prior_destructive_count + 1 >= BULK_THRESHOLD_PER_TASK)
    data_is_stale = state in ("invalid", "expired")
    if bulk:
        if not safety_check["user_requested"] or state not in ("invalid", "expired", "user_confirmed"):
            raise HTTPException(409, {
                "code": "agent_bulk_change_needs_confirmation",
                "message": "这是大量修改/删除：只有在“确认是用户的要求”且“数据确认无效或过期”时才能直接执行；"
                           "否则请先用 ask_user 向用户列出影响范围与可选方案，得到确认后以 data_state=user_confirmed 执行。",
            })
    elif not (safety_check["user_requested"] or data_is_stale):
        raise HTTPException(409, {
            "code": "agent_destructive_not_requested",
            "message": "该破坏性操作既不是用户明确要求，也未确认数据无效/过期。请先用 ask_user 确认用户意图。",
        })
    return {**safety_check, "bulk": bulk, "accepted": True}
