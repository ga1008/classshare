"""Native PostgreSQL attendance concurrency probe in an isolated local schema.

Uses anonymous unit fixture DDL, real production services/SQL, concurrent physical
connections and a standalone real FastAPI router. No remote source/model calls,
application startup or public table writes. Temporary schema/blobs are removed.
"""
from __future__ import annotations

import ast
import io
import json
import re
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def validate():
    from classroom_app import config
    from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
    from classroom_app.db.schema_ai_jobs import ensure_ai_job_schema, reset_ai_job_schema_guard_for_tests
    from classroom_app.db.schema_attendance_reports import ensure_attendance_report_schema
    from classroom_app.services import attendance_report_service as service, ai_durable_job_service as jobs, file_service
    from classroom_app.services.smart_classroom_attendance_adapter import external_account_key
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from classroom_app.dependencies import get_current_user
    from classroom_app.routers import attendance_reports as routes
    import psycopg

    if config.DB_ENGINE != "postgres" or urlparse(config.DATABASE_URL).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("This probe requires the configured local PostgreSQL database")
    schema = "attendance_probe_" + uuid.uuid4().hex
    checks=[]
    def passed(name, condition=True):
        if not condition: raise RuntimeError("Attendance probe failed: " + name)
        checks.append({"name":name,"ok":True})
    def connect():
        raw=psycopg.connect(config.DATABASE_URL, connect_timeout=5, row_factory=sqlite_compatible_dict_row)
        raw.execute(f'SET search_path TO "{schema}", pg_catalog')
        raw.execute("SET statement_timeout TO '15s'")
        raw.execute("SET lock_timeout TO '5s'")
        raw.commit()
        return LanSharePostgresConnection(raw)
    admin=psycopg.connect(config.DATABASE_URL, autocommit=True, connect_timeout=5)
    created=False
    try:
        admin.execute(f'CREATE SCHEMA "{schema}"');created=True
        with tempfile.TemporaryDirectory(prefix="attendance-pg-") as folder, \
             patch.object(file_service,"GLOBAL_FILES_DIR",Path(folder)), \
             patch.object(file_service,"GLOBAL_FILES_LEGACY_DIRS",()), \
             patch.object(jobs,"get_db_connection",side_effect=connect), \
             patch.object(config,"ATTENDANCE_ARCHIVE_ENABLED",True), \
             patch.object(config,"ATTENDANCE_PARSE_ENABLED",True), \
             patch.object(config,"ATTENDANCE_CONFIRMED_FACTS_ENABLED",True):
            # Extract the anonymous fixture literal without importing tests (which
            # intentionally set DB_ENGINE=sqlite for their own process).
            tree=ast.parse((Path(__file__).resolve().parents[1]/"tests/test_attendance_report_service.py").read_text("utf-8"))
            ddl=next(ast.literal_eval(n.args[0]) for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="executescript")
            with connect() as conn:
                passed("isolated_search_path",conn.execute("SELECT current_schema()").fetchone()[0]==schema)
                for statement in ddl.split(";"):
                    if statement.strip():conn.execute(statement)
                reset_ai_job_schema_guard_for_tests()
                ensure_ai_job_schema(conn,engine="postgres")
                ensure_attendance_report_schema(conn,engine="postgres")
                ensure_attendance_report_schema(conn,engine="postgres")
                conn.commit()
                passed("native_schema_idempotent")
                user={"id":1,"role":"teacher"}
                option={"academic_year":"2025-2026","academic_term":2,"remote_schedule_id":"opaque-a","course_name":"合成课程","course_code":"SYN-1","teaching_class_name":"合成教学班","school_code":"gxufl","platform_code":"gxufl_smart_classroom","credential_id":50,"external_account_key":external_account_key({"username":"synthetic-teacher"})}
                binding=service.save_attendance_binding(conn,user,source_token=service.sign_attendance_source_option(1,option),class_offering_id=10)["binding"]
                conn.commit()
            def export(key):
                with connect() as conn:
                    result=service.enqueue_attendance_export(conn,user,binding_id=binding["id"],expected_binding_revision=binding["revision"],idempotency_key=key)
                    conn.commit();return result
            with ThreadPoolExecutor(max_workers=2) as pool:
                exports=list(pool.map(export,["concurrent-a","concurrent-b"]))
            original=exports[0]
            passed("concurrent_export_single_job",exports[0]["job_id"]==exports[1]["job_id"])
            with connect() as conn:
                extra,_=jobs.create_ai_job(conn,task_type="attendance_export",dedupe_key="independent-capacity-probe",payload={},owner_role="teacher",owner_user_pk=2)
                conn.commit()
            def claim(_):
                return jobs.claim_due_ai_jobs(task_types=("attendance_export",),max_running=1,concurrency_lock_key=0x415454455850,fair_owner=True)
            with ThreadPoolExecutor(max_workers=2) as pool:
                claims=list(pool.map(claim,[1,2]))
            passed("shared_export_capacity_one",sum(len(c) for c in claims)==1)
            job=next(c[0] for c in claims if c)
            passed("claim_preserves_oldest_owner_request",job["id"]==original["job_id"])
            with connect() as conn:
                conn.execute("UPDATE ai_jobs SET status='cancelled' WHERE id=?",(extra["id"],));conn.commit()
            stored=file_service.store_file_object_globally(io.BytesIO(b"%PDF-1.7\nanonymous native service fixture"))
            with connect() as conn:
                try:service.cache_attendance_source(conn,job["id"],"stale-token",{})
                except HTTPException as exc:passed("stale_lease_cannot_cache",exc.status_code==409);conn.rollback()
                else:passed("stale_lease_cannot_cache",False)
                parsed=service.cache_attendance_source(conn,job["id"],job["lease_token"],{"file_hash":stored["hash"],"byte_size":stored["size"],"page_count":1,"filename":"fixture.pdf","checkin_manifest":{}})
                conn.commit()
                recovered=service.completed_attendance_job_result(conn,job["id"],job["lease_token"])
                passed("cache_publication_recovery",recovered["source_version_id"]==original["source_version_id"])
                conn.execute("UPDATE ai_jobs SET status='succeeded' WHERE id=?",(job["id"],));conn.commit()
                passed("blob_reference_count",file_service.count_global_file_references(conn,stored["hash"])==1)
            parsejob=jobs.claim_due_ai_jobs(task_types=("attendance_parse",),max_running=1,concurrency_lock_key=0x415454504152,fair_owner=True)[0]
            result={"students":[{"row_index":i,"student_number":no,"source_name":"合成","source_class_name":"合成班"} for i,no in ((1,"000123"),(2,"90000000001"))],
                    "sessions":[{"column_index":1,"source_header":"03-09 08:00","source_datetime":"2026-03-09 08:00:00","section":1,"remote_checkin_id":"event-a"}],
                    "cells":[{"row_index":i,"column_index":1,"normalized_status":"CHECKED","quality_state":"verified","raw_text":"出勤"} for i in (1,2)],
                    "ai_used":True,"coverage":{"processed_pages":[1]},"ai_coverage":{"processed_pages":[1]},"validation":{"blockers":[],"warnings":[]}}
            with connect() as conn:
                saved=service.save_attendance_parse_result(conn,parsejob["id"],parsejob["lease_token"],result);conn.commit()
                passed("native_candidate_complete",saved["validation"]["can_confirm"])
                passed("parse_publication_recovery",service.completed_attendance_job_result(conn,parsejob["id"],parsejob["lease_token"])["parse_run_id"]==parsed["parse_run_id"])
                report=service.get_attendance_report(conn,original["report_id"],user)
                run=dict(conn.execute("SELECT * FROM attendance_parse_runs WHERE id=?",(parsed["parse_run_id"],)).fetchone())
                conn.commit()
            def confirm(_):
                with connect() as conn:
                    try:
                        service.confirm_attendance_run(conn,user,report_id=report["id"],run_id=run["id"],expected_report_revision=report["revision"],expected_run_revision=run["revision"])
                        conn.commit();return "confirmed"
                    except HTTPException as exc:
                        conn.rollback();return exc.status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                confirmations=list(pool.map(confirm,[1,2]))
            passed("concurrent_confirmation_cas",sorted(map(str,confirmations))==["409","confirmed"])
            with connect() as conn:
                payload=service.list_attendance_reports(conn,user,q="合成")
                passed("native_search",payload["total"]==1)
                try:service.attendance_source_file(conn,{"id":2,"role":"teacher"},report["id"],original["source_version_id"])
                except HTTPException as exc:passed("cross_teacher_original_denied",exc.status_code==404)
                else:passed("cross_teacher_original_denied",False)
                conn.rollback()

            # Route integration uses a new native PG connection per request. Only
            # authentication is synthetic; routers, commands, queries and file
            # response/range handling are the real production implementation.
            current_user=dict(user)
            app=FastAPI()
            app.include_router(routes.router)
            def authenticated_user():
                if current_user is None:raise HTTPException(401,"Not authenticated")
                return current_user
            app.dependency_overrides[get_current_user]=authenticated_user
            prefix="/api/attendance-reports"
            report_path=f"{prefix}/{original['report_id']}"
            pdf_path=report_path+f"/versions/{original['source_version_id']}/source.pdf"
            with patch.object(routes,"get_db_connection",side_effect=connect), TestClient(app) as client:
                detail=client.get(report_path)
                passed("asgi_native_pg_detail_versions",detail.status_code==200 and detail.json()["versions"][0]["id"]==original["source_version_id"])
                pdf=client.get(pdf_path)
                head=client.head(pdf_path)
                partial=client.get(pdf_path,headers={"Range":"bytes=0-3"})
                passed("asgi_native_pg_original_get_head",pdf.status_code==200 and pdf.content.startswith(b"%PDF") and head.status_code==200 and head.content==b"" and int(head.headers["content-length"])==len(pdf.content))
                passed("asgi_native_pg_original_range_206",partial.status_code==206 and partial.content==b"%PDF" and partial.headers["content-range"].startswith("bytes 0-3/") and partial.headers["cache-control"]=="private, no-store")
                current_user={"id":2,"role":"teacher"}
                passed("asgi_native_pg_cross_teacher_404",client.get(report_path).status_code==404 and client.get(pdf_path,headers={"Range":"bytes=0-3"}).status_code==404 and client.get(prefix).json()["total"]==0)
                current_user={"id":1,"role":"student"}
                passed("asgi_native_pg_student_403",client.get(report_path).status_code==403 and client.get(pdf_path).status_code==403)
                current_user=None
                passed("asgi_native_pg_unauthenticated_401",client.get(report_path).status_code==401)
                current_user=dict(user)

                def create_source(source,offering_id=10):
                    payload={"source_token":service.sign_attendance_source_option(1,source),"class_offering_id":offering_id}
                    bound=client.post(prefix+"/source-bindings",json=payload)
                    if bound.status_code!=200:raise RuntimeError("Native PG API binding failed")
                    selected=bound.json()["binding"]
                    exported=client.post(prefix+"/exports",json={"binding_id":selected["id"],"expected_binding_revision":selected["revision"],"idempotency_key":"api-native-fixture"})
                    if exported.status_code!=202:raise RuntimeError("Native PG API export enqueue failed")
                    return selected,exported.json()

                b2,second=create_source({**option,"remote_schedule_id":"opaque-api-second","course_code":"SYN-2","course_name":"筛选合成课程"})
                create_source({**option,"remote_schedule_id":"opaque-api-year","course_code":"SYN-3","academic_year":"2024-2025"},None)
                page=client.get(prefix,params={"year":"2025-2026","page":2,"page_size":1,"sort":"course_asc"})
                passed("asgi_native_pg_filters_pagination",page.status_code==200 and page.json()["total"]==2 and len(page.json()["items"])==1 and client.get(prefix,params={"q":"筛选合成"}).json()["total"]==1)
                facets=client.get(prefix+"/options",params={"year":"2025-2026","course":"SYN-2"})
                passed("asgi_native_pg_facets_scope",facets.status_code==200 and facets.json()["years"]==["2025-2026","2024-2025"] and {c["value"] for c in facets.json()["courses"]}=={"SYN-1","SYN-2"} and [c["value"] for c in facets.json()["teaching_classes"]]==[b2["id"]])
                wrong_pdf=f"{prefix}/{second['report_id']}/versions/{original['source_version_id']}/source.pdf"
                passed("asgi_native_pg_cross_report_version_404",client.get(wrong_pdf).status_code==404)
                cancelled=client.post(prefix+f"/jobs/{second['job_id']}/cancel")
                passed("asgi_native_pg_persistent_job_cancel",cancelled.status_code==200 and cancelled.json()["job"]["status"]=="cancelled" and client.get(prefix+f"/jobs/{second['job_id']}").json()["job"]["status"]=="cancelled")

                with connect() as conn:
                    conn.execute("UPDATE ai_jobs SET status='succeeded' WHERE id=?",(parsejob["id"],));conn.commit()
                queued=client.post(report_path+f"/versions/{original['source_version_id']}/parse-runs",json={"idempotency_key":"native-api-reparse"})
                passed("asgi_native_pg_reparse_new_version",queued.status_code==202 and queued.json()["parse_run_id"]!=parsed["parse_run_id"])
                newjob=jobs.claim_due_ai_jobs(task_types=("attendance_parse",),max_running=1,concurrency_lock_key=0x415454504152,fair_owner=True)[0]
                candidate=json.loads(json.dumps(result))
                candidate["cells"][0].update(normalized_status="UNKNOWN",quality_state="unknown")
                with connect() as conn:
                    service.save_attendance_parse_result(conn,newjob["id"],newjob["lease_token"],candidate);conn.commit()
                newrun=queued.json()["parse_run_id"]
                run_path=report_path+f"/runs/{newrun}"
                students=client.get(run_path+"/students",params={"quality_state":"unknown","q":"000123","page_size":1})
                sessions=client.get(run_path+"/sessions",params={"page_size":1})
                passed("asgi_native_pg_matrix_quality_search",students.status_code==200 and students.json()["total"]==1 and sessions.status_code==200 and sessions.json()["total"]==1)
                cells=client.get(run_path+"/cells",params={"student_ids":students.json()["items"][0]["id"],"session_ids":sessions.json()["items"][0]["id"]})
                passed("asgi_native_pg_matrix_exact_window",cells.status_code==200 and cells.json()["total"]==1 and cells.json()["items"][0]["normalized_status"]=="UNKNOWN")
                passed("asgi_native_pg_cross_report_run_404",client.get(f"{prefix}/{second['report_id']}/runs/{newrun}/students").status_code==404)
                detail=client.get(report_path).json()
                run=next(r for r in detail["runs"] if r["id"]==newrun)
                change={"target_type":"cell","target_id":cells.json()["items"][0]["id"],"changes":{"normalized_status":"CHECKED"},"reason":"合成原件测试核对","expected_revision":run["revision"]}
                reviewed=client.patch(run_path+"/review",json=change)
                passed("asgi_native_pg_review_cas",reviewed.status_code==200 and reviewed.json()["run"]["state"]=="validated" and client.patch(run_path+"/review",json=change).status_code==409)
                confirmed=client.post(run_path+"/confirm",json={"expected_run_revision":reviewed.json()["run"]["revision"],"expected_report_revision":detail["report"]["revision"]})
                passed("asgi_native_pg_confirm_candidate",confirmed.status_code==200 and confirmed.json()["confirmed_parse_run_id"]==newrun)
                selection=client.post(prefix+f"/source-bindings/{binding['id']}/grade-source",json={"expected_revision":detail["binding"]["revision"]})
                passed("asgi_native_pg_persistent_grade_source",selection.status_code==200 and selection.json()["binding"]["is_grade_source"] and client.get(report_path).json()["binding"]["is_grade_source"])
                deleted=client.request("DELETE",report_path,json={"expected_revision":confirmed.json()["report_revision"]})
                historical=client.get(report_path)
                passed("asgi_native_pg_soft_delete_historical_reads",deleted.status_code==200 and historical.status_code==200 and bool(historical.json()["report"]["deleted_at"]) and client.get(run_path+"/students").status_code==200 and client.get(pdf_path,headers={"Range":"bytes=0-3"}).status_code==206 and client.get(prefix,params={"deleted":1}).json()["total"]==1)
                passed("asgi_native_pg_deleted_write_blocked",client.patch(run_path+"/review",json=change).status_code==404)
                restored=client.post(report_path+"/restore",json={"expected_revision":historical.json()["report"]["revision"]})
                passed("asgi_native_pg_restore_cas",restored.status_code==200 and client.get(report_path).json()["report"]["deleted_at"] is None and client.get(prefix,params={"deleted":1}).json()["total"]==0)
        return {"engine":"postgres","checks":checks,"ok":True,"source_or_model_calls":0,"public_tables_modified":False,"schema_removed":True,
                "generated_at":datetime.now().astimezone().isoformat(timespec="seconds"),
                "asgi":"standalone FastAPI router with real per-request PostgreSQL connections",
                "fixtures":"synthetic students and PDF bytes; no source PDF content recognition assertion"}
    finally:
        reset_ai_job_schema_guard_for_tests()
        if created:
            if not re.fullmatch(r"attendance_probe_[0-9a-f]{32}",schema):raise RuntimeError("Invalid owned schema name")
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            passed("owned_schema_removal_verified",admin.execute("SELECT COUNT(*) FROM pg_namespace WHERE nspname=%s",(schema,)).fetchone()[0]==0)
        admin.close()


