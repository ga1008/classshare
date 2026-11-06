"""Knowledge reads preserve real SQLite owner transactions and PG error identity."""
import sqlite3
from types import SimpleNamespace
import unittest

import psycopg.errors

from classroom_app import database
from classroom_app.db.connection import execute_insert_returning_id as insert_id
from classroom_app.db.errors import DatabaseBusyError, DatabaseConnectionError
from classroom_app.db.postgres import LanSharePostgresConnection
from classroom_app.services import platform_knowledge_service as service
from tests.sqlite_database_fixture import isolated_sqlite_database

SECRET = "private-parameters-profile-connection-or-exception-text"


class KnowledgeSQLiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(isolated_sqlite_database())
        with database.get_db_connection() as conn:
            cls.teacher = insert_id(conn, "INSERT INTO teachers(name,email,hashed_password,description) VALUES('Owned teacher','knowledge@example.invalid','unused','Teacher profile')", ())
            cls.other_teacher = insert_id(conn, "INSERT INTO teachers(name,email,hashed_password) VALUES('Other teacher','other@example.invalid','unused')", ())
            primary = insert_id(conn, "INSERT INTO classes(name,created_by_teacher_id) VALUES('Primary class',?)", (cls.teacher,))
            linked = insert_id(conn, "INSERT INTO classes(name,created_by_teacher_id) VALUES('Linked class',?)", (cls.teacher,))
            cls.student = insert_id(conn, "INSERT INTO students(name,student_id_number,class_id,description) VALUES('Owned student','KNOWLEDGE-1',?,'Student profile')", (primary,))
            other = insert_id(conn, "INSERT INTO students(name,student_id_number,class_id) VALUES('Other student','KNOWLEDGE-2',?)", (linked,))
            course = insert_id(conn, "INSERT INTO courses(name,created_by_teacher_id) VALUES('Owned course',?)", (cls.teacher,))
            insert_id(conn, "INSERT INTO courses(name,created_by_teacher_id) VALUES('Other course',?)", (cls.other_teacher,))
            offering = insert_id(conn, "INSERT INTO class_offerings(class_id,course_id,teacher_id) VALUES(?,?,?)", (primary,course,cls.teacher))
            conn.execute("INSERT INTO class_offering_class_links(offering_id,class_id,teacher_id) VALUES(?,?,?)", (offering,linked,cls.teacher))
            assignments = [insert_id(conn, "INSERT INTO assignments(course_id,title,class_offering_id) VALUES(?,?,?)", (course,f"Owned assignment {i}",offering)) for i in range(3)]
            for assignment in assignments:
                conn.execute("INSERT INTO submissions(assignment_id,student_pk_id,student_name,submitted_at) VALUES(?,?,?,?)", (assignment,cls.student,"Owned student","2026-01-01"))
            conn.execute("INSERT INTO submissions(assignment_id,student_pk_id,student_name,submitted_at) VALUES(?,?,?,?)", (assignments[0],other,"Other student","2026-01-01"))
            for role,user,count in (("student",cls.student,2),("student",other,1),("teacher",cls.teacher,1)):
                for number in range(count):
                    conn.execute("INSERT INTO blog_posts(author_identity,author_role,author_user_pk,author_display_name,title) VALUES(?,?,?,?,?)", (f"{role}:{user}",role,user,"Synthetic",f"Owned post {number}"))
            conn.execute("INSERT INTO gongwen_follow_hits(teacher_id,document_id) VALUES(?,77)", (cls.teacher,))
            conn.execute("INSERT INTO agent_tasks(task_uuid,teacher_id,actor_role,actor_id,task_type,title,private_instruction) VALUES('knowledge-owned-task',?,'teacher',?,'general_teaching_task','Owned task','Owned task')", (cls.teacher,cls.teacher))
            conn.execute("INSERT INTO teacher_calendar_events(teacher_id,source_type,source_key,title,starts_at) VALUES(?,'manual','knowledge-upcoming','Owned future event','2999-01-01')", (cls.teacher,))
            conn.execute("INSERT INTO teacher_calendar_events(teacher_id,source_type,source_key,title,starts_at,deleted_at) VALUES(?,'manual','knowledge-deleted','Deleted event','2999-01-01','2026-01-01')", (cls.teacher,))
            conn.execute("CREATE TABLE knowledge_transaction_effects(value TEXT)")
            conn.commit()

    def setUp(self):
        self.conn = database.get_db_connection()
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)

    def test_student_counts_real_authoritative_submission_column_and_keeps_blog_context(self):
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(submissions)")}
        self.assertIn("student_pk_id",columns); self.assertNotIn("student_id",columns)
        before = self.conn.total_changes
        value = service.build_user_knowledge_block(self.conn,self.student,"student")
        self.assertIn("Owned student",value); self.assertIn("Primary class",value)
        self.assertIn("Student profile",value)
        self.assertIn("累计提交作业/考试 3 次，发表博客 2 篇",value)
        self.assertEqual(self.conn.total_changes,before)

    def test_teacher_real_schema_queries_preserve_combined_class_and_calendar_fields(self):
        before=self.conn.total_changes
        value=service.build_user_knowledge_block(self.conn,self.teacher,"teacher")
        for fragment in ("Owned teacher","Teacher profile","1 门课程模板、1 个开课课堂、约 2 名学生、3 份作业/考试",
                         "公文关注命中 1 条；历史 Agent 任务 1 个","Owned course","Owned future event"):
            self.assertIn(fragment,value)
        self.assertNotIn("Deleted event",value)
        self.assertEqual(self.conn.total_changes,before)

    def test_optional_missing_table_preserves_prior_and_later_writes_until_owner_rollback(self):
        self.conn.execute("INSERT INTO knowledge_transaction_effects VALUES('before')")
        self.conn.execute("SAVEPOINT caller_owned")
        with self.assertLogs(service.logger,level="WARNING"):
            self.assertEqual(service._safe_scalar(self.conn,"SELECT COUNT(*) FROM optional_feature_absent",query_name="optional_count",optional_schema=True),0)
            self.assertEqual(service._safe_rows(self.conn,"SELECT * FROM optional_feature_absent",query_name="optional_rows",optional_schema=True),[])
        self.assertTrue(self.conn.in_transaction)
        self.conn.execute("INSERT INTO knowledge_transaction_effects VALUES('after')")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM knowledge_transaction_effects").fetchone()[0],2)
        self.conn.execute("ROLLBACK TO SAVEPOINT caller_owned")
        self.conn.execute("RELEASE SAVEPOINT caller_owned")
        self.assertEqual(self.conn.execute("SELECT value FROM knowledge_transaction_effects").fetchone()[0],"before")
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM knowledge_transaction_effects").fetchone()[0],0)

    def test_same_named_outer_savepoint_survives_success_and_optional_failure(self):
        self.conn.execute("INSERT INTO knowledge_transaction_effects VALUES('before outer')")
        self.conn.execute("SAVEPOINT platform_knowledge_read")
        self.conn.execute("INSERT INTO knowledge_transaction_effects VALUES('inside outer')")
        self.assertEqual(service._safe_scalar(self.conn,"SELECT COUNT(*) FROM knowledge_transaction_effects",query_name="nested_count"),2)
        with self.assertLogs(service.logger,level="WARNING"):
            self.assertEqual(service._safe_scalar(self.conn,"SELECT COUNT(*) FROM optional_feature_absent",query_name="nested_optional",optional_schema=True),0)
        self.conn.execute("INSERT INTO knowledge_transaction_effects VALUES('after helper')")
        self.conn.execute("ROLLBACK TO SAVEPOINT platform_knowledge_read")
        self.conn.execute("RELEASE SAVEPOINT platform_knowledge_read")
        self.assertEqual([row[0] for row in self.conn.execute("SELECT value FROM knowledge_transaction_effects")],["before outer"])
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM knowledge_transaction_effects").fetchone()[0],0)

    def test_bad_column_is_not_optional_and_error_identity_and_owner_transaction_survive(self):
        self.conn.execute("INSERT INTO knowledge_transaction_effects VALUES('before')")
        observed=[]
        real=self.conn
        class Observed:
            def execute(self,*args):
                try:return real.execute(*args)
                except sqlite3.OperationalError as exc:observed.append(exc);raise
        with self.assertLogs(service.logger,level="ERROR") as logs:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                service._safe_scalar(Observed(),"SELECT wrong_program_column FROM knowledge_transaction_effects WHERE value=?",(SECRET,),query_name="required_stat",optional_schema=True)
        self.assertIs(caught.exception,observed[0])
        self.assertNotIn(SECRET,"\n".join(logs.output))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM knowledge_transaction_effects").fetchone()[0],1)
        self.conn.execute("INSERT INTO knowledge_transaction_effects VALUES('after')")
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM knowledge_transaction_effects").fetchone()[0],0)

    def test_missing_core_table_is_not_quietly_replaced_by_zero(self):
        with self.assertLogs(service.logger,level="ERROR"):
            with self.assertRaises(sqlite3.OperationalError):
                service._safe_scalar(self.conn,"SELECT COUNT(*) FROM missing_core_table",query_name="core_stat")
        self.assertEqual(self.conn.execute("SELECT 1").fetchone()[0],1)

    def test_missing_optional_teacher_features_keep_core_profile_and_offering_context(self):
        self.conn.execute("BEGIN")
        self.conn.execute("DROP TABLE gongwen_follow_hits")
        self.conn.execute("DROP TABLE teacher_calendar_events")
        with self.assertLogs(service.logger,level="WARNING"):
            value=service.build_teacher_knowledge_block(self.conn,self.teacher)
        self.assertIn("约 2 名学生",value);self.assertIn("Owned course",value)
        self.assertIn("公文关注命中 0 条",value)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM gongwen_follow_hits").fetchone()[0],1)


