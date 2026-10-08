"""Seed selection/schedule QA in a new owned SQLite runtime; no remote services."""
from __future__ import annotations
import argparse
import copy
from datetime import date, timedelta
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-root',required=True,type=Path);args=parser.parse_args()
    runtime=args.runtime_root.resolve()
    if runtime==ROOT/'.codex-temp' or not runtime.is_relative_to(ROOT/'.codex-temp'):raise ValueError('Owned child runtime required')
    fp=runtime/'fixture.json';f=json.loads(fp.read_text(encoding='utf-8'))
    if not f.get('uiV3Synthetic') or not f.get('lqS3Synthetic') or Path(f['databasePath']).resolve()!=runtime/'db/classroom.db':raise ValueError('Synthetic identity mismatch')
    if f.get('selectSchedule'):raise ValueError('Use a new synthetic runtime, already seeded')
    from tools.isolated_environment import isolate_sqlite_environment,guard_dotenv_loading,guard_postgres_connections
    isolate_sqlite_environment(runtime);guard_dotenv_loading();guard_postgres_connections()
    from classroom_app.services.academic_schedule_prediction_service import claim_schedule_sync,reconcile_and_publish_snapshot,release_schedule_sync
    with sqlite3.connect(f['databasePath']) as conn:
        conn.row_factory=sqlite3.Row
        teacher,offering,course=f['teacher']['id'],f['classOfferingId'],f['courseId']
        owner=dict(conn.execute('SELECT * FROM class_offerings WHERE id=?',(offering,)).fetchone())
        semester=dict(conn.execute('SELECT * FROM academic_semesters WHERE id=?',(owner['semester_id'],)).fetchone())
        anchor=date.fromisoformat(semester['start_date']);monday=anchor-timedelta(days=anchor.weekday())
        long_class='人工智能2601班（专升本） · 人工智能2602班（专升本） · 人工智能2603班（专升本） · 软件工程2601班（专升本） · 网络工程2601班（专升本）'
        conn.execute('UPDATE classes SET name=? WHERE id=?',(long_class,owner['class_id']))
        conn.execute('UPDATE courses SET academic_course_code=? WHERE id=?',('QA-SELECT',course))
        conn.execute('UPDATE class_offerings SET academic_teaching_class_id=?,academic_teaching_class_name=?,combined_class_names=? WHERE id=?',('QA-SELECT-A','合成选择验收教学班',long_class,offering))
        identity={'teaching_class_id':'QA-SELECT-A','teaching_class_name':'合成选择验收教学班','course_code':'QA-SELECT','course_name':'计算机网络原理','class_label':long_class}
        def slot(day,sections,room='合成五合校区 B310'):
            d=date.fromisoformat(day) if isinstance(day,str) else day
            return {'date':d.isoformat(),'week':(d-monday).days//7+1,'weekday':d.isoweekday(),'sections':sections,'room':room}
        rows=[dict(r) for r in conn.execute('SELECT * FROM class_offering_sessions WHERE class_offering_id=? ORDER BY order_index',(offering,))]
        official=[]
        for row in rows:
            numbers=[int(n) for n in re.findall(r'\d+',row['academic_section_text'] or '')]
            sections=list(range(numbers[0],numbers[-1]+1)) if len(numbers)==2 else numbers or [4,5]
            official.append({**identity,**slot(row['session_date'],sections,row['academic_location'] or '合成五合校区 B310')})
        changes={}
        for key,index,target_week,sections in [('planned',2,7,[2,3]),('pending',4,8,[6,7]),('approved',6,9,[8,9])]:
            original={k:official[index][k] for k in ['date','week','weekday','sections','room']}
            proposed=slot(monday+timedelta(weeks=target_week-1,days=0),sections)
            changes[key]={'request_id':'QA-SELECT-'+key.upper(),'serial':'QA-'+key,'status':'pending','raw_status':'1','kind':'move','reason':'合成验收：'+key,'applied_at':'2026-10-08 10:00:00',**identity,'details':[{'detail_id':'QA-SELECT-'+key+'-1','original':original,'proposed':proposed}]}
        def publish(snapshot):
            lease=claim_schedule_sync(conn,teacher)
            if lease.get('status')!='claimed':raise RuntimeError('Synthetic lease unexpectedly busy')
            conn.commit()
            result=reconcile_and_publish_snapshot(conn,teacher,semester['id'],snapshot,lease['token'])
            release_schedule_sync(conn,teacher,lease['token']);conn.commit();return result
        publish({'official':official,'requests':list(changes.values()),'source_summary':[]})
        effective=copy.deepcopy(official)
        effective[6].update(changes['approved']['details'][0]['proposed'])
        for key in ['planned','approved']:changes[key].update(status='approved',raw_status='3')
        result=publish({'official':effective,'requests':list(changes.values()),'source_summary':[]})
        year=monday.year if monday.month>=8 else monday.year-1
        f['selectSchedule']={'synthetic':True,'semesterId':semester['id'],'longClassLabel':long_class,'courseName':identity['course_name'],'year':f'{year}-{year+1}','term':'1','week1Monday':monday.isoformat(),'officialCount':len(official),'sessionIds':[r['id'] for r in rows],'changes':changes,'publication':result}
        fp.write_text(json.dumps(f,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'synthetic':True,'officialCount':len(official),'phases':list(changes)},ensure_ascii=False))

if __name__=='__main__':main()
