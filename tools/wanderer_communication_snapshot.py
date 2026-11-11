"""Read-only, privacy-preserving evidence of natural AI conversations.

No application imports, model requests or business writes. Only database-side
hashes and operational metadata leave PostgreSQL; no message body or identity.
Speaker turns collapse consecutive messages from the same sender. Identical
plain-text candidates are suspicious only with no intervening incoming message
within ten minutes, and are not by themselves proof of an erroneous send.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
import sys
from zoneinfo import ZoneInfo

AI = "SELECT 'student:' || CAST(student_id AS TEXT) FROM wanderer_agents"
TABLES = frozenset({'wanderer_agents', 'social_messages', 'social_message_media',
    'private_messages', 'private_message_attachments', 'wanderer_message_deliveries',
    'wanderer_social_inbox', 'wanderer_group_observations', 'wanderer_wake_events', 'wanderer_turns',
    'social_group_members'})


def summarize_rows(rows, *, since_epoch):
    """rows: id, anonymous conversation, anonymous sender, epoch, AI, payload hash."""
    conversations, repeated, seen, last_by_sender = {}, [], {}, defaultdict(dict)
    for mid, conversation, sender, stamp, is_ai, signature, *extra in rows:
        mid, stamp = int(mid), float(stamp)
        spoken = bool(extra[0]) if extra else True
        peers = last_by_sender[conversation]
        watermark = max((value for key, value in peers.items() if key != sender), default=0)
        signature_key = (conversation, sender, signature, watermark)
        prior = seen.get(signature_key) if signature and is_ai else None
        if prior and stamp >= since_epoch and 0 <= stamp - prior[1] <= 600:
            repeated.append({'conversation': conversation[:16], 'previous_message_id': prior[0],
                             'message_id': mid, 'seconds_apart': round(stamp-prior[1], 3)})
        if signature and is_ai:
            seen[signature_key] = (mid, stamp)
        peers[sender] = mid
        if stamp < since_epoch or not spoken:
            continue
        item = conversations.setdefault(conversation, {'messages': 0, 'ai_messages': 0,
            'senders': set(), 'ai_senders': set(), 'speaker_turns': 0, 'last_sender': None})
        item['messages'] += 1
        item['ai_messages'] += int(bool(is_ai))
        item['senders'].add(sender)
        if is_ai:
            item['ai_senders'].add(sender)
        if sender != item['last_sender']:
            item['speaker_turns'] += 1
        item['last_sender'] = sender
    relevant = {key: value for key, value in conversations.items() if value['ai_messages']}
    examples = sorted(relevant.items(), key=lambda pair: (-pair[1]['speaker_turns'], pair[0]))[:8]
    return {'conversations': len(relevant), 'messages': sum(x['messages'] for x in relevant.values()),
        'ai_messages': sum(x['ai_messages'] for x in relevant.values()),
        **{f'conversations_with_{n}_alternating_speaker_turns': sum(x['speaker_turns'] >= n and x['ai_messages'] >= 2
           and len(x['senders']) >= 2 for x in relevant.values()) for n in (4, 6)},
        **{f'ai_to_ai_conversations_with_{n}_alternating_speaker_turns': sum(x['speaker_turns'] >= n
           and len(x['ai_senders']) >= 2 for x in relevant.values()) for n in (4, 6)},
        'plain_text_repeat_candidates': len(repeated), 'repeat_examples': repeated[:20],
        'conversation_examples': [{'conversation': key[:16], 'messages': value['messages'],
            'ai_messages': value['ai_messages'], 'participants': len(value['senders']),
            'ai_participants': len(value['ai_senders']), 'speaker_turns': value['speaker_turns']}
            for key, value in examples]}


def bounded_summary(rows, *, since_epoch, max_rows):
    value = summarize_rows(rows[:max_rows],since_epoch=since_epoch)
    value.update(scanned_rows=min(len(rows),max_rows),truncated=len(rows)>max_rows)
    return value


def _live_delivery(alias):
    """Mirror original-row existence, recall and current group membership guards."""
    if alias not in {'older','newer'}:
        raise ValueError('Unrecognized ledger alias')
    return f"""(({alias}.channel='social' AND EXISTS(SELECT 1 FROM social_messages msg
        JOIN wanderer_agents owner ON owner.id={alias}.wanderer_id
        WHERE msg.id={alias}.message_id AND msg.conversation_key={alias}.conversation_key
          AND msg.sender_identity='student:'||CAST(owner.student_id AS TEXT) AND msg.recalled_at IS NULL
          AND (msg.conversation_type<>'group' OR EXISTS(SELECT 1 FROM social_group_members member
            WHERE 'group:'||CAST(member.group_id AS TEXT)=msg.conversation_key
              AND member.member_identity=msg.sender_identity AND member.left_at IS NULL
              AND msg.created_at>=member.joined_at))))
        OR ({alias}.channel='legacy' AND EXISTS(SELECT 1 FROM private_messages msg
          JOIN wanderer_agents owner ON owner.id={alias}.wanderer_id
          WHERE msg.id={alias}.message_id AND msg.conversation_key={alias}.conversation_key
            AND msg.sender_identity='student:'||CAST(owner.student_id AS TEXT))))"""


def collect_snapshot(conn, *, since, max_rows=50000):
    since_local = since.astimezone(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None).isoformat(timespec='seconds')
    lookback = since - timedelta(minutes=10)
    local_start = lookback.astimezone(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None).isoformat(timespec='seconds')
    utc_start = lookback.astimezone(timezone.utc).isoformat(timespec='microseconds')
    channels = {}
    for label, kind in (('native_dm', 'dm'), ('group', 'group'), ('legacy_dm', None)):
        social = kind is not None
        table = 'social_messages' if social else 'private_messages'
        start = utc_start if social else local_start
        body = 'm.body' if social else 'm.content'
        extra = " AND m.conversation_type=%s AND m.recalled_at IS NULL" if social else ''
        media = 'social_message_media' if social else 'private_message_attachments'
        envelope = " || '|' || m.body_format || '|' || COALESCE(CAST(m.reply_to_id AS TEXT),'') || '|' || m.mentions_json" if social else ''
        epoch = "m.created_at::timestamptz" if social else "m.created_at::timestamp AT TIME ZONE 'Asia/Shanghai'"
        spoken = "m.message_type IN ('text','media','nudge')" if social else 'TRUE'
        current_membership = ("(m.conversation_type<>'group' OR EXISTS(SELECT 1 FROM social_group_members gm "
            "WHERE 'group:'||CAST(gm.group_id AS TEXT)=m.conversation_key AND gm.member_identity=m.sender_identity "
            "AND gm.left_at IS NULL AND m.created_at>=gm.joined_at))" if social else 'TRUE')
        rows = conn.execute(f"""SELECT m.id,md5(m.conversation_key),md5(m.sender_identity),
            EXTRACT(EPOCH FROM {epoch}),m.sender_identity IN ({AI}),
            CASE WHEN NOT EXISTS(SELECT 1 FROM {media} a WHERE a.message_id=m.id)
                 AND length(btrim({body}))>0 AND {spoken} AND {current_membership}
                 AND m.sender_identity IN ({AI}) THEN md5(btrim(replace({body},E'\\r\\n',E'\\n')){envelope}) ELSE NULL END,
            {spoken}
            FROM {table} m WHERE m.created_at>=%s {extra}
            AND EXISTS(SELECT 1 FROM {table} a WHERE a.conversation_key=m.conversation_key
              AND a.sender_identity IN ({AI}) AND a.created_at>=%s)
            ORDER BY m.id LIMIT %s""", (start, *([kind] if social else []), start, max_rows+1)).fetchall()
        channels[label] = bounded_summary(rows,since_epoch=since.timestamp(),max_rows=max_rows)
    deliveries = {f'{channel}:{kind}': int(n) for channel, kind, n in conn.execute(
        'SELECT channel,delivery_kind,COUNT(*) FROM wanderer_message_deliveries WHERE created_at>=%s GROUP BY 1,2',
        (since_local,)).fetchall()}
    duplicate_ledger = int(conn.execute(f"""SELECT COUNT(*) FROM wanderer_message_deliveries newer
        WHERE newer.delivery_kind='sent' AND newer.created_at>=%s AND {_live_delivery('newer')} AND EXISTS(
          SELECT 1 FROM wanderer_message_deliveries older WHERE older.delivery_kind='sent'
          AND older.wanderer_id=newer.wanderer_id AND older.life_epoch=newer.life_epoch
          AND older.channel=newer.channel AND older.conversation_key=newer.conversation_key
          AND older.payload_hash=newer.payload_hash AND older.message_id<newer.message_id
          AND {_live_delivery('older')}
          AND (older.chain_id=newer.chain_id OR (older.peer_watermark=newer.peer_watermark
            AND newer.created_at::timestamp-older.created_at::timestamp BETWEEN interval '0 second' AND interval '600 seconds')))
        """, (since_local,)).fetchone()[0])
    backlog = {f'{source}:{state}': int(n) for source, state, n in conn.execute(
        'SELECT source,state,COUNT(*) FROM wanderer_social_inbox WHERE latest_message_id>handled_message_id GROUP BY 1,2').fetchall()}
    failed_unhandled = int(conn.execute("""SELECT COUNT(*) FROM wanderer_social_inbox i
        JOIN wanderer_wake_events e ON e.wanderer_id=i.wanderer_id AND e.source='inbox'
          AND e.event_key=CAST(i.id AS TEXT)||':'||CAST(i.latest_message_id AS TEXT)
        WHERE i.latest_message_id>i.handled_message_id AND e.state='failed'
          AND NOT EXISTS(SELECT 1 FROM wanderer_wake_events r WHERE r.chain_id=e.chain_id
            AND r.source IN ('protocol_repair','model_retry') AND r.state IN ('pending','processing','completed','deferred'))""").fetchone()[0])
    return {'ok': not duplicate_ledger and not any(x['truncated'] for x in channels.values()),
        'since': since.isoformat(), 'channels': channels, 'delivery_receipts_since': deliveries,
        'duplicate_sent_ledger_candidates': duplicate_ledger, 'unhandled_inbox_by_state': backlog,
        'unhandled_failed_inbox_events_without_recovery': failed_unhandled,
        'interpretation': 'speaker_turns_collapse_same_sender; no_new_activity_is_not_observed; repeat_candidates_require_review'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', required=True)
    parser.add_argument('--max-rows', type=int, default=50000)
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since)
    if since.tzinfo is None or not 100 <= args.max_rows <= 100000:
        parser.error('Explicit UTC offset and max-rows between 100 and 100000 required')
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn.startswith(('postgresql://', 'postgres://')):
        parser.error('An already configured PostgreSQL DATABASE_URL is required; no dotenv or fallback')
    import psycopg
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10,
                options='-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=1000') as conn:
            conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            try:
                if conn.execute('SHOW transaction_read_only').fetchone()[0] != 'on':
                    raise RuntimeError('Read-only transaction not established')
                present = {r[0] for r in conn.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()').fetchall()}
                report = ({'ok': False, 'missing_tables': sorted(TABLES-present)} if TABLES-present else
                          collect_snapshot(conn, since=since, max_rows=args.max_rows))
                report.update(read_only=True, generated_at=datetime.now(timezone.utc).isoformat(),
                              private_text_or_identity_returned=False)
            finally:
                conn.execute('ROLLBACK')
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0 if report['ok'] else 1
    except Exception as error:
        print(json.dumps({'ok': False, 'read_only': True, 'error_type': type(error).__name__,
                          'sqlstate': getattr(error, 'sqlstate', None)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
