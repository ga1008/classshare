"""AI-to-AI communication credits, charged by the ordinary message transaction.

One message is one charge, including media and nudges. Group pricing uses the
actual AI audience and never treats a human mention as a free delivery channel.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from typing import Any

SCALE = 1_000_000
DISPLAY_UNIT = 3 * SCALE
RESOURCE = "social_message"


def message_cost(affinity: Any, *, partner_dm: bool = False) -> int:
    affinity = max(Decimal(0), min(Decimal(100), Decimal(str(affinity))))
    base = int((Decimal(SCALE) / (1 + affinity / 100)).to_integral_value(rounding=ROUND_CEILING))
    return base if partner_dm else 3 * base


def _agent(conn: Any, identity: str) -> Any:
    role, _, number = str(identity).partition(":")
    if role != "student" or not number.isdigit():
        return None
    from .account_control_service import table_exists
    if not table_exists(conn,"wanderer_agents"):
        return None  # Ordinary-message installations need no AI world schema or DDL.
    return conn.execute("SELECT id,status FROM wanderer_agents WHERE student_id=?", (int(number),)).fetchone()


def quote_message(conn: Any, sender: str, parsed: dict[str, Any], *, reply_to_id: Any = None) -> dict[str, Any]:
    from .social_conversation_service import participants
    from .wanderer_relationship_service import affinity_states
    from .wanderer_bond_service import is_direct_partner
    actor = _agent(conn, sender)
    if actor is None:
        return {"charged":False,"reason":"human_sender"}
    identities = [identity for identity in participants(conn,parsed) if identity != sender]
    student_ids = [int(identity.split(":",1)[1]) for identity in identities
                   if identity.startswith("student:") and identity.split(":",1)[1].isdigit()]
    recipients = {}
    if student_ids:
        marks = ",".join("?" for _ in student_ids)
        recipients = {f"student:{row['student_id']}":int(row["id"]) for row in conn.execute(
            f"SELECT id,student_id FROM wanderer_agents WHERE student_id IN ({marks}) AND status IN ('active','paused')",
            tuple(student_ids)).fetchall()}
    if not recipients:
        return {"charged":False,"reason":"human_audience"}
    target = None
    if parsed["type"] == "dm":
        target = parsed["peer"]
    elif reply_to_id:
        row = conn.execute("SELECT sender_identity FROM social_messages WHERE id=? AND conversation_key=? "
                           "AND recalled_at IS NULL", (reply_to_id,parsed["key"])).fetchone()
        if row is not None and row["sender_identity"] in recipients:
            target = row["sender_identity"]
    rated = [target] if target in recipients else list(recipients)
    states = affinity_states(conn,int(actor["id"]),rated)
    affinities = [Decimal(str(states[identity]["affinity"])) for identity in rated]
    affinity = sum(affinities) / len(affinities)
    partner = parsed["type"] == "dm" and is_direct_partner(conn,int(actor["id"]),recipients[parsed["peer"]])
    return {"charged":True,"actor_id":int(actor["id"]),"resource":RESOURCE,
            "amount":message_cost(affinity,partner_dm=partner),"affinity":float(affinity),
            "partner_dm":partner,"recipients":list(recipients),"display_unit":DISPLAY_UNIT}


def charge_message(conn: Any, sender: str, parsed: dict[str, Any], *, message_id: int,
                   reply_to_id: Any = None) -> dict[str, Any]:
    """Call after insert, before commit. Rollback removes both message and debit."""
    from .wanderer_resource_service import consume
    from .social_common import SocialError
    quote = quote_message(conn,sender,parsed,reply_to_id=reply_to_id)
    if not quote["charged"]:
        receipt = None
    else:
        receipt = consume(conn,quote["actor_id"],RESOURCE,quote["amount"],f"social-message:{message_id}")
        if not receipt.get("ok"):
            raise SocialError("今日散修交流额度不足，消息尚未发送。",429,retry_after=60)
    gained = _award_delivery(conn,sender,parsed,message_id,reply_to_id=reply_to_id)
    return {**quote,"receipt":receipt,"cultivation_gain":gained}


def _award_delivery(conn: Any, sender: str, parsed: dict, message_id: int, *, reply_to_id=None) -> float:
    actor = _agent(conn,sender)
    if actor is None:
        return 0.0
    from .wanderer_runtime_service import runtime_enabled
    if not runtime_enabled(conn):
        return 0.0 # The compatibility runner retains its original finish-time award.
    message = conn.execute("SELECT body,message_type FROM social_messages WHERE id=?", (message_id,)).fetchone()
    if message is None or message["message_type"] not in {"text","media","nudge"}:
        return 0.0
    peer = parsed.get("peer", "") if parsed["type"] == "dm" else ""
    if parsed["type"] == "group":
        row = None
        if reply_to_id:
            row = conn.execute("SELECT sender_identity FROM social_messages WHERE id=? AND conversation_key=? "
                               "AND recalled_at IS NULL AND sender_identity<>?",(reply_to_id,parsed["key"],sender)).fetchone()
        if row:
            peer = row["sender_identity"]
    return _award_interaction(conn,actor,sender,peer,f"social-message:{message_id}",str(message["body"] or ""),
                              message_id,parsed["key"],"social_message","social_group" if parsed["type"]=="group" else "social_dm",
                              answered=bool(reply_to_id))


def _award_interaction(conn, actor, sender, peer, event_key, body, message_id, target_key, action_kind, target_type,
                       *, answered=False):
    from .wanderer_identity_service import load_wanderer, log_wanderer_action
    from .wanderer_interaction_service import counterpart_context
    from .wanderer_life_core import interaction_gain, talent_value, INTERACTION_DAILY_CULTIVATION_CAP
    from .wanderer_cultivation_service import award_cultivation
    prior = conn.execute("SELECT awarded_units FROM wanderer_cultivation_awards WHERE event_id=? AND wanderer_id=?",
                         (event_key,int(actor["id"]))).fetchone()
    if prior:
        return int(prior["awarded_units"]) / 1_000_000
    w = load_wanderer(conn,int(actor["id"]))
    facts = counterpart_context(conn,peer) if peer else {"real_user":False,"tier":0}
    gain = interaction_gain("social_message",talent_value(w.get("talents"),"eloquence"),
                            real_user=facts["real_user"],target_tier=facts["tier"],
                            chars=len(body),source_tier=w.get("tier",0),answered=bool(answered))
    granted = award_cultivation(conn,int(actor["id"]),gain,"interaction",event_key,
                                daily_cap=INTERACTION_DAILY_CULTIVATION_CAP)
    log_wanderer_action(conn,int(actor["id"]),action_kind,target_type=target_type,
                        target_id=target_key,content_preview=body[:300],outcome="ok",
                        detail={"message_id":message_id,"cultivation_gain":granted,"runtime":"v2"})
    return granted


def charge_private_message(conn: Any, message: dict) -> dict:
    """Legacy private-message entry, same weighted social bucket as normal chat."""
    from .wanderer_resource_service import consume
    from .wanderer_relationship_service import affinity_state
    from .wanderer_bond_service import is_direct_partner
    from .wanderer_runtime_service import runtime_enabled
    from .rate_limit_service import RateLimitExceededError
    sender, peer = str(message["sender_identity"]), str(message["recipient_identity"])
    actor, recipient = _agent(conn,sender), _agent(conn,peer)
    if actor is None:
        return {"charged":False,"reason":"human_sender"}
    charged = recipient is not None and recipient["status"] in {"active","paused"}
    receipt = None
    if charged:
        affinity = affinity_state(conn,int(actor["id"]),peer)["affinity"]
        cost = message_cost(affinity,partner_dm=is_direct_partner(conn,int(actor["id"]),int(recipient["id"])))
        receipt = consume(conn,int(actor["id"]),RESOURCE,cost,f"private-message:{message['id']}")
        if not receipt.get("ok"):
            raise RateLimitExceededError("今日散修交流额度不足，私信尚未发送。",retry_after_seconds=60)
    gain = _award_interaction(conn,actor,sender,peer,f"private-message:{message['id']}",str(message.get("content") or ""),
                              int(message['id']),str(message['conversation_key']),"dm_reply","legacy_dm") if runtime_enabled(conn) else 0.0
    return {"charged":charged,"receipt":receipt,"cultivation_gain":gain}
