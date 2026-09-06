"""Career cold-start, publication, concurrency-contract and evidence regression tests."""
import copy
import json
import os
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("DB_ENGINE", "sqlite")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from classroom_app.db import schema_ai_jobs, schema_career_path, schema_resume
from classroom_app.dependencies import get_current_user
from classroom_app.routers import career_path as router
from classroom_app.services import career_lifecycle_service as lifecycle
from classroom_app.services import career_path_service as career
from classroom_app.services.career_recommendation_service import baseline_network, recommend


def fixture():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    schema_ai_jobs._SCHEMA_READY_ENGINES.clear()
    for module, ensure in ((schema_career_path, schema_career_path.ensure_career_path_schema),
                           (schema_resume, schema_resume.ensure_resume_schema),
                           (schema_ai_jobs, schema_ai_jobs.ensure_ai_job_schema)):
        module._SCHEMA_READY = False
        ensure(conn)
    conn.execute("""CREATE TABLE classes(id INTEGER PRIMARY KEY,name TEXT,major TEXT,academic_major TEXT,
        academic_class_code TEXT,academic_class_name TEXT,academic_grade TEXT,enrollment_year INTEGER,
        expected_graduation_year INTEGER,program_duration_years INTEGER,college TEXT,department TEXT)""")
    conn.execute("""CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT,gender TEXT,class_id INTEGER,
        school_code TEXT,school_name TEXT,academic_major TEXT,academic_grade TEXT,description TEXT,nickname TEXT,
        today_mood TEXT,enrollment_status TEXT)""")
    conn.execute("INSERT INTO classes(id,name,major,program_duration_years) VALUES(1,'语言2401','英语',4)")
    conn.execute("INSERT INTO students(id,name,gender,class_id,school_code,academic_grade) VALUES(1,'测试学生','女',1,'audit','2024')")
    conn.execute("INSERT INTO students(id,name,gender,class_id,school_code,academic_grade) VALUES(2,'另一学生','男',1,'audit','2024')")
    conn.commit()
    return conn


def answers_for(ctx, mode="quick"):
    answers=[]
    for q in career.get_questions(mode=mode,major_key=ctx["major_key"]):
        if q["kind"]=="single": value=q["options"][0]["value"]
        elif q["kind"]=="multi": value=[q["options"][0]["value"]]
        elif q["kind"]=="scale": value=3
        else: value=""
        answers.append({"question_id":q["id"],"value":value})
    return answers


class CareerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.conn=fixture()
        self.ctx=career.resolve_student_context(self.conn,1)

    def tearDown(self):
        self.conn.close()
        lifecycle._cached_recommend.cache_clear()
        lifecycle._validated_graph.cache_clear()

    def initialize(self):
        state=career.initialize_career(self.conn,1)
        self.conn.commit()
        return state

    def submit(self, enhance=False):
        state=self.initialize()
        result=career.save_test_and_generate(self.conn,self.ctx,answers_for(self.ctx),revision=state["revision"],enhance=enhance)
        self.conn.commit()
        return result["state"]

    def assert_pure_state(self):
        prohibited={sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,
                    sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_CREATE_INDEX,sqlite3.SQLITE_ALTER_TABLE}
        self.conn.set_authorizer(lambda action,*args:sqlite3.SQLITE_DENY if action in prohibited else sqlite3.SQLITE_OK)
        try: return career.build_state(self.conn,1)
        finally: self.conn.set_authorizer(None)

    def test_uninitialized_get_is_pure_and_has_usable_nontechnical_baseline(self):
        state=self.assert_pure_state()
        self.assertEqual(state["phase"],"intro")
        self.assertFalse(state["initialized"])
        self.assertEqual(state["major"]["name"],"英语")
        self.assertTrue(any("翻译" in n["name"] for n in state["network"]["nodes"]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0],0)

    def test_repeated_initialization_is_deduped_and_does_not_reset_revision(self):
        first=self.initialize();second=self.initialize()
        self.assertEqual(first["revision"],second["revision"])
        self.assertEqual(first["tasks"]["network"]["id"],second["tasks"]["network"]["id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0],1)
        self.assertEqual(self.assert_pure_state()["phase"],"intro")

    def test_light_state_skips_graph_and_recommendation_computation(self):
        state=self.submit()
        statements=[]
        self.conn.set_trace_callback(statements.append)
        with patch.object(lifecycle,"_baseline",side_effect=AssertionError("ranking must not run")), \
             patch.object(lifecycle,"get_or_prepare_network",side_effect=AssertionError("graph must not load")):
            result=career.build_state(self.conn,1,known_result_version=state["result_version"])
        self.conn.set_trace_callback(None)
        self.assertTrue(result["network_unchanged"])
        self.assertLessEqual(len(statements),5)
        self.assertFalse(any("resume_" in sql or "evidence_json" in sql for sql in statements))

    def test_disabled_or_saturated_ai_keeps_initialization_and_quiz_available(self):
        from classroom_app.services.student_career_job_service import CareerJobCapacityError
        with patch.object(lifecycle,"enqueue_student_career_job",side_effect=CareerJobCapacityError("paused")):
            state=self.initialize()
        self.assertEqual(state["phase"],"intro")
        self.assertEqual(state["tasks"]["network"]["status"],"paused")
        self.assertTrue(state["network"]["nodes"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0],0)

    def test_orphan_and_terminal_mismatch_recover_with_bounded_writes(self):
        state=self.initialize();job_id=state["tasks"]["network"]["id"]
        self.conn.execute("DELETE FROM ai_jobs WHERE id=?",(job_id,))
        self.assertEqual(career.recover_career_jobs(self.conn)["recovered"],1)
        fresh=career.build_state(self.conn,1)["tasks"]["network"]["id"]
        self.assertNotEqual(fresh,job_id)
        self.conn.execute("UPDATE ai_jobs SET status='succeeded' WHERE id=?",(fresh,))
        self.assertEqual(career.recover_career_jobs(self.conn)["recovered"],1)
        self.assertEqual(self.conn.execute("SELECT status FROM career_major_networks").fetchone()[0],"failed")
        state=career.build_state(self.conn,1)
        self.assertEqual(state["tasks"]["network"]["status"],"failed")
        self.assertTrue(state["tasks"]["network"]["can_retry"])
        self.assertEqual(career.build_state(self.conn,1,known_result_version=state["result_version"])["tasks"]["network"]["status"],"failed")

    def test_invalid_legacy_ready_network_exposes_retry_without_requeue(self):
        self.initialize()
        self.conn.execute("UPDATE career_major_networks SET network_json=?,status='ready'",(json.dumps({"cats":[],"nodes":[{}]*12,"links":[]}),))
        self.conn.execute("UPDATE ai_jobs SET status='succeeded'")
        state=self.assert_pure_state()
        self.assertEqual(state["tasks"]["network"]["error_code"],"network_invalid")
        self.assertTrue(state["tasks"]["network"]["can_retry"])

    def test_same_major_students_share_only_network_job(self):
        first=self.initialize();second=career.initialize_career(self.conn,2)
        self.assertEqual(first["tasks"]["network"]["id"],second["tasks"]["network"]["id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM career_student_sessions").fetchone()[0],2)

    def test_submit_yields_immediate_baseline_without_personal_model_call(self):
        state=self.submit()
        self.assertEqual(state["phase"],"ready")
        self.assertEqual(state["recommendation_source"],"baseline")
        self.assertTrue(state["rankings"])
        self.assertEqual(state["tasks"]["personalization"]["status"],"not_requested")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0],1)
        self.assertEqual(self.assert_pure_state()["phase"],"ready")

    def test_failed_network_is_visible_and_get_never_rearms(self):
        state=self.initialize();job_id=state["tasks"]["network"]["id"]
        job=dict(self.conn.execute("SELECT * FROM ai_jobs WHERE id=?",(job_id,)).fetchone())
        payload=json.loads(job["payload_json"])
        lifecycle.fail_network(self.conn,job,payload,"timeout","TimeoutError")
        self.conn.execute("UPDATE ai_jobs SET status='dead_letter',attempt_count=3,last_error_code='timeout' WHERE id=?",(job_id,))
        self.conn.commit()
        for _ in range(10):
            result=self.assert_pure_state()
            self.assertEqual(result["tasks"]["network"]["status"],"dead_letter")
            self.assertTrue(result["tasks"]["network"]["can_retry"])
            self.assertTrue(result["network"]["nodes"])
        self.assertEqual(self.conn.execute("SELECT attempt_count FROM ai_jobs WHERE id=?",(job_id,)).fetchone()[0],3)

    def test_network_publication_keeps_history_and_rejects_stale_generation(self):
        state=self.initialize();job=dict(self.conn.execute("SELECT * FROM ai_jobs").fetchone())
        payload=json.loads(job["payload_json"]);candidate={"network":baseline_network("英语")}
        self.assertTrue(lifecycle.apply_network(self.conn,job,payload,candidate))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM career_network_versions").fetchone()[0],1)
        stale={**payload,"generation":payload["generation"]-1}
        self.assertFalse(lifecycle.apply_network(self.conn,job,stale,candidate))

    def test_operator_restore_appends_history_and_fences_pending_result(self):
        self.initialize();job=dict(self.conn.execute("SELECT * FROM ai_jobs").fetchone())
        payload=json.loads(job["payload_json"])
        lifecycle.apply_network(self.conn,job,payload,{"network":baseline_network("英语")})
        restored=lifecycle.restore_network_version(self.conn,school_code="audit",major_key="英语",revision=1,reason="隔离验证恢复")
        self.assertEqual(restored["revision"],2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM career_network_versions").fetchone()[0],2)
        self.assertFalse(lifecycle.apply_network(self.conn,job,payload,{"network":baseline_network("英语")}))
        self.assertEqual(career.build_state(self.conn,1)["tasks"]["network"]["status"],"ready")

    def test_old_personal_result_cannot_resurrect_reset(self):
        state=self.submit(enhance=True)
        job=dict(self.conn.execute("SELECT * FROM ai_jobs WHERE task_type=?",(career.PERSONALIZE_TASK_KIND,)).fetchone())
        payload=json.loads(job["payload_json"])
        career.reset_session(self.conn,1,revision=state["revision"])
        self.assertFalse(lifecycle.apply_personalization_result(self.conn,job,payload,{"summary":"old"}))
        self.assertEqual(career.build_state(self.conn,1)["phase"],"intro")

    def test_refreshed_network_cannot_relabel_old_ai_as_current(self):
        self.submit(enhance=True)
        personal=dict(self.conn.execute("SELECT * FROM ai_jobs WHERE task_type=?",(career.PERSONALIZE_TASK_KIND,)).fetchone())
        lifecycle.apply_personalization_result(self.conn,personal,json.loads(personal["payload_json"]),{"summary":"old explanation"})
        self.conn.execute("UPDATE ai_jobs SET status='succeeded' WHERE id=?",(personal["id"],))
        network=dict(self.conn.execute("SELECT * FROM ai_jobs WHERE task_type=?",(career.NETWORK_GENERATE_TASK_KIND,)).fetchone())
        lifecycle.apply_network(self.conn,network,json.loads(network["payload_json"]),{"network":baseline_network("英语")})
        stale=career.build_state(self.conn,1)
        self.assertTrue(stale["stale"])
        self.assertTrue(career.build_state(self.conn,1,known_result_version=stale["result_version"])["stale"])
        refreshed=career.initialize_career(self.conn,1)
        self.assertEqual(refreshed["recommendation_source"],"baseline")
        self.assertEqual(self.conn.execute("SELECT personalized_json FROM career_student_sessions").fetchone()[0],"{}")

    def test_same_input_regeneration_retains_valid_ai_in_light_state(self):
        state=self.submit(enhance=True)
        job=dict(self.conn.execute("SELECT * FROM ai_jobs WHERE task_type=?",(career.PERSONALIZE_TASK_KIND,)).fetchone())
        lifecycle.apply_personalization_result(self.conn,job,json.loads(job["payload_json"]),{"summary":"valid explanation"})
        self.conn.execute("UPDATE ai_jobs SET status='succeeded' WHERE id=?",(job["id"],))
        state=career.career_job_command(self.conn,1,target="personalization",action="retry")
        self.assertEqual(state["recommendation_source"],"ai")
        light=career.build_state(self.conn,1,known_result_version=state["result_version"])
        self.assertEqual(light["recommendation_source"],"ai")
        self.assertEqual(light["tasks"]["personalization"]["status"],"queued")

    def test_major_change_invalidates_prior_quiz_and_recommendation(self):
        self.submit()
        self.conn.execute("UPDATE students SET academic_major='护理学（专升本）' WHERE id=1")
        self.assertTrue(career.build_state(self.conn,1)["context_changed"])
        state=career.initialize_career(self.conn,1)
        self.assertEqual(state["phase"],"intro")
        self.assertIn("护理",state["major"]["name"])
        self.assertFalse(state["draft"])

    def test_quiz_draft_conflict_and_incomplete_submission(self):
        state=self.initialize();answers=answers_for(self.ctx)
        saved=career.save_test_progress(self.conn,self.ctx,answers[:1],revision=state["revision"])
        with self.assertRaises(career.CareerConflict):
            career.save_test_progress(self.conn,self.ctx,answers[:2],revision=state["revision"])
        with self.assertRaises(ValueError):
            career.save_test_and_generate(self.conn,self.ctx,answers[:1],revision=saved["revision"])

    def test_duplicate_questions_and_choices_are_rejected(self):
        from classroom_app.services.career_lifecycle_service import validate_answers
        answer=answers_for(self.ctx)[0]
        with self.assertRaises(ValueError):
            validate_answers([answer,answer],mode="quick",major_key="英语",complete=False)
        with self.assertRaises(ValueError):
            validate_answers([{"question_id":"q_focus","value":["language_global","language_global"]}],
                             mode="quick",major_key="英语",complete=False)

    def test_unknown_major_is_never_assumed_software_and_missing_duration_is_unknown(self):
        self.conn.execute("UPDATE classes SET major=NULL,program_duration_years=NULL")
        ctx=career.resolve_student_context(self.conn,1)
        self.assertEqual(ctx["major_key"],"unknown")
        self.assertIsNone(ctx["timeline"]["graduation_year"])
        self.assertEqual(ctx["timeline"]["enrollment_year"],2024)

    def test_personal_task_cannot_be_cancelled_by_another_student(self):
        state=self.submit(enhance=True);job_id=state["tasks"]["personalization"]["id"]
        career.initialize_career(self.conn,2)
        with self.assertRaises(career.CareerConflict):
            career.career_job_command(self.conn,2,target="personalization",action="cancel",job_id=job_id)
        self.assertEqual(self.conn.execute("SELECT status FROM ai_jobs WHERE id=?",(job_id,)).fetchone()[0],"queued")

    def test_preference_and_feedback_change_input_and_explicit_ranking(self):
        state=self.submit();first_hash=self.conn.execute("SELECT input_hash FROM career_student_sessions").fetchone()[0]
        last=state["rankings"][-1]
        state=career.update_career_preferences(self.conn,1,{"cities":["南宁"],"target_positions":[last["name"]],"notes":""},revision=state["revision"])
        self.assertNotEqual(first_hash,self.conn.execute("SELECT input_hash FROM career_student_sessions").fetchone()[0])
        row=next(x for x in state["rankings"] if x["tag"]==last["tag"])
        self.assertGreater(row["score"],last["score"])
        state=career.record_career_feedback(self.conn,1,last["direction_id"],"dismissed",revision=state["revision"])
        self.assertLess(next(x for x in state["rankings"] if x["tag"]==last["tag"])["score"],row["score"])

    def test_changed_resume_evidence_invalidates_old_ai_and_refreshes_score(self):
        state=self.submit(enhance=True)
        job=dict(self.conn.execute("SELECT * FROM ai_jobs WHERE task_type=?",(career.PERSONALIZE_TASK_KIND,)).fetchone())
        payload=json.loads(job["payload_json"])
        self.conn.execute("INSERT INTO resume_skills(student_id,name,description) VALUES(1,'翻译','完成课程翻译作品')")
        lifecycle.invalidate_career_profile(self.conn,1)
        self.assertTrue(career.build_state(self.conn,1)["needs_refresh"])
        self.assertFalse(lifecycle.apply_personalization_result(self.conn,job,payload,{"summary":"old"}))
        with self.assertRaisesRegex(ValueError,"刷新资料"):
            career.career_job_command(self.conn,1,target="personalization",action="retry")
        refreshed=career.initialize_career(self.conn,1)
        translation=next(x for x in refreshed["rankings"] if x["name"]=="翻译与本地化")
        self.assertGreater(translation["evidence_coverage"],0)
        self.assertFalse(refreshed["needs_refresh"])

    def test_certificate_natural_expiry_invalidates_cached_direction_score(self):
        self.submit()
        self.conn.execute("INSERT INTO resume_certificates(student_id,name,expiry_date) VALUES(1,'教师资格','2027-01')")
        with patch.object(lifecycle,"_now",return_value="2027-01-01T12:00:00"):
            state=career.initialize_career(self.conn,1)
            state=career.career_job_command(self.conn,1,target="personalization",action="retry")
            job=dict(self.conn.execute("SELECT * FROM ai_jobs WHERE task_type=? ORDER BY id DESC LIMIT 1",(career.PERSONALIZE_TASK_KIND,)).fetchone())
            self.assertTrue(lifecycle.apply_personalization_result(self.conn,job,json.loads(job["payload_json"]),{"summary":"January advice"}))
            state=career.build_state(self.conn,1)
        before=next(row for row in state["rankings"] if row["name"]=="语言教学与培训")
        self.assertGreater(before["evidence_coverage"],0)
        with patch.object(lifecycle,"_now",return_value="2027-02-01T12:00:00"):
            expired=career.build_state(self.conn,1,known_result_version=state["result_version"])
            light=career.build_state(self.conn,1,known_result_version=expired["result_version"])
        self.assertEqual(expired["recommendation_source"],"baseline")
        self.assertEqual(light["recommendation_source"],"baseline")
        self.assertTrue(expired["needs_refresh"])
        self.assertTrue(light["needs_refresh"])
        self.assertTrue(light["tasks"]["personalization"]["can_retry"])
        self.assertNotEqual(state["result_version"],expired["result_version"])
        after=next(row for row in expired["rankings"] if row["name"]=="语言教学与培训")
        self.assertEqual(after["evidence_coverage"],0)
        self.assertLess(after["score"],before["score"])

    def test_network_same_name_preserves_feedback_and_model_rename_requires_review(self):
        state=self.submit();first=state["network"]["nodes"][0]
        career.record_career_feedback(self.conn,1,first["direction_id"],"saved",revision=state["revision"])
        job=dict(self.conn.execute("SELECT * FROM ai_jobs WHERE task_type=?",(career.NETWORK_GENERATE_TASK_KIND,)).fetchone())
        payload=json.loads(job["payload_json"])
        candidate=baseline_network("英语")
        candidate["nodes"][0]["name"]=" " + first["name"] + " "
        self.assertTrue(lifecycle.apply_network(self.conn,job,payload,{"network":candidate}))
        after=career.build_state(self.conn,1)
        self.assertEqual(after["network"]["nodes"][0]["direction_id"],first["direction_id"])
        self.assertEqual(after["feedback_by_tag"][first["tag"]],"saved")
        # A model cannot transfer the saved identity by copying its opaque ID
        # onto an unreviewed new name. The original feedback stays available.
        candidate["nodes"][0]["name"]="翻译与本地化服务"
        self.assertTrue(lifecycle.apply_network(self.conn,job,payload,{"network":candidate}))
        after=career.build_state(self.conn,1)
        self.assertNotEqual(after["network"]["nodes"][0]["direction_id"],first["direction_id"])
        self.assertEqual(after["feedback_by_tag"][first["tag"]],"")
        self.assertEqual(len(career.build_state(self.conn,1)["unmapped_feedback"]),1)


class CareerPayloadAndEvidenceTests(unittest.TestCase):
    def test_software_seed_has_stable_ids_and_no_unverified_salary_promises(self):
        graph=career._seed_network_for("软件工程")
        self.assertEqual(len(graph["nodes"]),24)
        self.assertEqual(len({node["direction_id"] for node in graph["nodes"]}),24)
        self.assertTrue(all(node["rec"]==3 for node in graph["nodes"]))
        self.assertNotRegex(json.dumps(graph,ensure_ascii=False),r"2025|5万|9\.7k|6-9k|越老越吃香|普通本科最|稳定抗周期")
        self.assertIsNone(career._seed_network_for("软件工程技术"))

    def test_empty_and_hostile_graphs_are_rejected(self):
        with self.assertRaises(ValueError):career._validate_network_payload({"cats":[],"nodes":[{}]*12,"links":[]},"英语")
        for mutate in (
            lambda g:g["nodes"][0].update(tag='A1\" onload=\"x'),
            lambda g:g["cats"][0].update(c1='red; background:url(x)'),
            lambda g:g["nodes"][1].update(tag=g["nodes"][0]["tag"]),
            lambda g:g["nodes"][0].update(cat=[]),
            lambda g:g["nodes"][0].update(pre=[{}]),
            lambda g:g.update(links=[[[],0,g["nodes"][1]["tag"],0]]),
            lambda g:g.update(links=[[g["nodes"][0]["tag"],99,g["nodes"][1]["tag"],0]]),
        ):
            graph=baseline_network("英语");mutate(graph)
            with self.assertRaises(ValueError):career._validate_network_payload(graph,"英语")

    def test_each_major_family_has_valid_bounded_graph(self):
        for major in ("英语","软件工程","工商管理","学前教育","视觉设计","护理学",""):
            self.assertGreaterEqual(len(career._validate_network_payload(baseline_network(major),major)["nodes"]),6)

    def test_intent_and_negated_skill_never_count_as_ability(self):
        graph=baseline_network("计算机")
        raw={"evidence":[{"section":"skill","id":1,"text":"不熟悉Python，尚未掌握SQL"}],"intent":{"expected_position":"Python开发"}}
        result=recommend(graph,test_result={},evidence=raw,preferences={},feedback={},timeline={})
        data=next(x for x in result["rankings"] if x["name"]=="数据分析")
        self.assertEqual(data["evidence_coverage"],0)
        self.assertEqual(career.score_personality_answers([{"question_id":"unknown","value":1}])["holland_code"],"")

    def test_base_rec_is_retained_before_personal_overlay(self):
        graph=baseline_network("英语");node=graph["nodes"][0]
        result=career.apply_personalization(graph,{"rec_overrides":{node["tag"]:5}})
        self.assertEqual(result["nodes"][0]["base_rec"],3)
        self.assertEqual(result["nodes"][0]["rec"],5)

    def test_preparation_stage_changes_priorities_without_hiding_long_term_paths(self):
        graph=baseline_network("护理学")
        kwargs=dict(test_result={},evidence={},preferences={"goal":"employment"},feedback={})
        near=recommend(graph,**kwargs,timeline={"months_to_graduation":3})
        far=recommend(graph,**kwargs,timeline={"months_to_graduation":30})
        clinical_near=next(x for x in near["rankings"] if "临床" in x["name"])
        clinical_far=next(x for x in far["rankings"] if "临床" in x["name"])
        self.assertLess(clinical_near["score"],clinical_far["score"])
        self.assertEqual(clinical_near["horizon"],"long_term")

    def test_major_annotation_retains_specialties_but_separates_education_path(self):
        self.assertEqual(career.normalize_major_key("网络工程（专升本）"),"网络工程")
        self.assertNotEqual(career.normalize_major_key("英语（商务方向）"),career.normalize_major_key("英语"))


class CareerRouterTests(unittest.TestCase):
    def setUp(self):
        self.conn=fixture()
        @contextmanager
        def connection():
            try:yield self.conn
            except Exception:self.conn.rollback();raise
        self.patch=patch.object(router,"get_db_connection",connection);self.patch.start()
        app=FastAPI();app.include_router(router.router)
        app.dependency_overrides[get_current_user]=lambda:{"id":1,"role":"student"}
        self.client=TestClient(app)
    def tearDown(self):
        self.client.close();self.patch.stop();self.conn.close()

    def test_initialize_progress_complete_conflict_and_ready_contract(self):
        state=self.client.post("/api/career-path/initialize").json()
        ctx=career.resolve_student_context(self.conn,1)
        data={"answers":answers_for(ctx),"mode":"quick","quiz_version":career.QUIZ_VERSION,"revision":state["revision"]}
        submitted=self.client.post("/api/career-path/answers",json=data)
        self.assertEqual(submitted.status_code,200,submitted.text)
        self.assertEqual(submitted.json()["state"]["phase"],"ready")
        conflict=self.client.post("/api/career-path/progress",json=data)
        self.assertEqual(conflict.status_code,409,conflict.text)
        self.assertEqual(conflict.json()["detail"]["code"],"revision_conflict")

    def test_nonstudent_and_invalid_revision_are_rejected(self):
        response=self.client.post("/api/career-path/answers",json={"answers":[],"revision":True})
        self.assertEqual(response.status_code,422)

    def test_nested_malformed_feedback_and_question_ids_are_client_errors(self):
        state=self.client.post("/api/career-path/initialize").json()
        response=self.client.post("/api/career-path/feedback",json={"career_tag":"B1","action":[],"revision":state["revision"]})
        self.assertEqual(response.status_code,422)
        response=self.client.post("/api/career-path/progress",json={"answers":[{"question_id":["q_work"],"value":"x"}],"revision":state["revision"]})
        self.assertEqual(response.status_code,400)

    def test_light_poll_omits_only_unchanged_heavy_payload(self):
        state=self.client.post("/api/career-path/initialize").json()
        response=self.client.get("/api/career-path/state",params={"known_result_version":state["result_version"]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()["network_unchanged"])
        self.assertNotIn("network",response.json())
        self.assertIn("tasks",response.json())

    def test_public_postings_routes_and_nonstudent_permissions(self):
        from tests.test_career_job_postings import posting
        from classroom_app.services.career_job_posting_service import upsert_job_posting
        item=upsert_job_posting(self.conn,posting())
        self.conn.commit()
        response=self.client.get("/api/career-path/job-postings",params={"keyword":"翻译","page_size":1})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()["items"]),1)
        target=self.client.post(f"/api/career-path/job-postings/{item['id']}/target")
        self.assertEqual(target.status_code,200,target.text)
        self.assertTrue(target.json()["job_target_id"])
        self.client.app.dependency_overrides[get_current_user]=lambda:{"id":1,"role":"teacher"}
        self.assertEqual(self.client.get("/api/career-path/job-postings").status_code,403)
        self.assertEqual(self.client.post(f"/api/career-path/job-postings/{item['id']}/target").status_code,403)


if __name__=="__main__":unittest.main()
