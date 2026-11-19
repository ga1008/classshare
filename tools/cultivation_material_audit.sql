-- Read-only before/after reconciliation. Pipe through psql -XAtq with
-- ON_ERROR_STOP=1. Keep the output private (it contains internal student IDs).
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='20s';
WITH offering_classes AS (
    SELECT id AS offering, class_id FROM class_offerings
    UNION SELECT offering_id, class_id FROM class_offering_class_links
), members AS (
    SELECT DISTINCT c.offering, s.id AS student, s.student_id_number
    FROM offering_classes c JOIN students s ON s.class_id=c.class_id
    WHERE COALESCE(s.enrollment_status, 'active')='active'
), scores AS (
    SELECT COALESCE(m.offering,p.class_offering_id) AS offering,
           COALESCE(m.student,p.student_id) AS student, m.student IS NOT NULL AS enrolled,
           m.student_id_number, p.score, p.components_json::jsonb AS components,
           p.metrics_json::jsonb AS metrics, p.calculated_at, COALESCE(d.hide_cultivation,0) AS hidden
    FROM members m FULL JOIN learning_progress_snapshots p
      ON p.class_offering_id=m.offering AND p.student_id=m.student
    LEFT JOIN cultivation_display_settings d ON d.student_id=COALESCE(m.student,p.student_id)
), rankings AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY offering,enrolled ORDER BY score DESC NULLS LAST,
                                 student_id_number, student) AS position
    FROM scores
)
SELECT json_build_object(
    'offering',offering,'student',student,'enrolled',enrolled,'hidden',hidden,
    'rank',CASE WHEN enrolled AND score IS NOT NULL THEN position END,
    'score',score,'components',components,'calculated_at',calculated_at,
    'required',metrics->'material'->'required_count','mastered',metrics->'material'->'mastered_count',
    'policy',metrics->>'scoring_policy_version','weight_version',metrics->>'weight_version',
    'material_scope',metrics->>'material_scope_key',
    'legacy_credit_ratio',metrics->'material'->'legacy_credit_ratio'
)
FROM rankings ORDER BY offering, enrolled DESC, position;
COMMIT;
