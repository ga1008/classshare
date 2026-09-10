#!/usr/bin/env python3
"""Runs only inside the dedicated E2E app/database; no credential output."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.dsh_e2e_cohort import names


def guard():
    from urllib.parse import urlsplit
    url=urlsplit(os.environ.get('DATABASE_URL',''))
    cohort=names(os.environ.get('DSH_E2E_COHORT'))
    if url.hostname!=cohort['pg'] or url.path!='/'+cohort['database'] or url.username!='e2e_app':
        raise RuntimeError('App helper must use its exact unprivileged cohort database')
    if os.environ.get('MAIN_DATA_DIR')!='/e2e-data':
        raise RuntimeError('App helper must use its isolated data mount')


def seed():
    from classroom_app.database import init_database, get_db_connection
    from classroom_app.dependencies import get_password_hash
    init_database()
    fixture=json.loads(Path('/e2e-private/fixture.json').read_text())
    model=json.loads(Path('/e2e-private/model-import.json').read_text())
    with get_db_connection() as conn:
        assert not conn.execute('SELECT id FROM teachers LIMIT 1').fetchone()
        assert not conn.execute('SELECT id FROM students LIMIT 1').fetchone()
        for kind in ('teacher','admin'):
            user=fixture[kind]
            conn.execute('INSERT INTO teachers(id,name,email,hashed_password,is_super_admin,is_active,school_code,school_name,college,department) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (user['id'],'DSH E2E '+kind,user['email'],get_password_hash(user['password']),int(kind=='admin'),1,'dsh-e2e','DSH E2E School','E2E College','E2E Department'))
        conn.execute("INSERT INTO classes(id,name,created_by_teacher_id,school_code,school_name,college,department) VALUES(900001,'DSH E2E class',900001,'dsh-e2e','DSH E2E School','E2E College','E2E Department')")
        user=fixture['student']
        conn.execute('INSERT INTO students(id,student_id_number,name,class_id,hashed_password,password_reset_required,enrollment_status,school_code,school_name,college,department) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (user['id'],user['identifier'],'DSH E2E student',900001,get_password_hash(user['password']),0,'active','dsh-e2e','DSH E2E School','E2E College','E2E Department'))
        conn.execute("INSERT INTO agent_runtime_api_keys(provider,key_label,key_fingerprint,key_encrypted,key_suffix,base_url,model,enabled,is_active,last_test_status) VALUES('deepseek','Isolated E2E imported active key',?,?, '',?,?,1,1,'unchecked')",
                     (model['key_fingerprint'],model['key_encrypted'],model['base_url'],model['model']))
        conn.commit()
    print(json.dumps({'seeded':True,'teachers':2,'students':1,'classes':1,'real_sessions':0,'key_probe_status':'unchecked until actual requests'}))


def execute(kind):
    import httpx
    from classroom_app.database import get_db_connection
    from agent_task_worker import _run_once
    fixture=json.loads(Path('/e2e-private/fixture.json').read_text())[kind]
    # One actor execution per cohort. A failed/uncertain run is preserved and
    # must not silently create another billed task under the same evidence name.
    marker=Path('/e2e-data')/('e2e-'+kind+'-started.json')
    with marker.open('x',encoding='utf-8') as stream:
        json.dump({'actor':kind,'cohort':os.environ['DSH_E2E_COHORT'],'started_at':time.time()},stream)
    instructions={
        'teacher':'这是隔离环境的端到端验收。请立即实际执行一次 create_blog_draft，标题严格为 DSH-E2E-Teacher-Draft，正文为“DSH 官方 Agent 隔离验收草稿”，不要发布。通过平台能力目录确认参数，并用 platform_read 读取我本人的课堂列表。把草稿业务回执和课堂读取结果写入 workspace 文件 e2e-teacher.md。不要仅提案；这是已授权的实际草稿创建请求。所有写操作仅限此草稿。',
        'student':'这是隔离环境的端到端验收。请实际通过平台能力目录和 platform_read 读取我本人的课堂列表 classroom.my_courses 以及我的消息统计 messages.summary。将真实返回的结果和当前学生身份写入 workspace 文件 e2e-student.md，简短汇报。不要写入任何平台业务数据，不要请求管理员数据。',
        'admin':'这是隔离环境的端到端验收。请实际执行一次 create_organization_school，school_code 严格为 dsh-e2e-created，school_name 严格为 DSH E2E Created School。通过平台能力目录确认参数。再用管理员组织或身份读取能力读取可见组织或教师列表以确认管理员身份。将真实业务回执写入 workspace 文件 e2e-admin.md。不要仅提案；这是已授权的实际创建请求。只创建这一个测试学校。'}
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=30,follow_redirects=False) as client:
        if kind=='student':
            login=client.post('/api/student/login/password',data={'identifier':fixture['identifier'],'password':fixture['password']})
            assert login.status_code==200, f'Isolated student login status {login.status_code}'
        else:
            login=client.post('/teacher/login',data={'email':fixture['email'],'password':fixture['password']})
            assert login.status_code==303, f'Isolated teacher login status {login.status_code}'
        assert client.cookies.get('access_token')
        created=client.post('/api/agent-tasks',json={'instruction':instructions[kind],'task_type':'general_teaching_task','deep_thinking':False,'no_history':True})
        if created.status_code!=200:
            Path('/e2e-data/e2e-create-error.json').write_text(created.text)
        assert created.status_code==200, f'Task creation status {created.status_code}'
        data=created.json(); task=data.get('task') or data
        task_id=int(task['id'])
        assert _run_once('isolated-e2e-manual-'+kind,cleanup=False)
        response=client.get('/api/agent-tasks/'+str(task_id))
        Path('/e2e-data/e2e-'+kind+'-task.json').write_text(json.dumps(response.json(),ensure_ascii=False,indent=2))
        with get_db_connection() as conn:
            stored=dict(conn.execute('SELECT id,status,error_message,result_summary FROM agent_tasks WHERE id=?',(task_id,)).fetchone())
            model=[dict(row) for row in conn.execute('SELECT model,status,upstream_status,input_tokens,output_tokens,config_generation FROM agent_model_requests WHERE task_id=? ORDER BY created_at',(task_id,)).fetchall()]
            operations=[dict(row) for row in conn.execute('SELECT operation_id,action,status,result_json FROM agent_action_executions WHERE task_id=?',(task_id,)).fetchall()]
        result={'actor':kind,'task':stored,'model_requests':model,'operations':operations,'normal_login':True}
        Path('/e2e-data/e2e-'+kind+'-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False))


def report():
    from classroom_app.database import get_db_connection
    result={}
    for kind in ('teacher','student','admin'):
        path=Path('/e2e-data/e2e-'+kind+'-evidence.json')
        if path.exists():
            item=json.loads(path.read_text())
            task_id=item['task']['id']
            with get_db_connection() as conn:
                detail=json.loads(conn.execute('SELECT result_detail_json FROM agent_tasks WHERE id=?',(task_id,)).fetchone()[0] or '{}')
                if kind=='teacher':
                    item['actual_business_rows']=[dict(row) for row in conn.execute("SELECT id,author_identity,status,title FROM blog_posts WHERE title='DSH-E2E-Teacher-Draft'").fetchall()]
                elif kind=='admin':
                    item['actual_business_rows']=[dict(row) for row in conn.execute("SELECT school_code,school_name,is_active FROM organization_schools WHERE school_code='dsh-e2e-created'").fetchall()]
                else:
                    item['business_write_receipts']=conn.execute('SELECT count(*) FROM agent_action_executions WHERE task_id=?',(task_id,)).fetchone()[0]
                    item['http_mutation_admissions']=conn.execute('SELECT count(*) FROM agent_platform_requests WHERE task_id=? AND mutates=1',(task_id,)).fetchone()[0]
                item['source_session_bound']=bool(conn.execute('SELECT source_session_hash FROM agent_tasks WHERE id=?',(task_id,)).fetchone()[0])
            item['completion_kind']=detail.get('completion_kind')
            item['tool_receipt_summary']=[{'title':r.get('title'),'status':r.get('status'),'kind':r.get('kind')} for r in detail.get('tool_receipts',[])]
            target=Path('/e2e-data/agent_tasks/tasks')/str(task_id)/('e2e-'+kind+'.md')
            item['file']={'exists':target.is_file(),'sha256':hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None,
                          'size':target.stat().st_size if target.is_file() else None}
            item['checks']={'completed':item['task']['status']=='completed',
                            'real_model_success':bool(item['model_requests']) and all(r['status']=='completed' and r['upstream_status']==200 for r in item['model_requests']),
                            'file_created':item['file']['exists'],'live_session_source':item['source_session_bound']}
            item['checks']['platform_read_completed']=any(r.get('title')=='mcp__lanshare__platform_read' and r.get('status')=='completed' for r in item['tool_receipt_summary'])
            if kind=='teacher':
                item['checks']['own_single_unpublished_draft']=len(item['actual_business_rows'])==1 and item['actual_business_rows'][0]['author_identity']=='teacher:900001' and item['actual_business_rows'][0]['status']=='draft'
            elif kind=='admin':
                item['checks']['single_synthetic_school']=len(item['actual_business_rows'])==1 and item['actual_business_rows'][0]['school_name']=='DSH E2E Created School'
            else: item['checks']['read_only_student']=item['business_write_receipts']==0 and item['http_mutation_admissions']==0
            result[kind]=item
    Path('/e2e-data/e2e-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
    if set(result)!={'teacher','student','admin'} or not all(all(item['checks'].values()) for item in result.values()):
        raise RuntimeError('Isolated verification failed; inspect evidence')


def boundaries():
    import httpx
    from classroom_app.database import get_db_connection
    fixture=json.loads(Path('/e2e-private/fixture.json').read_text())
    teacher_id=int(json.loads(Path('/e2e-data/e2e-teacher-evidence.json').read_text())['task']['id'])
    student_id=int(json.loads(Path('/e2e-data/e2e-student-evidence.json').read_text())['task']['id'])
    result={'checks':{}}
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=15) as client:
        user=fixture['student']
        assert client.post('/api/student/login/password',data={'identifier':user['identifier'],'password':user['password']}).status_code==200
        own=client.get(f'/api/agent-tasks/{student_id}/artifacts/e2e-student.md')
        other=client.get(f'/api/agent-tasks/{teacher_id}/artifacts/e2e-teacher.md')
        summary=client.get(f'/api/agent-tasks/{teacher_id}').json()['task']
        result['checks']['student_downloads_own_file']=own.status_code==200 and own.content==(Path('/e2e-data/agent_tasks/tasks')/str(student_id)/'e2e-student.md').read_bytes()
        result['checks']['same_id_other_role_file_denied']=other.status_code==403
        result['checks']['same_id_other_role_private_detail_hidden']=summary['is_owner'] is False and not set(summary)&{'private_instruction','result_detail','events','context_snapshot'}
    with get_db_connection() as conn:
        role=dict(conn.execute('SELECT rolname,rolsuper,rolcreaterole,rolcreatedb,rolreplication FROM pg_roles WHERE rolname=current_user').fetchone())
        result['database_role']=role
        result['checks']['database_role_unprivileged']=all(not role[name] for name in ('rolsuper','rolcreaterole','rolcreatedb','rolreplication'))
        result['checks']['no_connect_other_databases']=not conn.execute("SELECT has_database_privilege(current_user,'postgres','CONNECT') OR has_database_privilege(current_user,'template1','CONNECT')").fetchone()[0]
        result['checks']['no_active_delegations']=conn.execute("SELECT count(*) FROM agent_task_delegations WHERE status='active'").fetchone()[0]==0
        result['checks']['no_running_attempts']=conn.execute("SELECT count(*) FROM agent_task_attempts WHERE status='running'").fetchone()[0]==0
        result['checks']['no_active_request_leases']=conn.execute("SELECT count(*) FROM agent_request_budget_leases WHERE status='active'").fetchone()[0]==0
        result['counts']={table:conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('agent_tasks','agent_task_attempts','agent_task_delegations','agent_action_executions','agent_model_requests')}
    Path('/e2e-data/e2e-lifecycle-boundaries.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
    if not all(result['checks'].values()): raise RuntimeError('Lifecycle boundary check failed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['seed','teacher','student','admin','report','boundaries'])
    phase=parser.parse_args().phase
    guard()
    if phase=='seed': seed()
    elif phase=='report': report()
    elif phase=='boundaries': boundaries()
    else: execute(phase)