class Rows:
    def fetchone(self):return (7,)
    def fetchall(self):return [(7,)]


class AbortingPG:
    """Driver seam enforces PG's aborted-transaction behavior after an error."""
    def __init__(self,primary,*,rollback_error=None,release_error=None,start_error=None):
        self.primary,self.rollback_error,self.release_error,self.start_error=primary,rollback_error,release_error,start_error
        self.calls=[];self.info=SimpleNamespace(transaction_status=2)
        self.commits=self.rollbacks=0
        self.savepoints=[]
    def execute(self,sql,params=None):
        self.calls.append((sql,params))
        if sql.startswith("ROLLBACK TO SAVEPOINT "):
            if self.rollback_error:raise self.rollback_error
            name=sql.removeprefix("ROLLBACK TO SAVEPOINT ")
            index=len(self.savepoints)-1-self.savepoints[::-1].index(name)
            self.savepoints=self.savepoints[:index+1]
            self.info.transaction_status=2;return Rows()
        if sql.startswith("RELEASE SAVEPOINT "):
            if self.release_error:raise self.release_error
            if self.info.transaction_status==3:raise psycopg.errors.InFailedSqlTransaction(SECRET)
            name=sql.removeprefix("RELEASE SAVEPOINT ")
            index=len(self.savepoints)-1-self.savepoints[::-1].index(name)
            self.savepoints=self.savepoints[:index]
            return Rows()
        if sql.startswith("SAVEPOINT "):
            if self.start_error:raise self.start_error
            self.savepoints.append(sql.removeprefix("SAVEPOINT "))
            return Rows()
        if self.info.transaction_status==3:raise psycopg.errors.InFailedSqlTransaction(SECRET)
        if "broken_query" in sql:
            self.info.transaction_status=3;raise self.primary
        return Rows()
    def commit(self):self.commits+=1;raise AssertionError("Knowledge may not commit caller work")
    def rollback(self):self.rollbacks+=1;raise AssertionError("Knowledge may not roll back caller work")


