-- PostgreSQL 16 / psql. One JSON object per offering, including empty offerings.
-- psql -XAtq -v ON_ERROR_STOP=1 [-v offering_id=7] -f tools/interaction_expression_audit.sql
-- Run using an authorized read-only database role. No endpoint, DDL, student IDs,
-- message bodies, private projection or individual events are emitted or needed.
-- Live AI rows and published snapshots can have different cutoffs; a difference
-- identifies a reconciliation candidate, not the cause or a scoring correction.
-- Old-axis rates use numeric ratios only. Opportunity counts use student-activity
-- pairs. Channel reach is among published rows with a channel breakdown; an
-- unobserved denominator yields null even if the classroom already has members.
\set ON_ERROR_STOP on
\if :{?offering_id}
\else
    \set offering_id ''
\endif
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '20s';
SELECT to_regclass('interaction_expression_snapshots') IS NOT NULL
       AND to_regclass('interaction_expression_refresh_state') IS NOT NULL AS has_projection,
       to_regclass('ai_workspace_course_questions') IS NOT NULL AS has_course_links,
       to_regclass('wanderer_agents') IS NOT NULL AS has_agents
\gset

WITH selected AS (
    SELECT id, class_id FROM class_offerings
    WHERE NULLIF(:'offering_id', '') IS NULL OR id = NULLIF(:'offering_id', '')::bigint
), classes AS (
    SELECT id AS offering, class_id FROM selected
    UNION
    SELECT o.id, l.class_id FROM selected o JOIN class_offering_class_links l ON l.offering_id = o.id
), roster AS (
    SELECT c.offering, s.id AS student
    FROM classes c JOIN students s ON s.class_id = c.class_id
    WHERE COALESCE(s.enrollment_status, 'active') = 'active'
      AND lower(COALESCE(s.control_mode, 'human')) <> 'ai'
\if :has_agents
      AND NOT EXISTS (SELECT 1 FROM wanderer_agents w WHERE w.student_id = s.id)
\endif
), legacy_ai AS (
    -- A student course session plus one persisted user message is one raw message.
    SELECT r.offering, r.student, count(*) AS n
    FROM roster r JOIN ai_chat_sessions s ON s.class_offering_id = r.offering AND s.user_pk = r.student
    JOIN ai_chat_messages m ON m.session_id = s.id
    WHERE s.user_role = 'student' AND m.role = 'user'
    GROUP BY r.offering, r.student
), workspace_ai(offering, student, n) AS (
\if :has_course_links
    SELECT r.offering, r.student, count(*)
    FROM roster r JOIN ai_workspace_course_questions q ON q.class_offering_id = r.offering AND q.student_id = r.student
    JOIN ai_workspace_messages m ON m.id = q.message_id
    JOIN ai_workspace_sessions s ON s.id = m.session_id AND s.user_pk = r.student
    WHERE s.user_role = 'student' AND m.role = 'user' AND left(m.request_id, 7) <> 'legacy:'
    GROUP BY r.offering, r.student
\else
    SELECT NULL::bigint, NULL::bigint, NULL::bigint WHERE false
\endif
), publications(offering, calculated_at) AS (
\if :has_projection
    SELECT o.id, NULLIF(v.calculated_at, '') FROM selected o
    JOIN interaction_expression_refresh_state v ON v.class_offering_id = o.id
    WHERE v.model_version = 'course-evidence-v1'
\else
    SELECT NULL::bigint, NULL::text WHERE false
\endif
), published(offering, student, evidence) AS MATERIALIZED (
\if :has_projection
    SELECT r.offering, r.student, CASE WHEN x.teacher_json IS JSON OBJECT THEN x.teacher_json::jsonb END
    FROM roster r JOIN interaction_expression_snapshots x ON x.class_offering_id = r.offering AND x.student_id = r.student
    WHERE x.model_version = 'course-evidence-v1'
\else
    SELECT NULL::bigint, NULL::bigint, NULL::jsonb WHERE false
\endif
), old AS MATERIALIZED (
    SELECT r.offering, r.student, CASE WHEN p.metrics_json IS JSON OBJECT
        THEN p.metrics_json::jsonb -> 'interactions' END AS interactions
    FROM roster r JOIN learning_progress_snapshots p ON p.class_offering_id = r.offering AND p.student_id = r.student
), measures AS (
    SELECT o.id AS offering, r.student, v.calculated_at, p.student IS NOT NULL AS old_present,
           CASE WHEN jsonb_typeof(p.interactions -> 'interaction_ratio') = 'number'
                THEN 100 * least(1, greatest(0, (p.interactions ->> 'interaction_ratio')::numeric)) END AS old_axis,
           CASE WHEN jsonb_typeof(p.interactions -> 'ai_question_count') = 'number'
                THEN (p.interactions ->> 'ai_question_count')::numeric END AS old_ai,
           COALESCE(l.n, 0) AS legacy_messages,
           CASE WHEN :'has_course_links'::boolean THEN COALESCE(w.n, 0) END AS workspace_messages,
           x.evidence -> 'facts' AS facts, x.evidence -> 'opportunities' AS opp
    FROM selected o LEFT JOIN roster r ON r.offering = o.id
    LEFT JOIN publications v ON v.offering = o.id
    LEFT JOIN old p ON p.offering = r.offering AND p.student = r.student
    LEFT JOIN legacy_ai l ON l.offering = r.offering AND l.student = r.student
    LEFT JOIN workspace_ai w ON w.offering = r.offering AND w.student = r.student
    LEFT JOIN published x ON x.offering = r.offering AND x.student = r.student
), student_measures AS (
    -- Deduplication, recall, real addressees, anonymity and historic eligibility
    -- are resolved by the actual publisher. Read only its teacher-visible scalars.
    SELECT *, (facts ->> 'expression_count')::bigint AS expressions,
           (facts ->> 'participation_count')::bigint AS participation,
           (facts #>> '{expression_source_counts,group_exchange}')::bigint
             + (facts #>> '{expression_source_counts,group_task_discussion}')::bigint AS group_works,
           (facts #>> '{expression_source_counts,discussion_reply}')::bigint AS reply_works,
           (opp ->> 'expression_offered')::bigint AS expression_offered,
           (opp ->> 'offered')::bigint AS closed_offered, (opp ->> 'participated')::bigint AS closed_joined,
           (opp ->> 'open')::bigint AS open_offered, (opp ->> 'open_participated')::bigint AS open_joined
    FROM measures
), classified AS (
    SELECT *, CASE
        WHEN student IS NULL THEN NULL
        WHEN expressions IS NULL OR opp ->> 'coverage_status' IS NULL THEN 'pending'
        WHEN expressions > 0 THEN 'expressed'
        WHEN expression_offered > 0 THEN 'offered_without_expression'
        WHEN opp ->> 'coverage_status' <> 'complete' THEN 'limited_history'
        WHEN participation > 0 OR closed_joined > 0 OR open_joined > 0 THEN 'participation_only'
        ELSE 'no_assessable_opportunity'
    END AS status
    FROM student_measures
)
SELECT json_build_object(
    'offering', offering,
    'active_humans', count(student),
    'projection', json_build_object(
        'model', 'course-evidence-v1', 'last_published_at', calculated_at,
        'state', CASE WHEN calculated_at IS NULL THEN 'first_pending'
                      WHEN count(*) FILTER (WHERE status = 'pending') > 0 THEN 'partial_roster'
                      ELSE 'published' END,
        'published_students', count(student) FILTER (WHERE status <> 'pending'),
        'pending_students', count(*) FILTER (WHERE status = 'pending'),
        'coverage_pct', round(100.0 * count(student) FILTER (WHERE status <> 'pending') / NULLIF(count(student), 0), 2)
    ),
    'old_axis', json_build_object(
        'missing_snapshot_students', count(student) FILTER (WHERE NOT old_present),
        'missing_snapshot_pct', round(100.0 * count(student) FILTER (WHERE NOT old_present) / NULLIF(count(student), 0), 2),
        'missing_ratio_in_snapshot_students', count(student) FILTER (WHERE old_present AND old_axis IS NULL),
        'measured_students', count(old_axis), 'zero_students', count(*) FILTER (WHERE old_axis = 0),
        'zero_pct_of_measured', round(100.0 * count(*) FILTER (WHERE old_axis = 0) / NULLIF(count(old_axis), 0), 2),
        'p50', round((percentile_cont(0.50) WITHIN GROUP (ORDER BY old_axis))::numeric, 2),
        'p90', round((percentile_cont(0.90) WITHIN GROUP (ORDER BY old_axis))::numeric, 2),
        'max', round(max(old_axis), 2)
    ),
    'ai', json_build_object(
        'legacy_messages', sum(legacy_messages), 'legacy_students', count(*) FILTER (WHERE legacy_messages > 0),
        'course_workspace_messages', sum(workspace_messages),
        'course_workspace_students', CASE WHEN :'has_course_links'::boolean THEN count(*) FILTER (WHERE workspace_messages > 0) END,
        'old_ai_missing_in_snapshot_students', count(student) FILTER (WHERE old_present AND old_ai IS NULL),
        'comparable_students', count(old_ai), 'old_snapshot_count', sum(old_ai),
        'legacy_messages_in_comparable_roster', sum(legacy_messages) FILTER (WHERE old_ai IS NOT NULL),
        'legacy_above_snapshot_students', count(*) FILTER (WHERE legacy_messages > old_ai),
        'legacy_below_snapshot_students', count(*) FILTER (WHERE legacy_messages < old_ai),
        'legacy_positive_snapshot_zero_students', count(*) FILTER (WHERE legacy_messages > 0 AND old_ai = 0),
        'legacy_positive_old_axis_zero_students', count(*) FILTER (WHERE legacy_messages > 0 AND old_axis = 0)
    ),
    'expression', json_build_object(
        'works', sum(expressions),
        'students', CASE WHEN count(expressions) > 0 THEN count(*) FILTER (WHERE expressions > 0) END,
        'reached_pct_of_published', round(100.0 * count(*) FILTER (WHERE expressions > 0) / NULLIF(count(expressions), 0), 2),
        'students_with_channel_breakdown', count(group_works),
        'group_works', sum(group_works),
        'group_students', CASE WHEN count(group_works) > 0 THEN count(*) FILTER (WHERE group_works > 0) END,
        'reply_works', sum(reply_works),
        'reply_students', CASE WHEN count(reply_works) > 0 THEN count(*) FILTER (WHERE reply_works > 0) END
    ),
    'opportunity', json_build_object(
        'known_closed_person_activities', sum(closed_offered), 'joined_closed_person_activities', sum(closed_joined),
        'known_open_person_activities', sum(open_offered), 'joined_open_person_activities', sum(open_joined),
        'limited_coverage_students', count(*) FILTER (WHERE opp ->> 'coverage_status' = 'limited'),
        'expressed_students', count(*) FILTER (WHERE status = 'expressed'),
        'offered_without_expression_students', count(*) FILTER (WHERE status = 'offered_without_expression'),
        'limited_history_without_sample_students', count(*) FILTER (WHERE status = 'limited_history'),
        'participation_only_students', count(*) FILTER (WHERE status = 'participation_only'),
        'no_assessable_opportunity_students', count(*) FILTER (WHERE status = 'no_assessable_opportunity')
    )
)
FROM classified GROUP BY offering, calculated_at ORDER BY offering;
COMMIT;
