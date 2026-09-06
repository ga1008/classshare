"""Career HTTP, concurrency and source-contract probe in an ephemeral local PG schema.

Reuses the resume probe's local-only connection boundary. All students, source
records and model outputs are synthetic; nothing is imported to application
tables. Run python -B tools/career_postgres_workflow_probe.py.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

import psycopg
from fastapi import Request
from fastapi.testclient import TestClient
from tools.resume_postgres_workflow_probe import isolated_resume_postgres
from classroom_app import config
from classroom_app.db import schema_career_path
from classroom_app.dependencies import get_current_user
from classroom_app.routers import career_path as routes
from classroom_app.services import career_path_service as career, career_lifecycle_service as lifecycle
from classroom_app.services import career_job_posting_service as postings
from classroom_app.services import ai_durable_job_service as durable, student_career_job_worker as worker
from classroom_app.services.career_recommendation_service import baseline_network


@contextmanager
def isolated_career_postgres(*,students=100):
    with ExitStack() as stack:
        # Resume fixture material creation already invokes the career invalidation
        # hook, so its dialect must be patched before entering that fixture.
        for module in (schema_career_path,lifecycle,postings):
            stack.enter_context(patch.object(module,"get_configured_db_engine",return_value="postgres"))
        fixture=stack.enter_context(isolated_resume_postgres())
        connect=fixture["connect"]
        stack.enter_context(patch.object(routes,"get_db_connection",connect))
        stack.enter_context(patch.object(career,"get_db_connection",connect))
        stack.enter_context(patch("classroom_app.database.get_db_connection",connect))
        stack.enter_context(patch.object(routes,"record_student_career_event_safely",return_value=None))
        schema_career_path._SCHEMA_READY=False
        with connect() as conn:
            schema_career_path.ensure_career_path_schema(conn)
            conn.execute("""CREATE TABLE classes(id BIGINT PRIMARY KEY,name TEXT,major TEXT,academic_major TEXT,
                academic_class_code TEXT,academic_class_name TEXT,academic_grade TEXT,enrollment_year INTEGER,
                expected_graduation_year INTEGER,program_duration_years INTEGER,college TEXT,department TEXT)""")
            conn.execute("""CREATE TABLE students(id BIGINT PRIMARY KEY,name TEXT,gender TEXT,class_id BIGINT,
                school_code TEXT,school_name TEXT,academic_major TEXT,academic_grade TEXT,description TEXT,
                nickname TEXT,today_mood TEXT,enrollment_status TEXT)""")
            conn.execute("INSERT INTO classes(id,name,major,program_duration_years) VALUES(1,'合成语言班2401','英语',4)")
            conn.executemany("""INSERT INTO students(id,name,gender,class_id,school_code,school_name,academic_grade)
                VALUES(?,?,'',1,'career-probe','合成大学','2024')""",[(sid,f"合成学生{sid}") for sid in range(1,students+1)])
            conn.execute("""INSERT INTO students(id,name,gender,class_id,school_code,school_name,academic_grade)
                VALUES(100001,'其他学校合成学生','',1,'other-school','其他合成大学','2024')""")
            conn.commit()
        fixture["app"].include_router(routes.router)
        def identity(request:Request):
            return {"id":int(request.headers.get("x-probe-student","1")),"role":request.headers.get("x-probe-role","student")}
        fixture["app"].dependency_overrides[get_current_user]=identity
        try:yield fixture
        finally:
            schema_career_path._SCHEMA_READY=False
            lifecycle._cached_recommend.cache_clear();lifecycle._validated_graph.cache_clear();postings._match.cache_clear()


def _answers(ctx):
    result=[]
    for question in career.get_questions(mode="quick",major_key=ctx["major_key"]):
        if question["kind"]=="single":value=question["options"][0]["value"]
        elif question["kind"]=="multi":value=[question["options"][0]["value"]]
        elif question["kind"]=="scale":value=3
        else:value=""
        result.append({"question_id":question["id"],"value":value})
    return result


def _timing(values):
    values=sorted(values)
    return {"p50_ms":round(statistics.median(values),3),"p95_ms":round(values[int((len(values)-1)*.95)],3),
            "p99_ms":round(values[int((len(values)-1)*.99)],3),"maximum_ms":round(max(values),3)}


def _posting(external_id,*,school_code="",city="南宁",status="open"):
    now=datetime.now(timezone.utc)
    return {"source":"isolated-pg-source","external_id":external_id,"source_url":"https://example.invalid/jobs/"+external_id,
            "title":"合成翻译岗位","company":"合成机构","city":city,"school_code":school_code,"status":status,
            "job_description":"岗位要求本科及以上学历，负责英语翻译与内容校对，需要整理资料并配合团队完成文案工作。",
            "checked_at":(now-timedelta(hours=1)).isoformat(),"expires_at":(now+timedelta(days=7)).isoformat()}


def run(*,students=100,polls=1000,threads=20):
    checks=[];times={}
    with isolated_career_postgres(students=students) as fixture, TestClient(fixture["app"]) as client:
        schema=fixture["schema"];connect=fixture["connect"]
        def request(method,path,*,student=1,body=None,status=200,params=None):
            response=client.request(method,"/api/career-path/"+path,json=body,params=params,headers={"x-probe-student":str(student)})
            assert response.status_code==status,(path,response.status_code,response.text[:500])
            return response
        def initialize(sid):
            started=time.perf_counter();result=request("POST","initialize",student=sid).json()
            assert result["network"]["nodes"] and result["phase"]=="intro"
            return result,(time.perf_counter()-started)*1000
        with ThreadPoolExecutor(max_workers=threads) as pool:
            initialized=list(pool.map(initialize,range(1,students+1)))
        times["initialize_http"]=_timing([elapsed for _,elapsed in initialized])
        ids={state["tasks"]["network"]["id"] for state,_ in initialized};assert len(ids)==1,ids
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM ai_jobs WHERE task_type=?",(career.NETWORK_GENERATE_TASK_KIND,)).fetchone()[0]==1
            assert conn.execute("SELECT COUNT(*) FROM career_student_sessions").fetchone()[0]==students
            before=[tuple(row.values()) for row in conn.execute("SELECT student_id,revision,updated_at FROM career_student_sessions ORDER BY student_id")]
        checks += ["same_major_concurrent_initialize_single_job","one_session_per_student"]
        print(f"PG career initialize: {students} students / one shared job",flush=True)
        @contextmanager
        def readonly_connect():
            with connect() as conn:
                conn.execute("SET TRANSACTION READ ONLY")
                yield conn
        def read(index):
            sid=index%students+1;known=initialized[sid-1][0]["result_version"]
            started=time.perf_counter();response=request("GET","state",student=sid,params={"known_result_version":known})
            payload=response.json();assert payload["network_unchanged"] and "network" not in payload
            assert payload["student"]["name"]==f"合成学生{sid}"
            return (time.perf_counter()-started)*1000,len(response.content)
        with patch.object(routes,"get_db_connection",readonly_connect),ThreadPoolExecutor(max_workers=threads) as pool:
            responses=list(pool.map(read,range(polls)))
        times["metadata_http"]={**_timing([elapsed for elapsed,_ in responses]),"response_bytes_max":max(size for _,size in responses)}
        with connect() as conn:
            after=[tuple(row.values()) for row in conn.execute("SELECT student_id,revision,updated_at FROM career_student_sessions ORDER BY student_id")]
            assert after==before
        checks += ["readonly_pg_state_no_ddl_no_dml","metadata_poll_private_result_isolation"]
        print(f"PG career read-only metadata: {polls} successful responses",flush=True)
        state=request("GET","state").json()
        with connect() as conn:ctx=career.resolve_student_context(conn,1)
        body={"answers":_answers(ctx),"mode":"quick","quiz_version":career.QUIZ_VERSION,"revision":state["revision"]}
        submitted=request("POST","answers",body=body).json()["state"];assert submitted["phase"]=="ready"
        request("POST","progress",body=body,status=409)
        checks += ["quiz_ready_before_ai","quiz_revision_conflict_pg"]
        async def research(**kwargs):return {"digest":"合成结构测试，无招聘市场断言","queries":[]}
        async def model(*args,**kwargs):return baseline_network("英语")
        with patch.object(career.ai_web_research,"gather",side_effect=research),patch.object(career,"_call_career_ai",side_effect=model):
            claimed=durable.claim_due_ai_jobs(limit=1,worker_id="career-pg-probe",task_types=(career.NETWORK_GENERATE_TASK_KIND,))
            assert len(claimed)==1
            asyncio.run(worker._execute(claimed[0]))
            delivery=durable.claim_result_ready_ai_jobs(limit=1,worker_id="career-pg-delivery",task_types=(career.NETWORK_GENERATE_TASK_KIND,))
            assert len(delivery)==1 and worker._apply_result(delivery[0])
        published=request("GET","state").json();assert published["network_version"].startswith("network:")
        checks.append("durable_network_candidate_atomic_publication")
        with tempfile.TemporaryDirectory(prefix="career-pg-import-") as directory:
            from tools.career_catalog_import import main as import_catalog
            source=Path(directory)/"reviewed.json"
            rows=[_posting("public"),_posting("private",school_code="career-probe"),_posting("other",school_code="other-school"),
                  _posting("closed",status="closed"),_posting("remote",city="深圳")]
            data={"aliases":[{"school_code":"career-probe","alias_name":"English","canonical_name":"英语","reason":"合成名称映射"}],"postings":rows}
            source.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
            with patch("sys.argv",["career_catalog_import",str(source)]),redirect_stdout(io.StringIO()):import_catalog()
            with connect() as conn:assert conn.execute("SELECT COUNT(*) FROM career_job_postings").fetchone()[0]==0
            with patch("sys.argv",["career_catalog_import",str(source),"--apply"]),redirect_stdout(io.StringIO()):import_catalog()
            with patch("sys.argv",["career_catalog_import",str(source),"--apply"]),redirect_stdout(io.StringIO()):import_catalog()
            with connect() as conn:
                assert conn.execute("SELECT COUNT(*) FROM career_job_postings").fetchone()[0]==5
                assert conn.execute("SELECT MAX(version) FROM career_job_postings").fetchone()[0]==1
                conn.execute("UPDATE students SET academic_major='English（专升本）' WHERE id=?",(students,))
                conn.execute("INSERT INTO resume_educations(student_id,degree,major,end_date) VALUES(1,'专科','英语','2025-06')")
                conn.commit()
            alias=request("POST","initialize",student=students).json()
            assert alias["major"]["id"]==published["major"]["id"] and alias["timeline"]["program_pathway"]=="top_up"
            visible=request("GET","job-postings",params={"city":"南宁","keyword":"翻译"}).json()
            assert {item["external_id"] for item in visible["items"]}=={"public","private"}
            assert not request("GET","job-postings",params={"city":"南宁","qualification":"no_known_gaps"}).json()["items"]
            other=request("GET","job-postings",student=100001,params={"city":"南宁"}).json()
            assert {item["external_id"] for item in other["items"]}=={"public","other"}
            private=next(item for item in visible["items"] if item["external_id"]=="private")
            request("POST",f"job-postings/{private['id']}/target",student=100001,status=404)
            target=request("POST",f"job-postings/{private['id']}/target").json()
            repeated=request("POST",f"job-postings/{private['id']}/target").json()
            assert target["job_target_id"]==repeated["job_target_id"]
            assert target["item"]["analysis"]["posting_source"]["version"]==1
            with connect() as conn:
                conn.execute("UPDATE career_job_postings SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(private["id"],));conn.commit()
            request("POST",f"job-postings/{private['id']}/target",status=404)
            assert {item["external_id"] for item in request("GET","job-postings",params={"city":"南宁"}).json()["items"]}=={"public"}
            data={"network_restores":[{"school_code":"career-probe","major_key":"英语","revision":1,"reason":"合成旧版本恢复"}]}
            source.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
            with patch("sys.argv",["career_catalog_import",str(source),"--apply"]),redirect_stdout(io.StringIO()):import_catalog()
            restored=request("GET","state").json();assert restored["network_version"].endswith(":2")
        checks += ["operator_dry_run_and_idempotent_apply_pg","alias_identity_preserves_pathway","source_city_qualification_filter_pg",
                   "school_scoped_vacancy_and_target_isolation","expired_source_unavailable","target_source_snapshot_and_idempotency",
                   "network_restore_appends_new_revision"]
        assert client.get("/api/career-path/state",headers={"x-probe-role":"teacher"}).status_code==403
        checks.append("nonstudent_api_rejected")
    with psycopg.connect(config.DATABASE_URL,connect_timeout=5) as admin:
        assert admin.execute("SELECT COUNT(*) FROM pg_namespace WHERE nspname=%s",(schema,)).fetchone()[0]==0
    return {"ok":True,"engine":"local PostgreSQL / real FastAPI HTTP","synthetic_only":True,
            "isolated_schema_removed":True,"students":students,"polls":polls,"threads":threads,"checks":checks,"timings":times,
            "measurement_scope":"Includes local new-connection overhead; synthetic model output; not production mixed-load or real-AI SLA"}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--students",type=int,default=100)
    parser.add_argument("--polls",type=int,default=1000)
    parser.add_argument("--threads",type=int,default=20)
    args=parser.parse_args()
    print(json.dumps(run(students=max(2,min(args.students,1000)),polls=max(1,min(args.polls,10000)),
                         threads=max(1,min(args.threads,30))),ensure_ascii=False,indent=2))