class KnowledgePGAdapterTests(unittest.TestCase):
    def test_same_named_pg_outer_savepoint_remains_after_inner_error_and_success(self):
        raw=AbortingPG(psycopg.errors.UndefinedTable(SECRET));conn=LanSharePostgresConnection(raw)
        conn.execute("SAVEPOINT platform_knowledge_read")
        with self.assertLogs(service.logger,level="WARNING"):
            self.assertEqual(service._safe_scalar(conn,"SELECT broken_query",query_name="nested_optional",optional_schema=True),0)
        self.assertEqual(raw.savepoints,["platform_knowledge_read"])
        self.assertEqual(service._safe_scalar(conn,"SELECT 1",query_name="nested_count"),7)
        self.assertEqual(raw.savepoints,["platform_knowledge_read"])
        conn.execute("ROLLBACK TO SAVEPOINT platform_knowledge_read")
        conn.execute("RELEASE SAVEPOINT platform_knowledge_read")
        self.assertEqual(raw.savepoints,[])
        self.assertEqual((raw.commits,raw.rollbacks),(0,0))
        commands={sql for sql,_ in raw.calls if "SAVEPOINT" in sql}
        self.assertEqual(commands,{"SAVEPOINT platform_knowledge_read","ROLLBACK TO SAVEPOINT platform_knowledge_read","RELEASE SAVEPOINT platform_knowledge_read"})

    def test_optional_undefined_table_recovers_pg_transaction_and_preserves_adapter_bindings(self):
        raw=AbortingPG(psycopg.errors.UndefinedTable(SECRET));conn=LanSharePostgresConnection(raw)
        with self.assertLogs(service.logger,level="WARNING") as logs:
            value=service._safe_scalar(conn,"SELECT COUNT(*) FROM broken_query WHERE actor=?",(41,),query_name="optional_feature",optional_schema=True)
        self.assertEqual(value,0)
        self.assertIn(("SELECT COUNT(*) FROM broken_query WHERE actor=%s",(41,)),raw.calls)
        self.assertEqual(conn.execute("SELECT 1").fetchone(),(7,))
        self.assertEqual(raw.info.transaction_status,2)
        self.assertEqual((raw.commits,raw.rollbacks),(0,0))
        savepoint=raw.calls[0][0].removeprefix("SAVEPOINT ")
        self.assertEqual([row[0] for row in raw.calls[2:4]],["ROLLBACK TO SAVEPOINT "+savepoint,"RELEASE SAVEPOINT "+savepoint])
        self.assertNotIn(SECRET,"\n".join(logs.output))

    def test_programming_permission_pressure_connection_and_unknown_errors_keep_identity(self):
        failures=[psycopg.errors.UndefinedColumn(SECRET),psycopg.errors.SyntaxError(SECRET),
            psycopg.errors.InsufficientPrivilege(SECRET),psycopg.errors.QueryCanceled(SECRET),
            psycopg.errors.DeadlockDetected(SECRET),DatabaseBusyError(SECRET),DatabaseConnectionError(SECRET),RuntimeError(SECRET)]
        for primary in failures:
            with self.subTest(error=type(primary).__name__):
                raw=AbortingPG(primary);conn=LanSharePostgresConnection(raw)
                with self.assertLogs(service.logger,level="ERROR") as logs:
                    with self.assertRaises(type(primary)) as caught:
                        service._safe_scalar(conn,"SELECT broken_query",query_name="stat",optional_schema=True)
                self.assertIs(caught.exception,primary)
                self.assertEqual(conn.execute("SELECT 1").fetchone(),(7,))
                self.assertEqual((raw.commits,raw.rollbacks),(0,0))
                self.assertNotIn(SECRET,"\n".join(logs.output))

    def test_rows_and_identity_reads_share_error_isolation_and_outer_dispatcher_does_not_swallow(self):
        for role in ("teacher","student"):
            primary=DatabaseConnectionError(SECRET)
            raw=AbortingPG(primary);execute=raw.execute
            def identity_failure(sql,params=None):
                return execute("SELECT broken_query" if "FROM teachers" in sql or "FROM students" in sql else sql,params)
            raw.execute=identity_failure
            with self.assertLogs(service.logger,level="ERROR"):
                with self.assertRaises(DatabaseConnectionError) as caught:
                    service.build_user_knowledge_block(LanSharePostgresConnection(raw),1,role)
            self.assertIs(caught.exception,primary)
            self.assertEqual(raw.info.transaction_status,2)
        raw=AbortingPG(psycopg.errors.UndefinedTable(SECRET))
        with self.assertLogs(service.logger,level="WARNING"):
            self.assertEqual(service._safe_rows(LanSharePostgresConnection(raw),"SELECT * FROM broken_query",query_name="optional_rows",optional_schema=True),[])

    def test_cleanup_failure_never_turns_optional_error_into_default_and_preserves_primary(self):
        primary=psycopg.errors.UndefinedTable(SECRET)
        raw=AbortingPG(primary,rollback_error=DatabaseConnectionError(SECRET),release_error=OSError(SECRET))
        with self.assertLogs(service.logger,level="ERROR") as logs:
            with self.assertRaises(psycopg.errors.UndefinedTable) as caught:
                service._safe_scalar(LanSharePostgresConnection(raw),"SELECT broken_query",query_name="optional_feature",optional_schema=True)
        self.assertIs(caught.exception,primary)
        self.assertEqual(len(primary.__notes__),2)
        self.assertNotIn(SECRET,"\n".join(primary.__notes__)+"\n".join(logs.output))
        self.assertEqual((raw.commits,raw.rollbacks),(0,0))

    def test_savepoint_creation_and_successful_query_release_fail_closed(self):
        for position in ("start","release"):
            primary=DatabaseConnectionError(SECRET)
            raw=AbortingPG(None,**({"start_error":primary} if position=="start" else {"release_error":primary}))
            with self.assertLogs(service.logger,level="ERROR"):
                with self.assertRaises(DatabaseConnectionError) as caught:
                    service._safe_scalar(LanSharePostgresConnection(raw),"SELECT 1",query_name="optional_feature",optional_schema=True)
            self.assertIs(caught.exception,primary)
            if position=="start":self.assertEqual(len(raw.calls),1)
            self.assertEqual((raw.commits,raw.rollbacks),(0,0))


if __name__=="__main__":unittest.main()
