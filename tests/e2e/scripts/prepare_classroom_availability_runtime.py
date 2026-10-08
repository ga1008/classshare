"""Seed rooms and one official lesson in an explicitly owned synthetic runtime."""
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-root',required=True,type=Path);args=parser.parse_args()
    runtime=args.runtime_root.resolve();assert runtime.is_relative_to(ROOT/'.codex-temp') and runtime!=ROOT/'.codex-temp'
    fp=runtime/'fixture.json';f=json.loads(fp.read_text('utf-8'))
    assert f.get('uiV3Synthetic') is True and Path(f['databasePath']).resolve()==runtime/'db/classroom.db'
    from tools.isolated_environment import isolate_sqlite_environment,guard_dotenv_loading,guard_postgres_connections
    isolate_sqlite_environment(runtime);guard_dotenv_loading();guard_postgres_connections()
    from classroom_app.services.academic_schedule_prediction_service import claim_schedule_sync,reconcile_and_publish_snapshot,release_schedule_sync
    with sqlite3.connect(f['databasePath']) as conn:
        conn.row_factory=sqlite3.Row
        teacher,oid=f['teacher']['id'],f['classOfferingId']
        offering=conn.execute('SELECT * FROM class_offerings WHERE id=?',(oid,)).fetchone();semester=offering['semester_id']
        conn.execute("UPDATE academic_semesters SET name='2026-2027第一学期',start_date='2026-08-31',end_date='2027-01-17',week_count=20 WHERE id=?",(semester,))
        conn.execute("UPDATE courses SET academic_course_code='QA-ROOM' WHERE id=?",(offering['course_id'],))
        conn.execute("UPDATE class_offerings SET academic_teaching_class_id='QA-ROOM-A',academic_teaching_class_name='教室查询合成班' WHERE id=?",(oid,))
        sid=conn.execute('SELECT id FROM class_offering_sessions WHERE class_offering_id=? ORDER BY order_index LIMIT 1',(oid,)).fetchone()[0]
        conn.execute("UPDATE class_offering_sessions SET session_date='2026-10-09',week_index=6,weekday=4,academic_section_text='4-5',academic_location='知新楼B310' WHERE id=?",(sid,))
        school=conn.execute('SELECT school_code FROM teachers WHERE id=?',(teacher,)).fetchone()[0]
        for rid,name,code in [('136B310','知新楼B310','B310'),('136B312','知新楼B312','B312')]:
            conn.execute('''INSERT INTO teacher_academic_teaching_places
                (teacher_id,school_code,source,place_key,place_id,room_code,room_name,room_full_name,campus_id,campus_name,building_id,building_name,room_type_id,room_type_name,seat_count,is_schedulable,sync_status)
                VALUES (?,?,'gxufl_jwxt',?,?,?,?,?,'1','五合校区','136','知新楼','03','实验室',62,1,'active')''',
                (teacher,school,rid,rid,code,name,name))
        lease=claim_schedule_sync(conn,teacher);conn.commit()
        snapshot={'official':[{'teaching_class_id':'QA-ROOM-A','teaching_class_name':'教室查询合成班','course_code':'QA-ROOM','course_name':'计算机网络原理','class_label':'合成教室查询班','date':'2026-10-09','week':6,'weekday':5,'sections':[4,5],'room':'知新楼B310'}],'requests':[],'source_summary':[]}
        reconcile_and_publish_snapshot(conn,teacher,semester,snapshot,lease['token']);release_schedule_sync(conn,teacher,lease['token']);conn.commit()
    f['classroomAvailability']={'synthetic':True,'semesterId':semester,'sessionId':sid,'year':'2026-2027','term':'1'}
    fp.write_text(json.dumps(f,ensure_ascii=False,indent=2),'utf-8')
    (runtime/'availability-control.json').write_text(json.dumps({'mode':'success'}),'utf-8')
    print(json.dumps({'synthetic':True,'semesterId':semester,'sessionId':sid}))

if __name__=='__main__':main()
