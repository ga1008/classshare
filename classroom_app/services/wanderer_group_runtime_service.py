"""Group delivery adapter for the shared awakening runtime; no second AI loop."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..db.schema_wanderer_social import ensure_wanderer_social_schema
from ..time_utils import local_iso
from .wanderer_runtime_service import connection, enqueue_wake_event, runtime_enabled, wake_wanderer


def mark_membership(conn: Any, wanderer_id: int, group_id: int, identity: str) -> None:
    ensure_wanderer_social_schema(conn)
    row = conn.execute("SELECT joined_at FROM social_group_members WHERE group_id=? AND member_identity=? AND left_at IS NULL",
                       (group_id,identity)).fetchone()
    if row is None:
        return
    conn.execute("INSERT INTO wanderer_group_observations(wanderer_id,group_id,membership_stamp,updated_at) "
                 "VALUES(?,?,?,?) ON CONFLICT(wanderer_id,group_id) DO UPDATE SET "
                 "handled_message_id=CASE WHEN wanderer_group_observations.membership_stamp=excluded.membership_stamp "
                 "THEN wanderer_group_observations.handled_message_id ELSE 0 END,"
                 "membership_stamp=excluded.membership_stamp,updated_at=excluded.updated_at",
                 (int(wanderer_id),int(group_id),row["joined_at"],local_iso()))


def recover_group_events(conn: Any, *, limit: int = 100) -> int:
    """Periodic bounded outbox repair from durable membership/messages, never read receipts."""
    from .wanderer_settings_service import is_wanderer_runtime_enabled
    from .wanderer_social_triggers import schedule_coalesced, TASK_KIND_GROUP_WATCH, group_watch_dedupe_key
    if not runtime_enabled(conn) or not is_wanderer_runtime_enabled(conn):
        return 0
    ensure_wanderer_social_schema(conn)
    rows = conn.execute("SELECT a.id AS wanderer_id,m.group_id,m.member_identity,m.joined_at,o.membership_stamp,"
                        "COALESCE(o.handled_message_id,0) AS handled_message_id "
                        "FROM social_group_members m JOIN social_groups g ON g.id=m.group_id "
                        "JOIN wanderer_agents a ON m.member_identity=('student:' || CAST(a.student_id AS TEXT)) "
                        "LEFT JOIN wanderer_group_observations o ON o.wanderer_id=a.id AND o.group_id=m.group_id "
                        "WHERE m.left_at IS NULL AND g.dissolved_at IS NULL AND a.status='active' "
                        "AND (o.membership_stamp IS NULL OR o.membership_stamp<>m.joined_at OR EXISTS "
                        "(SELECT 1 FROM social_messages msg WHERE msg.conversation_key=('group:' || CAST(m.group_id AS TEXT)) "
                        "AND msg.id>COALESCE(o.handled_message_id,0) AND msg.created_at>=m.joined_at "
                        "AND msg.sender_identity<>m.member_identity AND msg.message_type IN ('text','media','nudge') "
                        "AND msg.recalled_at IS NULL)) ORDER BY COALESCE(o.updated_at,''),m.group_id,a.id LIMIT ?",
                        (max(1,min(500,int(limit))),)).fetchall()
    groups = set()
    for row in rows:
        group_id, wid = int(row["group_id"]), int(row["wanderer_id"])
        key = f"group:{group_id}"
        if row["membership_stamp"] != row["joined_at"]:
            enqueue_wake_event(conn,wid,"group_joined",f"{key}:{row['member_identity']}:{row['joined_at']}",
                               {"conversation_key":key,"event_kind":"group_joined","recovered":True,
                                "member_identities":[row["member_identity"]],"joined_at":row["joined_at"]})
            mark_membership(conn,wid,group_id,row["member_identity"])
        groups.add(group_id)
    for group_id in groups:
        schedule_coalesced(conn,task_kind=TASK_KIND_GROUP_WATCH,dedupe_key=group_watch_dedupe_key(group_id),
                           run_at=datetime.now(),payload={"group_id":group_id},title="散修群聊事件恢复",earlier=True)
    return len(groups)


def _snapshot(conn: Any, wanderer: dict, group_id: int) -> dict | None:
    from .social_conversation_service import require_access
    identity = f"student:{wanderer['student_id']}"
    key = f"group:{int(group_id)}"
    require_access(conn,identity,key)
    mark_membership(conn,int(wanderer["id"]),group_id,identity)
    state = conn.execute("SELECT * FROM wanderer_group_observations WHERE wanderer_id=? AND group_id=?",
                         (wanderer["id"],group_id)).fetchone()
    pending = [dict(row) for row in conn.execute("SELECT * FROM social_messages WHERE conversation_key=? AND created_at>=? "
               "AND id>? AND sender_identity<>? AND message_type IN ('text','media','nudge') AND recalled_at IS NULL "
               "ORDER BY id LIMIT 30", (key,state["membership_stamp"],state["handled_message_id"],identity)).fetchall()]
    if not pending:
        return None
    # Keep full messages. Process another bounded batch instead of acknowledging
    # unread text that could not fit in this decision's context.
    selected, used = [], 0
    for row in pending:
        size = len(json.dumps(row,ensure_ascii=False,default=str))
        if selected and used + size > 60_000:
            break
        selected.append(row)
        used += size
    pending = selected
    anchor = int(pending[-1]["id"])
    rows = [dict(row) for row in conn.execute("SELECT * FROM social_messages WHERE conversation_key=? AND created_at>=? "
              "AND id<=? ORDER BY id DESC LIMIT 30", (key,state["membership_stamp"],anchor)).fetchall()][::-1]
    # Own segments cannot evict unseen peer messages from this batch.
    selected = {int(r["id"]):r for r in pending}
    for row in reversed(rows):
        if int(row["id"]) in selected:
            continue
        size = len(json.dumps(row,ensure_ascii=False,default=str))
        if used + size > 90_000:
            break
        selected[int(row["id"])] = row
        used += size
    rows = sorted(selected.values(),key=lambda r:int(r["id"]))
    visible = [{"id":r["id"],"sender_identity":r["sender_identity"],"created_at":r["created_at"],
                "message_type":r["message_type"],"body":"（消息已撤回）" if r["recalled_at"] else r["body"]}
               for r in rows if int(r["id"])<=anchor]
    return {"conversation_key":key,"event_kind":"group_message","snapshot_message_id":anchor,
            "membership_stamp":state["membership_stamp"],"messages":visible,
            "evidence":[{"kind":"social_message","id":r["id"],"ref":f"social_message:{r['id']}"}
                        for r in pending]}


async def run_member_group(wanderer: dict, group_id: int, *, conn_factory=None, ai_caller=None) -> dict:
    from .social_common import SocialError
    with connection(conn_factory) as conn:
        try:
            context = _snapshot(conn,wanderer,group_id)
        except SocialError:
            return {"status":"no_access","ai_calls":0}
        conn.commit()
    if context is None:
        return {"status":"nothing_new","ai_calls":0}
    event_key = f"{context['conversation_key']}:{context['snapshot_message_id']}:{context['membership_stamp']}"
    result = await wake_wanderer(conn_factory,int(wanderer["id"]),source="group_message",event_key=event_key,
                                 context=context,ai_caller=ai_caller)
    with connection(conn_factory) as conn:
        # A crash after a completed decision but before this watermark is safe to replay.
        row = conn.execute("SELECT t.state,EXISTS(SELECT 1 FROM wanderer_wake_events r "
                           "JOIN wanderer_turns rt ON rt.turn_id=r.turn_id AND rt.event_id=r.event_id "
                           "WHERE r.source='protocol_repair' AND r.wanderer_id=e.wanderer_id "
                           "AND r.chain_id=e.chain_id AND r.event_key=e.chain_id AND r.parent_turn_id=e.turn_id "
                           "AND rt.state='completed') AS repaired FROM wanderer_wake_events e "
                           "LEFT JOIN wanderer_turns t ON t.turn_id=e.turn_id "
                           "WHERE e.wanderer_id=? AND e.source='group_message' AND e.event_key=?",
                           (wanderer["id"],event_key)).fetchone()
        considered = any(item.get('status')=='ok' for item in result.get('rounds',[]))
        if row and (row["state"] == "completed" or row['repaired'] or considered):
            conn.execute("UPDATE wanderer_group_observations SET handled_message_id=?,updated_at=? "
                         "WHERE wanderer_id=? AND group_id=? AND membership_stamp=? AND handled_message_id<?",
                         (context["snapshot_message_id"],local_iso(),wanderer["id"],group_id,
                          context["membership_stamp"],context["snapshot_message_id"]))
            conn.execute("UPDATE social_group_members SET last_read_message_id=? WHERE group_id=? AND member_identity=? "
                         "AND joined_at=? AND left_at IS NULL AND last_read_message_id<?",
                         (context["snapshot_message_id"],group_id,f"student:{wanderer['student_id']}",
                          context["membership_stamp"],context["snapshot_message_id"]))
        conn.commit()
    return result


async def run_group_runtime(group_id: int, *, conn_factory=None, ai_caller=None) -> dict:
    from .social_relations import group_member_identities
    from .wanderer_social_triggers import social_wanderers
    from .wanderer_settings_service import is_wanderer_runtime_enabled
    with connection(conn_factory) as conn:
        if not is_wanderer_runtime_enabled(conn):
            return {"status":"disabled","scheduled":[],"passed":[]}
        wanderers = social_wanderers(conn,group_member_identities(conn,group_id))
        initial = [dict(r) for r in conn.execute("SELECT * FROM wanderer_wake_events WHERE state='pending' "
                    "AND source IN ('group_created','group_joined') AND context_json LIKE ? ORDER BY created_at LIMIT 200",
                    (f'%"conversation_key":"group:{group_id}"%',)).fetchall()
                   if json.loads(r["context_json"]).get("conversation_key")==f"group:{group_id}"]
        conn.commit()
    outcomes = []
    for event in initial:
        if int(event["wanderer_id"]) in {int(w["id"]) for w in wanderers.values()}:
            outcomes.append(await wake_wanderer(conn_factory,event["wanderer_id"],source=event["source"],
                            event_key=event["event_key"],context=json.loads(event["context_json"]),ai_caller=ai_caller))
    for identity, wanderer in wanderers.items():
        result = await run_member_group(wanderer,group_id,conn_factory=conn_factory,ai_caller=ai_caller)
        outcomes.append({"identity":identity,**result})
    return {"status":"ok","scheduled":outcomes,"passed":[],"ai_calls":sum(r.get("ai_calls",0) for r in outcomes)}
