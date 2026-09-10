"""Academic roster persistence must retire changed actors' old authority atomically."""
from fastapi import HTTPException

from classroom_app.services import academic_roster_sync_service as roster
from classroom_app.services.academic_class_mapping_service import ensure_teaching_class_mapping_schema
from classroom_app.services.agent_delegation_service import create_persistent_authorization, verify_task_delegation
from classroom_app.services.organization_scope_service import apply_teacher_scope_to_org
from tests.test_agent_platform_writes import PlatformWriteFixture


class AcademicRosterAuthorityTests(PlatformWriteFixture):
    def setUp(self):
        super().setUp()
        ensure_teaching_class_mapping_schema(self.conn)
        self.scope=apply_teacher_scope_to_org(self.conn,7,college='',department='')
        self.conn.execute('UPDATE students SET school_code=?,school_name=?,college=?,department=?',tuple(self.scope[k] for k in ('school_code','school_name','college','department')))
        self.conn.execute("INSERT INTO academic_semesters(id,teacher_id,name,start_date,end_date) VALUES(90,7,'Synthetic term','2026-09-01','2027-01-30')")
        self.conn.commit()
        self.student_token=self.token(self.student,scopes=['platform:read'])
        self.teacher_token=self.token(self.teacher,scopes=['platform:read'])
        create_persistent_authorization(self.conn,actor_role='student',actor_id=7,source_session_id='student-session',scopes=['platform:read'],intent_reference='synthetic-roster-test',ttl_seconds=600)
        self.conn.commit()
        self.semester=dict(self.conn.execute('SELECT * FROM academic_semesters WHERE id=90').fetchone())

    def students(self, status='休学'):
        return [roster.AcademicRosterStudent(student_number='S7',name='Student 7',class_name='Class 30',school_status=status),
                roster.AcademicRosterStudent(student_number='S9',name='Student 9',class_name='Class 30',school_status='在读')]

    def incoming(self, students=None):
        return [roster.AcademicTeachingClassRoster(teaching_class_id='synthetic-roster',teaching_class_name='Course teaching class',
                course_name='Course',class_composition='Class 30',academic_year='2026',academic_term='3',students=students or self.students())]

    def persist(self, incoming, prepared):
        return roster._persist_rosters(self.conn,teacher_id=7,semester=self.semester,rosters=incoming,
            source_summary=[],synced_at='2026-09-10T12:00:00',prepared_authority_ids=prepared)

    def test_sync_suspension_and_later_activation_do_not_restore_old_grants(self):
        incoming=self.incoming()
        prepared=roster._prepare_roster_authority_transitions(self.conn,teacher_id=7,rosters=incoming)
        self.assertEqual(frozenset({7}),prepared)
        result=self.persist(incoming,prepared);self.conn.commit()
        self.assertEqual(2,result['memberships_upserted'])
        self.assertEqual('suspended',self.conn.execute('SELECT enrollment_status FROM students WHERE id=7').fetchone()[0])
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_user_key='student:7'").fetchone()[0])
        for table in ('agent_task_delegations','agent_persistent_authorizations'):
            self.assertEqual(('revoked','academic_roster_authority_changed'),tuple(self.conn.execute('SELECT status,revoke_reason FROM '+table+" WHERE actor_role='student'").fetchone()))
        incoming=self.incoming(self.students('在读'))
        prepared=roster._prepare_roster_authority_transitions(self.conn,teacher_id=7,rosters=incoming)
        self.persist(incoming,prepared);self.conn.commit()
        self.assertEqual('active',self.conn.execute('SELECT enrollment_status FROM students WHERE id=7').fetchone()[0])
        with self.assertRaises(HTTPException):verify_task_delegation(self.conn,self.student_token,purpose='tools')
        verify_task_delegation(self.conn,self.teacher_token,purpose='tools')

    def test_rollback_restores_roster_status_sessions_grants_and_memberships(self):
        incoming=self.incoming()
        before=self.conn.execute('SELECT COUNT(*) FROM teacher_academic_roster_memberships').fetchone()[0]
        prepared=roster._prepare_roster_authority_transitions(self.conn,teacher_id=7,rosters=incoming)
        self.persist(incoming,prepared)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual('active',self.conn.execute('SELECT enrollment_status FROM students WHERE id=7').fetchone()[0])
        self.assertEqual(before,self.conn.execute('SELECT COUNT(*) FROM teacher_academic_roster_memberships').fetchone()[0])
        verify_task_delegation(self.conn,self.student_token,purpose='tools')
        self.assertEqual('active',self.conn.execute('SELECT status FROM agent_persistent_authorizations').fetchone()[0])

    def test_unaffected_roster_does_not_scan_tasks_or_retire_authority(self):
        incoming=self.incoming(self.students('在读'))
        statements=[];self.conn.set_trace_callback(statements.append)
        prepared=roster._prepare_roster_authority_transitions(self.conn,teacher_id=7,rosters=incoming)
        self.conn.set_trace_callback(None)
        self.assertEqual(frozenset(),prepared)
        self.assertFalse(any('agent_tasks' in sql for sql in statements))
        self.persist(incoming,prepared);self.conn.commit()
        verify_task_delegation(self.conn,self.student_token,purpose='tools')

    def test_large_affected_set_uses_batched_task_queries_and_locks_only_real_running_tasks(self):
        additions=[]
        for student_id in range(1000,1450):
            number='S'+str(student_id)
            self.conn.execute("INSERT INTO students(id,student_id_number,name,class_id,enrollment_status,school_code,school_name,college,department) VALUES(?,?,?,30,'active',?,?,?,?)",
                (student_id,number,'Synthetic '+number,*[self.scope[k] for k in ('school_code','school_name','college','department')]))
            additions.append(roster.AcademicRosterStudent(student_number=number,name='Synthetic '+number,class_name='Class 30',school_status='休学'))
        self.conn.commit()
        incoming=self.incoming(self.students()+additions)
        statements=[];self.conn.set_trace_callback(statements.append)
        prepared=roster._prepare_roster_authority_transitions(self.conn,teacher_id=7,rosters=incoming)
        self.conn.set_trace_callback(None)
        self.assertEqual(451,len(prepared))
        self.assertEqual(2,len([s for s in statements if s.startswith('SELECT id,actor_id FROM agent_tasks')]))
        self.assertEqual(1,len([s for s in statements if s.startswith('UPDATE agent_tasks SET status=status')]))
        self.assertFalse(any(s.upper().startswith(('CREATE ','ALTER ','COMMIT')) for s in statements))
        self.conn.rollback()

    def test_new_authority_change_after_preparation_fails_closed_without_late_task_locks(self):
        incoming=self.incoming(self.students('在读'))
        prepared=roster._prepare_roster_authority_transitions(self.conn,teacher_id=7,rosters=incoming)
        self.assertEqual(frozenset(),prepared)
        # Simulate a committed change between the initial snapshot and writes.
        self.conn.execute("UPDATE students SET enrollment_status='suspended' WHERE id=7");self.conn.commit()
        statements=[];self.conn.set_trace_callback(statements.append)
        with self.assertRaises(HTTPException) as caught:self.persist(incoming,prepared)
        self.conn.set_trace_callback(None);self.conn.rollback()
        self.assertEqual(409,caught.exception.status_code)
        self.assertFalse(any(s.startswith('UPDATE agent_tasks') for s in statements))
        self.assertEqual('suspended',self.conn.execute('SELECT enrollment_status FROM students WHERE id=7').fetchone()[0])

    def test_skipped_class_and_foreign_current_owner_do_not_revoke_other_students(self):
        incoming=self.incoming()
        self.assertEqual(frozenset(),roster._prepare_roster_authority_transitions(self.conn,teacher_id=7,rosters=incoming,
            reconciliation={'class_decisions':{'Class 30':{'action':'skip'}}}))
        self.conn.execute('UPDATE classes SET created_by_teacher_id=8 WHERE id=30');self.conn.commit()
        prepared=roster._prepare_roster_authority_transitions(self.conn,teacher_id=7,rosters=incoming)
        self.assertEqual(frozenset(),prepared)
        self.persist(incoming,prepared);self.conn.commit()
        self.assertEqual('active',self.conn.execute('SELECT enrollment_status FROM students WHERE id=7').fetchone()[0])
        self.assertEqual('active',self.conn.execute('SELECT status FROM agent_persistent_authorizations').fetchone()[0])