def validate_performance():
    """One bounded 30,000-cell query probe, independent of lifecycle reruns."""
    from time import perf_counter
    from classroom_app import config
    from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
    from classroom_app.db.schema_attendance_reports import ensure_attendance_report_schema
    from classroom_app.services import attendance_report_service as service, file_service
    import psycopg

    if config.DB_ENGINE!="postgres" or urlparse(config.DATABASE_URL).hostname not in {"localhost","127.0.0.1","::1"}:
        raise RuntimeError("This probe requires the configured local PostgreSQL database")
    started=perf_counter()
    schema="attendance_perf_"+uuid.uuid4().hex
    admin=psycopg.connect(config.DATABASE_URL,autocommit=True,connect_timeout=5)
    created=False
    evidence={"engine":"postgres","mode":"single bounded query probe","generated_at":datetime.now().astimezone().isoformat(timespec="seconds"),
              "scale":{"students":300,"sessions":100,"cells":30000},"source_or_model_calls":0,"public_tables_modified":False,
              "timing_scope":"one local run, no throughput or production latency guarantee","queries":{},"plans":{},"checks":[],"ok":False}
    def checked(name,condition):
        if not condition:raise RuntimeError("Attendance performance probe failed: "+name)
        evidence["checks"].append({"name":name,"ok":True})
    try:
        admin.execute(f'CREATE SCHEMA "{schema}"');created=True
        with tempfile.TemporaryDirectory(prefix="attendance-pg-perf-") as directory, \
             patch.object(file_service,"GLOBAL_FILES_DIR",Path(directory)), \
             patch.object(file_service,"GLOBAL_FILES_LEGACY_DIRS",()), \
             patch.object(config,"ATTENDANCE_ARCHIVE_ENABLED",True):
            raw=psycopg.connect(config.DATABASE_URL,connect_timeout=5,row_factory=sqlite_compatible_dict_row)
            raw.execute(f'SET search_path TO "{schema}", pg_catalog')
            raw.execute("SET statement_timeout TO '20s'")
            raw.execute("SET lock_timeout TO '5s'");raw.commit()
            with LanSharePostgresConnection(raw) as conn:
                checked("isolated_search_path",conn.execute("SELECT current_schema()").fetchone()[0]==schema)
                tree=ast.parse((Path(__file__).resolve().parents[1]/"tests/test_attendance_report_service.py").read_text("utf-8"))
                ddl=next(ast.literal_eval(n.args[0]) for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="executescript")
                for statement in ddl.split(";"):
                    if statement.strip():conn.execute(statement)
                ensure_attendance_report_schema(conn,engine="postgres")
                user={"id":1,"role":"teacher"}
                source={"academic_year":"2025-2026","academic_term":2,"remote_schedule_id":"synthetic-performance-schedule","course_name":"合成性能课程","course_code":"SYN-PERF","teaching_class_name":"合成合班","school_code":"synthetic","platform_code":"synthetic","external_account_key":"synthetic-performance-account"}
                binding=service.save_attendance_binding(conn,user,source_token=service.sign_attendance_source_option(1,source))["binding"]
                now=service._now()
                report_id=service._insert(conn,"attendance_reports",{"binding_id":binding["id"],"created_at":now,"updated_at":now})
                stored=file_service.store_file_object_globally(io.BytesIO(b"%PDF-1.7\nsynthetic performance-only bytes"))
                version_id=service._insert(conn,"attendance_report_versions",{"report_id":report_id,"version_no":1,"request_key":"synthetic-performance","source_file_hash":stored["hash"],"source_state":"cached","source_page_count":1,"created_by":1,"created_at":now})
                run_id=service._insert(conn,"attendance_parse_runs",{"source_version_id":version_id,"run_no":1,"request_key":"synthetic-performance","state":"confirmed"})
                conn.execute("UPDATE attendance_reports SET latest_source_version_id=?,confirmed_parse_run_id=? WHERE id=?",(version_id,run_id,report_id))
                seed_started=perf_counter()
                conn.execute("INSERT INTO attendance_report_students(parse_run_id,row_index,student_number,source_name,source_class_name) SELECT ?,n,'SYN-'||LPAD(n::text,6,'0'),'synthetic-'||n::text,'synthetic-large-class' FROM generate_series(1,300) n",(run_id,))
                conn.execute("INSERT INTO attendance_report_sessions(parse_run_id,column_index,source_header,source_datetime,remote_checkin_id) SELECT ?,n,'synthetic-'||n::text,('2026-03-01'::date+(n-1))::text||' 08:00:00','synthetic-event-'||n::text FROM generate_series(1,100) n",(run_id,))
                conn.execute("INSERT INTO attendance_report_cells(parse_run_id,student_row_id,session_column_id,raw_text,normalized_status,quality_state) SELECT ?,s.id,se.id,'synthetic',CASE WHEN (s.row_index+se.column_index)%10=0 THEN 'UNCHECKED' ELSE 'CHECKED' END,'verified' FROM attendance_report_students s CROSS JOIN attendance_report_sessions se WHERE s.parse_run_id=? AND se.parse_run_id=?",(run_id,run_id,run_id))
                conn.commit()
                evidence["seed_elapsed_ms"]=round((perf_counter()-seed_started)*1000,3)
                checked("exact_synthetic_scale",conn.execute("SELECT COUNT(*) FROM attendance_report_cells WHERE parse_run_id=?",(run_id,)).fetchone()[0]==30000)
                for table in ("smart_attendance_source_bindings","attendance_reports","attendance_report_versions","attendance_parse_runs","attendance_report_students","attendance_report_sessions","attendance_report_cells"):
                    conn.execute("ANALYZE "+table)
                conn.commit()

                class CapturedConnection:
                    def __init__(self,inner):self.inner=inner;self.statements=[]
                    def execute(self,sql,params=()):
                        if str(sql).lstrip().startswith("SELECT"):self.statements.append((sql,params))
                        return self.inner.execute(sql,params)
                    def __getattr__(self,name):return getattr(self.inner,name)
                captured=CapturedConnection(conn)
                statement_sets={}
                def measured(name,call):
                    captured.statements=[]
                    stamp=perf_counter();result=call()
                    evidence["queries"][name]={"elapsed_ms":round((perf_counter()-stamp)*1000,3),"returned_rows":len(result.get("items") or []),"total":result.get("total"),"sql_statements":len(captured.statements)}
                    statement_sets[name]=list(captured.statements)
                    return result
                reports=measured("report_page",lambda:service.list_attendance_reports(captured,user,page=1,page_size=5))
                students=measured("student_page_with_all_session_summaries",lambda:service.attendance_run_students(captured,user,report_id,run_id,page=7,page_size=25))
                sessions=measured("session_page",lambda:service.attendance_run_sessions(captured,user,report_id,run_id,page=3,page_size=20))
                matrix=measured("matrix_25_by_20_window",lambda:service.attendance_run_cells(captured,user,report_id,run_id,student_ids=",".join(str(s["id"]) for s in students["items"]),session_ids=",".join(str(s["id"]) for s in sessions["items"])))
                checked("report_pagination_bounded",len(reports["items"])<=5 and reports["total"]==1)
                checked("student_pagination_bounded",len(students["items"])==25 and students["total"]==300)
                checked("session_pagination_bounded",len(sessions["items"])==20 and sessions["total"]==100)
                checked("matrix_window_bounded",len(matrix["items"])==500 and {c["row_index"] for c in matrix["items"]}==set(range(151,176)) and {c["column_index"] for c in matrix["items"]}==set(range(41,61)))
                checked("summary_denominator_all_100_sessions",all(s["summary"]["applicable"]==100 and s["summary"]["unknown"]==0 for s in students["items"]))

                selectors={"report_page":lambda sql:sql.startswith(service._REPORT_SELECT),
                           "student_page":lambda sql:sql.startswith("SELECT s.* FROM attendance_report_students"),
                           "student_summary":lambda sql:sql.startswith("SELECT student_row_id,normalized_status"),
                           "matrix_window":lambda sql:sql.startswith("SELECT c.*,s.row_index")}
                all_statements=[statement for statements in statement_sets.values() for statement in statements]
                for name,selector in selectors.items():
                    sql,params=next((sql,params) for sql,params in all_statements if selector(sql))
                    plan_data=conn.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "+sql,params).fetchone()[0]
                    if isinstance(plan_data,str):plan_data=json.loads(plan_data)
                    plan=plan_data[0]
                    nodes=[]
                    def walk(node):
                        keys=("Node Type","Relation Name","Index Name","Actual Rows","Actual Loops","Rows Removed by Filter","Shared Hit Blocks","Shared Read Blocks")
                        nodes.append({key:node[key] for key in keys if key in node})
                        for child in node.get("Plans",[]):walk(child)
                    walk(plan["Plan"])
                    evidence["plans"][name]={"planning_ms":plan.get("Planning Time"),"execution_ms":plan.get("Execution Time"),"nodes":nodes}
                checked("explain_core_queries_recorded",len(evidence["plans"])==4)
        checked("temporary_blobs_removed",not Path(directory).exists())
        evidence["ok"]=True
        return evidence
    finally:
        if created:
            if not re.fullmatch(r"attendance_perf_[0-9a-f]{32}",schema):raise RuntimeError("Invalid owned schema name")
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            checked("owned_schema_removal_verified",admin.execute("SELECT COUNT(*) FROM pg_namespace WHERE nspname=%s",(schema,)).fetchone()[0]==0)
        admin.close()
        evidence["total_elapsed_ms"]=round((perf_counter()-started)*1000,3)


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path)
    parser.add_argument("--performance-only",action="store_true",help="Run only the bounded 300x100 native query probe")
    args=parser.parse_args()
    report=validate_performance() if args.performance_only else validate()
    if args.output:
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=True,indent=2))
