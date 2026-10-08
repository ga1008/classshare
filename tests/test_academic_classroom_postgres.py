"""Actual PG classroom queries in an exclusively created synthetic database."""
import asyncio,os,unittest,uuid
from contextlib import asynccontextmanager,contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from classroom_app.db.postgres import LanSharePostgresConnection,sqlite_compatible_dict_row
from classroom_app.services import academic_classroom_sync_service as service
from tools.assessment_postgres_rehearsal import connect_offline

@unittest.skipUnless(os.environ.get('ASSESSMENT_REHEARSAL_TEST_CLUSTER') and os.environ.get('ASSESSMENT_REHEARSAL_TEST_PORT'),
                     'Explicit isolated loopback PostgreSQL cluster required')
class AcademicClassroomPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster=Path(os.environ['ASSESSMENT_REHEARSAL_TEST_CLUSTER']);cls.port=int(os.environ['ASSESSMENT_REHEARSAL_TEST_PORT'])
        cls.database='lanshare_assessment_rehearsal_rooms_'+uuid.uuid4().hex[:12]
        cls.admin=connect_offline(cluster_dir=cls.cluster,port=cls.port,database='lanshare_assessment_rehearsal');cls.admin.autocommit=True
        cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')
        from classroom_app import config
        dialect=patch.object(config,'DB_ENGINE','postgres');dialect.start();cls.addClassCleanup(dialect.stop)

    @classmethod
    def tearDownClass(cls):
        try:cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:cls.admin.close()

    def setUp(self):
        raw=connect_offline(cluster_dir=self.cluster,port=self.port,database=self.database);raw.row_factory=sqlite_compatible_dict_row
        self.conn=LanSharePostgresConnection(raw);self.addCleanup(self.conn.close)
        self.assertEqual(self.database,self.conn.execute('SELECT current_database()').fetchone()[0])
        self.conn.execute('DROP SCHEMA public CASCADE');self.conn.execute('CREATE SCHEMA public')
        self.conn.execute("CREATE TABLE teachers(id INTEGER PRIMARY KEY,school_code TEXT,school_name TEXT,college TEXT,department TEXT)")
        self.conn.execute("INSERT INTO teachers VALUES(1,'gxufl','合成学校','',''),(2,'other','另一个学校','','')")
        columns=["id INTEGER PRIMARY KEY","teacher_id INTEGER","school_code TEXT","source TEXT DEFAULT 'gxufl_jwxt'","sync_status TEXT DEFAULT 'active'","synced_at TEXT DEFAULT ''"]
        for name,field in service.AcademicTeachingPlace.__dataclass_fields__.items():
            kind='INTEGER DEFAULT 0' if name in {'seat_count','scheduling_seat_count','exam_seat_count','is_schedulable','is_borrowable','is_exam_schedulable','conflict_ignored'} else "TEXT DEFAULT ''"
            columns.append(name+' '+kind)
        self.conn.execute('CREATE TABLE teacher_academic_teaching_places('+','.join(columns)+')')
        for rid,teacher,school,name in [(1,1,'gxufl','知新楼\tB　310'),(2,2,'other','知新楼B310')]:
            self.conn.execute('''INSERT INTO teacher_academic_teaching_places(id,teacher_id,school_code,place_key,place_id,room_code,room_name,room_full_name,campus_id,campus_name,building_id,building_name,room_type_id,room_type_name,is_schedulable)
                VALUES (?,?,?,?,?,'B310',?,?,'1','五合校区','136','知新楼','03','实验室',1)''',
                (rid,teacher,school,f'{school}-B310',f'{school}-B310',name,name))
        self.conn.commit()

    def test_search_filters_execute_on_native_pg_and_preserve_school_scope(self):
        for query in ('B310','知新楼 B310','知新楼\tB　310'):
            with self.subTest(query=query):
                self.assertEqual(1,service.count_teacher_teaching_places(self.conn,1,search=query,campus_id='1'))
                rows=service.load_teacher_teaching_places(self.conn,1,search=query,campus_id='1')
                self.assertEqual(['gxufl-B310'],[row['place_id'] for row in rows])
        self.assertEqual(['other-B310'],[row['place_id'] for row in service.load_teacher_teaching_places(self.conn,2,search='B310')])

    def test_exact_free_query_reaches_upstream_after_real_pg_target_resolution(self):
        calls=[]
        def response(request):
            if request.method=='POST':
                from urllib.parse import parse_qs
                calls.append(parse_qs(request.content.decode(),keep_blank_values=True))
                return httpx.Response(200,json={'items':[{'cd_id':'gxufl-B310','cdbh':'B310','cdmc':'知新楼B310','xqh_id':'1'}],'totalResult':1,'totalPage':1})
            return httpx.Response(200,text='<html></html>')
        @asynccontextmanager
        async def opened(_credential):
            async with httpx.AsyncClient(base_url='https://academic.synthetic.invalid',transport=httpx.MockTransport(response)) as client:
                yield client,SimpleNamespace(base_url=client.base_url,school_code='gxufl'),{}
        @contextmanager
        def db():yield self.conn
        with patch.object(service,'get_db_connection',db),patch.object(service,'load_teacher_academic_access_method',return_value={'synthetic':True}),patch.object(service,'open_authenticated_academic_client',opened):
            result=asyncio.run(service.query_free_classrooms_from_academic_system(1,{'xnm':'2026','xqm':'3','xqh_id':'1','cdmc':'B310','weeks':[6],'weekday':[4],'sections':[2,3]}))
        self.assertEqual('success',result['status'],result)
        self.assertEqual('gxufl-B310',result['items'][0]['place_id'])
        self.assertEqual(['32'],calls[0]['zcd']);self.assertEqual(['6'],calls[0]['jcd']);self.assertEqual(['4'],calls[0]['xqj'])

    def test_repeated_slot_verdict_upsert_updates_one_native_pg_row(self):
        from classroom_app.db import schema_schedule_availability as schema
        from classroom_app.services.schedule_availability_service import record_room_slot_check
        with patch.object(schema,'_SCHEMA_READY',False):
            values=dict(year='2026-2027',term='1',room_id='gxufl-B310',room_name='合成B310',week=6,weekday=4,sections=[2,3],school_code='gxufl')
            record_room_slot_check(self.conn,**values,status='busy',detail='完整时段不可用')
            self.conn.commit()
            record_room_slot_check(self.conn,**values,status='free',detail='第二次查询空闲')
            self.conn.commit()
            rows=self.conn.execute('SELECT status,detail FROM academic_room_slot_checks').fetchall()
            self.assertEqual(1,len(rows));self.assertEqual('free',rows[0]['status'])
            self.assertEqual('第二次查询空闲',rows[0]['detail'])

if __name__=='__main__':unittest.main()
