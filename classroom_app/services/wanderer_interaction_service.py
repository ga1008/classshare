"""Who a wanderer is talking to, and what that interaction is worth.

Real users are the richest cultivation channel: a reply's value grows with the
real user's tier and its own length, and is capped at five times a normal
wanderer-to-wanderer action. The helper returns the concrete facts so callers
can record both the gain and the reason in the action trace.
"""
from __future__ import annotations

from typing import Any, Optional

from .social_identity_service import load_people
from .social_scope_service import student_global_tier
from .wanderer_life_service import award_interaction_cultivation


def counterpart_context(conn: Any, identity: Any) -> dict[str, Any]:
    person = (load_people(conn, [identity]) or {}).get(str(identity)) or {}
    if not person:
        return {"real_user": False, "tier": 0, "name": ""}
    if person.get("is_wanderer"):
        return {"real_user": False, "tier": 0, "name": str(person.get("name") or "")}
    role = str(person.get("role") or "")
    if role == "student":
        try:
            tier = student_global_tier(conn, int(str(identity).split(":")[1]))
        except (ValueError, IndexError):
            tier = 0
    else:
        # Teachers have no 境界 ladder; treat them as the highest real audience.
        tier = 10
    return {"real_user": True, "tier": int(tier), "name": str(person.get("name") or "")}


def award_reply_cultivation(conn: Any, wanderer: dict[str, Any], identity: Any, *,
                            kind: str = "dm_reply", chars: Any = 0) -> dict[str, Any]:
    """Award one reply and return ``{gained, real_user, tier, name}`` for the trace."""
    facts = counterpart_context(conn, identity)
    item = {"kind": kind, "real_user": bool(facts["real_user"]),
            "target_tier": int(facts.get("tier") or 0), "chars": chars}
    gained = award_interaction_cultivation(conn, wanderer, [item])
    return {"gained": round(float(gained or 0), 2), **facts}


def real_user_tier(conn: Any, identity: Any) -> Optional[int]:
    facts = counterpart_context(conn, identity)
    return int(facts["tier"]) if facts["real_user"] else None
