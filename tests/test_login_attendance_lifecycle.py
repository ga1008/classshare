"""Full synthetic schema: merging/deleting never guess slots or orphan tasks."""
import json
import unittest
from unittest.mock import patch
from fastapi import HTTPException
import test_offering_merge_service as fixture
from classroom_app import database
from classroom_app.services import login_attendance_service as attendance
from classroom_app.services import login_attendance_lifecycle_service as lifecycle
from classroom_app.services import offering_merge_service as merge
from classroom_app.services import approval_workflow_schema, approval_workflow_service


class AttendanceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.OfferingMergeServiceTests('test_execute_merges_everything_atomically')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.addCleanup(self.f.tearDown)
        self.user={'id':self.f.teacher_id,'role':'teacher','name':'Synthetic'}
        p=patch.object(approval_workflow_service,'_notify',return_value=0)
        p.start();self.addCleanup(p.stop)

    def seed(self,conn,*,current=1,state='published',key='original-request'):
        from classroom_app.db.connection import execute_insert_returning_id
        start=attendance.rules.to_ms('2026-03-09T08:30:00+08:00')
        context={'class_offering_id':self.f.source_id,'session_id':self.f.source_session,
                 'course_name':'Synthetic','session_date':'2026-03-09','session_title':'Original lesson'}
        rid=execute_insert_returning_id(conn,"INSERT INTO login_attendance_rounds(class_offering_id,session_id,teacher_id,request_key,state,is_current,starts_at_ms,ends_at_ms,window_start_ms,window_end_ms,settle_at_ms,created_at_ms,next_evaluation_ms,policy_json,context_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(self.f.source_id,self.f.source_session,self.f.teacher_id,key,state,current,start,start+5400000,start-600000,start+6000000,start+6120000,start,start,
            json.dumps({'appeal_days':7,'networks':['192.0.2.0/24']}),json.dumps(context)))
        conn.execute("INSERT INTO login_attendance_records(round_id,student_id,student_number,name,class_name,status,updated_at_ms) VALUES(?,?,'SYN','Synthetic student','Original class','PRESENT',?)",
            (rid,self.f.student_b,start))
        return rid

    def binding(self,conn,rid,*,state='preview'):
        conn.execute("INSERT INTO smart_attendance_write_bindings(id,owner_teacher_id,class_offering_id,session_id,local_round_id,external_account_key,credential_id,credential_generation,source_json,remote_checkin_id,remote_snapshot_json,created_at,updated_at) "
            "VALUES('b',?,?,?,?, 'synthetic-key',1,'g','{}','synthetic-remote','{}','2026-03-09','2026-03-09')",
            (self.f.teacher_id,self.f.source_id,self.f.source_session,str(rid)))
        conn.execute("INSERT INTO smart_attendance_sync_runs(id,binding_id,owner_teacher_id,local_round_id,local_revision,context_fingerprint,credential_generation,binding_revision,target_json,baseline_json,state,created_at,updated_at) "
            "VALUES('run','b',?,?,1,'fingerprint','g',1,'{}','{}',?,'2026-03-09','2026-03-09')",(self.f.teacher_id,str(rid),state))

    def test_merge_keeps_original_slot_roster_and_allows_only_former_current_history_edit(self):
        with database.get_db_connection() as conn:
            old=self.seed(conn,current=0,key='superseded-original')
            rid=self.seed(conn);self.binding(conn,rid)
            conn.commit()
            self.f._execute(conn);conn.commit()
            row=attendance.load_round(conn,rid,self.user)
            self.assertEqual(self.f.target_id,row['class_offering_id']);self.assertIsNone(row['session_id'])
            context=attendance._load(row['context_json'])
            self.assertEqual(self.f.source_session,context['merged_from']['session_id'])
            self.assertEqual('2026-03-09',context['session_date'])
            history=attendance.history(conn,self.f.target_id,self.user)
            items={r['id']:r for r in history['items']}
            self.assertTrue(items[rid]['can_edit']);self.assertFalse(items[old]['can_edit'])
            record=attendance._records(conn,rid)[0]
            result=attendance.change_records(conn,rid,self.user,{'records':[{'student_id':self.f.student_b,'revision':record['revision'],'status':'LATE'}],'reason':'Historical verification'})
            self.assertEqual('updated',result['results'][0]['result'])
            self.assertEqual('Original class',result['round']['records'][0]['class_name'])
            binding=conn.execute("SELECT * FROM smart_attendance_write_bindings WHERE id='b'").fetchone()
            self.assertEqual('paused',binding['state']);self.assertIsNone(binding['session_id'])
            self.assertEqual(self.f.target_id,binding['class_offering_id'])
            self.assertEqual('paused',conn.execute("SELECT state FROM smart_attendance_sync_runs WHERE id='run'").fetchone()[0])
            self.assertIsNone(conn.execute('SELECT id FROM class_offerings WHERE id=?',(self.f.source_id,)).fetchone())

    def test_active_round_and_unresolved_remote_outcome_block_merge_and_delete(self):
        with database.get_db_connection() as conn:
            rid=self.seed(conn,state='running');conn.commit()
            self.assertTrue(lifecycle.blockers(conn,[self.f.source_id],merging=True))
            conn.execute("UPDATE login_attendance_rounds SET state='published' WHERE id=?",(rid,));self.binding(conn,rid,state='outcome_unknown');conn.commit()
            preview=merge.build_merge_preview(conn,teacher_id=self.f.teacher_id,target_offering_id=self.f.target_id,source_offering_ids=[self.f.source_id])
            self.assertFalse(preview['can_execute'])
            with self.assertRaises(HTTPException) as error:lifecycle.prepare_deletion(conn,self.f.source_id,self.user)
            self.assertEqual(409,error.exception.status_code);conn.rollback()
            self.assertEqual('published',attendance.load_round(conn,rid)['state'])

    def test_deletion_retains_final_records_and_cancels_active_round_without_remote_orphans(self):
        with database.get_db_connection() as conn:
            rid=self.seed(conn);self.binding(conn,rid)
            conn.execute('UPDATE login_attendance_rounds SET is_current=0 WHERE id=?',(rid,))
            active=self.seed(conn,state='running',key='active-while-delete');conn.commit()
            lifecycle.prepare_deletion(conn,self.f.source_id,self.user)
            conn.execute('DELETE FROM class_offerings WHERE id=?',(self.f.source_id,));conn.commit()
            final=attendance.load_round(conn,rid,self.user)
            self.assertIsNone(final['class_offering_id']);self.assertEqual('published',final['state'])
            self.assertEqual('PRESENT',attendance._records(conn,rid)[0]['status'])
            self.assertFalse(attendance.round_view(conn,rid,self.user)['can_edit'])
            self.assertEqual('void',attendance.load_round(conn,active)['state'])
            binding=conn.execute("SELECT * FROM smart_attendance_write_bindings WHERE id='b'").fetchone()
            self.assertIsNone(binding['class_offering_id']);self.assertIsNone(binding['session_id'])
            self.assertEqual('paused',binding['state'])
