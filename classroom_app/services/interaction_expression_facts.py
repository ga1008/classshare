"""Read course-attributed participation and expression from the business records.

Counts describe observed works, not message quality. Expression is a subset of
participation. Attendance, telemetry and votes are deliberately not joined to
these records. The caller owns roster authorization and the separate opportunity
denominator. No text, participant identities or event times leave this module.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import sqlite3
from typing import Any, Iterable

from ..db.connection import get_configured_db_engine
from ..time_utils import to_local_datetime
from .account_control_service import table_exists


# Each query returns author, work identifier, date, text, and classification.
# Multiple turns of a conversation or group on one day are one observation.
# Replies to the same target, questions in one activity and reviews of different
# teammates on one task are likewise one work, regardless of request count.
_DISCUSSION = """
    WITH human_students AS (
        SELECT CAST(s.id AS TEXT) AS actor_id FROM students s
        WHERE LOWER(COALESCE(s.control_mode,'human'))<>'ai'
          AND LOWER(COALESCE(s.enrollment_status,'active'))<>'wanderer' {agent_guard}
    )
    SELECT m.user_id AS author, q.id AS work_id, m.logged_at AS stamp, m.message AS body,
           CASE WHEN peer.actor_id IS NOT NULL OR teacher.id IS NOT NULL
                THEN 'discussion_reply' ELSE 'discussion' END AS kind
    FROM chat_logs m JOIN human_students sender ON sender.actor_id=m.user_id
    LEFT JOIN chat_logs q
      ON q.id=m.quote_message_id AND q.class_offering_id=m.class_offering_id
     AND q.message_type IN ('text','rich')
     AND NOT (q.user_role=m.user_role AND q.user_id=m.user_id)
    LEFT JOIN human_students peer ON q.user_role='student' AND peer.actor_id=q.user_id
    LEFT JOIN teachers teacher ON q.user_role='teacher' AND CAST(teacher.id AS TEXT)=q.user_id
    WHERE m.class_offering_id=? AND m.user_role='student' AND m.user_id IN ({ids})
      AND m.message_type IN ('text','rich')"""
_GROUP = """
    WITH relevant_groups AS (
        SELECT DISTINCT g.id FROM study_groups g JOIN group_chat_messages own ON own.group_id=g.id
        WHERE g.class_offering_id=? AND own.sender_role='student' AND own.sender_user_pk IN ({ids})
    )
    SELECT m.sender_user_pk AS author, g.id AS work_id, m.created_at AS stamp, m.content AS body,
           g.assignment_id, g.updated_at AS group_updated_at
    FROM relevant_groups rg JOIN study_groups g ON g.id=rg.id JOIN group_chat_messages m ON m.group_id=g.id
    JOIN students s ON s.id=m.sender_user_pk
    WHERE m.sender_role='student' AND LOWER(COALESCE(s.enrollment_status,'active'))<>'wanderer'
      AND LOWER(COALESCE(s.control_mode,'human'))<>'ai' {agent_guard}
      AND m.message_type='text' AND m.recalled_at IS NULL"""
_QNA = """
    SELECT q.student_id AS author, q.activity_id AS work_id, q.created_at AS stamp,
           q.question_text AS body, 'qna' AS kind
    FROM classroom_live_questions q JOIN classroom_live_activities a
      ON a.id=q.activity_id AND a.class_offering_id=q.class_offering_id
    WHERE q.class_offering_id=? AND q.student_id IN ({ids}) AND a.kind='qna'
      AND q.status IN ('open','addressed') {privacy}"""
_QUIZ = """
    SELECT r.student_id AS author, r.activity_id AS work_id, r.created_at AS stamp,
           r.response_text AS body, 'quiz_response' AS kind
    FROM classroom_live_responses r JOIN classroom_live_activities a ON a.id=r.activity_id
    WHERE a.class_offering_id=? AND r.student_id IN ({ids}) AND a.kind='quiz' {privacy}"""
_REVIEW = """
    SELECT p.reviewer_student_id AS author, p.group_id AS work_id, p.created_at AS stamp,
           p.comment AS body, 'peer_review' AS kind, p.assignment_id AS assignment_id
    FROM peer_reviews p JOIN study_groups g ON g.id=p.group_id AND g.class_offering_id=p.class_offering_id
    WHERE p.class_offering_id=? AND p.reviewer_student_id IN ({ids})
      AND p.reviewer_student_id<>p.reviewee_student_id AND p.status='submitted'
      AND p.responsibility_score BETWEEN 1 AND 5 AND p.collaboration_score BETWEEN 1 AND 5
      AND p.quality_score BETWEEN 1 AND 5 AND TRIM(p.comment)<>''"""
_LEGACY_AI = """
    SELECT s.user_pk AS author, s.id AS work_id, m.timestamp AS stamp, m.message AS body,
           'legacy_ai' AS kind
    FROM ai_chat_sessions s JOIN ai_chat_messages m ON m.session_id=s.id
    WHERE s.class_offering_id=? AND s.user_role='student' AND s.user_pk IN ({ids}) AND m.role='user'"""
_WORKSPACE_AI = """
    SELECT q.student_id AS author, m.session_id AS work_id, m.created_at AS stamp, m.message AS body,
           'workspace_ai' AS kind
    FROM ai_workspace_course_questions q JOIN ai_workspace_messages m ON m.id=q.message_id
    JOIN ai_workspace_sessions s ON s.id=m.session_id AND s.user_role='student' AND s.user_pk=q.student_id
    WHERE q.class_offering_id=? AND q.student_id IN ({ids}) AND m.role='user'
      AND SUBSTR(m.request_id,1,7)<>'legacy:'"""

_GENERATED_AI_PROMPTS = {
    '请结合我上传的图片内容帮我分析或回答。',
    '请结合我上传的附件内容帮我分析或回答。',
    '请根据当前附件回答，并说明图片中的关键内容。',
}
_CHUNK_SIZE = 250
_SOURCES = ('discussion', 'discussion_reply', 'group_discussion', 'group_exchange', 'group_task_discussion',
            'qna', 'quiz_response', 'peer_review', 'legacy_ai', 'workspace_ai')


def _day(stamp: Any) -> str:
    local = to_local_datetime(stamp)
    return local.date().isoformat() if local else ''


def _has_text(body: str) -> bool:
    return any(c.isalnum() for c in body.replace('@助教', ''))


def _group_works(cursor, student_ids: list[int]):
    """Only classify an unbound group as an exchange when two human authors wrote that day."""
    requested = set(student_ids)
    authors: dict[tuple, set[int]] = {}
    own = {}
    for row in cursor:
        body = str(row['body'] or '').strip()
        if not _has_text(body):
            continue
        day = _day(row['stamp'])
        group_id = row['work_id']
        author = int(row['author'])
        pair = authors.setdefault((group_id, day), set())
        if day and len(pair) < 2:
            pair.add(author)
        if author in requested:
            # A group's current assignment cannot establish the task context
            # of a message predating its last change: groups may be rebound.
            last_change = to_local_datetime(row['group_updated_at'])
            message_time = to_local_datetime(row['stamp'])
            with_task = (bool(str(row['assignment_id'] or '').strip()) and last_change is not None
                         and message_time is not None and last_change <= message_time)
            key = (author, group_id, day)
            own[key] = own.get(key, False) or with_task
    for (author, group_id, day), with_task in own.items():
        kind = 'group_task_discussion' if with_task else 'group_discussion'
        if not with_task and len(authors[(group_id, day)]) >= 2:
            kind = 'group_exchange'
        yield author, (kind, group_id, day), kind, day, kind != 'group_discussion'


@dataclass
class _StudentFacts:
    # The date is retained only to count distinct days. A correction cannot
    # create an additional work if it changes the body or its update time.
    works: dict[tuple, tuple[str, str, bool]] = field(default_factory=dict)
    ai_messages: Counter = field(default_factory=Counter)

    def observe(self, key, kind: str, day: str, expression: bool):
        previous = self.works.get(key)
        first_day = min((d for d in (day, previous[1] if previous else '') if d), default='')
        self.works[key] = (kind, first_day, expression or bool(previous and previous[2]))

    def add(self, row) -> None:
        kind, work_id, day = row['kind'], row['work_id'], _day(row['stamp'])
        if kind == 'discussion':
            work_id = None
        body = str(row['body'] or '').strip()
        if kind in {'legacy_ai', 'workspace_ai'}:
            # Both joins identify exactly one persisted user message. A legacy
            # import, an assistant reply or telemetry never reaches this point.
            self.ai_messages[kind] += 1
        authored_text = _has_text(body)
        if not authored_text and kind != 'quiz_response':
            return
        is_expression = kind != 'discussion'
        if kind == 'quiz_response':
            is_expression = authored_text
        elif kind in {'legacy_ai', 'workspace_ai'} and body in _GENERATED_AI_PROMPTS:
            is_expression = False

        if kind in {'discussion', 'legacy_ai', 'workspace_ai'}:
            # Undated legacy rows stay together; they cannot fabricate distinct days.
            key = (kind, work_id, day)
        elif kind == 'peer_review':
            key = (kind, work_id, row['assignment_id'])
        else:
            key = (kind, work_id)
        self.observe(key, kind, day, is_expression)

    def public_counts(self, unavailable: list[str], sources: tuple[str, ...]) -> dict:
        counts = Counter(kind for kind, _, _ in self.works.values())
        expressed = [(kind, day) for kind, day, expression in self.works.values() if expression]
        expressed_counts = Counter(kind for kind, _ in expressed)
        return {
            'participation_count': len(self.works),
            'expression_count': len(expressed),
            'expression_days': len({day for _, day in expressed if day}),
            'ai_message_count': sum(self.ai_messages.values()),
            'legacy_ai_message_count': self.ai_messages['legacy_ai'],
            'workspace_ai_message_count': self.ai_messages['workspace_ai'],
            'source_counts': {key: counts[key] for key in sources},
            'expression_source_counts': {key: expressed_counts[key] for key in sources},
            'unavailable_sources': list(unavailable),
        }


def _missing_schema(error: Exception) -> bool:
    if isinstance(error, sqlite3.OperationalError):
        return str(error).lower().startswith(('no such table:', 'no such column:'))
    return getattr(error, 'sqlstate', None) in {'42P01', '42703'}


def load_expression_facts(conn: Any, offering_id: int, student_ids: Iterable[int], *,
                          include_anonymous: bool = False, include_private: bool = False) -> dict[int, dict]:
    """Load one teaching offer in bounded roster batches with no database writes.

    Access is restricted to the author and authorized teaching staff: both
    can see course-attributed AI counts. Anonymous activities and individual
    peer reviews require a separate, explicit caller policy.
    Missing historical tables/columns are reported per source; other failures
    propagate for retry instead of converting an unavailable source into zero.
    """
    students = {int(sid): _StudentFacts() for sid in student_ids if int(sid) > 0}
    if not students:
        return {}
    queries = [
        ('discussion', _DISCUSSION, ''),
        ('group_discussion', _GROUP, ''),
        ('qna', _QNA, '' if include_anonymous else 'AND q.is_anonymous=0'),
        ('quiz_response', _QUIZ, '' if include_anonymous else 'AND r.is_anonymous=0'),
        ('legacy_ai', _LEGACY_AI, ''),
        ('workspace_ai', _WORKSPACE_AI, ''),
    ]
    if include_private:
        queries.append(('peer_review', _REVIEW, ''))
    sources = tuple(key for key in _SOURCES if include_private or key != 'peer_review')
    ids = list(students)
    unavailable = []
    postgres = get_configured_db_engine() == 'postgres'
    agent_guard = ('AND NOT EXISTS (SELECT 1 FROM wanderer_agents w WHERE w.student_id=s.id)'
                   if table_exists(conn, 'wanderer_agents') else '')
    for source, template, privacy in queries:
        # One savepoint per source, not one per student. PostgreSQL requires a
        # rollback after an undefined column/table before other reads may proceed.
        if postgres:
            conn.execute('SAVEPOINT expression_source')
        try:
            for start in range(0, len(ids), _CHUNK_SIZE):
                batch = ids[start:start + _CHUNK_SIZE]
                query = template.format(ids=','.join('?' for _ in batch), privacy=privacy, agent_guard=agent_guard)
                authors = [str(sid) for sid in batch] if source == 'discussion' else batch
                cursor = conn.execute(query, (int(offering_id), *authors))
                try:
                    if source == 'group_discussion':
                        for author, key, kind, day, expression in _group_works(cursor, batch):
                            students[author].observe(key, kind, day, expression)
                    else:
                        for row in cursor:
                            students[int(row['author'])].add(row)
                finally:
                    cursor.close()
        except Exception as error:
            if postgres:
                conn.execute('ROLLBACK TO SAVEPOINT expression_source')
            if not _missing_schema(error):
                raise
            unavailable.append(source)
        finally:
            if postgres:
                conn.execute('RELEASE SAVEPOINT expression_source')
    return {sid: facts.public_counts(unavailable, sources) for sid, facts in students.items()}
